# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A numbered part of a screen: the ``E05`` in ``L3-S04-E05``.

**Append-only**, enforced by ``design_review.codes.check_append_only`` on every import: a number never
changes meaning, so a note pinned to it keeps pointing at the same part in the next revision.
"""

from frappe.model.document import Document


class DesignPart(Document):
	pass
