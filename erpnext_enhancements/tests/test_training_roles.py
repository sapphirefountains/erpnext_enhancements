"""Bench-free unit tests for granting the Training Learner role.

Two things are pinned here, and both are expensive to get wrong in ways that do
not announce themselves:

  * **``Training Learner`` keeps ``desk_access = 0``.** It is held by customer
    contacts as well as staff, so desk access would turn every one of them into a
    billable System User. Nothing at runtime would complain; the invoice would.
  * **A profiled user is granted through a profile, never directly.**
    ``User.validate`` rebuilds ``roles`` from the union of a user's Role Profiles
    on every save, so ``add_roles`` on a profiled user appears to succeed and then
    silently evaporates the next time anything touches that user. On this site
    that is 11 of 15 active employees. The inverse is worse: giving a profile to a
    profile-less user regenerates their roles from it and wipes ``System
    Manager``, and the four profile-less users here include the System Managers.

The stub is installed in ``setUpModule`` (execution time), not at import, so it
never fools the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_training_roles
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

roles_module = None
seed_patch = None

STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


class _StubUser(_Dict):
	"""Just enough User to exercise the two branches, including the trap.

	``save`` deliberately reproduces ``populate_role_profile_roles``: when the
	user holds any Role Profile, ``roles`` is rebuilt from the union of those
	profiles and anything granted directly is dropped. Without that behaviour the
	test would pass against the naive implementation too, and prove nothing.
	"""

	def add_roles(self, *role_names):
		for role in role_names:
			if role not in {r["role"] for r in self["roles"]}:
				self["roles"].append(_Dict(role=role))
		self.save()

	def append(self, fieldname, row):
		self.setdefault(fieldname, []).append(_Dict(row))

	def save(self, ignore_permissions=False):
		profiles = [r["role_profile"] for r in self.get("role_profiles") or []]
		if not profiles:
			return
		rebuilt = []
		for profile in profiles:
			for role in STATE["profile_roles"].get(profile, []):
				if role not in rebuilt:
					rebuilt.append(role)
		self["roles"] = [_Dict(role=r) for r in rebuilt]


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"users": {},
			"roles": {"Training Learner"},
			# Role Profile -> the roles it carries.
			"profile_roles": {
				"Production Team": ["Maintenance User", "Employee Self Service"],
				"Training Learner": ["Training Learner"],
			},
			"profiles_created": [],
		}
	)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.session = _Dict(user="tester@example.com")
	frappe.flags = _Dict()
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.get_doc = lambda doctype, name=None: STATE["users"][name]

	def _new_doc(doctype):
		doc = _Dict(doctype=doctype, roles=[])
		doc.append = lambda field, row, _d=doc: _d.setdefault(field, []).append(_Dict(row))
		doc.insert = lambda ignore_permissions=False, _d=doc: STATE["profiles_created"].append(_d)
		return doc

	frappe.new_doc = _new_doc

	def _exists(doctype, name=None, **kwargs):
		if doctype == "Role":
			return name in STATE["roles"]
		if doctype == "User":
			return name in STATE["users"]
		if doctype == "Role Profile":
			return name in STATE["profile_roles"]
		return None

	def _get_value(doctype, name, field=None, **kwargs):
		if doctype == "User" and field == "user_type":
			user = STATE["users"].get(name)
			return user.get("user_type") if user else None
		return None

	frappe.db = types.SimpleNamespace(exists=_exists, get_value=_get_value)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils


def setUpModule():
	global roles_module
	_install_frappe_stub()
	_reset_state()
	from erpnext_enhancements.training import roles as module

	roles_module = module


def _add_user(name, user_type="System User", profiles=None, direct_roles=None):
	# Child rows are _Dict, not plain dicts: Frappe child docs are attribute-
	# accessed (`row.role`), and a stub using plain dicts would raise
	# AttributeError into the module's own except-and-log, turning a real failure
	# into a silent False.
	user = _StubUser(
		name=name,
		user_type=user_type,
		roles=[_Dict(role=r) for r in (direct_roles or [])],
		role_profiles=[_Dict(role_profile=p) for p in (profiles or [])],
		role_profile_name=(profiles or [None])[0],
	)
	STATE["users"][name] = user
	return user


class TestRoleDefinition(unittest.TestCase):
	def test_training_learner_is_seeded_without_desk_access(self):
		"""Pinned deliberately: desk access here is a licensing decision, not a
		convenience, and nothing at runtime would flag the change."""
		from erpnext_enhancements.patches.seed_training_roles import ROLES

		by_name = dict(ROLES)
		self.assertEqual(by_name["Training Learner"], 0)
		self.assertEqual(by_name["Training Author"], 1)
		self.assertEqual(by_name["Training Manager"], 1)


class TestProfilelessUser(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_role_is_granted_directly(self):
		user = _add_user("boss@example.com", direct_roles=["System Manager"])
		self.assertTrue(roles_module.grant_learner_role("boss@example.com"))
		self.assertIn("Training Learner", {r["role"] for r in user["roles"]})

	def test_existing_direct_roles_survive(self):
		"""The whole reason profile-less users must not be given a profile."""
		user = _add_user("boss@example.com", direct_roles=["System Manager", "PO Approver"])
		roles_module.grant_learner_role("boss@example.com")
		held = {r["role"] for r in user["roles"]}
		self.assertIn("System Manager", held)
		self.assertIn("PO Approver", held)

	def test_no_profile_is_ever_assigned(self):
		user = _add_user("boss@example.com", direct_roles=["System Manager"])
		roles_module.grant_learner_role("boss@example.com")
		self.assertEqual(user.get("role_profiles"), [])

	def test_repeat_grant_is_a_no_op(self):
		_add_user("boss@example.com", direct_roles=["System Manager", "Training Learner"])
		self.assertFalse(roles_module.grant_learner_role("boss@example.com"))


class TestProfiledUser(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_role_is_granted_via_an_extra_profile(self):
		user = _add_user("tech@example.com", profiles=["Production Team"])
		self.assertTrue(roles_module.grant_learner_role("tech@example.com"))
		self.assertIn(
			"Training Learner", {r["role_profile"] for r in user["role_profiles"]}
		)

	def test_the_role_survives_the_next_save(self):
		"""The point of the whole design. A direct grant would vanish here."""
		user = _add_user("tech@example.com", profiles=["Production Team"])
		roles_module.grant_learner_role("tech@example.com")
		user.save()  # anything at all touching this user
		self.assertIn("Training Learner", {r["role"] for r in user["roles"]})

	def test_their_existing_profile_is_kept(self):
		user = _add_user("tech@example.com", profiles=["Production Team"])
		roles_module.grant_learner_role("tech@example.com")
		self.assertIn("Production Team", {r["role_profile"] for r in user["role_profiles"]})
		self.assertIn("Maintenance User", {r["role"] for r in user["roles"]})

	def test_repeat_grant_is_a_no_op(self):
		_add_user("tech@example.com", profiles=["Production Team", "Training Learner"])
		self.assertFalse(roles_module.grant_learner_role("tech@example.com"))

	def test_a_direct_grant_would_have_evaporated(self):
		"""Guards the stub itself: if this stops failing, the test above has
		stopped proving anything."""
		user = _add_user("tech@example.com", profiles=["Production Team"])
		user.add_roles("Training Learner")
		self.assertNotIn("Training Learner", {r["role"] for r in user["roles"]})


class TestWebsiteUsers(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_website_users_are_left_alone(self):
		"""A customer gaining training access is a decision somebody makes, not a
		side effect of an employee sync."""
		_add_user("customer@example.com", user_type="Website User")
		self.assertFalse(roles_module.grant_learner_role("customer@example.com"))

	def test_unknown_user_is_ignored(self):
		self.assertFalse(roles_module.grant_learner_role("nobody@example.com"))

	def test_missing_role_is_ignored(self):
		STATE["roles"].discard("Training Learner")
		_add_user("boss@example.com")
		self.assertFalse(roles_module.grant_learner_role("boss@example.com"))


if __name__ == "__main__":
	unittest.main()
