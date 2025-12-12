import unittest
from unittest.mock import MagicMock, patch
import sys
from datetime import datetime

# Mock Frappe and other dependencies before importing the app code
# This creates a global mock that will be imported by the 'todo' module
mock_frappe = MagicMock()
sys.modules["frappe"] = mock_frappe
sys.modules["frappe.utils"] = MagicMock()

from erpnext_enhancements.todo import validate_todo_dates, get_todo_events

class TestToDo(unittest.TestCase):
    def setUp(self):
        # Reset the global mock before each test to ensure isolation
        mock_frappe.reset_mock()

    @patch("erpnext_enhancements.todo.get_datetime")
    def test_validate_todo_dates_valid(self, mock_get_datetime):
        """Test that validation passes for valid dates."""
        doc = MagicMock()
        doc.custom_calendar_datetime_start = "2024-01-01 10:00:00"
        doc.custom_calendar_datetime_end = "2024-01-01 12:00:00"

        mock_get_datetime.side_effect = [
            datetime(2024, 1, 1, 10, 0, 0),
            datetime(2024, 1, 1, 12, 0, 0),
        ]

        try:
            validate_todo_dates(doc, None)
        except Exception as e:
            self.fail(f"validate_todo_dates() raised Exception unexpectedly: {e}")

    @patch("erpnext_enhancements.todo.frappe.throw")
    @patch("erpnext_enhancements.todo.get_datetime")
    def test_validate_todo_dates_invalid(self, mock_get_datetime, mock_frappe_throw):
        """Test that validation fails for invalid dates."""
        doc = MagicMock()
        doc.custom_calendar_datetime_start = "2024-01-01 12:00:00"
        doc.custom_calendar_datetime_end = "2024-01-01 10:00:00"

        mock_get_datetime.side_effect = [
            datetime(2024, 1, 1, 12, 0, 0),
            datetime(2024, 1, 1, 10, 0, 0),
        ]

        validate_todo_dates(doc, None)
        mock_frappe_throw.assert_called_once_with("End date and time cannot be before start date and time")

    def test_get_todo_events(self):
        """Test fetching of calendar events."""
        # Configure the global mock directly
        mock_frappe.session.user = "test@example.com"
        mock_frappe.get_all.return_value = [
            {
                "name": "test-todo-1",
                "description": "Test ToDo 1",
                "custom_calendar_datetime_start": "2024-01-01 10:00:00",
                "custom_calendar_datetime_end": "2024-01-01 12:00:00",
            }
        ]

        events = get_todo_events("2024-01-01", "2024-01-31")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "test-todo-1")

        # Verify that the method on the global mock was called
        mock_frappe.get_all.assert_called_once_with(
            "ToDo",
            filters=[
                {"owner": "test@example.com"},
                {"custom_calendar_datetime_start": ["<=", "2024-01-31"]},
                {"custom_calendar_datetime_end": [">=", "2024-01-01"]}
            ],
            fields=["name", "description", "custom_calendar_datetime_start", "custom_calendar_datetime_end"],
            as_dict=True
        )
