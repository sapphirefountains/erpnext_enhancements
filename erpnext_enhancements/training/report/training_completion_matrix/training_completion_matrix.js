// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// The colouring is the report. A matrix of forty text cells is unreadable at a
// glance, and "at a glance" is the only way anybody uses a compliance sheet.
const TRAINING_CELL_COLOURS = {
	Valid: "green",
	"Expiring Soon": "orange",
	Expired: "red",
	Superseded: "purple",
	Revoked: "red",
	"Older Version": "blue",
	Overdue: "red",
	"Awaiting Sign-off": "orange",
	"In Progress": "blue",
	"Not Started": "gray",
	Waived: "gray",
};

frappe.query_reports["Training Completion Matrix"] = {
	filters: [
		{
			fieldname: "learner_type",
			label: __("Learner Type"),
			fieldtype: "Select",
			options: "Employee\nWebsite User\nAll",
			default: "Employee",
			reqd: 1,
		},
		{
			fieldname: "weight",
			label: __("Weight"),
			fieldtype: "Select",
			options: "Required\nOptional\nAll",
			default: "Required",
		},
		{
			fieldname: "category",
			label: __("Category"),
			fieldtype: "Link",
			options: "Training Category",
		},
		{
			fieldname: "course",
			label: __("Single Course"),
			fieldtype: "Link",
			options: "Training Course",
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
			depends_on: 'eval:doc.learner_type != "Website User"',
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
			depends_on: 'eval:doc.learner_type != "Employee"',
		},
		{
			fieldname: "expiring_within_days",
			label: __("Flag Expiring Within (Days)"),
			fieldtype: "Int",
			default: 30,
		},
		{
			fieldname: "current_version_only",
			label: __("Flag Older Versions"),
			fieldtype: "Check",
			default: 0,
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		const colour = TRAINING_CELL_COLOURS[value];
		if (colour) {
			return `<span class="indicator-pill ${colour}">${frappe.utils.escape_html(value)}</span>`;
		}
		if (column.fieldname === "compliance_percent" && data) {
			const shade = data.compliance_percent >= 100 ? "green" : data.compliance_percent >= 80 ? "orange" : "red";
			return `<span style="color: var(--${shade}-500); font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
