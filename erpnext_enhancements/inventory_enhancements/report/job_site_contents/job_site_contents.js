// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Job Site Contents: what is at each job site, by job (v1.805.0).
 * The reasoning lives in job_site_contents.py.
 *
 * Every filter is optional. With none, the report is everything out on every
 * job. Another page can route here with frappe.route_options = { project }.
 */

// Stock that has sat at a site this many days or more is shown in orange.
const SF_JOB_SITE_LONG_STAY_DAYS = 60;

frappe.query_reports["Job Site Contents"] = {
	filters: [
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "item_code",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			// A group includes the groups under it.
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const face = window.erpnext_enhancements?.report_face;
		const formatted = face?.format
			? face.format(value, row, column, data, default_formatter)
			: default_formatter(value, row, column, data);
		if (column.fieldname === "days_there" && data && data.days_there >= SF_JOB_SITE_LONG_STAY_DAYS) {
			return `<span style="color: var(--orange-600); font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
