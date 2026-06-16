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
)

# Doctypes with a linked-folder field, in sync scope.
SYNCED_DOCTYPES = {
	"Project": "custom_drive_folder_id",
	"Customer": "custom_drive_folder_id",
	"Opportunity": "custom_drive_folder_id",
}

MAX_RETRY_ATTEMPTS = 3


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
		.execute()
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
	"""Hourly scheduler job: for every linked document, walk its Drive folder
	tree and create link-only shadow attachments for the files *and subfolders*
	ERPNext doesn't have yet, and flag shadows whose Drive item vanished as Stale
	(never deleting anything). One document's failure (e.g. its linked folder was
	deleted) is logged and skipped — it never aborts the whole run."""
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
			except Exception:
				frappe.log_error(
					f"Shadow sync failed for {doctype} {row.name}\n{frappe.get_traceback()}",
					"Drive Shadow Sync",
				)
	frappe.db.commit()


# Google Drive's folder mime type, and a hard cap on how deep the shadow walk
# recurses — a guard against Drive-shortcut cycles and pathological nesting.
FOLDER_MIME = "application/vnd.google-apps.folder"
MAX_SHADOW_DEPTH = 10


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
		result = service.files().list(**kwargs).execute()
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


def _flag_missing_drive_item(doctype, docname, drive_file_id, file_name, message):
	"""Record a single ``Stale`` Drive Sync Log row for a Drive item that can no
	longer be reached (a linked root folder, or a shadow's target). Idempotent —
	keyed on the Drive id, so the hourly job doesn't spam duplicate rows."""
	if frappe.db.exists("Drive Sync Log", {
		"action": "Shadow Attachment",
		"status": "Stale",
		"drive_file_id": drive_file_id,
	}):
		return
	log_sync(
		"Shadow Attachment", "Stale",
		reference_doctype=doctype, reference_name=docname,
		file_name=file_name, drive_file_id=drive_file_id, error=message,
	)


def _sync_folder_shadows(service, doctype, docname, folder_id, drive_id_cache):
	missing_msg = (
		"The linked Drive folder no longer exists or is not shared with the "
		"service account."
	)
	# Resolve the Shared Drive id once per root folder. A 404 here means the
	# linked folder itself was deleted or moved out of the service account's
	# reach — flag it once and bail for this document rather than letting the
	# error abort the hourly run (this is what crashed PRJ-00694 every hour).
	if folder_id not in drive_id_cache:
		try:
			drive_id_cache[folder_id] = _drive_id_of(service, folder_id)
		except HttpError as exc:
			if exc.resp.status == 404:
				_flag_missing_drive_item(doctype, docname, folder_id, None, missing_msg)
				return
			raise
	drive_id = drive_id_cache[folder_id]

	# Walk the whole tree under the linked folder — files and subfolders, nested.
	items = []
	try:
		_walk_drive_folder(service, folder_id, drive_id, "", 0, set(), items)
	except HttpError as exc:
		if exc.resp.status == 404:
			_flag_missing_drive_item(doctype, docname, folder_id, None, missing_msg)
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
		shadow = frappe.get_doc({
			"doctype": "File",
			"file_name": display_name,
			"file_url": drive_file.get("webViewLink"),
			"attached_to_doctype": doctype,
			"attached_to_name": docname,
			"is_private": 1,
			"custom_drive_file_id": drive_file["id"],
		})
		shadow.flags.ignore_permissions = True
		shadow.insert(ignore_permissions=True)
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

	raw = settings.get("service_account_json") or ""
	try:
		client_email = json.loads(raw).get("client_email")
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
