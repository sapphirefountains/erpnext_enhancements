# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The ``Enhancement Request`` state machine, pinned against the DocType that stores it.

Two failure modes this guards, both silent:

1. **The enum and the ``Select`` drift.** ``product_feedback/states.py`` is a transcription of
   ``Enhancement Request.status``'s options. Renaming a Select option needs a data patch or
   existing rows refuse to save, so a rename must be *visible* before it ships — and the only
   thing that can see it is a test comparing the two. Same guard as
   ``test_chat_sync_states.py``.
2. **A transition nobody meant to allow.** The table is enumerated over its whole cross
   product here, so every ordered pair is either explicitly legal or raises. A scattering of
   ``if`` statements can only be tested where somebody remembered to look.

Bench-free: the state module is stdlib-only and the DocType is read as JSON.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_states
"""

import json
import re
import unittest
from pathlib import Path

from erpnext_enhancements.product_feedback.states import (
	LEGAL_TRANSITIONS,
	TERMINAL_STATES,
	IllegalTransition,
	RequestState,
	assert_transition,
	is_legal,
	is_open,
)

APP = Path(__file__).resolve().parents[1]
SPA_STATUS_JS = APP / "public" / "js" / "feedback" / "status.js"
DOCTYPE_JSON = (
	APP / "product_feedback" / "doctype" / "enhancement_request" / "enhancement_request.json"
)


def _status_options():
	"""The shipped ``Select`` options for ``status``, in declaration order."""
	schema = json.loads(DOCTYPE_JSON.read_text(encoding="utf-8"))
	for field in schema["fields"]:
		if field.get("fieldname") == "status":
			return [option for option in (field.get("options") or "").split("\n") if option]
	raise AssertionError("Enhancement Request has no `status` field any more")


class TestStatusMatchesDocType(unittest.TestCase):
	def test_enum_matches_the_select(self):
		self.assertEqual(
			[state.value for state in RequestState],
			_status_options(),
			"RequestState and Enhancement Request.status have drifted. Renaming a Select "
			"option needs a data patch or existing rows refuse to save.",
		)

	def test_default_is_the_first_state(self):
		schema = json.loads(DOCTYPE_JSON.read_text(encoding="utf-8"))
		default = next(
			field.get("default") for field in schema["fields"] if field.get("fieldname") == "status"
		)
		self.assertEqual(default, RequestState.SUBMITTED.value)

	def test_every_state_has_a_transition_row(self):
		self.assertEqual(set(LEGAL_TRANSITIONS), {state.value for state in RequestState})


class TestTransitions(unittest.TestCase):
	def test_whole_cross_product_is_decided(self):
		"""Every ordered pair either passes or raises. Nothing is undefined."""
		for old in RequestState:
			for new in RequestState:
				with self.subTest(old=old.value, new=new.value):
					if old == new or new.value in LEGAL_TRANSITIONS[old.value]:
						self.assertEqual(assert_transition(old.value, new.value), new.value)
					else:
						with self.assertRaises(IllegalTransition):
							assert_transition(old.value, new.value)

	def test_a_no_op_is_legal(self):
		"""Saving a document without touching its status must not raise.

		``bench migrate``'s own resaves do exactly that, and so does every edit to a field
		other than ``status``.
		"""
		for state in RequestState:
			self.assertTrue(is_legal(state.value, state.value))

	def test_terminal_states_are_terminal(self):
		for state in TERMINAL_STATES:
			self.assertEqual(LEGAL_TRANSITIONS[state], frozenset())
			self.assertFalse(is_open(state))

	def test_tasks_created_cannot_be_walked_back(self):
		"""The property that stops one proposal being written to a board twice."""
		for state in RequestState:
			if state == RequestState.TASKS_CREATED:
				continue
			self.assertFalse(is_legal(RequestState.TASKS_CREATED.value, state.value))

	def test_a_rerun_goes_back_to_approved(self):
		"""Both post-breakdown states return to the state the sweeper re-drives.

		There is deliberately no ``Regenerating`` state, so a re-run and a job the deploy
		FLUSHDB destroyed are the same recovery and the same query.
		"""
		self.assertTrue(is_legal(RequestState.BREAKDOWN_READY.value, RequestState.APPROVED.value))
		self.assertTrue(is_legal(RequestState.BREAKDOWN_FAILED.value, RequestState.APPROVED.value))

	def test_a_reviewer_can_close_from_any_open_state(self):
		"""Rejecting must not require spending a model call first."""
		for state in (
			RequestState.SUBMITTED,
			RequestState.APPROVED,
			RequestState.BREAKDOWN_READY,
			RequestState.BREAKDOWN_FAILED,
		):
			self.assertTrue(is_legal(state.value, RequestState.REJECTED.value))
			self.assertTrue(is_legal(state.value, RequestState.DUPLICATE.value))

	def test_submitted_cannot_jump_straight_to_tasks_created(self):
		"""The human gate. Approval is not skippable."""
		self.assertFalse(is_legal(RequestState.SUBMITTED.value, RequestState.TASKS_CREATED.value))
		self.assertFalse(is_legal(RequestState.SUBMITTED.value, RequestState.BREAKDOWN_READY.value))

	def test_unknown_states_are_refused(self):
		with self.assertRaises(IllegalTransition):
			assert_transition("Submitted", "Shipped")
		with self.assertRaises(IllegalTransition):
			assert_transition("Invented", "Approved")

	def test_empty_reads_as_submitted(self):
		"""A row loaded before the default applied must not raise on save."""
		self.assertTrue(is_legal("", RequestState.APPROVED.value))
		self.assertTrue(is_open(""))
class TestTheDerivedLabelIsNotAStatus(unittest.TestCase):
	"""The SPA shows a label this machine deliberately cannot produce.

	`Tasks Created` is terminal, so a request whose work is all finished can never be moved
	anywhere — and it read "Tasks Created" on the board forever while the column beside it
	said 2/2. The fix is a display rule in `public/js/feedback/status.js` over the task
	counts the page already holds, not an eighth status.

	**The hazard is that somebody later adds a stored status of the same name.** Then two
	different things are spelled identically — one meaning "the column says so", one meaning
	"the tasks say so" — and they disagree the first time a task is reopened. This test
	failing is the point: it makes that a decision instead of a collision.
	"""

	def _derived_label(self):
		source = SPA_STATUS_JS.read_text(encoding="utf-8")
		match = re.search(r'export const TASKS_COMPLETED = "([^"]+)"', source)
		self.assertIsNotNone(match, "status.js no longer exports TASKS_COMPLETED")
		return match.group(1)

	def test_the_derived_label_is_not_a_request_state(self):
		self.assertNotIn(self._derived_label(), {s.value for s in RequestState})

	def test_it_derives_from_the_terminal_state_that_cannot_move(self):
		"""If `Tasks Created` ever stops being terminal, the display rule stops being the
		right shape and this whole approach wants revisiting."""
		source = SPA_STATUS_JS.read_text(encoding="utf-8")
		self.assertIn(f'"{RequestState.TASKS_CREATED.value}"', source)
		self.assertEqual(LEGAL_TRANSITIONS[RequestState.TASKS_CREATED], frozenset())


if __name__ == "__main__":
	unittest.main()
