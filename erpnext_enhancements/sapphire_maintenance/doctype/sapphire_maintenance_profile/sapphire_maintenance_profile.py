"""Controller for the Sapphire Maintenance Profile doctype.

One per Project (``project`` is unique): stores site-level briefing data shown to
technicians before a visit — ``safety_instructions`` and ``access_codes`` —
plus a read-only ``customer`` fetched from the project. Surfaced in the
Maintenance Record form's dashboard widget via
``sapphire_maintenance_record.get_dashboard_context``.

No custom controller logic; behaviour comes from the JSON field definitions.
"""

import frappe
from frappe.model.document import Document

class SapphireMaintenanceProfile(Document):
	pass
