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
from frappe.utils import cint
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive"]


def enqueue_customer_folder(doc, method=None):
	"""Customer ``after_insert`` doc_event: queue Drive folder creation when
	enabled (Project Folder Google Drive Settings → Create Customer Folders).
	Best-effort — never blocks Customer creation."""
	try:
		settings = frappe.get_cached_doc("Project Folder Google Drive Settings")
		if not cint(settings.get("create_customer_folders")):
			return
		if not (settings.get("service_account_json") and settings.get("shared_drive_id")):
			return
		frappe.enqueue(
			"erpnext_enhancements.google_drive.drive_utils.provision_customer_folder",
			queue="long",
			customer=doc.name,
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Customer Drive Folder enqueue")


def provision_customer_folder(customer):
	"""Background job: find-or-create the customer's top-level folder in the
	Shared Drive (the same folder :func:`provision_project_folders` nests
	project trees under) and store its id on
	``Customer.custom_drive_folder_id``. Failures land in Error Log under
	"Customer Drive Folder"."""
	try:
		if not frappe.db.exists("Customer", customer):
			return
		customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer
		service, shared_drive_id = get_drive_service()
		if not shared_drive_id:
			return
		folder_id = find_folder(service, customer_name, shared_drive_id, shared_drive_id)
		if not folder_id:
			folder_id, _link = create_folder(service, customer_name, shared_drive_id, shared_drive_id)
		if folder_id and frappe.db.has_column("Customer", "custom_drive_folder_id"):
			frappe.db.set_value(
				"Customer", customer, "custom_drive_folder_id", folder_id, update_modified=False
			)
		from erpnext_enhancements.google_drive.drive_sync import log_sync

		log_sync(
			"Provision Folder", "Success",
			reference_doctype="Customer", reference_name=customer,
			file_name=customer_name, drive_file_id=folder_id,
		)
	except Exception:
		frappe.log_error(
			f"Customer Drive folder failed for {customer}\n{frappe.get_traceback()}",
			"Customer Drive Folder",
		)


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


def provision_project_folders(project_name_full, party_name, project_type=None):
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

	# 3. Create subfolders — from the settings template table when configured
	# (rows optionally scoped to a project type), else the legacy defaults.
	# Paths support nesting via "/" (e.g. "Project Manager/Pictures").
	default_template = [
		"Accounting & Legal",
		"Build",
		"Design",
		"Project Manager",
		"Project Manager/Pictures",
	]
	settings = frappe.get_cached_doc("Project Folder Google Drive Settings")
	rows = settings.get("project_folder_template") or []
	paths = [
		(row.folder_path or "").strip()
		for row in rows
		if (row.folder_path or "").strip()
		and (not row.project_type or (project_type and row.project_type == project_type))
	] or default_template

	created = {}
	for path in paths:
		parent = project_folder_id
		partial = []
		for part in [p.strip() for p in path.split("/") if p.strip()]:
			partial.append(part)
			key = "/".join(partial)
			if key not in created:
				folder_id, _ = create_folder(service, part, parent, shared_drive_id)
				created[key] = folder_id
			parent = created[key]

	return project_folder_id, web_view_link


def enqueue_opportunity_folder(doc, method=None):
	"""Opportunity ``after_insert`` doc_event: queue Drive folder creation when
	enabled (Project Folder Google Drive Settings → Create Opportunity
	Folders). Only Customer-party opportunities get folders (Leads have no
	customer folder to nest under). Best-effort — never blocks the insert."""
	try:
		if (doc.get("opportunity_from") or "") != "Customer" or not doc.get("party_name"):
			return
		settings = frappe.get_cached_doc("Project Folder Google Drive Settings")
		if not cint(settings.get("create_opportunity_folders")):
			return
		if not (settings.get("service_account_json") and settings.get("shared_drive_id")):
			return
		frappe.enqueue(
			"erpnext_enhancements.google_drive.drive_utils.provision_opportunity_folder",
			queue="long",
			opportunity=doc.name,
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Opportunity Drive Folder enqueue")


def provision_opportunity_folder(opportunity):
	"""Background job: find-or-create ``<Customer>/<Opportunity ID - Title>`` in
	the Shared Drive (e.g. ``CRM-OPP-2026-00112 - Pool Reno``) and store its id on
	``Opportunity.custom_drive_folder_id``."""
	from erpnext_enhancements.google_drive.drive_sync import log_sync

	try:
		if not frappe.db.exists("Opportunity", opportunity):
			return
		opp = frappe.db.get_value(
			"Opportunity", opportunity, ["party_name", "title", "opportunity_from"], as_dict=True
		)
		if opp.opportunity_from != "Customer" or not opp.party_name:
			return
		customer_label = (
			frappe.db.get_value("Customer", opp.party_name, "customer_name") or opp.party_name
		)
		service, shared_drive_id = get_drive_service()
		if not shared_drive_id:
			return
		customer_folder_id = find_folder(service, customer_label, shared_drive_id, shared_drive_id)
		if not customer_folder_id:
			customer_folder_id, _ = create_folder(
				service, customer_label, shared_drive_id, shared_drive_id
			)
		folder_name = f"{opportunity} - {opp.title}".strip(" -") if opp.title else opportunity
		folder_id = find_folder(service, folder_name, customer_folder_id, shared_drive_id)
		if not folder_id:
			folder_id, _ = create_folder(service, folder_name, customer_folder_id, shared_drive_id)
		if folder_id and frappe.db.has_column("Opportunity", "custom_drive_folder_id"):
			frappe.db.set_value(
				"Opportunity", opportunity, "custom_drive_folder_id", folder_id,
				update_modified=False,
			)
		log_sync(
			"Provision Folder", "Success",
			reference_doctype="Opportunity", reference_name=opportunity,
			file_name=folder_name, drive_file_id=folder_id,
		)
	except Exception:
		frappe.log_error(
			f"Opportunity Drive folder failed for {opportunity}\n{frappe.get_traceback()}",
			"Opportunity Drive Folder",
		)
