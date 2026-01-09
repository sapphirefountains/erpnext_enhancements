import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field


def execute():
	doctype = "Project"
	if not frappe.db.exists("Custom Field", f"{doctype}-custom_project_notes"):
		create_custom_field(
			doctype,
			{
				"fieldname": "custom_project_notes",
				"label": "Project Notes",
				"fieldtype": "Table",
				"options": "Project Note",
				"insert_after": "custom_comments_field",
			},
		)
