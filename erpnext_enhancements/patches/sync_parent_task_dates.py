"""Make every existing parent task's expected dates follow its subtasks (v1.784.0, Nikolas 2026-11-03).

The rule is ``task_enhancements/parent_dates.py``: a task with subtasks has the earliest subtask
start and the latest subtask end, kept by Task hooks from now on. This patch brings the tasks that
exist up to the rule once: ``parent_dates.sync_all`` derives every parent bottom-up (a grandparent
sees its parents as the rule leaves them) and writes only the two date columns that differ, with
``update_modified`` off.

* **Idempotent.** A second run derives the same dates and writes nothing.
* **Cannot raise.** A patch that raises aborts ``bench migrate``, which on this repo is the deploy.
  A failure is logged and the hooks keep correcting parents as their subtasks are saved; re-run with
  ``bench --site <site> execute erpnext_enhancements.patches.sync_parent_task_dates.execute``.
"""

import frappe

LOG_TITLE = "Task: parent dates one-time sync"


def execute():
	try:
		if not frappe.db.exists("DocType", "Task"):
			return
		parents, written = sync()
		print(f"Parent task dates: {parents} parents, {written} changed")
	except Exception:
		frappe.log_error(
			title=LOG_TITLE,
			message=(
				"Existing parent tasks were not brought up to the rule; the hooks correct each one on its "
				"subtasks' next save. Re-run this patch with bench execute.\n\n" + frappe.get_traceback()
			),
		)


def sync():
	"""``(parents, written)`` from :func:`parent_dates.sync_all`; raises on failure (``execute`` logs)."""
	from erpnext_enhancements.task_enhancements import parent_dates

	if not parent_dates._ready():
		return 0, 0
	return parent_dates.sync_all()
