# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The citation manifest, and the URL resolution that keeps a model out of the anchor.

**Citations are assembly-order integers whose manifest is known before generation.** That is
the whole design in one sentence, and every property below follows from it: the manifest can
stream *first*, inline links resolve live as tokens arrive, and an id the model invents
resolves to nothing rather than to somebody else's room.

--------------------------------------------------------------------------------------
The model never authors a URL
--------------------------------------------------------------------------------------

The model emits ``[[ref:7]]``. It does not emit a link, and there is no code path by which
text it produced becomes an ``href``. The URL for ref 7 was built **server-side** from the
manifest before generation started.

That is not defence-in-depth, it is the actual defence. A model that can author an anchor can
author ``javascript:…``, or a link into a room the reader is not in, or a plausible-looking
link to an unrelated message — and the last of those is the dangerous one, because nothing
about it looks wrong. Resolving server-side from a manifest the gate built makes all three
unreachable rather than filtered.

**Anchors are built with DOM APIs, never ``innerHTML``.** The widget renders assistant text
through ``frappe.markdown(...)``, and whether the deployed Frappe sanitises that output is
itself an open question — so building citation anchors with ``createElement``/``textContent``
keeps them out of that blast radius entirely rather than betting on the answer.

--------------------------------------------------------------------------------------
An unknown ref is dropped silently, and counted
--------------------------------------------------------------------------------------

A model citing ``[[ref:99]]`` when the manifest has eleven entries is not a page the user
should see broken. The marker is removed and the sentence reads normally.

But it is **counted**, because a rising miss rate is a *prompt* regression signal rather than
a UI bug: the model has started inventing citations, which means it is being asked for them in
a way it cannot satisfy. Silence about that is how a slow degradation goes unnoticed for
months.

--------------------------------------------------------------------------------------
The stream tail buffer
--------------------------------------------------------------------------------------

``[[ref:11]]`` can arrive split across two SSE frames. A renderer that parses each frame in
isolation produces two broken fragments where there should be one anchor, and it does so only
sometimes — which is the worst kind of bug to reproduce. The client holds back up to
:data:`MAX_MARKER_LENGTH` characters of tail until it can prove they are not a partial marker.
That constant lives here so both sides derive it from one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from erpnext_enhancements.chat.links import build_chat_route

#: What the model is asked to emit, and what the client parses. Tolerant of internal spacing
#: because a model will produce ``[[ref: 3]]`` sooner or later and refusing it buys nothing.
REF_PATTERN: re.Pattern[str] = re.compile(r"\[\[\s*ref\s*:\s*(\d+)\s*\]\]")

#: The longest marker the tolerant pattern can match for a plausible id, which is how much
#: tail a streaming renderer must hold back. ``[[ref: 999 ]]`` is 14; 16 is that plus slack.
MAX_MARKER_LENGTH: int = 16

#: The label format for a context line, which is what the model sees. The timestamp and the
#: zone are both present on purpose: "yesterday at 2" is ambiguous across a team in two
#: timezones, and a model asked to reason about ordering needs the offset rather than a
#: friendly string.
CONTEXT_LINE_TEMPLATE: str = "⟦ref:{ref}⟧ {author} ({timestamp}): {body}"


@dataclass(frozen=True)
class Citation:
	"""One manifest entry. ``ref`` is its position in assembly order, 1-based."""

	ref: int
	room: str
	message: str | None
	label: str
	#: ``chunk`` / ``message`` / ``digest`` — what the reader will land on. The client shows
	#: a digest citation differently because "a summary said so" is a weaker claim than "this
	#: message said so", and collapsing the two would overstate the evidence.
	kind: str = "chunk"
	subtitle: str = ""
	thread_root: str | None = None
	url: str = ""

	def as_dict(self) -> dict[str, object]:
		"""The wire shape. Keys match what the existing chip renderer already reads
		(``label``, ``url``) plus the two it receives today and discards (``kind``,
		``subtitle``), so a manifest-backed chip row needs no new contract."""
		return {
			"ref": self.ref,
			"room": self.room,
			"message": self.message,
			"label": self.label,
			"kind": self.kind,
			"subtitle": self.subtitle,
			"url": self.url,
		}


def build_manifest(entries: list[dict[str, object]]) -> list[Citation]:
	"""Number the assembled sources in **assembly order**, 1-based.

	Assembly order rather than rank order, and the distinction matters: the model sees the
	context lines in assembly order, so a manifest numbered by rank would have the model
	citing ``[[ref:1]]`` for the third thing it read. Numbering what it actually sees is what
	makes the reference resolvable at all.

	Refs are **1-based** because they appear in prose. ``[[ref:0]]`` reads as a bug to a human
	and models trained on human text produce 1-based references whatever the prompt says.
	"""
	manifest: list[Citation] = []
	for index, entry in enumerate(entries, start=1):
		room = str(entry.get("room") or "")
		message = entry.get("message")
		manifest.append(
			Citation(
				ref=index,
				room=room,
				message=str(message) if message else None,
				label=str(entry.get("label") or f"message in {room}"),
				kind=str(entry.get("kind") or "chunk"),
				subtitle=str(entry.get("subtitle") or ""),
				thread_root=str(entry.get("thread_root")) if entry.get("thread_root") else None,
				url=str(entry.get("url") or ""),
			)
		)
	return manifest


def resolve_urls(manifest: list[Citation]) -> list[Citation]:
	"""Fill each entry's ``url`` from its room and message, server-side.

	Uses the same ``links`` helper as the SPA router and the notification deep link — one
	function, three consumers, which is why it was written in Phase 1 rather than at the first
	place that needed it. A fourth route table here would diverge from the other three, and
	the symptom would be citations that 404 for reasons nobody can reproduce from the answer.

	Called from a request context because the absolute origin comes from Frappe's own site-URL
	helper rather than from a settings field — a settings copy of the site host is wrong on
	exactly the day somebody changes the host.
	"""
	from erpnext_enhancements.chat.links import build_message_deep_link

	resolved: list[Citation] = []
	for entry in manifest:
		if entry.url:
			resolved.append(entry)
			continue
		try:
			url = build_message_deep_link(entry.room, entry.message, entry.thread_root)
		except Exception:
			# A citation with no URL renders as inert text rather than a link. Losing a link
			# is a cosmetic failure; raising here would lose the whole answer.
			url = ""
		resolved.append(
			Citation(
				ref=entry.ref,
				room=entry.room,
				message=entry.message,
				label=entry.label,
				kind=entry.kind,
				subtitle=entry.subtitle,
				thread_root=entry.thread_root,
				url=url,
			)
		)
	return resolved


def relative_route(room: str, message: str | None = None, thread: str | None = None) -> str:
	"""The in-app route, for a client that would rather not follow an absolute URL.

	Bench-free, unlike :func:`resolve_urls` — this is the half of the route table that needs
	no site origin, which is what lets the route shape be asserted on both sides without a
	bench.
	"""
	return build_chat_route(room, message, thread)


def context_line(citation: Citation, author: str, timestamp: str, body: str) -> str:
	"""One labelled line of context, as the model sees it.

	The marker in the *context* uses mathematical white brackets while the marker the model is
	asked to *emit* uses double square brackets. Two different shapes on purpose: it makes
	"the model echoed the label it was shown" distinguishable from "the model produced a
	citation", which is the difference between working and appearing to work.
	"""
	return CONTEXT_LINE_TEMPLATE.format(
		ref=citation.ref,
		author=author or "unknown",
		timestamp=timestamp or "unknown time",
		body=body or "",
	)


def cited_refs(text: str) -> list[int]:
	"""Every ref the model emitted, in order of first appearance, de-duplicated."""
	seen: set[int] = set()
	order: list[int] = []
	for match in REF_PATTERN.finditer(text or ""):
		ref = int(match.group(1))
		if ref in seen:
			continue
		seen.add(ref)
		order.append(ref)
	return order


def split_known_refs(text: str, manifest: list[Citation]) -> tuple[list[int], list[int]]:
	"""``(resolvable, missing)`` — the second list is the ``citation_miss_count``."""
	known = {entry.ref for entry in manifest}
	resolvable: list[int] = []
	missing: list[int] = []
	for ref in cited_refs(text):
		(resolvable if ref in known else missing).append(ref)
	return resolvable, missing


def strip_unknown_refs(text: str, manifest: list[Citation]) -> str:
	"""Remove markers that resolve to nothing, leaving the prose intact.

	Adjacent whitespace is collapsed so a removed marker does not leave a double space
	mid-sentence — the point of dropping silently is that the reader cannot tell, and a
	visible gap tells them.
	"""
	known = {entry.ref for entry in manifest}

	def _replace(match: re.Match[str]) -> str:
		return "" if int(match.group(1)) not in known else match.group(0)

	cleaned = REF_PATTERN.sub(_replace, text or "")
	return re.sub(r"[ \t]{2,}", " ", cleaned)
