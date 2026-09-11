// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/* Crew Qualification Roster filters.
 *
 * The Employee link deliberately does NOT filter on `status = "Active"`. The whole
 * point of an as-of date is that it can answer a question about somebody who has
 * since left, and after an incident a leaver is exactly who gets asked about. A
 * status filter here would make them unselectable and the report would look complete.
 */

frappe.query_reports["Crew Qualification Roster"] = {
	filters: [
		{
			fieldname: "as_of",
			label: __("As of"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
			description: __(
				"Everything is re-derived from stored dates as at this day. Facts with no recorded date are reported as not reconstructible rather than given today's value."
			),
		},
		{
			fieldname: "employee",
			label: __("Person"),
			fieldtype: "Link",
			options: "Employee",
			get_query: function () {
				// No status filter -- see the header.
				return {};
			},
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
	],

	onload: function (report) {
		report.page.add_inner_button(__("Today"), function () {
			report.set_filter_value("as_of", frappe.datetime.get_today());
		});
	},
};
