# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Staging record for one public fountain-move intake submission.

Everything an anonymous submitter sent lands here first and is converted, in a
background job, into Customer / Address / Contact / Lead / Opportunity. The row
outlives the conversion deliberately: it is the audit trail, the retry handle,
and the quarantine for anything that turns out to be spam.

Why a staging doctype rather than writing the five CRM records inline:

* **Spam never reaches CRM.** A tripped honeypot or failed captcha parks a
  metadata-only row here and creates nothing downstream.
* **Partial failure is recoverable.** The conversion commits per step and
  records each created docname, so a retry resumes rather than duplicating —
  which matters because ``cust_master_name = "Customer Name"`` means a rolled-back
  retry would mint "Jane Doe Residence - 2".
* **The payload is preserved verbatim.** If the field mapping turns out to be
  wrong six months in, the original submission is still here to re-derive from.

There is deliberately **no** ``after_insert`` hook enqueuing the conversion.
``intake.submit_intake`` enqueues explicitly, so creating a row by hand in the
desk ("Staff Entry") does not fire a conversion nobody asked for. This mirrors
``accounting_intake.intake``.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime

from erpnext_enhancements.utils.phone import normalize_phone


class FountainMoveRequest(Document):
	def validate(self):
		self.normalize_identity()
		self.enforce_consent_and_weight()
		self.set_title()

	def normalize_identity(self):
		"""Canonicalise the fields duplicate matching compares on."""
		self.email = (self.email or "").strip().lower()
		self.first_name = (self.first_name or "").strip()
		self.last_name = (self.last_name or "").strip()
		self.phone_normalized = normalize_phone(self.phone)
		self.country = (self.country or "").strip() or "United States"

	def enforce_consent_and_weight(self):
		"""Enforce the two fields ``reqd`` cannot enforce.

		Frappe's mandatory test asks whether the value has *content* after
		``cstr()``. ``cstr(0)`` is ``"0"``, which has content — so ``reqd`` on a
		Check passes with the box unticked, and on a Float passes with 0. Both
		of these are load-bearing (one is a legal record of consent, the other
		decides whether we can physically lift the thing), so they are checked
		here as well as at the guest boundary.
		"""
		if not cint(self.terms_accepted):
			frappe.throw(
				_("The Terms of Use and Privacy Policy must be accepted."),
				title=_("Consent Required"),
			)
		if flt(self.fountain_weight_lbs) <= 0:
			frappe.throw(
				_("Enter the weight of the fountain in pounds."),
				title=_("Weight Required"),
			)

	def set_title(self):
		"""Human label for the list view and links."""
		who = " ".join(part for part in (self.first_name, self.last_name) if part)
		self.title = f"{who} — {self.city}" if (who and self.city) else (who or self.city or self.name)

	def before_insert(self):
		if not self.submitted_on:
			self.submitted_on = now_datetime()
		if cint(self.terms_accepted) and not self.terms_accepted_on:
			self.terms_accepted_on = now_datetime()

	def mark_converting(self):
		"""Claim the row for a conversion attempt.

		``db_set`` with ``update_modified=False`` throughout the engine: the row
		is a job ledger, not a document someone is editing, and bumping
		``modified`` on every step would make the conversion look like user
		activity in the timeline (and race the desk's own dirty-check).
		"""
		self.db_set("conversion_attempts", cint(self.conversion_attempts) + 1, update_modified=False)
		self.db_set("status", "Converting", update_modified=False)

	@property
	def is_spam(self):
		return cint(self.honeypot_tripped) or self.turnstile_verdict == "Failed"
