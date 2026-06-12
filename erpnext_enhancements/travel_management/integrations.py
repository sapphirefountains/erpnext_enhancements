# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""doc_events handlers on native doctypes (Expense Claim, Employee Advance,
Vehicle Log) that mirror status back onto Travel Trip traveler rows and keep
the claim-stamp dedupe guard honest.

The stamps (``expense_claim`` on cost/mileage rows, ``per_diem_claimed`` on
the traveler) mark material as already-claimed so
``travel_management.api.create_expense_claim`` never claims it twice. When a
claim is cancelled or deleted these handlers clear every stamp pointing at it,
making the rows claimable again without manual surgery.

Every handler bails on a cheap ``doc.get("custom_travel_trip")`` check so the
hooks cost nothing on unrelated documents.
"""

import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import flt

# Travel Trip child tables that carry an expense_claim stamp.
STAMPED_TABLES = (
	"Trip Flight",
	"Trip Accommodation",
	"Trip Ground Transport",
	"Trip Expense",
	"Trip Mileage",
)


def sync_expense_claim_status(doc, method=None):
	trip = doc.get("custom_travel_trip")
	if not trip:
		return

	if method in ("on_cancel", "on_trash"):
		for table in STAMPED_TABLES:
			frappe.db.set_value(
				table,
				{"parenttype": "Travel Trip", "parent": trip, "expense_claim": doc.name},
				"expense_claim",
				None,
			)
		frappe.db.set_value(
			"Trip Traveler",
			{"parenttype": "Travel Trip", "parent": trip, "expense_claim": doc.name},
			{"expense_claim": None, "expense_claim_status": None, "per_diem_claimed": 0},
		)
	else:
		frappe.db.set_value(
			"Trip Traveler",
			{"parenttype": "Travel Trip", "parent": trip, "expense_claim": doc.name},
			"expense_claim_status",
			doc.get("status"),
		)

	_refresh_linked_total(trip, "Expense Claim", "total_claimed_amount", "total_claimed_amount")


def sync_employee_advance_status(doc, method=None):
	trip = doc.get("custom_travel_trip")
	if not trip:
		return

	if method in ("on_cancel", "on_trash"):
		frappe.db.set_value(
			"Trip Traveler",
			{"parenttype": "Travel Trip", "parent": trip, "employee_advance": doc.name},
			{"employee_advance": None, "advance_status": None},
		)
	else:
		frappe.db.set_value(
			"Trip Traveler",
			{"parenttype": "Travel Trip", "parent": trip, "employee_advance": doc.name},
			"advance_status",
			doc.get("status"),
		)

	_refresh_linked_total(trip, "Employee Advance", "advance_amount", "total_advance_amount")


def sync_vehicle_log_unlink(doc, method=None):
	trip = doc.get("custom_travel_trip")
	if not trip:
		return
	frappe.db.set_value(
		"Trip Ground Transport",
		{"parenttype": "Travel Trip", "parent": trip, "vehicle_log": doc.name},
		"vehicle_log",
		None,
	)


def _refresh_linked_total(trip, doctype, amount_field, trip_field):
	"""Recompute one synced rollup on the trip without a full save (a full
	save would collide with collaboratively open forms)."""
	if not frappe.db.has_column(doctype, "custom_travel_trip"):
		return  # fixture-managed back-link field not applied yet
	if not frappe.db.exists("Travel Trip", trip):
		return  # trip is mid-deletion; on_trash already unlinked us
	# Query builder: frappe 16 rejects SQL functions as get_all field strings.
	table = frappe.qb.DocType(doctype)
	rows = (
		frappe.qb.from_(table)
		.select(Sum(table[amount_field]).as_("total"))
		.where((table.custom_travel_trip == trip) & (table.docstatus < 2))
	).run(as_dict=True)
	total = flt(rows[0].total) if rows else 0
	frappe.db.set_value("Travel Trip", trip, trip_field, total, update_modified=False)
