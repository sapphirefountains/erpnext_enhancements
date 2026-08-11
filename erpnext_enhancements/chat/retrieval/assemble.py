# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""S0–S5 assembly, in prompt-cache order. **Pure — no ``frappe``, no clock, no randomness.**

Assembly is a **pure function of its arguments**, and that is the invariant rather than a
style preference. For a fixed user and a fixed watermark, the concatenation of the stable
segments must be **byte-identical** across two assemblies unless a named trigger has fired.

--------------------------------------------------------------------------------------
The segments, ordered by non-decreasing volatility
--------------------------------------------------------------------------------------

| Segment | Content | What invalidates it |
|---|---|---|
| S0 | system prompt, persona, tool definitions, citation instructions | a deploy |
| S1 | org glossary | a fixture edit |
| S2 | the asking user's profile card | that user's record |
| S3 | T0 — pinned document card and stable room digests, tagged `digest_version` | a digest republish |
| S4 | T2 + T3 retrieved chunks, in rank order | every turn |
| S5 | T1 current thread verbatim, then the live question | always |

**Rule 1 — no clock read above S5.** *"A single volatile character at the front invalidates
the entire prefix and silently costs the full discount on every request, with no error and no
log line."* There is no ``now()`` in this module, and there is no timestamp parameter that
defaults to one. Every age this assembly needs was computed by the caller and passed in.

**Rule 2 — the prefix's *identity* must be stable, which is not the same as its size.** The
deployment uses **explicit** context caching keyed by a hash over
``{system, history, tools fingerprint, catalog}``, not implicit prefix caching with a token
threshold. So sizing the prefix against a 4,096-token minimum would be a test against a
mechanism this deployment does not use. What actually breaks the cache here is the **toolset
fingerprint** changing — a tool appearing, vanishing or changing shape is baked into the cache
identity by design — which is why :func:`toolset_fingerprint` exists and is asserted stable.

Two facts about which turns are cached at all, worth knowing before adding a surface: any turn
with attachments or with search/maps/code-execution enabled runs **uncached** by deliberate
upstream design, and **the first turn of a session is never cached**. A chat-context addition
that only ever appears on a first turn buys nothing.

--------------------------------------------------------------------------------------
What this module does not do
--------------------------------------------------------------------------------------

It does not fetch, rank, budget or count tokens. It receives already-fetched rows, already
ranked, already fitted by :mod:`chat.retrieval.budget`, and it concatenates them in the one
order that is allowed. Keeping it that narrow is what makes "assembly order is an invariant" a
testable statement rather than an aspiration.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

SEGMENT_S0: str = "S0"
SEGMENT_S1: str = "S1"
SEGMENT_S2: str = "S2"
SEGMENT_S3: str = "S3"
SEGMENT_S4: str = "S4"
SEGMENT_S5: str = "S5"

#: The one permitted order. Asserted by test against this tuple rather than against a literal
#: list in the test, so a reordering has to be a deliberate edit here.
SEGMENT_ORDER: tuple[str, ...] = (
	SEGMENT_S0,
	SEGMENT_S1,
	SEGMENT_S2,
	SEGMENT_S3,
	SEGMENT_S4,
	SEGMENT_S5,
)

#: Segments that must be byte-stable between two assemblies with unchanged inputs. S4 and S5
#: are excluded because they are *supposed* to change every turn — a test that demanded
#: stability of them would fail on correct behaviour.
STABLE_SEGMENTS: tuple[str, ...] = (SEGMENT_S0, SEGMENT_S1, SEGMENT_S2, SEGMENT_S3)

#: The instruction that turns the truncation flag into something the user actually learns.
#: Without it the flag is an internal metric and the person asking gets a confident answer
#: from a cut view with no signal at all.
TRUNCATION_NOTICE: str = (
	"NOTE FOR THE ASSISTANT: your view of this conversation was truncated to fit a size "
	"limit, so some earlier or cross-room context is missing. Say so plainly in your reply "
	"— one short sentence — and answer from what you have. Do not guess at what was cut."
)

#: The separator inside the toolset fingerprint. A named constant rather than a literal
#: because it is part of a hash's identity: changing it silently invalidates every cached
#: prefix at once, which presents as a cost spike with no code change to point at.
_TOOL_SEPARATOR: str = "\x1f"

CITATION_INSTRUCTION: str = (
	"Cite the numbered context lines you actually used, inline, as [[ref:N]] where N is the "
	"number shown in the line's ⟦ref:N⟧ prefix. Cite only numbers that appear above; never "
	"invent one, and never write a URL."
)


@dataclass
class Segment:
	name: str
	lines: list[str] = field(default_factory=list)

	def text(self) -> str:
		return "\n".join(line for line in self.lines if line)


@dataclass
class Assembly:
	segments: list[Segment] = field(default_factory=list)
	context_truncated: bool = False

	def stable_prefix(self) -> str:
		"""S0–S3 concatenated. The bytes the cache identity is computed over."""
		return "\n\n".join(segment.text() for segment in self.segments if segment.name in STABLE_SEGMENTS)

	def volatile_suffix(self) -> str:
		return "\n\n".join(segment.text() for segment in self.segments if segment.name not in STABLE_SEGMENTS)

	def text(self) -> str:
		return "\n\n".join(segment.text() for segment in self.segments if segment.text())

	def segment(self, name: str) -> Segment | None:
		for segment in self.segments:
			if segment.name == name:
				return segment
		return None


def toolset_fingerprint(tool_names: list[str]) -> str:
	"""A stable hash over the tool names available this turn.

	**Sorted**, because an unordered iteration would make the fingerprint flap and force a
	cache rebuild on every single turn — the exact failure this whole ordering discipline
	exists to avoid, arriving through the back door.

	This is what ``T-14`` asserts. It deliberately does **not** count tokens against an
	implicit-caching threshold: this deployment uses explicit caching, where size is
	irrelevant and identity is everything.
	"""
	joined = _TOOL_SEPARATOR.join(sorted({name for name in tool_names if name}))
	return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def assemble(
	*,
	system_prompt: str,
	glossary_lines: list[str],
	user_card_lines: list[str],
	t0_lines: list[str],
	t2_t3_lines: list[str],
	thread_lines: list[str],
	question: str,
	context_truncated: bool = False,
	include_citation_instruction: bool = True,
) -> Assembly:
	"""Build the assembly. Keyword-only, and every input already resolved.

	Keyword-only because seven ordered lists of strings is seven chances to pass the thread
	where the glossary goes — and the result of that mistake is not an error, it is a working
	assembly with the volatile content at the front and the prompt cache silently disabled.

	``context_truncated`` prepends :data:`TRUNCATION_NOTICE` to **S5**, not to S0. Putting it
	in the stable prefix would change the cached prefix on exactly the turns that are already
	the most expensive, which is the worst possible place for it — and it is a fact about
	*this* turn, so it belongs on the volatile turn regardless.
	"""
	s0_lines = [system_prompt]
	if include_citation_instruction:
		s0_lines.append(CITATION_INSTRUCTION)

	s5_lines: list[str] = []
	if context_truncated:
		s5_lines.append(TRUNCATION_NOTICE)
	s5_lines.extend(thread_lines)
	if question:
		s5_lines.append(f"USER QUERY: {question}")

	segments = [
		Segment(SEGMENT_S0, s0_lines),
		Segment(SEGMENT_S1, list(glossary_lines)),
		Segment(SEGMENT_S2, list(user_card_lines)),
		Segment(SEGMENT_S3, list(t0_lines)),
		Segment(SEGMENT_S4, list(t2_t3_lines)),
		Segment(SEGMENT_S5, s5_lines),
	]
	return Assembly(segments=segments, context_truncated=context_truncated)


def context_cache_key(
	*,
	user: str,
	room: str,
	watermark: tuple[int, int, str],
	budget_fingerprint: str,
) -> str:
	"""``triton:ctx:{user}:{room}:{watermark}:{budget}`` — the assembled-context cache key.

	The **three-value watermark** is in the key, which is what makes an edit or a delete
	invalidate it rather than merely age it out. A key carrying only ``max(seq)`` is unchanged
	by a delete, so the cached context — containing the message the user just deleted — is
	served again until the TTL happens to roll.

	Namespaced with a distinct prefix because ``redis_cache`` and ``redis_socketio`` are the
	**same instance on the same database** in this deployment, so an under-namespaced key is a
	collision with the socket server rather than merely untidy.
	"""
	seq, count, modified = watermark
	return f"triton:ctx:{user}:{room}:{seq}-{count}-{modified}:{budget_fingerprint}"


def digest_cache_key(room: str, digest_version: int) -> str:
	"""``digest_version`` in the key is what makes a republish actually invalidate: without
	it a regenerated summary inherits the key of the one it replaced."""
	return f"triton:digest:{room}:{digest_version}"


def query_embedding_cache_key(query_hash: str, model: str, dim: int) -> str:
	"""Model and dimension are both in the key. A vector from another model or another
	dimension is not a cache hit, it is a number with no meaning that looks like one."""
	return f"triton:emb:q:{query_hash}:{model}:{dim}"
