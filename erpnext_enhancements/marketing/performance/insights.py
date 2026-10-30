# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The rules behind the marketing reports, graphs, alerts and digest, as pure functions (v1.757.0).

Nik's picks of 2026-10-29, built on the history ``web_sync`` keeps. Standard library only (and the
pure ``rules`` and ``publish/tracking`` helpers), so each rule is tested without a bench
(``tests/test_marketing_insights.py``):

* **R1** ``inquiry_sources`` -- Opportunities by source and month: count, won, win rate, won value,
  median days to close.
* **R2** ``compare_queries`` -- each query against the previous period and the same period last
  year; new and lost queries; branded.
* **R3** ``near_page_one`` -- position 8-20, ranked by clicks to gain at position 3.
* **R4** ``fading`` -- pages whose clicks fell by a settable share, above a minimum baseline.
* **R5** ``service_line_of`` / ``service_line_funnel`` -- the editable mapping, then traffic,
  inquiries and won work per line.
* **R6** ``missed_calls`` -- incoming calls not answered and not called back in time.
* **G2** ``funnel_bars``, **G3** ``ctr_curve`` / ``expected_ctr``.
* **F2** ``digest_markdown``; **F3** ``drop_alert``, ``index_alerts``, ``low_reviews``,
  ``throttle_due``; **F5** ``tracked_url``.
"""

import datetime
import re
import statistics
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from erpnext_enhancements.marketing.performance import rules

# ---------------------------------------------------------------- R1 inquiry sources

UNSOURCED = "Unsourced"
WON = "Closed Won"


def source_label(source, unknown_label):
	"""The source an Opportunity counts under: its Lead Source, or Unsourced for a blank or the
	recorded "Unknown (pre-Aug 2026)" gap."""
	return UNSOURCED if rules.is_unsourced(source, unknown_label) else str(source).strip()


def month_key(day):
	return f"{day:%Y-%m}" if day else ""


def inquiry_sources(opportunities, unknown_label):
	"""``[{source, month, count, won, win_rate, won_value, median_days_to_close}]``, newest month first.

	``opportunities``: ``{created, source, status, won_on, amount}``. A deal's month is the month it
	was created (the inquiry), and it counts as won there whenever it closed. Days to close are
	``won_on - created`` for the won ones; a won deal with no won date is left out of the median.
	"""
	groups = {}
	for opp in opportunities or ():
		created = rules.as_date(opp.get("created"))
		if not created:
			continue
		key = (source_label(opp.get("source"), unknown_label), month_key(created))
		g = groups.setdefault(key, {"count": 0, "won": 0, "won_value": 0.0, "days": []})
		g["count"] += 1
		if opp.get("status") == WON:
			g["won"] += 1
			g["won_value"] += float(opp.get("amount") or 0)
			won_on = rules.as_date(opp.get("won_on"))
			if won_on and won_on >= created:
				g["days"].append((won_on - created).days)
	out = []
	for (source, month), g in groups.items():
		out.append(
			{
				"source": source,
				"month": month,
				"count": g["count"],
				"won": g["won"],
				"win_rate": rules.ratio(g["won"], g["count"]),
				"won_value": g["won_value"],
				"median_days_to_close": statistics.median(g["days"]) if g["days"] else None,
			}
		)
	out.sort(key=lambda r: (r["month"], r["count"]), reverse=True)
	return out


# ---------------------------------------------------------------- R2 queries over time


def _sum_by(rows, key="value"):
	out = {}
	for row in rows or ():
		k = str(row.get(key) or "")
		if not k:
			continue
		g = out.setdefault(k, {"clicks": 0.0, "impressions": 0.0, "_w": 0.0})
		impressions = float(row.get("impressions") or 0)
		g["clicks"] += float(row.get("clicks") or 0)
		g["impressions"] += impressions
		g["_w"] += float(row.get("position") or 0) * impressions
	for g in out.values():
		g["position"] = (g.pop("_w") / g["impressions"]) if g["impressions"] else None
		g["ctr"] = rules.ratio(g["clicks"], g["impressions"])
	return out


def compare_queries(current, previous, last_year, terms):
	"""Each query's figures now, the change against the previous period and last year, and new / lost.

	Every input is ``[{value, clicks, impressions, position}]`` (rows per day are fine; they are summed
	with position weighted by impressions). **New**: clicked or seen now, absent before. **Lost**: seen
	in the previous period, absent now. A lost query is listed with its old figures.
	"""
	now, before, ly = _sum_by(current), _sum_by(previous), _sum_by(last_year)
	out = []
	for query in set(now) | set(before):
		cur = now.get(query)
		prev = before.get(query)
		year = ly.get(query)
		status = "new" if cur and not prev else "lost" if prev and not cur else ""
		base = cur or {"clicks": 0, "impressions": 0, "position": None, "ctr": None}
		out.append(
			{
				"query": query,
				"clicks": int(round(base["clicks"])),
				"impressions": int(round(base["impressions"])),
				"ctr": base["ctr"],
				"position": base["position"],
				"clicks_prev": int(round(prev["clicks"])) if prev else 0,
				"clicks_last_year": int(round(year["clicks"])) if year else None,
				"change_prev": rules.change(base["clicks"], prev["clicks"] if prev else None),
				"change_last_year": rules.change(base["clicks"], year["clicks"] if year else None),
				"position_prev": prev["position"] if prev else None,
				"status": status,
				"branded": rules.is_branded(query, terms),
			}
		)
	out.sort(key=lambda r: (r["clicks"], r["impressions"], r["clicks_prev"]), reverse=True)
	return out


# ---------------------------------------------------------------- G3 the expected click-rate curve

#: Expected CTR by position when the site's own data is too thin: a conservative mid-2020s organic
#: curve (the published studies cluster around these figures). Used bucket by bucket, and said so.
FALLBACK_CTR = {
	1: 0.28,
	2: 0.15,
	3: 0.10,
	4: 0.07,
	5: 0.055,
	6: 0.045,
	7: 0.035,
	8: 0.03,
	9: 0.025,
	10: 0.02,
}
FALLBACK_TAIL_CTR = 0.01
CURVE_MAX_POSITION = 20
#: A bucket is built from the site's own pages only when it has this many with enough impressions.
CURVE_MIN_PAGES = 3
CURVE_MIN_IMPRESSIONS = 20
#: A site bucket is floored at this share of the fallback figure for its position. Three pages with no
#: clicks at position 6 would otherwise put 0 there, and the non-increasing pass below would carry that
#: 0 down to position 20: Near Page One would rank nothing and the scatter flag nothing.
CURVE_SITE_FLOOR_SHARE = 0.25


def ctr_curve(rows, min_pages=CURVE_MIN_PAGES, min_impressions=CURVE_MIN_IMPRESSIONS):
	"""``{"points": {1..20: ctr}, "source": {position: "site" | "fallback"}, "site_buckets": n}``.

	Per rounded position, the median CTR of the site's pages (or queries) with at least
	``min_impressions``, where at least ``min_pages`` of them fall in that bucket; otherwise the
	fallback figure. The curve is then made non-increasing (a lower position never expects more
	clicks than a higher one), because a median of three pages can wobble. A site bucket is never
	below ``CURVE_SITE_FLOOR_SHARE`` of the fallback, so one thin bucket cannot zero the positions after it.
	"""
	buckets = {}
	for row in rows or ():
		position, impressions = row.get("position"), float(row.get("impressions") or 0)
		if position is None or impressions < min_impressions:
			continue
		p = max(1, min(CURVE_MAX_POSITION, int(round(float(position)))))
		buckets.setdefault(p, []).append(float(row.get("clicks") or 0) / impressions)
	points, source = {}, {}
	for p in range(1, CURVE_MAX_POSITION + 1):
		values = buckets.get(p) or []
		if len(values) >= min_pages:
			floor = FALLBACK_CTR.get(p, FALLBACK_TAIL_CTR) * CURVE_SITE_FLOOR_SHARE
			points[p], source[p] = max(statistics.median(values), floor), "site"
		else:
			points[p], source[p] = FALLBACK_CTR.get(p, FALLBACK_TAIL_CTR), "fallback"
	for p in range(2, CURVE_MAX_POSITION + 1):
		points[p] = min(points[p], points[p - 1])
	return {
		"points": points,
		"source": source,
		"site_buckets": sum(1 for s in source.values() if s == "site"),
	}


def expected_ctr(curve, position):
	"""The curve's CTR at a (fractional) position, interpolated; the last point past position 20."""
	points = curve["points"] if isinstance(curve, dict) and "points" in curve else curve
	if position is None:
		return None
	position = max(1.0, float(position))
	if position >= CURVE_MAX_POSITION:
		return points[CURVE_MAX_POSITION]
	low = int(position)
	frac = position - low
	return points[low] + (points[low + 1] - points[low]) * frac


def below_curve(rows, curve, margin=0.5):
	"""The pages whose CTR is under ``margin`` times what their position should earn: better titles."""
	out = []
	for row in rows or ():
		impressions = row.get("impressions") or 0
		if not impressions or row.get("position") is None:
			continue
		ctr = (row.get("clicks") or 0) / impressions
		want = expected_ctr(curve, row["position"])
		if want and ctr < want * margin:
			out.append({**row, "ctr": ctr, "expected_ctr": want})
	return out


# ---------------------------------------------------------------- R3 near page one

NEAR_POSITION_MIN = 8.0
NEAR_POSITION_MAX = 20.0
TARGET_POSITION = 3


def near_page_one(rows, curve, min_impressions):
	"""Queries or pages averaging position 8-20 with at least ``min_impressions``, most to gain first.

	``rows``: ``{key, clicks, impressions, position}`` summed over the range. Clicks to gain is
	``impressions x expected CTR at position 3 - clicks now``, the clicks a move onto the top of page
	one would bring at the site's own (or the fallback) click rate.
	"""
	at_target = expected_ctr(curve, TARGET_POSITION)
	out = []
	for row in rows or ():
		position, impressions = row.get("position"), row.get("impressions") or 0
		if position is None or impressions < (min_impressions or 0):
			continue
		if not NEAR_POSITION_MIN <= position <= NEAR_POSITION_MAX:
			continue
		gain = impressions * at_target - (row.get("clicks") or 0)
		out.append({**row, "clicks_to_gain": max(gain, 0.0), "expected_ctr_at_target": at_target})
	out.sort(key=lambda r: r["clicks_to_gain"], reverse=True)
	return out


# ---------------------------------------------------------------- R4 fading pages

DEFAULT_FADE_SHARE = 0.30
DEFAULT_FADE_MIN_BASELINE = 20


def fading(current, baseline, drop_share=DEFAULT_FADE_SHARE, min_baseline=DEFAULT_FADE_MIN_BASELINE):
	"""Pages whose clicks fell by at least ``drop_share`` against ``baseline``, biggest loss first.

	Both are ``{key: clicks}``. A page under ``min_baseline`` clicks in the baseline is never listed,
	so a page going from 3 clicks to 1 does not flap on and off the list. A page gone entirely counts
	as 0 now.
	"""
	out = []
	for key, base in (baseline or {}).items():
		base = float(base or 0)
		if base < (min_baseline or 0):
			continue
		now = float((current or {}).get(key) or 0)
		change = (now - base) / base
		if change <= -abs(drop_share):
			out.append({"key": key, "clicks": now, "baseline": base, "change": change, "lost": base - now})
	out.sort(key=lambda r: r["lost"], reverse=True)
	return out


# ---------------------------------------------------------------- R5 service lines

LINES = ("Design-Build", "Service & Maintenance", "Rentals", "Splash Pads", "Parts & Products")
MATCH_TYPES = ("URL Prefix", "URL Contains", "Query Keyword", "Value Stream", "Opportunity Keyword")


def _norm(text):
	return " ".join(str(text or "").lower().split())


def service_line_of(kind, value, mapping):
	"""The first line ``value`` matches, by ``kind``: "page" (a path), "query", "value_stream" or
	"opportunity" (its title). ``mapping`` is the ordered rule list from Marketing Settings."""
	text = _norm(value)
	path = rules.normalize_page(value) if kind == "page" else ""
	for rule in mapping or ():
		match, needle, line = rule.get("match_type"), _norm(rule.get("match_value")), rule.get("service_line")
		if not needle or line not in LINES:
			continue
		if kind == "page" and match == "URL Prefix" and path.startswith(rules.normalize_page(needle)):
			return line
		if kind == "page" and match == "URL Contains" and needle in path:
			return line
		if kind == "query" and match == "Query Keyword" and needle in text:
			return line
		if kind == "value_stream" and match == "Value Stream" and needle == text:
			return line
		if kind == "opportunity" and match == "Opportunity Keyword" and needle in text:
			return line
	return None


def lines_of_opportunity(opp, mapping):
	"""Every line an Opportunity belongs to: its value streams', and its title's keywords. A job that
	spans streams counts in each, as Sapphire's jobs routinely do."""
	found = []
	for stream in opp.get("value_streams") or ():
		line = service_line_of("value_stream", stream, mapping)
		if line and line not in found:
			found.append(line)
	line = service_line_of("opportunity", opp.get("title"), mapping)
	if line and line not in found:
		found.append(line)
	return found


def service_line_funnel(pages, opportunities, mapping):
	"""``[{line, sessions, search_clicks, inquiries, won, won_value}]`` for every line, in ``LINES`` order,
	plus an "Unmatched" row so nothing disappears.

	``pages``: ``{value, sessions, clicks}`` (landing pages over the range). ``opportunities``:
	``{title, value_streams, status, amount}`` created in the range.
	"""
	table = {
		line: {"line": line, "sessions": 0, "search_clicks": 0, "inquiries": 0, "won": 0, "won_value": 0.0}
		for line in (*LINES, "Unmatched")
	}
	for page in pages or ():
		line = service_line_of("page", page.get("value"), mapping) or "Unmatched"
		table[line]["sessions"] += int(page.get("sessions") or 0)
		table[line]["search_clicks"] += int(page.get("clicks") or 0)
	for opp in opportunities or ():
		for line in lines_of_opportunity(opp, mapping) or ["Unmatched"]:
			table[line]["inquiries"] += 1
			if opp.get("status") == WON:
				table[line]["won"] += 1
				table[line]["won_value"] += float(opp.get("amount") or 0)
	out = list(table.values())
	for row in out:
		row["inquiry_rate"] = rules.ratio(row["inquiries"], row["sessions"])
		row["win_rate"] = rules.ratio(row["won"], row["inquiries"])
	return out


# ---------------------------------------------------------------- R6 missed calls

#: Call Log statuses that mean nobody picked up (ERPNext's options; prod shows "No Answer").
MISSED_STATUSES = frozenset({"No Answer", "Missed", "Busy", "Failed", "Canceled", "Cancelled"})
DEFAULT_CALLBACK_HOURS = 4


def missed_calls(calls, now, callback_hours=DEFAULT_CALLBACK_HOURS):
	"""Incoming calls nobody answered and nobody called back within ``callback_hours``.

	``calls``: every call in and around the range, ``{name, at, type, status, number, first_time}``
	with ``at`` a ``datetime``. A missed incoming call is **called back** when an outgoing call to the
	same number (``rules.phone_key``) followed it inside the window. One still inside its window is
	listed as *waiting*, not *missed*. First-time callers come first, then the newest.
	"""
	window = datetime.timedelta(hours=float(callback_hours or 0))
	outgoing = {}
	for call in calls or ():
		if call.get("type") == "Outgoing" and rules.phone_key(call.get("number")):
			outgoing.setdefault(rules.phone_key(call["number"]), []).append(call["at"])
	out = []
	for call in calls or ():
		if call.get("type") != "Incoming" or call.get("status") not in MISSED_STATUSES:
			continue
		key = rules.phone_key(call.get("number"))
		called_back = (
			any(call["at"] <= t <= call["at"] + window for t in outgoing.get(key, ())) if key else False
		)
		if called_back:
			continue
		out.append({**call, "state": "waiting" if now < call["at"] + window else "missed"})
	out.sort(key=lambda c: (not c.get("first_time"), -c["at"].timestamp()))
	return out


#: The Missed Calls report's columns, in order. ``caller`` and ``number`` say who called, and are left out
#: -- columns and values both -- for anyone without call-detail rights (``rules.CALL_DETAIL_ROLES``).
MISSED_CALL_COLUMNS = ("at", "first_time", "state", "caller", "number", "status", "name")
WHO_CALLED = ("caller", "number")


def missed_calls_table(rows, call_details):
	"""``(fieldnames, rows)`` the Missed Calls report shows a viewer.

	``call_details`` is ``rules.views_for(roles)["call_details"]``. Without it the caller and the number
	are dropped from the columns **and** from every row, not just hidden: a Script Report hands its rows
	to the browser whole, and Call Log read alone (which Employee holds) is not leave to see who called.
	"""
	fields = [f for f in MISSED_CALL_COLUMNS if call_details or f not in WHO_CALLED]
	return fields, [{f: row.get(f) for f in fields} for row in rows or ()]


def weekday_hour_matrix(calls):
	"""``matrix[weekday][hour]`` incoming calls (Monday 0), for the Inquiries tab's heat map."""
	matrix = [[0] * 24 for _ in range(7)]
	for call in calls or ():
		at = call.get("at")
		if call.get("type") == "Incoming" and isinstance(at, datetime.datetime):
			matrix[at.weekday()][at.hour] += 1
	return matrix


def median_hours(pairs):
	"""The median hours from each ``(start, first_reply)`` pair to its reply; None with no replies."""
	hours = [
		(reply - start).total_seconds() / 3600
		for start, reply in pairs or ()
		if start and reply and reply >= start
	]
	return statistics.median(hours) if hours else None


# ---------------------------------------------------------------- G2 funnel bars


def funnel_bars(steps):
	"""``[(label, value)]`` -> each step with the share kept from the step before and from the first."""
	out = []
	first = previous = None
	for label, value in steps:
		out.append(
			{
				"label": label,
				"value": value,
				"kept": rules.ratio(value, previous) if (value is not None and previous) else None,
				"of_first": rules.ratio(value, first) if (value is not None and first) else None,
			}
		)
		if first is None:
			first = value
		previous = value
	return out


# ---------------------------------------------------------------- F3 alerts


def drop_alert(this_week, last_week, drop_share, min_baseline):
	"""A week-over-week fall of at least ``drop_share`` from a baseline of ``min_baseline`` or more, else None."""
	if this_week is None or not last_week or last_week < (min_baseline or 0):
		return None
	change = (float(this_week) - float(last_week)) / float(last_week)
	return (
		{"this_week": this_week, "last_week": last_week, "change": change}
		if change <= -abs(drop_share)
		else None
	)


def index_alerts(previous, current):
	"""Key pages Google indexed last time and does not now: ``{url: verdict}`` to ``{url: verdict}``.

	A page's first check alerts only when it is not indexed: there is nothing to compare it with, and
	a key page out of Google is worth one message.
	"""
	out = []
	for url, verdict in (current or {}).items():
		before = (previous or {}).get(url)
		if verdict != "PASS" and (before is None or before == "PASS"):
			out.append({"url": url, "verdict": verdict, "before": before})
	return out


def low_reviews(reviews, max_stars):
	"""New reviews at ``max_stars`` or below."""
	return [r for r in reviews or () if r.get("star_rating") and r["star_rating"] <= max_stars]


def throttle_due(last_sent, now, hours):
	"""Whether an alert last sent at ``last_sent`` may go again."""
	return last_sent is None or now - last_sent >= datetime.timedelta(hours=float(hours or 0))


#: Alert keys that name one record (a call, a review): about something that happened once, so sent once,
#: ever. The checks read a few days back so a missed night is caught up, which would otherwise send the
#: same call or review again each night inside that window.
ONCE_ONLY_PREFIXES = ("call:", "review:")


def alert_due(key, last_sent, now, hours):
	"""Whether the alert ``key`` may go now: a per-record key only if never sent, any other (a state:
	a weekly drop, a page out of Google) once its throttle window has passed."""
	if str(key).startswith(ONCE_ONLY_PREFIXES):
		return last_sent is None
	return throttle_due(last_sent, now, hours)


def new_since(reviews, since):
	"""The reviews Google says were written at or after ``since`` (naive, in site time like ``create_time``).

	Judged by the review's own ``create_time``, never by when this app first saw it: the first night
	Business Profile answers, every review the listing ever had is new *to us*, and none of them is news.
	"""
	return [r for r in reviews or () if r.get("create_time") and r["create_time"] >= since]


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+!|<>~])")


def md_escape(text, limit=None):
	"""Untrusted text made safe to drop into markdown: one line, every markdown character escaped.

	A Google review or a page path is somebody else's text. Unescaped, ``[text](https://...)`` in a review
	becomes a live link in the email, and a newline or a ``|`` breaks the list or the table around it.
	"""
	value = " ".join(str(text or "").split())
	if limit:
		value = value[:limit]
	return _MD_SPECIAL.sub(r"\\\1", value)


def prorated(amount, month, start, end):
	"""The share of a month's ``amount`` that falls inside ``start..end``, by days.

	Marketing Spend is entered per month. Counting a whole month in any range that touches it would count
	it twice in two adjacent ranges, and a one-week range would carry four weeks of spend.
	"""
	first = month.replace(day=1)
	nxt = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
	last = nxt - datetime.timedelta(days=1)
	lo, hi = max(first, start), min(last, end)
	if hi < lo:
		return 0.0
	return float(amount or 0) * ((hi - lo).days + 1) / ((last - first).days + 1)


# ---------------------------------------------------------------- F2 the Monday digest


def _fmt(value):
	if value is None:
		return "–"
	return f"{value:,.0f}" if isinstance(value, (int, float)) else str(value)


def _delta(current, previous):
	change = rules.change(current, previous)
	if change is None:
		return ""
	return f" ({'+' if change >= 0 else '−'}{abs(change) * 100:.0f}%)"


def digest_markdown(data):
	"""The Monday marketing section of the morning briefing, as markdown.

	``data``: ``{week: (start, end), metrics: [(label, this, last, last_year)], movers: [(label,
	change_text)], missed_calls: [str], new_reviews: [str], link}``. Every list may be empty; an empty
	week says so in one line rather than printing a table of dashes.
	"""
	start, end = data["week"]
	lines = [f"\n\n## 📈 Marketing, week of {start:%b} {start.day}"]
	lines.append("")
	lines.append(
		f"Last week ({start.isoformat()} to {end.isoformat()}) against the week before and the same week last year."
	)
	lines.append("")
	lines.append("| | Last week | Week before | A year ago |")
	lines.append("|---|---:|---:|---:|")
	for label, this, last, year in data.get("metrics") or ():
		lines.append(f"| {label} | {_fmt(this)}{_delta(this, last)} | {_fmt(last)} | {_fmt(year)} |")
	if data.get("movers"):
		lines.append("")
		lines.append(
			"**Biggest movers:** "
			+ "; ".join(f"{md_escape(label)} {md_escape(text)}" for label, text in data["movers"])
		)
	missed = data.get("missed_calls") or []
	lines.append("")
	lines.append(
		f"**Missed calls:** {len(missed)}"
		+ (" — " + "; ".join(md_escape(m) for m in missed[:5]) if missed else "")
	)
	reviews = data.get("new_reviews") or []
	if reviews:
		lines.append("")
		lines.append("**New reviews:** " + "; ".join(md_escape(r, 120) for r in reviews[:5]))
	if data.get("link"):
		lines.append("")
		lines.append(f"[Open Marketing Performance]({data['link']})")
	return "\n".join(lines)


def last_week(today):
	"""Monday to Sunday of the week before ``today``'s week."""
	monday = today - datetime.timedelta(days=today.weekday())
	return monday - datetime.timedelta(days=7), monday - datetime.timedelta(days=1)


def same_period_last_year(start, end):
	"""The same weekdays a year earlier (364 days back), so a Monday-to-Sunday stays one."""
	return start - datetime.timedelta(days=364), end - datetime.timedelta(days=364)


# ---------------------------------------------------------------- F5 tracked links

_UTM = re.compile(r"^utm_", re.I)


def tracked_url(destination, source, medium, campaign):
	"""The tagged address for a tracked link, or raise ``ValueError`` with the reason.

	Only Sapphire's own site (``publish/tracking.is_own``). Every ``utm_*`` already on the destination
	is replaced, and **never ``utm_id``**: the Ad Spend ROAS report reads ``utm_id`` as paid, and a
	flyer's QR code is not an ad. Values go through ``tracking.slug`` (lower case, ``a-z0-9-``) so a
	link typed two ways is still one campaign in GA4.
	"""
	from erpnext_enhancements.marketing.publish import tracking

	text = str(destination or "").strip()
	if not tracking.is_own(text):
		raise ValueError("The destination must be a page on sapphirefountains.com.")
	tags = {
		"utm_source": tracking.slug(source),
		"utm_medium": tracking.slug(medium),
		"utm_campaign": tracking.slug(campaign),
	}
	missing = [k for k, v in tags.items() if not v]
	if missing:
		raise ValueError("Fill in " + ", ".join(m.replace("utm_", "") for m in missing) + ".")
	parts = urlsplit(text)
	kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _UTM.match(k)]
	query = urlencode(kept + list(tags.items()))
	return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", query, parts.fragment))
