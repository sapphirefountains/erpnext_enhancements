import frappe
from frappe import _
from frappe.model.docstatus import DocStatus
from frappe.model.dynamic_links import get_dynamic_link_map
from frappe.model.rename_doc import get_link_fields


@frappe.whitelist()
def get_blocking_links(doctype, name):
	"""
	Returns a detailed list of documents blocking the deletion of the target document.
	"""
	# Ensure proper casing (e.g. 'task' -> 'Task')
	real_doctype = frappe.db.get_value("DocType", {"name": doctype}, "name")
	if not real_doctype:
		# Maybe it's already correct or it's a virtual doctype not in DB
		real_doctype = doctype
	
	doctype = real_doctype

	try:
		doc = frappe.get_doc(doctype, name)
	except frappe.DoesNotExistError:
		return []

	links = []
	link_fields = get_link_fields(doctype)
	ignored_doctypes = set(frappe.get_hooks("ignore_links_on_delete"))

	# Standard Links
	for lf in link_fields:
		link_dt, link_field, issingle = lf["parent"], lf["fieldname"], lf["issingle"]
		if link_dt in ignored_doctypes:
			continue

		try:
			meta = frappe.get_meta(link_dt)
		except Exception:
			continue

		if issingle:
			if frappe.db.get_single_value(link_dt, link_field) == name:
				links.append({
					"doctype": link_dt,
					"name": link_dt,
					"fieldname": link_field,
					"is_child": False,
					"is_single": True
				})
			continue

		fields = ["name", "docstatus"]
		if meta.istable:
			fields.extend(["parent", "parenttype", "idx"])

		records = frappe.db.get_values(link_dt, {link_field: name}, fields, as_dict=True)
		for rec in records:
			# Skip if it's just a self-reference or cancelled
			if DocStatus(rec.docstatus).is_cancelled():
				continue
				
			if meta.istable:
				if rec.parenttype == doctype and rec.parent == name:
					continue
				links.append({
					"doctype": rec.parenttype,
					"name": rec.parent,
					"child_doctype": link_dt,
					"child_name": rec.name,
					"fieldname": link_field,
					"is_child": True,
					"idx": rec.idx,
					"docstatus": rec.docstatus
				})
			else:
				if link_dt == doctype and rec.name == name:
					continue
				links.append({
					"doctype": link_dt,
					"name": rec.name,
					"fieldname": link_field,
					"is_child": False,
					"docstatus": rec.docstatus
				})

	# Dynamic Links
	for df in get_dynamic_link_map().get(doctype, []):
		if df.parent in ignored_doctypes:
			continue

		meta = frappe.get_meta(df.parent)
		if meta.issingle:
			refdoc = frappe.db.get_singles_dict(df.parent)
			if refdoc.get(df.options) == doctype and refdoc.get(df.fieldname) == name:
				links.append({
					"doctype": df.parent,
					"name": df.parent,
					"fieldname": df.fieldname,
					"doctype_field": df.options,
					"is_child": False,
					"is_single": True,
					"is_dynamic": True
				})
		else:
			RefDoc = frappe.qb.DocType(df.parent)
			fields = [RefDoc.name, RefDoc.docstatus]
			if meta.istable:
				fields.extend([RefDoc.parent, RefDoc.parenttype, RefDoc.idx])
			
			query = (
				frappe.qb.from_(RefDoc)
				.select(*fields)
				.where(RefDoc[df.options] == doctype)
				.where(RefDoc[df.fieldname] == name)
			)
			for refdoc in query.run(as_dict=True):
				if not DocStatus(refdoc.docstatus).is_cancelled():
					if meta.istable:
						if refdoc.parenttype == doctype and refdoc.parent == name:
							continue
						links.append({
							"doctype": refdoc.parenttype,
							"name": refdoc.parent,
							"child_doctype": df.parent,
							"child_name": refdoc.name,
							"fieldname": df.fieldname,
							"doctype_field": df.options,
							"is_child": True,
							"is_dynamic": True,
							"idx": refdoc.idx,
							"docstatus": refdoc.docstatus
						})
					else:
						if df.parent == doctype and refdoc.name == name:
							continue
						links.append({
							"doctype": df.parent,
							"name": refdoc.name,
							"fieldname": df.fieldname,
							"doctype_field": df.options,
							"is_child": False,
							"is_dynamic": True,
							"docstatus": refdoc.docstatus
						})

	return links


@frappe.whitelist()
def unlink_and_delete(doctype, name):
	if not frappe.has_permission(doctype, "delete", name):
		frappe.throw(_("You do not have permission to delete {0} {1}").format(doctype, name))

	# Ensure proper casing
	doctype = frappe.db.get_value("DocType", {"name": doctype}, "name") or doctype

	links = get_blocking_links(doctype, name)
	
	for link in links:
		try:
			if link.get("is_child"):
				# Low-level delete of child table row
				frappe.db.delete(link["child_doctype"], {"name": link["child_name"]})
				# Clear parent document's cache
				frappe.clear_cache(doctype=link["doctype"])
			else:
				# Clear field in document
				# We use db_set to bypass validation and mandatory checks if doc is submitted
				if link.get("is_single"):
					frappe.db.set_single_value(link["doctype"], link["fieldname"], None)
					if link.get("is_dynamic"):
						frappe.db.set_single_value(link["doctype"], link["doctype_field"], None)
				else:
					frappe.db.set_value(link["doctype"], link["name"], link["fieldname"], None, update_modified=False)
					if link.get("is_dynamic"):
						frappe.db.set_value(link["doctype"], link["name"], link["doctype_field"], None, update_modified=False)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"unlink_and_delete failed for link: {link}")

	frappe.db.commit()
	
	# Delete the target doc
	frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
	return {"success": True}
