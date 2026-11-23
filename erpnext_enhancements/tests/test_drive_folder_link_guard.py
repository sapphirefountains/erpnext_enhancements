"""Bench-free tests for ``google_drive/folder_link_guard.py``: who may set the Drive folder link
(``custom_drive_folder_id``) on Customer, Project and Opportunity.

Installs a minimal ``frappe`` stub in ``setUpModule`` (execution time, not import time, so the
bench-only suites' ``import frappe`` skip-guards are not fooled) and RUNS the ``validate`` hook
against stand-in documents. Also checks that ``hooks.py`` registers it for all three doctypes and
that the integration's own writers still write with ``frappe.db.set_value`` / ``db_set``, which
run no controller, so the rule never stands in their way.

Run: python -m unittest erpnext_enhancements.tests.test_drive_folder_link_guard
"""

import ast
import importlib
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

FIELD = "custom_drive_folder_id"
HOOK = "erpnext_enhancements.google_drive.folder_link_guard.guard_drive_folder_link"

guard = None
STATE = {}
_saved_modules = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _PermissionError(Exception):
	pass


class _Doc:
	"""The slice of ``frappe.model.document.Document`` the hook touches."""

	def __init__(self, doctype="Project", values=None, before=None, new=False):
		self.doctype = doctype
		self.flags = _Dict()
		self._values = dict(values or {})
		self._before = before
		self._new = new

	def get(self, key, default=None):
		return self._values.get(key, default)

	def set(self, key, value):
		self._values[key] = value

	def is_new(self):
		return self._new

	def get_doc_before_save(self):
		return self._before


def _has_column(doctype, column):
	if doctype.startswith("tab"):
		# Real frappe looks for "tabtab<doctype>" and raises TableMissingError.
		raise RuntimeError(f"has_column takes a doctype, got {doctype!r}")
	STATE["has_column_calls"] += 1
	return column in STATE["columns"].get(doctype, set())


def _throw(msg, exc=Exception, title=None, **kwargs):
	raise exc(msg)


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.flags = _Dict()
	frappe.session = _Dict(user="staff@example.com")
	frappe.PermissionError = _PermissionError
	frappe._ = lambda text, *a, **k: text
	frappe.throw = _throw
	frappe.get_roles = lambda user=None: list(STATE["roles"])
	frappe.db = types.SimpleNamespace(has_column=_has_column)
	return frappe


def setUpModule():
	global guard
	for name in ("frappe", "erpnext_enhancements.google_drive.folder_link_guard"):
		if name in sys.modules:
			_saved_modules[name] = sys.modules.pop(name)
	sys.modules["frappe"] = _install_stubs()
	guard = importlib.import_module("erpnext_enhancements.google_drive.folder_link_guard")


def tearDownModule():
	for name in ("frappe", "erpnext_enhancements.google_drive.folder_link_guard"):
		sys.modules.pop(name, None)
	sys.modules.update(_saved_modules)


def _stored(value, doctype="Project"):
	return _Doc(doctype, {FIELD: value})


class GuardBehaviour(unittest.TestCase):
	def setUp(self):
		frappe = sys.modules["frappe"]
		frappe.flags.clear()
		frappe.session.user = "staff@example.com"
		STATE.clear()
		STATE.update(
			{
				"roles": ["Projects User", "Sales User", "All"],
				"columns": {dt: {FIELD} for dt in ("Customer", "Project", "Opportunity")},
				"has_column_calls": 0,
			}
		)

	def _run(self, doc):
		guard.guard_drive_folder_link(doc, "validate")
		return doc.get(FIELD)

	def test_staff_save_that_changes_the_folder_is_refused(self):
		for doctype in ("Customer", "Project", "Opportunity"):
			with self.subTest(doctype=doctype):
				doc = _Doc(doctype, {FIELD: "folder-other"}, before=_stored("folder-own", doctype))
				with self.assertRaises(_PermissionError):
					self._run(doc)

	def test_staff_save_that_sets_a_folder_on_an_unlinked_record_is_refused(self):
		doc = _Doc("Customer", {FIELD: "folder-other"}, before=_stored(None, "Customer"))
		with self.assertRaises(_PermissionError):
			self._run(doc)

	def test_integration_flag_lets_the_change_through(self):
		doc = _Doc("Project", {FIELD: "folder-new"}, before=_stored("folder-old"))
		doc.flags[guard.DRIVE_FOLDER_FLAG] = True
		self.assertEqual(self._run(doc), "folder-new")

	def test_system_manager_may_change_the_folder(self):
		STATE["roles"] = ["System Manager", "All"]
		doc = _Doc("Opportunity", {FIELD: "folder-new"}, before=_stored("folder-old", "Opportunity"))
		self.assertEqual(self._run(doc), "folder-new")

	def test_administrator_may_change_the_folder(self):
		sys.modules["frappe"].session.user = "Administrator"
		STATE["roles"] = []
		doc = _Doc("Project", {FIELD: "folder-new"}, before=_stored("folder-old"))
		self.assertEqual(self._run(doc), "folder-new")

	def test_unchanged_value_passes_without_a_database_read(self):
		doc = _Doc(
			"Customer", {FIELD: "folder-own", "customer_name": "x"}, before=_stored("folder-own", "Customer")
		)
		self.assertEqual(self._run(doc), "folder-own")
		self.assertEqual(STATE["has_column_calls"], 0)

	def test_none_and_empty_count_as_the_same_value(self):
		doc = _Doc("Customer", {FIELD: ""}, before=_stored(None, "Customer"))
		self.assertEqual(self._run(doc), "")

	def test_stale_form_with_an_empty_value_keeps_the_stored_folder(self):
		doc = _Doc("Customer", {FIELD: None}, before=_stored("folder-own", "Customer"))
		self.assertEqual(self._run(doc), "folder-own")

	def test_missing_key_keeps_the_stored_folder(self):
		doc = _Doc("Project", {}, before=_stored("folder-own"))
		self.assertEqual(self._run(doc), "folder-own")

	def test_system_manager_may_clear_the_folder(self):
		STATE["roles"] = ["System Manager"]
		doc = _Doc("Project", {FIELD: ""}, before=_stored("folder-own"))
		self.assertEqual(self._run(doc), "")

	def test_new_record_from_staff_keeps_no_preset_folder(self):
		for doctype in ("Customer", "Project", "Opportunity"):
			with self.subTest(doctype=doctype):
				doc = _Doc(doctype, {FIELD: "folder-preset"}, new=True)
				self.assertIsNone(self._run(doc))

	def test_new_record_from_system_manager_keeps_its_folder(self):
		STATE["roles"] = ["System Manager"]
		doc = _Doc("Customer", {FIELD: "folder-preset"}, new=True)
		self.assertEqual(self._run(doc), "folder-preset")

	def test_new_record_with_the_integration_flag_keeps_its_folder(self):
		doc = _Doc("Project", {FIELD: "folder-preset"}, new=True)
		doc.flags[guard.DRIVE_FOLDER_FLAG] = True
		self.assertEqual(self._run(doc), "folder-preset")

	def test_new_record_without_a_folder_reads_nothing(self):
		doc = _Doc("Opportunity", {}, new=True)
		self.assertIsNone(self._run(doc))
		self.assertEqual(STATE["has_column_calls"], 0)

	def test_migrate_install_and_patch_pass(self):
		frappe = sys.modules["frappe"]
		for flag in ("in_migrate", "in_install", "in_patch"):
			with self.subTest(flag=flag):
				frappe.flags.clear()
				frappe.flags[flag] = True
				doc = _Doc("Project", {FIELD: "folder-new"}, before=_stored("folder-old"))
				self.assertEqual(self._run(doc), "folder-new")
				new_doc = _Doc("Customer", {FIELD: "folder-preset"}, new=True)
				self.assertEqual(self._run(new_doc), "folder-preset")

	def test_doctype_without_the_column_is_a_quiet_no_op(self):
		STATE["columns"] = {}
		doc = _Doc("Project", {FIELD: "folder-new"}, before=_stored("folder-old"))
		self.assertEqual(self._run(doc), "folder-new")
		new_doc = _Doc("Customer", {FIELD: "folder-preset"}, new=True)
		self.assertEqual(self._run(new_doc), "folder-preset")

	def test_no_doc_before_save_is_treated_as_unlinked(self):
		doc = _Doc("Project", {FIELD: ""}, before=None)
		self.assertEqual(self._run(doc), "")
		doc = _Doc("Project", {FIELD: "folder-x"}, before=None)
		with self.assertRaises(_PermissionError):
			self._run(doc)


def _doc_events():
	tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(
			isinstance(t, ast.Name) and t.id == "doc_events" for t in node.targets
		):
			return ast.literal_eval(node.value)
	raise AssertionError("doc_events not found in hooks.py")


class Wiring(unittest.TestCase):
	def test_registered_as_validate_on_all_three_doctypes(self):
		events = _doc_events()
		for doctype in ("Customer", "Project", "Opportunity"):
			with self.subTest(doctype=doctype):
				validate = events[doctype]["validate"]
				validate = validate if isinstance(validate, list) else [validate]
				self.assertIn(HOOK, validate)

	def test_integration_writers_write_without_running_a_controller(self):
		"""Every integration write of the field is a db-level write. A writer that instead saves
		the document must set ``doc.flags.drive_folder_update`` first, or the rule refuses it."""
		sources = {
			"google_drive/drive_utils.py": 2,
			"google_drive/drive_sync.py": 2,
			"google_drive/drive_link_manager.py": 2,
			"crm_enhancements/api.py": 1,
		}
		write = re.compile(r"(?:db\.set_value|db_set)\(")
		for rel, minimum in sources.items():
			with self.subTest(file=rel):
				text = (APP / rel).read_text(encoding="utf-8")
				self.assertGreaterEqual(len(write.findall(text)), minimum)
				self.assertNotRegex(text, r"\.custom_drive_folder_id\s*=[^=]")
				self.assertNotRegex(text, r"\.set\(\s*[\"']custom_drive_folder_id[\"']")


if __name__ == "__main__":
	unittest.main()
