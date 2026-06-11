# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Travel Spend by Category — flights / accommodation / ground / other /
per diem / mileage, pivoted per Trip, Project or Employee.

Grouping semantics:
	- **Trip / Project**: every cost row of the trip counts (company- and
	  employee-paid alike) — this is total trip spend.
	- **Employee**: only amounts owed to/paid by that person count — costs
	  they personally paid (``paid_by_traveler``) plus their per diem and
	  mileage. Company-card spend has no employee dimension.
"""

import frappe
from frappe import _
from frappe.utils import flt

CATEGORY_TABLES = (
	("flights", "Trip Flight", "flights"),
	("accommodation", "Trip Accommodation", "accommodations"),
	("ground", "Trip Ground Transport", "ground_transport"),
	("other", "Trip Expense", "other_costs"),
)

CATEGORIES = ("flights", "accommodation", "ground", "other", "per_diem", "mileage")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	group_by = filters.get("group_by") or "Trip"
	return get_columns(group_by), get_data(filters, group_by)


def get_columns(group_by):
	if group_by == "Project":
		key_column = {"label": _("Project"), "fieldname": "group_key", "fieldtype": "Link", "options": "Project", "width": 180}
	elif group_by == "Employee":
		key_column = {"label": _("Employee"), "fieldname": "group_key", "fieldtype": "Link", "options": "Employee", "width": 180}
	else:
		key_column = {"label": _("Trip"), "fieldname": "group_key", "fieldtype": "Link", "options": "Travel Trip", "width": 180}

	return [
		key_column,
		{"label": _("Flights"), "fieldname": "flights", "fieldtype": "Currency", "width": 110},
		{"label": _("Accommodation"), "fieldname": "accommodation", "fieldtype": "Currency", "width": 120},
		{"label": _("Ground Transport"), "fieldname": "ground", "fieldtype": "Currency", "width": 130},
		{"label": _("Other"), "fieldname": "other", "fieldtype": "Currency", "width": 110},
		{"label": _("Per Diem"), "fieldname": "per_diem", "fieldtype": "Currency", "width": 110},
		{"label": _("Mileage"), "fieldname": "mileage", "fieldtype": "Currency", "width": 110},
		{"label": _("Total"), "fieldname": "total", "fieldtype": "Currency", "width": 120},
	]


def get_data(filters, group_by):
	trip_filters = {}
	if filters.get("from_date"):
		trip_filters["end_date"] = [">=", filters.from_date]
	if filters.get("to_date"):
		trip_filters["start_date"] = ["<=", filters.to_date]

	trips = frappe.get_all(
		"Travel Trip", filters=trip_filters, fields=["name", "project"]
	)
	if not trips:
		return []
	trip_names = [t.name for t in trips]
	project_of = {t.name: t.project for t in trips}

	rows = {}

	def bucket(key):
		if key not in rows:
			rows[key] = {"group_key": key} | {c: 0 for c in CATEGORIES}
		return rows[key]

	def group_key(trip, employee=None):
		if group_by == "Project":
			return project_of.get(trip) or _("(No Project)")
		if group_by == "Employee":
			return employee
		return trip

	parent_filter = {"parenttype": "Travel Trip", "parent": ["in", trip_names]}

	for category, doctype, _fieldname in CATEGORY_TABLES:
		fields = ["parent", "cost", "paid_by", "paid_by_traveler"]
		for row in frappe.get_all(doctype, filters=parent_filter, fields=fields):
			if group_by == "Employee":
				if row.paid_by != "Employee" or not row.paid_by_traveler:
					continue
				key = group_key(row.parent, row.paid_by_traveler)
			else:
				key = group_key(row.parent)
			bucket(key)[category] += flt(row.cost)

	for row in frappe.get_all(
		"Trip Traveler",
		filters=parent_filter,
		fields=["parent", "employee", "per_diem_amount"],
	):
		key = group_key(row.parent, row.employee)
		if key is not None:
			bucket(key)["per_diem"] += flt(row.per_diem_amount)

	for row in frappe.get_all(
		"Trip Mileage", filters=parent_filter, fields=["parent", "traveler", "amount"]
	):
		key = group_key(row.parent, row.traveler)
		if key is not None:
			bucket(key)["mileage"] += flt(row.amount)

	data = sorted(rows.values(), key=lambda r: str(r["group_key"]))
	for row in data:
		row["total"] = sum(row[c] for c in CATEGORIES)
	return [r for r in data if r["total"]]
