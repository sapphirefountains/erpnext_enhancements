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
	# Assign To, Retire, and Open Builder. That last one stood as a "not yet"
	# placeholder for three releases after the builder actually shipped, so anyone
	# who trusted the button never found the builder — it now routes to the page.
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
		# handoff_meeting_dialog.js is NOT listed here: it moved into
		# erpnext_enhancements.bundle.js (app_include_js) in v1.263.0, because the
		# Closed-Won prompt now opens it from the Kanban board and the list view
		# too. Global load also retires the load-order pairing this entry used to
		# carry.
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
		# step 7 ("Hold Project Launch Meeting") opens the shared meeting dialog,
		# which now loads globally from erpnext_enhancements.bundle.js — see the
		# Opportunity entry above.
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
	# Chat (ADR 0009 Phase 3). ONE hook, and it is deliberately here rather than a
	# line inside `chat.sync.outbox.finalise_new_message`.
	#
	# The counter fan-out — "publish each OTHER member's new unread total to their
	# own user room" — is what drives the room-list indicator and the floating
	# bubble's badge on a desk page where no room's document room has been joined.
	# It is a Phase 3 concern, and `finalise_new_message` is the subject of Phase 2's
	# soak proof ("exactly once per genuinely new message, zero for echoes"). Adding
	# to it would put a Phase 3 concern inside the thing the proof measures; additive
	# wiring keeps that proof measuring what it was written to measure.
	#
	# It never raises: it runs in the inserting transaction's after_insert, where an
	# exception destroys the message it exists to announce. A missing badge costs one
	# refresh.
	"Chat Message": {
		"after_insert": "erpnext_enhancements.chat.api.readstate.announce_unread",
	},
	"Task": {
		"before_save": "erpnext_enhancements.script_migrations.task.calculate_project_elapsed_time",
		"after_insert": "erpnext_enhancements.script_migrations.task.sync_task_to_google_calendar",
		"on_update": [
			"erpnext_enhancements.tasks.generate_next_task",
			"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.publish_realtime_update",
			"erpnext_enhancements.script_migrations.task.sync_project_dates_from_tasks",
		],
		"on_trash": "erpnext_enhancements.script_migrations.task.sync_project_dates_from_tasks",
		# training: warn-only certification check when a task is assigned to somebody
		# lacking a current certification for the task type. Never blocks.
		"validate": "erpnext_enhancements.training.compliance.warn_uncertified_assignee",
	},
	"Project": {
		"before_validate": "erpnext_enhancements.sync_contact.sanitize_primary_address_link",
		"before_insert": [
			# ORDER IS LOAD-BEARING, and so is the hook itself being before_insert.
			# The gate refuses a Project whose source Opportunity is Closed Won with
			# no recorded hand-off (PRO-0204, 2026-08-06 process meeting). It cannot
			# live on `validate`: create_project_from_opportunity_background sets
			# flags.ignore_validate before inserting, so a validate hook silently
			# never runs on the path that creates most projects. before_insert
			# survives both ignore_validate and ignore_permissions.
			# It runs FIRST so a refused project never seeds its tracker rows.
			"erpnext_enhancements.process_steps.enforce_handoff_gate",
			"erpnext_enhancements.process_steps.seed_process_steps",
		],
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
		# training: WARN-ONLY certification check on the assigned technician. It NEVER
		# throws -- by the time this runs a truck is usually already at the site, and
		# blocking the visit form would mean the work happens with NO RECORD AT ALL, which
		# is worse than the uncertified assignment it is flagging. Appends a comment plus an
		# orange msgprint and notifies the supervisor. Gated by Training Settings ->
		# warn_on_uncertified_dispatch. On validate, not before_submit: before_submit would
		# read as a gate.
		"validate": "erpnext_enhancements.training.compliance.warn_uncertified_technician",
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
	# Lead attribution. Lead had no doc_events block at all before v1.241.0.
	# Both handlers are inert unless the attribution Custom Fields exist on the
	# bench (they check frappe.db.has_column), which is what keeps them safe
	# during erpnext's own test bootstrap.
	"Lead": {
		"validate": [
			# Stamp custom_attribution_captured_on the first time any UTM value
			# lands. Set once — a later edit never restates the acquisition date.
			"erpnext_enhancements.crm_enhancements.attribution.stamp_capture_time",
			# The source gate. NEW records only, and gated twice in Settings
			# (lead_attribution_enabled + require_lead_source_on_lead) so it can
			# be switched off from the UI without a deploy.
			"erpnext_enhancements.crm_enhancements.attribution.enforce_source",
		],
	},
	"Opportunity": {
		"before_validate": "erpnext_enhancements.sync_contact.sanitize_primary_address_link",
		"validate": [
			# First-touch inheritance from the originating Lead (or Customer, for
			# the fountain-move path, which creates Customer-party opportunities
			# on purpose). Fills blanks only — never overwrites.
			"erpnext_enhancements.crm_enhancements.attribution.propagate_to_opportunity",
			"erpnext_enhancements.crm_enhancements.attribution.enforce_source",
		],
		"before_save": [
			"erpnext_enhancements.crm_enhancements.api.sync_opportunity_tags",
			"erpnext_enhancements.script_migrations.opportunity.stamp_won_date",
			# must run after stamp_won_date: both describe the same Closed-Won
			# moment, and the hand-off/launch deadlines are measured from it.
			# Ungated on purpose (a silent data stamp, like custom_stage_changed_on)
			# so the SLA data is already meaningful whenever a switch is flipped.
			"erpnext_enhancements.crm_enhancements.handoff.stamp_handoff_gate",
			"erpnext_enhancements.script_migrations.opportunity.validate_ranks_on_won",
			"erpnext_enhancements.script_migrations.opportunity.validate_close_reason",
			"erpnext_enhancements.script_migrations.opportunity.update_lead_status",
			"erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline.stamp_stage_change",
		],
		"on_update": [
			"erpnext_enhancements.sync_contact.sync_from_main_doc",
			"erpnext_enhancements.crm_enhancements.project_prompt.prompt_create_project_on_won",
			"erpnext_enhancements.crm_enhancements.page.sales_pipeline.sales_pipeline.publish_pipeline_update",
			# Push attribution forward onto the Customer. The Lead -> Customer
			# link only exists when erpnext built the Customer from the Lead;
			# Customer-first deals (fountain-move, and every manually created
			# account) need this direction or the Customer — which is what the
			# value-stream dashboards group by — stays unattributed. Writes with
			# db.set_value, NOT save(): re-entering every Customer hook (Drive
			# provisioning, contact sync) for a metadata-only copy is both slow
			# and a real source of side effects.
			"erpnext_enhancements.crm_enhancements.attribution.backfill_opportunity_to_customer",
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
		# Inherit attribution from the Lead erpnext built this Customer from
		# (Customer.lead_name is the reliable back-link; custom_opportunities is
		# a child table populated after insert, so it is empty at validate time).
		"validate": [
			"erpnext_enhancements.crm_enhancements.attribution.propagate_to_customer",
			# WP-5: industry required on commercial accounts. NOT reqd/
			# mandatory_depends_on, which are evaluated on every save by every
			# caller -- QBO's mapping.py inserts Customers without
			# ignore_mandatory, so a declarative rule would park every synced
			# customer in manual review. This hook exempts background jobs, bulk
			# contexts and callers that set flags.ignore_mandatory.
			"erpnext_enhancements.crm_enhancements.data_quality.enforce_industry",
		],
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
		# ---- Chat sync engine (ADR 0009 Phase 2, v1.262.0) -------------------------------
		# EVERY job below no-ops while `Chat Settings.enabled` is 0, which is how it ships.
		# They are registered dormant on purpose: a scheduler entry added later, by hand, on
		# the day chat goes live is the one that gets forgotten.
		#
		# The per-minute pair exists because this app CANNOT run a supervised worker. There is
		# no Procfile in this repo -- the bench generates one on the VM and systemd runs honcho
		# against it -- so ADR §G.4.2's recommended long-running streaming-pull consumer is not
		# a change we can commit. The cron is its stated fallback, and the cost is up to a
		# minute of latency on a coworker's message.
		#
		# `pull_inbound_events` takes a Redis lock and drains for a bounded slice of the
		# minute, then returns; the next tick re-arms it. A deploy kills it mid-slice, which is
		# survivable precisely because the puller commits the raw row BEFORE acking Pub/Sub --
		# an unacked delivery is redelivered, an acked-but-uncommitted one is gone.
		"* * * * *": [
			"erpnext_enhancements.chat.sync.pubsub.pull_inbound_events",
			# Two jobs in one entry, and the second is not just a lost-work sweeper: it is the
			# defer TIMER. frappe.enqueue reaches no RQ scheduler and RQ's own scheduling lives
			# in the queue Redis a deploy FLUSHDBs, so inbound._defer writes
			# `Chat Inbound Event.available_at` and stops -- making THIS interval the real
			# granularity of a defer. At ten minutes, a three-defer budget would be a
			# thirty-minute worst case for one message.
			"erpnext_enhancements.chat.sync.inbound.sweep_stuck_inbound_events",
		],
		# The outbox sweeper, and the reason the relay survives anything. The production deploy
		# runs `redis-cli -p 11000 FLUSHDB`, so every queued-but-unrun job is destroyed on an
		# ordinary successful release -- silently, because the enqueue already returned. Frappe
		# v16 also wires no RQ retries at all. So the queue is a latency optimisation and THIS
		# is the delivery guarantee: it re-drives `Pending` rows past `available_at` and returns
		# `In Progress` rows whose lease expired (a crashed worker) to `Pending`.
		"*/5 * * * *": ["erpnext_enhancements.chat.sync.outbound.sweep_relay_jobs"],
		# Provisioning and attachments: both are prerequisite work for a relay that is already
		# queued and waiting, so they are frequent but cheap -- each returns immediately when
		# its table has nothing pending.
		"*/10 * * * *": [
			"erpnext_enhancements.chat.sync.provisioning.sweep_pending_provisioning",
			"erpnext_enhancements.chat.sync.attachments.sweep_pending_attachments",
		],
		# Subscription renewal. An expired Workspace Events subscription is DELETED and cannot
		# be renewed -- only recreated -- and the failure is completely silent, so this is the
		# highest-consequence entry in this block. Hourly against a granted lifetime measured
		# in days is deliberate over-frequency: the job is idempotent, the expiration reminders
		# fire at T-12h and T-1h, and the cost of running it needlessly is one cheap read while
		# the cost of missing it is total inbound loss until somebody notices.
		#
		# :25 and :50, NOT :20 and :40. Those two are QuickBooks' `cdc_poll` and
		# `retry_failed_syncs`, and a duplicate key in a Python dict literal does not warn --
		# the later entry silently REPLACES the earlier one. Reusing them would have deleted
		# two live QuickBooks jobs, and nothing would have reported it. Caught by
		# tests/test_hooks_integrity.py, which exists for exactly this.
		"25 * * * *": ["erpnext_enhancements.chat.sync.subscriptions.renew_due_subscriptions"],
		# The sweep that converts a missed renewal from data loss into lag: messages.list with
		# `createTime >` the room's watermark, ingested through the SAME idempotent inbound path.
		# Offset well clear of the renewal above so a renewal storm and a sweep do not contend
		# for the same per-project read budget.
		"50 * * * *": ["erpnext_enhancements.chat.sync.reconcile.reconcile_due_rooms"],
		# Orphaned document rooms -- a linked document deleted or cancelled must not silently
		# leave a Google space nobody owns.
		"30 4 * * *": ["erpnext_enhancements.chat.sync.provisioning.sweep_orphaned_document_rooms"],
		# chat Phase 4: retire push subscriptions nothing has been delivered to in 60 days.
		#
		# A minute nothing else uses -- scheduler_events is one dict literal, so a duplicate
		# key silently REPLACES the earlier entry with no warning, and the QuickBooks hourly
		# jobs already own :00/:20/:40 while chat owns :25/:50 and 4:30am.
		#
		# Needed because the push services only ever report a subscription gone (404/410) when
		# the BROWSER deliberately unsubscribed. A phone that was wiped, reassigned, or simply
		# never opened again is never mentioned by anyone -- so without this the table only
		# grows, and every dead row in it costs one HTTPS request per notification to that
		# person, forever. Deactivates rather than deletes, so a device that comes back is
		# reactivated by its next registration and the history survives somebody asking why
		# their phone stopped buzzing.
		"45 3 * * *": ["erpnext_enhancements.chat.notifications.webpush.subscriptions.prune_stale"],
		# Semi-monthly commission report — 07:00 site TZ, DAILY on purpose even
		# though it only emails on the 1st and the 16th. The job also owns the
		# saved date window on the "Brian's Closed Won" Report Builder report, and
		# running every day is what repairs that window after a missed tick or a
		# send that died half-way; gate it to "0 7 1,16 * *" and a bad day leaves
		# the desk report showing the wrong period for up to sixteen days.
		# Frappe's own dynamic date filters cannot express a semi-monthly period
		# and are inert on Report Builder reports anyway — see the module docstring.
		"0 7 * * *": ["erpnext_enhancements.crm_enhancements.pay_period_reports.run_pay_period_cycle"],
		# Offsite backups to a Google Drive Shared Drive. The three slots are
		# deliberately clear of the cluster above (05:00, 06:00, 06:30, 07:00,
		# 07:15) — a multi-GB dump and upload must not contend with the KPI
		# snapshots or the QuickBooks pulls for the long queue.
		#
		# Both backup entry points are thin shims: they check the master switch,
		# reconcile any stranded Running row, and hand off to the long queue with a
		# 4h timeout. The dump itself never runs on the scheduler tick.
		#
		# 02:00 daily — database only.
		"0 2 * * *": ["erpnext_enhancements.offsite_backup.backup.run_daily_backup"],
		# 03:00 Sunday — database + public files + private files. An hour after the
		# nightly run so the two cannot overlap even if the daily one runs long;
		# if it somehow still is, the weekly logs a Skipped row rather than queueing
		# behind it.
		"0 3 * * 0": ["erpnext_enhancements.offsite_backup.backup.run_weekly_backup"],
		# 08:00 daily — staleness watchdog. Checks the database and full tiers
		# separately against their own thresholds, because a healthy nightly
		# database backup would otherwise mask a weekly file backup that has been
		# skipped every Sunday for months. This is the check that catches "nothing
		# ran at all": a failure email only fires when a job runs and throws.
		"0 8 * * *": ["erpnext_enhancements.offsite_backup.backup.watchdog"],
		# Hand-off SLA compliance summary — Friday 07:30 site TZ. A cron entry
		# rather than the "weekly" bucket because that bucket cannot pin a
		# weekday, and the 2026-08-06 meeting asked for Friday mornings
		# specifically. :30 keeps it clear of the 07:00/07:15 cluster above.
		"30 7 * * 5": ["erpnext_enhancements.process_steps.send_weekly_sla_digest"],
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
		# Probe every linked Drive folder and stamp the records whose folder is
		# gone, so the "Open Drive Folder" button stops opening a Google 404
		"erpnext_enhancements.google_drive.drive_sync.reconcile_drive_links",
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

# ---------------------------------------------------------------------------
# Website routes.
#
# The chat SPA (ADR 0009 Phase 3) is a single shell at www/chat.html that serves
# EVERY sub-path of /chat. The rule below is what makes a HARD REFRESH at
# /chat/room/<room>?thread=<msg>&message=<msg> render that shell instead of
# 404ing — and every deep-link acceptance criterion in Phase 3, every
# notification link Phase 4 sends, and every `chat_message` citation Phase 5
# resolves depends on it. Frappe v16 hands from_route to werkzeug's Rule
# verbatim, so the full converter set including <path:...> is available.
#
# TRAP, recorded because it is operational rather than visible in code: loading
# /chat/room/X BEFORE this rule shipped caches that URL in the `website_404`
# cache until Redis is flushed. A full deploy FLUSHDBs Redis and clears it; a
# hotfix without a restart does not. So do not advertise the route to anybody
# before the deploy carrying it has landed.
#
# The bare /chat path is matched by www/chat.html itself and needs no rule.
website_route_rules = [
	{"from_route": "/chat/<path:chat_path>", "to_route": "chat"},
]

# Jinja methods available to Print Formats / web templates. The print sandbox
# cannot parse the Water Feature Design pipe segments' fittings/components JSON
# rows, so the aggregation (DOC-0121 Fitting Schedule) and the typed design
# issues (Design Review section) are exposed as callables instead.
#
# The Project Schedule format draws Gantt bars as percentage-positioned divs, and
# a bar's left/width are a fraction of the project's whole date span. The print
# sandbox has no date arithmetic to compute that per row, and a Print Format
# renders server-side with no JavaScript (so the browser SVG renderer in
# public/js/gantt_widget/gantt_export.js cannot help) -- hence pre-computed rows.
jinja = {
	"methods": [
		"erpnext_enhancements.water_engineering.issues.we_fitting_schedule",
		"erpnext_enhancements.water_engineering.issues.we_design_issues",
		"erpnext_enhancements.project_enhancements.print_data.project_schedule_rows",
		"erpnext_enhancements.project_enhancements.print_data.project_task_rows",
	],
}

# Run BEFORE each `bench migrate` (in pre_schema_updates, before fixture sync).
before_migrate = [
	# Rebuild the app -> modules map from every app's modules.txt BEFORE model sync.
	# ADDING A MODULE TO modules.txt IS NOT ENOUGH ON AN ALREADY-INSTALLED SITE.
	# frappe.model.sync.sync_for() iterates frappe.local.app_modules, NOT modules.txt.
	# That map is snapshotted once in frappe.init() out of the redis key "app_modules"
	# and nothing in `bench migrate` rebuilds it -- SiteMigration.setUp()'s
	# frappe.clear_cache() deletes the key but does not call setup_module_map(). So a
	# migrate that starts with a stale snapshot walks the PREVIOUS release's module
	# list: a module added in the release being deployed is never walked, no DocType
	# is imported, no table is created, no Module Def is made, every post_model_sync
	# patch touching those tables burns its Patch Log entry against a missing schema --
	# and the migrate exits 0. That is how v1.261.0 shipped ten Chat DocTypes and
	# installed none of them (2026-08-09). It is a race, not a certainty: this deploy
	# FLUSHDBs the cache AFTER the migrate, so whether the key is stale depends on what
	# last wrote it (typically the once-a-minute scheduler tick).
	# before_migrate is Frappe's pre_schema_updates, i.e. before BOTH patch phases and
	# before sync_all() -- the only window where this helps. See setup/module_map.py;
	# the one-shot twin is patches/refresh_module_map.py and the CI guard is
	# tests/test_module_installability.py.
	"erpnext_enhancements.setup.module_map.refresh_app_module_map",
	# Drop stale Role Profile document locks so fixture sync can't crash with
	# DocumentLockedError. Frappe core's RoleProfile.on_update queue_action locks
	# the doc and defers "resave all users" to the long queue; the deploy's Redis
	# FLUSHDB destroys that job before it can unlock, orphaning the lock for up to
	# 3h. A second migrate inside that window then aborts here. See document_locks.py.
	"erpnext_enhancements.setup.document_locks.clear_stale_role_profile_locks",
]

# Run once, during `bench install-app`, BEFORE core's add_module_defs and sync_for.
before_install = [
	# The same stale-snapshot hole as before_migrate above, on the install path, which
	# before_migrate does not cover. Core does guard this -- but on the APP, not the
	# module: install_app only refreshes when `name not in frappe.local.app_modules`,
	# and setup_module_map(include_all_apps=True) maps every app on the bench whether
	# installed on this site or not. So erpnext_enhancements is already a key in the
	# snapshot before it is installed here, the condition is False, no rebuild happens,
	# and sync_for walks whatever module list the redis key happened to hold. Safe to
	# return None: install_app aborts only on a literal False.
	"erpnext_enhancements.setup.module_map.refresh_app_module_map",
]

# Run once, at the end of `bench install-app`.
after_install = [
	# after_migrate does NOT run during install-app -- core runs before_install,
	# after_install and after_sync only -- and install-app writes the whole of
	# patches.txt to Patch Log as already-executed. So on a fresh site neither the
	# composite indexes nor the Chat Settings singleton would exist until somebody
	# happened to run a migrate. Both callables below are the same idempotent
	# functions the after_migrate backstops use, and both are contractually
	# forbidden from raising: a failure here must never abort an app install.
	"erpnext_enhancements.patches.add_chat_indexes.ensure_chat_indexes",
	"erpnext_enhancements.patches.default_chat_settings.ensure_chat_settings",
	# Phase 2's composites, same shape and same reason as the line above it.
	"erpnext_enhancements.patches.add_chat_phase2_indexes.ensure_chat_phase2_indexes",
	# Phase 5's composites, plus the FULLTEXT index on Chat Context Chunk.body, which is
	# the whole lexical half of retrieval -- the half that makes an exact invoice number
	# findable at all. frappe.db.add_index cannot create a FULLTEXT index, so that one is
	# raw DDL and exists only if this runs.
	"erpnext_enhancements.patches.add_chat_phase5_indexes.ensure_chat_phase5_indexes",
	# Phase 4's Notification Type records. Notification Log.type is a LINK on v16, so
	# without these two rows every chat bell notification fails link validation on insert --
	# one Error Log per message and a bell that never lights.
	"erpnext_enhancements.patches.chat_phase4_notifications.ensure_chat_phase4_notifications",
	# Chat log retention. Same shape, and needed here for a different reason than the
	# others: the Chat Settings retention fields have held their defaults since Phase 1 and
	# may never be saved again, so hanging the sync only off on_update would leave the
	# `Logs To Clear` rows absent on every site that does not happen to edit the form.
	"erpnext_enhancements.chat.retention.ensure_chat_log_retention",
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
	# enhancements_core: the three customer-facing sales formats — Quotation, Sales
	# Order, Sales Invoice (WI-020). Same module and same reason as the Purchase Order
	# format above; selling has no module of its own here either. These are what the
	# customer receives from 2027-01-01, and before this they fell back to the stock
	# unbranded `* Standard` formats. Like the certificate below, this MUST sit ABOVE
	# ensure_chrome_pdf_generator so that function sees them.
	"erpnext_enhancements.enhancements_core.setup_sales_print_formats.ensure_sales_print_formats",
	# package_dispatch: the Package Dispatch Sheet Print Format (idempotent +
	# guarded; re-upserts the HTML so template edits deploy on migrate).
	"erpnext_enhancements.package_dispatch.setup_print_formats.ensure_package_dispatch_print_formats",
	# project_enhancements: the Project Schedule (task tree + HTML/CSS Gantt bars)
	# and Project Task List formats. Same idempotent-upsert shape as the others,
	# and like them it MUST sit ABOVE ensure_chrome_pdf_generator so that pass
	# sees the formats and points them at a backend.
	"erpnext_enhancements.project_enhancements.setup_print_formats.ensure_project_print_formats",
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
	# Lives in `setup` beside the starter categories, not in `gamification`, which is
	# the runtime awarding logic. This pointed at gamification and the function was
	# never written there, so every migrate since Phase 4 died on AttributeError.
	"erpnext_enhancements.training.setup.ensure_training_badges",
	# Point every Print Format at the chrome PDF backend. Must run on EVERY migrate, not
	# once as a patch: standard formats re-sync from their app's JSON, so the setting is
	# reverted by the same migrate that would have applied a patch. It also has to use
	# frappe.db.set_value, because Print Format.validate refuses ORM writes to standard
	# formats outright. LAST in this list on purpose -- it should see any format the hooks
	# above have just created. See setup_print_formats.ensure_chrome_pdf_generator.
	"erpnext_enhancements.enhancements_core.setup_print_formats.ensure_chrome_pdf_generator",
	# Keep the three superseded ERPNext Purchase Order formats out of the print
	# dropdown -- "Purchase Order - Sapphire" is the only one we print. On EVERY
	# migrate for exactly the reason above: they are standard formats and re-sync
	# from erpnext's JSON, so a one-shot patch would come undone at the next
	# migrate and look like it had worked until somebody printed a PO. The two
	# CUSTOM PO formats are genuinely deleted, once, by
	# patches/purge_purchase_order_print_formats.py -- nothing recreates those.
	# After the chrome pass on purpose: disabling a format it has already pointed
	# at chrome costs nothing, and the reverse order would leave a disabled format
	# skipped by the chrome filter and then re-enabled by a future migrate with a
	# stale generator.
	"erpnext_enhancements.enhancements_core.setup_print_formats.disable_superseded_print_formats",
	# chat: re-assert the composite indexes and composite UNIQUE constraints that
	# Frappe's DocType JSON cannot express (there is no first-class composite-index
	# field, so `(room, seq)`, `(room, client_message_id)`, `(room, user)` and the rest
	# exist ONLY because a patch created them). This is the same module the patch entry
	# in patches.txt names, called deliberately a second time, because a patch is not
	# guaranteed to run on a FRESH site: `bench install-app` marks an app's whole
	# patches.txt as already-executed, so on a new bench the patch is skipped and never
	# runs again -- and the failure is silent, because inserts succeed happily without a
	# unique constraint until the day two of them should have collided.
	# CONFIRMED 2026-08-09 from frappe version-16 source: install_app() calls
	# set_all_patches_as_completed(name), which inserts a Patch Log row for every line in
	# patches.txt without executing any of them. Still worth one SHOW INDEX FROM
	# "tabChat Message" on the next fresh bench, since only the DB settles DDL.
	# Safe either way: every index is checked against information_schema before any DDL,
	# so on the normal migrate path (where the patch already ran) this is ~11 cheap reads
	# and no writes. It is `ensure_chat_indexes`, not the patch's own `execute`, because
	# the two want opposite failure behaviour: the patch runs once and SHOULD stop the
	# migrate if a constraint cannot be created; a hook that raises here would brick
	# every future deploy instead, so the backstop logs to the Error Log and returns.
	# Position in this list does not matter -- after_migrate runs after model sync, so
	# the tables exist by the time any of these do.
	"erpnext_enhancements.patches.add_chat_indexes.ensure_chat_indexes",
	# Same two-entry-point shape, same reason, for the dormancy seed -- and it is here
	# because the index backstop is the ONLY thing that saved the 2026-08-09 deploy while
	# `default_chat_settings`, which had no backstop, is still unseeded on prod.
	# `Chat Settings` is a Single: Frappe synthesises its DocField defaults only while
	# tabSingles holds NO row for it, so an unmaterialised Single reads correctly today
	# and flips every unwritten field to None on the first partial write -- taking
	# dry_run_mode and restrict_to_whitelist to 0, the unsafe direction for both.
	# Blank-fill only, so an operator's deliberate tick is never clobbered, and it never
	# raises. One get_singles_dict and no write on a healthy migrate.
	"erpnext_enhancements.patches.default_chat_settings.ensure_chat_settings",
	# chat Phase 2 (v1.262.0): same two-entry-point shape and the same reason as
	# ensure_chat_indexes above. unique(message, revision_no) on Chat Message Revision and
	# the crashed-worker sweep's (status, lease_expires_at) exist ONLY because a patch
	# created them, and `bench install-app` marks the whole of patches.txt executed without
	# running any of it. Checks information_schema before any DDL, so on the normal migrate
	# path this is five cheap reads and no writes. Never raises -- a hook that raised here
	# would brick every future deploy rather than report one bad constraint.
	"erpnext_enhancements.patches.add_chat_phase2_indexes.ensure_chat_phase2_indexes",
	# chat Phase 5 (v1.272.0): the retrieval composites, and one thing the other two index
	# backstops do not carry. unique(room, first_seq) on Chat Context Chunk is correctness
	# rather than speed -- the indexer is a retried background job, so two workers building
	# the same chunk is scheduled rather than unlikely, and the result is the same
	# conversation in the candidate set twice.
	#
	# The FULLTEXT index on Chat Context Chunk.body is why this specifically needs the
	# after_migrate half. `VERIFY:` whether bench migrate drops a hand-added FULLTEXT index
	# -- add it, migrate twice, SHOW INDEX. If it does, the lexical tier degrades INVISIBLY
	# after every deploy: exact-string matching stops working and nothing raises. Re-creating
	# it here makes the bad answer to that question a one-migrate window instead of forever.
	"erpnext_enhancements.patches.add_chat_phase5_indexes.ensure_chat_phase5_indexes",
	# chat Phase 4 (notifications): the two `Notification Type` records, plus the presence
	# retune. Both need the after_migrate half specifically, for opposite reasons.
	#
	# The records, because `Notification Log.type` is a Link on v16 and inserting a row of an
	# uninstalled type is a validation failure -- so a site that somehow reached Phase 4's
	# code without this line would log one error per message and light no bell at all.
	#
	# The retune, because Frappe synthesises a Single's DocField defaults ONLY while tabSingles
	# holds no row. Production's row exists and holds Phase 3's 30 s / 75 s (measured
	# 2026-08-11), so changing the shipped default moves nothing there; the patch rewrites the
	# stored pair to 20 s / 55 s, and only where it still equals what Phase 3 shipped. An
	# operator's own number is left alone.
	#
	# Idempotent: two exists-checks and a get_singles_dict on a healthy migrate, no writes.
	# Never raises.
	"erpnext_enhancements.patches.chat_phase4_notifications.ensure_chat_phase4_notifications",
	# Chat log retention. Same shape, and needed here for a different reason than the
	# others: the Chat Settings retention fields have held their defaults since Phase 1 and
	# may never be saved again, so hanging the sync only off on_update would leave the
	# `Logs To Clear` rows absent on every site that does not happen to edit the form.
	"erpnext_enhancements.chat.retention.ensure_chat_log_retention",
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
	# KPI Snapshot: a department manager sees their own departments' numbers only.
	# The DocPerms had to widen from System-Manager-only for the KPI assistant tool
	# to be visible to the people who own the numbers, and a DocPerm is doctype-wide
	# -- without this, `read` on the doctype would have meant read on every
	# department. api/kpi.py::_can_view decides which; this enforces it where Frappe
	# actually checks reads.
	"KPI Snapshot": "erpnext_enhancements.kpi_dashboards.permissions.get_permission_query_conditions",
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
	# Chat (ADR 0009 §F.18): row-level scoping is MEMBERSHIP, not role. Chat Room is
	# the only chat doctype carrying a DocPerm at all (`read` for "Chat User"), so it is
	# the only one where this hook is the live gate -- the other three ship with an
	# empty `permissions` array, which refuses everyone but Administrator before any
	# hook is consulted. They are registered anyway, deliberately: the ADR's standing
	# obligation is that the pair must already exist the day somebody adds a DocPerm row
	# so they can look at a message in the desk, and these are the tested expression of
	# the membership rule that the package's raw SQL reuses (permission hooks do NOT
	# protect frappe.db.sql -- see chat/permissions.py's review checklist).
	# Chat Room is active membership only; Chat Message / Chat Attachment additionally
	# let a departed member read history up to their `left_seq` (CQ-10).
	"Chat Room": "erpnext_enhancements.chat.permissions.chat_room_query",
	"Chat Room Member": "erpnext_enhancements.chat.permissions.chat_room_member_query",
	"Chat Message": "erpnext_enhancements.chat.permissions.chat_message_query",
	"Chat Attachment": "erpnext_enhancements.chat.permissions.chat_attachment_query",
}

has_permission = {
	# The single-document counterpart of the KPI Snapshot query condition above.
	# A query condition filters lists; this is what refuses a direct read of one
	# snapshot by name.
	"KPI Snapshot": "erpnext_enhancements.kpi_dashboards.permissions.has_permission",
	"Travel Trip": "erpnext_enhancements.travel_management.permissions.has_permission",
	"Managed Device": "erpnext_enhancements.device_management.permissions.has_permission",
	"Sapphire Maintenance Record": "erpnext_enhancements.sapphire_maintenance.permissions.has_permission",
	"Training Assignment": "erpnext_enhancements.training.permissions.assignment_has_permission",
	"Training Attempt": "erpnext_enhancements.training.permissions.attempt_has_permission",
	"Training Attempt Question": "erpnext_enhancements.training.permissions.attempt_question_has_permission",
	"Training Completion": "erpnext_enhancements.training.permissions.completion_has_permission",
	"Training Certificate": "erpnext_enhancements.training.permissions.certificate_has_permission",
	"Training Signoff": "erpnext_enhancements.training.permissions.signoff_has_permission",
	# Chat: the twin of every query condition above, and parity here is the house
	# doctrine -- ten and ten before this block, four and four after it.
	# "Chat Room" is not just the single-document gate: it IS the realtime security
	# boundary (invariant I8). socket.io's `doc_subscribe` calls back into Python and
	# runs the full document-level permission stack, including this hook, under the
	# joining user's own session before it joins `doc:Chat Room/<name>`. Get it right
	# and socket security is free; get it wrong and realtime leaks message bodies with
	# every REST endpoint still locked down.
	# On v16 a has_permission hook that returns None DENIES, so every path in these
	# four returns an explicit bool, exception paths included.
	"Chat Room": "erpnext_enhancements.chat.permissions.chat_room_has_permission",
	"Chat Room Member": "erpnext_enhancements.chat.permissions.chat_room_member_has_permission",
	"Chat Message": "erpnext_enhancements.chat.permissions.chat_message_has_permission",
	"Chat Attachment": "erpnext_enhancements.chat.permissions.chat_attachment_has_permission",
}

# Chat notifications (ADR 0009 Phase 4) may NEVER be emailed, and this hook is what makes
# that structural rather than a default somebody can undo.
#
# `Notification Log.after_insert` calls send_notification_email() whenever
# is_email_notifications_enabled_for_type(for_user, type) is true. That predicate consults
# get_skip_email_types() FIRST -- before it reads the user's own Notification Settings -- so a
# type listed here cannot be emailed by anybody, including a user who has explicitly ticked it
# on. A per-user default would have been defeated by the first person who re-enabled it.
#
# Decision #3 is "at most two notification surfaces, and neither is email". At the measured
# volume the alternative is not a nuisance, it is an incident: 600 messages a day fanned out
# to a ten-person room is 12,000 emails a day out of a mail domain with a reputation to lose.
#
# Two consequences worth knowing rather than discovering:
#   - Frappe's own hooks.py lists "Alert" here, so this MERGES to ["Alert", "Chat Message",
#     "Chat Mention"] rather than replacing anything.
#   - Registering a type here makes the desk's "enable email for all users" button THROW for
#     it ("{0} never sends email, so it cannot be enabled for users."). That throw is the
#     assertion we want, not a bug to work around.
#
# The names are keys, not labels: they must match the `Notification Type` records installed by
# chat_phase4_notifications, and renaming one silently re-enables email for it.
notification_skip_email_types = ["Chat Message", "Chat Mention"]

# `tabNotification Log` has grown untrimmed since this site was built, and nothing anywhere
# was ever going to stop it.
#
# Measured on production 2026-08-11: 9,717 rows, oldest 2025-07-09, and it is registered for
# retention in NEITHER `frappe/hooks.py` NOR `erpnext/hooks.py` -- checked against both hook
# dicts. `tabLogs To Clear` holds fifteen rows and Notification Log is not one of them. So
# every bell notification this site has ever produced is still on disk.
#
# One line fixes it, because the framework does the rest: the next `daily_maintenance` run
# calls LogSettings.add_default_logtypes(), which reads this hook and appends the missing
# `Logs To Clear` row itself. No patch, no fixture, no UI step.
#
# THE NUMBER IS CHOSEN ONCE AND NEVER REVISITED, so it is worth knowing why it is 90.
# `add_default_logtypes` only appends rows that are ABSENT -- it never updates an existing
# one -- so changing this value later has no effect on a site that already has the row, and
# the only remedy is editing `Logs To Clear` by hand on a bench. The site's own convention
# settled it: all fifteen existing rows are 90 days (bar DuckDB Sync at 45), including Error
# Log, whose hook value is 14 -- live proof that the hook loses to an existing row. 90 trims
# 6,322 of the 9,717 rows on the first run and matches everything around it.
#
# Two more mechanics that make this safe rather than merely small:
#   - `remove_unsupported_doctypes()` runs FIRST in run_log_clean_up and deletes the row of
#     any doctype whose controller has no `clear_old_logs(days)` -- retention would then stop
#     silently and permanently. Verified against the deployed build: NotificationLog
#     implements it, taking `days`.
#   - `frappe.get_hooks` merges dict-valued hooks across apps into a list per key and takes
#     `retentions[-1]`, so a second app declaring Notification Log would win on install
#     order. Nothing else declares it today.
#
# It also unlocks the one-time catch-up: `clear_log_table("Notification Log")` raises
# ValidationError("Unsupported logging DocType") for anything absent from this hook, and that
# helper is the only feasible way to clear thirteen months in one pass -- it copies the recent
# rows into a new table and swaps them, rather than issuing a DELETE across most of a table.
#
# Deliberately NOT extended to the chat log tables. `Chat Relay Job` and `Chat Inbound Event`
# both implement `clear_old_logs` and are both unregistered, so their retention is dead code
# today -- but Chat Settings also carries `relay_job_retention_days` and
# `inbound_event_retention_days`, and declaring a static number here while those fields claim
# to control it would ship exactly the lying-settings-field trap Phase 4 just removed from the
# presence constants. Wiring or removing those fields is chat work and does not belong in a
# standalone repo fix.
default_log_clearing_doctypes = {"Notification Log": 90}

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
	# training_course_catalog is the course-shaped third: what exists, how each is
	# configured, and aggregate assignment/attempt counts. Its item_analysis is
	# aggregate ONLY and withholds questions below a small-n threshold -- a success
	# rate over three people is a statement about one identifiable person's answer
	# wearing a percentage as a disguise.
	"erpnext_enhancements.assistant_tools.training_course_catalog.TrainingCourseCatalog",
	"erpnext_enhancements.assistant_tools.maintenance_day_board.MaintenanceDayBoard",
	"erpnext_enhancements.assistant_tools.maintenance_contract_status.MaintenanceContractStatus",
	"erpnext_enhancements.assistant_tools.maintenance_visit_history.MaintenanceVisitHistory",
	"erpnext_enhancements.assistant_tools.maintenance_site_briefing.MaintenanceSiteBriefing",
	"erpnext_enhancements.assistant_tools.project_status_overview.ProjectStatusOverview",
	"erpnext_enhancements.assistant_tools.project_procurement_status.ProjectProcurementStatus",
	# The physical counterpart of procurement_status: which suppliers have goods
	# waiting and where they are. Strips `api_key` from the underlying reply --
	# that is a live billable Google Maps BROWSER key, and an MCP client is not a
	# browser. Suppliers with no address are reported, not filtered: dropping them
	# produces a shorter route that is quietly wrong.
	"erpnext_enhancements.assistant_tools.project_pickup_route.ProjectPickupRoute",
	# Contracts: where each stands in the e-signature flow. Returns none of the
	# signing evidence -- token hashes, the agreement text as signed, the signature
	# image, signer IP, user agent, consent wording. days_out is measured from
	# first_sent_on, because every reminder rewrites sent_on and a figure driven by
	# that reports the most-chased contract as the freshest.
	"erpnext_enhancements.assistant_tools.contract_signing_status.ContractSigningStatus",
	# KPI cockpit reader. Unqualified calls return only Watch/Bad values across the
	# departments the caller may see -- nine departments in full is a context bomb
	# that buries the four numbers that matter. refresh_kpi_dashboard is NOT
	# exposed: it rebuilds and commits.
	"erpnext_enhancements.assistant_tools.kpi_dashboard_status.KpiDashboardStatus",
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
