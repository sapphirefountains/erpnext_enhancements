# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The retention planner cannot destroy anything. Bench-free, AST.

Phase 6 §4.F ships the eligibility rule, the survives-a-purge table and the ``retention_run``
writer, and **not** the purge. This file is what makes that structural rather than a promise
in a docstring: it asserts against the AST that no deletion primitive appears anywhere in
either module, and that the planner reads no message body.

**Why it matters more here than usual.** The destructive half was designed, reviewed by three
adversarial passes, and cut — so the obvious next commit is somebody adding the twenty lines
that "finish" it. This suite is what that commit collides with, and the collision message
names the blocker rather than the rule.

The scan is docstring-stripped, because both modules discuss deletion at length: the whole
point of them is explaining what a purge *would* do.
"""

import ast
import pathlib
import unittest

_GOV = pathlib.Path(__file__).resolve().parents[1] / "chat" / "governance"
RETENTION = _GOV / "retention.py"
RULES = _GOV / "purge_rules.py"


def _tree(path):
	return ast.parse(path.read_text(encoding="utf-8"))


def _func(path, name):
	for node in ast.walk(_tree(path)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found in {path.name}")


def _src(path, name):
	return ast.get_source_segment(path.read_text(encoding="utf-8"), _func(path, name))


def _calls(node):
	out = []
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			f = inner.func
			out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
	return out


def _all_calls(path):
	return _calls(_tree(path))


def _imports(path):
	out = set()
	for node in ast.walk(_tree(path)):
		if isinstance(node, ast.ImportFrom) and node.module:
			out.add(node.module)
			for alias in node.names:
				out.add(f"{node.module}.{alias.name}")
		elif isinstance(node, ast.Import):
			for alias in node.names:
				out.add(alias.name)
	return out


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


def _code_strings(path):
	tree = _tree(path)
	skip = _docstring_ids(tree)
	return [
		n.value
		for n in ast.walk(tree)
		if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip
	]


class TheScanActuallyScansTest(unittest.TestCase):
	def test_it_finds_calls_and_strings(self):
		self.assertGreater(len(_all_calls(RETENTION)), 25)
		self.assertGreater(len(_code_strings(RETENTION)), 25)

	def test_it_excludes_docstrings(self):
		"""Both modules explain deletion at length. If the scan saw docstrings, every
		assertion below would fail on prose — the mistake, in the other direction, that this
		helper exists to prevent."""
		self.assertIn("Chat Message", _code_strings(RETENTION))
		self.assertFalse([s for s in _code_strings(RETENTION) if len(s) > 400])


class NothingDestroysTest(unittest.TestCase):
	"""The property this whole change turns on."""

	FORBIDDEN_CALLS = frozenset(
		{
			"delete_doc",
			"delete",
			"remove_all",
			"truncate",
			"drop_table",
			"db_delete",
			"clear_table",
		}
	)

	def test_no_deletion_primitive_is_called(self):
		for path in (RETENTION, RULES):
			found = self.FORBIDDEN_CALLS & set(_all_calls(path))
			self.assertFalse(
				found,
				f"{path.name} calls {sorted(found)}.\n\n"
				"§4.F ships the eligibility rule and the survives-a-purge table, NOT the "
				"purge, and purge_rules.can_enable() says why: Phase 5's derived layer has a "
				"staleness story and no retirement story, so a purge can neither keep nor "
				"destroy the chunks and digests covering what it removes. Leaving them serves "
				"a model-written summary of the destroyed conversation forever; deleting them "
				"retreats the indexer watermark so the ten-minute sweep re-chunks the "
				"not-yet-purged messages VERBATIM. The prerequisite is a retirement path in "
				"chat/indexing/, not more code here.",
			)

	def test_no_sql_statement_destroys(self):
		for path in (RETENTION, RULES):
			for value in _code_strings(path):
				lowered = value.lower()
				for word in ("delete from", "truncate ", "drop table"):
					self.assertNotIn(word, lowered, f"{path.name}: {value[:80]}")

	def test_no_write_of_any_kind_outside_the_audit_row(self):
		"""``set_value``/``insert``/``save`` would each be a way to change a message without
		deleting one — a soft purge is still a purge."""
		writes = {"set_value", "insert", "save", "submit", "set_single_value", "sql"}
		found = writes & set(_all_calls(RETENTION))
		self.assertFalse(found, f"retention.py calls {sorted(found)}")

	def test_the_only_recorded_effect_is_the_governance_row(self):
		self.assertIn("record_governance_event", _all_calls(RETENTION))
		self.assertEqual(len([c for c in _all_calls(RETENTION) if c == "record_governance_event"]), 1)

	def test_it_imports_nothing_that_deletes(self):
		for module in _imports(RETENTION) | _imports(RULES):
			self.assertNotIn("delete_doc", module)
			self.assertNotIn("model.delete", module)


class NoMessageTextTest(unittest.TestCase):
	def test_the_planner_selects_no_body_column(self):
		"""A planner that read `text` would be a second unaudited transcript reader, and it
		could not meet the raw-SQL guard's system-context test on any of its three counts."""
		for value in _code_strings(RETENTION):
			self.assertNotIn(value, ("text", "text_plain"))

	def test_the_message_query_selects_identifiers_and_states_only(self):
		src = _src(RETENTION, "_messages")
		for column in ("name", "seq", "creation", "is_deleted", "thread_root"):
			self.assertIn(f'"{column}"', src)
		self.assertNotIn('"text"', src)

	def test_the_audit_detail_carries_counts_and_never_content(self):
		src = _src(RETENTION, "_record")
		for forbidden in ("text", "body", "summary_text", "message_text"):
			self.assertNotIn(f'"{forbidden}"', src)


class GateTest(unittest.TestCase):
	def test_the_planner_refuses_when_chat_is_disabled(self):
		self.assertIn('_setting_int("enabled")', _src(RETENTION, "plan"))

	def test_it_consults_can_enable_and_reports_the_blocker(self):
		src = _src(RETENTION, "plan")
		self.assertIn("can_enable()", src)
		self.assertIn("blocker", src)

	def test_the_planner_never_raises(self):
		fn = _func(RETENTION, "plan")
		handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
		self.assertTrue(handlers)
		for handler in handlers:
			self.assertFalse([n for n in ast.walk(handler) if isinstance(n, ast.Raise)])

	def test_there_is_no_http_endpoint(self):
		for path in (RETENTION, RULES):
			for node in ast.walk(_tree(path)):
				if isinstance(node, ast.FunctionDef):
					for dec in node.decorator_list:
						target = dec.func if isinstance(dec, ast.Call) else dec
						self.assertNotEqual(getattr(target, "attr", ""), "whitelist", node.name)


class ReportShapeTest(unittest.TestCase):
	#: Emitted strings, in the order the report must print them. Anchored on what the
	#: function *outputs* rather than on words in its prose — asserting on `blocker` matched
	#: the docstring paragraph explaining the blocker, which is the seventh time a
	#: text-matching assertion in this series has flagged the sentence rather than the code.
	ORDER = ("HELD BACK", "would be destroyed", "WHY THE DESTRUCTIVE PATH IS NOT BUILT")

	def test_the_report_prints_holds_then_the_count_then_the_blocker(self):
		"""'4,812 messages are eligible' as a headline is the report that invites the answer
		'so turn it on'. Every unsafe assumption in a retention rule lives in the holds, and
		the reason the destructive path is absent is the last word rather than a footnote."""
		emitted = [s for s in _code_strings(RETENTION) if any(k in s for k in self.ORDER)]
		self.assertEqual(len(emitted), len(self.ORDER), f"expected all three, got {emitted}")
		src = _src(RETENTION, "report")
		positions = [src.index(key) for key in self.ORDER]
		self.assertEqual(positions, sorted(positions), f"printed out of order: {self.ORDER}")


class PurityTest(unittest.TestCase):
	def test_the_rules_module_imports_nothing_at_all(self):
		allowed = {"__future__", "__future__.annotations", "typing", "typing.Final"}
		self.assertEqual(_imports(RULES) - allowed, set())


if __name__ == "__main__":
	unittest.main()
