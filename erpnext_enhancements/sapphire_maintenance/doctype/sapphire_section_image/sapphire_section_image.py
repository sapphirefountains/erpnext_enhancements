"""Sapphire Section Image — one how-to image for a maintenance step.

Reusable image+caption child table: ``step_images`` on Sapphire Maintenance
Section, and ``safety_images``/``wrapup_images`` on Sapphire Maintenance
Template. Each row illustrates how to complete a step, shown (with its
caption) inside the Visit Wizard's collapsible per-step instructions panel.
"""

from frappe.model.document import Document


class SapphireSectionImage(Document):
	pass
