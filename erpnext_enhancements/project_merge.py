import frappe
from frappe import _


@frappe.whitelist()
def get_merge_stats(source_project, target_project):
	"""
	Returns a summary of documents that will be impacted by the merge.
	Structure: {
		"Task": { "count": 5, "items": ["TASK-001", "TASK-002", ...] },
		"Material Request": { "count": 2, "items": ["MR-001", "MR-002"] },
		...
	}
	"""
	# Basic validation (same as merge_projects)
	if not source_project or not target_project:
		frappe.throw(_("Source and Target Project are required."))

	if source_project == target_project:
		frappe.throw(_("Source and Target Project cannot be the same."))

	if not frappe.db.exists("Project", source_project):
		frappe.throw(_("Source Project {0} does not exist.").format(source_project))

	impact_map = get_impacted_documents(source_project)

	# Clean up for UI consumption (remove empty doctypes)
	result = {}
	for doctype, items in impact_map.items():
		if items:
			result[doctype] = {
				"count": len(items),
				"items": items
			}

	return result


def get_impacted_documents(source_project):
	"""
	Finds all documents linked to the source_project.
	Returns a dict: { DocType: [doc_name, ...] }
	"""
	linked_doctypes = get_linked_doctypes("Project")
	impact_map = {}

	for doctype, fields in linked_doctypes.items():
		impact_map[doctype] = []

		is_single = frappe.get_meta(doctype).issingle

		for field in fields:
			if doctype == "Project" and field == "name":
				continue

			if is_single:
				# For Single DocTypes, check if the value matches
				val = frappe.db.get_single_value(doctype, field)
				if val == source_project:
					impact_map[doctype].append(doctype)
			else:
				# Find documents where the link field is the source project
				docs = frappe.db.get_all(doctype, filters={field: source_project}, pluck="name")

				if docs:
					# Use set to avoid duplicates if multiple fields link to Project in same doc
					# (Unlikely but possible)
					impact_map[doctype].extend(docs)

		# Deduplicate
		if impact_map[doctype]:
			impact_map[doctype] = list(set(impact_map[doctype]))
		else:
			del impact_map[doctype]

	return impact_map


@frappe.whitelist()
def merge_projects(source_project, target_project):
	"""
	Merge source_project into target_project.
	1. Validate projects and permissions.
	2. Find all documents linked to source_project.
	3. Update links to target_project.
	4. Cancel source_project.
	"""
	if not source_project or not target_project:
		frappe.throw(_("Source and Target Project are required."))

	if source_project == target_project:
		frappe.throw(_("Source and Target Project cannot be the same."))

	if not frappe.db.exists("Project", source_project):
		frappe.throw(_("Source Project {0} does not exist.").format(source_project))

	if not frappe.db.exists("Project", target_project):
		frappe.throw(_("Target Project {0} does not exist.").format(target_project))

	# Permission Check
	if not frappe.has_permission("Project", "write", doc=source_project):
		frappe.throw(_("You do not have permission to modify the Source Project."))
	if not frappe.has_permission("Project", "write", doc=target_project):
		frappe.throw(_("You do not have permission to modify the Target Project."))

	# Dynamic discovery of linked doctypes via helper
	linked_doctypes = get_linked_doctypes("Project")

	updated_count = 0

	# We iterate again here instead of using get_impacted_documents because
	# we need to know WHICH field to update, and get_impacted_documents just gives names.
	# Although we could refactor get_impacted_documents to return {doctype: {docname: [field, ...]}}
	# keeping the logic separated for "Stats" (read-only) vs "Merge" (write) is safer for now.

	for doctype, fields in linked_doctypes.items():
		meta = frappe.get_meta(doctype)
		is_table = meta.istable
		is_single = meta.issingle

		for field in fields:
			if doctype == "Project" and field == "name":
				continue

			if is_single:
				# For Single DocTypes, check and update if necessary
				try:
					val = frappe.db.get_single_value(doctype, field)
					if val == source_project:
						frappe.db.set_single_value(doctype, field, target_project)
						updated_count += 1
				except Exception as e:
					frappe.log_error(
						f"Failed to update Single DocType {doctype}: {e!s}", "Project Merge Error"
					)
				continue

			docs_to_update = frappe.db.get_all(doctype, filters={field: source_project}, pluck="name")

			if docs_to_update:
				for doc_name in docs_to_update:
					try:
						if is_table:
							# High volume child tables: Use set_value for speed
							frappe.db.set_value(doctype, doc_name, field, target_project)
						else:
							# Parent DocTypes: Use doc.save() to trigger logic/dashboards
							doc_to_update = frappe.get_doc(doctype, doc_name)
							doc_to_update.set(field, target_project)
							doc_to_update.save()

							# Add a comment if possible
							msg = _("Merged from Project {0}").format(source_project)
							if not is_table and not is_single:
								doc_to_update.add_comment("Info", msg)

						updated_count += 1
					except Exception as e:
						frappe.log_error(
							f"Failed to update {doctype} {doc_name}: {e!s}", "Project Merge Error"
						)

	# Cancel the source project
	source_doc = frappe.get_doc("Project", source_project)
	if source_doc.status != "Cancelled":
		source_doc.status = "Cancelled"
		source_doc.save(ignore_permissions=True)
		source_doc.add_comment("Info", _("Project merged into {0}").format(target_project))

	return _("Successfully merged {0} documents from {1} to {2}.").format(
		updated_count, source_project, target_project
	)


def get_linked_doctypes(doctype_name):
	"""
	Returns a dictionary of {DocType: [fieldname, ...]} that link to doctype_name.
	"""
	# Get all fields of type Link with options=doctype_name
	link_fields = frappe.get_all(
		"DocField", filters={"fieldtype": "Link", "options": doctype_name}, fields=["parent", "fieldname"]
	)

	# Also check Custom Fields
	custom_link_fields = frappe.get_all(
		"Custom Field",
		filters={"fieldtype": "Link", "options": doctype_name},
		fields=["dt as parent", "fieldname"],
	)

	links = {}

	for item in link_fields + custom_link_fields:
		if item.parent not in links:
			links[item.parent] = []
		links[item.parent].append(item.fieldname)

	return links
