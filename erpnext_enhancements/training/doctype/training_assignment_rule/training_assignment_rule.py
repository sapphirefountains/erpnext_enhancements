# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Assignment Rule — one "who owes this course" clause.

``applies_to_doctype`` is stamped from ``applies_to`` by the parent Course's
``validate`` so that ``applies_to_value`` can be a Dynamic Link; it is never
typed. The resolution from a rule to a set of users lives in
``training/assignment.py``, not here, because it needs the whole course.
"""

from frappe.model.document import Document


class TrainingAssignmentRule(Document):
	pass
