# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The signature-provider interface.

A provider owns exactly one thing: **how a signature is collected**. It knows
nothing about what a signature means. Everything downstream of collection —
stamping the evidence, flipping the contract to Signed, drafting the operational
maintenance contract, building and delivering the signed PDF — lives in
``esign.lifecycle`` and is shared by every provider.

That is the seam. Adding DocuSign later is a new module here plus one registry
entry plus a webhook that normalises its payload into a
:class:`~erpnext_enhancements.project_enhancements.esign.lifecycle.SignatureEvidence`
and calls ``lifecycle.execute_contract``. No change to the doctype, the contract
controller, the print format, or the lifecycle itself.
"""


class SignatureProvider:
	"""Base class. Subclasses set ``key`` and implement the four methods."""

	key = "In-House"

	def create_request(self, request, contract):
		"""Prepare a signing session for a freshly-inserted request row.

		Returns ``{"signing_url", "provider_reference", "provider_status"}``.
		Must be idempotent per row — a retried send must not create a second
		envelope at the provider.
		"""
		raise NotImplementedError

	def refresh_request(self, request, contract):
		"""Re-issue for a resend. Same return shape as :meth:`create_request`."""
		raise NotImplementedError

	def void_request(self, request, reason=None):
		"""Cancel at the provider. Must tolerate "already gone" without raising."""
		raise NotImplementedError

	def signing_url(self, request):
		"""The URL a signer would open for this request."""
		raise NotImplementedError
