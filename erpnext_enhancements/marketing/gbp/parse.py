# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Pure parsers and request builders for Google Business Profile. Standard library only.

Tested against recorded-shape fixtures in ``tests/test_marketing_performance.py``: API access is
still pending Google's approval, so replace them with real, redacted captures once a profile is
connected.
"""

import datetime
from urllib.parse import urlencode, urlsplit

from erpnext_enhancements.marketing.gbp import constants as G


def allowed(method, url):
	"""True if ``method url`` is on the Business Profile read-only allowlist."""
	parts = urlsplit(url)
	if parts.scheme != "https":
		return False
	return any(
		m == str(method).upper() and host == parts.hostname and pattern.match(parts.path)
		for m, host, pattern in G.ALLOWLIST
	)


def resource_id(name):
	"""``"123"`` from ``"locations/123"`` or ``"accounts/9/locations/123"``; the bare ID as given."""
	return str(name or "").rstrip("/").rsplit("/", 1)[-1]


def parse_accounts(body):
	"""``[{account_id, name}]`` from ``accounts.list``."""
	return [
		{"account_id": resource_id(a.get("name")), "name": a.get("accountName") or a.get("name")}
		for a in (body or {}).get("accounts") or []
		if a.get("name")
	]


def parse_locations(body):
	"""``[{location_id, title}]`` from ``accounts.locations.list`` (``readMask=name,title``)."""
	return [
		{"location_id": resource_id(loc.get("name")), "title": loc.get("title") or ""}
		for loc in (body or {}).get("locations") or []
		if loc.get("name")
	]


def metrics_query(start, end):
	"""The query string for ``fetchMultiDailyMetricsTimeSeries``.

	``dailyMetrics`` repeats, and the range takes snake_case subfields under a camelCase parent
	(``dailyRange.start_date.year``), exactly as Google's own example writes it.
	"""
	params = [("dailyMetrics", metric) for metric in G.DAILY_METRICS]
	for label, day in (("start_date", start), ("end_date", end)):
		params += [
			(f"dailyRange.{label}.year", day.year),
			(f"dailyRange.{label}.month", day.month),
			(f"dailyRange.{label}.day", day.day),
		]
	return urlencode(params)


def _day(date):
	try:
		return datetime.date(int(date["year"]), int(date["month"]), int(date["day"]))
	except (KeyError, TypeError, ValueError):
		return None  # a month-level value has no day; this connector reads days only


def parse_daily_metrics(body):
	"""``{date: {column: value}}`` from ``fetchMultiDailyMetricsTimeSeries``.

	Google sends each value as an int64 **string** and **leaves ``value`` out when it is zero**, so
	a dated entry with no value is 0, not missing. A metric Google did not answer at all leaves its
	column absent (None on the row), never 0. Desktop and mobile impressions are summed into one
	column each for Maps and Search.
	"""
	out = {}
	for multi in (body or {}).get("multiDailyMetricTimeSeries") or []:
		for series in multi.get("dailyMetricTimeSeries") or []:
			column = G.DAILY_METRICS.get(series.get("dailyMetric"))
			if not column:
				continue
			for point in ((series.get("timeSeries") or {}).get("datedValues")) or []:
				day = _day(point.get("date") or {})
				if day is None:
					continue
				try:
					value = int(point.get("value") or 0)
				except (TypeError, ValueError):
					value = 0
				row = out.setdefault(day, {})
				row[column] = row.get(column, 0) + value
	return out


STAR_VALUES = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}


def summarize_reviews(pages):
	"""Count, average rating and unreplied reviews from every page of ``reviews.list``.

	``totalReviewCount`` and ``averageRating`` come from Google (the first page that has them);
	**unreplied** is counted from the reviews themselves, a review with no ``reviewReply``. The
	average is recomputed from the stars only when Google sent none.
	"""
	total = average = None
	stars = []
	unreplied = 0
	seen = 0
	for body in pages or ():
		body = body or {}
		if total is None and body.get("totalReviewCount") is not None:
			total = int(body["totalReviewCount"])
		if average is None and body.get("averageRating") is not None:
			average = float(body["averageRating"])
		for review in body.get("reviews") or []:
			seen += 1
			if review.get("starRating") in STAR_VALUES:
				stars.append(STAR_VALUES[review["starRating"]])
			if not (review.get("reviewReply") or {}).get("comment"):
				unreplied += 1
	if total is None:
		total = seen
	if average is None and stars:
		average = sum(stars) / len(stars)
	return {"review_count": total, "average_rating": average, "unreplied_reviews": unreplied}
