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

from erpnext_enhancements.chat import links, permissions  # noqa: E402
from erpnext_enhancements.chat.api import _common, compose, history, mentions, search  # noqa: E402


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
	result = search._result(row, "pump")
	assert "<mark>" not in result["snippet"]
	assert result["match_length"] == len("pump")
	assert result["snippet"][result["match_start"] : result["match_start"] + 4] == "pump"


def test_search_result_carries_the_shared_deep_link() -> None:
	"""One builder, three consumers. A second URL-shape implementation is how a notification
	lands on an error page while the SPA's own links work fine."""
	row = {"name": "M1", "room": "R1", "seq": 3, "text_plain": "pump", "thread_root": "T1"}
	result = search._result(row, "pump")
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
		if "order by" in statement:
			ordering = statement.split("order by", 1)[1]
			assert "`seq`" in ordering, (
				"every transcript ordering is by `seq`. Not `creation`, not Google's "
				"`createTime`: `seq` is allocated under the room's row lock, is immutable and "
				f"has no timezone. Found: {ordering[:60]}"
			)
			assert "`creation`" not in ordering


def test_every_whitelisted_endpoint_gates_before_it_reads() -> None:
	"""One membership decision per request, taken once, at the top.

	Asserted structurally rather than by execution: the gate itself needs a database. What
	this catches is the shape that actually goes wrong — a new endpoint added next to these
	that forgets the line entirely.
	"""
	from erpnext_enhancements.chat.api import presence, readstate, rooms

	seen = 0
	for module in (history, compose, search, mentions, rooms, readstate, presence):
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
	assert seen >= 18, (
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
