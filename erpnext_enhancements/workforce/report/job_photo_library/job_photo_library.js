// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Job Photo Library"] = {
	filters: [
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			// The MASTER taxonomy is the plural doctype. The singular one is the
			// child table that attaches it to records — see the report docstring.
			fieldname: "value_stream",
			label: __("Value Stream"),
			fieldtype: "Link",
			options: "Value Streams",
		},
		{
			fieldname: "featured_only",
			label: __("Featured jobs only"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "from_date",
			label: __("Captured From"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
		},
		{
			fieldname: "to_date",
			label: __("Captured To"),
			fieldtype: "Date",
		},
	],
};
