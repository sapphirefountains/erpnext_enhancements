import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def setup_fields():
	"""
	Creates custom fields for Address doctype to support Map integration.
	"""
	click_fields = [
		{
			"fieldname": "custom_map_section",
			"label": "Map",
			"fieldtype": "Section Break",
			"insert_after": "pincode",
		},
		{
			"fieldname": "custom_full_address",
			"label": "Full Address",
			"fieldtype": "Data",
			"insert_after": "custom_map_section",
			"read_only": 1,
			"description": "Auto-generated from address fields",
		},
		{
			"fieldname": "custom_map_placeholder",
			"label": "Map Placeholder",
			"fieldtype": "HTML",
			"insert_after": "custom_full_address",
		},
	]

	for field in click_fields:
		if not frappe.db.exists("Custom Field", {"dt": "Address", "fieldname": field["fieldname"]}):
			create_custom_field("Address", field)
			print(f"Created field {field['fieldname']}")
		else:
			print(f"Field {field['fieldname']} already exists")

	frappe.db.commit()
