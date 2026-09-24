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
from html import unescape as _unescape

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

# Cell geometry carries `!important`, and it has to. Frappe appends two stylesheets to
# every print format, custom ones included -- `templates/styles/standard.css` sets
# `.print-format td, .print-format th {padding:6px !important; vertical-align:top
# !important}` and this site's "Redesign" Print Style sets `padding:10px !important` and
# `border-bottom-width:1px !important` on `th`. An `!important` in a stylesheet beats an
# ordinary inline style, so until v1.535.0 none of the padding, alignment or 2px header
# rule below ever reached a page: the rows printed half as dense again as designed and a
# six-line invoice pushed its totals onto page 2. An inline `!important` is the one thing
# that outranks a stylesheet `!important`. Colour and weight are not forced by either
# sheet, so they stay ordinary.
_CELL = "padding:5px 8px !important;vertical-align:top !important"

TH = (
	f"text-align:left;padding:5px 8px !important;font-size:11.5px;font-weight:700;color:{BAHAMA_BLUE};"
	f"vertical-align:bottom !important;border-bottom:2px solid __OPEN__ !important"
)
TH_RIGHT = TH.replace("text-align:left", "text-align:right")
TD = f"{_CELL};border-bottom:1px solid {BORDER_100} !important;color:{INK_700}"
TD_RIGHT = TD + ";text-align:right;white-space:nowrap"
LABEL = f"font-size:11.5px;font-weight:700;color:{BAHAMA_BLUE}"
STRONG = f"font-weight:700;color:{INK_900}"
FAIL = f"font-weight:700;color:{RED}"

# The totals block under a line table, shared by every priced format so the family
# cannot drift apart one edit at a time. Borderless cells, so `border:0 !important`:
# the Redesign sheet draws nothing on a plain `td`, but a site that switches Print Style
# must not grow rules between the totals.
TOTAL_LABEL = f"text-align:right;padding:3px 8px !important;border:0 !important;color:{INK_700}"
TOTAL_VALUE = TOTAL_LABEL + ";white-space:nowrap"
GRAND = (
	"text-align:right;padding:8px 8px 4px !important;border:0 !important;"
	f"border-top:2px solid {DEEP_SEA_BLUE} !important;{STRONG}"
)
# The empty spacer cell to the left of the totals.
TOTAL_SPACER = "padding:0 !important;border:0 !important"


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

	**An eyebrow that only repeats the title is dropped** (v1.535.0). The eyebrow exists
	to name the pillar — ``SERVICE · VISIT REPORT`` — and a neutral document has no
	pillar to name, so from v1.494.0 every sales and buying document printed its own name
	twice, one line apart: ``INVOICE`` over ``INVOICE``, since the display face is all
	capitals. The comparison is case-insensitive over the raw strings, so a Jinja pair such
	as ``{% if doc.is_return %}CREDIT NOTE{% else %}INVOICE{% endif %}`` over its
	mixed-case twin counts as a repeat too. With a pillar, the pillar's name stays on its
	own; an eyebrow that says something the title does not (``TRAINING · CERTIFICATE OF
	COMPLETION``) stays whole.
	"""
	p = pillar(key)
	repeats = (eyebrow or "").strip().upper() == (title or "").strip().upper()
	if p["name"]:
		eyebrow_text = p["name"] if repeats else f"{p['name']} &middot; {eyebrow}"
	else:
		eyebrow_text = "" if repeats else eyebrow
	eyebrow_html = f'<div style="{LABEL};color:{p["deep"]}">{eyebrow_text}</div>' if eyebrow_text else ""
	if address_field:
		# Through `ps_address`, which trims the line break every United States address
		# ends with: printed raw, it left a blank line above our phone number.
		address = (
			"{%- if doc." + address_field + " %}{{ ps_address(doc." + address_field + ") }}"
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
		+ eyebrow_html
		+ f'<h1 style="margin:{"4px" if eyebrow_html else "0"} 0 0;font-family:{DISPLAY_FONT};font-weight:700;font-size:38px;'
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
# Content helpers. What a value looks like on paper, as opposed to where it comes
# from: `print_lookup` finds a party's phone number, this module decides that
# `8015550100` prints as `(801) 555-0100`. Frappe-free, so the bench-free suites test
# them directly; the format templates reach them through the `ps_*` globals below.

_TAG = re.compile(r"<[A-Za-z/!][^>]*>")
_TRAILING_BREAKS = re.compile(r"(?:\s|&nbsp;|<br\s*/?>)+$", re.IGNORECASE)
_LEADING_BREAKS = re.compile(r"^(?:\s|&nbsp;|<br\s*/?>)+", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


def escape_html(value):
	"""HTML-escape text. `html.escape` by hand, because Jinja's autoescape is off in
	frappe's print environment and these strings go out raw."""
	return (
		str(value)
		.replace("&", "&amp;")
		.replace("<", "&lt;")
		.replace(">", "&gt;")
		.replace('"', "&quot;")
		.replace("'", "&#x27;")
	)


def address_html(html):
	"""A rendered address with its leading and trailing line breaks trimmed, or ``""``.

	Every address this site renders comes from the `United States` Address Template,
	which ends each one with ``<br>`` — so an address followed by a ``<br>`` of our own
	printed a blank line under it. Measured 2026-09-24: 1,629 of 1,629 Sales Invoices
	and 673 of 673 Quotations carry a company address ending that way. The markup
	itself is the template's and is returned as it is.
	"""
	if not html:
		return ""
	text = _TRAILING_BREAKS.sub("", str(html))
	return _LEADING_BREAKS.sub("", text)


def plain_text(html):
	"""The visible text of a fragment: tags dropped, entities decoded, whitespace collapsed.

	Every entity, not only ``&amp;``: a plain description is escaped on its way to the
	page, so an item named ``NOZZLE, 2" JET`` came back as ``2&quot; JET`` and never
	matched its own name. Decoded after the tags are dropped, so an escaped ``&lt;b&gt;``
	reads as the text it prints rather than vanishing as a tag.
	"""
	if not html:
		return ""
	text = _unescape(_TAG.sub(" ", str(html)))
	return _WHITESPACE.sub(" ", text).strip()


# A North American number as people write it: optional +1 / 1, an area code with or
# without parentheses, then 3 + 4 digits, with spaces, dots or dashes between. Checked
# against every phone-shaped value on production (2026-09-24), `(208)-283-2638`,
# `954. 579-9476` and `1800 407 6657` included.
_NANP_SHAPE = re.compile(r"(\+?1[-. ]?)?\(?\d{3}\)?[-. ]*\d{3}[-. ]?\d{4}")


def format_phone(value):
	"""A phone number for print: North American ten digits as ``(801) 555-0100``.

	This site stores numbers as bare digits (``8015550100``) about as often as it stores
	them formatted, and a run of ten digits is hard to read back over the phone. A
	leading ``1`` or ``+1`` is dropped for the same shape. Only a number already grouped
	the North American way is reshaped; anything else — an international number (with or
	without its ``+``: a Singapore contact here is stored ``65-688-000-88``, ten digits), an
	extension, text — prints exactly as stored, because a reformat that guessed wrong
	would print a number nobody can dial.
	"""
	text = str(value or "").strip()
	if not text:
		return ""
	digits = re.sub(r"\D", "", text)
	# A `+` that is not `+1` is another country's code, whatever the digit count:
	# `+65 6123 4567` is ten digits, and would otherwise print as `(656) 123-4567`.
	if text.startswith("+") and not digits.startswith("1"):
		return text
	if _NANP_SHAPE.fullmatch(text):
		if len(digits) == 11 and digits.startswith("1"):
			digits = digits[1:]
		if len(digits) == 10:
			return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
	return text


def qty_text(value):
	"""A quantity as a person writes it: ``1``, ``2.5``, ``-12``, ``1,200``.

	`{{ row.qty }}` printed the raw float — ``1.0``, ``2.0`` — on every line of every
	format. Up to three decimals survive, trailing zeros do not.
	"""
	try:
		number = float(value or 0)
	except (TypeError, ValueError):
		return escape_html(value)
	if number != number or number in (float("inf"), float("-inf")):
		return escape_html(value)
	if number == int(number):
		return f"{int(number):,}"
	return f"{number:,.3f}".rstrip("0").rstrip(".")


def rich_text(value):
	"""A text field that may or may not hold markup, printed either way.

	`description` is a Text Editor field, so a line authored in the Item master holds
	markup and must print unescaped — escaping it put a literal ``&lt;div&gt;`` in front
	of a supplier. But most lines on this site were imported, and plain: 1,657 of 6,148
	Sales Invoice lines carry newlines and none carries a tag, so printed as HTML their
	lines ran together into one paragraph. Markup passes through; plain text is escaped
	and keeps its line breaks.
	"""
	if value is None:
		return ""
	text = str(value)
	if _TAG.search(text):
		return text
	# Decoded first: a plain line can still carry an entity from wherever it was typed
	# (`POOL &amp; FOUNTAIN CENTER MINI CONTROLLER`, item PDT-0014), and escaping it as it
	# stands printed a literal `&amp;` on the page.
	return escape_html(_unescape(text.strip())).replace("\r\n", "\n").replace("\n", "<br>")


def line_html(item_name, description):
	"""A customer-facing line: the item's name in bold, its description under it.

	The description is left out when it only repeats the name — ERPNext copies the
	name into it for any Item without a description of its own, and printing both
	reads as a stutter. Compared as visible text, case-insensitively, with the name's
	whitespace collapsed as the description's is — an imported name with a double space
	carries it into its copy, and only one side was being collapsed.
	"""
	name = str(item_name or "").strip()
	body = rich_text(description)
	head = f'<span style="{STRONG}">{escape_html(name)}</span>' if name else ""
	if not body or plain_text(body).lower() == _WHITESPACE.sub(" ", name).lower():
		return head or body
	if not head:
		return body
	return head + f'<div style="margin-top:1px">{body}</div>'


# ERPNext's default unit is "Nos" ("numbers"), and it is on every one of the 6,148
# Sales Invoice lines here. It means "each"; the page says so.
_UOM_WORDS = {"nos": "ea", "nos.": "ea"}


def uom_text(uom):
	"""A unit of measure for print — ``Nos`` reads as ``ea``, everything else as stored."""
	text = str(uom or "").strip()
	return escape_html(_UOM_WORDS.get(text.lower(), text))


def clean_label(label, company_abbr=None):
	"""A ledger account's name as a reader would say it.

	Tax rows print their description, which on this site is the account name QuickBooks
	gave the tax code plus ERPNext's company suffix: ``UT SPECIAL - SF``, and on 51
	invoices a retired code — ``Utah Sales Tax - Inactive - SF`` on 33, ``Utah - Weber -
	Ogden - Inactive - SF`` on 18. The suffix and the ``Inactive`` marker are bookkeeping,
	not something a customer owes.
	"""
	text = str(label or "").strip()
	if company_abbr:
		suffix = f" - {company_abbr}"
		if text.endswith(suffix):
			text = text[: -len(suffix)].rstrip()
	text = re.sub(r"\s+-\s+Inactive$", "", text, flags=re.IGNORECASE)
	return text


def party_block(name, address="", attention="", phone="", email=""):
	"""Who a document is for: name, address, attention line, phone and email.

	Each line prints only when it has a value, so the sparsest party prints its name and
	nothing else, with no dangling breaks. ``address`` is rendered Address Template
	markup and goes out as it is; everything else is text and is escaped. A phone or an
	email that the address already prints — the stock Address Template prints both — is
	not printed a second time.
	"""
	lines = []
	if name:
		lines.append(f'<span style="{STRONG}">{escape_html(name)}</span>')
	address = address_html(address)
	if address:
		lines.append(address)
	if attention:
		lines.append(f"Attn: {escape_html(attention)}")
	seen = plain_text(address).lower()
	seen_digits = re.sub(r"\D", "", seen)
	if phone:
		phone_digits = re.sub(r"\D", "", str(phone))
		# A North American number is the same number with or without its leading 1.
		if len(phone_digits) == 11 and phone_digits.startswith("1"):
			phone_digits = phone_digits[1:]
		if not (len(phone_digits) >= 7 and phone_digits in seen_digits):
			lines.append(escape_html(format_phone(phone)))
	if email and str(email).strip().lower() not in seen:
		lines.append(escape_html(str(email).strip()))
	return "<br>".join(lines)


def state_marker(docstatus, cancelled_text="CANCELLED"):
	"""A red word for a document that is not the real thing yet, or no longer is.

	Frappe prints a "Draft" heading only through its standard macros, which a custom
	format never calls — so a draft invoice left the building looking final. This site
	allows printing drafts (Print Settings), and has 305 draft Sales Invoices.
	"""
	try:
		status = int(docstatus or 0)
	except (TypeError, ValueError):
		status = 0
	if status == 0:
		return f'<div style="{FAIL};letter-spacing:1px">DRAFT</div>'
	if status == 2:
		return f'<div style="{FAIL};letter-spacing:1px">{escape_html(cancelled_text)}</div>'
	return ""


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


# The content helpers, for the Python-composed formats' templates (v1.535.0). Same
# prefix, same reason.


def ps_address(html):
	return address_html(html)


def ps_phone(value):
	return escape_html(format_phone(value))


def ps_qty(value):
	return qty_text(value)


def ps_rich(value):
	return rich_text(value)


def ps_line(item_name, description=None):
	return line_html(item_name, description)


def ps_uom(uom):
	return uom_text(uom)


def ps_state(docstatus, cancelled_text="CANCELLED"):
	return state_marker(docstatus, cancelled_text)
