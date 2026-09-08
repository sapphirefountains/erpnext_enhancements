"""Bench-free tests for training announcements (WI-071 Phase D).

  * **the controller validates scope** — a Course announcement must name a course, a
    Batch one a batch, and switching scope clears the field that no longer applies so
    a stale course does not quietly keep narrowing who sees it (executable — the
    controller imports only frappe + a Document base);
  * **the doctype shape** — the scope options, the required body, the module;
  * **the learner surface is wired and scoped per scope** — `get_learner_bootstrap`
    sends an `announcements` key, and `_learner_announcements` queries `All Learners`,
    `Course` (the learner's own courses) and `Batch` (their own batches) as *separate*
    scoped reads, so a course or batch announcement never reaches anyone outside it.
    Source assertions, because importing `api/training.py` pulls in half the module.

Run: python -m unittest erpnext_enhancements.tests.test_training_announcements
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
controller = None


def _install_stubs():
	frappe = types.ModuleType("frappe")

	class _ValidationError(Exception):
		pass

	frappe.ValidationError = _ValidationError

	def _throw(msg, exc=None):
		raise (exc or _ValidationError)(msg)

	frappe.throw = _throw
	frappe.__dict__["_"] = lambda s, *a, **k: s
	sys.modules["frappe"] = frappe

	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: "2026-09-08 12:00:00"
	frappe.utils = utils
	sys.modules["frappe.utils"] = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document


def setUpModule():
	global controller
	_install_stubs()
	from erpnext_enhancements.training.doctype.training_announcement import training_announcement as ctrl

	controller = ctrl


def _doc(scope=None, course=None, batch=None, body=None, posted_on=None):
	doc = controller.TrainingAnnouncement()
	doc.scope = scope
	doc.course = course
	doc.batch = batch
	doc.body = body
	doc.posted_on = posted_on
	return doc


class TestScopeValidation(unittest.TestCase):
	def test_a_course_announcement_must_name_a_course(self):
		with self.assertRaises(Exception):
			_doc(scope="Course", course=None, body="x").validate()

	def test_a_batch_announcement_must_name_a_batch(self):
		with self.assertRaises(Exception):
			_doc(scope="Batch", batch=None, body="x").validate()

	def test_a_course_scope_clears_a_stale_batch(self):
		doc = _doc(scope="Course", course="TRN-CRS-1", batch="TRN-BATCH-1", body="x")
		doc.validate()
		self.assertEqual(doc.course, "TRN-CRS-1")
		self.assertIsNone(doc.batch)

	def test_all_learners_clears_both(self):
		doc = _doc(scope="All Learners", course="TRN-CRS-1", batch="TRN-BATCH-1", body="x")
		doc.validate()
		self.assertIsNone(doc.course)
		self.assertIsNone(doc.batch)

	def test_posted_on_is_stamped_once(self):
		doc = _doc(scope="All Learners", body="x")
		doc.validate()
		self.assertEqual(doc.posted_on, "2026-09-08 12:00:00")
		# A second save keeps the original stamp.
		doc.posted_on = "2026-01-01 00:00:00"
		doc.validate()
		self.assertEqual(doc.posted_on, "2026-01-01 00:00:00")


class TestShapeAndSurface(unittest.TestCase):
	def test_doctype_shape(self):
		spec = json.loads(
			(APP / "training/doctype/training_announcement/training_announcement.json").read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in spec["fields"]}
		self.assertEqual(fields["scope"]["options"], "All Learners\nCourse\nBatch")
		self.assertEqual(fields["body"].get("reqd"), 1)
		self.assertEqual(fields["published"].get("default"), "1")
		self.assertEqual(spec["module"], "Training")

	def test_the_surface_is_wired_and_scoped_per_scope(self):
		api = (APP / "api/training.py").read_text(encoding="utf-8")
		self.assertIn('"announcements": _learner_announcements(user)', api)
		start = api.index("def _learner_announcements")
		body = api[start : api.index("\ndef ", start + 1)]
		# Each scope is a separate scoped read — a course/batch announcement never
		# leaks to someone outside it.
		self.assertIn('"scope": "All Learners"', body)
		self.assertIn('"scope": "Course", "course": ["in"', body)
		self.assertIn('"scope": "Batch", "batch": ["in"', body)
		self.assertIn('"published": 1', body)

	def test_the_player_reads_announcements(self):
		player = (APP / "public/js/training/player.js").read_text(encoding="utf-8")
		self.assertIn("b.announcements", player)


if __name__ == "__main__":
	unittest.main()
