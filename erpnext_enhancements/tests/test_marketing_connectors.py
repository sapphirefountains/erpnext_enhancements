"""Bench-free tests for the read-only ad-spend connectors (TASK-2026-01476).

API access does not exist yet (Phase 0), so the connectors are built and tested against
recorded-shape responses in ``tests/data/marketing_api_fixtures.json``. What this pins, in
order of how badly it would hurt to get wrong:

1. **Read-only.** Nothing outside the allowlist is ever sent -- no ``:mutate``, no POST
   to Meta or LinkedIn -- and nothing mutate-shaped exists anywhere in ``marketing/``.
2. **No token is ever written down.** Errors, archived URLs and logs are redacted.
3. **Retries only what can succeed**: 408/429/5xx and transport errors, honoring
   Retry-After; every other 4xx fails at once.
4. **Restate + upsert + cursor-only-on-clean-run**, end to end through the sync engine
   against an in-memory frappe: a re-run adds no rows, a failure leaves the cursor alone.
5. The parsers' handling of each platform's quirks (micros, int64-as-string, URN pivots,
   Rest.li query encoding, closed accounts, manager accounts, pagination).
6. The OAuth ``state`` is single-use and bound to the user who clicked Connect.

Run: python -m unittest erpnext_enhancements.tests.test_marketing_connectors -v
"""

import ast
import datetime
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.marketing.core import client, utils
from erpnext_enhancements.marketing.core import constants as C
from erpnext_enhancements.marketing.platforms import google_ads, linkedin_ads, meta_ads

APP = REPO_ROOT / "erpnext_enhancements"
MARKETING = APP / "marketing"
FIX = json.loads((APP / "tests" / "data" / "marketing_api_fixtures.json").read_text(encoding="utf-8"))

D = datetime.date


# ---------------------------------------------------------------- fakes


class FakeResponse:
	def __init__(self, status, body=None, headers=None, url=""):
		self.status_code = status
		self._body = body
		self.headers = headers or {}
		self.url = url
		self.text = json.dumps(body) if body is not None else ""

	def json(self):
		if self._body is None:
			raise ValueError("no body")
		return self._body


class FakeHTTP:
	"""Routes requests to a callable; records every call."""

	def __init__(self, route):
		self.route = route
		self.calls = []

	def request(self, method, url, **kwargs):
		self.calls.append((method, url, kwargs))
		result = self.route(method, url, kwargs)
		if isinstance(result, Exception):
			raise result
		if not isinstance(result, FakeResponse):
			result = FakeResponse(200, result, url=url)
		result.url = result.url or url
		return result


def transport(platform, route, **kwargs):
	sleeps = []
	http = FakeHTTP(route)
	t = client.Transport(
		platform,
		headers={"Authorization": "Bearer SECRET-TOKEN-123456"},
		http=http,
		sleep=sleeps.append,
		**kwargs,
	)
	return t, http, sleeps


# ---------------------------------------------------------------- 1. read-only


class ReadOnlyTests(unittest.TestCase):
	def test_every_real_call_is_allowed(self):
		urls = [
			(C.PLATFORM_GOOGLE, "GET", f"{C.GOOGLE_ADS_BASE}/customers:listAccessibleCustomers"),
			(C.PLATFORM_GOOGLE, "POST", google_ads.search_url("123-456-7890")),
			(C.PLATFORM_META, "GET", f"{C.META_GRAPH_BASE}/me/adaccounts"),
			(C.PLATFORM_META, "GET", f"{C.META_GRAPH_BASE}/act_111/campaigns"),
			(C.PLATFORM_META, "GET", FIX["meta"]["insights_page_1"]["paging"]["next"]),
			(C.PLATFORM_LINKEDIN, "GET", linkedin_ads.accounts_url()),
			(C.PLATFORM_LINKEDIN, "GET", linkedin_ads.campaigns_url(507000001, "tok")),
			(
				C.PLATFORM_LINKEDIN,
				"GET",
				linkedin_ads.analytics_url(507000001, D(2026, 9, 1), D(2026, 9, 21)),
			),
		]
		for platform, method, url in urls:
			self.assertTrue(client.allowed(platform, method, url), f"{platform} {method} {url}")

	def test_writes_and_strays_are_refused(self):
		refused = [
			(C.PLATFORM_GOOGLE, "POST", f"{C.GOOGLE_ADS_BASE}/customers/123/campaigns:mutate"),
			(C.PLATFORM_GOOGLE, "POST", f"{C.GOOGLE_ADS_BASE}/customers/123/campaignBudgets:mutate"),
			(C.PLATFORM_GOOGLE, "GET", f"{C.GOOGLE_ADS_BASE}/customers/123/googleAds:search"),
			(C.PLATFORM_GOOGLE, "POST", google_ads.search_url(123).replace("https://", "http://")),
			(C.PLATFORM_META, "POST", f"{C.META_GRAPH_BASE}/act_111/campaigns"),
			(C.PLATFORM_META, "POST", f"{C.META_GRAPH_BASE}/act_111/adsets"),
			(C.PLATFORM_META, "GET", "https://evil.example/v26.0/act_111/insights"),
			(C.PLATFORM_META, "GET", f"{C.META_GRAPH_BASE}/act_111/ads"),
			(C.PLATFORM_LINKEDIN, "POST", f"{C.LINKEDIN_REST_BASE}/adAccounts/1/adCampaigns"),
			(C.PLATFORM_LINKEDIN, "GET", f"{C.LINKEDIN_REST_BASE}/adAccounts/1/adCampaignGroups"),
		]
		for platform, method, url in refused:
			self.assertFalse(client.allowed(platform, method, url), f"{platform} {method} {url}")

	def test_transport_refuses_before_sending(self):
		t, http, _ = transport(C.PLATFORM_GOOGLE, lambda *a: {})
		with self.assertRaises(client.ReadOnlyViolation):
			t.request("POST", f"{C.GOOGLE_ADS_BASE}/customers/123/campaigns:mutate", json={})
		self.assertEqual(http.calls, [], "a refused request must never reach the network")

	def test_allowlist_is_read_shaped(self):
		for platform, method, pattern in C.READ_ONLY_ALLOWLIST:
			self.assertNotIn("mutate", pattern.lower())
			if platform != C.PLATFORM_GOOGLE:
				self.assertEqual(method, "GET", f"{platform} may only GET: {pattern}")
			else:
				self.assertTrue(method == "GET" or pattern.endswith(":search$"), pattern)

	def test_no_mutate_string_anywhere_in_the_module(self):
		# String literals only, docstrings and comments excluded: the constants docstring
		# names ":mutate" to explain what is refused.
		for path in MARKETING.rglob("*.py"):
			tree = ast.parse(path.read_text(encoding="utf-8"))
			docstrings = set()
			for node in ast.walk(tree):
				if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
					first = node.body[0]
					if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
						docstrings.add(id(first.value))
			for node in ast.walk(tree):
				if (
					isinstance(node, ast.Constant)
					and isinstance(node.value, str)
					and id(node) not in docstrings
				):
					self.assertNotIn("mutate", node.value.lower(), f"{path.name}: {node.value!r}")

	def test_scopes_are_read_only_where_the_platform_allows(self):
		self.assertEqual(C.OAUTH[C.PLATFORM_META]["scopes"], ("ads_read",))
		self.assertEqual(set(C.OAUTH[C.PLATFORM_LINKEDIN]["scopes"]), {"r_ads", "r_ads_reporting"})
		# Google has one scope and it is full access; the allowlist + a Read-only user carry it.
		self.assertEqual(C.OAUTH[C.PLATFORM_GOOGLE]["scopes"], ("https://www.googleapis.com/auth/adwords",))


# ---------------------------------------------------------------- 2+3. transport


class TransportTests(unittest.TestCase):
	URL = google_ads.search_url(1234567890)

	def test_429_is_retried_honoring_retry_after(self):
		responses = [
			FakeResponse(429, FIX["google"]["error_quota"], headers={"Retry-After": "7"}),
			{"results": []},
		]
		t, http, sleeps = transport(C.PLATFORM_GOOGLE, lambda *a: responses.pop(0))
		self.assertEqual(t.request("POST", self.URL, json={}), {"results": []})
		self.assertEqual(sleeps, [7.0])
		self.assertEqual(len(http.calls), 2)

	def test_5xx_gives_up_after_max_retries(self):
		t, http, sleeps = transport(
			C.PLATFORM_GOOGLE, lambda *a: FakeResponse(503, {"error": {"message": "busy"}}), max_retries=3
		)
		with self.assertRaises(client.MarketingAPIError) as ctx:
			t.request("POST", self.URL, json={})
		self.assertEqual(ctx.exception.status, 503)
		self.assertEqual(len(http.calls), 4, "one try plus max_retries")
		self.assertEqual(len(sleeps), 3)

	def test_other_4xx_is_never_retried(self):
		for status in (400, 403, 404):
			t, http, sleeps = transport(
				C.PLATFORM_GOOGLE, lambda *a: FakeResponse(status, {"error": {"message": "no"}})
			)
			with self.assertRaises(client.MarketingAPIError):
				t.request("POST", self.URL, json={})
			self.assertEqual((len(http.calls), sleeps), (1, []), status)

	def test_401_is_an_auth_failure_and_not_retried(self):
		t, http, _ = transport(C.PLATFORM_GOOGLE, lambda *a: FakeResponse(401, FIX["google"]["error_auth"]))
		with self.assertRaises(client.MarketingAPIError) as ctx:
			t.request("POST", self.URL, json={})
		self.assertTrue(ctx.exception.is_auth_failure)
		self.assertEqual(len(http.calls), 1)

	def test_transport_errors_are_retried_then_raised_without_detail(self):
		t, http, _ = transport(
			C.PLATFORM_GOOGLE, lambda *a: ConnectionError("https://x/?access_token=LEAKED"), max_retries=2
		)
		with self.assertRaises(client.MarketingAPIError) as ctx:
			t.request("POST", self.URL, json={})
		self.assertIsNone(ctx.exception.status)
		self.assertEqual(len(http.calls), 3)
		self.assertNotIn("LEAKED", str(ctx.exception))
		self.assertIsNone(ctx.exception.__cause__, "raised from None: no chained frames")
		self.assertTrue(ctx.exception.__suppress_context__)

	def test_provider_errors_are_redacted(self):
		body = {
			"error": {
				"message": "bad request Bearer ya29.a0AfH6SMBSECRETSECRET echo access_token=EAAB123456 client_secret=shh"
			}
		}
		t, _, _ = transport(C.PLATFORM_GOOGLE, lambda *a: FakeResponse(400, body))
		with self.assertRaises(client.MarketingAPIError) as ctx:
			t.request("POST", self.URL, json={})
		text = str(ctx.exception)
		for secret in ("ya29.a0AfH6SMBSECRETSECRET", "EAAB123456", "shh"):
			self.assertNotIn(secret, text)

	def test_archived_urls_are_redacted(self):
		archived = []
		url = FIX["meta"]["insights_page_1"]["paging"]["next"]
		t, _, _ = transport(
			C.PLATFORM_META,
			lambda *a: FakeResponse(200, {"data": []}, url=url),
			archive=lambda *a: archived.append(a),
		)
		t.request("GET", url)
		self.assertNotIn("EAAB-TEST-TOKEN", archived[0][2])
		self.assertIn("access_token=REDACTED", archived[0][2])

	def test_backoff_is_capped_and_jittered(self):
		self.assertLessEqual(client.backoff_seconds(20, rand=lambda: 1.0), C.BACKOFF_CAP_SECONDS)
		self.assertEqual(client.backoff_seconds(1, retry_after="9999"), C.RETRY_AFTER_CAP_SECONDS)
		self.assertEqual(client.backoff_seconds(2, rand=lambda: 0.0), C.BACKOFF_BASE_SECONDS * 2 * 0.5)


class RedactionTests(unittest.TestCase):
	def test_url(self):
		out = utils.redact_url(
			"https://graph.facebook.com/v26.0/oauth/access_token?client_id=1&client_secret=S&code=CODE&x=1"
		)
		self.assertNotIn("=S&", out)
		self.assertNotIn("CODE", out)
		self.assertIn("client_id=1", out)

	def test_text(self):
		out = utils.redact_text('{"refresh_token": "1//abcdefgh", "note": "Bearer ya29.zzzzzzzzzz"}')
		self.assertNotIn("1//abcdefgh", out)
		self.assertNotIn("ya29.zzzzzzzzzz", out)


# ---------------------------------------------------------------- window


class WindowTests(unittest.TestCase):
	TODAY = D(2026, 9, 22)

	def test_first_run_backfills(self):
		self.assertEqual(utils.sync_window(None, self.TODAY, 7, 90), (D(2026, 6, 24), D(2026, 9, 21)))

	def test_restates_the_last_n_days(self):
		self.assertEqual(
			utils.sync_window(D(2026, 9, 21), self.TODAY, 7, 90), (D(2026, 9, 15), D(2026, 9, 21))
		)

	def test_a_cursor_left_behind_catches_up(self):
		self.assertEqual(
			utils.sync_window(D(2026, 9, 1), self.TODAY, 7, 90), (D(2026, 8, 26), D(2026, 9, 21))
		)

	def test_zero_restate_pulls_only_new_days(self):
		self.assertEqual(
			utils.sync_window(D(2026, 9, 19), self.TODAY, 0, 90), (D(2026, 9, 20), D(2026, 9, 21))
		)
		self.assertIsNone(utils.sync_window(D(2026, 9, 21), self.TODAY, 0, 90))

	def test_click_days_stop_at_googles_90(self):
		days = utils.click_days(D(2026, 6, 1), D(2026, 9, 21), self.TODAY)
		self.assertEqual(days[0], D(2026, 6, 25))
		self.assertEqual(days[-1], D(2026, 9, 21))
		self.assertEqual(len(days), 89)


# ---------------------------------------------------------------- 5. parsers


class GoogleParserTests(unittest.TestCase):
	G = FIX["google"]

	def test_metrics_micros_and_strings(self):
		rows = google_ads.parse_metrics(
			self.G["metrics_page_1"]["results"] + self.G["metrics_page_2"]["results"]
		)
		self.assertEqual(
			rows[0],
			{
				"campaign_external_id": "21456789012",
				"metric_date": "2026-09-20",
				"impressions": 1200,
				"clicks": 48,
				"spend": 152.34,
				"conversions": 3.0,
			},
		)
		self.assertEqual(rows[2]["conversions"], 0.0, "a missing metric reads as zero")
		self.assertEqual(rows[1]["spend"], 98.77)

	def test_search_follows_page_tokens(self):
		pages = [self.G["metrics_page_1"], self.G["metrics_page_2"]]
		t, http, _ = transport(C.PLATFORM_GOOGLE, lambda *a: pages.pop(0))
		rows = google_ads.fetch_daily_metrics(t, "123-456-7890", D(2026, 9, 20), D(2026, 9, 21))
		self.assertEqual(len(rows), 4)
		self.assertEqual(http.calls[1][2]["json"]["pageToken"], "page-2")
		self.assertIn("customers/1234567890/googleAds:search", http.calls[0][1], "dashes stripped")
		self.assertIn("BETWEEN '2026-09-20' AND '2026-09-21'", http.calls[0][2]["json"]["query"])

	def test_manager_accounts_are_skipped(self):
		def route(method, url, kw):
			if url.endswith("listAccessibleCustomers"):
				return self.G["accessible_customers"]
			return self.G["customer_manager"] if "9876543210" in url else self.G["customer_client"]

		t, _, _ = transport(C.PLATFORM_GOOGLE, route)
		self.assertEqual(
			google_ads.discover_accounts(t),
			[{"external_id": "1234567890", "account_name": "Sapphire Fountains", "currency": "USD"}],
		)

	def test_clicks_and_one_day_queries(self):
		rows = google_ads.parse_clicks(self.G["clicks"]["results"])
		self.assertEqual(
			rows[0],
			{
				"gclid": "Cj0KCQjw-TEST-GCLID-1",
				"campaign_external_id": "21456789012",
				"ad_group_id": "150000000001",
				"click_date": "2026-09-21",
			},
		)
		self.assertIn("segments.date = '2026-09-21'", google_ads.click_query(D(2026, 9, 21)))

	def test_headers(self):
		h = google_ads.headers("AT", "DT", "987-654-3210")
		self.assertEqual(h["login-customer-id"], "9876543210")
		self.assertNotIn("login-customer-id", google_ads.headers("AT", "DT", ""))


class MetaParserTests(unittest.TestCase):
	M = FIX["meta"]

	def test_closed_accounts_are_skipped(self):
		self.assertEqual(
			[a["external_id"] for a in meta_ads.parse_accounts(self.M["adaccounts"]["data"])],
			["111222333444"],
		)

	def test_insights_follow_next_and_count_leads(self):
		pages = [self.M["insights_page_1"], self.M["insights_page_2"]]
		t, http, _ = transport(C.PLATFORM_META, lambda *a: pages.pop(0))
		rows = meta_ads.fetch_daily_metrics(t, "111222333444", D(2026, 9, 20), D(2026, 9, 21))
		self.assertEqual([r["spend"] for r in rows], [63.41, 58.1])
		self.assertEqual([r["conversions"] for r in rows], [2.0, 0])
		self.assertEqual(http.calls[0][2]["params"]["time_increment"], 1)
		self.assertEqual(http.calls[1][1], self.M["insights_page_1"]["paging"]["next"])

	def test_campaign_dates(self):
		rows = meta_ads.parse_campaigns(self.M["campaigns"]["data"])
		self.assertEqual((rows[1]["start_date"], rows[1]["end_date"]), ("2026-06-01", "2026-08-31"))

	def test_token_travels_in_the_header(self):
		self.assertEqual(meta_ads.headers("EAAB")["Authorization"], "Bearer EAAB")


class LinkedInParserTests(unittest.TestCase):
	L = FIX["linkedin"]

	def test_restli_query_is_encoded_exactly(self):
		url = linkedin_ads.analytics_url(507000001, D(2026, 9, 1), D(2026, 9, 21))
		self.assertIn("dateRange=(start:(year:2026,month:9,day:1),end:(year:2026,month:9,day:21))", url)
		self.assertIn("accounts=List(urn%3Ali%3AsponsoredAccount%3A507000001)", url)
		self.assertIn("pivot=CAMPAIGN&timeGranularity=DAILY", url)

	def test_analytics(self):
		rows = linkedin_ads.parse_analytics(self.L["analytics"]["elements"])
		self.assertEqual(
			rows[0],
			{
				"campaign_external_id": "350000001",
				"metric_date": "2026-09-20",
				"impressions": 2200,
				"clicks": 19,
				"spend": 41.07,
				"conversions": 1.0,
			},
		)
		self.assertEqual(rows[1]["conversions"], 0.0)

	def test_accounts_and_campaigns(self):
		self.assertEqual(
			[a["external_id"] for a in linkedin_ads.parse_accounts(self.L["ad_accounts"]["elements"])],
			["507000001"],
		)
		campaigns = linkedin_ads.parse_campaigns(self.L["campaigns"]["elements"])
		self.assertEqual(campaigns[1]["end_date"], "2025-09-01")
		self.assertEqual(campaigns[0]["start_date"], "2025-08-01")

	def test_version_header(self):
		h = linkedin_ads.headers("AQV")
		self.assertEqual(
			(h["LinkedIn-Version"], h["X-Restli-Protocol-Version"]), (C.LINKEDIN_API_VERSION, "2.0.0")
		)


# ---------------------------------------------------------------- frappe stub


class Doc(dict):
	"""Enough of a Document for the sync engine."""

	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError:
			raise AttributeError(key) from None

	def __setattr__(self, key, value):
		self[key] = value

	def set(self, key, value):
		self[key] = value

	def update(self, values=None, **kw):
		dict.update(self, values or {}, **kw)
		return self

	def insert(self, ignore_permissions=False):
		DB.insert(self)
		return self

	def save(self, ignore_permissions=False):
		DB.docs[(self["doctype"], self["name"])] = self
		return self

	def reload(self):
		return self

	def get_password(self, fieldname, raise_exception=True):
		return STATE["secrets"].get(fieldname)

	@property
	def meta(self):
		return types.SimpleNamespace(has_field=lambda f: True)


class FakeDB:
	def __init__(self):
		self.docs = {}
		self.seq = 0
		self.sql_calls = []

	def name_for(self, doc):
		dt = doc["doctype"]
		if dt == "Ad Account":
			return f"ADACC-{doc['platform']}-{doc['external_id']}"
		if dt == "Ad Campaign":
			return f"ADCMP-{doc['ad_account']}-{doc['external_id']}"
		if dt == "Ad Daily Metric":
			return f"ADM-{doc['campaign']}-{doc['metric_date']}"
		if dt == "Ad Click":
			return doc["gclid"]
		self.seq += 1
		return f"{dt}-{self.seq}"

	def insert(self, doc):
		doc["name"] = doc.get("name") or self.name_for(doc)
		key = (doc["doctype"], doc["name"])
		if key in self.docs:
			raise sys.modules["frappe"].DuplicateEntryError(key)
		self.docs[key] = doc

	def of(self, doctype):
		return [d for (dt, _), d in self.docs.items() if dt == doctype]


DB = FakeDB()
STATE = {}


def install_frappe():
	frappe = types.ModuleType("frappe")

	class DuplicateEntryError(Exception):
		pass

	class UniqueValidationError(Exception):
		pass

	frappe.DuplicateEntryError = DuplicateEntryError
	frappe.UniqueValidationError = UniqueValidationError
	frappe._ = lambda s: s

	def get_doc(arg, name=None):
		if isinstance(arg, dict):
			return Doc(arg)
		if name is None and arg in (C.SETTINGS_DOCTYPE, C.CREDENTIALS_DOCTYPE):
			return STATE[arg]
		return DB.docs[(arg, name)]

	frappe.get_doc = get_doc
	frappe.get_cached_doc = lambda dt: STATE[dt]

	def get_all(doctype, filters=None, fields=None, **kw):
		rows = DB.of(doctype)
		for key, value in (filters or {}).items():
			rows = [r for r in rows if r.get(key) == value]
		return [Doc({f: r.get(f) for f in fields or ["name"]}) for r in rows]

	frappe.get_all = get_all

	def set_value(doctype, name, values, value=None, update_modified=True):
		if not isinstance(values, dict):
			values = {values: value}
		DB.docs[(doctype, name)].update(values)

	cache = {}
	frappe.cache = lambda: types.SimpleNamespace(
		set_value=lambda k, v, expires_in_sec=None: cache.__setitem__(k, v),
		get_value=lambda k: cache.get(k),
		delete_value=lambda k: cache.pop(k, None),
	)
	frappe.db = types.SimpleNamespace(
		exists=lambda doctype, name=None: (doctype, name) in DB.docs
		or (doctype == "Currency" and name == "USD"),
		set_value=set_value,
		get_value=lambda doctype, name, field: DB.docs[(doctype, name)].get(field),
		commit=lambda: None,
		rollback=lambda: None,
		has_column=lambda dt, col: True,
		sql=lambda *a, **k: DB.sql_calls.append(a),
		set_single_value=lambda dt, k, v: STATE[dt].update({k: v}),
	)
	frappe.enqueue = lambda method, **kw: STATE["enqueued"].append((method, kw))
	frappe.log_error = lambda *a, **k: STATE["errors"].append(a)
	frappe.get_traceback = lambda: "traceback"

	fu = types.ModuleType("frappe.utils")
	fu.getdate = (
		lambda v: v
		if isinstance(v, D) and not isinstance(v, datetime.datetime)
		else (v.date() if isinstance(v, datetime.datetime) else D.fromisoformat(str(v)))
	)
	fu.get_datetime = (
		lambda v: v if isinstance(v, datetime.datetime) else datetime.datetime.fromisoformat(str(v))
	)
	fu.now_datetime = lambda: STATE["now"]
	fu.cint = lambda v: int(v or 0)
	fu.add_days = lambda d, n: d + datetime.timedelta(days=n)
	fu.get_url = lambda: "https://erp.example"
	frappe.utils = fu
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = Doc
	model.document = document
	frappe.model = model
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = fu
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document


_saved = {}
STUBBED = ("frappe", "frappe.utils", "frappe.model", "frappe.model.document")


def setUpModule():
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	install_frappe()


def tearDownModule():
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]
	for name in list(sys.modules):
		if name.startswith("erpnext_enhancements.marketing.doctype.ad_daily_metric"):
			sys.modules.pop(name, None)


def reset(**settings):
	DB.docs.clear()
	DB.sql_calls.clear()
	STATE.clear()
	STATE.update(
		{
			C.SETTINGS_DOCTYPE: Doc(
				{
					"doctype": C.SETTINGS_DOCTYPE,
					"name": C.SETTINGS_DOCTYPE,
					"enabled": 1,
					"google_ads_enabled": 1,
					"restate_days": 7,
					"initial_backfill_days": 3,
					"max_retries": 1,
					**settings,
				}
			),
			C.CREDENTIALS_DOCTYPE: Doc(
				{
					"doctype": C.CREDENTIALS_DOCTYPE,
					"name": C.CREDENTIALS_DOCTYPE,
					"google_ads_client_id": "cid",
					"google_ads_connection_status": "Connected",
				}
			),
			"secrets": {
				"google_ads_client_secret": "csecret",
				"google_ads_refresh_token": "1//refresh",
				"google_ads_developer_token": "devtok",
			},
			"now": datetime.datetime(2026, 9, 22, 3, 25),
			"enqueued": [],
			"errors": [],
		}
	)


def google_route(fail_metrics=None):
	"""A whole Google Ads account behind a fake network."""
	G = FIX["google"]

	def route(method, url, kw):
		if url == C.OAUTH[C.PLATFORM_GOOGLE]["token_url"]:
			return G["token_refresh"]
		if url.endswith("listAccessibleCustomers"):
			return G["accessible_customers"]
		query = (kw.get("json") or {}).get("query", "")
		if "FROM customer" in query:
			return G["customer_manager"] if "9876543210" in url else G["customer_client"]
		if "FROM campaign WHERE" in query:
			if fail_metrics:
				return FakeResponse(fail_metrics, {"error": {"message": "backend"}})
			return {"results": G["metrics_page_1"]["results"] + G["metrics_page_2"]["results"]}
		if "FROM campaign" in query:
			return G["campaigns"]
		if "FROM click_view" in query:
			day = query.rsplit("'", 2)[1]
			return G["clicks"] if day == "2026-09-21" else {"results": []}
		raise AssertionError(f"unexpected {method} {url} {query}")

	return FakeHTTP(route)


# ---------------------------------------------------------------- 4. sync engine


class SyncEngineTests(unittest.TestCase):
	def run_google(self, http):
		from erpnext_enhancements.marketing.core import sync

		return sync.run_platform(C.PLATFORM_GOOGLE, http=http, today=D(2026, 9, 22))

	def test_clean_run_writes_everything_and_moves_the_cursor(self):
		reset()
		http = google_route()
		log = self.run_google(http)
		self.assertEqual(DB.docs[("Marketing Sync Log", log)]["status"], "Completed")
		accounts = DB.of("Ad Account")
		self.assertEqual(
			[a["external_id"] for a in accounts], ["1234567890"], "the manager account is not an ad account"
		)
		self.assertEqual(accounts[0]["sync_cursor"], D(2026, 9, 21))
		metrics = DB.of("Ad Daily Metric")
		self.assertEqual(len(metrics), 4)
		self.assertEqual({m["currency"] for m in metrics}, {"USD"})
		self.assertIn(
			("Ad Campaign", "ADCMP-ADACC-Google Ads-1234567890-99999999999"),
			DB.docs,
			"spend of a campaign missing from the list is kept",
		)
		self.assertEqual(
			sorted(c["gclid"] for c in DB.of("Ad Click")), ["Cj0KCQjw-TEST-GCLID-1", "Cj0KCQjw-TEST-GCLID-2"]
		)
		for payload in DB.of("Marketing Raw Payload"):
			self.assertNotIn("1//refresh", json.dumps(payload, default=str))
			self.assertNotIn("ya29.TEST-ACCESS-TOKEN", payload["endpoint"])

	def test_a_rerun_restates_without_adding_rows(self):
		reset()
		self.run_google(google_route())
		before = (len(DB.of("Ad Daily Metric")), len(DB.of("Ad Click")), len(DB.of("Ad Campaign")))
		self.run_google(google_route())
		after = (len(DB.of("Ad Daily Metric")), len(DB.of("Ad Click")), len(DB.of("Ad Campaign")))
		self.assertEqual(before, after)

	def test_a_failure_leaves_the_cursor_where_it_was(self):
		reset()
		self.run_google(google_route())
		account = DB.of("Ad Account")[0]
		account["sync_cursor"] = D(2026, 9, 18)
		log = self.run_google(google_route(fail_metrics=503))
		self.assertEqual(DB.docs[("Marketing Sync Log", log)]["status"], "Failed")
		self.assertEqual(account["sync_cursor"], D(2026, 9, 18), "the window is reprocessed next run")
		self.assertIn("503", account["status_message"])

	def test_a_dead_credential_marks_the_platform(self):
		reset()

		def route(method, url, kw):
			return FakeResponse(400, FIX["google"]["token_invalid_grant"])

		log = self.run_google(FakeHTTP(route))
		creds = STATE[C.CREDENTIALS_DOCTYPE]
		self.assertEqual(creds["google_ads_connection_status"], "Auth Failed")
		self.assertEqual(DB.docs[("Marketing Sync Log", log)]["status"], "Failed")
		self.assertNotIn("csecret", creds["google_ads_status_message"])

	def test_a_disabled_account_is_left_alone(self):
		reset()
		self.run_google(google_route())
		account = DB.of("Ad Account")[0]
		account["enabled"] = 0
		account["sync_cursor"] = D(2026, 9, 10)
		self.run_google(google_route())
		self.assertEqual(account["sync_cursor"], D(2026, 9, 10))
		self.assertEqual(account["enabled"], 0, "discovery must not switch it back on")


# ---------------------------------------------------------------- tasks


class TaskShimTests(unittest.TestCase):
	def test_master_switch_off_does_nothing(self):
		reset(enabled=0)
		from erpnext_enhancements.marketing.core import tasks

		self.assertIsNone(tasks.nightly_ad_spend_sync())
		self.assertEqual(STATE["enqueued"], [])

	def test_throttled_inside_twenty_hours(self):
		reset(last_sync_started_at=datetime.datetime(2026, 9, 21, 22, 0))
		from erpnext_enhancements.marketing.core import tasks

		self.assertIsNone(tasks.nightly_ad_spend_sync())

	def test_disconnected_platforms_are_skipped(self):
		reset()
		STATE[C.CREDENTIALS_DOCTYPE]["google_ads_connection_status"] = "Auth Failed"
		from erpnext_enhancements.marketing.core import tasks

		self.assertIsNone(tasks.nightly_ad_spend_sync())

	def test_due_and_connected_enqueues_once_on_long(self):
		reset(last_sync_started_at=datetime.datetime(2026, 9, 21, 3, 25))
		from erpnext_enhancements.marketing.core import tasks

		self.assertEqual(tasks.nightly_ad_spend_sync(), [C.PLATFORM_GOOGLE])
		method, kw = STATE["enqueued"][0]
		self.assertEqual(method, "erpnext_enhancements.marketing.core.tasks.run_sync")
		self.assertEqual((kw["queue"], kw["job_id"], kw["deduplicate"]), ("long", C.SYNC_JOB_ID, True))

	def test_prune_keeps_clicks_a_lead_carries(self):
		reset()
		from erpnext_enhancements.marketing.core import sync

		sync.prune(today=D(2026, 9, 22))
		click_sql = next(call[0] for call in DB.sql_calls if "Ad Click" in call[0])
		self.assertIn("not in (select custom_gclid from `tabLead`", click_sql)


# ---------------------------------------------------------------- 6. OAuth


class OAuthTests(unittest.TestCase):
	def test_state_is_single_use_and_bound_to_the_user(self):
		reset()
		from erpnext_enhancements.marketing.core import oauth

		state = oauth.mint_state(C.PLATFORM_META, "nik@example.com")
		self.assertIsNone(oauth.consume_state(state, "someone@example.com"))
		state = oauth.mint_state(C.PLATFORM_META, "nik@example.com")
		self.assertEqual(oauth.consume_state(state, "nik@example.com"), C.PLATFORM_META)
		self.assertIsNone(oauth.consume_state(state, "nik@example.com"), "single use")

	def test_authorization_url(self):
		reset()
		from erpnext_enhancements.marketing.core import oauth

		url = oauth.authorization_url(C.PLATFORM_GOOGLE, "cid", "S")
		self.assertIn("access_type=offline", url)
		self.assertIn("prompt=consent", url)
		self.assertIn(
			"redirect_uri=https%3A%2F%2Ferp.example%2Fapi%2Fmethod%2Ferpnext_enhancements.marketing.api.oauth_callback",
			url,
		)

	def test_meta_exchange_is_two_step_and_records_expiry(self):
		reset()
		from erpnext_enhancements.marketing.core import oauth

		creds = STATE[C.CREDENTIALS_DOCTYPE]
		STATE["secrets"]["meta_client_secret"] = "msecret"
		creds["meta_client_id"] = "app"
		replies = [FIX["meta"]["token_short"], FIX["meta"]["token_long"]]
		http = FakeHTTP(lambda *a: replies.pop(0))
		oauth.exchange_code(C.PLATFORM_META, "CODE", creds, http=http)
		self.assertEqual(creds["meta_access_token"], "EAAB-LONG-TOKEN")
		self.assertEqual(http.calls[1][2]["params"]["fb_exchange_token"], "EAAB-SHORT-TOKEN")
		# 5,183,944 s is a second under 60 days: 2026-09-22 03:25 -> 2026-11-21 03:24.
		self.assertEqual(creds["meta_access_token_expires_on"].date(), D(2026, 11, 21))

	def test_token_errors_never_carry_the_request(self):
		reset()
		from erpnext_enhancements.marketing.core import client as c
		from erpnext_enhancements.marketing.core import oauth

		http = FakeHTTP(
			lambda *a: FakeResponse(400, {"error": "invalid_grant", "error_description": "bad code THE-CODE"})
		)
		creds = STATE[C.CREDENTIALS_DOCTYPE]
		with self.assertRaises(c.MarketingAPIError) as ctx:
			oauth.exchange_code(C.PLATFORM_GOOGLE, "THE-CODE", creds, http=http)
		self.assertTrue(ctx.exception.is_auth_failure)
		self.assertNotIn("csecret", str(ctx.exception))

	def test_linkedin_refreshes_inside_seven_days(self):
		reset()
		from erpnext_enhancements.marketing.core import oauth

		creds = STATE[C.CREDENTIALS_DOCTYPE]
		creds["linkedin_client_id"] = "li"
		creds["linkedin_access_token_expires_on"] = datetime.datetime(2026, 9, 25)
		STATE["secrets"].update(
			{"linkedin_access_token": "OLD", "linkedin_refresh_token": "R", "linkedin_client_secret": "S"}
		)
		http = FakeHTTP(lambda *a: FIX["linkedin"]["token_exchange"])
		self.assertEqual(oauth.access_token(C.PLATFORM_LINKEDIN, creds, http=http), "AQV-TEST-ACCESS")
		self.assertEqual(http.calls[0][2]["data"]["grant_type"], "refresh_token")


# ---------------------------------------------------------------- wiring


class WiringTests(unittest.TestCase):
	def test_endpoints_are_post_except_the_callback_and_none_is_guest(self):
		source = (MARKETING / "core" / "api.py").read_text(encoding="utf-8")
		tree = ast.parse(source)
		for node in tree.body:
			if not isinstance(node, ast.FunctionDef):
				continue
			for dec in node.decorator_list:
				text = ast.unparse(dec)
				if "whitelist" not in text:
					continue
				self.assertNotIn("allow_guest", text, node.name)
				expected = "methods=['GET']" if node.name == "oauth_callback" else "methods=['POST']"
				self.assertIn(expected, text, node.name)
		self.assertIn("_require_operator()", source)

	def test_the_registered_redirect_path_resolves(self):
		source = (MARKETING / "api.py").read_text(encoding="utf-8")
		self.assertIn("oauth_callback", source)

	def test_scheduler(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		self.assertIn(
			'"25 3 * * *": ["erpnext_enhancements.marketing.core.tasks.nightly_ad_spend_sync"]', hooks
		)
		self.assertIn('"erpnext_enhancements.marketing.core.tasks.daily_prune"', hooks)

	def test_credentials_are_system_manager_only(self):
		doc = json.loads(
			(MARKETING / "doctype" / "marketing_credentials" / "marketing_credentials.json").read_text(
				encoding="utf-8"
			)
		)
		self.assertEqual([p["role"] for p in doc["permissions"]], ["System Manager"])
		tokens = [f for f in doc["fields"] if f["fieldname"].endswith(("_access_token", "_refresh_token"))]
		self.assertEqual(len(tokens), 4)
		for f in tokens:
			self.assertEqual((f["fieldtype"], f.get("hidden")), ("Password", 1), f["fieldname"])

	def test_every_credential_field_the_code_reads_exists(self):
		doc = json.loads(
			(MARKETING / "doctype" / "marketing_credentials" / "marketing_credentials.json").read_text(
				encoding="utf-8"
			)
		)
		names = {f["fieldname"] for f in doc["fields"]}
		for platform in C.PLATFORMS:
			for suffix in (
				"client_id",
				"client_secret",
				"connection_status",
				"status_message",
				"connected_on",
				"connected_by",
				"last_error_at",
			):
				self.assertIn(utils.field(platform, suffix), names)
		self.assertIn("google_ads_developer_token", names)
		self.assertIn("google_ads_login_customer_id", names)
		self.assertIn("meta_access_token_expires_on", names)
		self.assertIn("linkedin_refresh_token_expires_on", names)


if __name__ == "__main__":
	unittest.main()
