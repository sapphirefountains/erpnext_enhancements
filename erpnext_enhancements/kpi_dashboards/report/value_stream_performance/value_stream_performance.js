// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Value Stream Performance"] = {
	// NO required filters, and none that must be set before the report renders.
	// This is opened live in a standing weekly meeting; a report that greets you
	// with an empty filter bar wastes the first ninety seconds of it every week.
	// The server defaults to a rolling twelve months when these are blank.
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date" },
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (!data) return formatted;

		// The unallocated row is a caveat, not a value stream — grey it so nobody
		// reads it as a seventh stream in a meeting.
		if (data.value_stream === "Unallocated") {
			return `<span style="color: var(--text-muted); font-style: italic;">${formatted}</span>`;
		}
		if (column.fieldname === "win_rate" && data.win_rate != null) {
			const colour = data.win_rate >= 50 ? "var(--green-600)" : data.win_rate >= 25 ? "var(--orange-500)" : "var(--red-500)";
			return `<span style="color: ${colour}; font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
