// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Published articles whose review date has passed or is close (WI-080 PR 4). The rows come from
// knowledge_base/reporting.py; this file only declares the filters and colours the State column.
// An article number opens the article, where Confirm Still Accurate and Start Revision are.
const KB_DUE_STATE_COLOURS = {
	"No review date": "red",
	Overdue: "red",
	"Due soon": "orange",
};

frappe.query_reports["Knowledge Articles Due for Review"] = {
	filters: [
		{
			fieldname: "within_days",
			label: __("Due Within (Days)"),
			fieldtype: "Int",
			default: 30,
		},
		{
			fieldname: "process_owner",
			label: __("Process Owner"),
			fieldtype: "Link",
			options: "User",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		const colour = column.fieldname === "state" ? KB_DUE_STATE_COLOURS[value] : null;
		if (colour) {
			return `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(value)}</span>`;
		}
		return formatted;
	},
};
