# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The nightly Business Profile pull, its history backfill, and the review snapshot (v1.756.0).

Gates, in the order ``core/tasks.py`` set for the ad connectors:

1. **Master switch** ``Marketing Settings.enabled`` and **this one**,
   ``google_business_profile_enabled``. Both ship 0.
2. **Connected only.** *Auth Failed* waits for a person to reconnect.
3. **Enqueue on ``long``** with a fixed ``job_id``. A deploy ``FLUSHDB`` costs one night: the
   window is computed from what is stored, so the next run restates the same days.

Each listing's window is ``RESTATE_DAYS`` back from its newest stored day plus every day since
(``performance.rules.nightly_window``). History before that -- the Performance API's
``HISTORY_MONTHS`` -- is the shared backfill's job (``performance/web_sync.py``), which calls
``history_pull`` a window at a time once the profile is connected and switched on.

**Reviews are a nightly snapshot, kept for ever.** Google returns only the reviews that exist now,
so each one is its own Business Profile Review row (first seen, last seen, the reply), and one that
a complete read no longer returns is marked *Gone From Google*, never deleted. Tonight's count,
average and unreplied land on today's daily row too.
"""

from zoneinfo import ZoneInfo

import frappe
from frappe.utils import cint, get_system_timezone, getdate, now_datetime, nowdate

from erpnext_enhancements.marketing.core.client import MarketingAPIError, Transport
from erpnext_enhancements.marketing.core.utils import field, get_credentials, get_settings, is_connected
from erpnext_enhancements.marketing.gbp import constants as G
from erpnext_enhancements.marketing.gbp import parse
from erpnext_enhancements.marketing.performance import rules

DOCTYPE = "Business Profile Daily Metric"
REVIEW_DOCTYPE = "Business Profile Review"
LOG_DOCTYPE = "Marketing Sync Log"
MAX_CATCHUP_DAYS = 180
HISTORY_MONTHS = G.HISTORY_MONTHS
HISTORY_CHUNK_DAYS = G.MAX_RANGE_DAYS


def enabled(settings=None):
	settings = settings or get_settings()
	return bool(cint(settings.get("enabled"))) and bool(cint(settings.get(G.ENABLED_FLAG)))


def ready():
	"""Switched on and connected: the gate for the nightly pull and the backfill alike."""
	try:
		return bool(frappe.db.exists("DocType", DOCTYPE)) and enabled() and is_connected(G.PLATFORM_GBP)
	except Exception:
		return False


def open_transport(creds=None, http=None):
	from erpnext_enhancements.marketing.core import oauth

	creds = creds or get_credentials()
	settings = get_settings()
	token = oauth.access_token(G.PLATFORM_GBP, creds, http=http)
	return Transport(
		G.PLATFORM_GBP,
		headers={"Authorization": f"Bearer {token}"},
		max_retries=settings.get("max_retries") or 3,
		timeout=settings.get("request_timeout_seconds") or 30,
		http=http,
	)


def _pages(transport, url, params, key, limit=G.REVIEWS_MAX_PAGES):
	"""Every page of a list call, following ``nextPageToken``: ``(bodies, complete)``.

	``complete`` is False when the page cap stopped the loop with more to read: nothing may be
	concluded from what such a read left out.
	"""
	bodies = []
	params = dict(params)
	for _page in range(limit):
		body = transport.request("GET", url, params=params) or {}
		bodies.append(body)
		token = body.get("nextPageToken")
		if not token or not body.get(key):
			return bodies, True
		params["pageToken"] = token
	return bodies, False


def discover(transport):
	"""Every listing the token reaches: ``[{account_id, location_id, title}]``."""
	out = []
	accounts, _complete = _pages(transport, f"{G.ACCOUNTS_BASE}/accounts", {"pageSize": 20}, "accounts")
	for body in accounts:
		for account in parse.parse_accounts(body):
			locations, _complete = _pages(
				transport,
				f"{G.INFORMATION_BASE}/accounts/{account['account_id']}/locations",
				{"readMask": G.LOCATIONS_READ_MASK, "pageSize": 100},
				"locations",
			)
			for page in locations:
				for loc in parse.parse_locations(page):
					out.append({"account_id": account["account_id"], **loc})
	return out


def _last_stored(location_id):
	value = frappe.db.sql(
		f"select max(metric_date) from `tab{DOCTYPE}` where location_id = %s and metrics_read = 1",
		(location_id,),
	)[0][0]
	return getdate(value) if value else None


def upsert(location_id, title, day, values):
	"""Create or update one listing-day; only the columns given are written."""
	name = f"GBP-{location_id}-{day.isoformat()}"
	if frappe.db.exists(DOCTYPE, name):
		frappe.db.set_value(DOCTYPE, name, {"location_title": title, **values}, update_modified=True)
		return name
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"location_id": location_id,
			"location_title": title,
			"metric_date": day,
			**values,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		frappe.db.set_value(DOCTYPE, name, {"location_title": title, **values})
	return name


def pull_metrics(transport, listing, start, end):
	"""One listing's daily figures for ``start..end``: only the days Google answered. Returns rows written.

	A day Google has not published yet is absent (a zero is a dated entry with no value, which the
	parser reads as 0), and writing 0 for it would store a drop that the restate window might never
	correct.
	"""
	written = 0
	for chunk_start, chunk_end in rules.chunks(start, end, G.MAX_RANGE_DAYS):
		body = transport.request(
			"GET",
			f"{G.PERFORMANCE_BASE}/locations/{listing['location_id']}:fetchMultiDailyMetricsTimeSeries?"
			+ parse.metrics_query(chunk_start, chunk_end),
		)
		for day, columns in sorted(parse.parse_daily_metrics(body).items()):
			if chunk_start <= day <= chunk_end:
				values = {c: columns.get(c, 0) for c in G.COLUMNS}
				upsert(listing["location_id"], listing["title"], day, {**values, "metrics_read": 1})
				written += 1
	return written


def snapshot_reviews(transport, listing, today):
	"""Tonight's reviews: one row each, the removed ones marked, the summary on today's row."""
	location_id = listing["location_id"]
	pages, complete = _pages(
		transport,
		f"{G.REVIEWS_BASE}/accounts/{listing['account_id']}/locations/{location_id}/reviews",
		{"pageSize": G.REVIEWS_PAGE_SIZE},
		"reviews",
	)
	seen = set()
	for review in parse.parse_reviews(pages, tz=ZoneInfo(get_system_timezone())):
		key = rules.value_key(review["review_id"])
		seen.add(key)
		name = f"GBPR-{location_id}-{key}"
		values = {**review, "location_title": listing["title"], "last_seen": today, "removed_on": None}
		if frappe.db.exists(REVIEW_DOCTYPE, name):
			frappe.db.set_value(REVIEW_DOCTYPE, name, values)
		else:
			frappe.get_doc(
				{
					"doctype": REVIEW_DOCTYPE,
					"location_id": location_id,
					"review_key": key,
					"first_seen": today,
					**values,
				}
			).insert(ignore_permissions=True)
	if complete:
		for name, key in frappe.db.sql(
			f"""select name, review_key from `tab{REVIEW_DOCTYPE}`
			where location_id = %s and removed_on is null""",
			(location_id,),
		):
			if key not in seen:
				frappe.db.set_value(REVIEW_DOCTYPE, name, "removed_on", today)
	upsert(location_id, listing["title"], today, {**parse.summarize_reviews(pages), "reviews_read": 1})


def pull_listing(transport, listing, today):
	"""One listing tonight: its restate window, then its reviews. Returns daily rows written."""
	window = rules.nightly_window(
		_last_stored(listing["location_id"]), today, G.RESTATE_DAYS, G.INITIAL_DAYS, MAX_CATCHUP_DAYS
	)
	written = pull_metrics(transport, listing, *window) if window else 0
	# The figures are kept whatever the reviews read does: Google serves reviews only for verified
	# listings, and a refusal there must not roll back the night's metrics.
	frappe.db.commit()
	try:
		snapshot_reviews(transport, listing, today)
	except MarketingAPIError as exc:
		frappe.db.rollback()
		if exc.is_auth_failure:
			raise
		frappe.log_error(
			title="Marketing Business Profile",
			message=f"Business Profile reviews for {listing['location_id']}: {exc}",
		)
		return written
	frappe.db.commit()
	return written


# ---------------------------------------------------------------- history (web_sync's backfill)


def history_scopes():
	"""Each listing the token reaches, by ID: each has a backfill of its own, so one added later gets
	its 18 months too, not just the nightly window."""
	return [listing["location_id"] for listing in discover(open_transport())]


def history_earliest(today, _scope=None):
	"""The oldest day the Performance API serves: ``HISTORY_MONTHS`` back, the same for every listing."""
	return rules.months_back(today, HISTORY_MONTHS)


def history_pull(start, end, scope=None):
	"""One listing's daily figures for ``start..end`` (every listing's without a ``scope``).

	Raises on failure, so the backfill's cursor stays. A listing no longer reachable writes nothing.
	"""
	transport = open_transport()
	written = 0
	for listing in discover(transport):
		if scope and listing["location_id"] != scope:
			continue
		try:
			written += pull_metrics(transport, listing, start, end)
		except MarketingAPIError as exc:
			# A 400 for a window: older than Google keeps (the v1 limit is undocumented). Skipped so
			# the backfill moves on to the days Google does serve; anything else stops it.
			frappe.db.rollback()
			if exc.status != 400:
				raise
			continue
		frappe.db.commit()
	return written


# ---------------------------------------------------------------- the nightly job


def nightly_business_profile_sync():
	"""Cron entry (hooks.py, 03:45). See the module docstring for the gates."""
	if not ready():
		return None
	frappe.enqueue(
		"erpnext_enhancements.marketing.gbp.sync.run_sync",
		queue=G.QUEUE,
		timeout=G.TIMEOUT_SECONDS,
		job_id=G.JOB_ID,
		deduplicate=True,
	)
	return G.JOB_ID


def run_sync(http=None, today=None):
	"""The background job. A dead token marks the connection *Auth Failed*; it never retries nightly."""
	today = getdate(today or nowdate())
	creds = get_credentials()
	log = frappe.get_doc(
		{
			"doctype": LOG_DOCTYPE,
			"platform": G.PLATFORM_GBP,
			"sync_type": "Daily Metrics",
			"status": "Running",
			"started_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	written = failed = 0
	errors = []
	try:
		transport = open_transport(creds, http=http)
		for listing in discover(transport):
			try:
				written += pull_listing(transport, listing, today)
			except MarketingAPIError as exc:
				frappe.db.rollback()
				failed += 1
				errors.append(f"{listing['location_id']}: {exc}")
				if exc.is_auth_failure:
					raise
	except MarketingAPIError as exc:
		if exc.is_auth_failure:
			creds.set(field(G.PLATFORM_GBP, "connection_status"), "Auth Failed")
			creds.set(field(G.PLATFORM_GBP, "status_message"), str(exc)[:500])
			creds.set(field(G.PLATFORM_GBP, "last_error_at"), now_datetime())
			creds.save(ignore_permissions=True)
		errors.append(str(exc))
	status = "Failed" if errors and not written else "Completed"
	frappe.db.set_value(
		LOG_DOCTYPE,
		log.name,
		{
			"status": status,
			"finished_at": now_datetime(),
			"created_count": written,
			"failed_count": failed,
			"error_message": "; ".join(errors)[:2000] or None,
		},
		update_modified=False,
	)
	frappe.db.commit()
	return {"written": written, "failed": failed}
