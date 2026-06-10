"""Master record for one step of the won-opportunity hand-off process.

The enabled templates, ordered by ``step_number``, are the process definition
(PRO-0204 "Won Opportunity Hand-Off" — seeded by the
``seed_process_step_templates`` patch, insert-if-missing so site edits
survive). ``erpnext_enhancements.process_steps`` copies them onto every new
Project created from an Opportunity as ``Project Process Step`` rows; editing
a template therefore affects future projects only, never in-flight ones.
"""

from frappe.model.document import Document


class ProcessStepTemplate(Document):
	pass
