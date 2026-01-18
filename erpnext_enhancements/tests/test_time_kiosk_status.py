# -*- coding: utf-8 -*-
import frappe
from frappe.tests.utils import FrappeTestCase
from erpnext_enhancements.api.time_kiosk import get_current_status

class TestTimeKioskStatus(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.create_test_data()

	def create_test_data(self):
		# Create an Employee linked to the current user (Administrator) if not exists
		self.employee = "HR-EMP-KIOSK-STATUS"
		if not frappe.db.exists("Employee", self.employee):
			emp = frappe.get_doc({
				"doctype": "Employee",
				"employee": self.employee,
				"first_name": "KioskStatus",
				"last_name": "User",
				"company": frappe.defaults.get_user_default("Company") or "Test Company",
				"status": "Active",
				"date_of_joining": "2020-01-01",
				"user_id": frappe.session.user
			})
			emp.flags.ignore_mandatory = True
			emp.insert()
		else:
			# Ensure it is linked to current user
			frappe.db.set_value("Employee", self.employee, "user_id", frappe.session.user)

	def tearDown(self):
		# Clean up
		frappe.db.delete("Job Interval", {"employee": self.employee})
		# We don't delete the employee as it might affect other tests or be complex,
		# but we ensure no open intervals exist.
		super().tearDown()

	def test_get_current_status_idle(self):
		"""
		Test that get_current_status returns a dict with just 'employee'
		when the employee exists but has no open job interval.
		"""
		# Ensure no open interval
		frappe.db.delete("Job Interval", {"employee": self.employee, "status": "Open"})

		# Execute
		result = get_current_status()

		# Verify
		self.assertIsNotNone(result)
		self.assertIn("employee", result)
		self.assertEqual(result["employee"], self.employee)

		# Ensure no interval data is present
		self.assertNotIn("name", result)
		self.assertNotIn("project", result)

		# This confirms that 'if (result)' in JS would be true, leading to the frontend issue mentioned in memory
		self.assertTrue(bool(result))
