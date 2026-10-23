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
* :func:`get_todo_view` -- the top of a ToDo's own form: its heading, a card for its record and,
  when it is yours, the same banner (v1.739.0).
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
from frappe.utils import add_to_date, escape_html, formatdate, get_fullname, getdate, now_datetime, nowdate

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
	"role",
	"creation",
	"modified",
]

#: Roles that see the team load board and may reschedule, reassign or take back anyone's
#: assignment (Phase 3). System Manager could already; the others run the work day to day.
LEAD_ROLES = ("System Manager", "President", "Executive Team", "Operations Manager", "Production Manager")


def _todo_fields():
	"""The ToDo columns every read takes, plus this app's own where the site has them yet."""
	fields = list(_TODO_FIELDS)
	if frappe.db.has_column("ToDo", "custom_repeat"):
		fields.append("custom_repeat")
	return fields


def _is_lead(user=None):
	return bool(set(frappe.get_roles(user or frappe.session.user)) & set(LEAD_ROLES))


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
		"is_lead": _is_lead(user),
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
		fields=_todo_fields(),
		order_by="creation desc",
	)
	return _present(rows, user, getdate(nowdate()), with_facts=False)


@frappe.whitelist(methods=["GET"])
def get_todo_view(todo: str):
	"""The top of a ToDo's own form (v1.739.0), now that every link about an assignment that is not
	on a Task or a visit opens its ToDo rather than My Work.

	``{"heading", "status", "record", "item", "due_is_stamp"}``:

	* ``heading`` -- the ask's first line, else the record's title (``assignments.heading``), in
	  place of Frappe's whole description.
	* ``record`` -- the record it is for: ``doctype``, ``label``, ``name``, ``title``, ``facts``
	  (:data:`FACTS`), ``can_read`` and ``missing``; ``None`` when it names none. Its title and facts
	  are read only when the session user may read the record.
	* ``item`` -- the assignment as the banner draws it, with what you may do (Done, Snooze,
	  Reassign, Reply), when it is open and yours; else ``None``.
	* ``due_is_stamp`` -- its date is only the day it was made, which My Work reads as no due date,
	  so the form can say so under the Due Date it shows again.

	``None`` when the ToDo does not exist or the session user may not read it.
	"""
	if not todo or not frappe.db.exists("ToDo", todo):
		return None
	if not frappe.has_permission("ToDo", "read", doc=todo):
		return None
	user = frappe.session.user
	row = frappe.db.get_value("ToDo", todo, _todo_fields(), as_dict=True)
	if not row:
		return None
	record = None
	if row.reference_type and row.reference_name:
		key = (row.reference_type, str(row.reference_name))
		missing = not _exists(*key)
		readable = not missing and _may_read(*key)
		info = _record_info([row], True).get(key) if readable else None
		record = {
			"doctype": row.reference_type,
			"label": _(row.reference_type),
			"name": row.reference_name,
			"title": (info or {}).get("title") or row.reference_name,
			"facts": (info or {}).get("facts") or [],
			"can_read": readable,
			"missing": missing,
			"button": _(assignments.link_label(row.reference_type)),
		}
	item = None
	if row.status == "Open" and row.allocated_to == user:
		items = _present([row], user, getdate(nowdate()))
		item = items[0] if items else None
	title = record["title"] if record and record["can_read"] else None
	return {
		"heading": assignments.heading(row.description, row.reference_type, row.reference_name, title),
		"status": row.status,
		"record": record,
		"item": item,
		"due_is_stamp": bool(row.date) and assignments.due_date(row.date, row.creation) is None,
	}


def _exists(doctype, name):
	"""Whether the record exists; a ToDo can outlive both its record and its record's DocType."""
	try:
		return bool(frappe.db.exists("DocType", doctype) and frappe.db.exists(doctype, name))
	except Exception:
		return False


def _may_read(doctype, name):
	try:
		return bool(frappe.has_permission(doctype, "read", doc=name))
	except Exception:
		return False


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
		fields=_todo_fields(),
		order_by="creation desc",
		limit_page_length=MAX_ROWS,
	)


def _unclaimed_rows(user):
	if not _is_system_user(user):
		return []
	rows = frappe.get_all(
		"ToDo",
		filters={"status": "Open", "allocated_to": ["is", "not set"]},
		fields=_todo_fields(),
		order_by="creation desc",
		limit_page_length=MAX_ROWS,
	)
	return [row for row in rows if _may_see_unclaimed(row, user)]


def _may_see_unclaimed(row, user, roles=None):
	"""An unclaimed ToDo is for a team (``role``) or for anyone. A team's is for its members and the
	leads; one on a record, only for those who may read the record."""
	if row.get("role"):
		roles = roles if roles is not None else set(frappe.get_roles(user))
		if row.role not in roles and not (roles & set(LEAD_ROLES)):
			return False
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
	roles = set(frappe.get_roles(user))
	is_manager = bool(roles & set(LEAD_ROLES))
	checklists = _checklists([r.name for r in rows])
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
				"team": assignments.team_label(row.get("role")) if row.get("role") else None,
				"repeat": row.get("custom_repeat") or None,
				# Done on it also completes its Task (the page and banner say so before you press it).
				"completes_task": assignments.done_completes_task(
					row.reference_type, row.get("custom_repeat")
				),
				"checklist": checklists.get(row.name, []),
				"can": _abilities(row, user, is_manager, roles),
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


def _abilities(row, user, is_manager, roles=None):
	"""What ``user`` may do to one assignment. ``is_manager`` is a lead (LEAD_ROLES)."""
	mine = row.allocated_to == user
	theirs = row.assigned_by == user or (not row.assigned_by and row.owner == user)
	unclaimed = not row.allocated_to
	team = row.get("role") if hasattr(row, "get") else getattr(row, "role", None)
	in_team = not team or is_manager or (roles is not None and team in roles)
	return {
		"complete": mine,
		"reschedule": mine or is_manager,
		"reassign": mine or theirs or is_manager,
		"reply": mine and bool(row.assigned_by) and row.assigned_by != user,
		"claim": unclaimed and in_team,
		"nudge": theirs and bool(row.allocated_to) and not mine,
		"withdraw": (theirs or is_manager) and not mine and not unclaimed,
		"checklist": mine or theirs or is_manager or (unclaimed and in_team),
	}


def _checklists(names):
	"""{todo: [{"name", "item", "done"}]} for the ToDos given, in their order on the form."""
	if not names or not frappe.db.table_exists("Assignment Checklist Item"):
		return {}
	out = {}
	for row in frappe.get_all(
		"Assignment Checklist Item",
		filters={"parenttype": "ToDo", "parentfield": "custom_checklist", "parent": ["in", names]},
		fields=["name", "parent", "item", "done"],
		order_by="idx asc",
		limit_page_length=0,
	):
		out.setdefault(row.parent, []).append(
			{"name": row.name, "item": row.item, "done": int(row.done or 0)}
		)
	return out


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
	"""A record fact as text. A day -- a Date, or a Datetime at midnight like Task's Expected End
	Date -- is the date alone in the person's format (``assignments.shows_as_day``); everything else
	is ``frappe.format_value``'s."""
	try:
		if assignments.shows_as_day(value, getattr(df, "fieldtype", None)):
			text = formatdate(assignments.as_date(value))
		else:
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


def _is_staff(user):
	"""An active Employee: the gate ``hr_enhancements.availability.unavailable_on`` uses, because
	every customer contact on this site holds a login and who is off is none of their business."""
	return bool(frappe.db.exists("Employee", {"user_id": user, "status": "Active"}))


#: The most people the Assign To dialog asks about at once.
MAX_WORKLOAD_USERS = 20


@frappe.whitelist(methods=["GET"])
def get_workload(users: str | None = None, date: str | None = None):
	"""For the Assign To dialog (WI-081 Phase 2): how loaded each picked person already is.

	``[{"user", "name", "open", "overdue", "due_that_day", "away": [sentence, ...]}]`` in the order
	asked. ``open`` counts what is overdue, due today or undated (the sidebar number);
	``due_that_day`` is what else falls on ``date`` (the day being picked, else today). ``away`` is
	approved time off and work restrictions on that day (``hr_enhancements.availability``), sent
	only to staff, the same gate as ``unavailable_on``. Counts only, no titles: the dialog says how
	busy someone is, never what they are busy with.
	"""
	me = frappe.session.user
	if not _is_system_user(me):
		frappe.throw(_("Only desk users can see this."), frappe.PermissionError)
	picked = frappe.parse_json(users) if isinstance(users, str) and users.strip().startswith("[") else users
	if isinstance(picked, str):
		picked = [picked]
	picked = [u for u in dict.fromkeys(picked or []) if isinstance(u, str) and u][:MAX_WORKLOAD_USERS]
	if not picked:
		return []
	today = getdate(nowdate())
	day = getdate(date) if date else today
	load = {u: {"user": u, "open": 0, "overdue": 0, "due_that_day": 0, "away": []} for u in picked}
	for row in frappe.get_all(
		"ToDo",
		filters={"status": "Open", "allocated_to": ["in", picked]},
		fields=["allocated_to", "date", "creation"],
		limit_page_length=0,
	):
		entry = load.get(row.allocated_to)
		if not entry:
			continue
		due = assignments.due_date(row.date, row.creation)
		which = assignments.bucket(due, today)
		if which in assignments.ACTIONABLE:
			entry["open"] += 1
		if which == "overdue":
			entry["overdue"] += 1
		if due == day:
			entry["due_that_day"] += 1
	if _is_staff(me):
		from erpnext_enhancements.hr_enhancements.availability import reasons_unavailable

		for user, reasons in (reasons_unavailable(picked, day) or {}).items():
			if user in load:
				load[user]["away"] = [str(r) for r in reasons]
	names = _full_names(set(picked))
	for user, entry in load.items():
		entry["name"] = names.get(user) or user
	return [load[u] for u in picked]


@frappe.whitelist(methods=["GET"])
def get_record_overview(doctype: str, name: str):
	"""Every open assignment on one record, for the form sidebar (WI-081 Phase 2): who, what was
	asked, when it is due. The sidebar's own avatars say only who."""
	if not (doctype and name and frappe.db.exists("DocType", doctype)):
		return []
	if not frappe.has_permission(doctype, "read", doc=name):
		return []
	rows = frappe.get_all(
		"ToDo",
		filters={"status": "Open", "reference_type": doctype, "reference_name": str(name)},
		fields=_todo_fields(),
		order_by="creation asc",
		limit_page_length=50,
	)
	return _present(rows, frappe.session.user, getdate(nowdate()), with_facts=False)


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
	roles = set(frappe.get_roles(user))
	can = _abilities(doc, user, bool(roles & set(LEAD_ROLES)), roles)
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

	It points where every assignment link points (``assignment_alerts.link``, v1.739.0): the Task
	or visit, else the ToDo, else -- for a team member told about a queue item they cannot open --
	My Work's Unclaimed tab on it.
	"""
	if not user or user == frappe.session.user:
		return
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	from erpnext_enhancements.task_enhancements.assignment_alerts import bell_fields

	notification = {
		"type": "Assignment",
		"subject": subject,
		"from_user": frappe.session.user,
		"email_content": body,
	}
	notification.update(bell_fields(doc, user))
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

	On a Task it also completes the Task, dated today and by you (:func:`_task_to_complete`), and
	``completed`` in the reply names it. A Task ERPNext will not complete yet refuses the Done
	before anything changes.
	"""
	doc = _load(todo, "complete")
	task = _task_to_complete(doc)
	close_todo(doc, "Closed")
	if task:
		_complete_task(task)
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
	return {"ok": True, "completed": task}


def _task_to_complete(doc):
	"""The Task that Done on ``doc`` completes, or ``None``; refuses the Done if ERPNext would.

	``None`` when the assignment is not on a Task, repeats (``assignments.done_completes_task``),
	or its Task is gone or already finished (Completed, Canceled, Invoiced, Template). When the
	Task depends on another that is not Completed or Cancelled, ERPNext's ``validate_status``
	refuses to complete it; that is checked here first, so the person is told which Task is in
	the way and nothing has changed (Nik, 2026-10-20: refuse the Done rather than close the
	assignment and leave the Task open). 59 of the 179 open Task assignments were in that state.
	"""
	if not assignments.done_completes_task(doc.reference_type, getattr(doc, "custom_repeat", None)):
		return None
	task = frappe.db.get_value("Task", doc.reference_name, ["name", "status"], as_dict=True)
	if not task or task.status in assignments.TASK_FINISHED:
		return None
	names = frappe.get_all(
		"Task Depends On",
		filters={"parent": task.name, "parenttype": "Task", "parentfield": "depends_on"},
		pluck="task",
		order_by="idx asc",
	)
	names = [n for n in names if n]
	statuses = (
		{
			r.name: r
			for r in frappe.get_all(
				"Task", filters={"name": ["in", names]}, fields=["name", "subject", "status"]
			)
		}
		if names
		else {}
	)
	blocked = assignments.blocking_dependency([(n, (statuses.get(n) or {}).get("status")) for n in names])
	if blocked:
		other, status = blocked
		subject = (statuses.get(other) or {}).get("subject")
		label = f"{other} ({assignments.truncate(subject, 60)})" if subject else other
		frappe.throw(
			_(
				"Not marked done: {0} depends on {1}, which is {2}. ERPNext completes a Task only once "
				"every Task it depends on is Completed or Cancelled."
			).format(task.name, label, _(status) if status else _("missing"))
		)
	return task.name


def _complete_task(name):
	"""Complete Task ``name`` as the session user, today.

	``completed_by`` is set here because ERPNext never sets it (its form asks; the list's bulk
	action and the Kanban leave it blank); ``completed_on`` too, rather than leaving it to
	ERPNext's ``today()`` fallback, so both say who and when in one place. Saved with
	``ignore_permissions``: being assigned the Task is what entitles someone to finish it, and an
	assignee may hold only the read share Assign To gives. Saving runs ERPNext's own completion --
	the other assignees' assignments close, a recurring Task makes its next one -- and its
	validation; anything it refuses fails the whole Done, with the ToDo still open.
	"""
	task = frappe.get_doc("Task", name)
	task.status = "Completed"
	task.completed_on = nowdate()
	task.completed_by = frappe.session.user
	task.flags.ignore_permissions = True
	task.save()


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
# Phase 3: assigning to people or a team, repeats, checklists


#: The most team members told about one new queue item (a bell each).
TEAM_NOTIFY_LIMIT = 30


def _team_members(role):
	"""Enabled desk users holding ``role``."""
	users = frappe.get_all(
		"Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent", limit_page_length=0
	)
	if not users:
		return []
	return frappe.get_all(
		"User",
		filters={"name": ["in", users], "enabled": 1, "user_type": "System User"},
		pluck="name",
		limit_page_length=0,
	)


def _teams():
	"""Every team: a Role named "... Team" that is not disabled. [{"role", "label"}]."""
	roles = frappe.get_all(
		"Role",
		filters={"name": ["like", "%" + assignments.TEAM_SUFFIX], "disabled": 0},
		pluck="name",
		order_by="name asc",
	)
	return [{"role": r, "label": assignments.team_label(r)} for r in roles if assignments.is_team_role(r)]


def _steps(checklist):
	"""Checklist lines from the dialog's text box (one per line) or a JSON list, cleaned."""
	if not checklist:
		return []
	if isinstance(checklist, str) and checklist.strip().startswith("["):
		checklist = frappe.parse_json(checklist)
	if isinstance(checklist, str):
		checklist = checklist.splitlines()
	steps = [assignments.plain_text(line).lstrip("-*• ").strip() for line in checklist or []]
	return [{"item": step[:200]} for step in steps if step][:50]


def _apply_extras(todo_names, repeat, steps):
	"""Set a repeat and a checklist on ToDos ``assign_to.add`` just made."""
	if not (repeat or steps):
		return
	for name in todo_names:
		doc = frappe.get_doc("ToDo", name)
		if repeat and frappe.db.has_column("ToDo", "custom_repeat"):
			doc.custom_repeat = repeat
		if steps and frappe.db.table_exists("Assignment Checklist Item"):
			for step in steps:
				doc.append("custom_checklist", dict(step))
		doc.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def assign(
	doctype: str,
	name: str,
	assign_to: str | None = None,
	description: str | None = None,
	date: str | None = None,
	priority: str | None = None,
	team: str | None = None,
	repeat: str | None = None,
	checklist: str | None = None,
):
	"""The Assign To dialog's action (WI-081 Phase 3): people, a team queue, or both.

	For the people picked this is Frappe's own ``assign_to.add`` -- the same permission check, share,
	follow and notification as ever -- and then the repeat and the checklist go on the ToDos it made.
	A team (a Role named "... Team") gets one queue item: a ToDo with that Role in its native
	``role`` field and nobody in ``allocated_to``, which every member sees on My Work's Unclaimed
	tab and the first to take it owns; each member gets a bell. ``name`` is one record, or a JSON
	list of them (the list view's bulk Assign To). Returns ``assign_to.get`` for one record, which
	the form's sidebar draws.
	"""
	from frappe.desk.form.assign_to import add, get

	people = frappe.parse_json(assign_to) if assign_to else []
	if isinstance(people, str):
		people = [people]
	people = [p for p in dict.fromkeys(people or []) if p]
	if team and not (assignments.is_team_role(team) and frappe.db.exists("Role", team)):
		frappe.throw(_("{0} is not a team.").format(team))
	if not people and not team:
		frappe.throw(_("Pick someone, or a team."))
	if repeat and repeat not in assignments.REPEATS:
		frappe.throw(_("Unknown repeat."))
	if priority and priority not in ("Low", "Medium", "High"):
		priority = "Medium"
	steps = _steps(checklist)
	names = frappe.parse_json(name) if isinstance(name, str) and name.strip().startswith("[") else [name]
	for docname in names:
		started = now_datetime()
		if people:
			args = {
				"assign_to": people,
				"doctype": doctype,
				"name": docname,
				"description": description,
				"priority": priority or "Medium",
			}
			if date:
				args["date"] = date
			add(args)
			made = frappe.get_all(
				"ToDo",
				filters={
					"reference_type": doctype,
					"reference_name": str(docname),
					"allocated_to": ["in", people],
					"status": "Open",
					"creation": [">=", add_to_date(started, seconds=-2)],
				},
				pluck="name",
			)
			_apply_extras(made, repeat, steps)
		if team:
			_queue(doctype, docname, team, description, date, priority, repeat, steps)
	if len(names) == 1:
		return get({"doctype": doctype, "name": names[0]})
	return None


def _queue(doctype, docname, team, description, date, priority, repeat, steps):
	"""One team queue item on one record, and a bell for each member."""
	frappe.get_doc(doctype, docname).check_permission("read")
	text = description or _("Assignment for {0} {1}").format(doctype, docname)
	values = {
		"doctype": "ToDo",
		"status": "Open",
		"role": team,
		"assigned_by": frappe.session.user,
		"reference_type": doctype,
		"reference_name": str(docname),
		"description": text,
		"priority": priority or "Medium",
	}
	if date:
		values["date"] = date
	if repeat and frappe.db.has_column("ToDo", "custom_repeat"):
		values["custom_repeat"] = repeat
	if steps and frappe.db.table_exists("Assignment Checklist Item"):
		values["custom_checklist"] = [dict(step) for step in steps]
	todo = frappe.get_doc(values).insert(ignore_permissions=True)
	title = _record_title(todo) or docname
	subject = _("New in the {0} queue: {1}").format(
		frappe.bold(escape_html(assignments.team_label(team))), frappe.bold(escape_html(title))
	)
	for member in _team_members(team)[:TEAM_NOTIFY_LIMIT]:
		_notify(member, todo, subject)
	return todo


@frappe.whitelist(methods=["POST"])
def set_repeat(todo: str, repeat: str | None = None):
	"""Make an assignment repeat when it is done, or stop it repeating (an empty ``repeat``)."""
	doc = _load(todo, "checklist")
	if repeat and repeat not in assignments.REPEATS:
		frappe.throw(_("Unknown repeat."))
	if not frappe.db.has_column("ToDo", "custom_repeat"):
		frappe.throw(_("Run bench migrate first."))
	doc.db_set("custom_repeat", repeat or None)
	return {"ok": True}


def _checklist_row(doc, row):
	for item in doc.get("custom_checklist") or []:
		if item.name == row:
			return item
	frappe.throw(_("That step is not on this to-do."))


@frappe.whitelist(methods=["POST"])
def checklist_toggle(todo: str, row: str, done: int | str = 1):
	"""Tick or untick one step; a tick records who and when."""
	doc = _load(todo, "checklist")
	item = _checklist_row(doc, row)
	ticked = 1 if str(done) in ("1", "true", "True") else 0
	item.db_set(
		{
			"done": ticked,
			"done_by": frappe.session.user if ticked else None,
			"done_on": now_datetime() if ticked else None,
		}
	)
	done_count, total = assignments.checklist_progress(doc.get("custom_checklist") or [])
	return {"ok": True, "done": done_count, "total": total}


@frappe.whitelist(methods=["POST"])
def checklist_add(todo: str, item: str):
	"""Add a step to an assignment's checklist."""
	doc = _load(todo, "checklist")
	steps = _steps([item])
	if not steps:
		frappe.throw(_("Write the step first."))
	if not frappe.db.table_exists("Assignment Checklist Item"):
		frappe.throw(_("Run bench migrate first."))
	doc.append("custom_checklist", steps[0])
	doc.save(ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def checklist_remove(todo: str, row: str):
	"""Take a step off an assignment's checklist."""
	doc = _load(todo, "checklist")
	item = _checklist_row(doc, row)
	doc.remove(item)
	doc.save(ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist(methods=["GET"])
def get_teams():
	"""The teams the Assign To dialog offers (Roles named "... Team")."""
	if not _is_system_user(frappe.session.user):
		return []
	return _teams()


def todo_queue_comment(doc, method=None):
	"""doc_events ToDo ``before_save``: say the right thing on the record's timeline for a queue item.

	Frappe's ``ToDo.validate`` words a new assignment "{assigner} assigned {assignee}: ...", and with
	nobody assigned ``get_fullname(None)`` falls back to the session user, so a team queue item read
	"Nik assigned Nik: ...". ``before_save`` runs after the controller's ``validate`` and before its
	``on_update`` writes the comment, so the text is replaced here: "Nik put this in the Sales queue:
	...", and "Ann took this from the Sales queue" when someone claims it. Never raises.
	"""
	try:
		role = doc.get("role")
		if not role:
			return
		team = assignments.team_label(role)
		if doc.is_new() and not doc.get("allocated_to"):
			ask = assignments.truncate(
				assignments.the_ask(doc.description, doc.reference_type, doc.reference_name), 140
			)
			text = _("{0} put this in the {1} queue").format(
				get_fullname(doc.get("assigned_by") or frappe.session.user), team
			)
			doc._assignment = {"text": f"{text}: {ask}" if ask else text, "comment_type": "Assigned"}
			return
		before = doc.get_doc_before_save() if not doc.is_new() else None
		if before is not None and not before.get("allocated_to") and doc.get("allocated_to"):
			doc._assignment = {
				"text": _("{0} took this from the {1} queue").format(get_fullname(doc.allocated_to), team),
				"comment_type": "Assigned",
			}
	except Exception:
		frappe.log_error(title="My Work: queue comment failed", message=frappe.get_traceback())


# ---------------------------------------------------------------------------
# Phase 3: the team load board and the calendar

#: The most rows the board shows per person; the rest are a count.
BOARD_ROWS = 25


@frappe.whitelist(methods=["GET"])
def get_team_board(team: str | None = None):
	"""Leads only (LEAD_ROLES): each person's load and open assignments, and each team's queue.

	``team`` narrows it to one team's members and queue. A person appears when they hold open
	assignments, or are a member of the team asked for (so an idle member shows as free).
	"""
	if not _is_lead():
		frappe.throw(_("The team board is for leads."), frappe.PermissionError)
	user = frappe.session.user
	today = getdate(nowdate())
	members = set(_team_members(team)) if team else None
	filters = {"status": "Open"}
	rows = frappe.get_all(
		"ToDo", filters=filters, fields=_todo_fields(), order_by="creation desc", limit_page_length=0
	)
	assigned = [r for r in rows if r.allocated_to]
	if members is not None:
		assigned = [r for r in assigned if r.allocated_to in members]
	queued = [r for r in rows if not r.allocated_to and r.get("role") and (not team or r.role == team)]
	items = _present(assigned + queued, user, today, with_facts=False)
	by_name = {i["name"]: i for i in items}
	people_users = {r.allocated_to for r in assigned} | (members or set())
	enabled = set(
		frappe.get_all(
			"User",
			filters={"name": ["in", list(people_users) or [""]], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	)
	names = _full_names(enabled)
	people = []
	for person in enabled:
		mine = [by_name[r.name] for r in assigned if r.allocated_to == person and r.name in by_name]
		mine.sort(key=lambda i: (assignments.BUCKETS.index(i["bucket"]), i["due"] or "9999-12-31"))
		counts = {b: 0 for b in assignments.BUCKETS}
		for item in mine:
			counts[item["bucket"]] += 1
		people.append(
			{
				"user": person,
				"name": names.get(person) or person,
				"counts": counts,
				"actionable": sum(counts[b] for b in assignments.ACTIONABLE),
				"items": mine[:BOARD_ROWS],
				"more": max(len(mine) - BOARD_ROWS, 0),
			}
		)
	people.sort(key=lambda p: (-p["counts"]["overdue"], -p["actionable"], p["name"]))
	queues = {}
	for r in queued:
		if r.name in by_name:
			queues.setdefault(r.role, []).append(by_name[r.name])
	return {
		"today": str(today),
		"team": team,
		"teams": _teams(),
		"people": people,
		"queues": [
			{"role": role, "label": assignments.team_label(role), "items": queue[:BOARD_ROWS]}
			for role, queue in sorted(queues.items())
		],
	}


@frappe.whitelist(methods=["GET"])
def get_calendar(start: str, end: str):
	"""Your open assignments with a real due date between ``start`` and ``end`` (a month view).

	Undated ones are left off: a calendar of creation-day stamps is the ToDo calendar this replaces.
	"""
	user = frappe.session.user
	first, last = getdate(start), getdate(end)
	if (last - first).days > 62:
		frappe.throw(_("Ask for two months at most."))
	rows = frappe.get_all(
		"ToDo",
		filters={"status": "Open", "allocated_to": user, "date": ["between", [first, last]]},
		fields=_todo_fields(),
		limit_page_length=0,
	)
	items = _present(rows, user, getdate(nowdate()), with_facts=False)
	return {"today": nowdate(), "items": [i for i in items if i["due"]]}


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
