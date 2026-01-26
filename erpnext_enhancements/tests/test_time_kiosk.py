# -*- coding: utf-8 -*-
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, add_to_date
from datetime import timedelta
import erpnext_enhancements.api.time_kiosk as time_kiosk

class TestTimeKiosk(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.create_test_data()

	def create_test_data(self):
		# Ensure Warehouse Type 'Transit' exists
		if not frappe.db.exists("Warehouse Type", "Transit"):
			frappe.get_doc({
				"doctype": "Warehouse Type",
				"name": "Transit"
			}).insert()

		# Ensure Company exists
		self.company = frappe.defaults.get_user_default("Company") or "_Test Company_"
		if not frappe.db.exists("Company", self.company):
			frappe.get_doc({
				"doctype": "Company",
				"company_name": self.company,
				"abbr": "TC",
				"default_currency": "USD",
				"country": "United States"
			}).insert()

		# Ensure Project exists
		project_name = "Test Project"
		existing_project = frappe.db.get_value("Project", {"project_name": project_name}, "name")
		if existing_project:
			self.project = existing_project
		else:
			p = frappe.new_doc("Project")
			p.project_name = project_name
			p.company = self.company
			p.status = "Active"
			p.flags.ignore_validate = True
			p.insert()
			if p.status != "Active":
				frappe.db.set_value("Project", p.name, "status", "Active")
				p.reload()
			self.project = p.name

		# Ensure Activity Type exists
		if not frappe.db.exists("Activity Type", "Execution"):
			frappe.get_doc({
				"doctype": "Activity Type",
				"activity_type": "Execution"
			}).insert()

		# Ensure Employee exists
		self.employee = "HR-EMP-KIOSK"
		if not frappe.db.exists("Employee", self.employee):
			emp = frappe.get_doc({
				"doctype": "Employee",
				"employee": self.employee,
				"first_name": "Kiosk",
				"last_name": "User",
				"company": self.company,
				"status": "Active",
				"date_of_joining": "2020-01-01"
			})
			emp.flags.ignore_mandatory = True
			emp.insert()
			self.employee = emp.name

		# Mock current user as the employee user (if needed, but log_time uses frappe.session.user or db lookup)
		# Assuming log_time uses database to find employee linked to user.
		# For this test, we might need to mock frappe.db.get_value("Employee", ...) if the user is not linked.
		# However, in integration tests, it's better to link them properly or mock the function that gets the employee.

		# Let's see how log_time gets the employee. It probably does `frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")`
		# We can just mock `erpnext_enhancements.api.time_kiosk.get_employee_for_user` if it exists,
		# or simpler: create a User and link it.

		# Easier approach: Patch `frappe.db.get_value` locally in the test methods OR
		# set the current user to one that is linked.
		# Even better: The original code likely checks `frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")`
		# So let's link our test employee to Administrator (current session user) temporarily.

		frappe.db.set_value("Employee", self.employee, "user_id", "Administrator")

	def tearDown(self):
		# Clean up any open intervals to prevent pollution
		frappe.db.delete("Job Interval", {"employee": self.employee})
		frappe.db.delete("Timesheet", {"employee": self.employee})
		super().tearDown()

	def test_log_time_flow(self):
		# 1. Start Job
		result = time_kiosk.log_time(
			project=self.project,
			action="Start",
			lat="10.0",
			lng="20.0",
			description="Integration Test Start",
			task=None
		)

		self.assertEqual(result["status"], "success")

		# Verify Job Interval created
		interval = frappe.get_last_doc("Job Interval", {"employee": self.employee})
		self.assertEqual(interval.status, "Open")
		self.assertEqual(interval.project, self.project)

		# 2. Try to Start again (Should Fail)
		with self.assertRaises(frappe.ValidationError):
			time_kiosk.log_time(project=self.project, action="Start")

		# 3. Stop Job
		# Simulate work duration to ensure valid Timesheet (1 hour duration)
		interval = frappe.get_last_doc("Job Interval", {"employee": self.employee})
		new_start = add_to_date(interval.start_time, hours=-1)
		frappe.db.set_value("Job Interval", interval.name, "start_time", new_start)

		result = time_kiosk.log_time(action="Stop")

		self.assertEqual(result["status"], "success")

		# Verify Job Interval closed
		interval.reload()
		self.assertEqual(interval.status, "Completed")
		self.assertIsNotNone(interval.end_time)
		self.assertEqual(interval.sync_status, "Synced")

		# Verify Timesheet Created/Synced
		# The logic aggregates logs or appends to timesheet.
		timesheet = frappe.get_last_doc("Timesheet", {"employee": self.employee})
		self.assertTrue(timesheet)
		self.assertEqual(len(timesheet.time_logs), 1)
		self.assertEqual(timesheet.time_logs[0].project, self.project)

		# 4. Try to Stop again (Should Fail)
		with self.assertRaises(frappe.ValidationError):
			time_kiosk.log_time(action="Stop")

	def test_strict_single_interval(self):
		# Manually insert an open interval
		frappe.get_doc({
			"doctype": "Job Interval",
			"employee": self.employee,
			"project": self.project,
			"status": "Open",
			"start_time": now_datetime()
		}).insert()

		# Try to create another manually (should fail if validation is in `validate`)
		# Or try via API
		with self.assertRaises(frappe.ValidationError):
			time_kiosk.log_time(project=self.project, action="Start")

