# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The job-photo capture gate.

Crews are expected to photograph every job. The time clock is where that gets
enforced, because it is the one thing a technician always touches.

## The policy, and the one decision inside it

The gate blocks on **capture**, never on **upload**.

A photo row exists from the instant the shutter fires on the device
(``api.time_kiosk.record_job_photo`` writes it with ``upload_status =
"Pending"``); the bytes follow whenever the device next has a usable connection.
``photos_captured`` therefore counts Pending rows, and an interval closes cleanly
with every photo still queued on the phone.

That is deliberate and it is the difference between this shipping and this being
switched off in week two. A technician physically unable to leave a site because
an upload is failing will destroy trust in the system faster than any amount of
missing data justifies. The pending state is recorded, reported on
(``Job Photo Compliance``), and retried by the device — it is not a reason to
strand somebody in a mechanical room.

**This remains a live decision for Nikolas**, not a settled one: setting
``allow_photo_skip = 0`` in Time Kiosk Settings turns the gate into a hard block
with no escape hatch, which is a people decision rather than an engineering one.
The default ships permissive.

## Server-side, and why that phrase is load-bearing

Everything here runs on the server, inside ``log_time``. The kiosk also prompts
in the browser (``public/js/kiosk/app.js``), but that prompt is a courtesy. A
field device is the least trustworthy thing in the system: it is offline half the
day, it is running a service worker with a cached bundle of unknown age, and its
owner has every incentive to get home. Client-side validation on that device is a
suggestion. The check that counts is this one.

## Defensive throughout

Every field this module reads may be absent on a bench that has not migrated —
the Job Interval photo fields ship in the same release as the code. Reads go
through ``getattr(..., None) or default`` and ``frappe.db.has_column``, the same
convention the rest of the app uses for custom-field access.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.workforce.doctype.time_kiosk_settings.time_kiosk_settings import (
	get_settings,
)

#: Upload states that still count as "the job was photographed".
CAPTURED_STATES = ("Pending", "Uploaded")

#: Upload states meaning bytes are not on this server yet.
UNSETTLED_STATES = ("Pending", "Failed")

#: photo_status values.
STATUS_NOT_REQUIRED = "Not Required"
STATUS_REQUIRED = "Required"
STATUS_CAPTURED = "Captured"
STATUS_SKIPPED = "Skipped"


def gate_enabled(settings=None):
	"""True when the capture requirement is switched on."""
	settings = settings or get_settings()
	return bool(cint(settings.get("require_job_photos") or 0))


def minimum_photos(settings=None):
	settings = settings or get_settings()
	# A configured 0 would make the gate vacuous while still reading as ON, which
	# is worse than either state. Floor at 1.
	return max(1, cint(settings.get("min_photos_per_interval") or 1))


def photos_captured(interval):
	"""Photos on ``interval`` that count toward the minimum.

	Pending counts. Failed does not: the device gave up, and at that point we no
	longer have any evidence the photo exists anywhere.
	"""
	rows = getattr(interval, "photos", None) or []
	return sum(1 for row in rows if (getattr(row, "upload_status", None) or "Pending") in CAPTURED_STATES)


def has_unsettled_uploads(interval):
	rows = getattr(interval, "photos", None) or []
	return any((getattr(row, "upload_status", None) or "Pending") in UNSETTLED_STATES for row in rows)


def check(interval, skip_reason=None, settings=None):
	"""Raise unless ``interval`` may be closed. Returns the resolved photo status.

	Called from ``api.time_kiosk.log_time`` for the two actions that end an
	interval: ``Switch`` and ``Stop``. Start/Pause/Resume are untouched — pausing
	for lunch is not the end of a job and demanding a photo for it would train
	everybody to skip.
	"""
	settings = settings or get_settings()

	if not gate_enabled(settings):
		return STATUS_NOT_REQUIRED

	if not _interval_has_photo_fields():
		# Fields not migrated on this bench. Failing open is right: the
		# alternative is a time clock nobody can clock out of.
		return STATUS_NOT_REQUIRED

	if photos_captured(interval) >= minimum_photos(settings):
		return STATUS_CAPTURED

	skip_reason = (skip_reason or "").strip()

	if not cint(settings.get("allow_photo_skip") or 0):
		frappe.throw(
			_("A photo is required before you can finish this job. Take {0} photo(s) and try again.").format(
				minimum_photos(settings)
			),
			title=_("Photo required"),
		)

	if cint(settings.get("require_skip_reason") or 0) and not skip_reason:
		frappe.throw(
			_("Add a photo, or tell us why you are skipping it (for example: customer declined, nothing visible to photograph, camera not working)."),
			title=_("Photo required"),
		)

	return STATUS_SKIPPED


def stamp(interval, status, skip_reason=None):
	"""Record the gate's verdict on the interval.

	Written by the server on the closing call, never accepted from the client —
	otherwise a modified PWA bundle could mark every interval Captured.
	"""
	if not _interval_has_photo_fields():
		return

	if hasattr(interval, "photo_status"):
		interval.photo_status = status
	if hasattr(interval, "photo_skip_reason"):
		interval.photo_skip_reason = (skip_reason or "").strip() if status == STATUS_SKIPPED else None
	if hasattr(interval, "photos_pending_upload"):
		interval.photos_pending_upload = 1 if has_unsettled_uploads(interval) else 0


def _interval_has_photo_fields():
	try:
		return frappe.db.has_column("Job Interval", "photo_status")
	except Exception:
		return False


def bootstrap_payload(settings=None):
	"""The gate's configuration, for the kiosk PWA's bootstrap call.

	The client uses this to decide when to raise the camera prompt. It is a UX
	hint only — ``check`` above is what actually decides, and it re-reads settings
	server-side on every call rather than trusting whatever the device cached.
	"""
	settings = settings or get_settings()
	return {
		"require_job_photos": 1 if gate_enabled(settings) else 0,
		"min_photos_per_interval": minimum_photos(settings),
		"allow_photo_skip": cint(settings.get("allow_photo_skip") or 0),
		"require_skip_reason": cint(settings.get("require_skip_reason") or 0),
	}
