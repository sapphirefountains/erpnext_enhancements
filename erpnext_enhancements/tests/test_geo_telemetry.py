import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import patch
from erpnext_enhancements.api import time_kiosk

class TestGeoTelemetry(FrappeTestCase):
	def setUp(self):
		super().setUp()

		# Ensure company exists
		if not frappe.db.exists("Company", "_Test Company_"):
			frappe.get_doc({
				"doctype": "Company",
				"company_name": "_Test Company_",
				"abbr": "_TC_",
				"default_currency": "USD",
				"country": "United States"
			}).insert()

		existing_emp = frappe.db.get_value("Employee", {"first_name": "Geo", "last_name": "Tester"}, "name")
		if existing_emp:
			self.employee = existing_emp
		else:
			emp = frappe.get_doc({
				"doctype": "Employee",
				"first_name": "Geo",
				"last_name": "Tester",
				"status": "Active",
				"date_of_joining": "2020-01-01",
				"company": "_Test Company_"
			})
			emp.flags.ignore_mandatory = True
			emp.insert()
			self.employee = emp.name

	def tearDown(self):
		frappe.db.delete("Time Kiosk Log", {"employee": self.employee})
		super().tearDown()

	def test_log_geolocation_success(self):
		result = time_kiosk.log_geolocation(
			employee=self.employee,
			latitude=37.7749,
			longitude=-122.4194,
			device_agent="TestAgent/1.0",
			log_status="Success",
			timestamp="2023-10-27 10:00:00"
		)

		self.assertEqual(result['status'], 'success')

		# Verify db insertion
		logs = frappe.get_all("Time Kiosk Log", filters={"employee": self.employee}, fields=["latitude", "longitude"])
		self.assertTrue(len(logs) > 0)
		self.assertEqual(logs[0].latitude, 37.7749)

	def test_log_geolocation_missing_employee(self):
		# Pass None as employee
		result = time_kiosk.log_geolocation(
			employee=None,
			latitude=0,
			longitude=0,
			device_agent="Test",
			log_status="Error",
			timestamp="2023-10-27 10:00:00"
		)

		self.assertEqual(result['status'], 'error')
		self.assertIn("Employee ID is required", result['message'])

	def test_log_geolocation_db_error(self):
		# We patch frappe.get_doc to raise an exception ONLY when creating "Time Kiosk Log"
		# This avoids breaking log_error which might use get_doc

		original_get_doc = frappe.get_doc

		def side_effect(*args, **kwargs):
			# check if first arg is dict and has doctype "Time Kiosk Log"
			if args and isinstance(args[0], dict) and args[0].get('doctype') == 'Time Kiosk Log':
				raise Exception("DB Error")
			return original_get_doc(*args, **kwargs)

		with patch('frappe.get_doc', side_effect=side_effect):
			result = time_kiosk.log_geolocation(
				employee=self.employee,
				latitude=0,
				longitude=0,
				device_agent="Test",
				log_status="Success",
				timestamp="2023-10-27 10:00:00"
			)

		self.assertEqual(result['status'], 'error')
		self.assertIn("DB Error", result['message'])
