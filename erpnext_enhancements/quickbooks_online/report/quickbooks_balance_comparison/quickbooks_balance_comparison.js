// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/* eslint-disable */

frappe.query_reports["QuickBooks Balance Comparison"] = {
	filters: [
		{
			fieldname: "as_of_date",
			label: __("As of Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "tolerance",
			label: __("Tolerance"),
			fieldtype: "Float",
			default: 0.01,
		},
		{
			fieldname: "only_discrepancies",
			label: __("Only Discrepancies"),
			fieldtype: "Check",
			default: 0,
		},
	],

	// Tint mismatches / one-sided rows so discrepancies stand out at a glance.
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (data && data.status && data.status !== __("Match")) {
			value = `<span style="color: var(--text-on-red, #c0392b)">${value}</span>`;
		}
		return value;
	},
};
