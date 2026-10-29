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


def parse_youtube_subscribers(body):
	items = (body or {}).get("items") or []
	stats = (items[0] or {}).get("statistics") if items else None
	if not stats or stats.get("hiddenSubscriberCount"):
		return None
	return _int(stats.get("subscriberCount"))


def parse_youtube_views(body):
	"""``{date: {views}}`` from an Analytics report by day. No rows means no data, not zeros."""
	headers = [h.get("name") for h in (body or {}).get("columnHeaders") or []]
	if "day" not in headers or "views" not in headers:
		return {}
	at_day, at_views = headers.index("day"), headers.index("views")
	out = {}
	for row in (body or {}).get("rows") or []:
		try:
			day = datetime.date.fromisoformat(str(row[at_day]))
		except (ValueError, IndexError):
			continue
		out[day] = {"views": _int(row[at_views])}
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


def fetch_facebook(transport, page_id, start, end):
	followers = parse_followers(
		transport.request("GET", f"{GRAPH}/{page_id}", params={"fields": "followers_count"})
	)
	insights = transport.request(
		"GET",
		f"{GRAPH}/{page_id}/insights",
		params={
			"metric": ",".join(FACEBOOK_METRICS),
			"period": "day",
			"since": start.isoformat(),
			"until": (end + datetime.timedelta(days=1)).isoformat(),
		},
	)
	return followers, parse_facebook_insights(insights)


def fetch_instagram(transport, ig_user_id, start, end):
	followers = parse_followers(
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
	return followers, days


def fetch_linkedin(transport, org_id, start, end):
	urn = quote(f"urn:li:organization:{org_id}", safe="")
	followers = parse_linkedin_network_size(
		transport.request(
			"GET", f"{LINKEDIN_REST}/networkSizes/{urn}", params={"edgeType": "COMPANY_FOLLOWED_BY_MEMBER"}
		)
	)
	shares = transport.request(
		"GET",
		f"{LINKEDIN_REST}/organizationalEntityShareStatistics?{linkedin_share_query(org_id, start, end)}",
	)
	return followers, parse_linkedin_share_days(shares)


def fetch_youtube(transport, _channel_id, start, end):
	followers = parse_youtube_subscribers(
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
			"metrics": "views",
			"dimensions": "day",
			"sort": "day",
		},
	)
	return followers, parse_youtube_views(report)


FETCHERS = {
	P.NETWORK_FACEBOOK: fetch_facebook,
	P.NETWORK_INSTAGRAM: fetch_instagram,
	P.NETWORK_LINKEDIN: fetch_linkedin,
	P.NETWORK_YOUTUBE: fetch_youtube,
}


def rows_for(followers, days, today):
	"""``{date: values}`` to write: each day's reach and views, and tonight's followers on today."""
	out = {day: {k: v for k, v in values.items() if v is not None} for day, values in days.items()}
	if followers is not None:
		out.setdefault(today, {})["followers"] = followers
	return {day: values for day, values in out.items() if values}


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
