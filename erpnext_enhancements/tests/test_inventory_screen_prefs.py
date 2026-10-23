# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the inventory screens' reorder switch (v1.728.0, TASK-2026-02920).

Nik, 2026-10-22: "I would like the reorder values to be hidden or toggleable, it's not information I
need to see constantly. Ideally I would only need to change and alter once." The Bin Manager and the
Inventory Scanner Audit open with a clean list; a switch in each toolbar shows the reorder boxes; the
choice is the person's for good, on any device.

* **The store** (``api/planner_views_prefs.py``, the inventory part) against a ``frappe`` stub, in the
  shape ``tests/test_guided_view_settings.py`` uses: the caller's own ``__UserSettings`` row
  (``Inventory Screens``) written straight to the table and the cached copy dropped (frappe's own
  user_settings are Redis-only, and a deploy flushes Redis), one switch per screen, only a real
  ``true`` reads as on, anything unreadable reads as **off**, the role gate, an unknown screen refused.
* **The wiring**, read from the source: both boot payloads carry the switch, both pages call the one
  store, neither uses frappe's Redis-only user_settings, and the pages' defaults are off. The pages
  themselves run under node in ``scripts/test_bin_manager.mjs`` and ``scripts/test_desk_page_history.js``
  (default hidden; the switch shows the boxes and reads the rules once; the saved choice opens the screen
  as it was left; a preference that cannot be read, or saved, falls back to hidden / still works).

Run: python -m unittest erpnext_enhancements.tests.test_inventory_screen_prefs -v
"""

import ast
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
PREFS_PY = APP / "api" / "planner_views_prefs.py"
BINS_PY = APP / "inventory_enhancements" / "bin_assignments.py"
SCANNER_PY = APP / "api" / "inventory_scanner.py"
BIN_PAGE = APP / "inventory_enhancements" / "page" / "bin_manager" / "bin_manager.js"
ISA_PAGE = APP / "inventory_enhancements" / "page" / "inventory_scanner_audit" / "inventory_scanner_audit.js"
PREFS = "erpnext_enhancements.api.planner_views_prefs"
USER = "parker@example.com"
ROW = "Inventory Screens"

_saved_modules = {}
_modules_before = set()


class _Throw(Exception):
	pass


class _PermissionError(Exception):
	pass


class _FakeTable:
	"""``__UserSettings`` as a dict, with the two statements the module runs."""

	def __init__(self):
		self.rows = {}
		self.writes = []
		self.fail_reads = False

	def sql(self, query, values=None, **kwargs):
		if "from `__UserSettings`" in query:
			if self.fail_reads:
				raise RuntimeError("the database did not answer")
			text = self.rows.get((values[0], values[1]))
			return [(text,)] if text is not None else []
		return []

	def multisql(self, sql_dict, values=(), **kwargs):
		self.writes.append((sql_dict, values))
		user, doctype, text, again = values
		assert text == again
		self.rows[(user, doctype)] = text


STATE = {}


def _throw(message, exc=None, title=None):
	raise (exc or _Throw)(message)


def _install():
	global _modules_before
	_modules_before = set(sys.modules)
	for name in list(sys.modules):
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements.api")):
			_saved_modules[name] = sys.modules.pop(name)
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: "2026-10-22 09:00:00"
	utils.cint = lambda value: int(float(value or 0))
	frappe.utils = utils
	frappe._ = lambda text, *a, **k: text
	frappe.whitelist = lambda *a, **k: (a[0] if a and callable(a[0]) else (lambda fn: fn))
	frappe.throw = _throw
	frappe.PermissionError = _PermissionError
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})
	return frappe


def _uninstall():
	for name in set(sys.modules) - _modules_before:
		if name == "frappe" or name.startswith(("frappe.", "erpnext_enhancements.api")):
			sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)
	_saved_modules.clear()


class TestTheStore(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.frappe = _install()
		cls.prefs = importlib.import_module(PREFS)

	@classmethod
	def tearDownClass(cls):
		_uninstall()

	def setUp(self):
		f = self.frappe
		STATE.clear()
		STATE["roles"] = ["Stock User"]
		f.table = _FakeTable()
		f.dropped = []
		f.session = types.SimpleNamespace(user=USER)
		f.cache = types.SimpleNamespace(hdel=lambda name, key: f.dropped.append((name, key)))
		f.db = types.SimpleNamespace(sql=f.table.sql, multisql=f.table.multisql)

	def row(self, user=USER):
		text = self.frappe.table.rows.get((user, ROW))
		return json.loads(text) if text else None

	def test_nothing_stored_is_off_on_both_screens(self):
		self.assertEqual(
			self.prefs.get_inventory_prefs(), {"show_reorder": {"bin_manager": False, "scanner_audit": False}}
		)
		self.assertFalse(self.prefs.show_reorder("bin_manager"))
		self.assertFalse(self.prefs.show_reorder("scanner_audit"))

	def test_turning_it_on_is_remembered_for_that_screen_only(self):
		out = self.prefs.set_show_reorder("bin_manager", 1)
		self.assertEqual(out, {"screen": "bin_manager", "show_reorder": True})
		self.assertTrue(self.prefs.show_reorder("bin_manager"))
		self.assertFalse(self.prefs.show_reorder("scanner_audit"), "each screen has its own switch")
		self.prefs.set_show_reorder("scanner_audit", "true")
		self.assertTrue(self.prefs.show_reorder("scanner_audit"))
		self.prefs.set_show_reorder("bin_manager", 0)
		self.assertEqual(
			self.prefs.get_inventory_prefs(), {"show_reorder": {"bin_manager": False, "scanner_audit": True}}
		)

	def test_what_a_browser_sends_for_off_is_off(self):
		self.prefs.set_show_reorder("bin_manager", True)
		for off in (0, "0", "false", False, "", None):
			with self.subTest(sent=off):
				self.prefs.set_show_reorder("bin_manager", off)
				self.assertFalse(self.prefs.show_reorder("bin_manager"))
				self.prefs.set_show_reorder("bin_manager", True)

	def test_it_is_written_straight_to_the_table_and_the_cached_copy_dropped(self):
		"""Not frappe's user_settings.save: that writes only to Redis, which a deploy flushes."""
		self.prefs.set_show_reorder("bin_manager", 1)
		self.assertEqual(len(self.frappe.table.writes), 1)
		self.assertIn(("_user_settings", f"{ROW}::{USER}"), self.frappe.dropped)
		self.assertEqual(
			self.row(),
			{
				"inventory_screens": {
					"show_reorder": {"bin_manager": True, "scanner_audit": False},
					"version": 1,
				}
			},
		)

	def test_only_the_callers_own_row_and_other_parts_of_it_are_kept(self):
		self.frappe.table.rows[(USER, ROW)] = json.dumps({"something_else": {"keep": 1}})
		self.prefs.set_show_reorder("bin_manager", 1)
		self.assertEqual(self.row()["something_else"], {"keep": 1})
		self.frappe.session.user = "lisa@example.com"
		self.assertFalse(self.prefs.show_reorder("bin_manager"), "another person's choice is not hers")
		self.prefs.set_show_reorder("scanner_audit", 1)
		self.assertEqual(
			self.row(USER)["inventory_screens"]["show_reorder"], {"bin_manager": True, "scanner_audit": False}
		)

	def test_anything_unreadable_is_off(self):
		for damaged in (
			"not json at all",
			json.dumps([1, 2]),
			json.dumps({"inventory_screens": "yes"}),
			json.dumps({"inventory_screens": {"show_reorder": "on"}}),
			json.dumps({"inventory_screens": {"show_reorder": {"bin_manager": "yes", "scanner_audit": 1}}}),
			json.dumps({"inventory_screens": {"show_reorder": {"bin_manager": None}}}),
		):
			with self.subTest(row=damaged[:40]):
				self.frappe.table.rows[(USER, ROW)] = damaged
				self.assertFalse(self.prefs.show_reorder("bin_manager"))
				self.assertFalse(self.prefs.show_reorder("scanner_audit"))
				self.assertEqual(
					self.prefs.get_inventory_prefs(),
					{"show_reorder": {"bin_manager": False, "scanner_audit": False}},
				)

	def test_a_database_that_will_not_answer_never_stops_a_screen_opening(self):
		self.prefs.set_show_reorder("bin_manager", 1)
		self.frappe.table.fail_reads = True
		self.assertFalse(self.prefs.show_reorder("bin_manager"), "falls back to hidden, does not raise")

	def test_an_unknown_screen_is_refused_and_nothing_is_written(self):
		for bad in ("planner", "", None, "bin_manager; drop", 5):
			with self.subTest(screen=bad):
				with self.assertRaises(_Throw):
					self.prefs.set_show_reorder(bad, 1)
		self.assertEqual(self.frappe.table.writes, [])

	def test_the_role_gate_is_the_screens_own(self):
		STATE["roles"] = ["Sales User"]
		with self.assertRaises(_PermissionError):
			self.prefs.get_inventory_prefs()
		with self.assertRaises(_PermissionError):
			self.prefs.set_show_reorder("bin_manager", 1)
		self.assertEqual(self.frappe.table.writes, [])
		STATE["roles"] = ["Inventory Clerk"]
		self.assertFalse(self.prefs.show_reorder("scanner_audit"))
		self.prefs.set_show_reorder("scanner_audit", 1)
		self.assertTrue(self.prefs.show_reorder("scanner_audit"))


def _function(path, name):
	return next(
		n
		for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
		if isinstance(n, ast.FunctionDef) and n.name == name
	)


class TestTheWiring(unittest.TestCase):
	def test_both_boot_payloads_carry_the_switch(self):
		boot = ast.unparse(_function(BINS_PY, "manager_boot"))
		self.assertIn("'show_reorder': _show_reorder('bin_manager')", boot)
		scanner = ast.unparse(_function(SCANNER_PY, "get_bootstrap"))
		self.assertIn("'prefs': {'show_reorder': _show_reorder()}", scanner)
		self.assertIn("show_reorder('scanner_audit')", ast.unparse(_function(SCANNER_PY, "_show_reorder")))

	def test_the_endpoints_are_whitelisted_and_the_write_is_post_only(self):
		prefs = ast.parse(PREFS_PY.read_text(encoding="utf-8"))
		decorated = {
			n.name: ast.unparse(n.decorator_list[0])
			for n in prefs.body
			if isinstance(n, ast.FunctionDef) and n.decorator_list
		}
		self.assertEqual(decorated["get_inventory_prefs"], "frappe.whitelist()")
		self.assertEqual(decorated["set_show_reorder"], "frappe.whitelist(methods=['POST'])")
		self.assertNotIn("show_reorder", decorated, "the boot helper is not an endpoint")

	def test_it_does_not_use_frappes_redis_only_user_settings(self):
		text = PREFS_PY.read_text(encoding="utf-8")
		start = text.index("the inventory screens' switches")
		self.assertNotIn("user_settings.save", text[start:])
		self.assertNotIn("frappe.cache.hset", text[start:])
		self.assertIn("_write_row(INVENTORY_SETTINGS", text[start:])

	def test_both_pages_save_through_the_one_store_and_default_to_off(self):
		method = "erpnext_enhancements.api.planner_views_prefs.set_show_reorder"
		bins = BIN_PAGE.read_text(encoding="utf-8")
		isa = ISA_PAGE.read_text(encoding="utf-8")
		self.assertIn(method, bins)
		self.assertIn(method, isa)
		self.assertIn('screen: "bin_manager"', bins)
		self.assertIn("screen: 'scanner_audit'", isa)
		# only a real true turns them on
		self.assertIn("boot.show_reorder === true", bins)
		self.assertIn("d.prefs.show_reorder === true", isa)
		self.assertIn("showReorder: false", isa)
		# the browser alone is not the store
		for text in (bins, isa):
			self.assertNotIn("localStorage", text)

	def test_the_pages_read_the_rules_only_when_they_are_shown(self):
		bins = BIN_PAGE.read_text(encoding="utf-8")
		self.assertIn("if (!this.reorder_shown() || !loc || this.reorder_for === loc) return;", bins)
		isa = ISA_PAGE.read_text(encoding="utf-8")
		self.assertIn("if (this.reorderShown() && p) this.reorder.load(", isa)

	def test_the_scanner_switch_never_keeps_the_focus(self):
		isa = ISA_PAGE.read_text(encoding="utf-8")
		self.assertIn("this.$toggle.on('mousedown', (e) => e.preventDefault());", isa)
		self.assertIn("if (p) this.$pending.find('.isa-qty').focus();", isa)
		self.assertIn("else this.focusScan();", isa)


if __name__ == "__main__":
	unittest.main()
