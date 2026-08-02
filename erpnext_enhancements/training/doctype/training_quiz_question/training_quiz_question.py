# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Quiz Question — one row of a lesson's quiz pool.

Only a Link plus its weighting. The question text and options live on
``Training Question`` so the same question can serve several lessons and so
per-question analytics has a single join target.
"""

from frappe.model.document import Document


class TrainingQuizQuestion(Document):
	pass
