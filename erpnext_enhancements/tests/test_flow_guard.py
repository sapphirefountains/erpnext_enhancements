# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Flow guard (v1.836.0): Frappe Flow and the Studio User role stay off until the site opts in.
Bench-free.

While ERPNext Enhancements Settings ``allow_frappe_flow`` is off (or unset, unreadable, or not yet
a column), ``ai_governance/flow_guard.py`` refuses creating a Flow Provider or a Flow Model, and
refuses giving a User or a Role Profile the Flow User or Studio User role. What is worth guarding:

- the refusals happen, and only while the switch is off;
- a record that already holds a guarded role saves normally (only an addition is refused), and
  other roles never read the setting at all;
- nothing raises during ``bench migrate``, an install or a patch, because a raise there aborts the
  deploy;
- ``hooks.py`` wires exactly these events, and the setting is declared on the settings DocType with
  a default of 0 and a ``modified`` stamp newer than the last one (the importer skips older files).

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_flow_guard
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
HOOKS = APP / "hooks.py"
SETTINGS_JSON = (
	APP
	/ "enhancements_core"
	/ "doctype"
	/ "erpnext_enhancements_settings"
	/ "erpnext_enhancements_settings.json"
)
GUARD = "erpnext_enhancements.ai_governance.flow_guard"

STATE = {}
guard = None


class _Flags:
	pass


class _PermissionError(Exception):
	pass


def _reset(setting=None):
	STATE.clear()
	STATE.update({"setting": setting, "fail_read": False, "reads": [], "warnings": [], "thrown": []})
	flags = sys.modules["frappe"].flags
	for name in ("in_migrate", "in_install", "in_patch", "in_import", "in_test"):
		setattr(flags, name, False)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.flags = _Flags()
	frappe.PermissionError = _PermissionError

	def get_single_value(doctype, field):
		STATE["reads"].append((doctype, field))
		if STATE["fail_read"]:
			raise RuntimeError("Unknown column")
		return STATE["setting"]

	def throw(message, exc=None, title=None):
		STATE["thrown"].append({"message": message, "exc": exc, "title": title})
		raise (exc or Exception)(message)

	class _Logger:
		def warning(self, message):
			STATE["warnings"].append(message)

	frappe.db = types.SimpleNamespace(get_single_value=get_single_value)
	frappe.throw = throw
	frappe._ = lambda text: text
	frappe.logger = lambda *a, **k: _Logger()
	sys.modules["frappe"] = frappe


def setUpModule():
	global guard
	_install_frappe_stub()
	sys.modules.pop(GUARD, None)
	import importlib

	guard = importlib.import_module(GUARD)


class _Doc:
	"""Just enough of a Document: ``doctype``, ``get``, ``get_doc_before_save``."""

	def __init__(self, doctype, roles=(), before=None):
		self.doctype = doctype
		self._values = {"roles": [{"role": role} for role in roles]}
		self._before = before

	def get(self, key, default=None):
		return self._values.get(key, default)

	def get_doc_before_save(self):
		return self._before


def _holder(doctype, roles, before_roles=None):
	before = _Doc(doctype, before_roles) if before_roles is not None else None
	return _Doc(doctype, roles, before)


class FlowSetupTest(unittest.TestCase):
	def test_provider_and_model_refused_while_off(self):
		for setting in (None, 0, "0", ""):
			for doctype in ("Flow Provider", "Flow Model"):
				_reset(setting)
				with self.assertRaises(_PermissionError, msg=(setting, doctype)):
					guard.refuse_flow_setup(_Doc(doctype))
				self.assertEqual(STATE["thrown"][0]["title"], "Frappe Flow is off on this site")
				self.assertIn("Allow Frappe Flow and Studio User", STATE["thrown"][0]["message"])

	def test_allowed_once_switched_on(self):
		for setting in (1, "1"):
			_reset(setting)
			guard.refuse_flow_setup(_Doc("Flow Provider"))
			guard.refuse_flow_setup(_Doc("Flow Model"))
			self.assertEqual(STATE["thrown"], [])

	def test_unreadable_setting_counts_as_off(self):
		# a missing column (fresh install, before model sync) or a failed read must not open Flow
		_reset(1)
		STATE["fail_read"] = True
		with self.assertRaises(_PermissionError):
			guard.refuse_flow_setup(_Doc("Flow Model"))

	def test_reads_the_setting_through_get_single_value(self):
		_reset(1)
		guard.refuse_flow_setup(_Doc("Flow Model"))
		self.assertEqual(STATE["reads"], [("ERPNext Enhancements Settings", "allow_frappe_flow")])

	def test_other_doctypes_pass(self):
		_reset(None)
		guard.refuse_flow_setup(_Doc("Flow Agent"))
		guard.refuse_flow_setup(_Doc("Item"))
		self.assertEqual(STATE["thrown"], [])
		self.assertEqual(STATE["reads"], [])

	def test_never_raises_during_a_deploy(self):
		for flag in ("in_migrate", "in_install", "in_patch"):
			_reset(None)
			setattr(sys.modules["frappe"].flags, flag, True)
			guard.refuse_flow_setup(_Doc("Flow Model"))
			self.assertEqual(STATE["thrown"], [], flag)
			self.assertEqual(len(STATE["warnings"]), 1, flag)
			self.assertIn("Flow Model", STATE["warnings"][0])

	def test_a_data_import_is_still_refused(self):
		_reset(None)
		sys.modules["frappe"].flags.in_import = True
		with self.assertRaises(_PermissionError):
			guard.refuse_flow_setup(_Doc("Flow Provider"))


class RoleGrantTest(unittest.TestCase):
	def test_new_flow_or_studio_role_refused_while_off(self):
		for doctype in ("User", "Role Profile"):
			for role in ("Flow User", "Studio User"):
				_reset(None)
				with self.assertRaises(_PermissionError, msg=(doctype, role)):
					guard.refuse_new_flow_roles(_holder(doctype, ["Employee", role], ["Employee"]))
				self.assertIn(role, STATE["thrown"][0]["message"])

	def test_new_record_with_a_guarded_role_refused(self):
		_reset(None)
		with self.assertRaises(_PermissionError):
			guard.refuse_new_flow_roles(_holder("User", ["Studio User"]))

	def test_both_roles_named_once(self):
		_reset(None)
		with self.assertRaises(_PermissionError):
			guard.refuse_new_flow_roles(_holder("User", ["Studio User", "Flow User"], []))
		self.assertIn("Flow User, Studio User", STATE["thrown"][0]["message"])

	def test_role_already_held_saves(self):
		# switched off after a grant: the holder's other edits must not be blocked
		_reset(None)
		guard.refuse_new_flow_roles(_holder("User", ["Flow User", "Projects User"], ["Flow User"]))
		self.assertEqual(STATE["thrown"], [])
		self.assertEqual(STATE["reads"], [])

	def test_other_roles_never_read_the_setting(self):
		_reset(None)
		guard.refuse_new_flow_roles(_holder("User", ["System Manager", "Employee"], []))
		guard.refuse_new_flow_roles(_holder("Role Profile", ["Projects Manager"]))
		self.assertEqual(STATE["thrown"], [])
		self.assertEqual(STATE["reads"], [])

	def test_allowed_once_switched_on(self):
		_reset(1)
		guard.refuse_new_flow_roles(_holder("User", ["Flow User", "Studio User"], []))
		guard.refuse_new_flow_roles(_holder("Role Profile", ["Flow User"], []))
		self.assertEqual(STATE["thrown"], [])

	def test_never_raises_during_a_deploy(self):
		for flag in ("in_migrate", "in_install", "in_patch"):
			_reset(None)
			setattr(sys.modules["frappe"].flags, flag, True)
			guard.refuse_new_flow_roles(_holder("User", ["Flow User"], []))
			self.assertEqual(STATE["thrown"], [], flag)
			self.assertEqual(len(STATE["warnings"]), 1, flag)

	def test_other_doctypes_pass(self):
		_reset(None)
		guard.refuse_new_flow_roles(_holder("Workspace", ["Flow User"], []))
		self.assertEqual(STATE["thrown"], [])

	def test_record_without_a_roles_table(self):
		_reset(None)
		doc = _Doc("User")
		doc._values = {}
		guard.refuse_new_flow_roles(doc)
		self.assertEqual(STATE["thrown"], [])


def _doc_events():
	tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "doc_events":
			keys = [ast.literal_eval(key) for key in node.value.keys]
			assert len(keys) == len(set(keys)), "doc_events has a duplicate key, and the later one wins"
			return ast.literal_eval(node.value)
	raise AssertionError("doc_events not found in hooks.py")


def _as_list(value):
	return value if isinstance(value, list) else [value]


class WiringTest(unittest.TestCase):
	def test_hooks_wire_the_guard(self):
		events = _doc_events()
		setup = f"{GUARD}.refuse_flow_setup"
		roles = f"{GUARD}.refuse_new_flow_roles"
		self.assertIn(setup, _as_list(events["Flow Provider"]["before_insert"]))
		self.assertIn(setup, _as_list(events["Flow Model"]["before_insert"]))
		self.assertIn(roles, _as_list(events["User"]["validate"]))
		self.assertIn(roles, _as_list(events["Role Profile"]["validate"]))

	def test_no_other_event_calls_the_guard(self):
		wired = sorted(
			(doctype, event)
			for doctype, handlers in _doc_events().items()
			for event, paths in handlers.items()
			for path in _as_list(paths)
			if path.startswith(GUARD)
		)
		self.assertEqual(
			wired,
			[
				("Flow Model", "before_insert"),
				("Flow Provider", "before_insert"),
				("Role Profile", "validate"),
				("User", "validate"),
			],
		)

	def test_the_guarded_names_match_the_constants(self):
		self.assertEqual(set(guard.SETUP_DOCTYPES), {"Flow Provider", "Flow Model"})
		self.assertEqual(set(guard.ROLE_HOLDERS), {"User", "Role Profile"})
		self.assertEqual(guard.GUARDED_ROLES, {"Flow User", "Studio User"})

	def test_setting_declared_off_by_default(self):
		meta = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		fields = {field["fieldname"]: field for field in meta["fields"]}
		field = fields[guard.SETTING_FIELD]
		self.assertEqual(field["fieldtype"], "Check")
		self.assertEqual(field["default"], "0")
		self.assertEqual(field["label"], "Allow Frappe Flow and Studio User")
		self.assertIn(guard.SETTING_FIELD, meta["field_order"])
		# placed in the AI Governance section, before its column break
		order = meta["field_order"]
		self.assertLess(order.index("ai_governance_section"), order.index(guard.SETTING_FIELD))
		self.assertLess(order.index(guard.SETTING_FIELD), order.index("ai_governance_column_break"))

	def test_settings_modified_moved_forward(self):
		# the DocType importer skips a file whose modified is not newer than the site's row
		meta = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		self.assertGreaterEqual(meta["modified"], "2026-11-23 12:00:00.000000")

	def test_no_fixture_role_profile_carries_a_guarded_role(self):
		# a fixture that did would be skipped silently by the guard during migrate; keep it out
		for path in sorted((APP / "fixtures").glob("*.json")):
			text = path.read_text(encoding="utf-8")
			for role in ("Flow User", "Studio User"):
				self.assertNotIn(f'"{role}"', text, f"{path.name} names {role}")


if __name__ == "__main__":
	unittest.main()
