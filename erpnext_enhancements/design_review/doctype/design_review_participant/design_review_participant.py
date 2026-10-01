# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One person who may vote, give verdicts and write notes on a review.

A plain child table. Membership is checked by ``api.design_review`` on every write, and by the
read hooks in ``design_review.permissions``.
"""

from frappe.model.document import Document


class DesignReviewParticipant(Document):
	pass
