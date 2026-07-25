"""Teach the live Maintenance Services Agreement template to print a signature.

``seed_contract_templates`` is deliberately **insert-only**, so editing the
version-controlled HTML in ``templates/contracts/`` does not reach a site that
already has the Contract Template record. Without this patch a site would keep
printing the paper blanks, and an e-signed contract would produce a PDF with an
empty signature line.

**This patch never guesses.** It replaces one exact block — the two
``Signature: {{ blank(30) }}`` lines under ``<h1>SIGNATURES</h1>`` — and if the
body does not contain precisely that block (because someone's legal team rewrote
it on the site) it logs and moves on rather than overwriting bespoke wording.
That is the same instinct the insert-only seed encodes.

The safety net for "the patch did not apply here" lives in
``esign.api.send_for_signature``, which refuses to send a contract whose live
template has no ``sig(`` in it. A failed patch therefore surfaces as a blocked
send with an explanation, never as an executed contract with a blank signature.
"""

import frappe

TEMPLATE_KEYS = ("maintenance",)

OLD_BLOCK = """<p>Signature: {{ blank(30) }}    Date: {{ blank(30) }}</p>
<p>Print Name: {{ blank(30) }}</p>
<p>Title: {{ blank(30) }}</p>
<p>(Authorized Representative of Sapphire Fountains, LLC)</p>
<h3><b>CLIENT</b></h3>
<p>Signature: {{ blank(30) }}    Date: {{ blank(30) }}</p>
<p>Print Name: {{ blank(30) }}</p>"""

NEW_BLOCK = """<p>Signature: {{ sig('provider') }}    Date: {{ dt(signature.countersigned_on, 30) }}</p>
<p>Print Name: {{ fill(signature.countersigned_by) }}</p>
<p>Title: {{ fill(signature.countersigned_title) }}</p>
<p>(Authorized Representative of Sapphire Fountains, LLC)</p>
<h3><b>CLIENT</b></h3>
<p>Signature: {{ sig('client') }}    Date: {{ dt(doc.signed_on, 30) }}</p>
<p>Print Name: {{ fill(doc.signed_by) }}</p>"""


def rewrite_signature_block(body):
	"""Return ``(new_body, changed)``. ``changed`` is False when we must not touch it."""
	if not body:
		return body, False
	if "sig(" in body:
		return body, False  # already patched — idempotent
	if body.count(OLD_BLOCK) != 1:
		return body, False  # diverged (or absent) — refuse and report
	return body.replace(OLD_BLOCK, NEW_BLOCK), True


def execute():
	for key in TEMPLATE_KEYS:
		body = frappe.db.get_value("Contract Template", key, "body")
		if not body:
			continue
		if "sig(" in body:
			continue

		new_body, changed = rewrite_signature_block(body)
		if not changed:
			frappe.log_error(
				f"Contract Template '{key}' does not contain the expected SIGNATURES block, so the "
				"e-signature markers were not added. Paste the signature block from "
				"templates/contracts/ into the template by hand — until then, Send for Signature "
				"will refuse this template rather than produce an unsigned-looking PDF.",
				"Contract e-sign: signature block not patched",
			)
			continue

		frappe.db.set_value("Contract Template", key, "body", new_body, update_modified=False)

	frappe.db.commit()
