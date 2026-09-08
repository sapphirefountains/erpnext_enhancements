"""Bench-free tests for learner work submissions and grading (WI-071 Phase F).

The properties that matter, and each is a way the feature could quietly go wrong:

  * **a learner can only submit against a course they may actually take** — the gate
    is the same visibility predicate as taking it, and it is on the server;
  * **the submitted file follows the record** — submit_work re-parents the private
    File onto the new submission, so a learner reading their own submission can open
    their file and nobody else can, without a bespoke serving route;
  * **a learner cannot staple someone else's private file to a submission** — the
    file must be theirs;
  * **grading is Training-Manager-only**, re-checked server-side whatever the desk
    shows; a `Needs Rework` verdict must carry feedback; a terminal verdict stamps
    the grader and the time;
  * **row scoping** hands a learner their own submissions and nobody else's, while
    the unscoped roles see the whole queue.

Run: python -m unittest erpnext_enhancements.tests.test_training_submissions
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
submissions = None
permissions = None
controller = None

STATE = {}

NOW = "2026-09-08 12:00:00"


def _reset():
	STATE.clear()
	STATE.update(
		{
			"user": "learner@example.com",
			"roles": ["Training Learner"],
			"visible_courses": {"TRN-CRS-1"},
			"current_version": "TRN-CV-1",
			"lesson": {"name": "TRN-LSN-1", "lesson_title": "Draining a basin", "requires_submission": 1},
			"file_row": {"name": "FILE-1", "owner": "learner@example.com", "is_private": 1},
			"submission": None,
			"queue_rows": [],
			"graders": ["manager@example.com"],
			"reports_to": {},        # employee name -> [report user_ids]
			"employee_of": {},       # user -> employee name
			"course_titles": {"TRN-CRS-1": "Basin care"},
			"lesson_titles": {"TRN-LSN-1": "Draining a basin"},
			"inserted": [],
			"set_values": [],
			"saved": [],
			"counter": 0,
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


class _StubDoc(_Dict):
	def insert(self, **kwargs):
		STATE["counter"] += 1
		self["name"] = self.get("name") or f"TRN-SUB-{STATE['counter']:04d}"
		STATE["inserted"].append(self)
		return self

	def save(self, **kwargs):
		STATE["saved"].append(self)
		return self


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _StubDoc(doctype)
	if doctype == "Training Submission":
		return _StubDoc(STATE["submission"])
	raise Exception(f"unexpected get_doc({doctype!r})")


def _db_get_value(doctype, name, fieldname=None, **kwargs):
	as_dict = kwargs.get("as_dict")
	if doctype == "Training Course" and fieldname == "current_version":
		return STATE["current_version"]
	if doctype == "Training Course" and fieldname == "course_title":
		return STATE["course_titles"].get(name)
	if doctype == "Training Lesson" and fieldname == "lesson_title":
		return STATE["lesson_titles"].get(name)
	if doctype == "Training Lesson" and as_dict:
		row = STATE["lesson"]
		return _Dict(row) if row else None
	if doctype == "File" and as_dict:
		row = STATE["file_row"]
		return _Dict(row) if row else None
	if doctype == "Employee" and fieldname == "name":
		# _direct_report_users: user_id -> employee name
		return STATE["employee_of"].get(name.get("user_id") if isinstance(name, dict) else name)
	return None


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **kwargs):
	filters = filters or {}
	if doctype == "Has Role":
		return list(STATE["graders"])
	if doctype == "Training Submission":
		return [_Dict(r) for r in STATE["queue_rows"]]
	if doctype == "Employee":
		# _direct_report_users second call: reports_to -> [user_ids]
		manager = filters.get("reports_to")
		return list(STATE["reports_to"].get(manager, []))
	return []


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_doc = _get_doc
	frappe.session = _Dict(user="learner@example.com")
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.get_all = _get_all
	frappe.flags = _Dict(in_migrate=0, in_install=0, in_patch=0, in_import=0)
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.__dict__["_"] = lambda s, *a, **k: s

	def _set_value(doctype, name, field, value=None, **kwargs):
		if isinstance(field, dict):
			for key, val in field.items():
				STATE["set_values"].append({"doctype": doctype, "name": name, "field": key, "value": val})
		else:
			STATE["set_values"].append({"doctype": doctype, "name": name, "field": field, "value": value})

	def _escape(value):
		return "'" + str(value).replace("'", "''") + "'"

	frappe.db = types.SimpleNamespace(
		get_value=_db_get_value,
		set_value=_set_value,
		exists=lambda *a, **k: True,
		escape=_escape,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.get_url = lambda p="": "https://erp.example.com" + p
	utils.escape_html = lambda s: s or ""
	utils.now_datetime = lambda: NOW
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

	# Training Settings gate.
	ts = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	)
	ts.is_enabled = lambda flag: True
	sys.modules[
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	] = ts

	# The visibility predicate, stubbed. submit_work must gate on this, not a cheaper copy.
	api_training = types.ModuleType("erpnext_enhancements.api.training")
	api_training._learner_profile = lambda user: _Dict(name=user)
	api_training._visible_course_names = lambda user, profile: set(STATE["visible_courses"])
	sys.modules["erpnext_enhancements.api.training"] = api_training

	notifications = types.ModuleType("erpnext_enhancements.training.notifications")
	notifications._enabled = lambda: False
	notifications._recipient = lambda user: None
	notifications._send = lambda *a, **k: False
	sys.modules["erpnext_enhancements.training.notifications"] = notifications


def setUpModule():
	global submissions, permissions, controller
	_install_stubs()
	_reset()
	from erpnext_enhancements.training import permissions as perm_module
	from erpnext_enhancements.training import submissions as sub_module
	from erpnext_enhancements.training.doctype.training_submission import training_submission as ctrl

	submissions = sub_module
	permissions = perm_module
	controller = ctrl


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()
		sys.modules["frappe"].session.user = "learner@example.com"


# ------------------------------------------------------------------- submit_work


class TestSubmitWork(_Base):
	def _call(self, course="TRN-CRS-1", lesson_key="lkey1", file="/private/files/work.pdf", text=None):
		return submissions.submit_work(course, lesson_key, file=file, text=text)

	def test_an_invisible_course_is_refused(self):
		STATE["visible_courses"] = set()
		with self.assertRaises(Exception):
			self._call()
		self.assertEqual(STATE["inserted"], [])

	def test_a_lesson_that_does_not_ask_for_work_is_refused(self):
		STATE["lesson"] = {"name": "TRN-LSN-1", "lesson_title": "x", "requires_submission": 0}
		with self.assertRaises(Exception):
			self._call()
		self.assertEqual(STATE["inserted"], [])

	def test_nothing_to_submit_is_refused(self):
		with self.assertRaises(Exception):
			self._call(file=None, text=None)
		self.assertEqual(STATE["inserted"], [])

	def test_a_valid_submission_is_created_as_the_learner(self):
		result = self._call()
		self.assertEqual(len(STATE["inserted"]), 1)
		doc = STATE["inserted"][0]
		self.assertEqual(doc["doctype"], "Training Submission")
		self.assertEqual(doc["user"], "learner@example.com")
		self.assertEqual(doc["course"], "TRN-CRS-1")
		self.assertEqual(doc["lesson"], "TRN-LSN-1")
		self.assertEqual(doc["status"], "Submitted")
		self.assertEqual(doc["submitted_on"], NOW)
		self.assertEqual(result["status"], "Submitted")

	def test_the_file_is_reparented_onto_the_submission_and_made_private(self):
		self._call()
		name = STATE["inserted"][0]["name"]
		file_sets = [s for s in STATE["set_values"] if s["doctype"] == "File"]
		by_field = {s["field"]: s["value"] for s in file_sets}
		self.assertEqual(by_field["attached_to_doctype"], "Training Submission")
		self.assertEqual(by_field["attached_to_name"], name)
		self.assertEqual(by_field["is_private"], 1)
		# And the submission records the file URL.
		sub_file = [
			s for s in STATE["set_values"]
			if s["doctype"] == "Training Submission" and s["field"] == "file"
		]
		self.assertEqual(sub_file[0]["value"], "/private/files/work.pdf")

	def test_a_file_the_learner_does_not_own_is_refused(self):
		STATE["file_row"] = {"name": "FILE-9", "owner": "someone.else@example.com", "is_private": 1}
		with self.assertRaises(Exception):
			self._call()

	def test_a_note_only_submission_is_allowed(self):
		result = self._call(file=None, text="Left the file with the trainer on site.")
		self.assertEqual(result["status"], "Submitted")
		self.assertEqual(STATE["inserted"][0]["submission_text"], "Left the file with the trainer on site.")


# --------------------------------------------------------------- grade_submission


class TestGradeSubmission(_Base):
	def _submission(self, **over):
		row = {
			"name": "TRN-SUB-0001",
			"doctype": "Training Submission",
			"user": "learner@example.com",
			"lesson": "TRN-LSN-1",
			"status": "Submitted",
			"feedback": None,
			"grade": None,
			"graded_by": None,
			"graded_on": None,
		}
		row.update(over)
		STATE["submission"] = row

	def _call(self, status="Passed", feedback=None, grade=None):
		return submissions.grade_submission("TRN-SUB-0001", status, feedback=feedback, grade=grade)

	def test_a_learner_cannot_grade(self):
		self._submission()
		STATE["roles"] = ["Training Learner"]
		with self.assertRaises(Exception):
			self._call()
		self.assertEqual(STATE["saved"], [])

	def test_an_unknown_status_is_refused(self):
		self._submission()
		STATE["roles"] = ["Training Manager"]
		with self.assertRaises(Exception):
			self._call(status="Submitted")

	def test_needs_rework_requires_feedback(self):
		self._submission()
		STATE["roles"] = ["Training Manager"]
		with self.assertRaises(Exception):
			self._call(status="Needs Rework", feedback="   ")
		self.assertEqual(STATE["saved"], [])

	def test_passed_stamps_the_grader_and_time(self):
		self._submission()
		STATE["roles"] = ["Training Manager"]
		sys.modules["frappe"].session.user = "manager@example.com"
		result = self._call(status="Passed", grade="8/10")
		doc = STATE["saved"][0]
		self.assertEqual(doc["status"], "Passed")
		self.assertEqual(doc["grade"], "8/10")
		self.assertEqual(doc["graded_by"], "manager@example.com")
		self.assertEqual(doc["graded_on"], NOW)
		self.assertEqual(result["status"], "Passed")

	def test_under_review_claims_without_stamping_a_verdict_time(self):
		self._submission()
		STATE["roles"] = ["Training Manager"]
		sys.modules["frappe"].session.user = "manager@example.com"
		self._call(status="Under Review")
		doc = STATE["saved"][0]
		self.assertEqual(doc["status"], "Under Review")
		self.assertIsNone(doc["graded_on"])
		self.assertIsNone(doc["graded_by"])


# ------------------------------------------------------------ get_submission_queue


class TestSubmissionQueue(_Base):
	def test_a_non_manager_gets_an_empty_queue(self):
		STATE["roles"] = ["Training Learner"]
		self.assertEqual(submissions.get_submission_queue(), [])

	def test_a_manager_gets_the_pending_rows_with_titles(self):
		STATE["roles"] = ["Training Manager"]
		STATE["queue_rows"] = [
			{
				"name": "TRN-SUB-0001",
				"course": "TRN-CRS-1",
				"lesson": "TRN-LSN-1",
				"user": "learner@example.com",
				"status": "Submitted",
				"grade": None,
				"file": "/private/files/work.pdf",
				"submission_text": None,
				"submitted_on": NOW,
			}
		]
		rows = submissions.get_submission_queue()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["course_title"], "Basin care")
		self.assertEqual(rows[0]["lesson_title"], "Draining a basin")


# --------------------------------------------------------------------- scoping


class TestRowScoping(_Base):
	def test_an_unscoped_role_sees_everything(self):
		STATE["roles"] = ["Training Manager"]
		self.assertEqual(permissions.submission_query_conditions("manager@example.com"), "")

	def test_a_learner_is_scoped_to_their_own_rows(self):
		STATE["roles"] = ["Training Learner"]
		cond = permissions.submission_query_conditions("learner@example.com")
		self.assertIn("`tabTraining Submission`.`user`", cond)
		self.assertIn("learner@example.com", cond)

	def test_has_permission_allows_own_row_and_refuses_another(self):
		STATE["roles"] = ["Training Learner"]
		mine = _Dict(user="learner@example.com")
		theirs = _Dict(user="someone.else@example.com")
		self.assertTrue(permissions.submission_has_permission(mine, "read", "learner@example.com"))
		self.assertFalse(permissions.submission_has_permission(theirs, "read", "learner@example.com"))


# ----------------------------------------------------------- doctype + surface


class TestDoctypeAndSurface(unittest.TestCase):
	def test_status_options_and_module(self):
		spec = json.loads(
			(APP / "training/doctype/training_submission/training_submission.json").read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in spec["fields"]}
		self.assertEqual(fields["status"]["options"], "Submitted\nUnder Review\nPassed\nNeeds Rework")
		# The learner column MUST be named `user` for row scoping to bind.
		self.assertEqual(fields["user"]["fieldname"], "user")
		self.assertEqual(spec["module"], "Training")

	def test_the_lesson_carries_the_requires_submission_flag(self):
		spec = json.loads(
			(APP / "training/doctype/training_lesson/training_lesson.json").read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in spec["fields"]}
		self.assertIn("requires_submission", fields)
		self.assertEqual(fields["requires_submission"]["fieldtype"], "Check")

	def test_the_bootstrap_sends_submissions_scoped_to_the_learner(self):
		api = (APP / "api/training.py").read_text(encoding="utf-8")
		self.assertIn('"submissions": _learner_submissions(user)', api)
		start = api.index("def _learner_submissions")
		body = api[start : api.index("\ndef ", start + 1)]
		self.assertIn('"user": user', body)

	def test_the_player_reads_submissions(self):
		player = (APP / "public/js/training/player.js").read_text(encoding="utf-8")
		self.assertIn("b.submissions", player)

	def test_the_scoping_is_registered_in_both_registers(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn(
			'"Training Submission": "erpnext_enhancements.training.permissions.submission_query_conditions"',
			hooks,
		)
		self.assertIn(
			'"Training Submission": "erpnext_enhancements.training.permissions.submission_has_permission"',
			hooks,
		)

	def test_the_desk_grade_button_is_wired_in_public_js(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('"Training Submission": ["public/js/training/training_submission.js"]', hooks)


if __name__ == "__main__":
	unittest.main()
