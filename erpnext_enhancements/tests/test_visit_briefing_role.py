"""Bench-free checks that the Visit Wizard's bootstrap includes the site briefing only for the
roles the briefing endpoint answers.

``get_visit_bootstrap`` is loaded from the source with ``ast`` and RUN against a stand-in
``frappe``; the briefing builder, the section and template readers and the record are fakes
that record whether they were called. Nothing imports ``frappe`` or installs a stub.

What is pinned:

- A caller in one of the maintenance roles gets the briefing in the bootstrap, built for the
  visit's project, serial and record.
- A caller who can read the visit but holds none of those roles gets the rest of the bootstrap
  with an empty briefing, and the builder is never called for them.
- The bootstrap's yes or no and ``get_dashboard_context``'s role step agree for every role in
  the list and for roles outside it, so the two cannot drift apart.

Run: python -m unittest erpnext_enhancements.tests.test_visit_briefing_role -v
"""

import ast
import types
import unittest

from erpnext_enhancements.tests.test_access_checks_ops import (
	APP,
	MAINTENANCE_PERMISSIONS,
	RECORD,
	VISIT_API,
	_FakeFrappe,
	_function,
	_Refused,
	_run,
	_view_all_roles,
)

OTHER_ROLES = ("Customer", "Finance Team", "Employee", "Maintenance Reviewer", "Sales User")


class _FakeVisit:
	"""Enough of a submitted visit for the bootstrap: it is read, never written."""

	doctype = "Sapphire Maintenance Record"
	name = "VISIT-1"
	docstatus = 1
	project = "PRJ-1"
	serial_no = "SN-1"
	maintenance_contract = None
	template = None

	def get(self, key, default=None):
		return getattr(self, key, default)

	def apply_fieldlevel_read_permissions(self):
		pass

	def as_dict(self):
		return {"name": self.name, "project": self.project}


class _Calls:
	"""The fakes the bootstrap leans on, each noting that it was called."""

	def __init__(self, visit=None):
		self.visit = visit or _FakeVisit()
		self.briefing_calls = []

	def build_dashboard_context(self, project, serial_no=None, record=None):
		self.briefing_calls.append((project, serial_no, record))
		return {"profile": {"safety_instructions": "SAFETY"}, "marker": "briefing"}

	def namespace(self, fake):
		return {
			"_get_record": lambda record: self.visit,
			"_section_meta": lambda doc: {"sections": True},
			"_template_meta": lambda template: {"template": True},
			"_feature_names": lambda doc: {},
			"_wizard_state": lambda doc: {"state": True},
			"_sign_off_context": lambda doc: {"default_email": ""},
			"size_choices": lambda: {},
			"PAYLOAD_TABLE_MAP": {},
			"build_visit_payload": lambda **kwargs: {},
			"build_dashboard_context": self.build_dashboard_context,
		}


def _sees_all(fake):
	"""``permissions._sees_all``, compiled alone against ``fake``."""
	return _run(MAINTENANCE_PERMISSIONS, "_sees_all", fake, VIEW_ALL_ROLES=_view_all_roles())


def _bootstrap(fake, calls):
	"""``get_visit_bootstrap`` with the decision helper compiled from the same source."""
	namespace = calls.namespace(fake)
	namespace["_may_see_briefing"] = _run(
		VISIT_API, "_may_see_briefing", fake, _sees_all=_sees_all(fake)
	)
	node = _function(VISIT_API, "get_visit_bootstrap")
	node.decorator_list = []
	module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
	namespace.update({"frappe": fake, "_": lambda text: text})
	exec(compile(module, str(VISIT_API), "exec"), namespace)
	return namespace["get_visit_bootstrap"]


class TestBootstrapBriefingRole(unittest.TestCase):
	def test_a_maintenance_role_gets_the_briefing(self):
		for role in sorted(_view_all_roles()):
			with self.subTest(role=role):
				fake = _FakeFrappe(roles=[role, "Employee"])
				calls = _Calls()
				result = _bootstrap(fake, calls)("VISIT-1")
				self.assertEqual(result["dashboard"]["marker"], "briefing")
				self.assertEqual(calls.briefing_calls, [("PRJ-1", "SN-1", "VISIT-1")])

	def test_administrator_gets_the_briefing(self):
		fake = _FakeFrappe(user="Administrator", roles=[])
		calls = _Calls()
		result = _bootstrap(fake, calls)("VISIT-1")
		self.assertEqual(result["dashboard"]["marker"], "briefing")
		self.assertEqual(len(calls.briefing_calls), 1)

	def test_a_caller_who_can_only_read_the_visit_gets_it_without_the_briefing(self):
		for roles in (["Customer"], ["Finance Team", "Employee"], []):
			with self.subTest(roles=roles):
				fake = _FakeFrappe(roles=roles)
				calls = _Calls()
				result = _bootstrap(fake, calls)("VISIT-1")
				self.assertEqual(result["dashboard"], {})
				# Everything else still answers.
				self.assertEqual(result["record"], {"name": "VISIT-1", "project": "PRJ-1"})
				self.assertEqual(result["sections"], {"sections": True})
				self.assertEqual(result["template_meta"], {"template": True})
				self.assertEqual(result["state"], {"state": True})
				self.assertIn("sign_off", result)
				self.assertIn("sizes", result)

	def test_the_builder_is_not_called_for_that_caller(self):
		fake = _FakeFrappe(roles=["Customer"])
		calls = _Calls()
		_bootstrap(fake, calls)("VISIT-1")
		self.assertEqual(calls.briefing_calls, [])

	def test_a_visit_with_no_project_has_an_empty_briefing_for_anyone(self):
		visit = _FakeVisit()
		visit.project = None
		fake = _FakeFrappe(roles=["Maintenance User"])
		calls = _Calls(visit)
		result = _bootstrap(fake, calls)("VISIT-1")
		self.assertEqual(result["dashboard"], {})
		self.assertEqual(calls.briefing_calls, [])

	def test_the_visit_is_still_checked_before_anything_is_built(self):
		source = ast.unparse(_function(VISIT_API, "get_visit_bootstrap"))
		self.assertLess(source.index("_get_record(record)"), source.index("build_dashboard_context("))
		# The builder sits in the branch of a conditional whose test names the role rule.
		guards = [
			ast.unparse(node.test)
			for node in ast.walk(_function(VISIT_API, "get_visit_bootstrap"))
			if isinstance(node, ast.IfExp) and "build_dashboard_context(" in ast.unparse(node.body)
		]
		self.assertEqual(len(guards), 1)
		self.assertIn("_may_see_briefing()", guards[0])


class TestSameRuleAsTheBriefingEndpoint(unittest.TestCase):
	def _endpoint_role_step_passes(self, roles, user="tech@example.com"):
		"""True when ``get_dashboard_context``'s check lets this caller past its role step."""
		fake = _FakeFrappe(user=user, roles=roles, allowed=[("Project", "read", "PRJ-1")])
		check = _run(RECORD, "_check_dashboard_access", fake, VIEW_ALL_ROLES=_view_all_roles())
		try:
			check("PRJ-1")
		except _Refused:
			return False
		return True

	def _wizard_says_yes(self, roles, user="tech@example.com"):
		fake = _FakeFrappe(user=user, roles=roles)
		may = _run(VISIT_API, "_may_see_briefing", fake, _sees_all=_sees_all(fake))
		return may()

	def test_both_answer_the_same_for_every_role_and_for_other_roles(self):
		cases = [[role] for role in sorted(_view_all_roles())]
		cases += [[role] for role in OTHER_ROLES]
		cases += [list(OTHER_ROLES), []]
		cases += [[OTHER_ROLES[0], sorted(_view_all_roles())[0]]]
		for roles in cases:
			with self.subTest(roles=roles):
				self.assertEqual(self._wizard_says_yes(roles), self._endpoint_role_step_passes(roles))

	def test_both_let_administrator_through(self):
		self.assertTrue(self._wizard_says_yes([], user="Administrator"))
		self.assertTrue(self._endpoint_role_step_passes([], user="Administrator"))

	def test_the_decision_reads_the_shared_role_set_and_names_no_list_of_its_own(self):
		code = ast.unparse(_function(VISIT_API, "_may_see_briefing"))
		self.assertIn("_sees_all(frappe.session.user)", code)
		for role in _view_all_roles():
			self.assertNotIn(role, code)
		sees_all = ast.unparse(_function(MAINTENANCE_PERMISSIONS, "_sees_all"))
		self.assertIn("VIEW_ALL_ROLES", sees_all)


class TestTheWizardPageToleratesAMissingBriefing(unittest.TestCase):
	"""The page reads every briefing key through a fallback, so an empty briefing draws the
	Safety step without the access panel and the cards that come from the briefing."""

	def setUp(self):
		self.js = (APP / "sapphire_maintenance/page/visit_wizard/visit_wizard.js").read_text(
			encoding="utf-8"
		)

	def test_the_bootstrap_answer_falls_back_to_an_empty_briefing(self):
		self.assertIn("this.dashboard = data.dashboard || {};", self.js)

	def test_each_reader_has_a_fallback(self):
		for needle in (
			"const profile = this.dashboard.profile || {};",
			"const serial = this.dashboard.serial_no || {};",
			"const contract = this.dashboard.contract || {};",
			"const scope = this.dashboard.agreement;\n\t\tif (!scope || !scope.name) return;",
			"const last = this.dashboard.last_visit;\n\t\tif (!last) return;",
			"(this.dashboard.profile || {}).wrapup_instructions",
		):
			with self.subTest(needle=needle):
				self.assertIn(needle, self.js)

	def test_no_reader_walks_into_the_briefing_without_one(self):
		import re

		unguarded = re.findall(r"this\.dashboard\.(\w+)\.", self.js)
		self.assertEqual(unguarded, [], "a briefing key is read without a fallback")


if __name__ == "__main__":
	unittest.main()
