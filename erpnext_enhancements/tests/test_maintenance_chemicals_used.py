# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests for the Maintenance Chemicals Used report (``sapphire_maintenance/chemical_usage.py``).

TASK-2026-02480 (B3 of the 2026-10-12 Maintenance tools plan). A visit records a chemical in a
*dose unit* of its template line (a tablet, a cap full, a tablespoon), fractional, with no
conversion to the stock unit, so the report must:

* group by site x month x item x unit and **never add two units together**, nor convert;
* keep chlorine tablet sizes (1" and 3", two Items) apart;
* place a visit in a month by the day the work was done, with the right month edges;
* count only visits that really happened, by the *Visit state* filter (default: approved);
* default to the last three full months plus the current one;
* produce nothing for a visit with no chemicals.

Runs the real module over an in-memory ``frappe`` stub installed in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_maintenance_chemicals_used
"""

import datetime
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

PACKAGE = REPO_ROOT / "erpnext_enhancements" / "sapphire_maintenance"
REPORT_DIR = PACKAGE / "report" / "maintenance_chemicals_used"
D = datetime.date
TODAY = D(2026, 10, 23)
STUBBED = (
	"frappe",
	"frappe.utils",
	"erpnext_enhancements.sapphire_maintenance.chemical_usage",
	"erpnext_enhancements.sapphire_maintenance.item_sizes",
)

usage = None
TABLES = {}
SQL_CALLS = []
SQL_ROWS = []
_saved = {}


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Throw(Exception):
	pass


def _getdate(value=None):
	if value is None:
		return TODAY
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, D):
		return value
	return D.fromisoformat(str(value)[:10])


def _get_all(doctype, filters=None, fields=None, **kwargs):
	wanted = (filters or {}).get("name", [None, None])[1]
	rows = [row for row in TABLES.get(doctype, []) if wanted is None or row["name"] in wanted]
	return [_Dict({f: row.get(f) for f in fields or ["name"]}) for row in rows]


def _sql(query, params=None, as_dict=False):
	SQL_CALLS.append((query, params))
	return [_Dict(row) for row in SQL_ROWS]


def _forget_package_attributes():
	import erpnext_enhancements.sapphire_maintenance as package

	for attr in ("chemical_usage", "item_sizes"):
		if attr in vars(package):
			delattr(package, attr)


def setUpModule():
	global usage
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	_forget_package_attributes()

	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe.get_all = _get_all

	def throw(message, *a, **k):
		raise _Throw(message)

	frappe.throw = throw
	frappe.db = types.SimpleNamespace(sql=_sql, has_column=lambda doctype, column: True)

	utils = types.ModuleType("frappe.utils")
	utils.getdate = _getdate
	utils.nowdate = lambda: str(TODAY)
	frappe.utils = utils
	sys.modules.update({"frappe": frappe, "frappe.utils": utils})

	from erpnext_enhancements.sapphire_maintenance import chemical_usage

	usage = chemical_usage


def tearDownModule():
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]
	_forget_package_attributes()


def line(visit, item, qty, uom, day, docstatus=1, state="Final/Submitted", project="PRJ-1"):
	return {
		"visit": visit,
		"project": project,
		"item": item,
		"uom": uom,
		"qty": qty,
		"docstatus": docstatus,
		"workflow_state": state,
		"day": day,
	}


class _Base(unittest.TestCase):
	def setUp(self):
		TABLES.clear()
		SQL_CALLS.clear()
		SQL_ROWS.clear()
		TABLES["Item"] = [
			{
				"name": "CHLORINE 1IN",
				"stock_uom": "Nos",
				"custom_size_family": "Chlorine",
				"custom_size": '1"',
			},
			{
				"name": "CHLORINE 3IN",
				"stock_uom": "Nos",
				"custom_size_family": "Chlorine",
				"custom_size": '3"',
			},
			{"name": "ALGAECIDE", "stock_uom": "Bottle", "custom_size_family": "", "custom_size": ""},
			{"name": "PUMP SEAL", "stock_uom": "Nos", "custom_size_family": "", "custom_size": ""},
		]
		TABLES["Project"] = [{"name": "PRJ-1", "project_name": "Hardware Apartments"}]

	def rows(self, lines, state=None):
		items = {x["item"] for x in lines}
		info = usage._item_info(items)
		rows, left_out = usage.build_rows(lines, state or usage.DEFAULT_STATE, info, {"PRJ-1": "Hardware"})
		self.rows_left_out = left_out
		return rows


class GroupingTests(_Base):
	def test_different_units_of_one_item_are_never_added(self):
		rows = self.rows(
			[
				line("V1", "ALGAECIDE", 3, "cap full", D(2026, 9, 3)),
				line("V2", "ALGAECIDE", 2, "tablespoon", D(2026, 9, 10)),
			]
		)
		self.assertEqual(
			sorted((r["uom"], r["qty"]) for r in rows),
			[("cap full", 3.0), ("tablespoon", 2.0)],
		)

	def test_same_unit_is_added_ignoring_case_and_spacing(self):
		rows = self.rows(
			[
				line("V1", "ALGAECIDE", 1.5, "Cap Full", D(2026, 9, 3)),
				line("V2", "ALGAECIDE", 0.25, "cap  full ", D(2026, 9, 10)),
				line("V3", "ALGAECIDE", 1, "Cap Full", D(2026, 9, 17)),
			]
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["qty"], 2.75)
		self.assertEqual(rows[0]["uom"], "Cap Full")  # the spelling used most often
		self.assertEqual(rows[0]["visits"], 3)
		self.assertEqual(rows[0]["last_visit"], D(2026, 9, 17))

	def test_tablet_sizes_stay_apart_and_show_their_size(self):
		rows = self.rows(
			[
				line("V1", "CHLORINE 1IN", 4, "tablet", D(2026, 9, 3)),
				line("V2", "CHLORINE 3IN", 2, "tablet", D(2026, 9, 3)),
				line("V3", "CHLORINE 3IN", 1, "tablet", D(2026, 9, 12)),
			]
		)
		by_item = {r["item"]: r for r in rows}
		self.assertEqual(by_item["CHLORINE 1IN"]["qty"], 4.0)
		self.assertEqual(by_item["CHLORINE 1IN"]["size"], '1"')
		self.assertEqual(by_item["CHLORINE 3IN"]["qty"], 3.0)
		self.assertEqual(by_item["CHLORINE 3IN"]["size"], '3"')
		self.assertEqual((by_item["CHLORINE 1IN"]["visits"], by_item["CHLORINE 3IN"]["visits"]), (1, 2))

	def test_an_item_with_no_size_has_a_blank_size(self):
		rows = self.rows([line("V1", "ALGAECIDE", 1, "cap full", D(2026, 9, 3))])
		self.assertEqual(rows[0]["size"], "")

	def test_a_line_with_no_unit_is_counted_in_the_items_own_unit(self):
		rows = self.rows(
			[
				line("V1", "PUMP SEAL", 1, "", D(2026, 9, 3)),
				line("V2", "PUMP SEAL", 2, None, D(2026, 9, 4)),
				line("V3", "PUMP SEAL", 1, "Nos", D(2026, 9, 5)),
			]
		)
		self.assertEqual([(r["uom"], r["qty"], r["visits"]) for r in rows], [("Nos", 4.0, 3)])

	def test_sites_and_items_are_separate_rows_and_visits_are_distinct(self):
		rows = self.rows(
			[
				line("V1", "ALGAECIDE", 1, "cap full", D(2026, 9, 3), project="PRJ-1"),
				line("V1", "ALGAECIDE", 1, "cap full", D(2026, 9, 3), project="PRJ-1"),
				line("V2", "ALGAECIDE", 1, "cap full", D(2026, 9, 4), project="PRJ-2"),
			]
		)
		self.assertEqual(len(rows), 2)
		first = next(r for r in rows if r["project"] == "PRJ-1")
		self.assertEqual((first["qty"], first["visits"]), (2.0, 1))  # two lines, one visit
		self.assertEqual(first["site_name"], "Hardware")

	def test_float_noise_is_rounded_but_a_quarter_dose_survives(self):
		rows = self.rows(
			[
				line("V1", "ALGAECIDE", 0.1, "cap full", D(2026, 9, 3)),
				line("V2", "ALGAECIDE", 0.2, "cap full", D(2026, 9, 4)),
			]
		)
		self.assertEqual(rows[0]["qty"], 0.3)
		self.assertEqual(
			self.rows([line("V1", "ALGAECIDE", 0.25, "cap full", D(2026, 9, 3))])[0]["qty"], 0.25
		)


class MonthTests(_Base):
	def test_a_visit_lands_in_the_month_of_its_day(self):
		rows = self.rows(
			[
				line("V1", "ALGAECIDE", 1, "cap full", D(2026, 8, 31)),
				line("V2", "ALGAECIDE", 1, "cap full", D(2026, 9, 1)),
				line("V3", "ALGAECIDE", 1, "cap full", D(2026, 9, 30)),
				line("V4", "ALGAECIDE", 1, "cap full", D(2026, 10, 1)),
			]
		)
		self.assertEqual(
			[(r["month"], r["qty"], r["last_visit"]) for r in rows],
			[
				("2026-08", 1.0, D(2026, 8, 31)),
				("2026-09", 2.0, D(2026, 9, 30)),
				("2026-10", 1.0, D(2026, 10, 1)),
			],
		)

	def test_month_arithmetic_crosses_a_year(self):
		self.assertEqual(usage.shift_months(D(2026, 1, 15), -3), D(2025, 10, 1))
		self.assertEqual(usage.shift_months(D(2026, 12, 31), 1), D(2027, 1, 1))
		self.assertEqual(usage.last_of_month(D(2028, 2, 3)), D(2028, 2, 29))
		self.assertEqual(usage.month_key(D(2026, 3, 9)), "2026-03")


class WindowTests(_Base):
	def test_default_is_three_full_months_plus_the_current_one(self):
		self.assertEqual(usage.default_months(D(2026, 10, 23)), (D(2026, 7, 1), D(2026, 10, 31)))
		self.assertEqual(usage.default_months(D(2026, 1, 1)), (D(2025, 10, 1), D(2026, 1, 31)))
		self.assertEqual(usage.default_months(D(2026, 12, 31)), (D(2026, 9, 1), D(2026, 12, 31)))

	def test_no_filters_use_the_default_window(self):
		self.assertEqual(usage.resolve_months({}), (D(2026, 7, 1), D(2026, 10, 31)))
		usage.get_usage({})
		_, params = SQL_CALLS[-1]
		self.assertEqual((params["start"], params["end"]), (D(2026, 7, 1), D(2026, 10, 31)))

	def test_any_day_of_a_month_snaps_to_the_whole_month(self):
		self.assertEqual(
			usage.resolve_months({"from_month": "2026-02-17", "to_month": "2026-04-02"}),
			(D(2026, 2, 1), D(2026, 4, 30)),
		)

	def test_a_start_after_the_end_is_refused(self):
		with self.assertRaises(_Throw):
			usage.resolve_months({"from_month": "2026-09-01", "to_month": "2026-07-31"})

	def test_one_missing_end_takes_its_default(self):
		self.assertEqual(usage.resolve_months({"from_month": "2026-09-09"}), (D(2026, 9, 1), D(2026, 10, 31)))


class StateTests(_Base):
	def setUp(self):
		super().setUp()
		self.lines = [
			line("APPROVED", "ALGAECIDE", 1, "cap full", D(2026, 9, 3), 1, "Final/Submitted"),
			line("WAITING", "ALGAECIDE", 10, "cap full", D(2026, 9, 4), 0, "Pending Review"),
			line("DRAFT", "ALGAECIDE", 100, "cap full", D(2026, 9, 5), 0, "Draft"),
			line("CANCELED", "ALGAECIDE", 1000, "cap full", D(2026, 9, 6), 2, "Final/Submitted"),
		]

	def total(self, state):
		return sum(r["qty"] for r in self.rows(self.lines, state))

	def test_default_counts_only_approved_visits(self):
		self.assertEqual(usage.DEFAULT_STATE, "Approved")
		self.assertEqual(self.total("Approved"), 1.0)

	def test_waiting_for_review_adds_pending_review_visits_only(self):
		self.assertEqual(self.total("Approved or waiting for review"), 11.0)

	def test_drafts_option_adds_drafts_but_never_canceled_visits(self):
		self.assertEqual(self.total("Including drafts"), 111.0)

	def test_an_unknown_state_cannot_widen_the_report(self):
		TABLES["Item"] = [{"name": "ALGAECIDE", "stock_uom": "Bottle"}]
		SQL_ROWS.extend(self.lines)
		rows, _message = usage.get_usage({"state": "everything"})
		self.assertEqual(sum(r["qty"] for r in rows), 1.0)

	def test_what_the_state_hid_is_reported(self):
		self.rows(self.lines, "Approved")
		self.assertEqual(self.rows_left_out, {"waiting": 1, "drafts": 1})
		message = usage.left_out_message(self.rows_left_out, "Approved")
		self.assertIn("1 waiting for review", message)
		self.assertIn("1 unsent drafts", message)
		self.rows(self.lines, "Including drafts")
		self.assertIsNone(usage.left_out_message(self.rows_left_out, "Including drafts"))


class NoChemicalsTests(_Base):
	def test_a_visit_with_no_chemicals_makes_no_row(self):
		self.assertEqual(self.rows([]), [])
		rows, message = usage.get_usage({})
		self.assertEqual((rows, message), ([], None))

	def test_the_query_leaves_out_zero_quantity_lines_and_canceled_visits(self):
		usage.get_usage({"project": "PRJ-1", "item": "ALGAECIDE"})
		query, params = SQL_CALLS[-1]
		self.assertIn("c.qty > 0", query)
		self.assertIn("r.docstatus < 2", query)
		self.assertEqual(params["project"], "PRJ-1")
		self.assertEqual(params["item"], "ALGAECIDE")

	def test_values_are_bound_not_pasted_into_the_query(self):
		usage.get_usage({"project": "x'; DROP TABLE tabItem; --", "item": "y"})
		query, params = SQL_CALLS[-1]
		self.assertNotIn("DROP", query)
		self.assertIn("%(project)s", query)
		self.assertIn("%(start)s", query)
		self.assertNotIn("sum(", query.lower())  # grouped in Python, not by an aggregate string


class DeclarationTests(unittest.TestCase):
	"""The report's files agree with the module and carry no price."""

	def test_js_state_options_match_the_modules(self):
		source = (REPORT_DIR / "maintenance_chemicals_used.js").read_text(encoding="utf-8")
		for state in usage.STATES:
			self.assertIn(f'"{state}"', source)
		self.assertIn(f'default: "{usage.DEFAULT_STATE}"', source)
		self.assertIn("add_months(frappe.datetime.month_start(), -3)", source)

	def test_roles_are_the_maintenance_report_roles_plus_finance(self):
		report = json.loads((REPORT_DIR / "maintenance_chemicals_used.json").read_text(encoding="utf-8"))
		missed = json.loads(
			(PACKAGE / "report" / "missed_maintenance_visits" / "missed_maintenance_visits.json").read_text(
				encoding="utf-8"
			)
		)
		roles = {r["role"] for r in report["roles"]}
		self.assertEqual(roles, {r["role"] for r in missed["roles"]} | {"Finance Team"})
		self.assertEqual(report["report_type"], "Script Report")
		self.assertEqual(report["name"], "Maintenance Chemicals Used")

	def test_finance_team_can_report_on_the_reports_doctype(self):
		# Frappe's get_report_doc demands "report" on ref_doctype as well as a role on the report.
		record = json.loads(
			(
				PACKAGE / "doctype" / "sapphire_maintenance_record" / "sapphire_maintenance_record.json"
			).read_text(encoding="utf-8")
		)
		perms = [p for p in record["permissions"] if p["role"] == "Finance Team"]
		self.assertEqual(len(perms), 1)
		self.assertTrue(perms[0]["report"] and perms[0]["read"])
		self.assertFalse(any(perms[0].get(x) for x in ("write", "create", "delete", "submit")))

	def test_columns_fit_a_phone_and_carry_no_money(self):
		source = (REPORT_DIR / "maintenance_chemicals_used.py").read_text(encoding="utf-8")
		self.assertNotIn("Currency", source)
		self.assertNotIn("Cost", source)
		import re

		widths = [int(w) for w in re.findall(r'"width": (\d+)', source)]
		self.assertEqual(len(widths), 8)
		self.assertLessEqual(sum(widths), 900)

	def test_the_workspace_lists_the_report(self):
		workspace = json.loads(
			(PACKAGE / "workspace" / "sapphire_maintenance" / "sapphire_maintenance.json").read_text(
				encoding="utf-8"
			)
		)
		links = workspace["links"]
		tools = next(i for i, l in enumerate(links) if l.get("label") == "Tools")
		after = []
		for link in links[tools + 1 :]:
			if link["type"] == "Card Break":
				break
			after.append(link)
		self.assertEqual(links[tools]["link_count"], len(after))
		self.assertIn(
			("Report", "Maintenance Chemicals Used"), [(l["link_type"], l["link_to"]) for l in after]
		)
		# Workspace JSON is timestamp-gated on import: an unbumped stamp never reaches the site.
		self.assertGreaterEqual(workspace["modified"], "2026-10-23")


if __name__ == "__main__":
	unittest.main()
