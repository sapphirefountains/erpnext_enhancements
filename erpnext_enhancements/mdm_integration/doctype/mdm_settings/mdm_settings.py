"""Controller for the MDM Settings Single doctype.

Credential + state store for both providers (Miradore mobile MDM, Action1
computer RMM). All behaviour — reading secrets, provider selection, sync state —
lives in ``mdm_integration.utils`` / ``client`` so this stays a plain model.
"""

from frappe.model.document import Document


class MDMSettings(Document):
	pass
