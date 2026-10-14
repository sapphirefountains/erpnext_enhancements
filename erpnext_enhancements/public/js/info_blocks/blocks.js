// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * Form kit P3.1: the information blocks, the JavaScript twin of form_kit/blocks.py.
 *
 * One definition per piece of information (Party, Site, Schedule window, Scope by pillar, Money,
 * Crew): the fields that carry it on each DocType, and its three faces (the entry component, the
 * report face's column kind, the print helper). This file is form_kit.blocks.resolved() written
 * out: the Python registry points at utils/date_pairs.PAIRS and the Opportunity config's request
 * tables, and the twin holds the result. tests/test_form_kit_blocks.py imports it under node and
 * fails the build if the two differ by a single key, so change the Python first and copy the
 * result here.
 *
 * A face that is `pending` names the task that builds it. Pure: no frappe, no DOM. P3.2's Party
 * component (form_kit/ui/party.js, v1.677.0) is its first reader: it takes the Party and Site
 * blocks' `roles` from here, through form_kit/party.js party_roles.
 */

const BLOCKS_TASK = "TASK-2026-02753";
const PARTY_ENTRY = { module: "public/js/form_kit/ui/party.js", export: "PartyEntry" };

const SCOPE_STREAMS = {
	Design: { requests: "custom_design_customer_requests", deliverables: "custom_design_deliverables" },
	Build: { requests: "custom_build_customer_requests", deliverables: "custom_build_deliverables" },
	Service: { requests: "custom_service_customer_requests", deliverables: "custom_service_deliverables" },
	Events: { requests: "custom_rent_customer_requests", deliverables: "custom_rent_deliverables" },
};

const SCOPE_FIELDS = [
	"custom_value_stream",
	"custom_design_customer_requests",
	"custom_design_deliverables",
	"custom_build_customer_requests",
	"custom_build_deliverables",
	"custom_service_customer_requests",
	"custom_service_deliverables",
	"custom_rent_customer_requests",
	"custom_rent_deliverables",
];

const DATE_PAIRS = [
	["custom_project_start_date", "custom_project_end_date"],
	["custom_delivery_date_time", "custom_setup_date_time", "custom_event_date_time", "custom_take_down_date_time"],
];

function scope() {
	return {
		fields: [...SCOPE_FIELDS],
		streams_field: "custom_value_stream",
		streams: JSON.parse(JSON.stringify(SCOPE_STREAMS)),
	};
}

export const BLOCKS = {
	party: {
		label: "Party",
		what: "Who the record is for: the account, the person we deal with, and where invoices go.",
		faces: {
			entry: { ...PARTY_ENTRY },
			report: { kinds: ["party"] },
			print: { helpers: ["erpnext_enhancements.print_lookup.ps_party"] },
		},
		doctypes: {
			Opportunity: {
				fields: ["opportunity_from", "party_name", "primary_contact", "customer_address"],
				mirrors: { primary_contact: "contact_person" },
				roles: {
					account_type: "opportunity_from",
					account: "party_name",
					contact: "primary_contact",
					address: "customer_address",
				},
				accounts: ["Customer", "Lead", "Prospect"],
			},
			Project: {
				fields: ["customer", "primary_contact", "custom_billing_address"],
				roles: { account: "customer", contact: "primary_contact", address: "custom_billing_address" },
				accounts: ["Customer"],
			},
		},
	},
	site: {
		label: "Site",
		what: "Where the work is: the job site address.",
		faces: {
			entry: { ...PARTY_ENTRY },
			report: { kinds: ["party"] },
			print: { helpers: ["erpnext_enhancements.print_style.ps_address"] },
		},
		doctypes: {
			Opportunity: { fields: ["primary_address"], roles: { address: "primary_address" } },
			Project: { fields: ["primary_address"], roles: { address: "primary_address" } },
		},
	},
	schedule_window: {
		label: "Schedule window",
		what: "When it runs: start and end, and on an event the delivery-to-take-down sequence.",
		faces: {
			entry: {
				pending: BLOCKS_TASK,
				builds:
					"A start/end pair control. Until then the step renderer draws Frappe's Date and " +
					"Datetime controls and utils/date_pairs warns on save.",
			},
			report: { kinds: ["date", "datetime"] },
			print: {
				helpers: ["erpnext_enhancements.print_style.ps_date", "erpnext_enhancements.print_style.ps_datetime"],
			},
		},
		doctypes: {
			Opportunity: {
				fields: DATE_PAIRS.flat(),
				pairs: DATE_PAIRS.map((pair) => [...pair]),
				core_pairs: [],
			},
			Project: {
				fields: [
					...DATE_PAIRS.flat(),
					"expected_start_date",
					"expected_end_date",
					"actual_start_date",
					"actual_end_date",
				],
				pairs: DATE_PAIRS.map((pair) => [...pair]),
				core_pairs: [
					["expected_start_date", "expected_end_date"],
					["actual_start_date", "actual_end_date"],
				],
			},
		},
	},
	scope_by_pillar: {
		label: "Scope by pillar",
		what: "What is in scope for each chosen value stream: its Customer Requests and Deliverables.",
		faces: {
			entry: { module: "public/js/form_kit/fields.js", export: "text_list_column" },
			report: { kinds: ["pillar"] },
			print: {
				helpers: ["erpnext_enhancements.print_style.STREAM_PILLAR", "erpnext_enhancements.print_style.pillar"],
			},
		},
		doctypes: {
			Opportunity: scope(),
			Project: scope(),
		},
	},
	money: {
		label: "Money",
		what: "What the job is worth and what it may cost.",
		faces: {
			entry: {
				pending: BLOCKS_TASK,
				builds:
					"A money input. Until then Frappe's Currency control, with the decimal keyboard " +
					"form_kit/fields.js inputmode_for asks a phone for.",
			},
			report: { kinds: ["money"] },
			print: { helpers: ["frappe.format"] },
		},
		doctypes: {
			Opportunity: {
				fields: ["opportunity_amount", "custom_materials_budget", "currency"],
				currency: "currency",
			},
			Project: {
				fields: ["custom_project_dollar_amount", "estimated_costing", "custom_materials_budget", "custom_bid_cost"],
				currency: null,
			},
		},
	},
	crew: {
		label: "Crew",
		what: "Who works on it: the owner or manager, the technical lead and the scope contributors.",
		faces: {
			entry: {
				pending: BLOCKS_TASK,
				builds: "A people picker. Until then Frappe's Link controls and the contributors grid.",
			},
			report: { kinds: ["party"] },
			print: {
				pending: BLOCKS_TASK,
				builds: "A print helper that names a person; no ps_ helper prints one today.",
			},
		},
		doctypes: {
			Opportunity: { fields: ["opportunity_owner", "custom_scope_contributors"] },
			Project: { fields: ["custom_project_owner", "custom_technical_lead", "custom_scope_contributors"] },
		},
	},
};

// The fields that carry `block` on `doctype` (hidden mirrors excluded), or [].
export function fields_for(block, doctype) {
	const entry = Object.prototype.hasOwnProperty.call(BLOCKS, block) ? BLOCKS[block] : null;
	const spec = entry && Object.prototype.hasOwnProperty.call(entry.doctypes, doctype) ? entry.doctypes[doctype] : null;
	return spec ? [...spec.fields] : [];
}

// { visible field: hidden field } the Party block keeps equal on `doctype`.
export function mirrors_for(doctype) {
	const out = {};
	Object.values(BLOCKS).forEach((entry) => {
		const spec = Object.prototype.hasOwnProperty.call(entry.doctypes, doctype) ? entry.doctypes[doctype] : null;
		Object.assign(out, (spec && spec.mirrors) || {});
	});
	return out;
}
