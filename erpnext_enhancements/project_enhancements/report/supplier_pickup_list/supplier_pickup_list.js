// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Filters + row colouring for the Supplier Pickup List.
 *
 * A line past its promised date is the one thing worth spotting from across the
 * warehouse, so it is the only thing coloured. "Due today" is deliberately not
 * red: the vendor still has the rest of the day.
 */
frappe.query_reports["Supplier Pickup List"] = {
	filters: [
		{
			fieldname: "supplier",
			label: __("Supplier"),
			fieldtype: "Link",
			options: "Supplier",
			// Optional on purpose. Blank is the planning view — every counter
			// with something on it — and the print template sections it by
			// supplier so that sheet is still one page per stop.
		},
		{
			fieldname: "project",
			label: __("Job"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "expected_by",
			label: __("Expected On or Before"),
			fieldtype: "Date",
		},
		{
			fieldname: "include_settled",
			label: __("Include Closed / Delivered Orders"),
			fieldtype: "Check",
			default: 0,
			// Off by default because on production most orders reading
			// per_received < 100 are Closed — see the report's docstring.
		},
	],

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (data && data.schedule_date && frappe.datetime.get_day_diff(frappe.datetime.get_today(), data.schedule_date) > 0) {
			value = `<span style="color: var(--red-500)">${value}</span>`;
		}

		return value;
	},
};
