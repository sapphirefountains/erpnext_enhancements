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


class TestProjectEnhancements(unittest.TestCase):
    def setUp(self):
        # Reset all mocks before each test to ensure test isolation
        mock_frappe.reset_mock()
        # Re-apply the decorator mock since reset_mock clears all child mock configurations
        mock_frappe.whitelist.return_value = lambda f: f

    def test_get_project_comments_no_project_name(self):
        """Test that it returns an empty list if project_name is None or empty."""
        self.assertEqual(project_enhancements.get_project_comments(None), [])
        self.assertEqual(project_enhancements.get_project_comments(""), [])

    def test_get_project_comments_no_comments_found(self):
        """Test that it returns an empty list if no comments exist for the project."""
        mock_frappe.get_all.return_value = []  # Simulate no comments found
        result = project_enhancements.get_project_comments("test_project")
        self.assertEqual(result, [])
        mock_frappe.get_all.assert_called_once_with(
            "Comment",
            filters={"reference_doctype": "Project", "reference_name": "test_project"},
            fields=["name", "content", "owner", "creation"],
            order_by="creation desc",
        )

    def test_get_project_comments_with_data(self):
        """Test successful retrieval of comments and user data."""
        # Simulate finding one comment for owner 'user1'
        mock_frappe.get_all.side_effect = [
            [{"owner": "user1", "content": "a comment"}],
            [{"name": "user1", "full_name": "Test User", "user_image": "avatar.png"}],
        ]

        result = project_enhancements.get_project_comments("test_project")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["full_name"], "Test User")
        self.assertEqual(result[0]["user_image"], "avatar.png")
        self.assertEqual(result[0]["content"], "a comment")

    def test_add_project_comment_success(self):
        """Test successful creation of a new comment."""
        # Mock the `new_doc` call to return a mock document object
        mock_comment_doc = MagicMock()
        mock_comment_doc.name = "new_comment_id"  # Must have a .name attribute
        mock_frappe.new_doc.return_value = mock_comment_doc

        # Mock the `get_all` call to refetch the new comment
        mock_frappe.get_all.return_value = [
            {"name": "new_comment_id", "owner": "test_user"}
        ]

        # Mock the `get_doc` call to fetch the user's details
        mock_user_doc = MagicMock()
        mock_user_doc.full_name = "Test User"
        mock_user_doc.user_image = "avatar.png"
        mock_frappe.get_doc.return_value = mock_user_doc

        # Call the function
        result = project_enhancements.add_project_comment(
            "test_project", "a new comment"
        )

        # Assertions
        self.assertEqual(result["full_name"], "Test User")
        self.assertEqual(result["user_image"], "avatar.png")

        # Verify that the new document was created correctly
        mock_frappe.new_doc.assert_called_once_with("Comment")
        self.assertEqual(mock_comment_doc.reference_doctype, "Project")
        self.assertEqual(mock_comment_doc.reference_name, "test_project")
        self.assertEqual(mock_comment_doc.content, "a new comment")
        mock_comment_doc.insert.assert_called_once_with(ignore_permissions=True)


if __name__ == "__main__":
    unittest.main()
