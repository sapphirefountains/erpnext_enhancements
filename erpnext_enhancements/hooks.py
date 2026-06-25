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
	"Opportunity": [
		"public/js/opportunity.js",
		"public/js/crm_enhancements/opportunity.js",
		"public/js/global_enhancements/unified_tab_controller.js",
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
		"project_enhancements/doctype/project/project.js",
		"public/js/project_enhancements/project_form_script.js",
		"public/js/project_enhancements/project_brief.js",
		"public/js/project_migrated_scripts.js",
		"public/js/project_enhancements/process_steps.js",
		"public/js/contracts.js",
		"public/js/global_enhancements/drive_folder_button.js",
	],
	"Master Project": ["public/js/global_enhancements/unified_tab_controller.js"],
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
		"public/js/global_enhancements/drive_folder_button.js",
		"public/js/stripe_payments/customer_autopay.js",
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
	"Purchase Order": ["public/js/vue.global.js", "public/js/comments.js", "public/js/procurement_links.js"],
	"Material Request": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/procurement_links.js",
	],
	"Supplier": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"public/js/global_enhancements/unified_tab_controller.js",
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
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"Address": {
		"before_save": "erpnext_enhancements.script_migrations.address.set_full_address",
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
	"Opportunity": {
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
	"Contact": {
		# Title field custom_full_name_and_role = "First Last-Party" (ported from a
		# disabled Server Script; see script_migrations/contact.py)
		"validate": "erpnext_enhancements.script_migrations.contact.set_full_name_and_role",
		"on_update": "erpnext_enhancements.sync_contact.sync_from_contact",
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	"Employee": {
		# Cell Number -> linked User.phone (Call via Triton dials it)
		"on_update": "erpnext_enhancements.sync_contact.sync_employee_phone_to_user",
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
	},
	"daily": [
		"erpnext_enhancements.project_enhancements.send_project_start_reminders",
		"erpnext_enhancements.tasks.predictive_maintenance_scheduling",
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
	],
	"hourly": [
		"erpnext_enhancements.quickbooks_online.core.tasks.refresh_token_if_needed",
		"erpnext_enhancements.quickbooks_online.core.tasks.cdc_poll",
		"erpnext_enhancements.quickbooks_online.core.tasks.retry_failed_syncs",
		"erpnext_enhancements.tasks.nudge_unsubmitted_maintenance_forms",
		"erpnext_enhancements.ai_governance.tasks.expire_stale_pending_actions",
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
		"erpnext_enhancements.stripe_payments.core.tasks.retry_failed",
	],
	"weekly": [
		"erpnext_enhancements.tasks.suggest_truck_restocks",
	],
}

# Ship per-session data to the desk client (frappe.boot.*).
# Currently: the live-collab doctype allowlist (frappe.boot.collab_doctypes),
# read from ERPNext Enhancements Settings — see boot.py and api/collab.py.
extend_bootinfo = "erpnext_enhancements.boot.boot_session"

# Run after each `bench migrate` (from global_enhancements)
after_migrate = [
	"erpnext_enhancements.setup.custom_fields.create_primary_contact_fields",
	"erpnext_enhancements.setup.custom_fields.create_opportunity_winloss_fields",
	"erpnext_enhancements.setup.supplier_groups.create_supplier_group_customizations",
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
			[
				"name",
				"not in",
				[
					"User-hide_my_private_information_from_others",  # lms
					"User-user_category",  # lms
					"User-verify_terms",  # lms
					"User-assistant_enabled",  # frappe_assistant_core
					"Sapphire Maintenance Record-workflow_state",  # frappe workflow engine
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
	{"dt": "Workflow", "filters": [["document_type", "=", "Sapphire Maintenance Record"]]},
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
				],
			]
		],
	},
	{"dt": "Workflow Action Master", "filters": [["name", "in", ["Request Review", "Approve & Submit"]]]},
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
				],
			]
		],
	},
	{
		"dt": "Number Card",
		"filters": [["name", "in", ["Total Calls", "High Risk Calls", "Missed Calls", "Avg CSAT", "Active Projects", "Overdue Tasks", "Avg Project Completion %", "Projects Completed", "Open Opportunities", "Open Pipeline Value", "Closed-Won Opportunities", "Active Leads", "Open Purchase Orders", "Open PO Value", "Pending Material Requests", "QuickBooks Failed Syncs", "QuickBooks Records Mapped", "QuickBooks Open Conflicts", "QuickBooks Pending Review", "AR Outstanding", "Overdue Sales Invoices", "AP Outstanding", "Draft Sales Invoices"]]],
	},
	{"dt": "Dashboard", "filters": [["name", "in", ["Call Center", "Project Delivery", "Sales Pipeline", "Procurement", "Executive Summary", "QuickBooks Online", "Finance Health"]]]},
	# Public legal pages (guest-accessible Web Pages). stripe_payments adds the
	# payment/surcharge + refund policies (counsel-review-pending).
	{"dt": "Web Page", "filters": [["name", "in", ["eula", "privacy-policy", "payment-terms", "refund-policy"]]]},
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
}

has_permission = {
	"Travel Trip": "erpnext_enhancements.travel_management.permissions.has_permission",
	"Managed Device": "erpnext_enhancements.device_management.permissions.has_permission",
}

ignore_links_on_delete = ["User Form Draft"]

portal_menu_items = [
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
