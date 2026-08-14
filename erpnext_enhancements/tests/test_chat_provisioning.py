# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for space provisioning and membership sync (Phase 2 §4.H).

**Shape P: plain pytest functions.** It needs its own ``python -m pytest`` step in
``ci.yml``. ``python -m unittest`` collects zero function-style tests and reports success,
and this repo has already lost a suite that way for weeks, so the style is load-bearing
rather than a preference.

Why there is a Frappe stub here at all
--------------------------------------
``sync/provisioning.py`` and ``sync/membership.py`` import ``frappe`` at module scope —
they may, because neither is a ``doc_events`` target. The decisions inside them are pure
functions and are tested as such below, but the interesting failures of this feature are
**not** in the arithmetic: they are "the resume cursor skipped a unit", "the second run
created a second space", "the diff removed somebody it could not identify". Those only
exist once rows and a Google API are both in play, so this file supplies a small in-memory
document store and drives the **real** ``GoogleChatClient`` against ``FakeChatAPI``.

The store is deliberately strict where the database is strict. It enforces
``unique(gchat_space_name)`` and ``unique(linked_doctype, linked_document)``, because those
two indexes are what make provisioning idempotent, and a stub that let a duplicate through
would turn the most important test in this file green for the wrong reason.

What is NOT covered here, and where it lives instead
-----------------------------------------------------
* The transport, its retries and its dry-run branch — ``test_chat_gchat_client.py``.
* The fake's own fidelity to Google — ``test_chat_fake_api.py``.
* Permission behaviour — ``test_chat_permissions_bench.py`` (needs a bench). One assertion
  here is about permissions and it is a **source** assertion:
  :func:`test_left_seq_is_not_read_by_the_permission_layer` greps ``chat/permissions.py``,
  because CQ-10 is unanswered and the guarantee being defended is that nothing reads
  ``left_seq`` to grant anything.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import datetime
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest


def _real_frappe_is_installed() -> bool:
	"""Is an actual ``frappe`` package importable, as opposed to a stub another suite left?

	``find_spec`` answers from the import system, so a bare ``types.ModuleType`` in
	``sys.modules`` has no ``__spec__`` and reads as absent — which is the answer wanted.
	"""
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
# The in-memory document store
# ---------------------------------------------------------------------------


class StubValidationError(Exception):
	pass


class StubUniqueValidationError(StubValidationError):
	"""``frappe.UniqueValidationError`` — raised by **non-primary-key** unique indexes.

	Named and separated from ``DuplicateEntryError`` deliberately: PHASE2_VERIFIED.md §1.1
	establishes that they share no base beyond ``Exception``, and code that catches only the
	latter would not catch what ``unique(gchat_space_name)`` actually raises.
	"""


class StubDuplicateEntryError(Exception):
	"""``frappe.DuplicateEntryError`` — a duplicate **primary key** only."""


class StubDoesNotExistError(Exception):
	pass


class StubPermissionError(Exception):
	"""What ``frappe.only_for`` raises. Frappe answers 403 for it; here it just has to be
	distinguishable from a validation failure, so a refused *caller* cannot be mistaken for
	refused *input*."""


#: ``(doctype, tuple of fieldnames)`` the store enforces as unique, mirroring the indexes the
#: Phase 2 patch creates. Only enforced when every field in the tuple is non-empty, which is
#: MariaDB's own behaviour for a composite unique index containing a NULL.
UNIQUE_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
	("Chat Room", ("gchat_space_name",)),
	("Chat Room", ("linked_doctype", "linked_document")),
	("Chat Room Member", ("room", "user")),
)

#: DocField ``default`` values Frappe applies on insert, which a dict-backed stub otherwise
#: would not. Copied from the DocType JSONs. Not decoration: ``Chat Room.is_archived``
#: defaults to ``0`` and the orphan sweep filters on it, so a stub that left the column absent
#: would make the sweep find nothing and the test pass for the wrong reason in the other
#: direction — it would fail, but for a reason that has nothing to do with the feature.
DOCTYPE_DEFAULTS: dict[str, dict[str, Any]] = {
	"Chat Room": {
		"is_archived": 0,
		"retention_days": 0,
		"provisioning_state": "Pending",
		"provisioning_attempts": 0,
		"membership_authority": "Bidirectional",
		"external_users_allowed": 0,
		"mirror_whitelisted": 0,
		"seq_high_water": 0,
		"reverts_this_hour": 0,
	},
	"Chat Room Member": {
		"role": "Member",
		"is_active": 1,
		"derived_from_document": 0,
		"left_seq": 0,
		"last_read_seq": 0,
		"notification_mode": "All",
		"sync_state": "Pending",
		"gchat_membership_name": "",
		"gchat_member_state": "",
	},
	"Chat Provisioning Run": {
		"dry_run": 1,
		"status": "Pending",
		"cursor": "",
		"planned_count": 0,
		"created_count": 0,
		"skipped_count": 0,
		"failed_count": 0,
		"log": "",
	},
}


class Store:
	"""``{doctype: {name: row}}`` with just enough query surface for the modules under test."""

	def __init__(self) -> None:
		self.docs: dict[str, dict[str, dict[str, Any]]] = {}
		self.errors: list[dict[str, str]] = []
		self.enqueued: list[dict[str, Any]] = []
		self.commits: int = 0
		self._counter: int = 0

	# -- naming and insertion ------------------------------------------------

	def next_name(self, doctype: str) -> str:
		self._counter += 1
		prefix = "".join(part[0] for part in doctype.split()).lower() or "d"
		return f"{prefix}{self._counter:06d}"

	def table(self, doctype: str) -> dict[str, dict[str, Any]]:
		return self.docs.setdefault(doctype, {})

	def insert(self, doctype: str, row: dict[str, Any]) -> str:
		data = dict(row)
		for field, default in DOCTYPE_DEFAULTS.get(doctype, {}).items():
			if data.get(field) in (None, ""):
				data[field] = default
		data.setdefault("name", self.next_name(doctype))
		now = datetime.datetime.now()
		data.setdefault("creation", now)
		data["modified"] = now
		self._check_unique(doctype, data)
		self.table(doctype)[str(data["name"])] = data
		return str(data["name"])

	def _check_unique(self, doctype: str, candidate: dict[str, Any]) -> None:
		for indexed_doctype, fields in UNIQUE_INDEXES:
			if indexed_doctype != doctype:
				continue
			values = [str(candidate.get(field) or "") for field in fields]
			if not all(values):
				continue
			for name, row in self.table(doctype).items():
				if name == str(candidate.get("name") or ""):
					continue
				if [str(row.get(field) or "") for field in fields] == values:
					raise StubUniqueValidationError(
						f"{doctype} {'/'.join(fields)} must be unique; {values} already on {name}"
					)

	# -- reads ---------------------------------------------------------------

	def resolve(self, doctype: str, key: Any) -> dict[str, Any] | None:
		if isinstance(key, dict):
			for row in self.table(doctype).values():
				if matches(row, key):
					return row
			return None
		return self.table(doctype).get(str(key))

	def select(
		self,
		doctype: str,
		filters: dict[str, Any] | None = None,
		order_by: str | None = None,
		limit: int | None = None,
	) -> list[dict[str, Any]]:
		rows = [row for row in self.table(doctype).values() if matches(row, filters or {})]
		if order_by:
			field, _, direction = str(order_by).partition(" ")
			rows.sort(key=lambda row: _sort_key(row.get(field)), reverse=direction.strip() == "desc")
		if limit:
			rows = rows[: int(limit)]
		return rows

	# -- writes --------------------------------------------------------------

	def update(self, doctype: str, key: Any, values: dict[str, Any]) -> None:
		row = self.resolve(doctype, key)
		if row is None:
			return
		candidate = dict(row)
		candidate.update(values)
		self._check_unique(doctype, candidate)
		row.update(values)


def _sort_key(value: Any) -> Any:
	"""Sort ``None`` alongside strings rather than raising on a mixed column."""
	if value is None:
		return ""
	if isinstance(value, datetime.datetime):
		return value.isoformat()
	return value


def like_matches(value: str, pattern: str) -> bool:
	"""MariaDB ``LIKE`` for the one shape this codebase uses: a wildcard-free literal.

	``LIKE`` with no unescaped wildcard is an equality **under the column's collation**, and a
	Frappe ``name`` column is case-insensitive. That is the property ``sync/membership.py``
	leans on to resolve an address onto the ``User`` row whose name the ``unique(room, user)``
	index actually compares, so the stub has to model it: one that compared bytes would make
	the resolution look broken.

	The escaping is modelled too, and that is not pedantry. ``_`` is a ``LIKE`` wildcard and it
	is legal in an email local part, so an unescaped ``alice_b@…`` would match ``aliceXb@…``
	and resolve one coworker's membership onto another's account. Asserting the pattern is
	wildcard-free is how this fixture refuses to hide that bug.
	"""
	literal: list[str] = []
	index = 0
	while index < len(pattern):
		char = pattern[index]
		if char == "\\" and index + 1 < len(pattern):
			literal.append(pattern[index + 1])
			index += 2
			continue
		assert char not in "%_", (
			f"the stub implements only a wildcard-free LIKE, and {pattern!r} carries an "
			f"unescaped {char!r} — in MariaDB that would match a different row"
		)
		literal.append(char)
		index += 1
	return value.casefold() == "".join(literal).casefold()


def matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
	"""Frappe's filter dialect, only the operators the modules under test actually use."""
	for field, condition in (filters or {}).items():
		value = row.get(field)
		if isinstance(condition, list | tuple):
			operator, operand = condition[0], condition[1]
			operator = str(operator).lower()
			if operator == "!=":
				if value == operand:
					return False
			elif operator == "like":
				if not like_matches(str(value or ""), str(operand or "")):
					return False
			elif operator == "in":
				if value not in operand:
					return False
			elif operator == "is":
				empty = value in (None, "")
				if operand == "set" and empty:
					return False
				if operand == "not set" and not empty:
					return False
			else:  # pragma: no cover - an operator the stub has never been asked for
				raise AssertionError(f"stub store does not implement the {operator!r} filter")
		elif value != condition:
			return False
	return True


STORE = Store()


# ---------------------------------------------------------------------------
# The frappe stub
# ---------------------------------------------------------------------------


class StubDocument:
	"""Enough of ``frappe.model.document.Document`` for the controller and for inserts."""

	def __init__(self, *args: Any, **kwargs: Any) -> None:
		self.__dict__["_data"] = {}
		if args and isinstance(args[0], dict):
			self.__dict__["_data"].update(args[0])
		self.__dict__["_data"].update(kwargs)
		self.__dict__["_before"] = None

	def __getattr__(self, item: str) -> Any:
		try:
			return self.__dict__["_data"][item]
		except KeyError:
			raise AttributeError(item) from None

	def __setattr__(self, key: str, value: Any) -> None:
		self.__dict__["_data"][key] = value

	def get(self, key: str, default: Any = None) -> Any:
		return self.__dict__["_data"].get(key, default)

	def set(self, key: str, value: Any) -> None:
		self.__dict__["_data"][key] = value

	def as_dict(self) -> dict[str, Any]:
		return dict(self.__dict__["_data"])

	def is_new(self) -> bool:
		return not self.__dict__["_data"].get("name")

	def get_doc_before_save(self) -> Any:
		return self.__dict__["_before"]

	def insert(self, *_args: Any, **_kwargs: Any) -> StubDocument:
		data = self.as_dict()
		doctype = str(data.pop("doctype", "") or "")
		self.name = STORE.insert(doctype, data)
		self.__dict__["_data"]["doctype"] = doctype
		return self


class StubCache:
	"""``frappe.cache()`` — the handful of calls ``seams`` and the alert throttle make."""

	def __init__(self) -> None:
		self.values: dict[str, Any] = {}

	def get_value(self, key: str) -> Any:
		return self.values.get(key)

	def set_value(self, key: str, value: Any, expires_in_sec: int | None = None) -> None:
		self.values[key] = value

	def delete(self, key: str) -> None:
		self.values.pop(key, None)

	def incrby(self, key: str, step: int) -> int:
		self.values[key] = int(self.values.get(key) or 0) + int(step)
		return int(self.values[key])

	def mget(self, keys: list[str]) -> list[Any]:
		return [self.values.get(key) for key in keys]

	def eval(self, script: str, _numkeys: int, key: str, *args: Any) -> list[int]:
		"""``frappe.cache()`` **is** a ``redis.Redis`` subclass, so ``ratelimit`` calls
		``.eval()`` directly. Rather than interpret Lua, this dispatches on which of the two
		scripts was passed and runs the **pure Python the script transcribes** — which is the
		relationship ``ratelimit``'s docstring claims those two have, so the stub is a
		deployment of the same specification rather than a second guess at it.
		"""
		from erpnext_enhancements.chat.sync import ratelimit

		if script is ratelimit.PROJECT_QUOTA_LUA:
			cost, limit, ttl_ms = int(args[0]), int(args[1]), int(args[2])
			decision = ratelimit.quota_decision(int(self.values.get(key) or 0), cost, limit)
			if decision.allowed:
				self.values[key] = decision.count
			del ttl_ms
			return [1 if decision.allowed else 0, decision.count]

		if script is ratelimit.SPACE_BUCKET_LUA:
			now_ms, cost_ms, ttl_ms = int(args[0]), int(args[1]), int(args[2])
			decision = ratelimit.bucket_decision(now_ms, int(self.values.get(key) or 0), cost_ms)
			if decision.allowed:
				self.values[key] = decision.next_free_ms
			del ttl_ms
			return [1 if decision.allowed else 0, decision.wait_ms, decision.next_free_ms]

		raise AssertionError("the stub cache was handed a Lua script it does not know")


CACHE = StubCache()


class StubSettings:
	"""``Chat Settings``. Attribute reads only — every consumer uses ``getattr(..., None)``."""

	def __init__(self) -> None:
		self.enabled = 1
		self.dry_run_mode = 0
		self.org_structure_mirroring_enabled = 1
		self.document_spaces_enabled = 1
		self.project_space_writes_per_minute = 60
		self.project_membership_writes_per_minute = 300
		self.max_reverts_per_hour = 5


SETTINGS = StubSettings()


class _NullLogger:
	def info(self, *_a: Any, **_k: Any) -> None:
		return

	debug = warning = error = info


@contextlib.contextmanager
def stub_savepoint(catch: Any = None) -> Any:
	"""``frappe.database.database.savepoint`` — model which exceptions escape, and only that.

	Frappe's own issues ``SAVEPOINT``/``ROLLBACK TO`` around the block and swallows the classes
	in ``catch``. There is no transaction in this store to roll back, so what is modelled is the
	half the code under test depends on: a listed exception is suppressed and execution resumes
	*after* the ``with``, and anything else propagates.

	The filtering is the point. A stub that swallowed everything would turn the race tests below
	green against an implementation that caught ``Exception`` and hid a real fault, which is the
	failure mode ``TestDiscovery`` exists for, applied to a fixture.
	"""
	classes = catch if isinstance(catch, tuple) else ((catch,) if catch else ())
	try:
		yield
	except BaseException as exc:
		if classes and isinstance(exc, classes):
			return
		raise


def install_frappe_stub() -> types.ModuleType:
	"""The house pattern: build the module, attach only what the code under test uses."""
	frappe = types.ModuleType("frappe")

	frappe.local = types.SimpleNamespace(site="test.invalid", task_id=None, message_log=[])
	frappe.session = types.SimpleNamespace(user="alice@example.com")

	def _clear_messages() -> None:
		# `show_unique_validation_message` calls `frappe.msgprint` BEFORE raising, so an
		# expected duplicate leaves a user-visible "must be unique" banner behind unless the
		# dedupe helper clears it. Present here so that code path is executed rather than
		# skipped by a `getattr` miss.
		frappe.local.message_log.clear()

	frappe.clear_messages = _clear_messages

	frappe.ValidationError = StubValidationError
	frappe.UniqueValidationError = StubUniqueValidationError
	frappe.DuplicateEntryError = StubDuplicateEntryError
	frappe.DoesNotExistError = StubDoesNotExistError
	frappe.PermissionError = StubPermissionError

	#: Roles the stubbed session holds. A list rather than a constant so a test can take a
	#: role away — which is the only way `only_for` below is worth stubbing at all.
	frappe.session_roles = ["System Manager", "Chat User"]

	def _only_for(*roles: Any) -> None:
		"""The real ``frappe.only_for``, near enough: raise unless the caller holds one.

		**Stubbed with teeth rather than as a no-op**, and the difference is the point. Both
		whitelisted functions in `chat/sync/provisioning.py` shipped with *no* role gate — any
		authenticated System User could create `Chat Room` rows and open a provisioning run,
		with `dry_run` a caller-supplied parameter away from creating real Google spaces. A
		stub that swallowed `only_for` would let this suite pass whether the gate is there or
		not, which is how the gate goes missing again.
		"""
		wanted = {r for arg in roles for r in ([arg] if isinstance(arg, str) else arg)}
		if wanted and not wanted & set(frappe.session_roles):
			raise StubPermissionError(f"not permitted: needs one of {sorted(wanted)}")

	frappe.only_for = _only_for

	def _translate(text: str) -> str:
		return text

	frappe._ = _translate

	def _throw(message: str, *_a: Any, **_k: Any) -> None:
		raise StubValidationError(message)

	frappe.throw = _throw
	frappe.logger = lambda *_a, **_k: _NullLogger()
	frappe.cache = lambda: CACHE
	frappe.whitelist = lambda *_a, **_k: (lambda fn: fn)
	frappe.has_permission = lambda *_a, **_k: True
	frappe.get_cached_doc = lambda _doctype: SETTINGS

	def _log_error(title: str = "", message: str = "", **_k: Any) -> None:
		STORE.errors.append({"title": title, "message": message})

	frappe.log_error = _log_error

	def _enqueue(method: str, **kwargs: Any) -> None:
		# Recorded, never executed. Executing would recurse through
		# run_provisioning_run -> _enqueue_run -> run_provisioning_run forever, and the whole
		# point of the batch is that each pass hands control back.
		assert kwargs.get("enqueue_after_commit") is True, (
			f"{method} was enqueued without enqueue_after_commit=True; the worker would read a "
			"row its transaction has not committed yet."
		)
		STORE.enqueued.append({"method": method, **kwargs})

	frappe.enqueue = _enqueue

	def _get_doc(arg: Any, name: str | None = None) -> StubDocument:
		if isinstance(arg, dict):
			return StubDocument(arg)
		row = STORE.resolve(str(arg), name)
		if row is None:
			raise StubDoesNotExistError(f"{arg} {name} not found")
		return StubDocument({**row, "doctype": str(arg)})

	frappe.get_doc = _get_doc

	def _get_all(
		doctype: str,
		filters: dict[str, Any] | None = None,
		fields: list[str] | None = None,
		order_by: str | None = None,
		limit_page_length: int | None = None,
		**_kwargs: Any,
	) -> list[dict[str, Any]]:
		rows = STORE.select(doctype, filters, order_by, limit_page_length or None)
		if not fields:
			return [{"name": row["name"]} for row in rows]
		return [{field: row.get(field) for field in fields} for row in rows]

	frappe.get_all = _get_all
	frappe.get_list = _get_all

	db = types.SimpleNamespace()

	def _db_get_value(
		doctype: str,
		key: Any,
		fieldname: Any = "name",
		as_dict: bool = False,
		**_kwargs: Any,
	) -> Any:
		row = STORE.resolve(doctype, key)
		if row is None:
			return None
		if isinstance(fieldname, list | tuple):
			if as_dict:
				return {field: row.get(field) for field in fieldname}
			return [row.get(field) for field in fieldname]
		return row.get(str(fieldname))

	def _db_set_value(
		doctype: str,
		key: Any,
		field: Any,
		value: Any = None,
		update_modified: bool = True,
		**_kwargs: Any,
	) -> None:
		values = dict(field) if isinstance(field, dict) else {str(field): value}
		if update_modified:
			values["modified"] = datetime.datetime.now()
		STORE.update(doctype, key, values)

	def _db_exists(doctype: str, key: Any = None) -> Any:
		if doctype == "DocType":
			return key in STORE.docs or key in {"Project", "Department", "Team", "Task"}
		row = STORE.resolve(doctype, key)
		return row["name"] if row else None

	def _db_commit() -> None:
		STORE.commits += 1

	db.get_value = _db_get_value
	db.set_value = _db_set_value
	db.exists = _db_exists
	db.commit = _db_commit
	db.rollback = lambda: None
	frappe.db = db

	sys.modules["frappe"] = frappe

	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = datetime.datetime.now

	def _get_datetime(value: Any) -> datetime.datetime:
		if isinstance(value, datetime.datetime):
			return value
		return datetime.datetime.fromisoformat(str(value))

	utils.get_datetime = _get_datetime
	utils.cint = lambda value: int(value or 0)
	utils.flt = lambda value: float(value or 0)
	utils.get_url = lambda path="": f"https://test.invalid{path}"
	sys.modules["frappe.utils"] = utils
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = StubDocument
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	model.document = document
	frappe.model = model

	database = types.ModuleType("frappe.database")
	database_database = types.ModuleType("frappe.database.database")
	database_database.savepoint = stub_savepoint
	sys.modules["frappe.database"] = database
	sys.modules["frappe.database.database"] = database_database
	database.database = database_database
	frappe.database = database

	return frappe


FRAPPE = install_frappe_stub()

# Imported after the stub: both modules do `import frappe` at module scope.
from erpnext_enhancements.chat.doctype.chat_provisioning_run import (
	chat_provisioning_run as run_controller,
)
from erpnext_enhancements.chat.gchat.client import (
	AuthIdentity,
	GoogleChatClient,
)
from erpnext_enhancements.chat.sync import membership as ms
from erpnext_enhancements.chat.sync import provisioning as pv
from erpnext_enhancements.chat.testing.fake_chat import (
	FakeChatAPI,
	FakeChatSettings,
)

ALICE = "alice@example.com"
BOB = "bob@example.com"
CAROL = "carol@example.com"
DAVE = "dave@example.com"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_store() -> Any:
	"""A fresh store, cache and settings per test. Autouse: leaked rows are a false green."""
	global STORE
	STORE.docs.clear()
	STORE.errors.clear()
	STORE.enqueued.clear()
	STORE.commits = 0
	STORE._counter = 0
	CACHE.values.clear()
	FRAPPE.local.message_log.clear()
	fresh = StubSettings()
	for field, value in vars(fresh).items():
		setattr(SETTINGS, field, value)
	for user in (ALICE, BOB, CAROL, DAVE):
		seed_user(user)
	return STORE


def seed_user(email: str, *, name: str | None = None) -> str:
	"""A ``User`` row, idempotently. Not fixture decoration — it is the key the index compares.

	``Chat Room Member.user`` is a Link to ``User``, so the value ``unique(room, user)`` compares
	is this row's **name**. ``sync/membership.py`` resolves an incoming address onto it before it
	probes or inserts, and correctly refuses to invent a member row for an address that names no
	account, so a store with no ``User`` table would silently make every membership write a no-op.

	Passing ``name`` different from ``email`` is the case the resolution exists for: a login typed
	in mixed case, met by an address the diff has already lower-cased.
	"""
	key = str(name or email)
	if STORE.table("User").get(key):
		return key
	return STORE.insert("User", {"name": key, "email": email, "enabled": 1})


@pytest.fixture()
def fake() -> FakeChatAPI:
	return FakeChatAPI()


def client_for(fake: FakeChatAPI, subject: str = ALICE) -> GoogleChatClient:
	"""A **real** ``GoogleChatClient`` on the fake transport — real builders, real retry loop."""
	return GoogleChatClient(
		subject=subject,
		identity=AuthIdentity.USER,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=fake.token_provider,
		transport=fake,
	)


def make_room(**fields: Any) -> str:
	row: dict[str, Any] = {
		"room_type": pv.ROOM_TYPE_GROUP,
		"title": "Project Falcon",
		"is_archived": 0,
		"provisioning_mode": pv.PROVISION_ON_FIRST_MESSAGE,
		"provisioning_state": pv.STATE_PENDING,
		"provisioning_attempts": 0,
		"membership_authority": ms.AUTHORITY_ERPNEXT,
		"external_users_allowed": 0,
		"mirror_whitelisted": 0,
		"seq_high_water": 0,
		"reverts_this_hour": 0,
	}
	row.update(fields)
	return STORE.insert("Chat Room", row)


def add_member(room: str, user: str, *, role: str = "Member", **fields: Any) -> str:
	seed_user(user)
	row: dict[str, Any] = {
		"room": room,
		"user": user,
		"role": role,
		"is_active": 1,
		"gchat_membership_name": "",
		"gchat_member_state": "",
		"sync_state": ms.SYNC_PENDING,
	}
	row.update(fields)
	return STORE.insert("Chat Room Member", row)


def room_row(room: str) -> dict[str, Any]:
	return dict(STORE.table("Chat Room")[room])


def member_row(room: str, user: str) -> dict[str, Any]:
	found = STORE.resolve("Chat Room Member", {"room": room, "user": user})
	assert found is not None, f"no Chat Room Member row for {user} in {room}"
	return dict(found)


def calls_to(fake: FakeChatAPI, google_method: str) -> list[Any]:
	return [call for call in fake.calls if call.google_method == google_method]


# ---------------------------------------------------------------------------
# Pure: which space shape a room wants
# ---------------------------------------------------------------------------


def test_space_type_for_maps_every_room_type() -> None:
	assert pv.space_type_for(pv.ROOM_TYPE_DM, "") == "DIRECT_MESSAGE"
	assert pv.space_type_for(pv.ROOM_TYPE_DM, "ignored") == "DIRECT_MESSAGE"
	assert pv.space_type_for(pv.ROOM_TYPE_ORGANIZATION, "Engineering") == "SPACE"
	assert pv.space_type_for(pv.ROOM_TYPE_DOCUMENT, "PRJ-1") == "SPACE"
	assert pv.space_type_for(pv.ROOM_TYPE_GROUP, "Falcon") == "SPACE"
	assert pv.space_type_for(pv.ROOM_TYPE_GROUP, "   ") == "GROUP_CHAT", (
		"an untitled Group has to be a GROUP_CHAT: a SPACE requires a displayName and a "
		"GROUP_CHAT must not have one, so the title is the only thing that can decide"
	)


def test_choose_caller_prefers_a_manager_and_is_deterministic() -> None:
	members = [
		{"user": CAROL, "role": "Member", "is_active": 1},
		{"user": BOB, "role": "Manager", "is_active": 1},
		{"user": ALICE, "role": "Member", "is_active": 1},
	]
	assert pv.choose_caller(members) == BOB
	assert pv.choose_caller([row for row in members if row["role"] == "Member"]) == ALICE
	assert pv.choose_caller([{"user": BOB, "role": "Member", "is_active": 0}]) == ""


# ---------------------------------------------------------------------------
# The three spaceType shape constraints, as validation errors
# ---------------------------------------------------------------------------


def test_a_space_without_a_display_name_is_refused() -> None:
	"""``SPACE`` requires ``displayName`` — caught here, never discovered as a 400."""
	with pytest.raises(pv.ProvisioningRefused) as caught:
		pv.build_plan(
			room="r1",
			room_type=pv.ROOM_TYPE_ORGANIZATION,
			title="",
			members=[{"user": ALICE, "role": "Manager", "is_active": 1}, {"user": BOB, "is_active": 1}],
			site="test.invalid",
		)
	assert "displayName" in str(caught.value)


def test_a_group_chat_needs_two_memberships() -> None:
	"""``GROUP_CHAT`` needs at least two memberships **besides the caller**."""
	with pytest.raises(pv.ProvisioningRefused) as caught:
		pv.build_plan(
			room="r2",
			room_type=pv.ROOM_TYPE_GROUP,
			title="",
			members=[{"user": ALICE, "role": "Manager", "is_active": 1}, {"user": BOB, "is_active": 1}],
			site="test.invalid",
		)
	assert "GROUP_CHAT" in str(caught.value)
	assert "2 memberships" in str(caught.value)


def test_a_direct_message_needs_exactly_one_peer() -> None:
	"""``DIRECT_MESSAGE`` takes exactly one membership and never a display name."""
	with pytest.raises(pv.ProvisioningRefused) as caught:
		pv.build_plan(
			room="r3",
			room_type=pv.ROOM_TYPE_DM,
			title="",
			members=[],
			site="test.invalid",
			dm_user_1=ALICE,
			dm_user_2="",
		)
	assert "Direct Message" in str(caught.value)

	plan = pv.build_plan(
		room="r4",
		room_type=pv.ROOM_TYPE_DM,
		title="A title the DM must not carry",
		members=[],
		site="test.invalid",
		dm_user_1=ALICE,
		dm_user_2=BOB,
	)
	assert plan.space_type == "DIRECT_MESSAGE"
	assert plan.display_name == ""
	assert plan.member_emails == (BOB,)
	assert plan.caller_email == ALICE


def test_the_request_id_is_derived_from_the_room_and_is_stable() -> None:
	"""Idempotency's third line of defence: same room in, same ``requestId`` out, forever."""
	kwargs: dict[str, Any] = {
		"room_type": pv.ROOM_TYPE_GROUP,
		"title": "Falcon",
		"members": [{"user": ALICE, "role": "Manager", "is_active": 1}, {"user": BOB, "is_active": 1}],
		"site": "test.invalid",
	}
	first = pv.build_plan(room="room-a", **kwargs)
	second = pv.build_plan(room="room-a", **kwargs)
	other = pv.build_plan(room="room-b", **kwargs)
	assert first.request_id == second.request_id
	assert first.request_id != other.request_id


# ---------------------------------------------------------------------------
# Pure: the external-user gate and the read-back helpers
# ---------------------------------------------------------------------------


def test_external_users_allowed_reads_the_space_field_and_defaults_closed_to_false() -> None:
	assert pv.external_users_allowed({"externalUserAllowed": True}) is True
	assert pv.external_users_allowed({"externalUserAllowed": False}) is False
	assert pv.external_users_allowed({}) is False
	assert pv.external_users_allowed(None) is False


def test_threading_state_is_read_back_and_unspecified_is_not_stored() -> None:
	assert pv.threading_state_of({"spaceThreadingState": "GROUPED_MESSAGES"}) == "GROUPED_MESSAGES"
	assert pv.threading_state_of({"spaceThreadingState": "SPACE_THREADING_STATE_UNSPECIFIED"}) == ""
	assert pv.threading_state_of({}) == ""


def test_assert_mirror_allowed_refuses_only_the_unwhitelisted_external_case() -> None:
	pv.assert_mirror_allowed(room="r", external=False, whitelisted=False)
	pv.assert_mirror_allowed(room="r", external=True, whitelisted=True)
	with pytest.raises(pv.ProvisioningRefused) as caught:
		pv.assert_mirror_allowed(room="r", external=True, whitelisted=False)
	assert "externalUserAllowed" in str(caught.value)


# ---------------------------------------------------------------------------
# Pure: the resume slice
# ---------------------------------------------------------------------------


def test_resume_from_takes_units_strictly_after_the_cursor() -> None:
	units = ["a", "b", "c", "d", "e"]
	first = pv.resume_from(units, "", 2)
	assert first.units == ("a", "b")
	assert first.next_cursor == "b"
	assert first.remaining == 3

	second = pv.resume_from(units, first.next_cursor, 2)
	assert second.units == ("c", "d"), (
		"the cursor names a unit that is already done, so the next slice must start strictly "
		"after it — '>=' would re-provision it and spend a space write from a 60-a-minute budget"
	)

	last = pv.resume_from(units, "d", 10)
	assert last.units == ("e",)
	assert last.exhausted

	assert pv.resume_from(units, "e", 10).units == ()
	assert pv.resume_from(units, "e", 10).exhausted


def test_resume_from_is_total_over_unsorted_and_duplicate_input() -> None:
	messy = ["c", "a", "c", "", "b"]
	assert pv.resume_from(messy, "", 10).units == ("a", "b", "c")


# ---------------------------------------------------------------------------
# Mode 1 — provisioning one room, and doing it twice
# ---------------------------------------------------------------------------


def test_provisioning_reads_the_threading_state_back_off_the_created_space(fake: FakeChatAPI) -> None:
	room = make_room()
	add_member(room, ALICE, role="Manager")
	add_member(room, BOB)

	space = pv.provision_room(room, client=client_for(fake))

	assert space.startswith("spaces/")
	stored = room_row(room)
	assert stored["gchat_space_name"] == space
	assert stored["gchat_space_type"] == "SPACE"
	assert stored["provisioning_state"] == pv.STATE_READY
	assert stored["gchat_threading_state"] == "GROUPED_MESSAGES"
	assert calls_to(fake, "spaces.get"), (
		"the threading state must be READ BACK off the created space. No document states which "
		"state an API-created space lands in, so a value that was not read is a guess."
	)


def test_provisioning_twice_creates_exactly_one_space(fake: FakeChatAPI) -> None:
	"""The headline property. Two runs, one space, and the second makes no space write."""
	room = make_room()
	add_member(room, ALICE, role="Manager")
	add_member(room, BOB)
	api = client_for(fake)

	first = pv.provision_room(room, client=api)
	setups_after_first = len(calls_to(fake, "spaces.setup"))
	second = pv.provision_room(room, client=api)

	assert first == second
	assert len(fake._spaces) == 1
	assert setups_after_first == 1
	assert (
		len(calls_to(fake, "spaces.setup")) == 1
	), "the second run short-circuited on gchat_space_name and must not have spent a space write"


def test_a_direct_message_reads_before_it_writes(fake: FakeChatAPI) -> None:
	"""``findDirectMessage`` first. ``spaces.setup`` would also return the existing DM, but a
	read does not spend the 60-writes-per-minute space budget and a write does."""
	room_one = make_room(room_type=pv.ROOM_TYPE_DM, title="", dm_user_1=ALICE, dm_user_2=BOB)
	api = client_for(fake)
	space = pv.provision_room(room_one, client=api)
	assert calls_to(fake, "spaces.findDirectMessage"), "the DM path must probe before creating"
	assert len(calls_to(fake, "spaces.setup")) == 1

	# A second room for the same pair — the reconciliation case. The DM already exists, so the
	# probe finds it and no second space write happens.
	STORE.table("Chat Room")[room_one]["gchat_space_name"] = ""
	STORE.table("Chat Room")[room_one]["provisioning_state"] = pv.STATE_PENDING
	again = pv.provision_room(room_one, client=api)

	assert again == space
	assert (
		len(calls_to(fake, "spaces.setup")) == 1
	), "the existing DM was found by a read; setup must not have been called a second time"


def test_a_not_mirrored_room_is_disabled_and_never_calls_google(fake: FakeChatAPI) -> None:
	room = make_room(provisioning_mode=pv.PROVISION_NOT_MIRRORED)
	add_member(room, ALICE, role="Manager")

	assert pv.provision_room(room, client=client_for(fake)) == ""
	assert room_row(room)["provisioning_state"] == pv.STATE_DISABLED
	assert fake.calls == []


def test_a_dry_run_never_binds_a_synthetic_space_name() -> None:
	"""Binding ``spaces/DRYRUN-…`` would occupy the unique index with a space that does not
	exist and send every later reconciliation hunting for it."""
	room = make_room()
	add_member(room, ALICE, role="Manager")
	add_member(room, BOB)

	dry = GoogleChatClient(subject=ALICE, dry_run=True, settings=FakeChatSettings())
	assert pv.provision_room(room, client=dry) == ""

	stored = room_row(room)
	assert not stored.get("gchat_space_name")
	assert stored["provisioning_state"] == pv.STATE_PENDING
	assert "Dry run" in stored["provisioning_error"]


# ---------------------------------------------------------------------------
# The external-user gate at provisioning time (CQ-12)
# ---------------------------------------------------------------------------


def test_an_external_allowed_space_is_refused_loudly_and_not_bound() -> None:
	room = make_room()
	resource = {
		"name": "spaces/AAAAexternal",
		"spaceType": "SPACE",
		"spaceThreadingState": "GROUPED_MESSAGES",
		"externalUserAllowed": True,
	}

	with pytest.raises(pv.ProvisioningRefused):
		pv._bind_space(room, resource)

	stored = room_row(room)
	assert stored["external_users_allowed"] == 1, "what Google says is recorded even when refused"
	assert not stored.get("gchat_space_name"), "an external-allowed space must not be mirrored"
	assert stored["provisioning_state"] == pv.STATE_FAILED
	assert "externalUserAllowed" in stored["provisioning_error"]


def test_a_whitelisted_room_may_mirror_an_external_allowed_space() -> None:
	room = make_room(mirror_whitelisted=1)
	resource = {
		"name": "spaces/AAAAexternal",
		"spaceType": "SPACE",
		"spaceThreadingState": "GROUPED_MESSAGES",
		"externalUserAllowed": True,
	}

	assert pv._bind_space(room, resource) == "spaces/AAAAexternal"
	stored = room_row(room)
	assert stored["external_users_allowed"] == 1
	assert stored["provisioning_state"] == pv.STATE_READY


def test_the_refusal_is_reapplied_on_every_refresh(fake: FakeChatAPI) -> None:
	"""A space can acquire the flag long after provisioning, so the gate is re-read, not cached."""
	room = make_room()
	add_member(room, ALICE, role="Manager")
	add_member(room, BOB)
	api = client_for(fake)
	space = pv.provision_room(room, client=api)

	# Google now reports the space as external-allowed. The fake does not model the field, so
	# it is injected the same way the API would surface it: on the resource.
	original_get_space = api.get_space
	api.get_space = lambda name: {**original_get_space(name), "externalUserAllowed": True}  # type: ignore[method-assign]

	pv.refresh_space_metadata(room, client=api)

	stored = room_row(room)
	assert stored["external_users_allowed"] == 1
	assert stored["provisioning_state"] == pv.STATE_FAILED
	assert stored["gchat_space_name"] == space, (
		"the binding stays — the space is real and un-deleting it is not this code's decision; "
		"what stops is the mirroring"
	)
	assert any("external-allowed" in error["title"] for error in STORE.errors)


# ---------------------------------------------------------------------------
# Mode 2 — the throttled, resumable bulk run
# ---------------------------------------------------------------------------


class WindowQuota:
	"""A project quota whose fixed window is driven by the fake's clock.

	Mirrors :func:`ratelimit.fixed_window_index` against the same millisecond the fake charges
	its own bucket with, so the test asserts something real: *our* limiter and *Google's* agree
	on when the window rolls, and nothing 429s.
	"""

	def __init__(self, clock: Any) -> None:
		self.clock = clock
		self.counts: dict[tuple[str, int], int] = {}

	def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
		window = self.clock.now_ms() // 60_000
		key = (bucket, window)
		if self.counts.get(key, 0) + cost > limit:
			return False
		self.counts[key] = self.counts.get(key, 0) + cost
		return True


class UnthrottledQuota:
	"""No limiter at all — used only to prove the throttled assertion is not vacuous."""

	def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
		return True


def enroll(count: int, *, mode: str = "Department") -> list[str]:
	"""``count`` enrolled org rooms, each with ALICE as manager and one other member."""
	units = [f"unit-{index:03d}" for index in range(count)]
	pv.enroll_org_units(mode, units)
	for unit in units:
		room = STORE.resolve("Chat Room", {"linked_doctype": mode, "linked_document": unit})
		assert room is not None
		add_member(str(room["name"]), ALICE, role="Manager")
		add_member(str(room["name"]), f"{unit}@example.com")
	return units


def drive(run: str, fake: FakeChatAPI, quota: Any, *, batch_size: int = 10, passes: int = 60) -> str:
	"""Run the batch to completion the way the scheduler would, advancing on a pause."""
	api = client_for(fake)
	status = "Pending"
	for _pass in range(passes):
		outcome = pv.run_provisioning_run(run, client=api, quota=quota, batch_size=batch_size)
		status = str(outcome["status"])
		if status == "Completed":
			return status
		if status == "Paused":
			# What the scheduler does a minute later. The clock is the fake's, so Google's own
			# window rolls at the same instant ours does.
			fake.clock.advance(61_000)
	return status


def test_enrolment_is_the_opt_in_and_creates_no_spaces(fake: FakeChatAPI) -> None:
	units = enroll(5)
	assert len(STORE.table("Chat Room")) == 5
	assert fake.calls == [], "enrolment is a room row and nothing else — never a Google call"

	# Idempotent: enrolling the same units again adds nothing.
	result = pv.enroll_org_units("Department", units)
	assert result["created"] == []
	assert sorted(result["existing"]) == sorted(units)
	assert len(STORE.table("Chat Room")) == 5


def test_both_provisioning_endpoints_refuse_a_caller_without_the_role(fake: FakeChatAPI) -> None:
	"""Phase 6 §4.G.4. Both of these shipped with **no role gate at all**.

	Any authenticated System User could create `Chat Room` rows and open a
	`Chat Provisioning Run` — and `dry_run` is a *caller-supplied parameter*, so the default
	that exists to make the irreversible half deliberate was one query-string value away from
	being skipped. `org_structure_mirroring_enabled` is not a substitute either: it is a
	setting somebody turns on **in order to do this work**, so the window where it is on is
	exactly the window where an ungated endpoint is reachable.

	Asserted here rather than only in the AST scan (`tests/test_chat_endpoint_surface.py`)
	because the two answer different questions. That one asks *is a role check present in the
	function body*; this one asks *does a caller without the role actually get refused*. A
	gate placed after the first write would satisfy the first and fail this.
	"""
	frappe = sys.modules["frappe"]
	original = list(frappe.session_roles)
	frappe.session_roles = ["Chat User"]  # an ordinary employee
	try:
		with pytest.raises(StubPermissionError):
			pv.enroll_org_units("Department", ["dept-nobody-asked-for"])
		with pytest.raises(StubPermissionError):
			pv.start_org_mirror("Department", dry_run=0)
	finally:
		frappe.session_roles = original

	assert STORE.table("Chat Room") == {}, "a refused enrolment must leave no room behind"
	assert STORE.table("Chat Provisioning Run") == {}, "a refused mirror must open no run"
	assert fake.calls == [], "and neither may reach Google"


def test_the_pending_sweep_is_mode_ones_durable_trigger() -> None:
	"""The write path's enqueue can vanish in a deploy's ``FLUSHDB``; a row cannot."""
	quiet = make_room()
	spoken = make_room(last_message="msg-1")
	eager = make_room(provisioning_mode=pv.PROVISION_ON_CREATE)
	stuck = make_room(last_message="msg-2", provisioning_state=pv.STATE_PROVISIONING)
	off = make_room(last_message="msg-3", provisioning_mode=pv.PROVISION_NOT_MIRRORED)
	done = make_room(last_message="msg-4", gchat_space_name="spaces/AAAAdone")

	outcome = pv.sweep_pending_provisioning()
	queued = {str(job["room"]) for job in STORE.enqueued}

	assert outcome["queued"] == 3
	assert queued == {spoken, eager, stuck}, (
		"a conversation that has started, a room asked to provision eagerly, and a room a "
		"killed worker left saying Provisioning — nothing else"
	)
	assert quiet not in queued, "no first message, no space: 60 space writes a minute is the budget"
	assert off not in queued
	assert done not in queued


def test_a_dry_run_reports_what_it_would_create_and_creates_nothing(fake: FakeChatAPI) -> None:
	enroll(4)
	run = pv.start_org_mirror("Department", dry_run=1)
	assert drive(run, fake, WindowQuota(fake.clock), batch_size=2) == "Completed"

	row = STORE.table("Chat Provisioning Run")[run]
	assert row["created_count"] == 0
	assert row["skipped_count"] == 4
	assert fake.calls == []
	assert row["log"].count("would create") == 4, (
		"the counts say how many; only the log says WHICH, and 'which' is what a human reads "
		"before unticking Dry Run"
	)


def test_a_hundred_spaces_are_throttled_so_no_429_escapes(fake: FakeChatAPI) -> None:
	enroll(100)
	run = pv.start_org_mirror("Department", dry_run=0)

	assert drive(run, fake, WindowQuota(fake.clock), batch_size=10) == "Completed"

	assert len(fake._spaces) == 100
	assert len(calls_to(fake, "spaces.setup")) == 100, "exactly one space write per unit"
	assert [call for call in fake.calls if call.status == 429] == [], (
		"a throttled sweep must never see a 429. Project space writes are 60/minute and the run "
		"pauses on its own budget rather than discovering Google's."
	)
	row = STORE.table("Chat Provisioning Run")[run]
	assert row["created_count"] == 100
	assert row["failed_count"] == 0


def test_without_the_throttle_google_would_429(fake: FakeChatAPI) -> None:
	"""Proves the assertion above is not vacuous: remove the limiter and the 429s appear."""
	enroll(80)
	run = pv.start_org_mirror("Department", dry_run=0)

	drive(run, fake, UnthrottledQuota(), batch_size=80, passes=2)

	assert any(call.status == 429 for call in fake.calls), (
		"the fake must enforce the 60-per-minute project space-write bucket, or the throttling "
		"test above is asserting nothing"
	)


def test_an_interrupted_run_resumes_from_the_cursor(fake: FakeChatAPI) -> None:
	units = enroll(12)
	run = pv.start_org_mirror("Department", dry_run=0)
	api = client_for(fake)
	quota = WindowQuota(fake.clock)

	first = pv.run_provisioning_run(run, client=api, quota=quota, batch_size=3)
	assert first["status"] == "Running"
	assert first["processed"] == 3

	row = STORE.table("Chat Provisioning Run")[run]
	assert row["cursor"] == units[2], "the cursor is written after each unit COMMITS, never before"
	assert row["status"] == "Running"
	assert STORE.commits >= 3, "each unit commits, so a killed worker loses at most one unit"

	# The worker is killed by a deploy here. The row still says Running; nothing else survives —
	# the prod deploy FLUSHDBs the queue Redis. The resume path is what recovers it.
	STORE.enqueued.clear()
	assert pv.resume_provisioning_runs() == {"resumed": 1}
	assert STORE.enqueued[0]["method"].endswith("run_provisioning_run")

	assert drive(run, fake, quota, batch_size=3) == "Completed"

	assert len(calls_to(fake, "spaces.setup")) == 12, (
		"twelve units, twelve space writes. A resume that restarted, or a cursor written before "
		"the work, would show a different number here — and a skipped unit is invisible while a "
		"repeated one merely costs quota."
	)
	spaces = {
		str(row["gchat_space_name"])
		for row in STORE.table("Chat Room").values()
		if row.get("gchat_space_name")
	}
	assert len(spaces) == 12


def test_a_terminal_run_is_never_resumed(fake: FakeChatAPI) -> None:
	enroll(2)
	run = pv.start_org_mirror("Department", dry_run=0)
	assert drive(run, fake, WindowQuota(fake.clock), batch_size=10) == "Completed"

	again = pv.run_provisioning_run(run, client=client_for(fake), quota=WindowQuota(fake.clock))
	assert again == {"status": "Completed", "processed": 0, "note": "terminal"}


def test_one_failing_unit_does_not_abort_the_sweep(fake: FakeChatAPI) -> None:
	"""A department whose manager has no Workspace account must not take the other forty with
	it — the resume path would re-attempt them on every retry."""
	enroll(4)
	broken = STORE.resolve("Chat Room", {"linked_document": "unit-002"})
	assert broken is not None
	STORE.table("Chat Room")[str(broken["name"])]["title"] = ""  # a SPACE with no displayName

	run = pv.start_org_mirror("Department", dry_run=0)
	assert drive(run, fake, WindowQuota(fake.clock), batch_size=2) == "Completed"

	row = STORE.table("Chat Provisioning Run")[run]
	assert row["created_count"] == 3
	assert row["failed_count"] == 1
	assert "unit-002" in row["log"]


# ---------------------------------------------------------------------------
# Mode 3 — per-document rooms and the orphan sweep
# ---------------------------------------------------------------------------


def test_a_document_room_is_user_initiated_and_gated(fake: FakeChatAPI) -> None:
	SETTINGS.document_spaces_enabled = 0
	with pytest.raises(StubValidationError):
		pv.create_document_room("Project", "PRJ-1")

	SETTINGS.document_spaces_enabled = 1
	room = pv.create_document_room("Project", "PRJ-1", members=[BOB])
	stored = room_row(room)
	assert stored["room_type"] == pv.ROOM_TYPE_DOCUMENT
	assert stored["linked_doctype"] == "Project"
	assert stored["linked_document"] == "PRJ-1"
	assert fake.calls == [], "creating the room is not creating the space"

	# Idempotent, and the composite unique index is what says so.
	assert pv.create_document_room("Project", "PRJ-1") == room


def test_a_deleted_linked_document_archives_the_room_and_leaves_the_space(fake: FakeChatAPI) -> None:
	STORE.insert("Project", {"name": "PRJ-9", "docstatus": 0})
	room = pv.create_document_room("Project", "PRJ-9", members=[BOB])
	add_member(room, CAROL)
	api = client_for(fake)
	space = pv.provision_room(room, client=api)
	assert space

	# The project is deleted outside ERPNext's document events — frappe.db.delete, a bulk
	# delete, a SQL cleanup. No hook fires; the sweep is what notices.
	del STORE.table("Project")["PRJ-9"]

	# The sweep constructs its own clients, so the Google half is driven directly with ours.
	outcome = pv.sweep_orphaned_document_rooms()
	assert outcome["orphaned"] == 1

	stored = room_row(room)
	assert stored["is_archived"] == 1
	assert stored["provisioning_mode"] == pv.PROVISION_NOT_MIRRORED
	assert stored["provisioning_state"] == pv.STATE_DISABLED
	assert "no longer exists" in stored["provisioning_error"]
	assert any("orphaned" in error["title"].lower() for error in STORE.errors)
	assert fake.space(space) is not None, (
		"the space must survive. spaces.delete cascades and takes the conversation with it, and "
		"a document being deleted is not consent to destroy what people said about it."
	)


def test_a_cancelled_linked_document_orphans_the_room() -> None:
	STORE.insert("Project", {"name": "PRJ-8", "docstatus": 0})
	room = pv.create_document_room("Project", "PRJ-8")
	STORE.table("Project")["PRJ-8"]["docstatus"] = 2

	assert pv.sweep_orphaned_document_rooms()["orphaned"] == 1
	assert "cancelled" in room_row(room)["provisioning_error"]


def test_a_missing_doctype_is_not_treated_as_an_orphan() -> None:
	"""An app being uninstalled must not archive every room hung off it."""
	room = pv.create_document_room("Project", "PRJ-7")
	STORE.table("Chat Room")[room]["linked_doctype"] = "Nonexistent Doctype"

	assert pv.sweep_orphaned_document_rooms()["orphaned"] == 0
	assert room_row(room)["is_archived"] == 0


# ---------------------------------------------------------------------------
# Pure: the membership diff
# ---------------------------------------------------------------------------


def test_diff_membership_adds_removes_and_converges() -> None:
	diff = ms.diff_membership(
		desired=[ALICE, BOB, CAROL],
		known={ALICE: "spaces/S/members/1", DAVE: "spaces/S/members/9"},
		live=["spaces/S/members/1", "spaces/S/members/9"],
	)
	assert diff.to_add == (BOB, CAROL)
	assert diff.to_remove == ("spaces/S/members/9",)
	assert diff.unchanged == (ALICE,)
	assert not diff.converged

	converged = ms.diff_membership(
		desired=[ALICE],
		known={ALICE: "spaces/S/members/1"},
		live=["spaces/S/members/1"],
	)
	assert converged.converged
	assert converged.to_add == ()
	assert converged.to_remove == ()


def test_diff_membership_is_case_insensitive_on_the_erpnext_side() -> None:
	diff = ms.diff_membership(
		desired=["Alice@Example.com"],
		known={"ALICE@example.com": "spaces/S/members/1"},
		live=["spaces/S/members/1"],
	)
	assert diff.converged, "a casing difference must not produce an add-then-remove flap"


def test_a_membership_erpnext_cannot_name_is_reported_and_never_removed() -> None:
	"""The consequence of the API returning no addresses. See the module docstring."""
	diff = ms.diff_membership(
		desired=[ALICE],
		known={ALICE: "spaces/S/members/1"},
		live=["spaces/S/members/1", "spaces/S/members/stranger"],
	)
	assert diff.to_remove == ()
	assert diff.unknown == ("spaces/S/members/stranger",)
	assert diff.converged, (
		"an unnameable membership never resolves itself, so counting it as unconverged would "
		"make every affected room permanently noisy and train people to ignore the alert"
	)


def test_a_member_believed_present_without_a_resource_name_is_not_re_added() -> None:
	"""``spaces.setup`` creates memberships and returns none of them; without this the diff
	would re-add every one of them on every pass, forever."""
	diff = ms.diff_membership(
		desired=[ALICE, BOB], known={}, present=[ALICE, BOB], live=["spaces/S/members/x"]
	)
	assert diff.to_add == ()
	assert diff.unchanged == (ALICE, BOB)


def test_a_bound_member_missing_from_the_listing_is_re_added() -> None:
	diff = ms.diff_membership(
		desired=[ALICE], known={ALICE: "spaces/S/members/1"}, live=["spaces/S/members/other"]
	)
	assert diff.to_add == (ALICE,)


def test_protect_keeps_the_caller_out_of_the_removal_list() -> None:
	diff = ms.diff_membership(
		desired=[],
		known={ALICE: "spaces/S/members/1", BOB: "spaces/S/members/2"},
		live=["spaces/S/members/1", "spaces/S/members/2"],
		protect=["spaces/S/members/1"],
	)
	assert diff.to_remove == ("spaces/S/members/2",)


def test_external_members_are_detected_by_affiliation() -> None:
	"""``Membership.affiliation`` is populated under user auth; the member's email is not."""
	rows = [
		{"name": "spaces/S/members/1", "affiliation": "INTERNAL"},
		{"name": "spaces/S/members/2", "affiliation": "EXTERNAL"},
		{"name": "spaces/S/members/3", "affiliation": "MANAGED_EXTERNAL"},
		{"name": "spaces/S/members/4"},
	]
	assert ms.external_members(rows) == ("spaces/S/members/2", "spaces/S/members/3")


# ---------------------------------------------------------------------------
# Pure: the revert rule and the cap
# ---------------------------------------------------------------------------


def test_classify_inbound_membership_covers_the_whole_matrix() -> None:
	table = {
		# (authority, in_google, in_erpnext, revert_allowed) -> verdict
		(ms.AUTHORITY_BIDIRECTIONAL, True, True, False): ms.InboundMembershipVerdict.IGNORE,
		(ms.AUTHORITY_BIDIRECTIONAL, False, False, False): ms.InboundMembershipVerdict.IGNORE,
		(ms.AUTHORITY_BIDIRECTIONAL, True, False, False): ms.InboundMembershipVerdict.ACCEPT,
		(ms.AUTHORITY_BIDIRECTIONAL, False, True, False): ms.InboundMembershipVerdict.ACCEPT,
		(ms.AUTHORITY_ERPNEXT, True, True, True): ms.InboundMembershipVerdict.IGNORE,
		(ms.AUTHORITY_ERPNEXT, True, False, True): ms.InboundMembershipVerdict.REVERT,
		(ms.AUTHORITY_ERPNEXT, False, True, True): ms.InboundMembershipVerdict.REVERT,
		(ms.AUTHORITY_ERPNEXT, True, False, False): ms.InboundMembershipVerdict.CAPPED,
		(ms.AUTHORITY_ERPNEXT, False, True, False): ms.InboundMembershipVerdict.CAPPED,
		# An unknown authority reads as ERPNext — the strict direction.
		("Something Else", True, False, True): ms.InboundMembershipVerdict.REVERT,
	}
	for (authority, in_google, in_erpnext, allowed), expected in table.items():
		assert (
			ms.classify_inbound_membership(
				authority=authority,
				in_google=in_google,
				in_erpnext=in_erpnext,
				revert_allowed=allowed,
			)
			is expected
		), f"{authority} google={in_google} erpnext={in_erpnext} allowed={allowed}"


def test_revert_decision_counts_within_the_window() -> None:
	first = ms.revert_decision(now_epoch=1000.0, window_start_epoch=None, count=0, cap=3)
	assert first.allowed and first.window_reset and first.count == 1

	second = ms.revert_decision(now_epoch=1010.0, window_start_epoch=1000.0, count=1, cap=3)
	assert second.allowed and not second.window_reset and second.count == 2


def test_revert_decision_alarms_at_the_cap_instead_of_looping() -> None:
	capped = ms.revert_decision(now_epoch=1010.0, window_start_epoch=1000.0, count=3, cap=3)
	assert not capped.allowed
	assert capped.alarm
	assert capped.count == 3, "a refused revert must not increment the counter"


def test_revert_decision_opens_a_new_window_after_an_hour() -> None:
	rolled = ms.revert_decision(now_epoch=1000.0 + 3600.0, window_start_epoch=1000.0, count=99, cap=3)
	assert rolled.allowed
	assert rolled.window_reset
	assert rolled.count == 1


def test_a_zero_cap_disables_reverting_rather_than_meaning_unlimited() -> None:
	off = ms.revert_decision(now_epoch=1000.0, window_start_epoch=None, count=0, cap=0)
	assert not off.allowed
	assert not off.alarm, "a configured stop is not an alarm condition"


# ---------------------------------------------------------------------------
# Membership sync against the fake
# ---------------------------------------------------------------------------


def provisioned_room(fake: FakeChatAPI, *, members: tuple[str, ...] = (ALICE, BOB)) -> tuple[str, str]:
	room = make_room()
	add_member(room, members[0], role="Manager")
	for user in members[1:]:
		add_member(room, user)
	space = pv.provision_room(room, client=client_for(fake))
	return room, space


def test_a_freshly_provisioned_room_needs_zero_membership_writes(fake: FakeChatAPI) -> None:
	"""``spaces.setup`` already added them, so the first diff must be a no-op."""
	room, _space = provisioned_room(fake)
	before = len(calls_to(fake, "spaces.members.create"))

	result = ms.sync_membership_to_google(room, client=client_for(fake), quota=UnthrottledQuota())

	assert result["added"] == 0
	assert result["removed"] == 0
	assert len(calls_to(fake, "spaces.members.create")) == before


def test_membership_diff_converges_and_the_second_pass_is_a_no_op(fake: FakeChatAPI) -> None:
	room, _space = provisioned_room(fake)
	add_member(room, CAROL)
	add_member(room, DAVE)
	api = client_for(fake)

	first = ms.sync_membership_to_google(room, client=api, quota=UnthrottledQuota())
	assert first["added"] == 2
	assert sorted(first["to_add"]) == [CAROL, DAVE]

	second = ms.sync_membership_to_google(room, client=api, quota=UnthrottledQuota())
	assert second["added"] == 0
	assert second["removed"] == 0
	assert second["to_add"] == []

	assert member_row(room, CAROL)["gchat_membership_name"], (
		"the create response is the ONLY place a membership resource name is ever available — a "
		"listing carries no addresses, so an unrecorded name is unrecoverable"
	)


def test_a_member_removed_in_erpnext_is_removed_from_the_space(fake: FakeChatAPI) -> None:
	room, _space = provisioned_room(fake)
	add_member(room, CAROL)
	api = client_for(fake)
	ms.sync_membership_to_google(room, client=api, quota=UnthrottledQuota())
	assert CAROL in fake.members_of(_space)

	STORE.update("Chat Room Member", {"room": room, "user": CAROL}, {"is_active": 0})
	result = ms.sync_membership_to_google(room, client=api, quota=UnthrottledQuota())

	assert result["removed"] == 1
	assert CAROL not in fake.members_of(_space)


def test_membership_writes_stop_when_the_project_budget_is_spent(fake: FakeChatAPI) -> None:
	room, _space = provisioned_room(fake)
	for index in range(5):
		add_member(room, f"extra{index}@example.com")

	class TwoWrites:
		def __init__(self) -> None:
			self.left = 2

		def charge(self, bucket: str, *, limit: int, cost: int = 1) -> bool:
			if self.left <= 0:
				return False
			self.left -= 1
			return True

	result = ms.sync_membership_to_google(room, client=client_for(fake), quota=TwoWrites())
	assert result["added"] == 2
	assert result["deferred"] == 1, "a refusal defers the pass; it is not an error"

	# Convergence is what makes the deferral safe: the next pass finishes the job.
	rest = ms.sync_membership_to_google(room, client=client_for(fake), quota=UnthrottledQuota())
	assert rest["added"] == 3


def test_an_unprovisioned_room_is_skipped_not_failed(fake: FakeChatAPI) -> None:
	room = make_room()
	add_member(room, ALICE, role="Manager")
	assert ms.sync_membership_to_google(room, client=client_for(fake))["skipped"] == "unprovisioned"


def test_an_unwhitelisted_external_room_is_not_synced(fake: FakeChatAPI) -> None:
	room, _space = provisioned_room(fake)
	STORE.update("Chat Room", room, {"external_users_allowed": 1, "mirror_whitelisted": 0})
	add_member(room, CAROL)

	result = ms.sync_membership_to_google(room, client=client_for(fake), quota=UnthrottledQuota())
	assert result["skipped"] == "external_users_not_whitelisted"
	assert CAROL not in fake.members_of(_space)


# ---------------------------------------------------------------------------
# Google -> ERPNext: the revert rule and its cap
# ---------------------------------------------------------------------------


def test_erpnext_authority_reverts_a_member_added_in_chat(fake: FakeChatAPI) -> None:
	room, space = provisioned_room(fake)
	api = client_for(fake)

	# Somebody adds Carol in the native Chat client. ERPNext knows nothing about her, and the
	# inbound EVENT is what supplies the membership resource name a listing would not.
	created = api.create_membership(space, CAROL)
	assert CAROL in fake.members_of(space)

	verdict = ms.apply_inbound_membership(
		room, in_google=True, membership_name=str(created["name"]), client=api
	)

	assert verdict == ms.InboundMembershipVerdict.REVERT.value
	assert CAROL not in fake.members_of(space), "the revert deletes exactly the named membership"
	assert room_row(room)["reverts_this_hour"] == 1
	assert room_row(room)["revert_window_start"] is not None
	comments = [row for row in STORE.table("Comment").values()]
	assert any(
		"reverted" in str(row["content"]).lower() for row in comments
	), "a revert writes an audit row noting the attempted change"


def test_bidirectional_accepts_a_removal_and_stamps_the_departure(fake: FakeChatAPI) -> None:
	room, _space = provisioned_room(fake)
	STORE.update(
		"Chat Room", room, {"membership_authority": ms.AUTHORITY_BIDIRECTIONAL, "seq_high_water": 42}
	)

	verdict = ms.apply_inbound_membership(room, in_google=False, email=BOB, client=client_for(fake))

	assert verdict == ms.InboundMembershipVerdict.ACCEPT.value
	row = member_row(room, BOB)
	assert row["is_active"] == 0
	assert row["left_at"] is not None
	assert row["left_seq"] == 42
	assert room_row(room)["reverts_this_hour"] == 0, "an accepted change spends no revert budget"


def test_the_revert_cap_alarms_instead_of_looping_forever(fake: FakeChatAPI) -> None:
	SETTINGS.max_reverts_per_hour = 2
	room, space = provisioned_room(fake)
	api = client_for(fake)

	verdicts = []
	for index in range(3):
		created = api.create_membership(space, f"intruder{index}@example.com")
		verdicts.append(
			ms.apply_inbound_membership(
				room, in_google=True, membership_name=str(created["name"]), client=api
			)
		)

	assert verdicts == [
		ms.InboundMembershipVerdict.REVERT.value,
		ms.InboundMembershipVerdict.REVERT.value,
		ms.InboundMembershipVerdict.CAPPED.value,
	]
	assert room_row(room)["reverts_this_hour"] == 2, "the capped attempt must not increment"
	assert "intruder2@example.com" in fake.members_of(space), (
		"at the cap the room stops reverting — two systems disagreeing about who belongs in a "
		"room is a question about the org, not a retry"
	)
	assert any("cap" in error["title"].lower() for error in STORE.errors)


def test_a_change_both_sides_already_agree_on_is_ignored(fake: FakeChatAPI) -> None:
	room, _space = provisioned_room(fake)
	verdict = ms.apply_inbound_membership(room, in_google=True, email=BOB, client=client_for(fake))
	assert verdict == ms.InboundMembershipVerdict.IGNORE.value
	assert room_row(room)["reverts_this_hour"] == 0


# ---------------------------------------------------------------------------
# Leaving — stamping is not granting
# ---------------------------------------------------------------------------


def test_stamp_left_records_the_boundary_and_deactivates(fake: FakeChatAPI) -> None:
	room, _space = provisioned_room(fake)
	STORE.update("Chat Room", room, {"seq_high_water": 137})

	assert ms.stamp_left(room, BOB) == 137
	row = member_row(room, BOB)
	assert row["is_active"] == 0
	assert row["left_seq"] == 137
	assert row["left_at"] is not None


def test_left_seq_is_not_read_by_the_permission_layer() -> None:
	"""CQ-10 is unanswered and ``chat/permissions.py`` fails closed **deliberately**.

	A source assertion rather than a behavioural one, because the guarantee is about what the
	permission layer does *not* do: an inactive member sees nothing, and no rule anywhere
	widens that using the departure stamp. Stamping is not granting. If CQ-10 is ever settled
	in favour of retained access, this test is the thing that has to be deleted on purpose.
	"""
	permissions = Path(ms.__file__).resolve().parents[1] / "permissions.py"
	tree = ast.parse(permissions.read_text(encoding="utf-8"))

	# Docstrings are excluded and comments never reach the AST at all: permissions.py
	# *discusses* CQ-10 at length, which is exactly right and must not trip this. What is
	# banned is the field appearing in code — a live string in a query fragment, or an
	# attribute read off a row.
	docstrings = {
		id(node.body[0].value)
		for node in ast.walk(tree)
		if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
		and node.body
		and isinstance(node.body[0], ast.Expr)
		and isinstance(node.body[0].value, ast.Constant)
		and isinstance(node.body[0].value.value, str)
	}
	offenders: list[str] = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			if "left_seq" in node.value:
				offenders.append(f"string on line {node.lineno}")
		elif isinstance(node, ast.Attribute) and node.attr == "left_seq":
			offenders.append(f"attribute on line {node.lineno}")
		elif isinstance(node, ast.Name) and node.id == "left_seq":
			offenders.append(f"name on line {node.lineno}")

	assert not offenders, (
		f"chat/permissions.py reads left_seq in executable code ({'; '.join(offenders)}). CQ-10 — "
		"whether a departed member keeps access to what was said while they were present — is "
		"unanswered, and the permission layer fails closed on purpose. sync/membership.py stamps "
		"the boundary so the answer is cheap to implement later; stamping is not granting. Settle "
		"the question before reading the stamp."
	)


def test_leaving_a_space_removes_everyone_and_keeps_the_space(fake: FakeChatAPI) -> None:
	room, space = provisioned_room(fake)
	add_member(room, CAROL)
	api = client_for(fake)
	ms.sync_membership_to_google(room, client=api, quota=UnthrottledQuota())
	assert CAROL in fake.members_of(space)

	ms.leave_space(room, client=api, reason="linked document deleted")

	assert CAROL not in fake.members_of(space)
	assert member_row(room, CAROL)["is_active"] == 0
	assert member_row(room, CAROL)["left_at"] is not None
	assert fake.space(space) is not None, "leaving is not deleting"


# ---------------------------------------------------------------------------
# The Chat Provisioning Run controller
# ---------------------------------------------------------------------------


def test_dry_run_cannot_be_flipped_once_the_run_has_started() -> None:
	run = run_controller.ChatProvisioningRun(
		{"name": "r1", "mode": "Department", "dry_run": 0, "status": run_controller.STATUS_RUNNING}
	)
	run.__dict__["_before"] = {"dry_run": 1, "status": run_controller.STATUS_RUNNING}

	with pytest.raises(StubValidationError) as caught:
		run.validate()
	assert "Dry Run cannot be changed" in str(caught.value)


def test_dry_run_may_still_be_unticked_while_the_run_is_pending() -> None:
	run = run_controller.ChatProvisioningRun(
		{"name": "r1", "mode": "Department", "dry_run": 0, "status": run_controller.STATUS_PENDING}
	)
	run.__dict__["_before"] = {"dry_run": 1, "status": run_controller.STATUS_PENDING}
	run.validate()
	assert run.get("dry_run") == 0, "plan, read the log, untick, start — that IS the workflow"


def test_finished_at_is_aligned_with_the_terminal_statuses() -> None:
	done = run_controller.ChatProvisioningRun(
		{"name": "r2", "mode": "Team", "status": run_controller.STATUS_COMPLETED}
	)
	done.validate()
	assert done.get("finished_at") is not None

	paused = run_controller.ChatProvisioningRun(
		{
			"name": "r3",
			"mode": "Team",
			"status": run_controller.STATUS_PAUSED,
			"finished_at": datetime.datetime.now(),
		}
	)
	paused.validate()
	assert (
		paused.get("finished_at") is None
	), "an empty finished_at on a Paused run is how the resume path recognises one"


def test_counters_never_go_negative_and_errors_are_truncated() -> None:
	run = run_controller.ChatProvisioningRun(
		{
			"name": "r4",
			"mode": "Project",
			"status": run_controller.STATUS_RUNNING,
			"created_count": -3,
			"last_error": "x" * 5000,
		}
	)
	run.validate()
	assert run.get("created_count") == 0
	assert len(run.get("last_error")) == run_controller.MAX_ERROR_CHARS


def test_the_resumable_and_terminal_status_sets_do_not_overlap() -> None:
	assert not (run_controller.RESUMABLE_STATUSES & run_controller.TERMINAL_STATUSES)
	assert run_controller.STATUS_RUNNING in run_controller.RESUMABLE_STATUSES, (
		"a worker killed mid-unit leaves the row saying Running forever; excluding it from the "
		"resumable set is how a sweep silently stops halfway"
	)


# ---------------------------------------------------------------------------
# The lost race — the unique index is the guard, and a check-then-act is not
# ---------------------------------------------------------------------------


class LostRace:
	"""Commit an identical row in the window between an existence probe and its insert.

	The only faithful way to reproduce a TOCTOU in a single-threaded test. A second worker on a
	second connection commits first; the pass under test then issues the insert it had already
	decided on and meets the unique index. Everything about that is real except the scheduling —
	and the scheduling is precisely what a test cannot control in production either.

	Two deliberate properties. It fires **once**, so the recovery path is exercised rather than
	looped. And it copies the row the pass was about to write, so the collision lands on the
	exact index the design depends on rather than on something the test invented.
	"""

	def __init__(self, doctype: str, **match: Any) -> None:
		self.doctype = doctype
		self.match = match
		self.fired = False
		self._original = STORE.insert

	def __enter__(self) -> LostRace:
		def racing_insert(doctype: str, row: dict[str, Any]) -> str:
			if not self.fired and doctype == self.doctype and self._targets(row):
				self.fired = True
				self._original(doctype, dict(row))
			return self._original(doctype, row)

		STORE.insert = racing_insert  # type: ignore[method-assign]
		return self

	def __exit__(self, *_exc: Any) -> None:
		STORE.insert = self._original  # type: ignore[method-assign]

	def _targets(self, row: dict[str, Any]) -> bool:
		"""No filter means "the first insert of this doctype", which is what makes a
		continuation assertion deterministic — the collision lands on whichever row comes first
		and the remaining ones still have to be written."""
		return all(str(row.get(field) or "") == str(value or "") for field, value in self.match.items())


def test_two_concurrent_reconcile_passes_produce_one_member_row_and_no_exception() -> None:
	"""The headline property: a converging pass must be safe to run twice **at once**.

	``unique(room, user)`` is the guard. ``if member:`` in front of it is an optimisation, and an
	optimisation that is mistaken for a guard is a TOCTOU — reconciliation is exactly the
	workload that runs it twice in the same second, because a scheduled pass, a provisioning
	hand-off and an inbound event can all reach one room at once.

	``create_document_room`` seeds a room's membership one row at a time, so a collision on the
	first person is also the test of whether the *rest* of the pass survives it. Before the
	savepoint, the loser raised out of a whitelisted endpoint with the room created, one member
	row written by the winner and the others never attempted — a half-built room and an error on
	somebody's screen, from two people clicking the same button.
	"""
	STORE.insert("Project", {"name": "PRJ-RACE", "docstatus": 0})

	with LostRace("Chat Room Member") as race:
		room = pv.create_document_room("Project", "PRJ-RACE", members=[BOB, CAROL])

	assert race.fired, "the race never happened, so this test would be asserting nothing"
	assert room, "the losing pass has to return the room, not raise"

	members = sorted(str(row["user"]) for row in STORE.select("Chat Room Member", {"room": room}))
	assert members == sorted([ALICE, BOB, CAROL]), (
		"one row per person, and the pass that lost the race finished the work it had left. A "
		"converging diff that aborts on the row somebody else already wrote converges to nothing."
	)


def test_a_lost_race_on_an_inbound_join_is_success_not_an_exception(fake: FakeChatAPI) -> None:
	"""The same collision reached from the other direction: ``Bidirectional`` accepting a join.

	Pub/Sub is at-least-once, so the redelivery of one join event is the ordinary case, not the
	exotic one. The second writer's probe misses because the first has not committed yet, and
	the collision that follows **is** the outcome the writer wanted — the member is already
	there. Rule 2's "a duplicate on the index is SUCCESS" applied to membership.
	"""
	room, _space = provisioned_room(fake)
	STORE.update("Chat Room", room, {"membership_authority": ms.AUTHORITY_BIDIRECTIONAL})

	with LostRace("Chat Room Member", user=CAROL) as race:
		verdict = ms.apply_inbound_membership(
			room,
			in_google=True,
			email=CAROL,
			membership_name="spaces/AAAA/members/carol",
			client=client_for(fake),
		)

	assert race.fired
	assert verdict == ms.InboundMembershipVerdict.ACCEPT.value
	assert len(STORE.select("Chat Room Member", {"room": room, "user": CAROL})) == 1


def test_a_lost_race_on_the_document_room_returns_the_winners_room() -> None:
	"""``unique(linked_doctype, linked_document)`` is what makes "one room per document" true.

	Both callers already return the existing room when their probe hits, so the collision has
	exactly one correct answer — read back the winner — and raising it out of a button was never
	one of them.
	"""
	STORE.insert("Project", {"name": "PRJ-TWICE", "docstatus": 0})

	with LostRace("Chat Room") as race:
		room = pv.create_document_room("Project", "PRJ-TWICE")

	assert race.fired
	rooms = STORE.select("Chat Room", {"linked_doctype": "Project", "linked_document": "PRJ-TWICE"})
	assert len(rooms) == 1
	assert room == str(rooms[0]["name"]), "the loser returns the winner's room"
	assert STORE.select(
		"Chat Room Member", {"room": room}
	), "and goes on to seed the membership — a room with no members is a room nothing can relay into"


def test_a_lost_race_on_enrolment_counts_the_unit_as_existing() -> None:
	"""Enrolment is idempotent by design; the race must not be the one path that is not."""
	with LostRace("Chat Room") as race:
		result = pv.enroll_org_units("Department", ["engineering"])

	assert race.fired
	assert result["created"] == []
	assert result["existing"] == ["engineering"], (
		"the unit ends up enrolled exactly once, and the pass that lost reports it as existing "
		"rather than as created — a count that says otherwise is a count nobody can reconcile"
	)
	assert len(STORE.table("Chat Room")) == 1


def test_a_member_row_is_keyed_on_the_user_link_not_on_the_address_that_arrived() -> None:
	"""A guard that looks up a different key than the one the index enforces is not a guard.

	``Chat Room Member.user`` is a Link, so ``unique(room, user)`` compares the ``User`` row's
	name — whatever was typed when the account was made. An address reaching the membership
	module has usually been lower-cased by ``normalise_email`` on its way out of
	``diff_membership``. Probe one, insert the other, and the probe misses while the index does
	not.
	"""
	login = seed_user("Alice.Bradshaw@Example.com")
	room = make_room()

	first = ms.insert_room_member(room, "alice.bradshaw@example.com")
	assert first
	assert STORE.table("Chat Room Member")[first]["user"] == login, (
		"the stored Link is the User row's own name; a lower-cased copy of a mixed-case login is "
		"a broken link, not a tidier one"
	)

	assert ms.insert_room_member(room, "ALICE.BRADSHAW@EXAMPLE.COM") == first
	assert len(STORE.select("Chat Room Member", {"room": room})) == 1


def test_a_chat_member_with_no_erpnext_account_is_audited_never_invented(fake: FakeChatAPI) -> None:
	"""§4.H: a Chat member outside the roster is a real state, and a broken Link is not the fix.

	A ``Chat Room Member`` row whose ``user`` names no ``User`` is a row every later join
	silently drops, and in real Frappe the Link validation raises — out of the middle of an
	inbound pass, which is the abort this repair exists to remove.
	"""
	room, _space = provisioned_room(fake)
	STORE.update("Chat Room", room, {"membership_authority": ms.AUTHORITY_BIDIRECTIONAL})

	verdict = ms.apply_inbound_membership(
		room,
		in_google=True,
		email="stranger@partner.example",
		membership_name="spaces/AAAA/members/stranger",
		client=client_for(fake),
	)

	assert verdict == ms.InboundMembershipVerdict.ACCEPT.value
	assert STORE.select("Chat Room Member", {"room": room, "user": "stranger@partner.example"}) == []
	assert any(
		"no ERPNext account" in str(row.get("content") or "") for row in STORE.table("Comment").values()
	), "the join is recorded for a human rather than guessed at"


# ---------------------------------------------------------------------------
# The stub itself is not vacuous
# ---------------------------------------------------------------------------


def test_the_store_enforces_the_unique_indexes_the_design_depends_on() -> None:
	"""If the stub let a duplicate through, the idempotency test above would pass for the
	wrong reason — which is the failure mode ``TestDiscovery`` exists for, applied to a fixture."""
	make_room(gchat_space_name="spaces/AAAA")
	with pytest.raises(StubUniqueValidationError):
		make_room(gchat_space_name="spaces/AAAA")

	make_room(linked_doctype="Project", linked_document="PRJ-1")
	with pytest.raises(StubUniqueValidationError):
		make_room(linked_doctype="Project", linked_document="PRJ-1")

	# Two unlinked rooms must still coexist: the columns are NULL, and MariaDB permits
	# unlimited NULLs in a unique index while permitting exactly one empty string.
	make_room()
	make_room()


def test_unique_validation_error_is_not_duplicate_entry_error() -> None:
	"""PHASE2_VERIFIED.md §1.1: they share no base beyond ``Exception``, so code that catches
	only ``DuplicateEntryError`` would not catch what a non-PK unique index raises."""
	assert not issubclass(StubUniqueValidationError, StubDuplicateEntryError)
	assert not issubclass(StubDuplicateEntryError, StubUniqueValidationError)


def test_copy_is_used_nowhere_that_would_alias_a_stored_row() -> None:
	"""Guards the fixture, not the feature: ``room_row`` must hand back a copy.

	A test that mutated the store through a returned dict would pass while asserting nothing,
	because the assertion and the code under test would be looking at the same object.
	"""
	room = make_room()
	snapshot = room_row(room)
	snapshot["title"] = "mutated"
	assert STORE.table("Chat Room")[room]["title"] != "mutated"
	assert copy.copy(snapshot) is not snapshot
