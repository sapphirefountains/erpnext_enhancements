import frappe


def execute():
	# Doctypes to delete
	doctypes_to_delete = ["Master Project", "Sub Projects List"]
	for dt in doctypes_to_delete:
		if frappe.db.exists("DocType", dt):
			frappe.delete_doc("DocType", dt, ignore_missing=True, force=True)

	# Custom Fields to delete
	custom_fields_to_delete = ["Project-custom_master_project", "Master Project-task_tree_view"]
	for cf_name in custom_fields_to_delete:
		if frappe.db.exists("Custom Field", cf_name):
			frappe.delete_doc("Custom Field", cf_name, ignore_missing=True, force=True)

	frappe.db.commit()
