# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""What counts as drift, executed rather than inspected. Bench-free, unittest.

:mod:`chat.governance.drift_rules` imports nothing, which is what lets this run in the
bench-free tier — the same split :mod:`chat.governance.alert_rules` and
:mod:`chat.notifications.policy` already use.

**The property this file exists for** is the one that would otherwise ship and look fine:

    Every class fires on positive evidence that the mirror ACTED, never on the absence of a
    value.

This app ships switched off — ``enabled = 0``, ``dry_run_mode = 1``,
``relay_outbound_enabled = 0``, ``relay_inbound_enabled = 0``. So on a shipped-state site
every message has an empty ``gchat_message_name`` and no Google resource has an ERPNext row.
A detector keyed on absence reports the entire corpus as drifted, forever, and cannot tell
"the mirror drifted" from "the mirror was never turned on". :class:`EvidenceTest` is the
executable form of that rule, and :class:`DormantSiteTest` is the scenario itself.
"""

import unittest
from typing import ClassVar

from erpnext_enhancements.chat.governance import drift_rules as rules


class EvidenceTest(unittest.TestCase):
	"""The rule the module is shaped around, asserted rather than commented."""

	def test_every_class_states_the_positive_fact_it_fires_on(self):
		for drift_class in rules.CLASSES:
			self.assertFalse(
				rules.missing_evidence(drift_class),
				f"{drift_class} has no EVIDENCE entry, so nothing says what it fires on",
			)

	def test_a_class_without_evidence_cannot_produce_a_key(self):
		"""The enforcement point. A class added without writing down its evidence fails here
		rather than shipping as an absence test."""
		rules.CLASSES = (*rules.CLASSES, "invented_class")
		self.addCleanup(setattr, rules, "CLASSES", rules.CLASSES[:-1])
		with self.assertRaises(ValueError):
			rules.report_key("invented_class", "anything")

	def test_the_evidence_table_covers_exactly_the_classes(self):
		self.assertEqual(set(rules.EVIDENCE), set(rules.CLASSES))


class DormantSiteTest(unittest.TestCase):
	"""The scenario that kills the obvious detector, run against the real classifier.

	A site where the mirror has never been switched on: messages exist, none is bound to a
	Google resource, and no relay job or inbound event has ever been written.
	"""

	DORMANT: ClassVar[dict] = {
		"gchat_message_name": "",
		"resulting_message": "",
		"has_dead_relay_job": False,
		"has_done_relay_job": False,
		"inbound_failed": False,
	}

	def test_an_unmirrored_message_on_a_dormant_site_is_not_drift(self):
		self.assertIsNone(rules.classify_message(dict(self.DORMANT)))

	def test_the_same_message_IS_drift_once_a_relay_job_completed(self):
		"""One fact changes — Google accepted the write — and the verdict flips. That single
		difference is the entire distinction between drift and configuration."""
		facts = dict(self.DORMANT, has_done_relay_job=True)
		self.assertEqual(rules.classify_message(facts), rules.BOUND_LOST_AFTER_RELAY)

	def test_a_bound_message_with_a_completed_job_is_not_drift(self):
		facts = dict(self.DORMANT, has_done_relay_job=True, gchat_message_name="spaces/A/messages/B")
		self.assertIsNone(rules.classify_message(facts))

	def test_a_whitespace_only_binding_counts_as_unbound(self):
		facts = dict(self.DORMANT, has_done_relay_job=True, gchat_message_name="   ")
		self.assertEqual(rules.classify_message(facts), rules.BOUND_LOST_AFTER_RELAY)

	def test_an_inbound_event_that_never_arrived_is_not_drift(self):
		self.assertIsNone(rules.classify_message(dict(self.DORMANT, resulting_message="")))

	def test_an_inbound_event_that_arrived_and_was_abandoned_IS_drift(self):
		facts = dict(self.DORMANT, inbound_failed=True)
		self.assertEqual(rules.classify_message(facts), rules.INBOUND_ABANDONED)

	def test_an_abandoned_event_that_still_produced_a_message_is_not_drift(self):
		facts = dict(self.DORMANT, inbound_failed=True, resulting_message="cm-123")
		self.assertIsNone(rules.classify_message(facts))


class PrecedenceTest(unittest.TestCase):
	def test_a_dead_letter_beats_a_lost_binding(self):
		"""One object, one class. A dead create job on an unbound message satisfies both, and
		the dead letter is the more specific and more actionable fact."""
		facts = {
			"has_dead_relay_job": True,
			"has_done_relay_job": True,
			"gchat_message_name": "",
			"inbound_failed": True,
			"resulting_message": "",
		}
		self.assertEqual(rules.classify_message(facts), rules.RELAY_DEAD_LETTER)

	def test_precedence_lists_every_message_class(self):
		"""A message class missing from PRECEDENCE would be unreachable behind another."""
		self.assertEqual(set(rules.PRECEDENCE), set(rules.MESSAGE_CLASSES))

	def test_the_class_groups_partition_the_whole_set(self):
		self.assertEqual(set(rules.MESSAGE_CLASSES) | set(rules.ROOM_CLASSES), set(rules.CLASSES))
		self.assertFalse(set(rules.MESSAGE_CLASSES) & set(rules.ROOM_CLASSES))


class RoomTest(unittest.TestCase):
	def test_an_unreadable_room_beats_a_stale_one(self):
		"""The order is load-bearing: having nobody to impersonate is WHY the watermark is
		old, so reporting the staleness would describe the symptom and bury the cause."""
		facts = {
			"is_mirrored": True,
			"subject": "",
			"hours_since_reconcile": 10_000,
			"stale_after_hours": 168,
		}
		self.assertEqual(rules.classify_room(facts), rules.ROOM_UNREADABLE)

	def test_a_readable_stale_room_is_stale(self):
		facts = {
			"is_mirrored": True,
			"subject": "a@b.com",
			"hours_since_reconcile": 200,
			"stale_after_hours": 168,
		}
		self.assertEqual(rules.classify_room(facts), rules.RECONCILE_STALE)

	def test_a_freshly_swept_room_is_clean(self):
		facts = {
			"is_mirrored": True,
			"subject": "a@b.com",
			"hours_since_reconcile": 3,
			"stale_after_hours": 168,
		}
		self.assertIsNone(rules.classify_room(facts))

	def test_a_room_that_is_not_mirrored_is_never_a_finding(self):
		facts = {"is_mirrored": False, "subject": "", "hours_since_reconcile": 10_000}
		self.assertIsNone(rules.classify_room(facts))

	def test_the_boundary_is_inclusive(self):
		facts = {
			"is_mirrored": True,
			"subject": "a@b.com",
			"hours_since_reconcile": 168,
			"stale_after_hours": 168,
		}
		self.assertEqual(rules.classify_room(facts), rules.RECONCILE_STALE)


class StaleThresholdTest(unittest.TestCase):
	"""The ADR's seven days is right on a small site and wrong on a large one, in the
	direction that matters — it would alarm on the whole estate for working correctly."""

	def test_a_small_site_keeps_the_documented_seven_days(self):
		self.assertEqual(rules.reconcile_stale_hours(200, 168), 168)

	def test_a_large_site_raises_the_floor_above_its_own_rotation(self):
		"""5,000 rooms at 25 per hourly pass is a 200-hour rotation. Seven days would alarm
		on every room in it."""
		self.assertEqual(rules.reconcile_stale_hours(5000, 168), 400)

	def test_the_floor_is_twice_the_rotation_and_not_exactly_it(self):
		"""A threshold at exactly the interval alarms on whichever room is last in the
		rotation, every single pass."""
		rooms = 25 * 100  # a clean 100-hour rotation
		self.assertEqual(rules.reconcile_stale_hours(rooms, 1), 200)

	def test_a_partial_batch_rounds_up(self):
		self.assertEqual(rules.reconcile_stale_hours(26, 1), 4)

	def test_zero_rooms_falls_back_to_the_configured_value(self):
		self.assertEqual(rules.reconcile_stale_hours(0, 72), 72)

	def test_an_unset_setting_uses_the_documented_default(self):
		self.assertEqual(rules.reconcile_stale_hours(0, 0), rules.DEFAULT_RECONCILE_STALE_HOURS)


class SettlingTest(unittest.TestCase):
	def test_a_recent_object_is_not_settled(self):
		self.assertFalse(rules.is_settled(60, 30))

	def test_an_old_object_is_settled(self):
		self.assertTrue(rules.is_settled(3600, 30))

	def test_the_boundary_is_inclusive(self):
		self.assertTrue(rules.is_settled(30 * 60, 30))

	def test_an_unknown_age_is_treated_as_unsettled(self):
		"""A finding withheld this run is reported next run; a finding raised against an
		object still being written is one an operator has to chase."""
		self.assertFalse(rules.is_settled(None, 30))

	def test_a_zero_setting_falls_back_rather_than_settling_everything(self):
		"""0 meaning 'no window' would call every in-flight object drifted."""
		self.assertFalse(rules.is_settled(60, 0))
		self.assertTrue(rules.is_settled(rules.DEFAULT_SETTLING_MINUTES * 60, 0))

	def test_a_room_with_live_relay_work_is_converging(self):
		self.assertTrue(rules.room_is_converging(1))
		self.assertFalse(rules.room_is_converging(0))


class RunCapTest(unittest.TestCase):
	def test_under_the_cap_reports(self):
		self.assertEqual(rules.run_verdict(10, 200), rules.VERDICT_REPORT)

	def test_at_the_cap_still_reports(self):
		self.assertEqual(rules.run_verdict(200, 200), rules.VERDICT_REPORT)

	def test_over_the_cap_halts(self):
		self.assertEqual(rules.run_verdict(201, 200), rules.VERDICT_HALT)

	def test_a_zero_cap_falls_back_rather_than_halting_on_everything(self):
		self.assertEqual(rules.run_verdict(1, 0), rules.VERDICT_REPORT)
		self.assertEqual(rules.run_verdict(rules.DEFAULT_MAX_FINDINGS_PER_RUN + 1, 0), rules.VERDICT_HALT)

	def test_an_empty_run_reports(self):
		self.assertEqual(rules.run_verdict(0, 200), rules.VERDICT_REPORT)


class ReportKeyTest(unittest.TestCase):
	def test_the_key_is_class_and_scope(self):
		self.assertEqual(rules.report_key(rules.RELAY_DEAD_LETTER, "job-abc"), "relay_dead_letter::job-abc")

	def test_the_same_finding_produces_the_same_key(self):
		self.assertEqual(
			rules.report_key(rules.RECONCILE_STALE, "Room-1"),
			rules.report_key(rules.RECONCILE_STALE, "room-1"),
		)

	def test_two_scopes_are_two_findings(self):
		self.assertNotEqual(
			rules.report_key(rules.ROOM_UNREADABLE, "a"),
			rules.report_key(rules.ROOM_UNREADABLE, "b"),
		)

	def test_an_empty_scope_leaves_no_trailing_separator(self):
		self.assertEqual(rules.report_key(rules.RECONCILE_STALE, ""), rules.RECONCILE_STALE)

	def test_an_unknown_class_is_refused(self):
		with self.assertRaises(ValueError):
			rules.report_key("made_up", "x")

	def test_punctuation_in_a_scope_slugs_stably(self):
		key = rules.report_key(rules.ROOM_UNREADABLE, "spaces/AAQA1b2C3")
		self.assertEqual(key, "room_unreadable::spaces-aaqa1b2c3")
		self.assertEqual(key, rules.report_key(rules.ROOM_UNREADABLE, "spaces/AAQA1b2C3"))


class LifecycleTest(unittest.TestCase):
	def test_re_observing_keeps_an_open_finding_open(self):
		self.assertEqual(rules.transition(rules.STATE_OPEN, rules.EVENT_OBSERVE), rules.STATE_OPEN)

	def test_re_observing_does_not_un_accept(self):
		"""Accepting means 'we know and are not fixing it'. Undoing that would hand the
		operator who triaged it the same row again tomorrow."""
		self.assertEqual(rules.transition(rules.STATE_ACCEPTED, rules.EVENT_OBSERVE), rules.STATE_ACCEPTED)

	def test_an_accepted_finding_can_still_be_cleared(self):
		"""Accepting is not 'never tell me it is gone'."""
		self.assertEqual(rules.transition(rules.STATE_ACCEPTED, rules.EVENT_CLEAR), rules.STATE_CLEARED)

	def test_cleared_is_terminal(self):
		self.assertIsNone(rules.transition(rules.STATE_CLEARED, rules.EVENT_OBSERVE))
		self.assertIsNone(rules.transition(rules.STATE_CLEARED, rules.EVENT_ACCEPT))

	def test_live_states_are_exactly_open_and_accepted(self):
		"""The scan's re-observation lookup filters on this set; a state missing from it
		would make every scan open a second row instead of updating the first."""
		self.assertEqual(rules.LIVE_STATES, frozenset({rules.STATE_OPEN, rules.STATE_ACCEPTED}))
		self.assertFalse(rules.is_live(rules.STATE_CLEARED))

	def test_an_unknown_event_is_refused_rather_than_ignored(self):
		self.assertIsNone(rules.transition(rules.STATE_OPEN, "repair"))


if __name__ == "__main__":
	unittest.main()
