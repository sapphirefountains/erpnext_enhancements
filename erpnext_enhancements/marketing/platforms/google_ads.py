# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Google Ads (API v25, REST): accounts, campaigns, campaign-day metrics, and clicks.

Every read is a GAQL query POSTed to ``customers/{id}/googleAds:search`` -- a query
endpoint; GAQL cannot write. The ``parse_*`` functions are pure so they are tested
against recorded-shape fixtures (``tests/fixtures/marketing/``) without API access, which
does not exist yet (Phase 0: developer token pending).

REST returns field names in camelCase (``costMicros``, ``clickView``) and int64 metrics
as **strings**; the parsers handle both. Spend arrives in micros of the account currency.

**Clicks are decision D (TASK-2026-01570):** ``click_view`` maps each gclid to its campaign,
one day per query, and only for the last 90 days. The sync stores every click (Ad Click)
so a Lead that arrives weeks after its click still resolves.
"""

from erpnext_enhancements.marketing.core import constants as C

PLATFORM = C.PLATFORM_GOOGLE

CUSTOMER_QUERY = (
	"SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.manager "
	"FROM customer LIMIT 1"
)
CAMPAIGN_QUERY = (
	"SELECT campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type " "FROM campaign"
)


def metrics_query(date_from, date_to):
	return (
		"SELECT campaign.id, segments.date, metrics.impressions, metrics.clicks, "
		"metrics.cost_micros, metrics.conversions FROM campaign "
		f"WHERE segments.date BETWEEN '{date_from:%Y-%m-%d}' AND '{date_to:%Y-%m-%d}'"
	)


def click_query(day):
	# click_view accepts exactly one day per query.
	return (
		"SELECT click_view.gclid, campaign.id, ad_group.id, segments.date FROM click_view "
		f"WHERE segments.date = '{day:%Y-%m-%d}'"
	)


def headers(access_token, developer_token, login_customer_id=None):
	out = {
		"Authorization": f"Bearer {access_token}",
		"developer-token": developer_token,
		"Content-Type": "application/json",
	}
	if login_customer_id:
		# Required when the authorizing user reaches the account through a manager (MCC).
		out["login-customer-id"] = digits(login_customer_id)
	return out


def digits(customer_id):
	"""``123-456-7890`` -> ``1234567890``; Google shows dashes, the API refuses them."""
	return "".join(ch for ch in str(customer_id or "") if ch.isdigit())


def search_url(customer_id):
	return f"{C.GOOGLE_ADS_BASE}/customers/{digits(customer_id)}/googleAds:search"


# ---------------------------------------------------------------- calls


def search(transport, customer_id, query):
	"""All result rows for one GAQL query, following ``nextPageToken``."""
	rows, page_token = [], None
	while True:
		body = {"query": query}
		if page_token:
			body["pageToken"] = page_token
		response = transport.request("POST", search_url(customer_id), json=body)
		rows.extend(response.get("results") or [])
		page_token = response.get("nextPageToken")
		if not page_token:
			return rows


def discover_accounts(transport):
	"""Client (non-manager) accounts the authorizing user can read."""
	listed = transport.request("GET", f"{C.GOOGLE_ADS_BASE}/customers:listAccessibleCustomers")
	accounts = []
	for customer_id in parse_accessible_customers(listed):
		account = parse_customer(search(transport, customer_id, CUSTOMER_QUERY))
		if account and not account.pop("manager"):
			accounts.append(account)
	return accounts


def fetch_campaigns(transport, account_id):
	return parse_campaigns(search(transport, account_id, CAMPAIGN_QUERY))


def fetch_daily_metrics(transport, account_id, date_from, date_to):
	return parse_metrics(search(transport, account_id, metrics_query(date_from, date_to)))


def fetch_clicks(transport, account_id, day):
	return parse_clicks(search(transport, account_id, click_query(day)))


# ---------------------------------------------------------------- parsers (pure)


def _get(row, *path):
	"""``row[a][b]``, accepting camelCase or snake_case at each step."""
	node = row
	for key in path:
		if not isinstance(node, dict):
			return None
		camel = key.split("_")[0] + "".join(part.title() for part in key.split("_")[1:])
		node = node.get(camel, node.get(key))
	return node


def _int(value):
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _float(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def parse_accessible_customers(body):
	"""``{"resourceNames": ["customers/123"]}`` -> ``["123"]``."""
	return [name.split("/", 1)[1] for name in (body or {}).get("resourceNames", []) if "/" in name]


def parse_customer(rows):
	if not rows:
		return None
	row = rows[0]
	return {
		"external_id": str(_get(row, "customer", "id") or ""),
		"account_name": _get(row, "customer", "descriptive_name") or "",
		"currency": _get(row, "customer", "currency_code") or None,
		"manager": bool(_get(row, "customer", "manager")),
	}


def parse_campaigns(rows):
	return [
		{
			"external_id": str(_get(row, "campaign", "id")),
			"campaign_name": _get(row, "campaign", "name") or "",
			"objective": _get(row, "campaign", "advertising_channel_type") or "",
			"platform_status": _get(row, "campaign", "status") or "",
		}
		for row in rows or []
		if _get(row, "campaign", "id")
	]


def parse_metrics(rows):
	"""One row per campaign-day. ``spend`` converted from micros."""
	return [
		{
			"campaign_external_id": str(_get(row, "campaign", "id")),
			"metric_date": _get(row, "segments", "date"),
			"impressions": _int(_get(row, "metrics", "impressions")),
			"clicks": _int(_get(row, "metrics", "clicks")),
			"spend": round(_int(_get(row, "metrics", "cost_micros")) / 1_000_000, 2),
			"conversions": _float(_get(row, "metrics", "conversions")),
		}
		for row in rows or []
		if _get(row, "campaign", "id") and _get(row, "segments", "date")
	]


def parse_clicks(rows):
	return [
		{
			"gclid": _get(row, "click_view", "gclid"),
			"campaign_external_id": str(_get(row, "campaign", "id")),
			"ad_group_id": str(_get(row, "ad_group", "id") or ""),
			"click_date": _get(row, "segments", "date"),
		}
		for row in rows or []
		if _get(row, "click_view", "gclid") and _get(row, "campaign", "id")
	]
