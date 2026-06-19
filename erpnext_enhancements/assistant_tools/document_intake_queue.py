"""document_intake_queue — Accounting Document Intake review queue (read-only).

Only imported by frappe_assistant_core's tool loader via the assistant_tools
hook; see the package docstring for the FAC-optional invariant.

The companion to Triton's chat-side ``sfo_extract_document`` filing tool: that
tool *creates* Document Intake records; this one lets the assistant *read* the
review queue — counts by workflow status, the actionable backlog (what a human
still needs to review/fix), and one document's extracted fields, line items and
proposed matches. Read-only; the queue list goes through ``frappe.get_list`` so
the user only sees Document Intakes they are permitted to read.
"""

from typing import Any

import frappe
from frappe_assistant_core.core.base_tool import BaseTool

from erpnext_enhancements.assistant_tools._common import clamp_limit, require_doc_read, strip_meta

# Document Intake.status values, in workflow order.
_STATUSES = [
	"Received",
	"Extracting",
	"Needs Item Review",
	"Needs Review",
	"Approved",
	"Posting",
	"Posted",
	"Failed",
	"Rejected",
	"Duplicate",
]

# The statuses that mean "a human still has to do something".
_ATTENTION = ["Needs Review", "Needs Item Review", "Failed"]

_DOCUMENT_TYPES = ["Vendor Bill", "Customer Remittance", "Receipt / Expense", "Packing Slip", "Unknown"]

_QUEUE_FIELDS = [
	"name",
	"status",
	"document_type",
	"source_channel",
	"received_on",
	"party_name_text",
	"document_number",
	"document_date",
	"grand_total",
	"currency",
	"extraction_confidence",
	"proposed_action",
	"modified",
]

_DETAIL_FIELDS = [
	*_QUEUE_FIELDS,
	"net_total",
	"tax_total",
	"po_number_text",
	"proposed_party_type",
	"proposed_party",
	"party",
	"party_match_confidence",
	"created_doctype",
	"created_docname",
	"drive_link",
	"error",
	"review_notes",
]


class DocumentIntakeQueue(BaseTool):
	def __init__(self):
		super().__init__()
		self.name = "document_intake_queue"  # must match module filename
		self.description = (
			"Read the Accounting Document Intake review queue (scanned AP/AR/expense/"
			"packing documents extracted by Document AI). Returns a count of Document "
			"Intakes by status (Received/Extracting/Needs Item Review/Needs Review/"
			"Approved/Posting/Posted/Failed/Rejected/Duplicate), how many need human "
			"attention (Needs Review + Needs Item Review + Failed), and the actionable "
			"backlog — most recent first. Use it to answer 'what's waiting to be "
			"reviewed', 'did anything fail extraction', or 'how big is the intake "
			"backlog'. Filter with 'status' and/or 'document_type'. Pass 'document' to "
			"get one intake's extracted header fields, line items and proposed "
			"matches. Read-only — approving or posting still happens in the desk."
		)
		self.category = "Accounting"
		self.source_app = "erpnext_enhancements"
		self.requires_permission = "Document Intake"
		self.inputSchema = {
			"type": "object",
			"properties": {
				"document": {
					"type": "string",
					"description": "A Document Intake name (e.g. ACC-DOC-2026-00001) to return its full detail instead of the queue summary.",
				},
				"status": {
					"type": "string",
					"enum": _STATUSES,
					"description": "Limit the queue list to a single status. Omit to show the actionable backlog (Needs Review / Needs Item Review / Failed).",
				},
				"document_type": {
					"type": "string",
					"enum": _DOCUMENT_TYPES,
					"description": "Limit the queue list to one document type.",
				},
				"limit": {
					"type": "integer",
					"description": "How many queue rows to return (default 20, max 100).",
				},
			},
			"required": [],
		}

	def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
		args = arguments or {}
		document = args.get("document")
		if document:
			return self._detail(document)
		return self._summary(args)

	def _detail(self, document: str) -> dict[str, Any]:
		if not frappe.db.exists("Document Intake", document):
			return {"success": False, "error": f"Document Intake {document} not found"}
		require_doc_read("Document Intake", document)
		doc = frappe.get_doc("Document Intake", document)
		header = {f: doc.get(f) for f in _DETAIL_FIELDS}
		for key in ("received_on", "document_date", "modified"):
			header[key] = str(header.get(key) or "")
		return {
			"success": True,
			"document": header,
			"line_items": [strip_meta(row.as_dict()) for row in (doc.get("line_items") or [])],
			"proposed_matches": [strip_meta(row.as_dict()) for row in (doc.get("proposed_matches") or [])],
		}

	def _summary(self, args: dict[str, Any]) -> dict[str, Any]:
		limit = clamp_limit(args.get("limit"), 20, 100)

		counts = {
			row["status"]: row["count"]
			for row in frappe.get_list(
				"Document Intake",
				fields=["status", "count(name) as count"],
				group_by="status",
			)
		}
		needs_attention = sum(counts.get(s, 0) for s in _ATTENTION)

		filters: dict[str, Any] = {}
		if args.get("status"):
			filters["status"] = args["status"]
		elif not args.get("document_type"):
			# Default view: the actionable backlog only.
			filters["status"] = ["in", _ATTENTION]
		if args.get("document_type"):
			filters["document_type"] = args["document_type"]

		queue = frappe.get_list(
			"Document Intake",
			filters=filters,
			fields=_QUEUE_FIELDS,
			order_by="modified desc",
			limit_page_length=limit,
		)
		for row in queue:
			for key in ("received_on", "document_date", "modified"):
				row[key] = str(row.get(key) or "")

		return {
			"success": True,
			"counts_by_status": counts,
			"needs_attention": needs_attention,
			"queue": queue,
		}


__all__ = ["DocumentIntakeQueue"]
