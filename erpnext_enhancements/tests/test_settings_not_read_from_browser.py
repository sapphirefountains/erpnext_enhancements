# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The browser never asks for ERPNext Enhancements Settings.

Nik, 2026-10-12: "whenever someone tries to search, they get a warning or error saying they don't
have permissions for ERPNext Enhancements". Opening the Maintenance Record, Maintenance Section and
Maintenance Contract forms, the Sales Order form and the Fountain Move Request list's store picker
each asked for one value of the settings with ``frappe.db.get_single_value`` or
``frappe.client.get``. Both refuse a user without read on the settings, a System Manager doctype,
with "No permission for ERPNext Enhancements Settings" (``frappe/client.py``, v16.50). So everybody
else saw that dialog each time one of those opened, which is most often straight after a search.

The values now arrive in the boot payload (``boot._settings_for_forms``). This pins both halves: no
desk script asks the server for the settings, and boot still ships what the forms read.

Bench-free: source text and ``ast`` only.

Run: python -m unittest erpnext_enhancements.tests.test_settings_not_read_from_browser
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SETTINGS = "ERPNext Enhancements Settings"

#: A browser read of the settings: a ``frappe.db``/``frappe.model`` call, or a ``frappe.call`` to a
#: ``frappe.client.get*`` method, naming the doctype within the next few lines.
BROWSER_READ = re.compile(
	r"(?:(?:get_single_value|get_value|with_doc|get_doc)\s*\(|[\"']frappe\.client\.get\w*[\"'])"
	r"[^;]{0,200}?" + re.escape(SETTINGS),
	re.DOTALL,
)


def _scripts():
	for path in APP.rglob("*.js"):
		if "node_modules" in path.parts or path.name.endswith(".min.js") or "dist" in path.parts:
			continue
		yield path


def _code(src):
	"""JS with comments and string-free noise left in, but `//` and `/* */` comments removed:
	the scripts that used to do this now say so in a comment, which names the call."""
	src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
	return re.sub(r"(^|[^:\"'])//[^\n]*", r"\1", src)


class TestNoDeskScriptReadsTheSettings(unittest.TestCase):
	def test_the_scan_finds_scripts(self):
		self.assertGreater(len(list(_scripts())), 100)

	def test_no_script_asks_the_server_for_the_settings(self):
		offenders = []
		for path in _scripts():
			src = path.read_text(encoding="utf-8", errors="replace")
			if SETTINGS not in src:
				continue
			if BROWSER_READ.search(_code(src)):
				offenders.append(str(path.relative_to(APP)))
		self.assertEqual(
			offenders,
			[],
			"These read ERPNext Enhancements Settings from the browser, which shows everyone but a "
			"System Manager a permission error. Ship the value in boot.py instead.",
		)

	def test_the_check_would_catch_the_old_calls(self):
		"""The regex must match the shapes it replaced, or the test above proves nothing."""
		for old in (
			'frappe.db\n\t.get_single_value("ERPNext Enhancements Settings", "water_feature_item")',
			'frappe.call({\n\tmethod: "frappe.client.get",\n\targs: { doctype: "ERPNext Enhancements Settings" },',
		):
			with self.subTest(old=old[:30]):
				self.assertTrue(BROWSER_READ.search(old))


class TestBootShipsWhatTheFormsRead(unittest.TestCase):
	BOOT = APP / "boot.py"

	def test_boot_sets_the_payload(self):
		tree = ast.parse(self.BOOT.read_text(encoding="utf-8"))
		assigned = {
			node.targets[0].attr
			for node in ast.walk(tree)
			if isinstance(node, ast.Assign)
			and isinstance(node.targets[0], ast.Attribute)
			and isinstance(node.targets[0].value, ast.Name)
			and node.targets[0].value.id == "bootinfo"
		}
		self.assertIn("ee_item_pickers", assigned)
		self.assertIn("ee_fountain_move_locations", assigned)

	def test_every_key_a_form_reads_is_shipped(self):
		boot = self.BOOT.read_text(encoding="utf-8")
		for path in _scripts():
			src = path.read_text(encoding="utf-8", errors="replace")
			for key in re.findall(r"frappe\.boot\.ee_item_pickers \|\| \{\}\)\.(\w+)", src):
				with self.subTest(script=path.name, key=key):
					self.assertIn(f'"{key}"', boot)

	def test_reading_the_settings_cannot_break_boot(self):
		tree = ast.parse(self.BOOT.read_text(encoding="utf-8"))
		fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_settings_for_forms")
		self.assertTrue(any(isinstance(n, ast.Try) for n in ast.walk(fn)))


if __name__ == "__main__":
	unittest.main()
