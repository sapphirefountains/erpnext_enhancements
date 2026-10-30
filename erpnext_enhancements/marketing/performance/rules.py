# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What the Marketing Performance page shows, as pure functions (v1.756.0).

Standard library only, so every rule here is tested without a bench
(``tests/test_marketing_performance.py``). ``api.py`` fetches rows and hands them here;
``web_sync.py`` uses the window and record-name helpers.

## Who sees what (Nik, 2026-10-29)

* **The page, traffic, search and call counts:** the three marketing roles (System Manager,
  Marketing Manager, Marketing Team) plus Sales Manager.
* **Money** -- ad spend, offline spend, cost per lead, contract value, invoiced and ROAS -- is
  financial: System Manager, Sales Manager and Marketing Manager, never Marketing Team. The same
  line the Social Post Performance report draws.
* **Who called** -- the caller and the number -- only System Manager and Sales Manager. Everyone
  else on the page sees counts. Call Log rows carry the caller's number in ``from`` and, for an
  unknown caller, in the Customer's name too.

## Landing pages are joined on a normalized path

GA4 reports a landing page as a path (``/fountains/``), Search Console as a full URL
(``https://www.sapphirefountains.com/fountains/?utm_source=x``). ``normalize_page`` turns both
into the same key: no scheme, no host, no query string or fragment, no trailing slash (the root
stays ``/``), repeated slashes collapsed, lower case. Two raw URLs that normalize alike are one
page, and their figures are summed.

## Average position is weighted by impressions

Search Console's ``position`` is an average per row. Averaging the averages across days or
across URLs that share a path would weigh a day with 3 impressions like a day with 3,000, so the
stored rows carry ``position`` and the sums here carry ``position * impressions``.
"""

import datetime
import hashlib
import re
from urllib.parse import urlsplit

# ---------------------------------------------------------------- roles

SYSTEM_MANAGER = "System Manager"
SALES_MANAGER = "Sales Manager"
MARKETING_MANAGER = "Marketing Manager"
MARKETING_TEAM = "Marketing Team"

#: Who may open the page: traffic, search and call counts.
PAGE_ROLES = frozenset({SYSTEM_MANAGER, SALES_MANAGER, MARKETING_MANAGER, MARKETING_TEAM})
#: Spend, revenue and ROAS. Never Marketing Team (the Social Post Performance precedent).
FINANCIAL_ROLES = frozenset({SYSTEM_MANAGER, SALES_MANAGER, MARKETING_MANAGER})
#: Who called, and from which number. Everyone else on the page sees counts.
CALL_DETAIL_ROLES = frozenset({SYSTEM_MANAGER, SALES_MANAGER})
#: The backfill button.
OPERATOR_ROLES = frozenset({SYSTEM_MANAGER})


def views_for(roles):
	"""What a person holding ``roles`` may see on the page."""
	roles = set(roles or ())
	return {
		"page": bool(roles & PAGE_ROLES),
		"financial": bool(roles & FINANCIAL_ROLES),
		"call_details": bool(roles & CALL_DETAIL_ROLES),
		"operator": bool(roles & OPERATOR_ROLES),
	}


# ---------------------------------------------------------------- dates

DEFAULT_RANGE_DAYS = 28
#: The page reads stored history, which is kept forever (Nik, 2026-10-29: "let's make sure we are
#: keeping a history"), so a range may reach back years. Ten is far more than any source holds today.
MAX_RANGE_DAYS = 3660


def as_date(value):
	"""A ``date`` from a date, a datetime or an ISO string; None when it is not one."""
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	try:
		return datetime.date.fromisoformat(str(value)[:10])
	except ValueError:
		return None


def parse_range(from_date, to_date, today, default_days=DEFAULT_RANGE_DAYS, max_days=MAX_RANGE_DAYS):
	"""``(start, end)`` for the page, inclusive.

	The default is the last ``default_days`` days ending yesterday (today is still moving). A
	missing or unreadable end is yesterday; a missing start is ``default_days`` before the end;
	a start after the end is swapped; an end after today is today; and a range longer than
	``max_days`` keeps its end and loses the oldest days.
	"""
	end = as_date(to_date) or today - datetime.timedelta(days=1)
	end = min(end, today)
	start = as_date(from_date) or end - datetime.timedelta(days=default_days - 1)
	if start > end:
		start, end = end, start
	if (end - start).days + 1 > max_days:
		start = end - datetime.timedelta(days=max_days - 1)
	return start, end


def previous_range(start, end):
	"""The range of the same length that ends the day before ``start``."""
	length = (end - start).days + 1
	prev_end = start - datetime.timedelta(days=1)
	return prev_end - datetime.timedelta(days=length - 1), prev_end


def days_between(start, end):
	"""Every day from ``start`` to ``end`` inclusive."""
	out = []
	day = start
	while day <= end:
		out.append(day)
		day += datetime.timedelta(days=1)
	return out


def chunks(start, end, size):
	"""``[(from, to), ...]`` covering ``start``..``end`` in pieces of at most ``size`` days, oldest first."""
	out = []
	day = start
	while day <= end:
		last = min(day + datetime.timedelta(days=size - 1), end)
		out.append((day, last))
		day = last + datetime.timedelta(days=1)
	return out


def history_window(earliest, today, lag_days=1):
	"""``(start, end)`` of a source's full backfill: from the oldest day its API serves to the newest
	settled day, or None when there is nothing (an ``earliest`` after the end)."""
	end = today - datetime.timedelta(days=lag_days)
	if earliest is None or earliest > end:
		return None
	return earliest, end


#: A failed backfill waits this long before the nightly run resumes it, doubled with each retry ...
BACKFILL_BACKOFF_HOURS = 6
#: ... and never longer than a week, so one that keeps failing is still tried weekly, not abandoned.
BACKFILL_BACKOFF_CAP_HOURS = 7 * 24


def backfill_start(window_from, progress_through):
	"""Where a backfill (re)starts: the day after its cursor, or its first day when it has none."""
	if progress_through:
		return progress_through + datetime.timedelta(days=1)
	return window_from


def backfill_backoff(retries):
	"""How long a failed backfill waits before its ``retries + 1``-th automatic resume."""
	hours = BACKFILL_BACKOFF_HOURS * 2 ** max(int(retries or 0), 0)
	return datetime.timedelta(hours=min(hours, BACKFILL_BACKOFF_CAP_HOURS))


def backfill_plan(logs, now, manual=False):
	"""What to do about one source's (or one account's, one listing's) backfill: ``(action, log)``.

	``logs`` are its Backfill logs, newest first, as dicts with ``name``, ``status``, ``retry_count``
	and ``finished_at`` / ``started_at`` / ``creation``. ``action`` is one of:

	* ``"open"``: one is Queued or Running; leave it (``log`` is its name).
	* ``"resume"``: the newest **failed**, from its cursor -- by hand at once, automatically once
	  ``backfill_backoff(retry_count)`` has passed since it stopped. Never from the beginning: the
	  windows before its cursor are stored.
	* ``"create"``: never backfilled; or asked for by hand after one completed.
	* ``"skip"``: completed (only *Completed* counts as done), or failed and still backing off.
	"""
	for log in logs:
		if log.get("status") in ("Queued", "Running"):
			return "open", log.get("name")
	if not logs:
		return "create", None
	newest = logs[0]
	if newest.get("status") == "Completed":
		return ("create", None) if manual else ("skip", None)
	if manual:
		return "resume", newest.get("name")
	stopped = newest.get("finished_at") or newest.get("started_at") or newest.get("creation")
	if stopped is None or now >= stopped + backfill_backoff(newest.get("retry_count")):
		return "resume", newest.get("name")
	return "skip", None


def months_back(today, months):
	"""The same day ``months`` calendar months before ``today``, clamped to the month's last day."""
	month = today.month - 1 - months
	year = today.year + month // 12
	month = month % 12 + 1
	for day in (today.day, 30, 29, 28):
		try:
			return datetime.date(year, month, day)
		except ValueError:
			continue
	return datetime.date(year, month, 28)


def nightly_window(last_stored, today, restate_days, initial_days, max_catchup_days, lag_days=1):
	"""``(start, end)`` the nightly pull restates, inclusive, or None when there is nothing to pull.

	* ``end`` is ``today - lag_days``: GA4 and Search Console both finish a day after it closes.
	* Nothing stored yet: the last ``initial_days`` (the backfill does the rest).
	* Otherwise the last ``restate_days`` up to the newest stored day, plus every day since. Search
	  Console publishes final figures two or three days late and GA4 revises a day for a while
	  after it, so the window always reaches back over the days that may still change; the
	  per-day replace in ``web_sync`` makes pulling them again safe.
	* A pull that stopped for weeks catches up by itself, but never more than ``max_catchup_days``
	  in one night: a longer gap is the backfill's job.
	"""
	end = today - datetime.timedelta(days=lag_days)
	if last_stored is None:
		start = end - datetime.timedelta(days=max(int(initial_days), 1) - 1)
	else:
		restate = max(int(restate_days or 0), 1)
		start = min(
			last_stored - datetime.timedelta(days=restate - 1), last_stored + datetime.timedelta(days=1)
		)
	start = max(start, end - datetime.timedelta(days=max(int(max_catchup_days), 1) - 1))
	if start > end:
		return None
	return start, end


# ---------------------------------------------------------------- record names


def value_key(value):
	"""A short stable key for a dimension value (a path, a query, a channel). 16 hex characters."""
	return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:16]


def record_name(prefix, metric_date, code, value=None, *more):
	"""The deterministic name of one stored day: ``GA4-2026-10-01-L-<key>``, ``GSC-2026-10-01-T``.

	A Search Console query can be 300 characters and a path longer, so the value is hashed rather
	than embedded. The same day, dimension and value always give the same name, which is what makes
	restating a day an overwrite instead of a second copy.

	The finer grains (a page and a query, a landing page and a source) pass every part: the key is
	the hash of all of them, so two rows differing in any part never share a name.
	"""
	day = metric_date.isoformat() if isinstance(metric_date, datetime.date) else str(metric_date)[:10]
	if value is None and not more:
		return f"{prefix}-{day}-{code}"
	key = value if not more else "\x1f".join(str(v or "") for v in (value, *more))
	return f"{prefix}-{day}-{code}-{value_key(key)}"


# ---------------------------------------------------------------- URLs

NOT_SET = "(not set)"
_SLASHES = re.compile(r"/{2,}")
_HOSTLIKE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(:\d+)?(/|$)", re.I)


def normalize_page(value):
	"""The join key for a landing page: a lower-case path with no host, query or trailing slash.

	>>> normalize_page("https://www.sapphirefountains.com/Fountains/?utm_source=x#top")
	'/fountains'
	>>> normalize_page("/fountains")
	'/fountains'
	>>> normalize_page("https://sapphirefountains.com")
	'/'

	GA4's ``(not set)`` (a session with no landing page) stays itself, and a blank is ``""``.
	"""
	text = str(value or "").strip()
	if not text:
		return ""
	if text.lower() == NOT_SET:
		return NOT_SET
	if text.startswith("//"):
		text = "http:" + text
	elif "://" not in text and _HOSTLIKE.match(text):
		text = "http://" + text
	parts = urlsplit(text)
	path = parts.path if (parts.scheme or parts.netloc) else text.split("?", 1)[0].split("#", 1)[0]
	path = _SLASHES.sub("/", path or "/")
	if not path.startswith("/"):
		path = "/" + path
	if len(path) > 1:
		path = path.rstrip("/") or "/"
	return path.lower()


# ---------------------------------------------------------------- rows from the APIs


def _num(value):
	if value is None or value == "":
		return 0.0
	try:
		return float(value)
	except (TypeError, ValueError):
		return 0.0


def parse_ga4_rows(rows):
	"""GA4 ``runReport`` rows, already flattened to ``(dimension values, metric values)``.

	Dimensions are ``date`` (``YYYYMMDD``) then any others; metrics are sessions, totalUsers,
	engagedSessions and keyEvents in that order. Returns
	``[{metric_date, value, extra, sessions, total_users, engaged_sessions, key_events}]``: ``value``
	is the second dimension, ``extra`` the rest in order (a landing page's source / medium; a city's
	region and country). A row whose date does not parse is dropped.
	"""
	out = []
	for dims, metrics in rows or ():
		dims = list(dims or ())
		if not dims:
			continue
		try:
			day = datetime.datetime.strptime(str(dims[0]), "%Y%m%d").date()
		except ValueError:
			continue
		metrics = [*list(metrics or ()), 0, 0, 0, 0]
		out.append(
			{
				"metric_date": day,
				"value": str(dims[1]) if len(dims) > 1 else None,
				"extra": [str(d) for d in dims[2:]],
				"sessions": int(_num(metrics[0])),
				"total_users": int(_num(metrics[1])),
				"engaged_sessions": int(_num(metrics[2])),
				"key_events": _num(metrics[3]),
			}
		)
	return out


def parse_gsc_rows(rows):
	"""Search Console ``searchanalytics.query`` rows with ``date`` as the first dimension.

	Returns ``[{metric_date, value, extra, clicks, impressions, position}]``; ``value`` is the second
	key (page or query) or None for a date-only query, and ``extra`` the keys after it (the query,
	for a page x query row).
	"""
	out = []
	for row in rows or ():
		keys = list(row.get("keys") or ())
		day = as_date(keys[0]) if keys else None
		if not day:
			continue
		out.append(
			{
				"metric_date": day,
				"value": keys[1] if len(keys) > 1 else None,
				"extra": list(keys[2:]),
				"clicks": int(round(_num(row.get("clicks")))),
				"impressions": int(round(_num(row.get("impressions")))),
				"position": _num(row.get("position")),
			}
		)
	return out


# ---------------------------------------------------------------- landing pages and flags

#: "Many people see it in search, few click": rewrite the title and description.
LOW_CTR_MIN_IMPRESSIONS = 200
LOW_CTR_MAX = 0.015
#: "Striking distance": page two or the bottom of page one, where a small lift moves clicks.
STRIKING_POSITION_MIN = 8.0
STRIKING_POSITION_MAX = 20.0
STRIKING_MIN_IMPRESSIONS = 50
#: Traffic that never turns into an inquiry, call or form.
NO_KEY_EVENTS_MIN_SESSIONS = 30

FLAG_LOW_CTR = "low_ctr"
FLAG_STRIKING = "striking"
FLAG_NO_KEY_EVENTS = "no_key_events"


def ratio(numerator, denominator):
	"""``numerator / denominator``, or None when there is nothing to divide by."""
	return (float(numerator) / float(denominator)) if denominator else None


def search_flags(clicks, impressions, position):
	"""The search flags for a page or a query: low CTR and striking distance."""
	flags = []
	ctr = ratio(clicks, impressions)
	if impressions >= LOW_CTR_MIN_IMPRESSIONS and ctr is not None and ctr < LOW_CTR_MAX:
		flags.append(FLAG_LOW_CTR)
	if (
		impressions >= STRIKING_MIN_IMPRESSIONS
		and position is not None
		and STRIKING_POSITION_MIN <= position <= STRIKING_POSITION_MAX
	):
		flags.append(FLAG_STRIKING)
	return flags


def join_landing_pages(ga_rows, gsc_rows):
	"""GA4 landing pages and Search Console pages, joined on ``normalize_page``.

	``ga_rows``: ``{value, sessions, total_users, engaged_sessions, key_events}``;
	``gsc_rows``: ``{value, clicks, impressions, position}`` where ``position`` is already
	impression-weighted for that row. Rows are summed per path first. Returns one row per path,
	busiest first (sessions plus clicks), each with its rates and flags; a path only one source
	knows has the other source's figures as None, never 0, so "no data" is never read as "no
	traffic".
	"""
	pages = {}

	def page(path):
		return pages.setdefault(
			path,
			{
				"path": path,
				"sessions": None,
				"total_users": None,
				"engaged_sessions": None,
				"key_events": None,
				"clicks": None,
				"impressions": None,
				"_position_weight": 0.0,
			},
		)

	for row in ga_rows or ():
		path = normalize_page(row.get("value"))
		# "(not set)" is GA4's sessions with no landing page: not a page anyone can fix.
		if not path or path == NOT_SET:
			continue
		p = page(path)
		for key in ("sessions", "total_users", "engaged_sessions", "key_events"):
			p[key] = (p[key] or 0) + _num(row.get(key))
	for row in gsc_rows or ():
		path = normalize_page(row.get("value"))
		if not path:
			continue
		p = page(path)
		impressions = _num(row.get("impressions"))
		p["clicks"] = (p["clicks"] or 0) + _num(row.get("clicks"))
		p["impressions"] = (p["impressions"] or 0) + impressions
		p["_position_weight"] += _num(row.get("position")) * impressions

	out = []
	for p in pages.values():
		weight = p.pop("_position_weight")
		impressions = p["impressions"]
		p["position"] = (weight / impressions) if impressions else None
		p["ctr"] = ratio(p["clicks"], impressions) if impressions else None
		p["engagement_rate"] = ratio(p["engaged_sessions"], p["sessions"]) if p["sessions"] else None
		p["key_event_rate"] = ratio(p["key_events"], p["sessions"]) if p["sessions"] else None
		flags = search_flags(p["clicks"] or 0, impressions or 0, p["position"]) if impressions else []
		if (p["sessions"] or 0) >= NO_KEY_EVENTS_MIN_SESSIONS and not (p["key_events"] or 0):
			flags.append(FLAG_NO_KEY_EVENTS)
		p["flags"] = flags
		for key in ("sessions", "total_users", "engaged_sessions", "clicks", "impressions"):
			if p[key] is not None:
				p[key] = int(round(p[key]))
		out.append(p)
	out.sort(key=lambda r: ((r["sessions"] or 0) + (r["clicks"] or 0), r["impressions"] or 0), reverse=True)
	return out


def flagged(rows, flag, limit=15):
	"""The rows carrying ``flag``, most impressions (or sessions) first."""
	hits = [r for r in rows if flag in (r.get("flags") or ())]
	hits.sort(key=lambda r: (r.get("impressions") or 0, r.get("sessions") or 0), reverse=True)
	return hits[:limit]


# ---------------------------------------------------------------- queries

DEFAULT_BRANDED_TERMS = ("sapphire",)
_TERM_SPLIT = re.compile(r"[\n,;]+")


def branded_terms(text):
	"""The branded terms from Marketing Settings' text: one per line or comma, lower case."""
	terms = [t.strip().lower() for t in _TERM_SPLIT.split(str(text or "")) if t.strip()]
	return tuple(dict.fromkeys(terms)) or DEFAULT_BRANDED_TERMS


def is_branded(query, terms):
	"""Whether ``query`` names the brand: any term appears in it, ignoring case and spacing."""
	text = " ".join(str(query or "").lower().split())
	squeezed = text.replace(" ", "")
	return any(term in text or term.replace(" ", "") in squeezed for term in terms)


def summarize_queries(rows, terms, limit=50):
	"""Queries summed over the range, each marked branded or not, with the two totals.

	``rows``: ``{value, clicks, impressions, position}`` per query per day. Search Console leaves
	out anonymized queries, so these totals are below the property's own; the page says so.
	"""
	by_query = {}
	for row in rows or ():
		query = str(row.get("value") or "")
		if not query:
			continue
		q = by_query.setdefault(query, {"query": query, "clicks": 0.0, "impressions": 0.0, "_w": 0.0})
		impressions = _num(row.get("impressions"))
		q["clicks"] += _num(row.get("clicks"))
		q["impressions"] += impressions
		q["_w"] += _num(row.get("position")) * impressions
	split = {
		"branded": {"clicks": 0, "impressions": 0, "queries": 0},
		"unbranded": {"clicks": 0, "impressions": 0, "queries": 0},
	}
	out = []
	for q in by_query.values():
		weight = q.pop("_w")
		q["clicks"] = int(round(q["clicks"]))
		q["impressions"] = int(round(q["impressions"]))
		q["position"] = (weight / q["impressions"]) if q["impressions"] else None
		q["ctr"] = ratio(q["clicks"], q["impressions"])
		q["branded"] = is_branded(q["query"], terms)
		q["flags"] = search_flags(q["clicks"], q["impressions"], q["position"])
		bucket = split["branded" if q["branded"] else "unbranded"]
		bucket["clicks"] += q["clicks"]
		bucket["impressions"] += q["impressions"]
		bucket["queries"] += 1
		out.append(q)
	for bucket in split.values():
		bucket["ctr"] = ratio(bucket["clicks"], bucket["impressions"])
	out.sort(key=lambda r: (r["clicks"], r["impressions"]), reverse=True)
	return {"rows": out[:limit], "split": split, "total_queries": len(out)}


# ---------------------------------------------------------------- channels and the funnel


def summarize_channels(rows):
	"""GA4 channels summed over the range: sessions to key events, busiest first."""
	by_channel = {}
	for row in rows or ():
		name = str(row.get("value") or NOT_SET)
		c = by_channel.setdefault(
			name, {"channel": name, "sessions": 0, "engaged_sessions": 0, "key_events": 0.0}
		)
		c["sessions"] += int(_num(row.get("sessions")))
		c["engaged_sessions"] += int(_num(row.get("engaged_sessions")))
		c["key_events"] += _num(row.get("key_events"))
	total = sum(c["sessions"] for c in by_channel.values())
	out = []
	for c in by_channel.values():
		c["share"] = ratio(c["sessions"], total)
		c["key_event_rate"] = ratio(c["key_events"], c["sessions"])
		c["engagement_rate"] = ratio(c["engaged_sessions"], c["sessions"])
		out.append(c)
	out.sort(key=lambda c: c["sessions"], reverse=True)
	return out


def funnel(sessions, inquiries, opportunities, won):
	"""Sessions -> inquiries -> Opportunities -> won, with the rate from each step to the next.

	A step whose source is missing (GA4 not pulled for the range) is None, and the rate into and
	out of it is None too: a blank, never a zero that reads as "the website got no traffic".
	"""
	steps = [
		("sessions", sessions),
		("inquiries", inquiries),
		("opportunities", opportunities),
		("won", won),
	]
	out = []
	previous = None
	for key, value in steps:
		rate = ratio(value, previous) if (value is not None and previous) else None
		out.append({"step": key, "value": value, "rate": rate})
		previous = value
	return out


def change(current, previous):
	"""The relative change from ``previous`` to ``current``; None when either is missing or previous is 0."""
	if current is None or previous is None or not previous:
		return None
	return (float(current) - float(previous)) / float(previous)


def is_unsourced(lead_source, unknown_label):
	"""Blank, or the recorded "Unknown (pre-Aug 2026)" gap. A gap is not a channel."""
	value = str(lead_source or "").strip()
	return not value or value == unknown_label


# ---------------------------------------------------------------- the trend


def trend(days, series, covered):
	"""One time axis for several daily series.

	``series``: ``{name: {date: value}}``. ``covered``: ``{name: (first, last) or None}``, the days
	that source has data for. Inside its coverage a day with no row is 0 (nothing happened);
	outside it is None, so a source that started in June draws a gap before June, not a cliff.
	"""
	labels = [d.isoformat() for d in days]
	out = {}
	for name, values in series.items():
		span = covered.get(name)
		line = []
		for day in days:
			if span is None or day < span[0] or day > span[1]:
				line.append(values.get(day) if day in values else None)
			else:
				line.append(values.get(day, 0))
		out[name] = line
	return {"dates": labels, "series": out}


# ---------------------------------------------------------------- calls

#: How long after a call an Opportunity for the same Customer or Contact still counts as from it.
CALL_TO_OPPORTUNITY_DAYS = 30
_DIGITS = re.compile(r"\D+")


def phone_key(number):
	"""The last ten digits of a phone number, the part that is the same however it was typed."""
	digits = _DIGITS.sub("", str(number or ""))
	return digits[-10:] if len(digits) >= 7 else ""


def _call_order(call):
	return (call["day"], str(call.get("at") or ""), call.get("name") or "")


def summarize_calls(calls, opportunities, start, end, window_days=CALL_TO_OPPORTUNITY_DAYS):
	"""Incoming calls per day, first-time callers, and how many became an Opportunity.

	``calls``: every incoming call up to ``end`` (earlier ones too, so "first time" can look back):
	``{name, day, at, number, customers, contacts}``, ``at`` its creation time: calls are ordered by
	it, so two calls from one number on one day are first and repeat in the order they came in, not
	in the order of their names (v1.756.1). ``opportunities``: ``{name, day, customer,
	contact}`` created from ``start`` on. A call is **first-time** when no earlier call came from its
	number; a call with no number is never first-time, because nothing can say it is. A call
	**became an Opportunity** when an Opportunity for one of its Customers or Contacts was created
	that day or in the ``window_days`` after it. Each Opportunity is credited to one call at most,
	the latest before it, so a customer who called three times about one job is one conversion.
	"""
	calls = sorted(calls or (), key=_call_order)
	seen = set()
	first_time = set()
	for call in calls:
		key = phone_key(call.get("number"))
		if key and key not in seen:
			first_time.add(call.get("name"))
		if key:
			seen.add(key)

	in_range = [c for c in calls if start <= c["day"] <= end]
	converted = {}
	for opp in sorted(opportunities or (), key=lambda o: (o["day"], o.get("name") or "")):
		best = None
		for call in in_range:
			if call["day"] > opp["day"] or (opp["day"] - call["day"]).days > window_days:
				continue
			matches = (opp.get("customer") and opp["customer"] in (call.get("customers") or ())) or (
				opp.get("contact") and opp["contact"] in (call.get("contacts") or ())
			)
			if matches and (best is None or _call_order(call) > _call_order(best)):
				best = call
		if best is not None:
			converted.setdefault(best.get("name"), []).append(opp.get("name"))

	per_day = {}
	for call in in_range:
		d = per_day.setdefault(
			call["day"], {"incoming": 0, "first_time": 0, "converted": 0, "first_time_unconverted": 0}
		)
		d["incoming"] += 1
		if call.get("name") in first_time:
			d["first_time"] += 1
		if call.get("name") in converted:
			d["converted"] += 1
		if call.get("name") in first_time and call.get("name") not in converted:
			d["first_time_unconverted"] += 1
	return {
		"incoming": len(in_range),
		"first_time": sum(1 for c in in_range if c.get("name") in first_time),
		# The funnel's extra inquiries: first-time callers whose call did not become an Opportunity
		# (one that did is already counted as that Opportunity).
		"first_time_unconverted": sum(
			1 for c in in_range if c.get("name") in first_time and c.get("name") not in converted
		),
		"converted_calls": len(converted),
		"opportunities": sorted({o for names in converted.values() for o in names}),
		"per_day": per_day,
		"first_time_names": first_time,
		"converted": converted,
	}
