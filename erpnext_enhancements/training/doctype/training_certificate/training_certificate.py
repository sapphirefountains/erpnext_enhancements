# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Training Certificate — the thing that leaves the building.

A completion is the internal audit record; this is the document a client, an
insurer or a site manager is actually handed. Two properties make it that rather
than a report:

**It stores its rendered HTML.** ``certificate_html`` is written once, at issue,
from the course's print format. Serving that snapshot instead of re-rendering is
the whole point: editing a print format next year must never rewrite a document
somebody already holds — the same reasoning ``project_enhancements/esign`` gives
for not using ``frappe.attach_print`` on a signed agreement. The field is
``allow_on_submit`` so the issuing code can render either side of ``submit()``
without the order mattering.

**Its public handle is ``verification_code``, not its name.** The name is a
sequence, and a sequence printed on a certificate lets anybody who holds one
enumerate everybody else's. The code is a random hash, and the page it resolves
to returns initials only.

``completion`` is DB-unique, which means a certificate cannot be *amended* — the
amendment would carry the same completion and collide. That is deliberate: the
correction path for a wrong certificate is cancel (which revokes it, visibly) and
issue a fresh one, never a quiet rewrite of a document already in circulation.
Hence no ``amend`` permission below, even though the doctype is submittable.

Issuance itself — deciding that a completion earns a certificate, rendering it,
emailing it — lives in ``training/certificates.py``, driven from
``Training Completion.on_submit``. This controller only keeps one certificate
internally consistent.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_months, cint, getdate, nowdate

VALID = "Valid"
EXPIRED = "Expired"
REVOKED = "Revoked"


class TrainingCertificate(Document):
	def before_naming(self):
		# Not needed by the name, but generated here so the code exists before any
		# before_insert hook in the issuing module wants to build a verify URL out
		# of it. validate regenerates it if it is somehow still blank.
		self._ensure_verification_code()

	def validate(self):
		self._ensure_verification_code()
		self._snapshot_completion()
		self._default_dates()
		self._derive_status()

	def on_cancel(self):
		# db_set, not assignment: on_cancel runs after the document has been
		# written, so a plain field set would be discarded. Revoked has to stick —
		# training/compliance.py reads this status to decide whether somebody is
		# certified, and a cancelled certificate that still reads Valid is exactly
		# the safeguard-that-stopped-being-one this phase is guarding against.
		self.db_set("status", REVOKED, update_modified=False)

	# ------------------------------------------------------------------ helpers

	def _ensure_verification_code(self):
		"""Random, not derived. Anything derived from the holder or the course
		would let somebody who holds one certificate construct another's code."""
		if (self.verification_code or "").strip():
			return
		for _attempt in range(5):
			code = frappe.generate_hash(length=12).upper()
			if not frappe.db.exists("Training Certificate", {"verification_code": code}):
				self.verification_code = code
				return
		# Five collisions on a 12-character hash does not happen; if it somehow
		# did, an un-coded certificate is worse than a loud failure.
		frappe.throw(_("Could not allocate a unique verification code. Try again."))

	def _snapshot_completion(self):
		"""Copy the completion's identity in once, guarded on 'not already set'.

		Every one of these could have been a ``fetch_from`` and every one of them
		would then be wrong: a course renamed, a person married, a score corrected
		on an amended completion — none of that may alter a certificate that is
		already in somebody's hands.
		"""
		if not self.completion:
			frappe.throw(_("A certificate has to say which completion it certifies."))

		completion = frappe.db.get_value(
			"Training Completion",
			self.completion,
			[
				"course",
				"course_title_snapshot",
				"course_version",
				"version_number",
				"user",
				"employee",
				"customer",
				"score_percent",
				"docstatus",
			],
			as_dict=True,
		)
		if not completion:
			frappe.throw(_("Completion {0} no longer exists.").format(self.completion))
		if cint(completion.docstatus) == 2:
			frappe.throw(
				_("Completion {0} has been withdrawn; a certificate cannot be issued against it.").format(
					self.completion
				)
			)

		self._reject_duplicate_certificate()

		if not self.course:
			self.course = completion.course
		if not (self.course_title or "").strip():
			self.course_title = completion.course_title_snapshot
		if not self.course_version:
			self.course_version = completion.course_version
		if not cint(self.version_number):
			self.version_number = cint(completion.version_number)
		if not self.user:
			self.user = completion.user
		if not self.employee:
			self.employee = completion.employee
		if not self.customer:
			self.customer = completion.customer
		if self.score_percent is None:
			self.score_percent = completion.score_percent
		if not (self.holder_name or "").strip():
			self.holder_name = _holder_name(self.user, self.employee)

	def _reject_duplicate_certificate(self):
		"""``completion`` carries a unique index, so this is only about the message.

		Left to the database the operator gets a raw duplicate-entry traceback on
		what is a perfectly ordinary race — two workers reacting to the same
		submitted completion.
		"""
		existing = frappe.db.get_value(
			"Training Certificate",
			{"completion": self.completion, "name": ["!=", self.name or ""]},
			"name",
		)
		if existing:
			frappe.throw(
				_("Certificate {0} has already been issued for this completion.").format(existing)
			)

	def _default_dates(self):
		"""Expiry is computed once, from the course setting as it stood at issue.

		Re-deriving it on every save would mean shortening ``certificate_valid_months``
		retroactively expires certificates already issued — a policy change quietly
		rewriting history.
		"""
		if not self.issued_on:
			self.issued_on = nowdate()
		if self.expires_on or not self.course:
			return
		months = cint(frappe.db.get_value("Training Course", self.course, "certificate_valid_months"))
		if months > 0:
			self.expires_on = add_months(getdate(self.issued_on), months)

	def _derive_status(self):
		"""Status is a fact about the dates and the docstatus, never typed."""
		if cint(self.docstatus) == 2:
			self.status = REVOKED
			return
		if self.status == REVOKED and cint(self.docstatus) != 2:
			# An amended-from-revoked or hand-edited draft: let it come back rather
			# than carrying a revocation that no longer corresponds to anything.
			self.status = VALID
		if self.expires_on and getdate(self.expires_on) < getdate(nowdate()):
			self.status = EXPIRED
		else:
			self.status = VALID


def _holder_name(user, employee=None):
	"""What gets printed. Employee name first — it is the one somebody's manager
	maintains — falling back to the User's full name and finally the login, so a
	certificate is never issued with a blank name on it."""
	if employee:
		name = frappe.db.get_value("Employee", employee, "employee_name")
		if name:
			return name
	if user:
		name = frappe.db.get_value("User", user, "full_name")
		if name:
			return name
	return user or ""
