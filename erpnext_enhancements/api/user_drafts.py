import frappe
import json

@frappe.whitelist()
def save_draft(ref_doctype, ref_name, form_data):
	"""
	Upsert a User Form Draft for the current user.
	"""
	if not ref_doctype or not ref_name:
		return

	user = frappe.session.user
	if user == "Guest":
		return

	# Check for existing draft
	existing_draft = frappe.db.get_value(
		"User Form Draft",
		{"ref_doctype": ref_doctype, "ref_name": ref_name, "user": user},
		"name"
	)

	if existing_draft:
		# Update
		doc = frappe.get_doc("User Form Draft", existing_draft)
		doc.form_data = form_data
		doc.save(ignore_permissions=True)
	else:
		# Create
		doc = frappe.get_doc({
			"doctype": "User Form Draft",
			"ref_doctype": ref_doctype,
			"ref_name": ref_name,
			"user": user,
			"form_data": form_data
		})
		doc.insert(ignore_permissions=True)

	return doc.name

@frappe.whitelist()
def delete_draft(ref_doctype, ref_name):
	"""
	Delete a User Form Draft for the current user.
	"""
	if not ref_doctype or not ref_name:
		return

	user = frappe.session.user
	if user == "Guest":
		return

	existing_draft = frappe.db.get_value(
		"User Form Draft",
		{"ref_doctype": ref_doctype, "ref_name": ref_name, "user": user},
		"name"
	)

	if existing_draft:
		frappe.delete_doc("User Form Draft", existing_draft, ignore_permissions=True)
