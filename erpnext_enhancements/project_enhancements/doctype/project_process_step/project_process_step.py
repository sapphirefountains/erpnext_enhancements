"""Child table row: one hand-off process step on a Project.

Rows live in ``Project.custom_process_steps``, seeded from the enabled
``Process Step Template`` records when a Project is created from an
Opportunity (or via the "Start Hand-Off Process" button on in-flight
projects). The engine in ``erpnext_enhancements.process_steps`` stamps
completions, computes due dates, auto-completes anchored steps, notifies the
next responsible person, and escalates overdue steps daily.
"""

from frappe.model.document import Document


class ProjectProcessStep(Document):
	pass
