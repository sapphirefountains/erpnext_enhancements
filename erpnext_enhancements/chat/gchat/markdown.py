# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Markdown → Google Chat's own text formatting. The third of the three renderers.

**The bug this exists to fix, and why it looked intermittent.** Google Chat's bold is a
**single** asterisk (``*bold*``), not a double one. Triton emits CommonMark, so
``**Incompressibility:**`` reached the Chat client as ``*`` + ``*Incompressibility:*`` + ``*``
— which Chat renders as the phrase **in bold with a stray asterisk glued to it**. To a reader
that looks like the formatting works sometimes and produces a spare character other times,
which is exactly how it was reported. It is neither: it is one deterministic mistranslation.

Chat's whole formatting vocabulary is small and this module targets it exactly:

===================  ==========================  ==========================================
CommonMark           Google Chat                 Note
===================  ==========================  ==========================================
``**b**`` / ``__b__``  ``*b*``                   the reported bug
``*i*`` / ``_i_``      ``_i_``                   asterisk-italic must become underscore
``~~s~~``              ``~s~``
`````c`````            unchanged                 already Chat's syntax
fenced block           unchanged                 already Chat's syntax
``# Heading``          ``*Heading*``             Chat has no headings; bold is the closest
``- item``             ``• item``                Chat has no list syntax; the bullet is text
``1. item``            unchanged                 already reads as a list
``[label](url)``       ``label: url``            Chat autolinks bare URLs; ``<u|l>`` is Slack
===================  ==========================  ==========================================

WHY THIS IS A TOKENISER AND NOT A CHAIN OF ``re.sub`` CALLS
===========================================================

The obvious implementation is three substitutions, and it is wrong in a way that only shows up
on real input. Turning ``**b**`` into ``*b*`` and *then* turning ``*i*`` into ``_i_`` converts
the output of the first rule with the second, so every bold word silently becomes italic. Any
ordering has a case like it, because the source and target alphabets overlap.

So this walks the string once, longest-delimiter-first, and never re-examines what it has
already emitted — the same shape as the browser-side renderer in
``public/js/chat/markdown.js``, for the same reason.

**Code spans are extracted first and restored last.** Their contents are literal by definition:
``` `**not bold**` ``` must survive with both asterisks, and a substitution pass would eat them.

Indentation is tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import re
from typing import Final

#: Fenced blocks and inline code, matched together so the longer form wins. Extracted before
#: any conversion and put back afterwards, untouched.
_CODE_RE: Final = re.compile(r"(```.*?```|`[^`\n]+`)", re.S)

#: Inline rules, tried longest-delimiter-first at each position. Order is load-bearing: ``**``
#: before ``*``, or bold degrades to italic-plus-a-stray-asterisk — the same class of mistake
#: this module exists to undo.
_INLINE_RULES: Final = (
	# Non-greedy, and NOT `[^*]*`: the inner run must be allowed to contain single asterisks
	# so that `**bold with *inner* italic**` matches the outer pair. Non-greedy is what stops
	# `**a** and **b**` being read as one span from the first `**` to the last.
	(re.compile(r"\*\*(\S(?:.*?\S)?)\*\*"), "*{}*"),
	(re.compile(r"__(\S(?:.*?\S)?)__"), "*{}*"),
	(re.compile(r"~~(\S(?:.*?\S)?)~~"), "~{}~"),
	(re.compile(r"\*(\S(?:[^*]*\S)?)\*"), "_{}_"),
)

#: ``[label](url)``. Chat has no link syntax for plain-text messages — ``<url|label>`` is
#: Slack's, and it arrives in Chat as those literal characters — so the label and the URL are
#: both emitted and Chat's own autolinker makes the URL clickable.
_LINK_RE: Final = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")

_HEADING_RE: Final = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE: Final = re.compile(r"^(\s*)[-*+]\s+(.*)$")

#: What a bullet becomes. A real bullet character rather than ``-``, because the point is that
#: it reads as a list in a client that has no list markup.
_BULLET: Final = "• "

#: Underscore emphasis does not open inside a word: ``snake_case_name`` is one identifier, and
#: Triton emits identifiers constantly. Matches the browser renderer's rule and CommonMark's.
_WORD_CHAR: Final = re.compile(r"[A-Za-z0-9]")


def to_google_chat(source: str | None) -> str:
	"""Rewrite CommonMark into Google Chat's formatting. Pure; no I/O, no settings.

	Anything the table above does not name passes through as the characters the model typed,
	which is the correct failure mode: an unconverted construct is a cosmetic miss, while a
	half-converted one is the stray-asterisk bug in a new costume.
	"""
	text = str(source or "")
	if not text:
		return ""

	spans: list[str] = []

	def _stash(match: re.Match[str]) -> str:
		spans.append(match.group(0))
		return f"\x00{len(spans) - 1}\x00"

	text = _CODE_RE.sub(_stash, text)
	text = "\n".join(_convert_line(line) for line in text.split("\n"))

	for index, span in enumerate(spans):
		text = text.replace(f"\x00{index}\x00", span)
	return text


def _convert_line(line: str) -> str:
	heading = _HEADING_RE.match(line)
	if heading:
		# Chat has no headings. Bold is the closest thing it has, and a heading rendered as
		# body text loses the only cue that a section started.
		return f"*{_convert_inline(heading.group(2))}*" if heading.group(2).strip() else ""

	bullet = _BULLET_RE.match(line)
	if bullet:
		return f"{bullet.group(1)}{_BULLET}{_convert_inline(bullet.group(2))}"

	return _convert_inline(line)


def _convert_inline(text: str) -> str:
	text = _LINK_RE.sub(lambda m: f"{m.group(1)}: {m.group(2)}" if m.group(1) else m.group(2), text)

	out: list[str] = []
	index = 0
	while index < len(text):
		match = _first_match(text, index)
		if match is None:
			out.append(text[index:])
			break
		rule_match, template = match
		out.append(text[index : rule_match.start()])
		out.append(template.format(_convert_inline(rule_match.group(1))))
		index = rule_match.end()
	return "".join(out)


def _first_match(text: str, start: int) -> tuple[re.Match[str], str] | None:
	"""The earliest rule match at or after ``start``, ties going to the earlier rule.

	Ties matter: at the same offset ``**`` and ``*`` both match, and the tuple order is what
	makes bold win. ``_italic_`` is handled by the *underscore* branch below rather than by a
	rule, because it needs the intraword guard and because its output is its input — Chat and
	CommonMark spell italic the same way, so the only work is deciding whether it is one.
	"""
	best: tuple[re.Match[str], str] | None = None
	for pattern, template in _INLINE_RULES:
		found = pattern.search(text, start)
		if found and (best is None or found.start() < best[0].start()):
			best = (found, template)

	underscore = _underscore_italic(text, start)
	if underscore and (best is None or underscore.start() < best[0].start()):
		best = (underscore, "_{}_")
	return best


def _underscore_italic(text: str, start: int) -> re.Match[str] | None:
	"""``_italic_``, but only where it is not inside a word.

	Scans forward past rejected hits rather than giving up on the first one: in
	``a_b and _real_ italic`` the intraword run must be skipped and the genuine emphasis still
	found.
	"""
	pattern = re.compile(r"_(\S(?:[^_]*\S)?)_")
	position = start
	while position < len(text):
		found = pattern.search(text, position)
		if not found:
			return None
		before = text[found.start() - 1] if found.start() > 0 else ""
		after = text[found.end()] if found.end() < len(text) else ""
		if not _WORD_CHAR.match(before or "") and not _WORD_CHAR.match(after or ""):
			return found
		position = found.start() + 1
	return None
