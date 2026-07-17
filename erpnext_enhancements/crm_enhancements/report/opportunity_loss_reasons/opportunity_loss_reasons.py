# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Opportunity Loss Reasons — distribution of ERPNext's native ``lost_reasons``
across Lost opportunities, powering the "Opportunity Loss Reasons" donut on the
Sales dashboard.

Why a Script Report instead of a Group By dashboard chart: the reasons live in
the ``lost_reasons`` Table MultiSelect (child rows of ``Opportunity Lost Reason
Detail``), and a Group By chart can only group on a column of the base doctype —
it cannot traverse a child table. This report joins the child rows back to their
Lost parent and counts *distinct opportunities* per reason (an opportunity may
carry several reasons; each is counted once toward each of its reasons).

Superseded the app's own ``custom_lost_reason`` Select, removed in v1.159.0
(``patches.remove_opportunity_lost_reason``) as a duplicate of this native
taxonomy.
"""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"label": _("Lost Reason"), "fieldname": "lost_reason", "fieldtype": "Link", "options": "Opportunity Lost Reason", "width": 240},
		{"label": _("Opportunities"), "fieldname": "opportunities", "fieldtype": "Int", "width": 130},
		{"label": _("% of Lost"), "fieldname": "pct", "fieldtype": "Percent", "width": 110},
	]


def get_data(filters):
	conditions = ["o.status = 'Lost'", "d.parenttype = 'Opportunity'"]
	values = {}
	if filters.get("from_date"):
		conditions.append("o.modified >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("o.modified <= %(to_date)s")
		values["to_date"] = filters.to_date

	rows = frappe.db.sql(
		"""
		select d.lost_reason as lost_reason,
		       count(distinct d.parent) as opportunities
		from `tabOpportunity Lost Reason Detail` d
		join `tabOpportunity` o on o.name = d.parent
		where {conditions}
		group by d.lost_reason
		order by opportunities desc, d.lost_reason asc
		""".format(conditions=" and ".join(conditions)),
		values,
		as_dict=True,
	)

	# Denominator: distinct Lost opportunities in scope that carry at least one
	# reason (so percentages sum to >=100% when opps have multiple reasons, which
	# is the honest reading of a multi-select).
	total = sum(r.opportunities for r in rows) or 0
	scoped_opps = frappe.db.sql(
		"""
		select count(distinct o.name)
		from `tabOpportunity` o
		join `tabOpportunity Lost Reason Detail` d
		     on d.parent = o.name and d.parenttype = 'Opportunity'
		where {conditions}
		""".format(conditions=" and ".join(conditions)),
		values,
	)[0][0] or 0

	for r in rows:
		r["pct"] = (r["opportunities"] / scoped_opps * 100.0) if scoped_opps else 0.0
	return rows


def get_chart(data):
	if not data:
		return None
	return {
		"data": {
			"labels": [r["lost_reason"] for r in data],
			"datasets": [{"name": _("Opportunities"), "values": [r["opportunities"] for r in data]}],
		},
		"type": "donut",
	}
