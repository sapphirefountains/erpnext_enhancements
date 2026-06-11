"""Delete the legacy Travel Trip rows ahead of the v1.15.0 redesign.

The travel_management module is rebuilt in v1.15.0: Travel Trip flips from
submittable to non-submittable, the single ``employee`` field is replaced by
a Trip Traveler child table, the ``accommodation`` table fieldname becomes
``accommodations``, and several columns are dropped. Only two Travel Trips
were ever created on the production site (both docstatus 0 drafts, stuck in
the old workflow's Draft state) — re-entering them costs minutes, so they
are deleted rather than converted. This must run pre_model_sync so the
``is_submittable`` flip and field removals sync against an empty table.

IMPORTANT: this works on raw table rows (``frappe.db.delete``), NOT
``frappe.delete_doc``. Pre-model-sync the document loads with the NEW
controller and meta against the OLD schema: ``get_doc`` would query child
tables that don't exist yet (tabTrip Traveler, ...) and the new ``on_trash``
would filter on the ``custom_travel_trip`` Custom Field that fixtures only
create later in the migrate — both crash (seen live on the first deploy:
``Unknown column 'custom_travel_trip' in 'WHERE'``).

Note: on Frappe 16 the dropped columns (``employee``, ``workflow_state``,
``custom_expense_claim``, ...) remain as orphans on the data table until a
``bench trim-database`` removes them — same caveat as
delete_abandoned_doctypes.py.

Idempotent and fresh-install-safe: every delete is guarded by a table-exists
check and the loops are empty when no rows exist.
"""

import frappe

# The child tables that exist in the OLD schema (the new ones — Trip Traveler,
# Trip Expense, Trip Mileage — are created by model sync after this patch).
LEGACY_CHILD_TABLES = (
	"Trip Flight",
	"Trip Accommodation",
	"Trip Ground Transport",
	"Trip Agenda",
)

# Sidecar records frappe.delete_doc would normally clean up.
SIDECARS = (
	("Comment", "reference_doctype", "reference_name"),
	("Version", "ref_doctype", "docname"),
	("ToDo", "reference_type", "reference_name"),
	("DocShare", "share_doctype", "share_name"),
	("Workflow Action", "reference_doctype", "reference_name"),
)


def execute():
	if not frappe.db.table_exists("Travel Trip"):
		return

	names = frappe.get_all("Travel Trip", pluck="name")
	if not names:
		return

	for child_table in LEGACY_CHILD_TABLES:
		if frappe.db.table_exists(child_table):
			frappe.db.delete(child_table, {"parenttype": "Travel Trip"})

	for doctype, dt_field, name_field in SIDECARS:
		if frappe.db.table_exists(doctype):
			frappe.db.delete(doctype, {dt_field: "Travel Trip", name_field: ("in", names)})

	frappe.db.delete("Travel Trip")
