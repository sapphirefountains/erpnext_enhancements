# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Monday marketing digest: a section of the morning briefing (F2, v1.757.0).

Mondays only, and only for a briefing recipient who may open Marketing Performance (the page roles).
Last week against the week before and the same week last year, for sessions, search clicks,
inquiries and calls; the biggest movers among landing pages; the missed calls; the new Google
reviews. Deterministic markdown (``insights.digest_markdown``) appended after the narrative, like the
briefing's My Work list, so it is the same whether Gemini wrote the rest or not. The briefing email
renders it through ``email_style`` and the shared shell, as every briefing does
(``templates/emails/_shell.html``).
"""

import datetime

import frappe
from frappe.utils import cint, get_url, getdate, now_datetime, nowdate

from erpnext_enhancements.marketing.performance import insights, rules

GA4 = "GA4 Daily Metric"
GSC = "Search Console Daily Metric"
MOVERS = 3
#: Search Console's newest final day is two or three days back (``alerts.GSC_LAG_DAYS``).
GSC_LAG_DAYS = 3
#: The digest is the same for everyone who gets it, so a briefing batch builds it once a day.
_BUILT = {}


def _sum(doctype, column, start, end, dimension="Total"):
	"""The sum over ``start..end``, or None unless every day of it is stored: a week missing its last
	days would read as a fall against a whole one."""
	if not frappe.db.exists("DocType", doctype):
		return None
	value, days = frappe.db.sql(
		f"""select sum({column}), count(distinct metric_date) from `tab{doctype}`
		where dimension = %s and metric_date between %s and %s""",
		(dimension, start, end),
	)[0]
	return int(value or 0) if days and days >= (end - start).days + 1 else None


def _count(doctype, start, end, filters=None):
	if not frappe.db.exists("DocType", doctype):
		return None
	conditions = [
		["creation", ">=", start],
		["creation", "<", end + datetime.timedelta(days=1)],
		*(filters or []),
	]
	return frappe.db.count(doctype, conditions)


def _pages(start, end):
	if not frappe.db.exists("DocType", GA4):
		return {}
	return {
		r[0]: int(r[1] or 0)
		for r in frappe.db.sql(
			f"""select page_path, sum(sessions) from `tab{GA4}`
			where dimension = 'Landing Page' and metric_date between %s and %s group by page_path""",
			(start, end),
		)
		if r[0]
	}


def gather(today):
	start, end = insights.last_week(today)
	before = (start - datetime.timedelta(days=7), end - datetime.timedelta(days=7))
	year = insights.same_period_last_year(start, end)

	def three(fn):
		return fn(start, end), fn(*before), fn(*year)

	lag = datetime.timedelta(days=GSC_LAG_DAYS)
	from erpnext_enhancements.crm_enhancements.historical import exclude_filters

	metrics = [
		("Sessions", *three(lambda a, b: _sum(GA4, "sessions", a, b))),
		# Search Console's weekend is not in by Monday: the same seven days, three days earlier, for all
		# three weeks, so the comparison is a whole week against a whole week.
		("Search clicks (7 days to 3 days back)", *three(lambda a, b: _sum(GSC, "clicks", a - lag, b - lag))),
		# Historical deals left out, as the funnel and Inquiry Sources leave them out.
		("Inquiries (Opportunities)", *three(lambda a, b: _count("Opportunity", a, b, exclude_filters()))),
		("Inbound calls", *three(lambda a, b: _count("Call Log", a, b, [["type", "=", "Incoming"]]))),
	]
	now_pages, prev_pages = _pages(start, end), _pages(*before)
	moves = sorted(
		(
			(page, now_pages.get(page, 0) - prev_pages.get(page, 0))
			for page in set(now_pages) | set(prev_pages)
		),
		key=lambda pair: abs(pair[1]),
		reverse=True,
	)
	movers = [
		(page, f"{'+' if delta >= 0 else '−'}{abs(delta)} sessions")
		for page, delta in moves[:MOVERS]
		if delta
	]

	missed = []
	if frappe.db.exists("DocType", "Call Log"):
		from erpnext_enhancements.marketing.core.utils import get_settings
		from erpnext_enhancements.marketing.performance.calls import call_rows

		hours = cint(get_settings().get("missed_call_callback_hours")) or insights.DEFAULT_CALLBACK_HOURS
		missed = [
			f"{str(c['at'])[:16]}" + (" (first-time)" if c.get("first_time") else "")
			for c in insights.missed_calls(call_rows(start, end), now_datetime(), hours)
			if c["state"] == "missed"
		]
	reviews = []
	if frappe.db.exists("DocType", "Business Profile Review"):
		# Written last week by Google's own create time, not first seen by us: the first snapshot sees a
		# listing's whole history at once, and none of that is last week's news.
		reviews = [
			f"{r.star_rating}★ {(r.comment or '')[:80]}".strip()
			for r in frappe.get_all(
				"Business Profile Review",
				filters=[
					["create_time", ">=", datetime.datetime.combine(start, datetime.time.min)],
					[
						"create_time",
						"<",
						datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min),
					],
				],
				fields=["star_rating", "comment"],
				order_by="create_time desc",
			)
		]
	return {
		"week": (start, end),
		"metrics": metrics,
		"movers": movers,
		"missed_calls": missed,
		"new_reviews": reviews,
		"link": get_url("/desk/marketing-performance"),
	}


def section_for(user, today=None):
	"""The digest for ``user`` today, or "" (not Monday, or not someone the page is for)."""
	today = getdate(today or nowdate())
	if today.weekday() != 0:
		return ""
	if not rules.views_for(frappe.get_roles(user))["page"]:
		return ""
	try:
		if today not in _BUILT:
			_BUILT.clear()
			_BUILT[today] = insights.digest_markdown(gather(today))
		return _BUILT[today]
	except Exception as exc:
		# The briefing is a daily habit; a digest failure must never cost the rest of it.
		frappe.log_error(title="Marketing digest", message=f"{type(exc).__name__}: {exc}")
		return ""
