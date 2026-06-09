"""Supplier multi-group denormalization.

A Supplier has one standard ``supplier_group`` (relabeled "Primary Supplier
Group") plus a custom ``custom_additional_supplier_groups`` child table. To make
all of a supplier's groups searchable/visible from list view, this module
flattens them into two read-only text fields. The custom fields and the child
doctype are created by ``setup/supplier_groups.py`` at ``after_migrate``.
"""
import frappe

def sync_supplier_groups(doc, method=None):
	"""Flatten primary + additional supplier groups into the denormalized fields.

	Wired to the Supplier ``validate`` doc_event (see hooks.py). Writes a
	comma-wrapped ``custom_supplier_groups_search`` (hidden, all groups, padded
	with leading/trailing commas for whole-token matching) and a human-readable
	``custom_additional_supplier_groups_list`` (additional groups only).
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
	"""Backfill the denormalized group fields on every existing Supplier.

	One-shot maintenance helper (run manually, e.g. from ``bench execute``) for
	suppliers saved before this feature existed. Writes directly with
	``update_modified=False`` so it does not touch each doc's modified timestamp.
	"""
	suppliers = frappe.get_all("Supplier")
	for s in suppliers:
		doc = frappe.get_doc("Supplier", s.name)
		sync_supplier_groups(doc)
		# Update both fields
		frappe.db.set_value("Supplier", doc.name, {
			"custom_supplier_groups_search": doc.custom_supplier_groups_search,
			"custom_additional_supplier_groups_list": doc.custom_additional_supplier_groups_list
		}, update_modified=False)
