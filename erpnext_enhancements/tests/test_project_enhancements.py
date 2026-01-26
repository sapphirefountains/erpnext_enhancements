import unittest
from unittest.mock import MagicMock, patch
import frappe
from erpnext_enhancements import project_enhancements

class TestProjectEnhancements(unittest.TestCase):
	def setUp(self):
		pass

	@patch('frappe.get_all')
	def test_get_project_comments_no_project_name(self, mock_get_all):
		self.assertEqual(project_enhancements.get_project_comments(None), [])

	@patch('frappe.get_all')
	def test_get_project_comments_with_data(self, mock_get_all):
		# Setup mocks
		mock_comment = {"name": "c1", "content": "test", "owner": "user1", "creation": "2023-01-01"}

		def get_all_side_effect(doctype, filters=None, fields=None, order_by=None):
			if doctype == "Comment":
				return [mock_comment]
			if doctype == "User":
				return [{"name": "user1", "full_name": "Test User", "user_image": "avatar.png"}]
			return []
		mock_get_all.side_effect = get_all_side_effect

		# Call function
		result = project_enhancements.get_project_comments("test_project")

		# Assertions
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0]['full_name'], "Test User")

	@patch('frappe.get_doc')
	@patch('frappe.new_doc')
	def test_add_project_comment_success(self, mock_new_doc, mock_get_doc):
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "new_comment"
		mock_comment_doc.owner = "test_user"
		mock_comment_doc.content = "a new comment"

		mock_new_doc.return_value = mock_comment_doc

		mock_user_doc = MagicMock()
		mock_user_doc.full_name = "Test User"
		mock_user_doc.user_image = "img.png"

		def get_doc_side_effect(doctype, name):
			if doctype == "User":
				return mock_user_doc
			return MagicMock()
		mock_get_doc.side_effect = get_doc_side_effect

		result = project_enhancements.add_project_comment("test_project", "a new comment")

		self.assertEqual(result['full_name'], "Test User")
		mock_comment_doc.insert.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(mock_comment_doc.reference_doctype, "Project")
		self.assertEqual(mock_comment_doc.reference_name, "test_project")

	@patch('frappe.get_doc')
	@patch.object(frappe.session, 'user', 'test_user')
	def test_delete_project_comment_success(self, mock_get_doc):
		mock_comment_doc = MagicMock()
		mock_comment_doc.name = "note1"
		mock_comment_doc.owner = "test_user"

		def get_doc_side_effect(doctype, name):
			if doctype == "Comment" and name == "note1":
				return mock_comment_doc
			return MagicMock()
		mock_get_doc.side_effect = get_doc_side_effect

		result = project_enhancements.delete_project_comment("test_project", "note1")

		self.assertEqual(result, {"success": True})
		mock_comment_doc.delete.assert_called_once_with(ignore_permissions=True)

	@patch('frappe.get_doc')
	@patch.object(frappe.session, 'user', 'test_user')
	def test_update_project_comment_success(self, mock_get_doc):
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

		result = project_enhancements.update_project_comment("test_project", "note1", "new content")

		self.assertEqual(mock_comment_doc.content, "new content")
		self.assertEqual(result['full_name'], "Updated User")
		mock_comment_doc.save.assert_called_once_with(ignore_permissions=True)

if __name__ == "__main__":
	unittest.main()
