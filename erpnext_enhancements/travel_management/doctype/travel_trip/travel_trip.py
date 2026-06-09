# -*- coding: utf-8 -*-
# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

"""Server-side controller for the **Travel Trip** doctype.

A Travel Trip is the submittable parent document of the Travel Management
module. It groups an employee's travel logistics into child tables:

	- ``flights``         -> Trip Flight
	- ``accommodation``   -> Trip Accommodation
	- ``ground_transport``-> Trip Ground Transport
	- ``itinerary``       -> Trip Agenda

The trip is driven by the **Travel Trip Workflow** (states:
Draft -> Requested -> Approved -> Booking in Progress -> Ready for Travel ->
In Progress -> Expense Review -> Closed). When the workflow reaches the
expense-settlement states this controller automatically rolls up costed
flight/accommodation rows into a single ERPNext **Expense Claim** and links
it back via the ``custom_expense_claim`` custom field.

Form behaviour is layered on by ``public/js/travel_trip.js`` (registered in
hooks.py ``doctype_js``).
"""

import frappe
from frappe.model.document import Document
from frappe.utils import flt

class TravelTrip(Document):
	"""Controller for Travel Trip; auto-generates an Expense Claim on workflow transition."""

	def on_update(self):
		"""Document lifecycle hook: run on every save (and workflow transition)."""
		self.create_expense_claim_on_workflow_transition()

	def create_expense_claim_on_workflow_transition(self):
		"""Roll trip costs into a single Expense Claim once the trip reaches settlement.

		Idempotent and guarded: creates the Expense Claim only when all hold true:
		  * the workflow has advanced to "Expense Review" or "Closed";
		  * no Expense Claim has been linked yet (``custom_expense_claim`` empty);
		  * at least one flight or accommodation row exists.

		Each costed flight and accommodation row (cost > 0) becomes an expense
		line; the resulting claim is saved (left as a draft) and its name is
		written back to ``custom_expense_claim`` via ``db_set`` so this method
		does not re-fire on the next save.
		"""
		# Define target states
		target_states = ["Expense Review", "Closed"]

		# Check if workflow state is target
		if self.workflow_state not in target_states:
			return

		# Check if already created
		if self.custom_expense_claim:
			return

		# Check if child tables have data
		if not (self.flights or self.accommodation):
			return

		# Create Expense Claim
		ec = frappe.new_doc("Expense Claim")
		ec.employee = self.employee
		ec.company = frappe.defaults.get_user_default("Company")
		if self.project:
			ec.project = self.project
		ec.remark = f"Expense Claim for Trip {self.name}: {self.purpose}"
		ec.posting_date = self.end_date

		# Helper to resolve an Expense Claim Type, degrading to a generic
		# fallback ("Travel") if the preferred type is not configured.
		def get_expense_type(preferred, fallback="Travel"):
			if frappe.db.exists("Expense Claim Type", preferred):
				return preferred
			if frappe.db.exists("Expense Claim Type", fallback):
				return fallback
			# If even fallback doesn't exist, try to find any default or raise/log?
			# We'll assume "Travel" or similar exists, or use first available if desperate,
			# but safe to stick to user request logic.
			return fallback

		air_travel_type = get_expense_type("Air Travel")
		hotel_type = get_expense_type("Hotel Accommodation")

		# Process Flights
		for flight in self.flights:
			if flight.cost > 0:
				ec.append("expenses", {
					"expense_type": air_travel_type,
					"amount": flight.cost,
					"sanctioned_amount": flight.cost,
					"description": f"Flight: {flight.airline} [{flight.flight_number}] - {flight.departure_airport or ''} to {flight.arrival_airport or ''}",
					"expense_date": flight.departure_time or self.start_date
				})

		# Process Accommodation
		for stay in self.accommodation:
			if stay.cost > 0:
				ec.append("expenses", {
					"expense_type": hotel_type,
					"amount": stay.cost,
					"sanctioned_amount": stay.cost,
					"description": f"Accommodation: {stay.hotel_lodging} ({stay.check_in_date or ''} to {stay.check_out_date or ''})",
					"expense_date": stay.check_in_date or self.start_date
				})

		if ec.expenses:
			ec.save()
			# Link back
			self.db_set("custom_expense_claim", ec.name)
			frappe.msgprint(f"Expense Claim {ec.name} created successfully.")
