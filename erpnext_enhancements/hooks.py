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
# The capture widget (WI-079 slice 2) is deliberately NOT here: web_include_js is emitted on
# every website page, /pay and /contract-sign included. Its recorder ships in the Desk bundle
# and in capture.bundle.js, which only the allowlisted templates include (kiosk, feedback,
# itinerary, travel_guidelines). tests/test_feedback_capture_surface.py pins both.

# Help menu -> "Report a problem" (WI-079 slice 2; frappe/hooks.py standard_help_items shape).
# is_standard=1, so migrate syncs it and deleting this entry removes it. Navbar sync adds a
# label only when it is missing, so change the label, not just the action, to update it.
# The action is a JS expression (frappe.utils.eval) and must not start with this app's
# dotted name: test_hook_targets_resolve would read it as a Python path.
standard_help_items = [
	{
		"item_label": "Report a Problem",
		"item_type": "Action",
		# open() rejects when the report form cannot load; frappe.utils.eval ignores the result,
		# so without the catch a failed load would do nothing at all. /feedback is the fallback.
		"action": "window.ee_capture ? window.ee_capture.open().catch(function () { window.open('/feedback'); }) : window.open('/feedback')",
		"is_standard": 1,
	},
]

doctype_js = {
	# training: the Course form's doors into the authoring flow — New Draft
	# Version, Send For Review, Publish (Training Manager only, and it asks the
	# Minor-Edit vs Material-Change question explicitly rather than defaulting it),
	# Assign To, Retire, and Open Builder. That last one stood as a "not yet"
	# placeholder for three releases after the builder actually shipped, so anyone
	# who trusted the button never found the builder — it now routes to the page.
	"Training Course": ["public/js/training/training_course.js"],
	# training: the Evaluation form's "Record Outcome" button, which files a real
	# Training Signoff through the existing engine (evaluations.record_evaluation)
	# rather than saving a verdict on the form. In public/js like the Course script
	# above -- NOT the doctype-folder file (see the double-load note below).
	# training (Phase 6 D6): the door, on the record that says you owe a course.
	# This doctype had NO form script until v1.429.1 -- a learner could open the row
	# telling them a course is due and there was nothing on it that would take them
	# to it. Routes with frappe.set_route rather than an href: /app is a
	# website_redirect to /desk in v16, so a hand-built link costs a full reload
	# plus a hop and is not intercepted by the router.
	"Training Assignment": ["public/js/training/training_assignment.js"],
	# training (Phase 6 D9): "Preview as a learner" on one lesson. An author fixing a
	# typo in a summary had no way to see the result short of opening the whole
	# canvas and navigating back. Opens /training_preview rather than mounting a
	# player in a dialog: the draft payload is built server-side by _split_lesson
	# and no endpoint returns it as JSON, so an in-form player would mean rebuilding
	# it in JavaScript -- the ~640 lines the classic builder carried and the canvas
	# port deliberately did not.
	"Training Lesson": ["public/js/training/training_lesson.js"],
	"Training Evaluation": ["public/js/training/training_evaluation.js"],
	# training: the Submission form's "Grade" button, which files a real grade
	# through submissions.grade_submission (stamps grader, times it, mails the
	# learner) rather than a raw field edit. In public/js like the Evaluation script
	# above -- NOT the doctype-folder file (the double-load trap).
	"Training Submission": ["public/js/training/training_submission.js"],
	# training: the GCS signing key goes in through a dialog, not the field. The
	# field is a Password, which Frappe renders as a SINGLE-LINE masked input --
	# a control that cannot take a 2 KB multi-line service-account JSON by paste
	# without mangling it, and being masked it then hides the damage. Also carries
	# the Test GCS Connection action.
	"Training Settings": ["public/js/training/training_settings.js"],
	# inventory (v1.521.0): the Warehouse form's doors to the QR label print page
	# (/warehouse-labels) and to the Stock Scan page (/stock-scan) the label opens.
	# A location gets "QR Label" + "Open Stock Scan"; a group gets "Print QR Labels"
	# for everything beneath it. Both are www routes, so the buttons window.open them.
	"Warehouse": ["public/js/warehouse_stock_scan.js"],
	# security: the Drive service-account key goes in through a dialog, not the
	# field. It is a Password (v1.211.0, was Code and therefore cleartext), and a
	# Password renders as a single-line masked input that mangles a multi-line key
	# on paste.
	"Project Folder Google Drive Settings": ["public/js/google_drive/drive_settings.js"],
	# ER-2026-420503: an Item created inline from Asset.item_code saved fine and then
	# disappeared from the field, because asset.js filters that link on is_fixed_asset=1
	# and the Item Quick Entry dialog defaults that box to 0. This seeds the dialog --
	# and it is loaded HERE, on the Asset form only, because it subclasses the global
	# frappe.ui.form.ItemQuickEntryForm; the subclass additionally checks
	# frappe._from_link so that Item quick entries elsewhere in the same session (the
	# class stays loaded) are untouched. Also warns when Location or fixed-asset Items
	# are still empty, which is the rest of why the form looked broken.
	"Asset": ["public/js/asset_management/asset_form.js"],
	"Opportunity": [
		"public/js/opportunity.js",
		"public/js/crm_enhancements/opportunity.js",
		"public/js/party_naming_advisor.js",
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
		"public/js/party_naming_advisor.js",
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
		# quality (WI-075 sub-phase I): which inspection milestones have come round on this
		# project, and what is in the way. Shows EVERY milestone, not only the due ones -- a
		# list of just what is due loses "why has that one not come round" and "why is that one
		# not showing at all". Two of its states are findings rather than statuses: `blocked`
		# (due, and nobody has written the checklist) and `unknown` (the build status is not a
		# recognised option, so due-ness is not knowable). Generates nothing.
		"public/js/quality/project_inspections.js",
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
		# v1.337.0 -- the naming advisor. Advisory only: a headline on refresh (record-only
		# checks, no corpus read) plus two on-demand buttons; nothing in the script blocks a
		# save. From 2026-10-01 (POL-0602; shipped in v1.532.0) the server refuses a NEW Item
		# for two of its findings only --
		# see the "Item" doc_event (inventory_enhancements.item_naming_guard).
		"public/js/item_naming_advisor.js",
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
		# training: "how is this person doing?" -- the question a manager arrives
		# with, which every other training surface answered about a course, a cohort
		# or the whole org. Manager-gated and drawn only when the Employee has a
		# user_id, because training records belong to a User.
		"public/js/training/employee_training.js",
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
		# training: "are the people who operate this client's fountain trained?" --
		# completions with expiry computed against today. The endpoint answering it
		# had no caller until v1.334.0.
		"public/js/training/training_customer_view.js",
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
		# Receive Items (ER-2026-458194): what arrived on a submitted order becomes a real
		# Purchase Receipt through api/procurement.receive_items, so received_qty, the
		# status pill and the Order Stage all move on their own.
		"public/js/po_receive_items.js",
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
		# procurement: the "Pick Sheet" toolbar button — every open job's material
		# still sitting at THIS vendor's counter, so a crew driving there clears it
		# in one trip instead of one trip per project. The map file must load first:
		# it owns the dialog, the optimiser and the printed sheet, and exports the
		# launcher the button calls on `erpnext_enhancements.pick_routing`. Same file
		# as the Project button — one implementation, two scopes, deliberately not
		# forked, because two sheets that disagree about whether a PO is outstanding
		# is the failure docs/pick-routing-map-po-details.md was written to avoid.
		"public/js/project_enhancements/pick_routing_map.js",
		"public/js/procurement/supplier_pick_sheet.js",
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
		# training: grant/revoke a client contact's portal login. training/portal.py
		# has held the whole flow since v1.215.0 with no caller at all, so the only
		# way to put a client on the portal was to build the User by hand and
		# remember the role -- the sequence that module exists to stop.
		"public/js/training/training_portal_access.js",
	],
	"Address": [
		"public/js/vue.global.js",
		"public/js/comments.js",
		"project_enhancements/doctype/address/address.js",
		# v1.339.0 -- the party-naming advisor, bound to Project, Opportunity and Address.
		# Advisory only: a headline on refresh from a record-only check (no corpus read) and
		# a 'Why?' button. There is deliberately no validate hook on any of the three -- 769
		# of 823 Opportunity titles are a bare party name, so anything that blocked a save
		# would fire constantly on records that predate the rule.
		"public/js/party_naming_advisor.js",
	],
	# NOT listed here, on purpose: this app's OWN DocTypes' form scripts. Frappe loads
	# `<module>/doctype/<name>/<name>.js` on its own (FormMeta.add_code, v16) and then
	# appends every doctype_js entry to the SAME string with no dedupe, so listing that
	# file here evaluates it twice inside one Function body. Duplicate `function`
	# declarations survive that; a top-level `const` is a SyntaxError and the form loses
	# every button (Plaid Banking Settings shipped that way; so did its predecessor).
	# tests/test_hooks_integrity.py fails the build on such an entry. This hook is for
	# scripts under public/ and for scripts bound to erpnext's / frappe's DocTypes
	# (Managed Device, QuickBooks Online Settings and Plaid Banking Settings are
	# therefore absent -- their scripts sit in their doctype folders and load anyway).
	# accounting_intake
	"Document Intake": "public/js/accounting_intake/document_intake.js",
	# quickbooks_online write-back button (intake-created PI / Payment Entry)
	"Purchase Invoice": "public/js/quickbooks_online/qbo_writeback_button.js",
	"Payment Entry": "public/js/quickbooks_online/qbo_writeback_button.js",
	# stripe_payments
	"Stripe Payments Settings": "public/js/stripe_payments/stripe_payments_settings.js",
	"Sales Invoice": "public/js/stripe_payments/sales_invoice_pay_button.js",
	"Stripe Payment": "public/js/stripe_payments/stripe_payment.js",
	# plaid_banking: no entry. The Test / Refresh / "Map Plaid accounts" buttons live in
	# plaid_banking/doctype/plaid_banking_settings/plaid_banking_settings.js, which Frappe
	# auto-loads (see above). Linking a bank is the native ERPNext flow (Plaid Settings ->
	# "Link a new bank account"; Bank -> "Refresh Plaid Link"); nothing of ours touches
	# Plaid Link, and nothing of ours may be keyed "Plaid Settings": that name is
	# erpnext's, and this app shipping a DocType under it overwrote the native record
	# (patches/rename_plaid_settings_doctype).
	# fountain_move — triage actions (retry / spam) and jump-to-created-record
	"Fountain Move Request": "public/js/crm_enhancements/fountain_move_request.js",
	"Fountain Move Invite": "public/js/crm_enhancements/fountain_move_invite.js",
	# quality (WI-075 sub-phase G): the Acknowledge button on a Critical non-conformance, and
	# who is still silent. Keyed here because `Non Conformance` is ERPNext's DocType -- the
	# Quality DocTypes this app OWNS must never appear in this dict, since frappe already loads
	# <module>/doctype/<name>/<name>.js and doctype_js appends to the same string with no
	# dedupe, so a top-level `const` becomes a SyntaxError and the form loses every button.
	"Non Conformance": "public/js/quality/non_conformance.js",
}

doctype_list_js = {
	"Opportunity": [
		"public/js/opportunity_list.js",
		"public/js/crm_enhancements/opportunity_list.js",
		"public/js/crm_enhancements/opportunity_kanban_totals.js",
	],
	# supplier_list.js also carries the "Pick Sheet" bulk action (tick several
	# vendors, get one run across all of them), so the map file it calls into has
	# to load on the LIST as well as on the form — doctype_js does not reach list
	# views, and the launcher lives in that file.
	"Supplier": [
		"public/js/project_enhancements/pick_routing_map.js",
		"public/js/global_enhancements/supplier_list.js",
	],
	"Task": "public/js/project_enhancements/task_gantt.js",
	# training (WI-072) — the moment somebody decides to build a course. "New" gives
	# an empty form and asks the author to invent the content and the shape at the
	# same time, which is where authoring stops for most people. This adds two ways
	# in beside it: start from a shape, or draft it with AI. Both land on the same
	# unpublished Draft behind the same review gate.
	"Training Course": "public/js/training/training_course_list.js",
	# training (Phase 6 D6): "Open training", plus a real indicator per status.
	# permission_query_conditions already scopes this list to the learner's own
	# rows, so for the fifteen people holding Training Learner this list IS "what
	# I owe" -- which is why the learner workspace links straight at it.
	"Training Assignment": "public/js/training/training_assignment_list.js",
	"File": "public/js/global_enhancements/file_list.js",
	"Item": "public/js/item_list.js",
	# procurement — the Order Stage pill: a real colour per stage (frappe's
	# guess_colour() matches none of the seven names, so all seven came out grey),
	# click-to-change from the row, and a bulk "Set Order Stage" action. EXTENDS
	# erpnext's own purchase_order_list.js rather than replacing it.
	"Purchase Order": "public/js/purchase_order_list.js",
	"Call Log": "public/js/global_enhancements/call_log_list.js",
	"Document Intake": "public/js/accounting_intake/document_intake_list.js",
	# fountain_move — "Send Intake Link" / "Copy Public Link" + status indicators
	"Fountain Move Request": "public/js/crm_enhancements/fountain_move_request_list.js",
	# travel_management — "+ Add Travel Trip" opens the Plan a Trip page (the
	# step-by-step entry, travel_management/page/plan_a_trip) instead of a blank
	# form, via frappe's own listview_settings.primary_action hook.
	"Travel Trip": "public/js/travel/travel_trip_list.js",
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
	# quality (WI-075 sub-phase E). NOT an optimisation -- a precondition, and it must never be
	# separated from the `Quality Action-status-options` Property Setter that ships with it.
	# ERPNext's entire QualityAction controller is one line:
	#     self.status = "Open" if any([d.status == "Open" for d in self.resolutions]) else "Completed"
	# Two consequences. `any([])` is False, so an action with NO resolution rows -- which is
	# exactly what a punch-list item is -- saves as Completed, and every punch item would be
	# born closed. And once the Property Setter replaces Open/Completed with the five-state
	# lifecycle, that line writes a literal the field no longer offers, so _validate_selects
	# raises on EVERY save of the doctype, not only on ones this app makes. See ADR-0012.
	"Quality Action": "erpnext_enhancements.quality.overrides.quality_action.QualityAction",
}

doc_events = {
	# quality (WI-075 sub-phase E): a failed check becomes a Non-Conformance and a corrective
	# action. on_submit and not validate -- an inspection in progress has failures in it that
	# are about to be corrected on the spot, and raising an NCR per keystroke would make the
	# NCR list unusable. One NCR per FAILED CHECK, not per inspection, because each traces to a
	# different contracted standard and each closes separately. Never raises: losing a signed
	# inspection to protect its follow-up would be the wrong trade.
	"Project Quality Inspection": {
		# quality (WI-075 sub-phase H): warn when the template asked for a qualification the
		# inspector does not have. ADVISORY and never throws -- the company has two Senior
		# Technicians and no Masters, so a hard gate would routinely stop an inspection being
		# RECORDED rather than stop unqualified work. Gated on
		# `Quality Settings.advisory_inspector_qualification`, which ships off.
		"validate": "erpnext_enhancements.quality.inspector_advisory.warn_unqualified_inspector",
		"on_submit": "erpnext_enhancements.quality.routing.on_submit",
	},
	# quality (WI-075 sub-phase G): a Critical NCR pages the PM, Production Manager and
	# President, with an acknowledgement row per person. `on_update` and not `after_insert`,
	# because routing.py raises every NCR Minor on purpose -- severity is a judgement about
	# consequence that no checklist row carries -- so the alert has to fire on the SAVE where a
	# person decides this one is Critical. Idempotent on `custom_critical_alert_sent_on`.
	# The handler only enqueues, with enqueue_after_commit=True: `Document.hook`'s compose
	# increments frappe.db._disable_transaction_control around a doc_events handler, so it
	# cannot commit, and a worker starting before the commit would read the old severity.
	"Non Conformance": {
		"on_update": "erpnext_enhancements.quality.critical_alerts.on_ncr_update",
	},
	# quality (WI-075 sub-phase J): a period review that computes its own numbers. ERPNext
	# generates the review and copies the goal's objectives into it, then stops -- the actuals
	# are blank and somebody types a verdict. This fills them in and re-derives the verdicts,
	# then calls core's own set_status() again, because core already ran it before any actual
	# existed. Never raises: a review with Open rows and a note saying why beats a lost save.
	"Quality Review": {
		"validate": "erpnext_enhancements.quality.reviews.on_review_validate",
	},
	# quality (WI-075 sub-phase J): a project goal may only meet or exceed the company-wide
	# target for the same measure. No class override needed here, unlike Quality Action --
	# core's QualityGoal.validate is literally `pass`. Enforcement is a Quality Settings dial
	# (Off / Warn / Block) and ships on Warn.
	"Quality Goal": {
		"validate": "erpnext_enhancements.quality.reviews.on_goal_validate",
	},
	# quality (WI-075 sub-phase J): fill an empty meeting agenda from what the period actually
	# holds -- failed reviews, open Critical NCRs, reopened fixes, overdue actions. Only when the
	# agenda is empty, the same courtesy core extends to a review's objectives.
	"Quality Meeting": {
		"validate": "erpnext_enhancements.quality.reviews.on_meeting_validate",
	},
	# The `Chat Message` after_insert unread fan-out was removed in v1.426.0 with the
	# rest of the chat module (ADR 0011). It was the app's only chat doc_event.
	"Task": {
		"before_save": "erpnext_enhancements.script_migrations.task.calculate_project_elapsed_time",
		# after_insert used to sync every Task into one shared Google Calendar
		# (all tasks, everyone who could see the calendar) — removed v1.346.0 in
		# favour of nothing, deliberately: see script_migrations/task.py.
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
		# WI-075 sub-phase M. Derives the per-category budget rollup and, when category lines
		# exist, the project total from them. It does nothing and costs nothing on a project with
		# no budget lines, which is every project on prod today. Appended into THIS dict rather
		# than added as a second "Project" key: hooks.py is one dict literal and a repeated key
		# silently discards the earlier value, which here would drop the hand-off gate.
		#
		# `validate` and not `before_validate`: frappe skips validate AND before_save when
		# `flags.ignore_validate` is set, which create_project_from_opportunity_background does.
		# That path creates no budget lines, so there is nothing to roll up; every save that
		# edits a budget line is an ordinary Desk save where validate runs.
		"validate": "erpnext_enhancements.project_enhancements.budget_rollup.on_project_validate",
		"before_save": [
			"erpnext_enhancements.script_migrations.project.remove_open_status",
			"erpnext_enhancements.status_alerts.stamp_payment_received_date",
			# must run after stamp_payment_received_date: the Payment Received
			# anchor consumes the stamped date
			"erpnext_enhancements.process_steps.sync_process_steps",
		],
		"on_update": [
			"erpnext_enhancements.sync_contact.sync_from_main_doc",
			"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.publish_realtime_update",
			"erpnext_enhancements.status_alerts.notify_payment_received",
			"erpnext_enhancements.process_steps.notify_step_transitions",
			# Copy Opportunity/Lead attachments onto the Project. This was wired to
			# `after_save`, which Frappe never dispatches server-side, so it had never run
			# — Opportunity→Project conversions silently carried no files across. `on_update`
			# fires on the conversion insert AND on later saves (its idempotent file_name
			# guard makes the repeat picks-up-late-additions safe).
			"erpnext_enhancements.project_enhancements.sync_attachments_from_opportunity",
			# workforce (v1.480.0): geocode the project's site when its address text
			# changed or it has no coordinates yet, so the Time Kiosk can tell an
			# off-site clock-in. Only ENQUEUES (Google is a third-party call and a
			# Project save must not wait on it), after commit so the worker reads the
			# saved address. Every custom-field read is getattr-guarded; never raises.
			"erpnext_enhancements.workforce.sites.on_project_update",
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
		"after_insert": "erpnext_enhancements.api.communication.after_insert_communication",
		# email_from_communication runs on on_update, NOT after_insert: Frappe's
		# inbound-mail pipeline inserts the Communication BEFORE it creates the
		# attachment File docs (then re-saves), so at after_insert the attachment query
		# is always empty — the accounting-intake email channel had silently ingested
		# nothing since it shipped (v1.59.0). It fires on the post-attachment save; the
		# handler's per-file guard makes the email's later saves a no-op.
		"on_update": [
			"erpnext_enhancements.accounting_intake.channels.email_from_communication",
			# Lead triage (TASK-2026-01473): stamp Lead.custom_first_response_at from the
			# first Sent Communication of type Communication -- the one "has somebody
			# answered this Lead" rule, shared with the Speed-to-Lead widget and the SLA
			# sweep. on_update so a Communication linked to the Lead afterwards counts too.
			# Returns at once for anything not referencing a Lead; never raises.
			"erpnext_enhancements.crm_enhancements.lead_triage.stamp_first_response",
		],
	},
	"Fleet Vehicle": {
		# hr_enhancements (WI-073): `assigned_driver` became a Link to Employee, so the
		# licence expiry the credential register already tracks can finally be joined to
		# the truck. Warn only, and absence of a licence record is NOT a refusal -- it
		# means nobody has filed one, which is a gap to chase rather than a statement
		# that this person cannot drive.
		"validate": "erpnext_enhancements.hr_enhancements.availability.warn_driver_cannot_drive",
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
		"validate": [
			"erpnext_enhancements.training.compliance.warn_uncertified_technician",
			# hr_enhancements (WI-073): is this person actually free that day? Approved
			# time off and restricted duty were both already recorded and nothing read
			# them when a visit was scheduled, so a visit could be booked for somebody
			# with an approved day off and nobody found out until the morning. Warn
			# only, and a SEPARATE hook from the certification check above: the two ask
			# different questions (may they, versus can they be there) and a site may
			# want one without the other.
			"erpnext_enhancements.hr_enhancements.availability.warn_unavailable_technician",
		],
	},
	"Project Contract": {
		# quality (WI-075 sub-phase L): `validate_msa_gate` already refuses a Statement of Work
		# without a SIGNED master agreement -- and that is the whole of it. It never asks whether
		# the agreement is still IN FORCE, so one signed in 2019 gates a SOW issued today just as
		# well. This derives an MSA's expiry, checks it when a SOW is issued, and reports where
		# the SOW's FROZEN rates have drifted from what the agreement publishes now. It never
		# rewrites that snapshot: a signed agreement prints its own copy, and a live re-read
		# would change a document somebody has already signed. Only a KNOWN, past expiry can
		# block -- "no expiry recorded" never does, because none of the sixteen live contracts
		# has one and that is a gap in the record rather than a lapse.
		"validate": "erpnext_enhancements.quality.msa_enforcement.on_contract_validate",
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
	# inventory (v1.532.0): the app's first Item doc_event. Refuses a NEW Item for exactly two
	# naming findings -- a code that matches an existing code once case and punctuation are
	# ignored, and a name that is just the code (a blank name counts: ERPNext copies the code
	# into it before this runs). Every other naming finding stays advice on the form, the
	# Item Naming Audit and the KPI. Nik, 2026-09-24 (TASK-2026-02238; POL-0602): the full STOP
	# set was rejected because it includes unapproved category words, and several words are
	# still awaiting a ruling (TASK-2026-02215), so it would refuse legitimate items.
	#
	# Skips: any save before item_naming_rules.NAMING_GO_LIVE (2026-10-01, POL-0602's
	# effective date); an existing Item (never refused, whatever its name); a variant (ERPNext
	# derives its code and name from the template); import, migrate, install, patch, test and
	# setup-wizard flags; ANY save outside a web request -- the QuickBooks sync creates Items
	# on the scheduler, and a refusal there parks a record with nobody told why; and
	# `doc.flags.ignore_naming_guard`, set by the in-request callers whose user cannot choose
	# the code or name (configured-product Items, the QuickBooks upsert's per-entity Sync
	# button). Document Intake's Approve Items keeps the guard: the Stock Manager enters a
	# Proposed Item Code, and accounting_intake.review checks every line first. `validate`
	# doc_events run after ERPNext's own Item.validate, which is what fills the blank name.
	"Item": {
		"validate": "erpnext_enhancements.inventory_enhancements.item_naming_guard.validate_new_item",
	},
	# WI-013: block submitting a Purchase Order above the configurable approval
	# threshold (ERPNext Enhancements Settings.po_approval_threshold, default 500;
	# 0 disables) unless the user holds the "PO Approver" role — the CEO sign-off
	# escalation. Threshold resolution is per-project-ready (see po_approval.py).
	"Purchase Order": {
		# quality (WI-075 sub-phase L): say so when the supplier has a signed master agreement
		# and this order does not name it -- an order placed outside the agreement is an order at
		# rates nobody agreed. Advisory: refusing the save would stop somebody buying materials,
		# and this is a commercial problem to correct rather than an emergency to prevent.
		"validate": "erpnext_enhancements.quality.msa_enforcement.on_purchase_order_validate",
		# WI-014 follow-through: `Purchase Order Item.project` is mandatory, but
		# ERPNext never pushes the header project down to the item rows — fill the
		# blank ones before the mandatory check runs. Desk saves are already
		# handled client-side (public/js/purchase_order_project.js); this covers
		# the REST API, data import and Material-Request-mapped documents.
		"before_validate": [
			"erpnext_enhancements.procurement_project.cascade_project_to_items",
			# ER-2026-362239: push the header's expected delivery date into item rows
			# that have none. NOT the Required By cascade -- ERPNext already does that
			# one in buying_controller.validate_schedule_date(), and does more than
			# cascade it (the header is pulled up to the earliest row). Required By is
			# when we need it; Expected Delivery is when the supplier says it lands.
			# Fills blanks only: a per-item override is the whole point of the request.
			"erpnext_enhancements.api.procurement.cascade_expected_delivery_date",
		],
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
			# After both gates, deliberately: the stamp records that this order cleared BOTH
			# gates in this person's hands. The supplier-facing print format reads it, and
			# there is nowhere else truthful to read an approver from — Purchase Order
			# has no approver field and `modified_by` is whoever touched it last.
			"erpnext_enhancements.po_approval.stamp_approval",
			# v1.532.0 (Nik, 2026-09-24, TASK-2026-02238; POL-0602 §4.6): an orange message
			# listing any $0 line, because stock received against it comes in at $0. WARN,
			# never block -- 281 of 327 submitted lines in the 90 days to 2026-09-24 were $0,
			# so a refusal would have stopped purchasing on the day it shipped. After the gates
			# so a refused order is never also lectured about prices; silent outside a web
			# request and during import/migrate/install/patch. Never raises.
			"erpnext_enhancements.po_price_check.warn_zero_rate_lines",
		],
		# The submit gates above are bypassable without this. ERPNext's "Update Items"
		# button (`update_child_qty_rate`) edits qty/rate/rows on a SUBMITTED PO,
		# recalculates the total and calls `parent.save()` — an update-after-submit that
		# never re-runs `before_submit`. So both gates are re-asserted here: a non-approver
		# cannot inflate a PO past the threshold post-submit (WI-013), and the requester
		# cannot alter the money on an order that fills their own Material Request (WI-066).
		# New rows added via Update Items cannot carry a `material_request` link, so the SoD
		# re-check only bites edits to existing MR-linked rows — the threshold half is the
		# serious one. The Purchase Receipt order-stage handlers use
		# `db.set_value(update_modified=False)`, not a full save, so ordinary stage
		# transitions never reach this gate.
		"before_update_after_submit": [
			"erpnext_enhancements.po_segregation.enforce_requester_separation",
			"erpnext_enhancements.po_approval.enforce_threshold_after_submit",
		],
	},
	# The far end of Purchase Order.custom_order_stage (see po_order_stage.py). The stage
	# is set by hand everywhere else on purpose -- submitting an order in ERPNext is
	# approval, not the act of placing it with a supplier -- but full receipt is a fact
	# ERPNext already has, so a receipt that completes an order marks it Received, and a
	# cancelled receipt takes that back.
	#
	# doc_events run AFTER the controller's own on_submit, which is the only reason
	# reading per_received here returns the post-receipt figure: erpnext pushes it onto
	# the order from that same method. Both handlers swallow-and-log; a stage update must
	# never be the reason a Purchase Receipt cannot be submitted.
	"Purchase Receipt": {
		"on_submit": "erpnext_enhancements.po_order_stage.advance_on_receipt",
		"on_cancel": "erpnext_enhancements.po_order_stage.revert_on_receipt_cancel",
	},
	# Lead attribution. Lead had no doc_events block at all before v1.241.0.
	# Both handlers are inert unless the attribution Custom Fields exist on the
	# bench (they check frappe.db.has_column), which is what keeps them safe
	# during erpnext's own test bootstrap.
	"Lead": {
		# Accept a bare domain in any URL field. Our own Property Setter fixtures put
		# options="URL" on `website` (stock ERPNext leaves it plain), which both rejects
		# "example.com" on entry and — the expensive half — makes every record that
		# already held one unsaveable for ANY edit. before_validate rather than validate:
		# it runs ahead of the flags.ignore_validate early return and ahead of every
		# other validate handler, so nothing reads the half-fixed value.
		"before_validate": "erpnext_enhancements.crm_enhancements.website_cleanup.add_missing_scheme",
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
		"before_validate": [
			"erpnext_enhancements.sync_contact.sanitize_primary_address_link",
			# See the Lead block above — same fixture, same defect, 80 of 506
			# Opportunities were carrying a scheme-less website when this shipped.
			"erpnext_enhancements.crm_enhancements.website_cleanup.add_missing_scheme",
		],
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
		"after_insert": [
			"erpnext_enhancements.training.assignment.on_employee_insert",
			# hr_enhancements (WI-072): raise the first-week checklist. Contractually
			# cannot raise -- an Employee record failing to save because a checklist
			# could not be built would be the tail wagging the dog, and the training
			# handler above has the same contract for the same reason.
			"erpnext_enhancements.hr_enhancements.onboarding.on_employee_insert",
		],
		"on_update": [
			# Cell Number -> linked User.phone (Call via Triton dials it)
			"erpnext_enhancements.sync_contact.sync_employee_phone_to_user",
			# training: re-evaluate assignment rules when something a rule keys off
			# actually moved. Guarded with get_doc_before_save() on department /
			# designation / grade / employment_type / status (plus user_id first
			# appearing) — without that comparison EVERY Employee save enqueues a
			# full rule sweep, and Employee is saved often.
			"erpnext_enhancements.training.assignment.on_employee_update",
			# hr_enhancements (WI-073): raise the LEAVING checklist when status flips
			# to Left. Gated on the transition rather than the current value, because
			# Employee is saved often. ERPNext disables the login by itself and does
			# nothing else -- the device in their van, the four jobs assigned to them
			# and the two people who report to them are all invisible the day after,
			# so the list is GENERATED from what they hold rather than fixed.
			"erpnext_enhancements.hr_enhancements.onboarding.on_employee_update",
			# workforce (v1.480.0): pay rates changed -> re-derive Activity Cost per
			# (employee, Activity Type) so ERPNext's own Timesheet costing agrees with
			# the kiosk. Compared against get_doc_before_save() -- an ordinary Employee
			# save costs nothing -- and every failure is logged and swallowed: a costing
			# table must never block an Employee save.
			"erpnext_enhancements.workforce.costing.on_employee_update",
		],
		# workforce (v1.480.0): the permlevel-1 Pay Rates table. Every row carries the
		# amount its pay type needs, no two rows share an effective date, rows are
		# sorted, hourly_equivalent (annual / 2080) is filled. Reads the table through
		# getattr(..., None) because this fires during ERPNext's own test bootstrap
		# before the custom field exists.
		"validate": "erpnext_enhancements.workforce.costing.validate_employee_pay_rates",
	},
	# workforce (v1.480.0): a new activity gets an Activity Cost row for every employee
	# with a pay rate, so a hand-typed Timesheet against it costs correctly. Never raises.
	"Activity Type": {
		"after_insert": "erpnext_enhancements.workforce.costing.on_activity_type_insert",
	},
	"Training Assignment": {
		# training: tell the learner, however the row got here. notify_assigned had
		# exactly two callers -- the auto-assign engine and api.training_author's
		# assign_course -- and hooks.py named this doctype only in its two permission
		# hooks. So a Training Manager pressing New on the list produced no email, no
		# bell, no ToDo and no sign of any kind: the assignment existed, the learner
		# was never told, and the first anybody knew was the overdue sweep some days
		# later. A doc_event rather than a third explicit call, because the shape of
		# that bug is "one more path that forgot".
		"after_insert": "erpnext_enhancements.training.assignment.on_assignment_insert",
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
		#
		# STILL A LIST with one entry, deliberately. It held two until v1.426.0, when the
		# chat oversight-grant recorder went with the chat module (ADR 0011). Keeping the
		# list shape means the next feature that needs this event appends a line rather
		# than converting a string back into a list and getting the conversion wrong.
		"on_update": [
			"erpnext_enhancements.training.assignment.on_user_roles_changed",
		],
	},
	"Supplier": {
		# See the Lead block above — same fixture, same defect. 24 Suppliers were
		# carrying a scheme-less website. The QuickBooks sync has healed this on its
		# own save path since v1.36.0 (a parked vendor master cascaded into its Bills
		# failing to resolve a party); this is the same function, on the desk path the
		# sync never reaches.
		"before_validate": "erpnext_enhancements.crm_enhancements.website_cleanup.add_missing_scheme",
		"after_insert": "erpnext_enhancements.accounting_intake.filing.enqueue_supplier_folder",
		"on_update": "erpnext_enhancements.sync_contact.sync_from_main_doc",
		"validate": [
			"erpnext_enhancements.supplier_query.sync_supplier_groups",
			# Primary Address display text = Address.custom_full_address
			"erpnext_enhancements.sync_contact.set_supplier_primary_address_display",
		],
		"on_trash": "erpnext_enhancements.sync_contact.cleanup_directory_exclusions",
	},
	# Company's only handler. `Company-website-options` is the fifth of the URL Property
	# Setters, and the one record on this site is already fine — this is here so the
	# rule is not "every doctype we set options=URL on, except the one nobody noticed".
	# A Company save is rare and expensive (it rebuilds the chart of accounts on
	# insert); a before_validate that touches one string does not add to that.
	"Company": {
		"before_validate": "erpnext_enhancements.crm_enhancements.website_cleanup.add_missing_scheme",
	},
	"Customer": {
		# See the Lead block above — same fixture, same defect, and this is where it
		# bit hardest: 281 of 739 Customers with a website held a scheme-less one, so
		# more than a third of the book rejected every edit until this shipped.
		"before_validate": "erpnext_enhancements.crm_enhancements.website_cleanup.add_missing_scheme",
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
	# ai_governance (FAC 3.0.0 compat): give each of this app's assistant tools the FAC category
	# its own annotations imply, at the moment FAC inserts its row. FAC seeds every external
	# tool as `read_write`, and since FAC 2.5.0 that category OVERRIDES the tool's declared
	# readOnlyHint in tools/list -- so every read tool here was advertised as a write. This
	# half exists for ordering: this app is installed before frappe_assistant_core, so our
	# after_migrate (the other half, below) runs before FAC's creates the row for a new tool.
	# Inert without FAC -- the doctype never saves. Never raises.
	"FAC Tool Configuration": {
		"before_insert": "erpnext_enhancements.ai_governance.fac_tool_categories.set_category_before_insert",
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
		# Re-drive of the above if a deploy FLUSHDB destroyed the enqueued batch (a merge to
		# main between the 05:00 tick and completion leaves a permanent hole in the trend
		# data, silently). generate_all_snapshots upserts, so a re-run is idempotent.
		"0 9 * * *": ["erpnext_enhancements.kpi_dashboards.snapshots.verify_daily_snapshots"],
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
		# Raise whatever the assignment rules currently say is missing. 06:40, before
		# the reminder digest above, so anything raised today is in that morning's
		# email rather than tomorrow's.
		#
		# This is the job that was never there. `assignment.sync_course` had ONE
		# caller -- publish_version -- and it fires only if `auto_assign` was already
		# set at the moment of publishing, so turning auto-assign on for a live
		# course did nothing, and neither did adding a rule to one. The engine has
		# been complete since v1.207.0 and prod reached v1.385.0 with zero assignment
		# rules and five assignments in total, ever.
		#
		# It also makes the publish-time fan-out re-drivable, which this app has a
		# specific reason to want: the prod deploy FLUSHDBs the queue redis and
		# destroys every pending background job, so publishing shortly before a merge
		# loses its sweep silently. Idempotent -- `_assign` skips anybody who already
		# has an open assignment. Gated by Training Settings -> Auto Assign.
		"40 6 * * *": ["erpnext_enhancements.training.tasks.sweep_auto_assignments"],
		# ---- HR Enhancements (WI-072) ---------------------------------------------------
		# A credential's status is arithmetic on a date: correct the day it is saved and
		# wrong every day after. Re-derived nightly at 05:20, well before anybody looks.
		"20 5 * * *": [
			"erpnext_enhancements.hr_enhancements.tasks.refresh_credential_status",
			# The COMPANY half of the same question. Nothing in this app tracked the
			# contractor licence, the workers' comp policy and its premium audit, the
			# COIs customers ask for, or a subcontractor's certificate -- verified
			# before building: no doctype carried an expiry field for any of them.
			# Separate function from the credential sweep because the two answer
			# different questions about different subjects and a site could want one
			# without the other.
			"erpnext_enhancements.hr_enhancements.tasks.refresh_obligation_status",
		],
		# The forward view, Mondays at 07:30. Nothing in this app warned about anything
		# BEFORE the fact until now -- certificates.expire_and_recertify reacts after a
		# training certificate lapses, and fixtures/notification.json holds nineteen alerts
		# and not one HR or training one. An expiry model with no horizon tells you about a
		# problem on the morning of the job. One email per person, plus a roll-up to each
		# supervisor; gated by Training Settings -> Notifications, same as every other mail.
		"30 7 * * 1": [
			"erpnext_enhancements.hr_enhancements.tasks.send_expiry_digest",
			# Nobody on this site had an emergency contact when WI-073 looked -- all
			# sixteen blank, on a field that had existed the whole time. Nothing had
			# ever asked. It asks THEM rather than reporting a number to HR, because
			# the only person who can fill it in is the person whose contact it is,
			# and it only writes to the people who are missing one.
			"erpnext_enhancements.hr_enhancements.policies.nudge_missing_emergency_contacts",
			# Company renewals, weekly. A lapsed SUBCONTRACTOR certificate is called
			# out separately: their lapse is our exposure -- a claim on an uninsured
			# sub becomes ours -- and it reads differently from our own renewal
			# falling due.
			"erpnext_enhancements.hr_enhancements.tasks.send_obligation_digest",
		],
		# Work anniversaries into the team feed, 06:10. The feed has always known how to
		# RENDER these -- `Training Achievement` carries the kind and player.js draws it --
		# and the only thing that ever minted one was the one-shot backfill patch. So the
		# feed would have opened with sixteen and produced not one more, ever. Idempotent
		# on (user, kind, title), so a re-run the same day mints nothing twice.
		"10 6 * * *": [
			"erpnext_enhancements.hr_enhancements.tasks.mint_work_anniversaries",
			# 30 / 60 / 90-day check-ins to a new hire's supervisor. NO record and
			# nothing to fill in -- the value is the prompt, and a form attached to it
			# turns a two-minute conversation into an admin task, which is how the
			# conversation stops happening. Fires only on the exact day.
			"erpnext_enhancements.hr_enhancements.onboarding.nudge_new_hire_check_ins",
		],
		# The chat module owned 16 scheduler jobs across 11 cron keys here between v1.262.0 and
		# v1.423.0 -- the Pub/Sub puller and inbound defer timer on `* * * * *`, the relay
		# sweeper and digest summariser on `*/5`, provisioning/attachments/chunking/embedding
		# on `*/10`, and nine daily or hourly governance, indexing and notification passes
		# below. All sixteen went with the module in v1.426.0 (ADR 0011).
		#
		# WHAT SURVIVES THAT IS WORTH KNOWING: `scheduler_events` is ONE dict literal, and a
		# repeated key in a dict literal does not warn -- the later entry silently REPLACES
		# the earlier one. That is why the surviving jobs below are appended into existing
		# keys rather than given their own, and it is a live hazard on the next addition, not
		# a chat-specific one. `test_hooks_integrity` caught exactly that mistake once.
		"*/10 * * * *": [
			# hr_enhancements (WI-073): lone-worker check-in. Three stages fifteen
			# minutes apart -- chase the worker, then their supervisor, then the
			# executives. It escalates ONCE per stage, because a sweep that re-sends
			# every ten minutes trains people to filter it, and it never closes a
			# session by itself: "the sweep decided they were probably fine" is the
			# judgement nobody should be making at 7pm.
			#
			# Added to this list rather than as a second "*/10 * * * *" key. A repeated
			# key in a dict literal silently REPLACES the earlier one, so a new entry
			# would have stopped the four chat sweeps that shared this key with no error
			# anywhere. test_hooks_integrity caught exactly that. The chat sweeps are gone
			# as of v1.426.0; the rule that caught it is not.
			"erpnext_enhancements.hr_enhancements.lonework.sweep_overdue_sessions",
			# Lead triage (TASK-2026-01473): speed-to-lead SLA. Reminds a Lead's owner once
			# its first-response deadline passes, escalates once the escalation time does,
			# each at most once per Lead (Lead.custom_sla_alert). Deadlines are in working
			# time, so a Friday-evening enquiry is not chased overnight. No-op unless
			# lead_sla_enabled; only Leads an inbound channel stamped a deadline on.
			"erpnext_enhancements.crm_enhancements.lead_triage.sweep_first_response_sla",
		],
		# NOTE ON MINUTES, which outlived the entries that motivated it. Chat used to own
		# :25, :50, :35, 03:10, 03:45, 04:25, 04:30 and 04:40 in this dict, chosen to sit
		# clear of QuickBooks' :00/:20/:40. Ten of its eleven keys went with the module in
		# v1.426.0 (ADR 0011) and those minutes are free again -- but the reason they were
		# picked still binds anything added here: `scheduler_events` is one dict literal, a
		# duplicate key does NOT warn, and the later entry silently REPLACES the earlier one.
		# Reusing :20 or :40 would delete two live QuickBooks jobs and nothing would report
		# it. tests/test_hooks_integrity.py exists for exactly this.
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
		# The backup entry point is a thin shim: it checks the master switch,
		# reconciles any stranded Running row, and hands off to the long queue with a
		# 4h timeout. The dump itself never runs on the scheduler tick.
		#
		# 02:00 nightly — database + public files + private files. Every automatic
		# run is a full one. This used to be database-only with a separate full
		# backup at 03:00 on Sundays, which meant that for most of the week the
		# newest recoverable copy of the files was days old while the Log showed a
		# green run every night. The Sunday entry is gone rather than kept: it would
		# now be a second full backup an hour after the first one.
		"0 2 * * *": ["erpnext_enhancements.offsite_backup.backup.run_nightly_backup"],
		# 08:00 daily — staleness watchdog. Still checks the database and full tiers
		# separately against their own thresholds even though every scheduled run now
		# satisfies both: the tiers come apart when the nightly run is failing and
		# somebody is taking database-only backups by hand, which is exactly when the
		# aggregate "last successful backup" reads healthy. This is also the check
		# that catches "nothing ran at all": a failure email only fires when a job
		# runs and throws.
		"0 8 * * *": ["erpnext_enhancements.offsite_backup.backup.watchdog"],
		# Hand-off SLA compliance summary — Friday 07:30 site TZ. A cron entry
		# rather than the "weekly" bucket because that bucket cannot pin a
		# weekday, and the 2026-08-06 meeting asked for Friday mornings
		# specifically. :30 keeps it clear of the 07:00/07:15 cluster above.
		"30 7 * * 5": ["erpnext_enhancements.process_steps.send_weekly_sla_digest"],
		# inventory (v1.532.0): Monday 07:00 site time, last week's new Items that fail the
		# Item Naming Schema, to the Purchasing Agent (Nik, 2026-09-24, TASK-2026-02238). Its own
		# key, used nowhere else in this dict ("0 7 * * *" above is a different key); a repeated
		# key would silently replace the entry it collided with. Judged by the same
		# item_naming_rules.audit the report and the KPI use. Recipients are Inventory Scanner
		# Settings.naming_digest_recipients; blank, or a week with nothing failing, sends
		# nothing. Reads only, so a deploy FLUSHDB costs at most one Monday's email.
		"0 7 * * 1": ["erpnext_enhancements.inventory_enhancements.item_naming_digest.send_weekly_digest"],
		# workforce (v1.480.0): the supervisor digest -- yesterday's hours per person,
		# auto-closed intervals, tracking gaps, off-site clock-ins and pending time
		# correction requests, per team (Employee.reports_to) and company-wide for HR
		# Managers. 06:45 site TZ: after the 06:00 dispatch digest, before the 06:30
		# briefing batch has finished. Gated by Time Kiosk Settings.send_supervisor_digest;
		# a recipient with nothing to see gets no email.
		"45 6 * * *": ["erpnext_enhancements.workforce.digest.send_supervisor_digests"],
		# marketing (TASK-2026-01476): nightly read-only ad-spend pull -- Google Ads, Meta,
		# LinkedIn -> Ad Campaign / Ad Daily Metric / Ad Click. 03:25 site time: clear of
		# the 02:00 backup and QuickBooks' :00/:20/:40, and after every platform has closed
		# yesterday. A thin shim: master switch, 20-hour self-throttle, no-op unless a
		# platform is both switched on and connected, then one job on `long` with a fixed
		# job_id. Dormant: Marketing Settings.enabled and every platform flag ship 0.
		"25 3 * * *": ["erpnext_enhancements.marketing.core.tasks.nightly_ad_spend_sync"],
		# marketing (TASK-2026-01480): daily upkeep of the publishing connections' tokens,
		# one rule per connection -- Meta's Page token is checked by reading the Page,
		# LinkedIn's access token refreshed inside 7 days of expiry (and warned 30 days
		# before its non-rolling refresh token runs out), YouTube's refresh token exercised
		# so Google does not expire it for disuse. A dead credential is cleared and marked
		# Auth Failed rather than retried every night. 03:35, clear of the 03:25 ad pull.
		# Dormant: returns at once while Marketing Settings.enabled is 0.
		"35 3 * * *": ["erpnext_enhancements.marketing.publish.tasks.maintain_publishing_tokens"],
		# marketing (TASK-2026-01481): the publishing outbox sweep. The deploy FLUSHDBs the
		# queue and Frappe v16 wires no RQ retries, so this sweep over Social Publish Job.
		# available_at IS the timer for a scheduled post. Each run: reclaim lease-expired jobs
		# (back to Pending only if never dispatched, else Unconfirmed -- a public post must not
		# go out twice), then claim due jobs whose network may send and hand each to
		# run_dispatch on `long`. Every 5 minutes at :02/:07/..., off every other minute used
		# here. Dormant: returns at once while Marketing Settings.enabled is 0.
		"2-59/5 * * * *": ["erpnext_enhancements.marketing.publish.sweeper.sweep_publish_jobs"],
		# marketing (TASK-2026-01488): the engagement pull-back. Each published post's figures
		# for 90 days, into Social Post Metric (a lifetime total per date); YouTube's trailing
		# days are restated, and each job's cursor moves only on a clean pull. Only Connected
		# publishing connections; the publish switches play no part. Enqueues on `long`.
		# 03:50: after the 03:35 token upkeep. Dormant while Marketing Settings.enabled is 0.
		"50 3 * * *": ["erpnext_enhancements.marketing.publish.metrics_sync.nightly_social_metrics"],
	},
	"daily": [
		# quality (WI-075 sub-phase I): tell each project manager which inspection milestones
		# have come round on their jobs. Sub-phase C seeded seventeen milestones carrying a
		# trigger_basis and NOTHING read it, so a Build project could reach QA and the pre-final
		# commissioning check would come round only if somebody remembered.
		#
		# It NOTICES and never acts -- generating an inspection stays a deliberate act, because
		# one that appeared on its own is a draft nobody owns, aging in a list, looking like work
		# in progress. Due-ness is "reached or passed", not equality: build status is a Select
		# somebody types into, so a project can jump Procurement -> Ready for Install in one save
		# and an equality trigger would lose the inspection with nothing to see afterwards.
		# One digest per manager, and one milestone is mentioned again at most weekly.
		# No-op while Quality Settings has the module or notifications off.
		"erpnext_enhancements.quality.scheduling.sweep",
		# quality (WI-075 sub-phase J): the Annual review cadence, which ERPNext cannot run.
		# Its own daily quality_review.review() branches on Daily / Weekly / Monthly / Quarterly
		# and has NO Annual branch, so an Annual goal generates nothing, forever, with no error.
		# This handles Annual ONLY -- covering any of the other four would put a second review
		# beside every one core made, on the same goal, the same day, and nobody comparing two
		# identical reviews would guess why. goals.review_due RAISES if asked about one of them.
		"erpnext_enhancements.quality.reviews.generate_annual_reviews",
		# quality (WI-075 sub-phase L): master agreements coming up for renewal. 60 days' notice,
		# escalating to the Production Manager and President inside 14. Agreements with NO expiry
		# recorded get their own block in the digest -- reported as a gap in the record, never as
		# a lapse, because none of the existing sixteen has one.
		"erpnext_enhancements.quality.msa_enforcement.sweep_expiring_agreements",
		# quality (WI-075 sub-phase N): subcontractor scorecards for any CLOSED month that has
		# none yet, then the Supplier summary stamps. Daily rather than monthly on purpose -- a
		# prod deploy FLUSHDBs the queue redis on :11000 and silently destroys pending jobs, so a
		# monthly job caught by a deploy is a month with no scorecards and nothing to notice. A
		# daily job that fills gaps is self-healing, same as the Critical-NCR sweep in G.
		# No-op while Quality Settings has the module off, which is how it ships.
		"erpnext_enhancements.quality.scorecard_build.sweep",
		"erpnext_enhancements.quality.scorecard_build.refresh_all_supplier_fields",
		# training: re-grant Training Learner to anybody who owes a course and cannot
		# open it. `roles.grant_learner_role` is correct and always was; it is only
		# CALLED on Employee insert and on an Employee gaining a user_id, neither of
		# which fires again for somebody who already exists. So a direct grant wiped
		# by populate_role_profile_roles -- which rebuilds `roles` from the profile
		# union on every User save -- was never re-made, and two people with a due
		# course could not open it. Keyed on owing a course rather than on being an
		# Employee, because the obligation is what needs the role.
		"erpnext_enhancements.training.tasks.sweep_learner_roles",
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
		# QuickBooks: mirror new QBO attachments (Attachable files) onto their ERPNext
		# docs (WI-071). Daily and bounded (max_new) so it cannot run away; the one-time
		# historical backfill is a manual bench execute of attachments.sync_attachments.
		"erpnext_enhancements.quickbooks_online.core.tasks.sync_attachments_scheduled",
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
		# Triton chat attachments: retire used ones past their retention date, and un-sent
		# chips after 24h. Time-based because Triton owns the conversation -- ERPNext never
		# observes a chat being deleted, so there is no other GC signal. Deleting the row
		# deletes its private File through core's remove_all, which is the whole reason the
		# file is anchored to a row instead of left orphaned.
		"erpnext_enhancements.triton_attachments.purge_expired",
		# product_feedback capture retention (WI-079 slice 2, Nik 2026-09-23): a request's
		# screenshots and capture-context file are deleted 180 days after it closes; its text,
		# decision and Task links stay. Clock is terminal_at, falling back to modified (never
		# earlier than the real close). Joins to File on capture artifacts only, so a cleaned
		# request drops out even if it keeps a PDF. Also deletes the panel's screenshot uploads
		# (capture-shot-*) still unattached after a day: filings that failed after the upload.
		"erpnext_enhancements.product_feedback.capture_jobs.purge_expired_capture_files",
		# Client-IP derivation (TASK-2026-01478): Error Log row if recent logins were recorded
		# from a Google load-balancer address instead of the visitor's. The fix is a hand-placed
		# nginx file on the VM (/etc/nginx/conf.d/00-realip.conf, source infra/configs/), so
		# a rebuilt VM undoes it silently -- and every IP-keyed rate limit, the web-lead
		# ingress's included, quietly becomes one global bucket. Read-only, ungated.
		"erpnext_enhancements.utils.client_ip.check_client_ip_derivation",
		# marketing: prune Marketing Raw Payload past raw_payload_retention_days, and Ad
		# Click rows past 180 days that no Lead carries (Google keeps click_view 90 days, so
		# a click nobody matched in that time never will). No-op while the module is off.
		"erpnext_enhancements.marketing.core.tasks.daily_prune",
		# Re-enqueue Failed Drive Sync Log rows (uploads / recording exports)
		"erpnext_enhancements.google_drive.drive_sync.retry_failed_syncs",
		# Re-drive folder provisioning lost to a deploy FLUSHDB. retry_failed_syncs only
		# re-runs Failed log rows, and a job destroyed before it ran wrote no log row — so a
		# Customer/Opportunity inserted just before a merge to main keeps an empty
		# custom_drive_folder_id forever. Both provisioners are find-or-create (idempotent).
		"erpnext_enhancements.google_drive.drive_utils.resweep_missing_drive_folders",
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
		# workforce (v1.480.0): pick up Employee Pay Rate rows whose effective date has
		# arrived. The Employee on_update trigger only fires on a save, so a rate dated
		# next month would otherwise never reach Activity Cost until somebody touched
		# the record. Upsert; never touches an existing billing_rate.
		"erpnext_enhancements.workforce.costing.sync_all_activity_costs",
		# workforce (v1.480.0): re-drive project geocoding. The one-shot patch only
		# ENQUEUES, and a deploy FLUSHDBs the queue redis and destroys queued jobs, so
		# whatever is still missing is enqueued again here, bounded to 200 a day.
		# geocode_project is idempotent, so the overlap is harmless.
		"erpnext_enhancements.workforce.sites.backfill_missing_site_coordinates",
	],
	"hourly": [
		# training: drain Training Attempt progress still sitting in Redis from a
		# session that ended without a final beacon (closed laptop, dead phone,
		# killed tab). Bounds progress loss at one flush interval rather than a
		# whole lesson.
		"erpnext_enhancements.training.progress.flush_stale_attempts",
		# training: stat the GCS object behind every video asset, stamp
		# last_verified_on, and repair size/mime from what the bucket actually
		# holds. TrainingVideoAsset._derive_status has always deferred to this by
		# name while the module did not exist, so nothing ever moved an asset out
		# of Available -- a deleted video stayed green until a learner hit play.
		"erpnext_enhancements.training.drive_media.verify_video_assets",
		# QuickBooks Online sync jobs moved to staggered cron entries above to stop
		# the three from racing on the Settings doc (TimestampMismatchError). See the
		# "cron" section.
		"erpnext_enhancements.tasks.nudge_unsubmitted_maintenance_forms",
		# asset_management: recompute denormalised Asset rental status on booking-window
		# boundary crossings (update_asset_status otherwise only runs on a booking mutation, so
		# a booking made in advance never flips the asset when its window starts/ends).
		"erpnext_enhancements.asset_management.doctype.asset_booking.asset_booking.refresh_asset_statuses",
		# sapphire_maintenance: re-drive submissions whose on_submit background job never
		# ran (a prod deploy FLUSHDBs the queue redis and destroys queued jobs silently, so
		# a record submitted just before a deploy gets no Stock Entry/Timesheet/Invoice and
		# nothing reports it). Keyed on the absence of any processing Comment; the steps are
		# idempotent, so re-running a partially-run submission is safe. Same durability story
		# as product_feedback.sweep_stalled_breakdowns below.
		"erpnext_enhancements.api.maintenance_workflow.resweep_stalled_maintenance_submissions",
		# quality (WI-075 sub-phase G): re-drive Critical NCR alerts and chase the ones nobody
		# has acknowledged. Two independent passes, because they fail independently -- an alert
		# whose enqueue a deploy FLUSHDB destroyed was never sent at all, while a listed
		# recipient who was never reached has a row saying otherwise. Hourly so a destroyed
		# enqueue costs at most an hour; a recipient is still nagged at most once per calendar
		# day, because an hourly re-send trains people to filter exactly the message that must
		# not be filtered. No-op while Quality Settings has the module or notifications off.
		"erpnext_enhancements.quality.critical_alerts.sweep",
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
		# Re-charge autopay invoices whose enqueued charge job never ran (deploy FLUSHDB
		# between commit and execution). poll_pending/dunning only touch existing Stripe
		# Payment rows; this covers invoices that produced none. charge_saved_method is guarded.
		"erpnext_enhancements.stripe_payments.core.tasks.sweep_missed_autopay",
		# plaid_banking: refresh cached bank balances (self-throttled to
		# refresh_poll_minutes; skips while paused, so a dead link can't storm)
		"erpnext_enhancements.plaid_banking.core.tasks.scheduled_balance_refresh",
		# product_feedback: re-drive approvals whose Triton call never ran. This IS the
		# durability story for that feature — the prod deploy FLUSHDBs the queue redis, so
		# an ordinary successful deploy destroys queued jobs silently and `enqueue` has
		# already reported success. An Enhancement Request sitting in `Approved` with no
		# proposal is the observable trace a lost job leaves; nothing else is. ADR 0010.
		"erpnext_enhancements.product_feedback.breakdown.sweep_stalled_breakdowns",
		# product_feedback capture (WI-079 slice 2): note on each capture filed 20 min to 36 h
		# ago which Error Log belongs to each failed request in its snapshot. A job, not a
		# submit-time lookup, because v16 writes a 5xx's Error Log through deferred_insert on the
		# 0/15 cron, with owner = scheduler and creation = flush time -- so it matches on the
		# row's metadata (user, verb, path) inside the flush window. Idempotent per Error Log.
		"erpnext_enhancements.product_feedback.capture_jobs.match_capture_error_logs",
		# product_feedback release sync (WI-079 slice 4, ADR 0016 §5): a CHANGELOG section that
		# carries `Refs: ER-..., TASK-...` moves each named Task to Pending Review with
		# review_date +14 days and a comment naming the release -- only Tasks the feedback
		# pipeline created, only from Open/Working/Overdue, never to Completed. Acts only on
		# sections at or below the version tabInstalled Application records, which v16 writes
		# near the end of a migrate that got that far (frappe/migrate.py, update_versions), so a
		# half-installed deploy's CHANGELOG is never believed. Idempotent, and nothing is queued:
		# a deploy FLUSHDB costs an hour, never a transition. The marker is
		# Product Feedback Settings.release_sync_last_version, absent = process everything.
		"erpnext_enhancements.product_feedback.release_sync.sync_shipped_tasks",
		# workforce (v1.480.0): close clock-ins nobody clocked out of. An interval still
		# Open/Paused auto_close_after_hours (Time Kiosk Settings, 14) after it started
		# is closed at the pause time, else the last location fix, else start + limit,
		# flagged auto_closed with the rule that decided, and the employee + supervisor
		# are emailed. Per-interval try/except + commit, so one bad row cannot stop the
		# sweep; the candidate query only sees Open/Paused rows, so a closed one is
		# never revisited.
		"erpnext_enhancements.workforce.sweeper.auto_close_stale_intervals",
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
# /feedback (ADR 0010) is one shell at www/feedback.html serving every sub-path, so a hard
# refresh at /feedback/request/ER-2026-00001 renders it rather than 404ing. Every
# notification product_feedback/notify.py sends links to exactly such a URL, so this rule is
# what makes those links work at all. Frappe v16 hands from_route to werkzeug's Rule
# verbatim, so the full converter set including <path:...> is available.
#
# TRAP, recorded because it is operational rather than visible in code: loading
# /feedback/request/X BEFORE this rule shipped caches that URL in the `website_404` cache
# until Redis is flushed. A full deploy FLUSHDBs Redis and clears it; a hotfix without a
# restart does not. So do not advertise a new route here to anybody before the deploy
# carrying it has landed.
#
# This list held a second, IDENTICALLY SHAPED rule for the chat SPA until v1.426.0
# (ADR 0011). It is named here because the shapes are twins and the next person editing this
# list is one careless line-delete away from taking /feedback's deep links down with it —
# which would 404 every link in every enhancement-request notification, and cache those 404s
# until the following full deploy.
#
# /marketing (TASK-2026-01487, v1.515.0) is the same shape again: one shell at
# www/marketing.html for the calendar, composer, media library and approval queue, so a
# refresh at /marketing/post/SPOST-00001 or /marketing/calendar/2026-10 renders it. The client
# routes itself (public/js/marketing/routes.js); the server never parses the path.
website_route_rules = [
	{"from_route": "/feedback/<path:feedback_path>", "to_route": "feedback"},
	{"from_route": "/marketing/<path:marketing_path>", "to_route": "marketing"},
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
		# Control-panel NEC panel schedule (circuits + service totals). The 430.24/
		# 430.62/Art.450 math can't run in the print sandbox, so it's computed here.
		"erpnext_enhancements.water_engineering.doctype.control_panel_design.control_panel_design.we_panel_schedule",
		# Submittal-packet helpers: the resolved title block, the two server-rendered
		# schematics (SVG), and the code-keyed standard-note list — all computed in
		# Python because the print sandbox can't build them.
		"erpnext_enhancements.water_engineering.packet.we_title_block",
		"erpnext_enhancements.water_engineering.packet.we_circulation_schematic",
		"erpnext_enhancements.water_engineering.packet.we_electrical_oneline",
		"erpnext_enhancements.water_engineering.packet.we_standard_notes",
		"erpnext_enhancements.water_engineering.packet.we_standard_details",
		"erpnext_enhancements.project_enhancements.print_data.project_schedule_rows",
		"erpnext_enhancements.project_enhancements.print_data.project_task_rows",
		# The union of Purchase Order.project and Purchase Order Item.project, so the PO
		# print format and the PDF filename name the job the same way. The two fields can
		# disagree; a template working it out inline would be a third answer.
		"erpnext_enhancements.procurement_project.purchase_order_projects",
		# `PO-2026-00262-PRJ-00706` — the name the order goes by. The print format's
		# header and the downloaded PDF's filename are the same string because they are
		# this same call; two renderings of one idea drift.
		"erpnext_enhancements.po_pdf_filename.purchase_order_document_id",
		# The email design system (docs/email-design-system.md). Notification
		# bodies are Jinja strings edited through the Desk, so they reach the
		# shared chrome through these globals rather than {% extends %}: in a
		# child template anything outside a {% block %} is silently discarded,
		# and a mistyped extends path raises inside Notification.send()'s own
		# except, which logs an Error Log and drops the email.
		#
		# Registered as individual functions, NOT the module: get_jinja_hooks
		# exports every function of a module-valued entry into the global Jinja
		# namespace, and `wrap`/`table`/`render` reaching every Print Format and
		# web template on the site is not a trade worth making. The ee_ prefix
		# is what keeps them distinguishable there.
		"erpnext_enhancements.email_style.ee_email",
		"erpnext_enhancements.email_style.ee_button",
		"erpnext_enhancements.email_style.ee_doc_button",
		"erpnext_enhancements.email_style.ee_kv",
		"erpnext_enhancements.email_style.ee_table",
		"erpnext_enhancements.email_style.ee_kpis",
		"erpnext_enhancements.email_style.ee_callout",
		"erpnext_enhancements.email_style.ee_code",
		"erpnext_enhancements.email_style.ee_prose",
		"erpnext_enhancements.email_style.ee_pill",
		# The letterhead logo URL. Computed in Python because it must be
		# absolute (an email client has no site origin) and cache-busted with
		# the deploy token, which is www/ page context, not a Jinja global.
		"erpnext_enhancements.email_style.ee_email_logo_url",
		# The print design system (docs/print-design-system.md). The Python-composed
		# formats bake this chrome in at after_migrate; the Jinja fixture format
		# (`Maintenance Record Print`) reaches the same functions through these
		# globals at print time. Individually and prefixed, for the reason above.
		"erpnext_enhancements.print_style.ps_page_open",
		"erpnext_enhancements.print_style.ps_page_close",
		"erpnext_enhancements.print_style.ps_letterhead",
		"erpnext_enhancements.print_style.ps_section_title",
		"erpnext_enhancements.print_style.ps_th",
		"erpnext_enhancements.print_style.ps_td",
		"erpnext_enhancements.print_style.ps_facts_open",
		"erpnext_enhancements.print_style.ps_fact",
		"erpnext_enhancements.print_style.ps_facts_close",
		"erpnext_enhancements.print_style.ps_signature_lines",
		"erpnext_enhancements.print_style.ps_style",
		# v1.533.0: how a value looks on paper -- an address without its trailing break,
		# a bare-digit phone as (801) 555-0100, a quantity as 1 rather than 1.0, a plain
		# description that keeps its line breaks, "Nos" as "ea", the DRAFT marker.
		"erpnext_enhancements.print_style.ps_address",
		"erpnext_enhancements.print_style.ps_phone",
		"erpnext_enhancements.print_style.ps_qty",
		"erpnext_enhancements.print_style.ps_rich",
		"erpnext_enhancements.print_style.ps_line",
		"erpnext_enhancements.print_style.ps_uom",
		"erpnext_enhancements.print_style.ps_state",
		# v1.533.0: what a format cannot find for itself. The party block (name, address,
		# Attn, phone, email) walks the document -> its Contact -> its Address -> the party
		# record, because this site keeps phones and emails in the app's own custom fields
		# that ERPNext never copies onto a document; and the taxes table is split into the
		# QuickBooks billable-expense lines it also holds and the tax it is named for.
		"erpnext_enhancements.print_lookup.ps_party",
		"erpnext_enhancements.print_lookup.ps_rfq_suppliers",
		"erpnext_enhancements.print_lookup.ps_charge_rows",
		"erpnext_enhancements.print_lookup.ps_tax_rows",
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
	# WHY ANYTHING IS HERE AT ALL, kept because the reason is not chat-specific and the
	# eight chat entries that used to demonstrate it went in v1.426.0 (ADR 0011):
	# after_migrate does NOT run during install-app -- core runs before_install,
	# after_install and after_sync only -- and install-app writes the whole of patches.txt
	# to Patch Log as already-executed. So anything a patch would have created on an
	# existing site simply never exists on a fresh one unless it is ALSO named here, as the
	# same idempotent callable the after_migrate backstop uses. Every entry in this list is
	# contractually forbidden from raising: a failure here must never abort an app install.
	#
	# Desk tile artwork -- the same callable as the after_migrate entry below, and here
	# for the usual reason: after_migrate does NOT run during `bench install-app`, so a
	# fresh site would show grey letter avatars until somebody happened to run a migrate.
	"erpnext_enhancements.setup.desktop_icons.sync_desktop_icons",
	# hr_enhancements (WI-072). Same reason as everything above: install-app writes the
	# whole of patches.txt to Patch Log as already-executed, so a fresh site would get the
	# Position DocType and the Credential Type DocType with no rows in either -- an empty
	# ladder and an empty credential register that both look deliberately configured.
	# Both are insert-only and safe to run twice. They need only doctypes, which exist by
	# `sync_for` (frappe v16 `installer.py`: sync_for, then after_install).
	"erpnext_enhancements.patches.seed_positions_from_designations.execute",
	"erpnext_enhancements.patches.seed_credential_types.execute",
]

# Run at the END of `bench install-app`, after fixtures have synced.
#
# The distinction from after_install is not cosmetic. v16's `installer.install_app`
# runs: sync_for -> add_to_installed_apps -> **after_install** -> sync_jobs ->
# **sync_fixtures** -> sync_customizations -> **after_sync**. So a callable that needs
# a FIXTURE Custom Field must hang here and not on after_install, where the column does
# not exist yet. Exactly the same ordering trap as the migrate path, where
# sync_fixtures runs after the post-model-sync patches.
after_sync = [
	# Places every Employee on the Position ladder. Writes `Employee.custom_position`,
	# which is a fixture Custom Field -- hence here rather than in after_install.
	# One-shot: it stamps itself and afterwards only touches employees created since,
	# so clearing somebody's position by hand is never overruled on the next deploy.
	"erpnext_enhancements.patches.seed_positions_from_designations.map_employees_to_positions",
]

# Run after each `bench migrate` (from global_enhancements)
after_migrate = [
	"erpnext_enhancements.setup.custom_fields.create_primary_contact_fields",
	# Lead triage (v1.502.0): stamp Lead.custom_first_response_at from Communications that
	# predate the hook, so the metric has a history. Here, not in patches.txt, because the
	# column is a FIXTURE Custom Field and sync_fixtures() runs after the post-model-sync
	# patches. Fills blanks only; one cheap statement on every later migrate.
	"erpnext_enhancements.crm_enhancements.lead_triage.backfill_first_responses",
	"erpnext_enhancements.setup.supplier_groups.create_supplier_group_customizations",
	# Hide the "Project" DocType link in the core Projects module sidebar (user request)
	"erpnext_enhancements.setup.workspace_tweaks.hide_core_sidebar_items",
	# Desk home-grid tile artwork. Every tile this app contributes rendered as a grey
	# letter avatar because create_desktop_icons_from_workspace() assigns `icon.app_name`,
	# a field Desktop Icon does not have (the real one is `app`) -- so `app` stays NULL,
	# the filename-convention icon lookup fails its first guard, and the letter avatar is
	# the only branch left. We set `logo_url`, which that upstream bug cannot reach. Also
	# creates the tile -- and the Workspace Sidebar without which a tile never renders --
	# for a workspace added after install, which core only ever does at install time.
	# Idempotent, and contractually cannot raise: a desk tile is not worth a failed migrate.
	"erpnext_enhancements.setup.desktop_icons.sync_desktop_icons",
	# Mermaid.js Process Document charts — repo is the source of truth
	"erpnext_enhancements.setup.process_documents.sync_process_documents",
	# Projects-module dashboard widgets (Custom HTML Blocks) — repo is the source
	# of truth; upserts the blocks from "Custom HTML Block/" and places them on Home
	"erpnext_enhancements.setup.custom_html_blocks.sync_custom_html_blocks",
	# hr_enhancements (WI-072): place every Employee on the Position ladder. NOT in
	# the seeding patch, because the column it writes is `Employee.custom_position`
	# -- a FIXTURE Custom Field, and `sync_fixtures()` runs in post_schema_updates,
	# after the post-model-sync patches. A patch doing this would map nobody on the
	# migrate that introduces the field, then record itself in Patch Log and never
	# run again. Idempotent: writes only where custom_position is empty.
	"erpnext_enhancements.patches.seed_positions_from_designations.map_employees_to_positions",
	# accounting_intake: link each Employee to their reimbursement Supplier, which is
	# how an out-of-pocket receipt becomes a draft Purchase Invoice on a site with no
	# hrms. Here as well as in patches.txt for the same ordering reason as the line
	# above: the column is a FIXTURE Custom Field, sync_fixtures() runs after the
	# post-model-sync patches, and a patch that returns having done nothing still
	# records itself in Patch Log and never runs again. Idempotent -- writes only
	# where the field is empty, so a human's correction is never overwritten.
	"erpnext_enhancements.patches.link_reimbursement_suppliers.link_reimbursement_suppliers",
	# Ten Employee fields (cost to company, bank details, passport, health) move to
	# permlevel 1 via Property Setter fixtures, and on its own that hides them from
	# EVERYBODY -- no role on this site holds any permission at level 1. This grants
	# it to HR Manager and System Manager. Here as well as in patches.txt because
	# the Property Setters are fixtures and sync_fixtures() runs after the
	# post-model-sync patches. Idempotent; never raises.
	"erpnext_enhancements.patches.protect_employee_compensation_fields.grant_employee_field_permissions",
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
	# marketing: fill the Marketing Settings defaults into the EXISTING tabSingles row.
	# A `default` on a new field of a Single never reaches a row that already exists, and
	# this module ships dormant -- the shape where the first save of the settings page is
	# the one you need and the one that fails (Chat Settings, v1.277.3). Also in
	# patches.txt; here as the backstop for a site whose Patch Log already has the entry.
	# Idempotent, fills missing rows only, and must never raise.
	"erpnext_enhancements.patches.backfill_marketing_settings_defaults.backfill_marketing_settings_defaults",
	"erpnext_enhancements.water_engineering.setup.ensure_pump_catalog",
	# water_engineering: the aquatic-equipment catalog (filters, heaters, chem feed,
	# controllers, skimmers, VGB drains, therapy jets, gauges) — Item custom spec
	# fields gated on custom_equipment_class + the Fika reference items. Idempotent +
	# guarded; feeds the equipment schedule and the filter/heater/VGB cross-checks.
	"erpnext_enhancements.water_engineering.setup.ensure_equipment_catalog",
	# water_engineering: generic starter Nozzle Profiles so orifice nozzles compute
	# immediately (idempotent + guarded; flagged generic — replace with cut-sheet data).
	"erpnext_enhancements.water_engineering.setup.ensure_nozzle_profiles",
	# water_engineering: starter Standard Notes for the submittal packet's SP-5
	# notes sheet (health-dept boilerplate keyed to code articles). Idempotent +
	# guarded; the engineer edits/adds per jurisdiction.
	"erpnext_enhancements.water_engineering.setup.ensure_standard_notes",
	# water_engineering: starter Standard Details (schematic SVG placeholders) for
	# the packet's SP-4 details sheet. Idempotent + guarded; replaced with cut-sheets.
	"erpnext_enhancements.water_engineering.setup.ensure_standard_details",
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
	# enhancements_core: the other five procurement documents in the Purchase Order's design —
	# Material Request, Request for Quotation, Supplier Quotation, Purchase Receipt, Purchase
	# Invoice. Same module and same reason as the order's format. MUST sit ABOVE
	# ensure_chrome_pdf_generator, or they render on wkhtmltopdf.
	"erpnext_enhancements.enhancements_core.setup_procurement_print_formats.ensure_procurement_print_formats",
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
	# dropdown -- "Purchase Order - Sapphire" is the only one we print. Disabled, not
	# deleted: they are standard formats, and a deleted one is imported again from
	# erpnext's JSON. A disable is NOT undone by that sync -- this comment used to say it
	# was. frappe v16 keeps the site's `disabled` when a standard format re-imports
	# (`ignore_values` in frappe/modules/import_file.py, lines 28-30, applied by
	# `delete_old_doc` at 261-264). It still runs on EVERY migrate because that is
	# idempotent (an already-disabled format is skipped) and because it re-disables
	# anything an admin has switched back on. The two CUSTOM PO formats are genuinely
	# deleted, once, by patches/purge_purchase_order_print_formats.py -- nothing
	# recreates those. After the chrome pass on purpose: disabling a format it has
	# already pointed at chrome costs nothing, and the reverse order would leave a
	# disabled format skipped by the chrome filter, with a stale generator waiting for
	# whoever re-enables it. Since v1.519.0 it also disables the stock formats of the
	# other five procurement doctypes (SUPERSEDED_PROCUREMENT_FORMATS), and since v1.533.0
	# the Sales Invoice formats and two strays (SUPERSEDED_SALES_FORMATS) -- the Sapphire
	# sales formats are now those doctypes' defaults, through Property Setter fixtures.
	"erpnext_enhancements.enhancements_core.setup_print_formats.disable_superseded_print_formats",
	# The eight chat backstops that stood here from v1.261.0 went with the module in
	# v1.426.0 (ADR 0011). THE PATTERN THEY DEMONSTRATED IS STILL THE HOUSE RULE and is
	# why this list exists at all: a patch runs ONCE per site, recorded in `tabPatch Log`,
	# so a patch that was skipped, half-applied, or recorded-without-running is never
	# retried. An idempotent `ensure_*` callable named here runs on EVERY migrate and is
	# the only thing that repairs such a site. That is not hypothetical -- it is how
	# v1.261.0 shipped ten Chat DocTypes whose composite indexes did not exist, and how
	# `default_chat_settings` sat unseeded on prod while its Patch Log row said otherwise.
	# Anything here must be idempotent and must never raise: an after_migrate hook that
	# raises aborts `bench migrate`, which on this repo is the deploy.
	#
	# quality (WI-075): give the existing Quality Settings row the defaults its fields were
	# declared with. Here AS WELL AS in patches.txt, for the house rule stated directly
	# above -- a patch runs once per site and a skipped or recorded-without-running one is
	# never retried, and this is the Single whose dormant settings page cannot be saved
	# until its rows exist. Fills only where `tabSingles` has no row, so it never writes
	# over a deliberate 0.
	"erpnext_enhancements.patches.backfill_quality_settings_defaults.backfill_quality_settings_defaults",
	# ai_governance (FAC 3.0.0 compat): align the FAC Tool Configuration rows that already exist
	# with each assistant tool's declared annotations (read_only / write / privileged, override
	# on). Every migrate rather than a patch, because the FAC admin page can change a row and
	# `_gate.py` -- not that page -- is the source of truth. The doc_event on the same doctype
	# covers rows FAC creates AFTER this runs. Returns at once without FAC; never raises.
	"erpnext_enhancements.ai_governance.fac_tool_categories.sync_fac_tool_categories",
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
					# The six this app has always managed.
					"Maintenance Review Needed",
					"Maintenance Finalized",
					"Maintenance Reading Out of Range",
					"Maintenance Contract Renewal Due",
					"High Escalation Risk Call",
					"Compliance Flag on Call",
					# The thirteen that were created in the Desk UI and existed
					# only in the site database until v1.331.0. Adopting them is
					# what `patches/repoint_notifications_to_group_emails.py`
					# argued against, and its objection was right at the time:
					# transplanting ~40k characters of hand-copied chrome into
					# the repo would have drifted immediately. That is no longer
					# what happens — each body is now 300-800 characters of
					# *content* calling the shared ee_* macros, and the chrome it
					# used to carry cannot be edited from the Desk at all,
					# because it is not in the field being edited.
					#
					# The recipients in fixtures/notification.json are the exact
					# rows that patch installed. Re-exporting these from a site
					# whose recipients have been edited will overwrite them.
					"New Lead Created",
					"New Opportunity",
					"Email Team on Opportunity Won",
					"New Project Created",
					"Project Type Change Alert",
					"Task Completed",
					"New ToDo Created - Notify Creator and Assignee",
					"Remind Me Email",
					"New Fiscal Year Created",
					"Material Request Received",
					"Material Request Submission Notification",
					"Error Log",
					"Integration Request",
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
					# TASK-2026-01486: the marketing publishing surface, and nothing else.
					# "Marketing" is what a marketing hire gets -- Marketing Team only, which
					# grants the Marketing module's doctypes and no customers, invoices or
					# financials. "Marketing Approvers" is a single-role add-on carrying
					# Marketing Manager, the approval authority, the same shape as the PO
					# profiles below and for the same reason: a profiled user can only get a
					# role through a profile. Neither is "Sales & Marketing", which also
					# carries Sales Manager.
					"Marketing",
					"Marketing Approvers",
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
	"erpnext.crm.doctype.opportunity.opportunity.make_project": "erpnext_enhancements.opportunity_enhancements.make_project",
	# The Project Number in the downloaded Purchase Order PDF filename (ER-2026-256847).
	# This is the ONLY lever on that filename: frappe v16 builds it from the docname and
	# never consults `title_field`, so the obvious title-field fix fails silently. The route
	# reaches this hook via api/v1.handle_rpc_call -> handler.execute_cmd, whose first line
	# is override_whitelisted_method(). Note the blast radius -- every doctype in the system
	# prints through here -- which is why the override calls frappe's own function unchanged
	# and only rewrites the filename afterwards, for Purchase Order alone. See
	# po_pdf_filename.py.
	"frappe.utils.print_format.download_pdf": "erpnext_enhancements.po_pdf_filename.download_pdf",
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
	# Training Answer Dispute (v1.490.0): a learner sees their own, a supervisor their
	# reports'. Scoped for a sharper reason than the others -- a dispute SNAPSHOTS the
	# accepted answers for its question, so an unscoped list would be an answer key for
	# every question anybody has ever got wrong.
	"Training Answer Dispute": "erpnext_enhancements.training.permissions.answer_dispute_query_conditions",
	"Training Submission": "erpnext_enhancements.training.permissions.submission_query_conditions",
	# Gamification (WI-072 §0). Both of these grant `read` to "Training Learner" in
	# their doctype JSON, and Training Learner is held by CUSTOMER Website Users as
	# well as staff -- so until v1.386.0 a client contact could enumerate every staff
	# member's points, streaks and badge awards through /api/resource. They shipped
	# with DocPerms and no scoping hook, which is the one combination that leaks.
	# `Training Badge` itself stays unscoped on purpose: it is a catalogue of badge
	# definitions with no `user` column, and the player shows learners what there is
	# to earn.
	"Training Badge Award": "erpnext_enhancements.training.permissions.badge_award_query_conditions",
	"Training Learner Stat": "erpnext_enhancements.training.permissions.learner_stat_query_conditions",
	# Ask-the-author (WI-072 §0, same leak). The endpoint returns own rows plus
	# public+Answered ones; the DocPerm returned EVERY thread on the site to
	# anybody holding Training Learner, unanswered private questions included.
	"Training Question Thread": "erpnext_enhancements.training.permissions.question_thread_query_conditions",
	# The team feed (WI-072). Same shape as the three above and found the same way:
	# the generalised assertion in test_training_signoff_loop caught these before
	# they shipped. Achievement is staff-only and honours the opt-out stamped on the
	# row; Kudos is scoped by the achievement it hangs on, not by its sender, since
	# a reaction is a public act on a public row; a Preference is nobody's business
	# but its owner's -- knowing who has opted out of a feed is itself information
	# about them.
	"Training Achievement": "erpnext_enhancements.training.permissions.achievement_query_conditions",
	"Training Kudos": "erpnext_enhancements.training.permissions.kudos_query_conditions",
	"Training Profile Preference": "erpnext_enhancements.training.permissions.profile_preference_query_conditions",
	# A licence number, a medical card and a certificate number are personal. The
	# `Employee` DocPerm on Employee Credential is deliberate -- it is what puts a
	# technician's own forklift ticket on their own profile, and what keeps the HR
	# module inside allow_modules -- so the scoping hook ships in the same commit,
	# not after it. Own rows, direct reports, and the people you outrank.
	"Employee Credential": "erpnext_enhancements.hr_enhancements.permissions.credential_query_conditions",
	# Time off and onboarding (WI-072). Both grant the `Employee` role, which every
	# staff account holds, so both need scoping in the same release -- own rows,
	# your direct reports, and (for time off) anything you are the named approver
	# of. No position-tier arm on either: time off is "who plans your week", which
	# is what reports_to means and what a competence ladder does not.
	"Time Off Request": "erpnext_enhancements.hr_enhancements.permissions.timeoff_query_conditions",
	# Tighter than time off, deliberately: a Tier Review is a list of what somebody
	# cannot yet do, in their own words. Own reviews and ones you are named reviewer
	# on -- no reports_to arm, because being somebody's manager is not a reason to
	# read their self-assessment.
	"Tier Review": "erpnext_enhancements.hr_enhancements.permissions.tier_review_query_conditions",
	# A restriction carries no medical reason, but "no lifting, no ladders, until the
	# 14th" still says something about somebody's health with the why left out. Scoped
	# like time off -- yourself and the people whose week you plan. The dispatch
	# advisory does not read through this; it runs server-side and is told the summary.
	"Work Restriction": "erpnext_enhancements.hr_enhancements.permissions.restriction_query_conditions",
	# Everybody can FILE an incident -- a log a worker cannot open is a log that gets
	# a phone call instead -- but an injury record carries a body part, a treatment
	# and, on a privacy case, a category from a list that includes sexual assault and
	# mental illness. Own and reports' only; the log itself is read through the
	# role-gated OSHA reports.
	"Safety Incident": "erpnext_enhancements.hr_enhancements.permissions.incident_query_conditions",
	# Your own only. Whether a colleague has signed the handbook is HR's business
	# rather than their manager's, so there is no reports_to arm here.
	#
	# `HR Case Record` is deliberately NOT in this list: it has no Employee DocPerm
	# at all, so there is nothing for a row filter to narrow. HR Manager and System
	# Manager, and nobody else.
	"Policy Acknowledgement": "erpnext_enhancements.hr_enhancements.permissions.acknowledgement_query_conditions",
	"Onboarding Checklist": "erpnext_enhancements.hr_enhancements.permissions.onboarding_query_conditions",
	# Six chat entries stood here until v1.426.0 (ADR 0011) and are named in the comment
	# below rather than left as a silent gap, because their absence changes the parity
	# arithmetic this file states twice.
	#
	# Triton chat attachments are one person's private chat context. Owner-scoped, with the
	# single-document twin below -- a query condition filters lists and says nothing about
	# frappe.get_doc(), so shipping one without the other leaves the hole in whichever half
	# you skipped.
	"Triton Chat Attachment": "erpnext_enhancements.ai_governance.permissions.triton_chat_attachment_query",
	# workforce (v1.480.0): Job Interval grants read to the Employee role, which every
	# staff account holds, and a DocPerm is doctype-wide -- so every technician could
	# browse everybody's clock-ins. System Manager / HR Manager / Accounts Manager /
	# Projects Manager see all; everyone else their own session Employee's rows. The
	# permlevel-1 pay block on the row is a separate gate (Custom DocPerm permlevel 1 in
	# the DocType JSON) and is never widened here.
	"Job Interval": "erpnext_enhancements.workforce.permissions.job_interval_query_conditions",
	# workforce (v1.480.0): an employee sees their own requests; their reports_to sees
	# them too (read), because that person may approve them; HR Manager / Projects
	# Manager / System Manager see all.
	"Time Correction Request": "erpnext_enhancements.workforce.permissions.time_correction_request_query_conditions",
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
	"Training Answer Dispute": "erpnext_enhancements.training.permissions.answer_dispute_has_permission",
	"Training Submission": "erpnext_enhancements.training.permissions.submission_has_permission",
	# The single-document twins of the two gamification query conditions above. A
	# query condition filters lists and says nothing about frappe.get_doc(), so
	# shipping one without the other leaves the hole in whichever half you skipped.
	"Training Badge Award": "erpnext_enhancements.training.permissions.badge_award_has_permission",
	"Training Learner Stat": "erpnext_enhancements.training.permissions.learner_stat_has_permission",
	"Training Question Thread": "erpnext_enhancements.training.permissions.question_thread_has_permission",
	"Training Achievement": "erpnext_enhancements.training.permissions.achievement_has_permission",
	"Training Kudos": "erpnext_enhancements.training.permissions.kudos_has_permission",
	"Training Profile Preference": "erpnext_enhancements.training.permissions.profile_preference_has_permission",
	"Employee Credential": "erpnext_enhancements.hr_enhancements.permissions.credential_has_permission",
	"Time Off Request": "erpnext_enhancements.hr_enhancements.permissions.timeoff_has_permission",
	# The document-level twin. A query condition filters lists and says nothing
	# about frappe.get_doc(), which is exactly the gap that left three Training
	# doctypes readable by customers until v1.386.0.
	"Tier Review": "erpnext_enhancements.hr_enhancements.permissions.tier_review_has_permission",
	"Work Restriction": "erpnext_enhancements.hr_enhancements.permissions.restriction_has_permission",
	"Safety Incident": "erpnext_enhancements.hr_enhancements.permissions.incident_has_permission",
	"Policy Acknowledgement": "erpnext_enhancements.hr_enhancements.permissions.acknowledgement_has_permission",
	"Onboarding Checklist": "erpnext_enhancements.hr_enhancements.permissions.onboarding_has_permission",
	# PARITY IS THE HOUSE DOCTRINE, and it is the thing to preserve here. Every entry in
	# `permission_query_conditions` above must have a twin in this register, because a
	# query condition filters LIST VIEWS and reports and says nothing whatever about
	# `frappe.get_doc()` -- shipping one without the other leaves the hole in whichever
	# half you skipped, which is exactly what left three Training doctypes readable by
	# customers until v1.386.0. The two registers were at an exact ten-and-ten when ADR
	# 0009 cited them; chat's six twins went with the module in v1.426.0 and the parity
	# still holds. Check it when you add one.
	#
	# On v16 a has_permission hook that returns None DENIES, so every path in every hook
	# named here returns an explicit bool, exception paths included.
	#
	# This one is doing more work than it looks like. Beyond refusing a direct read, it is
	# what decides whether the MODEL may read an attached file at all:
	# `fac_extract_file_content` resolves a Frappe file_url and then calls
	# frappe.has_permission(file.attached_to_doctype, "read", file.attached_to_name), which
	# lands here under the ERPNext identity the user linked to Triton. "Can Triton read this"
	# and "can this person open it" are therefore the same boolean by construction, rather
	# than two rules that have to be kept in step.
	# Per the v16 rule stated at the top of this register: a hook returning None DENIES, so
	# every path in it returns an explicit bool, exception paths included.
	"Triton Chat Attachment": "erpnext_enhancements.ai_governance.permissions.triton_chat_attachment_has_permission",
	# workforce (v1.480.0): the single-document twins of the two query conditions above,
	# per the parity doctrine. Both return an explicit bool on every path.
	"Job Interval": "erpnext_enhancements.workforce.permissions.job_interval_has_permission",
	"Time Correction Request": "erpnext_enhancements.workforce.permissions.time_correction_request_has_permission",
}

# `notification_skip_email_types` held ["Chat Message", "Chat Mention"] from v1.267.0 until
# v1.426.0, when the chat module and its two `Notification Type` records went (ADR 0011).
# Left empty rather than deleted, with the mechanism recorded, because the next feature that
# fans out a Notification Log row will need it and the reasoning is not obvious:
#
# `Notification Log.after_insert` calls send_notification_email() whenever
# is_email_notifications_enabled_for_type(for_user, type) is true. That predicate consults
# get_skip_email_types() FIRST -- before it reads the user's own Notification Settings -- so a
# type listed here cannot be emailed by anybody, including a user who has explicitly ticked it
# on. A per-user default is defeated by the first person who re-enables it; this is not.
#
# Two consequences worth knowing rather than discovering:
#   - Frappe's own hooks.py lists "Alert" here, so this MERGES rather than replacing.
#   - Registering a type here makes the desk's "enable email for all users" button THROW for
#     it ("{0} never sends email, so it cannot be enabled for users."). That throw is the
#     assertion you want, not a bug to work around.
#
# The names are keys, not labels: they must match the installed `Notification Type` records,
# and renaming one silently re-enables email for it.
notification_skip_email_types = []

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
# `Chat Relay Job` and `Chat Inbound Event` were deliberately kept OUT of this dict while the
# chat module existed, because Chat Settings carried retention fields that claimed to control
# them and declaring a static number here would have made those fields lie. Both doctypes went
# in v1.426.0. The rule they illustrated stands: do not declare a static retention here for a
# table whose retention a settings field claims to own -- wire the field or remove it.
default_log_clearing_doctypes = {"Notification Log": 90}

ignore_links_on_delete = ["User Form Draft"]

portal_menu_items = [
	# The Training entry left in v1.429.2. Courses are taken in the Desk now, and
	# `Training Learner` keeps desk_access = 0, so a customer contact holding it
	# could not open the destination -- a menu item leading to a login page teaches
	# people to ignore the menu, which was the reason the entry was held back until
	# the page existed in the first place.
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
# FAC 3.0.0 (verified): the same three hooks, the same BaseTool contract, the same
# `_safe_execute` seam the write gate wraps. FAC's own category for each tool below is
# kept in step with its annotations by ai_governance/fac_tool_categories.py (the
# after_migrate + doc_events entries above) -- see that module for why it matters.
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
	# AI-authored trainings (draft -> confirm -> build). draft_course_spec is a
	# read tool: it turns a brief into a validated Course Spec via ERPNext's own
	# Vertex client and writes nothing. author_training_course is the write half
	# (APP_MUTATING): it builds a Training Course + unpublished draft from that
	# spec, deterministically, via the same engine the manual builder uses -- every
	# quiz question flagged unreviewed so publication stays gated on a human.
	"erpnext_enhancements.assistant_tools.draft_course_spec.DraftCourseSpec",
	"erpnext_enhancements.assistant_tools.author_training_course.AuthorTrainingCourse",
	# publish_training_course is the third step, and the one that had no tool at all:
	# publishing is NOT a document submit -- it is training_author.publish_version,
	# which materializes toc_json/content_hash from the lessons and only then submits.
	# So submit_document skipped the materializer and was refused by
	# _require_materialized_content, and nothing else could take a drafted course the
	# last inch. APP_MUTATING and Medium risk: it is a one-way door that freezes lesson
	# titles permanently and can fan assignments out to everyone.
	"erpnext_enhancements.assistant_tools.publish_training_course.PublishTrainingCourse",
	# create_training_draft_version is the rung BETWEEN authoring and publishing,
	# and its absence had a concrete cost: nothing could open a draft of a course
	# that already exists, so correcting a published course meant either editing the
	# live version's lessons in place -- which frappe permits, since a Training
	# Lesson is a plain document, and which the module forbids because learners are
	# reading that version -- or stopping to ask a human to press a button.
	# create_document cannot stand in: it makes an empty Training Course Version
	# with no lessons, and hand-copying the rows mints fresh lesson_key/block_key
	# values, which strands every resume position, checkpoint and video chapter,
	# because all of them join on the key rather than on an index.
	"erpnext_enhancements.assistant_tools.create_training_draft_version.CreateTrainingDraftVersion",
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
	"erpnext_enhancements.assistant_tools.workforce_clock_in.WorkforceClockIn",
	"erpnext_enhancements.assistant_tools.workforce_clock_out.WorkforceClockOut",
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
	# v1.335.0 Inventory -- read-only Item naming advisor, implementing the ERPNext Item
	# Naming Schema SOP (docs/item-naming-schema.md). ADVISORY ONLY: nothing this tool does
	# blocks a save -- the SOP itself says compliance is procedural, and a third of the live
	# catalogue would fail the comma rule. The one thing that does refuse is the v1.532.0
	# Item doc_event above, and only for a NEW Item whose code duplicates an existing one
	# after normalisation or whose name is just its code.
	#
	# All judgement lives in the pure inventory_enhancements.item_naming_rules (no frappe,
	# so CI actually executes it); inventory_enhancements.item_naming does the reads. That
	# split is load-bearing here rather than stylistic: block occupancy is a character-level
	# rule that a MariaDB regex gets wrong in both directions -- anchored, it reports
	# PDT-0008 free while the sole record of that product holds it; unanchored, the
	# five-digit "(deleted)" family collapses onto four-digit slots that are genuinely free.
	"erpnext_enhancements.assistant_tools.item_naming_check.ItemNamingCheck",
	# v1.339.0 CRM -- read-only naming advisor for the three doctypes that name themselves
	# after a party. Same posture as item_naming_check: advisory, no doc_event, every
	# judgement in the pure crm_enhancements.party_naming_rules so CI executes it.
	#
	# requires_permission is a single DocType and this tool spans three, so it gates on the
	# most broadly readable (Address) for VISIBILITY and re-checks the doctype actually
	# asked for inside execute(). Gating on the most restrictive would hide it from anyone
	# holding Opportunity but not Project, for no reason they could see.
	"erpnext_enhancements.assistant_tools.party_naming_check.PartyNamingCheck",
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
# monkeypatches.py for what/why — currently two:
#   1. stop a cached `None` (e.g. the `telephony` Module Def query) from crashing
#      get_modules_from_all_apps and the app switcher;
#   2. force-download every executable attachment type from /private/files/ and
#      set `nosniff`. v16's list is four extensions; frappe's develop widened it
#      to fourteen, and the seven in the gap are served INLINE from our own
#      origin with a scriptable Content-Type. nginx cannot hold this fix —
#      startup_script.sh regenerates its config from bench's template on boot.
from erpnext_enhancements.monkeypatches import apply as _apply_monkeypatches

_apply_monkeypatches()
