// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Billed vs Scheduled (estimating B9 follow-up). Pick a schedule of values to see its lines by phase
 * (or one row per line); leave it blank for one row per job.
 */
frappe.query_reports["Billed vs Scheduled"] = {
	filters: [
		{ fieldname: "schedule_of_values", label: __("Schedule of Values"), fieldtype: "Link", options: "Schedule of Values" },
		{ fieldname: "group_by", label: __("Rows"), fieldtype: "Select", options: "Phase\nLine", default: "Phase", depends_on: "schedule_of_values" },
		{ fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project", depends_on: "eval:!doc.schedule_of_values" },
		{
			fieldname: "include_drafts",
			label: __("Include draft schedules"),
			fieldtype: "Check",
			default: 0,
			depends_on: "eval:!doc.schedule_of_values",
		},
	],
};
