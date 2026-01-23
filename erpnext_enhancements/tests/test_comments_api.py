import unittest
import sys
from unittest.mock import MagicMock

# Pre-mock frappe module
mock_frappe = MagicMock()
sys.modules["frappe"] = mock_frappe

# Configure whitelist to return a passthrough decorator BEFORE import
mock_frappe.whitelist.return_value = lambda f: f

import frappe

# Now import the module under test
from erpnext_enhancements.api import comments

class TestCommentsAPI(unittest.TestCase):
	def setUp(self):
		global mock_frappe
		mock_frappe.reset_mock()
		mock_frappe.whitelist.return_value = lambda f: f
		mock_frappe._ = lambda x: x
		mock_frappe.session.user = "test_user"

		# Mock PermissionError
		class MockPermissionError(Exception):
			pass
		mock_frappe.PermissionError = MockPermissionError

		def mock_throw(msg, exc=MockPermissionError):
			raise exc(msg)
		mock_frappe.throw.side_effect = mock_throw

		mock_frappe.get_all.side_effect = None
		mock_frappe.get_doc.side_effect = None
		mock_frappe.new_doc.side_effect = None
		mock_frappe.get_doc.return_value = MagicMock()
		# Default has_permission to True
		mock_frappe.has_permission.return_value = True

	def test_get_comments_empty(self):
		self.assertEqual(comments.get_comments("Account", None), [])
		self.assertEqual(comments.get_comments(None, "Acc-001"), [])

	def test_get_comments_success(self):
		mock_comment = {"name": "c1", "content": "test", "owner": "user1", "creation": "2023-01-01"}

		def get_all_side_effect(doctype, filters=None, fields=None, order_by=None):
			if doctype == "Comment":
				# verify filters
				if filters.get("reference_doctype") == "Account" and filters.get("reference_name") == "Acc-001":
					return [mock_comment]
			if doctype == "User":
				return [{"name": "user1", "full_name": "Test User", "user_image": "avatar.png"}]
			return []
		mock_frappe.get_all.side_effect = get_all_side_effect

		result = comments.get_comments("Account", "Acc-001")
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]['full_name'], "Test User")

	def test_add_comment_success(self):
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "new_comment"
		mock_comment_doc.owner = "test_user"
		mock_comment_doc.content = "new account note"

		mock_frappe.new_doc.return_value = mock_comment_doc

		mock_user_doc = MagicMock()
		mock_user_doc.full_name = "Test User"
		mock_user_doc.user_image = "img.png"

		def get_doc_side_effect(doctype, name):
			if doctype == "User":
				return mock_user_doc
			return MagicMock()
		mock_frappe.get_doc.side_effect = get_doc_side_effect

		result = comments.add_comment("Account", "Acc-001", "new account note")

		self.assertEqual(result['full_name'], "Test User")
		mock_comment_doc.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(mock_comment_doc.reference_doctype, "Account")
		self.assertEqual(mock_comment_doc.reference_name, "Acc-001")

	def test_delete_comment_success(self):
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "note1"
		mock_comment_doc.owner = "test_user"

		def get_doc_side_effect(doctype, name):
			if doctype == "Comment" and name == "note1":
				return mock_comment_doc
			return MagicMock()
		mock_frappe.get_doc.side_effect = get_doc_side_effect

		result = comments.delete_comment("note1")
		self.assertEqual(result, {"success": True})
		mock_comment_doc.delete.assert_called_once_with(ignore_permissions=True)

	def test_update_comment_success(self):
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

		result = comments.update_comment("note1", "new content")
		self.assertEqual(mock_comment_doc.content, "new content")
		self.assertEqual(result['full_name'], "Updated User")

	def test_permission_denied(self):
		mock_frappe.has_permission.return_value = False
		with self.assertRaises(mock_frappe.PermissionError):
			comments.get_comments("Account", "Acc-001")

		with self.assertRaises(mock_frappe.PermissionError):
			comments.add_comment("Account", "Acc-001", "text")

if __name__ == "__main__":
	unittest.main()
