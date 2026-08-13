# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Phase 2 checkpoint gate: a scripted 200-message two-way soak, with printed assertions.

Two entry points, **one** implementation
========================================

* :func:`run` — ``bench execute erpnext_enhancements.chat.testing.soak.run``. Real Frappe,
  real database, the :class:`~erpnext_enhancements.chat.testing.fake_chat.FakeChatAPI` on the
  Google side.
* :func:`run_soak` driven by a bench-free harness — ``erpnext_enhancements/tests/
  test_chat_soak.py`` supplies the in-memory ``frappe`` and CI runs it on every push.

Everything that decides *what happens* and *what is asserted* lives in this module. The two
entry points differ only in where the rows are stored. That is the whole reason the harness is
an injected object rather than a subclass: a soak whose bench-free half asserted different
things from its bench half would prove nothing about either.

Why the Google side is always the fake, even on a bench
-------------------------------------------------------
The soak has to make Google fail on demand — a 60-second outage, a lease abandoned mid-relay,
an event delivered before the response that caused it. None of that is reachable against the
real API, and a soak that skipped it would pass on the happy path, which is the one path this
phase was never worried about. ``PHASE_2 §9`` asks separately for a live ~20-message round
trip; that is :mod:`erpnext_enhancements.chat.gchat.smoke_test`'s job, not this module's.

The composition, from ``PHASE_2 §9``
-------------------------------------
Two users, one 1:1 DM and one group space. 200 messages: 120 (60%) composed in ERPNext through
the write path a SPA would use, 80 (40%) typed in Google Chat and arriving through the inbound
pipeline. 10 edits and 10 deletes split across both origins. 5 attachments, including a
Drive-sourced inbound one and a deliberately oversized one. Three bursts of ten messages into
one space inside two seconds. A 60-second relay outage mid-run. Every tenth inbound event
delivered twice. An ``updated``-before-``created`` pair and a ``deleted``-before-``created``
pair. One worker killed mid-relay, simulated by abandoning a lease — never by signalling a
process, because a test that SIGKILLs something cannot assert what happened next.

The assertion that matters most
--------------------------------
``notify_new_message`` fires **exactly once per genuinely new message and zero times for
echoes, replays and reconciliations**. Both bugs this phase's reviews caught were
*suppressions* — a real coworker message consumed while believed to be our own echo — so the
report prints **arrival counts per origin**, not merely an absence of duplicates. A soak that
counted duplicates alone would have passed with both bugs present, which is the specific
failure this file exists to make impossible.

Honesty rules this module enforces on itself
---------------------------------------------
Every check prints ``PASS``, ``FAIL`` or ``SKIPPED`` with its actual and expected numbers side
by side. A check the harness cannot evaluate prints ``SKIPPED`` **and says what would evaluate
it**. Nothing is silently omitted and nothing prints ``PASS`` on an assertion that did not run
— a soak that reports success on work it did not do is worse than one that reports failure.

Imports
-------
Module scope is standard library only, per ``PHASE2_INTERFACES.md`` §9: the bench-free CI tier
installs no ``frappe``, and this module has to be importable there so the harness can install
its stub *before* the ``sync`` package is pulled in. Every frappe-touching import is inside a
function.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------------------
# Composition — the numbers from PHASE_2 §9, named so the report can print them
# --------------------------------------------------------------------------------------

#: Total messages the run produces, across both origins and both rooms.
TOTAL_MESSAGES: int = 200

#: ~60% composed in ERPNext. Split explicitly rather than computed from a percentage so the
#: two halves always sum to :data:`TOTAL_MESSAGES` and nobody has to trust a rounding rule.
ERPNEXT_MESSAGES: int = 120

#: ~40% typed in the native Google Chat client.
GOOGLE_MESSAGES: int = 80

#: Edits and deletes, split evenly across the two origins. They mutate messages that are
#: already part of the 200 — they never add to the count.
EDITS_PER_ORIGIN: int = 5
DELETES_PER_ORIGIN: int = 5

#: Three bursts of ten into the group space, inside two seconds of the fake clock. Verification
#: check 12 from the GCP runbook: all ten must arrive, in order, with the worker having backed
#: off — not eight successes and two exceptions.
BURSTS: int = 3
BURST_SIZE: int = 10
BURST_WINDOW_MS: int = 2_000

#: The deliberate mid-run relay outage. The kill switch rather than injected 503s, because a
#: kill switch is the only safe response to a live echo storm and its drain-in-order behaviour
#: is the thing an operator will actually depend on at 2am.
OUTAGE_SECONDS: int = 60

#: Every tenth *delivered* inbound event is delivered twice — Pub/Sub is at-least-once and the
#: duplicate carries the same ``messageId`` with a new ``ackId``, exactly as a redelivery does.
DUPLICATE_EVERY: int = 10

#: Five attachments: two outbound from ERPNext (one of which is oversized and must be refused),
#: one inbound ``UPLOADED_CONTENT``, one inbound ``DRIVE_FILE`` stored as a reference, and one
#: inbound oversized which must be recorded as skipped rather than dropped.
ATTACHMENTS: int = 5

#: A small ceiling, set into ``Chat Settings.attachment_byte_limit`` for the run. Allocating a
#: genuinely 200 MB buffer to prove a rejection would make CI slow and prove nothing extra —
#: the code path is the same one, and the ceiling is a setting precisely so it can be lowered.
SOAK_ATTACHMENT_BYTE_LIMIT: int = 4_096

#: The oversized attachment, comfortably over that ceiling.
OVERSIZED_ATTACHMENT_BYTES: int = 8_192

ALICE = "alice@sapphirefountains.com"
BOB = "bob@sapphirefountains.com"

ROOM_DM = "SOAK-ROOM-DM"
ROOM_GROUP = "SOAK-ROOM-GRP"

#: A message row is settled once its projection is anything but ``Pending``.
NON_TERMINAL_MESSAGE_STATES: frozenset[str] = frozenset({"Pending", ""})

#: Messages that are already deleted in Chat by the time ERPNext ingests them. Exactly one, the
#: ``deleted``-before-``created`` pair from :meth:`SoakDriver._phase_out_of_order`.
#:
#: It is **stored** — ERPNext is the record that the message existed — and it is stored already
#: tombstoned, so it notifies nobody: telling somebody about a message that is already deleted is
#: worse than not telling them. That is why the notification count below is
#: :data:`NOTIFIABLE_MESSAGES` and not :data:`TOTAL_MESSAGES`, and the two constants exist
#: separately so the difference has to be *stated* rather than absorbed into a smaller number
#: that nobody can account for.
DELETED_BEFORE_INGEST: int = 1

#: The number of the 200 that a human should hear about. Every other message notifies exactly
#: once, and echoes, redeliveries, replays and reconciliations notify zero times.
NOTIFIABLE_MESSAGES: int = TOTAL_MESSAGES - DELETED_BEFORE_INGEST

PASS = "PASS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

#: **Four checks, one missing line, and it is quoted here in full.** Everything else the
#: inbound attachment path needs is built, tested and verified end to end: the fake produces a
#: real ``Message.attachment[]`` in both source shapes, ``attachments.ingest_message_attachments``
#: parses it and writes the rows, ``download_attachment`` fetches the bytes against both read
#: buckets, and the two ceilings and the 403 land ``Skipped``/``Failed`` without touching the
#: message. What is missing is the **call**, in ``chat/sync/inbound.py`` — a file the attachment
#: work did not own.
#:
#: This run was executed with the call temporarily in place: all four checks pass, the row set
#: is ``[('ERPNext', 'Stored'), ('ERPNext', 'Skipped'), ('Uploaded', 'Stored'),
#: ('Drive Link', 'Linked'), ('Uploaded', 'Skipped')]``, and ``replay: attachments unchanged``
#: stays at 5. So this is a wiring gap with a known, measured fix and not an open question.
#:
#: Insert into ``inbound._apply_created``, immediately before ``_touch_room(room, created_utc)``
#: — after the row exists and its revision is written, before the room watermark moves::
#:
#:     attachments_module = _attachments_attr("ingest_message_attachments")
#:     if attachments_module is not None:
#:         attachments_module(doc.name, resource)
#:
#: …with a late-bound accessor beside :func:`_outbox_attr`, for the same reason that one
#: exists. ``_apply_updated`` needs nothing: it already delegates an ``updated``-for-an-unknown
#: -name to ``_apply_created``, so Rule 1 is covered by the single call. Adding a second one on
#: the applied-edit branch is safe (the ingest is idempotent on ``unique(gchat_attachment_name)``)
#: and buys only convergence for an attachment that was absent when the create was ingested.
#: Then delete these four entries in the same change, or this suite goes red the other way.
_INBOUND_ATTACHMENT_GAP = (
	"Inbound attachments are never ingested: `chat/sync/inbound.py` contains no reference to the "
	"attachments module. Everything downstream exists and is verified — the fake emits a real "
	"attachment[] in both source shapes, ingest_message_attachments parses it, download_attachment "
	"fetches the bytes and charges both read buckets, and the ceilings and the 403 land Skipped / "
	"Failed without losing the message. Fix: call attachments.ingest_message_attachments(doc.name, "
	"resource) from inbound._apply_created, immediately before _touch_room(room, created_utc), "
	"through a late-bound accessor beside _outbox_attr. _apply_updated needs no second call — it "
	"already delegates an updated-for-unknown-name to _apply_created. Verified with that line in "
	"place: all four of these pass, rows are ERPNext/Stored, ERPNext/Skipped, Uploaded/Stored, "
	"Drive Link/Linked, Uploaded/Skipped, and the replay leaves all five untouched. Delete these "
	"four entries in the same change that adds the call."
)

#: Checks that **fail today because a line is missing in the engine, not because the soak is
#: wrong.** Each value names the missing wiring precisely enough to be actioned without this
#: file open.
#:
#: This exists so the gate can report the truth (``FAIL``) while CI stays green on a branch
#: that does not own the fix. It cannot rot: the suite asserts the failing set is **exactly**
#: this set, so a new failure reddens CI and so does *fixing* one of these without deleting
#: its entry.
#: **Empty, and that is the state to keep it in.** All seven entries this dict carried on
#: 2026-08-09 were closed the same day: ERPNext-origin edits and deletes writing no revision
#: row, a ``deleted``-before-``created`` never converging, and the four inbound-attachment
#: checks. An entry here is a debt with a due date, not a permanent exemption.
KNOWN_GAPS: dict[str, str] = {}


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


@dataclass
class Check:
	"""One printed line of the gate.

	``actual`` and ``expected`` are printed side by side **always**, including on a pass. The
	brief is explicit about why: "print the number and the expected number side by side, never
	just OK". A green tick tells a reviewer nothing about whether the assertion was strong.
	"""

	name: str
	status: str
	actual: Any = ""
	expected: Any = ""
	note: str = ""

	@classmethod
	def compare(cls, name: str, actual: Any, expected: Any, *, note: str = "") -> Check:
		return cls(name, PASS if actual == expected else FAIL, actual, expected, note)

	@classmethod
	def at_least(cls, name: str, actual: int, minimum: int, *, note: str = "") -> Check:
		"""For the handful of facts whose correct value is a floor rather than a number.

		Used sparingly and never for a count the design pins exactly — "at least one row" is
		how a weak assertion gets past review. The one legitimate case here is the echo ledger:
		how a returning message is absorbed (as an ``ECHO`` on its client id, or structurally as
		a ``DUPLICATE`` because the relay's write-back won the race) depends on timing that the
		design deliberately does not fix. That *both* mechanisms fire, and that between them
		they account for every message we sent, is the property; which one fired is not.
		"""
		return cls(
			name, PASS if int(actual) >= int(minimum) else FAIL, int(actual), f">= {int(minimum)}", note
		)

	@classmethod
	def skipped(cls, name: str, *, note: str, expected: Any = "") -> Check:
		"""A check the harness could not evaluate. ``note`` must say what *would* evaluate it."""
		return cls(name, SKIPPED, "not evaluated", expected, note)


@dataclass
class SoakReport:
	"""Everything the run produced. :meth:`render` is the thirty-second block."""

	title: str = "chat two-way soak"
	checks: list[Check] = field(default_factory=list)
	facts: dict[str, Any] = field(default_factory=dict)
	counters: dict[str, int] = field(default_factory=dict)

	def add(self, check: Check) -> Check:
		self.checks.append(check)
		return check

	@property
	def failed(self) -> list[Check]:
		return [c for c in self.checks if c.status == FAIL]

	@property
	def regressions(self) -> list[Check]:
		"""Failures that are **not** a recorded gap. These are the ones that must never exist."""
		return [c for c in self.failed if c.name not in KNOWN_GAPS]

	@property
	def known_gaps_hit(self) -> list[Check]:
		return [c for c in self.failed if c.name in KNOWN_GAPS]

	@property
	def skipped(self) -> list[Check]:
		return [c for c in self.checks if c.status == SKIPPED]

	@property
	def ok(self) -> bool:
		"""A skipped check is **not** a pass. The gate is green only when nothing is unknown."""
		return not self.failed and not self.skipped

	def render(self) -> str:
		"""One line per check, then a legend. **Thirty seconds is the design constraint.**

		Gap explanations are numbered and printed once at the bottom rather than repeated under
		every check that shares a root cause — six checks quoting the same four-sentence
		paragraph is how a block that a human was supposed to read becomes one they scroll past.
		"""
		width = max((len(c.name) for c in self.checks), default=10)
		lines = [f"=== {self.title} " + "=" * max(4, 76 - len(self.title))]
		legend: list[str] = []
		for check in self.checks:
			is_gap = check.status == FAIL and check.name in KNOWN_GAPS
			marker = "FAIL*  " if is_gap else f"{check.status:<7}"
			lines.append(
				f"[{marker}] {check.name.ljust(width)}  actual={check.actual!r:<28} "
				f"expected={check.expected!r}"
			)
			if check.note:
				lines.append(f"{' ' * 10}  -> {check.note}")
			if is_gap:
				explanation = KNOWN_GAPS[check.name]
				if explanation not in legend:
					legend.append(explanation)
				lines.append(f"{' ' * 10}  -> KNOWN GAP #{legend.index(explanation) + 1} (see below)")
		lines.append("-" * 80)
		lines.append(
			f"{len(self.checks)} checks · {len(self.checks) - len(self.failed) - len(self.skipped)} pass"
			f" · {len(self.regressions)} fail · {len(self.known_gaps_hit)} fail-known-gap (marked FAIL*)"
			f" · {len(self.skipped)} skipped"
		)
		for index, explanation in enumerate(legend, start=1):
			lines.append(f"KNOWN GAP #{index}: {explanation}")
		if self.facts:
			lines.append("facts: " + json.dumps(self.facts, default=str, sort_keys=True))
		if self.counters:
			lines.append("seam counters: " + json.dumps(self.counters, sort_keys=True))
		return "\n".join(lines)


# --------------------------------------------------------------------------------------
# The scenario
# --------------------------------------------------------------------------------------


@dataclass
class Ledger:
	"""What the scenario *sent*, so arrival can be checked against intent rather than output.

	This is the anti-suppression instrument. Counting rows tells you nothing about a message
	that was eaten: the row simply is not there and neither is any complaint. Recording every
	send at the moment it is made, keyed by an identity the other side also knows, is what
	turns "no duplicates" into "no losses either".
	"""

	#: ERPNext-composed message → the ``Chat Message`` name. Arrival is measured in Chat.
	erpnext_sent: list[str] = field(default_factory=list)
	#: Chat-typed message → the Google resource name. Arrival is measured in ERPNext.
	google_sent: list[str] = field(default_factory=list)
	#: Rooms, in the order the scenario used them, for the per-room ordering check.
	rooms: dict[str, str] = field(default_factory=dict)
	#: ``Chat Message`` names edited locally, and resource names edited in Chat.
	erpnext_edits: list[str] = field(default_factory=list)
	google_edits: list[str] = field(default_factory=list)
	erpnext_deletes: list[str] = field(default_factory=list)
	google_deletes: list[str] = field(default_factory=list)
	#: Bookkeeping the checks read back.
	events_delivered: int = 0
	events_duplicated: int = 0
	burst_waits_ms: int = 0
	outage_backlog: int = 0
	lease_abandoned: str = ""
	oversized_outbound_refused: bool = False
	out_of_order_updated_first: str = ""
	out_of_order_deleted_first: str = ""
	notes: list[str] = field(default_factory=list)


class SoakDriver:
	"""Runs the scenario against a harness. **The harness contract is duck-typed and small.**

	Required attributes:

	``fake``                        a :class:`FakeChatAPI`
	``clock``                       its :class:`FakeClock`
	``settings``                    the ``Chat Settings`` object, mutable by attribute
	``limiter_waits_ms``            total time the per-space bucket made the worker wait
	``oversized_outbound_refused``  whether exactly one ``media.upload`` crossed the wire

	Required methods:

	``build_client(subject)``            a ``GoogleChatClient`` over ``fake``
	``rows(doctype)``                    every row as a plain dict, copied
	``set_row(doctype, name, **values)`` a raw field write, used only by the replay reset
	``seed_room(room, space, room_type, members)``
	``compose(room, sender, text)``      insert through the real ERPNext write path → name
	``local_edit(message, text)``        drive the real ``on_update`` edit path
	``local_delete(message)``            drive the real ``on_update`` soft-delete path
	``attach_file(message, name, data)`` a private ``File`` on a message row → File name
	``decorate_resource(name, patch)``   add fields to a fetched ``Message`` (attachments only)
	``seed_media(data_ref, blob)``       register bytes behind a ``media.download`` ref
	``commit()``
	``advance(ms)``                      move the clock, and site-local ``now`` with it

	Everything else — what to send, in what order, and what to assert — is here.
	"""

	def __init__(self, harness: Any) -> None:
		self.h = harness
		self.ledger = Ledger()
		# Imported here, never at module scope: the bench-free harness installs its `frappe`
		# stub first and these modules all `import frappe` at their own module scope.
		from erpnext_enhancements.chat import seams
		from erpnext_enhancements.chat.sync import inbound, outbound, pubsub

		self.seams = seams
		self.inbound = inbound
		self.outbound = outbound
		self.pubsub = pubsub
		self._delivery_seq = 0

	# -- plumbing --------------------------------------------------------------------

	def _next_pubsub_id(self) -> str:
		"""A fresh Pub/Sub ``messageId`` per **delivery**, not per event.

		``unique(pubsub_message_id)`` is the entire duplicate defence, so two genuinely
		different events sharing an id would be silently read as a redelivery and one of them
		would vanish — which is the exact bug class this soak exists to catch, arriving via
		the test harness instead of the code.
		"""
		self._delivery_seq += 1
		return f"soak-pubsub-{self._delivery_seq:06d}"

	def deliver(self, envelope: dict[str, Any], *, client: Any) -> str:
		"""One Pub/Sub delivery: ingest the raw row, then process it. Returns the verdict."""
		message = dict(envelope["message"])
		message["messageId"] = self._next_pubsub_id()
		name, created = self.pubsub.ingest_delivery(
			{"ackId": f"ack-{message['messageId']}", "message": message}
		)
		self.ledger.events_delivered += 1
		if not created:
			return "duplicate-delivery"
		return self.inbound.process_inbound_event(name, client=client)

	def pump(self, *, client: Any, duplicate: bool = True) -> list[str]:
		"""Drain every pending Workspace Event and process it, duplicating every tenth.

		The duplicate is a genuine redelivery — same envelope, same ``ce-id``, new Pub/Sub
		``messageId`` — because that is what Pub/Sub actually does when an ack is lost. A
		harness that re-used the Pub/Sub id would be testing the raw-event unique index
		instead of the pipeline's own idempotency, and the two are different claims.
		"""
		verdicts: list[str] = []
		for envelope in self.h.fake.drain_events():
			verdicts.append(self.deliver(envelope, client=client))
			if duplicate and self.ledger.events_delivered % DUPLICATE_EVERY == 0:
				self.ledger.events_duplicated += 1
				verdicts.append(self.deliver(envelope, client=client))
		self.drain_inbound_backlog(client=client)
		return verdicts

	def drain_inbound_backlog(self, *, client: Any, passes: int = 60) -> None:
		"""Re-drive every raw event still ``Received``. **This is the sweeper, run in line.**

		It is needed because the read bucket is switched **on** for this run: one
		``messages.get`` per inbound event against 15 reads/second/space means a batch of
		eighty events cannot all resolve in the same instant, and the ones that 429 are left
		``Received`` for the sweeper by design. A soak that turned the read bucket off to avoid
		this would delete the only pressure the inbound half is under, and would then report
		PASS on a pipeline that has never met its own quota.

		Each pass buys a second of read budget. The bound is finite so a genuinely stuck row
		fails an assertion instead of hanging CI.
		"""
		for _ in range(passes):
			pending = [
				row for row in self.h.rows("Chat Inbound Event") if str(row.get("status") or "") == "Received"
			]
			if not pending:
				return
			self.h.advance(1_000)
			for row in pending:
				self.inbound.process_inbound_event(str(row["name"]), client=client)
			self.h.commit()
		self.ledger.notes.append("inbound backlog still had Received rows after the pass bound")

	def drain_relay(self, *rooms: str) -> None:
		"""Run the outbound worker for each room until its FIFO is empty.

		Looped rather than called once: ``drain_room`` stops at the head of a deferred line, and
		the soak deliberately creates deferrals (a full token bucket, a reaped lease). The bound
		is generous and finite — an unbounded loop here would turn a relay bug into a hung CI
		job instead of a failed assertion.
		"""
		for room in rooms:
			for _ in range(64):
				summary = self.outbound.drain_room(room)
				if not summary.get("claimed"):
					break
				self.h.commit()

	def _native_call(
		self, method: str, path: str, *, sender: str, params: dict[str, str], body: dict[str, Any] | None
	) -> dict[str, Any]:
		"""One call as a coworker's **native** Chat client would make it.

		Deliberately *not* through ``GoogleChatClient``: a human in the Chat app sends no
		``messageId`` and no ``requestId``, and routing these through our own builders — which
		require both — would manufacture messages carrying our client id, i.e. messages that
		look like echoes. The whole point of the 80 is that they do not.

		The per-space one-write-per-second bucket is **shared across every Chat app acting in
		the space**, our relay included, so a human typing while the relay is writing genuinely
		does get throttled. A 429 here is answered by advancing the clock, which is the
		harness's way of saying "they typed a second later" — the alternative, disabling the
		bucket for arrangement, would quietly remove the pressure the soak exists to apply.
		"""
		url = f"https://fake{path}"
		for _ in range(8):
			response = self.h.fake.request(
				method,
				url,
				params=params,
				json=body,
				headers={"Authorization": f"Bearer {_token_for(sender)}"},
			)
			if response.status_code == 429:
				self.h.advance(1_000)
				continue
			if response.status_code != 200:
				raise AssertionError(
					f"the fake refused a native-client {method} {path}: {response.status_code} {response.text[:200]}"
				)
			# A second passes before anybody types again. Without it the relay's very next write
			# meets a bucket the human just emptied, 429s three times inside one frozen
			# millisecond and dead-letters a message for a reason that exists only in the
			# harness. Our Redis bucket cannot see the native client's writes — that asymmetry
			# is real (`PHASE2_VERIFIED.md` §2) and backoff is what covers it — but backoff
			# needs a clock that moves.
			self.h.advance(1_000)
			return dict(json.loads(response.text or "{}"))
		raise AssertionError(f"the native client could not get past the space bucket for {path}")

	def chat_post(self, space: str, *, sender: str, text: str) -> str:
		payload = self._native_call(
			"POST", f"/v1/{space}/messages", sender=sender, params={}, body={"text": text}
		)
		name = str(payload["name"])
		self.ledger.google_sent.append(name)
		return name

	def chat_patch(self, resource: str, *, sender: str, text: str) -> None:
		"""An edit made in the native client — ``messages.patch`` with ``updateMask=text``."""
		self._native_call(
			"PATCH", f"/v1/{resource}", sender=sender, params={"updateMask": "text"}, body={"text": text}
		)

	def chat_delete(self, resource: str, *, sender: str) -> None:
		self._native_call("DELETE", f"/v1/{resource}", sender=sender, params={"force": "true"}, body=None)

	# -- the run ---------------------------------------------------------------------

	def run(self) -> Ledger:
		"""The whole scenario, in order. Each phase is one paragraph of ``PHASE_2 §9``."""
		self._phase_setup()
		client = self.h.build_client(ALICE)

		self._phase_conversation(client)
		self._phase_bursts(client)
		self._phase_outage(client)
		self._phase_abandoned_lease(client)
		self._phase_out_of_order(client)
		self._phase_edits_and_deletes(client)
		self._phase_attachments(client)
		self._phase_settle(client)
		return self.ledger

	def _phase_setup(self) -> None:
		fake = self.h.fake
		dm_space = fake.seed_space(
			display_name="", space_type="DIRECT_MESSAGE", members=[ALICE, BOB], creator=ALICE
		)
		group_space = fake.seed_space(display_name="Soak Group", members=[ALICE, BOB], creator=ALICE)
		self.h.seed_room(ROOM_DM, space=dm_space, room_type="Direct", members=(ALICE, BOB))
		self.h.seed_room(ROOM_GROUP, space=group_space, room_type="Group", members=(ALICE, BOB))
		self.ledger.rooms = {ROOM_DM: dm_space, ROOM_GROUP: group_space}

		self.h.settings.attachment_byte_limit = SOAK_ATTACHMENT_BYTE_LIMIT
		self.h.settings.mirror_attachments = 1

	def _phase_conversation(self, client: Any) -> None:
		"""The bulk of the traffic: alternating origins across both rooms.

		Relay and pump run every few messages rather than once at the end, because the property
		under test is what happens when the two directions are *interleaved* — an echo arriving
		while another message's relay is still open is the §4.D race, and batching the whole run
		into "send everything, then sync everything" would never produce it.
		"""
		erpnext_budget = ERPNEXT_MESSAGES - (BURSTS * BURST_SIZE) - _OUTAGE_MESSAGES - _LATE_ERPNEXT_MESSAGES
		google_budget = GOOGLE_MESSAGES - _OUT_OF_ORDER_MESSAGES - _ATTACHMENT_INBOUND_MESSAGES

		for index in range(max(erpnext_budget, google_budget)):
			room = ROOM_DM if index % 2 else ROOM_GROUP
			space = self.ledger.rooms[room]
			if index < erpnext_budget:
				sender = ALICE if index % 2 else BOB
				self.ledger.erpnext_sent.append(
					self.h.compose(room=room, sender=sender, text=f"erpnext #{index} in {room}")
				)
			if index < google_budget:
				self.chat_post(space, sender=BOB if index % 2 else ALICE, text=f"chat #{index} in {room}")

			if index % 5 == 4:
				self.drain_relay(ROOM_GROUP, ROOM_DM)
				self.pump(client=client)

		self.drain_relay(ROOM_GROUP, ROOM_DM)
		self.pump(client=client)

	def _phase_bursts(self, client: Any) -> None:
		"""Three bursts of ten into the group space inside two seconds of the fake clock.

		The assertion is not "no exception" — it is that all ten land, in ``seq`` order, having
		*waited*. The limiter double runs the same GCRA arithmetic the Lua script deploys and
		models ``block=True`` by advancing the clock, so "the worker backed off for nine
		seconds" is a number the report can print instead of a stopwatch reading.
		"""
		room = ROOM_GROUP
		for burst in range(BURSTS):
			start_ms = self.h.clock.now_ms()
			for index in range(BURST_SIZE):
				self.ledger.erpnext_sent.append(
					self.h.compose(room=room, sender=ALICE, text=f"burst {burst} #{index}")
				)
				# Ten composes inside two seconds: the composer is not rate limited, only the
				# relay is. Spreading them over the window models two people typing fast.
				self.h.advance(BURST_WINDOW_MS // BURST_SIZE)
			self.ledger.notes.append(
				f"burst {burst}: {BURST_SIZE} composed in {self.h.clock.now_ms() - start_ms}ms"
			)
			self.drain_relay(room)
			self.pump(client=client)

	def _phase_outage(self, client: Any) -> None:
		"""Sixty seconds with the kill switch on. Nothing is dropped; the backlog drains in order."""
		self.h.settings.pause_outbound = 1
		for index in range(_OUTAGE_MESSAGES):
			self.ledger.erpnext_sent.append(
				self.h.compose(room=ROOM_GROUP, sender=BOB, text=f"during the outage #{index}")
			)
		self.drain_relay(ROOM_GROUP)
		self.ledger.outage_backlog = len(
			[j for j in self.h.rows("Chat Relay Job") if str(j.get("status")) == "Pending"]
		)
		self.h.advance(OUTAGE_SECONDS * 1000)
		self.h.settings.pause_outbound = 0
		self.drain_relay(ROOM_GROUP)
		self.pump(client=client)

	def _phase_abandoned_lease(self, client: Any) -> None:
		"""A worker killed mid-relay, simulated by taking a lease and never coming back.

		Cleanup is by lease expiry and never by a ``finally`` block, because a ``finally`` block
		does not run when the process is killed — which is the entire reason
		``lease_expires_at`` is a column rather than a Redis key. The sweeper is the only thing
		that reaps it, so the sweeper is what this phase actually tests.
		"""
		message = self.h.compose(room=ROOM_DM, sender=ALICE, text="the worker died holding this")
		self.ledger.erpnext_sent.append(message)
		self.h.commit()

		job = self.outbound.claim_next_job(ROOM_DM)
		if job is None:  # pragma: no cover - only if the compose path stopped queueing
			self.ledger.notes.append("abandoned-lease phase: nothing claimable, the kill was not simulated")
			return
		self.ledger.lease_abandoned = str(job.get("name") or "")

		# The worker vanishes here. Nothing releases the lease; time does.
		self.h.advance((self.h.settings.http_timeout_seconds + 600) * 1000)
		self.outbound.sweep_relay_jobs()
		self.h.commit()
		self.drain_relay(ROOM_DM)
		self.pump(client=client)

	def _phase_out_of_order(self, client: Any) -> None:
		"""One ``updated`` before its ``created``, and one ``deleted`` before its ``created``.

		Both are produced by the fake and then **re-ordered by the soak**, so the envelopes are
		the real ones. Out-of-order delivery is not exotic: Pub/Sub gives no ordering guarantee
		without an ordering key, and the two orderings have different correct answers — an
		``updated`` for an unknown name is applied as a create (losing it would lose the whole
		message, not merely the edit), while a ``deleted`` for an unknown name is recorded
		``Ignored`` and must never conjure a row to bury.
		"""
		space = self.ledger.rooms[ROOM_GROUP]

		updated_first = self.chat_post(space, sender=BOB, text="edited before we ever saw it")
		self.chat_patch(updated_first, sender=BOB, text="the edit that arrived first")
		events = self.h.fake.drain_events()
		for envelope in reversed(events):
			self.deliver(envelope, client=client)
		self.ledger.out_of_order_updated_first = updated_first

		deleted_first = self.chat_post(space, sender=BOB, text="deleted before we ever saw it")
		self.chat_delete(deleted_first, sender=BOB)
		events = self.h.fake.drain_events()
		for envelope in reversed(events):
			self.deliver(envelope, client=client)
		self.ledger.out_of_order_deleted_first = deleted_first

	def _phase_edits_and_deletes(self, client: Any) -> None:
		"""Ten edits and ten deletes, split evenly across the two origins."""
		local_pool = [name for name in self.ledger.erpnext_sent if name]
		remote_pool = [
			name for name in self.ledger.google_sent if name != self.ledger.out_of_order_deleted_first
		]

		for index in range(EDITS_PER_ORIGIN):
			target = local_pool[index]
			self.h.local_edit(target, f"edited in erpnext #{index}")
			self.ledger.erpnext_edits.append(target)
		for index in range(DELETES_PER_ORIGIN):
			target = local_pool[-(index + 1)]
			self.h.local_delete(target)
			self.ledger.erpnext_deletes.append(target)
		self.h.commit()
		self.drain_relay(ROOM_GROUP, ROOM_DM)
		self.pump(client=client)

		for index in range(EDITS_PER_ORIGIN):
			target = remote_pool[index]
			self.chat_patch(target, sender=BOB, text=f"edited in chat #{index}")
			self.ledger.google_edits.append(target)
		for index in range(DELETES_PER_ORIGIN):
			target = remote_pool[-(index + 1)]
			self.chat_delete(target, sender=BOB)
			self.ledger.google_deletes.append(target)
		self.pump(client=client)
		self.drain_relay(ROOM_GROUP, ROOM_DM)
		self.pump(client=client)

	def _phase_attachments(self, client: Any) -> None:
		"""Five attachments, including the two the brief singles out.

		The Drive-sourced one must be stored as a **reference**: copying the bytes detaches the
		file from Drive's permission model, which is the governing ACL, and ERPNext has no way
		to reproduce it. The oversized one must be *recorded as skipped* rather than dropped —
		a skip is a row an operator can find, a drop is a message whose content nobody can
		reconstruct and nobody knows is missing.
		"""
		space = self.ledger.rooms[ROOM_DM]

		# 1 + 2: outbound from ERPNext, one that fits and one that does not.
		fits = self.h.compose(room=ROOM_DM, sender=ALICE, text="here is the drawing")
		self.ledger.erpnext_sent.append(fits)
		self.h.attach_file(fits, "drawing.pdf", b"x" * 512)

		oversized = self.h.compose(room=ROOM_DM, sender=ALICE, text="here is the very large drawing")
		self.ledger.erpnext_sent.append(oversized)
		self.h.attach_file(oversized, "huge.pdf", b"x" * OVERSIZED_ATTACHMENT_BYTES)

		self.h.commit()
		self.drain_relay(ROOM_DM)
		self.pump(client=client)

		# 3 + 4 + 5: inbound. Built directly on the fake's store so the resource carries the
		# real `attachment[]` shape, then delivered through the ordinary inbound pipeline.
		self._chat_post_with_attachment(
			space,
			text="scan from my phone",
			attachment={
				"name": f"{space}/attachments/att-uploaded",
				"contentName": "scan.png",
				"contentType": "image/png",
				"source": "UPLOADED_CONTENT",
				"attachmentDataRef": {"resourceName": "media/soak-uploaded"},
			},
		)
		self._chat_post_with_attachment(
			space,
			text="the spec lives in Drive",
			attachment={
				"name": f"{space}/attachments/att-drive",
				"contentName": "spec.gdoc",
				"contentType": "application/vnd.google-apps.document",
				"source": "DRIVE_FILE",
				"driveDataRef": {"driveFileId": "1SoakDriveFileId"},
			},
		)
		self._chat_post_with_attachment(
			space,
			text="a very large scan",
			attachment={
				"name": f"{space}/attachments/att-oversized",
				"contentName": "huge-scan.png",
				"contentType": "image/png",
				"source": "UPLOADED_CONTENT",
				"attachmentDataRef": {"resourceName": "media/soak-oversized"},
			},
			blob=b"y" * OVERSIZED_ATTACHMENT_BYTES,
		)
		self.pump(client=client)

	def _chat_post_with_attachment(
		self, space: str, *, text: str, attachment: dict[str, Any], blob: bytes = b"z" * 64
	) -> str:
		"""Post a native-client message whose fetched resource carries an ``attachment[]``.

		The array is added at the client seam rather than in the fake's store, because
		``FakeChatAPI._message_resource`` emits no ``attachment`` field at all — the harness
		models messages, not attachments. That is a gap in the fake, not in the pipeline: what
		inbound has to do is *parse a resource*, and where the resource came from does not
		change the parse. The harness's ``decorate_resource`` is the seam, and it is named in
		the report so nobody reads this as the fake having produced it.
		"""
		name = self.chat_post(space, sender=BOB, text=text)
		self.h.decorate_resource(name, {"attachment": [dict(attachment)]})
		data_ref = str(dict(attachment.get("attachmentDataRef") or {}).get("resourceName") or "")
		if data_ref:
			self.h.seed_media(data_ref, blob)
		return name

	def _drain_attachment_downloads(self, client: Any) -> None:
		"""Run every ``Pending`` attachment download in line.

		The real path enqueues these (``enqueue_after_commit=True``, ``deduplicate=True``) and a
		scheduler sweeps whatever the deploy's ``FLUSHDB`` lost. There is no worker here, so the
		soak calls the same function the job would — ``download_attachment`` — rather than
		asserting on a queue entry, because a queued job that nobody runs proves nothing about
		whether the bytes arrive.
		"""
		from erpnext_enhancements.chat.sync import attachments

		for row in self.h.rows("Chat Attachment"):
			if str(row.get("ingest_state") or "") != "Pending":
				continue
			try:
				attachments.download_attachment(str(row["name"]), client=client)
			except Exception as exc:  # pragma: no cover - recorded, never fatal
				self.ledger.notes.append(f"attachment download {row['name']} raised {type(exc).__name__}")

	def _phase_settle(self, client: Any) -> None:
		"""Run the pipeline to quiescence, then stop. Bounded, and the bound is a printed note.

		The clock advances between passes because the recovery mechanisms are **time-based**:
		a job that failed transiently sits behind ``available_at``, a reaped lease sits behind
		a backoff, and an attachment download waits out a cooldown. A settle loop that did not
		let time pass would report "still Pending" for work that was never given the chance to
		run, which is a harness artefact wearing the costume of a bug.
		"""
		for _ in range(24):
			self.drain_relay(ROOM_GROUP, ROOM_DM)
			self.pump(client=client)
			self._drain_attachment_downloads(client)
			self.h.commit()
			if self._quiescent():
				return
			self.h.advance(30_000)
			self.outbound.sweep_relay_jobs()
			self.inbound.sweep_stuck_inbound_events()
			self.h.commit()
		self.ledger.notes.append("settle loop hit its bound: the pipeline was still moving after 24 passes")

	def _quiescent(self) -> bool:
		"""Nothing left to do anywhere: no open job, no unprocessed event, no pending download."""
		if self.h.fake.event_count():
			return False
		if any(
			str(j.get("status") or "") in {"Pending", "In Progress", "Failed"}
			for j in self.h.rows("Chat Relay Job")
		):
			return False
		if any(str(r.get("status") or "") == "Received" for r in self.h.rows("Chat Inbound Event")):
			return False
		return not any(str(a.get("ingest_state") or "") == "Pending" for a in self.h.rows("Chat Attachment"))


# Sub-budgets carved out of the 120/80 split. Named so :meth:`_phase_conversation` and the
# arrival check derive from the same numbers rather than two hand-counted ones.
_OUTAGE_MESSAGES: int = 12
_LATE_ERPNEXT_MESSAGES: int = 3  # the abandoned-lease message, plus the two attachment carriers
_OUT_OF_ORDER_MESSAGES: int = 2
_ATTACHMENT_INBOUND_MESSAGES: int = 3


def _token_for(email: str) -> str:
	from erpnext_enhancements.chat.testing.fake_chat import token_for

	return token_for(email)


# --------------------------------------------------------------------------------------
# The assertions
# --------------------------------------------------------------------------------------


def evaluate(harness: Any, driver: SoakDriver, *, title: str) -> SoakReport:
	"""Every printed assertion. Reads state; changes nothing."""
	report = SoakReport(title=title)
	ledger = driver.ledger
	messages = harness.rows("Chat Message")
	jobs = harness.rows("Chat Relay Job")
	revisions = harness.rows("Chat Message Revision")
	attachments = harness.rows("Chat Attachment")
	counters = driver.seams.counters()
	report.counters = dict(counters)

	sent_total = len(ledger.erpnext_sent) + len(ledger.google_sent)

	# --- composition actually executed ------------------------------------------------
	report.add(Check.compare("composition: messages sent", sent_total, TOTAL_MESSAGES))
	report.add(Check.compare("composition: erpnext-origin sent", len(ledger.erpnext_sent), ERPNEXT_MESSAGES))
	report.add(Check.compare("composition: chat-origin sent", len(ledger.google_sent), GOOGLE_MESSAGES))

	# --- row count, tombstones ---------------------------------------------------------
	report.add(Check.compare("rows: Chat Message count", len(messages), TOTAL_MESSAGES))
	deleted_rows = [m for m in messages if _int(m.get("is_deleted"))]
	by_google_name_early = {
		str(m.get("gchat_message_name") or ""): m for m in messages if m.get("gchat_message_name")
	}
	issued_deletes = [_row(messages, name) for name in ledger.erpnext_deletes] + [
		by_google_name_early.get(name) for name in ledger.google_deletes
	]
	report.add(
		Check.compare(
			"rows: deletes tombstoned not removed",
			len([m for m in issued_deletes if m and _int(m.get("is_deleted"))]),
			DELETES_PER_ORIGIN * 2,
			note=(
				f"counted against the {DELETES_PER_ORIGIN * 2} deletes the run issued, not against "
				f"the {len(deleted_rows)} rows that happen to be flagged — a tombstone that appeared "
				f"for some other reason is not evidence that a delete propagated. The body stays on "
				f"the row: Google's tombstone carries no content, so ERPNext is the only copy."
			),
		)
	)
	report.add(
		Check.compare(
			"rows: tombstones still carry their text",
			len([m for m in deleted_rows if (m.get("text") or "").strip()]),
			len(deleted_rows),
		)
	)

	# --- arrival, per origin. The anti-suppression instrument. --------------------------
	by_google_name = {}
	for row in messages:
		key = str(row.get("gchat_message_name") or "")
		if key:
			by_google_name.setdefault(key, []).append(row)

	erpnext_bound = [
		name
		for name in ledger.erpnext_sent
		if _row(messages, name) and _row(messages, name).get("gchat_message_name")
	]
	report.add(
		Check.compare(
			"arrival: erpnext-origin reached Chat",
			len(erpnext_bound),
			len(ledger.erpnext_sent),
			note=f"shortfall {len(ledger.erpnext_sent) - len(erpnext_bound)}",
		)
	)
	google_arrived = [name for name in ledger.google_sent if name in by_google_name]
	report.add(
		Check.compare(
			"arrival: chat-origin reached ERPNext",
			len(google_arrived),
			len(ledger.google_sent),
			note=(
				f"shortfall {len(ledger.google_sent) - len(google_arrived)} — a shortfall here is a "
				f"real coworker message consumed as though it were our own echo"
			),
		)
	)

	# --- duplicates ---------------------------------------------------------------------
	dupes = {k: len(v) for k, v in by_google_name.items() if len(v) > 1}
	report.add(
		Check.compare(
			"dupes: duplicate gchat_message_name values", len(dupes), 0, note=str(sorted(dupes)[:5])
		)
	)

	orphan_terminal = [
		m
		for m in messages
		if str(m.get("sync_state") or "") == "Relayed" and not str(m.get("gchat_message_name") or "")
	]
	report.add(Check.compare("dupes: Relayed rows with no gchat_message_name", len(orphan_terminal), 0))

	# --- the Chat side --------------------------------------------------------------------
	chat_live = 0
	chat_all = 0
	chat_dupes = 0
	for _room, space in ledger.rooms.items():
		live = harness.fake.messages_in(space)
		everything = harness.fake.messages_in(space, include_deleted=True)
		chat_live += len(live)
		chat_all += len(everything)
		names = [m["name"] for m in everything]
		chat_dupes += len(names) - len(set(names))
	expected_chat = len(ledger.erpnext_sent) + len(ledger.google_sent)
	report.add(
		Check.compare(
			"chat: messages in the fake spaces",
			chat_all,
			expected_chat,
			note="every message, tombstones included — one Chat message per message sent, from either side",
		)
	)
	report.add(Check.compare("chat: duplicate Chat messages", chat_dupes, 0))

	# --- sync state ------------------------------------------------------------------------
	unsettled = [m for m in messages if str(m.get("sync_state") or "") in NON_TERMINAL_MESSAGE_STATES]
	report.add(
		Check.compare(
			"state: messages still in a non-terminal sync_state",
			len(unsettled),
			0,
			note=str(sorted({str(m.get("sync_state")) for m in unsettled})),
		)
	)
	dead = [j for j in jobs if str(j.get("status") or "") == "Dead"]
	report.add(
		Check.compare(
			"state: relay jobs Dead",
			len(dead),
			0,
			# The same truncated note the open-jobs check carries, and for a better reason: a
			# Dead job is the one an operator can no longer interrogate by re-running anything.
			# Without this the report says "130 dead" and nothing else, and the failure it
			# produces upstream — `erpnext-origin reached Chat: 0` — reads like an auth or quota
			# fault. It was a test double one signature behind.
			note=str(
				[
					(j.get("operation"), str(j.get("last_error") or "")[:200])
					for j in dead[:3]
				]
			),
		)
	)
	open_jobs = [j for j in jobs if str(j.get("status") or "") in {"Pending", "In Progress", "Failed"}]
	report.add(
		Check.compare(
			"state: relay jobs still open",
			len(open_jobs),
			0,
			# `last_error` is truncated rather than printed whole. It is already body-free by
			# construction — `client.build_log_record` sees to that — but a report block is a
			# thing people paste into tickets, and 200 characters of Google error text is enough
			# to diagnose without inviting the habit of dumping whatever a field happens to hold.
			note=str(
				[
					(j.get("operation"), j.get("status"), str(j.get("last_error") or "")[:200])
					for j in open_jobs[:3]
				]
			),
		)
	)

	# --- ordering ----------------------------------------------------------------------------
	#
	# "Render order by seq is identical on both sides" is the brief's wording and it is not
	# achievable as literal equality when both sides originate messages concurrently — nor
	# should it be. `seq` is *assignment*-ordered and immutable (ADR §G.8), and a message typed
	# in Chat is assigned its seq when its event is ingested, which is necessarily after any
	# ERPNext message composed in the interim. That is precisely the case `late_arrival` exists
	# to mark. So the property is decomposed into the three claims that are true and testable:
	# each origin's own messages are in order on both sides, and every cross-origin inversion
	# is flagged.
	for room, space in ledger.rooms.items():
		local = sorted(
			[m for m in messages if m.get("room") == room and m.get("gchat_message_name")],
			key=lambda m: _int(m.get("seq")),
		)
		remote_index = {
			str(m["name"]): position
			for position, m in enumerate(harness.fake.messages_in(space, include_deleted=True))
		}
		local = [m for m in local if str(m.get("gchat_message_name")) in remote_index]

		for origin, label in (("ERPNext", "erpnext-origin"), ("Google Chat", "chat-origin")):
			subset = [m for m in local if str(m.get("sync_origin") or "") == origin]
			positions = [remote_index[str(m["gchat_message_name"])] for m in subset]
			report.add(
				Check.compare(
					f"order: {room} {label} messages are in seq order in Chat too",
					positions == sorted(positions),
					True,
					note=(
						f"{len(subset)} messages; the relay is a strict per-room FIFO in job_seq order, "
						f"so a create must never be overtaken"
					),
				)
			)

		unflagged: list[str] = []
		for index in range(1, len(local)):
			this_pos = remote_index[str(local[index]["gchat_message_name"])]
			prev_pos = remote_index[str(local[index - 1]["gchat_message_name"])]
			if this_pos < prev_pos and not _int(local[index].get("late_arrival")):
				unflagged.append(str(local[index]["name"]))
		report.add(
			Check.compare(
				f"order: {room} every cross-side inversion is a flagged late arrival",
				len(unflagged),
				0,
				note=(
					"a row that renders earlier in Chat than its seq implies must carry late_arrival, "
					"so Phase 3 can show the 'recovered' affordance instead of silently reordering"
				),
			)
		)

	# --- the seam. The headline number. ---------------------------------------------------------
	notify = int(counters.get(driver.seams.COUNTER_NOTIFY_NEW_MESSAGE, 0))
	report.add(
		Check.compare(
			"seam: notify_new_message calls",
			notify,
			NOTIFIABLE_MESSAGES,
			note=(
				f"exactly once per genuinely new message, zero for echoes/replays/reconciliations; "
				f"{ledger.events_delivered} events delivered of which {ledger.events_duplicated} were "
				f"redeliveries, {counters.get(driver.seams.COUNTER_ECHOES_SUPPRESSED, 0)} echoes "
				f"suppressed. {DELETED_BEFORE_INGEST} of the {TOTAL_MESSAGES} was already deleted in "
				f"Chat when we ingested it and notified nobody, which is the difference between this "
				f"number and 200"
			),
		)
	)
	report.add(
		Check.compare(
			"seam: inbound events that produced a new message",
			int(counters.get(driver.seams.COUNTER_EVENTS_INGESTED, 0)),
			GOOGLE_MESSAGES,
			note=(
				f"the other {ledger.events_delivered - GOOGLE_MESSAGES} of {ledger.events_delivered} "
				f"deliveries were echoes of our own writes, redeliveries, or events for messages "
				f"already stored — and every one of them notified nobody. This is the direct form "
				f"of 'zero times for echoes, replays and reconciliations'; the 200 above is the sum."
			),
		)
	)
	echoes = int(counters.get(driver.seams.COUNTER_ECHOES_SUPPRESSED, 0))
	structural = int(counters.get(driver.seams.COUNTER_DUPLICATES_REJECTED, 0))
	report.add(
		Check.at_least(
			"seam: our own messages came back and were absorbed",
			echoes + structural,
			len(erpnext_bound),
			note=(
				f"{echoes} bound by the client- id (the §4.D race: the event beat the relay's "
				f"write-back) and {structural} absorbed structurally by unique(gchat_message_name) "
				f"(the relay won). Which mechanism fires is a timing detail; that every one of the "
				f"{len(erpnext_bound)} relayed messages was absorbed by one of them is the property, "
				f"and 'no new row, no notification' is asserted above."
			),
		)
	)
	report.add(
		Check.at_least(
			"seam: both echo mechanisms actually fired",
			min(echoes, structural),
			1,
			note="a run where one of the two never fires is a run that only tested half the ladder",
		)
	)
	report.add(
		Check.compare("seam: echo orphans", int(counters.get(driver.seams.COUNTER_ECHO_ORPHANS, 0)), 0)
	)

	# --- the revision table --------------------------------------------------------------------
	#
	# Counted against **the mutations the run issued**, resolved back to the message each one
	# targeted, rather than against however many rows happen to be in the table. "Ten Edit rows
	# exist" is satisfied by ten revisions of one message; "each of the ten messages I edited has
	# an Edit revision" is the claim §4.F actually makes.
	by_type: dict[str, int] = {}
	for row in revisions:
		by_type[str(row.get("change_type") or "")] = by_type.get(str(row.get("change_type") or ""), 0) + 1
	revised: dict[tuple[str, str], list[dict[str, Any]]] = {}
	for row in revisions:
		revised.setdefault((str(row.get("message") or ""), str(row.get("change_type") or "")), []).append(row)

	edited_messages = list(ledger.erpnext_edits) + [
		str((by_google_name.get(name) or [{}])[0].get("name") or "") for name in ledger.google_edits
	]
	deleted_messages = list(ledger.erpnext_deletes) + [
		str((by_google_name.get(name) or [{}])[0].get("name") or "") for name in ledger.google_deletes
	]
	report.add(
		Check.compare(
			"audit: Edit revisions",
			len([m for m in edited_messages if revised.get((m, "Edit"))]),
			EDITS_PER_ORIGIN * 2,
			note=f"one per edited message; all change types in the table: {by_type}",
		)
	)
	report.add(
		Check.compare(
			"audit: Delete revisions",
			len([m for m in deleted_messages if revised.get((m, "Delete"))]),
			DELETES_PER_ORIGIN * 2,
			note="the delete revision is where the body of a deleted message is preserved for §12",
		)
	)
	bodied = [
		r
		for r in revisions
		if str(r.get("change_type")) in {"Edit", "Delete"}
		and (r.get("text_before") or "").strip()
		and (str(r.get("change_type")) == "Delete" or (r.get("text_after") or "").strip())
	]
	report.add(
		Check.compare(
			"audit: revisions carrying pre- and post-bodies",
			len(bodied),
			by_type.get("Edit", 0) + by_type.get("Delete", 0),
			note="an Edit needs both sides; a Delete needs the before-body, which is the whole point of it",
		)
	)

	# --- attachments --------------------------------------------------------------------------
	report.add(
		Check.compare(
			"attachments: Chat Attachment rows",
			len(attachments),
			ATTACHMENTS,
			note=str([(a.get("source"), a.get("ingest_state")) for a in attachments]),
		)
	)
	report.add(
		Check.compare(
			"attachments: outbound relayed to Chat",
			len(
				[
					a
					for a in attachments
					if str(a.get("source")) == "ERPNext" and str(a.get("ingest_state")) == "Stored"
				]
			),
			1,
		)
	)
	report.add(
		Check.compare(
			"attachments: oversized outbound recorded Skipped, message still relayed",
			len(
				[
					a
					for a in attachments
					if str(a.get("source")) == "ERPNext" and str(a.get("ingest_state")) == "Skipped"
				]
			),
			1,
			note="the body still goes with a deep link; refusing the message would let Google's ceiling "
			"decide what an employee may say inside ERPNext",
		)
	)
	report.add(
		Check.compare(
			"attachments: oversized outbound never crossed the wire",
			ledger.oversized_outbound_refused,
			True,
			note="exactly one media.upload in the fake's call journal — the refusal is a fact about "
			"what was sent, not about what was recorded",
		)
	)
	# `Chat Attachment.source` is a Select whose options are `Uploaded / Drive Link / ERPNext`
	# — **our** values, not Google's `UPLOADED_CONTENT` / `DRIVE_FILE` enum, which the ingest
	# translates through `chat_attachment.GOOGLE_SOURCE_MAP`. These three checks used to compare
	# against the Google spelling and were therefore unsatisfiable by any *valid* row: the enum
	# is not in the Select's options, so a row carrying it could not exist. That made them look
	# like engine gaps for as long as the inbound ingest was missing, and they would have gone on
	# failing after it landed. A check that cannot pass is worse than no check.
	report.add(
		Check.compare(
			"attachments: inbound UPLOADED_CONTENT stored as a private File",
			len([a for a in attachments if str(a.get("source")) == "Uploaded" and a.get("file")]),
			1,
		)
	)
	report.add(
		Check.compare(
			"attachments: inbound DRIVE_FILE stored as a reference, never copied",
			len([a for a in attachments if str(a.get("source")) == "Drive Link" and not a.get("file")]),
			1,
			note="copying detaches the file from Drive's permission model, which is the governing ACL",
		)
	)
	report.add(
		Check.compare(
			"attachments: oversized inbound recorded Skipped, not dropped",
			len(
				[
					a
					for a in attachments
					if str(a.get("source")) == "Uploaded" and str(a.get("ingest_state")) == "Skipped"
				]
			),
			1,
		)
	)

	# --- the named chaos cases this run covers ----------------------------------------------------
	report.add(
		Check.compare(
			"chaos: bursts of 10 in 2s, all delivered",
			len([m for m in messages if str(m.get("text") or "").startswith("burst ")]),
			BURSTS * BURST_SIZE,
			note=f"limiter waited {ledger.burst_waits_ms}ms in total",
		)
	)
	report.add(
		Check.compare(
			"chaos: burst forced the worker to back off",
			ledger.burst_waits_ms > 0,
			True,
			note="all ten arrive because the worker waited, not because the bucket was ignored",
		)
	)
	report.add(
		Check.compare(
			"chaos: 60s outage backlog drained",
			ledger.outage_backlog,
			_OUTAGE_MESSAGES,
			note="messages accumulated Pending behind the kill switch and drained in job_seq order",
		)
	)
	abandoned = _row(jobs, ledger.lease_abandoned)
	report.add(
		Check.compare(
			"chaos: abandoned lease was reaped and its job completed",
			str((abandoned or {}).get("status") or "no job was abandoned"),
			"Done",
			note=(
				f"job {ledger.lease_abandoned or 'none'} was left In Progress by a worker that never "
				f"came back; only lease expiry recovers it, because a finally block does not run "
				f"when a process is killed"
			),
		)
	)
	ooo_updated = by_google_name.get(ledger.out_of_order_updated_first, [])
	report.add(
		Check.compare(
			"chaos: updated-before-created produced exactly one row",
			len(ooo_updated),
			1,
			note="an updated event for an unknown name is applied as a create; dropping it loses the message",
		)
	)
	ooo_deleted = by_google_name.get(ledger.out_of_order_deleted_first, [])
	report.add(
		Check.compare(
			"chaos: deleted-before-created conjured no row to bury",
			len(ooo_deleted),
			1,
			note="the delete is recorded Ignored, then the later create is the one row. Rule 1 holds.",
		)
	)
	report.add(
		Check.compare(
			"chaos: deleted-before-created converges to a tombstone",
			_int((ooo_deleted[0] if ooo_deleted else {}).get("is_deleted")),
			1,
			note=(
				"the message is deleted in Chat; if ERPNext never applies that delete the two sides "
				"stay divergent forever and no counter moves"
			),
		)
	)
	report.add(
		Check.compare(
			"chaos: every 10th inbound event delivered twice",
			ledger.events_duplicated > 0,
			True,
			note=f"{ledger.events_duplicated} redeliveries out of {ledger.events_delivered} deliveries",
		)
	)

	# --- what this run cannot prove ---------------------------------------------------------------
	report.add(
		Check.skipped(
			"live: ~20-message round trip against a real Chat space",
			expected="20 messages, authored by the real humans",
			note=(
				"no live credentials in this environment. Four things only a live run proves and the "
				"fake cannot: that messages appear in the native client authored by the **humans** "
				"rather than by an app badge (CQ-1); the real Workspace Events payload shape; real "
				"quota behaviour, including the internal per-space limits Google says are not visible "
				"on the Quotas page; and real end-to-end latency. Entry point: "
				"`bench execute erpnext_enhancements.chat.gchat.smoke_test.run` from the production "
				"VM, which is also where PHASE2_VERIFIED.md §8.1 gets settled — whether a real "
				"`messages.get` populates clientAssignedMessageId, which this soak assumes it does."
			),
		)
	)

	report.facts = {
		"events_delivered": ledger.events_delivered,
		"events_redelivered": ledger.events_duplicated,
		"relay_jobs": len(jobs),
		"inbound_event_rows": len(harness.rows("Chat Inbound Event")),
		"chat_live_messages": chat_live,
		"notes": ledger.notes,
	}
	return report


def evaluate_replay(before: dict[str, Any], after: dict[str, Any]) -> SoakReport:
	"""The replay gate: reprocessing the whole backlog must change nothing."""
	report = SoakReport(title="chat two-way soak — REPLAY (reprocess every raw event, re-enqueue every job)")
	for key in ("messages", "chat_messages", "revisions", "attachments", "relay_jobs", "inbound_events"):
		report.add(Check.compare(f"replay: {key} unchanged", after.get(key), before.get(key)))
	report.add(
		Check.compare(
			"replay: notify_new_message calls added",
			int(after.get("notify", 0)) - int(before.get("notify", 0)),
			0,
			note="a replay is not news; the seam must stay silent",
		)
	)
	report.add(
		Check.compare(
			"replay: context_stale calls added",
			int(after.get("context_stale", 0)) - int(before.get("context_stale", 0)),
			0,
			note="a re-applied edit invalidates nothing new",
		)
	)
	changed = sorted(set(after.get("rows") or ()) ^ set(before.get("rows") or ()))
	report.add(
		Check.compare(
			"replay: message rows byte-identical",
			len(changed),
			0,
			note=(
				"(name, seq, sync_state, gchat_message_name, is_deleted, text) for all "
				f"{after.get('messages')} rows; first differences: {[c[0] for c in changed[:5]]}"
			),
		)
	)
	return report


def fingerprint(harness: Any, driver: SoakDriver) -> dict[str, Any]:
	"""The comparable state, before and after a replay."""
	messages = harness.rows("Chat Message")
	counters = driver.seams.counters()
	chat = 0
	for space in _spaces(harness, driver):
		chat += len(harness.fake.messages_in(space, include_deleted=True))
	return {
		"messages": len(messages),
		"chat_messages": chat,
		"revisions": len(harness.rows("Chat Message Revision")),
		"attachments": len(harness.rows("Chat Attachment")),
		"relay_jobs": len(harness.rows("Chat Relay Job")),
		"inbound_events": len(harness.rows("Chat Inbound Event")),
		"notify": int(counters.get(driver.seams.COUNTER_NOTIFY_NEW_MESSAGE, 0)),
		"context_stale": int(counters.get(driver.seams.COUNTER_CONTEXT_STALE, 0)),
		"rows": tuple(
			sorted(
				(
					str(m.get("name")),
					_int(m.get("seq")),
					str(m.get("sync_state") or ""),
					str(m.get("gchat_message_name") or ""),
					_int(m.get("is_deleted")),
					str(m.get("text") or ""),
				)
				for m in messages
			)
		),
	}


def replay(harness: Any, driver: SoakDriver) -> None:
	"""Reprocess **every** raw ``Chat Inbound Event`` and re-enqueue **every** outbound job.

	The status reset is deliberate and is the only honest way to run this. ``process_inbound_event``
	short-circuits a settled row with ``already-settled``, which is correct in production and
	would make a replay assertion vacuous here — it would prove the guard works, not that the
	pipeline is idempotent. Putting each row back to ``Received`` makes the pipeline do the work
	again, which is what "reprocessing the backlog is a no-op" actually claims.

	The outbound half needs no such trick: ``run_relay_job`` on a ``Done`` row finds nothing
	claimable and returns, which *is* the property under test.
	"""
	client = harness.build_client(ALICE)
	rows = sorted(
		harness.rows("Chat Inbound Event"), key=lambda r: (str(r.get("creation") or ""), str(r.get("name")))
	)
	for row in rows:
		harness.set_row("Chat Inbound Event", str(row["name"]), status="Received", available_at=None)
		driver.inbound.process_inbound_event(str(row["name"]), client=client)
	harness.commit()

	for job in sorted(harness.rows("Chat Relay Job"), key=lambda r: _int(r.get("job_seq"))):
		driver.outbound.run_relay_job(str(job["name"]))
	harness.commit()


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------


def _int(value: Any) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0


def _row(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
	for row in rows:
		if str(row.get("name")) == str(name):
			return row
	return None


class _ResourceOverlay:
	"""A ``GoogleChatClient`` proxy that can add fields to a fetched ``Message``.

	It exists for one reason, and the reason is a limitation of the fake rather than of the
	code under test: ``FakeChatAPI._message_resource`` emits no ``attachment[]`` array at all,
	so an inbound attachment cannot be expressed through the fake's own store. The inbound
	pipeline's job is to *parse a resource*, and where the resource came from does not change
	the parse, so the soak decorates three named resources here and drives the ordinary
	pipeline over them. Everything else — the real builders, the real retry loop, the real
	``_request`` contract — is untouched.

	Shared by both harnesses so the two entry points see identically shaped resources.
	"""

	def __init__(self, inner: Any, overlay: dict[str, dict[str, Any]]) -> None:
		self._inner = inner
		self._overlay = overlay

	def __getattr__(self, item: str) -> Any:
		return getattr(self._inner, item)

	def get_message(self, message_name: str) -> dict[str, Any]:
		resource = self._inner.get_message(message_name)
		patch = self._overlay.get(str(resource.get("name") or message_name))
		return {**resource, **patch} if patch else resource


def _spaces(harness: Any, driver: SoakDriver) -> list[str]:
	return list(driver.ledger.rooms.values()) or [
		str(r.get("gchat_space_name") or "") for r in harness.rows("Chat Room") if r.get("gchat_space_name")
	]


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------


def run_soak(harness: Any, *, do_replay: bool = True) -> tuple[SoakReport, SoakReport | None]:
	"""Cold run, then the replay run. **The one implementation both entry points use.**"""
	driver = SoakDriver(harness)
	driver.run()
	harness.commit()
	driver.ledger.burst_waits_ms = int(getattr(harness, "limiter_waits_ms", 0))
	driver.ledger.oversized_outbound_refused = bool(getattr(harness, "oversized_outbound_refused", False))

	cold = evaluate(harness, driver, title="chat two-way soak — COLD")
	if not do_replay:
		return cold, None

	before = fingerprint(harness, driver)
	replay(harness, driver)
	after = fingerprint(harness, driver)
	return cold, evaluate_replay(before, after)


def run(print_report: bool = True) -> dict[str, Any]:
	"""``bench execute erpnext_enhancements.chat.testing.soak.run``.

	Real Frappe, real database, the fake on the Google side. **This entry point has never been
	executed** — this repo has no bench in CI and none was available when it was written — so
	treat its harness as unproven code and the bench-free run as the evidence. It is here
	because a soak that only exists inside a pytest file cannot be run by an operator against a
	staging site, which is exactly when somebody will want it.

	**Two things a bench run behaves differently on, and both are honest limitations rather than
	bugs to be papered over:**

	1. ``advance(ms)`` moves the *fake* clock only. Frappe's ``now_datetime`` on a bench is the
	   real one, so the 60-second outage and the abandoned-lease expiry are not fast-forwarded —
	   those two phases will either take real wall-clock time or report their check as failing
	   because the lease has not yet expired. The bench-free run is where those two are proved;
	   the bench run's value is that the *storage* is real (real unique indexes, real
	   ``for_update``, real ``savepoint`` semantics, real ``File`` permissions).
	2. It writes 200 chat messages and two spaces' worth of rows into whatever site it is run
	   against. **Run it on staging, never on production.**
	"""
	harness = _BenchHarness()
	cold, replayed = run_soak(harness)
	if print_report:
		print(cold.render())
		if replayed is not None:
			print(replayed.render())
	return {
		"cold": {
			"ok": cold.ok,
			"failed": [c.name for c in cold.failed],
			"skipped": [c.name for c in cold.skipped],
		},
		"replay": None
		if replayed is None
		else {"ok": replayed.ok, "failed": [c.name for c in replayed.failed]},
	}


class _BenchHarness:
	"""The bench half of the harness contract. Frappe-backed rows, fake Google.

	Unverified — see :func:`run`. Every method is a thin translation of the contract in
	:class:`SoakDriver`'s docstring onto real Frappe calls, and the reason it is thin is so a
	reviewer with a bench can check it by reading rather than by running.
	"""

	def __init__(self) -> None:
		import frappe

		from erpnext_enhancements.chat.gchat.client import AuthIdentity, GoogleChatClient
		from erpnext_enhancements.chat.testing.fake_chat import FakeChatAPI, FakeChatSettings, FakeClock

		self.frappe = frappe
		self.clock = FakeClock()
		self.fake = FakeChatAPI(clock=self.clock)
		self.settings = frappe.get_cached_doc("Chat Settings")
		self._client_cls = GoogleChatClient
		self._identity = AuthIdentity
		self._fake_settings = FakeChatSettings()
		self.resource_overlay: dict[str, dict[str, Any]] = {}
		self.limiter_waits_ms = 0

	@property
	def oversized_outbound_refused(self) -> bool:
		uploads = [c for c in self.fake.calls if getattr(c, "google_method", "") == "media.upload"]
		return len(uploads) == 1

	def build_client(self, subject: str, *, identity: str = "USER") -> Any:
		client = self._client_cls(
			subject=subject,
			identity=self._identity.USER,
			dry_run=False,
			settings=self._fake_settings,
			token_provider=self.fake.token_provider,
			transport=self.fake,
		)
		return _ResourceOverlay(client, self.resource_overlay)

	def decorate_resource(self, resource_name: str, patch: dict[str, Any]) -> None:
		self.resource_overlay[resource_name] = patch

	def seed_media(self, data_ref: str, blob: bytes) -> None:
		self.fake._attachments[data_ref] = blob

	def rows(self, doctype: str) -> list[dict[str, Any]]:
		"""Every row, unfiltered.

		``frappe.get_all`` is ``get_list(ignore_permissions=True)`` wearing a friendlier name and
		the house rule forbids it on a chat table without a membership filter. The exemption
		here is narrow and stated so it is not copied: this runs under ``bench execute`` as
		Administrator, on a staging site, purely to *count* rows for an assertion — the soak has
		no session user to filter for, and a filtered read would make the row-count assertion
		measure the filter instead of the pipeline. Nothing in this class serves a request.
		"""
		return [dict(r) for r in self.frappe.get_all(doctype, fields=["*"])]

	def set_row(self, doctype: str, name: str, **values: Any) -> None:
		self.frappe.db.set_value(doctype, name, values, update_modified=False)

	def commit(self) -> None:
		self.frappe.db.commit()

	def advance(self, ms: int) -> None:
		self.clock.advance(int(ms))

	def seed_room(self, room: str, *, space: str, room_type: str, members: tuple[str, ...]) -> None:
		doc = self.frappe.get_doc(
			{
				"doctype": "Chat Room",
				"name": room,
				"room_type": room_type,
				"gchat_space_name": space,
				"provisioning_mode": "Lazy",
				"membership_authority": "Bidirectional",
			}
		)
		doc.insert(ignore_permissions=True)
		for email in members:
			self.frappe.get_doc(
				{
					"doctype": "Chat Room Member",
					"room": room,
					"user": email,
					"is_active": 1,
					"gchat_member_state": "JOINED",
				}
			).insert(ignore_permissions=True)

	def compose(self, *, room: str, sender: str, text: str) -> str:
		from erpnext_enhancements.chat.sync import outbox

		doc = self.frappe.new_doc("Chat Message")
		doc.room = room
		doc.sender = sender
		doc.sender_email = sender
		doc.sender_kind = "Human"
		doc.message_type = "Text"
		doc.text = text
		outbox.insert_message(doc)
		return str(doc.name)

	def local_edit(self, message: str, text: str) -> None:
		doc = self.frappe.get_doc("Chat Message", message)
		doc.text = text
		doc.save(ignore_permissions=True)

	def local_delete(self, message: str) -> None:
		doc = self.frappe.get_doc("Chat Message", message)
		doc.is_deleted = 1
		doc.save(ignore_permissions=True)

	def attach_file(self, message: str, file_name: str, data: bytes) -> str:
		doc = self.frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"attached_to_doctype": "Chat Message",
				"attached_to_name": message,
				"is_private": 1,
				"content": data,
			}
		)
		doc.insert(ignore_permissions=True)
		return str(doc.name)
