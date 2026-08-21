"""Bench-free proof that a missed subscription renewal cannot silently lose inbound sync.

**Shape P: plain pytest functions.** It needs its own ``python -m pytest`` step in ``ci.yml``.
``python -m unittest`` cannot collect a file of plain functions — it collects nothing and
reports success — and this repo has already lost a suite that way for weeks.

What is being defended, and why it needs a suite of its own
------------------------------------------------------------
Google **permanently deletes** an expired Workspace Events subscription. It cannot be renewed
or reactivated; the only recovery is a brand-new subscription, and every event delivered while
it was dead is gone, because Workspace Events has no replay. Nothing raises when this happens:
no job fails, no HTTP call errors, no Error Log row appears. Inbound Chat sync just stops.

So the assertions here are mostly about things *not* being silent:

* the renewal clock is derived from the ``expireTime`` Google returned, never from
  ``Chat Settings.subscription_ttl_seconds`` — and the lead is clamped well under half the
  granted lifetime, so a short grant cannot turn the scheduler into a per-tick no-op loop;
* ``expirationReminder`` fires **twice** (T−12h and T−1h), so the second one must cost nothing
  while a *failed* first one must still be retried by the second;
* a renewal failure raises an operator alert on the **first** failure, not the third;
* ``USER_SCOPE_REVOKED`` gets its own branch, is never retried, and names the person;
* an expiry recreates the subscription **and** sweeps the gap;
* and the sweep recovers a backlog exactly once, in ``createTime`` order.

The frappe stub is an in-memory database, not a mock
-----------------------------------------------------
``_Store`` enforces the three unique indexes that carry the exactly-once guarantees —
``Chat Inbound Event.pubsub_message_id``, ``Chat Message.gchat_message_name`` and
``Chat Event Subscription.subscription_uid`` — and raises ``frappe.UniqueValidationError``,
**not** ``DuplicateEntryError``. That distinction is the whole point of the stub being real:
a non-primary-key unique index raises the second exception, the ADR says only the first, and
code that caught only ``DuplicateEntryError`` would fail open on every redelivery
(``PHASE2_VERIFIED.md`` §1.1). A mock that raised whatever the code caught would prove nothing.

The site's clock is deliberately **not** UTC (``SITE_UTC_OFFSET`` is −6 hours) so that a
module storing Google's RFC-3339 timestamp unconverted fails loudly here rather than being
wrong by the site offset in production — in the direction that makes an expiring subscription
look healthier than it is.

The Chat side is the real ``GoogleChatClient`` over ``FakeChatAPI``, so the reconciliation
tests exercise the real ``build_message_filter``, the real paging and the real retry loop
rather than a hand-written stand-in for them.

No bench, no network, no database. Nothing here contains real employee content.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Bench guard
# ---------------------------------------------------------------------------


def _real_frappe_is_installed() -> bool:
	"""Is an actual ``frappe`` package importable, as opposed to a stub another suite left?

	``find_spec`` answers from the import system, so a bare ``types.ModuleType`` sitting in
	``sys.modules`` has no ``__spec__`` and reads as absent — which is the answer we want.
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


# ---------------------------------------------------------------------------
# The stub: a small in-memory frappe
# ---------------------------------------------------------------------------

#: The site's timezone offset from UTC, as a deliberate non-zero. Frappe's ``now_datetime()``
#: is site-local naive; Google returns UTC. Anything that stores one as the other is wrong by
#: exactly this much, and with an offset of zero the bug would pass every test here.
SITE_UTC_OFFSET = timedelta(hours=-6)

#: Unique indexes the store enforces, per doctype. Only the ones that carry a correctness
#: guarantee — this is a test double, not a schema.
UNIQUE_COLUMNS: dict[str, tuple[str, ...]] = {
	"Chat Inbound Event": ("pubsub_message_id",),
	"Chat Message": ("gchat_message_name",),
	"Chat Event Subscription": ("subscription_uid",),
}


class _ValidationError(Exception):
	pass


class _UniqueValidationError(_ValidationError):
	"""What a **non-primary-key** unique index raises. The one the ADR forgets."""


class _IntegrityError(Exception):
	"""What the **driver** raises when an ``UPDATE`` hits a non-PK unique index.

	Deliberately *not* the same class as :class:`_UniqueValidationError`. frappe translates
	a unique collision raised by ``doc.insert()``, but ``frappe.db.set_value`` goes straight
	to SQL and the driver's own error propagates untranslated — so one constraint surfaces
	under two class names depending on which route reached it. Production hit this one:
	``IntegrityError: (1062, "Duplicate entry '...' for key 'subscription_uid'")``, on the
	``_apply_subscription`` write, and code catching only the other name would sail past it.
	"""


class _NameError(Exception):
	pass


class _DuplicateEntryError(_NameError):
	"""Primary-key collision only, i.e. a duplicate ``name``."""


class _Doc(dict):
	"""Just enough ``Document`` for attribute access and ``.insert()``."""

	def __init__(self, store: _Store, values: dict[str, Any]) -> None:
		super().__init__(values)
		self._store = store

	def __getattr__(self, item: str) -> Any:
		try:
			return self[item]
		except KeyError as exc:
			raise AttributeError(item) from exc

	def __setattr__(self, key: str, value: Any) -> None:
		if key.startswith("_"):
			super().__setattr__(key, value)
		else:
			self[key] = value

	def insert(self, ignore_permissions: bool = False) -> _Doc:
		self._store.insert(self)
		return self


class _Store:
	"""Tables of dicts, with the unique indexes that matter and nothing else."""

	def __init__(self) -> None:
		self.tables: dict[str, dict[str, dict[str, Any]]] = {}
		self._counter = 0

	def table(self, doctype: str) -> dict[str, dict[str, Any]]:
		return self.tables.setdefault(doctype, {})

	def new_name(self) -> str:
		self._counter += 1
		return f"row{self._counter:06d}"

	def insert(self, doc: _Doc) -> None:
		doctype = doc.get("doctype")
		if not doctype:
			raise _ValidationError("insert without a doctype")
		table = self.table(doctype)
		name = doc.get("name") or self.new_name()
		if name in table:
			raise _DuplicateEntryError(f"Duplicate entry '{name}' for key 'PRIMARY'")
		for column in UNIQUE_COLUMNS.get(doctype, ()):
			value = doc.get(column)
			if not value:
				continue
			for existing in table.values():
				if existing.get(column) == value:
					raise _UniqueValidationError(f"{column} must be unique ({value})")
		doc["name"] = name
		row = dict(doc)
		row.setdefault("creation", FRAPPE.utils.now_datetime())
		row.setdefault("modified", row["creation"])
		table[name] = row

	def rows(self, doctype: str) -> list[dict[str, Any]]:
		return list(self.table(doctype).values())


STORE = _Store()


def _matches(row: dict[str, Any], filters: Any) -> bool:
	if not filters:
		return True
	if isinstance(filters, str):
		return row.get("name") == filters
	for field, condition in dict(filters).items():
		value = row.get(field)
		if isinstance(condition, list | tuple) and len(condition) == 2:
			operator, operand = condition
			operator = str(operator).lower()
			if operator == "!=" and value == operand:
				return False
			if operator == "=" and value != operand:
				return False
			if operator == "in" and value not in operand:
				return False
			if operator == "not in" and value in operand:
				return False
			if operator == "is":
				if str(operand).lower() == "set" and not value:
					return False
				if str(operand).lower() == "not set" and value:
					return False
			if operator in (">", ">=", "<", "<=") and value is None:
				return False
		elif value != condition:
			return False
	return True


def _sort_key(field: str) -> Any:
	def key(row: dict[str, Any]) -> Any:
		value = row.get(field)
		if value is None:
			# NULLs first on ascending — mirroring "order by expire_time asc", where a row with
			# no expiry is the most urgent thing in the table, not the least.
			return (0, datetime.min)
		if isinstance(value, datetime):
			return (1, value)
		return (1, datetime.min if not isinstance(value, str) else _parse_datetime(value) or datetime.min)

	return key


def _parse_datetime(value: Any) -> datetime | None:
	if value in (None, ""):
		return None
	if isinstance(value, datetime):
		return value
	try:
		return datetime.fromisoformat(str(value))
	except ValueError:
		return None


def _get_all(
	doctype: str,
	filters: Any = None,
	fields: Any = None,
	order_by: str | None = None,
	limit: int | None = None,
	pluck: str | None = None,
	**_ignored: Any,
) -> list[Any]:
	rows = [row for row in STORE.rows(doctype) if _matches(row, filters)]
	if order_by:
		field, _, direction = str(order_by).partition(" ")
		rows.sort(key=_sort_key(field), reverse=direction.strip().lower() == "desc")
	if limit:
		rows = rows[: int(limit)]
	if pluck:
		return [row.get(pluck) for row in rows]
	if fields:
		return [{field: row.get(field) for field in fields} for row in rows]
	return [dict(row) for row in rows]


def _db_get_value(doctype: str, name: Any, fields: Any = None, as_dict: bool = False) -> Any:
	if isinstance(name, str):
		row = STORE.table(doctype).get(name)
	else:
		row = next((candidate for candidate in STORE.rows(doctype) if _matches(candidate, name)), None)
	if row is None:
		return None
	if isinstance(fields, str):
		return row.get(fields)
	values = {field: row.get(field) for field in (fields or row)}
	return values if as_dict else tuple(values.values())


def _db_set_value(doctype: str, name: str, values: Any, value: Any = None, **_kwargs: Any) -> None:
	row = STORE.table(doctype).get(name)
	if row is None:
		return
	incoming = dict(values) if isinstance(values, dict) else {values: value}

	# The unique indexes apply to UPDATE too, and modelling that is the whole reason this
	# stub caught nothing for the uid collision that took inbound sync down: the store
	# enforced uniqueness on insert only, so the one write that actually failed in
	# production — _apply_subscription's set_value — passed here every time.
	for column in UNIQUE_COLUMNS.get(doctype, ()):
		if column not in incoming:
			continue
		candidate = incoming[column]
		if not candidate:
			continue
		for other_name, other in STORE.table(doctype).items():
			if other_name != name and other.get(column) == candidate:
				raise _IntegrityError(1062, f"Duplicate entry '{candidate}' for key '{column}'")

	row.update(incoming)


def _db_sql(query: str, params: Any = None, **_kwargs: Any) -> list[Any]:
	"""Only the one statement this module issues: the delivery counter increment."""
	params = dict(params or {})
	if "Chat Event Subscription" in query and "event_count" in query:
		for row in STORE.rows("Chat Event Subscription"):
			if row.get("subscription_uid") == params.get("uid"):
				row["event_count"] = int(row.get("event_count") or 0) + 1
				row["last_event_at"] = params.get("when")
		return []
	raise AssertionError(f"the stub does not implement this SQL: {query.strip()[:80]}")


class _Cache:
	"""``frappe.cache()``: a dict with the four operations the chat package actually uses."""

	def __init__(self) -> None:
		self.data: dict[str, Any] = {}

	def set(self, key: str, value: Any, nx: bool = False, px: int | None = None) -> bool:
		if nx and key in self.data:
			return False
		self.data[key] = value
		return True

	def get(self, key: str) -> Any:
		return self.data.get(key)

	def delete(self, key: str) -> None:
		self.data.pop(key, None)

	def incrby(self, key: str, by: int = 1) -> int:
		self.data[key] = int(self.data.get(key) or 0) + int(by)
		return self.data[key]

	def mget(self, keys: list[str]) -> list[Any]:
		return [self.data.get(key) for key in keys]

	def eval(self, *_args: Any, **_kwargs: Any) -> list[int]:
		# Both Lua scripts read reply[0] as "allowed"; the space bucket also unpacks a wait
		# and a next-free watermark. Always-allow keeps the quota seam exercised without
		# reimplementing GCRA in a test double — ``test_chat_ratelimit.py`` owns that.
		return [1, 0, 0]


CACHE = _Cache()


def _now_datetime() -> datetime:
	"""Site-local naive now, at :data:`SITE_UTC_OFFSET` from UTC."""
	return datetime.fromtimestamp(time.time(), tz=timezone.utc).replace(tzinfo=None) + SITE_UTC_OFFSET


def _install_frappe_stub() -> types.ModuleType:
	frappe = types.ModuleType("frappe")

	frappe.ValidationError = _ValidationError
	frappe.UniqueValidationError = _UniqueValidationError
	frappe.DuplicateEntryError = _DuplicateEntryError
	frappe.local = types.SimpleNamespace(site="test.invalid", task_id=None)

	frappe.get_all = _get_all
	frappe.get_list = _get_all
	frappe.get_doc = lambda values, *a, **k: _Doc(STORE, dict(values))
	frappe.get_cached_doc = lambda doctype, *a, **k: SETTINGS if doctype == "Chat Settings" else None
	frappe.cache = lambda: CACHE
	frappe.logger = lambda *a, **k: types.SimpleNamespace(
		debug=lambda *x, **y: None, info=lambda *x, **y: None, warning=lambda *x, **y: None
	)
	frappe.log_error = lambda message="", title="": ERROR_LOG.append((title, message))
	frappe._ = lambda text, *a, **k: text

	def _throw(message: str, *args: Any, **kwargs: Any) -> None:
		raise _ValidationError(message)

	frappe.throw = _throw

	frappe.db = types.SimpleNamespace(
		get_value=_db_get_value,
		set_value=_db_set_value,
		sql=_db_sql,
		commit=lambda: None,
		rollback=lambda: None,
		exists=lambda doctype, filters=None: bool(_db_get_value(doctype, filters, "name")),
		table_exists=lambda doctype: True,
		has_column=lambda doctype, column: True,
	)

	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = _now_datetime
	utils.get_datetime = lambda value: value if isinstance(value, datetime) else _parse_datetime(value)
	utils.escape_html = lambda text: str(text).replace("<", "&lt;").replace(">", "&gt;")
	utils.cint = lambda value, default=0: int(value or default)
	utils.flt = lambda value, default=0.0: float(value or default)
	utils.cstr = lambda value, encoding=None: "" if value is None else str(value)
	utils.now = lambda: _now_datetime().isoformat(sep=" ")

	def _add_to_date(value: Any = None, **deltas: Any) -> datetime:
		base = utils.get_datetime(value) if value else _now_datetime()
		return (base or _now_datetime()) + timedelta(
			days=deltas.get("days", 0) + 365 * deltas.get("years", 0) + 30 * deltas.get("months", 0),
			hours=deltas.get("hours", 0),
			minutes=deltas.get("minutes", 0),
			seconds=deltas.get("seconds", 0),
		)

	utils.add_to_date = _add_to_date
	utils.add_days = lambda value, days: _add_to_date(value, days=days)
	# These four exist only so `sync/inbound.py` imports cleanly under the stub, which is what
	# makes test_the_inbound_processor_resolves_to_the_real_entry_point a real integration
	# check rather than an assertion about this file.
	sys.modules["frappe.utils"] = utils
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class _Document:
		pass

	document.Document = _Document
	model.document = document
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	frappe.model = model

	database_pkg = types.ModuleType("frappe.database")
	database = types.ModuleType("frappe.database.database")

	@contextlib.contextmanager
	def savepoint(catch: Any = Exception):
		"""Frappe's own shape: swallow ``catch``, propagate everything else."""
		try:
			yield
		except catch:
			pass

	database.savepoint = savepoint
	database_pkg.database = database
	sys.modules["frappe.database"] = database_pkg
	sys.modules["frappe.database.database"] = database
	frappe.database = database_pkg

	sys.modules["frappe"] = frappe
	return frappe


ERROR_LOG: list[tuple[str, str]] = []


class _Settings:
	"""``Chat Settings``, as much of it as this module reads."""

	def __init__(self) -> None:
		self.enabled = 1
		self.dry_run_mode = 0
		self.pause_inbound = 0
		self.pubsub_topic = "projects/erpnext-465317/topics/chat-events"
		self.subscription_ttl_seconds = 604800
		self.subscription_renew_before_seconds = 86400
		self.reconcile_window_minutes = 60
		self.project_message_writes_per_minute = 3000
		# The pilot runs with the whitelist ON, which is what makes allowed_users the roster
		# (see subscriptions._roster). With it OFF the roster derives from active chat members.
		self.restrict_to_whitelist = 1
		self.allowed_users: list[Any] = []


SETTINGS = _Settings()
FRAPPE = _install_frappe_stub()

# Imported only after the stub is in ``sys.modules``: every module below does
# ``import frappe`` at module scope, and on a bench-free runner that is the stub or it is an
# ImportError.
from erpnext_enhancements.chat import seams
from erpnext_enhancements.chat.gchat import client as chat_client
from erpnext_enhancements.chat.gchat import events_client as events
from erpnext_enhancements.chat.sync import decisions, reconcile, subscriptions
from erpnext_enhancements.chat.testing import fixtures
from erpnext_enhancements.chat.testing.fake_chat import (
	FakeChatAPI,
	FakeChatSettings,
	FakeClock,
)

USER = "alice@example.invalid"
OTHER_USER = "bob@example.invalid"
SUBSCRIPTION_ID = "sub-alice-0001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_state() -> Any:
	"""A fresh store, cache, settings and counter set per test. Autouse: leakage is a false green."""
	STORE.tables.clear()
	STORE._counter = 0
	CACHE.data.clear()
	ERROR_LOG.clear()
	SETTINGS.__init__()  # type: ignore[misc]
	seams.reset_counters()
	yield


def _close(actual: Any, expected: Any, *, seconds: float) -> bool:
	"""Datetime closeness. ``pytest.approx`` computes a relative tolerance and cannot take a
	``datetime``, so the comparison is written out rather than coerced into a float."""
	if actual is None or expected is None:
		return False
	return abs((actual - expected).total_seconds()) <= seconds


def utc_epoch_in(seconds: float) -> float:
	return time.time() + seconds


def rfc3339(epoch: float) -> str:
	return subscriptions.rfc3339_utc(epoch)


def subscription_payload(
	*,
	subscription_id: str = SUBSCRIPTION_ID,
	expire_epoch: float | None = None,
	state: str = "ACTIVE",
) -> dict[str, Any]:
	"""The operation envelope ``subscriptions.create``/``patch`` really answer with."""
	resource = {
		"name": f"subscriptions/{subscription_id}",
		"uid": subscription_id,
		"targetResource": chat_client.chat_target_resource(),
		"eventTypes": list(events.MESSAGE_EVENT_TYPES),
		"state": state,
		"expireTime": rfc3339(expire_epoch if expire_epoch is not None else utc_epoch_in(7 * 86400)),
	}
	return {"name": "operations/xyz", "done": True, "response": resource}


class _AlreadyExists(Exception):
	"""The 409 ``subscriptions.create`` answers with when the principal already owns one.

	Shaped like ``GoogleChatAPIError``: a ``status`` and a ``google_status``, and **no resource
	name anywhere**, which is the entire difficulty. Google says something exists and declines to
	say what, so the only route back to the uid is a list call.
	"""

	def __init__(self) -> None:
		super().__init__(
			"workspaceevents.subscriptions.create returned HTTP 409 ALREADY_EXISTS after 1 "
			"attempt(s): Subscription associated with the resource already exists."
		)
		self.status = 409
		self.google_status = "ALREADY_EXISTS"


class FakeEventsClient:
	"""A ``WorkspaceEventsClient`` double that answers with real parsed ``Subscription``s.

	The responses go through the real :func:`events_client.parse_subscription`, so the
	invariant that matters — a subscription cannot exist without the ``expireTime`` Google
	granted — is exercised rather than mocked away.
	"""

	def __init__(self, subject: str = USER) -> None:
		self.subject = subject
		self.calls: list[tuple[str, str]] = []
		self.failures: dict[str, Exception] = {}
		self.expire_epoch: float | None = None
		self.state = "ACTIVE"
		self.next_subscription_id = SUBSCRIPTION_ID
		#: What Google already holds for this principal, as ``subscriptions.list`` would return it.
		self.existing: list[dict[str, Any]] = []

	def fail_next(self, method: str, error: Exception) -> None:
		self.failures[method] = error

	def _answer(self, method: str, subscription_id: str) -> events.Subscription:
		self.calls.append((method, subscription_id))
		error = self.failures.pop(method, None)
		if error is not None:
			raise error
		return events.parse_subscription(
			subscription_payload(
				subscription_id=subscription_id, expire_epoch=self.expire_epoch, state=self.state
			)
		)

	def create_subscription(self, **kwargs: Any) -> events.Subscription:
		return self._answer("create", self.next_subscription_id)

	def patch_subscription(self, name: str, **kwargs: Any) -> events.Subscription:
		return self._answer("patch", subscriptions.subscription_uid_of(name))

	def reactivate_subscription(self, name: str) -> events.Subscription:
		return self._answer("reactivate", subscriptions.subscription_uid_of(name))

	def list_subscriptions(self, *, filter_expression: str, **_kwargs: Any) -> dict[str, Any]:
		self.calls.append(("list", filter_expression))
		return {"subscriptions": list(self.existing)}


class AlertRecorder:
	def __init__(self) -> None:
		self.alerts: list[subscriptions.SyncAlert] = []

	def __call__(self, alert: subscriptions.SyncAlert) -> None:
		self.alerts.append(alert)

	def keys(self) -> list[str]:
		return [alert.key for alert in self.alerts]

	def with_prefix(self, prefix: str) -> list[subscriptions.SyncAlert]:
		return [alert for alert in self.alerts if alert.key.startswith(prefix)]


def seed_subscription(**overrides: Any) -> str:
	now = FRAPPE.utils.now_datetime()
	values: dict[str, Any] = {
		"doctype": subscriptions.SUBSCRIPTION_DOCTYPE,
		"subscription_uid": SUBSCRIPTION_ID,
		"target_resource": chat_client.chat_target_resource(),
		"target_user": USER,
		"state": "ACTIVE",
		"suspension_reason": "",
		"event_types": "\n".join(events.MESSAGE_EVENT_TYPES),
		"expire_time": now + timedelta(days=7),
		"renew_after": now + timedelta(days=6),
		"last_renewed": now,
		"consecutive_failures": 0,
		"last_error": "",
		"event_count": 5,
		"last_event_at": now,
	}
	values.update(overrides)
	return FRAPPE.get_doc(values).insert().name


# ---------------------------------------------------------------------------
# Pure arithmetic — the clamp and the clock
# ---------------------------------------------------------------------------


def test_the_renewal_lead_is_clamped_well_under_the_granted_lifetime() -> None:
	"""A 24-hour lead against a 4-hour grant would renew forever; the clamp is what stops it."""
	seven_days = 7 * 86400
	assert (
		subscriptions.renewal_lead_seconds(seven_days, 86400) == 86400
	), "a 24-hour lead fits comfortably inside a 7-day grant and must be honoured verbatim"

	four_hours = 4 * 3600
	clamped = subscriptions.renewal_lead_seconds(four_hours, 86400)
	assert clamped < four_hours / 2, (
		"the lead must stay well under half the granted lifetime; an unclamped 24-hour lead "
		"against a 4-hour grant puts renew_after 20 hours in the past and the scheduler then "
		"patches the same subscription on every single pass, forever"
	)
	assert clamped == pytest.approx(four_hours * subscriptions.MAX_RENEW_LEAD_FRACTION)


def test_a_nonsense_lifetime_does_not_raise_inside_the_scheduler() -> None:
	assert subscriptions.renewal_lead_seconds(0, 86400) == 0.0
	assert subscriptions.renewal_lead_seconds(-1, 86400) == 0.0


def test_the_google_timestamp_is_stored_as_site_local_not_utc() -> None:
	"""The conversion chat/health.py's VERIFY was about. Settled: site-local."""
	now_epoch = time.time()
	now_local = FRAPPE.utils.now_datetime()
	target = now_epoch + 3600

	local = subscriptions.local_datetime_from_epoch(target, now_epoch=now_epoch, now_local=now_local)
	utc = datetime.fromtimestamp(target, tz=timezone.utc).replace(tzinfo=None)

	assert _close(local, utc + SITE_UTC_OFFSET, seconds=1), (
		"expire_time must be stored on the same clock frappe.utils.now_datetime() reads, "
		"because chat/health.py compares the two. Storing Google's UTC value unconverted is "
		"wrong by the site offset in the direction that makes an expiring subscription look "
		"healthier than it is."
	)
	assert local != utc, "SITE_UTC_OFFSET is zero, so this test proves nothing — fix the stub"

	back = subscriptions.epoch_from_local_datetime(local, now_epoch=now_epoch, now_local=now_local)
	assert back == pytest.approx(target, abs=1.0)


def test_rfc3339_utc_is_the_shape_the_message_filter_accepts() -> None:
	stamp = subscriptions.rfc3339_utc(1_786_233_600)
	assert stamp.endswith("Z") and "." in stamp
	# The real builder is the arbiter: it refuses anything without an explicit offset.
	assert chat_client.build_message_filter(create_time_after=stamp) == f'createTime > "{stamp}"'


# ---------------------------------------------------------------------------
# The renewal path
# ---------------------------------------------------------------------------


def test_renew_after_is_derived_from_the_returned_expire_time_not_the_configured_ttl() -> None:
	now = FRAPPE.utils.now_datetime()
	row = seed_subscription(renew_after=now - timedelta(minutes=1))
	client = FakeEventsClient()
	# Google grants five days, not the seven Chat Settings asked for. Five is chosen so the
	# 24-hour lead still fits inside MAX_RENEW_LEAD_FRACTION and is therefore honoured
	# verbatim — the clamp has its own test, and this one is about where expire_time came from.
	client.expire_epoch = utc_epoch_in(5 * 86400)
	alerts = AlertRecorder()

	summary = subscriptions.renew_due_subscriptions(client_factory=lambda subject: client, alert=alerts)

	assert summary["renewed"] == 1, summary
	stored = STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)[row]
	expected_expiry = now + timedelta(days=5)
	assert _close(stored["expire_time"], expected_expiry, seconds=60)
	assert _close(stored["renew_after"], stored["expire_time"] - timedelta(seconds=86400), seconds=60), (
		"renew_after must be expire_time minus the lead, and expire_time must be what Google "
		"returned — not now() plus Chat Settings.subscription_ttl_seconds"
	)
	assert stored["consecutive_failures"] == 0
	assert seams.counters()["subscription_renewals"] == 1


def test_the_expiration_reminder_fires_twice_and_renews_once() -> None:
	"""T−12h and T−1h. The second must cost nothing."""
	now = FRAPPE.utils.now_datetime()
	seed_subscription(renew_after=now - timedelta(minutes=1))
	client = FakeEventsClient()
	alerts = AlertRecorder()

	reminder = decisions.parse_pubsub_envelope(
		fixtures.subscription_expiration_reminder_event(subscription_id=SUBSCRIPTION_ID)
	)

	first = subscriptions.handle_lifecycle_event(
		reminder, client_factory=lambda subject: client, alert=alerts
	)
	# Drop the short-lived Redis claim, the way its TTL would between T-12h and T-1h. What is
	# being proved here is the DURABLE guard — renew_after in the future — not the claim.
	CACHE.data.clear()
	second = subscriptions.handle_lifecycle_event(
		reminder, client_factory=lambda subject: client, alert=alerts
	)

	assert first == "renewed"
	assert second == "already_renewed", (
		"the reminder is delivered at T-12h AND T-1h; the second delivery must not spend a "
		"second subscriptions.patch for the whole roster"
	)
	assert [call[0] for call in client.calls] == ["patch"]


def test_a_reminder_still_retries_after_a_failed_renewal() -> None:
	"""Idempotent must not mean 'only ever once'. A failed T−12h has to be retried at T−1h."""
	now = FRAPPE.utils.now_datetime()
	seed_subscription(renew_after=now - timedelta(minutes=1))
	client = FakeEventsClient()
	client.fail_next("patch", RuntimeError("503 from Google"))
	alerts = AlertRecorder()
	reminder = decisions.parse_pubsub_envelope(
		fixtures.subscription_expiration_reminder_event(subscription_id=SUBSCRIPTION_ID)
	)

	assert (
		subscriptions.handle_lifecycle_event(reminder, client_factory=lambda subject: client, alert=alerts)
		== "failed"
	)
	# The claim is per-attempt bookkeeping, not a lockout: clear it the way its TTL would.
	CACHE.data.clear()
	assert (
		subscriptions.handle_lifecycle_event(reminder, client_factory=lambda subject: client, alert=alerts)
		== "renewed"
	)


def test_a_renewal_failure_raises_an_operator_alert_on_the_first_failure() -> None:
	"""Required by §4.J.5. One failure is already a subscription that will be deleted on schedule."""
	now = FRAPPE.utils.now_datetime()
	row = seed_subscription(renew_after=now - timedelta(minutes=1))
	client = FakeEventsClient()
	client.fail_next("patch", RuntimeError("500 Internal Error"))
	alerts = AlertRecorder()

	summary = subscriptions.renew_due_subscriptions(client_factory=lambda subject: client, alert=alerts)

	assert summary["failed"] == 1, summary
	raised = alerts.with_prefix("subscription-renew-failed:")
	assert raised, f"no renewal-failure alert was raised; keys were {alerts.keys()}"
	assert USER in raised[0].message, "the alert must name the coworker whose sync is at risk"
	assert STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)[row]["consecutive_failures"] == 1
	assert seams.counters()["subscription_failures"] >= 1


def test_a_renewal_failure_never_leaks_a_bearer_token_into_last_error() -> None:
	now = FRAPPE.utils.now_datetime()
	row = seed_subscription(renew_after=now - timedelta(minutes=1))
	client = FakeEventsClient()
	client.fail_next("patch", RuntimeError("401 with header Authorization: Bearer ya29.SECRETVALUE"))

	subscriptions.renew_due_subscriptions(client_factory=lambda subject: client, alert=AlertRecorder())

	stored = STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)[row]["last_error"]
	assert "SECRETVALUE" not in stored, (
		"last_error is written from a Google error string, which can quote the request that was "
		"rejected — and that request carried a bearer token"
	)


def test_one_failing_subscription_does_not_stop_the_others_being_renewed() -> None:
	now = FRAPPE.utils.now_datetime()
	seed_subscription(renew_after=now - timedelta(minutes=1))
	seed_subscription(
		subscription_uid="sub-bob-0001", target_user=OTHER_USER, renew_after=now - timedelta(minutes=1)
	)

	clients = {USER: FakeEventsClient(USER), OTHER_USER: FakeEventsClient(OTHER_USER)}
	clients[USER].fail_next("patch", RuntimeError("boom"))

	summary = subscriptions.renew_due_subscriptions(
		client_factory=lambda subject: clients[subject], alert=AlertRecorder()
	)

	assert summary["failed"] == 1 and summary["renewed"] == 1, summary


# ---------------------------------------------------------------------------
# Suspension, revocation and expiry
# ---------------------------------------------------------------------------


def test_user_scope_revoked_alerts_by_name_and_is_never_retried() -> None:
	"""§4.J.4. One person's revocation is the failure this whole DocType exists to make visible."""
	seed_subscription(state="ACTIVE")
	client = FakeEventsClient()
	alerts = AlertRecorder()

	event = decisions.parse_pubsub_envelope(
		fixtures.subscription_suspended_event(
			subscription_id=SUBSCRIPTION_ID, error_type="USER_SCOPE_REVOKED"
		)
	)
	verdict = subscriptions.handle_lifecycle_event(event, client_factory=lambda subject: client, alert=alerts)

	assert verdict == "revoked"
	assert client.calls == [], "a revoked grant must not be retried; nobody but that person can fix it"
	revoked = alerts.with_prefix("subscription-scope-revoked:")
	assert revoked, f"expected a scope-revoked alert; got {alerts.keys()}"
	assert (
		USER in revoked[0].subject and USER in revoked[0].message
	), "the alert has to name the person — that name is the entire actionable content"


def test_a_retryable_suspension_is_reactivated() -> None:
	seed_subscription(state="ACTIVE")
	client = FakeEventsClient()
	alerts = AlertRecorder()

	event = decisions.parse_pubsub_envelope(
		fixtures.subscription_suspended_event(
			subscription_id=SUBSCRIPTION_ID, error_type="ENDPOINT_PERMISSION_DENIED"
		)
	)
	verdict = subscriptions.handle_lifecycle_event(event, client_factory=lambda subject: client, alert=alerts)

	assert verdict == "reactivated"
	assert ("reactivate", SUBSCRIPTION_ID) in client.calls
	row = next(iter(STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE).values()))
	assert row["state"] == "ACTIVE" and not row["suspension_reason"]


def test_an_expired_subscription_is_recreated_and_the_gap_is_swept(monkeypatch: Any) -> None:
	"""Expiry is terminal: nothing to patch, nothing to reactivate. Create, then sweep."""
	now = FRAPPE.utils.now_datetime()
	old = seed_subscription(expire_time=now - timedelta(minutes=5), renew_after=now - timedelta(days=1))
	client = FakeEventsClient()
	# NOT a fresh id. Workspace Events subscription ids are deterministic per
	# (authorizing user, target resource) — base64 of the id segment reads
	# "s:-:<google user id>:<app>" — so a recreate is handed back the *same* name. This
	# test used to set next_subscription_id = "sub-alice-0002", and that single invented
	# line is what hid a table-wide unique collision that took inbound sync down for two
	# coworkers for a day (v1.340.1). The default already is SUBSCRIPTION_ID; it is
	# restated here because the whole point of the test is that it repeats.
	client.next_subscription_id = SUBSCRIPTION_ID
	alerts = AlertRecorder()

	swept: list[dict[str, Any]] = []
	monkeypatch.setattr(
		reconcile,
		"reconcile_rooms_for_user",
		lambda user, **kwargs: swept.append({"user": user, **kwargs}) or {"status": "ok"},
	)

	event = decisions.parse_pubsub_envelope(
		fixtures.subscription_expired_event(subscription_id=SUBSCRIPTION_ID)
	)
	verdict = subscriptions.handle_lifecycle_event(event, client_factory=lambda subject: client, alert=alerts)

	assert verdict == "recreated"
	assert STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)[old]["state"] == "DELETED"
	fresh = subscriptions.active_subscription_for(USER)
	assert fresh and fresh["subscription_uid"] == SUBSCRIPTION_ID and fresh["state"] == "ACTIVE"
	assert fresh["name"] != old, "the replacement is a new row, so the gap stays on the record"
	assert STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)[old]["subscription_uid"] in (None, ""), (
		"the superseded row has to release the uid: it is unique table-wide and the "
		"replacement arrives carrying the same one"
	)
	assert swept and swept[0]["user"] == USER, (
		"recreating the subscription stops the gap growing; only the sweep recovers what was "
		"said while it was dead, and Workspace Events has no replay"
	)
	assert swept[0]["reason"] == "subscription-expired"
	assert alerts.with_prefix("subscription-expired:")


def test_a_lapsed_row_is_recreated_by_the_scheduler_without_any_event(monkeypatch: Any) -> None:
	"""The reminder is the backstop, not the trigger: the scheduler must find this by itself."""
	now = FRAPPE.utils.now_datetime()
	seed_subscription(expire_time=now - timedelta(hours=1), renew_after=now - timedelta(days=1))
	monkeypatch.setattr(reconcile, "reconcile_rooms_for_user", lambda user, **kwargs: {"status": "ok"})

	client = FakeEventsClient()
	# Deterministic again — see the note in the lifecycle test above.
	client.next_subscription_id = SUBSCRIPTION_ID
	summary = subscriptions.renew_due_subscriptions(
		client_factory=lambda subject: client, alert=AlertRecorder()
	)

	assert summary["recreated"] == 1, summary
	assert [call[0] for call in client.calls] == ["create"]


def test_a_recreate_survives_a_uid_a_superseded_row_still_claims(monkeypatch: Any) -> None:
	"""The production deadlock, reproduced: a dead row holding the uid its replacement needs.

	Rows written before v1.340.1 kept ``subscription_uid`` when they were marked ``DELETED``,
	and the index is unique table-wide — so the replacement, which Google names identically,
	could not be written at all. 27 consecutive failures for two coworkers, and because the
	failing write sits upstream of the gap sweep, not one of the lost messages was fetched.
	"""
	now = FRAPPE.utils.now_datetime()
	stale = seed_subscription(
		state="DELETED",
		subscription_uid=SUBSCRIPTION_ID,
		expire_time=now - timedelta(days=2),
	)
	live = seed_subscription(
		subscription_uid="sub-alice-superseded",
		expire_time=now - timedelta(minutes=5),
		renew_after=now - timedelta(days=1),
	)
	swept: list[str] = []
	monkeypatch.setattr(
		reconcile,
		"reconcile_rooms_for_user",
		lambda user, **kwargs: swept.append(user) or {"status": "ok"},
	)
	client = FakeEventsClient()
	client.next_subscription_id = SUBSCRIPTION_ID

	summary = subscriptions.renew_due_subscriptions(
		client_factory=lambda subject: client, alert=AlertRecorder()
	)

	assert summary["recreated"] == 1, summary
	table = STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)
	assert table[stale]["subscription_uid"] in (
		None,
		"",
	), "the superseded row has to give the uid up; nothing else can ever claim it"
	assert table[live]["state"] == "DELETED"
	fresh = subscriptions.active_subscription_for(USER)
	assert fresh and fresh["subscription_uid"] == SUBSCRIPTION_ID
	assert swept == [USER], "and the gap sweep has to be reached, which is the point of all of it"


def test_a_redelivered_expiry_does_not_supersede_the_replacement(monkeypatch: Any) -> None:
	"""Pub/Sub redelivers, so the second `expired` must not replace the replacement.

	This is what made one failed recreate unbounded rather than a single error: ``_rows``
	skips ``DELETED`` so the scheduler let the dead row go, but ``_row_by_uid`` did not filter
	on state, so every redelivery found it again and re-ran the identical doomed recreate.
	"""
	now = FRAPPE.utils.now_datetime()
	seed_subscription(expire_time=now - timedelta(minutes=5), renew_after=now - timedelta(days=1))
	monkeypatch.setattr(reconcile, "reconcile_rooms_for_user", lambda user, **kwargs: {"status": "ok"})
	client = FakeEventsClient()
	event = decisions.parse_pubsub_envelope(
		fixtures.subscription_expired_event(subscription_id=SUBSCRIPTION_ID)
	)

	first = subscriptions.handle_lifecycle_event(
		event, client_factory=lambda subject: client, alert=AlertRecorder()
	)
	rows_after_first = len(STORE.rows(subscriptions.SUBSCRIPTION_DOCTYPE))
	second = subscriptions.handle_lifecycle_event(
		event, client_factory=lambda subject: client, alert=AlertRecorder()
	)

	assert first == "recreated"
	assert second == "already_recreated"
	assert (
		len(STORE.rows(subscriptions.SUBSCRIPTION_DOCTYPE)) == rows_after_first
	), "a redelivered expiry must not mint another row"
	assert [call[0] for call in client.calls] == ["create"], "nor call Google a second time"


def test_a_failed_create_reuses_its_placeholder_instead_of_leaking_another() -> None:
	"""The row is committed before the Google call, so a failure leaves one behind — reuse it.

	Leaking one per attempt is how a stuck recreate produced a pile of dead rows per coworker,
	each of which the scheduler then classified ``renew`` on every pass, forever, because a
	placeholder has no ``expire_time`` to reason about.
	"""
	client = FakeEventsClient()
	client.fail_next("create", RuntimeError("Google said no"))
	first = subscriptions.ensure_subscription_for_user(
		USER, client_factory=lambda subject: client, alert=AlertRecorder()
	)
	after_first = len(STORE.rows(subscriptions.SUBSCRIPTION_DOCTYPE))

	client.fail_next("create", RuntimeError("Google said no again"))
	subscriptions.ensure_subscription_for_user(
		USER, client_factory=lambda subject: client, alert=AlertRecorder()
	)

	assert first["status"] == "failed"
	assert after_first == 1, "the placeholder is committed before the call, on purpose"
	assert (
		len(STORE.rows(subscriptions.SUBSCRIPTION_DOCTYPE)) == after_first
	), "the second attempt has to adopt the abandoned placeholder, not add to the pile"


def test_recovery_recreates_a_coworker_with_no_row_at_all_and_sweeps_the_gap(monkeypatch: Any) -> None:
	"""The state prod was left in: every row DELETED, nobody covered, nothing to renew.

	``renew_due_subscriptions`` cannot fix this — ``_rows`` skips ``DELETED``, so the hourly job
	sees an empty list and does nothing while both coworkers stay uncovered. Before v1.340.2 the
	only repair was a hand-typed ``bench execute``.
	"""
	now = FRAPPE.utils.now_datetime()
	lapsed = now - timedelta(hours=2)
	seed_subscription(state="DELETED", subscription_uid=None, expire_time=lapsed)
	swept: list[dict[str, Any]] = []
	monkeypatch.setattr(
		reconcile,
		"reconcile_rooms_for_user",
		lambda user, **kwargs: swept.append({"user": user, **kwargs}) or {"status": "ok"},
	)
	client = FakeEventsClient()

	idle = subscriptions.renew_due_subscriptions(client_factory=lambda subject: client, alert=AlertRecorder())
	assert idle["checked"] == 0, "the hourly job cannot see a superseded row, which is the point"

	summary = subscriptions.recover_subscription_for(
		USER, client_factory=lambda subject: client, alert=AlertRecorder()
	)

	assert summary["status"] == "ok", summary
	assert [entry["user"] for entry in summary["recovered"]] == [USER]
	fresh = subscriptions.active_subscription_for(USER)
	assert fresh and fresh["state"] == "ACTIVE"
	assert swept and swept[0]["user"] == USER, "the gap has to be swept, not just the row replaced"
	assert _close(
		swept[0]["since"], lapsed, seconds=1
	), "the window comes from the superseded row, so it covers exactly the dead interval"


def test_a_create_that_bounces_with_409_adopts_what_google_already_has(monkeypatch: Any) -> None:
	"""Prod, 2026-08-20: recovery failed on both coworkers with 409 ALREADY_EXISTS.

	The subscriptions were never gone. Renewal had been broken for months (v1.340.2) so they ran
	to expiry, the recreate deadlocked on the uid (v1.340.1), and releasing that uid to break the
	deadlock discarded the only pointer we had to subscriptions Google still held. Creating
	cannot fix that — Google allows one per (principal, target) and refuses the duplicate without
	naming the incumbent. The list call is the way back.
	"""
	now = FRAPPE.utils.now_datetime()
	lapsed = now - timedelta(hours=2)
	seed_subscription(state="DELETED", subscription_uid=None, expire_time=lapsed)
	monkeypatch.setattr(reconcile, "reconcile_rooms_for_user", lambda user, **kwargs: {"status": "ok"})
	client = FakeEventsClient()
	client.fail_next("create", _AlreadyExists())
	client.existing = [subscription_payload(subscription_id=SUBSCRIPTION_ID)]

	summary = subscriptions.recover_subscription_for(
		USER, client_factory=lambda subject: client, alert=AlertRecorder()
	)

	assert summary["status"] == "ok", summary
	assert [entry["user"] for entry in summary["recovered"]] == [USER]
	fresh = subscriptions.active_subscription_for(USER)
	assert fresh and fresh["state"] == "ACTIVE"
	assert fresh["subscription_uid"] == SUBSCRIPTION_ID, "the lost pointer is what we came back for"
	assert not fresh["last_renewed"], (
		"an adoption renews nothing, and a last_renewed that lies about the one event it records "
		"is what hid the broken ttl patch for months"
	)
	assert [call[0] for call in client.calls] == ["create", "list"]


def test_a_create_that_bounces_with_no_recoverable_subscription_still_fails() -> None:
	"""A 409 whose subscription the list cannot produce is a real failure, not a silent pass.

	Adoption must not become a way for a broken create to report success. If Google says one
	exists and then does not return it — a different target, or an entry with no ``expireTime``,
	which ``parse_subscription`` refuses on purpose — the coworker is still uncovered and somebody
	has to be told.
	"""
	client = FakeEventsClient()
	client.fail_next("create", _AlreadyExists())
	client.existing = []
	alerts = AlertRecorder()

	summary = subscriptions.ensure_subscription_for_user(
		USER, client_factory=lambda subject: client, alert=alerts
	)

	assert summary["status"] == "failed", summary
	assert "ALREADY_EXISTS" in summary["reason"]
	assert alerts.with_prefix("subscription-create-failed:")


def test_recovery_is_idempotent_and_leaves_a_covered_coworker_alone() -> None:
	"""Running it twice, or against a healthy roster, must not mint a second subscription."""
	seed_subscription()
	client = FakeEventsClient()

	summary = subscriptions.recover_subscription_for(
		USER, client_factory=lambda subject: client, alert=AlertRecorder()
	)

	assert summary["skipped"] == [USER], summary
	assert summary["recovered"] == []
	assert client.calls == [], "an ACTIVE subscription must not be touched"
	assert len(STORE.rows(subscriptions.SUBSCRIPTION_DOCTYPE)) == 1


# ---------------------------------------------------------------------------
# The master switch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	"field, value",
	[("enabled", 0), ("dry_run_mode", 1)],
	ids=["chat disabled", "dry run"],
)
def test_everything_is_a_no_op_when_the_master_switch_is_off(field: str, value: int) -> None:
	setattr(SETTINGS, field, value)
	now = FRAPPE.utils.now_datetime()
	seed_subscription(renew_after=now - timedelta(days=1))
	client = FakeEventsClient()
	alerts = AlertRecorder()

	summary = subscriptions.renew_due_subscriptions(client_factory=lambda subject: client, alert=alerts)

	assert summary["status"] == "skipped" and summary["checked"] == 0, summary
	assert client.calls == [] and alerts.alerts == []
	assert subscriptions.ensure_subscription_for_user(USER, client_factory=lambda s: client)["status"] == (
		"skipped"
	)
	assert reconcile.reconcile_due_rooms()["status"] == "skipped"


def test_the_reconciliation_sweep_stops_when_inbound_is_paused() -> None:
	"""Unlike renewal: the sweep's whole output is inbound ingest, and the watermark does not move."""
	SETTINGS.pause_inbound = 1
	assert reconcile.reconcile_due_rooms()["status"] == "skipped"


# ---------------------------------------------------------------------------
# Health checks and alerting
# ---------------------------------------------------------------------------


def test_a_coworker_with_no_subscription_is_alerted_even_though_there_is_no_row() -> None:
	"""The check that needs no row to fire, because the failure it catches is the absence of one."""
	SETTINGS.allowed_users = [types.SimpleNamespace(user=USER), types.SimpleNamespace(user=OTHER_USER)]
	seed_subscription()  # alice is covered; bob is not
	alerts = AlertRecorder()

	subscriptions.check_subscription_health(alert=alerts)

	missing = alerts.with_prefix("subscription-missing:")
	assert [alert.user for alert in missing] == [OTHER_USER], alerts.keys()


def test_roster_derives_from_active_chat_members_when_whitelist_is_off() -> None:
	"""With ``restrict_to_whitelist`` OFF, coverage must follow access, not the stale pilot
	list — everyone may use chat, so the roster is the actual chat population (active
	``Chat Room Member`` users). Otherwise a coworker not on the list gets no subscription and
	their Google-Chat-origin messages never reach ERPNext, with no missing-subscription alert."""
	SETTINGS.restrict_to_whitelist = 0
	SETTINGS.allowed_users = [types.SimpleNamespace(user=USER)]  # stale pilot list, now ignored
	members = STORE.table("Chat Room Member")
	members["m1"] = {"name": "m1", "room": "R1", "user": OTHER_USER, "is_active": 1}
	members["m2"] = {"name": "m2", "room": "R2", "user": "carol@example.invalid", "is_active": 1}
	members["m3"] = {"name": "m3", "room": "R3", "user": OTHER_USER, "is_active": 1}  # dup user
	members["m4"] = {"name": "m4", "room": "R1", "user": "dave@example.invalid", "is_active": 0}

	roster = subscriptions._roster()

	assert sorted(roster) == sorted([OTHER_USER, "carol@example.invalid"])
	assert USER not in roster  # the whitelist no longer defines coverage
	assert roster.count(OTHER_USER) == 1  # deduped across rooms


def test_health_alerts_on_expiry_inside_the_renewal_window_and_on_a_lapsed_row() -> None:
	now = FRAPPE.utils.now_datetime()
	seed_subscription(subscription_uid="sub-soon", expire_time=now + timedelta(hours=2))
	seed_subscription(
		subscription_uid="sub-gone", target_user=OTHER_USER, expire_time=now - timedelta(hours=2)
	)
	alerts = AlertRecorder()

	subscriptions.check_subscription_health(alert=alerts)

	assert alerts.with_prefix("subscription-expiring:")
	lapsed = alerts.with_prefix("subscription-lapsed:")
	assert lapsed and lapsed[0].severity == subscriptions.ALERT_SEVERITY_ALARM


def test_an_active_subscription_that_has_never_delivered_is_alerted() -> None:
	"""State ACTIVE, expiry healthy, zero events. Identical to a quiet week from the outside."""
	now = FRAPPE.utils.now_datetime()
	old = now - timedelta(days=10)
	seed_subscription(event_count=42)
	silent = seed_subscription(
		subscription_uid="sub-silent", target_user=OTHER_USER, event_count=0, last_event_at=None
	)
	STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)[silent]["creation"] = old
	alerts = AlertRecorder()

	subscriptions.check_subscription_health(alert=alerts)

	assert alerts.with_prefix("subscription-silent:"), alerts.keys()


def _seed_system_managers() -> None:
	for name, parent in (("hr1", "manager@example.invalid"), ("hr2", "Administrator")):
		STORE.table("Has Role")[name] = {
			"name": name,
			"role": "System Manager",
			"parenttype": "User",
			"parent": parent,
		}


def _patch_governance_sink(monkeypatch, *, recorded: bool) -> list:
	"""Stand in for the §4.H alert board, which needs a database this suite does not have.

	Patched rather than left live so this test stays about what its name says — the *legacy*
	sink's cooldown and its one delivery. With the real path in place every assertion here
	would instead be measuring `raise_alert`'s own failure fallback under a frappe stub.
	"""
	calls: list = []

	def fake(alert):
		calls.append(alert)
		return recorded

	monkeypatch.setattr(subscriptions, "_to_governance_alerts", fake)
	return calls


def test_the_default_alert_sink_writes_a_notification_and_respects_the_cooldown(monkeypatch) -> None:
	_seed_system_managers()
	calls = _patch_governance_sink(monkeypatch, recorded=True)
	alert = subscriptions.SyncAlert(
		key="unit-test", severity=subscriptions.ALERT_SEVERITY_ALARM, subject="s", message="m"
	)

	subscriptions.raise_operator_alert(alert)
	subscriptions.raise_operator_alert(alert)

	logs = STORE.rows("Notification Log")
	assert [row["for_user"] for row in logs] == ["manager@example.invalid"], (
		"one delivery, to the one System Manager who is not Administrator — a repeat every hour "
		"is unsubscribed from within a week and then the next real outage is invisible"
	)
	assert len(ERROR_LOG) == 0, (
		"the Error Log row moved to the §4.H alert path in v1.292.0. Writing one here as well "
		"would put two rows in the same table for one event, which is the duplication that "
		"consolidation exists to remove"
	)
	assert len(calls) == 2, (
		"the alert board is fed ABOVE the six-hour cooldown, deliberately. Below it, the "
		"legacy claim would decide how many occurrences the board counted — and an occurrence "
		"count that is really a delivery count makes the doubling schedule meaningless"
	)


def test_the_error_log_is_written_when_the_alert_board_could_not_record(monkeypatch) -> None:
	"""The fallback, and the only case where dropping the Error Log would lose the event."""
	_seed_system_managers()
	_patch_governance_sink(monkeypatch, recorded=False)
	alert = subscriptions.SyncAlert(
		key="unit-test-fallback",
		severity=subscriptions.ALERT_SEVERITY_ALARM,
		subject="s",
		message="m",
	)

	subscriptions.raise_operator_alert(alert)

	assert len(ERROR_LOG) == 1
	assert "unit-test-fallback" in ERROR_LOG[0][1]


def test_delivery_bookkeeping_increments_the_event_count() -> None:
	row = seed_subscription(event_count=0)
	subscriptions.note_event_delivered(f"subscriptions/{SUBSCRIPTION_ID}")
	assert STORE.table(subscriptions.SUBSCRIPTION_DOCTYPE)[row]["event_count"] == 1


# ---------------------------------------------------------------------------
# The reconciliation sweep, against the real Chat client over the fake API
# ---------------------------------------------------------------------------


def build_chat_world(*, message_count: int, minutes_ago: int = 60) -> tuple[FakeChatAPI, Any, str, str]:
	"""A space with ``message_count`` messages, a mirrored room, and a real client over the fake.

	The fake's clock is started an hour in the past so the seeded messages sit inside the
	sweep's window, and advanced a second between messages so their ``createTime`` values
	differ — the sweep's ordering guarantee is meaningless against identical timestamps.
	"""
	start_ms = int((time.time() - minutes_ago * 60) * 1000)
	fake = FakeChatAPI(clock=FakeClock(start_ms=start_ms), enforce_space_write_quota=False)
	space = fake.seed_space(display_name="Ops", members=[USER])
	for index in range(message_count):
		fake.clock.advance(1000)
		fake.seed_message(space, text=f"backlog {index}", sender=USER)

	client = chat_client.GoogleChatClient(
		subject=USER,
		dry_run=False,
		settings=FakeChatSettings(),
		token_provider=fake.token_provider,
		transport=fake,
	)

	now = FRAPPE.utils.now_datetime()
	room = (
		FRAPPE.get_doc(
			{
				"doctype": reconcile.ROOM_DOCTYPE,
				"room_type": "Group",
				"provisioning_state": "Ready",
				"is_archived": 0,
				"gchat_space_name": space,
				"last_event_at": now - timedelta(minutes=minutes_ago + 5),
				"last_reconcile_at": None,
			}
		)
		.insert()
		.name
	)
	FRAPPE.get_doc(
		{"doctype": reconcile.ROOM_MEMBER_DOCTYPE, "room": room, "user": USER, "is_active": 1}
	).insert()
	seed_subscription()

	return fake, client, space, room


def recovered_resource_names(event_names: list[str]) -> list[str]:
	table = STORE.table(reconcile.INBOUND_EVENT_DOCTYPE)
	return [table[name]["gchat_resource_name"] for name in event_names]


def test_chaos_10_an_expired_subscription_with_a_twenty_message_backlog() -> None:
	"""Chaos 10. The sweep recovers all 20 exactly once, in order, flagged as late arrivals.

	This is the assertion that makes a missed renewal survivable. Twenty messages were sent
	while the subscription was dead; Workspace Events will never deliver them, and
	``spaces.messages.list`` filtered on ``createTime`` is the only way they can be found.
	"""
	fake, client, space, room = build_chat_world(message_count=20)
	ingested: list[str] = []
	alerts = AlertRecorder()

	summary = reconcile.reconcile_room(
		room, client=client, ingest=ingested.append, alert=alerts, reason="subscription-expired"
	)

	assert summary["status"] == "ok", summary
	assert summary["listed"] == 20 and summary["recovered"] == 20, summary
	assert summary["ingested"] == 20 and len(ingested) == 20

	rows = STORE.table(reconcile.INBOUND_EVENT_DOCTYPE)
	assert len(rows) == 20, "exactly one Chat Inbound Event per recovered message"

	# Ordering: `seq` is allocated in ingest order, so the order these were handed over in is
	# the order they will forever be displayed in.
	created = [json.loads(rows[name]["payload"])["reconcile"]["message"]["createTime"] for name in ingested]
	assert created == sorted(created), (
		"the sweep must hand messages to the inbound path in ascending createTime; anything else "
		"renumbers a conversation"
	)
	assert len(set(recovered_resource_names(ingested))) == 20, "twenty distinct Google messages"

	# The row must parse as a real delivery, because that is literally what inbound does with
	# it: `process_inbound_event` runs `parse_pubsub_envelope` over `payload` and settles the
	# row `Failed` on anything else. A sweep with its own payload shape would feed the inbound
	# path one MalformedEvent per recovered message instead of reusing it.
	payload = json.loads(rows[ingested[0]]["payload"])
	parsed = decisions.parse_pubsub_envelope(payload)
	assert parsed.event_type == reconcile.RECOVERED_EVENT_TYPE
	assert parsed.space_name == space
	assert parsed.resource_name == rows[ingested[0]]["gchat_resource_name"]
	assert not parsed.is_lifecycle
	assert parsed.raw == {"message": {"name": parsed.resource_name}}, (
		"the inner payload must be the name and nothing else — that is what includeResource: "
		"false actually delivers, and a sweep row carrying a body would be the only row in the "
		"table that does"
	)

	# Provenance rides outside `message`, where the parser cannot see it.
	provenance = payload["reconcile"]
	assert provenance["source"] == "reconcile"
	assert provenance["reason"] == "subscription-expired"
	assert provenance["late_arrival"] is True
	assert provenance["room"] == room and provenance["space"] == space
	assert provenance["message"]["name"] == parsed.resource_name, (
		"the already-fetched resource is carried so an inbound that wants it can skip its own "
		"messages.get; nothing depends on it today"
	)

	# The next scheduled sweep does not re-read a window it has already covered, because the
	# watermark moved. That is the cheap half of "exactly once".
	second: list[str] = []
	quiet = reconcile.reconcile_room(room, client=client, ingest=second.append, alert=alerts)
	assert quiet["listed"] == 0, quiet

	# The expensive half: force the same window again, which is exactly what the
	# subscription-expiry path does when it passes an explicit `since`. Nothing may be
	# recovered twice.
	again = reconcile.reconcile_room(
		room,
		client=client,
		ingest=second.append,
		alert=alerts,
		since=FRAPPE.utils.now_datetime() - timedelta(minutes=90),
	)
	assert again["listed"] == 20 and again["recovered"] == 0, again
	assert again["duplicate"] == 20, (
		"the deterministic synthetic pubsub_message_id is what makes a re-sweep free; without "
		"it an overlapping window re-ingests the whole backlog"
	)
	assert second == []
	assert len(STORE.table(reconcile.INBOUND_EVENT_DOCTYPE)) == 20


def test_a_message_erpnext_already_has_is_never_re_enqueued() -> None:
	"""The cheap guard: a live event already ingested this one."""
	fake, client, space, room = build_chat_world(message_count=3)
	existing = fake.messages_in(space)[0]["name"]
	FRAPPE.get_doc(
		{"doctype": reconcile.MESSAGE_DOCTYPE, "room": room, "gchat_message_name": existing}
	).insert()

	summary = reconcile.reconcile_room(room, client=client, ingest=lambda name: None)

	assert summary["already_present"] == 1 and summary["recovered"] == 2, summary


def test_the_sweep_only_filters_on_createTime() -> None:
	"""messages.list supports exactly two filterable fields; a third is a 400."""
	fake, client, space, room = build_chat_world(message_count=2)
	reconcile.reconcile_room(room, client=client, ingest=lambda name: None)

	listings = [call for call in fake.calls if call.google_method == "spaces.messages.list"]
	assert listings, "the sweep did not call messages.list at all"
	expression = listings[0].query.get("filter", "")
	assert expression.startswith("createTime > "), expression
	assert "thread.name" not in expression and " AND " not in expression


def test_the_watermark_is_the_start_of_the_sweep_not_the_end() -> None:
	"""A message created during the sweep must not fall into the hole after the last page."""
	fake, client, space, room = build_chat_world(message_count=2)
	before = FRAPPE.utils.now_datetime()

	reconcile.reconcile_room(room, client=client, ingest=lambda name: None)

	watermark = STORE.table(reconcile.ROOM_DOCTYPE)[room]["last_reconcile_at"]
	after = FRAPPE.utils.now_datetime()
	assert before <= watermark <= after
	assert watermark <= after, (
		"writing the post-sweep clock leaves anything created during the sweep in a hole "
		"between the last page and the watermark, and it falls there silently"
	)


def test_a_truncated_sweep_does_not_advance_the_watermark() -> None:
	"""A partial sweep must be redone; the dedupe guards are what make redoing it free."""
	fake, client, space, room = build_chat_world(message_count=5)

	summary = reconcile.reconcile_room(room, client=client, ingest=lambda name: None, max_messages=2)

	assert summary["truncated"] is True and summary["recovered"] == 2, summary
	assert STORE.table(reconcile.ROOM_DOCTYPE)[room]["last_reconcile_at"] is None


def test_the_sweep_counts_its_own_recoveries_and_never_events_ingested() -> None:
	"""``events_ingested`` has exactly one writer, and the sweep is not it.

	Every row the sweep recovers is handed to ``inbound.process_inbound_event``, whose
	``_apply_created`` bumps ``events_ingested`` when — and only when — a ``Chat Message`` was
	genuinely inserted. A sweep that bumped the same counter by ``summary["recovered"]`` made it
	read ``2N`` after recovering ``N`` messages, and a doubled counter is worse than no counter
	during the incident it exists for: the first question an operator asks a reconciliation
	sweep is *how much did the live stream miss*, and the answer was twice the truth.

	The sweep keeps a number of its own, because that is a different question. The two are
	expected to disagree — a recovered row can still classify ``ECHO`` or ``DUPLICATE``
	downstream and insert nothing — so ``reconcile_recovered > events_ingested`` is normal.
	"""
	fake, client, space, room = build_chat_world(message_count=3)

	summary = reconcile.reconcile_room(room, client=client, ingest=lambda name: None)

	assert summary["recovered"] == 3, summary
	counts = seams.counters()
	assert counts[seams.COUNTER_RECONCILE_RECOVERED] == 3
	assert counts[seams.COUNTER_EVENTS_INGESTED] == 0, (
		"the sweep must leave events_ingested alone; _apply_created is the single point that "
		"knows a row was actually inserted, and double-counting it here is the difference "
		"between a usable incident metric and a misleading one"
	)


def test_the_inbound_processor_resolves_to_the_real_entry_point() -> None:
	"""The name-candidate resolution must actually find ``sync/inbound.py``.

	This is the seam between two separately-authored modules, and the failure mode if it drifts
	is the quietest one available: the sweep keeps writing rows, nothing raises, and recovery
	silently degrades to whenever the inbound sweeper next runs. Asserting the identity is what
	turns a rename into a failing test instead of a latency regression nobody measures.
	"""
	from erpnext_enhancements.chat.sync import inbound

	assert (
		reconcile._inbound_processor() is inbound.process_inbound_event
	), "reconcile.INBOUND_PROCESSOR_FUNCTIONS no longer names sync/inbound.py's entry point"


def test_the_sweep_is_durable_when_the_inbound_processor_is_unavailable(monkeypatch: Any) -> None:
	"""A processor that cannot run is not a reason to drop what the sweep found.

	The rows are the delivery guarantee — the production deploy ``FLUSHDB``s the queue Redis
	and restarts honcho mid-flight, and Frappe v16 wires no RQ retries — so an in-process call
	that does not happen must leave something behind for the inbound sweeper to drain.
	"""
	fake, client, space, room = build_chat_world(message_count=4)
	monkeypatch.setattr(reconcile, "_inbound_processor", lambda: None)

	summary = reconcile.reconcile_room(room, client=client)

	assert summary["recovered"] == 4 and summary["ingested"] == 0, summary
	rows = STORE.rows(reconcile.INBOUND_EVENT_DOCTYPE)
	assert len(rows) == 4 and all(row["status"] == "Received" for row in rows), (
		"the rows have to survive for the inbound sweeper to drain; the Chat Inbound Event row "
		"is the delivery guarantee, not the in-process call"
	)


def test_a_room_with_traffic_but_no_events_raises_the_silence_alarm() -> None:
	"""§4.J: alert when Chat had traffic and the event stream did not deliver it."""
	fake, client, space, room = build_chat_world(message_count=3, minutes_ago=180)
	STORE.table(reconcile.ROOM_DOCTYPE)[room]["last_event_at"] = FRAPPE.utils.now_datetime() - timedelta(
		days=2
	)
	alerts = AlertRecorder()

	reconcile.reconcile_room(room, client=client, ingest=lambda name: None, alert=alerts)

	silence = alerts.with_prefix("reconcile-events-missing:")
	assert silence and silence[0].severity == subscriptions.ALERT_SEVERITY_ALARM, alerts.keys()


def test_a_room_whose_events_are_arriving_normally_does_not_alarm() -> None:
	"""Proof the alarm above is not vacuous: an overlapping window re-lists healthy traffic."""
	fake, client, space, room = build_chat_world(message_count=3, minutes_ago=5)
	STORE.table(reconcile.ROOM_DOCTYPE)[room]["last_event_at"] = FRAPPE.utils.now_datetime()
	alerts = AlertRecorder()

	reconcile.reconcile_room(room, client=client, ingest=lambda name: None, alert=alerts)

	assert not alerts.with_prefix("reconcile-events-missing:"), alerts.keys()


def test_a_room_with_no_google_space_is_skipped_with_a_reason() -> None:
	room = (
		FRAPPE.get_doc(
			{
				"doctype": reconcile.ROOM_DOCTYPE,
				"room_type": "Group",
				"provisioning_state": "Ready",
				"is_archived": 0,
				"gchat_space_name": "",
			}
		)
		.insert()
		.name
	)

	summary = reconcile.reconcile_room(room)

	assert summary["status"] == "skipped" and "gchat_space_name" in summary["reason"]


def test_a_room_with_nobody_to_impersonate_is_skipped_rather_than_read_as_the_app() -> None:
	"""messages.list is user-authenticated; reading as the app would see a different space."""
	room = (
		FRAPPE.get_doc(
			{
				"doctype": reconcile.ROOM_DOCTYPE,
				"room_type": "Group",
				"provisioning_state": "Ready",
				"is_archived": 0,
				"gchat_space_name": "spaces/AAAAnobody",
			}
		)
		.insert()
		.name
	)

	summary = reconcile.reconcile_room(room)

	assert summary["status"] == "skipped" and "impersonate" in summary["reason"]


def test_the_window_prefers_the_later_watermark_and_applies_the_overlap() -> None:
	now = FRAPPE.utils.now_datetime()
	room = {
		"last_reconcile_at": now - timedelta(hours=3),
		"last_event_at": now - timedelta(hours=1),
	}

	start = reconcile.reconcile_window_start(room, now_local=now, since=None, default_window_minutes=60)

	assert start == now - timedelta(hours=1) - timedelta(seconds=reconcile.RECONCILE_OVERLAP_SECONDS), (
		"a live event that arrived after the last sweep proves the stream was working up to "
		"that point, so the window starts there — minus the overlap, because createTime > is "
		"exclusive and Google's clock is not ours"
	)


def test_the_window_is_clamped_however_stale_the_watermark_is() -> None:
	now = FRAPPE.utils.now_datetime()
	room = {"last_reconcile_at": now - timedelta(days=365), "last_event_at": None}

	start = reconcile.reconcile_window_start(room, now_local=now, since=None, default_window_minutes=60)

	assert start >= now - timedelta(seconds=reconcile.MAX_RECONCILE_LOOKBACK_SECONDS)


def test_the_synthetic_delivery_id_is_deterministic_and_labelled() -> None:
	name = "spaces/AAAA/messages/abc.def"
	assert reconcile.synthetic_delivery_id(name) == reconcile.synthetic_delivery_id(name)
	assert reconcile.synthetic_delivery_id(name).startswith("reconcile:")
	assert reconcile.synthetic_delivery_id(name) != reconcile.synthetic_delivery_id(name + "x")


def test_the_store_raises_the_exception_the_adr_gets_wrong() -> None:
	"""Guard against the stub going soft: a non-PK unique index must raise UniqueValidationError.

	If this ever raises ``DuplicateEntryError`` instead, every exactly-once assertion in this
	file passes for the wrong reason and the shipped code's ``except`` clause is untested
	(``PHASE2_VERIFIED.md`` §1.1).
	"""
	FRAPPE.get_doc({"doctype": reconcile.INBOUND_EVENT_DOCTYPE, "pubsub_message_id": "dupe"}).insert()
	with pytest.raises(FRAPPE.UniqueValidationError):
		FRAPPE.get_doc({"doctype": reconcile.INBOUND_EVENT_DOCTYPE, "pubsub_message_id": "dupe"}).insert()
	assert not issubclass(FRAPPE.UniqueValidationError, FRAPPE.DuplicateEntryError)
