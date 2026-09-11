# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Kudos — one reaction, optionally with a sentence.

Reaction and comment are the **same record** rather than two. That is not
economy: a separate comment doctype would need its own moderation surface, its own
permission scoping and its own deletion story, and in a sixteen-person company the
comment is almost always an elaboration of the reaction anyway.

Not Frappe's ``Comment``, for two concrete reasons rather than taste.
``public/js/comments.js`` is Vue and needs the desk bundle, which ``www/training.py``
forbids because customer Website Users have ``desk_access = 0``. And
``tests/test_training_endpoint_surface.py`` hard-asserts the player contains no
``innerHTML`` — every node is built through ``el()`` and ``textContent`` — while
``Comment.content`` is HTML-editor output. Reusing it would mean either shipping
the desk bundle to customers or rendering HTML from one user into another user's
page.

The reactions are **words, not emoji**. A thumbs-up is read as encouragement by
one person and as sarcasm by another; "Nice work" cannot be. Five of them, fixed,
because a reaction picker with thirty options is a decision rather than a gesture.
"""

import frappe
from frappe import _
from frappe.model.document import Document

MAX_NOTE = 280


class TrainingKudos(Document):
	def validate(self):
		self._resolve_sender()
		self._reject_self_kudos()
		self._trim_note()

	def _resolve_sender(self):
		"""Always the session user. Accepting it would let one person post praise
		under another's name, which is a small thing that would feel very bad."""
		if not self.from_user or self.from_user != frappe.session.user:
			self.from_user = frappe.session.user

	def _reject_self_kudos(self):
		owner = frappe.db.get_value("Training Achievement", self.achievement, "user")
		if owner and owner == self.from_user:
			frappe.throw(_("You cannot congratulate yourself."))

	def _trim_note(self):
		"""Truncated rather than refused. Somebody typing a long note on a phone
		should not lose it to a validation error at the end."""
		note = (self.note or "").strip()
		self.note = note[:MAX_NOTE] if note else None
