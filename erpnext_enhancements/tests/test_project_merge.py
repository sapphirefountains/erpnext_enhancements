import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.project_merge import merge_projects


class TestProjectMerge(FrappeTestCase):
	def setUp(self):
		super().setUp()

		# Create Company
		self.company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "_Test Company Merge",
				"default_currency": "USD",
				"country": "United States",
			}
		)
		if not frappe.db.exists("Company", "_Test Company Merge"):
			self.company.insert(ignore_permissions=True)
		else:
			self.company = frappe.get_doc("Company", "_Test Company Merge")

		# Create Source Project
		self.source_project = frappe.new_doc("Project")
		self.source_project.project_name = "Source Project Test"
		self.source_project.company = "_Test Company Merge"
		self.source_project.status = "Active"
		# Bypass validation as "Open" is forced but invalid
		self.source_project._validate_selects = lambda: None
		self.source_project.insert(ignore_permissions=True)
		if self.source_project.status != "Active":
			frappe.db.set_value("Project", self.source_project.name, "status", "Active")
			self.source_project.reload()

		# Create Target Project
		self.target_project = frappe.new_doc("Project")
		self.target_project.project_name = "Target Project Test"
		self.target_project.company = "_Test Company Merge"
		self.target_project.status = "Active"
		# Bypass validation as "Open" is forced but invalid
		self.target_project._validate_selects = lambda: None
		self.target_project.insert(ignore_permissions=True)
		if self.target_project.status != "Active":
			frappe.db.set_value("Project", self.target_project.name, "status", "Active")
			self.target_project.reload()

		# Create a Task linked to Source Project
		self.task = frappe.get_doc(
			{
				"doctype": "Task",
				"subject": "Test Task for Merge",
				"project": self.source_project.name,
				"company": "_Test Company Merge",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		# Clean up
		if hasattr(self, "task"):
			frappe.delete_doc("Task", self.task.name, force=True)
		if hasattr(self, "source_project"):
			frappe.delete_doc("Project", self.source_project.name, force=True)
		if hasattr(self, "target_project"):
			frappe.delete_doc("Project", self.target_project.name, force=True)

		super().tearDown()

	def test_merge_projects(self):
		print(f"DEBUG: Source: {self.source_project.name}, Target: {self.target_project.name}")

		# Verify initial state
		self.assertEqual(self.task.project, self.source_project.name)

		# Perform Merge
		print("DEBUG: Calling merge_projects")
		merge_projects(self.source_project.name, self.target_project.name)
		print("DEBUG: merge_projects returned")

		# Reload documents
		self.task.reload()
		self.source_project.reload()

		# Verify Task is moved
		self.assertEqual(self.task.project, self.target_project.name)

		# Verify Source Project is Cancelled
		self.assertEqual(self.source_project.status, "Canceled")
