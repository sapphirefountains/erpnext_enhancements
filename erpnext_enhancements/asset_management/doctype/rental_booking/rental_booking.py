# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for Rental Booking — one event rental of one or more fountains and accessories.

A booking is **not submittable**; its lifecycle is ``status`` (see
:mod:`~erpnext_enhancements.asset_management.rental_rules` for the transitions), changed
through :meth:`RentalBooking.set_status`, the buttons at the top of the form.

What a save does:

1. **Checks the schedule** and fills it from the Project when the booking is new and blank.
2. **Checks availability** — every fountain against its own calendar (Asset Bookings of any
   type, from any other booking) and against out-of-service records, and every accessory
   against its pool's peak usage — with the Assets and pools row-locked first, so two
   people cannot both take the last one. It refuses with every problem listed, not the first.
3. **Syncs each fountain's calendar** (``rental_availability.sync_calendar``): the booking's
   Asset Booking legs are created, moved, submitted or released to match.
4. **Mirrors the schedule onto the Project**, which shows those fields read-only while this
   booking is live.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, getdate, now_datetime

from erpnext_enhancements.asset_management import rental_availability as availability
from erpnext_enhancements.asset_management import rental_holds
from erpnext_enhancements.asset_management import rental_rules as rules

#: Fields whose change can move what the booking holds, so a save re-checks availability.
SCHEDULE_FIELDS = ("delivery_datetime", "takedown_datetime")


class RentalBooking(Document):
	def onload(self):
		# The form draws its status buttons from this, so the legal moves live in one place.
		self.set_onload("next_statuses", list(rules.TRANSITIONS.get(self.status, ())))

	def validate(self):
		before = self.get_doc_before_save()
		old_status = before.status if before else None

		self.validate_status_change(old_status)
		if before and old_status in rules.FROZEN_STATUSES and _fingerprint(self) != _fingerprint(before):
			frappe.throw(
				_("A {0} booking cannot be changed. Renew the hold or start a new booking.").format(_(old_status)),
				title=_("Booking closed"),
			)

		self.fill_from_project()
		self.validate_schedule()
		self.set_line_details()
		self.validate_lines()
		self.validate_one_live_booking_per_project()
		self.set_hold_and_confirmation(old_status)
		self.set_totals()
		self.set_title()

		if self.status not in rules.RELEASED_STATUSES and self.availability_needs_checking(before, old_status):
			self.check_availability()

	def on_update(self):
		availability.sync_calendar(self)
		if self.status not in rules.RELEASED_STATUSES:
			availability.push_schedule_to_project(self)
		before = self.get_doc_before_save()
		if (
			self.status == "Tentative"
			and (before is None or before.status == "Expired")
			and not self.flags.skip_hold_notice
		):
			rental_holds.send_hold_notice(self)
		if self.status == "Confirmed" and (before is None or before.status != "Confirmed"):
			# Draft the deposit invoice (v1.564.0). Its own savepoint: it never stops a confirm.
			from erpnext_enhancements.asset_management import rental_sales

			rental_sales.after_confirmed(self)

	def on_trash(self):
		if self.status not in ("Tentative", "Expired", "Canceled"):
			frappe.throw(
				_("Only a tentative, expired or canceled booking can be deleted. Cancel this one first."),
				title=_("Cannot delete"),
			)
		availability.release_all(self)

	# ------------------------------------------------------------------ status

	def validate_status_change(self, old_status):
		if not rules.can_transition(old_status, self.status):
			if old_status is None:
				frappe.throw(_("A new booking starts as Tentative or Confirmed."))
			frappe.throw(
				_("A booking cannot go from {0} to {1}.").format(_(old_status), _(self.status)),
				title=_("Status"),
			)

	@frappe.whitelist()
	def set_status(self, status):
		"""Move the booking to ``status`` — the form's action buttons call this."""
		self.check_permission("write")
		if not rules.can_transition(self.status, status):
			frappe.throw(_("A booking cannot go from {0} to {1}.").format(_(self.status), _(status)))
		self.status = status
		self.save()
		return self.status

	def set_hold_and_confirmation(self, old_status):
		if self.status == "Tentative":
			today = getdate()
			renewing = old_status == "Expired"
			if not self.hold_expires_on or (renewing and getdate(self.hold_expires_on) <= today):
				self.hold_expires_on = rules.default_hold_expiry(today, rental_holds.hold_days())
			if renewing:
				# A renewed hold gets its own reminder.
				self.hold_reminder_sent_on = None
		if self.status == "Confirmed" and old_status != "Confirmed" and not self.confirmed_on:
			self.confirmed_on = now_datetime()

	# ------------------------------------------------------------------ schedule

	def fill_from_project(self):
		"""On a new booking, take a blank customer and schedule from the Project."""
		if not self.project or not self.is_new():
			return
		if not self.customer:
			self.customer = frappe.db.get_value("Project", self.project, "customer")
		for field, value in availability.project_schedule(self.project).items():
			if not self.get(field):
				self.set(field, value)

	def validate_schedule(self):
		problems = rules.schedule_problems(
			self._dt(self.delivery_datetime),
			self._dt(self.takedown_datetime),
			setup=self._dt(self.setup_datetime),
			event_start=self._dt(self.event_start_datetime),
			event_end=self._dt(self.event_end_datetime),
		)
		if problems:
			frappe.throw("<br>".join(_(p) for p in problems), title=_("Check the schedule"))

	@staticmethod
	def _dt(value):
		return get_datetime(value) if value else None

	# ------------------------------------------------------------------ lines

	def set_line_details(self):
		"""Copy each fountain's name, model and buffers from the Asset, and set every block window.

		Buffers are re-read on every save rather than typed per booking: the turnaround is a
		fact about the fountain, and editing it on the Asset should reach the next save of
		every booking that holds it.
		"""
		delivery, takedown = self._dt(self.delivery_datetime), self._dt(self.takedown_datetime)
		profiles = availability.fountain_profiles([row.asset for row in self.fountains or []])
		for row in self.fountains or []:
			profile = profiles.get(row.asset)
			if not profile:
				continue
			row.asset_name = profile.asset_name
			row.item_code = profile.item_code
			row.prep_hours = profile.prep_hours
			row.turnaround_hours = profile.turnaround_hours
			legs = rules.leg_windows(delivery, takedown, row.prep_hours, row.turnaround_hours)
			row.block_from, row.block_to = rules.block_span(legs)

		for row in self.accessories or []:
			_label, _capacity, turnaround, _disabled = availability.pool_capacity(row.pool)
			row.block_from, row.block_to = rules.pool_window(delivery, takedown, turnaround)

	def validate_lines(self):
		problems = []
		profiles = availability.fountain_profiles([row.asset for row in self.fountains or []])
		seen = set()
		for row in self.fountains or []:
			profile = profiles.get(row.asset)
			if row.asset in seen:
				problems.append(_("Row {0}: {1} is on this booking twice.").format(row.idx, row.asset))
			seen.add(row.asset)
			if not profile:
				problems.append(_("Row {0}: fountain {1} does not exist.").format(row.idx, row.asset))
			elif not profile.rentable:
				problems.append(
					_("Row {0}: {1} is not marked Available for Event Rental on the Asset.").format(
						row.idx, profile.asset_name or row.asset
					)
				)
			elif profile.status in availability.GONE_ASSET_STATUSES:
				problems.append(
					_("Row {0}: {1} is {2}.").format(row.idx, profile.asset_name or row.asset, _(profile.status))
				)

		seen = set()
		for row in self.accessories or []:
			if row.pool in seen:
				problems.append(_("Accessory row {0}: {1} is listed twice.").format(row.idx, row.pool))
			seen.add(row.pool)
			if cint(row.qty) <= 0:
				problems.append(_("Accessory row {0}: quantity has to be at least 1.").format(row.idx))

		if not (self.fountains or self.accessories):
			problems.append(_("Add at least one fountain or accessory."))
		if problems:
			frappe.throw("<br>".join(problems), title=_("Check the lines"))

	def validate_one_live_booking_per_project(self):
		if not self.project or self.status in rules.RELEASED_STATUSES:
			return
		other = frappe.db.get_value(
			"Rental Booking",
			{"project": self.project, "name": ["!=", self.name], "status": ["in", rules.HOLDING_STATUSES]},
			"name",
		)
		if other:
			frappe.throw(
				_("Project {0} already has rental {1}. Add the fountains to that booking instead.").format(
					self.project, other
				),
				title=_("One rental per project"),
			)

	# ------------------------------------------------------------------ availability

	def availability_needs_checking(self, before, old_status):
		"""Whether this save could take something it does not already hold.

		Not every save: a fountain can go out of service *after* a booking took it, and
		re-checking on each save would then refuse "Mark Returned" or a note edit on that
		booking. The out-of-service record flags the booking instead (``Asset Out of
		Service.after_insert``). What is checked is anything that can take *more*: a new
		booking, a changed schedule or lines, renewing an expired hold, and confirming.
		"""
		if before is None:
			return True
		if old_status in rules.RELEASED_STATUSES:
			return True
		if old_status == "Tentative" and self.status == "Confirmed":
			return True
		return _fingerprint(self) != _fingerprint(before)

	def check_availability(self):
		delivery, takedown = self._dt(self.delivery_datetime), self._dt(self.takedown_datetime)
		availability.lock_rows("Asset", [row.asset for row in self.fountains or []])
		availability.lock_rows("Rental Accessory Pool", [row.pool for row in self.accessories or []])

		problems = []
		for row in self.fountains or []:
			legs = rules.leg_windows(delivery, takedown, row.prep_hours, row.turnaround_hours)
			for conflict in availability.asset_conflicts(row.asset, legs, exclude_booking=self.name):
				until = frappe.utils.format_datetime(conflict["to"]) if conflict["to"] else _("further notice")
				problems.append(
					_("{0} is taken by {1}, {2} to {3}.").format(
						frappe.bold(row.asset_name or row.asset),
						conflict["label"],
						frappe.utils.format_datetime(conflict["from"]),
						until,
					)
				)

		for row in self.accessories or []:
			label, capacity, turnaround, disabled = availability.pool_capacity(row.pool)
			if disabled:
				problems.append(_("{0} is disabled and cannot be booked.").format(frappe.bold(label)))
				continue
			start, end = rules.pool_window(delivery, takedown, turnaround)
			peak = availability.pool_peak(row.pool, start, end, exclude_booking=self.name)
			problem = rules.pool_problem(label, row.qty, capacity, peak)
			if problem:
				problems.append(_(problem))

		if problems:
			frappe.throw(
				"<br>".join(problems),
				title=_("Not available for these dates"),
			)

	# ------------------------------------------------------------------ money + title

	def set_totals(self):
		self.total_amount = rules.booking_total(
			[row.rate for row in self.fountains or []],
			[(row.rate, row.qty) for row in self.accessories or []],
			[self.delivery_setup_fee, self.pickup_removal_fee, self.other_fee],
		)

	def set_title(self):
		who = self.customer_name or self.customer or _("Rental")
		what = self.event_name or (
			frappe.utils.formatdate(self.delivery_datetime) if self.delivery_datetime else ""
		)
		self.title = f"{who} — {what}" if what else who


def _fingerprint(doc):
	"""Everything that decides what a booking holds, as a comparable string."""
	return json.dumps(
		{
			"schedule": [str(get_datetime(doc.get(f))) if doc.get(f) else None for f in SCHEDULE_FIELDS],
			"fountains": sorted(row.asset or "" for row in doc.get("fountains") or []),
			"accessories": sorted(
				f"{row.pool}:{cint(row.qty)}" for row in doc.get("accessories") or []
			),
		},
		sort_keys=True,
	)

