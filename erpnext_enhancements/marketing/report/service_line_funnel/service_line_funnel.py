# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Service Line Funnel (R5, v1.757.0): traffic, inquiries and won work per service line.

The lines (Design-Build, Service & Maintenance, Rentals, Splash Pads, Parts & Products) come from the
ordered rules on Marketing Settings (*Service Line Rules*), first match wins:

* **Traffic**: each GA4 landing page's sessions and its Search Console clicks, by *URL Prefix* or *URL
  Contains*.
* **Inquiries and won work**: each Opportunity's **value streams** (``custom_value_stream``, the Table
  MultiSelect of Value Stream that Sapphire already fills in: Build, Design, Service, Events, Products)
  by *Value Stream* rules, and its title by *Opportunity Keyword*. A job that spans streams counts in
  each of its lines, as Sapphire's jobs routinely do; that field is what already carries the line, so
  no new one was invented.

Anything no rule matches is its own row, Unmatched, so nothing disappears. Won value is financial and
shown only to System Manager, Sales Manager and Marketing Manager.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from erpnext_enhancements.marketing.performance import data, insights, rules


def mapping():
	from erpnext_enhancements.marketing.core.utils import get_settings

	return [
		{"match_type": r.match_type, "match_value": r.match_value, "service_line": r.service_line}
		for r in (get_settings().get("service_line_rules") or [])
	]


def execute(filters=None):
	filters = frappe._dict(filters or {})
	views = rules.views_for(frappe.get_roles())
	if not views["page"]:
		frappe.throw(_("This report is for the marketing team and Sales Manager."), frappe.PermissionError)
	start, end = rules.parse_range(filters.get("from_date"), filters.get("to_date"), getdate(nowdate()))
	rows = insights.service_line_funnel(
		data.landing_pages(start, end), data.opportunities(start, end, with_streams=True), mapping()
	)
	for row in rows:
		for key in ("inquiry_rate", "win_rate"):
			row[key] = row[key] * 100 if row[key] is not None else None
		if not views["financial"]:
			row.pop("won_value", None)
	columns = [
		{"label": _("Service Line"), "fieldname": "line", "fieldtype": "Data", "width": 180},
		{"label": _("Sessions"), "fieldname": "sessions", "fieldtype": "Int", "width": 90},
		{"label": _("Search Clicks"), "fieldname": "search_clicks", "fieldtype": "Int", "width": 110},
		{"label": _("Inquiries"), "fieldname": "inquiries", "fieldtype": "Int", "width": 90},
		{
			"label": _("Inquiries per Session"),
			"fieldname": "inquiry_rate",
			"fieldtype": "Percent",
			"width": 150,
		},
		{"label": _("Won"), "fieldname": "won", "fieldtype": "Int", "width": 70},
		{"label": _("Win Rate"), "fieldname": "win_rate", "fieldtype": "Percent", "width": 90},
	]
	if views["financial"]:
		columns.append(
			{"label": _("Won Value"), "fieldname": "won_value", "fieldtype": "Currency", "width": 120}
		)
	return columns, rows
