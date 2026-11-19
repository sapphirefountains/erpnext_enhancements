# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Approval of time an employee changed or entered by hand.

A Job Interval whose times were changed by the employee it belongs to, or that was
added by hand (``api.time_kiosk.add_manual_interval`` and ``start_backdated``), is
marked ``Waiting`` in ``edit_approval`` and stays out of payroll until somebody else
approves it:

* ``payroll_export`` leaves it out of every total and lists it on its own sheet;
* ``api.time_kiosk.sync_interval_to_timesheet`` writes no Timesheet line for it, so it
  is not submitted with the day and does not reach a project's labour actuals.

Who decides is :func:`may_approve` (Nikolas, 2026-11-19): the user of the employee named
in the interval's employee's ``reports_to``, or anyone holding HR Manager or System
Manager, and never the interval's own employee or the person who made the edit. It is
its own rule, not the Time Correction Request's (``permissions.can_review``, whose
manager roles include Projects Manager): Projects Manager is not an approver of hand
edits. An employee with no ``reports_to``, or whose supervisor has no enabled user, is
decided by HR Manager or System Manager. An edit by an approver to another person's
time needs no approval; if that interval was waiting, the approver's edit settles it as
approved, since the approver set the times.

When an interval starts waiting (from blank, Approved or Sent Back), the approver is
told once for that waiting spell: a bell entry and an email from
:func:`notify_wait_started`, which ``JobInterval.on_update`` calls after the save, so a
save that is refused tells nobody. The Desk page ``time-edit-approvals`` lists what the
session user may decide (:func:`get_waiting_edits`) with Approve and Send Back.

States: blank, ``Waiting``, ``Approved``, ``Sent Back``. Blank is every interval that
was never edited this way, which includes every row that existed before the field did,
and blank counts toward payroll as it always has. ``Sent Back`` restores nothing: the
interval stays out until it is edited again (``Waiting`` once more) or approved.

Only the edit endpoints and ``guard_direct_save`` call ``record_edit``. The guard runs in
``JobInterval.validate`` and holds a save made on the Desk form, or through the REST
resource, to the same rule. A clock-out, the sweeper's auto-close and an approved Time
Correction Request are not hand edits and leave the state alone.
"""

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate, now_datetime

WAITING = "Waiting"
APPROVED = "Approved"
SENT_BACK = "Sent Back"

#: States that keep an interval out of payroll and off the Timesheet.
HELD_STATES = (WAITING, SENT_BACK)

#: ``sync_status`` of a held interval. Not ``Pending``, so nothing that picks up
#: Completed + Pending rows for a Timesheet takes it either.
HELD_SYNC_STATUS = "Held - Not Approved"

#: Roles that may decide any employee's hand edit. Deliberately not
#: ``permissions.TCR_MANAGER_ROLES``: Projects Manager decides Time Correction Requests
#: but not hand edits.
APPROVER_ROLES = frozenset({"HR Manager", "System Manager"})

#: The role told about a waiting edit when the employee has no supervisor to tell.
FALLBACK_NOTIFY_ROLE = "HR Manager"

#: The Desk page where an approver decides waiting edits.
APPROVALS_PAGE = "time-edit-approvals"


def state(doc):
	"""The interval's ``edit_approval``, blank when unset. Works on a Document or a row."""
	return (getattr(doc, "edit_approval", None) or "").strip()


def is_held(doc):
	return state(doc) in HELD_STATES


def session_employee(user=None):
	return frappe.db.get_value("Employee", {"user_id": user or frappe.session.user}, "name")


def employee_user(employee):
	"""The login of ``employee``, or None."""
	if not employee:
		return None
	return frappe.db.get_value("Employee", employee, "user_id") or None


def supervisor_user(employee):
	"""The enabled login of the employee named in ``employee``'s ``reports_to``, or None
	when there is no supervisor, the supervisor has no login, or it is disabled."""
	if not employee:
		return None
	supervisor = frappe.db.get_value("Employee", employee, "reports_to")
	if not supervisor:
		return None
	user = employee_user(supervisor)
	if not user or not frappe.db.get_value("User", user, "enabled"):
		return None
	return user


def may_approve(employee, user=None):
	"""May ``user`` approve ``employee``'s hand edits, as far as who they are goes?

	True for a holder of HR Manager or System Manager, and for the user of the employee
	named in ``employee``'s ``reports_to``. Nothing else: not the Time Correction
	managers' other roles. Whether this particular edit is theirs to decide (not their
	own time, not their own edit) is :func:`may_decide`.
	"""
	user = user or frappe.session.user
	if APPROVER_ROLES.intersection(frappe.get_roles(user)):
		return True
	return bool(employee) and supervisor_user(employee) == user


def needs_approval(employee, user=None):
	"""Whether an edit by ``user`` to ``employee``'s time waits for somebody else.

	Always for one's own time, approver or not. For another person's time, unless
	``user`` may approve that person's edits (:func:`may_approve`).
	"""
	user = user or frappe.session.user
	own = session_employee(user)
	if own and own == employee:
		return True
	return not may_approve(employee, user)


def same_minute(a, b):
	"""The kiosk's time inputs have minute resolution, so a value sent back unchanged
	arrives with its seconds dropped. Equal to the minute is unchanged."""
	if not a or not b:
		return not a and not b
	a, b = get_datetime(a), get_datetime(b)
	return a.replace(second=0, microsecond=0) == b.replace(second=0, microsecond=0)


def record_edit(doc, added=False, user=None, previous=None, waits=None):
	"""Mark ``doc`` for an edit by ``user`` of its times, or for its entry by hand
	(``added``). Call it BEFORE the new times are written: the times before the edit
	are kept from here. Returns True when the interval now waits for approval.

	The times before are kept once per waiting spell, so a second edit before anyone
	has decided still shows the change from the last approved times.

	An edit that starts a waiting spell (the interval was not ``Waiting``) sets
	``doc.flags.edit_wait_started``; ``JobInterval.on_update`` then tells the approver
	(:func:`notify_wait_started`) once the save has gone through.

	``previous`` and ``waits`` are for ``guard_direct_save``, which sees the document
	only after the new values were written: the ``(start, end)`` before the edit, and
	whether the edit waits when that is not simply ``needs_approval`` of ``doc.employee``.
	"""
	user = user or frappe.session.user
	now = now_datetime()
	if needs_approval(doc.employee, user) if waits is None else waits:
		if state(doc) != WAITING:
			doc.flags.edit_wait_started = True
		if not is_held(doc):
			before_start, before_end = previous if previous is not None else (doc.start_time, doc.end_time)
			doc.edit_previous_start = None if added else before_start
			doc.edit_previous_end = None if added else before_end
		doc.edit_approval = WAITING
		doc.edited_by = user
		doc.edited_on = now
		doc.edit_reviewed_by = None
		doc.edit_reviewed_on = None
		doc.edit_review_note = None
		return True
	if is_held(doc):
		doc.edit_approval = APPROVED
		doc.edit_reviewed_by = user
		doc.edit_reviewed_on = now
	return False


#: Written only by the edit endpoints and by approve / send back. A save made through the
#: DocType's own write permission keeps what is stored (``guard_direct_save``).
EDIT_FIELDS = (
	"edit_approval",
	"edited_by",
	"edited_on",
	"edit_previous_start",
	"edit_previous_end",
	"edit_reviewed_by",
	"edit_reviewed_on",
	"edit_review_note",
)


def _hours_moved(before, doc):
	"""Did a save change what the interval pays: whose it is, its start, its end, or its
	paused time (``payroll_export`` counts the span less ``total_paused_seconds``)?"""
	return (
		(before.get("employee") or "") != (doc.get("employee") or "")
		or not same_minute(before.get("start_time"), doc.get("start_time"))
		or not same_minute(before.get("end_time"), doc.get("end_time"))
		or abs(flt(before.get("total_paused_seconds")) - flt(doc.get("total_paused_seconds"))) >= 1
	)


def guard_direct_save(doc):
	"""``JobInterval.validate``, for a save made through the DocType's own write permission
	(the Desk form, the REST resource). This app's endpoints, the sweeper and an approved
	correction save with ``ignore_permissions`` and are left alone: the edit endpoints mark
	the interval themselves, and the others are not hand edits.

	The same two rules the endpoints hold:

	* the approval fields keep their stored values, whatever the save sent; on a new
	  interval they start blank;
	* a change to what the interval pays (:func:`_hours_moved`), or an interval entered
	  this way, is a hand edit. It waits for approval when it is the editor's own time,
	  before or after the change, or the time of someone whose edits the editor may not
	  approve (:func:`may_approve`), so a Projects Manager's change to another person's
	  interval waits too.

	Sets ``doc.flags.edit_state_changed`` when the save changed whether the interval is
	held, so ``JobInterval.on_update`` can bring its Timesheet line in step, and (through
	:func:`record_edit`) ``doc.flags.edit_wait_started`` when it started a waiting spell,
	so ``on_update`` tells the approver. Nothing is sent from here: ``validate`` runs
	before the save is known to succeed. Returns True when this save left the interval
	waiting.
	"""
	if doc.flags.ignore_permissions:
		return False
	before = None if doc.is_new() else doc.get_doc_before_save()
	if before is None and not doc.is_new():
		return False

	for field in EDIT_FIELDS:
		doc.set(field, before.get(field) if before is not None else None)
	if before is not None and not _hours_moved(before, doc):
		return False

	was_held = is_held(doc)
	employees = {doc.get("employee")} | ({before.get("employee")} if before is not None else set())
	waits = any(needs_approval(employee) for employee in employees if employee)
	waiting = record_edit(
		doc,
		added=before is None,
		previous=None if before is None else (before.get("start_time"), before.get("end_time")),
		waits=waits,
	)
	if was_held and not waiting and (doc.get("sync_status") or "") == HELD_SYNC_STATUS:
		doc.sync_status = "Pending"
	if was_held != is_held(doc):
		doc.flags.edit_state_changed = True
	return waiting


def may_decide(doc, user=None):
	"""May ``user`` approve or send back ``doc``'s edit? An approver of the employee's
	edits (:func:`may_approve`) who is neither that employee nor the person who made it."""
	user = user or frappe.session.user
	if not may_approve(doc.employee, user):
		return False
	own = session_employee(user)
	if own and own == doc.employee:
		return False
	return (getattr(doc, "edited_by", None) or "") != user


def _require_decider(doc):
	if not may_decide(doc):
		frappe.throw(
			_(
				"This edit can be decided only by the employee's supervisor, an HR Manager or a System Manager"
				" who is neither the employee nor the person who made it."
			),
			frappe.PermissionError,
		)


def _resync(doc):
	"""Rewrite the interval's Timesheet line from its current state (none while held)."""
	from erpnext_enhancements.api.time_kiosk import resync_interval_timesheet

	return resync_interval_timesheet(doc)


def _stamp_review(doc, note):
	doc.edit_reviewed_by = frappe.session.user
	doc.edit_reviewed_on = now_datetime()
	doc.edit_review_note = (note or "").strip() or None


@frappe.whitelist(methods=["POST"])
def approve_time_edit(interval, note=None):
	"""Approve a waiting (or sent back) edit: the interval counts again and its
	Timesheet line is written."""
	doc = frappe.get_doc("Job Interval", interval)
	_require_decider(doc)
	if not is_held(doc):
		frappe.throw(_("{0} is not waiting for approval.").format(doc.name))

	doc.edit_approval = APPROVED
	_stamp_review(doc, note)
	if (getattr(doc, "sync_status", None) or "") == HELD_SYNC_STATUS:
		doc.sync_status = "Pending"
	doc.save(ignore_permissions=True)
	_resync(doc)
	return {"status": doc.edit_approval, "interval": doc.name}


@frappe.whitelist(methods=["POST"])
def send_back_time_edit(interval, note=None):
	"""Send a waiting edit back. Restores nothing: the interval stays out of payroll
	and off the Timesheet, and the employee is told."""
	doc = frappe.get_doc("Job Interval", interval)
	_require_decider(doc)
	if state(doc) != WAITING:
		frappe.throw(_("{0} is not waiting for approval.").format(doc.name))

	doc.edit_approval = SENT_BACK
	_stamp_review(doc, note)
	doc.save(ignore_permissions=True)
	# Takes off any line the interval still has, and writes none while it is held.
	_resync(doc)
	notify_sent_back(doc)
	return {"status": doc.edit_approval, "interval": doc.name}


# ------------------------------------------------------------------ describing a change


def _fmt(value):
	return get_datetime(value).strftime("%Y-%m-%d %H:%M") if value else _("open")


def describe_change(row):
	"""One line for a person reading a list: what this edit changed."""
	before_start = getattr(row, "edit_previous_start", None)
	before_end = getattr(row, "edit_previous_end", None)
	if not before_start and not before_end:
		return _("Added by hand")
	parts = []
	if not same_minute(before_start, getattr(row, "start_time", None)):
		parts.append(_("Start {0} to {1}").format(_fmt(before_start), _fmt(getattr(row, "start_time", None))))
	if not same_minute(before_end, getattr(row, "end_time", None)):
		parts.append(_("End {0} to {1}").format(_fmt(before_end), _fmt(getattr(row, "end_time", None))))
	return "; ".join(parts) or _("Times changed")


# ------------------------------------------------------------------ email


def notify_sent_back(doc):
	"""Tell the employee their entry was sent back. Never raises."""
	from erpnext_enhancements import email_style

	try:
		user = frappe.db.get_value("Employee", doc.employee, "user_id")
		if not user:
			return False
		row = frappe.db.get_value("User", user, ["email", "enabled"], as_dict=True)
		if not row or not row.enabled or not row.email:
			return False
		body = email_style.callout(
			_("Your time entry {0} was sent back. It is not counted toward payroll.").format(doc.name), "warning"
		) + email_style.kv([
			(_("Entry"), doc.name),
			(_("Start"), _fmt(doc.start_time)),
			(_("End"), _fmt(doc.end_time)),
			(_("What changed"), describe_change(doc)),
		])
		if doc.edit_review_note:
			body += email_style.p(_("Note from the reviewer:")) + email_style.prose(doc.edit_review_note)
		body += email_style.note(
			_("Edit the entry again from My Day in the Time Kiosk, or file a correction request.")
		)
		frappe.sendmail(
			recipients=[row.email],
			subject=_("Time entry {0}: sent back").format(doc.name),
			message=email_style.wrap(body, title=_("Time entry sent back"), eyebrow=_("Time Kiosk")),
			reference_doctype="Job Interval",
			reference_name=doc.name,
		)
		return True
	except Exception:
		frappe.log_error(title=f"Time edit email failed: {getattr(doc, 'name', '')}")
		return False


# ------------------------------------------------------------------ telling the approver


def approvers_to_notify(doc):
	"""Who is told that ``doc`` started waiting: the employee's supervisor (``reports_to``)
	when that is an enabled user, otherwise every enabled holder of HR Manager. Never the
	interval's employee or the person who made the edit."""
	from frappe.utils.user import get_users_with_role

	exclude = {u for u in (employee_user(doc.employee), getattr(doc, "edited_by", None)) if u}
	supervisor = supervisor_user(doc.employee)
	if supervisor and supervisor not in exclude:
		return [supervisor]
	return sorted({u for u in get_users_with_role(FALLBACK_NOTIFY_ROLE) or [] if u and u not in exclude})


def notify_wait_started(doc):
	"""Tell the approver that ``doc``'s hand edit is waiting, when this save started a
	waiting spell (``doc.flags.edit_wait_started``, set by :func:`record_edit`). Once per
	spell: the flag is cleared here, and a second edit before anyone decides sets none.

	``JobInterval.on_update`` calls it, which is after the save on every path that can
	start a wait: the kiosk's ``update_interval_times``, ``add_manual_interval`` and
	``start_backdated`` (their ``save`` / ``insert`` run ``on_update``) and a save on the
	Desk form. A save that is refused never reaches it.

	Never raises: a failure is logged and the edit stands. Returns the users told.
	"""
	flags = getattr(doc, "flags", None)
	if not (flags and flags.get("edit_wait_started")):
		return []
	flags.edit_wait_started = False
	if state(doc) != WAITING:
		return []
	try:
		recipients = approvers_to_notify(doc)
	except Exception:
		frappe.log_error(title=f"Time edit notice failed: {getattr(doc, 'name', '')}")
		return []
	told = []
	for user in recipients:
		try:
			if _send_wait_notice(doc, user):
				told.append(user)
		except Exception:
			frappe.log_error(title=f"Time edit notice failed: {getattr(doc, 'name', '')}")
	return told


def _send_wait_notice(doc, user):
	"""One approver's bell entry and email. Who, which day, what changed and why, and a
	link to the approvals page. No pay figures."""
	from erpnext_enhancements import email_style

	row = frappe.db.get_value("User", user, ["email", "enabled"], as_dict=True)
	if not row or not row.enabled:
		return False

	name = frappe.db.get_value("Employee", doc.employee, "employee_name") or doc.employee
	day = str(getdate(doc.start_time)) if getattr(doc, "start_time", None) else ""
	project = getattr(doc, "project", None) or ""
	project_name = (frappe.db.get_value("Project", project, "project_name") if project else None) or project
	editor = getattr(doc, "edited_by", None) or ""
	editor_name = (frappe.db.get_value("User", editor, "full_name") if editor else None) or editor
	change = describe_change(doc)
	reason = getattr(doc, "time_edit_reason", None) or getattr(doc, "manual_start_reason", None) or ""
	subject = _("Time entry waiting for your approval: {0}, {1}").format(name, day)

	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": user,
			"from_user": editor or None,
			# "Alert" is never emailed by Frappe, so the email below is the only one.
			"type": "Alert",
			"document_type": "Job Interval",
			"document_name": doc.name,
			"link": f"/desk/{APPROVALS_PAGE}",
			"subject": frappe.utils.escape_html(subject),
			"email_content": frappe.utils.escape_html(change),
		}
	).insert(ignore_permissions=True)

	if not row.email:
		return True
	body = email_style.p(
		_(
			"A time entry for {0} is waiting for your approval."
			" It is not counted toward payroll until it is approved."
		).format(name)
	) + email_style.kv([
		(_("Employee"), name),
		(_("Day"), day),
		(_("Project"), project_name),
		(_("What changed"), change),
		(_("Start"), _fmt(getattr(doc, "start_time", None))),
		(_("End"), _fmt(getattr(doc, "end_time", None))),
		(_("Reason given"), reason or _("None given")),
		(_("Edited by"), editor_name),
		(_("Entry"), doc.name),
	])
	body += email_style.button(frappe.utils.get_url(f"/desk/{APPROVALS_PAGE}"), _("Open Time Edit Approvals"))
	frappe.sendmail(
		recipients=[row.email],
		subject=subject,
		message=email_style.wrap(body, title=_("Time entry waiting for approval"), eyebrow=_("Time Kiosk")),
		reference_doctype="Job Interval",
		reference_name=doc.name,
	)
	return True


# ------------------------------------------------------------------ the approvals page

#: What the page reads of a waiting interval. Nothing from the pay block.
_PAGE_FIELDS = [
	"name",
	"employee",
	"project",
	"start_time",
	"end_time",
	"edit_previous_start",
	"edit_previous_end",
	"edited_by",
	"edited_on",
	"time_edit_reason",
	"manual_start_reason",
]


def _text(value):
	return str(get_datetime(value)) if value else None


def _labels(doctype, names, field):
	names = sorted({n for n in names if n})
	if not names:
		return {}
	rows = frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", field])
	return {r.name: r.get(field) or r.name for r in rows}


@frappe.whitelist(methods=["GET"])
def get_waiting_edits():
	"""The entries waiting for approval that the session user may decide, oldest edit
	first, for the Time Edit Approvals page.

	The server picks the rows: a holder of HR Manager or System Manager reads every
	waiting entry, anyone else only their direct reports' (``reports_to``), and each row
	is then held to :func:`may_decide`, so the caller's own time and the caller's own
	edits are left out. A supervisor needs no read permission on Job Interval for this.
	Returns only what the page shows.
	"""
	user = frappe.session.user
	filters = {"edit_approval": WAITING}
	everyone = bool(APPROVER_ROLES.intersection(frappe.get_roles(user)))
	if not everyone:
		own = session_employee(user)
		reports = frappe.get_all("Employee", filters={"reports_to": own}, pluck="name") if own else []
		if not reports:
			return {"entries": [], "scope": "team"}
		filters["employee"] = ["in", reports]

	rows = frappe.get_all(
		"Job Interval", filters=filters, fields=_PAGE_FIELDS, order_by="edited_on asc", limit_page_length=500
	)
	rows = [r for r in rows if may_decide(r, user)]

	employees = _labels("Employee", [r.employee for r in rows], "employee_name")
	projects = _labels("Project", [r.project for r in rows], "project_name")
	editors = _labels("User", [r.edited_by for r in rows], "full_name")
	entries = []
	for r in rows:
		entries.append({
			"interval": r.name,
			"employee_name": employees.get(r.employee) or r.employee,
			"day": str(getdate(r.start_time)) if r.start_time else "",
			"project": r.project or "",
			"project_name": projects.get(r.project) or r.project or "",
			"added": not (r.edit_previous_start or r.edit_previous_end),
			"before_start": _text(r.edit_previous_start),
			"before_end": _text(r.edit_previous_end),
			"start": _text(r.start_time),
			"end": _text(r.end_time),
			"change": describe_change(r),
			"reason": r.time_edit_reason or r.manual_start_reason or "",
			"edited_by": editors.get(r.edited_by) or r.edited_by or "",
			"edited_on": _text(r.edited_on),
		})
	return {"entries": entries, "scope": "all" if everyone else "team"}
