# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The agreement's branded chrome — letterhead, footer, logo.

Every contract this app generates is read on four surfaces: the desk print view
and its PDF, the on-screen viewer (Preview, the Project Contracts tab, View
Agreement), the public ``/contract-sign`` page, and the executed PDF emailed to
the customer after signing. They must be the same document. This module is the
one place the chrome is defined; all four call :func:`wrap`.

Two constraints shape everything here.

**The chrome must live outside the signed snapshot.** ``executed_body()``
returns the ``agreement_html`` stored on the Contract Signature Request — the
instrument the customer actually signed, frozen so a later template edit can
never change what a signed contract says. Chrome emitted *inside* that snapshot
would therefore never reach the contracts signed before this module existed.
Emitted by the wrapper, it reaches all of them, and it still cannot alter a
single word of the agreement itself.

**The renderer is wkhtmltopdf.** So: no flexbox, no CSS ``counter()``, no
webfonts (see ``esign/render.py`` for why the typed signature avoids one too).
The logo goes in as **inline SVG** rather than ``<img src=...>``, which Qt WebKit
renders unreliably. The footer carries *inline* styles rather than relying on the
stylesheet, because frappe lifts ``#footer-html`` out of the document and renders
it as a separate wkhtmltopdf input — see :func:`footer_html`.
"""

import functools
import re

import frappe

from erpnext_enhancements import print_style

# The wordmark. Navy #00263E with a #00609C accent — the palette the rest of the
# contract styling is built from. Shared with www/fountain-move.html, which
# serves the same file over HTTP; here it is inlined into the document instead.
LOGO_PATH = ("public", "images", "fountain_move", "logo.svg")

# Which pillar an agreement belongs to, by the Contract Template's `template_key`.
# The customer-facing agreements each sit under one line of work; the NDA and the
# employee/contractor agreement belong to the company and take the neutral band.
# Since v1.495.0 (the print design system, docs/print-design-system.md).
PILLAR_BY_TEMPLATE = {
	"owner": "build",
	"architect": "build",
	"sow": "build",
	"msa": "build",
	"maintenance": "service",
	"rental": "rent",
}


def pillar_for(doc):
	"""The pillar key for ``doc``'s template, or ``None`` for the neutral band.

	``doc`` is a Project Contract, a ``frappe._dict`` carrying ``template_key``, or
	``None`` — every caller of :func:`wrap` hands over whichever it has.
	"""
	if doc is None:
		return None
	key = doc.get("template_key") if hasattr(doc, "get") else getattr(doc, "template_key", None)
	return PILLAR_BY_TEMPLATE.get((key or "").strip().lower())

# Rendered width of the wordmark (its natural aspect is 276x100). Set as an
# attribute as well as in CSS: wkhtmltopdf sizes inline SVG from the attributes.
LOGO_WIDTH = 190
LOGO_HEIGHT = 69

_XML_PROLOG = re.compile(r"^\s*<\?xml[^>]*\?>\s*", re.IGNORECASE)
_SVG_DIMENSIONS = re.compile(r'\s(?:width|height)="[^"]*"', re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def logo_svg():
	"""The wordmark as inline ``<svg>`` markup, or ``""`` if it cannot be read.

	Cached for the life of the process — this is a static asset read on the print
	path, and re-reading it once per contract render would be pure waste.

	Returning empty rather than raising is deliberate: a missing or unreadable
	logo must degrade to an unbranded contract, never to a 500 on the customer's
	signing page.
	"""
	try:
		path = frappe.get_app_path("erpnext_enhancements", *LOGO_PATH)
		with open(path, encoding="utf-8") as f:
			markup = f.read()
	except Exception:
		return ""

	markup = _XML_PROLOG.sub("", markup).strip()
	if not markup.startswith("<svg"):
		return ""

	# Re-stamp the dimensions on the root element only. The file ships at its
	# natural 276x100; wkhtmltopdf lays inline SVG out from these attributes, so
	# leaving them would print a logo half again too wide.
	head, sep, tail = markup.partition(">")
	if not sep:
		return ""
	head = _SVG_DIMENSIONS.sub("", head)
	return f'{head} width="{LOGO_WIDTH}" height="{LOGO_HEIGHT}">{tail}'


def letterhead_html(pillar=None):
	"""The branded header block that opens every agreement.

	The print design system's letterhead, minus the address: the pillar stripe,
	the wordmark, and an eyebrow naming the pillar. No address, because six of
	the eight shipped templates already open with their own company/address
	block, and duplicating it under the wordmark would read as a mistake. The two
	that do not (the architect agreement and the NDA) carry Sapphire's address in
	their body text.

	The ``@font-face`` for the display face rides inside the block as an inline
	``<style>``: the block is chrome, emitted outside the signed snapshot, so the
	face reaches every surface — desk print, viewer, signing page, executed PDF —
	and every contract, including those signed before it existed. The stripe is a
	flat colour before a gradient, so wkhtmltopdf, which paints no gradients,
	still paints the pillar's colour.

	One element, always: the stylesheet finds the document title by adjacent
	sibling (``.ct-letterhead + h3``).
	"""
	logo = logo_svg()
	if not logo:
		# No asset: set the name in type, so the document still opens with a
		# header rather than jumping straight into the agreement.
		logo = '<div class="ct-letterhead-name">Sapphire Fountains, LLC</div>'
	record = print_style.pillar(pillar)
	eyebrow = (
		f'<div class="ct-eyebrow" style="color:{record["deep"]};">{record["name"]} &middot; AGREEMENT</div>'
		if record["name"]
		else ""
	)
	stripe = (
		f'<div style="{print_style.stripe_css(pillar)};height:10px;line-height:10px;'
		'font-size:1px;margin-bottom:14px;">&nbsp;</div>'
	)
	return (
		'<div class="ct-letterhead">'
		f"<style>{print_style.font_face_css()}</style>"
		f"{stripe}{logo}{eyebrow}</div>"
	)


def footer_html(doc):
	"""The running footer: contract number on the left, page numbers on the right.

	This markup only ever renders on paper, and it gets there by a specific
	frappe mechanism: ``frappe.utils.pdf.prepare_header_footer`` finds
	``#footer-html``, **extracts it out of the document**, and hands it to
	wkhtmltopdf as ``--footer-html``. The wrapper frappe renders it in
	(``templates/print_formats/pdf_header_footer.html``) runs a ``subst()``
	script on load that fills any element with class ``page`` / ``topage`` from
	wkhtmltopdf's query string — which is where the page numbers come from, and
	why they are empty spans here.

	Two consequences worth spelling out:

	* the styling is **inline**, not in the stylesheet. The extracted footer is
	  rendered as its own standalone document, and betting the footer's
	  appearance on whether the print format's CSS reaches that document is a bet
	  with no upside.
	* on every surface that is *not* a PDF the div is still sitting in the body,
	  so the stylesheet hides it with ``.contract-doc #footer-html``. That
	  selector cannot match in the extracted document (nothing there has a
	  ``.contract-doc`` ancestor), which is exactly the behaviour wanted.
	"""
	label = frappe.utils.escape_html(frappe.utils.cstr(_footer_label(doc)))
	base = "font-family:Lato,'Helvetica Neue',Helvetica,Arial,sans-serif;font-size:8pt;color:#363636;"
	return (
		f'<div id="footer-html" style="{base}border-top:1px solid #00263E;'
		'padding-top:4px;margin:0 15mm;">'
		'<table style="width:100%;border:0;border-collapse:collapse;">'
		f'<tr><td style="border:0;padding:0;text-align:left;">{label}</td>'
		'<td style="border:0;padding:0;text-align:right;">Page '
		'<span class="page"></span> of <span class="topage"></span></td>'
		"</tr></table></div>"
	)


def _footer_label(doc):
	"""What identifies the agreement in the footer — its number, else its title."""
	if doc is None:
		return ""
	getter = doc.get if hasattr(doc, "get") else None
	name = (getter("name") if getter else getattr(doc, "name", None)) or ""
	if name:
		return name
	return (getter("title") if getter else getattr(doc, "title", None)) or ""


def wrap(body_html, doc=None):
	"""Letterhead, then the agreement exactly as given, then the footer.

	``body_html`` is passed through untouched — for a signed contract it is the
	executed instrument, and rewriting so much as its whitespace would defeat the
	snapshot. ``doc`` names the agreement in the footer and, through its
	``template_key``, picks the pillar the letterhead is coloured for.
	"""
	return f"{letterhead_html(pillar_for(doc))}{body_html or ''}{footer_html(doc)}"
