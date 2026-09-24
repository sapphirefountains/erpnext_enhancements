"""Bench-free tests for ``product_feedback/capture_jobs.py`` (WI-079 slice 2).

**Retention.** A request's screenshots and capture-context file go 180 days after it closes,
and nothing else attached to it goes with them. The job selects on
``coalesce(terminal_at, modified)``, joined to ``File`` on the capture artifacts only, so a
cleaned request drops out even when it keeps a PDF, and commits per deletion. The same run
deletes the panel's screenshot uploads that never reached a request. Covers acceptance criterion "closed 181 days ago keeps its text and
loses its files; closed 179 days ago keeps both". That comparison is SQL, so the tests pin the
cutoff the job computes and the predicate it sends.

**Error Log matching.** A failed request in a snapshot is paired with the Error Log row whose
``metadata`` has the same user, verb and path, inside the window the deferred-insert flush can
put it in. Browser clock skew is removed. A row is used once. A match is noted once, however
often the job runs.

``frappe`` is a local stub, so this suite has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_capture_jobs -v
"""

import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

NOW = datetime(2026, 9, 23, 20, 0, 0)
_saved = {}
jobs = None
W = None  # the stub's world, reset per test


class World:
	def __init__(self):
		self.sql_calls = []
		self.sql_results = []
		self.files_by_request = {}
		self.deleted = []
		self.delete_fails = set()
		self.commits = 0
		self.rollbacks = 0
		self.requests = []
		self.snapshots = {}
		self.comments = []
		self.errors = []


def _install():
	frappe = types.ModuleType("frappe")
	frappe._dict = lambda d=None, **k: types.SimpleNamespace(**(d or {}), **k)

	def sql(query, values=None, as_dict=False):
		W.sql_calls.append((query, values))
		return W.sql_results.pop(0) if W.sql_results else []

	def get_value(doctype, filters, field, *a, **k):
		if doctype == "File":
			name = filters.get("attached_to_name")
			return f"FILE-{name}" if name in W.snapshots else None
		return None

	def exists(doctype, filters):
		if doctype == "Comment":
			needle = filters["content"][1].strip("%")
			return any(c["reference_name"] == filters["reference_name"] and needle in c["content"] for c in W.comments)
		return False

	def commit():
		W.commits += 1

	def rollback():
		W.rollbacks += 1

	frappe.db = types.SimpleNamespace(sql=sql, get_value=get_value, exists=exists, commit=commit, rollback=rollback)

	def get_all(doctype, filters=None, fields=None, **k):
		if doctype == "File":
			name = filters["attached_to_name"]
			return [types.SimpleNamespace(**f) for f in W.files_by_request.get(name, [])]
		if doctype == "Enhancement Request":
			return [types.SimpleNamespace(**r) for r in W.requests]
		return []

	frappe.get_all = get_all

	def delete_doc(doctype, name, **k):
		if name in W.delete_fails:
			raise RuntimeError("disk")
		W.deleted.append(name)

	frappe.delete_doc = delete_doc

	class FileDoc:
		def __init__(self, name):
			self.name = name

		def get_content(self):
			return json.dumps(W.snapshots[self.name.removeprefix("FILE-")])

	class CommentDoc:
		def __init__(self, values):
			self.values = values

		def insert(self, ignore_permissions=False):
			W.comments.append(self.values)

	def get_doc(arg, name=None):
		if isinstance(arg, dict):
			return CommentDoc(arg)
		return FileDoc(name)

	frappe.get_doc = get_doc
	frappe.log_error = lambda *a, **k: W.errors.append(k.get("title"))

	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: NOW
	utils.add_days = lambda d, n: d + timedelta(days=n)
	utils.get_datetime = lambda v: v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
	utils.escape_html = lambda s: str(s).replace("<", "&lt;").replace(">", "&gt;")
	frappe.utils = utils

	for name in ("frappe", "frappe.utils"):
		_saved[name] = sys.modules.get(name)
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global jobs
	_install()
	sys.modules.pop("erpnext_enhancements.product_feedback.capture_jobs", None)
	from erpnext_enhancements.product_feedback import capture_jobs

	jobs = capture_jobs


def tearDownModule():
	for name, mod in _saved.items():
		if mod is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = mod
	sys.modules.pop("erpnext_enhancements.product_feedback.capture_jobs", None)


class Base(unittest.TestCase):
	def setUp(self):
		global W
		W = World()


class TestWhatCountsAsAnArtifact(Base):
	def test_screenshots_and_the_context_file(self):
		for name in ("capture-context-ER-2026-00001.json", "Screenshot 2026-09-23.PNG", "photo.jpeg", "x.webp"):
			self.assertTrue(jobs.is_capture_artifact(name), name)

	def test_nothing_else_attached_to_a_request(self):
		for name in ("invoice.pdf", "error.log", "notes.txt", "data.json", "noextension", ""):
			self.assertFalse(jobs.is_capture_artifact(name), name)


class TestRetention(Base):
	def test_the_cutoff_is_180_days_and_the_clock_is_terminal_at_then_modified(self):
		jobs.purge_expired_capture_files()
		query, values = W.sql_calls[0]
		self.assertEqual(values["cutoff"], NOW - timedelta(days=180))
		self.assertIn("coalesce(er.terminal_at, er.modified) < %(cutoff)s", " ".join(query.split()))
		self.assertEqual(set(values["terminal"]), {"Tasks Created", "Rejected", "Duplicate"})
		# Plain strings, not the enum members: str-enum equality hides the difference from the
		# assertion above, and the driver escapes a member as 'RequestState.REJECTED'.
		self.assertTrue(all(type(v) is str for v in values["terminal"]), values["terminal"])
		self.assertIn("join `tabFile` f", query)

	def test_the_join_selects_only_requests_with_an_artifact_left(self):
		jobs.purge_expired_capture_files()
		query, values = W.sql_calls[0]
		flat = " ".join(query.split())
		self.assertIn("lower(f.file_name) like %(context_like)s", flat)
		self.assertIn("lower(substring_index(f.file_name, '.', -1)) in %(extensions)s", flat)
		self.assertEqual(values["context_like"], "capture-context-%")
		# The same extensions is_capture_artifact deletes, so the two tests cannot drift apart.
		self.assertEqual(set(values["extensions"]), set(jobs.SCREENSHOT_EXTENSIONS))
		self.assertTrue(all(type(v) is str for v in values["extensions"]))

	def test_only_artifacts_are_deleted_each_with_its_own_commit(self):
		W.sql_results = [[types.SimpleNamespace(name="ER-1")]]
		W.files_by_request["ER-1"] = [
			{"name": "F1", "file_name": "capture-context-ER-1.json"},
			{"name": "F2", "file_name": "shot.png"},
			{"name": "F3", "file_name": "contract.pdf"},
		]
		out = jobs.purge_expired_capture_files()
		self.assertEqual(W.deleted, ["F1", "F2"])
		self.assertEqual(W.commits, 2)
		self.assertEqual(out, {"requests": 1, "deleted": 2, "failed": 0, "orphans": 0})

	def test_one_failed_delete_rolls_back_alone_and_the_rest_continue(self):
		W.sql_results = [[types.SimpleNamespace(name="ER-1")]]
		W.files_by_request["ER-1"] = [
			{"name": "F1", "file_name": "a.png"},
			{"name": "F2", "file_name": "b.png"},
		]
		W.delete_fails = {"F1"}
		out = jobs.purge_expired_capture_files()
		self.assertEqual(W.deleted, ["F2"])
		self.assertEqual((W.rollbacks, out["failed"]), (1, 1))

	def test_nothing_left_means_nothing_done(self):
		self.assertEqual(
			jobs.purge_expired_capture_files(), {"requests": 0, "deleted": 0, "failed": 0, "orphans": 0}
		)


class TestOrphanScreenshots(Base):
	def test_only_the_panels_unattached_private_uploads_a_day_old(self):
		jobs.purge_orphan_screenshots()
		query, values = W.sql_calls[0]
		flat = " ".join(query.split())
		self.assertIn("coalesce(attached_to_doctype, '') = ''", flat)
		self.assertIn("is_private = 1", flat)
		self.assertIn("file_name like %(prefix)s", flat)
		self.assertEqual(values["prefix"], "capture-shot-%")
		self.assertEqual(values["cutoff"], NOW - timedelta(days=1))

	def test_each_orphan_is_deleted_with_its_own_commit(self):
		W.sql_results = [[types.SimpleNamespace(name="S1"), types.SimpleNamespace(name="S2")]]
		W.delete_fails = {"S1"}
		self.assertEqual(jobs.purge_orphan_screenshots(), 1)
		self.assertEqual((W.deleted, W.commits, W.rollbacks), (["S2"], 1, 1))

	def test_the_daily_run_includes_them(self):
		W.sql_results = [[], [types.SimpleNamespace(name="S1")]]
		self.assertEqual(jobs.purge_expired_capture_files()["orphans"], 1)

	def test_the_prefix_is_the_panels(self):
		panel = (REPO_ROOT / "erpnext_enhancements" / "public" / "js" / "capture" / "panel.js").read_text(encoding="utf-8")
		self.assertIn(f'export const SHOT_PREFIX = "{jobs.CAPTURE_SHOT_PREFIX}";', panel)


FILED = datetime(2026, 9, 23, 12, 0, 0)


def _row(name, created, user="nik@example.com", method="POST", path="/api/method/frappe.desk.form.save.savedocs"):
	return {
		"name": name,
		"method": "TypeError: 'NoneType' object is not subscriptable",
		"creation": created,
		"metadata": json.dumps({"user": user, "method": method, "path": path, "type": "http_request"}),
	}


REQ = {
	"at": "2026-09-23T11:59:00",
	"method": "POST",
	"path": "/api/method/frappe.desk.form.save.savedocs",
	"status": 500,
	"exc_type": "TypeError",
}


class TestMatching(Base):
	def test_only_server_errors_are_candidates(self):
		snap = {"requests": [dict(REQ), dict(REQ, status=417), dict(REQ, status=0), {"status": 502}]}
		self.assertEqual(len(jobs.failed_server_requests(snap)), 1)

	def test_browser_clock_skew_is_removed(self):
		# The browser's clock runs 7 minutes fast, but the failure was 60 s before capture.
		when = jobs.estimate_server_time("2026-09-23T12:06:00", "2026-09-23T12:07:00", FILED)
		self.assertEqual(when, FILED - timedelta(seconds=60))

	def test_the_reference_is_sent_at_when_the_panel_stamped_it(self):
		self.assertEqual(jobs.reference_time({"captured_at": "c", "sent_at": "s"}), "s")
		self.assertEqual(jobs.reference_time({"captured_at": "c"}), "c")
		self.assertIsNone(jobs.reference_time(None))

	def test_time_spent_composing_does_not_shift_the_estimate(self):
		# Panel opened at 12:00 (browser), failure 60 s before; the person typed for 25 minutes and
		# sent at 12:25 (browser). The report arrived at FILED. Measured from sent_at the failure
		# is 26 minutes before FILED; from captured_at it would wrongly be 1 minute before.
		snap = {"captured_at": "2026-09-23T12:00:00", "sent_at": "2026-09-23T12:25:00"}
		when = jobs.estimate_server_time("2026-09-23T11:59:00", jobs.reference_time(snap), FILED)
		self.assertEqual(when, FILED - timedelta(minutes=26))

	def test_same_user_verb_and_path_inside_the_flush_window_matches(self):
		flushed = FILED + timedelta(minutes=9)  # the 0/15 flush wrote it later
		matches = jobs.match_error_logs([REQ], [_row("E1", flushed)], "nik@example.com", "2026-09-23T12:00:00", FILED)
		self.assertEqual([m[1]["name"] for m in matches], ["E1"])

	def test_another_user_path_verb_or_time_does_not(self):
		rows = [
			_row("E-user", FILED, user="jo@example.com"),
			_row("E-path", FILED, path="/api/method/other"),
			_row("E-verb", FILED, method="GET"),
			_row("E-late", FILED + timedelta(minutes=40)),
			_row("E-early", FILED - timedelta(minutes=30)),
		]
		self.assertEqual(jobs.match_error_logs([REQ], rows, "nik@example.com", "2026-09-23T12:00:00", FILED), [])

	def test_the_nearest_row_wins_and_a_row_is_used_once(self):
		rows = [_row("far", FILED + timedelta(minutes=14)), _row("near", FILED + timedelta(minutes=1))]
		two = [dict(REQ), dict(REQ)]
		matches = jobs.match_error_logs(two, rows, "nik@example.com", "2026-09-23T12:00:00", FILED)
		self.assertEqual([m[1]["name"] for m in matches], ["near", "far"])


class TestMatchJob(Base):
	def setUp(self):
		super().setUp()
		W.requests = [{"name": "ER-9", "requested_by": "nik@example.com", "requested_at": FILED}]
		W.snapshots["ER-9"] = {"captured_at": "2026-09-23T12:00:00", "requests": [dict(REQ)]}

	def test_a_match_becomes_a_comment_that_keeps_the_facts(self):
		W.sql_results = [[_row("E1", FILED + timedelta(minutes=5))]]
		out = jobs.match_capture_error_logs()
		self.assertEqual(out["noted"], 1)
		(c,) = W.comments
		self.assertEqual((c["reference_doctype"], c["reference_name"]), ("Enhancement Request", "ER-9"))
		self.assertIn("E1", c["content"])
		self.assertIn("/api/method/frappe.desk.form.save.savedocs", c["content"])
		self.assertIn("TypeError", c["content"])

	def test_running_again_notes_nothing_new(self):
		W.sql_results = [[_row("E1", FILED + timedelta(minutes=5))], [_row("E1", FILED + timedelta(minutes=5))]]
		jobs.match_capture_error_logs()
		jobs.match_capture_error_logs()
		self.assertEqual(len(W.comments), 1)

	def test_a_capture_with_no_server_error_asks_the_database_nothing(self):
		W.snapshots["ER-9"] = {"requests": [dict(REQ, status=404)]}
		jobs.match_capture_error_logs()
		self.assertEqual(W.sql_calls, [])

	def test_the_job_measures_from_sent_at(self):
		# Composed for 30 minutes: the Error Log was flushed 5 minutes after the real failure,
		# which is 25 minutes before filing. Measured from captured_at it would fall outside.
		W.snapshots["ER-9"] = {
			"captured_at": "2026-09-23T12:00:00",
			"sent_at": "2026-09-23T12:30:00",
			"requests": [dict(REQ, at="2026-09-23T11:59:00")],
		}
		W.sql_results = [[_row("E1", FILED - timedelta(minutes=26))]]
		self.assertEqual(jobs.match_capture_error_logs()["noted"], 1)

	def test_the_lookup_is_prefiltered_on_the_user_and_a_window(self):
		W.sql_results = [[]]
		jobs.match_capture_error_logs()
		query, values = W.sql_calls[0]
		self.assertIn("nik@example.com", values["user_like"])
		self.assertLess(values["lo"], FILED)
		self.assertGreater(values["hi"], FILED)


if __name__ == "__main__":
	unittest.main()
