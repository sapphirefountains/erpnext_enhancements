# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Where a chunk ends. **Pure — stdlib only, and deterministic by construction.**

Chunking is the one indexing decision that cannot be fixed later without re-embedding
everything, so it is a pure function of the message stream and nothing else. Feed the same
messages twice and you get byte-identical chunks, which is what makes re-running the indexer
idempotent rather than merely harmless.

--------------------------------------------------------------------------------------
Five boundaries, and one of them is the cost decision
--------------------------------------------------------------------------------------

A chunk seals at whichever comes first:

1. **size** — the token budget for one chunk;
2. **count** — a message ceiling, so a room of one-word replies still chunks;
3. **a gap** — a silence long enough that the next message is a new conversation. Two hours
   apart is two conversations even when it is the same two people;
4. **a thread boundary** — a reply belongs with its thread, not with whatever preceded it in
   ``seq`` order;
5. **an idle tail** — the open end of a room, quiet for long enough that nothing more is
   coming.

**Rule 5 is the largest cost lever in the design, and the tail it leaves unsealed is
deliberately excluded from the semantic index.** A reviewer will challenge that, so the
argument is here rather than in a commit message: sealing and embedding on every message means
one embedding call per chat message, which dominates the running cost of the whole feature.
What covers the gap instead:

* the tail of the room the question was asked in arrives **verbatim** in the thread tier, so
  the case that matters most loses nothing;
* other rooms' tails are reachable through the **lexical** tier, which needs no embedding;
* the next digest pass folds them in within minutes.

The residual is "a semantic match in another room's last few minutes", bounded by the digest
cadence, at a cost of one embedding call per message to close. That is the trade, stated
rather than assumed.

--------------------------------------------------------------------------------------
Ordering is ``seq``, never a timestamp
--------------------------------------------------------------------------------------

``seq`` is immutable once assigned and is never renumbered. The gap rule reads timestamps, but
only to measure a distance between two messages the ``seq`` order already fixed — it never
decides *order*. Sorting a mixed-origin transcript by ``creation`` (site-local) or by an origin
timestamp (UTC) is wrong by the site offset, always in the same direction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: The defaults, mirroring the shipped ``Chat Settings`` values so this module is usable — and
#: testable — without one.
DEFAULT_SEAL_TOKENS: int = 1_200
DEFAULT_SEAL_MESSAGES: int = 20
DEFAULT_GAP_MINUTES: int = 45
DEFAULT_IDLE_TAIL_MINUTES: int = 30

#: Boundary reasons, recorded on the sealed chunk so a human debugging retrieval can see *why*
#: a chunk ended where it did. A chunker whose decisions cannot be explained afterwards is one
#: nobody can tune.
REASON_TOKENS: str = "tokens"
REASON_MESSAGES: str = "messages"
REASON_GAP: str = "gap"
REASON_THREAD: str = "thread"
REASON_IDLE: str = "idle"
REASON_END: str = "end-of-stream"


@dataclass(frozen=True)
class Message:
	"""The subset of a message chunking needs. Deliberately not a Frappe document.

	``minutes_since_previous`` is computed by the caller, not here, for the same reason the
	ranker takes an age rather than a timestamp: a clock read inside a pure function makes two
	identical runs differ.
	"""

	name: str
	seq: int
	sender: str
	text: str
	thread_root: str | None = None
	minutes_since_previous: float = 0.0
	tokens: int = 0


@dataclass
class Chunk:
	"""One sealed (or open) run of messages."""

	messages: list[Message] = field(default_factory=list)
	sealed: bool = False
	reason: str = ""

	@property
	def first_seq(self) -> int:
		return self.messages[0].seq if self.messages else 0

	@property
	def last_seq(self) -> int:
		return self.messages[-1].seq if self.messages else 0

	@property
	def tokens(self) -> int:
		return sum(message.tokens for message in self.messages)

	@property
	def thread_root(self) -> str | None:
		return self.messages[0].thread_root if self.messages else None

	def participants(self) -> list[str]:
		"""Sorted and de-duplicated. Sorted because it is hashed downstream, and an
		unordered iteration makes a content hash flap for no reason."""
		return sorted({message.sender for message in self.messages if message.sender})


def chunk_messages(
	messages: list[Message],
	*,
	seal_tokens: int = DEFAULT_SEAL_TOKENS,
	seal_messages: int = DEFAULT_SEAL_MESSAGES,
	gap_minutes: int = DEFAULT_GAP_MINUTES,
	idle_tail_minutes: int = DEFAULT_IDLE_TAIL_MINUTES,
	tail_idle_minutes: float | None = None,
) -> list[Chunk]:
	"""Split a room's messages into chunks. The last one may be **open**.

	Args:
		messages: in ``seq`` order, oldest first. Order is trusted rather than re-sorted —
			a chunker that quietly reorders its input hides an upstream bug that would
			otherwise show up as a chunk spanning a gap it should have sealed on.
		tail_idle_minutes: how long the final message has been the final message. Supplied by
			the caller because it is a clock read. ``None`` means "unknown", which leaves the
			tail **open** — the safe direction: an unsealed tail costs a little recall, a
			prematurely sealed one costs a re-chunk and a wasted embedding.

	Returns:
		Chunks in order. Every one but possibly the last has ``sealed = True`` and a
		``reason``.
	"""
	chunks: list[Chunk] = []
	current = Chunk()

	for message in messages:
		if current.messages:
			reason = _boundary_before(
				current,
				message,
				seal_tokens=seal_tokens,
				seal_messages=seal_messages,
				gap_minutes=gap_minutes,
			)
			if reason:
				current.sealed = True
				current.reason = reason
				chunks.append(current)
				current = Chunk()
		current.messages.append(message)

	if current.messages:
		if tail_idle_minutes is not None and tail_idle_minutes >= idle_tail_minutes:
			current.sealed = True
			current.reason = REASON_IDLE
		chunks.append(current)

	return chunks


def _boundary_before(
	current: Chunk,
	message: Message,
	*,
	seal_tokens: int,
	seal_messages: int,
	gap_minutes: int,
) -> str:
	"""Does ``message`` start a new chunk? Returns the reason, or ``""``.

	**Checked in a fixed order**, and the order is part of the contract: when a message would
	trip two rules at once the recorded reason must be the same on every run, or two identical
	streams produce chunks that differ only in a column somebody will eventually key a cache
	on. Thread first, because it is the only structural one — the others are thresholds.
	"""
	if (message.thread_root or None) != (current.thread_root or None):
		return REASON_THREAD
	if message.minutes_since_previous >= gap_minutes > 0:
		return REASON_GAP
	if seal_messages > 0 and len(current.messages) >= seal_messages:
		return REASON_MESSAGES
	if seal_tokens > 0 and current.tokens + message.tokens > seal_tokens and current.messages:
		return REASON_TOKENS
	return ""


def render_body(chunk: Chunk, *, timestamps: dict[str, str] | None = None) -> str:
	"""The text stored on the chunk and handed to the embedding model.

	One line per message, ``author: text``, in ``seq`` order. The author is included because
	"who said it" is half of what a chat question is usually about — *"did Sam agree to
	that"* is unanswerable from bodies alone — and because it is what the citation line shows.

	Deterministic: no ``now()``, no dict iteration order, nothing that varies between two runs
	over the same messages. That is what makes ``content_hash`` a usable identity.
	"""
	stamps = timestamps or {}
	lines: list[str] = []
	for message in chunk.messages:
		stamp = stamps.get(message.name)
		prefix = f"{message.sender}" + (f" ({stamp})" if stamp else "")
		lines.append(f"{prefix}: {message.text}")
	return "\n".join(lines)
