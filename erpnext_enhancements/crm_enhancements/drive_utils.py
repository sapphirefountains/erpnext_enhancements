"""Google Drive helpers for auto-provisioning project folders.

These functions wrap the Google Drive v3 API (``google-api-python-client``).
Authentication uses a **service account**: the JSON key and the target **Shared
Drive ID** are stored on the ``Project Folder Google Drive Settings`` single
doctype. :func:`provision_project_folders` is the public entry point and is
invoked by
:func:`erpnext_enhancements.crm_enhancements.api.create_project_from_opportunity_background`
when a Project is created from an Opportunity; the resulting folder id is saved
to ``Project.custom_drive_folder_id``.

All network calls hit the live Google Drive API. ``create_folder`` /
``find_folder`` include simple exponential-backoff retries for 403/429
(rate-limit / quota) responses.
"""

import json
import time

import frappe
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
	"""Build an authenticated Google Drive v3 service from stored settings.

	Reads the ``Project Folder Google Drive Settings`` single doctype, parses its
	service-account JSON key, and constructs a Drive client scoped to full Drive
	access.

	Returns:
		tuple: ``(service, shared_drive_id)`` where ``service`` is the Drive v3
		resource object and ``shared_drive_id`` is the configured Shared Drive ID
		(may be empty if unset).

	Raises:
		frappe.ValidationError: via ``frappe.throw`` if the service-account JSON
			is not configured or the client fails to initialize.

	Side effects:
		Reads from the DB; opens a Google API client (``cache_discovery=False``).
	"""
	settings = frappe.get_single("Project Folder Google Drive Settings")
	if not settings.service_account_json:
		frappe.throw("Service Account JSON not configured in Project Folder Google Drive Settings")

	try:
		creds_info = json.loads(settings.service_account_json)
		creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
		service = build("drive", "v3", credentials=creds, cache_discovery=False)
		return service, settings.shared_drive_id
	except Exception as e:
		frappe.throw(f"Failed to initialize Google Drive service: {e!s}")


def create_folder(service, name, parent_id, shared_drive_id=None):
	"""Create a Drive folder under ``parent_id`` and return its id and link.

	Args:
		service: Authenticated Drive v3 service (from :func:`get_drive_service`).
		name: Folder name to create.
		parent_id: Drive id of the parent folder/drive.
		shared_drive_id: When set, enables Shared Drive support on the request
			(``supportsAllDrives``).

	Returns:
		tuple: ``(folder_id, web_view_link)`` for the created folder.

	Raises:
		googleapiclient.errors.HttpError: if the API call ultimately fails.

	Side effects:
		Creates a folder via the Google Drive API. Retries up to 5 times with
		exponential backoff on 403/429 responses.
	"""
	file_metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}

	kwargs = {
		"body": file_metadata,
		"fields": "id, webViewLink",
	}

	if shared_drive_id:
		kwargs["supportsAllDrives"] = True

	max_retries = 5
	for attempt in range(max_retries):
		try:
			folder = service.files().create(**kwargs).execute()
			return folder.get("id"), folder.get("webViewLink")
		except HttpError as error:
			if error.resp.status in [403, 429]:
				if attempt < max_retries - 1:
					time.sleep((2**attempt) + 1)
					continue
			raise error


def find_folder(service, name, parent_id, shared_drive_id=None):
	"""Find an existing (non-trashed) folder by name under ``parent_id``.

	Args:
		service: Authenticated Drive v3 service.
		name: Exact folder name to match (single quotes are escaped for the query).
		parent_id: Drive id of the parent to search within.
		shared_drive_id: When set, scopes the search to that Shared Drive.

	Returns:
		str | None: The id of the first matching folder, or ``None`` if none found.

	Side effects:
		Lists files via the Google Drive API. On a 403/429 it sleeps 2s and
		retries once.
	"""
	# Escape single quotes to prevent Google Drive API query syntax errors
	escaped_name = name.replace("'", "\\'")
	query = f"name='{escaped_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"

	kwargs = {"q": query, "spaces": "drive", "fields": "files(id, name)", "pageSize": 1}

	if shared_drive_id:
		kwargs["supportsAllDrives"] = True
		kwargs["includeItemsFromAllDrives"] = True
		kwargs["corpora"] = "drive"
		kwargs["driveId"] = shared_drive_id

	try:
		results = service.files().list(**kwargs).execute()
		items = results.get("files", [])
		if not items:
			return None
		return items[0].get("id")
	except HttpError as error:
		if error.resp.status in [403, 429]:
			time.sleep(2)
			results = service.files().list(**kwargs).execute()
			items = results.get("files", [])
			if not items:
				return None
			return items[0].get("id")
		raise error


def provision_project_folders(project_name_full, party_name):
	"""Create the full Drive folder tree for a new project and return its root.

	Layout created inside the configured Shared Drive::

		<Shared Drive>/
		  <party_name>/                      (reused if it already exists)
		    <project_name_full>/             (the project folder, always created)
		      Accounting & Legal/
		      Build/
		      Design/
		      Project Manager/
		        Pictures/

	The customer folder is looked up first and only created if missing; the
	project folder and its subfolders are always created fresh.

	Args:
		project_name_full: Display name for the project folder (e.g.
			``"<project id> <project name>"``).
		party_name: Customer name used as the top-level grouping folder.

	Returns:
		tuple: ``(project_folder_id, web_view_link)`` for the new project folder.
		The caller stores ``project_folder_id`` on ``Project.custom_drive_folder_id``.

	Raises:
		frappe.ValidationError: via ``frappe.throw`` if the Shared Drive ID is not
			configured.

	Side effects:
		Multiple Google Drive API calls (create/list folders).
	"""
	service, shared_drive_id = get_drive_service()

	if not shared_drive_id:
		frappe.throw("Shared Drive ID not configured in Project Folder Google Drive Settings")

	# 1. Resolve Customer Directory
	customer_folder_id = find_folder(service, party_name, shared_drive_id, shared_drive_id)

	if not customer_folder_id:
		customer_folder_id, _ = create_folder(service, party_name, shared_drive_id, shared_drive_id)

	# 2. Create Project Directory
	project_folder_id, web_view_link = create_folder(
		service, project_name_full, customer_folder_id, shared_drive_id
	)

	# 3. Create Subfolders
	subfolders = ["Accounting & Legal", "Build", "Design", "Project Manager"]

	for subfolder_name in subfolders:
		subfolder_id, _ = create_folder(service, subfolder_name, project_folder_id, shared_drive_id)

		# Nested Pictures folder inside Project Manager
		if subfolder_name == "Project Manager":
			create_folder(service, "Pictures", subfolder_id, shared_drive_id)

	return project_folder_id, web_view_link
