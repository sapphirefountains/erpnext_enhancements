// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Job Photo Compliance"] = {
	filters: [
		{
			// Defaults to the last 30 days server-side rather than scanning every
			// interval ever recorded.
			fieldname: "from_date",
			label: __("Closed From"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
		},
		{
			fieldname: "to_date",
			label: __("Closed To"),
			fieldtype: "Date",
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			// "Pending upload" is not a compliance problem — the photo exists, the
			// site has no signal. Excluded from this filter on purpose.
			fieldname: "only_problems",
			label: __("Only jobs with no photo at all"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "result" && data) {
			if (data.result === __("Missing")) {
				return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
			}
			if (data.result === __("Pending upload")) {
				return `<span style="color: var(--orange-500);">${formatted}</span>`;
			}
		}
		return formatted;
	},
};
