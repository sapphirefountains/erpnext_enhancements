# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Credential Type — the catalogue of qualifications this company cares about.

Deliberately data rather than a Select on ``Employee Credential``. The list of
things a fountain crew needs is not a thing to redeploy for: a new client site
demands a different card, an insurer asks for one nobody had thought about, a
manufacturer starts certifying its own installers. Seeded with the ones that
apply today (``patches/seed_credential_types.py``) and extended by whoever needs
the next one.

``default_validity_months`` is a *hint*, not a rule. The credential controller
uses it to fill in an empty expiry date and never to overwrite one somebody
typed, because the date on the card in their hand beats the arithmetic.
"""

from frappe.model.document import Document


class CredentialType(Document):
	pass
