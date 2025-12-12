import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext_enhancements.calendar_sync import sync_to_google_calendar


class TestFixOptionsError(unittest.TestCase):
	@patch("erpnext_enhancements.calendar_sync.get_google_calendar_object")
	@patch("erpnext_enhancements.calendar_sync.frappe.db.get_all")
	@patch("erpnext_enhancements.calendar_sync.frappe.log_error")
	def test_missing_field_save_prevention(self, mock_log_error, mock_get_all, mock_get_gc_obj):
		"""
		Test that if 'google_calendar_events' field is missing in metadata,
		we do not attempt to append to it and log an error instead.
		"""
		# Setup mocks
		mock_service = MagicMock()
		mock_get_gc_obj.return_value = (mock_service, "creds")
		mock_service.events().patch.side_effect = Exception("Event not found")  # Force create path
		mock_service.events().insert().execute.return_value = {"id": "new_event_id"}

		mock_get_all.return_value = []  # No existing logs

		doc = MagicMock()
		doc.name = "TASK-001"
		doc.doctype = "Task"
		doc.get.return_value = None  # doc.get("google_calendar_events") -> None

		# Simulate missing field in meta
		doc.meta.get_field.return_value = None

		google_calendar_doc = MagicMock()
		google_calendar_doc.name = "Test Calendar"
		google_calendar_doc.google_calendar_id = "primary"

		# Run function
		sync_to_google_calendar(
			doc, google_calendar_doc, "Summary", "2023-01-01 10:00:00", "2023-01-01 11:00:00", "Description"
		)

		# Verify doc.append was NOT called
		doc.append.assert_not_called()

		# Verify error was logged
		mock_log_error.assert_called_with(
			message="Field 'google_calendar_events' missing in Task. Skipping event log save.",
			title="Google Calendar Sync Error",
		)

	@patch("erpnext_enhancements.calendar_sync.get_google_calendar_object")
	@patch("erpnext_enhancements.calendar_sync.frappe.db.get_all")
	@patch("erpnext_enhancements.calendar_sync.frappe.log_error")
	def test_attribute_error_catch(self, mock_log_error, mock_get_all, mock_get_gc_obj):
		"""
		Test that if doc.save() raises the specific AttributeError, it is caught and logged.
		"""
		# Setup mocks
		mock_service = MagicMock()
		mock_get_gc_obj.return_value = (mock_service, "creds")
		mock_service.events().insert().execute.return_value = {"id": "new_event_id"}

		mock_get_all.return_value = []

		doc = MagicMock()
		doc.name = "TASK-001"
		doc.doctype = "Task"
		doc.get.return_value = None

		# Field exists this time
		doc.meta.get_field.return_value = True

		# Simulate save raising AttributeError
		doc.save.side_effect = AttributeError("'NoneType' object has no attribute 'options'")

		google_calendar_doc = MagicMock()
		google_calendar_doc.name = "Test Calendar"

		# Run function
		sync_to_google_calendar(
			doc, google_calendar_doc, "Summary", "2023-01-01 10:00:00", "2023-01-01 11:00:00", "Description"
		)

		# Verify doc.append WAS called
		doc.append.assert_called()

		# Verify error was logged appropriately
		args, _ = mock_log_error.call_args
		self.assertIn("Google Calendar Sync Save Error (Metadata Issue)", args[0])
		self.assertIn("'NoneType' object has no attribute 'options'", args[0])


if __name__ == "__main__":
	# Minimal mock for frappe.get_doc or other calls if needed by the test runner context
	unittest.main()
