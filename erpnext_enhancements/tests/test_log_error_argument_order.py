# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every Error Log row this app writes is titled with its title. Bench-free.

Frappe v16's ``log_error`` does not decide which argument is the title by position.
It decides by **content** (``frappe/utils/error.py``, unchanged from 16.31.0 to at
least 16.36.0)::

    def log_error(title=None, message=None, reference_doctype=None, reference_name=None, *, defer_insert=False):
        traceback = None
        if message:
            if "\\n" in title:  # traceback sent as title
                traceback, title = title, message
            else:
                traceback = message

Frappe's own comment calls it a hack that "tries to be smart about whats a title (single
line ;-))". So the legacy call ``frappe.log_error(message, title)`` works only when
``message`` contains a newline. A traceback always does. A one-line sentence does not,
and then the row comes out backwards: the sentence is the title (``Error Log.method``,
cut at 140 characters by ``ErrorLog.validate``, which puts the whole sentence at the top
of the body) and the intended title is the body.

Prod had 77 rows like that in the 90 days to 2026-09-29, for example a row titled
``Bridge token failed: 503 {"message":"database temporarily unavailable"...`` whose body
was just ``Triton Chat``. Such a row cannot be found by its title, and every report or
alert that filters on the Error Log title misses it. v1.567.1 moved 87 direct calls, and
the two forwards inside ``log_error_throttled``, to keywords.

The rules
---------------------------------------------------------------------------

1. **A call that passes both a title and a message passes them by keyword**, or its
   first positional argument is provably multi-line: a traceback
   (``frappe.get_traceback()``, ``traceback.format_exc()``), a literal containing
   ``\\n``, or a name assigned one of those just before the call (or only ever, in
   that function). That keeps the ~310 legacy ``(frappe.get_traceback(), title)``
   calls, which the heuristic swaps correctly, and refuses the one-line ones it
   cannot.
2. **Keywords do not bypass the heuristic.** ``log_error(title=t, message=m)`` still
   swaps when ``t`` contains a newline, so a title passed with a message must not
   interpolate a caught exception, whose text can have any number of lines. Put the
   exception in the message.
3. ``log_error(message=m)`` with no title raises ``TypeError`` on v16 (``"\\n" in
   None``), and ``log_error(m, title=t)`` raises for a duplicate ``title``.

A single positional argument, ``frappe.log_error("Something happened")``, is the title,
with the current traceback as the body. Nothing can be swapped, so it is allowed.

``utils/error_throttle.log_error_throttled(message, title)`` keeps its own
(message, title) signature and forwards by keyword, so its callers are held to rule 2
only.

Run: python -m unittest erpnext_enhancements.tests.test_log_error_argument_order
"""

import ast
import functools
import textwrap
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

#: Skipped: the suites stub ``frappe.log_error`` and call it in every shape on purpose.
SKIP_PARTS = frozenset({"tests", "node_modules", "__pycache__", ".git"})

#: Calls that return a traceback, which always contains a newline.
TRACEBACK_FUNCS = frozenset({"get_traceback", "format_exc", "format_exception"})

#: Helpers that hand back (a bounded or redacted copy of) their first argument. A
#: traceback's first line is ``Traceback (most recent call last):``, so the first 500
#: characters that ``error_snippet`` keeps still contain a newline.
PASS_THROUGH = frozenset({"str", "cstr", "error_snippet", "_redact"})

#: Wrappers with a (message, title) signature that forward to ``frappe.log_error``.
WRAPPERS = frozenset({"log_error_throttled"})

THROTTLE = APP / "utils" / "error_throttle.py"


def _name(func):
	if isinstance(func, ast.Attribute):
		return func.attr
	if isinstance(func, ast.Name):
		return func.id
	return None


class _Scan:
	"""One parsed module and the parent links the rules need."""

	def __init__(self, source):
		self.tree = ast.parse(source)
		self.parents = {}
		for node in ast.walk(self.tree):
			for child in ast.iter_child_nodes(node):
				self.parents[child] = node
		self.local_log_error = {
			alias.asname or alias.name
			for node in ast.walk(self.tree)
			if isinstance(node, ast.ImportFrom) and node.module in ("frappe", "frappe.utils.error")
			for alias in node.names
			if alias.name == "log_error"
		}

	def enclosing(self, node, kinds):
		while node in self.parents:
			node = self.parents[node]
			if isinstance(node, kinds):
				return node
		return None

	def exception_names(self, node):
		"""Names bound by every ``except ... as name`` around ``node``."""
		names = set()
		while node in self.parents:
			node = self.parents[node]
			if isinstance(node, ast.ExceptHandler) and node.name:
				names.add(node.name)
		return names

	def multiline(self, expr, at, seen=frozenset()):
		"""True when ``expr`` always evaluates to text containing a newline."""
		if isinstance(expr, ast.Constant):
			return isinstance(expr.value, str) and "\n" in expr.value
		if isinstance(expr, ast.JoinedStr):
			return any(
				(isinstance(part, ast.Constant) and "\n" in str(part.value))
				or (isinstance(part, ast.FormattedValue) and self.multiline(part.value, at, seen))
				for part in expr.values
			)
		if isinstance(expr, ast.BinOp):
			if isinstance(expr.op, ast.Add):
				return self.multiline(expr.left, at, seen) or self.multiline(expr.right, at, seen)
			if isinstance(expr.op, ast.Mod):
				return self.multiline(expr.left, at, seen)
			return False
		if isinstance(expr, ast.Call):
			name = _name(expr.func)
			if name in TRACEBACK_FUNCS:
				return True
			if name in PASS_THROUGH and expr.args:
				return self.multiline(expr.args[0], at, seen)
			if name == "format" and isinstance(expr.func, ast.Attribute):
				return self.multiline(expr.func.value, at, seen)
			return False
		if isinstance(expr, (ast.Name, ast.Subscript)):
			key = ast.unparse(expr)
			if key in seen:
				return False
			value = self._assigned_just_before(key, at)
			if value is not None:
				return self.multiline(value, at, seen | {key})
			if isinstance(expr, ast.Name):
				return self._local_is_multiline(expr.id, at, seen)
		return False

	def _assigned_just_before(self, key, at):
		"""The value ``key`` was last given earlier in the call's own block, if that is plain.

		``error = frappe.get_traceback()`` on the line above the call is the common shape.
		Anything else in between that could rebind ``key`` makes it unknown (None).
		"""
		stmt = at
		while stmt in self.parents and not isinstance(stmt, ast.stmt):
			stmt = self.parents[stmt]
		block = next(
			(
				body
				for field in ("body", "orelse", "finalbody")
				for body in [getattr(self.parents.get(stmt), field, None)]
				if isinstance(body, list) and stmt in body
			),
			None,
		)
		if block is None:
			return None
		base = key.split("[", 1)[0]
		for prev in reversed(block[: block.index(stmt)]):
			if (
				isinstance(prev, ast.Assign)
				and len(prev.targets) == 1
				and ast.unparse(prev.targets[0]) == key
			):
				return prev.value
			if any(
				isinstance(node, (ast.Name, ast.Subscript, ast.Attribute))
				and isinstance(node.ctx, ast.Store)
				and ast.unparse(node).split("[", 1)[0] == base
				for node in ast.walk(prev)
			):
				return None
		return None

	def _local_is_multiline(self, name, at, seen):
		"""A local that its function binds only by plain assignments of multi-line text.

		Any other binding -- a parameter, a loop or ``with`` target, ``+=``, a walrus --
		makes it unprovable, and unprovable counts as one line.
		"""
		scope = self.enclosing(at, (ast.FunctionDef, ast.AsyncFunctionDef)) or self.tree
		if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
			params = scope.args.posonlyargs + scope.args.args + scope.args.kwonlyargs
			if name in {a.arg for a in params}:
				return False
		stores = [
			node
			for node in ast.walk(scope)
			if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Store)
		]
		values = []
		for node in ast.walk(scope):
			if isinstance(node, ast.Assign) and len(node.targets) == 1:
				target = node.targets[0]
			elif isinstance(node, ast.AnnAssign) and node.value is not None:
				target = node.target
			else:
				continue
			if isinstance(target, ast.Name) and target.id == name:
				values.append(node.value)
		return (
			bool(values)
			and len(values) == len(stores)
			and all(self.multiline(value, at, seen | {name}) for value in values)
		)

	def interpolates(self, expr, names):
		"""True when ``expr`` can contain newline-bearing text: a ``\\n`` literal or one of ``names``."""
		if expr is None:
			return False
		for node in ast.walk(expr):
			if isinstance(node, ast.Constant) and isinstance(node.value, str) and "\n" in node.value:
				return True
			if isinstance(node, ast.Name) and node.id in names:
				return True
		return False

	def problems(self):
		"""Yield ``(lineno, rule, why)`` for every call that breaks a rule."""
		for node in ast.walk(self.tree):
			if not isinstance(node, ast.Call):
				continue
			func = node.func
			direct = (isinstance(func, ast.Attribute) and func.attr == "log_error") or (
				isinstance(func, ast.Name) and func.id in self.local_log_error
			)
			wrapper = not direct and _name(func) in WRAPPERS
			if not (direct or wrapper):
				continue
			kw = {k.arg: k.value for k in node.keywords if k.arg}
			args = node.args
			caught = self.exception_names(node)
			if wrapper:
				title = args[1] if len(args) > 1 else kw.get("title")
				if self.interpolates(title, caught):
					yield node.lineno, 2, "log_error_throttled title interpolates a caught exception"
				continue
			if "message" in kw and "title" not in kw and not args:
				yield node.lineno, 3, "message= without a title raises TypeError on v16"
				continue
			if args and "title" in kw:
				yield node.lineno, 3, "positional first argument plus title= raises TypeError"
				continue
			if len(args) > 1:
				# The legacy (message, title) pair. v16 swaps it only when the first
				# argument has a newline, and then the second is the title whatever it
				# holds.
				if not self.multiline(args[0], node):
					yield node.lineno, 1, "positional (message, title) whose first argument may be one line"
				continue
			if "message" not in kw:
				continue  # a lone title: there is nothing to swap it with
			title = args[0] if args else kw.get("title")
			if self.interpolates(title, caught):
				yield node.lineno, 2, "a title passed with a message interpolates a caught exception"


def _sources():
	for path in sorted(APP.rglob("*.py")):
		if SKIP_PARTS.intersection(path.parts):
			continue
		yield path


def problems_in(source):
	return list(_Scan(textwrap.dedent(source)).problems())


@functools.cache
def _app_scans():
	scans = {}
	for path in _sources():
		try:
			scans[path] = _Scan(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			continue
	return scans


def app_problems():
	return [
		f"{path.relative_to(APP).as_posix()}:{lineno} rule {rule}: {why}"
		for path, scan in _app_scans().items()
		for lineno, rule, why in scan.problems()
	]


class TestTheAppLogsTitlesAsTitles(unittest.TestCase):
	def test_no_log_error_call_can_come_out_backwards(self):
		found = app_problems()
		self.assertEqual(
			found,
			[],
			"v16's log_error picks the title by content: the first argument is the title unless "
			"it contains a newline. Pass title= and message= by keyword, and keep the "
			"exception out of the title:\n  " + "\n  ".join(found),
		)

	def test_the_scan_reaches_the_app(self):
		"""An absence assertion over an empty corpus passes forever."""
		scans = _app_scans()
		self.assertIn(THROTTLE.resolve(), {p.resolve() for p in scans})
		self.assertGreater(len(scans), 500, "source walk collapsed -- the rule proves nothing")
		calls = sum(
			1
			for scan in scans.values()
			for node in ast.walk(scan.tree)
			if isinstance(node, ast.Call) and _name(node.func) == "log_error"
		)
		self.assertGreater(calls, 500, "found almost no log_error calls -- the matcher is broken")


class TestTheRuleItself(unittest.TestCase):
	"""The scanner must refuse the shapes that came out backwards on prod, and pass the rest."""

	def test_a_positional_one_line_message_is_refused(self):
		self.assertEqual(
			[rule for _, rule, _ in problems_in('frappe.log_error("Bridge token failed", "Triton Chat")')],
			[1],
		)

	def test_a_positional_f_string_is_refused(self):
		src = """
		def f(resp):
			frappe.log_error(f"Bridge token failed: {resp.status_code} {resp.text[:500]}", "Triton Chat")
		"""
		self.assertEqual([rule for _, rule, _ in problems_in(src)], [1])

	def test_an_implicitly_concatenated_sentence_is_refused(self):
		src = """
		frappe.log_error(
			"Payroll seed: no Employee record matched these names. "
			"They will export with a blank Emp Num.",
			"Payroll Employee Number Seed",
		)
		"""
		self.assertEqual([rule for _, rule, _ in problems_in(src)], [1])

	def test_a_title_that_interpolates_the_exception_is_refused_even_by_keyword(self):
		src = """
		def f():
			try:
				pass
			except Exception as e:
				frappe.log_error(title=f"Initial task fetch failed: {e}", message=frappe.get_traceback())
		"""
		self.assertEqual([rule for _, rule, _ in problems_in(src)], [2])

	def test_the_legacy_title_first_pair_with_an_exception_is_refused(self):
		src = """
		def f():
			try:
				pass
			except Exception as e:
				frappe.log_error(f"Initial task fetch failed: {e}", frappe.get_traceback())
		"""
		self.assertEqual([rule for _, rule, _ in problems_in(src)], [1])

	def test_calls_that_raise_on_v16_are_refused(self):
		self.assertEqual([rule for _, rule, _ in problems_in('frappe.log_error(message="m")')], [3])
		self.assertEqual([rule for _, rule, _ in problems_in('frappe.log_error("m", title="t")')], [3])

	def test_a_throttled_title_with_the_exception_is_refused(self):
		src = """
		def f():
			try:
				pass
			except Exception as exc:
				log_error_throttled(frappe.get_traceback(), f"Sync failed: {exc}")
		"""
		self.assertEqual([rule for _, rule, _ in problems_in(src)], [2])

	def test_an_imported_log_error_is_checked_too(self):
		src = """
		from frappe import log_error as le
		le("one line", "Title")
		"""
		self.assertEqual([rule for _, rule, _ in problems_in(src)], [1])

	def test_the_shapes_v16_gets_right_pass(self):
		src = """
		def f(tb_text):
			try:
				pass
			except Exception as e:
				frappe.log_error(frappe.get_traceback(), "Legacy traceback first")
				frappe.log_error(traceback.format_exc(), f"Sync of {doc} failed")
				frappe.log_error(error_snippet(frappe.get_traceback()), "Bounded")
				frappe.log_error(f"{e}\\n{frappe.get_traceback()}", "Literal newline")
				frappe.log_error("Context:\\n" + str(e), "Concatenated")
				frappe.log_error(title="Keyword", message=f"one line {e}")
				frappe.log_error(title=f"Sync of {doc} failed", message=frappe.get_traceback())
				frappe.log_error(f"Lone title {e}")
				frappe.log_error(title="Title only")
				tb = frappe.get_traceback()
				frappe.log_error(tb, "Held in a local")
				log_error_throttled(f"one line {e}", "Throttled")
		"""
		self.assertEqual(problems_in(src), [])

	def test_a_traceback_assigned_just_before_the_call_passes(self):
		"""The shape of ``kpi_dashboards/snapshots.py`` and ``offsite_backup/backup.py``: the
		name is set to ``None`` earlier, so only the assignment right above the call counts."""
		src = """
		def f(info):
			error = None
			try:
				pass
			except Exception:
				error = frappe.get_traceback()
				frappe.log_error(error, "KPI aggregator failed")
				info["action"] = "Could not list the backup folder."
				info["error"] = _redact(frappe.get_traceback())
				frappe.log_error(info["error"], "Offsite Backup: prune listing failed")
		"""
		self.assertEqual(problems_in(src), [])

	def test_a_local_rebound_to_one_line_text_is_not_trusted(self):
		src = """
		def f():
			tb = frappe.get_traceback()
			tb = "short"
			frappe.log_error(tb, "Title")
		"""
		self.assertEqual([rule for _, rule, _ in problems_in(src)], [1])


class TestTheThrottledWrapperForwardsByKeyword(unittest.TestCase):
	"""``log_error_throttled(message, title)`` was the root of four backwards call sites."""

	def test_every_forward_names_its_arguments(self):
		tree = ast.parse(THROTTLE.read_text(encoding="utf-8"))
		calls = [
			node
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
			and isinstance(node.func, ast.Attribute)
			and node.func.attr == "log_error"
		]
		self.assertGreaterEqual(len(calls), 3)
		for call in calls:
			self.assertEqual(call.args, [], f"line {call.lineno} forwards positionally")
			self.assertEqual({k.arg for k in call.keywords}, {"title", "message"}, f"line {call.lineno}")


if __name__ == "__main__":
	unittest.main()
