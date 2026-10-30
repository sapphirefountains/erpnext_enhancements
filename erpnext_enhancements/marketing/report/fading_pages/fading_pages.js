// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// R4: pages whose search clicks fell against the previous period or last year (v1.757.0). The rules are in marketing/performance/insights.py.

const DEFAULT_FROM = frappe.datetime.add_days(frappe.datetime.get_today(), -28);

frappe.query_reports["Fading Pages"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: DEFAULT_FROM },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
		{ fieldname: "against", label: __("Against"), fieldtype: "Select", options: "Previous period\nLast year", default: "Previous period" },
		{ fieldname: "drop_percent", label: __("Drop (%)"), fieldtype: "Int", description: __("Blank: Marketing Settings.") },
		{ fieldname: "min_baseline", label: __("Minimum baseline clicks"), fieldtype: "Int", description: __("Blank: Marketing Settings.") },
	],
};
