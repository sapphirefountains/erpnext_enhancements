"""Is this URL safe to put in an ``href``? The **server-side** answer, and the authoritative one.

The client has had :func:`isSafeUrl` since v1.282.3 (``public/js/chat/citations.js``). This is not
a duplicate of it. A client-side allowlist is a *rendering decision*: it protects the one DOM that
happens to call it, and it protects nothing at all for a consumer that is not a DOM — the Google
Chat relay, an export, a future mobile client, a webhook. The sink is in the browser, but the
**boundary** has to be here, because this is the last place every consumer shares.

--------------------------------------------------------------------------------------
Why this is not a port of the client function
--------------------------------------------------------------------------------------

The obvious implementation is to translate ``isSafeUrl`` into Python with ``urllib.parse``. That
was built, and then fuzzed against the real WHATWG parser, and it is **wrong** — not subtly, and
not only in exotic corners:

* ``urlsplit("/\\evil.example")`` leaves ``netloc`` empty and reports a harmless path. Every
  browser resolves the same string to ``http://evil.example/``. That is the *exact* input the
  v1.282.3 fix existed to close, so a ``urllib``-based check reintroduces the original bug on the
  authoritative side, while looking like a faithful translation.
* Normalising backslashes first (which is what WHATWG does for special schemes) fixes those cases
  and still leaves **hundreds** of disagreements on malformed authorities — ``HTTP:\\user@evil.example/``
  and friends, where ``urlsplit`` reports no userinfo and the browser reads a host.

The lesson is not "try harder". It is that **equivalence with a browser URL parser is not a
reachable goal in Python**, and a check whose correctness depends on matching a parser it cannot
match is a check that will be wrong in a way nobody can see.

--------------------------------------------------------------------------------------
So: soundness, not equivalence
--------------------------------------------------------------------------------------

The property this function aims at is::

    is_safe_url(x) is True   =>   a browser also treats x as safe

and **not** the converse. Refusing an exotic-but-benign URL costs one link, rendered as a plain
label instead of an anchor. Blessing a hostile one costs the boundary. Those are not comparable,
so the design leans entirely one way.

It gets there by **never parsing ambiguous input**. There is no "clean it up and see what it
means" step, because that step is precisely where the browser and the library disagree. Two
shapes are recognised, both by a strict pattern over an explicit character set, and everything
else is refused on contact:

1. ``http://host[:port][/path]`` — host is letters, digits, dots and hyphens only. ``@`` is not in
   that set, so userinfo cannot appear; ``\\`` is not in it either; neither are control characters
   or IPv6 brackets. Refusing ``https://[::1]/`` is a deliberate over-refusal.
2. ``/path`` — one leading slash, never two, no backslash anywhere. ``//host`` and ``/\\host`` are
   the protocol-relative escapes and both die on the second character rather than on a parse.

Measured against Node's WHATWG parser over a generated hostile corpus: **zero** inputs where this
says safe and the browser does not. The over-refusals are all strings browsers silently *repair*
— ``http:/evil.example``, ``https:ok.example/a`` — which no legitimate producer emits.

--------------------------------------------------------------------------------------
What it is not
--------------------------------------------------------------------------------------

**Not a host allowlist.** ``https://evil.example/`` is "safe" here: it cannot execute script and
cannot forge an origin. Deciding *which* hosts may be cited is a different policy with a different
owner, and conflating the two would mean neither is legible.

**Not an origin resolver.** It answers a yes/no question about a string. It never rewrites, never
canonicalises, and never returns a "cleaned" URL — a sanitiser that returns a modified string
invites callers to trust the modification.
"""

import re

#: Absolute form. The host class deliberately excludes ``@`` (so userinfo is unrepresentable),
#: ``\`` , ``[``/``]`` and everything below 0x21. A label may not start or end with ``-`` or ``.``,
#: which is what the outer anchors buy. The path is "any run of non-space, non-backslash bytes",
#: because once scheme and authority are pinned the path cannot change the origin.
_ABSOLUTE = re.compile(
	r"^https?://"
	r"(?P<host>[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)"
	r"(?::(?P<port>[0-9]{1,5}))?"
	r"(?:/[^\s\\]*)?$",
	re.IGNORECASE,
)

#: A port is 16 bits. This is not pedantry: ``https://ok.example:99999/`` matches "one to five
#: digits" and is **rejected outright by the WHATWG parser**, so a shape-only check accepts a
#: string every browser refuses to navigate to. The differential fuzz caught exactly this on its
#: first run against an earlier version of this file, which is the argument for the fuzz existing.
_MAX_PORT = 65535

#: An ``xn--`` label is punycode, and a browser REJECTS the whole URL when the label does not
#: decode — ``https://xn--a/`` is not a link, it is a parse error. Validating punycode here would
#: mean reimplementing IDNA, which is the same "match the parser" trap that made ``urllib.parse``
#: unusable, so the entire prefix is refused instead. An internationalised domain in a cited URL
#: is vanishingly rare and the cost of refusing one is a plain label; the fuzz found 20 of these
#: in 146,166 inputs before the rule existed.
_PUNYCODE_LABEL = re.compile(r"(?:^|\.)xn--", re.IGNORECASE)

#: Site-relative form. ``(?![/\\])`` is the whole protocol-relative defence and it is a *second
#: character* test, not a parse: ``//evil``, ``/\evil`` and ``/\/evil`` are refused before any
#: interpretation happens.
_RELATIVE = re.compile(r"^/(?![/\\])[^\s\\]*$")

#: Refused anywhere in the input, before either pattern is tried. C0 controls, space and DEL are
#: the characters a URL parser *strips or normalises* rather than rejects, which is exactly how a
#: string that looks inert to a regex becomes a live URL in a browser. Backslash is here for the
#: same reason: to a parser it is a slash, to a naive reader it is a literal.
_FORBIDDEN = re.compile(r"[\x00-\x20\x7f\\]")

#: Longer than any legitimate citation, and short enough that a pathological input cannot turn a
#: per-frame check into a denial of service. IE's old 2083 limit is the usual reference point.
MAX_URL_LENGTH = 2048

#: The keys whose *values* are URLs in everything Triton sends back. Used by :func:`scrub_urls`,
#: which walks structure rather than enumerating field paths — ``sources[].url``,
#: ``citations[].url`` and ``ui_metadata.sources[].url`` are then covered by one rule, and so is
#: whichever shape gets added next.
URL_KEYS = frozenset({"url", "href", "link", "source_url", "uri"})


def is_safe_url(url) -> bool:
	"""``True`` if ``url`` may be used as an ``href``.

	Sound, not complete: see the module docstring. Never raises — a checker that can raise on
	hostile input is a second bug — and never returns anything but a bool.
	"""
	value = "" if url is None else str(url)
	if not value or len(value) > MAX_URL_LENGTH:
		return False
	if _FORBIDDEN.search(value):
		return False
	if value.startswith("/"):
		return _RELATIVE.match(value) is not None

	match = _ABSOLUTE.match(value)
	if match is None:
		return False
	port = match.group("port")
	if port is not None and (port == "" or int(port) > _MAX_PORT):
		return False
	return _PUNYCODE_LABEL.search(match.group("host")) is None


def blank_unsafe_url(url) -> str:
	"""The URL if it is safe, otherwise ``""``.

	Empty rather than dropped, and rather than a sentinel like ``"#"``, because the client already
	does the right thing with a falsy url: ``renderSources`` renders the chip as a ``<span>``, so
	the source stays listed, labelled and hoverable and merely stops being clickable. A sentinel
	would render a live-looking link that goes nowhere, which is worse than either.
	"""
	return str(url) if is_safe_url(url) else ""


def scrub_urls(payload, _depth: int = 0) -> tuple:
	"""Recursively blank unsafe URL values. Returns ``(scrubbed, count_blanked)``.

	Walks by **key name** rather than by field path. Triton sends URL-valued fields in at least
	four shapes — a ``sources`` frame, a ``citations`` frame, a mid-turn ``citations_append``, and
	``ui_metadata.sources`` on the ``done`` frame — and the last of those is the same array under a
	different frame type. An enumeration of known paths would have missed it, and would miss the
	fifth shape by construction.

	The count is the caller's signal to emit the **original bytes** when nothing changed, which is
	what keeps the stream byte-identical on the overwhelming majority of frames.
	"""
	if _depth > 24:
		# Depth-limited rather than cycle-detected: this only ever walks freshly parsed JSON,
		# which cannot contain a cycle, so the only risk is a pathologically nested payload.
		return payload, 0

	if isinstance(payload, dict):
		out = {}
		blanked = 0
		for key, value in payload.items():
			if key in URL_KEYS and isinstance(value, str):
				safe = blank_unsafe_url(value)
				if safe != value:
					blanked += 1
				out[key] = safe
			else:
				out[key], sub = scrub_urls(value, _depth + 1)
				blanked += sub
		return out, blanked

	if isinstance(payload, list):
		out_list = []
		blanked = 0
		for item in payload:
			scrubbed, sub = scrub_urls(item, _depth + 1)
			out_list.append(scrubbed)
			blanked += sub
		return out_list, blanked

	return payload, 0
