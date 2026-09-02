# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the Plaid Banking Settings Single doctype.

Switches and status for the Bank Balances widget, nothing more. Credentials,
the environment and the per-bank access tokens live in ERPNext's own
``Plaid Settings`` (module ERPNext Integrations) and on ``Bank.plaid_access_token``;
``plaid_banking.core.utils`` reads them from there. This doctype used to be a
second ``Plaid Settings`` and collided with the native one -- see
``patches/rename_plaid_settings_doctype.py`` for what that did and why this
name is not going back.

The only logic here mirrors ``mdm_settings.py``: when the operator flips the
widget switch, lift the auth-pause (set by the balance layer on a non-retryable
Plaid error) so the scheduler tries again. Credential edits happen on the native
form, which this controller cannot see; a successful Test Connection or manual
Refresh lifts the pause for that path.
"""

from frappe.model.document import Document

# Editing any of these lifts the auth pause. Programmatic status saves
# (refresh_balances / test_connection) never touch them, so a standing auth
# failure stays paused until a human reconfigures or a manual call succeeds.
_UNPAUSE_FIELDS = ("plaid_enabled", "refresh_poll_minutes")


class PlaidBankingSettings(Document):
	def validate(self):
		if self.is_new():
			return
		if any(self.has_value_changed(f) for f in _UNPAUSE_FIELDS):
			self.plaid_auth_blocked = 0
