"""Controller for the singleton ``QuickBooks Online Settings`` doctype.

This single doc is the integration's credential and state store: app
credentials (client_id/client_secret), the webhook verifier token, OAuth state
(encrypted access/refresh tokens, realm_id, token_expires_at), sync cursors
(last_full_import/last_cdc_sync/last_webhook_at), connection status and tuning
(cdc_poll_minutes, retry_limit). Secrets are stored in encrypted Password
fields and read/written via ``utils.get_secret``/``set_secret``.
"""

import frappe
from frappe.model.document import Document


class QuickBooksOnlineSettings(Document):
	"""Settings singleton; only enforces basic config invariants on save."""

	def validate(self):
		"""Guard config: require a Company before enabling sync; enforce environment.

		Raises if sync is enabled without an ERPNext Company set, or if environment
		is anything other than Sandbox/Production.
		"""
		if self.sync_enabled and not self.company:
			frappe.throw("ERPNext Company is required before enabling QuickBooks Online sync.")

		if self.environment not in {"Sandbox", "Production"}:
			frappe.throw("Environment must be Sandbox or Production.")

