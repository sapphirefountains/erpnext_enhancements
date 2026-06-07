# Copyright (c) 2024, Sapphire Fountains and Contributors
# See license.txt
"""Unit tests for the server-side functions of the Project Dashboard page.

This test suite covers permission checks, data retrieval, and data update
functions located in `project_dashboard.py`.
"""

import unittest
from unittest.mock import patch

import frappe

from erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard import (
	check_permission,
	get_priority_options,
	get_project_data,
	get_project_tasks,
	get_status_options,
	update_project_details,
)


class TestProjectDashboardPermissions(unittest.TestCase):
	"""Tests for the `check_permission` function."""

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_roles"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_all"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.db.get_value"
	)
	def test_check_permission_allowed(self, mock_get_value, mock_get_all, mock_get_roles):
		"""Test that permission is granted when user has a permitted role."""
		mock_get_value.return_value = "Custom Role 1"
		mock_get_all.return_value = [{"role": "Project Manager"}]
		mock_get_roles.return_value = ["Project Manager", "System User"]
		self.assertTrue(check_permission())

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_roles"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_all"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.db.get_value"
	)
	def test_check_permission_denied(self, mock_get_value, mock_get_all, mock_get_roles):
		"""Test that permission is denied when user lacks a permitted role."""
		mock_get_value.return_value = "Custom Role 1"
		mock_get_all.return_value = [{"role": "Project Manager"}]
		mock_get_roles.return_value = ["Project User", "System User"]
		self.assertFalse(check_permission())

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_all"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.db.get_value"
	)
	def test_check_permission_no_roles_configured(self, mock_get_value, mock_get_all):
		"""Test that permission is denied if no roles are set in settings."""
		mock_get_value.return_value = None
		mock_get_all.return_value = []
		self.assertFalse(check_permission())

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.log_error"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_all"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.db.get_value"
	)
	def test_check_permission_exception(self, mock_get_value, mock_get_all, mock_log_error):
		"""Test that permission is denied when an exception occurs."""
		mock_get_value.side_effect = Exception("DB Error")
		self.assertFalse(check_permission())
		mock_log_error.assert_called_once()


class TestProjectDashboard(unittest.TestCase):
	"""Tests for data handling functions of the Project Dashboard."""

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard._get_assignee_names"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.check_permission"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.db.count"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_list"
	)
	def test_get_project_data_success(
		self, mock_get_list, mock_db_count, mock_check_permission, mock_get_assignee_names
	):
		"""Test successful retrieval and enrichment of project data."""
		mock_check_permission.return_value = True
		mock_projects = [{"name": "PROJ-001", "project_name": "Test Project 1"}]
		mock_get_list.return_value = mock_projects
		# Mocking the return values for total_tasks and completed_tasks counts
		mock_db_count.side_effect = [5, 2]
		mock_get_assignee_names.return_value = []

		result = get_project_data()

		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["name"], "PROJ-001")
		self.assertEqual(result[0]["total_tasks"], 5)
		self.assertEqual(result[0]["completed_tasks"], 2)
		mock_get_list.assert_called_once_with(
			"Project",
			fields=[
				"name",
				"project_name",
				"status",
				"project_type",
				"project_user",
				"custom_project_priority",
				"custom_company_priority",
				"is_active",
				"percent_complete",
				"expected_start_date",
				"expected_end_date",
			],
			filters={"status": ["!=", "Canceled"]},
			order_by="creation desc",
		)
		self.assertEqual(mock_db_count.call_count, 2)
		mock_db_count.assert_any_call("Task", {"project": "PROJ-001"})
		mock_db_count.assert_any_call("Task", {"project": "PROJ-001", "status": "Completed"})

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.check_permission"
	)
	def test_get_project_data_permission_denied(self, mock_check_permission):
		"""Test that an error is returned when permission is denied."""
		mock_check_permission.return_value = False
		result = get_project_data()
		self.assertEqual(result, {"error": "You do not have permission to view the Project Dashboard."})

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.check_permission"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.log_error"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_list"
	)
	def test_get_project_data_exception(self, mock_get_list, mock_log_error, mock_check_permission):
		"""Test error handling when fetching project data fails."""
		mock_check_permission.return_value = True
		mock_get_list.side_effect = Exception("Database connection failed")
		result = get_project_data()
		self.assertEqual(result, {"error": "Could not fetch project data. Please check the logs."})
		mock_log_error.assert_called_once()

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard._fetch_all_project_tasks"
	)
	def test_get_project_tasks_success(self, mock_fetch_tasks):
		"""Test successful fetching and hierarchical structuring of tasks."""
		mock_tasks = [
			frappe._dict({"name": "TASK-001", "subject": "Root Task 1", "parent_task": None}),
			frappe._dict({"name": "TASK-002", "subject": "Child Task 1.1", "parent_task": "TASK-001"}),
			frappe._dict({"name": "TASK-003", "subject": "Root Task 2", "parent_task": None}),
			frappe._dict({"name": "TASK-004", "subject": "Child Task 1.2", "parent_task": "TASK-001"}),
		]
		mock_fetch_tasks.return_value = mock_tasks
		with patch(
			"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard._get_assignee_names",
			return_value=[{"full_name": "test user"}],
		):
			result = get_project_tasks("PROJ-001")

		self.assertEqual(len(result), 2, "Should return two root tasks.")
		root1 = next(t for t in result if t["name"] == "TASK-001")
		root2 = next(t for t in result if t["name"] == "TASK-003")
		self.assertEqual(len(root1["children"]), 2, "First root task should have two children.")
		self.assertEqual(len(root2["children"]), 0, "Second root task should have no children.")
		child_names = {c["name"] for c in root1["children"]}
		self.assertEqual(child_names, {"TASK-002", "TASK-004"})

	def test_get_project_tasks_no_project(self):
		"""Test that an error is returned if no project name is provided."""
		result = get_project_tasks(None)
		self.assertEqual(result, {"error": "Project name is required."})

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.log_error"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard._fetch_all_project_tasks"
	)
	def test_get_project_tasks_exception(self, mock_fetch_tasks, mock_log_error):
		"""Test error handling when fetching tasks fails."""
		mock_fetch_tasks.side_effect = Exception("DB Error")
		result = get_project_tasks("PROJ-001")
		self.assertEqual(result, {"error": "Could not fetch tasks for project PROJ-001. Please check logs."})
		mock_log_error.assert_called_once()

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_meta"
	)
	def test_get_priority_options_success(self, mock_get_meta):
		"""Test successful retrieval of priority options."""
		mock_meta = mock_get_meta.return_value
		mock_meta.fields = [
			frappe._dict({"fieldname": "custom_project_priority", "options": "High\nMedium\nLow"}),
			frappe._dict({"fieldname": "custom_company_priority", "options": "1\n2\n3"}),
		]
		result = get_priority_options()
		self.assertEqual(
			result, {"project_priority": ["High", "Medium", "Low"], "company_priority": ["1", "2", "3"]}
		)

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_meta"
	)
	def test_get_priority_options_no_field(self, mock_get_meta):
		"""Test behavior when priority field is not found in metadata."""
		mock_meta = mock_get_meta.return_value
		mock_meta.fields = []
		result = get_priority_options()
		self.assertEqual(result, {"project_priority": [], "company_priority": []})

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.log_error"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_meta"
	)
	def test_get_priority_options_exception(self, mock_get_meta, mock_log_error):
		"""Test error handling when an exception occurs fetching priorities."""
		mock_get_meta.side_effect = Exception("Meta error")
		result = get_priority_options()
		self.assertEqual(result, {"error": "Could not fetch priority options."})
		mock_log_error.assert_called_once()

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_meta"
	)
	def test_get_status_options_success(self, mock_get_meta):
		"""Test successful retrieval of status options."""
		mock_meta = mock_get_meta.return_value
		mock_meta.fields = [
			frappe._dict(
				{
					"fieldname": "status",
					"options": "Active\nClient Hold\nParked\nCompleted\nInvoiced\nPaid\nCanceled",
				}
			)
		]
		result = get_status_options()
		self.assertEqual(
			result, ["Active", "Client Hold", "Parked", "Completed", "Invoiced", "Paid", "Canceled"]
		)

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.log_error"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_meta"
	)
	def test_get_status_options_exception(self, mock_get_meta, mock_log_error):
		"""Test error handling when an exception occurs fetching statuses."""
		mock_get_meta.side_effect = Exception("Meta error")
		result = get_status_options()
		self.assertEqual(result, {"error": "Could not fetch status options."})
		mock_log_error.assert_called_once()

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.db.set_value"
	)
	def test_update_project_details_success(self, mock_set_value):
		"""Test successful update of a single project field."""
		result = update_project_details("PROJ-001", "status", "Completed")
		mock_set_value.assert_called_once_with("Project", "PROJ-001", "status", "Completed")
		self.assertEqual(result, {"status": "success"})

	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.log_error"
	)
	@patch(
		"erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.db.set_value"
	)
	def test_update_project_details_exception(self, mock_set_value, mock_log_error):
		"""Test error handling when updating a project field fails."""
		mock_set_value.side_effect = Exception("Failed to write to database")
		result = update_project_details("PROJ-001", "status", "Completed")
		self.assertEqual(
			result, {"status": "error", "message": "Could not update project. Please check the logs."}
		)
		mock_log_error.assert_called_once()

	@patch("erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_meta")
	@patch("erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard.frappe.get_all")
	def test_get_gantt_tasks_no_baseline_fields(self, mock_get_all, mock_get_meta):
		"""Test fetching Gantt tasks when baseline fields are missing from Task DocType."""
		from erpnext_enhancements.project_enhancements.page.project_dashboard.project_dashboard import get_gantt_tasks_for_project
		
		# Mock Task meta to not have baseline fields
		mock_meta = mock_get_meta.return_value
		mock_meta.has_field.side_effect = lambda f: f not in ["baseline_start_date", "baseline_end_date"]
		
		# Mock tasks return
		mock_get_all.side_effect = [
			[{"name": "TASK-001", "subject": "Test Task", "exp_start_date": "2024-01-01", "exp_end_date": "2024-01-05", "progress": 50, "status": "Open", "is_milestone": 0}], # Task query
			[], # Dependencies query
			[]  # ToDo query
		]
		
		result = get_gantt_tasks_for_project("PROJ-001")
		
		# Verify baseline fields were NOT requested in the first get_all call
		args, kwargs = mock_get_all.call_args_list[0]
		self.assertNotIn("baseline_start_date", kwargs["fields"])
		self.assertNotIn("baseline_end_date", kwargs["fields"])
		
		# Verify result has the fields (as None)
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]["id"], "TASK-001")
		self.assertIsNone(result[0]["baseline_start"])
		self.assertIsNone(result[0]["baseline_end"])
