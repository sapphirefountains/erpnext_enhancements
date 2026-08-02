# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Content Block — one renderable thing inside a lesson.

Typed by ``block_type`` rather than split into eight doctypes, because the
builder reorders them as a single list and a child Table gives that ordering for
free. Validation lives on the parent ``Training Lesson``, which is the only place
that can see whether a block's type and its filled-in fields agree.
"""

from frappe.model.document import Document


class TrainingContentBlock(Document):
	pass
