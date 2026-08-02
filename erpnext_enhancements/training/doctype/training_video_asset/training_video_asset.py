# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Video Asset — one video, registered once and reused across courses.

The same "how to backwash the filter" clip turns up in three courses. One record
knows its duration (which the coverage maths divides by), its transcript (which
feeds AI assistance), and where it currently lives.

Two locations, on purpose. **Drive** is where authors upload and where the
original stays. **GCS** is what the player streams, via a short-lived signed URL,
because a Drive preview frame is cross-origin: the player cannot read
``currentTime``, cannot ``pause()``, and therefore cannot run in-video checkpoints
or measure watch coverage. The copy is what buys back both features.

``duration_source`` is more load-bearing than it looks. Coverage is a fraction of
``duration_seconds``, so a hand-typed 600 against a real 900-second video lets an
80% gate pass on 53% of an actual watch. Duration is probed from Drive's
``videoMediaMetadata.durationMillis`` wherever possible and the field is locked;
manual entry survives only as the flagged fallback after a probe failure.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


class TrainingVideoAsset(Document):
	def validate(self):
		self._require_a_source()
		self._reject_absurd_duration()
		self._stamp_registration()
		self._derive_status()

	# ------------------------------------------------------------------ helpers

	def _require_a_source(self):
		if not (self.drive_file_id or "").strip() and not (self.gcs_object or "").strip():
			frappe.throw(_("Give the video a Drive file to copy from, or a GCS object it already lives at."))

	def _reject_absurd_duration(self):
		"""Zero would make every coverage calculation divide by zero and read as
		either 0% or 100% depending on which way the guard falls; both are wrong,
		and neither is obvious from the learner's side."""
		if cint(self.duration_seconds) <= 0:
			frappe.throw(_("Duration must be greater than zero — watch coverage is measured against it."))

	def _stamp_registration(self):
		if self.is_new():
			self.uploaded_by = frappe.session.user
			self.uploaded_on = now_datetime()
			# A duration that arrives with a brand-new record was typed by a human;
			# the probe path sets this to "Probed" explicitly after it reads the file.
			if not self.duration_source:
				self.duration_source = "Manual"

	def _derive_status(self):
		"""Status reflects whether the video is actually playable, so the authoring
		UI can grey out a block pointing at a file somebody moved. The hourly check
		in ``training/drive_media.py`` is what sets Missing / Error; this only ever
		promotes Draft to Available."""
		if self.status in ("Missing", "Error"):
			return
		self.status = "Available" if (self.gcs_object or "").strip() else "Draft"
