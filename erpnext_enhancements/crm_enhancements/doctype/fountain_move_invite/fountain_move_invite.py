# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One emailed (optionally texted) invitation to the public fountain-move form.

**The token confers zero privilege and gates nothing.** ``/fountain-move`` with
no query string is the canonical, permanent, QR-able URL; ``?ref=<token>`` only
attributes the resulting submission back to the person who sent it, which is
what makes "the sender owns the Opportunity" work. Treating the token as an
access control would mean either breaking the bare URL or building a second,
weaker auth system — so it is explicitly neither.

Consequences of that decision, all deliberate:

* An expired, revoked, unknown or malformed token behaves exactly like an absent
  one — the form opens normally, unattributed. ``resolve_invite`` never raises;
  a broken invite layer must not be able to take the form down.
* A token pre-fills **only** the store location. Name/email/phone are not
  pre-filled: invites get forwarded, and a forwarded link must not disclose the
  original recipient's details to whoever received it.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, get_datetime, now_datetime


class FountainMoveInvite(Document):
	def before_insert(self):
		if not self.token:
			self.token = frappe.generate_hash(length=22)
		if not self.sent_by:
			self.sent_by = frappe.session.user
		if not self.expires_on:
			self.expires_on = add_days(now_datetime(), self.default_expiry_days())

	def validate(self):
		self.recipient_email = (self.recipient_email or "").strip().lower()

	@staticmethod
	def default_expiry_days():
		configured = cint(
			frappe.db.get_single_value("ERPNext Enhancements Settings", "fountain_move_invite_expiry_days")
		)
		return configured if configured > 0 else 60

	@property
	def is_live(self):
		"""True when this invite may still attribute a submission."""
		if self.status in ("Revoked", "Expired"):
			return False
		if self.expires_on and get_datetime(self.expires_on) < now_datetime():
			return False
		return True

	def revoke(self):
		if self.status == "Submitted":
			frappe.throw(_("This invite has already been used and cannot be revoked."))
		self.db_set("status", "Revoked", update_modified=False)
