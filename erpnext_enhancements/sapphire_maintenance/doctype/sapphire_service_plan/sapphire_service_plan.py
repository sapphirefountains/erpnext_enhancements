"""Sapphire Service Plan — a one-pick preset for Maintenance Contracts.

Holds the standard offering's defaults (visit frequency, form template, visit
shape, invoicing cadence, seasonal startup/winterization). Picking a plan on a
Sapphire Maintenance Contract *stamps* these values onto the contract (see the
contract form JS); it is not a live link — editing a plan later never ripples
to contracts that already applied it, so a contract keeps the terms it was
signed with. Standard plans are seeded by ``patches.seed_service_plans``.
"""

from frappe.model.document import Document


class SapphireServicePlan(Document):
	pass
