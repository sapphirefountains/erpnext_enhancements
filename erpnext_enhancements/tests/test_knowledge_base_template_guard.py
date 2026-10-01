# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Templates, scripts and print formats never read knowledge base drafts (2026-10-01). Bench-free.

``knowledge_base/template_guard.py`` wraps ``Database.sql`` so a query naming the drafts' table is
refused while Frappe renders a stored template (``in_render_safe_exec``), runs a script
(``in_safe_exec``) or renders a print format (our own flag, raised by the ``pdf_body_html`` hook),
unless the knowledge base's own code holds the exemption. What this holds:

* **The match**: the table in any case and spacing, quoted, aliased or database-qualified; never the
  doctype's name as a value (a meta read, a File's or a ToDo's reference), and never the published
  table or the Revision History's.
* **The contexts**: each flag alone refuses; none, a zero count or the exemption lets it through.
* **The wrapper**: the original runs with every argument untouched; a query built and not run passes;
  a refusal names nothing of the query and logs one deferred row with the user and the context, and a
  failed log never stops it; applying the patch twice wraps once.
* **The counters** come back down after an exception, and nest.
* **The print hook** hands the print to the entry before it (never to itself), with the flag up only
  while it renders.
* **The wiring**: the patch is in ``monkeypatches._PATCHES``, the hook in ``hooks.py`` is this
  module's, and ``printing.kb_document`` takes the exemption around its own reads only.

Installs its own ``frappe`` stub in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_template_guard -v
"""

import ast
import importlib
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

TABLE = "`tabKnowledge Article Version`"
STATE = {}


class Refused(Exception):
	pass


class PermissionRefused(Refused):
	pass


class _Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Database:
	"""v16 ``frappe.database.database.Database``, as far as the guard touches it."""

	def sql(self, query, values=(), *, as_dict=0, run=True, **kwargs):
		STATE["ran"].append((query, values, as_dict, run, kwargs))
		return "rows"


class _File:
	"""v16 ``File``, as far as the guard touches it."""

	def __init__(self, **values):
		self.values = values

	def get(self, key, default=None):
		return self.values.get(key, default)

	def get_content(self, encodings=None):
		STATE["read_files"].append((self.values.get("name"), encodings))
		return b"PNG"


def _throw(message, exc=None, title=None, **kwargs):
	raise (PermissionRefused if exc is PermissionRefused else Refused)(message)


def _log_error(title=None, message=None, defer_insert=False, **kwargs):
	if STATE.get("log_fails"):
		raise RuntimeError("redis is down")
	STATE["logged"].append((title, message, defer_insert))


def _reset(**flags):
	STATE.clear()
	STATE.update({"ran": [], "logged": [], "hooks": [], "rendered": [], "read_files": []})
	frappe_module = sys.modules["frappe"]
	frappe_module.local.flags = _Flags(flags)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda message, *a, **k: message
	frappe.throw = _throw
	frappe.PermissionError = PermissionRefused
	frappe.local = types.SimpleNamespace(flags=_Flags())
	frappe.session = types.SimpleNamespace(user="sales.master@example.com")
	frappe.log_error = _log_error
	frappe.get_hooks = lambda name: list(STATE["hooks"])
	frappe.get_attr = lambda path: STATE["attrs"][path]

	database_pkg = types.ModuleType("frappe.database")
	database_mod = types.ModuleType("frappe.database.database")
	database_mod.Database = _Database
	database_pkg.database = database_mod
	frappe.database = database_pkg

	file_mod = types.ModuleType("frappe.core.doctype.file.file")
	file_mod.File = _File
	for name in ("frappe.core", "frappe.core.doctype", "frappe.core.doctype.file"):
		sys.modules[name] = types.ModuleType(name)
	sys.modules["frappe.core.doctype.file.file"] = file_mod

	utils = types.ModuleType("frappe.utils")
	pdf = types.ModuleType("frappe.utils.pdf")

	def frappe_pdf_body_html(template=None, args=None, **kwargs):
		STATE["rendered"].append(("frappe", dict(frappe.local.flags)))
		return "frappe html"

	pdf.pdf_body_html = frappe_pdf_body_html
	utils.pdf = pdf
	frappe.utils = utils

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.database": database_pkg,
			"frappe.database.database": database_mod,
			"frappe.utils": utils,
			"frappe.utils.pdf": pdf,
		}
	)


guard = None
ORIGINAL_SQL = _Database.sql
ORIGINAL_GET_CONTENT = _File.get_content


def setUpModule():
	global guard
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.knowledge_base.template_guard", None)
	guard = importlib.import_module("erpnext_enhancements.knowledge_base.template_guard")
	STATE["attrs"] = {}
	_reset()


def tearDownModule():
	_Database.sql = ORIGINAL_SQL
	_File.get_content = ORIGINAL_GET_CONTENT


def _sql(query, **kwargs):
	return _Database().sql(query, **kwargs)


# ================================================================== the match


class TestTheMatch(unittest.TestCase):
	def test_the_table_however_it_is_written(self):
		for query in (
			f"select body from {TABLE}",
			"SELECT `body` FROM `TABKNOWLEDGE ARTICLE VERSION` WHERE name = %s",
			"select x.body from `tabKnowledge  Article\n Version` x",
			"select body from `_1a2b3c`.`tabKnowledge Article Version`",
			'select body from "tabKnowledge Article Version"',
			f"with d as (select body from {TABLE}) select * from d",
			f"select name from tabFile where name in (select name from {TABLE})",
		):
			with self.subTest(query=query[:40]):
				self.assertTrue(guard.names_drafts(query))

	def test_never_the_doctype_as_a_value_nor_another_table(self):
		for query in (
			"select fieldname from tabDocField where parent = 'Knowledge Article Version'",
			"select name from tabFile where attached_to_doctype = %s",
			"select name from `tabToDo` where reference_type = 'Knowledge Article Version'",
			"select body from `tabKnowledge Article`",
			"select version_number from `tabKnowledge Article Revision`",
			"",
			None,
		):
			with self.subTest(query=str(query)[:40]):
				self.assertFalse(guard.names_drafts(query))

	def test_a_query_builder_object_is_read_as_its_sql(self):
		class Query:
			def __str__(self):
				return f"SELECT `body` FROM {TABLE}"

		self.assertTrue(guard.names_drafts(Query()))

	def test_the_table_is_the_versions(self):
		self.assertEqual(guard.DRAFTS_TABLE, "tabknowledge article version")


# ================================================================== the contexts


class TestTheContexts(unittest.TestCase):
	def test_each_flag_alone_is_a_guarded_context(self):
		self.assertEqual(guard.guarded_context(_Flags(in_render_safe_exec=1)), "a template")
		self.assertEqual(guard.guarded_context(_Flags(in_safe_exec=2)), "a script")
		self.assertEqual(guard.guarded_context(_Flags(kb_rendering_print=1)), "a print format")

	def test_none_a_zero_count_or_the_exemption_is_not(self):
		for flags in (
			None,
			_Flags(),
			_Flags(in_render_safe_exec=0, in_safe_exec=0, kb_rendering_print=0),
			_Flags(in_render_safe_exec=1, kb_reads_drafts=1),
			_Flags(kb_rendering_print=1, kb_reads_drafts=2),
		):
			with self.subTest(flags=flags):
				self.assertIsNone(guard.guarded_context(flags))

	def test_frappes_own_flags_are_the_ones_named(self):
		"""v16 ``utils/jinja.py`` ``safe_render_flags`` and ``utils/safe_exec.py`` ``safe_exec_flags``."""
		self.assertEqual(set(guard.GUARDED_FLAGS), {"in_render_safe_exec", "in_safe_exec", guard.PRINT_FLAG})


# ================================================================== the wrapper


class GuardBase(unittest.TestCase):
	def setUp(self):
		_Database.sql = ORIGINAL_SQL
		_File.get_content = ORIGINAL_GET_CONTENT
		guard.patch_database_sql()
		_reset()

	def tearDown(self):
		_Database.sql = ORIGINAL_SQL
		_File.get_content = ORIGINAL_GET_CONTENT


class TestTheWrapper(GuardBase):
	def test_refused_in_each_context(self):
		for flag in ("in_render_safe_exec", "in_safe_exec", guard.PRINT_FLAG):
			with self.subTest(flag=flag):
				_reset(**{flag: 1})
				with self.assertRaises(PermissionRefused) as caught:
					_sql(f"select body from {TABLE} where name = %s", values=("KBV-00001",))
				self.assertEqual(STATE["ran"], [])
				message = str(caught.exception)
				self.assertIn("Knowledge base drafts cannot be read from", message)
				self.assertNotIn("KBV-00001", message)
				self.assertNotIn("select", message.casefold())

	def test_the_original_runs_untouched_everywhere_else(self):
		_reset()
		self.assertEqual(_sql(f"select body from {TABLE}", values=("x",), as_dict=1, pluck="body"), "rows")
		self.assertEqual(STATE["ran"], [(f"select body from {TABLE}", ("x",), 1, True, {"pluck": "body"})])
		_reset(in_render_safe_exec=1)
		self.assertEqual(_sql("select body from `tabKnowledge Article`"), "rows")

	def test_the_exemption_lets_the_knowledge_base_read(self):
		_reset(kb_rendering_print=1)
		with guard.reading_drafts():
			self.assertEqual(_sql(f"select body from {TABLE}"), "rows")
		with self.assertRaises(PermissionRefused):
			_sql(f"select body from {TABLE}")

	def test_a_query_built_and_not_run_passes(self):
		_reset(in_render_safe_exec=1)
		self.assertEqual(_sql(f"select body from {TABLE}", run=False), "rows")

	def test_a_refusal_is_logged_once_deferred_with_no_query(self):
		_reset(in_render_safe_exec=1)
		with self.assertRaises(PermissionRefused):
			_sql(f"select body from {TABLE} where name = 'KBV-00001'")
		self.assertEqual(len(STATE["logged"]), 1)
		title, message, deferred = STATE["logged"][0]
		self.assertEqual(title, "Knowledge base drafts refused")
		self.assertEqual(
			message, "sales.master@example.com tried to read knowledge base drafts from a template."
		)
		self.assertTrue(deferred)

	def test_a_failed_log_never_stops_the_refusal(self):
		_reset(in_safe_exec=1)
		STATE["log_fails"] = True
		with self.assertRaises(PermissionRefused):
			_sql(f"select body from {TABLE}")
		self.assertEqual(STATE["ran"], [])

	def test_patching_twice_wraps_once(self):
		wrapped = _Database.sql
		guard.patch_database_sql()
		self.assertIs(_Database.sql, wrapped)
		self.assertTrue(getattr(_Database.sql, guard.PATCH_MARKER))
		self.assertIs(_Database.sql.__wrapped__, ORIGINAL_SQL)

	def test_no_request_local_is_no_context(self):
		frappe_module = sys.modules["frappe"]
		saved = frappe_module.local
		frappe_module.local = types.SimpleNamespace()
		try:
			self.assertEqual(_sql(f"select body from {TABLE}"), "rows")
		finally:
			frappe_module.local = saved


class TestADraftsFiles(GuardBase):
	"""A template can find a draft's Files by the doctype as a value, which the table match leaves alone,
	so ``File.get_content`` is guarded for a File attached to a draft."""

	def _file(self, doctype="Knowledge Article Version"):
		return _File(name="abc123", attached_to_doctype=doctype, attached_to_name="KBV-00001")

	def test_refused_in_each_context(self):
		for flag in ("in_render_safe_exec", "in_safe_exec", guard.PRINT_FLAG):
			with self.subTest(flag=flag):
				_reset(**{flag: 1})
				with self.assertRaises(PermissionRefused) as caught:
					self._file().get_content()
				self.assertEqual(STATE["read_files"], [])
				self.assertNotIn("abc123", str(caught.exception))
				self.assertEqual(len(STATE["logged"]), 1)

	def test_read_everywhere_else_and_any_other_file_always(self):
		_reset()
		self.assertEqual(self._file().get_content(encodings=()), b"PNG")
		_reset(in_render_safe_exec=1)
		for doctype in ("Knowledge Article", "Sales Invoice", None):
			with self.subTest(doctype=doctype):
				self.assertEqual(self._file(doctype).get_content(), b"PNG")

	def test_the_knowledge_bases_own_print_reads_its_pictures(self):
		_reset(kb_rendering_print=1)
		with guard.reading_drafts():
			self.assertEqual(self._file().get_content(encodings=()), b"PNG")
		self.assertEqual(STATE["read_files"], [("abc123", ())])

	def test_patching_twice_wraps_once(self):
		wrapped = _File.get_content
		guard.patch_database_sql()
		self.assertIs(_File.get_content, wrapped)
		self.assertIs(_File.get_content.__wrapped__, ORIGINAL_GET_CONTENT)


# ================================================================== the counters and the print hook


class TestTheCounters(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_they_nest_and_come_down_after_an_exception(self):
		flags = sys.modules["frappe"].local.flags
		with guard.reading_drafts():
			with guard.reading_drafts():
				self.assertEqual(flags[guard.EXEMPT_FLAG], 2)
			self.assertEqual(flags[guard.EXEMPT_FLAG], 1)
		self.assertEqual(flags[guard.EXEMPT_FLAG], 0)
		with self.assertRaises(ValueError), guard.rendering_print():
			raise ValueError("render failed")
		self.assertEqual(flags[guard.PRINT_FLAG], 0)


class TestThePrintHook(GuardBase):
	def _hooks(self, *paths, **attrs):
		STATE["hooks"] = list(paths)
		STATE["attrs"] = attrs

	def test_it_hands_the_print_to_the_entry_before_it_with_the_flag_up(self):
		def other(*args, **kwargs):
			STATE["rendered"].append(("other", dict(sys.modules["frappe"].local.flags), args, kwargs))
			return "other html"

		self._hooks(
			"frappe.utils.pdf.pdf_body_html",
			"other_app.pdf_body_html",
			guard.HOOK_PATH,
			**{
				"other_app.pdf_body_html": other,
			},
		)
		out = guard.pdf_body_html(jenv="J", template="T", print_format="P", args={"doc": "D"})
		self.assertEqual(out, "other html")
		name, flags, args, kwargs = STATE["rendered"][0]
		self.assertEqual(name, "other")
		self.assertEqual(flags[guard.PRINT_FLAG], 1)
		self.assertEqual(kwargs, {"jenv": "J", "template": "T", "print_format": "P", "args": {"doc": "D"}})
		self.assertEqual(sys.modules["frappe"].local.flags[guard.PRINT_FLAG], 0)

	def test_with_no_other_entry_it_uses_frappes_own(self):
		self._hooks(guard.HOOK_PATH)
		self.assertEqual(guard.pdf_body_html(template="T", args={}), "frappe html")
		self.assertEqual(STATE["rendered"][0][0], "frappe")
		self.assertEqual(STATE["rendered"][0][1][guard.PRINT_FLAG], 1)

	def test_a_print_format_reading_drafts_is_refused(self):
		def reads_drafts(*args, **kwargs):
			return _sql(f"select body from {TABLE}")

		self._hooks(
			"frappe.utils.pdf.pdf_body_html",
			guard.HOOK_PATH,
			**{"frappe.utils.pdf.pdf_body_html": reads_drafts},
		)
		with self.assertRaises(PermissionRefused):
			guard.pdf_body_html(template="T", args={})
		self.assertEqual(sys.modules["frappe"].local.flags[guard.PRINT_FLAG], 0)

	def test_the_knowledge_bases_own_print_reads_with_the_exemption(self):
		def kb_print(*args, **kwargs):
			with guard.reading_drafts():
				return _sql(f"select body from {TABLE}")

		self._hooks(
			"frappe.utils.pdf.pdf_body_html", guard.HOOK_PATH, **{"frappe.utils.pdf.pdf_body_html": kb_print}
		)
		self.assertEqual(guard.pdf_body_html(template="T", args={}), "rows")


# ================================================================== the wiring


class TestWiring(unittest.TestCase):
	def test_the_patch_is_applied_with_the_others(self):
		tree = ast.parse((APP / "monkeypatches.py").read_text(encoding="utf-8"))
		patches = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "_PATCHES" for t in node.targets)
		)
		names = [element.id for element in patches.elts]
		self.assertIn("_patch_templates_cannot_read_kb_drafts", names)
		source = (APP / "monkeypatches.py").read_text(encoding="utf-8")
		self.assertIn("template_guard.patch_database_sql()", source)

	def test_the_print_hook_is_this_modules(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn(f'pdf_body_html = "{guard.HOOK_PATH}"', hooks)
		self.assertEqual(guard.HOOK_PATH, f"{guard.__name__}.pdf_body_html")

	def test_kb_document_takes_the_exemption_around_its_own_reads_only(self):
		source = (APP / "knowledge_base" / "printing.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		kb_document = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "kb_document")
		withs = [n for n in ast.walk(kb_document) if isinstance(n, ast.With)]
		self.assertEqual(len(withs), 1)
		self.assertEqual(ast.unparse(withs[0].items[0].context_expr), "template_guard.reading_drafts()")
		self.assertEqual(ast.unparse(withs[0].body[0]), "return _draw(doctype, doc)")
		# Nothing outside the knowledge base takes it.
		takers = [
			path.relative_to(APP).as_posix()
			for path in APP.rglob("*.py")
			if "tests" not in path.parts and "reading_drafts()" in path.read_text(encoding="utf-8")
		]
		self.assertEqual(sorted(takers), ["knowledge_base/printing.py", "knowledge_base/template_guard.py"])


if __name__ == "__main__":
	unittest.main()
