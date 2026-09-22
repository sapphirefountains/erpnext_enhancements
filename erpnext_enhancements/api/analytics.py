"""Marketing analytics endpoints (Google Analytics 4 + Google Search Console).

Whitelisted API that powers the GA4 Dashboard Desk page
(``enhancements_core/page/ga4_dashboard/ga4_dashboard.js``), which calls
``get_ga4_data`` and ``get_gsc_data``.

External services:
        - Google Analytics 4 Data API (``BetaAnalyticsDataClient``).
        - Google Search Console Search Analytics API (``searchconsole`` v1).

Configuration & credentials: read from the ``GA4 Settings`` Single DocType
(property id / property url plus a service-account JSON file). The credentials
JSON MUST be uploaded as a Private file (``/private/files/...``); a public path
is rejected to avoid leaking the service account key. Each endpoint fans out
its independent report queries across a thread pool to reduce latency, and
returns Frappe-Charts-shaped dicts (or an ``{"error": ...}`` dict on failure).

Security: requires an authenticated session (default whitelist). Errors are
logged to the Error Log and returned as a plain ``{"error": ...}`` message.
"""

import concurrent.futures
import datetime as dt
import os
from datetime import datetime

import frappe
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
	DateRange,
	Dimension,
	Metric,
	OrderBy,
	RunReportRequest,
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from erpnext_enhancements.utils.error_throttle import log_error_throttled
from erpnext_enhancements.utils.search_console import rolling_sums, site_candidates, window_for


@frappe.whitelist()
def get_ga4_data():
	"""
	Retrieves Google Analytics 4 (GA4) 'activeUsers' and 'sessions' for Traffic Timeline,
	'sessions' by channel for Acquisition Channels, and 'conversions' by eventName for Conversions
	for the past 30 days. It fetches the property ID and credentials file path from the 'GA4 Settings' Single DocType.

	Returns:
		dict: A dictionary formatted for Frappe Charts, containing 'traffic_timeline', 'acquisition_channels', and 'conversions'.

	Raises:
		frappe.ValidationError: If GA4 settings are not configured or credentials file is missing.
		Exception: If authentication or the GA4 API call fails.
	"""
	ga4_settings = frappe.get_doc("GA4 Settings")

	if not ga4_settings.ga4_property_id:
		return {"error": "GA4 Property ID is missing in GA4 Settings."}

	if not ga4_settings.credentials_json:
		return {"error": "Credentials JSON file is missing in GA4 Settings."}

	credentials_url = ga4_settings.credentials_json
	if not credentials_url.startswith('/private/files/'):
		return {"error": "The Credentials JSON file must be uploaded as a Private file. Please re-upload it with 'Is Private' checked."}

	# Extract filename correctly
	credentials_file = credentials_url.split('/')[-1]
	credentials_path = frappe.get_site_path('private', 'files', credentials_file)

	if not os.path.exists(credentials_path):
		return {"error": f"Credentials file not found at: {credentials_path}"}

	try:
		# Use service account credentials explicitly to avoid modifying os.environ
		credentials = service_account.Credentials.from_service_account_file(credentials_path)
		client = BetaAnalyticsDataClient(credentials=credentials)

		property_id = ga4_settings.ga4_property_id
		date_range = DateRange(start_date="30daysAgo", end_date="today")

		def fetch_traffic():
			req = RunReportRequest(
				property=f"properties/{property_id}",
				dimensions=[Dimension(name="date")],
				metrics=[Metric(name="activeUsers"), Metric(name="sessions")],
				date_ranges=[date_range],
			)
			return client.run_report(req)

		def fetch_acquisition():
			req = RunReportRequest(
				property=f"properties/{property_id}",
				dimensions=[Dimension(name="sessionDefaultChannelGroup")],
				metrics=[Metric(name="sessions")],
				date_ranges=[date_range],
			)
			return client.run_report(req)

		def fetch_conversions():
			req = RunReportRequest(
				property=f"properties/{property_id}",
				dimensions=[Dimension(name="eventName")],
				metrics=[Metric(name="conversions")],
				date_ranges=[date_range],
			)
			return client.run_report(req)

		def fetch_top_pages():
			req = RunReportRequest(
				property=f"properties/{property_id}",
				dimensions=[Dimension(name="pageTitle")],
				metrics=[Metric(name="screenPageViews")],
				date_ranges=[date_range],
				order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
				limit=10,
			)
			return client.run_report(req)

		def fetch_device_breakdown():
			req = RunReportRequest(
				property=f"properties/{property_id}",
				dimensions=[Dimension(name="deviceCategory")],
				metrics=[Metric(name="sessions")],
				date_ranges=[date_range],
			)
			return client.run_report(req)

		def fetch_user_geography():
			req = RunReportRequest(
				property=f"properties/{property_id}",
				dimensions=[Dimension(name="country")],
				metrics=[Metric(name="activeUsers")],
				date_ranges=[date_range],
				order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="activeUsers"), desc=True)],
				limit=10,
			)
			return client.run_report(req)

		with concurrent.futures.ThreadPoolExecutor() as executor:
			future_traffic = executor.submit(fetch_traffic)
			future_acq = executor.submit(fetch_acquisition)
			future_conv = executor.submit(fetch_conversions)
			future_pages = executor.submit(fetch_top_pages)
			future_device = executor.submit(fetch_device_breakdown)
			future_geo = executor.submit(fetch_user_geography)

			res_traffic = future_traffic.result()
			res_acq = future_acq.result()
			res_conv = future_conv.result()
			res_pages = future_pages.result()
			res_device = future_device.result()
			res_geo = future_geo.result()

		# Process 1. Traffic Timeline
		traffic_labels = []
		traffic_active_users = []
		traffic_sessions = []
		for row in res_traffic.rows:
			date_str = row.dimension_values[0].value
			try:
				parsed_date = datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")
			except ValueError:
				parsed_date = date_str
			traffic_labels.append(parsed_date)
			traffic_active_users.append(int(row.metric_values[0].value))
			traffic_sessions.append(int(row.metric_values[1].value))

		combined_traffic = sorted(
			zip(traffic_labels, traffic_active_users, traffic_sessions, strict=True)
		)
		if combined_traffic:
			traffic_labels, traffic_active_users, traffic_sessions = zip(*combined_traffic, strict=True)
			traffic_labels = list(traffic_labels)
			traffic_active_users = list(traffic_active_users)
			traffic_sessions = list(traffic_sessions)

		# Process 2. Acquisition Channels
		acq_labels = []
		acq_sessions = []
		for row in res_acq.rows:
			acq_labels.append(row.dimension_values[0].value)
			acq_sessions.append(int(row.metric_values[0].value))

		# Process 3. Conversions
		conv_labels = []
		conv_conversions = []
		for row in res_conv.rows:
			conv_labels.append(row.dimension_values[0].value)
			conv_conversions.append(int(row.metric_values[0].value))

		# Process 4. Top Pages
		top_pages = []
		for row in res_pages.rows:
			top_pages.append({
				"pageTitle": row.dimension_values[0].value,
				"screenPageViews": int(row.metric_values[0].value)
			})

		# Process 5. Device Breakdown
		device_labels = []
		device_sessions = []
		for row in res_device.rows:
			device_labels.append(row.dimension_values[0].value)
			device_sessions.append(int(row.metric_values[0].value))

		# Process 6. User Geography
		geo_labels = []
		geo_users = []
		for row in res_geo.rows:
			geo_labels.append(row.dimension_values[0].value)
			geo_users.append(int(row.metric_values[0].value))

		return {
			"traffic_timeline": {
				"labels": traffic_labels,
				"datasets": [
					{"name": "Active Users", "values": traffic_active_users},
					{"name": "Sessions", "values": traffic_sessions}
				]
			},
			"acquisition_channels": {
				"labels": acq_labels,
				"datasets": [{"name": "Sessions", "values": acq_sessions}]
			},
			"conversions": {
				"labels": conv_labels,
				"datasets": [{"name": "Conversions", "values": conv_conversions}]
			},
			"top_pages": top_pages,
			"device_breakdown": {
				"labels": device_labels,
				"datasets": [{"name": "Sessions", "values": device_sessions}]
			},
			"user_geography": {
				"labels": geo_labels,
				"datasets": [{"name": "Active Users", "values": geo_users}]
			}
		}

	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="GA4 API Error")
		return {"error": f"Failed to fetch GA4 data: {e!s}"}

#: Where the Search Console property form that last answered is remembered, so the nightly
#: pull does not spend a refused request on the wrong form every run.
GSC_SITE_CACHE_KEY = "ee_gsc_working_site"
GSC_SITE_CACHE_SECONDS = 86400

#: Statuses that mean "this property string is not one we may read". Tried against the next
#: candidate form rather than treated as the answer.
GSC_REFUSED = (401, 403, 404)


def _gsc_service(ga4_settings):
	"""``(service, service_account_email, error)`` for the configured Search Console access."""
	if not ga4_settings.credentials_json:
		return None, None, "Credentials JSON file is missing in GA4 Settings."
	credentials_url = ga4_settings.credentials_json
	if not credentials_url.startswith('/private/files/'):
		return None, None, "The Credentials JSON file must be uploaded as a Private file. Please re-upload it with 'Is Private' checked."
	credentials_path = frappe.get_site_path('private', 'files', credentials_url.split('/')[-1])
	if not os.path.exists(credentials_path):
		return None, None, f"Credentials file not found at: {credentials_path}"
	credentials = service_account.Credentials.from_service_account_file(credentials_path)
	return build("searchconsole", "v1", credentials=credentials), credentials.service_account_email, None


def _http_status(error):
	status = getattr(getattr(error, "resp", None), "status", None)
	try:
		return int(status)
	except (TypeError, ValueError):
		return None


def _gsc_query_first_site(service, stored, body):
	"""Run ``body`` against the first property form Search Console accepts.

	Returns ``(site, response, refusals)``; ``site`` is None when every form was refused,
	and ``refusals`` lists ``(site, status)`` for each form that was. Any other error
	raises. The accepted form is cached for a day and tried first next time.
	"""
	candidates = site_candidates(stored)
	cached = frappe.cache().get_value(GSC_SITE_CACHE_KEY)
	if cached in candidates:
		candidates = [cached] + [c for c in candidates if c != cached]
	refusals = []
	for site in candidates:
		try:
			response = service.searchanalytics().query(siteUrl=site, body=body).execute()
		except HttpError as e:
			status = _http_status(e)
			if status in GSC_REFUSED:
				refusals.append((site, status))
				continue
			raise
		frappe.cache().set_value(GSC_SITE_CACHE_KEY, site, expires_in_sec=GSC_SITE_CACHE_SECONDS)
		return site, response, refusals
	return None, None, refusals


def _gsc_refused_message(stored, refusals, account):
	tried = ", ".join(f"{site} ({status})" for site, status in refusals) or "no usable form"
	return (
		f"Search Console refused every form of {stored!r}: {tried}. "
		f"Add {account or 'the GA4 service account'} as a user on the property in Search Console "
		f"(Settings > Users and permissions), then re-run."
	)


@frappe.whitelist()
def get_gsc_data():
	"""
	Retrieves Google Search Console (GSC) 'clicks' and 'impressions' for Search Performance Timeline,
	and 'clicks', 'impressions', 'ctr', and 'position' for Top Queries for the past 30 days.
	It fetches the property URL and credentials file path from the 'GA4 Settings' Single DocType.

	The stored property is tried in every form Search Console accepts
	(``utils/search_console.site_candidates``): prod stores the bare ``sapphirefountains.com``,
	which Google reads as the single URL prefix ``http://sapphirefountains.com/``, and a
	property that does not exist is refused with the same 403 as a missing grant
	(TASK-2026-01474).

	Returns:
		dict: A dictionary containing 'search_timeline' (formatted for Frappe Charts), 'top_queries'
		(formatted for a DataTable), 'top_pages', and 'property' (the form Search Console accepted).
	"""
	ga4_settings = frappe.get_doc("GA4 Settings")

	if not ga4_settings.gsc_property_url:
		return {"error": "GSC Property URL is missing in GA4 Settings."}

	service, account, error = _gsc_service(ga4_settings)
	if error:
		return {"error": error}

	try:
		today = dt.date.today()
		start, end = window_for(today)
		start_date, end_date = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

		site, response_timeline, refusals = _gsc_query_first_site(
			service,
			ga4_settings.gsc_property_url,
			{"startDate": start_date, "endDate": end_date, "dimensions": ["date"], "rowLimit": 31},
		)
		if not site:
			# Permanent, and the traceback says nothing the operator needs. Throttled because
			# the nightly pull would otherwise re-log it every run: it once buried 34 rows in
			# the log for one unchanging fact.
			message = _gsc_refused_message(ga4_settings.gsc_property_url, refusals, account)
			log_error_throttled(message, "GSC API Error", key="refused")
			return {"error": message}

		def fetch_dimension(dimension):
			return service.searchanalytics().query(
				siteUrl=site,
				body={"startDate": start_date, "endDate": end_date, "dimensions": [dimension], "rowLimit": 15},
			).execute()

		# GSC API has no orderBy for searchanalytics.query: rows come back grouped by the
		# dimension and ordered by clicks descending, which is the order wanted here.
		with concurrent.futures.ThreadPoolExecutor() as executor:
			future_keywords = executor.submit(fetch_dimension, "query")
			future_pages = executor.submit(fetch_dimension, "page")
			response_keywords = future_keywords.result()
			response_pages = future_pages.result()

		# 1. Timeline Data (Clicks and Impressions by Date)
		timeline_labels = []
		timeline_clicks = []
		timeline_impressions = []

		rows = response_timeline.get("rows", [])
		for row in rows:
			timeline_labels.append(row["keys"][0])
			timeline_clicks.append(row["clicks"])
			timeline_impressions.append(row["impressions"])

		combined_timeline = sorted(
			zip(timeline_labels, timeline_clicks, timeline_impressions, strict=True)
		)
		if combined_timeline:
			timeline_labels, timeline_clicks, timeline_impressions = zip(*combined_timeline, strict=True)
			timeline_labels = list(timeline_labels)
			timeline_clicks = list(timeline_clicks)
			timeline_impressions = list(timeline_impressions)

		# 2. Keyword Data (Top 15 Queries)
		top_queries = []
		kw_rows = response_keywords.get("rows", [])
		for row in kw_rows:
			top_queries.append({
				"query": row["keys"][0],
				"clicks": row["clicks"],
				"impressions": row["impressions"],
				"ctr": round(row["ctr"] * 100, 2), # Convert to percentage
				"position": round(row["position"], 1)
			})

		# 3. Landing Pages Data (Top 15 URLs)
		top_pages = []
		page_rows = response_pages.get("rows", [])
		for row in page_rows:
			top_pages.append({
				"page": row["keys"][0],
				"clicks": row["clicks"],
				"impressions": row["impressions"],
				"ctr": round(row["ctr"] * 100, 2),
				"position": round(row["position"], 1)
			})

		return {
			"search_timeline": {
				"labels": timeline_labels,
				"datasets": [
					{
						"name": "Clicks",
						"values": timeline_clicks
					},
					{
						"name": "Impressions",
						"values": timeline_impressions
					}
				]
			},
			"top_queries": top_queries,
			"top_pages": top_pages,
			"property": site,
		}

	except HttpError as e:
		status = _http_status(e)
		log_error_throttled(frappe.get_traceback(), "GSC API Error", key=str(status))
		return {"error": f"Failed to fetch GSC data: {e!s}"}

	except Exception as e:
		log_error_throttled(frappe.get_traceback(), "GSC API Error")
		return {"error": f"Failed to fetch GSC data: {e!s}"}


def backfill_gsc_snapshots(since=None, dry_run=False):
	"""Fill the organic figures on every Marketing Web Snapshot that Search Console failed.

	``bench --site <site> execute erpnext_enhancements.api.analytics.backfill_gsc_snapshots``
	(``--kwargs "{'dry_run': 1}"`` to see what it would write first). Not whitelisted.

	From 2026-06-26 every nightly pull recorded ``gsc_ok = 0`` and organic clicks and
	impressions of zero or blank -- a figure that read as a business fact and was not one.
	Search Console keeps 16 months of data, so the history is recoverable: one query for
	daily clicks and impressions across the whole gap, then each night's rolling 30-day sum
	(the same window the live pull uses) computed locally. Only snapshots with
	``gsc_ok = 0`` are touched, so a night that succeeded is never overwritten, and running
	it twice is harmless.

	Run it after the property grant is fixed; until then it reports the same refusal the
	nightly pull does and writes nothing.
	"""
	from frappe.utils import getdate

	filters = {"gsc_ok": 0}
	if since:
		filters["snapshot_date"] = [">=", getdate(since)]
	snapshots = frappe.get_all(
		"Marketing Web Snapshot",
		filters=filters,
		fields=["name", "snapshot_date", "source_status", "pull_error"],
		order_by="snapshot_date asc",
		limit_page_length=0,
	)
	if not snapshots:
		return {"updated": 0, "message": "No snapshot is missing Search Console data."}

	ga4_settings = frappe.get_doc("GA4 Settings")
	if not ga4_settings.gsc_property_url:
		return {"updated": 0, "error": "GSC Property URL is missing in GA4 Settings."}
	service, account, error = _gsc_service(ga4_settings)
	if error:
		return {"updated": 0, "error": error}

	days = [getdate(s.snapshot_date) for s in snapshots]
	start, _ = window_for(min(days))
	site, response, refusals = _gsc_query_first_site(
		service,
		ga4_settings.gsc_property_url,
		{
			"startDate": start.strftime("%Y-%m-%d"),
			"endDate": max(days).strftime("%Y-%m-%d"),
			"dimensions": ["date"],
			"rowLimit": 25000,
		},
	)
	if not site:
		return {"updated": 0, "error": _gsc_refused_message(ga4_settings.gsc_property_url, refusals, account)}

	daily = {row["keys"][0]: (row.get("clicks", 0), row.get("impressions", 0)) for row in response.get("rows", [])}
	sums = rolling_sums(daily, days)

	written = []
	for snap, day in zip(snapshots, days, strict=True):
		clicks, impressions = sums[day]
		written.append({"snapshot": snap.name, "organic_clicks_30": clicks, "organic_impressions_30": impressions})
		if dry_run:
			continue
		# snapshot_marketing_web appends GA4's error, then GSC's, joined by "; ". Cut at the
		# GSC one rather than splitting on every "; ": a message may contain one itself.
		kept = snap.pull_error or ""
		cut = 0 if kept.startswith("GSC") else kept.find("; GSC")
		if cut != -1:
			kept = kept[:cut]
		frappe.db.set_value(
			"Marketing Web Snapshot",
			snap.name,
			{
				"organic_clicks_30": clicks,
				"organic_impressions_30": impressions,
				"gsc_ok": 1,
				"source_status": (snap.source_status or "").replace("GSC ✗", "GSC ✓ (backfilled)")[:140],
				"pull_error": kept or None,
			},
			update_modified=False,
		)
	if not dry_run:
		frappe.db.commit()
	return {"property": site, "dry_run": bool(dry_run), "updated": len(written), "rows": written}
