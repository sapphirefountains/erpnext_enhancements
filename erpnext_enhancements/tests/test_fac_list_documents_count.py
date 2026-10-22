"""Bench-free tests for ``ai_governance/fac_list_documents_count.py`` (v1.727.1).

FAC 3.0.0's ``list_documents`` counts with ``fields=["count(name) as count"]``; Frappe 16 refuses
a SQL function written as a string, so every call wrote an Error Log row. The fix swaps the
``frappe`` global inside that one FAC module for a stand-in that answers the count the way the
Desk list view does.

There is no bench and no FAC in CI, so this suite builds both: a ``frappe`` stub whose
``get_list`` validates every string field with Frappe 16.50's own ``_validate_select_field`` rule
(its two regexes are copied verbatim below), and a throwaway ``frappe_assistant_core`` package on disk whose ``list_documents`` module makes
the same two calls FAC 3.0.0 makes (written for this test; FAC's own AGPL source is not copied
here). The pinned sha256 is swapped for the fake module's for the duration of a test.

What it proves:

* the stub reproduces the bug (unpatched: ``total_count`` None, one Error Log);
* patched, every field the count sends to ``get_list`` passes v16's ``ALLOWED_FIELD_PATTERN``
  (for DocType names with spaces and hyphens too), the count keeps the caller's filters and
  ``ignore_permissions``, drops ``limit``, runs under the Desk's one-second cap, and fills
  ``total_count`` with no Error Log; a timeout gives ``None`` and no Error Log;
* a virtual DocType's count goes to its controller unchanged, once, and nothing is counted;
* the listing call itself passes through untouched;
* the guard is inert -- module untouched -- when the source hash does not match, when FAC is not
  installed, and when the module's ``frappe`` is not the real one; it never raises;
* applying twice installs one stand-in.

Run: python -m unittest erpnext_enhancements.tests.test_fac_list_documents_count -v
"""

import ast
import hashlib
import importlib
import re
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
PATCH_MODULE = "erpnext_enhancements.ai_governance.fac_list_documents_count"
TARGET_MODULE = "frappe_assistant_core.plugins.core.tools.list_documents"
PERMITTED_ROWS = 7

# Copied verbatim from frappe/database/query.py at tag v16.50.0 (what prod runs), where
# _validate_select_field rejects FUNCTION_CALL_PATTERN and then accepts only ALLOWED_FIELD_PATTERN.
FUNCTION_CALL_PATTERN = re.compile(r"^\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\(", flags=re.ASCII)
ALLOWED_FIELD_PATTERN = re.compile(
	r"^(?:(`[\w\s-]+`|\w+)\.)?(`\w+`|\w+)(?:\s+as\s+(?:`[\w\s-]+`|'[\w\s:-]+'|\w+))?$",
	flags=re.IGNORECASE,
)


def validate_select_field(field):
	"""Frappe 16.50's ``_validate_select_field``, minus its ``*`` and all-digits shortcuts."""
	if FUNCTION_CALL_PATTERN.match(field):
		raise Exception(f"SQL functions are not allowed as strings in SELECT: {field}")
	if not ALLOWED_FIELD_PATTERN.match(field):
		raise Exception(f"Invalid field format for SELECT: {field}")


FAKE_LIST_DOCUMENTS = """\
import frappe


def run(doctype, filters):
	documents = frappe.get_list(
		doctype,
		filters=filters,
		fields=["name"],
		limit=20,
		order_by="creation desc",
		ignore_permissions=False,
	)
	try:
		count_result = frappe.get_list(
			doctype,
			filters=filters,
			fields=["count(name) as count"],
			limit=1,
			ignore_permissions=False,
		)
		total_count = count_result[0].get("count") if count_result else 0
	except Exception:
		frappe.log_error(title=f"list_documents: count query failed for {doctype}", message="traceback")
		total_count = None
	return {"data": documents, "total_count": total_count}
"""

STUB_SUBMODULES = (
	"frappe.query_builder",
	"frappe.query_builder.functions",
	"frappe.model",
	"frappe.model.utils",
)

_saved_modules = {}
_fake_root = None


class _Query:
	"""What ``get_list(..., run=0)`` returns: a query that has not run."""

	def __init__(self, doctype, kwargs):
		self.doctype = doctype
		self.kwargs = kwargs


class _CountSelect:
	def __init__(self, subquery):
		self.subquery = subquery
		self.selected = None

	def select(self, term):
		self.selected = term
		return self

	def walk(self):
		return "SELECT COUNT(*) FROM (<subquery>)", {"count_select": self}


class _StatementTimeout(Exception):
	pass


class _Count:
	def __init__(self, arg):
		self.arg = arg


def _build_frappe_stub():
	frappe_stub = types.ModuleType("frappe")
	frappe_stub.calls = []
	frappe_stub.error_logs = []
	frappe_stub.log_lines = []

	frappe_stub.timeout = False
	frappe_stub.virtual = set()

	def get_list(doctype, *args, **kwargs):
		for field in kwargs.get("fields") or []:
			if isinstance(field, str):
				validate_select_field(field)
		frappe_stub.calls.append(("get_list", doctype, args, kwargs))
		if kwargs.get("run", 1) in (0, False):
			return _Query(doctype, kwargs)
		return [{"name": f"{doctype}-0001"}]

	def log_error(title=None, message=None):
		frappe_stub.error_logs.append(title)

	class _Logger:
		def warning(self, message):
			frappe_stub.log_lines.append(("WARNING", message))

		def error(self, message):
			frappe_stub.log_lines.append(("ERROR", message))

	def sql(query, params):
		frappe_stub.calls.append(("sql", query, params))
		if frappe_stub.timeout:
			raise _StatementTimeout(1969, "Query execution was interrupted (max_statement_time exceeded)")
		return [(PERMITTED_ROWS,)]

	frappe_stub.get_list = get_list
	frappe_stub.log_error = log_error
	frappe_stub.logger = lambda name=None: _Logger()
	frappe_stub.qb = types.SimpleNamespace(from_=_CountSelect)
	frappe_stub.db = types.SimpleNamespace(
		db_type="mariadb",
		sql=sql,
		is_statement_timeout=lambda e: bool(e.args) and e.args[0] == 1969,
	)
	frappe_stub.session = types.SimpleNamespace(user="tester@example.com")

	query_builder = types.ModuleType("frappe.query_builder")
	functions = types.ModuleType("frappe.query_builder.functions")
	functions.Count = _Count
	query_builder.functions = functions
	frappe_stub.query_builder = query_builder

	model = types.ModuleType("frappe.model")
	model_utils = types.ModuleType("frappe.model.utils")
	model_utils.is_virtual_doctype = lambda doctype: doctype in frappe_stub.virtual
	model.utils = model_utils
	frappe_stub.model = model
	return frappe_stub, {
		"frappe.query_builder": query_builder,
		"frappe.query_builder.functions": functions,
		"frappe.model": model,
		"frappe.model.utils": model_utils,
	}


def _write_fake_fac(root):
	tools = Path(root, "frappe_assistant_core", "plugins", "core", "tools")
	tools.mkdir(parents=True)
	for package in (tools.parents[2], tools.parents[1], tools.parent, tools):
		Path(package, "__init__.py").write_text("", encoding="utf-8")
	Path(tools, "list_documents.py").write_text(FAKE_LIST_DOCUMENTS, encoding="utf-8", newline="\n")


def _forget_fake_fac():
	for name in list(sys.modules):
		if name == "frappe_assistant_core" or name.startswith("frappe_assistant_core."):
			del sys.modules[name]


def setUpModule():
	global _fake_root
	for name in ("frappe", *STUB_SUBMODULES, PATCH_MODULE):
		_saved_modules[name] = sys.modules.pop(name, None)
	_forget_fake_fac()
	_fake_root = tempfile.mkdtemp(prefix="fake_fac_")
	_write_fake_fac(_fake_root)
	sys.path.insert(0, _fake_root)


def tearDownModule():
	_forget_fake_fac()
	if _fake_root in sys.path:
		sys.path.remove(_fake_root)
	shutil.rmtree(_fake_root, ignore_errors=True)
	for name, module in _saved_modules.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _fake_source_hash():
	return hashlib.sha256(FAKE_LIST_DOCUMENTS.encode("utf-8")).hexdigest()


class _Base(unittest.TestCase):
	def setUp(self):
		self.frappe, submodules = _build_frappe_stub()
		sys.modules["frappe"] = self.frappe
		sys.modules.update(submodules)
		sys.modules.pop(PATCH_MODULE, None)
		_forget_fake_fac()
		importlib.invalidate_caches()
		self.patch = importlib.import_module(PATCH_MODULE)
		self.known = mock.patch.dict(self.patch.KNOWN_SOURCES, {_fake_source_hash(): "fake"}, clear=True)
		self.known.start()
		self.addCleanup(self.known.stop)

	def target(self):
		return importlib.import_module(TARGET_MODULE)

	def calls(self, kind):
		return [call for call in self.frappe.calls if call[0] == kind]


class TestStubReproducesTheBug(_Base):
	def test_unpatched_count_fails_and_logs(self):
		result = self.target().run("Task", {"status": "Open"})
		self.assertIsNone(result["total_count"])
		self.assertEqual(self.frappe.error_logs, ["list_documents: count query failed for Task"])
		self.assertEqual(result["data"], [{"name": "Task-0001"}])


class TestAppliedCount(_Base):
	def test_count_is_filled_without_an_error_log(self):
		self.assertEqual(self.patch.apply(), self.patch.APPLIED)
		result = self.target().run("Sales Invoice", {"docstatus": 1})
		self.assertEqual(result["total_count"], PERMITTED_ROWS)
		self.assertEqual(self.frappe.error_logs, [])
		self.assertEqual(result["data"], [{"name": "Sales Invoice-0001"}])

	def test_count_field_passes_v16_select_validation(self):
		# The stub would already have raised; this states the rule for the names that stretch it.
		for doctype in ("Task", "Sales Invoice", "Sapphire Maintenance Template", "Item-Variant_Map"):
			field = self.patch.count_field(doctype)
			self.assertIsNone(FUNCTION_CALL_PATTERN.match(field), field)
			self.assertIsNotNone(ALLOWED_FIELD_PATTERN.match(field), field)

	def test_every_field_sent_passes_v16_select_validation(self):
		self.patch.apply()
		self.target().run("Sales Invoice", {"docstatus": 1})
		fields = [field for call in self.calls("get_list") for field in call[3].get("fields") or []]
		self.assertIn("`tabSales Invoice`.name", fields)
		for field in fields:
			self.assertIsNotNone(ALLOWED_FIELD_PATTERN.match(field), field)

	def test_count_keeps_filters_and_permission_rules(self):
		self.patch.apply()
		filters = {"docstatus": 1, "customer": "Acme"}
		self.target().run("Sales Invoice", filters)
		get_lists = self.calls("get_list")
		self.assertEqual(len(get_lists), 2)
		_, doctype, args, kwargs = get_lists[1]
		self.assertEqual(doctype, "Sales Invoice")
		self.assertEqual(args, ())
		self.assertIs(kwargs["filters"], filters)
		self.assertIs(kwargs["ignore_permissions"], False)
		self.assertEqual(kwargs["fields"], ["`tabSales Invoice`.name"])
		self.assertIsNone(kwargs["order_by"])
		self.assertEqual(kwargs["run"], 0)
		self.assertNotIn("limit", kwargs)

		counts = self.calls("sql")
		self.assertEqual(len(counts), 1)
		_, query, params = counts[0]
		self.assertTrue(query.startswith("SET STATEMENT max_statement_time=1 FOR SELECT COUNT(*)"), query)
		count_select = params["count_select"]
		self.assertIsInstance(count_select.subquery, _Query)
		self.assertIs(count_select.subquery.kwargs, kwargs)
		self.assertIsInstance(count_select.selected, _Count)
		self.assertEqual(count_select.selected.arg, "*")

	def test_no_timeout_clause_off_mariadb(self):
		self.frappe.db.db_type = "postgres"
		stand_in = self.patch._CountSafeFrappe(self.frappe)
		stand_in.get_list("ToDo", filters={}, fields=["count(name) as count"], limit=1)
		self.assertTrue(self.calls("sql")[0][1].startswith("SELECT COUNT(*)"))

	def test_a_count_that_times_out_is_none_without_an_error_log(self):
		self.frappe.timeout = True
		self.patch.apply()
		result = self.target().run("Sales Invoice", {"docstatus": 1})
		self.assertIsNone(result["total_count"])
		self.assertEqual(self.frappe.error_logs, [])
		self.assertEqual(result["data"], [{"name": "Sales Invoice-0001"}])

	def test_other_count_errors_still_reach_fac(self):
		def broken_sql(query, params):
			raise RuntimeError("table gone")

		self.frappe.db.sql = broken_sql
		self.patch.apply()
		result = self.target().run("Task", {})
		self.assertIsNone(result["total_count"])
		self.assertEqual(self.frappe.error_logs, ["list_documents: count query failed for Task"])

	def test_listing_call_passes_through_untouched(self):
		self.patch.apply()
		self.target().run("Task", {"status": "Open"})
		_, doctype, args, kwargs = self.calls("get_list")[0]
		self.assertEqual(doctype, "Task")
		self.assertEqual(
			kwargs,
			{
				"filters": {"status": "Open"},
				"fields": ["name"],
				"limit": 20,
				"order_by": "creation desc",
				"ignore_permissions": False,
			},
		)

	def test_any_ignore_permissions_value_is_forwarded(self):
		stand_in = self.patch._CountSafeFrappe(self.frappe)
		for value in (False, True):
			self.frappe.calls.clear()
			stand_in.get_list(
				"ToDo", filters={}, fields=["count(name) as count"], limit=1, ignore_permissions=value
			)
			self.assertIs(self.frappe.calls[0][3]["ignore_permissions"], value)

	def test_virtual_doctype_count_goes_to_its_controller_once_as_written(self):
		# A virtual DocType has no table: its controller ignores run=0, so there is nothing to count.
		# FAC's own call goes through unchanged, exactly once, and no count query is built.
		self.frappe.virtual.add("Virtual Thing")
		controller_calls = []

		def controller_get_list(doctype, *args, **kwargs):
			controller_calls.append((doctype, args, kwargs))
			return ({"count": 3},)  # a controller may return any sequence

		self.frappe.get_list = controller_get_list
		stand_in = self.patch._CountSafeFrappe(self.frappe)
		result = stand_in.get_list(
			"Virtual Thing", filters={"a": 1}, fields=["count(name) as count"], limit=1
		)
		self.assertEqual(result, ({"count": 3},))
		self.assertEqual(
			controller_calls,
			[("Virtual Thing", (), {"filters": {"a": 1}, "fields": ["count(name) as count"], "limit": 1})],
		)
		self.assertEqual(self.calls("sql"), [])

	def test_other_fields_are_not_rewritten(self):
		stand_in = self.patch._CountSafeFrappe(self.frappe)
		with self.assertRaises(Exception):
			stand_in.get_list("ToDo", fields=["sum(grand_total) as total"])
		self.assertEqual(stand_in.get_list("ToDo", fields=[{"COUNT": "name"}]), [{"name": "ToDo-0001"}])

	def test_stand_in_delegates_reads_and_writes(self):
		self.patch.apply()
		module = self.target()
		self.assertIsInstance(module.frappe, self.patch._CountSafeFrappe)
		self.assertIs(module.frappe.session, self.frappe.session)
		self.assertIs(module.frappe.log_error, self.frappe.log_error)
		module.frappe.flags_for_test = "set"
		self.assertEqual(self.frappe.flags_for_test, "set")

	def test_apply_twice_installs_one_stand_in(self):
		self.patch.apply()
		first = self.target().frappe
		self.patch._state = None
		self.assertEqual(self.patch.apply(), self.patch.APPLIED)
		self.assertIs(self.target().frappe, first)
		self.assertIsInstance(first._ee_frappe, types.ModuleType)

	def test_only_the_first_call_does_work(self):
		self.patch.apply()
		with mock.patch.object(self.patch, "_install", side_effect=AssertionError("ran twice")):
			self.assertEqual(self.patch.apply(), self.patch.APPLIED)

	def test_crlf_checkout_hashes_the_same(self):
		with mock.patch.object(
			self.patch.inspect, "getsource", return_value=FAKE_LIST_DOCUMENTS.replace("\n", "\r\n")
		):
			self.assertEqual(self.patch.source_sha256(object()), _fake_source_hash())


class TestGuardIsInert(_Base):
	def assert_untouched(self):
		self.assertIs(self.target().frappe, self.frappe)

	def test_source_mismatch(self):
		with mock.patch.dict(self.patch.KNOWN_SOURCES, {"0" * 64: "3.0.0"}, clear=True):
			self.assertEqual(self.patch.apply(), self.patch.SOURCE_MISMATCH)
		self.assert_untouched()
		result = self.target().run("Task", {"status": "Open"})
		self.assertIsNone(result["total_count"])
		# WARNING, which a production site's file logger drops: an upgraded bench must not log this
		# on every worker start.
		self.assertEqual(
			self.frappe.log_lines, [("WARNING", "FAC list_documents count fix not applied: source-mismatch")]
		)

	def test_fac_not_installed(self):
		with mock.patch.object(self.patch.importlib.util, "find_spec", return_value=None):
			self.assertEqual(self.patch.apply(), self.patch.NO_FAC)
		self.assertNotIn(TARGET_MODULE, sys.modules)
		self.assertEqual(self.frappe.log_lines, [])

	def test_foreign_frappe(self):
		module = self.target()
		someone_else = object()
		module.frappe = someone_else
		self.assertEqual(self.patch.apply(), self.patch.FOREIGN_FRAPPE)
		self.assertIs(module.frappe, someone_else)
		self.assertEqual(
			self.frappe.log_lines, [("ERROR", "FAC list_documents count fix not applied: foreign-frappe")]
		)

	def test_never_raises(self):
		with mock.patch.object(self.patch.importlib, "import_module", side_effect=ImportError("boom")):
			self.assertEqual(self.patch.apply(), self.patch.FAILED)
		self.assertEqual(
			self.frappe.log_lines, [("ERROR", "FAC list_documents count fix not applied: failed")]
		)

	def test_logger_failure_never_raises(self):
		self.frappe.logger = mock.Mock(side_effect=RuntimeError("no logger"))
		with mock.patch.dict(self.patch.KNOWN_SOURCES, {}, clear=True):
			self.assertEqual(self.patch.apply(), self.patch.SOURCE_MISMATCH)


class TestPinnedSource(unittest.TestCase):
	def test_pin_is_one_sha256_for_fac_3_0_0(self):
		tree = ast.parse(
			(APP_DIR / "ai_governance" / "fac_list_documents_count.py").read_text(encoding="utf-8")
		)
		pins = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "KNOWN_SOURCES"
		)
		pinned = ast.literal_eval(pins)
		self.assertEqual(list(pinned.values()), ["3.0.0"])
		(digest,) = pinned
		self.assertRegex(digest, r"^[0-9a-f]{64}$")


class TestRegistered(unittest.TestCase):
	def test_monkeypatches_applies_it_in_every_request_and_job(self):
		tree = ast.parse((APP_DIR / "monkeypatches.py").read_text(encoding="utf-8"))
		patches = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "_PATCHES"
		)
		self.assertIn("_patch_fac_list_documents_count", [element.id for element in patches.elts])
		hooks = (APP_DIR / "hooks.py").read_text(encoding="utf-8")
		self.assertIn('before_job = ["erpnext_enhancements.monkeypatches.ensure_applied"]', hooks)
		self.assertIn('"erpnext_enhancements.monkeypatches.ensure_applied",', hooks)


if __name__ == "__main__":
	unittest.main()
