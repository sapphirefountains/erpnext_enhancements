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


class TrainingGlossaryTerm(Document):
	def validate(self):
		self.term = (self.term or "").strip()
		self._clean_aliases()
		self._require_ordinary_meaning_for_a_trap()
		self._reject_self_reference()

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

	# ------------------------------------------------------------------ matching

	def match_patterns(self):
		"""Every spelling this term should be found by, longest first.

		Longest first matters: with both "breakpoint" and "breakpoint chlorination" in the
		glossary, a lesson mentioning the latter should match the specific term rather than
		stopping at the general one.
		"""
		forms = [self.term] + [a for a in (self.aliases or "").splitlines() if a.strip()]
		forms = [f.strip() for f in forms if len(f.strip()) >= MIN_MATCHABLE]
		return sorted(set(forms), key=len, reverse=True)

	@staticmethod
	def compile_pattern(form):
		"""A whole-word, case-insensitive matcher for one spelling.

		``\\b`` is wrong at an edge that is not a word character — ``Link-Seal`` and ``lock-out``
		both end on a hyphenated part, and a trailing ``\\b`` after ``l`` is fine while a leading
		one before ``L`` is fine too, but a term like ``+/-`` would break it. Guarding with
		``re.escape`` plus explicit lookarounds keeps punctuation-bearing terms matchable.
		"""
		escaped = re.escape(form)
		return re.compile(rf"(?<![\w-]){escaped}(?![\w-])", re.IGNORECASE)
