# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Intake channels for the Accounting Document Intake pipeline. Every channel
funnels into the single ``intake.ingest_document`` door:

- ``email_from_communication`` — inbound-email attachments (Communication
  ``after_insert``);
- ``poll_watched_folder`` — a Google Drive watched folder (hourly scheduler);
- ``ingest_mobile_photo`` — a whitelisted endpoint for mobile capture.

Plus scheduler maintenance: ``retry_failed_intakes`` (re-enqueue Failed steps
that carry a retry payload) and ``purge_old_intake_logs``. All channels are
gated by Accounting Intake Settings ``intake_enabled`` and bail cheaply when off
(the Communication hook fires for every communication site-wide)."""

import json

import frappe
from frappe import _

from erpnext_enhancements.accounting_intake import intake
from erpnext_enhancements.accounting_intake.audit import log_intake

_DOC_EXTENSIONS = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp")
_MAX_RETRY = 3
_MOBILE_ROLES = {"Accounts User", "Accounts Manager", "System Manager"}


def _settings():
	return frappe.get_cached_doc("Accounting Intake Settings")


def _is_document(file_name):
	return bool(file_name) and file_name.lower().endswith(_DOC_EXTENSIONS)


# --- Email channel -------------------------------------------------------


def email_from_communication(doc, method=None):
	"""Communication ``after_insert``: ingest PDF/image attachments of inbound
	emails received at the configured intake Email Account."""
	if doc.get("communication_medium") != "Email" or doc.get("sent_or_received") != "Received":
		return
	settings = _settings()
	if not (settings.get("intake_enabled") and settings.get("email_account")):
		return
	# If the email landed in a specific account, only process the intake one.
	if doc.get("email_account") and doc.email_account != settings.email_account:
		return

	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Communication", "attached_to_name": doc.name},
		fields=["file_url", "file_name"],
	)
	ref = f"{doc.get('sender') or ''}: {doc.get('subject') or ''}".strip()[:140]
	for att in attachments:
		if not _is_document(att.file_name):
			continue
		try:
			intake.ingest_document(file_url=att.file_url, source_channel="Email", source_reference=ref)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Accounting Intake Email Channel")


# --- Google Drive watched-folder channel ---------------------------------


def poll_watched_folder():
	"""Scheduler (hourly): ingest new files dropped into the configured Drive
	watched folder, then move each to the processed folder when one is set.
	Deduped by the Drive file id recorded as the intake ``source_reference``."""
	settings = _settings()
	if not (settings.get("intake_enabled") and settings.get("watched_folder_id")):
		return

	from erpnext_enhancements.google_drive import drive_utils

	service, default_drive = drive_utils.get_drive_service()
	drive_id = settings.get("watched_drive_id") or default_drive
	folder = settings.watched_folder_id
	processed = settings.get("processed_folder_id")

	ingested = 0
	for fmeta in _list_folder_files(service, folder, drive_id):
		fid = fmeta.get("id")
		mime = fmeta.get("mimeType") or ""
		if not fid or mime.startswith("application/vnd.google-apps"):
			continue
		if frappe.db.exists("Document Intake", {"source_reference": f"drive:{fid}"}):
			continue
		try:
			content = service.files().get_media(fileId=fid, supportsAllDrives=True).execute()
			if isinstance(content, str):
				content = content.encode("utf-8")
			file_doc = frappe.get_doc(
				{"doctype": "File", "file_name": fmeta.get("name") or fid, "is_private": 1, "content": content}
			).insert(ignore_permissions=True)
			intake.ingest_document(
				file_url=file_doc.file_url, source_channel="Google Drive", source_reference=f"drive:{fid}"
			)
			ingested += 1
			if processed:
				_move_file(service, fid, folder, processed)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Accounting Intake Drive Watch")

	if ingested:
		log_intake("Ingest", "Success", detail=f"Drive watch ingested {ingested} file(s)")


def _list_folder_files(service, folder_id, drive_id):
	escaped = folder_id.replace("'", "\\'")
	q = f"'{escaped}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
	kwargs = {"q": q, "fields": "files(id, name, mimeType)", "pageSize": 50, "spaces": "drive"}
	if drive_id:
		kwargs.update(
			{"supportsAllDrives": True, "includeItemsFromAllDrives": True, "corpora": "drive", "driveId": drive_id}
		)
	return service.files().list(**kwargs).execute().get("files", [])


def _move_file(service, file_id, from_folder, to_folder):
	service.files().update(
		fileId=file_id, addParents=to_folder, removeParents=from_folder, supportsAllDrives=True, fields="id"
	).execute()


# --- Mobile channel ------------------------------------------------------


@frappe.whitelist()
def ingest_mobile_photo(file_url, document_type=None):
	"""Mobile capture: ingest a photo/scan uploaded from a phone."""
	if not (set(frappe.get_roles()) & _MOBILE_ROLES):
		frappe.throw(_("Not permitted to upload accounting documents."), frappe.PermissionError)
	if not file_url:
		frappe.throw(_("file_url is required"))
	return {"name": intake.ingest_document(file_url=file_url, source_channel="Mobile", document_type=document_type)}


# --- Scheduler maintenance ----------------------------------------------


def retry_failed_intakes():
	"""Daily: re-enqueue Failed Accounting Intake Log steps that carry a retry
	payload (extraction / posting). Mirrors ``drive_sync.retry_failed_syncs``."""
	rows = frappe.get_all(
		"Accounting Intake Log",
		filters={"status": "Failed", "attempts": ["<", _MAX_RETRY], "payload": ["is", "set"]},
		fields=["name", "payload"],
		limit_page_length=200,
	)
	for row in rows:
		try:
			payload = json.loads(row.payload)
			method = payload.get("method", "")
			if not method.startswith("erpnext_enhancements."):
				continue
			frappe.enqueue(method, queue="long", **(payload.get("kwargs") or {}))
			frappe.db.set_value("Accounting Intake Log", row.name, "status", "Skipped", update_modified=False)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Accounting Intake Retry")


def purge_old_intake_logs(days=90):
	"""Daily: delete Accounting Intake Log rows older than ``days``."""
	cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -days)
	frappe.db.delete("Accounting Intake Log", {"creation": ["<", cutoff]})
	frappe.db.commit()
