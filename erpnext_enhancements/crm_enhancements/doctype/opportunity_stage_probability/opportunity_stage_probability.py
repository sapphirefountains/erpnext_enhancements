"""Opportunity Stage Probability child-table doctype controller.

A row of Sales Activity Settings' ``stage_probabilities`` table: the forecast probability an
Opportunity takes when it enters ``stage`` (form kit P5.5). Read by
``crm_enhancements.opportunity_probability``. No controller logic of its own; the parent
validates the table as a whole.
"""

from frappe.model.document import Document


class OpportunityStageProbability(Document):
	pass
