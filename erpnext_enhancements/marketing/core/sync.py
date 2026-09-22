# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ad-spend sync engine: accounts -> campaigns -> campaign-day metrics (-> clicks).

Platform-agnostic: it drives the four-read contract in ``marketing/platforms`` and never
branches on which platform it is talking to, except to ask whether it has clicks.

The rules, each of which exists because the alternative fails quietly:

* **Restate, then upsert.** Every run re-pulls the last ``restate_days`` (Marketing
  Settings, default 7) because platforms revise closed days. ``Ad Daily Metric`` is named
  from (campaign, date), so a re-pull updates in place -- see ``ad_daily_metric.upsert``.
* **The cursor advances only on a clean run.** ``Ad Account.sync_cursor`` is the last day
  known to be complete. Any failure for an account leaves it where it was, so the next run
  reprocesses the same window -- the QBO CDC and MDM sync convention. A cursor that moved
  past a failed day would leave a hole nothing ever goes back for.
* **One account's failure is one account's problem.** Accounts are committed one at a time;
  a dead Meta token does not cost the Google numbers.
* **A dead credential stops the platform, not the job, and says so once.** 401 marks the
  platform *Auth Failed* on Marketing Credentials; the nightly shim skips it until somebody
  reconnects, so bad credentials never become a nightly error storm.
* **Nothing secret is written.** Raw payloads are archived with redacted URLs, and every
  error string has been through ``utils.redact_text`` by the time it gets here.
"""

from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.core.client import MarketingAPIError, Transport
from erpnext_enhancements.marketing.core.utils import click_days, field, sync_window


def _frappe():
	import frappe

	return frappe


# ---------------------------------------------------------------- session


def open_transport(platform, settings, creds, archive=None, http=None):
	"""An authorized, allowlisted transport for one platform."""
	from erpnext_enhancements.marketing.core import oauth
	from erpnext_enhancements.marketing.core.utils import get_secret
	from erpnext_enhancements.marketing.platforms import module_for

	token = oauth.access_token(platform, creds, http=http)
	module = module_for(platform)
	if platform == C.PLATFORM_GOOGLE:
		developer_token = get_secret(creds, field(platform, "developer_token"))
		if not developer_token:
			raise MarketingAPIError(platform, "Google Ads developer token is not set", status=401)
		headers = module.headers(token, developer_token, creds.get(field(platform, "login_customer_id")))
	else:
		headers = module.headers(token)
	return Transport(
		platform,
		headers=headers,
		max_retries=settings.get("max_retries") or 3,
		timeout=settings.get("request_timeout_seconds") or 30,
		archive=archive,
		http=http,
	)


# ---------------------------------------------------------------- records


def account_name(platform, external_id):
	return f"ADACC-{platform}-{external_id}"


def campaign_name(account, external_id):
	return f"ADCMP-{account}-{external_id}"


def upsert_account(platform, row):
	"""Create or refresh an Ad Account. Never re-enables one a human switched off."""
	frappe = _frappe()
	name = account_name(platform, row["external_id"])
	if frappe.db.exists("Ad Account", name):
		doc = frappe.get_doc("Ad Account", name)
		doc.account_name = row.get("account_name") or doc.account_name
		if row.get("currency") and frappe.db.exists("Currency", row["currency"]):
			doc.currency = row["currency"]
		doc.save(ignore_permissions=True)
		return doc
	doc = frappe.get_doc(
		{
			"doctype": "Ad Account",
			"platform": platform,
			"external_id": row["external_id"],
			"account_name": row.get("account_name"),
			"currency": row.get("currency")
			if row.get("currency") and frappe.db.exists("Currency", row["currency"])
			else None,
			"enabled": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def upsert_campaign(account, row):
	"""Create or refresh an Ad Campaign. Returns (name, created)."""
	frappe = _frappe()
	name = campaign_name(account.name, row["external_id"])
	values = {
		k: row.get(k)
		for k in ("campaign_name", "objective", "platform_status", "start_date", "end_date")
		if k in row
	}
	if frappe.db.exists("Ad Campaign", name):
		doc = frappe.get_doc("Ad Campaign", name)
		doc.update(values)
		doc.save(ignore_permissions=True)
		return name, False
	frappe.get_doc(
		{"doctype": "Ad Campaign", "ad_account": account.name, "external_id": row["external_id"], **values}
	).insert(ignore_permissions=True)
	return name, True


def upsert_click(account, campaign, row):
	frappe = _frappe()
	values = {
		"campaign": campaign,
		"ad_account": account.name,
		"click_date": row["click_date"],
		"ad_group_id": row.get("ad_group_id"),
	}
	if frappe.db.exists("Ad Click", row["gclid"]):
		frappe.db.set_value("Ad Click", row["gclid"], values, update_modified=False)
		return False
	frappe.get_doc({"doctype": "Ad Click", "gclid": row["gclid"], **values}).insert(ignore_permissions=True)
	return True


# ---------------------------------------------------------------- one account


def sync_account(platform, module, transport, account, window, today, counters):
	"""Campaigns, metrics and (Google) clicks for one account over ``window``.

	Raises on any platform failure; the caller decides what that means for the cursor.
	"""
	from erpnext_enhancements.marketing.doctype.ad_daily_metric.ad_daily_metric import upsert as upsert_metric

	date_from, date_to = window
	known = {}
	for row in module.fetch_campaigns(transport, account.external_id):
		name, created = upsert_campaign(account, row)
		known[row["external_id"]] = name
		counters["created" if created else "updated"] += 1

	for row in module.fetch_daily_metrics(transport, account.external_id, date_from, date_to):
		campaign = known.get(row["campaign_external_id"])
		if not campaign:
			# A campaign the campaign list did not return (removed since): keep its spend.
			campaign, _ = upsert_campaign(account, {"external_id": row["campaign_external_id"]})
			known[row["campaign_external_id"]] = campaign
		upsert_metric(
			campaign,
			row["metric_date"],
			{
				"ad_account": account.name,
				"platform": platform,
				"currency": account.currency,
				"impressions": row["impressions"],
				"clicks": row["clicks"],
				"spend": row["spend"],
				"conversions": row["conversions"],
			},
		)
		counters["updated"] += 1

	if hasattr(module, "fetch_clicks"):
		for day in click_days(date_from, date_to, today):
			for row in module.fetch_clicks(transport, account.external_id, day):
				campaign = known.get(row["campaign_external_id"])
				if not campaign:
					campaign, _ = upsert_campaign(account, {"external_id": row["campaign_external_id"]})
					known[row["campaign_external_id"]] = campaign
				counters["created" if upsert_click(account, campaign, row) else "updated"] += 1


# ---------------------------------------------------------------- one platform


def run_platform(platform, *, http=None, today=None):
	"""Sync every enabled account on ``platform``. Returns the Marketing Sync Log name."""
	from frappe.utils import getdate, now_datetime

	from erpnext_enhancements.marketing.core.utils import get_credentials, get_settings
	from erpnext_enhancements.marketing.platforms import module_for

	frappe = _frappe()
	settings = get_settings()
	creds = get_credentials()
	today = today or getdate(now_datetime())
	module = module_for(platform)

	log = frappe.get_doc(
		{
			"doctype": "Marketing Sync Log",
			"platform": platform,
			"sync_type": "Daily Metrics",
			"status": "Running",
			"started_at": now_datetime(),
		}
	).insert(ignore_permissions=True)
	frappe.db.commit()

	counters = {"created": 0, "updated": 0, "failed": 0}
	errors = []

	def archive(platform_, method, url, status, text):
		frappe.get_doc(
			{
				"doctype": "Marketing Raw Payload",
				"platform": platform_,
				"endpoint": f"{method} {url}"[:140],
				"response_code": status,
				"sync_log": log.name,
				"fetched_at": now_datetime(),
				"payload": (text or "")[: C.RAW_PAYLOAD_MAX_CHARS],
			}
		).insert(ignore_permissions=True)

	try:
		transport = open_transport(platform, settings, creds, archive=archive, http=http)
		discovered = module.discover_accounts(transport)
	except MarketingAPIError as exc:
		_mark_connection(creds, platform, exc)
		return _finish(log, counters, [str(exc)], failed=True)

	for row in discovered:
		upsert_account(platform, row)
	frappe.db.commit()

	accounts = frappe.get_all(
		"Ad Account",
		filters={"platform": platform, "enabled": 1},
		fields=["name", "external_id", "currency", "sync_cursor"],
	)
	for account in accounts:
		window = sync_window(
			getdate(account.sync_cursor) if account.sync_cursor else None,
			today,
			settings.get("restate_days") or 7,
			settings.get("initial_backfill_days") or 90,
		)
		if not window:
			continue
		try:
			sync_account(platform, module, transport, account, window, today, counters)
		except MarketingAPIError as exc:
			frappe.db.rollback()
			counters["failed"] += 1
			errors.append(f"{account.name}: {exc}")
			frappe.db.set_value(
				"Ad Account",
				account.name,
				{"last_error_at": now_datetime(), "status_message": str(exc)[:500]},
				update_modified=False,
			)
			frappe.db.commit()
			if exc.is_auth_failure:
				_mark_connection(creds, platform, exc)
				break
			continue
		# Clean run for this account: only now may the cursor move.
		frappe.db.set_value(
			"Ad Account",
			account.name,
			{
				"sync_cursor": window[1],
				"last_synced_on": now_datetime(),
				"connection_status": "Connected",
				"status_message": "",
			},
			update_modified=False,
		)
		frappe.db.commit()

	return _finish(log, counters, errors, failed=bool(errors))


def _mark_connection(creds, platform, exc):
	frappe = _frappe()
	from frappe.utils import now_datetime

	if exc.is_auth_failure:
		creds.set(field(platform, "connection_status"), "Auth Failed")
	creds.set(field(platform, "status_message"), str(exc)[:500])
	creds.set(field(platform, "last_error_at"), now_datetime())
	creds.save(ignore_permissions=True)
	frappe.db.commit()


def _finish(log, counters, errors, failed):
	frappe = _frappe()
	from frappe.utils import now_datetime

	log.reload()
	log.status = "Failed" if failed else "Completed"
	log.finished_at = now_datetime()
	log.created_count = counters["created"]
	log.updated_count = counters["updated"]
	log.failed_count = counters["failed"]
	log.error_message = "\n".join(errors)[:10000]
	log.save(ignore_permissions=True)
	frappe.db.commit()
	return log.name


# ---------------------------------------------------------------- housekeeping


def prune(today=None):
	"""Drop raw payloads past retention, and clicks past retention that no Lead carries."""
	from frappe.utils import add_days, getdate, now_datetime

	from erpnext_enhancements.marketing.core.utils import get_settings

	frappe = _frappe()
	today = today or getdate(now_datetime())
	retention = get_settings().get("raw_payload_retention_days") or 90
	frappe.db.sql(
		"delete from `tabMarketing Raw Payload` where creation < %s", (add_days(today, -int(retention)),)
	)
	if frappe.db.has_column("Lead", "custom_gclid"):
		frappe.db.sql(
			"""delete from `tabAd Click`
			where click_date < %s
			  and name not in (select custom_gclid from `tabLead` where coalesce(custom_gclid, '') != '')""",
			(add_days(today, -C.CLICK_RETENTION_DAYS),),
		)
	frappe.db.commit()
