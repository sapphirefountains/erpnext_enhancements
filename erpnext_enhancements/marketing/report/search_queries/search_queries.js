// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// R2: each search query against the previous period and the same period last year, new and lost, branded or not (v1.757.0). The rules are in marketing/performance/insights.py.

const DEFAULT_FROM = frappe.datetime.add_days(frappe.datetime.get_today(), -28);

frappe.query_reports["Search Queries"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: DEFAULT_FROM },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
	],
};
