// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Social Post Performance (TASK-2026-01488): each post on each network, from reach to revenue.
// The rules live in marketing/publish/performance.py. Deliberately no .html print template: a
// report print template is compiled whole, prose included (CLAUDE.md).

frappe.query_reports["Social Post Performance"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("Published From"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -90),
		},
		{
			fieldname: "to_date",
			label: __("Published To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "network",
			label: __("Network"),
			fieldtype: "Select",
			options: ["", "Facebook", "Instagram", "LinkedIn", "YouTube"],
		},
		{
			fieldname: "window_days",
			label: __("Attribution Window (days)"),
			fieldtype: "Int",
			default: 365,
		},
	],
};
