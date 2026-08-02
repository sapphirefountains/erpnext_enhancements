# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Answer Option — one selectable answer, shared by quiz questions and
in-video checkpoints.

``is_correct`` lives on this row, which is why no learner role holds a DocPerm on
this doctype: the runtime never reads it. The learner-facing payload is
materialized at publish into ``Training Lesson.published_content_json`` with the
flag stripped, and the key is kept separately at permlevel 1.
"""

from frappe.model.document import Document


class TrainingAnswerOption(Document):
	pass
