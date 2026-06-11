"""Controller for the Sapphire Maintenance Template doctype.

A maintenance form definition for field-service visits, scoped to a Customer
and/or Project with a ``status`` (Draft / Active / Disabled). Since the modular
forms rework the template is *composed* of reusable Sapphire Maintenance
Sections (the ``sections`` child table) rather than owning its own question
rows — the same Chemical Dosing / Water Chemistry / Equipment Inspection /
Cleaning Tasks sections are shared across all templates.

When a Maintenance Record is created, the template is resolved (contract
feature row -> contract default -> Active template for the record's Project or
Customer) and its sections are instantiated into the record's typed child
tables (see ``sapphire_maintenance_record.get_visit_payload``).
"""

import frappe
from frappe.model.document import Document

class SapphireMaintenanceTemplate(Document):
	pass
