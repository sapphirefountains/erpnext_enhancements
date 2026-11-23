# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Flow guard (v1.836.0): Frappe Flow and the Studio User role stay off until the site opts in.
Bench-free.

While ERPNext Enhancements Settings ``allow_frappe_flow`` is off (or unset, unreadable, or never
saved), ``ai_governance/flow_guard.py``:

- refuses a new or enabled Flow Provider or Flow Model in an ordinary request, and saves it
  **disabled** instead during ``bench migrate``, an install, a patch, the setup wizard, or before
  setup is complete, where a raise would abort the deploy or the wizard;
- takes a newly added Flow User or Studio User role off a User or Role Profile and never raises: the
  setup wizard gives the first user every role, erpnext gives Administrator every role before tests,
  and an LDAP sign-in syncs roles on login. A role already held is kept, and a signed-in person
  editing in the Desk is told.

Also guarded: ``hooks.py`` wires exactly these four events with no duplicate key at any depth of
``doc_events``, the setting is declared with a default of 0 and a newer ``modified`` stamp, and no
fixture grants the two roles or a permission on a Flow or Studio doctype.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_flow_guard
"""

import ast
import importlib
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


def _reset(setting=None, user="admin@example.com", setup_complete=True):
	"""``setting`` is the stored tabSingles value: None means no row at all."""
	STATE.clear()
	STATE.update(
		{
			"setting": setting,
			"fail_read": False,
			"setup_complete": setup_complete,
			"fail_setup_check": False,
			"fail_msgprint": False,
			"queries": [],
			"warnings": [],
			"thrown": [],
			"messages": [],
		}
	)
	frappe = sys.modules["frappe"]
	for name in ("in_migrate", "in_install", "in_patch", "in_import", "in_test", "in_setup_wizard"):
		setattr(frappe.flags, name, False)
	frappe.session = types.SimpleNamespace(user=user)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.flags = _Flags()
	frappe.PermissionError = _PermissionError

	def sql(query, values=None):
		STATE["queries"].append((query, values))
		if STATE["fail_read"]:
			raise RuntimeError("database gone")
		return [] if STATE["setting"] is None else [(STATE["setting"],)]

	def is_setup_complete():
		if STATE["fail_setup_check"]:
			raise RuntimeError("no Installed Application table")
		return STATE["setup_complete"]

	def throw(message, exc=None, title=None):
		STATE["thrown"].append({"message": message, "exc": exc, "title": title})
		raise (exc or Exception)(message)

	def msgprint(message, title=None, indicator=None):
		if STATE["fail_msgprint"]:
			raise RuntimeError("no request")
		STATE["messages"].append({"message": message, "title": title, "indicator": indicator})

	class _Logger:
		def warning(self, message):
			STATE["warnings"].append(message)

	frappe.db = types.SimpleNamespace(sql=sql)
	frappe.is_setup_complete = is_setup_complete
	frappe.throw = throw
	frappe.msgprint = msgprint
	frappe._ = lambda text: text
	frappe.logger = lambda *a, **k: _Logger()
	frappe.session = types.SimpleNamespace(user="Guest")
	sys.modules["frappe"] = frappe


def setUpModule():
	global guard
	_install_frappe_stub()
	sys.modules.pop(GUARD, None)
	guard = importlib.import_module(GUARD)


class _Row(dict):
	"""A child row: ``get`` like a Document, and a settable ``idx``."""

	def __init__(self, role, idx):
		super().__init__(role=role)
		self.idx = idx


class _Doc:
	"""Just enough of a Document: ``doctype``, ``get``, ``is_new``, ``get_doc_before_save``."""

	def __init__(self, doctype, roles=None, before=None, new=False, enabled=None):
		self.doctype = doctype
		self._new = new
		self._before = before
		self.roles = [_Row(role, idx) for idx, role in enumerate(roles or [], start=1)]
		if roles is None:
			del self.roles
		if enabled is not None:
			self.enabled = enabled

	def get(self, key, default=None):
		return getattr(self, key, default)

	def is_new(self):
		return self._new

	def get_doc_before_save(self):
		return self._before


def _holder(doctype, roles, before_roles=None):
	"""A role holder; ``before_roles`` None means a new record (no stored copy)."""
	before = _Doc(doctype, before_roles) if before_roles is not None else None
	return _Doc(doctype, roles, before, new=before is None)


def _role_names(doc):
	return [row["role"] for row in doc.roles]


class FlowSetupRefusedTest(unittest.TestCase):
	"""An ordinary request: a new or enabled Flow Provider or Flow Model is refused."""

	def test_new_provider_and_model_refused_while_off(self):
		for setting in (None, "0", "", "0.0"):
			for doctype in ("Flow Provider", "Flow Model"):
				_reset(setting)
				with self.assertRaises(_PermissionError, msg=(setting, doctype)):
					guard.guard_flow_setup(_Doc(doctype, new=True, enabled=1))
				self.assertEqual(STATE["thrown"][0]["title"], "Frappe Flow is off on this site")
				self.assertIn("Allow Frappe Flow and Studio User", STATE["thrown"][0]["message"])

	def test_new_disabled_record_refused_too(self):
		# it would still store a vendor account and its key
		_reset(None)
		with self.assertRaises(_PermissionError):
			guard.guard_flow_setup(_Doc("Flow Provider", new=True, enabled=0))

	def test_existing_record_saved_enabled_refused(self):
		# frappe's setup saves an existing provider with enabled = 1 (not an insert)
		_reset(None)
		with self.assertRaises(_PermissionError):
			guard.guard_flow_setup(_Doc("Flow Provider", new=False, enabled=1))

	def test_existing_record_saved_disabled_passes_without_a_read(self):
		_reset(None)
		guard.guard_flow_setup(_Doc("Flow Model", new=False, enabled=0))
		self.assertEqual(STATE["thrown"], [])
		self.assertEqual(STATE["queries"], [])

	def test_allowed_once_switched_on(self):
		for setting in ("1", 1, "1.0"):
			_reset(setting)
			doc = _Doc("Flow Model", new=True, enabled=1)
			guard.guard_flow_setup(doc)
			self.assertEqual(STATE["thrown"], [])
			self.assertEqual(doc.enabled, 1)

	def test_unreadable_setting_counts_as_off(self):
		_reset("1")
		STATE["fail_read"] = True
		with self.assertRaises(_PermissionError):
			guard.guard_flow_setup(_Doc("Flow Model", new=True, enabled=1))

	def test_reads_tab_singles_with_bound_parameters(self):
		# not get_single_value: that throws into message_log when the field is not synced yet
		_reset("1")
		guard.guard_flow_setup(_Doc("Flow Model", new=True, enabled=1))
		((query, values),) = STATE["queries"]
		self.assertIn("`tabSingles`", query)
		self.assertNotIn("order by", query.lower())
		self.assertEqual(values, ("ERPNext Enhancements Settings", "allow_frappe_flow"))

	def test_failed_setup_check_counts_as_complete(self):
		_reset(None)
		STATE["fail_setup_check"] = True
		with self.assertRaises(_PermissionError):
			guard.guard_flow_setup(_Doc("Flow Provider", new=True, enabled=1))

	def test_a_data_import_is_still_refused(self):
		_reset(None)
		sys.modules["frappe"].flags.in_import = True
		with self.assertRaises(_PermissionError):
			guard.guard_flow_setup(_Doc("Flow Provider", new=True, enabled=1))

	def test_other_doctypes_pass(self):
		_reset(None)
		guard.guard_flow_setup(_Doc("Flow Agent", new=True, enabled=1))
		guard.guard_flow_setup(_Doc("Item", new=True))
		self.assertEqual(STATE["thrown"], [])
		self.assertEqual(STATE["queries"], [])


class FlowSetupDisabledTest(unittest.TestCase):
	"""A deploy, the setup wizard, or a site not set up yet: never raise, save the record disabled."""

	def _assert_saved_disabled(self, label):
		doc = _Doc("Flow Model", new=True, enabled=1)
		guard.guard_flow_setup(doc)
		self.assertEqual(STATE["thrown"], [], label)
		self.assertEqual(doc.enabled, 0, label)
		self.assertEqual(len(STATE["warnings"]), 1, label)
		self.assertIn("Flow Model was saved disabled", STATE["warnings"][0])

	def test_deploy_flags(self):
		for flag in ("in_migrate", "in_install", "in_patch"):
			_reset(None)
			setattr(sys.modules["frappe"].flags, flag, True)
			self._assert_saved_disabled(flag)

	def test_setup_wizard(self):
		_reset(None)
		sys.modules["frappe"].flags.in_setup_wizard = True
		self._assert_saved_disabled("in_setup_wizard")

	def test_setup_not_complete(self):
		_reset(None, setup_complete=False)
		self._assert_saved_disabled("setup incomplete")

	def test_existing_enabled_record_disabled_during_a_deploy(self):
		_reset(None)
		sys.modules["frappe"].flags.in_migrate = True
		doc = _Doc("Flow Provider", new=False, enabled=1)
		guard.guard_flow_setup(doc)
		self.assertEqual(doc.enabled, 0)

	def test_left_enabled_when_the_site_opted_in(self):
		_reset("1")
		sys.modules["frappe"].flags.in_migrate = True
		doc = _Doc("Flow Model", new=True, enabled=1)
		guard.guard_flow_setup(doc)
		self.assertEqual(doc.enabled, 1)
		self.assertEqual(STATE["warnings"], [])

	def test_new_disabled_record_left_alone_during_a_deploy(self):
		_reset(None)
		sys.modules["frappe"].flags.in_patch = True
		doc = _Doc("Flow Provider", new=True, enabled=0)
		guard.guard_flow_setup(doc)
		self.assertEqual(doc.enabled, 0)
		self.assertEqual(STATE["warnings"], [])
		self.assertEqual(STATE["thrown"], [])


class RolesDroppedTest(unittest.TestCase):
	"""A Flow User or Studio User role a save adds is taken off; nothing ever raises."""

	def test_new_role_taken_off_others_kept(self):
		for doctype in ("User", "Role Profile"):
			for role in ("Flow User", "Studio User"):
				_reset(None)
				doc = _holder(doctype, ["Employee", role, "Projects User"], ["Employee"])
				guard.drop_new_flow_roles(doc)
				self.assertEqual(STATE["thrown"], [])
				self.assertEqual(_role_names(doc), ["Employee", "Projects User"], (doctype, role))
				self.assertEqual([row.idx for row in doc.roles], [1, 2])

	def test_person_in_the_desk_is_told(self):
		_reset(None, user="admin@example.com")
		guard.drop_new_flow_roles(_holder("User", ["Studio User", "Flow User"], []))
		(message,) = STATE["messages"]
		self.assertIn("Not given: Flow User, Studio User.", message["message"])
		self.assertIn("Allow Frappe Flow and Studio User", message["message"])
		self.assertEqual(message["indicator"], "orange")
		self.assertEqual(len(STATE["warnings"]), 1)

	def test_setup_wizard_first_user_gets_everything_else(self):
		# setup_wizard.create_or_update_user: every role, then System Manager, on a new User
		_reset(None, user="Administrator", setup_complete=False)
		sys.modules["frappe"].flags.in_setup_wizard = True
		doc = _holder("User", ["Accounts User", "Flow User", "Studio User", "System Manager"])
		guard.drop_new_flow_roles(doc)
		self.assertEqual(STATE["thrown"], [])
		self.assertEqual(_role_names(doc), ["Accounts User", "System Manager"])
		self.assertEqual(STATE["messages"], [])

	def test_admin_given_every_role_before_tests(self):
		# erpnext.setup.utils._enable_all_roles_for_admin -> add_all_roles_to("Administrator")
		_reset(None, user="Administrator")
		doc = _holder("User", ["System Manager", "Accounts Manager", "Flow User"], ["System Manager"])
		guard.drop_new_flow_roles(doc)
		self.assertEqual(STATE["thrown"], [])
		self.assertEqual(_role_names(doc), ["System Manager", "Accounts Manager"])

	def test_sign_in_syncing_roles_is_silent(self):
		# an LDAP sign-in adds mapped roles while the session is still Guest
		_reset(None, user="Guest")
		doc = _holder("User", ["Employee", "Flow User"], ["Employee"])
		guard.drop_new_flow_roles(doc)
		self.assertEqual(_role_names(doc), ["Employee"])
		self.assertEqual(STATE["messages"], [])
		self.assertEqual(len(STATE["warnings"]), 1)

	def test_deploy_flags_drop_without_a_message(self):
		for flag in ("in_migrate", "in_install", "in_patch"):
			_reset(None)
			setattr(sys.modules["frappe"].flags, flag, True)
			doc = _holder("Role Profile", ["Flow User"], [])
			guard.drop_new_flow_roles(doc)
			self.assertEqual(_role_names(doc), [], flag)
			self.assertEqual(STATE["messages"], [], flag)
			self.assertEqual(len(STATE["warnings"]), 1, flag)

	def test_a_failing_message_still_does_not_raise(self):
		_reset(None)
		STATE["fail_msgprint"] = True
		doc = _holder("User", ["Flow User"], [])
		guard.drop_new_flow_roles(doc)
		self.assertEqual(_role_names(doc), [])

	def test_role_already_held_is_kept(self):
		_reset(None)
		doc = _holder("User", ["Flow User", "Projects User"], ["Flow User"])
		guard.drop_new_flow_roles(doc)
		self.assertEqual(_role_names(doc), ["Flow User", "Projects User"])
		self.assertEqual(STATE["queries"], [])
		self.assertEqual(STATE["messages"], [])

	def test_other_roles_never_read_the_setting(self):
		_reset(None)
		guard.drop_new_flow_roles(_holder("User", ["System Manager", "Employee"], []))
		guard.drop_new_flow_roles(_holder("Role Profile", ["Projects Manager"]))
		self.assertEqual(STATE["queries"], [])
		self.assertEqual(STATE["warnings"], [])

	def test_kept_once_switched_on(self):
		_reset("1")
		doc = _holder("User", ["Flow User", "Studio User"], [])
		guard.drop_new_flow_roles(doc)
		self.assertEqual(_role_names(doc), ["Flow User", "Studio User"])
		self.assertEqual(STATE["warnings"], [])

	def test_other_doctypes_ignored(self):
		_reset(None)
		doc = _holder("Workspace", ["Flow User"], [])
		guard.drop_new_flow_roles(doc)
		self.assertEqual(_role_names(doc), ["Flow User"])

	def test_record_without_a_roles_table(self):
		_reset(None)
		guard.drop_new_flow_roles(_Doc("User"))
		self.assertEqual(STATE["warnings"], [])


def _doc_events_node():
	tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "doc_events":
			return node.value
	raise AssertionError("doc_events not found in hooks.py")


def _duplicate_keys(node, path=()):
	"""Every dict key written twice at any depth: in a literal the later one silently wins."""
	found = []
	if isinstance(node, ast.Dict):
		keys = [ast.literal_eval(key) for key in node.keys]
		found += [(path, key) for key in set(keys) if keys.count(key) > 1]
		for key, value in zip(keys, node.values, strict=True):
			found += _duplicate_keys(value, (*path, key))
	elif isinstance(node, (ast.List, ast.Tuple)):
		for value in node.elts:
			found += _duplicate_keys(value, path)
	return found


def _doc_events():
	return ast.literal_eval(_doc_events_node())


def _as_list(value):
	return value if isinstance(value, list) else [value]


class WiringTest(unittest.TestCase):
	def test_no_duplicate_key_at_any_depth(self):
		self.assertEqual(_duplicate_keys(_doc_events_node()), [])

	def test_duplicate_key_finder_sees_a_nested_one(self):
		node = ast.parse('{"User": {"validate": "a", "on_update": "b", "validate": "c"}}', mode="eval")
		self.assertEqual(_duplicate_keys(node.body), [(("User",), "validate")])

	def test_hooks_wire_the_guard(self):
		events = _doc_events()
		setup = f"{GUARD}.guard_flow_setup"
		roles = f"{GUARD}.drop_new_flow_roles"
		self.assertIn(setup, _as_list(events["Flow Provider"]["validate"]))
		self.assertIn(setup, _as_list(events["Flow Model"]["validate"]))
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
				("Flow Model", "validate"),
				("Flow Provider", "validate"),
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
		order = meta["field_order"]
		self.assertLess(order.index("ai_governance_section"), order.index(guard.SETTING_FIELD))
		self.assertLess(order.index(guard.SETTING_FIELD), order.index("ai_governance_column_break"))

	def test_settings_modified_moved_forward(self):
		# the DocType importer skips a file whose modified is not newer than the site's row
		meta = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
		self.assertGreaterEqual(meta["modified"], "2026-11-23 12:00:00.000000")

	def test_no_fixture_names_a_guarded_role(self):
		# a fixture that did would be dropped quietly during migrate; keep it out of the repo
		for path in sorted((APP / "fixtures").glob("*.json")):
			text = path.read_text(encoding="utf-8")
			for role in sorted(guard.GUARDED_ROLES):
				self.assertNotIn(f'"{role}"', text, f"{path.name} names {role}")

	def test_no_fixture_grants_a_permission_on_flow_or_studio(self):
		# the guard matches role names, not permissions: a Custom DocPerm would get round it
		path = APP / "fixtures" / "custom_docperm.json"
		rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
		offending = [
			(row.get("parent"), row.get("role"))
			for row in rows
			if str(row.get("parent") or "").startswith(("Flow ", "Studio "))
		]
		self.assertEqual(offending, [])


if __name__ == "__main__":
	unittest.main()
