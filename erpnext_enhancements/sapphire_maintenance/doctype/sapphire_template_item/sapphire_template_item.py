"""Controller for the Sapphire Template Item child doctype.

A single checklist line inside a Sapphire Maintenance Template
(``template_items`` table; ``istable``). Fields: ``sequence`` (ordering),
``question_prompt`` (the text copied onto a record's checklist), ``field_type``
(Data / Long Text / Select / Check), ``options`` and ``is_mandatory``.

No custom controller logic; behaviour comes from the JSON field definitions.
"""

import frappe
from frappe.model.document import Document

class SapphireTemplateItem(Document):
	pass
