"""Bench-free unit tests for the Error Log debugging pass (v1.254.0).

Two things are guarded here, both of them lessons from production rows rather
than hypotheticals:

1. ``script_migrations/task.py``'s shared-Google-Calendar sync — the other half
   of the original pass — was **removed outright in v1.346.0** (it broadcast
   every Task to one shared calendar with no per-person filtering, and had never
   delivered an event since the Server Script migration), so its tests went with
   it. ``test_hooks_integrity`` now asserts the hook's *absence* instead.

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

import pickle
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

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


def setUpModule():
	global error_throttle

	frappe = types.ModuleType("frappe")
	frappe.log_error = lambda message=None, title=None, **kw: LOGGED.append((title, message))
	frappe.cache = lambda: CACHE
	frappe.get_traceback = lambda: "traceback"
	frappe.flags = types.SimpleNamespace(in_test=False)
	# `error_throttle._site()` reads this to namespace its key, the same source
	# RedisWrapper.make_key uses.
	frappe.local = types.SimpleNamespace(conf={"db_name": DB_NAME})

	sys.modules["frappe"] = frappe

	from erpnext_enhancements.utils import error_throttle as _throttle

	error_throttle = _throttle


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
