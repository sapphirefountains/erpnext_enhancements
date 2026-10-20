"""My Work: one place to see and act on what you have been assigned (WI-081 Phase 1).

ERPNext's answer to "what am I meant to be doing?" is the ToDo list: rows that say
"Assignment for Opportunity CRM-OPP-2026-00187", a form you have to open to find the record, and
the record's link buried at the bottom of it. This module serves the screens that replace that
experience -- the My Work page (``task_enhancements/page/my_work``), the "Assigned to you" banner on
a record and the sidebar count (``public/js/global_enhancements/my_work.js``) -- **on top of
Frappe's own assignments**. Nothing here stores an assignment anywhere but a ToDo: the sidebar's
Assign To, Assignment Rules, the crew mirrors, Triton and the AI follow-up tool keep writing ToDos,
and everything they write shows up here unchanged.

Endpoints:

* :func:`get_items` -- one view (``mine``, ``delegated``, ``unclaimed``) as rows that lead with the
  *record* ("Smith Residence rebuild · Opportunity · $48,000"), bucketed Overdue / Today / No due
  date / This week / Later, with what the viewer may do to each.
* :func:`get_count` -- the sidebar number: your open assignments not put off to a later day.
* :func:`get_record_assignments` -- the banner on a record: your open assignments on it.
* Actions: :func:`complete`, :func:`reschedule`, :func:`reassign`, :func:`reply`, :func:`claim`,
  :func:`nudge`, :func:`withdraw`.
* Backlog triage (System Manager): :func:`triage_preview`, :func:`triage_apply`,
  :func:`triage_undo`; the rules are ``task_enhancements/backlog_triage.py``.

Things this module is careful about:

* **Permission is checked here, per action, against the ToDo's own people.** v16's ToDo permission
  query shows a non-System-Manager only the ToDos they are allocated, assigned or own
  (``todo.py``), and an endpoint is reachable by any signed-in user. So: only the assignee
  completes or reschedules (``assign_to.close`` refuses anyone else too); the assignee or whoever
  assigned it reassigns; only whoever assigned it nudges or withdraws it; a System Manager may
  reschedule, reassign and withdraw anything, as the Desk already lets them.
* **The Unclaimed view reads ToDos the viewer cannot otherwise see.** Triton files call follow-ups
  with no assignee and no record, owned by triton@, so only a System Manager could ever see them
  -- 201 of them on 2026-10-20, the newest that morning. An unclaimed ToDo with no record is shown
  to every System User; one on a record only to those who may read that record.
* **A ToDo's date is usually not a due date** (``task_enhancements/assignments.py``). Reassign
  therefore carries a date over only when it is a real one; carrying the creation-day stamp onto a
  new ToDo made later would turn "no due date" into "overdue".
* **Closing goes through the ToDo controller** (``auto_close.close_todo``), so the record's
  assignee avatars update exactly as when the sidebar closes one.
* **The record's facts are an allowlist** (:data:`FACTS`), read only when the doctype has the
  field, and formatted by ``frappe.format_value``; nothing the client sends reaches a field list.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, escape_html, get_fullname, getdate, now_datetime, nowdate

from erpnext_enhancements.task_enhancements import assignments
from erpnext_enhancements.task_enhancements.auto_close import close_todo

VIEWS = ("mine", "delegated", "unclaimed")

#: The most rows one view returns. Production's heaviest inbox held 379 before the triage.
MAX_ROWS = 500

#: What a row says about its record beyond the title: (doctype -> fields), shown when present.
FACTS = {
	"Opportunity": ("party_name", "status", "opportunity_amount"),
	"Task": ("project", "status", "exp_end_date"),
	"Project": ("customer", "status"),
	"Customer": ("customer_group", "territory"),
	"Lead": ("company_name", "status"),
	"Sapphire Maintenance Record": ("customer", "project", "workflow_state"),
	"Purchase Order": ("supplier", "status", "grand_total"),
	"Material Request": ("material_request_type", "status", "schedule_date"),
	"Quotation": ("party_name", "status", "grand_total"),
	"Issue": ("customer", "status"),
	"Sales Order": ("customer", "status", "grand_total"),
	"Sales Invoice": ("customer", "status", "outstanding_amount"),
}

_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}

#: A nudge is allowed once per assignment in this many seconds.
NUDGE_COOLDOWN = 12 * 60 * 60

_TODO_FIELDS = [
	"name",
	"status",
	"priority",
	"date",
	"description",
	"allocated_to",
	"assigned_by",
	"owner",
	"reference_type",
	"reference_name",
	"creation",
	"modified",
]


# ---------------------------------------------------------------------------
# Reading


@frappe.whitelist(methods=["GET"])
def get_items(view: str = "mine"):
	"""One view of open assignments, presented and bucketed for the My Work page.

	Returns ``{"view", "today", "items": [...], "counts": {bucket: n}, "tabs": {view: n},
	"is_manager"}``. Items come sorted High priority first, then by due date, then newest first.
	"""
	if view not in VIEWS:
		frappe.throw(_("Unknown view."))
	user = frappe.session.user
	today = getdate(nowdate())
	rows = _rows(view, user)
	items = _present(rows, user, today)
	counts = {b: 0 for b in assignments.BUCKETS}
	for item in items:
		counts[item["bucket"]] += 1
	return {
		"view": view,
		"today": str(today),
		"items": items,
		"counts": counts,
		"tabs": {v: (len(items) if v == view else _count_view(v, user)) for v in VIEWS},
		"is_manager": "System Manager" in frappe.get_roles(user),
	}


@frappe.whitelist(methods=["GET"])
def get_count():
	"""The sidebar number: your open assignments that are overdue, due today or undated.

	One query over your own open ToDos; the bucketing is ``assignments.bucket``, so the count and
	the page cannot disagree. ``overdue`` is returned separately for the red mark.
	"""
	user = frappe.session.user
	today = getdate(nowdate())
	rows = frappe.get_all(
		"ToDo",
		filters={"status": "Open", "allocated_to": user},
		fields=["date", "creation"],
		limit_page_length=0,
	)
	count = overdue = 0
	for row in rows:
		which = assignments.bucket(assignments.due_date(row.date, row.creation), today)
		if which in assignments.ACTIONABLE:
			count += 1
		if which == "overdue":
			overdue += 1
	return {"count": count, "overdue": overdue}


@frappe.whitelist(methods=["GET"])
def get_record_assignments(doctype: str, name: str):
	"""Your open assignments on one record, for the banner at the top of its form.

	The form only asks when its docinfo already lists you as an assignee, so this is one small
	query per form you are assigned on, not one per form.
	"""
	if not (doctype and name and frappe.db.exists("DocType", doctype)):
		return []
	if not frappe.has_permission(doctype, "read", doc=name):
		return []
	user = frappe.session.user
	rows = frappe.get_all(
		"ToDo",
		filters={
			"status": "Open",
			"allocated_to": user,
			"reference_type": doctype,
			"reference_name": str(name),
		},
		fields=_TODO_FIELDS,
		order_by="creation desc",
	)
	return _present(rows, user, getdate(nowdate()), with_facts=False)


def _rows(view, user):
	if view == "unclaimed":
		return _unclaimed_rows(user)
	filters = {"status": "Open"}
	if view == "mine":
		filters["allocated_to"] = user
	else:
		filters["assigned_by"] = user
		filters["allocated_to"] = ["!=", user]
	return frappe.get_all(
		"ToDo",
		filters=filters,
		fields=_TODO_FIELDS,
		order_by="creation desc",
		limit_page_length=MAX_ROWS,
	)


def _unclaimed_rows(user):
	if not _is_system_user(user):
		return []
	rows = frappe.get_all(
		"ToDo",
		filters={"status": "Open", "allocated_to": ["is", "not set"]},
		fields=_TODO_FIELDS,
		order_by="creation desc",
		limit_page_length=MAX_ROWS,
	)
	return [row for row in rows if _may_see_unclaimed(row, user)]


def _may_see_unclaimed(row, user):
	if not (row.reference_type and row.reference_name):
		return True
	try:
		return bool(frappe.has_permission(row.reference_type, "read", doc=row.reference_name, user=user))
	except Exception:
		return False


def _count_view(view, user):
	if view == "mine":
		return frappe.db.count("ToDo", {"status": "Open", "allocated_to": user})
	if view == "delegated":
		return frappe.db.count("ToDo", {"status": "Open", "assigned_by": user, "allocated_to": ["!=", user]})
	return len(_unclaimed_rows(user))


def _present(rows, user, today, with_facts=True):
	"""ToDo rows as the items every My Work surface draws."""
	if not rows:
		return []
	records = _record_info(rows, with_facts)
	people = _full_names({r.allocated_to for r in rows} | {r.assigned_by for r in rows})
	is_manager = "System Manager" in frappe.get_roles(user)
	day_ago = add_to_date(now_datetime(), days=-1)
	items = []
	for row in rows:
		due = assignments.due_date(row.date, row.creation)
		info = records.get((row.reference_type, row.reference_name)) or {}
		ask = assignments.the_ask(row.description, row.reference_type, row.reference_name)
		items.append(
			{
				"name": row.name,
				"ask": ask,
				"priority": row.priority or "Medium",
				"due": str(due) if due else None,
				"bucket": assignments.bucket(due, today),
				"assigned_on": str(assignments.as_date(row.creation)),
				"is_new": bool(row.creation and row.creation >= day_ago),
				"allocated_to": row.allocated_to,
				"allocated_to_name": people.get(row.allocated_to) or row.allocated_to,
				"assigned_by": row.assigned_by,
				"assigned_by_name": people.get(row.assigned_by) or row.assigned_by,
				"reference_type": row.reference_type,
				"reference_name": row.reference_name,
				"doctype_label": _(row.reference_type) if row.reference_type else None,
				"title": info.get("title")
				or (row.reference_name if row.reference_type else (ask or row.name)),
				"facts": info.get("facts") or [],
				"record_missing": bool(row.reference_type and row.reference_name and not info),
				"can": _abilities(row, user, is_manager),
			}
		)
	items.sort(
		key=lambda i: (
			assignments.BUCKETS.index(i["bucket"]),
			_PRIORITY_RANK.get(i["priority"], 1),
			i["due"] or "9999-12-31",
			"" if i["is_new"] else "1",
		)
	)
	return items


def _abilities(row, user, is_manager):
	mine = row.allocated_to == user
	theirs = row.assigned_by == user or (not row.assigned_by and row.owner == user)
	unclaimed = not row.allocated_to
	return {
		"complete": mine,
		"reschedule": mine or is_manager,
		"reassign": mine or theirs or is_manager,
		"reply": mine and bool(row.assigned_by) and row.assigned_by != user,
		"claim": unclaimed,
		"nudge": theirs and bool(row.allocated_to) and not mine,
		"withdraw": (theirs or is_manager) and not mine and not unclaimed,
	}


def _record_info(rows, with_facts):
	"""{(doctype, name): {"title", "facts"}} for every record the rows name; missing ones absent."""
	by_type = {}
	for row in rows:
		if row.reference_type and row.reference_name:
			by_type.setdefault(row.reference_type, set()).add(str(row.reference_name))
	out = {}
	for doctype, names in by_type.items():
		try:
			meta = frappe.get_meta(doctype)
		except Exception:
			continue
		title_field = meta.get_title_field() if not meta.issingle else None
		fact_fields = [f for f in FACTS.get(doctype, ()) if meta.has_field(f)] if with_facts else []
		fields = ["name"]
		if title_field and title_field != "name" and meta.has_field(title_field):
			fields.append(title_field)
		fields += [f for f in fact_fields if f not in fields]
		if meta.issingle:
			for name in names:
				out[(doctype, name)] = {"title": _(doctype), "facts": []}
			continue
		names = list(names)
		for start in range(0, len(names), 500):
			for rec in frappe.get_all(
				doctype, filters={"name": ["in", names[start : start + 500]]}, fields=fields
			):
				title = rec.get(title_field) if title_field else None
				facts = []
				for field in fact_fields:
					value = rec.get(field)
					if value in (None, "") or value == title:
						continue
					df = meta.get_field(field)
					facts.append([_(df.label) if df and df.label else field, _format(value, df)])
				out[(doctype, str(rec.name))] = {"title": title or rec.name, "facts": facts}
	return out


def _format(value, df):
	try:
		text = frappe.format_value(value, df)
	except Exception:
		text = value
	return assignments.plain_text(text)


def _full_names(users):
	users = [u for u in users if u]
	if not users:
		return {}
	rows = frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	return {r.name: r.full_name or r.name for r in rows}


def _is_system_user(user):
	return frappe.db.get_value("User", user, "user_type") == "System User"


# ---------------------------------------------------------------------------
# Acting


def _load(todo, need):
	"""The open ToDo ``todo``, after checking the session user holds ability ``need`` on it."""
	if not todo or not frappe.db.exists("ToDo", todo):
		frappe.throw(_("That to-do no longer exists."), frappe.DoesNotExistError)
	doc = frappe.get_doc("ToDo", todo)
	if doc.status != "Open":
		frappe.throw(_("That to-do is already {0}.").format(_(doc.status).lower()))
	user = frappe.session.user
	can = _abilities(doc, user, "System Manager" in frappe.get_roles(user))
	if need == "claim":
		if doc.allocated_to:
			frappe.throw(_("{0} has already taken this one.").format(get_fullname(doc.allocated_to)))
		if not (_is_system_user(user) and _may_see_unclaimed(doc, user)):
			can["claim"] = False
	if not can.get(need):
		frappe.throw(_("You cannot do that to this to-do."), frappe.PermissionError)
	return doc


def _record_title(doc):
	if not (doc.reference_type and doc.reference_name):
		return None
	try:
		from frappe.desk.doctype.notification_log.notification_log import get_title

		return get_title(doc.reference_type, doc.reference_name)
	except Exception:
		return doc.reference_name


def _notify(user, doc, subject, body=None):
	"""A bell entry (and, per the person's Notification Settings, an email) about ``doc``.

	It points at the record when there is one, else at My Work opened on this assignment.
	"""
	if not user or user == frappe.session.user:
		return
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	notification = {
		"type": "Assignment",
		"subject": subject,
		"from_user": frappe.session.user,
		"email_content": body,
	}
	if doc.reference_type and doc.reference_name:
		notification["document_type"] = doc.reference_type
		notification["document_name"] = doc.reference_name
	else:
		notification["document_type"] = "ToDo"
		notification["document_name"] = doc.name
		notification["link"] = f"/desk/my-work?todo={doc.name}"
	enqueue_create_notification(user, notification)


def _comment(doc, html):
	"""Add a Comment to the assignment's record, or to the ToDo when it names none."""
	if doc.reference_type and doc.reference_name:
		target = frappe.get_doc(doc.reference_type, doc.reference_name)
	else:
		target = doc
	return target.add_comment("Comment", html)


def _what(doc):
	"""The record's title and the ask, escaped, for a notification subject."""
	title = _record_title(doc)
	ask = assignments.truncate(
		assignments.the_ask(doc.description, doc.reference_type, doc.reference_name), 120
	)
	parts = [frappe.bold(escape_html(title))] if title else []
	if ask:
		parts.append(escape_html(ask))
	return ": ".join(parts) or escape_html(doc.name)


@frappe.whitelist(methods=["POST"])
def complete(todo: str, note: str | None = None):
	"""Mark your assignment done, optionally with a note.

	The note goes on the record's timeline as a comment (so it is where the next person looks),
	and whoever assigned it is told it is done, note included.
	"""
	doc = _load(todo, "complete")
	close_todo(doc, "Closed")
	note = (note or "").strip()
	if note:
		_comment(doc, f"<p><b>{escape_html(_('Done'))}:</b> {escape_html(note)}</p>")
	if doc.assigned_by and doc.assigned_by != doc.allocated_to:
		_notify(
			doc.assigned_by,
			doc,
			_("{0} finished {1}").format(
				frappe.bold(escape_html(get_fullname(frappe.session.user))), _what(doc)
			),
			f"<p>{escape_html(note)}</p>" if note else None,
		)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def reschedule(todo: str, date: str | None = None):
	"""Set the day an assignment is due: snooze, a drag to another bucket, or none at all.

	An empty ``date`` takes the due date off. A day in the past is refused: that is not a
	reschedule anyone means.
	"""
	doc = _load(todo, "reschedule")
	if date:
		day = getdate(date)
		if day < getdate(nowdate()):
			frappe.throw(_("Pick today or a later day."))
		doc.db_set("date", str(day))
	else:
		doc.db_set("date", None)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def reassign(todo: str, user: str, note: str | None = None):
	"""Hand an assignment to someone else.

	On a record this is Frappe's own Assign To for the new person -- which shares the record with
	them if they cannot read it, makes them follow it if they asked to, and sends Frappe's
	assignment notification -- then this ToDo is cancelled. Whoever originally assigned it stays
	the assigner, so it stays on their "Assigned by me". A ToDo with no record simply changes hands.
	"""
	doc = _load(todo, "reassign")
	target = frappe.db.get_value("User", user, ["name", "enabled", "user_type"], as_dict=True)
	if not target or not target.enabled or target.user_type != "System User":
		frappe.throw(_("Pick an active user."))
	if target.name == doc.allocated_to:
		frappe.throw(_("It is already theirs."))
	note = (note or "").strip()
	if doc.reference_type and doc.reference_name:
		from frappe.desk.form.assign_to import add

		args = {
			"assign_to": [target.name],
			"doctype": doc.reference_type,
			"name": doc.reference_name,
			"description": doc.description,
			"priority": doc.priority,
			# Handed back to whoever assigned it: they would be "assigning it to themselves", and
			# notify_assignment says nothing about a self-assignment. The reassigner is the assigner then.
			"assigned_by": doc.assigned_by
			if doc.assigned_by and doc.assigned_by != target.name
			else frappe.session.user,
		}
		due = assignments.due_date(doc.date, doc.creation)
		if due:
			args["date"] = str(due)
		add(args)
		close_todo(doc, "Cancelled")
		if note:
			_comment(
				doc,
				f"<p><b>{escape_html(_('Reassigned to {0}').format(get_fullname(target.name)))}:</b> "
				f"{escape_html(note)}</p>",
			)
	else:
		doc.allocated_to = target.name
		doc.save(ignore_permissions=True)
		if note:
			_comment(doc, f"<p>{escape_html(note)}</p>")
		_notify(
			target.name,
			doc,
			_("{0} handed you {1}").format(
				frappe.bold(escape_html(get_fullname(frappe.session.user))), _what(doc)
			),
			f"<p>{escape_html(note)}</p>" if note else None,
		)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def reply(todo: str, message: str):
	"""Answer whoever assigned this, on the record, with an @mention so they are told.

	A Comment that mentions someone notifies them through Frappe's own mention path
	(``Comment.after_insert`` -> ``notify_mentions``), so the reply lands in their bell and email
	and stays on the record's timeline for everyone else.
	"""
	doc = _load(todo, "reply")
	message = (message or "").strip()
	if not message:
		frappe.throw(_("Write a reply first."))
	name = get_fullname(doc.assigned_by)
	mention = (
		f'<span class="mention" data-id="{escape_html(doc.assigned_by)}" '
		f'data-value="{escape_html(name)}" data-denotation-char="@">@{escape_html(name)}</span>'
	)
	_comment(doc, f"<p>{mention} {escape_html(message)}</p>")
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def claim(todo: str):
	"""Take an unclaimed assignment: it becomes yours, and the record lists you as its assignee."""
	frappe.db.get_value("ToDo", todo, "name", for_update=True)
	doc = _load(todo, "claim")
	doc.allocated_to = frappe.session.user
	doc.save(ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def nudge(todo: str):
	"""Remind the assignee that you are waiting on this. Once per assignment every 12 hours."""
	doc = _load(todo, "nudge")
	key = f"ee_my_work_nudge:{doc.name}"
	cache = frappe.cache()
	if cache.get_value(key):
		frappe.throw(_("You nudged this recently. Give them a little time."))
	_notify(
		doc.allocated_to,
		doc,
		_("{0} is waiting on {1}").format(
			frappe.bold(escape_html(get_fullname(frappe.session.user))), _what(doc)
		),
	)
	cache.set_value(key, 1, expires_in_sec=NUDGE_COOLDOWN)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def withdraw(todo: str):
	"""Take back an assignment you made. The assignee is told, as when the sidebar removes one."""
	doc = _load(todo, "withdraw")
	close_todo(doc, "Cancelled")
	if doc.reference_type and doc.reference_name:
		from frappe.desk.form.assign_to import notify_assignment

		notify_assignment(doc.assigned_by, doc.allocated_to, doc.reference_type, doc.reference_name)
	else:
		_notify(
			doc.allocated_to,
			doc,
			_("{0} took back {1}").format(
				frappe.bold(escape_html(get_fullname(frappe.session.user))), _what(doc)
			),
		)
	return {"ok": True}


# ---------------------------------------------------------------------------
# Backlog triage (System Manager)


def _require_manager():
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can tidy the assignment backlog."), frappe.PermissionError)


@frappe.whitelist(methods=["GET"])
def triage_preview():
	"""What the backlog triage would close, by rule, with a sample of each. Changes nothing."""
	_require_manager()
	from erpnext_enhancements.task_enhancements import backlog_triage

	return backlog_triage.preview()


@frappe.whitelist(methods=["POST"])
def triage_apply():
	"""Close what :func:`triage_preview` lists, in a background job. Returns the batch's name."""
	_require_manager()
	from erpnext_enhancements.task_enhancements import backlog_triage

	batch = backlog_triage.new_batch_name()
	frappe.enqueue(
		"erpnext_enhancements.task_enhancements.backlog_triage.apply",
		queue="long",
		timeout=1800,
		batch=batch,
		user=frappe.session.user,
		job_id=f"ee_my_work_triage:{batch}",
	)
	return {"batch": batch}


@frappe.whitelist(methods=["POST"])
def triage_undo(batch: str):
	"""Reopen every ToDo one triage batch closed. Returns how many were reopened."""
	_require_manager()
	from erpnext_enhancements.task_enhancements import backlog_triage

	return {"reopened": backlog_triage.undo(batch)}


@frappe.whitelist(methods=["GET"])
def triage_batches():
	"""The triage batches that have closed anything, newest first, with their counts."""
	_require_manager()
	from erpnext_enhancements.task_enhancements import backlog_triage

	return backlog_triage.batches()
