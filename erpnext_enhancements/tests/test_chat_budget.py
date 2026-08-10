"""Bench-free tests for the 32,000-byte message budget.

Subject: ``erpnext_enhancements.chat.sync.budget``, standard library only, therefore
runnable in the only CI tier this repo protects automatically (``CLAUDE.md``: no Frappe
integration-test job).

Byte arithmetic over Unicode is the classic "works on my ASCII test data" defect. The
production symptom is a 400 from Google on one message out of a thousand — the one with an
emoji at the wrong offset — arriving as a ``Dead`` relay job hours later with a coworker
insisting they sent something. So the cases below are chosen for where the bugs actually
are rather than for coverage:

* a **4-byte emoji** — the only common character where a naive one-byte-per-character
  assumption is off by four, and the one that lands astride a byte cut;
* a **CJK string** — three bytes per character, so a 32,000-character message is 96,000
  bytes and ``len(text)`` passes it;
* a **combining sequence** — decodable after a cut on a codepoint boundary and still
  *wrong*, because the accent has been separated from its letter;
* **exactly at the limit** — the boundary is where an off-by-one lives, and the ``<=``
  versus ``<`` in the fit check is the whole function;
* **the suffix reservation** — reserving after truncating instead of before is how you
  produce a payload of ``limit + len(suffix)`` bytes, i.e. a 400 on precisely the messages
  that were already too long.

Plain pytest functions, so this file needs its **own**
``python -m pytest erpnext_enhancements/tests/test_chat_budget.py -q`` step in CI;
``python -m unittest`` collects nothing from a file shaped like this and reports success.
"""

from __future__ import annotations

import ast
import pathlib

from erpnext_enhancements.chat.sync import budget

APP_DIR: pathlib.Path = pathlib.Path(__file__).resolve().parents[1]
BUDGET_PY: pathlib.Path = APP_DIR / "chat" / "sync" / "budget.py"

#: U+1F600 GRINNING FACE. Four UTF-8 bytes, one codepoint, and the reason
#: ``text[:n]`` and ``text.encode()[:n]`` are different functions.
EMOJI: str = "\U0001f600"

#: "Chinese characters" — three UTF-8 bytes each.
CJK: str = "漢字"

#: "e" + COMBINING ACUTE ACCENT: two codepoints, three bytes, one grapheme. Cutting
#: between them is legal UTF-8 and still turns "café" into "cafe".
COMBINING: str = "e\u0301"

#: MAN + ZWJ + WOMAN. A split renders as two unrelated people instead of one sequence.
ZWJ_SEQUENCE: str = "\U0001f468\u200d\U0001f469"

LINK: str = " … (truncated, full message in ERPNext: /app/chat-message/abc123)"


def _fits(body: str, limit: int) -> bool:
	return len(body.encode("utf-8")) <= limit


# --- the module stays in the bench-free tier -----------------------------------


def test_budget_imports_nothing_outside_the_standard_library() -> None:
	"""``unicodedata`` is stdlib and is the only non-trivial import here. ``frappe`` or
	``requests`` appearing would delete this whole suite from CI silently."""
	tree = ast.parse(BUDGET_PY.read_text(encoding="utf-8"), filename=str(BUDGET_PY))
	roots = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			roots.update(alias.name.split(".")[0] for alias in node.names)
		elif isinstance(node, ast.ImportFrom) and node.level == 0:
			roots.add((node.module or "").split(".")[0])
	assert roots <= {
		"__future__",
		"unicodedata",
		"typing",
	}, f"{BUDGET_PY.name} imports {sorted(roots)}; standard library only"


# --- the counter ----------------------------------------------------------------


def test_the_limit_is_the_published_thirty_two_thousand() -> None:
	"""*"The maximum message size, including the message contents, is 32,000 bytes."*"""
	assert budget.MESSAGE_BYTE_LIMIT_DEFAULT == 32_000


def test_bytes_are_counted_not_characters() -> None:
	assert budget.message_payload_bytes("abc") == 3
	assert budget.message_payload_bytes(EMOJI) == 4
	assert budget.message_payload_bytes(CJK) == 6
	assert budget.message_payload_bytes(COMBINING) == 3


def test_a_cjk_message_that_len_would_pass_is_over_the_limit() -> None:
	"""The concrete reason ``len(text)`` is the wrong number: 32,000 CJK characters are
	96,000 bytes, and a length check would relay all of them into a 400."""
	text = "漢" * 32_000
	assert len(text) == 32_000
	assert budget.message_payload_bytes(text) == 96_000


def test_the_envelope_is_added_because_the_limit_covers_the_whole_resource() -> None:
	"""32,000 bytes is the message *resource*, not the ``text`` field. The caller estimates
	the envelope; this module refuses to guess (too small is a 400, too large silently
	truncates messages that would have fitted)."""
	assert budget.message_payload_bytes("abc", extra_bytes=100) == 103


def test_a_negative_envelope_estimate_cannot_understate_the_payload() -> None:
	assert budget.message_payload_bytes("abc", extra_bytes=-500) == 3


def test_a_missing_body_counts_as_empty_rather_than_raising() -> None:
	"""House rule: ``getattr(obj, "field", None) or ""``. ``doc_events`` fire during
	ERPNext's own test bootstrap, before this app's custom fields exist, so a ``None`` here
	must not turn a fresh-database install into a crash."""
	assert budget.message_payload_bytes(None) == 0  # type: ignore[arg-type]
	assert budget.message_payload_bytes("") == 0


# --- fitting: the easy direction -------------------------------------------------


def test_a_short_message_is_returned_untouched_and_unsuffixed() -> None:
	"""No link on a message that fitted: a marker on every message is noise, and noise is
	what makes a real truncation marker invisible."""
	assert budget.fit_to_byte_budget("hello", 100, suffix=LINK) == ("hello", False)


def test_exactly_at_the_limit_is_not_truncated() -> None:
	"""The boundary, as its own test, so the comparison cannot be "tidied" from ``<=`` to
	``<``. A message that is exactly 32,000 bytes is legal; Google says *maximum*."""
	text = "a" * 100
	assert budget.fit_to_byte_budget(text, 100, suffix=LINK) == (text, False)


def test_one_byte_over_the_limit_is_truncated() -> None:
	body, truncated = budget.fit_to_byte_budget("a" * 101, 100, suffix=LINK)
	assert truncated is True
	assert _fits(body, 100)


def test_a_multibyte_string_exactly_at_the_limit_is_not_truncated() -> None:
	"""The same boundary reached in bytes rather than characters — 25 emoji, 100 bytes."""
	text = EMOJI * 25
	assert budget.message_payload_bytes(text) == 100
	assert budget.fit_to_byte_budget(text, 100, suffix=LINK) == (text, False)


def test_an_empty_body_is_never_truncated() -> None:
	assert budget.fit_to_byte_budget("", 0, suffix=LINK) == ("", False)


# --- fitting: the truncating direction -------------------------------------------


def test_the_suffix_is_reserved_inside_the_limit() -> None:
	"""The single most valuable assertion in this file.

	Appending the marker *after* cutting to the limit produces ``limit + len(suffix)``
	bytes, which is a 400 from Google on exactly the messages that were already too long —
	the ones nobody tests with, because they only appear in production.
	"""
	body, truncated = budget.fit_to_byte_budget("a" * 500, 200, suffix=LINK)
	assert truncated is True
	assert body.endswith(LINK)
	assert _fits(body, 200)
	assert len(body.encode("utf-8")) == 200


def test_the_kept_text_is_a_prefix_of_the_original() -> None:
	"""Truncation, not rewriting. If the result is not a prefix, something reflowed the
	body and the reader in Chat is being shown words in an order nobody typed."""
	text = "the quick brown fox jumps over the lazy dog " * 10
	body, truncated = budget.fit_to_byte_budget(text, 120, suffix=LINK)
	assert truncated is True
	kept = body[: -len(LINK)]
	assert text.startswith(kept)


def test_a_four_byte_emoji_is_never_split_mid_codepoint() -> None:
	"""Sweep the cut across every byte offset of an all-emoji string.

	A mid-codepoint cut does not raise anywhere near the cut — it produces bytes that fail
	to decode later, most likely inside ``json.dumps`` in the transport, with a traceback
	pointing at the HTTP layer rather than at the truncation.
	"""
	text = EMOJI * 40  # 160 bytes
	for limit in range(1, 200):
		body, _ = budget.fit_to_byte_budget(text, limit, suffix="")
		assert _fits(body, limit), (limit, len(body.encode("utf-8")))
		# Round-trips, and contains only whole emoji.
		assert body.encode("utf-8").decode("utf-8") == body
		assert set(body) <= {EMOJI}


def test_a_cjk_body_is_cut_on_a_character_boundary() -> None:
	text = "漢字文化" * 20  # 4 chars x 3 bytes x 20 = 240 bytes
	for limit in range(1, 250):
		body, _ = budget.fit_to_byte_budget(text, limit, suffix="")
		assert _fits(body, limit)
		assert text.startswith(body)


def test_a_combining_accent_is_not_separated_from_its_letter() -> None:
	"""Cutting between "e" and its combining acute is valid UTF-8 and still wrong: the
	mirror in Chat would read "cafe" where ERPNext says "café". Cheap to avoid, so avoided."""
	text = "caf" + COMBINING + "s"
	# 6 bytes: c a f e U+0301(2 bytes) s. A 4-byte budget lands between "e" and the accent.
	body, truncated = budget.fit_to_byte_budget(text, 4, suffix="")
	assert truncated is True
	assert body == "caf", f"the accent was separated from its letter: {body!r}"


def test_a_complete_combining_sequence_survives_a_cut_after_it() -> None:
	"""The other half of the rule: only a *split* cluster is walked back. A cut that lands
	cleanly after the accent must keep it, or every truncation would silently lose a letter."""
	text = "caf" + COMBINING + "xyz"
	body, truncated = budget.fit_to_byte_budget(text, 6, suffix="")
	assert truncated is True
	assert body == "caf" + COMBINING


def test_a_zwj_emoji_sequence_is_not_left_joining_nothing() -> None:
	"""A trailing zero-width joiner renders as a stray glyph or an unexpected pairing."""
	text = ZWJ_SEQUENCE + "tail"
	for limit in range(1, len(text.encode("utf-8")) + 1):
		body, _ = budget.fit_to_byte_budget(text, limit, suffix="")
		assert not body.endswith("\u200d"), (limit, repr(body))
		assert _fits(body, limit)


def test_a_variation_selector_is_kept_when_its_cluster_is_complete() -> None:
	"""The mirror image of the ZWJ rule. Stripping a trailing VS-16 would quietly change an
	emoji-presentation glyph into a text-presentation one for no reason at all."""
	text = "\u2714\ufe0f" + "zzzz"
	body, truncated = budget.fit_to_byte_budget(text, 6, suffix="")
	assert truncated is True
	assert body == "\u2714\ufe0f"


# --- the degenerate configurations ------------------------------------------------


def test_a_suffix_that_does_not_fit_is_dropped_rather_than_truncated() -> None:
	"""A truncated URL is not a shorter URL; it is dead, or worse, a working link somewhere
	else. The body fragment is kept and ``was_truncated`` still says what happened."""
	body, truncated = budget.fit_to_byte_budget("a" * 100, 10, suffix=LINK)
	assert truncated is True
	assert LINK not in body
	assert body == "a" * 10


def test_a_suffix_exactly_as_long_as_the_limit_is_also_dropped() -> None:
	"""Equal is not enough: a suffix filling the whole budget leaves zero bytes of body,
	which is a link with nothing to explain it."""
	body, truncated = budget.fit_to_byte_budget("a" * 100, len(LINK), suffix=LINK)
	assert truncated is True
	assert body == "a" * len(LINK)


def test_a_zero_or_negative_limit_yields_an_empty_body_rather_than_raising() -> None:
	"""A degenerate settings value, not an exception. This runs inside a background job,
	and raising there is how a message vanishes with an empty Error Log."""
	assert budget.fit_to_byte_budget("hello", 0, suffix=LINK) == ("", True)
	assert budget.fit_to_byte_budget("hello", -1, suffix=LINK) == ("", True)


def test_a_missing_suffix_is_treated_as_empty() -> None:
	body, truncated = budget.fit_to_byte_budget("a" * 50, 10, suffix=None)  # type: ignore[arg-type]
	assert (body, truncated) == ("a" * 10, True)


# --- the invariant, swept ---------------------------------------------------------


def test_the_result_never_exceeds_the_limit_for_any_mixture_or_budget() -> None:
	"""The one invariant that must hold for every input: the returned body encodes within
	the limit. Swept over a deliberately nasty string at every budget from 0 to past its
	length, with and without a suffix — this is the assertion that would have caught every
	individual bug above even if the case had not been thought of.
	"""
	text = f"ascii {EMOJI} {CJK} {COMBINING} {ZWJ_SEQUENCE} \u2714\ufe0f end"
	size = len(text.encode("utf-8"))
	for suffix in ("", "…", LINK):
		for limit in range(0, size + 5):
			body, truncated = budget.fit_to_byte_budget(text, limit, suffix=suffix)
			assert _fits(body, limit) or limit <= 0, (limit, suffix, body)
			assert body.encode("utf-8").decode("utf-8") == body
			assert truncated == (budget.message_payload_bytes(text) > limit)
