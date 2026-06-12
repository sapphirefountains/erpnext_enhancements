# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Travel Trip Cost Summary — estimate vs actual vs claimed per trip.

Reads the rollup fields maintained by the Travel Trip controller (and the
integrations doc_events), so it needs no child-table aggregation. Uses
``frappe.get_list`` on purpose: the Travel Trip permission hooks scope plain
employees to their own/crew trips while coordinators see everything.
"""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Trip"), "fieldname": "name", "fieldtype": "Link", "options": "Travel Trip", "width": 150},
		{"label": _("Purpose"), "fieldname": "purpose", "fieldtype": "Data", "width": 180},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Travel For"), "fieldname": "travel_for_name", "fieldtype": "Dynamic Link", "options": "travel_for_doctype", "width": 150},
		{"label": _("Start"), "fieldname": "start_date", "fieldtype": "Date", "width": 95},
		{"label": _("End"), "fieldname": "end_date", "fieldtype": "Date", "width": 95},
		{"label": _("Travelers"), "fieldname": "travelers", "fieldtype": "Int", "width": 85},
		{"label": _("Estimated"), "fieldname": "total_estimated_cost", "fieldtype": "Currency", "width": 110},
		{"label": _("Actual"), "fieldname": "total_actual_cost", "fieldtype": "Currency", "width": 110},
		{"label": _("Variance (Est − Actual)"), "fieldname": "variance", "fieldtype": "Currency", "width": 130},
		{"label": _("Company Paid"), "fieldname": "total_company_paid", "fieldtype": "Currency", "width": 110},
		{"label": _("Employee Paid"), "fieldname": "total_employee_paid", "fieldtype": "Currency", "width": 110},
		{"label": _("Claimed"), "fieldname": "total_claimed_amount", "fieldtype": "Currency", "width": 110},
		{"label": _("Unclaimed"), "fieldname": "unclaimed", "fieldtype": "Currency", "width": 110},
	]


def get_data(filters):
	trip_filters = {}
	if filters.get("from_date"):
		trip_filters["end_date"] = [">=", filters.from_date]
	if filters.get("to_date"):
		trip_filters["start_date"] = ["<=", filters.to_date]
	if filters.get("status"):
		trip_filters["status"] = filters.status
	if filters.get("project"):
		trip_filters["project"] = filters.project

	if filters.get("employee"):
		names = frappe.get_all(
			"Trip Traveler",
			filters={"parenttype": "Travel Trip", "employee": filters.employee},
			pluck="parent",
		)
		if not names:
			return []
		trip_filters["name"] = ["in", names]

	trips = frappe.get_list(
		"Travel Trip",
		filters=trip_filters,
		fields=[
			"name",
			"purpose",
			"status",
			"travel_for_doctype",
			"travel_for_name",
			"start_date",
			"end_date",
			"total_estimated_cost",
			"total_actual_cost",
			"total_company_paid",
			"total_employee_paid",
			"total_claimed_amount",
		],
		order_by="start_date desc",
	)
	if not trips:
		return []

	# Query builder: frappe 16 rejects SQL functions as get_all field strings.
	from frappe.query_builder.functions import Count

	traveler = frappe.qb.DocType("Trip Traveler")
	counts = dict(
		(
			frappe.qb.from_(traveler)
			.select(traveler.parent, Count(traveler.name))
			.where(
				(traveler.parenttype == "Travel Trip")
				& (traveler.parent.isin([t.name for t in trips]))
			)
			.groupby(traveler.parent)
		).run()
	)

	for trip in trips:
		trip.travelers = counts.get(trip.name, 0)
		trip.variance = (trip.total_estimated_cost or 0) - (trip.total_actual_cost or 0)
		trip.unclaimed = (trip.total_employee_paid or 0) - (trip.total_claimed_amount or 0)
	return trips
