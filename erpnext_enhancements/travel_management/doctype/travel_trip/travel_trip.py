# -*- coding: utf-8 -*-
# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Server-side controller for the **Travel Trip** doctype.

A Travel Trip is the non-submittable hub of the Travel Management module:
a crew of travelers (``travelers`` -> Trip Traveler), logistics child tables
(``flights`` / ``accommodations`` / ``ground_transport`` / ``other_costs`` /
``mileage``) and an itinerary (``itinerary`` -> Trip Agenda). The lifecycle is
a plain ``status`` Select (Planning -> Booked -> In Progress -> Completed ->
Closed); In Progress and Completed are auto-advanced from the trip dates by
``travel_management.tasks.auto_advance_trip_statuses``, Booked and Closed are
manual. There is deliberately no Workflow and no submit step — trips are
edited collaboratively until Closed.

Expense Claims / Employee Advances / Vehicle Logs are created explicitly via
``travel_management.api`` (never as a save side-effect) and link back through
their ``custom_travel_trip`` custom field; their status is mirrored onto the
traveler rows by ``travel_management.integrations``.

IMPORTANT: the parent must never grow an ``employee`` field. The Employee
dashboard's Travel Trip count resolves the default ``employee`` fieldname
through the Trip Traveler child table (frappe ``get_filter`` falls back to
child-table metas) — a parent field with that name would silently zero it.

Form behaviour is layered on by ``public/js/travel_trip.js`` and the map by
``public/js/travel/travel_trip_map.js`` (hooks.py ``doctype_js``).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate, now_datetime

from erpnext_enhancements.travel_management import (
	COST_TABLES,
	RELATED_PARTY_DOCTYPES,
	TRAVEL_COORDINATOR_ROLES,
	TRAVEL_FOR_DOCTYPES,
)

# Status transitions a plain Employee may perform manually. Coordinators are
# unrestricted; the date-driven transitions belong to the daily job.
EMPLOYEE_ALLOWED_TRANSITIONS = {
	("Planning", "Booked"),
	("Booked", "Planning"),
	("Completed", "Closed"),
}


def get_travel_settings():
	return frappe.get_cached_doc("Travel Settings")


def user_is_travel_coordinator(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(TRAVEL_COORDINATOR_ROLES & set(frappe.get_roles(user)))


class TravelTrip(Document):
	"""Validation pipeline + financial rollups; document creation lives in api.py."""

	def validate(self):
		self._before = self.get_doc_before_save()
		self._check_closed_lock()
		self._validate_dates()
		self._validate_travel_for()
		self._validate_travelers()
		self._validate_cost_rows()
		self._compute_mileage()
		self._compute_per_diem()
		self._compute_rollups()
		self._handle_status_change()

	def on_trash(self):
		"""Block deletion while submitted financial documents point here;
		unlink drafts (and Leads/Opportunities, which are provenance only).

		Every query is guarded by ``has_column``: the ``custom_travel_trip``
		back-link fields are fixture-managed Custom Fields and may not exist
		yet mid-migrate or on a partially set-up site.
		"""
		linked = []
		for doctype in ("Expense Claim", "Employee Advance", "Vehicle Log"):
			if not frappe.db.has_column(doctype, "custom_travel_trip"):
				continue
			for row in frappe.get_all(
				doctype,
				filters={"custom_travel_trip": self.name},
				fields=["name", "docstatus"],
			):
				if row.docstatus == 1:
					linked.append(f"{doctype} {row.name}")
				else:
					frappe.db.set_value(doctype, row.name, "custom_travel_trip", None)
		if linked:
			frappe.throw(
				_("Cannot delete {0}: submitted documents are linked to it ({1}). Cancel them first.").format(
					self.name, ", ".join(linked)
				)
			)
		for doctype in ("Lead", "Opportunity"):
			if not frappe.db.has_column(doctype, "custom_travel_trip"):
				continue
			frappe.db.set_value(
				doctype, {"custom_travel_trip": self.name}, "custom_travel_trip", None
			)

	# ------------------------------------------------------------------ dates

	def _validate_dates(self):
		if getdate(self.end_date) < getdate(self.start_date):
			frappe.throw(_("End Date cannot be before Start Date."))

		start, end = getdate(self.start_date), getdate(self.end_date)
		clamped = []
		for traveler in self.travelers:
			traveler.from_date = traveler.from_date or self.start_date
			traveler.to_date = traveler.to_date or self.end_date
			if getdate(traveler.from_date) < start:
				traveler.from_date = self.start_date
				clamped.append(traveler.employee_name or traveler.employee)
			if getdate(traveler.to_date) > end:
				traveler.to_date = self.end_date
				clamped.append(traveler.employee_name or traveler.employee)
			if getdate(traveler.to_date) < getdate(traveler.from_date):
				frappe.throw(
					_("Traveler {0}: To Date cannot be before From Date.").format(
						traveler.employee_name or traveler.employee
					)
				)
		if clamped:
			frappe.msgprint(
				_("Traveler dates were clamped to the trip dates for: {0}").format(
					", ".join(sorted(set(clamped)))
				),
				alert=True,
			)

		out_of_range = []
		for row in self.mileage:
			if row.date and not (start <= getdate(row.date) <= end):
				out_of_range.append(_("Mileage row {0} ({1})").format(row.idx, row.date))
		for row in self.other_costs:
			if row.expense_date and not (start <= getdate(row.expense_date) <= end):
				out_of_range.append(_("Other Costs row {0} ({1})").format(row.idx, row.expense_date))
		if out_of_range:
			# Tolerated on purpose (pre-trip purchases, late receipts) — just flag it.
			frappe.msgprint(
				_("Dates outside the trip range: {0}").format("; ".join(out_of_range)), alert=True
			)

	# ------------------------------------------------------- business reason

	def _validate_travel_for(self):
		if self.travel_for_doctype and self.travel_for_doctype not in TRAVEL_FOR_DOCTYPES:
			frappe.throw(
				_("Travel For must be one of: {0}").format(", ".join(TRAVEL_FOR_DOCTYPES))
			)
		for stop in self.itinerary:
			if stop.related_party_doctype and stop.related_party_doctype not in RELATED_PARTY_DOCTYPES:
				frappe.throw(
					_("Itinerary row {0}: Related Party must be one of: {1}").format(
						stop.idx, ", ".join(RELATED_PARTY_DOCTYPES)
					)
				)

		# Mirror fields consumed by Expense Claim generation, the Project
		# dashboard count and the billable-costs report.
		self.project = (
			self.travel_for_name if self.travel_for_doctype == "Project" else None
		)
		self.customer = self._derive_customer()

	def _derive_customer(self):
		if not (self.travel_for_doctype and self.travel_for_name):
			return None
		if self.travel_for_doctype == "Customer":
			return self.travel_for_name
		if self.travel_for_doctype == "Project":
			return frappe.db.get_value("Project", self.travel_for_name, "customer")
		if self.travel_for_doctype == "Opportunity":
			party_type, party = frappe.db.get_value(
				"Opportunity", self.travel_for_name, ["opportunity_from", "party_name"]
			) or (None, None)
			return party if party_type == "Customer" else None
		return None  # Lead has no customer yet

	# -------------------------------------------------------------- travelers

	def _validate_travelers(self):
		if not self.travelers:
			frappe.throw(_("A Travel Trip needs at least one traveler."))

		seen = set()
		for traveler in self.travelers:
			if traveler.employee in seen:
				frappe.throw(
					_("Traveler {0} appears more than once.").format(
						traveler.employee_name or traveler.employee
					)
				)
			seen.add(traveler.employee)

		leads = [t for t in self.travelers if t.is_trip_lead]
		if not leads:
			self.travelers[0].is_trip_lead = 1
		elif len(leads) > 1:
			frappe.throw(_("Only one traveler can be the Trip Lead."))

		# A traveler row with financial history must not be deleted — the
		# linked Expense Claim / Advance would dangle.
		if self._before:
			current = {t.name for t in self.travelers}
			for old in self._before.travelers:
				if old.name in current:
					continue
				if old.expense_claim or old.employee_advance or old.per_diem_claimed:
					frappe.throw(
						_(
							"Cannot remove traveler {0}: an Expense Claim or Employee Advance is linked to them. Cancel those documents first."
						).format(old.employee_name or old.employee)
					)

	# -------------------------------------------------------------- cost rows

	def _validate_cost_rows(self):
		traveler_employees = {t.employee for t in self.travelers}

		for fieldname in COST_TABLES:
			for row in self.get(fieldname):
				if fieldname == "ground_transport" and row.transport_type == "Company Fleet":
					# Fleet usage is never reimbursed.
					row.paid_by = "Company"
					row.paid_by_traveler = None
				if row.paid_by == "Employee":
					if not row.paid_by_traveler:
						frappe.throw(
							_("{0} row {1}: Paid By Traveler is required for employee-paid costs.").format(
								_(self.meta.get_label(fieldname)), row.idx
							)
						)
					if row.paid_by_traveler not in traveler_employees:
						frappe.throw(
							_("{0} row {1}: {2} is not a traveler on this trip.").format(
								_(self.meta.get_label(fieldname)), row.idx, row.paid_by_traveler
							)
						)

		for row in self.mileage:
			if row.traveler not in traveler_employees:
				frappe.throw(
					_("Mileage row {0}: {1} is not a traveler on this trip.").format(
						row.idx, row.traveler
					)
				)

	# ------------------------------------------------------ computed amounts

	def _compute_mileage(self):
		settings = get_travel_settings()
		for row in self.mileage:
			if row.expense_claim:
				continue  # claimed rows are frozen
			if not flt(row.rate):
				row.rate = flt(settings.mileage_rate)
			row.amount = flt(row.distance) * flt(row.rate)

	def _compute_per_diem(self):
		settings = get_travel_settings()
		rate_row = next(
			(r for r in settings.per_diem_rates if r.travel_type == self.travel_type), None
		)
		changed_after_claim = []

		for traveler in self.travelers:
			if traveler.per_diem_claimed:
				# Frozen: the amount already sits on an Expense Claim.
				if self._before:
					old = next(
						(t for t in self._before.travelers if t.name == traveler.name), None
					)
					if old and (
						str(old.from_date) != str(traveler.from_date)
						or str(old.to_date) != str(traveler.to_date)
					):
						changed_after_claim.append(traveler.employee_name or traveler.employee)
				continue
			if not traveler.per_diem_eligible:
				traveler.per_diem_amount = 0
				continue

			rate = flt(traveler.per_diem_override_rate) or (
				flt(rate_row.daily_rate) if rate_row else 0
			)
			pct = flt(rate_row.first_last_day_percent) if rate_row else 100
			days = date_diff(traveler.to_date, traveler.from_date) + 1
			if days <= 1:
				traveler.per_diem_amount = rate * pct / 100
			else:
				traveler.per_diem_amount = rate * (days - 2) + 2 * rate * pct / 100

		if changed_after_claim:
			frappe.msgprint(
				_(
					"Per diem was already claimed for {0}; their dates changed but the claimed amount stays frozen. Adjust the Expense Claim manually if needed."
				).format(", ".join(changed_after_claim)),
				alert=True,
				indicator="orange",
			)

	def _compute_rollups(self):
		estimated = actual = company_paid = employee_paid = 0
		for fieldname in COST_TABLES:
			for row in self.get(fieldname):
				estimated += flt(row.estimated_cost)
				actual += flt(row.cost)
				if row.paid_by == "Employee":
					employee_paid += flt(row.cost)
				else:
					company_paid += flt(row.cost)

		per_diem = sum(flt(t.per_diem_amount) for t in self.travelers)
		mileage = sum(flt(m.amount) for m in self.mileage)

		self.total_per_diem = per_diem
		self.total_mileage_amount = mileage
		self.total_estimated_cost = estimated + per_diem + mileage
		self.total_actual_cost = actual + per_diem + mileage
		self.total_company_paid = company_paid
		# Per diem and mileage are always owed to the traveler.
		self.total_employee_paid = employee_paid + per_diem + mileage

		if self.is_new():
			self.total_claimed_amount = 0
			self.total_advance_amount = 0
		else:
			self.total_claimed_amount = self._linked_total(
				"Expense Claim", "total_claimed_amount"
			)
			self.total_advance_amount = self._linked_total("Employee Advance", "advance_amount")

	def _linked_total(self, doctype, amount_field):
		if not frappe.db.has_column(doctype, "custom_travel_trip"):
			return 0  # fixture-managed back-link field not applied yet
		# Query builder: frappe 16 rejects SQL functions as get_all field strings.
		from frappe.query_builder.functions import Sum

		table = frappe.qb.DocType(doctype)
		rows = (
			frappe.qb.from_(table)
			.select(Sum(table[amount_field]).as_("total"))
			.where((table.custom_travel_trip == self.name) & (table.docstatus < 2))
		).run(as_dict=True)
		return flt(rows[0].total) if rows else 0

	# ----------------------------------------------------------------- status

	def _handle_status_change(self):
		old_status = self._before.status if self._before else None
		if old_status == self.status:
			return

		if self.is_new():
			if self.status not in ("Planning", "Booked") and not user_is_travel_coordinator():
				frappe.throw(_("New trips start in Planning or Booked."))
		elif not user_is_travel_coordinator():
			if (old_status, self.status) not in EMPLOYEE_ALLOWED_TRANSITIONS:
				frappe.throw(
					_(
						"You cannot move this trip from {0} to {1}. In Progress and Completed advance automatically from the trip dates; ask a Travel Coordinator for anything else."
					).format(_(old_status or "Planning"), _(self.status))
				)

		if self.status == "Booked" and old_status != "Booked":
			self.booked_on = now_datetime()
		if self.status == "Closed" and old_status != "Closed":
			self.closed_on = now_datetime()
			self._warn_unclaimed_on_close()

	def _warn_unclaimed_on_close(self):
		unclaimed = 0
		for fieldname in COST_TABLES:
			for row in self.get(fieldname):
				if row.paid_by == "Employee" and not row.expense_claim:
					unclaimed += flt(row.cost)
		for row in self.mileage:
			if not row.expense_claim:
				unclaimed += flt(row.amount)
		for traveler in self.travelers:
			if traveler.per_diem_eligible and not traveler.per_diem_claimed:
				unclaimed += flt(traveler.per_diem_amount)
		if unclaimed:
			frappe.msgprint(
				_(
					"This trip is being closed with {0} of employee-paid costs, per diem or mileage not yet on an Expense Claim."
				).format(frappe.format_value(unclaimed, {"fieldtype": "Currency"})),
				indicator="orange",
			)

	def _check_closed_lock(self):
		if self._before and self._before.status == "Closed" and not user_is_travel_coordinator():
			frappe.throw(
				_(
					"This trip is Closed. Ask a Travel Coordinator to reopen it before making changes."
				)
			)
