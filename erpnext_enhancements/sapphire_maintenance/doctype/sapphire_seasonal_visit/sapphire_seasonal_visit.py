"""Sapphire Seasonal Visit — an annual month-anchored visit on a Maintenance Contract.

Child table of Sapphire Maintenance Contract, seeded from the Project
Contract's included seasonal service options (Spring startup / Fall
winterization). The daily scheduler drafts one Maintenance Record per row when
the target month arrives, stamping ``last_generated_year`` so it fires at most
once per year.
"""

from frappe.model.document import Document


class SapphireSeasonalVisit(Document):
	pass
