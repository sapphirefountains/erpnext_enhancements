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
import re

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.training.doctype.training_glossary_term.training_glossary_term import (
	compiled_pattern,
	match_patterns_for,
)
from erpnext_enhancements.training.doctype.training_settings.training_settings import runtime_ready

#: How many terms one panel will show.
#:
#: **Was 24, and 24 was guesswork.** The reasoning was that a lesson using forty glossary words
#: would get a Help panel longer than the lesson — sound in the abstract, and wrong about this
#: glossary. Measured against twelve technician lessons on production 2026-09-15, every single one
#: matched more than 24: the range was 28 to 105 and the middle was around 57. So the cap was not
#: a safety valve for the occasional dense lesson, it fired on **every** lesson, and the note
#: saying "…and N more terms in this lesson" was permanent furniture pointing at words a reader
#: had no way to reach.
#:
#: 200 is a ceiling rather than a budget: high enough that nothing real is hidden, low enough that a
#: glossary grown to thousands of entries cannot turn one panel into a multi-megabyte reply. The
#: payload is fetched only when somebody opens the panel (`loadHelp` in player.js runs on the
#: toggle, not on the lesson), so the cost is paid by the reader who asked for it.
MAX_TERMS = 200

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
	# `idx`, explicitly. `get_all` orders by `creation` unless told otherwise, and on a child table
	# that is the order the rows were WRITTEN -- close enough to reading order to look right and not
	# the same thing, which matters now that the panel lists terms in the order the lesson uses them.
	rows = frappe.get_all(
		"Training Content Block",
		filters={"parent": lesson, "parenttype": "Training Lesson"},
		fields=["heading", "content", "caption", "data"],
		order_by="idx asc",
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
	"""The terms that actually occur in ``haystack``, with where and how.

	Returns ``[{"row": …, "at": int, "forms": [spelling, …]}]``. Two things beyond "does it occur",
	and each one buys a feature:

	* ``at`` is the offset of the **earliest** occurrence of any of its spellings, which is what
	  lets the panel list terms in the order the lesson uses them. Alphabetical is the wrong order
	  for a list of fifty-seven: the word somebody just read is in a random position in it.
	* ``forms`` is **every** spelling that matched, not the first. The player marks up the lesson
	  text itself, and marking only the term's own name would miss the alias a lesson actually
	  used. Longest first, so the client can try them in order and a short spelling cannot claim
	  the first half of a long one.

	Note the deliberate loss of the old early ``break``: it stopped at the first matching spelling,
	which was enough to answer "is it here" and not enough for either of the above.
	"""
	if not haystack:
		return []
	found = []
	for row in terms:
		at = None
		forms = []
		for form in match_patterns_for(row.get("term"), row.get("aliases")):
			hit = compiled_pattern(form).search(haystack)
			if hit:
				forms.append(form)
				if at is None or hit.start() < at:
					at = hit.start()
		if forms:
			found.append({"row": row, "at": at, "forms": forms})
	return found


#: The inline tags a Desk author's editor can leave behind in a **plain** field. Deliberately a
#: short allowlist rather than a general ``<[^>]+>`` sweep: a glossary is full of "pH < 7 and
#: > 6", and a greedy pattern eats everything between the two and calls it a tag.
_INLINE_TAG = re.compile(r"</?\s*(?:b|i|p|br|em|strong|span)\s*/?>", re.IGNORECASE)


def _plain(value):
	"""A Small Text field, with any markup taken back out.

	``short_definition`` and ``ordinary_meaning`` are Small Text, so the player renders them as
	text and a tag in them arrives on screen as the word ``<i>``. Nothing stops somebody pasting
	from a rich editor into a plain field, and one seeded entry already does it. Stripped here
	rather than in the data because the data is insert-only: a correction to the JSON reaches a
	fresh install and never the site that has the term.
	"""
	return _INLINE_TAG.sub("", value or "").strip()


def _suppressed(hits, lesson):
	"""The names of the terms this lesson's quiz pool gives away.

	Split out of ``help_for_lesson`` because the single-term lookup and the search need the same
	answer, and a second implementation of "what must not be shown during a quiz" is the one piece
	of duplication in this module that could actually hand somebody a mark.
	"""
	rows = [hit["row"] for hit in hits]
	return {hit["row"]["name"] for hit in _matches(rows, _pool_text(lesson))}


def _serve(row, in_quiz, forms=()):
	"""One term, trimmed to what this context is allowed to show.

	``forms`` is the spellings that actually matched the lesson. The player uses them to mark the
	word where it appears in the text; an entry the reader reached by searching has none, and that
	is not a missing value — there is no occurrence to point at.
	"""
	trap = cint(row["trade_trap"])
	out = {
		"term": row["term"],
		"spellings": list(forms),
		"short_definition": _plain(row["short_definition"]),
		"trade_trap": trap,
		"ordinary_meaning": _plain(row["ordinary_meaning"]) if trap else "",
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
		hidden = _suppressed(present, lesson)
		withheld = len(hidden)
		present = [hit for hit in present if hit["row"]["name"] not in hidden]

	# In the order the lesson uses them, alphabetically only to break a tie. A lesson matches
	# around fifty-seven terms, and in an A-Z list the word somebody has just read sits in a
	# random position -- so the list reads as a dictionary bolted to the page rather than as a
	# key to the thing in front of them.
	present.sort(key=lambda hit: (hit["at"], (hit["row"]["term"] or "").lower()))
	shown = present[:MAX_TERMS]
	return {
		"terms": [_serve(hit["row"], in_quiz, hit["forms"]) for hit in shown],
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


# ------------------------------------------------------------------- one term, and searching


#: A search shorter than this matches most of the glossary and answers nothing. Two, not three,
#: for the same reason `MIN_MATCHABLE` is two: a technician looking up "CO" or "IP" is asking a
#: real question.
MIN_QUERY = 2

#: How many search hits come back. A search is a question with an answer in mind, so a long list
#: means the query was too broad and scrolling it is not the fix.
MAX_HITS = 40


def _term_row(term):
	"""One enabled glossary row by name, or ``None``.

	Read through `_enabled_terms` rather than by docname so `enabled` and the served field list are
	honoured in exactly one place. A term somebody has disabled must be unreachable by a See also
	link too, or disabling it only hides it from the panel.
	"""
	wanted = (term or "").strip().lower()
	if not wanted:
		return None
	for row in _enabled_terms():
		if (row.get("term") or "").strip().lower() == wanted:
			return row
	return None


def _gives_an_answer_away(row, lesson):
	"""Whether this term's own text appears in the lesson's quiz pool.

	The panel's suppression asks this of the terms the lesson uses. A See also link reaches outside
	that set -- the target need not appear in the lesson at all -- so the question has to be asked
	directly of the pool rather than inherited from the panel's answer. Missing that is how the one
	word the question turns on stays reachable by following a link from a word that does not.
	"""
	if not lesson:
		return False
	return bool(_matches([row], _pool_text(lesson)))


def term_help(term, lesson=None, in_quiz=False):
	"""One term by name. ``lesson`` is a **docname**, needed only to police quiz mode."""
	in_quiz = bool(cint(in_quiz))
	row = _term_row(term)
	if not row:
		return {"entry": None, "withheld": 0}
	if in_quiz and _gives_an_answer_away(row, lesson):
		# Said out loud rather than returned as "no such term": a learner who followed a link to a
		# word that plainly exists is owed the rule, not a dead end that reads like a bug.
		return {"entry": None, "withheld": 1}
	return {"entry": _serve(row, in_quiz), "withheld": 0}


def search_glossary(query, in_quiz=False):
	"""Terms whose name or alias contains ``query``.

	**Closed during a quiz, and that is the whole of the quiz rule here.** The panel's suppression
	is computed from the lesson's own pool, which is answerable because the lesson is known; a free
	search is a question about the entire glossary and there is no equivalent guarantee to give.
	Rather than approximate one, the search simply is not open mid-question -- the panel, already
	suppressed, still is.
	"""
	if bool(cint(in_quiz)):
		return {"terms": [], "more": 0}

	needle = (query or "").strip().lower()
	if len(needle) < MIN_QUERY:
		return {"terms": [], "more": 0}

	hits = []
	for row in _enabled_terms():
		haystack = [row.get("term") or "", *(row.get("aliases") or "").splitlines()]
		for form in haystack:
			if needle in form.strip().lower():
				hits.append(row)
				break

	# The word itself before the words that merely contain it: somebody typing "bond" wants
	# "Bonding", not the fourth entry that mentions it in an alias.
	hits.sort(key=lambda row: (not (row.get("term") or "").lower().startswith(needle), (row.get("term") or "").lower()))
	shown = hits[:MAX_HITS]
	return {"terms": [_serve(row, False) for row in shown], "more": max(0, len(hits) - len(shown))}


def get_term_help(course, term, lesson_key=None, in_quiz=0):
	"""One term, gated like every other learner read."""
	user = _learner()
	if not runtime_ready():
		return {"entry": None, "withheld": 0}

	_visible_or_throw(user, course)
	lesson = _lesson_row(course, lesson_key) if lesson_key else None
	return term_help(term, lesson=lesson, in_quiz=in_quiz)


def get_glossary_search(course, query, in_quiz=0):
	"""Search the glossary. Gated on the course so this is not an open dictionary endpoint."""
	user = _learner()
	if not runtime_ready():
		return {"terms": [], "more": 0}

	_visible_or_throw(user, course)
	return search_glossary(query, in_quiz=in_quiz)
