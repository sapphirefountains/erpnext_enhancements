"""Portal hardening before the customer portal opens (v1.755.0), bench-free.

``setup/stock_web_forms.py`` is EXECUTED against a small ``frappe`` stub installed in ``setUpModule``
and removed in ``tearDownModule`` (own CI step, so it cannot cross-talk with another suite's stub).
The Voice Call Settings permissions and the hook wiring are read from the JSON and ``hooks.py``.

Run: python -m unittest erpnext_enhancements.tests.test_portal_hardening -v

* :class:`TestStockWebForms` — ERPNext's ``tasks``, ``issues`` and ``addresses`` web forms stay
  unpublished, and the Issues and Addresses portal menu rows that lead to them stay disabled.
  ERPNext re-imports a standard web form, published, whenever its JSON's ``modified`` moves, so this
  must run after every migrate (not once, as a patch would), on install too, and must leave
  ``modified`` alone. Each flip is printed. It never touches the other standard forms or menu rows,
  and never raises.
* :class:`TestUnpublishedStockWebFormRoutes` — while those forms stay unpublished, their routes and
  every path under them answer frappe's Not Found page (v1.755.2) instead of falling through to a
  renderer that answers an error page. A published form, a different route, frappe's own exact-case
  print view and a failure are all left to frappe. Routes come from the rows, cached until frappe
  clears its website cache.
* :class:`TestVoiceCallSettingsPermissions` — the Custom DocPerm rows are the complete permission
  set for the doctype (they replace its standard rows), so they are pinned whole: staff only, no
  delete or share for staff, nothing at all for All or Guest.
"""

import ast
import contextlib
import importlib
import io
import json
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
if str(REPO) not in sys.path:
	sys.path.insert(0, str(REPO))

_SAVED = {}
STUBBED = (
	"frappe",
	"frappe.website",
	"frappe.website.utils",
	"frappe.website.doctype",
	"frappe.website.doctype.web_form",
	"frappe.website.doctype.web_form.web_form",
	"frappe.website.page_renderers",
	"frappe.website.page_renderers.not_found_page",
)
MODULE = "erpnext_enhancements.setup.stock_web_forms"
# The routes ERPNext's web form JSON gives each form (the Addresses form's route is singular).
ROUTES = {
	"tasks": "tasks",
	"issues": "issues",
	"addresses": "address",
	"edit-profile": "update-profile",
	"request-data": "request-data",
}
STOCK_MENU = {
	"/project": 1,
	"/orders": 1,
	"/invoices": 1,
	"/issues": 1,
	"/addresses": 1,
	"/timesheets": 1,
}


def _install_stub():
	for name in STUBBED:
		_SAVED[name] = sys.modules.get(name)
	frappe = types.ModuleType("frappe")
	frappe.forms = {}
	frappe.menu = {}
	frappe.writes = []
	frappe.errors = []
	frappe.cache_clears = []
	frappe.doc_cache_clears = []
	frappe.routes = dict(ROUTES)
	frappe.route_reads = 0
	frappe.not_found = []

	def get_all(doctype, filters=None, pluck=None, fields=None, **_kw):
		if doctype == "Web Form" and fields:
			assert set(fields) == {"name", "route"} and "published" not in filters
			frappe.route_reads += 1
			names = filters["name"][1]
			return [
				types.SimpleNamespace(name=name, route=route)
				for name, route in frappe.routes.items()
				if name in names
			]
		if doctype == "Web Form":
			assert pluck == "name"
			names = filters["name"][1]
			return [name for name, on in frappe.forms.items() if name in names and on == filters["published"]]
		assert doctype == "Portal Menu Item" and filters["parent"] == "Portal Settings"
		assert set(fields) >= {"name", "route"}
		routes = filters["route"][1]
		return [
			types.SimpleNamespace(name=f"row{route}", route=route)
			for route, on in frappe.menu.items()
			if route in routes and on == filters["enabled"]
		]

	class DB:
		def set_value(self, doctype, name, field, value, update_modified=True):
			frappe.writes.append((doctype, name, field, value, update_modified))
			if doctype == "Web Form":
				frappe.forms[name] = value
			else:
				frappe.menu[name[len("row") :]] = value

	frappe.get_all = frappe.real_get_all = get_all
	frappe.db = DB()
	frappe.log_error = lambda title=None, message=None: frappe.errors.append(title)
	frappe.get_traceback = lambda: "traceback"
	frappe.clear_document_cache = lambda doctype, name=None: frappe.doc_cache_clears.append((doctype, name))

	class Cache:
		"""frappe.cache's get_value(key, generator) / delete_value(key), in a dict."""

		def __init__(self):
			self.store = {}

		def get_value(self, key, generator=None):
			if self.store.get(key) is None and generator:
				self.store[key] = generator()
			return self.store.get(key)

		def delete_value(self, key):
			self.store.pop(key, None)

	frappe.cache = Cache()

	website = types.ModuleType("frappe.website")
	utils = types.ModuleType("frappe.website.utils")
	utils.clear_cache = lambda path=None: frappe.cache_clears.append(path)

	web_form = types.ModuleType("frappe.website.doctype.web_form.web_form")

	def get_published_web_forms():
		# frappe 16.53.0: get_all("Web Form", ["name", "route", "modified"], {"published": 1}).
		return [
			types.SimpleNamespace(name=name, route=frappe.routes.get(name))
			for name, on in frappe.forms.items()
			if on
		]

	web_form.get_published_web_forms = get_published_web_forms

	not_found_page = types.ModuleType("frappe.website.page_renderers.not_found_page")

	class NotFoundPage:
		def __init__(self, path, http_status_code=None):
			self.args = (path, http_status_code)

		def render(self):
			frappe.not_found.append(self.args)
			return f"404 for {self.args[0]}"

	not_found_page.NotFoundPage = NotFoundPage

	for name, module in (
		("frappe", frappe),
		("frappe.website", website),
		("frappe.website.utils", utils),
		("frappe.website.doctype", types.ModuleType("frappe.website.doctype")),
		("frappe.website.doctype.web_form", types.ModuleType("frappe.website.doctype.web_form")),
		("frappe.website.doctype.web_form.web_form", web_form),
		("frappe.website.page_renderers", types.ModuleType("frappe.website.page_renderers")),
		("frappe.website.page_renderers.not_found_page", not_found_page),
	):
		sys.modules[name] = module
	return frappe


def setUpModule():
	global frappe_stub, stock_web_forms
	frappe_stub = _install_stub()
	sys.modules.pop(MODULE, None)
	stock_web_forms = importlib.import_module(MODULE)


def tearDownModule():
	sys.modules.pop(MODULE, None)
	for name, module in _SAVED.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


def _hook(name):
	"""The value of the LAST top-level assignment to ``name`` in hooks.py (as frappe would see it)."""
	tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
	nodes = [n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == name]
	return ast.literal_eval(nodes[-1].value)


def _run():
	"""Run the hook once; return what it printed."""
	out = io.StringIO()
	with contextlib.redirect_stdout(out):
		stock_web_forms.unpublish_stock_web_forms()
	return out.getvalue()


class TestStockWebForms(unittest.TestCase):
	def setUp(self):
		frappe_stub.forms = {
			"tasks": 1,
			"issues": 1,
			"addresses": 1,
			"edit-profile": 1,
			"request-data": 1,
			"request-to-delete-data": 1,
			"contact-us": 0,
		}
		frappe_stub.menu = dict(STOCK_MENU)
		frappe_stub.writes = []
		frappe_stub.errors = []
		frappe_stub.cache_clears = []
		frappe_stub.doc_cache_clears = []
		frappe_stub.get_all = frappe_stub.real_get_all

	def _forms_written(self):
		return sorted(w for w in frappe_stub.writes if w[0] == "Web Form")

	def _menu_written(self):
		return sorted(w for w in frappe_stub.writes if w[0] == "Portal Menu Item")

	def test_the_lists_are_the_three_forms_and_their_two_menu_rows(self):
		self.assertEqual(stock_web_forms.STOCK_WEB_FORMS, ("tasks", "issues", "addresses"))
		self.assertEqual(stock_web_forms.STOCK_MENU_ROUTES, ("/issues", "/addresses"))

	def test_published_ones_are_unpublished_without_moving_modified(self):
		"""update_modified=False: a later ERPNext change to the form must still import (newer than the row)."""
		_run()
		self.assertEqual(
			self._forms_written(),
			sorted(("Web Form", name, "published", 0, False) for name in ("tasks", "issues", "addresses")),
		)
		self.assertEqual(frappe_stub.cache_clears, [None])
		self.assertEqual(frappe_stub.errors, [])

	def test_the_menu_rows_that_lead_to_them_are_disabled(self):
		"""Otherwise a customer's portal sidebar shows Issues and Addresses, and both 404."""
		_run()
		self.assertEqual(
			self._menu_written(),
			[
				("Portal Menu Item", "row/addresses", "enabled", 0, False),
				("Portal Menu Item", "row/issues", "enabled", 0, False),
			],
		)
		self.assertEqual(frappe_stub.doc_cache_clears, [("Portal Settings", "Portal Settings")])

	def test_the_other_menu_rows_are_left_alone(self):
		_run()
		for route in ("/project", "/orders", "/invoices", "/timesheets"):
			with self.subTest(route=route):
				self.assertEqual(frappe_stub.menu[route], 1)

	def test_the_other_standard_forms_are_left_alone(self):
		_run()
		for name in ("edit-profile", "request-data", "request-to-delete-data"):
			with self.subTest(form=name):
				self.assertEqual(frappe_stub.forms[name], 1)

	def test_a_second_migrate_writes_nothing_and_says_nothing(self):
		_run()
		frappe_stub.writes, frappe_stub.cache_clears, frappe_stub.doc_cache_clears = [], [], []
		self.assertEqual(_run(), "")
		self.assertEqual(frappe_stub.writes, [])
		self.assertEqual(frappe_stub.cache_clears, [])
		self.assertEqual(frappe_stub.doc_cache_clears, [])

	def test_a_re_import_or_re_enable_is_undone_and_printed(self):
		_run()
		frappe_stub.forms["addresses"] = 1  # an ERPNext upgrade re-imported it, published
		frappe_stub.menu["/issues"] = 1  # somebody ticked it again in Portal Settings
		frappe_stub.writes = []
		printed = _run()
		self.assertEqual(
			sorted(frappe_stub.writes),
			[
				("Portal Menu Item", "row/issues", "enabled", 0, False),
				("Web Form", "addresses", "published", 0, False),
			],
		)
		self.assertIn("unpublished ERPNext's stock web form 'addresses'", printed)
		self.assertIn("disabled the portal menu row for /issues", printed)

	def test_every_flip_is_printed(self):
		printed = _run()
		self.assertEqual(len(printed.strip().splitlines()), 5)

	def test_it_never_publishes_or_enables_anything(self):
		frappe_stub.forms = {"tasks": 0, "issues": 0, "addresses": 0}
		frappe_stub.menu = {"/issues": 0, "/addresses": 0}
		_run()
		self.assertEqual(frappe_stub.writes, [])

	def test_a_failure_is_logged_and_never_raised(self):
		"""An after_migrate hook that raises aborts bench migrate, which on this repo is the deploy."""

		def explode(*_args, **_kw):
			raise RuntimeError("database gone")

		frappe_stub.get_all = explode
		_run()
		self.assertEqual(frappe_stub.errors, ["Stock web forms not unpublished"])

	def test_it_runs_after_every_migrate_and_on_install_and_is_not_a_patch(self):
		dotted = "erpnext_enhancements.setup.stock_web_forms.unpublish_stock_web_forms"
		self.assertIn(dotted, _hook("after_migrate"))
		self.assertIn(dotted, _hook("after_install"))
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		self.assertNotIn("stock_web_forms", patches)


def _claims(path):
	"""Build the renderer the way frappe's PathResolver does, (endpoint, http_status_code), and ask it."""
	return stock_web_forms.UnpublishedStockWebFormPage(path, 200).can_render()


class TestUnpublishedStockWebFormRoutes(unittest.TestCase):
	def setUp(self):
		frappe_stub.forms = {"tasks": 0, "issues": 0, "addresses": 0, "edit-profile": 1, "request-data": 1}
		frappe_stub.routes = dict(ROUTES)
		frappe_stub.route_reads = 0
		frappe_stub.not_found = []
		frappe_stub.cache = type(frappe_stub.cache)()
		frappe_stub.get_all = frappe_stub.real_get_all

	def test_the_route_and_every_path_under_it_answer_not_found(self):
		"""The paths each form owned while published (route, list, new, a record, its edit) and deeper."""
		for path in (
			"address",
			"address/list",
			"address/new",
			"address/ADDR-0001",
			"address/ADDR-0001/edit",
			"address/a/b/c",
			"tasks/list",
			"tasks/new",
			"issues",
			"issues/ISS-0001",
		):
			with self.subTest(path=path):
				self.assertTrue(_claims(path))

	def test_slashes_around_the_path_do_not_matter(self):
		for path in ("/address/list", "address/list/", "/address/new/", " /address "):
			with self.subTest(path=path):
				self.assertTrue(_claims(path))

	def test_other_paths_are_left_to_frappe(self):
		"""`addresses` keeps ERPNext's redirect; `Address/<name>` is frappe's exact-case print view."""
		for path in (
			"",
			"addresses",
			"addresses/ADDR-0001",
			"addressbook",
			"address-book/list",
			"Address/ADDR-0001",
			"ADDRESS/list",
			"task/list",
			"issue/new",
			"orders/SO-0001",
			"me",
			"login",
			"update-profile",
			"request-data/new",
		):
			with self.subTest(path=path):
				self.assertFalse(_claims(path))

	def test_a_published_form_is_left_to_frappes_web_form_renderer(self):
		"""Hooked renderers run first: claiming a published form's route would hide the form."""
		frappe_stub.forms["addresses"] = 1
		self.assertFalse(_claims("address/list"))
		self.assertFalse(_claims("address"))
		self.assertTrue(_claims("tasks/list"))

	def test_it_renders_frappes_not_found_page_for_the_path(self):
		"""Built as the resolver's own fallback is, NotFoundPage(endpoint): no 200 handed on to it."""
		page = stock_web_forms.UnpublishedStockWebFormPage("address/list", 200)
		self.assertTrue(page.can_render())
		self.assertEqual(page.render(), "404 for address/list")
		self.assertEqual(frappe_stub.not_found, [("address/list", None)])

	def test_routes_come_from_the_rows_and_are_read_once(self):
		for _ in range(5):
			_claims("address/list")
			_claims("orders/SO-0001")
		self.assertEqual(frappe_stub.route_reads, 1)

	def test_a_website_cache_clear_rereads_a_changed_route(self):
		self.assertTrue(_claims("address/list"))
		frappe_stub.routes["addresses"] = "my-addresses"
		self.assertTrue(_claims("address/list"))  # still cached
		stock_web_forms.clear_route_cache("address")
		self.assertFalse(_claims("address/list"))
		self.assertTrue(_claims("my-addresses/new"))
		self.assertEqual(frappe_stub.route_reads, 2)

	def test_a_form_without_a_route_or_row_claims_nothing(self):
		frappe_stub.routes = {"tasks": "", "issues": None}
		for path in ("tasks", "issues/new", "address/list", "x"):
			with self.subTest(path=path):
				self.assertFalse(_claims(path))

	def test_a_failure_leaves_the_request_to_frappe(self):
		"""It runs before every website page, so raising would take the whole site down."""

		def explode(*_args, **_kw):
			raise RuntimeError("database gone")

		frappe_stub.get_all = explode
		self.assertFalse(_claims("address/list"))

	def test_it_is_registered_as_a_page_renderer_and_a_website_cache_hook(self):
		self.assertIn(
			"erpnext_enhancements.setup.stock_web_forms.UnpublishedStockWebFormPage", _hook("page_renderer")
		)
		self.assertIn(
			"erpnext_enhancements.setup.stock_web_forms.clear_route_cache", _hook("website_clear_cache")
		)
		renderer = stock_web_forms.UnpublishedStockWebFormPage
		self.assertTrue(hasattr(renderer, "can_render") and hasattr(renderer, "render"))


# Each row's grants, as ERPNext's own version-16-hotfix change to the doctype ends with them.
VCS = "Voice Call Settings"
EXPECTED = {
	(0, "Desk User"): {"read", "write", "create", "report", "export", "print", "email"},
	(0, "System Manager"): {"read", "write", "create", "delete", "report", "export", "print", "email"},
	(1, "System Manager"): {"read", "write", "delete", "report", "export", "print", "email"},
	(2, "Desk User"): {"read", "report", "export", "print", "email"},
	(2, "System Manager"): {"read", "write", "delete", "report", "export", "print", "email"},
}
FLAGS = (
	"amend",
	"cancel",
	"create",
	"delete",
	"email",
	"export",
	"if_owner",
	"impersonate",
	"import",
	"mask",
	"print",
	"read",
	"report",
	"select",
	"share",
	"submit",
	"write",
)


def _docperms():
	return json.loads((APP / "fixtures" / "custom_docperm.json").read_text(encoding="utf-8"))


class TestVoiceCallSettingsPermissions(unittest.TestCase):
	def setUp(self):
		self.rows = [row for row in _docperms() if row["parent"] == VCS]

	def test_the_rows_are_exactly_the_least_privilege_set(self):
		"""Custom DocPerm REPLACES the doctype's standard rows, so these five are the whole permission set."""
		found = {(row["permlevel"], row["role"]): {f for f in FLAGS if row.get(f)} for row in self.rows}
		self.assertEqual(found, EXPECTED)
		self.assertEqual(len(self.rows), len(EXPECTED))

	def test_nothing_for_all_or_guest(self):
		"""All includes Website Users; the settings are for staff who take calls."""
		self.assertFalse({row["role"] for row in self.rows} & {"All", "Guest"})

	def test_no_row_shares_or_is_owner_scoped(self):
		for row in self.rows:
			with self.subTest(row=row["name"]):
				self.assertEqual(row["share"], 0)
				self.assertEqual(row["if_owner"], 0)

	def test_every_flag_is_written_out(self):
		"""A missing key would import as the doctype default; the fixture states every one."""
		for row in self.rows:
			with self.subTest(row=row["name"]):
				self.assertEqual(set(FLAGS) - set(row), set())
				self.assertEqual(row["doctype"], "Custom DocPerm")

	def test_the_fixture_filter_exports_every_parent_in_the_file(self):
		docperm_filters = [
			entry
			for entry in _hook("fixtures")
			if isinstance(entry, dict) and entry.get("dt") == "Custom DocPerm"
		]
		self.assertEqual(len(docperm_filters), 1)
		field, op, parents = docperm_filters[0]["filters"][0]
		self.assertEqual((field, op), ("parent", "in"))
		self.assertIn(VCS, parents)
		self.assertEqual({row["parent"] for row in _docperms()} - set(parents), set())

	def test_the_file_keeps_its_permission_matrix_order(self):
		"""fixtures/README.md: custom_docperm.json is ordered by (parent, permlevel, role), names unique."""
		rows = _docperms()
		keys = [(row["parent"], row["permlevel"], row["role"]) for row in rows]
		self.assertEqual(keys, sorted(keys))
		self.assertEqual(len({row["name"] for row in rows}), len(rows))


if __name__ == "__main__":
	unittest.main()
