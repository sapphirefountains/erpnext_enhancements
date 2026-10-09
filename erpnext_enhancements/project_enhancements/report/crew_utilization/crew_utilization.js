// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Filters and coloring for the Crew Utilization report (Project Planner Phase 4).
 *
 * Booked hours come from the same availability engine as both planners; Actual is the kiosk's
 * clocked hours on customer jobs. A period over 100% is booked past the person's capacity and is
 * shown red; a blank percentage means the person had no capacity that period.
 */
frappe.query_reports["Crew Utilization"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "granularity",
			label: __("Per"),
			fieldtype: "Select",
			options: ["Week", "Month"].join("\n"),
			default: "Week",
		},
		{
			fieldname: "group",
			label: __("Group"),
			fieldtype: "Select",
			options: ["", "Field", "PM", "Design", "Subcontractor"].join("\n"),
		},
		{
			fieldname: "resource",
			label: __("Person"),
			fieldtype: "Link",
			options: "Planner Resource",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (!data || column.fieldname !== "utilization" || value === null || value === undefined) {
			return formatted;
		}
		if (value > 100) {
			return `<span style="color: var(--red-600); font-weight: 600">${formatted}</span>`;
		}
		if (value >= 75) {
			return `<span style="color: var(--orange-600)">${formatted}</span>`;
		}
		return formatted;
	},
};
