import frappe
from frappe.tests.utils import FrappeTestCase
from unittest.mock import patch, MagicMock
from erpnext_enhancements.api.search import search_global_docs

class TestSearchAPI(FrappeTestCase):
    @patch('frappe.db.sql')
    @patch('frappe.has_permission')
    @patch('frappe.get_meta')
    @patch('frappe.get_all')
    @patch('frappe.db.exists')
    def test_search_global_docs_permissions(self, mock_exists, mock_get_all, mock_get_meta, mock_has_permission, mock_sql):
        mock_sql.return_value = [
            MagicMock(doctype="User", name="test1@example.com", title="Test 1", route=None),
            MagicMock(doctype="User", name="test2@example.com", title="Test 2", route=None),
            MagicMock(doctype="Role", name="System Manager", title="System Manager", route=None),
            MagicMock(doctype="Role", name="Guest", title="Guest", route=None)
        ]

        mock_exists.return_value = True

        def has_permission_side_effect(doctype, ptype="read", doc=None):
            if doctype == "User":
                return True
            if doctype == "Role":
                return False
            return False

        mock_has_permission.side_effect = has_permission_side_effect

        mock_meta = MagicMock()
        mock_meta.issingle = False
        mock_get_meta.return_value = mock_meta

        def get_all_side_effect(doctype, filters=None, pluck=None, ignore_permissions=False):
            if doctype == "User":
                # Only test1 has permission dynamically
                return ["test1@example.com"]
            return []

        mock_get_all.side_effect = get_all_side_effect

        results = search_global_docs("test")

        # Since Role is blocked by has_permission, no Role docs are returned
        # Since User is allowed by has_permission, but only test1@example.com is in get_all, only test1 is returned

        self.assertEqual(len(results), 1)
        self.assertIn("test1@example.com", results[0]['value'])
