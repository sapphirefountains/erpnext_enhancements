# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The publishing networks and the Marketing Settings switch for each (TASK-2026-01479).

The four networks are decision 2 (2026-08-13): Facebook, Instagram, LinkedIn and YouTube,
deliberately excluding X (paid API tier), TikTok (audit) and Pinterest.

They are named apart from the ad platforms in ``core/constants.py`` even where the vendor is
the same. *Meta Ads* and *Facebook* are one company, but reading a campaign's spend and
posting to the Page are different permissions, reviewed separately and switched separately,
so a switch, a credential prefix or a log line must never be able to mean both.
``tests/test_marketing_publishing.py`` keeps the two sets disjoint.

Standard library only (plus the API versions from ``core/constants.py``), so the gate and the
allowlist are tested without a bench.
"""

import re

from erpnext_enhancements.marketing.core import constants as C

NETWORK_FACEBOOK = "Facebook"
NETWORK_INSTAGRAM = "Instagram"
NETWORK_LINKEDIN = "LinkedIn"
NETWORK_YOUTUBE = "YouTube"
PUBLISH_NETWORKS = (NETWORK_FACEBOOK, NETWORK_INSTAGRAM, NETWORK_LINKEDIN, NETWORK_YOUTUBE)

#: Per-network publishing switch on Marketing Settings. Each ships ``0``.
PUBLISH_FLAG = {
	NETWORK_FACEBOOK: "facebook_publishing_enabled",
	NETWORK_INSTAGRAM: "instagram_publishing_enabled",
	NETWORK_LINKEDIN: "linkedin_publishing_enabled",
	NETWORK_YOUTUBE: "youtube_publishing_enabled",
}

# ---------------------------------------------------------------- connections (TASK-2026-01480)

#: One OAuth connection per platform account, which is not one per network: Facebook and
#: Instagram publish through the same Meta Page token (the Instagram professional account is
#: reached through the Page it is linked to).
CONNECTION_META = "Meta Publishing"
CONNECTION_LINKEDIN = "LinkedIn Publishing"
CONNECTION_YOUTUBE = "YouTube Publishing"
PUBLISH_CONNECTIONS = (CONNECTION_META, CONNECTION_LINKEDIN, CONNECTION_YOUTUBE)

#: Which connection carries each network's token.
CONNECTION_FOR = {
	NETWORK_FACEBOOK: CONNECTION_META,
	NETWORK_INSTAGRAM: CONNECTION_META,
	NETWORK_LINKEDIN: CONNECTION_LINKEDIN,
	NETWORK_YOUTUBE: CONNECTION_YOUTUBE,
}

#: Field prefix on Marketing Connections. Disjoint from the ad platforms' prefixes
#: (``core/constants.CREDENTIAL_PREFIX``), so no field can hold both kinds of token.
CREDENTIAL_PREFIX = {
	CONNECTION_META: "meta_publishing",
	CONNECTION_LINKEDIN: "linkedin_publishing",
	CONNECTION_YOUTUBE: "youtube_publishing",
}

#: Read-only fields the Connect flow fills in to say *what* is connected. Cleared on
#: Disconnect; kept when a token dies, so the form still says which Page needs reconnecting.
IDENTITY_FIELDS = {
	CONNECTION_META: ("page_name", "instagram_user_id", "instagram_username"),
	CONNECTION_LINKEDIN: ("organization_name",),
	CONNECTION_YOUTUBE: ("channel_id", "channel_title"),
}

# ---------------------------------------------------------------- OAuth

GOOGLE_SCOPE_BASE = "https://www.googleapis.com/auth"

#: Meta permissions each network cannot publish without, checked against what the person
#: actually granted (``GET /me/permissions``) before any token is kept. Meta's consent dialog
#: lets a person untick permissions, so "requested" is not "granted".
META_REQUIRED = {
	NETWORK_FACEBOOK: frozenset({"pages_show_list", "pages_manage_posts", "pages_read_engagement"}),
	NETWORK_INSTAGRAM: frozenset({"instagram_basic", "instagram_content_publish", "pages_read_engagement"}),
}

#: Meta's Page role to post as the Page.
META_POSTING_TASK = "CREATE_CONTENT"

#: LinkedIn's organization role this connection requires. ADMINISTRATOR is the one role that
#: can both post (``w_organization_social``) and read share statistics (``rw_organization_admin``),
#: which the metrics pull (TASK-2026-01488) needs.
LINKEDIN_ORG_ROLE = "ADMINISTRATOR"

#: Same shape as ``core/constants.OAUTH``. Decided 2026-09-22 (Nik):
#:
#: * **Instagram publishes through the Facebook Page**, one Meta connection for both
#:   networks, as the approvals packet files it. ``ads_read`` is requested because Meta
#:   requires an ads permission when the person's Page role comes through Business Manager;
#:   it is read-only. ``ads_management`` never is, even though some Meta pages say Business
#:   Manager roles need it too. If Meta enforces that, Instagram fails with a named
#:   permission error, and the fix is Instagram Login, not a spend-capable scope.
#: * **YouTube may manage playlists**, so it asks for ``youtube.force-ssl``, which already
#:   covers upload, thumbnails, playlists and reading the channel. A narrower
#:   ``youtube.upload`` alongside it would add nothing.
#: * ``read_insights``, ``instagram_manage_insights``, ``rw_organization_admin`` and
#:   ``yt-analytics.readonly`` are for the metrics pull (TASK-2026-01488), requested now so
#:   turning metrics on does not mean reconnecting every account.
PUBLISH_OAUTH = {
	CONNECTION_META: {
		"authorize_url": f"https://www.facebook.com/{C.META_API_VERSION}/dialog/oauth",
		"token_url": f"{C.META_GRAPH_BASE}/oauth/access_token",
		"revoke_url": None,
		"scopes": (
			"pages_show_list",
			"pages_manage_posts",
			"pages_read_engagement",
			"read_insights",
			"instagram_basic",
			"instagram_content_publish",
			"instagram_manage_insights",
			"ads_read",
		),
		"scope_separator": ",",
		"extra_authorize_params": {},
	},
	CONNECTION_LINKEDIN: {
		"authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
		"token_url": "https://www.linkedin.com/oauth/v2/accessToken",
		"revoke_url": None,
		"scopes": ("w_organization_social", "r_organization_social", "rw_organization_admin"),
		"scope_separator": " ",
		"extra_authorize_params": {},
	},
	CONNECTION_YOUTUBE: {
		"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
		"token_url": "https://oauth2.googleapis.com/token",
		"revoke_url": "https://oauth2.googleapis.com/revoke",
		"scopes": (f"{GOOGLE_SCOPE_BASE}/youtube.force-ssl", f"{GOOGLE_SCOPE_BASE}/yt-analytics.readonly"),
		"scope_separator": " ",
		# As for Google Ads: offline + consent is what returns a refresh token on every connect.
		"extra_authorize_params": {"access_type": "offline", "prompt": "consent"},
	},
}

# ---------------------------------------------------------------- the publishing allowlist

GRAPH_HOST = "graph.facebook.com"
LINKEDIN_HOST = "api.linkedin.com"
YOUTUBE_HOST = "www.googleapis.com"

_V = re.escape(C.META_API_VERSION)

#: (connection, METHOD, host, compiled path regex) for every call ``publish/client.py`` may
#: send. It holds only the identity reads the Connect flow and the daily token check need;
#: each publisher (TASK-2026-01483 to 01485) adds its own writes. **No ad endpoint may ever
#: appear here** -- no ``act_``, ``adAccounts``, ``adCampaigns``, ``adAnalytics``, and not the
#: Google Ads host -- which ``tests/test_marketing_publish_oauth.py`` enforces.
PUBLISH_ALLOWLIST = tuple(
	(connection, method, host, re.compile(pattern))
	for connection, method, host, pattern in (
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/me/permissions$"),
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/me/accounts$"),
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/\d+$"),
		(CONNECTION_LINKEDIN, "GET", LINKEDIN_HOST, r"^/rest/organizationAcls$"),
		(CONNECTION_LINKEDIN, "GET", LINKEDIN_HOST, r"^/rest/organizations/\d+$"),
		(CONNECTION_YOUTUBE, "GET", YOUTUBE_HOST, r"^/youtube/v3/channels$"),
	)
)

YOUTUBE_API_BASE = f"https://{YOUTUBE_HOST}/youtube/v3"

# ---------------------------------------------------------------- token upkeep

#: LinkedIn access tokens are refreshed this close to expiry (same as the ad connector).
LINKEDIN_REFRESH_WITHIN_DAYS = 7

#: The form and the daily check start warning this long before a token nobody can refresh
#: runs out: LinkedIn's refresh token (365 days, not rolling), or a LinkedIn access token
#: when LinkedIn issued no refresh token at all.
EXPIRY_WARNING_DAYS = 30

# ---------------------------------------------------------------- rate limits (TASK-2026-01482)
# Checked 2026-09-22. The task text carried older figures (25 IG posts; 1,600 units per
# upload); both had moved. Read live figures where the platform offers them.

#: Instagram posts per rolling 24 hours per account, until the Meta publisher has read the
#: live figure from content_publishing_limit. Meta's docs say 100 and 50 in different places;
#: 25 is the most conservative figure ever published, so the fallback cannot overshoot.
INSTAGRAM_POSTS_PER_DAY_FALLBACK = 25

#: YouTube: videos.insert has its own bucket of 100 calls a day, and thumbnails.set and
#: playlistItems.insert cost about 50 units each from the 10,000-unit general budget. Both
#: reset at midnight Pacific.
YOUTUBE_UPLOADS_PER_DAY = 100
YOUTUBE_UNITS_PER_DAY = 10_000
YOUTUBE_UPLOAD_EXTRA_UNITS = 100  # one thumbnail + one playlist add per upload

#: Meta usage headers: pause the connection when any figure reaches this percent, for this
#: long, unless Meta names its own time to regain access.
META_USAGE_PAUSE_PERCENT = 90
META_DEFAULT_PAUSE_SECONDS = 15 * 60

#: A 429 without a Retry-After pauses the connection this long. No pause is ever longer than
#: a day, whatever a header says.
DEFAULT_429_PAUSE_SECONDS = 60
MAX_PAUSE_SECONDS = 86400
