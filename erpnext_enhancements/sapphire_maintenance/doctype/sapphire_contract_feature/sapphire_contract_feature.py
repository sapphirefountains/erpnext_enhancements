"""Sapphire Contract Feature — one covered water feature on a Maintenance Contract.

Child table of Sapphire Maintenance Contract. Carries the feature's visit
frequency, assigned form template, chemical source warehouse, and the rolling
last/next visit dates the predictive scheduler reads and the record submission
updates.
"""

from frappe.model.document import Document


class SapphireContractFeature(Document):
	pass
