"""Controller for the Time Kiosk Settings Single doctype.

App-wide tuning for the Time Kiosk PWA's location tracking (``issingle``):
the master ``enable_tracking`` switch, sampling trade-offs (distance filter,
heartbeat interval, high-accuracy GPS, min accuracy, max batch size), the screen
wake-lock, the ``retention_days`` window for purging old Time Kiosk Logs, and —
since v1.480.0 — the reliability dials (auto-close limit, tracking-gap threshold,
low-accuracy retention, anchor accuracy, off-site warning), the payroll/costing
dials (workweek start, weekly overtime threshold, default burden) and the
supervisor digest switch.

Exposes ``get_settings()``, a defensive reader used by the kiosk bootstrap
(``api.time_kiosk.get_kiosk_bootstrap``) that falls back to ``DEFAULTS`` for any
unset field, so it is safe even before the Single has ever been saved.

A ``default`` on a NEW field of a Single never reaches the row that already
exists (CLAUDE.md), so every field added here ships with a row in
``patches/backfill_time_kiosk_settings_defaults.py``; ``DEFAULTS`` below is the
in-process safety net for the window before that patch runs.
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
	# Both flipped 2026-09-17 (v1.480.0), Nik's decision: a sleeping phone stops
	# reporting, and a 90-day purge deleted the trail before anyone asked about it.
	"keep_wake_lock": 1,
	"retention_days": 0,
	# Reliability (v1.480.0).
	"auto_close_after_hours": 14,
	"tracking_gap_minutes": 15,
	"keep_low_accuracy_fixes": 1,
	"anchor_accuracy_m": 50,
	"offsite_warn": 1,
	# Payroll & costing (v1.480.0).
	"overtime_week_start": "Sunday",
	"overtime_weekly_hours": 40.0,
	"default_burden_pct": 0.0,
	"default_time_category": "",
	# Supervision (v1.480.0).
	"send_supervisor_digest": 1,
	# Job photo capture gate (v1.241.0). Off by default — the app's staged-rollout
	# convention, and a gate that arrives switched on without warning is how a
	# field rollout gets rejected on day one.
	"require_job_photos": 0,
	"min_photos_per_interval": 1,
	"allow_photo_skip": 1,
	"require_skip_reason": 1,
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
