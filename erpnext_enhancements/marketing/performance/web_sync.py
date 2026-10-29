# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""GA4 and Search Console stored by day, and the history engine every marketing source shares (v1.756.0).

Until v1.756.0 the only stored web figures were the nightly snapshot's 30-day totals
(``Marketing Web Snapshot``), and the GA4 Dashboard page asked Google live on every load for
the last 30 days, nothing older. Nik, 2026-10-29: "having all these analytics and the history of
them is great, so let's make sure we are keeping a history". So every day is kept, **forever**
(nothing here or anywhere prunes these tables), at the finest grain the page or a later report could
want, because once a source's own window has passed our rows are the only copy:

=========================== ============================================================
GA4 Daily Metric            date x site total, landing page, channel, landing page x
                            session source / medium, device, city (with region and
                            country): sessions, users, engaged sessions, key events
Search Console Daily Metric date x property total, page, query, page x query (which
                            searches land on which page): clicks, impressions, position
=========================== ============================================================

**Each day read is replaced whole.** For one dimension, every day the answer contains is deleted
and re-inserted in one transaction, so a page or query that drops out of a restated day leaves it
too, which an upsert never does. **A day the answer does not contain is left alone:** after Search
Console's 16 months Google returns nothing for it, and our rows are the history.

**The nightly pull** (cron 03:40, ``nightly_web_sync``) restates the last ``RESTATE_DAYS`` up to
the newest stored day plus every day since (``rules.nightly_window``). Search Console publishes
final figures two or three days late, so a night's answer for the last few days is incomplete and
the next nights fill it in. A missed night -- the deploy ``FLUSHDB`` drops queued jobs -- is caught up
the next night by the same rule, up to ``MAX_CATCHUP_DAYS``.

**The history backfill** reads each source back as far as its API allows (``SOURCES``): GA4 to the
property's first day of data, Search Console 16 months, Google Business Profile 18 months, the social
pages as far as each network serves. It runs **once, by itself, the first night a source is ready**
(configured, or connected and switched on) and has never been backfilled, and again whenever a
System Manager asks (the page's menu, ``start_backfill``, or ``bench --site <site> execute
erpnext_enhancements.marketing.performance.web_sync.backfill``). It is **not a migrate patch**: a
patch that calls Google would make the deploy depend on Google. Its cursor is on its Marketing Sync
Log row (``progress_through``), not in Redis, so a deploy that flushes the queue mid-run loses
nothing: the next nightly run finds the row still *Running* with nothing written for
``STALLED_AFTER_MINUTES`` and queues it again from where it stopped. Every source's writes are
replaces or upserts, so running any window twice is harmless.

GA4 and Search Console are not gated by Marketing Settings: the GA4 Settings they read are the ones
the nightly snapshot has used since June, and that snapshot is not gated either.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, getdate, now_datetime, nowdate

from erpnext_enhancements.marketing.performance import rules

GA4_DOCTYPE = "GA4 Daily Metric"
GSC_DOCTYPE = "Search Console Daily Metric"
LOG_DOCTYPE = "Marketing Sync Log"

SOURCE_GA4 = "Google Analytics 4"
SOURCE_GSC = "Search Console"
SOURCE_GBP = "Google Business Profile"
SOURCE_SOCIAL = "Social Pages"
WEB_SOURCES = (SOURCE_GA4, SOURCE_GSC)
SOURCES = (SOURCE_GA4, SOURCE_GSC, SOURCE_GBP, SOURCE_SOCIAL)

#: dimension -> (code in the record name, the API's dimensions after ``date``).
GA4_DIMENSIONS = {
	"Total": ("T", ()),
	"Landing Page": ("L", ("landingPage",)),
	"Channel": ("C", ("sessionDefaultChannelGroup",)),
	"Landing Page Source": ("S", ("landingPage", "sessionSourceMedium")),
	"Device": ("D", ("deviceCategory",)),
	"City": ("G", ("city", "region", "country")),
	# v1.757.0: the campaign a session's tags named (utm_campaign / utm_source / utm_medium), which is
	# how a Tracked Link's results are read back.
	"Campaign": ("K", ("sessionCampaignName", "sessionSource", "sessionMedium")),
}
GSC_DIMENSIONS = {
	"Total": ("T", ()),
	"Page": ("P", ("page",)),
	"Query": ("Q", ("query",)),
	"Page Query": ("X", ("page", "query")),
}
GA4_METRICS = ("sessions", "totalUsers", "engagedSessions", "keyEvents")
#: Where the search for the property's first day starts. GA4 properties exist only since 2019, and
#: Google documents no earliest startDate for the Data API (checked 2026-10-29).
GA4_EARLIEST = datetime.date(2019, 1, 1)
#: Search Console serves the last 16 months.
GSC_HISTORY_MONTHS = 16

RESTATE_DAYS = 7
#: The first nightly run with nothing stored reads this far back; the backfill does the rest.
INITIAL_DAYS = 30
MAX_CATCHUP_DAYS = 90
CHUNK_DAYS = 31
GA4_PAGE_SIZE = 100_000
GSC_PAGE_SIZE = 25_000
#: A runaway pagination loop stops here: 40 pages of 25,000 rows is more than a month can hold.
MAX_PAGES = 40

QUEUE = "long"
NIGHTLY_JOB_ID = "marketing-web-daily-sync"
BACKFILL_JOB_ID = "marketing-history-backfill"
TIMEOUT_SECONDS = 4 * 3600
#: A Running backfill row with nothing written for this long has no job behind it (a deploy flush).
STALLED_AFTER_MINUTES = 90

OPERATOR_ROLE = "System Manager"


class SourceUnavailable(Exception):
	"""A source that is not configured or refuses us. The message is one line, safe to log."""


# ---------------------------------------------------------------- Google


def _settings():
	return frappe.get_doc("GA4 Settings")


def ga4_reader(settings=None):
	"""``read(dimension, start, end) -> rows`` for GA4, or raise with a one-line reason."""
	from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

	from erpnext_enhancements.api.analytics import _ga4_client

	client, property_id, error = _ga4_client(settings or _settings())
	if error:
		raise SourceUnavailable(error)

	def read(dimension, start, end):
		names = ("date", *GA4_DIMENSIONS[dimension][1])
		flat = []
		offset = 0
		for _page in range(MAX_PAGES):
			response = client.run_report(
				RunReportRequest(
					property=f"properties/{property_id}",
					dimensions=[Dimension(name=n) for n in names],
					metrics=[Metric(name=m) for m in GA4_METRICS],
					date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
					limit=GA4_PAGE_SIZE,
					offset=offset,
				)
			)
			for row in response.rows:
				flat.append(([d.value for d in row.dimension_values], [m.value for m in row.metric_values]))
			offset += len(response.rows)
			if not response.rows or offset >= (response.row_count or 0):
				break
		return rules.parse_ga4_rows(flat)

	return read


def ga4_first_day(today, reader=None):
	"""The property's first day with any sessions: where GA4's history starts. None when it has none.

	One site-total read from the Data API's earliest allowed date; GA4 returns rows only for days with
	data, so the oldest row is the property's first day.
	"""
	reader = reader or ga4_reader()
	days = [r["metric_date"] for r in reader("Total", GA4_EARLIEST, today) if r["sessions"]]
	return min(days) if days else None


def gsc_reader(settings=None):
	"""``read(dimension, start, end) -> rows`` for Search Console, and the property form it used."""
	from erpnext_enhancements.api.analytics import (
		_gsc_account,
		_gsc_execute,
		_gsc_query_first_site,
		_gsc_refused_message,
		_gsc_service,
	)

	settings = settings or _settings()
	if not settings.gsc_property_url:
		raise SourceUnavailable("GSC Property URL is missing in GA4 Settings.")
	service, credentials, error = _gsc_service(settings)
	if error:
		raise SourceUnavailable(error)
	state = {"site": None}

	def body(dimension, start, end, start_row):
		return {
			"startDate": start.isoformat(),
			"endDate": end.isoformat(),
			"dimensions": ["date", *GSC_DIMENSIONS[dimension][1]],
			"rowLimit": GSC_PAGE_SIZE,
			"startRow": start_row,
		}

	def read(dimension, start, end):
		rows = []
		start_row = 0
		for _page in range(MAX_PAGES):
			request_body = body(dimension, start, end, start_row)
			if state["site"] is None:
				site, response, refusals = _gsc_query_first_site(
					service, credentials, settings.gsc_property_url, request_body
				)
				if not site:
					raise SourceUnavailable(
						_gsc_refused_message(settings.gsc_property_url, refusals, _gsc_account(credentials))
					)
				state["site"] = site
			else:
				response = _gsc_execute(
					service.searchanalytics().query(siteUrl=state["site"], body=request_body),
					credentials,
					dimension.lower(),
				)
			page = (response or {}).get("rows") or []
			rows.extend(page)
			# startRow pagination: a short page is the last one.
			if len(page) < GSC_PAGE_SIZE:
				break
			start_row += GSC_PAGE_SIZE
		return rules.parse_gsc_rows(rows)

	read.site = lambda: state["site"]
	return read


# ---------------------------------------------------------------- storage


def _replace(doctype, dimension, values):
	"""Replace ``dimension``'s rows for every day ``values`` holds, in one transaction. Returns rows written.

	Only those days: a day the source returned nothing for keeps what is stored, because past a
	source's window "nothing" means Google no longer has it, not that it never happened.
	"""
	days = sorted({v["metric_date"] for v in values})
	for chunk in range(0, len(days), 500):
		part = days[chunk : chunk + 500]
		frappe.db.sql(
			f"delete from `tab{doctype}` where dimension = %s and metric_date in ({', '.join(['%s'] * len(part))})",
			(dimension, *part),
		)
	if values:
		fields = list(values[0].keys())
		frappe.db.bulk_insert(
			doctype, fields, [tuple(v[f] for f in fields) for v in values], ignore_duplicates=True
		)
	frappe.db.commit()
	return len(values)


def _base(name, now, user="Administrator"):
	return {
		"name": name,
		"creation": now,
		"modified": now,
		"owner": user,
		"modified_by": user,
		"docstatus": 0,
	}


def ga4_values(dimension, rows, now):
	code = GA4_DIMENSIONS[dimension][0]
	out = []
	for row in rows:
		value = row["value"] if dimension != "Total" else None
		extra = [*(row.get("extra") or ()), None, None]
		out.append(
			{
				**_base(
					rules.record_name("GA4", row["metric_date"], code, value, *row.get("extra") or ()), now
				),
				"metric_date": row["metric_date"],
				"dimension": dimension,
				"dimension_value": value,
				"page_path": rules.normalize_page(value)
				if dimension in ("Landing Page", "Landing Page Source")
				else None,
				"source_medium": (
					extra[0]
					if dimension == "Landing Page Source"
					else f"{extra[0]} / {extra[1]}"
					if dimension == "Campaign"
					else None
				),
				"region": extra[0] if dimension == "City" else None,
				"country": extra[1] if dimension == "City" else None,
				"sessions": row["sessions"],
				"total_users": row["total_users"],
				"engaged_sessions": row["engaged_sessions"],
				"key_events": row["key_events"],
			}
		)
	return out


def gsc_values(dimension, rows, now, site):
	code = GSC_DIMENSIONS[dimension][0]
	out = []
	for row in rows:
		value = row["value"] if dimension != "Total" else None
		query = (row.get("extra") or [None])[0] if dimension == "Page Query" else None
		out.append(
			{
				**_base(
					rules.record_name("GSC", row["metric_date"], code, value, *row.get("extra") or ()), now
				),
				"metric_date": row["metric_date"],
				"dimension": dimension,
				"dimension_value": value,
				"page_path": rules.normalize_page(value) if dimension in ("Page", "Page Query") else None,
				"query": query,
				"property": site,
				"clicks": row["clicks"],
				"impressions": row["impressions"],
				"position": row["position"],
			}
		)
	return out


def pull(source, start, end, reader=None, on_chunk=None):
	"""Read GA4 or Search Console for ``start..end`` and replace the days answered. Returns rows written.

	One window of at most ``CHUNK_DAYS`` at a time, every dimension of it, then the next. A failure
	stops the run with the windows already written kept; ``on_chunk(chunk_end, written)`` is told
	after each, which is how the backfill moves its cursor.
	"""
	if source == SOURCE_GA4:
		reader = reader or ga4_reader()
		dimensions, doctype = GA4_DIMENSIONS, GA4_DOCTYPE
	else:
		reader = reader or gsc_reader()
		dimensions, doctype = GSC_DIMENSIONS, GSC_DOCTYPE
	written = 0
	for chunk_start, chunk_end in rules.chunks(start, end, CHUNK_DAYS):
		for dimension in dimensions:
			rows = reader(dimension, chunk_start, chunk_end)
			now = now_datetime()
			if source == SOURCE_GA4:
				values = ga4_values(dimension, rows, now)
			else:
				values = gsc_values(dimension, rows, now, getattr(reader, "site", lambda: None)())
			written += _replace(doctype, dimension, values)
		if on_chunk:
			on_chunk(chunk_end, written)
	return written


def last_stored(source):
	"""The newest day ``source`` has a site total for, or None."""
	doctype = GA4_DOCTYPE if source == SOURCE_GA4 else GSC_DOCTYPE
	if not frappe.db.table_exists(doctype):
		return None
	value = frappe.db.sql(f"select max(metric_date) from `tab{doctype}` where dimension = 'Total'")[0][0]
	return getdate(value) if value else None


def configured(settings=None):
	"""The web sources GA4 Settings is filled in for."""
	settings = settings or _settings()
	out = []
	if settings.get("ga4_property_id") and settings.get("credentials_json"):
		out.append(SOURCE_GA4)
	if settings.get("gsc_property_url") and settings.get("credentials_json"):
		out.append(SOURCE_GSC)
	return out


# ---------------------------------------------------------------- the four sources, for the backfill


def _sources():
	"""``{source: (ready(), earliest(today), pull(start, end, on_chunk), chunk_days)}``.

	``ready`` is the source's own gate: configured for the web sources; master switch, the source's
	switch and a connection for Business Profile and the social pages. ``earliest`` is the oldest day
	its API serves today.
	"""
	from erpnext_enhancements.marketing.gbp import sync as gbp_sync
	from erpnext_enhancements.marketing.publish import page_stats

	def web_ready(source):
		try:
			return source in configured()
		except Exception:
			return False

	return {
		SOURCE_GA4: (
			lambda: web_ready(SOURCE_GA4),
			lambda today: ga4_first_day(today),
			lambda start, end, on_chunk: pull(SOURCE_GA4, start, end, on_chunk=on_chunk),
			CHUNK_DAYS,
		),
		SOURCE_GSC: (
			lambda: web_ready(SOURCE_GSC),
			lambda today: rules.months_back(today, GSC_HISTORY_MONTHS),
			lambda start, end, on_chunk: pull(SOURCE_GSC, start, end, on_chunk=on_chunk),
			CHUNK_DAYS,
		),
		SOURCE_GBP: (
			gbp_sync.ready,
			gbp_sync.history_earliest,
			gbp_sync.history_pull,
			gbp_sync.HISTORY_CHUNK_DAYS,
		),
		SOURCE_SOCIAL: (
			page_stats.ready,
			page_stats.history_earliest,
			page_stats.history_pull,
			page_stats.HISTORY_CHUNK_DAYS,
		),
	}


# ---------------------------------------------------------------- the log


def _log(source, sync_type, start, end, status="Running"):
	doc = frappe.get_doc(
		{
			"doctype": LOG_DOCTYPE,
			"platform": source,
			"sync_type": sync_type,
			"status": status,
			"started_at": now_datetime(),
			"window_from": start,
			"window_to": end,
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	return doc.name


def _finish(log, status, written, error=None):
	frappe.db.set_value(
		LOG_DOCTYPE,
		log,
		{
			"status": status,
			"finished_at": now_datetime(),
			"created_count": written,
			"error_message": (str(error)[:2000] if error else None),
		},
		update_modified=False,
	)
	frappe.db.commit()


def _error_text(exc):
	"""One line for the log. ``SourceUnavailable`` and ``GscUnavailable`` are already safe; anything
	else is named by type and message only, never a traceback (the credentials are a frame local)."""
	return f"{type(exc).__name__}: {exc}"[:1000]


# ---------------------------------------------------------------- the nightly pull


def nightly_web_sync():
	"""Cron entry (hooks.py, 03:40). Starts any first backfill, resumes a dropped one, queues the pull."""
	if not frappe.db.exists("DocType", GA4_DOCTYPE) or not frappe.db.exists("DocType", "GA4 Settings"):
		return None
	ensure_backfills()
	resume_stalled_backfill()
	if not configured():
		return None
	frappe.enqueue(
		"erpnext_enhancements.marketing.performance.web_sync.run_nightly",
		queue=QUEUE,
		timeout=TIMEOUT_SECONDS,
		job_id=NIGHTLY_JOB_ID,
		deduplicate=True,
	)
	return NIGHTLY_JOB_ID


def run_nightly(today=None):
	"""The background job: each configured web source's restate window. One failing does not stop the other."""
	today = getdate(today or nowdate())
	outcomes = {}
	for source in configured():
		window = rules.nightly_window(
			last_stored(source), today, RESTATE_DAYS, INITIAL_DAYS, MAX_CATCHUP_DAYS
		)
		if not window:
			continue
		log = _log(source, "Daily Metrics", *window)
		try:
			written = pull(source, *window)
		except Exception as exc:
			frappe.db.rollback()
			_finish(log, "Failed", 0, _error_text(exc))
			outcomes[source] = "failed"
			continue
		_finish(log, "Completed", written)
		outcomes[source] = written
	return outcomes


# ---------------------------------------------------------------- the backfill


def _open_backfill(source):
	rows = frappe.get_all(
		LOG_DOCTYPE,
		filters={"platform": source, "sync_type": "Backfill", "status": ["in", ["Queued", "Running"]]},
		pluck="name",
		limit=1,
	)
	return rows[0] if rows else None


def _queue_backfill(source, today):
	"""Open (or widen) a Backfill log for ``source`` over its full history window. Returns its name or None."""
	ready, earliest, _pull, _chunk = _sources()[source]
	window = rules.history_window(earliest(today), today)
	if not window:
		return None
	start, end = window
	name = _open_backfill(source)
	if not name:
		return _log(source, "Backfill", start, end, status="Queued")
	# A longer history asked for while a run is open: widen its window and start it over from the new
	# start. Re-reading what it already did is harmless, since every write replaces or upserts.
	current = frappe.db.get_value(LOG_DOCTYPE, name, ["window_from", "window_to"], as_dict=True)
	if current and (not current.window_from or start < getdate(current.window_from)):
		frappe.db.set_value(
			LOG_DOCTYPE,
			name,
			{"window_from": start, "window_to": end, "progress_through": None},
			update_modified=False,
		)
		frappe.db.commit()
	return name


def ensure_backfills(today=None):
	"""The once-only automatic backfill: every ready source that has never had one gets one, and is queued.

	"Never" means no Backfill log at all, in any status. A source that becomes ready later -- Business
	Profile approved and connected, a social page switched on -- gets its history the first night after.
	Called nightly and right after a connection is made.
	"""
	today = getdate(today or nowdate())
	queued = []
	for source, (ready, _earliest, _pull, _chunk) in _sources().items():
		try:
			if not ready():
				continue
			if frappe.db.exists(LOG_DOCTYPE, {"platform": source, "sync_type": "Backfill"}):
				continue
			if _queue_backfill(source, today):
				queued.append(source)
		except Exception as exc:
			frappe.db.rollback()
			frappe.log_error(title="Marketing history backfill", message=f"{source}: {_error_text(exc)}")
	if queued:
		_enqueue_backfill()
	return queued


@frappe.whitelist(methods=["POST"])
def start_backfill(source=None):
	"""Queue the full-history backfill (System Manager): one source, or every ready one."""
	if OPERATOR_ROLE not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can start the backfill."), frappe.PermissionError)
	logs = backfill(sources=[source] if source else None, enqueue=True)
	if not logs:
		frappe.throw(_("No source is ready to backfill: fill in GA4 Settings, or connect and switch one on."))
	return {"logs": logs}


def backfill(sources=None, enqueue=False, today=None):
	"""A Backfill log per source over its whole history, then run them (here, or on ``long``).

	``bench execute`` runs it in the foreground; the button queues it. Either way the logs carry the
	cursor, so a run that is cut off is picked up by ``resume_stalled_backfill``.
	"""
	today = getdate(today or nowdate())
	registry = _sources()
	logs = []
	for source in sources or SOURCES:
		if source not in registry:
			frappe.throw(_("Unknown source {0}").format(source))
		if not registry[source][0]():
			continue
		name = _queue_backfill(source, today)
		if name:
			logs.append(name)
	if logs:
		if enqueue:
			_enqueue_backfill()
		else:
			run_backfill()
	return logs


def _enqueue_backfill():
	frappe.enqueue(
		"erpnext_enhancements.marketing.performance.web_sync.run_backfill",
		queue=QUEUE,
		timeout=TIMEOUT_SECONDS,
		job_id=BACKFILL_JOB_ID,
		deduplicate=True,
	)


def run_backfill():
	"""Every open Backfill log, from its cursor to its end. Each source commits after each window."""
	registry = _sources()
	results = {}
	for log in frappe.get_all(
		LOG_DOCTYPE,
		filters={
			"sync_type": "Backfill",
			"status": ["in", ["Queued", "Running"]],
			"platform": ["in", SOURCES],
		},
		fields=["name", "platform", "window_from", "window_to", "progress_through", "created_count"],
		order_by="creation asc",
	):
		_ready, _earliest, source_pull, chunk_days = registry[log.platform]
		start = getdate(log.window_from)
		if log.progress_through:
			start = getdate(add_to_date(getdate(log.progress_through), days=1))
		end = getdate(log.window_to)
		before = cint(log.created_count)
		frappe.db.set_value(
			LOG_DOCTYPE, log.name, {"status": "Running", "started_at": now_datetime()}, update_modified=False
		)
		frappe.db.commit()

		def moved(chunk_end, written, name=log.name, before=before):
			frappe.db.set_value(
				LOG_DOCTYPE,
				name,
				{
					"progress_through": chunk_end,
					"created_count": before + written,
					"finished_at": now_datetime(),
				},
				update_modified=False,
			)
			frappe.db.commit()

		written = 0
		try:
			for chunk_start, chunk_end in rules.chunks(start, end, chunk_days):
				written += source_pull(chunk_start, chunk_end, None) or 0
				moved(chunk_end, written)
		except Exception as exc:
			frappe.db.rollback()
			_finish(log.name, "Failed", before + written, _error_text(exc))
			results[log.platform] = "failed"
			continue
		_finish(log.name, "Completed", before + written)
		results[log.platform] = before + written
	return results


def resume_stalled_backfill():
	"""Re-queue a backfill a deploy flush dropped: a log still Queued or Running whose job is gone.

	"Gone" is read from the log, not the queue: nothing has been written to it for
	``STALLED_AFTER_MINUTES`` (``finished_at`` moves with every window). A live run writes a window
	every few minutes; one that stopped writing for an hour and a half is not running.
	"""
	threshold = add_to_date(now_datetime(), minutes=-STALLED_AFTER_MINUTES)
	stalled = False
	for log in frappe.get_all(
		LOG_DOCTYPE,
		filters={
			"sync_type": "Backfill",
			"status": ["in", ["Queued", "Running"]],
			"platform": ["in", SOURCES],
		},
		fields=["name", "started_at", "finished_at"],
	):
		last = log.finished_at or log.started_at
		if not last or get_datetime(last) < threshold:
			stalled = True
	if stalled:
		_enqueue_backfill()
	return stalled
