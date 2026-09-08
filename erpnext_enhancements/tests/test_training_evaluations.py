"""Bench-free tests for scheduled evaluations (WI-071 Phase C).

The load-bearing property is that an evaluation's outcome is *the same attestation a
manual sign-off produces* — `record_evaluation` never re-implements the sign-off, it
creates a draft `Training Signoff` and hands it to `signoff.record_signoff`. So the
stub records what `record_evaluation` does with a stubbed sign-off engine and asserts:

  * **the learner can never record their own evaluation**, and neither can a stranger
    — only the evaluator (by their own login) or a Training Manager;
  * **recording feeds the sign-off engine**: it creates a draft `Training Signoff`
    (supervisor = the evaluator), calls `signoff.record_signoff` with the outcome, and
    marks the evaluation Completed with the sign-off linked;
  * a second recording is refused once the sign-off is submitted.

Plus the controller's evaluator-user derivation + self-evaluation refusal (executable),
and source/shape guards for the doctype and the learner surface.

Run: python -m unittest erpnext_enhancements.tests.test_training_evaluations
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
evaluations = None
controller = None

STATE = {}


def _reset():
	STATE.clear()
	STATE.update(
		{
			"user": "evaluator@example.com",
			"roles": ["Training Learner"],
			"eval": None,
			"signoff_docstatus": 0,
			"employee_user": None,
			"record_signoff_calls": [],
			"inserted": [],
			"set_values": [],
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
		self["name"] = self.get("name") or f"TRN-SGN-{STATE['counter']:04d}"
		STATE["inserted"].append(self)
		return self


def _eval_doc():
	return _StubDoc(STATE["eval"])


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _StubDoc(doctype)
	if doctype == "Training Evaluation":
		return _eval_doc()
	raise Exception(f"unexpected get_doc({doctype!r})")


def _db_get_value(doctype, name, fieldname, **kwargs):
	if doctype == "Training Course" and fieldname == "current_version":
		return "TRN-CV-1"
	if doctype == "Training Signoff" and fieldname == "docstatus":
		return STATE["signoff_docstatus"]
	if doctype == "Employee" and fieldname == "user_id":
		return STATE["employee_user"]
	return None


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_doc = _get_doc
	frappe.session = _Dict(user="evaluator@example.com")
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.flags = _Dict()
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.__dict__["_"] = lambda s, *a, **k: s

	def _set_value(doctype, name, field, value=None, **kwargs):
		# set_value takes either (…, fieldname, value) or (…, {fieldname: value, …}).
		if isinstance(field, dict):
			for key, val in field.items():
				STATE["set_values"].append({"doctype": doctype, "name": name, "field": key, "value": val})
		else:
			STATE["set_values"].append({"doctype": doctype, "name": name, "field": field, "value": value})

	frappe.db = types.SimpleNamespace(get_value=_db_get_value, set_value=_set_value, exists=lambda *a, **k: True)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.get_url = lambda p="": "https://erp.example.com" + p
	utils.escape_html = lambda s: s or ""
	utils.format_datetime = lambda d: str(d or "")
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

	# The sign-off engine, stubbed. record_evaluation must reuse this, not reinvent it.
	signoff = types.ModuleType("erpnext_enhancements.training.signoff")
	signoff.SIGNOFF_DOCTYPE = "Training Signoff"

	def _record_signoff(name, outcome, competency_notes=None, signature_image=None):
		STATE["record_signoff_calls"].append(
			{"signoff": name, "outcome": outcome, "notes": competency_notes}
		)
		return {"signoff": name, "outcome": outcome, "notified": True}

	signoff.record_signoff = _record_signoff
	sys.modules["erpnext_enhancements.training.signoff"] = signoff

	notifications = types.ModuleType("erpnext_enhancements.training.notifications")
	notifications._enabled = lambda: False
	notifications._recipient = lambda user: None
	notifications._send = lambda *a, **k: False
	sys.modules["erpnext_enhancements.training.notifications"] = notifications


def setUpModule():
	global evaluations, controller
	_install_stubs()
	_reset()
	from erpnext_enhancements.training import evaluations as ev_module
	from erpnext_enhancements.training.doctype.training_evaluation import training_evaluation as ctrl

	evaluations = ev_module
	controller = ctrl


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()


EVAL = {
	"name": "TRN-EVAL-00001",
	"course": "TRN-CRS-00001",
	"learner": "learner@example.com",
	"evaluator": "HR-EMP-0001",
	"evaluator_user": "evaluator@example.com",
	"signoff": None,
	"status": "Scheduled",
}


class TestRecordEvaluation(_Base):
	def _call(self, outcome="Competent", notes="Watched them drain the basin."):
		return evaluations.record_evaluation("TRN-EVAL-00001", outcome, notes)

	def test_the_learner_can_never_record_their_own(self):
		STATE["eval"] = dict(EVAL)
		STATE["user"] = "learner@example.com"
		frappe = sys.modules["frappe"]
		frappe.session.user = "learner@example.com"
		with self.assertRaises(Exception):
			self._call()
		self.assertEqual(STATE["record_signoff_calls"], [])

	def test_a_stranger_is_refused(self):
		STATE["eval"] = dict(EVAL)
		sys.modules["frappe"].session.user = "someone.else@example.com"
		STATE["roles"] = ["Training Learner"]
		with self.assertRaises(Exception):
			self._call()
		self.assertEqual(STATE["record_signoff_calls"], [])

	def test_the_evaluator_records_and_it_feeds_the_signoff(self):
		STATE["eval"] = dict(EVAL)
		sys.modules["frappe"].session.user = "evaluator@example.com"
		result = self._call(outcome="Competent")
		# A draft Training Signoff was created, supervisor = the evaluator's Employee.
		signoff_doc = next(d for d in STATE["inserted"] if d.get("doctype") == "Training Signoff")
		self.assertEqual(signoff_doc["supervisor"], "HR-EMP-0001")
		self.assertEqual(signoff_doc["user"], "learner@example.com")
		self.assertNotIn("supervisor_user", signoff_doc)  # re-derived by the controller
		# It was recorded through the existing engine, not reinvented.
		self.assertEqual(len(STATE["record_signoff_calls"]), 1)
		self.assertEqual(STATE["record_signoff_calls"][0]["outcome"], "Competent")
		# The evaluation was linked to the sign-off and marked Completed.
		fields = {s["field"] for s in STATE["set_values"]}
		self.assertEqual(fields, {"signoff", "status"})
		self.assertEqual(result["signoff"], signoff_doc["name"])

	def test_a_manager_may_record_even_if_not_the_evaluator(self):
		STATE["eval"] = dict(EVAL)
		sys.modules["frappe"].session.user = "manager@example.com"
		STATE["roles"] = ["Training Manager"]
		self._call()
		self.assertEqual(len(STATE["record_signoff_calls"]), 1)

	def test_a_second_recording_is_refused_once_submitted(self):
		STATE["eval"] = dict(EVAL, signoff="TRN-SGN-0001")
		STATE["signoff_docstatus"] = 1
		sys.modules["frappe"].session.user = "evaluator@example.com"
		with self.assertRaises(Exception):
			self._call()
		self.assertEqual(STATE["record_signoff_calls"], [])


class TestControllerDerivation(_Base):
	def _doc(self, evaluator="HR-EMP-0001", learner="learner@example.com"):
		doc = controller.TrainingEvaluation()
		doc.evaluator = evaluator
		doc.learner = learner
		doc.evaluator_user = None
		return doc

	def test_evaluator_user_is_derived_from_the_employee(self):
		STATE["employee_user"] = "evaluator@example.com"
		doc = self._doc()
		doc._resolve_evaluator_user()
		self.assertEqual(doc.evaluator_user, "evaluator@example.com")

	def test_a_self_evaluation_is_refused(self):
		STATE["employee_user"] = "learner@example.com"  # evaluator's user == the learner
		with self.assertRaises(Exception):
			self._doc(learner="learner@example.com")._resolve_evaluator_user()


class TestDoctypeAndSurface(unittest.TestCase):
	def test_status_options(self):
		spec = json.loads(
			(APP / "training/doctype/training_evaluation/training_evaluation.json").read_text(encoding="utf-8")
		)
		fields = {f["fieldname"]: f for f in spec["fields"]}
		self.assertEqual(fields["status"]["options"], "Scheduled\nCompleted\nCanceled\nNo Show")
		self.assertEqual(fields["evaluator"]["options"], "Employee")
		self.assertEqual(spec["module"], "Training")

	def test_the_bootstrap_sends_evaluations_scoped_to_the_learner(self):
		api = (APP / "api/training.py").read_text(encoding="utf-8")
		self.assertIn('"evaluations": _learner_evaluations(user)', api)
		start = api.index("def _learner_evaluations")
		body = api[start : api.index("\ndef ", start + 1)]
		self.assertIn('"learner": user', body)
		self.assertIn('"status": "Scheduled"', body)

	def test_the_player_reads_evaluations(self):
		player = (APP / "public/js/training/player.js").read_text(encoding="utf-8")
		self.assertIn("b.evaluations", player)


if __name__ == "__main__":
	unittest.main()
