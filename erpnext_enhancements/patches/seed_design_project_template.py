"""Seed the Design Project (PRO-0206) template and retire the old one (Nik, 2026-10-30).

Creates, as an ERPNext **Project Template** (project type Design) made of four template Tasks
(``is_template``), PRO-0206's design stages (``design_projects.STAGES``), each waiting on the one
before: Predesign (PD) days 0-13, Schematic Design (SD) 14-34, Design Development (DD) 35-62 and
Construction Documents (CD) 63-83. Then disables the old "Design Project Template", so the Create
Project dialog and the Project form stop offering it.

* **Insert-only, by name.** When a Project Template called "Design Project (PRO-0206)" exists the seed
  does nothing: once seeded it is Nik's to edit in the Desk, and a deleted one stays deleted.
* **All or nothing.** The four Tasks and the template go in under one savepoint; a failure rolls them
  back together, so no orphan template Task is left behind.
* **The old template is disabled only when the new one exists.** Nothing else about it changes: its
  40 template Tasks and the 16 projects made from it are untouched, and a project's link to a disabled
  template still validates. If the seed failed, Design projects keep the template they have.
* **Cannot raise.** A patch that raises aborts ``bench migrate``, which on this repo is the deploy.
  A failure is logged; re-run with
  ``bench --site <site> execute erpnext_enhancements.patches.seed_design_project_template.execute``.
"""

import frappe

from erpnext_enhancements.project_enhancements import design_projects

SAVEPOINT = "seed_design_project_template"


def execute():
	try:
		if not frappe.db.exists("DocType", design_projects.TEMPLATE_DOCTYPE):
			return
		frappe.db.savepoint(SAVEPOINT)
		try:
			result = design_projects.seed_template()
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			raise
		print(f"Design Project (PRO-0206) template: {result}")
	except Exception:
		frappe.log_error(
			title=design_projects.LOG_TITLE,
			message=(
				"The Design Project (PRO-0206) template was not seeded, so the old Design Project Template "
				"was left enabled. Re-run this patch with bench execute.\n\n" + frappe.get_traceback()
			),
		)
		return
	try:
		print(f"Old Design Project Template: {design_projects.disable_old_template()}")
	except Exception:
		frappe.log_error(
			title=design_projects.LOG_TITLE,
			message=(
				"The old Design Project Template was not disabled, so it is still offered beside the new "
				"one. Tick its Disabled box by hand or re-run this patch with bench execute.\n\n"
				+ frappe.get_traceback()
			),
		)
