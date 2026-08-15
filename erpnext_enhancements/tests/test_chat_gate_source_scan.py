"""The retrieval gate is the only door onto the chat index. Bench-free.

ADR 0009 §I.3, invariant I4. Phase 5's real risk is a security bug rather than a
correctness bug: Triton may read the asking person's history across every room
they belong to, so a filter applied one `return` too late produces **a correct
answer delivered to the wrong reader** — with no user-visible symptom, no
exception, and nothing in any log. Every other risk in this phase produces a
wrong answer, which somebody notices.

This file is the fence, and it lands **before** the code it polices. That
ordering is deliberate and is the whole reason it works: a scan written after the
search functions exist is a scan that gets argued with, one exemption at a time,
by an author who has already written the query.

--------------------------------------------------------------------------------------
The four rules
--------------------------------------------------------------------------------------

* **Rule A — the index tables are readable from exactly one file.** No SQL
  naming ``Chat Context Chunk``, ``Chat Room Digest`` or ``Chat Thread Digest``
  exists anywhere in ``erpnext_enhancements/`` outside
  ``chat/retrieval/gate.py``. Scanned across the whole app, not just the chat
  package, because the module that will want to route around the gate is a
  reporting helper somebody adds under ``api/`` for speed.
* **Rule B — every SQL builder in the retrieval package takes
  ``allowed_rooms`` as its first positional parameter**, required and
  undefaulted, so omitting it is a ``TypeError`` at the call site rather than a
  leak at runtime.
* **Rule C — the package exports exactly two public symbols.** One gated entry
  point and one oversight entry point; everything else is private.
* **Rule D — the entry point has no parameter by which a caller supplies room
  ids.** Not a keyword, not an optional, not a "trusted internal" hint.
  ``allowed_rooms`` is *derived* from the caller's own membership rows. A
  ``restrict_to`` argument is permitted because it can only narrow by
  intersection — a caller may reduce the search to a subset it already holds, and
  a ``restrict_to`` naming a room the user cannot see contributes nothing.

--------------------------------------------------------------------------------------
Why Appendix B's wording is not the wording enforced here
--------------------------------------------------------------------------------------

Appendix B specifies this test as *"every SQL literal under
``erpnext_enhancements/chat/**`` lives in ``gate.py`` and contains
``allowed_rooms``"*. That sentence was written before Phase 3 existed and is now
false by construction: ``api/history.py`` pages the transcript with keyset SQL,
``api/search.py`` runs the lexical search, ``permissions.py`` *is* the membership
fragment, and ``health.py`` reads aggregates under ``bench execute``. All four
are legitimate, and all four are already policed — per ``(file, function,
table)`` triple, each with a written justification — by
``tests/test_chat_rawsql_guard.py``.

Enforcing the sentence literally would leave two options, both worse: delete that
guard, or exempt the entire chat package from this one. So the rule kept is the
half that carries the security weight — **the retrieval path specifically has one
door** — and the general "is every chat query scoped or justified" question stays
where it already lives and already works. The divergence is recorded in the
CHANGELOG rather than resolved silently.

--------------------------------------------------------------------------------------
What this file cannot do
--------------------------------------------------------------------------------------

It is a source-level check. It proves ``allowed_rooms`` is a parameter and that
the fragment is *named* in the function; it cannot prove the fragment was ANDed
into the right ``WHERE`` rather than assigned to an unused local. It reads Python
only. And it says nothing about ordering — that permission filtering happens
*before* ranking rather than after is a behavioural claim and belongs to
``tests/test_chat_triton_bench.py``, which CI does not run.

The synthetic controls in :class:`TestTheAnalyserItself` are load-bearing rather
than decorative: before the retrieval package exists, they are the only thing
proving this suite can fail at all.

Run: python -m unittest erpnext_enhancements.tests.test_chat_gate_source_scan -v
"""

from __future__ import annotations

import ast
import re
import unittest
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

APP_DIR: Path = Path(__file__).resolve().parents[1]
RETRIEVAL_DIR: Path = APP_DIR / "chat" / "retrieval"
GATE_REL: str = "chat/retrieval/gate.py"

#: The tables only the gate may touch. These are the Phase 5 index: chunk bodies
#: are verbatim copies of what coworkers said, and a digest is a *summary* of the
#: same thing, which is worse rather than better — a summary crosses room
#: boundaries by construction and cannot be un-said once it is in a context
#: window.
#:
#: ``Chat Message`` and ``Chat Room Member`` are deliberately NOT in this set.
#: They are read legitimately from a dozen places (the transcript, search, the
#: notification fan-out, the relay) and are policed per-triple by
#: ``tests/test_chat_rawsql_guard.py``. Duplicating that rule here with a
#: different exemption mechanism would give the package two answers to one
#: question, and the one that drifts is always the copy furthest from the rule.
RETRIEVAL_TABLES: frozenset[str] = frozenset(
	{
		"Chat Context Chunk",
		"Chat Room Digest",
		"Chat Thread Digest",
	}
)

#: The two public symbols :data:`RETRIEVAL_DIR` may export.
#:
#: ``retrieve_for_oversight`` is separate from ``retrieve`` rather than a flag on
#: it, and the reason is that a boolean is one typo from being ``True``. A
#: distinctly named function cannot be reached by a caller who did not mean to
#: reach it, and it shows up as itself in a stack trace and in a grep.
PUBLIC_SYMBOLS: frozenset[str] = frozenset(
	{"retrieve", "retrieve_for_oversight", "retrieve_transcript", "search_transcripts"}
)

#: The one entry point Rule D exempts, conditionally — see
#: :meth:`TestRuleDTheEntryPointDerivesItsOwnRoomSet.test_the_oversight_path_pays_for_its_exemption`.
OVERSIGHT_SYMBOL: str = "retrieve_for_oversight"

#: The verbatim read of one room, added in v1.308.0. It is a THIRD public symbol on a package
#: whose stated design is "one door", so it is named here rather than reached around: the
#: alternative was importing ``gate`` directly from the viewer and leaving ``__all__`` saying
#: something that was no longer true.
#:
#: Rule D does not trip on it — it takes ``room``, singular, which is deliberately absent from
#: :data:`FORBIDDEN_ENTRY_PARAMS` because ``retrieve(room=…)`` names a room *inside* the
#: derived set rather than supplying the set. Here the room **is** the scope, which is the same
#: shape the oversight exemption covers, so it pays the same price: see
#: :meth:`TestRuleDTheEntryPointDerivesItsOwnRoomSet.test_the_transcript_path_pays_for_its_exemption`.
TRANSCRIPT_SYMBOL: str = "retrieve_transcript"

#: The filtered cross-room search, added in v1.314.0. The third entry point that takes a
#: caller-supplied room set, and it pays the same price — see
#: :meth:`TestRuleDTheEntryPointDerivesItsOwnRoomSet.test_the_search_path_pays_for_its_exemption`.
SEARCH_SYMBOL: str = "search_transcripts"

#: The required first positional parameter of every SQL builder in the package.
ALLOWED_ROOMS: str = "allowed_rooms"

#: Parameter names that would let a caller hand the gate its own room set. The
#: check is on the *name* because that is all a scan can see — and a name is
#: enough: nobody smuggles a room list through a parameter called ``limit``, and
#: somebody absolutely would through one called ``rooms``.
FORBIDDEN_ENTRY_PARAMS: frozenset[str] = frozenset(
	{
		ALLOWED_ROOMS,
		"room_ids",
		"rooms",
		"room_set",
		"visible_rooms",
		"scope_rooms",
		"member_rooms",
	}
)

#: Permitted on the entry point, because intersection cannot widen.
NARROWING_PARAM: str = "restrict_to"

SQL_CALLS: frozenset[tuple[str, ...]] = frozenset(
	{
		("frappe", "db", "sql"),
		("frappe", "db", "get_all"),
		("frappe", "get_all"),
		("frappe", "db", "get_list"),
	}
)

#: ``(file, function)`` occurrences permitted to *name* a retrieval table outside the gate,
#: each with the reason it is a name rather than a read.
#:
#: Keyed on the pair rather than the file, deliberately, matching
#: ``tests/test_chat_rawsql_guard.py``: a file-level exemption silently covers whatever
#: function is added to that file next, and the next function is the one that answers a
#: user.
#:
#: The exemption is also **structural rather than trusted** —
#: :class:`TestTheExemptionsAreHonest` asserts that no exempted ``(file, function)``
#: executes anything. The day one of them grows a ``frappe.db.sql`` or a ``frappe.get_all``
#: it has stopped being a name and become a query, and this suite says so.
NAME_ONLY_EXEMPTIONS: dict[tuple[str, str], str] = {
	(
		"assistant_tools/_gate.py",
		"<module>",
	): (
		"CHAT_DENYLIST_DOCTYPES — the list of tables the generic AI tools must REFUSE. "
		"Reporting it as an ungated read would be exactly backwards: it is the thing that "
		"stops the read. Same inversion as permissions.py's fragment builders in the "
		"raw-SQL guard, which name the chat tables because they ARE the membership filter.\n"
		"It is also checked against the filesystem by set equality in "
		"tests/test_chat_mcp_denylist.py, so these three names are not optional there — "
		"omitting them to keep this scan quiet would silently open the surface the denylist "
		"exists to close."
	),
	(
		"chat/governance/purge_rules.py",
		"<module>",
	): (
		"The survives-a-purge table — every chat DocType classified with a written reason, "
		"and these three are the entries that say BLOCKED: a purge can neither keep nor "
		"destroy them correctly today. Naming them IS that finding, and omitting them to "
		"keep this scan quiet would delete the stated reason the destructive path is not "
		"built.\n"
		"The honesty condition is structural here rather than argued: purge_rules.py imports "
		"NOTHING — not frappe, not anything — which tests/test_chat_purge_surface.py's "
		"PurityTest asserts by set equality over its imports. A module with no imports cannot "
		"execute a query, so there is no read here to route around the gate. The table is "
		"separately checked for set equality against chat/doctype/ on disk, so these three "
		"names are not optional there either."
	),
	(
		"patches/add_chat_phase5_indexes.py",
		"<module>",
	): (
		"The index definitions: (doctype, columns, index_name) tuples the patch turns into "
		"DDL. Creating an index reads no rows and returns none — and the FULLTEXT index in "
		"particular has no framework helper at all, so its table name has to appear in raw "
		"DDL somewhere or the lexical tier does not exist.\n"
		"The module scope holds only the tuples; every statement that executes lives in a "
		"named function below, and the only SELECT among them is against "
		"information_schema.STATISTICS, which holds index metadata rather than conversation."
	),
}

#: The one *package* permitted to name the index tables besides the gate, and the four
#: properties it must keep to hold that permission.
#:
#: Rule A is not "only one file may mention these tables" — something has to **write** them.
#: It is "the only place a person can cause one of these tables to be read is the gate". The
#: indexing package is the writer: it runs on the scheduler with no session user, it reads
#: across every room by design, and its output is governed at the point of *consumption*
#: rather than production.
#:
#: That permission is worth exactly as much as the properties below, which is why they are
#: asserted rather than described.
WRITER_PACKAGE: str = "chat/indexing"

WRITER_PACKAGE_REASON: str = (
	"The indexer, the summariser and the invalidation writer. Something must write the index, "
	"and it cannot be the gate: the gate exists to answer one person from their own rooms, "
	"while these read every room and belong to nobody.\n"
	"What makes that safe is not this exemption, it is the four properties asserted below — "
	"no whitelisted method anywhere in the package, so no HTTP request can ask it for "
	"anything; every public entry point named and justified individually; every one of them "
	"either a registered scheduler job or the staleness seam's writer; and no endpoint "
	"importing it. The gate is still the only way a person's question reaches a chunk body."
)

#: ``{file: {public function: reason}}`` — the public surface of the writer package. Anything
#: else in it must be underscore-private.
#:
#: Named individually rather than by pattern, because "public function in the indexing
#: package" is exactly the shape somebody would add a convenient cross-room reader as.
WRITER_ENTRY_POINTS: dict[str, dict[str, str]] = {
	"chat/indexing/retire.py": {
		"plan_retirement": (
			"Answers how far the retirement mark may move and what that would remove. Writes "
			"NOTHING, which is the point: the snap decides which messages a purge is ALLOWED to "
			"delete, so a caller that snapped in its own code and then deleted up to the "
			"unsnapped seq would destroy exactly the messages the snap was protecting. Reads "
			"chunk spans and a room's two counters; no body column."
		),
		"set_retirement_mark": (
			"Advances Chat Room.retired_below_seq and deletes the derived coverage it retires. "
			"REFUSES unless the messages at or below the mark are already gone - the check is "
			"the whole safety argument, because the mark asserts they are, and every consumer "
			"acts on that assertion. Setting it over live messages would make readable "
			"conversation permanently invisible to the assistant with no error anywhere. No "
			"argument bypasses the check. Not whitelisted; bench execute and the future purge."
		),
		"sweep_retirement": (
			"Scheduler job. Finishes deletions an already-written mark authorises - it never "
			"advances a mark and never deletes a message, so it cannot destroy anything a human "
			"did not ask for. The ONE sweep in this package with no is_archived filter, and "
			"deliberately: archiving a room is otherwise the single action that makes its "
			"retired coverage permanent, because both other sweeps skip archived rooms."
		),
		"report": (
			"bench execute. Prints what a retirement would do and writes nothing, the same "
			"shape chat.health.report and chat.governance.drift.report use."
		),
	},
	"chat/indexing/indexer.py": {
		"sweep_chunks": (
			"Scheduler job, every ten minutes. Reads messages past each room's derived "
			"watermark and writes sealed chunks. No network I/O and no caller-supplied input "
			"of any kind — its only argument is the clock."
		),
		"sweep_embeddings": (
			"Scheduler job. Reads sealed chunk bodies and sends them to the embedding "
			"provider. This is the one place a chunk body legitimately leaves the database "
			"outside the gate, and it goes to the model that indexes it rather than to a "
			"reader."
		),
	},
	"chat/indexing/digest.py": {
		"sweep_digests": (
			"Scheduler job, every five minutes. Selects rooms by a derived dirty predicate "
			"and rewrites their summaries. Reads across every room, which is precisely why "
			"its OUTPUT is behind the same gate as the messages it summarises."
		),
		"clear_digest_poison": (
			"Operator recovery, `bench execute` only. Clears the latching `poisoned` flag so "
			"the sweeper will retry a room whose *cause* has been fixed. Writes two integer "
			"columns and returns two counts — it reads no message, no summary and no room "
			"name it was not given, and returns none of them."
		),
		"check_digest_staleness": (
			"Scheduler job, hourly. Reads one aggregate — the newest generated_at anywhere — "
			"and alerts if the summariser has stopped. No room, no body, no identifiers."
		),
	},
	"chat/indexing/invalidate.py": {
		"invalidate_span": (
			"The staleness seam's writer, and the one entry point here that is NOT a "
			"scheduler job: it is called from the edit and delete paths, synchronously, "
			"because a digest covering a message somebody just deleted must stop being "
			"served now rather than at the next pass.\n"
			"It only ever sets is_stale. It selects no column, returns no content, and its "
			"three statements are UPDATEs — so the widest thing it can do wrong is "
			"invalidate too much, which costs a recomputation."
		),
	},
}

_TAB_RE = re.compile(r"`tab([^`]+)`")
_MIN_REASON = 120


# --------------------------------------------------------------------------- collection


@dataclass(frozen=True)
class TableUse:
	"""One occurrence of a retrieval-table name in a live (non-docstring) string."""

	file: str  #: posix path relative to the app directory
	function: str
	line: int
	table: str

	def __str__(self) -> str:
		return f"{self.file}:{self.line} {self.function}() -> {self.table}"


def _python_files(root: Path) -> list[Path]:
	return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path, root: Path = APP_DIR) -> str:
	return path.relative_to(root).as_posix()


def _parse(path: Path) -> ast.Module:
	return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted(node: ast.AST) -> tuple[str, ...]:
	parts: list[str] = []
	current: ast.AST = node
	while isinstance(current, ast.Attribute):
		parts.append(current.attr)
		current = current.value
	if not isinstance(current, ast.Name):
		return ()
	parts.append(current.id)
	return tuple(reversed(parts))


def _docstring_constant_ids(tree: ast.Module) -> set[int]:
	"""So prose *about* a table is not read as a query against it. Without this
	the module docstring you are reading would fail Rule A."""
	ids: set[int] = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
			continue
		body = getattr(node, "body", None)
		if not body:
			continue
		first = body[0]
		if (
			isinstance(first, ast.Expr)
			and isinstance(first.value, ast.Constant)
			and isinstance(first.value.value, str)
		):
			ids.add(id(first.value))
	return ids


def _function_index(tree: ast.Module) -> dict[int, str]:
	index: dict[int, str] = {}

	def walk(node: ast.AST, current: str) -> None:
		name = current
		if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
			name = node.name
		index[id(node)] = name
		for child in ast.iter_child_nodes(node):
			walk(child, name)

	walk(tree, "<module>")
	return index


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
	"""``NAME = "literal"`` at module level, so ``f"… `tab{CHUNK}`"`` resolves."""
	out: dict[str, str] = {}
	for node in tree.body:
		target: ast.expr | None = None
		if isinstance(node, ast.Assign) and len(node.targets) == 1:
			target = node.targets[0]
		elif isinstance(node, ast.AnnAssign):
			target = node.target
		else:
			continue
		if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
			if isinstance(node.value.value, str):
				out[target.id] = node.value.value
	return out


def _text_of(node: ast.expr, consts: dict[str, str]) -> str | None:
	if isinstance(node, ast.Constant):
		return node.value if isinstance(node.value, str) else None
	if isinstance(node, ast.JoinedStr):
		parts: list[str] = []
		for piece in node.values:
			if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
				parts.append(piece.value)
			elif isinstance(piece, ast.FormattedValue):
				inner = piece.value
				if isinstance(inner, ast.Name):
					parts.append(consts.get(inner.id, ""))
				elif isinstance(inner, ast.Attribute):
					parts.append(consts.get(inner.attr, ""))
				elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
					parts.append(inner.value)
		return "".join(parts)
	return None


def collect_table_uses(source: str, rel: str) -> Iterator[TableUse]:
	"""Every live string in one module that names a retrieval table.

	Both the backticked ``\\`tabChat Context Chunk\\``` form and the bare DocType
	name are matched. The bare form matters because ``frappe.get_all("Chat
	Context Chunk", …)`` is ``get_list(ignore_permissions=True)`` wearing a
	friendlier name — it never sees a permission hook, and nothing at the call
	site looks wrong.
	"""
	tree = ast.parse(source, filename=rel)
	consts = _module_string_constants(tree)
	functions = _function_index(tree)
	docstrings = _docstring_constant_ids(tree)

	for node in ast.walk(tree):
		if not isinstance(node, ast.Constant | ast.JoinedStr) or id(node) in docstrings:
			continue
		text = _text_of(node, consts)
		if not text:
			continue
		where = functions.get(id(node), "<module>")
		for table in _TAB_RE.findall(text):
			if table in RETRIEVAL_TABLES:
				yield TableUse(rel, where, node.lineno, table)
		if text.strip() in RETRIEVAL_TABLES:
			yield TableUse(rel, where, node.lineno, text.strip())


def _builds_sql(func: ast.FunctionDef | ast.AsyncFunctionDef, consts: dict[str, str]) -> bool:
	"""Does this function execute or assemble a query?

	Two detectors, because one is evadable: a call to one of the SQL surfaces,
	or a live string naming a backticked table. The second is what makes a
	helper that returns a ``WHERE`` fragment visible — the fragment builder runs
	nothing, and it is exactly where a hand-rolled membership subquery would go.
	"""
	for node in ast.walk(func):
		if isinstance(node, ast.Call) and _dotted(node.func) in SQL_CALLS:
			return True
		if isinstance(node, ast.Attribute) and _dotted(node)[:2] == ("frappe", "qb"):
			return True
		if isinstance(node, ast.Constant | ast.JoinedStr):
			text = _text_of(node, consts)
			if text and "`tab" in text:
				return True
	return False


def _positional_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
	args = func.args
	return [*args.posonlyargs, *args.args]


def _all_param_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
	args = func.args
	names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
	if args.vararg:
		names.add(args.vararg.arg)
	if args.kwarg:
		names.add(args.kwarg.arg)
	return names


def _retrieval_functions() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, dict[str, str]]]:
	out = []
	if not RETRIEVAL_DIR.is_dir():
		return out
	for path in _python_files(RETRIEVAL_DIR):
		tree = _parse(path)
		consts = _module_string_constants(tree)
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
				out.append((_rel(path), node, consts))
	return out


def _app_table_uses() -> list[TableUse]:
	uses: list[TableUse] = []
	for path in _python_files(APP_DIR):
		rel = _rel(path)
		if rel.startswith("tests/"):
			continue  # a test naming the table is the test that polices it
		uses.extend(collect_table_uses(path.read_text(encoding="utf-8"), rel))
	return uses


# --------------------------------------------------------------------------------- tests


class TestTheAnalyserItself(unittest.TestCase):
	"""Positive and negative controls.

	Before the retrieval package exists these are the ONLY tests here that can
	fail, so they are what makes the suite a fence rather than a comment. They
	stay after it exists, because a compliant codebase exercises none of the
	failure branches and a branch no test exercises rots into always-false.
	"""

	def _uses(self, source: str) -> list[TableUse]:
		return list(collect_table_uses(source, "synthetic.py"))

	def test_a_backticked_index_table_is_detected(self):
		source = (
			"import frappe\n"
			"def leak(user):\n"
			"    return frappe.db.sql('select body from `tabChat Context Chunk`')\n"
		)
		self.assertEqual(
			[(u.function, u.table) for u in self._uses(source)],
			[("leak", "Chat Context Chunk")],
		)

	def test_a_bare_doctype_name_passed_to_get_all_is_detected(self):
		"""``frappe.get_all`` names no table in backticks and still bypasses the
		whole permission stack."""
		source = "import frappe\ndef leak():\n    return frappe.get_all('Chat Room Digest')\n"
		self.assertEqual([u.table for u in self._uses(source)], ["Chat Room Digest"])

	def test_an_f_string_resolves_through_a_module_constant(self):
		"""Naming the table in a constant is the *correct* style — a typo in a
		backticked identifier is a runtime SQL error on a live path, not a test
		failure — so a scan that could not follow one would push authors towards
		inline literals to keep it quiet."""
		source = (
			"import frappe\n"
			"CHUNK = 'Chat Context Chunk'\n"
			"def leak():\n"
			"    return frappe.db.sql(f'select body from `tab{CHUNK}`')\n"
		)
		uses = self._uses(source)
		# Two occurrences, and both are real: the constant's own assignment is a
		# bare table name at module level, and the f-string resolves through it.
		# Reporting both is right — a module that merely *names* one of these
		# tables outside the gate has already lost the argument, whether or not
		# the query is on the same line.
		self.assertEqual({u.table for u in uses}, {"Chat Context Chunk"})
		self.assertEqual({u.function for u in uses}, {"<module>", "leak"})

	def test_prose_about_a_table_in_a_docstring_is_not_a_query(self):
		source = '"""How not to read `tabChat Context Chunk` directly."""\nX = 1\n'
		self.assertEqual(self._uses(source), [])

	def test_an_unrelated_table_is_not_reported(self):
		source = "import frappe\ndef fine():\n    return frappe.get_all('Task')\n"
		self.assertEqual(self._uses(source), [])

	def test_a_sql_builder_is_recognised_without_executing_anything(self):
		"""The fragment builder case: it runs nothing and returns ``WHERE`` text.
		Rule B has to cover it, because a hand-rolled membership subquery would
		live in exactly such a function."""
		source = (
			"def _scope(allowed_rooms):\n"
			"    return '`tabChat Context Chunk`.room in %s' % (allowed_rooms,)\n"
		)
		tree = ast.parse(source)
		func = tree.body[0]
		self.assertTrue(_builds_sql(func, {}))

	def test_a_function_that_touches_no_sql_is_not_a_sql_builder(self):
		tree = ast.parse("def score(a, b):\n    return a + b\n")
		self.assertFalse(_builds_sql(tree.body[0], {}))


class TestRuleATheIndexTablesHaveOneDoor(unittest.TestCase):
	def test_no_module_outside_the_gate_names_a_retrieval_table(self):
		offenders = sorted(
			str(use)
			for use in _app_table_uses()
			if use.file != GATE_REL
			and not use.file.startswith(f"{WRITER_PACKAGE}/")
			and (use.file, use.function) not in NAME_ONLY_EXEMPTIONS
		)
		self.assertFalse(
			offenders,
			"these modules name a Phase 5 index table outside the retrieval gate:\n  "
			+ "\n  ".join(offenders)
			+ f"\n\nEvery read of {sorted(RETRIEVAL_TABLES)} belongs in {GATE_REL}, which "
			"derives the asking user's room set itself, filters on it in the WHERE clause "
			"before any vector is loaded or any score computed, and writes the audit row "
			"before it returns content.\n"
			"This is the module a well-meaning optimisation routes around — 'just this one "
			"query, for speed'. The cost of that shortcut is not a slow page: it is a "
			"correct answer delivered to somebody who is not in the room, with no "
			"exception, no symptom and nothing in any log. Move the query into the gate "
			"and give it a name.\n"
			"If the occurrence genuinely NAMES the table without reading it — a denylist, "
			"an index definition — add the (file, function) pair to NAME_ONLY_EXEMPTIONS "
			"with the reason. That exemption is structural: an exempted pair that executes "
			"any query fails the honesty check below.",
		)

	def test_the_scan_covers_the_whole_app_and_not_just_the_chat_package(self):
		"""A fence around ``chat/`` only would be a fence with a gate in it: the
		module that wants to route around the gate is a reporting helper under
		``api/``, added by somebody who never read the chat README."""
		scanned = {_rel(p) for p in _python_files(APP_DIR)}
		self.assertIn("hooks.py", scanned)
		self.assertTrue(
			any(rel.startswith("api/") for rel in scanned),
			"the walk no longer reaches api/, so Rule A now only polices the package "
			"whose authors already know the rule",
		)


class TestRuleBSqlBuildersTakeTheRoomSetFirst(unittest.TestCase):
	def test_every_sql_builder_takes_allowed_rooms_as_its_first_positional(self):
		offenders: list[str] = []
		for rel, func, consts in _retrieval_functions():
			if func.name in PUBLIC_SYMBOLS:
				continue  # Rule D governs these: they DERIVE the set
			if func.name.startswith("_normalise") or not _builds_sql(func, consts):
				continue
			positional = _positional_params(func)
			if not positional or positional[0].arg != ALLOWED_ROOMS:
				got = positional[0].arg if positional else "<no positional parameters>"
				offenders.append(f"{rel}:{func.lineno} {func.name}() takes {got} first")
				continue
			defaults = func.args.defaults
			required = len(positional) - len(defaults)
			if required < 1:
				offenders.append(f"{rel}:{func.lineno} {func.name}() defaults {ALLOWED_ROOMS}")
		self.assertFalse(
			offenders,
			"these query builders do not take the room set as a required first "
			"positional parameter:\n  "
			+ "\n  ".join(offenders)
			+ f"\n\nEvery private search function takes `{ALLOWED_ROOMS}: frozenset[str]` "
			"first — not a keyword, not defaulted, not optional. The point is the failure "
			"mode: omitting a required positional is a TypeError at the call site, which "
			"is a crash in development. Omitting a defaulted keyword is a query with no "
			"membership filter, which is a leak in production that looks like nothing at "
			"all.",
		)

	def test_no_sql_builder_hides_the_room_set_in_kwargs(self):
		"""``**kwargs`` would satisfy the letter of Rule B and defeat it: the
		parameter becomes optional again, and the call site stops failing."""
		offenders = [
			f"{rel}:{func.lineno} {func.name}()"
			for rel, func, consts in _retrieval_functions()
			if func.name not in PUBLIC_SYMBOLS and _builds_sql(func, consts) and func.args.kwarg
		]
		self.assertFalse(offenders, f"SQL builders taking **kwargs: {offenders}")


class TestRuleCThePackageExportsTwoSymbols(unittest.TestCase):
	def test_the_package_declares_its_public_surface(self):
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet — Rule A is the live fence")
		init = RETRIEVAL_DIR / "__init__.py"
		self.assertTrue(init.is_file(), f"{init} is missing")
		tree = _parse(init)
		declared: set[str] | None = None
		for node in tree.body:
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
			):
				declared = set(ast.literal_eval(node.value))
		self.assertIsNotNone(
			declared,
			"chat/retrieval/__init__.py declares no __all__. The package's whole design is "
			"'one public symbol, reached by one whitelisted method'; without __all__ that "
			"is a convention rather than a statement, and `from … import *` picks up "
			"whatever a helper module happened to leave at module scope.",
		)
		self.assertEqual(
			declared,
			set(PUBLIC_SYMBOLS),
			f"chat/retrieval/ must export exactly {sorted(PUBLIC_SYMBOLS)}. Anything else "
			"in that package is private, and a second public entry point is a second place "
			"the room set can be derived — or not derived.",
		)


class TestRuleDTheEntryPointDerivesItsOwnRoomSet(unittest.TestCase):
	def _entry_points(self):
		return [
			(rel, func)
			for rel, func, _ in _retrieval_functions()
			if func.name in PUBLIC_SYMBOLS and rel == GATE_REL
		]

	def test_no_entry_point_accepts_a_room_set(self):
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet — Rule A is the live fence")
		offenders: list[str] = []
		for rel, func in self._entry_points():
			# The oversight entry points name their rooms, because the audit row has to say what
			# was read and "derive it from their membership" is incoherent for a read that is
			# deliberately not about their membership. Each pays for the exemption in its own
			# test below rather than inheriting it from this list.
			if func.name in {OVERSIGHT_SYMBOL, SEARCH_SYMBOL}:
				continue
			smuggled = sorted(_all_param_names(func) & FORBIDDEN_ENTRY_PARAMS)
			if smuggled:
				offenders.append(f"{rel}:{func.lineno} {func.name}() accepts {smuggled}")
		self.assertFalse(
			offenders,
			"the retrieval entry point accepts a caller-supplied room set:\n  "
			+ "\n  ".join(offenders)
			+ "\n\nThere must be no parameter, keyword or otherwise, by which a caller "
			f"supplies room ids. `{ALLOWED_ROOMS}` is derived inside the entry point from "
			"the calling user's own membership rows, because a parameter is a thing a "
			"caller can get wrong — and the caller here is a model-driven turn assembling "
			"arguments from text.\n"
			f"If the intent is to NARROW the search, the parameter is `{NARROWING_PARAM}`, "
			"which is intersected with the derived set and therefore cannot widen it.",
		)

	def test_the_search_path_pays_for_its_exemption(self):
		"""``search_transcripts`` names its rooms too, and owes the same controls.

		It is the fourth public symbol on a package whose stated design is "one door", and the
		third that takes a caller-supplied room set. Each one of those is a place the set might
		not be bounded, so each is asserted rather than assumed — the alternative is a rule that
		covers the two entry points somebody remembered.
		"""
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet")
		source = (APP_DIR / GATE_REL).read_text(encoding="utf-8")
		func = None
		for node in ast.walk(ast.parse(source)):
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == SEARCH_SYMBOL:
				func = node
		self.assertIsNotNone(func, f"{SEARCH_SYMBOL} is gone from {GATE_REL}")

		params = _all_param_names(func)
		self.assertIn("reason", params, "an oversight search no longer requires a reason")
		self.assertIn("rooms", params, "an oversight search of 'everything' must not be expressible")

		body = ast.dump(func)
		self.assertIn("_has_oversight", body, "the search no longer checks the oversight role")
		self.assertIn(
			"record_or_refuse",
			body,
			"the search no longer writes its audit row through the fail-closed writer. A search "
			"returning hits from twelve rooms is twelve non-participant reads.",
		)
		self.assertIn(
			"MAX_OVERSIGHT_ROOMS",
			body,
			"the room bound is gone, so a caller can name an unbounded set",
		)
		self.assertNotIn(
			"_cap_rooms",
			ast.unparse(ast.Module(body=list(func.body), type_ignores=[])),
			"the search trims the rooms the auditor named instead of refusing. A trimmed search "
			"looks exactly like one that found nothing in those rooms.",
		)

	def test_the_transcript_path_pays_for_its_exemption(self):
		"""``retrieve_transcript`` names its room too, and owes the same compensating controls.

		Rule D does not catch it, because ``room`` singular is not in
		:data:`FORBIDDEN_ENTRY_PARAMS` — and must not be, since ``retrieve(room=…)`` names a
		room *within* the derived set. On this path the room **is** the scope, so the rule's
		reasoning applies even though its mechanism does not, and the controls are asserted
		here rather than left to the fact that the scan happens to look elsewhere.

		A public entry point that no rule governs is how a package with "one door" acquires a
		second one nobody is watching.
		"""
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet")
		source = (APP_DIR / GATE_REL).read_text(encoding="utf-8")
		func = None
		for node in ast.walk(ast.parse(source)):
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == TRANSCRIPT_SYMBOL:
				func = node
		self.assertIsNotNone(func, f"{TRANSCRIPT_SYMBOL} is gone from {GATE_REL}")

		params = _all_param_names(func)
		self.assertIn("reason", params, "a transcript read no longer requires a reason")
		self.assertIn("room", params, "a transcript read of 'everything' must not be expressible")

		body = ast.dump(func)
		self.assertIn(
			"_has_oversight",
			body,
			"the transcript read no longer checks the oversight role, which is what makes it "
			"fail closed while Chat Settings ships the role name blank",
		)
		self.assertIn(
			"record_or_refuse",
			body,
			"the transcript read no longer writes its audit row through the fail-closed "
			"writer. A page of somebody's conversation handed over without a record of who "
			"read it is the failure this whole surface exists to prevent.",
		)
		self.assertNotIn(
			NARROWING_PARAM,
			params,
			f"{TRANSCRIPT_SYMBOL} accepts {NARROWING_PARAM}, which only means something when "
			"there is a derived set to narrow. Here the caller names the room outright.",
		)

	def test_the_oversight_path_pays_for_its_exemption(self):
		"""``retrieve_for_oversight`` is the one entry point that DOES take rooms, and it has
		to earn that.

		Rule D exists because a caller can get a room list wrong, and the caller of
		:func:`retrieve` is a model-driven turn assembling arguments from text. The oversight
		caller is a named human clicking a button, reading rooms they are deliberately not in,
		and the audit row has to say *which* rooms — so "derive it from membership" is not
		merely unhelpful there, it is incoherent: their membership is precisely what the read
		is not about.

		The exemption is therefore conditional on three compensating controls, asserted here
		rather than assumed, because an exemption nobody re-checks is how the strict path ends
		up with the lenient path's signature.
		"""
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet")
		source = (APP_DIR / GATE_REL).read_text(encoding="utf-8")
		tree = ast.parse(source)
		func = None
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == OVERSIGHT_SYMBOL:
				func = node
		self.assertIsNotNone(func, f"{OVERSIGHT_SYMBOL} is gone from {GATE_REL}")

		params = _all_param_names(func)
		self.assertIn(
			"reason",
			params,
			"the oversight read no longer requires a reason. The reason is what makes the "
			"read defensible six months later; without it the audit row records that "
			"somebody read a colleague's rooms and nothing about why.",
		)
		self.assertIn(
			"rooms",
			params,
			"the oversight read no longer names its rooms. An oversight read of 'everything' "
			"is not expressible on purpose — the audit row has to say what was read.",
		)

		body = ast.dump(func)
		self.assertIn(
			"_has_oversight",
			body,
			"the oversight read no longer checks the oversight role. That role is read from "
			"Chat Settings and ships BLANK, which is what makes this path fail closed until "
			"somebody deliberately configures it — a literal role name here would ship it open.",
		)
		self.assertNotIn(
			"restrict_to",
			params,
			"the oversight read now takes restrict_to as well as rooms. Two narrowing "
			"parameters on one function is two chances for a caller to believe the wrong one "
			"is authoritative.",
		)

	def test_the_gate_module_exists_if_the_package_does(self):
		"""A retrieval package without its gate is the failure this whole file
		exists to prevent, arriving as a directory rather than as a query."""
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet")
		self.assertTrue(
			(APP_DIR / GATE_REL).is_file(),
			f"chat/retrieval/ exists but {GATE_REL} does not. Rule A is written against "
			"that exact path, so every index-table query in the package is currently an "
			"offender — or worse, there are none yet and the package is being assembled "
			"somewhere else.",
		)


class TestTheWriterPackagePaysForItsExemption(unittest.TestCase):
	"""The indexing package may name the index tables. These four assertions are the price.

	Without them the exemption reads as "except over there", which is how a fence acquires a
	gate nobody remembers installing.
	"""

	WRITER_DIR = APP_DIR / WRITER_PACKAGE

	def _writer_files(self):
		if not self.WRITER_DIR.is_dir():
			return []
		return [(_rel(path), _parse(path)) for path in _python_files(self.WRITER_DIR)]

	def test_the_exemption_is_justified_in_writing(self):
		self.assertGreaterEqual(len(WRITER_PACKAGE_REASON.strip()), _MIN_REASON)

	def test_the_writer_package_exposes_no_whitelisted_method(self):
		"""The property that matters most: **no HTTP request can ask this package for
		anything.** Everything else here is defence in depth around it."""
		offenders: list[str] = []
		for rel, tree in self._writer_files():
			for node in ast.walk(tree):
				if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
					continue
				for decorator in node.decorator_list:
					target = decorator.func if isinstance(decorator, ast.Call) else decorator
					if ".".join(_dotted(target)[-2:]) == "frappe.whitelist":
						offenders.append(f"{rel}:{node.lineno} {node.name}")
		self.assertFalse(
			offenders,
			f"these functions in {WRITER_PACKAGE}/ are whitelisted: {offenders}.\n\n"
			"This package reads every room with permissions ignored, because it runs on the "
			"scheduler with no session user. A whitelisted method here is a cross-room reader "
			"reachable over HTTP — which is the exact thing the retrieval gate exists to be "
			"the only instance of.",
		)

	def _files_naming_an_index_table(self) -> set[str]:
		"""The subset of the package the public-surface rule applies to.

		Scoped to files that actually name one of the tables, deliberately. ``chunker.py`` is
		pure boundary arithmetic and ``embed.py`` talks to a model — neither can reach a chunk
		row, so requiring their helpers to be private would be ceremony, and ceremony is what
		makes a reviewer stop reading a rule.
		"""
		return {use.file for use in _app_table_uses() if use.file.startswith(f"{WRITER_PACKAGE}/")}

	def test_every_public_function_in_the_writer_package_is_named_and_justified(self):
		"""A public function in a file that reaches the index must be one somebody
		deliberately made public. Anything else is private, so it cannot be imported and
		called from an endpoint."""
		in_scope = self._files_naming_an_index_table()
		offenders: list[str] = []
		for rel, tree in self._writer_files():
			if rel not in in_scope:
				continue
			declared = WRITER_ENTRY_POINTS.get(rel, {})
			for node in tree.body:  # module level only; nested helpers are not a surface
				if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
					continue
				if node.name.startswith("_"):
					continue
				if node.name in declared:
					continue
				offenders.append(f"{rel}:{node.lineno} {node.name}")
		self.assertFalse(
			offenders,
			f"these public functions in {WRITER_PACKAGE}/ are not in WRITER_ENTRY_POINTS: "
			f"{offenders}.\n\n"
			"Either make it private — the default here, because this package's callers are "
			"the scheduler and one seam — or add it with the reason it needs to be reachable "
			"and what it does NOT return.",
		)

	def test_every_declared_entry_point_still_exists(self):
		"""A stale entry justifies nothing today and whatever takes that name tomorrow."""
		live: set[tuple[str, str]] = set()
		for rel, tree in self._writer_files():
			for node in tree.body:
				if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
					live.add((rel, node.name))
		stale = sorted(
			f"{rel}:{name}"
			for rel, names in WRITER_ENTRY_POINTS.items()
			for name in names
			if (rel, name) not in live
		)
		if not self.WRITER_DIR.is_dir():
			self.skipTest(f"{WRITER_PACKAGE}/ does not exist yet")
		self.assertFalse(stale, f"stale WRITER_ENTRY_POINTS: {stale}")

	def test_every_declared_entry_point_states_what_it_does_not_return(self):
		for rel, names in sorted(WRITER_ENTRY_POINTS.items()):
			for name, reason in sorted(names.items()):
				with self.subTest(entry=f"{rel}:{name}"):
					self.assertGreaterEqual(
						len(reason.strip()),
						_MIN_REASON,
						f"WRITER_ENTRY_POINTS[{rel!r}][{name!r}] has no real justification. Say "
						"who calls it, and say what it reads and what it cannot return.",
					)

	#: Entry points that are neither the scheduler's nor the seam's: an operator runs them by
	#: hand, from `bench execute`, to recover from something. Listed **by name**, deliberately,
	#: so that "it is an operator tool" cannot become the cover story under which a cross-room
	#: reader is added to this package. Adding a name here is a reviewable act, and each one
	#: still has to pass every other rule in this file — in particular it may not be whitelisted.
	OPERATOR_ENTRY_POINTS: frozenset = frozenset(
		{
			("chat/indexing/digest.py", "clear_digest_poison"),
			# Phase 6 §4.F's retirement writer. Operator-and-purge, never the scheduler: the
			# whole point of `set_retirement_mark` is that a human or a purge has *just*
			# destroyed a span of messages and is now asserting so. A scheduled version would
			# be a job that retires conversation on a timer, which is the one thing nobody
			# asked for. `sweep_retirement` IS scheduled and is listed with the jobs, because
			# it only finishes deletions an already-written mark authorises.
			("chat/indexing/retire.py", "plan_retirement"),
			("chat/indexing/retire.py", "set_retirement_mark"),
			("chat/indexing/retire.py", "report"),
		}
	)

	def test_the_scheduler_owns_every_entry_point_but_the_seam_writer(self):
		"""The declared surface must actually be the scheduler's, not merely claimed to be.

		``invalidate_span`` is the one exception and is checked by name: it is called
		synchronously from the edit and delete paths, because a digest covering a message
		somebody just deleted has to stop being served *now*.
		"""
		if not self.WRITER_DIR.is_dir():
			self.skipTest(f"{WRITER_PACKAGE}/ does not exist yet")
		hooks = (APP_DIR / "hooks.py").read_text(encoding="utf-8")
		for rel, names in sorted(WRITER_ENTRY_POINTS.items()):
			module = rel.removesuffix(".py").replace("/", ".")
			for name in sorted(names):
				if name == "invalidate_span":
					self.assertIn(
						"invalidate.invalidate_span",
						(APP_DIR / "chat" / "seams.py").read_text(encoding="utf-8"),
						"invalidate_span is declared as the seam's writer but chat/seams.py "
						"does not call it. Then nothing invalidates a digest on an edit or a "
						"delete, and a summary of a deleted message keeps being served — "
						"silently, because a stale digest still answers.",
					)
					continue
				if (rel, name) in self.OPERATOR_ENTRY_POINTS:
					# Assert the category is honest rather than skipping: an operator tool that
					# HAS been wired to the scheduler is a job nobody knows is running, on a
					# package whose whole exemption rests on knowing exactly what runs here.
					self.assertNotIn(
						f"erpnext_enhancements.{module}.{name}",
						hooks,
						f"{rel}:{name} is declared as an operator-only entry point but "
						"hooks.py schedules it. Either it is a scheduler job and belongs in "
						"the list above with the rest, or it is running unattended and "
						"nobody has said so.",
					)
					continue
				self.assertIn(
					f"erpnext_enhancements.{module}.{name}",
					hooks,
					f"{rel}:{name} is declared as a scheduler entry point and hooks.py does "
					"not register it. Either it is dead code, or the index has silently "
					"stopped being maintained — and the second one looks exactly like chat "
					"being quiet.",
				)


class TestRuleETheWireSurfaceIsOneMethodAndItGates(unittest.TestCase):
	"""The package is reachable over HTTP by exactly one method, and it runs as the session.

	Asserted by AST rather than by import, unlike ``test_chat_api_contracts``: importing this
	package's endpoint pulls in the gate, which imports ``frappe``, and the whole reason the
	pure modules are pure is so this suite needs no stub set.
	"""

	def _whitelisted(self):
		found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
		for rel, func, _ in _retrieval_functions():
			for decorator in func.decorator_list:
				target = decorator.func if isinstance(decorator, ast.Call) else decorator
				if ".".join(_dotted(target)[-2:]) == "frappe.whitelist":
					found.append((rel, func))
		return found

	def test_there_is_exactly_one_whitelisted_method(self):
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet")
		found = self._whitelisted()
		self.assertEqual(
			len(found),
			1,
			f"chat/retrieval exposes {[f'{rel}:{fn.name}' for rel, fn in found]} over the wire. "
			"There is one method on purpose: every additional one is another place the acting "
			"identity is resolved, and they only have to disagree once.",
		)

	def test_the_whitelisted_method_gates_before_it_reads(self):
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet")
		for rel, func in self._whitelisted():
			body = ast.dump(func)
			self.assertIn(
				"require_session",
				body,
				f"{rel}:{func.name} is whitelisted but never calls require_session. That gate "
				"resolves the caller, refuses Guest and enforces the pilot whitelist; without "
				"it the endpoint is reachable by any signed-in user.",
			)

	def test_the_whitelisted_method_takes_no_user_parameter(self):
		"""A ``user`` argument on a whitelisted method is an impersonation parameter however
		carefully it is documented — and the caller here is a model-driven turn assembling
		arguments from text."""
		if not RETRIEVAL_DIR.is_dir():
			self.skipTest("chat/retrieval/ does not exist yet")
		for rel, func in self._whitelisted():
			self.assertNotIn(
				"user",
				_all_param_names(func),
				f"{rel}:{func.name} accepts a user parameter. The acting person is the "
				"session, full stop.",
			)


class TestTheHandlerCannotSeeTheOrigin(unittest.TestCase):
	"""Locked decision #4, asserted structurally rather than behaviourally.

	The strongest form — two real payloads through two real normalisers producing identical
	handler input — needs a bench, and CI has none. What runs here is the property that makes
	the bench test almost redundant: **the envelope has no origin field**, so a branch on
	origin cannot be written, and ``origin`` reaches the handler only as a parameter that is
	written to the log and read nowhere else.

	A reviewer can miss a branch. A reviewer cannot miss a branch that does not compile.
	"""

	INVOKE_DIR = APP_DIR / "chat" / "invoke"
	ENVELOPE_REL = "chat/invoke/envelope.py"
	HANDLER_REL = "chat/invoke/handler.py"

	def _handler_function(self):
		tree = _parse(APP_DIR / self.HANDLER_REL)
		for node in ast.walk(tree):
			if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "handle":
				return node
		return None

	def test_the_envelope_dataclass_declares_no_origin_field(self):
		if not self.INVOKE_DIR.is_dir():
			self.skipTest("chat/invoke/ does not exist yet")
		tree = _parse(APP_DIR / self.ENVELOPE_REL)
		fields: set[str] = set()
		for node in ast.walk(tree):
			if not isinstance(node, ast.ClassDef) or node.name != "Envelope":
				continue
			for item in node.body:
				if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
					fields.add(item.target.id)
		self.assertTrue(fields, "the Envelope dataclass has no annotated fields at all")
		for banned in ("origin", "source", "client", "space"):
			self.assertNotIn(
				banned,
				fields,
				f"Envelope declares a {banned!r} field. Decision #4 says a mention behaves "
				"identically from either client, and the way that is guaranteed rather than "
				"hoped for is that the handler has nothing to branch on. Origin is recorded — "
				"the normalisers write it to Triton Invocation Log — it just never travels "
				"with the question.",
			)

	def test_the_handler_reads_origin_only_to_log_it(self):
		"""``origin`` is a parameter of ``handle`` so the log row can carry it. It must be
		read exactly once, in the call that opens that row."""
		if not self.INVOKE_DIR.is_dir():
			self.skipTest("chat/invoke/ does not exist yet")
		func = self._handler_function()
		self.assertIsNotNone(func, "chat/invoke/handler.py has no handle()")
		reads = [
			node
			for node in ast.walk(func)
			if isinstance(node, ast.Name) and node.id == "origin" and isinstance(node.ctx, ast.Load)
		]
		self.assertLessEqual(
			len(reads),
			1,
			f"handle() reads `origin` {len(reads)} times. It may be read once, to write it to "
			"the invocation log. A second read is a decision being made on which client "
			"asked, which is exactly what decision #4 forbids — and the symptom would be two "
			"coworkers getting different answers to the same question for a reason neither "
			"of them can see.",
		)

	def test_the_handler_never_compares_against_an_origin_name(self):
		if not self.INVOKE_DIR.is_dir():
			self.skipTest("chat/invoke/ does not exist yet")
		source = (APP_DIR / self.HANDLER_REL).read_text(encoding="utf-8")
		tree = ast.parse(source)
		docstrings = _docstring_constant_ids(tree)
		offenders = [
			node.value
			for node in ast.walk(tree)
			if isinstance(node, ast.Constant)
			and isinstance(node.value, str)
			and id(node) not in docstrings
			and node.value in ("Google Chat", "SPA")
		]
		self.assertFalse(
			offenders,
			f"handler.py contains the literal(s) {offenders}. Those are origin names, and the "
			"handler has no business knowing them.",
		)


class TestTheExemptionsAreHonest(unittest.TestCase):
	"""An exemption list nobody prunes is a hole nobody is watching, and this one
	sits directly on the security boundary."""

	def test_every_exemption_states_why_the_name_is_not_a_read(self):
		for key, reason in sorted(NAME_ONLY_EXEMPTIONS.items()):
			with self.subTest(exemption=key):
				self.assertGreaterEqual(
					len(reason.strip()),
					_MIN_REASON,
					f"NAME_ONLY_EXEMPTIONS[{key!r}] has no real justification. Two things "
					"have to be true and the entry has to say both: the occurrence names "
					"the table without reading it, and nothing in that scope executes a "
					"query.",
				)

	def test_no_exempted_scope_executes_a_query(self):
		"""The exemption is granted on the claim 'this is a name, not a read'. This
		is what makes that claim structural instead of trusted."""
		offenders: list[str] = []
		for rel, function in sorted(NAME_ONLY_EXEMPTIONS):
			path = APP_DIR / rel
			if not path.is_file():
				continue  # reported as stale by the test below
			tree = _parse(path)
			functions = _function_index(tree)
			for node in ast.walk(tree):
				if not isinstance(node, ast.Call):
					continue
				if functions.get(id(node), "<module>") != function:
					continue
				dotted = _dotted(node.func)
				if dotted in SQL_CALLS:
					offenders.append(f"{rel}:{node.lineno} {function}() calls {'.'.join(dotted)}")
		self.assertFalse(
			offenders,
			"these NAME_ONLY_EXEMPTIONS scopes now execute a query:\n  "
			+ "\n  ".join(offenders)
			+ "\n\nThe exemption was granted on the claim that the occurrence NAMES a "
			"retrieval table without reading it. That is no longer true. Either move the "
			"query into the gate, or remove the exemption and justify the read properly.",
		)

	def test_no_exemption_is_stale(self):
		"""A pair that matches no occurrence exempts nothing today and whatever
		takes that name tomorrow."""
		live = {(use.file, use.function) for use in _app_table_uses()}
		stale = sorted(key for key in NAME_ONLY_EXEMPTIONS if key not in live)
		self.assertFalse(
			stale,
			f"these NAME_ONLY_EXEMPTIONS entries match no occurrence: {stale}. A function "
			"was renamed, moved or deleted. Delete the entry in the same commit — nobody "
			"re-reads an exemption they did not have to write.",
		)


class TestTheFenceIsNotVacuous(unittest.TestCase):
	def test_the_walk_found_python_files(self):
		self.assertGreater(
			len(_python_files(APP_DIR)),
			100,
			"the app-wide walk found almost nothing. Fix the path; do not delete the test.",
		)

	def test_the_retrieval_tables_are_named_and_not_empty(self):
		self.assertTrue(RETRIEVAL_TABLES)
		for table in RETRIEVAL_TABLES:
			self.assertTrue(table.startswith("Chat "), table)

	def test_rule_b_is_exercised_by_a_synthetic_violation(self):
		"""Rule B iterates a package that may not exist yet, and iterating
		nothing passes. This proves the predicate it iterates with can say no."""
		source = "def _search(query, allowed_rooms):\n    return f'select 1 from `tabChat Context Chunk`'\n"
		func = ast.parse(source).body[0]
		self.assertTrue(_builds_sql(func, {}))
		self.assertEqual(_positional_params(func)[0].arg, "query")

	def test_rule_d_is_exercised_by_a_synthetic_violation(self):
		source = "def retrieve(user, allowed_rooms=None):\n    return []\n"
		func = ast.parse(source).body[0]
		self.assertTrue(_all_param_names(func) & FORBIDDEN_ENTRY_PARAMS)


class TestTheWriterPackageIsNotReachableFromAnEndpoint(unittest.TestCase):
	"""The fourth property of ``WRITER_PACKAGE_REASON``, asserted rather than only stated.

	The writer package's exemption from Rule A rests on four properties. Three were already
	asserted here. The fourth -- *"no endpoint importing it"* -- was prose, and prose is how a
	documented invariant erodes: v1.295.0 put a shared pure module inside ``chat/indexing/``,
	and wiring the retrieval gate to it would have made the gate import the writer package for
	the sake of a WHERE fragment. The module moved to the package root; this is what stops the
	next one.

	**Why it matters beyond tidiness.** The exemption says the indexer may read every room
	because nothing user-facing can ask it to. An endpoint importing it does not by itself break
	that, but it is the first step of every way it could -- and the property is cheap to keep and
	expensive to recover once several modules depend on the shortcut.
	"""

	def test_no_whitelisted_chat_module_imports_the_writer_package(self):
		import ast
		import pathlib

		from erpnext_enhancements.chat import endpoints as endpoint_inventory

		chat_root = pathlib.Path(endpoint_inventory.CHAT_PACKAGE).resolve()
		writer = WRITER_PACKAGE.split("/")[-1]
		endpoint_files = {
			endpoint.relpath.replace(chr(92), "/")
			for endpoint in endpoint_inventory.discover().values()
		}
		self.assertTrue(endpoint_files, "the endpoint inventory found nothing; the scan is broken")

		offenders = []
		for rel in sorted(endpoint_files):
			if rel.startswith(writer + "/"):
				continue
			path = chat_root / rel
			try:
				tree = ast.parse(path.read_text(encoding="utf-8"))
			except (OSError, SyntaxError):  # pragma: no cover
				continue
			for node in ast.walk(tree):
				module = ""
				if isinstance(node, ast.ImportFrom) and node.module:
					module = node.module
				elif isinstance(node, ast.Import):
					module = ",".join(alias.name for alias in node.names)
				if "chat." + writer in module:
					offenders.append(rel + " imports " + module)

		self.assertFalse(
			offenders,
			"these modules contain a whitelisted endpoint AND import the writer package: "
			+ "; ".join(offenders)
			+ ". "
			+ WRITER_PACKAGE_REASON
			+ " If the import is for shared PURE logic that several packages need and none owns, "
			"move that module to the chat package root beside permissions.py -- which is the "
			"precedent, and what chat/retire_rules.py did after shipping in the wrong place.",
		)


class TestRuleFTheContentHooksScopeToMembershipForEveryone(unittest.TestCase):
	"""Rule F, added in v1.301.0: **no content permission hook grants an unrestricted scope.**

	Until then, eight hooks in ``chat/permissions.py`` returned ``""`` (and ``True``) to any
	holder of ``Chat Settings.admin_oversight_role``, and to ``Administrator``. Tracing every
	consumer found the audited oversight path used none of it — ``retrieve_for_oversight`` gates
	on ``_has_oversight`` itself and reads through raw SQL, touching neither
	``permission_query_conditions`` nor ``has_permission`` — while three things that were not
	audited at all used all of it: ``rooms.get_room`` (which returned ``last_message_preview``
	and the roster to a non-member, from the SPA, with no reason collected and no row written),
	the socket join, and the desk list / report / search-link paths.

	So the branch is gone, and this is the guard. It asserts **the phrase appears nowhere in the
	eight functions** rather than checking behaviour, because the failure being prevented is
	somebody re-adding a convenience shortcut to one hook in six months — an edit that looks
	entirely reasonable in isolation and restores the leak in full.

	The two audit-table hook pairs are exempt by name: an auditor who cannot read the audit log
	is not an auditor.
	"""

	#: The eight content hooks. Named rather than derived, so deleting one from the module fails
	#: this suite instead of silently shrinking its coverage.
	CONTENT_HOOKS = (
		"chat_room_query",
		"chat_room_member_query",
		"chat_message_query",
		"chat_attachment_query",
		"chat_room_has_permission",
		"chat_room_member_has_permission",
		"chat_message_has_permission",
		"chat_attachment_has_permission",
	)

	#: Exempt, and the exemption is the whole point of the role.
	AUDIT_HOOKS = ("chat_audit_log_query", "chat_retrieval_audit_query")

	def _body(self, name):
		"""A function's source minus its docstring.

		That module discusses the hatch at length, and a text scan that cannot tell an
		explanation from a branch is one that gets satisfied by deleting the explanation. That
		mistake has been made repeatedly across this series, which is why this helper exists.
		"""
		import ast as _ast
		import pathlib as _pathlib

		path = _pathlib.Path(__file__).resolve().parents[1] / "chat" / "permissions.py"
		text = path.read_text(encoding="utf-8")
		for node in _ast.walk(_ast.parse(text)):
			if isinstance(node, _ast.FunctionDef) and node.name == name:
				src = _ast.get_source_segment(text, node) or ""
				body = node.body
				if body and isinstance(body[0], _ast.Expr) and isinstance(body[0].value, _ast.Constant):
					doc = _ast.get_source_segment(text, body[0].value)
					if doc:
						src = src.replace(doc, "", 1)
				return src
		raise AssertionError(f"{name}() not found in chat/permissions.py — re-derive this scan")

	def test_the_scan_finds_all_eight(self):
		"""Otherwise every assertion below is vacuous."""
		for name in self.CONTENT_HOOKS:
			self.assertTrue(self._body(name).strip(), f"{name} has an empty body")

	def test_no_content_hook_consults_the_oversight_role(self):
		for name in self.CONTENT_HOOKS:
			self.assertNotIn(
				"_has_oversight",
				self._body(name),
				f"{name} consults the oversight role again. These hooks scope to active membership "
				"for every identity; the audited door is chat/governance/viewer.py via "
				"membership_filter_sql(allow_oversight=True), which only chat/retrieval/gate.py "
				"may pass.",
			)

	def test_no_content_hook_special_cases_administrator(self):
		"""Administrator is not a person. A shared login destroys the attribution a transcript
		exists to provide — the same reason ``retrieve_for_oversight`` refuses it."""
		for name in self.CONTENT_HOOKS:
			self.assertNotIn(
				'"Administrator"', self._body(name), f"{name} special-cases Administrator"
			)

	def test_no_content_query_hook_returns_an_unrestricted_scope(self):
		"""An empty string appended to a WHERE clause is *no filter*. It is the one return value
		from this module that can leak every room in the company; ``1 = 0`` is the safe empty."""
		for name in self.CONTENT_HOOKS:
			if not name.endswith("_query"):
				continue
			self.assertNotIn(
				'return ""',
				self._body(name),
				f"{name} returns an unrestricted scope",
			)

	def test_the_audit_tables_are_still_readable_by_the_auditor(self):
		"""Asserted so a future sweep of this rule cannot take the role's own log away from it
		and call that an improvement.

		Checks the delegation to ``_may_read_audit`` rather than an inline ``_has_oversight``.
		The audit hooks route the whole question through one predicate, which is exactly why the
		rule above can forbid that phrase in the content hooks without also taking the auditor's
		own log away. The first version of this assertion looked for the inline call and failed —
		correctly, since it was asserting a shape the module deliberately does not use.
		"""
		for name in self.AUDIT_HOOKS:
			self.assertIn(
				"_may_read_audit", self._body(name), f"{name} no longer admits the auditor"
			)

	def test_the_auditors_predicate_is_the_one_that_knows_the_role(self):
		"""And that predicate must still consult the configured role, or the delegation above is
		satisfied by a function that admits nobody."""
		self.assertIn("_has_oversight", self._body("_may_read_audit"))


if __name__ == "__main__":  # pragma: no cover
	unittest.main()
