# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Child row of a Document Intake holding one extracted line item. Carries the
advisory ``matched_item`` plus, when no Item matches, a proposed new Item that
the inventory clerk (Stock Manager) reviews before it is created/used."""

from frappe.model.document import Document


class DocumentIntakeLine(Document):
	pass
