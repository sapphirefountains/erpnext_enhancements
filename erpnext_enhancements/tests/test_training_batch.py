"""Bench-free tests for Training Batch assignment fan-out (WI-071 Phase A).

The fan-out worker (`training/batch.py::sync_batch`) is stubbed against a recording
`training_author.run_bulk_assign` so the test can assert what actually matters
without a bench:

  * an **active** batch raises assignments for **every member × every course**,
    through the existing engine (one `run_bulk_assign` call per course with all the
    learners) — never a second assignment path;
  * a batch that is **not Active** is a no-op, and so is an empty roster or course
    set — so a paused/completed batch, or one still being assembled, raises nothing;
  * the due date prefers the cohort's **end date**, then its start date, then `None`
    (let the assignment controller apply the course default);
  * **`enrolled_on` is stamped once** per member, straight to the child row, so it
    does not re-enter the controller's `on_update`.

Plus two source guards that a passing call cannot show: the controller **enqueues**
the fan-out on Active and **derives the Employee from the User** (the shipped
`fetch_from: learner.name` copied a login id into an Employee link), and the member
doctype no longer carries that broken `fetch_from`.

Run: python -m unittest erpnext_enhancements.tests.test_training_batch
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
batch = None

STATE = {}


def _reset():
	STATE.clear()
	STATE.update({"batch": None, "assign_calls": [], "set_values": [], "enqueued": []})


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


def _get_doc(doctype, name=None):
	if doctype == "Training Batch":
		b = STATE["batch"]
		return _Dict(
			name=name,
			status=b["status"],
			owner=b.get("owner", "manager@example.com"),
			start_date=b.get("start_date"),
			end_date=b.get("end_date"),
			members=[
				_Dict(
					name=m.get("name", f"BM-{i}"),
					learner=m.get("learner"),
					employee=m.get("employee"),
					enrolled_on=m.get("enrolled_on"),
				)
				for i, m in enumerate(b.get("members", []))
			],
			courses=[_Dict(course=c) for c in b.get("courses", [])],
		)
	raise Exception(f"unexpected get_doc({doctype!r})")


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_doc = _get_doc
	frappe.enqueue = lambda *a, **k: STATE["enqueued"].append(k)

	def _set_value(doctype, name, field, value=None, **kwargs):
		STATE["set_values"].append({"doctype": doctype, "name": name, "field": field, "value": value})

	frappe.db = types.SimpleNamespace(
		set_value=_set_value,
		get_value=lambda *a, **k: None,
	)
	frappe.__dict__["_"] = lambda s: s

	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: "2026-09-07 12:00:00"
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# The recording assignment engine. run_bulk_assign is imported lazily inside
	# sync_batch, so this stub only has to exist by the time it runs.
	ta = types.ModuleType("erpnext_enhancements.api.training_author")

	def _run_bulk_assign(course, targets, due_date=None, assigned_by=None):
		targets = list(targets)
		STATE["assign_calls"].append(
			{"course": course, "targets": targets, "due_date": due_date, "assigned_by": assigned_by}
		)
		return len(targets)

	ta.run_bulk_assign = _run_bulk_assign
	sys.modules["erpnext_enhancements.api.training_author"] = ta


def setUpModule():
	global batch
	_install_stubs()
	_reset()
	from erpnext_enhancements.training import batch as module

	batch = module


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()


ACTIVE = {
	"status": "Active",
	"start_date": "2026-09-01",
	"end_date": "2026-09-30",
	"members": [{"learner": "a@x.com"}, {"learner": "b@x.com"}],
	"courses": ["TRN-CRS-00001", "TRN-CRS-00002"],
}


class TestFanOut(_Base):
	def test_active_batch_assigns_every_member_to_every_course(self):
		STATE["batch"] = dict(ACTIVE)
		created = batch.sync_batch("TRN-BATCH-00001")
		# One call per course, each with both learners.
		self.assertEqual(len(STATE["assign_calls"]), 2)
		self.assertEqual([c["course"] for c in STATE["assign_calls"]], ["TRN-CRS-00001", "TRN-CRS-00002"])
		for call in STATE["assign_calls"]:
			self.assertEqual(call["targets"], ["a@x.com", "b@x.com"])
		self.assertEqual(created, 4)  # 2 courses x 2 learners

	def test_a_non_active_batch_raises_nothing(self):
		STATE["batch"] = dict(ACTIVE, status="Planning")
		self.assertEqual(batch.sync_batch("B"), 0)
		self.assertEqual(STATE["assign_calls"], [])

	def test_no_members_is_a_noop(self):
		STATE["batch"] = dict(ACTIVE, members=[])
		self.assertEqual(batch.sync_batch("B"), 0)
		self.assertEqual(STATE["assign_calls"], [])

	def test_no_courses_is_a_noop(self):
		STATE["batch"] = dict(ACTIVE, courses=[])
		self.assertEqual(batch.sync_batch("B"), 0)
		self.assertEqual(STATE["assign_calls"], [])

	def test_a_member_with_no_learner_is_skipped(self):
		STATE["batch"] = dict(ACTIVE, members=[{"learner": "a@x.com"}, {"learner": None}], courses=["C1"])
		batch.sync_batch("B")
		self.assertEqual(STATE["assign_calls"][0]["targets"], ["a@x.com"])


class TestDueDate(_Base):
	def test_prefers_the_end_date(self):
		STATE["batch"] = dict(ACTIVE)
		batch.sync_batch("B")
		self.assertEqual(STATE["assign_calls"][0]["due_date"], "2026-09-30")

	def test_falls_back_to_the_start_date(self):
		STATE["batch"] = dict(ACTIVE, end_date=None)
		batch.sync_batch("B")
		self.assertEqual(STATE["assign_calls"][0]["due_date"], "2026-09-01")

	def test_falls_back_to_none(self):
		STATE["batch"] = dict(ACTIVE, end_date=None, start_date=None)
		batch.sync_batch("B")
		self.assertIsNone(STATE["assign_calls"][0]["due_date"])


class TestEnrolledOn(_Base):
	def test_unstamped_members_get_stamped_once(self):
		STATE["batch"] = dict(
			ACTIVE,
			members=[
				{"name": "BM-0", "learner": "a@x.com"},
				{"name": "BM-1", "learner": "b@x.com", "enrolled_on": "2026-08-01 09:00:00"},
			],
			courses=["C1"],
		)
		batch.sync_batch("B")
		stamps = [s for s in STATE["set_values"] if s["field"] == "enrolled_on"]
		# Only the member without a stamp gets one.
		self.assertEqual(len(stamps), 1)
		self.assertEqual(stamps[0]["name"], "BM-0")
		self.assertEqual(stamps[0]["value"], "2026-09-07 12:00:00")


class TestSourceGuards(_Base):
	def test_the_controller_enqueues_the_fanout_on_active(self):
		src = (APP / "training/doctype/training_batch/training_batch.py").read_text(encoding="utf-8")
		self.assertIn('status == "Active"', src)
		self.assertIn("training.batch.sync_batch", src)
		self.assertIn("enqueue_after_commit=True", src)

	def test_the_controller_derives_employee_from_the_user(self):
		src = (APP / "training/doctype/training_batch/training_batch.py").read_text(encoding="utf-8")
		# Matched on user_id, not fetched from learner.name.
		self.assertIn('"Employee", {"user_id"', src)

	def test_the_member_doctype_no_longer_fetches_employee_from_learner_name(self):
		member = json.loads(
			(APP / "training/doctype/training_batch_member/training_batch_member.json").read_text(encoding="utf-8")
		)
		employee = next(f for f in member["fields"] if f["fieldname"] == "employee")
		self.assertNotIn("fetch_from", employee)


if __name__ == "__main__":
	unittest.main()
