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
