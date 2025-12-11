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

from erpnext_enhancements.calendar_sync import sync_doctype_to_event

class TestCalendarSyncLogic(unittest.TestCase):
	@patch("erpnext_enhancements.calendar_sync.delete_event_from_google")
	@patch("erpnext_enhancements.calendar_sync.get_sync_data")
	@patch("erpnext_enhancements.calendar_sync.get_google_calendars_for_doctype")
	@patch("erpnext_enhancements.calendar_sync.sync_to_google_calendar")
	def test_sync_deletion_criteria(self, mock_sync, mock_get_calendars, mock_get_data, mock_delete):
		# Setup mocks
		mock_get_data.return_value = (None, None, None, None, None)

		# Helper to create a dummy doc
		def get_doc(doctype, status):
			doc = MagicMock()
			doc.doctype = doctype
			doc.get.side_effect = lambda key: status if key == "status" else None
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
