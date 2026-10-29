# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Fading Pages (R4, v1.757.0): pages whose search clicks fell against the previous period or last year.

A page is listed when its clicks fell by at least the drop share (default Marketing Settings' *Fading
Pages: Drop*, 30%) from a baseline of at least the minimum (*Fading Pages: Minimum Baseline Clicks*,
20), so a page going from 3 clicks to 1 never flaps on and off the list. Biggest loss first. The rules
are ``performance.insights.fading``.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from erpnext_enhancements.marketing.performance import data, insights, rules


def _clicks(start, end):
	return {
		r["value"]: r["clicks"] or 0
		for r in data.search_rows("Page", start, end, key="page_path")
		if r["value"]
	}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not rules.views_for(frappe.get_roles())["page"]:
		frappe.throw(_("This report is for the marketing team and Sales Manager."), frappe.PermissionError)
	from erpnext_enhancements.marketing.core.utils import get_settings

	settings = get_settings()
	start, end = rules.parse_range(filters.get("from_date"), filters.get("to_date"), getdate(nowdate()))
	if filters.get("against") == "Last year":
		base = insights.same_period_last_year(start, end)
	else:
		base = rules.previous_range(start, end)
	share = (cint(filters.get("drop_percent")) or cint(settings.get("fading_drop_percent")) or 30) / 100.0
	minimum = cint(filters.get("min_baseline")) or cint(settings.get("fading_min_baseline_clicks")) or 20
	rows = insights.fading(_clicks(start, end), _clicks(*base), share, minimum)
	for row in rows:
		row["change"] = row["change"] * 100
	columns = [
		{"label": _("Page"), "fieldname": "key", "fieldtype": "Data", "width": 340},
		{"label": _("Clicks Now"), "fieldname": "clicks", "fieldtype": "Int", "width": 100},
		{"label": _("Clicks Before"), "fieldname": "baseline", "fieldtype": "Int", "width": 110},
		{"label": _("Change (%)"), "fieldname": "change", "fieldtype": "Percent", "width": 100},
		{"label": _("Clicks Lost"), "fieldname": "lost", "fieldtype": "Int", "width": 100},
	]
	return columns, rows
