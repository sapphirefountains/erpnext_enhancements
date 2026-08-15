# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The assistant's own answers never become indexed content. Bench-free, AST.

TASK-2026-01569. The failure this closes is not obvious from any one file, which is why it
survived four phases:

`chat/invoke/handler.py` posts Triton's reply as a **real `Chat Message`** through the ordinary
outbox, allocating a fresh `seq` off `Chat Room.seq_high_water`. Its text is composed from
exactly the chunk bodies and digest summaries retrieval handed it. And the indexing layer did
not distinguish it — so the reply was chunked verbatim, embedded, shipped to the model
provider, and summarised into the room digest.

**Every retention mechanism available is a floor keyed on `seq`.** The reply is written at the
*top* of the room, above every floor, and `retention._plan_rooms` ages each message by its own
`creation`. So asking about an old conversation restarted the retention clock on its
substance — and restarted it again every time anybody asked again. Purge a room's 2024
conversation and, if anyone asked about it in 2025, the substance survived four ways.

--------------------------------------------------------------------------------------
Why the fix is narrow, and what the narrowness costs
--------------------------------------------------------------------------------------

Three tiers read three different sources, which is what makes a narrow answer possible at all:

* **T0 room digest** — `digest._messages_for_digest`. Excluded. A digest over the assistant's
  answers is a summary of a summary.
* **T2 cross-room chunks** — `indexer._messages_after`. Excluded. This is the durable copy: a
  chunk is verbatim, and it makes the restatement retrievable from *other* rooms forever.
* **T1 thread** — `retrieval/gate._thread_messages`, which reads `Chat Message` **directly**.
  Untouched, deliberately. A follow-up in the same thread still sees the earlier answer, so the
  exclusion costs nothing in conversational continuity.

The cost is real but bounded and worth naming: Triton can no longer *retrieve* its own past
answers across rooms or from a room digest. It can still see them in the thread it said them
in, which is where a follow-up actually asks.
"""

import ast
import pathlib
import re
import unittest

_CHAT = pathlib.Path(__file__).resolve().parents[1] / "chat"
INDEXER = _CHAT / "indexing" / "indexer.py"
DIGEST = _CHAT / "indexing" / "digest.py"
GATE = _CHAT / "retrieval" / "gate.py"
HANDLER = _CHAT / "invoke" / "handler.py"

_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


def _docstring_ids(tree: ast.AST) -> set[int]:
	out: set[int] = set()
	for node in ast.walk(tree):
		if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			body = getattr(node, "body", None)
			if not body:
				continue
			first = body[0]
			if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
				if isinstance(first.value.value, str):
					out.add(id(first.value))
	return out


def _code(path: pathlib.Path) -> str:
	"""Comment- and docstring-stripped source, with the SQL left alone.

	**Docstrings removed by AST, not by regex**, and the first version of this file is why. A
	regex over triple-quoted strings eats the f-string SQL bodies too — so it stripped the very
	``sender_kind`` clauses it was asserting on, and then failed for the opposite of the real
	reason. Leaving docstrings in is not an option either: every file here explains the
	exclusion at length, so a raw-text assertion would pass on the prose.
	"""
	text = path.read_text(encoding="utf-8")
	tree = ast.parse(text)
	skip = _docstring_ids(tree)
	out = text
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) in skip:
			segment = ast.get_source_segment(text, node)
			if segment:
				out = out.replace(segment, "", 1)
	return _LINE.sub("", out)


def _func_src(path: pathlib.Path, name: str) -> str:
	text = path.read_text(encoding="utf-8")
	for node in ast.walk(ast.parse(text)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return ast.get_source_segment(text, node) or ""
	raise AssertionError(f"{name}() not found in {path.name}")


class TheScanActuallyScansTest(unittest.TestCase):
	def test_stripping_removes_prose_and_keeps_sql(self):
		stripped = _code(INDEXER)
		self.assertIn("sender_kind", stripped)
		self.assertNotIn("retention clock", stripped)

	def test_the_reader_finds_the_functions(self):
		for path, name in (
			(INDEXER, "_messages_after"),
			(DIGEST, "_messages_for_digest"),
			(GATE, "_thread_messages"),
		):
			with self.subTest(fn=name):
				self.assertGreater(len(_func_src(path, name)), 200)


class ExcludedFromTheDurableCopiesTest(unittest.TestCase):
	"""The two sources that produce a copy outliving the messages it quotes."""

	def test_the_chunk_source_excludes_the_assistant(self):
		src = _func_src(INDEXER, "_messages_after")
		self.assertIn("sender_kind", src)
		self.assertIn("assistant", src)

	def test_the_room_digest_source_excludes_the_assistant(self):
		src = _func_src(DIGEST, "_messages_for_digest")
		self.assertIn("sender_kind", src)
		self.assertIn("assistant", src)

	def test_both_use_one_shared_constant(self):
		"""Two spellings of "what an assistant reply is" would drift, and the drift would be
		invisible: one tier would quietly resume indexing the answers."""
		self.assertIn("ASSISTANT_SENDER_KIND = ", _code(INDEXER))
		self.assertIn("ASSISTANT_SENDER_KIND", _code(DIGEST))
		self.assertIn("from erpnext_enhancements.chat.indexing.indexer import", _code(DIGEST))

	def test_the_constant_matches_what_the_handler_writes(self):
		"""The whole exclusion turns on one string agreeing across two packages. If the handler
		ever wrote a different `sender_kind`, every filter here would silently match nothing."""
		handler = _code(HANDLER)
		self.assertIn('sender_kind = "Triton"', handler.replace("doc.", ""))
		self.assertIn('ASSISTANT_SENDER_KIND = "Triton"', _code(INDEXER))

	def test_the_filter_is_null_safe(self):
		"""`sender_kind` defaults to Human but pre-existing rows may hold NULL, and in SQL
		`NULL != 'Triton'` is NULL, not true — an unguarded comparison would silently drop
		every legacy message from the index rather than only the assistant's."""
		for path, name in ((INDEXER, "_messages_after"), (DIGEST, "_messages_for_digest")):
			with self.subTest(fn=name):
				self.assertIn("coalesce(`sender_kind`", _func_src(path, name))


class ThreadTierIsUntouchedTest(unittest.TestCase):
	"""The narrowness, asserted — otherwise a later tidy-up "completes" the exclusion and
	quietly costs Triton the ability to follow up on its own answer."""

	def test_the_thread_tier_does_not_exclude_the_assistant(self):
		src = _func_src(GATE, "_thread_messages")
		self.assertNotIn("sender_kind", src)

	def test_the_thread_tier_reads_messages_rather_than_chunks(self):
		"""The fact that makes the narrow answer possible at all. If this tier ever started
		reading chunks, excluding the assistant from chunking WOULD cost thread continuity,
		and this exclusion would need revisiting."""
		src = _func_src(GATE, "_thread_messages")
		self.assertIn("_MESSAGE_TABLE", src)
		self.assertNotIn("_CHUNK_TABLE", src)


class TheRestatementIsStillNotRetentionSafeTest(unittest.TestCase):
	"""What this does NOT fix, asserted so a green run is not over-read.

	The assistant's reply is still a `Chat Message` in the room, above every retention floor,
	with its own creation date. This change stops it being *re-indexed* — it does not stop it
	*existing*. A purge still leaves the reply itself behind, and only time ages it out.
	"""

	def test_the_handler_still_writes_a_real_message(self):
		self.assertIn("new_doc", _code(HANDLER))

	def test_the_limit_is_recorded_where_retention_is_decided(self):
		rules = _CHAT / "governance" / "purge_rules.py"
		text = rules.read_text(encoding="utf-8")
		self.assertIn("BLOCKED", text)


if __name__ == "__main__":
	unittest.main()
