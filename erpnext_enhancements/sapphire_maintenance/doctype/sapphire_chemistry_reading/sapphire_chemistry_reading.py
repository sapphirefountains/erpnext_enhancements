"""Sapphire Chemistry Reading — one water-chemistry measurement on a visit.

Child table of Sapphire Maintenance Record, instantiated from a Water
Chemistry section. ``min_value``/``max_value`` carry the target range (section
defaults, overridable per water feature via the Serial No's reading-range
overrides); ``out_of_range`` is computed server-side on every save and feeds
the supervisor notification.
"""

from frappe.model.document import Document


class SapphireChemistryReading(Document):
	pass
