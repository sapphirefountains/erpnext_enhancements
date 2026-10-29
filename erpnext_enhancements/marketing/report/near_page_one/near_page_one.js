// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// R3: queries and pages at positions 8-20, ranked by the clicks a move to position 3 would bring (v1.757.0). The rules are in marketing/performance/insights.py.

const DEFAULT_FROM = frappe.datetime.add_days(frappe.datetime.get_today(), -28);

frappe.query_reports["Near Page One"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: DEFAULT_FROM },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
		{ fieldname: "kind", label: __("Of"), fieldtype: "Select", options: "Query\nPage", default: "Query" },
		{ fieldname: "min_impressions", label: __("Minimum impressions"), fieldtype: "Int", description: __("Blank: Marketing Settings.") },
	],
};
