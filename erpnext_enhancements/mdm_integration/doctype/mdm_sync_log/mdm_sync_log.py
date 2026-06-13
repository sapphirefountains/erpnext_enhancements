"""Controller for the MDM Sync Log doctype.

Run lifecycle + per-action counters for one provider device sync. Written by
``mdm_integration.sync``; no behaviour here.
"""

from frappe.model.document import Document


class MDMSyncLog(Document):
	pass
