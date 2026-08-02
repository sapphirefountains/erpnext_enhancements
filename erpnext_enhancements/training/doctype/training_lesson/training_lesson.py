# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Lesson — the unit a learner completes and the unit progress is kept in.

Top-level rather than a child of the chapter, because Frappe has no grandchild
tables: a ``Training Chapter`` is already a child row of the version, so it cannot
own rows of its own. A lesson points back with ``course_version`` + ``chapter_key``
instead. Everything joins on stable keys rather than row positions, which is why
reordering a course never strands a learner mid-lesson.

Two of its fields are machine-written at publish and matter more than they look:

* ``published_content_json`` — exactly what the player renders, built with every
  correct answer stripped out. Content is split per lesson rather than held whole
  on the version because a forty-lesson course runs to megabytes and a phone on a
  cellular connection should not download all of it to read lesson one.
* ``answer_key_json`` — the key, at **permlevel 1**. Learner roles hold no DocPerm
  on this doctype at all, so ``/api/resource/Training Lesson`` refuses them
  outright; the permlevel is the second lock, for the day somebody grants read
  here without thinking it through.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

# Blocks whose whole purpose is a piece of media; saving one with the media
# missing produces a lesson that renders a blank space and no error.
REQUIRED_MEDIA_FIELD = {
	"Image": "image",
	"PDF": "file",
	"Downloadable File": "file",
	"Video": "video_asset",
	"External Embed": "embed_url",
}


class TrainingLesson(Document):
	def validate(self):
		self._assign_lesson_key()
		self._assign_block_keys()
		self._validate_blocks()
		self._validate_chapter()
		self._validate_quiz()
		self._reject_edits_to_published_version()

	def on_trash(self):
		self._reject_edits_to_published_version()
		# Checkpoints are top-level, so nothing cascades them for us. Leaving them
		# behind would resurrect questions attached to a lesson that no longer
		# exists the next time a version is cloned.
		for name in frappe.get_all("Training Checkpoint", filters={"lesson": self.name}, pluck="name"):
			frappe.delete_doc("Training Checkpoint", name, ignore_permissions=True, force=True)

	# ------------------------------------------------------------------ helpers

	def _assign_lesson_key(self):
		if not (self.lesson_key or "").strip():
			self.lesson_key = frappe.generate_hash(length=10)

	def _assign_block_keys(self):
		used = set()
		for row in self.blocks or []:
			key = (row.block_key or "").strip()
			if not key or key in used:
				key = frappe.generate_hash(length=10)
			row.block_key = key
			used.add(key)

	def _validate_blocks(self):
		for row in self.blocks or []:
			field = REQUIRED_MEDIA_FIELD.get(row.block_type)
			if field and not row.get(field):
				frappe.throw(
					_("Block {0} is a {1} block with nothing attached.").format(row.idx, row.block_type)
				)
			if row.block_type in ("Rich Text", "Callout") and not (row.content or "").strip():
				frappe.throw(_("Block {0} is a {1} block with no text.").format(row.idx, row.block_type))
			if not 0 <= cint(row.min_coverage_percent) <= 100:
				frappe.throw(_("Block {0}: minimum watched must be between 0 and 100.").format(row.idx))
			if row.block_type == "External Embed":
				# Silently ignoring these would let a course look coverage-gated in
				# the form while the runtime waives the gate — the exact mismatch
				# that lets a compliance course lose its teeth unnoticed.
				row.min_coverage_percent = 0
				row.checkpoints_enabled = 0

	def _validate_chapter(self):
		"""A chapter key that matches nothing on the version leaves the lesson
		unreachable in the outline, and the author gets no hint why."""
		if not (self.chapter_key or "").strip() or not self.course_version:
			return
		known = frappe.get_all(
			"Training Chapter",
			filters={"parent": self.course_version, "parenttype": "Training Course Version"},
			pluck="chapter_key",
		)
		if known and self.chapter_key not in known:
			frappe.throw(
				_("No chapter on {0} has the key {1}.").format(self.course_version, self.chapter_key)
			)

	def _validate_quiz(self):
		if not cint(self.has_quiz):
			return
		pool = self.quiz_questions or []
		if not pool:
			frappe.throw(_("This lesson is marked as having a quiz but no questions are in the pool."))

		seen = set()
		for row in pool:
			if row.question in seen:
				frappe.throw(_("{0} is in the pool twice.").format(row.question))
			seen.add(row.question)

		to_ask = cint(self.quiz_questions_to_ask)
		if to_ask and to_ask > len(pool):
			frappe.throw(
				_("The quiz draws {0} questions from a pool of {1}. Add more questions, or lower the "
				  "number drawn.").format(to_ask, len(pool))
			)
		if not 0 <= cint(self.quiz_pass_score) <= 100:
			frappe.throw(_("Passing score must be between 0 and 100. Use 0 to inherit the course setting."))

	def _reject_edits_to_published_version(self):
		"""The framework already refuses to change a submitted document's own child
		rows — but a lesson is a *separate* document, so nothing stops it being
		edited out from under a live course unless we say so here. This is the guard
		that makes "published means frozen" true of the content and not just of the
		version record."""
		if not self.course_version:
			return
		if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
			return
		docstatus = cint(frappe.db.get_value("Training Course Version", self.course_version, "docstatus"))
		if docstatus == 1:
			frappe.throw(
				_("{0} is published and its content is frozen. Create a new draft version of the course "
				  "to make changes.").format(self.course_version)
			)
		if docstatus == 2:
			frappe.throw(_("{0} has been retired.").format(self.course_version))
