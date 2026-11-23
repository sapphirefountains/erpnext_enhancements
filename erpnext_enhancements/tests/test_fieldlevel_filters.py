"""Field references in read requests, held to the caller's field-level read (v1.835.0).

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
* **Ordinary requests**, from a caller who cannot read the level-1 ``allowed_companies`` table that
  ERPNext v16.54.0's Item, Customer and Supplier carry: the link searches ERPNext sets up (with the
  ``query`` and ``filters`` it sends), searches with no custom query, list view, Kanban, report-view
  group-by, sidebar counts and tags, next/previous, number cards, charts, REST lists and standard
  columns all pass (``TestOrdinaryRequests``); the same routes naming a field hidden from that
  caller are still refused (``TestStillRefused``).

* **Names, list shapes and other doctypes**: a field named in another case is that field, in every
  position (``TestNamesInAnyCase``); a list of filters is read with and without its None entries
  (``TestListShapes``); ``fields`` and ``filters`` are read either way round where the route reaches
  ``frappe.get_list`` (``TestFieldsAndFiltersEitherWay``); on ``frappe.client.get``, a field named
  in another doctype is held to that doctype's levels (``TestAnotherDoctypesFields``); every spelling
  of a closed child table (``TestClosedChildTableSpellings``).

Bench-free: installs a ``frappe`` stub in ``setUpModule``, so it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_fieldlevel_filters
"""

import ast
import functools
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
#: frappe/client.py, frappe/desk/search.py, frappe/desk/form/utils.py, frappe/desk/calendar.py and the
#: Number Card and Dashboard Chart controllers at the v16.56.0 tag.
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
		# start / include_image / keep_order: frappe 16.60's additions (v1.835.1), forwarded only to a
		# frappe that declares them.
		["link_fieldname", "start", "include_image", "keep_order"],
		[None, 0, False, False],
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
		# keep_order: frappe 16.60's addition (v1.835.1).
		["link_fieldname", "for_link_validation", "query_filters_as_dict", "keep_order"],
		[None, False, False, False],
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
	"frappe.desk.calendar.get_events": (
		"calendar_get_events",
		["doctype", "start", "end", "field_map", "filters", "fields", "order_by"],
		[None, None, None],
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


def _perm(role, permlevel=0, read=1, select=0):
	return _Row(role=role, permlevel=permlevel, read=read, select=select)


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
		[
			_df("company"),
			_df("default_rate", 1, "Currency"),
			_df("rate_rows", 1, "Table", "Employee Pay Rate"),
		],
		[_perm("HR User"), _perm("HR Manager"), _perm("HR Manager", 1)],
		issingle=True,
	),
	"Department": _Meta("Department", [_df("department_name")], [_perm("All")]),
	"Other Child": _Meta("Other Child", [_df("x")], istable=True),
	# Nothing above level 0, for anyone, and three Links: to Employee (level-1 ``ctc``), to User (a
	# Desk user has ``select`` only) and to Department (everyone reads it).
	"ToDo": _Meta(
		"ToDo",
		[
			_df("description"),
			_df("allocated_to", 0, "Link", "User"),
			_df("employee", 0, "Link", "Employee"),
			_df("department", 0, "Link", "Department"),
		],
		[_perm("All")],
	),
	# A level-1 table whose child doctype has no space in its name, beside a level-0 table whose
	# fieldname starts with "tab".
	"Holiday List": _Meta(
		"Holiday List",
		[
			_df("holiday_list_name"),
			_df("holidays", 1, "Table", "Holiday"),
			_df("table_notes", 0, "Table", "Holiday Note"),
		],
		[_perm("HR User"), _perm("HR Manager"), _perm("HR Manager", 1)],
	),
	"Holiday": _Meta("Holiday", [_df("holiday_date", 0, "Date"), _df("description")], istable=True),
	"Holiday Note": _Meta("Holiday Note", [_df("note")], istable=True),
	# ERPNext v16.54.0's shapes (stock/doctype/item, selling/doctype/customer, buying/doctype/supplier):
	# each has a level-1 `allowed_companies` table (Company Restriction, whose one field is `company`),
	# after level-0 tables whose child doctypes also have a `company` field. Trimmed to what is read.
	"Item": _Meta(
		"Item",
		[
			_df("item_name"),
			_df("item_group", 0, "Link", "Item Group"),
			_df("is_stock_item", 0, "Check"),
			_df("is_sales_item", 0, "Check"),
			_df("is_purchase_item", 0, "Check"),
			_df("has_variants", 0, "Check"),
			_df("disabled", 0, "Check"),
			_df("item_defaults", 0, "Table", "Item Default"),
			_df("supplier_items", 0, "Table", "Item Supplier"),
			_df("company_restrictions_section", 1, "Section Break"),
			_df("restrict_to_companies", 1, "Check"),
			_df("allowed_companies", 1, "Table", "Company Restriction"),
		],
		[
			_perm("Stock User"),
			_perm("Sales User"),
			_perm("Purchase User"),
			_perm("Item Manager"),
			_perm("Item Manager", 1),
		],
	),
	"Item Default": _Meta(
		"Item Default",
		[_df("company", 0, "Link", "Company"), _df("default_warehouse", 0, "Link", "Warehouse")],
		istable=True,
	),
	"Item Supplier": _Meta("Item Supplier", [_df("supplier", 0, "Link", "Supplier")], istable=True),
	"Company Restriction": _Meta("Company Restriction", [_df("company", 0, "Link", "Company")], istable=True),
	"Customer": _Meta(
		"Customer",
		[
			_df("customer_name"),
			_df("customer_group", 0, "Link", "Customer Group"),
			_df("disabled", 0, "Check"),
			_df("companies", 0, "Table", "Allowed To Transact With"),
			_df("accounts", 0, "Table", "Party Account"),
			_df("restrict_to_companies", 1, "Check"),
			_df("allowed_companies", 1, "Table", "Company Restriction"),
		],
		[
			_perm("Sales User"),
			_perm("Accounts User"),
			_perm("Sales Master Manager"),
			_perm("Sales Master Manager", 1),
		],
	),
	"Supplier": _Meta(
		"Supplier",
		[
			_df("supplier_name"),
			_df("supplier_group", 0, "Link", "Supplier Group"),
			_df("disabled", 0, "Check"),
			_df("companies", 0, "Table", "Allowed To Transact With"),
			_df("accounts", 0, "Table", "Party Account"),
			_df("restrict_to_companies", 1, "Check"),
			_df("allowed_companies", 1, "Table", "Company Restriction"),
		],
		[_perm("Purchase User"), _perm("Purchase Master Manager"), _perm("Purchase Master Manager", 1)],
	),
	"Allowed To Transact With": _Meta(
		"Allowed To Transact With", [_df("company", 0, "Link", "Company")], istable=True
	),
	"Party Account": _Meta(
		"Party Account",
		[_df("company", 0, "Link", "Company"), _df("account", 0, "Link", "Account")],
		istable=True,
	),
	"Project": _Meta(
		"Project",
		[
			_df("project_name"),
			_df("status", 0, "Select"),
			_df("customer", 0, "Link", "Customer"),
			_df("expected_end_date", 0, "Date"),
			_df("percent_complete", 0, "Percent"),
			_df("estimated_costing", 1, "Currency"),
		],
		[_perm("Projects User"), _perm("Projects Manager"), _perm("Projects Manager", 1)],
	),
	# A stand-in whose level-1 table comes before a level-0 table with a field of the same name, so the
	# order frappe searches child tables in decides the answer.
	"Rate Card": _Meta(
		"Rate Card",
		[
			_df("title"),
			_df("restricted_rows", 1, "Table", "Company Restriction"),
			_df("default_rows", 0, "Table", "Item Default"),
		],
		[_perm("Stock User"), _perm("Item Manager"), _perm("Item Manager", 1)],
	),
	# A stand-in child doctype with a field that shares a standard column's name: frappe never looks a
	# standard column up in a child table (query.py 980), so neither may the rule.
	"Odd Row": _Meta("Odd Row", [_df("owner"), _df("_assign"), _df("idx", 0, "Int")], istable=True),
	"Odd Parent": _Meta(
		"Odd Parent",
		[_df("title"), _df("odd_rows", 1, "Table", "Odd Row")],
		[_perm("Stock User"), _perm("Item Manager"), _perm("Item Manager", 1)],
	),
	# frappe 16.56.0's User (frappe/core/doctype/user/user.json at the tag), trimmed: its level-1 fields
	# and tables as there, a few level-0 fields and tables, and its permissions: read at levels 0 and 1
	# for System Manager, `select` only (no read) for Desk User.
	"User": _Meta(
		"User",
		[
			_df("enabled", 0, "Check"),
			_df("email"),
			_df("full_name"),
			_df("sb1", 1, "Section Break"),
			_df("role_profile_name", 1, "Link", "Role Profile"),
			_df("roles", 1, "Table", "Has Role"),
			_df("reset_password_key", 1),
			_df("user_emails", 1, "Table", "User Email"),
			_df("block_modules", 1, "Table", "Block Module"),
			_df("defaults", 0, "Table", "DefaultValue"),
			_df("user_type", 1, "Link", "User Type"),
			_df("login_after", 1, "Int"),
			_df("login_before", 1, "Int"),
			_df("restrict_ip", 1, "Small Text"),
			_df("api_key", 1),
			_df("api_secret", 1, "Password"),
			_df("social_logins", 0, "Table", "User Social Login"),
			_df("role_profiles", 1, "Table MultiSelect", "User Role Profile"),
		],
		[_perm("System Manager"), _perm("System Manager", 1), _perm("Desk User", 0, read=0, select=1)],
	),
	# A stand-in with nothing above level 0, a table included: a caller with no read on it still
	# cannot read its child table.
	"Site Visit": _Meta(
		"Site Visit",
		[_df("visit_date", 0, "Date"), _df("checks", 0, "Table", "Visit Check")],
		[_perm("Projects User")],
	),
	"Visit Check": _Meta("Visit Check", [_df("item")], istable=True),
	"Has Role": _Meta("Has Role", [_df("role", 0, "Link", "Role")], istable=True),
	"User Email": _Meta("User Email", [_df("email_account", 0, "Link", "Email Account")], istable=True),
	"Block Module": _Meta("Block Module", [_df("module")], istable=True),
	"DefaultValue": _Meta("DefaultValue", [_df("defkey"), _df("defvalue")], istable=True),
	"User Social Login": _Meta("User Social Login", [_df("provider"), _df("userid")], istable=True),
	"User Role Profile": _Meta(
		"User Role Profile", [_df("role_profile", 0, "Link", "Role Profile")], istable=True
	),
}

HR_USER = ("hr.user@example.com", ("HR User", "All"))
HR_MANAGER = ("hr.manager@example.com", ("HR Manager", "All"))
PROJECTS_USER = ("pu@example.com", ("Projects User", "All"))
#: Reads level 0 of Item, Supplier and Project, and nothing of Customer (no Sales User role).
STAFF = ("staff@example.com", ("Stock User", "Purchase User", "Projects User", "All"))
#: Reads Customer at level 0.
SALES_USER = ("sales@example.com", ("Sales User", "All"))
#: A Desk user below System Manager: `select` on User, no read at any level of it.
DESK_USER = ("desk@example.com", ("Desk User", "Sales User", "All"))
SYSTEM_MANAGER = ("sm@example.com", ("System Manager", "Desk User", "All"))
#: The request the Create Project prompt sends (public/js/crm_enhancements/create_project_prompt.js)
#: and the filters of frappe's Users workspace cards.
DESK_USERS_FILTERS = {"enabled": 1, "user_type": "System User"}
USERS_CARD_FILTERS = [["User", "enabled", "=", 1], ["User", "user_type", "=", "System User"]]


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
	# frappe.get_hooks(name): that hook's merged value, or [] when no app declares it.
	frappe.get_hooks = lambda hook=None, *a, **k: STATE["hooks"].get(hook, [])

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
	desk.calendar = _module("frappe.desk.calendar", ["get_events"])
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
		hooks={},
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

	def refused(self, doctype="Employee", parent=None, child_names=True, **kwargs):
		return _refused(ff.check_request, doctype, parent, child_names=child_names, **kwargs)

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
			["tabloid.weekly", "EMP-2", "EMP-3"],
			[None, "EMP-1"],
			[None],
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
		# By keyword: frappe's function sits behind frappe.read_only, and its argument check reads
		# keywords only through a decorator.
		self.assertEqual(call[1], ())
		self.assertEqual(
			call[2],
			{
				"stats": json.dumps(["_user_tags", "department"]),
				"doctype": "Employee",
				"filters": json.dumps({"employee_name": "A"}),
			},
		)
		_reset()
		call = self.assert_passed_through(
			ff.reportview_get_stats, "reportview.get_stats", json.dumps(["_user_tags"]), "Employee"
		)
		self.assertEqual(call[1], ())
		self.assertEqual(
			call[2], {"stats": json.dumps(["_user_tags"]), "doctype": "Employee", "filters": None}
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
			ff.search_widget, "Employee", "a", query="some.custom.query", searchfield="ctc"
		)
		# With a custom query, the filters are that query's arguments and are not read as field names.
		_reset()
		call = self.assert_passed_through(
			ff.search_widget,
			"search.search_widget",
			"Employee",
			"a",
			query="some.custom.query",
			filters=json.dumps({"ctc": 1}),
		)
		self.assertEqual(call[1][2], "some.custom.query")
		self.assertEqual(call[1][6], json.dumps({"ctc": 1}))
		_reset()
		call = self.assert_passed_through(
			ff.search_link,
			"search.search_link",
			"Employee",
			"a",
			filters={"department": "Ops"},
			link_fieldname="employee",
		)
		# By keyword: frappe's function sits behind http_cache.
		self.assertEqual(call[1], ())
		self.assertEqual(
			call[2],
			{
				"doctype": "Employee",
				"txt": "a",
				"query": None,
				"filters": {"department": "Ops"},
				"page_length": 10,
				"searchfield": None,
				"reference_doctype": None,
				"ignore_user_permissions": False,
				"link_fieldname": "employee",
				# The stub names only *args/**kwargs, so frappe 16.60's arguments are not forwarded to
				# it: a catch-all is not taken as acceptance (see TestSixteenSixty).
			},
		)
		_reset()
		call = self.assert_passed_through(
			ff.search_widget, "search.search_widget", "Employee", "a", for_link_validation=True
		)
		self.assertEqual(
			call[2], {"link_fieldname": None, "for_link_validation": True, "query_filters_as_dict": False}
		)

	def test_a_doctype_that_is_not_a_string_is_left_to_frappes_type_check(self):
		"""frappe answers a wrongly typed argument with its own type error; the check must not fail
		on it first."""
		registered = {"standard_queries": {"User": ["frappe.core.doctype.user.user.user_query"]}}
		searches = ((ff.search_link, "search.search_link"), (ff.search_widget, "search.search_widget"))
		for doctype in (["Item"], {"doctype": "Item"}, 7, None, True):
			for fn, call in searches:
				with self.subTest(doctype=doctype, fn=fn.__name__):
					_reset()
					STATE["hooks"] = registered
					self.assert_passed_through(fn, call, doctype, "a")
		_reset()
		self.assert_passed_through(ff.client_get, "client.get", ["Employee"], filters=json.dumps({"ctc": 1}))
		_reset()
		self.assert_passed_through(ff.client_get_list, "client.get_list", {"a": 1}, filters={"ctc": 1})

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
		# A Count card keeps the name of the field it used to total: frappe counts * for one and never
		# reads that field (number_card.py 140-145), so neither does the check.
		_reset()
		self.assert_passed_through(
			ff.number_card_get_result,
			"number_card.get_result",
			{"document_type": "Employee", "function": "Count", "aggregate_function_based_on": "ctc"},
			[],
		)
		_reset()
		STATE["stored"][("Number Card", "Head Count")] = {
			"document_type": "Employee",
			"function": "Count",
			"aggregate_function_based_on": "ctc",
		}
		self.assert_passed_through(
			ff.number_card_get_percentage_difference,
			"number_card.get_percentage_difference",
			{"name": "Head Count"},
			"[]",
			5,
		)
		_reset()
		STATE["stored"][("Number Card", "Head Count")] = {
			"document_type": "Employee",
			"function": "Count",
			"aggregate_function_based_on": "ctc",
		}
		self.assert_refused_before_frappe(
			ff.number_card_get_percentage_difference, {"name": "Head Count"}, json.dumps({"ctc": 1}), 5
		)
		for function in ("Sum", "Average", "Minimum", "Maximum", None):
			with self.subTest(function=function):
				_reset()
				self.assert_refused_before_frappe(
					ff.number_card_get_result,
					{"document_type": "Employee", "function": function, "aggregate_function_based_on": "ctc"},
					[],
				)
		_reset()
		STATE["stored"][("Number Card", "Pay Card")] = {
			"document_type": "Employee",
			"parent_document_type": None,
			"function": "Sum",
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

	def test_calendar_events(self):
		dates = ("2026-01-01", "2026-02-01")
		field_map = {"start": "modified", "end": "modified", "title": "employee_name"}
		as_sent = json.dumps(field_map)
		refusals = {
			"the start field": {"field_map": json.dumps({**field_map, "start": "ctc"})},
			"the end field, in another case": {"field_map": json.dumps({**field_map, "end": "CTC"})},
			"filters": {"field_map": as_sent, "filters": json.dumps([["Employee", "ctc", ">", 1]])},
			"order_by": {"field_map": as_sent, "order_by": "ctc desc"},
			"an aggregate in fields": {"field_map": as_sent, "fields": json.dumps([{"MAX": "ctc"}])},
			"a field map that cannot be read": {"field_map": "not json"},
			"a field map with no date fields": {"field_map": json.dumps({"title": "employee_name"})},
		}
		for label, kwargs in refusals.items():
			with self.subTest(label):
				_reset()
				self.assert_refused_before_frappe(ff.calendar_get_events, "Employee", *dates, **kwargs)
		_reset()
		call = self.assert_passed_through(
			ff.calendar_get_events,
			"calendar.get_events",
			"Employee",
			*dates,
			as_sent,
			fields=json.dumps(["name", "employee_name"]),
		)
		self.assertEqual(call[1], ())
		self.assertEqual(
			call[2],
			{
				"doctype": "Employee",
				"start": dates[0],
				"end": dates[1],
				"field_map": as_sent,
				"filters": None,
				"fields": json.dumps(["name", "employee_name"]),
				"order_by": None,
			},
		)
		# A caller who reads everything is left to frappe, whatever the request.
		_reset(HR_MANAGER)
		self.assert_passed_through(
			ff.calendar_get_events, "calendar.get_events", "Employee", *dates, "not json", order_by="ctc"
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
# frappe 16.60's new link-search arguments (v1.835.1)
# ---------------------------------------------------------------------------


def _search_link_v16_56(
	doctype,
	txt,
	query=None,
	filters=None,
	page_length=10,
	searchfield=None,
	reference_doctype=None,
	ignore_user_permissions=False,
	*,
	link_fieldname=None,
):
	STATE["calls"].append(("search.search_link", (), {"doctype": doctype, "link_fieldname": link_fieldname}))
	return []


def _search_link_v16_60(
	doctype,
	txt,
	query=None,
	filters=None,
	page_length=10,
	searchfield=None,
	reference_doctype=None,
	ignore_user_permissions=False,
	*,
	link_fieldname=None,
	start=0,
	include_image=False,
	keep_order=False,
):
	STATE["calls"].append(
		(
			"search.search_link",
			(),
			{"start": start, "include_image": include_image, "keep_order": keep_order},
		)
	)
	return []


def _search_widget_v16_56(
	doctype,
	txt,
	query=None,
	searchfield=None,
	start=0,
	page_length=10,
	filters=None,
	filter_fields=None,
	as_dict=False,
	reference_doctype=None,
	ignore_user_permissions=False,
	*,
	link_fieldname=None,
	for_link_validation=False,
	query_filters_as_dict=False,
):
	STATE["calls"].append(("search.search_widget", (doctype, txt), {}))
	return []


def _search_widget_v16_60(
	*args, link_fieldname=None, for_link_validation=False, query_filters_as_dict=False, keep_order=False
):
	STATE["calls"].append(("search.search_widget", args, {"keep_order": keep_order}))
	return []


def _frappe_layers(fn):
	"""``fn`` under two ``functools.wraps`` wrappers, as frappe stacks ``http_cache`` and the
	whitelist's ``validate_argument_types`` on ``search_link``."""

	def layer(inner):
		@functools.wraps(inner)
		def wrapper(*args, **kwargs):
			return inner(*args, **kwargs)

		return wrapper

	return layer(layer(fn))


class TestSixteenSixty(unittest.TestCase):
	"""frappe 16.60 gave ``search_link`` ``start``, ``include_image`` and ``keep_order``, and
	``search_widget`` ``keep_order``; 16.56 has none of them. ``frappe.call`` passes a whitelisted
	function only the names it declares, so the wrappers declare them and forward them only to a frappe
	that declares them too: dropping them loses the Link dropdown's paging, passing them to 16.56 raises
	``TypeError``. A request carries them as text, so they are read as frappe's annotations would read
	them."""

	def setUp(self):
		_reset()
		self.search = sys.modules["frappe.desk.search"]
		self.saved = (self.search.search_link, self.search.search_widget)

	def tearDown(self):
		self.search.search_link, self.search.search_widget = self.saved

	def test_16_60_gets_the_new_arguments_typed(self):
		self.search.search_link = _search_link_v16_60
		ff.search_link("Employee", "a", start="20", include_image="1", keep_order="0")
		self.assertEqual(
			STATE["calls"],
			[("search.search_link", (), {"start": 20, "include_image": True, "keep_order": False})],
		)

	def test_16_60_defaults_when_the_desk_sends_nothing(self):
		self.search.search_link = _search_link_v16_60
		ff.search_link("Employee", "a")
		self.assertEqual(STATE["calls"][0][2], {"start": 0, "include_image": False, "keep_order": False})

	def test_16_56_never_receives_them(self):
		"""No TypeError on the frappe production ran until 2026-11-23, whatever the request carries."""
		self.search.search_link = _search_link_v16_56
		ff.search_link("Employee", "a", link_fieldname="employee", start="20", include_image=1, keep_order=1)
		self.assertEqual(
			STATE["calls"],
			[("search.search_link", (), {"doctype": "Employee", "link_fieldname": "employee"})],
		)

	def test_search_widget_keep_order(self):
		self.search.search_widget = _search_widget_v16_60
		ff.search_widget("Employee", "a", keep_order="true")
		self.assertEqual(STATE["calls"][0][2], {"keep_order": True})
		_reset()
		self.search.search_widget = _search_widget_v16_56
		ff.search_widget("Employee", "a", keep_order=True)
		self.assertEqual(STATE["calls"], [("search.search_widget", ("Employee", "a"), {})])

	def test_the_field_check_still_runs_first(self):
		self.search.search_link = _search_link_v16_60
		self.assertTrue(_refused(ff.search_link, "Employee", "a", searchfield="ctc", start=10))
		self.assertEqual(STATE["calls"], [], "frappe's function must not run")

	def test_text_values_read_as_frappe_reads_them(self):
		for raw, flag in (
			("1", True),
			("0", False),
			("true", True),
			("false", False),
			("", False),
			(1, True),
			(0, False),
			(None, False),
		):
			with self.subTest(raw=raw):
				self.assertIs(ff._as_flag(raw), flag)
		for raw, number in (("20", 20), (" 5 ", 5), (7, 7), (None, 0), ("", 0), ("x", 0)):
			with self.subTest(raw=raw):
				self.assertEqual(ff._as_int(raw), number)

	def test_an_unreadable_signature_forwards_nothing(self):
		self.assertEqual(ff._declared(object(), start=1), {})

	def test_read_through_frappes_two_decorator_layers(self):
		"""Production's ``search_link`` sits under ``http_cache`` and the whitelist's argument check,
		both ``functools.wraps`` wrappers taking ``(*args, **kwargs)``. The signature must be read
		through them, or every 16.56 search would get names it does not know."""
		for original, forwarded in (
			(_search_link_v16_60, {"start": 3, "include_image": True, "keep_order": True}),
			(_search_link_v16_56, {}),
		):
			with self.subTest(original=original.__name__):
				wrapped = _frappe_layers(original)
				self.assertEqual(
					ff._declared(wrapped, start=3, include_image=True, keep_order=True), forwarded
				)
		_reset()
		self.search.search_link = _frappe_layers(_search_link_v16_56)
		ff.search_link("Employee", "a", start="20", include_image="1", keep_order="1")
		self.assertEqual(STATE["calls"][0][2], {"doctype": "Employee", "link_fieldname": None})

	def test_a_catch_all_is_not_taken_as_acceptance(self):
		"""A decorator without ``wraps`` shows only ``(*args, **kwargs)``; forwarding there would raise
		on the frappe it hides, so nothing is forwarded."""

		def bare(*args, **kwargs):
			return _search_link_v16_56(*args, **kwargs)

		self.assertEqual(ff._declared(bare, start=1, include_image=True, keep_order=True), {})
		self.search.search_link = bare
		ff.search_link("Employee", "a", start=5)
		self.assertEqual(len(STATE["calls"]), 1)


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

	def test_a_head_request_is_checked_like_a_get(self):
		"""A werkzeug rule for GET answers HEAD too, and its status and length say as much as a body."""
		for path, form in (
			("/api/resource/Employee", {"filters": json.dumps({"ctc": 1})}),
			("/api/v2/document/Employee", {"order_by": "ctc desc"}),
			("/api/v2/doctype/Employee/count", {"filters": json.dumps({"ctc": 1})}),
		):
			with self.subTest(path=path):
				response = self.run_hook(path, form, method="HEAD")
				self.assertEqual(response.status_code, 403)
				self.assertNotIn("data", response.json())
		response = self.run_hook(
			"/api/resource/Employee", {"filters": json.dumps({"employee_name": "A"})}, method="HEAD"
		)
		self.assertEqual((response.status_code, response.set_data_calls), (200, 0))

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
# Ordinary Desk and ERPNext requests, from a caller with hidden fields
# ---------------------------------------------------------------------------

COMPANY = "Test Company"
ITEM_QUERY = "erpnext.controllers.queries.item_query"
PARTY_QUERY = "erpnext.controllers.queries.party_query"
SALES_ITEM_FILTERS = {"is_sales_item": 1, "customer": "CUST-0001", "has_variants": 0, "company": COMPANY}

#: The link searches ERPNext v16.54.0 sets up, each as (doctype, reference_doctype, query, filters).
#: Read from erpnext/public/js/utils/sales_common.js 41-48 and 77-90, public/js/controllers/buying.js
#: 85 and 92-115, public/js/queries.js 15-33, accounts/doctype/sales_invoice/sales_invoice.js 327-336,
#: stock/doctype/material_request/material_request.js 25-30 and 796-814 and
#: stock/doctype/stock_reconciliation/stock_reconciliation.js 20-28.
ERPNEXT_SEARCHES = {
	"item on Quotation": ("Item", "Quotation Item", ITEM_QUERY, SALES_ITEM_FILTERS),
	"item on Sales Order": ("Item", "Sales Order Item", ITEM_QUERY, SALES_ITEM_FILTERS),
	"item on Sales Invoice": ("Item", "Sales Invoice Item", ITEM_QUERY, SALES_ITEM_FILTERS),
	"item on Sales Invoice, timesheet dialog": (
		"Item",
		"Sales Invoice",
		ITEM_QUERY,
		{"is_sales_item": 1, "customer": "CUST-0001", "has_variants": 0},
	),
	"item on Purchase Order": (
		"Item",
		"Purchase Order Item",
		ITEM_QUERY,
		{"supplier": "SUP-0001", "is_purchase_item": 1, "has_variants": 0, "company": COMPANY},
	),
	"item on a subcontracted Purchase Order": (
		"Item",
		"Purchase Order Item",
		ITEM_QUERY,
		{"supplier": "SUP-0001", "company": COMPANY, "is_stock_item": 0},
	),
	"item on Material Request": ("Item", "Material Request Item", ITEM_QUERY, {"company": COMPANY}),
	"item on a Manufacture Material Request": (
		"Item",
		"Material Request Item",
		ITEM_QUERY,
		{"is_stock_item": 1, "company": COMPANY, "include_item_in_manufacturing": 1},
	),
	"item on Stock Reconciliation": (
		"Item",
		"Stock Reconciliation Item",
		ITEM_QUERY,
		{"is_stock_item": 1, "company": COMPANY},
	),
	"supplier on Purchase Order": (
		"Supplier",
		"Purchase Order",
		PARTY_QUERY,
		{"disabled": 0, "company": COMPANY},
	),
	"customer on Sales Invoice": (
		"Customer",
		"Sales Invoice",
		PARTY_QUERY,
		{"disabled": 0, "company": COMPANY},
	),
}

#: frappe 16.56.0's list view filters: [doctype, field, operator, value], from filter_area.get()
#: (base_list.js 497-501), with the Assigned To and Tags filters on _assign and _user_tags.
PROJECT_FILTERS = [
	["Project", "status", "=", "Open"],
	["Project", "_assign", "like", "%staff@example.com%"],
	["Project", "_user_tags", "like", "%Urgent%"],
	["Project", "customer", "=", "CUST-0001"],
]

STANDARD_NAMES = (
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"docstatus",
	"idx",
	"parent",
	"parenttype",
	"parentfield",
	"_assign",
	"_user_tags",
	"_comments",
	"_liked_by",
	"_seen",
)


class _RouteCase(unittest.TestCase):
	def setUp(self):
		_reset(STAFF)

	def passes(self, fn, *args, **kwargs):
		"""``fn`` is not refused and calls frappe's function once."""
		calls_before = len(STATE["calls"])
		self.assertFalse(_refused(fn, *args, **kwargs), f"{fn.__name__} refused {args} {kwargs}")
		self.assertEqual(len(STATE["calls"]), calls_before + 1)

	def refuses(self, fn, *args, **kwargs):
		calls_before = len(STATE["calls"])
		self.assertTrue(_refused(fn, *args, **kwargs), f"{fn.__name__} passed {args} {kwargs}")
		self.assertEqual(len(STATE["calls"]), calls_before, "frappe's function must not run")

	def rest(self, path, form, who=STAFF):
		_reset(who, form=form)
		response = _Response()
		ff.refuse_rest_list_references(response=response, request=_Request("GET", path))
		return response.status_code


class TestOrdinaryRequests(_RouteCase):
	"""What the Desk and ERPNext send every day, from STAFF, who cannot read the level-1
	``allowed_companies`` table on Item, Customer and Supplier, or Project's level-1 field."""

	def test_erpnext_link_searches_with_their_custom_query(self):
		"""The link control sends the query's filters as JSON on a GET and as a dict on a POST
		(frappe 16.56.0 controls/link.js 437-479 and 790-843)."""
		for label, (doctype, reference_doctype, query, filters) in ERPNEXT_SEARCHES.items():
			with self.subTest(label, route="search_link GET"):
				_reset(STAFF)
				self.passes(
					ff.search_link,
					doctype,
					"",
					query=query,
					filters=json.dumps(filters),
					page_length=10,
					reference_doctype=reference_doctype,
					link_fieldname="item_code" if doctype == "Item" else doctype.lower(),
				)
			with self.subTest(label, route="search_link POST"):
				_reset(STAFF)
				self.passes(ff.search_link, doctype, "abc", query=query, filters=dict(filters))
			with self.subTest(label, route="search_widget"):
				_reset(STAFF)
				self.passes(ff.search_widget, doctype, "abc", query=query, filters=dict(filters))

	def test_the_same_filters_without_a_custom_query(self):
		"""frappe makes each key of a dict a ``[doctype, key, operator, value]`` row (search.py 190-196),
		so a key the doctype lacks is never looked up in a child table."""
		for label, (doctype, _ref, _query, filters) in ERPNEXT_SEARCHES.items():
			with self.subTest(label):
				_reset(STAFF)
				self.passes(ff.search_link, doctype, "", filters=json.dumps(filters))

	def test_link_search_with_ordinary_filters(self):
		for doctype, filters in (
			("Item", {"is_stock_item": 1, "disabled": 0, "item_group": "Products"}),
			("Item", {"include_disabled": 1, "item_group": "Products"}),
			("Item", [["Item", "item_group", "=", "Products"], ["Item Default", "company", "=", COMPANY]]),
			("Item", [["item_group", "=", "Products"]]),
			("Item", [["company", "=", COMPANY]]),
			("Supplier", {"supplier_group": "Local", "company": COMPANY}),
			("Project", {"status": "Open", "customer": "CUST-0001"}),
			("Rate Card", {"company": COMPANY}),
			("Rate Card", [["Rate Card", "company", "=", COMPANY]]),
		):
			with self.subTest(doctype=doctype, filters=filters):
				_reset(STAFF)
				self.passes(ff.search_link, doctype, "a", filters=json.dumps(filters))
				_reset(STAFF)
				self.passes(ff.search_widget, doctype, "a", filters=filters, searchfield="item_name")

	def test_a_registered_standard_query_takes_the_custom_path(self):
		"""frappe uses the doctype's ``standard_queries`` hook when the request names no query
		(search.py 125-128), and hands it the filters as arguments."""
		STATE["hooks"]["standard_queries"] = {"Item": ["custom_app.queries.item_search"]}
		self.passes(ff.search_link, "Item", "a", filters=json.dumps({"restrict_to_companies": 1}))
		_reset(STAFF)
		STATE["hooks"]["standard_queries"] = {"Item": ["custom_app.queries.item_search"]}
		self.passes(ff.search_widget, "Item", "a", query="", filters={"restrict_to_companies": 1})

	def test_list_view_kanban_and_count(self):
		list_form = {
			"doctype": "Project",
			"fields": json.dumps(
				[f"`tabProject`.`{name}`" for name in ("name", "status", "project_name", *STANDARD_NAMES[1:])]
			),
			"filters": json.dumps(PROJECT_FILTERS),
			"order_by": "`tabProject`.`modified` desc",
			"start": 0,
			"page_length": 20,
			"view": "List",
			"group_by": None,
			"with_comment_count": 1,
		}
		kanban_form = dict(
			list_form,
			view="Kanban",
			filters=json.dumps([*PROJECT_FILTERS, ["Project", "name", "in", ["PROJ-0001", "PROJ-0002"]]]),
		)
		item_form = {
			"doctype": "Item",
			"fields": json.dumps(["`tabItem`.`name`", "`tabItem Default`.`company`"]),
			"filters": json.dumps([["Item", "disabled", "=", 0], ["Item Default", "company", "=", COMPANY]]),
			"order_by": "`tabItem`.`modified` desc",
			"group_by": "`tabItem`.`name`",
		}
		for form in (list_form, kanban_form, item_form):
			for fn in (ff.reportview_get, ff.reportview_get_list, ff.reportview_get_count):
				with self.subTest(fn=fn.__name__, view=form.get("view"), doctype=form["doctype"]):
					_reset(STAFF, form=form)
					self.passes(fn)

	def test_report_view_group_by_and_aggregate(self):
		"""``group_by.js`` set_args (frappe 16.56.0, ui/group_by/group_by.js 286-319)."""
		base = {
			"doctype": "Project",
			"fields": json.dumps(["`tabProject`.`status`"]),
			"filters": json.dumps(PROJECT_FILTERS),
			"with_comment_count": False,
			"aggregate_on_doctype": "Project",
			"group_by": "`tabProject`.`status`",
			"order_by": "_aggregate_column desc",
		}
		for on_field, function in (
			("name", "count"),
			("percent_complete", "sum"),
			("percent_complete", "avg"),
		):
			with self.subTest(aggregate_on_field=on_field, aggregate_function=function):
				_reset(STAFF, form=dict(base, aggregate_on_field=on_field, aggregate_function=function))
				self.passes(ff.reportview_get)

	def test_sidebar_group_by_counts_and_tags(self):
		for field in ("status", "customer", "assigned_to", "owner"):
			with self.subTest(field=field):
				_reset(STAFF)
				self.passes(ff.listview_get_group_by_count, "Project", json.dumps(PROJECT_FILTERS), field)
		for fn in (ff.reportview_get_sidebar_stats, ff.reportview_get_stats):
			with self.subTest(fn=fn.__name__):
				_reset(STAFF)
				self.passes(fn, json.dumps(["_user_tags"]), "Project", json.dumps(PROJECT_FILTERS))
		_reset(STAFF)
		self.passes(
			ff.reportview_get_filter_dashboard_data,
			json.dumps([{"name": "status", "type": "Select"}, {"name": "owner", "type": "Link"}]),
			"Project",
			json.dumps(PROJECT_FILTERS),
		)

	def test_form_previous_and_next(self):
		"""form.js 1591-1616: the list view's filters and sort, or the saved list settings (whose
		filters keep a fifth, hidden flag)."""
		saved = json.dumps([["Project", "status", "=", "Open", False]])
		for filters in (json.dumps(PROJECT_FILTERS), saved, None):
			for sort_field in ("modified", "creation", "name", "idx", "docstatus", "owner", "status"):
				with self.subTest(filters=filters, sort_field=sort_field):
					_reset(STAFF)
					self.passes(ff.form_get_next, "Project", "PROJ-0001", 1, filters, "desc", sort_field)
		_reset(STAFF)
		self.passes(ff.form_get_next, "Project", "PROJ-0001", 0)

	def test_number_cards_and_charts(self):
		card = {
			"name": "Open Projects",
			"document_type": "Project",
			"function": "Count",
			"aggregate_function_based_on": None,
			"filters_json": json.dumps(PROJECT_FILTERS),
		}
		self.passes(ff.number_card_get_result, json.dumps(card), json.dumps(PROJECT_FILTERS))
		_reset(STAFF)
		summed = dict(card, function="Sum", aggregate_function_based_on="percent_complete")
		self.passes(ff.number_card_get_result, json.dumps(summed), json.dumps(PROJECT_FILTERS))
		_reset(STAFF)
		STATE["stored"][("Number Card", "Open Projects")] = summed
		self.passes(
			ff.number_card_get_percentage_difference, json.dumps(card), json.dumps(PROJECT_FILTERS), 4
		)
		group_by = {
			"name": "Projects by Status",
			"document_type": "Project",
			"chart_type": "Group By",
			"group_by_type": "Count",
			"group_by_based_on": "status",
			"filters_json": json.dumps([["Project", "status", "!=", "Canceled"]]),
		}
		timeseries = {
			"name": "New Projects",
			"document_type": "Project",
			"chart_type": "Count",
			"based_on": "creation",
			"filters_json": "[]",
		}
		for chart in (
			group_by,
			timeseries,
			dict(timeseries, chart_type="Sum", value_based_on="percent_complete"),
		):
			with self.subTest(chart=chart["name"], chart_type=chart["chart_type"], by="name"):
				_reset(STAFF)
				STATE["stored"][("Dashboard Chart", chart["name"])] = chart
				self.passes(
					ff.dashboard_chart_get, chart_name=chart["name"], filters=chart["filters_json"], refresh=1
				)
			with self.subTest(chart=chart["name"], chart_type=chart["chart_type"], by="passed"):
				_reset(STAFF)
				self.passes(ff.dashboard_chart_get, chart=json.dumps(chart), no_cache=1)

	def test_rest_list_routes(self):
		for path, form in (
			("/api/resource/Project", {"order_by": "modified desc"}),
			(
				"/api/resource/Project",
				{
					"fields": json.dumps(["name", "project_name", "status"]),
					"filters": json.dumps([["status", "=", "Open"]]),
					"order_by": "creation asc",
					"limit_page_length": 20,
				},
			),
			("/api/v1/resource/Item", {"filters": json.dumps({"company": COMPANY, "disabled": 0})}),
			(
				"/api/v2/document/Project",
				{"order_by": "creation desc", "filters": json.dumps({"_assign": "x"})},
			),
			("/api/v2/doctype/Project/count", {"filters": json.dumps(PROJECT_FILTERS)}),
		):
			with self.subTest(path=path, form=form):
				self.assertEqual(self.rest(path, form), 200)

	def test_client_reads(self):
		"""A plain filter name the doctype lacks is resolved the way frappe's query engine resolves it:
		the first child table, in field order, that has such a field (query.py 983-1002). On Item that
		is Item Default, and on Customer and Supplier Allowed To Transact With, all level 0. Each is
		asked by a user who reads the doctype at level 0 (STAFF reads no level of Customer)."""
		for doctype, who in (("Item", STAFF), ("Customer", SALES_USER), ("Supplier", STAFF)):
			with self.subTest(doctype=doctype):
				_reset(who)
				self.passes(ff.client_get_list, doctype, filters={"company": COMPANY})
				_reset(who)
				self.passes(ff.client_get_count, doctype, json.dumps([["company", "=", COMPANY]]))
				_reset(who)
				self.passes(
					ff.client_get_value, doctype, "name", json.dumps({"company": COMPANY, "disabled": 0})
				)
				_reset(who)
				self.passes(ff.client_get, doctype, filters=json.dumps({"company": COMPANY}))
		_reset(STAFF)
		self.passes(ff.client_get_value, "Item", json.dumps(["item_name", "item_group"]), "ITEM-0001")
		_reset(STAFF)
		self.passes(ff.client_get_list, "Item", ["name", {"item_defaults": ["company"]}], {"disabled": 0})

	def test_standard_columns_are_never_refused(self):
		"""Even beside a hidden child table with fields of those names (a stand-in)."""
		for name in STANDARD_NAMES:
			for doctype in ("Odd Parent", "Item", "Project"):
				with self.subTest(name=name, doctype=doctype):
					for kwargs in (
						{"filters": {name: "x"}},
						{"filters": [[name, "=", "x"]]},
						{"filters": [name, "=", "x"]},
						{"filters": [[doctype, name, "=", "x"]]},
						{"or_filters": {name: "x"}},
						{"order_by": f"{name} desc"},
						{"group_by": name},
						{"fields": [{"COUNT": name}]},
					):
						_reset(STAFF)
						self.passes(ff.client_get_list, doctype, **kwargs)
					_reset(STAFF)
					self.passes(ff.form_get_next, doctype, "X-1", 1, json.dumps({name: "x"}), "desc", name)
					_reset(STAFF)
					self.passes(ff.client_get_value, doctype, name, json.dumps({name: "x"}))

	def test_a_singles_filters_are_its_stored_values(self):
		"""A Single's filters are compared with its stored values, never looked up in a child table
		(database.py 760-765): a name only a hidden child table has is not refused."""
		_reset(HR_USER)
		self.passes(ff.client_get_value, "Pay Settings", "company", json.dumps({"hourly_rate": 5}))
		_reset(HR_USER)
		self.passes(ff.client_get_single_value, "Pay Settings", "hourly_rate")
		# Its own hidden fields are still refused, the table field included.
		for filters in (json.dumps({"rate_rows": 1}), json.dumps({"default_rate": 1})):
			with self.subTest(filters=filters):
				_reset(HR_USER)
				self.refuses(ff.client_get_value, "Pay Settings", "company", filters)

	def test_the_create_project_prompt_lists_desk_users(self):
		"""``frappe.client.get_list`` on User by ``enabled`` and ``user_type``, from a Desk user with
		``select`` only on User, over POST (JSON strings) and as a dict."""
		for filters, fields in (
			(json.dumps(DESK_USERS_FILTERS), json.dumps(["name"])),
			(dict(DESK_USERS_FILTERS), ["name"]),
		):
			with self.subTest(filters=filters):
				_reset(DESK_USER)
				self.passes(ff.client_get_list, "User", fields, filters, limit_page_length=0)


class TestUserTypeReference(_RouteCase):
	"""``User.user_type`` is the one reference ``REFERENCES_NOT_HELD_BACK`` lets through; every other
	level-1 field of User is still refused for the same caller."""

	OTHER_LEVEL_1 = (
		"api_key",
		"reset_password_key",
		"roles",
		"role_profile_name",
		"restrict_ip",
		"login_after",
	)

	def setUp(self):
		_reset(DESK_USER)

	def test_the_request_passes_on_every_route(self):
		self.passes(ff.client_get_list, "User", ["name"], DESK_USERS_FILTERS, limit_page_length=0)
		_reset(DESK_USER)
		self.passes(ff.client_get_count, "User", json.dumps(DESK_USERS_FILTERS))
		_reset(DESK_USER, form={"doctype": "User", "filters": json.dumps(USERS_CARD_FILTERS)})
		self.passes(ff.reportview_get_count)
		_reset(DESK_USER)
		card = {"name": "Total System Users", "document_type": "User", "function": "Count"}
		self.passes(ff.number_card_get_result, json.dumps(card), json.dumps(USERS_CARD_FILTERS))
		self.assertEqual(
			self.rest(
				"/api/resource/User",
				{"filters": json.dumps(DESK_USERS_FILTERS), "fields": json.dumps(["name"])},
				who=DESK_USER,
			),
			200,
		)

	def test_in_every_position(self):
		for kwargs in (
			{"filters": [["user_type", "=", "System User"]]},
			{"filters": [["User", "user_type", "=", "System User"]]},
			{"filters": {"`tabUser`.`user_type`": "System User"}},
			{"or_filters": {"user_type": "Website User"}},
			{"order_by": "user_type asc"},
			{"group_by": "user_type"},
			{"fields": [{"COUNT": "user_type", "as": "n"}]},
		):
			with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
				_reset(DESK_USER)
				self.passes(ff.client_get_list, "User", **kwargs)
		_reset(DESK_USER)
		self.passes(ff.listview_get_group_by_count, "User", "[]", "user_type")
		_reset(DESK_USER)
		self.passes(ff.form_get_next, "User", "a@example.com", 1, None, "desc", "user_type")

	def test_the_other_level_1_fields_are_still_refused(self):
		for fieldname in self.OTHER_LEVEL_1:
			for kwargs in (
				{"filters": {**DESK_USERS_FILTERS, fieldname: "x"}},
				{"filters": DESK_USERS_FILTERS, "order_by": f"{fieldname} desc"},
				{"fields": [{"COUNT": fieldname}], "filters": DESK_USERS_FILTERS},
			):
				with self.subTest(fieldname=fieldname, **{k: str(v) for k, v in kwargs.items()}):
					_reset(DESK_USER)
					self.refuses(ff.client_get_list, "User", **kwargs)
			with self.subTest(fieldname=fieldname, route="other routes"):
				_reset(DESK_USER)
				self.refuses(ff.client_get_count, "User", json.dumps({fieldname: "x"}))
				_reset(DESK_USER)
				self.refuses(ff.form_get_next, "User", "a@example.com", 1, None, "desc", fieldname)
				self.assertEqual(
					self.rest("/api/resource/User", {"order_by": f"{fieldname} desc"}, who=DESK_USER), 403
				)

	def test_a_system_manager_is_untouched(self):
		for fieldname in ("user_type", *self.OTHER_LEVEL_1):
			with self.subTest(fieldname=fieldname):
				_reset(SYSTEM_MANAGER)
				self.passes(
					ff.client_get_list,
					"User",
					filters={fieldname: "x"},
					order_by=f"{fieldname} desc",
					fields=[{"COUNT": fieldname}],
				)


#: Reads no level of Employee.
NO_EMPLOYEE_READ = ("pm@example.com", ("Projects Manager", "All"))
PAY_RATE_SINCE = "2020-01-01"


class TestClosedChildTables(_RouteCase):
	"""A child table whose table field the caller cannot read (Employee's level-1 ``custom_pay_rates``),
	or any child table of a doctype the caller reads no level of, is refused in every reference,
	whatever the column, the standard columns included."""

	BELOW = (HR_USER, NO_EMPLOYEE_READ)

	def check(self, call, *args, form=None, **kwargs):
		"""Refused below the level and with no read on the parent; answered at the level."""
		for who in self.BELOW:
			with self.subTest(who=who[0]):
				_reset(who, form=form)
				self.refuses(call, *args, **kwargs)
		_reset(HR_MANAGER, form=form)
		self.passes(call, *args, **kwargs)

	def test_the_four_shapes(self):
		for label, filters in (
			("report view, table_field.name is set", [["custom_pay_rates.name", "is", "set"]]),
			(
				"report view, table_field.creation after a date",
				[["custom_pay_rates.creation", ">", PAY_RATE_SINCE]],
			),
		):
			with self.subTest(label):
				self.check(ff.reportview_get, form={"doctype": "Employee", "filters": json.dumps(filters)})
		with self.subTest("get_list on the parent, [child doctype, creation, >, date]"):
			self.check(
				ff.client_get_list,
				"Employee",
				filters=[["Employee Pay Rate", "creation", ">", PAY_RATE_SINCE]],
			)
		with self.subTest("get_list on the child through the parent, standard columns only"):
			self.check(
				ff.client_get_list,
				"Employee Pay Rate",
				["name", "parent", "creation", "modified"],
				parent="Employee",
			)

	def test_every_position(self):
		for kwargs in (
			{"filters": {"custom_pay_rates.name": ["is", "set"]}},
			{"filters": [["Employee", "custom_pay_rates.owner", "=", "x"]]},
			{"filters": [["`tabEmployee Pay Rate`.`modified`", ">", PAY_RATE_SINCE]]},
			{"filters": [["Employee Pay Rate", "parent", "is", "set"]]},
			{"or_filters": [["Employee Pay Rate", "name", "is", "set"]]},
			{"order_by": "custom_pay_rates.modified desc"},
			{"order_by": "`tabEmployee Pay Rate`.`creation` desc"},
			{"group_by": "`tabEmployee Pay Rate`.`parent`"},
			{"fields": [{"COUNT": "`tabEmployee Pay Rate`.`name`", "as": "n"}]},
			{"fields": [{"MAX": "custom_pay_rates.creation"}]},
			{"fields": ["name", "custom_pay_rates.name"]},
			{"fields": ["name", "`tabEmployee Pay Rate`.`creation` as added"]},
			{"fields": ["custom_pay_rates.*"]},
			{"fields": json.dumps(["name", "`tabEmployee Pay Rate`.`modified`"])},
			{"fields": "name, `tabEmployee Pay Rate`.`modified`"},
			{"fields": [{"custom_pay_rates": ["name", "creation"]}]},
		):
			with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
				self.check(ff.client_get_list, "Employee", **kwargs)

	def test_the_child_through_its_parent_on_every_route(self):
		self.check(ff.client_get_list, "Employee Pay Rate", parent="Employee")
		self.check(ff.client_get, "Employee Pay Rate", "row-1", parent="Employee")
		self.check(
			ff.client_get_value, "Employee Pay Rate", "name", json.dumps({"name": "row-1"}), parent="Employee"
		)
		for call in (
			ff.reportview_get,
			ff.reportview_get_list,
			ff.reportview_get_count,
			ff.reportview_export_query,
		):
			with self.subTest(call=call.__name__):
				self.check(call, form={"doctype": "Employee Pay Rate", "parent_doctype": "Employee"})
		card = {"document_type": "Employee Pay Rate", "parent_document_type": "Employee", "function": "Count"}
		self.check(ff.number_card_get_result, json.dumps(card), "[]")
		chart = {
			"name": "Pay rows",
			"document_type": "Employee Pay Rate",
			"parent_document_type": "Employee",
			"chart_type": "Count",
			"based_on": "creation",
		}
		self.check(ff.dashboard_chart_get, chart=json.dumps(chart), no_cache=1)
		for who, status in ((HR_USER, 403), (NO_EMPLOYEE_READ, 403), (HR_MANAGER, 200)):
			with self.subTest(route="REST", who=who[0]):
				self.assertEqual(
					self.rest(
						"/api/resource/Employee Pay Rate",
						{"parent": "Employee", "fields": json.dumps(["name", "parent", "creation"])},
						who=who,
					),
					status,
				)

	def test_a_readable_table_keeps_its_standard_columns(self):
		"""Timesheet's ``time_logs`` is level 0 with a level-1 column: its standard columns stay open,
		its hidden column is refused, as before."""
		for kwargs in (
			{"filters": [["Timesheet Detail", "creation", ">", PAY_RATE_SINCE]]},
			{"filters": [["time_logs.name", "is", "set"]]},
			{"order_by": "`tabTimesheet Detail`.`modified` desc"},
			{"fields": ["name", "time_logs.name", "`tabTimesheet Detail`.`hours`"]},
			{"fields": [{"time_logs": ["name", "hours"]}]},
		):
			with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
				_reset(PROJECTS_USER)
				self.passes(ff.client_get_list, "Timesheet", **kwargs)
		_reset(PROJECTS_USER)
		self.passes(
			ff.client_get_list,
			"Timesheet Detail",
			["name", "parent", "creation", "hours"],
			parent="Timesheet",
		)
		_reset(PROJECTS_USER)
		self.refuses(
			ff.client_get_list, "Timesheet", filters=[["Timesheet Detail", "base_costing_rate", ">", 1]]
		)
		_reset(PROJECTS_USER)
		self.refuses(
			ff.client_get_list,
			"Timesheet Detail",
			filters={"base_costing_rate": [">", 1]},
			parent="Timesheet",
		)

	def test_allowed_companies_is_closed_and_ordinary_item_requests_are_not(self):
		for kwargs in (
			{"filters": [["Company Restriction", "name", "is", "set"]]},
			{"filters": {"allowed_companies.creation": [">", PAY_RATE_SINCE]}},
			{"fields": ["name", "`tabCompany Restriction`.`parent`"]},
		):
			with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
				_reset(STAFF)
				self.refuses(ff.client_get_list, "Item", **kwargs)
		_reset(STAFF)
		self.refuses(ff.client_get_list, "Company Restriction", ["name", "parent"], parent="Item")
		_reset(STAFF)
		self.passes(ff.client_get_list, "Item Default", ["name", "parent", "company"], parent="Item")
		# A bare `company` still follows frappe's first-match order: Item Default here, open.
		_reset(STAFF)
		self.passes(ff.client_get_list, "Item", filters={"company": COMPANY})

	def test_no_read_on_the_parent_closes_every_child_table(self):
		"""Customer, for STAFF, who reads no level of it: even its level-0 tables are closed, as frappe
		refuses a child-table field to a caller with ``select`` only (database/query.py 1035-1043)."""
		for kwargs in (
			{"filters": {"company": COMPANY}},
			{"filters": [["Allowed To Transact With", "company", "=", COMPANY]]},
			{"filters": {"companies.name": ["is", "set"]}},
		):
			with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
				_reset(STAFF)
				self.refuses(ff.client_get_list, "Customer", **kwargs)
				_reset(SALES_USER)
				self.passes(ff.client_get_list, "Customer", **kwargs)
		_reset(STAFF)
		self.refuses(ff.client_get_list, "Party Account", ["name"], parent="Customer")
		_reset(SALES_USER)
		self.passes(ff.client_get_list, "Party Account", ["name"], parent="Customer")
		# Its own level-0 fields are not closed by this.
		_reset(STAFF)
		self.passes(ff.client_get_list, "Customer", filters={"customer_group": "Retail", "disabled": 0})
		# The same on a doctype with nothing above level 0: HR_USER reads no level of Site Visit.
		for kwargs in (
			{"filters": [["Visit Check", "name", "is", "set"]]},
			{"fields": [{"checks": ["item"]}]},
		):
			with self.subTest(doctype="Site Visit", **{k: str(v) for k, v in kwargs.items()}):
				_reset(HR_USER)
				self.refuses(ff.client_get_list, "Site Visit", **kwargs)
				_reset(PROJECTS_USER)
				self.passes(ff.client_get_list, "Site Visit", **kwargs)
		_reset(HR_USER)
		self.passes(ff.client_get_list, "Site Visit", filters={"visit_date": [">", PAY_RATE_SINCE]})


class TestStillRefused(_RouteCase):
	"""The same routes, from the same caller, naming a field hidden from it."""

	def test_search_without_a_custom_query(self):
		for filters in (
			{"restrict_to_companies": 1},
			[["Item", "restrict_to_companies", "=", 1]],
			[["Company Restriction", "company", "=", COMPANY]],
			[["`tabCompany Restriction`.`company`", "=", COMPANY]],
			{"allowed_companies.company": COMPANY},
		):
			with self.subTest(filters=filters):
				_reset(STAFF)
				self.refuses(ff.search_link, "Item", "a", filters=json.dumps(filters))
				_reset(STAFF)
				self.refuses(ff.search_widget, "Item", "a", filters=filters)
		# A standard query registered for another doctype changes nothing here.
		_reset(STAFF)
		STATE["hooks"]["standard_queries"] = {"Customer": ["custom_app.queries.customer_search"]}
		self.refuses(ff.search_link, "Item", "a", filters=json.dumps({"restrict_to_companies": 1}))

	def test_a_hidden_search_field_with_or_without_a_custom_query(self):
		for query in (ITEM_QUERY, None):
			with self.subTest(query=query):
				_reset(STAFF)
				self.refuses(
					ff.search_link,
					"Item",
					"a",
					query=query,
					filters=SALES_ITEM_FILTERS,
					searchfield="restrict_to_companies",
				)
				_reset(STAFF)
				self.refuses(ff.search_widget, "Item", "a", query=query, searchfield="restrict_to_companies")

	def test_engine_routes_by_child_table_notation(self):
		for kwargs in (
			{"filters": [["Company Restriction", "company", "=", COMPANY]]},
			{"filters": [["`tabCompany Restriction`.`company`", "=", COMPANY]]},
			{"filters": {"allowed_companies.company": COMPANY}},
			{"or_filters": {"allowed_companies.company": COMPANY}},
			{"order_by": "allowed_companies.company desc"},
			{"group_by": "`tabCompany Restriction`.`company`"},
			{"fields": [{"allowed_companies": ["company"]}]},
			{"fields": [{"COUNT": "`tabCompany Restriction`.`company`"}]},
			{"filters": {"restrict_to_companies": 0}},
		):
			with self.subTest(**{k: str(v) for k, v in kwargs.items()}):
				_reset(STAFF)
				self.refuses(ff.client_get_list, "Item", **kwargs)
		_reset(
			STAFF,
			form={
				"doctype": "Item",
				"filters": json.dumps([["Company Restriction", "company", "=", COMPANY]]),
			},
		)
		self.refuses(ff.reportview_get)

	def test_a_plain_name_frappe_resolves_to_a_hidden_child_field(self):
		"""On Rate Card the first child table with a ``company`` field is the hidden one."""
		for filters in ({"company": COMPANY}, [["company", "=", COMPANY]], ["company", "=", COMPANY]):
			with self.subTest(filters=filters):
				_reset(STAFF)
				self.refuses(ff.client_get_list, "Rate Card", filters=filters)
				_reset(STAFF)
				self.refuses(ff.client_get_count, "Rate Card", json.dumps(filters))
				_reset(STAFF)
				self.refuses(ff.client_get, "Rate Card", filters=json.dumps(filters))
				_reset(STAFF)
				self.refuses(ff.form_get_next, "Rate Card", "RC-1", 1, json.dumps(filters))
		_reset(STAFF)
		self.refuses(ff.search_link, "Rate Card", "a", filters=json.dumps([["company", "=", COMPANY]]))
		self.assertEqual(
			self.rest("/api/v2/document/Rate Card", {"filters": json.dumps({"company": COMPANY})}), 403
		)
		# A filter that names its own doctype, and a sort, are resolved on Rate Card itself.
		_reset(STAFF)
		self.passes(ff.client_get_list, "Rate Card", filters=[["Rate Card", "company", "=", COMPANY]])
		_reset(STAFF)
		self.passes(ff.client_get_list, "Rate Card", order_by="company desc")

	def test_other_routes(self):
		self.refuses(
			ff.form_get_next,
			"Project",
			"PROJ-0001",
			1,
			json.dumps(PROJECT_FILTERS),
			"desc",
			"estimated_costing",
		)
		_reset(STAFF)
		self.refuses(
			ff.form_get_next,
			"Project",
			"PROJ-0001",
			1,
			json.dumps([["Project", "estimated_costing", ">", 1]]),
		)
		_reset(STAFF)
		self.refuses(ff.listview_get_group_by_count, "Project", "[]", "estimated_costing")
		_reset(STAFF)
		self.refuses(
			ff.number_card_get_result,
			json.dumps(
				{
					"document_type": "Project",
					"function": "Sum",
					"aggregate_function_based_on": "estimated_costing",
				}
			),
			"[]",
		)
		_reset(STAFF)
		self.refuses(
			ff.dashboard_chart_get,
			chart=json.dumps(
				{
					"name": "Cost",
					"document_type": "Project",
					"chart_type": "Sum",
					"based_on": "creation",
					"value_based_on": "estimated_costing",
				}
			),
			no_cache=1,
		)
		_reset(
			STAFF,
			form={
				"doctype": "Project",
				"aggregate_on_field": "estimated_costing",
				"aggregate_function": "sum",
				"group_by": "`tabProject`.`status`",
			},
		)
		self.refuses(ff.reportview_get)
		for path, form in (
			("/api/resource/Project", {"order_by": "estimated_costing desc"}),
			(
				"/api/resource/Item",
				{"filters": json.dumps([["Company Restriction", "company", "=", COMPANY]])},
			),
			("/api/v2/doctype/Item/count", {"filters": json.dumps({"restrict_to_companies": 1})}),
		):
			with self.subTest(path=path, form=form):
				self.assertEqual(self.rest(path, form), 403)


# ---------------------------------------------------------------------------
# Names in any case, list shapes, fields and filters either way round, other doctypes
# ---------------------------------------------------------------------------


class TestNamesInAnyCase(_RouteCase):
	"""MariaDB matches a column name whatever its case, so another spelling of a field the caller
	cannot read is that field, in every position and on every route."""

	def setUp(self):
		_reset(HR_USER)

	def refused(self, doctype="Employee", parent=None, **kwargs):
		return _refused(ff.check_request, doctype, parent, child_names=True, **kwargs)

	def test_another_spelling_of_a_hidden_field_is_refused_in_every_position(self):
		cases = {
			"filters dict": {"filters": {"CTC": [">", 1]}},
			"filters dict, mixed case": {"filters": {"Ctc": 1}},
			"filters list": {"filters": [["cTc", ">", 1]]},
			"filters [doctype, field, op, value]": {"filters": [["Employee", "CTC", ">", 1]]},
			"filters, the doctype in another case": {"filters": [["employee", "CTC", ">", 1]]},
			"filters single condition": {"filters": ["CTC", ">", 1]},
			"filters as JSON": {"filters": json.dumps({"CTC": [">", 1]})},
			"or_filters": {"or_filters": [["Ctc", ">", 1]]},
			"backtick": {"filters": {"`tabEmployee`.`CTC`": 1}},
			"backtick, the table in another case": {"order_by": "`tabemployee`.`Ctc` desc"},
			"quoted": {"filters": {"`CTC`": 1}},
			"order_by": {"order_by": "CTC desc"},
			"group_by": {"group_by": "Ctc"},
			"aggregate": {"fields": [{"SUM": "CTC"}]},
			"a reference": {"references": ["CTC"]},
			"an expression": {"expressions": ["Ctc"]},
			"a hidden Link": {"filters": {"PAY_GRADE": "G1"}},
			"through a hidden Link": {"order_by": "Pay_Grade.grade_name"},
			"a closed table, dotted": {"filters": {"CUSTOM_PAY_RATES.name": "x"}},
			"a closed table, backtick": {"order_by": "`tabEMPLOYEE PAY RATE`.`idx`"},
			"a closed table by doctype": {"filters": [["employee pay rate", "name", "=", "x"]]},
			"a closed table in fields": {"fields": ["Custom_Pay_Rates.name"]},
			"a closed table in a child query": {"fields": [{"CUSTOM_PAY_RATES": ["name"]}]},
			"letters outside ASCII that read as the name": {"filters": {"ｃｔｃ": 1}},
		}
		for label, kwargs in cases.items():
			with self.subTest(label):
				self.assertTrue(self.refused(**kwargs))
				self.assertEqual(STATE["thrown"][-1], ff.REFUSAL)
		_reset(HR_MANAGER)
		for label, kwargs in cases.items():
			with self.subTest(label, who="hr manager"):
				self.assertFalse(self.refused(**kwargs))

	def test_a_child_tables_hidden_column_in_another_spelling(self):
		_reset(PROJECTS_USER)
		refused = {
			"dotted": {"filters": {"time_logs.BASE_COSTING_RATE": [">", 1]}},
			"dotted, the table field too": {"order_by": "TIME_LOGS.Base_Costing_Rate"},
			"backtick": {"filters": [["`tabTimesheet Detail`.`BASE_COSTING_RATE`", ">", 50]]},
			"[child doctype, field, op, value]": {
				"filters": [["Timesheet Detail", "Base_Costing_Rate", ">", 1]]
			},
			"a child's name used bare": {"filters": {"BASE_COSTING_RATE": 1}},
			"a child query": {"fields": [{"time_logs": ["hours", "BASE_COSTING_RATE"]}]},
		}
		for label, kwargs in refused.items():
			with self.subTest(label):
				self.assertTrue(self.refused("Timesheet", **kwargs))
		passed = {
			"a visible column": {"filters": {"time_logs.HOURS": [">", 1]}},
			"a standard column": {"order_by": "time_logs.IDX"},
			"a visible column by doctype": {"filters": [["timesheet detail", "Hours", ">", 1]]},
		}
		for label, kwargs in passed.items():
			with self.subTest(label):
				self.assertFalse(self.refused("Timesheet", **kwargs))

	def test_another_spelling_of_a_visible_field_passes(self):
		passed = {
			"a visible field": {"filters": {"EMPLOYEE_NAME": "A"}, "order_by": "Employee_Name asc"},
			"a standard column": {"filters": {"NAME": "EMP-1"}, "order_by": "MODIFIED desc"},
			"through a visible Link": {"filters": {"Department.department_name": "Ops"}},
			"a name the doctype lacks": {"order_by": "NOT_A_FIELD"},
			"a function name": {"fields": [{"count": "*", "as": "n"}]},
		}
		for label, kwargs in passed.items():
			with self.subTest(label):
				self.assertFalse(self.refused(**kwargs))

	def test_the_named_exception_holds_in_any_case(self):
		_reset(DESK_USER)
		self.assertFalse(self.refused("User", filters={"USER_TYPE": "System User"}))
		self.assertFalse(self.refused("User", order_by="User_Type asc"))
		self.assertTrue(self.refused("User", filters={"API_KEY": ["like", "a%"]}))

	def test_the_routes_that_use_a_name_as_written(self):
		"""The sort field of next-record, the search field of a custom query, a document looked up by
		filters and a Single's fields are not looked up by frappe before the database reads them."""
		self.refuses(ff.client_get, "Employee", filters=json.dumps({"CTC": [">", 1]}))
		self.refuses(ff.form_get_next, "Employee", "EMP-1", 1, sort_field="Ctc")
		self.refuses(
			ff.search_widget,
			"Employee",
			"a",
			query="erpnext.controllers.queries.employee_query",
			searchfield="CTC",
		)
		self.refuses(ff.search_link, "Employee", "a", searchfield="Ctc")
		self.refuses(ff.listview_get_group_by_count, "Employee", "{}", "CTC")
		self.refuses(ff.client_get_value, "Pay Settings", "DEFAULT_RATE")
		self.refuses(ff.client_get_value, "Pay Settings", json.dumps(["company", "Default_Rate"]))
		self.refuses(ff.client_get_single_value, "Pay Settings", "Default_Rate")

	def test_a_singles_answer_is_scrubbed_whatever_the_spelling(self):
		STATE["returns"]["client.get_value"] = {"company": "X", "default_rate": 5, "Default_Rate": 6}
		self.assertEqual(ff.client_get_value("Pay Settings", "company", {"company": "X"}), {"company": "X"})

	def test_folding(self):
		self.assertEqual(ff._fold("CTC"), "ctc")
		self.assertEqual(ff._fold("ranK"), "rank")  # KELVIN SIGN
		self.assertEqual(ff._fold("İd"), "id")  # LATIN CAPITAL LETTER I WITH DOT ABOVE
		self.assertEqual(ff._fold("café"), "cafe")


class TestListShapes(_RouteCase):
	"""A list of filters is read as given and as ``frappe.db.get_values`` leaves it, without its None
	entries (database.py 662): that is how a document looked up by filters is found."""

	REFUSED = (
		[None, ["ctc", ">", 1]],
		[None, None, ["ctc", ">", 1]],
		[None, {"ctc": 1}],
		[None, "ctc", ">", 1],
		["ctc", None, ">", 1],
		[None, None, "ctc", ">", 1],
		[None, ["employee_name", "=", "A"], "or", ["ctc", ">", 1]],
		[["employee_name", "=", "A"], None, ["ctc", ">", 1]],
		[None, ["CTC", ">", 1]],
	)
	PASSED = (
		[None, ["employee_name", "=", "A"]],
		[["employee_name", "=", "A"], None],
		["employee_name", "=", None],
		["employee_name", "in", ["A", "B"]],
		["employee_name", "between", ["A", "B"]],
		[None, "EMP-1"],
		[None],
	)

	def setUp(self):
		_reset(HR_USER)

	def test_on_the_rule(self):
		for filters in self.REFUSED:
			for sent in (filters, json.dumps(filters)):
				with self.subTest(filters=sent):
					self.assertTrue(_refused(ff.check_request, "Employee", child_names=True, filters=sent))
					self.assertTrue(
						_refused(ff.check_request, "Employee", child_names=True, or_filters=sent)
					)
		for filters in self.PASSED:
			for sent in (filters, json.dumps(filters)):
				with self.subTest(filters=sent):
					self.assertFalse(_refused(ff.check_request, "Employee", child_names=True, filters=sent))

	def test_a_document_looked_up_by_filters(self):
		for filters in self.REFUSED:
			with self.subTest(filters=filters):
				_reset(HR_USER)
				self.refuses(ff.client_get, "Employee", filters=json.dumps(filters))
		for filters in self.PASSED:
			with self.subTest(filters=filters):
				_reset(HR_USER)
				self.passes(ff.client_get, "Employee", filters=json.dumps(filters))

	def test_a_caller_who_reads_everything_is_untouched(self):
		for filters in self.REFUSED:
			with self.subTest(filters=filters):
				_reset(HR_MANAGER)
				self.passes(ff.client_get, "Employee", filters=json.dumps(filters))


class TestFieldsAndFiltersEitherWay(_RouteCase):
	"""``frappe.get_list`` takes ``fields`` and ``filters`` in either order (qb_query.py 107-115), so
	each is read as the other wherever the route reaches it."""

	def setUp(self):
		_reset(PROJECTS_USER)

	def refused(self, doctype="Timesheet", **kwargs):
		return _refused(ff.check_request, doctype, None, child_names=True, **kwargs)

	def test_filters_handed_over_as_fields_are_read_as_filters(self):
		cases = {
			"a list of conditions": {"fields": [["Timesheet Detail", "base_costing_rate", ">", 1]]},
			"a list of conditions, as JSON": {
				"fields": json.dumps([["time_logs.base_costing_rate", ">", 1]])
			},
			"a dict": {"fields": {"time_logs.base_costing_rate": [">", 1]}},
			"a dict, as JSON": {"fields": json.dumps({"`tabTimesheet Detail`.`base_costing_rate`": 1})},
			"the doctype's own field": {"fields": {"total_costing_amount": [">", 1]}},
			"beside a list of names in filters": {
				"fields": ["time_logs.base_costing_rate", ">", "1"],
				"filters": ["name", "note"],
			},
		}
		for label, kwargs in cases.items():
			with self.subTest(label):
				self.assertTrue(self.refused(**kwargs))
		_reset(HR_USER)
		self.assertTrue(self.refused("Employee", fields={"`tabEmployee Pay Rate`.`name`": ["like", "%"]}))
		self.assertTrue(self.refused("Employee", fields=[["Employee Pay Rate", "idx", ">", 0]]))

	def test_on_the_routes(self):
		conditions = json.dumps([["Timesheet Detail", "base_costing_rate", ">", 1]])
		self.refuses(ff.client_get_list, "Timesheet", fields=conditions)
		self.refuses(ff.client_get_value, "Timesheet", conditions)
		self.assertEqual(self.rest("/api/resource/Timesheet", {"fields": conditions}, who=PROJECTS_USER), 403)
		_reset(PROJECTS_USER, form={"doctype": "Timesheet", "fields": conditions})
		self.refuses(ff.reportview_get)

	def test_ordinary_fields_and_filters_still_pass(self):
		cases = {
			"plain fields and a list of conditions": {
				"fields": ["name", "note"],
				"filters": [["note", "like", "%a%"]],
			},
			"an aggregate": {"fields": [{"COUNT": "*", "as": "n"}], "filters": {"note": "x"}},
			"plain fields, one of them hidden, and a dict": {
				"fields": ["total_costing_amount", "name", "note"],
				"filters": {"note": "x"},
			},
			"a child query": {"fields": ["name", {"time_logs": ["hours"]}]},
			"fields as one string": {"fields": "name, note", "filters": [["note", "=", "x"]]},
			"a star": {"fields": ["*"], "filters": [["Timesheet", "note", "=", "x"]]},
			"a list of names in filters and no fields": {"filters": ["TS-1", "TS-2", "TS-3"]},
		}
		for label, kwargs in cases.items():
			with self.subTest(label):
				self.assertFalse(self.refused(**kwargs))

	def test_the_route_that_goes_to_the_query_engine_reads_them_as_given(self):
		"""``/api/v2/document`` hands both to the query engine, which takes them one way only."""
		kwargs = {"fields": ["total_costing_amount", "name", "note"], "filters": ["note", "like", "%a%"]}
		self.assertTrue(self.refused(**kwargs))
		self.assertFalse(self.refused(swap=False, **kwargs))
		form = {key: json.dumps(value) for key, value in kwargs.items()}
		self.assertEqual(self.rest("/api/v2/document/Timesheet", form, who=PROJECTS_USER), 200)
		self.assertEqual(self.rest("/api/resource/Timesheet", form, who=PROJECTS_USER), 403)


class TestAnotherDoctypesFields(_RouteCase):
	"""``frappe.client.get`` finds its document with no permission applied to the filters, so a field
	they name in another doctype, through a Link field or by table, is held to that doctype's read
	levels here. ToDo hides nothing from anyone; Employee's ``ctc`` is level 1; User gives a Desk user
	``select`` only."""

	def get(self, filters, who=HR_USER):
		_reset(who)
		refused = _refused(ff.client_get, "ToDo", filters=json.dumps(filters))
		self.assertEqual(len(STATE["calls"]), 0 if refused else 1)
		return refused

	def test_a_field_the_caller_cannot_read_in_another_doctype_is_refused(self):
		cases = {
			"a dict": {"employee.ctc": [">", 1]},
			"another spelling of the field": {"employee.CTC": [">", 1]},
			"another spelling of the Link field": {"Employee.ctc": 1},
			"a condition": [["employee.ctc", ">", 1]],
			"a single condition": ["employee.ctc", ">", 1],
			"behind a None": [None, ["employee.ctc", ">", 1]],
			"with an alias": {"employee.ctc as x": 1},
			"in a nested group": [["description", "=", "a"], "or", ["employee.ctc", ">", 1]],
			"after a shape frappe refuses": [["x"], ["employee.ctc", ">", 1]],
			"the table by name": {"`tabEmployee`.`ctc`": 1},
			"the table by name, unquoted field": {"`tabEmployee`.ctc": 1},
			"the doctype in a condition": [["Employee", "ctc", ">", 1]],
			"a table that is no doctype": {"`tab_Employee_1`.`ctc`": 1},
			"a doctype that is none, in a condition": [["_Employee_1", "employee_name", "=", "A"]],
			"a child table of another doctype": {"`tabEmployee Pay Rate`.`hourly_rate`": 1},
		}
		for label, filters in cases.items():
			with self.subTest(label):
				self.assertTrue(self.get(filters))
				self.assertEqual(STATE["thrown"][-1], ff.REFUSAL)

	def test_select_only_and_no_access_follow_frappes_own_rule(self):
		"""frappe's query engine lets a caller with ``select`` only name the level-0 fields of a
		doctype, and a caller with no access its standard columns (query.py 1074-1118)."""
		# A Desk user has ``select`` only on User: level 0, nothing above it.
		self.assertFalse(self.get({"allocated_to.full_name": ["like", "a%"]}, who=DESK_USER))
		self.assertFalse(self.get({"allocated_to.creation": [">", "2026-01-01"]}, who=DESK_USER))
		self.assertTrue(self.get({"allocated_to.api_key": ["like", "a%"]}, who=DESK_USER))
		self.assertTrue(self.get({"allocated_to.reset_password_key": ["is", "set"]}, who=DESK_USER))
		# The one reference a doctype does not hold back.
		self.assertFalse(self.get({"allocated_to.user_type": "System User"}, who=DESK_USER))
		self.assertFalse(self.get({"allocated_to.USER_TYPE": "System User"}, who=DESK_USER))
		# PROJECTS_USER holds no role with any permission on Employee: its standard columns only.
		self.assertTrue(self.get({"employee.employee_name": "A"}, who=PROJECTS_USER))
		self.assertTrue(self.get({"employee.EMPLOYEE_NAME": "A"}, who=PROJECTS_USER))
		self.assertTrue(self.get({"employee.ctc": [">", 1]}, who=PROJECTS_USER))
		self.assertFalse(self.get({"employee.creation": [">", "2026-01-01"]}, who=PROJECTS_USER))

	def test_what_the_caller_reads_passes(self):
		cases = {
			"a field the caller reads": ({"employee.employee_name": "A"}, HR_USER),
			"a standard column": ({"employee.creation": [">", "2026-01-01"]}, HR_USER),
			"at the level": ({"employee.ctc": [">", 1]}, HR_MANAGER),
			"a System Manager": ({"allocated_to.api_key": ["like", "a%"]}, SYSTEM_MANAGER),
			"a doctype everyone reads": ({"department.department_name": "Ops"}, DESK_USER),
			"the doctype's own fields": ({"description": ["like", "%a%"], "employee": "EMP-1"}, DESK_USER),
			"a document name": ("TODO-0001", HR_USER),
			"a list of names": (["T-1", "T-2", "T-3"], HR_USER),
			"a shape frappe refuses": ([["a", "b", "c", "d", "e", "f"]], HR_USER),
			"a field map of no kind": ({"nothing.here": 1}, HR_USER),
		}
		for label, (filters, who) in cases.items():
			with self.subTest(label):
				self.assertFalse(self.get(filters, who=who))

	def test_a_doctype_with_hidden_fields_of_its_own(self):
		_reset(HR_USER)
		self.refuses(ff.client_get, "Employee", filters=json.dumps({"`tabUser`.`api_key`": ["like", "a%"]}))
		self.refuses(ff.client_get, "Employee", filters=json.dumps({"department.nothing": 1, "ctc": 1}))
		self.passes(ff.client_get, "Employee", filters=json.dumps({"department.department_name": "Ops"}))

	def test_by_name_and_for_administrator_nothing_is_read(self):
		_reset(HR_USER)
		self.passes(ff.client_get, "ToDo", "TODO-0001")
		self.assertEqual(STATE["meta_calls"], [])
		_reset(("Administrator", ()))
		self.passes(ff.client_get, "ToDo", filters=json.dumps({"employee.ctc": 1}))
		self.assertEqual(STATE["meta_calls"], [])

	def test_where_frappe_applies_permissions_the_other_doctype_is_its_check(self):
		"""Every other route reaches frappe's query engine with permissions on, which holds a linked
		doctype's field to that doctype's own (query.py 888-898). Nothing is read here for a doctype
		that hides nothing."""
		_reset(HR_USER)
		self.passes(ff.client_get_list, "ToDo", filters={"employee.ctc": [">", 1]})
		self.passes(ff.client_get_count, "ToDo", filters=json.dumps({"employee.ctc": 1}))
		self.passes(ff.form_get_next, "ToDo", "T-1", 0, json.dumps({"employee.ctc": 1}))
		self.assertFalse(_refused(ff.check_request, "ToDo", child_names=True, filters={"employee.ctc": 1}))


class TestClosedChildTableSpellings(_RouteCase):
	"""frappe reads ``tabChild.col`` in ``fields`` with a backtick, a double quote or nothing around
	either part (query.py 183). Holiday List's ``holidays`` table is level 1; HR User reads level 0."""

	SPELLINGS = (
		"tabHoliday.description",
		"`tabHoliday`.`description`",
		"`tabHoliday`.description",
		'"tabHoliday".description',
		'tabHoliday."description"',
		"tabHoliday.description as note",
		"tabHoliday.name",
		"tabholiday.description",
		"holidays.description",
		"HOLIDAYS.description",
	)

	def setUp(self):
		_reset(HR_USER)

	def test_every_spelling_is_refused_in_fields(self):
		for entry in self.SPELLINGS:
			with self.subTest(entry=entry):
				_reset(HR_USER)
				self.assertTrue(_refused(ff.check_request, "Holiday List", child_names=True, fields=[entry]))
				self.refuses(ff.client_get_list, "Holiday List", fields=json.dumps(["name", entry]))
				_reset(HR_MANAGER)
				self.passes(ff.client_get_list, "Holiday List", fields=json.dumps(["name", entry]))

	def test_the_other_closed_table_with_a_space_in_its_name(self):
		for entry in ("tabEmployee Pay Rate.hourly_rate", '"tabEmployee Pay Rate".idx'):
			with self.subTest(entry=entry):
				self.assertTrue(_refused(ff.check_request, "Employee", child_names=True, fields=[entry]))

	def test_the_same_spellings_in_the_other_positions(self):
		for label, kwargs in {
			"order_by": {"order_by": "tabHoliday.holiday_date desc"},
			"group_by": {"group_by": '"tabHoliday".description'},
			"an aggregate": {"fields": [{"MAX": "tabHoliday.holiday_date"}]},
			"filters": {"filters": {"tabHoliday.description": "x"}},
		}.items():
			with self.subTest(label):
				self.assertTrue(_refused(ff.check_request, "Holiday List", child_names=True, **kwargs))

	def test_a_table_field_whose_name_starts_with_tab_is_read_as_a_table_field(self):
		for label, kwargs in {
			"fields": {"fields": ["table_notes.note", "table_notes.name as n"]},
			"filters": {"filters": {"table_notes.note": ["like", "%a%"]}},
			"order_by": {"order_by": "table_notes.note asc"},
			"the doctype's own table": {"fields": ["tabHoliday List.holiday_list_name"]},
		}.items():
			with self.subTest(label):
				self.assertFalse(_refused(ff.check_request, "Holiday List", child_names=True, **kwargs))


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
