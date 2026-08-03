# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Badge — the definition of an award, not the award itself.

A badge is configuration: a name, an image, a points value and one criterion. The
awards are ``Training Badge Award`` rows, and the evaluator that creates them
lives in ``training/gamification.py``.

**The criterion is one field, and the others are cleared.** ``criteria_type``
selects which of ``criteria_value`` / ``criteria_course`` / ``criteria_category``
the evaluator reads; the rest are ignored. Leaving a stale ``criteria_course``
behind after somebody switches the type to Streak Days is how a badge ends up
meaning something nobody chose — the record would still *look* course-specific in
the list view while awarding on an unrelated rule. So switching type blanks what
no longer applies, at the cost of the operator retyping if they switch back.

Points are copied onto each award rather than read through the link, so
re-pricing a badge next year does not silently rewrite everybody's score.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

COURSE_COMPLETED = "Course Completed"
CATEGORY_COMPLETED = "Category Completed"
COUNT_BASED = "Courses Completed Count"
STREAK_DAYS = "Streak Days"

# criteria_type -> the one companion field the evaluator reads for it. Types
# absent from this map need no companion (Perfect Score, First Completion).
CRITERION_FIELD = {
	COURSE_COMPLETED: "criteria_course",
	CATEGORY_COMPLETED: "criteria_category",
	COUNT_BASED: "criteria_value",
	STREAK_DAYS: "criteria_value",
}

COMPANION_FIELDS = ("criteria_value", "criteria_course", "criteria_category")


class TrainingBadge(Document):
	def validate(self):
		self._clear_irrelevant_criteria()
		self._require_the_relevant_criterion()
		self._clamp_points()

	# ------------------------------------------------------------------ helpers

	def _clear_irrelevant_criteria(self):
		keep = CRITERION_FIELD.get(self.criteria_type)
		for field in COMPANION_FIELDS:
			if field != keep:
				self.set(field, None)

	def _require_the_relevant_criterion(self):
		"""A badge with no threshold is not "award to everyone", it is a rule the
		evaluator cannot answer — and the evaluator runs in a scheduled job where
		nobody would see it fail."""
		field = CRITERION_FIELD.get(self.criteria_type)
		if not field:
			return
		value = self.get(field)
		if not value:
			frappe.throw(
				_("{0} needs a value in {1} — the evaluator has nothing to test otherwise.").format(
					self.criteria_type, _(self.meta.get_label(field))
				)
			)
		if field == "criteria_value" and cint(value) <= 0:
			frappe.throw(_("The threshold for {0} has to be a positive number.").format(self.criteria_type))

	def _clamp_points(self):
		if cint(self.points) < 0:
			self.points = 0
