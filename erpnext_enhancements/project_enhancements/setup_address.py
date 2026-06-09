"""One-off setup helper for the Address map integration.

Creates the Custom Fields the Address form script relies on (address/address.js
auto-builds ``custom_full_address`` and embeds a Google Maps iframe into
``custom_map_placeholder``). This is a manually-invoked installer (e.g. run from
``bench execute``); it is not wired into hooks.py (no after_migrate / scheduler
entry) and is safe to re-run because it skips fields that already exist.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def setup_fields():
	"""Create the Address Custom Fields used by the map integration (idempotent).

	Adds a "Map" Section Break, the read-only ``custom_full_address`` Data field, and
	the ``custom_map_placeholder`` HTML field. Each field is created only if it does
	not already exist, then the transaction is committed. Prints progress to stdout
	and has the side effect of writing Custom Field records.
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
