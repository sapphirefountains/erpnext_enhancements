# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""GA4 and Search Console, stored by day (v1.756.0).

Until v1.756.0 the only stored web figures were the nightly snapshot's 30-day totals
(``Marketing Web Snapshot``), and the GA4 Dashboard page asked Google live on every load for
the last 30 days, nothing older. This keeps every day instead:

=========================== ============================================================
GA4 Daily Metric            date x (site total | landing page | channel): sessions,
                            users, engaged sessions, key events
Search Console Daily Metric date x (property total | page | query): clicks, impressions,
                            average position
=========================== ============================================================

**Each day read is replaced whole.** For one source, dimension and window the rows are
deleted and the fresh answer inserted, in one transaction per window. Names are still
deterministic (``rules.record_name``), but the replace is what matters: a page or query that
drops out of a restated day must leave it too, which an upsert never does.

**The nightly pull** (cron 03:40, ``nightly_web_sync``) restates the last ``RESTATE_DAYS`` up to
the newest stored day plus every day since (``rules.nightly_window``). Search Console publishes
final figures two or three days late, so a night's answer for the last few days is incomplete
and the next nights fill it in. A missed night -- the deploy ``FLUSHDB`` drops queued jobs -- is
caught up the next night by the same rule, up to ``MAX_CATCHUP_DAYS``.

**The backfill** (``start_backfill``, a System Manager button on the page, or ``bench --site <site>
execute erpnext_enhancements.marketing.performance.web_sync.backfill --kwargs "{'days': 480}"``)
walks Search Console's 16 months and the same span of GA4, a month at a time, oldest first. It is
**not a migrate patch**: a patch that calls Google would make the deploy depend on Google. Its
progress lives on its Marketing Sync Log row (``progress_through``), not in Redis, so a deploy that
flushes the queue mid-run loses nothing: the next nightly run finds the row still *Running*, with no
job alive behind it, and queues it again from where it stopped. Replacing whole days makes running
any window twice harmless.

Neither is gated by Marketing Settings: the GA4 Settings this reads are the ones the nightly
snapshot has used since June, and that snapshot is not gated either.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, getdate, now_datetime, nowdate

from erpnext_enhancements.marketing.performance import rules

GA4_DOCTYPE = "GA4 Daily Metric"
GSC_DOCTYPE = "Search Console Daily Metric"
LOG_DOCTYPE = "Marketing Sync Log"

SOURCE_GA4 = "Google Analytics 4"
SOURCE_GSC = "Search Console"
SOURCES = (SOURCE_GA4, SOURCE_GSC)

#: dimension -> (code in the record name, the API's dimension name or None for the total).
GA4_DIMENSIONS = {
	"Total": ("T", None),
	"Landing Page": ("L", "landingPage"),
	"Channel": ("C", "sessionDefaultChannelGroup"),
}
GSC_DIMENSIONS = {
	"Total": ("T", None),
	"Page": ("P", "page"),
	"Query": ("Q", "query"),
}
GA4_METRICS = ("sessions", "totalUsers", "engagedSessions", "keyEvents")

RESTATE_DAYS = 7
#: The first nightly run with nothing stored reads this far back; the backfill does the rest.
INITIAL_DAYS = 30
MAX_CATCHUP_DAYS = 90
#: Search Console keeps 16 months, about 486 days.
BACKFILL_DAYS = 480
CHUNK_DAYS = 31
GA4_PAGE_SIZE = 100_000
GSC_PAGE_SIZE = 25_000
#: A runaway pagination loop stops here: 40 pages of 25,000 rows is more than a month can hold.
MAX_PAGES = 40

QUEUE = "long"
NIGHTLY_JOB_ID = "marketing-web-daily-sync"
BACKFILL_JOB_ID = "marketing-web-backfill"
TIMEOUT_SECONDS = 2 * 3600
#: A Running backfill row older than this with no job behind it was dropped (a deploy flush).
STALLED_AFTER_MINUTES = 90

OPERATOR_ROLE = "System Manager"


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
		api_dimension = GA4_DIMENSIONS[dimension][1]
		dimensions = [Dimension(name="date")] + ([Dimension(name=api_dimension)] if api_dimension else [])
		flat = []
		offset = 0
		for _page in range(MAX_PAGES):
			response = client.run_report(
				RunReportRequest(
					property=f"properties/{property_id}",
					dimensions=dimensions,
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
		api_dimension = GSC_DIMENSIONS[dimension][1]
		return {
			"startDate": start.isoformat(),
			"endDate": end.isoformat(),
			"dimensions": ["date"] + ([api_dimension] if api_dimension else []),
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


class SourceUnavailable(Exception):
	"""A source that is not configured or refuses us. The message is one line, safe to log."""


# ---------------------------------------------------------------- storage


def _replace(doctype, dimension, start, end, values):
	"""Delete ``doctype``'s ``dimension`` rows in ``start..end`` and insert ``values``. One transaction."""
	frappe.db.sql(
		f"delete from `tab{doctype}` where dimension = %s and metric_date between %s and %s",
		(dimension, start, end),
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
		out.append(
			{
				**_base(rules.record_name("GA4", row["metric_date"], code, value), now),
				"metric_date": row["metric_date"],
				"dimension": dimension,
				"dimension_value": value,
				"page_path": rules.normalize_page(value) if dimension == "Landing Page" else None,
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
		out.append(
			{
				**_base(rules.record_name("GSC", row["metric_date"], code, value), now),
				"metric_date": row["metric_date"],
				"dimension": dimension,
				"dimension_value": value,
				"page_path": rules.normalize_page(value) if dimension == "Page" else None,
				"property": site,
				"clicks": row["clicks"],
				"impressions": row["impressions"],
				"position": row["position"],
			}
		)
	return out


def pull(source, start, end, reader=None, on_chunk=None):
	"""Read ``source`` for ``start..end`` and replace those days. Returns rows written.

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
			written += _replace(doctype, dimension, chunk_start, chunk_end, values)
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
	"""The sources GA4 Settings is filled in for."""
	settings = settings or _settings()
	out = []
	if settings.get("ga4_property_id") and settings.get("credentials_json"):
		out.append(SOURCE_GA4)
	if settings.get("gsc_property_url") and settings.get("credentials_json"):
		out.append(SOURCE_GSC)
	return out


# ---------------------------------------------------------------- the log


def _log(source, sync_type, start, end):
	doc = frappe.get_doc(
		{
			"doctype": LOG_DOCTYPE,
			"platform": source,
			"sync_type": sync_type,
			"status": "Running",
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
	"""Cron entry (hooks.py, 03:40). Queues the pull on ``long``; resumes a dropped backfill first."""
	if not frappe.db.exists("DocType", GA4_DOCTYPE) or not frappe.db.exists("DocType", "GA4 Settings"):
		return None
	if not configured():
		return None
	resume_stalled_backfill()
	frappe.enqueue(
		"erpnext_enhancements.marketing.performance.web_sync.run_nightly",
		queue=QUEUE,
		timeout=TIMEOUT_SECONDS,
		job_id=NIGHTLY_JOB_ID,
		deduplicate=True,
	)
	return NIGHTLY_JOB_ID


def run_nightly(today=None):
	"""The background job: each configured source's restate window. One source failing does not stop the other."""
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


@frappe.whitelist(methods=["POST"])
def start_backfill(days=BACKFILL_DAYS):
	"""Queue the history backfill (System Manager). Returns the log rows it will fill."""
	if OPERATOR_ROLE not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can start the backfill."), frappe.PermissionError)
	sources = configured()
	if not sources:
		frappe.throw(_("Fill in GA4 Settings first: nothing is configured to read."))
	return {"logs": backfill(days=days, enqueue=True)}


def backfill(days=BACKFILL_DAYS, enqueue=False, sources=None):
	"""One Backfill log per source, then run them (here, or on ``long`` with ``enqueue``).

	``bench execute`` runs it in the foreground; the button queues it. Either way the logs carry the
	cursor, so a run that is cut off is picked up by ``resume_stalled_backfill``.
	"""
	days = min(max(cint(days) or BACKFILL_DAYS, 1), rules.MAX_RANGE_DAYS)
	end = getdate(nowdate())
	end = getdate(add_to_date(end, days=-1))
	start = getdate(add_to_date(end, days=-(days - 1)))
	logs = []
	for source in sources or configured():
		open_ = frappe.get_all(
			LOG_DOCTYPE,
			filters={"platform": source, "sync_type": "Backfill", "status": ["in", ["Queued", "Running"]]},
			pluck="name",
			limit=1,
		)
		if open_:
			# A longer history asked for while a run is open: widen its window and start it over from
			# the new start. Re-reading months it already did is harmless, since each is replaced whole.
			current = frappe.db.get_value(LOG_DOCTYPE, open_[0], "window_from")
			if current and start < getdate(current):
				frappe.db.set_value(
					LOG_DOCTYPE,
					open_[0],
					{"window_from": start, "progress_through": None},
					update_modified=False,
				)
				frappe.db.commit()
			logs.append(open_[0])
		else:
			logs.append(_log(source, "Backfill", start, end))
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
	"""Every open Backfill log, from its cursor to its end. Commits after each window."""
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

		if start > end:
			_finish(log.name, "Completed", before)
			results[log.platform] = before
			continue
		try:
			written = pull(log.platform, start, end, on_chunk=moved)
		except Exception as exc:
			frappe.db.rollback()
			progress = frappe.db.get_value(LOG_DOCTYPE, log.name, "created_count") or before
			_finish(log.name, "Failed", progress, _error_text(exc))
			results[log.platform] = "failed"
			continue
		_finish(log.name, "Completed", before + written)
		results[log.platform] = before + written
	return results


def resume_stalled_backfill():
	"""Re-queue a backfill a deploy flush dropped: a log still Running whose job is gone.

	"Gone" is read from the log, not the queue: its last window was written over
	``STALLED_AFTER_MINUTES`` ago (``finished_at`` moves with every window). A live run writes a
	window every few minutes; one that stopped writing for an hour and a half is not running.
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
