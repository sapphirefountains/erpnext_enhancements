# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Marketing Spend Rollup — the budget baseline, by month and channel.

`Marketing Spend` held **zero rows** as of 2026-08-04, so there was no baseline
against which any channel could be judged. This report is the view onto it, and
carries the **Import Spend** action that puts data there in the first place.

Two shapes, because two different questions get asked of the same data:

* **By month** (default) — a channel-per-column grid, one row per month. This is
  the "what did we spend, and on what, over time" view, and it is the one that
  shows a channel being quietly switched off.
* **By channel** — one row per channel with its total and monthly average. The
  "which of these is worth keeping" view.

Cost per lead and per opportunity are joined in on the channel view where
attribution allows it. That join is only as good as WP-1's capture, so the report
states the current coverage rather than presenting a confident number over a
near-empty denominator.
"""

import frappe
from frappe import _
from frappe.utils import add_months, flt, getdate, nowdate

DEFAULT_MONTHS = 12


def execute(filters=None):
	filters = frappe._dict(filters or {})
	from_date, to_date = _window(filters)

	rows = frappe.db.sql(
		"""
		SELECT month, channel, SUM(amount) AS amount
		FROM `tabMarketing Spend`
		WHERE month BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY month, channel
		ORDER BY month DESC, amount DESC
		""",
		{"from_date": from_date, "to_date": to_date},
		as_dict=True,
	)

	if filters.get("group_by") == "Channel":
		return _by_channel(rows, from_date, to_date)
	return _by_month(rows, from_date, to_date)


def _window(filters):
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or add_months(to_date, -DEFAULT_MONTHS))
	return from_date, to_date


# ------------------------------------------------------------------- by month


def _by_month(rows, from_date, to_date):
	"""A channel-per-column grid. Columns are built from the data, not a fixed
	list — a new channel appears the month it is first spent on."""
	channels = sorted({row.channel for row in rows})
	by_month = {}
	for row in rows:
		by_month.setdefault(str(row.month), {})[row.channel] = flt(row.amount)

	columns = [{"label": _("Month"), "fieldname": "month", "fieldtype": "Data", "width": 110}]
	for channel in channels:
		columns.append({
			"label": channel,
			# Frappe needs a safe fieldname; the label carries the real one.
			"fieldname": frappe.scrub(channel),
			"fieldtype": "Currency",
			"width": 130,
		})
	columns.append({"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "width": 140})

	data = []
	for month in sorted(by_month, reverse=True):
		entry = {"month": month}
		total = 0
		for channel in channels:
			amount = by_month[month].get(channel, 0)
			entry[frappe.scrub(channel)] = amount
			total += amount
		entry["total"] = total
		data.append(entry)

	return columns, data, _message(rows, from_date, to_date), _month_chart(data, channels)


def _month_chart(data, channels):
	ordered = list(reversed(data))
	return {
		"data": {
			"labels": [row["month"] for row in ordered],
			"datasets": [
				{"name": channel, "values": [flt(row.get(frappe.scrub(channel))) for row in ordered]}
				for channel in channels
			],
		},
		"type": "bar",
		"barOptions": {"stacked": True},
	}


# ----------------------------------------------------------------- by channel


def _by_channel(rows, from_date, to_date):
	totals = {}
	months = {}
	for row in rows:
		totals[row.channel] = totals.get(row.channel, 0) + flt(row.amount)
		months.setdefault(row.channel, set()).add(str(row.month))

	leads, opportunities = _attribution_counts(from_date, to_date)
	grand_total = sum(totals.values())

	columns = [
		{"label": _("Channel"), "fieldname": "channel", "fieldtype": "Data", "width": 190},
		{"label": _("Total Spend"), "fieldname": "total", "fieldtype": "Currency", "width": 140},
		{"label": _("% of Budget"), "fieldname": "share", "fieldtype": "Percent", "width": 110},
		{"label": _("Months"), "fieldname": "months", "fieldtype": "Int", "width": 80},
		{"label": _("Avg / Month"), "fieldname": "average", "fieldtype": "Currency", "width": 130},
		{"label": _("Leads"), "fieldname": "leads", "fieldtype": "Int", "width": 80},
		{"label": _("Cost / Lead"), "fieldname": "cost_per_lead", "fieldtype": "Currency", "width": 120},
		{"label": _("Opportunities"), "fieldname": "opportunities", "fieldtype": "Int", "width": 120},
		{"label": _("Cost / Opp"), "fieldname": "cost_per_opportunity", "fieldtype": "Currency", "width": 120},
	]

	data = []
	for channel in sorted(totals, key=lambda c: totals[c], reverse=True):
		total = totals[channel]
		month_count = len(months[channel])
		channel_leads = leads.get(channel, 0)
		channel_opps = opportunities.get(channel, 0)
		data.append({
			"channel": channel,
			"total": total,
			"share": (total / grand_total * 100.0) if grand_total else None,
			"months": month_count,
			"average": total / month_count if month_count else None,
			"leads": channel_leads,
			# Only where attribution actually produced a denominator. Dividing by
			# zero leads and printing the spend as "cost per lead" would be a lie
			# with a currency symbol on it.
			"cost_per_lead": (total / channel_leads) if channel_leads else None,
			"opportunities": channel_opps,
			"cost_per_opportunity": (total / channel_opps) if channel_opps else None,
		})

	return columns, data, _message(rows, from_date, to_date, attributed=bool(leads or opportunities)), None


def _attribution_counts(from_date, to_date):
	"""Leads and Opportunities per channel, matched on the captured UTM source.

	Matched case-insensitively against `custom_utm_source` — the raw value WP-1
	captures — rather than on `custom_lead_source`, because a spend line called
	"Google Ads" corresponds to a utm_source of "google", not to the human-facing
	channel taxonomy. Where nothing matches, the counts are simply absent and the
	cost columns stay blank.
	"""
	leads = opportunities = {}
	try:
		if not frappe.db.has_column("Lead", "custom_utm_source"):
			return {}, {}
		leads = _counts_by_source("Lead", from_date, to_date)
		opportunities = _counts_by_source("Opportunity", from_date, to_date)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Marketing Spend Rollup — attribution join")
		return {}, {}
	return leads, opportunities


def _counts_by_source(doctype, from_date, to_date):
	rows = frappe.db.sql(
		f"""
		SELECT LOWER(TRIM(custom_utm_source)) AS source, COUNT(*) AS n
		FROM `tab{doctype}`
		WHERE COALESCE(custom_utm_source, '') <> ''
		  AND DATE(creation) BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY source
		""",
		{"from_date": from_date, "to_date": to_date},
		as_dict=True,
	)
	# Map raw utm_source back onto the canonical channel names the spend uses, so
	# "google" lands on the "Google Ads" line rather than a line of its own.
	from erpnext_enhancements.kpi_dashboards.marketing_spend_import import canonical_channel

	out = {}
	for row in rows:
		channel = canonical_channel(row.source)
		out[channel] = out.get(channel, 0) + int(row.n)
	return out


def _message(rows, from_date, to_date, attributed=None):
	if not rows:
		return _(
			"<b>No marketing spend recorded for {0} to {1}.</b> There is no budget baseline yet — "
			"until there is, no channel can be evaluated. Use <b>Import Spend</b> above to load "
			"historical figures from a CSV."
		).format(from_date, to_date)

	notes = [_("{0} to {1}.").format(from_date, to_date)]
	if attributed is False:
		notes.append(
			_("Cost per lead and per opportunity are blank because no captured UTM source matches "
			  "these channel names yet. That is expected until the website capture script is live — "
			  "see docs/attribution-runbook.md.")
		)
	return " ".join(notes)
