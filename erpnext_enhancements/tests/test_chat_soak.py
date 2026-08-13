# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The bench-free half of the Phase 2 checkpoint gate — the 200-message two-way soak.

**Shape P: plain pytest functions.** It needs its own ``python -m pytest`` step in ``ci.yml``.
``python -m unittest`` collects nothing from a file of plain functions and reports success, and
this repo has already lost a suite that way for weeks (``CLAUDE.md``).

Why this file reuses ``test_chat_inbound``'s stub instead of writing a second one
---------------------------------------------------------------------------------
Two in-memory Frappes that disagree about anything — a unique index, the site timezone, what
``frappe.enqueue`` swallows — would let the soak pass on rules the rest of the suite does not
enforce, which is the one failure mode a gate must not have. ``test_chat_inbound`` already
carries the most complete stub in the repo: four real unique indexes, an ``America/Denver``
site so the Rule 3 timezone conversion is exercised rather than accidentally correct, the
``doc_events`` wiring, and ``frappe.enqueue`` with its **real** parameter list. This module
imports that stub and adds only the surfaces the *outbound* worker needs and that suite never
touches (``frappe.session``, ``only_for``, ``for_update``, ``get_single_value``,
``is_unique_key_violation``, ``File.get_content``).

Importing a test module for its fixtures is unusual and is the lesser evil here. It is safe
because this suite gets **its own CI step**: nothing else runs in the process, so the shared
``STORE`` and the shared clock cannot cross-talk with another suite (``CLAUDE.md``).

The clock
---------
One clock drives everything: the ``FakeClock`` inside ``FakeChatAPI``. ``now_datetime()`` is
re-pointed at it, so advancing the clock advances Google's ``createTime``, the relay's leases,
``available_at`` and the site-local timestamps together. Nothing sleeps: the 60-second outage
and the crashed-worker lease expiry are instant, deterministic, and cannot pass or fail
because of machine speed.

Run it directly for the printed block::

    python -m pytest erpnext_enhancements/tests/test_chat_soak.py -q
    python -m erpnext_enhancements.tests.test_chat_soak      # prints the report and exits 0/1
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
	sys.path.insert(0, str(REPO_ROOT))


def _real_frappe_is_installed() -> bool:
	"""Same guard as the suite whose stub this borrows, and it has to run **first**.

	``test_chat_inbound`` skips at module level when a real frappe is importable. A module-level
	skip raised during an *import* is not a skip, it is an error, so this check has to happen
	before that import — otherwise a bench run turns a deliberate skip into a red suite.
	"""
	try:
		spec = importlib.util.find_spec("frappe")
	except (ImportError, ValueError):
		return False
	return bool(spec and spec.origin)


if _real_frappe_is_installed():  # pragma: no cover - only true on a bench
	pytest.skip(
		"bench-free suite: a real frappe is installed. The bench equivalent is "
		"`bench execute erpnext_enhancements.chat.testing.soak.run`.",
		allow_module_level=True,
	)


from erpnext_enhancements.tests import test_chat_inbound as H

frappe = H.frappe


# --------------------------------------------------------------------------------------
# The clock, shared by Google and by Frappe
# --------------------------------------------------------------------------------------

from erpnext_enhancements.chat.testing.fake_chat import (
	DEFAULT_CLOCK_START_MS,
	FakeChatAPI,
	FakeChatSettings,
	FakeClock,
	_user_id_for,
)

#: ``FakeClock``'s epoch expressed as the **naive site-local** datetime Frappe would write for
#: that same instant. Derived rather than written out, and that is not fussiness: the borrowed
#: stub's site is ``America/Denver``, so a hand-written ``datetime(2026, 8, 9)`` would put
#: ``now_datetime()`` six hours ahead of Google's ``createTime`` for the same moment. Every
#: inbound message would then look six hours skewed, the §4.G median-skew alarm would fire on a
#: run containing no skew at all, and every arrival would be flagged ``late_arrival`` — three
#: false signals produced entirely by the harness.
CLOCK_EPOCH = (
	datetime.fromtimestamp(DEFAULT_CLOCK_START_MS / 1000, tz=timezone.utc)
	.astimezone(H.SITE_TZ)
	.replace(tzinfo=None)
)

_CLOCK: dict[str, Any] = {"clock": None}


def _soak_now() -> datetime:
	"""``frappe.utils.now_datetime`` for the run: site-local, naive, and **clock-driven**.

	Re-pointed rather than left as wall-clock because the soak deliberately jumps an hour in
	one step (the outage, the lease expiry) and because Google's timestamps come from the same
	clock. Two clocks would make every inbound message look minutes skewed and would fire the
	§4.G skew alarm on a run that has no skew in it.
	"""
	clock = _CLOCK.get("clock")
	if clock is None:
		return CLOCK_EPOCH
	return CLOCK_EPOCH + timedelta(milliseconds=clock.now_ms() - DEFAULT_CLOCK_START_MS)


def _install_clock(clock: FakeClock) -> None:
	_CLOCK["clock"] = clock
	# `_now_datetime` is the stub's own global; `_get_datetime` and `_add_to_date` close over it
	# by name, so rebinding it re-points those too. `utils.now_datetime` was bound to the
	# original function object and needs its own rebind.
	H._now_datetime = _soak_now
	frappe.utils.now_datetime = _soak_now
	frappe.utils.nowdate = lambda: _soak_now().date().isoformat()

	def _get_datetime(value: Any = None) -> datetime | None:
		if value in (None, ""):
			return _soak_now()
		if isinstance(value, datetime):
			return value
		return datetime.fromisoformat(str(value))

	def _add_to_date(value: Any = None, **deltas: Any) -> datetime:
		base = _get_datetime(value) or _soak_now()
		return base + timedelta(
			days=deltas.get("days", 0),
			hours=deltas.get("hours", 0),
			minutes=deltas.get("minutes", 0),
			seconds=deltas.get("seconds", 0),
		)

	frappe.utils.get_datetime = _get_datetime
	frappe.utils.add_to_date = _add_to_date

	# `outbound.py` and `outbox.py` do `from frappe.utils import now_datetime, …`, which binds
	# the **function object** at import time. Rebinding the attribute on the module above does
	# not reach them, and the symptom is quiet and vicious: leases and `available_at` get
	# stamped from the real wall clock while every other timestamp comes from the fake one, so
	# a lease written "30 minutes out" lands 28 hours in the future, the crashed-worker sweeper
	# never reaps anything, and the room's FIFO stays blocked behind a job nobody will finish.
	from erpnext_enhancements.chat.sync import outbound as _outbound
	from erpnext_enhancements.chat.sync import outbox as _outbox

	for module in (_outbound, _outbox):
		if hasattr(module, "now_datetime"):
			module.now_datetime = _soak_now
		if hasattr(module, "add_to_date"):
			module.add_to_date = _add_to_date
		if hasattr(module, "get_datetime"):
			module.get_datetime = _get_datetime


# --------------------------------------------------------------------------------------
# The surfaces the outbound worker needs and the inbound suite never touches
# --------------------------------------------------------------------------------------


class _DBAdapter:
	"""``frappe.db``, delegating to the borrowed ``_DB`` with three additions.

	A wrapper rather than an edit to :class:`H._DB`, because that class belongs to another
	suite and quietly changing its behaviour would change what *that* suite proves.

	* ``for_update`` — the relay's claim is a ``SELECT … FOR UPDATE``. There is one writer in
	  this process, so the lock is a no-op; the *parameter* still has to be accepted or the
	  claim raises ``TypeError`` and every job is silently unclaimable.
	* ``get_single_value`` — how the attachment module reads ``Chat Settings``.
	* ``is_unique_key_violation`` — Rule 2's predicate.
	"""

	def __init__(self, inner: Any) -> None:
		self._inner = inner

	def __getattr__(self, item: str) -> Any:
		return getattr(self._inner, item)

	def get_value(
		self,
		doctype: str,
		filters: Any = None,
		fieldname: Any = "name",
		as_dict: bool = False,
		for_update: bool = False,
		**_ignored: Any,
	) -> Any:
		return self._inner.get_value(doctype, filters, fieldname, as_dict)

	def get_single_value(self, doctype: str, fieldname: str) -> Any:
		if doctype != "Chat Settings":
			return None
		return getattr(H.SETTINGS, fieldname, None)

	def is_unique_key_violation(self, exc: BaseException) -> bool:
		return isinstance(exc, H.UniqueValidationError | H.DuplicateEntryError)


def _augment_stub() -> None:
	"""Everything ``outbound``/``attachments`` need that the inbound suite's stub omits."""
	import types

	frappe.db = _DBAdapter(frappe.db)
	frappe.session = types.SimpleNamespace(user="soak@sapphirefountains.com")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.only_for = lambda *a, **k: None
	frappe.PermissionError = PermissionError
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe._dict = dict
	frappe.flags.in_test = True

	# `attachments._file_bytes` reads bytes through `get_doc("File", name).get_content()`. The
	# borrowed Document has no such method; adding it here keeps the **real** `_file_bytes`
	# running, including its "unreadable File degrades to no bytes" branch.
	H.Document.get_content = lambda self: self.__dict__.get("content") or b""


# --------------------------------------------------------------------------------------
# The harness
# --------------------------------------------------------------------------------------


class _Limiter:
	"""The per-space bucket, running the **real** GCRA against the shared clock.

	``block=True`` advances the clock instead of sleeping, which is the faithful model: the
	production limiter sleeps out the wait inside the worker while holding the lease, and the
	room is a strict FIFO so nothing overtakes it. Here it means a ten-message burst genuinely
	paces at one write per second, ``waits_ms`` is a number the report can print, and the suite
	still finishes instantly.
	"""

	def __init__(self, clock: FakeClock, ratelimit: Any) -> None:
		self.clock = clock
		self.ratelimit = ratelimit
		self.free: dict[str, int] = {}
		self.waits_ms = 0

	def acquire(self, space: str, *, cost_ms: int, block: bool = True) -> Any:
		while True:
			decision = self.ratelimit.bucket_decision(self.clock.now_ms(), self.free.get(space, 0), cost_ms)
			if decision.allowed:
				self.free[space] = decision.next_free_ms
				return decision
			if not block:
				return decision
			self.waits_ms += decision.wait_ms
			self.clock.advance(decision.wait_ms)

	def peek(self, space: str) -> Any:
		return self.ratelimit.bucket_decision(self.clock.now_ms(), self.free.get(space, 0), 0)


class _Quota:
	"""Per-project fixed windows, running the real :func:`ratelimit.quota_decision`."""

	def __init__(self, clock: FakeClock, ratelimit: Any) -> None:
		self.clock = clock
		self.ratelimit = ratelimit
		self.counts: dict[tuple[str, int], int] = {}

	def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
		window = self.ratelimit.fixed_window_index(self.clock.now_ms(), self.ratelimit.PROJECT_WINDOW_MS)
		current = self.counts.get((bucket, window), 0)
		decision = self.ratelimit.quota_decision(current, cost, limit)
		if decision.allowed:
			self.counts[(bucket, window)] = decision.count
		return decision.allowed


class SoakHarness:
	"""The bench-free implementation of :class:`soak.SoakDriver`'s harness contract."""

	def __init__(self) -> None:
		from erpnext_enhancements.chat.gchat.client import AuthIdentity, GoogleChatClient
		from erpnext_enhancements.chat.sync import outbound, ratelimit

		self.clock = FakeClock()
		_install_clock(self.clock)
		# Read quota on: one `messages.get` per inbound event is exactly the traffic the
		# 15-reads-per-second-per-space bucket exists to shape, and the soak is the only test
		# that generates enough of it to matter.
		#
		# `request_latency_ms` is not decoration. With a frozen clock, an attachment relay's
		# `media.upload` and the `messages.create` that references it happen in the same
		# millisecond, so the create always 429s and the client's three synchronous retries all
		# land in that same millisecond too. Half a second per request is what makes backoff a
		# mechanism rather than a formality — and it is also the honest model, since two HTTP
		# round trips to Google do not take zero time.
		self.fake = FakeChatAPI(clock=self.clock, enforce_space_read_quota=True, request_latency_ms=500)
		self.settings = H.SETTINGS
		self.resource_overlay: dict[str, dict[str, Any]] = {}

		self._outbound = outbound
		self._client_cls = GoogleChatClient
		self._identity = AuthIdentity
		self._fake_settings = FakeChatSettings()

		self.limiter = _Limiter(self.clock, ratelimit)
		self.quota = _Quota(self.clock, ratelimit)
		self._saved = (outbound.build_client, outbound.space_limiter, outbound.project_quota)
		outbound.build_client = lambda subject, *, identity="USER", correlation_id="": (
			self.build_client(subject, identity=identity)
		)
		outbound.space_limiter = lambda: self.limiter
		outbound.project_quota = lambda: self.quota

	# -- lifecycle -------------------------------------------------------------------

	def restore(self) -> None:
		self._outbound.build_client, self._outbound.space_limiter, self._outbound.project_quota = self._saved

	@property
	def limiter_waits_ms(self) -> int:
		return self.limiter.waits_ms

	@property
	def oversized_outbound_refused(self) -> bool:
		"""Exactly one ``media.upload`` reached Google, so the oversized file did not.

		Asserted on the *call journal* rather than on a row, because "we chose not to send it"
		is a claim about what crossed the wire. A ``Chat Attachment`` row marked ``Skipped``
		would be consistent with an upload that happened anyway.
		"""
		uploads = [c for c in self.fake.calls if getattr(c, "google_method", "") == "media.upload"]
		return len(uploads) == 1

	# -- the contract ----------------------------------------------------------------

	def build_client(self, subject: str, *, identity: str = "USER") -> Any:
		"""The real client, wrapped in :class:`soak._ResourceOverlay`.

		``identity`` is accepted and **ignored**: this harness only drives the coworker mirror,
		which is `USER` throughout. It is in the signature because the worker passes it now, and
		a harness that did not accept it failed all 130 jobs with a `TypeError` that surfaced as
		*erpnext-origin reached Chat: 0* — a shape that reads like an auth or quota fault rather
		than a test double one signature behind.

		The overlay is shared with the bench harness rather than duplicated here, so both entry
		points hand the pipeline identically shaped resources; read its docstring for why an
		inbound attachment has to be injected at this seam at all.
		"""
		from erpnext_enhancements.chat.testing.soak import _ResourceOverlay

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
		"""Register bytes behind a ``media.download`` ref.

		Pokes the fake's store because the fake exposes a reader and no seeder, and uploading
		through the route would spend the space's write budget on arrangement.
		"""
		self.fake._attachments[data_ref] = blob

	def rows(self, doctype: str) -> list[dict[str, Any]]:
		return [dict(row) for row in H.STORE.table(doctype).values()]

	def set_row(self, doctype: str, name: str, **values: Any) -> None:
		row = H.STORE.table(doctype).get(name)
		if row is not None:
			row.update(values)

	def commit(self) -> None:
		frappe.db.commit()

	def advance(self, ms: int) -> None:
		self.clock.advance(int(ms))

	def seed_room(self, room: str, *, space: str, room_type: str, members: tuple[str, ...]) -> None:
		H.STORE.table("Chat Room")[room] = {
			"name": room,
			"room_type": room_type,
			"gchat_space_name": space,
			"provisioning_mode": "Lazy",
			"membership_authority": "Bidirectional",
			"seq_high_water": 0,
			"last_message_at": None,
			"last_event_at": None,
			"reverts_this_hour": 0,
			"revert_window_start": None,
			"external_users_allowed": 0,
			"mirror_whitelisted": 0,
		}
		for index, email in enumerate(members):
			name = f"{room}-M{index}"
			H.STORE.table("Chat Room Member")[name] = {
				"name": name,
				"room": room,
				"user": email,
				"is_active": 1,
				"gchat_member_state": "JOINED",
				"gchat_membership_name": f"{space}/members/{_user_id_for(email)}",
			}

	def compose(self, *, room: str, sender: str, text: str) -> str:
		from erpnext_enhancements.chat.sync import outbox

		doc = frappe.new_doc("Chat Message")
		doc.room = room
		doc.sender = sender
		doc.sender_email = sender
		doc.sender_kind = "Human"
		doc.message_type = "Text"
		doc.text = text
		outbox.insert_message(doc)
		return str(doc.name)

	def local_edit(self, message: str, text: str) -> None:
		"""An ERPNext-side edit, driven through the real ``on_update`` handler.

		The borrowed ``Document`` has no ``save()``, so the row write and the handler call are
		made explicitly — in Frappe's order, with a before-image, because
		``propagate_message_change`` refuses without one and that refusal is what stops every
		insert from relaying a second copy of itself.
		"""
		from erpnext_enhancements.chat.sync import outbox

		before = dict(H.STORE.table("Chat Message")[message])
		doc = frappe.get_doc("Chat Message", message)
		doc.text = text
		doc.get_doc_before_save = lambda: before  # type: ignore[method-assign]
		outbox.refresh_text_plain(doc)
		frappe.db.set_value(
			"Chat Message",
			message,
			{
				"text": text,
				"text_plain": getattr(doc, "text_plain", text),
				"is_edited": 1,
				"edited_at": _soak_now(),
			},
		)
		outbox.propagate_message_change(doc)

	def local_delete(self, message: str) -> None:
		from erpnext_enhancements.chat.sync import outbox

		before = dict(H.STORE.table("Chat Message")[message])
		doc = frappe.get_doc("Chat Message", message)
		doc.is_deleted = 1
		doc.get_doc_before_save = lambda: before  # type: ignore[method-assign]
		frappe.db.set_value(
			"Chat Message",
			message,
			{
				"is_deleted": 1,
				"deleted_at": _soak_now(),
				"deleted_by": doc.get("sender"),
				"deletion_source": "ERPNext",
			},
		)
		outbox.propagate_message_change(doc)

	def attach_file(self, message: str, file_name: str, data: bytes) -> str:
		name = H.STORE.next_name("File")
		H.STORE.table("File")[name] = {
			"name": name,
			"file_name": file_name,
			"attached_to_doctype": "Chat Message",
			"attached_to_name": message,
			"is_private": 1,
			"file_size": len(data),
			"content": data,
			"creation": _soak_now(),
		}
		return name


# --------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------


def _reset() -> None:
	H.STORE.rows.clear()
	H.STORE.counter = 0
	frappe.db._inner.commits = 0
	frappe.db._inner.after_commit.clear()
	frappe.enqueued.clear()
	frappe.published.clear()
	frappe.errors.clear()
	frappe.local.message_log.clear()
	frappe.cache().values.clear()
	H.SETTINGS.__init__()  # type: ignore[misc]


def run_bench_free() -> tuple[Any, Any]:
	"""Build the harness, run the soak cold, then replay it. Returns both reports."""
	from erpnext_enhancements.chat import seams
	from erpnext_enhancements.chat.testing import soak

	_augment_stub()
	_reset()
	seams.reset_counters()
	harness = SoakHarness()
	try:
		return soak.run_soak(harness)
	finally:
		harness.restore()


_RESULT: dict[str, Any] = {}


def _reports() -> tuple[Any, Any]:
	"""Run once; every assertion below reads the same pair of reports.

	One run rather than one per test because the soak *is* the unit — its assertions are about
	a single 200-message history, and re-running it per assertion would both be slow and let
	two tests disagree about what happened.
	"""
	if "cold" not in _RESULT:
		cold, replayed = run_bench_free()
		_RESULT["cold"] = cold
		_RESULT["replay"] = replayed
		print("\n" + cold.render())
		if replayed is not None:
			print("\n" + replayed.render())
	return _RESULT["cold"], _RESULT["replay"]


def test_the_soak_prints_a_readable_block() -> None:
	"""The deliverable is the printed block; this asserts it exists and names every check."""
	cold, replayed = _reports()
	rendered = cold.render()
	assert "actual=" in rendered and "expected=" in rendered
	assert replayed is not None
	for check in cold.checks + replayed.checks:
		assert check.status in {"PASS", "FAIL", "SKIPPED"}
		if check.status == "SKIPPED":
			assert check.note, f"a SKIPPED check must say what would evaluate it: {check.name}"


def test_no_assertion_was_silently_omitted() -> None:
	"""Every assertion the brief names has a line, and the composition really happened.

	A gate is only as strong as the checks that ran. Deleting a check makes the block *greener*,
	which is the wrong incentive, so the inventory is asserted by name — including the pieces of
	the §9 composition (bursts, outage, redeliveries, out-of-order pairs, the killed worker)
	whose absence would leave every other assertion trivially satisfiable.
	"""
	cold, replayed = _reports()
	names = " | ".join(c.name for c in cold.checks + (replayed.checks if replayed else []))
	for required in (
		"rows: Chat Message count",
		"rows: deletes tombstoned",
		"dupes: duplicate gchat_message_name",
		"dupes: Relayed rows with no gchat_message_name",
		"chat: messages in the fake spaces",
		"chat: duplicate Chat messages",
		"state: messages still in a non-terminal sync_state",
		"state: relay jobs Dead",
		"order:",
		"seam: notify_new_message calls",
		"audit: Edit revisions",
		"audit: Delete revisions",
		"audit: revisions carrying pre- and post-bodies",
		"arrival: erpnext-origin reached Chat",
		"arrival: chat-origin reached ERPNext",
		"attachments: Chat Attachment rows",
		"attachments: inbound DRIVE_FILE stored as a reference",
		"attachments: oversized outbound never crossed the wire",
		"chaos: bursts of 10 in 2s",
		"chaos: 60s outage backlog drained",
		"chaos: abandoned lease was reaped",
		"chaos: updated-before-created",
		"chaos: deleted-before-created",
		"chaos: every 10th inbound event delivered twice",
		"replay: messages unchanged",
		"replay: chat_messages unchanged",
		"replay: notify_new_message calls added",
		"replay: message rows byte-identical",
		"live: ~20-message round trip",
	):
		assert required in names, f"the soak is missing the assertion {required!r}"


def test_the_cold_run_has_no_regressions() -> None:
	"""Nothing fails except the recorded gaps.

	Skipped checks are permitted and are asserted elsewhere to carry a note saying what would
	evaluate them — there is exactly one, the live round trip, and it is skipped because this
	environment has no Google credentials rather than because the harness is weak. Anything
	else appearing as SKIPPED is a check that quietly stopped running, which the name-inventory
	test next door is what catches.
	"""
	cold, _replay = _reports()
	assert not cold.regressions, "\n" + cold.render()
	assert [c.name for c in cold.skipped] == ["live: ~20-message round trip against a real Chat space"], (
		"\n" + cold.render()
	)


def test_the_recorded_gaps_are_exactly_the_ones_still_failing() -> None:
	"""The gap list cannot rot in either direction.

	A gap that gets **fixed** without its entry being deleted fails here, which is the half
	that normally rots: a stale "known failure" is how a suite ends up tolerating a check it
	could be enforcing. A gap that gets **wider** — a second check falling into the same
	missing wiring — fails as a regression next door.
	"""
	from erpnext_enhancements.chat.testing.soak import KNOWN_GAPS

	cold, replayed = _reports()
	still_failing = {c.name for c in cold.known_gaps_hit}
	if replayed is not None:
		still_failing |= {c.name for c in replayed.known_gaps_hit}
	assert still_failing == set(KNOWN_GAPS), (
		"soak.KNOWN_GAPS is out of date. Fixed and should be deleted from it: "
		f"{sorted(set(KNOWN_GAPS) - still_failing)}"
	)


def test_the_replay_run_changes_nothing() -> None:
	"""Reprocessing the whole backlog is a no-op, apart from the recorded gaps."""
	_cold, replayed = _reports()
	assert replayed is not None
	assert not replayed.regressions, "\n" + replayed.render()


def main() -> int:  # pragma: no cover - the operator entry point
	"""Print both blocks and exit non-zero **only on a regression**.

	Not on a recorded gap and not on the live-run skip: those are known, named and listed in
	``soak.KNOWN_GAPS``, and an entry point that exited 1 for them would be an entry point
	nobody's shell script could distinguish from a real break.
	"""
	from erpnext_enhancements.chat.testing.soak import KNOWN_GAPS

	cold, replayed = run_bench_free()
	print(cold.render())
	if replayed is not None:
		print(replayed.render())

	regressions = list(cold.regressions) + list(replayed.regressions if replayed else [])
	gaps = list(cold.known_gaps_hit) + list(replayed.known_gaps_hit if replayed else [])
	print()
	print(
		f"VERDICT: {'REGRESSION' if regressions else 'no regressions'} · "
		f"{len(gaps)} recorded gap(s) still open of {len(KNOWN_GAPS)} · "
		f"{len(cold.skipped)} check(s) this environment cannot evaluate"
	)
	for check in regressions:
		print(f"  REGRESSION: {check.name} — actual {check.actual!r}, expected {check.expected!r}")
	return 1 if regressions else 0


if __name__ == "__main__":  # pragma: no cover
	raise SystemExit(main())
