
import unittest
from unittest.mock import MagicMock, patch
import sys
from types import ModuleType

# Mock frappe module
frappe = MagicMock()
frappe.flags = MagicMock()
frappe.flags.sync_source = None
frappe.db.exists.return_value = True
sys.modules['frappe'] = frappe

# Mock requests module
sys.modules['requests'] = MagicMock()

# Import the module under test
# We need to ensure we can import it even if dependencies are missing in the environment
# The mocks above should handle 'frappe' and 'requests' imports in triton_bridge.py
from erpnext_enhancements.integrations import triton_bridge

class TestTritonBridge(unittest.TestCase):
    def setUp(self):
        # Reset mocks
        frappe.reset_mock()
        frappe.flags.sync_source = None

    def test_route_history_ignored_on_update(self):
        """Test that Route History documents are ignored during update sync."""
        doc = MagicMock()
        doc.doctype = "Route History"
        doc.name = "RH-12345"
        doc.issingle = 0

        triton_bridge.hook_on_update(doc)

        # Verify frappe.enqueue was NOT called
        frappe.enqueue.assert_not_called()

    def test_route_history_ignored_on_trash(self):
        """Test that Route History documents are ignored during trash sync."""
        doc = MagicMock()
        doc.doctype = "Route History"
        doc.name = "RH-12345"
        doc.issingle = 0

        triton_bridge.hook_on_trash(doc)

        # Verify frappe.enqueue was NOT called
        frappe.enqueue.assert_not_called()

    def test_other_doctype_synced(self):
        """Test that other documents ARE synced."""
        doc = MagicMock()
        doc.doctype = "Customer"
        doc.name = "CUST-001"
        doc.issingle = 0

        triton_bridge.hook_on_update(doc)

        # Verify frappe.enqueue WAS called
        frappe.enqueue.assert_called()

        args, kwargs = frappe.enqueue.call_args
        self.assertEqual(kwargs['doctype'], "Customer")
        self.assertEqual(kwargs['name'], "CUST-001")

if __name__ == '__main__':
    unittest.main()
