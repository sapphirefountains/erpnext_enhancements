import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add current directory to path so we can import the package
sys.path.append(os.getcwd())

# Mock frappe and its submodules before importing the module under test
frappe_mock = MagicMock()
# Make whitelist a passthrough decorator
frappe_mock.whitelist = lambda: lambda f: f

sys.modules['frappe'] = frappe_mock
sys.modules['frappe.utils'] = MagicMock()

# Now import the module to test
from erpnext_enhancements.api.time_kiosk import get_current_status

class TestTimeKioskStatus(unittest.TestCase):
    def setUp(self):
        # Reset mocks before each test
        frappe_mock.reset_mock()
        # Restore whitelist behavior just in case (though it's set on module level)
        frappe_mock.whitelist = lambda: lambda f: f
        frappe_mock.session.user = "test@example.com"

    def test_get_current_status_idle(self):
        """
        Test that get_current_status returns a dict with just 'employee'
        when the employee exists but has no open job interval.
        This confirms the response is 'truthy' which confuses the frontend.
        """
        # Setup: Employee exists
        frappe_mock.db.get_value.side_effect = self.mock_get_value_idle

        # Execute
        result = get_current_status()

        # Verify
        self.assertIsNotNone(result)
        self.assertIn("employee", result)
        self.assertEqual(result["employee"], "EMP-001")
        # Ensure no interval data is present
        self.assertNotIn("name", result)
        self.assertNotIn("project", result)

        # This confirms that 'if (result)' in JS would be true, leading to the bug
        self.assertTrue(bool(result))

    def mock_get_value_idle(self, doctype, filters, fieldname=None, as_dict=False):
        if doctype == "Employee":
            return "EMP-001"
        if doctype == "Job Interval":
            # Simulate no open interval found
            return None
        return None

if __name__ == '__main__':
    unittest.main()
