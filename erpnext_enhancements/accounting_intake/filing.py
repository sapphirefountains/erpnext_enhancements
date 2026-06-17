# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""File a posted Document Intake's source document: always attach it to the
created ERPNext record, and — when enabled — push a copy to the party's Google
Drive folder (find-or-creating an "Accounting & Legal" subfolder). Suppliers get
a folder provisioned under a configurable Shared Drive + parent folder. Drive
filing is best-effort: it never fails the posting it follows."""

import io
import mimetypes

import frappe

from erpnext_enhancements.accounting_intake.audit import log_intake


def file_document(docname):
	doc = frappe.get_doc("Document Intake", docname)
	if not (doc.created_doctype and doc.created_docname):
		return
	_attach_to_record(doc)

	settings = frappe.get_cached_doc("Accounting Intake Settings")
	if settings.get("attach_to_party_drive"):
		try:
			_push_to_drive(doc, settings)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Accounting Intake Filing (Drive)")
			log_intake("File", "Failed", accounting_document=docname, error=frappe.get_traceback())


def _attach_to_record(doc):
	if not doc.source_file:
		return
	if frappe.db.exists(
		"File",
		{"attached_to_doctype": doc.created_doctype, "attached_to_name": doc.created_docname, "file_url": doc.source_file},
	):
		return
	src = frappe.db.get_value("File", {"file_url": doc.source_file}, ["file_name", "is_private"], as_dict=True)
	frappe.get_doc(
		{
			"doctype": "File",
			"file_url": doc.source_file,
			"file_name": (src.file_name if src else None) or "document",
			"attached_to_doctype": doc.created_doctype,
			"attached_to_name": doc.created_docname,
			"is_private": (src.is_private if src else 1),
		}
	).insert(ignore_permissions=True)
	log_intake(
		"File", "Success", accounting_document=doc.name,
		reference_doctype=doc.created_doctype, reference_name=doc.created_docname, detail="Attached to record",
	)


def _push_to_drive(doc, settings):
	from erpnext_enhancements.google_drive import drive_utils

	service, default_drive = drive_utils.get_drive_service()
	folder_id, drive_id = _resolve_folder(doc, settings, service, default_drive)
	if not folder_id:
		return

	from googleapiclient.http import MediaIoBaseUpload

	content, file_doc = _file_bytes(doc.source_file)
	mime = mimetypes.guess_type(file_doc.file_name or "")[0] or "application/octet-stream"
	media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime, resumable=True)
	created = (
		service.files()
		.create(
			body={"name": file_doc.file_name or doc.name, "parents": [folder_id]},
			media_body=media,
			supportsAllDrives=True,
			fields="id, webViewLink",
		)
		.execute()
	)
	frappe.db.set_value(
		"Document Intake", doc.name,
		{"drive_file_id": created.get("id"), "drive_link": created.get("webViewLink")},
		update_modified=False,
	)
	log_intake("File", "Success", accounting_document=doc.name, detail="Pushed to Google Drive")


def _resolve_folder(doc, settings, service, default_drive):
	"""Return ``(folder_id, drive_id)`` for the party's filing subfolder, or
	``(None, None)`` when no destination is configured (filing is then record-only)."""
	from erpnext_enhancements.google_drive import drive_utils

	sub = settings.get("filing_subfolder") or "Accounting & Legal"

	if doc.proposed_party_type == "Supplier" and doc.party:
		drive_id = settings.get("supplier_drive_id") or default_drive
		parent = settings.get("supplier_parent_folder_id") or drive_id
		if not (drive_id and parent):
			return None, None
		supplier_folder = _find_or_create_supplier_folder(service, doc.party, drive_id, parent)
		if not supplier_folder:
			return None, None
		return _subfolder(service, sub, supplier_folder, drive_id), drive_id

	if doc.proposed_party_type == "Customer" and doc.party:
		cust_folder = frappe.db.get_value("Customer", doc.party, "custom_drive_folder_id")
		if not cust_folder:
			return None, None
		return _subfolder(service, sub, cust_folder, default_drive), default_drive

	return None, None


def _subfolder(service, name, parent, drive_id):
	from erpnext_enhancements.google_drive import drive_utils

	found = drive_utils.find_folder(service, name, parent, drive_id)
	if found:
		return found
	folder_id, _link = drive_utils.create_folder(service, name, parent, drive_id)
	return folder_id


def _find_or_create_supplier_folder(service, supplier, drive_id, parent):
	from erpnext_enhancements.google_drive import drive_utils

	existing = frappe.db.get_value("Supplier", supplier, "custom_drive_folder_id")
	if existing:
		return existing
	name = frappe.db.get_value("Supplier", supplier, "supplier_name") or supplier
	folder_id = drive_utils.find_folder(service, name, parent, drive_id)
	if not folder_id:
		folder_id, _link = drive_utils.create_folder(service, name, parent, drive_id)
	if folder_id and frappe.db.has_column("Supplier", "custom_drive_folder_id"):
		frappe.db.set_value("Supplier", supplier, "custom_drive_folder_id", folder_id, update_modified=False)
	return folder_id


def _file_bytes(file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")
	return content, file_doc


def enqueue_supplier_folder(doc, method=None):
	"""Supplier ``after_insert`` doc_event: provision a Drive folder when supplier
	filing is configured. Best-effort; never blocks Supplier creation."""
	try:
		settings = frappe.get_cached_doc("Accounting Intake Settings")
		if not settings.get("provision_supplier_folders"):
			return
		if not (settings.get("supplier_drive_id") and settings.get("supplier_parent_folder_id")):
			return
		frappe.enqueue(
			"erpnext_enhancements.accounting_intake.filing.provision_supplier_folder",
			queue="long",
			supplier=doc.name,
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Supplier Drive Folder enqueue")


def provision_supplier_folder(supplier):
	from erpnext_enhancements.google_drive import drive_utils

	settings = frappe.get_cached_doc("Accounting Intake Settings")
	service, _default_drive = drive_utils.get_drive_service()
	_find_or_create_supplier_folder(
		service, supplier, settings.get("supplier_drive_id"), settings.get("supplier_parent_folder_id")
	)
