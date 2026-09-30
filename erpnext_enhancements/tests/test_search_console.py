"""Bench-free tests for the Search Console property fallback and backfill (TASK-2026-01474).

Prod stores ``GA4 Settings.gsc_property_url`` as the bare ``sapphirefountains.com``, which
Google reads as the one URL prefix ``http://sapphirefountains.com/`` and refuses with the
same 403 as a missing grant. These pin:

* the candidate forms tried for a stored value, Domain property first (decided 2026-09-22);
* that a refused form falls through to the next, the accepted one is cached and tried first
  next time, and anything other than a refusal still raises;
* that a total refusal names every form tried and the service account to add;
* the backfill: one query across the gap, each snapshot's rolling 30-day sum computed
  locally, only ``gsc_ok = 0`` rows touched, and ``dry_run`` writing nothing;
* the network retry (v1.561.2): the three queries run one after another on the calling
  thread, each attempt on a transport of its own with an explicit timeout; a TLS error or a
  timeout is retried with backoff, a 4xx and a certificate failure never are, and a failure
  that outlasts the retries writes exactly one short Error Log row;
* that a one-line message (that one, and the refusal) is stored as the row's body under the
  title ``GSC API Error``, not as the title itself (see ``stored_row``). Since v1.567.1 that is
  ``log_error_throttled`` passing ``title=`` and ``message=`` by keyword, not a trailing newline
  added here, so the body is the message exactly;
* that the key is loaded with a Search Console scope, and that the scoped object is the one
  every per-attempt transport authorizes with. Every other test replaces ``_gsc_service``
  whole, so ``CredentialScopeTests`` runs the real one.

``api/analytics.py`` imports Google's client libraries at module level and CI installs none
of them, so they are stubbed here along with frappe, and removed again afterwards.

Run: python -m unittest erpnext_enhancements.tests.test_search_console -v
"""

import datetime
import ssl
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

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
	"google_auth_httplib2",
	"httplib2",
)
OURS = ("erpnext_enhancements.api.analytics", "erpnext_enhancements.utils.error_throttle")
_saved = {}
STATE = {}
analytics = None
#: analytics._gsc_service as imported, before ``reset()`` replaces it.
REAL_GSC_SERVICE = None
DOMAIN = "sc-domain:sapphirefountains.com"
#: What _gsc_service hands back. The token is a plain placeholder, deliberately not shaped like
#: a real Google one: it is here to prove it never reaches a log row.
CREDS = types.SimpleNamespace(
	service_account_email="ga4-reader@proj.iam.gserviceaccount.com", token="fixture-token-value"
)


class _dict(dict):
	__getattr__ = dict.get


class HttpError(Exception):
	def __init__(self, status):
		super().__init__(f"HTTP {status}")
		self.resp = types.SimpleNamespace(status=status)


class FakeHttp:
	"""httplib2.Http: remembers its timeout and whether it was closed."""

	def __init__(self, timeout=None):
		self.timeout = timeout
		self.closed = False

	def close(self):
		self.closed = True


class FakeAuthorizedHttp:
	"""google_auth_httplib2.AuthorizedHttp over a FakeHttp."""

	def __init__(self, credentials, http=None):
		self.credentials = credentials
		self.http = http
		self.closed = False

	def close(self):
		self.closed = True
		self.http.close()


class FakeService:
	"""Search Console that accepts only ``accepts`` and answers ``rows``.

	``script`` maps a dimension (``date``, ``query``, ``page``) to the exceptions its attempts
	raise, in order, before it answers. Every execution is recorded in ``executions`` as
	``(dimension, http, thread id)``.
	"""

	def __init__(self, accepts, rows=(), fail_with=None, script=None):
		self.accepts = accepts
		self.rows = list(rows)
		self.fail_with = fail_with
		self.script = {k: list(v) for k, v in (script or {}).items()}
		self.calls = []
		self.executions = []

	def searchanalytics(self):
		return self

	def query(self, siteUrl, body):
		self.calls.append((siteUrl, body))
		service = self
		dimension = ",".join(body.get("dimensions") or [])

		class Request:
			def execute(self_inner, http=None):
				service.executions.append((dimension, http, threading.get_ident()))
				if service.fail_with:
					raise HttpError(service.fail_with)
				if siteUrl != service.accepts:
					raise HttpError(403)
				pending = service.script.get(dimension)
				if pending:
					raise pending.pop(0)
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
	frappe.log_error = lambda *a, **k: STATE["errors"].append((a, k))
	# Multi-line, as a real one is: v16 picks the Error Log title by whether the first argument
	# holds a newline, so a one-line fake would model a (traceback, title) call backwards.
	frappe.get_traceback = lambda: 'Traceback (most recent call last):\n  File "analytics.py"\nHttpError: 400'
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
	mods["google_auth_httplib2"].AuthorizedHttp = FakeAuthorizedHttp
	mods["httplib2"].Http = FakeHttp
	for name, module in mods.items():
		sys.modules[name] = module


def setUpModule():
	global analytics, REAL_GSC_SERVICE
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install()
	from erpnext_enhancements.api import analytics as module

	analytics = module
	REAL_GSC_SERVICE = module._gsc_service


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
	analytics._gsc_service = lambda settings: (service, CREDS, None)


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
		site, _response, refusals = analytics._gsc_query_first_site(
			service, CREDS, "sapphirefountains.com", {}
		)
		self.assertEqual(site, "https://www.sapphirefountains.com/")
		self.assertEqual(refusals, [("sc-domain:sapphirefountains.com", 403)])
		service.calls.clear()
		analytics._gsc_query_first_site(service, CREDS, "sapphirefountains.com", {})
		self.assertEqual(
			[c[0] for c in service.calls], ["https://www.sapphirefountains.com/"], "cached form first"
		)

	def test_other_errors_still_raise(self):
		service = FakeService(accepts="x", fail_with=500)
		reset(service)
		with self.assertRaises(HttpError):
			analytics._gsc_query_first_site(service, CREDS, "sapphirefountains.com", {})

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


# ---------------------------------------------------------------- the network retry


def _record_layer_failure():
	return ssl.SSLError(1, "[SSL: RECORD_LAYER_FAILURE] record layer failure (_ssl.c:2713)")


def _read_timeout():
	return TimeoutError("The read operation timed out")


class TransientRetryTests(unittest.TestCase):
	"""Every nightly pull from 2026-09-23 to 2026-09-29 failed inside the query or page fetch,
	which ran in parallel through one shared httplib2 transport (TASK-2026-01474)."""

	ROWS = [{"keys": ["2026-09-20"], "clicks": 3, "impressions": 40, "ctr": 0.075, "position": 4.2}]

	def setUp(self):
		self.sleeps = []
		patcher = mock.patch.object(analytics, "time", types.SimpleNamespace(sleep=self.sleeps.append))
		patcher.start()
		self.addCleanup(patcher.stop)

	def _run(self, script=None, accepts=DOMAIN):
		service = FakeService(accepts=accepts, rows=self.ROWS, script=script)
		reset(service)
		return service, analytics.get_gsc_data()

	@staticmethod
	def _attempts(service, dimension):
		return [e for e in service.executions if e[0] == dimension]

	def test_the_queries_run_one_at_a_time_on_the_calling_thread(self):
		service, result = self._run()
		self.assertEqual([e[0] for e in service.executions], ["date", "query", "page"])
		self.assertEqual({e[2] for e in service.executions}, {threading.get_ident()}, "no worker thread")
		# The snapshot's contract is unchanged.
		self.assertEqual(set(result), {"search_timeline", "top_queries", "top_pages", "property"})
		self.assertEqual(result["top_pages"][0]["ctr"], 7.5)
		self.assertEqual((STATE["errors"], self.sleeps), ([], []))

	def test_every_attempt_has_a_transport_of_its_own_with_a_timeout(self):
		service, _result = self._run()
		transports = [e[1] for e in service.executions]
		self.assertEqual(len({id(t) for t in transports}), len(transports), "never shared")
		for transport in transports:
			self.assertIsInstance(transport, FakeAuthorizedHttp)
			self.assertIs(transport.credentials, CREDS)
			self.assertEqual(transport.http.timeout, analytics.GSC_HTTP_TIMEOUT)
			self.assertTrue(transport.closed, "closed after use")

	def test_a_tls_failure_is_retried_on_a_new_connection(self):
		service, result = self._run({"page": [_record_layer_failure()]})
		self.assertEqual(result["property"], DOMAIN)
		attempts = self._attempts(service, "page")
		self.assertEqual(len(attempts), 2)
		self.assertIsNot(attempts[0][1], attempts[1][1])
		self.assertEqual(self.sleeps, [analytics.GSC_BACKOFF_SECONDS])
		self.assertEqual(STATE["errors"], [], "a failure that recovered logs nothing")

	def test_a_timeout_is_retried_with_backoff(self):
		service, result = self._run({"query": [_read_timeout(), _read_timeout()]})
		self.assertEqual(result["property"], DOMAIN)
		self.assertEqual(len(self._attempts(service, "query")), 3)
		self.assertEqual(self.sleeps, [analytics.GSC_BACKOFF_SECONDS, analytics.GSC_BACKOFF_SECONDS * 2])
		self.assertEqual(STATE["errors"], [])

	def test_a_4xx_is_never_retried(self):
		for status in (400, 429):
			with self.subTest(status=status):
				self.sleeps.clear()
				service, result = self._run({"query": [HttpError(status)]})
				self.assertEqual(len(self._attempts(service, "query")), 1)
				self.assertEqual(self._attempts(service, "page"), [], "stopped at the failure")
				self.assertEqual(self.sleeps, [])
				self.assertEqual(result["error"], f"Failed to fetch GSC data: HTTP {status}")
				self.assertEqual(len(STATE["errors"]), 1)

	def test_a_refused_form_is_not_retried_either(self):
		service, result = self._run(accepts="nothing-matches")
		forms = sc.site_candidates("sapphirefountains.com")
		self.assertEqual([e[0] for e in service.executions], ["date"] * len(forms), "each form once")
		self.assertEqual(self.sleeps, [])
		self.assertIn("refused every form", result["error"])

	def test_a_certificate_failure_is_not_retried(self):
		failure = ssl.SSLCertVerificationError(1, "certificate verify failed")
		service, result = self._run({"query": [failure]})
		self.assertEqual(len(self._attempts(service, "query")), 1)
		self.assertEqual(self.sleeps, [])
		self.assertIn("certificate verify failed", result["error"])

	def test_a_failure_that_outlasts_the_retries_logs_one_short_row(self):
		failures = [_record_layer_failure() for _ in range(analytics.GSC_ATTEMPTS)]
		service, result = self._run({"page": failures})
		self.assertEqual(len(self._attempts(service, "page")), analytics.GSC_ATTEMPTS)
		self.assertEqual(self.sleeps, [2, 4])
		self.assertEqual(len(STATE["errors"]), 1, "one row, not one per attempt")
		title, body = stored_row(STATE["errors"][0])
		self.assertEqual(title, "GSC API Error", "the row's title, not the message")
		self.assertIn("'page' query after 3 attempts", body)
		self.assertIn("SSLError: [SSL: RECORD_LAYER_FAILURE]", body)
		self.assertNotEqual(body, frappe_traceback(), "the message, not a traceback")
		self.assertNotIn(CREDS.token, body)
		self.assertEqual(result, {"error": f"Failed to fetch GSC data: {body}"})

	def test_a_refusal_row_is_titled_the_right_way_round_too(self):
		# The v1.505.0 refusal message is one line, so it would have been stored backwards.
		_service, result = self._run(accepts="nothing-matches")
		self.assertEqual(len(STATE["errors"]), 1)
		title, body = stored_row(STATE["errors"][0])
		self.assertEqual(title, "GSC API Error")
		self.assertEqual(body, result["error"])

	def test_a_4xx_row_is_the_traceback_under_the_title(self):
		# A positional (traceback, title) call is right on v16 only because a traceback holds a
		# newline, so the fake one must too: a one-line fake would store it backwards.
		traceback = frappe_traceback()
		self.assertIn("\n", traceback)
		self.assertEqual(stored_row(((traceback, "GSC API Error"), {})), ("GSC API Error", traceback))
		self._run({"query": [HttpError(400)]})
		self.assertEqual(len(STATE["errors"]), 1)
		self.assertEqual(stored_row(STATE["errors"][0]), ("GSC API Error", traceback))

	def test_the_final_error_carries_no_chained_exception(self):
		class AlwaysTimesOut:
			def execute(self, http=None):
				raise _read_timeout()

		with self.assertRaises(analytics.GscUnavailable) as caught:
			analytics._gsc_execute(AlwaysTimesOut(), CREDS, "page")
		self.assertIsNone(caught.exception.__cause__)
		self.assertTrue(caught.exception.__suppress_context__, "raised from None")
		self.assertIn("TimeoutError: The read operation timed out", str(caught.exception))


# ---------------------------------------------------------------- the credentials


class FakeCredentials:
	"""``service_account.Credentials`` as loaded from a key file: keeps the scopes it was given.

	The real class does the same, and a copy of it is what ``build()`` scopes when these are
	empty. The copy stays with the service's own transport, which ``_gsc_execute`` never uses.
	"""

	service_account_email = "ga4-reader@proj.iam.gserviceaccount.com"
	token = "fixture-token-value"

	def __init__(self, path, scopes=None, **kwargs):
		self.path = path
		self.scopes = scopes
		self.kwargs = kwargs


class CredentialScopeTests(unittest.TestCase):
	"""The v1.561.2 draft loaded the key with no scope. ``build()`` scoped only a copy, for the
	service's own transport, so every transport ``_gsc_execute`` built asked Google for a token
	with an empty ``scope`` claim: 400 ``invalid_scope`` before a single query, for the nightly
	pull, the dashboard and the backfill alike. Every other test here replaces ``_gsc_service``
	whole, so these run the real one, with only the key loader and ``build`` stubbed."""

	def setUp(self):
		tmp = tempfile.TemporaryDirectory()
		self.addCleanup(tmp.cleanup)
		files = Path(tmp.name, "private", "files")
		files.mkdir(parents=True)
		(files / "sa.json").write_text("{}")
		self.loaded = []
		self.built = []
		self.service = FakeService(accepts=DOMAIN, rows=TransientRetryTests.ROWS)

		def load(path, **kwargs):
			self.loaded.append(FakeCredentials(path, **kwargs))
			return self.loaded[-1]

		def build(name, version, credentials=None, **kwargs):
			self.built.append((name, version, credentials))
			return self.service

		reset(self.service)
		analytics._gsc_service = REAL_GSC_SERVICE
		for target, attr, value in (
			(analytics.frappe, "get_site_path", lambda *parts: str(Path(tmp.name, *parts))),
			(analytics.service_account.Credentials, "from_service_account_file", load),
			(analytics, "build", build),
		):
			patcher = mock.patch.object(target, attr, value, create=True)
			patcher.start()
			self.addCleanup(patcher.stop)

	def _assert_scoped(self, credentials):
		self.assertTrue(credentials.scopes, "a key loaded with no scope is refused a token")
		for scope in credentials.scopes:
			self.assertTrue(scope.startswith("https://www.googleapis.com/auth/webmasters"), scope)

	def test_the_key_is_loaded_with_a_search_console_scope(self):
		service, credentials, error = analytics._gsc_service(STATE["settings"])
		self.assertIsNone(error)
		self.assertIs(service, self.service)
		self.assertEqual(len(self.loaded), 1)
		self.assertIs(credentials, self.loaded[0], "the object that was loaded, not a copy")
		self.assertTrue(credentials.path.endswith("sa.json"))
		self._assert_scoped(credentials)
		self.assertEqual(list(credentials.scopes), list(analytics.GSC_SCOPES))
		self.assertIs(self.built[0][2], credentials, "build() gets the same scoped object")

	def test_every_transport_the_pull_builds_carries_the_scope(self):
		result = analytics.get_gsc_data()
		self.assertEqual(result["property"], DOMAIN)
		transports = [e[1] for e in self.service.executions]
		self.assertEqual(len(transports), 3, "date, query, page")
		for transport in transports:
			self.assertIs(transport.credentials, self.loaded[0])
			self._assert_scoped(transport.credentials)

	def test_the_backfill_transport_carries_it_too(self):
		STATE["snapshots"] = [{"name": "MWS-2026-09-20", "snapshot_date": D(2026, 9, 20), "pull_error": ""}]
		result = analytics.backfill_gsc_snapshots(dry_run=1)
		self.assertEqual(result["rows"][0]["organic_clicks_30"], 3)
		self.assertEqual(len(self.service.executions), 1, "one query for the gap")
		self._assert_scoped(self.service.executions[0][1].credentials)


def frappe_traceback():
	return sys.modules["frappe"].get_traceback()


def stored_row(call):
	"""``(title, body)`` of the Error Log row Frappe v16 writes for one ``frappe.log_error`` call.

	``log_error(title=None, message=None, ...)`` binds its arguments by position or keyword, then,
	given a message, swaps the two when -- and only when -- ``title`` holds a newline
	(``frappe/utils/error.py`` at v16.35.0). So the order the caller meant decides nothing; the
	content does. The stub records the raw ``(args, kwargs)``; this reads them the way the real
	function does, keywords included.
	"""
	args, kwargs = call
	bound = {**dict(zip(("title", "message"), args, strict=False)), **kwargs}
	title, message = bound.get("title"), bound.get("message")
	if message and "\n" in title:
		return message, title
	return title, message


if __name__ == "__main__":
	unittest.main()
