# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

""""I have read it and I agree" — with a name and a date on it.

The training module can already push the handbook out as a document lesson and
records that each person **opened and read** it. That is a page-view, and it is a
materially weaker thing than an agreement: the whole evidentiary value of a policy
acknowledgement is the person saying yes to it, which a scroll position cannot
show.

So this is an HR record rather than a lesson block. It also means a policy that
lives in Drive — which is where the handbook actually is — can be acknowledged
without first being rebuilt as a course.

**Declining is allowed and recorded.** A register that only accepts yes is a
register that gets a yes, and a refusal with a reason on it is far more useful to
whoever has to deal with it than a row that never got filled in.

**Version matters.** Without it, "he signed the handbook" says nothing about which
handbook, and the version people actually argue about is the one that changed.
"""

import frappe
from frappe import _
from frappe.model.document import Document

REQUESTED = "Requested"
SIGNED = "Signed"
DECLINED = "Declined"


#: Roles whose Data Import keeps the statuses in the file (historical records).
IMPORT_ROLES = ("HR Manager", "System Manager")


def _trusted_import():
	"""True during a Data Import run by HR Manager or System Manager."""
	if not getattr(frappe.flags, "in_import", None):
		return False
	return bool(set(IMPORT_ROLES) & set(frappe.get_roles()))


class PolicyAcknowledgement(Document):
	def validate(self):
		self._resolve_user()
		self._guard_status()

	def _resolve_user(self):
		if self.employee:
			self.user = frappe.db.get_value("Employee", self.employee, "user_id")

	def _guard_status(self):
		"""Signed and Declined are set by `hr_enhancements/policies.py` and nowhere
		else.

		`read_only` hides the field in the Desk form and nothing more -- Frappe does
		not enforce it against the API, and `Employee` holds write here because
		signing IS a write. Without this, anybody could POST their own row to Signed
		without ever seeing the document, which is precisely the thing the record
		exists to prove they did.

		A NEW row that did not come through `policies.request_acknowledgement`
		starts as Requested with no signature and no decline, whatever the insert
		carried.
		"""
		if self.flags.get("policy_transition"):
			return
		if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
			return
		# A Data Import by HR Manager or System Manager keeps the file's statuses (paper-signed rows).
		if _trusted_import():
			return
		if self.is_new():
			self.status = REQUESTED
			for field in ("signed_name", "signature", "signed_on", "signed_from_ip", "declined_reason"):
				self.set(field, None)
			return
		before = self.get_doc_before_save()
		if before and before.status != self.status:
			frappe.throw(
				_("Sign or decline with the buttons on this form."), frappe.PermissionError
			)
