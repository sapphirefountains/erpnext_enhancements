"""Bench-free tests for the configuration checks on three inbound paths (v1.826.5).

**Twilio voicemail download** (``api/call_recording_export``). The download sends
the Twilio account credentials, so ``is_twilio_recording_url`` accepts only
``https://api.twilio.com/2010-04-01/Accounts/<configured SID>/Recordings/<RE sid>``
(with an optional extension or query), ``_voicemail_audio`` makes no request for
anything else, and ``api/call_intelligence.process_call_intelligence`` checks the
same rule before it queues the export.

**Stored secrets** (``stripe_payments/core/utils.get_secret`` and
``quickbooks_online/core/utils.get_secret``). A value that is missing, cannot be
decrypted, is empty, or is only the ``*`` placeholder reads as None, which every
caller treats as "not configured".

**QuickBooks Time webhook** (``quickbooks_time/api.qb_timesheet_webhook``). It
refuses every request while ``qb_time_webhook_secret`` is unset, accepts the secret
from the ``X-QB-Time-Webhook-Secret`` header or the ``token`` parameter, answers a
refusal with a fixed body and no Error Log row, and never echoes exception text.

The stubs are installed in ``setUpModule`` and removed in ``tearDownModule``, so
they never leak into a suite sharing the process.

Run: python -m unittest erpnext_enhancements.tests.test_inbound_secret_checks -v
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
CALL_INTELLIGENCE = APP / "api" / "call_intelligence.py"

SID = "AC" + "0123456789abcdef" * 2
RE_SID = "RE" + "fedcba9876543210" * 2
GOOD_URL = f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Recordings/{RE_SID}"

STUBBED = (
	"frappe",
	"frappe.utils",
	"requests",
	"erpnext_enhancements.google_drive.drive_utils",
)
OURS = (
	"erpnext_enhancements.api.call_recording_export",
	"erpnext_enhancements.stripe_payments.core.utils",
	"erpnext_enhancements.quickbooks_online.core.utils",
	"erpnext_enhancements.quickbooks_time.api",
)

STATE = {}
_saved = {}
recording_export = None
stripe_utils = None
qbo_utils = None
qb_time = None


class AttrDict(dict):
	__getattr__ = dict.get

	def __setattr__(self, key, value):
		self[key] = value


def _install_stubs():
	frappe = types.ModuleType("frappe")
	frappe.whitelist = lambda **_kw: (lambda fn: fn)
	frappe._ = lambda text: text
	frappe.conf = types.SimpleNamespace(get=lambda key, default=None: STATE["conf"].get(key, default))
	frappe.local = types.SimpleNamespace(response=AttrDict())
	frappe.form_dict = AttrDict()
	frappe.request = types.SimpleNamespace(get_data=lambda: STATE["body"])
	frappe.get_request_header = lambda name, default=None: STATE["headers"].get(name, default)
	frappe.get_doc = lambda doctype: STATE["triton_settings"]
	frappe.get_traceback = lambda: "Traceback (most recent call last):\nValueError: boom"
	frappe.log_error = lambda *args, **kwargs: STATE["errors"].append((args, kwargs))
	frappe.log_message = lambda *args, **kwargs: None

	def throw(message, exc=Exception):
		raise exc(message)

	frappe.throw = throw

	class FakeTimeLog(types.SimpleNamespace):
		name = "TL-0001"

		def insert(self, ignore_permissions=False):
			STATE["inserted"].append(self)
			if STATE.get("insert_raises"):
				raise STATE["insert_raises"]
			return self

	frappe.new_doc = lambda doctype: FakeTimeLog(doctype=doctype)
	frappe.db = types.SimpleNamespace(
		get_value=lambda doctype, filters, field: f"{doctype}-1",
		commit=lambda: None,
	)

	utils = types.ModuleType("frappe.utils")
	utils.get_datetime = lambda value=None: value
	utils.get_system_timezone = lambda: "America/Denver"
	utils.now_datetime = lambda: "2026-11-18 12:00:00"
	utils.cint = lambda value=0: int(value or 0)
	utils.flt = lambda value=0, precision=None: float(value or 0)
	frappe.utils = utils

	requests = types.ModuleType("requests")

	def get(url, **kwargs):
		STATE["fetched"].append((url, kwargs))
		return types.SimpleNamespace(content=b"audio", raise_for_status=lambda: None)

	requests.get = get

	drive_utils = types.ModuleType("erpnext_enhancements.google_drive.drive_utils")
	drive_utils.create_folder = drive_utils.find_folder = drive_utils.get_drive_service = None

	for name, module in (
		("frappe", frappe),
		("frappe.utils", utils),
		("requests", requests),
		("erpnext_enhancements.google_drive.drive_utils", drive_utils),
	):
		sys.modules[name] = module


def setUpModule():
	global recording_export, stripe_utils, qbo_utils, qb_time
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stubs()
	_reset()
	from erpnext_enhancements.api import call_recording_export as recording_export_module
	from erpnext_enhancements.quickbooks_online.core import utils as qbo_utils_module
	from erpnext_enhancements.quickbooks_time import api as qb_time_module
	from erpnext_enhancements.stripe_payments.core import utils as stripe_utils_module

	recording_export = recording_export_module
	stripe_utils = stripe_utils_module
	qbo_utils = qbo_utils_module
	qb_time = qb_time_module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _triton_settings(account_sid=SID, token="auth-token"):
	return types.SimpleNamespace(
		twilio_account_sid=account_sid,
		get_password=lambda fieldname, raise_exception=True: token,
	)


def _reset(**overrides):
	STATE.clear()
	STATE.update(
		{
			"conf": {},
			"headers": {},
			"body": b"",
			"errors": [],
			"fetched": [],
			"inserted": [],
			"triton_settings": _triton_settings(),
		}
	)
	STATE.update(overrides)
	frappe = sys.modules["frappe"]
	frappe.local.response = AttrDict()
	frappe.form_dict = AttrDict()


class TwilioRecordingUrlTests(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_recording_urls_of_the_configured_account_are_accepted(self):
		for url in (
			GOOD_URL,
			GOOD_URL + ".wav",
			GOOD_URL + ".mp3",
			GOOD_URL + "?RequestedChannels=2",
			GOOD_URL + ".wav?Download=true",
			GOOD_URL.replace("api.twilio.com", "API.twilio.com"),
		):
			self.assertTrue(recording_export.is_twilio_recording_url(url, SID), url)

	def test_the_configured_sid_is_read_when_none_is_passed(self):
		self.assertTrue(recording_export.is_twilio_recording_url(GOOD_URL))
		# Site config is the fallback when Triton Settings has none.
		_reset(triton_settings=_triton_settings(account_sid=""), conf={"twilio_account_sid": SID})
		self.assertTrue(recording_export.is_twilio_recording_url(GOOD_URL))

	def test_other_urls_are_refused(self):
		other_sid = "AC" + "1" * 32
		for url in (
			GOOD_URL.replace(SID, other_sid),
			GOOD_URL.replace("https://", "http://"),
			GOOD_URL.replace("api.twilio.com", "api.twilio.com.example.com"),
			GOOD_URL.replace("api.twilio.com", "media.twilio.com"),
			GOOD_URL.replace("api.twilio.com", "api.twilio.com:8443"),
			GOOD_URL.replace("https://", "https://user:pass@"),
			GOOD_URL.replace("/Recordings/", "/Messages/"),
			GOOD_URL.replace("/2010-04-01/", "/2008-08-01/"),
			GOOD_URL + "/../../Calls",
			GOOD_URL + "/extra",
			GOOD_URL + "#frag",
			GOOD_URL[:-1],
			"https://example.com/" + GOOD_URL,
			"",
			None,
			123,
		):
			self.assertFalse(recording_export.is_twilio_recording_url(url, SID), url)

	def test_no_configured_sid_refuses_everything(self):
		_reset(triton_settings=_triton_settings(account_sid=""))
		self.assertFalse(recording_export.is_twilio_recording_url(GOOD_URL))
		self.assertFalse(recording_export.is_twilio_recording_url(GOOD_URL, ""))

	def test_voicemail_audio_fetches_a_good_url_with_the_account_credentials(self):
		self.assertEqual(recording_export._voicemail_audio(GOOD_URL), b"audio")
		((url, kwargs),) = STATE["fetched"]
		self.assertEqual(url, GOOD_URL + ".wav")
		self.assertEqual(kwargs["auth"], (SID, "auth-token"))
		self.assertTrue(kwargs["timeout"])

	def test_voicemail_audio_makes_no_request_for_any_other_url(self):
		for url in ("https://example.com/vm.wav", GOOD_URL.replace(SID, "AC" + "2" * 32)):
			_reset()
			self.assertIsNone(recording_export._voicemail_audio(url))
			self.assertEqual(STATE["fetched"], [])
			self.assertTrue(STATE["errors"])
			self.assertNotIn("auth-token", repr(STATE["errors"]))

	def test_call_intelligence_checks_the_url_before_queueing(self):
		tree = ast.parse(CALL_INTELLIGENCE.read_text(encoding="utf-8"))
		(fn,) = [
			n for n in ast.walk(tree)
			if isinstance(n, ast.FunctionDef) and n.name == "process_call_intelligence"
		]
		enqueues = [
			n for n in ast.walk(fn)
			if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "enqueue_recording_export"
		]
		self.assertEqual(len(enqueues), 1)
		guarded = False
		for node in ast.walk(fn):
			if isinstance(node, ast.If) and "is_twilio_recording_url" in ast.unparse(node.test):
				branch = node.orelse if isinstance(node.test, ast.UnaryOp) else node.body
				if any(e is n for stmt in branch for n in ast.walk(stmt) for e in enqueues):
					guarded = True
		self.assertTrue(guarded, "enqueue_recording_export must run only on an accepted URL")

	def test_call_intelligence_resolves_the_caller_in_process(self):
		"""The endpoint looks the caller up through ``_get_caller_info``, never the
		whitelisted ``get_caller_info`` wrapper: the wrapper checks the request again,
		after this endpoint has already passed that check and switched user."""
		tree = ast.parse(CALL_INTELLIGENCE.read_text(encoding="utf-8"))
		(fn,) = [
			n for n in ast.walk(tree)
			if isinstance(n, ast.FunctionDef) and n.name == "process_call_intelligence"
		]
		called = {
			getattr(n.func, "id", None) or getattr(n.func, "attr", None)
			for n in ast.walk(fn)
			if isinstance(n, ast.Call)
		}
		self.assertIn("_get_caller_info", called)
		self.assertNotIn("get_caller_info", called)
		imported = {
			alias.name
			for n in ast.walk(tree)
			if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("api.telephony")
			for alias in n.names
		}
		self.assertIn("_get_caller_info", imported)
		self.assertNotIn("get_caller_info", imported)


class StoredSecretTests(unittest.TestCase):
	def _settings(self, value=None, raises=None):
		reads = []

		def get_password(fieldname, raise_exception=True):
			reads.append((fieldname, raise_exception))
			if raises:
				raise raises
			return value

		settings = types.SimpleNamespace(get_password=get_password, get=lambda f: "*" * 12)
		return settings, reads

	def test_a_stored_value_is_returned(self):
		for module in (stripe_utils, qbo_utils):
			settings, reads = self._settings("whsec_abc")
			self.assertEqual(module.get_secret(settings, "webhook_signing_secret"), "whsec_abc")
			self.assertEqual(reads, [("webhook_signing_secret", False)], module.__name__)

	def test_unconfigured_values_read_as_none(self):
		for module in (stripe_utils, qbo_utils):
			for value in (None, "", "   ", "*", "*" * 12, b"bytes"):
				settings, _ = self._settings(value)
				self.assertIsNone(module.get_secret(settings, "x"), (module.__name__, value))

	def test_a_decryption_failure_reads_as_none_never_the_raw_field(self):
		for module in (stripe_utils, qbo_utils):
			settings, _ = self._settings(raises=ValueError("cannot decrypt"))
			self.assertIsNone(module.get_secret(settings, "x"), module.__name__)

	def test_signature_checks_refuse_when_unconfigured(self):
		self.assertFalse(qbo_utils.verify_intuit_signature(b"{}", "sig", qbo_utils.get_secret(
			self._settings("*" * 12)[0], "webhook_verifier_token"
		)))


class QuickBooksTimeWebhookTests(unittest.TestCase):
	BODY = json.dumps(
		{"timesheets": [{"user_id": "7", "jobcode_id": "", "start": "s", "end": "e", "duration": 3600}]}
	).encode()

	def _call(self, *, secret="s" * 40, headers=None, kwargs=None, form=None, **state):
		_reset(conf={"qb_time_webhook_secret": secret} if secret is not None else {}, body=self.BODY, **state)
		STATE["headers"] = headers or {}
		frappe = sys.modules["frappe"]
		frappe.form_dict = AttrDict(form or {})
		result = qb_time.qb_timesheet_webhook(**(kwargs or {}))
		return result, frappe.local.response.get("http_status_code")

	def test_the_secret_header_is_accepted(self):
		result, status = self._call(headers={"X-QB-Time-Webhook-Secret": "s" * 40})
		self.assertEqual(result["status"], "success")
		self.assertIsNone(status)
		self.assertEqual(len(STATE["inserted"]), 1)

	def test_the_token_parameter_is_still_accepted(self):
		result, _ = self._call(kwargs={"token": "s" * 40})
		self.assertEqual(result["status"], "success")
		result, _ = self._call(form={"token": "s" * 40})
		self.assertEqual(result["status"], "success")

	def test_an_unset_secret_refuses_every_request(self):
		for secret in (None, "", "   "):
			result, status = self._call(secret=secret, headers={"X-QB-Time-Webhook-Secret": ""})
			self.assertEqual((result, status), ({"status": "unauthorized"}, 401))
			self.assertEqual(STATE["inserted"], [])

	def test_a_wrong_or_missing_secret_is_a_fixed_401_without_an_error_log(self):
		for headers in ({}, {"X-QB-Time-Webhook-Secret": "t" * 40}, {"X-QB-Time-Webhook-Secret": "é" * 40}):
			result, status = self._call(headers=headers)
			self.assertEqual((result, status), ({"status": "unauthorized"}, 401))
			self.assertEqual(STATE["inserted"], [])
			self.assertEqual(STATE["errors"], [])

	def test_a_failure_returns_a_fixed_body(self):
		result, status = self._call(
			headers={"X-QB-Time-Webhook-Secret": "s" * 40},
			insert_raises=ValueError("internal detail"),
		)
		self.assertEqual(status, 500)
		self.assertEqual(result["status"], "error")
		self.assertNotIn("internal detail", json.dumps(result))
		self.assertTrue(STATE["errors"], "an accepted request that fails is still logged")


if __name__ == "__main__":
	unittest.main()
