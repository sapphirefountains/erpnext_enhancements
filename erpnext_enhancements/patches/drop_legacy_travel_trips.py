"""Delete the legacy Travel Trip documents ahead of the v1.15.0 redesign.

The travel_management module is rebuilt in v1.15.0: Travel Trip flips from
submittable to non-submittable, the single ``employee`` field is replaced by
a Trip Traveler child table, the ``accommodation`` table fieldname becomes
``accommodations``, and several columns are dropped. Only two Travel Trips
were ever created on the production site (both docstatus 0 drafts, stuck in
the old workflow's Draft state) — re-entering them costs minutes, so they
are deleted rather than converted. This must run pre_model_sync so the
``is_submittable`` flip and field removals sync against an empty table.

Note: on Frappe 16 the dropped columns (``employee``, ``workflow_state``,
``custom_expense_claim``, ...) remain as orphans on the data table until a
``bench trim-database`` removes them — same caveat as
delete_abandoned_doctypes.py.

Idempotent and fresh-install-safe: the loop is empty when no Travel Trip
exists (the doctype itself ships with this app, so the table is present on
any site that has run migrate before).
"""

import frappe


def execute():
	if not frappe.db.table_exists("Travel Trip"):
		return

	for name in frappe.get_all("Travel Trip", pluck="name"):
		frappe.delete_doc("Travel Trip", name, force=True, ignore_permissions=True)
