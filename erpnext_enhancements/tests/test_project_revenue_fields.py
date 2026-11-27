"""ERPNext's revenue, cost and margin totals on Project go to Accounts Manager and System Manager only.

Owner decision, 2026-11-19 (in force on the KPI dashboards since v1.836.6), applied to Project's
eight ERPNext totals on 2026-11-27 ("All eight"): ``project_revenue.PROTECTED_FIELDS`` sit at
permission level 3 through fixture Property Setters, and ``patches/grant_project_revenue_visibility``
gives the two roles read and write there. Project Dollar Amount, Estimated Cost, Bid Cost and
Materials Budget stay open. ERPNext's Costing tab, which works its figures out of the documents
and so is not reached by a field level, is wrapped (``project_revenue.get_project_costing``), and
four reports that read billed revenue in SQL leave it out for everyone else ("Hide in all").

Bench-free. The fixtures, the patch, ``patches.txt`` and ``hooks.py`` are read from the files;
the patch, the Costing wrapper and its seal, the four reports and the reconciliation query are
RUN over a stand-in ``frappe``, with every SQL statement they send captured.

Run: python -m unittest erpnext_enhancements.tests.test_project_revenue_fields
"""

import ast
import datetime
import json
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
FIXTURES = APP / "fixtures"
PATCH = APP / "patches" / "grant_project_revenue_visibility.py"

PROTECTED = {
	"total_sales_amount",
	"total_billable_amount",
	"total_billed_amount",
	"total_costing_amount",
	"total_purchase_cost",
	"total_consumed_material_cost",
	"gross_margin",
	"per_gross_margin",
}
#: The owner kept these open on 2026-11-27: project managers work from them.
OPEN = {"custom_project_dollar_amount", "estimated_costing", "custom_bid_cost", "custom_materials_budget"}
REVENUE_ROLES = {"Accounts Manager", "System Manager"}
COSTING = "erpnext.projects.doctype.project.costing.project_costing.get_project_costing"
WRAPPER = "erpnext_enhancements.project_revenue.get_project_costing"

_SAVED = {}
modules = {}


# ----------------------------------------------------------------- the stand-in frappe


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None

	def __setattr__(self, key, value):
		self[key] = value


class State:
	"""What the stand-in answers, reset before every test."""

	def reset(self):
		self.user = "pm@example.com"
		self.roles = ["Projects User", "Employee"]
		self.permitted = None  # Project fields the user reads; None = every field
		self.sql = []
		self.sql_rows = []
		self.custom_docperms = []
		self.roles_on_site = {"Accounts Manager", "System Manager", "Projects User"}
		self.calls = []
		self.whitelisted = set()
		self.overrides = {}
		self.attrs = {}
		self.logged = []


STATE = State()


def _install():
	frappe = types.ModuleType("frappe")
	frappe.__path__ = []
	frappe._ = lambda text, *a, **k: text
	frappe._dict = _Dict

	class _Session:
		@property
		def user(self):
			return STATE.user

	frappe.session = _Session()
	frappe.get_roles = lambda user=None: list(STATE.roles)
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.get_attr = lambda path: STATE.attrs[path]
	frappe.override_whitelisted_method = lambda path: STATE.overrides.get(path, path)

	class _Whitelisted:
		def __contains__(self, fn):
			return fn in STATE.whitelisted

		def discard(self, fn):
			STATE.whitelisted.discard(fn)

		def add(self, fn):
			STATE.whitelisted.add(fn)

	frappe.whitelisted = _Whitelisted()

	def log_error(*a, **k):
		STATE.logged.append(k.get("title") or (a[0] if a else ""))

	frappe.log_error = log_error
	frappe.get_traceback = lambda *a, **k: "traceback"
	frappe.format_value = lambda value, df=None: str(value)
	frappe.get_all = lambda *a, **k: []
	frappe.clear_cache = lambda **k: STATE.calls.append(("clear_cache", k))

	def get_doc(data):
		doc = _Dict(data)

		def insert(ignore_permissions=False):
			STATE.calls.append(("insert", dict(data)))
			if data.get("doctype") == "Custom DocPerm":
				STATE.custom_docperms.append(dict(data))
			return doc

		doc.insert = insert
		return doc

	frappe.get_doc = get_doc

	class _DB:
		def sql(self, query, values=None, as_dict=False, **k):
			STATE.sql.append(query)
			return [_Dict(row) for row in STATE.sql_rows]

		def has_column(self, doctype, column):
			return True

		def table_exists(self, doctype):
			return True

		def exists(self, doctype, filters=None):
			if doctype == "DocType":
				return True
			if doctype == "Role":
				return filters in STATE.roles_on_site
			if doctype == "Custom DocPerm":
				return any(all(row.get(k) == v for k, v in filters.items()) for row in STATE.custom_docperms)
			return False

	frappe.db = _DB()

	utils = types.ModuleType("frappe.utils")

	def getdate(value=None):
		if value is None:
			return datetime.date(2026, 11, 27)
		if isinstance(value, datetime.datetime):
			return value.date()
		if isinstance(value, datetime.date):
			return value
		return datetime.date.fromisoformat(str(value)[:10])

	def add_months(date, months):
		date = getdate(date)
		month = date.month - 1 + months
		return datetime.date(date.year + month // 12, month % 12 + 1, 1)

	utils.getdate = getdate
	utils.add_months = add_months
	utils.add_days = lambda date, days: getdate(date) + datetime.timedelta(days=days)
	utils.nowdate = lambda: "2026-11-27"
	utils.now_datetime = lambda: datetime.datetime(2026, 11, 27, 12, 0)
	utils.get_first_day = lambda date: getdate(date).replace(day=1)
	utils.flt = lambda value, precision=None: float(value or 0)
	utils.cint = lambda value: int(value or 0)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")

	def get_permitted_fields(doctype, parenttype=None, user=None, permission_type=None, **k):
		STATE.calls.append(("get_permitted_fields", doctype, user, permission_type))
		if STATE.permitted is None:
			return ["name", *PROTECTED, *OPEN]
		return ["name", *STATE.permitted]

	model.get_permitted_fields = get_permitted_fields
	frappe.model = model

	permissions = types.ModuleType("frappe.permissions")
	permissions.setup_custom_perms = lambda doctype: STATE.calls.append(("setup_custom_perms", doctype))
	frappe.permissions = permissions

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.permissions": permissions,
		}
	)


def setUpModule():
	for name in list(sys.modules):
		if name.startswith(("erpnext_enhancements", "frappe")):
			_SAVED[name] = sys.modules.pop(name)
	_install()
	from erpnext_enhancements import project_revenue
	from erpnext_enhancements.crm_enhancements import reconciliation
	from erpnext_enhancements.kpi_dashboards import money
	from erpnext_enhancements.kpi_dashboards.report.value_stream_performance import value_stream_performance
	from erpnext_enhancements.marketing.report.ad_spend_roas import ad_spend_roas
	from erpnext_enhancements.marketing.report.social_post_performance import social_post_performance
	from erpnext_enhancements.patches import grant_project_revenue_visibility

	modules.update(
		project_revenue=project_revenue,
		reconciliation=reconciliation,
		money=money,
		value_stream=value_stream_performance,
		ad_spend_roas=ad_spend_roas,
		social=social_post_performance,
		patch=grant_project_revenue_visibility,
	)


def tearDownModule():
	for name in list(sys.modules):
		if name.startswith(("erpnext_enhancements", "frappe")):
			sys.modules.pop(name, None)
	sys.modules.update(_SAVED)
	_SAVED.clear()
	modules.clear()


class Base(unittest.TestCase):
	def setUp(self):
		STATE.reset()

	def as_accounts_manager(self):
		STATE.user = "accounts@example.com"
		STATE.roles = ["Accounts Manager", "Projects User"]
		STATE.permitted = None

	def as_projects_user(self):
		STATE.user = "pm@example.com"
		STATE.roles = ["Projects User", "Projects Manager", "Sales Manager", "Marketing Manager"]
		STATE.permitted = set(OPEN)


def _hooks_literal(name):
	tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
	for node in tree.body:
		if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
			return ast.literal_eval(node.value)
	raise AssertionError(f"{name} is gone from hooks.py")


# ----------------------------------------------------------------- the fixtures


class FixtureRows(Base):
	def setUp(self):
		super().setUp()
		self.setters = json.loads((FIXTURES / "property_setter.json").read_text(encoding="utf-8"))
		self.fields = json.loads((FIXTURES / "custom_field.json").read_text(encoding="utf-8"))

	def test_the_module_names_the_owners_eight(self):
		pr = modules["project_revenue"]
		self.assertEqual(set(pr.PROTECTED_FIELDS), PROTECTED)
		self.assertEqual(len(pr.PROTECTED_FIELDS), 8)
		self.assertEqual(pr.PERMLEVEL, 3)

	def test_each_of_the_eight_is_at_level_three(self):
		levels = {
			row["field_name"]: row
			for row in self.setters
			if row["doc_type"] == "Project" and row["property"] == "permlevel"
		}
		for field in PROTECTED:
			with self.subTest(field=field):
				row = levels.get(field)
				self.assertIsNotNone(row, f"no permlevel Property Setter for Project.{field}")
				self.assertEqual(row["name"], f"Project-{field}-permlevel")
				self.assertEqual(row["value"], "3")
				self.assertEqual(row["property_type"], "Int")
				self.assertEqual(row["doctype_or_field"], "DocField")
				self.assertEqual(row["is_system_generated"], 0)

	def test_nothing_else_on_project_uses_level_three(self):
		"""Level 3 must stay the eight's alone, or whoever reads it reads them too."""
		others = [
			row["name"]
			for row in self.setters
			if row["doc_type"] == "Project"
			and row["property"] == "permlevel"
			and row["field_name"] not in PROTECTED
		]
		self.assertEqual(others, [])
		custom = [
			row["fieldname"]
			for row in self.fields
			if row.get("dt") == "Project" and int(row.get("permlevel") or 0) == 3
		]
		self.assertEqual(custom, [])
		# Project Budget Line answers to Project's levels, so its fields must not use 3 either.
		budget_lines = [
			row["fieldname"]
			for row in self.fields
			if row.get("dt") == "Project Budget Line" and int(row.get("permlevel") or 0) == 3
		]
		self.assertEqual(budget_lines, [])

	def test_the_open_fields_stay_open(self):
		for row in self.setters:
			if row["doc_type"] == "Project" and row["property"] == "permlevel":
				self.assertNotIn(row["field_name"], OPEN)
		for row in self.fields:
			if row.get("dt") == "Project" and row["fieldname"] in OPEN:
				self.assertEqual(int(row.get("permlevel") or 0), 0, row["fieldname"])

	def test_no_other_patch_grants_level_three_on_project(self):
		for path in (APP / "patches").glob("*.py"):
			if path.name == PATCH.name:
				continue
			source = path.read_text(encoding="utf-8")
			with self.subTest(patch=path.name):
				self.assertFalse(
					re.search(r"PERMLEVEL\s*=\s*3\b", source) and '"Project"' in source,
					f"{path.name} also grants level 3 on Project",
				)
		docperms = json.loads((FIXTURES / "custom_docperm.json").read_text(encoding="utf-8"))
		self.assertFalse([row for row in docperms if row.get("parent") == "Project"])


# ----------------------------------------------------------------- the patch


class ThePatch(Base):
	def test_roles_agree_everywhere(self):
		self.assertEqual(set(modules["patch"].ROLES), REVENUE_ROLES)
		self.assertEqual(set(modules["project_revenue"].ROLES), REVENUE_ROLES)
		self.assertEqual(set(modules["money"].REVENUE_ROLES), REVENUE_ROLES)
		self.assertEqual(modules["patch"].PERMLEVEL, modules["project_revenue"].PERMLEVEL)

	def test_grants_read_and_write_at_level_three_after_setup_custom_perms(self):
		created = modules["patch"].grant_project_revenue_visibility()
		self.assertEqual(created, 2)
		kinds = [call[0] for call in STATE.calls]
		self.assertEqual(kinds[0], "setup_custom_perms", "the standard rows must be copied first")
		self.assertIn(("setup_custom_perms", "Project"), STATE.calls)
		self.assertEqual({row["role"] for row in STATE.custom_docperms}, REVENUE_ROLES)
		for row in STATE.custom_docperms:
			self.assertEqual(row["parent"], "Project")
			self.assertEqual(row["parenttype"], "DocType")
			self.assertEqual(row["parentfield"], "permissions")
			self.assertEqual(row["permlevel"], 3)
			self.assertEqual(row["read"], 1)
			self.assertEqual(row["write"], 1)
			for flag in ("create", "delete", "submit", "cancel", "amend", "export", "share"):
				self.assertFalse(row.get(flag), flag)
		self.assertIn(("clear_cache", {"doctype": "Project"}), STATE.calls)

	def test_insert_only_and_safe_twice(self):
		STATE.custom_docperms.append(
			{"parent": "Project", "role": "System Manager", "permlevel": 3, "read": 1}
		)
		self.assertEqual(modules["patch"].grant_project_revenue_visibility(), 1)
		self.assertEqual(modules["patch"].grant_project_revenue_visibility(), 0)
		self.assertEqual(len(STATE.custom_docperms), 2)

	def test_a_missing_role_is_skipped(self):
		STATE.roles_on_site = {"System Manager"}
		self.assertEqual(modules["patch"].grant_project_revenue_visibility(), 1)
		self.assertEqual([row["role"] for row in STATE.custom_docperms], ["System Manager"])

	def test_never_raises(self):
		patch = modules["patch"]
		original = patch.grant_project_revenue_visibility
		patch.grant_project_revenue_visibility = lambda: 1 / 0
		try:
			patch.execute()
		finally:
			patch.grant_project_revenue_visibility = original
		self.assertIn("grant_project_revenue_visibility", STATE.logged)

	def test_listed_after_the_labor_forecast_grant(self):
		lines = [
			line.strip()
			for line in (APP / "patches.txt").read_text(encoding="utf-8").splitlines()
			if line.strip() and not line.startswith("#")
		]
		ours = "erpnext_enhancements.patches.grant_project_revenue_visibility"
		self.assertEqual(lines.count(ours), 1)
		self.assertGreater(lines.index(ours), lines.index("[post_model_sync]"))
		self.assertGreater(
			lines.index(ours), lines.index("erpnext_enhancements.patches.grant_labor_forecast_visibility")
		)

	def test_a_fresh_install_runs_both_grants(self):
		"""``bench install-app`` marks every patch as run without running it. Without these, a fresh
		site gets the level-3 (and level-2) Property Setters and no row granting either level."""
		after_install = _hooks_literal("after_install")
		for grant in (
			"erpnext_enhancements.patches.grant_project_revenue_visibility.execute",
			"erpnext_enhancements.patches.grant_labor_forecast_visibility.execute",
		):
			with self.subTest(grant=grant):
				self.assertIn(grant, after_install)
				self.assertTrue(
					(APP / "patches" / (grant.split(".")[-2] + ".py")).exists(), f"{grant} points at no file"
				)


# ----------------------------------------------------------------- the Costing tab


class CostingWrapper(Base):
	PAYLOAD = {
		"project": "PRJ-1",
		"currency": "USD",
		"consumption_voucher_types": ["Stock Entry"],
		"access": {"invoices": True},
		"errors": {},
		"summary": {"billed": 1000.0, "margin": 250.0, "cost": 750.0},
		"monthly": [{"month": "2026-11", "billed": 1000.0}],
		"billing": {"invoices": [{"amount": 1000.0}]},
		"drivers": {"suppliers": [{"amount": 500.0}]},
		"rate_gaps": {"count": 1},
		"reports": [{"name": "Project Billing Summary"}],
	}

	def setUp(self):
		super().setUp()
		self.seen = []

		def original(project):
			self.seen.append(project)
			return json.loads(json.dumps(self.PAYLOAD))

		self.original = original
		STATE.attrs[COSTING] = original

	def test_wired_in_hooks(self):
		overrides = _hooks_literal("override_whitelisted_methods")
		self.assertEqual(overrides.get(COSTING), WRAPPER)
		self.assertIn("erpnext_enhancements.project_revenue.seal_original", _hooks_literal("before_request"))
		self.assertEqual(modules["project_revenue"].ORIGINAL, COSTING)

	def test_a_reader_of_level_three_gets_it_unchanged(self):
		self.as_accounts_manager()
		self.assertEqual(modules["project_revenue"].get_project_costing("PRJ-1"), self.PAYLOAD)
		self.assertEqual(self.seen, ["PRJ-1"], "ERPNext's own access check runs inside the original")

	def test_administrator_gets_it_unchanged(self):
		STATE.user = "Administrator"
		STATE.permitted = set()
		self.assertEqual(modules["project_revenue"].get_project_costing("PRJ-1"), self.PAYLOAD)

	def test_anyone_else_gets_no_amount(self):
		self.as_projects_user()
		out = modules["project_revenue"].get_project_costing("PRJ-1")
		for section in ("summary", "monthly", "billing", "drivers", "rate_gaps"):
			self.assertIsNone(out[section], section)
			self.assertEqual(out["errors"].get(section), 1, section)
		self.assertEqual(out["restricted"], 1)
		for key in ("project", "currency", "consumption_voucher_types", "access", "reports"):
			self.assertEqual(out[key], self.PAYLOAD[key], key)
		self.assertNotIn("1000", json.dumps(out))
		self.assertNotIn("250", json.dumps(out))

	def test_the_check_is_frappes_field_rule_for_the_session_user(self):
		self.as_projects_user()
		modules["project_revenue"].get_project_costing("PRJ-1")
		self.assertIn(("get_permitted_fields", "Project", "pm@example.com", "read"), STATE.calls)

	def test_one_field_missing_is_enough_to_withhold(self):
		self.as_projects_user()
		STATE.permitted = set(PROTECTED) - {"gross_margin"}
		self.assertTrue(modules["project_revenue"].get_project_costing("PRJ-1")["restricted"])

	def test_without_money_never_changes_its_input(self):
		payload = json.loads(json.dumps(self.PAYLOAD))
		modules["project_revenue"].without_money(payload)
		self.assertEqual(payload, self.PAYLOAD)
		self.assertIsNone(modules["project_revenue"].without_money(None))

	def test_the_seal_takes_the_original_off_while_overridden(self):
		pr = modules["project_revenue"]
		STATE.whitelisted.add(self.original)
		STATE.overrides[COSTING] = WRAPPER
		pr.seal_original()
		self.assertNotIn(self.original, STATE.whitelisted)
		STATE.overrides.clear()
		pr.seal_original()
		self.assertIn(self.original, STATE.whitelisted, "back on once the override is gone")

	def test_the_seal_never_raises(self):
		STATE.attrs.clear()  # get_attr raises KeyError
		modules["project_revenue"]._seal_failure_logged = False
		modules["project_revenue"].seal_original()
		modules["project_revenue"].seal_original()
		self.assertEqual(len(STATE.logged), 1, "logged once per process")


# ----------------------------------------------------------------- the reports


def _billed_reads():
	return [
		query
		for query in STATE.sql
		if "total_billed_amount" in query and "NULL AS total_billed_amount" not in query
	]


class ValueStreamPerformance(Base):
	ROWS = [
		{
			"value_stream": "Events",
			"spend": 10.0,
			"opportunities": 4,
			"won": 1,
			"lost": 1,
			"win_rate": 50.0,
			"revenue": 900.0,
			"avg_deal": 900.0,
			"cost_per_opportunity": 2.5,
		},
		{
			"value_stream": "Build",
			"spend": 5.0,
			"opportunities": 9,
			"won": 3,
			"lost": 0,
			"win_rate": 100.0,
			"revenue": 300.0,
			"avg_deal": 100.0,
			"cost_per_opportunity": None,
		},
		{
			"value_stream": "Unallocated",
			"spend": 7.0,
			"opportunities": None,
			"won": None,
			"lost": None,
			"win_rate": None,
			"revenue": None,
			"avg_deal": None,
			"cost_per_opportunity": None,
		},
	]

	def test_columns(self):
		vs = modules["value_stream"]
		full = [c["fieldname"] for c in vs.get_columns()]
		self.assertIn("revenue", full)
		self.assertIn("avg_deal", full)
		narrowed = [c["fieldname"] for c in vs.get_columns(False)]
		self.assertEqual(narrowed, [f for f in full if f not in ("revenue", "avg_deal")])

	def test_rows_lose_revenue_and_are_ranked_by_wins(self):
		vs = modules["value_stream"]
		out = vs.without_revenue(self.ROWS)
		self.assertEqual([row["value_stream"] for row in out], ["Build", "Events", "Unallocated"])
		for row in out:
			self.assertNotIn("revenue", row)
			self.assertNotIn("avg_deal", row)
		self.assertIn("revenue", self.ROWS[0], "input unchanged")

	def test_chart_drops_the_revenue_series(self):
		vs = modules["value_stream"]
		chart = vs.get_chart(vs.without_revenue(self.ROWS), False)
		self.assertEqual([d["name"] for d in chart["data"]["datasets"]], ["Spend"])
		chart = vs.get_chart(self.ROWS)
		self.assertEqual([d["name"] for d in chart["data"]["datasets"]], ["Revenue", "Spend"])

	def test_execute_for_a_sales_manager_never_reads_billed_revenue(self):
		self.as_projects_user()
		columns, data, message, chart = modules["value_stream"].execute({})
		self.assertEqual(_billed_reads(), [])
		self.assertNotIn("revenue", [c["fieldname"] for c in columns])
		self.assertIn("Accounts Manager and System Manager only", message)

	def test_execute_for_an_accounts_manager_is_unchanged(self):
		self.as_accounts_manager()
		columns, data, message, chart = modules["value_stream"].execute({})
		self.assertEqual(len(_billed_reads()), 1)
		self.assertIn("revenue", [c["fieldname"] for c in columns])
		self.assertNotIn("Accounts Manager and System Manager only", message)


class MarketingReports(Base):
	def test_ad_spend_roas(self):
		report = modules["ad_spend_roas"]
		self.as_projects_user()
		columns, rows, _message = report.execute({})
		self.assertEqual(_billed_reads(), [])
		names = [c["fieldname"] for c in columns]
		self.assertNotIn("invoiced", names)
		self.assertNotIn("roas_invoiced", names)
		self.assertIn("roas_contract", names)
		self.assertIn("contract_value", names)

		STATE.sql.clear()
		self.as_accounts_manager()
		columns, rows, _message = report.execute({})
		self.assertEqual(len(_billed_reads()), 1)
		names = [c["fieldname"] for c in columns]
		self.assertIn("invoiced", names)
		self.assertIn("roas_invoiced", names)

	def test_ad_spend_roas_rows(self):
		rows = [
			{
				"platform": "Google",
				"spend": 10.0,
				"contract_value": 50.0,
				"invoiced": 40.0,
				"roas_contract": 5.0,
				"roas_invoiced": 4.0,
			}
		]
		out = modules["ad_spend_roas"].without_revenue(rows)
		self.assertEqual(
			out, [{"platform": "Google", "spend": 10.0, "contract_value": 50.0, "roas_contract": 5.0}]
		)
		self.assertIn("invoiced", rows[0])

	def test_social_post_performance(self):
		report = modules["social"]
		self.as_projects_user()
		columns, rows, _message = report.execute({})
		self.assertEqual(_billed_reads(), [])
		names = [c["fieldname"] for c in columns]
		self.assertNotIn("invoiced", names)
		self.assertIn("contract_value", names)

		self.as_accounts_manager()
		self.assertIn("invoiced", [c["fieldname"] for c in report.get_columns(True)])

	def test_social_opportunity_query_reads_billed_only_when_asked(self):
		report = modules["social"]
		report._opportunities({"post-1"}, with_billed=False)
		self.assertEqual(_billed_reads(), [])
		report._opportunities({"post-1"}, with_billed=True)
		self.assertEqual(len(_billed_reads()), 1)

	def test_marketing_performance_page_drops_invoiced(self):
		"""The page's ROAS block reads through Ad Spend ROAS; summed, a missing key becomes a zero."""
		source = (APP / "marketing" / "performance" / "api.py").read_text(encoding="utf-8")
		body = source[source.index("def _roas(") :]
		body = body[: body.index("\ndef ", 1)]
		self.assertIn("money.can_see_revenue()", body)
		self.assertRegex(body, r"keys = \([^)]*\"contract_value\"\)")
		self.assertRegex(body, r"if show_revenue:\s+keys \+= \(\"invoiced\",\)")
		self.assertRegex(body, r"if show_revenue:\s+p\[\"roas_invoiced\"\]")


# ----------------------------------------------------------------- other readers


class Reconciliation(Base):
	def test_value_reads_the_dollar_amount_and_never_the_missing_column(self):
		self.as_projects_user()
		modules["reconciliation"]._candidate_projects("CUST-1")
		query = STATE.sql[-1]
		self.assertIn("p.custom_project_dollar_amount", query)
		self.assertNotIn("custom_project_estimated_amount", query)
		self.assertNotIn("total_sales_amount", query)

	def test_sales_order_total_is_the_fallback_for_accounts_only(self):
		self.as_accounts_manager()
		modules["reconciliation"]._candidate_projects("CUST-1")
		self.assertIn("p.total_sales_amount", STATE.sql[-1])


class CreateProjectBroadcast(Base):
	def test_the_realtime_payload_is_not_the_whole_project(self):
		source = (APP / "crm_enhancements" / "api.py").read_text(encoding="utf-8")
		self.assertNotIn("project_doc = project.as_dict()", source)
		match = re.search(r"project_doc = \{(.*?)\n\t\t\t\}", source, re.S)
		self.assertIsNotNone(match)
		keys = set(re.findall(r'"(\w+)":', match.group(1)))
		self.assertEqual(keys, {"doctype", "name", "project_name", "custom_drive_folder_id"})


if __name__ == "__main__":
	unittest.main()
