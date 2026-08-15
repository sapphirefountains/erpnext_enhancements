"""Bench-free contracts for the Phase 3 chat HTTP surface — the read/write API the SPA runs on.

**Shape P: plain pytest functions.** It needs its own ``python -m pytest`` step in
``ci.yml``. ``python -m unittest`` cannot collect a file of plain functions — it collects
nothing and reports success — and this repo has already lost a suite that way for weeks.

What is actually being defended here, and what deliberately is not.

**Is:** the shapes and the rules that can be checked without a database. Every serialiser
strips the body off a deleted row (the row keeps its ``text`` on purpose — Google's
tombstone is content-free, so ERPNext is the only copy — and one read path forgetting to
strip it is a leak of exactly the thing the user chose to delete). Page sizes are clamped so
``limit=100000`` is not a denial of service written as a query parameter. Search escapes the
user's own LIKE wildcards, so somebody searching for ``100%`` does not match every message
in the company. Mentions of non-members are dropped rather than stored. The keyset paging
uses ``seq`` and never a timestamp, and never ``OFFSET``.

**Is not:** authorisation. Every endpoint's gate runs through
``chat.permissions.chat_room_has_permission``, whose real behaviour needs a database and a
session and is covered by ``tests/test_chat_permissions_bench.py`` — which is bench-required
and, as of this phase, has still never been executed (``TASK-2026-01296``, open since Phase
1). That gap is stated in the phase report rather than papered over with a mock here: a
permission test against a stub proves the call is made, not that it refuses.

No bench, no network, no database. Nothing here contains real employee content.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Bench guard, then the frappe stub
# ---------------------------------------------------------------------------


def _real_frappe_is_installed() -> bool:
	"""Is there an actual ``frappe`` package importable here, as opposed to a stub?"""
	try:
		spec = importlib.util.find_spec("frappe")
	except (ImportError, ValueError):
		return False
	return bool(spec and spec.origin)


if _real_frappe_is_installed():  # pragma: no cover - only true on a bench
	pytest.skip(
		"bench-free suite: a real frappe is installed, and stubbing over it would break "
		"the rest of the bench run",
		allow_module_level=True,
	)


def _install_frappe_stub() -> types.ModuleType:
	"""The smallest ``frappe`` the modules under test import at module scope.

	Deliberately minimal. Everything that would touch a database raises, so a test that
	accidentally exercises a query path fails loudly rather than passing against a mock that
	quietly answered ``[]``.
	"""
	frappe = types.ModuleType("frappe")

	def _not_here(*_args: Any, **_kwargs: Any) -> Any:
		raise AssertionError(
			"this bench-free suite reached a database call. Either the test is exercising a "
			"path that needs a bench (move it to the bench-required tier and say so), or a "
			"pure function grew a query."
		)

	class _DB:
		sql = staticmethod(_not_here)
		get_value = staticmethod(_not_here)
		get_all = staticmethod(_not_here)
		exists = staticmethod(_not_here)
		set_value = staticmethod(_not_here)
		get_single_value = staticmethod(_not_here)
		get_singles_dict = staticmethod(_not_here)

		@staticmethod
		def escape(value: Any) -> str:
			# Faithful enough for the fragment assertions: single quotes, doubled inside.
			return "'" + str(value).replace("'", "''") + "'"

	frappe.db = _DB()
	frappe.get_all = staticmethod(_not_here)
	frappe.session = types.SimpleNamespace(user="tester@example.com")
	frappe.local = types.SimpleNamespace(site="test.local")
	frappe.flags = types.SimpleNamespace(in_install=False, in_migrate=False)
	frappe.cache = lambda: _not_here()
	frappe._ = lambda text: text
	frappe.throw = _not_here
	frappe.log_error = lambda **_kwargs: None
	frappe.get_roles = lambda _user: []
	frappe.parse_json = staticmethod(lambda value: __import__("json").loads(value))
	frappe.new_doc = staticmethod(_not_here)
	frappe.get_doc = staticmethod(_not_here)

	# A pass-through decorator. The real one registers the function in Frappe's whitelist; the
	# only thing this suite needs is for the decorated functions to still BE functions, so the
	# ast walk below can find them and so importing the module does not explode.
	def _whitelist(*_args: Any, **_kwargs: Any):
		def wrap(fn):
			fn.is_whitelisted = True
			return fn

		return wrap

	frappe.whitelist = _whitelist

	class _PermissionError(Exception):
		pass

	class _ValidationError(Exception):
		pass

	frappe.PermissionError = _PermissionError
	frappe.ValidationError = _ValidationError
	frappe.DoesNotExistError = _ValidationError
	frappe.UniqueValidationError = _ValidationError
	frappe.DuplicateEntryError = _ValidationError

	utils = types.ModuleType("frappe.utils")

	def _cint(value: Any) -> int:
		try:
			return int(float(value))
		except (TypeError, ValueError):
			return 0

	utils.cint = _cint
	utils.get_datetime_str = lambda value: str(value)
	utils.now_datetime = lambda: "2026-08-10 12:00:00"
	utils.get_url = lambda path="": "https://erp.example.com" + str(path)
	utils.get_url_to_form = lambda dt, dn: f"/app/{dt.lower().replace(' ', '-')}/{dn}"
	utils.quoted = lambda value: str(value)

	# `chat/realtime.py` imports `from frappe.realtime import get_doc_room, get_user_room` at
	# module scope — deliberately, so the room names it builds are the ones Frappe's own node
	# server builds rather than a second hand-rolled copy. That import is what pulls the
	# submodule in here; the two helpers are reproduced faithfully because
	# `test_chat_realtime_targeting.py` already proves the real targeting chain and this suite
	# only needs the names to resolve.
	realtime = types.ModuleType("frappe.realtime")
	realtime.get_doc_room = lambda doctype, name: f"doc:{doctype}/{name}"
	realtime.get_user_room = lambda user: f"user:{user}"
	realtime.get_site_room = lambda: "all"
	frappe.publish_realtime = lambda **_kwargs: None

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.realtime"] = realtime
	frappe.utils = utils
	frappe.realtime = realtime
	return frappe


FRAPPE = _install_frappe_stub()

from erpnext_enhancements.chat import audit, links, permissions  # noqa: E402
from erpnext_enhancements.chat.api import (  # noqa: E402
	_common,
	compose,
	conversations,
	history,
	mentions,
	search,
)


# ---------------------------------------------------------------------------
# The decision #12 audit — the pure half, which is the half that can be wrong silently
# ---------------------------------------------------------------------------


def test_the_query_hash_is_sha256_of_the_query() -> None:
	"""The hash is stored; the text is not, unless a flag that ships off says otherwise.

	The query a manager types is itself content — "did anyone mention my name", typed by the
	person about to run a redundancy, is not metadata.
	"""
	import hashlib

	assert audit.query_hash("pump housing") == hashlib.sha256(b"pump housing").hexdigest()
	# None and "" hash the same, and that is fine: both mean "no query", and the alternative
	# is a null that reads as "we forgot to record it".
	assert audit.query_hash(None) == audit.query_hash("")


def test_the_chain_hash_commits_to_the_fields_that_matter() -> None:
	"""Change any audited field and the hash moves. That is the whole mechanism.

	A chain that does not cover a field is a field an editor can rewrite for free, so each one
	is checked individually rather than by changing the row wholesale — a single combined
	assertion would pass if only ONE field were covered.
	"""
	row = {
		"accessed_by": "auditor@example.com",
		"actor_type": "Admin",
		"purpose": "search",
		"request_id": "req-1",
		"reason": "investigating a complaint",
		"query_hash": audit.query_hash("pump"),
		"message_count": 3,
		"recorded_at": "2026-08-10 12:00:00.000000",
	}
	rooms = [{"room": "R1", "was_participant": 0, "messages_read": 3, "first_seq": 1, "last_seq": 3}]
	base = audit.compute_chain_hash(row, "prev", rooms)

	assert audit.compute_chain_hash(dict(row), "prev", list(rooms)) == base, "not deterministic"

	for field, replacement in [
		("accessed_by", "someone.else@example.com"),
		("actor_type", "User"),
		("purpose", "oversight"),
		("request_id", "req-2"),
		("reason", "no reason at all"),
		("query_hash", audit.query_hash("housing")),
		("message_count", 4),
		("recorded_at", "2026-08-10 12:00:01.000000"),
	]:
		mutated = dict(row)
		mutated[field] = replacement
		assert audit.compute_chain_hash(mutated, "prev", rooms) != base, (
			f"{field} is not covered by the chain, so it can be rewritten without detection"
		)


def test_the_chain_covers_the_rooms_and_the_participation_flag() -> None:
	"""``was_participant`` is the field the log exists for, so it must be signed.

	A tamperer who could flip it to 1 could turn every non-participant read into an ordinary
	one, which is precisely the evidence decision #12 wants preserved.
	"""
	row = {"accessed_by": "a@b.c", "actor_type": "Admin", "purpose": "oversight", "creation": "x"}
	was_not = [{"room": "R1", "was_participant": 0, "messages_read": 2, "first_seq": 1, "last_seq": 2}]
	was = [{"room": "R1", "was_participant": 1, "messages_read": 2, "first_seq": 1, "last_seq": 2}]

	assert audit.compute_chain_hash(row, "p", was_not) != audit.compute_chain_hash(row, "p", was)
	# And a room disappearing from the list must show up too.
	assert audit.compute_chain_hash(row, "p", was_not) != audit.compute_chain_hash(row, "p", [])


def test_the_chain_links_to_its_predecessor() -> None:
	"""Same row, different predecessor, different hash — otherwise it is a per-row checksum
	and rows could be reordered or deleted wholesale without detection."""
	row = {"accessed_by": "a@b.c", "actor_type": "Admin", "purpose": "oversight", "creation": "x"}
	assert audit.compute_chain_hash(row, "prev-a", []) != audit.compute_chain_hash(row, "prev-b", [])


def test_the_chain_survives_the_round_trip_through_the_database() -> None:
	"""The writer hashes a timestamp STRING; the verifier reads a ``datetime`` back.

	``str(datetime)`` drops ``.000000`` when the microseconds are zero and
	``frappe.utils.now()`` never does, so without normalisation roughly one row in a million
	reports as tampered for no reason — the worst kind of intermittent, on the one log whose
	entire value is being trustworthy.
	"""
	import datetime as _dt

	stamp = _dt.datetime(2026, 8, 11, 3, 52, 11, 123456)
	as_written = "2026-08-11 03:52:11.123456"        # what frappe.utils.now() produces
	row = {"accessed_by": "a@b.c", "actor_type": "Admin", "purpose": "oversight"}

	assert audit.compute_chain_hash({**row, "recorded_at": as_written}, "prev", []) == (
		audit.compute_chain_hash({**row, "recorded_at": stamp}, "prev", [])
	), "the writer's string and the database's datetime hash differently"

	midnight = _dt.datetime(2026, 8, 11, 0, 0, 0, 0)
	assert audit.compute_chain_hash({**row, "recorded_at": midnight}, "p", []) == (
		audit.compute_chain_hash({**row, "recorded_at": "2026-08-11 00:00:00.000000"}, "p", [])
	), "a whole-second timestamp hashes differently as a datetime than as a string"


def test_the_chain_signs_a_timestamp_frappe_cannot_overwrite() -> None:
	"""**The v1.268.0 bug, and why the fix is a new field rather than a better assignment.**

	``Document.insert()`` calls ``set_user_and_timestamp()`` before anything else, and that
	assigns ``creation = modified = now()`` unconditionally for a new document. A
	caller-supplied ``creation`` therefore cannot survive — which is why the first attempt at
	this fix, ``doc.creation = row["creation"]``, was inert. Signing ``creation`` meant
	signing a value the database never stored, and ``verify_chain`` reported the very first
	row ever written as tampered. Confirmed on production against a real row before this was
	rewritten.

	So the chain signs ``recorded_at``, a field of this app's own that Frappe has no opinion
	about, and ``creation`` is not signed at all.
	"""
	import ast
	import inspect
	import textwrap

	assert "recorded_at" in audit._CHAINED_FIELDS
	assert "creation" not in audit._CHAINED_FIELDS, (
		"creation is signed again. Frappe overwrites it during insert, so the signed value "
		"and the stored value cannot be the same instant."
	)

	# And the writer must actually put it on the document, or the column is null and reqd
	# validation fails on the first write.
	written = ast.parse(textwrap.dedent(inspect.getsource(audit._write)))
	keys = {
		k.value
		for node in ast.walk(written)
		if isinstance(node, ast.Dict)
		for k in node.keys
		if isinstance(k, ast.Constant)
	}
	assert "recorded_at" in keys, "_write never sets recorded_at, so the reqd column is null"


def test_the_canonical_form_is_stable_against_key_order() -> None:
	"""Writer and verifier must agree byte for byte, or every row reports as tampered — which
	trains people to ignore the alarm, and is worse than having no chain."""
	assert audit._canonical({"b": 1, "a": 2}) == audit._canonical({"a": 2, "b": 1})
	assert " " not in audit._canonical({"a": 1, "b": 2})


def test_a_read_collapses_its_hits_into_one_audit_entry_per_room() -> None:
	"""The audit records the seq RANGE read per room, not one entry per message.

	"They looked at that room once" is not a useful audit record; "they read seq 4 through 91
	of a room they were not in" is.

	Lives in ``_common`` rather than in ``search`` since v1.283.3, because four readers need it
	— search and the three history paths — and *"which rooms did this read touch, over which
	range"* is one question with one right answer.
	"""
	rows = [
		{"room": "R1", "seq": 9},
		{"room": "R1", "seq": 4},
		{"room": "R2", "seq": 30},
		{"room": "R1", "seq": 7},
	]
	entries = {e["room"]: e for e in _common.audited_room_ranges(rows)}
	assert set(entries) == {"R1", "R2"}
	assert entries["R1"]["messages_read"] == 3
	assert entries["R1"]["first_seq"] == 4
	assert entries["R1"]["last_seq"] == 9
	assert entries["R2"]["messages_read"] == 1
	# was_participant is deliberately absent: the writer resolves it against live membership,
	# so there is one answer to "were they a member" rather than one per caller.
	assert "was_participant" not in entries["R1"]


def test_the_chain_is_serialised_and_refuses_rather_than_forking() -> None:
	"""Two overlapping privileged reads must not both sign the same predecessor.

	Without serialisation they do, the chain forks, and ``verify_chain`` reports a permanent
	break that is indistinguishable from tampering. This is not a rare race: the SPA issues
	its room list, unread counts and transcript as parallel requests, so an oversight user's
	first page load is enough.

	Structural, because the real thing needs two concurrent database connections and CI has no
	bench. Three properties, each of which the other two do not imply.
	"""
	import ast
	import inspect
	import textwrap

	fn = ast.parse(textwrap.dedent(inspect.getsource(audit._write))).body[0]

	# 1. The lock is taken, reachably.
	guards = [
		node
		for node in ast.walk(fn)
		if isinstance(node, ast.If)
		and not (isinstance(node.test, ast.Constant) and not node.test.value)
		and "_acquire_chain_lock" in ast.dump(node.test)
	]
	assert guards, "_write does not reachably acquire the chain lock, so writers can fork it"

	# 2. Failing to take it REFUSES. Proceeding unlocked is the fork.
	raises = [n for g in guards for n in ast.walk(g) if isinstance(n, ast.Raise)]
	assert raises, (
		"_write proceeds when the chain lock cannot be acquired. A row signed against a stale "
		"head is a permanent false 'tampered' verdict on the one log whose job is to be "
		"trustworthy — refusing the read is the lesser failure."
	)

	# 3. It is released on every path, including the raising one.
	tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
	released = any(
		"_release_chain_lock" in ast.dump(stmt) for t in tries for stmt in t.finalbody
	)
	assert released, "_write does not release the chain lock in a finally; an exception strands it"


def test_the_endpoint_records_and_the_hook_only_marks() -> None:
	"""**The rule the v1.268.0 redesign turns on.**

	``search_messages`` is about to return message bodies, so it records first and refuses to
	return if it cannot. ``note_privileged_read`` runs inside permission hooks — where a write
	commits inside whatever transaction the request was already building, and where the
	permission answer is not even known yet — so it marks memory and writes nothing.
	"""
	import ast
	import inspect
	import textwrap

	assert "audit.record_or_refuse" in _called_names(search, "search_messages")

	hook_calls = _called_names(permissions, "note_privileged_read")
	assert "audit.mark_privileged_scope" in hook_calls
	for forbidden in ("audit.record_privileged_read", "audit.record_or_refuse"):
		assert forbidden not in hook_calls, (
			f"note_privileged_read calls {forbidden}. A permission hook must not write: "
			f"announce_unread is a Chat Message.after_insert, so the commit lands inside the "
			f"relay's transaction."
		)

	def _fn(func: Any) -> ast.FunctionDef:
		return ast.parse(textwrap.dedent(inspect.getsource(func))).body[0]

	# The fail-closed one must REACHABLY refuse. Asserting only that the source mentions
	# `frappe.throw` passes for `if False: frappe.throw(...)` — which is exactly the mutation
	# that slipped through the first version of this test.
	refuse = _fn(audit.record_or_refuse)
	guarded_throws = [
		node
		for node in ast.walk(refuse)
		if isinstance(node, ast.If)
		and not (isinstance(node.test, ast.Constant) and not node.test.value)
		and any(
			isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "throw"
			for c in ast.walk(node)
		)
	]
	assert guarded_throws, "record_or_refuse has no reachable frappe.throw — it cannot refuse"
	assert not [n for n in ast.walk(refuse) if isinstance(n, ast.ExceptHandler)], (
		"record_or_refuse swallows, so it is not fail-closed"
	)

	# ...and the hook-facing one must catch BROADLY, or a permission hook can raise and take
	# down every desk page touching a chat DocType. A narrowed `except ValueError` is the
	# realistic regression, so the handler type is checked rather than the word "except".
	swallow = _fn(audit.record_privileged_read)
	# The OUTERMOST try only — a direct child of the function body. Walking the whole tree
	# finds the inner `except Exception: pass` around the log_error call and passes even when
	# the real handler has been narrowed to `except ValueError`, which was the miss.
	outer = [n for n in swallow.body if isinstance(n, ast.Try)]
	assert outer, "record_privileged_read no longer wraps its work in a try at all"
	broad = [
		h
		for t in outer
		for h in t.handlers
		if h.type is None or getattr(h.type, "id", "") == "Exception"
	]
	assert broad, (
		"record_privileged_read's outer handler has been narrowed; anything it does not catch "
		"is raised out of a permission hook, which denies the read at best and breaks every "
		"desk page touching a chat DocType at worst"
	)


# ---------------------------------------------------------------------------
# The tombstone rule — one serialiser, so it cannot be forgotten in five places
# ---------------------------------------------------------------------------


def test_a_deleted_row_never_emits_its_body() -> None:
	"""``is_deleted = 1`` rows KEEP their ``text`` in the database, on purpose.

	Google's tombstone is content-free, so ERPNext is the only record of what was said and
	Phase 6's audit needs it. That makes the serialiser the single place the body is
	withheld — and the reason it is a single place rather than a rule four read paths have to
	remember.
	"""
	row = {
		"name": "M1",
		"room": "R1",
		"seq": 7,
		"text": "the thing they wish they had not said",
		"is_deleted": 1,
	}
	payload = _common.message_payload(row)
	assert payload["text"] == ""
	assert payload["deleted"] is True
	assert payload["is_deleted"] is True
	assert "the thing" not in str(payload)


def test_a_live_row_emits_its_body() -> None:
	payload = _common.message_payload({"name": "M1", "room": "R1", "seq": 7, "text": "hello"})
	assert payload["text"] == "hello"
	assert payload.get("deleted") is None


def test_every_message_payload_carries_a_mentions_key() -> None:
	"""The renderers call ``tokenizeMentions(text, message.mentions || [])``.

	``message_payload`` builds an explicit literal dict — no ``**row`` spread — so a key that
	is not written here can never reach them no matter what the query selects. It shipped
	without one, and the result was that **no ``@mention`` had ever rendered as a mention** on
	either surface: the spans were stored, one read path used them for the room-list badge, and
	the transcript got plain text with a CSS class that could not match.

	An empty list means "no mentions". It never means "not loaded" — see
	``history._attach_mentions``, which fills it for every transcript row.
	"""
	payload = _common.message_payload({"name": "M1", "room": "R1", "seq": 1, "text": "hi @Jane"})
	assert payload["mentions"] == []


def test_a_mention_span_is_offsets_and_never_markup() -> None:
	"""Same rule as the search snippet, for the same reason: the client slices, never parses.

	``user`` is ``None`` for a Triton mention, which names no ``User`` row.
	"""
	span = _common.mention_payload(
		{"mention_type": "User", "user": "jane@example.com", "start_index": "3", "length": "5"}
	)
	assert span == {
		"mention_type": "User",
		"user": "jane@example.com",
		"start_index": 3,
		"length": 5,
	}
	assert "<" not in str(span)

	triton = _common.mention_payload({"mention_type": "Triton", "start_index": 0, "length": 7})
	assert triton["user"] is None


def test_every_transcript_path_hydrates_mentions() -> None:
	"""All three of them, asserted structurally rather than by reading the code once.

	A page that skips the hydration is not visibly broken — it renders, with the mention chips
	silently missing — so "the one I remembered to wire" is exactly how this regresses.
	"""
	for function in ("get_messages", "get_thread", "get_message_context"):
		assert "_attach_mentions" in _called_names(history, function), (
			f"history.{function} does not hydrate mention spans, so mentions in that view render "
			f"as plain text"
		)

	# And the send response, or the sender's own chips vanish the moment the server acks —
	# `reconcile()` assigns the response over the optimistic entry that had them.
	assert "_attach_mentions" in _called_names(compose, "_sent")


def test_a_null_sender_is_rendered_from_sender_email() -> None:
	"""An external Chat participant has no ``User`` link, and the client must not assume one."""
	payload = _common.message_payload(
		{"name": "M1", "room": "R1", "seq": 1, "sender": None, "sender_email": "ext@partner.com"}
	)
	assert payload["sender"] is None
	assert payload["sender_email"] == "ext@partner.com"


def test_unread_is_derived_arithmetically_rather_than_counted() -> None:
	"""``seq`` is dense per room, so the subtraction IS the count and no join is needed."""
	payload = _common.room_payload(
		{"name": "R1", "room_type": "Group", "seq_high_water": 30}, member={"last_read_seq": 12}
	)
	assert payload["unread"] == 18
	# Never negative: a member whose read mark is ahead of the room (their own message, marked
	# read by the insert, racing the room denormalisation) reads as zero, not as -1.
	ahead = _common.room_payload({"name": "R1", "seq_high_water": 5}, member={"last_read_seq": 9})
	assert ahead["unread"] == 0


# ---------------------------------------------------------------------------
# Page sizes — a browser supplies these
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	("supplied", "expected"),
	[
		(None, _common.DEFAULT_PAGE_SIZE),
		("", _common.DEFAULT_PAGE_SIZE),
		(0, _common.DEFAULT_PAGE_SIZE),
		(-5, _common.DEFAULT_PAGE_SIZE),
		(10, 10),
		("25", 25),
		(100000, _common.MAX_PAGE_SIZE),
	],
)
def test_page_size_is_clamped(supplied: Any, expected: int) -> None:
	"""``limit`` arrives from a query string. ``limit=100000`` is a denial of service."""
	assert _common.page_size(supplied) == expected


# ---------------------------------------------------------------------------
# The membership fragment — the thing every raw query ANDs in
# ---------------------------------------------------------------------------


def test_the_filter_never_returns_an_empty_string() -> None:
	"""An empty fragment disappears out of a hand-written WHERE and returns EVERYTHING.

	``permission_query_conditions`` may return ``""`` because the get_list engine drops it.
	``membership_filter_sql`` may not, and the difference is the whole reason there are two
	functions: unrestricted is spelled ``1 = 1`` and denied is spelled ``1 = 0``, both of
	which survive being concatenated by a careless caller.
	"""
	FRAPPE.session.user = "Guest"
	assert permissions.membership_filter_sql("`m`.`room`") == "1 = 0"
	FRAPPE.session.user = ""
	assert permissions.membership_filter_sql("`m`.`room`") == "1 = 0"
	FRAPPE.session.user = "tester@example.com"
	fragment = permissions.membership_filter_sql("`m`.`room`", "tester@example.com")
	assert fragment
	assert "tabChat Room Member" in fragment
	assert "'tester@example.com'" in fragment, "the user must be escaped, not interpolated raw"


def test_the_filter_escapes_a_quote_in_the_user_name() -> None:
	"""A permission hook is the last place in a codebase you want an injection."""
	fragment = permissions.membership_filter_sql("`m`.`room`", "o'brien@example.com")
	assert "o''brien" in fragment


# ---------------------------------------------------------------------------
# Search — the endpoint most likely to leak, and its LIKE escaping
# ---------------------------------------------------------------------------


def test_search_refuses_a_query_too_short_to_be_a_search() -> None:
	assert search.MIN_QUERY_CHARS == 2


def test_search_snippet_carries_offsets_rather_than_pre_wrapped_html() -> None:
	"""Message bodies are user-authored. Handing the client HTML to ``innerHTML`` is the
	stored-XSS vector; offsets plus ``textContent`` slicing is not."""
	row = {
		"name": "M1",
		"room": "R1",
		"seq": 3,
		"text_plain": "the pump housing is cracked and the pump seal is fine",
		"room_title": "Riverwalk",
		"sender_name": "Jane",
	}
	result = search._result(row, "pump", "jane@example.com")
	assert "<mark>" not in result["snippet"]
	assert result["match_length"] == len("pump")
	assert result["snippet"][result["match_start"] : result["match_start"] + 4] == "pump"


def test_a_search_hit_in_a_dm_is_labelled_with_the_counterpart_not_the_docname() -> None:
	"""A DM has no stored title, and ``Chat Room`` is ``autoname: hash``.

	``search.py`` is the one payload builder that does not go through ``room_payload``, so it
	emitted ``""`` for every DM hit — and the client's ``hit.room_title || hit.room`` fallback
	turned that into a ten-character random hash sitting where the conversation name belongs.

	Without a database ``dm_counterpart`` cannot resolve the display name and degrades to the
	docname, which is the documented behaviour. What matters here, and what is actually
	asserted, is that the field is **not empty** — an empty ``room_title`` is what let the
	client fall through to the hash, and it is the failure this reproduces.
	"""
	row = {
		"name": "M1",
		"room": "b7f2c91a04",
		"seq": 3,
		"text_plain": "pump",
		"room_title": None,
		"room_type": "Direct Message",
		"dm_user_1": "jane@example.com",
		"dm_user_2": "james@example.com",
	}
	result = search._result(row, "pump", "jane@example.com")
	assert result["room_title"], "a DM hit must carry a label, or the client renders the docname"
	assert result["room_title"] != row["room"]
	# The counterpart, not the viewer: a DM is named after whoever you are not.
	assert result["room_title"] == "james@example.com"

	# And from the other side, the same room resolves to the other name.
	mirrored = search._result(row, "pump", "james@example.com")
	assert mirrored["room_title"] == "jane@example.com"


def test_a_group_search_hit_keeps_its_stored_title() -> None:
	"""The DM resolution must not disturb the ordinary case."""
	row = {
		"name": "M1",
		"room": "R1",
		"seq": 3,
		"text_plain": "pump",
		"room_title": "Riverwalk",
		"room_type": "Group",
	}
	assert search._result(row, "pump", "jane@example.com")["room_title"] == "Riverwalk"


def test_search_result_carries_the_shared_deep_link() -> None:
	"""One builder, three consumers. A second URL-shape implementation is how a notification
	lands on an error page while the SPA's own links work fine."""
	row = {"name": "M1", "room": "R1", "seq": 3, "text_plain": "pump", "thread_root": "T1"}
	result = search._result(row, "pump", "jane@example.com")
	assert result["route"] == links.build_chat_route("R1", message="M1", thread="T1")
	assert result["route"] == "/chat/room/R1?message=M1&thread=T1"


# ---------------------------------------------------------------------------
# The deep-link builder — byte-identical to the client's, because Phase 4 compares them
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	("kwargs", "expected"),
	[
		({}, "/chat/room/R1"),
		({"message": "M1"}, "/chat/room/R1?message=M1"),
		({"thread": "T1"}, "/chat/room/R1?thread=T1"),
		({"message": "M1", "thread": "T1"}, "/chat/room/R1?message=M1&thread=T1"),
		# Order is fixed at message-then-thread whichever order the caller passes them.
		({"thread": "T1", "message": "M1"}, "/chat/room/R1?message=M1&thread=T1"),
		# Empty values are dropped rather than emitted as `?message=`.
		({"message": "", "thread": "  "}, "/chat/room/R1"),
	],
)
def test_the_route_shape_is_byte_stable(kwargs: dict[str, Any], expected: str) -> None:
	"""These bytes are compared for equality by Phase 4's notification dedupe and stored in
	``Notification Log.link``. ``public/js/chat/routes.js`` asserts the same table."""
	assert links.build_chat_route("R1", **kwargs) == expected


def test_a_room_name_with_a_slash_cannot_retarget_the_link() -> None:
	assert links.build_chat_route("a/b") == "/chat/room/a%2Fb"


# ---------------------------------------------------------------------------
# Mentions — offsets, and the non-member rule
# ---------------------------------------------------------------------------


def test_mention_offsets_outside_the_body_are_dropped_not_clamped() -> None:
	"""A clamped offset renders a chip over the wrong words, which looks like corruption."""
	rows = mentions.extract_mentions(
		"hi @Jane",
		[
			{"kind": "user", "user": "jane@x.com", "start": 3, "length": 5},
			{"kind": "user", "user": "sam@x.com", "start": 3, "length": 500},
			{"kind": "user", "user": "ali@x.com", "start": -1, "length": 4},
		],
	)
	assert [row["user"] for row in rows] == ["jane@x.com"]


def test_a_triton_mention_carries_no_user_link() -> None:
	rows = mentions.extract_mentions("@Triton help", [{"kind": "triton", "start": 0, "length": 7}])
	assert rows == [{"mention_type": "Triton", "user": None, "start_index": 0, "length": 7}]


def test_triton_is_offered_for_a_prefix_and_not_for_an_unrelated_one() -> None:
	"""A substring rule would put the assistant in the menu every time somebody mentioned a
	colleague with a 't' in their name."""
	assert mentions._triton_matches("") is True
	assert mentions._triton_matches("tri") is True
	assert mentions._triton_matches("TRITON") is True
	assert mentions._triton_matches("jane") is False


# ---------------------------------------------------------------------------
# Paging — seq, never a timestamp; keyset, never OFFSET
# ---------------------------------------------------------------------------


def test_history_pages_on_seq_and_never_uses_offset() -> None:
	"""``OFFSET`` over a growing table gets slower the further back you scroll and can skip or
	duplicate rows when new messages arrive mid-scroll — which presents as "a message went
	missing when I scrolled up" and is unreproducible on a quiet site.

	``seq`` rather than ``creation`` because ``seq`` is allocated under the room's row lock,
	is immutable and has no timezone; a late-arriving inbound message still gets the next
	``seq`` and renders in ``seq`` order rather than being re-sorted into the past.
	"""
	# The SQL literals only — a docstring that DISCUSSES why `offset` is wrong must not read
	# as a violation. That distinction is exactly how a guard gets muted with a broad
	# exemption and then stops guarding.
	statements = [text.lower() for text in _sql_literals(history) if "select" in text.lower()]
	assert statements, "no SQL literals found in history.py; the extraction has stopped working"

	transcript = [s for s in statements if "`tabchat message`" in s]
	assert transcript, "no Chat Message queries found; the extraction has stopped working"

	for statement in statements:
		assert " offset " not in statement, f"keyset paging only, found OFFSET in: {statement[:80]}"

	for statement in transcript:
		# Attachments legitimately order by `creation` — a Chat Attachment has no `seq`, and
		# the order that matters there is the order they were uploaded within one message. The
		# rule is about the TRANSCRIPT, so it is asserted only on Chat Message reads.
		#
		# Child-row fetches are the same carve-out for the same reason. `_attach_mentions`
		# joins `tabChat Message` only to reach `room` for the membership filter; its rows are
		# spans WITHIN one message and their meaningful order is by offset into the body. It is
		# not a page of messages and `seq` would order it by nothing.
		#
		# Deliberately narrow: keyed on the child table actually being selected, not a blanket
		# "unless the statement looks unusual". A page of messages that stopped ordering by
		# `seq` still fails, which is the whole point of the rule.
		if "`tabchat mention`" in statement:
			continue
		if "order by" in statement:
			ordering = statement.split("order by", 1)[1]
			assert "`seq`" in ordering, (
				"every transcript ordering is by `seq`. Not `creation`, not Google's "
				"`createTime`: `seq` is allocated under the room's row lock, is immutable and "
				f"has no timezone. Found: {ordering[:60]}"
			)
			assert "`creation`" not in ordering


def test_a_dm_is_probed_in_the_same_canonical_order_the_unique_index_enforces() -> None:
	"""The probe and the insert must both use the SORTED pair, or dedupe fails half the time.

	``unique(dm_user_1, dm_user_2)`` is a real index and ``ChatRoom.before_insert`` sorts the
	pair lexicographically, so a conversation between A and B is one row whoever starts it.
	That only holds if the *lookup* sorts too: probing ``{dm_user_1: me, dm_user_2: them}``
	unsorted finds the existing room exactly when the caller's address happens to sort first,
	and misses it — creating a second room, and eventually a second Google space for one pair
	of people — the other half of the time.

	Asserted on the source because the behaviour needs a database. What it catches is the
	shape that actually goes wrong: somebody "simplifying" the sort away.
	"""
	source = _module_source(conversations)
	assert "sorted([me, other])" in source, (
		"create_direct_message no longer sorts the pair before probing. See "
		"ChatRoom._order_dm_pair — the index is on the sorted pair."
	)
	# The same two names must feed the probe AND the inserted values, not two different orders.
	assert 'probe={"dm_user_1": first, "dm_user_2": second}' in source
	assert '"dm_user_1": first' in source
	assert '"dm_user_2": second' in source


def test_room_creation_goes_through_phase_2s_deduped_helpers() -> None:
	"""Not a second room creator. A collision on the unique index means SUCCESS.

	``_insert_room_deduped`` and ``insert_room_member`` both already treat a composite-unique
	collision as "somebody else got there first, return theirs". Re-implementing either is how
	two people clicking the same button at the same moment produce two rooms, and both of those
	docstrings exist because that rule was got wrong once.
	"""
	# Asserted on CALL nodes, not on the source text. The module docstring names both helpers
	# at length, so a substring scan passes even after the calls are ripped out — which is
	# exactly what a mutation test caught here. A guard that a comment can satisfy is not a
	# guard.
	called = _called_names(conversations)
	assert "provisioning._insert_room_deduped" in called, (
		"the DM path no longer calls the deduped room helper. A hand-rolled probe-then-insert "
		"is the TOCTOU that produces two rooms for one pair of people."
	)
	# Per FUNCTION, not per module: a room created with no member rows is a conversation
	# nobody can see, and module-wide presence is satisfied by whichever path still has it.
	for fn in ("create_direct_message", "create_group"):
		assert "membership.insert_room_member" in _called_names(conversations, fn), (
			f"{fn} no longer creates member rows through the one helper that treats a "
			"unique(room, user) collision as success. A room with no members is invisible "
			"to the person who just made it."
		)
	assert "frappe.get_doc" in called, "the group path builds a room doc"
	# A hand-rolled existence check in front of an insert is the TOCTOU the helpers exist to
	# close; it must not reappear here.
	assert "frappe.db.exists" not in called


def test_a_group_is_deliberately_not_deduplicated() -> None:
	"""Two group rooms may legitimately share a title. Only DMs and document rooms have an
	identity the database can enforce; a group's identity is that somebody made it."""
	source = _module_source(conversations)
	assert "not deduplicated" in source.lower() or "Not deduplicated" in source


def test_the_initial_roster_is_bounded() -> None:
	"""A blast-radius limit, not a schema limit: a fat-fingered select-all would eventually
	provision a large Google space, and this codebase deliberately has no ``spaces.delete``."""
	assert 1 < conversations.MAX_INITIAL_MEMBERS <= 100
	assert 1 < conversations.MAX_PEOPLE_RESULTS <= 50


@pytest.mark.parametrize(
	("supplied", "expected"),
	[
		(None, []),
		("", []),
		([], []),
		(["a@x.com", "b@x.com"], ["a@x.com", "b@x.com"]),
		('["a@x.com", " b@x.com "]', ["a@x.com", "b@x.com"]),
		("not json at all", []),
		({"0": "a@x.com"}, ["a@x.com"]),
		(["a@x.com", "", "   "], ["a@x.com"]),
	],
)
def test_member_lists_survive_both_wire_shapes(supplied: Any, expected: list[str]) -> None:
	"""``frappe.xcall`` sends arrays as JSON strings on some paths and lists on others."""
	assert conversations._coerce_list(supplied) == expected


def test_every_whitelisted_endpoint_gates_before_it_reads() -> None:
	"""One membership decision per request, taken once, at the top.

	Asserted structurally rather than by execution: the gate itself needs a database. What
	this catches is the shape that actually goes wrong — a new endpoint added next to these
	that forgets the line entirely.
	"""
	from erpnext_enhancements.chat.api import presence, readstate, rooms

	seen = 0
	for module in (history, compose, search, mentions, rooms, readstate, presence, conversations):
		tree = ast.parse(_module_source(module))
		for node in ast.walk(tree):
			if not isinstance(node, ast.FunctionDef):
				continue
			decorators = {_decorator_name(d) for d in node.decorator_list}
			if "frappe.whitelist" not in decorators:
				continue
			seen += 1
			body = ast.dump(node)
			assert (
				"require_room" in body or "require_message" in body or "require_session" in body
			), (
				f"{module.__name__}.{node.name} is whitelisted but never calls a gate. Every "
				"endpoint in chat/api resolves the caller and asserts membership before it "
				"reads anything; an endpoint that does not is reachable by any signed-in user."
			)

	# Not vacuous: if the decorator resolver stops matching, `seen` collapses to 0 and every
	# assertion above is skipped while this test still reports success. That is the exact
	# failure this repo has shipped twice.
	assert seen >= 21, (
		f"only {seen} whitelisted endpoints were found across chat/api. Either the surface "
		"genuinely shrank — lower this floor deliberately, in the same commit — or "
		"_decorator_name stopped resolving @frappe.whitelist() and this test is now green "
		"while checking nothing."
	)


def _decorator_name(node: Any) -> str:
	parts: list[str] = []
	target = node.func if isinstance(node, ast.Call) else node  # type: ignore[name-defined]
	while isinstance(target, ast.Attribute):
		parts.append(target.attr)
		target = target.value
	if isinstance(target, ast.Name):
		parts.append(target.id)
	return ".".join(reversed(parts))


def _module_source(module: Any) -> str:
	import pathlib

	return pathlib.Path(module.__file__).read_text(encoding="utf-8")


def _called_names(module: Any, function: str | None = None, *, follow_local: bool = True) -> set[str]:
	"""Every dotted callee name actually invoked — in the module, or in one function of it.

	Deliberately AST rather than text: these modules explain their own rules at length in
	docstrings, so a substring scan for ``provisioning._insert_room_deduped`` passes on the
	prose alone. Only a call counts.

	``function`` narrows it, which matters for a helper that several paths must ALL use:
	module-wide presence is satisfied by one surviving caller, so "the group path stopped
	creating member rows" reads as fine while it is not.

	``follow_local`` folds in the calls made by same-module private helpers the function calls,
	one level deep. **The property these tests assert is about the request**, not about which
	function in the file happens to contain the line: ``get_messages`` delegating its query to a
	private ``_page`` still hydrates mentions, and a scan that reported otherwise would be
	demanding a particular factoring rather than a behaviour. One level, not full recursion,
	because a rule nobody can evaluate by reading the function and its helper is a rule that
	stops being checked by hand.
	"""
	source = _module_source(module)
	module_tree = ast.parse(source)

	tree: Any = module_tree
	if function:
		tree = None
		for node in ast.walk(module_tree):
			if isinstance(node, ast.FunctionDef) and node.name == function:
				tree = node
		assert tree is not None, f"{module.__name__} has no function named {function}"

		if follow_local:
			local_defs = {
				node.name: node
				for node in ast.walk(module_tree)
				if isinstance(node, ast.FunctionDef) and node.name.startswith("_")
			}
			direct = _direct_call_names(tree)
			extra = {
				name
				for helper in direct & set(local_defs)
				for name in _direct_call_names(local_defs[helper])
			}
			return direct | extra

	return _direct_call_names(tree)


def _direct_call_names(tree: Any) -> set[str]:
	"""Dotted callee names invoked directly inside one AST subtree."""
	names: set[str] = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.Call):
			continue
		parts: list[str] = []
		target: Any = node.func
		while isinstance(target, ast.Attribute):
			parts.append(target.attr)
			target = target.value
		if isinstance(target, ast.Name):
			parts.append(target.id)
		if parts:
			names.add(".".join(reversed(parts)))
	return names


def _sql_literals(module: Any) -> list[str]:
	"""Every string constant in the module that is not a docstring.

	Docstrings are excluded because these modules explain at length WHY ``OFFSET`` and
	timestamp ordering are wrong, and a naive substring scan reports every one of those
	sentences. f-strings are flattened to their literal parts, which is enough: the
	interpolated holes are the membership fragment and the WHERE clauses, and the parts that
	matter here — ``order by``, ``limit``, ``offset`` — are always literal.
	"""
	tree = ast.parse(_module_source(module))
	docstrings: set[int] = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			first = node.body[0] if node.body else None
			if (
				isinstance(first, ast.Expr)
				and isinstance(first.value, ast.Constant)
				and isinstance(first.value.value, str)
			):
				docstrings.add(id(first.value))

	out: list[str] = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			out.append(node.value)
		elif isinstance(node, ast.JoinedStr):
			parts = [
				piece.value
				for piece in node.values
				if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
			]
			if parts:
				out.append(" ".join(parts))
	return out


import ast  # noqa: E402  (used by the two helpers above; imported late to keep the stub first)


# ---------------------------------------------------------------------------
# The bot's own User is not a mentionable person (v1.280.7)
# ---------------------------------------------------------------------------


def _picker_results(monkeypatch, *, members, bot):
	"""Drive ``search_mention_targets`` with the three seams replaced and nothing else."""
	monkeypatch.setattr(mentions, "require_room", lambda room: ("someone@example.com", room))
	monkeypatch.setattr(mentions, "_member_candidates", lambda room, pattern: list(members))
	monkeypatch.setattr(mentions, "_other_candidates", lambda pattern, exclude, room_type_limit: [])
	monkeypatch.setattr(mentions, "_bot_user_id", lambda: bot)
	return mentions.search_mention_targets("ROOM-1", "")["results"]


def test_the_bot_user_is_never_offered_as_a_mention_target(monkeypatch) -> None:
	"""Mentioning it does nothing at all, silently — which is the worst shape a failure takes.

	``dispatch_spa_message`` gates on a ``Chat Mention`` row of type ``Triton``, which only the
	picker's sentinel produces. A mention of the bot's *User* is an ordinary ``User`` mention:
	the turn is never dispatched, nothing is enqueued, and nothing is logged. On the day the
	bot became a real ERPNext account the menu offered "Triton" and "Triton Chat Bot" side by
	side — one worked, the other produced no answer and no error.
	"""
	members = [
		{"kind": "user", "user": "alice@example.com", "label": "Alice", "is_member": True},
		{"kind": "user", "user": "chatbot@example.com", "label": "Triton Chat Bot", "is_member": True},
	]
	results = _picker_results(monkeypatch, members=members, bot="chatbot@example.com")

	assert [row["user"] for row in results if row["kind"] == "user"] == ["alice@example.com"]
	# The assistant is still offered — by the sentinel, which is the entry that works.
	assert any(row["user"] == mentions.TRITON_KEY for row in results)


def test_an_unset_bot_user_hides_nobody(monkeypatch) -> None:
	"""A settings page mid-configuration must not empty the mention menu."""
	members = [{"kind": "user", "user": "alice@example.com", "label": "Alice", "is_member": True}]
	results = _picker_results(monkeypatch, members=members, bot="")

	assert "alice@example.com" in [row["user"] for row in results]


# ---------------------------------------------------------------------------
# request_id — the correlation that existed as a column and a sentence and nowhere else
# ---------------------------------------------------------------------------


def _captured_audit_row(monkeypatch, *, sid: str | None = "cookie-value", **kwargs) -> dict[str, Any]:
	"""Drive ``record_privileged_content_read`` and return what reached the writer."""
	captured: dict[str, Any] = {}
	monkeypatch.setattr(
		audit, "record_or_refuse", lambda **kw: captured.update(kw) or "CRA-1", raising=True
	)
	monkeypatch.setattr(
		FRAPPE, "session", types.SimpleNamespace(user="auditor@example.com", sid=sid), raising=False
	)
	_common.record_privileged_content_read(
		[{"name": "M1", "room": "R1", "seq": 7}],
		user="auditor@example.com",
		privileged=True,
		purpose="transcript",
		**kwargs,
	)
	return captured


def test_a_privileged_content_read_carries_a_session_correlation(monkeypatch) -> None:
	"""§4.D.2 asks for one audit row per page. Without a shared id those rows are N unrelated
	facts rather than one act of reading.

	``record_privileged_content_read`` had no ``request_id`` parameter at all until v1.307.1, so
	every row this package wrote landed with the column NULL — while both audit DocTypes
	documented it as correlating "the rows written by one viewer session".
	"""
	captured = _captured_audit_row(monkeypatch)
	assert captured["request_id"], (
		"no correlation id reached the writer, so each page of a transcript read is recorded "
		"as an unrelated event"
	)


def test_the_correlation_never_carries_the_session_cookie(monkeypatch) -> None:
	"""``sid`` is a bearer credential; an audit row is read by more people than the cookie."""
	captured = _captured_audit_row(monkeypatch, sid="supersecret-bearer-token")
	assert "supersecret-bearer-token" not in str(captured["request_id"])


def test_two_pages_of_one_read_share_an_id(monkeypatch) -> None:
	"""The property the column exists for."""
	first = _captured_audit_row(monkeypatch)["request_id"]
	second = _captured_audit_row(monkeypatch)["request_id"]
	assert first == second


def test_an_explicit_correlation_wins_over_the_session_default(monkeypatch) -> None:
	"""Triton passes an invocation id through the gate; the two meanings share the column."""
	captured = _captured_audit_row(monkeypatch, request_id="triton-turn-42")
	assert captured["request_id"] == "triton-turn-42"


def test_no_session_records_no_correlation_rather_than_a_guess(monkeypatch) -> None:
	captured = _captured_audit_row(monkeypatch, sid=None)
	assert captured["request_id"] is None


def test_an_unprivileged_read_still_records_nothing(monkeypatch) -> None:
	"""Invariant I9's other half. Adding a parameter must not turn a member's own read into a row."""
	captured: dict[str, Any] = {}
	monkeypatch.setattr(audit, "record_or_refuse", lambda **kw: captured.update(kw) or "CRA-1")
	_common.record_privileged_content_read(
		[{"name": "M1", "room": "R1", "seq": 7}],
		user="member@example.com",
		privileged=False,
		purpose="transcript",
	)
	assert captured == {}
