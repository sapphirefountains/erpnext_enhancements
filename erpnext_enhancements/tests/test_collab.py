"""Tests for the live collaborative form sync relay (``api.collab``).

Verifies that ``broadcast_field_update`` enforces the doctype allowlist,
write permission, field validity (existence + value-holding fieldtype),
value size cap, and child-table validity — and that a valid call publishes
exactly one ``collab_field_update`` event to the document's realtime room.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.api.collab import MAX_VALUE_LENGTH, broadcast_field_update

NO_ROLE_USER = "collab_test_no_role@example.com"


class TestCollabRelay(FrappeTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.task = frappe.get_doc({"doctype": "Task", "subject": "Collab Relay Test Task"}).insert(
			ignore_permissions=True
		)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.delete_doc("Task", self.task.name, force=True)
		super().tearDown()

	def _valid_args(self, **overrides):
		args = {
			"doctype": "Task",
			"docname": self.task.name,
			"fieldname": "subject",
			"value": "Updated subject",
			"origin": "Administrator:testclient",
		}
		args.update(overrides)
		return args

	def test_rejects_disallowed_doctype(self):
		"""Doctypes outside COLLAB_DOCTYPES are rejected."""
		with self.assertRaises(frappe.ValidationError):
			broadcast_field_update(**self._valid_args(doctype="Project", docname="X"))

	def test_rejects_without_write_permission(self):
		"""A user without write permission on the Task gets PermissionError."""
		if not frappe.db.exists("User", NO_ROLE_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": NO_ROLE_USER,
					"first_name": "Collab",
					"last_name": "NoRole",
				}
			).insert(ignore_permissions=True)
		frappe.set_user(NO_ROLE_USER)
		with self.assertRaises(frappe.PermissionError):
			broadcast_field_update(**self._valid_args())

	def test_rejects_unknown_field(self):
		"""Fieldnames not present on the target meta are rejected."""
		with self.assertRaises(frappe.ValidationError):
			broadcast_field_update(**self._valid_args(fieldname="not_a_real_field"))

	def test_rejects_display_fieldtype(self):
		"""Fields that hold no value (e.g. the depends_on Table) are rejected."""
		with self.assertRaises(frappe.ValidationError):
			broadcast_field_update(**self._valid_args(fieldname="depends_on"))

	def test_rejects_oversized_value(self):
		"""Values above MAX_VALUE_LENGTH are rejected."""
		with self.assertRaises(frappe.ValidationError):
			broadcast_field_update(**self._valid_args(value="x" * (MAX_VALUE_LENGTH + 1)))

	def test_rejects_invalid_child_doctype(self):
		"""child_doctype must be one of the parent's table-field options."""
		with self.assertRaises(frappe.ValidationError):
			broadcast_field_update(**self._valid_args(child_doctype="Sales Order Item", child_name="row1"))

	def test_publishes_to_doc_room(self):
		"""A valid call publishes one collab_field_update to the doc room."""
		with patch("erpnext_enhancements.api.collab.frappe.publish_realtime") as pub:
			broadcast_field_update(**self._valid_args())

		pub.assert_called_once()
		args, kwargs = pub.call_args
		self.assertEqual(args[0], "collab_field_update")
		self.assertEqual(kwargs.get("doctype"), "Task")
		self.assertEqual(kwargs.get("docname"), self.task.name)
		payload = args[1]
		self.assertEqual(payload["fieldname"], "subject")
		self.assertEqual(payload["value"], "Updated subject")
		self.assertEqual(payload["origin"], "Administrator:testclient")
		self.assertEqual(payload["user"], "Administrator")

	def test_publishes_valid_child_row_update(self):
		"""Child-row updates targeting a declared table doctype are published."""
		with patch("erpnext_enhancements.api.collab.frappe.publish_realtime") as pub:
			broadcast_field_update(
				**self._valid_args(
					fieldname="task",
					value="TASK-0001",
					child_doctype="Task Depends On",
					child_name="some-row-name",
				)
			)

		pub.assert_called_once()
		payload = pub.call_args[0][1]
		self.assertEqual(payload["child_doctype"], "Task Depends On")
		self.assertEqual(payload["child_name"], "some-row-name")
