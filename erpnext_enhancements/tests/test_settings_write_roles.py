# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who may change a ``... Settings`` Single. Bench-free, pure filesystem plus a stubbed ``frappe``.

A settings page is configuration for the whole company, so the roles that can write it are
a deliberate list. This suite pins that list: for every single DocType in the app whose name
ends in "Settings", the roles with ``write`` at permlevel 0 must be inside ``REVIEWED_WRITERS``
below. The table records today's state; a new writer role, or a new settings DocType, fails
the build until the table is edited on purpose.

It also covers the ERPNext Enhancements Settings controller's ``require_system_manager``
check, which holds the same rule if a permission row is ever added back from the Desk.
"""

import json
import sys
import types
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]  # the erpnext_enhancements/ package

# System Manager may write every settings page; the other roles are listed per DocType exactly
# as they stand today.
REVIEWED_WRITERS = {
	"Accounting Intake Settings": {"Accounts Manager", "System Manager"},
	"Call Routing Settings": {"System Manager"},
	"Triton Assistant Settings": {"System Manager"},
	"Triton Settings": {"System Manager"},
	"Rental Settings": {"Operations Team", "System Manager"},
	"Carrier Tracking Settings": {"System Manager"},
	"Sales Activity Settings": {"System Manager"},
	"Device Compliance Settings": {"System Manager"},
	"ERPNext Enhancements Settings": {"System Manager"},
	"Project Folder Google Drive Settings": {"System Manager"},
	"GA4 Settings": {"System Manager"},
	"Inventory Scanner Settings": {"Stock Manager", "System Manager"},
	"Marketing Settings": {"System Manager"},
	"MDM Settings": {"System Manager"},
	"Offsite Backup Settings": {"System Manager"},
	"Plaid Banking Settings": {"System Manager"},
	"Product Feedback Settings": {"System Manager"},
	"Calendar Sync Settings": {"System Manager"},
	"Project Dashboard Settings": {"System Manager"},
	"Project Planner Settings": {"Projects Manager", "System Manager"},
	"Quality Settings": {"Quality Manager", "System Manager"},
	"QuickBooks Online Settings": {"System Manager"},
	"Stripe Payments Settings": {"System Manager"},
	"Training Settings": {"System Manager", "Training Manager"},
	"Travel Settings": {"System Manager", "Travel Coordinator"},
	"Water Engineering Settings": {"System Manager", "Water Engineer"},
	"Time Kiosk Settings": {"HR Manager", "System Manager"},
}

SETTINGS_DIR = APP_DIR / "enhancements_core" / "doctype" / "erpnext_enhancements_settings"


def _settings_doctypes():
	found = {}
	for path in APP_DIR.glob("**/doctype/*/*.json"):
		try:
			data = json.loads(path.read_text(encoding="utf-8"))
		except (OSError, ValueError):
			continue
		if not isinstance(data, dict) or data.get("doctype") != "DocType":
			continue
		if data.get("issingle") and str(data.get("name", "")).endswith("Settings"):
			found[data["name"]] = data
	return found


def _writers(doctype_json):
	return {
		row.get("role")
		for row in doctype_json.get("permissions", [])
		if row.get("write") and not row.get("permlevel")
	}


class TestSettingsWriteRoles(unittest.TestCase):
	def test_every_settings_single_is_reviewed(self):
		found = _settings_doctypes()
		self.assertTrue(found, "no settings Singles found; the glob is wrong")
		unreviewed = sorted(set(found) - set(REVIEWED_WRITERS))
		self.assertEqual(
			unreviewed,
			[],
			"new settings Single(s) need an entry in REVIEWED_WRITERS: %s" % unreviewed,
		)

	def test_writer_roles_stay_within_the_reviewed_list(self):
		for name, data in _settings_doctypes().items():
			allowed = REVIEWED_WRITERS.get(name, set())
			extra = _writers(data) - allowed
			self.assertEqual(
				extra,
				set(),
				"%s grants write to %s; add the role to REVIEWED_WRITERS deliberately" % (name, sorted(extra)),
			)

	def test_system_manager_can_write_every_settings_page(self):
		for name, data in _settings_doctypes().items():
			self.assertIn("System Manager", _writers(data), name)

	def test_enhancements_settings_is_read_only_for_system_user(self):
		data = json.loads((SETTINGS_DIR / "erpnext_enhancements_settings.json").read_text(encoding="utf-8"))
		rows = [r for r in data["permissions"] if r.get("role") == "System User"]
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row.get("read"), 1)
		for right in ("write", "create", "delete", "share"):
			self.assertFalse(row.get(right), right)
		self.assertEqual(_writers(data), {"System Manager"})


class _Throw(Exception):
	pass


def _load_controller():
	"""Import the controller under a stub ``frappe`` and return (module, stub)."""
	frappe = types.ModuleType("frappe")
	frappe.local = types.SimpleNamespace(request=object())
	frappe.flags = types.SimpleNamespace()
	frappe.session = types.SimpleNamespace(user="staff@example.com")
	frappe.roles = ["System User"]
	frappe.get_roles = lambda: list(frappe.roles)
	frappe.PermissionError = _Throw
	frappe._ = lambda text: text

	def throw(message, exc=_Throw, **kwargs):
		raise exc(message)

	frappe.throw = throw

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value: int(value or 0)

	saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.model", "frappe.model.document", "frappe.utils")}
	sys.modules.update(
		{"frappe": frappe, "frappe.model": model, "frappe.model.document": document, "frappe.utils": utils}
	)
	frappe.model = model
	frappe.utils = utils
	try:
		import importlib.util

		spec = importlib.util.spec_from_file_location(
			"_enh_settings_controller", SETTINGS_DIR / "erpnext_enhancements_settings.py"
		)
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
	finally:
		for key, value in saved.items():
			if value is None:
				sys.modules.pop(key, None)
			else:
				sys.modules[key] = value
	return module, frappe


class TestRequireSystemManager(unittest.TestCase):
	def setUp(self):
		self.module, self.frappe = _load_controller()
		self.doc = self.module.ERPNextEnhancementsSettings()

	def test_staff_request_is_refused(self):
		with self.assertRaises(_Throw):
			self.doc.require_system_manager()

	def test_system_manager_request_passes(self):
		self.frappe.roles = ["System User", "System Manager"]
		self.doc.require_system_manager()

	def test_administrator_passes(self):
		self.frappe.session.user = "Administrator"
		self.doc.require_system_manager()

	def test_no_request_passes(self):
		self.frappe.local.request = None
		self.doc.require_system_manager()

	def test_patch_and_install_flags_pass(self):
		self.frappe.flags.in_patch = True
		self.doc.require_system_manager()
		self.frappe.flags.in_patch = False
		self.frappe.flags.in_install = True
		self.doc.require_system_manager()

	def test_validate_runs_the_check_first(self):
		source = (SETTINGS_DIR / "erpnext_enhancements_settings.py").read_text(encoding="utf-8")
		body = source.split("def validate(self):")[1].split("def ")[0]
		self.assertTrue(body.strip().startswith("self.require_system_manager()"))


if __name__ == "__main__":
	unittest.main()
