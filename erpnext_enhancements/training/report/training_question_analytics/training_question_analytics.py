# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Question Analytics — which questions are teaching, and which are just failing people.

**Read this report as a review of the content, not of the learners.** That is not
a nicety; it is the only reading that produces an action. A question 20% of people
get right is not a hard question — a hard question sits around 60%. Below about
40% the usual causes, in order of how often they turn out to be true, are: the
lesson never actually says the answer; the wording is ambiguous; two options are
both defensible; the correct option is wrong. All four are the author's to fix,
and none of them is fixed by retraining anybody.

The column labels are written to make that reading unavoidable — "Reads As" spells
out the conclusion rather than leaving a percentage for somebody to interpret
optimistically.

Three numbers, and each is useless without the other two:

* **Correct %** — the headline, and on its own it cannot tell a badly worded
  question from a genuinely difficult one.
* **Most-Chosen Wrong Answer** and the share taking it — this is what separates
  them. Misses spread evenly across the distractors means people are guessing:
  the content did not land. Misses piled on *one* distractor means that option is
  competing with the right answer, which is usually a wording problem and
  occasionally means the answer key is wrong.
* **Avg Time** — a question everybody gets right in three seconds is not testing
  anything. One that takes ninety and is still missed is being puzzled over.

Both the quiz and the in-video checkpoints are in scope, filtered by ``Source``.
Checkpoints are where a video's own clarity shows up: a checkpoint everybody
misses points at the sixty seconds of footage before it.

Source is ``Training Attempt Question``, one flat row per answer — see that
doctype's controller for why it is not a child table. Correct answers are *not*
read here and never leave ``training/grading.py``; this report only ever sees the
verdict that was already recorded and what the learner picked.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

# Below this many answers a percentage is noise. Reported anyway — an author needs
# to see that a new question exists — but the reading column says so rather than
# handing over a confident-looking 33%.
DEFAULT_MIN_ANSWERS = 5

# Widely-used thresholds for item analysis, and they are conventions rather than
# laws. Tuned once here so every reading below agrees with every other.
STRUGGLING = 40.0
SHAKY = 60.0
TOO_EASY = 95.0
DOMINANT_DISTRACTOR = 30.0

ANSWER_DISPLAY_CHARS = 60


def execute(filters=None):
	filters = frappe._dict(filters or {})
	rows = _answer_rows(filters)
	if not rows:
		return _columns(), []

	minimum = cint(filters.get("min_answers") or DEFAULT_MIN_ANSWERS)
	buckets = {}
	for row in rows:
		key = row.question or row.checkpoint
		if not key:
			continue
		bucket = buckets.setdefault(key, _new_bucket(row))
		bucket["asked"] += 1
		bucket["correct"] += 1 if cint(row.is_correct) else 0
		if row.time_taken_seconds:
			bucket["seconds"].append(cint(row.time_taken_seconds))
		if not cint(row.is_correct):
			answer = _display_answer(row.given_answer)
			if answer:
				bucket["wrong"][answer] = bucket["wrong"].get(answer, 0) + 1

	data = [_finish(bucket, minimum) for bucket in buckets.values()]
	# Worst first. The point of the report is the questions that are not working.
	data.sort(key=lambda r: (r["correct_percent"], -r["asked"]))
	return _columns(), data


# ------------------------------------------------------------------ columns


def _columns():
	return [
		{"label": _("Question"), "fieldname": "question", "fieldtype": "Link",
		 "options": "Training Question", "width": 130},
		{"label": _("Asked"), "fieldname": "question_text", "fieldtype": "Data", "width": 300},
		{"label": _("Course"), "fieldname": "course", "fieldtype": "Link",
		 "options": "Training Course", "width": 130},
		{"label": _("Lesson"), "fieldname": "lesson", "fieldtype": "Link",
		 "options": "Training Lesson", "width": 130},
		{"label": _("Source"), "fieldname": "source", "fieldtype": "Data", "width": 90},
		{"label": _("Times Answered"), "fieldname": "asked", "fieldtype": "Int", "width": 120},
		{"label": _("Correct %"), "fieldname": "correct_percent", "fieldtype": "Percent", "width": 100},
		{"label": _("Avg Time (s)"), "fieldname": "avg_seconds", "fieldtype": "Float",
		 "precision": 1, "width": 110},
		{"label": _("Most-Chosen Wrong Answer"), "fieldname": "top_wrong_answer",
		 "fieldtype": "Data", "width": 240},
		{"label": _("% Of Misses On It"), "fieldname": "top_wrong_share", "fieldtype": "Percent",
		 "width": 130},
		# The conclusion, spelled out. See the module docstring: a bare percentage
		# gets read as a fact about the learners.
		{"label": _("Reads As"), "fieldname": "reading", "fieldtype": "Data", "width": 320},
	]


# --------------------------------------------------------------------- data


def _answer_rows(filters):
	conditions = {}
	if filters.get("course"):
		conditions["course"] = filters.course
	if filters.get("course_version"):
		conditions["course_version"] = filters.course_version
	if filters.get("lesson"):
		conditions["lesson"] = filters.lesson
	if filters.get("source") and filters.source != "All":
		conditions["source"] = filters.source
	if filters.get("from_date") and filters.get("to_date"):
		conditions["answered_on"] = ["between", [filters.from_date, filters.to_date]]
	elif filters.get("from_date"):
		conditions["answered_on"] = [">=", filters.from_date]
	elif filters.get("to_date"):
		conditions["answered_on"] = ["<=", filters.to_date]

	if cint(filters.get("current_version_only")) and not filters.get("course_version"):
		current = frappe.get_all(
			"Training Course",
			filters={"current_version": ["is", "set"]},
			pluck="current_version",
		)
		if not current:
			return []
		conditions["course_version"] = ["in", current]

	return frappe.get_all(
		"Training Attempt Question",
		filters=conditions,
		fields=[
			"question",
			"checkpoint",
			"course",
			"lesson",
			"source",
			"question_text_snapshot",
			"given_answer",
			"is_correct",
			"time_taken_seconds",
		],
		limit_page_length=0,
	)


def _new_bucket(row):
	return {
		"question": row.question,
		"checkpoint": row.checkpoint,
		"course": row.course,
		"lesson": row.lesson,
		"source": row.source,
		"question_text": _plain_text(row.question_text_snapshot),
		"asked": 0,
		"correct": 0,
		"seconds": [],
		"wrong": {},
	}


def _finish(bucket, minimum):
	asked = bucket["asked"]
	correct_percent = round(bucket["correct"] * 100.0 / asked, 1) if asked else 0.0
	misses = asked - bucket["correct"]

	top_answer, top_count = ("", 0)
	if bucket["wrong"]:
		top_answer, top_count = max(bucket["wrong"].items(), key=lambda pair: pair[1])
	# Share of the *misses*, not of everybody. "Half the people who got it wrong
	# picked B" is the sentence an author can act on; "12% of everyone picked B"
	# is not.
	top_share = round(top_count * 100.0 / misses, 1) if misses else 0.0

	avg_seconds = round(sum(bucket["seconds"]) / len(bucket["seconds"]), 1) if bucket["seconds"] else 0.0

	return {
		"question": bucket["question"],
		"question_text": bucket["question_text"] or bucket["checkpoint"] or "",
		"course": bucket["course"],
		"lesson": bucket["lesson"],
		"source": bucket["source"],
		"asked": asked,
		"correct_percent": correct_percent,
		"avg_seconds": avg_seconds,
		"top_wrong_answer": top_answer,
		"top_wrong_share": top_share,
		"reading": _reading(asked, correct_percent, top_share, avg_seconds, minimum),
	}


def _reading(asked, correct_percent, top_share, avg_seconds, minimum):
	"""The sentence an author should act on, in priority order.

	Ordered so the strongest signal wins: a dominant distractor is a more specific
	diagnosis than "everybody misses this", so it is checked first among the
	failing cases.
	"""
	if asked < minimum:
		return _("Too few answers yet to read anything into ({0}).").format(asked)
	if correct_percent < STRUGGLING and top_share >= DOMINANT_DISTRACTOR:
		return _("Almost nobody gets this, and the misses pile onto one option — check that option is not "
		         "also defensible, or that the key is right.")
	if correct_percent < STRUGGLING:
		return _("Almost nobody gets this. Misses are spread, so suspect the lesson never says the answer.")
	if correct_percent < SHAKY and top_share >= DOMINANT_DISTRACTOR:
		return _("One wrong option is competing with the right one — usually a wording problem.")
	if correct_percent < SHAKY:
		return _("Hard. Worth a look at whether the material covers it properly.")
	if correct_percent >= TOO_EASY and avg_seconds and avg_seconds < 5:
		return _("Answered right, fast, by everyone — it is not testing anything.")
	if correct_percent >= TOO_EASY:
		return _("Everyone gets this. Fine as a confidence builder; it is not a gate.")
	return _("Working as intended.")


# ------------------------------------------------------------------ display


def _plain_text(html):
	"""Question text as one readable line.

	``question_text`` is a Text Editor field, so the snapshot carries markup. Left
	as HTML it renders as tags in a data cell, and stripping it here rather than in
	the formatter keeps the exported spreadsheet readable too.
	"""
	if not html:
		return ""
	text = frappe.utils.strip_html(str(html)).replace("\n", " ").strip()
	return " ".join(text.split())


def _display_answer(given):
	"""What the learner picked, as one short string.

	``given_answer`` is free text for a short answer and a JSON list for multiple
	choice. Both are normalised so the two shapes group together instead of
	producing one bucket per formatting variant.
	"""
	if given is None:
		return ""
	raw = str(given).strip()
	if not raw:
		return ""
	if raw.startswith("[") or raw.startswith("{"):
		try:
			parsed = json.loads(raw)
		except ValueError:
			parsed = None
		if isinstance(parsed, list):
			raw = " + ".join(str(item) for item in sorted(parsed, key=str))
		elif isinstance(parsed, dict):
			raw = " + ".join(f"{k}={v}" for k, v in sorted(parsed.items()))
	raw = _plain_text(raw)
	if len(raw) > ANSWER_DISPLAY_CHARS:
		return raw[: ANSWER_DISPLAY_CHARS - 1] + "…"
	return raw
