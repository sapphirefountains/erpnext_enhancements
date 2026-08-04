// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Featured Jobs"] = {
	filters: [
		{
			// On by default: the report exists to get a photographer booked before
			// the crew leaves, not to catalogue finished work.
			fieldname: "upcoming_only",
			label: __("Upcoming and in-progress only"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		// A flagged job with no photos yet is the whole point of the report.
		if (column.fieldname === "photo_count" && data && !data.photo_count &&
			data.timing !== __("Upcoming")) {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
