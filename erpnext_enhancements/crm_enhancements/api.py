"""Server-side API for the CRM Enhancements module (Opportunity customizations).

This module backs the custom Opportunity workflow used by Sapphire Fountains:

* ``sync_opportunity_tags`` is wired in ``hooks.py`` under
  ``doc_events["Opportunity"]["before_save"]`` and keeps the Opportunity's
  ``_user_tags`` in sync with the ``custom_value_stream`` child table.
* The "convert Opportunity to Project" flow: the client calls
  :func:`enqueue_project_creation` (whitelisted), which enqueues
  :func:`create_project_from_opportunity_background` on the ``long`` queue. That
  background worker creates a Project from the Opportunity, copies its
  attachments, and (via :mod:`erpnext_enhancements.google_drive.drive_utils`)
  provisions a Google Drive folder tree, then notifies the requesting users in
  real time and by email. Since v1.749.0 it reports each step as it starts
  (:mod:`~erpnext_enhancements.crm_enhancements.project_creation_progress`), so
  the dialog shows a progress bar and the page refreshes when it is done.
"""

import frappe

from erpnext_enhancements import email_style
from erpnext_enhancements import sync_contact
from erpnext_enhancements.crm_enhancements import project_creation_progress as progress
from erpnext_enhancements.crm_enhancements.project_copy import copy_site_and_budget


def _notify_recipients(users):
	"""Normalize the dialog's ``users`` payload into a clean list of user IDs.

	The "Users to Notify" MultiSelect submits a comma-separated string and leaves a
	trailing separator behind (``"a@x.com, b@y.com, "``), so a bare ``split(",")``
	yields an empty final element. That empty string was reaching
	:func:`frappe.sendmail` as a recipient and Gmail refused the whole message
	(``555 5.5.2 Syntax error``) — one dead Email Queue row and one Error Log for
	every conversion from 2026-07-24 on, while the real recipients still got theirs.

	Accepts a comma-separated string or a list; blank and whitespace-only entries
	are dropped.
	"""
	if isinstance(users, str):
		users = users.split(",")
	return [str(user).strip() for user in (users or []) if str(user or "").strip()]


# The enqueue function now accepts 'project_template' and passes it on.
@frappe.whitelist()
def enqueue_project_creation(opportunity_name, users=None, project_template=None):
	"""Queue background creation of a Project from an Opportunity.

	Whitelisted entry point called from the Opportunity form (client side) so the
	UI returns immediately while the heavy work runs on the ``long`` worker queue.

	Args:
		opportunity_name: Name (ID) of the source Opportunity.
		users: Comma-separated string / list of users to notify when the job
			finishes (realtime + email). Required. Normalized by
			:func:`_notify_recipients`, so a trailing separator is harmless.
		project_template: Name of the Project Template to apply to the new
			Project. Required.

	Returns:
		dict: ``{"status": "queued", "phase", "state", "steps"}`` once the job is
		enqueued; ``{"status": "running", ...}`` with the same keys when a job
		for this Opportunity is already queued or running (nothing new is
		queued); ``{"status": "exists", "project"}`` when it already has its
		Project. The client draws its progress bar from ``steps``.

	Raises:
		frappe.ValidationError: via ``frappe.throw`` if ``users`` is missing,
			resolves to nobody, or names something that is not an email address,
			or if ``project_template`` is missing.

	Side effects:
		Enqueues :func:`create_project_from_opportunity_background` (queue
		``long``, 1800s timeout) under one job id per Opportunity, deduplicated,
		and records the queued state for the progress dialog.

	Raises:
		frappe.PermissionError: when the caller may not write the Opportunity.
	"""
	# Write access to the deal, first (v1.749.0 review). The job runs as Administrator: it
	# makes a Project, saves the Opportunity, and can link an existing Project to it. Until
	# now any desk user could start that for any Opportunity, and read back its creation
	# state (the requester, the Error Log's name). project_creation_state's read check is
	# the same rule's other half.
	frappe.has_permission("Opportunity", "write", doc=opportunity_name, throw=True)

	# Normalized here so a field holding only separators ("," / " ") is refused up
	# front rather than queueing a job that notifies nobody.
	users = _notify_recipients(users)
	if not users:
		frappe.throw("Please select at least one user to notify.")

	# The dialog's field takes free text since v1.529.0 (its default is group
	# inboxes, which are not Users), so a typo can get this far. frappe.sendmail
	# would refuse it too, but only after the project exists, as an "Invalid email
	# address" Error Log, and that recipient simply gets nothing. Refused here while
	# it can still be fixed. This is the authority: the dialog's own check uses
	# the looser browser regex, so a few odd shapes are only caught here.
	invalid = [user for user in users if not frappe.utils.validate_email_address(user)]
	if invalid:
		frappe.throw(f"Not a valid email address: {frappe.utils.escape_html(', '.join(invalid))}")

	if not project_template:
		frappe.throw("Please select a Project Template.")

	# v1.749.0. Already made: say so rather than queue a job that would only find it.
	project = frappe.db.get_value("Opportunity", opportunity_name, "custom_created_project")
	if project:
		return {"status": "exists", "project": project}

	# Already on its way (a double click, a second tab, or a colleague on the same deal): that
	# creation is the one to watch. Asked before mark_queued, which would otherwise overwrite the
	# running job's progress with "queued".
	current = progress.creation_state(opportunity_name)
	if current["phase"] in ("queued", "running"):
		return {"status": "running", **current}

	state = progress.mark_queued(opportunity_name, frappe.session.user)
	# One job id per Opportunity, deduplicated: frappe.enqueue queues nothing while a job with this
	# id is queued or started. The job's own row lock covers the rest (a job queued some other way).
	job = frappe.enqueue(
		"erpnext_enhancements.crm_enhancements.api.create_project_from_opportunity_background",
		queue="long",
		timeout=1800,
		job_id=progress.job_id(opportunity_name),
		deduplicate=True,
		opportunity_name=opportunity_name,
		users=users,
		project_template=project_template,  # Pass the template to the background job
	)
	if job is None:
		# Deduplicated after all: another request queued this Opportunity's job between the check
		# above and here. Report that job as it stands rather than a "queued" this call never made.
		return {"status": "running", **progress.creation_state(opportunity_name)}
	return {"status": "queued", "phase": "queued", "state": state, "steps": progress.steps()}


def sync_opportunity_tags(doc, method=None):
	"""Synchronize Opportunity ``_user_tags`` with the ``custom_value_stream`` table.

	Wired in ``hooks.py`` as an Opportunity ``before_save`` doc_event. Mutates
	``doc`` in place (no DB write of its own) so the tag set always reflects the
	selected value streams (Build/Design/Events/Service) while preserving any other
	user tags.

	Args:
		doc: The Opportunity document being saved.
		method: Frappe doc-event hook name (unused).

	Side effects:
		Sets ``doc._user_tags`` to a ``",Tag1,Tag2,"`` formatted string (or
		``None`` when empty).
	"""
	# 1. Define the possible value stream options
	value_stream_options = {"Build", "Design", "Events", "Service", "Delivery", "Products"}

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
	"""Sync tags for an existing Opportunity without triggering a full save.

	Whitelisted helper called from the client when opening a record whose tags
	are missing/stale. Recomputes tags via :func:`sync_opportunity_tags` and
	writes only ``_user_tags`` back via ``frappe.db.set_value`` (no full save,
	so other ``before_save`` hooks do not re-run).

	Args:
		opportunity_name: Name (ID) of the Opportunity to refresh.

	Returns:
		str | None: The recomputed ``_user_tags`` value.

	Side effects:
		DB write to ``Opportunity._user_tags``.
	"""
	doc = frappe.get_doc("Opportunity", opportunity_name)
	sync_opportunity_tags(doc)
	frappe.db.set_value("Opportunity", opportunity_name, "_user_tags", doc._user_tags)
	return doc._user_tags


class _HandoffBlocked(Exception):
	"""The hand-off gate refused this project creation.

	Private and deliberately not a ``frappe.ValidationError``: it is control
	flow inside the background job, never something a caller should catch
	generically or a user should see as a traceback.
	"""


#: A Project in either of these is not an Opportunity's Project to link: "Canceled" is this site's
#: spelling (a Property Setter), "Cancelled" ERPNext's own.
CANCELLED_PROJECT = ["Cancelled", "Canceled"]


class _ProjectExists(Exception):
	"""The Opportunity already has its Project, so this job makes none (v1.749.0).

	Control flow like :class:`_HandoffBlocked`: it unwinds to the handler that
	still reports back, because the person watching the progress bar has to be
	told how it ended. Carries the Project's name.
	"""


def _failure_reason(exc, step):
	"""What the progress dialog says went wrong, in words a person can act on.

	A ``frappe.throw`` message is written for people, so it is passed on as it
	is. Anything else is only named by the step it stopped at: its text is a
	traceback's last line, and the Error Log (linked beside it) has the rest.
	"""
	if isinstance(exc, frappe.ValidationError):
		message = frappe.utils.strip_html_tags(str(exc) or "").strip()
		if message:
			return message
	label = dict(progress.STEPS).get(step, step)
	return frappe._("It stopped at: {0}.").format(frappe._(label))


def _send_closed_won_alerts(opportunity_name):
	"""Queue the Closed-Won team SMS (PRO-0204 Step 1) for an Opportunity that now has its Project.

	The alert is coupled to the Project actually existing, so it fires from this job rather than
	on the won-save, and this job is its only sender. enqueue_after_commit=False: the Opportunity
	is already committed, so the job reads a consistent row even if the Drive step after it fails.
	"""
	frappe.enqueue(
		"erpnext_enhancements.status_alerts.deliver_closed_won_alerts",
		opportunity=opportunity_name,
		queue="short",
		enqueue_after_commit=False,
	)


def _provision_project_drive(project, opp):
	"""Give ``project`` its Google Drive folder: ``(drive_success, error_details, folder_id)``.

	Shared by a Project this job made and one it linked (v1.749.0). Never raises: a Drive failure
	is logged and reported, and never undoes the Project, which is committed before this runs.
	"""
	try:
		from erpnext_enhancements.google_drive.drive_utils import (
			provision_project_folder_for_opportunity,
		)

		# Project folder name "<Project ID> - <Opportunity Name>", e.g.
		# "PRJ-00123 - Smith Residence" (project_name was set above from the
		# Opportunity's custom_opportunity_name). .strip(" -") leaves just the
		# ID if the name is blank.
		project_folder_name = f"{project.name} - {project.project_name}".strip(" -")
		party_name = project.customer or opp.party_name or "Unknown Customer"

		# Reuse the Opportunity's Drive folder when it has one: rename it in
		# place (its files come along) and add the project subfolders; else
		# create a fresh project folder tree under the customer.
		folder_id, web_view_link = provision_project_folder_for_opportunity(
			opp.get("custom_drive_folder_id"),
			project_folder_name,
			party_name,
			project_type=project.get("project_type"),
		)

		# Update Project
		project.db_set("custom_drive_folder_id", folder_id)

		# Attach link
		drive_link_doc = frappe.new_doc("File")
		drive_link_doc.file_url = web_view_link
		drive_link_doc.attached_to_doctype = "Project"
		drive_link_doc.attached_to_name = project.name
		drive_link_doc.is_private = 0
		drive_link_doc.insert(ignore_permissions=True)

		# Audit trail (the opportunity/customer provisioning logs too).
		from erpnext_enhancements.google_drive.drive_sync import log_sync

		log_sync(
			"Provision Folder", "Success",
			reference_doctype="Project", reference_name=project.name,
			file_name=project_folder_name, drive_file_id=folder_id,
		)

		frappe.db.commit()
		return True, None, folder_id
	except Exception:
		details = frappe.get_traceback()
		frappe.log_error(title="[Google Drive Integration] Folder Creation Failed", message=details)
		return False, details, None


# The background worker now accepts 'project_template' and uses it.
def create_project_from_opportunity_background(opportunity_name, users, project_template):
	"""Create a Project from an Opportunity (runs on the ``long`` worker queue).

	Enqueued by :func:`enqueue_project_creation`. Runs the whole flow as
	Administrator (restoring the original session user in a ``finally`` block)
	and is idempotent, so a creation a deploy interrupted can simply be started
	again (v1.749.0):

	* it locks the Opportunity's row (``SELECT ... FOR UPDATE``) before anything
	  else, so a second job waits for the first to commit and then finds its
	  Project;
	* it makes nothing when the Opportunity already has
	  ``custom_created_project``, and reports ``exists``;
	* when a Project already points back at the Opportunity
	  (``Project.custom_opportunity``, set by the Desk's *Create > Project* and
	  by the Closed Won Reconciliation report, neither of which fills in
	  ``custom_created_project``) it links that one and makes no second. Not a
	  cancelled one, which is passed over. The linked Project gets what a made
	  one gets after the link: the team SMS and, if it has none, its Drive
	  folder. It gets no "New Project Created" email;
	* a failure before the link is committed rolls back, so a retry never finds
	  half a Project. Until v1.749.0 the exception was caught and the job
	  returned normally, and the worker's closing commit kept whatever had been
	  inserted.

	Each step below reports itself as it starts
	(:func:`~erpnext_enhancements.crm_enhancements.project_creation_progress.mark_step`),
	to the person who clicked only.

	Steps:
		1. Create a new Project, optionally applying ``project_template`` (with a
		   guard that skips the template if the Task doctype's module is
		   misconfigured, logging instead of crashing).
		2. Copy direct field mappings (including ``primary_contact`` — the deal's
		   contact follows it into delivery, with the read-through phone / email /
		   job title re-derived from the Contact by
		   :func:`~erpnext_enhancements.sync_contact.apply_primary_contact_details`),
		   child tables, value-stream-derived ``project_type``, and Opportunity
		   notes (both as a Project Comments child table and as rendered HTML in
		   ``custom_opportunity_notes``).
		3. Insert with ``ignore_validate`` to dodge Workflow "Status cannot be
		   Open" errors, then force ``status = "Active"`` if a Workflow overrode it.
		4. Re-attach the Opportunity's File attachments to the Project.
		5. Stamp ``Opportunity.custom_created_project`` and commit.
		6. Provision the Google Drive folder via
		   :func:`~erpnext_enhancements.google_drive.drive_utils.provision_project_folder_for_opportunity`
		   — renaming the source Opportunity's existing folder in place to
		   ``<Project ID> - <Name>`` (so its files carry over) when it has one, else
		   creating a fresh project folder tree — store the returned folder id on
		   ``Project.custom_drive_folder_id`` and attach the web view link as a File.
		   Drive failures are caught/logged so they never abort Project creation.

	Args:
		opportunity_name: Name (ID) of the source Opportunity.
		users: Users to notify (comma-separated string or list).
		project_template: Project Template name to apply (may be falsy).

	Returns:
		None. Results are delivered via realtime event and email (see below).

	Side effects:
		Inserts a Project, File records, optional ToDo-like comments; writes back
		to the Opportunity; calls the Google Drive API; commits the DB; publishes a
		``project_creation_status`` realtime event and sends a notification email
		to each user. All exceptions are logged via ``frappe.log_error`` rather
		than raised.
	"""
	project_doc = None
	blocked_reason = None
	# Initialised here, not where the Drive block first assigns them: project_doc is
	# set before that block, so a failure in between (opp.save, the commit, the
	# alert enqueue) reached the broadcast with these unbound, and the
	# UnboundLocalError killed the job before anyone heard the project existed.
	drive_success = False
	drive_error_details = None
	# v1.749.0: what the progress dialog is told. The job was queued as the person who clicked,
	# so this is them; the steps run as Administrator below.
	requester = frappe.session.user
	step = "check"
	existing_project = None
	adopted = False
	failure_reason = None
	error_log = None
	# Set once the Project and the Opportunity's link to it are committed together. Before that, a
	# failure rolls back; after it, the Project exists whatever goes wrong next.
	committed = False
	try:
		original_user = frappe.session.user
		try:
			frappe.set_user("Administrator")
			progress.mark_step(opportunity_name, requester, step)
			# The row lock comes first, so a second job for this Opportunity waits here for the
			# first to commit and then finds its Project, instead of making another.
			# The value the locked read returns is the one acted on: under REPEATABLE READ an
			# earlier plain read in this transaction would pin an older snapshot.
			linked = frappe.db.get_value(
				"Opportunity", opportunity_name, "custom_created_project", for_update=True
			)
			opp = frappe.get_doc("Opportunity", opportunity_name)
			if linked:
				raise _ProjectExists(linked)

			# A Project that already points back at this Opportunity is its Project: the Desk's
			# Create > Project and the reconciliation report set that back-link without filling in
			# custom_created_project. Linked, not duplicated; two Projects claiming one Opportunity
			# double-count its revenue (reconciliation.py refuses the same thing). A cancelled one
			# is not its Project (a PM retired it), so it is passed over and a new one is made.
			#
			# A plain read, deliberately NOT for_update. Project.custom_opportunity has no index,
			# so a locking read scans tabProject, and under REPEATABLE READ InnoDB locks every row
			# it scans and the gaps between them: in the usual case (no back-link) the whole table,
			# held until the link commits, through the template's task creation. Every Project
			# save and insert on the site would wait on it, past the 50 s lock timeout for a long
			# template (v1.749.0 review). The Opportunity's row lock above already serializes this
			# job; the one Project found is then locked by name, its primary key, which locks that
			# row alone.
			back_linked = None
			if frappe.db.has_column("Project", "custom_opportunity"):
				back_linked = frappe.db.get_value(
					"Project",
					{"custom_opportunity": opportunity_name, "status": ["not in", CANCELLED_PROJECT]},
					"name",
					order_by="creation asc",
				)
			if back_linked:
				frappe.db.get_value("Project", back_linked, "name", for_update=True)
				step = "link"
				progress.mark_step(opportunity_name, requester, step)
				opp.custom_created_project = back_linked
				opp.save(ignore_permissions=True)
				frappe.db.commit()
				committed = True
				adopted = True
				existing_project = back_linked
				# The team SMS goes out, as for a Project made here: this job is its only sender,
				# and an Opportunity with no custom_created_project never had a job finish for it,
				# so the team has not heard. No "New Project Created" email: nothing is new.
				_send_closed_won_alerts(opportunity_name)
				# Nor is it left without a Drive folder when it never had one.
				step = "drive"
				progress.mark_step(opportunity_name, requester, step)
				project = frappe.get_doc("Project", back_linked)
				if project.get("custom_drive_folder_id"):
					drive_success = True
				else:
					drive_success, drive_error_details, _folder = _provision_project_drive(project, opp)
				raise _ProjectExists(back_linked)

			# Hand-off gate (PRO-0204, 2026-08-06). The authoritative check is
			# Project before_insert, which this path would hit anyway — but that
			# would surface as a worker traceback nobody reads. Refuse here so the
			# reason travels back through the realtime status this job already
			# publishes, and the user sees what to do instead of a silent failure.
			#
			# Note this job runs as Administrator, so it cannot rely on any
			# permission-shaped protection: the gate has to be an explicit check.
			from erpnext_enhancements.crm_enhancements.handoff import handoff_block_reason

			blocked_reason = handoff_block_reason(opportunity_name)
			if blocked_reason:
				frappe.log_error(
					title="Hand-Off Gate: project creation blocked",
					message=f"Project creation from Opportunity '{opportunity_name}' refused: {blocked_reason}",
				)
				# Raised rather than returned: a bare `return` here would exit the
				# whole function and skip the realtime broadcast below, leaving the
				# user staring at a spinner that never resolves. This unwinds to the
				# handler that lets the broadcast run.
				raise _HandoffBlocked(blocked_reason)

			step = "project"
			progress.mark_step(opportunity_name, requester, step)
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
							title="CRM Enhancements: Misconfigured Task DocType",
							message=f"Project creation from Opportunity '{opportunity_name}' failed because Project Template "
							f"'{project_template}' references a misconfigured Task doctype. The project will be created "
							"without the template.",
						)
					else:
						# Re-raise exceptions that are not the one we're handling
						raise

			project.project_name = opp.custom_opportunity_name
			# Persisted forward link to the source Opportunity: keys the PRO-0204
			# hand-off seeding (process_steps), AE resolution for payment/step
			# alerts, and the after_save attachment sync.
			project.custom_opportunity = opportunity_name

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
				# Who the deal was actually with. Same fieldname on both sides
				# (setup/custom_fields.py provisions it for every party doctype),
				# so this is a straight copy.
				"primary_contact": "primary_contact",
				# The step-4 invoice terms, agreed on the deal and used by the
				# hand-off note to Billing (crm_enhancements.handoff.billing_terms).
				# Copied rather than fetched so the PM can revise them on the
				# project without rewriting a closed opportunity; blank here still
				# falls back to the Opportunity at send time.
				"custom_first_invoice_percentage": "custom_first_invoice_percentage",
				"custom_billing_email": "custom_billing_email",
			}
			for source_field, target_field in direct_mappings.items():
				project.set(target_field, opp.get(source_field))
			# The job site, the billing address and the budget (crm_enhancements/project_copy.py),
			# shared with the Desk's "Create > Project" so the two cannot drift.
			copy_site_and_budget(opp, project)

			# The three read-through fields beside primary_contact are re-derived from
			# the Contact rather than copied across from the Opportunity. Project's
			# on_update pushes them straight back DOWN onto that same Contact
			# (sync_contact.sync_from_main_doc), so carrying over an Opportunity whose
			# copies were never filled would blank the Contact's own job title.
			sync_contact.apply_primary_contact_details(project)

			# Derive a single Project.project_type from the (possibly multiple)
			# selected value streams, picking the highest-priority one present.
			priority_order = ["Design", "Build", "Service", "Events"]
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
			# The row's own identity and audit stamps stay behind. Frappe keeps a child
			# row's creation/owner when they are already set, so a straight as_dict() copy
			# made every stakeholder row on a new Project look older than the Project and
			# authored by whoever filled in the Opportunity (20 of 26 copied rows on prod,
			# 2026-10-06), which misdirects anyone tracing who put a party on a job.
			row_identity = {"name", "parent", "parenttype", "parentfield", "idx", "docstatus", "doctype",
				"creation", "owner", "modified", "modified_by"}
			for source_table, target_table in child_table_mappings.items():
				project.set(target_table, [])
				for source_row in opp.get(source_table):
					new_row = project.append(target_table, {})
					new_row.update({k: v for k, v in source_row.as_dict().items() if k not in row_identity})

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
						f"""<div style="margin-bottom: 10px; padding: 10px; border: 1px solid var(--border-color); border-radius: 4px; background-color: var(--control-bg);">
							<div style="margin-bottom: 5px;">
								<strong>{frappe.utils.escape_html(str(note_row.added_by))}</strong>
								<span style="color: var(--text-muted); font-size: 0.9em; margin-left: 5px;">on {frappe.utils.escape_html(str(note_row.added_on))}</span>
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

			step = "attachments"
			progress.mark_step(opportunity_name, requester, step)
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

			step = "link"
			progress.mark_step(opportunity_name, requester, step)
			project_doc = project.as_dict()
			opp.custom_created_project = project.name
			opp.save(ignore_permissions=True)
			frappe.db.commit()
			committed = True

			# Deferred Closed-Won team SMS (PRO-0204 Step 1), now _send_closed_won_alerts.
			_send_closed_won_alerts(opportunity_name)

			drive_success = False
			drive_error_details = None

			# Provision Google Drive Folders
			step = "drive"
			progress.mark_step(opportunity_name, requester, step)
			if not project.get("custom_drive_folder_id"):
				drive_success, drive_error_details, folder_id = _provision_project_drive(project, opp)
				if folder_id:
					project_doc["custom_drive_folder_id"] = folder_id

		finally:
			frappe.set_user(original_user)

	except _HandoffBlocked:
		# An expected refusal, already logged with its reason. Deliberately not
		# re-logged as a traceback: this is the process working, not a fault.
		pass
	except _ProjectExists as exists:
		# Not a failure either: the Opportunity has its Project, and the person who
		# asked is told which one.
		existing_project = str(exists)
	except Exception as exc:
		# Nothing half-made may outlive a failure before the link was committed: the
		# worker commits when this function returns, so without the rollback it kept
		# whatever had been inserted, and a retry made a second Project.
		if not committed:
			frappe.db.rollback()
			project_doc = None
		log = frappe.log_error(
			title="CRM Enhancements App Background Job Failed",
			message=frappe.get_traceback(),
			reference_doctype="Opportunity",
			reference_name=opportunity_name,
		)
		error_log = getattr(log, "name", None)
		failure_reason = _failure_reason(exc, step)

	# The real-time broadcast logic remains the same. Normalized again rather than
	# trusting the caller: this is the last point before frappe.sendmail, and a
	# blank recipient poisons the whole message at the SMTP layer.
	users = _notify_recipients(users)

	if project_doc:
		status = "success"
	elif existing_project:
		status = "exists"
	elif blocked_reason:
		status = "blocked"
	else:
		status = "failed"

	message_payload = {
		"status": status,
		"project_doc": project_doc,
		"opportunity_name": opportunity_name,
		# The Project's name on every outcome that has one (v1.749.0), so the
		# dialog can link it without reaching into project_doc.
		"project": (project_doc or {}).get("name") or existing_project,
	}
	if blocked_reason:
		message_payload["blocked_reason"] = blocked_reason
	if existing_project:
		message_payload["adopted"] = adopted
	if status == "failed":
		message_payload["reason"] = failure_reason or ""
		message_payload["failed_step"] = step
		message_payload["error_log"] = error_log

	if project_doc or adopted:
		message_payload["drive_success"] = drive_success
		if drive_error_details:
			message_payload["drive_error"] = str(drive_error_details)

	# Recorded before the broadcast, so a client that hears it and then asks
	# (project_creation_state) already reads the outcome.
	progress.mark_finished(
		opportunity_name,
		requester,
		{
			key: message_payload.get(key)
			for key in (
				"status", "project", "adopted", "blocked_reason", "reason", "failed_step",
				"error_log", "drive_success",
			)
		}
		| {"drive_error": bool(drive_error_details)},
		step=None if status in ("success", "exists") else step,
	)

	# Whenever a status goes out, the requester gets it too, listed or not. Since
	# v1.529.0 the list defaults to group inboxes, which are not Users and have no
	# desk session to receive it — so without this the person who clicked Create
	# Project would hear nothing back, the hand-off gate's refusal included. The
	# session is theirs again here: the ``finally`` above restored it. An
	# Opportunity that already had its Project tells only the requester: nothing
	# new happened for the list to hear about.
	realtime_to = (
		[frappe.session.user] if status == "exists" else list(dict.fromkeys([*users, frappe.session.user]))
	)
	for user in realtime_to:
		frappe.publish_realtime(
			event="project_creation_status",
			message=message_payload,
			user=user,
		)

	if project_doc:
		subject = f"New Project Created: {project_doc.get('project_name')}"
		message = email_style.wrap(
			email_style.rich(
				"A new project has been created from Opportunity <b>"
				+ frappe.utils.escape_html(opportunity_name)
				+ "</b>."
			)
			+ email_style.kv(
				[("Project", project_doc.get("project_name")), ("Opportunity", opportunity_name)]
			)
			+ email_style.button(
				frappe.utils.get_url_to_form("Project", project_doc.get("name")), "View the project"
			),
			title=subject,
			eyebrow="Projects",
		)
		for user in users:
			frappe.sendmail(recipients=[user], subject=subject, message=message)
