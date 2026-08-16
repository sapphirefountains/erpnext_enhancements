"""The server-side URL boundary: the predicate, the scrubber, and the frame reassembler.

Bench-free and frappe-free on purpose. ``utils/url_safety.py`` and ``utils/sse_filter.py`` import
nothing but the standard library precisely so they can be called from inside the SSE hot loop, and
this suite asserts that importability rather than assuming it — a later ``import frappe`` added for
one log line would make the filter unusable in the place it exists for, and would otherwise be
noticed only in production.

The corpus below is the ``expect_py`` half of ``scripts/fixtures/url_safety_corpus.json``, which
``scripts/test_chat_citations.mjs`` reads for the client half. One file, two languages, so a row
cannot be "fixed" on one side to match an implementation that is wrong.
"""

import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
CORPUS = REPO / "scripts" / "fixtures" / "url_safety_corpus.json"

sys.path.insert(0, str(REPO))

from erpnext_enhancements.utils.sse_filter import (
	FRAME_SEP,
	filter_sse_stream,
	scrub_sse_frame,
)
from erpnext_enhancements.utils.url_safety import (
	MAX_URL_LENGTH,
	blank_unsafe_url,
	is_safe_url,
	scrub_urls,
)


def _rows():
	data = json.loads(CORPUS.read_text(encoding="utf-8"))
	return data["rows"]


def _decode(row):
	"""Rows store inputs as CODEPOINT ARRAYS, never as JSON strings.

	The corpus is made of exactly the characters that get mangled in transit — backslash, tab,
	NUL, U+FEFF. A JSON string round-trips them through whatever quoting the reader used to
	create the file, and this repo has already been bitten twice by a shell eating a backslash
	in a test fixture. An integer array cannot be misquoted.
	"""
	return "".join(chr(c) for c in row["codepoints"])


# --------------------------------------------------------------------- the predicate


def test_the_module_imports_without_frappe():
	"""No ``frappe`` at import time, ever.

	This is a design requirement and not a happy accident: the predicate runs inside the SSE
	relay's per-frame loop, and it is called from a bench-free test. An added ``import frappe``
	would break both, and would look harmless in review.
	"""
	for name in ("url_safety.py", "sse_filter.py"):
		source = (REPO / "erpnext_enhancements" / "utils" / name).read_text(encoding="utf-8")
		assert "import frappe" not in source, f"{name} must stay frappe-free"
	# And the import above this line already succeeded, which is the other half of the claim:
	# these modules load with no bench, no site and no stub installed.
	assert callable(is_safe_url)


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["id"])
def test_corpus(row):
	expected = row["expect_py"] if row.get("expect_py") is not None else row["expect"]
	assert is_safe_url(_decode(row)) is expected, row["why"]


def test_the_corpus_asserts_the_soundness_invariant_is_unwritable():
	"""``expect_py`` may only ever make the server STRICTER, never looser.

	A row saying "the client refuses this and the server allows it" is the vulnerability this
	whole module exists to prevent, so it is rejected as a schema error rather than left to be
	caught by a test somebody could later re-baseline.
	"""
	for row in _rows():
		if row.get("expect_py") is None:
			continue
		assert row["expect_py"] is False, (
			f"{row['id']}: expect_py may only be false. A row asserting the server accepts what "
			"the client refuses would invert the boundary."
		)
		assert row["expect"] is True, f"{row['id']}: expect_py is redundant unless expect is true"


def test_the_protocol_relative_escapes_that_started_this():
	"""The v1.282.3 inputs, on the authoritative side.

	``/\\evil.example`` is why this module does not use ``urllib.parse``: ``urlsplit`` leaves
	``netloc`` empty and calls it a path, every browser resolves it to ``http://evil.example/``.
	"""
	for value in ("//evil.example", "/\\evil.example", "/\\/evil.example", "/\\", "//"):
		assert is_safe_url(value) is False, value


def test_scheme_allowlist():
	for value in (
		"javascript:alert(1)",
		"JaVaScRiPt:alert(1)",
		"data:text/html,<script>x</script>",
		"vbscript:x",
		"file:///etc/passwd",
		"blob:https://a/b",
		"mailto:a@b.c",
	):
		assert is_safe_url(value) is False, value


def test_userinfo_is_unrepresentable_rather_than_rejected():
	"""``@`` is absent from the host character class, so credentials cannot parse at all."""
	assert is_safe_url("https://evil.example@good.example/") is False
	assert is_safe_url("https://user:pw@ok.example/") is False


def test_real_urls_are_accepted():
	"""Over-refusal is the chosen trade, so the false-refusal rate has to be checked, not assumed."""
	for value in (
		"/app/project/PRJ-00580",
		"/app/chat?room=abc123&seq=42",
		"/app/customer/Acme%20Fountains%20LLC",
		"/",
		"https://erp.sapphirefountains.com/app/project/PRJ-00580",
		"https://docs.google.com/document/d/1a2b3c/edit#heading=h.x",
		"https://drive.google.com/file/d/1AbC-dEf_gh/view?usp=sharing",
		"http://localhost:8000/app",
		"https://sub.domain.co.uk/a/b/c?x=1&y=2#frag",
	):
		assert is_safe_url(value) is True, value


def test_bounds_and_junk():
	assert is_safe_url(None) is False
	assert is_safe_url("") is False
	assert is_safe_url("   ") is False
	assert is_safe_url(12345) is False
	assert is_safe_url("https://ok.example/" + "a" * MAX_URL_LENGTH) is False


# --------------------------------------------------------------------- the scrubber


def test_scrub_walks_by_key_not_by_path():
	"""``ui_metadata.sources[].url`` is the same array under a different frame type.

	An enumeration of known field paths would have covered ``sources`` and missed this one.
	"""
	payload = {
		"type": "done",
		"ui_metadata": {
			"sources": [
				{"label": "ok", "url": "/app/task/T-1"},
				{"label": "bad", "url": "javascript:alert(1)"},
			],
			"citations": [{"k": 1, "url": "//evil.example"}],
		},
	}
	scrubbed, blanked = scrub_urls(payload)
	assert blanked == 2
	assert scrubbed["ui_metadata"]["sources"][0]["url"] == "/app/task/T-1"
	assert scrubbed["ui_metadata"]["sources"][1]["url"] == ""
	assert scrubbed["ui_metadata"]["citations"][0]["url"] == ""
	# The label survives: the chip stays listed and hoverable, it just stops being clickable.
	assert scrubbed["ui_metadata"]["sources"][1]["label"] == "bad"


def test_scrub_reports_zero_when_nothing_changed():
	"""The count is what lets the relay emit ORIGINAL bytes, so a false non-zero costs fidelity."""
	payload = {"type": "text", "content": "no urls here at all"}
	scrubbed, blanked = scrub_urls(payload)
	assert blanked == 0
	assert scrubbed == payload


def test_scrub_is_depth_limited():
	deep = {"url": "javascript:alert(1)"}
	for _ in range(60):
		deep = {"nested": deep}
	scrubbed, blanked = scrub_urls(deep)
	assert isinstance(scrubbed, dict)
	assert blanked == 0  # beyond the limit it is left alone rather than recursing forever


# --------------------------------------------------------------------- the reassembler


def _identity(frame):
	return frame


def test_frames_split_at_every_byte_offset_reassemble_identically():
	"""The property the whole stream design rests on, asserted exhaustively rather than sampled."""
	stream = (
		b'data: {"type":"text","content":"hello"}\n\n'
		b': ping\n\n'
		b'data: {"type":"done","ui_metadata":{}}\n\n'
	)
	for cut in range(len(stream) + 1):
		chunks = [stream[:cut], stream[cut:]]
		out = b"".join(filter_sse_stream(chunks, _identity))
		assert out == stream, f"split at {cut}"


def test_multibyte_utf8_survives_a_split_mid_character():
	"""0x0A cannot appear inside a multi-byte sequence, so a frame cut is character-safe."""
	stream = 'data: {"type":"text","content":"café 🎉 日本"}\n\n'.encode()
	for cut in range(len(stream) + 1):
		out = b"".join(filter_sse_stream([stream[:cut], stream[cut:]], _identity))
		assert out == stream, f"split at {cut}"


def test_several_frames_in_one_chunk():
	stream = b"data: 1\n\ndata: 2\n\ndata: 3\n\n"
	assert b"".join(filter_sse_stream([stream], _identity)) == stream


def test_a_trailing_partial_frame_is_still_emitted():
	"""A stream that ends without a terminator must not swallow the user's last bytes."""
	assert b"".join(filter_sse_stream([b"data: x\n\ndata: unterminated"], _identity)) == (
		b"data: x\n\ndata: unterminated"
	)


def test_a_transform_that_raises_degrades_to_passthrough():
	"""The one unacceptable outcome is a truncated answer, so an exception yields the original."""

	def boom(frame):
		raise RuntimeError("nope")

	stream = b"data: 1\n\ndata: 2\n\n"
	assert b"".join(filter_sse_stream([stream], boom)) == stream


def test_overflow_flushes_verbatim_rather_than_buffering_forever():
	seen = {}
	blob = b"x" * 5000
	out = b"".join(
		filter_sse_stream(
			[blob, blob],
			_identity,
			max_buffer=1000,
			on_overflow=lambda n: seen.setdefault("n", n),
		)
	)
	assert out == blob + blob
	assert seen["n"] >= 1000


#: A frame whose bytes any re-serialisation would silently change: doubled spaces, a float that
#: ``json.dumps`` renders differently, non-ASCII that ``ensure_ascii`` would escape, and key order
#: that is not alphabetical. Every earlier version of these tests used tidy ASCII fixtures, and a
#: sabotage that mangled bytes passed all of them — a byte-identity test whose fixture has no
#: interesting bytes asserts nothing.
_AWKWARD = (
	b'data: {"type": "text",  "z": 1, "a": 2, "n": 1.50, "content": "caf\xc3\xa9 \xf0\x9f\x8e\x89"}'
)


def test_an_unchanged_frame_is_emitted_byte_for_byte():
	"""The single property that makes filtering a live stream safe.

	Not "semantically equal" — *identical*. The relay must be indistinguishable from the old
	byte pass-through on every frame it does not need to touch, because the failure mode of a
	rewritten frame is a corrupted answer that raises nothing anywhere.
	"""
	assert scrub_sse_frame(_AWKWARD) is _AWKWARD


def test_the_whole_stream_is_byte_identical_when_no_url_is_unsafe():
	stream = _AWKWARD + FRAME_SEP + b': ping' + FRAME_SEP + b'data: {"type":"done"}' + FRAME_SEP
	assert b"".join(filter_sse_stream([stream], scrub_sse_frame)) == stream


def test_only_the_offending_url_is_touched():
	frame = b'data: {"type":"sources","content":[{"label":"a","url":"javascript:alert(1)"}]}'
	out = scrub_sse_frame(frame)
	assert out != frame
	assert b"javascript" not in out
	assert b'"label": "a"' in out or b'"label":"a"' in out


def test_non_json_and_comment_frames_pass_through_untouched():
	for frame in (b": ping", b"event: message", b"id: 42", b"data: not json at all", b"data:"):
		assert scrub_sse_frame(frame) is frame


def test_transform_actually_runs_on_whole_frames_only():
	seen = []

	def record(frame):
		seen.append(frame)
		return frame

	list(filter_sse_stream([b"data: ab", b"cd\n\ndata: e", b"f\n\n"], record))
	assert seen == [b"data: abcd", b"data: ef"]
	assert FRAME_SEP == b"\n\n"
