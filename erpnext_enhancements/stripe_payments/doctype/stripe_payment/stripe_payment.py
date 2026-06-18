# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for ``Stripe Payment`` — the operational ledger row for one payment.

One row is created per Checkout Session (or, in a later phase, per off-session
charge). It tracks the Stripe ids, the amount/method, the lifecycle ``status`` and
the resulting ERPNext ``payment_entry``. It is a plain (non-submittable) record;
all state transitions are driven server-side by the webhook reconciler, so there
is no controller logic beyond being a data holder.
"""

from frappe.model.document import Document


class StripePayment(Document):
	pass
