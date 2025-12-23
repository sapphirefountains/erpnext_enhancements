import frappe
from frappe.model.document import Document


class ERPNextEnhancementsSettings(Document):
	pass

@frappe.whitelist()
def get_auto_save_configuration():
	# Security: Ensure user is a System User (Desk User)
	roles = frappe.get_roles(frappe.session.user)
	if "System User" not in roles:
		return {}

	doc = frappe.get_single("ERPNext Enhancements Settings")
	return {
		"auto_save_doctypes": [d.dt for d in doc.auto_save_doctypes],
		"auto_save_users": [u.user for u in doc.auto_save_users]
	}
