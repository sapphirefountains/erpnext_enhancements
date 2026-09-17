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

``TestWholeDriveIndex`` guards the cost model that replaced the per-folder walk
(v1.475.0): one Shared Drive listing per run, no Google call per document, and
the live walk kept only for a root the listing does not contain.

Run: python -m unittest erpnext_enhancements.tests.test_drive_sync_recovery
"""

import re
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


class _FakeDoc:
	"""What ``frappe.get_doc({...})`` hands back: enough Document for the shadow
	insert (``flags``, ``insert``, ``db_set``) and for ``log_sync``. Inserted docs
	land in ``STATE["inserted"]`` as the same dict, so a later ``db_set`` shows up
	in a query — as it does through the real DB."""

	def __init__(self, data):
		self.data = dict(data)
		self.flags = _Dict()

	def get(self, key, default=None):
		return self.data.get(key, default)

	def insert(self, **kwargs):
		if not STATE["connected"]:
			raise _lost_connection()
		if self.data.get("doctype") == "File" and self.data.get("file_name"):
			# Frappe v16 File.set_file_name: re.sub(r"/", "", file_name) on every
			# insert — the reason shadow names use SHADOW_PATH_SEPARATOR. Kept
			# here so a separator Frappe would eat fails the build, not production.
			self.data["file_name"] = self.data["file_name"].replace("/", "")
		self.data.setdefault("name", f"{self.data.get('doctype')}-{len(STATE['inserted']) + 1}")
		STATE["inserted"].append(self.data)
		return self

	def db_set(self, field, value, **kwargs):
		self.data[field] = value


def _matches(row, filters):
	"""The filter shapes ``_sync_folder_shadows`` actually uses: equality,
	``["in", [...]]``, ``["is", "set"]`` and ``["like", "%x%"]``."""
	for field, cond in (filters or {}).items():
		value = row.get(field)
		if isinstance(cond, (list, tuple)):
			op, arg = cond
			if op == "in" and value not in arg:
				return False
			if op == "is" and bool(value) != (arg == "set"):
				return False
			if op == "like" and arg.strip("%") not in (value or ""):
				return False
		elif value != cond:
			return False
	return True


def _query_inserted(doctype, filters=None, pluck=None):
	rows = [d for d in STATE["inserted"] if d.get("doctype") == doctype and _matches(d, filters)]
	if pluck:
		return [d.get(pluck) for d in rows]
	return [_Dict(d) for d in rows]


def _shadows():
	return [d for d in STATE["inserted"] if d.get("doctype") == "File"]


def _logs(status=None):
	return [
		d
		for d in STATE["inserted"]
		if d.get("doctype") == "Drive Sync Log" and (status is None or d.get("status") == status)
	]


def _shadow_map():
	"""``{drive id: (attached doctype, attached name, file_name)}``, refusing a
	duplicate — the contract is one shadow per Drive item, ever."""
	out = {}
	for d in _shadows():
		assert d["custom_drive_file_id"] not in out, f"duplicate shadow for {d['custom_drive_file_id']}"
		out[d["custom_drive_file_id"]] = (
			d.get("attached_to_doctype"),
			d.get("attached_to_name"),
			d["file_name"],
		)
	return out


class _Request:
	def __init__(self, fn):
		self._fn = fn

	def execute(self, num_retries=None):
		return self._fn()


class _FakeDrive:
	"""Stand-in for the Drive v3 ``service``. ``items`` is the Shared Drive's
	content (``{"id", "name", "mimeType", "parents", "webViewLink"}`` each);
	``elsewhere`` is content Google knows about that is *not* in the Shared Drive
	(My Drive, another drive). Every call is counted by kind, so a test can say
	exactly how many round-trips a run cost."""

	def __init__(self, items=(), elsewhere=(), page_size=1000, drive_id="shared-drive", fail_listing=False):
		self.items = [dict(i) for i in items]
		self.elsewhere = [dict(i) for i in elsewhere]
		self.page_size = page_size
		self.drive_id = drive_id
		self.fail_listing = fail_listing
		self.calls = {"list_drive": 0, "list_folder": 0, "get": 0}

	def files(self):
		return self

	def list(self, **kwargs):
		match = re.search(r"'([^']+)' in parents", kwargs.get("q", ""))
		if match:
			parent = match.group(1)
			self.calls["list_folder"] += 1
			rows = [i for i in self.items + self.elsewhere if parent in (i.get("parents") or [])]
			return _Request(lambda: {"files": rows})
		self.calls["list_drive"] += 1
		if self.fail_listing:
			return _Request(self._boom)
		start = int(kwargs.get("pageToken") or 0)
		result = {"files": self.items[start : start + self.page_size]}
		if start + self.page_size < len(self.items):
			result["nextPageToken"] = str(start + self.page_size)
		return _Request(lambda: result)

	def _boom(self):
		raise RuntimeError("Drive listing is down")

	def get(self, fileId=None, **kwargs):
		self.calls["get"] += 1
		known = {i["id"] for i in self.items + self.elsewhere}

		def _execute():
			if fileId in known:
				return {"driveId": self.drive_id}
			raise drive_sync.HttpError(resp=types.SimpleNamespace(status=404))

		return _Request(_execute)


def _folder(fid, name, parent):
	return {
		"id": fid,
		"name": name,
		"mimeType": drive_sync.FOLDER_MIME,
		"parents": [parent],
		"webViewLink": f"https://drive.google.com/drive/folders/{fid}",
	}


def _file(fid, name, parent):
	return {
		"id": fid,
		"name": name,
		"mimeType": "application/pdf",
		"parents": [parent],
		"webViewLink": f"https://drive.google.com/file/d/{fid}",
	}


def _tree():
	"""A Shared Drive shaped like production: the Project folder sits *inside*
	the Customer folder, and both are linked roots (``folder-<docname>``)."""
	return [
		_folder("folder-C1", "Acme", "shared-drive"),
		_file("brief", "brief.pdf", "folder-C1"),
		_folder("folder-P1", "PRJ-1 - Acme", "folder-C1"),
		_folder("design", "Design", "folder-P1"),
		_file("front", "front.png", "design"),
		_file("quote", "quote.pdf", "folder-P1"),
	]


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
			"inserted": [],  # every doc .insert()ed: File shadows + Drive Sync Log rows
			"set_values": [],  # frappe.db.set_value calls (the missing-folder flag)
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
	frappe.get_doc = lambda *a, **k: _FakeDoc(a[0]) if a and isinstance(a[0], dict) else _Dict()
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

	def _set_value(doctype, name, field, value, **kwargs):
		# Recorded for assertions, and applied to the fake rows so a later query
		# sees the write — as it does through the real DB.
		STATE["set_values"].append((doctype, name, field, value))
		for row in STATE["inserted"]:
			if row.get("doctype") == doctype and row.get("name") == name:
				row[field] = value

	frappe.db = types.SimpleNamespace(
		commit=_commit,
		rollback=_rollback,
		close=_close,
		connect=_connect,
		has_column=lambda *a, **k: True,
		exists=lambda *a, **k: None,
		get_value=lambda *a, **k: None,
		set_value=_set_value,
	)

	def _get_all(doctype, **kwargs):
		_require_connection()
		if doctype == "File":
			return _query_inserted("File", kwargs.get("filters"), kwargs.get("pluck"))
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

		def _fake_sync(service, doctype, docname, folder_id, cache, index=None):
			STATE["synced"].append(docname)
			exc = STATE["fail_on"].get(docname)
			if exc:
				if drive_sync._is_lost_connection(exc):
					STATE["connected"] = False
				raise exc

		drive_sync._sync_folder_shadows = _fake_sync
		drive_sync.get_drive_service = lambda: (_FakeDrive(), "shared-drive")

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

		def _fake_sync(service, doctype, docname, folder_id, cache, index=None):
			STATE["synced"].append(docname)
			self.clock.now += STATE.get("seconds_per_doc", 0)

		drive_sync._sync_folder_shadows = _fake_sync
		drive_sync.get_drive_service = lambda: (_FakeDrive(), "shared-drive")

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


class TestWholeDriveIndex(unittest.TestCase):
	"""One Shared Drive listing per run replaces a Google round-trip per folder.

	The production shape being guarded (measured 2026-09-17): 2,743 linked
	documents cost on the order of 12,000 Drive calls per hourly run — a
	``files.get`` per document to learn a drive id the run had been handed
	already, a ``files.list`` per folder, and every Project/Opportunity subtree
	listed twice because the Customer folder contains it — to find, on average,
	less than one new file. Now the drive is listed once and walked in memory.

	The tree (``_tree``) mirrors production: the Project folder sits *inside*
	the Customer folder, and both are linked roots.
	"""

	def setUp(self):
		_reset_state()
		STATE["rows"] = {"Project": ["P1"], "Customer": ["C1"], "Opportunity": []}
		self._real_service = drive_sync.get_drive_service
		self.tree = _tree()

	def tearDown(self):
		drive_sync.get_drive_service = self._real_service

	def _run_with(self, drive, shared_drive_id="shared-drive"):
		drive_sync.get_drive_service = lambda: (drive, shared_drive_id)
		drive_sync.run_shadow_sync()
		return drive

	def test_one_listing_and_no_call_per_document(self):
		drive = self._run_with(_FakeDrive(self.tree, page_size=4))

		# Six items at four per page: two listing calls, and nothing else.
		self.assertEqual(drive.calls, {"list_drive": 2, "list_folder": 0, "get": 0})
		# Every item became exactly one shadow, on the first document that reached
		# it (Projects walk before Customers) and never a second one: the Customer
		# walk still descends into the Project subtree — in memory — and finds
		# everything there already known.
		self.assertEqual(len(_shadows()), 5)
		self.assertEqual(
			_shadow_map(),
			{
				"design": ("Project", "P1", "Design (folder)"),
				"front": ("Project", "P1", "Design › front.png"),
				"quote": ("Project", "P1", "quote.pdf"),
				"brief": ("Customer", "C1", "brief.pdf"),
				"folder-P1": ("Customer", "C1", "PRJ-1 - Acme (folder)"),
			},
		)
		self.assertEqual(STATE["commits"], 2)
		self.assertEqual(STATE["errors"], [])

	def test_a_rerun_creates_nothing_and_flags_what_vanished(self):
		self._run_with(_FakeDrive(self.tree))
		before = len(_shadows())
		# quote.pdf is deleted in Drive before the next hour.
		remaining = [i for i in self.tree if i["id"] != "quote"]

		self._run_with(_FakeDrive(remaining))

		self.assertEqual(len(_shadows()), before)
		stale = [(r["reference_name"], r["drive_file_id"]) for r in _logs("Stale")]
		self.assertEqual(stale, [("P1", "quote")])

	def test_a_root_the_listing_lacks_is_asked_of_google(self):
		# P2's folder was deleted: it is not in the listing, and Google 404s it.
		STATE["rows"]["Project"] = ["P1", "P2"]
		drive = self._run_with(_FakeDrive(self.tree))

		self.assertEqual(drive.calls["get"], 1)
		self.assertEqual(drive.calls["list_folder"], 0)
		self.assertEqual([r["reference_name"] for r in _logs("Stale")], ["P2"])
		self.assertIn(("Project", "P2", drive_sync.MISSING_FLAG_FIELD, 1), STATE["set_values"])
		# P1 was unaffected.
		self.assertEqual(_shadow_map()["quote"], ("Project", "P1", "quote.pdf"))

	def test_a_root_outside_the_shared_drive_is_walked_live_not_flagged(self):
		# Absent from the listing is not the same as gone: a folder linked from
		# My Drive or another Shared Drive takes the per-folder walk it always did.
		STATE["rows"]["Project"] = ["P1", "P2"]
		elsewhere = [
			_folder("folder-P2", "PRJ-2", "my-drive"),
			_file("legacy", "legacy.pdf", "folder-P2"),
		]
		drive = self._run_with(_FakeDrive(self.tree, elsewhere=elsewhere))

		self.assertEqual(drive.calls["get"], 1)
		self.assertEqual(drive.calls["list_folder"], 1)
		self.assertEqual(_logs("Stale"), [])
		self.assertEqual(_shadow_map()["legacy"], ("Project", "P2", "legacy.pdf"))

	def test_a_failed_listing_degrades_to_the_per_folder_walk(self):
		drive = self._run_with(_FakeDrive(self.tree, fail_listing=True))

		# Logged once, then every document walked live: one get per root, one
		# list per folder (P1: itself + Design; C1: itself + P1 + Design).
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertIn("listing failed", STATE["errors"][0])
		self.assertEqual(drive.calls, {"list_drive": 1, "list_folder": 5, "get": 2})
		self.assertEqual(len(_shadows()), 5)

	def test_no_shared_drive_configured_means_no_listing(self):
		drive = self._run_with(_FakeDrive(self.tree), shared_drive_id="")

		self.assertEqual(drive.calls, {"list_drive": 0, "list_folder": 5, "get": 2})
		self.assertEqual(len(_shadows()), 5)
		self.assertEqual(STATE["errors"], [])

	def test_the_index_walk_matches_the_live_walk(self):
		# Same tree, same guards: a shortcut loop and a chain deeper than
		# MAX_SHADOW_DEPTH come out identical either way, and both terminate.
		tree = list(self.tree)
		tree.append(_folder("loop-a", "A", "folder-P1"))
		tree[-1]["parents"].append("loop-b")
		tree.append(_folder("loop-b", "B", "loop-a"))
		parent = "folder-P1"
		for depth in range(1, drive_sync.MAX_SHADOW_DEPTH + 3):
			tree.append(_folder(f"d{depth}", f"D{depth}", parent))
			parent = f"d{depth}"
		drive = _FakeDrive(tree)
		index = drive_sync._build_drive_index(drive, "shared-drive")

		via_index, via_live = [], []
		drive_sync._walk_drive_index(index, "folder-P1", "", 0, set(), via_index)
		drive_sync._walk_drive_folder(drive, "folder-P1", "shared-drive", "", 0, set(), via_live)

		flat = lambda items: [(item["id"], rel) for item, rel in items]  # noqa: E731
		self.assertEqual(flat(via_index), flat(via_live))
		ids = {item["id"] for item, _rel in via_index}
		self.assertIn(f"d{drive_sync.MAX_SHADOW_DEPTH + 1}", ids)
		self.assertNotIn(f"d{drive_sync.MAX_SHADOW_DEPTH + 2}", ids)
		self.assertEqual(drive.calls["list_drive"], 1)


class TestShadowNames(unittest.TestCase):
	"""Shadow names survive Frappe and follow Drive.

	Frappe v16's ``File.set_file_name`` runs ``re.sub(r"/", "", file_name)`` on
	every insert and save, so the "Design/Renderings/front.png" and "Design/"
	names this module built from the start were stored as
	"DesignRenderingsfront.png" and "Design" — 0 of 32,654 production shadows had
	a slash on 2026-09-17, and nothing noticed because the row still saved. The
	fake ``insert`` strips the same way, so a separator Frappe would eat fails
	here rather than in production.
	"""

	def setUp(self):
		_reset_state()
		STATE["rows"] = {"Project": ["P1"], "Customer": ["C1"], "Opportunity": []}
		self._real_service = drive_sync.get_drive_service
		self.tree = _tree()

	def tearDown(self):
		drive_sync.get_drive_service = self._real_service

	def _run_with(self, drive):
		drive_sync.get_drive_service = lambda: (drive, "shared-drive")
		drive_sync.run_shadow_sync()
		return drive

	def _renames(self):
		return [s for s in STATE["set_values"] if s[0] == "File"]

	def test_nested_names_keep_their_path_and_folders_are_marked(self):
		self._run_with(_FakeDrive(self.tree))

		names = {d["custom_drive_file_id"]: d["file_name"] for d in _shadows()}
		self.assertEqual(names["front"], "Design › front.png")
		self.assertEqual(names["design"], "Design (folder)")
		self.assertEqual(names["quote"], "quote.pdf")
		self.assertNotIn("/", drive_sync.SHADOW_PATH_SEPARATOR)
		self.assertNotIn("/", drive_sync.SHADOW_FOLDER_SUFFIX)

	def test_a_name_frappe_keeps_is_not_repaired_on_every_pass(self):
		# The repair compares the stored name against the computed one. If the
		# separator were one Frappe strips, every pass would rename every shadow.
		self._run_with(_FakeDrive(self.tree))
		self.assertEqual(self._renames(), [])

		self._run_with(_FakeDrive(self.tree))
		self.assertEqual(self._renames(), [])

	def test_flat_names_from_before_the_fix_are_repaired_in_place(self):
		# A shadow exactly as production stored it: slashes gone, attached to P1.
		STATE["inserted"].append(
			{
				"doctype": "File",
				"name": "F-old",
				"file_name": "Designfront.png",
				"file_url": "https://drive.google.com/file/d/front",
				"custom_drive_file_id": "front",
				"attached_to_doctype": "Project",
				"attached_to_name": "P1",
			}
		)

		self._run_with(_FakeDrive(self.tree))

		self.assertEqual(self._renames(), [("File", "F-old", "file_name", "Design › front.png")])
		# Repaired, not re-shadowed and not flagged.
		front = [d for d in _shadows() if d["custom_drive_file_id"] == "front"]
		self.assertEqual([d["name"] for d in front], ["F-old"])
		self.assertEqual(_logs("Stale"), [])

	def test_a_rename_or_move_in_drive_follows(self):
		self._run_with(_FakeDrive(self.tree))
		(front_row,) = [d for d in _shadows() if d["custom_drive_file_id"] == "front"]
		moved = [dict(i) for i in self.tree]
		for item in moved:
			if item["id"] == "front":
				item["name"] = "front-v2.png"
				item["parents"] = ["folder-P1"]  # out of Design, up to the root

		self._run_with(_FakeDrive(moved))

		self.assertEqual(self._renames(), [("File", front_row["name"], "file_name", "front-v2.png")])
		front = [d for d in _shadows() if d["custom_drive_file_id"] == "front"]
		self.assertEqual([d["file_name"] for d in front], ["front-v2.png"])
		self.assertEqual(_logs("Stale"), [])

	def test_a_name_over_the_limit_keeps_its_tail(self):
		deep = _file("deep", "x" * 20, "folder-P1")
		prefix = "a" * 150 + drive_sync.SHADOW_PATH_SEPARATOR

		name = drive_sync._shadow_display_name(deep, prefix)

		self.assertEqual(len(name), drive_sync.MAX_FILE_NAME_LENGTH)
		self.assertTrue(name.startswith("..."))
		self.assertTrue(name.endswith("x" * 20))


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
