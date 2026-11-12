// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Supplier Returns: Money Outstanding (v1.807.0). Every filter is optional; the server builds its
// conditions from the ones that hold a value. The View options are
// `supplier_return_rules.REPORT_VIEWS`, and `tests/test_supplier_returns.py` holds the two equal.
frappe.query_reports["Supplier Returns Money Outstanding"] = {
	filters: [
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Waiting on money", "Came back short or over", "All returns"],
			default: "Waiting on money",
		},
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "from_date", label: __("Returns Dated From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date" },
	],
	formatter(value, row, column, data, default_formatter) {
		const text = default_formatter(value, row, column, data);
		if (!data) return text;
		if (column.fieldname === "days_waiting" && data.days_waiting > 30) {
			return `<span style="color: var(--red-600); font-weight: 600;">${text}</span>`;
		}
		if (column.fieldname === "money_source" && data.money_source && data.money_source.startsWith("Typed by hand")) {
			return `<span style="color: var(--orange-600);">${text}</span>`;
		}
		return text;
	},
};
