# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A design review round: options, screens, participants, and a lifecycle.

The lifecycle is the ``status`` Select, moved by the **Design Review Lifecycle** Frappe Workflow
(Draft -> Open -> Closed -> Decided), because every transition is a human action gated by a
role — the case ADR 0016 §2 adopts a Workflow for, unlike the Enhancement Request's machine
states.

Content (tracks, stylesheet, click-through rules) is written by ``design_review.importer``
only. ``validate`` refuses a change to it from anywhere else, so the sanitized copy is the only
copy.
"""

from frappe.model.document import Document


class DesignReview(Document):
	def validate(self) -> None:
		from erpnext_enhancements.design_review.service import validate_review

		validate_review(self)
