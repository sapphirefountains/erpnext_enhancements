# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Templates, scripts and print formats never read knowledge base drafts (2026-10-01). Bench-free.

``knowledge_base/template_guard.py``, applied by ``monkeypatches.py``, wraps ``Database.execute_query``
so a statement reading the drafts' table is refused while Frappe renders a stored template
(``in_render_safe_exec``), runs a script (``in_safe_exec``) or renders a print format (our flag, raised
by the ``pdf_body_html`` hook); wraps ``File.get_content`` for a draft's file; and keeps drafts out of
the document cache. The one exemption is the knowledge base's own print of the very version being
printed. What this holds, each from the review that found it:

* **The statement as the driver sends it.** The cursor's ``mogrify`` of the query and its values is
  checked, so a table name split with a ``%`` directive the driver fills with nothing (``%.0s``) or a
  space (``%1.0s``) is caught; when ``mogrify`` fails, the query's letters with every directive removed.
* **Writes** may target a draft (Frappe's own ``_assign`` updates run inside scripts) but may not read
  one: a subquery in an ``update`` or an ``insert ... select`` is refused.
* **The server's view of statements in flight** (``processlist``, ``performance_schema``) is refused too.
* **No draft in the document cache**, so ``get_cached_doc`` always reaches the guarded statement.
* **The exemption only for a genuine print** of the object printview handed the template: not from a
  template or a script, not for another document, and gone when the print ends.
* **Every worker**: ``ensure_applied`` runs from ``before_request`` and ``before_job``, because v16
  serves hooks from the redis cache and may never import ``hooks.py`` in a worker.
* The match, the contexts, the wrapper leaving everything else untouched, the refusal naming nothing
  of the query and logging once, and the wiring.

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

VERSION = "Knowledge Article Version"
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


def _mogrify(query, values):
	"""What mysqlclient's ``Cursor.mogrify`` does with a tuple or a dict: ``%`` formatting of each
	value's SQL literal (a string quoted, a number bare, ``None`` as NULL)."""

	def literal(value):
		if value is None:
			return "NULL"
		if isinstance(value, int | float):
			return str(value)
		return "'" + str(value).replace("'", "\\'") + "'"

	if isinstance(values, dict):
		return query % {key: literal(value) for key, value in values.items()}
	return query % tuple(literal(value) for value in values)


class _Cursor:
	def mogrify(self, query, values):
		if STATE.get("mogrify_fails"):
			raise AttributeError("no mogrify")
		return _mogrify(query, values)


class _Database:
	"""v16 ``frappe.database.database.Database``, as far as the guard touches it."""

	def __init__(self):
		self._cursor = _Cursor()

	def execute_query(self, query, values=None):
		STATE["ran"].append((query, values))
		return 1


class _File:
	"""v16 ``File``, as far as the guard touches it."""

	def __init__(self, **values):
		self.values = values

	def get(self, key, default=None):
		return self.values.get(key, default)

	def get_content(self, encodings=None):
		STATE["read_files"].append((self.values.get("name"), encodings))
		return b"PNG"


class _Doc:
	def __init__(self, doctype, name):
		self.doctype, self.name = doctype, name


def _set_document_in_cache(key, doc):
	STATE["cached"].append(key)


def _throw(message, exc=None, title=None, **kwargs):
	raise (PermissionRefused if exc is PermissionRefused else Refused)(message)


def _log_error(title=None, message=None, defer_insert=False, **kwargs):
	if STATE.get("log_fails"):
		raise RuntimeError("redis is down")
	STATE["logged"].append((title, message, defer_insert))


def _reset(**flags):
	STATE.clear()
	STATE.update(
		{"ran": [], "logged": [], "hooks": [], "attrs": {}, "rendered": [], "read_files": [], "cached": []}
	)
	sys.modules["frappe"].local.flags = _Flags(flags)


def _module(name, **attrs):
	module = types.ModuleType(name)
	module.__dict__.update(attrs)
	sys.modules[name] = module
	return module


def _install_frappe_stub():
	frappe = _module(
		"frappe",
		_=lambda message, *a, **k: message,
		throw=_throw,
		PermissionError=PermissionRefused,
		local=types.SimpleNamespace(flags=_Flags()),
		session=types.SimpleNamespace(user="sales.master@example.com"),
		log_error=_log_error,
		get_hooks=lambda name: list(STATE["hooks"]),
		get_attr=lambda path: STATE["attrs"][path],
		logger=lambda *a, **k: types.SimpleNamespace(
			warning=lambda *a, **k: STATE.setdefault("warned", []).append(a)
		),
		_set_document_in_cache=_set_document_in_cache,
	)
	_module("frappe.database")
	_module("frappe.database.database", Database=_Database)
	frappe.model = _module("frappe.model")
	_module("frappe.model.document", _set_document_in_cache=_set_document_in_cache)
	for name in ("frappe.core", "frappe.core.doctype", "frappe.core.doctype.file"):
		_module(name)
	_module("frappe.core.doctype.file.file", File=_File)

	def frappe_pdf_body_html(template=None, args=None, **kwargs):
		STATE["rendered"].append(("frappe", dict(frappe.local.flags)))
		return "frappe html"

	utils = _module("frappe.utils")
	frappe.utils = utils
	utils.pdf = _module("frappe.utils.pdf", pdf_body_html=frappe_pdf_body_html)
	# monkeypatches.py's other two patches, for ensure_applied.
	utils.modules = _module("frappe.utils.modules", get_modules_from_app=lambda app: [])
	utils.response = _module(
		"frappe.utils.response",
		FORCE_DOWNLOAD_EXTENSIONS=(".svg",),
		send_private_file=lambda *a, **k: types.SimpleNamespace(headers={}),
	)


guard = None
ORIGINALS = {}


def setUpModule():
	global guard
	_install_frappe_stub()
	ORIGINALS.update(
		execute_query=_Database.execute_query,
		get_content=_File.get_content,
		cache=sys.modules["frappe.model.document"]._set_document_in_cache,
	)
	for name in ("erpnext_enhancements.knowledge_base.template_guard", "erpnext_enhancements.monkeypatches"):
		sys.modules.pop(name, None)
	guard = importlib.import_module("erpnext_enhancements.knowledge_base.template_guard")
	_reset()


def _restore():
	_Database.execute_query = ORIGINALS["execute_query"]
	_File.get_content = ORIGINALS["get_content"]
	sys.modules["frappe.model.document"]._set_document_in_cache = ORIGINALS["cache"]
	sys.modules["frappe"]._set_document_in_cache = ORIGINALS["cache"]


def tearDownModule():
	_restore()


def _run(query, values=None):
	return _Database().execute_query(query, values)


class GuardBase(unittest.TestCase):
	def setUp(self):
		_restore()
		guard.apply_patches()
		_reset()

	def tearDown(self):
		_restore()


# ================================================================== the match


class TestTheMatch(unittest.TestCase):
	def test_the_table_however_it_is_written(self):
		for query in (
			f"select body from {TABLE}",
			"SELECT `body` FROM `TABKNOWLEDGE ARTICLE VERSION` WHERE name = %s",
			"select x.body from `tabKnowledge  Article\n Version` x",
			"select body from `_1a2b3c`.`tabKnowledge Article Version`",
			f"with d as (select body from {TABLE}) select * from d",
		):
			with self.subTest(query=query[:40]):
				self.assertTrue(guard.names_drafts(query))

	def test_never_the_doctype_as_a_value_nor_another_table(self):
		for query in (
			"select fieldname from tabDocField where parent = 'Knowledge Article Version'",
			"select name from `tabToDo` where reference_type = 'Knowledge Article Version'",
			"select body from `tabKnowledge Article`",
			"select version_number from `tabKnowledge Article Revision`",
			"",
			None,
		):
			with self.subTest(query=str(query)[:40]):
				self.assertFalse(guard.names_drafts(query))

	def test_a_write_may_target_a_draft_but_reads_nothing_else(self):
		self.assertFalse(
			guard.names_drafts(guard.readable_part(f"update {TABLE} set `_assign`=%s where name=%s"))
		)
		self.assertFalse(
			guard.names_drafts(guard.readable_part(f"/* x */ DELETE FROM {TABLE} WHERE name=%s"))
		)
		for query in (
			f"UPDATE `tabNote` SET `content`=(SELECT `body` FROM {TABLE} LIMIT 1) WHERE name='x'",
			f"insert into `tabNote` (content) select body from {TABLE}",
			f"update {TABLE} set title = (select body from {TABLE} limit 1)",
			f"select body from {TABLE}",
		):
			with self.subTest(query=query[:40]):
				self.assertTrue(guard.names_drafts(guard.readable_part(query)))

	def test_the_letters_of_a_split_name(self):
		for query in (
			"select body from `tabKnow%.0sledge Article Version`",
			"select body from `tabKnowledge%1.0sArticle%1.0sVersion`",
			"select body from `tabKnow%(a).0sledge Article Version`",
			"select body from `tab%-0.0sKnowledge Article Version`",
		):
			with self.subTest(query=query):
				self.assertIn(guard.DRAFTS_LETTERS, guard._letters(query))

	def test_the_server_view_of_statements_in_flight(self):
		self.assertTrue(guard.names_server_view("select info from information_schema.PROCESSLIST"))
		self.assertTrue(guard.names_server_view("select * from performance_schema.events_statements_current"))
		self.assertFalse(guard.names_server_view("select name from tabUser"))


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
		):
			with self.subTest(flags=flags):
				self.assertIsNone(guard.guarded_context(flags))

	def test_frappes_own_flags(self):
		"""v16 ``utils/jinja.py`` ``safe_render_flags`` and ``utils/safe_exec.py`` ``safe_exec_flags``."""
		self.assertEqual(guard.FRAPPE_FLAGS, ("in_render_safe_exec", "in_safe_exec"))
		self.assertEqual(set(guard.GUARDED_FLAGS), {*guard.FRAPPE_FLAGS, guard.PRINT_FLAG})


# ================================================================== the statement guard


class TestTheStatementGuard(GuardBase):
	def test_refused_in_each_context(self):
		for flag in ("in_render_safe_exec", "in_safe_exec", guard.PRINT_FLAG):
			with self.subTest(flag=flag):
				_reset(**{flag: 1})
				with self.assertRaises(PermissionRefused) as caught:
					_run(f"select body from {TABLE} where name = %s", ("KBV-00001",))
				self.assertEqual(STATE["ran"], [])
				message = str(caught.exception)
				self.assertIn("Knowledge base drafts cannot be read from", message)
				self.assertNotIn("KBV-00001", message)
				self.assertNotIn("select", message.casefold())

	def test_a_name_the_driver_rejoins_is_refused(self):
		"""The review's proof: ``%.0s`` prints nothing and ``%1.0s`` one space, after any check of the
		query alone."""
		_reset(in_render_safe_exec=1)
		for query, values in (
			("select name, body from `tabKnow%.0sledge Article Version`", [0]),
			("select body from `tabKnowledge%1.0sArticle%1.0sVersion`", (0, 0)),
			("select body from `tabKnow%(x).0sledge Article Version`", {"x": 0}),
		):
			with self.subTest(query=query):
				self.assertFalse(guard.names_drafts(query))
				with self.assertRaises(PermissionRefused):
					_run(query, values)
		self.assertEqual(STATE["ran"], [])

	def test_when_mogrify_fails_the_letters_decide(self):
		_reset(in_safe_exec=1)
		STATE["mogrify_fails"] = True
		with self.assertRaises(PermissionRefused):
			_run("select body from `tabKnow%.0sledge Article Version`", [0])
		self.assertEqual(_run("select name from `tabNote` where x = %s", ["Knowledge"]), 1)

	def test_a_write_targeting_a_draft_runs_and_one_reading_a_draft_does_not(self):
		_reset(in_safe_exec=1)
		self.assertEqual(_run(f"update {TABLE} set `_assign`=%s where name=%s", ('["a@x.com"]', "KBV-1")), 1)
		with self.assertRaises(PermissionRefused):
			_run(
				f"update `tabNote` set `content`=(select `body` from {TABLE} limit 1) where name=%s", ("n1",)
			)

	def test_the_server_view_is_refused(self):
		_reset(in_render_safe_exec=1)
		with self.assertRaises(PermissionRefused):
			_run("select info from information_schema.processlist")

	def test_everything_runs_untouched_elsewhere(self):
		_reset()
		self.assertEqual(_run(f"select body from {TABLE}", ("x",)), 1)
		self.assertEqual(STATE["ran"], [(f"select body from {TABLE}", ("x",))])
		_reset(in_render_safe_exec=1)
		self.assertEqual(_run("select body from `tabKnowledge Article` where name=%s", ("SOP-06-0001",)), 1)
		self.assertEqual(_run("select 1"), 1)

	def test_the_exemption_lets_the_knowledge_base_read(self):
		_reset(kb_rendering_print=1)
		with guard.reading_drafts():
			self.assertEqual(_run(f"select body from {TABLE}"), 1)
		with self.assertRaises(PermissionRefused):
			_run(f"select body from {TABLE}")

	def test_a_refusal_is_logged_once_deferred_with_no_query(self):
		_reset(in_render_safe_exec=1)
		with self.assertRaises(PermissionRefused):
			_run(f"select body from {TABLE} where name = 'KBV-00001'")
		self.assertEqual(
			STATE["logged"],
			[
				(
					"Knowledge base drafts refused",
					"sales.master@example.com tried to read knowledge base drafts from a template.",
					True,
				)
			],
		)

	def test_a_failed_log_never_stops_the_refusal(self):
		_reset(in_safe_exec=1)
		STATE["log_fails"] = True
		with self.assertRaises(PermissionRefused):
			_run(f"select body from {TABLE}")
		self.assertEqual(STATE["ran"], [])

	def test_no_request_local_is_no_context(self):
		frappe_module = sys.modules["frappe"]
		saved = frappe_module.local
		frappe_module.local = types.SimpleNamespace()
		try:
			self.assertEqual(_run(f"select body from {TABLE}"), 1)
		finally:
			frappe_module.local = saved

	def test_patching_twice_wraps_once(self):
		document = sys.modules["frappe.model.document"]
		wrapped = (_Database.execute_query, _File.get_content, document._set_document_in_cache)
		guard.apply_patches()
		self.assertEqual(
			(_Database.execute_query, _File.get_content, document._set_document_in_cache), wrapped
		)
		self.assertIs(_Database.execute_query.__wrapped__, ORIGINALS["execute_query"])


# ================================================================== files and the cache


class TestADraftsFiles(GuardBase):
	def _file(self, doctype=VERSION):
		return _File(name="abc123", attached_to_doctype=doctype, attached_to_name="KBV-00001")

	def test_refused_in_each_context(self):
		for flag in ("in_render_safe_exec", "in_safe_exec", guard.PRINT_FLAG):
			with self.subTest(flag=flag):
				_reset(**{flag: 1})
				with self.assertRaises(PermissionRefused) as caught:
					self._file().get_content()
				self.assertEqual(STATE["read_files"], [])
				self.assertNotIn("abc123", str(caught.exception))

	def test_read_everywhere_else_and_any_other_file_always(self):
		_reset()
		self.assertEqual(self._file().get_content(encodings=()), b"PNG")
		_reset(in_render_safe_exec=1)
		for doctype in ("Knowledge Article", "Sales Invoice", None):
			with self.subTest(doctype=doctype):
				self.assertEqual(self._file(doctype).get_content(), b"PNG")


class TestTheDocumentCache(GuardBase):
	"""``get_cached_doc`` reads redis with no SQL; a draft is never put there, so it always reaches the
	statement guard."""

	def test_a_draft_is_never_cached_and_anything_else_is(self):
		document = sys.modules["frappe.model.document"]
		for setter in (document._set_document_in_cache, sys.modules["frappe"]._set_document_in_cache):
			with self.subTest(setter=setter):
				_reset()
				setter("document_cache::Knowledge Article Version::KBV-1", _Doc(VERSION, "KBV-1"))
				setter("document_cache::Note::n1", _Doc("Note", "n1"))
				setter(
					"document_cache::Knowledge Article::SOP-06-0001", _Doc("Knowledge Article", "SOP-06-0001")
				)
				self.assertEqual(
					STATE["cached"],
					["document_cache::Note::n1", "document_cache::Knowledge Article::SOP-06-0001"],
				)

	def test_the_name_frappe_re_exports_is_the_patched_one(self):
		self.assertIs(
			sys.modules["frappe"]._set_document_in_cache,
			sys.modules["frappe.model.document"]._set_document_in_cache,
		)


# ================================================================== the print and the exemption


class TestThePrintHook(GuardBase):
	def _hooks(self, *paths, **attrs):
		STATE["hooks"] = list(paths)
		STATE["attrs"] = attrs

	def test_it_hands_the_print_to_the_entry_before_it_with_the_flag_up(self):
		doc = _Doc(VERSION, "KBV-1")

		def other(*args, **kwargs):
			flags = sys.modules["frappe"].local.flags
			STATE["rendered"].append((flags[guard.PRINT_FLAG], list(flags[guard.PRINTING_DOCS]), kwargs))
			return "other html"

		self._hooks(
			"frappe.utils.pdf.pdf_body_html",
			"other_app.pdf_body_html",
			guard.HOOK_PATH,
			**{"other_app.pdf_body_html": other},
		)
		out = guard.pdf_body_html(jenv="J", template="T", print_format="P", args={"doc": doc})
		self.assertEqual(out, "other html")
		flag, printing, kwargs = STATE["rendered"][0]
		self.assertEqual((flag, printing), (1, [doc]))
		self.assertEqual(kwargs, {"jenv": "J", "template": "T", "print_format": "P", "args": {"doc": doc}})
		flags = sys.modules["frappe"].local.flags
		self.assertEqual((flags[guard.PRINT_FLAG], flags[guard.PRINTING_DOCS]), (0, []))

	def test_with_no_other_entry_it_uses_frappes_own(self):
		self._hooks(guard.HOOK_PATH)
		self.assertEqual(guard.pdf_body_html(template="T", args={}), "frappe html")

	def test_a_print_format_reading_drafts_is_refused_and_the_flag_comes_down(self):
		def reads_drafts(*args, **kwargs):
			return _run(f"select body from {TABLE}")

		self._hooks(
			"frappe.utils.pdf.pdf_body_html",
			guard.HOOK_PATH,
			**{"frappe.utils.pdf.pdf_body_html": reads_drafts},
		)
		with self.assertRaises(PermissionRefused):
			guard.pdf_body_html(template="T", args={"doc": _Doc("Sales Invoice", "S1")})
		flags = sys.modules["frappe"].local.flags
		self.assertEqual((flags[guard.PRINT_FLAG], flags[guard.PRINTING_DOCS]), (0, []))

	def test_hook_is_last(self):
		self._hooks("frappe.utils.pdf.pdf_body_html", guard.HOOK_PATH)
		self.assertTrue(guard.hook_is_last())
		self._hooks(guard.HOOK_PATH, "later_app.pdf_body_html")
		self.assertFalse(guard.hook_is_last())


class TestOnlyAGenuinePrint(GuardBase):
	"""The review's proof: ``kb_document`` is a Jinja global any template can call, and a template in a
	job runs as Administrator, so the exemption is bound to the print itself."""

	def test_the_object_being_printed_in_a_plain_print(self):
		doc = _Doc(VERSION, "KBV-1")
		with guard.rendering_print(doc):
			self.assertTrue(guard.printing_this(doc))
			self.assertFalse(guard.printing_this(_Doc(VERSION, "KBV-1")))  # an equal object is not it
			with guard.rendering_print(_Doc("Sales Invoice", "S1")):
				self.assertFalse(guard.printing_this(doc))  # only the innermost print
		self.assertFalse(guard.printing_this(doc))

	def test_never_from_a_template_or_a_script(self):
		doc = _Doc(VERSION, "KBV-1")
		for flag in guard.FRAPPE_FLAGS:
			with self.subTest(flag=flag):
				_reset(**{flag: 1})
				with guard.rendering_print(doc):
					self.assertFalse(guard.printing_this(doc))

	def test_never_outside_a_print(self):
		_reset()
		self.assertFalse(guard.printing_this(_Doc(VERSION, "KBV-1")))
		self.assertFalse(guard.printing_this(None))


# ================================================================== every worker and the wiring


class TestEveryWorker(GuardBase):
	def test_ensure_applied_installs_the_guards_and_takes_any_arguments(self):
		_restore()
		sys.modules.pop("erpnext_enhancements.monkeypatches", None)
		monkeypatches = importlib.import_module("erpnext_enhancements.monkeypatches")
		STATE["hooks"] = [guard.HOOK_PATH]
		monkeypatches.ensure_applied(method="x.y", kwargs={}, transaction_type="job")
		self.assertTrue(getattr(_Database.execute_query, guard.PATCH_MARKER))
		self.assertTrue(getattr(_File.get_content, guard.PATCH_MARKER))
		self.assertTrue(
			getattr(sys.modules["frappe.model.document"]._set_document_in_cache, guard.PATCH_MARKER)
		)
		monkeypatches.ensure_applied()
		self.assertIs(_Database.execute_query.__wrapped__, ORIGINALS["execute_query"])

	def test_it_runs_before_every_request_and_every_job(self):
		hooks = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		values = {
			target.id: node.value
			for node in hooks.body
			if isinstance(node, ast.Assign)
			for target in node.targets
			if isinstance(target, ast.Name)
		}
		for hook in ("before_request", "before_job"):
			with self.subTest(hook=hook):
				entries = [element.value for element in values[hook].elts]
				self.assertIn("erpnext_enhancements.monkeypatches.ensure_applied", entries)


class TestWiring(unittest.TestCase):
	def test_the_patch_is_applied_with_the_others(self):
		source = (APP / "monkeypatches.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		patches = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			and any(getattr(t, "id", None) == "_PATCHES" for t in node.targets)
		)
		self.assertIn("_patch_templates_cannot_read_kb_drafts", [element.id for element in patches.elts])
		self.assertIn("template_guard.apply_patches()", source)

	def test_the_print_hook_is_this_modules(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn(f'pdf_body_html = "{guard.HOOK_PATH}"', hooks)
		self.assertEqual(guard.HOOK_PATH, f"{guard.__name__}.pdf_body_html")

	def test_kb_document_takes_the_exemption_only_for_a_genuine_print_of_a_version(self):
		tree = ast.parse((APP / "knowledge_base" / "printing.py").read_text(encoding="utf-8"))
		kb_document = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "kb_document")
		guarded = [
			n
			for n in ast.walk(kb_document)
			if isinstance(n, ast.If) and "printing_this" in ast.unparse(n.test)
		]
		self.assertEqual(len(guarded), 1)
		self.assertEqual(
			ast.unparse(guarded[0].test), "doctype == VERSION and template_guard.printing_this(doc)"
		)
		withs = [n for n in ast.walk(kb_document) if isinstance(n, ast.With)]
		self.assertEqual(len(withs), 1)
		self.assertIn(withs[0], list(ast.walk(guarded[0])))
		self.assertEqual(ast.unparse(withs[0].items[0].context_expr), "template_guard.reading_drafts()")
		takers = sorted(
			path.relative_to(APP).as_posix()
			for path in APP.rglob("*.py")
			if "tests" not in path.parts and "reading_drafts()" in path.read_text(encoding="utf-8")
		)
		self.assertEqual(takers, ["knowledge_base/printing.py", "knowledge_base/template_guard.py"])


if __name__ == "__main__":
	unittest.main()
