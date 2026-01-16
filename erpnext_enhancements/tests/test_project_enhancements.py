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
        # Mock frappe.get_all to return empty list
        mock_frappe.get_all.return_value = []
        result = project_enhancements.get_project_comments("test_project")
        self.assertEqual(result, [])
        # Verify get_all called with correct filters
        args, kwargs = mock_frappe.get_all.call_args
        self.assertEqual(args[0], "Comment")
        self.assertEqual(kwargs['filters']['reference_name'], "test_project")

    def test_get_project_comments_with_data(self):
        # Mock comments
        mock_comment = MagicMock()
        mock_comment.owner = "user1"
        mock_comment.content = "Test Content"
        mock_comment.creation = "2023-01-01"

        # We need get_all to return distinct values for different calls
        # 1. Fetch comments
        # 2. Fetch users
        def get_all_side_effect(doctype, **kwargs):
            if doctype == "Comment":
                return [mock_comment]
            if doctype == "User":
                user_mock = MagicMock()
                user_mock.name = "user1"
                user_mock.full_name = "Test User"
                user_mock.user_image = "avatar.png"
                return [user_mock]
            return []

        mock_frappe.get_all.side_effect = get_all_side_effect

        # Call function
        result = project_enhancements.get_project_comments("test_project")

        # Assertions
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].full_name, "Test User")
        self.assertEqual(result[0].user_image, "avatar.png")

    def test_add_project_comment_success(self):
        mock_project = MagicMock()
        mock_comment = MagicMock()
        mock_comment.name = "new-comment-id"
        mock_comment.content = "a new comment"
        mock_comment.owner = "test_user"
        mock_comment.creation = "2023-01-01"

        mock_project.add_comment.return_value = mock_comment

        mock_user_doc = MagicMock()
        mock_user_doc.full_name = "Test User"
        mock_user_doc.user_image = "avatar.png"

        def get_doc_side_effect(doctype, name):
            if doctype == "Project":
                return mock_project
            elif doctype == "User":
                return mock_user_doc
            return MagicMock()
        mock_frappe.get_doc.side_effect = get_doc_side_effect

        result = project_enhancements.add_project_comment("test_project", "a new comment")

        self.assertEqual(result['full_name'], "Test User")
        self.assertEqual(result['content'], "a new comment")
        mock_project.add_comment.assert_called_once_with("Comment", "a new comment")

if __name__ == "__main__":
    unittest.main()
