import frappe


# The enqueue function now accepts 'project_template' and passes it on.
@frappe.whitelist()
def enqueue_project_creation(opportunity_name, users=None, project_template=None):
	"""
	Called by the client to quickly add the main task to the background queue.
	"""
	if not users:
		frappe.throw("Please select at least one user to notify.")

	if not project_template:
		frappe.throw("Please select a Project Template.")

	frappe.enqueue(
		"erpnext_enhancements.crm_enhancements.api.create_project_from_opportunity_background",
		queue="long",
		timeout=1800,
		opportunity_name=opportunity_name,
		users=users,
		project_template=project_template,  # Pass the template to the background job
	)
	return {"status": "queued"}


def sync_opportunity_tags(doc, method=None):
	"""
	Synchronizes the Opportunity tags with the values in the custom_value_stream child table.
	"""
	# 1. Define the possible value stream options
	value_stream_options = {"Build", "Design", "Rent", "Service"}

	# 2. Get the current selected value streams from the child table
	selected_value_streams = set()
	if doc.get("custom_value_stream"):
		for row in doc.custom_value_stream:
			if row.get("value_stream"):
				selected_value_streams.add(row.value_stream)

	# 3. Get existing tags
	# _user_tags is typically formatted as ",Tag1,Tag2,"
	current_tags_str = doc.get("_user_tags") or ""
	current_tags = {tag.strip() for tag in current_tags_str.split(",") if tag.strip()}

	# 4. Remove existing value stream options from the tags to get "other" tags
	other_tags = current_tags - value_stream_options

	# 5. Combine other tags with the currently selected value streams
	final_tags = other_tags | selected_value_streams

	# 6. Update the _user_tags field
	if final_tags:
		# Format back to ",Tag1,Tag2,"
		doc._user_tags = "," + ",".join(sorted(list(final_tags))) + ","
	else:
		doc._user_tags = None


@frappe.whitelist()
def sync_opportunity_tags_for_existing(opportunity_name):
	"""
	Syncs tags for an existing Opportunity without triggering a full save.
	Called from the client side when opening an existing record missing tags.
	"""
	doc = frappe.get_doc("Opportunity", opportunity_name)
	sync_opportunity_tags(doc)
	frappe.db.set_value("Opportunity", opportunity_name, "_user_tags", doc._user_tags)
	return doc._user_tags


# The background worker now accepts 'project_template' and uses it.
def create_project_from_opportunity_background(opportunity_name, users, project_template):
	"""
	This function does the heavy lifting in the background.
	"""
	project_doc = None
	try:
		original_user = frappe.session.user
		try:
			frappe.set_user("Administrator")
			opp = frappe.get_doc("Opportunity", opportunity_name)
			if opp.custom_created_project:
				return

			project = frappe.new_doc("Project")
			project.status = "Active"

			# Check for a misconfigured Task doctype before applying the template
			# to prevent a ModuleNotFoundError on project.insert()
			if project_template:
				try:
					template = frappe.get_doc("Project Template", project_template)
					if template.tasks:
						# This will fail if the Task doctype's module is incorrect
						frappe.get_meta("Task")

					# If the check passes, assign the template
					project.project_template = project_template
				except ModuleNotFoundError as e:
					if "erpnext_enhancements.task" in str(e):
						frappe.log_error(
							f"Project creation from Opportunity '{opportunity_name}' failed because Project Template "
							f"'{project_template}' references a misconfigured Task doctype. The project will be created "
							"without the template.",
							"CRM Enhancements: Misconfigured Task DocType",
						)
					else:
						# Re-raise exceptions that are not the one we're handling
						raise

			project.project_name = opp.custom_opportunity_name

			# --- All field and table mapping logic remains the same ---
			direct_mappings = {
				"custom_scope_rank": "custom_scope_rank",
				"custom_schedule_rank": "custom_schedule_rank",
				"custom_budget_rank": "custom_budget_rank",
				"custom_opportunity_summary": "custom_project_description",
				"custom_general_scope_description": "custom_general_scope_description",
				"custom_project_start_date": "custom_project_start_date",
				"custom_project_end_date": "custom_project_end_date",
				"custom_notes_for_scheduling": "custom_notes_for_scheduling",
				"custom_delivery_date_time": "custom_delivery_date_time",
				"custom_setup_date_time": "custom_setup_date_time",
				"custom_event_date_time": "custom_event_date_time",
				"custom_take_down_date_time": "custom_take_down_date_time",
				"custom_delivery_date_time_notes": "custom_delivery_date_time_notes",
				"custom_setup_date_time_notes": "custom_setup_date_time_notes",
				"custom_event_date_time_notes": "custom_event_date_time_notes",
				"custom_take_down_date_time_notes": "custom_take_down_date_time_notes",
				"opportunity_amount": "custom_project_dollar_amount",
				"custom_estimated_cost": "custom_project_cost",
				"party_name": "customer",
			}
			for source_field, target_field in direct_mappings.items():
				project.set(target_field, opp.get(source_field))

			priority_order = ["Design", "Build", "Service", "Rent"]
			project_type_value = None

			value_streams = [d.get("value_stream") for d in opp.get("custom_value_stream")]

			for p in priority_order:
				if p in value_streams:
					project_type_value = p
					break

			if project_type_value:
				project.project_type = project_type_value

			child_table_mappings = {
				"custom_value_stream": "custom_value_stream",
				"custom_contacts__address_table": "custom_contacts__address_table",
				"custom_scope_contributors": "custom_scope_contributors",
				"custom_design_customer_requests": "custom_design_customer_requests",
				"custom_design_deliverables": "custom_design_deliverables",
				"custom_build_customer_requests": "custom_build_customer_requests",
				"custom_build_deliverables": "custom_build_deliverables",
				"custom_service_customer_requests": "custom_service_customer_requests",
				"custom_service_deliverables": "custom_service_deliverables",
				"custom_rent_customer_requests": "custom_rent_customer_requests",
				"custom_rent_deliverables": "custom_rent_deliverables",
			}
			for source_table, target_table in child_table_mappings.items():
				project.set(target_table, [])
				for source_row in opp.get(source_table):
					new_row = project.append(target_table, {})
					new_row.update(source_row.as_dict())

			# Map Opportunity notes to Project comments
			notes_html_parts = []
			if opp.get("notes"):
				has_comments_field = project.meta.get_field("custom_opportunity_comments")
				if has_comments_field:
					project.set("custom_opportunity_comments", [])

				for note_row in opp.get("notes"):
					# Add to Project Comments child table
					if has_comments_field:
						new_comment = project.append("custom_opportunity_comments", {})
						new_comment.notes = note_row.note
						new_comment.added_by = note_row.added_by
						new_comment.added_on = note_row.added_on

					# Build HTML for custom_opportunity_notes
					notes_html_parts.append(
						f"""<div style="margin-bottom: 10px; padding: 10px; border: 1px solid #d1d8dd; border-radius: 4px; background-color: #f9f9f9;">
							<div style="margin-bottom: 5px;">
								<strong>{frappe.utils.escape_html(str(note_row.added_by))}</strong>
								<span style="color: #6c757d; font-size: 0.9em; margin-left: 5px;">on {frappe.utils.escape_html(str(note_row.added_on))}</span>
							</div>
							<div>{frappe.utils.escape_html(str(note_row.note))}</div>
						</div>"""
					)

			project.custom_opportunity_notes = "".join(notes_html_parts)

			# Bypass validation to avoid "Status cannot be Open" error if Workflow forces invalid status
			project.flags.ignore_validate = True
			project.insert(ignore_permissions=True)

			# Ensure status is Active even if Workflow overwrote it
			if project.status != "Active":
				project.db_set("status", "Active")
				project.status = "Active"

			attachments = frappe.get_all(
				"File",
				filters={"attached_to_doctype": "Opportunity", "attached_to_name": opportunity_name},
				fields=["file_name", "file_url", "is_private", "folder"],
			)
			for attachment in attachments:
				file_doc = frappe.new_doc("File")
				file_doc.file_name = attachment.file_name
				file_doc.file_url = attachment.file_url
				file_doc.is_private = attachment.is_private
				file_doc.attached_to_doctype = "Project"
				file_doc.attached_to_name = project.name
				if attachment.folder:
					file_doc.folder = attachment.folder
				file_doc.insert(ignore_permissions=True)

			project_doc = project.as_dict()
			opp.custom_created_project = project.name
			opp.save(ignore_permissions=True)
			frappe.db.commit()

			drive_success = False
			drive_error_details = None

			# Provision Google Drive Folders
			if not project.get("custom_drive_folder_id"):
				try:
					from erpnext_enhancements.crm_enhancements.drive_utils import provision_project_folders

					project_folder_name = f"{project.name} {project.project_name}"
					party_name = project.customer or opp.party_name or "Unknown Customer"

					folder_id, web_view_link = provision_project_folders(project_folder_name, party_name)

					# Update Project
					project.db_set("custom_drive_folder_id", folder_id)
					project_doc["custom_drive_folder_id"] = folder_id

					# Attach link
					drive_link_doc = frappe.new_doc("File")
					drive_link_doc.file_url = web_view_link
					drive_link_doc.attached_to_doctype = "Project"
					drive_link_doc.attached_to_name = project.name
					drive_link_doc.is_private = 0
					drive_link_doc.insert(ignore_permissions=True)

					frappe.db.commit()
					drive_success = True
				except Exception:
					drive_error_details = frappe.get_traceback()
					frappe.log_error(drive_error_details, "[Google Drive Integration] Folder Creation Failed")

		finally:
			frappe.set_user(original_user)

	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM Enhancements App Background Job Failed")

	# The real-time broadcast logic remains the same.
	if isinstance(users, str):
		users = users.split(",")

	for user in users:
		message_payload = {
			"status": "success" if project_doc else "failed",
			"project_doc": project_doc,
			"opportunity_name": opportunity_name,
		}

		if project_doc:
			message_payload["drive_success"] = drive_success
			if drive_error_details:
				message_payload["drive_error"] = str(drive_error_details)

		frappe.publish_realtime(
			event="project_creation_status",
			message=message_payload,
			user=user,
		)

		if project_doc:
			subject = f"New Project Created: {project_doc.get('project_name')}"
			message = f"""
				<p>A new project has been created from Opportunity <b>{opportunity_name}</b>.</p>
				<p><a href="{frappe.utils.get_url_to_form('Project', project_doc.get('name'))}">Click here to view the project</a></p>
			"""
			frappe.sendmail(recipients=[user], subject=subject, message=message)
