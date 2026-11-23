# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Document Intake — the human review queue at the heart of the Accounting
Document Intake pipeline.

A scanned document lands here (status ``Received``), is extracted via Triton's
Document AI service, then waits for human review: the inventory clerk (Stock
Manager) approves any proposed new Items, and the accountant (Accounts Manager)
approves the proposed action, which creates a *draft* ERPNext record. Nothing
is submitted automatically.

This controller only maintains the title and guards status transitions; the
heavy lifting (extraction, matching, posting, filing) lives in the
``accounting_intake`` package modules."""

import frappe
from frappe import _
from frappe.model.document import Document

# Permitted status transitions. Backward edges exist only so a stuck or failed
# document can be re-run (Failed -> Extracting/Posting, Rejected -> Needs Review).
ALLOWED_TRANSITIONS = {
	"Received": {"Extracting", "Needs Item Review", "Needs Review", "Rejected", "Duplicate", "Failed"},
	"Extracting": {"Needs Item Review", "Needs Review", "Failed"},
	"Needs Item Review": {"Needs Review", "Rejected", "Failed"},
	"Needs Review": {"Approved", "Needs Item Review", "Rejected", "Failed"},
	"Approved": {"Posting", "Rejected", "Failed"},
	"Posting": {"Posted", "Failed"},
	"Posted": set(),
	"Rejected": {"Needs Review"},
	"Duplicate": set(),
	"Failed": {"Extracting", "Needs Item Review", "Needs Review", "Posting"},
}


class DocumentIntake(Document):
	def validate(self):
		self._set_title()
		self._guard_status_transition()
		self._guard_source_file()

	def _guard_source_file(self):
		"""A person who sets or changes ``source_file`` must be able to read that file
		(``intake.require_file_read``, the File doctype's own rule), so a document is only
		ever extracted from a file its setter could open.

		Asked on a save made through the DocType's own write permission (the Desk form, the
		REST resource). The channels and the rest of this package save with
		``ignore_permissions`` and are not asked: email, the Drive watch and the scheduler
		have no signed-in reader, and the upload and mobile endpoints check before they
		ingest. A save that leaves ``source_file`` as it was is not asked either, so a
		reviewer works a document whoever sent it in."""
		if self.flags.ignore_permissions or not self.source_file:
			return
		if not self.is_new():
			previous = self.get_doc_before_save()
			if previous and (previous.source_file or "") == self.source_file:
				return
		from erpnext_enhancements.accounting_intake.intake import require_file_read

		require_file_read(self.source_file)

	def _set_title(self):
		parts = [self.document_type or "Document"]
		if self.party_name_text:
			parts.append(self.party_name_text)
		if self.document_number:
			parts.append(f"#{self.document_number}")
		self.title = " — ".join(parts)[:140]

	def _guard_status_transition(self):
		if self.is_new():
			return
		previous = self.get_doc_before_save()
		if not previous or previous.status == self.status:
			return
		allowed = ALLOWED_TRANSITIONS.get(previous.status, set())
		if self.status not in allowed:
			frappe.throw(
				_("Cannot move Document Intake from {0} to {1}.").format(previous.status, self.status)
			)
