"""Feature flags for staged rollout (read from ERPNext Enhancements Settings).

One master switch, ``process_automation_enabled``, gates the entire Jun 9
process-automation suite so production can carry the code dormant — behaving
exactly as before — while the test environment runs with it on. Flip the
checkbox in **ERPNext Enhancements Settings → Process Automation** when the
test environment is producing what you want; no deploy needed (server hooks
read the live value; desk clients pick the flag up from bootinfo on their
next page load, same model as live-collab).

Gated when OFF (the suite is dormant):
* Closed-Won SMS alerts, the won-but-unconverted daily reminder, and the
  Payment Received comment + PM/AE alerts (``status_alerts``).
* The PRO-0204 hand-off engine: step seeding on new projects, step
  notifications, SLA escalations, the form progress bar and Start button
  (``process_steps`` + ``process_steps.js``).
* Contract generation: the Generate Contract buttons and ``create_contract``
  / the Sales Pipeline board data (``project_contract``, ``sales_pipeline``).

Deliberately NOT gated (invisible, zero flow impact — and they make the
eventual flip seamless instead of starting from blank data):
* Silent data stamps: ``custom_stage_changed_on`` on Opportunity status
  changes and the Payment Received date default — so stage-aging and
  payment history are already meaningful the day the board goes live.
* Schema, fixtures, seeded templates (Process Step Templates, Contract
  Templates) — data at rest.
* The Task Dashboard block (it only appears where someone has explicitly
  added it to a Workspace — placement is its own switch).
* Form-level conveniences applied by fixtures (Lead quick-entry dialog,
  Opportunity field descriptions) — property setters can't be gated by a
  runtime flag; they are low-risk and documented in the settings field.
"""

import frappe
from frappe.utils import cint


def process_automation_enabled():
	"""True when the Jun 9 process-automation suite is switched on."""
	return bool(
		cint(
			frappe.db.get_single_value("ERPNext Enhancements Settings", "process_automation_enabled")
		)
	)


def ai_write_gating_enabled():
	"""True when AI-initiated mutations through FAC require human confirmation.

	Default OFF (the staged-rollout contract): the gate code ships dormant and
	behaves byte-identically to before until the checkbox in **ERPNext
	Enhancements Settings → AI Governance** is flipped — no deploy needed.
	"""
	return bool(
		cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "ai_write_gating_enabled"))
	)


def field_description_icons_enabled():
	"""True when field descriptions render as hover ⓘ icons.

	Drives the global desk script in
	``public/js/global_enhancements/field_description_icons.js`` via
	``frappe.boot.ee_field_description_icons`` (see boot.py). Default ON: the
	Check field ships ``default "1"`` (applied when a new site first creates the
	Settings Single) and the ``default_field_description_icons_on`` patch writes
	1 on existing installs; a user who unchecks it is then respected.
	"""
	return bool(
		cint(
			frappe.db.get_single_value("ERPNext Enhancements Settings", "field_description_icons_enabled")
		)
	)


def fleet_maintenance_enabled():
	"""True when the Fleet Maintenance suite is switched on.

	Default OFF (the staged-rollout contract): the nightly status refresh and
	reminders stay dormant until the checkbox in **ERPNext Enhancements Settings
	→ Fleet Maintenance** is flipped. The doctypes/forms themselves are always
	usable; this gates only the background automation.
	"""
	return bool(
		cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "fleet_maintenance_enabled"))
	)


def fleet_reminders_enabled():
	"""True when fleet status changes notify fleet managers (default ON once the
	suite is enabled — see the ``default_fleet_reminders_on`` patch). Turn off to
	keep the due dashboard without the desk notifications."""
	return bool(
		cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "fleet_reminders_enabled"))
	)


def document_merge_enabled():
	"""True when the generic Document Merge tool is switched on.

	Default OFF: the engine and the desk "Merge into…" button ship dormant until
	the checkbox in **ERPNext Enhancements Settings → Document Merge** is flipped
	(no deploy needed — the server guard reads the live value and the desk button
	picks the flag up from bootinfo on the next page load). Gating a destructive,
	irreversible tool behind an explicit switch keeps it off until an admin
	deliberately enables it.
	"""
	return bool(
		cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "document_merge_enabled"))
	)


def throw_if_process_automation_disabled():
	"""Guard for whitelisted entry points — explains instead of misbehaving."""
	if not process_automation_enabled():
		frappe.throw(
			frappe._(
				"The process-automation suite is currently switched off "
				"(ERPNext Enhancements Settings → Process Automation)."
			),
			title=frappe._("Feature Disabled"),
		)


def throw_if_document_merge_disabled():
	"""Guard for the Document Merge whitelisted endpoints."""
	if not document_merge_enabled():
		frappe.throw(
			frappe._(
				"The Document Merge tool is currently switched off "
				"(ERPNext Enhancements Settings → Document Merge)."
			),
			title=frappe._("Feature Disabled"),
		)
