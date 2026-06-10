"""Sapphire Reading Range Override — per-water-feature chemistry target range.

Child table attached to Serial No via the ``custom_reading_overrides`` Custom
Field. When a visit form is instantiated, a row whose ``reading`` matches a
Water Chemistry section item's label replaces that item's default min/max for
this feature.
"""

from frappe.model.document import Document


class SapphireReadingRangeOverride(Document):
	pass
