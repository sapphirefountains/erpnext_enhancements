# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Child table: one partner store location offered by the fountain-move intake form.

Rows live on **ERPNext Enhancements Settings → Fountain Move Intake → Partner Store
Locations** and are seeded with the three Cactus & Tropicals stores by
``patches.seed_fountain_move_defaults``.

Editing the list needs no deploy. If every row is deleted or disabled,
``crm_enhancements.fountain_move.get_store_locations()`` falls back to the built-in
``CT_LOCATIONS`` constant rather than rendering a dropdown with no options — an
empty dropdown would make the public form unsubmittable with no server-side symptom.
"""

from frappe.model.document import Document


class FountainMoveLocation(Document):
	pass
