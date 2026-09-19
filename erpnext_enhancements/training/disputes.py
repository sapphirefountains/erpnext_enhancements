# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Disputing a graded answer — the human half of AI grading.

As of v1.490.0 a Short Answer is marked by an AI with **no human sign-off in
front of it** (Nik, 2026-09-19; ADR 0015). This module is the thing that makes
that defensible: the machine decides, and the person it decided about can say it
got that wrong, and a named Training Manager then rules.

Three things happen here and they are deliberately in one module, because they
are one story:

* :func:`raise_dispute` — a learner presses "I think this was right" on the
  review screen. Snapshots everything and opens a queue row. Costs them nothing
  and changes nothing.
* :func:`resolve_dispute` — a Training Manager upholds or rejects. **Upholding
  finishes the job**: the answer is re-marked, the run is re-scored, and the
  attempt is re-driven through the ordinary completion path, so a correction can
  issue the Training Completion and the certificate exactly as passing first time
  would have. That was the explicit product choice over "record the correction
  and let somebody do the rest by hand", on the grounds that a half-corrected
  pass which never issues the certificate is its own support ticket.
* :func:`reset_quiz_attempts` — the thing ``api/training.py`` has been promising
  learners since the module shipped. On running out of attempts it says "A
  Training Manager can reset it for you", and until now **no reset existed
  anywhere in the app** — a dead end at the exact moment a learner is locked out.

Two design notes worth keeping:

**The re-drive rides an existing path, not a new one.**
``api.training._evaluate_attempt`` was already split out to be re-drivable — a
supervisor's sign-off re-drives it from a ``doc_event`` with no session learner —
so upholding a dispute reuses the gate evaluation, the completion, the
certificate and the assignment close rather than reimplementing any of them. A
second implementation of "what a pass does" is how two of them disagree.

**Nothing here has to un-fail an attempt, because nothing ever fails one.**
``Training Attempt.status`` offers ``Failed`` and no code path in the app writes
it: an attempt that has not passed is still ``In Progress``, and
``_evaluate_attempt`` re-runs on it cleanly. If that ever changes, this module
needs a status transition and this paragraph needs deleting.

Indentation is tabs, matching the rest of ``training/``.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from erpnext_enhancements.training import grading

MANAGER_ROLES = ("Training Manager", "System Manager")


def _require_manager():
	if not set(MANAGER_ROLES) & set(frappe.get_roles()):
		frappe.throw(_("Only a Training Manager can do that."), frappe.PermissionError)


def _answer_row(attempt, lesson_key, quiz_run, question):
	"""Find the filed answer being disputed, refused unless it is the caller's.

	Addressed by *what the learner was asked* rather than by the answer row's own
	name, because the review screen does not know that name: ``per_question`` is
	built and returned before ``_file_quiz_answers`` runs, so the row does not
	exist yet when the payload the learner is looking at is assembled.

	Ownership is checked against the ATTEMPT rather than through DocPerm. A
	learner is typically a Website User holding no DocPerm on these doctypes at
	all — that is the module's design, see ``training/permissions.py`` — so the
	test has to be explicit here or it does not happen.
	"""
	doc = frappe.get_doc("Training Attempt", attempt)
	is_manager = bool(set(MANAGER_ROLES) & set(frappe.get_roles()))
	if doc.user != frappe.session.user and not is_manager:
		frappe.throw(_("That is not your attempt."), frappe.PermissionError)

	lesson = grading._lesson_name(doc, lesson_key)
	rows = frappe.get_all(
		"Training Attempt Question",
		filters={
			"attempt": doc.name,
			"lesson": lesson,
			"quiz_run": cint(quiz_run),
			"question": question,
			"source": "Quiz",
		},
		pluck="name",
		limit=1,
	)
	if not rows:
		# `_file_quiz_answers` is best-effort and logs rather than throwing, so a
		# missing row is a real possibility rather than a theoretical one. Say so
		# plainly instead of failing as "not found", which reads as "you did not
		# answer that".
		frappe.throw(
			_("We cannot find the record of that answer, so it cannot be disputed here. "
			  "Tell a Training Manager which question it was and they can look at it directly.")
		)
	return frappe.get_doc("Training Attempt Question", rows[0])


# ----------------------------------------------------------------- raising


@frappe.whitelist(methods=["POST"])
def raise_dispute(attempt, lesson_key, quiz_run, question, note=None):
	"""Open a dispute against one graded answer. Returns the dispute name.

	Idempotent by design: a learner who presses the button twice, or reloads the
	review screen and presses it again, gets the dispute they already have rather
	than an error or a second queue row. Two rows against one answer is how two
	managers rule opposite ways on the same fact.
	"""
	row = _answer_row(attempt, lesson_key, quiz_run, question)

	existing = frappe.get_all(
		"Training Answer Dispute",
		filters={"attempt_question": row.name, "status": "Open"},
		pluck="name",
		limit=1,
	)
	if existing:
		return {"dispute": existing[0], "created": False}

	if cint(row.is_correct):
		# Nothing to dispute, and allowing it would put a row in the queue that a
		# manager can only close as Rejected while agreeing with the learner.
		frappe.throw(_("That answer was already marked correct."))

	dispute = frappe.get_doc(
		{
			"doctype": "Training Answer Dispute",
			"attempt_question": row.name,
			"attempt": row.attempt,
			"course": row.course,
			"lesson": row.lesson,
			"user": row.user,
			"question": row.question,
			"status": "Open",
			"raised_on": now_datetime(),
			"question_text": row.question_text_snapshot,
			"given_answer": row.given_answer,
			# Snapshotted from the PUBLISHED key, not from the live question: a new
			# draft version can rewrite the accepted answers freely, and the
			# reviewer has to see what this learner was actually marked against.
			"accepted_answers": "\n".join(_accepted_for(row)),
			"ai_reasoning": getattr(row, "ai_reasoning", None) or "",
			"learner_note": (note or "")[:1000],
		}
	)
	# The learner holds `read` on this doctype and not `create` -- raising one is
	# an act the server performs on their behalf, after the ownership check above.
	dispute.insert(ignore_permissions=True)
	return {"dispute": dispute.name, "created": True}


def _accepted_for(row):
	"""The accepted answers this answer was graded against, from the published key.

	Best-effort: a dispute on an answer whose version has since been retired is
	still worth raising, and an empty list reads honestly on the form as "not
	recorded" rather than as "there were none".
	"""
	try:
		key = grading.answer_key(row.lesson) or {}
		entry = (key.get("quiz") or {}).get(row.question) or {}
		return [str(text) for text in (entry.get("accepted_text") or [])]
	except Exception:
		return []


# --------------------------------------------------------------- resolving


@frappe.whitelist(methods=["POST"])
def resolve_dispute(dispute, outcome, note=None):
	"""Rule on a dispute. ``outcome`` is ``Upheld`` or ``Rejected``.

	Upholding re-marks the answer, re-scores the run and re-drives the attempt.
	Rejecting records the reasoning and changes nothing — but the reasoning is
	still required, because it is what the learner is shown, and "no" with no
	explanation is what made them dispute in the first place.
	"""
	_require_manager()
	if outcome not in ("Upheld", "Rejected"):
		frappe.throw(_("An outcome is either Upheld or Rejected."))

	doc = frappe.get_doc("Training Answer Dispute", dispute)
	if doc.status != "Open":
		frappe.throw(_("{0} was already resolved as {1}.").format(doc.name, doc.status))

	note = (note or "").strip()
	if not note:
		frappe.throw(
			_("Say why. The learner is shown this, and an unexplained ruling is what "
			  "they disputed in the first place.")
		)

	detail = _uphold(doc) if outcome == "Upheld" else ""

	doc.status = outcome
	doc.reviewed_by = frappe.session.user
	doc.reviewed_on = now_datetime()
	doc.reviewer_note = note[:1000]
	doc.outcome_detail = detail[:1000]
	doc.save(ignore_permissions=True)
	return {"dispute": doc.name, "status": doc.status, "outcome": detail}


def _uphold(dispute):
	"""Re-mark the answer, re-score its run, re-drive the attempt.

	Returns a sentence describing what actually happened, which is written to
	``outcome_detail`` — the record should say what was done rather than what was
	intended, because these three steps can each be a no-op for good reasons.
	"""
	row = frappe.get_doc("Training Attempt Question", dispute.attempt_question)
	steps = []

	if not cint(row.is_correct):
		row.db_set(
			{
				"is_correct": 1,
				"points_awarded": flt(_points_for(row)),
				"corrected_by": frappe.session.user,
				"corrected_on": now_datetime(),
			},
			update_modified=True,
		)
		steps.append(_("answer re-marked correct"))

	rescored = _rescore_run(row)
	if rescored is not None:
		steps.append(_("run re-scored to {0}%").format(rescored))

	passed = _redrive(row.attempt)
	steps.append(_("attempt now passes") if passed else _("attempt still has outstanding gates"))
	return "; ".join(steps)


def _points_for(row):
	"""What the question was worth in the run it was asked in.

	Read back off the published key rather than assumed to be 1 — a pool can
	weight a question, and awarding 1 point for a 3-point question turns a
	correction into a smaller correction.
	"""
	try:
		key = grading.answer_key(row.lesson) or {}
		entry = (key.get("quiz") or {}).get(row.question) or {}
		return cint(entry.get("points")) or 1
	except Exception:
		return 1


def _rescore_run(row):
	"""Recompute the disputed run's percentage and record it. ``None`` if it could not.

	The drawn set is re-derived with :func:`grading.draw_quiz`, which is seeded by
	attempt, lesson and run and therefore gives back exactly the questions this
	learner was asked. The verdicts come from the filed answer rows, which now
	include the correction.

	``grading._record_quiz_run`` keeps the **best** score (``max``), which is
	exactly right here and worth stating: a correction can only raise a run's
	score, so recording it can only raise the learner's best — it can never take
	away a pass they already had on another run.
	"""
	try:
		attempt = frappe.get_doc("Training Attempt", row.attempt)
		lesson_key = frappe.db.get_value("Training Lesson", row.lesson, "lesson_key")
		run = cint(row.quiz_run)
		if not lesson_key or not run:
			return None

		drawn = grading.draw_quiz(attempt, lesson_key, run)
		if not drawn:
			return None
		key = grading.answer_key(row.lesson) or {}
		points_by_question = {}
		for question in drawn:
			name = question.get("question")
			entry = (key.get("quiz") or {}).get(name) or {}
			points_by_question[name] = cint(entry.get("points")) or cint(question.get("points")) or 1

		verdicts = {
			filed["question"]: cint(filed["is_correct"])
			for filed in frappe.get_all(
				"Training Attempt Question",
				filters={"attempt": attempt.name, "lesson": row.lesson, "quiz_run": run, "source": "Quiz"},
				fields=["question", "is_correct"],
			)
		}

		possible = sum(points_by_question.values())
		earned = sum(points for name, points in points_by_question.items() if verdicts.get(name))
		if not possible:
			return None
		score = flt(100.0 * earned / possible, 2)
		grading._record_quiz_run(attempt, lesson_key, run, score)
		return score
	except Exception:
		frappe.log_error(
			f"Could not re-score run for disputed answer {row.name}. The answer was corrected; "
			"the lesson's best score was not updated, so the attempt may still show as short.",
			"Training disputes",
		)
		return None


def _redrive(attempt):
	"""Re-run every gate on the attempt, issuing the completion if they now pass.

	Reuses ``api.training._evaluate_attempt``, the same function a supervisor's
	sign-off re-drives, so a corrected pass produces the identical Training
	Completion, certificate and assignment close that passing first time would.

	Imported inside the function: ``api.training`` is a large learner-runtime
	module and importing it at the top of this one would make a Desk save of a
	dispute pull the whole runtime in.
	"""
	try:
		from erpnext_enhancements.api import training

		doc = frappe.get_doc("Training Attempt", attempt)
		result = training._evaluate_attempt(doc) or {}
		return bool(result.get("passed"))
	except Exception:
		frappe.log_error(
			f"Could not re-drive attempt {attempt} after upholding a dispute. The answer was "
			"corrected and the run re-scored; the learner may need to press Finish again.",
			"Training disputes",
		)
		return False


# ------------------------------------------------------------ attempt reset


@frappe.whitelist(methods=["POST"])
def reset_quiz_attempts(attempt, lesson_key, reason=None):
	"""Give a learner their quiz attempts back on one lesson.

	**This is the function the app has been telling learners about since the
	module shipped.** ``start_quiz`` refuses a draw past ``max_attempts`` with
	"A Training Manager can reset it for you", and until v1.490.0 no such thing
	existed anywhere in the app — the sentence was a dead end at the exact moment
	a learner was locked out of a course they had been assigned.

	Resets the run COUNT only. The best score stays: a reset is "have another go",
	not "lose what you earned", and zeroing the best could take away a pass the
	learner already holds on an earlier run.
	"""
	_require_manager()
	doc = frappe.get_doc("Training Attempt", attempt)

	progress = grading._progress()
	data = progress.load(doc.name) or {}
	quiz = data.setdefault("lessons", {}).setdefault(lesson_key, {}).setdefault("quiz", {})
	before = cint(quiz.get("runs"))
	if not before:
		return {"reset": False, "runs_before": 0}

	quiz["runs"] = 0
	progress.save(doc.name, data, force=True)

	# On the attempt, because that is the document somebody looks at when they ask
	# why this learner has had six goes at a three-attempt quiz.
	doc.add_comment(
		"Comment",
		_("Quiz attempts on {0} reset from {1} to 0 by {2}.{3}").format(
			lesson_key, before, frappe.session.user, f" {reason}" if reason else ""
		),
	)
	return {"reset": True, "runs_before": before}
