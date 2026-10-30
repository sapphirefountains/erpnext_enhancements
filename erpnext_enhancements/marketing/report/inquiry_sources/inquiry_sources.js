// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// R1: Opportunities by Lead Source and month, with wins, win rate, won value and days to close (v1.757.0). The rules are in marketing/performance/insights.py.

const DEFAULT_FROM = frappe.datetime.add_months(frappe.datetime.get_today(), -12);

frappe.query_reports["Inquiry Sources"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: DEFAULT_FROM },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
	],
};
