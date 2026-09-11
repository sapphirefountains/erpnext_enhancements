# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Checkpoint — a question that interrupts a video partway through.

Attached to a video block by ``(lesson, block_key)`` rather than nested inside it.
Two reasons, and both matter: Frappe has no grandchild tables (a block is already
a child row, so it cannot own rows of its own), and per-checkpoint analytics —
"87% of people miss the one at 4:12", which usually means the *video* is unclear
rather than the learners — needs a queryable row with a stable identity that an
answer record can link to. Holding these as JSON on the block would have made that
impossible.

The timestamps are deliberately never handed to the player as a list. A list of
``at_seconds`` is a map of exactly where to skip to; the runtime returns only the
next unanswered one and re-arms after each answer.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

# Two checkpoints closer together than this read as one interruption and mostly
# annoy; a checkpoint in the closing seconds asks a question of someone who has
# already mentally left.
MIN_GAP_SECONDS = 5
MIN_TAIL_SECONDS = 10


class TrainingCheckpoint(Document):
	def validate(self):
		self._assign_key()
		self._validate_block()
		self._validate_options()
		self._validate_position()

	# ------------------------------------------------------------------ helpers

	def _assign_key(self):
		if not (self.checkpoint_key or "").strip():
			self.checkpoint_key = frappe.generate_hash(length=10)

	def _validate_block(self):
		"""A checkpoint pointing at a block that is not a real, non-embedded video
		can never fire, and nothing else would ever tell the author why."""
		block = self._block()
		if not block:
			frappe.throw(
				_("No content block on {0} has the key {1}.").format(self.lesson, self.block_key)
			)
		if block.get("block_type") == "External Embed":
			frappe.throw(
				_("An External Embed block cannot be checkpointed — the player cannot pause a video it "
				  "does not control. Register the video as a Training Video Asset instead.")
			)
		if block.get("block_type") != "Video":
			frappe.throw(_("Checkpoints only attach to Video blocks; this one is a {0} block.").format(
				block.get("block_type")
			))

	def _validate_options(self):
		"""Only CONTRADICTIONS throw here. Incompleteness is a publish-time refusal.

		The distinction is the whole of this change. A checkpoint with one option is
		*unfinished* -- which is what every checkpoint is for the first few seconds of
		its life, while somebody is still typing. A Single Choice with two correct
		answers, or one where every option is correct, is *self-contradictory*: no
		amount of further typing makes it coherent, and it can never become valid by
		accident, so refusing it immediately costs nothing.

		Same shape, and the same reasoning, as ``TrainingLesson.incomplete_blocks``:
		the check MOVED rather than being relaxed. Relaxing alone would let a
		half-built checkpoint reach ``_split_lesson``, which writes an answer key of
		``"correct": []`` -- a checkpoint nobody can pass, holding the lesson open
		forever through ``grading._unanswered_checkpoints``, with nothing raising.
		"""
		rows = self.options or []
		correct = [row for row in rows if row.is_correct]
		if self.question_type in ("Single Choice", "True-False") and len(correct) > 1:
			frappe.throw(_("{0} allows exactly one correct option.").format(self.question_type))
		if rows and len(correct) == len(rows):
			frappe.throw(_("Every option is ticked correct, so the checkpoint cannot be got wrong."))

		used = set()
		for row in rows:
			key = (row.option_key or "").strip()
			if not key or key in used:
				key = frappe.generate_hash(length=8)
			row.option_key = key
			used.add(key)

	def incomplete_reasons(self):
		"""Why this checkpoint is not ready to publish, as a list of sentences.

		The one implementation of "this checkpoint is half-built", read twice: while
		the version is a **draft** it is advisory, and at **publish** it is a hard
		refusal. Exactly the contract ``TrainingLesson.incomplete_blocks`` already has.

		Empty list means ready. Never throws -- a caller asking what is missing must
		be able to ask about a checkpoint that is missing everything.
		"""
		why = []
		if not (self.question_text or "").strip():
			why.append(_("no question has been typed"))
		rows = self.options or []
		if len(rows) < 2:
			why.append(_("fewer than two options"))
		elif not [row for row in rows if row.is_correct]:
			why.append(_("no option is ticked correct, so it can never be passed"))
		return why

	def _validate_position(self):
		at = cint(self.at_seconds)
		if at < 0:
			frappe.throw(_("A checkpoint cannot sit before the start of the video."))

		duration = cint((self._block() or {}).get("video_duration_seconds"))
		if duration and at > duration - MIN_TAIL_SECONDS:
			frappe.throw(
				_("This checkpoint sits in the last {0} seconds of a {1}-second video. Move it earlier — "
				  "a question at the very end interrupts someone who has already finished watching.").format(
					MIN_TAIL_SECONDS, duration
				)
			)

		clash = frappe.db.sql(
			"""
			select at_seconds from `tabTraining Checkpoint`
			where lesson = %(lesson)s and block_key = %(block_key)s and name != %(name)s
			  and abs(at_seconds - %(at)s) < %(gap)s
			limit 1
			""",
			{
				"lesson": self.lesson,
				"block_key": self.block_key,
				"name": self.name or "",
				"at": at,
				"gap": MIN_GAP_SECONDS,
			},
		)
		if clash:
			frappe.throw(
				_("Another checkpoint on this video sits at {0}s, within {1} seconds of this one. "
				  "Two interruptions that close together read as one.").format(clash[0][0], MIN_GAP_SECONDS)
			)

	def _block(self):
		"""The parent lesson's content block this checkpoint targets, as a dict."""
		if not self.lesson or not self.block_key:
			return None
		rows = frappe.get_all(
			"Training Content Block",
			filters={
				"parent": self.lesson,
				"parenttype": "Training Lesson",
				"block_key": self.block_key,
			},
			fields=["block_type", "video_duration_seconds"],
			limit=1,
		)
		return rows[0] if rows else None
