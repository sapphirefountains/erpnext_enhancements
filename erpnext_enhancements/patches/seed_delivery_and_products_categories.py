"""Seed the "Delivery" and "Products" project categories.

Adds the master records that back the Projects Dashboard category fields so
"Delivery" (a new client-facing value stream / project stage) and "Products"
(a new value stream) become selectable everywhere the existing
Build/Design/Rent/Service options are:

* **Project Type** ``Delivery`` — the ``project_type`` (relabelled "Project
  Stage") Link target on Project; the dashboard treats it as client-facing
  alongside Build/Design/Rent/Service.
* **Value Streams** ``Delivery`` and ``Products`` — the master records behind
  the ``custom_value_stream`` Table MultiSelect on Project / Opportunity /
  Customer (rows link to "Value Streams").

Insert-only and idempotent: existing records are never modified and nothing is
deleted, so re-migrations and any UI renames are preserved. The Select-field
options (``custom_project_priority`` / ``custom_company_priority``) carry their
own "Delivery" entry via the custom_field fixtures — only the Link/Table
masters need seeding here.
"""

import frappe

PROJECT_TYPES = ["Delivery"]
VALUE_STREAMS = ["Delivery", "Products"]


def execute():
	for name in PROJECT_TYPES:
		# Project Type autoname is field:project_type, so name == project_type.
		if not frappe.db.exists("Project Type", name):
			frappe.get_doc({"doctype": "Project Type", "project_type": name}).insert(
				ignore_permissions=True
			)

	for name in VALUE_STREAMS:
		# Value Streams has no autoname rule; set the name explicitly so the
		# record is named "Delivery"/"Products" (matching value_stream) instead
		# of a random hash — the value_stream value is what the dashboard and
		# tag-sync logic compare against.
		if not frappe.db.exists("Value Streams", name):
			doc = frappe.new_doc("Value Streams")
			doc.value_stream = name
			doc.name = name
			doc.insert(ignore_permissions=True)
