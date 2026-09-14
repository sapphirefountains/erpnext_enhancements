# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A generated, frozen inspection — WI-075 sub-phase D.

The rows on this document were **copied** from a master template and a locked Scope of Work at
the moment it was generated, and are never re-read from either. A later edit to the template, or
to a reusable section, cannot reach an inspection that already happened.

There is no ``fetch_from`` on a single result field, and that absence is load-bearing: one would
silently un-freeze the snapshot the moment somebody edited the source, and it would look
entirely ordinary in a diff. The merge and the hash live in
:mod:`erpnext_enhancements.quality.merge`, which imports no ``frappe`` and is asserted on every
push — including by mutating a template after generation and demanding nothing moved.

What this file adds on top of the frozen rows is the live part: validating an answer against the
row's own options, deriving out-of-range from the frozen bounds, and counting progress.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_enhancements.quality import merge


class ProjectQualityInspection(Document):
	def validate(self):
		self._validate_answers()
		self._flag_out_of_range()
		self._recount_progress()
		self._derive_status()

	def before_submit(self):
		self._require_mandatory_answers()
		self._require_photos()
		if not self.inspection_date:
			frappe.throw(
				_("Record the date the inspection was actually carried out."),
				title=_("Inspection date missing"),
			)

	# ------------------------------------------------------------------ helpers
	#
	# Each of these is a thin call into `quality.merge`, which imports no frappe and is
	# asserted on every push. Keeping the judgement there rather than here is the whole
	# reason the freeze and the failure count are testable at all.

	def _validate_answers(self):
		"""An answer outside the row's own frozen options is refused rather than stored.

		Compared in Python, not SQL: under MariaDB's default PAD SPACE collation ``"Pass "``
		compares equal to ``"Pass"``, so a padded answer would be accepted and stored, and the
		failure count would then depend on invisible whitespace.
		"""
		for row in self.results or []:
			answer = (row.outcome or "").strip()
			if not answer:
				continue
			allowed = merge.allowed_answers(row)
			if answer not in allowed:
				frappe.throw(
					_("Row {0}: {1} is not one of {2}.").format(row.idx, answer, ", ".join(allowed)),
					title=_("Answer not offered for this check"),
				)
			row.outcome = answer

	def _flag_out_of_range(self):
		for row in self.results or []:
			row.out_of_range = 1 if merge.is_out_of_range(row) else 0

	def _recount_progress(self):
		counts = merge.tally(self.results)
		self.mandatory_total = counts["mandatory_total"]
		self.mandatory_answered = counts["mandatory_answered"]
		self.fail_count = counts["fail_count"]
		self.completion_percent = counts["completion_percent"]

	def _derive_status(self):
		"""Derived, never typed. A stored status disagreeing with the rows it summarises is a
		record nobody can trust."""
		if self.docstatus == 1:
			self.status = "Failed" if self.fail_count else "Passed"
		elif self.mandatory_answered:
			self.status = "In Progress"
		else:
			self.status = "Draft"

	def _require_mandatory_answers(self):
		unanswered = merge.unanswered_mandatory(self.results)
		if unanswered:
			frappe.throw(
				_("These mandatory checks have no answer: rows {0}.").format(
					", ".join(str(i) for i in unanswered)
				),
				title=_("Inspection incomplete"),
			)

	def _require_photos(self):
		missing = merge.missing_evidence(self.results)
		if missing:
			frappe.throw(
				_("These checks failed and the template requires a photo: rows {0}.").format(
					", ".join(str(i) for i in missing)
				),
				title=_("Evidence missing"),
			)


def recompute_snapshot_hash(doc):
	"""The hash of what this document's rows say *now*.

	Used to detect tampering: it should equal the stored ``snapshot_hash`` on any inspection
	nobody has edited through the API. Exposed as a function rather than run in ``validate``
	because an in-progress inspection legitimately differs — answers, notes and photos are not
	part of the hash, but the fields that are could be reached by a script.
	"""
	rows = [
		{field: getattr(row, field, "") for field in merge._IDENTITY_FIELDS}
		for row in doc.results or []
	]
	return merge.spec_hash(rows)
