"""The one request this app sends to the Gemini Enterprise Agent Platform, pinned.

``api/gemini.py`` failed silently for its whole life and then loudly for a week,
and both failures were in a string literal no test exercised:

* it authenticated with an API key the platform refuses outright (a 401 that
  reads like a missing IAM grant), until v1.466.2 minted an OAuth2 token;
* it then posted to the ``us-central1`` regional host for a model Google serves
  only on ``global`` (a 404 that reads like a missing IAM grant), every weekday
  from 2026-09-16 until v1.476.0.

So this module asserts the shape of the request rather than the behaviour of
the model: the global host with ``locations/global`` in the path, a bearer
token and **no** ``x-goog-api-key``, the lifecycle note on a 404 and the IAM
grant on a 401/403, the usage row stamped with the model that answered, and —
the one security property — the ``Authorization`` header cleared from the
frame before anything raises, so it cannot reach an Error Log rendered with
"Traceback with variables".

Pure Python. ``frappe``, ``requests``, ``google.auth`` / ``google.oauth2`` and
``drive_utils`` are stubbed at module scope, so this runs with no bench and no
network; it has its own CI step for the same reason the other bench-free
suites do — each installs its own frappe stub, and two in one process would
let one suite's stub decide another's outcome.
"""
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

#: What the stubs record, reset per test.
STATE = {}

TOKEN = "ya29.this-token-must-never-reach-a-log"


def _reset():
	STATE.clear()
	STATE.update(
		{
			"posts": [],
			"inserted": [],
			"logged": [],
			"service_account": {"client_email": "drive@other-project.iam.gserviceaccount.com"},
			"response": None,
			"post_raises": None,
			"usage_tracking": 1,
		}
	)


# ------------------------------------------------------------------ stubs


class _Response:
	def __init__(self, status_code=200, body=None, text=""):
		self.status_code = status_code
		self._body = body or {}
		self.text = text or ""

	def raise_for_status(self):
		if self.status_code >= 400:
			raise _requests.exceptions.HTTPError(f"{self.status_code} Client Error")

	def json(self):
		return self._body


class _InsertingDoc:
	def __init__(self, data):
		self.data = data

	def insert(self, **kwargs):
		STATE["inserted"].append(self.data)
		return self


def _post(url, headers=None, json=None, timeout=None):
	STATE["posts"].append({"url": url, "headers": dict(headers or {}), "json": json, "timeout": timeout})
	if STATE["post_raises"]:
		raise STATE["post_raises"]
	return STATE["response"]


_requests = types.ModuleType("requests")
_requests.post = _post
_requests.exceptions = types.SimpleNamespace(HTTPError=type("HTTPError", (Exception,), {}))
sys.modules["requests"] = _requests

_frappe = types.ModuleType("frappe")
_frappe_utils = types.ModuleType("frappe.utils")
_frappe_utils.cint = lambda value=0, *a, **k: int(float(value or 0))
_frappe_utils.now_datetime = lambda: "2026-09-17 06:30:00"
_frappe.utils = _frappe_utils
_frappe.session = types.SimpleNamespace(user="brian.morisseau@sapphirefountains.com")
_frappe.db = types.SimpleNamespace(get_single_value=lambda doctype, field: STATE["usage_tracking"])
_frappe.get_doc = lambda data: _InsertingDoc(data)
_frappe.log_error = lambda *a, **k: STATE["logged"].append((a, k))
_frappe.get_traceback = lambda: "traceback"
sys.modules["frappe"] = _frappe
sys.modules["frappe.utils"] = _frappe_utils


class _Credentials:
	def __init__(self):
		self.token = None

	@classmethod
	def from_service_account_info(cls, info, scopes=None):
		STATE["scopes"] = list(scopes or [])
		return cls()

	def refresh(self, request):
		self.token = TOKEN


_google = types.ModuleType("google")
_google_auth = types.ModuleType("google.auth")
_google_auth_transport = types.ModuleType("google.auth.transport")
_google_auth_transport_requests = types.ModuleType("google.auth.transport.requests")
_google_auth_transport_requests.Request = lambda: object()
_google_oauth2 = types.ModuleType("google.oauth2")
_google_oauth2_sa = types.ModuleType("google.oauth2.service_account")
_google_oauth2_sa.Credentials = _Credentials
for name, mod in (
	("google", _google),
	("google.auth", _google_auth),
	("google.auth.transport", _google_auth_transport),
	("google.auth.transport.requests", _google_auth_transport_requests),
	("google.oauth2", _google_oauth2),
	("google.oauth2.service_account", _google_oauth2_sa),
):
	sys.modules[name] = mod

_drive_utils = types.ModuleType("erpnext_enhancements.google_drive.drive_utils")
_drive_utils.get_service_account_info = lambda: STATE["service_account"]
_google_drive_pkg = types.ModuleType("erpnext_enhancements.google_drive")
_google_drive_pkg.drive_utils = _drive_utils
sys.modules["erpnext_enhancements.google_drive"] = _google_drive_pkg
sys.modules["erpnext_enhancements.google_drive.drive_utils"] = _drive_utils

sys.modules.pop("erpnext_enhancements.api.gemini", None)
from erpnext_enhancements.api import gemini


def _ok_response(text="Good morning.", thought="Plan the day.", usage=None):
	return _Response(
		200,
		{
			"candidates": [
				{
					"content": {
						"parts": [
							{"thought": True, "text": thought},
							{"text": text},
						]
					}
				}
			],
			"usageMetadata": usage
			or {"promptTokenCount": 120, "candidatesTokenCount": 80, "thoughtsTokenCount": 40, "totalTokenCount": 240},
		},
	)


class EndpointTests(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_default_endpoint_is_the_global_host(self):
		"""The regional form 404'd every weekday briefing for a week."""
		self.assertEqual(
			gemini.build_endpoint(),
			"https://aiplatform.googleapis.com/v1/projects/sapphire-fountains-poseidon"
			"/locations/global/publishers/google/models/gemini-3.8-flash:generateContent",
		)

	def test_global_has_no_region_prefix_on_the_host(self):
		self.assertEqual(gemini.LOCATION, "global")
		self.assertNotIn("global-aiplatform", gemini.build_endpoint())

	def test_a_region_still_builds_the_regional_host(self):
		url = gemini.build_endpoint("gemini-3.5-flash", location="us-central1")
		self.assertTrue(url.startswith("https://us-central1-aiplatform.googleapis.com/"))
		self.assertIn("/locations/us-central1/", url)

	def test_the_model_is_a_current_generation_id(self):
		"""The 2.5 family retires on 2026-10-20 and MINIMAL/thinking rules differ
		by generation. A downgrade here should be a deliberate edit of this test."""
		self.assertTrue(gemini.MODEL_ID.startswith("gemini-3."), gemini.MODEL_ID)
		self.assertIn(gemini.THINKING_LEVEL, ("LOW", "MEDIUM", "HIGH"))


class RequestShapeTests(unittest.TestCase):
	def setUp(self):
		_reset()
		STATE["response"] = _ok_response()

	def test_bearer_token_and_no_api_key(self):
		gemini.generate_content_with_vertex_ai("prompt", "system", None, feature="morning_briefing")
		post = STATE["posts"][0]
		self.assertEqual(post["headers"]["Authorization"], f"Bearer {TOKEN}")
		self.assertNotIn("x-goog-api-key", {k.lower() for k in post["headers"]})
		self.assertEqual(STATE["scopes"], ["https://www.googleapis.com/auth/cloud-platform"])

	def test_the_request_goes_to_the_global_endpoint_for_the_default_model(self):
		gemini.generate_content_with_vertex_ai("prompt", "system", None)
		self.assertEqual(STATE["posts"][0]["url"], gemini.build_endpoint())
		self.assertEqual(STATE["posts"][0]["timeout"], gemini.REQUEST_TIMEOUT)

	def test_payload_carries_system_instruction_and_thinking_level(self):
		gemini.generate_content_with_vertex_ai("the prompt", "the persona", None)
		body = STATE["posts"][0]["json"]
		self.assertEqual(body["contents"][0]["parts"][0]["text"], "the prompt")
		self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "the persona")
		self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingLevel"], gemini.THINKING_LEVEL)
		self.assertIs(body["generationConfig"]["thinkingConfig"]["includeThoughts"], True)

	def test_a_per_call_model_override_changes_the_url_and_the_usage_row(self):
		gemini.generate_content_with_vertex_ai("p", "s", None, feature="email_draft", model_id="gemini-3.1-pro-preview")
		self.assertIn("/models/gemini-3.1-pro-preview:generateContent", STATE["posts"][0]["url"])
		self.assertEqual(STATE["inserted"][0]["model"], "gemini-3.1-pro-preview")

	def test_text_and_thoughts_are_split(self):
		text, thoughts = gemini.generate_content_with_vertex_ai("p", "s", None)
		self.assertEqual(text, "Good morning.")
		self.assertEqual(thoughts, "Plan the day.")

	def test_the_usage_row_names_feature_model_and_tokens(self):
		gemini.generate_content_with_vertex_ai("p", "s", None, feature="morning_briefing")
		row = STATE["inserted"][0]
		self.assertEqual(row["doctype"], "AI Model Usage")
		self.assertEqual(row["feature"], "morning_briefing")
		self.assertEqual(row["model"], gemini.MODEL_ID)
		self.assertEqual(row["total_tokens"], 240)
		self.assertEqual(row["thoughts_tokens"], 40)

	def test_usage_tracking_off_records_nothing(self):
		STATE["usage_tracking"] = 0
		gemini.generate_content_with_vertex_ai("p", "s", None)
		self.assertEqual(STATE["inserted"], [])


class FailureTests(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_missing_service_account_raises_before_any_request(self):
		STATE["service_account"] = None
		with self.assertRaises(Exception) as ctx:
			gemini.generate_content_with_vertex_ai("p", "s", None)
		self.assertIn("Project Folder Google Drive Settings", str(ctx.exception))
		self.assertIn("roles/aiplatform.user", str(ctx.exception))
		self.assertEqual(STATE["posts"], [])

	def test_a_404_names_the_lifecycle_not_iam(self):
		"""The platform's own 404 text says "or your project does not have access
		to it", which is one more status code pointing at IAM. The hint says
		where to actually look."""
		STATE["response"] = _Response(404, text='{"error": {"code": 404, "message": "Publisher model ... was not found"}}')
		with self.assertRaises(Exception) as ctx:
			gemini.generate_content_with_vertex_ai("p", "s", None)
		message = str(ctx.exception)
		self.assertIn("was not found", message)
		self.assertIn("lifecycle", message)
		self.assertIn("MODEL_ID", message)
		self.assertNotIn("roles/aiplatform.user", message)

	def test_401_and_403_name_the_iam_grant(self):
		for status in (401, 403):
			with self.subTest(status=status):
				STATE["posts"].clear()
				STATE["response"] = _Response(status, text='{"error": {"message": "denied"}}')
				with self.assertRaises(Exception) as ctx:
					gemini.generate_content_with_vertex_ai("p", "s", None)
				self.assertIn("roles/aiplatform.user", str(ctx.exception))
				self.assertIn(gemini.PROJECT_ID, str(ctx.exception))

	def test_no_candidates_raises(self):
		STATE["response"] = _Response(200, {"candidates": [], "usageMetadata": {"totalTokenCount": 1}})
		with self.assertRaises(Exception) as ctx:
			gemini.generate_content_with_vertex_ai("p", "s", None)
		self.assertIn("no candidates", str(ctx.exception))

	def test_nothing_is_logged_here(self):
		"""Every caller logs what it catches; a second row here is a duplicate."""
		STATE["response"] = _Response(500, text="boom")
		with self.assertRaises(Exception):
			gemini.generate_content_with_vertex_ai("p", "s", None)
		self.assertEqual(STATE["logged"], [])

	def test_the_bearer_is_cleared_from_the_frame_before_the_error_escapes(self):
		"""Frappe renders "Traceback with variables". If `_post` raised with its
		`headers` local still populated, the token would be written into the
		Error Log. Walk the traceback to the `_post` frame and read the local."""
		STATE["post_raises"] = RuntimeError("connection reset")
		try:
			gemini.generate_content_with_vertex_ai("p", "s", None)
		except Exception as exc:
			self.assertNotIn(TOKEN, str(exc))
			self.assertIn("connection reset", str(exc))
			tb = exc.__traceback__
			post_frames = []
			while tb is not None:
				if tb.tb_frame.f_code.co_name == "_post":
					post_frames.append(tb.tb_frame)
				tb = tb.tb_next
			self.assertTrue(post_frames, "expected the _post frame on the traceback")
			for frame in post_frames:
				self.assertIsNone(frame.f_locals.get("headers"))
			self.assertIsNone(exc.__cause__)
		else:
			self.fail("expected the request failure to raise")


if __name__ == "__main__":
	unittest.main()
