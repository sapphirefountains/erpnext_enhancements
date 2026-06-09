"""Unit tests for the generic ``api.comments`` CRUD endpoints.

Pure unit tests: ``frappe.get_all`` / ``get_doc`` / ``new_doc`` / ``has_permission``
are patched with ``unittest.mock`` (no database), so they exercise the API's
permission gating, filter construction and author-enrichment logic in isolation.
"""
import unittest
from unittest.mock import MagicMock, patch
import frappe
from erpnext_enhancements.api import comments

class TestCommentsAPI(unittest.TestCase):
	def setUp(self):
		pass

	@patch('frappe.get_all')
	@patch('frappe.has_permission', return_value=True)
	def test_get_comments_empty(self, mock_has_perm, mock_get_all):
		"""get_comments returns [] when either reference doctype or name is missing."""
		self.assertEqual(comments.get_comments("Account", None), [])
		self.assertEqual(comments.get_comments(None, "Acc-001"), [])

	@patch('frappe.get_all')
	@patch('frappe.has_permission', return_value=True)
	def test_get_comments_success(self, mock_has_perm, mock_get_all):
		"""get_comments filters by reference and enriches each comment with author full_name."""
		mock_comment = {"name": "c1", "content": "test", "owner": "user1", "creation": "2023-01-01"}

		def get_all_side_effect(doctype, filters=None, fields=None, order_by=None):
			if doctype == "Comment":
				# verify filters
				if filters.get("reference_doctype") == "Account" and filters.get("reference_name") == "Acc-001":
					return [mock_comment]
			if doctype == "User":
				return [{"name": "user1", "full_name": "Test User", "user_image": "avatar.png"}]
			return []
		mock_get_all.side_effect = get_all_side_effect

		result = comments.get_comments("Account", "Acc-001")
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]['full_name'], "Test User")

	@patch('frappe.get_doc')
	@patch('frappe.new_doc')
	@patch('frappe.has_permission', return_value=True)
	def test_add_comment_success(self, mock_has_perm, mock_new_doc, mock_get_doc):
		"""add_comment inserts a Comment bound to the reference and returns the author."""
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "new_comment"
		mock_comment_doc.owner = "test_user"
		mock_comment_doc.content = "new account note"

		mock_new_doc.return_value = mock_comment_doc

		mock_user_doc = MagicMock()
		mock_user_doc.full_name = "Test User"
		mock_user_doc.user_image = "img.png"

		def get_doc_side_effect(doctype, name):
			if doctype == "User":
				return mock_user_doc
			return MagicMock()
		mock_get_doc.side_effect = get_doc_side_effect

		result = comments.add_comment("Account", "Acc-001", "new account note")

		self.assertEqual(result['full_name'], "Test User")
		mock_comment_doc.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(mock_comment_doc.reference_doctype, "Account")
		self.assertEqual(mock_comment_doc.reference_name, "Acc-001")

	@patch('frappe.get_doc')
	@patch('frappe.session')
	def test_delete_comment_success(self, mock_session, mock_get_doc):
		"""delete_comment deletes the Comment when the session user is its owner."""
		mock_session.user = 'test_user'
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "note1"
		mock_comment_doc.owner = "test_user"

		def get_doc_side_effect(doctype, name):
			if doctype == "Comment" and name == "note1":
				return mock_comment_doc
			return MagicMock()
		mock_get_doc.side_effect = get_doc_side_effect

		result = comments.delete_comment("note1")
		self.assertEqual(result, {"success": True})
		mock_comment_doc.delete.assert_called_once_with(ignore_permissions=True)

	@patch('frappe.get_doc')
	@patch('frappe.session')
	def test_update_comment_success(self, mock_session, mock_get_doc):
		"""update_comment overwrites content for the owner and returns refreshed author info."""
		mock_session.user = 'test_user'
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
		mock_get_doc.side_effect = get_doc_side_effect

		result = comments.update_comment("note1", "new content")
		self.assertEqual(mock_comment_doc.content, "new content")
		self.assertEqual(result['full_name'], "Updated User")

	@patch('frappe.has_permission', return_value=False)
	def test_permission_denied(self, mock_has_perm):
		"""get_comments and add_comment raise ValidationError when permission is denied."""
		# get_comments calls frappe.throw if has_permission is false
		# We expect frappe.ValidationError (default for throw) or whatever throw raises

		# Since we are using real frappe.throw, it raises frappe.ValidationError
		# Note: frappe.throw raises frappe.exceptions.ValidationError by default.

		with self.assertRaises(frappe.ValidationError):
			comments.get_comments("Account", "Acc-001")

		with self.assertRaises(frappe.ValidationError):
			comments.add_comment("Account", "Acc-001", "text")

if __name__ == "__main__":
	unittest.main()
