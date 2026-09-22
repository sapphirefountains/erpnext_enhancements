"""Bench-free behavior tests for two guest-endpoint authenticators that could
not work on Frappe v16 as written.

**MDM webhook** (``mdm_integration/webhooks.py``). Run against a stubbed frappe,
because every defect worth guarding here was invisible from inside the app:

* The secret travels in ``X-MDM-Webhook-Secret``. The original contract put it in
  ``Authorization: Bearer``, which ``frappe.auth.validate_auth`` rejects with a 401
  before the endpoint runs (verified on prod 2026-09-22). The endpoint must never
  read ``Authorization``, and a request carrying only that must not be accepted.
* ``provider`` comes from the query string, and only from there. On a JSON POST,
  Frappe v16 builds ``form_dict`` from the body *instead of* the query string, so
  the old ``handle_webhook(provider)`` died with ``TypeError`` (HTTP 500) on
  exactly the requests a webhook sender makes. Also verified on prod.
* An unset secret is read without ``get_password``'s raising default, whose
  ``frappe.throw`` queued "Password not found ..." into the guest's response.
* A secret too short to be one is treated as unset; a non-ASCII header is a
  clean 401 rather than ``compare_digest``'s ``TypeError``.
* A push never overwrites ``status_message`` (the reason the last sync failed),
  the resync it queues is deduplicated, and that resync skips a disabled or
  auth-paused provider -- the gates the hourly job applies and
  ``run_device_sync`` does not.

**Telephony decorator** (``api/telephony.validate_webhook_secret``). Its
``Bearer <admin_webhook_secret>`` branch was dead twice over: Frappe 401s the
header first, and it compared against the Password field's masked ``****``
placeholder rather than the secret. It now accepts only a caller Frappe has
already authenticated with an API key (``token key:secret``, what Triton sends).

The stubs are installed in ``setUpModule`` and removed in ``tearDownModule``, so
they never leak into a suite sharing the process.

Run: python -m unittest erpnext_enhancements.tests.test_webhook_auth -v
"""

import ast
import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
WEBHOOKS = APP / "mdm_integration" / "webhooks.py"
MDM_UTILS = APP / "mdm_integration" / "utils.py"
TELEPHONY = APP / "api" / "telephony.py"

SECRET = "s" * 43
STUBBED = (
	"frappe",
	"frappe.utils",
	"frappe.rate_limiter",
	"requests",
	"twilio",
	"twilio.jwt",
	"twilio.jwt.access_token",
	"twilio.jwt.access_token.grants",
	"twilio.request_validator",
	"erpnext_enhancements.email_style",
	"erpnext_enhancements.mdm_integration.client",
	"erpnext_enhancements.mdm_integration.sync",
)
OURS = (
	"erpnext_enhancements.mdm_integration.webhooks",
	"erpnext_enhancements.mdm_integration.utils",
	"erpnext_enhancements.utils.error_throttle",
	"erpnext_enhancements.api.telephony",
)

#: Mutable per-test state the stub reads at call time.
STATE = {}
#: What each ``@rate_limit`` was declared with, recorded at import.
STATE_RATE_LIMITS = []
webhooks = None
mdm_utils = None
telephony = None
_saved = {}


class AttrDict(dict):
	"""``frappe._dict``: the response object is written by attribute."""

	__getattr__ = dict.get

	def __setattr__(self, key, value):
		self[key] = value


class FakePermissionError(Exception):
	pass


class FakeMDMProviderError(Exception):
	pass


class FakeSettings(dict):
	"""MDM Settings. ``get_password`` behaves like frappe's: with the default
	``raise_exception=True`` and nothing stored, it msgprints and raises."""

	def get(self, key, default=None):
		return super().get(key, default)

	def get_password(self, fieldname, raise_exception=True):
		STATE["password_reads"].append((fieldname, raise_exception))
		value = STATE["secret"]
		if value is None and raise_exception:
			STATE["msgprints"].append(f"Password not found for MDM Settings MDM Settings {fieldname}")
			raise Exception("Password not found")
		return value

	def db_set(self, fieldname, value, update_modified=True):
		STATE["settings_writes"].append((fieldname, value))


class FakeRawPayload(types.SimpleNamespace):
	def insert(self, ignore_permissions=False):
		STATE["archived"].append(self)
		return self


class Request:
	def __init__(self):
		self.args = {}
		self.headers = {}

	def get_data(self, as_text=False):
		return STATE["body"]


def _throw(message, exc=Exception):
	raise exc(message)


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda **_kw: (lambda fn: fn)
	frappe._ = lambda text: text
	frappe.PermissionError = FakePermissionError
	frappe.throw = _throw
	frappe.local = types.SimpleNamespace(
		response=AttrDict(), conf=types.SimpleNamespace(get=lambda key: "test")
	)
	frappe.session = types.SimpleNamespace(user="Guest")
	frappe.request = Request()
	frappe.get_request_header = lambda name, default=None: STATE["headers"].get(name, default)
	frappe.get_single = lambda doctype: STATE["settings"]
	frappe.get_doc = lambda doctype: STATE["triton_settings"]
	frappe.new_doc = lambda doctype: FakeRawPayload(doctype=doctype)
	frappe.parse_json = json.loads
	frappe.set_user = lambda user: STATE["set_user"].append(user)
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *args, **kwargs: STATE["errors"].append((args, kwargs))
	frappe.enqueue = lambda method, **kwargs: STATE["enqueued"].append((method, kwargs))
	frappe.db = types.SimpleNamespace(
		exists=lambda doctype, name=None: doctype == "User",
		commit=lambda: STATE.__setitem__("commits", STATE["commits"] + 1),
	)

	def cache():
		raise RuntimeError("no redis in a unit test")  # error_throttle falls back to log_error

	frappe.cache = cache

	utils = types.ModuleType("frappe.utils")
	utils.now_datetime = lambda: "2026-09-22 12:00:00"
	frappe.utils = utils

	rate_limiter = types.ModuleType("frappe.rate_limiter")

	def rate_limit(**kwargs):
		STATE_RATE_LIMITS.append(kwargs)
		return lambda fn: fn

	rate_limiter.rate_limit = rate_limit
	frappe.rate_limiter = rate_limiter

	# api/telephony.py's third-party imports; nothing under test calls them.
	requests = types.ModuleType("requests")
	twilio = types.ModuleType("twilio")
	twilio_jwt = types.ModuleType("twilio.jwt")
	access_token = types.ModuleType("twilio.jwt.access_token")
	access_token.AccessToken = object
	grants = types.ModuleType("twilio.jwt.access_token.grants")
	grants.VoiceGrant = object
	request_validator = types.ModuleType("twilio.request_validator")
	request_validator.RequestValidator = object
	email_style = types.ModuleType("erpnext_enhancements.email_style")

	# What resync_from_webhook imports lazily; the real ones pull in the HTTP clients.
	client = types.ModuleType("erpnext_enhancements.mdm_integration.client")
	client.MDMProviderError = FakeMDMProviderError
	sync = types.ModuleType("erpnext_enhancements.mdm_integration.sync")
	sync.auth_blocked = lambda settings, key: bool(settings.get(f"{key.lower()}_auth_blocked"))

	def run_device_sync(provider_key):
		STATE["synced"].append(provider_key)
		if STATE.get("sync_raises"):
			raise STATE["sync_raises"]

	sync.run_device_sync = run_device_sync

	for name, module in (
		("frappe", frappe),
		("frappe.utils", utils),
		("frappe.rate_limiter", rate_limiter),
		("requests", requests),
		("twilio", twilio),
		("twilio.jwt", twilio_jwt),
		("twilio.jwt.access_token", access_token),
		("twilio.jwt.access_token.grants", grants),
		("twilio.request_validator", request_validator),
		("erpnext_enhancements.email_style", email_style),
		("erpnext_enhancements.mdm_integration.client", client),
		("erpnext_enhancements.mdm_integration.sync", sync),
	):
		sys.modules[name] = module


def setUpModule():
	global webhooks, mdm_utils, telephony
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stubs()
	from erpnext_enhancements.api import telephony as telephony_module
	from erpnext_enhancements.mdm_integration import utils as utils_module
	from erpnext_enhancements.mdm_integration import webhooks as webhooks_module

	webhooks = webhooks_module
	mdm_utils = utils_module
	telephony = telephony_module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _reset(*, provider="Miradore", headers=None, secret=SECRET, body="{}", settings=None):
	STATE.clear()
	STATE.update(
		{
			"settings": FakeSettings(
				miradore_enabled=1, action1_enabled=1, miradore_auth_blocked=0, action1_auth_blocked=0
			)
			if settings is None
			else settings,
			"secret": secret,
			"headers": {"X-MDM-Webhook-Secret": SECRET} if headers is None else headers,
			"body": body,
			"archived": [],
			"enqueued": [],
			"errors": [],
			"msgprints": [],
			"password_reads": [],
			"settings_writes": [],
			"set_user": [],
			"synced": [],
			"commits": 0,
		}
	)
	frappe = sys.modules["frappe"]
	frappe.local.response = AttrDict()
	frappe.request.args = {} if provider is None else {"provider": provider}


def _call(*, body_kwargs=None, **kwargs):
	"""Invoke the endpoint the way Frappe v16 does on a JSON POST: the body's keys
	are the kwargs, and the query string is only on the request."""
	_reset(**kwargs)
	return webhooks.handle_webhook(**(body_kwargs or {}))


def _status():
	return sys.modules["frappe"].local.response.get("http_status_code")


def _literals_outside_docstrings(path):
	tree = ast.parse(path.read_text(encoding="utf-8"))
	docstrings = set()
	for node in ast.walk(tree):
		if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
			first = node.body[0]
			if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
				docstrings.add(id(first.value))
	return [
		n.value
		for n in ast.walk(tree)
		if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
	]


class MDMWebhookAuthTests(unittest.TestCase):
	def test_the_secret_header_is_accepted(self):
		result = _call(body='{"event": "enrolled"}', body_kwargs={"event": "enrolled"})
		self.assertEqual(result, {"status": "ok"})
		self.assertIsNone(_status())
		(doc,) = STATE["archived"]
		self.assertEqual((doc.provider, doc.source), ("Miradore", "Webhook"))
		self.assertEqual(json.loads(doc.payload), {"event": "enrolled"})

	def test_missing_header_is_401(self):
		self.assertEqual(_call(headers={})["message"], "invalid webhook secret")
		self.assertEqual(_status(), 401)
		self.assertEqual(STATE["archived"], [])
		self.assertEqual(STATE["enqueued"], [])

	def test_wrong_secret_is_401(self):
		_call(headers={"X-MDM-Webhook-Secret": "t" * 43})
		self.assertEqual(_status(), 401)

	def test_authorization_bearer_is_never_accepted(self):
		# The original contract. Frappe 401s it before we run; if it ever reached us,
		# it must not authorize -- the secret lives in exactly one header.
		_call(headers={"Authorization": "Bearer " + SECRET})
		self.assertEqual(_status(), 401)
		self.assertEqual(STATE["archived"], [])

	def test_non_ascii_header_is_a_clean_401(self):
		# compare_digest raises TypeError on non-ASCII str; that would be a 500.
		_call(headers={"X-MDM-Webhook-Secret": "é" * 43})
		self.assertEqual(_status(), 401)

	def test_unset_secret_authorizes_nobody_and_says_nothing(self):
		_call(secret=None)
		self.assertEqual(_status(), 401)
		self.assertEqual(STATE["msgprints"], [], "an unset secret must not msgprint to a guest")
		self.assertEqual(STATE["password_reads"], [("webhook_secret", False)])

	def test_short_secret_is_treated_as_unset_and_logged(self):
		short = "x" * (mdm_utils.MIN_WEBHOOK_SECRET_LENGTH - 1)
		_call(secret=short, headers={"X-MDM-Webhook-Secret": short})
		self.assertEqual(_status(), 401)
		self.assertTrue(STATE["errors"], "a too-short secret must say so in the Error Log")
		self.assertNotIn(short, repr(STATE["errors"]), "the secret must never reach the Error Log")

	def test_source_never_reads_authorization(self):
		# Belt and braces for the behavioral test above: no string literal outside a
		# docstring names the header, so nobody can quietly add it back as a fallback.
		for path in (WEBHOOKS, MDM_UTILS):
			literals = _literals_outside_docstrings(path)
			self.assertFalse(
				[s for s in literals if s.strip().lower().startswith("authorization")], path.name
			)
		self.assertIn("X-MDM-Webhook-Secret", _literals_outside_docstrings(WEBHOOKS))
		self.assertEqual(webhooks.SECRET_HEADER, "X-MDM-Webhook-Secret")


class MDMWebhookProviderTests(unittest.TestCase):
	def test_json_post_without_a_provider_argument_works(self):
		# The v16 shape: form_dict came from the JSON body, so no `provider` kwarg at
		# all. The old signature raised TypeError here, before any of our code ran.
		result = _call(provider="Action1", body='{"a": 1}', body_kwargs={"a": 1})
		self.assertEqual(result, {"status": "ok"})
		self.assertEqual(STATE["archived"][0].provider, "Action1")

	def test_a_provider_key_in_the_body_is_ignored(self):
		_call(provider="Action1", body='{"provider": "Miradore"}', body_kwargs={"provider": "Miradore"})
		self.assertEqual(STATE["archived"][0].provider, "Action1")
		# ...and cannot stand in for a missing query parameter either.
		_call(provider=None, body='{"provider": "Miradore"}', body_kwargs={"provider": "Miradore"})
		self.assertEqual(_status(), 404)
		self.assertEqual(STATE["archived"], [])

	def test_unknown_provider_is_404(self):
		self.assertEqual(_call(provider="Mock")["message"], "unknown provider")
		self.assertEqual(_status(), 404)

	def test_post_only_and_rate_limited(self):
		# A leaked secret can write Raw Payload rows; the limit is what bounds that.
		self.assertIn({"limit": 120, "seconds": 3600, "methods": ["POST"]}, STATE_RATE_LIMITS)

	def test_unparseable_body_is_archived_raw(self):
		_call(body="not json")
		self.assertEqual(json.loads(STATE["archived"][0].payload), {"_raw": "not json"})

	def test_a_push_never_overwrites_the_failure_reason(self):
		_call()
		self.assertEqual(STATE["settings_writes"], [])

	def test_the_resync_is_deduplicated_per_provider(self):
		_call(provider="Action1")
		((method, kwargs),) = STATE["enqueued"]
		self.assertEqual(method, "erpnext_enhancements.mdm_integration.webhooks.resync_from_webhook")
		self.assertEqual(kwargs["provider_key"], "Action1")
		self.assertTrue(kwargs["deduplicate"])
		self.assertEqual(kwargs["job_id"], "mdm-webhook-resync-Action1")


class MDMWebhookResyncTests(unittest.TestCase):
	def _resync(self, provider, **settings):
		base = {
			"miradore_enabled": 1,
			"action1_enabled": 1,
			"miradore_auth_blocked": 0,
			"action1_auth_blocked": 0,
		}
		base.update(settings)
		_reset(settings=FakeSettings(**base))
		webhooks.resync_from_webhook(provider)

	def test_an_enabled_provider_is_synced(self):
		self._resync("Miradore")
		self.assertEqual(STATE["synced"], ["Miradore"])

	def test_a_disabled_provider_is_not(self):
		self._resync("Miradore", miradore_enabled=0)
		self.assertEqual(STATE["synced"], [])

	def test_an_auth_paused_provider_is_not(self):
		# Prod on 2026-09-22: both providers paused on a standing 401. A push must not
		# be a way around the pause.
		self._resync("Action1", action1_auth_blocked=1)
		self.assertEqual(STATE["synced"], [])

	def test_a_provider_error_is_already_recorded(self):
		_reset()
		STATE["sync_raises"] = FakeMDMProviderError("401")
		webhooks.resync_from_webhook("Miradore")
		self.assertEqual(STATE["errors"], [])


class TelephonyDecoratorTests(unittest.TestCase):
	def _call(self, authorization=None, user="Guest", admin_secret_attr="*" * 43):
		_reset()
		frappe = sys.modules["frappe"]
		frappe.request.headers = {} if authorization is None else {"Authorization": authorization}
		frappe.session.user = user
		STATE["triton_settings"] = types.SimpleNamespace(admin_webhook_secret=admin_secret_attr)
		calls = []

		@telephony.validate_webhook_secret
		def endpoint():
			calls.append(1)
			return "ran"

		return endpoint, calls

	def test_an_api_key_frappe_authenticated_is_accepted(self):
		endpoint, calls = self._call("token key:secret", user="triton@sapphirefountains.com")
		self.assertEqual(endpoint(), "ran")
		self.assertEqual(calls, [1])

	def test_scheme_is_case_insensitive_like_frappe(self):
		endpoint, _ = self._call("Token key:secret", user="triton@sapphirefountains.com")
		self.assertEqual(endpoint(), "ran")

	def test_a_token_header_that_left_us_guest_is_refused(self):
		endpoint, calls = self._call("token key:secret", user="Guest")
		with self.assertRaises(FakePermissionError):
			endpoint()
		self.assertEqual(calls, [])

	def test_bearer_is_refused_even_matching_the_masked_placeholder(self):
		# The removed branch compared against getattr(settings, "admin_webhook_secret"),
		# which is the Password field's "****" placeholder. A run of asterisks would
		# have passed it, had Frappe ever let a Bearer through.
		endpoint, calls = self._call("Bearer " + "*" * 43, user="Guest")
		with self.assertRaises(FakePermissionError):
			endpoint()
		endpoint, calls = self._call("Bearer " + "*" * 43, user="someone@example.com")
		with self.assertRaises(FakePermissionError):
			endpoint()
		self.assertEqual(calls, [])

	def test_no_header_is_refused(self):
		endpoint, calls = self._call(None, user="Guest")
		with self.assertRaises(FakePermissionError):
			endpoint()

	def test_the_decorator_names_no_bearer_and_no_secret(self):
		tree = ast.parse(TELEPHONY.read_text(encoding="utf-8"))
		(fn,) = [
			n
			for n in ast.walk(tree)
			if isinstance(n, ast.FunctionDef) and n.name == "validate_webhook_secret"
		]
		body = fn.body[1:]  # skip the docstring, which explains what was removed
		literals = [
			n.value
			for stmt in body
			for n in ast.walk(stmt)
			if isinstance(n, ast.Constant) and isinstance(n.value, str)
		]
		names = {n.attr for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Attribute)}
		names |= {n.id for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Name)}
		self.assertFalse([s for s in literals if "bearer" in s.lower()])
		self.assertNotIn("admin_webhook_secret", literals)
		self.assertNotIn("admin_webhook_secret", names)


if __name__ == "__main__":
	unittest.main()
