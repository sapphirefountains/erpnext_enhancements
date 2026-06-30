"""Document Merge Log — append-only record of every document merge.

A merge (``erpnext_enhancements.document_merge``) deletes the absorbed "loser"
document, so the operation is irreversible; this log is the durable record of
what happened — which doctype, survivor and loser, how many fields were
backfilled, child rows appended and references repointed, and any free-text
mentions that were flagged for manual review.

Append-only, mirroring AI Action Log: every field is read_only, the permission
rows grant read + create only, and the controller rejects edits. Rows are
inserted server-side with ``ignore_permissions=True``.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class DocumentMergeLog(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("Document Merge Log entries are append-only and cannot be edited."))
