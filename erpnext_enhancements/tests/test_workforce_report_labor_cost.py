"""The Labor Cost Analysis report's contracts, read statically (v1.480.0).

Three things fail silently if they drift, and each is pinned here:

* **Roles.** A Script Report is readable by the roles on the Report record, but frappe also
  requires `read` on its ``ref_doctype``. So the report's roles must be a subset of the roles
  that can read Job Interval at permlevel 0 — and, because finance is the audience, must
  include Accounts Manager. `Training Completion Matrix` listed HR Manager while the
  doctype granted HR Manager nothing, and errored for exactly its intended reader. The
  opposite bound matters as much: the report's raw SQL applies no permlevel, so its roles
  must also be a subset of Job Interval's **permlevel-1** readers, or it hands the pay block
  to someone the doctype hides it from. Projects Manager was exactly that until v1.538.0.
* **Groupings.** The JS `group_by` Select offers a list; the Python refuses anything outside
  ``GROUP_BY``. A grouping offered but refused is a report that errors for the reader who
  picked it, so the two lists are compared verbatim.
* **Money columns are Currency.** A cost column typed Float renders with no currency and
  formats 1234.5 as "1234.5" in an export finance forwards.

Run: python -m unittest erpnext_enhancements.tests.test_workforce_report_labor_cost
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPORT_DIR = APP / "workforce" / "report" / "labor_cost_analysis"
REPORT_JSON = REPORT_DIR / "labor_cost_analysis.json"
REPORT_PY = REPORT_DIR / "labor_cost_analysis.py"
REPORT_JS = REPORT_DIR / "labor_cost_analysis.js"
JOB_INTERVAL_JSON = APP / "workforce" / "doctype" / "job_interval" / "job_interval.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _module_constant(name):
    """A module-level literal assignment, without importing the module (it imports frappe)."""
    tree = ast.parse(_text(REPORT_PY))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a module-level literal in {REPORT_PY.name}")


class TestReportRecord(unittest.TestCase):
    def test_it_is_a_standard_script_report_on_job_interval_in_workforce(self):
        record = json.loads(_text(REPORT_JSON))
        self.assertEqual(record["report_type"], "Script Report")
        self.assertEqual(record["ref_doctype"], "Job Interval")
        self.assertEqual(record["module"], "Workforce")
        self.assertEqual(record["is_standard"], "Yes")
        self.assertEqual(record["name"], "Labor Cost Analysis")

    def test_roles_can_read_job_interval_and_include_finance(self):
        record = json.loads(_text(REPORT_JSON))
        report_roles = {r["role"] for r in record["roles"]}
        interval = json.loads(_text(JOB_INTERVAL_JSON))
        readers = {
            p["role"]
            for p in interval["permissions"]
            if p.get("read") and not p.get("permlevel")
        }
        self.assertTrue(report_roles, "the report grants no roles at all")
        self.assertLessEqual(
            report_roles, readers, f"report roles not readers of Job Interval: {report_roles - readers}"
        )
        self.assertIn("Accounts Manager", report_roles)
        self.assertNotIn("Employee", report_roles, "every staff account holds Employee; this is a pay report")
        pay_readers = {p["role"] for p in interval["permissions"] if p.get("read") and p.get("permlevel") == 1}
        self.assertLessEqual(
            report_roles,
            pay_readers,
            f"a raw-SQL report must not be wider than the pay block it reads: {report_roles - pay_readers}",
        )
        self.assertNotIn(
            "Projects Manager", report_roles, "Projects Manager reads Job Interval but not its permlevel-1 pay block"
        )


class TestGroupings(unittest.TestCase):
    def test_js_offers_exactly_the_groupings_the_server_accepts(self):
        group_by = list(_module_constant("GROUP_BY"))
        js = _text(REPORT_JS)
        match = re.search(r'fieldname:\s*"group_by".*?options:\s*"([^"]+)"', js, flags=re.S)
        self.assertIsNotNone(match, "no group_by Select with an options string in the JS")
        offered = match.group(1).split("\\n")
        self.assertEqual(offered, group_by)

    def test_every_grouping_has_group_keys_and_a_chart_label(self):
        source = _text(REPORT_PY)
        for grouping in _module_constant("GROUP_BY"):
            self.assertIn(f'"{grouping}":', source, f"{grouping!r} has no _GROUP_KEYS / chart label entry")

    def test_column_groupings_name_real_groupings(self):
        group_by = set(_module_constant("GROUP_BY"))
        for fieldname, _label, _type, _options, _width, groupings in _module_constant("COLUMNS"):
            for grouping in groupings:
                self.assertIn(grouping, group_by, f"column {fieldname} names unknown grouping {grouping!r}")


class TestColumns(unittest.TestCase):
    MONEY = {"avg_rate", "labor_cost", "straight_pay", "budget", "variance"}

    def test_money_columns_are_currency(self):
        columns = {c[0]: c for c in _module_constant("COLUMNS")}
        for fieldname in self.MONEY:
            self.assertIn(fieldname, columns)
            self.assertEqual(columns[fieldname][2], "Currency", f"{fieldname} is not Currency")

    def test_budget_columns_appear_only_when_grouped_by_project(self):
        columns = {c[0]: c for c in _module_constant("COLUMNS")}
        for fieldname in ("budget", "variance"):
            self.assertEqual(columns[fieldname][5], ("Project",))

    def test_unrated_is_counted_not_hidden(self):
        columns = {c[0]: c for c in _module_constant("COLUMNS")}
        self.assertIn("unrated", columns)
        self.assertEqual(columns["unrated"][5], (), "unrated must appear in every grouping")

    def test_the_query_reads_the_stamped_cost_not_a_live_rate(self):
        source = _text(REPORT_PY)
        self.assertIn("ji.labor_cost", source)
        self.assertIn("ji.pay_rate", source)
        for live in ("tabEmployee Pay Rate", "tabActivity Cost", "rate_for("):
            self.assertNotIn(live, source, f"the report must not re-price history from {live}")


if __name__ == "__main__":
    unittest.main()
