"""Controller for the Sapphire Maintenance Template doctype.

A reusable checklist definition for field-service visits. Scoped to a Customer
and/or Project with a ``status`` (Draft / Active / Disabled), it owns the
``template_items`` child table (Sapphire Template Item) — the ordered list of
questions/prompts. When a Maintenance Record is created, the active template for
the record's Project (or Customer) is resolved and its items are copied into the
record's ``maintenance_results`` checklist (see
``sapphire_maintenance_record.get_template_items``).

No custom controller logic; behaviour comes from the JSON field definitions.
"""

import frappe
from frappe.model.document import Document

class SapphireMaintenanceTemplate(Document):
	pass
