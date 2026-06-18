"""Tests for the Closed-Won create-project prompt
(``erpnext_enhancements.crm_enhancements.project_prompt``).

* ``prompt_create_project_on_won`` publishes the popup event only on the
  *transition* into "Closed Won" (incl. docs created directly in that status),
  never on a re-save, another status, an already-converted opportunity, or in a
  bulk/migrate context.
* ``revert_won_status`` (the popup's "No") restores the prior status, clears the
  won-date stamp, and refuses once a Project exists.
* ``default_project_notify_users`` resolves the Account Executive + Project
  Manager role holders, skips a missing role, and falls back to the current user.

These fake the document / DB calls; full delivery + creation run against a bench.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.crm_enhancements.project_prompt import (
	default_project_notify_users,
	opportunity_handoff_steps,
	prompt_create_project_on_won,
	revert_won_status,
)


class _FakeOpp:
	"""Just enough of an Opportunity Document for the prompt + revert logic."""

	def __init__(self, status="Open", before_status=None, name="OPP-0001", **fields):
		self.name = name
		self.status = status
		self.custom_date_closed_won = fields.pop("custom_date_closed_won", None)
		self._fields = fields  # e.g. custom_created_project, _perm
		self._before = frappe._dict(status=before_status) if before_status is not None else None
		self._saved = False

	def get(self, key, default=None):
		if key in self._fields:
			return self._fields[key]
		return getattr(self, key, default)

	def get_doc_before_save(self):
		return self._before

	def has_permission(self, ptype):
		return self._fields.get("_perm", True)

	def save(self):
		self._saved = True


class TestPromptGuard(FrappeTestCase):
	def test_transition_into_won_publishes(self):
		doc = _FakeOpp(status="Closed Won", before_status="Negotiation/Review")
		with patch.object(frappe, "publish_realtime") as pub:
			prompt_create_project_on_won(doc)
		pub.assert_called_once()
		self.assertEqual(pub.call_args.args[0], "ee_prompt_create_project")
		payload = pub.call_args.args[1]
		self.assertEqual(payload["opportunity_name"], "OPP-0001")
		self.assertEqual(payload["previous_status"], "Negotiation/Review")

	def test_created_directly_as_won_publishes(self):
		doc = _FakeOpp(status="Closed Won")  # no before-doc
		with patch.object(frappe, "publish_realtime") as pub:
			prompt_create_project_on_won(doc)
		pub.assert_called_once()
		self.assertIsNone(pub.call_args.args[1]["previous_status"])

	def test_resave_of_won_is_silent(self):
		doc = _FakeOpp(status="Closed Won", before_status="Closed Won")
		with patch.object(frappe, "publish_realtime") as pub:
			prompt_create_project_on_won(doc)
		pub.assert_not_called()

	def test_other_status_is_silent(self):
		with patch.object(frappe, "publish_realtime") as pub:
			prompt_create_project_on_won(_FakeOpp(status="Qualification"))
			prompt_create_project_on_won(_FakeOpp(status="Lost", before_status="Closed Won"))
		pub.assert_not_called()

	def test_already_converted_is_silent(self):
		doc = _FakeOpp(status="Closed Won", before_status="Open", custom_created_project="PRJ-0001")
		with patch.object(frappe, "publish_realtime") as pub:
			prompt_create_project_on_won(doc)
		pub.assert_not_called()

	def test_silent_during_migrate(self):
		frappe.flags.in_migrate = True
		try:
			with patch.object(frappe, "publish_realtime") as pub:
				prompt_create_project_on_won(_FakeOpp(status="Closed Won", before_status="Open"))
			pub.assert_not_called()
		finally:
			frappe.flags.in_migrate = False

	def test_silent_during_bulk_update(self):
		frappe.flags.in_bulk_update = True
		try:
			with patch.object(frappe, "publish_realtime") as pub:
				prompt_create_project_on_won(_FakeOpp(status="Closed Won", before_status="Open"))
			pub.assert_not_called()
		finally:
			frappe.flags.in_bulk_update = False


class TestRevertWonStatus(FrappeTestCase):
	def test_reverts_status_and_clears_stamp(self):
		doc = _FakeOpp(status="Closed Won", custom_date_closed_won="2026-06-18")
		with patch.object(frappe, "get_doc", return_value=doc):
			result = revert_won_status("OPP-0001", previous_status="Negotiation/Review")
		self.assertEqual(doc.status, "Negotiation/Review")
		self.assertIsNone(doc.custom_date_closed_won)
		self.assertTrue(doc._saved)
		self.assertEqual(result["status"], "Negotiation/Review")

	def test_defaults_to_open_without_previous(self):
		doc = _FakeOpp(status="Closed Won", custom_date_closed_won="2026-06-18")
		with patch.object(frappe, "get_doc", return_value=doc):
			revert_won_status("OPP-0001")
		self.assertEqual(doc.status, "Open")

	def test_refuses_when_project_exists(self):
		doc = _FakeOpp(status="Closed Won", custom_created_project="PRJ-0001")
		with patch.object(frappe, "get_doc", return_value=doc):
			with self.assertRaises(frappe.ValidationError):
				revert_won_status("OPP-0001")
		self.assertFalse(doc._saved)


class TestDefaultNotifyUsers(FrappeTestCase):
	def test_returns_role_holders_deduped(self):
		holders = {
			"Account Executive": ["ae@example.com", "shared@example.com"],
			"Project Manager": ["pm@example.com", "shared@example.com"],
		}

		def fake_get_all(doctype, **kwargs):
			if doctype == "Has Role":
				return holders.get(kwargs["filters"]["role"], [])
			if doctype == "User":
				return list(kwargs["filters"]["name"][1])  # echo requested names as enabled
			return []

		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_all", side_effect=fake_get_all),
		):
			users = default_project_notify_users()
		self.assertEqual(set(users), {"ae@example.com", "pm@example.com", "shared@example.com"})

	def test_skips_absent_role(self):
		def fake_exists(doctype, name):
			return name != "Account Executive"

		def fake_get_all(doctype, **kwargs):
			if doctype == "Has Role":
				return ["pm@example.com"] if kwargs["filters"]["role"] == "Project Manager" else []
			if doctype == "User":
				return list(kwargs["filters"]["name"][1])
			return []

		with (
			patch.object(frappe.db, "exists", side_effect=fake_exists),
			patch.object(frappe, "get_all", side_effect=fake_get_all),
		):
			users = default_project_notify_users()
		self.assertEqual(users, ["pm@example.com"])

	def test_fallback_to_current_user_when_empty(self):
		with (
			patch.object(frappe.db, "exists", return_value=True),
			patch.object(frappe, "get_all", return_value=[]),
		):
			users = default_project_notify_users()
		self.assertEqual(users, [frappe.session.user])


class TestOpportunityHandoffSteps(FrappeTestCase):
	def test_no_project_returns_empty(self):
		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe.db, "get_value", return_value=None),
		):
			result = opportunity_handoff_steps("OPP-0001")
		self.assertEqual(result, {"project": None, "steps": []})

	def test_returns_first_three_project_steps(self):
		rows = [
			frappe._dict(step_number=1, step_title="Mark Opportunity as Won", status="Completed"),
			frappe._dict(step_number=2, step_title="Hold Hand-Off Meeting", status="Pending"),
			frappe._dict(step_number=3, step_title="Create Project in PM System", status="Completed"),
		]
		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe.db, "get_value", return_value="PRJ-0001"),
			patch.object(frappe, "get_all", return_value=rows) as get_all,
		):
			result = opportunity_handoff_steps("OPP-0001")
		self.assertEqual(result["project"], "PRJ-0001")
		self.assertEqual(len(result["steps"]), 3)
		self.assertEqual(get_all.call_args.kwargs["filters"]["parent"], "PRJ-0001")

	def test_refuses_without_permission(self):
		with patch.object(frappe, "has_permission", return_value=False):
			with self.assertRaises(frappe.PermissionError):
				opportunity_handoff_steps("OPP-0001")
