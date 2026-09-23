# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Each network's read of one published post's figures (TASK-2026-01488).

Read-only calls through ``publish/client.py``'s allowlist, with the scopes requested since v1.508.0
for exactly this, so turning metrics on needs no reconnect. Checked against the official references
on 2026-09-22; what changed recently is noted where it bites.

========== ==================================================== =========================
Network    What is read                                         Granularity
========== ==================================================== =========================
Facebook   ``/{post}/insights``: post_media_view (views, the     lifetime
           2025 replacement for the removed post_impressions),
           post_total_media_view_unique (reach), post_clicks,
           post_video_views; and the post's reaction, comment
           and share counts. A **video** was published to
           ``/videos`` and its ID is a video's, not a post's:
           ``/{video}/video_insights`` total_video_views, and
           the video's reaction and comment counts.
Instagram  ``/{media}/insights``: views (``impressions`` was      lifetime (Meta sets it;
           removed in April 2025), reach, total_interactions.    up to 48 h late)
           Organic only, by Meta's definition.
LinkedIn   ``organizationalEntityShareStatistics`` for the one   lifetime ("time-bound
           post: impressionCount, uniqueImpressionsCount,        statistics is not
           clickCount, and like + comment + share counts. A      supported for specific
           post with no activity is left out of the answer,      share queries")
           which LinkedIn documents as all zeros.
YouTube    Analytics ``reports`` by day, filtered to the video:  per day, 48-72 h late
           views, likes, comments, shares. No per-video
           impressions exist in that API.
========== ==================================================== =========================

**Engagements** means the same everywhere: reactions (likes) + comments + shares, and saves on
Instagram (``total_interactions``). **Clicks** are link clicks, which only Facebook and LinkedIn
report. A figure a network does not report is None, never 0.

Two notes for whoever reads the numbers. YouTube changed what a view is on 2026-08-24 (it now
counts from the moment playback starts, autoplay included), so its history has a step there.
And Instagram may insist on ``ads_management`` for a Page role that came through Business
Manager; that scope is never requested here (decision 3), so such an account's reads fail and the
job says why.

The parsers are pure; the ``fetch_*`` functions take a transport.
"""

import datetime
from urllib.parse import quote

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish.metrics import DAILY, LIFETIME

GRAPH = C.META_GRAPH_BASE
YOUTUBE_ANALYTICS = f"https://{P.YOUTUBE_ANALYTICS_HOST}/v2/reports"
LINKEDIN_SHARE_STATISTICS = f"{C.LINKEDIN_REST_BASE}/organizationalEntityShareStatistics"

FACEBOOK_POST_METRICS = ("post_media_view", "post_total_media_view_unique", "post_clicks", "post_video_views")
FACEBOOK_VIDEO_METRICS = ("total_video_views",)
FACEBOOK_POST_COUNTS = "shares,comments.summary(true).limit(0),reactions.summary(true).limit(0)"
FACEBOOK_VIDEO_COUNTS = "comments.summary(true).limit(0),reactions.summary(true).limit(0)"
INSTAGRAM_METRICS = ("views", "reach", "total_interactions")
YOUTUBE_METRICS = ("views", "likes", "comments", "shares")


def _int(value):
	if value is None or value == "":
		return None
	if isinstance(value, dict):  # a by-type breakdown: the total is the sum
		return sum(int(v or 0) for v in value.values())
	return int(value)


def _sum(*values):
	present = [v for v in values if v is not None]
	return sum(present) if present else None


def insight_values(body):
	"""``{metric: lifetime value}`` from a Graph insights answer. Missing metrics are absent."""
	out = {}
	for item in (body or {}).get("data") or []:
		values = item.get("values") or []
		if item.get("name") and values:
			out[item["name"]] = _int(values[-1].get("value"))
	return out


def _count(body, edge):
	"""A Graph edge's ``summary.total_count`` (comments, reactions), or None when absent."""
	summary = ((body or {}).get(edge) or {}).get("summary") or {}
	return _int(summary.get("total_count"))


def parse_facebook(insights, counts):
	values = insight_values(insights)
	shares = _int(((counts or {}).get("shares") or {}).get("count"))
	if counts is not None and shares is None:
		shares = 0  # Graph leaves ``shares`` out of a post nobody has shared
	return {
		"impressions": values.get("post_media_view"),
		"reach": values.get("post_total_media_view_unique"),
		"engagements": _sum(_count(counts, "reactions"), _count(counts, "comments"), shares),
		"clicks": values.get("post_clicks"),
		"video_views": values.get("post_video_views"),
	}


def parse_facebook_video(insights, counts):
	values = insight_values(insights)
	return {
		"impressions": None,
		"reach": None,
		"engagements": _sum(_count(counts, "reactions"), _count(counts, "comments")),
		"clicks": None,
		"video_views": values.get("total_video_views"),
	}


def parse_instagram(insights, has_video):
	values = insight_values(insights)
	return {
		"impressions": values.get("views"),
		"reach": values.get("reach"),
		"engagements": values.get("total_interactions"),
		"clicks": None,
		"video_views": values.get("views") if has_video else None,
	}


def parse_linkedin(body, urn):
	"""One post's lifetime totals. LinkedIn leaves out a post with no activity: that is all zeros."""
	element = next(
		(e for e in (body or {}).get("elements") or [] if urn in (e.get("share"), e.get("ugcPost"))),
		None,
	)
	stats = (element or {}).get("totalShareStatistics") or {}
	if element is None:
		stats = {"impressionCount": 0, "clickCount": 0, "likeCount": 0, "commentCount": 0, "shareCount": 0}
	return {
		"impressions": _int(stats.get("impressionCount")),
		"reach": _int(stats.get("uniqueImpressionsCount")),
		"engagements": _sum(
			_int(stats.get("likeCount")), _int(stats.get("commentCount")), _int(stats.get("shareCount"))
		),
		"clicks": _int(stats.get("clickCount")),
		"video_views": None,
	}


def parse_youtube(body):
	"""``[{date, video_views, engagements}]`` from a YouTube Analytics report by day."""
	headers = [h.get("name") for h in (body or {}).get("columnHeaders") or []]
	if "day" not in headers:
		return []
	at = {name: i for i, name in enumerate(headers)}

	def get(row, name):
		return _int(row[at[name]]) if name in at else None

	return [
		{
			"date": row[at["day"]],
			"video_views": get(row, "views"),
			"engagements": _sum(get(row, "likes"), get(row, "comments"), get(row, "shares")),
		}
		for row in (body or {}).get("rows") or []
	]


def linkedin_query(org_id, urn):
	"""The Rest.li 2.0 query for one post's statistics, built by hand: ``List(...)`` must reach
	LinkedIn with its parentheses intact and the URN inside it encoded."""
	kind = "ugcPosts" if ":ugcPost:" in urn else "shares"
	return (
		f"q=organizationalEntity&organizationalEntity={quote(f'urn:li:organization:{org_id}', safe='')}"
		f"&{kind}=List({quote(urn, safe='')})"
	)


# ---------------------------------------------------------------- reads


def fetch_facebook(transport, job):
	post_id = job["external_post_id"]
	if "_" in post_id:
		insights = transport.request(
			"GET", f"{GRAPH}/{post_id}/insights", params={"metric": ",".join(FACEBOOK_POST_METRICS)}
		)
		counts = transport.request("GET", f"{GRAPH}/{post_id}", params={"fields": FACEBOOK_POST_COUNTS})
		return {"kind": LIFETIME, "figures": parse_facebook(insights, counts)}
	insights = transport.request(
		"GET", f"{GRAPH}/{post_id}/video_insights", params={"metric": ",".join(FACEBOOK_VIDEO_METRICS)}
	)
	counts = transport.request("GET", f"{GRAPH}/{post_id}", params={"fields": FACEBOOK_VIDEO_COUNTS})
	return {"kind": LIFETIME, "figures": parse_facebook_video(insights, counts)}


def fetch_instagram(transport, job):
	insights = transport.request(
		"GET", f"{GRAPH}/{job['external_post_id']}/insights", params={"metric": ",".join(INSTAGRAM_METRICS)}
	)
	return {"kind": LIFETIME, "figures": parse_instagram(insights, bool(job.get("has_video")))}


def fetch_linkedin(transport, job):
	urn = job["external_post_id"]
	body = transport.request(
		"GET", f"{LINKEDIN_SHARE_STATISTICS}?{linkedin_query(job['account_external_id'], urn)}"
	)
	return {"kind": LIFETIME, "figures": parse_linkedin(body, urn)}


def fetch_youtube(transport, job, today):
	"""Every day since the video went out: the rows are summed from publishing (``metrics.cumulative``)."""
	published = job["published_at"]
	published = published.date() if isinstance(published, datetime.datetime) else published
	body = transport.request(
		"GET",
		YOUTUBE_ANALYTICS,
		params={
			"ids": "channel==MINE",
			"startDate": str(published)[:10],
			"endDate": str(today),
			"metrics": ",".join(YOUTUBE_METRICS),
			"dimensions": "day",
			"filters": f"video=={job['external_post_id']}",
			"sort": "day",
		},
	)
	return {"kind": DAILY, "days": parse_youtube(body)}


def fetch(transport, job, today):
	network = job.get("network")
	if network == P.NETWORK_FACEBOOK:
		return fetch_facebook(transport, job)
	if network == P.NETWORK_INSTAGRAM:
		return fetch_instagram(transport, job)
	if network == P.NETWORK_LINKEDIN:
		return fetch_linkedin(transport, job)
	if network == P.NETWORK_YOUTUBE:
		return fetch_youtube(transport, job, today)
	raise ValueError(f"no metrics read for {network!r}")
