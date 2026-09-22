"""Bench-free tests for the spend -> revenue join (marketing/core/roas.py) and its report.

TASK-2026-01477, with the decisions of 2026-09-22 pinned:

* a record finds its campaign by ``utm_id`` first, then ``gclid`` via Ad Click (A + D);
* paid records nothing can match are their own row, never free leads;
* cohorts by **lead month**, an Opportunity counted in the month the ad touched it;
* a 365-day attribution window, so a repeat customer's later deal does not count;
* **two** revenue columns: contract value (won amount) and invoiced (Project billed);
* a ratio over zero is None, never 0 or infinity.

``roas.py`` is standard library only, so it is imported with no stub.

Run: python -m unittest erpnext_enhancements.tests.test_ad_spend_roas -v
"""

import ast
import datetime
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.core import roas

REPORT_DIR = REPO_ROOT / "erpnext_enhancements" / "marketing" / "report" / "ad_spend_roas"
PAID = frozenset({"cpc", "ppc", "paid", "display"})

G1 = {
	"name": "ADCMP-G-1",
	"external_id": "21456789012",
	"campaign_name": "Search - Commercial",
	"platform": "Google Ads",
}
M1 = {
	"name": "ADCMP-M-1",
	"external_id": "120210000000000001",
	"campaign_name": "Lead Gen",
	"platform": "Meta Ads",
}
CLASH_G = {"name": "ADCMP-G-9", "external_id": "777", "campaign_name": "Google 777", "platform": "Google Ads"}
CLASH_L = {
	"name": "ADCMP-L-9",
	"external_id": "777",
	"campaign_name": "LinkedIn 777",
	"platform": "LinkedIn Ads",
}
CAMPAIGNS = [G1, M1, CLASH_G, CLASH_L]
CLICKS = {"Cj0-GCLID-1": "ADCMP-G-1"}


def lead(name, created, **attr):
	return {"name": name, "creation": created, **attr}


def opp(name, touched, created, status="Open", amount=0, project=None, billed=0, **attr):
	return {
		"name": name,
		"status": status,
		"creation": created,
		"custom_attribution_captured_on": touched,
		"opportunity_amount": amount,
		"project": project,
		"total_billed_amount": billed,
		**attr,
	}


class ResolveTests(unittest.TestCase):
	def setUp(self):
		self.by_id = roas.index_campaigns(CAMPAIGNS)
		self.by_name = {c["name"]: c for c in CAMPAIGNS}

	def resolve(self, **record):
		return roas.resolve(record, self.by_id, self.by_name, CLICKS)

	def test_utm_id_is_exact(self):
		self.assertEqual(self.resolve(custom_utm_id="21456789012"), (G1, roas.VIA_UTM_ID))

	def test_gclid_is_the_fallback(self):
		self.assertEqual(self.resolve(custom_gclid="Cj0-GCLID-1"), (G1, roas.VIA_GCLID))
		self.assertEqual(
			self.resolve(custom_utm_id="no-such-id", custom_gclid="Cj0-GCLID-1"), (G1, roas.VIA_GCLID)
		)

	def test_an_id_on_two_platforms_is_settled_by_utm_source_or_not_at_all(self):
		self.assertEqual(
			self.resolve(custom_utm_id="777", custom_utm_source="LinkedIn"), (CLASH_L, roas.VIA_UTM_ID)
		)
		self.assertEqual(
			self.resolve(custom_utm_id="777", custom_utm_source="newsletter"), (None, None), "never guessed"
		)

	def test_nothing_matches(self):
		self.assertEqual(self.resolve(custom_gclid="unknown"), (None, None))

	def test_paid_signals(self):
		self.assertTrue(roas.is_paid({"custom_gclid": "x"}, PAID))
		self.assertTrue(roas.is_paid({"custom_utm_id": "1"}, PAID))
		self.assertTrue(roas.is_paid({"custom_utm_medium": "CPC"}, PAID))
		self.assertTrue(roas.is_paid({"custom_lead_source": "Advertisement"}, PAID))
		self.assertFalse(
			roas.is_paid({"custom_utm_medium": "organic", "custom_lead_source": "Website"}, PAID)
		)


class BuildTests(unittest.TestCase):
	def build(self, spend=(), leads=(), opps=(), **kw):
		return roas.build(list(spend), list(leads), list(opps), CAMPAIGNS, CLICKS, paid_mediums=PAID, **kw)

	def row(self, rows, month, name):
		matches = [r for r in rows if r["month"] == month and r["campaign_name"] == name]
		self.assertEqual(len(matches), 1, f"{month} {name}: {rows}")
		return matches[0]

	def test_spend_rolls_up_by_campaign_month(self):
		rows, _ = self.build(
			spend=[
				{
					"campaign": "ADCMP-G-1",
					"metric_date": datetime.date(2026, 9, 1),
					"spend": 100,
					"impressions": 1000,
					"clicks": 40,
				},
				{
					"campaign": "ADCMP-G-1",
					"metric_date": datetime.date(2026, 9, 2),
					"spend": 50.5,
					"impressions": 500,
					"clicks": 20,
				},
				{
					"campaign": "ADCMP-G-1",
					"metric_date": datetime.date(2026, 10, 1),
					"spend": 10,
					"impressions": 1,
					"clicks": 1,
				},
			]
		)
		sep = self.row(rows, "2026-09", "Search - Commercial")
		self.assertEqual((sep["spend"], sep["impressions"], sep["clicks"]), (150.5, 1500, 60))

	def test_leads_are_joined_counted_and_the_gap_is_its_own_row(self):
		rows, coverage = self.build(
			leads=[
				lead("L1", "2026-09-03 10:00:00", custom_utm_id="21456789012"),
				lead("L2", "2026-09-04 10:00:00", custom_gclid="Cj0-GCLID-1"),
				lead("L3", "2026-09-05 10:00:00", custom_utm_medium="cpc"),  # paid, no key
				lead("L4", "2026-09-06 10:00:00", custom_utm_medium="organic"),  # not paid
			]
		)
		g = self.row(rows, "2026-09", "Search - Commercial")
		self.assertEqual((g["leads"], g["leads_via_utm_id"], g["leads_via_gclid"]), (2, 1, 1))
		gap = self.row(rows, "2026-09", roas.UNJOINABLE)
		self.assertEqual(gap["leads"], 1)
		self.assertEqual(
			coverage,
			{"paid_leads": 3, "joined_utm_id": 1, "joined_gclid": 1, "unjoinable": 1, "outside_window": 0},
		)

	def test_an_opportunity_counts_in_the_month_the_ad_touched_it(self):
		rows, _ = self.build(
			spend=[{"campaign": "ADCMP-G-1", "metric_date": "2026-09-10", "spend": 1000}],
			opps=[
				opp(
					"O1",
					"2026-09-12 09:00:00",
					"2026-11-02 09:00:00",
					status="Closed Won",
					amount=48000,
					project="PRJ-1",
					billed=12000,
					custom_utm_id="21456789012",
				),
				opp(
					"O2",
					"2026-09-20 09:00:00",
					"2026-10-01 09:00:00",
					status="Lost",
					amount=9000,
					custom_utm_id="21456789012",
				),
			],
		)
		sep = self.row(rows, "2026-09", "Search - Commercial")
		self.assertEqual((sep["opportunities"], sep["won"], sep["won_projects"]), (2, 1, 1))
		self.assertEqual((sep["contract_value"], sep["invoiced"]), (48000.0, 12000.0))
		self.assertEqual(
			(sep["roas_contract"], sep["roas_invoiced"], sep["cost_per_won_project"]), (48.0, 12.0, 1000.0)
		)
		self.assertFalse([r for r in rows if r["month"] == "2026-11"], "not in the month it closed")

	def test_won_without_a_project_counts_contract_but_not_invoiced(self):
		rows, _ = self.build(
			opps=[
				opp(
					"O1",
					"2026-09-01",
					"2026-09-15",
					status="Closed Won",
					amount=5000,
					custom_gclid="Cj0-GCLID-1",
				)
			]
		)
		r = self.row(rows, "2026-09", "Search - Commercial")
		self.assertEqual(
			(r["won"], r["won_projects"], r["contract_value"], r["invoiced"]), (1, 0, 5000.0, 0.0)
		)

	def test_the_attribution_window(self):
		rows, coverage = self.build(
			opps=[
				opp(
					"O1",
					"2025-01-10",
					"2026-09-01",
					status="Closed Won",
					amount=99000,
					custom_utm_id="21456789012",
				),
				opp(
					"O2",
					"2025-01-10",
					"2025-06-01",
					status="Closed Won",
					amount=10000,
					custom_utm_id="21456789012",
				),
			],
			window_days=365,
		)
		r = self.row(rows, "2025-01", "Search - Commercial")
		self.assertEqual(
			r["contract_value"], 10000.0, "the deal 20 months later is repeat business, not this ad"
		)
		self.assertEqual(coverage["outside_window"], 1)

	def test_ratios_over_zero_are_none(self):
		rows, _ = self.build(leads=[lead("L1", "2026-09-03", custom_utm_id="21456789012")])
		r = self.row(rows, "2026-09", "Search - Commercial")
		self.assertIsNone(r["roas_contract"])
		self.assertIsNone(r["cost_per_won_project"])
		self.assertEqual(r["cost_per_lead"], 0.0, "free leads cost nothing: 0 / 1 is a number")

	def test_group_by_platform(self):
		rows, _ = self.build(
			spend=[
				{"campaign": "ADCMP-G-1", "metric_date": "2026-09-01", "spend": 100},
				{"campaign": "ADCMP-G-9", "metric_date": "2026-09-01", "spend": 50},
				{"campaign": "ADCMP-M-1", "metric_date": "2026-09-01", "spend": 70},
			],
			group_by="platform",
		)
		by_platform = {r["platform"]: r["spend"] for r in rows}
		self.assertEqual(by_platform, {"Google Ads": 150.0, "Meta Ads": 70.0})


class ReportWiringTests(unittest.TestCase):
	def test_report_definition(self):
		doc = json.loads((REPORT_DIR / "ad_spend_roas.json").read_text(encoding="utf-8"))
		self.assertEqual(
			(doc["report_type"], doc["is_standard"], doc["module"]), ("Script Report", "Yes", "Marketing")
		)
		self.assertEqual(doc["ref_doctype"], "Ad Daily Metric")
		self.assertEqual({r["role"] for r in doc["roles"]}, {"System Manager", "Sales Manager"})
		self.assertEqual(doc["add_total_row"], 0, "a total row would sum the ratio columns")

	def test_no_print_template(self):
		# A report .html is compiled whole, comments included (CLAUDE.md).
		self.assertFalse((REPORT_DIR / "ad_spend_roas.html").exists())

	def test_paid_mediums_come_from_attribution(self):
		source = (REPORT_DIR / "ad_spend_roas.py").read_text(encoding="utf-8")
		self.assertIn("from erpnext_enhancements.crm_enhancements.attribution import PAID_MEDIUMS", source)

	def test_no_sql_function_strings_in_get_all(self):
		# Frappe 16 refuses fields=["count(name) as n"] (CLAUDE.md); aggregates live in roas.py.
		tree = ast.parse((REPORT_DIR / "ad_spend_roas.py").read_text(encoding="utf-8"))
		for node in ast.walk(tree):
			if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("get_all", "get_list"):
				for kw in node.keywords:
					if kw.arg == "fields":
						for elt in getattr(kw.value, "elts", []):
							self.assertNotIn(
								"(", getattr(elt, "value", ""), "a SQL function in get_all fields"
							)


if __name__ == "__main__":
	unittest.main()
