import frappe
from frappe import _


@frappe.whitelist()
def merge_projects(source_project, target_project):
	"""
	Merge source_project into target_project.
	1. Validate projects.
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

	# Dynamic discovery of linked doctypes
	linked_doctypes = get_linked_doctypes("Project")

	updated_count = 0

	for doctype, fields in linked_doctypes.items():
		for field in fields:
			# Find documents where the link field is the source project
			# We avoid updating the Project doctype itself (except maybe parent project?)
			# But Project linking to Project is usually 'parent_project' or similar.
			# We should be careful not to merge a project into its own child if that creates a loop,
			# but for now we just assume standard linking.

			if doctype == "Project" and field == "name":
				continue

			print(f"DEBUG: Checking {doctype}.{field} for {source_project}")
			docs_to_update = frappe.db.get_all(doctype, filters={field: source_project}, pluck="name")

			if docs_to_update:
				print(f"DEBUG: Found {len(docs_to_update)} docs in {doctype}")
				for doc_name in docs_to_update:
					try:
						print(f"DEBUG: Updating {doctype} {doc_name}")
						frappe.db.set_value(doctype, doc_name, field, target_project)
						msg = _("Merged from Project {0}").format(source_project)
						frappe.msgprint(_("Updated {0} {1}: {2}").format(doctype, doc_name, msg))

						# Add a comment if possible
						if frappe.get_meta(doctype).issingle == 0:
							frappe.get_doc(doctype, doc_name).add_comment("Info", msg)

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
