import sys
from unittest.mock import MagicMock

# MOCK FRAPPE BEFORE IMPORTING MODULE
mock_frappe = MagicMock()
# Configure whitelist decorator to pass through the function
def passthrough(func=None):
    if func:
        return func
    return lambda f: f

mock_frappe.whitelist.side_effect = passthrough

sys.modules["frappe"] = mock_frappe
sys.modules["frappe.utils"] = MagicMock()
sys.modules["frappe.model.document"] = MagicMock()

import unittest
from unittest.mock import patch

# Now import the module under test
from erpnext_enhancements.api.time_kiosk import log_geolocation

class TestGeoTelemetry(unittest.TestCase):

    def setUp(self):
        # Reset mock
        mock_frappe.reset_mock()
        # Explicitly clear side_effects of critical methods
        mock_frappe.get_doc.side_effect = None
        mock_frappe.get_doc.return_value = MagicMock()
        mock_frappe.throw.side_effect = None

        mock_frappe.session = MagicMock()
        mock_frappe.session.user = "administrator"
        mock_frappe.whitelist.side_effect = passthrough

    def test_log_geolocation_success(self):
        # Setup mock return for get_doc
        mock_doc = MagicMock()
        mock_frappe.get_doc.return_value = mock_doc
        # Ensure side_effect is None
        mock_frappe.get_doc.side_effect = None

        # Execute
        result = log_geolocation(
            employee="EMP-001",
            latitude=37.7749,
            longitude=-122.4194,
            device_agent="TestAgent/1.0",
            log_status="Success",
            timestamp="2023-10-27 10:00:00"
        )

        # DEBUG: Print result if error
        if result.get('status') == 'error':
            print(f"DEBUG ERROR: {result.get('message')}")

        # Verify
        self.assertEqual(result['status'], 'success')

        # Check get_doc arguments
        mock_frappe.get_doc.assert_called_once()
        args = mock_frappe.get_doc.call_args[0][0]
        self.assertEqual(args['doctype'], 'Time Kiosk Log')
        self.assertEqual(args['employee'], 'EMP-001')
        self.assertEqual(args['latitude'], 37.7749)
        self.assertEqual(args['log_status'], 'Success')

        # Check insert called
        mock_doc.insert.assert_called_once_with(ignore_permissions=True)

    def test_log_geolocation_missing_employee(self):
        # Mock throw to raise exception (simulate Frappe behavior)
        mock_frappe.throw.side_effect = Exception("Employee ID is required")

        # Execute
        result = log_geolocation(
            employee=None,
            latitude=0,
            longitude=0,
            device_agent="Test",
            log_status="Error",
            timestamp="2023-10-27 10:00:00"
        )

        # Verify
        self.assertEqual(result['status'], 'error')
        self.assertIn("Employee ID is required", result['message'])

    def test_log_geolocation_db_error(self):
        # Mock get_doc to raise DB error
        mock_frappe.get_doc.side_effect = Exception("DB Error")

        result = log_geolocation(
            employee="EMP-001",
            latitude=0,
            longitude=0,
            device_agent="Test",
            log_status="Success",
            timestamp="2023-10-27 10:00:00"
        )

        self.assertEqual(result['status'], 'error')
        self.assertIn("DB Error", result['message'])
        mock_frappe.log_error.assert_called()

if __name__ == '__main__':
    unittest.main()
