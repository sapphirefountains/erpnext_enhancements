"""Teach the live Rental Agreement template to print a signature (v1.564.0).

The rental twin of ``add_esign_signature_block`` (which is maintenance-only and has already run on
every site, so adding "rental" to its tuple would never execute). Without a ``sig(`` in the live
template, ``esign.api.send_for_signature`` refuses to send a Rental Agreement ("Template Not
Ready"), and event rentals are confirmed by signing one.

``seed_contract_templates`` is insert-only, so the edit to ``templates/contracts/rental_agreement.html``
does not reach a site that already has the record; this replaces the exact paper block under
SIGNATURES — Owner, then RENTER — with the ``sig()`` one. **It never guesses**: if the body does not
contain precisely that block (someone rewrote the wording on the site) it logs and moves on, and
``send_for_signature`` keeps refusing with an explanation rather than executing a contract with an
empty signature line. The Exhibit A condition-report lines stay blanks on purpose: they are filled
in by hand at delivery and at return, not at signing.
"""

import frappe

TEMPLATE_KEY = "rental"

OLD_BLOCK = """<p>Signature: {{ blank(30) }}    Date: {{ blank(30) }}</p>
<p>Print Name: {{ blank(30) }}</p>
<p>Title: {{ blank(30) }}</p>
<p>(Authorized Representative of Sapphire Fountains, LLC)</p>
<h3><b>RENTER</b></h3>
<p>Signature: {{ blank(30) }}    Date: {{ blank(30) }}</p>
<p>Print Name: {{ blank(30) }}</p>
<p>Title: {{ blank(30) }}</p>
<p>(Renter)</p>"""

NEW_BLOCK = """<p>Signature: {{ sig('provider') }}    Date: {{ dt(signature.countersigned_on, 30) }}</p>
<p>Print Name: {{ fill(signature.countersigned_by) }}</p>
<p>Title: {{ fill(signature.countersigned_title) }}</p>
<p>(Authorized Representative of Sapphire Fountains, LLC)</p>
<h3><b>RENTER</b></h3>
<p>Signature: {{ sig('client') }}    Date: {{ dt(doc.signed_on, 30) }}</p>
<p>Print Name: {{ fill(doc.signed_by) }}</p>
<p>Title: {{ fill(signature.signed_title) }}</p>
<p>(Renter)</p>"""


def rewrite_signature_block(body):
	"""``(new_body, changed)``. Refuses anything but exactly one untouched paper block."""
	if not body:
		return body, False
	if "sig(" in body:
		return body, False
	if body.count(OLD_BLOCK) != 1:
		return body, False
	return body.replace(OLD_BLOCK, NEW_BLOCK), True


def execute():
	body = frappe.db.get_value("Contract Template", TEMPLATE_KEY, "body")
	if not body or "sig(" in body:
		return
	new_body, changed = rewrite_signature_block(body)
	if not changed:
		frappe.log_error(
			"The live Rental Agreement template's SIGNATURES block does not match the shipped wording, "
			"so it was left alone. Add {{ sig('client') }} and {{ sig('provider') }} to it by hand; until "
			"then Send for Signature refuses rental agreements.",
			"Contract e-sign: rental signature block not patched",
		)
		return
	frappe.db.set_value("Contract Template", TEMPLATE_KEY, "body", new_body, update_modified=False)
	frappe.db.commit()
