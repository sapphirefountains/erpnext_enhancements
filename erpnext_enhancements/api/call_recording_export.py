"""Mirror call recordings + voicemails into the Operations Shared Drive.

Every recorded call ingested by ``api.telephony.process_unified_recording``
and every Twilio voicemail ingested by
``api.call_intelligence.process_call_intelligence`` is uploaded, in a
background job, to Google Drive under monthly folders::

	<Triton Settings.call_recordings_drive_folder>/
		2026_06/
			2026-06-12 1530 — Inbound — Arthur Pendelton (+18015551234) — CAxxxx.wav
			Voicemail — 2026-06-12 0907 — Inbound — +13855022007 — CAyyyy.wav

Authentication reuses the project-folders **service account**
(``google_drive.drive_utils`` / "Project Folder Google Drive Settings");
that account must be a member (Content Manager) of the Shared Drive that
contains the configured folder. The export is OFF until
``Triton Settings.call_recordings_drive_folder`` is filled in — that field is
the feature switch.

Audio sources: answered calls are read back from the private File already
saved on the call's Communication; voicemails are fetched from Twilio with
the account's Basic-auth credentials (Triton Settings), since their bytes
never reach ERPNext otherwise.

Idempotent per call: filenames embed the Twilio Call SID, and a file with the
same SID already in the month folder short-circuits the job (Triton retries
its webhooks on failures).
"""

import io

import frappe
import requests
from frappe.utils import get_datetime

from erpnext_enhancements.google_drive.drive_utils import (
	create_folder,
	find_folder,
	get_drive_service,
)


def _configured_folder():
	"""The recordings parent folder — configured on Project Folder Google
	Drive Settings (the app's Google Drive home, alongside the service account
	and the customer/project folder options); the original Triton Settings
	field is honoured as a fallback."""
	drive_settings = frappe.get_cached_doc("Project Folder Google Drive Settings")
	folder = (drive_settings.get("call_recordings_folder_id") or "").strip()
	if folder:
		return folder
	settings = frappe.get_cached_doc("Triton Settings")
	return (settings.get("call_recordings_drive_folder") or "").strip()


def enqueue_recording_export(**kwargs):
	"""Queue the Drive upload when the feature is configured. Best-effort and
	swallow-all: the calling telephony webhooks must never fail over an export."""
	try:
		if not _configured_folder() or not kwargs.get("call_sid"):
			return
		frappe.enqueue(
			"erpnext_enhancements.api.call_recording_export.export_call_recording",
			queue="long",
			**kwargs,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Call Recording Export enqueue")


def _direction_label(direction):
	return (
		"Outbound"
		if str(direction or "").strip().lower() in ("outbound", "outgoing", "out")
		else "Inbound"
	)


def _drive_filename(call_sid, when, direction, caller_name, caller_number, is_voicemail):
	stamp = get_datetime(when or frappe.utils.now()).strftime("%Y-%m-%d %H%M")
	caller_name = (caller_name or "").strip()
	caller_number = (caller_number or "").strip()
	if caller_name and caller_number:
		who = f"{caller_name} ({caller_number})"
	else:
		who = caller_name or caller_number or "Unknown"
	prefix = "Voicemail — " if is_voicemail else ""
	# The SID keeps names unique and lets retried webhooks dedupe.
	return f"{prefix}{stamp} — {_direction_label(direction)} — {who} — {call_sid}.wav"


def _drive_id_of(service, folder_id):
	"""The Shared Drive id containing ``folder_id`` (None for My Drive)."""
	meta = (
		service.files()
		.get(fileId=folder_id, fields="driveId", supportsAllDrives=True)
		.execute()
	)
	return (meta or {}).get("driveId")


def _month_folder(service, parent_id, drive_id, when):
	"""Find-or-create the YYYY_MM folder under the configured parent; the id is
	cached for a day so steady-state exports cost one list query less."""
	name = get_datetime(when or frappe.utils.now()).strftime("%Y_%m")
	cache_key = f"call_rec_drive_folder::{parent_id}::{name}"
	cached = frappe.cache().get_value(cache_key)
	if cached:
		return cached
	folder_id = find_folder(service, name, parent_id, drive_id)
	if not folder_id:
		folder_id, _link = create_folder(service, name, parent_id, drive_id)
	frappe.cache().set_value(cache_key, folder_id, expires_in_sec=86400)
	return folder_id


def _already_uploaded(service, folder_id, drive_id, call_sid, is_voicemail):
	kwargs = {
		"q": f"name contains '{call_sid}' and '{folder_id}' in parents and trashed=false",
		"fields": "files(id, name)",
		"pageSize": 5,
	}
	if drive_id:
		kwargs.update(
			supportsAllDrives=True,
			includeItemsFromAllDrives=True,
			corpora="drive",
			driveId=drive_id,
		)
	files = service.files().list(**kwargs).execute().get("files", [])
	# A call has either a recording or a voicemail, but match the kind anyway.
	return any(f.get("name", "").startswith("Voicemail") == bool(is_voicemail) for f in files)


def _voicemail_audio(twilio_audio_url):
	"""Download voicemail audio from Twilio (Basic auth from Triton Settings)."""
	settings = frappe.get_doc("Triton Settings")
	account_sid = (
		getattr(settings, "twilio_account_sid", None)
		or frappe.conf.get("twilio_account_sid")
		or ""
	)
	auth_token = settings.get_password("twilio_auth_token", raise_exception=False) or ""
	url = twilio_audio_url if twilio_audio_url.endswith(".wav") else twilio_audio_url + ".wav"
	resp = requests.get(url, auth=(account_sid, auth_token), timeout=60)
	resp.raise_for_status()
	return resp.content


def export_call_recording(
	call_sid,
	when=None,
	direction=None,
	caller_name=None,
	caller_number=None,
	file_docname=None,
	twilio_audio_url=None,
	is_voicemail=False,
	summary=None,
	transcript=None,
	attempts=1,
):
	"""Background job: upload one call recording / voicemail to the monthly
	Drive folder, plus a companion ``.txt`` with the summary + transcript so
	the month folder is browsable without opening ERPNext. Audio comes from
	the saved ERPNext File (answered calls) or straight from Twilio
	(voicemails). Failures land in the Error Log and as a Failed Drive Sync
	Log row whose payload the nightly retry job re-enqueues."""
	from erpnext_enhancements.google_drive.drive_sync import log_sync

	name = None
	try:
		parent_id = _configured_folder()
		if not parent_id or not call_sid:
			return

		audio = None
		if file_docname and frappe.db.exists("File", file_docname):
			audio = frappe.get_doc("File", file_docname).get_content()
			if isinstance(audio, str):
				audio = audio.encode("utf-8")
		elif twilio_audio_url:
			audio = _voicemail_audio(twilio_audio_url)
		if not audio:
			return

		from googleapiclient.http import MediaIoBaseUpload

		service, _project_drive_id = get_drive_service()
		drive_id = _drive_id_of(service, parent_id)
		folder_id = _month_folder(service, parent_id, drive_id, when)

		if _already_uploaded(service, folder_id, drive_id, call_sid, is_voicemail):
			return

		name = _drive_filename(call_sid, when, direction, caller_name, caller_number, is_voicemail)
		media = MediaIoBaseUpload(io.BytesIO(audio), mimetype="audio/wav", resumable=True)
		created = service.files().create(
			body={"name": name, "parents": [folder_id]},
			media_body=media,
			supportsAllDrives=True,
			fields="id, webViewLink",
		).execute()

		# Companion text file: summary + transcript next to the audio.
		text_parts = []
		if (summary or "").strip():
			text_parts.append(f"Summary\n-------\n{summary.strip()}")
		if (transcript or "").strip():
			text_parts.append(f"Transcript\n----------\n{transcript.strip()}")
		if text_parts:
			txt_media = MediaIoBaseUpload(
				io.BytesIO("\n\n".join(text_parts).encode("utf-8")),
				mimetype="text/plain",
				resumable=False,
			)
			service.files().create(
				body={"name": name.rsplit(".", 1)[0] + ".txt", "parents": [folder_id]},
				media_body=txt_media,
				supportsAllDrives=True,
				fields="id",
			).execute()

		log_sync(
			"Recording Export", "Success",
			reference_doctype="Call Log", reference_name=call_sid,
			file_name=name, drive_file_id=created.get("id"),
			drive_link=created.get("webViewLink"), attempts=attempts,
		)
	except Exception:
		frappe.log_error(
			f"Drive export failed for {call_sid} (voicemail={bool(is_voicemail)})\n"
			f"{frappe.get_traceback()}",
			"Call Recording Export",
		)
		log_sync(
			"Recording Export", "Failed",
			reference_doctype="Call Log", reference_name=call_sid,
			file_name=name, error=frappe.get_traceback(),
			payload={
				"method": "erpnext_enhancements.api.call_recording_export.export_call_recording",
				"kwargs": {
					"call_sid": call_sid, "when": str(when) if when else None,
					"direction": direction, "caller_name": caller_name,
					"caller_number": caller_number, "file_docname": file_docname,
					"twilio_audio_url": twilio_audio_url,
					"is_voicemail": bool(is_voicemail), "summary": summary,
					"transcript": transcript, "attempts": attempts + 1,
				},
			},
			attempts=attempts,
		)
