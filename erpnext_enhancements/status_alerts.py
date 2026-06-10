"""Status-change alerts (SMS + Notification Log) for the PRO-0204 hand-off process.

Three alert flows, configured in **ERPNext Enhancements Settings → Status
Change SMS Alerts** (``status_alert_recipients`` child table of Employees;
numbers resolve from ``Employee.cell_number`` at send time; texts go out
through the Triton gateway via
:func:`erpnext_enhancements.api.telephony.send_system_sms`):

* :func:`notify_closed_won` — Opportunity ``on_update``. Fires once, on the
  *transition* into status "Closed Won" (PRO-0204 Step 1: "system sends
  auto-alerts"); texts each opted-in recipient a deep link so the Project can
  be created and QuickBooks set up without waiting on email.
* :func:`nag_unconverted_opportunities` — daily scheduler. Re-nags while a won
  opportunity still has no Project (``custom_created_project`` empty) after
  the configured number of hours — the Step 1 → Step 2 gap where leads have
  historically fallen through.
* :func:`stamp_payment_received_date` / :func:`notify_payment_received` —
  Project ``before_save`` / ``on_update``. When the *Payment Received* box is
  ticked (PRO-0204 Step 5), posts a timeline comment and alerts the Project
  Manager (``custom_project_owner``) and the Account Executive (owner of the
  source Opportunity) that the project is financially cleared to proceed —
  per the process doc those two roles specifically, not a broadcast list.

Delivery runs in background jobs (``enqueue_after_commit``) so a slow or
failing SMS gateway can never block or roll back a save; per-recipient
failures are logged and skipped so one bad number doesn't starve the rest.
Every SMS recipient with a linked User also gets a Notification Log entry,
giving an in-app audit trail of what was sent.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_url, get_url_to_form, getdate, now_datetime, today

from erpnext_enhancements.feature_flags import process_automation_enabled


def _in_maintenance_context():
	"""True while migrating/installing/patching/importing — never alert from those."""
	flags = frappe.flags
	return bool(flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import)


def _alert_recipients(flag):
	"""Employees from the settings child table with ``flag`` ticked.

	Returns dicts of ``employee_name`` / ``cell_number`` / ``user_id`` (either
	contact handle may be empty — delivery skips what's missing).
	"""
	settings = frappe.get_single("ERPNext Enhancements Settings")
	employee_ids = [row.employee for row in (settings.status_alert_recipients or []) if row.employee and cint(row.get(flag))]
	if not employee_ids:
		return []
	return frappe.get_all(
		"Employee",
		filters={"name": ("in", employee_ids)},
		fields=["name", "employee_name", "cell_number", "user_id"],
	)


def _deliver(recipients, message, subject, reference_doctype, reference_docname):
	"""Send ``message`` to every recipient: SMS to ``cell_number``, Notification Log to ``user_id``."""
	seen = set()
	for recipient in recipients:
		key = (recipient.get("cell_number"), recipient.get("user_id"))
		if key in seen:
			continue
		seen.add(key)

		if recipient.get("cell_number"):
			try:
				# Lazy import: api.telephony pulls in twilio at module top, which is
				# not a declared dependency — it must never be on the doc-save path
				# (these hooks load on every Opportunity/Project save). A missing
				# package costs the SMS (logged below), never the save.
				from erpnext_enhancements.api.telephony import send_system_sms

				send_system_sms(recipient["cell_number"], message)
			except Exception:
				frappe.log_error(
					f"Status alert SMS to {recipient.get('employee_name') or recipient['cell_number']} failed:\n"
					f"{frappe.get_traceback()}",
					"Status Alert SMS Error",
				)

		if recipient.get("user_id"):
			try:
				frappe.get_doc(
					{
						"doctype": "Notification Log",
						"subject": subject,
						"email_content": message,
						"for_user": recipient["user_id"],
						"type": "Alert",
						"document_type": reference_doctype,
						"document_name": reference_docname,
					}
				).insert(ignore_permissions=True)
			except Exception:
				frappe.log_error(
					f"Status alert Notification Log for {recipient['user_id']} failed:\n{frappe.get_traceback()}",
					"Status Alert Error",
				)


# ---------------------------------------------------------------------------
# Closed Won (PRO-0204 Step 1)
# ---------------------------------------------------------------------------


def notify_closed_won(doc, method=None):
	"""Opportunity ``on_update`` — queue team SMS on the transition into Closed Won.

	Transition-guarded via ``get_doc_before_save()``: re-saving an already-won
	opportunity never re-sends. A document *created* directly in Closed Won
	(no before-doc) does alert.
	"""
	if doc.status != "Closed Won" or _in_maintenance_context():
		return
	if not process_automation_enabled():
		return
	before = doc.get_doc_before_save()
	if before and before.status == "Closed Won":
		return
	frappe.enqueue(
		"erpnext_enhancements.status_alerts.deliver_closed_won_alerts",
		opportunity=doc.name,
		queue="short",
		enqueue_after_commit=True,
	)


def deliver_closed_won_alerts(opportunity):
	"""Background job: text the opted-in recipients about a won opportunity."""
	recipients = _alert_recipients("closed_won")
	if not recipients:
		return
	opp = frappe.db.get_value(
		"Opportunity",
		opportunity,
		["name", "customer_name", "party_name", "custom_opportunity_summary"],
		as_dict=True,
	)
	if not opp:
		return

	customer = opp.customer_name or opp.party_name or opp.name
	parts = [f"WON: {customer}"]
	if opp.custom_opportunity_summary:
		parts.append(opp.custom_opportunity_summary)
	message = " - ".join(parts) + f"\nReview & convert: {get_url_to_form('Opportunity', opp.name)}"

	_deliver(
		recipients,
		message,
		subject=_("Opportunity {0} marked Closed Won").format(opp.name),
		reference_doctype="Opportunity",
		reference_docname=opp.name,
	)


def nag_unconverted_opportunities():
	"""Daily scheduler: remind the team about won opportunities with no Project yet.

	Threshold comes from ``unconverted_nag_hours`` (unset → 24; explicit 0
	disables). ``custom_date_closed_won`` is a Date, so the comparison has
	day granularity — with the 24h default an opportunity won today is never
	nagged, one won yesterday or earlier is.
	"""
	if not process_automation_enabled():
		return
	settings = frappe.get_single("ERPNext Enhancements Settings")
	raw_hours = settings.get("unconverted_nag_hours")
	hours = 24 if raw_hours in (None, "") else cint(raw_hours)
	if hours <= 0:
		return

	recipients = _alert_recipients("unconverted_reminder")
	if not recipients:
		return

	cutoff = getdate(add_to_date(now_datetime(), hours=-hours))
	opportunities = frappe.get_all(
		"Opportunity",
		filters={
			"status": "Closed Won",
			"custom_created_project": ("is", "not set"),
			"custom_date_closed_won": ("<=", cutoff),
		},
		fields=["name", "customer_name", "party_name"],
		order_by="custom_date_closed_won asc",
	)
	if not opportunities:
		return

	labels = [opp.customer_name or opp.party_name or opp.name for opp in opportunities]
	shown = ", ".join(labels[:3])
	if len(labels) > 3:
		shown += f" +{len(labels) - 3} more"
	noun = "opportunity" if len(labels) == 1 else "opportunities"
	message = (
		f"{len(labels)} won {noun} still waiting on a project: {shown}\n"
		f"{get_url('/app/opportunity?status=Closed%20Won')}"
	)

	_deliver(
		recipients,
		message,
		subject=_("{0} won {1} still awaiting project creation").format(len(labels), noun),
		reference_doctype="Opportunity",
		reference_docname=opportunities[0].name,
	)


# ---------------------------------------------------------------------------
# Payment Received (PRO-0204 Step 5)
# ---------------------------------------------------------------------------


def stamp_payment_received_date(doc, method=None):
	"""Project ``before_save`` — default the received-on date when the box is ticked."""
	if cint(doc.custom_payment_received) and not doc.custom_payment_received_on:
		doc.custom_payment_received_on = today()


def notify_payment_received(doc, method=None):
	"""Project ``on_update`` — comment + alert PM and AE when Payment Received is ticked.

	Transition-guarded like :func:`notify_closed_won`; unticking and re-ticking
	deliberately re-alerts (it means a recorded payment was corrected and then
	confirmed again).
	"""
	if not cint(doc.custom_payment_received) or _in_maintenance_context():
		return
	if not process_automation_enabled():
		return
	before = doc.get_doc_before_save()
	if before and cint(before.custom_payment_received):
		return

	via = f" via {doc.custom_payment_method}" if doc.custom_payment_method else ""
	received_on = frappe.utils.formatdate(doc.custom_payment_received_on or today())
	doc.add_comment(
		"Comment",
		_("Payment received{0} on {1} — project is financially cleared to proceed (PRO-0204 Step 5).").format(
			via, received_on
		),
	)
	frappe.enqueue(
		"erpnext_enhancements.status_alerts.deliver_payment_received_alerts",
		project=doc.name,
		queue="short",
		enqueue_after_commit=True,
	)


def deliver_payment_received_alerts(project):
	"""Background job: text/notify the project's PM and AE that payment landed.

	PM = ``custom_project_owner`` (Employee). AE = ``opportunity_owner`` (User)
	of the source Opportunity — found via the project's ``custom_opportunity``
	link, falling back to the Opportunity whose ``custom_created_project``
	points here. An AE User without an Employee record still gets the
	Notification Log (just no SMS).
	"""
	proj = frappe.db.get_value(
		"Project",
		project,
		["name", "project_name", "custom_project_owner", "custom_opportunity", "custom_payment_method"],
		as_dict=True,
	)
	if not proj:
		return

	recipients = []
	if proj.custom_project_owner:
		pm = frappe.db.get_value(
			"Employee",
			proj.custom_project_owner,
			["name", "employee_name", "cell_number", "user_id"],
			as_dict=True,
		)
		if pm:
			recipients.append(pm)

	opportunity = proj.custom_opportunity or frappe.db.get_value(
		"Opportunity", {"custom_created_project": proj.name}, "name"
	)
	ae_user = (
		frappe.db.get_value("Opportunity", opportunity, "opportunity_owner") if opportunity else None
	)
	if ae_user:
		ae = frappe.db.get_value(
			"Employee",
			{"user_id": ae_user, "status": "Active"},
			["name", "employee_name", "cell_number", "user_id"],
			as_dict=True,
		)
		recipients.append(ae or frappe._dict(employee_name=ae_user, cell_number=None, user_id=ae_user))

	if not recipients:
		return

	via = f" via {proj.custom_payment_method}" if proj.custom_payment_method else ""
	message = (
		f"Payment received{via} for {proj.project_name or proj.name} - cleared to proceed.\n"
		f"{get_url_to_form('Project', proj.name)}"
	)

	_deliver(
		recipients,
		message,
		subject=_("Payment received for {0}").format(proj.project_name or proj.name),
		reference_doctype="Project",
		reference_docname=proj.name,
	)
