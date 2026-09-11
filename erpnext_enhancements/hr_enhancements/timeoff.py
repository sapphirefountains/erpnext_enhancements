# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Time off: the transitions, and who is allowed to make them.

Four moves and nothing else — **submit**, **approve**, **decline**, **cancel**.
The state machine is small on purpose; a request that can be in nine states is a
request nobody can answer at a glance.

Authority is deliberately *not* the position ladder. Time off is "who plans your
week", which is exactly what ``Employee.reports_to`` means and exactly what the
Position tier does not — a Senior Technician outranks a Junior on competence and
has no standing at all over their Thursday. So the approver is the reporting
manager, frozen on the request, plus HR Manager and System Manager as the people
who cover when somebody's manager is away.

Everything here is scoped by ``hr_enhancements/permissions.py`` on the same three
arms the rest of the module uses, except that the tier arm is absent for the same
reason.
"""

import frappe
from frappe import _
from frappe.utils import get_url, getdate, now_datetime

from erpnext_enhancements.hr_enhancements.doctype.time_off_request.time_off_request import (
	APPROVED,
	CANCELED,
	DECLINED,
	DRAFT,
	REQUESTED,
)

DOCTYPE = "Time Off Request"

#: Who may decide somebody else's request without being their named approver.
#: Not the position ladder -- see the module docstring.
DECIDER_ROLES = {"HR Manager", "System Manager"}


def _me():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


def _may_decide(doc, user):
	if doc.approver_user and doc.approver_user == user:
		return True
	return bool(DECIDER_ROLES & set(frappe.get_roles(user)))


@frappe.whitelist()
def submit_request(request):
	"""Send it to the approver. The requester's own action."""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, request)
	if doc.user != me and not (DECIDER_ROLES & set(frappe.get_roles(me))):
		frappe.throw(_("That is not your request."), frappe.PermissionError)
	if doc.status != DRAFT:
		frappe.throw(_("That request has already been sent."))
	if not doc.approver_user:
		# Unroutable, and worth saying so plainly rather than leaving it to sit.
		# Same failure mode the training sign-off was built to avoid: a request
		# nobody owns is one nobody finds out about until it is too late.
		frappe.throw(
			_("There is nobody to send this to: {0} has no manager on their Employee "
			  "record. Ask HR to set one.").format(doc.employee_name or doc.employee)
		)

	doc.db_set("status", REQUESTED)
	notified = _notify(
		doc.approver_user,
		_("Time off request: {0}").format(doc.employee_name or doc.employee),
		_body(doc, _("has asked for time off")),
	)
	return {"status": doc.status, "notified": notified}


@frappe.whitelist()
def decide(request, decision, note=None):
	"""Approve or decline. The approver's action, or a stand-in's."""
	me = _me()
	if decision not in (APPROVED, DECLINED):
		frappe.throw(_("A decision is either {0} or {1}.").format(APPROVED, DECLINED))

	doc = frappe.get_doc(DOCTYPE, request)
	if doc.user == me:
		# First and unconditional, whatever roles they hold. Same rule as the
		# training sign-off, and for the same reason: it is the line an auditor
		# reads out.
		frappe.throw(_("You cannot decide your own time off."), frappe.PermissionError)
	if not _may_decide(doc, me):
		frappe.throw(
			_("Only {0} or HR can decide this.").format(doc.approver_user or _("the approver")),
			frappe.PermissionError,
		)
	if doc.status != REQUESTED:
		frappe.throw(_("That request is {0}, so there is nothing to decide.").format(doc.status))

	doc.status = decision
	doc.decision_note = (note or "").strip() or None
	doc.decided_on = now_datetime()
	# The controller refuses a status change that did not come through here.
	doc.flags.timeoff_transition = True
	doc.save(ignore_permissions=True)

	notified = _notify(
		doc.user,
		_("Your time off: {0}").format(decision),
		_body(doc, _("was {0}").format(decision.lower()), note=doc.decision_note),
	)
	return {"status": doc.status, "notified": notified}


@frappe.whitelist()
def cancel_request(request, note=None):
	"""Withdraw it. The requester's, up until it has been taken.

	An approved request can still be cancelled — plans change, and a system that
	makes somebody keep a day off they no longer want is a system people route
	around. The approver is told, because they planned around it.
	"""
	me = _me()
	doc = frappe.get_doc(DOCTYPE, request)
	if doc.user != me and not _may_decide(doc, me):
		frappe.throw(_("That is not your request."), frappe.PermissionError)
	if doc.status in (CANCELED, DECLINED):
		frappe.throw(_("That request is already {0}.").format(doc.status))

	was = doc.status
	doc.status = CANCELED
	doc.decision_note = (note or "").strip() or doc.decision_note
	doc.flags.timeoff_transition = True
	doc.save(ignore_permissions=True)

	notified = False
	if was in (REQUESTED, APPROVED) and doc.approver_user and doc.approver_user != me:
		notified = _notify(
			doc.approver_user,
			_("Time off canceled: {0}").format(doc.employee_name or doc.employee),
			_body(doc, _("canceled their time off")),
		)
	return {"status": doc.status, "notified": notified}


@frappe.whitelist()
def who_is_out(from_date=None, to_date=None):
	"""Approved time off in a window, for dispatch. Staff-wide and deliberately thin.

	Names and dates only — no type and no reason. "Who is out on Thursday" is a
	scheduling question; *why* somebody is off is between them, their manager and
	HR, and a sick day is not something to publish to the crew.
	"""
	me = _me()
	if not frappe.db.exists("Employee", {"user_id": me, "status": "Active"}):
		# Staff only, by EMPLOYMENT rather than by role -- a customer contact holds
		# a login and could otherwise enumerate every staff member's absences, which
		# is both none of their business and a rough map of the company's week.
		frappe.throw(_("Only staff can see who is out."), frappe.PermissionError)
	filters = {"status": APPROVED}
	if to_date:
		filters["from_date"] = ["<=", getdate(to_date)]
	if from_date:
		filters["to_date"] = [">=", getdate(from_date)]
	return {
		"days": frappe.get_all(
			DOCTYPE,
			filters=filters,
			fields=["employee_name", "from_date", "to_date", "half_day"],
			order_by="from_date asc",
		)
	}


def _body(doc, what, note=None):
	when = (
		f"{getdate(doc.from_date):%d %b}"
		if str(doc.from_date) == str(doc.to_date)
		else f"{getdate(doc.from_date):%d %b} – {getdate(doc.to_date):%d %b}"
	)
	lines = [
		f"<p><b>{frappe.utils.escape_html(doc.employee_name or doc.employee)}</b> "
		f"{frappe.utils.escape_html(what)}: {frappe.utils.escape_html(when)} "
		f"({frappe.utils.escape_html(str(doc.time_off_type))}).</p>"
	]
	if doc.reason:
		lines.append(f"<p>{frappe.utils.escape_html(doc.reason)}</p>")
	if note:
		lines.append(f"<p>{frappe.utils.escape_html(note)}</p>")
	lines.append(f'<p><a href="{get_url("/app/time-off-request/" + doc.name)}">Open it</a></p>')
	return "".join(lines)


def _notify(user, subject, body_html):
	"""Best-effort, and reuses training's mailer rather than growing a second one.

	It already resolves an Employee's preferred address the way travel does,
	already honours the notification switch, and already writes a Notification Log
	entry beside the email. Never raises: a decision that recorded but failed to
	email is a decision that recorded.
	"""
	try:
		from erpnext_enhancements.training import notifications

		if not notifications._enabled():
			return False
		recipient = notifications._recipient(user)
		return bool(recipient) and notifications._send(recipient, subject, body_html)
	except Exception:
		frappe.log_error(
			f"Could not send the time-off notification to {user}\n{frappe.get_traceback()}",
			"HR time off",
		)
		return False
