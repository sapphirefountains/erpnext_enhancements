"""Allowlist sanitizer for concept-screen HTML and CSS (WI-079 slice 5, ADR 0016 §2).

Concept screens are model-written HTML that the Review Room renders inside the Desk's origin,
where a System Manager's session lives. Two independent layers keep that safe:

1. **This module, on import.** Every tag and attribute not on an allowlist is dropped, every
   event-handler attribute goes, ``<script>``, ``<style>``, ``<iframe>`` and their content go,
   every ``href`` becomes ``#``, an ``src`` survives only as a ``data:image/`` URI, and a
   ``style`` attribute that mentions ``url(``, ``expression(``, ``@import`` or ``javascript:``
   is dropped whole.
2. **The frame, on render.** Each screen is drawn in an ``<iframe sandbox="allow-same-origin">``
   (never with ``allow-scripts``: that pair lets a frame lift its own sandbox) carrying a
   ``Content-Security-Policy`` of ``default-src 'none'``. The spike recorded in WI-079 proved,
   in Chrome with real mouse input, that an *unsanitized* hostile screen ran no script and
   reached no host with the policy in place.

The parser is the standard library's ``html.parser`` and the output is re-serialized from
its events, never patched with a regular expression (see the memory note on regex-rewritten
sanitized HTML: a heading regex once turned an inert attribute into live script).

Stdlib only, so ``tests/test_design_review_sanitize.py`` runs without a bench. Tabs, per
``CLAUDE.md``.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset(
	{
		"div",
		"span",
		"p",
		"a",
		"button",
		"h1",
		"h2",
		"h3",
		"h4",
		"h5",
		"h6",
		"ul",
		"ol",
		"li",
		"nav",
		"header",
		"footer",
		"main",
		"section",
		"aside",
		"article",
		"strong",
		"em",
		"b",
		"i",
		"small",
		"br",
		"hr",
		"label",
		"input",
		"textarea",
		"select",
		"option",
		"table",
		"thead",
		"tbody",
		"tr",
		"th",
		"td",
		"img",
		"details",
		"summary",
		"pre",
		"code",
		"blockquote",
		"svg",
		"path",
		"circle",
		"rect",
		"line",
		"polyline",
		"polygon",
		"g",
		"text",
		"tspan",
		"ellipse",
	}
)

#: Dropped together with everything inside them.
DROPPED_WITH_CONTENT = frozenset(
	{
		"script",
		"style",
		"iframe",
		"frame",
		"frameset",
		"object",
		"embed",
		"template",
		"noscript",
		"link",
		"meta",
		"base",
		"form",
		"foreignobject",
		"math",
		"audio",
		"video",
		"source",
		"track",
		"canvas",
		"applet",
		"portal",
		"title",
		"head",
	}
)

#: Tags written without a closing tag.
VOID_TAGS = frozenset({"br", "hr", "input", "img"})

ALLOWED_ATTRS = frozenset(
	{
		"class",
		"style",
		"data-c",
		"type",
		"checked",
		"value",
		"placeholder",
		"aria-label",
		"aria-hidden",
		"title",
		"role",
		"name",
		"href",
		"src",
		"alt",
		"width",
		"height",
		"viewbox",
		"fill",
		"stroke",
		"stroke-width",
		"stroke-linecap",
		"stroke-linejoin",
		"d",
		"cx",
		"cy",
		"r",
		"x",
		"y",
		"x1",
		"x2",
		"y1",
		"y2",
		"points",
		"transform",
		"text-anchor",
		"font-size",
		"font-weight",
		"rx",
		"ry",
		"colspan",
		"rowspan",
		"disabled",
		"selected",
		"open",
		"for",
		"id",
	}
)

#: html.parser lowercases attribute names; SVG needs this one back in camel case.
_SVG_CASE = {"viewbox": "viewBox"}

_DANGEROUS_CSS = re.compile(r"url\s*\(|expression\s*\(|@import|javascript:|behavior\s*:|-moz-binding", re.I)
_DATA_IMAGE = re.compile(r"^data:image/(png|jpeg|gif|webp);base64,[a-z0-9+/=\s]+$", re.I)


class _Sanitizer(HTMLParser):
	def __init__(self) -> None:
		super().__init__(convert_charrefs=False)
		self.out: list[str] = []
		self.skip = 0
		self.dropped: set[str] = set()

	def handle_starttag(self, tag, attrs):
		self._start(tag, attrs)

	def handle_startendtag(self, tag, attrs):
		self._start(tag, attrs, selfclosing=True)

	def _start(self, tag, attrs, selfclosing=False):
		if tag in DROPPED_WITH_CONTENT:
			self.dropped.add(tag)
			if not selfclosing and tag not in VOID_TAGS:
				self.skip += 1
			return
		if self.skip:
			return
		if tag not in ALLOWED_TAGS:
			self.dropped.add(tag)
			return
		kept = []
		for key, value in attrs:
			key = (key or "").lower()
			value = value or ""
			if key.startswith("on") or key not in ALLOWED_ATTRS:
				self.dropped.add("@" + key)
				continue
			if key == "style" and _DANGEROUS_CSS.search(value):
				self.dropped.add("@style")
				continue
			if key == "href":
				# A concept's links never leave the frame; the Review Room decides where a click goes.
				value = "#"
			if key == "src" and not _DATA_IMAGE.match(value.strip()):
				self.dropped.add("@src")
				continue
			kept.append((_SVG_CASE.get(key, key), value))
		attributes = "".join(f' {k}="{escape(v, quote=True)}"' for k, v in kept)
		if selfclosing and tag not in VOID_TAGS:
			self.out.append(f"<{tag}{attributes}></{tag}>")
		else:
			self.out.append(f"<{tag}{attributes}>")

	def handle_endtag(self, tag):
		if tag in DROPPED_WITH_CONTENT:
			if self.skip:
				self.skip -= 1
			return
		if self.skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
			return
		self.out.append(f"</{tag}>")

	def handle_data(self, data):
		if not self.skip:
			self.out.append(escape(data, quote=False))

	def handle_entityref(self, name):
		if not self.skip:
			self.out.append(f"&{name};")

	def handle_charref(self, name):
		if not self.skip:
			self.out.append(f"&#{name};")

	def handle_comment(self, data):
		# Comments are dropped: a conditional comment is a parser differential waiting to happen.
		self.dropped.add("<!--")

	def handle_decl(self, decl):
		self.dropped.add("<!")

	def handle_pi(self, data):
		self.dropped.add("<?")


def sanitize_html(html: str) -> tuple[str, list[str]]:
	"""``(clean_html, what_was_dropped)``. What was dropped is reported, not hidden, so an
	import can say "this screen lost an onclick" instead of rendering it silently different."""
	parser = _Sanitizer()
	parser.feed(html or "")
	parser.close()
	return "".join(parser.out), sorted(parser.dropped)


#: The one font a concept may name. Everything else in a stylesheet stays declarative.
_FONT_FACE = re.compile(r"@font-face\s*\{[^}]*\}", re.I)
_IMPORT = re.compile(r"@import[^;{}]*;?", re.I)


def sanitize_css(css: str) -> tuple[str, list[str]]:
	"""A concept stylesheet, minus anything that can fetch or execute.

	``@font-face`` blocks are removed (the Review Room supplies the brand face itself), and so
	is any declaration block containing ``url(``, ``expression(``, ``@import`` or
	``javascript:``. The frame's policy would block a fetch anyway; this keeps the stored
	copy honest about what it does.
	"""
	dropped: list[str] = []
	css = css or ""
	if _FONT_FACE.search(css):
		dropped.append("@font-face")
		css = _FONT_FACE.sub("", css)
	if "<" in css:
		# Nothing in a stylesheet needs "<", and "</style" is how CSS escapes its element.
		dropped.append("<")
		css = css.replace("<", "")
	if _IMPORT.search(css):
		dropped.append("@import")
		css = _IMPORT.sub("", css)
	out = []
	for block in re.split(r"(?<=\})", css):
		if _DANGEROUS_CSS.search(block):
			dropped.append(block.strip()[:60])
			continue
		out.append(block)
	return "".join(out), dropped
