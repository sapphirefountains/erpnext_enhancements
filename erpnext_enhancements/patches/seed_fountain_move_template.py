"""Seed the Fountain Move Project Template (Fountain Move jobs, Nik 2026-10-21).

Creates, as an ERPNext **Project Template** (project type Service) made of three template Tasks
(``is_template``), Nik's compact move job (``fountain_move_jobs.TEMPLATE_TASKS``):

1. *Confirm move date with customer*: office, 0 h.
2. *Move day*: drain, disconnect, load, transport, set, reconnect, fill, startup test and walkthrough;
   4 h for the whole crew, crew size 2, Outdoor.
3. *Invoice the move*: office, 0 h.

* **Insert-only, by name.** When a Project Template called "Fountain Move" exists the patch does
  nothing at all: once seeded it is Nik's to edit in the Desk, and a deleted one stays deleted.
* **All or nothing.** The three Tasks and the template go in under one savepoint; a failure rolls
  them back together, so no orphan template Task is left behind.
* **Cannot raise.** A patch that raises aborts ``bench migrate``, which on this repo is the deploy.
  A failure is logged; re-run with
  ``bench --site <site> execute erpnext_enhancements.patches.seed_fountain_move_template.execute``.
"""

import frappe

from erpnext_enhancements.project_enhancements import fountain_move_jobs

SAVEPOINT = "seed_fountain_move_template"


def execute():
	try:
		if not frappe.db.exists("DocType", fountain_move_jobs.TEMPLATE_DOCTYPE):
			return
		frappe.db.savepoint(SAVEPOINT)
		try:
			result = fountain_move_jobs.seed_template()
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			raise
		print(f"Fountain Move template: {result}")
	except Exception:
		frappe.log_error(
			title=fountain_move_jobs.LOG_TITLE,
			message=(
				"The Fountain Move Project Template was not seeded, so ticking Fountain Move on a new "
				"project adds no tasks until it exists. Re-run this patch with bench execute.\n\n"
				+ frappe.get_traceback()
			),
		)
