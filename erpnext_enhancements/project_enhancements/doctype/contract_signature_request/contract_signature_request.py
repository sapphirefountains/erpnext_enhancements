# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""One request to have a Project Contract signed — and, once signed, the
evidence that it was.

**Append-only by design.** Every field is ``read_only`` and no role holds
``create`` or ``delete``: the rows are written by
``project_enhancements.esign`` with ``ignore_permissions=True`` and are never
edited or removed afterwards. That is the same shape as ``Stripe Autopay
Consent`` — evidence integrity comes from there being no write path, not from
controller logic — which is why this controller is deliberately almost empty.

Why the evidence lives here and not on Project Contract: ``hooks.py`` binds
``on_update_after_submit`` on Project Contract to
``autocreate_maintenance_contract_on_signed``, so *every* save of a submitted
contract runs the maintenance-contract automation. Stamping a view timestamp on
the contract would fire it. With a separate row the contract is saved **exactly
once, ever** — at the moment of execution.

The trust model is the inverse of ``Fountain Move Invite``, whose docstring
says "the token is attribution, not authorisation". **This token is
authorisation**: possession of it plus knowledge of one email address executes a
contract. Hence the token is stored only as a SHA-256 hash (``token_hash``), the
recipient address is frozen at send (``signer_email``), and the provider in force
is frozen on the row (``provider``) so flipping a Settings switch cannot orphan a
link already in flight.

Multi-signer is deliberately not modelled. It is a provider concept (DocuSign
routing order); when it is needed it arrives as a child table without disturbing
anything here.
"""

import frappe
from frappe.model.document import Document

# Statuses a signing link is still live in — anything else means the link is
# spent, retired or was never sent.
LIVE_STATUSES = ("Sent", "Viewed")


class ContractSignatureRequest(Document):
	@property
	def is_live(self):
		"""True when this request's link could still be used to sign.

		Expiry is checked against the stored ``expires_on``; the daily sweeper
		flips stale rows to ``Expired`` so the list view tells the truth, but the
		live check here is the control — never the sweeper.
		"""
		if self.status not in LIVE_STATUSES:
			return False
		if self.expires_on and frappe.utils.now_datetime() > frappe.utils.get_datetime(self.expires_on):
			return False
		return True
