# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Whitelisted document-creation methods for Travel Trip.

Expense Claims, Employee Advances, Vehicle Logs and Lead/Opportunity outcomes
are created HERE, explicitly, from form buttons — never as save side-effects.
Every method permission-checks the trip first and inserts native documents
WITHOUT ``ignore_permissions`` (the caller needs native create rights). All
methods refuse to act on a Closed trip except :func:`reopen_trip`.

Claim dedupe contract: each claimed cost/mileage row is stamped with the
Expense Claim name (``expense_claim``) and the traveler's per diem with
``per_diem_claimed``. Stamps are written with ``frappe.db.set_value`` — never
a full parent save — so a colleague's concurrently open Travel Trip form can
still save without clobbering them (the fields are read-only/no_copy, the
client never posts them back). ``travel_management.integrations`` clears the
stamps when a claim is cancelled or deleted.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from erpnext_enhancements.travel_management import COST_TABLES
from erpnext_enhancements.travel_management.doctype.travel_trip.travel_trip import (
	user_is_travel_coordinator,
)

OUTCOME_DOCTYPES = ("Lead", "Opportunity")


# --------------------------------------------------------------------- guards


def _get_trip(trip, ptype="write"):
	doc = frappe.get_doc("Travel Trip", trip)
	frappe.has_permission("Travel Trip", ptype, doc=doc, throw=True)
	return doc


def _refuse_closed(doc):
	if doc.status == "Closed":
		frappe.throw(
			_("{0} is Closed. Reopen it (Travel Coordinator) before creating documents.").format(
				doc.name
			)
		)


def _require_hrms(doctype, feature):
	"""Refuse a finance action with a clear message when its HRMS doctype is
	missing. HRMS is optional (see ``travel_management.expense_claims_available``);
	without it ``frappe.new_doc("Expense Claim")`` etc. would raise a raw
	``DoesNotExistError``, so we surface the real cause instead."""
	if not frappe.db.exists("DocType", doctype):
		frappe.throw(
			_(
				"Travel {0} need the HR module (Frappe HR / “hrms”), which is not installed "
				"on this site. Install hrms to use this."
			).format(feature),
			title=_("HR module not installed"),
		)


def _get_traveler_row(doc, traveler):
	row = next((t for t in doc.travelers if t.name == traveler), None)
	if not row:
		frappe.throw(_("Traveler row {0} not found on {1}.").format(traveler, doc.name))
	return row


def _resolve_expense_type(settings, settings_field):
	value = settings.get(settings_field)
	if value and frappe.db.exists("Expense Claim Type", value):
		return value
	frappe.throw(
		_(
			"Expense Claim Type for {0} is not configured (or no longer exists). Set it under Travel Settings → Expense Claim Types."
		).format(_(settings.meta.get_label(settings_field)))
	)


# ------------------------------------------------------------- expense claims


@frappe.whitelist()
def create_expense_claim(trip: str, traveler: str):
	"""Create (or extend) the draft Expense Claim for one traveler row,
	pulling in every unclaimed employee-paid cost, mileage row and the per
	diem. Returns the claim name, or None when nothing is claimable."""
	_require_hrms("Expense Claim", _("expense claims"))
	doc = _get_trip(trip)
	_refuse_closed(doc)
	row = _get_traveler_row(doc, traveler)

	claim = _create_claim_for_traveler(doc, row)
	if claim:
		_notify_claims(doc, {row.employee: claim})
	return claim


@frappe.whitelist()
def create_expense_claims(trip: str):
	"""Create/extend draft Expense Claims for every traveler with claimable
	material. Returns ``{employee: claim_name}``."""
	_require_hrms("Expense Claim", _("expense claims"))
	doc = _get_trip(trip)
	_refuse_closed(doc)

	claims = {}
	for row in doc.travelers:
		claim = _create_claim_for_traveler(doc, row)
		if claim:
			claims[row.employee] = claim
	if claims:
		_notify_claims(doc, claims)
	return claims


def _notify_claims(doc, claims):
	try:
		from erpnext_enhancements.travel_management import notifications

		notifications.notify_expense_claims_generated(doc, claims)
	except Exception:
		# Claim creation must never fail on a notification problem.
		frappe.log_error(
			title="Travel expense-claim notification failed",
			message=f"{doc.name}: {claims}\n{frappe.get_traceback()}",
		)


def _gather_claimable(doc, employee, settings):
	"""Collect unclaimed employee-paid material for one employee as
	``(source_doctype, row_name, expense_line)`` tuples."""
	lines = []

	def add(table_doctype, row, expense_type, description, expense_date, amount, billable):
		lines.append(
			(
				table_doctype,
				row.name,
				{
					"expense_date": expense_date or doc.start_date,
					"expense_type": expense_type,
					"description": description,
					"amount": amount,
					"sanctioned_amount": amount,
					"project": doc.project if (doc.billable or billable) and doc.project else None,
				},
			)
		)

	for row in doc.flights:
		if row.paid_by == "Employee" and row.paid_by_traveler == employee and flt(row.cost) > 0 and not row.expense_claim:
			add(
				"Trip Flight",
				row,
				_resolve_expense_type(settings, "flight_expense_type"),
				f"Flight: {row.airline} [{row.flight_number}] {row.departure_airport or ''} → {row.arrival_airport or ''}".strip(),
				getdate(row.departure_time) if row.departure_time else None,
				flt(row.cost),
				row.billable,
			)

	for row in doc.accommodations:
		if row.paid_by == "Employee" and row.paid_by_traveler == employee and flt(row.cost) > 0 and not row.expense_claim:
			add(
				"Trip Accommodation",
				row,
				_resolve_expense_type(settings, "hotel_expense_type"),
				f"Accommodation: {row.hotel_lodging} ({row.check_in_date or ''} – {row.check_out_date or ''})",
				row.check_in_date,
				flt(row.cost),
				row.billable,
			)

	for row in doc.ground_transport:
		if row.paid_by == "Employee" and row.paid_by_traveler == employee and flt(row.cost) > 0 and not row.expense_claim:
			add(
				"Trip Ground Transport",
				row,
				_resolve_expense_type(settings, "ground_expense_type"),
				f"Ground transport: {row.transport_type} {row.supplier or row.vehicle or ''} {row.pickup_location or ''} → {row.dropoff_location or ''}".strip(),
				getdate(row.pickup_datetime) if row.pickup_datetime else None,
				flt(row.cost),
				row.billable,
			)

	for row in doc.other_costs:
		if row.paid_by == "Employee" and row.paid_by_traveler == employee and flt(row.cost) > 0 and not row.expense_claim:
			expense_type = row.expense_claim_type or _resolve_expense_type(
				settings, "misc_expense_type"
			)
			add(
				"Trip Expense",
				row,
				expense_type,
				row.description,
				row.expense_date,
				flt(row.cost),
				row.billable,
			)

	for row in doc.mileage:
		if row.traveler == employee and flt(row.amount) > 0 and not row.expense_claim:
			add(
				"Trip Mileage",
				row,
				_resolve_expense_type(settings, "mileage_expense_type"),
				f"Mileage {row.date}: {row.from_location or ''} → {row.to_location or ''} ({flt(row.distance)} mi @ {flt(row.rate)})",
				row.date,
				flt(row.amount),
				row.billable,
			)

	return lines


def _create_claim_for_traveler(doc, row):
	settings = frappe.get_cached_doc("Travel Settings")
	lines = _gather_claimable(doc, row.employee, settings)

	per_diem_line = None
	if row.per_diem_eligible and not row.per_diem_claimed and flt(row.per_diem_amount) > 0:
		days = (getdate(row.to_date or doc.end_date) - getdate(row.from_date or doc.start_date)).days + 1
		per_diem_line = {
			"expense_date": row.to_date or doc.end_date,
			"expense_type": _resolve_expense_type(settings, "per_diem_expense_type"),
			"description": f"Per diem: {days} day(s) ({row.from_date or doc.start_date} – {row.to_date or doc.end_date})",
			"amount": flt(row.per_diem_amount),
			"sanctioned_amount": flt(row.per_diem_amount),
			"project": doc.project if doc.billable and doc.project else None,
		}

	if not lines and not per_diem_line:
		return None

	ec = None
	if row.expense_claim:
		existing_docstatus = frappe.db.get_value("Expense Claim", row.expense_claim, "docstatus")
		if existing_docstatus == 0:
			ec = frappe.get_doc("Expense Claim", row.expense_claim)

	if ec is None:
		company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
		ec = frappe.new_doc("Expense Claim")
		ec.update(
			{
				"employee": row.employee,
				"company": doc.company,
				"posting_date": today(),
				"currency": company_currency,
				"exchange_rate": 1,
				"remark": f"Travel Trip {doc.name}: {doc.purpose}",
				"custom_travel_trip": doc.name,
			}
		)
		if doc.project:
			ec.project = doc.project

	for _table, _row_name, line in lines:
		ec.append("expenses", line)
	if per_diem_line:
		ec.append("expenses", per_diem_line)

	if ec.is_new():
		ec.insert()
	else:
		ec.save()

	# Stamp the claimed material row by row (never a full Travel Trip save —
	# see module docstring for the collaborative-editing rationale).
	for table_doctype, row_name, _line in lines:
		frappe.db.set_value(table_doctype, row_name, "expense_claim", ec.name)
	traveler_updates = {"expense_claim": ec.name, "expense_claim_status": ec.status}
	if per_diem_line:
		traveler_updates["per_diem_claimed"] = 1
	frappe.db.set_value("Trip Traveler", row.name, traveler_updates)

	doc.notify_update()
	return ec.name


# ----------------------------------------------------------------- advances


@frappe.whitelist()
def create_employee_advance(trip: str, traveler: str, amount, mode_of_payment: str | None = None):
	"""Create a draft Employee Advance for one traveler and link it to the
	trip. HR submits and pays it natively; status flows back via doc_events."""
	_require_hrms("Employee Advance", _("advances"))
	doc = _get_trip(trip)
	_refuse_closed(doc)
	row = _get_traveler_row(doc, traveler)

	amount = flt(amount)
	if amount <= 0:
		frappe.throw(_("Advance amount must be greater than zero."))

	if row.employee_advance:
		existing_docstatus = frappe.db.get_value(
			"Employee Advance", row.employee_advance, "docstatus"
		)
		if existing_docstatus is not None and existing_docstatus < 2:
			frappe.throw(
				_("{0} already has Employee Advance {1} for this trip.").format(
					row.employee_name or row.employee, row.employee_advance
				)
			)

	company_currency = frappe.get_cached_value("Company", doc.company, "default_currency")
	advance = frappe.new_doc("Employee Advance")
	advance.update(
		{
			"employee": row.employee,
			"company": doc.company,
			"posting_date": today(),
			"purpose": f"Travel advance — {doc.name} ({doc.purpose})",
			"advance_amount": amount,
			"currency": company_currency,
			"exchange_rate": 1,
			"custom_travel_trip": doc.name,
		}
	)
	if mode_of_payment:
		advance.mode_of_payment = mode_of_payment
	advance.insert()

	frappe.db.set_value(
		"Trip Traveler",
		row.name,
		{"employee_advance": advance.name, "advance_status": advance.status},
	)
	doc.notify_update()
	return advance.name


# ----------------------------------------------------------------- outcomes


@frappe.whitelist()
def create_outcome_from_stop(trip: str, agenda_row: str, target_doctype: str, values=None):
	"""Quick-create a Lead or Opportunity from an itinerary stop and link it
	back (``outcome_doctype`` / ``outcome_name`` on the row, plus the
	``custom_travel_trip`` provenance field on the new record)."""
	doc = _get_trip(trip)
	_refuse_closed(doc)

	if target_doctype not in OUTCOME_DOCTYPES:
		frappe.throw(_("Outcome must be one of: {0}").format(", ".join(OUTCOME_DOCTYPES)))

	stop = next((s for s in doc.itinerary if s.name == agenda_row), None)
	if not stop:
		frappe.throw(_("Itinerary row {0} not found on {1}.").format(agenda_row, doc.name))

	if isinstance(values, str):
		values = json.loads(values or "{}")
	values = values or {}

	outcome = frappe.new_doc(target_doctype)
	outcome.update(values)
	if target_doctype == "Opportunity" and not outcome.get("party_name"):
		if stop.related_party_doctype in ("Lead", "Customer") and stop.related_party_name:
			outcome.opportunity_from = stop.related_party_doctype
			outcome.party_name = stop.related_party_name
	outcome.custom_travel_trip = doc.name
	outcome.insert()

	stop_updates = {"outcome_doctype": target_doctype, "outcome_name": outcome.name}
	if not stop.related_party_name:
		stop_updates.update(
			{"related_party_doctype": target_doctype, "related_party_name": outcome.name}
		)
	frappe.db.set_value("Trip Agenda", stop.name, stop_updates)

	doc.notify_update()
	return outcome.name


# -------------------------------------------------------------- vehicle log


@frappe.whitelist()
def create_vehicle_log(trip: str, ground_row: str, odometer, date: str | None = None, employee: str | None = None):
	"""Create a draft Vehicle Log for a Company Fleet ground-transport row.
	Left as draft on purpose — HRMS validates odometer continuity on submit."""
	_require_hrms("Vehicle Log", _("vehicle logs"))
	doc = _get_trip(trip)
	_refuse_closed(doc)

	row = next((g for g in doc.ground_transport if g.name == ground_row), None)
	if not row:
		frappe.throw(_("Ground transport row {0} not found on {1}.").format(ground_row, doc.name))
	if row.transport_type != "Company Fleet" or not row.vehicle:
		frappe.throw(_("Vehicle Logs are only for Company Fleet rows with a Vehicle."))
	if row.vehicle_log:
		frappe.throw(_("This row already has Vehicle Log {0}.").format(row.vehicle_log))

	if not employee:
		lead = next((t for t in doc.travelers if t.is_trip_lead), None)
		employee = lead.employee if lead else doc.travelers[0].employee

	log = frappe.new_doc("Vehicle Log")
	log.update(
		{
			"license_plate": row.vehicle,
			"employee": employee,
			"date": date or (getdate(row.pickup_datetime) if row.pickup_datetime else today()),
			"odometer": odometer,
			"custom_travel_trip": doc.name,
		}
	)
	log.insert()

	frappe.db.set_value("Trip Ground Transport", row.name, "vehicle_log", log.name)
	doc.notify_update()
	return log.name


# ------------------------------------------------------------------ lifecycle


@frappe.whitelist()
def reopen_trip(trip: str):
	"""Coordinator escape hatch: Closed -> Completed (late receipts, fixes)."""
	if not user_is_travel_coordinator():
		frappe.throw(_("Only a Travel Coordinator can reopen a closed trip."))

	doc = _get_trip(trip)
	if doc.status != "Closed":
		frappe.throw(_("{0} is not Closed.").format(doc.name))

	doc.db_set("status", "Completed", notify=True)
	doc.db_set("closed_on", None)
	doc.add_comment("Comment", _("Trip reopened by {0}").format(frappe.session.user))
	return "Completed"


# -------------------------------------------------------------------- summary


@frappe.whitelist()
def get_trip_financial_summary(trip: str):
	"""Read-only per-traveler financial breakdown for the trip form."""
	doc = _get_trip(trip, ptype="read")

	def unclaimed_for(employee):
		total = 0
		for fieldname in COST_TABLES:
			for row in doc.get(fieldname):
				if (
					row.paid_by == "Employee"
					and row.paid_by_traveler == employee
					and not row.expense_claim
				):
					total += flt(row.cost)
		for row in doc.mileage:
			if row.traveler == employee and not row.expense_claim:
				total += flt(row.amount)
		return total

	travelers = []
	for t in doc.travelers:
		unclaimed = unclaimed_for(t.employee)
		if t.per_diem_eligible and not t.per_diem_claimed:
			unclaimed += flt(t.per_diem_amount)
		travelers.append(
			{
				"row_name": t.name,
				"employee": t.employee,
				"employee_name": t.employee_name,
				"is_trip_lead": t.is_trip_lead,
				"from_date": t.from_date,
				"to_date": t.to_date,
				"per_diem_amount": flt(t.per_diem_amount),
				"per_diem_claimed": t.per_diem_claimed,
				"expense_claim": t.expense_claim,
				"expense_claim_status": t.expense_claim_status,
				"employee_advance": t.employee_advance,
				"advance_status": t.advance_status,
				"unclaimed_amount": unclaimed,
			}
		)

	return {
		"travelers": travelers,
		"totals": {
			"estimated": flt(doc.total_estimated_cost),
			"actual": flt(doc.total_actual_cost),
			"company_paid": flt(doc.total_company_paid),
			"employee_paid": flt(doc.total_employee_paid),
			"per_diem": flt(doc.total_per_diem),
			"mileage": flt(doc.total_mileage_amount),
			"claimed": flt(doc.total_claimed_amount),
			"advances": flt(doc.total_advance_amount),
		},
	}
