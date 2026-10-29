"""The website print view takes only a DocType's exact name (v1.755.3), bench-free.

``monkeypatches._patch_print_view_takes_the_exact_doctype_name`` is EXECUTED against a small
``frappe`` stub installed in ``setUpModule`` and removed in ``tearDownModule`` (own CI step, so it
cannot cross-talk with another suite's stub). The stub's ``PrintPage.can_render`` is frappe
16.53.0's, line for line, and its ``frappe.db.exists`` matches a DocType name the way MariaDB's
collation does: in any letter case, trailing spaces ignored.

Run: python -m unittest erpnext_enhancements.tests.test_print_view_doctype_name -v

**These tests assert the patch, not frappe.** frappe's ``PrintPage`` claims a two-part path whose
first part names a DocType in any letter case, and ``printview`` then cannot load a controller for
that spelling, so the request ends on frappe's error page. The patch makes ``can_render`` also
require the exact name, which is the upstream fix. Declined paths fall through to ``ListPage``,
which declines any two-part path, and end on frappe's Not Found page. When frappe ships the fix,
these tests still pass (the patch becomes redundant), and the patch should be deleted.
"""

import importlib
import sys
import types
import unittest

_SAVED = {}
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.utils.modules",
	"frappe.website",
	"frappe.website.page_renderers",
	"frappe.website.page_renderers.print_page",
)
MODULE = "erpnext_enhancements.monkeypatches"
# The DocType rows the stub's database holds.
DOCTYPES = ("DocType", "Item", "Sales Order", "ToDo", "Web Page")


def _collation_key(name):
	"""How MariaDB's utf8mb4 ``_ci`` collation compares a name: case-blind, PAD SPACE."""
	return name.rstrip(" ").casefold()


def _frappe_16_53_0_print_page():
	"""A fresh ``PrintPage`` whose ``can_render`` is frappe 16.53.0's, unpatched."""
	frappe = sys.modules["frappe"]

	class PrintPage:
		def __init__(self, path=None, http_status_code=None):
			self.path = (path or "").strip("/ ")
			self.http_status_code = http_status_code

		def can_render(self):
			parts = self.path.split("/", 1)
			if len(parts) != 2 or not frappe.db.exists("DocType", parts[0], True):
				return False

			return True

	return PrintPage


def _install_stub():
	for name in STUBBED:
		_SAVED[name] = sys.modules.get(name)
	frappe = types.ModuleType("frappe")
	frappe.warnings = []

	class DB:
		def __init__(self):
			self.calls = []
			self.fail_from_call = None

		def exists(self, dt, dn=None, cache=False):
			self.calls.append((dt, dn, cache))
			if self.fail_from_call and len(self.calls) >= self.fail_from_call:
				raise RuntimeError("database gone")
			assert dt == "DocType"
			matches = [name for name in DOCTYPES if _collation_key(name) == _collation_key(dn or "")]
			return matches[0] if matches else None

	frappe.db = DB()
	frappe.logger = lambda *_a, **_k: types.SimpleNamespace(
		warning=lambda message, **_kw: frappe.warnings.append(message)
	)

	utils = types.ModuleType("frappe.utils")
	modules = types.ModuleType("frappe.utils.modules")
	modules.get_modules_from_app = lambda app: []
	utils.modules = modules
	frappe.utils = utils

	website = types.ModuleType("frappe.website")
	page_renderers = types.ModuleType("frappe.website.page_renderers")
	print_page = types.ModuleType("frappe.website.page_renderers.print_page")
	website.page_renderers = page_renderers
	page_renderers.print_page = print_page
	frappe.website = website

	for name, module in (
		("frappe", frappe),
		("frappe.utils", utils),
		("frappe.utils.modules", modules),
		("frappe.website", website),
		("frappe.website.page_renderers", page_renderers),
		("frappe.website.page_renderers.print_page", print_page),
	):
		sys.modules[name] = module
	print_page.PrintPage = _frappe_16_53_0_print_page()
	return frappe


def setUpModule():
	global frappe_stub, monkeypatches
	frappe_stub = _install_stub()
	sys.modules.pop(MODULE, None)
	monkeypatches = importlib.import_module(MODULE)


def tearDownModule():
	sys.modules.pop(MODULE, None)
	for name, module in _SAVED.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


class TestPrintViewTakesTheExactDocTypeName(unittest.TestCase):
	def setUp(self):
		self.print_page = sys.modules["frappe.website.page_renderers.print_page"]
		self.print_page.PrintPage = _frappe_16_53_0_print_page()
		frappe_stub.db.calls = []
		frappe_stub.db.fail_from_call = None
		frappe_stub.warnings = []

	def _apply(self):
		monkeypatches._patch_print_view_takes_the_exact_doctype_name()

	def _claims(self, path):
		"""Build the renderer the way frappe's PathResolver does, (endpoint, http_status_code), and ask it."""
		return self.print_page.PrintPage(path, 200).can_render()

	def test_unpatched_it_claims_a_doctype_name_in_any_letter_case(self):
		"""The premise. Without this, every other test here could pass against a no-op."""
		for path in ("item/ITEM-0001", "ITEM/x", "sales order/x", "todo/x", "Item /x"):
			with self.subTest(path=path):
				self.assertTrue(
					self._claims(path), "frappe's PrintPage no longer claims this; is the patch needed?"
				)

	def test_the_exact_name_is_still_claimed(self):
		self._apply()
		for path in (
			"Item/ITEM-0001",
			"Sales Order/SAL-ORD-2026-00001",
			"ToDo/abc123",
			"DocType/User",
			"Item/a/name/with/slashes",
			"/Item/ITEM-0001/",
		):
			with self.subTest(path=path):
				self.assertTrue(self._claims(path))

	def test_any_other_spelling_is_declined(self):
		"""It then falls through to ListPage, which declines a two-part path, and ends on Not Found."""
		self._apply()
		for path in (
			"item/ITEM-0001",
			"ITEM/x",
			"iTEM/x",
			"sales order/x",
			"SALES ORDER/x",
			"todo/x",
			"TODO/x",
			"doctype/User",
			"web page/x",
			"Item /x",
		):
			with self.subTest(path=path):
				self.assertFalse(self._claims(path))

	def test_what_frappe_declined_stays_declined(self):
		self._apply()
		for path in ("", "Item", "item", "Nothing/x", "nothing/x", "Sales-Order/x", "sales_order/x"):
			with self.subTest(path=path):
				self.assertFalse(self._claims(path))

	def test_the_extra_lookup_is_the_query_frappe_just_made(self):
		"""Same arguments, cache flag included, so frappe's per-request value cache answers it."""
		self._apply()
		self._claims("item/x")
		self.assertEqual(frappe_stub.db.calls, [("DocType", "item", True), ("DocType", "item", True)])

	def test_nothing_more_is_looked_up_for_a_path_frappe_declines(self):
		self._apply()
		self._claims("Item")
		self._claims("Nothing/x")
		self.assertEqual(frappe_stub.db.calls, [("DocType", "Nothing", True)])

	def test_a_failed_lookup_keeps_frappes_answer(self):
		"""It runs on website requests; failing the page over this check would be worse than the bug."""
		self._apply()
		frappe_stub.db.fail_from_call = 2  # frappe's own lookup succeeds, the patch's raises
		self.assertTrue(self._claims("item/x"))

	def test_an_upstream_fix_makes_it_redundant_not_wrong(self):
		frappe = frappe_stub

		def fixed_can_render(self):
			parts = self.path.split("/", 1)
			if len(parts) != 2:
				return False
			return frappe.db.exists("DocType", parts[0], True) == parts[0]

		self.print_page.PrintPage.can_render = fixed_can_render
		self._apply()
		self.assertTrue(self._claims("Item/ITEM-0001"))
		self.assertFalse(self._claims("item/ITEM-0001"))
		self.assertFalse(self._claims("Item"))

	def test_applying_twice_does_not_double_wrap(self):
		"""A doubly-wrapped method would grow a frame, and a query, on every website request."""
		self._apply()
		first = self.print_page.PrintPage.can_render
		self._apply()
		self.assertIs(self.print_page.PrintPage.can_render, first, "the wrapper was applied twice")
		self._claims("item/x")
		self.assertEqual(len(frappe_stub.db.calls), 2)

	def test_it_keeps_the_original_methods_name(self):
		self._apply()
		self.assertEqual(self.print_page.PrintPage.can_render.__name__, "can_render")

	def test_apply_runs_this_patch(self):
		"""It is registered in `_PATCHES`, not merely defined."""
		self.assertIn(monkeypatches._patch_print_view_takes_the_exact_doctype_name, monkeypatches._PATCHES)
		monkeypatches.apply()
		self.assertFalse(self._claims("item/x"))
		self.assertTrue(self._claims("Item/x"))


if __name__ == "__main__":
	unittest.main()
