"""Bench-free unit tests for the Training publish step.

Stubs a minimal ``frappe`` (no site/bench) so ``api.training_author`` runs under
plain unittest. The stub is installed in ``setUpModule`` (execution time), not at
import, so it never fools the bench-only suites' ``import frappe`` skip-guards.

What is guarded here is the one thing that cannot be allowed to regress quietly:

  * **the answer key never appears in a learner-facing payload** — asserted by
    walking the serialized public payload to any depth and looking for the
    correct-answer markers, rather than by checking the handful of keys the
    current implementation happens to emit. A future field that leaks would be
    caught; an assertion listing today's keys would not;
  * **checkpoint timestamps are not shipped either** — the public payload carries
    a per-block *count*, never a list of ``at_seconds``, because a list of
    timestamps is a map of exactly where to skip to;
  * the answer key is nonetheless complete enough to grade against — correct
    option keys and accepted short-answer text survive into it;
  * ``_clone_lessons`` preserves every stable key, which is the whole mechanism
    behind "a minor edit does not reset an in-flight learner".

Run: python -m unittest erpnext_enhancements.tests.test_training_publish
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

training_author = None

#: Tables the stub serves, reset by each test.
STATE = {}

# The strings that must never reach a browser. Deliberately includes the field
# names as well as the sentinel values, so a payload that leaked a whole child
# row would trip this too.
FORBIDDEN_MARKERS = ("is_correct", "correct_text_answers", "__SECRET_ANSWER__")


def _reset_state():
	STATE.clear()
	STATE.update({"Training Checkpoint": [], "Training Answer Option": [], "docs": {}})


# ----------------------------------------------------------------- frappe stub


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


def _matches(row, filters):
	for field, expected in (filters or {}).items():
		actual = row.get(field)
		if isinstance(expected, (list, tuple)) and len(expected) == 2:
			op, value = expected
			if op == "in" and actual not in value:
				return False
			if op == "!=" and actual == value:
				return False
			if op == "is" and value == "not set" and not actual:
				return False
		elif actual != expected:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None, **kwargs):
	rows = [r for r in STATE.get(doctype, []) if _matches(r, filters)]
	if pluck:
		return [r.get(pluck) for r in rows]
	if fields and fields != ["*"]:
		return [_Dict({f: r.get(f) for f in fields}) for r in rows]
	return [_Dict(r) for r in rows]


def _get_doc(doctype, name=None):
	return STATE["docs"][name if name else doctype]


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.get_cached_doc = _get_doc
	frappe.generate_hash = lambda length=10: "h" * length
	frappe.get_roles = lambda user=None: ["System Manager"]
	frappe.session = _Dict(user="tester@example.com")
	frappe.flags = _Dict()
	frappe.parse_json = json.loads
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.copy_doc = lambda doc, **k: _Dict(doc)
	frappe.new_doc = lambda doctype: _Dict(doctype=doctype)
	frappe.enqueue = lambda *a, **k: None

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.msgprint = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""

	db = types.SimpleNamespace(
		exists=lambda *a, **k: None,
		get_value=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		get_single_value=lambda *a, **k: None,
		sql=lambda *a, **k: [],
		commit=lambda: None,
		escape=lambda v: f"'{v}'",
		has_column=lambda *a, **k: True,
	)
	frappe.db = db

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v: float(v or 0)
	utils.today = lambda: "2026-08-01"
	utils.nowdate = utils.today
	utils.now_datetime = lambda: "2026-08-01 12:00:00"
	utils.add_days = lambda d, n: d
	utils.formatdate = lambda d, *a, **k: str(d)
	utils.escape_html = lambda s: s
	# The real sanitizer strips scripts; identity is enough here because these
	# tests are about what fields are present, not about HTML cleaning.
	utils.sanitize_html = lambda s: s or ""
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document

	frappe._ = lambda s: s
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

	# api.training_author imports the two change-type constants from the version
	# controller, which would otherwise pull in the whole doctype package.
	pkg = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_course_version.training_course_version"
	)
	pkg.MINOR_EDIT = "Minor Edit (keep completions)"
	pkg.MATERIAL_CHANGE = "Material Change (require retake)"
	sys.modules[
		"erpnext_enhancements.training.doctype.training_course_version.training_course_version"
	] = pkg

	# `from frappe import _` needs the name on the module object.
	frappe.__dict__["_"] = lambda s: s


def setUpModule():
	global training_author
	_install_frappe_stub()
	_reset_state()
	from erpnext_enhancements.api import training_author as module

	training_author = module


# ------------------------------------------------------------------- fixtures


def _option(key, text, correct=0):
	return _Dict(
		option_key=key,
		option_text=text,
		is_correct=correct,
		explanation="__SECRET_ANSWER__" if correct else "",
	)


def _lesson_with_quiz_and_checkpoint():
	"""A lesson carrying one choice question, one short answer and one checkpoint."""
	question = _Dict(
		name="TRN-Q-000001",
		question_type="Single Choice",
		question_text="Which valve isolates the basin?",
		points=1,
		explanation="The isolation valve.",
		correct_text_answers="",
		options=[
			_option("optA", "The isolation valve", correct=1),
			_option("optB", "The bleed valve"),
		],
	)
	short = _Dict(
		name="TRN-Q-000002",
		question_type="Short Answer",
		question_text="Name the required PPE.",
		points=2,
		explanation="",
		correct_text_answers="Gloves\nGoggles",
		options=[],
	)
	STATE["docs"]["TRN-Q-000001"] = question
	STATE["docs"]["TRN-Q-000002"] = short

	STATE["Training Checkpoint"] = [
		_Dict(
			name="CP1",
			lesson="TRN-LSN-000001",
			checkpoint_key="cpkey1",
			block_key="blk1",
			at_seconds=252,
			question_text="What comes next?",
			question_type="Single Choice",
			explanation="Because of the pressure drop.",
			pause_video=1,
			allow_skip=0,
			max_attempts=2,
			rewind_seconds_on_wrong=15,
			counts_toward_score=1,
		)
	]
	STATE["Training Answer Option"] = [
		_Dict(
			parent="CP1",
			parenttype="Training Checkpoint",
			option_key="cpA",
			option_text="Close the valve",
			is_correct=1,
			explanation="__SECRET_ANSWER__",
		),
		_Dict(
			parent="CP1",
			parenttype="Training Checkpoint",
			option_key="cpB",
			option_text="Open the drain",
			is_correct=0,
			explanation="",
		),
	]

	return _Dict(
		name="TRN-LSN-000001",
		lesson_key="lkey1",
		lesson_title="Draining a basin",
		summary="How to drain safely.",
		estimated_minutes=12,
		allow_questions=1,
		has_quiz=1,
		quiz_questions_to_ask=0,
		quiz_pass_score=80,
		quiz_shuffle_questions=1,
		quiz_shuffle_options=1,
		blocks=[
			_Dict(
				block_key="blk1",
				block_type="Video",
				heading="Watch first",
				content="",
				image="",
				file="",
				video_asset="TRN-VID-00001",
				video_duration_seconds=600,
				embed_url="",
				poster_image="",
				caption="",
				required_for_completion=1,
				min_coverage_percent=80,
				checkpoints_enabled=1,
			)
		],
		quiz_questions=[
			_Dict(question="TRN-Q-000001", points=1, is_required=1),
			_Dict(question="TRN-Q-000002", points=2, is_required=1),
		],
	)


def _walk_strings(value):
	"""Every string anywhere in a nested structure, keys included."""
	if isinstance(value, dict):
		for key, item in value.items():
			yield str(key)
			yield from _walk_strings(item)
	elif isinstance(value, (list, tuple)):
		for item in value:
			yield from _walk_strings(item)
	else:
		yield str(value)


# ---------------------------------------------------------------------- tests


class TestPublicPayloadNeverLeaksAnswers(unittest.TestCase):
	def setUp(self):
		_reset_state()
		self.lesson = _lesson_with_quiz_and_checkpoint()
		self.public, self.key = training_author._split_lesson(self.lesson)

	def test_no_answer_marker_at_any_depth(self):
		"""Walk the whole public payload rather than checking known keys.

		A test that asserted "these five keys are absent" would pass forever while
		a newly added sixth field leaked the key.
		"""
		haystack = list(_walk_strings(self.public))
		for marker in FORBIDDEN_MARKERS:
			self.assertNotIn(
				marker,
				haystack,
				f"{marker!r} reached the learner-facing payload",
			)

	def test_no_answer_marker_in_serialized_form(self):
		"""Belt and braces: the payload is shipped as JSON, so check the JSON."""
		blob = json.dumps(self.public)
		for marker in FORBIDDEN_MARKERS:
			self.assertNotIn(marker, blob)

	def test_options_are_offered_without_which_is_right(self):
		question = self.public["quiz"]["questions"][0]
		self.assertEqual(len(question["options"]), 2)
		for option in question["options"]:
			self.assertIn("option_key", option)
			self.assertIn("text", option)
			self.assertNotIn("is_correct", option)

	def test_short_answer_accepted_text_is_not_offered(self):
		question = self.public["quiz"]["questions"][1]
		self.assertNotIn("accepted_text", question)
		self.assertNotIn("correct_text_answers", question)

	def test_checkpoint_timestamps_are_not_shipped(self):
		"""Only a per-block count reaches the client.

		A list of ``at_seconds`` is a map of exactly where to skip past, so the
		runtime hands out the next one at a time instead.
		"""
		self.assertEqual(self.public["checkpoint_count"], {"blk1": 1})
		self.assertNotIn("252", json.dumps(self.public))


class TestAnswerKeyIsUsable(unittest.TestCase):
	def setUp(self):
		_reset_state()
		self.lesson = _lesson_with_quiz_and_checkpoint()
		self.public, self.key = training_author._split_lesson(self.lesson)

	def test_choice_answer_is_recorded_by_option_key(self):
		self.assertEqual(self.key["quiz"]["TRN-Q-000001"]["correct"], ["optA"])

	def test_short_answer_is_normalised_to_lowercase_lines(self):
		self.assertEqual(
			self.key["quiz"]["TRN-Q-000002"]["accepted_text"], ["gloves", "goggles"]
		)

	def test_question_points_prefer_the_lesson_row_over_the_question(self):
		"""The pool row's weighting wins, so the same bank question can be worth
		more in one lesson than another."""
		self.assertEqual(self.key["quiz"]["TRN-Q-000002"]["points"], 2)

	def test_checkpoint_key_carries_timestamp_and_answer(self):
		checkpoint = self.key["checkpoints"]["cpkey1"]
		self.assertEqual(checkpoint["at"], 252)
		self.assertEqual(checkpoint["correct"], ["cpA"])
		self.assertEqual(checkpoint["block_key"], "blk1")


if __name__ == "__main__":
	unittest.main()
