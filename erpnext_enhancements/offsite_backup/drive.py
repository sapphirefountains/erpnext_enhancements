# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Google Drive v3 transport for the offsite backup module.

A deliberately small, dependency-free-of-Frappe layer: build a service, look at a
folder, push a file, list a folder, delete an object. Everything that knows what a
backup *is* lives in :mod:`erpnext_enhancements.offsite_backup.backup`; everything
that knows how to talk to Drive lives here.

**Separate from** :mod:`erpnext_enhancements.google_drive.drive_utils`, which
provisions per-project folders on a different Shared Drive with a different service
account. The two must not share a client: the backup key should be scoped to the
backup drive and nothing else, so that a compromise of the (widely-used) project
Drive credential does not also hand over the encrypted database dumps. Do not
"consolidate" these — the duplication is the isolation.

Two rules that this whole file exists to enforce:

* **Every call passes ``supportsAllDrives=True``** (and list calls add
  ``includeItemsFromAllDrives=True``). Omitted against a Shared Drive, the API
  reports the object as if it did not exist or as if the caller had no rights —
  the symptom reads exactly like a permissions problem, so it costs hours.
* **The retry budget is per-stall, not per-file.** A 20 GiB tarball is roughly a
  thousand 20 MiB chunks and can spend three hours on the wire. Five transient
  5xx responses spread over those three hours are normal Drive behaviour, not a
  broken upload; :func:`upload_file` therefore resets its attempt counter and its
  backoff every time a chunk lands. The budget only counts *consecutive* failures.
"""

import json
import time

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive"]

# 20 MiB. Must be a multiple of 256 KiB for a resumable upload; large enough that a
# multi-GB dump is a sane number of round trips, small enough that one retried
# chunk is not a ten-minute setback.
CHUNK_SIZE = 20 * 1024 * 1024

# Transient by Google's own definition — everything else is a real answer and
# retrying it just delays a failure the operator needs to see.
RETRYABLE_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 5
INITIAL_BACKOFF_SECONDS = 2

# Fields worth having back from an upload. ``md5Checksum`` is what lets the caller
# prove the bytes that landed are the bytes it sent.
UPLOAD_FIELDS = "id, name, size, md5Checksum, createdTime"
LIST_FIELDS = "nextPageToken, files(id, name, size, md5Checksum, createdTime, mimeType)"
FOLDER_FIELDS = "id, name, mimeType, driveId, capabilities"


# --------------------------------------------------------------------- auth


def get_drive_service(service_account_json):
	"""Build an authenticated Drive v3 client from a service-account key.

	``service_account_json`` is the full JSON key, as the string stored in the
	settings Password field (a already-parsed dict is accepted too).

	Service accounts only. A user OAuth credential pasted in here would authorise
	fine and then write backups into a *person's* My Drive, where they die with
	that person's account — so an ``"type"`` that is not ``service_account`` is
	rejected outright rather than half-working.

	No part of the key is ever placed in an exception message. The caller logs
	tracebacks without frame locals for the same reason.
	"""
	info = service_account_json
	if isinstance(info, (bytes, bytearray)):
		info = info.decode("utf-8")
	if isinstance(info, str):
		raw = info.strip()
		if not raw:
			raise ValueError(
				"No Google service account key is configured on Offsite Backup Settings."
			)
		try:
			info = json.loads(raw)
		except ValueError:
			raise ValueError(
				"The service account key on Offsite Backup Settings is not valid JSON. "
				"Paste the whole downloaded key file, including the outermost braces."
			) from None
	if not isinstance(info, dict):
		raise ValueError("The service account key must be a JSON object.")

	key_type = info.get("type")
	if key_type != "service_account":
		raise ValueError(
			"That Google credential is not a service account key (its \"type\" is "
			f"{key_type or 'missing'}). Offsite backups must authenticate as a service "
			"account: a user credential would write the backups into that person's "
			"My Drive, where they are lost the day the account is closed."
		)

	creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
	return build("drive", "v3", credentials=creds, cache_discovery=False)


# ------------------------------------------------------------------- retries


def _is_retryable(exc):
	"""True for failures that are worth waiting out.

	The HTTP statuses are the ones Google documents as retryable. The transport
	errors are here because an upload that holds a socket open for hours will meet
	a reset connection eventually, and that is the same class of event as a 503 —
	the request never reached a decision, so retrying it is safe rather than
	merely hopeful.
	"""
	if isinstance(exc, HttpError):
		return getattr(getattr(exc, "resp", None), "status", None) in RETRYABLE_STATUS
	# ConnectionError covers ConnectionReset/Aborted/BrokenPipe; TimeoutError covers
	# socket.timeout, which has been an alias for it since Python 3.10.
	return isinstance(exc, (ConnectionError, TimeoutError))


def _execute(request):
	"""Run one Drive request with bounded exponential backoff."""
	attempt = 0
	delay = INITIAL_BACKOFF_SECONDS
	while True:
		try:
			return request.execute()
		except Exception as exc:
			attempt += 1
			if attempt >= MAX_ATTEMPTS or not _is_retryable(exc):
				raise
			time.sleep(delay)
			delay *= 2


# --------------------------------------------------------------------- calls


def check_folder(service, folder_id):
	"""Metadata for the destination folder.

	``driveId`` is the reason this returns the whole object: it is present only
	when the folder lives on a Shared Drive, which is how the caller both proves
	the destination is a Shared Drive and gets the id needed to scope listings —
	without anybody having to store that id in a second settings field that can go
	stale when the folder moves.

	``capabilities`` carries ``canAddChildren`` / ``canDeleteChildren``, which are
	what actually decide whether uploads and pruning will work.
	"""
	return _execute(
		service.files().get(
			fileId=folder_id,
			fields=FOLDER_FIELDS,
			supportsAllDrives=True,
		)
	)


def upload_file(service, local_path, folder_id, remote_name):
	"""Resumable-upload one local file into ``folder_id``; return its metadata.

	Resumable rather than simple upload because these are multi-GB objects: a
	simple upload restarts from byte zero on any hiccup, which for a 20 GiB
	tarball means it may never finish at all.

	The retry counter and the backoff both reset after every chunk that lands.
	See the module docstring — the budget bounds a *stall*, not the whole
	transfer.
	"""
	# Explicit mimetype rather than letting MediaFileUpload guess from the
	# extension: these are opaque encrypted blobs, and a failed guess is a
	# constructor error rather than a sensible default.
	media = MediaFileUpload(
		local_path,
		mimetype="application/octet-stream",
		chunksize=CHUNK_SIZE,
		resumable=True,
	)
	try:
		request = service.files().create(
			body={"name": remote_name, "parents": [folder_id]},
			media_body=media,
			fields=UPLOAD_FIELDS,
			supportsAllDrives=True,
		)

		attempt = 0
		delay = INITIAL_BACKOFF_SECONDS
		response = None
		while response is None:
			try:
				_status, response = request.next_chunk()
			except Exception as exc:
				attempt += 1
				if attempt >= MAX_ATTEMPTS or not _is_retryable(exc):
					raise
				time.sleep(delay)
				delay *= 2
				continue
			# Progress was made. Whatever went wrong before this point is history.
			attempt = 0
			delay = INITIAL_BACKOFF_SECONDS
		return response
	finally:
		# MediaFileUpload holds the file open for the life of the transfer and has
		# no public close(); a long-lived worker that leaks one descriptor per
		# artefact per night eventually runs out.
		try:
			media.stream().close()
		except Exception:
			pass


def list_backup_files(service, folder_id, drive_id=None):
	"""Every non-trashed object directly inside ``folder_id``, newest first.

	Paginated to exhaustion on purpose: retention decides what to delete from this
	list, and a listing truncated at the first page would look like "only 100
	backups exist" and start deleting things that are not old.
	"""
	params = {
		"q": f"'{_escape(folder_id)}' in parents and trashed = false",
		"fields": LIST_FIELDS,
		"orderBy": "createdTime desc",
		"pageSize": 1000,
		"supportsAllDrives": True,
		"includeItemsFromAllDrives": True,
	}
	if drive_id:
		# Without corpora/driveId a Shared Drive listing silently searches the
		# service account's own (empty) My Drive and returns nothing.
		params["corpora"] = "drive"
		params["driveId"] = drive_id

	files = []
	page_token = None
	while True:
		response = _execute(service.files().list(pageToken=page_token, **params))
		files.extend(response.get("files") or [])
		page_token = response.get("nextPageToken")
		if not page_token:
			break
	return files


def delete_file(service, file_id):
	"""Permanently remove one Drive object."""
	return _execute(service.files().delete(fileId=file_id, supportsAllDrives=True))


def _escape(value):
	"""Escape a value for interpolation into a Drive ``q`` string."""
	return str(value or "").replace("\\", "\\\\").replace("'", "\\'")
