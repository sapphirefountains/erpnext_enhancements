# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One participant's ranking of one track's options. One per participant per track; casting again
replaces it. Written only by ``api.design_review.cast_vote`` after the participant check.
"""

from frappe.model.document import Document


class DesignVote(Document):
	pass
