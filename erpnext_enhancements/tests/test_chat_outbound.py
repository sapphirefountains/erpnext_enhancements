"""Bench-free tests for the outbound relay worker (``chat/sync/outbound.py``).

**Shape U: ``unittest`` with a ``frappe`` stub installed in ``setUpModule``.** It needs its
own ``python -m unittest erpnext_enhancements.tests.test_chat_outbound -v`` step in
``ci.yml``. The stub is installed at *execution* time, not import time, so it never fools a
bench-only suite's ``import frappe`` skip guard, and the suite gets its own CI step so the
stub cannot cross-talk with another one (``CLAUDE.md``).

--------------------------------------------------------------------------------------
What is under test, and what is not
--------------------------------------------------------------------------------------

The relay is exercised end to end against :class:`FakeChatAPI` injected as a
``transport=``, so every test runs the **real** builders, the **real** retry loop, the
**real** error classifier and the **real** ``_request`` contract. The only things replaced
are the three declared seams — ``build_client``, ``space_limiter``, ``project_quota`` — and
``frappe`` itself.

The two doubles that are *not* the fake API deserve a word each:

* ``_Limiter`` re-uses :func:`ratelimit.bucket_decision`, the same pure GCRA the Lua script
  deploys, and models ``block=True`` by **advancing the shared clock** rather than sleeping.
  So "the worker waited a second for the space bucket" is a deterministic, instant
  assertion, and a suite that paces ten writes at one per second finishes immediately.
* ``_FakeDB`` is an in-memory table store, not a mock. Filters, ordering, ``for_update`` and
  the unique index on ``gchat_message_name`` are real behaviours here, because half of what
  this module does *is* the query — "the lowest open ``job_seq`` for the room" is Rule 1,
  and a mock that returned a canned row would test nothing.

One clock drives everything: :class:`FakeClock` from the harness. ``now_datetime()`` is
derived from it, so advancing the clock advances leases, ``available_at`` and Google's own
timestamps together. Nothing here sleeps and nothing can pass or fail because of machine
speed.

--------------------------------------------------------------------------------------
The named chaos cases (Phase 2 §7 tier 3) this file owns
--------------------------------------------------------------------------------------

4.  timeout then success ⇒ **one** Chat message
5.  delete arriving while the create is In Progress
7.  ten messages in two seconds ⇒ all ten, in order, having backed off
8.  worker SIGKILL mid-relay
9.  429 for a sustained period, then recovery
17. kill switch toggled mid-burst

Each has a test named after it. No test asserts on a message body being logged, and the
harness journal is body-free by construction.
"""

from __future__ import annotations

import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

outbound = None
seams = None
ratelimit = None

ALICE = "alice@example.com"
BOB = "bob@example.com"

STATE: dict[str, Any] = {}

#: ``FakeClock``'s default start, as a datetime. The harness constant is
#: 2026-08-09T00:00:00Z in epoch milliseconds; keeping the two in step is what lets one
#: clock drive both Google's timestamps and Frappe's site-local ``now_datetime()``.
CLOCK_EPOCH = datetime(2026, 8, 9, 0, 0, 0)


# --------------------------------------------------------------------------------------
# The frappe stub
# --------------------------------------------------------------------------------------


class _Dict(dict):
	def __getattr__(self, key: str) -> Any:
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key: str, value: Any) -> None:
		self[key] = value


class _ValidationError(Exception):
	pass


class _PermissionError(Exception):
	pass


class _DuplicateEntryError(NameError):
	"""Frappe's primary-key collision. ``NameError``-derived, exactly as the real one is."""


class _UniqueValidationError(_ValidationError):
	"""The one a **non**-primary unique index raises — ``gchat_message_name``'s collision.

	It shares no base with :class:`_DuplicateEntryError` beyond ``Exception``, which is the
	whole reason the relay has to catch both. Modelled faithfully so a test can prove it.
	"""


def _now() -> datetime:
	"""Site-local ``now``, derived from the single fake clock."""
	clock = STATE.get("clock")
	if clock is None:
		return CLOCK_EPOCH
	from erpnext_enhancements.chat.testing.fake_chat import DEFAULT_CLOCK_START_MS

	return CLOCK_EPOCH + timedelta(milliseconds=clock.now_ms() - DEFAULT_CLOCK_START_MS)


def _as_datetime(value: Any) -> Any:
	if value is None or isinstance(value, datetime):
		return value
	try:
		return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	except ValueError:
		return None


def _compare(left: Any, right: Any) -> int | None:
	left_dt, right_dt = _as_datetime(left), _as_datetime(right)
	if isinstance(left_dt, datetime) and isinstance(right_dt, datetime):
		if left_dt.tzinfo and not right_dt.tzinfo:
			left_dt = left_dt.replace(tzinfo=None)
		if right_dt.tzinfo and not left_dt.tzinfo:
			right_dt = right_dt.replace(tzinfo=None)
		return (left_dt > right_dt) - (left_dt < right_dt)
	if left is None or right is None:
		return None
	try:
		return (left > right) - (left < right)
	except TypeError:
		return None


def _matches(row: dict[str, Any], filters: Any) -> bool:
	if not filters:
		return True
	if isinstance(filters, str):
		return row.get("name") == filters
	for field, condition in dict(filters).items():
		value = row.get(field)
		if isinstance(condition, tuple | list) and len(condition) == 2:
			operator, wanted = condition
			operator = str(operator).lower()
			if operator == "in":
				if value not in list(wanted):
					return False
				continue
			if operator == "not in":
				if value in list(wanted):
					return False
				continue
			if operator == "like":
				if not str(value or "").startswith(str(wanted).rstrip("%")):
					return False
				continue
			order = _compare(value, wanted)
			if order is None:
				return False
			if operator == "<" and not order < 0:
				return False
			if operator == "<=" and not order <= 0:
				return False
			if operator == ">" and not order > 0:
				return False
			if operator == ">=" and not order >= 0:
				return False
			if operator == "!=" and order == 0:
				return False
			continue
		if value != condition:
			return False
	return True


class _FakeDB:
	"""An in-memory table store with the parts of ``frappe.db`` the relay touches.

	The unique index on ``Chat Message.gchat_message_name`` is enforced for real, because
	Rule 2 (FIRST-WRITER-WINS on the resource name) is entirely a statement about what that
	index does, and a store that let two rows hold the same value would make the rule
	untestable.
	"""

	def __init__(self) -> None:
		self.tables: dict[str, dict[str, dict[str, Any]]] = {}
		self.commits = 0
		self.rollbacks = 0
		#: Every write, so a test can assert ``update_modified=False`` on the projection.
		self.writes: list[tuple[str, str, dict[str, Any], bool]] = []

	# -- helpers ---------------------------------------------------------------------

	def table(self, doctype: str) -> dict[str, dict[str, Any]]:
		return self.tables.setdefault(doctype, {})

	def insert(self, doctype: str, row: dict[str, Any]) -> dict[str, Any]:
		stored = dict(row)
		stored.setdefault("creation", _now())
		stored.setdefault("modified", _now())
		self.table(doctype)[stored["name"]] = stored
		return stored

	def rows(self, doctype: str) -> list[dict[str, Any]]:
		return list(self.table(doctype).values())

	# -- the frappe.db surface -------------------------------------------------------

	def get_value(
		self,
		doctype: str,
		filters: Any = None,
		fieldname: Any = "name",
		as_dict: bool = False,
		for_update: bool = False,
		**_ignored: Any,
	) -> Any:
		matched = [row for row in self.rows(doctype) if _matches(row, filters)]
		if not matched:
			return None
		row = matched[0]
		if isinstance(fieldname, list | tuple):
			values = {field: row.get(field) for field in fieldname}
			return _Dict(values) if as_dict else list(values.values())
		if as_dict:
			return _Dict({fieldname: row.get(fieldname)})
		return row.get(fieldname)

	def set_value(
		self,
		doctype: str,
		name: Any,
		field: Any,
		value: Any = None,
		update_modified: bool = True,
		**_ignored: Any,
	) -> None:
		updates = dict(field) if isinstance(field, dict) else {str(field): value}
		targets = [row for row in self.rows(doctype) if _matches(row, name)]
		for row in targets:
			for key, new in updates.items():
				if (
					doctype == "Chat Message"
					and key == "gchat_message_name"
					and new
					and any(
						other.get("gchat_message_name") == new and other["name"] != row["name"]
						for other in self.rows(doctype)
					)
				):
					raise _UniqueValidationError(f"{new} must be unique")
				row[key] = new
			if update_modified:
				row["modified"] = _now()
			self.writes.append((doctype, row["name"], dict(updates), bool(update_modified)))

	def exists(self, doctype: str, filters: Any = None) -> str | None:
		"""``frappe.db.exists`` — the row's name, or ``None``.

		Needed by ``attachments.record_outbound_attachments``, which probes per ``File`` so a
		retried relay updates the row it already wrote instead of adding a second one.
		"""
		for row in self.rows(doctype):
			if _matches(row, filters):
				return str(row.get("name") or "")
		return None

	def delete(self, doctype: str, filters: Any) -> None:
		for row in [row for row in self.rows(doctype) if _matches(row, filters)]:
			self.table(doctype).pop(row["name"], None)

	def commit(self) -> None:
		self.commits += 1

	def rollback(self, *_a: Any, **_k: Any) -> None:
		self.rollbacks += 1

	def is_unique_key_violation(self, exc: BaseException) -> bool:
		return isinstance(exc, _UniqueValidationError | _DuplicateEntryError)


class _Cache:
	def __init__(self) -> None:
		self.store: dict[str, Any] = {}

	def get_value(self, key: str, *_a: Any, **_k: Any) -> Any:
		return self.store.get(key)

	def set_value(self, key: str, value: Any, **_k: Any) -> None:
		self.store[key] = value

	def delete(self, key: str) -> None:
		self.store.pop(key, None)

	def incrby(self, key: str, step: int) -> int:
		self.store[key] = int(self.store.get(key) or 0) + int(step)
		return self.store[key]

	def mget(self, keys: list[str]) -> list[Any]:
		return [self.store.get(key) for key in keys]


class _Logger:
	def __init__(self) -> None:
		self.lines: list[tuple[str, str]] = []

	def _record(self, level: str, message: str, *_a: Any, **_k: Any) -> None:
		self.lines.append((level, str(message)))

	def info(self, message: str, *a: Any, **k: Any) -> None:
		self._record("info", message)

	def debug(self, message: str, *a: Any, **k: Any) -> None:
		self._record("debug", message)

	def warning(self, message: str, *a: Any, **k: Any) -> None:
		self._record("warning", message)

	def error(self, message: str, *a: Any, **k: Any) -> None:
		self._record("error", message)


def _install_frappe_stub() -> None:
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.flags = _Dict()
	frappe.local = types.SimpleNamespace(site="relay-test.local")
	frappe.session = _Dict(user="operator@example.com")

	frappe.ValidationError = _ValidationError
	frappe.PermissionError = _PermissionError
	frappe.DuplicateEntryError = _DuplicateEntryError
	frappe.UniqueValidationError = _UniqueValidationError

	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.__dict__["_"] = lambda text, *a, **k: text

	def _throw(message: Any, exc: Any = None, title: Any = None, **_k: Any) -> None:
		raise _ValidationError(str(message))

	def _only_for(role: Any, *_a: Any, **_k: Any) -> None:
		roles = role if isinstance(role, list | tuple) else [role]
		if not set(STATE.get("roles", [])) & set(roles):
			raise _PermissionError(f"not permitted: {roles}")

	def _log_error(title: Any = None, message: Any = None, **_k: Any) -> None:
		STATE["errors"].append({"title": str(title or ""), "message": str(message or "")})

	def _enqueue(method: Any, **kwargs: Any) -> None:
		STATE["enqueued"].append({"method": str(method), **kwargs})

	def _get_all(
		doctype: str,
		filters: Any = None,
		fields: Any = None,
		order_by: Any = None,
		limit: Any = None,
		pluck: Any = None,
		**_ignored: Any,
	) -> list[Any]:
		rows = [row for row in STATE["db"].rows(doctype) if _matches(row, filters)]
		for clause in reversed([part.strip() for part in str(order_by or "").split(",") if part.strip()]):
			field, _, direction = clause.partition(" ")
			rows.sort(
				key=lambda row, f=field: (row.get(f) is None, row.get(f) or 0),
				reverse=direction.strip().lower() == "desc",
			)
		if limit:
			rows = rows[: int(limit)]
		if pluck:
			return [row.get(pluck) for row in rows]
		if fields:
			return [_Dict({field: row.get(field) for field in fields}) for row in rows]
		return [_Dict(name=row.get("name")) for row in rows]

	class _StubDoc:
		"""``frappe.get_doc``'s return, just real enough for the two writers that use it.

		``Notification Log`` still lands in ``STATE["notifications"]`` — the alert tests assert
		on that list and it is not a table anybody queries. **Everything else inserts into the
		fake store**, because ``attachments.record_outbound_attachments`` writes real
		``Chat Attachment`` rows that the attachment test then reads back by ``ingest_state``;
		a doc whose ``insert`` went nowhere would let a relay that recorded nothing pass.

		``get_content`` is the ``File`` half. ``attachments._file_bytes`` reads bytes through
		``get_doc("File", name).get_content()``, so the fake row carries them under
		``_content`` and the **real** ``_file_bytes`` runs — including its "unreadable File
		degrades to no bytes" branch, which is one of the failure directions under test.
		"""

		def __init__(self, payload: Any) -> None:
			self.payload = dict(payload) if isinstance(payload, dict) else {}
			self.name = str(self.payload.get("name") or "")

		def get_content(self) -> Any:
			return self.payload.get("_content") or b""

		def insert(self, *_a: Any, **_k: Any) -> None:
			doctype = str(self.payload.get("doctype") or "")
			if doctype == "Notification Log":
				STATE["notifications"].append(dict(self.payload))
				return
			if not self.name:
				self.name = f"{doctype or 'DOC'}-{len(STATE['db'].table(doctype)) + 1:04d}"
			STATE["db"].insert(doctype, dict(self.payload, name=self.name))

	def _get_doc(payload: Any = None, name: Any = None, *_a: Any, **_k: Any) -> _StubDoc:
		"""Both call shapes: ``get_doc({...})`` to build, ``get_doc(doctype, name)`` to load."""
		if isinstance(payload, str):
			row = STATE["db"].table(payload).get(str(name)) or {}
			return _StubDoc({"doctype": payload, **row})
		return _StubDoc(payload)

	frappe.throw = _throw
	frappe.only_for = _only_for
	frappe.log_error = _log_error
	frappe.enqueue = _enqueue
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.get_cached_doc = lambda *a, **k: STATE["settings"]
	frappe.cache = lambda: STATE["cache"]
	frappe.logger = lambda *a, **k: STATE["logger"]
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.db = _FakeDatabaseProxy()

	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = _now
	utils.nowdate = lambda: _now().date()
	utils.cint = lambda value=0: int(value or 0)
	utils.flt = lambda value=0, *a, **k: float(value or 0)
	utils.get_datetime = lambda value=None: _as_datetime(value) if value is not None else _now()
	utils.get_system_timezone = lambda: "UTC"
	utils.get_url = lambda path="": f"https://erp.example.com{path}"
	utils.escape_html = lambda text: str(text)

	def _add_to_date(date: Any = None, **kwargs: Any) -> datetime:
		base = _as_datetime(date) or _now()
		return base + timedelta(
			days=int(kwargs.get("days") or 0),
			hours=int(kwargs.get("hours") or 0),
			minutes=int(kwargs.get("minutes") or 0),
			seconds=int(kwargs.get("seconds") or 0),
		)

	utils.add_to_date = _add_to_date
	utils.add_days = lambda date=None, days=0: _add_to_date(date, days=days)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class _Document:
		def __init__(self, *a: Any, **k: Any) -> None:
			pass

		def is_new(self) -> bool:
			return True

		def get_doc_before_save(self) -> Any:
			return None

		def get(self, key: str, default: Any = None) -> Any:
			return getattr(self, key, default)

	document.Document = _Document
	model.document = document
	frappe.model = model

	database = types.ModuleType("frappe.database")
	database_inner = types.ModuleType("frappe.database.database")

	import contextlib

	@contextlib.contextmanager
	def _savepoint(catch: Any = None):
		"""Frappe's savepoint, faithfully: it swallows only what ``catch`` names.

		Modelling that exactly is the point of this suite's Rule 2 test — a savepoint that
		swallowed everything would make "catch both exception types" untestable.
		"""
		try:
			yield
		except Exception as exc:
			STATE["db"].rollbacks += 1
			classes = catch if isinstance(catch, tuple) else ((catch,) if catch else ())
			if classes and isinstance(exc, classes):
				return
			raise

	database_inner.savepoint = _savepoint
	database.database = database_inner
	frappe.database = database

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	sys.modules["frappe.database"] = database
	sys.modules["frappe.database.database"] = database_inner


class _FakeDatabaseProxy:
	"""``frappe.db``, resolved through ``STATE`` so each test gets a clean store.

	The module-scope ``frappe`` object is installed once (installing it per test would
	re-import half the app), so the *database* has to be swappable underneath it. Delegating
	every attribute keeps the indirection invisible to the code under test.
	"""

	def __getattr__(self, name: str) -> Any:
		return getattr(STATE["db"], name)


# --------------------------------------------------------------------------------------
# Doubles for the three declared seams
# --------------------------------------------------------------------------------------


class _Limiter:
	"""The per-space bucket, running the **real** GCRA arithmetic against the fake clock.

	``block=True`` advances the clock instead of sleeping. That is the faithful model: the
	production limiter sleeps out the wait inside the worker, which is safe there because the
	lease is held and the room is a strict FIFO. Here it means a ten-message burst paces at
	one write per second and the suite still finishes instantly.
	"""

	def __init__(self, clock: Any) -> None:
		self.clock = clock
		self.free: dict[str, int] = {}
		self.acquisitions: list[tuple[str, int]] = []
		self.waits_ms = 0

	def acquire(self, space: str, *, cost_ms: int, block: bool = True) -> Any:
		self.acquisitions.append((space, int(cost_ms)))
		while True:
			decision = ratelimit.bucket_decision(self.clock.now_ms(), self.free.get(space, 0), cost_ms)
			if decision.allowed:
				self.free[space] = decision.next_free_ms
				return decision
			if not block:
				return decision
			self.waits_ms += decision.wait_ms
			self.clock.advance(decision.wait_ms)

	def peek(self, space: str) -> Any:
		return ratelimit.bucket_decision(self.clock.now_ms(), self.free.get(space, 0), 0)


class _Quota:
	"""Per-project fixed windows, running the real :func:`ratelimit.quota_decision`."""

	def __init__(self, clock: Any) -> None:
		self.clock = clock
		self.counts: dict[tuple[str, int], int] = {}
		self.charges: list[str] = []

	def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
		self.charges.append(bucket)
		window = ratelimit.fixed_window_index(self.clock.now_ms(), ratelimit.PROJECT_WINDOW_MS)
		current = self.counts.get((bucket, window), 0)
		decision = ratelimit.quota_decision(current, cost, limit)
		if decision.allowed:
			self.counts[(bucket, window)] = decision.count
		return decision.allowed


# --------------------------------------------------------------------------------------
# Module setup
# --------------------------------------------------------------------------------------


def setUpModule() -> None:
	global outbound, seams, ratelimit
	_install_frappe_stub()
	from erpnext_enhancements.chat import seams as seams_module
	from erpnext_enhancements.chat.sync import outbound as outbound_module
	from erpnext_enhancements.chat.sync import ratelimit as ratelimit_module

	outbound = outbound_module
	seams = seams_module
	ratelimit = ratelimit_module


def _settings(**overrides: Any) -> _Dict:
	base = {
		"enabled": 1,
		"dry_run_mode": 0,
		"relay_outbound_enabled": 1,
		"pause_outbound": 0,
		"relay_max_attempts": 3,
		"relay_initial_backoff_seconds": 2,
		"backoff_cap_seconds": 32,
		"http_timeout_seconds": 30,
		"sweeper_batch_size": 200,
		"message_byte_limit": 32000,
		"project_message_writes_per_minute": 3000,
		"project_membership_writes_per_minute": 300,
		"project_space_writes_per_minute": 60,
		"project_attachment_writes_per_minute": 600,
		# Off, as the field ships. Written out rather than left absent so the attachment tests
		# read as switching a documented flag on, not as depending on a missing-key default.
		"mirror_attachments": 0,
		"circuit_breaker_threshold": 10,
		"circuit_breaker_cooldown_seconds": 300,
	}
	base.update(overrides)
	return _Dict(base)


class RelayTestCase(unittest.TestCase):
	"""Wires the fake Chat API, the fake DB and the three seams for every test."""

	space_write_quota = True
	request_latency_ms = 0
	fake_settings_kwargs: ClassVar[dict[str, Any]] = {}

	def setUp(self) -> None:
		from erpnext_enhancements.chat.gchat.client import AuthIdentity, GoogleChatClient
		from erpnext_enhancements.chat.testing.fake_chat import (
			FakeChatAPI,
			FakeChatSettings,
			FakeClock,
		)

		self.clock = FakeClock()
		self.fake = FakeChatAPI(
			clock=self.clock,
			enforce_space_write_quota=self.space_write_quota,
			request_latency_ms=self.request_latency_ms,
		)
		self.limiter = _Limiter(self.clock)
		self.quota = _Quota(self.clock)

		STATE.clear()
		STATE.update(
			{
				"clock": self.clock,
				"db": _FakeDB(),
				"cache": _Cache(),
				"logger": _Logger(),
				"settings": _settings(),
				"errors": [],
				"enqueued": [],
				"notifications": [],
				"roles": ["System Manager"],
			}
		)
		self.db = STATE["db"]

		fake_settings = FakeChatSettings(**self.fake_settings_kwargs)

		def _build_client(subject: str, *, identity: str = "USER", correlation_id: str = "") -> Any:
			return GoogleChatClient(
				subject=subject,
				identity=AuthIdentity.USER,
				dry_run=False,
				settings=fake_settings,
				token_provider=self.fake.token_provider,
				transport=self.fake,
				correlation_id=correlation_id or None,
			)

		self._saved = (outbound.build_client, outbound.space_limiter, outbound.project_quota)
		outbound.build_client = _build_client
		outbound.space_limiter = lambda: self.limiter
		outbound.project_quota = lambda: self.quota
		seams.reset_counters()

	def tearDown(self) -> None:
		outbound.build_client, outbound.space_limiter, outbound.project_quota = self._saved

	# -- fixtures --------------------------------------------------------------------

	def make_room(self, *, name: str = "ROOM-0001", members: tuple[str, ...] = (ALICE,)) -> str:
		space = self.fake.seed_space(display_name="Relay Room", members=list(members), creator=ALICE)
		self.db.insert("Chat Room", {"name": name, "gchat_space_name": space, "seq_high_water": 0})
		for index, email in enumerate(members):
			self.db.insert(
				"Chat Room Member",
				{
					"name": f"{name}-M{index}",
					"room": name,
					"user": email,
					"gchat_member_state": "JOINED",
				},
			)
		return space

	def make_message(
		self, *, name: str, room: str = "ROOM-0001", text: str = "hello", seq: int = 1, sender: str = ALICE
	) -> str:
		self.db.insert(
			"Chat Message",
			{
				"name": name,
				"room": room,
				"seq": seq,
				"sender": sender,
				"text": text,
				"text_plain": text,
				"client_message_id": f"client-{name.lower().replace('-', '')}",
				"sync_state": "Pending",
				"sync_origin": "ERPNext",
				"is_deleted": 0,
			},
		)
		return name

	def make_job(
		self,
		*,
		name: str,
		job_seq: int,
		operation: str = "Message Create",
		message: str = "MSG-0001",
		room: str = "ROOM-0001",
		status: str = "Pending",
		attempts: int = 0,
		available_at: Any = None,
		impersonate_user: str = ALICE,
	) -> str:
		self.db.insert(
			"Chat Relay Job",
			{
				"name": name,
				"room": room,
				"job_seq": job_seq,
				"operation": operation,
				"status": status,
				"attempts": attempts,
				"available_at": available_at if available_at is not None else _now(),
				"lease_expires_at": None,
				"reference_doctype": "Chat Message",
				"reference_name": message,
				"request_id": f"req-{name.lower()}",
				"impersonate_user": impersonate_user,
				"payload": "",
				"last_error": "",
			},
		)
		return name

	# -- assertions ------------------------------------------------------------------

	def job(self, name: str) -> dict[str, Any]:
		return dict(self.db.table("Chat Relay Job")[name])

	def message_row(self, name: str) -> dict[str, Any]:
		return dict(self.db.table("Chat Message")[name])

	def alerts(self, title: str) -> list[dict[str, Any]]:
		return [entry for entry in STATE["errors"] if entry["title"] == title]

	def relayed_texts(self, space: str) -> list[str]:
		return [message.get("text", "") for message in self.fake.messages_in(space)]


# --------------------------------------------------------------------------------------
# The pure helpers
# --------------------------------------------------------------------------------------


class TestPureHelpers(RelayTestCase):
	def test_lease_covers_the_whole_job_not_one_http_timeout(self) -> None:
		"""A lease sized to one timeout reaps live workers, which duplicates messages.

		One job is up to ``relay_max_attempts`` synchronous HTTP calls with jittered sleeps
		between them, plus a blocking bucket acquire before the first — under exactly the
		Google slowdown that makes leases matter.
		"""
		naive = 30 + outbound.LEASE_MARGIN_SECONDS
		computed = outbound.lease_seconds(http_timeout=30, max_attempts=3, backoff_base=2, backoff_cap=32)
		self.assertGreater(computed, naive)
		# 5s block + 3 x 30s + (2 + 4) sleeps + 60s margin.
		self.assertAlmostEqual(computed, 5 + 90 + 6 + 60, places=6)

	def test_lease_is_clamped_so_a_settings_accident_cannot_strand_a_message(self) -> None:
		self.assertEqual(
			outbound.lease_seconds(http_timeout=3600, max_attempts=9, backoff_base=60, backoff_cap=600),
			float(outbound.LEASE_MAX_SECONDS),
		)

	def test_blocking_predecessor_is_the_head_of_the_fifo_unless_it_is_us(self) -> None:
		rows = [
			{"name": "J1", "job_seq": 1, "status": "In Progress"},
			{"name": "J2", "job_seq": 2, "status": "Pending"},
		]
		self.assertIsNone(outbound.blocking_predecessor(rows[:1], "J1"))
		self.assertEqual(outbound.blocking_predecessor(rows, "J2")["name"], "J1")
		self.assertIsNone(outbound.blocking_predecessor([], "J2"))

	def test_a_terminal_predecessor_does_not_block_forever(self) -> None:
		"""``Dead`` and ``Skipped`` are absent from ``BLOCKING_STATUSES`` on purpose.

		A literal "not Done" reading would wedge a room's entire mirror behind one dead
		letter, silently. The edit still converges because ``patch`` upserts through
		``allowMissing`` — the documented safety net under Rule 1.
		"""
		self.assertNotIn("Dead", outbound.BLOCKING_STATUSES)
		self.assertNotIn("Skipped", outbound.BLOCKING_STATUSES)
		self.assertNotIn("Done", outbound.BLOCKING_STATUSES)

	def test_circuit_opens_at_the_threshold_and_stays_open_for_the_cooldown(self) -> None:
		closed = outbound.circuit_decision(
			failures=9, threshold=10, open_until_ms=0, now_ms=1000, cooldown_seconds=300
		)
		self.assertFalse(closed.open)

		opened = outbound.circuit_decision(
			failures=10, threshold=10, open_until_ms=0, now_ms=1000, cooldown_seconds=300
		)
		self.assertTrue(opened.open)
		self.assertEqual(opened.retry_after_seconds, 300)

		# The counter is not incremented while open, so the stored instant — not the
		# counter — is what keeps it open. Otherwise every probe re-opens for a fresh
		# cooldown and the outage never ends.
		still = outbound.circuit_decision(
			failures=0, threshold=10, open_until_ms=61_000, now_ms=1_000, cooldown_seconds=300
		)
		self.assertTrue(still.open)
		self.assertEqual(still.retry_after_seconds, 60)

	def test_a_zero_threshold_disables_the_breaker(self) -> None:
		decision = outbound.circuit_decision(
			failures=999, threshold=0, open_until_ms=0, now_ms=1, cooldown_seconds=300
		)
		self.assertFalse(decision.open)


# --------------------------------------------------------------------------------------
# The happy path, the projection and the dependency gate
# --------------------------------------------------------------------------------------


class TestHappyPath(RelayTestCase):
	def test_one_message_relays_and_binds_its_resource_name(self) -> None:
		space = self.make_room()
		self.make_message(name="MSG-0001", text="first")
		self.make_job(name="JOB-1", job_seq=1)

		summary = outbound.drain_room("ROOM-0001")

		self.assertEqual(summary["done"], 1)
		self.assertEqual(self.relayed_texts(space), ["first"])
		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Done")
		self.assertIsNone(job["lease_expires_at"])
		message = self.message_row("MSG-0001")
		self.assertTrue(message["gchat_message_name"])
		self.assertEqual(message["sync_state"], "Relayed")
		self.assertEqual(seams.counters()["messages_relayed"], 1)

	def test_the_sync_state_projection_never_touches_modified(self) -> None:
		"""The room digest watermark is ``(max(seq), count(*), max(modified))``.

		A relay retry that bumped ``modified`` would invalidate every cached digest for the
		room on every attempt, which is why the projection is the one write in this module
		that must pass ``update_modified=False``.
		"""
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		outbound.drain_room("ROOM-0001")

		projections = [
			write for write in self.db.writes if write[0] == "Chat Message" and "sync_state" in write[2]
		]
		self.assertTrue(projections)
		for write in projections:
			self.assertFalse(write[3], f"sync_state written with update_modified=True: {write}")

	def test_an_inbound_origin_message_is_never_projected_back_to_relayed(self) -> None:
		"""``sync_origin = 'Google Chat'`` means ``Inbound`` forever — an outbound edit of an
		inbound message does not make the message ours."""
		self.make_room()
		self.make_message(name="MSG-0001")
		self.db.set_value("Chat Message", "MSG-0001", {"sync_origin": "Google Chat", "sync_state": "Inbound"})
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")
		self.assertEqual(self.message_row("MSG-0001")["sync_state"], "Inbound")

	def test_an_oversized_message_is_truncated_with_a_link_not_rejected(self) -> None:
		space = self.make_room()
		self.make_message(name="MSG-0001", text="x" * 500)
		STATE["settings"]["message_byte_limit"] = 200
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.message_row("MSG-0001")["truncated_for_relay"], 1)
		relayed = self.relayed_texts(space)[0]
		self.assertLessEqual(len(relayed.encode("utf-8")), 200)
		self.assertIn("truncated", relayed)
		# The full body is untouched on the ERPNext row, which is the source of truth.
		self.assertEqual(len(self.message_row("MSG-0001")["text"]), 500)


class TestDependencyGate(RelayTestCase):
	def test_a_room_with_no_space_defers_and_spends_no_attempt(self) -> None:
		"""Provisioning is a prerequisite job, not a side effect. Waiting must be free."""
		self.db.insert("Chat Room", {"name": "ROOM-0001", "gchat_space_name": ""})
		self.db.insert(
			"Chat Room Member",
			{"name": "M1", "room": "ROOM-0001", "user": ALICE, "gchat_member_state": "JOINED"},
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)

		summary = outbound.drain_room("ROOM-0001")

		self.assertEqual(summary["deferred"], 1)
		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Pending")
		self.assertEqual(job["attempts"], 0)
		self.assertIn("gchat_space_name", job["last_error"])
		self.assertGreater(job["available_at"], _now())
		self.assertEqual(self.fake.calls, [])

	def test_an_invited_but_not_joined_author_defers(self) -> None:
		"""``INVITED`` is not ``JOINED``: an invited coworker cannot post, and relaying as
		them is a 403, which classifies non-retryable and dead-letters a good message."""
		self.make_room()
		self.db.set_value(
			"Chat Room Member", {"room": "ROOM-0001", "user": ALICE}, "gchat_member_state", "INVITED"
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Pending")
		self.assertEqual(job["attempts"], 0)
		self.assertIn("JOINED", job["last_error"])
		self.assertEqual(self.fake.calls, [])

	def test_a_deleted_message_row_is_skipped_not_dead_lettered(self) -> None:
		self.make_room()
		self.make_job(name="JOB-1", job_seq=1, message="MSG-GONE")
		outbound.drain_room("ROOM-0001")
		self.assertEqual(self.job("JOB-1")["status"], "Skipped")
		self.assertEqual(self.alerts(outbound.ALERT_DEAD_LETTER), [])


class TestQuotaRouting(RelayTestCase):
	def test_membership_writes_consume_no_per_space_budget(self) -> None:
		"""Correction 1. Charging them to the 1-write/second space bucket throttles a bulk
		provisioning sweep roughly 300x harder than the API requires."""
		outbound.charge_membership_write(STATE["settings"])
		self.assertEqual(self.limiter.acquisitions, [])
		self.assertEqual(self.quota.charges, [ratelimit.BUCKET_MEMBERSHIP_WRITES])

	def test_space_setup_consumes_no_per_space_budget_either(self) -> None:
		"""Correction 2 — the space does not exist yet, so there is no bucket to charge."""
		outbound.charge_space_write(STATE["settings"])
		self.assertEqual(self.limiter.acquisitions, [])
		self.assertEqual(self.quota.charges, [ratelimit.BUCKET_SPACE_WRITES])

	def test_an_attachment_upload_costs_the_space_two_seconds(self) -> None:
		"""``media.upload`` shares the write bucket with ``messages.create``."""
		outbound.charge_attachment_write(STATE["settings"], "spaces/AAAA")
		self.assertEqual(self.limiter.acquisitions, [("spaces/AAAA", ratelimit.UPLOAD_WRITE_COST_MS)])

	def test_a_full_project_bucket_defers_without_spending_an_attempt(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		STATE["settings"]["project_message_writes_per_minute"] = 0

		outbound.drain_room("ROOM-0001")

		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Pending")
		self.assertEqual(job["attempts"], 0)
		self.assertEqual(self.fake.calls, [])
		self.assertEqual(seams.counters()["quota_backoffs"], 1)


# --------------------------------------------------------------------------------------
# Attachments on the create path — the wiring, the reservation, and the failure direction
# --------------------------------------------------------------------------------------

#: Bytes, deliberately not valid UTF-8, so a path that treats an attachment as text corrupts
#: it visibly instead of round-tripping by luck.
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\xff\xfe\x00\x01"
PDF_BYTES = b"%PDF-1.7\n\xff\xd8trailer\n"


class _UploadFailsOnNth:
	"""The fake transport, with the *n*th ``media.upload`` refused. Everything else passes through.

	A per-call fault rather than :meth:`FakeChatAPI.fail_with_server_error`, because the harness
	arms faults per ``google_method`` and consumes them in order — which fails the **first**
	upload. What has to be proved here is the harder direction: an upload failing *after* one
	has already succeeded, which is where a naive loop either loses the earlier token or aborts
	the message.

	The refusal is a 403, built by the harness's own :meth:`FakeChatAPI._error` so the AIP-193
	envelope stays byte-shaped like the real one. 403 is also non-retryable in
	``gchat.backoff.classify_error``, so the client's internal retry loop does not turn one
	injected fault into three and the test stays about the relay rather than about backoff.
	"""

	def __init__(self, fake: Any, *, fail_upload_number: int) -> None:
		self.fake = fake
		self.fail_upload_number = int(fail_upload_number)
		self.uploads = 0
		#: One label per request, in order. The ordering assertion reads this rather than the
		#: fake's journal, because an intercepted upload never reaches the fake to be journalled
		#: — and "the failed upload was still attempted before the create" is the claim.
		self.seen: list[str] = []
		self.create_body: dict[str, Any] | None = None

	def __getattr__(self, name: str) -> Any:
		return getattr(self.fake, name)

	def request(self, method: str, url: str, **kwargs: Any) -> Any:
		path = str(url)
		if str(method).upper() == "POST" and "/attachments:upload" in path:
			self.uploads += 1
			self.seen.append("upload")
			if self.uploads == self.fail_upload_number:
				return self.fake._error(
					403,
					"PERMISSION_DENIED",
					"injected: the caller may not upload to this space",
					google_method="media.upload",
				)
		elif str(method).upper() == "POST" and path.endswith("/messages"):
			self.seen.append("create")
			self.create_body = dict(kwargs.get("json") or {})
		else:
			self.seen.append("other")
		return self.fake.request(method, url, **kwargs)


class TestOutboundAttachments(RelayTestCase):
	"""``media.upload`` is wired into the create, charged, ordered, and survivable.

	``space_write_quota`` is **off** for this class, and the reason is worth stating rather
	than discovering. The fake enforces Google's *server-side* one-write-per-second bucket; the
	relay deliberately does **not** pace its own upload/create burst against it — it takes one
	client-side reservation covering the whole burst (``charge_attachment_write``) and lets the
	client's retry loop absorb whatever 429s Google returns inside that second. Leaving the
	server bucket on here would make this a second copy of chaos 9's backoff test with the
	wiring incidental. What is under test is the reservation, the ordering, and the failure
	direction.
	"""

	space_write_quota = False

	def seed_two_attachments(self, message: str = "MSG-0001") -> None:
		"""Two private ``File`` rows on the message, both comfortably under every ceiling."""
		for index, (file_name, payload) in enumerate(
			(("site-photo.png", PNG_BYTES), ("roof-plan.pdf", PDF_BYTES)), start=1
		):
			self.db.insert(
				"File",
				{
					"name": f"FILE-{index}",
					"file_name": file_name,
					"file_size": len(payload),
					"is_private": 1,
					"attached_to_doctype": "Chat Message",
					"attached_to_name": message,
					"_content": payload,
				},
			)

	def use_transport(self, transport: Any) -> None:
		"""Rebuild the relay's client seam over ``transport`` instead of the bare fake."""
		from erpnext_enhancements.chat.gchat.client import AuthIdentity, GoogleChatClient
		from erpnext_enhancements.chat.testing.fake_chat import FakeChatSettings

		settings = FakeChatSettings()

		def _build_client(subject: str, *, identity: str = "USER", correlation_id: str = "") -> Any:
			return GoogleChatClient(
				subject=subject,
				identity=AuthIdentity.USER,
				dry_run=False,
				settings=settings,
				token_provider=self.fake.token_provider,
				transport=transport,
				correlation_id=correlation_id or None,
			)

		outbound.build_client = _build_client

	def attachment_rows(self) -> dict[str, dict[str, Any]]:
		return {str(row.get("file") or ""): dict(row) for row in self.db.rows("Chat Attachment")}

	def test_two_attachments_upload_before_the_create_and_one_failure_still_relays(self) -> None:
		"""The regression test for the whole finding, in one run.

		Before the fix ``media.upload`` was charged to no bucket at all, ``upload_cost_ms``,
		``OutboundPlan.cost_ms`` and ``charge_attachment_write`` had no callers anywhere, and no
		handler ever uploaded — so a message with attachments relayed its text and silently lost
		its files. Four separate claims, because each one failed differently:

		1. **The space bucket is charged ``(n + 1)`` seconds, once, up front.** Two attachments
		   cost three seconds — the two uploads and the create — reserved in a single acquire so
		   another worker cannot slip a message write into the middle and leave a half-uploaded
		   message sitting in the bucket's way.
		2. **Uploads happen before the create.** A Chat message cannot reference an attachment
		   that has not been uploaded; the ``attachmentUploadToken`` only exists once
		   ``media.upload`` has answered.
		3. **A failure after a success loses one file, not the message.** The second upload 403s;
		   the first token is still referenced, the text still relays, and the two rows land on
		   opposite ``ingest_state``s so an operator can see exactly which file did not go.
		4. **The recipient is told.** A file Chat did not get is named in the message text,
		   because ``Chat Attachment.skip_reason`` is visible only to somebody already looking at
		   the ERPNext row — which is nobody, at the moment it matters.
		"""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="here is the survey")
		self.seed_two_attachments()
		self.make_job(name="JOB-1", job_seq=1)
		STATE["settings"]["mirror_attachments"] = 1

		transport = _UploadFailsOnNth(self.fake, fail_upload_number=2)
		self.use_transport(transport)

		summary = outbound.drain_room("ROOM-0001")

		# 1 — one reservation, three seconds, taken before any upload went out.
		self.assertEqual(summary["done"], 1)
		self.assertEqual(self.limiter.acquisitions, [(space, 3 * ratelimit.SPACE_WRITE_COST_MS)])
		self.assertEqual(
			self.limiter.acquisitions[0][1],
			outbound.attachments.upload_cost_ms(2),
			"the reservation must come from upload_cost_ms, not a third copy of the arithmetic",
		)
		# The attachment writes are their own per-project bucket, not the message one.
		self.assertIn(ratelimit.BUCKET_ATTACHMENT_WRITES, self.quota.charges)
		self.assertIn(ratelimit.BUCKET_MESSAGE_WRITES, self.quota.charges)

		# 2 — both uploads were attempted, and both before the create.
		self.assertEqual(transport.seen, ["upload", "upload", "create"])
		self.assertEqual(transport.uploads, 2)

		# 3 — the message went, carrying exactly the one token that survived.
		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.alerts(outbound.ALERT_DEAD_LETTER), [])
		parts = (transport.create_body or {}).get("attachment") or []
		self.assertEqual(len(parts), 1, "the create must reference the upload that succeeded")
		self.assertTrue(parts[0]["attachmentDataRef"]["attachmentUploadToken"])

		rows = self.attachment_rows()
		self.assertEqual(rows["FILE-1"]["ingest_state"], "Stored")
		self.assertEqual(rows["FILE-2"]["ingest_state"], "Failed")
		self.assertIn("PERMISSION_DENIED", rows["FILE-2"]["skip_reason"])
		self.assertNotIn("Bearer", rows["FILE-2"]["skip_reason"])

		# 4 — the text is intact and names the file the recipient will not see.
		relayed = self.relayed_texts(space)[0]
		self.assertIn("here is the survey", relayed)
		self.assertIn("roof-plan.pdf", relayed)

	def test_mirroring_off_relays_the_text_and_charges_one_second(self) -> None:
		"""The flag is a policy switch, not a broken path: no uploads, no attachment bucket."""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="text only")
		self.seed_two_attachments()
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.limiter.acquisitions, [(space, ratelimit.SPACE_WRITE_COST_MS)])
		self.assertNotIn(ratelimit.BUCKET_ATTACHMENT_WRITES, self.quota.charges)
		self.assertEqual([call.google_method for call in self.fake.calls], ["spaces.messages.create"])
		self.assertEqual(self.relayed_texts(space), ["text only"])

	def test_citation_markers_are_flattened_for_google(self) -> None:
		"""Google Chat has no manifest and no chip, so it showed ``[[ref:25]]`` verbatim.

		Production rendered a real answer as *"invocation errors [[ref:25]], [[ref:12]]"* in the
		space while the same message showed clickable ``[25]`` chips in ERPNext. The raw marker
		reads as a bug in the bot rather than as a citation.
		"""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="hit errors [[ref:25]], [[ref: 12 ]] earlier")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.relayed_texts(space), ["hit errors [25], [12] earlier"])

	def test_the_stored_text_keeps_its_markers(self) -> None:
		"""Flattened at the relay boundary and nowhere else.

		The SPA places its chips from these markers, so a transform that reached storage would
		fix Google Chat by breaking ERPNext.
		"""
		self.make_room()
		self.make_message(name="MSG-0001", text="hit errors [[ref:25]] earlier")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		stored = self.db.get_value("Chat Message", "MSG-0001", ["text", "text_plain"], as_dict=True)
		self.assertIn("[[ref:25]]", str(stored["text"]))
		self.assertIn("[[ref:25]]", str(stored["text_plain"]))

	def test_the_relay_learns_the_author_google_user_id(self) -> None:
		"""The one fact inbound is missing, on a response the relay already has.

		Google's inbound message carries ``sender.name = users/{id}`` and **no email**, so
		``_sender_identity`` maps the id back through ``Chat Room Member.gchat_membership_name``
		— whose member segment *is* that id. Rooms created by ``spaces.setup`` never learn those
		names, so every message a real coworker sent from Google Chat was stored against
		``chat-user-{id}@unresolved.invalid`` and flagged EXTERNAL.

		This write is made under DWD **as** that human, so the sender on the response is
		definitionally their identity. No matching, no display-name guess, no second call.
		"""
		space = self.make_room()
		self.db.set_value("Chat Room Member", "ROOM-0001-M0", {"gchat_membership_name": ""})
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		learned = self.db.get_value("Chat Room Member", "ROOM-0001-M0", "gchat_membership_name")
		self.assertTrue(learned, "the relay must bind the author's Google id")
		self.assertTrue(str(learned).startswith(f"{space}/members/"))
		# The id has to be the one Google reported, not one we invented. `FakeCall` is body-free
		# by construction, so this asserts against the harness's own deterministic mapping —
		# which is numeric and opaque precisely so a test cannot pass by matching an email.
		from erpnext_enhancements.chat.testing.fake_chat import _user_id_for

		self.assertEqual(str(learned), f"{space}/members/{_user_id_for(ALICE)}")

	def _ctx(self, *, subject: str, identity: str = "USER", space: str = "spaces/S") -> Any:
		return outbound._MessageContext(
			message="MSG-0001",
			room="ROOM-0001",
			space=space,
			client_message_id="client-x",
			text="hi",
			truncated=False,
			stored_google_name="",
			subject=subject,
			identity=identity,
			request_id="req-1",
		)

	def test_an_app_identity_relay_binds_nobody(self) -> None:
		"""The bot is not a ``Chat Room Member`` and has no Google user to learn.

		Driven directly rather than through the harness: an APP relay reaches here with **no
		subject at all**, because ``build_client`` refuses an APP client that was given one — so
		the empty-subject guard is the only thing standing between "the bot replied" and a
		binding written against whichever member row happened to match.
		"""
		self.make_room()
		outbound._learn_member_id(
			self._ctx(subject="", identity="APP"),
			{"sender": {"name": "users/999"}},
		)
		self.assertFalse(
			self.db.get_value("Chat Room Member", "ROOM-0001-M0", "gchat_membership_name")
		)

	def test_a_response_with_no_sender_binds_nothing(self) -> None:
		"""Google not telling us who sent it is not a licence to guess."""
		self.make_room()
		outbound._learn_member_id(self._ctx(subject=ALICE), {})
		self.assertFalse(
			self.db.get_value("Chat Room Member", "ROOM-0001-M0", "gchat_membership_name")
		)

	def test_an_already_bound_member_is_never_overwritten(self) -> None:
		"""A bound row is a fact something already established, and a relay does not revise it."""
		self.make_room()
		self.db.set_value(
			"Chat Room Member", "ROOM-0001-M0", {"gchat_membership_name": "spaces/S/members/999"}
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(
			self.db.get_value("Chat Room Member", "ROOM-0001-M0", "gchat_membership_name"),
			"spaces/S/members/999",
		)

	def test_a_standalone_attachment_upload_job_is_skipped_not_dead_lettered(self) -> None:
		"""The chosen design registers a handler rather than leaving the operation undispatched.

		An unregistered operation raises inside ``execute_claimed_job``, classifies
		non-retryable, dead-letters and alerts — an alarm about a design decision rather than
		about anything wrong. ``Skipped`` with the reason on the row is the honest answer.
		"""
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1, operation="Attachment Upload")

		outbound.drain_room("ROOM-0001")

		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Skipped")
		self.assertIn("Message Create", job["last_error"])
		self.assertEqual(self.alerts(outbound.ALERT_DEAD_LETTER), [])
		self.assertEqual(self.fake.calls, [])


# --------------------------------------------------------------------------------------
# Chaos 5 — a delete arriving while the create is In Progress
# --------------------------------------------------------------------------------------


class TestChaos5DeleteDuringCreate(RelayTestCase):
	def test_a_delete_is_deferred_while_its_create_holds_the_lease(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001", text="doomed")
		self.make_job(name="JOB-1", job_seq=1, operation="Message Create")
		self.make_job(name="JOB-2", job_seq=2, operation="Message Delete")

		# Claim the create and leave it in flight, exactly as a worker mid-HTTP-call would.
		claimed = outbound.claim_next_job("ROOM-0001")
		self.assertEqual(claimed["name"], "JOB-1")
		self.assertEqual(self.job("JOB-1")["status"], "In Progress")

		# A second worker woken for the delete must DEFER it, never fail it and never run it.
		result = outbound.run_relay_job("JOB-2")

		self.assertEqual(self.job("JOB-2")["status"], "Pending")
		self.assertIn("CREATE-BEFORE-EDIT", self.job("JOB-2")["last_error"])
		self.assertEqual(self.job("JOB-2")["attempts"], 0)
		self.assertEqual(result["claimed"], 0)
		self.assertEqual(self.fake.calls, [])

	def test_the_pair_relays_in_order_once_the_create_completes(self) -> None:
		space = self.make_room()
		self.make_message(name="MSG-0001", text="doomed")
		self.make_job(name="JOB-1", job_seq=1, operation="Message Create")
		self.make_job(name="JOB-2", job_seq=2, operation="Message Delete")

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.job("JOB-2")["status"], "Done")
		methods = [call.google_method for call in self.fake.calls]
		self.assertEqual(methods, ["spaces.messages.create", "spaces.messages.delete"], "FIFO order violated")
		self.assertEqual(self.fake.messages_in(space), [], "the message should be tombstoned")


# --------------------------------------------------------------------------------------
# Chaos 4 — timeout then success must produce exactly ONE Chat message
# --------------------------------------------------------------------------------------


class TestChaos4TimeoutThenSuccess(RelayTestCase):
	#: Each request costs a second of wall clock, so the client's own internal retry is not
	#: refused by the fake's server-side 1-write/second bucket. That is what a real slow
	#: request looks like and it keeps the test about idempotency rather than about quota.
	request_latency_ms = 1000

	def test_a_read_timeout_after_processing_does_not_post_a_second_copy(self) -> None:
		"""The server did the work and the answer was lost. Both idempotency keys exist for
		exactly this, and ``requestId`` replay is what makes the retry find the original."""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="only once")
		self.make_job(name="JOB-1", job_seq=1)
		self.fake.fail_with_timeout("spaces.messages.create", times=1, after_processing=True)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.relayed_texts(space), ["only once"], "the retry duplicated the message")
		self.assertTrue(self.message_row("MSG-0001")["gchat_message_name"])

	def test_a_timeout_that_exhausts_the_client_still_relays_once_after_the_sweeper(self) -> None:
		"""Three timeouts kill the client's internal budget; the job fails, backs off, and the
		next drain finds the original through ``requestId`` rather than creating a twin."""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="only once")
		self.make_job(name="JOB-1", job_seq=1)
		self.fake.fail_with_timeout("spaces.messages.create", times=3, after_processing=True)

		outbound.drain_room("ROOM-0001")
		self.assertEqual(self.job("JOB-1")["status"], "Pending")
		self.assertEqual(self.job("JOB-1")["attempts"], 1)

		self.clock.advance_seconds(30)
		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.relayed_texts(space), ["only once"])


# --------------------------------------------------------------------------------------
# Chaos 7 — ten messages in two seconds
# --------------------------------------------------------------------------------------


class TestChaos7Burst(RelayTestCase):
	def test_ten_messages_in_two_seconds_all_arrive_in_order(self) -> None:
		"""**Not eight successes and two exceptions.** The bucket is a pacing device, and a
		refusal is a wait, never a failure — the whole burst arrives, in ``job_seq`` order,
		spread at one write per second."""
		space = self.make_room()
		for index in range(1, 11):
			self.make_message(name=f"MSG-{index:04d}", text=f"burst {index}", seq=index)
			self.make_job(name=f"JOB-{index}", job_seq=index, message=f"MSG-{index:04d}")
			# Ten messages typed inside two seconds.
			self.clock.advance(200)

		summary = outbound.drain_room("ROOM-0001")

		self.assertEqual(summary["done"], 10)
		self.assertEqual(summary["failed"], 0)
		self.assertEqual(summary["deferred"], 0)
		self.assertEqual(self.relayed_texts(space), [f"burst {index}" for index in range(1, 11)])
		for index in range(1, 11):
			self.assertEqual(self.job(f"JOB-{index}")["status"], "Done")
		# Nine of the ten had to wait for the space's next second.
		self.assertGreaterEqual(self.limiter.waits_ms, 9 * 1000 - 2000)
		self.assertEqual(seams.counters()["messages_relayed"], 10)

	def test_hitting_the_run_bound_re_enqueues_rather_than_stranding_the_rest(self) -> None:
		"""RQ kills a short-queue job at 300s and a deploy kills it whenever it likes, so the
		worker stops early and wakes a successor. Nothing may be left with nobody coming."""
		space = self.make_room()
		for index in range(1, 6):
			self.make_message(name=f"MSG-{index:04d}", text=f"m{index}", seq=index)
			self.make_job(name=f"JOB-{index}", job_seq=index, message=f"MSG-{index:04d}")

		summary = outbound.drain_room("ROOM-0001", max_jobs=2)

		self.assertEqual(summary["stopped"], "job count bound")
		self.assertEqual(self.relayed_texts(space), ["m1", "m2"])
		self.assertEqual([entry["room"] for entry in STATE["enqueued"]], ["ROOM-0001"])

	def test_nine_redundant_wakeups_do_not_produce_nine_workers(self) -> None:
		"""Ten enqueues wake ten workers; the claim is atomic, so one drains and nine leave."""
		self.make_room()
		for index in range(1, 4):
			self.make_message(name=f"MSG-{index:04d}", text=f"burst {index}", seq=index)
			self.make_job(name=f"JOB-{index}", job_seq=index, message=f"MSG-{index:04d}")

		first = outbound.claim_next_job("ROOM-0001")
		self.assertEqual(first["name"], "JOB-1")
		self.assertIsNone(outbound.claim_next_job("ROOM-0001"))


# --------------------------------------------------------------------------------------
# Chaos 8 — worker SIGKILL mid-relay
# --------------------------------------------------------------------------------------


class TestChaos8CrashedWorker(RelayTestCase):
	request_latency_ms = 1000

	def test_an_expired_lease_is_reaped_alerted_and_counted(self) -> None:
		"""A SIGKILL runs no ``finally``. Cleanup is by lease expiry or it does not happen."""
		self.make_room()
		self.make_message(name="MSG-0001", text="survivor")
		self.make_job(name="JOB-1", job_seq=1)

		claimed = outbound.claim_next_job("ROOM-0001")
		self.assertIsNotNone(claimed["lease_expires_at"])
		# ... and the process dies here. Nothing else runs.

		self.clock.advance_seconds(outbound.LEASE_MAX_SECONDS + 60)
		summary = outbound.sweep_relay_jobs()

		self.assertEqual(summary["leases_reaped"], 1)
		self.assertTrue(self.alerts(outbound.ALERT_CRASHED_WORKER))
		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Pending")
		self.assertEqual(job["attempts"], 1, "a crashed attempt must be counted or it loops forever")
		self.assertIsNone(job["lease_expires_at"])

	def test_the_reaped_job_relays_exactly_once_on_the_retry(self) -> None:
		space = self.make_room()
		self.make_message(name="MSG-0001", text="survivor")
		self.make_job(name="JOB-1", job_seq=1)

		claimed = outbound.claim_next_job("ROOM-0001")
		# The worker got as far as posting before it died: the message exists at Google and
		# the response never came back.
		outbound.build_client(ALICE).create_message(
			space,
			self.message_row("MSG-0001")["client_message_id"],
			claimed["request_id"],
			"survivor",
		)

		self.clock.advance_seconds(outbound.LEASE_MAX_SECONDS + 60)
		outbound.sweep_relay_jobs()
		self.clock.advance_seconds(30)
		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.relayed_texts(space), ["survivor"], "the reap duplicated the message")

	def test_a_live_lease_is_not_reaped(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		outbound.claim_next_job("ROOM-0001")

		summary = outbound.sweep_relay_jobs()

		self.assertEqual(summary["leases_reaped"], 0)
		self.assertEqual(self.job("JOB-1")["status"], "In Progress")


# --------------------------------------------------------------------------------------
# Chaos 9 — 429 for a sustained period, then recovery
# --------------------------------------------------------------------------------------


class TestChaos9RateLimitedThenRecovers(RelayTestCase):
	def test_a_sustained_429_backs_off_and_then_delivers_exactly_once(self) -> None:
		"""Google's own caveats say staying under one write per second does not guarantee no
		429, so backoff — not the bucket — is the correctness mechanism."""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="eventually")
		self.make_job(name="JOB-1", job_seq=1)
		# Enough forced 429s to exhaust the client's internal budget twice over.
		self.fake.fail_with_rate_limit("spaces.messages.create", times=6)

		outbound.drain_room("ROOM-0001")
		self.assertEqual(self.job("JOB-1")["status"], "Pending")
		self.assertEqual(self.job("JOB-1")["attempts"], 1)
		self.assertEqual(self.job("JOB-1")["http_status"], 429)
		self.assertEqual(self.job("JOB-1")["google_error_status"], "RESOURCE_EXHAUSTED")

		self.clock.advance_seconds(30)
		outbound.drain_room("ROOM-0001")
		self.assertEqual(self.job("JOB-1")["status"], "Pending")
		self.assertEqual(self.job("JOB-1")["attempts"], 2)

		self.clock.advance_seconds(30)
		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.relayed_texts(space), ["eventually"])
		self.assertEqual(seams.counters()["retries"], 2)
		self.assertEqual(seams.counters()["dead_letters"], 0)

	def test_backoff_is_deterministic_so_the_sweeper_is_testable(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		self.fake.fail_with_rate_limit("spaces.messages.create", times=6)

		before = _now()
		outbound.drain_room("ROOM-0001")

		# attempts = 1 -> base seconds exactly, no jitter. The jittered sleep lives inside one
		# HTTP call; this is the scheduling delay between job attempts.
		expected = before + timedelta(seconds=STATE["settings"]["relay_initial_backoff_seconds"])
		self.assertGreaterEqual(self.job("JOB-1")["available_at"], expected)


# --------------------------------------------------------------------------------------
# Chaos 17 — the kill switch, toggled mid-burst
# --------------------------------------------------------------------------------------


class TestChaos17KillSwitch(RelayTestCase):
	def test_pausing_mid_burst_stops_writes_and_drops_nothing(self) -> None:
		space = self.make_room()
		for index in range(1, 6):
			self.make_message(name=f"MSG-{index:04d}", text=f"m{index}", seq=index)
			self.make_job(name=f"JOB-{index}", job_seq=index, message=f"MSG-{index:04d}")

		# Two relayed, then the operator flips the switch.
		outbound.drain_room("ROOM-0001", max_jobs=2)
		STATE["settings"]["pause_outbound"] = 1
		summary = outbound.drain_room("ROOM-0001")

		self.assertIn("paused", summary["stopped"])
		self.assertEqual(summary["claimed"], 0)
		self.assertEqual(self.relayed_texts(space), ["m1", "m2"])
		for index in range(3, 6):
			self.assertEqual(self.job(f"JOB-{index}")["status"], "Pending", "a paused relay dropped a job")

	def test_re_enabling_drains_the_backlog_in_job_seq_order(self) -> None:
		space = self.make_room()
		for index in range(1, 6):
			self.make_message(name=f"MSG-{index:04d}", text=f"m{index}", seq=index)
			self.make_job(name=f"JOB-{index}", job_seq=index, message=f"MSG-{index:04d}")

		STATE["settings"]["pause_outbound"] = 1
		outbound.drain_room("ROOM-0001")
		self.assertEqual(self.fake.calls, [])

		STATE["settings"]["pause_outbound"] = 0
		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.relayed_texts(space), ["m1", "m2", "m3", "m4", "m5"])

	def test_the_feature_flag_is_the_same_gate_as_the_incident_lever(self) -> None:
		STATE["settings"]["relay_outbound_enabled"] = 0
		self.assertIn("relay_outbound_enabled", outbound.outbound_pause_reason())
		STATE["settings"]["relay_outbound_enabled"] = 1
		STATE["settings"]["pause_outbound"] = 1
		self.assertIn("pause_outbound", outbound.outbound_pause_reason())

	def test_the_sweeper_enqueues_nothing_while_paused(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		STATE["settings"]["pause_outbound"] = 1

		summary = outbound.sweep_relay_jobs()

		self.assertEqual(summary["rooms_enqueued"], 0)
		self.assertEqual(STATE["enqueued"], [])
		self.assertEqual(self.job("JOB-1")["status"], "Pending")


# --------------------------------------------------------------------------------------
# Reconciliation, dead letters, the breaker and the manual retry
# --------------------------------------------------------------------------------------


class TestReconciliation(RelayTestCase):
	request_latency_ms = 1000

	def test_a_name_already_bound_by_inbound_is_silent_success(self) -> None:
		"""§4.D: the Workspace Event can be fetched and matched before our own response
		returns. Same value ⇒ nothing to do, and above all no alert."""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="raced")
		self.make_job(name="JOB-1", job_seq=1)

		client_id = self.message_row("MSG-0001")["client_message_id"]
		self.fake.race_on_create(
			space,
			during=lambda fake: self.db.set_value(
				"Chat Message",
				"MSG-0001",
				"gchat_message_name",
				fake.message(f"{space}/messages/{client_id}")["name"],
				update_modified=False,
			),
		)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertEqual(self.alerts(outbound.ALERT_DUPLICATE_MESSAGE), [])
		self.assertEqual(len(self.fake.messages_in(space)), 1)

	def test_a_different_bound_name_is_a_real_duplicate_and_the_new_one_is_deleted(self) -> None:
		space = self.make_room()
		self.make_message(name="MSG-0001", text="twice")
		# Something already bound a DIFFERENT Google message to this row.
		other = self.fake.seed_message(space, text="twice", sender=ALICE)
		self.db.set_value("Chat Message", "MSG-0001", "gchat_message_name", other, update_modified=False)
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done")
		self.assertTrue(self.alerts(outbound.ALERT_DUPLICATE_MESSAGE))
		# The bound one survives (everything downstream already points at it); the one this
		# job created is gone.
		surviving = [message["name"] for message in self.fake.messages_in(space)]
		self.assertEqual(surviving, [other])
		self.assertEqual(self.message_row("MSG-0001")["gchat_message_name"], other)

	def test_rule_2_a_unique_collision_on_the_resource_name_is_success(self) -> None:
		"""``gchat_message_name`` is a plain unique index, so a collision raises
		``UniqueValidationError`` and **not** ``DuplicateEntryError`` — they share no base
		beyond ``Exception``. Catching only the ADR's one would miss every collision."""
		space = self.make_room()
		self.make_message(name="MSG-0001", text="mine", seq=1)
		self.make_message(name="MSG-0002", text="theirs", seq=2)
		self.make_job(name="JOB-1", job_seq=1, message="MSG-0001")

		# A different ERPNext row already holds the name our create is about to be given.
		def _steal(fake: Any) -> None:
			client_id = self.message_row("MSG-0001")["client_message_id"]
			self.db.set_value(
				"Chat Message",
				"MSG-0002",
				"gchat_message_name",
				fake.message(f"{space}/messages/{client_id}")["name"],
				update_modified=False,
			)

		self.fake.race_on_create(space, during=_steal)
		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Done", "Rule 2 must be success, not failure")
		self.assertEqual(seams.counters()["duplicates_rejected"], 1)


class TestDeadLetter(RelayTestCase):
	def test_a_non_retryable_failure_dead_letters_immediately_with_context(self) -> None:
		"""403/404/400 go straight to ``Dead``. Retrying converts a fast legible failure into
		a slow confusing one, and parks the row where the sweeper picks it up again."""
		self.make_room()
		self.make_message(name="MSG-0001", text="doomed")
		self.make_job(name="JOB-1", job_seq=1)
		self.fake.fail_with_server_error("spaces.messages.create", times=1, status=403)

		outbound.drain_room("ROOM-0001")

		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Dead")
		self.assertEqual(job["attempts"], 1)
		alerts = self.alerts(outbound.ALERT_DEAD_LETTER)
		self.assertTrue(alerts)
		self.assertIn("job_seq", alerts[0]["message"])
		self.assertNotIn("doomed", alerts[0]["message"], "a message body reached the Error Log")
		self.assertNotIn("Bearer", alerts[0]["message"])
		self.assertEqual(seams.counters()["dead_letters"], 1)

	def test_a_dead_mirror_never_loses_the_message(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001", text="still readable")
		self.make_job(name="JOB-1", job_seq=1)
		self.fake.fail_with_server_error("spaces.messages.create", times=1, status=400)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Dead")
		message = self.message_row("MSG-0001")
		self.assertEqual(message["text"], "still readable")
		self.assertEqual(message["is_deleted"], 0)
		self.assertEqual(message["sync_state"], "Failed")

	def test_the_retry_budget_dead_letters_after_relay_max_attempts(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		STATE["settings"]["relay_max_attempts"] = 2

		for _ in range(2):
			self.fake.fail_with_server_error("spaces.messages.create", times=9, status=503)
			outbound.drain_room("ROOM-0001")
			self.clock.advance_seconds(60)

		self.assertEqual(self.job("JOB-1")["status"], "Dead")
		self.assertEqual(self.job("JOB-1")["attempts"], 2)


class TestCircuitBreaker(RelayTestCase):
	def test_consecutive_failures_open_the_breaker_and_stop_burning_quota(self) -> None:
		self.make_room()
		STATE["settings"]["circuit_breaker_threshold"] = 2
		STATE["settings"]["relay_max_attempts"] = 1
		for index in range(1, 4):
			self.make_message(name=f"MSG-{index:04d}", seq=index)
			self.make_job(name=f"JOB-{index}", job_seq=index, message=f"MSG-{index:04d}")
		self.fake.fail_with_server_error("spaces.messages.create", times=9, status=503)

		outbound.drain_room("ROOM-0001")
		outbound.drain_room("ROOM-0001")
		calls_after_two = len(self.fake.calls)

		summary = outbound.drain_room("ROOM-0001")

		self.assertEqual(summary["stopped"], "circuit breaker open")
		self.assertEqual(len(self.fake.calls), calls_after_two, "the breaker did not stop the writes")
		self.assertTrue(self.alerts(outbound.ALERT_CIRCUIT_OPEN))

	def test_a_success_closes_the_breaker(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		STATE["cache"].store[outbound._CIRCUIT_FAILURES_KEY] = 5

		outbound.drain_room("ROOM-0001")

		self.assertEqual(int(STATE["cache"].store[outbound._CIRCUIT_FAILURES_KEY]), 0)

	def test_a_local_bug_does_not_open_the_breaker(self) -> None:
		"""The breaker protects Google's quota during a *Google* outage. Counting our own
		``ValueError`` would open it on a code defect and hide the defect behind a cooldown."""
		self.make_room()
		self.make_message(name="MSG-0001")
		self.db.set_value("Chat Message", "MSG-0001", "client_message_id", "")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		self.assertEqual(self.job("JOB-1")["status"], "Dead")
		self.assertFalse(STATE["cache"].store.get(outbound._CIRCUIT_FAILURES_KEY))


class TestSweeper(RelayTestCase):
	def test_it_wakes_one_worker_per_room_with_due_work(self) -> None:
		self.make_room()
		self.make_room(name="ROOM-0002", members=(BOB,))
		self.make_message(name="MSG-0001")
		self.make_message(name="MSG-0002", room="ROOM-0002", sender=BOB)
		self.make_job(name="JOB-1", job_seq=1)
		self.make_job(name="JOB-2", job_seq=1, room="ROOM-0002", message="MSG-0002", impersonate_user=BOB)

		summary = outbound.sweep_relay_jobs()

		self.assertEqual(summary["rooms_enqueued"], 2)
		rooms = sorted(entry["room"] for entry in STATE["enqueued"])
		self.assertEqual(rooms, ["ROOM-0001", "ROOM-0002"])
		for entry in STATE["enqueued"]:
			self.assertIs(entry["enqueue_after_commit"], True)

	def test_a_deferred_job_is_not_woken_before_it_is_due(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1, available_at=_now() + timedelta(seconds=120))

		summary = outbound.sweep_relay_jobs()
		self.assertEqual(summary["rooms_enqueued"], 0)

	def test_a_stranded_failed_row_is_routed_rather_than_left(self) -> None:
		"""A worker that died *between* recording the failure and routing it leaves a row in
		the one state nothing else looks at."""
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1, status="Failed", attempts=1)

		summary = outbound.sweep_relay_jobs()

		self.assertEqual(summary["failed_routed"], 1)
		self.assertEqual(self.job("JOB-1")["status"], "Pending")

	def test_a_room_whose_provisioning_failed_is_skipped_not_deferred_forever(self) -> None:
		"""\"Not yet\" and \"never\" are different answers, and only one of them is a wait.

		``sweep_pending_provisioning`` selects only ``Pending`` and ``Provisioning``, so
		``Failed`` is terminal — no space will ever appear. Deferring against it is a promise
		the system cannot keep: the job waits, the sweeper re-defers it every pass, and every
		later message in the room queues behind it under Rule 1, forever.

		Production hit this within an hour of the DM feature being used. The message carried
		"provisioning has not completed", which reads as *in progress*. It had completed.
		"""
		self.db.insert(
			"Chat Room",
			{
				"name": "ROOM-0001",
				"gchat_space_name": "",
				"provisioning_state": "Failed",
				"provisioning_error": "spaces.setup returned HTTP 400 INVALID_ARGUMENT",
			},
		)
		self.db.insert(
			"Chat Room Member",
			{"name": "M1", "room": "ROOM-0001", "user": ALICE, "gchat_member_state": "JOINED"},
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Skipped", "a room that will never provision must not wait")
		# The room's own reason has to travel with the job, or the operator gets "skipped" and
		# no way to find out why.
		self.assertIn("INVALID_ARGUMENT", str(job["last_error"]))

	def test_a_room_still_provisioning_is_still_deferred(self) -> None:
		"""The distinction has to cut both ways or it is just a broken gate."""
		self.db.insert(
			"Chat Room",
			{"name": "ROOM-0001", "gchat_space_name": "", "provisioning_state": "Pending"},
		)
		self.db.insert(
			"Chat Room Member",
			{"name": "M1", "room": "ROOM-0001", "user": ALICE, "gchat_member_state": "JOINED"},
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")

		job = self.job("JOB-1")
		self.assertEqual(job["status"], "Pending")
		self.assertEqual(job["attempts"], 0, "a deferral is not an attempt")

	def test_a_job_blocked_on_provisioning_for_too_long_is_alerted(self) -> None:
		"""Driven through the real gate, not a hand-written ``last_error``.

		A room whose space has not appeared in half an hour is a room whose coworkers think
		they are talking to somebody. It is not a failure — the gate is doing its job — so it
		alerts and stays ``Pending`` with its retry budget intact.
		"""
		self.db.insert("Chat Room", {"name": "ROOM-0001", "gchat_space_name": ""})
		self.db.insert(
			"Chat Room Member",
			{"name": "M1", "room": "ROOM-0001", "user": ALICE, "gchat_member_state": "JOINED"},
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)

		outbound.drain_room("ROOM-0001")
		self.assertEqual(self.job("JOB-1")["attempts"], 0)

		self.db.set_value(
			"Chat Relay Job",
			"JOB-1",
			{"creation": _now() - timedelta(seconds=outbound.DEPENDENCY_STALE_SECONDS + 60)},
		)
		summary = outbound.sweep_relay_jobs()

		self.assertEqual(summary["dependency_stale"], 1)
		self.assertTrue(self.alerts(outbound.ALERT_DEPENDENCY_STALE))
		self.assertEqual(self.job("JOB-1")["status"], "Pending")

	def test_a_job_blocked_for_the_same_reason_is_not_re_alerted_every_pass(self) -> None:
		"""One blocked job, one alert — not one per sweep.

		Eleven jobs stuck behind a single head-of-line blocker wrote **305 identical Error Log
		rows in a day** on production. A real 403 from Google sat in the middle of them and
		took a query grouped by title to find. The count still rises every pass, because that
		is a measurement; the alert does not, because that is a notification.
		"""
		self.db.insert("Chat Room", {"name": "ROOM-0001", "gchat_space_name": ""})
		self.db.insert(
			"Chat Room Member",
			{"name": "M1", "room": "ROOM-0001", "user": ALICE, "gchat_member_state": "JOINED"},
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		outbound.drain_room("ROOM-0001")
		self.db.set_value(
			"Chat Relay Job",
			"JOB-1",
			{"creation": _now() - timedelta(seconds=outbound.DEPENDENCY_STALE_SECONDS + 60)},
		)

		first = outbound.sweep_relay_jobs()
		alerts_after_first = len(self.alerts(outbound.ALERT_DEPENDENCY_STALE))
		second = outbound.sweep_relay_jobs()
		alerts_after_second = len(self.alerts(outbound.ALERT_DEPENDENCY_STALE))

		self.assertEqual(alerts_after_first, 1)
		self.assertEqual(alerts_after_second, 1, "the second pass must not re-alert")
		# The counter is a measurement of what is blocked and must keep counting.
		self.assertEqual(first["dependency_stale"], 1)
		self.assertEqual(second["dependency_stale"], 1)

	def test_a_cache_that_cannot_answer_still_alerts(self) -> None:
		"""Fails **open**, and this is the one direction that is not a judgement call.

		A duplicate Error Log row costs noise. A swallowed one costs an outage nobody is told
		about. Every other dedupe in this module fails closed; this one must not, and the
		difference is worth a test rather than a comment.
		"""
		import frappe

		class _BrokenCache:
			def get_value(self, *args, **kwargs):
				raise RuntimeError("redis is down")

			def set_value(self, *args, **kwargs):
				raise RuntimeError("redis is down")

		saved = frappe.cache
		frappe.cache = lambda: _BrokenCache()
		try:
			self.assertTrue(outbound._stale_alert_is_new("JOB-1", "Deferred: anything"))
			self.assertTrue(outbound._stale_alert_is_new("JOB-1", "Deferred: anything"))
		finally:
			frappe.cache = saved

	def test_a_blocked_job_whose_reason_changes_alerts_again(self) -> None:
		"""The reason changing is the news.

		"waiting on provisioning" becoming "not a joined member" is a different fact about the
		world, and suppressing it would be the failure mode of every dedupe ever written.
		"""
		self.db.insert("Chat Room", {"name": "ROOM-0001", "gchat_space_name": ""})
		self.db.insert(
			"Chat Room Member",
			{"name": "M1", "room": "ROOM-0001", "user": ALICE, "gchat_member_state": "JOINED"},
		)
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		outbound.drain_room("ROOM-0001")
		self.db.set_value(
			"Chat Relay Job",
			"JOB-1",
			{"creation": _now() - timedelta(seconds=outbound.DEPENDENCY_STALE_SECONDS + 60)},
		)

		outbound.sweep_relay_jobs()
		self.assertEqual(len(self.alerts(outbound.ALERT_DEPENDENCY_STALE)), 1)

		self.db.set_value(
			"Chat Relay Job", "JOB-1", {"last_error": "Deferred: something else entirely"}
		)
		outbound.sweep_relay_jobs()
		self.assertEqual(len(self.alerts(outbound.ALERT_DEPENDENCY_STALE)), 2)


class TestManualRetry(RelayTestCase):
	def test_it_requires_system_manager(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1, status="Failed", attempts=3)
		STATE["roles"] = ["Chat User"]

		with self.assertRaises(_PermissionError):
			outbound.retry_relay_job("JOB-1")

	def test_it_re_enters_the_state_machine_and_never_calls_google(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1, status="Failed", attempts=3)

		result = outbound.retry_relay_job("JOB-1")

		self.assertEqual(result["status"], "Pending")
		job = self.job("JOB-1")
		self.assertEqual(job["attempts"], 0)
		self.assertEqual(self.fake.calls, [], "the retry button talked to Google directly")
		self.assertEqual(len(STATE["enqueued"]), 1)

	def test_a_dead_row_is_refused_because_job_seq_is_a_fifo_position(self) -> None:
		"""Reviving a stale ``job_seq`` would replay an edit before the create it edits."""
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1, status="Dead", attempts=3)

		with self.assertRaises(_ValidationError) as caught:
			outbound.retry_relay_job("JOB-1")
		self.assertIn("new job_seq", str(caught.exception))

	def test_a_live_claim_is_not_stolen_by_the_button(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		outbound.claim_next_job("ROOM-0001")

		with self.assertRaises(_ValidationError):
			outbound.retry_relay_job("JOB-1")

	def test_an_expired_claim_may_be_taken_back(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		outbound.claim_next_job("ROOM-0001")
		self.clock.advance_seconds(outbound.LEASE_MAX_SECONDS + 60)

		outbound.retry_relay_job("JOB-1")
		self.assertEqual(self.job("JOB-1")["status"], "Pending")


class TestTransitionGate(RelayTestCase):
	def test_every_status_write_goes_through_the_table(self) -> None:
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1, status="Done")
		job = outbound.load_job("JOB-1")

		from erpnext_enhancements.chat.sync.states import IllegalTransition, RelayState

		with self.assertRaises(IllegalTransition):
			outbound.transition(job, RelayState.PENDING)

	def test_in_progress_cannot_be_claimed_twice(self) -> None:
		"""One claim edge means the in-flight set — which is also the crashed-worker detector
		— is unambiguous."""
		self.make_room()
		self.make_message(name="MSG-0001")
		self.make_job(name="JOB-1", job_seq=1)
		claimed = outbound.claim_next_job("ROOM-0001")

		from erpnext_enhancements.chat.sync.states import IllegalTransition, RelayState

		with self.assertRaises(IllegalTransition):
			outbound.transition(claimed, RelayState.IN_PROGRESS)


if __name__ == "__main__":  # pragma: no cover
	unittest.main()


class TestAuthIdentity(RelayTestCase):
	"""Which Google identity the worker writes as, and the two guards that stop applying.

	This is the change that lets Triton reply without a Workspace licence, and every assertion
	here is about *not* doing something the USER path must always do. That asymmetry is the
	risk: a bug in this direction does not fail, it silently posts a coworker's message as the
	app — so each test names which side it is pinning.
	"""

	def test_a_missing_column_reads_as_the_human(self):
		"""Deployable over a queue that predates the column.

		Every job already in flight was written for the coworker mirror. Defaulting the other
		way would re-attribute a backlog of people's own messages to the app, silently.
		"""
		self.assertEqual(outbound._identity_of({}), "USER")
		self.assertEqual(outbound._identity_of({"auth_identity": ""}), "USER")

	def test_an_unrecognised_identity_stops_the_job(self):
		"""Not coerced to the default. A third state nobody handled should stop, not be guessed."""
		with self.assertRaises(ValueError) as caught:
			outbound._identity_of({"auth_identity": "SERVICE"})
		self.assertIn("SERVICE", str(caught.exception))

	def test_the_app_needs_no_author_and_the_human_still_does(self):
		app = {"auth_identity": "APP", "impersonate_user": ""}
		self.assertEqual(outbound._subject_for(app), "")

		with self.assertRaises(ValueError) as caught:
			outbound._subject_for({"auth_identity": "USER", "impersonate_user": ""})
		# The message has to send the reader to the writer, not to the auth layer.
		self.assertIn("bug in the writer", str(caught.exception))

	def test_the_app_is_not_required_to_be_a_joined_member(self):
		"""The behaviour change. A Chat app is installed in a space, not a member of it.

		There is no `Chat Room Member` row for the app and there never will be, so the
		membership guard would defer every Triton reply forever while reporting a sync that is
		not late for anybody — which is exactly what production showed.
		"""
		# No member row exists for this room at all; the USER path must still refuse.
		with self.assertRaises(outbound.Deferred):
			outbound._require_joined_author("room-1", "nobody@example.com", "USER")
		# And the APP path must not even look.
		self.assertIsNone(outbound._require_joined_author("room-1", "", "APP"))

	def test_an_app_client_is_never_given_a_subject(self):
		"""A subject that silently does nothing is how somebody later concludes the reply is
		attributed to a person. The app grant has no `sub` claim at all."""
		# `self._saved[0]` is the REAL `build_client`: this suite replaces it with the fake
		# harness in setUp, and asserting against the fake would assert nothing about the seam
		# the worker actually calls in production.
		real_build_client = self._saved[0]
		with self.assertRaises(ValueError) as caught:
			real_build_client("someone@example.com", identity="APP")
		self.assertIn("does not impersonate", str(caught.exception))
