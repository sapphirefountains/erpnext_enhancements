"""Field references in read requests, held to the caller's field-level read (v1.835.5).

``fieldlevel_filters.py`` refuses a list, count, value, search, next-record, number-card or chart
request that names a field the caller cannot read at its permlevel, in ``filters``, ``or_filters``,
``order_by``, ``group_by``, an aggregate or another ``fields`` expression. A child table's field
answers to the parent's levels.

Two halves, as in ``test_fieldlevel_read.py``:

* **Wiring**, by ``ast``: every override is keyed on frappe's dotted path, restates frappe
  16.56.0's parameter list (read with ``gh api repos/frappe/frappe/contents/<path>?ref=v16.56.0``)
  and HTTP methods, and calls frappe's own function once; the seal covers exactly the overridden
  routes; the REST check and the seal are registered hooks, not endpoints.
* **The rule, run**, over stand-in metadata through a ``frappe`` stub: Employee (level-1 ``ctc``,
  a level-1 Link and a level-1 pay-rate table), Timesheet (a level-0 table with a level-1 field
  inside it) and a Single with a level-1 field. Each position refuses a hidden field and passes a
  visible one; child-table and backtick notation; a child table read through its parent; malformed
  input refused for a caller with hidden fields and left to frappe otherwise; a caller who reads
  everything, and Administrator, untouched; each wrapper refusing before frappe's function runs
  and passing its arguments through unchanged otherwise; the REST after_request hook.

Bench-free: installs a ``frappe`` stub in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_fieldlevel_filters
"""

import ast
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"
MODULE_PY = APP / "fieldlevel_filters.py"
MODULE = "erpnext_enhancements.fieldlevel_filters"
READ_MODULE = "erpnext_enhancements.fieldlevel_read"

ANY = ["GET", "POST", "PUT", "DELETE"]

#: frappe 16.56.0: dotted path -> (wrapper, positional parameters, their defaults from the right,
#: keyword-only parameters, their defaults). Every one is @frappe.whitelist() with no methods, so
#: it answers GET, POST, PUT and DELETE. Read from frappe/desk/reportview.py, frappe/desk/listview.py,
#: frappe/client.py, frappe/desk/search.py, frappe/desk/form/utils.py and the Number Card and
#: Dashboard Chart controllers at the v16.56.0 tag.
FRAPPE_V16_56 = {
	"frappe.desk.reportview.get": ("reportview_get", [], [], [], []),
	"frappe.desk.reportview.get_list": ("reportview_get_list", [], [], [], []),
	"frappe.desk.reportview.get_count": ("reportview_get_count", [], [], [], []),
	"frappe.desk.reportview.export_query": ("reportview_export_query", [], [], [], []),
	"frappe.desk.reportview.get_sidebar_stats": (
		"reportview_get_sidebar_stats",
		["stats", "doctype", "filters"],
		[None],
		[],
		[],
	),
	"frappe.desk.reportview.get_stats": (
		"reportview_get_stats",
		["stats", "doctype", "filters"],
		[None],
		[],
		[],
	),
	"frappe.desk.reportview.get_filter_dashboard_data": (
		"reportview_get_filter_dashboard_data",
		["stats", "doctype", "filters"],
		[None],
		[],
		[],
	),
	"frappe.desk.listview.get_group_by_count": (
		"listview_get_group_by_count",
		["doctype", "current_filters", "field"],
		[],
		[],
		[],
	),
	"frappe.client.get_list": (
		"client_get_list",
		[
			"doctype",
			"fields",
			"filters",
			"group_by",
			"order_by",
			"limit_start",
			"limit_page_length",
			"parent",
			"debug",
			"as_dict",
			"or_filters",
			"expand",
		],
		[None, None, None, None, None, 20, None, False, True, None, None],
		[],
		[],
	),
	"frappe.client.get_count": (
		"client_get_count",
		["doctype", "filters", "debug", "cache"],
		[None, False, False],
		[],
		[],
	),
	"frappe.client.get": ("client_get", ["doctype", "name", "filters", "parent"], [None, None, None], [], []),
	"frappe.client.get_value": (
		"client_get_value",
		["doctype", "fieldname", "filters", "as_dict", "debug", "parent"],
		[None, True, False, None],
		[],
		[],
	),
	"frappe.client.get_single_value": ("client_get_single_value", ["doctype", "field"], [], [], []),
	"frappe.desk.search.search_link": (
		"search_link",
		[
			"doctype",
			"txt",
			"query",
			"filters",
			"page_length",
			"searchfield",
			"reference_doctype",
			"ignore_user_permissions",
		],
		[None, None, 10, None, None, False],
		["link_fieldname"],
		[None],
	),
	"frappe.desk.search.search_widget": (
		"search_widget",
		[
			"doctype",
			"txt",
			"query",
			"searchfield",
			"start",
			"page_length",
			"filters",
			"filter_fields",
			"as_dict",
			"reference_doctype",
			"ignore_user_permissions",
		],
		[None, None, 0, 10, None, None, False, None, False],
		["link_fieldname", "for_link_validation", "query_filters_as_dict"],
		[None, False, False],
	),
	"frappe.desk.form.utils.get_next": (
		"form_get_next",
		["doctype", "value", "prev", "filters", "sort_order", "sort_field"],
		[None, "desc", "creation"],
		[],
		[],
	),
	"frappe.desk.doctype.number_card.number_card.get_result": (
		"number_card_get_result",
		["doc", "filters", "to_date"],
		[None],
		[],
		[],
	),
	"frappe.desk.doctype.number_card.number_card.get_percentage_difference": (
		"number_card_get_percentage_difference",
		["doc", "filters", "result"],
		[],
		[],
		[],
	),
	"frappe.desk.doctype.dashboard_chart.dashboard_chart.get": (
		"dashboard_chart_get",
		[
			"chart_name",
			"chart",
			"no_cache",
			"filters",
			"from_date",
			"to_date",
			"timespan",
			"time_interval",
			"heatmap_year",
			"refresh",
		],
		[None] * 10,
		[],
		[],
	),
}

AFTER_REQUEST = f"{MODULE}.refuse_rest_list_references"
BEFORE_REQUEST = f"{MODULE}.seal_filter_originals"

ff = None
STATE = {}
_SAVED_MODULES = {}


class _Row(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _PermissionError(Exception):
	pass


class _DoesNotExistError(Exception):
	pass


def _df(fieldname, permlevel=0, fieldtype="Data", options=None):
	return _Row(fieldname=fieldname, permlevel=permlevel, fieldtype=fieldtype, options=options)


def _perm(role, permlevel=0, read=1):
	return _Row(role=role, permlevel=permlevel, read=read)


class _Meta:
	def __init__(self, name, fields, permissions=(), istable=False, issingle=False):
		self.name = name
		self.fields = list(fields)
		self.permissions = list(permissions)
		self.istable = istable
		self.issingle = issingle


#: Employee: ctc, a Link and the pay-rate table at level 1; HR User reads level 0, HR Manager both.
#: Timesheet: the time_logs table at level 0 with a level-1 field inside it; Projects User reads
#: level 0, Accounts Manager both. Pay Settings: a Single with one level-1 field.
METAS = {
	"Employee": _Meta(
		"Employee",
		[
			_df("employee_name"),
			_df("department", 0, "Link", "Department"),
			_df("ctc", 1, "Currency"),
			_df("pay_grade", 1, "Link", "Pay Grade"),
			_df("custom_pay_rates", 1, "Table", "Employee Pay Rate"),
		],
		[_perm("HR User"), _perm("HR Manager"), _perm("HR Manager", 1)],
	),
	"Employee Pay Rate": _Meta(
		"Employee Pay Rate",
		[_df("effective_from", 1, "Date"), _df("hourly_rate", 1, "Currency")],
		istable=True,
	),
	"Timesheet": _Meta(
		"Timesheet",
		[
			_df("note"),
			_df("total_costing_amount", 1, "Currency"),
			_df("time_logs", 0, "Table", "Timesheet Detail"),
		],
		[_perm("Projects User"), _perm("Accounts Manager"), _perm("Accounts Manager", 1)],
	),
	"Timesheet Detail": _Meta(
		"Timesheet Detail",
		[_df("hours", 0, "Float"), _df("base_costing_rate", 1, "Currency")],
		istable=True,
	),
	"Pay Settings": _Meta(
		"Pay Settings",
		[_df("company"), _df("default_rate", 1, "Currency")],
		[_perm("HR User"), _perm("HR Manager"), _perm("HR Manager", 1)],
		issingle=True,
	),
	"Department": _Meta("Department", [_df("department_name")], [_perm("All")]),
	"Other Child": _Meta("Other Child", [_df("x")], istable=True),
	"ToDo": _Meta("ToDo", [_df("description")], [_perm("All")]),
}

HR_USER = ("hr.user@example.com", ("HR User", "All"))
HR_MANAGER = ("hr.manager@example.com", ("HR Manager", "All"))
PROJECTS_USER = ("pu@example.com", ("Projects User", "All"))


def _whitelist(allow_guest=False, methods=None, **_kwargs):
	def decorate(fn):
		STATE.setdefault("whitelisted", {})[fn.__name__] = methods
		return fn

	return decorate


def _get_meta(doctype):
	STATE["meta_calls"].append(doctype)
	if doctype not in METAS:
		raise _DoesNotExistError(doctype)
	return METAS[doctype]


def _throw(msg, exc=Exception, title=None, **kwargs):
	STATE["thrown"].append(msg)
	raise exc(msg)


def _log_error(title=None, message=None, **kw):
	STATE["errors"].append(title)


def _get_attr(method_string):
	if STATE.get("get_attr_raises"):
		raise ImportError(method_string)
	module, _dot, attr = method_string.rpartition(".")
	return getattr(sys.modules[module], attr)


def _db_get_value(doctype, name, fields, as_dict=False):
	return STATE["stored"].get((doctype, name))


def _install_frappe_stub():
	for name in list(sys.modules):
		if name == "frappe" or name.startswith("frappe.") or name in (MODULE, READ_MODULE):
			_SAVED_MODULES[name] = sys.modules.pop(name)

	frappe = types.ModuleType("frappe")
	frappe.__path__ = []
	frappe.whitelist = _whitelist
	frappe.session = _Row(user=HR_USER[0])
	frappe.response = _Row()
	frappe.form_dict = _Row()
	frappe.get_meta = _get_meta
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	frappe.log_error = _log_error
	frappe.get_traceback = lambda with_context=False: "traceback"
	frappe.whitelisted = set()
	frappe.get_attr = _get_attr
	frappe.override_whitelisted_method = lambda original: STATE["overrides"].get(original, original)
	frappe.throw = _throw
	frappe._ = lambda text: text
	frappe.PermissionError = _PermissionError
	frappe.DoesNotExistError = _DoesNotExistError
	frappe.db = types.SimpleNamespace(get_value=_db_get_value)

	def _record(name):
		def fn(*args, **kwargs):
			STATE["calls"].append((name, args, kwargs))
			return STATE["returns"].get(name, name)

		return fn

	def _module(name, attrs, package=False):
		module = types.ModuleType(name)
		if package:
			module.__path__ = []
		short = name.rpartition(".")[2]
		for attr in attrs:
			setattr(module, attr, _record(f"{short}.{attr}"))
		sys.modules[name] = module
		return module

	sys.modules["frappe"] = frappe
	desk = _module("frappe.desk", [], package=True)
	form = _module("frappe.desk.form", [], package=True)
	doctype_pkg = _module("frappe.desk.doctype", [], package=True)
	nc_pkg = _module("frappe.desk.doctype.number_card", [], package=True)
	dc_pkg = _module("frappe.desk.doctype.dashboard_chart", [], package=True)
	desk.reportview = _module(
		"frappe.desk.reportview",
		[
			"get",
			"get_list",
			"get_count",
			"export_query",
			"get_sidebar_stats",
			"get_stats",
			"get_filter_dashboard_data",
		],
	)
	desk.listview = _module("frappe.desk.listview", ["get_group_by_count"])
	desk.search = _module("frappe.desk.search", ["search_link", "search_widget"])
	form.utils = _module("frappe.desk.form.utils", ["get_next"])
	nc_pkg.number_card = _module(
		"frappe.desk.doctype.number_card.number_card", ["get_result", "get_percentage_difference"]
	)
	dc_pkg.dashboard_chart = _module("frappe.desk.doctype.dashboard_chart.dashboard_chart", ["get"])
	desk.form, desk.doctype = form, doctype_pkg
	doctype_pkg.number_card, doctype_pkg.dashboard_chart = nc_pkg, dc_pkg
	frappe.desk = desk
	frappe.client = _module(
		"frappe.client", ["get_list", "get_count", "get", "get_value", "get_single_value"]
	)
	return frappe


def setUpModule():
	global ff
	_install_frappe_stub()
	_reset()
	ff = importlib.import_module(MODULE)


def tearDownModule():
	for name in list(sys.modules):
		if name == "frappe" or name.startswith("frappe.") or name in (MODULE, READ_MODULE):
			del sys.modules[name]
	sys.modules.update(_SAVED_MODULES)


def _reset(who=HR_USER, form=None):
	frappe = sys.modules["frappe"]
	frappe.session = _Row(user=who[0])
	frappe.form_dict = _Row(form or {})
	STATE.update(
		roles=list(who[1]),
		calls=[],
		thrown=[],
		errors=[],
		meta_calls=[],
		overrides={},
		stored={},
		get_attr_raises=False,
		returns={},
	)


def _refused(fn, *args, **kwargs):
	try:
		fn(*args, **kwargs)
	except _PermissionError:
		return True
	return False


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class TestWiring(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.tree = ast.parse(MODULE_PY.read_text(encoding="utf-8"))
		cls.functions = {n.name: n for n in cls.tree.body if isinstance(n, ast.FunctionDef)}
		cls.hooks = {}
		for node in ast.parse(HOOKS.read_text(encoding="utf-8")).body:
			if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
				try:
					cls.hooks[node.targets[0].id] = ast.literal_eval(node.value)
				except Exception:  # not a literal: not a hook read here
					continue

	def _whitelist_keywords(self, fn):
		for decorator in fn.decorator_list:
			target = decorator.func if isinstance(decorator, ast.Call) else decorator
			if isinstance(target, ast.Attribute) and target.attr == "whitelist":
				return {k.arg: k.value for k in getattr(decorator, "keywords", [])}
		return None

	def test_every_route_is_overridden_by_this_module(self):
		overrides = self.hooks["override_whitelisted_methods"]
		for original, (wrapper, *_rest) in FRAPPE_V16_56.items():
			with self.subTest(original=original):
				self.assertEqual(overrides.get(original), f"{MODULE}.{wrapper}")
				self.assertIn(wrapper, self.functions)

	def test_parameters_mirror_frappe_v16_56(self):
		"""frappe.call matches the request against the wrapper's signature: a missing name is dropped."""
		for _original, (wrapper, params, defaults, kwonly, kw_defaults) in FRAPPE_V16_56.items():
			with self.subTest(wrapper=wrapper):
				args = self.functions[wrapper].args
				self.assertEqual([a.arg for a in args.args], params)
				self.assertEqual([ast.literal_eval(d) for d in args.defaults], defaults)
				self.assertEqual([a.arg for a in args.kwonlyargs], kwonly)
				self.assertEqual([ast.literal_eval(d) for d in args.kw_defaults], kw_defaults)
				self.assertFalse(args.posonlyargs or args.vararg or args.kwarg)

	def test_http_methods_are_frappes_default_written_out(self):
		"""frappe's originals declare no methods (GET, POST, PUT, DELETE); the wrapper says so explicitly."""
		for _original, (wrapper, *_rest) in FRAPPE_V16_56.items():
			with self.subTest(wrapper=wrapper):
				keywords = self._whitelist_keywords(self.functions[wrapper])
				self.assertIsNotNone(keywords, f"{wrapper} is not whitelisted")
				self.assertNotIn("allow_guest", keywords)
				self.assertEqual(ast.literal_eval(keywords["methods"]), ANY)
				self.assertEqual(STATE["whitelisted"][wrapper], ANY)

	def test_each_wrapper_calls_frappes_own_function_once(self):
		for original, (wrapper, *_rest) in FRAPPE_V16_56.items():
			module_path, _dot, attr = original.rpartition(".")
			package, _dot, name = module_path.rpartition(".")
			with self.subTest(wrapper=wrapper):
				fn = self.functions[wrapper]
				imports = [
					n
					for n in ast.walk(fn)
					if isinstance(n, ast.ImportFrom)
					and n.module == package
					and any(a.name == name for a in n.names)
				]
				self.assertTrue(imports, f"{wrapper} must call frappe's {original}, not reimplement it")
				calls = [
					n
					for n in ast.walk(fn)
					if isinstance(n, ast.Call)
					and isinstance(n.func, ast.Attribute)
					and n.func.attr == attr
					and isinstance(n.func.value, ast.Name)
					and n.func.value.id == name
				]
				self.assertEqual(len(calls), 1, f"{wrapper} should call {name}.{attr} exactly once")

	def test_the_seal_covers_exactly_the_overridden_routes(self):
		self.assertEqual(set(ff.FILTER_WRAPPED_ORIGINALS), set(FRAPPE_V16_56))
		self.assertEqual(len(ff.FILTER_WRAPPED_ORIGINALS), len(FRAPPE_V16_56))

	def test_no_helper_is_whitelisted(self):
		wrappers = {w for w, *_rest in FRAPPE_V16_56.values()}
		for name, fn in self.functions.items():
			if name not in wrappers:
				with self.subTest(function=name):
					self.assertIsNone(self._whitelist_keywords(fn))

	def test_hooks_are_registered_and_are_not_endpoints(self):
		self.assertIn(AFTER_REQUEST, self.hooks["after_request"])
		self.assertIn(BEFORE_REQUEST, self.hooks["before_request"])
		self.assertEqual(
			[a.arg for a in self.functions["refuse_rest_list_references"].args.args], ["response", "request"]
		)
		seal = self.functions["seal_filter_originals"].args
		self.assertFalse(seal.args or seal.vararg or seal.kwarg)

	def test_the_existing_read_seal_still_runs_first(self):
		"""fieldlevel_read's seal is untouched and stays ahead of this one."""
		before = self.hooks["before_request"]
		self.assertLess(before.index(f"{READ_MODULE}.seal_wrapped_originals"), before.index(BEFORE_REQUEST))


# ---------------------------------------------------------------------------
# The rule, over stand-in metadata
# ---------------------------------------------------------------------------


class TestRule(unittest.TestCase):
	def setUp(self):
		_reset()

	def refused(self, doctype="Employee", parent=None, **kwargs):
		return _refused(ff.check_request, doctype, parent, **kwargs)

	def test_a_hidden_field_is_refused_in_every_position(self):
		cases = {
			"filters dict": {"filters": {"ctc": [">", 50000]}},
			"filters list": {"filters": [["ctc", ">", 50000]]},
			"filters [doctype, field, op, value]": {"filters": [["Employee", "ctc", ">", 1]]},
			"filters [field, value]": {"filters": [["ctc", 1]]},
			"filters single condition": {"filters": ["ctc", ">", 1]},
			"filters nested group": {"filters": [["employee_name", "=", "A"], "or", ["ctc", ">", 1]]},
			"filters wrapped group": {"filters": [[["employee_name", "=", "A"], "and", ["ctc", ">", 1]]]},
			"filters list holding a dict": {"filters": [{"ctc": 1}]},
			"filters as JSON": {"filters": json.dumps({"ctc": [">", 1]})},
			"or_filters": {"or_filters": [["ctc", ">", 1]]},
			"order_by": {"order_by": "ctc desc"},
			"order_by second part": {"order_by": "employee_name asc, ctc desc"},
			"group_by": {"group_by": "ctc"},
			"aggregate": {"fields": [{"SUM": "ctc", "as": "total"}]},
			"aggregate as JSON": {"fields": json.dumps([{"MAX": "ctc"}])},
			"nested expression": {"fields": [{"DIV": [1, {"NULLIF": ["ctc", 0]}]}]},
			"a reference": {"references": ["ctc"]},
			"an expression": {"expressions": ["ctc"]},
			"a hidden Link": {"filters": {"pay_grade": "G1"}},
			"through a hidden Link": {"order_by": "pay_grade.grade_name"},
		}
		for label, kwargs in cases.items():
			with self.subTest(label):
				self.assertTrue(self.refused(**kwargs))
				self.assertEqual(STATE["thrown"][-1], ff.REFUSAL)

	def test_visible_fields_pass_in_every_position(self):
		cases = {
			"filters dict": {"filters": {"employee_name": ["like", "%a%"], "name": "EMP-1"}},
			"filters list with a list value": {"filters": [["employee_name", "in", ["A", "B"]]]},
			"filters single condition": {"filters": ["employee_name", "in", ["A", "B"]]},
			"standard fields": {"filters": [["owner", "=", "x"], ["_assign", "like", "%x%"]]},
			"order_by": {"order_by": "`tabEmployee`.`modified` desc, employee_name asc"},
			"positional order": {"order_by": "1 desc"},
			"an alias": {"order_by": "count desc", "group_by": "department"},
			"count star": {"fields": [{"COUNT": "*", "as": "count"}]},
			"a literal": {"fields": [{"IFNULL": ["employee_name", "'none'"]}]},
			"through a visible Link": {"filters": {"department.department_name": "Ops"}},
			"plain names, left to frappe": {"fields": ["employee_name", "ctc"]},
		}
		for label, kwargs in cases.items():
			with self.subTest(label):
				self.assertFalse(self.refused(**kwargs))

	def test_child_table_notation(self):
		_reset(PROJECTS_USER)
		refused = {
			"backtick filter": {"filters": [["`tabTimesheet Detail`.`base_costing_rate`", ">", 50]]},
			"backtick filter, field unquoted": {"filters": {"`tabTimesheet Detail`.base_costing_rate": 1}},
			"backtick order_by": {"order_by": "`tabTimesheet Detail`.`base_costing_rate` desc"},
			"backtick group_by": {"group_by": "`tabTimesheet Detail`.`base_costing_rate`"},
			"backtick aggregate": {"fields": [{"SUM": "`tabTimesheet Detail`.`base_costing_rate`"}]},
			"dotted filter": {"filters": {"time_logs.base_costing_rate": [">", 1]}},
			"dotted order_by": {"order_by": "time_logs.base_costing_rate"},
			"[child doctype, field, op, value]": {
				"filters": [["Timesheet Detail", "base_costing_rate", ">", 1]]
			},
			"a child's name used bare": {"filters": {"base_costing_rate": 1}},
			"a child query dict": {"fields": [{"time_logs": ["hours", "base_costing_rate"]}]},
		}
		for label, kwargs in refused.items():
			with self.subTest(label):
				self.assertTrue(self.refused("Timesheet", **kwargs))
		passed = {
			"backtick visible": {"filters": [["`tabTimesheet Detail`.`hours`", ">", 1]]},
			"dotted visible": {"order_by": "time_logs.hours desc"},
			"[child doctype, field, op, value] visible": {"filters": [["Timesheet Detail", "hours", ">", 1]]},
			"aggregate visible": {"fields": [{"SUM": "`tabTimesheet Detail`.`hours`"}]},
		}
		for label, kwargs in passed.items():
			with self.subTest(label):
				self.assertFalse(self.refused("Timesheet", **kwargs))

	def test_a_hidden_table_hides_every_field_of_its_child(self):
		self.assertTrue(self.refused(filters={"custom_pay_rates.effective_from": [">", "2026-01-01"]}))
		self.assertTrue(self.refused(order_by="`tabEmployee Pay Rate`.`hourly_rate`"))

	def test_a_child_table_read_through_its_parent_answers_to_the_parents_levels(self):
		for kwargs in (
			{"fields": [{"MAX": "hourly_rate"}]},
			{"filters": {"hourly_rate": [">", 1]}},
			{"order_by": "hourly_rate desc"},
			{"group_by": "effective_from"},
		):
			with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
				_reset(HR_USER)
				self.assertTrue(self.refused("Employee Pay Rate", "Employee", **kwargs))
				_reset(HR_MANAGER)
				self.assertFalse(self.refused("Employee Pay Rate", "Employee", **kwargs))
		# Read with no parent named, a child table keeps only permlevel 0 (fieldlevel_read's rule).
		_reset(HR_MANAGER)
		self.assertTrue(self.refused("Employee Pay Rate", None, filters={"hourly_rate": 1}))

	def test_backtick_forms_of_the_doctypes_own_fields(self):
		for ref in ("`ctc`", "`tabEmployee`.`ctc`", "`tabEmployee`.ctc", "  ctc  "):
			with self.subTest(ref=ref):
				self.assertTrue(self.refused(filters={ref: 1}))
				self.assertTrue(self.refused(order_by=f"{ref} desc"))

	def test_malformed_input_is_refused_for_a_caller_with_hidden_fields(self):
		cases = {
			"order_by not a string": {"order_by": ["ctc"]},
			"order_by an expression": {"order_by": "ifnull(ctc, 0) desc"},
			"tab notation without backticks": {"order_by": "tabEmployee.ctc"},
			"three parts": {"order_by": "a.b.c"},
			"a dotted name that is no field": {"filters": {"nothing.here": 1}},
			"a child table of another doctype": {"filters": [["`tabOther Child`.`x`", "=", 1]]},
			"a condition of six": {"filters": [["a", "b", "c", "d", "e", "f"]]},
			"an empty condition": {"filters": [[]]},
			"a stray word in a condition list": {"filters": [["employee_name", "=", "A"], "xor"]},
			"a number for a field": {"filters": [[7, "=", 1]]},
			"an object": {"filters": object()},
			"stats that are not a list": {"references": [None]},
		}
		for label, kwargs in cases.items():
			with self.subTest(label):
				_reset(HR_USER)
				self.assertTrue(self.refused(**kwargs))
		# The same input from a caller who reads everything is left to frappe, which refuses it itself.
		for label, kwargs in cases.items():
			with self.subTest(label, who="hr manager"):
				_reset(HR_MANAGER)
				self.assertFalse(self.refused(**kwargs))

	def test_names_are_never_refused_for_their_shape(self):
		"""frappe reads a bare value, or a top-level list of plain values, as document names."""
		for filters in (
			"EMP-0001",
			json.dumps("EMP-0001"),
			["EMP-1", "EMP-2", "EMP-3"],
			["EMP-1", "EMP-2", "EMP-3", "EMP-4"],
			["EMP-1", "EMP-2"],
			7,
		):
			with self.subTest(filters=filters):
				self.assertFalse(self.refused(filters=filters))

	def test_a_caller_who_reads_everything_is_untouched(self):
		_reset(HR_MANAGER)
		self.assertFalse(self.refused(filters={"ctc": 1}, order_by="ctc", fields=[{"SUM": "ctc"}]))
		_reset(("Administrator", ()))
		self.assertFalse(self.refused(filters={"ctc": 1}, order_by="garbage(("))
		self.assertEqual(STATE["meta_calls"], [], "Administrator leaves before any metadata is read")

	def test_a_doctype_with_nothing_hidden_leaves_on_the_first_check(self):
		self.assertFalse(self.refused("ToDo", filters={"description": 1}, order_by="garbage(("))
		self.assertFalse(self.refused(None, filters={"ctc": 1}))

	def test_the_message_names_no_field_and_no_value(self):
		self.refused(filters={"ctc": [">", 123456]})
		message = STATE["thrown"][-1]
		self.assertNotIn("ctc", message)
		self.assertNotIn("123456", message)


# ---------------------------------------------------------------------------
# The wrappers
# ---------------------------------------------------------------------------


class TestWrappers(unittest.TestCase):
	def setUp(self):
		_reset()

	def assert_refused_before_frappe(self, fn, *args, **kwargs):
		self.assertTrue(_refused(fn, *args, **kwargs))
		self.assertEqual(STATE["calls"], [], "frappe's function must not run")

	def assert_passed_through(self, fn, expected_call, *args, **kwargs):
		self.assertFalse(_refused(fn, *args, **kwargs))
		self.assertEqual(len(STATE["calls"]), 1, "frappe's function runs exactly once")
		self.assertEqual(STATE["calls"][0][0], expected_call)
		return STATE["calls"][0]

	def test_report_view_routes_check_the_form(self):
		for wrapper, call in (
			("reportview_get", "reportview.get"),
			("reportview_get_list", "reportview.get_list"),
			("reportview_get_count", "reportview.get_count"),
			("reportview_export_query", "reportview.export_query"),
		):
			fn = getattr(ff, wrapper)
			for form in (
				{"doctype": "Employee", "filters": json.dumps([["Employee", "ctc", ">", 1]])},
				{"doctype": "Employee", "or_filters": json.dumps({"ctc": 1})},
				{"doctype": "Employee", "order_by": "`tabEmployee`.`ctc` desc"},
				{"doctype": "Employee", "group_by": "ctc"},
				{"doctype": "Employee", "aggregate_on_doctype": "Employee", "aggregate_on_field": "ctc"},
				{"doctype": "Employee", "aggregate_on_field": "ctc"},
				{
					"doctype": "Employee Pay Rate",
					"parent_doctype": "Employee",
					"fields": json.dumps([{"MAX": "hourly_rate"}]),
				},
			):
				with self.subTest(wrapper=wrapper, form=form):
					_reset(form=form)
					self.assert_refused_before_frappe(fn)
			with self.subTest(wrapper=wrapper, form="visible"):
				_reset(
					form={
						"doctype": "Employee",
						"fields": json.dumps(["`tabEmployee`.`name`", "`tabEmployee`.`ctc`"]),
						"filters": json.dumps([["Employee", "employee_name", "like", "%a%"]]),
						"order_by": "`tabEmployee`.`modified` desc",
						"group_by": "department",
						"aggregate_on_doctype": "Employee",
						"aggregate_on_field": "employee_name",
					}
				)
				self.assert_passed_through(fn, call)

	def test_sidebar_routes(self):
		refusals = (
			(ff.reportview_get_sidebar_stats, (json.dumps(["ctc"]), "Employee")),
			(ff.reportview_get_sidebar_stats, (["_user_tags"], "Employee", json.dumps({"ctc": 1}))),
			(
				ff.reportview_get_stats,
				(json.dumps(["_user_tags"]), "Employee", json.dumps([["ctc", ">", 1]])),
			),
			(
				ff.reportview_get_filter_dashboard_data,
				(json.dumps([{"name": "ctc", "type": "Currency"}]), "Employee"),
			),
			(ff.reportview_get_filter_dashboard_data, ("not json", "Employee")),
			(ff.listview_get_group_by_count, ("Employee", json.dumps({}), "ctc")),
			(ff.listview_get_group_by_count, ("Employee", json.dumps({"ctc": 1}), "department")),
		)
		for fn, args in refusals:
			with self.subTest(fn=fn.__name__, args=args):
				_reset()
				self.assert_refused_before_frappe(fn, *args)
		_reset()
		call = self.assert_passed_through(
			ff.reportview_get_sidebar_stats,
			"reportview.get_sidebar_stats",
			json.dumps(["_user_tags", "department"]),
			"Employee",
			json.dumps({"employee_name": "A"}),
		)
		self.assertEqual(
			call[1],
			(json.dumps(["_user_tags", "department"]), "Employee", json.dumps({"employee_name": "A"})),
		)
		_reset()
		self.assert_passed_through(
			ff.listview_get_group_by_count, "listview.get_group_by_count", "Employee", "{}", "assigned_to"
		)

	def test_client_get_list_and_count(self):
		self.assert_refused_before_frappe(ff.client_get_list, "Employee", filters={"ctc": 1})
		_reset()
		self.assert_refused_before_frappe(ff.client_get_list, "Employee", order_by="ctc")
		_reset()
		self.assert_refused_before_frappe(
			ff.client_get_list, "Employee Pay Rate", fields=[{"SUM": "hourly_rate"}], parent="Employee"
		)
		_reset()
		self.assert_refused_before_frappe(ff.client_get_count, "Employee", json.dumps([["ctc", ">", 1]]))
		_reset()
		call = self.assert_passed_through(
			ff.client_get_list, "client.get_list", "Employee", ["name", "ctc"], {"employee_name": "A"}
		)
		self.assertEqual(
			call[1],
			(
				"Employee",
				["name", "ctc"],
				{"employee_name": "A"},
				None,
				None,
				None,
				20,
				None,
				False,
				True,
				None,
				None,
			),
		)
		_reset()
		call = self.assert_passed_through(ff.client_get_count, "client.get_count", "Employee", {"name": "x"})
		self.assertEqual(call[1], ("Employee", {"name": "x"}, False, False))

	def test_client_get_checks_filters_only_when_there_is_no_name(self):
		self.assert_refused_before_frappe(ff.client_get, "Employee", filters=json.dumps({"ctc": 1}))
		_reset()
		call = self.assert_passed_through(ff.client_get, "client.get", "Employee", "EMP-1", {"ctc": 1})
		self.assertEqual(call[1], ("Employee", "EMP-1", {"ctc": 1}, None))
		_reset()
		self.assert_passed_through(ff.client_get, "client.get", "Employee", filters={"employee_name": "A"})

	def test_client_get_value_on_a_normal_doctype(self):
		self.assert_refused_before_frappe(ff.client_get_value, "Employee", "name", {"ctc": 1})
		_reset()
		self.assert_refused_before_frappe(ff.client_get_value, "Employee", json.dumps({"SUM": "ctc"}))
		_reset()
		call = self.assert_passed_through(ff.client_get_value, "client.get_value", "Employee", "ctc", "EMP-1")
		self.assertEqual(call[1], ("Employee", "ctc", "EMP-1", True, False, None))

	def test_client_get_value_on_a_single(self):
		refusals = (
			("default_rate", None, True),
			(json.dumps(["company", "default_rate"]), None, True),
			("*", None, True),
			("company", json.dumps({"default_rate": 5}), 0),
			("company", json.dumps([["default_rate", ">", 5]]), True),
			(json.dumps({"SUM": "company"}), None, True),
		)
		for fieldname, filters, as_dict in refusals:
			with self.subTest(fieldname=fieldname, filters=filters, as_dict=as_dict):
				_reset()
				self.assert_refused_before_frappe(
					ff.client_get_value, "Pay Settings", fieldname, filters, as_dict
				)
		for fieldname, filters, as_dict in (
			("company", None, True),
			(json.dumps(["company"]), None, True),
			("company", json.dumps({"company": "X"}), 0),
		):
			with self.subTest(fieldname=fieldname, filters=filters, as_dict=as_dict, outcome="passes"):
				_reset()
				self.assert_passed_through(
					ff.client_get_value, "client.get_value", "Pay Settings", fieldname, filters, as_dict
				)

	def test_a_singles_whole_record_answer_leaves_without_hidden_fields(self):
		"""A dict lookup on a Single answers with every stored value (get_values_from_single); a bare
		name is such a lookup too ({"name": ...})."""
		record = {"name": "Pay Settings", "company": "X", "default_rate": 41.5}
		for filters in (json.dumps({"company": "X"}), "Pay Settings"):
			with self.subTest(filters=filters):
				_reset()
				STATE["returns"] = {"client.get_value": dict(record)}
				result = ff.client_get_value("Pay Settings", "company", filters)
				self.assertEqual(result, {"name": "Pay Settings", "company": "X"})
				self.assertEqual(len(STATE["calls"]), 1)
		_reset(HR_MANAGER)
		STATE["returns"] = {"client.get_value": dict(record)}
		self.assertEqual(ff.client_get_value("Pay Settings", "default_rate", {"company": "X"}), record)
		# A normal doctype's answer comes from frappe.get_list, which already leaves hidden fields out.
		_reset()
		STATE["returns"] = {"client.get_value": {"name": "EMP-1", "employee_name": "A"}}
		self.assertEqual(
			ff.client_get_value("Employee", "employee_name", "EMP-1"), {"name": "EMP-1", "employee_name": "A"}
		)

	def test_client_get_single_value(self):
		self.assert_refused_before_frappe(ff.client_get_single_value, "Pay Settings", "default_rate")
		_reset()
		call = self.assert_passed_through(
			ff.client_get_single_value, "client.get_single_value", "Pay Settings", "company"
		)
		self.assertEqual(call[1], ("Pay Settings", "company"))
		_reset(HR_MANAGER)
		self.assert_passed_through(
			ff.client_get_single_value, "client.get_single_value", "Pay Settings", "default_rate"
		)

	def test_search(self):
		self.assert_refused_before_frappe(ff.search_link, "Employee", "a", filters={"ctc": [">", 1]})
		_reset()
		self.assert_refused_before_frappe(ff.search_link, "Employee", "a", searchfield="ctc")
		_reset()
		self.assert_refused_before_frappe(
			ff.search_widget, "Employee", "a", query="some.custom.query", filters=json.dumps({"ctc": 1})
		)
		_reset()
		call = self.assert_passed_through(
			ff.search_link,
			"search.search_link",
			"Employee",
			"a",
			filters={"department": "Ops"},
			link_fieldname="employee",
		)
		self.assertEqual(call[1], ("Employee", "a", None, {"department": "Ops"}, 10, None, None, False))
		self.assertEqual(call[2], {"link_fieldname": "employee"})
		_reset()
		call = self.assert_passed_through(
			ff.search_widget, "search.search_widget", "Employee", "a", for_link_validation=True
		)
		self.assertEqual(
			call[2], {"link_fieldname": None, "for_link_validation": True, "query_filters_as_dict": False}
		)

	def test_get_next(self):
		self.assert_refused_before_frappe(ff.form_get_next, "Employee", "EMP-1", 1, sort_field="ctc")
		_reset()
		self.assert_refused_before_frappe(ff.form_get_next, "Employee", "EMP-1", 1, json.dumps({"ctc": 1}))
		_reset()
		call = self.assert_passed_through(ff.form_get_next, "utils.get_next", "Employee", "EMP-1", 0)
		self.assertEqual(call[1], ("Employee", "EMP-1", 0, None, "desc", "creation"))

	def test_number_cards(self):
		card = {"document_type": "Employee Pay Rate", "parent_document_type": "Employee", "function": "Sum"}
		self.assert_refused_before_frappe(
			ff.number_card_get_result,
			json.dumps({**card, "aggregate_function_based_on": "hourly_rate"}),
			"[]",
		)
		_reset()
		self.assert_refused_before_frappe(
			ff.number_card_get_result, {"document_type": "Employee", "function": "Count"}, [["ctc", ">", 1]]
		)
		_reset()
		self.assert_passed_through(
			ff.number_card_get_result,
			"number_card.get_result",
			{"document_type": "Employee", "function": "Count", "aggregate_function_based_on": None},
			[],
		)
		_reset()
		STATE["stored"][("Number Card", "Pay Card")] = {
			"document_type": "Employee",
			"parent_document_type": None,
			"aggregate_function_based_on": "ctc",
		}
		self.assert_refused_before_frappe(
			ff.number_card_get_percentage_difference, {"name": "Pay Card"}, "[]", 5
		)
		_reset(HR_MANAGER)
		STATE["stored"][("Number Card", "Pay Card")] = {
			"document_type": "Employee",
			"aggregate_function_based_on": "ctc",
		}
		self.assert_passed_through(
			ff.number_card_get_percentage_difference,
			"number_card.get_percentage_difference",
			{"name": "Pay Card"},
			"[]",
			5,
		)

	def test_dashboard_charts(self):
		passed = {"name": "Mine", "document_type": "Employee", "chart_type": "Sum", "based_on": "modified"}
		self.assert_refused_before_frappe(
			ff.dashboard_chart_get, chart=json.dumps({**passed, "value_based_on": "ctc"}), no_cache=1
		)
		_reset()
		self.assert_refused_before_frappe(
			ff.dashboard_chart_get,
			chart=json.dumps({**passed, "chart_type": "Group By", "group_by_based_on": "pay_grade"}),
			no_cache=1,
		)
		_reset()
		self.assert_refused_before_frappe(
			ff.dashboard_chart_get,
			chart=json.dumps({**passed, "filters_json": json.dumps({"ctc": 1})}),
			no_cache=1,
		)
		_reset()
		self.assert_refused_before_frappe(
			ff.dashboard_chart_get,
			chart=json.dumps(passed),
			no_cache=1,
			filters=json.dumps([["ctc", ">", 1]]),
		)
		# A stored chart by name, and a passed chart whose name keys the cache of a stored one.
		stored = {
			"name": "Pay",
			"document_type": "Employee",
			"chart_type": "Sum",
			"based_on": "modified",
			"value_based_on": "ctc",
		}
		_reset()
		STATE["stored"][("Dashboard Chart", "Pay")] = stored
		self.assert_refused_before_frappe(ff.dashboard_chart_get, chart_name="Pay")
		_reset()
		STATE["stored"][("Dashboard Chart", "Pay")] = stored
		self.assert_refused_before_frappe(
			ff.dashboard_chart_get,
			chart=json.dumps({"name": "Pay", "document_type": "ToDo", "based_on": "modified"}),
		)
		_reset()
		call = self.assert_passed_through(
			ff.dashboard_chart_get, "dashboard_chart.get", chart=json.dumps(passed), no_cache=1
		)
		self.assertEqual(call[2]["chart"], json.dumps(passed))
		self.assertEqual(call[2]["no_cache"], 1)
		_reset(HR_MANAGER)
		STATE["stored"][("Dashboard Chart", "Pay")] = stored
		self.assert_passed_through(ff.dashboard_chart_get, "dashboard_chart.get", chart_name="Pay")


# ---------------------------------------------------------------------------
# REST list routes (after_request)
# ---------------------------------------------------------------------------


class _Request:
	def __init__(self, method, path):
		self.method = method
		self.path = path


class _Response:
	def __init__(self, status_code=200):
		self._data = b'{"data": []}'
		self.status_code = status_code
		self.set_data_calls = 0

	def get_data(self):
		return self._data

	def set_data(self, value):
		self.set_data_calls += 1
		self._data = value.encode() if isinstance(value, str) else value

	def json(self):
		return json.loads(self._data)


class TestRestLists(unittest.TestCase):
	def run_hook(self, path, form, method="GET", who=HR_USER, status_code=200):
		_reset(who, form=form)
		response = _Response(status_code)
		ff.refuse_rest_list_references(response=response, request=_Request(method, path))
		return response

	def test_list_and_count_routes_naming_a_hidden_field_get_a_403(self):
		for path, form, v2 in (
			("/api/resource/Employee", {"filters": json.dumps({"ctc": 1})}, False),
			("/api/v1/resource/Employee/", {"order_by": "ctc desc"}, False),
			(
				"/api/resource/Employee Pay Rate",
				{"parent": "Employee", "fields": [{"MAX": "hourly_rate"}]},
				False,
			),
			("/api/v2/document/Employee", {"filters": json.dumps([["ctc", ">", 1]])}, True),
			("/api/v2/document/Employee", {"group_by": "ctc"}, True),
			("/api/v2/doctype/Employee/count", {"filters": json.dumps({"ctc": 1})}, True),
		):
			with self.subTest(path=path, form=form):
				response = self.run_hook(path, form)
				self.assertEqual(response.status_code, 403)
				body = response.json()
				if v2:
					self.assertEqual(body["errors"][0]["type"], "PermissionError")
				else:
					self.assertEqual(body["exc_type"], "PermissionError")
				self.assertNotIn("data", body)

	def test_everything_else_is_left_alone(self):
		for path, form, kwargs in (
			("/api/resource/Employee", {"filters": json.dumps({"employee_name": "A"})}, {}),
			("/api/resource/Employee", {"filters": json.dumps({"ctc": 1})}, {"who": HR_MANAGER}),
			("/api/resource/Employee", {"filters": json.dumps({"ctc": 1})}, {"method": "POST"}),
			("/api/resource/Employee", {"filters": json.dumps({"ctc": 1})}, {"status_code": 403}),
			("/api/resource/Employee/EMP-1", {"filters": json.dumps({"ctc": 1})}, {}),
			("/api/method/frappe.client.get_list", {"filters": json.dumps({"ctc": 1})}, {}),
			("/app/employee", {"filters": json.dumps({"ctc": 1})}, {}),
		):
			with self.subTest(path=path, **{k: str(v) for k, v in kwargs.items()}):
				response = self.run_hook(path, form, **kwargs)
				self.assertEqual(response.set_data_calls, 0)
				self.assertEqual(response.status_code, kwargs.get("status_code", 200))

	def test_a_check_that_fails_closes_and_is_logged(self):
		"""frappe swallows an after_request exception and would send the body."""
		response = _Response()
		_reset(form={"filters": json.dumps({"employee_name": "A"})})
		sys.modules["frappe"].get_roles = lambda user=None: (_ for _ in ()).throw(RuntimeError("boom"))
		try:
			ff.refuse_rest_list_references(
				response=response, request=_Request("GET", "/api/resource/Employee")
			)
		finally:
			sys.modules["frappe"].get_roles = lambda user=None: list(STATE["roles"])
		self.assertEqual(response.status_code, 403)
		self.assertEqual(len(STATE["errors"]), 1)

	def test_missing_request_or_response_is_a_no_op(self):
		ff.refuse_rest_list_references(response=None, request=None)
		ff.refuse_rest_list_references(response=_Response(), request=None)


# ---------------------------------------------------------------------------
# The seal
# ---------------------------------------------------------------------------


class TestSeal(unittest.TestCase):
	def setUp(self):
		_reset()
		frappe = sys.modules["frappe"]
		self.originals = [_get_attr(path) for path in FRAPPE_V16_56]
		frappe.whitelisted.clear()
		frappe.whitelisted.update(self.originals)
		ff._SEALED.clear()
		ff._seal_failure_logged = False
		STATE["overrides"] = {path: f"{MODULE}.{w}" for path, (w, *_rest) in FRAPPE_V16_56.items()}

	def test_overridden_originals_leave_the_whitelist(self):
		ff.seal_filter_originals()
		for path, fn in zip(FRAPPE_V16_56, self.originals, strict=True):
			with self.subTest(original=path):
				self.assertNotIn(fn, sys.modules["frappe"].whitelisted)

	def test_a_route_this_site_does_not_override_keeps_its_original(self):
		del STATE["overrides"]["frappe.client.get_value"]
		ff.seal_filter_originals()
		self.assertIn(_get_attr("frappe.client.get_value"), sys.modules["frappe"].whitelisted)

	def test_an_original_goes_back_on_once_no_longer_overridden(self):
		ff.seal_filter_originals()
		STATE["overrides"] = {}
		ff.seal_filter_originals()
		for fn in self.originals:
			self.assertIn(fn, sys.modules["frappe"].whitelisted)
		self.assertEqual(ff._SEALED, set())

	def test_a_failure_never_raises_and_is_logged_once_per_process(self):
		STATE["get_attr_raises"] = True
		ff.seal_filter_originals()
		ff.seal_filter_originals()
		self.assertEqual(len(STATE["errors"]), 1)


if __name__ == "__main__":
	unittest.main()
