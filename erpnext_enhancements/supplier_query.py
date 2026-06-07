import frappe

def sync_supplier_groups(doc, method=None):
	"""
	Combines primary supplier group and additional supplier groups into search and list fields.
	"""
	primary = getattr(doc, "supplier_group", None)
	additional_list = []
	
	# Additional groups from child table
	additional_rows = doc.get("custom_additional_supplier_groups") or []
	for row in additional_rows:
		if row.get("supplier_group") and row.supplier_group not in additional_list:
			# Only add to additional list if it's NOT the primary group
			if row.supplier_group != primary:
				additional_list.append(row.supplier_group)
	
	# 1. Update the Search Field (Primary + Additional)
	all_groups = []
	if primary:
		all_groups.append(primary)
	all_groups.extend(additional_list)
	
	if all_groups:
		doc.custom_supplier_groups_search = ", " + ", ".join(all_groups) + ", "
	else:
		doc.custom_supplier_groups_search = ""
		
	# 2. Update the Display List Field (Additional Only)
	doc.custom_additional_supplier_groups_list = ", ".join(additional_list)
		
def sync_all_suppliers():
	suppliers = frappe.get_all("Supplier")
	for s in suppliers:
		doc = frappe.get_doc("Supplier", s.name)
		sync_supplier_groups(doc)
		# Update both fields
		frappe.db.set_value("Supplier", doc.name, {
			"custom_supplier_groups_search": doc.custom_supplier_groups_search,
			"custom_additional_supplier_groups_list": doc.custom_additional_supplier_groups_list
		}, update_modified=False)
