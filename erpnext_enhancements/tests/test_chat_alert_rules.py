# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The alert path's judgement, executed rather than inspected. Bench-free, unittest.

Most of the chat governance suites in this series assert against the AST, because the thing
under test needs a database. This one does not: :mod:`chat.governance.alert_rules` imports
nothing and touches nothing, which was the point of splitting it out — the decisions an
alert path makes are exactly the part you want to be able to run.

The four properties, each with the failure it prevents:

1. **The deduplication key never carries a measurement.** A key containing the queue depth
   changes every time the depth does, so nothing ever matches its predecessor and the table
   grows exactly as fast as it would with no deduplication — while the code, the schema and
   the dashboard all say the alerts are deduplicated. Invisible until somebody counts rows.
2. **Repeats notify on a doubling schedule.** Not every time (twelve pages for one incident)
   and not once ever (an incident firing for eight hours looks identical to one that stopped).
3. **Resolved is terminal.** Reopening overwrites the resolution timestamp, which destroys
   the only evidence that a problem is flapping rather than continuing.
4. **An alert is never delivered by the thing it is about.** The ops-space channel is a
   ``Chat Message`` carried by the relay, so an alert about the relay would be queued behind
   the incident it reports.
"""

import unittest

from erpnext_enhancements.chat.governance import alert_rules as rules


class DedupKeyTest(unittest.TestCase):
	def test_the_key_is_subsystem_kind_scope(self):
		self.assertEqual(
			rules.dedup_key("relay", "queue_stalled", "room-abc"), "relay::queue_stalled::room-abc"
		)

	def test_scope_is_omitted_when_absent_rather_than_left_trailing(self):
		"""``relay::x::`` and ``relay::x`` must not be two keys for one problem."""
		self.assertEqual(rules.dedup_key("relay", "x"), "relay::x")
		self.assertEqual(rules.dedup_key("relay", "x", ""), "relay::x")
		self.assertEqual(rules.dedup_key("relay", "x", "   "), "relay::x")

	def test_the_same_problem_produces_the_same_key_every_time(self):
		"""The whole mechanism. Stated as its own test because everything else assumes it."""
		first = rules.dedup_key("inbound", "ECHO_ORPHAN", "3f9a2b")
		second = rules.dedup_key("inbound", "echo_orphan", "3F9A2B")
		self.assertEqual(first, second)

	def test_two_scopes_are_two_incidents(self):
		self.assertNotEqual(
			rules.dedup_key("relay", "stalled", "room-a"),
			rules.dedup_key("relay", "stalled", "room-b"),
		)

	def test_punctuation_in_a_scope_survives_as_a_stable_slug(self):
		"""Space names carry ``/`` and room names are hashes. Both must key stably."""
		key = rules.dedup_key("gchat", "subscription_lapsed", "spaces/AAQA1b2C3d4")
		self.assertEqual(key, "gchat::subscription_lapsed::spaces-aaqa1b2c3d4")
		self.assertEqual(key, rules.dedup_key("gchat", "subscription_lapsed", "spaces/AAQA1b2C3d4"))

	def test_an_unknown_subsystem_is_refused_rather_than_accepted(self):
		"""Free strings would drift into three spellings, and three spellings are three
		incidents that never deduplicate against each other."""
		with self.assertRaises(ValueError):
			rules.dedup_key("relayworker", "stalled")

	def test_a_kind_is_required(self):
		with self.assertRaises(ValueError):
			rules.dedup_key("relay", "")

	def test_every_subsystem_that_is_self_delivering_is_a_known_subsystem(self):
		"""``SELF_DELIVERING`` is folded into ``SUBSYSTEMS``; a name in one and not the other
		would be silently un-keyable."""
		self.assertTrue(rules.SELF_DELIVERING <= rules.SUBSYSTEMS)


class NotificationScheduleTest(unittest.TestCase):
	def test_it_notifies_on_powers_of_two(self):
		notifying = [n for n in range(1, 65) if rules.notifies_at(n)]
		self.assertEqual(notifying, [1, 2, 4, 8, 16, 32, 64])

	def test_thirty_two_occurrences_produce_six_notifications(self):
		"""The number the design is chosen against: not 32, and not 1."""
		self.assertEqual(sum(1 for n in range(1, 33) if rules.notifies_at(n)), 6)

	def test_the_first_occurrence_always_notifies(self):
		self.assertTrue(rules.notifies_at(1))

	def test_a_zero_or_negative_occurrence_notifies_nothing(self):
		"""``0 & -1 == 0``, so the power-of-two test says yes to zero. It must not."""
		self.assertFalse(rules.notifies_at(0))
		self.assertFalse(rules.notifies_at(-4))

	def test_the_gaps_widen_and_never_stop(self):
		"""An incident that has been firing all weekend keeps saying so."""
		self.assertTrue(rules.notifies_at(1024))


class LifecycleTest(unittest.TestCase):
	def test_a_recurrence_keeps_an_open_alert_open(self):
		self.assertEqual(rules.transition(rules.STATE_OPEN, rules.EVENT_RECUR), rules.STATE_OPEN)

	def test_a_recurrence_does_not_un_acknowledge(self):
		"""Somebody said 'I am on it'; the problem continuing is what they are on."""
		self.assertEqual(
			rules.transition(rules.STATE_ACKNOWLEDGED, rules.EVENT_RECUR),
			rules.STATE_ACKNOWLEDGED,
		)

	def test_resolved_is_terminal_for_a_recurrence(self):
		"""The transition that matters, and it matters by being absent: reopening would
		overwrite ``resolved_at`` and lose the evidence that this is flapping."""
		self.assertIsNone(rules.transition(rules.STATE_RESOLVED, rules.EVENT_RECUR))

	def test_a_resolved_alert_cannot_be_acknowledged_either(self):
		self.assertIsNone(rules.transition(rules.STATE_RESOLVED, rules.EVENT_ACKNOWLEDGE))

	def test_both_live_states_can_be_resolved(self):
		for state in (rules.STATE_OPEN, rules.STATE_ACKNOWLEDGED):
			self.assertEqual(rules.transition(state, rules.EVENT_RESOLVE), rules.STATE_RESOLVED)

	def test_an_unknown_event_is_refused_rather_than_treated_as_no_change(self):
		self.assertIsNone(rules.transition(rules.STATE_OPEN, "escalate"))

	def test_live_states_are_exactly_open_and_acknowledged(self):
		"""``_live_alerts`` filters on this set; adding a state without adding it here would
		make new occurrences open a second row instead of updating the first."""
		self.assertEqual(rules.LIVE_STATES, frozenset({rules.STATE_OPEN, rules.STATE_ACKNOWLEDGED}))
		self.assertFalse(rules.is_live(rules.STATE_RESOLVED))


class TransportTest(unittest.TestCase):
	def test_the_log_and_the_record_are_unconditional(self):
		channels = rules.transports_for(
			"audit", rules.SEVERITY_WARNING, space_configured=False, email_configured=False
		)
		self.assertEqual(channels, (rules.TRANSPORT_LOG, rules.TRANSPORT_RECORD))

	def test_a_self_delivering_subsystem_never_uses_the_operations_space(self):
		"""The rule the whole module turns on. An alert about the relay posted into a chat
		room is carried by the relay — it arrives when the incident ends."""
		for subsystem in sorted(rules.SELF_DELIVERING):
			channels = rules.transports_for(
				subsystem, rules.SEVERITY_CRITICAL, space_configured=True, email_configured=True
			)
			self.assertNotIn(rules.TRANSPORT_SPACE, channels, subsystem)

	def test_a_self_delivering_subsystem_uses_email_even_for_a_warning(self):
		"""It has no other channel, so the severity filter that protects the inbox elsewhere
		would silence it entirely here."""
		channels = rules.transports_for(
			"relay", rules.SEVERITY_WARNING, space_configured=True, email_configured=True
		)
		self.assertIn(rules.TRANSPORT_EMAIL, channels)

	def test_an_ordinary_subsystem_emails_only_its_criticals(self):
		warning = rules.transports_for(
			"governance", rules.SEVERITY_WARNING, space_configured=True, email_configured=True
		)
		critical = rules.transports_for(
			"governance", rules.SEVERITY_CRITICAL, space_configured=True, email_configured=True
		)
		self.assertNotIn(rules.TRANSPORT_EMAIL, warning)
		self.assertIn(rules.TRANSPORT_EMAIL, critical)
		self.assertIn(rules.TRANSPORT_SPACE, warning)

	def test_an_unconfigured_channel_is_not_offered(self):
		channels = rules.transports_for(
			"governance", rules.SEVERITY_CRITICAL, space_configured=False, email_configured=False
		)
		self.assertNotIn(rules.TRANSPORT_SPACE, channels)
		self.assertNotIn(rules.TRANSPORT_EMAIL, channels)


class UndeliverableTest(unittest.TestCase):
	"""A configuration gap that silences alerting is the one gap that cannot report itself."""

	def test_a_self_delivering_subsystem_with_no_email_reaches_nobody(self):
		reason = rules.undeliverable_reason("relay", space_configured=True, email_configured=False)
		self.assertTrue(reason)
		self.assertIn("relay", reason)

	def test_a_configured_operations_space_does_not_rescue_a_self_delivering_subsystem(self):
		"""The trap this exists for: the space looks configured, so alerting looks configured,
		and the alerts that matter most are the ones it cannot carry."""
		self.assertTrue(rules.undeliverable_reason("inbound", space_configured=True, email_configured=False))
		self.assertFalse(
			rules.undeliverable_reason("indexing", space_configured=True, email_configured=False)
		)

	def test_nothing_configured_at_all_is_reported(self):
		self.assertTrue(
			rules.undeliverable_reason("governance", space_configured=False, email_configured=False)
		)

	def test_a_deliverable_alert_reports_no_reason(self):
		self.assertEqual(
			rules.undeliverable_reason("relay", space_configured=False, email_configured=True), ""
		)


class RateLimitTest(unittest.TestCase):
	def test_below_the_limit_is_allowed(self):
		self.assertEqual(rules.rate_limit_verdict(5, 20), rules.VERDICT_ALLOW)

	def test_the_storm_verdict_is_returned_exactly_once(self):
		"""Once, at the crossing — so there is one loud row rather than one per suppressed
		alert, which would be the storm all over again."""
		verdicts = [rules.rate_limit_verdict(n, 20) for n in range(18, 24)]
		self.assertEqual(verdicts.count(rules.VERDICT_STORM), 1)
		self.assertEqual(verdicts[2], rules.VERDICT_STORM)

	def test_past_the_limit_suppresses(self):
		self.assertEqual(rules.rate_limit_verdict(50, 20), rules.VERDICT_SUPPRESS)

	def test_a_zero_limit_falls_back_rather_than_silencing_everything(self):
		"""``alert_rate_limit_per_hour`` is a new field on a Single, so it reads 0 on every
		existing site until the backfill runs. Zero meaning 'no alerts at all' would turn a
		migration detail into total silence."""
		self.assertEqual(rules.rate_limit_verdict(5, 0), rules.VERDICT_ALLOW)
		self.assertEqual(rules.rate_limit_verdict(rules.DEFAULT_RATE_LIMIT_PER_HOUR, 0), rules.VERDICT_STORM)

	def test_the_storm_key_is_a_valid_key_so_it_can_be_deduplicated_too(self):
		self.assertEqual(rules.STORM_KEY, rules.dedup_key("alerting", "storm"))


class SummaryLineTest(unittest.TestCase):
	def test_it_carries_the_severity_the_subsystem_and_the_kind(self):
		line = rules.summary_line(rules.SEVERITY_CRITICAL, "audit", "chain_verification_failed", 1)
		self.assertIn("CRITICAL", line)
		self.assertIn("audit", line)
		self.assertIn("chain_verification_failed", line)

	def test_a_repeat_says_how_many(self):
		self.assertIn("(x8)", rules.summary_line(rules.SEVERITY_WARNING, "relay", "stalled", 8))
		self.assertNotIn("(x", rules.summary_line(rules.SEVERITY_WARNING, "relay", "stalled", 1))


if __name__ == "__main__":
	unittest.main()
