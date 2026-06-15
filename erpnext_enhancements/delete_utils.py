"""Force-delete helper: enumerate and clear links blocking a deletion.

Frappe refuses to delete a document still referenced by other documents
(``LinkExistsError``). This module backs a UI flow that lets a user see exactly
what is blocking the delete and then unlink-and-delete in one shot:

* :func:`get_blocking_links` walks both standard Link fields and Dynamic Links
  (single, normal, and child-table parents) pointing at the target, skipping
  cancelled docs, self-references, and doctypes in the ``ignore_links_on_delete``
  hook, and returns a structured list the frontend renders.
* :func:`unlink_and_delete` clears each of those references low-level (bypassing
  validation so it works on submitted docs) and then force-deletes the target.

Companion to ``utils/patch_delete.py``, which monkey-patches Frappe's delete
endpoints to surface ``LinkExistsError`` as a signal that triggers this flow.
"""
import frappe
from frappe import _
from frappe.model.docstatus import DocStatus
from frappe.model.dynamic_links import get_dynamic_link_map
from frappe.model.rename_doc import get_link_fields


def _resolve_doctype(doctype: str) -> str:
	"""Map a doctype identifier to its real DocType name.

	The "Unlink and Delete" frontend reads the doctype out of a desk URL, so it
	arrives as the route *slug* — lowercased with spaces turned into hyphens
	(e.g. "sapphire-maintenance-contract" for "Sapphire Maintenance Contract").
	A bare DocType lookup only corrects casing (the name column collates
	case-insensitively, which is why "task" already resolved to "Task"); it does
	*not* undo the space→hyphen swap, so ``frappe.get_doc`` then failed to import
	the controller (``No module named '…sapphire_maintenance_contract'``). Try an
	exact (case-insensitive) match first — which also covers doctypes whose real
	name legitimately contains a hyphen — then retry with hyphens turned back
	into spaces. Fall back to the input unchanged (e.g. a virtual doctype not in
	the table)."""
	for candidate in (doctype, doctype.replace("-", " ")):
		real = frappe.db.get_value("DocType", {"name": candidate}, "name")
		if real:
			return real
	return doctype


@frappe.whitelist()
def get_blocking_links(doctype, name):
	"""
	Returns a detailed list of documents blocking the deletion of the target document.
	"""
	# The frontend extracts the doctype from a desk URL, so it arrives as the
	# route slug (e.g. 'sapphire-maintenance-contract'). Resolve it to the real
	# DocType name before loading anything (see _resolve_doctype).
	doctype = _resolve_doctype(doctype)

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
	"""Clear every reference returned by :func:`get_blocking_links`, then delete.

	Requires delete permission on the target. Child-table references are removed
	row-by-row; scalar/dynamic links are nulled via ``db.set_value`` /
	``set_single_value`` (with ``update_modified=False``) to bypass mandatory and
	validation checks on submitted documents. Per-link failures are logged but do
	not abort the others. Finally force-deletes the target with permissions
	ignored. Returns ``{"success": True}``.
	"""
	# Resolve the route slug / casing to the real DocType name *before* the
	# permission check and delete — the frontend passes the desk URL slug (see
	# _resolve_doctype), so checking permission or loading the doc against the
	# raw slug would fail to resolve the doctype and abort the whole flow.
	doctype = _resolve_doctype(doctype)

	if not frappe.has_permission(doctype, "delete", name):
		frappe.throw(_("You do not have permission to delete {0} {1}").format(doctype, name))

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
