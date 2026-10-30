# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Marketing alerts (F3, v1.757.0): nightly at 06:15, one email with whatever is new.

Four checks, each with its threshold on Marketing Settings and each alert throttled by its own key in
**Marketing Alert** (in the database, because the deploy flushes Redis):

* **A sharp week-over-week drop** in sessions or search clicks (Mondays, last week against the week
  before; ``traffic_drop_percent`` from a baseline of at least ``traffic_drop_min_baseline``). Keyed by
  the week, so one drop is one email.
* **A key page out of Google**: Search Console's URL Inspection on each address in ``key_pages``, one
  call a page a night (the API allows 2,000 a day per property). Each result is kept as a Key Page
  Check row; the alert goes when a page Google indexed last time is not indexed now.
* **A Google review at ``low_review_stars`` or fewer**, the night it first appears.
* **A missed first-time call**: an incoming call nobody answered or called back within
  ``missed_call_callback_hours`` (``insights.missed_calls``) from a number never seen before.

**None of this is dormant by accident.** It ships on (``alerts_paused`` ships unticked) with every
figure defaulted, and the defaults reach the existing Marketing Settings row through
``patches/backfill_marketing_insights_settings`` -- a Single never receives a new field's default on
its own (CLAUDE.md). Recipients are the enabled users holding ``alert_roles`` (Marketing Manager by
default), never Administrator, Guest or the service account. A check that fails (Search Console
unreachable, say) is logged and the others still run.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, get_url, getdate, now_datetime, nowdate

from erpnext_enhancements.marketing.performance import insights, rules

ALERT_DOCTYPE = "Marketing Alert"
CHECK_DOCTYPE = "Key Page Check"
GA4 = "GA4 Daily Metric"
GSC = "Search Console Daily Metric"
REVIEW = "Business Profile Review"
#: Search Console's newest final day is two or three days back.
GSC_LAG_DAYS = 3


def _settings():
	from erpnext_enhancements.marketing.core.utils import get_settings

	return get_settings()


def recipients(settings):
	"""Enabled System Users holding an alert role, minus Administrator, Guest and the service account."""
	from erpnext_enhancements.inventory_enhancements.reorder_rules import filter_recipients

	roles = [r.strip() for r in (settings.get("alert_roles") or "").splitlines() if r.strip()]
	if not roles:
		return []
	users = frappe.db.sql(
		f"""select distinct u.name, u.email, u.enabled, u.user_type from `tabUser` u
		join `tabHas Role` r on r.parent = u.name and r.parenttype = 'User'
		where r.role in ({", ".join(["%s"] * len(roles))})""",
		roles,
		as_dict=True,
	)
	return filter_recipients(users)


def _total(doctype, column, start, end):
	"""The sum over ``start..end``, or None unless every day of it is stored.

	A week with its last days not in yet (Search Console's lag, a missed night) would read as a fall
	against a full one, so a partial week is no figure at all.
	"""
	if not frappe.db.exists("DocType", doctype):
		return None
	value, days = frappe.db.sql(
		f"""select sum({column}), count(distinct metric_date) from `tab{doctype}`
		where dimension = 'Total' and metric_date between %s and %s""",
		(start, end),
	)[0]
	return float(value or 0) if days and days >= (end - start).days + 1 else None


def drop_checks(today, settings):
	"""Monday's week-over-week comparison: ``[(key, text)]``."""
	if today.weekday() != 0:
		return []
	share = cint(settings.get("traffic_drop_percent")) / 100.0
	floor = cint(settings.get("traffic_drop_min_baseline"))
	out = []
	week_start, week_end = insights.last_week(today)
	before = (week_start - datetime.timedelta(days=7), week_end - datetime.timedelta(days=7))
	iso = f"{week_end.isocalendar()[0]}-W{week_end.isocalendar()[1]:02d}"
	for label, doctype, column, lag in (
		(_("Sessions"), GA4, "sessions", 0),
		(_("Search clicks"), GSC, "clicks", GSC_LAG_DAYS),
	):
		# Search Console's last days are not final yet: compare the two weeks ending ``lag`` days earlier.
		shift = datetime.timedelta(days=lag)
		this = _total(doctype, column, week_start - shift, week_end - shift)
		last = _total(doctype, column, before[0] - shift, before[1] - shift)
		hit = insights.drop_alert(this, last, share, floor)
		if hit:
			out.append(
				(
					f"drop:{column}:{iso}",
					_("{0} fell {1}% last week: {2} against {3} the week before.").format(
						label, round(-hit["change"] * 100), int(hit["this_week"]), int(hit["last_week"])
					),
				)
			)
	return out


def _inspect(urls, today):
	"""URL Inspection for each key page: ``{url: verdict}``, a Key Page Check row each."""
	from erpnext_enhancements.api.analytics import _gsc_execute, _gsc_service

	settings = frappe.get_doc("GA4 Settings")
	site = frappe.db.get_value(GSC, {"property": ["is", "set"]}, "property", order_by="metric_date desc")
	if not settings.get("gsc_property_url") or not settings.get("credentials_json") or not site:
		# Not set up yet (or no property has answered): nothing to inspect, and nothing to log every night.
		return None
	service, credentials, error = _gsc_service(settings)
	if error:
		return None
	verdicts = {}
	for url in urls:
		key = rules.value_key(url)
		row = {"verdict": None, "coverage_state": None, "last_crawl_time": None, "error": None}
		try:
			body = _gsc_execute(
				service.urlInspection().index().inspect(body={"inspectionUrl": url, "siteUrl": site}),
				credentials,
				"url inspection",
			)
			status = ((body or {}).get("inspectionResult") or {}).get("indexStatusResult") or {}
			row.update(verdict=status.get("verdict"), coverage_state=status.get("coverageState"))
			crawled = status.get("lastCrawlTime")
			if crawled:
				row["last_crawl_time"] = get_datetime(
					crawled.replace("Z", "").split(".")[0].replace("T", " ")
				)
		except Exception as exc:
			row["error"] = f"{type(exc).__name__}: {exc}"[:500]
		name = f"KPC-{today.isoformat()}-{key}"
		if frappe.db.exists(CHECK_DOCTYPE, name):
			frappe.db.set_value(CHECK_DOCTYPE, name, row)
		else:
			frappe.get_doc(
				{"doctype": CHECK_DOCTYPE, "check_date": today, "url": url, "url_key": key, **row}
			).insert(ignore_permissions=True)
		if row["verdict"]:
			verdicts[url] = row["verdict"]
	frappe.db.commit()
	return verdicts


def index_checks(today, settings):
	urls = [u.strip() for u in (settings.get("key_pages") or "").splitlines() if u.strip()]
	if not urls or not frappe.db.exists("DocType", CHECK_DOCTYPE):
		return []
	previous = {}
	for url in urls:
		last = frappe.db.get_value(
			CHECK_DOCTYPE,
			{"url_key": rules.value_key(url), "check_date": ["<", today], "verdict": ["is", "set"]},
			"verdict",
			order_by="check_date desc",
		)
		if last:
			previous[url] = last
	current = _inspect(urls, today)
	if current is None:
		return []
	return [
		(
			f"index:{rules.value_key(hit['url'])}:{hit['verdict']}",
			_("Google no longer indexes {0} (URL Inspection: {1}).").format(
				insights.md_escape(hit["url"]), insights.md_escape(hit["verdict"])
			),
		)
		for hit in insights.index_alerts(previous, current)
	]


def review_checks(today, settings):
	"""Low reviews written in the last two days (Google's ``create_time``, stored in site time since
	v1.756.1, so the window is measured from the site's clock too).

	Not "first seen" by this app: the first night Business Profile answers, every review the listing
	ever had is first seen, and alerting on them would mail its whole history. ``low_review_stars`` 0
	turns this alert off.
	"""
	stars = cint(settings.get("low_review_stars"))
	if stars <= 0 or not frappe.db.exists("DocType", REVIEW):
		return []
	since = now_datetime().replace(microsecond=0) - datetime.timedelta(days=2)
	rows = frappe.get_all(
		REVIEW,
		filters={"first_seen": [">=", add_days(today, -2)], "create_time": [">=", since]},
		fields=["name", "star_rating", "location_title", "comment", "create_time"],
	)
	fresh = insights.new_since([dict(r) for r in rows], since)
	return [
		(
			f"review:{r['name']}",
			_("A {0}-star Google review on {1}: {2}").format(
				r["star_rating"],
				insights.md_escape(r.get("location_title") or _("the listing")),
				insights.md_escape(r.get("comment"), 200),
			),
		)
		for r in insights.low_reviews(fresh, stars)
	]


def call_checks(today, settings):
	from erpnext_enhancements.marketing.performance.calls import call_rows

	calls = call_rows(add_days(today, -2), today)
	missed = insights.missed_calls(calls, now_datetime(), cint(settings.get("missed_call_callback_hours")))
	return [
		(
			f"call:{c['name']}",
			_("A first-time caller was missed at {0} and not called back.").format(str(c["at"])[:16]),
		)
		for c in missed
		if c.get("first_time") and c["state"] == "missed"
	]


def _due(key, now, hours):
	last = frappe.db.get_value(ALERT_DOCTYPE, key, "last_sent_at")
	return insights.alert_due(key, get_datetime(last) if last else None, now, hours)


def _record(key, kind, text, now):
	if frappe.db.exists(ALERT_DOCTYPE, key):
		count = cint(frappe.db.get_value(ALERT_DOCTYPE, key, "sent_count"))
		frappe.db.set_value(
			ALERT_DOCTYPE, key, {"last_sent_at": now, "last_message": text[:1000], "sent_count": count + 1}
		)
	else:
		frappe.get_doc(
			{
				"doctype": ALERT_DOCTYPE,
				"alert_key": key,
				"alert_type": kind,
				"last_sent_at": now,
				"last_message": text[:1000],
				"sent_count": 1,
			}
		).insert(ignore_permissions=True)


def run_alerts(today=None):
	"""Cron entry (hooks.py, 06:15). Returns the alerts sent."""
	if not frappe.db.exists("DocType", ALERT_DOCTYPE):
		return []
	settings = _settings()
	if cint(settings.get("alerts_paused")):
		return []
	today = getdate(today or nowdate())
	now = now_datetime()
	found = []
	for kind, check in (
		("Traffic drop", drop_checks),
		("Key page", index_checks),
		("Review", review_checks),
		("Missed call", call_checks),
	):
		try:
			found += [(key, kind, text) for key, text in check(today, settings)]
		except Exception as exc:
			frappe.db.rollback()
			frappe.log_error(
				title="Marketing alerts", message=f"{kind} check failed: {type(exc).__name__}: {exc}"
			)
	hours = cint(settings.get("alert_throttle_hours"))
	due = [(key, kind, text) for key, kind, text in found if _due(key, now, hours)]
	to = recipients(settings)
	if not due or not to:
		return []
	from erpnext_enhancements import email_style

	body = "\n".join(f"- **{kind}:** {text}" for _key, kind, text in due)
	body += f"\n\n[Open Marketing Performance]({get_url('/desk/marketing-performance')})"
	frappe.sendmail(
		recipients=to,
		# The kind, never the text: the text may hold a reviewer's words.
		subject=_("Marketing alert: {0}").format(_(due[0][1]))
		if len(due) == 1
		else _("Marketing alerts: {0}").format(len(due)),
		message=email_style.wrap(email_style.markdown(body), title=_("Marketing alerts"), eyebrow=str(today)),
	)
	for key, kind, text in due:
		_record(key, kind, text, now)
	frappe.db.commit()
	return [text for _key, _kind, text in due]
