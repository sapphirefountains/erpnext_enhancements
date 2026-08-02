# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Chapter — a grouping label inside a course version.

Deliberately carries no content. Frappe has no grandchild tables, so a chapter
cannot own its lessons as rows; ``Training Lesson`` is top-level and points back
with ``course_version`` + ``chapter_key`` instead.
"""

from frappe.model.document import Document


class TrainingChapter(Document):
	pass
