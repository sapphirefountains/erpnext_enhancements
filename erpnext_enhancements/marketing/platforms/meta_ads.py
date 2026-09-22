# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Meta Marketing API (Graph v26.0): ad accounts, campaigns, campaign-day insights.

GET-only, with the ``ads_read`` scope. The access token travels in the ``Authorization``
header rather than as ``access_token=`` in the query string, so it is not in any URL the
sync archives; ``utils.redact_url`` strips it anyway if Meta ever echoes it into a
``paging.next`` link.

Pagination follows ``paging.next``, and only while the link stays on graph.facebook.com
and on an allowlisted path -- the transport checks every hop.

Spend arrives as a decimal **string** in the account currency. ``conversions`` is the sum
of ``lead`` actions: the platform's own count is recorded for reference only, because the
spend report (TASK-2026-01477) measures return against booked revenue, not against what
Meta says converted.
"""

import json

from erpnext_enhancements.marketing.core import constants as C

PLATFORM = C.PLATFORM_META

#: Action types counted as a conversion.
LEAD_ACTION_TYPES = frozenset({"lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"})

PAGE_LIMIT = 200


def headers(access_token):
	return {"Authorization": f"Bearer {access_token}"}


def _all_pages(transport, url, params):
	rows = []
	response = transport.request("GET", url, params=params)
	while True:
		rows.extend(response.get("data") or [])
		next_url = (response.get("paging") or {}).get("next")
		if not next_url:
			return rows
		# The next link carries its own query string; the transport re-checks the host and
		# path against the allowlist before following it.
		response = transport.request("GET", next_url)


def discover_accounts(transport):
	rows = _all_pages(
		transport,
		f"{C.META_GRAPH_BASE}/me/adaccounts",
		{"fields": "account_id,name,currency,account_status", "limit": PAGE_LIMIT},
	)
	return parse_accounts(rows)


def fetch_campaigns(transport, account_id):
	rows = _all_pages(
		transport,
		f"{C.META_GRAPH_BASE}/act_{account_id}/campaigns",
		{"fields": "id,name,objective,effective_status,start_time,stop_time", "limit": PAGE_LIMIT},
	)
	return parse_campaigns(rows)


def fetch_daily_metrics(transport, account_id, date_from, date_to):
	rows = _all_pages(
		transport,
		f"{C.META_GRAPH_BASE}/act_{account_id}/insights",
		{
			"level": "campaign",
			"time_increment": 1,
			"time_range": json.dumps({"since": f"{date_from:%Y-%m-%d}", "until": f"{date_to:%Y-%m-%d}"}),
			"fields": "campaign_id,impressions,clicks,spend,actions,date_start",
			"limit": PAGE_LIMIT,
		},
	)
	return parse_insights(rows)


# ---------------------------------------------------------------- parsers (pure)

#: account_status 1 is ACTIVE; 2 DISABLED, 101 CLOSED. Anything else is kept -- a
#: temporarily unsettled account still has spend to report.
CLOSED_ACCOUNT_STATUSES = frozenset({2, 101})


def parse_accounts(rows):
	return [
		{
			"external_id": str(row.get("account_id")),
			"account_name": row.get("name") or "",
			"currency": row.get("currency") or None,
		}
		for row in rows or []
		if row.get("account_id") and row.get("account_status") not in CLOSED_ACCOUNT_STATUSES
	]


def _date(value):
	"""``2026-09-01T00:00:00-0600`` -> ``2026-09-01``."""
	return str(value)[:10] if value else None


def parse_campaigns(rows):
	return [
		{
			"external_id": str(row.get("id")),
			"campaign_name": row.get("name") or "",
			"objective": row.get("objective") or "",
			"platform_status": row.get("effective_status") or "",
			"start_date": _date(row.get("start_time")),
			"end_date": _date(row.get("stop_time")),
		}
		for row in rows or []
		if row.get("id")
	]


def _num(value, cast):
	try:
		return cast(value or 0)
	except (TypeError, ValueError):
		return cast(0)


def parse_insights(rows):
	out = []
	for row in rows or []:
		if not (row.get("campaign_id") and row.get("date_start")):
			continue
		leads = sum(
			_num(action.get("value"), float)
			for action in row.get("actions") or []
			if action.get("action_type") in LEAD_ACTION_TYPES
		)
		out.append(
			{
				"campaign_external_id": str(row["campaign_id"]),
				"metric_date": row["date_start"],
				"impressions": _num(row.get("impressions"), int),
				"clicks": _num(row.get("clicks"), int),
				"spend": round(_num(row.get("spend"), float), 2),
				"conversions": leads,
			}
		)
	return out
