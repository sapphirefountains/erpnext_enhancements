# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Master template — the company's standard of care for one milestone (WI-075 sub-phase C).

An inspection is generated from two halves: this template, which says what Sapphire checks on
every job of this type, and the project's own contracted acceptance criteria, which say what was
promised on *this* job. Sub-phase D merges them and freezes the result.

What `revision` is, and what it is not
---------------------------------------

``revision`` bumps when **this template's own composition** changes — which sections it holds and
in what order. It is a coarse, human-facing version number, and it is deliberately not load-bearing
for provenance, because it cannot be: a change made *inside* a referenced ``Inspection Section``
alters what a future inspection will contain without touching this document at all.

Rather than pretend otherwise, the authoritative provenance lives on the generated inspection: it
stores a content hash over its own merged rows, so what an instance actually contained is
recoverable from the instance and never inferred from what the template says today. Saying that
out loud here is the point — a version number that looks authoritative and is not is worse than
no version number, because people stop checking.
"""

import frappe
from frappe import _
from frappe.model.document import Document


def composition(rows):
	"""A comparable fingerprint of a template's section list: order, section, location note.

	A plain tuple rather than a hash — it is compared, never stored, and a tuple is readable in
	a traceback while a hex digest is not.
	"""
	return tuple(
		(getattr(row, "section", None), (getattr(row, "location_note", None) or "").strip())
		for row in rows or []
	)


class ProjectInspectionTemplate(Document):
	def validate(self):
		self._require_matching_project_type()
		self._reject_duplicate_sections()
		self._bump_revision_when_composition_changes()

	def _require_matching_project_type(self):
		"""`project_type` is fetched from the milestone, so this only fires if somebody
		writes it directly through the API."""
		if not self.milestone:
			return
		expected = frappe.db.get_value("Inspection Milestone", self.milestone, "project_type")
		if expected and self.project_type and self.project_type != expected:
			frappe.throw(
				_("This template says {0} but its milestone belongs to {1}.").format(
					self.project_type, expected
				),
				title=_("Project stage does not match the milestone"),
			)
		self.project_type = expected or self.project_type

	def _reject_duplicate_sections(self):
		"""The same section twice would produce the same checks twice on every inspection,
		with no way for the inspector to tell which copy they already answered."""
		seen = set()
		for row in self.sections or []:
			if row.section in seen:
				frappe.throw(
					_("Row {0}: {1} is already on this template.").format(row.idx, row.section),
					title=_("Section listed twice"),
				)
			seen.add(row.section)

	def _bump_revision_when_composition_changes(self):
		"""Increment on a real change, and never on a save that changed nothing else.

		A revision that moves every time somebody opens and saves the form is noise, and noise
		in a version number is indistinguishable from a version number nobody maintains.
		"""
		if self.is_new():
			self.revision = self.revision or 1
			return

		before = self.get_doc_before_save()
		if before is None:
			return
		if composition(before.sections) != composition(self.sections):
			self.revision = (self.revision or 0) + 1
