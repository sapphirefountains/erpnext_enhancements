"""Sapphire Section Item — one line of a reusable maintenance form section.

Child table of Sapphire Maintenance Section. Which columns matter depends on
the parent's ``section_type``; see the parent controller's docstring.
"""

from frappe.model.document import Document


class SapphireSectionItem(Document):
	pass
