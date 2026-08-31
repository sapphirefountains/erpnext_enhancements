# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Mirror QuickBooks Online attachments into ERPNext as native File attachments.

QBO stores files as ``Attachable`` entities, each linked to one or more transactions
through ``AttachableRef``. This module queries those Attachables, resolves each to the
ERPNext document the QBO transaction was imported as (via the ``QuickBooks Sync
Mapping``), downloads the file from its pre-signed ``TempDownloadUri``, and saves it as a
**private File attached to that document** -- so the receipt/scan lives on the ERPNext
doc and is visible to anything that reads ERPNext (notably Triton, which indexes ERPNext,
not Google Drive).

Idempotent: every mirrored File records its source Attachable id in
``custom_qbo_attachable_id``, keyed together with the target document, so a re-run (or the
daily scheduled pass) downloads only what is not already mirrored to that document. An
Attachable can be linked to several transactions, so it may legitimately be mirrored onto
more than one ERPNext document.

QBO remains the read-only source of the originals -- nothing here deletes from QBO, and a
mirrored file that is later removed in ERPNext can simply be re-downloaded on the next
run. (A Google-Drive backup mirror is a deferred v2 follow-on; see WI-071.)

Run modes:
  * **Backfill** (one-time): ``sync_attachments()`` with no ``max_new`` -- pages the whole
    Attachable list and mirrors everything not already present. Run via ``bench execute``;
    safe to re-run if interrupted (idempotent).
  * **Ongoing** (steady state): ``tasks.sync_attachments_scheduled`` calls this with a
    ``max_new`` cap so a single scheduled run never runs away.
"""

from __future__ import annotations

import frappe

from erpnext_enhancements.quickbooks_online.core.client import QuickBooksClient
from erpnext_enhancements.quickbooks_online.core.utils import get_settings

# QBO caps a query page at 1000 rows; page through with STARTPOSITION.
QBO_ATTACHABLE_PAGE = 1000


def sync_attachments(entity_types=None, max_new=None, settings=None):
	"""Mirror QBO Attachables onto their mapped ERPNext documents (idempotent).

	``entity_types`` -- optional list of QBO entity types to restrict to (e.g.
	``["Bill", "Invoice"]``); ``None`` mirrors every linked type we have a mapping for.
	``max_new`` -- cap on how many NEW files this run downloads, so a bounded scheduled
	pass cannot run away; ``None`` (the backfill) processes everything.

	Returns a summary dict: ``scanned`` (Attachables seen), ``mirrored`` (files saved),
	``skipped_existing`` (already mirrored to that doc), ``no_mapping`` (linked entity not
	imported into ERPNext), ``no_file`` (a Note / metadata-only Attachable), ``errors``.
	"""
	settings = settings or get_settings()
	client = QuickBooksClient(settings)
	summary = {
		"scanned": 0,
		"mirrored": 0,
		"skipped_existing": 0,
		"no_mapping": 0,
		"no_file": 0,
		"errors": 0,
	}
	start = 1
	while True:
		resp = client.query(
			f"SELECT * FROM Attachable STARTPOSITION {start} MAXRESULTS {QBO_ATTACHABLE_PAGE}"
		)
		rows = ((resp or {}).get("QueryResponse") or {}).get("Attachable") or []
		if not rows:
			break
		for att in rows:
			summary["scanned"] += 1
			file_name = att.get("FileName")
			temp_uri = att.get("TempDownloadUri")
			att_id = str(att.get("Id") or "")
			# A Note (text-only) or a metadata Attachable with no downloadable file.
			if not file_name or not temp_uri or not att_id:
				summary["no_file"] += 1
				continue
			for ref in att.get("AttachableRef") or []:
				entity = ref.get("EntityRef") or {}
				qbo_type = entity.get("type")
				qbo_id = str(entity.get("value") or "")
				if not qbo_type or not qbo_id:
					continue
				if entity_types and qbo_type not in entity_types:
					continue
				target = _mapped_document(qbo_type, qbo_id)
				if not target:
					summary["no_mapping"] += 1
					continue
				doctype, name = target
				if _already_mirrored(att_id, doctype, name):
					summary["skipped_existing"] += 1
					continue
				try:
					content = client.download_attachable(temp_uri)
					_save_attachment(content, file_name, att_id, doctype, name)
					summary["mirrored"] += 1
				except Exception:
					summary["errors"] += 1
					frappe.log_error(
						frappe.get_traceback(),
						f"QBO attachment mirror {att_id} -> {doctype} {name}",
					)
				if max_new and summary["mirrored"] >= max_new:
					return summary
		# A short page is the last page.
		if len(rows) < QBO_ATTACHABLE_PAGE:
			break
		start += QBO_ATTACHABLE_PAGE
	return summary


def _mapped_document(qbo_entity_type, qbo_id):
	"""Resolve a QBO ``(type, id)`` to the ERPNext ``(doctype, name)`` it was imported as.

	Returns ``None`` when there is no mapping, the mapping has no ERPNext record, or the
	record no longer exists (e.g. a mapping soft-deleted after the linked doc was removed).
	"""
	row = frappe.db.get_value(
		"QuickBooks Sync Mapping",
		{"qbo_entity_type": qbo_entity_type, "qbo_id": qbo_id},
		["erpnext_doctype", "erpnext_name"],
		as_dict=True,
	)
	if (
		row
		and row.erpnext_doctype
		and row.erpnext_name
		and frappe.db.exists(row.erpnext_doctype, row.erpnext_name)
	):
		return row.erpnext_doctype, row.erpnext_name
	return None


def _already_mirrored(att_id, doctype, name):
	"""True when this Attachable is already mirrored onto this document."""
	return bool(
		frappe.db.exists(
			"File",
			{
				"custom_qbo_attachable_id": att_id,
				"attached_to_doctype": doctype,
				"attached_to_name": name,
			},
		)
	)


def _save_attachment(content, file_name, att_id, doctype, name):
	"""Save downloaded bytes as a private File attached to the ERPNext document.

	Mirrors the ``accounting_intake.channels`` pattern (create a File with ``content``);
	Frappe writes the bytes to storage on insert. ``custom_qbo_attachable_id`` stamps the
	source so re-runs skip it.
	"""
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"is_private": 1,
			"content": content,
			"custom_qbo_attachable_id": att_id,
		}
	).insert(ignore_permissions=True)
