// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Opportunity Loss Reasons"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From (Last Modified)"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To (Last Modified)"),
			fieldtype: "Date",
		},
	],
};
