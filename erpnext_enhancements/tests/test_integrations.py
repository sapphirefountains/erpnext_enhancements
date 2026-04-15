# -*- coding: utf-8 -*-
import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import patch, MagicMock
import responses
import erpnext_enhancements.calendar_sync as calendar_sync

class TestCalendarSync(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.create_test_data()

	def create_test_data(self):
		# Enable Google Settings first
		google_settings = frappe.get_doc("Google Settings")
		google_settings.enable = 1
		google_settings.client_id = "test_client_id"
		google_settings.client_secret = "test_client_secret"
		google_settings.save()

		# Create "Test Global Calendar"
		if not frappe.db.exists("Google Calendar", "Test Global Calendar"):
			frappe.get_doc({
				"doctype": "Google Calendar",
				"calendar_name": "Test Global Calendar",
				"user": "Administrator",
				"google_calendar_id": "global_cal_id",
				"enable": 1
			}).insert()

		# Create Settings
		if not frappe.db.exists("ERPNext Enhancements Settings"):
			self.settings = frappe.get_doc({"doctype": "ERPNext Enhancements Settings"})
			self.settings.insert()
		else:
			self.settings = frappe.get_doc("ERPNext Enhancements Settings")

		# Add mapping
		self.settings.append("google_calendar_sync_map", {
			"reference_doctype": "Task",
			"google_calendar": "Test Global Calendar"
		})
		self.settings.save()

	@patch('frappe.integrations.doctype.google_calendar.google_calendar.get_google_calendar_object')
	def test_sync_to_google_calendar(self, mock_get_gc_object):
		# Mock Google Service
		mock_service = MagicMock()
		mock_get_gc_object.return_value = (mock_service, None)
		mock_service.events().insert().execute.return_value = {"id": "g_event_123"}

		# Create Task
		task = frappe.get_doc({
			"doctype": "Task",
			"subject": "Test Sync Task",
			"status": "Open",
			"exp_start_date": "2024-01-01",
			"exp_end_date": "2024-01-02"
		}).insert()

		# Run sync
		# Depending on implementation, this might be called automatically via hooks or we call it manually
		calendar_sync.run_google_calendar_sync(task, "on_update")

		# Assert event inserted
		mock_service.events().insert.assert_called()

		# Assert Log created
		log = frappe.db.get_value("Global Calendar Sync Log", {"reference_docname": task.name}, "event_id")
		self.assertEqual(log, "g_event_123")

	@patch('frappe.integrations.doctype.google_calendar.google_calendar.get_google_calendar_object')
	def test_delete_event_from_google(self, mock_get_gc_object):
		# Mock Service
		mock_service = MagicMock()
		mock_get_gc_object.return_value = (mock_service, None)

		# Create Task and Log entry to simulate existing sync
		task = frappe.get_doc({
			"doctype": "Task",
			"subject": "Test Delete Task",
			"status": "Open"
		}).insert()

		frappe.get_doc({
			"doctype": "Global Calendar Sync Log",
			"reference_doctype": "Task",
			"reference_docname": task.name,
			"google_calendar": "Test Global Calendar",
			"event_id": "existing_event_id"
		}).insert()

		# Trigger delete (e.g. Cancelled)
		task.status = "Cancelled"
		# We manually call delete logic because status change usually triggers it inside `run_google_calendar_sync`
		frappe.flags.sync_source = "background_worker"
		calendar_sync.delete_event_from_google(task, "on_update")
		frappe.flags.sync_source = None

		# Assert delete called
		mock_service.events().delete.assert_called_with(calendarId="global_cal_id", eventId="existing_event_id")

		# Assert Log deleted
		self.assertFalse(frappe.db.exists("Global Calendar Sync Log", {"reference_docname": task.name}))
