"""Rename the hand-off finance role to "Finance & Accounting Manager" (v1.251.0).

``responsible_role`` on **Process Step Template** and **Project Process Step** is a
Select, and its value is *stored on every row* rather than looked up. So renaming
the option in the DocType JSON without migrating the data would leave 132 rows
(2 templates + 130 project steps at the time of writing) holding a value the
field no longer offers.

That fails quietly, which is the reason this patch exists rather than a note in
the release: Frappe renders a Select whose stored value is not in its options as
**blank** on the form, the Hand-Off SLA Compliance report's role filter stops
matching those rows, and ``process_steps._resolve_responsible`` — which compares
``responsible_role`` against ``ROLE_AR`` to find the right recipient — stops
resolving anyone, so the finance steps silently stop notifying. Nothing raises.

Scoped to the exact old value so a row someone has since re-assigned by hand is
never touched, and idempotent (the filter cannot match after it has run).
``update_modified=False`` throughout: this is a label migration, and touching
``modified`` would drag every affected Project to the top of every
recently-modified list on the site.
"""

import frappe

OLD_ROLE = "Accounts Receivable"
NEW_ROLE = "Finance & Accounting Manager"

#: Both carry the same Select. Order does not matter; neither reads the other.
DOCTYPES = ("Process Step Template", "Project Process Step")


def execute():
	for doctype in DOCTYPES:
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			if not frappe.db.has_column(doctype, "responsible_role"):
				continue
		except Exception:
			continue

		# Interpolated because a table name cannot be a bind parameter. Safe:
		# DOCTYPES is a hard-coded tuple in this file, never user input. The
		# values themselves are bound.
		table = f"tab{doctype}"
		affected = frappe.db.sql(
			f"SELECT COUNT(*) FROM `{table}` WHERE responsible_role = %(old)s",
			{"old": OLD_ROLE},
		)[0][0]
		if not affected:
			continue

		frappe.db.sql(
			f"""
			UPDATE `{table}`
			SET responsible_role = %(new)s
			WHERE responsible_role = %(old)s
			""",
			{"new": NEW_ROLE, "old": OLD_ROLE},
		)
		frappe.log_error(
			f"Renamed responsible_role on {affected} {doctype} row(s): "
			f"{OLD_ROLE!r} -> {NEW_ROLE!r}",
			"Hand-off role rename",
		)
