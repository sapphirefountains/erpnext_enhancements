# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Content hygiene for the Knowledge Base: presentation stripped, secrets refused, a stable hash.

WI-080 PR 2, ADR 0017 section 1 ("On save and publish"). Standard library only, so every rule runs
in the bench-free CI tier. Three jobs:

* :func:`strip_presentation` removes the markup that can make text invisible to the person reading
  the page while leaving it in front of any AI that reads the HTML: colour, background, size and
  font, whether they arrive as ``style`` or as the attributes HTML had before CSS (``<font color
  size face>``, ``bgcolor``); the ``hidden`` attribute; **every class except the few v16's Text
  Editor writes for structure** (:data:`KEPT_CLASSES`), because any stylesheet on the page can hide
  text by class (``hidden``, ``d-none``, ``sr-only``, Frappe's own ``icon``, Quill's own
  ``ql-clipboard``); ``id``, which a stylesheet can target the same way; **the tags of every element
  except the ones v16's Text Editor writes** (:data:`KEPT_ELEMENTS`), because a browser never shows
  the text of a ``<dialog>``, ``<audio>``, ``<canvas>`` or an SVG ``<desc>``; and comments and
  declarations, which are never shown either. White-on-white or zero-size text is exactly what a
  reviewer would never see and a model would obey. Tables, lists, code blocks, alignment, direction
  and indent survive.
* :func:`secret_findings` finds secret-shaped strings (vendor keys, private keys, tokens, a
  password written out) and reports **where and what kind, never the value**, because the refusal
  message is shown on screen and may be logged.
* :func:`content_hash` answers "did the text people and AI read change?", for the Drive copy
  (slice 4) to compare.

**When they run.** The Version controller strips on every save and scans on every save that
changes content (``before_validate``, which runs before v16's own ``sanitize_html``), and scans
again at approval. ``body_md`` and ``content_hash`` are **not** computed
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

#: The only classes kept: exactly those v16's Text Editor writes for structure, each of which moves
#: or frames text and none of which can hide it. **Every other class is dropped**, because a class
#: means whatever the stylesheets on the page say, and the page carries Bootstrap, Frappe, ERPNext
#: and this app. ``hidden``, ``hide``, ``d-none``, ``invisible``, ``sr-only``, ``visually-hidden``
#: and ``text-white`` hide text. So do two that the old denylist of Quill's presentation classes
#: kept: Frappe's ``icon`` (``font-size: 0``, frappe ``origin/version-16``
#: ``public/scss/common/icons.scss:3``) and Quill's own ``ql-clipboard`` (``left: -100000px``, Quill
#: 2.0.3 ``packages/quill/src/assets/core.styl:30-35``). Compared exactly, case included, as a
#: browser in standards mode compares them.
#:
#: Derived from frappe ``origin/version-16`` (the ``.js`` files below are in
#: ``public/js/frappe/form/controls/``) and from Quill 2.0.3, the version v16 pins (the frappe
#: repo's ``package.json:73``; Quill paths are under ``packages/quill/src/``).
#: ``tests/test_knowledge_base_rules.py`` holds the cited lines verbatim, derives this set from
#: them, and checks the Frappe lines against a local v16 checkout when one is present.
#:
#: - ``ql-editor``, ``read-mode``: the wrapper the editor puts round every body it saves
#:   (``text_editor.js:402``, ``:405``).
#: - ``ql-indent-1`` to ``ql-indent-8``: indent, and the nesting of a list, which Quill writes as a
#:   flat list of indented items (toolbar ``text_editor.js:350``; ``formats/indent.ts:28-31``,
#:   registered by ``quill.ts:78``).
#: - ``ql-align-right``, ``ql-align-center``, ``ql-align-justify``: Quill's own form of alignment
#:   (``formats/align.ts:5``, ``:9``, registered by ``quill.ts:76``). v16 re-registers alignment as
#:   a style (``text_editor.js:106``, ``:111``), so its editor writes ``text-align`` instead (see
#:   :data:`KEPT_STYLE`), but it still reads these classes from pasted HTML, and Quill's stylesheet
#:   renders them.
#: - ``ql-direction-rtl``: right-to-left text (``text_editor.js:115-116`` registers the class
#:   attributor; ``formats/direction.ts:5``, ``:9``).
#: - ``ql-code-block-container``, ``ql-code-block``: a code block, which v16 writes as a ``<pre>``
#:   (``text_editor.js:7-9``; ``formats/code.ts:46``, ``:49``).
#: - ``ql-ui``: the empty span at the start of every list item that the bullet, number or checkbox
#:   is drawn on (``core/quill.ts:31``, attached by ``formats/list.ts:41``). Without it a list
#:   loses its markers. Quill only positions it (``assets/core.styl:205-206``).
#: - ``table``, ``table-bordered``: added to every table the editor inserts
#:   (``text_editor.js:53-54``). Quill's own table blots carry no class (``formats/table.ts``), only
#:   ``data-row``.
#: - ``mention``, ``ql-mention-denotation-char``: an @-mention
#:   (``quill-mention/blots/mention.js:49``, ``:9``; registered globally by ``comment.js:4``, used by
#:   ``text_editor.js:281``).
#:
#: Deliberately not kept, though Quill or Frappe can write them: ``ql-color-*``, ``ql-bg-*``,
#: ``ql-size-*`` and ``ql-font-*`` (presentation: v16 writes them as ``style`` instead,
#: ``text_editor.js:44-46``, ``:103-110``); ``icon`` and ``icon-sm`` on a group mention's SVG
#: (``mention.js:16``; ``icon`` is ``font-size: 0``, above, and the KB body does not enable
#: mentions, ``text_editor.js:302``, so one arrives only pasted); ``ql-cursor``, a transient caret
#: span holding one U+FEFF (``blots/cursor.ts:9``); and ``ql-video`` and ``ql-formula``, which
#: v16's toolbar does not offer (``text_editor.js:338-365``).
KEPT_CLASSES = frozenset(
	{
		"ql-editor",
		"read-mode",
		*(f"ql-indent-{level}" for level in range(1, 9)),
		"ql-align-right",
		"ql-align-center",
		"ql-align-justify",
		"ql-direction-rtl",
		"ql-code-block-container",
		"ql-code-block",
		"ql-ui",
		"table",
		"table-bordered",
		"mention",
		"ql-mention-denotation-char",
	}
)

#: What separates the classes in a ``class`` value: HTML's ASCII whitespace, not Python's wider
#: ``str.split``. A value a browser reads as one token is one token here too, and matches nothing.
_CLASS_SEPARATORS = re.compile(r"[\t\n\f\r ]+")

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

#: Every attribute dropped outright: the presentation attributes, and ``id``. v16's Text Editor
#: never writes an ``id`` (Quill has no format for one; a mention's user is ``data-id``), and a
#: stylesheet can hide an element by its id as surely as by its class: Frappe's desk stylesheet gives
#: ``#freeze`` ``opacity: 0`` (frappe ``origin/version-16`` ``public/scss/desk/global.scss:511-514``),
#: so ``<p id="freeze">`` is text no reader sees.
DROPPED_ATTRIBUTES = PRESENTATION_ATTRIBUTES | {"id"}

#: The only elements whose tags are kept: the ones v16's Text Editor writes, plus a table's header
#: row. **Every other element is unwrapped**: its start and end tags go and what it held stays, as
#: text a reader sees. v16's ``sanitize_html`` allows over a hundred more (frappe
#: ``origin/version-16`` ``utils/html_utils.py``: ``acceptable_elements``, ``svg_elements``,
#: ``mathml_elements``), and a browser never shows the text of many of them: ``<dialog>`` and
#: ``<audio>`` are ``display: none``; ``<datalist>``, ``<video>``, ``<canvas>``, ``<meter>``,
#: ``<progress>`` and an SVG ``<desc>``, ``<title>`` or ``<metadata>`` render none of their text; an
#: SVG's own ``opacity``/``visibility``/``font-size`` attributes and MathML's ``<mphantom>`` hide it.
#: The text of each is still in the HTML, and in ``body_md``. SVG and MathML are unwrapped like the
#: rest, so nothing of either survives to hide what is left. As with classes, this is an allowlist
#: on purpose: an element this does not know is one whose rendering it cannot vouch for.
#:
#: Each tag is the ``tagName`` of a format v16's editor uses, Quill 2.0.3 (``packages/quill/src/``)
#: or v16's own (frappe ``origin/version-16``, ``text_editor.js`` and ``mention.js`` are under
#: ``public/js/frappe/form/controls/``). ``tests/test_knowledge_base_rules.py`` holds the cited
#: lines verbatim and derives this set from them.
#:
#: - ``p``, ``br``: Quill's block and line break (``blots/block.ts:127``, ``blots/break.ts:23``);
#:   v16 registers its own ``br`` too (``text_editor.js:15``).
#: - ``h1`` to ``h6``, ``blockquote`` (``formats/header.ts:5``, ``formats/blockquote.ts:5``).
#: - ``ol``, ``li``: a list, which Quill writes as ``<ol>`` whatever its kind
#:   (``formats/list.ts:8``, ``:53``); ``ul``: v16 rewrites a bullet list as one
#:   (``patch_unordered_list``, ``text_editor.js:428``).
#: - ``pre``, ``div``: a code block, whose container v16 makes a ``<pre>`` (``text_editor.js:8``)
#:   round one ``div`` per line (``formats/code.ts:47``); ``div`` is also the ``ql-editor`` wrapper
#:   (``text_editor.js:402``).
#: - ``table``, ``tbody``, ``tr``, ``td`` (``formats/table.ts:128``, ``:121``, ``:61``, ``:7``).
#:   ``thead`` and ``th`` are not Quill's, but a table written any other way has them, a header cell
#:   shows its text like any other, and unwrapping them would break the table apart.
#: - ``strong`` and ``b``, ``em`` and ``i``, ``s`` and ``strike``, ``u``, ``sub`` and ``sup``,
#:   ``code``, ``a``, ``img`` (``formats/bold.ts:5``, ``italic.ts:5``, ``strike.ts:5``,
#:   ``underline.ts:5``, ``script.ts:5``, ``code.ts:43``, ``link.ts:5``, ``image.ts:8``). Quill
#:   writes the first of each pair and reads both as the same format.
#: - ``span``: every inline style, the list marker and a mention (``blots/cursor.ts:10``,
#:   ``mention.js:48``).
#: - ``font``: v16's ``CustomColor`` blot (``text_editor.js:132``). Only a bare one survives, since
#:   its ``color``, ``size`` and ``face`` are in :data:`PRESENTATION_ATTRIBUTES`.
KEPT_ELEMENTS = frozenset(
	{
		"p",
		"br",
		*(f"h{level}" for level in range(1, 7)),
		"blockquote",
		"ol",
		"ul",
		"li",
		"pre",
		"div",
		"table",
		"thead",
		"tbody",
		"tr",
		"th",
		"td",
		"strong",
		"b",
		"em",
		"i",
		"s",
		"strike",
		"u",
		"sub",
		"sup",
		"code",
		"a",
		"img",
		"span",
		"font",
	}
)

#: Elements that go together with what they hold: they hold code, not text, and v16's own list of
#: tags whose content is removed with them is exactly these two (``REMOVE_CONTENT_TAGS``, frappe
#: ``origin/version-16`` ``utils/html_utils.py:21``).
DROPPED_WITH_CONTENT = frozenset({"script", "style"})

#: An end tag with nothing in it but its name, which is kept byte for byte (``</P>`` stays ``</P>``).
#: Anything else in an end tag is ignored by a browser, and is not kept.
_PLAIN_END_TAG = re.compile(r"</([a-zA-Z][a-zA-Z0-9]*)>")
#: HTMLParser counts lines on "\n" alone, so positions are mapped back the same way.
_NEWLINE = re.compile("\n")


def strip_presentation(markup):
	"""``markup`` with presentation removed. Anything not a string is returned as it came.

	Kept: the start and end tags of :data:`KEPT_ELEMENTS`, with only their dropped attributes, their
	dropped ``style`` declarations and their classes outside :data:`KEPT_CLASSES` removed, and all
	text. Removed: the tags of every other element (what it held stays), ``script`` and ``style``
	with their contents, and every comment, declaration, CDATA section and processing instruction.

	**Why comments and declarations go whole.** Python's parser and a browser disagree about where
	some of them end: Python reads ``<!-->`` as the start of a comment that runs to the next
	``-->``, and ``<![CDATA[`` as a section that runs to ``]]>``, where a browser ends both at the
	first ``>``. A tag Python counted as inside one is a live element to a browser, so it must not
	be kept unread. The same goes for text Python reads as raw (inside ``<textarea>``, ``<title>``,
	``<xmp>``, ``<plaintext>`` and the like, where a browser inside an ``<svg>`` reads markup): when
	its element is unwrapped it is written back **escaped**, as text, never as markup. Any other
	text holding a raw ``<`` is escaped for the same reason; v16's editor and ``sanitize_html``
	both write ``&lt;``, so a real body never has one.

	Everything else (text, entities, the kept tags that need no change and their end tags, ``data:``
	images) is returned byte for byte, so a body with nothing to strip comes back identical. The output holds only
	kept tags and text with no raw ``<``, so stripping it again changes nothing. Tags are found with
	the standard library's HTML parser rather than a regex, so an attribute value containing ``>``
	or an unquoted value containing ``=`` is read the way a browser reads it.
	"""
	if not isinstance(markup, str) or "<" not in markup:
		return markup
	reader = _Markup()
	reader.feed(markup)
	reader.close()

	line_starts = [0]
	line_starts.extend(match.end() for match in _NEWLINE.finditer(markup))
	starts = [line_starts[line - 1] + offset for line, offset, _kind, _value in reader.tokens]
	starts.append(len(markup))
	out = []
	for index, (_line, _offset, kind, value) in enumerate(reader.tokens):
		out.append(_rewritten(kind, value, markup[starts[index] : starts[index + 1]]))
	stripped = "".join(out)
	return markup if stripped == markup else stripped


def _rewritten(kind, value, raw):
	"""What one token of the markup becomes. ``raw`` runs from the token to the next one, so it also
	holds any bytes the parser passed over without a token (``</>``, a tag cut off at the end),
	which a browser ignores too: only a text token's ``raw`` is ever written back, and only when it
	holds no ``<``, which every one of those starts with."""
	if kind == "start":
		tag, attrs, closed, text = value
		if tag not in KEPT_ELEMENTS:
			return ""
		cleaned = _clean_attrs(attrs)
		return text if cleaned == attrs else _start_tag(tag, cleaned, closed)
	if kind == "end":
		if value not in KEPT_ELEMENTS:
			return ""
		plain = _PLAIN_END_TAG.match(raw)
		return plain.group(0) if plain and plain.group(1).lower() == value else f"</{value}>"
	if kind == "text":
		return raw if "<" not in raw else html.escape(value, quote=True)
	return ""  # a comment, a declaration, a processing instruction, or script or style content


class _Markup(HTMLParser):
	"""The markup as tokens, each ``(line, offset, kind, value)`` at the position it starts.

	``convert_charrefs`` is on, so text arrives decoded, and no character or entity reference is
	reported as a token of its own.
	"""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.tokens = []

	def _note(self, kind, value):
		line, offset = self.getpos()
		self.tokens.append((line, offset, kind, value))

	def handle_starttag(self, tag, attrs):
		self._note("start", (tag, attrs, False, self.get_starttag_text()))

	def handle_startendtag(self, tag, attrs):
		self._note("start", (tag, attrs, True, self.get_starttag_text()))

	def handle_endtag(self, tag):
		self._note("end", tag)

	def handle_data(self, data):
		self._note("dropped" if self.cdata_elem in DROPPED_WITH_CONTENT else "text", data)

	def handle_comment(self, data):
		self._note("dropped", None)

	def handle_decl(self, decl):
		self._note("dropped", None)

	def unknown_decl(self, data):
		self._note("dropped", None)

	def handle_pi(self, data):
		self._note("dropped", None)


def _clean_attrs(attrs):
	cleaned = []
	for name, value in attrs:
		if name in DROPPED_ATTRIBUTES:
			continue
		if name == "style":
			kept = _kept_style(value)
			if kept is not None:
				cleaned.append((name, kept))
		elif name == "class":
			tokens = [t for t in _CLASS_SEPARATORS.split(value or "") if t]
			kept = [t for t in tokens if t in KEPT_CLASSES]
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
