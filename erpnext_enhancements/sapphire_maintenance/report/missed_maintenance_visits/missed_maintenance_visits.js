// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Missed Maintenance Visits (v1.595.0, TASK-2026-02475). The rows come from
// sapphire_maintenance/missed_visits.py, which the Visit Review page reads too; this file only
// declares the filters, colours the Kind column and turns Open into a link: the Visit Wizard
// for a draft (where the day the work was done can be set), the contract for a visit that was
// never drafted.
const MMV_KIND_COLOURS = {
	"Not drafted": "red",
	"Not started": "orange",
	"Started, not sent": "blue",
};

frappe.query_reports["Missed Maintenance Visits"] = {
	filters: [
		{
			fieldname: "kind",
			label: __("Kind"),
			fieldtype: "Select",
			options: ["", "Not drafted", "Not started", "Started, not sent"],
		},
		{
			fieldname: "technician",
			label: __("Technician"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "project",
			label: __("Site"),
			fieldtype: "Link",
			options: "Project",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "kind" && MMV_KIND_COLOURS[value]) {
			return `<span class="indicator-pill ${MMV_KIND_COLOURS[value]}">${frappe.utils.escape_html(value)}</span>`;
		}
		if (column.fieldname === "open" && data && value) {
			const name = encodeURIComponent(value);
			if (data.open_kind === "visit") {
				return `<a href="/desk/visit-wizard/${name}">${__("Open in Visit Wizard")}</a>`;
			}
			return `<a href="/desk/sapphire-maintenance-contract/${name}">${__("Open the contract")}</a>`;
		}
		return formatted;
	},
};
