"""Sapphire Template Section — composes a reusable Section into a Template.

Child table of Sapphire Maintenance Template. Row order (idx) is the order
sections of the same type are instantiated into a Maintenance Record.
"""

from frappe.model.document import Document


class SapphireTemplateSection(Document):
	pass
