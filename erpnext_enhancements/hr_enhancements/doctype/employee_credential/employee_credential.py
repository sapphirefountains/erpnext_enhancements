# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Employee Credential — a qualification somebody *else* issued.

OSHA 10, forklift, CDL and its medical card, first aid and CPR, respirator fit
test, electrical. The things a general contractor asks for at the gate and an
insurer asks for after an incident, and the things that were living in a filing
cabinet because this app structurally could not hold them: ``Training
Certificate.completion`` is ``reqd: 1``, so every certificate the system could
issue had to originate in an internal course.

**Not a Training Certificate, and not a variant of one.** A completion is
evidence *this system* produced and can re-derive — it snapshots the version, the
content hash and the score, and it can be recomputed from the attempt behind it.
A credential is evidence somebody outside produced, which this app can only ever
*hold*. Merging them would mean either weakening the completion's guarantees or
inventing an attempt for a forklift ticket. So: two records, one shared idea of
expiry, and both feed the same profile, the same expiry horizon and the same
dispatch advisory.

Status is derived and never typed
---------------------------------
``Valid`` / ``Expiring`` / ``Expired`` / ``Revoked``, computed from the dates on
every save and re-swept nightly. A typed status is a status that goes stale the
day after somebody types it, and the whole value of this record is that it is
true on the morning of the job rather than on the day it was filed.

``Expiring`` is not a separate fact — it is ``Valid`` inside the warning horizon.
It exists as its own value because "show me what is about to lapse" is the
question people actually ask, and a report that has to compute a date window to
answer it is a report nobody writes.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, add_months, cint, getdate, today

VALID = "Valid"
EXPIRING = "Expiring"
EXPIRED = "Expired"
REVOKED = "Revoked"

#: How far ahead a credential starts reading as ``Expiring``. Ninety days is the
#: shortest notice that still leaves time to book a course, sit it, and have the
#: card arrive — which is the whole point of a horizon rather than an alarm.
EXPIRY_HORIZON_DAYS = 90


class EmployeeCredential(Document):
	def validate(self):
		self._resolve_user()
		self._default_expiry()
		self._require_revocation_reason()
		self._reject_backwards_dates()
		self.status = self.derive_status()

	# ------------------------------------------------------------------ helpers

	def _resolve_user(self):
		"""Derived from the Employee, never accepted from the form.

		This column is what ``hr_enhancements/permissions.py`` scopes rows on, so a
		caller-supplied value would be a way to file somebody else's licence under
		your own name and then read it back.
		"""
		self.user = frappe.db.get_value("Employee", self.employee, "user_id") or None

	def _default_expiry(self):
		"""Fill the expiry from the credential's usual validity, once.

		Only when the field is empty: a card that was issued late, or renewed early,
		has a real date on it and the person recording it is reading that date off
		the card. Overwriting them would be the software insisting it knows better
		than the document in their hand.
		"""
		if self.expires_on or not self.issued_on or not self.credential_type:
			return
		months = cint(
			frappe.db.get_value("Credential Type", self.credential_type, "default_validity_months")
		)
		if months > 0:
			self.expires_on = add_months(getdate(self.issued_on), months)

	def _require_revocation_reason(self):
		if self.revoked_on and not (self.revoked_reason or "").strip():
			frappe.throw(
				_("Say why this credential was revoked. A withdrawal with no reason tells "
				  "the next person to read this record nothing at all.")
			)

	def _reject_backwards_dates(self):
		if self.expires_on and self.issued_on and getdate(self.expires_on) < getdate(self.issued_on):
			frappe.throw(_("This credential expires before it was issued."))

	def derive_status(self, as_of=None):
		"""The one place status is decided. Read by ``validate`` and by the sweep.

		Revocation wins over everything: a revoked credential that has not yet
		reached its expiry date is still revoked, and reading it as ``Valid``
		because the arithmetic says so is the failure this ordering prevents.
		"""
		# Compared against `as_of`, not merely truthy. A credential revoked in June
		# was genuinely held in March, and an as-of-March roster that reports it as
		# Revoked is restating today over a question about the past -- which is the
		# whole failure the roster exists to avoid. With `as_of` unset this is
		# identical to the old behaviour.
		on = getdate(as_of or today())
		if self.revoked_on and getdate(self.revoked_on) <= on:
			return REVOKED
		if not self.expires_on:
			# No expiry is a real answer, not missing data — an OSHA 10 card does
			# not lapse. Valid until somebody revokes it.
			return VALID
		expires = getdate(self.expires_on)
		if expires < on:
			return EXPIRED
		if expires <= getdate(add_days(on, EXPIRY_HORIZON_DAYS)):
			return EXPIRING
		return VALID


def current_credentials(user):
	"""Credential names this user currently holds, keyed by credential type.

	``Valid`` and ``Expiring`` both count as held — somebody whose ticket lapses in
	six weeks is qualified today, and refusing to dispatch them would be the
	horizon doing the opposite of its job. ``Expired`` and ``Revoked`` do not.
	"""
	if not user or not frappe.db.exists("DocType", "Employee Credential"):
		return {}
	return {
		row.credential_type: row.name
		for row in frappe.get_all(
			"Employee Credential",
			filters={"user": user, "status": ["in", (VALID, EXPIRING)]},
			fields=["name", "credential_type"],
			order_by="expires_on desc",
		)
	}
