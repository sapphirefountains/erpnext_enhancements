// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// R6: incoming calls nobody answered and nobody called back in time, first-time callers first (v1.757.0). The rules are in marketing/performance/insights.py.

const DEFAULT_FROM = frappe.datetime.add_days(frappe.datetime.get_today(), -30);

frappe.query_reports["Missed Calls"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: DEFAULT_FROM },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
		{ fieldname: "callback_hours", label: __("Callback window (hours)"), fieldtype: "Int", description: __("Blank: Marketing Settings.") },
	],
};
