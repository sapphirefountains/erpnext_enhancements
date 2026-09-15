# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Glossary Term — one word a learner might not know, explained once.

**The glossary is shared, not per-lesson.** "Annular space" is defined once and surfaces in
every lesson that happens to use it, because the alternative — help text written into each
lesson — means the same word explained seventy-two times, drifting, and a new lesson starting
with nothing. Matching is done against the lesson's own text at request time
(``training/help.py``), so adding a term makes it appear everywhere it was already needed.

**Three fields, and the split between them is a safety boundary rather than a layout choice.**

* ``short_definition`` is the only field Help shows **during a quiz**. That is a deliberate
  product decision: a quiz is meant to measure whether somebody understood the lesson, not
  whether they happened to know a word, and a technician who can do the work but has never heard
  the term "weir" should not fail for it. The field is therefore written to define the word
  without answering a question about it — and ``help.py`` additionally suppresses any term whose
  text appears in the options of the question on screen.
* ``explanation`` and ``example`` are the teaching, and they are **never** served mid-quiz. An
  explanation good enough to be worth writing is usually good enough to answer a question about
  the thing it explains.

Because the boundary is enforced in ``help.py`` and not here, this doctype carries **no DocPerm
for Training Learner at all** — the same stance the module takes on Training Question. A learner
cannot read ``/api/resource/Training Glossary Term``, so the mid-quiz suppression is a real
restriction rather than a decoration on data they could fetch anyway.

``trade_trap`` marks the words that mean something different in ordinary English — bonding,
aggressive, shock, weir, invert, hardness. Those are the ones somebody gets wrong while feeling
perfectly confident, which is why they get a field of their own rather than a tag.
"""

import re
from functools import lru_cache

import frappe
from frappe import _
from frappe.model.document import Document

#: Terms shorter than this are not matched in lesson text.
#:
#: **Two, not three.** The first cut of this was three, on the reasoning that a short term matches
#: nearly every lesson and drowns the panel it was meant to help — and that was wrong, because
#: matching is **whole-word**. ``IP`` does not match inside ``IP68`` (the lookahead refuses a
#: following word character), and ``CO`` standing alone in a lesson about gas heaters is carbon
#: monoxide rather than a coincidence. A floor of three would have refused twenty-one real
#: acronyms a learner actually meets — CO, LB, CT, PI, FI, TI, IP, MΩ — and the controller would
#: have thrown on them at seed time.
#:
#: One character is still refused, and that is the real line: a single letter carries no signal at
#: all and would match a variable name in a formula.
MIN_MATCHABLE = 2


#: Up to this many letters, an all-capital spelling is matched case-sensitively. Three, because the
#: collisions that actually happen are two-letter (``NO``/no, ``CO``/co, ``IP``/ip) and the
#: three-letter band still holds ordinary words — ``PIT``, ``CAP``, ``SET``. At four the shortest
#: real collision would have to be a word like ``PIPE``, which is not an acronym anybody writes.
SHORT_ACRONYM = 3


def _is_short_acronym(form):
	letters = re.sub(r"[^A-Za-z]", "", form or "")
	if not letters or len(letters) > SHORT_ACRONYM:
		return False
	return letters == letters.upper()


def normalised_spelling(value):
	"""A spelling reduced to what a reader would call the same word.

	Lower case, parenthetical dropped, punctuation flattened: ``Lock-out/tag-out`` and
	``Lock-out tag-out`` are one word to a technician and two different strings to a database, and
	the seeded glossary holds both. Shared by the duplicate warning here and by
	``training/glossary_review.py``, so the two cannot disagree about what a duplicate is.
	"""
	text = (value or "").lower().strip()
	text = re.sub(r"\s*\([^)]*\)\s*", " ", text)
	text = re.sub(r"[^a-z0-9 ]+", " ", text)
	return re.sub(r"\s+", " ", text).strip()


def match_patterns_for(term, aliases):
	"""Every spelling this term should be found by, longest first.

	Longest first matters: with both "breakpoint" and "breakpoint chlorination" in the glossary, a
	lesson mentioning the latter should match the specific term rather than stopping at the general
	one. The same order lets the player mark up lesson text without a short spelling stealing the
	first half of a long one.

	**A plain function, taking the two fields rather than a document.** ``help.py`` matches every
	enabled term against every lesson on every Help request, and building a ``Document`` per term
	to reach one method costs more than all the regex work put together — measured on production,
	a loop over twelve lessons could not finish inside a thirty-second budget. The method below
	delegates here so there is still only one definition of what a spelling is.
	"""
	forms = [term or ""] + [a for a in (aliases or "").splitlines() if a.strip()]
	forms = [f.strip() for f in forms if len(f.strip()) >= MIN_MATCHABLE]
	return sorted(set(forms), key=len, reverse=True)


class TrainingGlossaryTerm(Document):
	def validate(self):
		self.term = (self.term or "").strip()
		self._clean_aliases()
		self._require_ordinary_meaning_for_a_trap()
		self._reject_self_reference()
		self._say_if_this_looks_like_one_we_have()

	# ------------------------------------------------------------------ helpers

	def _clean_aliases(self):
		"""One alias per line, trimmed, de-duplicated, and never the term itself.

		Matching walks this list for every term on every Help request, so a blank line or a
		duplicate is work done on every page view for nothing.
		"""
		seen = {self.term.lower()}
		kept = []
		for raw in (self.aliases or "").splitlines():
			alias = raw.strip()
			if not alias or alias.lower() in seen:
				continue
			if len(alias) < MIN_MATCHABLE:
				frappe.throw(
					_("{0} is too short to match on. An alias needs at least {1} characters.").format(
						alias, MIN_MATCHABLE
					)
				)
			seen.add(alias.lower())
			kept.append(alias)
		self.aliases = "\n".join(kept)

	def _require_ordinary_meaning_for_a_trap(self):
		"""A trap with nothing to contrast against is just a tick.

		The whole value of ``trade_trap`` is being able to say *"you are probably thinking of X;
		here it means Y"*. Ticking the box without X leaves Help with a warning it cannot explain.
		"""
		if self.trade_trap and not (self.ordinary_meaning or "").strip():
			frappe.throw(
				_(
					"Say what {0} sounds like it means in everyday English — the point of the flag "
					"is the contrast, and without it there is nothing to contrast with."
				).format(self.term)
			)

	def _reject_self_reference(self):
		"""A term that lists itself under See also renders a link back to the open panel."""
		for line in (self.see_also or "").splitlines():
			if line.strip().lower() == (self.term or "").lower():
				frappe.throw(_("{0} cannot be its own See also.").format(self.term))

	def _say_if_this_looks_like_one_we_have(self):
		"""Warn — never refuse — when a new entry's name already exists in another shape.

		**A message rather than a throw, because the collision is sometimes correct.** *Scale* on a
		drawing and *scale* in a basin are different concepts that share a word, and refusing the
		second one would be wrong. What is never right is writing it *by accident*, which is how
		the seeded glossary ended up with *Authority having jurisdiction* and *Authority having
		jurisdiction (AHJ)* as two entries with two independently written definitions.

		**On insert only.** The question is being decided when the entry is created; re-asking it on
		every later save of a deliberate homonym is a nag that teaches people to ignore the box.
		"""
		if not self.is_new():
			return
		key = normalised_spelling(self.term)
		if not key:
			return
		for row in frappe.get_all("Training Glossary Term", fields=["name", "term"]):
			if row["name"] == self.name:
				continue
			if normalised_spelling(row["term"]) == key:
				frappe.msgprint(
					_(
						"There is already an entry called {0}. If this is the same thing, add this "
						"spelling to that entry as an alias instead — two entries for one word both "
						"show up in the Help panel."
					).format(row["term"]),
					title=_("That word may already be in the glossary"),
					indicator="orange",
				)
				return

	# ------------------------------------------------------------------ matching

	def match_patterns(self):
		"""Every spelling this term should be found by, longest first."""
		return match_patterns_for(self.term, self.aliases)

	@staticmethod
	def compile_pattern(form):
		"""A whole-word matcher for one spelling, case-insensitive unless it is a short acronym.

		``\\b`` is wrong at an edge that is not a word character — ``Link-Seal`` and ``lock-out``
		both end on a hyphenated part, and a trailing ``\\b`` after ``l`` is fine while a leading
		one before ``L`` is fine too, but a term like ``+/-`` would break it. Guarding with
		``re.escape`` plus explicit lookarounds keeps punctuation-bearing terms matchable.

		**A two- or three-letter acronym matches case-sensitively, and that is not fussiness.**
		Found on production 2026-09-15, by hovering it: ``Normally closed`` carries the alias
		``NO``, and case-insensitively that matches the English word *no* — "landscape lighting on
		a photocell will come on at dusk **no** matter what you did at the pump panel". The panel
		listed a glossary entry about relay contacts because the lesson said "no", and once the
		lesson text is marked up that word gets a dotted underline in front of the reader.

		``CO``, ``IP``, ``OL`` and ``PI`` are all one ordinary word away from the same thing. An
		acronym is written in capitals by whoever means it, so requiring them costs almost nothing
		and stops the panel explaining a word nobody used. Anything longer, or anything not written
		in capitals, stays case-insensitive — ``GFCI`` does not collide with English, and *Haunching*
		at the start of a sentence must still match ``haunching``.
		"""
		escaped = re.escape(form)
		flags = 0 if _is_short_acronym(form) else re.IGNORECASE
		return re.compile(rf"(?<![\w-]){escaped}(?![\w-])", flags)


@lru_cache(maxsize=8192)
def compiled_pattern(form):
	"""``compile_pattern`` with the result kept.

	Same spelling, same regex, and a Help request compiles a few thousand of them. Keyed on the
	spelling itself, so an edited alias simply produces a different key rather than a stale hit.
	"""
	return TrainingGlossaryTerm.compile_pattern(form)
