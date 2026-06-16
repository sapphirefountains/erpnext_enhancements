"""Controller for the MDM Settings Single doctype.

Credential + state store for both providers (Miradore mobile MDM, Action1
computer RMM). All behaviour — reading secrets, provider selection, sync state —
lives in ``mdm_integration.utils`` / ``client`` so this stays a plain model.
"""

from frappe.model.document import Document

# Credential/enable fields per provider. When the operator edits any of these,
# the provider's auth pause (set by the sync layer on a non-retryable 401/403) is
# lifted so the scheduler retries with the new config. Programmatic status saves
# (run_device_sync / fail_log) never touch these, so a standing auth failure
# stays paused until a human reconfigures it.
_MIRADORE_CRED_FIELDS = ("miradore_enabled", "miradore_instance_name", "miradore_api_key")
_ACTION1_CRED_FIELDS = (
	"action1_enabled",
	"action1_org_id",
	"action1_client_id",
	"action1_client_secret",
)


class MDMSettings(Document):
	def validate(self):
		if self.is_new():
			return
		if any(self.has_value_changed(f) for f in _MIRADORE_CRED_FIELDS):
			self.miradore_auth_blocked = 0
		if any(self.has_value_changed(f) for f in _ACTION1_CRED_FIELDS):
			self.action1_auth_blocked = 0
