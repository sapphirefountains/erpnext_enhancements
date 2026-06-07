import frappe


def execute():
	"""Rename existing Master Project records to their title."""
	master_projects = frappe.get_all("Master Project", fields=["name", "title"])

	for mp in master_projects:
		if mp.name != mp.title:
			try:
				frappe.rename_doc("Master Project", mp.name, mp.title, force=True, ignore_permissions=True)
			except Exception:
				frappe.log_error(
					f"Failed to rename Master Project {mp.name} to {mp.title}", "Master Project Rename Error"
				)
