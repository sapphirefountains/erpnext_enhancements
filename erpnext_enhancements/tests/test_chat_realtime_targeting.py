"""Bench-free proof that the chat realtime helper cannot broadcast to the wrong room.

**Shape P: plain pytest functions.** It needs its own ``python -m pytest`` step in
``ci.yml``. ``python -m unittest`` cannot collect a file of plain functions — it collects
nothing and reports success — and this repo has already lost a suite that way for weeks.

What is actually being defended. ``frappe.publish_realtime`` fails *open*: when it cannot
work out where to send something it sends it to ``get_site_room()``, the literal ``"all"``,
a room every logged-in System User is already sitting in. Three mechanisms inside that one
function can steer a chat message away from the room it was written for:

  1. the final fallback is that site-wide broadcast;
  2. ``task_id`` outranks ``user=`` **and** ``doctype=``/``docname=`` in the fallback chain,
     and it is seeded implicitly from ``frappe.local.task_id`` — so inside a background job,
     which is where every relay write happens, a correctly-scoped call is silently
     retargeted to ``task_progress:<id>``, a room any client may join with no permission
     check at all;
  3. the event names ``list_update`` and ``docinfo_update`` are special-cased **above** the
     ``if not room:`` guard and overwrite an explicitly passed ``room=``.

``tests/test_chat_guardrails.py`` lints the source for (1) and (3). It cannot see (2) at
all, and it cannot see whether the room a helper computes is the *right* room. This suite
does, by executing the helper.

**The stub is a faithful port, not a mock.** ``_resolve_room_like_frappe`` reproduces the
targeting chain from ``frappe/realtime.py`` statement for statement, so the assertions are
about the room Frappe *would actually pick*, not about which keyword arguments we
remembered to pass. Two tests
(``test_the_resolver_would_retarget_a_publish_that_omitted_room`` and
``test_the_resolver_broadcasts_site_wide_with_no_targeting_at_all``) exist solely to prove
the resolver is not vacuous — that it really does produce the dangerous answers when fed
the dangerous inputs — because a resolver that returned the doc room unconditionally would
make every other assertion here pass while guarding nothing.

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
	"""Is there an actual ``frappe`` package importable here, as opposed to a stub?

	``find_spec`` answers from the import system, so a bare ``types.ModuleType`` another
	suite left in ``sys.modules`` has no ``__spec__`` and reads as absent — which is the
	answer we want.
	"""
	try:
		spec = importlib.util.find_spec("frappe")
	except (ImportError, ValueError):
		return False
	return bool(spec and spec.origin)


if _real_frappe_is_installed():  # pragma: no cover - only true on a bench
	# Skip rather than stub: replacing frappe.publish_realtime on a live bench would take
	# the rest of a `bench run-tests` run with it.
	pytest.skip(
		"bench-free suite: a real frappe is installed, and stubbing over it would break "
		"the rest of the bench run",
		allow_module_level=True,
	)


class _Recorder:
	"""Records every ``publish_realtime`` call, and resolves the room Frappe would use."""

	def __init__(self) -> None:
		self.calls: list[dict[str, Any]] = []
		self.local_task_id: str | None = None

	def publish_realtime(self, *args: Any, **kwargs: Any) -> None:
		# Positional arguments to publish_realtime are their own hazard — the signature is
		# (event, message, room, user, doctype, docname, task_id, after_commit) and getting
		# one slot wrong silently changes the target. The helper must always use keywords.
		assert not args, (
			f"the chat realtime helper called publish_realtime with positional arguments {args!r}. "
			"The signature is (event, message, room, user, doctype, docname, task_id, after_commit); "
			"one slot out of place retargets the publish and nothing errors. Use keywords."
		)
		record = dict(kwargs)
		record["__resolved_room__"] = _resolve_room_like_frappe(
			event=kwargs.get("event"),
			message=kwargs.get("message"),
			room=kwargs.get("room"),
			user=kwargs.get("user"),
			doctype=kwargs.get("doctype"),
			docname=kwargs.get("docname"),
			task_id=kwargs.get("task_id"),
			local_task_id=self.local_task_id,
		)
		self.calls.append(record)

	@property
	def last(self) -> dict[str, Any]:
		assert self.calls, "expected a publish_realtime call and none was recorded"
		return self.calls[-1]


def _get_doc_room(doctype: str, docname: str) -> str:
	"""``frappe.realtime.get_doc_room``, verbatim."""
	return f"doc:{doctype}/{docname}"


def _get_user_room(user: str) -> str:
	"""``frappe.realtime.get_user_room``, verbatim."""
	return f"user:{user}"


def _get_doctype_room(doctype: str) -> str:
	"""``frappe.realtime.get_doctype_room``, verbatim."""
	return f"doctype:{doctype}"


SITE_ROOM = "all"


def _resolve_room_like_frappe(
	*,
	event: str | None,
	message: Any,
	room: str | None,
	user: str | None,
	doctype: str | None,
	docname: str | None,
	task_id: str | None,
	local_task_id: str | None,
) -> str:
	"""The targeting chain out of ``frappe/realtime.py``, ported statement for statement.

	This is the whole point of the suite: the assertions below are about the room Frappe
	would genuinely resolve, so they keep holding if somebody changes *which* keyword
	arguments the helper passes. Note the ordering — ``task_id`` is seeded before the chain
	and consulted first inside it, which is trap 2.
	"""
	if message is None:
		message = {}
	if not task_id and local_task_id is not None:
		task_id = local_task_id
	if event is None:
		event = "task_progress" if task_id else "global"
	elif event == "msgprint" and not user:
		user = "session-user"
	elif event == "list_update":
		doctype = doctype or (message.get("doctype") if isinstance(message, dict) else None)
		room = _get_doctype_room(doctype)
	elif event == "docinfo_update":
		room = _get_doc_room(doctype, docname)

	if not room:
		if task_id:
			room = f"task_progress:{task_id}"
		elif user:
			room = _get_user_room(user)
		elif doctype and docname:
			room = _get_doc_room(doctype, docname)
		else:
			room = SITE_ROOM
	return room


RECORDER = _Recorder()


def install_frappe_stub() -> types.ModuleType:
	"""Install the minimal fake ``frappe`` that ``chat.realtime`` imports.

	The house pattern (``test_chat_webhook_verify``, ``test_quickbooks_online``): build the
	module, attach only what the module under test actually uses, and put it in
	``sys.modules`` before the import. ``frappe.realtime`` is a separate entry because
	``chat/realtime.py`` deliberately imports ``get_doc_room``/``get_user_room`` from it
	rather than hand-rolling the room strings.
	"""
	frappe = types.ModuleType("frappe")
	frappe.publish_realtime = lambda *a, **k: RECORDER.publish_realtime(*a, **k)
	frappe.local = types.SimpleNamespace(site="test.invalid")
	sys.modules["frappe"] = frappe

	realtime = types.ModuleType("frappe.realtime")
	realtime.get_doc_room = _get_doc_room
	realtime.get_user_room = _get_user_room
	realtime.get_doctype_room = _get_doctype_room
	sys.modules["frappe.realtime"] = realtime
	frappe.realtime = realtime

	return frappe


FRAPPE = install_frappe_stub()

# Imported here, after the stub: at module scope `chat.realtime` does `import frappe` and
# `from frappe.realtime import ...`, and on a bench-free runner those are the stub or they
# are an ImportError.
from erpnext_enhancements.chat import realtime as chat_realtime

ROOM = "abc123def456"
DOC_ROOM = f"doc:Chat Room/{ROOM}"
USER = "coworker@example.invalid"
USER_ROOM = f"user:{USER}"

ROOM_EVENTS = (
	chat_realtime.EVENT_MESSAGE_CREATED,
	chat_realtime.EVENT_MESSAGE_EDITED,
	chat_realtime.EVENT_MESSAGE_DELETED,
)


@pytest.fixture(autouse=True)
def fresh_recorder() -> Any:
	"""Reset between tests. Autouse: a leaked call list is a false green."""
	RECORDER.calls.clear()
	RECORDER.local_task_id = None
	return RECORDER


# ---------------------------------------------------------------------------
# The resolver is not vacuous — prove it produces the dangerous answers
# ---------------------------------------------------------------------------


def test_the_resolver_would_retarget_a_publish_that_omitted_room() -> None:
	"""Trap 2, demonstrated. Without ``room=``, ``task_id`` beats doctype/docname."""
	resolved = _resolve_room_like_frappe(
		event="chat_message_created",
		message={},
		room=None,
		user=None,
		doctype="Chat Room",
		docname=ROOM,
		task_id=None,
		local_task_id="T-1",
	)
	assert resolved == "task_progress:T-1", (
		"the ported resolver does not reproduce Frappe's task_id retargeting, so every "
		"assertion in this file that relies on it is passing vacuously. Fix the port, do not "
		"delete this test."
	)


def test_the_resolver_would_retarget_a_user_publish_inside_a_job() -> None:
	resolved = _resolve_room_like_frappe(
		event="chat_unread",
		message={},
		room=None,
		user=USER,
		doctype=None,
		docname=None,
		task_id=None,
		local_task_id="T-2",
	)
	assert resolved == "task_progress:T-2", "task_id must outrank user= in the ported chain"


def test_the_resolver_broadcasts_site_wide_with_no_targeting_at_all() -> None:
	"""Trap 1, demonstrated. No room, no user, no doc, no task: everybody gets it."""
	resolved = _resolve_room_like_frappe(
		event="chat_message_created",
		message={},
		room=None,
		user=None,
		doctype=None,
		docname=None,
		task_id=None,
		local_task_id=None,
	)
	assert resolved == SITE_ROOM


def test_the_resolver_lets_the_reserved_names_overwrite_an_explicit_room() -> None:
	"""Trap 3, demonstrated — which is why the helper refuses those two names outright."""
	assert (
		_resolve_room_like_frappe(
			event="docinfo_update",
			message={},
			room=DOC_ROOM,
			user=None,
			doctype="Sales Order",
			docname="SO-0001",
			task_id=None,
			local_task_id=None,
		)
		== "doc:Sales Order/SO-0001"
	)
	assert (
		_resolve_room_like_frappe(
			event="list_update",
			message={"doctype": "Sales Order"},
			room=DOC_ROOM,
			user=None,
			doctype=None,
			docname=None,
			task_id=None,
			local_task_id=None,
		)
		== "doctype:Sales Order"
	)


# ---------------------------------------------------------------------------
# Room events land in the room's document room, always
# ---------------------------------------------------------------------------


def test_a_room_event_is_published_at_all() -> None:
	"""Non-vacuity for everything below: the helper must actually publish something."""
	chat_realtime.publish_room_event(ROOM, chat_realtime.EVENT_MESSAGE_CREATED, {"message": "m1"})
	assert len(RECORDER.calls) == 1


@pytest.mark.parametrize("event", ROOM_EVENTS)
def test_every_room_event_carries_doctype_docname_and_after_commit(event: str) -> None:
	chat_realtime.publish_room_event(ROOM, event, {"message": "m1", "seq": 7})
	call = RECORDER.last
	assert call["doctype"] == "Chat Room"
	assert call["docname"] == ROOM
	assert call["after_commit"] is True, (
		"every chat publish is after_commit=True. The consumer immediately re-reads the row; an "
		"event that beats its own transaction points at a row that does not exist yet."
	)
	assert call["event"] == event


@pytest.mark.parametrize("event", ROOM_EVENTS)
def test_every_room_event_resolves_to_the_document_room(event: str) -> None:
	chat_realtime.publish_room_event(ROOM, event, {"message": "m1"})
	assert RECORDER.last["__resolved_room__"] == DOC_ROOM


def test_the_room_kwarg_is_never_a_caller_supplied_string() -> None:
	"""The helper takes a ``Chat Room`` name, and there is no way to inject a raw room.

	A room-shaped argument is not honoured as a room: it is treated as a (nonexistent) room
	name and wrapped again, so it delivers to nobody. Failing closed is the right direction
	for this mistake — the alternative is naming a socket room the permission stack was
	never asked about.
	"""
	chat_realtime.publish_room_event("doc:Chat Room/SOMEONE-ELSE", chat_realtime.EVENT_MESSAGE_CREATED)
	call = RECORDER.last
	assert call["room"] == "doc:Chat Room/doc:Chat Room/SOMEONE-ELSE"
	assert call["room"] == _get_doc_room("Chat Room", "doc:Chat Room/SOMEONE-ELSE")


def test_the_room_kwarg_always_equals_the_derived_document_room() -> None:
	for name in (ROOM, "x", "a-b-c", "Room With Spaces"):
		RECORDER.calls.clear()
		chat_realtime.publish_room_event(name, chat_realtime.EVENT_MESSAGE_EDITED)
		call = RECORDER.last
		assert call["room"] == _get_doc_room("Chat Room", name)
		assert call["room"] == _get_doc_room(call["doctype"], call["docname"])


# ---------------------------------------------------------------------------
# task_id — the trap that only fires in a background job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event", ROOM_EVENTS)
def test_no_room_publish_ever_passes_task_id(event: str) -> None:
	chat_realtime.publish_room_event(ROOM, event, {"message": "m1"})
	assert "task_id" not in RECORDER.last or RECORDER.last["task_id"] is None


def test_no_user_counter_ever_passes_task_id() -> None:
	chat_realtime.publish_user_counter(USER, "chat_unread", {"unread": 3})
	assert "task_id" not in RECORDER.last or RECORDER.last["task_id"] is None


def test_a_background_job_task_id_cannot_retarget_a_room_publish() -> None:
	"""The live trap: relay writes happen in background jobs, where ``task_id`` is set."""
	RECORDER.local_task_id = "T-99"
	chat_realtime.publish_room_event(ROOM, chat_realtime.EVENT_MESSAGE_CREATED, {"message": "m1"})
	resolved = RECORDER.last["__resolved_room__"]
	assert resolved == DOC_ROOM, (
		f"inside a background job the publish resolved to {resolved!r}. A task-progress room is "
		"joinable by any client with no permission check; message content must never reach one."
	)
	assert not resolved.startswith("task_progress:")


def test_a_background_job_task_id_cannot_retarget_a_user_counter() -> None:
	RECORDER.local_task_id = "T-99"
	chat_realtime.publish_user_counter(USER, "chat_unread", {"unread": 1})
	assert RECORDER.last["__resolved_room__"] == USER_ROOM


def test_nothing_ever_resolves_to_the_site_wide_room() -> None:
	RECORDER.local_task_id = "T-7"
	chat_realtime.publish_room_event(ROOM, chat_realtime.EVENT_MESSAGE_DELETED, {"message": "m1"})
	chat_realtime.publish_user_counter(USER, "chat_unread", {"unread": 0})
	for call in RECORDER.calls:
		resolved = call["__resolved_room__"]
		assert resolved != SITE_ROOM, "a chat publish reached Frappe's site-wide broadcast room"
		assert resolved.startswith(("doc:Chat Room/", "user:"))


# ---------------------------------------------------------------------------
# The two reserved event names
# ---------------------------------------------------------------------------


def test_the_reserved_name_set_is_exactly_frappes_two_special_cases() -> None:
	assert chat_realtime.FORBIDDEN_EVENTS == frozenset({"list_update", "docinfo_update"})


@pytest.mark.parametrize("event", ["list_update", "docinfo_update"])
def test_a_reserved_event_name_raises_and_publishes_nothing(event: str) -> None:
	with pytest.raises(ValueError, match="reserved"):
		chat_realtime.publish_room_event(ROOM, event, {"message": "m1"})
	with pytest.raises(ValueError, match="reserved"):
		chat_realtime.publish_user_counter(USER, event, {"unread": 1})
	assert RECORDER.calls == [], "a refused event name must not publish anything at all"


@pytest.mark.parametrize("event", ["task_progress", "global", "msgprint", "unread", ""])
def test_an_event_name_outside_the_chat_namespace_raises(event: str) -> None:
	with pytest.raises(ValueError):
		chat_realtime.publish_room_event(ROOM, event, {"message": "m1"})
	assert RECORDER.calls == []


@pytest.mark.parametrize("event", ["chat_anything", "chat:presence"])
def test_a_chat_prefixed_event_name_is_accepted(event: str) -> None:
	chat_realtime.publish_room_event(ROOM, event, {"message": "m1"})
	assert RECORDER.last["event"] == event


# ---------------------------------------------------------------------------
# User counters carry counters
# ---------------------------------------------------------------------------


def test_a_user_counter_targets_the_user_room_and_no_document_room() -> None:
	chat_realtime.publish_user_counter(USER, "chat_unread", {"unread": 4, "room": ROOM})
	call = RECORDER.last
	assert call["room"] == USER_ROOM
	assert call["user"] == USER
	assert call["after_commit"] is True
	assert "doctype" not in call and "docname" not in call, (
		"a user counter has no document to scope to; passing doctype/docname would imply a "
		"permission-checked room that a user room is not."
	)


@pytest.mark.parametrize("key", ["text", "text_plain", "body", "preview", "html", "snippet", "content"])
def test_a_user_counter_refuses_content_shaped_payload_keys(key: str) -> None:
	with pytest.raises(ValueError, match="refuses payload key"):
		chat_realtime.publish_user_counter(USER, "chat_unread", {key: "anything"})
	assert RECORDER.calls == []


def test_a_user_counter_still_accepts_identifiers() -> None:
	chat_realtime.publish_user_counter(USER, "chat_unread", {"message": "m1", "room": ROOM, "unread": 2})
	assert RECORDER.last["room"] == USER_ROOM


# ---------------------------------------------------------------------------
# Payload hygiene
# ---------------------------------------------------------------------------


def test_an_empty_room_or_user_raises_before_anything_is_published() -> None:
	for bad in ("", "   ", None):
		with pytest.raises(ValueError):
			chat_realtime.publish_room_event(bad, chat_realtime.EVENT_MESSAGE_CREATED)
		with pytest.raises(ValueError):
			chat_realtime.publish_user_counter(bad, "chat_unread")
	assert RECORDER.calls == []


def test_raw_bytes_in_a_payload_are_refused() -> None:
	with pytest.raises(ValueError, match="raw bytes"):
		chat_realtime.publish_room_event(ROOM, chat_realtime.EVENT_MESSAGE_CREATED, {"blob": b"\x00\x01"})
	assert RECORDER.calls == []


def test_an_oversized_payload_is_refused_without_echoing_it() -> None:
	oversized = {"note": "x" * (chat_realtime.MAX_ROOM_PAYLOAD_BYTES + 1)}
	with pytest.raises(ValueError) as caught:
		chat_realtime.publish_room_event(ROOM, chat_realtime.EVENT_MESSAGE_CREATED, oversized)
	assert "xxxx" not in str(caught.value), (
		"the oversize error quotes the payload back. Payloads can carry message text and this "
		"exception ends up in an Error Log; report the size, never the content."
	)
	assert RECORDER.calls == []


def test_the_counter_ceiling_is_tighter_than_the_room_ceiling() -> None:
	assert chat_realtime.MAX_COUNTER_PAYLOAD_BYTES < chat_realtime.MAX_ROOM_PAYLOAD_BYTES
	with pytest.raises(ValueError):
		chat_realtime.publish_user_counter(
			USER, "chat_unread", {"ids": ["m" * 32] * chat_realtime.MAX_COUNTER_PAYLOAD_BYTES}
		)


def test_the_payload_is_copied_so_a_later_mutation_cannot_change_what_is_sent() -> None:
	"""``after_commit=True`` means Frappe serialises later; a live reference would drift."""
	payload = {"message": "m1"}
	chat_realtime.publish_room_event(ROOM, chat_realtime.EVENT_MESSAGE_CREATED, payload)
	payload["message"] = "m2"
	assert RECORDER.last["message"] == {"message": "m1"}


def test_a_missing_payload_is_an_empty_mapping_not_none() -> None:
	chat_realtime.publish_room_event(ROOM, chat_realtime.EVENT_MESSAGE_DELETED)
	assert RECORDER.last["message"] == {}
