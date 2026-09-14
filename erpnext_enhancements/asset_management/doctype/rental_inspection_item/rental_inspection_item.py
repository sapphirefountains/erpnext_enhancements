# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Child table: one component on a Rental Inspection.

No logic of its own — ``RentalInspection`` validates the rows as a set, because the
questions worth asking are about the set ("is every row answered?", "did anything come
back short?") rather than any row alone.

``component`` and ``qty_expected`` are read-only on purpose: they are copied from the
template (going out) or from the pre-shipping sheet (coming back), and a crew member who
could retype "expected 4" as "expected 3" could close a shortfall by editing the
question instead of answering it.

The class name must stay ``RentalInspectionItem`` — see
``tests/test_doctype_controller_names.py``.
"""

from frappe.model.document import Document


class RentalInspectionItem(Document):
    pass
