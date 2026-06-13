"""Idempotent custom-field provisioning for Device Management.

Registered in ``after_migrate`` (hooks.py). Adds a read-only "Assigned Devices"
panel to the Employee form (an HTML widget rendered by
``public/js/device_management/employee_devices.js``) so HR / managers see a
person's devices in context. Insert-only — it never rewrites an existing field —
mirroring ``setup.custom_fields.create_comments_tab``; if the field is later
exported to ``fixtures/custom_field.json`` the fixture owns it and this becomes a
no-op for that field.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_device_employee_fields():
	"""``after_migrate`` entry point: add the Employee "Assigned Devices" panel."""
	if not frappe.db.exists("DocType", "Employee"):
		return

	meta = frappe.get_meta("Employee")
	# Anchor after our existing vehicle-warehouse custom field when present, else
	# after the last non-tab field so the widget lands at the form's end.
	anchor = "custom_default_vehicle_warehouse"
	if not meta.has_field(anchor):
		non_tab = [f.fieldname for f in meta.fields if f.fieldtype != "Tab Break"]
		anchor = non_tab[-1] if non_tab else None

	fields = [
		{
			"fieldname": "custom_managed_devices_section",
			"label": "Assigned Devices",
			"fieldtype": "Section Break",
			"insert_after": anchor,
			"collapsible": 1,
		},
		{
			"fieldname": "custom_managed_devices_html",
			"label": "Assigned Devices",
			"fieldtype": "HTML",
			"insert_after": "custom_managed_devices_section",
		},
	]
	to_create = [f for f in fields if not meta.has_field(f["fieldname"])]
	if to_create:
		create_custom_fields({"Employee": to_create}, update=True)
