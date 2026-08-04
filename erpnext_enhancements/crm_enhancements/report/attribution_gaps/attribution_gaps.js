// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.query_reports["Attribution Gaps"] = {
	filters: [
		{
			fieldname: "only_live_gaps",
			label: __("Only records with no source at all"),
			fieldtype: "Check",
			default: 0,
			// Hides the "Unknown (pre-Aug 2026)" backfill bucket, leaving only
			// records created since capture went in — i.e. the ones somebody is
			// working right now with no idea where they came from.
		},
		{
			fieldname: "open_only",
			label: __("Open opportunities only"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "owner_user",
			label: __("Owner"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "from_date",
			label: __("Created From"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("Created To"),
			fieldtype: "Date",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "gap" && data && data.gap === __("No source")) {
			return `<span style="color: var(--red-500); font-weight: 600;">${formatted}</span>`;
		}
		return formatted;
	},
};
