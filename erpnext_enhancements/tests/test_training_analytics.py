"""Bench-free tests for the manager analytics rollup (WI-071 Phase H).

`get_training_analytics` is the one source of the dashboard's numbers, and the
things that can go quietly wrong with a rollup are all about the *edges*:

  * it is **manager-only** — it reports across every learner, which is what row
    scoping withholds from everyone else;
  * the **overdue** predicate excludes closed assignments, honours an explicit
    *Overdue* status, and only then looks at a past due date (a `<`-on-a-date in
    SQL would silently sweep in NULLs — see CLAUDE.md);
  * a **Cancelled** assignment is out of the completion-rate denominator, so it
    does not drag every rate down forever;
  * **average score** is measured on real completions, never on assignments;
  * **cohort progress** is completed (member × course) pairs over the expected
    total.

Plus source guards for the page controller and its route.

Run: python -m unittest erpnext_enhancements.tests.test_training_analytics
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
analytics = None

STATE = {}

NOW = "2026-09-08 12:00:00"
TODAY = "2026-09-08"


def _reset():
	STATE.clear()
	STATE.update(
		{
			"roles": ["Training Manager"],
			"enabled": True,
			"present": {
				"Training Assignment",
				"Training Completion",
				"Training Certificate",
				"Training Batch",
				"Training Submission",
			},
			"assignments": [],
			"completions": [],
			"certificates_valid": 0,
			"batches": [],
			"members_by_batch": {},
			"courses_by_batch": {},
			"submissions": [],
		}
	)


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit_page_length=None, **kwargs):
	filters = filters or {}
	rows = []
	if doctype == "Training Assignment":
		rows = STATE["assignments"]
	elif doctype == "Training Completion":
		rows = STATE["completions"]
	elif doctype == "Training Batch":
		rows = STATE["batches"]
	elif doctype == "Training Batch Member":
		vals = STATE["members_by_batch"].get(filters.get("parent"), [])
		return list(vals) if pluck else [_Dict(learner=v) for v in vals]
	elif doctype == "Training Batch Course":
		vals = STATE["courses_by_batch"].get(filters.get("parent"), [])
		return list(vals) if pluck else [_Dict(course=v) for v in vals]
	elif doctype == "Training Submission":
		rows = STATE["submissions"]
	if pluck:
		return [r.get(pluck) for r in rows]
	return [_Dict(r) for r in rows]


def _db_exists(doctype, name=None):
	if doctype == "DocType":
		return name in STATE["present"]
	return True


def _db_count(doctype, filters=None):
	if doctype == "Training Certificate":
		return STATE["certificates_valid"]
	return 0


def _getdate(value=None):
	if isinstance(value, datetime.date):
		return value
	if not value:
		return None
	return datetime.datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.session = _Dict(user="manager@example.com")
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.get_all = _get_all
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.__dict__["_"] = lambda s, *a, **k: s
	frappe.db = types.SimpleNamespace(exists=_db_exists, count=_db_count)

	utils = types.ModuleType("frappe.utils")
	utils.flt = lambda v: float(v or 0)
	utils.getdate = _getdate
	utils.now_datetime = lambda: NOW
	utils.today = lambda: TODAY
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	ts = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	)
	ts.is_enabled = lambda flag: STATE["enabled"]
	sys.modules[
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	] = ts


def setUpModule():
	global analytics
	_install_stubs()
	_reset()
	from erpnext_enhancements.training import analytics as an_module

	analytics = an_module


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()


def _assignment(user, course, status, due_date=None, title=None):
	return {"user": user, "course": course, "course_title": title or course, "status": status, "due_date": due_date}


class TestGate(_Base):
	def test_a_non_manager_is_refused(self):
		STATE["roles"] = ["Training Learner"]
		with self.assertRaises(Exception):
			analytics.get_training_analytics()

	def test_disabled_returns_a_flag_not_zeros(self):
		STATE["enabled"] = False
		self.assertEqual(analytics.get_training_analytics(), {"enabled": False})


class TestTotalsAndOverdue(_Base):
	def test_overdue_honours_the_predicate(self):
		STATE["assignments"] = [
			_assignment("a@x.com", "C1", "In Progress", due_date="2026-09-01"),   # past -> overdue
			_assignment("b@x.com", "C1", "Overdue"),                                # explicit -> overdue
			_assignment("c@x.com", "C1", "Completed", due_date="2026-01-01"),      # closed -> NOT overdue
			_assignment("d@x.com", "C1", "In Progress", due_date="2026-12-01"),    # future -> not overdue
			_assignment("e@x.com", "C1", "In Progress", due_date=None),            # no date -> not overdue
			_assignment("f@x.com", "C1", "Waived", due_date="2026-01-01"),         # closed -> not overdue
		]
		result = analytics.get_training_analytics()
		t = result["totals"]
		self.assertEqual(t["overdue"], 2)
		self.assertEqual(t["learners"], 6)
		self.assertEqual(t["active"], 4)  # 3 In Progress + 1 Overdue
		self.assertEqual(t["completed"], 1)

	def test_awaiting_signoff_and_certificates(self):
		STATE["assignments"] = [_assignment("a@x.com", "C1", "Awaiting Sign-off")]
		STATE["certificates_valid"] = 7
		t = analytics.get_training_analytics()["totals"]
		self.assertEqual(t["awaiting_signoff"], 1)
		self.assertEqual(t["certificates"], 7)


class TestByCourse(_Base):
	def test_rate_excludes_cancelled_and_scores_come_from_completions(self):
		STATE["assignments"] = [
			_assignment("a@x.com", "C1", "Completed", title="Basin care"),
			_assignment("b@x.com", "C1", "In Progress", title="Basin care"),
			_assignment("c@x.com", "C1", "Cancelled", title="Basin care"),   # out of the denominator
		]
		STATE["completions"] = [
			{"user": "a@x.com", "course": "C1", "course_title_snapshot": "Basin care", "score_percent": 90, "completed_on": "2026-09-07 10:00:00"},
			{"user": "z@x.com", "course": "C1", "course_title_snapshot": "Basin care", "score_percent": 70, "completed_on": "2026-09-06 10:00:00"},
		]
		row = next(r for r in analytics.get_training_analytics()["by_course"] if r["course"] == "C1")
		self.assertEqual(row["assigned"], 2)          # cancelled dropped
		self.assertEqual(row["completed"], 1)
		self.assertEqual(row["completion_rate"], 50)
		self.assertEqual(row["avg_score"], 80)        # (90 + 70) / 2

	def test_avg_score_is_none_without_completions(self):
		STATE["assignments"] = [_assignment("a@x.com", "C2", "In Progress")]
		row = next(r for r in analytics.get_training_analytics()["by_course"] if r["course"] == "C2")
		self.assertIsNone(row["avg_score"])


class TestByBatch(_Base):
	def test_cohort_progress_is_completed_pairs_over_expected(self):
		STATE["batches"] = [{"name": "B1", "title": "Autumn intake", "end_date": "2026-12-01"}]
		STATE["members_by_batch"] = {"B1": ["a@x.com", "b@x.com"]}
		STATE["courses_by_batch"] = {"B1": ["C1", "C2"]}
		# 4 expected pairs; a@C1 and a@C2 completed -> 2/4 = 50%.
		STATE["assignments"] = [
			_assignment("a@x.com", "C1", "Completed"),
			_assignment("a@x.com", "C2", "Completed"),
			_assignment("b@x.com", "C1", "In Progress"),
		]
		batch = analytics.get_training_analytics()["by_batch"][0]
		self.assertEqual(batch["members"], 2)
		self.assertEqual(batch["courses"], 2)
		self.assertEqual(batch["progress"], 50)

	def test_no_batch_doctype_yields_none(self):
		STATE["present"].discard("Training Batch")
		self.assertIsNone(analytics.get_training_analytics()["by_batch"])


class TestSubmissions(_Base):
	def test_backlog_counts(self):
		STATE["submissions"] = [
			{"status": "Submitted"},
			{"status": "Under Review"},
			{"status": "Passed"},
			{"status": "Needs Rework"},
			{"status": "Passed"},
		]
		s = analytics.get_training_analytics()["submissions"]
		self.assertEqual(s["pending"], 2)
		self.assertEqual(s["passed"], 2)
		self.assertEqual(s["needs_rework"], 1)

	def test_no_submission_doctype_yields_none(self):
		STATE["present"].discard("Training Submission")
		self.assertIsNone(analytics.get_training_analytics()["submissions"])


class TestRecentCompletions(_Base):
	def test_it_caps_at_ten_newest_first(self):
		STATE["completions"] = [
			{"user": f"u{i}@x.com", "course": "C1", "course_title_snapshot": "Basin care", "score_percent": i, "completed_on": f"2026-09-{i:02d} 10:00:00"}
			for i in range(12, 0, -1)  # already newest-first, the order get_all is asked for
		]
		recent = analytics.get_training_analytics()["recent_completions"]
		self.assertEqual(len(recent), 10)
		self.assertEqual(recent[0]["score_percent"], 12)


class TestPageSurface(unittest.TestCase):
	def test_the_controller_gates_to_managers_and_404s_others(self):
		src = (APP / "www/training_analytics.py").read_text(encoding="utf-8")
		self.assertIn('ALLOWED_ROLES = {"System Manager", "Training Manager", "HR Manager"}', src)
		self.assertIn("raise frappe.DoesNotExistError", src)
		self.assertIn("context.analytics = get_training_analytics()", src)

	def test_the_controller_filename_is_underscored(self):
		# Frappe never imports a hyphenated web controller; the template basename must
		# hyphen-to-underscore onto this exact name (scripts/check_www_controllers.py).
		self.assertTrue((APP / "www/training_analytics.py").exists())
		self.assertTrue((APP / "www/training_analytics.html").exists())

	def test_the_page_renders_the_rollup_server_side(self):
		html = (APP / "www/training_analytics.html").read_text(encoding="utf-8")
		self.assertIn("analytics.totals", html)
		self.assertIn("analytics.by_course", html)
		# Server-rendered, autoescaped Jinja — no client fetch, no innerHTML.
		self.assertNotIn("innerHTML", html)


if __name__ == "__main__":
	unittest.main()
