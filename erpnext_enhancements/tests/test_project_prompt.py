"""Tests for the Closed-Won create-project prompt
(``erpnext_enhancements.crm_enhancements.project_prompt``).

* ``prompt_create_project_on_won`` publishes the popup event only on the
  *transition* into "Closed Won" (incl. docs created directly in that status),
  never on a re-save, another status, an already-converted opportunity, or in a
  bulk/migrate context.
* ``revert_won_status`` (the popup's "No") restores the prior status, clears the
  won-date stamp, and refuses once a Project exists.
* ``default_project_notify_users`` returns the four group inboxes as addresses,
  without looking any of them up as a User.
* ``crm_enhancements.api._notify_recipients`` — tested here rather than beside its
  module because it exists to clean up what *this* dialog submits — drops the empty
  element a trailing MultiSelect separator leaves behind, which used to reach
  ``frappe.sendmail`` as a blank recipient and get the message refused by Gmail.
  ``enqueue_project_creation`` refuses anything that is not an email address, now
  that the field takes free text.
* ``create_project_from_opportunity_background`` sends its realtime status to the
  requester as well as the listed recipients, since the default list is inboxes
  with no desk session to receive it.

These fake the document / DB calls; full delivery + creation run against a bench.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.crm_enhancements.api import (
	_notify_recipients,
	create_project_from_opportunity_background,
	enqueue_project_creation,
)
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


INBOXES = [
	"billing@sapphirefountains.com",
	"operations@sapphirefountains.com",
	"production@sapphirefountains.com",
	"sales@sapphirefountains.com",
]


class TestDefaultNotifyUsers(FrappeTestCase):
	def test_returns_the_group_inboxes(self):
		self.assertEqual(default_project_notify_users(), INBOXES)

	def test_never_resolves_them_through_user(self):
		# None of the four is an enabled System User on prod, so any User lookup
		# would filter every one of them out.
		with (
			patch.object(frappe, "get_all", side_effect=AssertionError("queried")),
			patch.object(frappe.db, "exists", side_effect=AssertionError("queried")),
		):
			self.assertEqual(default_project_notify_users(), INBOXES)

	def test_returns_a_fresh_list(self):
		default_project_notify_users().append("someone@example.com")
		self.assertEqual(default_project_notify_users(), INBOXES)


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


class TestNotifyRecipients(FrappeTestCase):
	"""``_notify_recipients`` — the guard on what reaches ``frappe.sendmail``."""

	def test_trailing_separator_yields_no_blank(self):
		# What the MultiSelect actually submits after two picks.
		self.assertEqual(
			_notify_recipients("a@x.com, b@y.com, "),
			["a@x.com", "b@y.com"],
		)

	def test_strips_surrounding_whitespace(self):
		self.assertEqual(_notify_recipients(" a@x.com ,b@y.com"), ["a@x.com", "b@y.com"])

	def test_accepts_a_list(self):
		self.assertEqual(_notify_recipients(["a@x.com", "", " ", None]), ["a@x.com"])

	def test_separators_only_resolve_to_nobody(self):
		for raw in (",", " , ", "", None, []):
			self.assertEqual(_notify_recipients(raw), [], f"unexpected recipients for {raw!r}")

	def test_enqueue_refuses_a_separators_only_field(self):
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				enqueue_project_creation("OPP-0001", users=",", project_template="PT-0001")
		enqueue.assert_not_called()

	def test_enqueue_passes_the_cleaned_list(self):
		with patch.object(frappe, "enqueue") as enqueue:
			enqueue_project_creation(
				"OPP-0001", users="a@x.com, b@y.com, ", project_template="PT-0001"
			)
		self.assertEqual(enqueue.call_args.kwargs["users"], ["a@x.com", "b@y.com"])

	def test_enqueue_accepts_the_default_inboxes(self):
		with patch.object(frappe, "enqueue") as enqueue:
			enqueue_project_creation("OPP-0001", users=", ".join(INBOXES), project_template="PT-0001")
		self.assertEqual(enqueue.call_args.kwargs["users"], INBOXES)

	def test_enqueue_refuses_something_that_is_not_an_address(self):
		# The field is free text now; a typo must not reach the queue.
		for raw in ("billing@sapphirefountains.com, Nik Bradshaw", "sales@", "Administrator"):
			with patch.object(frappe, "enqueue") as enqueue:
				with self.assertRaises(frappe.ValidationError, msg=raw):
					enqueue_project_creation("OPP-0001", users=raw, project_template="PT-0001")
			enqueue.assert_not_called()


REQUESTER = "requester@example.com"


class TestCreationStatusRecipients(FrappeTestCase):
	"""Who hears how the background creation went."""

	def setUp(self):
		# A requester who is not Administrator, and the real set_user. The job runs
		# as Administrator and switches back in a ``finally``; FrappeTestCase's own
		# user IS Administrator, so only a distinct requester shows the status went
		# to the restored identity rather than the elevated one. (set_user does not
		# check that the user exists.)
		frappe.set_user(REQUESTER)
		self.addCleanup(frappe.set_user, "Administrator")

	def _run_failing_job(self, users):
		# get_doc raising sends the job straight to its generic failure path, which
		# is all this needs: the notification block runs the same for every outcome.
		with (
			patch.object(frappe, "get_doc", side_effect=RuntimeError("boom")),
			patch.object(frappe, "log_error"),
			patch.object(frappe, "publish_realtime") as publish,
			patch.object(frappe, "sendmail") as sendmail,
		):
			create_project_from_opportunity_background("OPP-0001", users, "PT-0001")
		return [c.kwargs["user"] for c in publish.call_args_list], sendmail

	def test_requester_hears_back_when_only_inboxes_are_listed(self):
		recipients, sendmail = self._run_failing_job(INBOXES)
		self.assertEqual(recipients, [*INBOXES, REQUESTER])
		self.assertNotIn("Administrator", recipients)
		sendmail.assert_not_called()  # no project, so no email

	def test_listed_requester_is_not_told_twice(self):
		recipients, _ = self._run_failing_job([REQUESTER, *INBOXES])
		self.assertEqual(recipients.count(REQUESTER), 1)
