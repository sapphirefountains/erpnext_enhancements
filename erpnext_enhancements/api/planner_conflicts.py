"""The Conflict center for both planners (Project Planner Phase 6D, TASK-2026-02470).

One list of everything wrong right now (Nik, 2026-10-09): double bookings, people over their
hours, work on a day off, work on a personal block, vehicle and equipment clashes, and missing
qualifications, each with its one-click fixes.

* :func:`get_conflicts` makes **one** engine pass (``crew_availability._compute``) with Google
  switched off for the range plus the 30 days after it, so each conflict's "next free day" is worked
  out from the same answer. Qualification gaps and equipment clashes are the Phase 3A / Phase 4 rules
  (``project_planner.qualification_gaps``, ``crew_availability.equipment_findings``), not new ones.
* **Fixes go through the existing write paths**, never a second one, so the reason prompt, draft
  mode, change alerts and Undo all still apply. Each fix names the endpoint and the arguments the
  page should send (``method`` / ``args``); see :func:`fixes_for`. Nothing here writes a task or
  a visit.
* **Each planner moves only its own records.** An item is ``own`` only when the calling planner
  moves that kind of record: project Tasks for ``planner="project"``, maintenance visits (and the
  contract behind a projected visit) for ``planner="maintenance"``. Rental crew tasks follow their
  Rental Booking and travel its trip, so they are never anyone's here, and only owned items get
  fixes.
* **"Pick someone who's free" never picks.** The fix carries the date and hours for
  ``project_planner.who_is_free``; a person chooses (Nik declined "suggest a crew").
* **"Keep it with a reason"** (:func:`acknowledge_conflict`) stores a ``Planner Conflict Ack`` and a
  timeline comment on every owned item, like the drag prompt's reason. The acknowledgement hides the
  conflict only while its fingerprint (the involved records' ``modified`` stamps, any block's, and
  the conflict's wording) still matches, so any later change brings it back.
* **Block notes** reach a conflict only through ``crew_availability.block_notes`` for the caller;
  the sentences themselves never carry one.
"""

import datetime
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, now_datetime, nowdate

from erpnext_enhancements.api import maintenance_planner as mp
from erpnext_enhancements.api import planner_blocks
from erpnext_enhancements.api import project_planner as pp
from erpnext_enhancements.project_enhancements import crew_availability as engine

#: The longest range one Conflict center load covers.
MAX_DAYS = 60
#: How far past the range the one engine pass reaches, for "next free day".
LOOKAHEAD_DAYS = engine.NEXT_FREE_HORIZON_DAYS
ACK_DOCTYPE = "Planner Conflict Ack"
PLANNERS = ("project", "maintenance")
#: Every conflict kind, in the order a day lists them.
KINDS = ("overbooked", "overlap", "day_off", "blocked", "equipment", "qualification")
KIND_ORDER = {kind: index for index, kind in enumerate(KINDS)}
#: The kinds that are about one person's day.
PERSON_KINDS = ("overbooked", "overlap", "day_off", "blocked")
VISIT = "Sapphire Maintenance Record"
CONTRACT = "Sapphire Maintenance Contract"
TRAVEL = "Travel Trip"
PP_METHOD = "erpnext_enhancements.api.project_planner."
MP_METHOD = "erpnext_enhancements.api.maintenance_planner."
SELF_METHOD = "erpnext_enhancements.api.planner_conflicts."
STALE = "This conflict has changed or cleared since the list loaded. Refresh the Conflict center."


# ---------------------------------------------------------------------- pure helpers


def day_label(day):
	"""``2026-10-12`` → ``"Mon Oct 12"``."""
	day = getdate(day)
	return f"{day:%a} {day:%b} {day.day}"


def undated(message):
	"""An engine sentence without its ``"YYYY-MM-DD: "`` prefix."""
	text = str(message or "")
	if len(text) > 12 and text[10:12] == ": ":
		try:
			datetime.date.fromisoformat(text[:10])
			return text[12:]
		except ValueError:
			pass
	return text


def booking_item(booking, planner=None):
	"""A conflict item from an engine booking, or None for what is not a record (a block, driving).

	``{"doctype", "name", "title", "planner", "own", "kind", "project", "hours", "slot"}``, plus
	``projected``, ``key``, ``serial_no``, ``movable`` and ``from_date`` for a projected visit (its
	record is the contract). ``planner`` is the caller's (``project`` / ``maintenance``) and decides
	``own``; None leaves every item un-owned.
	"""
	booking = booking or {}
	kind, ref = booking.get("kind"), booking.get("ref")
	if not ref:
		return None
	projected = booking.get("projected") or None
	if kind == "task":
		doctype, owner = "Task", "project"
	elif kind == "rental":
		doctype, owner = "Task", "rental"
	elif kind == "visit":
		doctype, owner = (CONTRACT if projected else VISIT), "maintenance"
	elif kind == "travel":
		doctype, owner = TRAVEL, "travel"
	else:
		return None
	name = (projected.get("contract") or ref) if projected else ref
	item = {
		"doctype": doctype,
		"name": name,
		"title": booking.get("label") or name,
		"planner": owner,
		"own": bool(planner) and owner == planner,
		"kind": kind,
		"project": booking.get("project"),
		"hours": round(flt(booking.get("hours")), 2),
		"slot": booking.get("slot"),
	}
	if projected:
		item.update(
			projected=True,
			key=booking.get("key"),
			serial_no=projected.get("serial_no"),
			movable=bool(projected.get("movable")),
			from_date=projected.get("from_date"),
		)
	return item


def task_item(task, planner=None):
	"""A conflict item for a Task row (equipment and qualification conflicts are about tasks)."""
	return booking_item(
		{
			"kind": "rental" if task.get("custom_rental_booking") else "task",
			"ref": task.get("name"),
			"label": task.get("subject") or task.get("name"),
			"project": task.get("project"),
			"hours": 0,
			"slot": engine._slot_text(engine.task_slot(task)),
		},
		planner,
	)


def _identity(item):
	return (item["doctype"], item["name"], item.get("key") or "")


def _unique(items):
	out, seen = [], set()
	for item in items:
		if item and _identity(item) not in seen:
			seen.add(_identity(item))
			out.append(item)
	return out


def conflict_key(kind, date, anchor, items):
	"""``kind|date|anchor|hash``: the same conflict gets the same key on every load.

	``anchor`` is the Planner Resource (people), ``type:name`` (equipment) or ``-`` (a task's
	qualifications); the hash is of the sorted records involved, so it stays short enough for a Data
	field however many there are, and changes when a different set of records is involved.
	"""
	names = sorted(":".join(_identity(item)) for item in items or [])
	digest = hashlib.sha1(",".join(names).encode("utf-8")).hexdigest()[:16]
	return f"{kind}|{date}|{anchor or '-'}|{digest}"


def conflict_fingerprint(message, items, block=None):
	"""A hash of the conflict's wording, its records' ``modified`` stamps and any block's: an
	acknowledgement hides the conflict only while this is unchanged."""
	payload = {
		"message": message,
		"items": sorted(
			[item["doctype"], item["name"], item.get("key") or "", str(item.get("modified"))]
			for item in items or []
		),
		"block": [block.get("name"), str(block.get("modified"))] if block else None,
	}
	return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def person_conflicts(days, resources, start, end, planner):
	"""The conflicts on people's days from ``start`` to ``end`` (the engine's own sentences)."""
	people = {r.get("name"): r for r in resources or []}
	out = []
	for resource, per_day in (days or {}).items():
		person = people.get(resource) or {}
		label = person.get("label") or resource
		for day_text, cell in sorted((per_day or {}).items()):
			if not (start <= getdate(day_text) <= end):
				continue
			for detail in engine.day_conflict_details(
				cell.get("capacity"), cell.get("off"), cell.get("bookings")
			):
				items = _unique(booking_item(b, planner) for b in detail["bookings"])
				if not items:
					continue
				block = detail.get("block")
				out.append(
					{
						"kind": detail["type"],
						"date": str(getdate(day_text)),
						"resource": resource,
						"resource_label": label,
						"user": person.get("user"),
						"sentence": detail["message"],
						"message": f"{label}: {detail['message']}",
						"items": items,
						"block": {
							"name": block.get("ref"),
							"all_day": bool(block.get("all_day")),
							"slot": block.get("slot"),
							"window": block.get("window"),
							"note": None,
						}
						if block
						else None,
						"equipment": None,
						"credentials": None,
					}
				)
	return out


def _describe(task):
	return f"{task.get('name')} ({task.get('subject') or task.get('name')})"


def equipment_conflict_list(findings, tasks, start, end, planner):
	"""One conflict per vehicle or asset clash (``crew_availability.equipment_findings``).

	Two tasks on one vehicle are one conflict, dated the first day they share (from their whole
	spans, so the date, and the key, do not move as the range does); a vehicle In Shop is dated the
	task's first day; an asset out on another booking, the first day both cover.
	"""
	rows = {t.get("name"): t for t in tasks or []}
	groups = {}
	for finding in findings or []:
		task = rows.get(finding.get("task"))
		span = engine.task_span(task) if task else None
		if not span:
			continue
		ref = tuple(finding.get("ref") or ())
		label = finding.get("label") or (ref[1] if len(ref) > 1 else "")
		if finding.get("type") == "shared":
			other = rows.get(finding.get("other"))
			other_span = engine.task_span(other) if other else None
			if not other_span or (finding.get("day") and finding["day"] > end):
				continue
			names = tuple(sorted((task["name"], other["name"])))
			group = ("shared", ref, names)
			date = max(span[0], other_span[0])
			message = _("{0} is on {1} and {2}").format(
				label, _describe(rows[names[0]]), _describe(rows[names[1]])
			)
		elif finding.get("type") == "status":
			if span[0] > end or span[1] < start:
				continue
			names = (task["name"],)
			group = ("status", ref, names)
			date = span[0]
			message = undated(finding.get("message"))
		else:
			if finding.get("day") and finding["day"] > end:
				continue
			booking = finding.get("booking") or {}
			names = (task["name"],)
			group = ("booking", ref, (task["name"], booking.get("name")))
			first = engine._booking_days(booking)
			date = max(span[0], first[0]) if first else span[0]
			message = undated(finding.get("message"))
		if group in groups:
			continue
		groups[group] = {
			"kind": "equipment",
			"date": str(date),
			"resource": None,
			"resource_label": label,
			"user": None,
			"sentence": message,
			"message": message,
			"items": _unique(task_item(rows[name], planner) for name in names),
			"block": None,
			"equipment": {
				"type": ref[0] if ref else None,
				"name": ref[1] if len(ref) > 1 else None,
				"label": label,
			},
			"credentials": None,
		}
	return list(groups.values())


def qualification_conflict_list(tasks, crews, credentials, held, start, end, planner):
	"""One conflict per firm task whose crew lacks a required qualification (the Phase 3A rule,
	``project_planner.qualification_gaps``), dated the task's first day. None held (HR not
	installed) means unknown, never a gap."""
	if held is None:
		return []
	out = []
	for task in tasks or []:
		name = task.get("name")
		span = engine.task_span(task)
		if engine.is_tentative(task) or not span or span[0] > end or span[1] < start:
			continue
		gaps = pp.qualification_gaps((credentials or {}).get(name), (crews or {}).get(name), held, span[1])
		if not gaps:
			continue
		warning = pp.qualification_warning(gaps)
		out.append(
			{
				"kind": "qualification",
				"date": str(span[0]),
				"resource": None,
				"resource_label": None,
				"user": None,
				"sentence": warning,
				"message": f"{task.get('subject') or name}: {warning}",
				"items": _unique([task_item(task, planner)]),
				"block": None,
				"equipment": None,
				"credentials": list(gaps),
			}
		)
	return out


def _who_is_free_range(task, today):
	"""``(first, last)`` for asking who is free over a task's days: from today when it is under way,
	at most 31 days (``who_is_free``'s limit)."""
	span = engine.task_span(task)
	first = max(span[0], today) if span[1] >= today else span[0]
	last = min(span[1], first + datetime.timedelta(days=pp.WHO_FREE_MAX_DAYS - 1))
	return first, max(last, first)


def fixes_for(conflict, days, planner, today, settings=None, tasks=None, crews=None):
	"""The one-click fixes for a conflict: for each owned item, then ``keep``.

	Each fix is ``{"type", "label", "doctype", "name", "method", "args", ...}``; ``method`` is the
	existing endpoint the page calls and ``args`` what to send (the page adds ``reason`` when the
	answer is ``needs_reason``, and ``draft=1`` in draft mode, exactly as for a drag):

	* ``next_free_day``: the person's first day after the conflict (never before today) with the
	  item's hours free, from the same engine pass (``crew_availability.next_free_day``), omitted
	  when there is none within 30 days. Tasks of one day (``project_planner.save_task(task, modified,
	  start)``, which keeps a slot's times), visits not started (``maintenance_planner.move_visit(
	  record, date, modified)``) and the movable projected visit (``maintenance_planner.move_projected(
	  contract, from_date, to_date, serial_no)``).
	* ``pick_free``: ``who_is_free`` (``{"method", "args"}``: the date and the hours needed) shows
	  who could; the person picked fills ``pick.arg`` from the who-is-free entry's ``pick.field``.
	  ``project_planner.swap_crew(task, from_resource, to_resource, modified)`` for a task;
	  ``maintenance_planner.move_visit(record, technician, from_user, modified)`` for a draft visit
	  (``technician`` is the picked entry's ``user``); ``project_planner.add_crew(task, resource,
	  modified)`` for a qualification gap, with ``credentials`` to say what is missing.
	* ``pencil`` (project tasks only; visits have no pencil): ``save_task(task, modified,
	  tentative=1)``. A pencil never conflicts.
	* ``keep`` (always): :func:`acknowledge_conflict` with ``key``, ``items``, ``planner`` and
	  ``fingerprint``; the page asks for the reason.
	"""
	settings = settings or {}
	tasks = tasks or {}
	kind, person, date = conflict.get("kind"), conflict.get("resource"), getdate(conflict.get("date"))
	after = max(date, today - datetime.timedelta(days=1))
	out = []
	for item in conflict.get("items") or []:
		if not item.get("own"):
			continue
		hours = flt(item.get("hours"))
		ask_hours = round(hours, 2) if 0 < hours <= 24 else pp.WHO_FREE_DEFAULT_HOURS
		if item["doctype"] == "Task" and item.get("kind") == "task":
			args = {"task": item["name"], "modified": item.get("modified")}
			if kind in PERSON_KINDS and person:
				if item.get("single_day"):
					target = engine.next_free_day(days, person, hours, after, exclude_task=item["name"])
					if target:
						out.append(
							{
								"type": "next_free_day",
								"label": _("Move to {0}").format(day_label(target)),
								"doctype": "Task",
								"name": item["name"],
								"resource": person,
								"date": str(target),
								"from_date": str(date),
								"hours": hours,
								"method": PP_METHOD + "save_task",
								"args": dict(args, start=str(target)),
							}
						)
				out.append(
					{
						"type": "pick_free",
						"label": _("Pick someone who's free"),
						"doctype": "Task",
						"name": item["name"],
						"resource": person,
						"user": conflict.get("user"),
						"date": str(date),
						"hours": ask_hours,
						"who_is_free": {
							"method": PP_METHOD + "who_is_free",
							"args": {"start": str(date), "hours": ask_hours},
						},
						"method": PP_METHOD + "swap_crew",
						"args": dict(args, from_resource=person),
						"pick": {"arg": "to_resource", "field": "resource"},
					}
				)
			if kind == "qualification" and item["name"] in tasks:
				task = tasks[item["name"]]
				first, last = _who_is_free_range(task, today)
				need = pp.hours_per_day(task, len((crews or {}).get(item["name"]) or []), settings, first)
				need = (
					round(flt(need), 2)
					if need and 0 < flt(need) <= 24
					else flt(settings.get("default_day_hours") or 8)
				)
				out.append(
					{
						"type": "pick_free",
						"label": _("Pick someone who's free"),
						"doctype": "Task",
						"name": item["name"],
						"resource": None,
						"credentials": list(conflict.get("credentials") or []),
						"date": str(first),
						"hours": need,
						"who_is_free": {
							"method": PP_METHOD + "who_is_free",
							"args": {"start": str(first), "end": str(last), "hours": need},
						},
						"method": PP_METHOD + "add_crew",
						"args": dict(args),
						"pick": {"arg": "resource", "field": "resource"},
					}
				)
			if kind != "qualification":
				out.append(
					{
						"type": "pencil",
						"label": _("Make it pencil"),
						"doctype": "Task",
						"name": item["name"],
						"method": PP_METHOD + "save_task",
						"args": dict(args, tentative=1),
					}
				)
		elif item["doctype"] == VISIT and kind in PERSON_KINDS and person:
			args = {"record": item["name"], "modified": item.get("modified")}
			if item.get("movable"):
				target = engine.next_free_day(days, person, hours, after)
				if target:
					out.append(
						{
							"type": "next_free_day",
							"label": _("Move to {0}").format(day_label(target)),
							"doctype": VISIT,
							"name": item["name"],
							"resource": person,
							"date": str(target),
							"from_date": str(date),
							"hours": hours,
							"method": MP_METHOD + "move_visit",
							"args": dict(args, date=str(target)),
						}
					)
			if item.get("editable") and conflict.get("user"):
				out.append(
					{
						"type": "pick_free",
						"label": _("Pick someone who's free"),
						"doctype": VISIT,
						"name": item["name"],
						"resource": person,
						"user": conflict.get("user"),
						"date": str(date),
						"hours": ask_hours,
						"who_is_free": {
							"method": PP_METHOD + "who_is_free",
							"args": {"start": str(date), "hours": ask_hours},
						},
						"method": MP_METHOD + "move_visit",
						"args": dict(args, from_user=conflict.get("user")),
						"pick": {"arg": "technician", "field": "user"},
					}
				)
		elif item["doctype"] == CONTRACT and kind in PERSON_KINDS and person and item.get("movable"):
			target = engine.next_free_day(days, person, hours, after)
			if target:
				out.append(
					{
						"type": "next_free_day",
						"label": _("Move to {0}").format(day_label(target)),
						"doctype": CONTRACT,
						"name": item["name"],
						"resource": person,
						"date": str(target),
						"from_date": str(date),
						"hours": hours,
						"method": MP_METHOD + "move_projected",
						"args": {
							"contract": item["name"],
							"from_date": item.get("from_date") or str(date),
							"to_date": str(target),
							"serial_no": item.get("serial_no"),
						},
					}
				)
	out.append(
		{
			"type": "keep",
			"label": _("Keep it with a reason"),
			"method": SELF_METHOD + "acknowledge_conflict",
			"args": {
				"key": conflict.get("key"),
				"items": [{"doctype": i["doctype"], "name": i["name"]} for i in conflict.get("items") or []],
				"planner": planner,
				"fingerprint": conflict.get("fingerprint"),
			},
			"needs": "reason",
		}
	)
	return out


# ---------------------------------------------------------------------- reads


def _planner(value):
	planner = str(pp.given(value) or "project").strip().lower()
	if planner not in PLANNERS:
		frappe.throw(_("The planner must be one of: {0}.").format(", ".join(PLANNERS)))
	return planner


def _gate(planner):
	if planner == "maintenance":
		mp._require_planner()
	else:
		pp._require_planner()


def _installed(doctype):
	check = getattr(frappe.db, "table_exists", None)
	if not callable(check):
		return False
	try:
		return bool(check(doctype))
	except Exception:
		return False


def _decorate(conflicts, tasks, blocks):
	"""Give every item its ``modified`` stamp and whether its planner could move it, from one read
	per doctype (tasks come from the engine pass already)."""
	by_task = {t.get("name"): t for t in tasks or []}
	wanted = {}
	for conflict in conflicts:
		for item in conflict["items"]:
			if item["doctype"] != "Task":
				wanted.setdefault(item["doctype"], set()).add(item["name"])
	found = {}
	for doctype, names in wanted.items():
		fields = ["name", "modified"]
		if doctype == VISIT:
			fields += ["docstatus", "workflow_state", "visit_date"]
		try:
			found[doctype] = {
				row.get("name"): row
				for row in frappe.get_all(
					doctype, filters={"name": ["in", sorted(names)]}, fields=fields, limit_page_length=0
				)
			}
		except Exception:
			frappe.log_error(
				title="Conflict center: record stamps unreadable", message=frappe.get_traceback()
			)
			found[doctype] = {}
	for conflict in conflicts:
		for item in conflict["items"]:
			if item["doctype"] == "Task":
				task = by_task.get(item["name"]) or {}
				span = engine.task_span(task) if task else None
				item["modified"] = str(task.get("modified")) if task.get("modified") else None
				item["start"], item["end"] = (str(span[0]), str(span[1])) if span else (None, None)
				item["single_day"] = bool(span and span[0] == span[1])
				item["movable"] = item.get("kind") == "task"
				item["editable"] = item.get("kind") == "task"
				continue
			row = (found.get(item["doctype"]) or {}).get(item["name"]) or {}
			item["modified"] = str(row.get("modified")) if row.get("modified") else None
			if item["doctype"] == VISIT:
				draft = (
					int(row.get("docstatus") or 0) == 0
					and (row.get("workflow_state") or "") != mp.PENDING_STATE
				)
				item["editable"] = bool(row) and draft
				item["movable"] = bool(row) and draft and not row.get("visit_date")
			elif item["doctype"] == CONTRACT:
				item["editable"] = False
				item["movable"] = bool(item.get("movable"))
			else:
				item["editable"] = item["movable"] = False
		block = conflict.get("block")
		if block and block.get("name"):
			block["modified"] = (blocks.get(block["name"]) or {}).get("modified")


def _acks(keys):
	"""``{key: newest Planner Conflict Ack row}``."""
	keys = sorted({k for k in keys or () if k})
	if not keys or not _installed(ACK_DOCTYPE):
		return {}
	out = {}
	try:
		rows = frappe.get_all(
			ACK_DOCTYPE,
			filters={"conflict_key": ["in", keys]},
			fields=["conflict_key", "fingerprint", "reason", "acknowledged_by", "acknowledged_on"],
			order_by="acknowledged_on desc, creation desc",
			limit_page_length=0,
		)
	except Exception:
		frappe.log_error(title="Conflict center: acknowledgements unreadable", message=frappe.get_traceback())
		return {}
	for row in rows or []:
		out.setdefault(row.get("conflict_key"), row)
	return out


def _full_names(users):
	users = sorted({u for u in users or () if u})
	if not users:
		return {}
	return {
		row.get("name"): row.get("full_name") or row.get("name")
		for row in frappe.get_all(
			"User", filters={"name": ["in", users]}, fields=["name", "full_name"], limit_page_length=0
		)
	}


def acknowledgement(row, names=None):
	"""``{"reason", "by", "by_name", "on"}`` for an ack row."""
	by = row.get("acknowledged_by")
	return {
		"reason": row.get("reason"),
		"by": by,
		"by_name": (names or {}).get(by) or by,
		"on": str(row.get("acknowledged_on")) if row.get("acknowledged_on") else None,
	}


def _collect(start, end, planner, lookahead=LOOKAHEAD_DAYS):
	"""Every conflict from ``start`` to ``end`` for ``planner``, from ONE engine pass (Google off)."""
	today = getdate(nowdate())
	data = engine._compute(start, end + datetime.timedelta(days=lookahead), google=False, equipment=True)
	tasks = data.get("tasks") or []
	conflicts = person_conflicts(data.get("days"), data.get("resources"), start, end, planner)
	conflicts += equipment_conflict_list(data.get("equipment_findings"), tasks, start, end, planner)
	credentials = pp._credentials([t.get("name") for t in tasks])
	if credentials:
		held = pp._held_credentials(
			{r["name"]: r for r in data.get("resources") or []},
			{c for types in credentials.values() for c in types},
		)
		conflicts += qualification_conflict_list(
			tasks, data.get("crews"), credentials, held, start, end, planner
		)

	_decorate(conflicts, tasks, data.get("blocks") or {})
	for conflict in conflicts:
		anchor = conflict.get("resource")
		if conflict["kind"] == "equipment" and conflict.get("equipment"):
			anchor = "{type}:{name}".format(**conflict["equipment"])
		conflict["key"] = conflict_key(conflict["kind"], conflict["date"], anchor, conflict["items"])
		conflict["fingerprint"] = conflict_fingerprint(
			conflict["message"], conflict["items"], conflict.get("block")
		)
		conflict["own"] = any(item.get("own") for item in conflict["items"])

	acks = _acks(c["key"] for c in conflicts)
	names = _full_names(row.get("acknowledged_by") for row in acks.values()) if acks else {}
	notes = engine.block_notes([c["block"]["name"] for c in conflicts if c.get("block")])
	by_task = {t.get("name"): t for t in tasks}
	for conflict in conflicts:
		row = acks.get(conflict["key"])
		conflict["acknowledged"] = (
			acknowledgement(row, names) if row and row.get("fingerprint") == conflict["fingerprint"] else None
		)
		if conflict.get("block"):
			conflict["block"]["note"] = notes.get(conflict["block"]["name"])
		conflict["fixes"] = fixes_for(
			conflict, data.get("days"), planner, today, data.get("settings"), by_task, data.get("crews")
		)
	conflicts.sort(
		key=lambda c: (c["date"], KIND_ORDER.get(c["kind"], 99), str(c.get("resource_label") or ""), c["key"])
	)
	return conflicts


# ---------------------------------------------------------------------- endpoints


@frappe.whitelist()
def get_conflicts(start, end, planner="project"):
	"""Everything wrong from ``start`` to ``end`` (at most 60 days), sorted by date.

	``planner`` is ``project`` or ``maintenance`` and decides which items are ``own`` and so which
	have fixes; the list itself is the same for both. Each conflict is ``{"key", "kind", "date",
	"resource", "resource_label", "user", "message", "sentence", "items": [{"doctype", "name",
	"title", "planner", "own", "kind", "project", "hours", "slot", "modified", "movable",
	"editable", ...}], "fixes": [...], "acknowledged": None | {"reason", "by", "by_name", "on"},
	"own", "fingerprint", "block": None | {"name", "all_day", "slot", "window", "note"},
	"equipment": None | {"type", "name", "label"}, "credentials": None | [...]}``. ``kind`` is one
	of :data:`KINDS`; ``block.note`` is filled only for a caller who may read it. One engine pass,
	Google switched off: drive times are cached or estimated.
	"""
	planner = _planner(planner)
	_gate(planner)
	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(_("The end date is before the start date."))
	if date_diff(end, start) + 1 > MAX_DAYS:
		frappe.throw(_("Pick a range of {0} days or less.").format(MAX_DAYS))
	return _collect(start, end, planner)


@frappe.whitelist()
def get_next_free_day(resource, hours=None, after=None, task=None):
	"""The first day after ``after`` (default today, never before today) on which ``resource`` has
	``hours`` free, within 30 days (``crew_availability.next_free_day``). Any planner role.

	With ``task`` and no ``hours``, the hours one person needs per day of that task
	(``project_planner.hours_per_day``; its whole day when it has no estimate), and the task itself
	is left out of the count. ``{"resource", "after", "hours", "task", "date", "horizon", "note"}``;
	``date`` is None when no day fits. Google-free.
	"""
	planner_blocks._require_access()
	today = getdate(nowdate())
	after = max(getdate(pp.given(after) or today), today - datetime.timedelta(days=1))
	task = pp.given(task)
	need = flt(pp.given(hours)) if pp.given(hours) is not None else None
	if need is None and task:
		doc = frappe.get_doc("Task", task)
		need = pp.hours_per_day(
			pp._state(doc),
			len(pp._current_crew(doc)),
			engine.get_settings(),
			after + datetime.timedelta(days=1),
		)
	if need is not None and not 0 <= need <= 24:
		frappe.throw(_("Hours must be between 0 and 24."))
	horizon = engine.NEXT_FREE_HORIZON_DAYS
	data = engine._compute(
		after + datetime.timedelta(days=1),
		after + datetime.timedelta(days=horizon),
		[resource],
		exclude={task} if task else (),
		google=False,
	)
	if resource not in {r.get("name") for r in data.get("resources") or []}:
		frappe.throw(_("{0} is not an active Planner Resource.").format(resource))
	day = engine.next_free_day(data.get("days"), resource, need, after, exclude_task=task, horizon=horizon)
	return {
		"resource": resource,
		"after": str(after),
		"hours": need,
		"task": task,
		"date": str(day) if day else None,
		"horizon": horizon,
		"note": None if day else _("No day in the next {0} days has the hours free.").format(horizon),
	}


def _key_date(key):
	parts = str(key or "").split("|")
	if len(parts) < 4 or parts[0] not in KINDS:
		frappe.throw(_("That is not a Conflict center key."))
	try:
		return datetime.date.fromisoformat(parts[1])
	except ValueError:
		frappe.throw(_("That is not a Conflict center key."))


def _item_names(items):
	"""The record names the page sent back with a ``keep``, or None when it sent none."""
	items = pp.parse_list(pp.given(items))
	if items is None:
		return None
	out = set()
	for entry in items:
		if isinstance(entry, dict):
			out.add(entry.get("name"))
		elif isinstance(entry, list | tuple) and len(entry) >= 2:
			out.add(entry[1])
		else:
			out.add(entry)
	return {name for name in out if name}


@frappe.whitelist(methods=["POST"])
def acknowledge_conflict(key, reason, items=None, planner="project", fingerprint=None):
	"""Keep a conflict on purpose: "Keep it with a reason" in the Conflict center.

	A reason is required. The conflict is worked out again for its day; when it is gone, or its
	records (``items``) or ``fingerprint`` no longer match what the page showed, nothing is written
	and the page is told to refresh. Otherwise a ``Planner Conflict Ack`` is stored and every
	**owned** item (the caller's planner moves it, and the caller may write it) gets the timeline
	comment "Kept a conflict on Thu Oct 12: <message>. Reason: <reason>", escaped. A conflict with
	no owned item may be kept only by a scheduler (it writes on nobody's timeline).

	Returns ``{"key", "fingerprint", "acknowledged": {"reason", "by", "by_name", "on"},
	"commented": [{"doctype", "name"}]}``.
	"""
	planner = _planner(planner)
	_gate(planner)
	reason = str(pp.given(reason) or "").strip()
	if not reason:
		frappe.throw(_("Say why this conflict is being kept."))
	day = _key_date(key)
	found = next((c for c in _collect(day, day, planner, lookahead=0) if c["key"] == key), None)
	if not found:
		frappe.throw(_(STALE))
	wanted = _item_names(items)
	if wanted is not None and wanted != {item["name"] for item in found["items"]}:
		frappe.throw(_(STALE))
	if pp.given(fingerprint) and pp.given(fingerprint) != found["fingerprint"]:
		frappe.throw(_(STALE))

	owned = [item for item in found["items"] if item.get("own")]
	docs = []
	for item in owned:
		doc = frappe.get_doc(item["doctype"], item["name"])
		doc.check_permission("write")
		docs.append(doc)
	if not owned and not planner_blocks.can_schedule():
		frappe.throw(
			_("Only a planner can keep a conflict that has nothing on this planner in it."),
			frappe.PermissionError,
		)

	user = frappe.session.user
	when = now_datetime()
	frappe.get_doc(
		{
			"doctype": ACK_DOCTYPE,
			"conflict_key": key,
			"kind": found["kind"],
			"conflict_date": found["date"],
			"planner": planner,
			"message": found["message"],
			"fingerprint": found["fingerprint"],
			"reason": reason,
			"acknowledged_by": user,
			"acknowledged_on": when,
		}
	).insert(ignore_permissions=True)
	text = _("Kept a conflict on {0}: {1}. Reason: {2}").format(
		day_label(found["date"]),
		frappe.utils.escape_html(found["message"]),
		frappe.utils.escape_html(reason),
	)
	for doc in docs:
		doc.add_comment("Comment", text)
	return {
		"key": key,
		"fingerprint": found["fingerprint"],
		"acknowledged": acknowledgement(
			{"reason": reason, "acknowledged_by": user, "acknowledged_on": when}, _full_names([user])
		),
		"commented": [{"doctype": item["doctype"], "name": item["name"]} for item in owned],
	}
