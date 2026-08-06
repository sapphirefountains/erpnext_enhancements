// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Filters + colouring for the Hand-Off SLA Compliance report.
 *
 * "Not Started" is deliberately styled differently from "Overdue": those steps
 * are blocked upstream waiting on a prior step, not late, and colouring them red
 * would overstate lateness while hiding the stall.
 */
frappe.query_reports["Hand-Off SLA Compliance"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("Projects Touched Since"),
			fieldtype: "Date",
			// The date the hand-off tracker went live; earlier projects have no
			// meaningful steps. A filter, not a constant, so the window moves
			// without a deploy.
			default: "2026-06-10",
			reqd: 1,
		},
		{
			fieldname: "project",
			label: __("Project"),
			fieldtype: "Link",
			options: "Project",
		},
		{
			fieldname: "project_status",
			label: __("Project Status"),
			fieldtype: "Select",
			options: ["", "Open", "Active", "Completed", "Cancelled"].join("\n"),
		},
		{
			fieldname: "responsible_role",
			label: __("Responsible Role"),
			fieldtype: "Select",
			options: ["", "Account Executive", "Accounts Receivable", "Project Manager"].join("\n"),
		},
		{
			fieldname: "state",
			label: __("State"),
			fieldtype: "Select",
			options: ["", "Completed", "Skipped", "Overdue", "Due", "Not Started"].join("\n"),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;

		if (column.fieldname === "state") {
			if (data.state === __("Overdue")) {
				return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
			}
			if (data.state === __("Skipped")) {
				return `<span style="color: var(--orange-500); font-weight: 600;">${formatted}</span>`;
			}
			if (data.state === __("Not Started")) {
				return `<span style="color: var(--text-muted);">${formatted}</span>`;
			}
			if (data.state === __("Completed")) {
				return `<span style="color: var(--green-600);">${formatted}</span>`;
			}
		}

		if (column.fieldname === "days_over" && data.days_over) {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}

		if (column.fieldname === "launch_goal" && data.launch_goal === __("Missed")) {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}

		return formatted;
	},
};
