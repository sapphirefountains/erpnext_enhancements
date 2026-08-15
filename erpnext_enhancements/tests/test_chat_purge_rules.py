# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The survives-a-purge table and the eligibility rule, executed. Bench-free, unittest.

:mod:`chat.governance.purge_rules` imports nothing, which is what lets this run in the
bench-free tier.

**The load-bearing assertion is :class:`TableCoversTheFilesystemTest`** — set equality
between the disposition table and ``chat/doctype/`` on disk, so a chat DocType added next
year fails the build until somebody decides what a purge does to it. Containment would let a
new table be treated by accident, and the accident is silent in both directions: purged when
it should survive, or kept when it holds a body.

The second thing worth stating: :func:`purge_rules.holds` returns **every** reason rather
than the first. A message held by three rules is a different fact from one held by a boundary
condition, and a report that stops at the first hold cannot tell an operator which rule is
actually binding.
"""

import json
import pathlib
import unittest
from typing import ClassVar

from erpnext_enhancements.chat.governance import purge_rules as rules

_DOCTYPE_DIR = pathlib.Path(__file__).resolve().parents[1] / "chat" / "doctype"


def _doctypes_on_disk() -> set[str]:
	names = set()
	for path in _DOCTYPE_DIR.glob("*/*.json"):
		try:
			data = json.loads(path.read_text(encoding="utf-8"))
		except Exception:  # pragma: no cover - a malformed fixture is a different failure
			continue
		if data.get("doctype") == "DocType" and data.get("name"):
			names.add(str(data["name"]))
	return names


class TableCoversTheFilesystemTest(unittest.TestCase):
	def test_the_walk_finds_the_doctypes(self):
		"""An empty walk passes every assertion below it."""
		found = _doctypes_on_disk()
		self.assertGreater(len(found), 15)
		self.assertIn("Chat Message", found)

	def test_every_chat_doctype_is_classified(self):
		missing = rules.unclassified(_doctypes_on_disk())
		self.assertFalse(
			missing,
			f"these chat DocTypes exist on disk and nobody has decided what a purge does to "
			f"them: {list(missing)}.\n\n"
			"Add each to purge_rules.DISPOSITION as purge / survives / blocked, WITH the "
			"reason. This is set equality rather than containment precisely so a newly added "
			"chat table fails the build instead of being treated by accident — and the "
			"accident is silent in both directions.",
		)

	def test_no_entry_names_a_doctype_that_no_longer_exists(self):
		stale = rules.stale_entries(_doctypes_on_disk())
		self.assertFalse(
			stale,
			f"purge_rules.DISPOSITION names {list(stale)}, which no longer exist. Noise in a "
			"table that gets read under pressure is how the real entry hides.",
		)

	def test_every_entry_carries_a_reason(self):
		for name, (_disposition, reason) in rules.DISPOSITION.items():
			self.assertTrue(reason.strip(), f"{name} has no reason")
			self.assertGreater(len(reason), 40, f"{name}'s reason is too short to be one")

	def test_every_disposition_is_one_of_the_three(self):
		for name, (disposition, _reason) in rules.DISPOSITION.items():
			self.assertIn(disposition, rules.DISPOSITIONS, name)

	def test_the_three_groups_partition_the_table(self):
		total = (
			len(rules.purgeable_doctypes()) + len(rules.surviving_doctypes()) + len(rules.blocked_doctypes())
		)
		self.assertEqual(total, len(rules.DISPOSITION))


class TheAuditTrailSurvivesTest(unittest.TestCase):
	"""Decision D-7, as an assertion rather than a paragraph."""

	AUDIT_TABLES: ClassVar[tuple] = ("Chat Audit Log", "Chat Retrieval Audit", "Chat Retrieval Audit Room")

	def test_no_audit_table_is_ever_purged(self):
		for name in self.AUDIT_TABLES:
			self.assertEqual(
				rules.DISPOSITION[name][0],
				rules.SURVIVES,
				f"{name} is not marked as surviving a purge. Bodies go; the record that "
				"somebody read them stays.",
			)

	def test_the_chained_child_table_is_named_explicitly(self):
		"""The trap: its controller is `pass`, so it has no guard of its own, yet its rows are
		inside the parent's hash. A cleanup over it meets nothing and breaks the chain."""
		reason = rules.DISPOSITION["Chat Retrieval Audit Room"][1]
		self.assertIn("hash", reason.lower())

	def test_the_bodies_and_their_sidecars_are_purged(self):
		for name in ("Chat Message", "Chat Message Revision", "Chat Attachment"):
			self.assertEqual(rules.DISPOSITION[name][0], rules.PURGE, name)

	def test_the_queue_tables_keep_their_own_retention(self):
		"""A second deleter for one table is how two retention rules end up disagreeing."""
		for name in ("Chat Relay Job", "Chat Inbound Event"):
			self.assertEqual(rules.DISPOSITION[name][0], rules.SURVIVES, name)


class BlockerTest(unittest.TestCase):
	def test_the_derived_artefacts_are_blocked_rather_than_decided(self):
		for name in ("Chat Context Chunk", "Chat Room Digest", "Chat Thread Digest"):
			self.assertEqual(
				rules.DISPOSITION[name][0],
				rules.BLOCKED,
				f"{name} must be BLOCKED, not purge or survives. Both answers are wrong: "
				"leaving it serves a summary of the destroyed conversation forever, and "
				"deleting it retreats the indexer watermark so the sweep re-chunks the "
				"not-yet-purged messages verbatim.",
			)

	def test_the_destructive_path_cannot_be_enabled_while_anything_is_blocked(self):
		ok, why = rules.can_enable()
		self.assertFalse(ok)
		self.assertTrue(why)
		for name in rules.blocked_doctypes():
			self.assertIn(name, why)

	def test_clearing_the_blocker_is_what_unblocks_it(self):
		"""The one place that has to change the day the retirement path lands."""
		saved = dict(rules.DISPOSITION)
		self.addCleanup(rules.DISPOSITION.update, saved)
		for name in rules.blocked_doctypes():
			rules.DISPOSITION[name] = (rules.SURVIVES, saved[name][1])
		ok, why = rules.can_enable()
		self.assertTrue(ok)
		self.assertEqual(why, "")


class EligibilityTest(unittest.TestCase):
	#: A message that would be eligible but for the hold under test.
	OLD: ClassVar[dict] = {
		"age_days": 400,
		"retention_days": 90,
		"open_relay_jobs": 0,
		"is_deleted": False,
		"keep_tombstones": True,
		"live_replies": 0,
		"is_room_last_message": False,
		"derived_blocked": False,
	}

	def test_the_baseline_is_eligible(self):
		"""Otherwise every assertion below passes for the wrong reason."""
		self.assertEqual(rules.holds(dict(self.OLD)), set())
		self.assertTrue(rules.is_eligible(dict(self.OLD)))

	def test_a_message_inside_the_window_is_held(self):
		self.assertIn(rules.HOLD_NOT_OLD_ENOUGH, rules.holds(dict(self.OLD, age_days=10)))

	def test_zero_retention_days_holds_everything(self):
		"""0 means NEVER — the convention this form already uses. It must never become a
		'use the default' sentinel, which is the reading that would purge an entire estate
		on a site that had simply not configured retention."""
		self.assertIn(rules.HOLD_NOT_OLD_ENOUGH, rules.holds(dict(self.OLD, retention_days=0)))

	def test_an_unknown_age_is_held_rather_than_purged(self):
		self.assertIn(rules.HOLD_NOT_OLD_ENOUGH, rules.holds(dict(self.OLD, age_days=None)))

	def test_outstanding_relay_work_holds(self):
		"""A Message Delete job whose message is gone Skips silently, leaving the Chat copy
		live with nothing in ERPNext to reconcile against. Irreparable."""
		self.assertIn(rules.HOLD_RELAY_IN_FLIGHT, rules.holds(dict(self.OLD, open_relay_jobs=1)))

	def test_a_tombstone_is_held_while_keep_tombstones_is_on(self):
		self.assertIn(
			rules.HOLD_TOMBSTONE_KEPT,
			rules.holds(dict(self.OLD, is_deleted=True, keep_tombstones=True)),
		)

	def test_a_tombstone_is_not_held_when_the_setting_is_off(self):
		self.assertEqual(rules.holds(dict(self.OLD, is_deleted=True, keep_tombstones=False)), set())

	def test_a_thread_root_with_a_live_reply_is_held(self):
		self.assertIn(rules.HOLD_THREAD_ROOT, rules.holds(dict(self.OLD, live_replies=1)))

	def test_the_rooms_last_message_is_held(self):
		"""Chat Room.last_message is a Link. Purging it leaves the room list pointing at
		nothing."""
		self.assertIn(
			rules.HOLD_ROOM_LAST_MESSAGE,
			rules.holds(dict(self.OLD, is_room_last_message=True)),
		)

	def test_the_derived_blocker_holds_every_message(self):
		self.assertIn(rules.HOLD_DERIVED_BLOCKED, rules.holds(dict(self.OLD, derived_blocked=True)))

	def test_every_reason_is_returned_and_not_just_the_first(self):
		"""A message held by four rules is a different fact from one held by a boundary
		condition, and a report that stops at the first cannot say which rule binds."""
		reasons = rules.holds(
			dict(
				self.OLD,
				age_days=1,
				open_relay_jobs=3,
				live_replies=2,
				is_room_last_message=True,
			)
		)
		self.assertEqual(
			reasons,
			{
				rules.HOLD_NOT_OLD_ENOUGH,
				rules.HOLD_RELAY_IN_FLIGHT,
				rules.HOLD_THREAD_ROOT,
				rules.HOLD_ROOM_LAST_MESSAGE,
			},
		)

	def test_a_message_that_has_completed_its_retention_period_is_past_it(self):
		"""The boundary, pinned because off-by-one here is a day's worth of conversation.

		``retention_days = 90`` means *keep for 90 days*, so a message exactly 90.0 days old
		has served its term and is eligible; 89.9 has not. The predicate is
		``age < retention_days`` holds, and the direction is stated here rather than left to
		be re-derived from the comparison operator.
		"""
		self.assertEqual(rules.holds(dict(self.OLD, age_days=90, retention_days=90)), set())
		self.assertIn(
			rules.HOLD_NOT_OLD_ENOUGH,
			rules.holds(dict(self.OLD, age_days=89.9, retention_days=90)),
		)


if __name__ == "__main__":
	unittest.main()
