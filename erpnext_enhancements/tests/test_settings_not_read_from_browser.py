# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The browser never asks the server for a settings Single.

Nik, 2026-10-12: "whenever someone tries to search, they get a warning or error saying they don't
have permissions for ERPNext Enhancements". Opening the Maintenance Record, Maintenance Section and
Maintenance Contract forms, the Sales Order form and the Fountain Move Request list's store picker
each asked for one value of the settings with ``frappe.db.get_single_value`` or
``frappe.client.get``. Both refuse a user without read on the settings, a System Manager doctype,
with "No permission for ERPNext Enhancements Settings" (``frappe/client.py``, v16.50). So everybody
else saw that dialog each time one of those opened, which is most often straight after a search.

The values now arrive in the boot payload (``boot._settings_for_forms``). This pins both halves: no
desk script asks the server for the settings, and boot still ships what the forms read.

**Every Single, not only that one** (v1.592.1, Nik: "fix the QBO settings pop up too"). The
QuickBooks Online dashboard's Connect button read QuickBooks Online Settings the same way, only to
pass the value straight back to ``start_oauth``. The refusal is the same for any Single, so the
scan refuses any ``get_single_value`` in a desk script, and any ``frappe.client.get*``,
``get_value``, ``with_doc`` or ``get_doc`` call that names a ``... Settings`` doctype.

Bench-free: source text and ``ast`` only.

Run: python -m unittest erpnext_enhancements.tests.test_settings_not_read_from_browser
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

#: ``frappe.db.get_single_value`` reads a Single by definition, whichever one it names.
SINGLE_READ = re.compile(r"\bget_single_value\s*\(")

#: Any other browser request naming a ``... Settings`` doctype within the next few lines:
#: ``frappe.db.get_value`` / ``frappe.db.get_doc`` / ``frappe.model.with_doc``, or a
#: ``frappe.call`` to a ``frappe.client.get*`` method. Only calls that reach the server: a
#: ``frappe.model.get_doc`` or ``frappe.get_doc`` reads the browser's own locals and is refused
#: by nobody (``project_enhancements.js`` reads Print Settings that way, and that is fine).
SETTINGS_READ = re.compile(
	r"(?:(?:db\s*\.\s*get_value|db\s*\.\s*get_doc|with_doc)\s*\(|[\"']frappe\.client\.get\w*[\"'])"
	r"[^;]{0,200}?[\"'][A-Z][\w ]*Settings[\"']",
	re.DOTALL,
)


def _reads(code):
	return bool(SINGLE_READ.search(code) or SETTINGS_READ.search(code))


def _scripts():
	for path in APP.rglob("*.js"):
		if "node_modules" in path.parts or path.name.endswith(".min.js") or "dist" in path.parts:
			continue
		yield path


def _code(src):
	"""JS with ``//`` and ``/* */`` comments removed: the scripts that used to do this now say so in
	a comment, which names the call."""
	src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
	return re.sub(r"(^|[^:\"'])//[^\n]*", r"\1", src)


class TestNoDeskScriptReadsASettingsSingle(unittest.TestCase):
	def test_the_scan_finds_scripts(self):
		self.assertGreater(len(list(_scripts())), 100)

	def test_no_script_asks_the_server_for_a_settings_single(self):
		offenders = [
			str(path.relative_to(APP))
			for path in _scripts()
			if _reads(_code(path.read_text(encoding="utf-8", errors="replace")))
		]
		self.assertEqual(
			offenders,
			[],
			"These read a settings Single from the browser, which shows anyone without read on it "
			'"No permission for ... Settings". Ship the value in boot.py, or have the endpoint '
			"that needs it read it on the server.",
		)

	def test_the_check_would_catch_the_old_calls(self):
		"""The patterns must match the shapes they replaced, or the test above proves nothing."""
		for old in (
			'frappe.db\n\t.get_single_value("ERPNext Enhancements Settings", "water_feature_item")',
			'frappe.call({\n\tmethod: "frappe.client.get",\n\targs: { doctype: "ERPNext Enhancements Settings" },',
			'frappe.db.get_single_value("QuickBooks Online Settings", "environment").then((environment) => {',
			'frappe.db.get_value("Training Settings", "Training Settings", "gcs_bucket")',
		):
			with self.subTest(old=old[:40]):
				self.assertTrue(_reads(_code(old)))

	def test_the_check_leaves_ordinary_reads_alone(self):
		for fine in (
			'frappe.db.get_value("Item", item, "item_name")',
			'frappe.set_route("Form", "Training Settings")',
			'frappe.model.get_doc(":Print Settings", "Print Settings") || {}',
			"// frappe.db.get_single_value used to be here",
		):
			with self.subTest(fine=fine[:40]):
				self.assertFalse(_reads(_code(fine)))

	def test_the_qbo_dashboard_lets_the_server_pick_the_environment(self):
		"""``start_oauth`` with no environment connects to the one stored on the Settings."""
		js = (
			APP / "quickbooks_online/page/quickbooks_online_dashboard/quickbooks_online_dashboard.js"
		).read_text(encoding="utf-8")
		at = js.index("function connectQuickBooks()")
		body = _code(js[at : js.index("function disconnectQuickBooks()")])
		self.assertIn("core.api.start_oauth", body)
		self.assertNotIn("environment", body)


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
