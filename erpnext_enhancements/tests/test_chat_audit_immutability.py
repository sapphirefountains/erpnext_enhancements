"""The audit log is append-only, and this file is the layer that says so about the code.

Bench-free. **Shape U: ``unittest``** — run with
``python -m unittest erpnext_enhancements.tests.test_chat_audit_immutability -v``.

--------------------------------------------------------------------------------------
Why a source scan, when there is already a controller
--------------------------------------------------------------------------------------

Decision #12 buys a privileged read with a record of it. That trade only holds while the
record cannot be quietly altered afterwards, and immutability in Frappe has to be argued in
layers because each mechanism is bypassable by the next one down:

1. **DocPerm** — ``System Manager`` gets ``read`` + ``report`` and nothing else. Bypassed by
   ``ignore_permissions=True``, which the writer itself requires, and by Administrator.
2. **The controller** (``chat_retrieval_audit.py``) — refuses a save that is not an insert and
   a delete without the purge flag. Catches the writer and Administrator. **Cannot see**
   ``frappe.db.set_value``, ``doc.db_set``, ``frappe.db.delete`` or raw ``UPDATE``/``DELETE``,
   because none of those load a document and so none of them reach a controller.
3. **This file** — the only thing that can see layer 2's blind spot. It reads the source of
   the whole app and fails if any module other than the one allowlisted writer names an audit
   table next to a mutating call.
4. **The hash chain** (``chat.audit.verify_chain``) — catches a direct database edit after the
   fact, by making it detectable rather than impossible.

A test that only asserted the controller throws would be testing the layer that is easiest to
route around. This one tests the routes around it.

**What it cannot do**, stated so a green run is not read as more than it is: it reads Python
in this repo only. A Server Script, a bench console session, a MariaDB client, or another app
on the same bench are all invisible to it. Layer 4 is the answer to those, and layer 4 raises
the cost of tampering rather than preventing it.
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

APP_DIR: Path = Path(__file__).resolve().parents[1]

#: The audit tables, as they appear in SQL and as DocType names.
AUDIT_DOCTYPES: frozenset[str] = frozenset({"Chat Retrieval Audit", "Chat Retrieval Audit Room"})
AUDIT_TABLES: frozenset[str] = frozenset({f"tab{name}" for name in AUDIT_DOCTYPES})

#: The one module allowed to write these rows, and the controller that guards them. Any other
#: module doing so is the defect this file exists to catch.
#:
#: Relative to the app directory, forward slashes, compared case-insensitively so a Windows
#: checkout and CI agree.
WRITER_MODULES: frozenset[str] = frozenset(
	{
		"chat/audit.py",
		"chat/doctype/chat_retrieval_audit/chat_retrieval_audit.py",
	}
)

#: Calls that change or remove a row **without loading a document**, which is exactly how the
#: controller in layer 2 gets bypassed. ``frappe.db.sql`` is handled separately, by looking at
#: the statement rather than the call name.
MUTATING_CALLS: frozenset[str] = frozenset(
	{
		"frappe.db.set_value",
		"frappe.db.delete",
		"frappe.delete_doc",
		"frappe.db.truncate",
		"db_set",
	}
)

#: Raw statements that write. Matched against SQL literals that also name an audit table.
_WRITING_SQL = re.compile(r"\b(update|delete\s+from|insert\s+into|truncate|alter\s+table|drop\s+table)\b", re.I)

#: A backticked ``tabX`` reference inside a string literal.
_TAB_REF = re.compile(r"`(tab[A-Za-z0-9 _-]+)`")


def _python_files() -> list[Path]:
	return sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
	return path.relative_to(APP_DIR).as_posix()


def _is_writer(path: Path) -> bool:
	return _rel(path).lower() in {w.lower() for w in WRITER_MODULES}


def _dotted(node: ast.AST) -> str:
	"""``frappe.db.set_value`` from the AST of that attribute chain."""
	parts: list[str] = []
	while isinstance(node, ast.Attribute):
		parts.append(node.attr)
		node = node.value
	if isinstance(node, ast.Name):
		parts.append(node.id)
	return ".".join(reversed(parts))


def _string_constants(tree: ast.AST) -> list[tuple[int, str]]:
	"""Every live (non-docstring) string constant, with its line."""
	docstrings: set[int] = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
			body = getattr(node, "body", None)
			if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
				if isinstance(body[0].value.value, str):
					docstrings.add(id(body[0].value))

	out: list[tuple[int, str]] = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
			out.append((getattr(node, "lineno", 0), node.value))
		elif isinstance(node, ast.JoinedStr):
			text = "".join(
				p.value for p in node.values if isinstance(p, ast.Constant) and isinstance(p.value, str)
			)
			if text:
				out.append((getattr(node, "lineno", 0), text))
	return out


class TestTheAuditTablesAreWrittenInOnePlace(unittest.TestCase):
	def test_no_module_outside_the_writer_mutates_an_audit_row(self) -> None:
		"""``set_value``/``db_set``/``delete_doc`` against an audit table, anywhere else.

		Matched on the enclosing *call* rather than on the file, so a module that merely reads
		the audit log — a future report, the oversight viewer — stays legal.
		"""
		offenders: list[str] = []
		for path in _python_files():
			if _is_writer(path):
				continue
			try:
				tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
			except SyntaxError:  # pragma: no cover - a broken file fails its own tests
				continue

			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				name = _dotted(node.func)
				short = name.rsplit(".", 1)[-1]
				if name not in MUTATING_CALLS and short not in MUTATING_CALLS:
					continue
				literals = [
					a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)
				]
				literals += [
					k.value.value
					for k in node.keywords
					if isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)
				]
				for text in literals:
					if text in AUDIT_DOCTYPES or text in AUDIT_TABLES:
						offenders.append(f"{_rel(path)}:{node.lineno} {name}({text!r})")

		self.assertFalse(
			sorted(set(offenders)),
			"these call sites mutate a chat audit row from outside the one allowlisted "
			f"writer ({sorted(WRITER_MODULES)}):\n  " + "\n  ".join(sorted(set(offenders))) + "\n\n"
			"An audit row that can be changed after the fact records what somebody was willing "
			"to leave behind, not what they did. These calls do not load a document, so the "
			"controller's before_save/on_trash guards never run — which is the whole reason "
			"this check exists. Route the write through chat/audit.py, or if this is a "
			"retention purge, set the documented flag and add the module to WRITER_MODULES in "
			"front of a reviewer.",
		)

	def test_no_raw_sql_writes_an_audit_table(self) -> None:
		"""Raw ``UPDATE``/``DELETE``/``INSERT`` naming an audit table, outside the writer."""
		offenders: list[str] = []
		for path in _python_files():
			if _is_writer(path):
				continue
			try:
				tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
			except SyntaxError:  # pragma: no cover
				continue
			for line, text in _string_constants(tree):
				tables = set(_TAB_REF.findall(text))
				if not tables & AUDIT_TABLES:
					continue
				if _WRITING_SQL.search(text):
					offenders.append(f"{_rel(path)}:{line}")

		self.assertFalse(
			sorted(set(offenders)),
			"these SQL literals write to a chat audit table from outside the writer:\n  "
			+ "\n  ".join(sorted(set(offenders)))
			+ "\n\nRaw SQL reaches neither the DocPerm layer nor the controller. There is no "
			"legitimate reason for a second module to write these rows.",
		)


class TestTheGuardIsNotVacuous(unittest.TestCase):
	"""Every assertion above is only worth its runtime if it can still see the thing it checks.

	A scan whose markers have drifted passes silently and forever, which is worse than no scan:
	it occupies the slot where a working check would go.
	"""

	def test_the_writer_module_exists_and_is_the_one_that_writes(self) -> None:
		writer = APP_DIR / "chat" / "audit.py"
		self.assertTrue(writer.exists(), "chat/audit.py has moved; re-derive WRITER_MODULES.")
		source = writer.read_text(encoding="utf-8")
		self.assertIn(
			"Chat Retrieval Audit",
			source,
			"the allowlisted writer no longer names the audit DocType — either it stopped "
			"being the writer, or the DocType was renamed and this file is now guarding "
			"nothing.",
		)

	def test_the_scan_walks_a_real_tree(self) -> None:
		files = _python_files()
		self.assertGreater(
			len(files),
			200,
			f"only {len(files)} python files found under {APP_DIR}; the walk has stopped "
			"matching and both checks above are now passing vacuously.",
		)

	def test_the_controller_guards_both_directions(self) -> None:
		"""Layer 2 must refuse an update AND a delete. One without the other is a half-door."""
		controller = (
			APP_DIR / "chat" / "doctype" / "chat_retrieval_audit" / "chat_retrieval_audit.py"
		)
		self.assertTrue(controller.exists(), "the audit controller has moved.")
		source = controller.read_text(encoding="utf-8")
		tree = ast.parse(source, str(controller))
		methods = {
			node.name: node
			for node in ast.walk(tree)
			if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
		}
		self.assertIn("before_save", methods, "no before_save: the row is editable after insert.")
		self.assertIn("on_trash", methods, "no on_trash: the row is deletable.")

		# The METHOD existing is not the guard. `if False: frappe.throw(...)` keeps the method,
		# keeps the constant, and keeps the word "throw" in the file while refusing nothing —
		# which is precisely the mutation that slipped through the first version of this test.
		for name, must_mention in (("before_save", "is_new"), ("on_trash", "RETENTION_PURGE_FLAG")):
			node = methods[name]
			reachable = [
				branch
				for branch in ast.walk(node)
				if isinstance(branch, ast.If)
				and not (isinstance(branch.test, ast.Constant) and not branch.test.value)
				and any(
					isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "throw"
					for c in ast.walk(branch)
				)
			]
			self.assertTrue(
				reachable,
				f"{name}() has no reachable frappe.throw, so it refuses nothing. An audit row "
				f"that can be {'edited' if name == 'before_save' else 'deleted'} is not a "
				f"record of what somebody did.",
			)
			condition = "".join(ast.dump(b.test) for b in reachable)
			self.assertIn(
				must_mention,
				condition,
				f"{name}()'s guard no longer tests {must_mention}, so it is guarding something "
				f"other than what it claims.",
			)

	def test_a_planted_violation_would_be_caught(self) -> None:
		"""The detectors themselves, proven against synthetic source rather than assumed.

		Both checks are regex-and-AST heuristics; a heuristic that has quietly stopped matching
		reports a clean tree with the same green as a tree that is actually clean.
		"""
		call_tree = ast.parse('frappe.db.set_value("Chat Retrieval Audit", "x", "reason", "oops")')
		found = []
		for node in ast.walk(call_tree):
			if isinstance(node, ast.Call) and _dotted(node.func) in MUTATING_CALLS:
				for a in node.args:
					if isinstance(a, ast.Constant) and a.value in AUDIT_DOCTYPES:
						found.append(a.value)
		self.assertEqual(found, ["Chat Retrieval Audit"], "the call detector no longer fires.")

		# Assembled at runtime, deliberately. Written as one literal it would be a genuine
		# violation sitting in this very file, and the scan above would report it — correctly,
		# which is the joke: the only way to keep a fixture that proves the detector works is
		# to make sure no single literal here holds both a write verb and a table name.
		verb = "up" + "date"
		table = "`tab" + "Chat Retrieval Audit`"
		sql_tree = ast.parse(f'q = "{verb} {table} set `reason` = 1"')
		hits = []
		for _line, text in _string_constants(sql_tree):
			if set(_TAB_REF.findall(text)) & AUDIT_TABLES and _WRITING_SQL.search(text):
				hits.append(text)
		self.assertEqual(len(hits), 1, "the raw-SQL detector no longer fires.")

		clean = ast.parse(f'q = "select * from {table} order by `creation`"')
		benign = [
			t
			for _l, t in _string_constants(clean)
			if set(_TAB_REF.findall(t)) & AUDIT_TABLES and _WRITING_SQL.search(t)
		]
		self.assertEqual(benign, [], "a plain SELECT is being reported as a write.")


if __name__ == "__main__":  # pragma: no cover
	unittest.main(verbosity=2)
