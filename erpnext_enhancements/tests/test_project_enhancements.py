import unittest
import sys
from unittest.mock import MagicMock

# Pre-mock frappe module
mock_frappe = MagicMock()
sys.modules["frappe"] = mock_frappe

# Configure whitelist to return a passthrough decorator BEFORE import
# @frappe.whitelist() calls whitelist() which returns a decorator.
# That decorator is called with the function.
mock_frappe.whitelist.return_value = lambda f: f

import frappe

# Now import the module under test
from erpnext_enhancements import project_enhancements

class TestProjectEnhancements(unittest.TestCase):
	def setUp(self):
		# Setup default mock behaviors on the global mock
		# We must use the SAME mock object that was used during import
		global mock_frappe

		# Reset any side effects or return values from previous tests
		mock_frappe.reset_mock()

		# Restore whitelist mock (reset_mock might clear it?)
		mock_frappe.whitelist.return_value = lambda f: f

		mock_frappe._ = lambda x: x
		mock_frappe.session.user = "test_user"
		mock_frappe.utils.now_datetime.return_value = "2023-01-01 00:00:00"

		# Mock PermissionError
		class MockPermissionError(Exception):
			pass
		mock_frappe.PermissionError = MockPermissionError

		def mock_throw(msg, exc=MockPermissionError):
			raise exc(msg)
		mock_frappe.throw.side_effect = mock_throw

        # Reset side effects for get_all/get_doc before each test
		mock_frappe.get_all.side_effect = None
		mock_frappe.get_doc.side_effect = None
		mock_frappe.new_doc.side_effect = None
		mock_frappe.get_doc.return_value = MagicMock()

	def test_get_project_comments_no_project_name(self):
		self.assertEqual(project_enhancements.get_project_comments(None), [])

	def test_get_project_comments_with_data(self):
		# Setup mocks for this test specifically
		mock_comment = {"name": "c1", "content": "test", "owner": "user1", "creation": "2023-01-01"}

		# Mock frappe.get_all for Comments
		def get_all_side_effect(doctype, filters=None, fields=None, order_by=None):
			if doctype == "Comment":
				return [mock_comment]
			if doctype == "User":
				return [{"name": "user1", "full_name": "Test User", "user_image": "avatar.png"}]
			return []
		mock_frappe.get_all.side_effect = get_all_side_effect

		# Call function
		result = project_enhancements.get_project_comments("test_project")

		# Assertions
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]['full_name'], "Test User")

	def test_add_project_comment_success(self):
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "new_comment"
		mock_comment_doc.owner = "test_user"
		mock_comment_doc.content = "a new comment"

		mock_frappe.new_doc.return_value = mock_comment_doc

		# Reset get_doc side effect if set previously
		mock_frappe.get_doc.side_effect = None

		mock_user_doc = MagicMock()
		mock_user_doc.full_name = "Test User"
		mock_user_doc.user_image = "img.png"

		def get_doc_side_effect(doctype, name):
			if doctype == "User":
				return mock_user_doc
			return MagicMock()
		mock_frappe.get_doc.side_effect = get_doc_side_effect

		result = project_enhancements.add_project_comment("test_project", "a new comment")

		self.assertEqual(result['full_name'], "Test User")
		mock_comment_doc.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(mock_comment_doc.reference_doctype, "Project")
		self.assertEqual(mock_comment_doc.reference_name, "test_project")

	def test_delete_project_comment_success(self):
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "note1"
		mock_comment_doc.owner = "test_user"

		def get_doc_side_effect(doctype, name):
			if doctype == "Comment" and name == "note1":
				return mock_comment_doc
			return MagicMock()
		mock_frappe.get_doc.side_effect = get_doc_side_effect

		result = project_enhancements.delete_project_comment("test_project", "note1")

		self.assertEqual(result, {"success": True})
		mock_comment_doc.delete.assert_called_once_with(ignore_permissions=True)

	def test_update_project_comment_success(self):
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "note1"
		mock_comment_doc.owner = "test_user"
		mock_comment_doc.content = "old content"

		def get_doc_side_effect(doctype, name):
			if doctype == "Comment" and name == "note1":
				return mock_comment_doc
			if doctype == "User":
				user = MagicMock()
				user.full_name = "Updated User"
				return user
			return MagicMock()
		mock_frappe.get_doc.side_effect = get_doc_side_effect

		result = project_enhancements.update_project_comment("test_project", "note1", "new content")

		self.assertEqual(mock_comment_doc.content, "new content")
		self.assertEqual(result['full_name'], "Updated User")
		mock_comment_doc.save.assert_called_once_with(ignore_permissions=True)

if __name__ == "__main__":
	unittest.main()
