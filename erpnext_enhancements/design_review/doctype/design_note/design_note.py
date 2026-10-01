# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A note pinned to one part of one screen.

No role may create or write one: ``api.design_review.add_note`` writes it after checking the
session user is a participant and the review is Open (ADR 0016 §2), so the REST API cannot go
around that check. A System Manager moves its status through ``set_note_status``.
"""

from frappe.model.document import Document


class DesignNote(Document):
	pass
