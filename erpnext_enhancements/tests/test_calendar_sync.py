import sys
import unittest
from unittest.mock import MagicMock, patch

# --- Mocking Frappe and other dependencies ---

# Create a master mock for the frappe module
frappe = MagicMock()

# Mock submodules and functions that are imported in calendar_sync.py
frappe.utils = MagicMock()
frappe.integrations = MagicMock()
frappe.integrations.doctype = MagicMock()
frappe.integrations.doctype.google_calendar = MagicMock()
frappe.integrations.doctype.google_calendar.google_calendar = MagicMock()

# Mock googleapiclient
googleapiclient = MagicMock()
googleapiclient.errors = MagicMock()

# Insert mocks into sys.modules so they are used when calendar_sync is imported
sys.modules['frappe'] = frappe
sys.modules['frappe.utils'] = frappe.utils
sys.modules['frappe.integrations'] = frappe.integrations
sys.modules['frappe.integrations.doctype'] = frappe.integrations.doctype
sys.modules['frappe.integrations.doctype.google_calendar'] = frappe.integrations.doctype.google_calendar
sys.modules['frappe.integrations.doctype.google_calendar.google_calendar'] = frappe.integrations.doctype.google_calendar.google_calendar
sys.modules['googleapiclient'] = googleapiclient
sys.modules['googleapiclient.errors'] = googleapiclient.errors

# --- Import the function to be tested ---
# This must be done AFTER the mocks are in place
from erpnext_enhancements.calendar_sync import get_google_calendars_for_doctype

# --- Test Class ---

class TestCalendarSync(unittest.TestCase):

    def setUp(self):
        # Reset mocks before each test to ensure test isolation
        frappe.reset_mock()
        # It's good practice to also reset the mocked attributes
        frappe.get_single.reset_mock()
        frappe.db.get_value.reset_mock()
        frappe.get_doc.reset_mock()


    def test_get_calendars_for_invalid_doctype(self):
        """
        Test that get_google_calendars_for_doctype returns an empty list
        for a doctype that is not configured for syncing.
        """
        result = get_google_calendars_for_doctype("Sales Order", "testuser@example.com")
        self.assertEqual(result, [])
        # Ensure no database calls were made for an invalid doctype
        frappe.get_single.assert_not_called()
        frappe.db.get_value.assert_not_called()

    def test_get_calendars_for_valid_doctype(self):
        """
        Test that get_google_calendars_for_doctype returns the correct
        calendars for a valid doctype.
        """
        # 1. Mock the settings from frappe.get_single
        mock_settings = MagicMock()
        mock_settings.google_calendar_sync_map = [
            MagicMock(reference_doctype="Task", google_calendar="Global Task Calendar"),
            MagicMock(reference_doctype="Project", google_calendar="Global Project Calendar")
        ]
        frappe.get_single.return_value = mock_settings

        # 2. Mock the user's personal calendar from frappe.db.get_value
        frappe.db.get_value.return_value = "User Calendar"

        # 3. Mock frappe.get_doc to return calendar objects
        def get_doc_side_effect(doctype, name):
            # Create a mock object and explicitly set its `name` attribute
            # to the string value, so that `c.name` returns a string.
            mock_doc = MagicMock()
            mock_doc.name = name
            if name == "Global Task Calendar":
                mock_doc.enable = 1
            elif name == "User Calendar":
                mock_doc.enable = 1
            else:
                mock_doc.enable = 0 # Ensure others are disabled
            return mock_doc
        frappe.get_doc.side_effect = get_doc_side_effect

        # Call the function for a valid doctype
        result = get_google_calendars_for_doctype("Task", "testuser@example.com")

        # --- Assertions ---
        # Check that the correct number of calendars is returned
        self.assertEqual(len(result), 2)

        # Check that the returned objects are the ones we mocked
        calendar_names = [c.name for c in result]
        self.assertIn("Global Task Calendar", calendar_names)
        self.assertIn("User Calendar", calendar_names)

        # Check that the mocked functions were called with the correct arguments
        frappe.get_single.assert_called_once_with("ERPNext Enhancements Settings")
        frappe.db.get_value.assert_called_once_with("Google Calendar", {"user": "testuser@example.com", "enable": 1}, "name")

        # Check that frappe.get_doc was called for the calendars
        # It should be called for "Global Task Calendar" and "User Calendar"
        self.assertIn(unittest.mock.call("Google Calendar", "Global Task Calendar"), frappe.get_doc.call_args_list)
        self.assertIn(unittest.mock.call("Google Calendar", "User Calendar"), frappe.get_doc.call_args_list)


if __name__ == '__main__':
    unittest.main()
