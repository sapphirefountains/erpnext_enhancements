# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The reads the marketing reports and the page's tabs share (v1.757.0). Stored rows only.

Each returns plain dicts for ``insights``. Permissions are the caller's job: the reports and the
page check roles first (``rules.views_for``); the Missed Calls report reads Call Log through
``calls.call_rows(respect_permissions=True)`` instead.
"""

import datetime

import frappe

GA4 = "GA4 Daily Metric"
GSC = "Search Console Daily Metric"


def _exists(doctype):
	return bool(frappe.db.exists("DocType", doctype))


def _has_column(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def search_rows(dimension, start, end, key="dimension_value"):
	"""Search Console ``dimension`` summed over ``start..end``: ``[{value, clicks, impressions, position}]``.

	``key`` groups by the raw value (a URL or a query) or by ``page_path`` (pages, normalized). The
	position is weighted by impressions.
	"""
	if not _exists(GSC):
		return []
	return [
		dict(r)
		for r in frappe.db.sql(
			f"""select {key} as value, sum(clicks) as clicks, sum(impressions) as impressions,
				sum(position * impressions) / nullif(sum(impressions), 0) as position
			from `tab{GSC}` where dimension = %s and metric_date between %s and %s
			group by {key}""",
			(dimension, start, end),
			as_dict=True,
		)
	]


def daily(doctype, column, start, end, dimension="Total"):
	"""``{date: value}`` of one column of a site total, for a trend line."""
	if not _exists(doctype):
		return {}
	return {
		r[0]: float(r[1] or 0)
		for r in frappe.db.sql(
			f"""select metric_date, sum({column}) from `tab{doctype}`
			where dimension = %s and metric_date between %s and %s group by metric_date""",
			(dimension, start, end),
		)
	}


def channel_days(start, end):
	"""``{channel: {date: sessions}}`` from GA4's Channel rows, for the channel-mix areas."""
	out = {}
	if not _exists(GA4):
		return out
	for day, channel, sessions in frappe.db.sql(
		f"""select metric_date, dimension_value, sum(sessions) from `tab{GA4}`
		where dimension = 'Channel' and metric_date between %s and %s group by metric_date, dimension_value""",
		(start, end),
	):
		out.setdefault(channel or "(not set)", {})[day] = int(sessions or 0)
	return out


def landing_pages(start, end):
	"""GA4 landing pages by normalized path with their search clicks: ``[{value, sessions, clicks}]``."""
	pages = {}
	if _exists(GA4):
		for path, sessions in frappe.db.sql(
			f"""select page_path, sum(sessions) from `tab{GA4}`
			where dimension = 'Landing Page' and metric_date between %s and %s group by page_path""",
			(start, end),
		):
			if path:
				pages.setdefault(path, {"value": path, "sessions": 0, "clicks": 0})["sessions"] += int(
					sessions or 0
				)
	for row in search_rows("Page", start, end, key="page_path"):
		if row["value"]:
			pages.setdefault(row["value"], {"value": row["value"], "sessions": 0, "clicks": 0})["clicks"] += (
				int(row["clicks"] or 0)
			)
	return list(pages.values())


def opportunities(start, end, with_streams=False):
	"""Opportunities created ``start..end``, historical ones left out:
	``[{name, created, title, source, status, won_on, amount, value_streams}]``."""
	from erpnext_enhancements.crm_enhancements.historical import exclude_sql

	source = "o.custom_lead_source" if _has_column("Opportunity", "custom_lead_source") else "null"
	won_on = "o.custom_date_closed_won" if _has_column("Opportunity", "custom_date_closed_won") else "null"
	title = (
		"coalesce(nullif(o.custom_opportunity_name, ''), o.title, o.name)"
		if _has_column("Opportunity", "custom_opportunity_name")
		else "coalesce(o.title, o.name)"
	)
	rows = frappe.db.sql(
		f"""select o.name, o.creation as created, {title} as title, {source} as source, o.status,
			{won_on} as won_on, o.opportunity_amount as amount
		from `tabOpportunity` o
		where o.creation >= %s and o.creation < %s and {exclude_sql("o")}
		order by o.creation""",
		(start, end + datetime.timedelta(days=1)),
		as_dict=True,
	)
	out = [dict(r) for r in rows]
	if with_streams and out and _exists("Value Stream"):
		streams = {}
		for parent, a, b in frappe.db.sql(
			"""select parent, value_stream, value_streams from `tabValue Stream`
			where parenttype = 'Opportunity' and parent in %s""",
			([r["name"] for r in out],),
		):
			# The child row has two Link columns, value_stream (what live rows use) and value_streams.
			streams.setdefault(parent, set()).update(v for v in (a, b) if v)
		for row in out:
			row["value_streams"] = sorted(streams.get(row["name"], ()))
	return out


def first_replies(names):
	"""``{opportunity: first sent Communication time}``: how fast somebody wrote back."""
	if not names or not _exists("Communication"):
		return {}
	return {
		r[0]: r[1]
		for r in frappe.db.sql(
			"""select reference_name, min(communication_date) from `tabCommunication`
			where reference_doctype = 'Opportunity' and reference_name in %s
			  and sent_or_received = 'Sent' and communication_type = 'Communication'
			group by reference_name""",
			(list(names),),
		)
	}


def quotations(start, end):
	"""Quotations made ``start..end`` and not cancelled: the funnel's quote step."""
	if not _exists("Quotation"):
		return None
	return frappe.db.count(
		"Quotation",
		[
			["creation", ">=", start],
			["creation", "<", end + datetime.timedelta(days=1)],
			["docstatus", "<", 2],
		],
	)
