# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Child table: one component on a Rental Checklist Template.

No logic of its own — the parent (``RentalChecklistTemplate``) validates the rows
together, because the rules that matter (a template must have rows; a component may
not be listed twice) are about the set rather than any single row.

The class name must stay ``RentalChecklistTemplateItem``: Frappe resolves a controller
with ``doctype.replace(" ", "").replace("-", "")`` and passes anything that fails to
import to ``frappe.delete_doc(force=True)`` on every migrate. See
``tests/test_doctype_controller_names.py``.
"""

from frappe.model.document import Document


class RentalChecklistTemplateItem(Document):
    pass
