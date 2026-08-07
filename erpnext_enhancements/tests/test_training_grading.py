"""Bench-free unit tests for Training grading — the module that reads the answer key.

Stubs a minimal ``frappe`` (no site/bench) so ``training.grading`` runs under plain
unittest. The stub is installed in ``setUpModule`` (execution time), not at import,
so it never fools the bench-only suites' ``import frappe`` skip-guards.

**The fixtures are published by the real publisher.** Every lesson here is built by
calling ``api.training_author._split_lesson`` and storing what it returns, rather
than by hand-writing two JSON blobs that look about right. Grading and publishing
are one contract written in two files, and a hand-written fixture would keep
passing on the day they drifted apart.

What is guarded:

  * **no correct answer ever leaves the server** — asserted by walking *every*
    learner-facing return value, to any depth, looking for the answer markers,
    rather than by checking the handful of keys the current implementation happens
    to emit. That style already caught a real leak in Phase 1; a known-keys
    assertion would not have;
  * **checkpoints are handed out one at a time and only once reached** — the
    runtime returns the next due one, never a list, because a list of timestamps
    is a map of exactly where to skip to;
  * **a checkpoint cannot be answered from a position that was never watched**;
  * **the quiz denominator is drawn by the server** — omitting the questions you
    do not know must not raise your score, and a client-supplied ``score`` is
    ignored entirely;
  * **the draw is deterministic per (attempt, lesson, run)** — a reshuffle on
    reload reads, from where the learner is sitting, as the app losing answers;
  * **the gates refuse to gate what they cannot measure** — an External Embed and
    an unverified duration are waived with a recorded reason, never silently
    passed and never silently failed;
  * **the refusal reasons carry the numbers**, because a learner told only "not
    complete" files a support ticket.

Run: python -m unittest erpnext_enhancements.tests.test_training_grading
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

grading = None
training_author = None

#: Tables the stub serves, reset by each test.
STATE = {}

# The strings that must never reach a browser. Deliberately includes the field
# names as well as the sentinel values, so a payload that leaked a whole child row
# or a whole answer-key entry would trip this too.
FORBIDDEN_MARKERS = (
	"is_correct",
	"correct_text_answers",
	"accepted_text",
	"__SECRET_ANSWER__",
	"__secret_text__",
)

ATTEMPT = "TRN-ATT-000001"
VERSION = "TRN-CRS-00001-V1"
COURSE = "TRN-CRS-00001"
LESSON = "TRN-LSN-000001"
LESSON_KEY = "lkey1"


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


# ----------------------------------------------------------------- frappe stub


def _matches(row, filters):
	for field, expected in (filters or {}).items():
		actual = row.get(field)
		if isinstance(expected, (list, tuple)) and len(expected) == 2:
			op, value = expected
			if op == "in" and actual not in value:
				return False
			if op == "!=" and actual == value:
				return False
		elif actual != expected:
			return False
	return True


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **kwargs):
	rows = [r for r in STATE.get(doctype, []) if _matches(r, filters)]
	if pluck:
		return [r.get(pluck) for r in rows]
	if fields and fields != ["*"]:
		return [_Dict({f: r.get(f) for f in fields}) for r in rows]
	return [_Dict(r) for r in rows]


def _get_value(doctype, filters=None, fieldname=None, as_dict=False, **kwargs):
	rows = STATE.get(doctype, [])
	if isinstance(filters, str):
		rows = [r for r in rows if r.get("name") == filters]
	else:
		rows = [r for r in rows if _matches(r, filters)]
	if not rows:
		return None
	row = rows[0]
	if isinstance(fieldname, (list, tuple)):
		picked = _Dict({f: row.get(f) for f in fieldname})
		return picked if as_dict else tuple(picked.values())
	return row.get(fieldname)


def _get_doc(doctype, name=None):
	return STATE["docs"][name if name else doctype]


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.get_cached_doc = _get_doc
	frappe.generate_hash = lambda length=10: "h" * length
	frappe.get_roles = lambda user=None: ["Training Learner"]
	frappe.session = _Dict(user="learner@example.com")
	frappe.flags = _Dict()
	frappe.parse_json = lambda v: json.loads(v) if isinstance(v, str) else v
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.copy_doc = lambda doc, **k: _Dict(doc)
	frappe.new_doc = lambda doctype: _Dict(doctype=doctype)
	frappe.enqueue = lambda *a, **k: None
	frappe.msgprint = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a[0] if a else "")
	frappe.get_traceback = lambda: ""

	class _PermissionError(Exception):
		pass

	frappe.PermissionError = _PermissionError

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw

	frappe.db = types.SimpleNamespace(
		get_value=_get_value,
		set_value=lambda *a, **k: None,
		exists=lambda *a, **k: None,
		get_single_value=lambda *a, **k: None,
		sql=lambda *a, **k: [],
		commit=lambda: None,
		escape=lambda v: f"'{v}'",
		has_column=lambda *a, **k: True,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, precision=None: (
		round(float(v or 0), precision) if precision is not None else float(v or 0)
	)
	utils.today = lambda: "2026-08-01"
	utils.nowdate = utils.today
	utils.now_datetime = lambda: "2026-08-01 12:00:00"
	utils.add_days = lambda d, n: d
	utils.getdate = lambda d=None: d
	utils.formatdate = lambda d, *a, **k: str(d)
	utils.escape_html = lambda s: s
	# The real sanitizer strips scripts; identity is enough here because these tests
	# are about which fields are present, not about HTML cleaning.
	utils.sanitize_html = lambda s: s or ""
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document

	frappe.__dict__["_"] = lambda s: s
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

	# api.training_author imports the two change-type constants from the version
	# controller, which would otherwise pull in the whole doctype package.
	version_module = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_course_version.training_course_version"
	)
	version_module.MINOR_EDIT = "Minor Edit (keep completions)"
	version_module.MATERIAL_CHANGE = "Material Change (require retake)"
	sys.modules[
		"erpnext_enhancements.training.doctype.training_course_version.training_course_version"
	] = version_module

	settings_module = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	)
	settings_module.get_settings = lambda: _Dict(STATE["settings"])
	sys.modules[
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	] = settings_module

	_install_progress_stub()


def _install_progress_stub():
	"""Stand in for ``training.progress``, which grading imports at call time.

	Deliberately a stub rather than the real store: what is under test here is the
	grading decision, and a real progress module would drag in the whole attempt
	runtime. ``coverage`` is implemented for real because the gates divide by it.
	"""
	progress = types.ModuleType("erpnext_enhancements.training.progress")

	def load(attempt_name):
		return STATE["progress"].setdefault(attempt_name, {})

	def save(attempt_name, data, force=False):
		STATE["progress"][attempt_name] = data
		STATE["saves"].append({"attempt": attempt_name, "force": bool(force)})

	def coverage(intervals, duration):
		if not duration or duration <= 0:
			return 0.0
		watched = sum(max(0, int(end) - int(start)) for start, end in intervals or [])
		return min(1.0, watched / float(duration))

	progress.load = load
	progress.save = save
	progress.coverage = coverage
	sys.modules["erpnext_enhancements.training.progress"] = progress


def setUpModule():
	global grading, training_author
	_install_frappe_stub()
	_reset_state()
	from erpnext_enhancements.api import training_author as author_module
	from erpnext_enhancements.training import grading as grading_module

	training_author = author_module
	grading = grading_module


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"docs": {},
			"errors": [],
			"saves": [],
			"progress": {},
			"settings": {"default_passing_score": 80, "default_min_video_coverage": 80},
			"Training Lesson": [],
			"Training Course": [],
			"Training Checkpoint": [],
			"Training Answer Option": [],
			"Training Video Asset": [],
		}
	)


# ------------------------------------------------------------------- fixtures


def _attempt(shuffle_seed="seed-abc"):
	return _Dict(name=ATTEMPT, course_version=VERSION, shuffle_seed=shuffle_seed)


def _option(key, text, correct=0):
	# Every option carries the sentinel explanation, right or wrong: a per-option
	# explanation says *why* an option is the answer, so revealing one is as good
	# as revealing is_correct.
	return _Dict(option_key=key, option_text=text, is_correct=correct, explanation="__SECRET_ANSWER__")


def _choice_question(name, text, correct_key="optA", qtype="Single Choice", points=1):
	question = _Dict(
		name=name,
		question_type=qtype,
		question_text=text,
		points=points,
		explanation="Isolating the basin stops the flow.",
		correct_text_answers="",
		options=[
			_option("optA", "The isolation valve", correct=1 if correct_key == "optA" else 0),
			_option("optB", "The bleed valve", correct=1 if correct_key == "optB" else 0),
			_option("optC", "The drain plug", correct=1 if correct_key == "optC" else 0),
		],
	)
	STATE["docs"][name] = question
	return question


def _multi_question(name):
	question = _Dict(
		name=name,
		question_type="Multiple Choice",
		question_text="Which two checks come first?",
		points=2,
		explanation="Both isolate the system.",
		correct_text_answers="",
		options=[
			_option("mA", "Isolate power", correct=1),
			_option("mB", "Isolate water", correct=1),
			_option("mC", "Open the drain"),
		],
	)
	STATE["docs"][name] = question
	return question


def _short_question(name):
	question = _Dict(
		name=name,
		question_type="Short Answer",
		question_text="Name the required PPE.",
		points=4,
		explanation="Goggles protect against chemical splash.",
		correct_text_answers="Safety Goggles\n__SECRET_TEXT__",
		options=[],
	)
	STATE["docs"][name] = question
	return question


def _block(
	key="blk1",
	block_type="Video",
	duration=600,
	asset="TRN-VID-00001",
	required=1,
	min_coverage=0,
	heading="Safety basics",
):
	return _Dict(
		block_key=key,
		block_type=block_type,
		heading=heading,
		content="",
		image="",
		file="",
		video_asset=asset,
		video_duration_seconds=duration,
		embed_url="https://example.invalid/embed" if block_type == "External Embed" else "",
		poster_image="",
		caption="",
		required_for_completion=required,
		min_coverage_percent=min_coverage,
		checkpoints_enabled=1 if block_type == "Video" else 0,
	)


def _checkpoint(key="cpkey1", block_key="blk1", at=252, allow_skip=0, max_attempts=2, rewind=15):
	STATE["Training Checkpoint"].append(
		_Dict(
			name=f"CP-{key}",
			lesson=LESSON,
			course_version=VERSION,
			checkpoint_key=key,
			block_key=block_key,
			at_seconds=at,
			question_text="What comes next?",
			question_type="Single Choice",
			explanation="Because the pressure drops.",
			pause_video=1,
			allow_skip=allow_skip,
			max_attempts=max_attempts,
			rewind_seconds_on_wrong=rewind,
			counts_toward_score=1,
		)
	)
	STATE["Training Answer Option"].extend(
		[
			_Dict(
				parent=f"CP-{key}",
				parenttype="Training Checkpoint",
				option_key="cpA",
				option_text="Close the valve",
				is_correct=1,
				explanation="__SECRET_ANSWER__",
			),
			_Dict(
				parent=f"CP-{key}",
				parenttype="Training Checkpoint",
				option_key="cpB",
				option_text="Open the drain",
				is_correct=0,
				explanation="__SECRET_ANSWER__",
			),
		]
	)


def _publish_lesson(
	blocks=None,
	questions=None,
	has_quiz=1,
	questions_to_ask=0,
	shuffle=1,
	pass_score=80,
):
	"""Build a lesson, run the *real* publisher over it, and store both payloads.

	Going through ``_split_lesson`` rather than hand-writing the JSON is the point:
	if the publisher's shapes ever move, these tests move with them.
	"""
	lesson = _Dict(
		name=LESSON,
		lesson_key=LESSON_KEY,
		lesson_title="Draining a basin",
		summary="How to drain safely.",
		estimated_minutes=12,
		allow_questions=1,
		has_quiz=has_quiz,
		quiz_questions_to_ask=questions_to_ask,
		quiz_pass_score=pass_score,
		quiz_shuffle_questions=shuffle,
		quiz_shuffle_options=shuffle,
		blocks=blocks if blocks is not None else [_block()],
		quiz_questions=[
			_Dict(question=q.name, points=q.points, is_required=1) for q in (questions or [])
		],
	)
	public, key = training_author._split_lesson(lesson)

	STATE["Training Lesson"] = [
		_Dict(
			name=LESSON,
			lesson_key=LESSON_KEY,
			course=COURSE,
			course_version=VERSION,
			quiz_pass_score=pass_score,
			published_content_json=json.dumps(public, separators=(",", ":")),
			answer_key_json=json.dumps(key, separators=(",", ":")),
		)
	]
	return public, key


def _course(min_coverage=80, passing_score=80, require_checkpoints=1):
	STATE["Training Course"] = [
		_Dict(
			name=COURSE,
			min_video_coverage=min_coverage,
			passing_score=passing_score,
			require_checkpoints_answered=require_checkpoints,
		)
	]


def _video_asset(name="TRN-VID-00001", source="Probed"):
	STATE["Training Video Asset"].append(_Dict(name=name, duration_source=source))


def _six_questions():
	return [
		_choice_question("TRN-Q-000001", "Which valve isolates the basin?"),
		_choice_question("TRN-Q-000002", "Which valve bleeds the line?", correct_key="optB"),
		_choice_question("TRN-Q-000003", "Which plug drains it?", correct_key="optC"),
		_choice_question("TRN-Q-000004", "Which valve is first?", correct_key="optA"),
		_multi_question("TRN-Q-000005"),
		_short_question("TRN-Q-000006"),
	]


def _set_progress(blocks=None, checkpoints=None, quiz=None):
	STATE["progress"][ATTEMPT] = {
		"lessons": {
			LESSON_KEY: {
				"status": "in_progress",
				"blocks": blocks or {},
				"checkpoints": checkpoints or {},
				"quiz": quiz or {},
			}
		}
	}


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


def _assert_answer_free(case, payload, what):
	haystack = list(_walk_strings(payload))
	for marker in FORBIDDEN_MARKERS:
		case.assertNotIn(marker, haystack, f"{marker!r} reached the learner via {what}")
	blob = json.dumps(payload, default=str)
	for marker in FORBIDDEN_MARKERS:
		case.assertNotIn(marker, blob, f"{marker!r} reached the learner via {what} (serialized)")


# ------------------------------------------------------- the leak test, first


class TestNothingLearnerFacingCarriesAnAnswer(unittest.TestCase):
	"""Walk every learner-facing return value, to any depth, for answer markers.

	Written as a walk rather than as "these keys are absent" on purpose. The
	known-keys version passes forever while a newly added seventh field leaks the
	key, and this module is the one place in the app that holds the answers.
	"""

	def setUp(self):
		_reset_state()
		_course()
		_video_asset()
		_checkpoint()
		self.public, self.key = _publish_lesson(questions=_six_questions())
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}},
			quiz={"runs": 0, "best": 0.0},
		)
		self.attempt = _attempt()

	def test_public_lesson_is_answer_free(self):
		_assert_answer_free(self, grading.public_lesson(LESSON), "public_lesson")

	def test_drawn_quiz_is_answer_free(self):
		for run in range(1, 4):
			_assert_answer_free(self, grading.draw_quiz(self.attempt, LESSON_KEY, run), "draw_quiz")

	def test_graded_quiz_is_answer_free_when_everything_is_wrong(self):
		"""The dangerous direction: a wrong answer is where an implementation is
		tempted to say what the right one was."""
		drawn = grading.draw_quiz(self.attempt, LESSON_KEY, 1)
		answers = {q["question"]: ["nonsense"] for q in drawn}
		_assert_answer_free(self, grading.grade_quiz(self.attempt, LESSON_KEY, answers), "grade_quiz")

	def test_graded_quiz_is_answer_free_when_everything_is_right(self):
		_assert_answer_free(
			self, grading.grade_quiz(self.attempt, LESSON_KEY, _perfect_answers(self.attempt, self.key)), "grade_quiz"
		)

	def test_graded_quiz_is_answer_free_when_nothing_was_answered(self):
		_assert_answer_free(self, grading.grade_quiz(self.attempt, LESSON_KEY, {}), "grade_quiz")

	def test_next_checkpoint_is_answer_free(self):
		_assert_answer_free(
			self, grading.next_checkpoint(self.attempt, LESSON_KEY, 300), "next_checkpoint"
		)

	def test_graded_checkpoint_is_answer_free_both_ways(self):
		wrong = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		_assert_answer_free(self, wrong, "grade_checkpoint (wrong)")
		right = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])
		_assert_answer_free(self, right, "grade_checkpoint (correct)")

	def test_gate_evaluation_is_answer_free(self):
		_assert_answer_free(self, grading.evaluate_gates(self.attempt, LESSON_KEY), "evaluate_gates")

	def test_the_markers_are_actually_present_in_the_key(self):
		"""Guards the guard. If the fixture stopped carrying the sentinels, every
		assertion above would pass while testing nothing at all."""
		blob = json.dumps(self.key)
		for marker in ("__SECRET_ANSWER__", "__secret_text__"):
			self.assertIn(marker, blob)


def _perfect_answers(attempt, key):
	"""Every drawn question answered correctly, read from the key (server side)."""
	answers = {}
	for question in grading.draw_quiz(attempt, LESSON_KEY, 1):
		entry = key["quiz"][question["question"]]
		if entry["type"] == "Short Answer":
			answers[question["question"]] = "  Safety   GOGGLES "
		else:
			answers[question["question"]] = list(entry["correct"])
	return answers


# ------------------------------------------------------------------- payloads


class TestPublishedPayloadAccess(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_course()
		_video_asset()
		self.public, self.key = _publish_lesson(questions=_six_questions())

	def test_unpublished_lesson_refuses_rather_than_returns_empty(self):
		STATE["Training Lesson"][0]["published_content_json"] = ""
		with self.assertRaises(Exception) as caught:
			grading.public_lesson(LESSON)
		self.assertIn("published", str(caught.exception).lower())

	def test_answer_key_is_complete_enough_to_grade(self):
		key = grading.answer_key(LESSON)
		self.assertEqual(key["quiz"]["TRN-Q-000002"]["correct"], ["optB"])
		self.assertIn("safety goggles", key["quiz"]["TRN-Q-000006"]["accepted_text"])

	def test_lesson_outside_this_version_is_refused(self):
		"""Lesson keys are stable across versions on purpose, so the lookup has to
		be scoped — otherwise a learner is graded on content they never saw."""
		attempt = _Dict(name=ATTEMPT, course_version="SOME-OTHER-V9", shuffle_seed="s")
		with self.assertRaises(Exception):
			grading.draw_quiz(attempt, LESSON_KEY, 1)


# ----------------------------------------------------------------- draw_quiz


class TestDrawQuiz(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_course()
		_video_asset()
		self.public, self.key = _publish_lesson(questions=_six_questions())
		self.attempt = _attempt()

	def _order(self, run, attempt=None):
		return [q["question"] for q in grading.draw_quiz(attempt or self.attempt, LESSON_KEY, run)]

	def test_the_same_run_draws_the_same_order_every_time(self):
		"""A reshuffle on reload reads, from the learner's side of the screen, as
		the application having lost their answers."""
		self.assertEqual(self._order(1), self._order(1))
		self.assertEqual(self._order(1), self._order(1))

	def test_a_later_run_is_reshuffled(self):
		first = self._order(1)
		self.assertTrue(
			any(self._order(run) != first for run in range(2, 7)),
			"every run drew the same order — the run is not part of the seed",
		)

	def test_two_learners_get_different_orders(self):
		other = _Dict(name="TRN-ATT-000002", course_version=VERSION, shuffle_seed="seed-xyz")
		self.assertTrue(
			any(self._order(run) != self._order(run, other) for run in range(1, 6)),
			"the seed is not per attempt",
		)

	def test_a_missing_seed_still_varies_by_attempt(self):
		"""Falling back to a constant would give the whole site one question order."""
		a = _Dict(name="TRN-ATT-000003", course_version=VERSION, shuffle_seed="")
		b = _Dict(name="TRN-ATT-000004", course_version=VERSION, shuffle_seed="")
		self.assertTrue(any(self._order(run, a) != self._order(run, b) for run in range(1, 6)))

	def test_questions_to_ask_draws_exactly_that_many(self):
		_publish_lesson(questions=_six_questions(), questions_to_ask=3)
		drawn = grading.draw_quiz(self.attempt, LESSON_KEY, 1)
		self.assertEqual(len(drawn), 3)
		self.assertEqual(len({q["question"] for q in drawn}), 3)

	def test_zero_questions_to_ask_means_all_of_them(self):
		self.assertEqual(len(grading.draw_quiz(self.attempt, LESSON_KEY, 1)), 6)

	def test_shuffle_flags_off_preserve_the_authored_order(self):
		public, _key = _publish_lesson(questions=_six_questions(), shuffle=0)
		authored = [q["question"] for q in public["quiz"]["questions"]]
		self.assertEqual(self._order(1), authored)
		drawn = grading.draw_quiz(self.attempt, LESSON_KEY, 1)
		self.assertEqual(
			[o["option_key"] for o in drawn[0]["options"]],
			[o["option_key"] for o in public["quiz"]["questions"][0]["options"]],
		)

	def test_shuffling_options_keeps_the_whole_set(self):
		"""Shuffling that drops or duplicates an option makes the question
		unanswerable, and it would look like a grading bug."""
		drawn = grading.draw_quiz(self.attempt, LESSON_KEY, 1)
		by_name = {q["question"]: q for q in drawn}
		self.assertEqual(
			sorted(o["option_key"] for o in by_name["TRN-Q-000001"]["options"]),
			["optA", "optB", "optC"],
		)

	def test_options_carry_only_a_key_and_text(self):
		drawn = grading.draw_quiz(self.attempt, LESSON_KEY, 1)
		for question in drawn:
			for option in question["options"]:
				self.assertEqual(sorted(option.keys()), ["option_key", "text"])

	def test_a_lesson_without_a_quiz_draws_nothing(self):
		_publish_lesson(questions=[], has_quiz=0)
		self.assertEqual(grading.draw_quiz(self.attempt, LESSON_KEY, 1), [])


# ---------------------------------------------------------------- grade_quiz


class TestGradeQuiz(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_course()
		_video_asset()
		self.public, self.key = _publish_lesson(questions=_six_questions())
		self.attempt = _attempt()
		_set_progress(quiz={"runs": 0, "best": 0.0})

	def _grade(self, answers):
		return grading.grade_quiz(self.attempt, LESSON_KEY, answers)

	def test_all_correct_scores_a_hundred_and_passes(self):
		result = self._grade(_perfect_answers(self.attempt, self.key))
		self.assertEqual(result["score"], 100.0)
		self.assertTrue(result["passed"])

	def test_score_is_weighted_by_points_not_by_question_count(self):
		"""The short answer is worth 4 and the multiple choice 2, so a run that
		gets only the four one-pointers is 4/10, not 4/6."""
		answers = {
			name: list(self.key["quiz"][name]["correct"])
			for name in ("TRN-Q-000001", "TRN-Q-000002", "TRN-Q-000003", "TRN-Q-000004")
		}
		result = self._grade(answers)
		self.assertEqual(result["points_possible"], 10)
		self.assertEqual(result["points_earned"], 4)
		self.assertEqual(result["score"], 40.0)
		self.assertFalse(result["passed"])

	def test_omitting_questions_cannot_shrink_the_denominator(self):
		"""Otherwise the winning strategy is to answer only what you know."""
		one = {"TRN-Q-000001": ["optA"]}
		self.assertEqual(self._grade(one)["points_possible"], 10)
		self.assertEqual(self._grade(one)["score"], 10.0)

	def test_a_client_supplied_score_is_ignored(self):
		answers = {"TRN-Q-000001": ["optA"], "score": 100, "passed": True, "points_earned": 999}
		result = self._grade(answers)
		self.assertEqual(result["score"], 10.0)
		self.assertFalse(result["passed"])

	def test_answers_to_questions_that_were_not_drawn_are_discarded(self):
		_publish_lesson(questions=_six_questions(), questions_to_ask=2)
		drawn = {q["question"] for q in grading.draw_quiz(self.attempt, LESSON_KEY, 1)}
		answers = {name: list(self.key["quiz"][name]["correct"]) for name in self.key["quiz"]}
		result = self._grade(answers)
		self.assertEqual(len(result["per_question"]), 2)
		self.assertEqual({row["question"] for row in result["per_question"]}, drawn)

	def test_multiple_choice_needs_the_exact_set(self):
		exact = self._grade({"TRN-Q-000005": ["mA", "mB"]})
		self.assertTrue(self._row(exact, "TRN-Q-000005")["correct"])
		subset = self._grade({"TRN-Q-000005": ["mA"]})
		self.assertFalse(self._row(subset, "TRN-Q-000005")["correct"])
		everything = self._grade({"TRN-Q-000005": ["mA", "mB", "mC"]})
		self.assertFalse(self._row(everything, "TRN-Q-000005")["correct"])

	def test_a_single_choice_answer_may_arrive_bare(self):
		"""The player sends one key, not a list of one, for single choice."""
		result = self._grade({"TRN-Q-000001": "optA"})
		self.assertTrue(self._row(result, "TRN-Q-000001")["correct"])

	def test_short_answer_ignores_case_padding_and_double_spaces(self):
		for typed in ("Safety Goggles", "  safety goggles  ", "SAFETY   GOGGLES"):
			result = self._grade({"TRN-Q-000006": typed})
			self.assertTrue(self._row(result, "TRN-Q-000006")["correct"], typed)

	def test_short_answer_still_has_to_be_the_answer(self):
		result = self._grade({"TRN-Q-000006": "a hard hat"})
		self.assertFalse(self._row(result, "TRN-Q-000006")["correct"])

	def test_an_empty_short_answer_is_not_a_free_mark(self):
		result = self._grade({"TRN-Q-000006": "   "})
		self.assertFalse(self._row(result, "TRN-Q-000006")["correct"])

	def test_explanations_appear_only_for_questions_that_were_answered(self):
		result = self._grade({"TRN-Q-000001": ["optB"]})
		self.assertTrue(self._row(result, "TRN-Q-000001")["explanation"])
		self.assertEqual(self._row(result, "TRN-Q-000002")["explanation"], "")

	def test_the_review_screen_never_names_the_right_option(self):
		"""A run is retryable, so naming the correct option hands over the retake."""
		result = self._grade({"TRN-Q-000001": ["optB"]})
		for row in result["per_question"]:
			self.assertNotIn("correct_options", row)
			self.assertNotIn("answer", row)
			self.assertIsInstance(row["correct"], bool)

	def test_the_pass_mark_comes_from_the_published_snapshot(self):
		_publish_lesson(questions=_six_questions(), pass_score=40)
		answers = {
			name: list(self.key["quiz"][name]["correct"])
			for name in ("TRN-Q-000001", "TRN-Q-000002", "TRN-Q-000003", "TRN-Q-000004")
		}
		self.assertTrue(self._grade(answers)["passed"])

	def test_the_best_score_is_persisted_and_never_walked_back(self):
		self._grade(_perfect_answers(self.attempt, self.key))
		self._grade({})
		stored = STATE["progress"][ATTEMPT]["lessons"][LESSON_KEY]["quiz"]
		self.assertEqual(stored["best"], 100.0)
		self.assertTrue(all(save["force"] for save in STATE["saves"]))

	def test_the_graded_run_is_the_one_the_runtime_drew(self):
		"""``get_quiz`` draws run ``runs + 1``; grading must land on the same number.

		If the two disagree the learner is graded against a draw they were never
		shown, and from the outside that does not look like an off-by-one — it looks
		like the quiz marking correct answers wrong at random on every retake.
		"""
		_publish_lesson(questions=_six_questions(), questions_to_ask=2)
		_set_progress(quiz={"runs": 1, "best": 40.0})
		second_run = {q["question"] for q in grading.draw_quiz(self.attempt, LESSON_KEY, 2)}
		self.assertNotEqual(
			second_run,
			{q["question"] for q in grading.draw_quiz(self.attempt, LESSON_KEY, 1)},
			"fixture no longer reshuffles between runs, so this test proves nothing",
		)
		answers = {name: list(self.key["quiz"][name]["correct"]) for name in self.key["quiz"]}
		answers["TRN-Q-000006"] = "safety goggles"
		result = self._grade(answers)
		self.assertEqual(result["run"], 2)
		self.assertEqual({row["question"] for row in result["per_question"]}, second_run)
		self.assertEqual(result["score"], 100.0)

	def _row(self, result, question):
		return next(row for row in result["per_question"] if row["question"] == question)


# ----------------------------------------------------------- next_checkpoint


class TestNextCheckpoint(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_course()
		_video_asset()
		_checkpoint(key="cpkey1", at=120)
		_checkpoint(key="cpkey2", at=400)
		self.public, self.key = _publish_lesson(questions=[], has_quiz=0)
		self.attempt = _attempt()
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}})

	def test_nothing_is_offered_before_the_learner_reaches_it(self):
		self.assertIsNone(grading.next_checkpoint(self.attempt, LESSON_KEY, 60))

	def test_exactly_one_checkpoint_comes_back_at_a_time(self):
		"""Two are due at 400. Returning both would be a map of where to skip to."""
		due = grading.next_checkpoint(self.attempt, LESSON_KEY, 400)
		self.assertIsInstance(due, dict)
		self.assertEqual(due["checkpoint_key"], "cpkey1")
		self.assertEqual(due["at"], 120)

	def test_the_next_one_re_arms_after_the_first_is_answered(self):
		grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])
		due = grading.next_checkpoint(self.attempt, LESSON_KEY, 400)
		self.assertEqual(due["checkpoint_key"], "cpkey2")

	def test_an_exhausted_checkpoint_stops_being_offered(self):
		self.assertEqual(
			grading.next_checkpoint(self.attempt, LESSON_KEY, 130)["checkpoint_key"], "cpkey1"
		)
		for _ in range(2):
			grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertIsNone(grading.next_checkpoint(self.attempt, LESSON_KEY, 130))

	def test_the_payload_carries_the_options_but_no_correctness(self):
		due = grading.next_checkpoint(self.attempt, LESSON_KEY, 400)
		self.assertEqual(sorted(o["option_key"] for o in due["options"]), ["cpA", "cpB"])
		for option in due["options"]:
			self.assertEqual(sorted(option.keys()), ["option_key", "text"])

	def test_it_can_be_scoped_to_one_block(self):
		self.assertIsNone(grading.next_checkpoint(self.attempt, LESSON_KEY, 400, block_key="blk9"))


# ----------------------------------------------------------- grade_checkpoint


class TestGradeCheckpoint(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_course()
		_video_asset()
		_checkpoint(key="cpkey1", at=252, max_attempts=2, rewind=15)
		self.public, self.key = _publish_lesson(questions=[], has_quiz=0)
		self.attempt = _attempt()
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 300]], "cov": 0.5}})

	def test_a_correct_answer_resumes_where_the_video_paused(self):
		result = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])
		self.assertTrue(result["correct"])
		self.assertFalse(result["rewind_applied"])
		self.assertEqual(result["resume_at"], 252)

	def test_a_wrong_answer_rewinds(self):
		result = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertFalse(result["correct"])
		self.assertTrue(result["rewind_applied"])
		self.assertEqual(result["resume_at"], 237)

	def test_a_rewind_never_goes_negative(self):
		_reset_state()
		_course()
		_video_asset()
		_checkpoint(key="cpkey1", at=5, max_attempts=3, rewind=15)
		_publish_lesson(questions=[], has_quiz=0)
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 300]], "cov": 0.5}})
		result = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertEqual(result["resume_at"], 0)

	def test_a_checkpoint_in_unwatched_footage_is_refused(self):
		"""Without this, the questions are farmable: seek to the end, collect them
		all, answer at leisure."""
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 100]], "cov": 0.16}})
		with self.assertRaises(Exception) as caught:
			grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])
		self.assertIn("4:12", str(caught.exception))

	def test_no_watch_history_at_all_is_refused(self):
		_set_progress(blocks={})
		with self.assertRaises(Exception):
			grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])

	def test_the_two_second_tolerance_is_honoured_at_the_boundary(self):
		"""Heartbeats are coarse and a pause lands where the browser feels like
		landing, so exact containment rejects honest answers."""
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 250]], "cov": 0.41}})
		self.assertTrue(grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])["correct"])

	def test_the_tolerance_does_not_stretch_to_three_seconds(self):
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 249]], "cov": 0.41}})
		with self.assertRaises(Exception):
			grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])

	def test_attempts_are_counted_server_side_and_run_out(self):
		first = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertEqual(first["attempts"], 1)
		self.assertFalse(first["exhausted"])
		second = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertEqual(second["attempts"], 2)
		self.assertTrue(second["exhausted"])

	def test_answering_past_the_limit_does_not_keep_counting(self):
		for _ in range(4):
			result = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertEqual(result["attempts"], 2)
		self.assertTrue(result["exhausted"])
		self.assertFalse(result["rewind_applied"])

	def test_a_correct_answer_cannot_be_re_used_to_reset_the_counter(self):
		grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])
		again = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertTrue(again["correct"])
		stored = STATE["progress"][ATTEMPT]["lessons"][LESSON_KEY]["checkpoints"]["cpkey1"]
		self.assertEqual(stored["attempts"], 1)

	def test_the_explanation_is_the_checkpoints_own_not_the_options(self):
		result = grading.grade_checkpoint(self.attempt, "cpkey1", ["cpB"])
		self.assertEqual(result["explanation"], "Because the pressure drops.")

	def test_an_unknown_checkpoint_is_refused(self):
		with self.assertRaises(Exception):
			grading.grade_checkpoint(self.attempt, "cpkey-nope", ["cpA"])

	def test_the_answer_is_recorded_against_the_lesson(self):
		grading.grade_checkpoint(self.attempt, "cpkey1", ["cpA"])
		stored = STATE["progress"][ATTEMPT]["lessons"][LESSON_KEY]["checkpoints"]["cpkey1"]
		self.assertEqual(stored["answered"], 1)
		self.assertEqual(stored["correct"], 1)
		self.assertTrue(stored["at"])


# -------------------------------------------------------------- evaluate_gates


class TestEvaluateGates(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_course()
		_video_asset()
		_checkpoint(key="cpkey1", at=252)
		self.public, self.key = _publish_lesson(questions=_six_questions())
		self.attempt = _attempt()

	def _gates(self):
		return grading.evaluate_gates(self.attempt, LESSON_KEY)

	def _fully_done(self):
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}},
			checkpoints={"cpkey1": {"answered": 1, "correct": 1, "attempts": 1}},
			quiz={"runs": 1, "best": 90.0},
		)

	def test_everything_done_opens_the_gate(self):
		self._fully_done()
		result = self._gates()
		self.assertTrue(result["ok"], result["reasons"])
		self.assertEqual(result["reasons"], [])
		self.assertEqual(result["coverage"], 100.0)

	def test_short_coverage_closes_it_and_says_by_how_much(self):
		self._fully_done()
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 300]], "cov": 0.5}},
			checkpoints={"cpkey1": {"answered": 1, "correct": 1, "attempts": 1}},
			quiz={"runs": 1, "best": 90.0},
		)
		result = self._gates()
		self.assertFalse(result["ok"])
		self.assertEqual(result["coverage"], 50.0)
		reason = result["reasons"][0]
		self.assertIn("50%", reason)
		self.assertIn("80%", reason)
		self.assertIn("Safety basics", reason)

	def test_coverage_is_recomputed_from_the_intervals_not_the_cached_number(self):
		"""``cov`` is a heartbeat cache; the intervals are the evidence. A stale or
		hand-edited cache must not open a gate on its own."""
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 60]], "cov": 1.0}},
			checkpoints={"cpkey1": {"answered": 1, "correct": 1, "attempts": 1}},
			quiz={"runs": 1, "best": 90.0},
		)
		result = self._gates()
		self.assertFalse(result["ok"])
		self.assertEqual(result["coverage"], 10.0)

	def test_a_block_level_minimum_overrides_the_course_default(self):
		_publish_lesson(
			blocks=[_block(min_coverage=40)], questions=_six_questions()
		)
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 300]], "cov": 0.5}},
			checkpoints={"cpkey1": {"answered": 1, "correct": 1, "attempts": 1}},
			quiz={"runs": 1, "best": 90.0},
		)
		self.assertTrue(self._gates()["ok"])

	def test_an_external_embed_is_never_coverage_gated(self):
		"""The trap in the README: a compliance course must not quietly lose its
		teeth because somebody picked the convenient block type."""
		_publish_lesson(
			blocks=[_block(key="blk1", block_type="External Embed", duration=0, asset="")],
			questions=[],
			has_quiz=0,
		)
		_set_progress(blocks={})
		result = self._gates()
		self.assertTrue(result["ok"])
		self.assertEqual(result["waived"], [{"block_key": "blk1", "reason": "external_embed"}])

	def test_an_unverified_duration_is_not_gated_either(self):
		"""Coverage divides by duration, so a hand-typed 600 against a real 900
		passes an 80% gate on 53% of a watch — worse than no gate, because it reads
		as one."""
		_video_asset(name="TRN-VID-00002", source="Manual")
		_publish_lesson(
			blocks=[_block(key="blk1", asset="TRN-VID-00002")], questions=[], has_quiz=0
		)
		_set_progress(blocks={})
		result = self._gates()
		self.assertTrue(result["ok"])
		self.assertEqual(result["waived"], [{"block_key": "blk1", "reason": "duration_unverified"}])

	def test_a_zero_duration_is_not_gated(self):
		_publish_lesson(blocks=[_block(duration=0)], questions=[], has_quiz=0)
		_set_progress(blocks={})
		self.assertTrue(self._gates()["ok"])

	def test_an_optional_block_is_ignored_entirely(self):
		_publish_lesson(blocks=[_block(required=0)], questions=[], has_quiz=0)
		_set_progress(blocks={})
		result = self._gates()
		self.assertTrue(result["ok"])
		self.assertEqual(result["waived"], [])

	def test_an_unanswered_checkpoint_closes_the_gate(self):
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}},
			quiz={"runs": 1, "best": 90.0},
		)
		result = self._gates()
		self.assertFalse(result["ok"])
		self.assertTrue(any("unanswered" in reason for reason in result["reasons"]))

	def test_checkpoints_are_only_required_when_the_course_says_so(self):
		_course(require_checkpoints=0)
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}},
			quiz={"runs": 1, "best": 90.0},
		)
		self.assertTrue(self._gates()["ok"])

	def test_a_skippable_checkpoint_does_not_block_completion(self):
		"""``allow_skip`` has to mean something, or it is a lie told to the author."""
		_reset_state()
		_course()
		_video_asset()
		_checkpoint(key="cpkey1", at=252, allow_skip=1)
		_publish_lesson(questions=[], has_quiz=0)
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}})
		self.assertTrue(self._gates()["ok"])

	def test_a_checkpoint_on_a_waived_block_does_not_block_completion(self):
		_reset_state()
		_course()
		_video_asset(name="TRN-VID-00002", source="Manual")
		_checkpoint(key="cpkey1", at=252)
		_publish_lesson(blocks=[_block(asset="TRN-VID-00002")], questions=[], has_quiz=0)
		_set_progress(blocks={})
		self.assertTrue(self._gates()["ok"])

	def test_an_untaken_quiz_says_so_rather_than_quoting_zero_percent(self):
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}},
			checkpoints={"cpkey1": {"answered": 1, "correct": 1, "attempts": 1}},
			quiz={},
		)
		result = self._gates()
		self.assertFalse(result["ok"])
		self.assertTrue(any("not been taken" in reason for reason in result["reasons"]))

	def test_a_failed_quiz_quotes_both_numbers(self):
		_set_progress(
			blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}},
			checkpoints={"cpkey1": {"answered": 1, "correct": 1, "attempts": 1}},
			quiz={"runs": 2, "best": 60.0},
		)
		reason = next(r for r in self._gates()["reasons"] if "quiz" in r)
		self.assertIn("60%", reason)
		self.assertIn("80%", reason)

	def test_every_reason_is_a_sentence_a_learner_can_act_on(self):
		"""A learner told only "not complete" cannot act on it and opens a ticket."""
		_set_progress(blocks={}, checkpoints={}, quiz={})
		for reason in self._gates()["reasons"]:
			self.assertTrue(reason.endswith("."), reason)
			self.assertGreater(len(reason), 20, reason)
			self.assertNotIn("_", reason)

	def test_evaluating_the_gates_never_writes(self):
		"""It runs on the completion path and can run on every heartbeat. A write
		here is a write in a loop."""
		self._fully_done()
		STATE["saves"].clear()
		self._gates()
		self.assertEqual(STATE["saves"], [])


class TestTheCheckpointGateOnAnEmptyMap(unittest.TestCase):
	"""What ``require_checkpoints_answered`` does when nothing has been answered.

	The question is not academic. Checkpoints had never armed in production
	(TASK-2026-01174), so every attempt carried ``checkpoints == {}`` — and the
	gate that stands on them was live the whole time. Silently waived and
	permanently blocked are both defensible readings of an empty map; they are
	very different bugs, and nothing pinned which one this was.

	It is neither, because "empty" is two situations and the gate distinguishes
	them: it iterates the *lesson's authored* checkpoints and looks each one up in
	the learner's answers. No checkpoints authored means no iterations, so the gate
	opens. Checkpoints authored and none answered means one miss each, so it
	closes. Both are pinned below so a future refactor cannot quietly swap them.
	"""

	def _gates(self):
		return grading.evaluate_gates(self.attempt, LESSON_KEY)

	def _watched(self, **extra):
		_set_progress(blocks={"blk1": {"dur": 600, "iv": [[0, 600]], "cov": 1.0}}, **extra)

	def test_a_lesson_with_no_checkpoints_is_not_blocked_by_the_flag(self):
		"""Fail-open, and deliberately: turning the flag on at course level must not
		strand every lesson that happens to carry no pins."""
		_reset_state()
		_course(require_checkpoints=1)
		_video_asset()
		_publish_lesson(questions=[], has_quiz=0)
		self.attempt = _attempt()
		self._watched()
		result = self._gates()
		self.assertTrue(result["ok"], result["reasons"])

	def test_an_authored_checkpoint_with_an_empty_answer_map_blocks(self):
		"""The production case. This is why shipping the arming fix also releases
		learners who were stuck behind a question they were never asked."""
		_reset_state()
		_course(require_checkpoints=1)
		_video_asset()
		_checkpoint(key="cpkey1", at=252)
		_publish_lesson(questions=[], has_quiz=0)
		self.attempt = _attempt()
		self._watched(checkpoints={})
		result = self._gates()
		self.assertFalse(result["ok"])
		self.assertTrue(any("unanswered" in reason for reason in result["reasons"]))

	def test_answering_it_opens_the_gate(self):
		"""The other half of the pair — otherwise the test above passes for a gate
		that is simply always closed."""
		_reset_state()
		_course(require_checkpoints=1)
		_video_asset()
		_checkpoint(key="cpkey1", at=252)
		_publish_lesson(questions=[], has_quiz=0)
		self.attempt = _attempt()
		self._watched(checkpoints={"cpkey1": {"answered": 1, "correct": 1, "attempts": 1}})
		self.assertTrue(self._gates()["ok"])


if __name__ == "__main__":
	unittest.main()
