// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// R5: traffic, inquiries and won work per service line, from the mapping on Marketing Settings (v1.757.0). The rules are in marketing/performance/insights.py.

const DEFAULT_FROM = frappe.datetime.add_days(frappe.datetime.get_today(), -90);

frappe.query_reports["Service Line Funnel"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: DEFAULT_FROM },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
	],
};
