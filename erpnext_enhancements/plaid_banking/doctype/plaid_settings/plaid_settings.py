# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Plaid Settings Single doctype.

Credential + connection-state store for the Plaid bank-balance integration. All
behaviour (reading secrets, calling Plaid, refreshing balances) lives in
``plaid_banking.core`` so this stays a plain model. The only logic here mirrors
``mdm_settings.py``: when the operator edits any credential field, lift the
auth-pause (set by the balance layer on a non-retryable Plaid error) so the
scheduler retries with the new config.
"""

from frappe.model.document import Document

# Editing any of these lifts the auth pause. Programmatic status saves
# (refresh_balances / connect) never touch these, so a standing auth failure
# stays paused until a human reconfigures or successfully reconnects.
_CRED_FIELDS = ("plaid_enabled", "plaid_environment", "plaid_client_id", "plaid_secret")


class PlaidSettings(Document):
	def validate(self):
		if self.is_new():
			return
		if any(self.has_value_changed(f) for f in _CRED_FIELDS):
			self.plaid_auth_blocked = 0
