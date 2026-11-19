# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bot check for the signing page.

The verifier itself is **reused, not re-implemented** — see
``crm_enhancements.fountain_move.intake._verify_turnstile``. That function
carries the hard-won details (the four-verdict model, the action and hostname
assertions, and above all the freshness check that must compare in UTC; a
site-local comparison once made every legitimate solve fail by ~6 hours). A
second copy would be a second chance to get that wrong.

What differs here is **policy, not mechanism**.

On the intake form Turnstile is the *primary* control: anyone with a browser can
reach that page, so an unverifiable submission is parked for a human — "an outage
costs a delay, not a flood".

On the signing page it is *tertiary*. To get this far a caller must already hold
a 256-bit single-use token that we emailed to an address we chose, and must enter
the code we email to that address. Automated abuse is not the residual threat once
both hold. So an ``Unavailable`` verdict — Cloudflare unreachable as observed by the
server, not a failed challenge —
**accepts the signature**, records the verdict on the evidence record and flags
it in the staff alert. A request that carries no bot-check token at all (the widget
did not load or errored in the browser) is recorded the same way, with a detail that
says no token was sent, and goes on to the emailed code, which is the identity check,
under the same limits. Refusing would cost a real, certain contract to defend
against a threat the token has already eliminated, and the blast radius is
bounded anyway: the operational Maintenance Contract that signing creates is a
**Draft** a human still has to activate.

``Failed`` (a token was sent and Cloudflare said no) still refuses.

The verdict always comes from :func:`verify`, on the server's own configuration: with no
secret configured it is "Not Checked", with a secret and no token it is "Unavailable",
and otherwise the token is checked. The page has no way to state the outcome itself.
"""

import frappe

TURNSTILE_ACTION = "contract-sign"
SECRET_FIELD = "contract_esign_turnstile_secret_key"
SITE_KEY_FIELD = "contract_esign_turnstile_site_key"

NO_TOKEN_DETAIL = "No bot-check token was sent by the browser."

# Verdicts that let a signature through. "Not Checked" means no keys are
# configured at all, which is only reachable on a site that turned the page on
# without them — treated the same as an outage rather than as a silent block.
ACCEPTED_VERDICTS = ("Passed", "Unavailable", "Not Checked")


def site_key():
	return frappe.db.get_single_value("ERPNext Enhancements Settings", SITE_KEY_FIELD)


def verify(token):
	"""Return ``(verdict, detail)`` for a signing-page challenge.

	A request with no token (None or blank) on a site that has a secret is
	"Unavailable" with a detail saying no token was sent, not "Failed": only a token
	that was sent and rejected is a failed challenge.
	"""
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import _verify_turnstile

	verdict, detail = _verify_turnstile(token, action=TURNSTILE_ACTION, secret_field=SECRET_FIELD)
	if verdict == "Failed" and not str(token or "").strip():
		return "Unavailable", {"errors": NO_TOKEN_DETAIL}
	return verdict, detail


def accepts(verdict):
	"""Whether ``verdict`` permits the signature — see the module docstring."""
	return verdict in ACCEPTED_VERDICTS
