"""Make PRJ-00775 ("Michelle Ainge - 4 Pots Moved") the first Fountain Move job.

Nik approved exactly this on 2026-10-21:

* project type Build -> **Service**, **Fountain Move** ticked, template -> **Fountain Move**
  (field-level writes, so no Project hook re-runs for three fields none of them read);
* the 11 tasks ERPNext made from **Service Project Template** (Project Launch, Billing, Service
  Visit(s) and their subtasks) set to **Canceled**, nothing deleted. Only a task that is still Open
  and untouched (nobody assigned, no hours, no timesheet) is canceled; anything else is left and
  logged;
* the Fountain Move template's three tasks added **undated**, so the Move day sits in the
  Maintenance Planner's Unscheduled tray until it is dragged onto the real day.

Runs after ``seed_fountain_move_template`` and creates the ``custom_is_fountain_move`` Custom Field
first when it is missing: patches run before fixture sync, so on this deploy the field does not exist
yet (the ``backfill_stage_changed_on`` precedent); fixture sync adopts the same record later.

Safe twice (``fountain_move_jobs.make_move_job`` skips what is done). Does nothing when the project
or the template is missing. Cannot raise: one savepoint, rolled back whole and logged on a failure.
"""

import json

import frappe

from erpnext_enhancements.project_enhancements import fountain_move_jobs

PROJECT = "PRJ-00775"
OLD_TEMPLATE = "Service Project Template"
FIELD = "Project-custom_is_fountain_move"
SAVEPOINT = "make_prj_00775_a_move_job"


def execute():
	try:
		if not frappe.db.exists("Project", PROJECT):
			return
		ensure_field()
		if not fountain_move_jobs.template_exists():
			frappe.log_error(
				title=fountain_move_jobs.LOG_TITLE,
				message=f"{PROJECT} was not made a Fountain Move: the Fountain Move template does not exist.",
			)
			return
		frappe.db.savepoint(SAVEPOINT)
		try:
			report = fountain_move_jobs.make_move_job(PROJECT, cancel_from_template=OLD_TEMPLATE)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			raise
		print(f"{PROJECT} as a Fountain Move: {json.dumps(report, default=str)}")
		if report.get("kept"):
			frappe.log_error(
				title=fountain_move_jobs.LOG_TITLE,
				message=(
					f"{PROJECT}: these tasks from {OLD_TEMPLATE} were not canceled because someone had "
					f"started on them: {', '.join(report['kept'])}"
				),
			)
	except Exception:
		frappe.log_error(
			title=fountain_move_jobs.LOG_TITLE,
			message=(
				f"{PROJECT} was not made a Fountain Move (rolled back whole). Re-run this patch with bench "
				"execute.\n\n" + frappe.get_traceback()
			),
		)


def ensure_field():
	"""Create the Fountain Move Check from its fixture record when it does not exist yet."""
	if frappe.db.exists("Custom Field", FIELD):
		return
	path = frappe.get_app_path("erpnext_enhancements", "fixtures", "custom_field.json")
	with open(path, encoding="utf-8") as handle:
		record = next((row for row in json.load(handle) if row.get("name") == FIELD), None)
	if record:
		frappe.get_doc(dict(record)).insert(ignore_permissions=True)
