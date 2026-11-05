# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Who may change User records: the v1.797.1 ``remove_production_team_user_docperm`` patch, and a
guard on the ``Custom DocPerm`` fixture. Bench-free.

A Desk-added ``Custom DocPerm`` row gave Production Team read, write, create and delete on User.
The patch deletes Production Team's rows on User. What is worth guarding is what it must not touch:
other roles' rows on User (System Manager's above all), and Production Team's rows on other
doctypes. It removes rows the way Role Permissions Manager does (``delete_doc`` with ``force``), so
the change leaves a Deleted Document and a Permission Log. It does nothing when the row is already
gone, and it never raises, because a patch that raises aborts ``bench migrate``, which is the
deploy.

The fixture guard covers the repo only. It fails the build if ``custom_docperm.json`` ever grants a
role other than System Manager anything on User beyond Select and Export, or anything at all above
permlevel 0. It cannot see a row added later through the Desk, which is how this one arrived.

Installs its own ``frappe`` stub in ``setUpModule``, which is why it has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_user_docperm_guard
"""

import json
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PATCHES_TXT = APP / "patches.txt"
CUSTOM_DOCPERM_FIXTURE = APP / "fixtures" / "custom_docperm.json"

# What a role other than System Manager may never hold on User: anything that changes an account,
# and read, which shows every person's phone, birth date and last sign-in.
FORBIDDEN_ON_USER = (
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"import",
	"share",
	"impersonate",
)

STATE = {}
patch = None


def _reset():
	STATE.clear()
	STATE.update(
		{
			"rows": [],  # Custom DocPerm rows: {"name", "parent", "role", "permlevel"}
			"get_all": [],
			"deleted": [],
			"delete_kwargs": [],
			"clear_cache": [],
			"errors": [],
			"fail_get_all": False,
			"fail_delete": set(),
		}
	)


def _row(name, parent, role, permlevel=0):
	STATE["rows"].append({"name": name, "parent": parent, "role": role, "permlevel": permlevel})


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")

	def get_all(doctype, filters=None, pluck=None, **kwargs):
		if STATE["fail_get_all"]:
			raise RuntimeError("database gone")
		assert doctype == "Custom DocPerm", doctype
		assert pluck == "name", pluck
		STATE["get_all"].append(dict(filters))
		return [
			row["name"]
			for row in STATE["rows"]
			if all(row.get(key) == value for key, value in filters.items())
		]

	def delete_doc(doctype, name, **kwargs):
		if name in STATE["fail_delete"]:
			raise RuntimeError("delete refused")
		assert doctype == "Custom DocPerm", doctype
		STATE["deleted"].append(name)
		STATE["delete_kwargs"].append(kwargs)
		STATE["rows"] = [row for row in STATE["rows"] if row["name"] != name]

	def clear_cache(*args, **kwargs):
		STATE["clear_cache"].append(kwargs)

	frappe.get_all = get_all
	frappe.delete_doc = delete_doc
	frappe.clear_cache = clear_cache
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title") or a)
	sys.modules["frappe"] = frappe


def setUpModule():
	global patch
	_install_frappe_stub()
	sys.modules.pop("erpnext_enhancements.patches.remove_production_team_user_docperm", None)
	from erpnext_enhancements.patches import remove_production_team_user_docperm as p

	patch = p


def _remaining():
	return sorted((row["parent"], row["role"], row["permlevel"]) for row in STATE["rows"])


def _production_like_site():
	"""User's rows as production held them on 2026-11-05, plus Production Team elsewhere."""
	_row("cfgcmeik1e", "User", "Desk User")
	_row("cfgp6gluk6", "User", "Employee Self Service")
	_row("6r1gpkrmfa", "User", "Production Team")
	_row("cfg1laoso9", "User", "System Manager")
	_row("cfghn8dq1v", "User", "System Manager", permlevel=1)
	_row("mr-pt", "Material Request", "Production Team")
	_row("po-pt", "Purchase Order", "Production Team")


class RemovesTheRowTest(unittest.TestCase):
	def setUp(self):
		_reset()
		_production_like_site()

	def test_production_teams_row_on_user_is_deleted(self):
		self.assertEqual(patch.remove_production_team_user_docperm(), ["6r1gpkrmfa"])
		self.assertEqual(STATE["deleted"], ["6r1gpkrmfa"])
		self.assertEqual(STATE["get_all"], [{"parent": "User", "role": "Production Team"}])

	def test_deleted_the_way_role_permissions_manager_does(self):
		# permission_manager.remove: delete_doc(..., ignore_permissions=True, force=True), which
		# writes a Deleted Document and a Permission Log rather than a bare DELETE.
		patch.remove_production_team_user_docperm()
		self.assertEqual(STATE["delete_kwargs"], [{"ignore_permissions": True, "force": True}])

	def test_everything_else_stays(self):
		patch.remove_production_team_user_docperm()
		self.assertEqual(
			_remaining(),
			[
				("Material Request", "Production Team", 0),
				("Purchase Order", "Production Team", 0),
				("User", "Desk User", 0),
				("User", "Employee Self Service", 0),
				("User", "System Manager", 0),
				("User", "System Manager", 1),
			],
		)

	def test_rows_at_any_permlevel_go(self):
		_row("pt-level-1", "User", "Production Team", permlevel=1)
		self.assertEqual(sorted(patch.remove_production_team_user_docperm()), ["6r1gpkrmfa", "pt-level-1"])
		self.assertNotIn("Production Team", {role for parent, role, _ in _remaining() if parent == "User"})

	def test_users_cache_is_cleared_once(self):
		patch.remove_production_team_user_docperm()
		self.assertEqual(STATE["clear_cache"], [{"doctype": "User"}])


class NothingToDoTest(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_row_already_removed_by_hand(self):
		_row("cfg1laoso9", "User", "System Manager")
		self.assertEqual(patch.remove_production_team_user_docperm(), [])
		self.assertEqual(STATE["deleted"], [])
		self.assertEqual(STATE["clear_cache"], [])

	def test_safe_twice(self):
		_production_like_site()
		patch.execute()
		patch.execute()
		self.assertEqual(STATE["deleted"], ["6r1gpkrmfa"])
		self.assertEqual(len(STATE["clear_cache"]), 1)
		self.assertEqual(STATE["errors"], [])


class NeverRaisesTest(unittest.TestCase):
	def setUp(self):
		_reset()
		_production_like_site()

	def test_failed_read_is_logged(self):
		STATE["fail_get_all"] = True
		patch.execute()
		self.assertEqual(STATE["errors"], ["remove_production_team_user_docperm: failed"])
		self.assertEqual(STATE["deleted"], [])

	def test_failed_delete_is_logged(self):
		STATE["fail_delete"] = {"6r1gpkrmfa"}
		patch.execute()
		self.assertEqual(STATE["errors"], ["remove_production_team_user_docperm: failed"])


class RegisteredTest(unittest.TestCase):
	def test_runs_post_model_sync(self):
		lines = [line.strip() for line in PATCHES_TXT.read_text(encoding="utf-8").splitlines()]
		entry = "erpnext_enhancements.patches.remove_production_team_user_docperm"
		self.assertEqual(lines.count(entry), 1)
		self.assertGreater(lines.index(entry), lines.index("[post_model_sync]"))


def _user_grants_beyond_select(rows):
	"""(role, permlevel, what) for each grant on User a non-System-Manager role may not hold."""
	found = []
	for row in rows:
		if row.get("parent") != "User" or row.get("role") == "System Manager":
			continue
		level = row.get("permlevel") or 0
		if level > 0:
			found.append((row.get("role"), level, "permlevel"))
		found.extend((row.get("role"), level, ptype) for ptype in FORBIDDEN_ON_USER if row.get(ptype))
	return found


class FixtureGuardTest(unittest.TestCase):
	"""The repo may never hand User records to a role other than System Manager."""

	def test_no_fixture_row_on_user_beyond_select_for_other_roles(self):
		rows = json.loads(CUSTOM_DOCPERM_FIXTURE.read_text(encoding="utf-8"))
		self.assertEqual(
			_user_grants_beyond_select(rows), [], "only System Manager may read or change User records"
		)

	def test_guard_catches_the_row_that_was_on_production(self):
		# The same check, run on the 2026-05-28 row and its neighbours, so a broken check cannot
		# pass silently on a fixture that happens to hold no User rows.
		rows = [
			{"parent": "User", "role": "Production Team", "permlevel": 0, "read": 1, "write": 1, "delete": 1},
			{"parent": "User", "role": "Some Team", "permlevel": 1, "select": 1},
			{"parent": "User", "role": "Desk User", "permlevel": 0, "select": 1},
			{"parent": "User", "role": "Employee Self Service", "permlevel": 0, "select": 1, "export": 1},
			{"parent": "User", "role": "System Manager", "permlevel": 0, "read": 1, "write": 1},
			{"parent": "Material Request", "role": "Production Team", "permlevel": 0, "write": 1},
		]
		self.assertEqual(
			_user_grants_beyond_select(rows),
			[
				("Production Team", 0, "read"),
				("Production Team", 0, "write"),
				("Production Team", 0, "delete"),
				("Some Team", 1, "permlevel"),
			],
		)


if __name__ == "__main__":
	unittest.main()
