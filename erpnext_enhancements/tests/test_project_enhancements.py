import unittest
from unittest.mock import MagicMock, patch
import sys


# Mock Frappe and related modules before import to prevent import errors
mock_frappe = MagicMock()
# This is the key to solving the decorator issue:
# We make the `whitelist` decorator return the function that it's decorating.
mock_frappe.whitelist.return_value = lambda f: f
# Also mock the translation function `_`
mock_frappe._ = lambda x: x

sys.modules["frappe"] = mock_frappe
sys.modules["frappe.utils"] = MagicMock()
sys.modules["frappe.model"] = MagicMock()


# Now that mocks are in place, we can import the module to be tested
from erpnext_enhancements import project_enhancements


class MockFrappeException(Exception):
    def __init__(self, msg=""):
        super().__init__(msg)

class MockPermissionError(MockFrappeException):
    pass

class TestProjectEnhancements(unittest.TestCase):
    def setUp(self):
        mock_frappe.reset_mock(return_value=True, side_effect=True)

        # Explicitly reset mocks on attributes used across different tests to ensure isolation
        for attr in ["get_all", "get_doc", "throw"]:
             # These are MagicMocks, they get created on first access.
             # On subsequent runs, we need to reset them.
             if hasattr(mock_frappe, attr):
                getattr(mock_frappe, attr).reset_mock(return_value=True, side_effect=True)

        mock_frappe.whitelist.return_value = lambda f: f
        mock_frappe.session.user = "test_user"
        mock_frappe.PermissionError = MockPermissionError

        def mock_throw(msg, exc=MockFrappeException):
            raise exc(msg)
        mock_frappe.throw.side_effect = mock_throw

    def test_get_project_comments_no_project_name(self):
        self.assertEqual(project_enhancements.get_project_comments(None), [])
        self.assertEqual(project_enhancements.get_project_comments(""), [])

    def test_get_project_comments_no_comments_found(self):
        # If frappe.get_all returns an empty list for notes, the function should return []
        mock_frappe.get_all.return_value = []
        result = project_enhancements.get_project_comments("test_project")
        self.assertEqual(result, [])
        # Check that get_all was called correctly for the 'Project Note' doctype
        mock_frappe.get_all.assert_called_once()
        self.assertEqual(mock_frappe.get_all.call_args[0][0], "Project Note")

    def test_get_project_comments_with_data(self):
        # Mock the return values for the two separate calls to frappe.get_all
        mock_notes_data = [
            {"name": "note1", "owner": "user1", "content": "Test content", "creation": "2024-01-01 10:00:00"}
        ]
        mock_users_data = [
            {"name": "user1", "full_name": "Test User", "user_image": "avatar.png"}
        ]

        # side_effect as a list will return one item per call
        mock_frappe.get_all.side_effect = [mock_notes_data, mock_users_data]

        # Call function
        result = project_enhancements.get_project_comments("test_project")

        # Assertions
        self.assertEqual(len(result), 1)
        # The result is now a dict, so we access items by key
        self.assertEqual(result[0]['full_name'], "Test User")
        self.assertEqual(result[0]['content'], "Test content")
        self.assertEqual(mock_frappe.get_all.call_count, 2)

    def test_add_project_comment_success(self):
        mock_project = MagicMock()
        mock_note = MagicMock()
        mock_note.owner = "test_user"
        mock_project.append.return_value = mock_note

        mock_user_doc = MagicMock()
        mock_user_doc.full_name = "Test User"

        def get_doc_side_effect(doctype, name):
            if doctype == "Project":
                return mock_project
            elif doctype == "User":
                return mock_user_doc
            return MagicMock()
        mock_frappe.get_doc.side_effect = get_doc_side_effect

        result = project_enhancements.add_project_comment("test_project", "a new comment")

        self.assertEqual(result.full_name, "Test User")
        mock_project.save.assert_called_once_with(ignore_permissions=True)

    def test_delete_project_comment_success(self):
        mock_note = MagicMock()
        mock_note.name = "note1"
        mock_note.owner = "test_user"

        mock_project = MagicMock()
        def project_get_side_effect(key):
            if key == "custom_project_notes":
                return [mock_note]
            return []
        mock_project.get.side_effect = project_get_side_effect

        def get_doc_side_effect(doctype, name):
            if doctype == "Project":
                return mock_project
            return MagicMock()
        mock_frappe.get_doc.side_effect = get_doc_side_effect

        result = project_enhancements.delete_project_comment("test_project", "note1")

        self.assertEqual(result, {"success": True})
        mock_project.remove.assert_called_once_with(mock_note)
        mock_project.save.assert_called_once_with(ignore_permissions=True)

    def test_update_project_comment_success(self):
        mock_note = MagicMock()
        mock_note.name = "note1"
        mock_note.owner = "test_user"

        mock_project = MagicMock()
        def project_get_side_effect(key):
            if key == "custom_project_notes":
                return [mock_note]
            return []
        mock_project.get.side_effect = project_get_side_effect

        mock_user_doc = MagicMock()
        mock_user_doc.full_name = "Updated User"

        def get_doc_side_effect(doctype, name):
            if doctype == "Project":
                return mock_project
            elif doctype == "User":
                return mock_user_doc
            return MagicMock()
        mock_frappe.get_doc.side_effect = get_doc_side_effect

        result = project_enhancements.update_project_comment("test_project", "note1", "new content")

        self.assertEqual(mock_note.content, "new content")
        self.assertEqual(result.full_name, "Updated User")
        mock_project.save.assert_called_once_with(ignore_permissions=True)

if __name__ == "__main__":
    unittest.main()
