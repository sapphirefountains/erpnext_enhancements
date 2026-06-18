"""Backstop: ensure the Opportunity "Hand-Off Process" tab fields exist (v1.68.0).

v1.67.0 added `custom_process_tab` (Tab Break) + `custom_process_progress` (HTML)
to the Opportunity via fixtures, but on at least one deploy the fixture sync did
not apply them (model-sync + the v1.67.0 patches ran, yet the two Custom Fields
were never created), so the tab never appeared on the form.

Patches always run on migrate, so this creates them idempotently via
``create_custom_fields`` regardless of whether fixture sync applied them — the
fixtures remain the source of truth; this is a deploy-agnostic backstop. The tab
is created before the HTML field so the latter's ``insert_after`` resolves.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	if not frappe.db.exists("DocType", "Opportunity"):
		return
	create_custom_fields(
		{
			"Opportunity": [
				{
					"fieldname": "custom_process_tab",
					"label": "Hand-Off Process",
					"fieldtype": "Tab Break",
					"insert_after": "dashboard_tab",
				},
				{
					"fieldname": "custom_process_progress",
					"label": "Process Progress",
					"fieldtype": "HTML",
					"insert_after": "custom_process_tab",
				},
			]
		},
		ignore_validate=True,
	)
	# create_custom_fields stamps is_system_generated=1; the fixtures own these
	# (is_system_generated=0). Normalize so a future export-fixtures keeps them.
	for fieldname in ("custom_process_tab", "custom_process_progress"):
		name = frappe.db.get_value("Custom Field", {"dt": "Opportunity", "fieldname": fieldname})
		if name:
			frappe.db.set_value("Custom Field", name, "is_system_generated", 0, update_modified=False)
