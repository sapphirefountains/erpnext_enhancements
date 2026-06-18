# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for ``Stripe Event`` — the inbound-webhook audit trail and the
idempotency ledger. The document name *is* the Stripe ``event_id`` (autoname
``field:event_id``), so the unique constraint alone prevents a redelivered event
from being processed twice. Append-only; no controller logic."""

from frappe.model.document import Document


class StripeEvent(Document):
	pass
