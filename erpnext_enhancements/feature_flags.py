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
