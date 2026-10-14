"""The guided flow for an Opportunity: how its form is walked one step at a time.

The layout itself is frozen (docs/opportunity-field-guide.md, 2026-06-09) and every field the
form shows stays in the flow (owner, 2026-10-13: "keep showing everything"). So this file never
hides anything the form shows. It only says which of the form's sections go together, in what
order, what each step is called and the one sentence that introduces it, which steps apply to
which value streams, and what step 1 needs before the record can exist. It changes by PR, like
the layout.

Section keys are the Section Break fieldnames. ``_top`` is the run of fields before the first
Section Break (on Opportunity, the whole top of the Details tab), and a Tab Break whose first
field is not a Section Break opens a section keyed by the tab's own fieldname
(``custom_schedule``, ``custom_process_tab``).
"""

#: The per-stream rule ``toggle_value_stream_fields`` applies on the form
#: (public/js/crm_enhancements/opportunity_migrated_scripts.js): each Value Stream row shows
#: its sections. A step that ``applies`` to a stream is offered only while that stream is chosen.
STREAMS_FIELD = "custom_value_stream"
STREAM_ROW_FIELD = "value_stream"


def _stream(name):
	return {"field": STREAMS_FIELD, "row_field": STREAM_ROW_FIELD, "in": [name]}


#: The Customer Requests table for each stream that has one, and the text column a row's
#: request is written in. Products and Delivery have no requests table, so they never count.
REQUEST_TABLES = {
	"Design": ["custom_design_customer_requests", "design_customer_requests"],
	"Build": ["custom_build_customer_requests", "build_customer_requests"],
	"Service": ["custom_service_customer_requests", "service_customer_requests"],
	"Events": ["custom_rent_customer_requests", "rent_customer_requests"],
}

# --------------------------------------------------------------------------- completeness
# Decided from prod fill rates (TASK-2026-02773). Cumulative. Rarely filled fields (scope
# description, requested dates, budget notes, the Events schedule, budgets, the initial
# invoice, contributors, deliverables) are deliberately not counted.

QUALIFICATION = [
	{"field": "party_name", "label": "Account"},
	{"field": "custom_opportunity_name", "label": "Opportunity Name"},
	{"field": STREAMS_FIELD, "row_field": STREAM_ROW_FIELD, "label": "At least one Value Stream"},
	{"field": "opportunity_owner", "label": "Opportunity Owner"},
	{"field": "custom_opportunity_summary", "label": "Opportunity Summary"},
	{"any": ["primary_contact", "contact_person"], "label": "A contact"},
]
NEEDS_ANALYSIS = [
	*QUALIFICATION,
	{"field": "custom_lead_source", "label": "Lead Source"},
	{"field": "expected_closing", "label": "Expected Closing Date"},
	{
		"chosen_rows": {"field": STREAMS_FIELD, "row_field": STREAM_ROW_FIELD, "tables": REQUEST_TABLES},
		"label": "Customer Requests for a chosen value stream",
	},
]
PROPOSAL = [
	*NEEDS_ANALYSIS,
	{"field": "opportunity_amount", "positive": True, "label": "Opportunity Amount"},
	# The job site only: customer_address is the Billing Address (Nik, 2026-10-13).
	{"field": "primary_address", "label": "A site address"},
]
CLOSED_WON = [
	*PROPOSAL,
	{"field": "custom_scope_rank", "label": "Scope Rank"},
	{"field": "custom_schedule_rank", "label": "Schedule Rank"},
	{"field": "custom_budget_rank", "label": "Budget Rank"},
]
# Lost can happen at any stage, so it asks for the basics and the reason, not a proposal's
# amount (owner, 2026-10-13: "Basics + lost reason"). The reason is ERPNext's ``lost_reasons`` table: the app's own ``custom_lost_reason``
# Select was removed in v1.159.0 (patches/remove_opportunity_lost_reason.py), and
# ``script_migrations.opportunity.validate_close_reason`` requires the table.
LOST = [*QUALIFICATION, {"field": "lost_reasons", "row_field": "lost_reason", "label": "A lost reason"}]

#: Website and lead attribution, written by the capture and copied from the Lead: read-only on
#: the Desk form and left out of the flow (Nik, 2026-10-13), since typing into them rewrites
#: where the deal came from.
ATTRIBUTION_FIELDS = (
	"custom_utm_source",
	"custom_utm_medium",
	"custom_utm_campaign",
	"custom_utm_id",
	"custom_utm_content",
	"custom_utm_term",
	"custom_gclid",
	"custom_landing_page",
	"custom_first_referrer",
	"custom_attribution_captured_on",
)
_ATTRIBUTION = "Nik, 2026-10-13: attribution history from the website capture; read-only on the Desk form."
_COMPANY_CURRENCY = (
	"Nik, 2026-10-13: deals are always in the company currency, so it only repeats Opportunity "
	"Amount; the Desk form hides it too."
)

CONFIG = {
	"doctype": "Opportunity",
	# HTML fields whose content the form's JavaScript draws: there is nothing to fill in, and the
	# flow cannot draw them. Each one's reason is what it is on the form.
	"skip_fields": {
		"custom_reminder_action": "The reminder button the form script draws beside the value streams.",
		"contact_list_html": "The Contact Directory widget (unified_tab_controller.js); the flow offers Primary Contact.",
		"location_map_html": "The site map the form script draws for the primary address.",
		"address_list_html": "The Address Directory widget (unified_tab_controller.js); the flow offers Primary Address.",
		"custom_process_progress": "The Hand-Off Process panel (form JS); the Hand-off step links to it.",
		"contact_html": "ERPNext's contact cards, drawn by frappe.contacts on the form.",
		"address_html": "ERPNext's address cards, drawn by frappe.contacts on the form.",
		"open_activities_html": "ERPNext's CRM open-activities widget.",
		"all_activities_html": "ERPNext's CRM activity timeline widget.",
		"notes_html": "ERPNext's CRM notes widget.",
		# Fields the Desk form keeps that the flow leaves out (Nik's review, 2026-10-13).
		"language": "Nik, 2026-10-13: not needed in the flow; it stays on the Desk form.",
		# frappe.new_doc fills it with the one option there is, so a new record still gets its ID.
		"naming_series": "Nik, 2026-10-13: one series that never changes; it stays on the Desk form.",
		"conversion_rate": _COMPANY_CURRENCY,
		"base_opportunity_amount": _COMPANY_CURRENCY,
		**{fieldname: _ATTRIBUTION for fieldname in ATTRIBUTION_FIELDS},
	},
	# Fields the form hides that the flow shows, because the form fills them through a widget the
	# flow cannot draw, or because the owner named them for a step. Owner, 2026-10-13: Primary
	# Contact and the general scope description, and not the scope contributors, which stay hidden
	# on the form and in the flow alike.
	"show_hidden": {
		"primary_contact": (
			"Its section (primary_contact_section) is hidden on the form, where the Contact Directory "
			"widget sets it; the flow has no widget, so it offers the Link."
		),
		"custom_general_scope_description": (
			"Owner, 2026-10-13: the Scope step has the general description. On the form it sits in an "
			"unlabeled, hidden section."
		),
	},
	# Copies the form script makes from the chosen contact (primary_contact.js): shown, never
	# written by the flow, so a stale copy can never be typed over the contact's own value.
	"read_only_fields": {
		"primary_contact_job_title": "Copied from the Primary Contact by primary_contact.js.",
		"primary_contact_phone": "Copied from the Primary Contact by primary_contact.js.",
		"primary_contact_email": "Copied from the Primary Contact by primary_contact.js.",
	},
	# Named above, present on production (the Opportunity field_order Property Setter and the
	# primary_contact_* Property Setters list them) but not created by this repository, so the
	# test meta built from the repo does not have them. The schema test excuses only these.
	"site_only_fields": {
		"primary_contact_job_title",
		"primary_contact_phone",
		"primary_contact_email",
	},
	"create": {
		# Step 1: what the record needs to exist. Saving step 1 inserts the draft.
		"fields": [
			"opportunity_from",
			"party_name",
			"custom_opportunity_name",
			STREAMS_FIELD,
			"opportunity_owner",
			# Required on a NEW Opportunity whenever Lead Attribution Settings turns on
			# require_lead_source_on_opportunity (crm_enhancements.attribution.enforce_source),
			# so it is asked for here rather than failing the insert.
			"custom_lead_source",
		],
		# Required fields with no static default that the new record still fills itself.
		"filled_on_create": {
			"company": "frappe.new_doc sets it from the user's or the global default company.",
		},
		# Hidden fields a deal started elsewhere arrives with (the guided page is where every new
		# Opportunity opens, Nik, 2026-10-13), taken on create alongside the flow's own fields.
		"prefill_fields": {
			"contact_person": (
				"ERPNext's Create > Opportunity on a Customer sets it (customer._set_missing_values), "
				"as does Create Opportunity on a Lead (public/js/lead.js); hidden on the form."
			),
			"custom_description": "Create Opportunity on a Lead copies the Lead's details into it; hidden.",
			"company": "Create Opportunity on a Lead copies the Lead's company; hidden, with a default.",
		},
	},
	# The guided page's start screen: the caller's open deals, newest change first. Historical
	# deals (crm_enhancements/historical.py) are left out; they are not being worked.
	"mine": {
		"label": "Your open opportunities",
		"owner_field": "opportunity_owner",
		"title_field": "custom_opportunity_name",
		"detail_fields": ["customer_name", "status"],
		"open": {"field": "status", "not_in": ["Closed Won", "Lost", "Closed", "Converted"]},
		"hide_flag": "custom_is_historical",
	},
	"steps": [
		{
			"key": "basics",
			"title": "Basics",
			"intro": (
				"Who the opportunity is for, what to call it, its value streams, owner and lead source; "
				"saving this step creates it."
			),
			"sections": ["_top"],
		},
		{
			"key": "contact",
			"title": "Contact & site",
			"intro": (
				"The person we deal with and the job site, picked from the account's contacts and "
				"addresses; add new ones on the form."
			),
			"sections": ["primary_contact_section", "section_break_map"],
		},
		{
			"key": "scope",
			"title": "Scope",
			"intro": "The whole job in a paragraph; each value stream you chose gets its own page next.",
			"sections": ["custom_section_break_zozu1", "custom_section_break_ieovn"],
		},
		{
			"key": "scope_design",
			"title": "Design scope",
			"intro": (
				"Design requests in the customer's own words, and the deliverables PM and Design break "
				"them into."
			),
			"sections": ["custom_design_scope"],
			"applies": _stream("Design"),
		},
		{
			"key": "scope_build",
			"title": "Build scope",
			"intro": (
				"Build requests in the customer's own words, and the deliverables PM and Design break "
				"them into."
			),
			"sections": ["custom_build_scope"],
			"applies": _stream("Build"),
		},
		{
			"key": "scope_service",
			"title": "Service scope",
			"intro": (
				"Service requests in the customer's own words, and the deliverables PM and Design break "
				"them into."
			),
			"sections": ["custom_service_scope"],
			"applies": _stream("Service"),
		},
		{
			"key": "scope_events",
			"title": "Events scope",
			"intro": (
				"Event requests in the customer's own words, and the deliverables PM and Design break "
				"them into."
			),
			"sections": ["custom_rent_scope"],
			"applies": _stream("Events"),
		},
		{
			"key": "schedule",
			"title": "Schedule",
			"intro": (
				"When the customer wants the work to start and finish, and anything that limits the "
				"timing."
			),
			"sections": ["custom_schedule", "custom_schedule_notes"],
		},
		{
			"key": "schedule_events",
			"title": "Events schedule",
			"intro": (
				"Delivery, setup, the event itself and take-down, in that order, each with its own " "notes."
			),
			"sections": ["custom_rent"],
			"applies": _stream("Events"),
		},
		{
			"key": "budget",
			"title": "Budget",
			"intro": (
				"What the opportunity is worth, what it should cost, and how the first invoice goes " "out."
			),
			"sections": [
				"custom_customer_budget",
				"section_break_14",
				"custom_budget_notes",
				"custom_initial_invoice_section",
			],
		},
		{
			"key": "close",
			"title": "Close",
			"intro": (
				"Where the deal stands: Closed Won needs scope, schedule and budget ranked 1, 2 and 3; "
				"choosing Lost asks for the reasons."
			),
			"fields": [
				"status",
				"custom_scope_rank",
				"custom_schedule_rank",
				"custom_budget_rank",
				"custom_date_closed_won",
				"custom_stage_changed_on",
				# Read-only, set by the nightly job (crm_enhancements/historical.py): shown
				# beside the other status stamps, like custom_stage_changed_on.
				"custom_is_historical",
			],
			"sections": ["lost_detail_section"],
		},
		{
			"key": "more",
			"title": "More details",
			"intro": "About the account and where the lead came from; most of this fills itself in.",
			"sections": ["organization_details_section", "custom_attribution_section", "more_info"],
		},
		{
			"key": "handoff",
			"title": "Hand-off",
			"intro": (
				"What happened after the win, shown read-only; book and record the hand-off meeting on "
				"the form's Hand-Off Process tab."
			),
			"fields": ["custom_created_project"],
			"sections": ["custom_process_tab"],
			"read_only": True,
			"final": True,
			"link": {"label": "Open the Hand-Off Process tab", "tab": "custom_process_tab"},
		},
	],
	"stages": {
		"field": "status",
		"default": "Qualification",
		"lists": {
			"Qualification": QUALIFICATION,
			"Needs Analysis": NEEDS_ANALYSIS,
			"Proposal/Price Quote": PROPOSAL,
			"Negotiation/Review": PROPOSAL,
			"Closed Won": CLOSED_WON,
			"Lost": LOST,
			"On Hold": QUALIFICATION,
		},
	},
}
