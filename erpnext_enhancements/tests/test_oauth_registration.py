"""``oauth_registration.py``: which OAuth clients may register themselves, and how often.

Covers the redirect-URI allow-list (allowed and refused URIs, mixed lists, unusual forms), the
site-config entries, the request matcher, the per-address rate, and the ``before_request`` hook
itself: it must leave every other request alone without raising, and refuse a matching request
with a JSON body, failing closed.

A stand-in ``frappe`` is put in ``sys.modules`` in ``setUpModule`` and taken out again in
``tearDownModule``. werkzeug is not installed in CI, so the refusal tests give the module a small
stand-in for it; one test runs against the real werkzeug when it is importable.

Run: python -m unittest erpnext_enhancements.tests.test_oauth_registration
"""

import ast
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

HOOKS = REPO_ROOT / "erpnext_enhancements" / "hooks.py"
HOOK_PATH = "erpnext_enhancements.oauth_registration.guard_registration"

FRAPPE_PATH = "/api/method/frappe.integrations.oauth2.register_client"
FAC_PATH = "/api/method/frappe_assistant_core.api.oauth_registration.register_client"

CLAUDE = "https://claude.ai/api/mcp/auth_callback"
GOOGLE = [
	"https://oauth-redirect.googleusercontent.com/r/example-project",
	"https://oauth-redirect.googleusercontent.com/r/example-project-2",
	"https://oauth-redirect-sandbox.googleusercontent.com/r/example-project",
	"https://oauth-redirect-sandbox.googleusercontent.com/r/example-project-2",
	"https://oauth-redirect-test.googleusercontent.com/r/example-project",
	"https://oauth-redirect-test.googleusercontent.com/r/example-project-2",
]

STATE = {}
_saved_modules = {}
oauth = None


class _Request:
	def __init__(self, method="POST", path=FRAPPE_PATH, body=None, body_raises=False):
		self.method = method
		self.path = path
		self.body = body
		self.body_raises = body_raises
		self.body_reads = 0

	def get_json(self, silent=False):
		self.body_reads += 1
		if self.body_raises:
			raise AssertionError("the body must not be read")
		return self.body


class _ExplodingRequest:
	"""A request whose every attribute raises: the hook must still not raise."""

	def __getattr__(self, name):
		raise RuntimeError(name)


class _Cache:
	def __init__(self):
		self.counts = {}
		self.expiries = []
		self.raises = False

	def make_key(self, key):
		return f"site|{key}"

	def incrby(self, key, amount):
		if self.raises:
			raise ConnectionError("cache unavailable")
		self.counts[key] = self.counts.get(key, 0) + amount
		return self.counts[key]

	def expire(self, key, seconds):
		self.expiries.append((key, seconds))


class _PermissionError(Exception):
	pass


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe.local = types.SimpleNamespace(request=None, form_dict={}, conf={}, request_ip="203.0.113.7")
	frappe.cache = _Cache()
	frappe.PermissionError = _PermissionError
	STATE["frappe"] = frappe
	sys.modules["frappe"] = frappe


def setUpModule():
	global oauth
	for name in ("frappe", "erpnext_enhancements.oauth_registration"):
		_saved_modules[name] = sys.modules.pop(name, None)
	_install_frappe_stub()
	oauth = importlib.import_module("erpnext_enhancements.oauth_registration")


def tearDownModule():
	for name, module in _saved_modules.items():
		sys.modules.pop(name, None)
		if module is not None:
			sys.modules[name] = module


def _werkzeug_stub():
	"""Just enough of werkzeug for ``refusal_exception``: a Response and two HTTPExceptions."""

	class Response:
		def __init__(self, body, status=200, mimetype=None):
			self.data = body
			self.status_code = status
			self.mimetype = mimetype

	class HTTPException(Exception):
		def __init__(self, description=None, response=None):
			super().__init__(description)
			self.response = response

	class BadRequest(HTTPException):
		code = 400

	class TooManyRequests(HTTPException):
		code = 429

	package = types.ModuleType("werkzeug")
	exceptions = types.ModuleType("werkzeug.exceptions")
	exceptions.HTTPException = HTTPException
	exceptions.BadRequest = BadRequest
	exceptions.TooManyRequests = TooManyRequests
	wrappers = types.ModuleType("werkzeug.wrappers")
	wrappers.Response = Response
	package.exceptions = exceptions
	package.wrappers = wrappers
	return {"werkzeug": package, "werkzeug.exceptions": exceptions, "werkzeug.wrappers": wrappers}


class _Base(unittest.TestCase):
	def setUp(self):
		frappe = STATE["frappe"]
		frappe.local.request = None
		frappe.local.form_dict = {}
		frappe.local.conf = {}
		frappe.local.request_ip = "203.0.113.7"
		frappe.cache = _Cache()
		self.frappe = frappe
		patcher = mock.patch.dict(sys.modules, _werkzeug_stub())
		patcher.start()
		self.addCleanup(patcher.stop)

	def request(self, **kwargs):
		req = _Request(**kwargs)
		self.frappe.local.request = req
		return req

	def refusal(self):
		"""Run the hook; return (status, json body) of the refusal it raised."""
		with self.assertRaises(Exception) as caught:
			oauth.guard_registration()
		response = caught.exception.response
		return response.status_code, json.loads(response.data)


class TestRedirectUriAllowList(unittest.TestCase):
	def test_the_two_production_clients_are_allowed(self):
		self.assertTrue(oauth.redirect_uri_allowed(CLAUDE))
		self.assertTrue(oauth.redirect_uri_allowed("https://claude.com/api/mcp/auth_callback"))
		for uri in GOOGLE:
			self.assertTrue(oauth.redirect_uri_allowed(uri), uri)

	def test_google_hosts_allow_any_path(self):
		self.assertTrue(oauth.redirect_uri_allowed("https://oauth-redirect.googleusercontent.com"))
		self.assertTrue(
			oauth.redirect_uri_allowed("https://oauth-redirect-test.googleusercontent.com/a/b?c=d")
		)

	def test_letter_case_of_scheme_and_host_does_not_matter(self):
		self.assertTrue(oauth.redirect_uri_allowed("https://CLAUDE.AI/api/mcp/auth_callback"))
		self.assertTrue(oauth.redirect_uri_allowed("HTTPS://claude.ai/api/mcp/auth_callback"))

	def test_claude_path_must_be_exact(self):
		for uri in (
			"https://claude.ai/api/mcp/auth_callback/extra",
			"https://claude.ai/api/mcp/auth_callbackx",
			"https://claude.ai/api/mcp/",
			"https://claude.ai/",
			"https://claude.ai",
			"https://claude.ai/API/MCP/AUTH_CALLBACK",
		):
			self.assertFalse(oauth.redirect_uri_allowed(uri), uri)

	def test_refused_forms(self):
		for uri in (
			"http://claude.ai/api/mcp/auth_callback",
			"https://claude.ai./api/mcp/auth_callback",
			"https://claude.ai:443/api/mcp/auth_callback",
			"https://claude.ai:/api/mcp/auth_callback",
			"https://user@claude.ai/api/mcp/auth_callback",
			"https://claude.ai@other.example/api/mcp/auth_callback",
			"https://user:pw@oauth-redirect.googleusercontent.com/r/x",
			"https://claude.ai/api/mcp/auth_callback#frag",
			"https://claude.ai/api/mcp/auth_callback#",
			"https://other.example/api/mcp/auth_callback",
			"https://sub.claude.ai/api/mcp/auth_callback",
			"https://claude.ai.other.example/api/mcp/auth_callback",
			"https://xclaude.ai/api/mcp/auth_callback",
			"https://oauth-redirect.googleusercontent.com.other.example/r/x",
			"https://other-oauth-redirect.googleusercontent.com/r/x",
			"https://googleusercontent.com/r/x",
			"https:claude.ai/api/mcp/auth_callback",
			"https:///claude.ai/api/mcp/auth_callback",
			"//claude.ai/api/mcp/auth_callback",
			"claude.ai/api/mcp/auth_callback",
			"https://[::1]/api/mcp/auth_callback",
			"javascript:alert(1)",
			"com.example.app:/callback",
			"",
			"   ",
		):
			self.assertFalse(oauth.redirect_uri_allowed(uri), uri)

	def test_internationalized_hosts_are_refused(self):
		# A Cyrillic letter (U+0430) in place of the Latin letter, and the same host's punycode form.
		self.assertFalse(
			oauth.redirect_uri_allowed("https://cl" + chr(0x430) + "ude.ai/api/mcp/auth_callback")
		)
		self.assertFalse(oauth.redirect_uri_allowed("https://xn--clude-6ve.ai/api/mcp/auth_callback"))
		# A full-width full stop (U+FF0E) between the labels.
		self.assertFalse(
			oauth.redirect_uri_allowed("https://claude" + chr(0xFF0E) + "ai/api/mcp/auth_callback")
		)

	def test_dot_segments_and_encoded_separators_are_refused(self):
		for uri in (
			"https://claude.ai/api/mcp/auth_callback/../../other",
			"https://claude.ai/api/mcp/../mcp/auth_callback",
			"https://claude.ai/api/./mcp/auth_callback",
			"https://claude.ai/api/mcp/%2e%2e/mcp/auth_callback",
			"https://claude.ai/api/mcp%2Fauth_callback",
			"https://oauth-redirect.googleusercontent.com/r/%2E%2E/x",
			"https://oauth-redirect.googleusercontent.com/r/..",
			"https://oauth-redirect.googleusercontent.com//r/x",
			"https://oauth-redirect.googleusercontent.com/r%5cx",
			"https://claude.ai\\@other.example/api/mcp/auth_callback",
			"https://claude.ai/api/mcp/auth_callback\\..\\x",
		):
			self.assertFalse(oauth.redirect_uri_allowed(uri), uri)

	def test_whitespace_control_and_non_strings_are_refused(self):
		for uri in (
			" https://claude.ai/api/mcp/auth_callback",
			"https://claude.ai/api/mcp/auth_callback ",
			"https://claude.ai/api/mcp/auth\tcallback",
			"https://claude.ai/api/mcp/auth_callback\n",
			None,
			42,
			["https://claude.ai/api/mcp/auth_callback"],
			{"uri": CLAUDE},
		):
			self.assertFalse(oauth.redirect_uri_allowed(uri), repr(uri))

	def test_an_overlong_uri_is_refused(self):
		self.assertFalse(
			oauth.redirect_uri_allowed("https://oauth-redirect.googleusercontent.com/" + "a" * 2100)
		)

	def test_loopback_is_refused_unless_configured(self):
		for uri in ("http://localhost:3000/callback", "http://127.0.0.1/callback", "https://localhost/cb"):
			self.assertFalse(oauth.redirect_uri_allowed(uri), uri)


class TestRegistrationBody(unittest.TestCase):
	def test_every_entry_must_be_allowed(self):
		self.assertIsNone(oauth.refused_redirect_uri({"redirect_uris": [CLAUDE], "client_name": "c"}))
		self.assertIsNone(oauth.refused_redirect_uri({"redirect_uris": GOOGLE, "client_name": "g"}))

	def test_one_bad_entry_refuses_a_mixed_list(self):
		error, description = oauth.refused_redirect_uri(
			{"redirect_uris": [CLAUDE, "https://other.example/cb"], "client_name": "c"}
		)
		self.assertEqual(error, "invalid_redirect_uri")
		self.assertIn("https://other.example/cb", description)
		error, _d = oauth.refused_redirect_uri({"redirect_uris": [*GOOGLE, None]})
		self.assertEqual(error, "invalid_redirect_uri")

	def test_missing_empty_or_wrong_typed_lists_are_refused(self):
		for body in (
			{"client_name": "c"},
			{"redirect_uris": []},
			{"redirect_uris": CLAUDE},
			{"redirect_uris": None},
			{"redirect_uris": {"a": CLAUDE}},
			None,
			[CLAUDE],
			"redirect_uris",
		):
			error, _d = oauth.refused_redirect_uri(body)
			self.assertEqual(error, "invalid_client_metadata", repr(body))

	def test_too_many_entries_are_refused(self):
		error, _d = oauth.refused_redirect_uri({"redirect_uris": [CLAUDE] * (oauth.MAX_REDIRECT_URIS + 1)})
		self.assertEqual(error, "invalid_client_metadata")


class TestConfiguredEntries(unittest.TestCase):
	def test_entry_forms(self):
		self.assertEqual(oauth.parse_allowlist_entry("example.org"), ("https", "example.org", None, False))
		self.assertEqual(
			oauth.parse_allowlist_entry("Example.org/oauth/cb/"), ("https", "example.org", "/oauth/cb", False)
		)
		self.assertEqual(
			oauth.parse_allowlist_entry("https://example.org/cb"), ("https", "example.org", "/cb", False)
		)
		self.assertEqual(oauth.parse_allowlist_entry("http://localhost"), ("http", "localhost", None, False))
		self.assertEqual(
			oauth.parse_allowlist_entry("http://127.0.0.1/cb"), ("http", "127.0.0.1", "/cb", False)
		)

	def test_unusable_entries_are_ignored(self):
		for entry in (
			"http://example.org",
			"*.example.org",
			"example.*",
			"example.org:8443",
			"http://localhost:8080",
			"user@example.org",
			"example.org/cb#x",
			"example.org/cb?x=1",
			"example.org/../cb",
			"example.org/%2e%2e",
			"example.org.",
			".example.org",
			"exa mple.org",
			"ex" + chr(0xE4) + "mple.org",
			"",
			None,
			42,
			["example.org"],
		):
			self.assertIsNone(oauth.parse_allowlist_entry(entry), repr(entry))

	def test_site_config_extends_the_built_in_list(self):
		frappe = STATE["frappe"]
		frappe.local.conf = {oauth.ALLOWLIST_KEY: ["example.org/cb", "http://localhost", "*.bad.example", 7]}
		try:
			rules = oauth.configured_rules()
		finally:
			frappe.local.conf = {}
		self.assertEqual(rules[: len(oauth.BUILTIN_RULES)], oauth.BUILTIN_RULES)
		self.assertEqual(len(rules), len(oauth.BUILTIN_RULES) + 2)
		self.assertTrue(oauth.redirect_uri_allowed("https://example.org/cb", rules))
		self.assertTrue(oauth.redirect_uri_allowed("https://example.org/cb/x", rules))
		self.assertFalse(oauth.redirect_uri_allowed("https://example.org/cbx", rules))
		self.assertFalse(oauth.redirect_uri_allowed("https://example.org/cb/../x", rules))
		self.assertFalse(oauth.redirect_uri_allowed("https://example.org:8443/cb", rules))
		self.assertFalse(oauth.redirect_uri_allowed("http://example.org/cb", rules))
		# A loopback entry accepts any port, and only over http to that exact host.
		self.assertTrue(oauth.redirect_uri_allowed("http://localhost:33418/callback", rules))
		self.assertTrue(oauth.redirect_uri_allowed("http://localhost/callback", rules))
		self.assertFalse(oauth.redirect_uri_allowed("http://127.0.0.1:33418/callback", rules))
		self.assertFalse(oauth.redirect_uri_allowed("http://localhost.example.org/callback", rules))

	def test_a_single_string_and_junk_config_are_tolerated(self):
		frappe = STATE["frappe"]
		try:
			frappe.local.conf = {oauth.ALLOWLIST_KEY: "example.org"}
			self.assertEqual(len(oauth.configured_rules()), len(oauth.BUILTIN_RULES) + 1)
			frappe.local.conf = {oauth.ALLOWLIST_KEY: {"example.org": 1}}
			self.assertEqual(oauth.configured_rules(), oauth.BUILTIN_RULES)
		finally:
			frappe.local.conf = {}


class TestRequestMatcher(unittest.TestCase):
	def test_registration_paths(self):
		for path in (
			FRAPPE_PATH,
			FAC_PATH,
			FRAPPE_PATH + "/",
			"/api/v1/method/frappe.integrations.oauth2.register_client",
			"/api/v2/method/frappe_assistant_core.api.oauth_registration.register_client",
			"/api/v2/method/frappe.integrations.oauth2.register_client/",
			"/api/method/frappe.integrations.oauth2.frappe.integrations.oauth2.register_client",
			"/api/method/register_client",
		):
			self.assertTrue(oauth.is_registration_path(path), path)

	def test_other_paths(self):
		for path in (
			"/api/method/frappe.integrations.oauth2.register_clients",
			"/api/method/frappe.integrations.oauth2.get_client",
			"/api/method/frappe.integrations.oauth2.authorize",
			"/api/resource/register_client",
			"/api/method/",
			"/api/method",
			"/register_client",
			"/app/register_client",
			"/method/x.register_client",
			"",
			None,
		):
			self.assertFalse(oauth.is_registration_path(path), repr(path))

	def test_cmd_names(self):
		self.assertTrue(oauth.is_registration_method("frappe.integrations.oauth2.register_client"))
		self.assertFalse(oauth.is_registration_method("frappe.client.get_list"))
		self.assertFalse(oauth.is_registration_method(""))
		self.assertFalse(oauth.is_registration_method(None))


class TestRateLimit(_Base):
	def test_ten_an_hour_per_address(self):
		for _i in range(oauth.RATE_LIMIT):
			self.assertFalse(oauth.over_rate_limit("203.0.113.7"))
		self.assertTrue(oauth.over_rate_limit("203.0.113.7"))
		self.assertFalse(oauth.over_rate_limit("198.51.100.4"))
		key = "site|ee_oauth_registration:203.0.113.7"
		self.assertEqual(self.frappe.cache.expiries.count((key, oauth.RATE_WINDOW_SECONDS)), 1)

	def test_a_missing_address_shares_one_bucket(self):
		oauth.over_rate_limit(None)
		oauth.over_rate_limit("")
		self.assertEqual(self.frappe.cache.counts, {"site|ee_oauth_registration:unknown": 2})


class TestHook(_Base):
	def test_it_is_registered_in_before_request(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		hooks = {
			target.id: node.value
			for node in tree.body
			if isinstance(node, ast.Assign)
			for target in node.targets
			if isinstance(target, ast.Name)
		}
		self.assertIn(HOOK_PATH, ast.literal_eval(hooks["before_request"]))

	def test_other_requests_pass_without_touching_body_or_cache(self):
		for kwargs in (
			{"method": "GET"},
			{"method": "PUT"},
			{"method": "POST", "path": "/api/method/frappe.client.get_list"},
			{"method": "POST", "path": "/api/resource/ToDo"},
			{"method": "POST", "path": "/login"},
		):
			req = self.request(body_raises=True, **kwargs)
			self.assertIsNone(oauth.guard_registration())
			self.assertEqual(req.body_reads, 0)
		self.assertEqual(self.frappe.cache.counts, {})

	def test_never_raises_outside_a_matching_request(self):
		self.frappe.local.request = None
		self.assertIsNone(oauth.guard_registration())
		self.frappe.local.request = _ExplodingRequest()
		self.assertIsNone(oauth.guard_registration())
		self.request(path="/api/method/frappe.client.get_list")
		self.frappe.local.form_dict = None
		self.assertIsNone(oauth.guard_registration())
		self.frappe.local.form_dict = {"cmd": 7}
		self.assertIsNone(oauth.guard_registration())
		self.assertEqual(self.frappe.cache.counts, {})

	def test_allowed_registrations_pass(self):
		self.request(body={"redirect_uris": [CLAUDE], "client_name": "Claude"})
		self.assertIsNone(oauth.guard_registration())
		self.request(path=FAC_PATH, body={"redirect_uris": GOOGLE, "client_name": "Google"})
		self.assertIsNone(oauth.guard_registration())
		self.assertEqual(sum(self.frappe.cache.counts.values()), 2)

	def test_a_refused_uri_gets_a_400_in_rfc_7591_shape(self):
		self.request(body={"redirect_uris": ["https://other.example/cb"], "client_name": "x"})
		status, body = self.refusal()
		self.assertEqual(status, 400)
		self.assertEqual(body["error"], "invalid_redirect_uri")
		self.assertIn("error_description", body)

	def test_both_endpoints_and_every_route_are_judged(self):
		bad = {"redirect_uris": ["https://other.example/cb"]}
		for path in (
			FRAPPE_PATH,
			FAC_PATH,
			"/api/v2/method/frappe.integrations.oauth2.register_client",
			"/api/method/frappe.integrations.oauth2.frappe.integrations.oauth2.register_client",
		):
			self.request(path=path, body=bad)
			self.assertEqual(self.refusal()[0], 400, path)

	def test_the_cmd_route_is_judged(self):
		self.request(path="/", body={"redirect_uris": ["https://other.example/cb"]})
		self.frappe.local.form_dict = {"cmd": "frappe.integrations.oauth2.register_client"}
		self.assertEqual(self.refusal()[0], 400)

	def test_an_unreadable_body_fails_closed(self):
		self.request(body=None)
		status, body = self.refusal()
		self.assertEqual((status, body["error"]), (400, "invalid_client_metadata"))
		self.request(body_raises=True)
		status, body = self.refusal()
		self.assertEqual((status, body["error"]), (400, "invalid_client_metadata"))

	def test_a_cache_failure_fails_closed(self):
		self.frappe.cache.raises = True
		self.request(body={"redirect_uris": [CLAUDE]})
		self.assertEqual(self.refusal()[0], 400)

	def test_the_eleventh_request_in_an_hour_gets_a_429(self):
		for _i in range(oauth.RATE_LIMIT):
			self.request(body={"redirect_uris": [CLAUDE]})
			self.assertIsNone(oauth.guard_registration())
		self.request(body={"redirect_uris": [CLAUDE]})
		status, body = self.refusal()
		self.assertEqual(status, 429)
		self.assertEqual(body["error"], "too_many_requests")
		self.frappe.local.request_ip = "198.51.100.4"
		self.request(body={"redirect_uris": [CLAUDE]})
		self.assertIsNone(oauth.guard_registration())

	def test_refused_requests_count_toward_the_rate(self):
		for _i in range(oauth.RATE_LIMIT):
			self.request(body={"redirect_uris": ["https://other.example/cb"]})
			self.assertEqual(self.refusal()[0], 400)
		self.request(body={"redirect_uris": [CLAUDE]})
		self.assertEqual(self.refusal()[0], 429)

	def test_site_config_zero_turns_it_off(self):
		for value in (0, "0", False):
			self.frappe.local.conf = {oauth.GUARD_KEY: value}
			self.request(body={"redirect_uris": ["https://other.example/cb"]})
			self.assertIsNone(oauth.guard_registration(), repr(value))
		for value in (1, "1", True, None):
			self.frappe.local.conf = {oauth.GUARD_KEY: value}
			self.request(body={"redirect_uris": ["https://other.example/cb"]})
			self.assertEqual(self.refusal()[0], 400, repr(value))

	def test_configured_entries_are_honored(self):
		self.frappe.local.conf = {oauth.ALLOWLIST_KEY: ["example.org/cb"]}
		self.request(body={"redirect_uris": ["https://example.org/cb"]})
		self.assertIsNone(oauth.guard_registration())

	def test_without_werkzeug_it_raises_permission_error(self):
		with mock.patch.dict(sys.modules, {"werkzeug.exceptions": None}):
			self.request(body={"redirect_uris": ["https://other.example/cb"]})
			with self.assertRaises(_PermissionError):
				oauth.guard_registration()


class TestRealWerkzeug(unittest.TestCase):
	def test_the_exception_carries_the_response_frappe_sends(self):
		try:
			importlib.import_module("werkzeug.exceptions")
		except ImportError:
			self.skipTest("werkzeug is not installed")
		exc = oauth.refusal_exception(400, "invalid_redirect_uri", "Not on the list.")
		response = exc.get_response({})
		self.assertEqual(response.status_code, 400)
		self.assertEqual(response.mimetype, "application/json")
		self.assertEqual(
			json.loads(response.get_data(as_text=True)),
			{"error": "invalid_redirect_uri", "error_description": "Not on the list."},
		)
		self.assertEqual(
			oauth.refusal_exception(429, "too_many_requests", "x").get_response({}).status_code, 429
		)


if __name__ == "__main__":
	unittest.main()
