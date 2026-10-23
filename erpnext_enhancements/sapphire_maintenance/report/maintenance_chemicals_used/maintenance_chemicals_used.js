// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Maintenance Chemicals Used (v1.731.0, TASK-2026-02480). The rows come from
// sapphire_maintenance/chemical_usage.py; this file declares the filters and shows a site as its
// name. The month filters take any day of the month (the desk has no month picker): the server
// snaps the start to the 1st and the end to the month's last day. The defaults are the last three
// full months plus the current one, the same window chemical_usage.default_months works out.
// The Visit state options must match chemical_usage.STATES (a test compares them).
frappe.query_reports["Maintenance Chemicals Used"] = {
	filters: [
		{
			fieldname: "from_month",
			label: __("From Month"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.month_start(), -3),
			reqd: 1,
		},
		{
			fieldname: "to_month",
			label: __("To Month"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "project",
			label: __("Site"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "item",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "state",
			label: __("Visit state"),
			fieldtype: "Select",
			options: ["Approved", "Approved or waiting for review", "Including drafts"],
			default: "Approved",
			reqd: 1,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "project" && data && value) {
			const name = frappe.utils.escape_html(value);
			const label = data.site_name ? `${name} ${frappe.utils.escape_html(data.site_name)}` : name;
			return `<a href="/desk/project/${encodeURIComponent(value)}" data-doctype="Project" data-name="${name}">${label}</a>`;
		}
		return default_formatter(value, row, column, data);
	},
};
