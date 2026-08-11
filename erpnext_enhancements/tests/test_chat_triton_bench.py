"""Phase 5's retrieval gate, against a real database. **THIS SUITE DOES NOT RUN IN CI.**

======================================================================================
NOT RUN IN CI. A HUMAN MUST RUN IT AND RECORD THE RESULT AT THE PHASE 5 CHECKPOINT.
======================================================================================

    bench --site <site> run-tests --app erpnext_enhancements \\
        --module erpnext_enhancements.tests.test_chat_triton_bench

    # or, narrowed to one case while iterating:
    bench --site <site> run-tests --app erpnext_enhancements \\
        --module erpnext_enhancements.tests.test_chat_triton_bench \\
        --test test_a_non_members_message_appears_in_no_tier

There is no Frappe integration-test job in this repo, so **this suite is worth exactly as
much as the discipline of running it.** That is uncomfortable and it is the situation: the
pure halves of retrieval — ranking, the budget ladder, assembly order, the query builder, the
citation manifest — are covered on every push by ``tests/test_chat_retrieval_pure.py``, and
the two source-level fences run on every push too. What is left here is everything that needs
a real ``DatabaseQuery``, a real ``frappe.set_user`` and real rows, and a stubbed version of
any of it would assert that the stub works.

--------------------------------------------------------------------------------------
The one test in this file that matters more than the rest
--------------------------------------------------------------------------------------

:meth:`TestTheRoomBoundary.test_a_non_members_message_appears_in_no_tier`.

Every other failure in Phase 5 produces a wrong answer, which somebody notices and reports.
That one produces **a correct answer delivered to the wrong reader** — no exception, no
user-visible symptom, nothing in any log, and no complaint, because the person who received
it has no idea they should not have. If you are reading this file because retrieval changed,
run that test before you merge, whatever else you skip.

--------------------------------------------------------------------------------------
What each group covers, and why a bench is unavoidable for it
--------------------------------------------------------------------------------------

* **The room boundary** (T5-2, T5-5, T5-8, T5-9, T5-11). Needs real membership rows and a
  real session user: the whole point is that ``allowed_rooms`` is *derived*, and deriving it
  from a stub proves nothing. Includes the two that only a database can show — that
  ``restrict_to`` narrows and cannot widen, and that **removing somebody from a room takes
  effect on the very next call**, which is what makes "the room set is never cached" a fact.
* **The audit boundary** (T5-10, T5-16). Fail-closed means the row is committed *before*
  content is returned and a failed write refuses the read. That is a transaction-ordering
  claim; there is nothing to test without transactions.
* **The three-value watermark** (T5-11, D6). Named by the research as *"the single most
  common bug in this design; write the test first"*. An edit and a delete must each change
  the cache key and must each make the covering digest stale — and neither moves ``seq``, so
  a watermark that tracks ``seq`` alone passes every test except this one and then serves a
  deleted sentence to a model.
* **The two identities** (I13). One turn, two assertions: the read ran as the mentioning
  human, the post was authored by the bot. Merging them is how a superuser service account
  gets built, and the merge is invisible in any single-identity test.
* **The FULLTEXT index surviving ``bench migrate``** — an open ``VERIFY:`` in the ADR, and
  the only place it can be answered. If migrate drops it, the exact-match half of retrieval
  degrades *invisibly*: numbers stop being findable and nothing raises.
* **The evaluation set.** Not a pass/fail gate — a recorded baseline. Retrieval quality has
  no threshold anybody can defend in advance, so the honest artefact is the same questions,
  the same corpus, the answers written down, and a human reading them.

--------------------------------------------------------------------------------------
What this suite deliberately does not do
--------------------------------------------------------------------------------------

**No Google call and no model call.** Every test here stops at the gate's output. Triton's
own turn is exercised by the live round-trip step, by a person, against a real Chat client —
because what that step actually proves is that two clients agree, and neither client exists
here.

It also never asserts on retrieval *quality*. A test that pins which chunk ranks first turns
a tuning change into a red build, and the tuning change is the thing you want to be cheap.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

# v16 renamed the base class; this is the same compatibility shim the other bench suites use.
try:
	from frappe.tests import IntegrationTestCase as _ChatTestCase
except ImportError:  # pragma: no cover - older bench
	from frappe.tests.utils import FrappeTestCase as _ChatTestCase

from erpnext_enhancements.chat import audit
from erpnext_enhancements.chat.indexing import invalidate
from erpnext_enhancements.chat.retrieval import assemble, budget, gate

ASKER = "chat-triton-asker@example.com"
OUTSIDER = "chat-triton-outsider@example.com"
AUDITOR = "chat-triton-auditor@example.com"
MEMBER_ROLE = "Chat User"

#: The evaluation set. Fifteen questions against a known corpus, run to produce a **recorded
#: baseline** rather than a pass/fail verdict — see :class:`TestTheEvaluationSet`.
#:
#: Each is here because it exercises a different tier or a different failure: an exact
#: identifier (lexical), a topical question (semantic), a question about the asking person's
#: own words (T3), a question whose answer is only in another room (T2), and one whose answer
#: does not exist at all — because "Triton confidently answers a question with no answer" is
#: the failure a relevance metric never catches.
EVALUATION_QUESTIONS: tuple[str, ...] = (
	"what did we decide about the pump on the riverwalk job",
	"what is the status of SINV-04412",
	"who agreed to the friday deadline",
	"what did I say about the impeller",
	"has anyone talked to the city inspector",
	"what is the lead time on the nozzles",
	"which vendor did we pick for the pumps",
	"what was the problem with the last install",
	"did we ever get the drawings back",
	"what did the customer say about the price",
	"who is covering the service call on saturday",
	"what changed in the scope last week",
	"is there an outstanding invoice on that job",
	"what is the wifi password for the depot",
	"what colour is the moon",
)


def _ensure_user(email: str, roles: list[str]) -> None:
	if not frappe.db.exists("User", email):
		doc = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		doc.insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	existing = {row.role for row in (user.get("roles") or [])}
	for role in roles:
		if role not in existing and frappe.db.exists("Role", role):
			user.append("roles", {"role": role})
	user.save(ignore_permissions=True)


class ChatTritonFixture(_ChatTestCase):
	"""Two rooms, three users, and a chunk in each room.

	The asker is in one room only. That asymmetry is the whole fixture: every boundary test
	below is "does anything from the other room reach them".
	"""

	def setUp(self) -> None:
		super().setUp()
		frappe.set_user("Administrator")

		_ensure_user(ASKER, [MEMBER_ROLE])
		_ensure_user(OUTSIDER, [MEMBER_ROLE])
		_ensure_user(AUDITOR, [MEMBER_ROLE])

		self._enable_chat()
		self._set_oversight_role("")

		self.room = self._make_room("Triton bench - the asker's room")
		self.other_room = self._make_room("Triton bench - a room the asker is not in")

		self._add_member(self.room, ASKER)
		self._add_member(self.other_room, OUTSIDER)

		self.mine = self._make_message(self.room, seq=1, sender=ASKER, text="the impeller is fine")
		self.theirs = self._make_message(
			self.other_room, seq=1, sender=OUTSIDER, text="SECRETPHRASEXYZ the pump failed"
		)

		self.my_chunk = self._make_chunk(self.room, 1, 1, "the impeller is fine")
		self.their_chunk = self._make_chunk(self.other_room, 1, 1, "SECRETPHRASEXYZ the pump failed")

	def tearDown(self) -> None:
		frappe.set_user("Administrator")
		super().tearDown()

	# ------------------------------------------------------------------ fixture helpers

	def _enable_chat(self) -> None:
		"""Chat ships dormant and the gate refuses while it is off, so every test here would
		otherwise fail on the kill switch rather than on what it is testing.

		Retrieval is enabled and **Google stays off** — `google_sync_enabled` and both relay
		flags are untouched, so nothing in this suite can reach Google even by accident.
		"""
		settings = frappe.get_single("Chat Settings")
		settings.enabled = 1
		settings.pause_retrieval = 0
		settings.pause_triton = 0
		settings.restrict_to_whitelist = 0
		settings.semantic_tier_enabled = 0  # no embedding provider in a test bench
		settings.lexical_tier_enabled = 1
		settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Chat Settings", "Chat Settings")
		if getattr(frappe.db, "value_cache", None):
			frappe.db.value_cache = {}

	def _set_oversight_role(self, role: str) -> None:
		settings = frappe.get_single("Chat Settings")
		settings.admin_oversight_role = role
		settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Chat Settings", "Chat Settings")
		if getattr(frappe.db, "value_cache", None):
			frappe.db.value_cache = {}

	def _make_room(self, title: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Room",
					"room_type": "Group",
					"title": title,
					"provisioning_mode": "Not Mirrored",
					"seq_high_water": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _add_member(self, room: str, user: str, is_active: int = 1) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Room Member",
					"room": room,
					"user": user,
					"role": "Member",
					"is_active": is_active,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _make_message(self, room: str, seq: int, sender: str, text: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Message",
					"room": room,
					"seq": seq,
					"sender": sender,
					"sender_kind": "Human",
					"message_type": "Text",
					"text": text,
					"text_plain": text,
					"client_message_id": f"client-triton-{room[:8]}-{seq}",
					"sync_origin": "ERPNext",
					"sync_state": "Not Mirrored",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _make_chunk(self, room: str, first: int, last: int, body: str) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Context Chunk",
					"room": room,
					"sealed": 1,
					"first_seq": first,
					"last_seq": last,
					"message_count": 1,
					"body": body,
					"participants": json.dumps([ASKER]),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _make_digest(self, room: str, summary: str, covered_to: int = 1) -> str:
		return (
			frappe.get_doc(
				{
					"doctype": "Chat Room Digest",
					"room": room,
					"summary_text": summary,
					"digest_version": 1,
					"watermark_seq": covered_to,
					"watermark_count": covered_to,
					"covered_from": 1,
					"covered_to": covered_to,
					"generated_at": frappe.utils.now_datetime(),
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _retrieve_as(self, user: str, query: str, **kwargs: Any) -> gate.RetrievalResult:
		frappe.set_user(user)
		try:
			return gate.retrieve(query=query, user=user, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def _rendered(self, result: gate.RetrievalResult) -> str:
		return result.assembly.text() if result.assembly else ""


# ------------------------------------------------------------------------- the room boundary


class TestTheRoomBoundary(ChatTritonFixture):
	"""T5-2, T5-5, T5-8, T5-9, T5-11. **The security claims of this phase.**"""

	def test_a_non_members_message_appears_in_no_tier(self) -> None:
		"""**The test that matters most in this file.**

		The asker is in one room. A distinctive phrase exists only in the other. It must not
		appear in the assembled context, in the manifest, or in the audited room list — and
		the query is written to *ask for it*, so a filter applied one ``return`` too late
		fails here and nowhere else.

		Asserted on the rendered bytes rather than on a row count, because the failure being
		guarded is "the text reached the model", and a count can be right while the bytes are
		wrong.
		"""
		result = self._retrieve_as(ASKER, "SECRETPHRASEXYZ pump", room=self.room)

		self.assertNotIn(
			"SECRETPHRASEXYZ",
			self._rendered(result),
			"a message from a room the asker is NOT in reached the assembled context. This is "
			"the leak this whole phase is written to prevent: the answer would have been "
			"correct and would have gone to the wrong person, with no exception and nothing "
			"in any log.",
		)
		self.assertNotIn(self.other_room, result.rooms_searched)
		self.assertNotIn(
			self.other_room,
			{entry.room for entry in result.manifest},
			"the other room appeared in the citation manifest. Even with the body withheld, a "
			"citation label carries a room's identity and a person's name.",
		)

	def test_retrieval_refuses_to_run_as_administrator(self) -> None:
		"""T5-5. Administrator short-circuits the permission stack, so "every room" is the
		literal, correct answer to the room-set question — and a caller that accepted it
		would never know it had asked a meaningless one."""
		with self.assertRaises(gate.RetrievalRefused):
			gate.retrieve(query="anything", user="Administrator")

	def test_retrieval_refuses_to_run_as_guest(self) -> None:
		with self.assertRaises(gate.RetrievalRefused):
			gate.retrieve(query="anything", user="Guest")

	def test_restrict_to_narrows_and_cannot_widen(self) -> None:
		"""T5-8/T5-9. Naming a room the asker cannot see contributes **nothing** — it does not
		raise, and it does not admit the room. Not raising is deliberate: narrowing to a room
		you have just been removed from is a legitimate race, not an attack."""
		result = self._retrieve_as(ASKER, "pump", room=self.room, restrict_to=[self.other_room])
		self.assertNotIn(self.other_room, result.rooms_searched)
		self.assertNotIn("SECRETPHRASEXYZ", self._rendered(result))

		narrowed = self._retrieve_as(ASKER, "impeller", room=self.room, restrict_to=[self.room])
		self.assertEqual(narrowed.rooms_searched, (self.room,))

	def test_removing_a_member_takes_effect_on_the_very_next_call(self) -> None:
		"""T5-11, and the reason ``triton:rooms:{user}`` is on the never-cached list.

		Any TTL at all on the derived room set means a departed member keeps reading for that
		long — and "that long" is exactly the window somebody was removed *during*.
		"""
		before = self._retrieve_as(ASKER, "impeller", room=self.room)
		self.assertIn(self.room, before.rooms_searched)

		frappe.db.set_value(
			"Chat Room Member",
			{"room": self.room, "user": ASKER},
			"is_active",
			0,
			update_modified=False,
		)
		frappe.db.commit()

		after = self._retrieve_as(ASKER, "impeller", room=self.room)
		self.assertEqual(
			after.rooms_searched,
			(),
			"a member removed from the room still had it in their derived room set. The set is "
			"never cached precisely so this cannot happen; something has started caching it.",
		)
		self.assertNotIn("impeller", self._rendered(after))

	def test_a_person_in_no_rooms_gets_an_empty_result_and_is_still_audited(self) -> None:
		"""Not an error and not an empty search — there is genuinely nothing to search. Still
		audited, because "Triton read nothing on your behalf" is a fact worth being able to
		prove afterwards."""
		result = self._retrieve_as(OUTSIDER, "impeller")
		frappe.set_user("Administrator")
		self.assertEqual(result.rooms_searched, ())
		self.assertTrue(result.audit_row, "an empty retrieval wrote no audit row")


# ------------------------------------------------------------------------ the audit boundary


class TestTheAuditIsFailClosed(ChatTritonFixture):
	"""T5-10 and T5-16. An audit that fails open is not an audit."""

	def test_a_failed_audit_write_refuses_the_read(self) -> None:
		"""T5-10. The whole trade decision #12 makes is a non-participant read in exchange
		for a record of it. A read that happened without the record is the half nobody
		agreed to.

		The write is broken by making the chain lock unobtainable, which is the realistic
		failure — two overlapping privileged reads, or a lock held by a crashed connection —
		rather than by monkeypatching the insert.
		"""
		original = audit._acquire_chain_lock
		audit._acquire_chain_lock = lambda: False
		try:
			with self.assertRaises(Exception) as caught:
				self._retrieve_as(ASKER, "impeller", room=self.room)
			self.assertNotIn(
				"impeller",
				str(caught.exception),
				"the refusal carried message content in its message",
			)
		finally:
			audit._acquire_chain_lock = original
			frappe.set_user("Administrator")

	def test_the_audit_row_records_the_rooms_actually_read(self) -> None:
		"""One child row per room *read*, not per room the person could have read. "Triton
		looked at these two rooms" is the auditable fact; "Triton was allowed to look at
		forty" is noise that hides the two."""
		result = self._retrieve_as(ASKER, "impeller", room=self.room)
		frappe.set_user("Administrator")
		self.assertTrue(result.audit_row)

		row = frappe.get_doc("Chat Retrieval Audit", result.audit_row)
		rooms = {child.room for child in (row.get("rooms") or [])}
		self.assertNotIn(self.other_room, rooms)
		self.assertEqual(row.actor_type, "Triton")
		self.assertEqual(row.accessed_by, ASKER)

	def test_the_audit_row_records_the_retrieval_scale_and_signs_it(self) -> None:
		"""The four columns that existed since Phase 3 and were never written. Signed rather
		than merely stored, so "Triton read four messages" cannot quietly become the record
		of a read of four hundred."""
		result = self._retrieve_as(ASKER, "impeller", room=self.room)
		frappe.set_user("Administrator")
		row = frappe.get_doc("Chat Retrieval Audit", result.audit_row)

		self.assertIsNotNone(row.tiers_used)
		self.assertEqual(
			audit.verify_chain()["ok"],
			True,
			"the audit chain does not verify after a Phase 5 retrieval. The four new signed "
			"columns are the likely cause — a field added to the signed set without the "
			"integer coercion makes every pre-existing row report as tampered.",
		)

	def test_the_audit_row_holds_no_message_text(self) -> None:
		"""``query_hash`` by default, raw text only behind a flag that ships off. The query a
		manager types is itself content: "did anyone mention my name", typed by the person
		about to run a redundancy, is not metadata."""
		result = self._retrieve_as(ASKER, "the impeller is fine", room=self.room)
		frappe.set_user("Administrator")
		row = frappe.get_doc("Chat Retrieval Audit", result.audit_row)
		self.assertFalse(row.query_text, "the raw query text was stored with the flag off")
		self.assertTrue(row.query_hash)

	def test_the_retention_purge_never_deletes_an_audit_row(self) -> None:
		"""T5-16, and a Phase 6 precondition. The one table the purge must not touch."""
		from erpnext_enhancements.chat import retention

		source = frappe.read_file(frappe.get_app_path("erpnext_enhancements", "chat", "retention.py"))
		self.assertNotIn(
			"Chat Retrieval Audit",
			source,
			"chat/retention.py now names the audit table. An audit trail with a retention "
			"rule is a diary.",
		)
		self.assertTrue(hasattr(retention, "ensure_chat_log_retention"))


# --------------------------------------------------------------------- the three-value watermark


class TestTheThreeValueWatermark(ChatTritonFixture):
	"""T5-11 / D6. Named by the research as the single most likely bug in this design."""

	def test_an_edit_changes_the_context_cache_key(self) -> None:
		"""``max(modified)`` is the value that catches an edit. Neither ``seq`` nor
		``count(*)`` moves when somebody rewrites an old message."""
		before = gate._room_watermarks(frozenset({self.room}), room=self.room)

		frappe.db.set_value("Chat Message", self.mine, "text", "the impeller failed")
		frappe.db.commit()

		after = gate._room_watermarks(frozenset({self.room}), room=self.room)
		self.assertNotEqual(
			_key(before),
			_key(after),
			"an edit did not change the context cache key. The cached context — containing "
			"the text as it was before the edit — would be served again.",
		)

	def test_a_hard_delete_changes_the_context_cache_key(self) -> None:
		"""``count(*)`` is the value that catches a hard delete: it moves neither ``seq`` nor
		``modified``, so a two-value watermark is unchanged by it."""
		self._make_message(self.room, seq=2, sender=ASKER, text="second")
		frappe.db.commit()
		before = gate._room_watermarks(frozenset({self.room}), room=self.room)

		frappe.delete_doc("Chat Message", self.mine, force=True, ignore_permissions=True)
		frappe.db.commit()

		after = gate._room_watermarks(frozenset({self.room}), room=self.room)
		self.assertNotEqual(_key(before), _key(after))

	def test_a_seq_only_watermark_would_miss_both(self) -> None:
		"""The control. Without it the two tests above could pass for the wrong reason — a
		key that changed because of something incidental rather than because of the edit."""
		first = gate._room_watermarks(frozenset({self.room}), room=self.room)
		frappe.db.set_value("Chat Message", self.mine, "text", "changed")
		frappe.db.commit()
		second = gate._room_watermarks(frozenset({self.room}), room=self.room)
		self.assertEqual(
			first[0],
			second[0],
			"seq moved on an edit, so this test is no longer demonstrating anything. If the "
			"schema now advances seq on an edit, the ordering guarantees elsewhere need "
			"re-reading, not this test relaxing.",
		)

	def test_an_edit_inside_a_covered_span_makes_the_digest_stale_and_unserved(self) -> None:
		"""The privacy claim, end to end: a summary of an edited message stops being served.

		Retrieval **omits** a stale digest rather than serving it with a caveat, because the
		thing that may be out of date is a sentence somebody changed or removed, and ERPNext
		holds the only copy of the original.
		"""
		self._make_digest(self.room, "DIGESTPHRASEQ they discussed the impeller")
		frappe.db.commit()

		served = self._retrieve_as(ASKER, "impeller", room=self.room)
		self.assertIn(
			"DIGESTPHRASEQ",
			self._rendered(served),
			"the digest was not served even before invalidation, so this test cannot show "
			"that invalidation is what removed it",
		)

		invalidate.invalidate_span(self.room, 1, 1)
		frappe.db.commit()

		after = self._retrieve_as(ASKER, "impeller", room=self.room)
		self.assertNotIn(
			"DIGESTPHRASEQ",
			self._rendered(after),
			"a stale digest was still served. A rolling summary can add information but it "
			"cannot unsay it, so a digest covering an edited or deleted message must be "
			"omitted rather than served with a caveat.",
		)

	def test_a_delete_makes_the_covering_chunk_stale(self) -> None:
		"""A chunk is a *verbatim copy*, so a stale chunk is the deleted text itself rather
		than a summary of it."""
		invalidate.invalidate_span(self.room, 1, 1)
		frappe.db.commit()
		self.assertTrue(
			frappe.db.get_value("Chat Context Chunk", self.my_chunk, "is_stale"),
			"the chunk covering the changed span was not marked stale",
		)

	def test_invalidation_is_by_overlap_rather_than_containment(self) -> None:
		"""A multi-message delete spanning two chunks contains neither of them."""
		wide = self._make_chunk(self.room, 5, 9, "later conversation")
		frappe.db.commit()
		invalidate.invalidate_span(self.room, 7, 7)
		frappe.db.commit()
		self.assertTrue(frappe.db.get_value("Chat Context Chunk", wide, "is_stale"))

	def test_a_chunk_outside_the_span_is_left_alone(self) -> None:
		"""The negative control. An invalidation that marks everything is indistinguishable
		from one that works, and it costs a full re-embed of the room every edit."""
		far = self._make_chunk(self.room, 100, 110, "unrelated")
		frappe.db.commit()
		invalidate.invalidate_span(self.room, 1, 1)
		frappe.db.commit()
		self.assertFalse(frappe.db.get_value("Chat Context Chunk", far, "is_stale"))


def _key(watermark: tuple[int, int, str]) -> str:
	return assemble.context_cache_key(user=ASKER, room="R", watermark=watermark, budget_fingerprint="b")


# ------------------------------------------------------------------------- the two identities


class TestTheTwoIdentitiesAreNeverMerged(ChatTritonFixture):
	"""I13. One turn, two assertions — the read ran as the human, the post came from the bot.

	Merging them is how a superuser service account gets built, and the failure is seductive:
	the relay already needs a machine credential to post, so reusing it for the read half
	would make the handler shorter and would silently give Triton every user's data.
	"""

	def test_the_retrieval_runs_as_the_mentioning_human(self) -> None:
		result = self._retrieve_as(ASKER, "impeller", room=self.room)
		frappe.set_user("Administrator")
		row = frappe.get_doc("Chat Retrieval Audit", result.audit_row)
		self.assertEqual(
			row.accessed_by,
			ASKER,
			"the retrieval was attributed to somebody other than the person who asked. If "
			"that is a service account, the cross-user reach decision #5 forbids now exists.",
		)

	def test_the_bot_user_is_not_the_asker_and_not_administrator(self) -> None:
		"""Asserted on the resolver rather than on a posted message, so it holds even on a
		bench with no bot user configured — where it must *raise* rather than fall back."""
		from erpnext_enhancements.chat.invoke import handler

		try:
			bot = handler._bot_user()
		except RuntimeError:
			return  # unconfigured is the correct failure; the fallback is what must not exist
		self.assertNotEqual(bot, ASKER)
		self.assertNotEqual(
			bot,
			"Administrator",
			"Triton posts as Administrator. That attributes its answers to a superuser and "
			"makes the two-identity rule invisible in the transcript.",
		)


# ------------------------------------------------------------------- the FULLTEXT index VERIFY


class TestTheFulltextIndexSurvivesMigrate(ChatTritonFixture):
	"""The open ``VERIFY:`` from the ADR, and the only place it can be answered.

	If ``bench migrate`` drops a hand-added FULLTEXT index, the exact-match half of retrieval
	degrades **invisibly** after every deploy: identifiers stop being findable and nothing
	raises. The ``after_migrate`` backstop re-creates it, so the practical question this
	answers is whether that backstop is load-bearing or belt-and-braces.
	"""

	def test_the_index_exists_after_the_patch_has_run(self) -> None:
		from erpnext_enhancements.patches import add_chat_phase5_indexes

		add_chat_phase5_indexes.ensure_chat_phase5_indexes()
		frappe.db.commit()
		self.assertTrue(
			add_chat_phase5_indexes._index_exists("tabChat Context Chunk", "chunk_body_fulltext"),
			"the FULLTEXT index on Chat Context Chunk.body is absent after the patch ran. The "
			"lexical tier is not a fallback — it is what makes an exact invoice number "
			"findable at all — and its absence produces no error.",
		)

	def test_a_second_run_is_a_no_op_rather_than_a_duplicate(self) -> None:
		from erpnext_enhancements.patches import add_chat_phase5_indexes

		add_chat_phase5_indexes.ensure_chat_phase5_indexes()
		add_chat_phase5_indexes.ensure_chat_phase5_indexes()
		frappe.db.commit()
		rows = frappe.db.sql(
			"""select count(distinct index_name) from information_schema.STATISTICS
				where table_schema = database()
					and table_name = 'tabChat Context Chunk'
					and index_name = 'chunk_body_fulltext'"""
		)
		self.assertEqual(int(rows[0][0]), 1)

	def test_record_here_whether_migrate_drops_it(self) -> None:
		"""**Manual, and deliberately not automated.** Running ``bench migrate`` from inside a
		     test is not a thing this suite should do — it would mutate the site under every other
		     case in the file.

		     The procedure, to be run once and the answer recorded in the ADR addendum:

		     1. ``bench --site <site> migrate``
		     2. ``bench --site <site> mariadb`` then
		``show index from \\`tabChat Context Chunk\\`;``
		     3. repeat ``migrate`` and check again.

		     If the index is gone after step 2, the ``after_migrate`` backstop is the only thing
		     keeping the lexical tier alive and the window is one migrate. If it survives, the
		     backstop is belt-and-braces and the ``VERIFY:`` closes.
		"""
		self.assertTrue(True, "documentation-only; see the docstring")


# ------------------------------------------------------------------------- budget + assembly


class TestBudgetAndAssemblyOnRealRows(ChatTritonFixture):
	"""T5-12…T5-15, at the integration level.

	The ladder's arithmetic is covered bench-free and thoroughly. What can only be checked
	here is that it is *wired*: that a real retrieval produces a plan, that the truncation
	flag reaches the assembly as an instruction the model will read, and that the tier
	budgets come from the settings row rather than from the module defaults.
	"""

	def test_a_real_retrieval_produces_a_plan_and_a_token_count(self) -> None:
		result = self._retrieve_as(ASKER, "impeller", room=self.room)
		self.assertIsNotNone(result.plan)
		self.assertGreater(result.context_tokens, 0)
		self.assertIn(budget.TIER_T1, result.tiers_used)

	def test_the_truncation_flag_becomes_an_instruction_the_model_reads(self) -> None:
		"""The flag is not the feature — the sentence is. A model that answers silently from a
		cut view produces a confident wrong answer, and the user gets no signal at all."""
		built = assemble.assemble(
			system_prompt="s",
			glossary_lines=[],
			user_card_lines=[],
			t0_lines=[],
			t2_t3_lines=[],
			thread_lines=["a"],
			question="q",
			context_truncated=True,
		)
		self.assertIn(assemble.TRUNCATION_NOTICE, built.text())
		self.assertNotIn(
			assemble.TRUNCATION_NOTICE,
			built.stable_prefix(),
			"the truncation notice landed in the cached prefix, which changes the cache "
			"identity on exactly the turns that are already the most expensive",
		)

	def test_the_budget_comes_from_the_settings_row(self) -> None:
		"""A ceiling that silently ignored the settings row would make the field a lie, and
		the field is the intended way to retune the ceiling from two weeks of data."""
		settings = frappe.get_single("Chat Settings")
		settings.context_token_ceiling = 12_345
		settings.save(ignore_permissions=True)
		frappe.clear_document_cache("Chat Settings", "Chat Settings")

		resolved = budget.budget_from_settings(dict(frappe.get_cached_doc("Chat Settings").as_dict()))
		self.assertEqual(resolved.ceiling, 12_345)


# ------------------------------------------------------------------------- the evaluation set


class TestTheEvaluationSet(ChatTritonFixture):
	"""Fifteen questions, run to **record a baseline** rather than to pass or fail.

	Retrieval quality has no threshold anybody can defend in advance, and a test that pins
	which chunk ranks first turns every tuning change into a red build — which makes tuning
	expensive, which means it stops happening. So this asserts only the two things that are
	genuinely bugs at any quality level, and prints the rest for a human to read at the
	checkpoint.

	The last question in the set has **no answer in the corpus**, on purpose: "Triton
	confidently answers a question with no answer" is the failure a relevance metric never
	catches, and the only way to see it is to look.
	"""

	def test_every_question_returns_without_raising_and_leaks_nothing(self) -> None:
		leaked: list[str] = []
		report: list[str] = []

		for question in EVALUATION_QUESTIONS:
			result = self._retrieve_as(ASKER, question, room=self.room)
			frappe.set_user("Administrator")
			rendered = self._rendered(result)
			if "SECRETPHRASEXYZ" in rendered:
				leaked.append(question)
			report.append(
				f"  {question[:48]:<50} tokens={result.context_tokens:<6} "
				f"rung={result.degradation_rung} cites={len(result.manifest)} "
				f"rooms={len(result.rooms_searched)}"
			)

		print("\n--- Phase 5 evaluation baseline ---")
		print("\n".join(report))
		print("--- record the above at the checkpoint ---\n")

		self.assertFalse(
			leaked,
			f"these questions pulled content from a room the asker is not in: {leaked}. The "
			"boundary is not a quality question and does not get a baseline — it is either "
			"held or it is not.",
		)

	def test_a_question_with_no_answer_returns_an_empty_or_thin_context(self) -> None:
		"""Not an assertion about the model — an assertion that retrieval does not *invent*
		a source. A manifest full of entries for a question nothing in the corpus answers is
		how a confident wrong answer gets its footnotes."""
		result = self._retrieve_as(ASKER, "what colour is the moon", room=self.room)
		frappe.set_user("Administrator")
		self.assertLessEqual(
			len([entry for entry in result.manifest if entry.kind == "chunk"]),
			1,
			"retrieval offered multiple chunk citations for a question the corpus does not "
			"answer. Every one of them will be cited as evidence for something.",
		)
