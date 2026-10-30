# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Near Page One (R3, v1.757.0): queries or pages averaging position 8-20, most clicks to gain first.

Clicks to gain is ``impressions x expected CTR at position 3 - clicks now``. The expected CTR comes from
the site's own pages (``insights.ctr_curve``: the median CTR at each position where at least three
pages have enough impressions), position by position, and from a conservative published curve where
the site's data is too thin; the message under the report says which. The minimum impressions is a
filter, defaulting to Marketing Settings' *Near Page One: Minimum Impressions*.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from erpnext_enhancements.marketing.performance import data, insights, rules


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not rules.views_for(frappe.get_roles())["page"]:
		frappe.throw(_("This report is for the marketing team and Sales Manager."), frappe.PermissionError)
	from erpnext_enhancements.marketing.core.utils import get_settings

	start, end = rules.parse_range(filters.get("from_date"), filters.get("to_date"), getdate(nowdate()))
	minimum = (
		cint(filters.get("min_impressions"))
		or cint(get_settings().get("near_page_one_min_impressions"))
		or 50
	)
	pages = data.search_rows("Page", start, end, key="page_path")
	curve = insights.ctr_curve(pages)
	kind = "Page" if filters.get("kind") == "Page" else "Query"
	source = pages if kind == "Page" else data.search_rows("Query", start, end)
	rows = insights.near_page_one(source, curve, minimum)
	for row in rows:
		row["key"] = row.pop("value", "")
		impressions = row.get("impressions") or 0
		row["ctr"] = (row.get("clicks") or 0) / impressions * 100 if impressions else None
		row["expected_ctr_at_target"] = row["expected_ctr_at_target"] * 100
	columns = [
		{"label": _(kind), "fieldname": "key", "fieldtype": "Data", "width": 320},
		{"label": _("Position"), "fieldname": "position", "fieldtype": "Float", "precision": 1, "width": 80},
		{"label": _("Impressions"), "fieldname": "impressions", "fieldtype": "Int", "width": 100},
		{"label": _("Clicks"), "fieldname": "clicks", "fieldtype": "Int", "width": 70},
		{"label": _("CTR"), "fieldname": "ctr", "fieldtype": "Percent", "width": 70},
		{
			"label": _("Expected CTR at 3"),
			"fieldname": "expected_ctr_at_target",
			"fieldtype": "Percent",
			"width": 130,
		},
		{
			"label": _("Clicks to Gain"),
			"fieldname": "clicks_to_gain",
			"fieldtype": "Float",
			"precision": 0,
			"width": 110,
		},
	]
	note = _(
		"Expected CTR from the site's own pages at {0} of 20 positions, the published fallback curve elsewhere."
	).format(curve["site_buckets"])
	return columns, rows, note
