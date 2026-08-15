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

#: The named predicate for "this scope is unrestricted". Calling it counts as branching on the
#: unrestricted scope, exactly as comparing to the literal does — otherwise the helper would be
#: a way to ask the privilege question with the rule's keyword nowhere in sight.
_CLASSIFIER: str = "is_privileged_scope"

#: What counts as recording. ``record_privileged_content_read`` is the endpoint-facing wrapper:
#: it is a no-op for an ordinary member and calls ``record_or_refuse`` for a privileged one, so
#: a function that calls it has discharged the obligation.
_RECORDING_CALLS: frozenset[str] = frozenset(
	{"record_or_refuse", "record_privileged_read", "record_privileged_content_read"}
)

#: The single exemption, by function name, and it is the classifier itself. Its whole body is
#: the comparison and it returns a bool — there is no content for it to record, and requiring
#: an audit row from a predicate would mean writing one every time somebody *asks* whether a
#: read is privileged rather than every time one happens.
#:
#: Keep this at one entry. A second name here is the moment this rule starts describing an
#: aspiration instead of a property.
_SCOPE_RULE_EXEMPT: frozenset[str] = frozenset({_CLASSIFIER})

#: The audit tables, as they appear in SQL and as DocType names.
AUDIT_DOCTYPES: frozenset[str] = frozenset(
	{"Chat Audit Log", "Chat Retrieval Audit", "Chat Retrieval Audit Room"}
)
AUDIT_TABLES: frozenset[str] = frozenset({f"tab{name}" for name in AUDIT_DOCTYPES})

#: ``chat/audit.py`` itself, read for the doctype constants it defines.
_AUDIT_MODULE: Path = APP_DIR / "chat" / "audit.py"


def _audit_constant_map() -> dict[str, str]:
	"""``{"AUDIT_DOCTYPE": "Chat Retrieval Audit", ...}``, read out of ``chat/audit.py``.

	**This scan used to collect string *literals* only, and that is how it missed a real
	violation for eleven releases.** ``viewer._stamp_category`` wrote ``reason_category`` onto
	an audit row with ``frappe.db.set_value(audit.AUDIT_DOCTYPE, ...)`` — an ``ast.Attribute``,
	not an ``ast.Constant`` — so the one check that exists to forbid exactly that call could
	not see it. Naming the constant instead of the string was enough to walk past the guard.

	Derived rather than hard-coded, so renaming a constant in ``chat/audit.py`` moves this map
	with it instead of silently disarming the scan again.
	"""
	tree = ast.parse(_AUDIT_MODULE.read_text(encoding="utf-8"), str(_AUDIT_MODULE))
	found: dict[str, str] = {}
	for node in tree.body:
		if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
			continue
		value = node.value.value
		if not isinstance(value, str) or value not in AUDIT_DOCTYPES:
			continue
		for target in node.targets:
			if isinstance(target, ast.Name):
				found[target.id] = value
	return found


def _audit_constant_bindings(tree: ast.AST, constants: dict[str, str]) -> tuple[set[str], dict[str, str]]:
	"""Names in *this* file that certainly refer to ``chat/audit.py``'s doctype constants.

	Binding-aware, and that is load-bearing rather than fastidious. Matching any attribute
	whose final component is ``AUDIT_DOCTYPE``/``ROOM_DOCTYPE``/``GOVERNANCE_DOCTYPE`` looks
	equivalent and is not: several modules define a local ``ROOM_DOCTYPE = "Chat Room"`` and
	write to it legitimately, and a name-only rule reports all of them. Requiring the import in
	the same file separates "this is audit's constant" from "this happens to share its spelling".

	Returns the module aliases (``from ...chat import audit`` → ``{"audit"}``) and the directly
	imported names (``from ...chat.audit import AUDIT_DOCTYPE`` → ``{"AUDIT_DOCTYPE": ...}``).
	"""
	module_aliases: set[str] = set()
	direct: dict[str, str] = {}
	for node in ast.walk(tree):
		if not isinstance(node, ast.ImportFrom) or not node.module:
			continue
		if node.module.endswith("chat.audit"):
			for alias in node.names:
				if alias.name in constants:
					direct[alias.asname or alias.name] = constants[alias.name]
		elif node.module.endswith("chat") or node.module.endswith("erpnext_enhancements"):
			for alias in node.names:
				if alias.name == "audit":
					module_aliases.add(alias.asname or alias.name)
	return module_aliases, direct


def _named_audit_doctypes(node: ast.Call, module_aliases: set[str], direct: dict[str, str],
                          constants: dict[str, str]) -> list[str]:
	"""The audit doctype names this call passes, whether spelled as a literal or a constant."""
	out: list[str] = []
	for arg in list(node.args) + [kw.value for kw in node.keywords]:
		if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
			if arg.value in AUDIT_DOCTYPES or arg.value in AUDIT_TABLES:
				out.append(arg.value)
		elif isinstance(arg, ast.Attribute) and arg.attr in constants:
			if isinstance(arg.value, ast.Name) and arg.value.id in module_aliases:
				out.append(constants[arg.attr])
		elif isinstance(arg, ast.Name) and arg.id in direct:
			out.append(direct[arg.id])
	return out

#: The one module allowed to write these rows, and the controller that guards them. Any other
#: module doing so is the defect this file exists to catch.
#:
#: Relative to the app directory, forward slashes, compared case-insensitively so a Windows
#: checkout and CI agree.
WRITER_MODULES: frozenset[str] = frozenset(
	{
		"chat/audit.py",
		"chat/doctype/chat_retrieval_audit/chat_retrieval_audit.py",
		# The ONE deleter, and it is on this list deliberately rather than by accident: this
		# scan caught it the moment it was written, which is the check doing its job. It
		# removes the rows the withdrawn v1.268.0 design wrote — rows carrying no rooms, no
		# counts and an `accessed_by` of Administrator, whose chain_hash was computed over a
		# `creation` Frappe overwrote and which therefore fail verification permanently. It
		# goes through the controller's documented purge flag rather than around it, and
		# selects on `recorded_at is null`, which cannot match a row from the current writer
		# because that field is required.
		#
		# A second entry here should be argued as hard as this one was.
		"patches/purge_pre_redesign_chat_audit_rows.py",
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
		constants = _audit_constant_map()
		self.assertTrue(
			constants,
			"no doctype constants were resolved out of chat/audit.py, so this scan is now "
			"literal-only again — which is the exact state in which it missed _stamp_category",
		)
		for path in _python_files():
			if _is_writer(path):
				continue
			try:
				tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
			except SyntaxError:  # pragma: no cover - a broken file fails its own tests
				continue

			# Resolved per file, because a constant only counts as *this* module's constant when
			# this file imported it from here.
			module_aliases, direct = _audit_constant_bindings(tree, constants)

			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				name = _dotted(node.func)
				short = name.rsplit(".", 1)[-1]
				if name not in MUTATING_CALLS and short not in MUTATING_CALLS:
					continue
				for text in _named_audit_doctypes(node, module_aliases, direct, constants):
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


class TestThePrivilegedReadIsRecordedAtTheEndpoint(unittest.TestCase):
	"""**The rule that replaced writing from permission hooks.**

	v1.268.0 wrote the audit row inside ``note_privileged_read``, which fires from nine places
	in the permission stack. That committed inside other requests' transactions, recorded
	reads that were later denied, recorded nothing about what was read, and fired for
	``Administrator`` from background jobs. The row is now written by the endpoint returning
	the content — so the thing to enforce is that **every endpoint which can obtain the
	unrestricted scope actually records one.**
	"""

	def test_the_hook_marks_and_does_not_write(self) -> None:
		perms = (APP_DIR / "chat" / "permissions.py").read_text(encoding="utf-8")
		tree = ast.parse(perms)
		fn = next(
			(
				n
				for n in ast.walk(tree)
				if isinstance(n, ast.FunctionDef) and n.name == "note_privileged_read"
			),
			None,
		)
		self.assertIsNotNone(fn, "note_privileged_read has moved; re-derive this check.")

		called = {
			_dotted(n.func)
			for n in ast.walk(fn)
			if isinstance(n, ast.Call)
		}
		short = {c.rsplit(".", 1)[-1] for c in called}

		self.assertIn(
			"mark_privileged_scope",
			short,
			"note_privileged_read no longer marks the scope, so nothing records that the "
			"permission stack handed out an unrestricted read.",
		)
		for forbidden in ("record_privileged_read", "record_or_refuse", "insert", "commit"):
			self.assertNotIn(
				forbidden,
				short,
				f"note_privileged_read calls {forbidden}(). A database write from a permission "
				f"hook commits inside whatever transaction the request was already building - "
				f"announce_unread is a Chat Message.after_insert, so this reaches the relay "
				f"path - and it runs before the permission answer is even known.",
			)

	def test_every_endpoint_that_can_go_unrestricted_records_the_read(self) -> None:
		"""A function that branches on the unrestricted scope must audit in that branch.

		Keyed on the ``"1 = 1"`` comparison, because that literal IS the unrestricted scope -
		``membership_filter_sql`` returns it by contract and spells denial ``"1 = 0"`` so that
		neither can be produced by accident.

		**Calling the named classifier counts as branching on it.** Once
		``_common.is_privileged_scope`` existed, a function could ask the privilege question
		without the literal appearing anywhere in it - which is precisely the shape this rule
		exists to catch, arriving through the front door. Widening it here is strictly
		stronger than the literal-only form, and it is why the classifier itself is the one
		exemption: its whole body IS the comparison, and it returns a bool rather than
		content, so there is nothing for it to record.
		"""
		offenders: list[str] = []
		chat_files = [p for p in _python_files() if p.parts[len(APP_DIR.parts)] == "chat"]
		self.assertGreater(len(chat_files), 20, "the chat package scan found almost nothing.")

		for path in chat_files:
			if _is_writer(path):
				continue
			try:
				tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
			except SyntaxError:  # pragma: no cover
				continue
			for node in ast.walk(tree):
				if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
					continue
				# CONSUMERS only: a function that COMPARES something to "1 = 1" has been handed
				# the unrestricted scope and is deciding what to do about it. The function that
				# RETURNS the literal is membership_filter_sql itself — the producer, which must
				# not write (it runs inside the permission stack), and which matched the first
				# version of this rule. Restricting to the chat package likewise stops an
				# unrelated `1 = 1` in a SQL builder elsewhere in the app reading as a defect.
				calls = {
					_dotted(c.func).rsplit(".", 1)[-1]
					for c in ast.walk(node)
					if isinstance(c, ast.Call)
				}
				compares = any(
					isinstance(cmp_node, ast.Compare)
					and any(
						isinstance(c, ast.Constant) and c.value == "1 = 1"
						for c in cmp_node.comparators
					)
					for cmp_node in ast.walk(node)
				)
				consumes = compares or _CLASSIFIER in calls
				if not consumes or node.name in _SCOPE_RULE_EXEMPT:
					continue
				if not calls & _RECORDING_CALLS:
					offenders.append(f"{_rel(path)}:{node.lineno} {node.name}()")

		self.assertFalse(
			sorted(set(offenders)),
			"these functions branch on the unrestricted scope but record no audit row:\n  "
			+ "\n  ".join(sorted(set(offenders)))
			+ "\n\nDecision #12 permits a non-participant read BECAUSE it is recorded. A code "
			"path that obtains the unrestricted scope and returns content without writing a "
			"Chat Retrieval Audit row is the half of that bargain the feature exists to keep. "
			"Use audit.record_or_refuse() if the function returns message content.",
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


class TestTheGateHasExactlyTwoDoors(unittest.TestCase):
	"""The oversight escape hatch is opt-in per call site, and only one module opts in.

	§4.B states it as *"the gate has exactly two doors"*. Stated from the other end: the
	employee-facing surface scopes to membership for **everybody**, auditors included, and an
	oversight read happens through the surface built for it — which is also the only place a
	mandatory ``reason`` can be collected, and therefore the only place §4.D.2 can be true
	rather than aspirational.

	Settled with the human 2026-08-13. Before it, ``membership_filter_sql`` short-circuited on
	the caller's *roles*, so an auditor's ordinary scrollback ran unrestricted and wrote an
	audit row about their own reading — while its Python twin ``visible_room_names`` had
	always refused to do that, on exactly the grounds the decision adopted.
	"""

	#: The one module permitted to open the hatch. ``retrieve_for_oversight`` is the oversight
	#: door; ``retrieve`` shares the same private query helpers, and splitting those two is the
	#: oversight-read-path work rather than this rule's business.
	OVERSIGHT_DOOR = "chat/retrieval/gate.py"

	def test_only_the_gate_opts_into_the_oversight_hatch(self) -> None:
		offenders: list[str] = []
		chat_files = [p for p in _python_files() if p.parts[len(APP_DIR.parts)] == "chat"]
		self.assertGreater(len(chat_files), 20, "the chat package scan found almost nothing.")

		for path in chat_files:
			if _rel(path) == self.OVERSIGHT_DOOR:
				continue
			try:
				tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
			except SyntaxError:  # pragma: no cover
				continue
			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				if _dotted(node.func).rsplit(".", 1)[-1] != "membership_filter_sql":
					continue
				for keyword in node.keywords:
					if keyword.arg == "allow_oversight" and getattr(keyword.value, "value", False):
						offenders.append(f"{_rel(path)}:{node.lineno}")

		self.assertFalse(
			sorted(set(offenders)),
			"these call sites open the oversight hatch outside the gate:\n  "
			+ "\n  ".join(sorted(set(offenders)))
			+ f"\n\nOnly {self.OVERSIGHT_DOOR} may. Everywhere else — the SPA's history, search, "
			"room list, notification fan-out — scopes to membership for everybody, auditors "
			"included. An oversight read goes through the audited viewer, which is the only "
			"surface that can collect the reason §4.D.2 requires.",
		)

	def test_the_hatch_is_shut_by_default(self) -> None:
		"""The parameter's default is the whole protection; a truthy default undoes the rule."""
		source = (APP_DIR / "chat" / "permissions.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef) and node.name == "membership_filter_sql":
				defaults = {
					kw.arg: getattr(default, "value", None)
					for kw, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=False)
				}
				self.assertIn(
					"allow_oversight",
					defaults,
					"allow_oversight must stay KEYWORD-ONLY, so it cannot be passed by accident "
					"in the seq_column position",
				)
				self.assertIs(
					defaults["allow_oversight"],
					False,
					"allow_oversight must default to False — fail closed. A truthy default "
					"restores the behaviour where every caller silently got the hatch.",
				)
				return
		self.fail("membership_filter_sql not found in chat/permissions.py")


class TestEveryAuditControllerRefusesEveryPath(unittest.TestCase):
	"""Three tables, three doors each. The child table had none of them.

	``Chat Retrieval Audit Room``'s controller was a bare ``pass``, and its docstring explained
	that a guard was unnecessary because reaching a child row without its parent "means
	bypassing the ORM altogether". **It does not.**
	``frappe.delete_doc("Chat Retrieval Audit Room", name)`` and
	``frappe.get_doc(...).db_set(...)`` are ordinary ORM paths that never load the parent, and
	the parent's refusals do not reach a document that was never loaded through it.

	The older assertion above tests one controller by path. That is why this gap survived: the
	table nobody checked was the one nobody had written a guard for.

	``before_change`` is the third door, and it is unconditional by design. In v16
	``Document.db_set`` runs ``run_method("before_change")`` immediately before writing, and
	that is the **only** call site in the framework — so the hook cannot fire on an insert or
	an ordinary save, and any call at all is the violation. Hence no ``if`` to assert, unlike
	its two siblings.
	"""

	CONTROLLERS = ("chat_retrieval_audit", "chat_retrieval_audit_room", "chat_audit_log")

	def _methods(self, module: str) -> dict[str, ast.AST]:
		path = APP_DIR / "chat" / "doctype" / module / f"{module}.py"
		self.assertTrue(path.exists(), f"{module} controller has moved or been renamed")
		return {
			node.name: node
			for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path)))
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
		}

	def test_all_three_controllers_refuse_update_and_delete(self) -> None:
		for module in self.CONTROLLERS:
			methods = self._methods(module)
			for hook in ("before_save", "on_trash"):
				self.assertIn(
					hook,
					methods,
					f"{module} has no {hook}. An audit row that can be "
					f"{'edited' if hook == 'before_save' else 'deleted'} is not a record of "
					"what somebody did — and a child table is a document in its own right.",
				)

	def test_all_three_controllers_carry_the_db_set_tripwire(self) -> None:
		for module in self.CONTROLLERS:
			methods = self._methods(module)
			self.assertIn(
				"before_change",
				methods,
				f"{module} has no before_change, so `doc.db_set(...)` rewrites the row with no "
				"controller consulted. That path writes the column directly and never reaches "
				"before_save.",
			)

	def test_the_tripwire_actually_refuses(self) -> None:
		"""The method existing is not the guard — the same lesson as the sibling assertion."""
		for module in self.CONTROLLERS:
			node = self._methods(module)["before_change"]
			throws = [
				c
				for c in ast.walk(node)
				if isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "throw"
			]
			self.assertTrue(
				throws, f"{module}.before_change() has no frappe.throw — it refuses nothing"
			)
			guards = [
				b
				for b in ast.walk(node)
				if isinstance(b, ast.If)
				and isinstance(b.test, ast.Constant)
				and not b.test.value
			]
			self.assertFalse(
				guards,
				f"{module}.before_change() hides its throw behind a falsy branch, which keeps "
				"the method, the word 'throw' and the refusal of nothing.",
			)

	def test_the_child_no_longer_claims_it_needs_no_guard(self) -> None:
		"""The docstring that made the gap look deliberate.

		Asserted because a future reader finding guards here and prose saying they are
		unnecessary will believe the prose — it is shorter.
		"""
		path = (
			APP_DIR
			/ "chat"
			/ "doctype"
			/ "chat_retrieval_audit_room"
			/ "chat_retrieval_audit_room.py"
		)
		source = path.read_text(encoding="utf-8")
		self.assertNotIn(
			"No immutability guard of its own",
			source,
			"the child controller still says it has no guard of its own, which is now false",
		)


class TestTheConstantResolverItself(unittest.TestCase):
	"""Controls for the resolver, because the scan it belongs to was blind for eleven releases.

	``viewer._stamp_category`` wrote a signed audit column after the hash, from outside the
	writer, and this file is the one check that should have refused it. It passed the whole
	time: the scan collected ``ast.Constant`` strings and the call named ``audit.AUDIT_DOCTYPE``.
	Naming the constant instead of the string was the entire evasion, and it was not deliberate.

	So the resolver gets both a positive and a negative control. A guard nobody has watched fail
	is a guard that may already be guarding nothing, and this one has the receipts.
	"""

	def _flagged(self, source: str) -> list[str]:
		tree = ast.parse(source)
		constants = _audit_constant_map()
		module_aliases, direct = _audit_constant_bindings(tree, constants)
		found: list[str] = []
		for node in ast.walk(tree):
			if isinstance(node, ast.Call):
				found.extend(_named_audit_doctypes(node, module_aliases, direct, constants))
		return found

	def test_the_constants_resolve_out_of_the_writer(self) -> None:
		self.assertEqual(
			_audit_constant_map(),
			{
				"AUDIT_DOCTYPE": "Chat Retrieval Audit",
				"ROOM_DOCTYPE": "Chat Retrieval Audit Room",
				"GOVERNANCE_DOCTYPE": "Chat Audit Log",
			},
		)

	def test_the_shape_that_escaped_is_now_caught(self) -> None:
		"""``_stamp_category``'s call, in shape, as it stood until v1.307.0."""
		source = (
			"from erpnext_enhancements.chat import audit\n"
			"frappe.db.set_value(audit.AUDIT_DOCTYPE, n, 'reason_category', c, update_modified=False)\n"
		)
		self.assertEqual(self._flagged(source), ["Chat Retrieval Audit"])

	def test_a_directly_imported_constant_is_caught(self) -> None:
		source = (
			"from erpnext_enhancements.chat.audit import GOVERNANCE_DOCTYPE\n"
			"frappe.db.set_value(GOVERNANCE_DOCTYPE, n, 'x', 1)\n"
		)
		self.assertEqual(self._flagged(source), ["Chat Audit Log"])

	def test_a_literal_is_still_caught(self) -> None:
		"""The original rule has to keep working; this is a widening, not a replacement."""
		self.assertEqual(
			self._flagged("frappe.db.set_value('Chat Audit Log', n, 'x', 1)\n"),
			["Chat Audit Log"],
		)

	def test_a_same_spelled_constant_from_elsewhere_is_not_caught(self) -> None:
		"""Why the rule is binding-aware rather than spelling-aware.

		Several modules define their own ``ROOM_DOCTYPE = "Chat Room"`` and write to it, quite
		legitimately. A rule keyed on the attribute's last component reports every one of them,
		and a scan that cries wolf is one somebody widens until it says nothing at all.
		"""
		source = (
			"from erpnext_enhancements.chat.sync import outbox\n"
			"ROOM_DOCTYPE = 'Chat Room'\n"
			"frappe.db.set_value(ROOM_DOCTYPE, n, 'x', 1)\n"
			"frappe.db.set_value(outbox.ROOM_DOCTYPE, n, 'x', 1)\n"
		)
		self.assertEqual(self._flagged(source), [])


if __name__ == "__main__":  # pragma: no cover
	unittest.main(verbosity=2)
