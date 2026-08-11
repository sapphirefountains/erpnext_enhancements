# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The invocation envelope. **Pure — stdlib only.** The contract everything else is measured against.

Locked decision #4 says a mention of ``@triton`` behaves identically whether it was typed in
the native Google Chat client or in the SPA. The way to *guarantee* that rather than hope for
it is structural: **the envelope carries no origin field, so the handler has nothing to branch
on.** Not "the handler must not branch on origin" — there is no origin there to branch on.

Origin is real and is recorded: the normalisers write it to ``Triton Invocation Log`` for
observability. It simply never reaches the handler, which is a different thing from never
being known.

--------------------------------------------------------------------------------------
Why frozen, and why a canonical form
--------------------------------------------------------------------------------------

``frozen=True`` because the envelope crosses a queue boundary: the webhook builds it, a
background job consumes it, and an envelope a worker could edit is one where "the same
question from both origins" stops being decidable.

:func:`canonical` exists for the byte-identity test. Two logically identical mentions from the
two clients must serialise to the **same bytes**, and asserting that on a canonical form is
stronger than asserting field-by-field equality — a field added later is covered without the
test being updated, which is exactly the case where a field-by-field test silently stops
proving anything.

--------------------------------------------------------------------------------------
``request_id`` is derived, never generated
--------------------------------------------------------------------------------------

It is a hash of the room, the message and the text. Two consequences, both wanted: a
redelivered interaction event produces **one** turn rather than two answers, and the whole
cost of a turn is one row rather than a set nobody can join.

Generating a UUID here would make idempotency a property of the transport — and the transport
is Google's interaction webhook, which is at-least-once by design.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

#: The envelope format. Bumped when a field is added or its meaning changes, so a job queued
#: before a deploy and run after one fails loudly rather than being interpreted under the new
#: rules. A deploy FLUSHDBs the queue Redis, so in practice this is belt and braces — but the
#: braces are cheap and the belt is somebody else's deploy script.
ENVELOPE_VERSION: int = 1

#: What the handler is told the mention was addressed to. Kept as a constant because it is
#: matched in three places (the mention picker, the write path, and here) and a literal in any
#: one of them is a rename waiting to break the other two silently.
TRITON_HANDLE: str = "triton"


@dataclass(frozen=True)
class Envelope:
	"""One ``@triton`` turn, as the handler sees it. **No origin field. Deliberately.**"""

	#: The person who typed it. Every tool call and every chat read in this turn runs as
	#: them — never as a service account, and never as the bot that posts the answer.
	user: str
	room: str
	#: The message containing the mention.
	message: str
	#: The question, with the ``@triton`` mention already removed.
	text: str
	#: The thread to reply in. ``None`` means the room's main timeline.
	thread_root: str | None = None
	#: Derived, not generated. See the module docstring.
	request_id: str = ""
	#: The message's own seq, for the audit range and for ordering.
	seq: int = 0
	version: int = ENVELOPE_VERSION
	#: Anything a normaliser wants to carry that the handler must not branch on — the Google
	#: space name, the client's locale. **Never read by the handler**, and asserted so.
	transport: dict[str, str] = field(default_factory=dict)

	def canonical(self) -> str:
		"""Stable bytes for the byte-identity test. ``transport`` is excluded.

		Excluded because it is precisely the part that legitimately differs between the two
		origins — a Google space name exists on one path and not the other. Including it would
		make the identity test impossible to satisfy and the natural fix would be to delete
		the test.
		"""
		payload = {key: value for key, value in asdict(self).items() if key != "transport"}
		return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

	def fingerprint(self) -> str:
		return hashlib.sha256(self.canonical().encode("utf-8")).hexdigest()[:32]

	def as_job_kwargs(self) -> dict[str, object]:
		"""The shape enqueued to a background worker.

		Plain JSON-safe values rather than the dataclass: ``frappe.enqueue`` pickles its
		kwargs, and a pickled dataclass is a class-definition dependency between the process
		that queued and the process that runs — which a deploy breaks in the one direction
		nobody tests.
		"""
		return asdict(self)


def from_job_kwargs(kwargs: dict[str, object]) -> Envelope:
	"""Rebuild an envelope on the worker side, refusing one from another format version."""
	version = int(kwargs.get("version") or 0)
	if version != ENVELOPE_VERSION:
		raise ValueError(
			f"invocation envelope version {version} is not {ENVELOPE_VERSION}. This job was "
			"queued by a different build. Refusing rather than interpreting it under the "
			"current rules, because the field that changed is the one nobody would notice."
		)
	return Envelope(
		user=str(kwargs.get("user") or ""),
		room=str(kwargs.get("room") or ""),
		message=str(kwargs.get("message") or ""),
		text=str(kwargs.get("text") or ""),
		thread_root=(str(kwargs["thread_root"]) if kwargs.get("thread_root") else None),
		request_id=str(kwargs.get("request_id") or ""),
		seq=int(kwargs.get("seq") or 0),
		version=version,
		transport=dict(kwargs.get("transport") or {}),  # type: ignore[arg-type]
	)


def derive_request_id(room: str, message: str, text: str) -> str:
	"""``sha256(room ‖ message ‖ text)``, truncated. The idempotency key for one turn.

	``text`` is in the hash as well as the identifiers because an **edited** mention is a new
	question. Keying on the message alone would mean editing ``@triton what is the status`` to
	``@triton what is the budget`` returns the first answer, from the cache, forever.
	"""
	seed = "\x1f".join((room or "", message or "", text or ""))
	return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48]


def strip_mention(text: str, handle: str = TRITON_HANDLE) -> str:
	"""Remove the ``@triton`` token, leaving the question.

	Only the **first** occurrence is removed, and only when it is a whole token. A coworker
	writing *"ask @triton about the @triton rollout"* means the second one literally, and a
	blanket replace would turn their question into nonsense.
	"""
	needle = f"@{handle}"
	lowered = (text or "").lower()
	index = lowered.find(needle)
	if index < 0:
		return (text or "").strip()
	end = index + len(needle)
	if end < len(text) and (text[end].isalnum() or text[end] == "_"):
		# `@tritonics` is not a mention of `@triton`.
		return (text or "").strip()

	before = text[:index].rstrip(" \t")
	after = text[end:].lstrip(" \t")
	if not before or not after:
		return (before or after).strip()
	# Rejoin without leaving the double space the removal would otherwise create, and
	# without inserting one before punctuation: "hey @triton, status?" should read as
	# "hey, status?" rather than "hey , status?". Cosmetic on its own — but this string is
	# what the model is asked, and it is also what appears in the audit row's query hash,
	# so two spellings of the same question would hash differently.
	separator = "" if after[0] in ",.;:!?)]}" else " "
	return f"{before}{separator}{after}".strip()
