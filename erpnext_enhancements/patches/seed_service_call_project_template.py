"""Seed the three-task "Service Call" Project Template (Nikolas, 2026-11-03).

A Service Call's Project (``sapphire_maintenance/service_calls.py``) is made on this template:
**Approval received**, **Service visit** and **Invoice sent** (``service_calls.TASKS``), each a
leaf task waiting on the one before. ERPNext dates them from the project's expected start by the
template's offsets; the intake then re-dates them from the scheduled visit
(``service_calls.redate_tasks``), so nothing is born Overdue. The eleven-task build template stays
for Build and Design.

* **Insert-only, by name.** When a Project Template called "Service Call" exists the seed does
  nothing: once seeded it is Nikolas's to edit in the Desk, and a deleted one stays deleted.
* **All or nothing.** The three Tasks and the template go in under one savepoint.
* **Cannot raise.** A patch that raises aborts ``bench migrate``, which on this repo is the deploy.
  A failure is logged; re-run with
  ``bench --site <site> execute erpnext_enhancements.patches.seed_service_call_project_template.execute``.
"""

import frappe

from erpnext_enhancements.sapphire_maintenance import service_calls

SAVEPOINT = "seed_service_call_project_template"
LOG_TITLE = "Service calls: project template"


def execute():
	try:
		if not frappe.db.exists("DocType", "Project Template"):
			return
		frappe.db.savepoint(SAVEPOINT)
		try:
			result = seed_template()
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			raise
		print(f"Service Call project template: {result}")
	except Exception:
		frappe.log_error(
			title=LOG_TITLE,
			message=(
				"The Service Call project template was not seeded, so a new Service Call's project gets no "
				"tasks. Re-run this patch with bench execute.\n\n" + frappe.get_traceback()
			),
		)


def seed_template():
	"""Create the template and its three Tasks unless it exists. ``"created"`` or ``"exists"``."""
	if frappe.db.exists("Project Template", service_calls.PROJECT_TEMPLATE):
		return "exists"
	docs = []
	for task in service_calls.TASKS:
		values = {
			"doctype": "Task",
			"is_template": 1,
			"status": "Template",
			"subject": task["subject"],
			"description": f"<p>{task['description']}</p>",
			"start": int(task["start"]),
			"duration": int(task["duration"]),
		}
		if docs:
			values["depends_on"] = [{"task": docs[-1].name}]
		doc = frappe.get_doc(values)
		doc.insert(ignore_permissions=True)
		docs.append(doc)
	template = {
		"doctype": "Project Template",
		"project_type": service_calls.PROJECT_TYPE if frappe.db.exists("Project Type", service_calls.PROJECT_TYPE) else None,
		"tasks": [{"task": doc.name, "subject": doc.subject} for doc in docs],
	}
	frappe.get_doc(template).insert(ignore_permissions=True, set_name=service_calls.PROJECT_TEMPLATE)
	return "created"
