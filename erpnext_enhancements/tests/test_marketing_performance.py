"""Bench-free tests for the Marketing Performance page and the data behind it (v1.756.0).

What is held:

* **URLs.** ``rules.normalize_page`` makes GA4's paths and Search Console's URLs one key: host,
  scheme, trailing slash, query string, fragment and case ignored; the root stays ``/``.
* **The join.** Pages are summed per path, a source that does not know a page reads None (never
  0), average position is weighted by impressions, and the three flags fire at their thresholds.
* **Queries and channels.** The branded split, rates, and the funnel's blanks.
* **Windows.** The nightly restate window (catch-up, cap, nothing to do), the page's range, the
  previous range, chunking, and record names that are deterministic and short.
* **API rows.** GA4 and Search Console rows parsed into what is stored.
* **Calls.** First-time callers by number, a call with no number never first-time, an Opportunity
  credited to the latest call before it within 30 days, and never to a later call.
* **Roles.** Money only for System Manager, Sales Manager, Marketing Manager; who called only for
  System Manager and Sales Manager; the page JSON admits exactly the page roles.
* **Google Business Profile.** GET-only allowlist on its four hosts, the scope confined to its own
  constants, the daily-metrics parser (a missing value is 0, desktop and mobile summed, a
  month-level point skipped), reviews summarised, the query Google's example writes.
* **Social page stats.** Each network's parser, LinkedIn's time-bound query, followers written on
  tonight's row only, and the one new allowlist read is a GET.
* **The page.** Its logic under node: the range from the address, the address from the range,
  presets, deltas, chart segments broken at missing days; and that only a chosen range writes
  history.

Standard library only, no ``frappe`` import, so it collects under ``python -m unittest`` with no stub.
Run: python -m unittest erpnext_enhancements.tests.test_marketing_performance -v
"""

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MARKETING = APP / "marketing"
PAGE_DIR = MARKETING / "page" / "marketing_performance"

from erpnext_enhancements.marketing.gbp import constants as G
from erpnext_enhancements.marketing.gbp import parse as gbp
from erpnext_enhancements.marketing.performance import rules
from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import page_stats as social

D = dt.date


class TestNormalizePage(unittest.TestCase):
	def test_ga4_and_search_console_forms_meet(self):
		for raw in (
			"https://www.sapphirefountains.com/Fountains/",
			"http://sapphirefountains.com/fountains?utm_source=x",
			"/fountains/",
			"/fountains",
			"//www.sapphirefountains.com/fountains/#top",
			"www.sapphirefountains.com/fountains",
			"https://sapphirefountains.com//fountains//",
		):
			with self.subTest(raw=raw):
				self.assertEqual(rules.normalize_page(raw), "/fountains")

	def test_the_root_and_the_blanks(self):
		self.assertEqual(rules.normalize_page("https://sapphirefountains.com"), "/")
		self.assertEqual(rules.normalize_page("https://sapphirefountains.com/?gclid=1"), "/")
		self.assertEqual(rules.normalize_page("/"), "/")
		self.assertEqual(rules.normalize_page(""), "")
		self.assertEqual(rules.normalize_page(None), "")
		self.assertEqual(rules.normalize_page("(not set)"), "(not set)")

	def test_a_relative_path_gains_its_slash(self):
		self.assertEqual(rules.normalize_page("contact-us/"), "/contact-us")


class TestJoin(unittest.TestCase):
	def test_pages_join_and_sum_per_path(self):
		ga = [
			{
				"value": "/fountains/",
				"sessions": 40,
				"total_users": 30,
				"engaged_sessions": 20,
				"key_events": 2,
			},
			{"value": "/Fountains", "sessions": 10, "total_users": 9, "engaged_sessions": 5, "key_events": 1},
			{"value": "/only-ga4", "sessions": 5, "total_users": 5, "engaged_sessions": 1, "key_events": 0},
		]
		gsc = [
			{
				"value": "https://www.sapphirefountains.com/fountains/",
				"clicks": 10,
				"impressions": 100,
				"position": 4.0,
			},
			{
				"value": "https://sapphirefountains.com/fountains?x=1",
				"clicks": 0,
				"impressions": 300,
				"position": 12.0,
			},
			{
				"value": "https://www.sapphirefountains.com/only-gsc/",
				"clicks": 1,
				"impressions": 20,
				"position": 30.0,
			},
		]
		rows = {r["path"]: r for r in rules.join_landing_pages(ga, gsc)}
		f = rows["/fountains"]
		self.assertEqual((f["sessions"], f["key_events"], f["clicks"], f["impressions"]), (50, 3, 10, 400))
		self.assertAlmostEqual(f["position"], (4 * 100 + 12 * 300) / 400)
		self.assertAlmostEqual(f["ctr"], 10 / 400)
		self.assertAlmostEqual(f["key_event_rate"], 3 / 50)
		# A page one source does not know: None, never 0.
		self.assertIsNone(rows["/only-ga4"]["clicks"])
		self.assertIsNone(rows["/only-ga4"]["ctr"])
		self.assertIsNone(rows["/only-gsc"]["sessions"])
		self.assertEqual(next(iter(rows)), "/fountains", "busiest first")

	def test_not_set_is_not_a_page(self):
		rows = rules.join_landing_pages([{"value": "(not set)", "sessions": 500, "key_events": 0}], [])
		self.assertEqual(rows, [])

	def test_flags(self):
		self.assertEqual(rules.search_flags(2, 200, 3.0), [rules.FLAG_LOW_CTR])
		self.assertEqual(rules.search_flags(3, 200, 3.0), [])  # 1.5% exactly is not low
		self.assertEqual(rules.search_flags(2, 199, 3.0), [])  # too few impressions to judge
		self.assertEqual(rules.search_flags(10, 50, 8.0), [rules.FLAG_STRIKING])
		self.assertEqual(rules.search_flags(10, 50, 20.0), [rules.FLAG_STRIKING])
		self.assertEqual(rules.search_flags(10, 50, 20.1), [])
		self.assertEqual(rules.search_flags(10, 49, 10.0), [])
		self.assertEqual(rules.search_flags(1, 1000, 10.0), [rules.FLAG_LOW_CTR, rules.FLAG_STRIKING])
		rows = rules.join_landing_pages(
			[
				{"value": "/a", "sessions": 30, "key_events": 0},
				{"value": "/b", "sessions": 29, "key_events": 0},
				{"value": "/c", "sessions": 300, "key_events": 1},
			],
			[],
		)
		flagged = {r["path"] for r in rules.flagged(rows, rules.FLAG_NO_KEY_EVENTS)}
		self.assertEqual(flagged, {"/a"})


class TestQueriesChannelsFunnel(unittest.TestCase):
	def test_branded_split(self):
		terms = rules.branded_terms("Sapphire\nsapphire fountains, SAPPHIRE")
		self.assertEqual(terms, ("sapphire", "sapphire fountains"))
		self.assertEqual(rules.branded_terms(""), rules.DEFAULT_BRANDED_TERMS)
		self.assertTrue(rules.is_branded("Sapphire Fountains Utah", terms))
		self.assertTrue(rules.is_branded("saph sapphirefountains", ("sapphire fountains",)))
		self.assertFalse(rules.is_branded("fountain repair near me", terms))
		out = rules.summarize_queries(
			[
				{"value": "sapphire fountains", "clicks": 5, "impressions": 10, "position": 1.0},
				{"value": "sapphire fountains", "clicks": 5, "impressions": 30, "position": 2.0},
				{"value": "pond fountain", "clicks": 1, "impressions": 100, "position": 15.0},
			],
			terms,
		)
		self.assertEqual(out["split"]["branded"]["clicks"], 10)
		self.assertEqual(out["split"]["unbranded"]["impressions"], 100)
		first = out["rows"][0]
		self.assertEqual(first["query"], "sapphire fountains")
		self.assertAlmostEqual(first["position"], (10 * 1 + 30 * 2) / 40)
		self.assertIn(rules.FLAG_STRIKING, out["rows"][1]["flags"])

	def test_channels(self):
		out = rules.summarize_channels(
			[
				{"value": "Organic Search", "sessions": 30, "engaged_sessions": 20, "key_events": 3},
				{"value": "Organic Search", "sessions": 10, "engaged_sessions": 5, "key_events": 1},
				{"value": "Direct", "sessions": 60, "engaged_sessions": 10, "key_events": 0},
			]
		)
		self.assertEqual([c["channel"] for c in out], ["Direct", "Organic Search"])
		self.assertAlmostEqual(out[1]["share"], 0.4)
		self.assertAlmostEqual(out[1]["key_event_rate"], 0.1)

	def test_funnel_blank_is_not_zero(self):
		steps = rules.funnel(None, 12, 10, 2)
		self.assertIsNone(steps[0]["value"])
		self.assertIsNone(steps[1]["rate"], "no rate out of a missing step")
		self.assertAlmostEqual(steps[3]["rate"], 0.2)
		self.assertAlmostEqual(rules.funnel(1000, 12, 10, 2)[1]["rate"], 0.012)

	def test_unsourced(self):
		unknown = "Unknown (pre-Aug 2026)"
		self.assertTrue(rules.is_unsourced("", unknown))
		self.assertTrue(rules.is_unsourced(None, unknown))
		self.assertTrue(rules.is_unsourced(unknown, unknown))
		self.assertFalse(rules.is_unsourced("Website", unknown))

	def test_trend_gaps_outside_coverage(self):
		days = rules.days_between(D(2026, 6, 24), D(2026, 6, 28))
		out = rules.trend(days, {"s": {D(2026, 6, 27): 5}}, {"s": (D(2026, 6, 26), D(2026, 6, 28))})
		self.assertEqual(out["series"]["s"], [None, None, 0, 5, 0])
		self.assertEqual(out["dates"][0], "2026-06-24")


class TestWindowsAndNames(unittest.TestCase):
	TODAY = D(2026, 10, 29)

	def test_nightly_window(self):
		# Nothing stored: the initial days, ending yesterday.
		self.assertEqual(rules.nightly_window(None, self.TODAY, 7, 30, 90), (D(2026, 9, 29), D(2026, 10, 28)))
		# Up to date: the last 7 stored days restated.
		self.assertEqual(
			rules.nightly_window(D(2026, 10, 28), self.TODAY, 7, 30, 90), (D(2026, 10, 22), D(2026, 10, 28))
		)
		# A missed week: from 7 before the newest stored day, through yesterday.
		self.assertEqual(
			rules.nightly_window(D(2026, 10, 20), self.TODAY, 7, 30, 90), (D(2026, 10, 14), D(2026, 10, 28))
		)
		# Months missed: capped at 90 days, the backfill does the rest.
		self.assertEqual(
			rules.nightly_window(D(2026, 1, 1), self.TODAY, 7, 30, 90), (D(2026, 7, 31), D(2026, 10, 28))
		)
		# A two-day lag: the newest settled day restated, and nothing when that is already past it.
		self.assertEqual(
			rules.nightly_window(D(2026, 10, 27), self.TODAY, 1, 30, 90, lag_days=2),
			(D(2026, 10, 27), D(2026, 10, 27)),
		)
		self.assertIsNone(rules.nightly_window(D(2026, 10, 28), self.TODAY, 1, 30, 90, lag_days=2))

	def test_parse_range(self):
		self.assertEqual(rules.parse_range(None, None, self.TODAY), (D(2026, 10, 1), D(2026, 10, 28)))
		self.assertEqual(
			rules.parse_range("2026-10-10", "2026-10-01", self.TODAY), (D(2026, 10, 1), D(2026, 10, 10))
		)
		self.assertEqual(rules.parse_range("junk", "2026-11-30", self.TODAY)[1], self.TODAY)
		start, end = rules.parse_range("2020-01-01", "2026-10-28", self.TODAY)
		self.assertEqual((end - start).days + 1, rules.MAX_RANGE_DAYS)
		self.assertEqual(
			rules.previous_range(D(2026, 10, 1), D(2026, 10, 28)), (D(2026, 9, 3), D(2026, 9, 30))
		)

	def test_chunks(self):
		self.assertEqual(
			rules.chunks(D(2026, 1, 1), D(2026, 3, 5), 31),
			[(D(2026, 1, 1), D(2026, 1, 31)), (D(2026, 2, 1), D(2026, 3, 3)), (D(2026, 3, 4), D(2026, 3, 5))],
		)

	def test_record_names(self):
		a = rules.record_name("GSC", D(2026, 10, 1), "Q", "fountain repair " * 30)
		self.assertEqual(a, rules.record_name("GSC", "2026-10-01", "Q", "fountain repair " * 30))
		self.assertLessEqual(len(a), 40)
		self.assertNotEqual(a, rules.record_name("GSC", D(2026, 10, 1), "Q", "fountain repair"))
		self.assertEqual(rules.record_name("GA4", D(2026, 10, 1), "T"), "GA4-2026-10-01-T")


class TestApiRows(unittest.TestCase):
	def test_ga4(self):
		rows = rules.parse_ga4_rows(
			[(["20261001", "/a"], ["12", "10", "8", "1.0"]), (["bad"], ["1"]), (["20261002"], ["3"])]
		)
		self.assertEqual(len(rows), 2)
		self.assertEqual(
			rows[0],
			{
				"metric_date": D(2026, 10, 1),
				"value": "/a",
				"sessions": 12,
				"total_users": 10,
				"engaged_sessions": 8,
				"key_events": 1.0,
			},
		)
		self.assertEqual((rows[1]["value"], rows[1]["sessions"], rows[1]["key_events"]), (None, 3, 0.0))

	def test_gsc(self):
		rows = rules.parse_gsc_rows(
			[
				{"keys": ["2026-10-01", "q"], "clicks": 2, "impressions": 40, "ctr": 0.05, "position": 7.5},
				{"keys": []},
			]
		)
		self.assertEqual(
			rows,
			[{"metric_date": D(2026, 10, 1), "value": "q", "clicks": 2, "impressions": 40, "position": 7.5}],
		)


class TestCalls(unittest.TestCase):
	def call(self, name, day, number, customers=(), contacts=()):
		return {
			"name": name,
			"day": day,
			"number": number,
			"customers": set(customers),
			"contacts": set(contacts),
		}

	def test_first_time_and_conversion(self):
		calls = [
			self.call("old", D(2026, 9, 1), "+1 (801) 555-0100"),
			self.call("c1", D(2026, 10, 2), "8015550100", customers=["Acme"]),  # repeat caller
			self.call("c2", D(2026, 10, 3), "+18015550199", customers=["Beta"]),
			self.call("c3", D(2026, 10, 5), "+18015550199", customers=["Beta"]),
			self.call("c4", D(2026, 10, 6), None, contacts=["Zed"]),
			self.call("c5", D(2026, 10, 7), "+18015550123", customers=["Gamma"]),
		]
		opps = [
			{"name": "OPP-1", "day": D(2026, 10, 9), "customer": "Beta", "contact": None},
			{"name": "OPP-2", "day": D(2026, 10, 6), "customer": None, "contact": "Zed"},
			{"name": "OPP-3", "day": D(2026, 10, 1), "customer": "Acme", "contact": None},  # before the call
			{"name": "OPP-4", "day": D(2026, 11, 20), "customer": "Gamma", "contact": None},  # 44 days on
		]
		out = rules.summarize_calls(calls, opps, D(2026, 10, 1), D(2026, 10, 31))
		self.assertEqual(out["incoming"], 5)
		self.assertEqual(out["first_time_names"], {"old", "c2", "c5"})
		self.assertEqual(out["first_time"], 2)
		# OPP-1 is credited to the latest Beta call before it, c3 -- one conversion, not two.
		self.assertEqual(out["converted"], {"c3": ["OPP-1"], "c4": ["OPP-2"]})
		self.assertEqual(out["converted_calls"], 2)
		# c2 and c5 are first-time and neither is credited with an Opportunity: two extra inquiries.
		self.assertEqual(out["first_time_unconverted"], 2)
		self.assertEqual(out["per_day"][D(2026, 10, 3)], {"incoming": 1, "first_time": 1, "converted": 0})

	def test_phone_key(self):
		self.assertEqual(rules.phone_key("+1 (801) 555-0100"), "8015550100")
		self.assertEqual(rules.phone_key("8015550100"), "8015550100")
		self.assertEqual(rules.phone_key("anonymous"), "")
		self.assertEqual(rules.phone_key(None), "")


class TestRoles(unittest.TestCase):
	def test_views(self):
		team = rules.views_for(["Marketing Team"])
		self.assertEqual(team, {"page": True, "financial": False, "call_details": False, "operator": False})
		self.assertEqual(rules.views_for(["Marketing Manager"])["financial"], True)
		self.assertEqual(rules.views_for(["Marketing Manager"])["call_details"], False)
		self.assertEqual(rules.views_for(["Sales Manager"])["call_details"], True)
		self.assertFalse(rules.views_for(["Sales User"])["page"])
		self.assertTrue(rules.views_for(["System Manager"])["operator"])
		self.assertNotIn("Marketing Team", rules.FINANCIAL_ROLES)

	def test_the_page_admits_exactly_the_page_roles(self):
		page = json.loads((PAGE_DIR / "marketing_performance.json").read_text(encoding="utf-8"))
		self.assertEqual({r["role"] for r in page["roles"]}, set(rules.PAGE_ROLES))
		self.assertEqual(page["name"], "marketing-performance")
		self.assertEqual(page["module"], "Marketing")

	def test_the_endpoint_gates_before_it_reads(self):
		source = (MARKETING / "performance" / "api.py").read_text(encoding="utf-8")
		body = source[source.index("def get_performance") :]
		self.assertLess(body.index("_require()"), body.index("_period("))
		self.assertIn('@frappe.whitelist(methods=["POST"])\ndef get_performance', source)
		# Money and callers are added only under their views.
		self.assertIn('if views["financial"]:\n\t\t\t\trow.update(spend=', source)
		self.assertIn('if not views["financial"]:\n\t\treturn out', source)
		self.assertIn('if views["call_details"]:', source)
		self.assertIn('frappe.has_permission("Opportunity", "read")', source)

	def test_daily_doctypes_are_read_through_the_page_and_written_by_nobody(self):
		"""The page reads them for every page role. Only System Manager and Sales Manager get the lists
		themselves, like Ad Daily Metric: the marketing roles' DocPerms are the publishing surface
		(tests/test_marketing_approval.py) and nothing else."""
		for folder in (
			"ga4_daily_metric",
			"search_console_daily_metric",
			"business_profile_daily_metric",
			"social_page_daily_metric",
		):
			doc = json.loads((MARKETING / "doctype" / folder / f"{folder}.json").read_text(encoding="utf-8"))
			with self.subTest(doctype=doc["name"]):
				self.assertEqual(
					{p["role"] for p in doc["permissions"] if p.get("read")},
					{"System Manager", "Sales Manager"},
				)
				self.assertFalse(any(p.get("write") or p.get("create") for p in doc["permissions"]))
				self.assertEqual(doc["module"], "Marketing")


class TestBusinessProfile(unittest.TestCase):
	def test_allowlist_is_get_only_on_its_hosts(self):
		self.assertTrue(
			gbp.allowed(
				"GET",
				"https://businessprofileperformance.googleapis.com/v1/locations/123:fetchMultiDailyMetricsTimeSeries",
			)
		)
		self.assertTrue(
			gbp.allowed("GET", "https://mybusiness.googleapis.com/v4/accounts/9/locations/123/reviews")
		)
		self.assertTrue(
			gbp.allowed("GET", "https://mybusinessbusinessinformation.googleapis.com/v1/accounts/9/locations")
		)
		self.assertTrue(gbp.allowed("GET", "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"))
		# A reply to a review, an edit of the listing, the wrong host, plain http.
		self.assertFalse(
			gbp.allowed(
				"PUT", "https://mybusiness.googleapis.com/v4/accounts/9/locations/123/reviews/abc/reply"
			)
		)
		self.assertFalse(
			gbp.allowed("PATCH", "https://mybusinessbusinessinformation.googleapis.com/v1/locations/123")
		)
		self.assertFalse(
			gbp.allowed("POST", "https://mybusinessaccountmanagement.googleapis.com/v1/accounts")
		)
		self.assertFalse(gbp.allowed("GET", "https://evil.example.com/v1/accounts"))
		self.assertFalse(gbp.allowed("GET", "http://mybusinessaccountmanagement.googleapis.com/v1/accounts"))
		self.assertTrue(all(method == "GET" for method, _host, _p in G.ALLOWLIST))

	def test_the_core_transport_defers_to_it(self):
		from erpnext_enhancements.marketing.core.client import allowed

		self.assertTrue(
			allowed(G.PLATFORM_GBP, "GET", "https://mybusinessaccountmanagement.googleapis.com/v1/accounts")
		)
		self.assertFalse(
			allowed(G.PLATFORM_GBP, "POST", "https://mybusinessaccountmanagement.googleapis.com/v1/accounts")
		)

	def test_the_scope_is_confined_and_not_spend_capable(self):
		from erpnext_enhancements.marketing.core import constants as C

		scope = G.OAUTH[G.PLATFORM_GBP]["scopes"]
		self.assertEqual(scope, ("https://www.googleapis.com/auth/business.manage",))
		self.assertFalse(set(scope) & C.SPEND_CAPABLE_SCOPES)
		for path in MARKETING.rglob("*.py"):
			if path.name == "constants.py" and path.parent.name == "gbp":
				continue
			with self.subTest(file=str(path.relative_to(APP))):
				self.assertNotIn("auth/business.manage", path.read_text(encoding="utf-8"))

	def test_prefixes_are_disjoint(self):
		from erpnext_enhancements.marketing.core import constants as C

		others = [*C.CREDENTIAL_PREFIX.values(), *P.CREDENTIAL_PREFIX.values()]
		(mine,) = G.CREDENTIAL_PREFIX.values()
		for other in others:
			with self.subTest(other=other):
				self.assertFalse(
					mine.startswith(other + "_") or other.startswith(mine + "_") or mine == other
				)

	def test_daily_metrics(self):
		body = {
			"multiDailyMetricTimeSeries": [
				{
					"dailyMetricTimeSeries": [
						{
							"dailyMetric": "CALL_CLICKS",
							"timeSeries": {
								"datedValues": [
									{"date": {"year": 2026, "month": 10, "day": 1}, "value": "12"},
									{"date": {"year": 2026, "month": 10, "day": 2}},
								]
							},
						},
						{
							"dailyMetric": "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
							"timeSeries": {
								"datedValues": [{"date": {"year": 2026, "month": 10, "day": 1}, "value": "5"}]
							},
						},
						{
							"dailyMetric": "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
							"timeSeries": {
								"datedValues": [{"date": {"year": 2026, "month": 10, "day": 1}, "value": "7"}]
							},
						},
						{
							"dailyMetric": "WEBSITE_CLICKS",
							"timeSeries": {
								"datedValues": [{"date": {"year": 2026, "month": 10}, "value": "99"}]
							},
						},
						{"dailyMetric": "BUSINESS_FOOD_ORDERS", "timeSeries": {"datedValues": []}},
					]
				}
			]
		}
		out = gbp.parse_daily_metrics(body)
		self.assertEqual(out[D(2026, 10, 1)], {"call_clicks": 12, "maps_impressions": 12})
		self.assertEqual(out[D(2026, 10, 2)], {"call_clicks": 0}, "a missing value is zero")
		self.assertNotIn("website_clicks", out.get(D(2026, 10, 1), {}), "a month-level point is skipped")

	def test_metrics_query(self):
		query = gbp.metrics_query(D(2026, 10, 1), D(2026, 10, 7))
		self.assertIn("dailyMetrics=CALL_CLICKS&dailyMetrics=WEBSITE_CLICKS", query)
		self.assertIn(
			"dailyRange.start_date.year=2026&dailyRange.start_date.month=10&dailyRange.start_date.day=1",
			query,
		)
		self.assertIn("dailyRange.end_date.day=7", query)

	def test_reviews(self):
		pages = [
			{
				"averageRating": 4.6,
				"totalReviewCount": 3,
				"reviews": [
					{"starRating": "FIVE", "reviewReply": {"comment": "Thanks!"}},
					{"starRating": "FOUR"},
				],
				"nextPageToken": "x",
			},
			{"reviews": [{"starRating": "FIVE", "reviewReply": {"comment": ""}}]},
		]
		self.assertEqual(
			gbp.summarize_reviews(pages), {"review_count": 3, "average_rating": 4.6, "unreplied_reviews": 2}
		)
		self.assertEqual(
			gbp.summarize_reviews([{"reviews": [{"starRating": "FIVE"}, {"starRating": "THREE"}]}]),
			{"review_count": 2, "average_rating": 4.0, "unreplied_reviews": 2},
		)

	def test_ids(self):
		self.assertEqual(gbp.resource_id("accounts/9/locations/123"), "123")
		self.assertEqual(
			gbp.parse_accounts({"accounts": [{"name": "accounts/9", "accountName": "Sapphire"}]}),
			[{"account_id": "9", "name": "Sapphire"}],
		)
		self.assertEqual(
			gbp.parse_locations({"locations": [{"name": "locations/123", "title": "Shop"}]}),
			[{"location_id": "123", "title": "Shop"}],
		)


class TestSocialPageStats(unittest.TestCase):
	def test_facebook_end_time_is_the_day_after(self):
		body = {
			"data": [
				{
					"name": "page_total_media_view_unique",
					"period": "day",
					"values": [{"value": 40, "end_time": "2026-10-02T07:00:00+0000"}],
				},
				{
					"name": "page_media_view",
					"period": "day",
					"values": [{"value": 90, "end_time": "2026-10-02T07:00:00+0000"}],
				},
				{
					"name": "page_media_view",
					"period": "week",
					"values": [{"value": 900, "end_time": "2026-10-02T07:00:00+0000"}],
				},
			]
		}
		self.assertEqual(social.parse_facebook_insights(body), {D(2026, 10, 1): {"reach": 40, "views": 90}})

	def test_instagram_total_values(self):
		body = {
			"data": [
				{"name": "reach", "total_value": {"value": 12}},
				{"name": "views", "total_value": {"value": 30}},
			]
		}
		self.assertEqual(social.parse_total_values(body), {"reach": 12, "views": 30})

	def test_linkedin(self):
		start = social._ms(D(2026, 10, 1))
		body = {
			"elements": [
				{
					"timeRange": {"start": start},
					"totalShareStatistics": {"uniqueImpressionsCounts": 7, "impressionCount": 20},
				},
				{"timeRange": {"start": start + 86400000}, "totalShareStatistics": {}},
			]
		}
		self.assertEqual(
			social.parse_linkedin_share_days(body),
			{D(2026, 10, 1): {"reach": 7, "views": 20}, D(2026, 10, 2): {"reach": 0, "views": 0}},
		)
		self.assertEqual(social.parse_linkedin_network_size({"firstDegreeSize": 219}), 219)
		query = social.linkedin_share_query("123", D(2026, 10, 1), D(2026, 10, 7))
		self.assertIn("organizationalEntity=urn%3Ali%3Aorganization%3A123", query)
		self.assertIn(
			f"timeIntervals=(timeRange:(start:{start},end:{social._ms(D(2026, 10, 8))}),timeGranularityType:DAY)",
			query,
		)

	def test_youtube(self):
		self.assertEqual(
			social.parse_youtube_subscribers({"items": [{"statistics": {"subscriberCount": "1230"}}]}), 1230
		)
		self.assertIsNone(
			social.parse_youtube_subscribers(
				{"items": [{"statistics": {"subscriberCount": "5", "hiddenSubscriberCount": True}}]}
			)
		)
		self.assertIsNone(social.parse_youtube_subscribers({}))
		body = {"columnHeaders": [{"name": "day"}, {"name": "views"}], "rows": [["2026-10-01", 15]]}
		self.assertEqual(social.parse_youtube_views(body), {D(2026, 10, 1): {"views": 15}})
		self.assertEqual(
			social.parse_youtube_views({"columnHeaders": [{"name": "day"}, {"name": "views"}]}), {}
		)

	def test_followers_land_on_tonight_only(self):
		today = D(2026, 10, 29)
		out = social.rows_for(
			500,
			{D(2026, 10, 26): {"reach": 3, "views": None}, D(2026, 10, 27): {"reach": None, "views": None}},
			today,
		)
		self.assertEqual(out, {D(2026, 10, 26): {"reach": 3}, today: {"followers": 500}})
		self.assertEqual(social.rows_for(None, {}, today), {})

	def test_the_one_new_read_is_a_get(self):
		from erpnext_enhancements.marketing.publish.client import allowed

		url = "https://api.linkedin.com/rest/networkSizes/urn%3Ali%3Aorganization%3A123"
		self.assertTrue(allowed(P.CONNECTION_LINKEDIN, "GET", url))
		self.assertFalse(allowed(P.CONNECTION_LINKEDIN, "POST", url))
		self.assertFalse(allowed(P.CONNECTION_META, "GET", url))

	def test_the_switches_ship_off(self):
		settings = json.loads(
			(MARKETING / "doctype" / "marketing_settings" / "marketing_settings.json").read_text(
				encoding="utf-8"
			)
		)
		fields = {f["fieldname"]: f for f in settings["fields"]}
		self.assertEqual(fields[social.ENABLED_FLAG]["default"], "0")
		self.assertEqual(fields[G.ENABLED_FLAG]["default"], "0")
		self.assertEqual(fields["branded_terms"]["default"], "sapphire")


NODE_CHECK = r"""
const fs = require("fs");
const vm = require("vm");
const src = fs.readFileSync(process.argv[process.argv.length - 1], "utf8");
const frappe = { pages: { "marketing-performance": {} } };
const ctx = { frappe, __: (s, args) => (args || []).reduce((t, a, i) => t.replace(`{${i}}`, a), s), window: {}, console, Intl };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const M = frappe.pages["marketing-performance"].logic;
const out = {
	states: [
		M.state_from({}),
		M.state_from({ from: "2026-10-01", to: "2026-10-28", compare: "1" }),
		M.state_from({ from: "2026-10-28", to: "2026-10-01" }),
		M.state_from({ from: "2026-02-30", to: "junk", compare: "0" }),
	],
	queries: [
		M.query_for({ from: null, to: null, compare: false }),
		M.query_for({ from: "2026-10-01", to: "2026-10-28", compare: true }),
		M.query_for({ from: null, to: null, compare: true }),
	],
	preset: M.preset_range(28, "2026-10-29"),
	preset_of: [
		M.preset_of({ from: null, to: null }, "2026-10-29"),
		M.preset_of({ from: "2026-10-22", to: "2026-10-28" }, "2026-10-29"),
		M.preset_of({ from: "2026-10-02", to: "2026-10-28" }, "2026-10-29"),
	],
	same: [M.same({ from: null, to: null, compare: false }, {}), M.same({ compare: true }, {})],
	delta: [M.delta(120, 100), M.delta(80, 100), M.delta(5, 0), M.delta(null, 3)],
	segments: M.line_segments([1, null, 2, 4, null], 100, 10),
	single: M.line_segments([3], 100, 10),
	pct: [M.fmt_pct(0.1234), M.fmt_pct(null)],
	sum: [M.sum([1, null, 2]), M.sum([null, null])],
};
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestPageLogicUnderNode(unittest.TestCase):
	def test_the_page_logic(self):
		result = subprocess.run(
			["node", "-e", NODE_CHECK, str(PAGE_DIR / "marketing_performance.js")],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=60,
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		out = json.loads(result.stdout)
		self.assertEqual(
			out["states"],
			[
				{"from": None, "to": None, "compare": False},
				{"from": "2026-10-01", "to": "2026-10-28", "compare": True},
				{"from": "2026-10-01", "to": "2026-10-28", "compare": False},
				{"from": None, "to": None, "compare": False},
			],
		)
		self.assertEqual(out["queries"], ["", "?from=2026-10-01&to=2026-10-28&compare=1", "?compare=1"])
		self.assertEqual(out["preset"], {"from": "2026-10-01", "to": "2026-10-28"})
		self.assertEqual(out["preset_of"], ["28", "7", None])
		self.assertEqual(out["same"], [True, False])
		self.assertEqual(out["delta"][0], {"text": "+20%", "tone": "good"})
		self.assertEqual(out["delta"][1], {"text": "−20%", "tone": "danger"})
		self.assertEqual(out["delta"][2:], [None, None])
		self.assertEqual(out["segments"]["segments"], [["0.0,7.5"], ["50.0,5.0", "75.0,0.0"]])
		self.assertEqual(out["segments"]["max"], 4)
		self.assertEqual(out["single"]["segments"], [["50.0,0.0"]])
		self.assertEqual(out["pct"], ["12.3%", "–"])
		self.assertEqual(out["sum"], [3, None])


class TestPageHistory(unittest.TestCase):
	"""Only a range the person chose writes history; a route change only reads the address."""

	def setUp(self):
		src = (PAGE_DIR / "marketing_performance.js").read_text(encoding="utf-8")
		self.code = "\n".join(
			line
			for line in re.sub(r"/\*[\s\S]*?\*/", "", src).splitlines()
			if not line.strip().startswith("//")
		)

	def method(self, name):
		start = self.code.index(f"\n\t{name}(")
		end = self.code.index("\n\t}\n", start)
		return self.code[start:end]

	def test_one_push_and_it_is_in_go(self):
		self.assertEqual(self.code.count("history.pushState"), 1)
		self.assertIn("history.pushState", self.method("go"))
		self.assertNotIn("replaceState", self.code)
		self.assertNotIn("set_route", self.method("route_changed"))
		self.assertNotIn("pushState", self.method("route_changed"))

	def test_route_options_are_consumed(self):
		body = self.method("take_route_options")
		self.assertIn("delete opts[key]", body)
		self.assertIn("frappe.route_options = null", body)


if __name__ == "__main__":
	unittest.main()
