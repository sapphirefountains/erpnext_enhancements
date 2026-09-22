"""Bench-free tests for the Search Console property fallback and backfill (TASK-2026-01474).

Prod stores ``GA4 Settings.gsc_property_url`` as the bare ``sapphirefountains.com``, which
Google reads as the one URL prefix ``http://sapphirefountains.com/`` and refuses with the
same 403 as a missing grant. These pin:

* the candidate forms tried for a stored value, Domain property first (decided 2026-09-22);
* that a refused form falls through to the next, the accepted one is cached and tried first
  next time, and anything other than a refusal still raises;
* that a total refusal names every form tried and the service account to add;
* the backfill: one query across the gap, each snapshot's rolling 30-day sum computed
  locally, only ``gsc_ok = 0`` rows touched, and ``dry_run`` writing nothing.

``api/analytics.py`` imports Google's client libraries at module level and CI installs none
of them, so they are stubbed here along with frappe, and removed again afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_search_console -v
"""

import datetime
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.utils import search_console as sc

D = datetime.date
STUBBED = (
	"frappe",
	"frappe.utils",
	"google",
	"google.analytics",
	"google.analytics.data_v1beta",
	"google.analytics.data_v1beta.types",
	"google.oauth2",
	"google.oauth2.service_account",
	"googleapiclient",
	"googleapiclient.discovery",
	"googleapiclient.errors",
)
OURS = ("erpnext_enhancements.api.analytics", "erpnext_enhancements.utils.error_throttle")
_saved = {}
STATE = {}
analytics = None


class _dict(dict):
	__getattr__ = dict.get


class HttpError(Exception):
	def __init__(self, status):
		super().__init__(f"HTTP {status}")
		self.resp = types.SimpleNamespace(status=status)


class FakeService:
	"""Search Console that accepts only ``accepts`` and answers ``rows``."""

	def __init__(self, accepts, rows=(), fail_with=None):
		self.accepts = accepts
		self.rows = list(rows)
		self.fail_with = fail_with
		self.calls = []

	def searchanalytics(self):
		return self

	def query(self, siteUrl, body):
		self.calls.append((siteUrl, body))
		service = self

		class Request:
			def execute(self_inner):
				if service.fail_with:
					raise HttpError(service.fail_with)
				if siteUrl != service.accepts:
					raise HttpError(403)
				return {"rows": service.rows}

		return Request()


def _install():
	mods = {name: types.ModuleType(name) for name in STUBBED}
	frappe = mods["frappe"]
	cache = {}
	frappe.cache = lambda: types.SimpleNamespace(
		get_value=lambda k: cache.get(k),
		set_value=lambda k, v, expires_in_sec=None: cache.__setitem__(k, v),
		incr=lambda k: 1,
		expire=lambda k, s: None,
	)
	STATE["cache"] = cache
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a)
	frappe.get_traceback = lambda: "tb"
	frappe.local = types.SimpleNamespace(conf=types.SimpleNamespace(get=lambda k: "test"))
	frappe.get_doc = lambda doctype: STATE["settings"]
	frappe._dict = _dict

	def get_all(doctype, filters=None, **kw):
		STATE["filters"] = filters
		return [_dict(s) for s in STATE["snapshots"]]

	frappe.get_all = get_all
	frappe.db = types.SimpleNamespace(
		set_value=lambda dt, name, values, update_modified=True: STATE["writes"].append((name, values)),
		commit=lambda: STATE.__setitem__("committed", True),
	)
	mods["frappe.utils"].getdate = lambda v: v if isinstance(v, D) else D.fromisoformat(str(v))
	frappe.utils = mods["frappe.utils"]

	types_mod = mods["google.analytics.data_v1beta.types"]
	for name in ("DateRange", "Dimension", "Metric", "OrderBy", "RunReportRequest"):
		setattr(types_mod, name, type(name, (), {}))
	mods["google.analytics.data_v1beta"].BetaAnalyticsDataClient = type("Client", (), {})
	mods["google.oauth2.service_account"].Credentials = types.SimpleNamespace(from_service_account_file=None)
	mods["google.oauth2"].service_account = mods["google.oauth2.service_account"]
	mods["googleapiclient.discovery"].build = lambda *a, **k: None
	mods["googleapiclient.errors"].HttpError = HttpError
	for name, module in mods.items():
		sys.modules[name] = module


def setUpModule():
	global analytics
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install()
	from erpnext_enhancements.api import analytics as module

	analytics = module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _source_of(name):
	"""A function's source, decorators included (the stubbed whitelist returns it unwrapped)."""
	import inspect

	return "".join(inspect.getsourcelines(getattr(analytics, name))[0])


def reset(service=None, snapshots=()):
	STATE.update({"errors": [], "writes": [], "snapshots": list(snapshots), "committed": False})
	STATE["cache"].clear()
	STATE["settings"] = types.SimpleNamespace(
		gsc_property_url="sapphirefountains.com", credentials_json="/private/files/sa.json"
	)
	analytics._gsc_service = lambda settings: (service, "ga4-reader@proj.iam.gserviceaccount.com", None)


# ---------------------------------------------------------------- pure


class CandidateTests(unittest.TestCase):
	def test_bare_domain_tries_domain_property_first(self):
		self.assertEqual(
			sc.site_candidates("sapphirefountains.com"),
			[
				"sc-domain:sapphirefountains.com",
				"https://www.sapphirefountains.com/",
				"https://sapphirefountains.com/",
				"http://www.sapphirefountains.com/",
				# What Google reads the bare string as -- every prod 403 names it. Keep it.
				"http://sapphirefountains.com/",
			],
		)
		self.assertEqual(
			sc.site_candidates("www.Sapphirefountains.com")[0], "sc-domain:sapphirefountains.com"
		)

	def test_explicit_forms_are_taken_as_meant(self):
		self.assertEqual(sc.site_candidates("sc-domain:example.com"), ["sc-domain:example.com"])
		self.assertEqual(sc.site_candidates("https://www.example.com"), ["https://www.example.com/"])
		self.assertEqual(sc.site_candidates(""), [])

	def test_rolling_sum_is_the_live_window(self):
		daily = {"2026-08-01": (5, 100), "2026-08-31": (2, 50), "2026-07-31": (99, 999)}
		sums = sc.rolling_sums(daily, [D(2026, 8, 31)])
		# 2026-08-01 .. 2026-08-31: 31 days, today - 30 inclusive, like get_gsc_data.
		self.assertEqual(sums[D(2026, 8, 31)], (7, 150))
		self.assertEqual(sc.window_for(D(2026, 8, 31)), (D(2026, 8, 1), D(2026, 8, 31)))

	def test_missing_days_are_zero(self):
		self.assertEqual(sc.rolling_sums({}, [D(2026, 9, 1)]), {D(2026, 9, 1): (0, 0)})


# ---------------------------------------------------------------- the fallback


class FallbackTests(unittest.TestCase):
	def test_a_refused_form_falls_through_and_the_winner_is_cached(self):
		service = FakeService(accepts="https://www.sapphirefountains.com/")
		reset(service)
		site, _response, refusals = analytics._gsc_query_first_site(service, "sapphirefountains.com", {})
		self.assertEqual(site, "https://www.sapphirefountains.com/")
		self.assertEqual(refusals, [("sc-domain:sapphirefountains.com", 403)])
		service.calls.clear()
		analytics._gsc_query_first_site(service, "sapphirefountains.com", {})
		self.assertEqual(
			[c[0] for c in service.calls], ["https://www.sapphirefountains.com/"], "cached form first"
		)

	def test_other_errors_still_raise(self):
		service = FakeService(accepts="x", fail_with=500)
		reset(service)
		with self.assertRaises(HttpError):
			analytics._gsc_query_first_site(service, "sapphirefountains.com", {})

	def test_total_refusal_names_every_form_and_the_account(self):
		service = FakeService(accepts="nothing-matches")
		reset(service)
		result = analytics.get_gsc_data()
		for form in sc.site_candidates("sapphirefountains.com"):
			self.assertIn(form, result["error"])
		self.assertIn("ga4-reader@proj.iam.gserviceaccount.com", result["error"])
		self.assertTrue(STATE["errors"], "logged, throttled")

	def test_success_reports_the_property_used(self):
		rows = [{"keys": ["2026-09-20"], "clicks": 3, "impressions": 40, "ctr": 0.075, "position": 4.2}]
		service = FakeService(accepts="sc-domain:sapphirefountains.com", rows=rows)
		reset(service)
		result = analytics.get_gsc_data()
		self.assertEqual(result["property"], "sc-domain:sapphirefountains.com")
		self.assertEqual(result["search_timeline"]["datasets"][0]["values"], [3])


# ---------------------------------------------------------------- the backfill


class BackfillTests(unittest.TestCase):
	SNAPS = [
		{
			"name": "MWS-2026-08-31",
			"snapshot_date": D(2026, 8, 31),
			"source_status": "GA4 ✓ · GSC ✗",
			"pull_error": "GSC: Search Console denied access",
		},
		{
			"name": "MWS-2026-09-01",
			"snapshot_date": D(2026, 9, 1),
			"source_status": "GA4 ✗ · GSC ✗",
			# A GSC message with its own "; " must not leave a fragment behind.
			"pull_error": "GA4: quota; GSC: denied; try again",
		},
	]
	ROWS = [
		{"keys": ["2026-08-01"], "clicks": 5, "impressions": 100},
		{"keys": ["2026-08-31"], "clicks": 2, "impressions": 50},
		{"keys": ["2026-09-01"], "clicks": 1, "impressions": 10},
	]

	def test_one_query_rolling_sums_and_only_failed_nights(self):
		service = FakeService(accepts="sc-domain:sapphirefountains.com", rows=self.ROWS)
		reset(service, self.SNAPS)
		result = analytics.backfill_gsc_snapshots()
		self.assertEqual(
			len([c for c in service.calls if c[0] == "sc-domain:sapphirefountains.com"]),
			1,
			"one query for the whole gap",
		)
		body = service.calls[-1][1]
		self.assertEqual((body["startDate"], body["endDate"]), ("2026-08-01", "2026-09-01"))
		writes = dict(STATE["writes"])
		self.assertEqual(
			(
				writes["MWS-2026-08-31"]["organic_clicks_30"],
				writes["MWS-2026-08-31"]["organic_impressions_30"],
			),
			(7, 150),
		)
		self.assertEqual(writes["MWS-2026-09-01"]["organic_clicks_30"], 3, "08-02..09-01 drops 08-01")
		self.assertEqual(writes["MWS-2026-08-31"]["source_status"], "GA4 ✓ · GSC ✓ (backfilled)")
		self.assertIsNone(writes["MWS-2026-08-31"]["pull_error"], "the GSC error is gone")
		self.assertEqual(
			writes["MWS-2026-09-01"]["pull_error"], "GA4: quota", "other sources' errors are kept"
		)
		self.assertEqual(result["updated"], 2)
		self.assertTrue(STATE["committed"])

	def test_only_gsc_failures_are_selected(self):
		service = FakeService(accepts="sc-domain:sapphirefountains.com", rows=self.ROWS)
		reset(service, self.SNAPS)
		analytics.backfill_gsc_snapshots(since="2026-08-15")
		self.assertEqual(STATE["filters"], {"gsc_ok": 0, "snapshot_date": [">=", D(2026, 8, 15)]})

	def test_not_whitelisted(self):
		# A bulk writer over every failed night is a bench command, not an endpoint.
		self.assertNotIn("@frappe.whitelist", _source_of("backfill_gsc_snapshots"))

	def test_dry_run_writes_nothing(self):
		service = FakeService(accepts="sc-domain:sapphirefountains.com", rows=self.ROWS)
		reset(service, self.SNAPS)
		result = analytics.backfill_gsc_snapshots(dry_run=1)
		self.assertEqual(STATE["writes"], [])
		self.assertFalse(STATE["committed"])
		self.assertEqual(result["rows"][0]["organic_clicks_30"], 7)

	def test_refused_backfill_writes_nothing(self):
		service = FakeService(accepts="nope")
		reset(service, self.SNAPS)
		result = analytics.backfill_gsc_snapshots()
		self.assertEqual((result["updated"], STATE["writes"]), (0, []))
		self.assertIn("refused every form", result["error"])


if __name__ == "__main__":
	unittest.main()
