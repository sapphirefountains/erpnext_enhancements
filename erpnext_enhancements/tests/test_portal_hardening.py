"""Portal hardening before the customer portal opens (v1.755.0), bench-free.

``setup/stock_web_forms.py`` is EXECUTED against a small ``frappe`` stub installed in ``setUpModule``
and removed in ``tearDownModule`` (own CI step, so it cannot cross-talk with another suite's stub).
The Voice Call Settings permissions and the hook wiring are read from the JSON and ``hooks.py``.

Run: python -m unittest erpnext_enhancements.tests.test_portal_hardening -v

* :class:`TestStockWebForms` — ERPNext's ``tasks``, ``issues`` and ``addresses`` web forms stay
  unpublished. ERPNext re-imports a standard web form, published, whenever its JSON's ``modified``
  moves, so this must run after every migrate (not once, as a patch would) and must leave
  ``modified`` alone. It never touches the other standard forms and never raises.
* :class:`TestVoiceCallSettingsPermissions` — the Custom DocPerm rows are the complete permission
  set for the doctype (they replace its standard rows), so they are pinned whole: staff only, no
  delete or share for staff, nothing at all for All or Guest.
"""

import ast
import importlib
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
STUBBED = ("frappe", "frappe.website", "frappe.website.router")
MODULE = "erpnext_enhancements.setup.stock_web_forms"


def _install_stub():
	for name in STUBBED:
		_SAVED[name] = sys.modules.get(name)
	frappe = types.ModuleType("frappe")
	frappe.forms = {}
	frappe.writes = []
	frappe.errors = []
	frappe.cache_clears = []

	def get_all(doctype, filters=None, pluck=None, **_kw):
		assert doctype == "Web Form" and pluck == "name"
		names = filters["name"][1]
		return [
			name
			for name, published in frappe.forms.items()
			if name in names and published == filters["published"]
		]

	class DB:
		def set_value(self, doctype, name, field, value, update_modified=True):
			frappe.writes.append((doctype, name, field, value, update_modified))
			frappe.forms[name] = value

	frappe.get_all = frappe.real_get_all = get_all
	frappe.db = DB()
	frappe.log_error = lambda title=None, message=None: frappe.errors.append(title)
	frappe.get_traceback = lambda: "traceback"

	website = types.ModuleType("frappe.website")
	router = types.ModuleType("frappe.website.router")
	router.clear_routing_cache = lambda: frappe.cache_clears.append(1)
	for name, module in (("frappe", frappe), ("frappe.website", website), ("frappe.website.router", router)):
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
		frappe_stub.writes = []
		frappe_stub.errors = []
		frappe_stub.cache_clears = []
		frappe_stub.get_all = frappe_stub.real_get_all

	def test_the_list_is_the_three_forms_the_portal_replaces(self):
		self.assertEqual(stock_web_forms.STOCK_WEB_FORMS, ("tasks", "issues", "addresses"))

	def test_published_ones_are_unpublished_without_moving_modified(self):
		"""update_modified=False: a later ERPNext change to the form must still import (newer than the row)."""
		stock_web_forms.unpublish_stock_web_forms()
		self.assertEqual(
			sorted(frappe_stub.writes),
			sorted(("Web Form", name, "published", 0, False) for name in ("tasks", "issues", "addresses")),
		)
		self.assertEqual(frappe_stub.cache_clears, [1])
		self.assertEqual(frappe_stub.errors, [])

	def test_the_other_standard_forms_are_left_alone(self):
		stock_web_forms.unpublish_stock_web_forms()
		for name in ("edit-profile", "request-data", "request-to-delete-data"):
			with self.subTest(form=name):
				self.assertEqual(frappe_stub.forms[name], 1)

	def test_a_second_migrate_writes_nothing(self):
		stock_web_forms.unpublish_stock_web_forms()
		frappe_stub.writes, frappe_stub.cache_clears = [], []
		stock_web_forms.unpublish_stock_web_forms()
		self.assertEqual(frappe_stub.writes, [])
		self.assertEqual(frappe_stub.cache_clears, [])

	def test_a_re_import_is_undone_on_the_next_run(self):
		stock_web_forms.unpublish_stock_web_forms()
		frappe_stub.forms["addresses"] = 1  # an ERPNext upgrade re-imported it, published
		frappe_stub.writes = []
		stock_web_forms.unpublish_stock_web_forms()
		self.assertEqual(frappe_stub.writes, [("Web Form", "addresses", "published", 0, False)])

	def test_it_never_publishes_anything(self):
		frappe_stub.forms = {"tasks": 0, "issues": 0, "addresses": 0}
		stock_web_forms.unpublish_stock_web_forms()
		self.assertEqual(frappe_stub.writes, [])

	def test_a_failure_is_logged_and_never_raised(self):
		"""An after_migrate hook that raises aborts bench migrate, which on this repo is the deploy."""

		def explode(*_args, **_kw):
			raise RuntimeError("database gone")

		frappe_stub.get_all = explode
		stock_web_forms.unpublish_stock_web_forms()
		self.assertEqual(frappe_stub.errors, ["Stock web forms not unpublished"])

	def test_it_runs_after_every_migrate_and_is_not_a_patch(self):
		self.assertIn(
			"erpnext_enhancements.setup.stock_web_forms.unpublish_stock_web_forms", _hook("after_migrate")
		)
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		self.assertNotIn("stock_web_forms", patches)


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
