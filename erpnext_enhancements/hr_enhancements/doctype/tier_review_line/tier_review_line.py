# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One requirement, as it stood when the review opened.

Deliberately empty of logic. The row is a **snapshot**: its kind, label and
`held_at_open` are written once by `hr_enhancements/tier_review.py` and never
recomputed, so there is nothing here to validate and nothing to derive.

The two rating columns are the only thing a human fills in, and the point of
having both is the **gap** between them rather than agreement. A technician who
rates themselves "on my own" where their reviewer says "with help" has surfaced
exactly the conversation this record exists to cause; a form that nudged the two
into line would destroy the only information it collects.

Present at all because `bench migrate` calls `load_doctype_module()` for every
DocType including child tables, and raises `ModuleNotFoundError` without it —
aborting the migrate partway and blocking every later deploy.
"""

from frappe.model.document import Document


class TierReviewLine(Document):
	pass
