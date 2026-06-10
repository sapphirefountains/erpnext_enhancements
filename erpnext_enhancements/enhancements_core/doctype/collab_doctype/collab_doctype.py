"""Child table row for the live-collab doctype allowlist.

Rows live in the ``collab_doctypes`` table on ERPNext Enhancements Settings;
``api.collab.get_collab_doctypes()`` reads them as the server-side allowlist
and ``boot.boot_session`` ships them to the desk client. Onboarding caution:
before enabling a doctype, audit its form scripts for field-level change
handlers with non-idempotent side effects — they re-fire on every receiving
client when remote values are applied (see ``public/js/collab/live_form_sync.js``).
"""

from frappe.model.document import Document


class CollabDoctype(Document):
	pass
