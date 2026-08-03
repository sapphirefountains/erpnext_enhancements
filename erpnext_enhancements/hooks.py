"""Frappe hook registry — the wiring diagram for every customization in this app.

Nothing here executes logic; it is a declaration of what Frappe should load and when.
Read it as the index it is: `doc_events` shows which controllers this app attaches to,
`scheduler_events` shows every recurring job, `override_whitelisted_methods` and the
monkeypatches show where core behaviour is replaced, and the `app_include_*` / `doctype_js`
lists show which browser assets reach which form.

**This file is annotated, and the annotations are documentation.** Several of the comments
below record why an apparently odd choice is load-bearing — why global assets ship as
esbuild bundles rather than raw `/assets` paths (the immutable one-year cache means edits
never reach a device that already cached them), why two vendored UMD libraries are
deliberately excluded from that rule, why `setup.document_locks` runs on `before_migrate`
rather than `after_migrate`. Keep that density when you add an entry; a bare hook line with
no explanation is the thing that gets "cleaned up" two years later.

Every customization added to the app needs a line here **and** a matching entry in the
owning module's README. See `CLAUDE.md` and `.claude/skills/`.
"""

app_name = "erpnext_enhancements"
app_title = "ERPNext Enhancements"
app_publisher = "Sapphire Fountains"
app_description = "Customizations and enhancements to ERPNext."
app_email = "info@sapphirefountains.com"
app_license = "mit"

# include js, css files in header of desk.html
#
# Everything global ships as esbuild bundles ("name.bundle.css/js", resolved
# through assets.json to a content-hashed filename) — NOT raw /assets paths.
# Raw /assets paths are served with a 1-year *immutable* Cache-Control and
# carry no content hash, so edits to them never reach a device that already
# cached them (the "Kanban fix works on desktop, phones still broken" bug,
# v0.8.1). The only exceptions are the two vendored libraries below.
app_include_css = [
	"desk_enhancements.bundle.css",
	# The remaining global styles, in the old include order (cascade preserved):
	# see public/css/desk_addons.bundle.scss (a .scss entry — its imports must
	# be inlined by sass, not esbuild — but the built asset name stays .css).
	"desk_addons.bundle.css",
]
app_include_js = [
	# Vendored global-defining libraries stay raw ON PURPOSE: importing a UMD
	# build from an esbuild bundle captures its exports instead of letting it
	# set window.Vue / window.Gantt — and their content never changes, so the
	# immutable /assets cache cannot serve them stale. Loaded first so the
	# globals exist before any bundled consumer runs.
	"/assets/erpnext_enhancements/js/vue.global.js",
	"/assets/erpnext_enhancements/js/project_enhancements/lib/frappe-gantt.umd.js",
	# Kanban patch suite (hold-to-drag, Opportunity styling, leak hotfix for
	# frappe/frappe#24156, drag-to-scroll perf fix). See public/js/kanban.bundle.js
	# for the imports and each file's removal conditions.
	"kanban.bundle.js",
	# Every other global desk script (awesomebar/nav/drafts, Comments App,
	# Triton widget, telephony, task tree/gantt preloads, ...), in the old
	# include order: see public/js/erpnext_enhancements.bundle.js.
	"erpnext_enhancements.bundle.js",
]

# include js, css files in header of web template
# Bundle reference (was "/assets/erpnext_enhancements/css/login_enhancements.css",
# which 404s — public/css only contains login_enhancements.bundle.css).
web_include_css = "login_enhancements.bundle.css"
# Login page legal footer (Privacy Policy + EULA links). Loads on website pages
# but only injects on /login; styled by login_enhancements.bundle.css.
web_include_js = "login_enhancements.bundle.js"

doctype_js = {
	# training: the Course form's doors into the authoring flow — New Draft
	# Version, Send For Review, Publish (Training Manager only, and it asks the
	# Minor-Edit vs Material-Change question explicitly rather than defaulting it),
	# Assign To and Retire. The drag-and-drop builder is a later release; this is
	# what makes authoring usable before it lands.
	"Training Course": ["public/js/training/training_course.js"],
	# training: the GCS signing key goes in through a dialog, not the field. The
	# field is a Password, which Frappe renders as a SINGLE-LINE masked input --
	# a control that cannot take a 2 KB multi-line service-account JSON by paste
	# without mangling it, and being masked it then hides the damage. Also carries
	# the Test GCS Connection action.
	"Training Settings": ["public/js/training/training_settings.js"],
	# security: the Drive service-account key goes in through a dialog, not the
	# field. It is a Password (v1.211.0, was Code and therefore cleartext), and a
	# Password renders as a single-line masked input that mangles a multi-line key
	# on paste.
	"Project Folder Google Drive Settings": ["public/js/google_drive/drive_settings.js"],
	"Opportunity": [
		"public/js/opportunity.js",
		"public/js/crm_enhancements/opportunity.js",
		"public/js/global_enhancements/unified_tab_controller.js",
		# primary_contact.js binds five doctypes but was listed under "Lead" only, so
		# on the other four it ran only if the user had opened a Lead earlier in the
		# same session. Listed on all five now (v1.198.0).
		"public/js/global_enhancements/primary_contact.js",
		"project_enhancements/doctype/opportunity/opportunity.js",
		"public/js/crm_enhancements/opportunity_migrated_scripts.js",
		"public/js/crm_enhancements/opportunity_handoff.js",
		"public/js/contracts.js",
		"public/js/global_enhancements/drive_folder_button.js",
	],
	"Communication": ["public/js/communication.js"],
	"Project": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/project_merge.js",
		"public/js/project_enhancements.js",
		"public/js/project.js",
		"public/js/global_enhancements/unified_tab_controller.js",
		"public/js/global_enhancements/primary_contact.js",
		"project_enhancements/doctype/project/project.js",
		"public/js/project_enhancements/project_form_script.js",
		"public/js/project_enhancements/project_brief.js",
		"public/js/project_migrated_scripts.js",
		"public/js/project_enhancements/process_steps.js",
		"public/js/contracts.js",
		# Contracts tab (custom_contracts_html): every contract on the job —
		# agreements and the operational maintenance contracts beside them —
		# each openable in place to its full legal text. The viewer is the
		# shared renderer and must load first.
		"public/js/project_enhancements/contract_viewer.js",
		"public/js/project_enhancements/contracts_tab.js",
		"public/js/global_enhancements/drive_folder_button.js",
		# Schedule tab Gantt (custom_gantt_chart_html): read-only embed of the
		# reusable Gantt widget (erpnext_enhancements.gantt.mount — see
		# public/js/gantt_widget/). Replaces the legacy frappe-gantt renderer
		# that lived in project_enhancements/doctype/project/project.js.
		"public/js/project_enhancements/project_gantt_widget.js",
		# Budget tab "Pick Routing Map" button (custom_btn_pick_routing_map):
		# every supplier with material still to collect, in drive-time order out
		# of the shop. Backed by api/pickup_routing.py.
		"public/js/project_enhancements/pick_routing_map.js",
	],
	"Master Project": ["public/js/global_enhancements/unified_tab_controller.js"],
	# Reading a contract on screen — the template's language with the data
	# filled in. Loaded on the two forms that offer it (Preview Contract on the
	# agreement itself, View Agreement on the operational maintenance contract);
	# the Project form gets it in its own list above.
	"Project Contract": ["public/js/project_enhancements/contract_viewer.js"],
	"Sapphire Maintenance Contract": ["public/js/project_enhancements/contract_viewer.js"],
	# NOTE: the custom Comments App is now mounted globally by comments_auto.js
	# (see app_include_js + COMMENT_APP_DOCTYPES). Doctypes that only needed the
	# comments tab no longer require a doctype_js entry; the entries below keep
	# only their non-comments form scripts.
	"Item": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/item.js",
		"public/js/water_engineering/pump_curve_chart.js",
	],
	# water_engineering: shared fountain "design canvas" renderer (window.WaterFountain),
	# used by the design form's live dashboard; loaded before the auto-loaded form script.
	"Water Feature Design": ["public/js/water_engineering/fountain_canvas.js"],
	"Process Document": ["public/js/process_document.js"],
	"Employee": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/employee.js",
		"public/js/device_management/employee_devices.js",
	],
	"Account": ["public/js/vue.global.js", "public/js/comments.js", "public/js/account.js"],
	"Customer": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/customer.js",
		"public/js/global_enhancements/unified_tab_controller.js",
		"public/js/global_enhancements/primary_contact.js",
		"public/js/global_enhancements/drive_folder_button.js",
		"public/js/stripe_payments/customer_autopay.js",
		# Contracts tab — the same list the Project form carries, scoped to the
		# agreements this customer is a party to. Viewer first (shared renderer).
		"public/js/project_enhancements/contract_viewer.js",
		"public/js/project_enhancements/contracts_tab.js",
	],
	"Timesheet": ["public/js/vue.global.js", "public/js/comments.js", "public/js/timesheet.js"],
	"Sales Order": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/sales_order_enhancements.js",
	],
	"Task": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/task_enhancements.js",
		"task_enhancements/doctype/task/task.js",
	],
	"Travel Trip": ["public/js/travel_trip.js", "public/js/travel/travel_trip_map.js"],
	"Call Log": ["public/js/call_log.js"],
	"Purchase Order": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/procurement_links.js",
		"public/js/purchase_order_project.js",
	],
	"Material Request": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/procurement_links.js",
		"public/js/po_creation_guard.js",
	],
	"Supplier": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/global_enhancements/unified_tab_controller.js",
		"public/js/global_enhancements/primary_contact.js",
		"public/js/contracts.js",
	],
	"Lead": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/lead.js",
		"public/js/global_enhancements/primary_contact.js",
	],
	"Contact": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/contact.js",
		"public/js/global_enhancements/unified_tab_controller.js",
	],
	"Address": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"project_enhancements/doctype/address/address.js",
	],
	# device_management (MDM/EMM)
	"Managed Device": "device_management/doctype/managed_device/managed_device.js",
	# quickbooks_online
	"QuickBooks Online Settings": "quickbooks_online/doctype/quickbooks_online_settings/quickbooks_online_settings.js",
	# accounting_intake
	"Document Intake": "public/js/accounting_intake/document_intake.js",
	# quickbooks_online write-back button (intake-created PI / Payment Entry)
	"Purchase Invoice": "public/js/quickbooks_online/qbo_writeback_button.js",
	"Payment Entry": "public/js/quickbooks_online/qbo_writeback_button.js",
	# stripe_payments
	"Stripe Payments Settings": "public/js/stripe_payments/stripe_payments_settings.js",
	"Sales Invoice": "public/js/stripe_payments/sales_invoice_pay_button.js",
	"Stripe Payment": "public/js/stripe_payments/stripe_payment.js",
	# plaid_banking — Plaid Link connect flow on the Settings form
	"Plaid Settings": "plaid_banking/doctype/plaid_settings/plaid_settings.js",
	# fountain_move — triage actions (retry / spam) and jump-to-created-record
	"Fountain Move Request": "public/js/crm_enhancements/fountain_move_request.js",
	"Fountain Move Invite": "public/js/crm_enhancements/fountain_move_invite.js",
}

doctype_list_js = {
	"Opportunity": [
		"public/js/opportunity_list.js",
		"public/js/crm_enhancements/opportunity_list.js",
		"public/js/crm_enhancements/opportunity_kanban_totals.js",
	],
	"Supplier": "public/js/global_enhancements/supplier_list.js",
	"Task": "public/js/project_enhancements/task_gantt.js",
	"File": "public/js/global_enhancements/file_list.js",
	"Item": "public/js/item_list.js",
	"Call Log": "public/js/global_enhancements/call_log_list.js",
	"Document Intake": "public/js/accounting_intake/document_intake_list.js",
	# fountain_move — "Send Intake Link" / "Copy Public Link" + status indicators
	"Fountain Move Request": "public/js/crm_enhancements/fountain_move_request_list.js",
}
doctype_calendar_js = {
	"Asset Booking": "public/js/asset_booking_calendar.js",
	"Travel Trip": "public/js/travel_trip_calendar.js",
}
doctype_css = {
	"Opportunity": "public/css/global_enhancements/horizontal_scroll.css",
}

# Override standard doctype classes (from task_enhancements)
override_doctype_class = {
	"Task": "erpnext_enhancements.task_enhancements.doctype.task.task.Task",
}

doc_events = {
	"Task": {
		"before_save": "erpnext_enhancements.script_migrations.task.calculate_project_elapsed_time",
		"after_insert": "erpnext_enhancements.script_migrations.task.sync_task_to_google_calendar",
		"on_update": [
			"erpnext_enhancements.tasks.generate_next_task",
			"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.publish_realtime_update",
			"erpnext_enhancements.script_migrations.task.sync_project_dates_from_tasks",
		],
		"on_trash": "erpnext_enhancements.script_migrations.task.sync_project_dates_from_tasks",
	},
	"Project": {
		"before_validate": "erpnext_enhancements.sync_contact.sanitize_primary_address_link",
		"before_insert": "erpnext_enhancements.process_steps.seed_process_steps",
		"after_insert": "erpnext_enhancements.process_steps.announce_seeded_steps",
		"before_save": [
			"erpnext_enhancements.script_migrations.project.remove_open_status",
			"erpnext_enhancements.status_alerts.stamp_payment_received_date",
			# must run after stamp_payment_received_date: the Payment Received
			# anchor consumes the stamped date
			"erpnext_enhancements.process_steps.sync_process_steps",
		],
		"after_save": "erpnext_enhancements.project_enhancements.sync_attachments_from_opportunity",
		"on_update": [
			"erpnext_enhancements.sync_contact.sync_from_main_doc",
			"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.publish_realtime_update",
			"erpnext_enhancements.status_alerts.notify_payment_received",
			"erpnext_enhancements.process_steps.notify_step_transitions",
		],
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"Master Project": {
		"before_validate": "erpnext_enhancements.sync_contact.sanitize_primary_address_link",
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"Address": {
		"before_save": [
			"erpnext_enhancements.script_migrations.address.set_full_address",
			# Latitude/longitude are user-editable (v1.207.0) and every map trusts
			# them over the address text, so they are gated on the way in: half a
			# pair saves cleanly and then reads as "no point" everywhere.
			"erpnext_enhancements.script_migrations.address.validate_coordinates",
		],
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"Communication": {
		"after_insert": [
			"erpnext_enhancements.api.communication.after_insert_communication",
			"erpnext_enhancements.accounting_intake.channels.email_from_communication",
		],
	},
	"Sapphire Maintenance Record": {
		"on_submit": "erpnext_enhancements.api.maintenance_scheduling.update_next_visit_dates",
	},
	"Project Contract": {
		# When a Maintenance Services Agreement is Signed, draft the operational
		# Maintenance Contract (left as a draft; activation stays the human gate).
		# Both signing paths: submitting an already-Signed draft (on_submit) and
		# the post-submit "Mark as Signed" button (on_update_after_submit).
		"on_submit": "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract.autocreate_maintenance_contract_on_signed",
		"on_update_after_submit": "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract.autocreate_maintenance_contract_on_signed",
	},
	# travel_management: trip emails + mirroring claim/advance status onto
	# traveler rows and clearing claim stamps on cancel/trash (dedupe guard)
	"Travel Trip": {
		"on_update": "erpnext_enhancements.travel_management.notifications.on_trip_update",
	},
	"Expense Claim": {
		"on_update": "erpnext_enhancements.travel_management.integrations.sync_expense_claim_status",
		"on_update_after_submit": "erpnext_enhancements.travel_management.integrations.sync_expense_claim_status",
		"on_cancel": "erpnext_enhancements.travel_management.integrations.sync_expense_claim_status",
		"on_trash": "erpnext_enhancements.travel_management.integrations.sync_expense_claim_status",
	},
	"Employee Advance": {
		"on_update": "erpnext_enhancements.travel_management.integrations.sync_employee_advance_status",
		"on_update_after_submit": "erpnext_enhancements.travel_management.integrations.sync_employee_advance_status",
		"on_cancel": "erpnext_enhancements.travel_management.integrations.sync_employee_advance_status",
		"on_trash": "erpnext_enhancements.travel_management.integrations.sync_employee_advance_status",
	},
	"Vehicle Log": {
		"on_trash": "erpnext_enhancements.travel_management.integrations.sync_vehicle_log_unlink",
	},
	# WI-013: block submitting a Purchase Order above the configurable approval
	# threshold (ERPNext Enhancements Settings.po_approval_threshold, default 500;
	# 0 disables) unless the user holds the "PO Approver" role — the CEO sign-off
	# escalation. Threshold resolution is per-project-ready (see po_approval.py).
	"Purchase Order": {
		# WI-014 follow-through: `Purchase Order Item.project` is mandatory, but
		# ERPNext never pushes the header project down to the item rows — fill the
		# blank ones before the mandatory check runs. Desk saves are already
		# handled client-side (public/js/purchase_order_project.js); this covers
		# the REST API, data import and Material-Request-mapped documents.
		"before_validate": "erpnext_enhancements.procurement_project.cascade_project_to_items",
		# Two independent submit gates, in this order deliberately:
		#   1. WI-066 separation of duties — the person who raised the Material
		#      Request may not submit the PO that fills it. NON-waivable: no role
		#      clears it, not "PO Approver", not the CEO. Only Administrator.
		#   2. WI-013 approval threshold — waivable by the "PO Approver" role.
		# SoD reports first because it is the hard constraint. Leading with the
		# threshold's "only a PO Approver can submit it" would imply self-submission
		# becomes possible at some amount — and reads as flatly wrong when the CEO
		# is himself the requester. The SoD message names both remedies, so a
		# blocked user never needs a second round trip to discover the other gate.
		"before_submit": [
			"erpnext_enhancements.po_segregation.enforce_requester_separation",
			"erpnext_enhancements.po_approval.enforce_threshold",
			# Last, deliberately: the stamp records that this order cleared BOTH gates
			# in this person's hands. The supplier-facing print format reads it, and
			# there is nowhere else truthful to read an approver from — Purchase Order
			# has no approver field and `modified_by` is whoever touched it last.
			"erpnext_enhancements.po_approval.stamp_approval",
		],
	},
	"Opportunity": {
		"before_validate": "erpnext_enhancements.sync_contact.sanitize_primary_address_link",
		"before_save": [
			"erpnext_enhancements.crm_enhancements.api.sync_opportunity_tags",
			"erpnext_enhancements.script_migrations.opportunity.stamp_won_date",
			"erpnext_enhancements.script_migrations.opportunity.validate_ranks_on_won",
			"erpnext_enhancements.script_migrations.opportunity.validate_close_reason",
			"erpnext_enhancements.script_migrations.opportunity.update_lead_status",
			"erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline.stamp_stage_change",
		],
		"on_update": [
			"erpnext_enhancements.sync_contact.sync_from_main_doc",
			"erpnext_enhancements.crm_enhancements.project_prompt.prompt_create_project_on_won",
			"erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline.publish_pipeline_update",
		],
		# Drive folder per Customer-party opportunity (settings opt-in)
		"after_insert": "erpnext_enhancements.google_drive.drive_utils.enqueue_opportunity_folder",
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"File": {
		# ERPNext -> Drive half of the attachment sync (settings opt-in;
		# cheap bail-out for files not attached to a Drive-linked document)
		"after_insert": "erpnext_enhancements.google_drive.drive_sync.on_file_attached",
	},
	"Activity Log": {
		# Email on every Administrator authentication, success or failure. Frappe's
		# 2FA exempts Administrator unconditionally, so watching it is the only
		# control left on that account (security_alerts.py). Hooked here rather
		# than on_session_creation because this row is written for failed attempts
		# too, and the failures are the early warning.
		"after_insert": "erpnext_enhancements.security_alerts.notify_administrator_login",
	},
	"Contact": {
		# custom_account <-> Customer link two-way sync also runs before naming:
		# core Contact.autoname reads links[0], so an insert carrying only the
		# Account must get its Customer row before the name is set (contacts_ux.py)
		"before_insert": "erpnext_enhancements.contacts_ux.sync_contact_account_links",
		"validate": [
			# account/link sync first so the title below sees the final link set
			"erpnext_enhancements.contacts_ux.sync_contact_account_links",
			# Title field custom_full_name_and_role = "First Last-Party" (ported from a
			# disabled Server Script; see script_migrations/contact.py)
			"erpnext_enhancements.script_migrations.contact.set_full_name_and_role",
		],
		"on_update": "erpnext_enhancements.sync_contact.sync_from_contact",
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"Employee": {
		# training: a new hire picks up the Training Learner role and every Required
		# course their department/designation owes, on day one. No-ops when user_id
		# is not set yet (employees are routinely created before a login exists) —
		# the on_update handler catches it when one appears.
		"after_insert": "erpnext_enhancements.training.assignment.on_employee_insert",
		"on_update": [
			# Cell Number -> linked User.phone (Call via Triton dials it)
			"erpnext_enhancements.sync_contact.sync_employee_phone_to_user",
			# training: re-evaluate assignment rules when something a rule keys off
			# actually moved. Guarded with get_doc_before_save() on department /
			# designation / grade / employment_type / status (plus user_id first
			# appearing) — without that comparison EVERY Employee save enqueues a
			# full rule sweep, and Employee is saved often.
			"erpnext_enhancements.training.assignment.on_employee_update",
		],
	},
	"Training Completion": {
		# training: certificate issuance, badge awards and the "you passed" email ride the
		# Completion submit rather than the endpoint, so a completion recorded by a manager
		# by hand gets identical treatment to one a learner earned.
		"on_submit": "erpnext_enhancements.training.certificates.after_completion",
		# Revocation must expire the certificate AND re-open the assignment, or a revoked
		# pass silently still reads as compliant -- which is the whole point of revoking.
		"on_cancel": "erpnext_enhancements.training.certificates.on_revoke",
	},
	"Sapphire Maintenance Record": {
		# training: WARN-ONLY certification check on the assigned technician. It NEVER
		# throws -- by the time this runs a truck is usually already at the site, and
		# blocking the visit form would mean the work happens with NO RECORD AT ALL, which
		# is worse than the uncertified assignment it is flagging. Appends a comment plus an
		# orange msgprint and notifies the supervisor. Gated by Training Settings ->
		# warn_on_uncertified_dispatch. On validate, not before_submit: before_submit would
		# read as a gate.
		"validate": "erpnext_enhancements.training.compliance.warn_uncertified_technician",
	},
	"Task": {
		# training: the same warn-only check when a task is assigned to somebody lacking a
		# current certification for the task type. Never blocks.
		"validate": "erpnext_enhancements.training.compliance.warn_uncertified_assignee",
	},
	"User": {
		# training: a Role Profile change rewrites a user's roles wholesale and can
		# bring a role-targeted Required course into scope for someone the Employee
		# hook never sees. Compares the roles child table against
		# get_doc_before_save() and returns immediately when unchanged; the sweep
		# itself is enqueue_after_commit so it can never delay a login or a save.
		"on_update": "erpnext_enhancements.training.assignment.on_user_roles_changed",
	},
	"Supplier": {
		"after_insert": "erpnext_enhancements.accounting_intake.filing.enqueue_supplier_folder",
		"on_update": "erpnext_enhancements.sync_contact.sync_from_main_doc",
		"validate": [
			"erpnext_enhancements.supplier_query.sync_supplier_groups",
			# Primary Address display text = Address.custom_full_address
			"erpnext_enhancements.sync_contact.set_supplier_primary_address_display",
		],
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"Customer": {
		"before_save": "erpnext_enhancements.script_migrations.customer.set_last_activity",
		"on_update": "erpnext_enhancements.sync_contact.sync_from_main_doc",
		# Drive folder per customer (Project Folder Google Drive Settings opt-in)
		"after_insert": "erpnext_enhancements.google_drive.drive_utils.enqueue_customer_folder",
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	# stripe_payments: auto-charge a saved method when an invoice for an
	# autopay-enrolled customer is submitted (covers maintenance-generated invoices).
	"Sales Invoice": {
		"on_submit": "erpnext_enhancements.stripe_payments.core.saved_methods.auto_charge_on_invoice_submit",
	},
	"*": {
		"after_save": "erpnext_enhancements.utils.triton_sync.global_triton_sync",
	},
}

scheduler_events = {
	"cron": {
		# Morning Briefing pre-generation, weekdays 06:30. Frappe evaluates cron
		# in the site's System Settings timezone (must be America/Denver here).
		# The handler immediately enqueues the batch onto the long queue.
		"30 6 * * 1-5": ["erpnext_enhancements.api.briefing.scheduled_briefing_run"],
		# KPI dashboard snapshots — nightly 05:00 (site TZ), one precomputed
		# KPI Snapshot per department. Handler enqueues the batch onto long.
		"0 5 * * *": ["erpnext_enhancements.kpi_dashboards.snapshots.scheduled_kpi_run"],
		# Morning technician dispatch digest — 06:00 site TZ (gated in Settings).
		"0 6 * * *": ["erpnext_enhancements.api.maintenance_dispatch.send_morning_digests"],
		# QuickBooks Online sync — STAGGERED across the hour, not all fired together.
		# The three jobs each write the single QuickBooks Online Settings doc (token
		# refresh must save() through the doc for Password-field encryption, so it
		# can't use db.set_value like the cursor writes do); firing them at the same
		# instant made two saves race and the loser fail with TimestampMismatchError
		# (~2 CDC runs/day — self-healing, since the next tick re-reads last_cdc_sync,
		# but it tripped monitoring). Offsets keep the writers apart. Ordered so the
		# token is refreshed (:00) before CDC pulls (:20) and retries run last (:40).
		# All three self-throttle/guard internally and no-op while disconnected.
		"0 * * * *": ["erpnext_enhancements.quickbooks_online.core.tasks.refresh_token_if_needed"],
		"20 * * * *": ["erpnext_enhancements.quickbooks_online.core.tasks.cdc_poll"],
		"40 * * * *": ["erpnext_enhancements.quickbooks_online.core.tasks.retry_failed_syncs"],
		# Training due/overdue digest — 07:15 site TZ, deliberately AFTER the 06:00
		# technician dispatch digest so a tech opening their phone finds two clearly
		# separated emails rather than two competing ones in the same minute. One
		# digest per learner covering every course they owe, not one per assignment.
		# Gated by Training Settings -> Send Notifications.
		"15 7 * * *": ["erpnext_enhancements.training.tasks.send_due_reminders"],
	},
	"daily": [
		# training: move assignments past their due date into Overdue. A separate
		# pass rather than a side effect of the reminder job, because the status has
		# to be right whether or not notifications are switched on — the compliance
		# warning and the completion reports both read it. Must run BEFORE the
		# escalation below, which only looks at rows already marked Overdue.
		"erpnext_enhancements.training.tasks.refresh_overdue_status",
		# training: expire completions past expires_on and raise the recertification
		# assignment, so "does this tech hold a current cert" stays one indexed query rather
		# than a date calculation at read time. Re-dates an existing open assignment rather
		# than inserting a second, so it cannot collide with a material-change retake.
		"erpnext_enhancements.training.certificates.expire_and_recertify",
		# training: streaks decay at midnight; refresh the denormalised Training Learner
		# Stat rows the leaderboards read.
		"erpnext_enhancements.training.gamification.refresh_learner_stats",
		# training: escalate assignments that have stayed overdue past the course's
		# grace period to its escalation role. Gated in Training Settings; the
		# learner is excluded from their own escalation email.
		"erpnext_enhancements.training.tasks.escalate_overdue_assignments",
		"erpnext_enhancements.project_enhancements.send_project_start_reminders",
		"erpnext_enhancements.tasks.predictive_maintenance_scheduling",
		# maintenance renewal/rate engine: T-30 rate-change notices (§4.5). The
		# auto-renew/expire step runs inside predictive_maintenance_scheduling.
		"erpnext_enhancements.api.maintenance_renewal.send_rate_change_notices",
		# recurring billing (§4.2): draft period invoices for Monthly/Quarterly/
		# Annually contracts (base + rolled-up consumables), gated in Settings.
		"erpnext_enhancements.api.maintenance_billing.generate_recurring_invoices",
		# declined-card dunning: retry failed Stripe auto-charges on a schedule,
		# email the customer, and on exhaustion disable autopay + service hold.
		"erpnext_enhancements.stripe_payments.core.dunning.run_dunning_cycle",
		# contract e-signature housekeeping: retire timed-out signing links so the
		# list view is truthful, and re-send a signed copy that never landed.
		"erpnext_enhancements.project_enhancements.esign.tasks.expire_stale_requests",
		"erpnext_enhancements.project_enhancements.esign.tasks.retry_undelivered",
		# chase an unsigned agreement on the configured cadence, then tell the
		# contract owner once the last nudge goes unanswered.
		"erpnext_enhancements.project_enhancements.esign.tasks.send_signature_reminders",
		"erpnext_enhancements.script_migrations.customer.customer_inactivity_reminder",
		"erpnext_enhancements.script_migrations.project.update_elapsed_time_daily",
		"erpnext_enhancements.api.user_drafts.cleanup_stale_drafts",
		"erpnext_enhancements.api.time_kiosk.purge_old_location_logs",
		"erpnext_enhancements.status_alerts.nag_unconverted_opportunities",
		"erpnext_enhancements.process_steps.escalate_overdue_steps",
		# travel_management — auto-advance must run before the reminders so
		# they see today's statuses
		"erpnext_enhancements.travel_management.tasks.auto_advance_trip_statuses",
		"erpnext_enhancements.travel_management.reminders.send_pre_travel_reminders",
		"erpnext_enhancements.travel_management.reminders.send_post_trip_expense_nudges",
		"erpnext_enhancements.api.briefing.purge_old_briefings",
		"erpnext_enhancements.kpi_dashboards.snapshots.purge_old_snapshots",
		"erpnext_enhancements.ai_governance.tasks.purge_old_action_logs",
		# Re-enqueue Failed Drive Sync Log rows (uploads / recording exports)
		"erpnext_enhancements.google_drive.drive_sync.retry_failed_syncs",
		# device_management (MDM/EMM): warranty lead-time + stale-attestation nudges
		"erpnext_enhancements.device_management.tasks.send_device_warranty_reminders",
		"erpnext_enhancements.device_management.tasks.nudge_stale_device_attestations",
		# accounting_intake: retry failed intake steps + purge old logs
		"erpnext_enhancements.accounting_intake.channels.retry_failed_intakes",
		"erpnext_enhancements.accounting_intake.channels.purge_old_intake_logs",
		# fleet_maintenance: refresh vehicle maintenance status (Due Soon / Overdue
		# as dates pass) + notify fleet managers on a new slip. Dormant unless enabled.
		"erpnext_enhancements.fleet_maintenance.tasks.refresh_fleet_status",
		# fountain_move: expire stale intake invites, chase requests stuck in a
		# status nobody watches, and catch photos whose Drive folders arrived late
		"erpnext_enhancements.crm_enhancements.fountain_move.invites.expire_stale_invites",
		"erpnext_enhancements.crm_enhancements.fountain_move.notify.digest_stuck_requests",
		"erpnext_enhancements.crm_enhancements.fountain_move.photos.sweep_unmirrored_photos",
	],
	"hourly": [
		# training: drain Training Attempt progress still sitting in Redis from a
		# session that ended without a final beacon (closed laptop, dead phone,
		# killed tab). Bounds progress loss at one flush interval rather than a
		# whole lesson.
		"erpnext_enhancements.training.progress.flush_stale_attempts",
		# QuickBooks Online sync jobs moved to staggered cron entries above to stop
		# the three from racing on the Settings doc (TimestampMismatchError). See the
		# "cron" section.
		"erpnext_enhancements.tasks.nudge_unsubmitted_maintenance_forms",
		"erpnext_enhancements.ai_governance.tasks.expire_stale_pending_actions",
		# fountain_move: delete photos uploaded by someone who never submitted the
		# form. Without this the guest upload endpoint doubles as free storage.
		"erpnext_enhancements.crm_enhancements.fountain_move.intake.gc_orphan_intake_files",
		# Drive -> ERPNext half of the attachment sync (link-only shadows)
		"erpnext_enhancements.google_drive.drive_sync.sync_shadow_attachments",
		# mdm_integration: pull Miradore/Action1 device inventory + keep the
		# Action1 OAuth token alive + retry failed syncs (each throttled/guarded)
		"erpnext_enhancements.mdm_integration.tasks.refresh_action1_token",
		"erpnext_enhancements.mdm_integration.tasks.sync_devices",
		"erpnext_enhancements.mdm_integration.tasks.retry_failed_syncs",
		# accounting_intake: ingest new files dropped into the Drive watched folder
		"erpnext_enhancements.accounting_intake.channels.poll_watched_folder",
		# stripe_payments: backstop for missed webhooks + retry of errored events
		"erpnext_enhancements.stripe_payments.core.tasks.poll_pending",
		"erpnext_enhancements.stripe_payments.core.tasks.poll_payouts",
		"erpnext_enhancements.stripe_payments.core.tasks.retry_failed",
		# plaid_banking: refresh cached bank balances (self-throttled to
		# refresh_poll_minutes; skips while paused, so a dead link can't storm)
		"erpnext_enhancements.plaid_banking.core.tasks.scheduled_balance_refresh",
	],
	"weekly": [
		"erpnext_enhancements.tasks.suggest_truck_restocks",
		# one summary of every agreement still out for signature, so a link that
		# quietly went nowhere is visible without anyone remembering to look.
		"erpnext_enhancements.project_enhancements.esign.tasks.digest_awaiting_signature",
	],
}

# Ship per-session data to the desk client (frappe.boot.*).
# Currently: the live-collab doctype allowlist (frappe.boot.collab_doctypes),
# read from ERPNext Enhancements Settings — see boot.py and api/collab.py.
extend_bootinfo = "erpnext_enhancements.boot.boot_session"

# Jinja methods available to Print Formats / web templates. The print sandbox
# cannot parse the Water Feature Design pipe segments' fittings/components JSON
# rows, so the aggregation (DOC-0121 Fitting Schedule) and the typed design
# issues (Design Review section) are exposed as callables instead.
jinja = {
	"methods": [
		"erpnext_enhancements.water_engineering.issues.we_fitting_schedule",
		"erpnext_enhancements.water_engineering.issues.we_design_issues",
	],
}

# Run BEFORE each `bench migrate` (in pre_schema_updates, before fixture sync).
before_migrate = [
	# Drop stale Role Profile document locks so fixture sync can't crash with
	# DocumentLockedError. Frappe core's RoleProfile.on_update queue_action locks
	# the doc and defers "resave all users" to the long queue; the deploy's Redis
	# FLUSHDB destroys that job before it can unlock, orphaning the lock for up to
	# 3h. A second migrate inside that window then aborts here. See document_locks.py.
	"erpnext_enhancements.setup.document_locks.clear_stale_role_profile_locks",
]

# Run after each `bench migrate` (from global_enhancements)
after_migrate = [
	"erpnext_enhancements.setup.custom_fields.create_primary_contact_fields",
	"erpnext_enhancements.setup.supplier_groups.create_supplier_group_customizations",
	# Hide the "Project" DocType link in the core Projects module sidebar (user request)
	"erpnext_enhancements.setup.workspace_tweaks.hide_core_sidebar_items",
	# Mermaid.js Process Document charts — repo is the source of truth
	"erpnext_enhancements.setup.process_documents.sync_process_documents",
	# Projects-module dashboard widgets (Custom HTML Blocks) — repo is the source
	# of truth; upserts the blocks from "Custom HTML Block/" and places them on Home
	"erpnext_enhancements.setup.custom_html_blocks.sync_custom_html_blocks",
	# device_management (MDM/EMM): Employee "Assigned Devices" panel field
	"erpnext_enhancements.device_management.setup.create_device_employee_fields",
	# accounting_intake: Supplier Drive folder id (document filing)
	"erpnext_enhancements.accounting_intake.setup.create_supplier_drive_field",
	# accounting_intake: QBO write-back fields on Purchase Invoice / Payment Entry
	"erpnext_enhancements.accounting_intake.setup.create_qbo_writeback_fields",
	# stripe_payments: Stripe id back-reference fields + Stripe/ACH Modes of Payment
	"erpnext_enhancements.stripe_payments.setup.create_stripe_custom_fields",
	"erpnext_enhancements.stripe_payments.setup.create_stripe_modes_of_payment",
	# water_engineering: pump-spec fields on Item (rated flow/head + nameplate) +
	# the DOC-0028 starter pump catalog, so the design spine resolves a pump. Runs
	# on every migrate (idempotent + guarded) — Frappe Cloud gets it on deploy with
	# no shell needed.
	"erpnext_enhancements.water_engineering.setup.ensure_pump_catalog",
	# water_engineering: generic starter Nozzle Profiles so orifice nozzles compute
	# immediately (idempotent + guarded; flagged generic — replace with cut-sheet data).
	"erpnext_enhancements.water_engineering.setup.ensure_nozzle_profiles",
	# water_engineering: the Results + Calculation Audit Print Formats for a design
	# (idempotent + guarded; re-upserts the HTML so template edits deploy on migrate).
	"erpnext_enhancements.water_engineering.setup_print_formats.ensure_water_print_formats",
	# water_engineering: workspace triage Number Cards over the denormalized
	# issue counters (Designs with Blockers / Ready to Issue). Idempotent + guarded.
	"erpnext_enhancements.water_engineering.setup.ensure_water_number_cards",
	# fleet_maintenance: the Vehicle Maintenance Checklist Print Format (idempotent
	# + guarded; re-upserts the HTML so template edits deploy on migrate).
	"erpnext_enhancements.fleet_maintenance.setup_print_formats.ensure_fleet_print_formats",
	# product_configurator: Item provenance field (marks configurator-generated
	# Items so regenerate can safely reuse them) + the Build Instructions /
	# QC Checklist / Pricing Summary Print Formats (idempotent + guarded).
	"erpnext_enhancements.product_configurator.setup.create_configurator_item_fields",
	"erpnext_enhancements.product_configurator.setup_print_formats.ensure_configurator_print_formats",
	# enhancements_core: the supplier-facing Purchase Order print format. Procurement
	# has no module of its own (po_approval / po_segregation / procurement_project sit
	# at the app root) and a Print Format needs a real Module Def, so it lands in the
	# catch-all. Idempotent + guarded like the others.
	"erpnext_enhancements.enhancements_core.setup_print_formats.ensure_enhancements_core_print_formats",
	# package_dispatch: the Package Dispatch Sheet Print Format (idempotent +
	# guarded; re-upserts the HTML so template edits deploy on migrate).
	"erpnext_enhancements.package_dispatch.setup_print_formats.ensure_package_dispatch_print_formats",
	# training: starter Training Categories, so the builder's category picker is
	# never empty on a fresh site (an empty picker reads as a broken form).
	# Insert-only — a category somebody renamed or deleted stays that way.
	"erpnext_enhancements.training.setup.ensure_training_categories",
	# training: the Training Certificate print format (idempotent upsert, so template
	# edits deploy on the next migrate). MUST sit ABOVE ensure_chrome_pdf_generator,
	# which is last on purpose and has to SEE this format to point it at the right
	# backend -- registered after it, the certificate silently renders with the wrong one.
	"erpnext_enhancements.training.setup_print_formats.ensure_training_print_formats",
	# training: starter Training Badges. Insert-only and inert until gamification is on.
	"erpnext_enhancements.training.gamification.ensure_training_badges",
	# Point every Print Format at the chrome PDF backend. Must run on EVERY migrate, not
	# once as a patch: standard formats re-sync from their app's JSON, so the setting is
	# reverted by the same migrate that would have applied a patch. It also has to use
	# frappe.db.set_value, because Print Format.validate refuses ORM writes to standard
	# formats outright. LAST in this list on purpose -- it should see any format the hooks
	# above have just created. See setup_print_formats.ensure_chrome_pdf_generator.
	"erpnext_enhancements.enhancements_core.setup_print_formats.ensure_chrome_pdf_generator",
]

# Version-controlled customizations: every manually created Custom Field and
# Property Setter on the site lives in fixtures/ and is re-applied on migrate —
# the repo is the source of truth, UI changes do not survive deploys.
# The "not in" lists exclude records that are flagged manual on the site but are
# owned by other installed apps or the framework; they must never be exported or
# synced from here. See fixtures/README.md for the full spec.
fixtures = [
	{
		"dt": "Custom Field",
		"filters": [
			["is_system_generated", "=", 0],
			# hrms-app doctypes are NOT installed on prod/test. One record targeting a
			# missing doctype raises DoesNotExistError, and sync_fixtures then skips the
			# ENTIRE custom_field.json silently — which is exactly what had been happening
			# on every prod deploy (discovered 2026-07-14 via the WI-065 label changes not
			# landing). Their records live in fixtures/custom_field_hrms.json instead,
			# which sync_fixtures skips gracefully PER-FILE where hrms is absent and
			# applies on hrms-bearing benches. Keep this dt filter in sync with that file.
			[
				"dt",
				"not in",
				[
					"Employee Advance",  # hrms
					"Expense Claim",  # hrms
					"Vehicle Log",  # hrms
				],
			],
			[
				"name",
				"not in",
				[
					"User-hide_my_private_information_from_others",  # lms
					"User-user_category",  # lms
					"User-verify_terms",  # lms
					"User-assistant_enabled",  # frappe_assistant_core
					"Sapphire Maintenance Record-workflow_state",  # frappe workflow engine
					"Purchase Invoice-workflow_state",  # frappe workflow engine (Purchase Invoice Approval)
					"Payment Entry-workflow_state",  # frappe workflow engine (Payment Entry Approval)
				],
			],
		],
	},
	{
		"dt": "Property Setter",
		"filters": [
			["is_system_generated", "=", 0],
			[
				"name",
				"not in",
				[
					"LMS Certificate-main-default_print_format",  # lms
				],
			],
		],
	},
	{
		"dt": "Workflow",
		"filters": [["document_type", "in", ["Sapphire Maintenance Record", "Purchase Invoice", "Payment Entry"]]],
	},
	{
		"dt": "Workflow State",
		"filters": [
			[
				"name",
				"in",
				[
					"Draft",
					"Pending Review",
					"Final/Submitted",
					"Pending Approval",
					"Approved",
					"Rejected",
				],
			]
		],
	},
	{
		"dt": "Workflow Action Master",
		"filters": [
			["name", "in", ["Request Review", "Approve & Submit", "Submit for Approval", "Approve", "Reject"]]
		],
	},
	{
		"dt": "Notification",
		"filters": [
			[
				"name",
				"in",
				[
					"Maintenance Review Needed",
					"Maintenance Finalized",
					"Maintenance Reading Out of Range",
					"Maintenance Contract Renewal Due",
					"High Escalation Risk Call",
					"Compliance Flag on Call",
				],
			]
		],
	},
	{"dt": "Print Format", "filters": [["name", "in", ["Maintenance Record Print", "Project Contract Print"]]]},
	# Call Center analytics (v1.11.0). Charts/cards are filtered by name so a
	# re-export never sweeps up user-created dashboards from the site.
	{
		"dt": "Dashboard Chart",
		"filters": [
			[
				"name",
				"in",
				[
					"Call Volume (Daily)",
					"Call Sentiment",
					"Call Escalation Risk",
					"Calls by Direction",
					"Calls by Intent",
					"AI Tokens per Day",
					"AI Actions by Status",
					"AI Mutations by Risk",
					# Operational dashboards (Project Delivery / Sales Pipeline / Procurement / Executive)
					"Projects by Type",
					"Projects by Status",
					"Project Tasks by Status",
					"New Projects (Weekly)",
					"Avg Completion by Project Type",
					"Opportunities by Status",
					"Opportunity Value by Status",
					"Opportunities by Territory",
					"Weekly Opportunity Inflow",
					"Leads by Status",
					"Purchase Orders by Status",
					"Monthly PO Value",
					"Material Requests by Status",
					# QuickBooks Online sync health (v1.53.0)
					"QuickBooks Sync Runs (Daily)",
					"QuickBooks Syncs by Type",
					"QuickBooks Syncs by Status",
					# Finance Health dashboard (KPI dashboards, v1.115.0)
					"Monthly Revenue",
					"Sales Invoices by Status",
					# Win/loss reasons (Phase 4)
					"Opportunity Loss Reasons",
					# Product Management KPI dashboard (KPI dashboards, v1.124.0)
					"Catalog by Item Group",
					"Catalog Additions (Monthly)",
					# HR department (KPI dashboards, v1.150.0)
					"Active Headcount by Department",
					"Active Headcount by Employment Type",
					"Hires by Month",
				],
			]
		],
	},
	{
		"dt": "Number Card",
		"filters": [["name", "in", ["Total Calls", "High Risk Calls", "Missed Calls", "Avg CSAT", "Active Projects", "Overdue Tasks", "Avg Project Completion %", "Projects Completed", "Open Opportunities", "Open Pipeline Value", "Closed-Won Opportunities", "Active Leads", "Open Purchase Orders", "Open PO Value", "Pending Material Requests", "QuickBooks Failed Syncs", "QuickBooks Records Mapped", "QuickBooks Open Conflicts", "QuickBooks Pending Review", "AR Outstanding", "Overdue Sales Invoices", "AP Outstanding", "Draft Sales Invoices"]]],
	},
	{"dt": "Dashboard", "filters": [["name", "in", ["Call Center", "Project Delivery", "Sales Pipeline", "Procurement", "Executive Summary", "QuickBooks Online", "Finance Health", "Product Catalog", "HR Overview"]]]},
	# Public legal pages (guest-accessible Web Pages). stripe_payments adds the
	# payment/surcharge + refund policies (counsel-review-pending); fountain_move
	# adds the terms of use its consent checkbox links to.
	# NOTE: a record added to fixtures/web_page.json but NOT named here is never
	# exported and never synced — the page simply 404s with no error anywhere.
	{
		"dt": "Web Page",
		"filters": [
			[
				"name",
				"in",
				["eula", "privacy-policy", "payment-terms", "refund-policy", "terms-of-use"],
			]
		],
	},
	# WI-012: version-control the Material Request / Purchase Order permission split
	# (team lead raises the MR, PM converts to the PO). Custom DocPerm fully overrides
	# a doctype's standard perms, so these rows ARE the complete effective perm set for
	# both doctypes — the parent-in filter captures exactly those two doctypes' rows.
	{
		"dt": "Custom DocPerm",
		"filters": [["parent", "in", ["Material Request", "Purchase Order"]]],
	},
	# WI-010: version-control the security architecture — the 18 hand-built Role
	# Profiles + the one is_custom Role ("Employee Self Service"). name-in allowlists
	# so re-export never sweeps user-created records. "PO Approver" and "PO Creator"
	# are deliberately absent from the Role entry — they are owned by
	# patches/seed_po_approver_role.py and patches/seed_po_creator_role.py.
	# NOTE: this list's order governs *export* only. Fixtures IMPORT in alphabetical
	# filename order (frappe/utils/fixtures.py sorts the directory), so
	# custom_docperm.json lands before role.json and role.json before
	# role_profile.json. Any Role a fixture references must therefore be seeded by a
	# post_model_sync patch, which runs before fixture sync — not added here and
	# hoped for. Retire/rename of the legacy "Poseidon" profile is out of scope.
	{
		"dt": "Role",
		"filters": [["name", "in", ["Employee Self Service"]]],
	},
	{
		"dt": "Role Profile",
		"filters": [
			[
				"name",
				"in",
				[
					"Accounting",
					"Design Team",
					"Executive",
					"Finance",
					"Finance Team",
					"HR",
					"Inventory",
					"Manufacturing",
					# WI-066: two single-role add-on profiles, each carrying exactly one
					# purchasing authority. They exist because a user who holds ANY role
					# profile has `roles` regenerated from the union of their profiles on
					# every save (User.populate_role_profile_roles) — a direct role grant
					# does not survive, so an authority can only reach a profiled user
					# through a profile of its own.
					#
					# Single-role on purpose. The alternative was folding "PO Approver"
					# into the departmental "Finance Team" profile, which had one member
					# at the time; that would have silently promoted every future finance
					# hire to approving POs over the threshold — the same way "Purchase
					# User" quietly grew to sixteen people and made WI-066 necessary.
					#
					# Deliberately NOT named "Purchasing" — a legacy "Purchase" profile
					# already exists below, and assigning the wrong one to a profile-less
					# user regenerates their roles from it and wipes System Manager.
					"PO Approvers",
					"PO Creators",
					"Poseidon",
					"Production Team",
					"Projects & Operations",
					"Purchase",
					"Sales",
					"Sales & Marketing",
					"Sales Team",
					"System Manager",
					"Technician",
				],
			]
		],
	},
]

override_whitelisted_methods = {
	"erpnext.crm.doctype.opportunity.opportunity.make_project": "erpnext_enhancements.opportunity_enhancements.make_project"
}

override_doctype_dashboards = {
	"Project": "erpnext_enhancements.project_enhancements.get_dashboard_data",
	"Employee": "erpnext_enhancements.dashboard_overrides.get_data",
	# Travel Trips taken FOR these doctypes (dynamic travel_for link)
	"Opportunity": "erpnext_enhancements.travel_management.dashboard.get_opportunity_dashboard_data",
	"Lead": "erpnext_enhancements.travel_management.dashboard.get_lead_dashboard_data",
	"Customer": "erpnext_enhancements.travel_management.dashboard.get_customer_dashboard_data",
}

# Row-level Travel Trip access: crew members (travelers child table) and
# owners only; Travel Coordinator / HR Manager / System Manager see all.
permission_query_conditions = {
	"Travel Trip": "erpnext_enhancements.travel_management.permissions.get_permission_query_conditions",
	# Managed Device: employees see only the device assigned to them (BYOD privacy)
	"Managed Device": "erpnext_enhancements.device_management.permissions.get_permission_query_conditions",
	# Sapphire Maintenance Record: portal customers see only their own submitted visits
	"Sapphire Maintenance Record": "erpnext_enhancements.sapphire_maintenance.permissions.get_permission_query_conditions",
	# Training Assignment: a learner sees only their own; a supervisor also sees
	# their direct reports (Employee.reports_to), which is what makes the sign-off
	# queue a plain filtered list rather than a bespoke endpoint. Course CONTENT is
	# not scoped here at all -- learner roles hold no DocPerm on Training Course /
	# Version / Lesson / Question / Answer Option, so /api/resource refuses them
	# outright and the answer key cannot leak through a careless future endpoint.
	"Training Assignment": "erpnext_enhancements.training.permissions.assignment_query_conditions",
	"Training Attempt": "erpnext_enhancements.training.permissions.attempt_query_conditions",
	"Training Attempt Question": "erpnext_enhancements.training.permissions.attempt_question_query_conditions",
	"Training Completion": "erpnext_enhancements.training.permissions.completion_query_conditions",
	"Training Certificate": "erpnext_enhancements.training.permissions.certificate_query_conditions",
	"Training Signoff": "erpnext_enhancements.training.permissions.signoff_query_conditions",
}

has_permission = {
	"Travel Trip": "erpnext_enhancements.travel_management.permissions.has_permission",
	"Managed Device": "erpnext_enhancements.device_management.permissions.has_permission",
	"Sapphire Maintenance Record": "erpnext_enhancements.sapphire_maintenance.permissions.has_permission",
	"Training Assignment": "erpnext_enhancements.training.permissions.assignment_has_permission",
	"Training Attempt": "erpnext_enhancements.training.permissions.attempt_has_permission",
	"Training Attempt Question": "erpnext_enhancements.training.permissions.attempt_question_has_permission",
	"Training Completion": "erpnext_enhancements.training.permissions.completion_has_permission",
	"Training Certificate": "erpnext_enhancements.training.permissions.certificate_has_permission",
	"Training Signoff": "erpnext_enhancements.training.permissions.signoff_has_permission",
}

ignore_links_on_delete = ["User Form Draft"]

portal_menu_items = [
	# training: customers reach "how to operate your fountain" at /training -- the
	# same mobile page the field crew uses, role-gated rather than duplicated into
	# a second customer-only page. Added only now that the page actually exists; a
	# dead menu item teaches people to ignore the menu.
	{"title": "Training", "route": "/training", "role": "Training Learner"},
	{"title": "Maintenance Records", "route": "/maintenance-records", "role": "Customer"},
	{"title": "Pay Invoices", "route": "/pay", "role": "Customer"},
]

# ---------------------------------------------------------------------------
# Frappe Assistant Core (FAC) integration — read-only MCP tools + skills
# ---------------------------------------------------------------------------
# These hooks are read ONLY by frappe_assistant_core: its tool loader imports
# the dotted paths below (each wrapped in try/except on FAC's side), and its
# migrate hook syncs the skills manifest into FAC Skill rows. On sites without
# FAC installed they are inert strings — erpnext_enhancements has no import-
# time or install-time dependency on FAC. Do not import assistant_tools/* from
# app code (tripwire-tested). The assistant_tool_configs hook is deliberately
# NOT used: Frappe's hook merging list-wraps scalar values and FAC does not
# unwrap them — tool defaults live in each tool's default_config; per-site
# overrides go in site_config.json under "assistant_tools".
# NOTE: each module filename must equal its tool's name (FAC's custom_tools
# plugin derives tool identifiers from the module path).
assistant_tools = [
	# training: two read-only tools. Compliance status answers "is my team current"
	# and leads with the exceptions; learner record answers "can I send this person to
	# that job" and distinguishes a current certification from one superseded by a
	# material change to the course. Neither reports watch coverage on its own --
	# coverage is time with a video playing, not attention, and alone it reads as
	# proof of engagement that it cannot support.
	"erpnext_enhancements.assistant_tools.training_compliance_status.TrainingComplianceStatus",
	"erpnext_enhancements.assistant_tools.training_learner_record.TrainingLearnerRecord",
	"erpnext_enhancements.assistant_tools.maintenance_day_board.MaintenanceDayBoard",
	"erpnext_enhancements.assistant_tools.maintenance_contract_status.MaintenanceContractStatus",
	"erpnext_enhancements.assistant_tools.maintenance_visit_history.MaintenanceVisitHistory",
	"erpnext_enhancements.assistant_tools.maintenance_site_briefing.MaintenanceSiteBriefing",
	"erpnext_enhancements.assistant_tools.project_status_overview.ProjectStatusOverview",
	"erpnext_enhancements.assistant_tools.project_procurement_status.ProjectProcurementStatus",
	"erpnext_enhancements.assistant_tools.workforce_time_status.WorkforceTimeStatus",
	# v1.14.0 AI governance: the model's read-only half of the write-confirmation
	# round-trip (see assistant_tools/_gate.py — there is deliberately no MCP
	# confirm tool).
	"erpnext_enhancements.assistant_tools.check_ai_pending_action.CheckAiPendingAction",
	# v1.29.0 — the first AI *write* tool. Mutating: gated by _gate.py
	# (APP_MUTATING) so it proposes an AI Pending Action when write gating is on.
	"erpnext_enhancements.assistant_tools.create_followup_task.CreateFollowupTask",
	# v1.32.0 — mdm_integration remote device actions. All mutating + gated; wipe/
	# lock/run-script are HIGH risk (see _gate.py). Each routes to the device's
	# provider (Miradore mobile / Action1 computers) via mdm_integration.actions.
	"erpnext_enhancements.assistant_tools.remote_lock_device.RemoteLockDevice",
	"erpnext_enhancements.assistant_tools.remote_wipe_device.RemoteWipeDevice",
	"erpnext_enhancements.assistant_tools.locate_device.LocateDevice",
	"erpnext_enhancements.assistant_tools.reboot_device.RebootDevice",
	"erpnext_enhancements.assistant_tools.run_device_script.RunDeviceScript",
	"erpnext_enhancements.assistant_tools.deploy_device_patch.DeployDevicePatch",
	# v1.70.0 — read-only status tools for subsystems that previously had no AI
	# surface (Stripe Payments, QuickBooks Online sync, Accounting Document Intake
	# review queue, Closed-Won -> Project hand-off backlog). All read-only (listed
	# in _gate.py EXPLICIT_READONLY); each gates on its subsystem's DocType.
	"erpnext_enhancements.assistant_tools.stripe_payment_status.StripePaymentStatus",
	"erpnext_enhancements.assistant_tools.quickbooks_sync_status.QuickbooksSyncStatus",
	"erpnext_enhancements.assistant_tools.document_intake_queue.DocumentIntakeQueue",
	"erpnext_enhancements.assistant_tools.closed_won_handoff_status.ClosedWonHandoffStatus",
	# v1.90.0 Water Engineering — fountain hydraulic calc tools. water_calc and
	# water_design_status are read-only (EXPLICIT_READONLY); save_water_design
	# writes a Water Feature Design and is gated (APP_MUTATING, Low risk). All
	# three share the pure water_engineering.engine with the desk wizard.
	"erpnext_enhancements.assistant_tools.water_calc.WaterCalc",
	"erpnext_enhancements.assistant_tools.water_design_status.WaterDesignStatus",
	"erpnext_enhancements.assistant_tools.save_water_design.SaveWaterDesign",
	# v1.93.0 Water Engineering controls — read-only control-panel reader.
	"erpnext_enhancements.assistant_tools.control_panel_status.ControlPanelStatus",
]

# Paths are relative to the app package dir (frappe.get_app_path).
assistant_skills = [
	{
		"app": "erpnext_enhancements",
		"manifest": "data/assistant_skills.json",
		"content_dir": "data/skills",
	},
]

# ---------------------------------------------------------------------------
# Runtime framework monkeypatches
# ---------------------------------------------------------------------------
# Carried in app code so they survive `bench update` (vs. editing apps/frappe).
# Applied here because Frappe imports every app's hooks.py in every worker the
# first time it loads hooks, so this runs once per process before any patched
# path is reached. `_load_app_hooks` skips functions and `_`-prefixed names, so
# neither the import alias nor the call is mistaken for a hook. See
# monkeypatches.py for what/why — currently: stop a cached `None` (e.g. the
# `telephony` Module Def query) from crashing get_modules_from_all_apps and the
# app switcher.
from erpnext_enhancements.monkeypatches import apply as _apply_monkeypatches

_apply_monkeypatches()
