import frappe
import os
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

		# 1. Traffic Timeline
		req_traffic = RunReportRequest(
			property=f"properties/{ga4_settings.ga4_property_id}",
			dimensions=[Dimension(name="date")],
			metrics=[Metric(name="activeUsers"), Metric(name="sessions")],
			date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
		)
		res_traffic = client.run_report(req_traffic)

		traffic_labels = []
		traffic_active_users = []
		traffic_sessions = []

		# GA4 returns dates like '20230520'
		for row in res_traffic.rows:
			date_str = row.dimension_values[0].value
			try:
				parsed_date = datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")
			except ValueError:
				parsed_date = date_str
			traffic_labels.append(parsed_date)
			traffic_active_users.append(int(row.metric_values[0].value))
			traffic_sessions.append(int(row.metric_values[1].value))

		# Sort by date
		combined_traffic = sorted(zip(traffic_labels, traffic_active_users, traffic_sessions))
		if combined_traffic:
			traffic_labels, traffic_active_users, traffic_sessions = zip(*combined_traffic)
			traffic_labels = list(traffic_labels)
			traffic_active_users = list(traffic_active_users)
			traffic_sessions = list(traffic_sessions)

		# 2. Acquisition Channels
		req_acq = RunReportRequest(
			property=f"properties/{ga4_settings.ga4_property_id}",
			dimensions=[Dimension(name="sessionDefaultChannelGroup")],
			metrics=[Metric(name="sessions")],
			date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
		)
		res_acq = client.run_report(req_acq)

		acq_labels = []
		acq_sessions = []
		for row in res_acq.rows:
			acq_labels.append(row.dimension_values[0].value)
			acq_sessions.append(int(row.metric_values[0].value))

		# 3. Conversions
		req_conv = RunReportRequest(
			property=f"properties/{ga4_settings.ga4_property_id}",
			dimensions=[Dimension(name="eventName")],
			metrics=[Metric(name="conversions")],
			date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
		)
		res_conv = client.run_report(req_conv)

		conv_labels = []
		conv_conversions = []
		for row in res_conv.rows:
			conv_labels.append(row.dimension_values[0].value)
			conv_conversions.append(int(row.metric_values[0].value))

		return {
			"traffic_timeline": {
				"labels": traffic_labels,
				"datasets": [
					{
						"name": "Active Users",
						"values": traffic_active_users
					},
					{
						"name": "Sessions",
						"values": traffic_sessions
					}
				]
			},
			"acquisition_channels": {
				"labels": acq_labels,
				"datasets": [
					{
						"name": "Sessions",
						"values": acq_sessions
					}
				]
			},
			"conversions": {
				"labels": conv_labels,
				"datasets": [
					{
						"name": "Conversions",
						"values": conv_conversions
					}
				]
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

		# 1. Timeline Data (Clicks and Impressions by Date)
		request_timeline = {
			"startDate": start_date,
			"endDate": end_date,
			"dimensions": ["date"],
			"rowLimit": 31
		}

		response_timeline = service.searchanalytics().query(
			siteUrl=ga4_settings.gsc_property_url,
			body=request_timeline
		).execute()

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
		request_keywords = {
			"startDate": start_date,
			"endDate": end_date,
			"dimensions": ["query"],
			"rowLimit": 15
		}

		response_keywords = service.searchanalytics().query(
			siteUrl=ga4_settings.gsc_property_url,
			body=request_keywords
		).execute()

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
			"top_queries": top_queries
		}

	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="GSC API Error")
		frappe.throw(f"Failed to fetch GSC data: {str(e)}")
