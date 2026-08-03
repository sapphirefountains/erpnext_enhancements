"""Bench-free unit tests for the Drive linked-folder reconciliation
(``google_drive/drive_sync.py``).

Stubs a minimal ``frappe`` and the Google client libraries (no site, no bench, no
network) so ``drive_sync`` imports under plain unittest. Installed in
``setUpModule`` — execution time, not import time — so it never fools the
bench-only suites' ``import frappe`` skip-guards.

What is being guarded: a Drive folder id outlives the folder. Deleting one in
Drive, or moving it out of the Shared Drive, leaves the id on the record, and the
"Open Drive Folder" button then hands the user a bare Google 404 — PRJ-00706,
PRJ-00695 and CRM-OPP-2026-00113 were all in that state, and nothing in the app
knew. The shadow sync spotted some of them but only ever wrote to the Drive Sync
Log; the record was never told. So these assert the loop actually closes: a dead
folder ends up flagged *on the record*, a folder that comes back clears itself,
and one bad record never costs the rest of the run.

Run: python -m unittest erpnext_enhancements.tests.test_drive_link_reconcile
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

drive_sync = None

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
			"logs": [],  # Drive Sync Log rows written
			"commits": 0,
			"reconnects": 0,
			"connected": True,
			"reconnect_fails": False,
			"has_column": True,  # False simulates a pre-migrate bench
			"rows": {"Project": [], "Customer": [], "Opportunity": []},
			"flags": {},  # (doctype, docname) -> current custom_drive_folder_missing
			"folders": {},  # folder id -> meta dict, or None for a 404
			"probe_raises": {},  # folder id -> exception
			"stale_rows": {},  # log row name -> drive_file_id
			"status_writes": [],  # (log row name, new status)
			"drive_configured": True,
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
	frappe.enqueue = lambda *a, **k: STATE.setdefault("enqueued", []).append((a, k))
	frappe.get_single = lambda *a, **k: _Dict()

	def _get_cached_doc(*a, **k):
		return _Dict(
			attachment_sync_enabled=1,
			service_account_json="****" if STATE["drive_configured"] else "",
		)

	frappe.get_cached_doc = _get_cached_doc

	def _get_doc(payload=None, *a, **k):
		# Drive Sync Log rows are written through frappe.get_doc(...).insert().
		row = _Dict(payload or {})
		row.insert = lambda **kw: STATE["logs"].append(dict(row))
		return row

	frappe.get_doc = _get_doc

	def _require_connection():
		if not STATE["connected"]:
			raise _lost_connection()

	def _commit():
		_require_connection()
		STATE["commits"] += 1

	def _rollback(*a, **k):
		_require_connection()

	def _close():
		if not STATE["connected"]:
			raise _lost_connection()

	def _connect():
		STATE["reconnects"] += 1
		if STATE["reconnect_fails"]:
			raise _lost_connection()
		STATE["connected"] = True

	def _get_value(doctype, docname, field, *a, **k):
		_require_connection()
		if field == drive_sync.MISSING_FLAG_FIELD:
			return STATE["flags"].get((doctype, docname), 0)
		return None

	def _set_value(doctype, docname, field, value, *a, **k):
		_require_connection()
		if doctype == "Drive Sync Log":
			STATE["status_writes"].append((docname, value))
			return
		STATE["flags"][(doctype, docname)] = value

	frappe.db = types.SimpleNamespace(
		commit=_commit,
		rollback=_rollback,
		close=_close,
		connect=_connect,
		has_column=lambda *a, **k: STATE["has_column"],
		exists=lambda *a, **k: None,
		get_value=_get_value,
		set_value=_set_value,
	)

	def _get_all(doctype, **kwargs):
		_require_connection()
		if doctype == "Drive Sync Log":
			wanted = (kwargs.get("filters") or {}).get("drive_file_id")
			return [n for n, fid in STATE["stale_rows"].items() if fid == wanted]
		return [
			_Dict(name=name, custom_drive_folder_id=f"folder-{name}")
			for name in STATE["rows"].get(doctype, [])
		]

	frappe.get_all = _get_all

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	frappe.utils = utils
	frappe.__dict__["_"] = lambda s: s

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

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
	global drive_sync
	_install_stubs()
	_reset_state()
	from erpnext_enhancements.google_drive import drive_sync as module

	drive_sync = module


def _fake_get_folder_meta(service, folder_id, shared_drive_id=None):
	exc = STATE["probe_raises"].get(folder_id)
	if exc:
		raise exc
	return STATE["folders"].get(folder_id)


class TestProbeFolder(unittest.TestCase):
	def setUp(self):
		_reset_state()
		self._real = drive_sync.get_folder_meta
		drive_sync.get_folder_meta = _fake_get_folder_meta

	def tearDown(self):
		drive_sync.get_folder_meta = self._real

	def test_deleted_folder_is_gone(self):
		# get_folder_meta returns None on a 404 — the folder was deleted, or moved
		# somewhere the service account cannot see.
		STATE["folders"] = {"f1": None}
		gone, reason = drive_sync._probe_folder(None, "f1", "drive")
		self.assertTrue(gone)
		self.assertEqual(reason, drive_sync.FOLDER_GONE_MSG)

	def test_trashed_folder_is_gone(self):
		# Still resolves for the API, but it is not a place to send a person.
		STATE["folders"] = {"f1": {"id": "f1", "trashed": True}}
		gone, reason = drive_sync._probe_folder(None, "f1", "drive")
		self.assertTrue(gone)
		self.assertEqual(reason, drive_sync.FOLDER_TRASHED_MSG)

	def test_live_folder_is_not_gone(self):
		STATE["folders"] = {"f1": {"id": "f1", "trashed": False}}
		gone, reason = drive_sync._probe_folder(None, "f1", "drive")
		self.assertFalse(gone)
		self.assertIsNone(reason)

	def test_missing_trashed_key_is_not_gone(self):
		# Drive omits falsy fields on some responses; absence must not read as trashed.
		STATE["folders"] = {"f1": {"id": "f1"}}
		gone, _reason = drive_sync._probe_folder(None, "f1", "drive")
		self.assertFalse(gone)


class TestSetFolderMissing(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_sets_and_reports_the_change(self):
		self.assertTrue(drive_sync.set_folder_missing("Project", "P1", 1))
		self.assertEqual(STATE["flags"][("Project", "P1")], 1)

	def test_no_write_when_already_correct(self):
		# The daily pass touches every linked record; re-stamping an unchanged flag
		# would bump nothing useful and inflate the run's "newly missing" count.
		STATE["flags"][("Project", "P1")] = 1
		self.assertFalse(drive_sync.set_folder_missing("Project", "P1", 1))

	def test_clears_when_folder_returns(self):
		STATE["flags"][("Project", "P1")] = 1
		self.assertTrue(drive_sync.set_folder_missing("Project", "P1", 0))
		self.assertEqual(STATE["flags"][("Project", "P1")], 0)

	def test_no_column_is_a_no_op(self):
		# Scheduler jobs run during ERPNext's own test bootstrap, before this app's
		# custom fields exist. Writing then is a crash on a fresh install.
		STATE["has_column"] = False
		self.assertFalse(drive_sync.set_folder_missing("Project", "P1", 1))
		self.assertEqual(STATE["flags"], {})


class TestRunDriveLinkReconcile(unittest.TestCase):
	def setUp(self):
		_reset_state()
		self._real_meta = drive_sync.get_folder_meta
		self._real_service = drive_sync.get_drive_service
		drive_sync.get_folder_meta = _fake_get_folder_meta
		drive_sync.get_drive_service = lambda: (object(), "shared-drive")

	def tearDown(self):
		drive_sync.get_folder_meta = self._real_meta
		drive_sync.get_drive_service = self._real_service

	def _summary(self):
		rows = [r for r in STATE["logs"] if r.get("action") == "Reconcile Link"
				and r.get("status") == "Success"]
		return rows[-1]["file_name"] if rows else ""

	def test_dead_folder_is_flagged_on_the_record(self):
		# The whole point: the record itself learns, so the button can read it
		# without a Drive round-trip per click.
		STATE["rows"]["Project"] = ["PRJ-00706"]
		STATE["folders"] = {"folder-PRJ-00706": None}

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["flags"][("Project", "PRJ-00706")], 1)
		stale = [r for r in STATE["logs"] if r.get("status") == "Stale"]
		self.assertEqual(len(stale), 1)
		self.assertEqual(stale[0]["action"], "Reconcile Link")
		self.assertEqual(stale[0]["drive_file_id"], "folder-PRJ-00706")

	def test_live_folder_is_left_alone(self):
		STATE["rows"]["Project"] = ["PRJ-00700"]
		STATE["folders"] = {"folder-PRJ-00700": {"id": "x", "trashed": False}}

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["flags"], {})
		self.assertEqual([r for r in STATE["logs"] if r.get("status") == "Stale"], [])
		self.assertIn("Checked 1", self._summary())

	def test_restored_folder_clears_its_flag_and_rearms_the_log(self):
		# _flag_missing_drive_item dedupes on the Drive id, so a Stale row left
		# behind would silence the *next* disappearance forever.
		STATE["rows"]["Project"] = ["PRJ-00706"]
		STATE["flags"][("Project", "PRJ-00706")] = 1
		STATE["folders"] = {"folder-PRJ-00706": {"id": "x", "trashed": False}}
		STATE["stale_rows"] = {"LOG-1": "folder-PRJ-00706"}

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["flags"][("Project", "PRJ-00706")], 0)
		self.assertEqual(STATE["status_writes"], [("LOG-1", "Skipped")])
		self.assertIn("1 restored", self._summary())

	def test_all_three_doctypes_are_walked(self):
		STATE["rows"] = {
			"Project": ["PRJ-00706"],
			"Customer": ["Balance House"],
			"Opportunity": ["CRM-OPP-2026-00113"],
		}
		STATE["folders"] = {
			"folder-PRJ-00706": None,
			"folder-Balance House": {"id": "x"},
			"folder-CRM-OPP-2026-00113": None,
		}

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["flags"][("Project", "PRJ-00706")], 1)
		self.assertEqual(STATE["flags"][("Opportunity", "CRM-OPP-2026-00113")], 1)
		self.assertNotIn(("Customer", "Balance House"), STATE["flags"])
		self.assertIn("Checked 3", self._summary())
		self.assertIn("2 newly missing", self._summary())

	def test_one_bad_record_does_not_abort_the_run(self):
		STATE["rows"]["Project"] = ["P1", "P2", "P3"]
		STATE["folders"] = {"folder-P1": None, "folder-P3": None}
		STATE["probe_raises"] = {"folder-P2": ValueError("Drive said no")}

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["flags"][("Project", "P1")], 1)
		self.assertEqual(STATE["flags"][("Project", "P3")], 1)
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertIn("P2", STATE["errors"][0])

	def test_lost_connection_reconnects_and_continues(self):
		# Same failure mode the shadow sync hits: this walk also holds a connection
		# across minutes of Google traffic.
		def _die(service, folder_id, shared_drive_id=None):
			if folder_id == "folder-P2":
				STATE["connected"] = False
				raise _lost_connection()
			return STATE["folders"].get(folder_id)

		drive_sync.get_folder_meta = _die
		STATE["rows"]["Project"] = ["P1", "P2", "P3"]
		STATE["folders"] = {"folder-P1": None, "folder-P3": None}

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["reconnects"], 1)
		self.assertEqual(STATE["flags"][("Project", "P3")], 1)

	def test_unrecoverable_connection_stops_quietly(self):
		def _die(service, folder_id, shared_drive_id=None):
			STATE["connected"] = False
			raise _lost_connection()

		drive_sync.get_folder_meta = _die
		STATE["reconnect_fails"] = True
		STATE["rows"]["Project"] = ["P1", "P2"]

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["errors"], [])

	def test_does_nothing_without_a_service_account(self):
		STATE["drive_configured"] = False
		STATE["rows"]["Project"] = ["PRJ-00706"]
		STATE["folders"] = {"folder-PRJ-00706": None}

		drive_sync.run_drive_link_reconcile()

		self.assertEqual(STATE["flags"], {})
		self.assertEqual(STATE["logs"], [])


class TestScheduling(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_daily_entry_enqueues_the_worker(self):
		drive_sync.reconcile_drive_links()
		enqueued = STATE.get("enqueued") or []
		self.assertEqual(len(enqueued), 1)
		self.assertEqual(
			enqueued[0][0][0],
			"erpnext_enhancements.google_drive.drive_sync.run_drive_link_reconcile",
		)

	def test_daily_entry_is_dormant_without_a_key(self):
		STATE["drive_configured"] = False
		drive_sync.reconcile_drive_links()
		self.assertEqual(STATE.get("enqueued"), None)

	def test_reconcile_runs_even_when_attachment_sync_is_off(self):
		# The button exists whether or not the shadow sync is enabled, so its links
		# need checking either way — this is why the gate is _drive_configured and
		# not _sync_enabled.
		self.assertTrue(drive_sync._drive_configured())

	def test_settings_button_queues_the_same_worker(self):
		drive_sync.check_drive_links()
		enqueued = STATE.get("enqueued") or []
		self.assertEqual(
			enqueued[0][0][0],
			"erpnext_enhancements.google_drive.drive_sync.run_drive_link_reconcile",
		)


if __name__ == "__main__":
	unittest.main()
