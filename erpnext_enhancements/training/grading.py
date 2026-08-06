# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Server-side grading for the Training runtime — the only reader of the answer key.

**This module is the trust boundary.** ``Training Lesson.answer_key_json`` is
written once at publish by ``api/training_author.py::_split_lesson`` and read
*here and nowhere else*. Every other module — the runtime endpoints, the player,
the Phase-3 builder preview — asks this one for a decision and receives a verdict,
never the material the verdict was made from. That is a deliberately narrow door:
the guarantee "a learner cannot read the answers out of their own browser" is only
as strong as the number of functions capable of breaking it, and there is one.

The shapes are not invented here. ``public_lesson`` returns exactly what
``_split_lesson`` materialised as ``published_content_json``, and the key is exactly
its ``answer_key_json``. Read that function before changing anything in this file;
the two are one contract written in two places.

Five rules the code below exists to enforce, each of which has a plausible-looking
implementation that quietly breaks it:

* **Options are rebuilt field by field, never copied.** ``draw_quiz`` constructs
  ``{"option_key", "text"}`` explicitly rather than passing the published option
  dict through. The published payload is answer-free today; a field added to it in
  a year would otherwise leak through this function on the day it was added.
* **The denominator is drawn by the server.** ``grade_quiz`` re-draws the run with
  the same deterministic seed and grades *that* set. Grading whatever the client
  submitted would let a learner score 100% by answering the one question they knew
  and omitting the rest.
* **Checkpoint timestamps are handed out one at a time.** :func:`next_checkpoint`
  returns the single checkpoint that is due at the position the learner has
  actually reached. A list of ``at_seconds`` is a map of exactly where to skip to.
* **A checkpoint cannot be answered from a position that was never watched.**
  :func:`grade_checkpoint` refuses unless the stored watch intervals cover the
  checkpoint's timestamp. Otherwise the questions can be farmed by seeking.
* **Correct option keys never appear in a result.** Explanations are revealed after
  an answer, because that is the teaching moment; *which option was right* is not,
  because attempts are retryable and a failed run would otherwise hand over a
  perfect second run.

``evaluate_gates`` is deliberately read-only — it is a decision, called on the
completion path and potentially on every heartbeat, and a function that writes on
a read path is a function that writes in a loop. Waivers it decides
(``external_embed``, ``duration_unverified``) come back in the result for the
caller to persist onto the progress row.
"""

import hashlib
import importlib
import random

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

# How far outside a recorded watch interval a checkpoint may sit and still be
# answerable. Heartbeats are coarse (15s by default) and a pause lands where the
# browser felt like landing, so an exact containment test rejects honest answers.
CHECKPOINT_TOLERANCE_SECONDS = 2

# Only a real <video> block can be coverage-gated: it is the only block type whose
# consumption the server can measure. See the External Embed note in README.md.
GATEABLE_BLOCK_TYPE = "Video"
EXTERNAL_EMBED_BLOCK_TYPE = "External Embed"

WAIVED_EXTERNAL_EMBED = "external_embed"
WAIVED_DURATION_UNVERIFIED = "duration_unverified"

# Last-resort fallbacks, used only when both the course and Training Settings are
# silent. They match the doctype defaults so a half-configured course still gates.
FALLBACK_PASS_SCORE = 80
FALLBACK_MIN_COVERAGE = 80


# ------------------------------------------------------------------- published


def public_lesson(lesson_name):
	"""The answer-free payload the player renders.

	Read with ``db.get_value`` on the single field rather than ``get_doc`` on
	purpose: a request that only needs public content must never pull
	``answer_key_json`` into memory alongside it.
	"""
	blob = frappe.db.get_value("Training Lesson", lesson_name, "published_content_json")
	if not blob:
		frappe.throw(
			_("This lesson has not been published yet, so there is nothing to show. "
			  "Ask a Training Manager to publish the course version.")
		)
	return frappe.parse_json(blob) or {}


def answer_key(lesson_name):
	"""The correct answers. **Server only — never return this to a caller.**

	Every function in this module that touches the value it returns reduces it to a
	verdict before handing anything back. Nothing outside this module may call it;
	that is the whole design, not a style preference.
	"""
	blob = frappe.db.get_value("Training Lesson", lesson_name, "answer_key_json")
	if not blob:
		frappe.throw(_("This lesson has no answer key, so it cannot be graded."))
	return frappe.parse_json(blob) or {}


# ------------------------------------------------------------------------ quiz


def draw_quiz(attempt, lesson_key, run):
	"""The questions for one quiz run, shuffled deterministically, answer-free.

	Deterministic because a learner who refreshes mid-quiz and gets a different
	question order has, from where they are sitting, watched the application lose
	their answers. The seed is per attempt *and* per lesson *and* per run, so a
	retake is genuinely reshuffled while a reload is not.
	"""
	doc = _attempt(attempt)
	lesson_name = _lesson_name(doc, lesson_key)
	public = public_lesson(lesson_name)
	quiz = public.get("quiz") or {}
	if not cint(quiz.get("enabled")):
		return []

	questions = list(quiz.get("questions") or [])
	rng = _rng(doc, lesson_key, run)
	if cint(quiz.get("shuffle_questions")):
		rng.shuffle(questions)

	ask = cint(quiz.get("questions_to_ask"))
	if ask > 0:
		questions = questions[:ask]

	shuffle_options = cint(quiz.get("shuffle_options"))
	drawn = []
	for question in questions:
		options = [
			# Rebuilt, not forwarded. See the module docstring.
			{"option_key": option.get("option_key"), "text": option.get("text")}
			for option in question.get("options") or []
		]
		if shuffle_options:
			rng.shuffle(options)
		drawn.append(
			{
				"question": question.get("question"),
				"type": question.get("type"),
				"text": question.get("text"),
				"points": cint(question.get("points")) or 1,
				"options": options,
			}
		)
	return drawn


def grade_quiz(attempt, lesson_key, answers, run=None):
	"""Grade a submitted quiz run against the answer key alone.

	``answers`` maps question name to the submitted option keys (or to typed text
	for Short Answer). Anything else in it — a ``score`` the client helpfully
	calculated, a question that was not drawn — is discarded before grading, which
	is why the drawn set is recomputed here rather than inferred from the keys of
	``answers``.
	"""
	doc = _attempt(attempt)
	lesson_name = _lesson_name(doc, lesson_key)
	key = answer_key(lesson_name)
	submitted = frappe.parse_json(answers) if isinstance(answers, str) else (answers or {})
	if not isinstance(submitted, dict):
		submitted = {}

	run = cint(run) or _current_run(doc, lesson_key)
	drawn = draw_quiz(doc, lesson_key, run)

	earned = 0
	possible = 0
	per_question = []
	for question in drawn:
		name = question.get("question")
		entry = (key.get("quiz") or {}).get(name) or {}
		points = cint(entry.get("points")) or cint(question.get("points")) or 1
		possible += points

		answered = name in submitted
		correct = _matches_key(entry, submitted.get(name)) if answered else False
		if correct:
			earned += points

		per_question.append(
			{
				"question": name,
				"text": question.get("text"),
				"type": question.get("type"),
				"points": points,
				"awarded": points if correct else 0,
				"answered": answered,
				"correct": correct,
				# Revealed only for a question the learner actually attempted, and
				# never accompanied by the correct option keys — the run is
				# retryable, so naming the right option hands over the retake.
				"explanation": entry.get("explanation") or "" if answered else "",
			}
		)

	score = flt(100.0 * earned / possible, 2) if possible else 0.0
	# The pass mark comes from the published snapshot, not the live lesson row: a
	# learner is graded against the course as it was when they took it.
	published_quiz = public_lesson(lesson_name).get("quiz") or {}
	pass_score = cint(published_quiz.get("pass_score")) or _course_policy(lesson_name)["pass_score"]
	passed = bool(possible) and score >= pass_score

	_record_quiz_run(doc, lesson_key, run, score)
	_file_quiz_answers(doc, lesson_name, run, drawn, submitted, per_question)

	return {
		"score": score,
		"passed": passed,
		"pass_score": pass_score,
		"run": run,
		"points_earned": earned,
		"points_possible": possible,
		"per_question": per_question,
	}


# ----------------------------------------------------------------- checkpoints


def next_checkpoint(attempt, lesson_key, position, block_key=None):
	"""The one checkpoint that is due at ``position``, or ``None``.

	Due means: its timestamp has been reached, it has not been answered correctly,
	and its attempts are not exhausted. One at a time and only once reached —
	handing the player the whole list would tell it precisely where to skip to.
	"""
	doc = _attempt(attempt)
	lesson_name = _lesson_name(doc, lesson_key)
	key = answer_key(lesson_name)
	state = _lesson_progress(doc, lesson_key).get("checkpoints") or {}
	position = cint(position)

	due = []
	for checkpoint_key, entry in (key.get("checkpoints") or {}).items():
		if block_key and entry.get("block_key") != block_key:
			continue
		if cint(entry.get("at")) > position:
			continue
		answered = state.get(checkpoint_key) or {}
		if cint(answered.get("correct")):
			continue
		if cint(answered.get("attempts")) >= (cint(entry.get("max_attempts")) or 1):
			continue
		due.append((cint(entry.get("at")), checkpoint_key, entry))

	if not due:
		return None

	at, checkpoint_key, entry = sorted(due)[0]
	return _public_checkpoint(checkpoint_key, entry, cint((state.get(checkpoint_key) or {}).get("attempts")))


def grade_checkpoint(attempt, checkpoint_key, option_keys):
	"""Grade one in-video checkpoint and say where the video resumes.

	Refuses outright when the learner's stored watch intervals do not cover the
	checkpoint's timestamp. Without that check the checkpoints are farmable: seek
	to the end, collect every question, answer them at leisure. The intervals are
	the record of what was genuinely played, so they are the thing to test against.
	"""
	doc = _attempt(attempt)
	lesson_name, lesson_key = _lesson_of_checkpoint(doc, checkpoint_key)
	key = answer_key(lesson_name)
	entry = (key.get("checkpoints") or {}).get(checkpoint_key)
	if not entry:
		frappe.throw(_("That question is not part of this lesson."))

	at = cint(entry.get("at"))
	lesson_state = _lesson_progress(doc, lesson_key)
	intervals = ((lesson_state.get("blocks") or {}).get(entry.get("block_key")) or {}).get("iv") or []
	if not _covers(intervals, at):
		frappe.throw(
			_("This question comes from a part of the video that has not been played yet. "
			  "Watch up to {0} and it will be asked again.").format(_mmss(at))
		)

	state = (lesson_state.get("checkpoints") or {}).get(checkpoint_key) or {}
	attempts = cint(state.get("attempts"))
	max_attempts = cint(entry.get("max_attempts")) or 1

	if cint(state.get("correct")):
		# Already answered correctly. Re-grading would let a learner reset their
		# attempt count by answering it again.
		return _checkpoint_result(checkpoint_key, entry, True, attempts, exhausted=False)

	if attempts >= max_attempts:
		return _checkpoint_result(checkpoint_key, entry, False, attempts, exhausted=True)

	correct = sorted(entry.get("correct") or [])
	correct_now = sorted(_as_keys(option_keys)) == correct
	attempts += 1
	_record_checkpoint(doc, lesson_key, checkpoint_key, correct_now, attempts)
	_file_checkpoint_answer(doc, lesson_name, checkpoint_key, entry, option_keys, correct_now, attempts)

	return _checkpoint_result(
		checkpoint_key, entry, correct_now, attempts, exhausted=attempts >= max_attempts
	)


# ----------------------------------------------------------------------- gates


def evaluate_gates(attempt, lesson_key):
	"""Decide whether a lesson may be marked complete, and say why not.

	The reasons are written to be read by the learner, with the numbers in them. A
	learner told only "not complete" cannot act on it and files a support ticket
	instead; "You have watched 62% of Site Safety; 80% is needed" is self-serve.

	Read-only by design. Waivers come back in ``waived`` for the caller to persist
	onto the progress row — this runs on the completion path and can run on every
	heartbeat, and a write here is a write in a loop.
	"""
	doc = _attempt(attempt)
	lesson_name = _lesson_name(doc, lesson_key)
	public = public_lesson(lesson_name)
	key = answer_key(lesson_name)
	policy = _course_policy(lesson_name)
	state = _lesson_progress(doc, lesson_key)
	blocks_state = state.get("blocks") or {}

	reasons = []
	waived = []
	gated_blocks = []
	coverages = []

	for block in public.get("blocks") or []:
		block_key = block.get("block_key")
		if not cint(block.get("required")):
			continue
		if block.get("type") == EXTERNAL_EMBED_BLOCK_TYPE:
			# The trap this waiver exists for: a compliance course must not quietly
			# lose its teeth because somebody picked the convenient block type.
			waived.append({"block_key": block_key, "reason": WAIVED_EXTERNAL_EMBED})
			continue
		if block.get("type") != GATEABLE_BLOCK_TYPE:
			continue

		duration = cint(block.get("duration_s"))
		if not duration or not _duration_is_verified(block.get("video_asset")):
			# Coverage is a fraction of duration, so an unverified duration turns
			# the gate into arithmetic on a guess. Waive rather than gate on it.
			waived.append({"block_key": block_key, "reason": WAIVED_DURATION_UNVERIFIED})
			continue

		required_pct = cint(block.get("min_coverage")) or policy["min_coverage"]
		watched = blocks_state.get(block_key) or {}
		actual_pct = _coverage_percent(watched, duration)
		coverages.append(actual_pct)
		gated_blocks.append(block_key)

		if actual_pct + 0.5 < required_pct:
			reasons.append(
				_("You have watched {0}% of {1}; {2}% is needed.").format(
					int(actual_pct), block.get("heading") or _("the video"), required_pct
				)
			)

	if policy["require_checkpoints"]:
		outstanding = _unanswered_checkpoints(key, state, gated_blocks)
		if outstanding:
			reasons.append(
				_("{0} in-video question(s) are still unanswered.").format(outstanding)
			)

	quiz = public.get("quiz") or {}
	if cint(quiz.get("enabled")) and (quiz.get("questions") or []):
		pass_score = cint(quiz.get("pass_score")) or policy["pass_score"]
		best = flt((state.get("quiz") or {}).get("best"))
		if best + 0.005 < pass_score:
			if not cint((state.get("quiz") or {}).get("runs")):
				reasons.append(_("The end-of-lesson quiz has not been taken yet."))
			else:
				reasons.append(
					_("Your best quiz score is {0}%; {1}% is needed to pass.").format(
						int(best), pass_score
					)
				)

	overall = flt(sum(coverages) / len(coverages), 2) if coverages else 0.0
	# `gated` distinguishes "there was no video here" from "watched none of it" —
	# both of which leave `coverage` at 0.0. Without it the completion record of a
	# text-only course reads as though the learner sat through nothing, which is
	# the same plausible-and-wrong failure as the score field.
	return {
		"ok": not reasons,
		"reasons": reasons,
		"coverage": overall,
		"gated": len(gated_blocks),
		"waived": waived,
	}


# --------------------------------------------------------------------- helpers


def _attempt(attempt):
	"""Accept either a Training Attempt name or its already-loaded document."""
	if isinstance(attempt, str):
		return frappe.get_doc("Training Attempt", attempt)
	return attempt


def _lesson_name(attempt, lesson_key):
	"""Resolve a lesson key inside *this attempt's* course version.

	Scoped to the version rather than looked up globally: lesson keys are stable
	across versions on purpose, so an unscoped lookup would happily grade a learner
	on a lesson from a version they never saw.
	"""
	version = getattr(attempt, "course_version", None) or ""
	if not version:
		frappe.throw(_("This attempt is not tied to a published course version."))
	name = frappe.db.get_value(
		"Training Lesson", {"course_version": version, "lesson_key": lesson_key}, "name"
	)
	if not name:
		frappe.throw(_("That lesson is not part of this course."))
	return name


def _lesson_of_checkpoint(attempt, checkpoint_key):
	"""``(lesson_name, lesson_key)`` for a checkpoint, scoped to the attempt's version."""
	version = getattr(attempt, "course_version", None) or ""
	if not version:
		frappe.throw(_("This attempt is not tied to a published course version."))
	lesson_name = frappe.db.get_value(
		"Training Checkpoint",
		{"checkpoint_key": checkpoint_key, "course_version": version},
		"lesson",
	)
	if not lesson_name:
		frappe.throw(_("That question is not part of this course."))
	lesson_key = frappe.db.get_value("Training Lesson", lesson_name, "lesson_key")
	return lesson_name, lesson_key


def _rng(attempt, lesson_key, run):
	"""Deterministic shuffler for one (attempt, lesson, run).

	Falls back to the attempt name when ``shuffle_seed`` is empty. A constant
	fallback would give every learner on the site the same question order, which is
	the single failure this seed exists to prevent.
	"""
	seed_source = getattr(attempt, "shuffle_seed", None) or getattr(attempt, "name", "") or ""
	digest = hashlib.sha256(f"{seed_source}|{lesson_key}|{cint(run)}".encode()).hexdigest()
	return random.Random(int(digest[:16], 16))


def _as_keys(value):
	"""Normalise a submitted answer into a list of option keys."""
	if value is None:
		return []
	if isinstance(value, str):
		parsed = frappe.parse_json(value) if value.strip().startswith("[") else value
		return [str(v) for v in parsed] if isinstance(parsed, list) else [parsed]
	if isinstance(value, (list, tuple, set)):
		return [str(v) for v in value]
	return [str(value)]


def _normalise_text(value):
	"""Trim, lowercase and collapse internal whitespace.

	The accepted answers were lowercased and stripped at publish, but a learner
	typing "safety  goggles" with two spaces is not wrong, and neither is one who
	pastes a trailing newline.
	"""
	return " ".join(str(value or "").strip().lower().split())


def _matches_key(entry, submitted):
	"""Whether one submitted answer matches the key. The only correctness test."""
	if entry.get("type") == "Short Answer":
		accepted = {_normalise_text(text) for text in entry.get("accepted_text") or []}
		if isinstance(submitted, (list, tuple)):
			submitted = submitted[0] if submitted else ""
		answer = _normalise_text(submitted)
		return bool(answer) and answer in accepted

	# Choice questions: the exact set. A Multiple Choice answer that is a subset of
	# the correct options is not partially right — it is wrong, and treating it
	# otherwise makes "tick everything" a winning strategy.
	return sorted(set(_as_keys(submitted))) == sorted(set(entry.get("correct") or []))


def _covers(intervals, at, tolerance=CHECKPOINT_TOLERANCE_SECONDS):
	for interval in intervals or []:
		if len(interval) != 2:
			continue
		start, end = cint(interval[0]), cint(interval[1])
		if start - tolerance <= at <= end + tolerance:
			return True
	return False


def _public_checkpoint(checkpoint_key, entry, attempts):
	"""The answer-free view of one checkpoint. Rebuilt field by field."""
	return {
		"checkpoint_key": checkpoint_key,
		"block_key": entry.get("block_key"),
		"at": cint(entry.get("at")),
		"type": entry.get("type"),
		"question": entry.get("question"),
		"options": [
			{"option_key": option.get("option_key"), "text": option.get("text")}
			for option in entry.get("options") or []
		],
		"pause": cint(entry.get("pause")),
		"allow_skip": cint(entry.get("allow_skip")),
		"max_attempts": cint(entry.get("max_attempts")) or 1,
		"attempts_used": cint(attempts),
	}


def _checkpoint_result(checkpoint_key, entry, correct, attempts, exhausted):
	"""The verdict, the resume position, and whether the video rewinds.

	A rewind only applies to a wrong answer that still has an attempt left —
	rewinding into an exhausted checkpoint just replays footage the learner is
	about to be moved past anyway.
	"""
	at = cint(entry.get("at"))
	rewind = cint(entry.get("rewind"))
	rewind_applies = bool(not correct and not exhausted and rewind > 0)
	return {
		"checkpoint_key": checkpoint_key,
		"correct": bool(correct),
		"exhausted": bool(exhausted),
		"attempts": cint(attempts),
		"max_attempts": cint(entry.get("max_attempts")) or 1,
		"allow_skip": cint(entry.get("allow_skip")),
		# Shown once the answer is in — that is the teaching moment. Which option
		# was right is still never said, because the checkpoint may be retried.
		"explanation": entry.get("explanation") or "",
		"rewind_applied": rewind_applies,
		"resume_at": max(0, at - rewind) if rewind_applies else at,
	}


def _mmss(seconds):
	seconds = max(0, cint(seconds))
	return f"{seconds // 60}:{seconds % 60:02d}"


def _duration_is_verified(video_asset):
	"""True only when the duration was probed from the source.

	A hand-typed 600 against a real 900-second video passes an 80% gate on 53% of
	an actual watch, which is worse than no gate because it reads as one.
	"""
	if not video_asset:
		return False
	return frappe.db.get_value("Training Video Asset", video_asset, "duration_source") == "Probed"


def _coverage_percent(block_state, duration):
	"""Coverage for one block as 0..100, recomputed from the intervals when present.

	The stored ``cov`` is a cache written by the heartbeat; the intervals are the
	evidence. Prefer the evidence, so a stale or hand-edited ``cov`` cannot pass a
	gate on its own.
	"""
	intervals = block_state.get("iv") or []
	if intervals:
		return flt(100.0 * _progress().coverage(intervals, duration), 2)
	return flt(100.0 * flt(block_state.get("cov")), 2)


def _unanswered_checkpoints(key, lesson_state, gated_blocks):
	"""How many required checkpoints are still open.

	Skippable checkpoints do not count: the author declared them optional, and a
	gate that ignores that turns ``allow_skip`` into a lie. Checkpoints on a block
	whose coverage gate was waived do not count either — the learner was never
	measured against that block at all.
	"""
	answered = lesson_state.get("checkpoints") or {}
	outstanding = 0
	for checkpoint_key, entry in (key.get("checkpoints") or {}).items():
		if entry.get("block_key") not in gated_blocks:
			continue
		if cint(entry.get("allow_skip")):
			continue
		if not cint((answered.get(checkpoint_key) or {}).get("answered")):
			outstanding += 1
	return outstanding


# -------------------------------------------------------------------- policy


def _course_policy(lesson_name):
	"""Gate thresholds for the course this lesson belongs to, with fallbacks."""
	course = frappe.db.get_value("Training Lesson", lesson_name, "course")
	row = (
		frappe.db.get_value(
			"Training Course",
			course,
			["min_video_coverage", "passing_score", "require_checkpoints_answered"],
			as_dict=True,
		)
		if course
		else None
	) or {}
	return {
		"min_coverage": cint(row.get("min_video_coverage"))
		or _setting("default_min_video_coverage", FALLBACK_MIN_COVERAGE),
		"pass_score": cint(row.get("passing_score"))
		or _setting("default_passing_score", FALLBACK_PASS_SCORE),
		"require_checkpoints": cint(row.get("require_checkpoints_answered")),
	}


def _setting(fieldname, fallback):
	"""A Training Settings default, or ``fallback`` on a fresh or broken site."""
	try:
		from erpnext_enhancements.training.doctype.training_settings.training_settings import (
			get_settings,
		)

		return cint(getattr(get_settings(), fieldname, 0)) or fallback
	except Exception:
		return fallback


# ------------------------------------------------------------------- progress


def _progress():
	"""Late import of the progress store.

	Imported at call time rather than at module scope so that grading — which is
	pure arithmetic over two JSON blobs — stays importable in contexts where the
	attempt runtime is not loaded, and so the two modules can never deadlock into a
	circular import as the runtime grows.
	"""
	return importlib.import_module("erpnext_enhancements.training.progress")


def _lesson_progress(attempt, lesson_key):
	"""This attempt's stored progress for one lesson. Never ``None``."""
	data = _progress().load(getattr(attempt, "name", attempt)) or {}
	return (data.get("lessons") or {}).get(lesson_key) or {}


def _current_run(attempt, lesson_key):
	"""The in-flight quiz run number, 1-based.

	``progress["quiz"]["runs"]`` counts runs *completed* — it is incremented when a
	run is graded, not when it is started — so the run now in flight is one past it.
	This has to agree exactly with what ``api/training.py::get_quiz`` passed to
	:func:`draw_quiz`, which is also ``runs + 1``: the two numbers seed the same
	shuffle, and if they disagree the learner is graded against a different draw
	from the one they were shown. Nothing about that failure looks like an
	off-by-one from the outside; it looks like the quiz marking correct answers
	wrong at random on every retake.
	"""
	return cint((_lesson_progress(attempt, lesson_key).get("quiz") or {}).get("runs")) + 1


def _record_quiz_run(attempt, lesson_key, run, score):
	"""Persist the run count and the best score. Forced: this is a state change."""
	name = getattr(attempt, "name", attempt)
	progress = _progress()
	data = progress.load(name) or {}
	lesson = data.setdefault("lessons", {}).setdefault(lesson_key, {})
	quiz = lesson.setdefault("quiz", {})
	quiz["runs"] = max(cint(quiz.get("runs")), cint(run))
	quiz["best"] = max(flt(quiz.get("best")), flt(score))
	progress.save(name, data, force=True)


def _record_checkpoint(attempt, lesson_key, checkpoint_key, correct, attempts):
	"""Persist one checkpoint answer.

	Written here rather than by the endpoint because the attempt count is what
	enforces ``max_attempts``, and a counter the caller is trusted to increment is
	a counter that can be skipped by calling a different endpoint.
	"""
	name = getattr(attempt, "name", attempt)
	progress = _progress()
	data = progress.load(name) or {}
	lesson = data.setdefault("lessons", {}).setdefault(lesson_key, {})
	entry = lesson.setdefault("checkpoints", {}).setdefault(checkpoint_key, {})
	entry["answered"] = 1
	entry["attempts"] = cint(attempts)
	entry["correct"] = 1 if correct else cint(entry.get("correct"))
	entry["at"] = str(now_datetime())
	progress.save(name, data, force=True)


# --------------------------------------------------------------- answer filing
#
# ``Training Attempt Question`` is a flat row per graded answer, and the reason it
# exists is the analytics report: a question everybody misses usually means the
# content is unclear, not that the learners are. That only works if the rows get
# written, and for four phases they did not — the report queried a table nothing
# filled, and answered every question with an empty set rather than an error.
#
# Filing happens here because this is the one place that holds both halves: the
# drawn options (so the row can record *what the learner picked*, in words) and
# the verdict. It must never cost a learner their submission, so a failure is
# logged and swallowed — logged loudly, because a silent except is how the
# missing rows went unnoticed to begin with.


def _answer_in_words(entry_or_question, submitted):
	"""What the learner picked, as the text they saw.

	The option *keys* are stable across learners, so storing those would group
	correctly — but the report's most useful column names the dominant distractor
	("half the misses picked B"), and ``opt_3`` is not a sentence an author can act
	on. Text is stored so the column reads.
	"""
	if (entry_or_question or {}).get("type") == "Short Answer":
		if isinstance(submitted, list | tuple):
			submitted = submitted[0] if submitted else ""
		return str(submitted or "")[:500]

	by_key = {
		str(option.get("option_key")): (option.get("text") or "")
		for option in (entry_or_question or {}).get("options") or []
	}
	# Sorted so the same pair of ticked options is one bucket in the report rather
	# than two, whatever order the client happened to send them in.
	picked = [by_key.get(str(key)) or str(key) for key in sorted(_as_keys(submitted))]
	return " | ".join(text for text in picked if text)[:500]


def _file_answer(row):
	"""Insert one row. The caller has already decided it should exist."""
	doc = frappe.get_doc(dict(row, doctype="Training Attempt Question"))
	# The learner is typically a Website User with no DocPerm here at all — that is
	# the design (see training/permissions.py), so filing is necessarily a
	# server-side act. The controller still re-derives course/version/user from the
	# attempt, so nothing filed here can land under the wrong name.
	doc.insert(ignore_permissions=True)


def _file_quiz_answers(attempt, lesson_name, run, drawn, submitted, per_question):
	"""One row per question in a graded quiz run."""
	try:
		by_name = {question.get("question"): question for question in drawn or []}
		# A retake is a new run and genuinely new data. A re-grade of the *same*
		# run is not, and double-filing it would quietly weight one learner twice
		# in every percentage the report prints.
		already = set(
			frappe.get_all(
				"Training Attempt Question",
				filters={"attempt": attempt.name, "lesson": lesson_name, "quiz_run": cint(run),
				         "source": "Quiz"},
				pluck="question",
			)
		)
		for result in per_question or []:
			name = result.get("question")
			if not name or name in already:
				continue
			_file_answer(
				{
					"attempt": attempt.name,
					"lesson": lesson_name,
					"source": "Quiz",
					"question": name,
					"quiz_run": cint(run),
					"question_text_snapshot": (result.get("text") or "")[:1000],
					"given_answer": _answer_in_words(by_name.get(name), (submitted or {}).get(name)),
					"is_correct": 1 if result.get("correct") else 0,
					"points_awarded": flt(result.get("awarded")),
					# time_taken_seconds is deliberately left unset: nothing measures
					# per-question time yet, and dividing the run's duration by the
					# question count would invent a number the report acts on. It
					# guards on truthiness, so unset reads as "unknown", not "0s".
				}
			)
	except Exception:
		frappe.log_error(
			f"Could not file quiz answers for attempt {getattr(attempt, 'name', attempt)} "
			f"lesson {lesson_name} run {run}. The learner's submission was graded and saved; "
			"only the per-question analytics rows are missing.",
			"Training analytics",
		)


def _file_checkpoint_answer(attempt, lesson_name, checkpoint_key, entry, option_keys, correct, attempts):
	"""One row per in-video checkpoint answer.

	Every attempt at a checkpoint is filed, not just the final one. A checkpoint
	answered wrong twice and right on the third go is exactly the signal the report
	exists to surface, and keeping only the last answer would render it a pass.
	``quiz_run`` carries the attempt number so the rows stay distinguishable.
	"""
	try:
		_file_answer(
			{
				"attempt": attempt.name,
				"lesson": lesson_name,
				"source": "Checkpoint",
				"checkpoint": entry.get("checkpoint"),
				"quiz_run": cint(attempts),
				"question_text_snapshot": (entry.get("question") or "")[:1000],
				"given_answer": _answer_in_words(entry, option_keys),
				"is_correct": 1 if correct else 0,
				"points_awarded": 1.0 if (correct and cint(entry.get("scored"))) else 0.0,
			}
		)
	except Exception:
		frappe.log_error(
			f"Could not file checkpoint {checkpoint_key} for attempt "
			f"{getattr(attempt, 'name', attempt)}. The answer was graded and the learner's "
			"progress saved; only the analytics row is missing.",
			"Training analytics",
		)
