"""Controller for the MDM Raw Payload doctype.

Archive of each device record fetched from a provider (audit + replay source).
Written by ``mdm_integration.sync.store_raw_payload``; no behaviour here.
"""

from frappe.model.document import Document


class MDMRawPayload(Document):
	pass
