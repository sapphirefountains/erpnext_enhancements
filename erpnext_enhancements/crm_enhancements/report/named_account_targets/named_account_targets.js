// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Named Account Targets"] = {
	filters: [
		{ fieldname: "industry", label: __("Industry"), fieldtype: "Link", options: "Industry Type" },
		{ fieldname: "territory", label: __("Territory"), fieldtype: "Link", options: "Territory" },
		{
			fieldname: "customer_type",
			label: __("Customer Type"),
			fieldtype: "Select",
			options: ["", "Commercial", "Company", "Residential", "Individual", "Partnership"].join("\n"),
		},
		{
			fieldname: "custom_account_status",
			label: __("Account Status"),
			fieldtype: "Select",
			options: ["", "Lead", "Prospect", "Client", "Champion", "Vendor", "Competitor", "Advocate"].join("\n"),
		},
		{
			// PLURAL doctype = the master list. See crm_enhancements/README.md.
			fieldname: "value_stream",
			label: __("Value Stream"),
			fieldtype: "Link",
			options: "Value Streams",
		},
		{ fieldname: "customer_group", label: __("Customer Group"), fieldtype: "Link", options: "Customer Group" },
		{
			fieldname: "no_recent_activity_days",
			label: __("No activity in the last N days"),
			fieldtype: "Int",
			default: 0,
		},
		// No scale filter: the event-planner scale taxonomy is undecided. Adding a
		// guessed one would ship a filter that quietly matches nothing.
	],
};
