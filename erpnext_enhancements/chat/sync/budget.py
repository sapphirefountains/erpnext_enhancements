# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Google Chat's 32,000-byte message ceiling — **pure functions, no I/O, no Frappe**.

Standard library only, so this lives in the bench-free CI tier (``CLAUDE.md``: there is no
Frappe integration-test job, so anything needing a bench is verified by a human or not at
all). Byte arithmetic is exactly the kind of code that is cheap to test and expensive to
get wrong in production, where the symptom is a 400 from Google on one unlucky message
containing an emoji.

--------------------------------------------------------------------------------------
The limit is the whole message resource, not ``len(text)``
--------------------------------------------------------------------------------------

Google's wording is *"The maximum message size, including the message contents, is 32,000
bytes"* (``PHASE2_VERIFIED.md`` §4). It is a byte count over the **encoded resource**, so
three separate things make ``len(text)`` the wrong number:

1. **Bytes, not characters.** UTF-8 gives 1 byte for ASCII, 2 for most Latin accents and
   Cyrillic, 3 for CJK, and 4 for emoji above the BMP. A 32,000-*character* CJK message is
   96,000 bytes. ``len()`` would pass it.
2. **The envelope counts.** Field names, quoting, the space name, ``clientAssignedMessageId``
   and any attachment references are part of the resource. The caller knows what it is
   about to send, so it passes its own estimate as ``extra_bytes`` — this module refuses to
   guess, because a guess that is too small is a 400 and a guess that is too large silently
   truncates messages that would have fitted.
3. **JSON escaping.** A newline is one character and two bytes on the wire (``\\n``); a
   control character can become six (``\\u0001``). Ordinary prose is unaffected, so this is
   folded into ``extra_bytes`` rather than modelled: modelling it exactly would mean
   serialising the payload here, which is the caller's job and not a pure one.

--------------------------------------------------------------------------------------
Oversized messages are truncated for the relay, never rejected at compose time
--------------------------------------------------------------------------------------

The full body always stays on the ``Chat Message`` row — ERPNext is the source of truth —
and only the *relayed copy* is cut, with a deep link back so the Chat reader can reach the
whole thing. ``Chat Message.truncated_for_relay`` records that it happened.

Rejecting at compose time was considered and refused: it would let Google's limit dictate
what an employee may say inside ERPNext, which inverts decision #1 of ADR 0009 (ERPNext is
the system of record and Chat is the mirror). **Pending human confirmation at the Phase 2
checkpoint** — it is the only user-visible behaviour in this module.
"""

from __future__ import annotations

import unicodedata
from typing import Final

#: *"The maximum message size, including the message contents, is 32,000 bytes."*
#: The default only; a caller that knows its envelope subtracts from it before calling
#: :func:`fit_to_byte_budget`, and ``Chat Settings`` may lower it for a site that wants
#: shorter mirrors.
#:
#: VERIFY: **which** bytes Google counts — the raw UTF-8 of the resource's field values, or
#: the JSON-escaped wire form. No Chat page says. It matters only for bodies within a few
#: hundred bytes of the ceiling and only for text that escapes badly (control characters,
#: which prose does not contain), and the caller's ``extra_bytes`` estimate absorbs the
#: difference in the safe direction. Settle with one live round trip: post a message whose
#: escaped form crosses 32,000 while its raw form does not, and see whether it 400s. Blocks
#: nothing — an over-estimate truncates a message that would just have fitted.
#:
#: VERIFY: the truncation *policy* itself — relay a cut body plus a deep link, rather than
#: refusing the send — is the only user-visible behaviour in this module and is flagged for
#: the Phase 2 checkpoint. See the module docstring for the argument.
MESSAGE_BYTE_LIMIT_DEFAULT: Final[int] = 32_000

#: Zero-width joiner, written as an escape because an invisible literal in source is a
#: character nobody can review. Not a combining mark by ``unicodedata.combining`` — it is
#: category ``Cf`` with combining class 0 — so it has to be named explicitly, or a cut
#: immediately after it leaves a joiner joining nothing.
_ZWJ: Final[str] = "\u200d"

#: Variation selectors 15 and 16 (text vs emoji presentation). Same story as the ZWJ:
#: combining class 0, and meaningless once separated from the character they qualify.
_VARIATION_SELECTORS: Final[frozenset[str]] = frozenset({"\ufe0e", "\ufe0f"})

#: Unicode general categories that only ever *attach* to a preceding character.
_MARK_CATEGORIES: Final[frozenset[str]] = frozenset({"Mn", "Mc", "Me"})


def message_payload_bytes(text: str, *, extra_bytes: int = 0) -> int:
	"""UTF-8 bytes of the message as Google counts it — the whole resource, not just ``text``.

	``extra_bytes`` is the caller's estimate of the non-text envelope (see the module
	docstring). It is additive and unvalidated beyond a floor of zero: a negative estimate
	is meaningless and would understate the payload, which is the dangerous direction.

	``None`` and non-string input are coerced rather than rejected. This is called from the
	relay path on a custom field that ``doc_events`` can observe before it exists — the
	house rule is ``getattr(obj, "field", None) or ""`` — and raising here would turn a
	missing field into a failed insert during ERPNext's own test bootstrap.
	"""
	body = text or ""
	if not isinstance(body, str):
		body = str(body)
	return len(body.encode("utf-8")) + max(int(extra_bytes), 0)


def _truncate_to_bytes(text: str, budget: int) -> str:
	"""Longest prefix of ``text`` that encodes to at most ``budget`` UTF-8 bytes.

	Slice the encoded bytes, then decode and walk back one byte at a time until it
	succeeds. At most three iterations, because a UTF-8 sequence is at most four bytes, and
	the loop is *provably* correct in a way that ``errors="ignore"`` is not:
	``bytes.decode(errors="ignore")`` would also swallow a genuinely malformed sequence
	somewhere else in the string and silently shorten the output without saying so.
	"""
	if budget <= 0:
		return ""
	chunk = text.encode("utf-8")[:budget]
	while chunk:
		try:
			return chunk.decode("utf-8")
		except UnicodeDecodeError:
			chunk = chunk[:-1]
	return ""


def _is_cluster_continuation(char: str) -> bool:
	"""Does ``char`` only have meaning attached to the character before it?

	Combining marks (``unicodedata.combining`` non-zero, plus the ``Mn``/``Mc``/``Me``
	categories that a zero combining class still puts in a mark category), the zero-width
	joiner, and the variation selectors.
	"""
	if not char:
		return False
	if unicodedata.combining(char):
		return True
	if char == _ZWJ or char in _VARIATION_SELECTORS:
		return True
	return unicodedata.category(char) in _MARK_CATEGORIES


def _trim_broken_cluster(kept: str, next_char: str) -> str:
	"""Walk ``kept`` back off the middle of a grapheme cluster.

	Cutting on a codepoint boundary keeps the string *decodable*, which is the correctness
	requirement, but it can still cut ``e`` away from its combining acute or split a ZWJ
	emoji sequence in half — the reader sees a different word, or two unrelated emoji where
	there was one. Cheap to avoid, so avoided.

	The test for "the cut landed inside a cluster" is the **first dropped character**: if it
	only has meaning attached to what precedes it, the cluster was split, so the trailing
	partial cluster (its marks *and* the base character they attach to) comes off too.
	A dangling joiner at the end of ``kept`` is then removed with the character it was
	joining from, since it now joins nothing.

	**Deliberate limits.** This is an approximation of UAX-29, not an implementation: it
	handles combining marks, ZWJ sequences and variation selectors, and does not handle
	regional-indicator pairs (a split flag renders as two letters) or Indic conjuncts.
	Full grapheme segmentation needs the third-party ``regex`` module, and adding a
	dependency to keep a truncated flag emoji intact is not a trade worth making — the
	worst case here is cosmetic in the *mirror*, while the ERPNext row still holds the
	whole body.
	"""
	if not kept:
		return kept

	if _is_cluster_continuation(next_char):
		while kept and _is_cluster_continuation(kept[-1]):
			kept = kept[:-1]
		kept = kept[:-1]

	# A trailing ZWJ — and only a ZWJ — is always dangling: it joins *forwards*, and what it
	# was joining to has just been dropped. A trailing variation selector is the opposite
	# case, a complete two-codepoint cluster, and stripping it would silently change an
	# emoji-presentation glyph into a text-presentation one for no reason.
	while kept and kept[-1] == _ZWJ:
		kept = kept[:-2] if len(kept) >= 2 else ""

	return kept


def fit_to_byte_budget(text: str, limit: int, *, suffix: str) -> tuple[str, bool]:
	"""``(body, was_truncated)`` — a body guaranteed to encode within ``limit`` UTF-8 bytes.

	``suffix`` is the deep link back to ERPNext and is reserved **inside** the limit, not
	appended after it. That ordering is the entire point of the function: reserving
	afterwards is how you produce a payload that is ``limit + len(suffix)`` bytes and get a
	400 from Google on exactly the messages that were already too long — the ones nobody
	tests with.

	The contract, in order of precedence:

	* If the text already fits, it is returned **unchanged and without the suffix**. A link
	  on every message that happened to be short would be noise.
	* Otherwise the text is cut on a UTF-8 character boundary (never mid-codepoint) and,
	  where cheap, on a grapheme boundary too — see :func:`_trim_broken_cluster` — and the
	  suffix is appended. ``len(result.encode("utf-8")) <= limit`` is guaranteed by
	  construction, and asserted over a sweep of every limit in the byte-fuzz test.
	* **If the suffix alone does not fit inside the limit, the suffix is dropped** and the
	  text is cut to the whole limit. A truncated URL is not a shorter URL; it is either
	  dead or, worse, a working link to somewhere else. The caller can still tell
	  truncation happened, because ``was_truncated`` is ``True`` either way.
	* A limit of zero or less yields ``("", True)`` for any non-empty text. It is a
	  degenerate configuration, not an exception: this runs on the relay path, and raising
	  from inside a background job over a settings value is how a message disappears with
	  an empty Error Log.

	``limit`` is the budget for **this string**, so a caller that has an envelope to pay for
	passes ``MESSAGE_BYTE_LIMIT_DEFAULT - envelope_estimate`` rather than the raw ceiling;
	see :func:`message_payload_bytes`.
	"""
	body = text or ""
	if not isinstance(body, str):
		body = str(body)
	tail = suffix or ""
	if not isinstance(tail, str):
		tail = str(tail)

	safe_limit = int(limit)
	if message_payload_bytes(body) <= safe_limit:
		return body, False

	if safe_limit <= 0:
		return "", True

	suffix_bytes = len(tail.encode("utf-8"))
	if suffix_bytes >= safe_limit:
		# No room for the marker inside the budget. Keep the body fragment, drop the link.
		return _finish(body, safe_limit, ""), True

	return _finish(body, safe_limit - suffix_bytes, tail), True


def _finish(body: str, budget: int, tail: str) -> str:
	"""Cut ``body`` to ``budget`` bytes on a safe boundary and append ``tail``."""
	kept = _truncate_to_bytes(body, budget)
	next_char = body[len(kept) : len(kept) + 1]
	return _trim_broken_cluster(kept, next_char) + tail
