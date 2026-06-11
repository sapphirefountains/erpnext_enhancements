"""Sapphire Section Image — one how-to image for a maintenance step.

Child table of Sapphire Maintenance Section (``step_images``). Each row is an
illustration of how to complete that section's step, shown (with its caption)
inside the Visit Wizard's collapsible per-step instructions panel.
"""

from frappe.model.document import Document


class SapphireSectionImage(Document):
	pass
