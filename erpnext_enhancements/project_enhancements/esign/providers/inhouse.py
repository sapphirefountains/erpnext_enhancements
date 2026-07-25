# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""In-house signing: a tokenised link to our own ``/contract-sign`` page.

No network calls, no third-party account, no per-envelope fee. The "envelope" is
the Contract Signature Request row; the "signing session" is a 256-bit token that
exists only in the email we send and as a SHA-256 on the row.

**Resend supersedes rather than extends.** ``resend_intake_link`` keeps its
original token because that token gates nothing; here the plaintext cannot be
recovered from the stored hash even if we wanted to, and one live bearer
credential is a better security posture than N of them accumulating over a
30-day window. A customer who opens the older email gets "this link has been
replaced — send me a new one", not a dead end.
"""

import urllib.parse

import frappe
from frappe.utils import add_days, cint, get_url, now_datetime

from erpnext_enhancements.project_enhancements.esign.providers.base import SignatureProvider
from erpnext_enhancements.project_enhancements.esign.tokens import mint_token

DEFAULT_EXPIRY_DAYS = 30
SIGN_ROUTE = "/contract-sign"


def expiry_days():
	days = cint(
		frappe.db.get_single_value("ERPNext Enhancements Settings", "contract_esign_link_expiry_days")
	)
	return days if days > 0 else DEFAULT_EXPIRY_DAYS


def build_signing_url(token):
	"""Always via ``get_url`` — never hand-build a site URL."""
	return f"{get_url(SIGN_ROUTE)}?ref={urllib.parse.quote(token, safe='')}"


class InHouseProvider(SignatureProvider):
	key = "In-House"

	def create_request(self, request, contract=None):
		"""Mint the token, stamp the hash and expiry, return the one-time URL.

		The plaintext token is returned to the caller (to build the email) and
		then dropped — it is never written to the row.
		"""
		token, token_hash = mint_token()
		request.token_hash = token_hash
		request.expires_on = add_days(now_datetime(), expiry_days())
		return {
			"signing_url": build_signing_url(token),
			"provider_reference": None,
			"provider_status": "Sent",
		}

	def refresh_request(self, request, contract=None):
		"""Resend: a brand-new token, and the old hash is replaced (superseding
		the previous link on this row) — see the module docstring."""
		return self.create_request(request, contract)

	def void_request(self, request, reason=None):
		"""Nothing lives outside our own database, so voiding the row is enough."""
		return None

	def signing_url(self, request):
		"""Unavailable after the fact by design — the plaintext token is not stored.

		Staff who need a working link use Resend, which issues a fresh one.
		"""
		return None
