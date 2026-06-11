# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Scheduled travel reminder emails (hooks.py ``scheduler_events.daily``,
registered AFTER tasks.auto_advance_trip_statuses so they see today's
statuses).

Idempotency follows the maintenance-nudge pattern
(``erpnext_enhancements.tasks.nudge_unsubmitted_maintenance_forms``): the
traveler row is stamped (``pre_travel_reminder_sent`` / ``expense_nudge_sent``)
BEFORE the email goes out, so at-most-once delivery holds even when a window
is evaluated twice. Both jobs bail in maintenance context and while
Travel Settings → Send Travel Notifications is off.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, date_diff, flt, getdate, today

from erpnext_enhancements.travel_management import COST_TABLES
from erpnext_enhancements.travel_management.notifications import (
	_base_context,
	_in_maintenance_context,
	_notifications_enabled,
	_send,
	_traveler_recipients,
	send_itinerary_emails,
)

PRE_TRAVEL_WINDOW_DAYS = 2  # ~48h before the traveler's departure date
EXPENSE_NUDGE_AFTER_DAYS = 3  # days after trip end before the single nudge


def send_pre_travel_reminders():
	"""Daily: email each traveler their itinerary (+ ICS) when their personal
	departure date falls within the next ~48 hours."""
	if _in_maintenance_context() or not _notifications_enabled():
		return

	window_end = add_days(today(), PRE_TRAVEL_WINDOW_DAYS)
	trips = frappe.get_all(
		"Travel Trip",
		filters={
			"status": ["in", ("Booked", "In Progress")],
			"start_date": ["<=", window_end],
			"end_date": [">=", today()],
		},
		pluck="name",
	)

	for name in trips:
		try:
			doc = frappe.get_doc("Travel Trip", name)
			for traveler in doc.travelers:
				if cint(traveler.pre_travel_reminder_sent):
					continue
				departure = getdate(traveler.from_date or doc.start_date)
				if not (getdate(today()) <= departure <= getdate(window_end)):
					continue
				# Stamp FIRST — at-most-once even if the send below dies mid-loop.
				frappe.db.set_value(
					"Trip Traveler", traveler.name, "pre_travel_reminder_sent", 1
				)
				send_itinerary_emails(doc, employee=traveler.employee, force=True)
		except Exception:
			frappe.log_error(
				title="Pre-travel reminder failed",
				message=f"{name}\n{frappe.get_traceback()}",
			)


def send_post_trip_expense_nudges():
	"""Daily: single-shot nudge to travelers still owed money once the trip
	ended ≥ EXPENSE_NUDGE_AFTER_DAYS ago (same population as the Unclaimed
	Travel Expenses report). Recurring re-nudges are deliberately v2."""
	if _in_maintenance_context() or not _notifications_enabled():
		return

	cutoff = add_days(today(), -EXPENSE_NUDGE_AFTER_DAYS)
	trips = frappe.get_all(
		"Travel Trip",
		filters={"status": ["in", ("Completed", "Closed")], "end_date": ["<=", cutoff]},
		pluck="name",
	)

	for name in trips:
		try:
			doc = frappe.get_doc("Travel Trip", name)
			pending = {
				t.employee
				for t in doc.travelers
				if not cint(t.expense_nudge_sent) and _unclaimed_total(doc, t) > 0
			}
			if not pending:
				continue
			context = _base_context(doc)
			subject = _("Unclaimed travel expenses: {0}").format(doc.purpose)
			for recipient in _traveler_recipients(doc, employees=pending):
				amount = _unclaimed_total(doc, recipient.row)
				# Stamp first (at-most-once), then send.
				frappe.db.set_value("Trip Traveler", recipient.row.name, "expense_nudge_sent", 1)
				_send(
					recipient,
					subject,
					"expense_nudge.html",
					dict(
						context,
						unclaimed_amount=frappe.format_value(amount, {"fieldtype": "Currency"}),
						days_since_end=date_diff(today(), doc.end_date),
					),
					doc,
				)
		except Exception:
			frappe.log_error(
				title="Post-trip expense nudge failed",
				message=f"{name}\n{frappe.get_traceback()}",
			)


def _unclaimed_total(doc, traveler):
	"""Employee-paid cost rows without a claim stamp + unclaimed mileage +
	unclaimed per diem, for one traveler row."""
	total = 0
	for fieldname in COST_TABLES:
		for row in doc.get(fieldname):
			if (
				row.paid_by == "Employee"
				and row.paid_by_traveler == traveler.employee
				and not row.expense_claim
			):
				total += flt(row.cost)
	for row in doc.mileage:
		if row.traveler == traveler.employee and not row.expense_claim:
			total += flt(row.amount)
	if traveler.per_diem_eligible and not traveler.per_diem_claimed:
		total += flt(traveler.per_diem_amount)
	return total
