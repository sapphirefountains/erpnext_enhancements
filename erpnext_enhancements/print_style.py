# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The print design system — the chrome every printed document shares.

The print twin of :mod:`email_style`, built for the same reason: the app printed
from four Python-composed format modules and two fixtures that shared no
letterhead, no palette and no type, and the customer-facing ones (WI-020) shipped
in a generic grey Helvetica look with the site's Letter Head pasted top-right.
This module is the one place the *Pillar Stripe* concept lives for paper — the
concept Nik picked on 2026-09-21 from the four on the design canvas.

The look, in the design system's own terms (its tokens are the only colours here):

* a **pillar stripe** along the top edge — the pillar's left-to-right gradient,
  ``open`` stop first — and a thinner one along the bottom;
* the **wordmark on white** with our address and phone beside it;
* an **eyebrow** naming the pillar and the document, in the pillar's closing
  stop (the one that clears AA at small sizes), over a **display-face title**
  in ``deep-sea-blue``;
* body in Lato-or-system sans, ``ink-700``; section titles in the display face;
* tables ruled, never boxed: a 2px rule in the pillar's opening stop under the
  header row, hairlines in ``border-100`` between rows, no zebra fills;
* square structure, no shadows, nothing rounded but a button (there are none
  on paper).

**Frappe-free on purpose**, like ``enhancements_core/company_contact.py``. The
four format modules compose their HTML at ``after_migrate`` and the bench-free
suites ``exec`` them under a stub ``frappe``; a ``import frappe`` at the top of
this file would break both. The two things that need a file — the display font
and the wordmark — are read relative to ``__file__``.

**Why the font is a data URI.** Print formats render server-side in Chromium (see
``enhancements_core/setup_print_formats.ensure_chrome_pdf_generator``); a
``url(/assets/...)`` would make every PDF depend on the worker reaching the
site's own public origin, which is one more thing to go wrong on a host whose
PDF history is ``docs/pdf-generation.md``. Base64 of a 13 KB woff2 is ~18 KB per
document and depends on nothing. wkhtmltopdf, still the fallback for report
exports, takes a data-URI ``@font-face`` too. The fallback stack is
``Arial Narrow`` — a condensed bold that reads as the same idea when the face
is absent.

**Why the wordmark is inline SVG.** Same file the contracts inline
(``public/images/fountain_move/logo.svg``, 276×100, two inks baked in), for the
same reason ``contract_style`` gives: ``<img>`` is the unreliable path through
the PDF backends. Print-safe CSS throughout — ``display:table`` for the
letterhead, no flex, no grid — because ``tests/test_sales_print_formats.py``
forbids them and wkhtmltopdf cannot lay them out.

Pillars: ``service``, ``build``, ``design``, ``rent``, or ``None`` for a document
that belongs to the company rather than to one pillar (sales documents span all
four, so they take the neutral navy stripe until a document can say which pillar
it is under).
"""

import base64
import functools
import os
import re

from erpnext_enhancements.enhancements_core.company_contact import (
	COMPANY_ADDRESS_HTML,
	COMPANY_PHONE,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(_HERE, "public", "fonts", "big_noodle_titling.woff2")
LOGO_PATH = os.path.join(_HERE, "public", "images", "fountain_move", "logo.svg")

# ---------------------------------------------------------------------------
# The palette. Every value is a token of the Sapphire Fountains design system,
# by its token name; nothing here is invented. Contrast is against white.
DEEP_SEA_BLUE = "#00263e"  # titles, rules — 14.1:1
NAVY_800 = "#002136"  # Build's closing stop
NAVY_900 = "#00111c"  # the dark gradient's end, and the label on a fresh-blue fill
BAHAMA_BLUE = "#00609c"  # eyebrows and labels on light grounds — 6.7:1
FRESH_BLUE = "#00a0df"  # Service's opening stop; a fill, never small text (3.0:1)
FRESH_BLUE_DEEP = "#005779"  # Service's closing stop — 7.9:1
VIOLET = "#b14fc5"  # Design's opening stop
VIOLET_DEEP = "#55265f"  # Design's closing stop — 10.9:1
TEAL = "#62cbc9"  # Rent's opening stop
TEAL_DEEP = "#316564"  # Rent's closing stop — 6.4:1
OFF_WHITE = "#f8f8f8"
INK_900 = "#151515"  # the strongest ink: totals, names
INK_700 = "#363636"  # body copy — 12.1:1
BORDER_100 = "#dadbdd"  # hairlines between rows. Never carries text.
# Status colours come from the 2016 brand guide's supportive set; the token set
# has none and the guide says to take them from here rather than invent one.
RED = "#bd2e2b"  # out of range, failed — 5.9:1
YELLOW = "#ffb819"  # attention rule only — 1.7:1, so never text

DISPLAY_FONT = "'Big Noodle Titling','Arial Narrow','Oswald',Arial,sans-serif"
SANS_FONT = "Lato,'Helvetica Neue',Helvetica,Arial,sans-serif"

# ---------------------------------------------------------------------------
# The four pillars. `open` is the gradient's first stop and the only flat form of
# the colour; `deep` is the closing stop and the one safe for small text. The
# neutral entry is the brand's dark band, for documents that belong to no pillar.
PILLARS = {
	"service": {"name": "SERVICE", "open": FRESH_BLUE, "deep": FRESH_BLUE_DEEP},
	"build": {"name": "BUILD", "open": BAHAMA_BLUE, "deep": NAVY_800},
	"design": {"name": "DESIGN", "open": VIOLET, "deep": VIOLET_DEEP},
	"rent": {"name": "RENT", "open": TEAL, "deep": TEAL_DEEP},
}
NEUTRAL = {"name": "", "open": DEEP_SEA_BLUE, "deep": BAHAMA_BLUE}


def pillar(key):
	"""The pillar record for ``key`` — ``None`` or an unknown key is the neutral band."""
	return PILLARS.get((key or "").lower(), NEUTRAL)


def stripe_css(key):
	"""The stripe's background: flat opening stop first, so a renderer with no
	gradient support still paints the pillar's colour."""
	p = pillar(key)
	if p is NEUTRAL:
		# The brand's dark band runs deep-sea-blue into navy-900; on a stripe a
		# few pixels tall the angle is invisible, so it borrows the pillars' 90deg.
		return f"background-color:{DEEP_SEA_BLUE};background-image:linear-gradient(90deg,{DEEP_SEA_BLUE} 0%,{NAVY_900} 100%)"
	return f"background-color:{p['open']};background-image:linear-gradient(90deg,{p['open']} 0%,{p['deep']} 100%)"


# ---------------------------------------------------------------------------
# Assets, read once per process.


@functools.lru_cache(maxsize=1)
def font_face_css():
	"""An ``@font-face`` for the display face with the woff2 inlined, or ``""``.

	Empty rather than raised when the file is missing: a document without its
	display face prints in Arial Narrow, a document that raised prints nothing.
	"""
	try:
		with open(FONT_PATH, "rb") as handle:
			data = base64.b64encode(handle.read()).decode("ascii")
	except OSError:
		return ""
	return (
		"@font-face{font-family:'Big Noodle Titling';font-weight:700;font-style:normal;"
		f"src:url(data:font/woff2;base64,{data}) format('woff2')}}"
	)


_XML_PROLOG = re.compile(r"^\s*<\?xml[^>]*\?>\s*", re.IGNORECASE)
_SVG_DIMENSIONS = re.compile(r'\s(?:width|height)="[^"]*"', re.IGNORECASE)


@functools.lru_cache(maxsize=4)
def logo_svg(width=170):
	"""The wordmark as inline ``<svg>`` at ``width`` px (natural ratio 276:100), or ``""``."""
	try:
		with open(LOGO_PATH, encoding="utf-8") as handle:
			markup = handle.read()
	except OSError:
		return ""
	markup = _XML_PROLOG.sub("", markup).strip()
	if not markup.startswith("<svg"):
		return ""
	head, sep, tail = markup.partition(">")
	if not sep:
		return ""
	head = _SVG_DIMENSIONS.sub("", head)
	height = round(width * 100 / 276)
	return f'{head} width="{width}" height="{height}" style="display:block;width:{width}px;height:{height}px">{tail}'


# ---------------------------------------------------------------------------
# The chrome. Each function returns a fragment of HTML; the format modules
# concatenate them around their own body. Jinja braces inside are meant for the
# print renderer, which is why the address is a Jinja expression rather than a
# Python one — the document's own company address wins, exactly as
# `company_contact` explains.

# The page. `font-size` is 9.5pt-equivalent for tables; prose steps up per block.
PAGE_STYLE = f"font-family:{SANS_FONT};color:{INK_700};font-size:12.5px;line-height:1.4"

TH = (
	f"text-align:left;padding:5px 8px;font-size:11.5px;font-weight:700;color:{BAHAMA_BLUE};"
	f"vertical-align:bottom;border-bottom:2px solid __OPEN__"
)
TH_RIGHT = TH.replace("text-align:left", "text-align:right")
TD = f"padding:5px 8px;border-bottom:1px solid {BORDER_100};vertical-align:top;color:{INK_700}"
TD_RIGHT = TD + ";text-align:right;white-space:nowrap"
LABEL = f"font-size:11.5px;font-weight:700;color:{BAHAMA_BLUE}"
STRONG = f"font-weight:700;color:{INK_900}"
FAIL = f"font-weight:700;color:{RED}"


def th(key="", right=False):
	"""A header-cell style with the pillar's opening stop as its rule."""
	return (TH_RIGHT if right else TH).replace("__OPEN__", pillar(key)["open"])


def page_open(key=None):
	"""Opens the document: the ``@font-face``, the top stripe, the outer div."""
	return (
		f"<style>{font_face_css()}</style>\n"
		f'<div style="{stripe_css(key)};height:12px;line-height:12px;font-size:1px">&nbsp;</div>\n'
		f'<div style="{PAGE_STYLE};padding:18px 0 0">\n'
	)


def page_close(key=None):
	"""Closes the document with the running line and the bottom stripe."""
	return (
		"\n</div>\n"
		f'<div style="margin-top:18px;padding-top:8px;border-top:1px solid {BORDER_100};'
		f'font-size:10.5px;color:{INK_700};font-family:{SANS_FONT}">'
		"Sapphire Fountains, LLC &middot; 85 W 300 S, Bountiful, UT 84010 &middot; "
		"www.sapphirefountains.com</div>\n"
		f'<div style="{stripe_css(key)};height:5px;line-height:5px;font-size:1px;margin-top:8px">&nbsp;</div>\n'
	)


def letterhead(key, eyebrow, title, meta_html, address_field=None):
	"""The wordmark and our contact details, then the eyebrow, title and meta block.

	``address_field`` is the document's company-address field — the sales
	doctypes' ``company_address_display`` — rendered as Jinja so the document's
	own address wins and the constant covers a document without one. ``None``
	prints the constant outright, for documents that carry no company address.
	``meta_html`` is the right-hand block under the title: number, date, status.
	"""
	p = pillar(key)
	eyebrow_text = f"{p['name']} &middot; {eyebrow}" if p["name"] else eyebrow
	if address_field:
		address = (
			"{%- if doc." + address_field + " %}{{ doc." + address_field + " }}"
			"{% else %}" + COMPANY_ADDRESS_HTML + "{% endif -%}"
		)
	else:
		address = COMPANY_ADDRESS_HTML
	return (
		'<div style="display:table;width:100%;margin-bottom:16px">\n'
		'  <div style="display:table-cell;width:50%;vertical-align:top">' + logo_svg(170) + "</div>\n"
		'  <div style="display:table-cell;width:50%;vertical-align:top;text-align:right;'
		f'font-size:11px;line-height:1.45;color:{INK_700}">'
		"<b>Sapphire Fountains, LLC</b><br>" + address + "<br>" + COMPANY_PHONE + "</div>\n"
		"</div>\n"
		'<div style="display:table;width:100%;margin-bottom:12px">\n'
		'  <div style="display:table-cell;width:60%;vertical-align:bottom">'
		f'<div style="{LABEL};color:{p["deep"]}">{eyebrow_text}</div>'
		f'<h1 style="margin:4px 0 0;font-family:{DISPLAY_FONT};font-weight:700;font-size:38px;'
		f'line-height:38px;color:{DEEP_SEA_BLUE}">{title}</h1></div>\n'
		'  <div style="display:table-cell;width:40%;vertical-align:bottom;text-align:right;'
		f'font-size:12px;line-height:1.5;color:{INK_700}">' + meta_html + "</div>\n"
		"</div>\n"
	)


def section_title(text):
	"""A section heading in the display face, `bahama-blue` on the light page."""
	return (
		f'<h2 style="margin:16px 0 6px;font-family:{DISPLAY_FONT};font-weight:700;font-size:20px;'
		f'line-height:20px;color:{BAHAMA_BLUE};page-break-after:avoid">{text}</h2>\n'
	)


def facts_open(top=True):
	"""A row of labelled facts under the letterhead, ruled above and below.

	``top=False`` drops the heavier top rule, for a second row that continues the
	first — the Purchase Order needs six facts, and two rules a row apart read as
	two blocks rather than one.
	"""
	rule = f"border-top:1px solid {DEEP_SEA_BLUE};" if top else ""
	return (
		f'<div style="display:table;width:100%;margin:0 0 6px;padding:10px 0;'
		f'{rule}border-bottom:1px solid {BORDER_100}">\n'
	)


def fact(label, value_html, width="33%"):
	return (
		f'  <div style="display:table-cell;width:{width};vertical-align:top;padding-right:16px">'
		f'<div style="{LABEL}">{label}</div>'
		f'<div style="margin-top:2px;font-size:12.5px;line-height:1.4">{value_html}</div></div>\n'
	)


def facts_close():
	return "</div>\n"


def signature_lines(*labels):
	"""Ruled signature lines side by side — ``("ACCEPTED BY", "DATE")``."""
	cells = []
	width = f"{100 // len(labels)}%"
	for label in labels:
		cells.append(
			f'  <div style="display:table-cell;width:{width};vertical-align:top;padding-right:32px">'
			f'<div style="{LABEL}">{label}</div>'
			f'<div style="height:34px;border-bottom:1px solid {DEEP_SEA_BLUE}"></div></div>\n'
		)
	return (
		'<div style="display:table;width:100%;margin-top:14px;page-break-inside:avoid">\n'
		+ "".join(cells)
		+ "</div>\n"
	)


# ---------------------------------------------------------------------------
# Jinja globals for the Jinja-authored fixture formats (`Maintenance Record
# Print`), registered individually in hooks.py under the `ps_` prefix — the same
# reasoning as the `ee_` globals: get_jinja_hooks would otherwise put every
# function of a module-valued entry into the namespace of every template.


def ps_page_open(pillar_key=None):
	return page_open(pillar_key)


def ps_page_close(pillar_key=None):
	return page_close(pillar_key)


def ps_letterhead(pillar_key, eyebrow, title, meta_html="", address_field=None):
	return letterhead(pillar_key, eyebrow, title, meta_html, address_field)


def ps_section_title(text):
	return section_title(text)


def ps_th(pillar_key=None, right=False):
	return th(pillar_key, right)


def ps_td(right=False):
	return TD_RIGHT if right else TD


def ps_facts_open(top=True):
	return facts_open(top)


def ps_fact(label, value_html, width="33%"):
	return fact(label, value_html, width)


def ps_facts_close():
	return facts_close()


def ps_signature_lines(*labels):
	return signature_lines(*labels)


def ps_style(name):
	"""A named style string: ``label``, ``strong``, ``fail``, ``td``, ``td_right``."""
	return {"label": LABEL, "strong": STRONG, "fail": FAIL, "td": TD, "td_right": TD_RIGHT}.get(name, "")
