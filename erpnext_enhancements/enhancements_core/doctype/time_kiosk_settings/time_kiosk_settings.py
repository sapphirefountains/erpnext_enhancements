"""Controller for the Time Kiosk Settings Single doctype.

App-wide tuning for the Time Kiosk PWA's location tracking (``issingle``):
the master ``enable_tracking`` switch, sampling trade-offs (distance filter,
heartbeat interval, high-accuracy GPS, min accuracy, max batch size), an optional
screen wake-lock, and the ``retention_days`` window for purging old Time Kiosk
Logs.

Exposes ``get_settings()``, a defensive reader used by the kiosk bootstrap
(``api.time_kiosk.get_kiosk_bootstrap``) that falls back to ``DEFAULTS`` for any
unset field, so it is safe even before the Single has ever been saved.
"""

import frappe
from frappe.model.document import Document

# Defaults used when the Single doc has never been saved or a field is blank.
# Kept in sync with the field defaults in time_kiosk_settings.json so the client
# bootstrap (api.time_kiosk.get_kiosk_bootstrap) always has sane numbers.
DEFAULTS = {
	"enable_tracking": 1,
	"distance_filter_m": 25,
	"heartbeat_seconds": 300,
	"high_accuracy": 0,
	"min_accuracy_m": 100,
	"max_batch_size": 50,
	"keep_wake_lock": 0,
	"retention_days": 90,
}


class TimeKioskSettings(Document):
	pass


def get_settings():
	"""Return Time Kiosk Settings as a dict, falling back to DEFAULTS for any
	field that is unset/blank. Safe to call before the Single has ever been saved."""
	doc = frappe.get_cached_doc("Time Kiosk Settings")
	resolved = {}
	for key, default in DEFAULTS.items():
		value = doc.get(key)
		# Checks come back as 0/1; ints may be 0 legitimately, so only fall back on None/"".
		resolved[key] = default if value in (None, "") else value
	return resolved
