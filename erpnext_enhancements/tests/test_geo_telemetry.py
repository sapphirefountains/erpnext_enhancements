import json

import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import patch
from erpnext_enhancements.api import time_kiosk


class TestGeoTelemetry(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

		# Company
		if not frappe.db.exists("Company", "_Test Company_"):
			frappe.get_doc({
				"doctype": "Company",
				"company_name": "_Test Company_",
				"abbr": "_TC_",
				"default_currency": "USD",
				"country": "United States"
			}).insert()

		# Unlinked employee — used by the legacy log_geolocation tests (run as Admin).
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

		# A user + employee linked together — used by the session-trusted endpoints.
		self.user = "geo.kiosk@example.com"
		if not frappe.db.exists("User", self.user):
			u = frappe.get_doc({
				"doctype": "User",
				"email": self.user,
				"first_name": "Geo",
				"last_name": "Kiosk",
				"send_welcome_email": 0,
				"roles": [{"role": "Employee"}],
			})
			u.flags.ignore_permissions = True
			u.insert()

		linked = frappe.db.get_value("Employee", {"user_id": self.user}, "name")
		if linked:
			self.linked_emp = linked
		else:
			emp = frappe.get_doc({
				"doctype": "Employee",
				"first_name": "Geo",
				"last_name": "Kiosk",
				"status": "Active",
				"date_of_joining": "2020-01-01",
				"company": "_Test Company_",
				"user_id": self.user,
			})
			emp.flags.ignore_mandatory = True
			emp.insert()
			self.linked_emp = emp.name

		# A project + an open Job Interval for the linked employee.
		if not frappe.db.exists("Project", {"project_name": "_Test Geo Project"}):
			proj = frappe.get_doc({
				"doctype": "Project",
				"project_name": "_Test Geo Project",
				"company": "_Test Company_",
			})
			proj.flags.ignore_mandatory = True
			proj.insert()
			self.project = proj.name
		else:
			self.project = frappe.db.get_value("Project", {"project_name": "_Test Geo Project"}, "name")

		self.interval = frappe.get_doc({
			"doctype": "Job Interval",
			"employee": self.linked_emp,
			"project": self.project,
			"start_time": "2026-01-01 09:00:00",
			"status": "Open",
		}).insert(ignore_permissions=True).name

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Time Kiosk Log", {"employee": ["in", [self.employee, self.linked_emp]]})
		frappe.db.delete("Job Interval", {"employee": ["in", [self.employee, self.linked_emp]]})
		super().tearDown()

	# ----- Legacy single-point endpoint (runs as Administrator) -------------

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
		logs = frappe.get_all("Time Kiosk Log", filters={"employee": self.employee}, fields=["latitude", "longitude"])
		self.assertTrue(len(logs) > 0)
		self.assertEqual(logs[0].latitude, 37.7749)

	def test_log_geolocation_missing_employee(self):
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
		original_get_doc = frappe.get_doc

		def side_effect(*args, **kwargs):
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

	# ----- Batched, session-trusted ingest ---------------------------------

	def test_batch_happy_path_ties_to_interval(self):
		frappe.set_user(self.user)
		points = [{
			"client_id": "c1",
			"job_interval": self.interval,
			"timestamp": "2026-01-01 10:00:00",
			"latitude": 37.0, "longitude": -122.0, "accuracy": 10,
			"log_status": "Success",
		}]
		result = time_kiosk.log_geolocation_batch(json.dumps(points))
		self.assertEqual(result["accepted"], ["c1"])

		rows = frappe.get_all("Time Kiosk Log", filters={"employee": self.linked_emp},
			fields=["job_interval", "latitude"])
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0].job_interval, self.interval)

	def test_batch_rejects_invalid_coords(self):
		frappe.set_user(self.user)
		points = [{"client_id": "bad", "latitude": 200, "longitude": 0, "log_status": "Success"}]
		result = time_kiosk.log_geolocation_batch(points)
		self.assertEqual(result["accepted"], [])
		self.assertEqual(result["rejected"][0]["reason"], "invalid_coords")

	def test_batch_foreign_interval_not_tied(self):
		# An interval owned by a *different* employee must not be attached.
		foreign = frappe.get_doc({
			"doctype": "Job Interval",
			"employee": self.employee,
			"project": self.project,
			"start_time": "2026-01-01 09:00:00",
			"status": "Open",
		}).insert(ignore_permissions=True).name

		frappe.set_user(self.user)
		points = [{
			"client_id": "f1", "job_interval": foreign,
			"latitude": 37.1, "longitude": -122.1, "log_status": "Success",
		}]
		result = time_kiosk.log_geolocation_batch(points)
		self.assertEqual(result["accepted"], ["f1"])

		row = frappe.get_all("Time Kiosk Log", filters={"employee": self.linked_emp},
			fields=["job_interval"])
		self.assertEqual(len(row), 1)
		self.assertIsNone(row[0].job_interval)

		frappe.set_user("Administrator")
		frappe.delete_doc("Job Interval", foreign, ignore_permissions=True, force=True)

	# ----- History read + permissions --------------------------------------

	def test_history_grouping_and_self_view(self):
		frappe.set_user(self.user)
		time_kiosk.log_geolocation_batch([
			{"client_id": "h1", "job_interval": self.interval, "timestamp": "2026-01-01 10:00:00",
			 "latitude": 37.0, "longitude": -122.0, "log_status": "Success"},
			{"client_id": "h2", "job_interval": self.interval, "timestamp": "2026-01-01 10:05:00",
			 "latitude": 37.01, "longitude": -122.01, "log_status": "Success"},
		])

		history = time_kiosk.get_location_history(
			self.linked_emp, "2026-01-01 00:00:00", "2026-01-01 23:59:59")
		self.assertEqual(history["point_count"], 2)
		self.assertEqual(len(history["intervals"]), 1)
		self.assertEqual(history["intervals"][0]["job_interval"], self.interval)
		self.assertEqual(len(history["intervals"][0]["points"]), 2)

	def test_history_permission_denied_for_other_employee(self):
		frappe.set_user(self.user)
		with self.assertRaises(frappe.PermissionError):
			time_kiosk.get_location_history(
				self.employee, "2026-01-01 00:00:00", "2026-01-01 23:59:59")

	# ----- Retention purge --------------------------------------------------

	def test_purge_old_location_logs(self):
		frappe.set_user("Administrator")
		# One ancient log, one fresh log.
		for cid, ts in [("old", "2000-01-01 00:00:00"), ("new", frappe.utils.now_datetime())]:
			frappe.get_doc({
				"doctype": "Time Kiosk Log",
				"employee": self.linked_emp,
				"user": self.user,
				"timestamp": ts,
				"latitude": 1, "longitude": 1,
				"log_status": "Success",
			}).insert(ignore_permissions=True)

		original = frappe.db.get_single_value("Time Kiosk Settings", "retention_days")
		frappe.db.set_single_value("Time Kiosk Settings", "retention_days", 1)
		try:
			time_kiosk.purge_old_location_logs()
		finally:
			frappe.db.set_single_value("Time Kiosk Settings", "retention_days", original or 90)

		remaining = frappe.get_all("Time Kiosk Log", filters={"employee": self.linked_emp})
		self.assertEqual(len(remaining), 1)
