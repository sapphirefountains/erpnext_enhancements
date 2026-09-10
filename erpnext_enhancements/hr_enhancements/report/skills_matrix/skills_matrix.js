// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Skills Matrix"] = {
	filters: [
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
		{
			fieldname: "courses_only",
			label: __("Internal courses only"),
			fieldtype: "Check",
		},
		{
			fieldname: "credentials_only",
			label: __("External credentials only"),
			fieldtype: "Check",
		},
	],
	// Colour is the secondary signal; the cell says the word. Somebody reading this
	// on a phone in daylight, or colour-blind, gets the same answer either way --
	// and "Expiring" in particular MUST read as still-qualified, which a red cell
	// with no text would not.
	formatter(value, row, column, data, default_formatter) {
		const out = default_formatter(value, row, column, data);
		if (value === "Current") return `<span style="color: var(--green-600)">${out}</span>`;
		if (value === "Expiring") return `<span style="color: var(--orange-600)">${out}</span>`;
		if (value === "Lapsed") return `<span style="color: var(--red-600)">${out}</span>`;
		if (value === "Never") return `<span class="text-muted">${out}</span>`;
		return out;
	},
};
