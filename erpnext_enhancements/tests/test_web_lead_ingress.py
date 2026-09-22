"""Bench-free behaviour tests for the website lead ingress (crm_enhancements/web_lead.py).

test_lead_attribution.py guards the fixtures and the shape of the code by reading
it. This suite RUNS ``submit_web_lead`` against a stubbed frappe, because the
defects worth guarding here are all silent in production:

* The secret travels in ``X-Web-Lead-Secret``. The original contract put it in
  ``Authorization: Bearer``, which Frappe v16 rejects with a 401 before the
  endpoint runs (verified on prod 2026-09-22) -- so the endpoint must never read
  ``Authorization``, and a request that only carries it must not be accepted.
* A secret too short to be one is treated as unset.
* The honeypot fires on any non-empty value, strings or not, and succeeds
  silently.
* ``utm_id`` -- the spend-to-lead join key -- lands on the Lead, and the paid-click
  IDs with no field of their own still make the Lead Source "Advertisement" and
  are kept in the submission comment.

The stub is installed in ``setUpModule`` and removed in ``tearDownModule``, so it
never leaks into a suite sharing the process.

Run: python -m unittest erpnext_enhancements.tests.test_web_lead_ingress -v
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

WEB_LEAD = REPO_ROOT / "erpnext_enhancements" / "crm_enhancements" / "web_lead.py"
SECRET = "s" * 43
STUBBED = ("frappe", "frappe.rate_limiter", "frappe.utils")
OURS = (
	"erpnext_enhancements.crm_enhancements.web_lead",
	"erpnext_enhancements.crm_enhancements.attribution",
	"erpnext_enhancements.crm_enhancements.lead_triage",
	"erpnext_enhancements.utils.error_throttle",
)

#: Mutable per-test state the stub reads at call time.
STATE = {}
web_lead = None
_saved = {}


class FakeLead:
	"""Enough of a Lead: every attribution field exists, so hasattr() is True."""

	def __init__(self, fields):
		for name in (
			"custom_lead_source",
			"custom_attribution_captured_on",
			"lead_owner",
			"custom_utm_source",
			"custom_utm_medium",
			"custom_utm_campaign",
			"custom_utm_id",
			"custom_utm_content",
			"custom_utm_term",
			"custom_gclid",
			"custom_landing_page",
			"custom_first_referrer",
		):
			setattr(self, name, None)
		for key, value in fields.items():
			setattr(self, key, value)
		self.doctype = "Lead"
		self.name = None
		self.comments = []

	def set(self, key, value):
		setattr(self, key, value)

	def get(self, key, default=None):
		return getattr(self, key, default)

	def insert(self, ignore_permissions=False):
		self.name = "CRM-LEAD-TEST-0001"
		STATE["inserted"].append(self)
		return self

	def add_comment(self, comment_type, text):
		self.comments.append(text)


class FakeSettings(dict):
	def get_password(self, fieldname, raise_exception=True):
		return STATE["secret"]


def _install_stub():
	frappe = types.ModuleType("frappe")

	def whitelist(**_kw):
		return lambda fn: fn

	frappe.whitelist = whitelist
	frappe._ = lambda text: text
	frappe.local = types.SimpleNamespace(
		response={}, request_ip=None, conf=types.SimpleNamespace(db_name="test")
	)
	frappe.flags = types.SimpleNamespace(
		in_import=False,
		in_migrate=False,
		in_patch=False,
		in_install=False,
		in_test=False,
		in_setup_wizard=False,
	)
	frappe.get_cached_doc = lambda doctype: STATE["settings"]
	frappe.get_request_header = lambda name, default=None: STATE["headers"].get(name, default)
	frappe.get_doc = lambda fields: FakeLead({k: v for k, v in fields.items() if k != "doctype"})
	frappe.get_traceback = lambda: "traceback"
	frappe.log_error = lambda *args, **kwargs: STATE["errors"].append((args, kwargs))

	def cache():
		raise RuntimeError("no redis in a unit test")  # error_throttle falls back to log_error

	frappe.cache = cache

	class Request:
		def get_data(self, as_text=False):
			return STATE["body"]

	frappe.request = Request()
	frappe.form_dict = {}
	frappe.db = types.SimpleNamespace(
		exists=lambda doctype, name=None: (doctype == "Lead Source" and name in STATE["lead_sources"])
		or (doctype == "User" and name == "triage@example.com"),
		has_column=lambda doctype, column: True,
		get_value=lambda *args, **kwargs: None,
		get_default=lambda key: None,
		set_default=lambda key, value: None,
	)
	# No users and no roles: triage finds no owner, which is the ingress's concern
	# only in that the Lead must still be created. tests/test_lead_triage.py covers
	# the owner and the deadline.
	frappe.get_all = lambda *args, **kwargs: []

	rate_limiter = types.ModuleType("frappe.rate_limiter")
	rate_limiter.rate_limit = lambda **_kw: (lambda fn: fn)
	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda value: int(value or 0)
	utils.now_datetime = lambda: "2026-09-22 12:00:00"
	utils.get_datetime = lambda value: value
	utils.get_time = lambda value: value
	utils.get_url_to_form = lambda doctype, name: f"/app/{doctype}/{name}"
	frappe.rate_limiter = rate_limiter
	frappe.utils = utils

	sys.modules["frappe"] = frappe
	sys.modules["frappe.rate_limiter"] = rate_limiter
	sys.modules["frappe.utils"] = utils
	return frappe


def setUpModule():
	global web_lead
	for name in STUBBED + OURS:
		_saved[name] = sys.modules.pop(name, None)
	_install_stub()
	from erpnext_enhancements.crm_enhancements import web_lead as module

	web_lead = module


def tearDownModule():
	for name in STUBBED + OURS:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _reset(payload, headers=None, secret=SECRET, enabled=True, body=None):
	STATE.clear()
	STATE.update(
		{
			"settings": FakeSettings(
				lead_attribution_enabled=1 if enabled else 0,
				web_lead_ingress_enabled=1 if enabled else 0,
				web_lead_default_owner="",
			),
			"secret": secret,
			"headers": {"X-Web-Lead-Secret": SECRET} if headers is None else headers,
			"body": json.dumps(payload) if body is None else body,
			"inserted": [],
			"errors": [],
			"lead_sources": {"Advertisement", "Website", "Email Campaign", "Social Media Campaign"},
		}
	)
	sys.modules["frappe"].local.response = {}
	sys.modules["frappe"].local.request_ip = "203.0.113.10"


def _submit(payload, **kwargs):
	_reset(payload, **kwargs)
	return web_lead.submit_web_lead(**payload)


GOOD = {"first_name": "Jane", "last_name": "Doe", "email_id": "jane@example.com"}


class AuthTests(unittest.TestCase):
	def test_the_secret_header_is_accepted(self):
		result = _submit(dict(GOOD))
		self.assertEqual(result, {"status": "accepted", "lead": "CRM-LEAD-TEST-0001"})
		self.assertEqual(len(STATE["inserted"]), 1)
		self.assertEqual(STATE["errors"], [], "triage with no owner available must still be clean")

	def test_missing_header_is_401(self):
		result = _submit(dict(GOOD), headers={})
		self.assertEqual(result, {"status": "unauthorized"})
		self.assertEqual(sys.modules["frappe"].local.response.get("http_status_code"), 401)
		self.assertEqual(STATE["inserted"], [])

	def test_wrong_secret_is_401(self):
		result = _submit(dict(GOOD), headers={"X-Web-Lead-Secret": "t" * 43})
		self.assertEqual(result["status"], "unauthorized")

	def test_authorization_bearer_is_never_accepted(self):
		# The v1.241.0 contract. Frappe 401s it before we run; if it ever reached us,
		# it must not authorize -- the secret lives in exactly one header.
		result = _submit(dict(GOOD), headers={"Authorization": "Bearer " + SECRET})
		self.assertEqual(result["status"], "unauthorized")

	def test_unset_secret_authorizes_nobody(self):
		result = _submit(dict(GOOD), secret=None)
		self.assertEqual(result["status"], "unauthorized")

	def test_short_secret_is_treated_as_unset_and_logged(self):
		short = "x" * (web_lead.MIN_SECRET_LENGTH - 1)
		result = _submit(dict(GOOD), secret=short, headers={"X-Web-Lead-Secret": short})
		self.assertEqual(result["status"], "unauthorized")
		self.assertTrue(STATE["errors"], "a too-short secret must say so in the Error Log")
		self.assertNotIn(short, repr(STATE["errors"]), "the secret must never reach the Error Log")

	def test_disabled_ingress_is_inert(self):
		result = _submit(dict(GOOD), enabled=False)
		self.assertEqual(result, {"status": "rejected"})
		self.assertEqual(STATE["inserted"], [])

	def test_source_never_reads_authorization(self):
		# Belt and braces for the behavioural test above: no string literal outside a
		# docstring names the header, so nobody can quietly add it back as a fallback.
		tree = ast.parse(WEB_LEAD.read_text(encoding="utf-8"))
		docstrings = set()
		for node in ast.walk(tree):
			if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
				first = node.body[0]
				if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
					docstrings.add(id(first.value))
		literals = [
			n.value
			for n in ast.walk(tree)
			if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
		]
		self.assertFalse([s for s in literals if s.strip().lower().startswith("authorization")])
		self.assertIn("X-Web-Lead-Secret", literals)


class HoneypotTests(unittest.TestCase):
	def test_filled_honeypot_fakes_success(self):
		payload = dict(GOOD, hp_company_url="http://spam.example")
		self.assertEqual(_submit(payload), {"status": "accepted", "lead": None})
		self.assertEqual(STATE["inserted"], [])

	def test_non_string_honeypot_is_a_bot(self):
		payload = dict(GOOD, hp_company_url=1)
		self.assertEqual(_submit(payload), {"status": "accepted", "lead": None})
		self.assertEqual(STATE["inserted"], [])

	def test_empty_honeypot_is_a_human(self):
		payload = dict(GOOD, hp_company_url="  ")
		self.assertEqual(_submit(payload)["lead"], "CRM-LEAD-TEST-0001")

	def test_honeypot_is_read_from_the_raw_body(self):
		# frappe sanitises a guest's form_dict and can blank the value; the raw body
		# is what a bot actually sent.
		body = json.dumps(dict(GOOD, hp_company_url="<b>spam</b>"))
		_reset(dict(GOOD, hp_company_url=""), body=body)
		result = web_lead.submit_web_lead(**dict(GOOD, hp_company_url=""))
		self.assertEqual(result, {"status": "accepted", "lead": None})


class AttributionTests(unittest.TestCase):
	def test_utm_id_lands_on_the_lead(self):
		payload = dict(
			GOOD, utm_source="google", utm_medium="cpc", utm_campaign="spring", utm_id="21456789012"
		)
		_submit(payload)
		lead = STATE["inserted"][0]
		self.assertEqual(lead.custom_utm_id, "21456789012")
		self.assertEqual(lead.custom_utm_campaign, "spring")
		self.assertEqual(lead.custom_lead_source, "Advertisement")

	def test_gbraid_alone_is_a_paid_click(self):
		_submit(dict(GOOD, gbraid="0AAAAA-ios", landing_page="/"))
		lead = STATE["inserted"][0]
		self.assertEqual(lead.custom_lead_source, "Advertisement")
		self.assertIsNone(lead.custom_gclid, "gbraid is not a gclid and must not be stored as one")
		self.assertIn("gbraid=0AAAAA-ios", lead.comments[-1])

	def test_msclkid_is_paid_but_fbclid_is_not(self):
		_submit(dict(GOOD, msclkid="MS1", landing_page="/"))
		self.assertEqual(STATE["inserted"][0].custom_lead_source, "Advertisement")
		_submit(dict(GOOD, fbclid="FB1", landing_page="/"))
		self.assertEqual(STATE["inserted"][0].custom_lead_source, "Website")

	def test_context_comment_records_the_real_caller(self):
		_submit(dict(GOOD, form_name="contact-us", notes="A courtyard fountain"))
		lead = STATE["inserted"][0]
		self.assertEqual(lead.comments[0], "Website enquiry:\n\nA courtyard fountain")
		self.assertIn("caller_ip=203.0.113.10", lead.comments[-1])
		self.assertIn("form_name=contact-us", lead.comments[-1])

	def test_no_contact_method_is_400(self):
		result = _submit({"first_name": "Nobody"})
		self.assertEqual(result, {"status": "rejected", "reason": "no_contact_method"})
		self.assertEqual(sys.modules["frappe"].local.response.get("http_status_code"), 400)

	def test_unknown_keys_never_reach_the_document(self):
		_submit(dict(GOOD, status="Converted", owner="Administrator", docstatus=1))
		lead = STATE["inserted"][0]
		self.assertEqual(lead.status, "Lead")
		self.assertFalse(hasattr(lead, "docstatus"))


if __name__ == "__main__":
	unittest.main()
