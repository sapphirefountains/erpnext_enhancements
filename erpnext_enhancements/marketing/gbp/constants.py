# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Google Business Profile: hosts, the read-only allowlist, OAuth, and the metrics read.

Checked against Google's references on 2026-10-29:

* **Performance API v1** ``locations/{id}:fetchMultiDailyMetricsTimeSeries`` (a GET) for the
  daily figures. The legacy v4 ``reportInsights`` was discontinued on 2023-03-30.
* **Account Management v1** ``accounts`` and **Business Information v1**
  ``accounts/{a}/locations`` (``readMask`` is required) to find the listing.
* **Reviews are still the legacy My Business v4** ``accounts/{a}/locations/{l}/reviews``
  (``pageSize`` at most 50). It is not on Google's sunset list.

**There is no read-only scope.** ``business.manage`` is the only one Google offers (the older
``plus.business.manage`` is deprecated), and it can edit the listing and reply to reviews. As with
Google Ads, read-only is enforced twice instead: by ``ALLOWLIST`` below, which holds GETs only
and which ``core/client.py`` checks before a request leaves, and by authorizing as a Google user
whose role on the profile is the least that can read it. The scope is confined to this file.

**API access is gated by Google.** A project starts with a quota of 0 requests a minute and gets
300 once Google approves it; the profile must be verified, active for 60 days and list a website.
"""

import re

PLATFORM_GBP = "Google Business Profile"

#: Master switch AND this one. Ships 0.
ENABLED_FLAG = "google_business_profile_enabled"

#: Field prefix on Marketing Connections; disjoint from the ad and publishing prefixes.
CREDENTIAL_PREFIX = {PLATFORM_GBP: "google_business"}

PERFORMANCE_HOST = "businessprofileperformance.googleapis.com"
ACCOUNTS_HOST = "mybusinessaccountmanagement.googleapis.com"
INFORMATION_HOST = "mybusinessbusinessinformation.googleapis.com"
REVIEWS_HOST = "mybusiness.googleapis.com"

PERFORMANCE_BASE = f"https://{PERFORMANCE_HOST}/v1"
ACCOUNTS_BASE = f"https://{ACCOUNTS_HOST}/v1"
INFORMATION_BASE = f"https://{INFORMATION_HOST}/v1"
REVIEWS_BASE = f"https://{REVIEWS_HOST}/v4"

OAUTH = {
	PLATFORM_GBP: {
		"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
		"token_url": "https://oauth2.googleapis.com/token",
		"revoke_url": "https://oauth2.googleapis.com/revoke",
		"scopes": ("https://www.googleapis.com/auth/business.manage",),
		"scope_separator": " ",
		"extra_authorize_params": {"access_type": "offline", "prompt": "consent"},
	},
}

#: (METHOD, host, path regex). GET only: nothing here can edit the listing or answer a review.
ALLOWLIST = tuple(
	(method, host, re.compile(pattern))
	for method, host, pattern in (
		("GET", ACCOUNTS_HOST, r"^/v1/accounts$"),
		("GET", INFORMATION_HOST, r"^/v1/accounts/(\d+|-)/locations$"),
		("GET", PERFORMANCE_HOST, r"^/v1/locations/\d+:fetchMultiDailyMetricsTimeSeries$"),
		("GET", REVIEWS_HOST, r"^/v4/accounts/\d+/locations/\d+/reviews$"),
	)
)

#: The daily metrics read, and the column each lands in. Desktop and mobile are summed.
DAILY_METRICS = {
	"CALL_CLICKS": "call_clicks",
	"WEBSITE_CLICKS": "website_clicks",
	"BUSINESS_DIRECTION_REQUESTS": "direction_requests",
	"BUSINESS_IMPRESSIONS_DESKTOP_MAPS": "maps_impressions",
	"BUSINESS_IMPRESSIONS_MOBILE_MAPS": "maps_impressions",
	"BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": "search_impressions",
	"BUSINESS_IMPRESSIONS_MOBILE_SEARCH": "search_impressions",
}
COLUMNS = ("call_clicks", "website_clicks", "direction_requests", "maps_impressions", "search_impressions")

#: How far back the history backfill asks for. Google documents 18 months only for the retired v4
#: insights ("The maximum range is 18 months from the request date"); the v1 Performance API names no
#: limit, so 18 months is the request and a refusal of the oldest windows is skipped, not fatal
#: (``sync.history_pull``). Checked 2026-10-29.
HISTORY_MONTHS = 18
#: One Performance API call's span. v1 documents no maximum; a month keeps every answer small.
MAX_RANGE_DAYS = 31

#: The listing's figures settle over a few days, like the ad platforms'.
RESTATE_DAYS = 7
#: The first pull reaches this far back. The Performance API serves about 18 months.
INITIAL_DAYS = 90
REVIEWS_PAGE_SIZE = 50
#: Reviews are read in full each night; this caps a runaway page loop.
REVIEWS_MAX_PAGES = 40
LOCATIONS_READ_MASK = "name,title"

QUEUE = "long"
JOB_ID = "marketing-business-profile-sync"
TIMEOUT_SECONDS = 20 * 60
