"""Bench-free proof of the chat write path — invariant I1.

**Shape P: plain pytest functions.** It needs its own ``python -m pytest`` step in
``ci.yml``. ``python -m unittest`` cannot collect a file of plain functions — it collects
nothing and reports success — and this repo has already lost a suite that way for weeks.

--------------------------------------------------------------------------------------
Why this suite stubs a database instead of mocking the module under test
--------------------------------------------------------------------------------------

The write path's interesting properties are all *orderings*, and a mock cannot see an
ordering. So the stub below reproduces ``Document.insert``'s real sequence —
``before_insert`` → ``set_new_name`` (which **nulls the name first**) → ``validate`` →
``db_insert`` → ``after_insert`` — statement for statement out of Frappe v16's
``model/document.py`` and ``model/naming.py``, and then runs the **real**
``ChatMessage`` controller and the **real** :mod:`erpnext_enhancements.chat.sync.outbox`
against it. Two consequences worth stating:

* ``test_before_insert_cannot_see_the_document_name`` exists to prove the port is not
  vacuous. If Frappe ever assigned the name earlier, that test goes red and tells the next
  reader that ``client_message_id`` could move back into ``before_insert`` — rather than
  everything else here passing for the wrong reason.
* ``_FakeDB.sql`` **raises on a query it does not recognise**. That is a feature: it is how
  a future ``SELECT MAX(seq) FROM tabChat Message`` — the allocator this design rejects —
  fails loudly here instead of quietly deadlocking in production.

The unique indexes are enforced by the stub with the real constraint names from
``patches/add_chat_indexes.py``, because the retry logic keys on those names and a rename
would otherwise turn a retry into an unhandled error.

No bench, no network, no database, no employee content.
"""

from __future__ import annotations

import ast
import copy
import html
import importlib.util
import json
import pathlib
import re
import sys
import types
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Bench guard
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
		"bench-free suite: a real frappe is installed and stubbing over it would break the "
		"rest of the bench run",
		allow_module_level=True,
	)


# ---------------------------------------------------------------------------
# The frappe stub
# ---------------------------------------------------------------------------

SITE = "chat-test.invalid"
SITE_URL = "https://erp.test.invalid"


class _dict(dict):
	"""``frappe._dict``: attribute access, and a miss reads as ``None`` rather than raising."""

	def __getattr__(self, key: str) -> Any:
		return self.get(key)

	def __setattr__(self, key: str, value: Any) -> None:
		self[key] = value

	def __delattr__(self, key: str) -> None:
		self.pop(key, None)


class ValidationError(Exception):
	pass


class FrappeNameError(Exception):
	pass


class DuplicateEntryError(FrappeNameError):
	"""Primary-key collisions only — the trap ``PHASE2_VERIFIED.md`` §1.1 documents."""


class UniqueValidationError(ValidationError):
	"""Every *other* unique index. Shares no base with the above beyond ``Exception``."""


#: ``(doctype, constraint_name, columns)``. Lifted from ``patches/add_chat_indexes.py`` and
#: ``add_chat_phase2_indexes.py``; the retry paths match on these exact names.
UNIQUE_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
	("Chat Message", "unique_room_seq", ("room", "seq")),
	("Chat Message", "unique_room_client_message_id", ("room", "client_message_id")),
	("Chat Message", "gchat_message_name", ("gchat_message_name",)),
	("Chat Relay Job", "unique_room_job_seq", ("room", "job_seq")),
	("Chat Room Member", "unique_room_user", ("room", "user")),
	# The audit trail's own index. Modelled here because `on_update` now writes to that table:
	# `record_revision` allocates `revision_no` as `max + 1` inside the caller's transaction and
	# relies on this constraint to turn "the same save recorded twice" into a caught duplicate
	# rather than two rows both claiming to be revision 4.
	("Chat Message Revision", "unique_message_revision_no", ("message", "revision_no")),
)


def _norm(query: str) -> str:
	return re.sub(r"\s+", " ", query or "").strip().lower()


class _FakeDB:
	"""An in-memory table store that answers exactly the statements the write path issues."""

	def __init__(self) -> None:
		self.tables: dict[str, dict[str, dict[str, Any]]] = {}
		self.queries: list[tuple[str, Any]] = []
		self.set_value_calls: list[dict[str, Any]] = []
		self.savepoints: dict[str, Any] = {}
		self._hash_counter = 0

	# -- helpers the tests use directly -------------------------------------

	def table(self, doctype: str) -> dict[str, dict[str, Any]]:
		return self.tables.setdefault(doctype, {})

	def seed(self, doctype: str, name: str, **fields: Any) -> dict[str, Any]:
		row = dict(fields)
		row["name"] = name
		self.table(doctype)[name] = row
		return row

	def rows(self, doctype: str) -> list[dict[str, Any]]:
		return list(self.table(doctype).values())

	def next_name(self, doctype: str) -> str:
		self._hash_counter += 1
		return f"{doctype.replace(' ', '')[:4].lower()}{self._hash_counter:028d}"

	# -- the frappe.db surface ----------------------------------------------

	def sql(self, query: str, values: Any = None, as_dict: bool = False, **_: Any) -> Any:
		text = _norm(query)
		self.queries.append((text, values))
		params = values if isinstance(values, dict) else {}

		if text.startswith("savepoint ") or text.startswith("release savepoint "):
			return []
		if text.startswith("rollback to savepoint "):
			return []

		if text == "select name from `tabchat room` where name = %(room)s for update":
			row = self.table("Chat Room").get(params.get("room"))
			return [_dict(name=row["name"])] if row else []

		if text == (
			"update `tabchat room` set seq_high_water = coalesce(seq_high_water, 0) + 1 "
			"where name = %(room)s"
		):
			row = self.table("Chat Room").get(params.get("room"))
			if row is not None:
				row["seq_high_water"] = int(row.get("seq_high_water") or 0) + 1
			return []

		if text == "select seq_high_water from `tabchat room` where name = %(room)s":
			row = self.table("Chat Room").get(params.get("room"))
			return [_dict(seq_high_water=int(row.get("seq_high_water") or 0))] if row else []

		if text == "select coalesce(max(seq), 0) as high from `tabchat message` where room = %(room)s":
			# The REPAIR, not the allocator. See outbox.repair_seq_high_water.
			seqs = [
				int(r.get("seq") or 0)
				for r in self.rows("Chat Message")
				if r.get("room") == params.get("room")
			]
			return [_dict(high=max(seqs) if seqs else 0)]

		if text == (
			"update `tabchat room` set seq_high_water = %(high)s where name = %(room)s "
			"and coalesce(seq_high_water, 0) < %(high)s"
		):
			row = self.table("Chat Room").get(params.get("room"))
			if row is not None and int(row.get("seq_high_water") or 0) < int(params.get("high") or 0):
				row["seq_high_water"] = int(params["high"])
			return []

		if text == (
			"select coalesce(max(job_seq), 0) as high from `tabchat relay job` where room = %(room)s"
		):
			highs = [
				int(r.get("job_seq") or 0)
				for r in self.rows("Chat Relay Job")
				if r.get("room") == params.get("room")
			]
			return [_dict(high=max(highs) if highs else 0)]

		if text.startswith("update `tabchat room member` set last_read_seq"):
			for row in self.rows("Chat Room Member"):
				if row.get("room") == params.get("room") and row.get("user") == params.get("user"):
					if int(row.get("last_read_seq") or 0) < int(params.get("seq") or 0):
						row["last_read_seq"] = int(params["seq"])
						row["last_read_at"] = params.get("now")
			return []

		raise AssertionError(
			"the chat write path issued a SQL statement this suite does not model:\n"
			f"  {text}\n"
			"That is deliberately fatal. Either the statement is new and belongs in _FakeDB.sql "
			"with a test beside it, or it is the rejected `SELECT MAX(seq)` allocator sneaking "
			"back in — see erpnext_enhancements/chat/sync/outbox.py's module docstring."
		)

	def savepoint(self, name: str) -> None:
		self.savepoints[name] = copy.deepcopy(self.tables)
		self.sql(f"savepoint {name}")

	def rollback(self, save_point: str | None = None) -> None:
		if save_point is not None and save_point in self.savepoints:
			self.tables = self.savepoints.pop(save_point)
			self.sql(f"rollback to savepoint {save_point}")

	def release_savepoint(self, name: str) -> None:
		self.savepoints.pop(name, None)
		self.sql(f"release savepoint {name}")

	def get_value(self, doctype: str, name: Any, fields: Any = None, as_dict: bool = False, **_: Any) -> Any:
		"""``frappe.db.get_value``: by name, by filter dict, and the one aggregate form.

		The aggregate branch is ``max(revision_no) as revision_no``, which is how
		``ChatMessageRevision.next_revision_no`` allocates. It is modelled rather than special-cased
		away because the allocation and the ``unique(message, revision_no)`` collision are two
		halves of one design: a stub that always answered ``1`` would make every second revision of
		a message a duplicate, and a stub that never collided would prove the retry path works when
		it does not exist.
		"""
		matches = self._matching(doctype, name)

		if isinstance(fields, str) and fields.lower().startswith("max("):
			column, _, alias = fields.partition(" as ")
			column = column.strip()[4:-1].strip()
			alias = alias.strip() or column
			values = [row.get(column) for row in matches if row.get(column) is not None]
			highest = max(values) if values else None
			return _dict({alias: highest}) if as_dict else highest

		row = matches[0] if matches else None
		if row is None:
			return None
		if isinstance(fields, str):
			return row.get(fields)
		picked = _dict({f: row.get(f) for f in (fields or ["name"])})
		return picked if as_dict else tuple(picked.values())

	def _matching(self, doctype: str, name: Any) -> list[dict[str, Any]]:
		if isinstance(name, dict):
			return [
				row
				for row in self.rows(doctype)
				if all(row.get(key) == value for key, value in name.items())
			]
		row = self.table(doctype).get(name) if isinstance(name, str) else None
		return [row] if row is not None else []

	def set_value(
		self,
		doctype: str,
		name: str,
		fieldname: Any,
		value: Any = None,
		update_modified: bool = True,
		**_: Any,
	) -> None:
		self.set_value_calls.append(
			{
				"doctype": doctype,
				"name": name,
				"fieldname": fieldname,
				"value": value,
				"update_modified": update_modified,
			}
		)
		row = self.table(doctype).setdefault(name, {"name": name})
		updates = fieldname if isinstance(fieldname, dict) else {fieldname: value}
		row.update(updates)
		if update_modified:
			row["modified"] = FRAPPE.utils.now_datetime()

	def db_insert(self, doc: Any) -> None:
		"""Enforce the composite unique indexes, in MariaDB's own error shape."""
		row = {k: v for k, v in vars(doc).items() if not k.startswith("_") and k not in ("flags", "meta")}
		for doctype, constraint, columns in UNIQUE_INDEXES:
			if doctype != doc.doctype:
				continue
			key = tuple(row.get(c) for c in columns)
			if any(v in (None, "", 0) for v in key):
				# Frappe coerces an unset unique column to NULL and MariaDB permits unlimited
				# NULLs in a unique index. Modelling that matters: without it every
				# not-yet-relayed message would collide on gchat_message_name.
				continue
			for existing in self.rows(doctype):
				if existing.get("name") == doc.name:
					continue
				if tuple(existing.get(c) for c in columns) == key:
					FRAPPE.msgprint(f"{columns[-1]} must be unique")
					raise UniqueValidationError(
						f"Duplicate entry '{'-'.join(str(v) for v in key)}' for key '{constraint}'"
					)
		self.table(doc.doctype)[doc.name] = row

	def db_update(self, doc: Any) -> None:
		"""The ``UPDATE`` half. No unique-index re-check: none of the indexed columns is
		writable after insert, and a second check would only be able to fire on a bug the
		insert path already owns."""
		self.table(doc.doctype)[doc.name] = {
			k: v for k, v in vars(doc).items() if not k.startswith("_") and k not in ("flags", "meta")
		}


class _Document:
	"""``frappe.model.document.Document``, reduced to the insert pipeline and its ordering."""

	def __init__(self, data: dict[str, Any] | None = None) -> None:
		object.__setattr__(self, "flags", _dict())
		for key, value in (data or {}).items():
			setattr(self, key, value)
		if not hasattr(self, "name"):
			self.name = None
		self._inserted = False

	def get(self, key: str, default: Any = None) -> Any:
		return getattr(self, key, default)

	def set(self, key: str, value: Any) -> None:
		setattr(self, key, value)

	def is_new(self) -> bool:
		return not self._inserted

	def _run(self, method: str) -> None:
		fn = getattr(self, method, None)
		if callable(fn):
			fn()

	def get_doc_before_save(self) -> Any:
		"""``None`` on an insert. Frappe loads the before-image in ``_save`` only."""
		return getattr(self, "_doc_before_save", None)

	def has_value_changed(self, fieldname: str) -> bool:
		"""Frappe's own implementation, ported verbatim — including the part that surprises.

		With **no** before-image it returns ``True``, so ``has_value_changed`` alone cannot
		tell an edit from an insert. Anything gating an outbound edit on it has to check the
		before-image itself, and this port is what makes that provable here.
		"""
		previous = self.get_doc_before_save()
		return previous.get(fieldname) != self.get(fieldname) if previous else True

	def insert(self, ignore_permissions: bool = False, **_: Any) -> "_Document":
		"""Frappe v16's ordering, ported. The ported lines that matter are marked."""
		self._run("before_insert")
		# frappe/model/naming.py set_new_name(): every autoname other than prompt/UUID has
		# its name CLEARED first, then regenerated. This is why before_insert cannot see it.
		self.name = None
		self.name = FRAPPE.db.next_name(self.doctype)
		# document.py insert(): `flags.in_insert = True` is set around run_before_save_methods
		# and again around run_post_save_methods, and cleared only at the very end.
		self.flags.in_insert = True
		self._run("validate")
		FRAPPE.db.db_insert(self)
		self._inserted = True
		self._run("after_insert")
		# **insert() also calls run_post_save_methods(), which runs on_update.** So on_update
		# fires on the insert as well as on every later save, with no before-image and with
		# flags.in_insert still set. An on_update that relays an edit without checking for
		# that would double-relay every message ever sent, which is why this line is ported
		# rather than left out as a detail of a code path this suite "does not test".
		self._run("on_update")
		self.flags.in_insert = False
		return self

	def save(self, ignore_permissions: bool = False, **_: Any) -> "_Document":
		"""``Document._save`` for a row that already exists: before-image, validate, update,
		``on_update``. The before-image is a snapshot of the stored row, which is what makes
		``get_doc_before_save`` answer the question the edit gate actually asks."""
		self._doc_before_save = _dict(copy.deepcopy(FRAPPE.db.table(self.doctype).get(self.name) or {}))
		self._run("validate")
		FRAPPE.db.db_update(self)
		self._run("on_update")
		return self

	def delete(self, **_: Any) -> None:
		"""``frappe.delete_doc``, reduced to the one hook that can refuse it."""
		self._run("on_trash")
		FRAPPE.db.table(self.doctype).pop(self.name, None)


class _Recorder:
	def __init__(self) -> None:
		self.publishes: list[dict[str, Any]] = []
		self.enqueues: list[dict[str, Any]] = []
		self.errors: list[dict[str, Any]] = []
		self.messages: list[str] = []


REC = _Recorder()
SETTINGS = _dict()


def _cint(value: Any, default: int = 0) -> int:
	try:
		return int(float(value))
	except (TypeError, ValueError):
		return default


def _throw(msg: Any, exc: Any = ValidationError, title: Any = None, **_: Any) -> None:
	raise (exc if isinstance(exc, type) else ValidationError)(str(msg))


def install_frappe_stub() -> types.ModuleType:
	frappe = types.ModuleType("frappe")
	db = _FakeDB()

	frappe._dict = _dict
	frappe.local = types.SimpleNamespace(site=SITE, message_log=[])
	frappe.flags = _dict()
	frappe.db = db
	frappe.ValidationError = ValidationError
	frappe.NameError = FrappeNameError
	frappe.DuplicateEntryError = DuplicateEntryError
	frappe.UniqueValidationError = UniqueValidationError
	frappe._ = lambda s, *a, **k: s
	frappe.throw = _throw
	frappe.msgprint = lambda msg, *a, **k: (
		frappe.local.message_log.append(str(msg)),
		REC.messages.append(str(msg)),
	)
	frappe.clear_last_message = lambda: (frappe.local.message_log.pop() if frappe.local.message_log else None)
	frappe.as_json = lambda obj, **k: json.dumps(obj, default=str)
	frappe.log_error = lambda title="", message="", **k: REC.errors.append(
		{"title": title, "message": message}
	)
	frappe.logger = lambda *a, **k: types.SimpleNamespace(
		debug=lambda *a, **k: None, warning=lambda *a, **k: None, info=lambda *a, **k: None
	)
	frappe.cache = lambda: types.SimpleNamespace(
		incrby=lambda *a, **k: None,
		mget=lambda *a, **k: [],
		delete=lambda *a, **k: None,
	)
	frappe.publish_realtime = lambda **kwargs: REC.publishes.append(dict(kwargs))
	frappe.enqueue = lambda method, **kwargs: REC.enqueues.append({"method": method, **kwargs})
	frappe.get_cached_doc = lambda doctype, *a, **k: SETTINGS
	frappe.get_doc = lambda data: _Document(dict(data))
	frappe.session = types.SimpleNamespace(user="Administrator")

	def _new_doc(doctype: str) -> _Document:
		"""``frappe.new_doc``, wired to the **real** controller for the tables ``on_update`` writes.

		The audit row's ``room`` is ``reqd`` and is backfilled by
		``ChatMessageRevision.before_insert``; handing back a plain ``_Document`` would skip that
		and quietly prove nothing about the denormalisation the oversight query depends on.
		"""
		from erpnext_enhancements.chat.doctype.chat_message_revision.chat_message_revision import (
			ChatMessageRevision,
		)

		controller = {"Chat Message Revision": ChatMessageRevision}.get(doctype, _Document)
		return controller({"doctype": doctype})

	frappe.new_doc = _new_doc

	utils = types.ModuleType("frappe.utils")
	utils.cint = _cint
	utils.escape_html = lambda s: html.escape(str(s or ""), quote=True)
	utils.now_datetime = lambda: datetime(2026, 8, 9, 12, 0, 0)
	utils.get_url = lambda path="": f"{SITE_URL}{path}"
	utils.add_days = lambda d, n: d
	sys.modules["frappe.utils"] = utils
	frappe.utils = utils

	realtime = types.ModuleType("frappe.realtime")
	realtime.get_doc_room = lambda doctype, docname: f"doc:{doctype}/{docname}"
	realtime.get_user_room = lambda user: f"user:{user}"
	sys.modules["frappe.realtime"] = realtime
	frappe.realtime = realtime

	# `frappe.database.database.savepoint` — what `inbound.savepoint_catching_duplicates` opens
	# around the audit-row insert. Ported with its real shape: **swallow the listed classes and
	# re-raise everything else**, because "an expected duplicate is success" and "the revision
	# writer blew up" must not become the same outcome.
	database_pkg = types.ModuleType("frappe.database")
	database_mod = types.ModuleType("frappe.database.database")

	@contextmanager
	def _savepoint(catch: Any = Exception) -> Any:
		point = f"sp{len(db.savepoints)}"
		db.savepoint(point)
		try:
			yield
		except catch:
			db.rollback(save_point=point)
		else:
			db.release_savepoint(point)

	database_mod.savepoint = _savepoint
	database_pkg.database = database_mod
	sys.modules["frappe.database"] = database_pkg
	sys.modules["frappe.database.database"] = database_mod
	frappe.database = database_pkg

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	model.document = document
	frappe.model = model

	sys.modules["frappe"] = frappe
	return frappe


FRAPPE = install_frappe_stub()

# Imported after the stub: every module below does `import frappe` at module scope.
from erpnext_enhancements.chat import seams  # noqa: E402
from erpnext_enhancements.chat.doctype.chat_message.chat_message import ChatMessage  # noqa: E402
from erpnext_enhancements.chat.doctype.chat_room.chat_room import ChatRoom  # noqa: E402
from erpnext_enhancements.chat.doctype.chat_room_member import chat_room_member  # noqa: E402
from erpnext_enhancements.chat.gchat import ids  # noqa: E402
from erpnext_enhancements.chat.sync import outbox, states  # noqa: E402

ROOM = "room0000000000000000000000001"
USER = "coworker@example.invalid"
OTHER = "colleague@example.invalid"


def _default_settings() -> _dict:
	"""A site with the feature fully switched on. Individual tests turn things off."""
	return _dict(
		enabled=1,
		google_sync_enabled=1,
		relay_outbound_enabled=1,
		pause_outbound=0,
		import_mode_enabled=0,
		message_byte_limit=32000,
		oversized_message_policy="Truncate With Link",
	)


@pytest.fixture(autouse=True)
def fresh_world() -> Any:
	"""A clean database, clean recorder and clean settings between tests."""
	FRAPPE.db.__init__()
	REC.__init__()
	FRAPPE.flags.clear()
	FRAPPE.local.message_log.clear()
	FRAPPE.session.user = USER
	SETTINGS.clear()
	SETTINGS.update(_default_settings())
	seams.reset_counters()
	FRAPPE.db.seed(
		"Chat Room",
		ROOM,
		room_type="Group",
		provisioning_mode="On First Message",
		external_users_allowed=0,
		mirror_whitelisted=0,
		gchat_space_name="spaces/AAAATESTSPACE",
		seq_high_water=0,
	)
	FRAPPE.db.seed("Chat Room Member", "mem1", room=ROOM, user=USER, is_active=1, last_read_seq=0)
	FRAPPE.db.seed("Chat Room Member", "mem2", room=ROOM, user=OTHER, is_active=1, last_read_seq=0)
	return FRAPPE


def make_message(**overrides: Any) -> ChatMessage:
	data: dict[str, Any] = {
		"doctype": "Chat Message",
		"room": ROOM,
		"sender": USER,
		"sender_kind": "Human",
		"message_type": "Text",
		"text": "hello",
		"sync_origin": "ERPNext",
	}
	data.update(overrides)
	return ChatMessage(data)


def send(**overrides: Any) -> ChatMessage:
	return make_message(**overrides).insert()


def edit(doc: ChatMessage, **changes: Any) -> ChatMessage:
	"""Change fields on a stored message and save it, the way the desk or an endpoint would."""
	for field, value in changes.items():
		setattr(doc, field, value)
	return doc.save()


def soft_delete(doc: ChatMessage) -> ChatMessage:
	"""The **only** delete this design has: ``is_deleted`` 0 → 1, with ``text`` left on the row.

	Google's tombstone is content-free (ADR §F.6.5), so a hard delete would destroy the last
	copy of what was said. The relay is driven off this flip and never off ``on_trash``.
	"""
	return edit(doc, is_deleted=1, deletion_source="ERPNext", deleted_by=USER)


def relay_jobs() -> list[dict[str, Any]]:
	return FRAPPE.db.rows("Chat Relay Job")


def revisions() -> list[dict[str, Any]]:
	return FRAPPE.db.rows("Chat Message Revision")


# ---------------------------------------------------------------------------
# The ordering port is not vacuous
# ---------------------------------------------------------------------------


def test_before_insert_cannot_see_the_document_name() -> None:
	"""The claim ``client_message_id`` cannot be minted in ``before_insert``, executed.

	If this goes red, Frappe changed its insert ordering and the derivation may move back —
	but nothing else in this suite would have told you.
	"""
	seen: list[Any] = []

	class Probe(ChatMessage):
		def before_insert(self) -> None:
			seen.append(self.name)
			super().before_insert()

	Probe(
		{
			"doctype": "Chat Message",
			"room": ROOM,
			"sender": USER,
			"sender_kind": "Human",
			"message_type": "Text",
			"text": "hi",
		}
	).insert()

	assert seen == [None], (
		"before_insert saw a document name. Frappe's set_new_name runs AFTER before_insert and "
		"nulls the name first (frappe/model/naming.py v16); if that changed, revisit "
		"outbox.derive_client_message_id."
	)


# ---------------------------------------------------------------------------
# seq allocation
# ---------------------------------------------------------------------------


def test_seq_is_allocated_from_the_room_counter_and_is_monotonic() -> None:
	first = send(text="one")
	second = send(text="two")
	third = send(text="three")

	assert [first.seq, second.seq, third.seq] == [1, 2, 3]
	assert FRAPPE.db.table("Chat Room")[ROOM]["seq_high_water"] == 3


def test_the_happy_path_never_selects_max_seq_from_the_message_table() -> None:
	"""``MAX(seq) … FOR UPDATE`` over ``tabChat Message`` is the rejected **allocator**.

	It takes range locks across the busiest table in the feature; the counter column takes
	one row lock. ``MAX(seq)`` is legitimate exactly once — as the *repair* after a collision
	has already proved the counter is behind — which is why this asserts about the path where
	nothing has gone wrong rather than about the string appearing anywhere.
	"""
	send(text="one")
	send(text="two")
	offenders = [q for q, _ in FRAPPE.db.queries if "max(seq)" in q]
	assert not offenders, (
		f"the write path issued {offenders!r} with no collision in sight. seq is allocated by "
		"incrementing Chat Room.seq_high_water under that row's lock; MAX(seq) over "
		"tabChat Message locks a range of the hottest table in the feature."
	)


def test_the_seq_allocation_holds_the_room_row() -> None:
	"""The ``UPDATE`` is what takes the lock, and it must target exactly one room row."""
	send(text="one")
	updates = [q for q, _ in FRAPPE.db.queries if q.startswith("update `tabchat room` set seq_high_water")]
	assert len(updates) == 1
	assert "where name = %(room)s" in updates[0]


def test_a_seq_collision_is_retried_rather_than_raised() -> None:
	"""``unique(room, seq)`` is the guarantee; losing a race for a number is not an error."""
	# Somebody wrote seq 1 outside the allocator - a restored dump, a hand-run backfill.
	FRAPPE.db.seed("Chat Message", "squatter", room=ROOM, seq=1, client_message_id="client-other")

	doc = make_message(text="mine")
	outbox.insert_message(doc)

	assert doc.seq == 2, "the second allocation should have taken the next free position"
	assert doc.name and doc.client_message_id.startswith("client-")
	assert not FRAPPE.local.message_log, (
		"the expected duplicate leaked Frappe's 'must be unique' msgprint to the user; "
		"show_unique_validation_message calls msgprint BEFORE raising and it has to be cleared"
	)
	assert (
		FRAPPE.db.table("Chat Room")[ROOM]["seq_high_water"] == 2
	), "the retry left the counter behind the tail, so the NEXT message collides too"


def test_the_retry_repairs_the_counter_outside_the_rolled_back_savepoint() -> None:
	"""The bug this suite found on its first run, pinned by name.

	``ROLLBACK TO SAVEPOINT`` undoes the ``UPDATE`` that allocated the sequence, so a retry
	that only re-allocates receives the number that just collided — five times, and then a
	user-visible error. The repair has to run after the rollback, and it is the one
	legitimate ``MAX(seq)``.
	"""
	FRAPPE.db.seed("Chat Message", "squatter", room=ROOM, seq=1, client_message_id="client-other")
	FRAPPE.db.seed("Chat Message", "squatter2", room=ROOM, seq=2, client_message_id="client-other2")

	doc = outbox.insert_message(make_message(text="mine"))
	assert doc.seq == 3, "the repair must catch the counter up to the real tail, not to tail+0"

	repairs = [q for q, _ in FRAPPE.db.queries if "max(seq)" in q]
	assert repairs, "no repair query ran, so the retry was allocating the same number again"


def test_a_gchat_message_name_collision_is_not_retried() -> None:
	"""Rule 2. A resource-name collision means the row already exists — the caller's business.

	Retrying it would insert the same Google message a second time under a fresh ``seq``,
	which is the precise outcome the unique index exists to prevent.
	"""
	FRAPPE.db.seed(
		"Chat Message",
		"stored",
		room=ROOM,
		seq=9,
		client_message_id="client-stored",
		gchat_message_name="spaces/AAAATESTSPACE/messages/abc",
	)
	doc = make_message(sync_origin="Google Chat", gchat_message_name="spaces/AAAATESTSPACE/messages/abc")
	with pytest.raises(UniqueValidationError):
		outbox.insert_message(doc)


def test_seq_high_water_cannot_be_moved_backwards() -> None:
	room = ChatRoom({"doctype": "Chat Room", "name": ROOM, "room_type": "Group"})
	room._inserted = True
	room.seq_high_water = 5
	FRAPPE.db.table("Chat Room")[ROOM]["seq_high_water"] = 12

	with pytest.raises(ValidationError) as caught:
		room.validate()
	assert "backwards" in str(caught.value)


# ---------------------------------------------------------------------------
# derived fields
# ---------------------------------------------------------------------------


def test_client_message_id_is_derived_from_the_final_name() -> None:
	doc = send(text="hello")
	assert doc.client_message_id == ids.client_message_id(doc.name, site=SITE)
	assert doc.client_message_id.startswith("client-")
	assert len(doc.client_message_id) <= 63


def test_client_message_id_is_not_reminted_on_a_later_save() -> None:
	"""An edit must not change the idempotency key Google has already seen."""
	doc = send(text="hello")
	minted = doc.client_message_id
	doc.text = "hello, edited"
	doc.validate()
	assert doc.client_message_id == minted


def test_thread_root_denormalises_from_the_parent() -> None:
	root = send(text="root")
	reply = send(text="reply", parent_message=root.name)
	nested = send(text="reply to reply", parent_message=reply.name)

	assert root.thread_root is None, "a root message stores NULL, not its own name"
	assert reply.thread_root == root.name
	assert nested.thread_root == root.name, "threading is one level; a nested reply keeps the root"


def test_text_plain_strips_markup_and_normalises_whitespace() -> None:
	assert outbox.extract_text_plain("<p>one</p><p>two</p>") == "one\ntwo"
	assert outbox.extract_text_plain("a<br>b") == "a\nb"
	assert outbox.extract_text_plain("&amp;&lt;tag&gt;") == "&<tag>"
	assert outbox.extract_text_plain("a\r\nb") == "a\nb"
	assert outbox.extract_text_plain("a\n\n\n\n\nb") == "a\n\nb"
	assert outbox.extract_text_plain("   ") == ""
	assert outbox.extract_text_plain(None) == ""


def test_preview_is_one_escaped_line_within_the_cap() -> None:
	preview = outbox.build_preview("<script>", limit=200)
	assert "<" not in preview and "&lt;" in preview

	long_preview = outbox.build_preview("x" * 500)
	assert len(long_preview) <= outbox.PREVIEW_MAX_CHARS
	assert long_preview.endswith("…")

	assert outbox.build_preview("a\nb\nc") == "a b c", "a preview is one line in a room list"


# ---------------------------------------------------------------------------
# the outbox row
# ---------------------------------------------------------------------------


def test_a_message_writes_exactly_one_outbox_row_in_the_same_transaction() -> None:
	doc = send(text="hello")
	jobs = relay_jobs()

	assert len(jobs) == 1
	job = jobs[0]
	assert job["room"] == ROOM
	assert job["operation"] == "Message Create"
	assert job["status"] == "Pending"
	assert job["job_seq"] == 1
	assert job["reference_doctype"] == "Chat Message"
	assert job["reference_name"] == doc.name
	assert job["impersonate_user"] == USER
	assert job["request_id"] == ids.request_id(doc.name, "Message Create", site=SITE)
	assert doc.sync_state == "Pending"


def test_the_outbox_payload_carries_identifiers_and_never_the_body() -> None:
	"""The relay reads the message row. A second copy of every body in a table with a
	different retention rule and a different permission story is not worth the convenience."""
	doc = send(text="a secret about a customer")
	payload = json.loads(relay_jobs()[0]["payload"])

	assert payload["message"] == doc.name
	assert payload["client_message_id"] == doc.client_message_id
	assert "text" not in payload
	assert "a secret" not in relay_jobs()[0]["payload"]


def test_job_seq_is_a_per_room_fifo() -> None:
	other_room = "room0000000000000000000000002"
	FRAPPE.db.seed(
		"Chat Room",
		other_room,
		provisioning_mode="On First Message",
		external_users_allowed=0,
		mirror_whitelisted=0,
		seq_high_water=0,
	)

	send(text="one")
	send(text="two")
	send(text="elsewhere", room=other_room)
	send(text="three")

	by_room: dict[str, list[int]] = {}
	for job in relay_jobs():
		by_room.setdefault(job["room"], []).append(job["job_seq"])

	assert sorted(by_room[ROOM]) == [1, 2, 3]
	assert sorted(by_room[other_room]) == [1], "job_seq is per room, not global"


def test_job_seq_allocation_takes_the_room_row_lock() -> None:
	"""The lock is on a table the statement does not read, which is easy to lose in a refactor."""
	send(text="one")
	locks = [q for q, _ in FRAPPE.db.queries if q.endswith("for update")]
	assert locks, (
		"allocate_job_seq read MAX(job_seq) without taking the Chat Room row lock first. "
		"Nothing serialises two allocators for the same room without it, and Rule 1 "
		"(CREATE-BEFORE-EDIT) is only as strong as that number."
	)


#: `frappe.enqueue`'s own parameters, taken from its signature. Everything else in the call is
#: forwarded to the worker verbatim — so a worker parameter that collides with one of these is
#: never passed anything, and a name that is not in the call at all is passed nothing either.
#: Both failures are a TypeError at run time, in a worker, where only the Error Log sees them.
#:
#: `job_name` is the trap: it reads like "the name of the job" and is exactly what a relay
#: worker wants to be called. It cost two releases — one failure mode each.
_ENQUEUE_OPTIONS = frozenset(
	{
		"method",
		"queue",
		"timeout",
		"event",
		"is_async",
		"job_name",
		"now",
		"enqueue_after_commit",
		"on_success",
		"on_failure",
		"at_front",
		"job_id",
		"deduplicate",
		"at_front_when_starved",
	}
)


def test_the_enqueue_is_after_commit_and_deduplicated_by_job_id() -> None:
	send(text="hello")

	# Filtered by method rather than counted. One send now schedules TWO jobs — the relay,
	# and Phase 4's notification fan-out off the `notify_new_message` seam — and a bare
	# `len(...) == 1` here asserted "the relay is the only thing anybody may ever enqueue",
	# which was never the intended claim and would have to be relaxed again by whoever adds
	# the third. Naming the method says what this test is actually about.
	relays = [c for c in REC.enqueues if c["method"] == outbox.RELAY_WORKER_PATH]
	assert len(relays) == 1, "exactly one relay job per message, and never two"
	call = relays[0]

	assert call["enqueue_after_commit"] is True, (
		"without enqueue_after_commit the worker can get_doc the job before the transaction "
		"commits and see nothing — an intermittent 'job not found' that only reproduces under load"
	)
	assert call["deduplicate"] is True
	assert call["job_id"], "deduplicate=True REQUIRES job_id and frappe.enqueue throws without it"
	# The kwarg is asserted against the WORKER'S SIGNATURE, not against a literal.
	#
	# This said `call["job"]`, matching a comment in outbox.py — while the worker has always
	# been `run_relay_job(job_name: str)`. So every enqueue died with TypeError before doing
	# anything, and this test stayed green because it was checking the enqueue against the
	# same wrong belief the enqueue was written from. A test that restates the code's
	# assumption cannot detect that the assumption is false; it has to reach the other side.
	#
	# Nothing noticed for weeks because the sweeper, not the queue, is the delivery guarantee
	# — messages went out one sweep late and looked fine.
	# Read from the worker's SOURCE rather than imported: this suite runs on a frappe stub, and
	# importing `outbound` pulls in the real framework. Source-level is also how the other
	# cross-module guards here work, and it is enough — the point is to consult the other side
	# of the contract instead of restating this side of it.
	import re

	worker_src = (
		pathlib.Path(__file__).resolve().parent.parent / "chat" / "sync" / "outbound.py"
	).read_text(encoding="utf-8")
	match = re.search(r"^def run_relay_job\(\s*(\w+)", worker_src, re.M)
	assert match, "run_relay_job has moved or changed shape; this guard needs updating"
	parameter = match.group(1)
	assert parameter not in _ENQUEUE_OPTIONS, (
		f"run_relay_job's parameter is {parameter!r}, which frappe.enqueue takes as its own — so "
		"the kwarg is consumed by the enqueue machinery and the worker is handed nothing. It "
		"fails with 'missing 1 required positional argument', which reads like a caller bug and "
		"is not one. Rename the worker's parameter."
	)
	assert parameter in call, (
		f"the enqueue passes {sorted(k for k in call if k not in _ENQUEUE_OPTIONS)!r} but the "
		f"worker takes {parameter!r}. frappe.enqueue forwards kwargs verbatim, so this is a "
		"TypeError on every job — and one that only shows up in the Error Log of a worker."
	)
	assert call[parameter] == relay_jobs()[0]["name"]


def test_a_send_also_schedules_the_notification_fan_out_exactly_once() -> None:
	"""The other half of what a send schedules, pinned here so it cannot quietly disappear.

	The fan-out is what turns a message into a bell row and a push, and it is invisible when
	it stops happening — no error, no log, just nobody being told. Its absence would otherwise
	be caught by nothing in the bench-free tier, because every other assertion in this file is
	about the relay.
	"""
	send(text="hello")

	fanouts = [
		c
		for c in REC.enqueues
		if c["method"] == "erpnext_enhancements.chat.notifications.fanout.run_fanout"
	]
	assert len(fanouts) == 1, "one message, one fan-out — twice would notify everybody twice"
	assert fanouts[0]["enqueue_after_commit"] is True, (
		"a fan-out that starts before its transaction lands reads a message row that does not "
		"exist yet, and tells nobody about it"
	)


def test_a_failed_enqueue_does_not_fail_the_message() -> None:
	"""The row is committed and the sweeper is the delivery guarantee; Redis is not."""

	def boom(*_a: Any, **_k: Any) -> None:
		raise RuntimeError("redis is down")

	FRAPPE.enqueue = boom
	try:
		doc = send(text="hello")
	finally:
		FRAPPE.enqueue = lambda method, **kwargs: REC.enqueues.append({"method": method, **kwargs})

	assert doc.name
	assert len(relay_jobs()) == 1
	assert REC.errors and "enqueue failed" in REC.errors[0]["title"]


# ---------------------------------------------------------------------------
# relay refusal and the gate
# ---------------------------------------------------------------------------


def test_create_relay_job_refuses_a_google_chat_origin_row() -> None:
	"""The cheapest guard in the system, asserted by name."""
	with pytest.raises(outbox.RelayRefused):
		outbox.create_relay_job(
			room=ROOM,
			operation="Message Create",
			reference_doctype="Chat Message",
			reference_name="whatever",
			origin="Google Chat",
		)
	assert not relay_jobs()


def test_an_inbound_message_is_marked_inbound_and_never_relayed() -> None:
	doc = send(
		sync_origin="Google Chat",
		sender=None,
		sender_email="external@partner.invalid",
		gchat_message_name="spaces/AAAATESTSPACE/messages/xyz",
	)
	assert doc.sync_state == "Inbound"
	assert not relay_jobs()


@pytest.mark.parametrize(
	"switch",
	["enabled", "google_sync_enabled", "relay_outbound_enabled"],
)
def test_a_rollout_switch_being_off_writes_no_outbox_row(switch: str) -> None:
	SETTINGS[switch] = 0
	doc = send(text="hello")
	assert not relay_jobs()
	assert doc.sync_state == "Not Mirrored", (
		"a message with no outbox row behind it must not render as Pending forever, and the "
		"health report's Pending count has to stay meaningful"
	)


def test_pause_outbound_still_writes_the_outbox_row() -> None:
	"""The kill switch defers; it does not drop. Flipping it back drains the backlog in order."""
	SETTINGS.pause_outbound = 1
	doc = send(text="hello")
	assert len(relay_jobs()) == 1
	assert doc.sync_state == "Pending"


def test_an_unmirrored_room_writes_no_outbox_row() -> None:
	FRAPPE.db.table("Chat Room")[ROOM]["provisioning_mode"] = "Not Mirrored"
	doc = send(text="hello")
	assert not relay_jobs()
	assert doc.sync_state == "Not Mirrored"


def test_a_room_with_external_members_is_not_mirrored_without_the_whitelist() -> None:
	"""§4.H data-egress: mirroring a room an outsider can read sends company conversation
	to somebody the org chart does not cover, and only a human may approve that."""
	FRAPPE.db.table("Chat Room")[ROOM]["external_users_allowed"] = 1
	stored = send(text="hello")
	assert stored.name, "the message is still stored — ERPNext is the record of what was said"
	assert not relay_jobs()

	FRAPPE.db.table("Chat Room")[ROOM]["mirror_whitelisted"] = 1
	send(text="approved now")
	assert len(relay_jobs()) == 1


def test_a_system_message_is_not_relayed() -> None:
	send(message_type="System", text="Bob joined the room")
	assert not relay_jobs()


# ---------------------------------------------------------------------------
# CQ-11 — oversized messages
# ---------------------------------------------------------------------------


def _oversized_text() -> str:
	# CJK is three UTF-8 bytes per character, so this is ~36,000 bytes from 12,000 characters
	# — the case len(text) would wave through.
	return "水" * 12_000


def test_truncate_with_link_sets_the_flag_and_appends_a_deep_link() -> None:
	doc = send(text=_oversized_text())
	body, truncated = outbox.relay_text(doc)

	assert truncated is True
	assert doc.truncated_for_relay == 1
	assert FRAPPE.db.table("Chat Message")[doc.name]["truncated_for_relay"] == 1
	assert f"{SITE_URL}/chat/room/{doc.room}?message={doc.name}" in body
	assert len(body.encode("utf-8")) <= 32000 - outbox.ENVELOPE_ESTIMATE_BYTES
	assert "水" in body, "the truncated relay copy still carries the beginning of the message"


def test_truncation_records_the_flag_with_update_modified_false() -> None:
	"""The D6 watermark is ``(max(seq), count(*), max(modified))``; churning ``modified``
	invalidates every cached digest for the room."""
	doc = send(text=_oversized_text())
	calls = [c for c in FRAPPE.db.set_value_calls if c["doctype"] == "Chat Message" and c["name"] == doc.name]
	assert calls and all(c["update_modified"] is False for c in calls)


def test_reject_refuses_an_oversized_message_at_compose_time() -> None:
	SETTINGS.oversized_message_policy = "Reject"
	with pytest.raises(ValidationError) as caught:
		send(text=_oversized_text())
	assert "bytes" in str(caught.value)
	assert not FRAPPE.db.rows("Chat Message"), "nothing may be stored when the policy rejects"


def test_reject_does_not_fire_for_a_message_that_would_not_be_relayed() -> None:
	"""Otherwise Google's transport limit dictates what an employee may say inside ERPNext
	in a room with no mirror on the other end — which inverts decision #1."""
	SETTINGS.oversized_message_policy = "Reject"
	FRAPPE.db.table("Chat Room")[ROOM]["provisioning_mode"] = "Not Mirrored"
	doc = send(text=_oversized_text())
	assert doc.name and doc.sync_state == "Not Mirrored"


def test_reject_never_blocks_an_inbound_message() -> None:
	"""ERPNext is the record of what was said; a Chat message we cannot store is a hole in it."""
	SETTINGS.oversized_message_policy = "Reject"
	doc = send(
		sync_origin="Google Chat",
		sender=None,
		sender_email="external@partner.invalid",
		text=_oversized_text(),
	)
	assert doc.name and doc.sync_state == "Inbound"


# ---------------------------------------------------------------------------
# §4.F — edit and delete propagation, ERPNext → Chat
# ---------------------------------------------------------------------------


def test_an_edit_and_a_soft_delete_queue_jobs_behind_the_create_in_job_seq_order() -> None:
	"""Rule 1, CREATE-BEFORE-EDIT, obtained for free — and the finding this section exists for.

	Before this wiring landed the outbound half of §4.F was dead code: ``outbound`` registered
	``Message Update`` / ``Message Delete`` handlers that nothing outside inbound's Rule-3
	revert could ever reach, so an ERPNext edit reached Chat never and an ERPNext delete left
	the message on screen in the space forever.

	The ordering is not asserted for its own sake: the worker drains a room by ascending
	``job_seq``, so "create, then edit, then delete" is the *only* sequence in which the three
	calls are individually valid.
	"""
	doc = send(text="hello")
	edit(doc, text="hello, corrected")
	soft_delete(doc)

	jobs = sorted(relay_jobs(), key=lambda j: j["job_seq"])
	assert [j["operation"] for j in jobs] == ["Message Create", "Message Update", "Message Delete"]
	assert [j["job_seq"] for j in jobs] == [1, 2, 3]
	assert all(j["reference_doctype"] == "Chat Message" for j in jobs)
	assert all(j["reference_name"] == doc.name for j in jobs)
	assert all(j["status"] == "Pending" for j in jobs)
	assert all(j["impersonate_user"] == USER for j in jobs), (
		"the author is the only identity allowed to patch or delete their own message — app "
		"auth may only touch what the app created"
	)


def test_the_edit_and_delete_payloads_carry_identifiers_and_never_the_body() -> None:
	doc = send(text="the original")
	edit(doc, text="a secret about a customer")
	soft_delete(doc)

	rows = sorted(relay_jobs(), key=lambda j: j["job_seq"])[1:]
	kinds = [json.loads(r["payload"])["kind"] for r in rows]
	assert kinds == ["message.update", "message.delete"]
	for row in rows:
		assert json.loads(row["payload"])["message"] == doc.name
		assert "a secret" not in row["payload"]


def test_a_save_that_did_not_touch_the_text_queues_nothing() -> None:
	"""The budget is one write per second per space; a job per save would spend it on no-ops.

	``sync_state`` is written by the relay itself, so a rule of "enqueue on every save" would
	have the relay's own completion write queue the next edit — a loop, at one write a second,
	in a table nobody is watching.
	"""
	doc = send(text="hello")
	edit(doc, sync_state="Relayed", is_edited=0)
	assert len(relay_jobs()) == 1


def test_the_insert_itself_does_not_also_queue_an_edit() -> None:
	"""``insert()`` runs ``on_update`` too, and ``has_value_changed`` returns True with no
	before-image — so the obvious implementation relays every message twice."""
	send(text="hello")
	assert [j["operation"] for j in relay_jobs()] == ["Message Create"]


def test_an_inbound_row_that_is_edited_or_deleted_never_queues_an_outbound_job() -> None:
	"""A coworker's own edit, relayed back at them, is the echo loop with an extra step."""
	doc = send(
		sync_origin="Google Chat",
		sender=None,
		sender_email="external@partner.invalid",
		gchat_message_name="spaces/AAAATESTSPACE/messages/xyz",
	)
	edit(doc, text="edited over in Chat")
	soft_delete(doc)
	assert not relay_jobs()


@pytest.mark.parametrize("switch", ["enabled", "google_sync_enabled", "relay_outbound_enabled"])
def test_an_edit_respects_the_same_rollout_switches_as_the_create(switch: str) -> None:
	doc = send(text="hello")
	SETTINGS[switch] = 0
	edit(doc, text="edited")
	soft_delete(doc)
	assert len(relay_jobs()) == 1, "only the create, from before the switch went off"


def test_an_edit_in_an_unmirrored_room_queues_nothing() -> None:
	FRAPPE.db.table("Chat Room")[ROOM]["provisioning_mode"] = "Not Mirrored"
	doc = send(text="hello")
	edit(doc, text="edited")
	soft_delete(doc)
	assert not relay_jobs()


def test_an_undelete_is_not_relayed() -> None:
	"""There is no such call. A Chat delete is permanent, so a restore has no counterpart —
	and queuing a create for a message Google already tombstoned would 409 on ``messageId``."""
	doc = send(text="hello")
	soft_delete(doc)
	edit(doc, is_deleted=0)
	assert [j["operation"] for j in sorted(relay_jobs(), key=lambda j: j["job_seq"])] == [
		"Message Create",
		"Message Delete",
	]


def test_a_delete_supersedes_an_edit_made_in_the_same_save() -> None:
	"""One job, not two. Google's tombstone is content-free, so patching the text of a message
	we are about to delete spends a write from a one-per-second budget to change nothing."""
	doc = send(text="hello")
	edit(doc, text="edited and deleted at once", is_deleted=1)
	assert [j["operation"] for j in sorted(relay_jobs(), key=lambda j: j["job_seq"])] == [
		"Message Create",
		"Message Delete",
	]


def test_a_hard_delete_is_refused_and_relays_nothing() -> None:
	"""``on_trash`` refuses rather than relaying, and the refusal is the point.

	The delete is a **soft** delete that keeps ``text``, because Google's tombstone returns
	the delete time and no content — so a hard delete destroys the last copy of what was said.
	It also could not work: the relay job addresses the message by ``reference_name`` and reads
	``client_message_id`` off the row, both of which a hard delete has already taken away.
	"""
	doc = send(text="hello")
	with pytest.raises(ValidationError) as caught:
		doc.delete()

	assert "is_deleted" in str(caught.value)
	assert doc.name in FRAPPE.db.table("Chat Message"), "the row must survive the refusal"
	assert [j["operation"] for j in relay_jobs()] == ["Message Create"]


def test_a_hard_delete_is_permitted_during_a_bootstrap() -> None:
	"""An install, a migrate or a patch is not somebody deleting a colleague's message, and a
	controller that refused there would brick a rollback of its own fixtures."""
	doc = send(text="fixture")
	FRAPPE.flags.in_patch = 1
	doc.delete()
	assert doc.name not in FRAPPE.db.table("Chat Message")


def test_an_edit_refreshes_text_plain_so_the_relay_sends_the_NEW_body() -> None:
	"""The bug that makes the whole edit path a no-op if it is missed.

	``text_plain`` was computed in ``before_insert`` and never again, and both the byte budget
	and the relay worker read ``text_plain`` in preference to ``text`` — so an edit would have
	queued a ``Message Update`` that patched Chat with the *original* wording.
	"""
	doc = send(text="<p>first</p>")
	edit(doc, text="<p>second</p>")

	assert doc.text_plain == "second"
	assert FRAPPE.db.table("Chat Message")[doc.name]["text_plain"] == "second"
	body, _truncated = outbox.relay_text(doc)
	assert body == "second"


def test_an_edit_across_the_byte_ceiling_moves_truncated_for_relay_both_ways() -> None:
	"""CQ-11 on the edit path. An edit can cross 32,000 bytes just as a create can, and — the
	half the create path never has to handle — an edit can also cross back."""
	doc = send(text="short")
	assert not _cint(getattr(doc, "truncated_for_relay", 0))

	edit(doc, text=_oversized_text())
	assert doc.truncated_for_relay == 1
	assert FRAPPE.db.table("Chat Message")[doc.name]["truncated_for_relay"] == 1

	edit(doc, text="short again")
	assert doc.truncated_for_relay == 0, "the flag is a fact about the current body, not a scar"

	calls = [c for c in FRAPPE.db.set_value_calls if c["fieldname"] == "truncated_for_relay"]
	assert calls and all(
		c["update_modified"] is False for c in calls
	), "the D6 watermark reads max(modified); churning it invalidates every cached digest"


def test_an_edit_and_a_delete_mark_the_room_context_stale_even_when_nothing_relays() -> None:
	"""Phase 5 invalidation is an ERPNext-side fact and does not depend on Google.

	Gating it on the relay is how a summary ends up quoting a message the user deleted in a
	room that was never mirrored in the first place.
	"""
	FRAPPE.db.table("Chat Room")[ROOM]["provisioning_mode"] = "Not Mirrored"
	doc = send(text="hello")
	seams.reset_counters()

	edit(doc, text="edited")
	soft_delete(doc)

	assert not relay_jobs()
	assert seams.counters()[seams.COUNTER_CONTEXT_STALE] == 2


def test_an_edit_during_a_bootstrap_has_no_side_effects() -> None:
	doc = send(text="hello")
	FRAPPE.flags.in_migrate = 1
	edit(doc, text="a patch rewrote this")
	assert len(relay_jobs()) == 1
	assert not revisions(), "a migrate rewriting a row is not somebody editing a message"


# ---------------------------------------------------------------------------
# The audit trail — §4.F, the ERPNext-originated half
# ---------------------------------------------------------------------------


def test_an_erpnext_edit_writes_a_revision_carrying_both_bodies() -> None:
	"""§4.F: a revision on **every** mutation, not only the ones Chat performed.

	``inbound.record_revision`` was the only writer of this table, so an edit made in ERPNext
	left no audit row at all — which is the half of the trail a user is most likely to ask
	about, because it is the half they performed themselves.
	"""
	doc = send(text="the original wording")
	edit(doc, text="the wording I meant")

	rows = revisions()
	assert [r["change_type"] for r in rows] == ["Edit"]
	assert rows[0]["message"] == doc.name
	assert rows[0]["room"] == ROOM, "backfilled by the controller; the oversight query needs it"
	assert rows[0]["origin"] == "ERPNext"
	assert rows[0]["text_before"] == "the original wording"
	assert rows[0]["text_after"] == "the wording I meant"
	assert rows[0]["actor"] == USER and rows[0]["actor_email"] == USER
	assert rows[0]["revision_no"] == 1


def test_an_erpnext_delete_writes_a_revision_carrying_the_deleted_body() -> None:
	"""The branch that matters most: this is where the deleted body is preserved.

	Google's tombstone carries no content, so after the relay this row and the message row are
	between them the last copies of what was said — and the message row is the one a Phase 6
	retention run is allowed to remove.
	"""
	doc = send(text="something an e-discovery request will ask about")
	soft_delete(doc)

	rows = revisions()
	assert [r["change_type"] for r in rows] == ["Delete"]
	assert rows[0]["text_before"] == "something an e-discovery request will ask about"
	assert rows[0]["text_after"] == ""
	assert rows[0]["origin"] == "ERPNext"


def test_the_revision_reads_the_body_BEFORE_the_row_was_written() -> None:
	"""The ordering trap. ``on_update`` runs **after** the row is saved.

	Reading ``text`` off the database here would record a transition from the new body to
	itself — an audit row that looks complete and says nothing. Two successive edits make the
	mistake visible: revision 2's ``text_before`` must be revision 1's ``text_after``.
	"""
	doc = send(text="one")
	edit(doc, text="two")
	edit(doc, text="three")

	rows = sorted(revisions(), key=lambda r: r["revision_no"])
	assert [(r["text_before"], r["text_after"]) for r in rows] == [("one", "two"), ("two", "three")]
	assert [r["revision_no"] for r in rows] == [1, 2]


@pytest.mark.parametrize(
	("arrange", "why"),
	[
		(
			lambda: FRAPPE.db.table("Chat Room")[ROOM].update({"provisioning_mode": "Not Mirrored"}),
			"a room nobody mirrors still has an audit trail",
		),
		(lambda: SETTINGS.update({"relay_outbound_enabled": 0}), "a rollout switch is not a retention policy"),
		(
			lambda: FRAPPE.db.table("Chat Room")[ROOM].update({"external_users_allowed": 1}),
			"an egress refusal is about what leaves the company, not about what we record",
		),
	],
)
def test_the_audit_trail_is_not_conditional_on_the_mirror(arrange: Any, why: str) -> None:
	"""Every reason not to *send* a Chat write, and none of them is a reason not to *record*."""
	doc = send(text="before")
	arrange()
	jobs_before = len(relay_jobs())

	edit(doc, text="after")
	soft_delete(doc)

	assert len(relay_jobs()) == jobs_before, "arranged a refusal and the relay went anyway"
	assert [r["change_type"] for r in sorted(revisions(), key=lambda r: r["revision_no"])] == [
		"Edit",
		"Delete",
	], why


def test_an_inbound_row_edited_in_erpnext_is_still_audited() -> None:
	"""A Google-origin row refuses the relay (the echo loop) and still records the change."""
	doc = send(text="typed in chat", sync_origin="Google Chat")
	edit(doc, text="corrected in erpnext")

	assert not relay_jobs(), "relaying an inbound row back to Chat is the echo loop"
	assert [r["change_type"] for r in revisions()] == ["Edit"]
	assert revisions()[0]["origin"] == "ERPNext", (
		"`origin` is who made THIS change, not where the message came from — the row's own "
		"sync_origin already records that"
	)


def test_a_delete_supersedes_an_edit_in_the_audit_trail_too() -> None:
	"""One save, one revision, and it is the delete — matching the relay's own precedence."""
	doc = send(text="original")
	edit(doc, text="rewritten and deleted in one save", is_deleted=1)

	rows = revisions()
	assert [r["change_type"] for r in rows] == ["Delete"]
	assert rows[0]["text_before"] == "original", (
		"what the delete buried is what was on the row before this save"
	)


def test_a_save_that_changes_nothing_writes_no_revision() -> None:
	"""Including the insert itself, and including the relay's own ``sync_state`` write.

	An audit row per save would put two copies of every message body into this table for every
	status update the relay makes.
	"""
	doc = send(text="hello")
	assert not revisions(), "the insert is a Create, and inbound records that; on_update must not"

	edit(doc, sync_state="Relayed")
	assert not revisions()


def test_an_undelete_writes_no_revision_because_there_is_no_word_for_it() -> None:
	"""Pinned rather than hidden: ``change_type`` is ``Create/Edit/Delete`` and nothing else.

	An ERPNext restore has no Chat counterpart either — the deletion is permanent there and
	there is no call to reverse it — so the row silently changes state and the trail does not
	say so. Recording it as an ``Edit`` would be a lie in the column an auditor filters on.
	Widening the vocabulary is a schema change and belongs with the Phase 6 oversight work.
	"""
	doc = send(text="hello")
	soft_delete(doc)
	assert len(revisions()) == 1

	edit(doc, is_deleted=0)
	assert len(revisions()) == 1


def test_the_two_writers_share_one_numbered_trail() -> None:
	"""``revision_no`` is per message and not per writer, so the trail reconstructs in order.

	Inbound records the ``Create`` for a Chat-origin row; ERPNext then edits it here. If this
	writer allocated its own numbering — or hard-coded 1 — the two would interleave into a
	sequence that reads as two competing histories of one message.
	"""
	doc = send(text="one", sync_origin="Google Chat")
	FRAPPE.db.seed(
		"Chat Message Revision",
		"rev-create",
		message=doc.name,
		room=ROOM,
		revision_no=1,
		change_type="Create",
		origin="Google Chat",
	)

	edit(doc, text="two")

	rows = sorted(revisions(), key=lambda r: r["revision_no"])
	assert [(r["revision_no"], r["change_type"], r["origin"]) for r in rows] == [
		(1, "Create", "Google Chat"),
		(2, "Edit", "ERPNext"),
	]


def test_a_revision_number_collision_is_absorbed_and_never_reaches_the_user() -> None:
	"""``unique(message, revision_no)`` is what stops two audit rows claiming to be revision 4.

	The allocation is ``max + 1`` inside the caller's transaction, so two concurrent writers can
	compute the same number — the constraint decides, and the loser treats the refusal as
	success. Forced here rather than raced, because a race cannot be made deterministic and the
	branch that matters is what the *loser* does: not raise, and not leak Frappe's
	``must be unique`` msgprint into the face of somebody editing a message.
	"""
	from erpnext_enhancements.chat.doctype.chat_message_revision.chat_message_revision import (
		ChatMessageRevision,
	)

	doc = send(text="one")
	edit(doc, text="two")
	assert len(revisions()) == 1

	original = ChatMessageRevision.next_revision_no
	ChatMessageRevision.next_revision_no = staticmethod(lambda message: 1)  # type: ignore[method-assign]
	try:
		edit(doc, text="three")
	finally:
		ChatMessageRevision.next_revision_no = original  # type: ignore[method-assign]

	assert len(revisions()) == 1, "the collision was refused by the index, as it should be"
	assert FRAPPE.db.table("Chat Message")[doc.name]["text"] == "three", (
		"and it did not take the user's edit down with it"
	)
	assert REC.messages, "the stub never emitted the msgprint, so the clear proves nothing"
	assert not FRAPPE.local.message_log, (
		"the expected duplicate leaked Frappe's 'must be unique' msgprint; "
		"show_unique_validation_message calls msgprint BEFORE raising"
	)


def test_the_edit_and_delete_jobs_carry_no_request_id() -> None:
	"""Deliberate, and the reason is worth the assertion.

	``messages.patch`` and ``messages.delete`` take no ``requestId`` parameter at all, and a
	deterministic one derived from the message name would repeat across two successive edits
	of the same message — a value that reads like a replay of the first edit to anyone
	debugging the queue. Idempotency here comes from the ``client-`` alias instead: a patch is
	idempotent by construction and a delete's 404 is success.
	"""
	doc = send(text="hello")
	edit(doc, text="edited")
	soft_delete(doc)

	rows = sorted(relay_jobs(), key=lambda j: j["job_seq"])
	assert rows[0]["request_id"], "the create still sends one — messages.create accepts it"
	assert not rows[1]["request_id"]
	assert not rows[2]["request_id"]


# ---------------------------------------------------------------------------
# room denormalisation, read marks, realtime, seams
# ---------------------------------------------------------------------------


def test_the_room_tail_is_denormalised_without_touching_modified() -> None:
	doc = send(text="the last thing said")
	room = FRAPPE.db.table("Chat Room")[ROOM]

	assert room["last_message"] == doc.name
	assert room["last_message_sender"] == USER
	assert room["last_message_preview"] == "the last thing said"
	assert "modified" not in room, (
		"Chat Room.modified must move when somebody renames or archives the room, not on every "
		"message — and the room digest watermark reads it"
	)


def test_the_preview_stored_on_the_room_is_escaped() -> None:
	send(text="<b>bold</b> & dangerous")
	preview = FRAPPE.db.table("Chat Room")[ROOM]["last_message_preview"]
	assert "<b>" not in preview
	assert "&amp;" in preview


def test_the_author_read_mark_advances_and_never_retreats() -> None:
	first = send(text="one")
	second = send(text="two")
	member = next(r for r in FRAPPE.db.rows("Chat Room Member") if r["user"] == USER)
	assert member["last_read_seq"] == second.seq

	chat_room_member.advance_read_mark(ROOM, USER, first.seq)
	assert member["last_read_seq"] == second.seq, "a read mark is monotonic"

	other = next(r for r in FRAPPE.db.rows("Chat Room Member") if r["user"] == OTHER)
	assert other["last_read_seq"] == 0, "only the author's own mark moves"


def test_a_missing_membership_row_cannot_fail_the_insert() -> None:
	"""An inbound message can legitimately come from somebody who has left the space."""
	FRAPPE.db.tables["Chat Room Member"] = {}
	doc = send(text="hello")
	assert doc.name


def test_the_realtime_payload_carries_identifiers_and_no_body() -> None:
	doc = send(text="something private")
	assert len(REC.publishes) == 1
	published = REC.publishes[0]

	assert published["room"] == f"doc:Chat Room/{ROOM}"
	assert published["after_commit"] is True
	assert "task_id" not in published
	assert published["event"] == "chat_message_created"
	assert published["message"]["message"] == doc.name
	assert published["message"]["seq"] == doc.seq
	assert "something private" not in json.dumps(published["message"])
	assert "text" not in published["message"]


def test_a_realtime_failure_does_not_roll_back_the_message() -> None:
	def boom(**_k: Any) -> None:
		raise RuntimeError("socket server is gone")

	FRAPPE.publish_realtime = boom
	try:
		doc = send(text="hello")
	finally:
		FRAPPE.publish_realtime = lambda **kwargs: REC.publishes.append(dict(kwargs))

	assert doc.name in FRAPPE.db.table("Chat Message")
	assert REC.errors and "realtime publish failed" in REC.errors[0]["title"]
	assert "hello" not in REC.errors[0]["message"], "the error log must never carry a body"


def test_notify_new_message_fires_exactly_once_per_insert() -> None:
	"""The cheapest proof the sync engine is not duplicating."""
	send(text="one")
	send(text="two")
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 2


def test_notify_new_message_is_silent_during_an_import() -> None:
	SETTINGS.import_mode_enabled = 1
	send(text="historical")
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 0
	assert len(relay_jobs()) == 1, "a backfilled message still mirrors; it just notifies nobody"


def test_a_row_that_is_born_deleted_notifies_nobody_and_announces_nothing() -> None:
	"""The inbound ingest lands a tombstone directly when Google's resource is already one.

	A ``deleted`` event that overtook its ``created``, or a reconciliation replay weeks later:
	see ``inbound._apply_created``. Notifying somebody about a message that is already deleted
	is worse than not notifying at all, and a ``chat_message_created`` event would push the SPA
	to render a row every read path filters out.

	The row is still **stored** and still denormalises onto the room, because ERPNext is the
	record that the message existed — asserted here so the suppression cannot quietly grow into
	"a deleted arrival is dropped".
	"""
	doc = send(text="deleted in chat before we saw it", sync_origin="Google Chat", is_deleted=1)

	assert doc.name in FRAPPE.db.table("Chat Message")
	assert doc.seq == 1
	assert FRAPPE.db.table("Chat Room")[ROOM]["last_message"] == doc.name
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 0
	assert not REC.publishes


def test_an_ordinary_insert_still_notifies_and_publishes() -> None:
	"""The other half of the branch above — a guard nobody can trust is one that never lets go."""
	send(text="alive")
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 1
	assert [p["event"] for p in REC.publishes] == ["chat_message_created"]


def test_a_bootstrap_insert_has_no_side_effects() -> None:
	"""``doc_events`` fire during ERPNext's own test bootstrap, before this app's fields exist."""
	FRAPPE.flags.in_install = 1
	doc = send(text="fixture")
	assert doc.seq == 1, "seq is still allocated — a message row without one is broken"
	assert not relay_jobs()
	assert not REC.publishes


# ---------------------------------------------------------------------------
# attribution — the wave 1 rule, extended and not duplicated
# ---------------------------------------------------------------------------


def test_a_message_with_neither_sender_nor_sender_email_is_refused() -> None:
	with pytest.raises(ValidationError) as caught:
		send(sender=None, sender_email=None)
	assert "Sender" in str(caught.value)


def test_sender_email_alone_is_enough() -> None:
	doc = send(sender=None, sender_email="external@partner.invalid", sync_origin="Google Chat")
	assert doc.name


# ---------------------------------------------------------------------------
# The AST guard: no document event may reach Google
# ---------------------------------------------------------------------------

APP_DIR: Path = Path(__file__).resolve().parents[1]
CHAT_DIR: Path = APP_DIR / "chat"

#: Every module reachable from a ``Chat Message`` / ``Chat Room`` / ``Chat Room Member``
#: document event. A Chat API call from any of them runs inside the inserting transaction,
#: on a web worker, so a Google timeout becomes a failed message insert.
DOC_EVENT_MODULES: tuple[Path, ...] = (
	CHAT_DIR / "doctype" / "chat_message" / "chat_message.py",
	CHAT_DIR / "doctype" / "chat_room" / "chat_room.py",
	CHAT_DIR / "doctype" / "chat_room_member" / "chat_room_member.py",
	CHAT_DIR / "sync" / "outbox.py",
)

#: Import roots that mean "this module can speak to Google". ``chat.gchat.ids`` is
#: deliberately absent: it is pure identifier derivation with no transport in it, and the
#: write path legitimately needs it.
FORBIDDEN_IMPORTS: frozenset[str] = frozenset(
	{
		"requests",
		"urllib.request",
		"http.client",
		"httplib2",
		"googleapiclient",
		"google.auth",
		"socket",
		"erpnext_enhancements.chat.gchat.client",
		"erpnext_enhancements.chat.gchat.auth",
		"erpnext_enhancements.chat.gchat.events_client",
	}
)

#: Hosts no document-event module may name in a literal. Kept in step with
#: ``tests/test_chat_guardrails.py``'s ``PERMITTED_GOOGLE_HOSTS``.
FORBIDDEN_HOSTS: frozenset[str] = frozenset(
	{
		"chat.googleapis.com",
		"oauth2.googleapis.com",
		"iamcredentials.googleapis.com",
		"workspaceevents.googleapis.com",
		"metadata.google.internal",
	}
)


def google_surface_violations(paths: tuple[Path, ...] = DOC_EVENT_MODULES) -> list[str]:
	"""Return one string per violation. Empty means invariant I1 holds at source level.

	Exposed as a function rather than inlined into the test so that
	``tests/test_chat_guardrails.py`` can call it once the orchestrator wires it in — the
	guardrail suite is the natural home for a source-level lint, and this file owns the
	behavioural half.
	"""
	violations: list[str] = []
	for path in paths:
		if not path.exists():
			violations.append(f"{path.name}: expected to exist and does not — the guard is vacuous")
			continue
		tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
		for node in ast.walk(tree):
			if isinstance(node, ast.Import):
				for alias in node.names:
					if _forbidden_module(alias.name):
						violations.append(f"{path.name}:{node.lineno} imports {alias.name}")
			elif isinstance(node, ast.ImportFrom):
				module = node.module or ""
				if _forbidden_module(module):
					violations.append(f"{path.name}:{node.lineno} imports from {module}")
			elif isinstance(node, ast.Constant) and isinstance(node.value, str):
				for host in FORBIDDEN_HOSTS:
					if host in node.value:
						violations.append(f"{path.name}:{node.lineno} names {host} in a literal")
	return violations


def _forbidden_module(name: str) -> bool:
	return any(name == root or name.startswith(root + ".") for root in FORBIDDEN_IMPORTS)


def test_no_document_event_module_can_reach_google() -> None:
	"""Invariant I1, at source level.

	A Chat API call inside a document event runs inside the inserting transaction and on a
	web worker: a Google timeout becomes a *failed message insert*. ERPNext is the system of
	record and Chat is the mirror, and a mirror that can refuse a write is not a mirror. The
	insert writes a ``Chat Relay Job`` row and stops.
	"""
	violations = google_surface_violations()
	assert not violations, (
		"a document-event module reached the Google surface:\n  "
		+ "\n  ".join(violations)
		+ "\nWrite a Chat Relay Job row instead and let the relay worker make the call."
	)


def test_the_ast_guard_is_not_vacuous(tmp_path: Path) -> None:
	"""A guard that never fires is a guard nobody can trust."""
	offender = tmp_path / "offender.py"
	offender.write_text("import requests\nHOST = 'chat.googleapis.com'\n", encoding="utf-8")
	found = google_surface_violations((offender,))
	assert len(found) == 2, found


# --------------------------------------------------------------------------------------
# Which Google identity a job is written as (v1.279.0)
# --------------------------------------------------------------------------------------
#
# The decision is one line of code and the whole reason Triton can reply without anybody
# buying a Workspace seat, so it is asserted rather than left to the caller's memory.


def test_a_triton_reply_is_written_as_the_app() -> None:
	"""The app is *added* to a space, not licensed. That is the entire point of this branch."""
	assert outbox.auth_identity_for_origin(outbox.ORIGIN_TRITON) == states.AUTH_IDENTITY_APP


def test_a_coworker_mirror_is_written_as_the_human() -> None:
	"""CQ-1. An App badge on somebody's own message is a lie about who spoke, and app auth
	cannot patch or delete a message the app did not create — so an edit would fail later."""
	assert outbox.auth_identity_for_origin(outbox.ORIGIN_ERPNEXT) == states.AUTH_IDENTITY_USER


def test_an_unknown_or_missing_origin_defaults_to_the_human() -> None:
	"""Fails towards the identity that cannot silently re-attribute anybody.

	Defaulting to APP would turn any origin nobody thought about into a message stamped as the
	app — which is a wrong author, applied quietly, to a coworker's words.
	"""
	for origin in ("", None, "Something New"):
		assert outbox.auth_identity_for_origin(origin) == states.AUTH_IDENTITY_USER


def test_the_auth_identity_backfill_is_registered_and_cannot_overrule_the_writer() -> None:
	"""The patch that unblocks the replies queued before ``auth_identity`` existed.

	Two properties, and the second is the one worth a test rather than a code review:

	* it is in ``patches.txt`` — an unregistered patch is a file, not a migration;
	* its Triton update is guarded on the column being **empty**, so it can only ever fill a
	  row nobody decided. A backfill able to rewrite a ``USER`` is a backfill able to
	  re-attribute a coworker's own message to the app, which is the single outcome CQ-1
	  exists to prevent — and it would do it to a whole queue at once, on deploy, silently.
	"""
	root = pathlib.Path(__file__).resolve().parents[1]
	registered = (root / "patches.txt").read_text(encoding="utf-8")
	assert "erpnext_enhancements.patches.backfill_relay_auth_identity" in registered

	source = (root / "patches" / "backfill_relay_auth_identity.py").read_text(encoding="utf-8")
	triton_update = source.split("set j.`auth_identity` = 'APP'", 1)[1].split('"""', 1)[0]
	assert "coalesce(j.`auth_identity`, '') = ''" in triton_update
	assert "'USER'" not in triton_update, (
		"the Triton backfill's WHERE clause mentions USER. It must match only rows with an "
		"EMPTY identity — anything else can overrule a decision the writer already made."
	)
	assert "j.`status` in ('Pending', 'Failed')" in triton_update, (
		"the backfill must not touch In Progress: a worker has already read that row and built "
		"its client, so changing the identity underneath it makes the two disagree."
	)


# --------------------------------------------------------------------------------------
# The report says WHY a job is dead (v1.279.4)
# --------------------------------------------------------------------------------------
#
# Lives in this file rather than its own: `_render_queue` is the relay-queue section of the
# report, this is the relay-queue suite, and a new bench-free suite means a new CI step whose
# only job would be one assertion. `chat/health.py` imports nothing this suite's stub does not
# already provide.


def test_the_report_names_why_dead_jobs_died() -> None:
	"""A count with no reason reads as actionable and is not.

	Production reported `jobs dead: 3` with the note "the two sides are divergent" — true, and
	it sent us looking at auth and space membership. The actual cause was sitting in
	`last_error` on all three rows: a settled, self-explaining, one-release-old client-id
	prefix rejection. The number was right; the silence next to it was the defect.
	"""
	from erpnext_enhancements.chat import health

	lines: list[str] = []
	health._render_queue(
		lines,
		{
			"relay_ready": True,
			"relay_by_status": {"Dead": 3},
			"dead_reasons": [
				{
					"n": 3,
					"room": "e0c9csbl5k",
					"operation": "Message Create",
					"auth_identity": "USER",
					"reason": "ValueError: messageId rejected: must begin with 'client-'",
				}
			],
			"due_pending_count": 0,
			"oldest_pending": None,
			"oldest_pending_age": None,
			"oldest_due_pending": None,
			"oldest_due_pending_age": None,
			"expired_lease_count": 0,
			"expired_leases": [],
			"inbound_by_status": {},
			"oldest_inbound_age": None,
			"lease_column": True,
		},
	)
	text = "\n".join(lines)

	assert "must begin with 'client-'" in text, "the reason must reach the report"
	assert "3x Message Create" in text, "identical causes are grouped, so N jobs read as one finding"
	assert "as USER" in text, "which identity it was written as is half the diagnosis now"
	assert "e0c9csbl5k" in text


def test_a_dead_job_with_no_recorded_error_still_prints_a_row() -> None:
	"""Silence must be reported as silence, not as an absent section.

	A dead job whose `last_error` never got written is a *worse* finding than one with a
	reason, and the shape that hides it is a renderer that skips falsy rows.
	"""
	from erpnext_enhancements.chat import health

	lines: list[str] = []
	health._render_queue(
		lines,
		{
			"relay_ready": True,
			"relay_by_status": {"Dead": 1},
			"dead_reasons": [
				{"n": 1, "room": "r1", "operation": "Message Delete", "reason": "(none recorded)"}
			],
			"due_pending_count": 0,
			"oldest_pending": None,
			"oldest_pending_age": None,
			"oldest_due_pending": None,
			"oldest_due_pending_age": None,
			"expired_lease_count": 0,
			"expired_leases": [],
			"inbound_by_status": {},
			"oldest_inbound_age": None,
			"lease_column": True,
		},
	)
	text = "\n".join(lines)
	assert "(none recorded)" in text
	# No `auth_identity` key at all — a site that has not migrated must render, not crash.
	assert "as None" not in text and "as USER" not in text


def test_the_spa_prefix_backfill_only_touches_never_relayed_messages() -> None:
	"""``client_message_id`` is an identity, and rewriting one is only safe in one case.

	The guard that matters is ``gchat_message_name`` being empty — never relayed, so Google has
	never seen the id and no Chat resource points at it. Rewrite one that *has* relayed and both
	directions break at once: inbound echo suppression infers "our own echo" from a ``client-``
	id we issued (invariant I3), and a later edit or delete addressed by client alias misses.

	Source-level, because the behaviour is a SQL predicate against a real table. What can be
	checked without a bench is that the predicate is still there, and that is the half a
	refactor silently drops.
	"""
	root = pathlib.Path(__file__).resolve().parents[1]
	registered = (root / "patches.txt").read_text(encoding="utf-8")
	assert "erpnext_enhancements.patches.backfill_spa_client_message_id" in registered

	source = (root / "patches" / "backfill_spa_client_message_id.py").read_text(encoding="utf-8")
	select = source.split("select `name`, `client_message_id`", 1)[1].split('"""', 1)[0]
	assert "coalesce(`gchat_message_name`, '') = ''" in select, (
		"the backfill must only select messages that never relayed — rewriting the id of one "
		"that did breaks echo suppression and every later edit or delete of it."
	)

	# A save would re-enter the outbox and enqueue a SECOND relay job for a message that
	# already has one queued, which is how a repair becomes a duplicate.
	assert "frappe.db.set_value" in source
	assert ".save(" not in source and "get_doc(" not in source


def test_the_triton_restamp_is_keyed_on_origin_and_matches_the_writer() -> None:
	"""The re-run of a backfill that matched nothing and reported success.

	Two properties:

	* it keys on ``sync_origin = 'Triton'`` — the *same* rule
	  :func:`outbox.auth_identity_for_origin` applies to every job written since, so the patch
	  and the writer cannot disagree about what a row should have been;
	* it is therefore allowed to overwrite ``USER``, and must still never touch a row that is
	  not Triton-origin. That restriction is the whole safety argument, because the case the
	  original guard protected — a coworker's message re-stamped as the app — cannot occur
	  inside this predicate.
	"""
	root = pathlib.Path(__file__).resolve().parents[1]
	registered = (root / "patches.txt").read_text(encoding="utf-8")
	assert "erpnext_enhancements.patches.restamp_triton_relay_auth_identity" in registered

	source = (root / "patches" / "restamp_triton_relay_auth_identity.py").read_text(encoding="utf-8")
	update = source.split("set j.`auth_identity` = %(app)s", 1)[1].split('"""', 1)[0]
	assert "m.`sync_origin` = %(origin)s" in update, (
		"the restamp must be restricted to Triton-origin rows; without that it can re-attribute "
		"a coworker's own message to the app."
	)
	assert "j.`status` in ('Pending', 'Failed')" in update
	assert source.count('ORIGIN_TRITON = "Triton"') == 1

	# It must agree with the writer, not merely look similar to it: the patch hardcodes the
	# origin string, so this pins that string to the one the writer actually branches on.
	assert outbox.auth_identity_for_origin(outbox.ORIGIN_TRITON) == states.AUTH_IDENTITY_APP
	assert f'ORIGIN_TRITON = "{outbox.ORIGIN_TRITON}"' in source
