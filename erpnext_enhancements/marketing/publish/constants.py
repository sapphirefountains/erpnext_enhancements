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
			# The first comment (TASK-2026-01483, decided 2026-09-22): Facebook needs
			# pages_manage_engagement (and the MODERATE task), Instagram instagram_manage_comments.
			# Neither can touch spend; both can also moderate the Page's comments.
			"pages_manage_engagement",
			"instagram_manage_comments",
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
#: send: the identity reads the Connect flow and the daily token check need, and each
#: publisher's own calls (Meta since TASK-2026-01483). **No ad endpoint may ever appear here**
#: -- no ``act_``, ``adAccounts``, ``adCampaigns``, ``adAnalytics``, and not the Google Ads host
#: -- and nothing here deletes; ``tests/test_marketing_connectors.py`` and
#: ``tests/test_marketing_meta_publisher.py`` enforce both. IDs are digits (a Facebook post is
#: ``{page-id}_{post-id}``), so a path can never name anything but a Page, post, photo, video,
#: Instagram account, container or media object.
PUBLISH_ALLOWLIST = tuple(
	(connection, method, host, re.compile(pattern))
	for connection, method, host, pattern in (
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/me/permissions$"),
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/me/accounts$"),
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/\d+(_\d+)?$"),
		# Facebook Page (TASK-2026-01483): photos are uploaded unpublished in prepare(), then one
		# feed post makes them public; a single video goes to /videos. Comments on our own post.
		(CONNECTION_META, "POST", GRAPH_HOST, rf"^/{_V}/\d+/feed$"),
		(CONNECTION_META, "POST", GRAPH_HOST, rf"^/{_V}/\d+/photos$"),
		(CONNECTION_META, "POST", GRAPH_HOST, rf"^/{_V}/\d+/videos$"),
		(CONNECTION_META, "POST", GRAPH_HOST, rf"^/{_V}/\d+(_\d+)?/comments$"),
		# Instagram: containers (non-public), then media_publish (the public step). GET .../media
		# lists recent media, to find a post a timed-out media_publish did in fact publish.
		(CONNECTION_META, "POST", GRAPH_HOST, rf"^/{_V}/\d+/media$"),
		(CONNECTION_META, "POST", GRAPH_HOST, rf"^/{_V}/\d+/media_publish$"),
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/\d+/media$"),
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/\d+/content_publishing_limit$"),
		(CONNECTION_LINKEDIN, "GET", LINKEDIN_HOST, r"^/rest/organizationAcls$"),
		(CONNECTION_LINKEDIN, "GET", LINKEDIN_HOST, r"^/rest/organizations/\d+$"),
		# LinkedIn publishing (TASK-2026-01484). Uploads are registered (initializeUpload), the
		# bytes PUT to the returned upload URL, and the asset read back until AVAILABLE -- all
		# before the post. URNs appear URL-encoded in paths (urn%3Ali%3Aimage%3A...).
		(CONNECTION_LINKEDIN, "POST", LINKEDIN_HOST, r"^/rest/(images|documents)$"),
		(
			CONNECTION_LINKEDIN,
			"GET",
			LINKEDIN_HOST,
			r"^/rest/(images|documents)/urn%3Ali%3A(image|document)%3A[\w-]+$",
		),
		(CONNECTION_LINKEDIN, "POST", LINKEDIN_HOST, r"^/rest/posts$"),
		(CONNECTION_LINKEDIN, "GET", LINKEDIN_HOST, r"^/rest/posts$"),
		(
			CONNECTION_LINKEDIN,
			"POST",
			LINKEDIN_HOST,
			r"^/rest/socialActions/urn%3Ali%3A(share|ugcPost)%3A\d+/comments$",
		),
		# Upload URLs are LinkedIn's own; the bearer token rides on these PUTs, so only these two.
		(CONNECTION_LINKEDIN, "PUT", "www.linkedin.com", r"^/dms-uploads/"),
		(CONNECTION_LINKEDIN, "PUT", LINKEDIN_HOST, r"^/mediaUpload/"),
		(CONNECTION_YOUTUBE, "GET", YOUTUBE_HOST, r"^/youtube/v3/channels$"),
		# YouTube publishing (TASK-2026-01485): a resumable upload session is opened (POST), the
		# bytes PUT to its session URI -- the same path, with an upload_id -- then a thumbnail and a
		# playlist item. PUT reaches the upload path only.
		(CONNECTION_YOUTUBE, "POST", YOUTUBE_HOST, r"^/upload/youtube/v3/videos$"),
		(CONNECTION_YOUTUBE, "PUT", YOUTUBE_HOST, r"^/upload/youtube/v3/videos$"),
		(CONNECTION_YOUTUBE, "POST", YOUTUBE_HOST, r"^/upload/youtube/v3/thumbnails/set$"),
		(CONNECTION_YOUTUBE, "POST", YOUTUBE_HOST, r"^/youtube/v3/playlistItems$"),
		# The engagement pull-back (TASK-2026-01488): reads only. A Facebook post's and an
		# Instagram media object's insights, a Facebook video's; LinkedIn's per-post statistics;
		# YouTube Analytics' report by day. (A post's own reaction/comment/share counts go through
		# the existing GET on a post ID.)
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/\d+(_\d+)?/insights$"),
		(CONNECTION_META, "GET", GRAPH_HOST, rf"^/{_V}/\d+/video_insights$"),
		(CONNECTION_LINKEDIN, "GET", LINKEDIN_HOST, r"^/rest/organizationalEntityShareStatistics$"),
		(CONNECTION_YOUTUBE, "GET", "youtubeanalytics.googleapis.com", r"^/v2/reports$"),
	)
)

YOUTUBE_ANALYTICS_HOST = "youtubeanalytics.googleapis.com"

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

# ---------------------------------------------------------------- Meta publishing (TASK-2026-01483)
# From Meta's official references, checked 2026-09-22.

#: Instagram captions.
INSTAGRAM_CAPTION_MAX = 2200
INSTAGRAM_HASHTAGS_MAX = 30
INSTAGRAM_MENTIONS_MAX = 20
#: A carousel holds up to 10 items (and counts as one post). Two is the least that is a carousel.
INSTAGRAM_CAROUSEL_MAX = 10
#: Instagram images: JPEG only, aspect ratio 4:5 to 1.91:1. Width outside 320-1440 px is scaled,
#: not refused, so it is not checked.
INSTAGRAM_IMAGE_TYPES = ("image/jpeg",)
INSTAGRAM_IMAGE_RATIO_MIN = 4 / 5
INSTAGRAM_IMAGE_RATIO_MAX = 1.91
#: Reels (the only single-video format since Meta retired feed video on 2023-11-09).
INSTAGRAM_VIDEO_TYPES = ("video/mp4", "video/quicktime")
INSTAGRAM_REEL_SECONDS_MIN = 3
INSTAGRAM_REEL_SECONDS_MAX = 15 * 60
INSTAGRAM_REEL_RATIO_MIN = 0.01
INSTAGRAM_REEL_RATIO_MAX = 10
#: Facebook Page photos.
FACEBOOK_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/bmp", "image/tiff")

#: Container processing: Meta recommends polling at most once a minute for no more than five
#: minutes. A container still processing after that is retried later (nothing was published).
CONTAINER_POLL_SECONDS = 30
CONTAINER_POLL_ATTEMPTS = 10
#: Instagram error subcode for "the daily publishing limit is reached": treated as a 429.
INSTAGRAM_LIMIT_SUBCODE = 2207042

# ---------------------------------------------------------------- LinkedIn publishing (TASK-2026-01484)
# From LinkedIn's official references, checked 2026-09-22. The Development tier allows 100 calls
# per member and 500 per app a day, across every API; a post costs about three to six.

#: Commentary: 3,000 characters (the figure LinkedIn publishes; the Posts API names no number).
LINKEDIN_COMMENTARY_MAX = 3000
#: A multi-image post: 2 to 20 images. Images JPG, GIF or PNG under 36,152,320 pixels.
LINKEDIN_IMAGES_MIN = 2
LINKEDIN_IMAGES_MAX = 20
LINKEDIN_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif")
LINKEDIN_IMAGE_PIXELS_MAX = 36_152_320
#: Documents: PDF, PPT, PPTX, DOC, DOCX; 100 MB and 300 pages. The post needs a title.
LINKEDIN_DOCUMENT_TYPES = (
	"application/pdf",
	"application/vnd.ms-powerpoint",
	"application/vnd.openxmlformats-officedocument.presentationml.presentation",
	"application/msword",
	"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)
#: A link post: LinkedIn does not read the page, so the preview's title is ours to send.
LINKEDIN_ARTICLE_TITLE_MAX = 400
#: Characters LinkedIn's "little text" format reserves; each is escaped with a backslash. A "#"
#: that starts a hashtag is left alone, so hashtags stay hashtags.
LINKEDIN_RESERVED = "\\|{}@[]()<>#*_~"
#: Waiting for an uploaded image or document to read AVAILABLE.
LINKEDIN_ASSET_POLL_SECONDS = 5
LINKEDIN_ASSET_POLL_ATTEMPTS = 24

# ---------------------------------------------------------------- YouTube publishing (TASK-2026-01485)
# From Google's official references, checked 2026-09-22.

#: Snippet limits. Title and description may not contain "<" or ">".
YOUTUBE_TITLE_MAX = 100
YOUTUBE_DESCRIPTION_MAX_BYTES = 5000
#: Tags: 500 characters in all, commas counted, and a tag with a space counted with two quotes.
YOUTUBE_TAGS_MAX = 500
#: Google's own sample default ("People & Blogs"); the API documents none.
YOUTUBE_CATEGORY_ID = "22"
#: Custom thumbnails: JPEG or PNG (50 MB since 2026-09-14; needs a verified channel).
YOUTUBE_THUMBNAIL_TYPES = ("image/jpeg", "image/png")
#: Upload chunk: a multiple of 256 KiB, as the protocol requires. 32 MiB keeps memory bounded
#: for a video of hundreds of MB, in a handful of requests.
YOUTUBE_CHUNK_BYTES = 32 * 1024 * 1024
YOUTUBE_CHUNK_TIMEOUT_SECONDS = 300
#: Transient failures while uploading are resumed from where the session says it got to;
#: this many times, with backoff, before settling.
YOUTUBE_RESUME_ATTEMPTS = 5
#: Error reasons that mean "not today": re-raised as a 429 so the job waits, never fails.
#: quotaExceeded/dailyLimitExceeded are the project's quota (403); uploadLimitExceeded (400) is
#: the channel's own daily upload limit; uploadRateLimitExceeded (429) is "try again later".
YOUTUBE_WAIT_REASONS = frozenset(
	{"quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded", "uploadRateLimitExceeded"}
)
