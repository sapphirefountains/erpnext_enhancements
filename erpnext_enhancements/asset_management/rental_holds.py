# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Tentative holds: remind before they lapse, release them when they do (v1.564.0).

A hold (a Tentative Rental Booking) keeps its fountains until ``hold_expires_on``. Every morning
:func:`run_daily`:

1. **reminds** whoever placed each hold that lapses in ``Rental Settings.hold_reminder_days``
   days — an assigned ToDo on the booking, once (``hold_reminder_sent_on``);
2. **releases** every hold whose date has passed: status Expired, which deletes its draft calendar
   legs and frees the fountains. A comment on the booking says why. Gated on
   ``Rental Settings.expire_holds``.

**Why a scheduled sweep and not an enqueued job per hold:** the production deploy flushes the
queue Redis, silently killing anything enqueued. A daily sweep over the table re-derives the
work each morning, so a missed run is caught by the next one. Each booking is handled in its own
savepoint and logged on failure, so one bad record never stops the rest.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate

from erpnext_enhancements.asset_management import rental_rules as rules


def settings():
	return frappe.get_cached_doc("Rental Settings")


def hold_days():
	"""Days a new hold lasts (Rental Settings, falling back to the rules' default)."""
	try:
		days = cint(settings().hold_days)
	except Exception:
		days = 0
	return days or rules.DEFAULT_HOLD_DAYS


def run_daily():
	"""Scheduler entry (daily): remind, then release."""
	conf = settings()
	today = getdate()
	remind_days = cint(conf.hold_reminder_days)
	if remind_days > 0:
		for name in frappe.get_all(
			"Rental Booking",
			# List filters so the date can carry both an "is set" and a range: Frappe coalesces a
			# comparison on a nullable date, and a hold with no date must never match.
			filters=[
				["status", "=", "Tentative"],
				["hold_expires_on", "is", "set"],
				["hold_expires_on", "between", [today, add_days(today, remind_days)]],
				["hold_reminder_sent_on", "is", "not set"],
			],
			pluck="name",
		):
			_each(name, remind)
	if cint(conf.expire_holds):
		for name in frappe.get_all(
			"Rental Booking",
			filters=[
				["status", "=", "Tentative"],
				["hold_expires_on", "is", "set"],
				["hold_expires_on", "<", today],
			],
			pluck="name",
		):
			_each(name, release)


def _each(name, fn):
	savepoint = "rental_hold"
	frappe.db.savepoint(savepoint)
	try:
		fn(frappe.get_doc("Rental Booking", name))
		frappe.db.commit()
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		frappe.log_error(title=f"Rental hold sweep failed for {name}", message=frappe.get_traceback())


def remind(doc):
	"""Assign the booking to whoever placed the hold, once, with the lapse date in the note."""
	from frappe.desk.form.assign_to import add as assign

	owner = doc.owner if frappe.db.get_value("User", doc.owner, "enabled") else None
	if owner and owner not in ("Administrator", "Guest"):
		assign(
			{
				"doctype": "Rental Booking",
				"name": doc.name,
				"assign_to": [owner],
				"description": _("The hold on {0} ({1}) lapses on {2}. Confirm it, renew it or let it go.").format(
					doc.name, doc.customer_name or doc.customer, frappe.utils.formatdate(doc.hold_expires_on)
				),
				"date": doc.hold_expires_on,
			}
		)
	doc.db_set("hold_reminder_sent_on", getdate(), update_modified=False)


def release(doc):
	"""Expire a lapsed hold, which frees its fountains, and say so on the booking."""
	if doc.status != "Tentative":
		return
	lapsed = doc.hold_expires_on
	doc.status = "Expired"
	doc.flags.ignore_permissions = True
	doc.save()
	doc.add_comment(
		"Comment",
		_("The hold lapsed on {0} and its fountains were released. Renew Hold re-checks the dates.").format(
			frappe.utils.formatdate(lapsed)
		),
	)


def send_hold_notice(booking):
	"""Tell the customer what is held and until when (Rental Settings.email_customer_on_hold, off by default).

	Called when a hold is placed or renewed. Never raises: a notice that could not be sent must not
	undo the hold. Logged instead.
	"""
	try:
		if not cint(settings().email_customer_on_hold):
			return
		from erpnext_enhancements import email_style
		from erpnext_enhancements.asset_management.rental_sales import _recipient

		recipient = _recipient(booking)
		if not recipient:
			return
		event = booking.event_name or _("your event")
		subject = _("Fountains held for {0} until {1}").format(event, frappe.utils.formatdate(booking.hold_expires_on))
		held = [row.asset_name or row.asset for row in booking.fountains or []]
		held += [f"{cint(row.qty)} × {row.pool}" for row in booking.accessories or []]
		body = (
			email_style.p(_("Hello,"))
			+ email_style.p(
				_("We are holding the following for {0} until {1}. Sign the rental agreement before then to keep them.").format(
					event, frappe.utils.formatdate(booking.hold_expires_on)
				)
			)
			+ email_style.bullets(held)
			+ email_style.kv(
				[
					(_("Delivery"), frappe.utils.format_datetime(booking.delivery_datetime)),
					(_("Take-down"), frappe.utils.format_datetime(booking.takedown_datetime)),
				]
			)
			+ email_style.p(_("Thank you, Sapphire Fountains"))
		)
		frappe.sendmail(
			recipients=[recipient],
			subject=subject,
			message=email_style.wrap(body, title=subject, eyebrow=_("Rentals"), tagline=True, pillar="rent"),
			reference_doctype="Rental Booking",
			reference_name=booking.name,
		)
	except Exception:
		frappe.log_error(title=f"Rental hold notice not sent for {booking.name}", message=frappe.get_traceback())
