"""Bench-free unit tests for the Drive shadow sync's DB-connection recovery
(``google_drive/drive_sync.py``).

Stubs a minimal ``frappe`` and the Google client libraries (no site, no bench, no
network) so ``drive_sync`` imports under plain unittest. Installed in
``setUpModule`` — execution time, not import time — so it never fools the
bench-only suites' ``import frappe`` skip-guards.

What is being guarded: the hourly shadow walk is the one job in this app that
holds a DB connection open across minutes of uninterrupted Google API traffic
(~2,200 linked documents in production as of 2026-08-28, enough that the walk
now time-boxes itself — see ``TestRunShadowSyncTimeBox``), and roughly once a
day that connection is gone by the time the walk's first query runs. The
per-document ``except`` was meant to log it and move on, but its
``frappe.db.rollback()`` issues SQL — so on a dead connection it raised the same
error straight back out, aborting the whole run and taking ``frappe.log_error``
with it. The symptom was an RQ traceback with *nothing* in the Error Log.

So these assert the contract rather than the implementation: whatever one
document does, the run reaches the last document, and every failure is recorded.

Run: python -m unittest erpnext_enhancements.tests.test_drive_sync_recovery
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

drive_sync = None
drive_utils = None

STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _OperationalError(Exception):
	"""Stand-in for ``MySQLdb.OperationalError``, which carries its error code as
	``args[0]`` — the thing ``_is_lost_connection`` keys on."""


def _lost_connection(code=2006):
	return _OperationalError(code, "Server has gone away")


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"errors": [],  # frappe.log_error calls
			"commits": 0,
			"rollbacks": 0,
			"reconnects": 0,
			"connected": True,  # False once a lost connection is simulated
			"rollback_raises": False,
			"reconnect_fails": False,
			"synced": [],  # documents _sync_folder_shadows was reached for
			"fail_on": {},  # docname -> exception to raise
			"cache": {},  # what frappe.cache() persists (the resume cursor)
		}
	)


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.flags = _Dict()
	frappe.session = _Dict(user="tester@example.com")
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.only_for = lambda *a, **k: None
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a[0] if a else "")
	frappe.enqueue = lambda *a, **k: None
	frappe.get_doc = lambda *a, **k: _Dict()
	frappe.get_single = lambda *a, **k: _Dict()
	frappe.get_cached_doc = lambda *a, **k: _Dict(attachment_sync_enabled=1, service_account_json="{}")

	def _cache():
		# Only the *_value family the resume cursor uses. Deliberately no `incr`:
		# error_throttle's raw-family calls then raise AttributeError and it falls
		# back to plain log_error, exactly as it does when Redis is unavailable.
		store = STATE.setdefault("cache", {})
		return types.SimpleNamespace(
			get_value=lambda key: store.get(key),
			set_value=lambda key, value, expires_in_sec=None: store.__setitem__(key, value),
			delete_value=lambda key: store.pop(key, None),
		)

	frappe.cache = _cache

	def _require_connection():
		"""Every DB call goes through here, so a simulated dead connection fails
		the same way a real one does: on use, not on some flag check."""
		if not STATE["connected"]:
			raise _lost_connection()

	def _commit():
		_require_connection()
		STATE["commits"] += 1

	def _rollback(*a, **k):
		STATE["rollbacks"] += 1
		if STATE["rollback_raises"]:
			raise _lost_connection()
		_require_connection()

	def _close():
		# A dead socket can raise on close; the real Database.close() then leaves
		# _conn populated. Mirrored here so the helper is exercised properly.
		if not STATE["connected"]:
			raise _lost_connection()

	def _connect():
		STATE["reconnects"] += 1
		if STATE["reconnect_fails"]:
			raise _lost_connection()
		STATE["connected"] = True

	frappe.db = types.SimpleNamespace(
		commit=_commit,
		rollback=_rollback,
		close=_close,
		connect=_connect,
		has_column=lambda *a, **k: True,
		exists=lambda *a, **k: None,
		get_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
	)

	def _get_all(doctype, **kwargs):
		_require_connection()
		return [
			_Dict(name=name, custom_drive_folder_id=f"folder-{name}")
			for name in STATE.get("rows", {}).get(doctype, [])
		]

	frappe.get_all = _get_all

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	frappe.utils = utils
	frappe.__dict__["_"] = lambda s: s

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# googleapiclient.errors.HttpError — imported at module scope by drive_sync.
	googleapiclient = types.ModuleType("googleapiclient")
	errors = types.ModuleType("googleapiclient.errors")

	class HttpError(Exception):
		def __init__(self, resp=None, content=b""):
			super().__init__("http error")
			self.resp = resp

	errors.HttpError = HttpError
	googleapiclient.errors = errors
	discovery = types.ModuleType("googleapiclient.discovery")
	discovery.build = lambda *a, **k: None
	googleapiclient.discovery = discovery
	http_mod = types.ModuleType("googleapiclient.http")
	http_mod.MediaIoBaseUpload = object
	googleapiclient.http = http_mod
	sys.modules["googleapiclient"] = googleapiclient
	sys.modules["googleapiclient.errors"] = errors
	sys.modules["googleapiclient.discovery"] = discovery
	sys.modules["googleapiclient.http"] = http_mod

	google = sys.modules.get("google") or types.ModuleType("google")
	oauth2 = types.ModuleType("google.oauth2")
	service_account = types.ModuleType("google.oauth2.service_account")
	service_account.Credentials = types.SimpleNamespace(from_service_account_info=lambda info: None)
	oauth2.service_account = service_account
	google.oauth2 = oauth2
	sys.modules["google"] = google
	sys.modules["google.oauth2"] = oauth2
	sys.modules["google.oauth2.service_account"] = service_account


def setUpModule():
	global drive_sync, drive_utils
	_install_stubs()
	_reset_state()
	from erpnext_enhancements.google_drive import drive_sync as module
	from erpnext_enhancements.google_drive import drive_utils as utils_module

	drive_sync = module
	drive_utils = utils_module


class TestLostConnectionDetection(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_recognises_every_lost_connection_code(self):
		# 2006 gone away, 2013 lost during query, 2055 lost connection. Production
		# has produced 2006 and 2013 from the identical call site.
		for code in (2006, 2013, 2055):
			self.assertTrue(drive_sync._is_lost_connection(_OperationalError(code, "x")))

	def test_ignores_ordinary_query_errors(self):
		# 1054 unknown column, 1146 no such table — real bugs, must not be
		# mistaken for a dead socket and papered over with a reconnect.
		for code in (1054, 1146):
			self.assertFalse(drive_sync._is_lost_connection(_OperationalError(code, "x")))

	def test_ignores_non_driver_exceptions(self):
		self.assertFalse(drive_sync._is_lost_connection(ValueError("nope")))
		self.assertFalse(drive_sync._is_lost_connection(Exception()))


class TestRecovery(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_lost_connection_reconnects_without_rollback(self):
		STATE["connected"] = False
		self.assertTrue(drive_sync._recover_after_document_failure(_lost_connection()))
		self.assertEqual(STATE["reconnects"], 1)
		self.assertTrue(STATE["connected"])
		# Rolling back on a dead connection is the bug — it must not be attempted.
		self.assertEqual(STATE["rollbacks"], 0)

	def test_ordinary_failure_still_rolls_back(self):
		self.assertTrue(drive_sync._recover_after_document_failure(ValueError("bad folder")))
		self.assertEqual(STATE["rollbacks"], 1)
		self.assertEqual(STATE["reconnects"], 0)

	def test_failing_rollback_falls_back_to_reconnect(self):
		STATE["rollback_raises"] = True
		self.assertTrue(drive_sync._recover_after_document_failure(ValueError("bad folder")))
		self.assertEqual(STATE["reconnects"], 1)

	def test_gives_up_when_reconnect_fails(self):
		STATE["connected"] = False
		STATE["reconnect_fails"] = True
		self.assertFalse(drive_sync._recover_after_document_failure(_lost_connection()))


class TestRunShadowSyncSurvival(unittest.TestCase):
	"""The contract: one document's failure never aborts the run."""

	def setUp(self):
		_reset_state()
		STATE["rows"] = {"Project": ["P1", "P2", "P3"], "Customer": [], "Opportunity": []}
		self._real_sync = drive_sync._sync_folder_shadows
		self._real_service = drive_sync.get_drive_service

		def _fake_sync(service, doctype, docname, folder_id, cache):
			STATE["synced"].append(docname)
			exc = STATE["fail_on"].get(docname)
			if exc:
				if drive_sync._is_lost_connection(exc):
					STATE["connected"] = False
				raise exc

		drive_sync._sync_folder_shadows = _fake_sync
		drive_sync.get_drive_service = lambda: (object(), "shared-drive")

	def tearDown(self):
		drive_sync._sync_folder_shadows = self._real_sync
		drive_sync.get_drive_service = self._real_service

	def test_lost_connection_midway_does_not_abort_the_run(self):
		# The exact production failure: the walk's first query finds the socket
		# gone, and the rollback that follows would hit the same dead socket.
		STATE["fail_on"] = {"P2": _lost_connection()}
		STATE["rollback_raises"] = True

		drive_sync.run_shadow_sync()

		self.assertEqual(STATE["synced"], ["P1", "P2", "P3"])
		self.assertEqual(STATE["reconnects"], 1)
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertIn("P2", STATE["errors"][0])

	def test_documents_after_the_failure_still_commit(self):
		STATE["fail_on"] = {"P1": _lost_connection(2013)}

		drive_sync.run_shadow_sync()

		# P1 rolled back and was skipped; P2 and P3 committed on the new connection.
		self.assertEqual(STATE["commits"], 2)

	def test_ordinary_document_failure_is_unchanged(self):
		# A deleted linked folder must behave exactly as before: rollback, log, next.
		STATE["fail_on"] = {"P2": ValueError("folder gone")}

		drive_sync.run_shadow_sync()

		self.assertEqual(STATE["synced"], ["P1", "P2", "P3"])
		self.assertEqual(STATE["rollbacks"], 1)
		self.assertEqual(STATE["reconnects"], 0)
		self.assertEqual(len(STATE["errors"]), 1)

	def test_unrecoverable_connection_stops_the_run_quietly(self):
		# If the DB is genuinely unreachable there is nothing to do but stop —
		# and stop without raising, so RQ doesn't record a bare traceback.
		STATE["fail_on"] = {"P1": _lost_connection()}
		STATE["reconnect_fails"] = True

		drive_sync.run_shadow_sync()

		self.assertEqual(STATE["synced"], ["P1"])
		self.assertEqual(STATE["errors"], [])

	def test_an_unrecoverable_stop_remembers_where_it_was(self):
		# The DB is gone but Redis isn't: the give-up path still records the last
		# finished document so the next hourly run does not redo the prefix.
		STATE["fail_on"] = {"P2": _lost_connection()}
		STATE["reconnect_fails"] = True

		drive_sync.run_shadow_sync()

		self.assertEqual(STATE["cache"].get(drive_sync.SHADOW_SYNC_CURSOR_KEY), "Project/P1")


class TestRunShadowSyncTimeBox(unittest.TestCase):
	"""The walk stops inside its own worker timeout and resumes next run.

	The production shape being guarded: at ~2,200 linked documents the full pass
	overran the 3600s RQ hard timeout, which killed the worker mid-walk five runs
	in a row every morning (JobTimeoutException, 2026-08-25..27) — and a killed
	run restarts from the top, so the tail documents were never reached at all.
	"""

	def setUp(self):
		_reset_state()
		STATE["rows"] = {"Project": ["P1", "P2", "P3"], "Customer": [], "Opportunity": []}
		self._real_sync = drive_sync._sync_folder_shadows
		self._real_service = drive_sync.get_drive_service
		self._real_time = drive_sync.time
		self.clock = types.SimpleNamespace(now=0.0)
		drive_sync.time = types.SimpleNamespace(monotonic=lambda: self.clock.now)

		def _fake_sync(service, doctype, docname, folder_id, cache):
			STATE["synced"].append(docname)
			self.clock.now += STATE.get("seconds_per_doc", 0)

		drive_sync._sync_folder_shadows = _fake_sync
		drive_sync.get_drive_service = lambda: (object(), "shared-drive")

	def tearDown(self):
		drive_sync._sync_folder_shadows = self._real_sync
		drive_sync.get_drive_service = self._real_service
		drive_sync.time = self._real_time

	def test_budget_exhaustion_stops_cleanly_and_saves_a_cursor(self):
		# Two documents fit in the budget; the third must wait for the next run.
		STATE["seconds_per_doc"] = drive_sync.SHADOW_SYNC_TIME_BUDGET / 2 + 1

		drive_sync.run_shadow_sync()

		self.assertEqual(STATE["synced"], ["P1", "P2"])
		self.assertEqual(
			STATE["cache"].get(drive_sync.SHADOW_SYNC_CURSOR_KEY), "Project/P2"
		)

	def test_the_next_run_resumes_after_the_cursor(self):
		STATE["cache"][drive_sync.SHADOW_SYNC_CURSOR_KEY] = "Project/P2"

		drive_sync.run_shadow_sync()

		# Wraps around: one full circle still visits every document.
		self.assertEqual(STATE["synced"], ["P3", "P1", "P2"])

	def test_a_completed_run_clears_the_cursor(self):
		STATE["cache"][drive_sync.SHADOW_SYNC_CURSOR_KEY] = "Project/P2"

		drive_sync.run_shadow_sync()

		self.assertNotIn(drive_sync.SHADOW_SYNC_CURSOR_KEY, STATE["cache"])

	def test_a_cursor_for_a_deleted_record_starts_from_the_top(self):
		STATE["cache"][drive_sync.SHADOW_SYNC_CURSOR_KEY] = "Project/GONE"

		drive_sync.run_shadow_sync()

		self.assertEqual(STATE["synced"], ["P1", "P2", "P3"])


class TestFindFolderQueryEscaping(unittest.TestCase):
	"""The Drive query grammar, fed real production names.

	Quote-only escaping left the customer literally named
	``A\\ Typical Design Studio`` un-queryable: Drive rejects the stray backslash
	with ``HttpError 400 "Invalid Value"``, once per hourly shadow walk, forever.
	Backslashes must be doubled *before* quotes are escaped, or the escape
	character itself becomes one.
	"""

	def _query_for(self, name):
		captured = {}

		def _list(**kwargs):
			captured.update(kwargs)
			return types.SimpleNamespace(execute=lambda: {"files": []})

		service = types.SimpleNamespace(
			files=lambda: types.SimpleNamespace(list=_list)
		)
		drive_utils.find_folder(service, name, "parent-1")
		return captured["q"]

	def test_a_backslash_is_escaped(self):
		q = self._query_for("A\\ Typical Design Studio")
		self.assertIn("name='A\\\\ Typical Design Studio'", q)

	def test_a_quote_is_escaped(self):
		q = self._query_for("Alta's Rustler Lodge")
		self.assertIn("name='Alta\\'s Rustler Lodge'", q)

	def test_a_backslash_cannot_disarm_a_quote_escape(self):
		# name ends in \' — if quotes were escaped first, the backslash pass
		# would turn \' into \\' and the quote would end the string early.
		q = self._query_for("weird\\'name")
		self.assertIn("name='weird\\\\\\'name'", q)


if __name__ == "__main__":
	unittest.main()
