"""Sapphire Visit Follow-Up: something a technician found needs doing after the visit.

Child table ``follow_ups`` of Sapphire Maintenance Record (v1.600.0, D3 of Phase D). Lisa,
2026-10-12: "say Austin talked to someone and told them you need a new manifold, and then he
adds it to the form with a follow up and then I can get it over to Brian and follow it
through until the new part is installed and invoiced." When the visit is approved each row
becomes a Maintenance Follow-Up, tracked from Open to Invoiced (``follow_up`` links it).
"""

from frappe.model.document import Document


class SapphireVisitFollowUp(Document):
	pass
