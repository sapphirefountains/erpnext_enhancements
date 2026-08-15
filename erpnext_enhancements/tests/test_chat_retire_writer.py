# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The retirement writer. Bench-free, AST — the rows themselves need a database.

`chat/indexing/retire.py` is the only thing that moves `Chat Room.retired_below_seq`. Its
arithmetic is executed by `test_chat_retire_rules`; this asserts the properties that make it
safe to have at all.

--------------------------------------------------------------------------------------
The refusal is the whole safety argument
--------------------------------------------------------------------------------------

The mark means *"every message at or below this seq is gone forever"*, and every consumer acts
on that claim: the chunk sweep never reads below it, the digest sweep drops the room, the gate
stops serving anything covering it.

**Set it over messages that still exist and you have not retired anything** — you have made a
stretch of live, readable conversation permanently invisible to the assistant. Nobody reports
that as a bug. They report that Triton "doesn't know about" a conversation, and the cause is a
column nobody thinks to look at.

So the order is forced by code rather than by documentation: **destroy first, then retire.**
There is deliberately no argument that bypasses the check, because the case for skipping it is
always *"I know they are gone"* — which is exactly the belief the check exists to test.
"""

import ast
import pathlib
import re
import unittest

_CHAT = pathlib.Path(__file__).resolve().parents[1] / "chat"
RETIRE = _CHAT / "indexing" / "retire.py"
HOOKS = _CHAT.parent / "hooks.py"

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


def _func(name):
	text = RETIRE.read_text(encoding="utf-8")
	for node in ast.walk(ast.parse(text)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found")


def _src(name):
	return ast.get_source_segment(RETIRE.read_text(encoding="utf-8"), _func(name)) or ""


def _body(name):
	"""A function's source **without its docstring**.

	Every docstring in this module explains, at length, the exact thing its assertion is
	looking for — ``is_archived``, ``is_deleted``, ``last_seq``. Asserting over raw source
	matches the explanation and passes for the wrong reason, or fails for it. That mistake has
	now been made eight times across this series, which is why the helper exists rather than
	the discipline.
	"""
	text = RETIRE.read_text(encoding="utf-8")
	node = _func(name)
	src = ast.get_source_segment(text, node) or ""
	body = node.body
	if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
		doc = ast.get_source_segment(text, body[0].value)
		if doc:
			src = src.replace(doc, "", 1)
	return _LINE.sub("", src)


def _calls(node):
	out = []
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			f = inner.func
			out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
	return out


class TheScanActuallyScansTest(unittest.TestCase):
	def test_it_reads_the_module(self):
		self.assertGreater(len(_code(RETIRE)), 1500)
		self.assertIn("set_retirement_mark", _code(RETIRE))


class TheRefusalTest(unittest.TestCase):
	"""The property the module exists for."""

	def test_the_writer_refuses_when_messages_are_still_present(self):
		src = _src("plan_retirement")
		self.assertIn("_messages_at_or_below", src)
		self.assertIn("refusal", src)

	def test_the_write_path_raises_on_a_refusal_rather_than_returning_it(self):
		"""A caller that ignored a returned refusal would have destroyed messages and then
		silently not retired their coverage — the worst of both."""
		fn = _func("set_retirement_mark")
		raises = [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]
		self.assertTrue(raises, "set_retirement_mark does not raise on refusal")

	def test_the_refusal_is_checked_before_anything_is_written(self):
		src = _src("set_retirement_mark")
		self.assertLess(src.index("refusal"), src.index("set_value"))

	def test_there_is_no_argument_that_bypasses_the_check(self):
		"""The case for a `force=` is always "I know they are gone", which is the belief the
		check tests. Its absence is the design, so it is asserted."""
		for name in ("plan_retirement", "set_retirement_mark"):
			args = {a.arg for a in _func(name).args.args} | {a.arg for a in _func(name).args.kwonlyargs}
			with self.subTest(fn=name):
				self.assertFalse(args & {"force", "skip_checks", "ignore_messages", "unsafe"})

	def test_existence_is_the_test_rather_than_is_deleted(self):
		"""A tombstoned message is still a row holding its text — divergence D4 is explicit
		that the body stays, and an oversight expansion can still reveal it. Counting only
		live rows would let the mark be set over a room full of tombstones, which is the
		opposite of retired."""
		src = _body("_messages_at_or_below")
		self.assertNotIn("is_deleted", src)
		self.assertIn("count", src)


class DeletionTest(unittest.TestCase):
	def test_chunks_are_deleted_by_first_seq(self):
		"""Equivalent to `last_seq` for a mark this module snapped; NOT equivalent for one set
		by hand, where `last_seq` leaves the straddling chunk holding the retired transcript
		verbatim — unreachable forever, because the mark can never be lowered to re-snap it."""
		src = _body("_delete_chunks")
		self.assertIn("first_seq", src)
		self.assertNotIn("last_seq", src)

	def test_it_deletes_only_derived_coverage(self):
		"""Messages, revisions and attachments are the purge's business and are classified in
		purge_rules. A retirement that also deleted them would be a purge nobody reviewed."""
		code = _code(RETIRE)
		for name in ("_delete_chunks", "_delete_room_digests", "_delete_thread_digests"):
			self.assertIn(name, code)
		for forbidden in ("Chat Message Revision", "Chat Attachment"):
			self.assertNotIn(forbidden, code)

	def test_it_never_deletes_a_message(self):
		code = _code(RETIRE)
		self.assertNotIn("delete(MESSAGE_DOCTYPE", code)
		self.assertNotIn("delete_doc", code)

	def test_the_snap_happens_in_the_writer_rather_than_the_caller(self):
		"""A purge that snapped in its own code and then deleted up to the UNSNAPPED seq would
		destroy exactly the messages the snap was protecting."""
		self.assertIn("snap_to_chunk_boundary", _calls(_func("plan_retirement")))


class SweepTest(unittest.TestCase):
	def test_the_sweep_never_advances_a_mark(self):
		"""It finishes deletions an already-written mark authorises. If it could also move the
		mark it would be an unattended job that retires conversation on a timer."""
		src = _body("sweep_retirement")
		self.assertNotIn("set_value", src)
		self.assertNotIn("snap_to_chunk_boundary", src)

	def test_the_sweep_carries_no_archived_filter(self):
		"""The one sweep in this package without one, deliberately: the chunk sweep and the
		digest sweep both open `where is_archived = 0`, so archiving a room is otherwise the
		single action that makes its retired coverage permanent."""
		src = _body("sweep_retirement")
		self.assertNotIn("is_archived", src)

	def test_the_sweep_is_registered_and_the_writer_is_not(self):
		"""The category has to be honest in both directions: a scheduled `set_retirement_mark`
		would be a job that retires conversation unattended."""
		hooks = HOOKS.read_text(encoding="utf-8")
		self.assertIn("chat.indexing.retire.sweep_retirement", hooks)
		self.assertNotIn("chat.indexing.retire.set_retirement_mark", hooks)

	def test_the_sweep_never_raises(self):
		fn = _func("sweep_retirement")
		handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
		self.assertTrue(handlers)
		for handler in handlers:
			self.assertFalse([n for n in ast.walk(handler) if isinstance(n, ast.Raise)])


class SurfaceTest(unittest.TestCase):
	def test_there_is_no_http_endpoint(self):
		for node in ast.walk(ast.parse(RETIRE.read_text(encoding="utf-8"))):
			if isinstance(node, ast.FunctionDef):
				for dec in node.decorator_list:
					target = dec.func if isinstance(dec, ast.Call) else dec
					self.assertNotEqual(getattr(target, "attr", ""), "whitelist", node.name)

	def test_it_records_the_run(self):
		"""`retention_run` with `mode: retire` — the same event type the planner writes,
		because a retirement IS a retention run: it is the half that destroys derived
		coverage. A second event type would split one question across two vocabularies."""
		self.assertIn("record_governance_event", _calls(_func("_record")))
		self.assertIn("retention_run", _src("_record"))

	def test_the_audit_detail_carries_no_content(self):
		src = _body("_record")
		for forbidden in ("text", "body", "summary_text"):
			self.assertNotIn(f'"{forbidden}"', src)


if __name__ == "__main__":
	unittest.main()
