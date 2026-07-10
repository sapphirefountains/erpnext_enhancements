// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Hand-Off Process Coverage"] = {
	filters: [
		{
			fieldname: "opp_status",
			label: __("Opportunity Status"),
			fieldtype: "Select",
			options: ["", "Open", "Quotation", "Converted", "Closed Won", "Closed Lost"].join("\n"),
			default: "Closed Won",
		},
		{
			fieldname: "coverage",
			label: __("Tracker"),
			fieldtype: "Select",
			options: ["", "Started", "Not Started"].join("\n"),
			default: "Not Started",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "tracker_started" && data && data.tracker_started === __("No")) {
			value = `<span style="color: var(--text-on-orange, #b45309); font-weight: 600;">${value}</span>`;
		}
		return value;
	},
};
