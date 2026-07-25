# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The one helper that puts a signature into a rendered agreement.

``sig(party)`` is injected into the contract Jinja context by
``project_contract._render_context`` and used in the SIGNATURES block of the
templates in ``templates/contracts/``. It returns:

* the drawn signature as an ``<img>``, or the adopted name in a signature face
  (Typed), when the contract has a signed :doc:`Contract Signature Request`;
* **otherwise exactly what ``blank()`` returns today.**

That fallthrough is the whole design. It means an unsigned contract prints the
same paper blanks it always has, every existing template keeps rendering, and the
"no unrendered Jinja" template test in ``tests/test_project_contract.py`` passes
untouched — the helper adds a capability without changing any existing output.

The typed signature deliberately uses a **plain serif italic**, not a script
webfont: the browser preview and the wkhtmltopdf render must agree, and a webfont
that loads in one and not the other would show the customer something different
from what their PDF says.
"""

import frappe

# Kept in sync with the print CSS in fixtures/print_format.json.
_TYPED_STYLE = 'font-family:"Times New Roman",serif;font-style:italic;font-size:15pt;'


def signed_signature_for(doc):
	"""The signed Contract Signature Request for a contract, or None.

	Resolved **once** per render and closed over by ``sig()`` — the SIGNATURES
	block calls the helper several times per document. Never raises: this runs on
	the print path, and a print must not 500 because an evidence lookup hiccupped.
	"""
	name = doc.get("name") if hasattr(doc, "get") else None
	if not name:
		return None
	try:
		return frappe.db.get_value(
			"Contract Signature Request",
			{"project_contract": name, "status": "Signed"},
			[
				"name",
				"signature_mode",
				"signed_name",
				"signed_title",
				"signature_image",
				"countersigned_by",
				"countersigned_title",
				"countersigned_on",
			],
			order_by="signed_on desc",
			as_dict=True,
		)
	except Exception:
		return None


def signature_markup(signature, party="client", width=30, blank=None):
	"""Signature markup for ``party`` ("client" | "provider"), else a blank line.

	``blank`` is the caller's blank-line helper, passed in so this module stays
	independent of the contract controller (and so the fallback is always byte
	-identical to the surrounding paper form).
	"""
	fallback = blank(width) if blank else ""
	if not signature:
		return fallback

	if party == "provider":
		# The countersignature is recorded, never required — an executed contract
		# with no countersigner still prints a blank line for a wet one.
		name = signature.get("countersigned_by")
		return _typed(name) if name else fallback

	if signature.get("signature_mode") == "Drawn" and signature.get("signature_image"):
		return (
			f'<img class="ct-sig" src="{frappe.utils.escape_html(signature["signature_image"])}" '
			f'alt="Signature of {frappe.utils.escape_html(signature.get("signed_name") or "")}">'
		)
	return _typed(signature.get("signed_name")) or fallback


def _typed(name):
	if not name:
		return ""
	return f'<span class="ct-sig-typed" style="{_TYPED_STYLE}">{frappe.utils.escape_html(str(name))}</span>'
