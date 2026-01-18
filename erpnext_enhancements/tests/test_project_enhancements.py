import unittest
from unittest.mock import MagicMock, patch
from erpnext_enhancements import project_enhancements

class TestProjectEnhancements(unittest.TestCase):
	def setUp(self):
		# Create a patcher for the frappe module used in project_enhancements
		self.frappe_patcher = patch('erpnext_enhancements.project_enhancements.frappe')
		self.mock_frappe = self.frappe_patcher.start()

		# Setup default mock behaviors
		self.mock_frappe.whitelist.return_value = lambda f: f
		self.mock_frappe._ = lambda x: x
		self.mock_frappe.session.user = "test_user"

		# Mock PermissionError
		class MockPermissionError(Exception):
			pass
		self.mock_frappe.PermissionError = MockPermissionError

		def mock_throw(msg, exc=MockPermissionError):
			raise exc(msg)
		self.mock_frappe.throw.side_effect = mock_throw

	def tearDown(self):
		self.frappe_patcher.stop()

	def test_get_project_comments_no_project_name(self):
		self.assertEqual(project_enhancements.get_project_comments(None), [])
		self.assertEqual(project_enhancements.get_project_comments(""), [])

	def test_get_project_comments_no_comments_found(self):
		mock_project = MagicMock()
		# Ensure get returns an empty list for any argument
		mock_project.get.side_effect = lambda key: []
		self.mock_frappe.get_doc.return_value = mock_project

		result = project_enhancements.get_project_comments("test_project")

		self.assertEqual(result, [])
		self.mock_frappe.get_doc.assert_called_once_with("Project", "test_project")

	def test_get_project_comments_with_data(self):
		# Setup mocks for this test specifically
		mock_note = MagicMock()
		mock_note.owner = "user1"
		mock_note.get.side_effect = lambda key: {"owner": "user1"}.get(key)

		mock_project = MagicMock()
		def project_get_side_effect(key):
			if key == "custom_project_notes":
				return [mock_note]
			return [] # Important: return empty list for other keys
		mock_project.get.side_effect = project_get_side_effect

		def get_doc_side_effect(doctype, name):
			if doctype == "Project":
				return mock_project
			return MagicMock()
		self.mock_frappe.get_doc.side_effect = get_doc_side_effect

		self.mock_frappe.get_all.return_value = [
			{"name": "user1", "full_name": "Test User", "user_image": "avatar.png"}
		]

		# Call function
		result = project_enhancements.get_project_comments("test_project")

		# Assertions
		self.assertEqual(len(result), 1)
		self.assertEqual(result[0].full_name, "Test User")

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
		self.mock_frappe.get_doc.side_effect = get_doc_side_effect

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
		self.mock_frappe.get_doc.side_effect = get_doc_side_effect

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
		self.mock_frappe.get_doc.side_effect = get_doc_side_effect

		result = project_enhancements.update_project_comment("test_project", "note1", "new content")

		self.assertEqual(mock_note.content, "new content")
		self.assertEqual(result.full_name, "Updated User")
		mock_project.save.assert_called_once_with(ignore_permissions=True)

if __name__ == "__main__":
	unittest.main()
