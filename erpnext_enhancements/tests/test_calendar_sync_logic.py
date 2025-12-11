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

from erpnext_enhancements.calendar_sync import get_google_calendars_for_doctype, sync_doctype_to_event


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
	def test_global_mapping_precedence(self, mock_get_doc, mock_get_value, mock_get_single):
		# Setup: Global mapping exists for 'Task'
		mock_settings = MagicMock()
		mock_settings.google_calendar_sync_map = [
			MagicMock(reference_doctype="Task", google_calendar="Global Calendar")
		]
		mock_get_single.return_value = mock_settings
		mock_gc_doc = MagicMock(enable=1)
		mock_get_doc.return_value = mock_gc_doc

		# Execute
		calendars = get_google_calendars_for_doctype("Task", "test_user@example.com")

		# Assert
		self.assertEqual(len(calendars), 1)
		self.assertEqual(calendars[0], mock_gc_doc)
		mock_get_doc.assert_called_with("Google Calendar", "Global Calendar")
		mock_get_value.assert_not_called()  # User calendar should not be checked

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
