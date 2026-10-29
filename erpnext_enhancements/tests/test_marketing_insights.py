"""Bench-free tests for the marketing reports, graphs, alerts and digest (v1.757.0).

The rules in ``marketing/performance/insights.py``, each against hand-worked figures:

* **R1** inquiry sources by month: unsourced bucket, win rate, won value, median days to close.
* **R2** query comparison: changes against before and last year, new and lost, branded.
* **G3** the expected-CTR curve: the site's own median where a bucket has enough pages, the fallback
  elsewhere, never rising with position; interpolation; pages under the curve.
* **R3** near page one: positions 8-20, minimum impressions, clicks to gain at position 3.
* **R4** fading pages: the drop share and the minimum baseline (no flapping).
* **R5** the service-line mapping: each match type, first match wins, a job spanning streams counts in
  each line, the Unmatched row.
* **R6** missed calls: unanswered and not called back in the window, waiting vs missed, first-time
  callers first.
* **G2** funnel bars; **F3** drop, index, review and throttle rules; **F2** the digest's markdown and
  its weeks; **F5** the tracked-link builder (own site only, never ``utm_id``, values slugged).
* The settings defaults the alerts depend on, so they cannot ship dormant.

Standard library only, no ``frappe``. Run: python -m unittest erpnext_enhancements.tests.test_marketing_insights -v
"""

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MARKETING = APP / "marketing"

from erpnext_enhancements.marketing.performance import insights as I

D = dt.date
UNKNOWN = "Unknown (pre-Aug 2026)"


class TestInquirySources(unittest.TestCase):
	def test_by_source_and_month(self):
		opps = [
			{"created": "2026-09-02", "source": "Website", "status": "Closed Won", "won_on": "2026-09-12", "amount": 1000},
			{"created": "2026-09-05", "source": "Website", "status": "Closed Won", "won_on": "2026-09-25", "amount": 500},
			{"created": "2026-09-09", "source": "Website", "status": "Lost"},
			{"created": "2026-09-10", "source": UNKNOWN, "status": "Open"},
			{"created": "2026-09-11", "source": "", "status": "Closed Won", "won_on": None, "amount": 70},
			{"created": "2026-10-01", "source": "Referral", "status": "Open"},
		]
		rows = {(r["source"], r["month"]): r for r in I.inquiry_sources(opps, UNKNOWN)}
		web = rows[("Website", "2026-09")]
		self.assertEqual((web["count"], web["won"], web["won_value"]), (3, 2, 1500.0))
		self.assertAlmostEqual(web["win_rate"], 2 / 3)
		self.assertEqual(web["median_days_to_close"], 15)  # 10 and 20 days
		unsourced = rows[("Unsourced", "2026-09")]
		self.assertEqual((unsourced["count"], unsourced["won"], unsourced["median_days_to_close"]), (2, 1, None))
		self.assertEqual(I.inquiry_sources(opps, UNKNOWN)[0]["month"], "2026-10", "newest month first")


class TestQueries(unittest.TestCase):
	def test_compare(self):
		now = [
			{"value": "fountain repair", "clicks": 10, "impressions": 100, "position": 4},
			{"value": "sapphire fountains", "clicks": 30, "impressions": 60, "position": 1},
			{"value": "pond pump", "clicks": 2, "impressions": 40, "position": 9},
		]
		before = [
			{"value": "fountain repair", "clicks": 5, "impressions": 80, "position": 6},
			{"value": "water feature", "clicks": 4, "impressions": 90, "position": 7},
		]
		year = [{"value": "fountain repair", "clicks": 20, "impressions": 150, "position": 3}]
		rows = {r["query"]: r for r in I.compare_queries(now, before, year, ("sapphire",))}
		repair = rows["fountain repair"]
		self.assertAlmostEqual(repair["change_prev"], 1.0)
		self.assertAlmostEqual(repair["change_last_year"], -0.5)
		self.assertEqual((repair["status"], repair["clicks_prev"], repair["position_prev"]), ("", 5, 6))
		self.assertEqual(rows["pond pump"]["status"], "new")
		self.assertEqual(rows["water feature"]["status"], "lost")
		self.assertEqual(rows["water feature"]["clicks"], 0)
		self.assertTrue(rows["sapphire fountains"]["branded"])
		self.assertIsNone(rows["pond pump"]["clicks_last_year"])


class TestCurve(unittest.TestCase):
	def test_site_buckets_and_fallback(self):
		pages = [
			{"position": 1.2, "clicks": 30, "impressions": 100},
			{"position": 0.9, "clicks": 40, "impressions": 100},
			{"position": 1.4, "clicks": 20, "impressions": 100},
			{"position": 3, "clicks": 1, "impressions": 10},  # too few impressions to count
		]
		curve = I.ctr_curve(pages)
		self.assertEqual(curve["source"][1], "site")
		self.assertAlmostEqual(curve["points"][1], 0.30)
		self.assertEqual(curve["source"][3], "fallback")
		self.assertEqual(curve["site_buckets"], 1)
		values = [curve["points"][p] for p in range(1, 21)]
		self.assertEqual(values, sorted(values, reverse=True), "never rises with position")

	def test_a_noisy_site_median_cannot_rise(self):
		pages = [{"position": 5, "clicks": 50, "impressions": 100}] * 3
		curve = I.ctr_curve(pages)
		self.assertLessEqual(curve["points"][5], curve["points"][4])

	def test_interpolation_and_below(self):
		curve = I.ctr_curve([])
		self.assertAlmostEqual(I.expected_ctr(curve, 2.5), (0.15 + 0.10) / 2)
		self.assertEqual(I.expected_ctr(curve, 35), I.FALLBACK_TAIL_CTR)
		self.assertIsNone(I.expected_ctr(curve, None))
		below = I.below_curve(
			[
				{"value": "/a", "position": 1, "clicks": 5, "impressions": 100},
				{"value": "/b", "position": 1, "clicks": 25, "impressions": 100},
			],
			curve,
		)
		self.assertEqual([r["value"] for r in below], ["/a"])


class TestNearAndFading(unittest.TestCase):
	def test_near_page_one(self):
		curve = I.ctr_curve([])
		rows = I.near_page_one(
			[
				{"value": "a", "position": 9, "clicks": 1, "impressions": 500},
				{"value": "b", "position": 12, "clicks": 0, "impressions": 100},
				{"value": "c", "position": 7.9, "clicks": 0, "impressions": 900},
				{"value": "d", "position": 15, "clicks": 0, "impressions": 49},
			],
			curve,
			50,
		)
		self.assertEqual([r["value"] for r in rows], ["a", "b"])
		self.assertAlmostEqual(rows[0]["clicks_to_gain"], 500 * 0.10 - 1)

	def test_fading(self):
		rows = I.fading({"/a": 6, "/b": 14, "/c": 1}, {"/a": 20, "/b": 20, "/c": 3, "/gone": 40}, 0.3, 20)
		self.assertEqual([r["key"] for r in rows], ["/gone", "/a", "/b"])
		self.assertAlmostEqual(rows[1]["change"], -0.7)
		self.assertNotIn("/c", [r["key"] for r in rows], "under the baseline never flaps on")
		self.assertEqual(I.fading({"/b": 15}, {"/b": 20}, 0.3, 20), [], "a 25% dip is not a 30% drop")


class TestServiceLines(unittest.TestCase):
	MAPPING = [
		{"match_type": "Value Stream", "match_value": "Design", "service_line": "Design-Build"},
		{"match_type": "Value Stream", "match_value": "Service", "service_line": "Service & Maintenance"},
		{"match_type": "Opportunity Keyword", "match_value": "splash pad", "service_line": "Splash Pads"},
		{"match_type": "URL Prefix", "match_value": "/rentals", "service_line": "Rentals"},
		{"match_type": "URL Contains", "match_value": "repair", "service_line": "Service & Maintenance"},
		{"match_type": "Query Keyword", "match_value": "pump", "service_line": "Parts & Products"},
		{"match_type": "URL Contains", "match_value": "x", "service_line": "Not A Line"},
	]

	def test_match_types(self):
		m = self.MAPPING
		self.assertEqual(I.service_line_of("page", "https://www.sapphirefountains.com/Rentals/fountain/", m), "Rentals")
		self.assertEqual(I.service_line_of("page", "/fountain-repair", m), "Service & Maintenance")
		self.assertIsNone(I.service_line_of("page", "/about", m))
		self.assertEqual(I.service_line_of("query", "Pond PUMP replacement", m), "Parts & Products")
		self.assertEqual(I.service_line_of("value_stream", "Design", m), "Design-Build")
		self.assertIsNone(I.service_line_of("value_stream", "Designer", m), "a value stream matches exactly")
		self.assertIsNone(I.service_line_of("page", "/x", m), "a rule naming no real line is ignored")

	def test_funnel(self):
		rows = {
			r["line"]: r
			for r in I.service_line_funnel(
				[{"value": "/rentals/a", "sessions": 30, "clicks": 4}, {"value": "/about", "sessions": 10, "clicks": 1}],
				[
					{"title": "Plaza", "value_streams": ["Design", "Service"], "status": "Closed Won", "amount": 100},
					{"title": "City splash pad", "value_streams": [], "status": "Open"},
					{"title": "Unknown", "value_streams": ["Events"], "status": "Open"},
				],
				self.MAPPING,
			)
		}
		self.assertEqual((rows["Rentals"]["sessions"], rows["Rentals"]["search_clicks"]), (30, 4))
		self.assertEqual(rows["Unmatched"]["sessions"], 10)
		self.assertEqual((rows["Design-Build"]["inquiries"], rows["Design-Build"]["won_value"]), (1, 100.0))
		self.assertEqual(rows["Service & Maintenance"]["won"], 1, "a job spanning streams counts in each")
		self.assertEqual(rows["Splash Pads"]["inquiries"], 1)
		self.assertEqual(rows["Unmatched"]["inquiries"], 1)
		self.assertEqual([r["line"] for r in I.service_line_funnel([], [], [])][:5], list(I.LINES))

	def test_the_seed_names_only_real_lines_and_types(self):
		import ast

		source = (APP / "patches" / "backfill_marketing_insights_settings.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		seed = next(
			ast.literal_eval(node.value)
			for node in tree.body
			if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SEED_RULES" for t in node.targets)
		)
		self.assertGreater(len(seed), 10)
		for match_type, value, line in seed:
			self.assertIn(match_type, I.MATCH_TYPES)
			self.assertIn(line, I.LINES)
			self.assertTrue(value.strip())


class TestMissedCalls(unittest.TestCase):
	NOW = dt.datetime(2026, 10, 29, 12, 0)

	def call(self, name, at, type_, status, number, first=False):
		return {"name": name, "at": at, "type": type_, "status": status, "number": number, "first_time": first}

	def test_rule(self):
		t = dt.datetime
		calls = [
			self.call("a", t(2026, 10, 28, 9), "Incoming", "No Answer", "+1 801 555 0100", first=True),
			self.call("cb", t(2026, 10, 28, 11), "Outgoing", "Completed", "8015550100"),  # called back in 2 h
			self.call("b", t(2026, 10, 28, 9), "Incoming", "No Answer", "+18015550111"),
			self.call("late", t(2026, 10, 28, 20), "Outgoing", "Completed", "+18015550111"),  # too late
			self.call("c", t(2026, 10, 27, 9), "Incoming", "Busy", "+18015550122", first=True),
			self.call("d", t(2026, 10, 29, 10), "Incoming", "No Answer", "+18015550133"),  # still in window
			self.call("e", t(2026, 10, 28, 9), "Incoming", "Completed", "+18015550144"),
		]
		rows = I.missed_calls(calls, self.NOW, 4)
		self.assertEqual([r["name"] for r in rows], ["c", "d", "b"], "first-time first, then newest")
		self.assertEqual({r["name"]: r["state"] for r in rows}, {"c": "missed", "d": "waiting", "b": "missed"})


class TestSmallRules(unittest.TestCase):
	def test_funnel_bars(self):
		bars = I.funnel_bars([("Sessions", 1000), ("Key events", 50), ("Inquiries", None), ("Won", 2)])
		self.assertAlmostEqual(bars[1]["kept"], 0.05)
		self.assertIsNone(bars[2]["kept"])
		self.assertIsNone(bars[3]["kept"], "no rate out of a missing step")
		self.assertAlmostEqual(bars[3]["of_first"], 0.002)

	def test_drop(self):
		self.assertIsNotNone(I.drop_alert(60, 100, 0.3, 50))
		self.assertIsNone(I.drop_alert(71, 100, 0.3, 50))
		self.assertIsNone(I.drop_alert(10, 40, 0.3, 50), "a quiet week cannot alarm")
		self.assertIsNone(I.drop_alert(None, 100, 0.3, 50))

	def test_index(self):
		hits = I.index_alerts({"/a": "PASS", "/b": "NEUTRAL"}, {"/a": "NEUTRAL", "/b": "NEUTRAL", "/c": "FAIL", "/d": "PASS"})
		self.assertEqual(sorted(h["url"] for h in hits), ["/a", "/c"])

	def test_reviews_and_throttle(self):
		self.assertEqual(len(I.low_reviews([{"star_rating": 2}, {"star_rating": 3}, {"star_rating": None}], 2)), 1)
		now = dt.datetime(2026, 10, 29, 6, 15)
		self.assertTrue(I.throttle_due(None, now, 24))
		self.assertFalse(I.throttle_due(now - dt.timedelta(hours=23), now, 24))
		self.assertTrue(I.throttle_due(now - dt.timedelta(hours=24), now, 24))

	def test_weeks(self):
		self.assertEqual(I.last_week(D(2026, 11, 2)), (D(2026, 10, 26), D(2026, 11, 1)))  # a Monday
		self.assertEqual(I.last_week(D(2026, 11, 4)), (D(2026, 10, 26), D(2026, 11, 1)))
		start, end = I.same_period_last_year(D(2026, 10, 26), D(2026, 11, 1))
		self.assertEqual((start.weekday(), end.weekday()), (0, 6))

	def test_heatmap_and_reply(self):
		m = I.weekday_hour_matrix(
			[
				{"type": "Incoming", "at": dt.datetime(2026, 10, 26, 9, 30)},
				{"type": "Incoming", "at": dt.datetime(2026, 10, 26, 9, 5)},
				{"type": "Outgoing", "at": dt.datetime(2026, 10, 26, 9, 5)},
			]
		)
		self.assertEqual(m[0][9], 2)
		self.assertEqual(sum(map(sum, m)), 2)
		t = dt.datetime(2026, 10, 1, 9)
		self.assertEqual(I.median_hours([(t, t + dt.timedelta(hours=2)), (t, t + dt.timedelta(hours=4)), (t, None)]), 3)
		self.assertIsNone(I.median_hours([(t, None)]))


class TestDigest(unittest.TestCase):
	def test_markdown(self):
		text = I.digest_markdown(
			{
				"week": (D(2026, 10, 26), D(2026, 11, 1)),
				"metrics": [("Sessions", 150, 100, None), ("Inbound calls", 4, 0, 3)],
				"movers": [("/fountains", "+20 sessions")],
				"missed_calls": ["2026-10-27 09:12 (first-time)"],
				"new_reviews": [],
				"link": "https://erp.example/desk/marketing-performance",
			}
		)
		self.assertIn("## 📈 Marketing, week of Oct 26", text)
		self.assertIn("| Sessions | 150 (+50%) | 100 | – |", text)
		self.assertIn("| Inbound calls | 4 | 0 | 3 |", text, "no change against a zero")
		self.assertIn("**Missed calls:** 1", text)
		self.assertNotIn("New reviews", text)
		self.assertIn("[Open Marketing Performance](https://erp.example/desk/marketing-performance)", text)


class TestTrackedLinks(unittest.TestCase):
	def test_builder(self):
		url = I.tracked_url("https://www.sapphirefountains.com/rentals/?utm_id=9&utm_source=x&ref=a#top", "Fall Flyer", "QR", "Parade of Homes 2026")
		self.assertEqual(
			url,
			"https://www.sapphirefountains.com/rentals/?ref=a&utm_source=fall-flyer&utm_medium=qr&utm_campaign=parade-of-homes-2026#top",
		)
		self.assertNotIn("utm_id", url)
		self.assertEqual(I.tracked_url("https://sapphirefountains.com", "truck", "qr", "fleet"), "https://sapphirefountains.com/?utm_source=truck&utm_medium=qr&utm_campaign=fleet")

	def test_refusals(self):
		for destination in ("https://example.com/", "ftp://sapphirefountains.com/", "sapphirefountains.com.evil.com/", ""):
			with self.subTest(destination=destination), self.assertRaises(ValueError):
				I.tracked_url(destination, "a", "b", "c")
		with self.assertRaises(ValueError):
			I.tracked_url("https://www.sapphirefountains.com/", "", "qr", "c")


class TestNoAlertShipsDormant(unittest.TestCase):
	def test_settings_defaults(self):
		doc = json.loads((MARKETING / "doctype" / "marketing_settings" / "marketing_settings.json").read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in doc["fields"]}
		self.assertEqual(fields["alerts_paused"]["default"], "0", "alerts run from day one")
		self.assertEqual(fields["alert_roles"]["default"], "Marketing Manager")
		self.assertTrue(fields["key_pages"]["default"].startswith("https://www.sapphirefountains.com/"))
		for name in (
			"near_page_one_min_impressions",
			"fading_drop_percent",
			"fading_min_baseline_clicks",
			"missed_call_callback_hours",
			"alert_throttle_hours",
			"traffic_drop_percent",
			"traffic_drop_min_baseline",
			"low_review_stars",
		):
			with self.subTest(field=name):
				self.assertGreater(int(fields[name]["default"]), 0)
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		self.assertIn("erpnext_enhancements.patches.backfill_marketing_insights_settings", patches)


if __name__ == "__main__":
	unittest.main()
