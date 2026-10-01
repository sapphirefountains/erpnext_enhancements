# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""An article as the company register's controlled document (2026-09-30): its kind's template, for
the printed copy, the draft preview and the article's form.

Nik asked for articles to print, preview and show "as the templates in the google drive": POL-0002
(Policy), POL-0003 (Process) and POL-0004 (SOP), ``constants.KIND_REGISTER_TEMPLATES``. So this is
their look, measured from the templates' Google Docs export on 2026-09-30, and not the app's Pillar
Stripe print chrome (``print_style``, ``docs/print-design-system.md``), whose logo is the only thing
borrowed:

* Arial throughout, 11pt text.
* Top left, "Sapphire Fountains" (14pt bold, ``#004a7c``) over "Masters of Fountaineering(TM)"
  (10pt, ``#0072bc``). Top right, "Document ID: SOP-06-0001" (11pt bold, ``#004a7c``) over the
  version and the date (9pt, ``#666666``): "Last Updated", or "Effective Date" on a Policy.
* The title, 24pt bold ``#004a7c``, and a rule under it.
* One grey row (``#f2f2f2``, ``#cccccc`` borders): "SOP Owner:" and the owner's role, "Group:" and
  the department. The template's four cells are equal; here the labels take only their width, so a
  long job title does not wrap into four lines.
* Numbered section headings, 18pt bold black. Tables with a ``#004a7c`` header row in white.
* The Revision History: a small table with a grey header row.
* A rule, the logo, and "Confidential - Sapphire Fountains Internal Use Only" in 8pt grey (each
  template words its own).

Standard library only, like ``constants`` and ``content``: ``printing.py`` loads the saved record,
checks permission and looks names up, and :func:`render` draws what it is given, so the layout is
tested bench-free.

What is not obvious:

* **The headings are numbered by the stylesheet, never by rewriting the body.** :func:`outline`
  reads the body (``HTMLParser``, nothing written) for the highest level of heading it uses (``##`` in
  the drafting tool's Markdown, Heading 2 in the editor) and how many there are, and :func:`render`
  puts the body inside ``<div class="kb-body kb-top-h2 kb-count">`` **byte for byte as stored**; CSS
  counters number that level, "1. Purpose", and style lower levels as sub-headings. The Revision
  History takes the next number, which :func:`outline` counted. If the author numbered every top-level
  heading themselves ("1. Purpose"), ``kb-count`` is left off so nothing is numbered twice. The first
  version of this rewrote each heading's start tag with a regular expression, and the security review
  showed why that must never come back: a pattern that assumes no ``>`` inside an attribute value
  turns an inert ``<h2 title="a><img onerror=...>">`` into live markup, in the Desk and in an
  approver's preview, where a script could approve the draft as them. The body is only ever passed
  through.
* **The Revision History is drawn, never typed**: from the article's ``revisions`` rows, which
  publishing writes (``publish.publish``), plus a draft's own line in its preview, marked as not yet
  approved. A template's section list stops before it for that reason (``constants.KIND_SECTIONS``).
* **Print-safe CSS, scoped.** Tables and blocks only, no flex or grid, which the PDF engines here
  do not lay out. Every rule is under ``.kb-doc``, so the same markup sits inside the Desk form
  without restyling it. Cell padding and borders are ``!important`` on selectors more specific than
  frappe's ``.print-format td`` (``standard.css``, and the Redesign print style's ``padding: 10px
  !important``), which would otherwise win: an ``!important`` rule loses only to a more specific
  ``!important`` rule, or an inline one (docs/print-design-system.md, "Cell geometry").
* **Everything a person typed is escaped, except the body**: the stored HTML the Version controller
  cleaned on save (``content.strip_presentation``, then v16's own sanitizer), which the Desk already
  shows as it is, and which goes onto the page unchanged.
* **The editor's own wrapper is neutralised.** A stored body keeps Quill's ``<div class="ql-editor
  read-mode">``, and frappe's print bundle and Desk theme style ``.ql-editor`` (its font, its colour,
  a light grey in the dark theme, and its padding), so ``.kb-doc .kb-body .ql-editor`` sets them back
  to the page's.
"""

import datetime
import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from erpnext_enhancements import print_style
from erpnext_enhancements.knowledge_base import constants

#: The templates' sapphire, measured from their export: the name, the Document ID, the title and a
#: table's header row.
SAPPHIRE = "#004a7c"
#: The "Masters of Fountaineering" line.
BRIGHT = "#0072bc"
#: The version, the date and the Confidential line.
GREY = "#666666"
#: A notice that the copy is not the approved text: a draft, a retired article.
WARN = "#b3261e"

#: What each kind's template calls its owner, its date and its closing line. A draft with no kind
#: yet prints the neutral words (:data:`UNCLASSIFIED`).
KIND_LABELS = {
	"Policy": {
		"owner": "Policy Owner",
		"date": "Effective Date",
		"footer": "Confidential - Sapphire Fountains Policy Document - For Internal Use Only",
	},
	"Process": {
		"owner": "Process Owner",
		"date": "Last Updated",
		"footer": "Confidential - Sapphire Fountains Process Definition - For Internal Use Only",
	},
	"SOP": {
		"owner": "SOP Owner",
		"date": "Last Updated",
		"footer": "Confidential - Sapphire Fountains Internal Use Only",
	},
}
UNCLASSIFIED = {
	"owner": "Owner",
	"date": "Last Updated",
	"footer": "Confidential - Sapphire Fountains Internal Use Only",
}

#: The Revision History's heading and columns. The template's four, with Approved By added: every
#: version here is approved by a second person, and the printed copy should say who.
REVISION_HEADING = "Revision History"
REVISION_COLUMNS = ("Version", "Date", "Author", "Approved By", "Description of Change")

#: How dates print: the templates' own [MM/DD/YYYY].
DATE_FORMAT = "%m/%d/%Y"


@dataclass
class Revision:
	"""One line of the Revision History. ``pending`` marks a draft's own line in its preview."""

	version: int | None
	date: datetime.date | None = None
	author: str = ""
	approved_by: str = ""
	note: str = ""
	pending: bool = False


@dataclass
class Notice:
	"""A box above the document saying it is not the approved text. ``tone`` is ``"warn"`` (a draft,
	a retired article: red) or ``"history"`` (superseded or discarded: grey)."""

	tone: str
	heading: str
	text: str = ""


@dataclass
class Sheet:
	"""What :func:`render` draws. Plain values: ``printing.py`` fills it from a saved record."""

	title: str
	kind: str | None = None
	#: The article number, ``SOP-06-0001``; ``None`` before a first version is approved.
	number: str | None = None
	#: ``SOP-06-``, for a first version that has a kind and a department but no number yet.
	scope: str | None = None
	version: int | None = None
	#: When this version was approved; ``None`` for a draft.
	date: datetime.date | None = None
	owner: str = ""
	owner_role: str = ""
	#: The department's name, ``Operations``.
	group: str = ""
	#: The stored, cleaned body HTML.
	body: str = ""
	revisions: list = field(default_factory=list)
	notice: Notice | None = None
	#: A draft's preview prints a faint DRAFT across each page, and only on paper.
	watermark: bool = False
	#: One small line under the footer, e.g. "Printed 09/30/2026. ..."; ``""`` for none.
	footnote: str = ""
	#: The on-screen form's copy: framed as a page, and no watermark.
	screen: bool = False


def render(sheet):
	"""The document as HTML: one ``<style>`` and one ``<div class="kb-doc">``."""
	labels = KIND_LABELS.get(sheet.kind, UNCLASSIFIED)
	body = sheet.body or ""
	top, sections, numbered_by_author = outline(body)
	parts = [STYLE, f'<div class="kb-doc{" kb-screen" if sheet.screen else ""}">']
	if sheet.watermark and not sheet.screen:
		parts.append('<div class="kb-watermark">DRAFT</div>')
	if sheet.notice:
		parts.append(_notice(sheet.notice))
	parts.append(_header(sheet, labels))
	parts.append(f'<h1 class="kb-title">{_e(sheet.title or "Untitled")}</h1>')
	parts.append('<hr class="kb-rule">')
	parts.append(_owner_row(sheet, labels))
	if body.strip():
		classes = ["kb-body"]
		if top:
			classes.append(f"kb-top-h{top}")
			if not numbered_by_author:
				classes.append("kb-count")
		# The body exactly as stored: never parsed and written back (see the module docstring).
		parts.append(f'<div class="{" ".join(classes)}">{body}</div>')
	parts.append(_revision_history(sheet.revisions, sections + 1))
	parts.append(_footer(labels, sheet.footnote))
	parts.append("</div>")
	return "".join(parts)


def document_id(sheet):
	"""``SOP-06-0001``; ``SOP-06-####`` for a first version not yet numbered (its number is given at
	approval, and guessing it would be wrong the moment another article was approved first); ``Not
	numbered yet`` for a draft with no kind or department."""
	if sheet.number:
		return sheet.number
	if sheet.scope:
		return f"{sheet.scope}####"
	return "Not numbered yet"


def skeleton(kind):
	"""A new draft's body for ``kind``: each of its template's sections as a heading, the guidance in
	square brackets under it, in italics (``constants.KIND_SECTIONS``). ``""`` for anything that is not
	a kind. ``content.guidance_left`` refuses to submit a draft that still has any of that guidance."""
	sections = constants.KIND_SECTIONS.get(kind)
	if not sections:
		return ""
	return "".join(f"<h2>{_e(name)}</h2><p><em>[{_e(guidance)}]</em></p>" for name, guidance in sections)


# ------------------------------------------------------------------ the headings

#: A heading its author already numbered: "1. Purpose", "2) Scope".
_OWN_NUMBER = re.compile(r"^\d+[.)]\s")
_HEADING_TAGS = {f"h{level}": level for level in range(1, 7)}


def outline(body):
	"""``(top, count, numbered_by_author)`` for a body, read and never rewritten: the highest heading
	level it uses (``2`` for ``<h2>``; ``None`` for no heading), how many headings it has at that level,
	and whether its author numbered every one of them. A heading counts wherever it sits, as the
	stylesheet's counter does."""
	if not isinstance(body, str) or "<" not in body:
		return None, 0, False
	reader = _Headings()
	reader.feed(body)
	reader.close()
	if not reader.headings:
		return None, 0, False
	top = min(level for level, _text in reader.headings)
	texts = [text for level, text in reader.headings if level == top]
	return top, len(texts), all(_OWN_NUMBER.match(text) for text in texts)


class _Headings(HTMLParser):
	"""Each heading's level and its text, entities decoded. Reads only."""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.headings = []
		self._open = None

	def handle_starttag(self, tag, attrs):
		level = _HEADING_TAGS.get(tag)
		if level and self._open is None:
			self._open = (level, [])

	def handle_endtag(self, tag):
		if self._open is not None and _HEADING_TAGS.get(tag) == self._open[0]:
			self.headings.append((self._open[0], " ".join("".join(self._open[1]).split())))
			self._open = None

	def handle_data(self, data):
		if self._open is not None:
			self._open[1].append(data)


# ------------------------------------------------------------------ the parts


def _notice(notice):
	text = f'<div class="kb-notice-text">{_e(notice.text)}</div>' if notice.text else ""
	return (
		f'<div class="kb-notice kb-notice-{_e(notice.tone)}">'
		f'<div class="kb-notice-heading">{_e(notice.heading)}</div>{text}</div>'
	)


def _header(sheet, labels):
	date = _date(sheet.date) or "Not yet approved"
	version = str(sheet.version) if sheet.version else "Not yet approved"
	return (
		'<table class="kb-head"><tr>'
		'<td class="kb-head-left">'
		'<div class="kb-brand">Sapphire Fountains</div>'
		'<div class="kb-tagline">Masters of Fountaineering&trade;</div>'
		"</td>"
		'<td class="kb-head-right">'
		f'<div class="kb-docid">Document ID: {_e(document_id(sheet))}</div>'
		f'<div class="kb-small">Version: {_e(version)}</div>'
		f'<div class="kb-small">{_e(labels["date"])}: {_e(date)}</div>'
		"</td>"
		"</tr></table>"
	)


def _owner_row(sheet, labels):
	return (
		'<table class="kb-owner"><tr>'
		f'<td class="kb-label">{_e(labels["owner"])}:</td>'
		f"<td>{_e(owner_text(sheet.owner, sheet.owner_role))}</td>"
		'<td class="kb-label">Group:</td>'
		f"<td>{_e(sheet.group or 'Not set')}</td>"
		"</tr></table>"
	)


def owner_text(name, role):
	"""The owner cell: the template asks for a role, and a person changes jobs, so the role leads and
	the name follows, ``Purchasing Agent/Inventory Clerk (Parker Bailey)``."""
	name, role = (name or "").strip(), (role or "").strip()
	if name and role:
		return f"{role} ({name})"
	return role or name or "Not set"


def _revision_history(revisions, number):
	head = "".join(f"<th>{_e(column)}</th>" for column in REVISION_COLUMNS)
	rows = []
	for line in sorted(revisions, key=lambda r: (r.version or 0, r.pending)):
		version = f"{line.version or ''}{' (draft)' if line.pending else ''}".strip()
		date = "Not yet approved" if line.pending else _date(line.date)
		approved = "" if line.pending else line.approved_by
		cells = (version, date, line.author, approved, line.note)
		css = ' class="kb-pending"' if line.pending else ""
		rows.append(f"<tr{css}>" + "".join(f"<td>{_e(cell)}</td>" for cell in cells) + "</tr>")
	if not rows:
		rows.append(f'<tr><td colspan="{len(REVISION_COLUMNS)}">None yet.</td></tr>')
	return (
		f'<h2 class="kb-section kb-revisions-heading"><span class="kb-number">{number}.</span> '
		f"{_e(REVISION_HEADING)}</h2>"
		f'<table class="kb-revisions"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'
	)


def _footer(labels, footnote):
	note = f'<div class="kb-footnote">{_e(footnote)}</div>' if footnote else ""
	return (
		'<div class="kb-foot"><hr class="kb-rule">'
		f'<div class="kb-logo">{print_style.logo_svg(150)}</div>'
		'<div class="kb-logo-tagline">Masters of Fountaineering&trade;</div>'
		f'<div class="kb-confidential">{_e(labels["footer"])}</div>'
		f"{note}</div>"
	)


def _date(value):
	if isinstance(value, datetime.datetime):
		value = value.date()
	if isinstance(value, datetime.date):
		return value.strftime(DATE_FORMAT)
	return ""


def _e(value):
	return html.escape("" if value is None else str(value), quote=True)


# ------------------------------------------------------------------ the stylesheet

#: The heading rules for each possible top level, so the level the body uses is numbered and styled
#: as sections and every level under it as sub-headings (:func:`outline` says which, :func:`render`
#: puts it on the body's class).
_SECTION_SELECTORS = ", ".join(f".kb-doc .kb-top-h{n} h{n}" for n in range(1, 7))
_SUB_SELECTORS = ", ".join(f".kb-doc .kb-top-h{n} h{m}" for n in range(1, 7) for m in range(n + 1, 7))
_COUNTED_SELECTORS = ", ".join(f".kb-doc .kb-count.kb-top-h{n} h{n}::before" for n in range(1, 7))

#: Every rule is under ``.kb-doc``; see the module docstring for the specificity the cell rules need.
STYLE = f"""<style>
.kb-doc {{ font-family: Arial, "Liberation Sans", Helvetica, sans-serif; font-size: 11pt;
	line-height: 1.25; color: #000; background: #fff; text-align: left; position: relative; }}
.kb-doc.kb-screen {{ max-width: 8.5in; margin: 0 auto; padding: 0.75in; border: 1px solid #d1d8dd;
	box-sizing: border-box; }}
@media screen and (max-width: 640px) {{
	.kb-doc.kb-screen {{ padding: 16px; }}
	.kb-doc h1.kb-title {{ font-size: 20pt; }}
}}
.kb-doc table {{ border-collapse: collapse; border-spacing: 0; }}
.kb-doc tr {{ page-break-inside: avoid; }}
.kb-doc .kb-notice {{ border: 1.5pt solid {WARN}; color: {WARN}; padding: 6pt 9pt; margin: 0 0 14pt;
	font-size: 10pt; }}
.kb-doc .kb-notice-history {{ border-color: {GREY}; color: {GREY}; }}
.kb-doc .kb-notice-heading {{ font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }}
.kb-doc table.kb-head {{ width: 100%; margin: 0; }}
.kb-doc table.kb-head td {{ width: 50%; padding: 0 !important; border: 0 !important;
	vertical-align: top !important; }}
.kb-doc table.kb-head td.kb-head-right {{ text-align: right; }}
.kb-doc .kb-brand {{ color: {SAPPHIRE}; font-size: 14pt; font-weight: 700; line-height: 1.15; }}
.kb-doc .kb-tagline {{ color: {BRIGHT}; font-size: 10pt; line-height: 1.2; }}
.kb-doc .kb-docid {{ color: {SAPPHIRE}; font-size: 11pt; font-weight: 700; line-height: 1.2; }}
.kb-doc .kb-small {{ color: {GREY}; font-size: 9pt; line-height: 1.3; }}
.kb-doc h1.kb-title {{ color: {SAPPHIRE}; font-size: 24pt; font-weight: 700; line-height: 1.1;
	margin: 22pt 0 14pt; padding: 0; border: 0; }}
.kb-doc hr.kb-rule {{ border: 0; border-top: 1px solid #9e9e9e; height: 0; margin: 0 0 10pt; }}
.kb-doc table.kb-owner {{ width: 100%; margin: 0 0 6pt; }}
.kb-doc table.kb-owner td {{ background: #f2f2f2; border: 1pt solid #cccccc !important;
	padding: 7.5pt !important; vertical-align: top !important; }}
.kb-doc table.kb-owner td.kb-label {{ width: 1%; white-space: nowrap; font-weight: 700; }}
.kb-doc .kb-section, {_SECTION_SELECTORS} {{ color: #000; font-size: 18pt; font-weight: 700;
	line-height: 1.15; margin: 16pt 0 8pt; padding: 0; border: 0; page-break-after: avoid; }}
{_SUB_SELECTORS} {{ color: #000; font-size: 13pt; font-weight: 700; line-height: 1.2;
	margin: 12pt 0 6pt; padding: 0; border: 0; page-break-after: avoid; }}
.kb-doc .kb-body {{ counter-reset: kb-section; }}
{_COUNTED_SELECTORS} {{ counter-increment: kb-section; content: counter(kb-section) ". "; }}
.kb-doc .kb-body .ql-editor {{ font-family: inherit; font-size: inherit; color: inherit;
	line-height: inherit; padding: 0; }}
.kb-doc .kb-body p {{ margin: 0 0 8pt; }}
.kb-doc .kb-body ul, .kb-doc .kb-body ol {{ margin: 0 0 8pt; padding-left: 24pt; }}
.kb-doc .kb-body li {{ line-height: 1.5; }}
.kb-doc .kb-body blockquote {{ border-left: 3pt solid #cccccc; margin: 0 0 8pt; padding: 0 0 0 10pt;
	color: #333333; }}
.kb-doc .kb-body pre, .kb-doc .kb-body code {{ font-family: "Courier New", monospace; font-size: 10pt; }}
.kb-doc .kb-body img {{ max-width: 100%; height: auto; }}
.kb-doc .kb-body table {{ width: 100%; margin: 4pt 0 10pt; }}
.kb-doc .kb-body table td, .kb-doc .kb-body table th {{ border: 1pt solid #333333 !important;
	padding: 7.5pt !important; vertical-align: top !important; text-align: left; color: #000;
	background: #fff; }}
.kb-doc .kb-body table th, .kb-doc .kb-body table thead td {{ background: {SAPPHIRE}; color: #fff;
	font-weight: 400; }}
.kb-doc table.kb-revisions {{ width: 100%; margin: 0 0 8pt; font-size: 9pt; }}
.kb-doc table.kb-revisions th, .kb-doc table.kb-revisions td {{ border: 1pt solid #cccccc !important;
	padding: 3.8pt !important; vertical-align: top !important; text-align: left; color: #000;
	font-weight: 400; }}
.kb-doc table.kb-revisions th {{ background: #f2f2f2; }}
.kb-doc table.kb-revisions tr.kb-pending td {{ font-style: italic; color: {GREY}; }}
.kb-doc .kb-foot {{ margin-top: 30pt; text-align: center; page-break-inside: avoid; }}
.kb-doc .kb-logo {{ width: 150px; margin: 24pt auto 0; }}
.kb-doc .kb-logo-tagline {{ color: {BRIGHT}; font-size: 8pt; margin: 3pt 0 10pt; }}
.kb-doc .kb-confidential {{ color: {GREY}; font-size: 8pt; }}
.kb-doc .kb-footnote {{ color: {GREY}; font-size: 7.5pt; margin-top: 4pt; }}
.kb-doc .kb-watermark {{ display: none; }}
@media print {{
	.kb-doc .kb-watermark {{ display: block; position: fixed; top: 38%; left: 0; right: 0;
		text-align: center; font-size: 110pt; font-weight: 700; color: rgba(179, 38, 30, 0.08);
		transform: rotate(-30deg); z-index: 0; pointer-events: none; }}
}}
</style>"""
