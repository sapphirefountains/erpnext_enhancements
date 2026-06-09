"""Controller for the ``QuickBooks Raw Payload`` doctype.

Append-only audit log of every QBO payload the integration fetched or received,
written by ``sync.store_raw_payload``. Stores the source (Import/Resync/Webhook/
CDC/Manual), entity type/id, owning realm, the linked sync log and the verbatim
JSON payload. It is also the data source replayed by ``sync.run_resync`` and
``mapping.link_existing_record``. No custom controller logic.
"""

from frappe.model.document import Document


class QuickBooksRawPayload(Document):
	"""Stored QBO payload record; behaviour is entirely the Frappe default."""

	pass

