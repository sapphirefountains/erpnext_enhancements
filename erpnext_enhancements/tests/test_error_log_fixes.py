"""Bench-free unit tests for the Error Log debugging pass (v1.254.0).

Two things are guarded here, both of them lessons from production rows rather
than hypotheticals:

1. ``script_migrations/task.py`` built its Google Calendar payload by calling
   ``.isoformat()`` on ``doc.exp_start_date``. That attribute is a
   ``datetime.date`` when the Task has been read back from the database and a
   plain ``str`` on the in-memory doc ``after_insert`` receives — so every Task
   created through the desk raised ``AttributeError: 'str' object has no
   attribute 'isoformat'``. 541 rows, 299 of them in the last month, still
   firing the day this was written. The tests below feed the helper both shapes
   because *that* is the bug; a test that only passed ``date`` objects would
   have gone green against the broken code.

2. ``utils/error_throttle.py`` exists because one dead credential wrote 44,069
   Error Log rows in thirty hours. Its whole contract is "a storm becomes a
   handful of rows", so that is what is asserted — including that distinct
   signatures never share a budget, which is the property that keeps throttling
   from hiding a *second* unrelated failure.

Stubs a minimal ``frappe`` (no site, no bench, no network) in ``setUpModule``,
following ``test_drive_sync_recovery.py`` — execution time, not import time, so
it never fools the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_error_log_fixes
"""

import datetime
import pickle
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

task_module = None
error_throttle = None

LOGGED = []


DB_NAME = "test_site_db"


class _FakeCache:
	"""A stand-in that reproduces ``RedisWrapper``'s asymmetry **on purpose**.

	This is the entire point of the class, and the reason it is not three lines
	long. Frappe's ``RedisWrapper`` namespaces and pickles inside the ``*_value``
	family — ``set_value`` / ``get_value`` / ``delete_value`` all route through
	``make_key()``, which returns ``f"{db_name}|{key}"`` — while ``incr``,
	``expire``, ``get`` and ``delete`` are **not overridden at all**. They come
	straight from redis-py, take the key verbatim, and store a plain integer
	string with no pickling.

	v1.254.0's version of this stub was a flat dict that collapsed the two
	families into one keyspace. That is why ``test_reset_restores_the_budget``
	passed 18/18 green against code where ``reset()`` provably did nothing on a
	real bench: the stub was kinder than production, so it certified a defect
	instead of catching it. A fake that is more forgiving than the real thing
	tests nothing.

	``expire`` is recorded rather than enforced — the tests drive window
	behaviour by clearing keys directly, which keeps them free of sleeps.
	"""

	def __init__(self):
		self.store = {}
		self.expiries = {}
		self.raise_on_incr = False

	def make_key(self, key):
		"""Verbatim copy of ``RedisWrapper.make_key``'s shape."""
		return f"{DB_NAME}|{key}".encode()

	# ---- raw redis-py family: key verbatim, integer payload, no pickling ----

	def incr(self, key):
		if self.raise_on_incr:
			raise RuntimeError("redis is down")
		self.store[key] = self.store.get(key, 0) + 1
		return self.store[key]

	def expire(self, key, seconds):
		self.expiries[key] = seconds

	def get(self, key):
		val = self.store.get(key)
		# redis returns bytes, never an int. Callers must cope with that.
		return None if val is None else str(val).encode()

	def delete(self, key):
		self.store.pop(key, None)

	# ---- frappe *_value family: make_key + pickle ---------------------------

	def get_value(self, key):
		raw = self.store.get(self.make_key(key))
		if raw is None:
			return None
		# Test double; the only thing ever pickled here is what set_value wrote.
		return pickle.loads(raw)

	def set_value(self, key, value, expires_in_sec=None):
		k = self.make_key(key)
		self.store[k] = pickle.dumps(value)
		self.expiries[k] = expires_in_sec

	def delete_value(self, key):
		self.store.pop(self.make_key(key), None)


CACHE = _FakeCache()


def _getdate(value=None):
	"""frappe.utils.getdate: a date from a date, datetime, or ``YYYY-MM-DD``."""
	if value is None:
		return None
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	text = str(value).strip()
	if not text:
		return None
	return datetime.datetime.strptime(text[:10], "%Y-%m-%d").date()


def _add_days(value, days):
	return (_getdate(value) + datetime.timedelta(days=days)).isoformat()


def _today():
	return datetime.date.today().isoformat()


def setUpModule():
	global task_module, error_throttle

	frappe = types.ModuleType("frappe")
	frappe.log_error = lambda message=None, title=None, **kw: LOGGED.append((title, message))
	frappe.cache = lambda: CACHE
	frappe.get_traceback = lambda: "traceback"
	frappe.call = lambda *a, **kw: None
	frappe.flags = types.SimpleNamespace(in_test=False)
	# `error_throttle._site()` reads this to namespace its key, the same source
	# RedisWrapper.make_key uses.
	frappe.local = types.SimpleNamespace(conf={"db_name": DB_NAME})

	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.add_days = _add_days
	utils.today = _today
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	from erpnext_enhancements.script_migrations import task as _task
	from erpnext_enhancements.utils import error_throttle as _throttle

	task_module = _task
	error_throttle = _throttle


class _Doc:
	"""A stand-in Task. ``add_comment`` records rather than writes."""

	def __init__(self, **fields):
		self.subject = "A task"
		self.description = None
		self.exp_start_date = None
		self.exp_end_date = None
		self.creation = "2026-08-06 16:09:03.516516"
		self.comments = []
		self.__dict__.update(fields)

	def add_comment(self, *a, **kw):
		self.comments.append((a, kw))

	def get_formatted(self, field):  # pragma: no cover - the old broken fallback
		raise AssertionError("get_formatted must not be used to build a calendar payload")


class TestCalendarDateCoercion(unittest.TestCase):
	"""The AttributeError itself."""

	def test_a_string_date_survives(self):
		"""The exact production shape: a Date field that is still a str.

		This is the assertion that fails against the pre-fix code.
		"""
		self.assertEqual(task_module._calendar_date("2026-08-06"), "2026-08-06")

	def test_a_real_date_object_survives(self):
		self.assertEqual(task_module._calendar_date(datetime.date(2026, 8, 6)), "2026-08-06")

	def test_a_datetime_is_reduced_to_its_date(self):
		"""``creation`` is a Datetime, and it is one of the fallbacks."""
		self.assertEqual(task_module._calendar_date("2026-08-06 16:09:03.516516"), "2026-08-06")

	def test_unset_is_none_not_an_exception(self):
		for empty in (None, "", 0):
			self.assertIsNone(task_module._calendar_date(empty))


class TestCalendarPayload(unittest.TestCase):
	"""What actually gets sent to Google."""

	def setUp(self):
		LOGGED.clear()
		CACHE.store.clear()
		self.sent = []
		self._real_call = task_module.frappe.call
		task_module.frappe.call = lambda *a, **kw: self.sent.append(kw)

	def tearDown(self):
		task_module.frappe.call = self._real_call

	def _sync(self, **fields):
		doc = _Doc(**fields)
		task_module.sync_task_to_google_calendar(doc)
		return doc

	def test_a_task_with_string_dates_does_not_log_an_error(self):
		"""The regression, end to end: no Error Log row for an ordinary Task."""
		self._sync(exp_start_date="2026-08-10", exp_end_date="2026-08-12")
		self.assertEqual(LOGGED, [], f"sync logged an error it should not have: {LOGGED}")
		self.assertEqual(len(self.sent), 1, "the calendar insert never happened")

	def test_dates_are_all_day_not_datetime(self):
		"""Task's expected start/end are Date fields with no time component, and
		Google rejects a bare ``YYYY-MM-DD`` in a ``dateTime`` slot."""
		self._sync(exp_start_date="2026-08-10", exp_end_date="2026-08-12")
		event = self.sent[0]["doc"]
		self.assertIn("date", event["start"])
		self.assertNotIn("dateTime", event["start"])
		self.assertEqual(event["start"]["date"], "2026-08-10")

	def test_the_end_date_is_exclusive(self):
		"""Google treats an all-day ``end.date`` as exclusive: a task running
		through the 12th ends on the 13th, or it renders a day short."""
		self._sync(exp_start_date="2026-08-10", exp_end_date="2026-08-12")
		self.assertEqual(self.sent[0]["doc"]["end"]["date"], "2026-08-13")

	def test_a_single_day_task_is_not_zero_length(self):
		"""Without the +1 a one-day task collapses and vanishes from the grid."""
		self._sync(exp_start_date="2026-08-10", exp_end_date="2026-08-10")
		event = self.sent[0]["doc"]
		self.assertEqual(event["start"]["date"], "2026-08-10")
		self.assertEqual(event["end"]["date"], "2026-08-11")

	def test_no_dates_falls_back_to_creation(self):
		"""And specifically not to ``get_formatted``, which returns a *display*
		string in the user's date format that Google would refuse."""
		self._sync()
		event = self.sent[0]["doc"]
		self.assertEqual(event["start"]["date"], "2026-08-06")
		self.assertEqual(event["end"]["date"], "2026-08-07")

	def test_a_start_without_an_end_is_a_single_day(self):
		self._sync(exp_start_date="2026-08-10")
		event = self.sent[0]["doc"]
		self.assertEqual(event["start"]["date"], "2026-08-10")
		self.assertEqual(event["end"]["date"], "2026-08-11")


class TestErrorThrottle(unittest.TestCase):
	"""The circuit breaker's contract."""

	def setUp(self):
		LOGGED.clear()
		CACHE.store.clear()
		CACHE.raise_on_incr = False

	def test_the_first_errors_are_logged_in_full(self):
		for _ in range(3):
			error_throttle.log_error_throttled("boom", "T", window=60, limit=3)
		self.assertEqual(len(LOGGED), 3)
		self.assertTrue(all(title == "T" for title, _ in LOGGED))

	def test_a_storm_is_capped(self):
		"""44,069 occurrences must not become 44,069 rows."""
		for _ in range(44069):
			error_throttle.log_error_throttled("boom", "T", window=60, limit=3)
		# 3 full rows + 1 suppression notice, and nothing else.
		self.assertEqual(len(LOGGED), 4)
		self.assertEqual(LOGGED[-1][0], "T (throttled)")
		self.assertIn("suppressed", LOGGED[-1][1])

	def test_the_return_value_tracks_whether_it_was_written(self):
		results = [error_throttle.log_error_throttled("boom", "T", window=60, limit=2) for _ in range(5)]
		self.assertEqual(results, [True, True, True, False, False])

	def test_distinct_titles_do_not_share_a_budget(self):
		"""Throttling must never hide a *second*, unrelated failure."""
		for _ in range(10):
			error_throttle.log_error_throttled("boom", "First", window=60, limit=1)
		error_throttle.log_error_throttled("boom", "Second", window=60, limit=1)
		titles = [title for title, _ in LOGGED]
		self.assertIn("Second", titles)

	def test_an_explicit_key_splits_one_title(self):
		"""A dead Miradore credential must not mask an Action1 failure."""
		for _ in range(10):
			error_throttle.log_error_throttled("boom", "MDM", window=60, limit=1, key="Miradore")
		error_throttle.log_error_throttled("boom", "MDM", window=60, limit=1, key="Action1")
		self.assertEqual(sum(1 for _, msg in LOGGED if msg == "boom"), 2)

	def test_a_window_is_stamped_on_first_use(self):
		"""incr creates the key without a TTL; without an explicit expire the
		budget would never reset and the signature would be throttled forever."""
		error_throttle.log_error_throttled("boom", "T", window=99, limit=1)
		self.assertIn(99, CACHE.expiries.values())

	def test_a_broken_cache_still_logs(self):
		"""Losing an error is worse than writing a duplicate."""
		CACHE.raise_on_incr = True
		for _ in range(5):
			self.assertTrue(error_throttle.log_error_throttled("boom", "T"))
		self.assertEqual(len(LOGGED), 5)

	def test_reset_restores_the_budget(self):
		for _ in range(10):
			error_throttle.log_error_throttled("boom", "T", window=60, limit=1)
		before = len(LOGGED)
		error_throttle.reset("T")
		error_throttle.log_error_throttled("boom", "T", window=60, limit=1)
		self.assertEqual(len(LOGGED), before + 1)


class TestThrottleKeyAgreement(unittest.TestCase):
	"""Every operation must address the *same* Redis slot.

	v1.254.0 shipped a module that wrote its counter with raw ``incr`` and read
	it back with ``get_value``. Those are different keyspaces in Frappe —
	``get_value`` routes through ``make_key`` and ``incr`` does not — so
	``reset()`` was a silent no-op and ``suppressed_count()`` always answered 0.
	The suite was green throughout, because the stub of the day was a flat dict.

	These tests assert the agreement directly, against a stub that now models
	the real split, so the same mistake cannot pass again.
	"""

	def setUp(self):
		LOGGED.clear()
		CACHE.store.clear()
		CACHE.expiries.clear()
		CACHE.raise_on_incr = False

	def test_the_counter_lands_in_exactly_one_slot(self):
		"""Not two — a write under one key and a read under another is the bug."""
		error_throttle.log_error_throttled("boom", "T", window=60, limit=5)
		self.assertEqual(len(CACHE.store), 1, f"expected one cache key, got {list(CACHE.store)}")

	def test_suppressed_count_reads_what_the_throttle_wrote(self):
		for _ in range(7):
			error_throttle.log_error_throttled("boom", "T", window=60, limit=2)
		self.assertEqual(error_throttle.suppressed_count("T"), 7)

	def test_suppressed_count_is_zero_for_an_unseen_signature(self):
		self.assertEqual(error_throttle.suppressed_count("never-happened"), 0)

	def test_reset_actually_clears_the_slot(self):
		"""The operator-facing promise: fix the cause, reset, see the next one."""
		error_throttle.log_error_throttled("boom", "T", window=60, limit=1)
		self.assertEqual(len(CACHE.store), 1)
		error_throttle.reset("T")
		self.assertEqual(CACHE.store, {}, "reset() left the counter behind")
		self.assertEqual(error_throttle.suppressed_count("T"), 0)

	def test_the_key_is_namespaced_by_site(self):
		"""Otherwise every site on a shared Redis shares one budget, and one
		site's storm suppresses another site's first, genuinely-distinct error —
		exactly the masking this module exists to prevent."""
		error_throttle.log_error_throttled("boom", "T", window=60, limit=5)
		key = next(iter(CACHE.store))
		self.assertTrue(
			key.startswith(f"{DB_NAME}|"),
			f"cache key {key!r} is not namespaced by the site",
		)

	def test_two_sites_do_not_share_a_budget(self):
		"""Same title, different site: independent counters."""
		error_throttle.log_error_throttled("boom", "Shared", window=60, limit=1)
		first_key = next(iter(CACHE.store))

		frappe_stub = sys.modules["frappe"]
		original = frappe_stub.local.conf
		try:
			frappe_stub.local.conf = {"db_name": "another_site_db"}
			error_throttle.log_error_throttled("boom", "Shared", window=60, limit=1)
		finally:
			frappe_stub.local.conf = original

		self.assertEqual(len(CACHE.store), 2, "the second site reused the first site's counter")
		self.assertNotIn(first_key, [k for k in CACHE.store if k != first_key])

	def test_a_missing_site_conf_does_not_break_throttling(self):
		"""A throttle that cannot name its site must still throttle."""
		frappe_stub = sys.modules["frappe"]
		original = frappe_stub.local
		try:
			frappe_stub.local = None  # attribute access will raise
			for _ in range(6):
				error_throttle.log_error_throttled("boom", "T", window=60, limit=2)
		finally:
			frappe_stub.local = original
		# 2 full rows + 1 suppression notice: still capped, not crashed.
		self.assertEqual(len(LOGGED), 3)


if __name__ == "__main__":
	unittest.main()
