# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Somebody went to a site alone. Did they come back?

Three stages, fifteen minutes apart, and the shape of the escalation is the whole
feature:

1. **chase them** — most overdue sessions are somebody who finished and forgot,
   and a nudge to their own phone clears the great majority without troubling
   anybody;
2. **tell their supervisor** — a person who has not answered a nudge is a person
   somebody should ring;
3. **tell the CEO** — sixteen people, so the top of the escalation is one phone
   call away from everybody, and a chain that ends in a mailbox nobody reads is
   not an escalation.

**It escalates once per stage and then stops.** A sweep that re-sends every ten
minutes trains people to filter it, and the filtered version of this alert is
worth nothing at all.

**It never closes a session by itself.** An overdue session stays overdue until a
human closes it, because "the sweep decided they were probably fine" is precisely
the judgement nobody should be making at 7pm.
"""

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, get_url_to_form, now_datetime

DOCTYPE = "Lone Work Session"

OPEN = "Open"
CLOSED = "Closed"
OVERDUE = "Overdue"
ESCALATED = "Escalated"

#: Minutes past `expected_out_by` at which each stage fires.
STAGES = (
	(0, "worker"),
	(15, "supervisor"),
	(30, "executive"),
)


def _me():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


@frappe.whitelist()
def start_session(where, expected_out_by, customer=None, maintenance_record=None, notes=None):
	"""Declare that you are going somewhere alone. Four fields, on purpose.

	A check-in that takes longer than this is one that gets skipped on the day it
	would have mattered.
	"""
	me = _me()
	employee = frappe.db.get_value("Employee", {"user_id": me, "status": "Active"}, "name")
	if not employee:
		frappe.throw(_("Only staff can start a lone-work session."), frappe.PermissionError)

	existing = frappe.db.exists(DOCTYPE, {"employee": employee, "status": ["in", (OPEN, OVERDUE, ESCALATED)]})
	if existing:
		frappe.throw(
			_("You already have an open session ({0}). Close it before starting another.").format(existing)
		)

	doc = frappe.new_doc(DOCTYPE)
	doc.employee = employee
	doc.where = where
	doc.expected_out_by = expected_out_by
	doc.customer = customer
	doc.maintenance_record = maintenance_record
	doc.notes = notes
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "expected_out_by": str(doc.expected_out_by)}


@frappe.whitelist()
def check_out(session=None, notes=None):
	"""I am out. Closes your own open session, or a named one.

	`session` is optional because the common case is one person with one open
	session pressing one button, and asking them which one is asking a question
	with only one answer.
	"""
	me = _me()
	employee = frappe.db.get_value("Employee", {"user_id": me, "status": "Active"}, "name")
	if not session:
		session = frappe.db.get_value(
			DOCTYPE, {"employee": employee, "status": ["in", (OPEN, OVERDUE, ESCALATED)]}, "name"
		)
	if not session:
		frappe.throw(_("You have no open session."))

	doc = frappe.get_doc(DOCTYPE, session)
	if doc.employee != employee and not (
		{"HR Manager", "System Manager", "Maintenance Manager"} & set(frappe.get_roles(me))
	):
		frappe.throw(_("That is not your session."), frappe.PermissionError)
	if doc.status == CLOSED:
		frappe.throw(_("That session is already closed."))

	doc.status = CLOSED
	doc.closed_on = now_datetime()
	if notes:
		doc.notes = f"{doc.notes}\n{notes}" if doc.notes else notes
	doc.save(ignore_permissions=True)

	# If anybody was told, tell them it ended. An escalation with no resolution is
	# how the next one gets ignored.
	if cint(doc.escalation_stage):
		_notify_resolved(doc)
	return {"status": doc.status}


@frappe.whitelist()
def extend(session, expected_out_by):
	"""Still working. Moves the clock and resets the escalation.

	The stage reset matters: somebody who was chased, answered and extended should
	not be escalated to their supervisor five minutes later on the strength of the
	old stage counter.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, session)
	employee = frappe.db.get_value("Employee", {"user_id": me, "status": "Active"}, "name")
	if doc.employee != employee and not (
		{"HR Manager", "System Manager", "Maintenance Manager"} & set(frappe.get_roles(me))
	):
		frappe.throw(_("That is not your session."), frappe.PermissionError)
	if doc.status == CLOSED:
		frappe.throw(_("That session is closed."))

	doc.expected_out_by = expected_out_by
	doc.status = OPEN
	doc.escalation_stage = 0
	doc.escalated_on = None
	doc.save(ignore_permissions=True)
	return {"status": doc.status, "expected_out_by": str(doc.expected_out_by)}


# ------------------------------------------------------------------- the sweep


def sweep_overdue_sessions():
	"""Every ten minutes. Escalates one stage at a time and never closes anything.

	Wrapped so a failure cannot take the scheduler down, and logged so a silent
	failure is still a visible one — a safety sweep that dies quietly is worse than
	no sweep, because somebody is relying on it.
	"""
	try:
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			return 0
		if not frappe.db.exists("DocType", DOCTYPE):
			return 0
		return _sweep()
	except Exception:
		frappe.log_error(
			f"Lone-work sweep failed\n{frappe.get_traceback()}", "Lone work"
		)
		return 0


def _sweep():
	now = now_datetime()
	moved = 0
	for row in frappe.get_all(
		DOCTYPE,
		filters={"status": ["in", (OPEN, OVERDUE, ESCALATED)]},
		fields=["name", "expected_out_by", "escalation_stage"],
	):
		if not row.expected_out_by or get_datetime(row.expected_out_by) > now:
			continue
		late_minutes = (now - get_datetime(row.expected_out_by)).total_seconds() / 60.0

		# The highest stage whose threshold has passed. One notification per stage,
		# ever -- a sweep that re-sends every ten minutes trains people to filter it.
		due_stage = 0
		for index, (after, _who) in enumerate(STAGES, start=1):
			if late_minutes >= after:
				due_stage = index
		if due_stage <= cint(row.escalation_stage):
			continue

		doc = frappe.get_doc(DOCTYPE, row.name)
		_escalate(doc, due_stage)
		moved += 1
	return moved


def _escalate(doc, stage):
	audience = STAGES[stage - 1][1]
	recipients = _recipients_for(doc, audience)
	subject = _("Overdue on site: {0}").format(doc.employee_name or doc.employee)
	body = _(
		"<p><b>{0}</b> said they would be out of <b>{1}</b> by <b>{2}</b> and has not checked "
		"out.</p>"
	).format(doc.employee_name or doc.employee, doc.where or "", doc.expected_out_by)
	if audience == "worker":
		subject = _("Are you out?")
		body = _(
			"<p>You said you would be out of <b>{0}</b> by <b>{1}</b>. Check out, or extend if "
			"you are still working.</p>"
		).format(doc.where or "", doc.expected_out_by)
	elif audience == "executive":
		body += _("<p>Their supervisor was told fifteen minutes ago and they are still not out.</p>")
	body += _("<p><a href='{0}'>Open the session</a></p>").format(get_url_to_form(DOCTYPE, doc.name))

	_send(recipients, subject, body)

	doc.db_set("escalation_stage", stage, update_modified=False)
	doc.db_set("escalated_on", now_datetime(), update_modified=False)
	# OVERDUE at stage one, ESCALATED once somebody other than the worker is
	# involved -- the distinction a board wants to draw in colour.
	doc.db_set("status", OVERDUE if stage == 1 else ESCALATED, update_modified=False)


def _recipients_for(doc, audience):
	if audience == "worker":
		return [doc.user] if doc.user else []
	if audience == "supervisor":
		manager = frappe.db.get_value("Employee", doc.employee, "reports_to")
		user = frappe.db.get_value("Employee", manager, "user_id") if manager else None
		# No manager on the record is not a reason to send nothing -- it is a reason
		# to go straight up. Silence is the one outcome this must never produce.
		return [user] if user else _executives()
	return _executives()


def _executives():
	users = set()
	for role in ("System Manager", "HR Manager"):
		users.update(
			frappe.get_all("Has Role", filters={"parenttype": "User", "role": role}, pluck="parent")
			or []
		)
	return [u for u in users if u and u not in ("Administrator", "Guest")]


def _send(recipients, subject, body):
	try:
		from erpnext_enhancements.training import notifications

		sent = False
		for user in recipients or []:
			recipient = notifications._recipient(user)
			if recipient:
				sent = notifications._send(recipient, subject, body) or sent
		return sent
	except Exception:
		frappe.log_error(f"Could not send lone-work alert\n{frappe.get_traceback()}", "Lone work")
		return False


def _notify_resolved(doc):
	"""Tell whoever was alarmed that it ended. An escalation with no resolution is
	how the next one gets ignored."""
	stage = cint(doc.escalation_stage)
	audience = STAGES[min(stage, len(STAGES)) - 1][1] if stage else "worker"
	recipients = set(_recipients_for(doc, audience))
	if stage >= 2:
		recipients.update(_recipients_for(doc, "supervisor"))
	if stage >= 3:
		recipients.update(_executives())
	recipients.discard(doc.user)
	_send(
		sorted(recipients),
		_("Out safe: {0}").format(doc.employee_name or doc.employee),
		_("<p><b>{0}</b> has checked out of <b>{1}</b>.</p>").format(
			doc.employee_name or doc.employee, doc.where or ""
		),
	)
