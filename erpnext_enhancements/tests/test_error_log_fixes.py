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


class _FakeCache:
	"""Enough of frappe's cache for the throttle: incr, expire, get, delete.

	``expire`` is recorded rather than enforced — the tests drive window
	behaviour by clearing keys directly, which keeps them free of sleeps.
	"""

	def __init__(self):
		self.store = {}
		self.expiries = {}
		self.raise_on_incr = False

	def incr(self, key):
		if self.raise_on_incr:
			raise RuntimeError("redis is down")
		self.store[key] = self.store.get(key, 0) + 1
		return self.store[key]

	def expire(self, key, seconds):
		self.expiries[key] = seconds

	def get_value(self, key):
		return self.store.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value
		self.expiries[key] = expires_in_sec

	def delete_value(self, key):
		self.store.pop(key, None)


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


if __name__ == "__main__":
	unittest.main()
