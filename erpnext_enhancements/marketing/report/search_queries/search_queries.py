# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Search Queries (R2, v1.757.0): each query against the previous period and the same period last year.

Clicks, impressions, CTR and average position over the range, the change in clicks against the
range just before it and against the same dates a year earlier (364 days back, so weekdays line up),
and whether the query is **new** (absent before) or **lost** (present before, absent now). Branded
queries contain a term from Marketing Settings' *Branded Search Terms*. From the stored Search Console
rows, so last year's column fills in as the history does. The rules are
``performance.insights.compare_queries``.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_enhancements.marketing.performance import data, insights, rules


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not rules.views_for(frappe.get_roles())["page"]:
		frappe.throw(_("This report is for the marketing team and Sales Manager."), frappe.PermissionError)
	start, end = rules.parse_range(filters.get("from_date"), filters.get("to_date"), getdate(nowdate()))
	prev = rules.previous_range(start, end)
	year = insights.same_period_last_year(start, end)
	from erpnext_enhancements.marketing.core.utils import get_settings

	terms = rules.branded_terms(get_settings().get("branded_terms"))
	rows = insights.compare_queries(
		data.search_rows("Query", start, end),
		data.search_rows("Query", *prev),
		data.search_rows("Query", *year),
		terms,
	)
	for row in rows:
		for key in ("ctr", "change_prev", "change_last_year"):
			row[key] = row[key] * 100 if row[key] is not None else None
		row["branded"] = 1 if row["branded"] else 0
	columns = [
		{"label": _("Query"), "fieldname": "query", "fieldtype": "Data", "width": 280},
		{"label": _("Branded"), "fieldname": "branded", "fieldtype": "Check", "width": 80},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 70},
		{"label": _("Clicks"), "fieldname": "clicks", "fieldtype": "Int", "width": 70},
		{
			"label": _("Change vs Before (%)"),
			"fieldname": "change_prev",
			"fieldtype": "Percent",
			"width": 140,
		},
		{
			"label": _("Change vs Last Year (%)"),
			"fieldname": "change_last_year",
			"fieldtype": "Percent",
			"width": 160,
		},
		{"label": _("Impressions"), "fieldname": "impressions", "fieldtype": "Int", "width": 100},
		{"label": _("CTR"), "fieldname": "ctr", "fieldtype": "Percent", "width": 70},
		{"label": _("Position"), "fieldname": "position", "fieldtype": "Float", "precision": 1, "width": 80},
		{
			"label": _("Position Before"),
			"fieldname": "position_prev",
			"fieldtype": "Float",
			"precision": 1,
			"width": 110,
		},
		{"label": _("Clicks Before"), "fieldname": "clicks_prev", "fieldtype": "Int", "width": 100},
		{"label": _("Clicks Last Year"), "fieldname": "clicks_last_year", "fieldtype": "Int", "width": 120},
	]
	return columns, rows
