"""Idempotent provisioning for the Supplier multi-group feature.

Entry point :func:`create_supplier_group_customizations` is registered in
``after_migrate`` (hooks.py). It relabels the standard ``supplier_group`` field
to "Primary Supplier Group" and adds the additional-groups Table MultiSelect
plus the two denormalized text fields populated at runtime by
``supplier_query.sync_supplier_groups``. Each step is guarded by an existence
check, so re-running on migrate is a no-op.

The "Additional Supplier Group" child doctype these fields rely on ships with
the app (global_enhancements/doctype/additional_supplier_group) since v0.8.0 —
it is synced by doctype sync before this hook runs, so this module no longer
creates it at runtime.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_supplier_group_customizations():
	"""``after_migrate`` entry point: build the supplier multi-group customizations."""
	update_standard_supplier_group_label()
	add_additional_supplier_groups_field()


def update_standard_supplier_group_label():
	"""Relabel Supplier's standard ``supplier_group`` to "Primary Supplier Group"."""
	# Rename standard field to "Primary Supplier Group" via Property Setter
	if not frappe.db.exists(
		"Property Setter", {"doc_type": "Supplier", "field_name": "supplier_group", "property": "label"}
	):
		frappe.get_doc(
			{
				"doctype": "Property Setter",
				"doctype_or_field": "DocField",
				"doc_type": "Supplier",
				"field_name": "supplier_group",
				"property": "label",
				"value": "Primary Supplier Group",
				"property_type": "Data",
			}
		).insert()


def add_additional_supplier_groups_field():
	"""Add the additional-groups MultiSelect + the two denormalized text fields.

	``custom_supplier_groups_search`` is hidden (search-only) and
	``custom_additional_supplier_groups_list`` is shown in list view; both are
	read-only and kept current by ``supplier_query.sync_supplier_groups``.
	"""
	custom_fields = {
		"Supplier": [
			{
				"fieldname": "custom_additional_supplier_groups",
				"label": "Additional Supplier Groups",
				"fieldtype": "Table MultiSelect",
				"options": "Additional Supplier Group",
				"insert_after": "supplier_group",
			},
			{
				"fieldname": "custom_supplier_groups_search",
				"label": "All Supplier Groups (Search)",
				"fieldtype": "Small Text",
				"hidden": 1,
				"read_only": 1,
				"insert_after": "custom_additional_supplier_groups",
			},
			{
				"fieldname": "custom_additional_supplier_groups_list",
				"label": "Additional Groups",
				"fieldtype": "Small Text",
				"read_only": 1,
				"in_list_view": 1,
				"insert_after": "custom_supplier_groups_search",
			},
		]
	}
	create_custom_fields(custom_fields, update=True)
