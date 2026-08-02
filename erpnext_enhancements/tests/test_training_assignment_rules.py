"""Bench-free unit tests for the Training assignment rule engine.

Stubs a minimal ``frappe`` (no site/bench) so ``training.assignment`` runs under
plain unittest. Installed in ``setUpModule`` (execution time), not at import, so
it never fools the bench-only suites' ``import frappe`` skip-guards.

What is guarded is the part that quietly assigns the wrong training to the wrong
people:

  * each rule target resolves against the right thing — Department / Designation /
    Grade / Employment Type against the Employee record, Role against the user's
    actual roles, Role Profile against their profile;
  * **first match wins, and only once.** Somebody caught by two rules gets one
    assignment, and the rule recorded is the one that explains it;
  * a disabled rule never fires, and "All Employees" does not sweep in customer
    Website Users, who have no Employee record and no business owing internal
    training;
  * a rule keyed on a column this site does not have is skipped rather than
    raising — the defensive-column guard CLAUDE.md requires;
  * the Employee ``on_update`` guard: a save that changes nothing a rule keys off
    does no work at all. Without that comparison every Employee save enqueues a
    full sweep.

Run: python -m unittest erpnext_enhancements.tests.test_training_assignment_rules
"""

import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

assignment = None

STATE = {}


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value

	def get_doc_before_save(self):
		return self.get("_before")


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"employees": {},
			"user_roles": {},
			"user_profile": {},
			"missing_columns": set(),
			"enqueued": [],
			"granted": [],
		}
	)


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.session = _Dict(user="tester@example.com")
	frappe.flags = _Dict(
		in_migrate=False, in_install=False, in_patch=False, in_import=False, in_test=False
	)
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda: ""
	frappe.get_roles = lambda user=None: STATE["user_roles"].get(user, [])
	frappe.enqueue = lambda method, **kwargs: STATE["enqueued"].append((method, kwargs))
	frappe.get_all = lambda *a, **k: []
	frappe.get_cached_doc = lambda doctype, name: STATE["courses"][name]
	frappe.get_doc = lambda *a, **k: _Dict()

	def _get_value(doctype, filters, fieldname=None, as_dict=False, **kwargs):
		if doctype == "Employee":
			if isinstance(filters, dict):
				user = filters.get("user_id")
				row = STATE["employees"].get(user)
				if not row:
					return None
				if as_dict:
					return _Dict(row)
				return row.get(fieldname)
			return None
		if doctype == "User" and fieldname == "role_profile_name":
			return STATE["user_profile"].get(filters)
		return None

	frappe.db = types.SimpleNamespace(
		get_value=_get_value,
		exists=lambda *a, **k: None,
		set_value=lambda *a, **k: None,
		get_single_value=lambda *a, **k: 14,
		commit=lambda: None,
		has_column=lambda doctype, column: column not in STATE["missing_columns"],
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(v or 0)
	utils.today = lambda: "2026-08-01"
	utils.add_days = lambda d, n: d
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils

	# training.assignment imports these two at module load; stub them so the
	# import does not drag in the whole doctype package.
	settings = types.ModuleType(
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	)
	settings.is_enabled = lambda switch="training_enabled": True
	sys.modules[
		"erpnext_enhancements.training.doctype.training_settings.training_settings"
	] = settings

	roles = types.ModuleType("erpnext_enhancements.training.roles")
	roles.ROLE = "Training Learner"
	roles.PROFILE = "Training Learner"
	roles.ensure_profile = lambda: None
	roles.grant_learner_role = lambda user: STATE["granted"].append(user) or True
	sys.modules["erpnext_enhancements.training.roles"] = roles
	# Also stamp it on the package. ``training.assignment`` does
	# ``from erpnext_enhancements.training import roles``, which reads the
	# attribute off the already-imported package rather than sys.modules — so if
	# another suite in the same process imported the real module first, the
	# sys.modules entry above would be ignored. (CI runs each Training suite in
	# its own step for the same reason; this keeps a local `unittest a b c` honest
	# too.)
	import importlib

	package = importlib.import_module("erpnext_enhancements.training")
	package.roles = roles


def setUpModule():
	global assignment
	_install_frappe_stub()
	_reset_state()
	from erpnext_enhancements.training import assignment as module

	assignment = module


# ------------------------------------------------------------------- fixtures


def _rule(applies_to, value=None, enabled=1, due_days=0):
	return _Dict(
		applies_to=applies_to,
		applies_to_value=value,
		applies_to_doctype="",
		enabled=enabled,
		due_days=due_days,
		idx=1,
	)


def _course(*rules):
	return _Dict(name="TRN-CRS-00001", weight="Required", auto_assign=1, assign_rules=list(rules))


def _employee(user, **fields):
	STATE["employees"][user] = dict(
		{"name": "HR-EMP-0001", "department": "", "designation": "", "grade": "", "employment_type": ""},
		**fields,
	)


# ---------------------------------------------------------------------- tests


class TestRuleTargeting(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_all_employees_matches_anyone_with_an_employee_record(self):
		_employee("tech@example.com")
		rule = _rule("All Employees")
		self.assertIs(assignment._matching_rule(_course(rule), "tech@example.com"), rule)

	def test_all_employees_does_not_match_a_customer(self):
		"""A Website User has no Employee record and no business owing internal
		training; an "All Employees" rule must not sweep them in."""
		self.assertIsNone(
			assignment._matching_rule(_course(_rule("All Employees")), "customer@example.com")
		)

	def test_department_matches_the_employee_field(self):
		_employee("tech@example.com", department="Production")
		rule = _rule("Department", "Production")
		self.assertIs(assignment._matching_rule(_course(rule), "tech@example.com"), rule)

	def test_department_does_not_match_a_different_department(self):
		_employee("tech@example.com", department="Design - SF")
		self.assertIsNone(
			assignment._matching_rule(_course(_rule("Department", "Production")), "tech@example.com")
		)

	def test_designation_matches(self):
		_employee("tech@example.com", designation="Senior Technician")
		rule = _rule("Designation", "Senior Technician")
		self.assertIs(assignment._matching_rule(_course(rule), "tech@example.com"), rule)

	def test_role_matches_the_users_actual_roles(self):
		_employee("tech@example.com")
		STATE["user_roles"]["tech@example.com"] = ["Maintenance User", "Employee Self Service"]
		rule = _rule("Role", "Maintenance User")
		self.assertIs(assignment._matching_rule(_course(rule), "tech@example.com"), rule)

	def test_role_profile_matches(self):
		_employee("tech@example.com")
		STATE["user_profile"]["tech@example.com"] = "Production Team"
		rule = _rule("Role Profile", "Production Team")
		self.assertIs(assignment._matching_rule(_course(rule), "tech@example.com"), rule)

	def test_disabled_rule_never_fires(self):
		_employee("tech@example.com", department="Production")
		self.assertIsNone(
			assignment._matching_rule(
				_course(_rule("Department", "Production", enabled=0)), "tech@example.com"
			)
		)

	def test_missing_column_is_skipped_not_raised(self):
		"""Employee Grade is ERPNext core but not guaranteed to be present. The
		defensive-column guard means a rule against it silently never matches
		rather than turning a fresh-DB install into a crash."""
		_employee("tech@example.com", grade="Level 3")
		STATE["missing_columns"].add("grade")
		self.assertIsNone(
			assignment._matching_rule(_course(_rule("Employee Grade", "Level 3")), "tech@example.com")
		)


class TestFirstMatchWins(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def test_two_matching_rules_return_only_the_first(self):
		"""Somebody caught by two rules is assigned once, and the rule recorded is
		the one that explains why they got it."""
		_employee("tech@example.com", department="Production", designation="Senior Technician")
		first = _rule("Department", "Production")
		second = _rule("Designation", "Senior Technician")
		self.assertIs(assignment._matching_rule(_course(first, second), "tech@example.com"), first)

	def test_order_decides_which_rule_is_recorded(self):
		_employee("tech@example.com", department="Production", designation="Senior Technician")
		first = _rule("Designation", "Senior Technician")
		second = _rule("Department", "Production")
		self.assertIs(assignment._matching_rule(_course(first, second), "tech@example.com"), first)

	def test_a_non_matching_rule_does_not_block_a_later_match(self):
		_employee("tech@example.com", designation="Senior Technician")
		miss = _rule("Department", "Production")
		hit = _rule("Designation", "Senior Technician")
		self.assertIs(assignment._matching_rule(_course(miss, hit), "tech@example.com"), hit)


class TestEmployeeUpdateGuard(unittest.TestCase):
	"""The guard that keeps every Employee save from enqueuing a rule sweep."""

	def setUp(self):
		_reset_state()

	def _doc(self, before, now):
		doc = _Dict(name="HR-EMP-0001", **now)
		doc["_before"] = _Dict(**before)
		return doc

	def test_unrelated_change_does_no_work(self):
		doc = self._doc(
			{"department": "Production", "user_id": "tech@example.com"},
			{"department": "Production", "user_id": "tech@example.com"},
		)
		assignment.on_employee_update(doc)
		self.assertEqual(STATE["enqueued"], [])

	def test_department_change_enqueues_a_sweep(self):
		doc = self._doc(
			{"department": "Design - SF", "user_id": "tech@example.com"},
			{"department": "Production", "user_id": "tech@example.com"},
		)
		assignment.on_employee_update(doc)
		self.assertEqual(len(STATE["enqueued"]), 1)

	def test_status_change_enqueues_a_sweep(self):
		doc = self._doc(
			{"status": "Active", "user_id": "tech@example.com"},
			{"status": "Left", "user_id": "tech@example.com"},
		)
		assignment.on_employee_update(doc)
		self.assertEqual(len(STATE["enqueued"]), 1)

	def test_gaining_a_login_grants_the_role_and_sweeps(self):
		"""An employee created before their login existed becomes assignable the
		moment one appears — otherwise they would never pick anything up."""
		doc = self._doc({"user_id": ""}, {"user_id": "tech@example.com"})
		assignment.on_employee_update(doc)
		self.assertEqual(STATE["granted"], ["tech@example.com"])
		self.assertEqual(len(STATE["enqueued"]), 1)

	def test_the_sweep_runs_after_commit(self):
		"""Never inline: User and Employee are written on paths adjacent to login,
		and a slow sweep must not delay one."""
		doc = self._doc(
			{"designation": "Junior Technician", "user_id": "tech@example.com"},
			{"designation": "Senior Technician", "user_id": "tech@example.com"},
		)
		assignment.on_employee_update(doc)
		_method, kwargs = STATE["enqueued"][0]
		self.assertTrue(kwargs["enqueue_after_commit"])


class TestUserRolesGuard(unittest.TestCase):
	def setUp(self):
		_reset_state()

	def _doc(self, before_roles, now_roles):
		doc = _Dict(name="tech@example.com", roles=[_Dict(role=r) for r in now_roles])
		doc["_before"] = _Dict(roles=[_Dict(role=r) for r in before_roles])
		return doc

	def test_unchanged_roles_do_no_work(self):
		assignment.on_user_roles_changed(self._doc(["Maintenance User"], ["Maintenance User"]))
		self.assertEqual(STATE["enqueued"], [])

	def test_reordered_roles_do_no_work(self):
		"""Compared as a set — a Role Profile rebuild reorders rows constantly."""
		assignment.on_user_roles_changed(
			self._doc(["A", "B"], ["B", "A"])
		)
		self.assertEqual(STATE["enqueued"], [])

	def test_added_role_enqueues_a_sweep(self):
		assignment.on_user_roles_changed(self._doc(["A"], ["A", "Maintenance User"]))
		self.assertEqual(len(STATE["enqueued"]), 1)


if __name__ == "__main__":
	unittest.main()
