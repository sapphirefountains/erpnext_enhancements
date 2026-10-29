# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Unclaimed Travel Expenses — one row per (ended trip, traveler) still owed
money: employee-paid cost rows without an Expense Claim stamp, unclaimed
mileage, and unclaimed per diem. The expense-nudge scheduler job
(travel_management.reminders) emails the same population.

Without HRMS — production, where it cannot be installed
(accounting_intake/actions/receipt_expense.py) — nothing is ever on a claim, so
every row is simply what the trip says the company owes that traveler, and
accounting reimburses from the trip (Nik, 2026-09-28). The two claim columns
are dropped there and the amount is called *To Reimburse*, the expense nudge's
own words. Until v1.755.3 every row read "No claim" beside an empty Link to
Expense Claim, a doctype the site does not have — which told a coordinator a
claim was missing when there was never one to file."""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, today

from erpnext_enhancements.travel_management import COST_TABLES, expense_claims_available


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	hrms = expense_claims_available()
	columns = [
		{"label": _("Trip"), "fieldname": "trip", "fieldtype": "Link", "options": "Travel Trip", "width": 150},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 95},
		{"label": _("Trip End"), "fieldname": "end_date", "fieldtype": "Date", "width": 95},
		{"label": _("Days Since End"), "fieldname": "days_since_end", "fieldtype": "Int", "width": 110},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 130},
		{"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 150},
		{
			"label": _("Unclaimed Amount") if hrms else _("To Reimburse"),
			"fieldname": "unclaimed",
			"fieldtype": "Currency",
			"width": 130,
		},
	]
	if hrms:
		columns += [
			{"label": _("Expense Claim"), "fieldname": "expense_claim", "fieldtype": "Link", "options": "Expense Claim", "width": 140},
			{"label": _("Claim Status"), "fieldname": "claim_status", "fieldtype": "Data", "width": 110},
		]
	return columns


def get_data(filters):
	min_days = cint(filters.get("min_days_since_end") or 0)

	trips = frappe.get_all(
		"Travel Trip",
		filters={"end_date": ["<", today()], "status": ["!=", "Planning"]},
		fields=["name", "status", "end_date"],
	)
	trips = [t for t in trips if date_diff(today(), t.end_date) >= min_days]
	if not trips:
		return []
	trip_names = [t.name for t in trips]
	trip_by_name = {t.name: t for t in trips}
	parent_filter = {"parenttype": "Travel Trip", "parent": ["in", trip_names]}

	# unclaimed[(trip, employee)] -> amount
	unclaimed = {}

	for fieldname, doctype in COST_TABLES.items():
		for row in frappe.get_all(
			doctype,
			filters=dict(parent_filter, paid_by="Employee", expense_claim=("is", "not set")),
			fields=["parent", "paid_by_traveler", "cost"],
		):
			if row.paid_by_traveler and flt(row.cost):
				key = (row.parent, row.paid_by_traveler)
				unclaimed[key] = unclaimed.get(key, 0) + flt(row.cost)

	for row in frappe.get_all(
		"Trip Mileage",
		filters=dict(parent_filter, expense_claim=("is", "not set")),
		fields=["parent", "traveler", "amount"],
	):
		if flt(row.amount):
			key = (row.parent, row.traveler)
			unclaimed[key] = unclaimed.get(key, 0) + flt(row.amount)

	travelers = frappe.get_all(
		"Trip Traveler",
		filters=parent_filter,
		fields=[
			"parent",
			"employee",
			"employee_name",
			"per_diem_eligible",
			"per_diem_claimed",
			"per_diem_amount",
			"expense_claim",
			"expense_claim_status",
		],
	)
	traveler_info = {(t.parent, t.employee): t for t in travelers}

	for t in travelers:
		if t.per_diem_eligible and not t.per_diem_claimed and flt(t.per_diem_amount):
			key = (t.parent, t.employee)
			unclaimed[key] = unclaimed.get(key, 0) + flt(t.per_diem_amount)

	hrms = expense_claims_available()
	data = []
	for (trip, employee), amount in sorted(unclaimed.items()):
		info = traveler_info.get((trip, employee)) or frappe._dict()
		trip_doc = trip_by_name[trip]
		row = {
			"trip": trip,
			"status": trip_doc.status,
			"end_date": trip_doc.end_date,
			"days_since_end": date_diff(today(), trip_doc.end_date),
			"employee": employee,
			"employee_name": info.get("employee_name")
			or frappe.db.get_value("Employee", employee, "employee_name"),
			"unclaimed": amount,
		}
		if hrms:
			row["expense_claim"] = info.get("expense_claim")
			row["claim_status"] = info.get("expense_claim_status") or (
				_("No claim") if not info.get("expense_claim") else None
			)
		data.append(row)
	return data
