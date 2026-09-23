"""Bench-free test: publishing rate limits (TASK-2026-01482, Marketing P2).

``publish/ratelimit.py`` keeps each rule twice: a pure Python function (the specification) and,
where it must hold across workers, a Redis Lua script. These tests pin the specification, then
run **the real Lua** (fakeredis + lupa) against it over the same sequences, so the two cannot
drift. On CI the Lua half is mandatory: a suite that quietly skipped its hard half would be
the QuickBooks-suite failure all over again, so if ``CI`` is set and fakeredis or lupa is
missing, the suite fails instead of skipping.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_ratelimit
(the Lua half needs ``pip install fakeredis lupa``)
"""

import datetime
import json
import os
import random
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import ratelimit as R
from erpnext_enhancements.marketing.publish.client import PublishTransport

try:
	import fakeredis
	import lupa  # noqa: F401  (fakeredis needs it to run Lua)

	HAVE_LUA = True
except ImportError:
	HAVE_LUA = False

U = datetime.datetime  # naive UTC throughout
DAY = R.DAY


def fake_cache():
	return fakeredis.FakeRedis()


# ---------------------------------------------------------------- an in-memory limiter from the spec


class SpecLimiter:
	"""The RedisLimiter interface, implemented with the pure functions. What Lua must equal."""

	def __init__(self, clock=None):
		self.windows = {}
		self.counters = {}
		self.values = {}
		self.pauses = {}
		self.clock = clock or (lambda: 0.0)

	def window(self, name, now, window, limit, member):
		stamps = [t for t in self.windows.get(name, []) if t > now - window]
		admit, remaining, wait = R.window_decision(stamps, now, limit, window)
		if admit:
			stamps.append(now)
		self.windows[name] = stamps
		return admit, remaining, wait

	def budgets(self, names, costs, budgets, ttl):
		used = [self.counters.get(n, 0) for n in names]
		admit, remaining = R.budgets_decision(used, costs, budgets)
		if admit:
			for n, c in zip(names, costs, strict=True):
				self.counters[n] = self.counters.get(n, 0) + max(c, 0)
		return admit, remaining

	def pause(self, name, seconds):
		if seconds > 0:
			self.pauses[name] = self.clock() + seconds

	def paused_for(self, name):
		return max(self.pauses.get(name, 0) - self.clock(), 0)

	def set_value(self, name, value, seconds):
		self.values[name] = str(value)

	def get_int(self, name):
		value = self.values.get(name)
		return int(value) if value is not None else None


# ---------------------------------------------------------------- the specification


class WindowSpecTests(unittest.TestCase):
	def test_admits_up_to_the_limit_then_says_when(self):
		self.assertEqual(R.window_decision([], 100, 2, 50), (True, 1, 0))
		self.assertEqual(R.window_decision([100], 101, 2, 50), (True, 0, 0))
		self.assertEqual(R.window_decision([100, 101], 102, 2, 50), (False, 0, 48))

	def test_an_event_exactly_window_old_has_left(self):
		self.assertEqual(R.window_decision([100], 150, 1, 50)[0], True)
		self.assertEqual(R.window_decision([100], 149, 1, 50), (False, 0, 1))

	def test_a_lowered_limit_waits_for_enough_to_leave(self):
		# Five in the window, limit cut to 2: room for one more once the count is down to 1,
		# i.e. once four have left -- the fourth-oldest (40) leaves at 140.
		self.assertEqual(R.window_decision([10, 20, 30, 40, 50], 60, 2, 100), (False, 0, 140 - 60))

	def test_a_zero_limit_never_admits(self):
		self.assertEqual(R.window_decision([], 5, 0, 100), (False, 0, 100))
		self.assertEqual(R.window_decision([1], 5, -1, 100), (False, 0, 100))


class BudgetSpecTests(unittest.TestCase):
	def test_all_or_nothing(self):
		self.assertEqual(R.budgets_decision([0, 0], [1, 100], [100, 10000]), (True, [99, 9900]))
		self.assertEqual(R.budgets_decision([100, 0], [1, 100], [100, 10000]), (False, [0, 10000]))
		self.assertEqual(R.budgets_decision([0, 9950], [1, 100], [100, 10000]), (False, [100, 50]))

	def test_exactly_full_fits_and_negative_costs_are_zero(self):
		self.assertEqual(R.budgets_decision([99], [1], [100]), (True, [0]))
		self.assertEqual(R.budgets_decision([100], [-5], [100]), (True, [0]))


class QuotaDayTests(unittest.TestCase):
	def test_dst_boundaries_2026(self):
		# DST began 2026-03-08 at 02:00 PST (10:00 UTC) and ends 2026-11-01 at 02:00 PDT (09:00 UTC).
		self.assertEqual(R.pacific_offset_hours(U(2026, 3, 8, 9, 59)), -8)
		self.assertEqual(R.pacific_offset_hours(U(2026, 3, 8, 10, 0)), -7)
		self.assertEqual(R.pacific_offset_hours(U(2026, 11, 1, 8, 59)), -7)
		self.assertEqual(R.pacific_offset_hours(U(2026, 11, 1, 9, 0)), -8)

	def test_the_quota_day_turns_at_pacific_midnight(self):
		self.assertEqual(R.quota_day(U(2026, 9, 23, 6, 59)), datetime.date(2026, 9, 22))
		self.assertEqual(R.quota_day(U(2026, 9, 23, 7, 0)), datetime.date(2026, 9, 23))
		self.assertEqual(R.quota_day(U(2026, 1, 15, 7, 59)), datetime.date(2026, 1, 14))
		self.assertEqual(R.quota_day(U(2026, 1, 15, 8, 0)), datetime.date(2026, 1, 15))

	def test_seconds_to_reset(self):
		self.assertEqual(R.seconds_to_quota_reset(U(2026, 9, 23, 6, 0)), 3600)
		# Across the fall-back night: Oct 31 23:30 PDT -> midnight Nov 1 PDT is 30 minutes.
		self.assertEqual(R.seconds_to_quota_reset(U(2026, 11, 1, 6, 30)), 1800)
		# And the day after: Nov 1 23:30 PST -> midnight Nov 2 PST.
		self.assertEqual(R.seconds_to_quota_reset(U(2026, 11, 2, 7, 30)), 1800)

	def test_agrees_with_the_tz_database(self):
		try:
			from zoneinfo import ZoneInfo

			pacific = ZoneInfo("America/Los_Angeles")
		except Exception:
			self.skipTest("no tz database on this Python (Windows without tzdata)")
		start = U(2026, 1, 1)
		for hour in range(0, 2 * 366 * 24, 7):
			when = start + datetime.timedelta(hours=hour)
			aware = when.replace(tzinfo=datetime.UTC).astimezone(pacific)
			self.assertEqual(R.quota_day(when), aware.date(), when)
			self.assertEqual(
				R.pacific_offset_hours(when), int(aware.utcoffset().total_seconds() // 3600), when
			)


class HeaderTests(unittest.TestCase):
	def test_meta_usage(self):
		quiet = {"X-App-Usage": json.dumps({"call_count": 12, "total_cputime": 5, "total_time": 8})}
		self.assertEqual(R.meta_pause_seconds(quiet), 0)
		loud = {"x-app-usage": json.dumps({"call_count": 91, "total_cputime": 5, "total_time": 8})}
		self.assertEqual(R.meta_pause_seconds(loud), P.META_DEFAULT_PAUSE_SECONDS)
		named = {
			"X-Business-Use-Case-Usage": json.dumps(
				{
					"1234": [
						{
							"type": "pages",
							"call_count": 40,
							"total_cputime": 3,
							"total_time": 2,
							"estimated_time_to_regain_access": 7,
						}
					]
				}
			)
		}
		self.assertEqual(R.meta_pause_seconds(named), 7 * 60, "Meta's named time wins")
		self.assertEqual(R.meta_pause_seconds({"X-App-Usage": "not json"}), 0)
		self.assertEqual(R.meta_pause_seconds({}), 0)
		self.assertEqual(R.meta_pause_seconds(None), 0)

	def test_retry_after(self):
		now = U(2026, 9, 22, 12, 0)
		self.assertEqual(R.retry_after_seconds("120"), 120)
		self.assertEqual(R.retry_after_seconds("Tue, 22 Sep 2026 12:01:30 GMT", now), 90)
		self.assertIsNone(R.retry_after_seconds("soon"))
		self.assertIsNone(R.retry_after_seconds(None))

	def test_pause_after_response(self):
		self.assertEqual(R.pause_after_response(P.CONNECTION_LINKEDIN, 429, {"Retry-After": "30"}), 30)
		self.assertEqual(R.pause_after_response(P.CONNECTION_LINKEDIN, 429, {}), P.DEFAULT_429_PAUSE_SECONDS)
		self.assertEqual(R.pause_after_response(P.CONNECTION_LINKEDIN, 200, {"Retry-After": "30"}), 0)
		loud = {"X-App-Usage": json.dumps({"call_count": 95})}
		self.assertEqual(R.pause_after_response(P.CONNECTION_META, 200, loud), P.META_DEFAULT_PAUSE_SECONDS)
		self.assertEqual(
			R.pause_after_response(P.CONNECTION_LINKEDIN, 200, loud), 0, "Meta's headers, Meta only"
		)
		self.assertEqual(
			R.pause_after_response(P.CONNECTION_META, 429, {"Retry-After": "999999"}), P.MAX_PAUSE_SECONDS
		)

	def test_instagram_limit(self):
		body = {"data": [{"quota_usage": 3, "config": {"quota_total": 100, "quota_duration": 86400}}]}
		self.assertEqual(R.parse_instagram_limit(body), (3, 100))
		self.assertIsNone(R.parse_instagram_limit({"data": []}))
		self.assertIsNone(R.parse_instagram_limit({"data": [{"quota_usage": "3"}]}))


# ---------------------------------------------------------------- the Lua equals the specification


@unittest.skipUnless(HAVE_LUA or os.environ.get("CI"), "the Lua half needs: pip install fakeredis lupa")
class LuaMatchesSpecTests(unittest.TestCase):
	def setUp(self):
		if not HAVE_LUA:
			self.fail("CI is set but fakeredis/lupa are not installed: the Lua half must run on CI")
		self.redis = R.RedisLimiter(fake_cache())
		self.spec = SpecLimiter()

	def test_window_over_random_sequences(self):
		rng = random.Random(1482)
		for run in range(20):
			name = f"w{run}"
			now = 1_000_000
			window = rng.choice([10, 60, 3600])
			for step in range(200):
				now += rng.choice([0, 1, 2, 5, 13, window // 2, window])
				limit = rng.choice([0, 1, 2, 3, 5, 5, 5])
				got = self.redis.window(name, now, window, limit, f"{step}:{now}")
				want = self.spec.window(name, now, window, limit, None)
				self.assertEqual(got[:2], want[:2], (run, step, now, limit))
				self.assertAlmostEqual(got[2], float(want[2]), places=6, msg=(run, step))

	def test_budgets_over_random_sequences(self):
		rng = random.Random(482)
		for run in range(20):
			names = [f"b{run}:{i}" for i in range(rng.choice([1, 2, 3]))]
			budgets = [rng.choice([1, 5, 100]) for _ in names]
			for step in range(100):
				costs = [rng.choice([-1, 0, 1, 1, 2, 50]) for _ in names]
				self.assertEqual(
					self.redis.budgets(names, costs, budgets, 3600),
					self.spec.budgets(names, costs, budgets, 3600),
					(run, step, costs),
				)

	def test_pause_and_keys(self):
		self.redis.pause("p", 30)
		self.assertGreater(self.redis.paused_for("p"), 29)
		self.assertEqual(self.redis.paused_for("never"), 0)
		self.redis.set_value("v", 42, 60)
		self.assertEqual(self.redis.get_int("v"), 42)
		self.assertIsNone(self.redis.get_int("absent"))

	def test_site_prefix_is_applied_to_raw_keys(self):
		cache = fake_cache()
		cache.make_key = lambda k: f"site1|{k}".encode()
		limiter = R.RedisLimiter(cache)
		limiter.set_value("v", 1, 60)
		self.assertIsNotNone(cache.get("site1|v"), "raw Redis calls must carry Frappe's site prefix")
		self.assertIsNone(cache.get("v"))


# ---------------------------------------------------------------- admit()


class AdmitTests(unittest.TestCase):
	NOW = U(2026, 9, 22, 18, 0)

	def limiters(self):
		yield "spec", SpecLimiter()
		if HAVE_LUA:
			yield "lua", R.RedisLimiter(fake_cache())

	def job(self, network, name="SPJ-1", account=None):
		return {"name": name, "network": network, "social_account": account or f"SACC-{network}-1"}

	def test_instagram_uses_the_fallback_until_the_live_limit_is_known(self):
		for label, limiter in self.limiters():
			results = [R.admit(self.job("Instagram", f"J{i}"), limiter, self.NOW) for i in range(26)]
			self.assertTrue(all(r[0] for r in results[:25]), label)
			self.assertEqual(results[24][3], 0, label)
			ok, wait, note, remaining = results[25]
			self.assertFalse(ok, label)
			self.assertIn("25 posts", note)
			self.assertAlmostEqual(wait, DAY, delta=1)

	def test_instagram_live_limit_replaces_the_fallback(self):
		for label, limiter in self.limiters():
			R.set_instagram_limit("SACC-Instagram-1", 100, limiter)
			results = [R.admit(self.job("Instagram", f"J{i}"), limiter, self.NOW) for i in range(30)]
			self.assertTrue(all(r[0] for r in results), label)
			self.assertEqual(results[-1][3], 70, label)

	def test_youtube_budget_is_per_pacific_day(self):
		for label, limiter in self.limiters():
			for _ in range(P.YOUTUBE_UPLOADS_PER_DAY):
				self.assertTrue(R.admit(self.job("YouTube"), limiter, self.NOW)[0], label)
			ok, wait, note, remaining = R.admit(self.job("YouTube"), limiter, self.NOW)
			self.assertFalse(ok, label)
			self.assertEqual(wait, R.seconds_to_quota_reset(self.NOW))
			# 18:00 UTC is 11:00 PDT; the next Pacific day opens a fresh budget.
			tomorrow = self.NOW + datetime.timedelta(seconds=wait + 1)
			self.assertTrue(R.admit(self.job("YouTube"), limiter, tomorrow)[0], label)

	def test_a_paused_connection_defers_everything_on_it(self):
		for label, limiter in self.limiters():
			limiter.pause(R.pause_key(P.CONNECTION_META), 600)
			for network in ("Facebook", "Instagram"):
				ok, wait, note, _ = R.admit(self.job(network), limiter, self.NOW)
				self.assertFalse(ok, (label, network))
				self.assertGreater(wait, 590)
			self.assertTrue(R.admit(self.job("LinkedIn"), limiter, self.NOW)[0], "other connections carry on")

	def test_facebook_and_linkedin_have_no_local_bucket(self):
		limiter = SpecLimiter()
		for i in range(500):
			self.assertEqual(R.admit(self.job("Facebook", f"F{i}"), limiter, self.NOW), (True, 0, "", None))

	def test_observe_pauses_only_when_told(self):
		limiter = SpecLimiter()
		self.assertEqual(R.observe(P.CONNECTION_META, 200, {}, limiter), 0)
		self.assertEqual(limiter.pauses, {})
		R.observe(P.CONNECTION_META, 200, {"X-App-Usage": json.dumps({"call_count": 97})}, limiter)
		self.assertIn(R.pause_key(P.CONNECTION_META), limiter.pauses)


class TransportObserveTests(unittest.TestCase):
	class Response:
		def __init__(self, status, headers):
			self.status_code = status
			self.headers = headers
			self.text = "{}"
			self.url = ""

		def json(self):
			return {}

	class HTTP:
		def __init__(self, response):
			self.response = response

		def request(self, *a, **k):
			return self.response

	URL = f"https://graph.facebook.com/{__import__('erpnext_enhancements.marketing.core.constants', fromlist=['x']).META_API_VERSION}/me/accounts"

	def test_every_response_is_observed(self):
		seen = []
		headers = {"X-App-Usage": json.dumps({"call_count": 95})}
		t = PublishTransport(
			P.CONNECTION_META,
			token="T",
			http=self.HTTP(self.Response(200, headers)),
			observe=lambda status, h: seen.append((status, h)),
		)
		t.request("GET", self.URL)
		self.assertEqual(seen, [(200, headers)])

	def test_a_failing_observer_never_fails_the_request(self):
		def broken(status, headers):
			raise ConnectionError("redis is down")

		t = PublishTransport(
			P.CONNECTION_META, token="T", http=self.HTTP(self.Response(200, {})), observe=broken
		)
		self.assertEqual(t.request("GET", self.URL), {})


if __name__ == "__main__":
	unittest.main()
