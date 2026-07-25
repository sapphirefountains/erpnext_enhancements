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
a 256-bit single-use token that we emailed to an address we chose, and must know
that address. Automated abuse is not the residual threat once both hold. So an
``Unavailable`` verdict — Cloudflare unreachable, not a failed challenge —
**accepts the signature**, records the verdict on the evidence record and flags
it in the staff alert. Refusing would cost a real, certain contract to defend
against a threat the token has already eliminated, and the blast radius is
bounded anyway: the operational Maintenance Contract that signing creates is a
**Draft** a human still has to activate.

``Failed`` (Cloudflare actively said no) still refuses.
"""

import frappe

TURNSTILE_ACTION = "contract-sign"
SECRET_FIELD = "contract_esign_turnstile_secret_key"
SITE_KEY_FIELD = "contract_esign_turnstile_site_key"

# Verdicts that let a signature through. "Not Checked" means no keys are
# configured at all, which is only reachable on a site that turned the page on
# without them — treated the same as an outage rather than as a silent block.
ACCEPTED_VERDICTS = ("Passed", "Unavailable", "Not Checked")


def site_key():
	return frappe.db.get_single_value("ERPNext Enhancements Settings", SITE_KEY_FIELD)


def verify(token):
	"""Return ``(verdict, detail)`` for a signing-page challenge."""
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import _verify_turnstile

	return _verify_turnstile(token, action=TURNSTILE_ACTION, secret_field=SECRET_FIELD)


def accepts(verdict):
	"""Whether ``verdict`` permits the signature — see the module docstring."""
	return verdict in ACCEPTED_VERDICTS
