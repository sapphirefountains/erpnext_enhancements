# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The company's own renewal dates, and the subcontractors' certificates we hold.

Everything else in this module tracks what a *person* holds. This tracks what the
**company** holds, which nothing in the app did: the contractor licence, the
workers' compensation policy and its annual premium audit, general liability, the
certificates of insurance customers ask for, vehicle registrations, the business
licence. Verified before building it — no doctype in this app carried an expiry
field for any of them.

Three decisions.

**`Audit` is a category.** The workers' comp premium audit is a deadline with no
certificate behind it, and a register that only holds documents misses exactly
that kind — which is the kind that arrives as a surprise bill.

**The warning horizon is per row.** A contractor licence renewal takes weeks; a
vehicle registration takes a morning. One horizon for both would be wrong for one
of them, and being wrong in the short direction is how a licence lapses.

**A subcontractor's certificate lives here too**, pointed at their Supplier.
Their lapse is our exposure — a claim on an uninsured sub becomes ours — and it is
the one nobody is watching, because it is somebody else's paperwork sitting in
somebody's inbox.

The four status words are copied from the credential register on purpose. Two
expiry models that disagree about what "Expiring" means is worse than either one
alone.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, cint, getdate, today

VALID = "Valid"
EXPIRING = "Expiring"
EXPIRED = "Expired"
SUPERSEDED = "Superseded"

#: Used when a row does not set its own. Matches the credential register's
#: horizon, for the consistency reason in the module docstring.
DEFAULT_LEAD_DAYS = 90


class CompanyObligation(Document):
	def validate(self):
		self._derive_status()

	def _derive_status(self):
		"""Arithmetic on a date, so it is correct the day it is saved and wrong
		every day after — which is why `refresh_obligation_status` re-runs nightly
		and this is only the on-save half."""
		if not cint(self.is_active):
			self.status = SUPERSEDED
			return
		if not self.expires_on:
			self.status = VALID
			return
		due = getdate(self.expires_on)
		now = getdate(today())
		lead = cint(self.lead_days) or DEFAULT_LEAD_DAYS
		if due < now:
			self.status = EXPIRED
		elif due <= getdate(add_days(now, lead)):
			self.status = EXPIRING
		else:
			self.status = VALID
