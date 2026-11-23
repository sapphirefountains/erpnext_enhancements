// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Pay Applications Outstanding (estimating B9 follow-up): applications still open, and locked jobs whose
 * last certified application ended longer ago than the reminder window ("Due"), or that never had one.
 */
frappe.query_reports["Pay Applications Outstanding"] = {
	filters: [
		{ fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project" },
		{
			fieldname: "due_after_days",
			label: __("Due after (days)"),
			fieldtype: "Int",
			default: 30,
			description: __("A locked job counts as due when its last certified period ended longer ago than this. Blank or 0 means 30."),
		},
	],
};
