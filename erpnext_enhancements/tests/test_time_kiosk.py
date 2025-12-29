import unittest
from unittest.mock import MagicMock, patch, ANY
import sys
import datetime

# Mock frappe module
mock_frappe = MagicMock()
sys.modules["frappe"] = mock_frappe
sys.modules["frappe.utils"] = MagicMock()
sys.modules["frappe.model"] = MagicMock()
sys.modules["frappe.model.document"] = MagicMock()

# Make whitelist a passthrough decorator
def whitelist_passthrough(*args, **kwargs):
    def decorator(func):
        return func
    if len(args) == 1 and callable(args[0]):
        return args[0]
    return decorator

mock_frappe.whitelist = whitelist_passthrough

# Make frappe._ return the string itself or format it
def mock_translate(msg):
    return msg

mock_frappe._ = mock_translate

import frappe
from frappe import _

# Configure now_datetime mock
frappe.utils.now_datetime = MagicMock(return_value=datetime.datetime(2023, 1, 1, 12, 0, 0))
frappe.utils.get_datetime = MagicMock(side_effect=lambda x: x if isinstance(x, datetime.datetime) else datetime.datetime.now())

# Import the module to test
import erpnext_enhancements.api.time_kiosk as time_kiosk

class TestTimeKiosk(unittest.TestCase):
    def setUp(self):
        frappe.session.user = "test@example.com"
        frappe.db.get_value.reset_mock()
        frappe.db.get_value.side_effect = None # Clear side effects
        frappe.db.exists.reset_mock()
        frappe.db.exists.side_effect = None
        frappe.get_doc.reset_mock()
        frappe.get_doc.side_effect = None
        frappe.throw.reset_mock()
        frappe.throw.side_effect = None
        frappe.get_list.reset_mock()
        frappe.get_list.side_effect = None

    def test_log_time_start_success(self):
        # Setup mocks
        frappe.db.get_value.side_effect = lambda dt, filters, field, **kwargs: "EMP-001" if dt == "Employee" else None
        frappe.db.exists.return_value = None # No existing interval

        mock_doc = MagicMock()
        mock_doc.name = "JOB-INT-00001"
        frappe.get_doc.return_value = mock_doc

        # Execute
        result = time_kiosk.log_time("PROJ-001", "Start", "10.0", "20.0", "Test Description")

        # Verify
        self.assertEqual(result["status"], "success")
        frappe.get_doc.assert_called_once()
        args = frappe.get_doc.call_args[0][0]
        self.assertEqual(args["doctype"], "Job Interval")
        self.assertEqual(args["employee"], "EMP-001")
        self.assertEqual(args["project"], "PROJ-001")
        self.assertEqual(args["status"], "Open")
        self.assertEqual(args["latitude"], "10.0")
        self.assertEqual(args["longitude"], "20.0")
        self.assertEqual(args["description"], "Test Description")
        mock_doc.insert.assert_called_once()

    def test_log_time_start_fail_existing(self):
        # Setup mocks
        frappe.db.get_value.return_value = "EMP-001"
        frappe.db.exists.return_value = "JOB-INT-EXISTING"

        # Execute
        time_kiosk.log_time("PROJ-001", "Start")

        # Verify
        # Check that throw was called with a string containing "already have an open job"
        frappe.throw.assert_called()
        self.assertIn("already have an open job", frappe.throw.call_args[0][0])

    def test_log_time_stop_success(self):
        # Setup mocks
        # logic:
        # 1. get employee -> "EMP-001"
        # 2. get open interval -> "JOB-INT-OPEN"
        # 3. sync_interval_to_timesheet calls get_value("Timesheet") -> "TS-001"

        def get_value_side_effect(dt, filters, field=None, **kwargs):
            if dt == "Employee": return "EMP-001"
            if dt == "Job Interval" and isinstance(filters, dict) and filters.get("status") == "Open": return "JOB-INT-OPEN"
            if dt == "Timesheet": return "TS-001" # Existing timesheet
            return None

        frappe.db.get_value.side_effect = get_value_side_effect

        mock_doc = MagicMock()
        mock_doc.name = "JOB-INT-OPEN"
        mock_doc.start_time = datetime.datetime(2023, 1, 1, 10, 0, 0)
        mock_doc.end_time = datetime.datetime(2023, 1, 1, 12, 0, 0)

        mock_ts = MagicMock()
        mock_ts.time_logs = []

        def get_doc_side_effect(dt, name=None):
            if dt == "Job Interval": return mock_doc
            if dt == "Timesheet": return mock_ts
            return MagicMock()

        frappe.get_doc.side_effect = get_doc_side_effect

        # Execute
        result = time_kiosk.log_time(None, "Stop")

        # Verify
        self.assertEqual(result["status"], "success")

        # Verify Job Interval was saved
        # Note: We can't use assert_called_with on frappe.get_doc because it's called multiple times
        # But we can verify mock_doc.save was called
        self.assertEqual(mock_doc.status, "Completed")
        mock_doc.save.assert_called()

        # Verify Timesheet sync happened
        mock_ts.append.assert_called()
        mock_ts.save.assert_called()

    def test_log_time_stop_fail_no_open(self):
        # Setup mocks
        frappe.db.get_value.side_effect = lambda dt, filters, field=None, **kwargs: "EMP-001" if dt == "Employee" else None

        # Execute
        time_kiosk.log_time(None, "Stop")

        # Verify
        frappe.throw.assert_called()
        self.assertIn("No open job found", frappe.throw.call_args[0][0])

    def test_no_employee(self):
        frappe.db.get_value.return_value = None
        time_kiosk.log_time("PROJ-001", "Start")
        frappe.throw.assert_called()
        self.assertIn("No Employee record found", frappe.throw.call_args[0][0])

    def test_get_current_status(self):
        frappe.db.get_value.side_effect = [
            "EMP-001", # Employee
            {"name": "JOB-1", "project": "PROJ-1", "start_time": "Now", "description": "Desc"}, # Interval
            "Test Project" # Project Title
        ]

        status = time_kiosk.get_current_status()
        self.assertEqual(status["project_title"], "Test Project")
        self.assertEqual(status["employee"], "EMP-001")

    @patch('erpnext_enhancements.api.time_kiosk.frappe.get_meta')
    def test_get_projects_active_field(self, mock_get_meta):
        mock_field = MagicMock()
        mock_field.fieldname = "is_active"
        mock_get_meta.return_value.fields = [mock_field]

        time_kiosk.get_projects()

        frappe.get_list.assert_called_with("Project", filters={"is_active": "Yes"}, fields=["name", "project_name"])

    @patch('erpnext_enhancements.api.time_kiosk.frappe.get_meta')
    def test_get_projects_no_active_field(self, mock_get_meta):
        mock_field = MagicMock()
        mock_field.fieldname = "status"
        mock_get_meta.return_value.fields = [mock_field]

        time_kiosk.get_projects()

        frappe.get_list.assert_called_with("Project", filters={"status": "Open"}, fields=["name", "project_name"])

if __name__ == '__main__':
    unittest.main()
