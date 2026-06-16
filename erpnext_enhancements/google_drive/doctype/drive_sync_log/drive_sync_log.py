# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Audit log for every Google Drive automation action (folder provisioning,
attachment uploads, shadow-attachment sync, recording exports, backfills).
Rows are written exclusively by ``google_drive.drive_sync`` /
``drive_utils`` / ``api.call_recording_export``; Failed rows carry a JSON
``payload`` (method + kwargs) the nightly retry job re-enqueues."""

from frappe.model.document import Document


class DriveSyncLog(Document):
	pass
