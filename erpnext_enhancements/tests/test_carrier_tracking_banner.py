# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Purchase Order's package banner and the carrier health tiles (v1.819.0,
TASK-2026-03206).

* ``scripts/test_po_package_tracking.js`` runs the real form script under node against a stubbed
  Desk: the form is never dirtied, the read is a GET, nothing is asked on a new or canceled order,
  links open in a new tab and are never ``javascript:``, the purchaser's date sits beside the
  carrier's, and the buttons follow what the server says the caller may do.
* The source checks are the cheap second line: no ``set_value``, ``dirty``, ``reload_doc``,
  ``set_route`` or ``frappe.require`` in the script, and it is wired as a plain ``doctype_js``.
* The FedEx and UPS tiles of Integrations Health, run on a ``frappe`` stub: not configured, switched
  off, the sandbox warning, the last answer's age and the last problem, masked on the way out; and the
  secret read only as a boolean.

Run: python -m unittest erpnext_enhancements.tests.test_carrier_tracking_banner -v
"""

import ast
import re
import shutil
import subprocess
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
SCRIPT = APP / "public" / "js" / "po_package_tracking.js"
HARNESS = REPO_ROOT / "scripts" / "test_po_package_tracking.js"
HOOKS = APP / "hooks.py"
HEALTH = APP / "api" / "integrations_health.py"

NOW = datetime(2026, 11, 13, 10, 0, 0)


def _strip_comments(src):
	src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
	return re.sub(r"(?m)^\s*//.*$", "", src)


class TestBannerScript(unittest.TestCase):
	def test_executed_under_node(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

	def test_never_touches_the_form_or_the_route(self):
		code = _strip_comments(SCRIPT.read_text(encoding="utf-8"))
		for pattern in (
			r"\.set_value\s*\(",
			r"\.dirty\s*\(",
			r"reload_doc",
			r"set_route",
			r"frappe\.require\s*\(",
			r"history\.(push|replace)State",
			r"frm\.doc\.\w+\s*=[^=]",
		):
			self.assertNotRegex(code, pattern)

	def test_is_a_plain_doctype_js_on_purchase_order(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		doctype_js = next(
			node.value
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "doctype_js"
		)
		mapping = ast.literal_eval(doctype_js)
		self.assertIn("public/js/po_package_tracking.js", mapping["Purchase Order"])


class _Row(dict):
	def __getattr__(self, key):
		return self.get(key)


def _install_frappe(rows):
	frappe = types.ModuleType("frappe")
	utils = types.ModuleType("frappe.utils")

	def get_datetime(value):
		return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))

	utils.get_datetime = get_datetime
	utils.now_datetime = lambda: NOW
	utils.add_days = lambda d, n: d
	utils.nowdate = lambda: "2026-11-13"
	utils.time_diff_in_seconds = lambda a, b: (get_datetime(a) - get_datetime(b)).total_seconds()
	utils.add_to_date = lambda dt, hours=0, as_datetime=True: dt + timedelta(hours=hours)
	utils.cint = lambda v: int(v or 0)
	frappe.utils = utils
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.db = types.SimpleNamespace(
		exists=lambda *a, **k: True, count=lambda doctype, filters=None: 2, get_single_value=lambda *a: None
	)

	def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, **k):
		key = "last_success" if "last_success" in (filters or {}) else "last_error"
		return [_Row(r) for r in rows.get(key, [])]

	frappe.get_all = get_all
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	return frappe


class TestHealthTiles(unittest.TestCase):
	def setUp(self):
		self.saved = {name: sys.modules.get(name) for name in ("frappe", "frappe.utils")}
		for name in (
			"erpnext_enhancements.api.integrations_health",
			"erpnext_enhancements.carrier_tracking.config",
		):
			sys.modules.pop(name, None)

	def tearDown(self):
		for name, value in self.saved.items():
			if value is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = value
		for name in (
			"erpnext_enhancements.api.integrations_health",
			"erpnext_enhancements.carrier_tracking.config",
		):
			sys.modules.pop(name, None)

	def tile(self, state, master=True, rows=None):
		_install_frappe(rows or {})
		import importlib

		health = importlib.import_module("erpnext_enhancements.api.integrations_health")
		config = importlib.import_module("erpnext_enhancements.carrier_tracking.config")
		config.feature_enabled = lambda settings=None: master
		config.carrier_state = lambda carrier, settings=None: state
		return health._check_fedex("fedex", "Package Tracking — FedEx", "/desk/carrier-tracking-settings")

	def test_listed(self):
		source = HEALTH.read_text(encoding="utf-8")
		self.assertIn(
			'("fedex", "Package Tracking — FedEx", "/desk/carrier-tracking-settings", _check_fedex)', source
		)
		self.assertIn(
			'("ups", "Package Tracking — UPS", "/desk/carrier-tracking-settings", _check_ups)', source
		)
		self.assertNotIn("get_password", source)

	def test_not_configured(self):
		out = self.tile((False, False, "Sandbox"))
		self.assertEqual(out["headline"], "Not configured")
		self.assertFalse(out["configured"])
		self.assertEqual(out["status"], "neutral")

	def test_switched_off(self):
		out = self.tile((False, True, "Production"), master=False)
		self.assertEqual(out["headline"], "Switched off")
		self.assertIn("master switch", out["notes"][0])

	def test_sandbox_is_amber(self):
		out = self.tile(
			(True, True, "Sandbox"), rows={"last_success": [{"last_success": NOW - timedelta(hours=1)}]}
		)
		self.assertEqual(out["status"], "amber")
		self.assertTrue(any("test data" in note for note in out["notes"]))

	def test_production_recent_answer_is_green(self):
		out = self.tile(
			(True, True, "Production"), rows={"last_success": [{"last_success": NOW - timedelta(hours=1)}]}
		)
		self.assertEqual(out["status"], "green")
		self.assertEqual(out["headline"], "Checking")

	def test_stale_answer_is_red(self):
		out = self.tile(
			(True, True, "Production"), rows={"last_success": [{"last_success": NOW - timedelta(days=2)}]}
		)
		self.assertEqual(out["status"], "red")

	def test_last_problem_is_masked(self):
		rows = {
			"last_success": [{"last_success": NOW - timedelta(hours=3)}],
			"last_error": [
				{
					"last_error": "FedEx said: Bearer abcdef0123456789 client_secret=hunter22",
					"last_checked": NOW - timedelta(hours=1),
				}
			],
		}
		out = self.tile((True, True, "Production"), rows=rows)
		blob = " ".join(out["notes"])
		self.assertIn("Last problem", blob)
		self.assertNotIn("abcdef0123456789", blob)
		self.assertNotIn("hunter22", blob)

	def test_an_old_problem_after_a_success_is_not_shown(self):
		rows = {
			"last_success": [{"last_success": NOW - timedelta(hours=1)}],
			"last_error": [{"last_error": "old", "last_checked": NOW - timedelta(hours=5)}],
		}
		out = self.tile((True, True, "Production"), rows=rows)
		self.assertFalse(any("Last problem" in note for note in out["notes"]))


if __name__ == "__main__":
	unittest.main()
