# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Content hygiene for the Knowledge Base: presentation stripped, secrets refused, a stable hash.

WI-080 PR 2, ADR 0017 section 1 ("On save and publish"). Standard library only, so every rule runs
in the bench-free CI tier. Three jobs:

* :func:`strip_presentation` removes the markup that can make text invisible to the person reading
  the page while leaving it in front of any AI that reads the HTML: colour, background, size and
  font, whether they arrive as ``style``, as Quill's ``ql-color-*``/``ql-bg-*``/``ql-size-*``/
  ``ql-font-*`` classes or as the attributes HTML had before CSS (``<font color size face>``,
  ``bgcolor``), and the ``hidden`` attribute. White-on-white or zero-size text is exactly what a
  reviewer would never see and a model would obey. Tables, lists, alignment and indent survive.
* :func:`secret_findings` finds secret-shaped strings (vendor keys, private keys, tokens, a
  password written out) and reports **where and what kind, never the value**, because the refusal
  message is shown on screen and may be logged.
* :func:`content_hash` answers "did the text people and AI read change?", for the Drive copy
  (slice 4) to compare.

**When they run.** The Version controller strips and scans on every save that changes content
(``validate``), and scans again at approval. ``body_md`` and ``content_hash`` are **not** computed
here or in ``validate``: PR 3 computes them at publish, from the stored body, so they always
describe what was approved (``body_md`` with v16's own ``frappe.utils.to_markdown``,
``utils/data.py:2468``).

**Why the scan removes ``data:`` URIs first.** v16 turns a pasted image's ``data:`` URI into a
private File only *after* ``validate`` (``Document._validate`` -> ``_extract_images_from_text_editor``,
frappe ``origin/version-16`` ``model/document.py:594`` then ``:835``), so in ``validate`` the body
still carries every pasted screenshot as megabytes of base64, in which a vendor-key pattern turns
up by chance. The scan never looks at them.
"""

import base64
import hashlib
import html
import json
import re
import unicodedata
from collections import namedtuple
from html.parser import HTMLParser

# ------------------------------------------------------------------ presentation

#: Quill's class attributors for colour, background, size and font. Frappe v16's Text Editor
#: registers the *style* attributors for these instead (``text_editor.js``), so they arrive as
#: ``style``; the classes come from pasted Quill HTML or from a REST write, and mean the same thing.
PRESENTATION_CLASS_PREFIXES = ("ql-color-", "ql-bg-", "ql-size-", "ql-font-")

#: The only ``style`` declarations kept. v16's Text Editor registers Quill's **style** attributor for
#: alignment (``attributors/style/align``), so a centred paragraph is ``style="text-align:
#: center;"``; dropping every ``style`` would lose it. Alignment cannot hide text. Everything else
#: (``color``, ``font-size``, ``display``, ``opacity``, ``position`` and the rest) goes, and so does
#: any declaration this does not recognise exactly: this is an allowlist on purpose.
KEPT_STYLE = {"text-align": frozenset({"left", "right", "center", "justify", "start", "end"})}

#: Presentation attributes, dropped from every tag. v16's ``sanitize_html`` keeps the ``font``
#: element and every one of these (frappe ``origin/version-16`` ``utils/html_utils.py``: ``font``
#: at :267, ``bgcolor`` :413, ``color`` :432, ``face`` :453, ``hidden`` :462, ``size`` :516), and
#: a REST write stores the body as sent, so ``<font color="#ffffff">`` is white-on-white text and
#: ``<p hidden>`` is not displayed at all. The Desk editor would turn a ``<font>`` into a coloured
#: ``<span>`` (its ``CustomColor`` blot, ``text_editor.js``), but only if someone opens the draft.
#: A ``<font>`` left with no attributes renders as plain text, so the element itself stays:
#: renaming it would mean rewriting its end tag too, and nothing else here touches an end tag.
PRESENTATION_ATTRIBUTES = frozenset({"bgcolor", "color", "face", "hidden", "size"})

#: A quick test before the parser runs: no tag can need stripping without one of these in it.
_MAY_NEED_STRIPPING = re.compile(r"style|ql-|color|face|hidden|size", re.IGNORECASE)
#: HTMLParser counts lines on "\n" alone, so positions are mapped back the same way.
_NEWLINE = re.compile("\n")


def strip_presentation(markup):
	"""``markup`` with presentation removed. Anything not a string is returned as it came.

	Only the start tags that carry a dropped ``style`` declaration, a presentation class or a
	presentation attribute are rewritten; every other byte (text, entities, comments, other tags,
	end tags, ``data:`` images) is returned exactly as it was, so the function is idempotent and a
	body with nothing to strip comes back identical. ``indent`` (``ql-indent-N``), ``direction``, table and list markup are
	untouched. Tags are found with the standard library's HTML parser rather than a regex, so an
	attribute value containing ``>`` or an unquoted value containing ``=`` is read the way a
	browser reads it.
	"""
	if not isinstance(markup, str) or not _MAY_NEED_STRIPPING.search(markup):
		return markup
	finder = _PresentationFinder()
	finder.feed(markup)
	finder.close()
	if not finder.found:
		return markup

	line_starts = [0]
	line_starts.extend(match.end() for match in _NEWLINE.finditer(markup))
	out, cursor = [], 0
	for line, offset, raw, tag, attrs, closed in finder.found:
		index = line_starts[line - 1] + offset
		if not markup.startswith(raw, index):
			index = markup.find(raw, cursor)
		if index < cursor:
			continue
		out.append(markup[cursor:index])
		out.append(_start_tag(tag, attrs, closed))
		cursor = index + len(raw)
	out.append(markup[cursor:])
	return "".join(out)


class _PresentationFinder(HTMLParser):
	"""Records each start tag whose attributes :func:`_clean_attrs` would change, with its position."""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.found = []

	def handle_starttag(self, tag, attrs):
		self._note(tag, attrs, closed=False)

	def handle_startendtag(self, tag, attrs):
		self._note(tag, attrs, closed=True)

	def _note(self, tag, attrs, closed):
		cleaned = _clean_attrs(attrs)
		if cleaned != attrs:
			line, offset = self.getpos()
			self.found.append((line, offset, self.get_starttag_text(), tag, cleaned, closed))


def _clean_attrs(attrs):
	cleaned = []
	for name, value in attrs:
		if name in PRESENTATION_ATTRIBUTES:
			continue
		if name == "style":
			kept = _kept_style(value)
			if kept is not None:
				cleaned.append((name, kept))
		elif name == "class":
			tokens = (value or "").split()
			kept = [t for t in tokens if not t.lower().startswith(PRESENTATION_CLASS_PREFIXES)]
			if len(kept) == len(tokens):
				cleaned.append((name, value))
			elif kept:
				cleaned.append((name, " ".join(kept)))
		else:
			cleaned.append((name, value))
	return cleaned


def _kept_style(value):
	"""The allowed part of a ``style`` value: verbatim if all of it is allowed, ``None`` if none is."""
	kept, dropped = [], False
	for declaration in (value or "").split(";"):
		if not declaration.strip():
			continue
		prop, colon, setting = declaration.partition(":")
		prop, setting = prop.strip().lower(), setting.strip().lower()
		if colon and setting in KEPT_STYLE.get(prop, ()):
			kept.append(f"{prop}: {setting}")
		else:
			dropped = True
	if not dropped:
		return value if kept else None
	return "; ".join(kept) + ";" if kept else None


def _start_tag(tag, attrs, closed):
	parts = [f"<{tag}"]
	for name, value in attrs:
		parts.append(f" {name}" if value is None else f' {name}="{html.escape(value, quote=True)}"')
	parts.append(" />" if closed else ">")
	return "".join(parts)


# ------------------------------------------------------------------ secrets

#: Where a secret-shaped string was found. ``line`` counts from 1: in a plain-text field it is the
#: line of the field; in the body it is the line of text as it reads on the page (each paragraph,
#: heading, list item or table row is one; blank ones are not counted). ``kind`` is a phrase such
#: as "a Stripe secret key". **There is deliberately no field for the value.**
Finding = namedtuple("Finding", ("line", "kind"))

#: The content fields that are scanned, and the one among them that is HTML.
SCANNED_FIELDS = ("title", "summary", "keywords", "change_note", "body")
HTML_FIELDS = frozenset({"body"})

#: Each field's label on the Version form, for the refusal message.
FIELD_LABELS = {
	"title": "Title",
	"summary": "Summary",
	"keywords": "Keywords",
	"change_note": "Change Note",
	"body": "Body",
}

#: A ``data:`` URI: the media type, then everything up to a quote, space, ``<``, ``>`` or ``)``.
#: Base64 contains none of them, so a pasted screenshot goes in one linear match.
_DATA_URI = re.compile(r"data:[^,\s\"'<>]*,[^\s\"'<>)]*", re.IGNORECASE)

#: A value that names where a secret is kept rather than being one.
_POINTS_AT_A_VAULT = ("1password", "bitwarden", "lastpass", "keepass", "dashlane", "vault", "manager")

#: Punctuation around a value that belongs to the sentence, not the value: "If the password is
#: forgotten, ...", "Password: (optional)". The quotes are already outside the pattern's group.
_SENTENCE_LEADING = "([{"
_SENTENCE_TRAILING = ".,;:!?)]}…"
#: Characters that join words in prose ("case-sensitive", "self-service", "first.last", "and/or").
#: On their own they do not make a word a password; a digit or any other symbol does.
_WORD_JOINERS = frozenset("-./")


def _written_value(value):
	"""The value after "password:" or "API key:" without the sentence's punctuation around it."""
	return value.lstrip(_SENTENCE_LEADING).rstrip(_SENTENCE_TRAILING)


def _written_password(value):
	"""A value after "password:" or "password is" that is a password rather than a word of the sentence.

	Eight characters or more once the sentence's own punctuation is off, a letter, and a digit or a
	symbol that is not just joining two words. So ``Fountain#2026`` and ``Welcome1`` are passwords,
	and "is forgotten,", "is case-sensitive.", "Password: (unchanged)" and "is: first.last" are
	not. A password of letters and hyphens alone is missed, as a password of letters alone always
	was: the scan is for secrets pasted by mistake, and refusing ordinary sentences, which nothing
	lets an author past, costs more than that miss.
	"""
	token = _written_value(value)
	if len(token) < 8:
		return False
	lowered = token.casefold()
	if "://" in token or any(word in lowered for word in _POINTS_AT_A_VAULT):
		return False
	if not any(c.isalpha() for c in token):
		return False
	return any(c.isdigit() or not (c.isalnum() or c in _WORD_JOINERS) for c in token)


def _written_key(value):
	"""A value after "API key:" or "auth token:" long and mixed enough to be the key itself."""
	token = _written_value(value)
	return len(token) >= 16 and _written_password(token) and any(c.isdigit() for c in token)


def _basic_credential(value):
	"""A token after "Basic" that is what HTTP Basic sends: ``user:password`` in base64.

	To a pattern, "Basic Maintenance/Cleaning" has the same shape, because letters and ``/`` are
	base64 characters. So the token has to decode, to printable text with a ``:`` between a user
	and a password; a phrase of words decodes to bytes that are not text.
	"""
	try:
		decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("utf-8")
	except ValueError:  # binascii.Error and UnicodeDecodeError are both ValueErrors
		return False
	user, colon, password = decoded.partition(":")
	return bool(colon and user and password and decoded.isprintable())


#: ``(kind, pattern, check)``. ``check``, when present, is applied to the pattern's first group.
#: Vendor prefixes are matched the way each vendor documents them. None of these fire on a Drive
#: file id, a Google Doc link or an ERPNext document name.
_SECRET_PATTERNS = tuple(
	(kind, re.compile(pattern, flags), check)
	for kind, pattern, flags, check in (
		("a private key", r"-----BEGIN[A-Z ]*PRIVATE KEY(?: BLOCK)?-----", 0, None),
		("a Google service-account key", r"\"private_key_id\"\s*:\s*\"[0-9a-f]{40}\"", 0, None),
		("a Stripe secret key", r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{10,}", 0, None),
		("a Stripe webhook secret", r"\bwhsec_[0-9A-Za-z]{20,}", 0, None),
		("an AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", 0, None),
		("a Google API key", r"\bAIza[0-9A-Za-z_-]{35}", 0, None),
		("a Google OAuth client secret", r"\bGOCSPX-[0-9A-Za-z_-]{20,}", 0, None),
		("a Google OAuth token", r"\bya29\.[0-9A-Za-z_-]{20,}|(?<![0-9A-Za-z/])1//0[0-9A-Za-z_-]{20,}", 0, None),
		("a GitHub token", r"\bgh[pousr]_[0-9A-Za-z]{30,}|\bgithub_pat_[0-9A-Za-z_]{22,}", 0, None),
		("a Slack token", r"\bxox[abposr]-[0-9A-Za-z-]{10,}", 0, None),
		("a SendGrid API key", r"\bSG\.[0-9A-Za-z_-]{16,}\.[0-9A-Za-z_-]{16,}", 0, None),
		("an Anthropic API key", r"\bsk-ant-[0-9A-Za-z_-]{20,}", 0, None),
		("an OpenAI API key", r"\bsk-(?!ant-)(?:proj-|svcacct-|admin-)?[0-9A-Za-z_-]{20,}", 0, None),
		(
			"a Plaid access token",
			r"\baccess-(?:sandbox|development|production)-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
			0,
			None,
		),
		("a JSON Web Token", r"\beyJ[0-9A-Za-z_-]{8,}\.eyJ[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}", 0, None),
		("a Frappe API key and secret", r"\btoken\s+[0-9a-f]{15}:[0-9a-f]{15}\b", re.IGNORECASE, None),
		("a bearer token", r"\bbearer\s+[0-9A-Za-z._~+/-]{20,}", re.IGNORECASE, None),
		(
			"an HTTP Basic credential",
			r"\bbasic\s+([0-9A-Za-z+/]{16,}={0,2})(?![0-9A-Za-z+/=])",
			re.IGNORECASE,
			_basic_credential,
		),
		("a password in a web address", r"\b[a-z][a-z0-9+.-]*://[^\s/:@<>\"']+:[^\s/@<>\"']+@", re.IGNORECASE, None),
		(
			"a written-out password",
			r"\b(?:password|passwd|passcode|passphrase)\s*(?:[:=]|\bis\b:?)\s*[\"'“‘]?([^\s\"'“”‘’<>]{8,})",
			re.IGNORECASE,
			_written_password,
		),
		(
			"a written-out key or token",
			r"\b(?:api[ _-]?(?:key|secret)|secret[ _-]?key|client[ _-]?secret|(?:auth|access|refresh)[ _-]?token)"
			r"\s*(?:[:=]|\bis\b:?)\s*[\"'“‘]?([^\s\"'“”‘’<>]{16,})",
			re.IGNORECASE,
			_written_key,
		),
	)
)


def without_data_uris(text):
	"""``text`` with every ``data:`` URI removed (the image tags stay, with an empty ``src``)."""
	return _DATA_URI.sub("", text) if isinstance(text, str) else ""


def secret_findings(text, *, html=False):
	"""Secret-shaped strings in ``text``, as ``[Finding(line, kind)]``, never the value.

	``data:`` URIs are removed first (see the module docstring). With ``html=True`` the text is read
	as the page shows it, one line per paragraph, heading, list item or table row, and every
	attribute value (a link's address, an image's alt text, a hidden ``data-*``) is read as part of
	its line, because a secret in an ``href`` is as leaked as one on screen. Each ``(line, kind)`` is
	reported once, in line order.
	"""
	source = without_data_uris(text)
	if not source:
		return []
	if html:
		lines = _page_lines(source)
	else:
		lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
	findings = []
	for number, line in enumerate(lines, 1):
		for kind, pattern, check in _SECRET_PATTERNS:
			for match in pattern.finditer(line):
				if check is None or check(match.group(1)):
					findings.append(Finding(number, kind))
					break
	return list(dict.fromkeys(findings))


def document_secret_findings(doc, fields=SCANNED_FIELDS):
	"""``[(fieldname, Finding)]`` across the scanned fields of a version (anything with ``.get``)."""
	found = []
	for fieldname in fields:
		value = doc.get(fieldname)
		if isinstance(value, str) and value:
			found.extend((fieldname, f) for f in secret_findings(value, html=fieldname in HTML_FIELDS))
	return found


def secret_refusal(found):
	"""The message for :func:`document_secret_findings`: where and what kind, never the value."""
	places = [f"{FIELD_LABELS.get(field, field)} line {f.line} looks like {f.kind}" for field, f in found]
	return (
		"This cannot be saved: "
		+ "; ".join(places)
		+ ". Take it out. Every staff member and every AI tool Sapphire uses reads the knowledge "
		"base, so a secret belongs in the password manager; an article can say where it is kept "
		"and who to ask."
	)


#: The elements that start a new line of text on the page.
_BLOCK_TAGS = frozenset(
	{
		"address",
		"article",
		"aside",
		"blockquote",
		"br",
		"caption",
		"dd",
		"details",
		"div",
		"dl",
		"dt",
		"figcaption",
		"figure",
		"footer",
		"h1",
		"h2",
		"h3",
		"h4",
		"h5",
		"h6",
		"header",
		"hr",
		"li",
		"main",
		"nav",
		"ol",
		"p",
		"pre",
		"section",
		"summary",
		"table",
		"tbody",
		"tfoot",
		"thead",
		"tr",
		"ul",
	}
)
_CELL_TAGS = frozenset({"td", "th"})
#: Attributes that are presentation, never content, so never read as text.
_NOT_TEXT_ATTRIBUTES = frozenset({"class", "style"})


def _page_lines(markup):
	reader = _PageLines()
	reader.feed(markup)
	reader.close()
	return reader.finish()


class _PageLines(HTMLParser):
	"""The text of a page as lines, with each line's attribute values appended after its text."""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self._lines = []
		self._text, self._attrs = [], []

	def _break(self):
		text = "".join(self._text).strip()
		attrs = " ".join(self._attrs).strip()
		line = f"{text} {attrs}".strip()
		if line:
			self._lines.append(line)
		self._text, self._attrs = [], []

	def _open(self, tag, attrs):
		if tag in _BLOCK_TAGS:
			self._break()
		elif tag in _CELL_TAGS:
			self._text.append(" ")
		self._attrs.extend(v for name, v in attrs if v and name not in _NOT_TEXT_ATTRIBUTES)

	def handle_starttag(self, tag, attrs):
		self._open(tag, attrs)

	def handle_startendtag(self, tag, attrs):
		self._open(tag, attrs)

	def handle_endtag(self, tag):
		if tag in _BLOCK_TAGS:
			self._break()
		elif tag in _CELL_TAGS:
			self._text.append(" ")

	def handle_data(self, data):
		pieces = data.split("\n")
		for i, piece in enumerate(pieces):
			if i:
				self._break()
			self._text.append(piece)

	def handle_comment(self, data):
		# Not shown on the page, but still in the HTML every reader of the stored text gets.
		self._break()
		self._text.append(data)
		self._break()

	def finish(self):
		self._break()
		return self._lines


# ------------------------------------------------------------------ the content hash

#: What the hash covers: the text a reader reads. Not the version number, the approver, the dates,
#: the review interval or the process owner, which change without the text changing; slice 4's
#: exporter decides for itself whether its Drive header needs a comparison of its own.
HASHED_FIELDS = ("title", "summary", "keywords", "body")

_SPACES = re.compile(r"[ \t\f\v]+")
_KEYWORD_SEPARATORS = re.compile(r"[,;\n]+")


def content_hash(doc):
	"""A SHA-256 hex digest of what a reader reads, stable across differences nobody can see.

	The WI names no rule, so this is the documented choice. Treated as the same text:

	* a missing value and an empty one;
	* ``\\r\\n``, ``\\r`` and ``\\n`` line endings;
	* the two Unicode spellings of one character (NFC), e.g. a pasted "é" as one code point or two;
	* space at either end of a field; runs of spaces or tabs inside the title, summary and keywords;
	* keyword order, case and repeats (``"PO, QBO"`` and ``"qbo,po, PO"`` hash the same);
	* presentation that :func:`strip_presentation` removes.

	Everything else counts, including space and markup inside the body, because inside a ``<pre>``
	or a table it is visible. A change to this normalisation changes every hash once, which costs
	one extra Drive export per article and nothing else.
	"""
	canonical = {
		"title": _line_text(doc.get("title")),
		"summary": "\n".join(_line_text(line) for line in _text(doc.get("summary")).split("\n")).strip(),
		"keywords": _keywords(doc.get("keywords")),
		"body": _text(strip_presentation(doc.get("body") or "")),
	}
	payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _text(value):
	if value is None:
		return ""
	text = value if isinstance(value, str) else str(value)
	text = text.replace("\r\n", "\n").replace("\r", "\n")
	return unicodedata.normalize("NFC", text).strip()


def _line_text(value):
	return _SPACES.sub(" ", _text(value))


def _keywords(value):
	words = {_line_text(word).casefold() for word in _KEYWORD_SEPARATORS.split(_text(value))}
	words.discard("")
	return sorted(words)
