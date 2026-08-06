"""Two-way attachment sync between ERPNext and the Google Drive folders.

Once a Project / Customer / Opportunity is linked to a Drive folder
(``custom_drive_folder_id``, set by the provisioning in ``drive_utils`` or the
backfill below), this module keeps attachments and Drive files in step:

* **ERPNext → Drive** — a ``File`` ``after_insert`` hook uploads every new
  attachment on a linked document into its Drive folder (background job).
  The Drive file id is stamped on ``File.custom_drive_file_id``.
* **Drive → ERPNext** — an hourly job walks every linked folder's whole tree
  (files **and** subfolders, nested) and creates **link-only shadow
  attachments** for Drive items ERPNext doesn't know yet: a ``File`` row whose
  ``file_url`` is the Drive ``webViewLink`` (no bytes copied — Drive stays the
  source of truth). Subfolders are mirrored as link-only ``File`` rows too, so
  the folder structure is visible on the document; nested file names are
  path-prefixed. Shadows are recognised by their stamped
  ``custom_drive_file_id``, which also prevents echo loops with the upload hook.
* **Deletions never propagate** in either direction: a shadow whose Drive
  file disappeared is flagged ``Stale`` in the Drive Sync Log, nothing is
  deleted automatically.
* **Link reconciliation** — a daily job (:func:`reconcile_drive_links`) probes
  every record's ``custom_drive_folder_id`` and stamps
  ``custom_drive_folder_missing`` on the ones whose folder is gone, so the
  "Open Drive Folder" button can stop sending people to a Google 404. Runs
  whether or not attachment sync is on, and clears itself when a folder comes
  back.

Every action writes a ``Drive Sync Log`` row; Failed rows carry a retry
payload that :func:`retry_failed_syncs` (daily) re-enqueues up to 3 attempts.
The whole feature is opt-in via ``Project Folder Google Drive Settings →
Enable Attachment Sync``.

Also here: :func:`test_connection` (settings-form button validating the
service account, Drive API, and access to each configured ID) and
:func:`backfill_drive_links` (links pre-existing customers/projects to their
already-existing folders by name — never creates folders).
"""

import io
import json
import mimetypes

import frappe
from frappe.utils import cint
from googleapiclient.errors import HttpError

from erpnext_enhancements.google_drive.drive_utils import (
	find_folder,
	get_drive_service,
	get_folder_meta,
)
from erpnext_enhancements.utils.error_throttle import log_error_throttled

# Doctypes with a linked-folder field, in sync scope.
SYNCED_DOCTYPES = {
	"Project": "custom_drive_folder_id",
	"Customer": "custom_drive_folder_id",
	"Opportunity": "custom_drive_folder_id",
}

MAX_RETRY_ATTEMPTS = 3

# Passed as `num_retries` to every read call in the hourly shadow walk.
#
# The Drive API answers a plain metadata GET with `HttpError 500 "Unknown
# Error." Details: "[{}]"` often enough that Google documents it as expected and
# tells clients to retry with exponential backoff. We were not retrying, so each
# blip aborted one customer's shadow sync and wrote an Error Log row — 61 of
# them in a month, every one for a folder that was perfectly healthy on the next
# attempt. googleapiclient's own retry handles 429/500/502/503/504 with
# randomised backoff, which is exactly the policy Google asks for; there is no
# reason to hand-roll it.
GOOGLE_API_RETRIES = 4

# Driver error codes meaning "this connection is dead", as opposed to "that query
# was bad": 2006 server has gone away, 2013 lost connection during query, 2055
# lost connection to server. The shadow sync meets these because it is the one
# job here that holds a DB connection across minutes of uninterrupted Google API
# traffic — see :func:`_recover_after_document_failure`.
LOST_CONNECTION_ERRORS = (2006, 2013, 2055)


def log_sync(action, status, reference_doctype=None, reference_name=None,
			file_name=None, drive_file_id=None, drive_link=None, error=None,
			payload=None, attempts=1):
	"""Write one Drive Sync Log row. Never raises — logging must not break
	the action being logged."""
	try:
		frappe.get_doc({
			"doctype": "Drive Sync Log",
			"action": action,
			"status": status,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"file_name": file_name,
			"drive_file_id": drive_file_id,
			"drive_link": drive_link,
			"error": (error or "")[:1000] or None,
			"payload": json.dumps(payload) if payload else None,
			"attempts": attempts,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Drive Sync Log write failed")


def _settings():
	return frappe.get_cached_doc("Project Folder Google Drive Settings")


def _sync_enabled(settings=None):
	settings = settings or _settings()
	return bool(
		cint(settings.get("attachment_sync_enabled"))
		and settings.get("service_account_json")
	)


def _drive_id_of(service, folder_id):
	"""The Shared Drive id containing ``folder_id`` (None for My Drive)."""
	meta = (
		service.files()
		.get(fileId=folder_id, fields="driveId", supportsAllDrives=True)
		.execute(num_retries=GOOGLE_API_RETRIES)
	)
	return (meta or {}).get("driveId")


# ------------------------------------------------------------------ ERPNext → Drive


def on_file_attached(doc, method=None):
	"""``File`` ``after_insert`` doc_event: queue the Drive upload for
	attachments on linked documents. Cheap bail-outs first — this hook runs
	for every File on the site."""
	try:
		folder_field = SYNCED_DOCTYPES.get(doc.get("attached_to_doctype"))
		if not folder_field or not doc.get("attached_to_name"):
			return
		# Shadows (and anything already mirrored) carry the Drive id — never
		# re-upload them, that's the echo loop.
		if doc.get("custom_drive_file_id"):
			return
		file_url = doc.get("file_url") or ""
		if file_url.startswith("http"):
			return  # remote/link files (incl. Drive shadows) have no bytes to upload
		settings = _settings()
		if not _sync_enabled(settings):
			return
		folder_id = frappe.db.get_value(
			doc.attached_to_doctype, doc.attached_to_name, folder_field
		)
		if not folder_id:
			return
		frappe.enqueue(
			"erpnext_enhancements.google_drive.drive_sync.upload_attachment_to_drive",
			queue="long",
			file_docname=doc.name,
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Drive attachment sync enqueue")


def upload_attachment_to_drive(file_docname, attempts=1):
	"""Background job: mirror one ERPNext attachment into its document's
	Drive folder and stamp the Drive id back on the File."""
	reference_doctype = reference_name = file_name = None
	try:
		if not frappe.db.exists("File", file_docname):
			return
		file_doc = frappe.get_doc("File", file_docname)
		reference_doctype = file_doc.attached_to_doctype
		reference_name = file_doc.attached_to_name
		file_name = file_doc.file_name or file_docname
		if file_doc.get("custom_drive_file_id"):
			return  # already mirrored (job retry / duplicate event)

		folder_field = SYNCED_DOCTYPES.get(reference_doctype)
		folder_id = folder_field and frappe.db.get_value(
			reference_doctype, reference_name, folder_field
		)
		if not folder_id:
			return

		content = file_doc.get_content()
		if isinstance(content, str):
			content = content.encode("utf-8")
		if not content:
			return

		from googleapiclient.http import MediaIoBaseUpload

		service, _drive = get_drive_service()
		mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
		media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=True)
		created = service.files().create(
			body={"name": file_name, "parents": [folder_id]},
			media_body=media,
			supportsAllDrives=True,
			fields="id, webViewLink",
		).execute()

		file_doc.db_set("custom_drive_file_id", created.get("id"), update_modified=False)
		log_sync(
			"Upload to Drive", "Success",
			reference_doctype=reference_doctype, reference_name=reference_name,
			file_name=file_name, drive_file_id=created.get("id"),
			drive_link=created.get("webViewLink"), attempts=attempts,
		)
	except Exception:
		frappe.log_error(
			f"Drive upload failed for File {file_docname}\n{frappe.get_traceback()}",
			"Drive Attachment Sync",
		)
		log_sync(
			"Upload to Drive", "Failed",
			reference_doctype=reference_doctype, reference_name=reference_name,
			file_name=file_name, error=frappe.get_traceback(),
			payload={
				"method": "erpnext_enhancements.google_drive.drive_sync.upload_attachment_to_drive",
				"kwargs": {"file_docname": file_docname, "attempts": attempts + 1},
			},
			attempts=attempts,
		)


# ------------------------------------------------------------------ Drive → ERPNext


def sync_shadow_attachments():
	"""Hourly scheduler entry: hand the Drive→ERPNext shadow walk to a long
	worker. Walking every linked document's whole Drive tree (a network round-trip
	per folder) and inserting shadows must not run on a short 300s worker — a
	large first-time sync blew that budget mid-insert (PRJ-00275)."""
	if not _sync_enabled():
		return
	frappe.enqueue(
		"erpnext_enhancements.google_drive.drive_sync.run_shadow_sync",
		queue="long",
		timeout=3600,
	)


def _is_lost_connection(exc):
	"""True when ``exc`` is the DB connection dying rather than a rejected query.

	Read off the driver's error code rather than the message: Frappe surfaces
	MySQLdb's ``OperationalError`` unwrapped, and the code is stable across the
	wordings ("Server has gone away" / "Lost connection to server during query")
	that the same underlying event produces."""
	args = getattr(exc, "args", None) or ()
	return bool(args) and args[0] in LOST_CONNECTION_ERRORS


def _reconnect_db():
	"""Re-open the site DB connection after it was lost mid-run. True if usable.

	Frappe *asks* for auto-reconnect (``conn.auto_reconnect = True`` in the
	MariaDB driver's ``get_connection``), but mysqlclient dropped that feature —
	the assignment is an inert attribute set on a Python object, so a dead
	connection stays dead for the rest of the job unless we rebuild it here.
	``close()`` is best-effort: closing an already-dead socket can itself raise,
	and it leaves ``_conn`` populated when it does, which ``connect()`` then
	overwrites anyway."""
	try:
		frappe.db.close()
	except Exception:
		pass
	try:
		frappe.db.connect()
		return True
	except Exception:
		return False


def _recover_after_document_failure(exc):
	"""Put the DB back in a usable state after one document's sync failed, so the
	run can carry on with the next document. False means the run must give up.

	The lost-connection case is why this exists. ``frappe.db.rollback()`` issues
	SQL, so calling it on a dead connection raises *the same error again* — out
	of the ``except`` block that was meant to contain it. That is how a single
	document's failure aborted the entire hourly run, and it took
	``frappe.log_error`` down with it (which also needs the connection), so the
	only evidence was an RQ traceback: nothing in the Error Log, nothing in the
	Drive Sync Log."""
	if _is_lost_connection(exc):
		return _reconnect_db()
	try:
		frappe.db.rollback()
	except Exception:
		# Rollback failing for any other reason still leaves the session
		# unusable; a fresh connection is the only way to continue.
		return _reconnect_db()
	return True


def run_shadow_sync():
	"""Background worker: for every linked document, walk its Drive folder
	tree and create link-only shadow attachments for the files *and subfolders*
	ERPNext doesn't have yet, and flag shadows whose Drive item vanished as Stale
	(never deleting anything). One document's failure (e.g. its linked folder was
	deleted, or the DB connection dropped during its Drive walk) is logged and
	skipped — it never aborts the whole run, and a commit-per-document keeps
	finished work durable if a later one fails."""
	settings = _settings()
	if not _sync_enabled(settings):
		return
	try:
		service, _drive = get_drive_service()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Drive Shadow Sync (service)")
		return

	drive_id_cache = {}
	for doctype, folder_field in SYNCED_DOCTYPES.items():
		if not frappe.db.has_column(doctype, folder_field):
			continue
		for row in frappe.get_all(
			doctype, filters={folder_field: ["is", "set"]}, fields=["name", folder_field]
		):
			try:
				_sync_folder_shadows(
					service, doctype, row.name, row.get(folder_field), drive_id_cache
				)
				frappe.db.commit()
			except Exception as exc:
				traceback = frappe.get_traceback()
				# Recover *before* logging: frappe.log_error writes to the DB too,
				# so on a dropped connection it would raise on its way out.
				if not _recover_after_document_failure(exc):
					return
				# Throttled per doctype: this is the inner loop over every record
				# with a linked folder, so anything systemic (revoked service
				# account, Drive outage) writes a row per record per hourly run.
				# Keying on the doctype keeps a Customer-wide failure from
				# masking an unrelated Project one.
				log_error_throttled(
					f"Shadow sync failed for {doctype} {row.name}\n{traceback}",
					"Drive Shadow Sync",
					key=doctype,
				)


# Google Drive's folder mime type, and a hard cap on how deep the shadow walk
# recurses — a guard against Drive-shortcut cycles and pathological nesting.
FOLDER_MIME = "application/vnd.google-apps.folder"
MAX_SHADOW_DEPTH = 10
# File.file_name is Data(140). A deep tree's path-prefixed shadow name can exceed that;
# inserting it raises CharacterLengthExceededError and fails the whole sync, so the
# shadow name is capped to this length (keeping the meaningful tail).
MAX_FILE_NAME_LENGTH = 140


def _list_folder_children(service, folder_id, drive_id):
	"""All non-trashed children — files **and** subfolders — directly inside
	``folder_id`` (following ``nextPageToken`` across pages)."""
	children, page_token = [], None
	kwargs = {
		"q": f"'{folder_id}' in parents and trashed=false",
		"fields": "nextPageToken, files(id, name, webViewLink, mimeType)",
		"pageSize": 200,
	}
	if drive_id:
		kwargs.update(
			supportsAllDrives=True,
			includeItemsFromAllDrives=True,
			corpora="drive",
			driveId=drive_id,
		)
	while True:
		if page_token:
			kwargs["pageToken"] = page_token
		result = service.files().list(**kwargs).execute(num_retries=GOOGLE_API_RETRIES)
		children.extend(result.get("files", []))
		page_token = result.get("nextPageToken")
		if not page_token:
			return children


def _walk_drive_folder(service, folder_id, drive_id, rel_path, depth, visited, items):
	"""Depth-first walk of ``folder_id``'s tree. Every descendant — files and
	subfolders alike — is appended to ``items`` as a ``(drive_file, rel_path)``
	pair, where ``rel_path`` is the "/"-joined names of the parent folders below
	the linked root ("" at the top level). Guards against Drive-shortcut cycles
	(``visited``) and runaway nesting (``MAX_SHADOW_DEPTH``); a subfolder that
	404s mid-walk (moved/deleted) is skipped, not fatal."""
	if depth > MAX_SHADOW_DEPTH or folder_id in visited:
		return
	visited.add(folder_id)
	try:
		children = _list_folder_children(service, folder_id, drive_id)
	except HttpError as exc:
		if exc.resp.status == 404:
			return
		raise
	for child in children:
		items.append((child, rel_path))
		if child.get("mimeType") == FOLDER_MIME:
			_walk_drive_folder(
				service, child["id"], drive_id,
				f"{rel_path}{child.get('name')}/", depth + 1, visited, items,
			)


def _flag_missing_drive_item(doctype, docname, drive_file_id, file_name, message,
			action="Shadow Attachment"):
	"""Record a single ``Stale`` Drive Sync Log row for a Drive item that can no
	longer be reached (a linked root folder, or a shadow's target). Idempotent —
	keyed on the Drive id, so the hourly job doesn't spam duplicate rows."""
	if frappe.db.exists("Drive Sync Log", {
		"action": action,
		"status": "Stale",
		"drive_file_id": drive_file_id,
	}):
		return
	log_sync(
		action, "Stale",
		reference_doctype=doctype, reference_name=docname,
		file_name=file_name, drive_file_id=drive_file_id, error=message,
	)


def _flag_missing_root_folder(doctype, docname, folder_id):
	"""The linked root folder itself is unreachable: log it once *and* stamp the
	record, so the "Open Drive Folder" button stops offering a link into a Google
	404. The hourly walk gets here first for documents it syncs; the daily
	:func:`run_drive_link_reconcile` covers the rest."""
	_flag_missing_drive_item(doctype, docname, folder_id, None, FOLDER_GONE_MSG)
	set_folder_missing(doctype, docname, 1)


def _sync_folder_shadows(service, doctype, docname, folder_id, drive_id_cache):
	# Resolve the Shared Drive id once per root folder. A 404 here means the
	# linked folder itself was deleted or moved out of the service account's
	# reach — flag it once and bail for this document rather than letting the
	# error abort the hourly run (this is what crashed PRJ-00694 every hour).
	if folder_id not in drive_id_cache:
		try:
			drive_id_cache[folder_id] = _drive_id_of(service, folder_id)
		except HttpError as exc:
			if exc.resp.status == 404:
				_flag_missing_root_folder(doctype, docname, folder_id)
				return
			raise
	drive_id = drive_id_cache[folder_id]

	# Walk the whole tree under the linked folder — files and subfolders, nested.
	items = []
	try:
		_walk_drive_folder(service, folder_id, drive_id, "", 0, set(), items)
	except HttpError as exc:
		if exc.resp.status == 404:
			_flag_missing_root_folder(doctype, docname, folder_id)
			return
		raise

	drive_ids = [f["id"] for f, _rel in items]

	# Everything ERPNext already knows about (uploads we mirrored + shadows),
	# keyed on the stamped Drive id — covers both files and folders.
	known_ids = set(
		frappe.get_all(
			"File",
			filters={"custom_drive_file_id": ["in", drive_ids]},
			pluck="custom_drive_file_id",
		)
		if drive_ids
		else []
	)

	for drive_file, rel_path in items:
		if drive_file["id"] in known_ids:
			continue
		is_folder = drive_file.get("mimeType") == FOLDER_MIME
		# Path-prefixed so the flat attachment list stays legible; a trailing
		# slash marks folders. Link-only either way — no bytes are copied.
		display_name = f"{rel_path}{drive_file.get('name')}" + ("/" if is_folder else "")
		# Cap to the File.file_name limit, keeping the tail (the real file/folder name is
		# more useful than the leading path) so a deeply-nested item can't crash the sync.
		if len(display_name) > MAX_FILE_NAME_LENGTH:
			display_name = "..." + display_name[-(MAX_FILE_NAME_LENGTH - 3):]
		# Insert unattached, then link via db_set. Inserting with attached_to_*
		# set fires File.after_insert -> create_attachment_record, which adds an
		# "Attachment" comment to the reference doc and publishes realtime per
		# file — that per-shadow overhead is what timed the job out mid-insert
		# (PRJ-00275), and on a first-time sync it spams the document timeline
		# with one "Added <file>" comment per shadow. db_set runs no hooks.
		shadow = frappe.get_doc({
			"doctype": "File",
			"file_name": display_name,
			"file_url": drive_file.get("webViewLink"),
			"is_private": 1,
			"custom_drive_file_id": drive_file["id"],
		})
		shadow.flags.ignore_permissions = True
		shadow.insert(ignore_permissions=True)
		shadow.db_set("attached_to_doctype", doctype, update_modified=False)
		shadow.db_set("attached_to_name", docname, update_modified=False)
		log_sync(
			"Shadow Attachment", "Success",
			reference_doctype=doctype, reference_name=docname,
			file_name=display_name, drive_file_id=drive_file["id"],
			drive_link=drive_file.get("webViewLink"),
		)

	# Stale detection: shadows pointing at Drive items no longer anywhere in the
	# tree. Flag once (deletions never propagate).
	shadows = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": doctype,
			"attached_to_name": docname,
			"custom_drive_file_id": ["is", "set"],
			"file_url": ["like", "%drive.google.com%"],
		},
		fields=["name", "file_name", "custom_drive_file_id"],
	)
	listed = set(drive_ids)
	for shadow_row in shadows:
		if shadow_row.custom_drive_file_id in listed:
			continue
		_flag_missing_drive_item(
			doctype, docname, shadow_row.custom_drive_file_id, shadow_row.file_name,
			"The Drive file behind this shadow attachment was moved or deleted.",
		)


# ------------------------------------------------------------------ link reconciliation

# Check field stamped on the record when its linked folder can no longer be
# reached. Deliberately separate from ``custom_drive_folder_id``: the id is kept
# so a restored folder can be recognised, and so the Drive Link Manager can still
# show what the record used to point at.
MISSING_FLAG_FIELD = "custom_drive_folder_missing"

FOLDER_GONE_MSG = (
	"The linked Drive folder no longer exists or is not shared with the "
	"service account."
)
FOLDER_TRASHED_MSG = "The linked Drive folder is in the Drive trash."


def _drive_configured(settings=None):
	"""Whether Drive is set up at all. Deliberately *not* ``_sync_enabled``: the
	"Open Drive Folder" button works whether or not attachment sync is on, so its
	links need reconciling either way. Truthiness on the raw Password field is
	safe — the placeholder is non-empty exactly when a key is stored."""
	settings = settings or _settings()
	return bool(settings.get("service_account_json"))


def reconcile_drive_links():
	"""Daily scheduler entry: hand the linked-folder probe to a long worker.

	782 linked records at a Drive round-trip each will not fit a 300s worker, and
	this is the same shape of walk as the shadow sync — so it gets the same
	treatment."""
	if not _drive_configured():
		return
	frappe.enqueue(
		"erpnext_enhancements.google_drive.drive_sync.run_drive_link_reconcile",
		queue="long",
		timeout=3600,
	)


def _probe_folder(service, folder_id, shared_drive_id):
	"""``(gone, reason)`` for one linked folder id.

	Trashed counts as gone. A folder in the Shared Drive trash still resolves for
	the API, but it is not a place a user can be sent — and "can I send someone
	here" is the only question the button needs answered."""
	meta = get_folder_meta(service, folder_id, shared_drive_id)
	if meta is None:
		return True, FOLDER_GONE_MSG
	if meta.get("trashed"):
		return True, FOLDER_TRASHED_MSG
	return False, None


def set_folder_missing(doctype, docname, missing):
	"""Set the record's missing-folder flag; True when it actually changed.

	Column-guarded: scheduler jobs run during ERPNext's own test bootstrap, before
	this app's custom fields exist."""
	if not frappe.db.has_column(doctype, MISSING_FLAG_FIELD):
		return False
	current = cint(frappe.db.get_value(doctype, docname, MISSING_FLAG_FIELD))
	if current == cint(missing):
		return False
	frappe.db.set_value(
		doctype, docname, MISSING_FLAG_FIELD, cint(missing), update_modified=False
	)
	return True


def _clear_stale_log_rows(drive_file_id):
	"""Mark a Drive id's ``Stale`` rows consumed once the folder is reachable
	again, so a later disappearance logs a fresh one — ``_flag_missing_drive_item``
	dedupes on the id and would otherwise stay silent forever. ``Skipped`` is the
	same "this row has been dealt with" status :func:`retry_failed_syncs` uses."""
	for name in frappe.get_all(
		"Drive Sync Log",
		filters={"status": "Stale", "drive_file_id": drive_file_id},
		pluck="name",
	):
		frappe.db.set_value("Drive Sync Log", name, "status", "Skipped", update_modified=False)


def run_drive_link_reconcile():
	"""Background worker: probe every record's linked Drive folder and record on
	the record itself whether it is still there.

	The shadow sync already noticed dead folders, but only for documents it
	happened to walk, and only ever wrote the fact to the Drive Sync Log — nothing
	put it back on the record. So the "Open Drive Folder" button kept offering a
	link to a folder Google answers 404 for, with no way for the form to know
	(PRJ-00706, PRJ-00695, CRM-OPP-2026-00113). This closes that loop: the flag
	lives on the record, so the button reads it for free instead of paying a Drive
	round-trip per click.

	Both directions are applied. A folder restored from the Shared Drive trash, or
	re-shared with the service account, clears its own flag on the next run.

	Failure containment follows the shadow sync's contract: one record's failure is
	logged and skipped, never fatal to the run, and the DB is put back into a
	usable state *before* anything is written — see
	:func:`_recover_after_document_failure`.
	"""
	settings = _settings()
	if not _drive_configured(settings):
		return
	try:
		service, shared_drive_id = get_drive_service()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Drive Link Reconcile (service)")
		return

	checked = newly_missing = restored = 0
	for doctype, folder_field in SYNCED_DOCTYPES.items():
		if not frappe.db.has_column(doctype, folder_field):
			continue
		for row in frappe.get_all(
			doctype, filters={folder_field: ["is", "set"]}, fields=["name", folder_field]
		):
			folder_id = row.get(folder_field)
			try:
				gone, reason = _probe_folder(service, folder_id, shared_drive_id)
				if gone:
					if set_folder_missing(doctype, row.name, 1):
						newly_missing += 1
					_flag_missing_drive_item(
						doctype, row.name, folder_id, None, reason, action="Reconcile Link"
					)
					frappe.db.commit()
				elif set_folder_missing(doctype, row.name, 0):
					_clear_stale_log_rows(folder_id)
					restored += 1
					frappe.db.commit()
				checked += 1
			except Exception as exc:
				traceback = frappe.get_traceback()
				# Recover *before* logging: frappe.log_error writes to the DB too,
				# so on a dropped connection it would raise on its way out.
				if not _recover_after_document_failure(exc):
					return
				frappe.log_error(
					f"Drive link reconcile failed for {doctype} {row.name}\n{traceback}",
					"Drive Link Reconcile",
				)

	log_sync(
		"Reconcile Link", "Success",
		file_name=f"Checked {checked} — {newly_missing} newly missing, {restored} restored",
	)


@frappe.whitelist()
def check_drive_links():
	"""Settings-form button: run the linked-folder reconciliation now instead of
	waiting for the daily pass — the point of finding a dead link is usually that
	someone is looking at it right now."""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"erpnext_enhancements.google_drive.drive_sync.run_drive_link_reconcile",
		queue="long",
		timeout=3600,
	)
	return {"status": "queued"}


# ------------------------------------------------------------------ retries


def retry_failed_syncs():
	"""Daily scheduler job: re-enqueue Failed Drive Sync Log rows that carry a
	retry payload, up to MAX_RETRY_ATTEMPTS. The shadow sync needs no retry —
	it is naturally idempotent and runs hourly."""
	rows = frappe.get_all(
		"Drive Sync Log",
		filters={
			"status": "Failed",
			"attempts": ["<", MAX_RETRY_ATTEMPTS],
			"payload": ["is", "set"],
		},
		fields=["name", "payload"],
		limit_page_length=200,
	)
	for row in rows:
		try:
			payload = json.loads(row.payload)
			if not payload.get("method", "").startswith("erpnext_enhancements."):
				continue
			frappe.enqueue(payload["method"], queue="long", **(payload.get("kwargs") or {}))
			# Mark the old row consumed so it isn't re-enqueued tomorrow; the
			# retried job writes a fresh row with the bumped attempt count.
			frappe.db.set_value("Drive Sync Log", row.name, "status", "Skipped", update_modified=False)
		except Exception:
			frappe.log_error(
				f"Drive sync retry failed for log {row.name}\n{frappe.get_traceback()}",
				"Drive Sync Retry",
			)


# ------------------------------------------------------------------ tools


@frappe.whitelist()
def test_connection():
	"""Settings-form button: validate the service-account JSON, the Drive API,
	and access to every configured Drive/folder. Returns per-check results."""
	frappe.only_for("System Manager")
	settings = frappe.get_single("Project Folder Google Drive Settings")
	checks = []
	client_email = None

	# Not settings.get(...): the field is a Password and returns a placeholder.
	from erpnext_enhancements.google_drive.drive_utils import get_service_account_info

	try:
		client_email = (get_service_account_info() or {}).get("client_email")
		checks.append({"check": "Service Account JSON parses", "ok": bool(client_email),
					"detail": client_email or "no client_email key"})
	except Exception as e:
		checks.append({"check": "Service Account JSON parses", "ok": False, "detail": str(e)})
		return {"service_account": None, "checks": checks}

	try:
		service, _drive = get_drive_service()
		service.about().get(fields="user").execute()
		checks.append({"check": "Drive API reachable", "ok": True, "detail": "authenticated"})
	except Exception as e:
		checks.append({"check": "Drive API reachable", "ok": False, "detail": str(e)[:300]})
		return {"service_account": client_email, "checks": checks}

	def check_id(label, file_id):
		if not file_id:
			checks.append({"check": label, "ok": None, "detail": "not configured"})
			return
		try:
			meta = service.files().get(
				fileId=file_id, fields="id, name, driveId", supportsAllDrives=True
			).execute()
			checks.append({"check": label, "ok": True, "detail": meta.get("name") or file_id})
		except Exception as e:
			checks.append({
				"check": label, "ok": False,
				"detail": f"Not accessible — add {client_email} to the Shared Drive. ({str(e)[:160]})",
			})

	check_id("Shared Drive accessible", (settings.get("shared_drive_id") or "").strip())
	check_id("Call Recordings folder accessible", (settings.get("call_recordings_folder_id") or "").strip())
	return {"service_account": client_email, "checks": checks}


@frappe.whitelist()
def backfill_drive_links():
	"""Settings-form button: queue the backfill that links existing Customers
	and Projects to their already-existing Drive folders by name (never
	creates folders)."""
	frappe.only_for("System Manager")
	frappe.enqueue(
		"erpnext_enhancements.google_drive.drive_sync.run_backfill_drive_links",
		queue="long",
	)
	return {"status": "queued"}


def run_backfill_drive_links():
	"""Find-by-name backfill: Customer folders at the Shared Drive root,
	Project folders inside their customer's folder (matched by the
	"<name> <project_name>" convention, then by project_name alone)."""
	try:
		service, shared_drive_id = get_drive_service()
		if not shared_drive_id:
			return
		linked = {"Customer": 0, "Project": 0}

		customers = frappe.get_all(
			"Customer",
			filters={"custom_drive_folder_id": ["is", "not set"]},
			fields=["name", "customer_name"],
		)
		customer_folder_ids = {}
		for customer in customers:
			label = customer.customer_name or customer.name
			folder_id = find_folder(service, label, shared_drive_id, shared_drive_id)
			if folder_id:
				frappe.db.set_value(
					"Customer", customer.name, "custom_drive_folder_id", folder_id,
					update_modified=False,
				)
				customer_folder_ids[customer.name] = folder_id
				linked["Customer"] += 1

		projects = frappe.get_all(
			"Project",
			filters={"custom_drive_folder_id": ["is", "not set"], "customer": ["is", "set"]},
			fields=["name", "project_name", "customer"],
		)
		for project in projects:
			parent = customer_folder_ids.get(project.customer) or frappe.db.get_value(
				"Customer", project.customer, "custom_drive_folder_id"
			)
			if not parent:
				continue
			folder_id = None
			for candidate in (f"{project.name} {project.project_name}", project.project_name, project.name):
				if candidate:
					folder_id = find_folder(service, candidate, parent, shared_drive_id)
					if folder_id:
						break
			if folder_id:
				frappe.db.set_value(
					"Project", project.name, "custom_drive_folder_id", folder_id,
					update_modified=False,
				)
				linked["Project"] += 1

		frappe.db.commit()
		log_sync(
			"Backfill", "Success",
			file_name=f"Linked {linked['Customer']} customers, {linked['Project']} projects",
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Drive Backfill")
		log_sync("Backfill", "Failed", error=frappe.get_traceback())
