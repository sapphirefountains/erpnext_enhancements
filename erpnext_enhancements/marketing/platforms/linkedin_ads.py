# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""LinkedIn Marketing API (versioned REST, ``LinkedIn-Version: 202608``).

GET-only, scopes ``r_ads`` + ``r_ads_reporting``. Requires the Marketing Developer
Platform product on the LinkedIn app (Phase 0, gate 5). If that never clears, LinkedIn
spend goes into Marketing Spend by hand -- the fallback the plan already names.

**Rest.li 2.0 query syntax is the trap here.** Its structure -- ``List(...)``,
``(start:(year:2026,...))`` -- must reach LinkedIn with the parentheses, colons and commas
*unencoded*, while the values inside (URNs) *are* encoded: ``urn%3Ali%3AsponsoredAccount%3A1``.
``requests`` would encode all of it, so these URLs are built by hand and sent with no
``params``. ``tests/test_marketing_connectors.py`` pins the exact strings.

``costInLocalCurrency`` is a decimal string in the account currency. ``conversions`` is
``externalWebsiteConversions`` -- recorded for reference; the spend report measures against
booked revenue.
"""

from urllib.parse import quote

from erpnext_enhancements.marketing.core import constants as C

PLATFORM = C.PLATFORM_LINKEDIN

ANALYTICS_FIELDS = "impressions,clicks,costInLocalCurrency,externalWebsiteConversions,dateRange,pivotValues"


def headers(access_token):
	return {
		"Authorization": f"Bearer {access_token}",
		"LinkedIn-Version": C.LINKEDIN_API_VERSION,
		"X-Restli-Protocol-Version": "2.0.0",
	}


def _urn(kind, identifier):
	return quote(f"urn:li:{kind}:{identifier}", safe="")


def _restli_date(day):
	return f"(year:{day.year},month:{day.month},day:{day.day})"


def accounts_url(start=0, count=100):
	return f"{C.LINKEDIN_REST_BASE}/adAccounts?q=search&start={start}&count={count}"


def campaigns_url(account_id, page_token=None, page_size=100):
	url = f"{C.LINKEDIN_REST_BASE}/adAccounts/{int(account_id)}/adCampaigns?q=search&pageSize={page_size}"
	return url + (f"&pageToken={quote(page_token, safe='')}" if page_token else "")


def analytics_url(account_id, date_from, date_to):
	return (
		f"{C.LINKEDIN_REST_BASE}/adAnalytics?q=analytics&pivot=CAMPAIGN&timeGranularity=DAILY"
		f"&dateRange=(start:{_restli_date(date_from)},end:{_restli_date(date_to)})"
		f"&accounts=List({_urn('sponsoredAccount', int(account_id))})"
		f"&fields={ANALYTICS_FIELDS}"
	)


# ---------------------------------------------------------------- calls


def discover_accounts(transport):
	rows, start, count = [], 0, 100
	while True:
		response = transport.request("GET", accounts_url(start, count))
		elements = response.get("elements") or []
		rows.extend(elements)
		total = (response.get("paging") or {}).get("total")
		start += count
		if not elements or (total is not None and start >= total) or len(elements) < count:
			return parse_accounts(rows)


def fetch_campaigns(transport, account_id):
	rows, token = [], None
	while True:
		response = transport.request("GET", campaigns_url(account_id, token))
		rows.extend(response.get("elements") or [])
		token = (response.get("metadata") or {}).get("nextPageToken")
		if not token:
			return parse_campaigns(rows)


def fetch_daily_metrics(transport, account_id, date_from, date_to):
	response = transport.request("GET", analytics_url(account_id, date_from, date_to))
	return parse_analytics(response.get("elements") or [])


# ---------------------------------------------------------------- parsers (pure)

CLOSED_ACCOUNT_STATUSES = frozenset({"CANCELED", "REMOVED"})


def parse_accounts(rows):
	return [
		{
			"external_id": str(row.get("id")),
			"account_name": row.get("name") or "",
			"currency": row.get("currency") or None,
		}
		for row in rows or []
		if row.get("id") and row.get("status") not in CLOSED_ACCOUNT_STATUSES
	]


def _epoch_ms_to_date(value):
	if not value:
		return None
	import datetime

	return datetime.datetime.fromtimestamp(int(value) / 1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


def parse_campaigns(rows):
	out = []
	for row in rows or []:
		if not row.get("id"):
			continue
		schedule = row.get("runSchedule") or {}
		out.append(
			{
				"external_id": str(row["id"]),
				"campaign_name": row.get("name") or "",
				"objective": row.get("objectiveType") or "",
				"platform_status": row.get("status") or "",
				"start_date": _epoch_ms_to_date(schedule.get("start")),
				"end_date": _epoch_ms_to_date(schedule.get("end")),
			}
		)
	return out


def _num(value, cast):
	try:
		return cast(value or 0)
	except (TypeError, ValueError):
		return cast(0)


def parse_analytics(elements):
	out = []
	for row in elements or []:
		pivots = row.get("pivotValues") or []
		start = (row.get("dateRange") or {}).get("start") or {}
		if not pivots or not start.get("year"):
			continue
		campaign_id = str(pivots[0]).rsplit(":", 1)[-1]
		out.append(
			{
				"campaign_external_id": campaign_id,
				"metric_date": f"{int(start['year']):04d}-{int(start['month']):02d}-{int(start['day']):02d}",
				"impressions": _num(row.get("impressions"), int),
				"clicks": _num(row.get("clicks"), int),
				"spend": round(_num(row.get("costInLocalCurrency"), float), 2),
				"conversions": _num(row.get("externalWebsiteConversions"), float),
			}
		)
	return out
