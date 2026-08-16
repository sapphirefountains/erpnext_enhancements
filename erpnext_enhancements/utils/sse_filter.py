"""Reassemble an SSE byte stream into frames, let a caller inspect each, pass the rest through.

Used by ``triton_chat.stream_query``, which relays Triton's ``text/event-stream`` to the browser.
Until now that relay was ``for chunk in r.iter_content(): yield chunk`` — byte-for-byte, which is
safe by construction and inspects nothing.

--------------------------------------------------------------------------------------
Why a filter here is dangerous, and what makes it safe anyway
--------------------------------------------------------------------------------------

**A broken filter is worse than the bug it fixes**, and by a wide margin. The bug needs a hostile
tool result; a filter regression hits every turn. And it is *invisible*: the widget's frame handler
swallows ``JSON.parse`` failures, and the 200 and the headers have already gone out before the
first byte, so a byte-mangling filter presents as "the answer stopped mid-sentence" with no HTTP
error, no Error Log row and no console message.

So the design rule is the one that removes the whole class:

    **If the transform changed nothing, emit the ORIGINAL bytes — never a re-serialisation.**

In production that is essentially every frame. The relay therefore stays byte-identical to what it
is today except on the frames that were about to poison an ``href``. What is left to get right is
*reassembly*, which is pure, offline-testable at every byte offset, and already what the client
does to the same stream.

--------------------------------------------------------------------------------------
Multi-byte UTF-8, answered structurally
--------------------------------------------------------------------------------------

The filter never decodes a chunk — only a complete frame. And a complete frame can never end
mid-character: the delimiter is ``b"\\n\\n"``, and ``0x0A`` cannot appear inside a multi-byte UTF-8
sequence (continuation bytes are all ``>= 0x80``). So splitting on the delimiter is *always* a
character-safe cut, by the encoding's own design rather than by a length check.

That matters more than it looks. Today's frames happen to be pure ASCII, because the producer's
``json.dumps`` defaults to ``ensure_ascii=True`` — a kwarg in a different repo, not an invariant
this app controls. The moment somebody passes ``ensure_ascii=False``, a filter that had been
decoding chunks would start corrupting emoji, intermittently, under load.
"""

import json

from erpnext_enhancements.utils.url_safety import scrub_urls

FRAME_SEP = b"\n\n"

#: A single frame larger than this is treated as "not a frame" and passed through untouched. The
#: point is not to police the upstream; it is that an unbounded ``carry`` on a stream that never
#: emits a separator would grow until the worker died. 4 MiB is far above a legitimate ``done``
#: frame carrying a full answer plus its thinking trace.
MAX_BUFFER = 4 << 20


def filter_sse_stream(chunks, transform, *, max_buffer: int = MAX_BUFFER, on_overflow=None):
	"""Yield the stream, having passed each complete frame through ``transform``.

	``chunks`` is any iterable of ``bytes`` (``requests``' ``iter_content(chunk_size=None)`` yields
	one HTTP transfer-encoding chunk at a time — neither a line nor a frame, and any intermediary
	may split or coalesce at an arbitrary byte offset).

	``transform`` takes one frame's bytes **without** its separator and returns bytes. Returning
	the object it was given is the signal for "unchanged"; this function does not diff.

	One ``yield`` per input chunk, so HTTP chunk granularity — and therefore the browser's
	perception of streaming — is unchanged. Frames are never held back waiting for a later frame,
	so the added latency is exactly zero for a complete frame and at most one frame for a split
	one, which is the same wait the client already had.
	"""
	carry = bytearray()
	overflowed = False

	for chunk in chunks:
		if not chunk:
			continue
		carry += chunk
		out = bytearray()

		while True:
			cut = carry.find(FRAME_SEP)
			if cut < 0:
				break
			frame = bytes(carry[:cut])
			del carry[: cut + len(FRAME_SEP)]
			# `transform` is caller code and this is the hot loop of a live stream. It must not be
			# able to take the answer down: on any exception the ORIGINAL frame goes out, which
			# degrades to today's behaviour rather than to a truncated reply.
			try:
				result = transform(frame)
			except Exception:
				result = frame
			out += result if isinstance(result, bytes) else frame
			out += FRAME_SEP

		if len(carry) > max_buffer:
			# No separator in 4 MiB. Either the upstream is not sending SSE or something is very
			# wrong; flush verbatim and stop buffering for the rest of the stream, because the one
			# unacceptable outcome is swallowing the user's answer.
			if on_overflow is not None and not overflowed:
				try:
					on_overflow(len(carry))
				except Exception:
					pass
			overflowed = True
			out += bytes(carry)
			carry.clear()

		if out:
			yield bytes(out)

	# A stream that ends without a trailing separator still has bytes the client needs. Emitting
	# them unfiltered is deliberate: a final frame with no terminator is not a frame this filter
	# can reason about, and passing it through is what the old byte relay did.
	if carry:
		yield bytes(carry)


def scrub_sse_frame(frame: bytes) -> bytes:
	"""Blank unsafe URL values in one SSE frame, or return the frame's ORIGINAL bytes.

	Lives here rather than in ``triton_chat`` so it can be tested with no bench and no frappe
	stub. It is the piece where a mistake is silent — a frame this rewrites when it did not need
	to is a corrupted answer — so it is the piece that most needs to be cheap to test.

	Returning the input object unchanged is the load-bearing case, not the fallback: it is what
	happens to every frame carrying no URL (all the ``text`` frames, i.e. most of the stream) and
	to every frame whose URLs are already fine.

	**Never re-serialises a frame it did not change.** ``json.dumps`` preserves key order but
	still rewrites spacing, float formatting and non-ASCII escaping — three ways to corrupt an
	answer in exchange for no security at all.

	Anything that is not a parseable ``data:`` frame — an SSE comment (``: ping``), an
	``event:``/``id:`` line, a ``data:`` payload that is not JSON — passes through untouched.
	Fail-open is correct *here and only here*: this function's job is URL values, and a frame it
	cannot parse holds no URL value it could have blanked. Refusing to forward it would break the
	stream to protect nothing.
	"""
	if not frame.startswith(b"data:"):
		return frame
	body = frame[len(b"data:") :].strip()
	if not body:
		return frame
	try:
		payload = json.loads(body)
	except Exception:
		return frame
	scrubbed, blanked = scrub_urls(payload)
	if not blanked:
		return frame
	return b"data: " + json.dumps(scrubbed).encode()
