import json
import time

import frappe
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]


def get_drive_service():
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
