"""Controller for the Sapphire Maintenance Result child doctype.

One answered checklist row on a Sapphire Maintenance Record
(``maintenance_results`` table; ``istable``). ``question`` is copied (read-only)
from the template item; the technician fills ``selection`` (e.g. Pass / Fail /
Replace / Other), a free-text ``answer``, and ``other_details`` (required when
``selection`` is "Other"). Rows marked "Fail"/"Replace" drive the warranty/RMA
check in ``api.maintenance_workflow.check_warranty_and_rma``.

No custom controller logic; behaviour comes from the JSON field definitions.
"""

import frappe
from frappe.model.document import Document

class SapphireMaintenanceResult(Document):
	pass
