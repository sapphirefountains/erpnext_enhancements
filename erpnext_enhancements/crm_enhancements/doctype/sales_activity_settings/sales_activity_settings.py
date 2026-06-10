"""Sales Activity Settings (Single) doctype controller.

Holds the global ``inactivity_threshold`` (days) used as the fallback reminder
window by ``script_migrations.customer.customer_inactivity_reminder`` for
Customers without a per-customer ``custom_reminder_days``. Ported from a
DB-only custom DocType; no custom controller logic.
"""

from frappe.model.document import Document


class SalesActivitySettings(Document):
	pass
