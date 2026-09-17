// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Labor Cost Analysis"] = {
	filters: [
		{
			// Defaults to the last 30 days server-side rather than scanning every
			// interval ever recorded.
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			// Must equal GROUP_BY in labor_cost_analysis.py, in order — the test
			// compares the two, because a grouping offered here that the server
			// refuses is a report that errors for the reader who picked it.
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: "Project\nEmployee\nPosition\nActivity Type\nProject and Employee",
			default: "Project",
			reqd: 1,
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "position",
			label: __("Position"),
			fieldtype: "Link",
			options: "Position",
		},
		{
			fieldname: "time_category",
			label: __("Activity Type"),
			fieldtype: "Link",
			options: "Activity Type",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;
		if (column.fieldname === "unrated" && data.unrated > 0) {
			// Cost is understated on this row: some intervals closed with no pay rate.
			return `<span style="color: var(--orange-500); font-weight: 600;">${formatted}</span>`;
		}
		if (column.fieldname === "variance" && data.variance != null && data.variance < 0) {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
