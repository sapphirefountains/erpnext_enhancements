"""Controller for the ``QuickBooks Sync Log`` doctype.

One row per sync run (Import All / Preview Resync / Run Resync / Entity Sync /
Webhook / CDC / Retry). Tracks lifecycle (status, started/finished), per-action
counters (created/updated/linked/deleted/conflict/manual_review/failed),
retry_count, the dry-run plan (preview_payload) and any error_message. Created
and updated by the helpers in ``sync.py`` (``start_log``/``finish_log``/
``fail_log``/``_track_result``). No custom controller logic.
"""

from frappe.model.document import Document


class QuickBooksSyncLog(Document):
	"""Sync run record; behaviour is entirely the Frappe default."""

	pass

