"""Rename hand-off step 5 to "Initial Payment From Customer" (v1.260.0).

``step_title`` is *copied onto every project's child rows* when the process is
seeded (``process_steps._append_steps``), not looked up from the template. So
renaming the template alone leaves every existing project displaying the old
wording — 88 rows across 85 open projects at the time of writing — and the site
shows two different names for the same step depending on which project you open.

Unlike its sibling :mod:`rename_handoff_ar_role`, nothing here fails *silently*:
``step_title`` is a Data field, and every consumer (the process tracker, the
production dashboard, both hand-off reports, the escalation emails, the sales
pipeline rail) only ever displays it. Nothing matches on it — the one
title-based match in the codebase, ``/task/i`` in ``process_steps.js``, is step
6 and is untouched. That was verified before this rename rather than assumed,
because a title match hiding somewhere is exactly what would turn a cosmetic
patch into a broken process.

Scoped to the exact old value, so a row someone has re-titled by hand is never
touched, and idempotent — the filter cannot match after it has run.
``update_modified=False`` throughout: this is a label migration, and touching
``modified`` would drag 85 projects to the top of every recently-modified list
on the site.
"""

import frappe

OLD_TITLE = "Receive Customer Payment"
NEW_TITLE = "Initial Payment From Customer"

#: The template holds the definition; the child rows hold the copies made when
#: each project was seeded. Both need it. Order does not matter.
DOCTYPES = ("Process Step Template", "Project Process Step")


def execute():
	for doctype in DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			if not frappe.db.has_column(doctype, "step_title"):
				continue
		except Exception:
			continue

		# Interpolated because a table name cannot be a bind parameter. Safe:
		# DOCTYPES is a hard-coded tuple in this file, never user input. The
		# values themselves are bound.
		table = f"tab{doctype}"
		affected = frappe.db.sql(
			f"SELECT COUNT(*) FROM `{table}` WHERE step_title = %(old)s",
			{"old": OLD_TITLE},
		)[0][0]
		if not affected:
			continue

		frappe.db.sql(
			f"""
			UPDATE `{table}`
			SET step_title = %(new)s
			WHERE step_title = %(old)s
			""",
			{"new": NEW_TITLE, "old": OLD_TITLE},
		)
		frappe.log_error(
			f"Renamed step_title on {affected} {doctype} row(s): "
			f"{OLD_TITLE!r} -> {NEW_TITLE!r}",
			"Hand-off payment step rename",
		)
