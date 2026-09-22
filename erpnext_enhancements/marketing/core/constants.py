# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Static configuration for the ad-spend connectors (TASK-2026-01476).

Versions, hosts, OAuth endpoints and scopes, and -- the part that matters most -- the
**read-only allowlist**. ``core/client.py`` refuses any request whose method and path are
not listed here, so "no automation may touch spend" (decision 3, 2026-08-13) is enforced
by the transport rather than by everybody remembering. ``tests/test_marketing_connectors.py``
fails the build if a mutate-shaped path ever appears in this file or anywhere under
``marketing/``.

**Versions were checked on 2026-09-22 and all three retire on a schedule.** Google Ads v25
(released 2026-07-22, sunsets 2027-08); Meta Graph/Marketing API v26.0 (released
2026-07-29; v24.0 is the oldest still served); LinkedIn Marketing ``202608`` (each monthly
version is served for at least a year). A retired version fails every call with a 4xx that
reads like a credentials problem -- bump these first when a connector that worked starts
refusing.
"""

PLATFORM_GOOGLE = "Google Ads"
PLATFORM_META = "Meta Ads"
PLATFORM_LINKEDIN = "LinkedIn Ads"
PLATFORMS = (PLATFORM_GOOGLE, PLATFORM_META, PLATFORM_LINKEDIN)

SETTINGS_DOCTYPE = "Marketing Settings"
CONNECTIONS_DOCTYPE = "Marketing Connections"

#: Per-platform switch on Marketing Settings.
ENABLED_FLAG = {
	PLATFORM_GOOGLE: "google_ads_enabled",
	PLATFORM_META: "meta_ads_enabled",
	PLATFORM_LINKEDIN: "linkedin_ads_enabled",
}

#: Field prefix on Marketing Connections.
CREDENTIAL_PREFIX = {
	PLATFORM_GOOGLE: "google_ads",
	PLATFORM_META: "meta",
	PLATFORM_LINKEDIN: "linkedin",
}

# ---------------------------------------------------------------- versions

GOOGLE_ADS_API_VERSION = "v25"
META_API_VERSION = "v26.0"
LINKEDIN_API_VERSION = "202608"

GOOGLE_ADS_BASE = f"https://googleads.googleapis.com/{GOOGLE_ADS_API_VERSION}"
META_GRAPH_BASE = f"https://graph.facebook.com/{META_API_VERSION}"
LINKEDIN_REST_BASE = "https://api.linkedin.com/rest"

# ---------------------------------------------------------------- OAuth

#: The one scope Google Ads offers is full access. Read-only is enforced twice instead:
#: by READ_ONLY_ALLOWLIST below, and by authorizing as a Google user whose access level in
#: the Ads account is "Read only" (docs/marketing-connectors-runbook.md).
OAUTH = {
	PLATFORM_GOOGLE: {
		"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
		"token_url": "https://oauth2.googleapis.com/token",
		"revoke_url": "https://oauth2.googleapis.com/revoke",
		"scopes": ("https://www.googleapis.com/auth/adwords",),
		"scope_separator": " ",
		# access_type=offline + prompt=consent is what makes Google return a refresh token
		# on every connect, including a reconnect.
		"extra_authorize_params": {"access_type": "offline", "prompt": "consent"},
	},
	PLATFORM_META: {
		"authorize_url": f"https://www.facebook.com/{META_API_VERSION}/dialog/oauth",
		"token_url": f"{META_GRAPH_BASE}/oauth/access_token",
		"revoke_url": None,
		# ads_read is read-only by definition: insights and campaign structure, no writes.
		"scopes": ("ads_read",),
		"scope_separator": ",",
		"extra_authorize_params": {},
	},
	PLATFORM_LINKEDIN: {
		"authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
		"token_url": "https://www.linkedin.com/oauth/v2/accessToken",
		"revoke_url": None,
		# r_ads: read ad accounts and campaigns; r_ads_reporting: read analytics. Neither
		# can create, edit or fund anything.
		"scopes": ("r_ads", "r_ads_reporting"),
		"scope_separator": " ",
		"extra_authorize_params": {},
	},
}

#: How long a minted OAuth ``state`` is honored.
OAUTH_STATE_TTL_SECONDS = 600

#: Meta long-lived user tokens last ~60 days and cannot be refreshed without the user.
#: The status page warns inside this many days of expiry.
TOKEN_EXPIRY_WARNING_DAYS = 10

# ---------------------------------------------------------------- read-only allowlist

#: (platform, METHOD, path regex) for every data call the connectors may make. Anything
#: else -- in particular Google's ``:mutate`` endpoints and any Meta/LinkedIn POST --
#: is refused before it leaves the process. OAuth token exchange lives in core/oauth.py
#: and has its own, separate list.
#:
#: Google's ``googleAds:search`` is a POST, but it is a query: GAQL is SELECT-only and
#: the endpoint cannot write.
READ_ONLY_ALLOWLIST = (
	(PLATFORM_GOOGLE, "GET", r"^/customers:listAccessibleCustomers$"),
	(PLATFORM_GOOGLE, "POST", r"^/customers/\d+/googleAds:search$"),
	(PLATFORM_META, "GET", r"^/me/adaccounts$"),
	(PLATFORM_META, "GET", r"^/act_\d+/campaigns$"),
	(PLATFORM_META, "GET", r"^/act_\d+/insights$"),
	(PLATFORM_LINKEDIN, "GET", r"^/adAccounts$"),
	(PLATFORM_LINKEDIN, "GET", r"^/adAccounts/\d+/adCampaigns$"),
	(PLATFORM_LINKEDIN, "GET", r"^/adAnalytics$"),
)

#: Hosts each platform's data calls may reach. A pagination URL handed back by the API
#: is followed only if it stays on these.
DATA_HOSTS = {
	PLATFORM_GOOGLE: "googleads.googleapis.com",
	PLATFORM_META: "graph.facebook.com",
	PLATFORM_LINKEDIN: "api.linkedin.com",
}

#: Path prefix stripped before matching the allowlist (the version segment).
PATH_PREFIX = {
	PLATFORM_GOOGLE: f"/{GOOGLE_ADS_API_VERSION}",
	PLATFORM_META: f"/{META_API_VERSION}",
	PLATFORM_LINKEDIN: "/rest",
}

# ---------------------------------------------------------------- transport

#: The only statuses worth retrying. Every other 4xx is a request we got wrong or a
#: credential that will not get better by asking again, and retrying it just burns quota.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

#: Statuses that mean the stored credential is dead and a human has to reconnect.
AUTH_FAILURE_STATUSES = frozenset({401})

BACKOFF_BASE_SECONDS = 2
BACKOFF_CAP_SECONDS = 60
RETRY_AFTER_CAP_SECONDS = 120

#: Cap on one archived response body (Marketing Raw Payload.payload).
RAW_PAYLOAD_MAX_CHARS = 200_000

#: Query-string keys that must never be written anywhere: logs, raw payloads, errors.
SECRET_QUERY_KEYS = frozenset(
	{"access_token", "client_secret", "code", "refresh_token", "fb_exchange_token", "developer_token"}
)

# ---------------------------------------------------------------- sync

#: Google keeps click_view for 90 days. Beyond that a gclid can never be resolved.
CLICK_VIEW_LOOKBACK_DAYS = 90

#: Ad Click rows older than this are pruned unless a Lead carries the gclid.
CLICK_RETENTION_DAYS = 180

#: Nightly self-throttle: a second trigger inside this many hours is a no-op.
SYNC_MIN_INTERVAL_HOURS = 20

#: Background queue and the job id that stops two runs overlapping.
SYNC_QUEUE = "long"
SYNC_JOB_ID = "marketing-ad-spend-sync"
SYNC_TIMEOUT_SECONDS = 3600
