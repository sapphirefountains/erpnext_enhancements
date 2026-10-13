# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Draft mode and Publish for the Maintenance Planner (v1.601.0, C1).

The Project Planner has had draft mode since Phase 3B (``api/project_planner.py``, "drafts"): a
planner rearranges the week without anybody being told, then presses Publish. The Maintenance
Planner wrote every drag straight to the visit. A Google Calendar sync is coming that pushes only
*committed* work, so the Maintenance Planner needs the same line between "I am trying things" and
"this is the schedule". This module is that line for visits, built to the Project Planner's rules:

* **A draft never touches the visit.** ``move_visit(draft=1)`` and ``add_crew(draft=1)``
  (``api/maintenance_planner.py``) come here: the change is applied to the visit **in memory**
  with the very function a live save uses (``maintenance_planner._edit_visit``), and the result is
  kept on the caller's **Planner Draft Change** row for that visit. No ``doc.save()``, so no crew
  mirror, no ToDo, no customer date email and no digest moves.
* **One Draft row per (owner, visit)**, its ``payload`` the visit's drafted values for everything
  changed so far (:data:`VISIT_KEYS`: the day, technician, crew, length, Full day and the pencil
  flag). Drafting it back to exactly what is stored (an Undo, a drag there and back) leaves no
  draft.
* **Optimistic locking**: ``base_modified`` is the visit's ``modified`` when it was drafted. A
  visit somebody else saved since is never published over: it is skipped and reported, its draft
  kept for the planner to redo or discard. A new draft of it starts from the visit as it is now.
* **Publishing replays the live path.** Each draft goes through ``maintenance_planner._apply_visit``,
  the function ``move_visit`` saves with, so the workflow, the validate warnings, the crew mirror,
  the technician's dispatch ToDo and the timeline note all happen exactly as for a drag.
* **Conflicts are judged on the whole batch, all or nothing**: the engine works the affected
  people's days out with every draft in place (``crew_availability.availability(visit_overrides=)``)
  and compares them with the days as stored (``project_planner.batch_conflicts``); any new conflict
  asks for one reason for the lot before anything is saved. A visit that only conflicts while the
  batch is half saved is retried after the rest, and saved with the fallback reason on the third
  pass, as on the Project Planner.
* **One notice per person per publish** (``planner_notices.send_publish_notices``), never to the
  planner who published, with ``frappe.flags.planner_publishing`` set while saving.
* **Each planner publishes its own drafts.** Task rows (``task`` set) belong to the Project
  Planner and visit rows (``visit`` set) to this one; neither planner's Publish or Discard touches
  the other's, so a planner with drafts in both publishes each from its own page.

**Refused in draft mode**, with a sentence that says so (:data:`DRAFT_REFUSAL`,
:data:`PROJECTED_REFUSAL`): *Add visit here* (``maintenance_actions.create_visit``: a visit that
does not exist has no record for a draft to hang on, the Project Planner's rule for a new task) and
moving a **projected** visit (``move_projected``: it rewrites the contract's feature rows and may
draft a record on the spot; holding that back would need a second kind of draft and a second
projection overlay, out of proportion to how rarely a projected card is moved while rehearsing a
week). The page refuses both before asking the server.

The pure helpers (:func:`merge_payload`, :func:`load_payload`, :func:`payload_kwargs`,
:func:`visit_state`) take plain values so ``tests/test_maintenance_visit_drafts.py`` runs them, and
the rest against a stub, without a bench.
"""

import json

import frappe
from frappe import _

from erpnext_enhancements.api import maintenance_planner as mp

DRAFT_DOCTYPE = "Planner Draft Change"
RECORD = "Sapphire Maintenance Record"
DRAFT, PUBLISHED, DISCARDED = "Draft", "Published", "Discarded"
#: The visit values a Draft row may carry, as ``maintenance_planner._edit_visit`` takes them.
#: Nothing else in a stored payload is applied.
VISIT_KEYS = ("date", "technician", "crew", "planned_hours", "full_day", "tentative")
DRAFT_LIMIT = 300
#: The timeline reason for the rare visit that only conflicts while its batch is half saved.
PUBLISH_FALLBACK_REASON = "Published from the Maintenance Planner with other drafted changes"
PLANNER = "Maintenance Planner"
DRAFT_REFUSAL = (
	"Draft mode holds back changes to visits that already exist; a new visit cannot be a draft. "
	"Turn Draft mode off to add it."
)
PROJECTED_REFUSAL = (
	"Draft mode holds back changes to drafted visits only. This visit is projected from its contract, "
	"and moving it changes the contract's next visit date at once. Turn Draft mode off to move it."
)
NOT_INSTALLED = "Draft mode for visits arrives with this update's migrate. Try again in a few minutes."


# ---------------------------------------------------------------------- pure helpers


def merge_payload(existing, changes):
	"""A Draft row's payload after one more drafted change: the keys the change sets replace the
	stored ones, the rest keep theirs, so the payload is everything drafted on the visit so far."""
	out = {key: value for key, value in (existing or {}).items() if key in VISIT_KEYS}
	out.update({key: value for key, value in (changes or {}).items() if key in VISIT_KEYS})
	return out


def load_payload(text):
	"""A stored payload as a dict of known keys; anything unreadable is an empty draft."""
	try:
		value = json.loads(text) if isinstance(text, str) else (text or {})
	except ValueError:
		return {}
	if not isinstance(value, dict):
		return {}
	return {key: value[key] for key in VISIT_KEYS if key in value}


def payload_kwargs(payload):
	"""``maintenance_planner._edit_visit`` / ``_apply_visit`` keyword arguments for a payload.

	``technician`` ``""`` is kept (it means nobody); a missing or None value is not passed. ``date``
	None (an unscheduled visit drafted without a day) is not passed either: a visit is never
	unscheduled from the planner.
	"""
	return {key: (payload or {})[key] for key in VISIT_KEYS if (payload or {}).get(key) is not None}


def crew_payload(entries):
	"""A crew as the payload keeps it: ``[{"user", "hours"}]``, hours None for the visit's length."""
	out = []
	for entry in entries or []:
		user = entry.get("user")
		if user and user not in [row["user"] for row in out]:
			hours = mp._positive(entry.get("hours"))
			out.append({"user": user, "hours": hours})
	return out


def visit_state(doc):
	"""What a draft can change on a visit, comparable: ``(day, technician, crew, hours, full day,
	pencil)``. Two equal states mean nothing is drafted."""
	day = doc.get("scheduled_visit_date")
	return (
		str(day)[:10] if day else None,
		doc.get("technician") or "",
		tuple(sorted((entry["user"], entry["hours"] or 0) for entry in mp._crew_entries(doc))),
		mp._positive(doc.get("planned_hours")) or 0,
		1 if doc.get("full_day") else 0,
		1 if mp.is_pencil(doc) else 0,
	)


def same_moment(a, b):
	from erpnext_enhancements.api.project_planner import same_moment as pp_same_moment

	return pp_same_moment(a, b)


# ---------------------------------------------------------------------- plumbing


def refuse(message):
	"""Refuse a write that draft mode cannot hold back, with the sentence that says why."""
	frappe.throw(_(message), title=_("Not in Draft mode"))


def installed():
	"""Whether Planner Draft Change can hold a visit yet (its ``visit`` column, v1.601.0)."""
	try:
		return bool(frappe.db.has_column(DRAFT_DOCTYPE, "visit"))
	except Exception:
		return False


def _require_installed():
	if not installed():
		frappe.throw(_(NOT_INSTALLED))


def has_open_draft(record):
	"""True when anybody holds an unpublished draft of the visit (Add visit's Undo asks)."""
	return bool(installed() and frappe.db.exists(DRAFT_DOCTYPE, {"visit": record, "status": DRAFT}))


def _quiet_message_log():
	"""``(log, mark)`` so a caught ``frappe.throw`` does not also pop up in the browser."""
	log = frappe.local.message_log if isinstance(getattr(frappe.local, "message_log", None), list) else None
	return log, (len(log) if log is not None else 0)


def _draft_rows(user=None):
	"""``(rows, duplicates)``: the caller's visit Draft rows, one per visit (the newest wins),
	oldest first, and any older duplicate a racing double-save left behind. Task rows (the Project
	Planner's) are never read here."""
	if not installed():
		return [], []
	rows = frappe.get_all(
		DRAFT_DOCTYPE,
		filters={"owner": user or frappe.session.user, "status": DRAFT, "visit": ["is", "set"]},
		fields=["name", "visit", "subject", "payload", "base_modified", "modified"],
		order_by="creation asc",
		limit_page_length=DRAFT_LIMIT,
	)
	latest = {}
	for row in rows:
		known = latest.get(row.get("visit"))
		if known is None or str(row.get("modified")) >= str(known.get("modified")):
			latest[row.get("visit")] = row
	keep = [latest[visit] for visit in dict.fromkeys(row.get("visit") for row in rows)]
	kept = {row.get("name") for row in keep}
	return keep, [row for row in rows if row.get("name") not in kept]


def _subject(doc):
	"""``"Highlands · Spring startup"``: what the Draft row and the publish answer call the visit."""
	title = frappe.db.get_value("Project", doc.get("project"), "project_name") if doc.get("project") else None
	site = mp.short_site_name(title or doc.get("project") or "") or doc.name
	text = f"{site} · {doc.get('visit_label')}" if doc.get("visit_label") else f"{site} · {doc.name}"
	return text[:140]


def _drafted_doc(row):
	"""``(doc, problem)``: the visit with ``row``'s draft applied in memory, or why it cannot be.

	Nothing is saved. ``problem`` is None for a draft that applies; otherwise a sentence for the
	planner (the visit is gone, somebody else changed it since, or the change no longer fits).
	"""
	try:
		doc = frappe.get_doc(RECORD, row.get("visit"))
	except Exception:
		return None, _("The visit no longer exists.")
	if not same_moment(row.get("base_modified"), doc.modified):
		return doc, _("Changed by someone else since you drafted it.")
	log, mark = _quiet_message_log()
	try:
		mp._refuse_finished(doc)
		mp._edit_visit(doc, **payload_kwargs(load_payload(row.get("payload"))))
	except Exception as exc:
		if log is not None:
			del log[mark:]
		return doc, _("Cannot be applied any more: {0}").format(frappe.utils.strip_html_tags(str(exc))[:300])
	return doc, None


def draft_context(record, modified):
	"""Load a visit for drafting, as a live move would (permission, finished, fresh), then with the
	caller's draft so far applied.

	A Draft row whose visit changed since it was drafted, or that no longer applies, is not built
	on: the new draft starts from the visit as it is now, and the planner is told (``reset``).
	"""
	_require_installed()
	doc = frappe.get_doc(RECORD, record)
	doc.check_permission("write")
	mp._refuse_finished(doc)
	mp._check_fresh(doc, modified)
	rows, duplicates = _draft_rows()
	row = next((r for r in rows if r.get("visit") == doc.name), None)
	stored = visit_state(doc)
	stored_specs = mp._hours_specs(doc)
	payload, reset = {}, False
	if row:
		if same_moment(row.get("base_modified"), doc.modified):
			payload = load_payload(row.get("payload"))
			log, mark = _quiet_message_log()
			try:
				mp._edit_visit(doc, **payload_kwargs(payload))
			except Exception:
				# The earlier draft no longer applies (its day has passed, a person left): start over.
				if log is not None:
					del log[mark:]
				doc = frappe.get_doc(RECORD, record)
				payload, reset = {}, True
		else:
			reset = True
	return {
		"doc": doc,
		"row": row,
		"payload": payload,
		"stored": stored,
		"stored_specs": stored_specs,
		"reset": reset,
		"duplicates": [r for r in duplicates if r.get("visit") == doc.name],
	}


def _recorded(doc, change):
	"""The payload values one drafted change leaves on the visit, read back off the drafted doc."""
	out = {}
	if change.get("date"):
		day = doc.get("scheduled_visit_date")
		out["date"] = str(day)[:10] if day else None
	if change.get("technician") is not None or change.get("crew") is not None:
		# A handover can move people between the technician and the crew, so both are kept.
		out["technician"] = doc.get("technician") or ""
		out["crew"] = crew_payload(mp._crew_entries(doc))
	if change.get("planned_hours") is not None:
		out["planned_hours"] = mp._positive(doc.get("planned_hours")) or 0
	if change.get("full_day") is not None:
		out["full_day"] = 1 if doc.get("full_day") else 0
	if change.get("tentative") is not None:
		out["tentative"] = 1 if mp.is_pencil(doc) else 0
	return out


def store_draft(context, change):
	"""Apply one change to a drafted visit in memory and keep it on the caller's Draft row.

	Returns ``move_visit``'s shape plus ``draft_saved: True`` and ``in_drafts`` (False when the
	change put the visit back as stored, so no draft is left).
	``conflicts`` are the ones the drafted visit would create against everything as stored, as
	information: drafting never needs a reason. They also come back as ``warnings``.
	"""
	doc = context["doc"]
	mp._edit_visit(doc, **{key: value for key, value in change.items() if value is not None})
	payload = merge_payload(context["payload"], _recorded(doc, change))
	after = visit_state(doc)
	unchanged = after == context["stored"]
	_write_draft(context, doc, payload, discard=unchanged)

	conflicts = {}
	if not unchanged and doc.get("scheduled_visit_date"):
		stored, specs = context["stored"], mp._hours_specs(doc)
		moved = (after[0], after[3], after[4]) != (stored[0], stored[3], stored[4]) or (
			stored[5] and not after[5]
		)
		if moved:
			affected = list(specs)
		else:
			before = context.get("stored_specs") or {}
			affected = [user for user, spec in specs.items() if user not in before or before[user] != spec]
		if affected:
			conflicts = mp._people_conflicts(doc, affected)

	warnings = []
	if context.get("reset"):
		warnings.append(
			_(
				"Your earlier draft of this visit was replaced: it changed since you drafted it, or no longer applied."
			)
		)
	for label, messages in conflicts.items():
		warnings.append(_("Not published yet. {0}: {1}").format(label, ", ".join(messages)))
	result = mp._visit_result(doc, warnings)
	result.update(draft_saved=True, in_drafts=not unchanged, conflicts=conflicts)
	return result


def _write_draft(context, doc, payload, discard=False):
	"""Insert or update the caller's one Draft row for the visit (permission-checked). With
	``discard`` the row, if any, is marked Discarded instead and nothing is inserted."""
	text = json.dumps(payload, sort_keys=True, default=str)
	row = context.get("row")
	if discard:
		if row:
			draft = frappe.get_doc(DRAFT_DOCTYPE, row.get("name"))
			draft.status = DISCARDED
			draft.save()
	elif row:
		draft = frappe.get_doc(DRAFT_DOCTYPE, row.get("name"))
		draft.payload = text
		draft.base_modified = doc.modified
		draft.save()
	else:
		frappe.get_doc(
			{
				"doctype": DRAFT_DOCTYPE,
				"visit": doc.name,
				"subject": _subject(doc),
				"status": DRAFT,
				"payload": text,
				"base_modified": doc.modified,
			}
		).insert()
	for duplicate in context.get("duplicates") or []:
		frappe.db.set_value(DRAFT_DOCTYPE, duplicate.get("name"), "status", DISCARDED)


# ---------------------------------------------------------------------- drafting (from maintenance_planner)


def draft_move(record, modified, **edits):
	"""``move_visit(draft=1)``: the change into the caller's draft of the visit, nothing saved."""
	context = draft_context(record, modified)
	return store_draft(context, edits)


def draft_add_crew(record, user, modified=None, hours=None):
	"""``add_crew(draft=1)``: the person joins the visit's drafted crew, nothing saved. Refuses what
	the live ``add_crew`` refuses; someone already on the (drafted) visit changes nothing."""
	context = draft_context(record, modified)
	doc = context["doc"]
	if doc.get("visit_date"):
		frappe.throw(
			_("This visit was started for {0}. Change who is on it on the visit form.").format(
				frappe.utils.formatdate(doc.visit_date)
			)
		)
	if not user:
		frappe.throw(_("Pick a person to add."))
	mp._require_active(user)
	current = mp._crew_entries(doc)
	if user == doc.get("technician") or user in [entry["user"] for entry in current]:
		result = mp._visit_result(doc, [])
		result.update(draft_saved=True, in_drafts=visit_state(doc) != context["stored"], conflicts={})
		return result
	return store_draft(context, {"crew": [*current, {"user": user, "hours": hours}]})


# ---------------------------------------------------------------------- showing drafts (get_planner)


def _people(row):
	"""``[(user, own hours or None)]`` on an engine row, the technician first."""
	out = [(row.get("technician"), None)] if row.get("technician") else []
	out += [(user, hours) for user, hours in row.get("crew") or [] if user]
	return out


def engine_row(doc, title=None):
	"""A visit as the engine's ``visit_overrides`` take it (``crew_availability._read_visits``)."""
	day = doc.get("scheduled_visit_date") or doc.get("visit_date")
	return {
		"project": doc.get("project"),
		"visit_label": doc.get("visit_label"),
		"technician": doc.get("technician"),
		"clock_in_time": doc.get("clock_in_time"),
		"clock_out_time": doc.get("clock_out_time"),
		"planned_hours": doc.get("planned_hours"),
		"full_day": 1 if doc.get("full_day") else 0,
		"tentative": 1 if mp.is_pencil(doc) else 0,
		"project_title": title,
		"plan_date": str(day)[:10] if day else None,
		"crew": [(entry["user"], entry["hours"]) for entry in mp._crew_entries(doc)],
	}


def _titles(docs):
	projects = sorted({doc.get("project") for doc in docs if doc.get("project")})
	if not projects:
		return {}
	return dict(
		frappe.get_all(
			"Project", filters={"name": ["in", projects]}, fields=["name", "project_name"], as_list=True
		)
	)


def draft_overlay():
	"""The caller's visit drafts as the planner shows them: ``docs`` (``{visit: drafted doc}``),
	``engine`` (``{visit: engine row}`` for ``availability(visit_overrides=)``) and ``stale``
	(drafts that cannot be shown: the visit changed since, is gone, or the change no longer fits)."""
	rows, _duplicates = _draft_rows()
	overlay = {"docs": {}, "engine": {}, "stale": []}
	for row in rows:
		doc, problem = _drafted_doc(row)
		if problem:
			overlay["stale"].append(
				{
					"visit": row.get("visit"),
					"subject": row.get("subject") or row.get("visit"),
					"reason": problem,
				}
			)
			continue
		overlay["docs"][doc.name] = doc
	titles = _titles(overlay["docs"].values())
	for name, doc in overlay["docs"].items():
		overlay["engine"][name] = engine_row(doc, titles.get(doc.get("project")))
	return overlay


def _card_row(doc):
	"""A drafted visit as ``maintenance_planner._visit_card`` reads a SQL row."""
	day = doc.get("scheduled_visit_date")
	return frappe._dict(
		name=doc.name,
		project=doc.get("project"),
		customer=doc.get("customer"),
		maintenance_contract=doc.get("maintenance_contract"),
		serial_no=doc.get("serial_no"),
		visit_label=doc.get("visit_label"),
		technician=doc.get("technician"),
		scheduled_visit_date=day,
		visit_date=doc.get("visit_date"),
		docstatus=doc.get("docstatus") or 0,
		workflow_state=doc.get("workflow_state"),
		completion_percent=doc.get("completion_percent"),
		has_out_of_range_readings=doc.get("has_out_of_range_readings"),
		modified=doc.get("modified"),
		planned_hours=doc.get("planned_hours"),
		full_day=doc.get("full_day"),
		clock_in_time=doc.get("clock_in_time"),
		clock_out_time=doc.get("clock_out_time"),
		tentative=1 if mp.is_pencil(doc) else 0,
		plan_date=day,
	)


def with_drafts(visits, unscheduled, overlay, start, end, today, can_move):
	"""``(visits, unscheduled)`` with each drafted visit's card replaced by its drafted one
	(``drafted: True``): on its drafted day when that is in range, in the tray when it has none, and
	gone from the range when it was drafted out of it."""
	docs = (overlay or {}).get("docs") or {}
	if not docs:
		return visits, unscheduled
	visits = [card for card in visits if card.get("name") not in docs]
	unscheduled = [card for card in unscheduled if card.get("name") not in docs]
	names = mp._full_names({entry["user"] for doc in docs.values() for entry in mp._crew_entries(doc)})
	start, end = str(start), str(end)
	for doc in docs.values():
		card = mp._visit_card(_card_row(doc), today, can_move)
		card["crew"] = [
			{
				"user": entry["user"],
				"name": names.get(entry["user"]) or entry["user"],
				"hours": entry["hours"],
			}
			for entry in mp._crew_entries(doc)
		]
		card["drafted"] = True
		if not card.get("date"):
			unscheduled.append(card)
		elif start <= card["date"] <= end:
			visits.append(card)
	return visits, unscheduled


def summary(overlay):
	"""``get_planner``'s ``drafts``: how many, which visits, and the ones that cannot be shown."""
	overlay = overlay or {}
	docs, stale = overlay.get("docs") or {}, overlay.get("stale") or []
	return {
		"count": len(docs) + len(stale),
		"visits": sorted(docs),
		"stale": stale,
		"installed": installed(),
	}


# ---------------------------------------------------------------------- publishing


def _publish_plan(row):
	"""``(plan, problem)`` for one Draft row: the visit before and after, and its save arguments."""
	doc, problem = _drafted_doc(row)
	if problem:
		return None, problem
	if not frappe.has_permission(RECORD, "write", doc=doc):
		return None, _("You cannot edit this visit.")
	stored = frappe.get_doc(RECORD, doc.name)
	title = _titles([stored]).get(stored.get("project"))
	return {
		"row": row,
		"name": doc.name,
		"subject": row.get("subject") or doc.name,
		"kwargs": payload_kwargs(load_payload(row.get("payload"))),
		"before": engine_row(stored, title),
		"after": engine_row(doc, title),
	}, None


def _batch_check(plans):
	"""``project_planner.batch_conflicts`` for a publish: the affected people's days as stored
	against the same days with every drafted visit in place. Never raises: an engine failure is
	logged and checks nothing, as ``maintenance_planner.visit_conflicts`` does for a single move."""
	from erpnext_enhancements.api.project_planner import batch_conflicts

	users, first, last = set(), None, None
	for plan in plans:
		for row in (plan["before"], plan["after"]):
			day = frappe.utils.getdate(row["plan_date"]) if row.get("plan_date") else None
			if day:
				first = day if first is None else min(first, day)
				last = day if last is None else max(last, day)
			users.update(user for user, _hours in _people(row))
	if first is None or not users:
		return {}
	try:
		from erpnext_enhancements.project_enhancements.crew_availability import availability

		resources = frappe.get_all(
			"Planner Resource", filters={"user": ["in", sorted(users)], "is_active": 1}, pluck="name"
		)
		if not resources:
			return {}
		before = availability(first, last, resources)
		after = availability(
			first, last, resources, visit_overrides={plan["name"]: plan["after"] for plan in plans}
		)
	except Exception:
		mp._log_failure("Maintenance Planner: publish conflict check failed")
		return {}
	labels = {r.get("name"): r.get("label") for r in after.get("resources") or []}
	return batch_conflicts(
		before.get("days"), after.get("days"), [plan["name"] for plan in plans], labels, kinds=("visit",)
	)


def _publish_one(plan, reason):
	"""Save one draft through ``_apply_visit``. ``("saved", result)``, ``("wait", None)`` when it asks
	for a reason, or ``("failed", sentence)``, with its own savepoint so a refusal undoes only it."""
	log, mark = _quiet_message_log()
	frappe.db.savepoint("planner_publish_visit")
	try:
		doc = frappe.get_doc(RECORD, plan["name"])
		if not same_moment(plan["row"].get("base_modified"), doc.modified):
			return "failed", _("Changed by someone else since you drafted it.")
		doc.check_permission("write")
		mp._refuse_finished(doc)
		result = mp._apply_visit(doc, reason=reason, **plan["kwargs"])
	except Exception as exc:
		frappe.db.rollback(save_point="planner_publish_visit")
		if log is not None:
			del log[mark:]
		return "failed", _("Not saved: {0}").format(frappe.utils.strip_html_tags(str(exc))[:300])
	if result.get("needs_reason"):
		return "wait", None
	return "saved", result


def _apply_plans(plans, reason):
	"""Save every plan. A visit that asks for a reason only because the batch is half saved is
	retried after the rest; one still asking on the third pass is saved with the fallback reason
	(the batch check already passed, or the planner gave a reason for all of it)."""
	saved, failed, pending = [], [], list(plans)
	passes = (reason or None, reason or None, reason or _(PUBLISH_FALLBACK_REASON))
	frappe.flags.planner_publishing = True
	try:
		for pass_reason in passes:
			waiting = []
			for plan in pending:
				status, value = _publish_one(plan, pass_reason)
				if status == "saved":
					plan["result"] = value
					saved.append(plan)
				elif status == "wait":
					waiting.append(plan)
				else:
					failed.append({"visit": plan["name"], "subject": plan["subject"], "reason": value})
			pending = waiting
			if not pending:
				break
	finally:
		frappe.flags.planner_publishing = False
	for plan in pending:
		failed.append({"visit": plan["name"], "subject": plan["subject"], "reason": _("Not saved.")})
	return saved, failed


def _notify_publish(saved):
	"""One notice per person whose visits moved (``planner_notices.send_publish_notices``)."""
	from erpnext_enhancements.project_enhancements import planner_notices as notices

	def snap(plan, row):
		day = row.get("plan_date")
		state = {
			"name": plan["name"],
			"subject": plan["subject"],
			"exp_start_date": day,
			"exp_end_date": day,
			"expected_time": mp._positive(row.get("planned_hours")) or 0,
		}
		people = _people(row)
		return notices.snapshot(state, [user for user, _h in people], {user: hours for user, hours in people})

	window = notices.alert_window() if notices.setting("change_alerts") else None
	out = {}
	for plan in saved:
		before, after = snap(plan, plan["before"]), snap(plan, plan["after"])
		urgent = notices.change_lines(before, after, window, long=True) if window else {}
		for user, entries in notices.change_lines(before, after).items():
			notice = out.setdefault(
				user, {"lines": [], "urgent": [], "task": plan["name"], "doctype": RECORD, "day": None}
			)
			notice["lines"].extend(entries)
			notice["urgent"].extend(urgent.get(user) or [])
			days = [e["day"] for e in notice["lines"] if e.get("day")]
			notice["day"] = min(days) if days else None
	out.pop(frappe.session.user, None)
	return notices.send_publish_notices(out, frappe.session.user, PLANNER) if out else 0


@frappe.whitelist(methods=["POST"])
def publish_drafts(reason=None):
	"""Publish every one of the caller's visit drafts, all or nothing on conflicts.

	* A draft whose visit changed since it was drafted (or is gone, finished, or no longer applies)
	  is **skipped** and listed in ``skipped``; its row stays a Draft to redo or discard.
	* When the drafts together create conflicts and no ``reason`` came, nothing is saved:
	  ``{"needs_reason": True, "conflicts": {person: [..]}, "skipped": [..], "count": n}``.
	* Otherwise every draft is saved through ``maintenance_planner._apply_visit`` (the path
	  ``move_visit`` saves with), its row marked Published, and each person whose visits moved gets
	  one notice. The Project Planner's task drafts are not touched.

	Returns ``{"published": [{visit, subject}], "skipped": [..], "failed": [..], "notified": n,
	"conflicts": {..}}``; ``failed`` lists a visit the record itself refused, which never stops the
	others.
	"""
	mp._require_planner()
	_require_installed()
	reason = (_given(reason) or "").strip()
	rows, _duplicates = _draft_rows()
	plans, skipped = [], []
	for row in rows:
		plan, problem = _publish_plan(row)
		if problem:
			skipped.append(
				{
					"visit": row.get("visit"),
					"subject": row.get("subject") or row.get("visit"),
					"reason": problem,
				}
			)
		else:
			plans.append(plan)
	if not plans:
		return {"published": [], "skipped": skipped, "failed": [], "notified": 0, "conflicts": {}}

	conflicts = _batch_check(plans)
	if conflicts and not reason:
		return {"needs_reason": True, "conflicts": conflicts, "skipped": skipped, "count": len(plans)}

	saved, failed = _apply_plans(plans, reason)
	now = frappe.utils.now_datetime()
	for plan in saved:
		draft = frappe.get_doc(DRAFT_DOCTYPE, plan["row"].get("name"))
		draft.status = PUBLISHED
		draft.published_on = now
		draft.reason = reason or None
		draft.save()
	notified = _notify_publish(saved) if saved else 0
	return {
		"published": [{"visit": plan["name"], "subject": plan["subject"]} for plan in saved],
		"skipped": skipped,
		"failed": failed,
		"notified": notified,
		"conflicts": conflicts,
	}


@frappe.whitelist(methods=["POST"])
def discard_drafts(visits=None):
	"""Discard the caller's visit drafts: all of them, or only those for ``visits`` (a JSON list).
	The rows are marked Discarded, not deleted; the visits stay as they are. Task drafts are never
	touched. Returns ``{"discarded": n}``."""
	mp._require_planner()
	wanted = _given(visits)
	if isinstance(wanted, str):
		wanted = frappe.parse_json(wanted)
	wanted = set(wanted) if wanted is not None else None
	rows, duplicates = _draft_rows()
	count = 0
	for row in rows + duplicates:
		if wanted is not None and row.get("visit") not in wanted:
			continue
		draft = frappe.get_doc(DRAFT_DOCTYPE, row.get("name"))
		draft.status = DISCARDED
		draft.save()
		count += 1
	return {"discarded": count}


def _given(value):
	"""None for an argument the caller did not set (``""``, ``"null"``, ``"undefined"``)."""
	if value is None or (isinstance(value, str) and value.strip() in ("", "null", "undefined")):
		return None
	return value
