# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Inquiry Sources (R1, v1.757.0): Opportunities by Lead Source and month.

Count, won, win rate, won value and the median days from inquiry to won
(``custom_date_closed_won - creation``). Historical deals are left out, as the funnel always has
(``crm_enhancements.historical``), and "Unknown (pre-Aug 2026)" counts as Unsourced: it is a recorded
gap, not a channel. The source is ``custom_lead_source``, never ``source`` (the frozen pre-2023
column). Won value is financial, so it is shown only to System Manager, Sales Manager and Marketing
Manager. The rules are ``performance.insights.inquiry_sources``; this file only fetches.

Its ``ref_doctype`` is GA4 Daily Metric, not Opportunity: Frappe checks report permission on the
``ref_doctype`` before ``execute`` runs, and giving the marketing roles report rights on Opportunity would open
every Opportunity's amount in Report View. The marketing roles read and report on their own daily figures; the checks inside
``execute`` decide the rest.
"""

import frappe
from frappe import _
from frappe.utils import add_months, getdate, nowdate

from erpnext_enhancements.crm_enhancements.attribution import UNKNOWN_LEAD_SOURCE
from erpnext_enhancements.marketing.performance import data, insights, rules


def execute(filters=None):
	filters = frappe._dict(filters or {})
	views = rules.views_for(frappe.get_roles())
	if not views["page"]:
		frappe.throw(_("This report is for the marketing team and Sales Manager."), frappe.PermissionError)
	start, end = rules.parse_range(
		filters.get("from_date") or add_months(getdate(nowdate()), -12),
		filters.get("to_date"),
		getdate(nowdate()),
	)
	rows = insights.inquiry_sources(data.opportunities(start, end), UNKNOWN_LEAD_SOURCE)
	columns = [
		{"label": _("Source"), "fieldname": "source", "fieldtype": "Data", "width": 200},
		{"label": _("Month"), "fieldname": "month", "fieldtype": "Data", "width": 90},
		{"label": _("Inquiries"), "fieldname": "count", "fieldtype": "Int", "width": 90},
		{"label": _("Won"), "fieldname": "won", "fieldtype": "Int", "width": 70},
		{"label": _("Win Rate"), "fieldname": "win_rate", "fieldtype": "Percent", "width": 90},
	]
	if views["financial"]:
		columns.append(
			{"label": _("Won Value"), "fieldname": "won_value", "fieldtype": "Currency", "width": 120}
		)
	columns.append(
		{
			"label": _("Median Days to Close"),
			"fieldname": "median_days_to_close",
			"fieldtype": "Float",
			"width": 150,
		}
	)
	for row in rows:
		row["win_rate"] = row["win_rate"] * 100 if row["win_rate"] is not None else None
		if not views["financial"]:
			row.pop("won_value", None)
	return columns, rows
