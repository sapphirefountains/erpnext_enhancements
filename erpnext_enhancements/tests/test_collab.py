"""Tests for the live collaborative form sync relay (``api.collab``).

Verifies that ``broadcast_field_update`` enforces the doctype allowlist,
write permission, field validity (existence + value-holding fieldtype),
value size cap, and child-table validity — and that a valid call publishes
exactly one ``collab_field_update`` event to the document's realtime room.
``broadcast_focus`` (per-field presence) shares the same guards and is
covered for both the focus and blur shapes.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.api.collab import (
	MAX_VALUE_LENGTH,
	broadcast_field_update,
	broadcast_focus,
)

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
			broadcast_field_update(**self._valid_args(doctype="Sales Invoice", docname="X"))

	def _ensure_no_role_user(self):
		if not frappe.db.exists("User", NO_ROLE_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": NO_ROLE_USER,
					"first_name": "Collab",
					"last_name": "NoRole",
				}
			).insert(ignore_permissions=True)

	def test_rejects_without_write_permission(self):
		"""A user without write permission on the Task gets PermissionError."""
		self._ensure_no_role_user()
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

	# ---------------------------------------------------- broadcast_focus

	def _focus_args(self, **overrides):
		args = {
			"doctype": "Task",
			"docname": self.task.name,
			"fieldname": "subject",
			"origin": "Administrator:testclient",
			"focused": 1,
		}
		args.update(overrides)
		return args

	def test_focus_rejects_disallowed_doctype(self):
		"""Focus presence respects the same doctype allowlist."""
		with self.assertRaises(frappe.ValidationError):
			broadcast_focus(**self._focus_args(doctype="Sales Invoice", docname="X"))

	def test_focus_rejects_without_write_permission(self):
		"""Only users who can edit the document broadcast focus presence."""
		self._ensure_no_role_user()
		frappe.set_user(NO_ROLE_USER)
		with self.assertRaises(frappe.PermissionError):
			broadcast_focus(**self._focus_args())

	def test_focus_requires_fieldname_when_focused(self):
		"""A focus event must name a field; only blurs may omit it."""
		with self.assertRaises(frappe.ValidationError):
			broadcast_focus(**self._focus_args(fieldname=None))

	def test_focus_publishes_to_doc_room(self):
		"""A valid focus event publishes collab_focus with user identity."""
		with patch("erpnext_enhancements.api.collab.frappe.publish_realtime") as pub:
			broadcast_focus(**self._focus_args())

		pub.assert_called_once()
		args, kwargs = pub.call_args
		self.assertEqual(args[0], "collab_focus")
		self.assertEqual(kwargs.get("doctype"), "Task")
		self.assertEqual(kwargs.get("docname"), self.task.name)
		payload = args[1]
		self.assertEqual(payload["fieldname"], "subject")
		self.assertEqual(payload["focused"], 1)
		self.assertEqual(payload["user"], "Administrator")
		self.assertTrue(payload["user_fullname"])

	def test_blur_publishes_without_fieldname(self):
		"""A blur (focused=0) needs no fieldname and still publishes."""
		with patch("erpnext_enhancements.api.collab.frappe.publish_realtime") as pub:
			broadcast_focus(**self._focus_args(fieldname=None, focused=0))

		pub.assert_called_once()
		payload = pub.call_args[0][1]
		self.assertEqual(payload["focused"], 0)
		self.assertIsNone(payload["fieldname"])
