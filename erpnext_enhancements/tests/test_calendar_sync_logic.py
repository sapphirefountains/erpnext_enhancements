import sys
import unittest
from unittest.mock import MagicMock, patch

# Check if frappe is already imported, if not mock it
if "frappe" not in sys.modules:
    frappe = MagicMock()
    frappe.utils = MagicMock()
    frappe.integrations = MagicMock()
    frappe.integrations.doctype = MagicMock()
    frappe.integrations.doctype.google_calendar = MagicMock()
    frappe.integrations.doctype.google_calendar.google_calendar = MagicMock()
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = frappe.utils
    sys.modules["frappe.integrations"] = frappe.integrations
    sys.modules["frappe.integrations.doctype"] = frappe.integrations.doctype
    sys.modules["frappe.integrations.doctype.google_calendar"] = frappe.integrations.doctype.google_calendar
    sys.modules["frappe.integrations.doctype.google_calendar.google_calendar"] = frappe.integrations.doctype.google_calendar.google_calendar
else:
    import frappe

# Mock googleapiclient and create a proper Exception for HttpError
if "googleapiclient" not in sys.modules:
    googleapiclient = MagicMock()
    sys.modules["googleapiclient"] = googleapiclient
    googleapiclient_errors = MagicMock()
    googleapiclient_errors.HttpError = type('HttpError', (Exception,), {})
    sys.modules["googleapiclient.errors"] = googleapiclient_errors


from erpnext_enhancements.calendar_sync import get_google_calendars_for_doctype, sync_doctype_to_event, get_sync_data, has_relevant_fields_changed, delete_event_from_google
from datetime import datetime


class TestDeleteEventFromGoogle(unittest.TestCase):
	@patch('erpnext_enhancements.calendar_sync.get_google_calendars_for_doctype')
	@patch('frappe.integrations.doctype.google_calendar.google_calendar.get_google_calendar_object')
	def test_delete_legacy_event_on_trash(self, mock_get_gc_object, mock_get_calendars):
		# Setup mocks for Google Calendar API
		mock_service = MagicMock()
		mock_get_gc_object.return_value = (mock_service, None)

		mock_calendar_conf = MagicMock()
		mock_calendar_conf.google_calendar_id = 'primary_test_calendar'
		mock_get_calendars.return_value = [mock_calendar_conf]

		# Setup a mock document with a legacy event ID
		doc = MagicMock()
		doc.doctype = 'ToDo'
		doc.owner = 'test_user@example.com'
		legacy_event_id = 'legacy_event_xyz123'

		# Configure the .get() method on the mock
		doc.get.side_effect = lambda key: {
			'custom_google_event_id': legacy_event_id,
			'google_calendar_events': []  # No child table events
		}.get(key)

		doc.meta.has_field.return_value = True

		# Call the function being tested, simulating an 'on_trash' event
		delete_event_from_google(doc, method='on_trash')

		# Assertions to verify the correct calls were made
		mock_get_calendars.assert_called_once_with('ToDo', 'test_user@example.com')
		mock_get_gc_object.assert_called_once_with(mock_calendar_conf)
		mock_service.events().delete.assert_called_once_with(
			calendarId='primary_test_calendar', eventId=legacy_event_id
		)

		# Ensure that doc.save() is not called during 'on_trash'
		doc.save.assert_not_called()


class TestHasRelevantFieldsChanged(unittest.TestCase):
	@patch('erpnext_enhancements.calendar_sync.get_datetime')
	def test_datetime_normalization_and_change_detection(self, mock_get_datetime):
		# Mock get_datetime to behave like the real one for this test
		def get_dt_side_effect(val):
			if isinstance(val, datetime):
				return val
			# Basic string parsing for test purposes
			return datetime.strptime(str(val).split('.')[0], '%Y-%m-%d %H:%M:%S')
		mock_get_datetime.side_effect = get_dt_side_effect

		doc_before_save = MagicMock()
		doc = MagicMock()
		doc.doctype = "ToDo"
		doc.is_new.return_value = False
		doc.get_doc_before_save.return_value = doc_before_save

		# Case 1: No change (different format, same value)
		doc_before_save.get.side_effect = lambda key: {"due_date": datetime(2024, 1, 1, 10, 0, 0)}.get(key)
		doc.get.side_effect = lambda key: {"due_date": "2024-01-01 10:00:00.000000"}.get(key)
		self.assertFalse(has_relevant_fields_changed(doc), "Should detect no change for same datetime in different format")

		# Case 2: Change in time
		doc.get.side_effect = lambda key: {"due_date": "2024-01-01 11:00:00"}.get(key)
		self.assertTrue(has_relevant_fields_changed(doc), "Should detect change in datetime")


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

	def test_todo_uses_due_date(self):
		# Setup a mock ToDo doc with a due_date
		doc = MagicMock()
		doc.doctype = "ToDo"
		doc.get.side_effect = lambda key: {
			"due_date": "2024-02-15 10:00:00",
			"description": "Test ToDo uses due_date"
		}.get(key)

		# We also need to patch add_to_date
		with patch("erpnext_enhancements.calendar_sync.add_to_date") as mock_add_date:
			mock_add_date.return_value = "2024-02-15 11:00:00" # Expected end time

			start_dt, end_dt, _, _, _ = get_sync_data(doc)

			self.assertEqual(start_dt, "2024-02-15 10:00:00")
			self.assertEqual(end_dt, "2024-02-15 11:00:00")
			mock_add_date.assert_called_once_with("2024-02-15 10:00:00", hours=1)

	def test_todo_ignores_custom_dates(self):
		# Setup a mock ToDo doc with both custom dates and a due_date
		# to ensure due_date is always used.
		doc = MagicMock()
		doc.doctype = "ToDo"
		doc.get.side_effect = lambda key: {
			"custom_calendar_datetime_start": "2099-01-01 12:00:00", # Should be ignored
			"custom_calendar_datetime_end": "2099-01-01 13:00:00",   # Should be ignored
			"due_date": "2024-03-20 14:00:00",                      # Should be used
			"description": "Test ToDo Prioritization"
		}.get(key)

		with patch("erpnext_enhancements.calendar_sync.add_to_date") as mock_add_date:
			mock_add_date.return_value = "2024-03-20 15:00:00"

			start_dt, end_dt, _, _, _ = get_sync_data(doc)

			self.assertEqual(start_dt, "2024-03-20 14:00:00")
			self.assertEqual(end_dt, "2024-03-20 15:00:00")
			mock_add_date.assert_called_once_with("2024-03-20 14:00:00", hours=1)
