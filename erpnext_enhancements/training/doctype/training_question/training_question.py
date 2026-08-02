# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Question — one quiz question, whether shared or written for a lesson.

There is deliberately only **one** storage doctype. "Inline" is the
``is_bank_question`` flag, not a second shape. That buys three things: per-question
analytics has a single join target, promoting a good inline question to the shared
bank is a checkbox rather than a data migration, and the AI drafting endpoint has
one place to write.

Immutability across course versions is *not* achieved by freezing these rows — an
author must stay free to fix a typo. It comes from the publish step, which
snapshots question text and options into
``Training Lesson.published_content_json`` (answer-free) and ``answer_key_json``
(permlevel 1). The Link survives so analytics can group across versions; the
snapshot is what is served and what is graded.

No learner role holds a DocPerm here, so ``/api/resource/Training Question``
refuses them outright — a defence that survives a future endpoint being careless
with ``fields=["*"]``.
"""

import frappe
from frappe import _
from frappe.model.document import Document

MIN_OPTIONS = 2
MAX_OPTIONS = 6


class TrainingQuestion(Document):
	def validate(self):
		self._normalise_true_false()
		self._validate_options()
		self._validate_short_answer()
		self._assign_option_keys()

	# ------------------------------------------------------------------ helpers

	def _normalise_true_false(self):
		"""A True-False question is a two-option single choice; materialising the
		options means the player, the grader and the analytics all treat it
		identically to every other choice question instead of special-casing it."""
		if self.question_type != "True-False":
			return
		texts = [(row.option_text or "").strip().lower() for row in self.options or []]
		if sorted(texts) == ["false", "true"]:
			return
		correct_is_true = True
		for row in self.options or []:
			if (row.option_text or "").strip().lower() == "false" and row.is_correct:
				correct_is_true = False
		self.options = []
		self.append("options", {"option_text": _("True"), "is_correct": 1 if correct_is_true else 0})
		self.append("options", {"option_text": _("False"), "is_correct": 0 if correct_is_true else 1})

	def _validate_options(self):
		if self.question_type == "Short Answer":
			return
		rows = self.options or []
		if len(rows) < MIN_OPTIONS or len(rows) > MAX_OPTIONS:
			frappe.throw(
				_("A {0} question needs between {1} and {2} options.").format(
					self.question_type, MIN_OPTIONS, MAX_OPTIONS
				)
			)

		seen = set()
		for row in rows:
			text = (row.option_text or "").strip().lower()
			if text in seen:
				frappe.throw(_("Two options both read {0}. A learner cannot tell them apart.").format(row.option_text))
			seen.add(text)

		correct = [row for row in rows if row.is_correct]
		if not correct:
			frappe.throw(_("Tick the option that is correct — a question with no right answer can never be passed."))
		if self.question_type in ("Single Choice", "True-False") and len(correct) > 1:
			frappe.throw(
				_("{0} allows exactly one correct option; {1} are ticked. Switch the type to Multiple Choice "
				  "if more than one is right.").format(self.question_type, len(correct))
			)
		if len(correct) == len(rows):
			frappe.throw(_("Every option is ticked correct, so the question cannot be got wrong."))

	def _validate_short_answer(self):
		if self.question_type != "Short Answer":
			return
		accepted = [line.strip() for line in (self.correct_text_answers or "").splitlines() if line.strip()]
		if not accepted:
			frappe.throw(_("List at least one accepted answer, one per line."))
		self.correct_text_answers = "\n".join(accepted)

	def _assign_option_keys(self):
		"""Stable per-option keys. The player is handed these instead of a row
		index, which is what lets the server shuffle the options per attempt while
		still understanding the answer that comes back."""
		used = set()
		for row in self.options or []:
			key = (row.option_key or "").strip()
			if not key or key in used:
				key = frappe.generate_hash(length=8)
			row.option_key = key
			used.add(key)
