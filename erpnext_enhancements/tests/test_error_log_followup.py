"""Bench-free coverage for the five v1.254.0 behaviour changes that shipped
untested (v1.254.1).

The v1.254.0 Error Log pass changed seven things and tested two of them. An
adversarial audit after the merge pointed out the obvious: the date coercion and
the throttle were guarded, while the Google-API error handling, the migrate
guard, the Drive retry and both permission hooks were not. Those five are the
ones whose failure modes are *quiet* — a swallowed exception, a skipped seed, a
missing retry, a permission check that returns the wrong bool — so they are
exactly the ones a test should be holding.

This is a separate module from ``test_error_log_fixes`` rather than more classes
in it, because it installs a much wider ``frappe`` stub (db, get_doc, get_roles,
session) plus a fake ``googleapiclient``. Per CLAUDE.md, a bench-free suite that
installs its own stub in ``setUpModule`` gets its own CI step so it cannot
cross-talk with the other stub-installing suites.

Note the ``googleapiclient`` stub is not optional convenience: CI installs only
``httpx pytest jinja2``, so the real library is absent there. Both
``api/finance_calendar.py`` and ``google_drive/drive_sync.py`` import
``HttpError`` at module scope, and without the stub this suite would not import
on the runner at all.

Run: python -m unittest erpnext_enhancements.tests.test_error_log_followup
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

finance_calendar = None
training_setup = None
drive_sync = None
device_perms = None
travel_perms = None

STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _Resp:
	def __init__(self, status):
		self.status = status


class FakeHttpError(Exception):
	"""Shaped like ``googleapiclient.errors.HttpError``: the status lives on
	``.resp.status``, which is what the production code branches on."""

	def __init__(self, status, message="google said no"):
		self.resp = _Resp(status)
		super().__init__(message)


class _Cache:
	"""Only the ``*_value`` family — that is all finance_calendar uses."""

	def __init__(self):
		self.store = {}

	def get_value(self, key):
		return self.store.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.store[key] = value

	def delete_value(self, key):
		self.store.pop(key, None)


CACHE = _Cache()


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"errors": [],  # (title, message)
			"inserted": [],  # docs training setup tried to create
			"existing_doctypes": {"Training Category"},
			"existing_records": set(),
			"roles": [],
			"user": "someone@example.com",
			"executed": [],  # kwargs each fake Drive .execute() was called with
			"raise_on_execute": None,
		}
	)
	CACHE.store.clear()


def setUpModule():
	global finance_calendar, training_setup, drive_sync, device_perms, travel_perms

	_reset_state()

	frappe = types.ModuleType("frappe")
	frappe.log_error = lambda message=None, title=None, **kw: STATE["errors"].append((title, message))
	frappe.get_traceback = lambda: "traceback"
	frappe.cache = lambda: CACHE
	frappe.flags = types.SimpleNamespace(in_test=False)
	frappe.local = types.SimpleNamespace(conf={"db_name": "test_site_db"})
	frappe.session = types.SimpleNamespace(user="someone@example.com")
	frappe._dict = _Dict

	def _translate(msg, *a, **kw):
		return msg

	frappe._ = _translate

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError
	frappe.throw = lambda msg, exc=None, **kw: (_ for _ in ()).throw(exc or Exception(msg))

	def _throw(msg, exc=None, **kw):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.get_roles = lambda user=None: STATE["roles"]

	def _whitelist(*a, **kw):
		def deco(fn):
			return fn

		return deco

	frappe.whitelist = _whitelist

	db = types.SimpleNamespace()
	db.exists = lambda doctype, name=None: (
		name in STATE["existing_doctypes"] if doctype == "DocType" else name in STATE["existing_records"]
	)
	db.get_value = lambda *a, **kw: None
	db.escape = lambda v: f"'{v}'"
	db.has_column = lambda dt, col: True
	db.commit = lambda: None
	frappe.db = db

	class _Doc(_Dict):
		def insert(self, **kw):
			STATE["inserted"].append(dict(self))
			return self

	def _get_doc(payload):
		if isinstance(payload, dict) and payload.get("doctype") not in STATE["existing_doctypes"]:
			# What Frappe really does when the DocType row is absent: it fails to
			# resolve a controller module. Reproducing that is the whole point of
			# the guard under test.
			raise ImportError(
				f"Module import failed for {payload.get('doctype')}, "
				f"the DocType you're trying to open might be deleted."
			)
		return _Doc(payload)

	frappe.get_doc = _get_doc

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v, default=0: int(v) if str(v or "").strip().lstrip("-").isdigit() else default
	utils.now_datetime = lambda: "2026-08-07 00:00:00"
	utils.flt = lambda v, p=None: float(v or 0)
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# --- fake googleapiclient (absent on the CI runner) ---------------------
	gac = types.ModuleType("googleapiclient")
	errors_mod = types.ModuleType("googleapiclient.errors")
	errors_mod.HttpError = FakeHttpError
	gac.errors = errors_mod
	sys.modules["googleapiclient"] = gac
	sys.modules["googleapiclient.errors"] = errors_mod

	# --- stub the finance gating chain finance_calendar imports -------------
	fin = types.ModuleType("erpnext_enhancements.api.finance_dashboard")
	fin._require_finance = lambda: None
	fin._settings = lambda: _Dict({"finance_calendar_id": "cal-1", "finance_calendar_max_events": 10})
	fin._widget_enabled = lambda field, settings=None: True
	sys.modules["erpnext_enhancements.api.finance_dashboard"] = fin

	# --- stub the drive_utils chain drive_sync imports ----------------------
	du = types.ModuleType("erpnext_enhancements.google_drive.drive_utils")
	du.find_folder = lambda *a, **kw: None
	du.get_drive_service = lambda *a, **kw: (None, None)
	du.get_folder_meta = lambda *a, **kw: {}
	sys.modules["erpnext_enhancements.google_drive.drive_utils"] = du

	from erpnext_enhancements.api import finance_calendar as _fc
	from erpnext_enhancements.device_management import permissions as _dp
	from erpnext_enhancements.google_drive import drive_sync as _ds
	from erpnext_enhancements.training import setup as _ts
	from erpnext_enhancements.travel_management import permissions as _tp

	finance_calendar = _fc
	training_setup = _ts
	drive_sync = _ds
	device_perms = _dp
	travel_perms = _tp


# --------------------------------------------------------------- finance calendar


class _FakeEvents:
	def __init__(self, exc):
		self._exc = exc

	def list(self, **kw):
		return self

	def execute(self, **kw):
		raise self._exc


class TestFinanceCalendarErrorHandling(unittest.TestCase):
	"""A permanent misconfiguration must not be retried, and must say what to fix.

	Before v1.254.0 this logged a 40-line traceback on every dashboard load for a
	404 that no amount of retrying could fix.
	"""

	def setUp(self):
		_reset_state()

	def _fetch_raising(self, exc):
		def _raise(calendar_id, max_results=10):
			raise exc

		mod = sys.modules["erpnext_enhancements.google_calendar.calendar_utils"] = types.ModuleType(
			"erpnext_enhancements.google_calendar.calendar_utils"
		)
		mod.fetch_upcoming_events = _raise
		return finance_calendar.get_finance_calendar()

	def test_a_404_names_the_fix_rather_than_reporting_an_outage(self):
		result = self._fetch_raising(FakeHttpError(404))
		self.assertEqual(result["events"], [])
		self.assertIn("calendar id", result["reason"].lower())
		self.assertNotIn("unavailable", result["reason"].lower())

	def test_a_403_names_sharing_rather_than_the_id(self):
		result = self._fetch_raising(FakeHttpError(403))
		self.assertIn("shared", result["reason"].lower())

	def test_a_permanent_failure_is_cached_so_the_next_load_does_not_re_ask(self):
		self._fetch_raising(FakeHttpError(404))
		before = len(STATE["errors"])

		def _explode(calendar_id, max_results=10):
			raise AssertionError("Google was called again despite a cached refusal")

		mod = sys.modules["erpnext_enhancements.google_calendar.calendar_utils"]
		mod.fetch_upcoming_events = _explode

		second = finance_calendar.get_finance_calendar()
		self.assertEqual(second["events"], [])
		self.assertTrue(second.get("cached"))
		self.assertEqual(len(STATE["errors"]), before, "a cached refusal still logged")

	def test_a_transient_failure_is_not_cached_as_a_misconfiguration(self):
		"""A 500 is an outage; the next load should try again."""
		result = self._fetch_raising(FakeHttpError(500))
		self.assertIn("unavailable", result["reason"].lower())
		self.assertFalse(result.get("cached"))

	def test_a_non_http_failure_still_degrades_instead_of_raising(self):
		result = self._fetch_raising(RuntimeError("boom"))
		self.assertEqual(result["events"], [])
		self.assertIn("unavailable", result["reason"].lower())

	def test_the_widget_never_raises_at_the_caller(self):
		"""It is a dashboard tile. A broken calendar must not 500 the board."""
		for exc in (FakeHttpError(403), FakeHttpError(404), FakeHttpError(503), ValueError("x")):
			_reset_state()
			result = self._fetch_raising(exc)
			self.assertTrue(result["enabled"])
			self.assertEqual(result["events"], [])


# ----------------------------------------------------------------- training setup


class TestTrainingCategoryGuard(unittest.TestCase):
	"""`ensure_training_categories` runs on every migrate. It must no-op — not
	crash, and not half-seed — when the Training DocTypes are not installed yet."""

	def setUp(self):
		_reset_state()

	def test_it_seeds_when_the_doctype_exists(self):
		training_setup.ensure_training_categories()
		self.assertTrue(STATE["inserted"], "nothing was seeded on a healthy site")
		self.assertTrue(all(d["doctype"] == "Training Category" for d in STATE["inserted"]))

	def test_it_is_a_no_op_when_the_doctype_is_not_installed_yet(self):
		"""The bug: `frappe.db.exists("Training Category", name)` queries a table
		that survives from an earlier migrate, so it answers happily while the
		DocType row is missing. Only the DocType check catches that."""
		STATE["existing_doctypes"].discard("Training Category")
		training_setup.ensure_training_categories()
		self.assertEqual(STATE["inserted"], [])
		self.assertEqual(STATE["errors"], [], "a missing DocType should be silent, not logged")

	def test_it_does_not_reseed_what_already_exists(self):
		"""Insert-only: a category an admin renamed or deleted stays that way."""
		STATE["existing_records"] = {name for name, _ in training_setup.STARTER_CATEGORIES}
		training_setup.ensure_training_categories()
		self.assertEqual(STATE["inserted"], [])

	def test_one_bad_category_does_not_abort_the_migrate(self):
		calls = {"n": 0}
		original = sys.modules["frappe"].get_doc

		def _fail_second(payload):
			calls["n"] += 1
			if calls["n"] == 2:
				raise ValueError("bad record")
			return original(payload)

		sys.modules["frappe"].get_doc = _fail_second
		try:
			training_setup.ensure_training_categories()
		finally:
			sys.modules["frappe"].get_doc = original

		self.assertEqual(len(STATE["inserted"]), len(training_setup.STARTER_CATEGORIES) - 1)
		self.assertTrue(STATE["errors"], "the failure should still be recorded")


# -------------------------------------------------------------------- drive sync


class _FakeFiles:
	def __init__(self):
		self.calls = []

	def get(self, **kw):
		self.calls.append(kw)
		return self

	def execute(self, **kw):
		STATE["executed"].append(kw)
		if STATE["raise_on_execute"]:
			raise STATE["raise_on_execute"]
		return {"driveId": "drive-123"}


class _FakeService:
	def __init__(self):
		self._files = _FakeFiles()

	def files(self):
		return self._files


class TestDriveRetries(unittest.TestCase):
	"""Google documents transient 500s on Drive metadata reads and asks clients to
	retry with backoff. Not retrying is what turned a blip into an Error Log row
	per customer per hourly run — 61 of them in a month, all healthy on retry."""

	def setUp(self):
		_reset_state()

	def test_the_drive_id_read_asks_for_retries(self):
		drive_sync._drive_id_of(_FakeService(), "folder-1")
		self.assertEqual(len(STATE["executed"]), 1)
		self.assertGreaterEqual(
			STATE["executed"][0].get("num_retries", 0),
			1,
			"the metadata read is not retrying transient Google 5xx",
		)

	def test_the_retry_count_is_a_positive_int(self):
		self.assertIsInstance(drive_sync.GOOGLE_API_RETRIES, int)
		self.assertGreater(drive_sync.GOOGLE_API_RETRIES, 0)

	def test_a_404_still_propagates_rather_than_being_retried_away(self):
		"""A deleted folder is permanent. It must reach the caller, which flags it
		on the record; retrying it would just be slower before the same answer."""
		STATE["raise_on_execute"] = FakeHttpError(404)
		with self.assertRaises(FakeHttpError):
			drive_sync._drive_id_of(_FakeService(), "folder-gone")


# ------------------------------------------------------------------- permissions


class TestPermissionHooksAcceptDicts(unittest.TestCase):
	"""Frappe hands ``has_permission`` hooks a plain dict on some paths, and a dict
	has no ``.is_new()``. The v1.254.0 change swapped that for a ``.get()`` call
	so both shapes work; these pin the *behaviour*, not the implementation.

	Deliberately NOT asserting equivalence with ``is_new()``. On Frappe v16
	``is_new()`` is ``bool(self.get("__islocal"))``, which is a different
	predicate — the v1.254.0 changelog claimed otherwise and was wrong. What
	matters here is that neither hook raises, and that a saved record belonging
	to someone else is still refused.
	"""

	def setUp(self):
		_reset_state()

	# -- Managed Device ---------------------------------------------------

	def test_device_hook_does_not_raise_on_a_dict(self):
		doc = {"creation": "2026-01-01 00:00:00", "assigned_to_user": "someone@example.com"}
		self.assertTrue(device_perms.has_permission(doc, "read", "someone@example.com"))

	def test_device_hook_refuses_someone_elses_device(self):
		doc = {"creation": "2026-01-01 00:00:00", "assigned_to_user": "other@example.com"}
		self.assertFalse(device_perms.has_permission(doc, "read", "someone@example.com"))

	def test_device_hook_allows_an_unsaved_record(self):
		self.assertTrue(device_perms.has_permission({}, "read", "someone@example.com"))

	def test_device_hook_lets_fleet_roles_through(self):
		STATE["roles"] = ["Device Manager"]
		doc = {"creation": "2026-01-01 00:00:00", "assigned_to_user": "other@example.com"}
		self.assertTrue(device_perms.has_permission(doc, "read", "someone@example.com"))

	# -- Travel Trip ------------------------------------------------------

	def test_travel_hook_does_not_raise_on_a_dict(self):
		doc = {"creation": "2026-01-01 00:00:00", "owner": "someone@example.com", "travelers": []}
		self.assertTrue(travel_perms.has_permission(doc, "read", "someone@example.com"))

	def test_travel_hook_refuses_someone_elses_trip(self):
		doc = {"creation": "2026-01-01 00:00:00", "owner": "other@example.com", "travelers": []}
		self.assertFalse(travel_perms.has_permission(doc, "read", "someone@example.com"))

	def test_travel_hook_allows_an_unsaved_record(self):
		self.assertTrue(travel_perms.has_permission({}, "read", "someone@example.com"))

	def test_neither_hook_raises_on_any_plausible_dict_shape(self):
		"""The regression in one line: an AttributeError from inside a permission
		check surfaces as an unrelated-looking save failure, which is what made
		the original report so hard to place."""
		shapes = [
			{},
			{"creation": None},
			{"creation": "2026-01-01 00:00:00"},
			{"creation": "2026-01-01 00:00:00", "owner": "x@example.com", "travelers": []},
		]
		for shape in shapes:
			for hook in (device_perms.has_permission, travel_perms.has_permission):
				try:
					hook(dict(shape), "read", "someone@example.com")
				except AttributeError as exc:  # pragma: no cover - the bug itself
					self.fail(f"{hook.__module__} raised on {shape!r}: {exc}")


if __name__ == "__main__":
	unittest.main()
