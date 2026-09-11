// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The Skills Matrix turned sideways: how many people hold each thing, rather than
// what each person holds. The number that matters is the small one.

frappe.query_reports["Qualification Coverage"] = {
	filters: [
		{
			fieldname: "show_all",
			label: __("Show everything"),
			fieldtype: "Check",
			default: 0,
			// Off by default on purpose. A report that opens on forty rows of "eight
			// people hold this" buries the three rows that matter, and the three rows
			// that matter are the whole point of the report.
			description: __("Off: only qualifications two or fewer people hold."),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (!data) return value;
		if (column.fieldname === "current") {
			// Colour AND the note column carry it -- a colour-only signal is one a
			// colour-blind manager reads as "all the same".
			if (data.current === 0) return `<span style="color:var(--red-600)">${value}</span>`;
			if (data.current === 1) return `<span style="color:var(--red-600);font-weight:600">${value}</span>`;
			if (data.current <= 2) return `<span style="color:var(--orange-600)">${value}</span>`;
		}
		return value;
	},
};
