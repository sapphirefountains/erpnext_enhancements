import sys
import unittest
from unittest.mock import MagicMock, patch

# Check if frappe is already imported, if not mock it
if "frappe" not in sys.modules:
    frappe = MagicMock()
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = MagicMock()
    sys.modules["frappe.integrations.doctype.google_calendar.google_calendar"] = MagicMock()
    sys.modules["googleapiclient.errors"] = MagicMock()
else:
    import frappe

from erpnext_enhancements.calendar_sync import get_google_calendars_for_doctype, sync_doctype_to_event, get_sync_data


class TestCalendarSyncLogic(unittest.TestCase):
	@patch("erpnext_enhancements.calendar_sync.delete_event_from_google")
	@patch("erpnext_enhancements.calendar_sync.get_sync_data")
	@patch("erpnext_enhancements.calendar_sync.get_google_calendars_for_doctype")
	@patch("erpnext_enhancements.calendar_sync.sync_to_google_calendar")
	def test_sync_deletion_criteria(self, mock_sync, mock_get_calendars, mock_get_data, mock_delete):
		# Setup mocks
		mock_get_data.return_value = ("start", "end", "summary", "desc", "loc")

		# Helper to create a dummy doc
		def get_doc(doctype, status):
			doc = MagicMock()
			doc.doctype = doctype
			doc.owner = "test@example.com"
			doc.get.side_effect = lambda key: status if key == "status" else None
			# Fix: mock flags to be False initially so sync proceeds
			doc.flags = MagicMock()
			doc.flags.in_google_calendar_sync = False
			return doc

		# Test Task
		# Task should be deleted if status is Cancelled or Closed
		doc = get_doc("Task", "Cancelled")
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_called_with(doc, "on_update")
		mock_delete.reset_mock()

		doc = get_doc("Task", "Closed")
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_called_with(doc, "on_update")
		mock_delete.reset_mock()

		doc = get_doc("Task", "Completed")
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_not_called()
		mock_delete.reset_mock()

		# Test Event
		# Event should be deleted only if Cancelled
		doc = get_doc("Event", "Cancelled")
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_called_with(doc, "on_update")
		mock_delete.reset_mock()

		doc = get_doc("Event", "Closed") # Should NOT delete
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_not_called()
		mock_delete.reset_mock()

		# Test Project
		# Project should be deleted only if Cancelled
		doc = get_doc("Project", "Cancelled")
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_called_with(doc, "on_update")
		mock_delete.reset_mock()

		doc = get_doc("Project", "Completed") # Should NOT delete
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_not_called()
		mock_delete.reset_mock()

		# Test ToDo
		# ToDo should be deleted only if Cancelled
		doc = get_doc("ToDo", "Cancelled")
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_called_with(doc, "on_update")
		mock_delete.reset_mock()

		doc = get_doc("ToDo", "Closed") # Should NOT delete
		sync_doctype_to_event(doc, "on_update")
		mock_delete.assert_not_called()
		mock_delete.reset_mock()


class TestGetGoogleCalendars(unittest.TestCase):
	@patch("frappe.get_single")
	@patch("frappe.db.get_value")
	@patch("frappe.get_doc")
	def test_combines_global_and_user_calendars(self, mock_get_doc, mock_get_value, mock_get_single):
		# Setup: Global mapping exists for 'Task', and user has a personal calendar.
		mock_settings = MagicMock()
		mock_settings.google_calendar_sync_map = [
			MagicMock(reference_doctype="Task", google_calendar="Global Calendar")
		]
		mock_get_single.return_value = mock_settings

		mock_get_value.return_value = "User Calendar"

		# Return different mocks for global and user calendars
		global_cal = MagicMock(enable=1)
		global_cal.name = "Global Calendar"
		user_cal = MagicMock(enable=1)
		user_cal.name = "User Calendar"

		def get_doc_side_effect(doctype, name):
			if name == "Global Calendar":
				return global_cal
			if name == "User Calendar":
				return user_cal
			return MagicMock()

		mock_get_doc.side_effect = get_doc_side_effect

		# Execute
		calendars = get_google_calendars_for_doctype("Task", "test_user@example.com")

		# Assert: Both calendars should be returned
		self.assertEqual(len(calendars), 2)
		self.assertIn(global_cal, calendars)
		self.assertIn(user_cal, calendars)
		mock_get_doc.assert_any_call("Google Calendar", "Global Calendar")
		mock_get_value.assert_called_with(
			"Google Calendar", {"user": "test_user@example.com", "enable": 1}, "name"
		)
		mock_get_doc.assert_any_call("Google Calendar", "User Calendar")

	@patch("frappe.get_single")
	@patch("frappe.db.get_value")
	@patch("frappe.get_doc")
	def test_deduplicates_calendars(self, mock_get_doc, mock_get_value, mock_get_single):
		# Setup: Global mapping and user calendar refer to the same calendar.
		mock_settings = MagicMock()
		mock_settings.google_calendar_sync_map = [
			MagicMock(reference_doctype="Task", google_calendar="Shared Calendar")
		]
		mock_get_single.return_value = mock_settings

		mock_get_value.return_value = "Shared Calendar"

		shared_cal = MagicMock(enable=1)
		shared_cal.name = "Shared Calendar"
		mock_get_doc.return_value = shared_cal

		# Execute
		calendars = get_google_calendars_for_doctype("Task", "test_user@example.com")

		# Assert: Only one calendar instance is returned.
		self.assertEqual(len(calendars), 1)
		self.assertEqual(calendars[0], shared_cal)
		mock_get_doc.assert_called_once_with("Google Calendar", "Shared Calendar")

	@patch("frappe.get_single")
	@patch("frappe.db.get_value")
	@patch("frappe.get_doc")
	def test_user_calendar_fallback(self, mock_get_doc, mock_get_value, mock_get_single):
		# Setup: No global mapping for 'ToDo'
		mock_settings = MagicMock()
		mock_settings.google_calendar_sync_map = [
			MagicMock(reference_doctype="Task", google_calendar="Global Calendar")
		]
		mock_get_single.return_value = mock_settings
		mock_get_value.return_value = "User Calendar"  # User has a calendar
		mock_gc_doc = MagicMock(enable=1)
		mock_gc_doc.name = "User Calendar"
		mock_get_doc.return_value = mock_gc_doc

		# Execute
		calendars = get_google_calendars_for_doctype("ToDo", "test_user@example.com")

		# Assert
		self.assertEqual(len(calendars), 1)
		self.assertEqual(calendars[0], mock_gc_doc)
		mock_get_value.assert_called_with(
			"Google Calendar", {"user": "test_user@example.com", "enable": 1}, "name"
		)
		mock_get_doc.assert_called_with("Google Calendar", "User Calendar")

	@patch("frappe.get_single")
	@patch("frappe.db.get_value")
	def test_no_config_found(self, mock_get_value, mock_get_single):
		# Setup: No global mapping and no user calendar
		mock_settings = MagicMock()
		mock_settings.google_calendar_sync_map = []
		mock_get_single.return_value = mock_settings
		mock_get_value.return_value = None

		# Execute
		calendars = get_google_calendars_for_doctype("Event", "test_user@example.com")

		# Assert
		self.assertEqual(len(calendars), 0)


class TestGetSyncData(unittest.TestCase):
	def test_project_fallback_dates(self):
		# Setup a mock Project doc that will return None for custom fields
		# but will return values for the standard date fields.
		doc = MagicMock()
		doc.doctype = "Project"
		doc.get.side_effect = lambda key: {
			"custom_calendar_datetime_start": None,
			"custom_calendar_datetime_end": None,
			"expected_start_date": "2024-01-01",
			"expected_end_date": "2024-01-31",
			"project_name": "Test Project Fallback"
		}.get(key)

		start_dt, end_dt, _, _, _ = get_sync_data(doc)

		self.assertEqual(start_dt, "2024-01-01")
		self.assertEqual(end_dt, "2024-01-31")

	def test_todo_fallback_dates(self):
		# Setup a mock ToDo doc with no custom dates but a due_date
		doc = MagicMock()
		doc.doctype = "ToDo"
		doc.get.side_effect = lambda key: {
			"custom_calendar_datetime_start": None,
			"custom_calendar_datetime_end": None,
			"due_date": "2024-02-15 10:00:00",
			"description": "Test ToDo Fallback"
		}.get(key)

		# We also need to patch add_to_date used by the fallback
		with patch("erpnext_enhancements.calendar_sync.add_to_date") as mock_add_date:
			mock_add_date.return_value = "2024-02-15 11:00:00" # Expected end time

			start_dt, end_dt, _, _, _ = get_sync_data(doc)

			self.assertEqual(start_dt, "2024-02-15 10:00:00")
			self.assertEqual(end_dt, "2024-02-15 11:00:00")
			mock_add_date.assert_called_once_with("2024-02-15 10:00:00", hours=1)
