# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Controller for the single ``Stripe Payments Settings`` document.

Holds the integration's credentials (encrypted secret key + webhook signing
secret), the payment-routing config (deposit account, card/ACH modes of payment)
and connection state. The only logic here is light validation: a *sandbox guard*
that refuses a key whose ``sk_live_``/``sk_test_`` prefix contradicts the selected
``environment`` (so we cannot accidentally transact against the live account while
the build is sandbox-only), plus surfacing the webhook URL to paste into Stripe.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt


class StripePaymentsSettings(Document):
	def validate(self):
		self._guard_environment_vs_key()
		self._set_webhook_url()
		if self.statement_descriptor and len(self.statement_descriptor) > 22:
			frappe.throw("Statement Descriptor must be 22 characters or fewer (Stripe limit).")
		if self.enabled and not self.deposit_account:
			frappe.msgprint(
				"Set a Deposit / Clearing Account before taking live payments — "
				"successful payments cannot post a Payment Entry without it.",
				indicator="orange",
				alert=True,
			)
		self._validate_surcharge()

	def _validate_surcharge(self):
		"""Enforce the hard card-surcharge cap and nudge for the income account.

		The US card-network cap is 3% (and must be ≤ cost of acceptance / the lowest
		applicable state cap). See docs/stripe_surcharging_compliance.md.
		"""
		if not cint(self.surcharge_enabled):
			return
		if flt(self.card_surcharge_percent) > 3:
			frappe.throw(
				"Card Surcharge % cannot exceed the US network cap of 3%. "
				"Some states require less — see docs/stripe_surcharging_compliance.md."
			)
		if not self.surcharge_income_account:
			frappe.msgprint(
				"Set a Surcharge Income Account — collected fees can't post without it.",
				indicator="orange",
				alert=True,
			)

	def _guard_environment_vs_key(self):
		"""Reject a secret key whose prefix contradicts the selected environment.

		Only acts on a freshly entered key (one that still looks like a real
		``sk_...`` value); an unchanged Password field arrives masked, so this never
		false-positives on save.
		"""
		key = (self.secret_key or "").strip()
		if not key.startswith("sk_"):
			return
		if self.environment == "Test" and key.startswith("sk_live_"):
			frappe.throw("Environment is Test but a live secret key (sk_live_…) was supplied.")
		if self.environment == "Live" and key.startswith("sk_test_"):
			frappe.throw("Environment is Live but a test secret key (sk_test_…) was supplied.")

	def _set_webhook_url(self):
		"""Display the endpoint to register in the Stripe Dashboard's webhook settings."""
		self.webhook_url = (
			f"{frappe.utils.get_url()}/api/method/"
			"erpnext_enhancements.stripe_payments.api.stripe_webhook"
		)
