// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Unclaimed Travel Expenses"] = {
	filters: [
		{
			fieldname: "min_days_since_end",
			label: __("Minimum Days Since Trip End"),
			fieldtype: "Int",
			default: 0,
		},
	],
};
