# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Help — the words in front of you, explained, without leaving the lesson.

A learner reading about bedding a buried pipe meets *haunching*, *invert* and *thrust restraint* in
three consecutive paragraphs. Ask-the-author (``qa.py``) is the right tool for *"why does this work
like that"* and the wrong one for *"what does that word mean"*: it costs a round trip through a
human and an afternoon of waiting, for a question whose answer never changes.

**The glossary is shared and matched at read time.** ``Training Glossary Term`` holds one entry per
word; this module finds which of them actually occur in the lesson on screen and returns those.
Nothing is stored per lesson, so a term added today appears in every lesson that was already using
the word, and a lesson written tomorrow arrives already explained.

The quiz rule
-------------

Help stays available **during a quiz**, showing ``short_definition`` and nothing else. That is a
decision about what a quiz measures: a technician who can bed a pipe correctly but has never heard
the word *haunch* should not fail a question about haunching. Knowing the trade is what is being
assessed; knowing the vocabulary is what the lesson was for.

Two restrictions apply in quiz mode, and they are different in kind:

* **By field.** ``explanation`` and ``example`` are never assembled, so no version of the returned
  dict ever held them. A worked example good enough to be worth writing is usually good enough to
  answer a question about the thing it works through.
* **By pool.** Every term whose text occurs anywhere in **this lesson's whole quiz pool** — every
  stem, every option, not merely the question on screen — is withheld. A definition of *breakpoint*
  is the answer to "what is breakpoint chlorination?".

**The pool, not the drawn questions, and that is forced rather than chosen.**
``Training Attempt Question`` rows are written when an answer is *graded*, not when the quiz is
drawn, so mid-quiz the server cannot discover which questions this learner was given. The
alternative — letting the client name them — is not an alternative at all: a client that declares
its own suppression list can declare an empty one. The pool is a superset the server derives on its
own from the lesson, so no input from the browser decides what is withheld.

**Be honest about the ceiling.** ``in_quiz`` is asserted by the client, and a caller that simply
omits it gets the full payload. That is not a hole worth closing, because it is not the threat: a
learner who wants the answer can read the options in front of them, or open the lesson in a second
tab, long before they think to forge a flag. What this prevents is the accidental case — the panel
*handing* somebody the answer to the question they are staring at — and that is all it claims.
Label it that way anywhere it is described, the same way the module labels watch coverage as
"watched" rather than "paid attention".

What this module never touches
-------------------------------

``answer_key_json`` is opened by ``grading`` alone and leaves by ``grade_quiz`` alone; Help does not
read it, and does not read ``is_correct`` on any option. The only question data it reads is stems
and option text — the strings already on the learner's screen — and it reads them to take something
away rather than to send it. Nothing read for suppression is ever returned.

This is the one learner-facing payload in the module that ``_split_lesson`` does not assemble, and
it is allowed to exist because it is built from a different table entirely.

Indentation is tabs, matching the ``training/`` package.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.training.doctype.training_glossary_term.training_glossary_term import (
	TrainingGlossaryTerm,
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import runtime_ready

#: How many terms one panel will show. A lesson that legitimately uses forty glossary words is a
#: lesson whose Help panel is longer than the lesson, so the list is capped and the count of what
#: did not fit is returned rather than silently dropped.
MAX_TERMS = 24

#: Block fields whose text a term can be found in. ``data`` (the interactive-list JSON) is handled
#: separately because a Checklist's items are lesson content as much as a paragraph is.
TEXT_FIELDS = ("heading", "content", "caption")

#: The only fields this module will ever read off a question or its options, named as constants so
#: the boundary is something a test can hold rather than a habit.
#:
#: **Every one of these strings is already on the learner's screen** — the question they are being
#: asked and the options they are choosing between. Reading them back to decide what to withhold
#: discloses nothing they do not have, and none of it is returned.
#:
#: ``is_correct`` is absent and must stay absent.
QUESTION_FIELDS = ("question_text",)
OPTION_FIELDS = ("option_text",)


def _enabled_terms():
	"""Every glossary entry, with the fields Help can serve.

	One read of a small table per request rather than a cache, deliberately: an author who fixes a
	definition expects to see it on the next refresh, and a stale glossary is the kind of wrong that
	nobody reports because it looks like the entry was never written.
	"""
	return frappe.get_all(
		"Training Glossary Term",
		filters={"enabled": 1},
		fields=[
			"name",
			"term",
			"aliases",
			"short_definition",
			"trade_trap",
			"ordinary_meaning",
			"explanation",
			"example",
			"see_also",
			"ai_generated",
			"reviewed_by",
		],
	)


def _flatten(value):
	"""Every string inside an arbitrarily shaped bit of parsed JSON."""
	if isinstance(value, str):
		return value
	if isinstance(value, list):
		return "\n".join(_flatten(v) for v in value)
	if isinstance(value, dict):
		return "\n".join(_flatten(v) for v in value.values())
	return ""


def _lesson_text(lesson):
	"""Everything a learner can read in this lesson, as one string to match against.

	Built from the content blocks rather than ``published_content_json`` so the same call works on a
	draft — the authoring canvas wants to preview Help too, and a helper that only works after
	publication would be discovered useless at exactly the wrong moment.
	"""
	rows = frappe.get_all(
		"Training Content Block",
		filters={"parent": lesson, "parenttype": "Training Lesson"},
		fields=["heading", "content", "caption", "data"],
	)
	parts = []
	for row in rows:
		for field in TEXT_FIELDS:
			if row.get(field):
				parts.append(str(row[field]))
		if row.get("data"):
			# A malformed `data` column contributes nothing rather than raising: a Help panel that
			# will not open because one checklist has bad JSON is worse than one missing a word.
			try:
				parts.append(_flatten(json.loads(row["data"])))
			except (ValueError, TypeError):
				pass
	return "\n".join(p for p in parts if p)


def _pool_text(lesson):
	"""Every stem and option in this lesson's quiz pool, as one string to match against.

	The **pool**, not the questions this learner was drawn, and that is forced rather than chosen —
	see the module docstring. It is a superset, which is the safe direction: a term withheld that
	need not have been costs a learner one definition, and a term served that should not have been
	costs the question.
	"""
	pool = frappe.get_all(
		"Training Quiz Question",
		filters={"parent": lesson, "parenttype": "Training Lesson"},
		pluck="question",
	)
	if not pool:
		return ""

	parts = []
	for row in frappe.get_all(
		"Training Question", filters={"name": ["in", pool]}, fields=list(QUESTION_FIELDS)
	):
		parts.append(row.get("question_text") or "")
	for opt in frappe.get_all(
		"Training Answer Option",
		filters={"parent": ["in", pool], "parenttype": "Training Question"},
		fields=list(OPTION_FIELDS),
	):
		parts.append(opt.get("option_text") or "")
	return "\n".join(p for p in parts if p)


def _matches(terms, haystack):
	"""The terms that actually occur in ``haystack``.

	Longest spelling first inside a single entry means "breakpoint chlorination" is preferred to
	"breakpoint" when both are aliases of the same term. Across entries both still match, which is
	right — they are two words and a learner may want either.
	"""
	if not haystack:
		return []
	found = []
	for row in terms:
		doc = frappe.get_doc({"doctype": "Training Glossary Term", **row})
		for form in doc.match_patterns():
			if TrainingGlossaryTerm.compile_pattern(form).search(haystack):
				found.append(row)
				break
	return found


def _serve(row, in_quiz):
	"""One term, trimmed to what this context is allowed to show."""
	trap = cint(row["trade_trap"])
	out = {
		"term": row["term"],
		"short_definition": row["short_definition"] or "",
		"trade_trap": trap,
		"ordinary_meaning": (row["ordinary_meaning"] or "") if trap else "",
		# Said out loud on every entry rather than left for the reader to infer. A definition
		# somebody has stood behind and one a machine wrote last week are different things to rely
		# on when you are about to go and do the work.
		"unreviewed": bool(cint(row["ai_generated"]) and not row["reviewed_by"]),
		# Assembled in quiz mode as empty rather than omitted, so the payload has ONE shape and the
		# client has no branch that can accidentally render a key it did not expect.
		"explanation": "" if in_quiz else (row["explanation"] or ""),
		"example": "" if in_quiz else (row["example"] or ""),
		"see_also": [] if in_quiz else [s.strip() for s in (row["see_also"] or "").splitlines() if s.strip()],
	}
	return out


def help_for_lesson(lesson, in_quiz=False):
	"""The Help payload for one lesson. ``lesson`` is a **docname**, not a lesson_key."""
	in_quiz = bool(cint(in_quiz))
	terms = _enabled_terms()
	if not terms:
		return {"terms": [], "withheld": 0, "more": 0}

	present = _matches(terms, _lesson_text(lesson))

	withheld = 0
	if in_quiz:
		hidden = {r["name"] for r in _matches(present, _pool_text(lesson))}
		withheld = len(hidden)
		present = [r for r in present if r["name"] not in hidden]

	present.sort(key=lambda r: (r["term"] or "").lower())
	shown = present[:MAX_TERMS]
	return {
		"terms": [_serve(r, in_quiz) for r in shown],
		# Two counts, because they mean different things to a reader: `withheld` is the rule working
		# and is worth saying out loud on screen; `more` is just a long lesson. A total is
		# deliberately NOT sent -- it is `terms.length + more`, and a number the client can compute
		# is a number two places can disagree about.
		"withheld": withheld,
		"more": max(0, len(present) - len(shown)),
	}


# ------------------------------------------------------------------------- the gated entry


def _learner():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Please sign in."), frappe.PermissionError)
	return user


def _visible_or_throw(user, course):
	"""Refuse unless this learner may actually open the course.

	Whether somebody may read help about a course is the same predicate as whether they may take it,
	and ``api/training._visible_course_names`` is the single implementation of it. The cheap
	approximation is how an internal safety course ends up explained to a customer.

	Lazy import for the reason ``qa.py`` documents: ``api.training`` pulls in several ``training.*``
	modules, and a module-scope import here would put this file one careless edit from a cycle.
	"""
	from erpnext_enhancements.api.training import _learner_profile, _visible_course_names

	profile = _learner_profile(user)
	if course not in _visible_course_names(user, profile):
		frappe.throw(_("This course is not available to you."), frappe.PermissionError)
	return profile


def _lesson_row(course, lesson_key):
	"""The published lesson help is being asked about.

	Addressed by ``lesson_key`` and never by docname, matching the player and ``qa.py``. This is
	also the translation the gate exists to do: ``help_for_lesson`` takes a lesson **docname**, and
	handing it a ``lesson_key`` instead would match no content and return an empty panel rather than
	raising — a Help button that silently explains nothing.
	"""
	current_version = frappe.db.get_value("Training Course", course, "current_version")
	if not current_version:
		frappe.throw(_("That course has no published version."))
	name = frappe.db.get_value(
		"Training Lesson", {"course_version": current_version, "lesson_key": lesson_key}, "name"
	)
	if not name:
		frappe.throw(_("That lesson is not part of this course."))
	return name


def get_lesson_help(course, lesson_key, in_quiz=0):
	"""Help for the lesson a learner has open, gated like every other learner read."""
	user = _learner()
	if not runtime_ready():
		# Every read endpoint in this module answers a closed runtime with a message rather than an
		# exception, so ticking Training off shows a sentence instead of filling the Error Log.
		return {"terms": [], "withheld": 0, "more": 0}

	_visible_or_throw(user, course)
	return help_for_lesson(_lesson_row(course, lesson_key), in_quiz=in_quiz)
