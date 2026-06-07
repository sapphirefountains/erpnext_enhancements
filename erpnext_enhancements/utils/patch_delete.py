def patch_delete_doc():
	"""
	Monkey patch frappe.delete_doc to catch LinkExistsError 
	and return a special signal that the frontend can use to offer unlinking.
	"""
	import frappe
	from frappe.exceptions import LinkExistsError

	if getattr(frappe, "_delete_doc_patched_v4", False):
		return

	# 1. Patch frappe.client.delete
	import frappe.client
	original_client_delete = frappe.client.delete
	
	def patched_client_delete(doctype, name):
		try:
			return original_client_delete(doctype, name)
		except LinkExistsError as e:
			if frappe.request and frappe.request.method == "POST":
				return {
					"link_exists": True,
					"doctype": doctype,
					"name": name,
					"error_message": str(e)
				}
			raise e

	frappe.client.delete = patched_client_delete
	
	# 2. Patch reportview delete (bulk delete)
	try:
		import frappe.desk.reportview
		if hasattr(frappe.desk.reportview, "delete_items"):
			original_delete_items = frappe.desk.reportview.delete_items
			def patched_delete_items(*args, **kwargs):
				try:
					return original_delete_items(*args, **kwargs)
				except LinkExistsError as e:
					if frappe.request and frappe.request.method == "POST":
						return {
							"link_exists": True,
							"error_message": str(e)
						}
					raise e
			frappe.desk.reportview.delete_items = patched_delete_items
	except ImportError:
		pass

	frappe._delete_doc_patched_v4 = True
