# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The engagement pull-back: each published post's figures, nightly (TASK-2026-01488).

**Every Social Post Metric row is a lifetime total as of its date**, not that day's increment.
That is what three of the four networks can give: Facebook, Instagram and LinkedIn report a
post's lifetime figures and nothing per day, so the only honest row is "as of this date, the
post had reached N". It is also the only meaning **reach** can have -- unique people do not add
up across days. YouTube's Analytics API does report per day; its days are summed from the day
of publishing, so a YouTube row means the same thing as everybody else's. A day's increment is
the difference of two rows; a post's figures are its newest row.

The conventions are the ad connectors' (``core/sync.py``), applied per job:

* **Restate a trailing window, then upsert.** YouTube revises closed days, so each run re-pulls
  the last ``restate_days`` (Marketing Settings, default 7) and rewrites those rows in place --
  a row is named from (job, date). The lifetime networks cannot restate a past day at all;
  their newest total already carries any revision, and it is written on today's row.
* **The cursor advances only on a clean run.** ``Social Publish Job.metrics_through`` is the last
  day known complete. A failure leaves it, and the error on the job, so the next run starts from
  it again, however long ago that was: a cursor that moved past a failed day would leave a hole
  nothing ever goes back for.
* **One job's failure is one job's problem, and one dead credential is one network's.** A 401
  stops that network for the rest of the run instead of failing every post on it.
* A post is followed for ``TRACK_DAYS`` after it went out; after that its last row stands.

Pure over a *store* and a *fetch*, like the outbox, so the state machine is tested without a
database or a network. The frappe half is ``metrics_sync.py``; the per-network reads and
parsers are ``insights.py``.
"""

import datetime

FIGURES = ("impressions", "reach", "engagements", "clicks", "video_views")
#: A post's engagement is followed this long after it went out.
TRACK_DAYS = 90
#: What a fetch returns.
LIFETIME = "lifetime"
DAILY = "daily"


def as_date(value):
	if value is None or value == "":
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def figures(values):
	"""The five figures from ``values``: ints, or None where the network gave nothing."""
	out = {}
	for key in FIGURES:
		value = (values or {}).get(key)
		out[key] = None if value is None or value == "" else int(value)
	return out


def is_due(job, today, track_days=TRACK_DAYS):
	"""Whether a job's engagement is still being followed."""
	published = as_date(job.get("published_at"))
	return bool(
		job.get("state") == "Published"
		and (job.get("external_post_id") or "").strip()
		and published
		and published <= today
		and (today - published).days <= track_days
	)


def window_start(job, today, restate_days):
	"""The first day a daily source is re-pulled from: the cursor's next day, or the trailing
	window, whichever is earlier -- never before the post existed."""
	published = as_date(job.get("published_at"))
	through = as_date(job.get("metrics_through"))
	trailing = today - datetime.timedelta(days=max(int(restate_days or 0), 1))
	start = min(through + datetime.timedelta(days=1), trailing) if through else published
	return max(start, published)


def cumulative(days):
	"""Daily rows ``[{date, figures...}]`` -> lifetime-to-date rows, summed in date order.

	A figure a day did not report adds nothing; one no day reported stays None.
	"""
	running = dict.fromkeys(FIGURES)
	out = []
	for day in sorted(days, key=lambda d: as_date(d["date"])):
		values = figures(day)
		for key in FIGURES:
			if values[key] is not None:
				running[key] = (running[key] or 0) + values[key]
		out.append({"metric_date": as_date(day["date"]), **running})
	return out


def rows_for(job, result, today, start):
	"""The Social Post Metric rows a fetch result writes: ``[{metric_date, figures...}]``."""
	if result.get("kind") == DAILY:
		return [row for row in cumulative(result.get("days") or []) if row["metric_date"] >= start]
	return [{"metric_date": today, **figures(result.get("figures"))}]


def pull(store, jobs, fetch, today, now, restate_days=7, track_days=TRACK_DAYS):
	"""Pull every due job's figures. Returns counters.

	``fetch(job, start, today)`` returns ``{"kind": "lifetime", "figures": {...}}`` or
	``{"kind": "daily", "days": [{"date", ...figures}]}`` and raises ``MarketingAPIError`` (with a
	``status``) on a network failure.
	"""
	counters = {"jobs": 0, "rows": 0, "failed": 0, "skipped": 0}
	dead = {}  # network -> the refusal that stopped it this run
	for job in jobs:
		if not is_due(job, today, track_days):
			continue
		network = job.get("network")
		if network in dead:
			counters["skipped"] += 1
			store.mark_failed(job["name"], dead[network], now)
			continue
		start = window_start(job, today, restate_days)
		try:
			result = fetch(job, start, today)
		except Exception as exc:  # a failure is recorded on the job; the cursor stays
			message = str(exc)[:1000] or type(exc).__name__
			counters["failed"] += 1
			if getattr(exc, "status", None) == 401:
				dead[network] = f"{network}: the credential was refused; reconnect it ({message})"[:1000]
				message = dead[network]
			store.mark_failed(job["name"], message, now)
			continue
		rows = rows_for(job, result, today, start)
		for row in rows:
			store.upsert(job, row)
		counters["jobs"] += 1
		counters["rows"] += len(rows)
		through = max((row["metric_date"] for row in rows), default=None)
		store.mark_done(job["name"], through, now)
	return counters


def latest(rows):
	"""A post's figures: its newest row's (rows are lifetime totals as of their date)."""
	if not rows:
		return dict.fromkeys(FIGURES)
	newest = max(rows, key=lambda row: as_date(row.get("metric_date")) or datetime.date.min)
	return figures(newest)
