"""Child table row for the status-change SMS recipient list.

Rows live in the ``status_alert_recipients`` table on ERPNext Enhancements
Settings; ``erpnext_enhancements.status_alerts`` reads them to decide who gets
texted when an Opportunity is marked Closed Won (PRO-0204 Step 1) and who is
included in the daily won-but-unconverted reminder. Phone numbers are not
stored here — delivery resolves the linked Employee's ``cell_number`` at send
time, so number changes need no settings edit.
"""

from frappe.model.document import Document


class StatusAlertRecipient(Document):
	pass
