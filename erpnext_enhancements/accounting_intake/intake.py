# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Document intake — the single entry point every channel funnels through.

``ingest_document`` dedupes by content hash, creates a ``Document Intake`` row
in the review queue, and (when enabled) enqueues extraction via Triton. Channel
adapters are thin wrappers: the manual-upload channel lives here; email, Google
Drive watched-folder, mobile, and chat-origin adapters arrive in later PRs."""

import hashlib
import mimetypes

import frappe
from frappe import _

from erpnext_enhancements.accounting_intake.audit import log_intake

# Statuses that count as a still-live document for dedup purposes (a Rejected /
# Failed / Duplicate row should not block re-ingesting the same file).
LIVE_STATUSES = (
	"Received",
	"Extracting",
	"Needs Item Review",
	"Needs Review",
	"Approved",
	"Posting",
	"Posted",
)

_UPLOAD_ROLES = {"Accounts User", "Accounts Manager", "System Manager"}


def compute_content_hash(content):
	if isinstance(content, str):
		content = content.encode("utf-8")
	return hashlib.sha256(content).hexdigest()


def find_duplicate(content_hash):
	"""Return the name of a live Document Intake with this content hash, else None."""
	if not content_hash:
		return None
	existing = frappe.get_all(
		"Document Intake",
		filters={"content_hash": content_hash, "status": ["in", LIVE_STATUSES]},
		pluck="name",
		limit=1,
	)
	return existing[0] if existing else None


def _file_bytes(file_url):
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")
	return content, file_doc


def _guess_mime(file_doc):
	if file_doc.file_type:
		guessed = mimetypes.types_map.get(f".{file_doc.file_type.lower()}")
		if guessed:
			return guessed
	return "application/pdf"


def ingest_document(*, file_url, source_channel="Upload", source_reference=None, document_type=None):
	"""Create a Document Intake row from an already-uploaded File.

	Returns the new docname, or the existing docname when the same file (by
	content hash) is already live in the queue."""
	content, _file_doc = _file_bytes(file_url)
	content_hash = compute_content_hash(content)

	duplicate = find_duplicate(content_hash)
	if duplicate:
		log_intake(
			"Dedup", "Duplicate", accounting_document=duplicate,
			detail=f"Duplicate file via {source_channel}",
		)
		return duplicate

	doc = frappe.get_doc(
		{
			"doctype": "Document Intake",
			"status": "Received",
			"source_channel": source_channel,
			"source_file": file_url,
			"source_reference": source_reference,
			"content_hash": content_hash,
			"document_type": document_type or "Unknown",
			"received_on": frappe.utils.now_datetime(),
		}
	).insert(ignore_permissions=True)

	log_intake("Ingest", "Success", accounting_document=doc.name, detail=f"Ingested via {source_channel}")

	settings = frappe.get_cached_doc("Accounting Intake Settings")
	if settings.get("intake_enabled") and settings.get("auto_extract"):
		frappe.enqueue(
			"erpnext_enhancements.accounting_intake.intake.run_extraction",
			queue="long",
			enqueue_after_commit=True,
			docname=doc.name,
		)

	return doc.name


@frappe.whitelist()
def ingest_upload(file_url, document_type=None):
	"""Manual-upload channel: called from the Document Intake list view after a
	file is uploaded. Creates the queue row and returns its name."""
	if not (set(frappe.get_roles()) & _UPLOAD_ROLES):
		frappe.throw(_("Not permitted to upload accounting documents."), frappe.PermissionError)
	if not file_url:
		frappe.throw(_("file_url is required"))
	name = ingest_document(file_url=file_url, source_channel="Upload", document_type=document_type)
	return {"name": name}


def run_extraction(docname):
	"""Background job: extract a received document via Triton and stage it for
	review — populating header fields, line items (with Item resolution), and
	advisory party/document matches, then routing to ``Needs Item Review`` or
	``Needs Review``.

	Gated by Accounting Intake Settings (``intake_enabled`` + ``auto_extract``)
	at the ingest call site, so it is inert until Triton is configured."""
	from erpnext_enhancements.accounting_intake import extraction, triton_client

	doc = frappe.get_doc("Document Intake", docname)
	if doc.status not in ("Received", "Failed"):
		return

	try:
		doc.db_set("status", "Extracting", update_modified=False)
		content, file_doc = _file_bytes(doc.source_file)
		result = triton_client.extract_document(
			content,
			_guess_mime(file_doc),
			doc.document_type,
			filename=file_doc.file_name or "document",
		)

		doc.reload()
		extraction.apply_extraction(doc, result)
		doc.save(ignore_permissions=True)
		log_intake("Extract", "Success", accounting_document=docname, detail=f"-> {doc.status}")
	except Exception:
		frappe.db.rollback()
		failed = frappe.get_doc("Document Intake", docname)
		failed.db_set("status", "Failed", update_modified=False)
		failed.db_set("error", frappe.get_traceback()[:1000], update_modified=False)
		failed.db_set("attempts", (failed.attempts or 0) + 1, update_modified=False)
		log_intake(
			"Extract", "Failed", accounting_document=docname,
			error=frappe.get_traceback(),
			payload={
				"method": "erpnext_enhancements.accounting_intake.intake.run_extraction",
				"kwargs": {"docname": docname},
			},
		)
		frappe.log_error(frappe.get_traceback(), "Accounting Intake Extraction")
