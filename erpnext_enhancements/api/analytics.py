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

@frappe.whitelist()
def get_ga4_data():
	"""
	Retrieves Google Analytics 4 (GA4) 'activeUsers' metric for the past 30 days.
	It fetches the property ID and credentials file path from the 'GA4 Settings' Single DocType.

	Returns:
		dict: A dictionary formatted for Frappe Charts, containing 'labels' and 'datasets'.

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

		request = RunReportRequest(
			property=f"properties/{ga4_settings.ga4_property_id}",
			dimensions=[Dimension(name="date")],
			metrics=[Metric(name="activeUsers")],
			date_ranges=[DateRange(start_date="30daysAgo", end_date="today")],
		)

		response = client.run_report(request)

		# Process the response
		labels = []
		data = []

		# GA4 returns dates like '20230520'
		for row in response.rows:
			date_str = row.dimension_values[0].value
			try:
				parsed_date = datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")
			except ValueError:
				parsed_date = date_str
			labels.append(parsed_date)
			data.append(int(row.metric_values[0].value))

		# Sort by date
		combined = sorted(zip(labels, data))
		if combined:
			labels, data = zip(*combined)
			labels = list(labels)
			data = list(data)

		return {
			"labels": labels,
			"datasets": [
				{
					"name": "Active Users",
					"values": data
				}
			]
		}

	except Exception as e:
		frappe.log_error(message=frappe.get_traceback(), title="GA4 API Error")
		frappe.throw(f"Failed to fetch GA4 data: {str(e)}")
