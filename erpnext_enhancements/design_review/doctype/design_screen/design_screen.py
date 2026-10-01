# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One drawn frame of one screen of one option: desktop, phone, or a board.

``html`` is sanitized by ``design_review.sanitize`` on import and rendered only inside a sandboxed frame. Written by the
importer only.
"""

from frappe.model.document import Document


class DesignScreen(Document):
	pass
