# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A design review round: its participants, its status, and the content file it shows.

The review itself is drawn by the Review Room at ``/review`` (``www/review.py`` and the
``design_review`` bundle), not by the Desk. This record is the moderator's: the participant list,
the status, and a link to the sanitized content file an import wrote. Content is changed only by
importing a new revision (``design_review.importer``); ``validate`` refuses anything else.
"""

from frappe.model.document import Document


class DesignReview(Document):
	def validate(self) -> None:
		from erpnext_enhancements.design_review.service import validate_review

		validate_review(self)
