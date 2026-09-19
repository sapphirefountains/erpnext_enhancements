# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""AI-graded Short Answers, and the dispute that keeps that honest. Bench-free.

v1.490.0 let an AI decide a Short Answer with **no human sign-off in front of
it** — a deliberate departure from this module's standing rule that a model may
draft and a named human must accept (Nik, 2026-09-19; ADR 0015). Two properties
are what make that defensible, and this file exists to stop either being lost in
a refactor, because neither fails loudly:

**1. The AI can only ever be generous.** The exact match runs FIRST, and the
model is consulted only on an answer it already rejected. So the AI can turn a
wrong into a right and cannot do the reverse — a correct answer never reaches
it. This is tested by *execution* rather than by reading the source, because
"exact match first" is an ordering, and an ordering is exactly the kind of thing
a tidy-up reverses without changing a single assertion elsewhere.

**2. Every failure falls back to the exact match.** Switch off, Vertex down,
unparseable reply, an answer too long to be a short answer — all of them leave
the learner with precisely the behaviour they had before v1.490.0, rather than
an error in the middle of a quiz submission. A learner must never be marked
wrong *because* a model was unavailable, and must never be blocked by one.

The rest is the dispute flow: a learner pushes back, a Training Manager rules,
and upholding re-marks the answer, re-scores the run and re-drives the attempt
through the ordinary completion path — `api.training._evaluate_attempt`, the
same function a supervisor's sign-off re-drives. A second implementation of
"what a pass does" is how two of them come to disagree, so this file asserts the
reuse rather than the behaviour.

Also pinned here: the canvas could not author a working Short Answer at all.
Its accepted-answers control was a single-line input placeholdered "comma
separated" while every reader splits on NEWLINES, so an author could not enter a
second accepted answer and one who followed the placeholder created a single
accepted answer reading "gloves, goggles".

Run: python -m unittest erpnext_enhancements.tests.test_training_disputes
"""

import ast
import io
import json
import sys
import tokenize
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = Path(__file__).resolve().parents[1]
GRADING = APP / "training/grading.py"
DISPUTES = APP / "training/disputes.py"
AI = APP / "api/training_ai.py"
TRAINING_API = APP / "api/training.py"
CANVAS_JS = APP / "training/page/training_canvas/training_canvas.js"
QUIZ_JS = APP / "public/js/training/quiz.js"
TRANSPORT_JS = APP / "public/js/training/transport.js"
HOOKS = APP / "hooks.py"
DISPUTE_JSON = APP / "training/doctype/training_answer_dispute/training_answer_dispute.json"
SETTINGS_JSON = APP / "training/doctype/training_settings/training_settings.json"
ANSWER_JSON = APP / "training/doctype/training_attempt_question/training_attempt_question.json"

grading = None

#: What the fake judge was asked, and what it should answer. Reset per test.
JUDGE = {}


def setUpModule():
	"""Stub ``frappe`` at execution time, then import ``training.grading``.

	Execution time rather than import time, as every bench-free suite here does:
	a stub installed at import satisfies the bench-only suites' ``import frappe``
	skip-guards and makes them run against a fake.
	"""
	global grading
	frappe = types.ModuleType("frappe")

	def _throw(msg, exc=None):
		raise (exc or Exception)(msg)

	frappe.throw = _throw
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.__dict__["_"] = lambda s: s
	frappe.get_doc = lambda *a, **k: None
	frappe.get_all = lambda *a, **k: []
	frappe.get_roles = lambda *a, **k: []
	frappe.session = types.SimpleNamespace(user="learner@example.com")
	frappe.parse_json = json.loads
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.generate_hash = lambda length=10: "h" * length
	frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None, escape=lambda v: f"'{v}'")

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.flt = lambda v, p=None: round(float(v or 0), p) if p is not None else float(v or 0)
	utils.now_datetime = lambda: "2026-09-19 12:00:00"
	frappe.utils = utils

	sys.modules.setdefault("frappe", frappe)
	sys.modules.setdefault("frappe.utils", utils)

	from erpnext_enhancements.training import grading as module

	grading = module

	# The judge, faked at the import path `_judge_text_answer` reaches for. The
	# real one is exercised in test_training_ai; what matters here is WHEN it is
	# called and what the caller does with each answer it can give.
	ai = types.ModuleType("erpnext_enhancements.api.training_ai")

	def _judge(question_text, accepted, submitted):
		JUDGE["calls"].append({"question": question_text, "accepted": list(accepted), "submitted": submitted})
		verdict = JUDGE["verdict"]
		if isinstance(verdict, Exception):
			raise verdict
		return verdict

	ai.judge_short_answer = _judge
	sys.modules["erpnext_enhancements.api.training_ai"] = ai


def _reset():
	JUDGE.clear()
	JUDGE.update({"calls": [], "verdict": None})


def _text(path):
	return path.read_text(encoding="utf-8")


def _py_code(path):
	"""Python source with comments and docstrings stripped.

	Every absence assertion below needs it: ``disputes.py``'s docstring explains
	at length that it does NOT reimplement the completion, naming
	``Training Completion`` while doing so, and a plain scan for that string is
	then satisfied by the paragraph promising its absence.
	"""
	kept = [
		token
		for token in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
		if token.type != tokenize.COMMENT
	]
	tree = ast.parse(tokenize.untokenize(kept))
	for node in ast.walk(tree):
		if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
			continue
		body = node.body
		if (
			body
			and isinstance(body[0], ast.Expr)
			and isinstance(body[0].value, ast.Constant)
			and isinstance(body[0].value.value, str)
		):
			body[0].value.value = ""
	return ast.unparse(tree)


def _strip_js_comments(src):
	out, in_block = [], False
	for line in src.splitlines():
		stripped = line.strip()
		if in_block:
			if "*/" in stripped:
				in_block = False
			continue
		if stripped.startswith("/*"):
			in_block = "*/" not in stripped
			continue
		if stripped.startswith("//"):
			continue
		out.append(line)
	return "\n".join(out)


def _entry(accepted=("isolation valve",)):
	return {"type": "Short Answer", "accepted_text": list(accepted)}


# ============================================ the AI can only be generous


class TestExactMatchRunsFirst(unittest.TestCase):
	"""The whole safety argument, tested as behaviour rather than as source.

	If this ordering is ever reversed, a model becomes able to mark a *correct*
	answer wrong — and nothing else in the suite would notice, because every
	other assertion is about what happens to answers that were already wrong.
	"""

	def setUp(self):
		_reset()

	def test_an_exact_match_never_reaches_the_model(self):
		JUDGE["verdict"] = {"correct": False, "reasoning": "nope", "model": "m"}
		correct, judgement = grading._judge_text_answer(_entry(), ["isolation valve"])
		self.assertTrue(correct)
		self.assertIsNone(judgement)
		self.assertEqual(JUDGE["calls"], [], "a correct answer was sent to a model")

	def test_case_and_spacing_are_still_forgiven_without_a_model(self):
		JUDGE["verdict"] = {"correct": False, "reasoning": "nope", "model": "m"}
		for typed in ("Isolation Valve", "  isolation   valve  ", "ISOLATION VALVE"):
			with self.subTest(typed=typed):
				_reset()
				JUDGE["verdict"] = {"correct": False, "reasoning": "nope", "model": "m"}
				correct, judgement = grading._judge_text_answer(_entry(), [typed])
				self.assertTrue(correct)
				self.assertEqual(JUDGE["calls"], [])

	def test_a_near_miss_is_the_case_that_does_reach_the_model(self):
		JUDGE["verdict"] = {"correct": True, "reasoning": "Same valve.", "model": "m"}
		correct, judgement = grading._judge_text_answer(
			_entry(), ["the isolation valve."], "Which valve?"
		)
		self.assertTrue(correct)
		self.assertEqual(judgement["reasoning"], "Same valve.")
		self.assertEqual(len(JUDGE["calls"]), 1)
		self.assertEqual(JUDGE["calls"][0]["submitted"], "the isolation valve.")
		self.assertEqual(JUDGE["calls"][0]["question"], "Which valve?")

	def test_the_model_can_confirm_a_wrong_answer_and_still_explain_it(self):
		"""A rejection carries its reasoning, because that is what the learner
		disputes against. Dropping it would leave them a bare "wrong"."""
		JUDGE["verdict"] = {"correct": False, "reasoning": "That is the bleed valve.", "model": "m"}
		correct, judgement = grading._judge_text_answer(_entry(), ["bleed valve"])
		self.assertFalse(correct)
		self.assertEqual(judgement["reasoning"], "That is the bleed valve.")


class TestEveryFailureFallsBackToTheExactMatch(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_no_opinion_leaves_the_answer_wrong_and_unjudged(self):
		"""`None` is "the AI was not consulted or could not be trusted". The
		verdict must then be the exact match's, and `judgement` must be None so
		`ai_judged` records that nobody asked — which is how a dispute months
		later tells "the AI said no" from "the AI was never asked"."""
		JUDGE["verdict"] = None
		correct, judgement = grading._judge_text_answer(_entry(), ["bleed valve"])
		self.assertFalse(correct)
		self.assertIsNone(judgement)

	def test_a_raising_judge_is_caught_rather_than_breaking_the_submission(self):
		"""This runs inside a learner's quiz submit. An exception here is an error
		on the last click of a safety course."""
		JUDGE["verdict"] = RuntimeError("vertex is down")
		correct, judgement = grading._judge_text_answer(_entry(), ["bleed valve"])
		self.assertFalse(correct)
		self.assertIsNone(judgement)

	def test_an_outage_cannot_turn_a_right_answer_wrong(self):
		"""The two properties together. Even with the model exploding, an exact
		match is still correct — because it never got that far."""
		JUDGE["verdict"] = RuntimeError("vertex is down")
		correct, _judgement = grading._judge_text_answer(_entry(), ["isolation valve"])
		self.assertTrue(correct)
		self.assertEqual(JUDGE["calls"], [])

	def test_a_blank_answer_is_wrong_and_costs_no_model_call(self):
		JUDGE["verdict"] = {"correct": True, "reasoning": "sure", "model": "m"}
		for blank in ([], [""], ["   "], None):
			with self.subTest(blank=blank):
				_reset()
				JUDGE["verdict"] = {"correct": True, "reasoning": "sure", "model": "m"}
				correct, _j = grading._judge_text_answer(_entry(), blank)
				self.assertFalse(correct)


class TestTheGradedRunCarriesTheVerdict(unittest.TestCase):
	def test_the_review_payload_names_the_ai_and_its_reasons(self):
		"""Half of what was asked for: the learner is told why. Asserted on the
		source because building a whole graded run needs a bench."""
		code = _py_code(GRADING)
		at = code.index("per_question.append")
		window = code[at : at + 1200]
		for field in ("ai_reasoning", "ai_judged", "ai_model"):
			with self.subTest(field=field):
				self.assertIn(field, window)

	def test_the_verdict_is_filed_on_the_answer_row(self):
		"""`AI Model Usage` stores token counts only, so the answer row is the ONLY
		place the reasoning survives — and a dispute weeks later is adjudicated on
		it."""
		code = _py_code(GRADING)
		at = code.index("def _file_quiz_answers")
		window = code[at : at + 2500]
		for field in ("ai_judged", "ai_reasoning", "ai_model"):
			with self.subTest(field=field):
				self.assertIn(field, window)

	def test_the_answer_doctype_has_somewhere_to_put_them(self):
		fields = {f["fieldname"] for f in json.loads(_text(ANSWER_JSON))["fields"]}
		for field in ("ai_judged", "ai_reasoning", "ai_model", "corrected_by", "corrected_on"):
			with self.subTest(field=field):
				self.assertIn(field, fields)

	def test_the_ai_fields_are_read_only(self):
		"""They are the evidence in a dispute. A field a manager can quietly retype
		is not evidence — the correction has its own `corrected_by`."""
		by_name = {f["fieldname"]: f for f in json.loads(_text(ANSWER_JSON))["fields"]}
		for field in ("ai_judged", "ai_reasoning", "ai_model", "corrected_by", "corrected_on"):
			with self.subTest(field=field):
				self.assertEqual(by_name[field].get("read_only"), 1)


class TestTheSwitch(unittest.TestCase):
	def test_grading_has_its_own_flag_separate_from_drafting(self):
		"""Drafting is opt-in per action and discardable; grading happens silently
		to people. A site should be able to have one without the other."""
		fields = {f["fieldname"] for f in json.loads(_text(SETTINGS_JSON))["fields"]}
		self.assertIn("ai_grade_short_answers", fields)
		self.assertIn("ai_assist_enabled", fields)

	def test_it_defaults_off(self):
		by_name = {f["fieldname"]: f for f in json.loads(_text(SETTINGS_JSON))["fields"]}
		self.assertEqual(by_name["ai_grade_short_answers"].get("default"), "0")

	def test_no_backfill_patch_is_needed_and_that_is_deliberate(self):
		"""Training Settings is a **Single**, where a new field's default never
		reaches the row that already exists — normally the reason to ship a
		backfill. Not here: the wanted state on every existing site is OFF, and a
		missing `tabSingles` row reads as 0 through `is_enabled`'s
		`cint(getattr(settings, switch, 0))`. Shipping a backfill that wrote 0
		would be writing the value it already has."""
		src = _text(APP / "training/doctype/training_settings/training_settings.py")
		self.assertIn("getattr(settings, switch, 0)", src)


# ==================================================== the dispute flow


class TestTheDisputeEndpoints(unittest.TestCase):
	def _functions(self, path):
		return {
			node.name: node
			for node in ast.parse(_text(path)).body
			if isinstance(node, ast.FunctionDef)
		}

	def _decorator_methods(self, node):
		for dec in node.decorator_list:
			if not isinstance(dec, ast.Call):
				continue
			if getattr(dec.func, "attr", "") != "whitelist":
				continue
			for keyword in dec.keywords:
				if keyword.arg == "methods":
					return [e.value for e in keyword.value.elts]
		return []

	def test_the_three_endpoints_exist_and_are_post_only(self):
		functions = self._functions(DISPUTES)
		for name in ("raise_dispute", "resolve_dispute", "reset_quiz_attempts"):
			with self.subTest(endpoint=name):
				self.assertIn(name, functions)
				self.assertEqual(self._decorator_methods(functions[name]), ["POST"])

	def test_ruling_and_resetting_are_manager_only(self):
		functions = self._functions(DISPUTES)
		for name in ("resolve_dispute", "reset_quiz_attempts"):
			with self.subTest(endpoint=name):
				self.assertIn("_require_manager()", ast.unparse(functions[name]))

	def test_raising_one_is_not_manager_gated(self):
		"""It is the learner's own act. Gating it would make the dispute path
		unreachable by the only person who ever needs it."""
		functions = self._functions(DISPUTES)
		self.assertNotIn("_require_manager()", ast.unparse(functions["raise_dispute"]))

	def test_only_raising_is_reachable_from_the_learner_runtime(self):
		"""Resolving is deliberately NOT re-exported through api/training.py, where
		the player's single transport prefix could reach it."""
		code = _py_code(TRAINING_API)
		self.assertIn("disputes.raise_dispute", code)
		self.assertNotIn("disputes.resolve_dispute", code)
		self.assertNotIn("disputes.reset_quiz_attempts", code)

	def test_the_re_export_delegates_rather_than_reimplements(self):
		functions = self._functions(TRAINING_API)
		body = ast.unparse(functions["raise_answer_dispute"])
		self.assertIn("disputes.raise_dispute", body)
		self.assertNotIn("Training Answer Dispute", body)

	def test_the_player_can_dial_it(self):
		self.assertIn("raiseDispute", _strip_js_comments(_text(TRANSPORT_JS)))
		self.assertIn('raiseDispute: "raise_answer_dispute"', _strip_js_comments(_text(TRANSPORT_JS)))

	def test_a_rejection_still_has_to_be_explained(self):
		"""An unexplained "no" is what they disputed in the first place."""
		functions = self._functions(DISPUTES)
		self.assertIn("Say why", ast.unparse(functions["resolve_dispute"]))


class TestUpholdingReusesTheRealPassPath(unittest.TestCase):
	def test_it_re_drives_the_attempt_rather_than_reimplementing_a_pass(self):
		"""`_evaluate_attempt` was already split out to be re-drivable — a
		supervisor's sign-off reaches it from a doc_event with no session learner.
		Reusing it is what makes a corrected pass issue the identical completion,
		certificate and assignment close."""
		code = _py_code(DISPUTES)
		self.assertIn("_evaluate_attempt", code)

	def test_it_creates_no_completion_or_certificate_of_its_own(self):
		"""A second implementation of "what a pass does" is how two of them come to
		disagree. Comments and docstrings stripped first — the module docstring
		names both while promising not to build them."""
		code = _py_code(DISPUTES)
		for forbidden in ("Training Completion", "Training Certificate"):
			with self.subTest(doctype=forbidden):
				self.assertNotIn(forbidden, code)

	def test_the_re_score_can_only_raise_the_best(self):
		"""`_record_quiz_run` keeps `max(best, score)`, and a correction can only
		add points — so recording one can never take away a pass the learner
		already holds on another run."""
		code = _py_code(GRADING)
		at = code.index("def _record_quiz_run")
		self.assertIn("max(", code[at : at + 600])

	def test_the_correction_is_stamped_rather_than_silent(self):
		code = _py_code(DISPUTES)
		self.assertIn("corrected_by", code)
		self.assertIn("corrected_on", code)

	def test_a_settled_dispute_cannot_be_re_ruled(self):
		"""Upholding runs a re-score and can issue a certificate. Flipping the
		status afterwards would imply those were undone, and nothing undoes them."""
		controller = APP / "training/doctype/training_answer_dispute/training_answer_dispute.py"
		self.assertIn("_freeze_a_settled_verdict", _py_code(controller))

	def test_only_one_open_dispute_per_answer(self):
		controller = APP / "training/doctype/training_answer_dispute/training_answer_dispute.py"
		self.assertIn("_one_open_per_answer", _py_code(controller))


class TestTheResetTheAppPromised(unittest.TestCase):
	def test_the_message_now_has_an_implementation(self):
		"""`start_quiz` has told learners "A Training Manager can reset it for you"
		since the module shipped, and until v1.490.0 no reset existed anywhere in
		the app — a dead end at the exact moment a learner is locked out."""
		self.assertIn("A Training Manager can reset it for you", _text(TRAINING_API))
		self.assertIn("def reset_quiz_attempts", _text(DISPUTES))

	def test_it_resets_the_run_count_and_not_the_score(self):
		"""A reset is "have another go", not "lose what you earned" — zeroing the
		best could take away a pass held on an earlier run."""
		functions = {
			node.name: node
			for node in ast.parse(_text(DISPUTES)).body
			if isinstance(node, ast.FunctionDef)
		}
		body = ast.unparse(functions["reset_quiz_attempts"])
		self.assertIn('"runs"', body.replace("'", '"'))
		self.assertNotIn('"best"', body.replace("'", '"'))

	def test_it_leaves_a_trace_on_the_attempt(self):
		"""Somebody will ask why this learner had six goes at a three-attempt quiz."""
		code = _py_code(DISPUTES)
		self.assertIn("add_comment", code)


class TestDisputeVisibilityIsScoped(unittest.TestCase):
    def test_the_doctype_carries_the_user_the_scoping_needs(self):
        fields = {f["fieldname"] for f in json.loads(_text(DISPUTE_JSON))["fields"]}
        self.assertIn("user", fields)

    def test_both_permission_hooks_are_wired(self):
        hooks = _text(HOOKS)
        self.assertIn("answer_dispute_query_conditions", hooks)
        self.assertIn("answer_dispute_has_permission", hooks)

    def test_a_learner_cannot_write_one_through_the_desk(self):
        """They hold `read` only; raising one is an act the server performs on
        their behalf after an ownership check. `create` here would let a learner
        file a dispute naming somebody else's answer."""
        perms = {p["role"]: p for p in json.loads(_text(DISPUTE_JSON))["permissions"]}
        self.assertEqual(perms["Training Learner"].get("write"), None)
        self.assertEqual(perms["Training Learner"].get("create"), None)
        self.assertEqual(perms["Training Learner"]["read"], 1)

    def test_the_accepted_answers_are_snapshotted_not_looked_up_live(self):
        """A new draft version can rewrite the accepted answers freely. The
        reviewer has to see what the learner was actually marked against."""
        by_name = {f["fieldname"]: f for f in json.loads(_text(DISPUTE_JSON))["fields"]}
        self.assertIn("accepted_answers", by_name)
        self.assertEqual(by_name["accepted_answers"].get("read_only"), 1)


# ======================================= the canvas could not author one


class TestTheCanvasCanAuthorAShortAnswer(unittest.TestCase):
	"""It could not, and every Short Answer made there was close to unpassable."""

	def test_the_control_takes_more_than_one_line(self):
		code = _strip_js_comments(_text(CANVAS_JS))
		at = code.index('row.question_type === "Short Answer"')
		window = code[at : at + 600]
		self.assertIn("<textarea", window)
		self.assertNotIn('<input type="text"', window)

	def test_the_placeholder_matches_how_the_field_is_actually_split(self):
		"""`_validate_short_answer` and `_split_lesson`'s `accepted_text` both do
		`.splitlines()`, and the Desk form's own description says "one per line".
		The canvas said "comma separated", so an author following it created one
		accepted answer reading literally "gloves, goggles"."""
		code = _strip_js_comments(_text(CANVAS_JS))
		self.assertIn("Accepted answers, one per line", code)
		self.assertNotIn("comma separated", code)

	def test_the_desk_form_and_the_canvas_now_agree(self):
		question_json = APP / "training/doctype/training_question/training_question.json"
		by_name = {f["fieldname"]: f for f in json.loads(_text(question_json))["fields"]}
		self.assertIn("one per line", by_name["correct_text_answers"]["description"])


class TestTheLearnerCanSeeAndContestTheVerdict(unittest.TestCase):
	def test_the_reasoning_is_rendered(self):
		self.assertIn("ai_reasoning", _strip_js_comments(_text(QUIZ_JS)))

	def test_it_is_rendered_as_text_not_markup(self):
		"""`ai_reasoning` is a model-authored string and the one thing on this
		screen that is neither author-written nor server-computed. `el()` sets
		textContent; `setHtml` is the sanitiser path for author HTML."""
		code = _strip_js_comments(_text(QUIZ_JS))
		# The window opens AT the guard and closes at the end of that statement.
		# Opening it earlier swept in the PRECEDING line, which is the author's
		# explanation going through `setHtml` quite legitimately -- so the
		# assertion failed on correct code, which is the same sliding-window
		# problem as a window that is too generous, in the other direction.
		at = code.index("if (entry.ai_reasoning)")
		window = code[at : code.index("}", at) + 1]
		self.assertIn('el("div", "tr-rev-ai"', window)
		self.assertNotIn("setHtml", window)

	def test_the_dispute_button_appears_only_on_an_ai_rejection(self):
		"""Not on a plain exact-match miss: there is no machine verdict to contest,
		and the author's accepted list is the thing to fix instead."""
		code = _strip_js_comments(_text(QUIZ_JS))
		self.assertIn("entry.ai_judged && entry.correct === false", code)

	def test_the_button_is_hidden_where_the_transport_cannot_carry_it(self):
		"""The authoring preview mounts the same quiz with a different transport.
		A button that opens nothing is worse than no button."""
		code = _strip_js_comments(_text(QUIZ_JS))
		self.assertIn('typeof transport.raiseDispute === "function"', code)

	def test_the_run_comes_off_the_graded_result(self):
		"""This card is about one finished run; the player's live state may have
		moved on to another lesson by the time anybody reads it."""
		code = _strip_js_comments(_text(QUIZ_JS))
		at = code.index("transport.raiseDispute({")
		self.assertIn("result && result.run", code[at : at + 400])

	def test_every_class_it_renders_is_styled(self):
		code = _text(QUIZ_JS)
		for name in ("tr-rev-ai", "tr-rev-dispute", "tr-rev-dispute-note", "tr-rev-dispute-said"):
			with self.subTest(css_class=name):
				self.assertIn("." + name, code)


if __name__ == "__main__":
	unittest.main()
