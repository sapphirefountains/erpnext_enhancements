# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free proof of the inbound half — the Pub/Sub puller and the ingest pipeline.

**Shape P: plain pytest functions.** It needs its own ``python -m pytest`` step in ``ci.yml``.
``python -m unittest`` cannot collect a file of plain functions — it collects nothing and
reports success — and this repo has already lost a suite that way for weeks (``CLAUDE.md``).

Why this suite installs a whole in-memory Frappe rather than mocking
---------------------------------------------------------------------
Almost everything worth asserting here is a claim about a **unique index**, and a mock cannot
disagree with you. ``unique(pubsub_message_id)`` is the entire duplicate defence for an
at-least-once transport; ``unique(gchat_message_name)`` is ADR §G.8 Rule 2; ``unique(room,
client_message_id)`` is the authoritative half of echo suppression; ``unique(message,
revision_no)`` is what stops a retried writer recording one edit twice. So :class:`_Store`
enforces all four the way MariaDB does — including the part that trips people up, that an
empty value on a single-column unique is stored as ``NULL`` and therefore never collides —
and raises the class Frappe would raise, which is **not** the one the ADR names
(``PHASE2_VERIFIED.md`` §1.1: a non-primary-key unique index raises
``frappe.UniqueValidationError``, not ``frappe.DuplicateEntryError``, and the two share no base
beyond ``Exception``).

The site timezone is deliberately **America/Denver**, not UTC. ADR §G.8 Rule 3 compares
Google's RFC-3339 UTC ``lastUpdateTime`` against Frappe's site-local ``edited_at``, and with a
UTC site every naive comparison is accidentally correct — the offset bug this app has already
shipped once (``now_datetime()`` is site-local; the Turnstile always-fail) would pass every
test. With a real offset, a naive comparison is wrong by six or seven hours in the direction
that always makes Google win.

Chaos coverage (Phase 2 §7 tier 3), by number
----------------------------------------------
1 duplicate ×5 from two workers · 2 updated before created · 3 deleted before created ·
6 redelivery after the ack deadline · 11 ``createTime`` ±10 minutes · 12 unknown/unmirrored
space · 13 Chat user with no ERPNext ``User`` · 16 simultaneous ERPNext and Chat edit.
Plus the §4.D race, driven through ``FakeChatAPI.race_on_create``: the Workspace Event is
published *before* ``messages.create`` returns, so inbound meets a message the relay has not
yet bound.

No bench, no network, no database. Nothing here contains real employee content.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import re
import sys
import types
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

# ---------------------------------------------------------------------------
# Bench guard
# ---------------------------------------------------------------------------


def _real_frappe_is_installed() -> bool:
	"""Is there an actual ``frappe`` package importable here, as opposed to a stub?

	``find_spec`` answers from the import system, so a bare ``types.ModuleType`` another suite
	left in ``sys.modules`` has no ``__spec__`` and reads as absent — which is the answer we
	want.
	"""
	try:
		spec = importlib.util.find_spec("frappe")
	except (ImportError, ValueError):
		return False
	return bool(spec and spec.origin)


if _real_frappe_is_installed():  # pragma: no cover - only true on a bench
	pytest.skip(
		"bench-free suite: a real frappe is installed, and stubbing over it would break the "
		"rest of the bench run",
		allow_module_level=True,
	)


SITE_TZ = ZoneInfo("America/Denver")
SITE_NAME = "chat-inbound-test.localhost"


# ---------------------------------------------------------------------------
# Exceptions — the pair the ADR gets wrong
# ---------------------------------------------------------------------------


class ValidationError(Exception):
	pass


class UniqueValidationError(ValidationError):
	"""What a **non-primary-key** unique index raises. ``PHASE2_VERIFIED.md`` §1.1."""


class DuplicateEntryError(Exception):
	"""What a **primary key** collision raises. Frappe derives it from its own ``NameError``."""


class DoesNotExistError(Exception):
	pass


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

#: ``doctype -> tuple of unique column groups``, mirroring ``patches/add_chat_indexes.py`` and
#: ``patches/add_chat_phase2_indexes.py``. A single-column group behaves like MariaDB with
#: Frappe's ``unique: 1`` coercion: an empty value is ``NULL`` and NULLs never collide.
UNIQUE_INDEXES: dict[str, tuple[tuple[str, ...], ...]] = {
	"Chat Message": (("gchat_message_name",), ("room", "seq"), ("room", "client_message_id")),
	"Chat Inbound Event": (("pubsub_message_id",),),
	"Chat Message Revision": (("message", "revision_no"),),
	"Chat Room": (("gchat_space_name",),),
	"Chat Room Member": (("room", "user"),),
}


@dataclass
class _Store:
	rows: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
	counter: int = 0

	def table(self, doctype: str) -> dict[str, dict[str, Any]]:
		return self.rows.setdefault(doctype, {})

	def next_name(self, doctype: str) -> str:
		self.counter += 1
		return f"{doctype.replace(' ', '')[:10]}{self.counter:06d}"

	def check_unique(self, doctype: str, row: dict[str, Any], *, exclude: str | None = None) -> None:
		for columns in UNIQUE_INDEXES.get(doctype, ()):
			values = tuple(row.get(c) for c in columns)
			if len(columns) == 1 and values[0] in (None, ""):
				# Frappe coerces "" to NULL on a `unique: 1` DocField and MariaDB allows
				# unlimited NULLs in a unique index. Every not-yet-relayed message stores an
				# empty `gchat_message_name`, so getting this wrong makes the SECOND message on
				# the site fail to insert — and only in production.
				continue
			for name, other in self.table(doctype).items():
				if name == exclude:
					continue
				if tuple(other.get(c) for c in columns) == values:
					raise UniqueValidationError(f"{doctype}: duplicate {columns} = {values}")


STORE = _Store()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class _Flags:
	"""``doc.flags`` is a ``frappe._dict``: attribute access **and** ``.get`` both work, and a
	missing key reads as ``None`` rather than raising. Both halves are used by the write path."""

	def __init__(self) -> None:
		self.__dict__["_values"] = {}

	def __getattr__(self, item: str) -> Any:
		if item == "get":
			return self.__dict__["_values"].get
		return self.__dict__["_values"].get(item)

	def __setattr__(self, item: str, value: Any) -> None:
		self.__dict__["_values"][item] = value


class Document:
	"""Enough of ``frappe.model.document.Document`` for the three controllers under test.

	``insert`` runs ``before_insert`` then ``validate`` then the unique checks, in that order,
	because that is the order that matters here: ``Chat Message Revision.before_insert``
	backfills ``room`` (which is ``reqd``) and ``ChatMessage.validate`` enforces "sender OR
	sender_email, never neither", and both would be untested if this stub wrote straight to the
	store.
	"""

	def __init__(self, doctype: str = "", name: str = "") -> None:
		self.doctype = doctype
		self.name = name
		self.flags = _Flags()

	def get(self, key: str, default: Any = None) -> Any:
		return getattr(self, key, default)

	def as_row(self) -> dict[str, Any]:
		return {
			k: v for k, v in self.__dict__.items() if k not in ("flags", "doctype") and not k.startswith("_")
		}

	def insert(self, ignore_permissions: bool = False) -> Document:
		for handler in _doc_events(self.doctype, "before_insert"):
			handler(self)
		before = getattr(self, "before_insert", None)
		if callable(before):
			before()

		# Frappe's ordering, which `outbox.derive_client_message_id` depends on: `set_new_name`
		# runs AFTER before_insert and BEFORE validate, so `doc.name` exists at validate time
		# and does not exist at before_insert time.
		if not self.name:
			self.name = STORE.next_name(self.doctype)

		validate = getattr(self, "validate", None)
		if callable(validate):
			validate()
		for handler in _doc_events(self.doctype, "validate"):
			handler(self)

		row = self.as_row()
		row.setdefault("creation", frappe.utils.now_datetime())
		row["modified"] = frappe.utils.now_datetime()
		if self.name in STORE.table(self.doctype):
			raise DuplicateEntryError(f"{self.doctype} {self.name} already exists")
		STORE.check_unique(self.doctype, row)
		STORE.table(self.doctype)[self.name] = row

		for handler in _doc_events(self.doctype, "after_insert"):
			handler(self)
		return self


def _doc_events(doctype: str, event: str) -> list[Any]:
	"""The ``hooks.py`` ``doc_events`` this suite emulates.

	Wired here rather than mocked away because the properties under test are **joint**: that
	``notify_new_message`` fires exactly once per genuinely new message is only true if inbound
	stays silent *and* ``after_insert`` fires, and that an inbound row never produces a relay
	job is only true if ``outbox.relay_disposition`` actually runs. Stubbing either side would
	turn both assertions into tautologies.
	"""
	if doctype != "Chat Message":
		return []
	from erpnext_enhancements.chat.sync import outbox

	return {
		"before_insert": [outbox.prepare_new_message],
		"validate": [outbox.derive_client_message_id],
		"after_insert": [outbox.finalise_new_message],
	}.get(event, [])


def _controller(doctype: str) -> type[Document]:
	"""The real controller class for a chat DocType, or the plain stub for anything else."""
	modules = {
		"Chat Message": "erpnext_enhancements.chat.doctype.chat_message.chat_message",
		"Chat Message Revision": (
			"erpnext_enhancements.chat.doctype.chat_message_revision.chat_message_revision"
		),
		"Chat Inbound Event": ("erpnext_enhancements.chat.doctype.chat_inbound_event.chat_inbound_event"),
	}
	path = modules.get(doctype)
	if not path:
		return Document
	module = importlib.import_module(path)
	return getattr(module, doctype.replace(" ", ""), Document)


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _matches(row: dict[str, Any], filters: Any) -> bool:
	if filters is None:
		return True
	if isinstance(filters, str):
		return row.get("name") == filters
	for key, want in dict(filters).items():
		have = row.get(key)
		if isinstance(want, tuple | list) and len(want) == 2:
			op, value = want
			op = str(op).lower()
			if op == "<" and not (have is not None and have < value):
				return False
			if op == ">" and not (have is not None and have > value):
				return False
			if op == "<=" and not (have is not None and have <= value):
				return False
			if op == ">=" and not (have is not None and have >= value):
				return False
			if op == "is":
				# Frappe's ``("is", "set")`` / ``("is", "not set")``. The inbound sweeper's
				# lost-job branch selects rows whose ``available_at`` is NULL, and a stub that
				# quietly ignored an operator it did not recognise would match every row —
				# making the two populations the branch exists to separate indistinguishable,
				# and the test green for the wrong reason.
				is_set = have not in (None, "")
				if str(value).lower() == "set" and not is_set:
					return False
				if str(value).lower() == "not set" and is_set:
					return False
			if op == "in" and have not in value:
				return False
			if op == "like":
				pattern = "^" + re.escape(str(value)).replace("%", ".*") + "$"
				if not re.match(pattern, str(have or "")):
					return False
			continue
		if have != want:
			return False
	return True


def _sorted(rows: list[dict[str, Any]], order_by: str | None) -> list[dict[str, Any]]:
	if not order_by:
		return rows
	field_name, _, direction = order_by.partition(" ")
	return sorted(
		rows,
		key=lambda r: (r.get(field_name) is None, r.get(field_name)),
		reverse=direction.strip().lower() == "desc",
	)


# ---------------------------------------------------------------------------
# frappe.db
# ---------------------------------------------------------------------------

#: The handful of statements the write path issues. Matched by shape rather than parsed: a
#: stub that pretended to be a SQL engine would quietly accept a statement it did not
#: understand and return an empty result, which is how a ``seq`` allocator "passes" its test
#: while allocating nothing.
_SQL_BUMP_SEQ = re.compile(r"set seq_high_water = coalesce\(seq_high_water, 0\) \+ 1", re.I)
_SQL_READ_SEQ = re.compile(r"select seq_high_water from", re.I)
_SQL_LOCK_ROOM = re.compile(r"select name from .*for update", re.I | re.S)
_SQL_MAX_SEQ = re.compile(r"select coalesce\(max\(seq\), 0\) as high", re.I)
_SQL_MAX_JOB_SEQ = re.compile(r"select coalesce\(max\(job_seq\), 0\) as high", re.I)
_SQL_REPAIR_SEQ = re.compile(r"set seq_high_water = %\(high\)s", re.I)
_SQL_READ_MARK = re.compile(r"set last_read_seq = %\(seq\)s", re.I)


class _DB:
	def __init__(self) -> None:
		self.commits = 0
		self.after_commit: list[Any] = []
		self.savepoints: list[str] = []

	def commit(self) -> None:
		self.commits += 1
		pending, self.after_commit = self.after_commit, []
		for job in pending:
			job()

	def rollback(self, save_point: str | None = None) -> None:
		return None

	def savepoint(self, name: str) -> None:
		self.savepoints.append(name)

	def release_savepoint(self, name: str) -> None:
		if name in self.savepoints:
			self.savepoints.remove(name)

	def get_value(self, doctype: str, filters: Any, fieldname: Any = "name", as_dict: bool = False) -> Any:
		rows = [r for r in STORE.table(doctype).values() if _matches(r, filters)]

		if isinstance(fieldname, str) and fieldname.lower().startswith("max("):
			column = fieldname[fieldname.index("(") + 1 : fieldname.index(")")].strip()
			values = [r.get(column) for r in rows if r.get(column) is not None]
			best = max(values) if values else None
			alias = fieldname.rsplit(" as ", 1)[-1].strip() if " as " in fieldname else column
			return {alias: best} if as_dict else best

		if not rows:
			return None
		row = rows[0]
		if isinstance(fieldname, list | tuple):
			picked = {f: row.get(f) for f in fieldname}
			return picked if as_dict else tuple(picked.values())
		return {fieldname: row.get(fieldname)} if as_dict else row.get(fieldname)

	def set_value(
		self,
		doctype: str,
		name: Any,
		field_or_map: Any,
		value: Any = None,
		update_modified: bool = True,
	) -> None:
		targets = [r for r in STORE.table(doctype).values() if _matches(r, name)]
		values = dict(field_or_map) if isinstance(field_or_map, dict) else {field_or_map: value}
		for row in targets:
			candidate = dict(row)
			candidate.update(values)
			STORE.check_unique(doctype, candidate, exclude=row.get("name"))
			row.update(values)
			if update_modified:
				row["modified"] = frappe.utils.now_datetime()

	def count(self, doctype: str, filters: Any = None) -> int:
		return len([r for r in STORE.table(doctype).values() if _matches(r, filters)])

	def exists(self, doctype: str, filters: Any = None) -> Any:
		row = self.get_value(doctype, filters, "name")
		return row

	def delete(self, doctype: str, filters: Any = None) -> None:
		table = STORE.table(doctype)
		for name in [n for n, r in table.items() if _matches(r, filters)]:
			table.pop(name, None)

	def sql(self, query: str, values: Any = None, as_dict: bool = False, **_: Any) -> Any:
		"""The statements the chat write path issues, and no others. See the note above."""
		params = dict(values) if isinstance(values, dict) else {}
		room = params.get("room", "")

		if _SQL_LOCK_ROOM.search(query):
			return [{"name": room}] if room in STORE.table("Chat Room") else []
		if _SQL_BUMP_SEQ.search(query):
			row = STORE.table("Chat Room").get(room)
			if row is not None:
				row["seq_high_water"] = int(row.get("seq_high_water") or 0) + 1
			return []
		if _SQL_READ_SEQ.search(query):
			row = STORE.table("Chat Room").get(room)
			return [{"seq_high_water": int((row or {}).get("seq_high_water") or 0)}] if row else []
		if _SQL_MAX_SEQ.search(query):
			seqs = [
				int(r.get("seq") or 0) for r in STORE.table("Chat Message").values() if r.get("room") == room
			]
			return [{"high": max(seqs) if seqs else 0}]
		if _SQL_MAX_JOB_SEQ.search(query):
			seqs = [
				int(r.get("job_seq") or 0)
				for r in STORE.table("Chat Relay Job").values()
				if r.get("room") == room
			]
			return [{"high": max(seqs) if seqs else 0}]
		if _SQL_REPAIR_SEQ.search(query):
			row = STORE.table("Chat Room").get(room)
			if row is not None and int(row.get("seq_high_water") or 0) < int(params.get("high") or 0):
				row["seq_high_water"] = int(params["high"])
			return []
		if _SQL_READ_MARK.search(query):
			for member in STORE.table("Chat Room Member").values():
				if member.get("room") == room and member.get("user") == params.get("user"):
					if int(member.get("last_read_seq") or 0) < int(params.get("seq") or 0):
						member["last_read_seq"] = int(params["seq"])
						member["last_read_at"] = params.get("now")
			return []
		raise AssertionError(f"the frappe stub does not implement this SQL: {query!r}")


# ---------------------------------------------------------------------------
# frappe
# ---------------------------------------------------------------------------


class _Cache:
	def __init__(self) -> None:
		self.values: dict[str, Any] = {}

	def set(self, key: str, value: Any, nx: bool = False, ex: int | None = None, **_: Any) -> bool:
		if nx and key in self.values:
			return False
		self.values[key] = value
		return True

	def get(self, key: str) -> Any:
		return self.values.get(key)

	def delete(self, key: str) -> None:
		self.values.pop(key, None)

	def incrby(self, key: str, step: int = 1) -> int:
		self.values[key] = int(self.values.get(key) or 0) + int(step)
		return self.values[key]

	def mget(self, keys: list[str]) -> list[Any]:
		return [self.values.get(k) for k in keys]


class _Logger:
	def __init__(self) -> None:
		self.lines: list[str] = []

	def _record(self, message: str) -> None:
		self.lines.append(str(message))

	debug = info = warning = error = _record


def _now_datetime() -> datetime:
	"""Frappe's ``now_datetime``: **site-local and naive**, which is the whole timezone trap."""
	return datetime.now(SITE_TZ).replace(tzinfo=None)


def _install_frappe_stub() -> types.ModuleType:
	frappe_mod = types.ModuleType("frappe")

	frappe_mod.ValidationError = ValidationError
	frappe_mod.UniqueValidationError = UniqueValidationError
	frappe_mod.DuplicateEntryError = DuplicateEntryError
	frappe_mod.DoesNotExistError = DoesNotExistError
	frappe_mod._ = lambda text, *a, **k: text

	def _throw(message: str, exc: type[Exception] = ValidationError, **_: Any) -> None:
		raise exc(message)

	frappe_mod.throw = _throw
	frappe_mod.db = _DB()
	frappe_mod.local = types.SimpleNamespace(site=SITE_NAME, message_log=[], lang="en")
	frappe_mod.flags = types.SimpleNamespace(in_test=True)

	logger = _Logger()
	frappe_mod.logger = lambda *a, **k: logger
	frappe_mod.log = logger

	cache = _Cache()
	frappe_mod.cache = lambda: cache

	frappe_mod.errors = []

	def _log_error(title: str = "", message: str = "", **_: Any) -> None:
		frappe_mod.errors.append({"title": title, "message": message})

	frappe_mod.log_error = _log_error
	frappe_mod.clear_messages = lambda: frappe_mod.local.message_log.clear()

	def _clear_last_message() -> None:
		if frappe_mod.local.message_log:
			frappe_mod.local.message_log.pop()

	frappe_mod.clear_last_message = _clear_last_message
	frappe_mod.msgprint = lambda *a, **k: frappe_mod.local.message_log.append(a[0] if a else "")

	frappe_mod.enqueued = []

	def _enqueue(
		method: str,
		queue: str = "default",
		timeout: int | None = None,
		event: Any = None,
		is_async: bool = True,
		job_name: str | None = None,
		now: bool = False,
		enqueue_after_commit: bool = False,
		*,
		at_front: bool = False,
		job_id: str | None = None,
		deduplicate: bool = False,
		on_success: Any = None,
		on_failure: Any = None,
		**kwargs: Any,
	) -> None:
		"""``frappe.enqueue`` **with its real parameter list**, and that is the point.

		Only ``**kwargs`` is forwarded to the job. Spelling the reserved names out — rather
		than popping the two this suite happens to pass — is what makes
		``test_the_processing_kwarg_is_not_one_frappe_enqueue_reserves`` able to fail: a job
		argument named ``event`` would bind to the parameter above and vanish, exactly as it
		would in production.
		"""
		record = {"method": method, "queue": queue, "kwargs": kwargs}
		if enqueue_after_commit:
			frappe_mod.db.after_commit.append(lambda: frappe_mod.enqueued.append(record))
		else:  # pragma: no cover - the guardrail lint forbids this branch in chat/
			frappe_mod.enqueued.append(record)

	frappe_mod.enqueue = _enqueue

	frappe_mod.published = []
	frappe_mod.publish_realtime = lambda **kwargs: frappe_mod.published.append(kwargs)

	def _new_doc(doctype: str) -> Document:
		return _controller(doctype)(doctype)

	def _get_doc(doctype: Any, name: str = "") -> Document:
		if isinstance(doctype, dict):
			# `frappe.get_doc({...})` builds an unsaved document from a field bag; the write
			# path uses this form for `Chat Relay Job`.
			fields = dict(doctype)
			doc = _controller(fields.pop("doctype"))(fields.get("doctype", ""))
			doc.doctype = dict(doctype)["doctype"]
			for key, value in fields.items():
				setattr(doc, key, value)
			return doc
		row = STORE.table(doctype).get(name)
		if row is None:
			raise DoesNotExistError(f"{doctype} {name} not found")
		doc = _controller(doctype)(doctype, name)
		for key, value in row.items():
			setattr(doc, key, value)
		return doc

	frappe_mod.new_doc = _new_doc
	frappe_mod.get_doc = _get_doc
	frappe_mod.get_cached_doc = (
		lambda doctype, *a: SETTINGS if doctype == "Chat Settings" else _get_doc(doctype, *a)
	)

	def _get_all(
		doctype: str,
		filters: Any = None,
		fields: Any = None,
		order_by: str | None = None,
		limit: int | None = None,
		pluck: str | None = None,
		**_: Any,
	) -> list[Any]:
		rows = [r for r in STORE.table(doctype).values() if _matches(r, filters)]
		rows = _sorted(rows, order_by)
		if limit:
			rows = rows[: int(limit)]
		if pluck:
			return [r.get(pluck) for r in rows]
		wanted = list(fields or ["name"])
		return [{f: r.get(f) for f in wanted} for r in rows]

	frappe_mod.get_all = _get_all
	frappe_mod.get_list = _get_all
	frappe_mod.parse_json = lambda value: json.loads(value) if isinstance(value, str) else value
	frappe_mod.as_json = lambda value, **k: json.dumps(value, default=str)

	# --- frappe.utils ---
	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = _now_datetime
	utils.nowdate = lambda: _now_datetime().date().isoformat()
	utils.cint = lambda v, default=0: int(v) if str(v or "").strip().lstrip("-").isdigit() else default
	utils.get_system_timezone = lambda: "America/Denver"
	utils.get_url = lambda *a, **k: "https://erp.example.test"
	utils.escape_html = lambda text: (
		str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
	)
	utils.strip_html_tags = lambda text: re.sub(r"<[^>]+>", "", str(text or ""))

	def _get_datetime(value: Any = None) -> datetime | None:
		if value in (None, ""):
			return _now_datetime()
		if isinstance(value, datetime):
			return value
		return datetime.fromisoformat(str(value))

	utils.get_datetime = _get_datetime

	def _add_to_date(value: Any = None, **deltas: Any) -> datetime:
		base = _get_datetime(value) or _now_datetime()
		return base + timedelta(
			days=deltas.get("days", 0),
			hours=deltas.get("hours", 0),
			minutes=deltas.get("minutes", 0),
			seconds=deltas.get("seconds", 0),
		)

	utils.add_to_date = _add_to_date
	utils.add_days = lambda value, days: _add_to_date(value, days=days)
	frappe_mod.utils = utils

	# --- frappe.model.document ---
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = Document
	model.document = document

	# --- frappe.database.database.savepoint ---
	database_pkg = types.ModuleType("frappe.database")
	database_mod = types.ModuleType("frappe.database.database")

	@contextmanager
	def _savepoint(catch: Any = Exception):
		"""Frappe's own shape: swallow ``catch``, re-raise anything else."""
		try:
			yield
		except catch:
			frappe_mod.db.rollback(save_point="sp")

	database_mod.savepoint = _savepoint
	database_pkg.database = database_mod

	# --- frappe.realtime ---
	realtime = types.ModuleType("frappe.realtime")
	realtime.get_doc_room = lambda doctype, docname: f"doc:{doctype}/{docname}"
	realtime.get_user_room = lambda user: f"user:{user}"
	frappe_mod.realtime = realtime

	frappe_mod.model = model

	sys.modules["frappe"] = frappe_mod
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	sys.modules["frappe.database"] = database_pkg
	sys.modules["frappe.database.database"] = database_mod
	sys.modules["frappe.realtime"] = realtime
	return frappe_mod


class _Settings:
	"""``Chat Settings`` with the Phase 2 defaults from the shipped DocType JSON."""

	def __init__(self) -> None:
		self.enabled = 1
		self.dry_run_mode = 0
		self.relay_inbound_enabled = 1
		self.relay_outbound_enabled = 1
		self.google_sync_enabled = 1
		self.pause_inbound = 0
		self.echo_defer_budget = 3
		self.echo_fallback_enabled = 0
		self.echo_fallback_window_seconds = 120
		self.clock_skew_alert_ms = 300_000
		self.max_reverts_per_hour = 5
		self.http_timeout_seconds = 30
		self.sweeper_batch_size = 200
		self.pubsub_events_subscription = "projects/erpnext-465317/subscriptions/chat-events-sub"
		self.pubsub_interaction_subscription = ""
		self.allowed_users: list[Any] = []


SETTINGS = _Settings()
frappe = _install_frappe_stub()

# Imported only after the stub is in place: every module below does `import frappe` at module
# scope, so the order here is load-bearing.
from erpnext_enhancements.chat import seams
from erpnext_enhancements.chat.gchat import ids
from erpnext_enhancements.chat.gchat.client import GoogleChatClient
from erpnext_enhancements.chat.sync import inbound, pubsub
from erpnext_enhancements.chat.sync.decisions import InboundVerdict
from erpnext_enhancements.chat.testing import fixtures
from erpnext_enhancements.chat.testing.fake_chat import (
	FakeChatAPI,
	FakeChatSettings,
	_user_id_for,
)

SPACE = "spaces/AAAA1111"
ROOM = "ROOM-0001"
ALICE = "alice@sapphirefountains.com"
BOB = "bob@sapphirefountains.com"
STRANGER = "guest@partner.example"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
	STORE.rows.clear()
	STORE.counter = 0
	frappe.db.commits = 0
	frappe.db.after_commit.clear()
	frappe.enqueued.clear()
	frappe.published.clear()
	frappe.errors.clear()
	frappe.local.message_log.clear()
	frappe.cache().values.clear()
	seams.reset_counters()
	SETTINGS.__init__()  # type: ignore[misc]
	yield


def make_room(*, space: str = SPACE, members: tuple[str, ...] = (ALICE, BOB)) -> str:
	STORE.table("Chat Room")[ROOM] = {
		"name": ROOM,
		"room_type": "Group",
		"gchat_space_name": space,
		"seq_high_water": 0,
		"last_message_at": None,
		"last_event_at": None,
		"reverts_this_hour": 0,
		"revert_window_start": None,
	}
	for index, email in enumerate(members):
		STORE.table("Chat Room Member")[f"MEMBER-{index}"] = {
			"name": f"MEMBER-{index}",
			"room": ROOM,
			"user": email,
			"is_active": 1,
			"gchat_membership_name": f"{space}/members/{_user_id_for(email)}",
		}
	return ROOM


def make_message(
	*,
	text: str = "hello",
	sender: str = ALICE,
	client_message_id: str | None = None,
	gchat_message_name: str = "",
	sync_origin: str = "ERPNext",
	sync_state: str = "Pending",
	edited_at: Any = None,
	seq: int | None = None,
) -> str:
	room = STORE.table("Chat Room")[ROOM]
	room["seq_high_water"] = int(room.get("seq_high_water") or 0) + 1
	name = STORE.next_name("Chat Message")
	STORE.table("Chat Message")[name] = {
		"name": name,
		"room": ROOM,
		"seq": seq if seq is not None else room["seq_high_water"],
		"sender": sender,
		"sender_email": sender,
		"sender_kind": "Human",
		"message_type": "Text",
		"text": text,
		"text_plain": text,
		"client_message_id": client_message_id or ids.client_message_id(name, site=SITE_NAME),
		"gchat_message_name": gchat_message_name,
		"sync_origin": sync_origin,
		"sync_state": sync_state,
		"is_edited": 1 if edited_at else 0,
		"edited_at": edited_at,
		"is_deleted": 0,
		"creation": frappe.utils.now_datetime(),
		"modified": frappe.utils.now_datetime(),
	}
	return name


def compose_message(*, text: str = "composed in erpnext", sender: str = ALICE) -> str:
	"""Send a message the way the composer does — **through the real write path**.

	Distinct from :func:`make_message`, which seeds a row directly. Arrangement is not
	behaviour: most tests here want a row to exist and should not pay for a relay job and a
	notification to get one. The §4.D race is the exception, because the whole question there
	is how many relay jobs and how many notifications the *pair* of paths produces.
	"""
	from erpnext_enhancements.chat.sync import outbox

	doc = frappe.new_doc("Chat Message")
	doc.room = ROOM
	doc.sender = sender
	doc.sender_email = sender
	doc.sender_kind = "Human"
	doc.message_type = "Text"
	doc.text = text
	outbox.insert_message(doc)
	return str(doc.name)


def google_client(fake: FakeChatAPI, *, subject: str = ALICE) -> GoogleChatClient:
	return GoogleChatClient(
		subject=subject,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=fake.token_provider,
		transport=fake,
	)


class _StubClient:
	"""``client.get_message`` only, answering from a dict of prepared resources."""

	def __init__(self, resources: dict[str, dict[str, Any]]) -> None:
		self.resources = resources
		self.gets: list[str] = []

	def get_message(self, name: str) -> dict[str, Any]:
		self.gets.append(name)
		if name not in self.resources:
			raise KeyError(name)
		return copy.deepcopy(self.resources[name])


def rfc3339(moment: datetime) -> str:
	"""An aware datetime rendered the way Google renders one: UTC, ``Z``-suffixed."""
	return moment.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def message_resource(
	name: str,
	*,
	text: str = "from chat",
	sender: str = BOB,
	created: datetime | None = None,
	updated: datetime | None = None,
	client_id: str = "",
) -> dict[str, Any]:
	created = created or datetime.now(ZoneInfo("UTC"))
	resource: dict[str, Any] = {
		"name": name,
		"sender": {"name": f"users/{_user_id_for(sender)}", "displayName": sender.split("@")[0]},
		"createTime": rfc3339(created),
		"space": {"name": SPACE},
		"thread": {"name": f"{SPACE}/threads/T1"},
		"text": text,
		"argumentText": text,
	}
	if updated is not None:
		resource["lastUpdateTime"] = rfc3339(updated)
	if client_id:
		resource["clientAssignedMessageId"] = client_id
	return resource


_DELIVERY_SEQ = [0]


def next_pubsub_id() -> str:
	"""A fresh Pub/Sub ``messageId`` per delivery.

	The fixtures carry a fixed default, which is right for a parser test and wrong here: two
	*different* events sharing one ``messageId`` would collide on ``unique(pubsub_message_id)``
	and be silently read as a redelivery, quietly hiding whatever the test meant to exercise.
	"""
	_DELIVERY_SEQ[0] += 1
	return f"90000000000{_DELIVERY_SEQ[0]:05d}"


def created_event(resource_name: str, **kwargs: Any) -> dict[str, Any]:
	kwargs.setdefault("pubsub_message_id", next_pubsub_id())
	return fixtures.message_created_event(space=SPACE, message=resource_name, **kwargs)


def messages() -> list[dict[str, Any]]:
	return list(STORE.table("Chat Message").values())


def revisions() -> list[dict[str, Any]]:
	return list(STORE.table("Chat Message Revision").values())


# ---------------------------------------------------------------------------
# The puller — ordering, dedupe, locking
# ---------------------------------------------------------------------------


class _FakePubSub:
	"""A Pub/Sub REST endpoint: ``:pull`` hands out batches, ``:acknowledge`` records ack ids."""

	def __init__(self, batches: list[list[dict[str, Any]]]) -> None:
		self.batches = batches
		self.acked: list[str] = []
		self.pulls = 0
		self.commits_at_first_ack: int | None = None
		self.rows_at_first_ack: int | None = None

	def request(self, method: str, url: str, json: Any = None, headers: Any = None, **_: Any) -> Any:
		assert method == "POST"
		assert str(headers.get("Authorization", "")).startswith("Bearer ")
		if url.endswith(":pull"):
			self.pulls += 1
			batch = self.batches.pop(0) if self.batches else []
			return _Resp(200, {"receivedMessages": batch})
		if url.endswith(":acknowledge"):
			if self.commits_at_first_ack is None:
				self.commits_at_first_ack = frappe.db.commits
				self.rows_at_first_ack = len(STORE.table("Chat Inbound Event"))
			self.acked.extend(json["ackIds"])
			return _Resp(200, {})
		raise AssertionError(f"unexpected Pub/Sub URL {url}")


class _Resp:
	def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
		self.status_code = status_code
		self._payload = payload
		self.text = ""

	def json(self) -> dict[str, Any]:
		return self._payload


def _delivery(resource_name: str, *, message_id: str, ack_id: str) -> dict[str, Any]:
	envelope = created_event(resource_name, pubsub_message_id=message_id)
	return {"ackId": ack_id, "message": envelope["message"]}


def test_the_row_is_committed_before_the_delivery_is_acked() -> None:
	"""Ack-before-commit loses events on a crash. The order is the whole correctness argument."""
	make_room()
	transport = _FakePubSub([[_delivery(f"{SPACE}/messages/m1", message_id="p1", ack_id="a1")]])

	result = pubsub.run_pull_cycle(
		subscription="projects/p/subscriptions/s", transport=transport, token="t", budget_seconds=1
	)

	assert result["ingested"] == 1
	assert transport.acked == ["a1"]
	assert transport.rows_at_first_ack == 1, "the row must exist before the ack"
	assert transport.commits_at_first_ack >= 1, "the row must be COMMITTED before the ack"


def test_processing_is_enqueued_only_after_the_ack_and_only_for_new_rows() -> None:
	make_room()
	transport = _FakePubSub([[_delivery(f"{SPACE}/messages/m1", message_id="p1", ack_id="a1")]])
	pubsub.run_pull_cycle(
		subscription="projects/p/subscriptions/s", transport=transport, token="t", budget_seconds=1
	)

	assert len(frappe.enqueued) == 1
	assert frappe.enqueued[0]["method"].endswith("inbound.process_inbound_event")


#: ``frappe.enqueue``'s own parameters. A job kwarg sharing one of these names is swallowed by
#: ``enqueue`` and never reaches the job, which calls the target with a missing argument and
#: dies with a ``TypeError`` in a worker nobody is watching.
ENQUEUE_RESERVED_KWARGS = frozenset(
	{
		"method",
		"queue",
		"timeout",
		"event",
		"is_async",
		"job_name",
		"now",
		"enqueue_after_commit",
		"at_front",
		"job_id",
		"deduplicate",
		"on_success",
		"on_failure",
	}
)


def test_the_processing_kwarg_is_not_one_frappe_enqueue_reserves() -> None:
	"""The job argument must survive the trip through ``frappe.enqueue``.

	This app has been bitten by the same class of bug from the other direction — Frappe pops a
	POSTed key named ``sid`` and answers "session expired" — and the failure mode here is
	identical in shape and worse in consequence: every inbound event silently fails to process,
	and the only thing that ever delivers a message is the ten-minute sweeper.
	"""
	make_room()
	inbound.enqueue_inbound_event("EVT-1")
	frappe.db.commit()

	kwargs = frappe.enqueued[0]["kwargs"]
	assert kwargs.get("inbound_event") == "EVT-1"
	assert not (set(kwargs) & ENQUEUE_RESERVED_KWARGS), (
		f"these job kwargs collide with frappe.enqueue's own parameters and would never reach "
		f"the job: {sorted(set(kwargs) & ENQUEUE_RESERVED_KWARGS)}"
	)


def test_a_second_pull_of_the_same_delivery_ingests_nothing_and_still_acks() -> None:
	"""Chaos 6 — redelivery after the ack deadline expired while we were still working."""
	make_room()
	first = _delivery(f"{SPACE}/messages/m1", message_id="p1", ack_id="a1")
	# Pub/Sub redelivers the same message with the SAME messageId and a NEW ackId.
	again = _delivery(f"{SPACE}/messages/m1", message_id="p1", ack_id="a2")
	transport = _FakePubSub([[first], [again]])

	pubsub.run_pull_cycle(
		subscription="projects/p/subscriptions/s", transport=transport, token="t", budget_seconds=1
	)
	pubsub.run_pull_cycle(
		subscription="projects/p/subscriptions/s", transport=transport, token="t", budget_seconds=1
	)

	assert len(STORE.table("Chat Inbound Event")) == 1
	assert transport.acked == ["a1", "a2"], "a redelivery is still acked; it is a success"
	assert len(frappe.enqueued) == 1, "the duplicate must not queue a second processing job"


def test_five_redeliveries_from_two_workers_produce_one_row() -> None:
	"""Chaos 1. Dedupe is ``unique(pubsub_message_id)``, never a check."""
	make_room()
	deliveries = [_delivery(f"{SPACE}/messages/m1", message_id="p1", ack_id=f"a{i}") for i in range(5)]

	created_flags = []
	for delivery in deliveries:
		_name, created = pubsub.ingest_delivery(delivery)
		created_flags.append(created)

	assert created_flags == [True, False, False, False, False]
	assert len(STORE.table("Chat Inbound Event")) == 1
	assert frappe.local.message_log == [], "an expected duplicate must not leak a msgprint"


def test_the_puller_refuses_when_both_subscription_fields_hold_one_value() -> None:
	"""The live 2026-08-09 misconfiguration: two consumers on one subscription lose messages."""
	SETTINGS.pubsub_interaction_subscription = SETTINGS.pubsub_events_subscription
	with pytest.raises(pubsub.PubSubError):
		pubsub.events_subscription()
	assert pubsub.pull_inbound_events()["skipped"]


def test_only_one_tick_pulls_at_a_time() -> None:
	assert pubsub._acquire_lock() is True
	assert pubsub._acquire_lock() is False
	pubsub._release_lock()
	assert pubsub._acquire_lock() is True


def test_a_delivery_with_no_message_id_is_refused_rather_than_stored_undeduped() -> None:
	envelope = created_event(f"{SPACE}/messages/m1", pubsub_message_id="p1")
	envelope["message"].pop("messageId")
	with pytest.raises(pubsub.PubSubError):
		pubsub.ingest_delivery({"ackId": "a1", "message": envelope["message"]})


def test_the_payload_is_stored_verbatim() -> None:
	make_room()
	delivery = _delivery(f"{SPACE}/messages/m1", message_id="p1", ack_id="a1")
	name, _created = pubsub.ingest_delivery(copy.deepcopy(delivery))
	stored = json.loads(STORE.table("Chat Inbound Event")[name]["payload"])
	assert stored == delivery, "payload must be the delivery untouched — it is the fixture source"


# ---------------------------------------------------------------------------
# Ingest — the classifier's verdicts, acted on
# ---------------------------------------------------------------------------


def process(
	resource_name: str, *, event: str = "created", client: Any = None, space: str = SPACE, **kwargs: Any
) -> str:
	"""Ingest one event the way the puller would, then process it. Returns the verdict string."""
	builder = {
		"created": fixtures.message_created_event,
		"updated": fixtures.message_updated_event,
		"deleted": fixtures.message_deleted_event,
	}[event]
	kwargs.setdefault("pubsub_message_id", next_pubsub_id())
	envelope = builder(space=space, message=resource_name, **kwargs)
	name, created = pubsub.ingest_delivery({"ackId": "a", "message": envelope["message"]})
	assert created
	return inbound.process_inbound_event(name, client=client)


def test_a_genuinely_new_message_is_stored_and_notifies_exactly_once() -> None:
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource, text="morning all")})

	process(resource, client=client)

	rows = messages()
	assert len(rows) == 1
	row = rows[0]
	assert row["text"] == "morning all"
	assert row["sync_origin"] == "Google Chat"
	assert row["sync_state"] == "Inbound"
	assert row["gchat_message_name"] == resource
	assert row["seq"] == 1
	assert not row["client_message_id"].startswith(
		"client-"
	), "an inbound row must not wear an id shaped like one ERPNext minted"
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 1
	assert [p["event"] for p in frappe.published] == ["chat_message_created"]
	assert [r["change_type"] for r in revisions()] == ["Create"]


def test_the_same_created_event_twice_stores_one_message_and_notifies_once() -> None:
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource)})

	first_row, _ = pubsub.ingest_delivery(_delivery(resource, message_id="p1", ack_id="a1"))
	inbound.process_inbound_event(first_row, client=client)
	# Same row, re-driven by the sweeper. Same event, delivered again with a new Pub/Sub id.
	inbound.process_inbound_event(first_row, client=client)
	second_row, _ = pubsub.ingest_delivery(_delivery(resource, message_id="p2", ack_id="a2"))
	verdicts = inbound.process_inbound_event(second_row, client=client)

	assert len(messages()) == 1
	assert verdicts == InboundVerdict.DUPLICATE.value
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 1


def test_an_event_for_an_unmirrored_space_writes_nothing() -> None:
	"""Chaos 12. A ``spaces/-`` subscription sees every space the coworker is in."""
	make_room(space="spaces/MIRRORED")
	resource = "spaces/UNKNOWN9/messages/m1"
	verdict = process(resource, space="spaces/UNKNOWN9", client=_StubClient({}))
	row = next(iter(STORE.table("Chat Inbound Event")))

	assert verdict == InboundVerdict.IGNORE.value
	assert messages() == []
	assert STORE.table("Chat Inbound Event")[row]["status"] == "Ignored"
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 0


def test_a_chat_user_with_no_erpnext_user_is_stored_not_dropped() -> None:
	"""Chaos 13. ERPNext is the record of what was said."""
	make_room(members=(ALICE,))
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource, sender=STRANGER, text="from outside")})

	process(resource, client=client)

	row = messages()[0]
	assert row["sender"] is None
	assert row["sender_email"].endswith("@unresolved.invalid")
	assert _user_id_for(STRANGER) in row["sender_email"], "the placeholder must carry the Google id"
	assert row["text"] == "from outside"


def test_a_known_member_is_resolved_to_their_erpnext_user() -> None:
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource, sender=BOB)})

	process(resource, client=client)

	assert messages()[0]["sender"] == BOB


# ---------------------------------------------------------------------------
# Echo suppression
# ---------------------------------------------------------------------------


def test_our_own_message_coming_back_binds_and_does_not_insert() -> None:
	make_room()
	local = make_message(text="sent from erpnext")
	client_id = STORE.table("Chat Message")[local]["client_message_id"]
	resource = f"{SPACE}/messages/m1"
	client = _StubClient(
		{resource: message_resource(resource, text="sent from erpnext", client_id=client_id)}
	)

	verdict = process(resource, client=client)

	assert verdict == InboundVerdict.ECHO.value
	assert len(messages()) == 1
	assert STORE.table("Chat Message")[local]["gchat_message_name"] == resource
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 0
	assert seams.counters()[seams.COUNTER_ECHOES_SUPPRESSED] == 1
	assert frappe.published == [], "an echo informs nobody of anything"


def test_binding_an_echo_does_not_bump_modified() -> None:
	"""The D6 digest watermark is ``(max(seq), count(*), max(modified))``."""
	make_room()
	local = make_message()
	before = STORE.table("Chat Message")[local]["modified"]
	client_id = STORE.table("Chat Message")[local]["client_message_id"]
	resource = f"{SPACE}/messages/m1"
	client = _StubClient(
		{
			resource: message_resource(
				resource, text=STORE.table("Chat Message")[local]["text"], client_id=client_id
			)
		}
	)

	process(resource, client=client)

	assert STORE.table("Chat Message")[local]["modified"] == before


def test_a_client_id_with_no_local_row_alarms_and_binds_nothing() -> None:
	make_room()
	resource = f"{SPACE}/messages/m1"
	orphan = ids.client_message_id("GHOST", site=SITE_NAME)
	client = _StubClient({resource: message_resource(resource, client_id=orphan)})

	verdict = process(resource, client=client)

	assert verdict == InboundVerdict.ECHO_ORPHAN.value
	assert messages() == []
	assert seams.counters()[seams.COUNTER_ECHO_ORPHANS] == 1
	assert any("ECHO_ORPHAN" in e["title"] for e in frappe.errors)


def test_the_44d_race_event_before_response() -> None:
	"""§4.D. The Workspace Event is published **before** ``messages.create`` returns.

	At the moment inbound runs, the relay has not written ``gchat_message_name`` yet, so
	structural dedupe cannot help and echo suppression has to survive on the ``client-`` id
	alone. If it does not, the mirror duplicates every message it sends whenever Google is
	faster than the response — which is most of the time.
	"""
	make_room()
	fake = FakeChatAPI()
	space = fake.seed_space(display_name="Race", members=[ALICE, BOB], creator=ALICE)
	STORE.table("Chat Room")[ROOM]["gchat_space_name"] = space
	for row in STORE.table("Chat Room Member").values():
		row["gchat_membership_name"] = f"{space}/members/{_user_id_for(row['user'])}"

	# Composed through the real write path, so the relay job and the notification below are
	# the ones production would produce rather than ones this test arranged.
	local = compose_message(text="race me")
	client_id = STORE.table("Chat Message")[local]["client_message_id"]
	assert len(STORE.table("Chat Relay Job")) == 1, "the compose path queues exactly one create"
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 1

	client = google_client(fake)
	inbound_runs: list[str] = []

	def run_inbound_now(api: FakeChatAPI) -> None:
		# The Pub/Sub consumer, executed inside the create's response window.
		for envelope in api.drain_events():
			row, created = pubsub.ingest_delivery({"ackId": "a", "message": envelope["message"]})
			if created:
				inbound_runs.append(inbound.process_inbound_event(row, client=client))

	fake.race_on_create(space, during=run_inbound_now)

	response = client.create_message(space, client_id, "req-1", "race me")
	resource_name = response["name"]

	# The relay's write-back, exactly as the outbound worker would do it after the response.
	frappe.db.set_value("Chat Message", local, "gchat_message_name", resource_name, update_modified=False)

	assert inbound_runs == [InboundVerdict.ECHO.value]
	assert len(messages()) == 1, "the race must not produce a second row"
	assert STORE.table("Chat Message")[local]["gchat_message_name"] == resource_name
	assert len(fake.messages_in(space)) == 1, "exactly one Chat message"
	assert len(STORE.table("Chat Relay Job")) == 1, "an echo must not queue an extra relay job"
	assert (
		seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 1
	), "the compose path notified once; inbound must not notify again"
	assert seams.counters()[seams.COUNTER_ECHOES_SUPPRESSED] == 1


# ---------------------------------------------------------------------------
# Ordering — Rule 1, and §4.G skew
# ---------------------------------------------------------------------------


def test_updated_before_created_is_applied_as_a_create() -> None:
	"""Chaos 2 / ADR §G.8 Rule 1. Dropping it would lose the message, not merely the edit."""
	make_room()
	resource = f"{SPACE}/messages/m1"
	now = datetime.now(ZoneInfo("UTC"))
	client = _StubClient({resource: message_resource(resource, text="edited body", created=now, updated=now)})

	process(resource, event="updated", client=client)

	rows = messages()
	assert len(rows) == 1
	assert rows[0]["text"] == "edited body"
	assert rows[0]["gchat_message_name"] == resource
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 1

	# The create then arrives late and must collapse onto the row that already exists.
	verdict = process(resource, event="created", client=client)
	assert verdict == InboundVerdict.DUPLICATE.value
	assert len(messages()) == 1


def test_deleted_before_created_is_recorded_ignored_and_creates_no_tombstone() -> None:
	"""Chaos 3 / Rule 1: a ``deleted`` for a message we cannot name is recorded ``Ignored``."""
	make_room()
	resource = f"{SPACE}/messages/m1"

	verdict = process(resource, event="deleted", client=_StubClient({}))

	assert verdict == InboundVerdict.IGNORE.value
	assert messages() == [], "a delete must never conjure a row to bury"
	row = next(iter(STORE.table("Chat Inbound Event").values()))
	assert row["status"] == "Ignored"


def _tombstone_resource(
	resource: str, *, deletion_type: str = "CREATOR", sender: str = BOB
) -> dict[str, Any]:
	"""What ``messages.get`` hands back for a message that has been deleted.

	Metadata and **no ``text`` key at all** — not an empty string. That is the shape
	``FakeChatAPI._message_resource`` produces and the shape the whole tombstone design rests
	on: after a delete, the body exists nowhere on Google's side.
	"""
	deleted_at = datetime.now(ZoneInfo("UTC"))
	return {
		"name": resource,
		"sender": {"name": f"users/{_user_id_for(sender)}"},
		"space": {"name": SPACE},
		"thread": {"name": f"{SPACE}/threads/T1"},
		"createTime": rfc3339(deleted_at - timedelta(minutes=5)),
		"deleteTime": rfc3339(deleted_at),
		"deletionMetadata": {"deletionType": deletion_type},
	}


def test_deleted_before_created_converges_when_the_create_finally_lands() -> None:
	"""Chaos 3, the half the soak found and no review did.

	The delete is ``Ignored`` (above) and nothing ever revisits it. Until this converged, the
	create that followed produced a row that was **alive in ERPNext and dead in Chat,
	permanently, with nothing in any log** — the silent divergence this phase exists to
	prevent. The create now consumes Google's own tombstone and lands the row already dead.
	"""
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: _tombstone_resource(resource)})

	assert process(resource, event="deleted", client=client) == InboundVerdict.IGNORE.value
	assert messages() == []

	process(resource, event="created", client=client)

	rows = messages()
	assert len(rows) == 1, "exactly one row: the delete conjured none and the create made one"
	row = rows[0]
	assert row["is_deleted"] == 1, (
		"ERPNext and Chat now agree. Before this, the row stayed alive here and deleted there "
		"and no counter, log line or alarm recorded the divergence."
	)
	assert row["deleted_at"] is not None
	assert row["deletion_source"] == "Google Chat"
	assert row["gchat_message_name"] == resource
	assert row["sync_origin"] == "Google Chat"


def test_a_row_born_tombstoned_says_so_instead_of_looking_blank() -> None:
	"""The body is unrecoverable, which is a different fact from the body being empty.

	Google's tombstone is metadata only, so a message deleted before we ingested it has no text
	on either side, ever. Storing ``""`` would be indistinguishable from a blank message and
	from a soft delete that wrongly cleared the row — and telling those apart is the entire job
	of the one reader this row will ever have.
	"""
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: _tombstone_resource(resource)})

	process(resource, event="deleted", client=client)
	process(resource, event="created", client=client)

	row = messages()[0]
	assert row["text"] == inbound.TOMBSTONE_BODY_NOTICE
	assert row["text"].startswith("[") and "unrecoverable" in row["text"], (
		"the notice has to be unmistakably not something a coworker typed"
	)


def test_a_row_born_tombstoned_notifies_nobody_and_announces_nothing() -> None:
	"""Notifying somebody about a message that is already deleted is worse than staying quiet.

	A ``chat_message_created`` publish would be just as wrong: it pushes the SPA to render a row
	that every read path filters out. Both suppressions live in ``outbox.finalise_new_message``
	because ``after_insert`` is the single place either call is made — this asserts the inbound
	path actually reaches that state rather than inserting a live row and deleting it after.
	"""
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: _tombstone_resource(resource)})

	process(resource, event="deleted", client=client)
	process(resource, event="created", client=client)

	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 0
	assert [p["event"] for p in frappe.published] == []
	assert seams.counters()[seams.COUNTER_EVENTS_INGESTED] == 1, (
		"the message was still ingested — ERPNext is the record that it existed"
	)


def test_a_row_born_tombstoned_carries_one_revision_and_not_an_invented_transition() -> None:
	"""``Create``, noting the state it arrived in. ERPNext performed no delete to record."""
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: _tombstone_resource(resource, deletion_type="ADMIN")})

	process(resource, event="deleted", client=client)
	process(resource, event="created", client=client)

	assert [r["change_type"] for r in revisions()] == ["Create"]
	note = revisions()[0]["note"]
	assert "already deleted in Chat" in note and "deletionType=ADMIN" in note
	assert revisions()[0]["text_after"] == inbound.TOMBSTONE_BODY_NOTICE


def test_convergence_does_not_depend_on_the_defer_budget() -> None:
	"""**Why option (b).** The create can arrive long after any defer window has closed.

	A ``deleted`` recovered by the §4.J reconciliation sweep can precede its ``created`` by days,
	so a fix that settled the delete ``Deferred`` and retried it inside ``echo_defer_budget``
	would converge only for the fast case. With deferral switched off entirely — the harshest
	setting there is — the row still lands tombstoned, because the fact lives on Google's
	resource rather than in a timer.
	"""
	make_room()
	SETTINGS.echo_defer_budget = 0
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: _tombstone_resource(resource)})

	process(resource, event="deleted", client=client)
	_in_flight_relay_job()
	process(resource, event="created", client=client)

	assert messages()[0]["is_deleted"] == 1


def test_a_live_resource_still_lands_alive() -> None:
	"""The tombstone probe must not fire on an ordinary message — the anti-vacuity half."""
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource, text="very much alive")})

	process(resource, event="created", client=client)

	row = messages()[0]
	# The column is not written at all on the live path, which is the strongest form of "the
	# probe did not fire": a `0` would be consistent with a tombstone branch that ran and
	# decided not to set anything.
	assert not row.get("is_deleted")
	assert row["text"] == "very much alive"
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 1


@pytest.mark.parametrize(
	("resource", "expected"),
	[
		({"name": "m", "text": "alive"}, None),
		({"name": "m", "deleteTime": "2026-08-09T00:00:00Z"}, "Google Chat"),
		({"name": "m", "deletionMetadata": {"deletionType": "ADMIN"}}, "Admin"),
		(
			{
				"name": "m",
				"deleteTime": "2026-08-09T00:00:00Z",
				"deletionMetadata": {"deletionType": "CREATOR_VIA_APP"},
			},
			"ERPNext",
		),
	],
)
def test_the_tombstone_probe_reads_either_key(resource: dict[str, Any], expected: str | None) -> None:
	"""Either key alone is enough: one is the timestamp, the other the attribution."""
	found = inbound.resource_tombstone(resource)
	if expected is None:
		assert found is None
	else:
		assert found is not None and found[1] == expected


def test_a_late_arrival_appends_and_is_flagged() -> None:
	"""Chaos 11, the early half: a ``createTime`` older than the room tail."""
	make_room()
	STORE.table("Chat Room")[ROOM]["last_message_at"] = frappe.utils.now_datetime()
	resource = f"{SPACE}/messages/m1"
	ten_minutes_ago = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=10)
	client = _StubClient({resource: message_resource(resource, created=ten_minutes_ago)})

	process(resource, client=client)

	row = messages()[0]
	assert row["late_arrival"] == 1
	assert row["seq"] == 1, "a late arrival APPENDS; seq is assignment-ordered and immutable"
	assert row["clock_skew_ms"] > 9 * 60 * 1000
	assert seams.counters()[seams.COUNTER_LATE_ARRIVALS] == 1


def test_a_message_created_in_the_future_is_not_late_and_records_negative_skew() -> None:
	"""Chaos 11, the other half: ten minutes *ahead* is a clock disagreement, not a late arrival."""
	make_room()
	resource = f"{SPACE}/messages/m1"
	ahead = datetime.now(ZoneInfo("UTC")) + timedelta(minutes=10)
	client = _StubClient({resource: message_resource(resource, created=ahead)})

	process(resource, client=client)

	row = messages()[0]
	assert row["late_arrival"] == 0
	assert row["clock_skew_ms"] < -9 * 60 * 1000


def test_a_sustained_median_skew_alarms() -> None:
	make_room()
	SETTINGS.clock_skew_alert_ms = 1000
	ahead = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=30)
	for index in range(4):
		resource = f"{SPACE}/messages/m{index}"
		client = _StubClient({resource: message_resource(resource, created=ahead)})
		process(resource, client=client)

	assert seams.counters()[seams.COUNTER_SKEW_ALERTS] >= 1
	assert any("CLOCK_SKEW" in e["title"] for e in frappe.errors)


def test_one_slow_delivery_alone_does_not_alarm() -> None:
	"""The alarm is on the median over a window precisely so a single redelivery cannot page."""
	make_room()
	SETTINGS.clock_skew_alert_ms = 1000
	fresh = datetime.now(ZoneInfo("UTC"))
	stale = fresh - timedelta(minutes=30)
	for index, created in enumerate([fresh, fresh, stale, fresh, fresh]):
		resource = f"{SPACE}/messages/m{index}"
		client = _StubClient({resource: message_resource(resource, created=created)})
		process(resource, client=client)

	assert seams.counters()[seams.COUNTER_SKEW_ALERTS] == 0


# ---------------------------------------------------------------------------
# Rule 3 — the edit conflict
# ---------------------------------------------------------------------------


def _edit_scenario(*, erp_edited_minutes_ago: int | None, google_updated_minutes_ago: int) -> tuple[str, str]:
	make_room()
	edited_at = None
	if erp_edited_minutes_ago is not None:
		edited_at = frappe.utils.now_datetime() - timedelta(minutes=erp_edited_minutes_ago)
	local = make_message(text="erpnext text", gchat_message_name=f"{SPACE}/messages/m1", edited_at=edited_at)
	resource = f"{SPACE}/messages/m1"
	updated = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=google_updated_minutes_ago)
	client = _StubClient({resource: message_resource(resource, text="chat text", updated=updated)})
	process(resource, event="updated", client=client)
	return (local, resource)


def test_a_newer_chat_edit_wins_and_is_applied() -> None:
	"""Chaos 16, the branch where Google is strictly newer."""
	local, _resource = _edit_scenario(erp_edited_minutes_ago=30, google_updated_minutes_ago=1)

	row = STORE.table("Chat Message")[local]
	assert row["text"] == "chat text"
	assert row["is_edited"] == 1
	assert seams.counters()[seams.COUNTER_CONTEXT_STALE] == 1, "an edit must invalidate the digest"
	assert [p["event"] for p in frappe.published] == ["chat_message_edited"]
	assert seams.counters()[seams.COUNTER_NOTIFY_NEW_MESSAGE] == 0
	assert [r["change_type"] for r in revisions()] == ["Edit"]
	assert revisions()[0]["text_before"] == "erpnext text"
	assert revisions()[0]["text_after"] == "chat text"


def test_an_older_chat_edit_loses_and_the_revert_is_requeued() -> None:
	"""Chaos 16, and **the half people leave out**: the else-branch must re-queue outbound.

	Asserted against a real ``Chat Relay Job`` row written through ``outbox.create_relay_job``
	rather than a spy, because the thing that could go wrong is the operation string: the
	DocType's Select is the authority, and a job with an operation the worker does not
	recognise is queued, ordered, leased and never sent.
	"""
	local, _resource = _edit_scenario(erp_edited_minutes_ago=1, google_updated_minutes_ago=30)

	assert STORE.table("Chat Message")[local]["text"] == "erpnext text"
	jobs = list(STORE.table("Chat Relay Job").values())
	assert [j["operation"] for j in jobs] == [
		"Message Update"
	], "discarding an inbound edit without re-queueing leaves the two sides permanently divergent"
	assert jobs[0]["reference_name"] == local
	assert jobs[0]["impersonate_user"] == ALICE, "an edit must be relayed as the message's author"
	note = revisions()[0]["note"]
	assert "DISCARDED" in note and "Rule 3" in note
	assert seams.counters()[seams.COUNTER_REVERTS_APPLIED] == 1


def test_an_exactly_equal_timestamp_is_a_tie_and_erpnext_wins() -> None:
	"""The actual tie, which nothing tested until 2026-08-09.

	ADR §G.8 Rule 3: *"If they are equal, or if either is missing, ERPNext wins — because
	decision #1 makes ERPNext the source of truth, and a tie-break that favours the mirror
	inverts that."* Two edits landing in the same second is not exotic: relaying an ERPNext
	edit and a coworker editing the same message are exactly the pair this rule arbitrates,
	and Google's ``lastUpdateTime`` has second granularity.

	The three neighbouring scenarios cover strictly-newer, strictly-older and no-ERPNext-edit.
	Equality is the boundary between the first two, and it is the one where the comparison
	operator (``>`` versus ``>=``) decides which system owns the message. Left untested, a
	one-character change silently inverts decision #1.
	"""
	make_room()
	# One instant, expressed in both clocks. `edited_at` is Frappe-written site-local and the
	# Google stamp is UTC, so building them from the same moment is also what makes this a
	# test of the CONVERSION rather than of two coincidentally-close numbers.
	moment_utc = datetime.now(ZoneInfo("UTC")).replace(microsecond=0) - timedelta(minutes=5)
	local = make_message(
		text="erpnext text",
		gchat_message_name=f"{SPACE}/messages/m1",
		edited_at=frappe.utils.now_datetime().replace(microsecond=0) - timedelta(minutes=5),
	)
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource, text="chat text", updated=moment_utc)})

	process(resource, event="updated", client=client)

	assert STORE.table("Chat Message")[local]["text"] == "erpnext text", (
		"a tie must go to ERPNext; favouring the mirror inverts decision #1"
	)
	assert [j["operation"] for j in STORE.table("Chat Relay Job").values()] == ["Message Update"], (
		"the loser of a tie still has to be reverted, or the two sides stay divergent"
	)


def test_no_erpnext_edit_is_not_a_tie_it_is_not_a_conflict_at_all() -> None:
	"""ADR §G.8 Rule 3 opens *"When **both** sides have edited the same message"*.

	This test asserted the opposite until 2026-08-09, and the behaviour it locked in was a
	silent data-loss bug rather than a conservative default.

	``edited_at`` is written in exactly one place: the branch that applies an inbound edit. So
	requiring a non-null ``erp_at`` before an inbound edit could win made the condition
	unsatisfiable — for any message ERPNext had never itself edited, the answer was ``False``
	forever. Every edit a coworker made to their own message in the native Chat client was
	discarded **and** an outbound revert was queued, overwriting their edit in Chat with our
	stale copy. Not a tie broken in ERPNext's favour: a conflict that never existed, resolved
	against the only party who had actually written anything.

	"ERPNext wins ties" is intact and tested next door. It simply has a precondition, and this
	is the case that does not meet it.
	"""
	local, _resource = _edit_scenario(erp_edited_minutes_ago=None, google_updated_minutes_ago=1)
	assert STORE.table("Chat Message")[local]["text"] == "chat text", (
		"a Chat-side edit of a message ERPNext never edited must be applied, not reverted"
	)


def test_the_revert_loop_is_capped() -> None:
	make_room()
	SETTINGS.max_reverts_per_hour = 2
	assert inbound._charge_revert(ROOM) is True
	assert inbound._charge_revert(ROOM) is True
	assert inbound._charge_revert(ROOM) is False, "two systems must not be able to fight forever"


def test_a_replayed_edit_that_is_already_applied_writes_no_revision() -> None:
	make_room()
	local = make_message(
		text="chat text",
		gchat_message_name=f"{SPACE}/messages/m1",
		edited_at=frappe.utils.now_datetime() - timedelta(minutes=30),
	)
	resource = f"{SPACE}/messages/m1"
	client = _StubClient(
		{
			resource: message_resource(
				resource, text="chat text", updated=datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1)
			)
		}
	)

	process(resource, event="updated", client=client)

	assert revisions() == [], "an audit row must describe a change that happened"
	assert STORE.table("Chat Message")[local]["text"] == "chat text"


def test_the_timezone_conversion_is_explicit() -> None:
	"""A naive comparison is wrong by the site offset, always in Google's favour.

	Denver is UTC−6/−7, so a Google timestamp one hour *older* than the ERPNext watermark still
	reads as newer when the two are compared without conversion. This is the assertion that
	catches that.
	"""
	make_room()
	erp_edit_local = frappe.utils.now_datetime() - timedelta(hours=1)
	local = make_message(
		text="erpnext text", gchat_message_name=f"{SPACE}/messages/m1", edited_at=erp_edit_local
	)
	google_update = datetime.now(ZoneInfo("UTC")) - timedelta(hours=2)
	# Naively (both read as wall-clock numbers) Google's UTC stamp is ~4-5 hours AHEAD of the
	# Denver-local one and would win.
	assert google_update.replace(tzinfo=None) > erp_edit_local

	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource, text="chat text", updated=google_update)})
	process(resource, event="updated", client=client)

	assert (
		STORE.table("Chat Message")[local]["text"] == "erpnext text"
	), "converted correctly, Google's edit is an hour older and must lose"


# ---------------------------------------------------------------------------
# Tombstones
# ---------------------------------------------------------------------------


def test_a_delete_keeps_the_body_on_the_row() -> None:
	"""ADR §F.6.5 / §G.7.4. Google's tombstone is content-free, so ERPNext is the only copy."""
	make_room()
	local = make_message(text="please forget this", gchat_message_name=f"{SPACE}/messages/m1")
	resource = f"{SPACE}/messages/m1"
	deleted_at = datetime.now(ZoneInfo("UTC"))
	tombstone = {
		"name": resource,
		"sender": {"name": f"users/{_user_id_for(BOB)}"},
		"space": {"name": SPACE},
		"createTime": rfc3339(deleted_at - timedelta(minutes=5)),
		"deleteTime": rfc3339(deleted_at),
		"deletionMetadata": {"deletionType": "CREATOR"},
	}
	client = _StubClient({resource: tombstone})

	process(resource, event="deleted", client=client)

	row = STORE.table("Chat Message")[local]
	assert row["is_deleted"] == 1
	assert row["text"] == "please forget this", "the body STAYS — nobody else has it"
	assert row["deletion_source"] == "Google Chat"
	assert row["deleted_at"] is not None
	assert seams.counters()[seams.COUNTER_CONTEXT_STALE] == 1
	assert [p["event"] for p in frappe.published] == ["chat_message_deleted"]
	assert [r["change_type"] for r in revisions()] == ["Delete"]
	assert revisions()[0]["text_before"] == "please forget this"


@pytest.mark.parametrize(
	("deletion_type", "expected"),
	[
		("CREATOR", "Google Chat"),
		("ADMIN", "Admin"),
		("APP_MESSAGE_EXPIRY", "Retention"),
		("CREATOR_VIA_APP", "ERPNext"),
		("SPACE_OWNER_VIA_APP", "ERPNext"),
		("SOMETHING_GOOGLE_ADDED_LATER", "Google Chat"),
	],
)
def test_deletion_type_maps_onto_deletion_source(deletion_type: str, expected: str) -> None:
	make_room()
	local = make_message(gchat_message_name=f"{SPACE}/messages/m1")
	resource = f"{SPACE}/messages/m1"
	client = _StubClient(
		{
			resource: {
				"name": resource,
				"space": {"name": SPACE},
				"deleteTime": rfc3339(datetime.now(ZoneInfo("UTC"))),
				"deletionMetadata": {"deletionType": deletion_type},
			}
		}
	)

	process(resource, event="deleted", client=client)

	assert STORE.table("Chat Message")[local]["deletion_source"] == expected


def test_a_tombstoned_row_is_never_resurrected() -> None:
	make_room()
	local = make_message(gchat_message_name=f"{SPACE}/messages/m1")
	STORE.table("Chat Message")[local]["is_deleted"] = 1
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource, text="back from the dead")})

	verdict = process(resource, event="created", client=client)

	assert verdict == InboundVerdict.TOMBSTONED.value
	assert len(messages()) == 1
	assert STORE.table("Chat Message")[local]["is_deleted"] == 1


def test_a_delete_with_no_reachable_tombstone_still_applies() -> None:
	"""``VERIFY:`` whether ``get`` 404s on a deleted message is unstated; the delete must land."""
	make_room()
	local = make_message(gchat_message_name=f"{SPACE}/messages/m1")

	process(f"{SPACE}/messages/m1", event="deleted", client=_StubClient({}))

	assert STORE.table("Chat Message")[local]["is_deleted"] == 1
	assert STORE.table("Chat Message")[local]["deletion_source"] == "Google Chat"


# ---------------------------------------------------------------------------
# Row lifecycle
# ---------------------------------------------------------------------------


def test_a_malformed_payload_is_failed_and_kept() -> None:
	make_room()
	name = STORE.next_name("Chat Inbound Event")
	STORE.table("Chat Inbound Event")[name] = {
		"name": name,
		"status": "Received",
		"attempts": 0,
		"payload": json.dumps({"nonsense": True}),
		"received_at": frappe.utils.now_datetime(),
	}

	inbound.process_inbound_event(name)

	row = STORE.table("Chat Inbound Event")[name]
	assert row["status"] == "Failed"
	assert "MalformedEvent" in row["last_error"]
	assert row["payload"], "the payload is kept: it is the only way to reprocess after a fix"


def test_a_settled_row_is_not_processed_twice() -> None:
	make_room()
	resource = f"{SPACE}/messages/m1"
	client = _StubClient({resource: message_resource(resource)})
	row, _ = pubsub.ingest_delivery(_delivery(resource, message_id="p1", ack_id="a1"))
	inbound.process_inbound_event(row, client=client)

	assert inbound.process_inbound_event(row, client=client) == "already-settled"
	assert len(messages()) == 1


def test_pausing_inbound_leaves_the_row_for_the_sweeper() -> None:
	make_room()
	SETTINGS.pause_inbound = 1
	resource = f"{SPACE}/messages/m1"
	row, _ = pubsub.ingest_delivery(_delivery(resource, message_id="p1", ack_id="a1"))

	assert inbound.process_inbound_event(row) == "paused"
	assert STORE.table("Chat Inbound Event")[row]["status"] == "Received"


class _ExplodingClient:
	"""``messages.get`` always fails — a Google 5xx, a token mint timing out, a 404."""

	def get_message(self, name: str) -> dict[str, Any]:
		raise RuntimeError("google is having a moment")


def test_a_transient_fetch_failure_leaves_the_row_for_the_sweeper() -> None:
	make_room()
	resource = f"{SPACE}/messages/m1"
	row, _ = pubsub.ingest_delivery(_delivery(resource, message_id="p1", ack_id="a1"))

	assert inbound.process_inbound_event(row, client=_ExplodingClient()) == "retry"

	stored = STORE.table("Chat Inbound Event")[row]
	assert stored["status"] == "Received", "a transient failure must stay sweepable"
	assert stored["attempts"] == 1
	assert "RuntimeError" in stored["last_error"]
	assert messages() == []


def test_a_permanently_failing_row_stops_being_retried() -> None:
	"""Otherwise one anomaly becomes a permanent ten-minute load that buries its own Error Log."""
	make_room()
	SETTINGS.relay_max_attempts = 2
	resource = f"{SPACE}/messages/m1"
	row, _ = pubsub.ingest_delivery(_delivery(resource, message_id="p1", ack_id="a1"))

	assert inbound.process_inbound_event(row, client=_ExplodingClient()) == "retry"
	STORE.table("Chat Inbound Event")[row]["status"] = "Received"
	assert inbound.process_inbound_event(row, client=_ExplodingClient()) == "Failed"

	assert STORE.table("Chat Inbound Event")[row]["status"] == "Failed"
	assert any("INBOUND_GIVING_UP" in e["title"] for e in frappe.errors)


def test_a_failure_never_stores_a_traceback() -> None:
	"""Frappe's job handler writes ``get_traceback(with_context=True)`` — frame locals and all.

	The locals in the failing frames here hold a fetched Message resource (a coworker's body)
	and, one frame down, an ``Authorization: Bearer`` header. Nothing may escape this frame.
	"""
	make_room()
	resource = f"{SPACE}/messages/m1"
	row, _ = pubsub.ingest_delivery(_delivery(resource, message_id="p1", ack_id="a1"))
	inbound.process_inbound_event(row, client=_ExplodingClient())

	stored = str(STORE.table("Chat Inbound Event")[row]["last_error"])
	assert "Traceback" not in stored
	assert "Bearer" not in stored
	assert len(stored) <= 500


def test_the_sweeper_redrives_received_rows_and_leaves_failed_ones() -> None:
	old = frappe.utils.now_datetime() - timedelta(minutes=30)
	for status in ("Received", "Failed", "Processed"):
		name = STORE.next_name("Chat Inbound Event")
		STORE.table("Chat Inbound Event")[name] = {
			"name": name,
			"status": status,
			"received_at": old,
			"attempts": 0,
		}

	assert inbound.sweep_stuck_inbound_events()["requeued"] == 1
	frappe.db.commit()
	assert len(frappe.enqueued) == 1


def _in_flight_relay_job() -> None:
	"""One ``Chat Relay Job`` for this room, ``In Progress`` with an unexpired lease.

	The lease is what makes the claim a fact rather than a guess — a crashed worker leaves a row
	``In Progress`` forever — so the expiry is set in the future deliberately.
	"""
	STORE.table("Chat Relay Job")["JOB-1"] = {
		"name": "JOB-1",
		"room": ROOM,
		"status": "In Progress",
		"lease_expires_at": frappe.utils.now_datetime() + timedelta(minutes=5),
	}


def _deferrable_delivery() -> str:
	"""A ``deleted`` naming a message we cannot resolve — the event a defer exists for.

	With an outbound write in flight, the ``created`` this delete belongs to may be seconds
	behind, so the verdict is ``DEFER`` rather than ``IGNORE``.
	"""
	row, _ = pubsub.ingest_delivery(
		{
			"ackId": "a",
			"message": fixtures.message_deleted_event(
				space=SPACE, message=f"{SPACE}/messages/m1", pubsub_message_id=next_pubsub_id()
			)["message"],
		}
	)
	return row


def test_a_deferred_event_waits_on_a_durable_timer_rather_than_re_enqueueing_at_once() -> None:
	"""The delay has to be a column, because there is nowhere else to put it.

	``frappe.enqueue`` exposes no route to RQ's scheduler, and RQ's scheduling lives in the queue
	Redis a production deploy ``FLUSHDB``s. So the old ``enqueue_inbound_event(name,
	delay_seconds=…)`` could only discard its delay — the call passed ``queue``,
	``inbound_event`` and ``enqueue_after_commit`` and nothing else — and a ``DEFER`` re-entered
	the queue immediately. Three defers then spent the whole ``echo_defer_budget`` inside a
	millisecond and the mechanism waited out precisely nothing, which is the opposite of its
	purpose: an outbound write for the room completes in about a second, and the defer exists to
	outlast it.

	``available_at`` is that second, made durable, and :func:`sweep_stuck_inbound_events` is the
	clock that reads it.
	"""
	make_room()
	_in_flight_relay_job()
	row = _deferrable_delivery()

	assert inbound.process_inbound_event(row) == "DEFER"

	stored = STORE.table("Chat Inbound Event")[row]
	assert stored["status"] == "Received", "a defer is a delay, never a settlement"
	assert stored["defer_count"] == 1
	assert stored["available_at"] is not None, (
		"a defer with no stored timer is not a defer: nothing anywhere remembers that this row "
		"asked to be tried again shortly"
	)
	assert stored["available_at"] > frappe.utils.now_datetime()

	frappe.db.commit()
	assert frappe.enqueued == [], (
		"re-enqueueing here is the bug — the job would run again immediately, the in-flight "
		"write would still be open, and the budget would be gone before it ever elapsed"
	)

	# Before the timer, the sweeper declines the row rather than re-driving it early.
	assert inbound.sweep_stuck_inbound_events()["requeued"] == 0
	frappe.db.commit()
	assert frappe.enqueued == []

	# After it, the sweeper is what actually delivers the retry.
	stored["available_at"] = frappe.utils.now_datetime() - timedelta(seconds=1)
	assert inbound.sweep_stuck_inbound_events()["requeued"] == 1
	frappe.db.commit()
	assert len(frappe.enqueued) == 1


def test_enqueue_takes_no_delay_parameter_it_has_no_way_to_honour() -> None:
	"""A parameter that is accepted and dropped is worse than one that does not exist.

	The next reader trusts it. This one is asserted on the signature rather than on behaviour
	because behaviour cannot distinguish "honoured a zero delay" from "ignored a five-second
	one" — the absence of the parameter is the whole claim.
	"""
	import inspect

	assert "delay_seconds" not in inspect.signature(inbound.enqueue_inbound_event).parameters, (
		"the defer delay lives on Chat Inbound Event.available_at; an enqueue argument that "
		"silently does nothing will be believed"
	)


def test_the_defer_budget_and_the_failure_retry_budget_are_separate_counters() -> None:
	"""Two unrelated ceilings need two columns, and on one column they ate each other.

	``attempts`` bounds **failure** retries against ``relay_max_attempts``; ``defer_count``
	bounds **deferral** against ``echo_defer_budget``. Sharing ``attempts`` meant three defers
	left only two real retries before the row was marked ``Failed`` and exempted from the
	sweeper, and — the direction that actually loses data — one transient ``messages.get``
	timeout permanently shrank that event's defer budget, so an unrelated Google 5xx decided
	whether a coworker's message could still be told apart from our own echo.
	"""
	make_room()
	_in_flight_relay_job()
	row = _deferrable_delivery()
	# Google has failed to answer this delivery as many times as the defer budget allows
	# deferrals. The two numbers have nothing to do with each other.
	STORE.table("Chat Inbound Event")[row]["attempts"] = SETTINGS.echo_defer_budget

	assert (
		inbound.process_inbound_event(row) == "DEFER"
	), "a transient fetch failure must not spend this event's defer budget"

	stored = STORE.table("Chat Inbound Event")[row]
	assert stored["defer_count"] == 1
	assert stored["attempts"] == SETTINGS.echo_defer_budget, (
		"and the converse: a defer must not spend a failure retry, so the row is no closer to "
		"being marked Failed than it was"
	)


def test_the_defer_budget_is_a_hard_ceiling() -> None:
	"""A defer is a delay, never a suppression."""
	make_room()
	_in_flight_relay_job()
	row = _deferrable_delivery()
	STORE.table("Chat Inbound Event")[row]["defer_count"] = SETTINGS.echo_defer_budget

	assert inbound.process_inbound_event(row) == InboundVerdict.IGNORE.value


def test_an_expired_lease_is_not_an_in_flight_claim() -> None:
	make_room()
	STORE.table("Chat Relay Job")["JOB-1"] = {
		"name": "JOB-1",
		"room": ROOM,
		"status": "In Progress",
		"lease_expires_at": frappe.utils.now_datetime() - timedelta(minutes=5),
	}
	assert inbound._in_flight_claims(ROOM) == 0


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


def test_revision_numbers_are_monotonic_and_a_replay_collides() -> None:
	make_room()
	local = make_message()

	assert inbound.record_revision(local, room=ROOM, change_type="Create", text_after="a") is not None
	assert inbound.record_revision(local, room=ROOM, change_type="Edit", text_after="b") is not None
	assert [r["revision_no"] for r in revisions()] == [1, 2]

	# A retried writer recomputing the same number must fail the insert, not add a third row
	# claiming to be revision 2.
	doc = frappe.new_doc("Chat Message Revision")
	doc.message = local
	doc.revision_no = 2
	doc.change_type = "Edit"
	doc.origin = "Google Chat"
	with pytest.raises(UniqueValidationError):
		doc.insert()
	assert len(revisions()) == 2


def test_the_revision_room_is_backfilled_from_the_message() -> None:
	make_room()
	local = make_message()
	name = inbound.record_revision(local, room="", change_type="Create", text_after="a")
	assert STORE.table("Chat Message Revision")[name]["room"] == ROOM


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	"value",
	["2026-08-09T12:00:00Z", "2026-08-09T12:00:00.123Z", "2026-08-09T12:00:00.123456789Z"],
)
def test_google_timestamps_parse_to_aware_utc(value: str) -> None:
	parsed = inbound.parse_google_timestamp(value)
	assert parsed is not None
	assert parsed.tzinfo is not None
	assert parsed.utcoffset() == timedelta(0)


def test_a_site_local_datetime_round_trips_through_utc() -> None:
	local = frappe.utils.now_datetime()
	assert inbound.utc_to_site(inbound.site_to_utc(local)) == local


def test_an_unparseable_timestamp_is_none_rather_than_now() -> None:
	assert inbound.parse_google_timestamp("not a timestamp") is None
	assert inbound.parse_google_timestamp("") is None
	assert inbound.parse_google_timestamp(None) is None


# ======================================================================================
# A resource Google will not return — and the 403 that means two opposite things
# ======================================================================================
#
# Observed on production 2026-08-10, in the live smoke run. Deleting a thread parent with
# `force=true` cascades to its replies, and `messages.get` on a cascaded reply answers
# **403 PERMISSION_DENIED**, not a tombstone — while a *directly* deleted message answers
# with a readable tombstone carrying deleteTime and deletionMetadata.
#
# Chat's 403 body is, verbatim: "Permission denied to perform the requested action on the
# specified resource, or the resource doesn't exist." One status, two conditions, opposite
# handling. "Gone" is one message nobody can recover; "forbidden" is EVERY message in that
# space failing silently until somebody notices. The space-readability probe is what tells
# them apart, and these tests are the reason it can be trusted.


class _ApiError(Exception):
	"""Shaped like the transport's GoogleChatAPIError: what matters is `.status`.

	Defined locally rather than imported so this suite keeps its stdlib-only module scope —
	and so the test states its own contract with `fetch_message_resource`, which is "any
	exception carrying a .status", not "this one class".
	"""

	def __init__(self, status: int) -> None:
		super().__init__(f"HTTP {status}")
		self.status = status


class _GoneClient:
	"""``get_message`` fails with a chosen status; ``list_messages`` decides which branch."""

	def __init__(self, status: int, *, space_readable: bool) -> None:
		self.status = status
		self.space_readable = space_readable
		self.gets: list[str] = []
		self.lists: list[str] = []

	def get_message(self, name: str) -> dict[str, Any]:
		self.gets.append(name)
		raise _ApiError(self.status)

	def list_messages(self, space: str, **_kwargs: Any) -> dict[str, Any]:
		self.lists.append(space)
		if not self.space_readable:
			raise _ApiError(403)
		return {"messages": []}


def _gone_run(status: int, *, space_readable: bool) -> tuple[str, _GoneClient]:
	make_room()
	client = _GoneClient(status, space_readable=space_readable)
	verdict = process(f"{SPACE}/messages/gone1", event="created", client=client)
	return verdict, client


def test_a_gone_resource_is_settled_not_retried_to_the_ceiling() -> None:
	"""403 + a readable space ⇒ the message is gone. Settle it; do not burn the budget."""
	verdict, client = _gone_run(403, space_readable=True)

	assert verdict == InboundVerdict.IGNORE.value
	row = STORE.table("Chat Inbound Event")[next(iter(STORE.table("Chat Inbound Event")))]
	assert row["status"] == "Ignored", "a retry cannot conjure a resource Google has deleted"
	assert "gone rather than forbidden" in (row.get("last_error") or "")
	assert int(row.get("attempts") or 0) == 0, "settling is not a failure; attempts must not move"
	assert seams.counters()[seams.COUNTER_RESOURCES_GONE] == 1
	# No row invented: without the resource there is no client id, no body and no sender, and
	# Google keeps no copy of the text either.
	assert STORE.table("Chat Message") == {}


def test_a_404_is_gone_for_the_same_reason() -> None:
	verdict, _client = _gone_run(404, space_readable=True)
	assert verdict == InboundVerdict.IGNORE.value
	assert seams.counters()[seams.COUNTER_RESOURCES_GONE] == 1


def test_a_403_on_an_UNREADABLE_space_is_loud_because_it_is_not_gone() -> None:
	"""The direction that matters. A broken reader subject loses every message in the space.

	If this ever starts settling quietly, the symptom is that one space stops syncing and
	nothing anywhere says so — which is the exact failure mode the whole phase is built
	against. It must stay on the retry-and-alarm path.
	"""
	verdict, client = _gone_run(403, space_readable=False)

	assert verdict != InboundVerdict.IGNORE.value
	row = STORE.table("Chat Inbound Event")[next(iter(STORE.table("Chat Inbound Event")))]
	assert row["status"] != "Ignored", "an inaccessible space must never be settled as 'gone'"
	assert int(row.get("attempts") or 0) == 1, "it has to be retried; the fault may be transient"
	assert seams.counters()[seams.COUNTER_RESOURCES_GONE] == 0
	assert client.lists, "the readability probe is what distinguishes the two 403s"


def test_a_500_is_never_gone() -> None:
	"""Transient failures keep their retries. Only 403/404 are eligible for the gone branch."""
	verdict, client = _gone_run(500, space_readable=True)

	assert verdict != InboundVerdict.IGNORE.value
	assert seams.counters()[seams.COUNTER_RESOURCES_GONE] == 0
	assert not client.lists, "a 5xx must not even ask the readability question"
