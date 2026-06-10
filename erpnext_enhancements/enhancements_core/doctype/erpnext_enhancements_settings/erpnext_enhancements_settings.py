"""Controller for the ERPNext Enhancements Settings Single doctype.

The app-wide configuration hub (``issingle``). Holds:
  * ``project_reminder_emails`` — recipients (child table Project Reminder Email)
    for the daily project-start reminder job.
  * ``maintenance_fee_item`` / ``maintenance_services_group`` — defaults read by
    ``api.maintenance_workflow.create_sales_invoice`` when billing a maintenance
    visit.
  * ``collab_enabled`` + ``collab_doctypes`` (child table Collab Doctype) — the
    live collaborative editing master switch and doctype allowlist, read by
    ``api.collab.get_collab_doctypes()`` and shipped to the desk client via
    ``boot.boot_session``. Seeded with the launch doctypes by the
    ``seed_collab_doctypes`` patch.

No custom controller logic; values are read via ``frappe.get_single`` /
``frappe.get_cached_doc`` elsewhere.
"""

import frappe
from frappe.model.document import Document


class ERPNextEnhancementsSettings(Document):
	pass
