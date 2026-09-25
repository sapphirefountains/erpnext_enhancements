// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Filters + coloring for Store Run Charge Matching (read-only; no buttons).
 *
 * The Show options are the buckets store_run_matching.py assigns, spelled exactly as it spells
 * them (tests/test_store_run_matching.py compares the two). Only What to Do is colored: it is
 * the column Accounting works down at step S-D.
 */
frappe.query_reports["Store Run Charge Matching"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -60),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			// Only the Suppliers ticked Store-Run Vendor. Picking "Lowes" also shows the trips
			// recorded at "Lowe's": the pairing treats the two as one store.
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Supplier",
			get_query: () => ({ filters: { custom_store_run_vendor: 1 } }),
		},
		{
			fieldname: "show",
			label: __("Show"),
			fieldtype: "Select",
			options: ["All", "Needs action", "Waiting", "Done"].join("\n"),
			default: "All",
		},
	],

	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname === "action" && data && data.show) {
			const color = {
				"Needs action": "var(--red-500)",
				Waiting: "var(--orange-500)",
				Done: "var(--green-600)",
			}[data.show];
			if (color) {
				return `<span style="color: ${color}; font-weight: 600;">${formatted}</span>`;
			}
		}
		return formatted;
	},
};
