# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Subscribable calendar feeds of the rental fleet (v1.567.0).

An ``.ics`` feed per fountain, and one for the whole fleet, that Google Calendar or a phone
subscribes to by URL. Read-only and one-way: nothing written in a calendar comes back.

**The URL is the credential.** A calendar app fetches a subscription with no login, so the feed is
``allow_guest`` and guarded by a secret key in the query string, compared in constant time against
``Rental Settings.calendar_feed_key`` (a Password field, stored encrypted). The events name
customers, so the key is 256 bits from :func:`secrets.token_urlsafe`, it is never shown in a list or
a log, and **Rotate** in Rental Settings replaces it — every subscription made with the old one stops
at its next refresh. No key set means no feed at all.

What is on it: every live calendar entry of the fleet (or of one fountain) from 30 days ago to a
year ahead — the Rental Booking legs (held ones marked "held"), bookings made by hand, and
out-of-service periods. UIDs are the Asset Booking / out-of-service names, so an entry that moves
updates in place, and one that is released simply drops out at the next refresh.
"""

import hmac
import secrets
from urllib.parse import quote

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, get_datetime, get_url, nowdate

KEY_FIELD = "calendar_feed_key"
FEED_METHOD = "erpnext_enhancements.asset_management.rental_calendar.feed"
PAST_DAYS = 30
FUTURE_DAYS = 365
MANAGE_ROLES = ("System Manager", "Operations Team")


def _stored_key():
	try:
		return frappe.get_doc("Rental Settings").get_password(KEY_FIELD, raise_exception=False) or ""
	except Exception:
		return ""


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=120, seconds=3600, methods=["GET"])
def feed(feed_key=None, asset=None):
	"""The ``.ics`` feed. 403 on a missing or wrong key, and the same 403 when no key is set."""
	stored = _stored_key()
	if not stored or not feed_key or not hmac.compare_digest(str(feed_key), stored):
		frappe.local.response["http_status_code"] = 403
		return _("Not found.")
	from erpnext_enhancements.travel_management.ics import build_ics

	events = fleet_events(asset or None)
	frappe.local.response["type"] = "download"
	frappe.local.response["filename"] = f"sapphire-rentals{'-' + asset if asset else ''}.ics"
	frappe.local.response["content_type"] = "text/calendar; charset=utf-8"
	frappe.local.response["display_content_as"] = "inline"
	frappe.local.response["filecontent"] = build_ics(events)


def fleet_events(asset=None):
	start = get_datetime(add_days(nowdate(), -PAST_DAYS))
	end = get_datetime(add_days(nowdate(), FUTURE_DAYS))
	params = {"start": start, "end": end, "asset": asset or ""}
	host = frappe.local.site or "erpnext"
	events = []
	for row in frappe.db.sql(
		"""
		select ab.name, ab.asset, ab.booking_type, ab.rental_leg, ab.docstatus, ab.from_datetime,
			ab.to_datetime, ab.rental_booking, rb.customer_name, rb.event_name, a.asset_name
		from `tabAsset Booking` ab
		left join `tabRental Booking` rb on rb.name = ab.rental_booking
		left join `tabAsset` a on a.name = ab.asset
		where ab.docstatus < 2
			and ab.from_datetime < %(end)s and ab.to_datetime > %(start)s
			and (%(asset)s = '' or ab.asset = %(asset)s)
		""",
		params,
		as_dict=True,
	):
		fountain = row.asset_name or row.asset
		if row.rental_booking:
			what = row.rental_leg if row.rental_leg != "Rental" else (row.customer_name or row.rental_booking)
			summary = f"{fountain}: {what}"
			if row.rental_leg == "Rental" and row.event_name:
				summary += f" ({row.event_name})"
			if row.docstatus == 0:
				summary += " — held"
			link = get_url(f"/desk/rental-booking/{row.rental_booking}")
		else:
			summary = f"{fountain}: {row.booking_type}"
			link = get_url(f"/desk/asset-booking/{row.name}")
		events.append(
			{"uid": f"{row.name}@{host}", "summary": summary, "start": row.from_datetime, "end": row.to_datetime, "url": link}
		)
	for row in frappe.db.sql(
		"""
		select o.name, o.asset, o.reason, o.out_from, o.blocked_until, a.asset_name
		from `tabAsset Out of Service` o
		left join `tabAsset` a on a.name = o.asset
		where o.out_from < %(end)s and (o.blocked_until is null or o.blocked_until > %(start)s)
			and (%(asset)s = '' or o.asset = %(asset)s)
		""",
		params,
		as_dict=True,
	):
		events.append(
			{
				"uid": f"{row.name}@{host}",
				"summary": f"{row.asset_name or row.asset}: out of service ({row.reason})",
				"start": row.out_from,
				# Open-ended: shown to the end of the feed's window.
				"end": row.blocked_until or end,
				"url": get_url(f"/desk/asset-out-of-service/{row.name}"),
			}
		)
	return events


@frappe.whitelist(methods=["POST"])
def feed_links(rotate=0):
	"""Rental Settings' Calendar Feeds button: the fleet link and one per fountain; ``rotate`` makes a new key."""
	frappe.only_for(MANAGE_ROLES)
	settings = frappe.get_doc("Rental Settings")
	key = _stored_key()
	if not key or str(rotate) in ("1", "true", "True"):
		key = secrets.token_urlsafe(32)
		settings.set(KEY_FIELD, key)
		settings.flags.ignore_permissions = True
		settings.save()
	base = get_url(f"/api/method/{FEED_METHOD}?feed_key={key}")
	from erpnext_enhancements.asset_management.rental_availability import fountain_profiles

	return {
		"fleet": base,
		"fountains": [
			{"asset": p.name, "asset_name": p.asset_name, "url": f"{base}&asset={quote(p.name)}"}
			for p in fountain_profiles(rentable_only=True).values()
		],
	}
