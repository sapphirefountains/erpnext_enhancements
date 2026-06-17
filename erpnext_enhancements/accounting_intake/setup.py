# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""after_migrate setup for Accounting Intake. Adds the ``custom_drive_folder_id``
field to Supplier (the per-supplier Google Drive folder id used by document
filing) — mirroring the existing Customer/Project/Opportunity fields. Idempotent,
like the other ``setup`` field creators wired in hooks.py ``after_migrate``."""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def create_supplier_drive_field():
	create_custom_fields(
		{
			"Supplier": [
				{
					"fieldname": "custom_drive_folder_id",
					"label": "Drive Folder ID",
					"fieldtype": "Data",
					"insert_after": "supplier_group",
					"read_only": 1,
					"hidden": 1,
					"no_copy": 1,
					"print_hide": 1,
				}
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
