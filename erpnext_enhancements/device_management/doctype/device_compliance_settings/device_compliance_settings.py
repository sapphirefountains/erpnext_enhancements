"""Controller for the Device Compliance Settings Single doctype.

App-wide knobs for the Device Management module: the self-attestation cadence
and warranty-reminder lead used by the scheduled nudges
(``device_management.tasks``), whether BYOD devices must attest, whether to
notify Device Managers on non-compliance, and whether the Device Console shows
the in-page camera scanner.

``get_settings()`` is a defensive reader (used by the API, the dashboard and the
scheduled tasks) that falls back to ``DEFAULTS`` for any unset field, so it is
safe even before the Single has ever been saved — mirroring
``inventory_scanner_settings.get_settings``.
"""

import frappe
from frappe.model.document import Document

# Defaults used when the Single has never been saved or a field is blank. Kept
# in sync with the field defaults in device_compliance_settings.json.
DEFAULTS = {
	"attestation_interval_days": 90,
	"warranty_reminder_lead_days": 30,
	"require_attestation_for_byod": 1,
	"notify_device_manager_on_noncompliance": 1,
	"enable_camera_scan": 1,
}


class DeviceComplianceSettings(Document):
	pass


def get_settings():
	"""Return Device Compliance Settings as a dict, falling back to DEFAULTS for
	any unset/blank field. Safe to call before the Single has ever been saved."""
	doc = frappe.get_cached_doc("Device Compliance Settings")
	resolved = {}
	for key, default in DEFAULTS.items():
		value = doc.get(key)
		# Checks come back as 0/1; 0 is legitimate, so only fall back on None/"".
		resolved[key] = default if value in (None, "") else value
	return resolved
