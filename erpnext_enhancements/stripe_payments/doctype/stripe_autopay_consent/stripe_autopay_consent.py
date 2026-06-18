# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for ``Stripe Autopay Consent`` — the proof-of-authorization record
for storing a customer's payment method and charging it off-session (card on file
+ ACH). Captured at enrollment and activated when the setup completes; kept as the
Nacha/card-network record of authorization (amount/timing/revocation terms, who
agreed, when, and from where). Append-only; no controller logic."""

from frappe.model.document import Document


class StripeAutopayConsent(Document):
	pass
