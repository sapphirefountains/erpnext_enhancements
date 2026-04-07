import frappe
import os
import concurrent.futures
from datetime import datetime
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
	DateRange,
	Dimension,
	Metric,
	RunReportRequest,
)
from googleapiclient.discovery import build
import datetime as dt

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
		frappe.throw("GA4 Property ID is missing in GA4 Settings.")

	if not ga4_settings.credentials_json:
		frappe.throw("Credentials JSON file is missing in GA4 Settings.")

	credentials_url = ga4_settings.credentials_json
	if not credentials_url.startswith('/private/files/'):
		frappe.throw("The Credentials JSON file must be uploaded as a Private file. Please re-upload it with 'Is Private' checked.")

	# Extract filename correctly
	credentials_file = credentials_url.split('/')[-1]
	credentials_path = frappe.get_site_path('private', 'files', credentials_file)

	if not os.path.exists(credentials_path):
		frappe.throw(f"Credentials file not found at: {credentials_path}")

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

		combined_traffic = sorted(zip(traffic_labels, traffic_active_users, traffic_sessions))
		if combined_traffic:
			traffic_labels, traffic_active_users, traffic_sessions = zip(*combined_traffic)
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
		frappe.throw(f"Failed to fetch GA4 data: {str(e)}")

@frappe.whitelist()
def get_gsc_data():
	"""
	Retrieves Google Search Console (GSC) 'clicks' and 'impressions' for Search Performance Timeline,
	and 'clicks', 'impressions', 'ctr', and 'position' for Top Queries for the past 30 days.
	It fetches the property URL and credentials file path from the 'GA4 Settings' Single DocType.

	Returns:
		dict: A dictionary containing 'search_timeline' (formatted for Frappe Charts) and 'top_queries' (formatted for a DataTable).

	Raises:
		frappe.ValidationError: If GSC settings are not configured or credentials file is missing.
		Exception: If authentication or the GSC API call fails.
	"""
	ga4_settings = frappe.get_doc("GA4 Settings")

	if not ga4_settings.gsc_property_url:
		frappe.throw("GSC Property URL is missing in GA4 Settings.")

	if not ga4_settings.credentials_json:
		frappe.throw("Credentials JSON file is missing in GA4 Settings.")

	credentials_url = ga4_settings.credentials_json
	if not credentials_url.startswith('/private/files/'):
		frappe.throw("The Credentials JSON file must be uploaded as a Private file. Please re-upload it with 'Is Private' checked.")

	credentials_file = credentials_url.split('/')[-1]
	credentials_path = frappe.get_site_path('private', 'files', credentials_file)

	if not os.path.exists(credentials_path):
		frappe.throw(f"Credentials file not found at: {credentials_path}")

	try:
		credentials = service_account.Credentials.from_service_account_file(credentials_path)
		service = build("searchconsole", "v1", credentials=credentials)

		today = dt.date.today()
		start_date = (today - dt.timedelta(days=30)).strftime("%Y-%m-%d")
		end_date = today.strftime("%Y-%m-%d")

		def fetch_timeline():
			request_timeline = {
				"startDate": start_date,
				"endDate": end_date,
				"dimensions": ["date"],
				"rowLimit": 31
			}
			return service.searchanalytics().query(
				siteUrl=ga4_settings.gsc_property_url,
				body=request_timeline
			).execute()

		def fetch_keywords():
			request_keywords = {
				"startDate": start_date,
				"endDate": end_date,
				"dimensions": ["query"],
				"rowLimit": 15
			}
			return service.searchanalytics().query(
				siteUrl=ga4_settings.gsc_property_url,
				body=request_keywords
			).execute()

		def fetch_landing_pages():
			request_pages = {
				"startDate": start_date,
				"endDate": end_date,
				"dimensions": ["page"],
				"rowLimit": 15
			}
			# GSC API doesn't have an orderBy parameter for searchanalytics.query directly via API.
			# By default, rows are grouped by dimensions and ordered by clicks descending.
			return service.searchanalytics().query(
				siteUrl=ga4_settings.gsc_property_url,
				body=request_pages
			).execute()

		with concurrent.futures.ThreadPoolExecutor() as executor:
			future_timeline = executor.submit(fetch_timeline)
			future_keywords = executor.submit(fetch_keywords)
			future_pages = executor.submit(fetch_landing_pages)

			response_timeline = future_timeline.result()
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

		combined_timeline = sorted(zip(timeline_labels, timeline_clicks, timeline_impressions))
		if combined_timeline:
			timeline_labels, timeline_clicks, timeline_impressions = zip(*combined_timeline)
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
			"top_pages": top_pages
		}

	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="GSC API Error")
		frappe.throw(f"Failed to fetch GSC data: {str(e)}")
