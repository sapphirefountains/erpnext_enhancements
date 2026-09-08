"""Bench-free unit tests for AI-authored trainings.

Two things are guarded, both without a bench:

  * **The Course Spec validator is strict** (``training.course_spec``). A spec that
    validates here must survive ``insert()`` later — so the option bounds, the
    choice-type rules, the authorable-block-types restriction and the count caps are
    all enforced up front, with a message that names the lesson and block. A model or
    a hand editor cannot slip a Video block, a seven-option question, or a
    True-False/Yes-No past it.
  * **The materializer maps a spec onto the *existing* builder engine and nothing
    else** (``api.training_course_authoring``). It is stubbed against a recording
    ``training_author`` so the test can assert the exact ``save_draft_version``
    payloads: chapters minted first, lessons+blocks next (interactive blocks carried
    as their ``data`` JSON), and quizzes last — every question created
    ``ai_generated`` with **no** ``ai_reviewed_by``, so publication stays gated on a
    human, and the course left as a Draft.

The stub is installed in ``setUpModule`` (execution time), not at import, so it
never fools the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_training_course_authoring
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

course_spec = None
authoring = None

STATE = {}

# Mirrors TrainingQuestion's real bounds (2..6). The module under test imports the
# real constants; this stub only frees the test from importing the controller.
MIN_OPTIONS = 2
MAX_OPTIONS = 6


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"inserted": [],
			"roles": ["Training Author"],
			"has_permission": True,
			"counter": 0,
			"hash_counter": 0,
			"modified_counter": 0,
			"create_draft_calls": [],
			"save_calls": [],
			"cache": {},
		}
	)


# ----------------------------------------------------------------- frappe stub


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
		prefix = {"Training Course": "TRN-CRS", "Training Question": "TRN-QN"}.get(
			self.get("doctype", ""), "NEW"
		)
		self["name"] = self.get("name") or f"{prefix}-{STATE['counter']:04d}"
		STATE["inserted"].append(self)
		return self


def _get_doc(doctype, name=None):
	if isinstance(doctype, dict):
		return _StubDoc(doctype)
	raise Exception(f"unexpected get_doc({doctype!r}, {name!r})")


def _db_get_value(doctype, name, fieldname, **kwargs):
	if doctype == "Training Course Version" and fieldname == "modified":
		return "m0"
	return None


def _db_exists(doctype, filters=None, **kwargs):
	# Training Category "Safety" exists; DocType "Training Course" exists; nothing else.
	if doctype == "DocType":
		return True
	if doctype == "Training Category":
		return filters == "Safety"
	return None


class _Cache:
	def set_value(self, key, value, expires_in_sec=None):
		STATE["cache"][key] = value

	def get_value(self, key):
		return STATE["cache"].get(key)

	def delete_value(self, key):
		STATE["cache"].pop(key, None)


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_doc = _get_doc
	frappe.get_roles = lambda user=None: STATE["roles"]
	frappe.session = _Dict(user="author@example.com")
	frappe.flags = _Dict()
	frappe.parse_json = json.loads
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.has_permission = lambda *a, **k: STATE["has_permission"]
	frappe.cache = lambda: _Cache()

	def _generate_hash(length=10):
		STATE["hash_counter"] += 1
		return f"h{STATE['hash_counter']:0{max(length - 1, 1)}d}"

	frappe.generate_hash = _generate_hash

	class _PermissionError(Exception):
		pass

	class _ValidationError(Exception):
		pass

	frappe.PermissionError = _PermissionError
	frappe.ValidationError = _ValidationError
	exceptions = types.ModuleType("frappe.exceptions")
	exceptions.ValidationError = _ValidationError
	exceptions.PermissionError = _PermissionError
	frappe.exceptions = exceptions

	def _throw(msg, exc=None):
		raise (exc or _ValidationError)(msg)

	frappe.throw = _throw
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: "traceback"
	frappe.db = types.SimpleNamespace(get_value=_db_get_value, exists=_db_exists)
	frappe.__dict__["_"] = lambda s: s

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.strip_html = lambda s: s or ""
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.exceptions"] = exceptions
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

	# Training Question controller — just the bounds the spec imports.
	tq = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_question.training_question"
	)
	tq.MIN_OPTIONS = MIN_OPTIONS
	tq.MAX_OPTIONS = MAX_OPTIONS
	sys.modules[tq.__name__] = tq

	# training_author — a recording stub. create_draft_version mints a draft;
	# save_draft_version records its payload and hands back names for temp_ids and
	# keys for chapters, exactly as the real one does.
	ta = types.ModuleType("erpnext_enhancements.api.training_author")

	def _create_draft_version(course, change_type=None):
		STATE["create_draft_calls"].append(course)
		return "TRN-CV-DRAFT"

	def _save_draft_version(version, payload, modified=None):
		STATE["save_calls"].append({"version": version, "payload": payload, "modified": modified})
		created = [
			{"temp_id": lsn["temp_id"], "name": f"LSN-{lsn['temp_id']}"}
			for lsn in payload.get("lessons", [])
			if lsn.get("temp_id")
		]
		chapters = [
			{"chapter_key": f"chk{i}", "chapter_title": ch.get("chapter_title"), "idx": i + 1}
			for i, ch in enumerate(payload.get("chapters", []))
		]
		STATE["modified_counter"] += 1
		return {
			"modified": f"m{STATE['modified_counter']}",
			"saved": [],
			"created_lessons": created,
			"deleted": [],
			"chapters": chapters,
			"rejected": [],
		}

	ta.create_draft_version = _create_draft_version
	ta.save_draft_version = _save_draft_version
	sys.modules["erpnext_enhancements.api.training_author"] = ta


def setUpModule():
	global course_spec, authoring
	_install_stubs()
	_reset_state()
	from erpnext_enhancements.api import training_course_authoring as auth_module
	from erpnext_enhancements.training import course_spec as spec_module

	course_spec = spec_module
	authoring = auth_module


# ------------------------------------------------------------------- fixtures


def _question(stem="Which valve is closed first?", qtype="Single Choice", correct=("A",), opts=("A", "B")):
	return {
		"question": stem,
		"type": qtype,
		"explanation": "Because it stops the backflow.",
		"options": [{"text": o, "is_correct": o in correct} for o in opts],
	}


def _lesson(title="Isolating the pump", with_quiz=True, chapter=None, blocks=None):
	lesson = {
		"lesson_title": title,
		"summary": "Why the isolation valve comes first.",
		"estimated_minutes": 6,
		"blocks": blocks
		or [
			{"block_type": "Rich Text", "heading": "Intro", "content": "<p>Close the valve first.</p>"},
			{"block_type": "Callout", "content": "Do not skip this.", "callout_tone": "Warning"},
			{"block_type": "Checklist", "items": ["Close valve", "Open strainer"]},
		],
	}
	if chapter is not None:
		lesson["chapter"] = chapter
	if with_quiz:
		lesson["quiz"] = {"pass_score": 80, "questions_to_ask": 0, "questions": [_question()]}
	return lesson


def _spec(**overrides):
	spec = {
		"course": {"course_title": "Pump Isolation Basics", "summary": "Isolate a pump safely."},
		"lessons": [_lesson()],
	}
	spec.update(overrides)
	return spec


class _Base(unittest.TestCase):
	def setUp(self):
		_reset_state()


# ============================================================ spec validation


class TestCourseSpecValidation(_Base):
	def test_a_good_spec_normalises(self):
		spec = course_spec.validate_course_spec(_spec())
		self.assertEqual(spec["course"]["course_title"], "Pump Isolation Basics")
		self.assertEqual(spec["course"]["weight"], "Optional")  # default
		self.assertEqual(spec["course"]["audience"], "Internal Staff")  # default
		self.assertEqual(len(spec["lessons"]), 1)

	def test_a_json_string_is_parsed(self):
		spec = course_spec.validate_course_spec(json.dumps(_spec()))
		self.assertEqual(len(spec["lessons"]), 1)

	def test_missing_course_title_is_refused(self):
		with self.assertRaises(Exception):
			course_spec.validate_course_spec({"course": {}, "lessons": [_lesson()]})

	def test_no_lessons_is_refused(self):
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(_spec(lessons=[]))

	def test_a_media_block_type_is_refused(self):
		"""A model cannot supply an image or a video, so those block types are not
		authorable — a blank-rendering block is worse than none."""
		for bad in ("Video", "Image", "PDF", "Image Hotspots", "External Embed"):
			with self.assertRaises(Exception):
				course_spec.validate_course_spec(
					_spec(lessons=[_lesson(with_quiz=False, blocks=[{"block_type": bad}])])
				)

	def test_a_rich_text_block_needs_text(self):
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(lessons=[_lesson(with_quiz=False, blocks=[{"block_type": "Rich Text", "content": "  "}])])
			)

	def test_a_checklist_needs_items(self):
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(lessons=[_lesson(with_quiz=False, blocks=[{"block_type": "Checklist", "items": []}])])
			)

	def test_a_flashcards_block_keeps_only_complete_cards(self):
		spec = course_spec.validate_course_spec(
			_spec(
				lessons=[
					_lesson(
						with_quiz=False,
						blocks=[
							{
								"block_type": "Flashcards",
								"cards": [{"front": "Q", "back": "A"}, {"front": "", "back": "x"}],
							}
						],
					)
				]
			)
		)
		self.assertEqual(len(spec["lessons"][0]["blocks"][0]["cards"]), 1)

	def test_a_divider_needs_nothing(self):
		spec = course_spec.validate_course_spec(
			_spec(lessons=[_lesson(with_quiz=False, blocks=[{"block_type": "Divider"}])])
		)
		self.assertEqual(spec["lessons"][0]["blocks"][0]["block_type"], "Divider")

	def test_a_question_with_every_option_correct_is_refused(self):
		bad = _question(correct=("A", "B"), opts=("A", "B"))
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(lessons=[dict(_lesson(with_quiz=False), quiz={"questions": [bad]})])
			)

	def test_a_question_with_no_correct_option_is_refused(self):
		bad = _question(correct=(), opts=("A", "B"))
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(lessons=[dict(_lesson(with_quiz=False), quiz={"questions": [bad]})])
			)

	def test_single_choice_with_two_correct_is_refused(self):
		bad = _question(qtype="Single Choice", correct=("A", "B"), opts=("A", "B", "C"))
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(lessons=[dict(_lesson(with_quiz=False), quiz={"questions": [bad]})])
			)

	def test_true_false_must_be_true_and_false(self):
		bad = _question(qtype="True-False", correct=("Yes",), opts=("Yes", "No"))
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(lessons=[dict(_lesson(with_quiz=False), quiz={"questions": [bad]})])
			)
		good = _question(qtype="True-False", correct=("True",), opts=("True", "False"))
		spec = course_spec.validate_course_spec(
			_spec(lessons=[dict(_lesson(with_quiz=False), quiz={"questions": [good]})])
		)
		self.assertEqual(len(spec["lessons"][0]["quiz"]["questions"]), 1)

	def test_too_few_options_is_refused(self):
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(lessons=[dict(_lesson(with_quiz=False), quiz={"questions": [_question(opts=("A",), correct=("A",))]})])
			)

	def test_too_many_options_is_refused(self):
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(
					lessons=[
						dict(
							_lesson(with_quiz=False),
							quiz={"questions": [_question(opts=tuple("ABCDEFG"), correct=("A",))]},
						)
					]
				)
			)

	def test_a_chapter_index_out_of_range_is_refused(self):
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(
				_spec(chapters=[{"title": "One"}], lessons=[_lesson(with_quiz=False, chapter=3)])
			)

	def test_a_valid_chapter_index_is_kept(self):
		spec = course_spec.validate_course_spec(
			_spec(chapters=[{"title": "One"}, {"title": "Two"}], lessons=[_lesson(with_quiz=False, chapter=1)])
		)
		self.assertEqual(spec["lessons"][0]["chapter"], 1)

	def test_more_than_the_max_lessons_is_refused(self):
		many = [_lesson(title=f"L{n}", with_quiz=False) for n in range(course_spec.MAX_LESSONS + 1)]
		with self.assertRaises(Exception):
			course_spec.validate_course_spec(_spec(lessons=many))

	def test_weight_and_audience_round_trip(self):
		spec = course_spec.validate_course_spec(
			_spec(course={"course_title": "X", "weight": "Required", "audience": "Both"})
		)
		self.assertEqual(spec["course"]["weight"], "Required")
		self.assertEqual(spec["course"]["audience"], "Both")

	def test_the_bounds_are_imported_not_restated(self):
		"""Drift-safety: the spec must read MIN/MAX_OPTIONS from the controller that
		enforces them, exactly as api/training_ai.py does."""
		source = Path(course_spec.__file__).read_text(encoding="utf-8")
		self.assertIn("from erpnext_enhancements.training.doctype.training_question.training_question import", source)
		self.assertIn("MAX_OPTIONS", source)
		self.assertIn("MIN_OPTIONS", source)


# ============================================================ materializer


class TestMaterializer(_Base):
	def _build(self, **overrides):
		return authoring.author_course_from_spec(_spec(**overrides))

	def test_it_creates_the_course_as_the_author(self):
		result = self._build()
		course = next(d for d in STATE["inserted"] if d["doctype"] == "Training Course")
		self.assertEqual(course["course_title"], "Pump Isolation Basics")
		self.assertEqual(course["author"], "author@example.com")
		self.assertEqual(result["status"], "Draft")
		self.assertTrue(result["url"].startswith("/app/training-course/"))

	def test_a_non_author_is_refused(self):
		STATE["roles"] = ["Employee"]
		with self.assertRaises(Exception):
			self._build()

	def test_without_create_permission_it_is_refused(self):
		STATE["has_permission"] = False
		with self.assertRaises(Exception):
			self._build()

	def test_it_mints_one_draft_version(self):
		self._build()
		self.assertEqual(STATE["create_draft_calls"], ["TRN-CRS-0001"])

	def test_a_category_that_exists_is_set_and_an_unknown_one_dropped(self):
		self._build(course={"course_title": "X", "category": "Safety"})
		course = next(d for d in STATE["inserted"] if d["doctype"] == "Training Course")
		self.assertEqual(course.get("category"), "Safety")

		_reset_state()
		self._build(course={"course_title": "X", "category": "Nonexistent"})
		course = next(d for d in STATE["inserted"] if d["doctype"] == "Training Course")
		self.assertNotIn("category", course)

	def test_blocks_are_mapped_onto_the_builder_payload(self):
		self._build()
		lessons_call = STATE["save_calls"][0]  # no chapters -> lessons is first save
		patch = lessons_call["payload"]["lessons"][0]
		self.assertEqual(patch["temp_id"], "L0")
		types_ = [b["block_type"] for b in patch["blocks"]]
		self.assertEqual(types_, ["Rich Text", "Callout", "Checklist"])
		# Rich Text carries content; Callout carries tone; Checklist carries data JSON.
		self.assertIn("Close the valve", patch["blocks"][0]["content"])
		self.assertEqual(patch["blocks"][1]["callout_tone"], "Warning")
		self.assertEqual(json.loads(patch["blocks"][2]["data"]), {"items": ["Close valve", "Open strainer"]})

	def test_no_chapters_means_no_chapter_save_and_no_chapter_key(self):
		self._build()
		# Only lessons + quiz saves; the first save carries lessons, not chapters.
		self.assertNotIn("chapters", STATE["save_calls"][0]["payload"])
		self.assertNotIn("chapter_key", STATE["save_calls"][0]["payload"]["lessons"][0])

	def test_chapters_are_saved_first_and_lessons_reference_the_minted_key(self):
		_reset_state()
		authoring.author_course_from_spec(
			_spec(chapters=[{"title": "Basics"}], lessons=[_lesson(with_quiz=False, chapter=0)])
		)
		first = STATE["save_calls"][0]["payload"]
		self.assertIn("chapters", first)
		self.assertEqual(first["chapters"][0]["chapter_title"], "Basics")
		lessons_call = STATE["save_calls"][1]["payload"]
		self.assertEqual(lessons_call["lessons"][0]["chapter_key"], "chk0")

	def test_questions_are_created_flagged_and_unreviewed(self):
		self._build()
		questions = [d for d in STATE["inserted"] if d["doctype"] == "Training Question"]
		self.assertEqual(len(questions), 1)
		q = questions[0]
		self.assertEqual(q["ai_generated"], 1)
		# The review gate: generated, but NO reviewer. This is what blocks publish.
		self.assertNotIn("ai_reviewed_by", q)
		self.assertEqual(q["source_lesson"], "LSN-L0")
		self.assertEqual([o["is_correct"] for o in q["options"]], [1, 0])

	def test_the_quiz_pool_references_the_created_questions(self):
		self._build()
		quiz_call = STATE["save_calls"][-1]["payload"]  # quiz is the last save
		patch = quiz_call["lessons"][0]
		self.assertEqual(patch["name"], "LSN-L0")
		self.assertEqual(patch["has_quiz"], 1)
		self.assertEqual(patch["quiz_pass_score"], 80)
		question_name = next(d["name"] for d in STATE["inserted"] if d["doctype"] == "Training Question")
		self.assertEqual(patch["quiz"], [{"question": question_name}])

	def test_a_quizless_lesson_writes_no_questions_and_no_quiz_pass(self):
		_reset_state()
		authoring.author_course_from_spec(_spec(lessons=[_lesson(with_quiz=False)]))
		self.assertEqual([d for d in STATE["inserted"] if d["doctype"] == "Training Question"], [])
		# Only the lessons save; no third quiz pass.
		payloads = [c["payload"] for c in STATE["save_calls"]]
		self.assertFalse(any("has_quiz" in (p.get("lessons") or [{}])[0] for p in payloads if p.get("lessons")))

	def test_it_never_publishes(self):
		"""Seeding is not shipping — the materializer must not CALL publish_version
		(the word appears in the docstring explaining exactly that; a call carries a
		paren)."""
		source = Path(authoring.__file__).read_text(encoding="utf-8")
		self.assertNotIn("publish_version(", source)

	def test_the_result_counts_the_course(self):
		result = self._build()
		self.assertEqual(result["lessons"], 1)
		self.assertEqual(result["blocks"], 3)
		self.assertEqual(result["questions"], 1)


# ============================================================ spec stash


class TestSpecStash(_Base):
	def test_a_stashed_spec_round_trips_once(self):
		token = authoring.stash_spec({"course": {"course_title": "X"}})
		self.assertEqual(authoring.pop_spec(token)["course"]["course_title"], "X")
		# One-shot: a second pop is empty.
		self.assertIsNone(authoring.pop_spec(token))

	def test_an_unknown_token_is_none(self):
		self.assertIsNone(authoring.pop_spec("nope"))
		self.assertIsNone(authoring.pop_spec(""))


if __name__ == "__main__":
	unittest.main()
