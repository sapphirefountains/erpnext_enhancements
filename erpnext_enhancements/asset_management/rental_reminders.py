# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Customer reminder emails for event rentals (v1.566.0).

Four emails to the booking's contact, **each off until it is turned on** in Rental Settings (Nik,
2026-09-29 — emailing customers automatically is a choice made on purpose, not a default):

* ``remind_week_out`` — seven days before delivery, with the schedule and the portal link;
* ``remind_site_prep`` — ten days out, only if the site details are still blank;
* ``remind_day_before`` — the day before delivery, with the delivery time;
* ``send_thank_you`` — the day after take-down, with ``review_url`` when one is set.

Each is sent once per booking (a date stamp on the booking), only for a live booking in the right
status, and in the email design system. A daily sweep rather than a job per booking, because the
deploy's Redis flush kills queued jobs; a missed morning is caught by the next one for the windows
that are ranges (week out, site prep).
"""

from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate, nowdate

WEEK_OUT_DAYS = 7
SITE_PREP_DAYS = 10


def settings():
	return frappe.get_cached_doc("Rental Settings")


def run_daily():
	conf = settings()
	today = getdate(nowdate())
	if cint(conf.get("remind_week_out")):
		_sweep(
			"reminder_week_sent_on",
			["Confirmed"],
			today,
			add_days(today, WEEK_OUT_DAYS),
			week_out,
		)
	if cint(conf.get("remind_site_prep")):
		_sweep(
			"reminder_prep_sent_on",
			["Tentative", "Confirmed"],
			today,
			add_days(today, SITE_PREP_DAYS),
			site_prep,
			extra=[["site_prep_updated_on", "is", "not set"]],
		)
	if cint(conf.get("remind_day_before")):
		tomorrow = add_days(today, 1)
		_sweep("reminder_day_before_sent_on", ["Confirmed"], tomorrow, tomorrow, day_before)
	if cint(conf.get("send_thank_you")):
		_sweep(
			"thank_you_sent_on",
			["Returned", "Closed"],
			add_days(today, -3),
			add_days(today, -1),
			thank_you,
			field="takedown_datetime",
		)


def _sweep(stamp, statuses, first_day, last_day, send, extra=None, field="delivery_datetime"):
	filters = [
		["status", "in", statuses],
		[field, "is", "set"],
		[field, ">=", get_datetime(first_day)],
		[field, "<", get_datetime(add_days(last_day, 1))],
		[stamp, "is", "not set"],
		*(extra or []),
	]
	for name in frappe.get_all("Rental Booking", filters=filters, pluck="name"):
		try:
			booking = frappe.get_doc("Rental Booking", name)
			if send(booking):
				booking.db_set(stamp, getdate(nowdate()), update_modified=False)
				frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Rental reminder failed for {name}", message=frappe.get_traceback())


def _portal_url(booking):
	return frappe.utils.get_url(f"/rentals?booking={quote(booking.name)}")


def _send(booking, subject, body):
	from erpnext_enhancements import email_style
	from erpnext_enhancements.asset_management.rental_sales import _recipient

	recipient = _recipient(booking)
	if not recipient:
		return False
	frappe.sendmail(
		recipients=[recipient],
		subject=subject,
		message=email_style.wrap(body, title=subject, eyebrow=_("Rentals"), tagline=True, pillar="rent"),
		reference_doctype="Rental Booking",
		reference_name=booking.name,
	)
	return True


def _schedule(booking):
	from erpnext_enhancements import email_style

	rows = [(_("Delivery"), frappe.utils.format_datetime(booking.delivery_datetime))]
	if booking.event_start_datetime:
		rows.append((_("Event"), frappe.utils.format_datetime(booking.event_start_datetime)))
	rows.append((_("Pickup"), frappe.utils.format_datetime(booking.takedown_datetime)))
	return email_style.kv(rows)


def _event(booking):
	return booking.event_name or _("your event")


def week_out(booking):
	from erpnext_enhancements import email_style

	url = _portal_url(booking)
	body = (
		email_style.p(_("Hello,"))
		+ email_style.p(_("{0} is a week away. Here is the plan:").format(_event(booking)))
		+ _schedule(booking)
		+ email_style.p(_("If anything has changed, let us know from your rental page."))
		+ email_style.button(url, _("View my rental"))
		+ email_style.button_fallback(url)
		+ email_style.p(_("Thank you, Sapphire Fountains"))
	)
	return _send(booking, _("One week to go: {0}").format(_event(booking)), body)


def site_prep(booking):
	from erpnext_enhancements import email_style

	url = _portal_url(booking)
	body = (
		email_style.p(_("Hello,"))
		+ email_style.p(
			_(
				"To deliver {0} smoothly our crew needs a few details: who to call on site, the surface the "
				"fountain will stand on, gate codes or parking, and where the nearest outlet and water spigot are."
			).format(_event(booking))
		)
		+ email_style.button(url, _("Add site details"))
		+ email_style.button_fallback(url)
		+ email_style.p(_("Thank you, Sapphire Fountains"))
	)
	return _send(booking, _("A few details for your delivery: {0}").format(_event(booking)), body)


def day_before(booking):
	from erpnext_enhancements import email_style

	body = (
		email_style.p(_("Hello,"))
		+ email_style.p(
			_("We deliver tomorrow for {0}, at {1}.").format(
				_event(booking), frappe.utils.format_time(get_datetime(booking.delivery_datetime))
			)
		)
		+ _schedule(booking)
		+ email_style.p(_("Please make sure the site is open and the area is clear. See you tomorrow."))
		+ email_style.p(_("Thank you, Sapphire Fountains"))
	)
	return _send(booking, _("We deliver tomorrow: {0}").format(_event(booking)), body)


def thank_you(booking):
	from erpnext_enhancements import email_style

	review = (settings().get("review_url") or "").strip()
	body = email_style.p(_("Hello,")) + email_style.p(
		_("Thank you for renting with us for {0}. We hope it was a wonderful event.").format(_event(booking))
	)
	if review:
		body += email_style.p(_("If you have a minute, a review helps other people find us.")) + email_style.button(
			review, _("Leave a review")
		)
	body += email_style.p(_("Thank you, Sapphire Fountains"))
	return _send(booking, _("Thank you from Sapphire Fountains"), body)
