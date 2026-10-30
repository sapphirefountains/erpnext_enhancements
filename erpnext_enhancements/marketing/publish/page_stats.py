# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Page-level followers, reach and views for each social account (v1.756.0).

Read-only calls on the **existing publishing connections**: their scopes already carry the
insight reads (``read_insights``, ``instagram_manage_insights``, ``rw_organization_admin``,
``yt-analytics.readonly``, and ``youtube.readonly`` since this release), so turning this on needs
no new connection and no spend-capable scope. Checked against the vendors' references on
2026-10-29:

========== ============================================== =====================================
Network    Followers (tonight)                             Reach / views (per day)
========== ============================================== =====================================
Facebook   Page ``followers_count``                        ``/{page}/insights`` period=day:
                                                           ``page_total_media_view_unique``
                                                           (reach; ``page_impressions_unique``
                                                           is gone) and ``page_media_view``
                                                           (views; ``page_impressions`` was
                                                           deprecated 2025-11-15)
Instagram  IG user ``followers_count``                     ``/{ig-user}/insights`` reach and
                                                           views, ``metric_type=total_value``
                                                           one day at a time (views takes no
                                                           time series)
LinkedIn   ``networkSizes`` ``firstDegreeSize``            time-bound share statistics by DAY:
           (follower statistics no longer return a total)  ``uniqueImpressionsCount`` (the
                                                           docs' own sample spells it with an
                                                           s; both are read), ``impressionCount``
YouTube    ``channels?part=statistics&mine=true``          Analytics ``views`` by day. YouTube
           ``subscriberCount`` (rounded by YouTube to      reports no reach
           three significant figures)
========== ============================================== =====================================

Gated like every pull here: ``Marketing Settings.enabled`` and ``social_page_stats_enabled``
(both ship 0), then only *Connected* connections and only accounts ticked **Enabled**. A figure a
network does not report is None, never 0. The parsers are pure; the ``fetch_*`` functions take a
transport and the sync around them is at the bottom.
"""

import datetime
from urllib.parse import quote

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.publish import constants as P

GRAPH = C.META_GRAPH_BASE
YOUTUBE_ANALYTICS = f"https://{P.YOUTUBE_ANALYTICS_HOST}/v2/reports"
LINKEDIN_REST = C.LINKEDIN_REST_BASE

ENABLED_FLAG = "social_page_stats_enabled"
DOCTYPE = "Social Page Daily Metric"
RESTATE_DAYS = 7
INITIAL_DAYS = 30
MAX_CATCHUP_DAYS = 90
FACEBOOK_METRICS = ("page_total_media_view_unique", "page_media_view")
FACEBOOK_MAX_SPAN_DAYS = 90
#: How far back each network's page insights go (checked 2026-10-29; see ``history_start``).
HISTORY_DAYS = {P.NETWORK_FACEBOOK: 730, P.NETWORK_INSTAGRAM: 90, P.NETWORK_LINKEDIN: 365}
#: LinkedIn's statistics end two days before the request.
LINKEDIN_LAG_DAYS = 2
HISTORY_CHUNK_DAYS = 31
INSTAGRAM_METRICS = ("reach", "views")


def _int(value):
	if value is None or value == "":
		return None
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return None


def _ms(day):
	return int(
		datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp() * 1000
	)


def _unix(day):
	return int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp())


# ---------------------------------------------------------------- parsers (pure)


def parse_facebook_insights(body):
	"""``{date: {reach, views}}`` from Page insights with ``period=day``.

	Each value's ``end_time`` is the **end** of the day it covers (midnight Pacific, written in
	UTC as the next morning), so the day is the one before ``end_time``'s date.
	"""
	out = {}
	columns = {"page_total_media_view_unique": "reach", "page_media_view": "views"}
	for item in (body or {}).get("data") or []:
		column = columns.get(item.get("name"))
		if not column or item.get("period") not in (None, "day"):
			continue
		for point in item.get("values") or []:
			end = str(point.get("end_time") or "")[:10]
			try:
				day = datetime.date.fromisoformat(end) - datetime.timedelta(days=1)
			except ValueError:
				continue
			out.setdefault(day, {})[column] = _int(point.get("value"))
	return out


def parse_total_values(body):
	"""``{metric: value}`` from an Instagram insights answer with ``metric_type=total_value``."""
	out = {}
	for item in (body or {}).get("data") or []:
		total = item.get("total_value") or {}
		if item.get("name") and "value" in total:
			out[item["name"]] = _int(total.get("value"))
	return out


def parse_followers(body, key="followers_count"):
	return _int((body or {}).get(key))


def parse_linkedin_network_size(body):
	return _int((body or {}).get("firstDegreeSize"))


def parse_linkedin_share_days(body):
	"""``{date: {reach, views}}`` from time-bound share statistics by DAY."""
	out = {}
	for element in (body or {}).get("elements") or []:
		start = (element.get("timeRange") or {}).get("start")
		try:
			day = datetime.datetime.fromtimestamp(int(start) / 1000, tz=datetime.timezone.utc).date()
		except (TypeError, ValueError, OverflowError):
			continue
		stats = element.get("totalShareStatistics") or {}
		reach = stats.get("uniqueImpressionsCount", stats.get("uniqueImpressionsCounts"))
		out[day] = {
			"reach": _int(reach) if reach is not None else 0,
			"views": _int(stats.get("impressionCount")) or 0,
		}
	return out


def parse_linkedin_follower_gains(body):
	"""``{date: net new followers}`` from time-bound follower statistics by DAY (organic + paid)."""
	out = {}
	for element in (body or {}).get("elements") or []:
		start = (element.get("timeRange") or {}).get("start")
		try:
			day = datetime.datetime.fromtimestamp(int(start) / 1000, tz=datetime.timezone.utc).date()
		except (TypeError, ValueError, OverflowError):
			continue
		gains = element.get("followerGains") or {}
		out[day] = (_int(gains.get("organicFollowerGain")) or 0) + (_int(gains.get("paidFollowerGain")) or 0)
	return out


def parse_youtube_subscribers(body):
	items = (body or {}).get("items") or []
	stats = (items[0] or {}).get("statistics") if items else None
	if not stats or stats.get("hiddenSubscriberCount"):
		return None
	return _int(stats.get("subscriberCount"))


def parse_youtube_views(body):
	"""``{date: {views, follower_gain}}`` from an Analytics report by day. No rows means no data, not zeros.

	``follower_gain`` is subscribersGained minus subscribersLost, where the report carries both.
	"""
	headers = [h.get("name") for h in (body or {}).get("columnHeaders") or []]
	if "day" not in headers or "views" not in headers:
		return {}
	at = {name: i for i, name in enumerate(headers)}
	out = {}
	for row in (body or {}).get("rows") or []:
		try:
			day = datetime.date.fromisoformat(str(row[at["day"]]))
		except (ValueError, IndexError):
			continue
		values = {"views": _int(row[at["views"]])}
		if "subscribersGained" in at and "subscribersLost" in at:
			values["follower_gain"] = (_int(row[at["subscribersGained"]]) or 0) - (
				_int(row[at["subscribersLost"]]) or 0
			)
		out[day] = values
	return out


def linkedin_share_query(org_id, start, end):
	"""Rest.li 2.0 time-bound share statistics, built by hand so the parentheses arrive intact.

	``end`` is exclusive in LinkedIn's time range, so it is the day after the last day wanted.
	"""
	urn = quote(f"urn:li:organization:{org_id}", safe="")
	end_ms = _ms(end + datetime.timedelta(days=1))
	return (
		f"q=organizationalEntity&organizationalEntity={urn}"
		f"&timeIntervals=(timeRange:(start:{_ms(start)},end:{end_ms}),timeGranularityType:DAY)"
	)


# ---------------------------------------------------------------- reads (take a transport)
#
# Each returns ``(followers, days)``: tonight's follower total (None when ``followers`` is False, as
# for a backfill window), and ``{date: {reach, views, follower_gain}}`` for ``start..end``.


def fetch_facebook(transport, page_id, start, end, followers=True):
	total = None
	if followers:
		total = parse_followers(
			transport.request("GET", f"{GRAPH}/{page_id}", params={"fields": "followers_count"})
		)
	days = {}
	# "Only 90 days of insights can be viewed at one time."
	for chunk_start, chunk_end in _chunks(start, end, FACEBOOK_MAX_SPAN_DAYS):
		insights = transport.request(
			"GET",
			f"{GRAPH}/{page_id}/insights",
			params={
				"metric": ",".join(FACEBOOK_METRICS),
				"period": "day",
				"since": chunk_start.isoformat(),
				"until": (chunk_end + datetime.timedelta(days=1)).isoformat(),
			},
		)
		days.update(parse_facebook_insights(insights))
	return total, days


def fetch_instagram(transport, ig_user_id, start, end, followers=True):
	total = None
	if followers:
		total = parse_followers(
			transport.request("GET", f"{GRAPH}/{ig_user_id}", params={"fields": "followers_count"})
		)
	days = {}
	day = start
	while day <= end:
		body = transport.request(
			"GET",
			f"{GRAPH}/{ig_user_id}/insights",
			params={
				"metric": ",".join(INSTAGRAM_METRICS),
				"period": "day",
				"metric_type": "total_value",
				"since": _unix(day),
				"until": _unix(day + datetime.timedelta(days=1)) - 1,
			},
		)
		values = parse_total_values(body)
		days[day] = {"reach": values.get("reach"), "views": values.get("views")}
		day += datetime.timedelta(days=1)
	return total, days


def fetch_linkedin(transport, org_id, start, end, followers=True):
	urn = quote(f"urn:li:organization:{org_id}", safe="")
	total = None
	if followers:
		total = parse_linkedin_network_size(
			transport.request(
				"GET",
				f"{LINKEDIN_REST}/networkSizes/{urn}",
				params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"},
			)
		)
	shares = transport.request(
		"GET",
		f"{LINKEDIN_REST}/organizationalEntityShareStatistics?{linkedin_share_query(org_id, start, end)}",
	)
	gains = transport.request(
		"GET",
		f"{LINKEDIN_REST}/organizationalEntityFollowerStatistics?{linkedin_share_query(org_id, start, end)}",
	)
	days = parse_linkedin_share_days(shares)
	for day, gain in parse_linkedin_follower_gains(gains).items():
		days.setdefault(day, {})["follower_gain"] = gain
	return total, days


def fetch_youtube(transport, _channel_id, start, end, followers=True):
	total = None
	if followers:
		total = parse_youtube_subscribers(
			transport.request(
				"GET", f"{P.YOUTUBE_API_BASE}/channels", params={"part": "statistics", "mine": "true"}
			)
		)
	report = transport.request(
		"GET",
		YOUTUBE_ANALYTICS,
		params={
			"ids": "channel==MINE",
			"startDate": start.isoformat(),
			"endDate": end.isoformat(),
			"metrics": "views,subscribersGained,subscribersLost",
			"dimensions": "day",
			"sort": "day",
		},
	)
	return total, parse_youtube_views(report)


def youtube_created(transport):
	"""The channel's creation day (``snippet.publishedAt``): where its Analytics history starts."""
	body = transport.request(
		"GET", f"{P.YOUTUBE_API_BASE}/channels", params={"part": "snippet", "mine": "true"}
	)
	items = (body or {}).get("items") or []
	published = ((items[0] or {}).get("snippet") or {}).get("publishedAt") if items else None
	try:
		return datetime.date.fromisoformat(str(published)[:10]) if published else None
	except ValueError:
		return None


FETCHERS = {
	P.NETWORK_FACEBOOK: fetch_facebook,
	P.NETWORK_INSTAGRAM: fetch_instagram,
	P.NETWORK_LINKEDIN: fetch_linkedin,
	P.NETWORK_YOUTUBE: fetch_youtube,
}


def _chunks(start, end, size):
	out = []
	day = start
	while day <= end:
		last = min(day + datetime.timedelta(days=size - 1), end)
		out.append((day, last))
		day = last + datetime.timedelta(days=1)
	return out


def rows_for(followers, days, today):
	"""``{date: values}`` to write: each day's figures, and tonight's followers (marked read) on today."""
	out = {day: {k: v for k, v in values.items() if v is not None} for day, values in days.items()}
	if followers is not None:
		out.setdefault(today, {}).update(followers=followers, followers_read=1)
	return {day: values for day, values in out.items() if values}


def history_start(network, today, created=None):
	"""The oldest day ``network``'s page insights serve today, or None when it keeps no history.

	Facebook two years; Instagram 90 days (Meta's overview: "User Metrics data is stored for up to 90
	days"; the reference names no limit, so the shorter figure is used); LinkedIn the rolling 12
	months; YouTube from the channel's creation (``created``).
	"""
	if network == P.NETWORK_YOUTUBE:
		return created
	days = HISTORY_DAYS.get(network)
	return today - datetime.timedelta(days=days) if days else None


# ---------------------------------------------------------------- the nightly sync


def enabled(settings):
	from frappe.utils import cint

	return bool(cint(settings.get("enabled"))) and bool(cint(settings.get(ENABLED_FLAG)))


def due_accounts(creds):
	"""Enabled Social Accounts whose publishing connection is *Connected*."""
	import frappe

	from erpnext_enhancements.marketing.core.utils import field
	from erpnext_enhancements.marketing.publish import oauth

	live = {
		connection
		for connection in P.PUBLISH_CONNECTIONS
		if (creds.get(field(connection, "connection_status")) or "") == oauth.STATUS_CONNECTED
	}
	return [
		row
		for row in frappe.get_all(
			"Social Account", filters={"enabled": 1}, fields=["name", "network", "external_id"]
		)
		if P.CONNECTION_FOR.get(row.network) in live and row.external_id
	]


def nightly_social_page_stats():
	"""Cron entry (hooks.py, 03:55): master switch, this switch, something connected, then ``long``."""
	import frappe

	from erpnext_enhancements.marketing.core.utils import get_credentials, get_settings

	if not frappe.db.exists("DocType", DOCTYPE) or not enabled(get_settings()):
		return None
	if not due_accounts(get_credentials()):
		return None
	frappe.enqueue(
		"erpnext_enhancements.marketing.publish.page_stats.run_page_stats",
		queue="long",
		timeout=30 * 60,
		job_id="marketing-social-page-stats",
		deduplicate=True,
	)
	return "marketing-social-page-stats"


def _upsert(account, network, day, values):
	import frappe

	name = f"SPDM-{account}-{day.isoformat()}"
	if frappe.db.exists(DOCTYPE, name):
		frappe.db.set_value(DOCTYPE, name, values)
		return
	frappe.get_doc(
		{"doctype": DOCTYPE, "social_account": account, "network": network, "metric_date": day, **values}
	).insert(ignore_permissions=True)


def run_page_stats(http=None, today=None):
	"""The background job: each due account's window, one account at a time, committed after each."""
	import frappe
	from frappe.utils import getdate, now_datetime, nowdate

	from erpnext_enhancements.marketing.core.utils import get_credentials, get_settings
	from erpnext_enhancements.marketing.performance import rules
	from erpnext_enhancements.marketing.publish import oauth

	settings = get_settings()
	creds = get_credentials()
	today = getdate(today or nowdate())
	log = frappe.get_doc(
		{
			"doctype": "Marketing Sync Log",
			"platform": "Social Pages",
			"sync_type": "Daily Metrics",
			"status": "Running",
			"started_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()
	transports, written, errors = {}, 0, []
	for account in due_accounts(creds):
		connection = P.CONNECTION_FOR[account.network]
		try:
			if connection not in transports:
				try:
					transports[connection] = oauth.transport_for(
						connection, creds, http=http, settings=settings
					)
				except Exception as exc:  # a dead token: every account on it fails with the same reason
					transports[connection] = exc
			if isinstance(transports[connection], Exception):
				raise transports[connection]
			last = frappe.db.sql(
				f"select max(metric_date) from `tab{DOCTYPE}` where social_account = %s", (account.name,)
			)[0][0]
			# LinkedIn's statistics end two days before the request, YouTube's lag 48-72 hours.
			window = rules.nightly_window(
				getdate(last) if last else None,
				today,
				RESTATE_DAYS,
				INITIAL_DAYS,
				MAX_CATCHUP_DAYS,
				lag_days=2,
			)
			# Nothing new to restate still reads tonight's followers, with the last settled day beside it.
			start, end = window or (today - datetime.timedelta(days=2),) * 2
			followers, days = FETCHERS[account.network](
				transports[connection], account.external_id, start, end
			)
			for day, values in rows_for(followers, days, today).items():
				_upsert(account.name, account.network, day, values)
				written += 1
			frappe.db.commit()
		except Exception as exc:
			frappe.db.rollback()
			errors.append(f"{account.name}: {type(exc).__name__}: {exc}"[:300])
	frappe.db.set_value(
		"Marketing Sync Log",
		log.name,
		{
			"status": "Failed" if errors and not written else "Completed",
			"finished_at": now_datetime(),
			"created_count": written,
			"failed_count": len(errors),
			"error_message": "; ".join(errors)[:2000] or None,
		},
		update_modified=False,
	)
	frappe.db.commit()
	return {"written": written, "failed": len(errors)}


# ---------------------------------------------------------------- history (web_sync's backfill)

#: Channel creation days already read in this process, so a backfill window does not re-ask.
_CREATED = {}


def ready():
	"""Switched on with an account to read: the gate for the nightly pull and the backfill alike."""
	import frappe

	from erpnext_enhancements.marketing.core.utils import get_credentials, get_settings

	try:
		return (
			bool(frappe.db.exists("DocType", DOCTYPE))
			and enabled(get_settings())
			and bool(due_accounts(get_credentials()))
		)
	except Exception:
		return False


def _transports():
	from erpnext_enhancements.marketing.core.utils import get_credentials, get_settings
	from erpnext_enhancements.marketing.publish import oauth

	settings, creds, cache = get_settings(), get_credentials(), {}

	def transport(network):
		connection = P.CONNECTION_FOR[network]
		if connection not in cache:
			cache[connection] = oauth.transport_for(connection, creds, settings=settings)
		return cache[connection]

	return creds, transport


def _account_start(account, today, transport):
	created = None
	if account.network == P.NETWORK_YOUTUBE:
		if account.name not in _CREATED:
			_CREATED[account.name] = youtube_created(transport(account.network))
		created = _CREATED[account.name]
	return history_start(account.network, today, created)


def history_scopes():
	"""Each due Social Account, by name: each has a backfill of its own, so an account connected later
	gets as much history as its network serves, not just the nightly window."""
	from erpnext_enhancements.marketing.core.utils import get_credentials

	return [account.name for account in due_accounts(get_credentials())]


def _due(creds, scope):
	return [a for a in due_accounts(creds) if not scope or a.name == scope]


def history_earliest(today, scope=None):
	"""The oldest day the account's network serves (the oldest of any due account's, without a
	``scope``): the backfill's window start."""
	creds, transport = _transports()
	starts = [s for s in (_account_start(a, today, transport) for a in _due(creds, scope)) if s]
	return min(starts) if starts else None


def history_pull(start, end, scope=None):
	"""One account's daily figures for ``start..end`` (every due account's without a ``scope``),
	clamped to what its network still serves.

	Followers are not read here: only tonight's total exists, and the nightly pull takes it. Raises on
	a failure so the backfill's cursor stays where it was.
	"""
	import frappe
	from frappe.utils import getdate, nowdate

	today = getdate(nowdate())
	creds, transport = _transports()
	written = 0
	for account in _due(creds, scope):
		earliest = _account_start(account, today, transport)
		last = end
		if account.network == P.NETWORK_LINKEDIN:
			last = min(end, today - datetime.timedelta(days=LINKEDIN_LAG_DAYS))
		first = max(start, earliest) if earliest else None
		if first is None or first > last:
			continue
		_total, days = FETCHERS[account.network](
			transport(account.network), account.external_id, first, last, followers=False
		)
		for day, values in rows_for(None, days, today).items():
			_upsert(account.name, account.network, day, values)
			written += 1
		frappe.db.commit()
	return written
