# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Yes, maybe or no on one screen of one option, by one participant. One per participant per
screen; giving another replaces it. Written only by ``api.design_review.cast_verdict``.
"""

from frappe.model.document import Document


class DesignVerdict(Document):
	pass
