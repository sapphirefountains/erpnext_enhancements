# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The nightly Business Profile pull: listings, their daily figures, their reviews (v1.756.0).

Gates, in the order ``core/tasks.py`` set for the ad connectors:

1. **Master switch** ``Marketing Settings.enabled`` and **this one**,
   ``google_business_profile_enabled``. Both ship 0.
2. **Connected only.** *Auth Failed* waits for a person to reconnect.
3. **Enqueue on ``long``** with a fixed ``job_id``. A deploy ``FLUSHDB`` costs one night: the
   window is computed from what is stored, so the next run restates the same days.

Each listing's window is ``RESTATE_DAYS`` back from its newest stored day plus every day since
(``performance.rules.nightly_window``); the first run reads ``INITIAL_DAYS``. Reviews are read in
full each night and written onto that night's row: count, average and unreplied, as of the pull.
"""

import frappe
from frappe.utils import cint, getdate, nowdate

from erpnext_enhancements.marketing.core.client import Transport
from erpnext_enhancements.marketing.core.utils import field, get_credentials, get_settings, is_connected
from erpnext_enhancements.marketing.gbp import constants as G
from erpnext_enhancements.marketing.gbp import parse
from erpnext_enhancements.marketing.performance import rules

DOCTYPE = "Business Profile Daily Metric"
LOG_DOCTYPE = "Marketing Sync Log"
MAX_CATCHUP_DAYS = 180


def enabled(settings=None):
	settings = settings or get_settings()
	return bool(cint(settings.get("enabled"))) and bool(cint(settings.get(G.ENABLED_FLAG)))


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
	"""Every page of a list call, following ``nextPageToken``."""
	bodies = []
	params = dict(params)
	for _page in range(limit):
		body = transport.request("GET", url, params=params) or {}
		bodies.append(body)
		token = body.get("nextPageToken")
		if not token or not body.get(key):
			break
		params["pageToken"] = token
	return bodies


def discover(transport):
	"""Every listing the token reaches: ``[{account_id, location_id, title}]``."""
	out = []
	for body in _pages(transport, f"{G.ACCOUNTS_BASE}/accounts", {"pageSize": 20}, "accounts"):
		for account in parse.parse_accounts(body):
			for page in _pages(
				transport,
				f"{G.INFORMATION_BASE}/accounts/{account['account_id']}/locations",
				{"readMask": G.LOCATIONS_READ_MASK, "pageSize": 100},
				"locations",
			):
				for loc in parse.parse_locations(page):
					out.append({"account_id": account["account_id"], **loc})
	return out


def _last_stored(location_id):
	value = frappe.db.sql(
		f"select max(metric_date) from `tab{DOCTYPE}` where location_id = %s", (location_id,)
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


def pull_listing(transport, listing, today):
	"""One listing: its daily figures for the restate window, then its reviews onto today's row."""
	location_id = listing["location_id"]
	window = rules.nightly_window(
		_last_stored(location_id), today, G.RESTATE_DAYS, G.INITIAL_DAYS, MAX_CATCHUP_DAYS
	)
	written = 0
	if window:
		body = transport.request(
			"GET",
			f"{G.PERFORMANCE_BASE}/locations/{location_id}:fetchMultiDailyMetricsTimeSeries?"
			+ parse.metrics_query(*window),
		)
		by_day = parse.parse_daily_metrics(body)
		for day in rules.days_between(*window):
			values = {column: by_day.get(day, {}).get(column, 0) for column in G.COLUMNS}
			upsert(location_id, listing["title"], day, values)
			written += 1
	reviews = _pages(
		transport,
		f"{G.REVIEWS_BASE}/accounts/{listing['account_id']}/locations/{location_id}/reviews",
		{"pageSize": G.REVIEWS_PAGE_SIZE},
		"reviews",
	)
	upsert(location_id, listing["title"], today, parse.summarize_reviews(reviews))
	frappe.db.commit()
	return written


def nightly_business_profile_sync():
	"""Cron entry (hooks.py, 03:45). See the module docstring for the gates."""
	if not frappe.db.exists("DocType", DOCTYPE) or not enabled():
		return None
	if not is_connected(G.PLATFORM_GBP):
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
	from frappe.utils import now_datetime

	from erpnext_enhancements.marketing.core.client import MarketingAPIError

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
		listings = discover(transport)
		for listing in listings:
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
