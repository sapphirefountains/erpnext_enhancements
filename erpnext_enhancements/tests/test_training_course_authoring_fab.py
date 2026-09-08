"""Bench-free tests for "Create a course with Triton" (the floating trident).

Two halves:

  * the drafting endpoint's *gates* and its *JSON extraction* -- the parts that run
    before, and independently of, the Vertex call and the deterministic
    materialiser (both of which need a bench). The endpoint must refuse a
    non-author, refuse when AI is switched off, refuse an empty or oversized brief,
    and reduce a model reply -- fenced, prose-wrapped, or clean -- to the JSON
    object it should have been (or throw, creating nothing);
  * source-surface guards that the trident is actually wired on BOTH SPAs and that
    its endpoint is reachable -- a floating button nobody can click, or one wired to
    a method that is not whitelisted or not on the player's transport, is exactly
    the silent break these guard against.

Run: python -m unittest erpnext_enhancements.tests.test_training_course_authoring_fab
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
training_ai = None

STATE = {}


def _reset():
	STATE.clear()
	STATE.update({"roles": ["Training Author"], "ai_enabled": True})


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.session = _Dict(user="author@example.com")
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.log_error = lambda *a, **k: None

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.get_traceback = lambda: ""
	frappe.__dict__["_"] = lambda s, *a, **k: s

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# The module-scope imports training_ai makes, stubbed so it loads without a bench.
	cp = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_checkpoint.training_checkpoint"
	)
	cp.MIN_GAP_SECONDS = 20
	cp.MIN_TAIL_SECONDS = 10
	sys.modules[
		"erpnext_enhancements.training.doctype.training_checkpoint.training_checkpoint"
	] = cp

	q = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_question.training_question"
	)
	q.MAX_OPTIONS = 6
	q.MIN_OPTIONS = 2
	sys.modules[
		"erpnext_enhancements.training.doctype.training_question.training_question"
	] = q

	ts = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	)
	ts.is_enabled = lambda flag: STATE["ai_enabled"]
	sys.modules[
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	] = ts


def setUpModule():
	global training_ai
	_install_stubs()
	_reset()
	from erpnext_enhancements.api import training_ai as ta

	training_ai = ta


class _Base(unittest.TestCase):
	def setUp(self):
		_reset()


class TestJsonExtraction(_Base):
	def test_clean_object(self):
		self.assertEqual(training_ai._parse_course_spec_json('{"a": 1}'), {"a": 1})

	def test_code_fence_is_stripped(self):
		self.assertEqual(
			training_ai._parse_course_spec_json('```json\n{"a": 1}\n```'), {"a": 1}
		)

	def test_leading_prose_is_dropped(self):
		self.assertEqual(
			training_ai._parse_course_spec_json('Sure! Here is the course:\n{"a": 1}\nHope that helps.'),
			{"a": 1},
		)

	def test_garbage_throws_and_returns_nothing(self):
		with self.assertRaises(Exception):
			training_ai._parse_course_spec_json("not json at all")

	def test_a_json_array_is_refused(self):
		# The schema's root is an object; a top-level array is not a course.
		with self.assertRaises(Exception):
			training_ai._parse_course_spec_json("[1, 2, 3]")


class TestGates(_Base):
	def test_a_non_author_is_refused(self):
		STATE["roles"] = ["Training Learner"]
		with self.assertRaises(Exception):
			training_ai.draft_course_with_triton("Teach pump isolation.")

	def test_ai_switched_off_is_refused(self):
		STATE["ai_enabled"] = False
		with self.assertRaises(Exception):
			training_ai.draft_course_with_triton("Teach pump isolation.")

	def test_an_empty_brief_is_refused(self):
		with self.assertRaises(Exception):
			training_ai.draft_course_with_triton("   ")

	def test_an_oversized_brief_is_refused(self):
		with self.assertRaises(Exception):
			training_ai.draft_course_with_triton("x" * (training_ai.MAX_BRIEF_CHARS + 1))


class TestSystemInstruction(_Base):
	def test_it_names_the_media_rule_and_json_only(self):
		from erpnext_enhancements.training import course_spec

		instruction = training_ai._course_author_system_instruction(course_spec)
		self.assertIn("JSON only", instruction)
		# The load-bearing constraint: a model cannot supply media, so it must not
		# invent media blocks.
		self.assertIn("Never invent a Video", instruction)
		# The schema itself is in the prompt (spot-check a required key).
		self.assertIn("course_title", instruction)


class TestSurfaceWiring(unittest.TestCase):
	def test_the_endpoint_is_whitelisted_and_post(self):
		src = (APP / "api/training_ai.py").read_text(encoding="utf-8")
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef draft_course_with_triton', src)

	def test_the_player_transport_wrapper_exists_and_delegates(self):
		src = (APP / "api/training.py").read_text(encoding="utf-8")
		self.assertIn("def draft_course(brief)", src)
		self.assertIn("training_ai.draft_course_with_triton", src)

	def test_the_bootstrap_sends_can_author(self):
		src = (APP / "api/training.py").read_text(encoding="utf-8")
		self.assertIn('"can_author": bool(frappe.has_permission("Training Course", "create"))', src)

	def test_the_method_map_dials_draft_course(self):
		src = (APP / "www/training.html").read_text(encoding="utf-8")
		self.assertIn('draftCourse: "draft_course"', src)

	def test_the_learner_player_mounts_the_trident_for_authors(self):
		src = (APP / "public/js/training/player.js").read_text(encoding="utf-8")
		self.assertIn("function tritonFab()", src)
		self.assertIn("if (b.can_author) rootEl.appendChild(tritonFab())", src)
		self.assertIn('call("draftCourse"', src)

	def test_the_builder_mounts_the_trident(self):
		src = (APP / "training/page/training_builder/training_builder.js").read_text(encoding="utf-8")
		self.assertIn("build_triton_fab()", src)
		self.assertIn("erpnext_enhancements.api.training_ai.draft_course_with_triton", src)


if __name__ == "__main__":
	unittest.main()
