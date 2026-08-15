# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every consumer honours the retirement mark. Bench-free, source scan.

`chat/retire_rules.py` holds the arithmetic and is executed by its own suite. This asserts the
*wiring*: that each of the places able to read or recreate derived coverage actually consults
`Chat Room.retired_below_seq`, because a mark nothing reads is a column.

**The one that matters most is the watermark floor.** `indexer._rooms_needing_chunks` computes
its watermark as `max(last_seq)` over sealed chunks with no staleness filter, so deleting a
room's retired chunks would drop it — and the ten-minute sweep would then re-read every
surviving message above the hole and re-chunk it **verbatim**, with a fresh embedding. A purge
that tidied up after itself would manufacture new copies of the text it was destroying, once
every ten minutes, for as many nights as its batch cap took. That is the specific reason
`purge_rules` marked the three derived DocTypes BLOCKED, and the floor is what clears it.

The gate assertions are the correctness half: a retired chunk or digest must never be served,
and there are five separate queries that could serve one.
"""

import ast
import pathlib
import re
import unittest

_CHAT = pathlib.Path(__file__).resolve().parents[1] / "chat"
INDEXER = _CHAT / "indexing" / "indexer.py"
DIGEST = _CHAT / "indexing" / "digest.py"
GATE = _CHAT / "retrieval" / "gate.py"
ROOM_JSON = _CHAT / "doctype" / "chat_room" / "chat_room.json"
ROOM_PY = _CHAT / "doctype" / "chat_room" / "chat_room.py"
RETENTION = _CHAT / "governance" / "retention.py"

MARK = "retired_below_seq"
_LINE = re.compile(r"^\s*#.*$", re.MULTILINE)


def _docstring_ids(tree):
	out = set()
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


def _code(path):
	"""Comment- and docstring-stripped source, SQL intact.

	Docstrings removed by AST rather than regex: a regex over triple-quoted strings eats the
	f-string SQL bodies, which is precisely where every assertion here is looking.
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


def _func_src(path, name):
	text = path.read_text(encoding="utf-8")
	for node in ast.walk(ast.parse(text)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return ast.get_source_segment(text, node) or ""
	raise AssertionError(f"{name}() not found in {path.name}")


class TheScanActuallyScansTest(unittest.TestCase):
	def test_stripping_keeps_the_sql(self):
		self.assertIn(MARK, _code(INDEXER))
		self.assertNotIn("manufacture new copies", _code(INDEXER))


class FieldTest(unittest.TestCase):
	def test_the_field_exists_and_defaults_to_zero(self):
		"""`Chat Room` is a NORMAL doctype, so this default reaches every existing row in the
		one ALTER — the opposite of the Single behaviour. That is what makes the whole
		mechanism inert on every site until a purge moves it, with no backfill patch."""
		import json

		data = json.loads(ROOM_JSON.read_text(encoding="utf-8"))
		field = next((f for f in data["fields"] if f.get("fieldname") == MARK), None)
		self.assertIsNotNone(field, "retired_below_seq is missing from Chat Room")
		self.assertEqual(field.get("fieldtype"), "Int")
		self.assertEqual(str(field.get("default")), "0")
		self.assertEqual(field.get("read_only"), 1)

	def test_the_controller_refuses_to_lower_it(self):
		src = _code(ROOM_PY)
		self.assertIn("refuse_lowering", src)
		self.assertIn("retire_rules", src)

	def test_the_controller_refuses_to_exceed_the_high_water(self):
		"""Retiring past what was ever allocated would declare messages gone that never
		existed — and the watermark floor would then skip every message the room goes on to
		receive."""
		self.assertIn("seq_high_water", _func_src(ROOM_PY, "_refuse_backwards_retirement"))


class WatermarkFloorTest(unittest.TestCase):
	"""The reason the purge was blocked, and the fix for it."""

	def test_the_chunk_sweep_floors_its_watermark(self):
		src = _func_src(INDEXER, "_rooms_needing_chunks")
		self.assertIn("greatest(", src)
		self.assertIn(MARK, src)

	def test_the_floor_applies_to_the_selection_and_the_ordering_too(self):
		"""All three moved together on purpose: a room whose only lag is retired messages must
		fall OUT of the rotation, not be selected forever with nothing to do."""
		src = _func_src(INDEXER, "_rooms_needing_chunks")
		self.assertGreaterEqual(src.count("greatest("), 3)

	def test_the_message_source_is_unchanged_because_the_floor_arrives_in_the_watermark(self):
		"""`_messages_after` already reads `seq > watermark`; flooring the value it is given is
		what makes it honour the mark, and a second filter here would be a second place to
		forget."""
		src = _func_src(INDEXER, "_messages_after")
		self.assertIn("watermark", src)


class DigestTest(unittest.TestCase):
	def test_a_fully_retired_room_falls_out_of_the_dirty_sweep(self):
		"""Otherwise it is re-selected every five minutes forever: the source returns nothing,
		the rebuild returns before writing, `is_stale` stays 1 and `rebuild_failures` is never
		incremented, so it never poisons out either."""
		src = _func_src(DIGEST, "_dirty_rooms")
		self.assertIn(MARK, src)
		self.assertIn("seq_high_water", src)

	def test_the_digest_source_is_floored(self):
		self.assertIn(MARK, _func_src(DIGEST, "_messages_for_digest"))


class GateTest(unittest.TestCase):
	"""Retired content must never be served, and there are five queries that could serve it."""

	def test_the_chunk_fragment_is_declared_once(self):
		"""Five hand-written copies of a correctness filter is five chances to forget one, and
		the one forgotten serves the transcript of destroyed messages."""
		self.assertIn("_RETIRED_CHUNK_SQL = ", _code(GATE))

	def test_the_chunk_fragment_keys_on_first_seq(self):
		"""Equivalent to `last_seq` for a mark snapped to a chunk boundary; NOT equivalent for
		a mark set by hand, where `last_seq` would serve a chunk straddling the mark whose body
		is the retired transcript verbatim. See retire_rules.wholly_retired."""
		src = _code(GATE)
		fragment = src[src.index("_RETIRED_CHUNK_SQL = ") : src.index("_RETIRED_CHUNK_SQL = ") + 400]
		self.assertIn("first_seq", fragment)
		self.assertNotIn("last_seq", fragment)

	def test_all_three_chunk_queries_use_it(self):
		self.assertGreaterEqual(_code(GATE).count("{_RETIRED_CHUNK_SQL}"), 3)

	def test_both_digest_queries_use_their_fragment(self):
		src = _code(GATE)
		self.assertIn("{_RETIRED_ROOM_DIGEST_SQL}", src)
		self.assertIn("{_RETIRED_THREAD_DIGEST_SQL}", src)

	def test_the_digest_fragments_key_on_covered_from(self):
		"""A digest is a summary crossing time and topic boundaries, so ANY intersection with
		the retired range disqualifies it — there is no partial un-saying of a summary."""
		src = _code(GATE)
		self.assertIn("covered_from", src)

	def test_the_fragments_are_constants_rather_than_a_builder(self):
		"""Every private FUNCTION in the gate takes `allowed_rooms` as its required first
		positional, and `test_chat_gate_source_scan` enforces it. A fragment builder taking a
		table name first would have been the first exception to a rule worth keeping
		exceptionless."""
		self.assertNotIn("def _retired_digest_sql", _code(GATE))


class RetentionPlannerTest(unittest.TestCase):
	def test_the_planner_holds_messages_the_mark_does_not_cover(self):
		self.assertIn(MARK, _code(RETENTION))

	def test_the_hold_is_named(self):
		rules = (_CHAT / "governance" / "purge_rules.py").read_text(encoding="utf-8")
		self.assertIn("HOLD_NOT_RETIRED", rules)


class StillNotEnabledTest(unittest.TestCase):
	"""The mechanism exists; nothing sets it yet. Asserted so a green run is not over-read."""

	def test_nothing_writes_the_mark(self):
		"""There is no writer, deliberately — it arrives with the purge. Until then every
		predicate above is a no-op, which is the shipped state.

		**Write forms only.** The first version of this looked for the field name as a dict
		key and flagged `retention.py`, which builds it as a *fact* for the eligibility
		predicate — a read. A detector that cannot tell a read from a write would have to be
		silenced, and a silenced guard is worse than none.
		"""
		writers = []
		for path in _CHAT.rglob("*.py"):
			if path.name.startswith("test_"):
				continue
			text = path.read_text(encoding="utf-8")
			for node in ast.walk(ast.parse(text)):
				# `doc.retired_below_seq = ...`
				if isinstance(node, ast.Assign):
					for target in node.targets:
						if isinstance(target, ast.Attribute) and target.attr == MARK:
							writers.append(f"{path.name}: assignment")
				# `db.set_value(..., {"retired_below_seq": ...})` and friends
				if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {
					"set_value",
					"set_single_value",
				}:
					if MARK in (ast.get_source_segment(text, node) or ""):
						writers.append(f"{path.name}: set_value")
		self.assertFalse(writers, f"something now writes the mark: {writers}")

	def test_the_purge_is_still_refused(self):
		from erpnext_enhancements.chat.governance import purge_rules

		ok, why = purge_rules.can_enable()
		self.assertFalse(ok)
		self.assertTrue(why)


if __name__ == "__main__":
	unittest.main()
