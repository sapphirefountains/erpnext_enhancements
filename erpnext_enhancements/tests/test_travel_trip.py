# -*- coding: utf-8 -*-
import frappe
from frappe.tests.utils import FrappeTestCase

class TestTravelTrip(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.create_dependencies()

	def create_dependencies(self):
		from frappe.modules.utils import doctype_python_modules
		import sys

		# Fix potential issue where Expense Claim Type is incorrectly assigned to Core module
		if frappe.db.exists("DocType", "Expense Claim Type"):
			# Always force update to be safe
			frappe.db.sql("""
				UPDATE `tabDocType`
				SET custom=1, module='Enhancements Core'
				WHERE name='Expense Claim Type'
			""")
			frappe.db.commit()

			# Clear global cache to ensure fresh fetch of DocType metadata
			frappe.clear_cache()

			# Clear module cache to prevent using stale controller paths
			key = (frappe.local.site, "Expense Claim Type", "", "")
			if key in doctype_python_modules:
				del doctype_python_modules[key]

			# Clear site_controllers if possible (accessed via sys.modules)
			if 'frappe.model.base_document' in sys.modules:
				site_controllers = sys.modules['frappe.model.base_document'].site_controllers
				if "Expense Claim Type" in site_controllers:
					del site_controllers["Expense Claim Type"]

			# Also clear local meta cache to ensure get_meta returns fresh data
			if hasattr(frappe.local, "meta_cache") and "Expense Claim Type" in frappe.local.meta_cache:
				del frappe.local.meta_cache["Expense Claim Type"]

		# Create Expense Claim Types if they don't exist
		if not frappe.db.exists("Expense Claim Type", "Air Travel"):
			frappe.get_doc({
				"doctype": "Expense Claim Type",
				"name": "Air Travel",
				"expense_type": "Air Travel"
			}).insert()

		if not frappe.db.exists("Expense Claim Type", "Hotel Accommodation"):
			frappe.get_doc({
				"doctype": "Expense Claim Type",
				"name": "Hotel Accommodation",
				"expense_type": "Hotel Accommodation"
			}).insert()

		# Create Employee
		self.employee = frappe.db.exists("Employee", "HR-EMP-00001")
		if not self.employee:
			emp = frappe.get_doc({
				"doctype": "Employee",
				"employee": "HR-EMP-00001",
				"first_name": "Test",
				"last_name": "Traveler",
				"company": frappe.defaults.get_user_default("Company") or "Test Company",
				"status": "Active",
				"date_of_joining": "2020-01-01"
			})
			# Bypass mandatory fields that might be required in standard ERPNext but irrelevant here
			emp.flags.ignore_mandatory = True
			emp.insert()
			self.employee = emp.name

	def test_create_expense_claim_on_workflow_transition(self):
		# Create Travel Trip
		trip = frappe.get_doc({
			"doctype": "Travel Trip",
			"employee": self.employee,
			"purpose": "Client Visit",
			"start_date": "2024-02-01",
			"end_date": "2024-02-05",
			"workflow_state": "Draft",
			"flights": [
				{
					"airline": "Test Air",
					"flight_number": "TA101",
					"departure_airport": "JFK",
					"arrival_airport": "LHR",
					"cost": 500,
					"departure_time": "2024-02-01 10:00:00"
				}
			],
			"accommodation": [
				{
					"hotel_lodging": "Test Hotel",
					"check_in_date": "2024-02-01",
					"check_out_date": "2024-02-05",
					"cost": 800
				}
			]
		}).insert()

		# Transition to "Expense Review"
		trip.workflow_state = "Expense Review"
		trip.save()

		# Fetch updated doc
		trip.reload()

		# Assert Expense Claim was created
		self.assertTrue(trip.custom_expense_claim, "Expense Claim should be linked to Travel Trip")

		ec = frappe.get_doc("Expense Claim", trip.custom_expense_claim)
		self.assertEqual(ec.employee, self.employee)
		self.assertEqual(ec.remark, f"Expense Claim for Trip {trip.name}: Client Visit")

		# Check Expenses
		self.assertEqual(len(ec.expenses), 2)

		expense_types = [d.expense_type for d in ec.expenses]
		self.assertIn("Air Travel", expense_types)
		self.assertIn("Hotel Accommodation", expense_types)

		total_amount = sum([d.amount for d in ec.expenses])
		self.assertEqual(total_amount, 1300)
