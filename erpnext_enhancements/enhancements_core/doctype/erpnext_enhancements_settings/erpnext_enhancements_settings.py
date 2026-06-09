"""Controller for the ERPNext Enhancements Settings Single doctype.

The app-wide configuration hub (``issingle``). Holds:
  * ``project_reminder_emails`` — recipients (child table Project Reminder Email)
    for the daily project-start reminder job.
  * ``maintenance_fee_item`` / ``maintenance_services_group`` — defaults read by
    ``api.maintenance_workflow.create_sales_invoice`` when billing a maintenance
    visit.

No custom controller logic; values are read via ``frappe.get_single`` elsewhere.
"""

import frappe
from frappe.model.document import Document


class ERPNextEnhancementsSettings(Document):
	pass
