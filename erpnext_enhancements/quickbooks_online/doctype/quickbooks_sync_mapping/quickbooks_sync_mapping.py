"""Controller for the ``QuickBooks Sync Mapping`` doctype.

The link ledger keyed on (qbo_entity_type, qbo_id) that makes the sync
idempotent. Each row records the mapped ERPNext DocType/name, the QBO SyncToken
and last-updated time, match metadata (status/rule/confidence), a soft-delete
flag, conflict_status, and ``owned_fields`` -- a JSON snapshot of the
QBO-sourced field values used to detect later user edits (conflict detection).
Created/updated by the ``save_*`` helpers in ``mapping.py``. No custom
controller logic.
"""

from frappe.model.document import Document


class QuickBooksSyncMapping(Document):
	"""QBO<->ERPNext link record; behaviour is entirely the Frappe default."""

	pass

