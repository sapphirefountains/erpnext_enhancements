"""Bench-free tests for the emailed signing code on ``/contract-sign`` (v1.836.2).

Nik, 2026-11-19: before a customer signs online, the server emails a short code to the
address on file and the customer types it back. These cases RUN the code, over a stand-in
``frappe`` with an in-memory cache and a clock the tests move:

* ``esign.email_code``: six digits, ten minutes, five tries, three sends per fifteen
  minutes, single use, bound to the link, only a keyed hash in the cache, the masks;
* ``esign.portal``: the endpoints. A right code lets the session sign and decline; a
  wrong, expired, reused or exhausted one is refused with one message; the send limit
  trips; the bot-check verdict is the server's own (a page cannot state it); the
  agreement comes back whole only after the code; the evidence carries the time;
* ``www/contract_sign.get_context``: the agreement rendered before the code masks the
  signer's address;
* ``esign.lifecycle.execute_contract``: the evidence record notes the verified code and
  when.

The stub is installed in ``setUpModule`` and removed in ``tearDownModule``, so it never
reaches the bench-only suites' ``import frappe`` guards. Own CI step: it must not meet
``test_contract_esign``'s stub in one process.

Run: python -m unittest erpnext_enhancements.tests.test_esign_email_code
"""

import ast
import hashlib
import inspect
import json
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP_DIR = Path(__file__).resolve().parents[1]

CLOCK = {"now": 1_800_000_000.0}
STATE = {"secret": None, "mail": [], "rendered": [], "executed": [], "db_writes": [], "locked": []}
SIGNER = "jane.doe@example.com"

email_code = None
portal = None
lifecycle = None

_INSTALLED = []


class _Dict(dict):
    """``frappe._dict``: a dict with attribute reads."""

    def __getattr__(self, key):
        return self.get(key)


class FakeCache:
    """The parts of ``frappe.cache`` the signing code uses, with expiry on ``CLOCK``."""

    def __init__(self):
        self.store = {}

    def _alive(self, key):
        item = self.store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and CLOCK["now"] >= expires_at:
            del self.store[key]
            return None
        return item

    def make_key(self, key):
        return f"site1|{key}"

    def get_value(self, key):
        item = self._alive(self.make_key(key))
        return item[0] if item else None

    def set_value(self, key, value, expires_in_sec=None):
        expires_at = CLOCK["now"] + expires_in_sec if expires_in_sec else None
        self.store[self.make_key(key)] = (value, expires_at)

    def delete_value(self, key):
        self.store.pop(self.make_key(key), None)

    def incrby(self, key, amount):
        item = self._alive(key)
        value = (item[0] if item else 0) + amount
        self.store[key] = (value, item[1] if item else None)
        return value

    def expire(self, key, ttl):
        value, _ = self.store[key]
        self.store[key] = (value, CLOCK["now"] + ttl)

    def flush(self):
        """What a deploy does to it."""
        self.store.clear()


class _Doc:
    """A Document-ish object: attributes, ``get``, ``flags``, and recorded saves."""

    def __init__(self, **values):
        self.flags = _Dict()
        self.saved = 0
        self.db_sets = {}
        for key, value in values.items():
            setattr(self, key, value)

    def get(self, key, default=None):
        return getattr(self, key, default)

    def save(self, **kwargs):
        self.saved += 1

    def db_set(self, key, value, **kwargs):
        self.db_sets[key] = value
        setattr(self, key, value)

    def add_comment(self, *args, **kwargs):
        pass


def _install_frappe_stub(cache):
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe._dict = _Dict
    frappe.cache = cache
    frappe.local = types.SimpleNamespace(
        conf=_Dict(encryption_key="test-site-key"), request_ip="203.0.113.7", form_dict=_Dict()
    )
    frappe.form_dict = frappe.local.form_dict
    frappe.request = None
    frappe.session = types.SimpleNamespace(user="Guest", data={})
    frappe.flags = _Dict()
    frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    frappe.log_error = lambda *a, **k: None
    frappe.get_traceback = lambda: ""
    frappe.clear_messages = lambda: None
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("throw"))
    frappe.get_all = lambda *a, **k: []
    frappe.enqueue = lambda *a, **k: None
    frappe.get_request_header = lambda name, default=None: "Test Browser/1.0"
    frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "Sapphire Fountains")
    frappe.generate_hash = lambda length=10: ("S" * 8 + str(len(STATE["mail"])) + "x" * 40)[:length]

    def whitelist(*dargs, **dkwargs):
        def wrap(fn):
            return fn

        return wrap(dargs[0]) if dargs and callable(dargs[0]) else wrap

    frappe.whitelist = whitelist

    def sendmail(**kwargs):
        STATE["mail"].append(kwargs)

    frappe.sendmail = sendmail

    def get_doc(doctype, name=None, *args, **kwargs):
        if doctype == "Contract Signature Request":
            return STATE["request"]
        if doctype == "Project Contract":
            return STATE["contract"]
        return None

    frappe.get_doc = get_doc
    frappe.get_cached_doc = lambda doctype, name=None: _Dict(
        contract_esign_consent_text="I agree to sign electronically.",
        contract_esign_disclosure_text="Disclosure.",
    )

    def get_value(doctype, name=None, fieldname=None, as_dict=False, for_update=False, **kwargs):
        if doctype == "Contract Signature Request" and fieldname == "email_attempts":
            return STATE.get("email_attempts", 0)
        if doctype == "Project Contract" and for_update:
            return _Dict(docstatus=1, status="Out for Signature")
        if doctype == "Project Contract" and fieldname == "template_key":
            return "maintenance_services_agreement"
        if doctype == "Project Contract" and isinstance(fieldname, list) and "contract_template" in fieldname:
            return _Dict(contract_template="Maintenance Services Agreement", title="")
        if doctype == "Contract Template":
            return "Maintenance Services Agreement"
        return None

    def set_value(doctype, name, field, value=None, **kwargs):
        STATE["db_writes"].append((doctype, name, field, value))
        if field == "email_attempts":
            STATE["email_attempts"] = value
        if isinstance(field, dict) and field.get("status") == "Locked":
            STATE["locked"].append(name)

    frappe.db = types.SimpleNamespace(
        get_value=get_value,
        set_value=set_value,
        commit=lambda: None,
        rollback=lambda: None,
        get_single_value=lambda *a, **k: None,
    )

    utils = types.ModuleType("frappe.utils")

    def cint(value):
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    utils.cint = cint
    utils.escape_html = lambda s: (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    )
    utils.get_datetime = lambda value=None: value
    utils.getdate = lambda value=None: value
    utils.now_datetime = lambda: types.SimpleNamespace(isoformat=lambda: "2026-11-19T10:00:00")
    utils.now = lambda: f"2026-11-19 10:{int(CLOCK['now']) % 60:02d}:00.000000"
    utils.format_datetime = lambda value=None, *a, **k: str(value or "")
    utils.get_system_timezone = lambda: "America/Denver"
    utils.cstr = lambda value: "" if value is None else str(value)
    frappe.utils = utils

    rate_limiter = types.ModuleType("frappe.rate_limiter")
    rate_limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
    frappe.rate_limiter = rate_limiter

    for name, module in (("frappe", frappe), ("frappe.utils", utils), ("frappe.rate_limiter", rate_limiter)):
        sys.modules[name] = module
        _INSTALLED.append(name)
    return frappe


def _real_verify_turnstile():
    """The real ``intake._verify_turnstile``, compiled from its source over a stand-in.

    Importing ``fountain_move.intake`` pulls in far more of frappe than this stub has. Only
    the configuration it reads (the secret) and the network call are stood in for; the
    decision about a missing or malformed token is the real code's.
    """
    source_path = APP_DIR / "crm_enhancements" / "fountain_move" / "intake.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    wanted = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_verify_turnstile":
            wanted.append(node)
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in ("TURNSTILE_TOKEN_RE", "TURNSTILE_VERIFY_URL", "TURNSTILE_TIMEOUT", "TURNSTILE_ACTION")
            for t in node.targets
        ):
            wanted.append(node)
    namespace = {
        "re": re,
        "frappe": sys.modules["frappe"],
        "_turnstile_secret": lambda field=None: STATE["secret"],
        "_hostname_allowed": lambda hostname: True,
        "_challenge_fresh": lambda ts: True,
    }
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["_verify_turnstile"]


def _fake_requests():
    """Cloudflare's siteverify as a stand-in: "good-token" passes, anything else fails."""
    module = types.ModuleType("requests")

    def post(url, data=None, timeout=None):
        ok = data.get("response") == "good-token"
        payload = {"success": ok, "action": "contract-sign", "hostname": "erp.example.com", "challenge_ts": "now"}
        if not ok:
            payload["error-codes"] = ["invalid-input-response"]
        return types.SimpleNamespace(status_code=200, json=lambda: payload)

    module.post = post
    return module


CACHE = FakeCache()


def setUpModule():
    global email_code, portal, lifecycle
    _install_frappe_stub(CACHE)
    for mod in list(sys.modules):
        if mod.startswith("erpnext_enhancements.project_enhancements.esign"):
            sys.modules.pop(mod, None)

    intake = types.ModuleType("erpnext_enhancements.crm_enhancements.fountain_move.intake")
    intake._verify_turnstile = _real_verify_turnstile()
    for name, module in (
        ("erpnext_enhancements.crm_enhancements.fountain_move.intake", intake),
        ("requests", _fake_requests()),
    ):
        if name in sys.modules:
            _INSTALLED.append(("restore", name, sys.modules[name]))
        sys.modules[name] = module
        _INSTALLED.append(name)

    from erpnext_enhancements.project_enhancements.esign import email_code as _email_code
    from erpnext_enhancements.project_enhancements.esign import lifecycle as _lifecycle
    from erpnext_enhancements.project_enhancements.esign import portal as _portal

    email_code, portal, lifecycle = _email_code, _portal, _lifecycle
    email_code._now = lambda: CLOCK["now"]


def tearDownModule():
    restores = [entry for entry in _INSTALLED if isinstance(entry, tuple)]
    for entry in _INSTALLED:
        if isinstance(entry, str):
            sys.modules.pop(entry, None)
    for _, name, module in restores:
        sys.modules[name] = module
    for mod in list(sys.modules):
        if mod.startswith("erpnext_enhancements.project_enhancements.esign"):
            sys.modules.pop(mod, None)


def _reset():
    CACHE.flush()
    CLOCK["now"] += 10_000
    STATE.update(secret=None, mail=[], rendered=[], executed=[], db_writes=[], locked=[], email_attempts=0)


# ---------------------------------------------------------------------------
# The code itself
# ---------------------------------------------------------------------------


class EmailCodeRules(unittest.TestCase):
    REQ = "CSR-2026-00001"
    LINK = "a" * 64

    def setUp(self):
        _reset()

    def test_a_code_is_six_digits_from_the_cryptographic_source(self):
        codes = {email_code.new_code() for _ in range(300)}
        self.assertTrue(all(re.fullmatch(r"[0-9]{6}", c) for c in codes))
        self.assertGreater(len(codes), 250, "codes repeat far too often to be random")
        source = inspect.getsource(email_code.new_code)
        self.assertIn("secrets.randbelow", source)

    def test_the_right_code_works_once(self):
        code = email_code.issue(self.REQ, self.LINK)
        self.assertTrue(email_code.check(self.REQ, self.LINK, code))
        self.assertFalse(email_code.check(self.REQ, self.LINK, code), "a used code worked a second time")

    def test_spaces_and_dashes_inside_the_code_are_ignored(self):
        code = email_code.issue(self.REQ, self.LINK)
        self.assertTrue(email_code.check(self.REQ, self.LINK, f" {code[:3]}-{code[3:]} "))

    def test_a_wrong_code_is_refused(self):
        code = email_code.issue(self.REQ, self.LINK)
        wrong = f"{(int(code) + 1) % 1_000_000:06d}"
        self.assertFalse(email_code.check(self.REQ, self.LINK, wrong))
        self.assertTrue(email_code.check(self.REQ, self.LINK, code), "one wrong try must not spend the code")

    def test_an_expired_code_is_refused(self):
        code = email_code.issue(self.REQ, self.LINK)
        CLOCK["now"] += email_code.CODE_TTL - 1
        self.assertTrue(email_code.check(self.REQ, self.LINK, code), "a code must work for its whole ten minutes")
        code = email_code.issue(self.REQ, self.LINK)
        CLOCK["now"] += email_code.CODE_TTL + 1
        self.assertFalse(email_code.check(self.REQ, self.LINK, code))

    def test_the_expiry_is_checked_even_if_the_cache_keeps_the_record(self):
        code = email_code.issue(self.REQ, self.LINK)
        key = CACHE.make_key(email_code._key(self.REQ))
        value, _ = CACHE.store[key]
        CACHE.store[key] = (value, None)  # a cache that never expires anything
        CLOCK["now"] += email_code.CODE_TTL + 1
        self.assertFalse(email_code.check(self.REQ, self.LINK, code))

    def test_five_wrong_tries_spend_the_code(self):
        code = email_code.issue(self.REQ, self.LINK)
        wrong = f"{(int(code) + 7) % 1_000_000:06d}"
        for _ in range(email_code.MAX_TRIES):
            self.assertFalse(email_code.check(self.REQ, self.LINK, wrong))
        self.assertFalse(email_code.check(self.REQ, self.LINK, code), "the right code worked after five wrong tries")

    def test_the_right_code_on_the_fifth_try_still_works(self):
        code = email_code.issue(self.REQ, self.LINK)
        wrong = f"{(int(code) + 7) % 1_000_000:06d}"
        for _ in range(email_code.MAX_TRIES - 1):
            email_code.check(self.REQ, self.LINK, wrong)
        self.assertTrue(email_code.check(self.REQ, self.LINK, code))

    def test_a_code_is_bound_to_the_link_it_was_issued_for(self):
        code = email_code.issue(self.REQ, self.LINK)
        self.assertFalse(email_code.check(self.REQ, "b" * 64, code), "a code survived a resend of the link")
        self.assertFalse(email_code.check("CSR-2026-00002", self.LINK, code), "a code worked for another request")
        self.assertFalse(email_code.check(self.REQ, None, code))

    def test_a_new_code_replaces_the_old_one(self):
        first = email_code.issue(self.REQ, self.LINK)
        second = email_code.issue(self.REQ, self.LINK)
        if first != second:
            self.assertFalse(email_code.check(self.REQ, self.LINK, first))
        self.assertTrue(email_code.check(self.REQ, self.LINK, second))

    def test_three_sends_per_fifteen_minutes(self):
        for _ in range(email_code.MAX_SENDS):
            self.assertTrue(email_code.send_allowed(self.REQ))
        self.assertFalse(email_code.send_allowed(self.REQ))
        self.assertTrue(email_code.send_allowed("CSR-2026-00002"), "the limit is per request")
        CLOCK["now"] += email_code.SEND_WINDOW + 1
        self.assertTrue(email_code.send_allowed(self.REQ))

    def test_only_a_keyed_hash_is_kept_and_only_in_the_cache(self):
        code = email_code.issue(self.REQ, self.LINK)
        stored = " ".join(str(v) for v, _ in CACHE.store.values())
        self.assertNotIn(code, stored)
        record = json.loads(CACHE.get_value(email_code._key(self.REQ)))
        self.assertNotEqual(record["digest"], hashlib.sha256(code.encode()).hexdigest())
        self.assertNotEqual(record["digest"], hashlib.sha256(f"{self.REQ}:{record['salt']}:{code}".encode()).hexdigest())
        self.assertEqual(record["token_hash"], self.LINK)
        source = Path(email_code.__file__).read_text(encoding="utf-8")
        self.assertNotIn("frappe.db.", source)
        self.assertIn("hmac.compare_digest(", source)

    def test_a_cleared_cache_refuses_the_outstanding_code_and_a_new_one_works(self):
        code = email_code.issue(self.REQ, self.LINK)
        CACHE.flush()
        self.assertFalse(email_code.check(self.REQ, self.LINK, code))
        self.assertTrue(email_code.check(self.REQ, self.LINK, email_code.issue(self.REQ, self.LINK)))

    def test_malformed_entries_never_reach_the_cache(self):
        email_code.issue(self.REQ, self.LINK)
        before = dict(CACHE.store)
        for bad in (None, "", "12345", "1234567", "abcdef", "12 34 5"):
            self.assertFalse(email_code.check(self.REQ, self.LINK, bad))
        self.assertEqual(CACHE.store, before, "a malformed entry spent a try")

    def test_the_mask_is_the_first_character_and_the_domain(self):
        self.assertEqual(email_code.mask_email("jane.doe@example.com"), "j•••@example.com")
        self.assertEqual(email_code.mask_email("j@example.com"), "j•••@example.com")
        self.assertEqual(email_code.mask_email("not-an-address"), "")
        self.assertEqual(email_code.mask_email(None), "")

    def test_the_address_is_masked_wherever_the_document_prints_it(self):
        html = (
            "<td><b>Email:</b></td><td><span class='ct-fill'>Jane.Doe@Example.com</span></td>"
            "<td>Invoices emailed to: jane.doe@example.com</td>"
            "<td>Sapphire Email: office@sapphire.example</td>"
        )
        out = email_code.mask_address_in(html, SIGNER)
        self.assertNotIn("jane.doe@example.com", out.lower())
        self.assertEqual(out.count("j•••@example.com"), 2)
        self.assertIn("office@sapphire.example", out, "only the signer's address is masked")

    def test_an_escaped_address_is_masked_too(self):
        email = "o'neil@example.com"
        for spelling in ("o&apos;neil@example.com", "o&#x27;neil@example.com", "o&#39;neil@example.com"):
            out = email_code.mask_address_in(f"<p>{spelling}</p>", email)
            self.assertEqual(out, "<p>o•••@example.com</p>", spelling)


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------


def _call(fn, **kwargs):
    """Call an endpoint the way frappe does: arguments it does not declare are dropped."""
    params = inspect.signature(fn).parameters
    return fn(**{k: v for k, v in kwargs.items() if k in params})


class PortalCodeStep(unittest.TestCase):
    def setUp(self):
        _reset()
        self.request = _Doc(
            name="CSR-2026-00042",
            status="Sent",
            token_hash="c" * 64,
            signer_email=SIGNER,
            project_contract="PC-2026-0042",
            document_snapshot="<p>Client email: jane.doe@example.com</p><p>Terms.</p>",
            provider="In-House",
            signing_channel="Email Link",
        )
        STATE["request"] = self.request
        self._saved = {
            "resolve": portal.lifecycle.resolve_request,
            "signable": portal.lifecycle.is_signable,
            "execute": portal.lifecycle.execute_contract,
            "gate": portal._require_public_page,
            "render": portal.email_style.render,
        }
        portal.lifecycle.resolve_request = lambda ref: self.request if ref == "the-link" else None
        portal.lifecycle.is_signable = lambda request, contract=None: request.status in ("Sent", "Viewed")
        portal.lifecycle.execute_contract = lambda request, evidence: STATE["executed"].append(evidence)
        portal._require_public_page = lambda: None

        def render(template, context=None, **kwargs):
            STATE["rendered"].append((template, context, kwargs))
            return "<html>wrapped</html>"

        portal.email_style.render = render

    def tearDown(self):
        portal.lifecycle.resolve_request = self._saved["resolve"]
        portal.lifecycle.is_signable = self._saved["signable"]
        portal.lifecycle.execute_contract = self._saved["execute"]
        portal._require_public_page = self._saved["gate"]
        portal.email_style.render = self._saved["render"]

    # helpers ---------------------------------------------------------------

    def _begin(self, **kwargs):
        result = _call(portal.begin_signing, ref="the-link", **kwargs)
        self.assertTrue(result["ok"], result)
        return result

    def _send(self, sid):
        return _call(portal.send_signing_code, esign_sid=sid)

    def _last_code(self):
        return STATE["rendered"][-1][1]["code"]

    def _sign(self, sid):
        return _call(
            portal.sign_contract, esign_sid=sid, signature_mode="Typed", signed_name="Jane Doe", agree=1, initials="{}"
        )

    # the bot check ---------------------------------------------------------

    def test_begin_takes_no_parameter_that_states_the_bot_check_outcome(self):
        self.assertEqual(list(inspect.signature(portal.begin_signing).parameters), ["ref", "turnstile_token", "esign_sid"])

    def test_a_request_with_no_token_goes_on_to_the_code_and_is_recorded_as_not_checked_in_the_browser(self):
        STATE["secret"] = "configured"
        for token in (None, "", "   "):
            result = self._begin(turnstile_token=token)
            self.assertEqual(result["verdict"], "Unavailable")
            self.assertTrue(portal.turnstile.accepts(result["verdict"]))
            session = portal._get_session(result["esign_sid"])
            self.assertEqual(session["turnstile"], {"errors": portal.turnstile.NO_TOKEN_DETAIL})
            self.assertIn("no bot-check token", session["turnstile"]["errors"].lower())

    def test_a_no_token_session_can_be_sent_a_code_verify_it_sign_and_decline(self):
        STATE["secret"] = "configured"
        sid = self._begin()["esign_sid"]
        self.assertTrue(self._send(sid)["ok"])
        self.assertTrue(_call(portal.verify_signing_code, esign_sid=sid, code=self._last_code())["ok"])
        self.assertTrue(self._sign(sid)["ok"])
        evidence = STATE["executed"][-1]
        self.assertEqual(evidence.turnstile["verdict"], "Unavailable")
        self.assertEqual(evidence.turnstile["errors"], portal.turnstile.NO_TOKEN_DETAIL)

        other = self._begin(esign_sid="Declinesession123")["esign_sid"]
        self._send(other)
        _call(portal.verify_signing_code, esign_sid=other, code=self._last_code())
        self.request.status = "Sent"
        self.assertTrue(_call(portal.decline_contract, esign_sid=other, reason="no")["ok"])
        self.assertEqual(self.request.status, "Declined")

    def test_a_token_that_is_sent_and_fails_is_still_refused_at_the_code_step(self):
        STATE["secret"] = "configured"
        result = self._begin(turnstile_token="wrong-token")
        self.assertEqual(result["verdict"], "Failed")
        sent = self._send(result["esign_sid"])
        self.assertFalse(sent["ok"])
        self.assertTrue(sent.get("retry_captcha"))
        checked = _call(portal.verify_signing_code, esign_sid=result["esign_sid"], code="123456")
        self.assertFalse(checked["ok"])
        self.assertEqual(STATE["mail"], [], "a code was emailed without an accepted bot check")

    def test_a_no_token_session_keeps_every_code_limit(self):
        STATE["secret"] = "configured"
        sid = self._begin()["esign_sid"]
        for _ in range(email_code.MAX_SENDS):
            self.assertTrue(self._send(sid)["ok"])
        self.assertFalse(self._send(sid)["ok"])
        self.assertEqual(len(STATE["mail"]), email_code.MAX_SENDS)

        CLOCK["now"] += email_code.CODE_TTL * 100
        STATE["email_attempts"] = 0
        other = self._begin(esign_sid="Limitsession1234")["esign_sid"]
        self._send(other)
        code = self._last_code()
        wrong = f"{(int(code) + 3) % 1_000_000:06d}"
        for _ in range(email_code.MAX_TRIES):
            _call(portal.verify_signing_code, esign_sid=other, code=wrong)
        self.assertEqual(
            _call(portal.verify_signing_code, esign_sid=other, code=code), {"ok": False, "error": portal.CODE_FAILURE}
        )
        CLOCK["now"] += 3601  # past the hourly entry cap, so only the lifetime limit is in play
        STATE["email_attempts"] = portal.CODE_ENTRIES_LIFETIME
        third = self._begin(esign_sid="Lifetimesession12")["esign_sid"]
        self._send(third)
        result = _call(portal.verify_signing_code, esign_sid=third, code=self._last_code())
        self.assertEqual(result, {"ok": False, "error": portal.CODE_FAILURE})
        self.assertEqual(STATE["locked"], [self.request.name])

    def test_a_passed_bot_check_lets_the_code_be_sent(self):
        STATE["secret"] = "configured"
        result = self._begin(turnstile_token="good-token")
        self.assertEqual(result["verdict"], "Passed")
        self.assertTrue(self._send(result["esign_sid"])["ok"])

    def test_a_site_without_a_secret_keeps_its_behaviour(self):
        STATE["secret"] = None
        result = self._begin(turnstile_token=None)
        self.assertEqual(result["verdict"], "Not Checked")
        self.assertTrue(self._send(result["esign_sid"])["ok"])

    # sending ---------------------------------------------------------------

    def test_the_code_goes_to_the_address_on_file_through_the_email_shell(self):
        sid = self._begin()["esign_sid"]
        result = self._send(sid)
        self.assertEqual(result, {"ok": True, "sent_to": "j•••@example.com", "minutes": 10})
        mail = STATE["mail"][-1]
        self.assertEqual(mail["recipients"], [SIGNER])
        self.assertTrue(mail["now"], "a code must not wait for the queue")
        self.assertTrue(mail["redact_message_after_send"], "the code must not stay in the Email Queue")
        template, context, kwargs = STATE["rendered"][-1]
        self.assertEqual(template, portal.CODE_TEMPLATE)
        self.assertEqual(context["contract_label"], "Maintenance Services Agreement")
        self.assertRegex(context["code"], r"^[0-9]{6}$")
        self.assertNotIn(context["code"], mail["subject"])
        self.assertIn("Maintenance Services Agreement", mail["subject"])
        self.assertEqual(kwargs.get("pillar"), "build")

    def test_the_send_limit_trips(self):
        sid = self._begin()["esign_sid"]
        for _ in range(email_code.MAX_SENDS):
            self.assertTrue(self._send(sid)["ok"])
        refused = self._send(sid)
        self.assertFalse(refused["ok"])
        self.assertEqual(len(STATE["mail"]), email_code.MAX_SENDS)

    def test_a_lost_session_asks_the_page_to_start_again(self):
        sent = self._send("Nosuchsession123")
        self.assertEqual((sent["ok"], sent.get("restart")), (False, True))
        checked = _call(portal.verify_signing_code, esign_sid="Nosuchsession123", code="123456")
        self.assertEqual((checked["ok"], checked.get("restart")), (False, True))
        self.assertEqual(checked["error"], portal.CODE_FAILURE)

    # checking --------------------------------------------------------------

    def test_a_right_code_returns_the_whole_agreement_and_lets_the_session_sign(self):
        sid = self._begin()["esign_sid"]
        self.assertEqual(self._sign(sid).get("code_required"), True)
        self.assertEqual(STATE["executed"], [], "signed without a code")

        self._send(sid)
        result = _call(portal.verify_signing_code, esign_sid=sid, code=self._last_code())
        self.assertTrue(result["ok"], result)
        self.assertIn(SIGNER, result["agreement_html"], "after the code the document shows in full")

        signed = self._sign(sid)
        self.assertTrue(signed["ok"], signed)
        evidence = STATE["executed"][-1]
        self.assertEqual(evidence.email_confirmed, 1)
        self.assertTrue(evidence.email_code_verified_on.startswith("2026-11-19"))

    def test_wrong_expired_reused_and_exhausted_codes_get_one_message(self):
        sid = self._begin()["esign_sid"]
        self._send(sid)
        code = self._last_code()
        wrong = f"{(int(code) + 3) % 1_000_000:06d}"

        outcomes = {"wrong": _call(portal.verify_signing_code, esign_sid=sid, code=wrong)}

        CLOCK["now"] += email_code.CODE_TTL + 1
        outcomes["expired"] = _call(portal.verify_signing_code, esign_sid=sid, code=code)

        self._send(sid)
        code = self._last_code()
        self.assertTrue(_call(portal.verify_signing_code, esign_sid=sid, code=code)["ok"])
        other = self._begin()["esign_sid"]  # a second session, on the same link
        self.assertNotEqual(other, sid)
        outcomes["reused"] = _call(portal.verify_signing_code, esign_sid=other, code=code)

        STATE["email_attempts"] = 0
        CLOCK["now"] += 3601  # past the hourly entry cap, so only the code's own limit is in play
        self._begin(esign_sid=other)  # sessions last an hour too
        self._send(other)
        code = self._last_code()
        wrong = f"{(int(code) + 3) % 1_000_000:06d}"
        for _ in range(email_code.MAX_TRIES):
            _call(portal.verify_signing_code, esign_sid=other, code=wrong)
        outcomes["exhausted"] = _call(portal.verify_signing_code, esign_sid=other, code=code)

        for name, outcome in outcomes.items():
            self.assertEqual(outcome, {"ok": False, "error": portal.CODE_FAILURE}, name)
        self.assertEqual(self._sign(other).get("code_required"), True)

    def test_a_blank_or_malformed_entry_costs_nothing(self):
        sid = self._begin()["esign_sid"]
        self._send(sid)
        for bad in ("", None, "12ab56"):
            result = _call(portal.verify_signing_code, esign_sid=sid, code=bad)
            self.assertFalse(result["ok"])
            self.assertNotEqual(result["error"], portal.CODE_FAILURE)
        self.assertEqual(STATE.get("email_attempts", 0), 0)

    def test_entries_count_against_the_request_and_the_lifetime_limit_pauses_the_link(self):
        sid = self._begin()["esign_sid"]
        STATE["email_attempts"] = portal.CODE_ENTRIES_LIFETIME
        self._send(sid)
        result = _call(portal.verify_signing_code, esign_sid=sid, code=self._last_code())
        self.assertEqual(result, {"ok": False, "error": portal.CODE_FAILURE})
        self.assertEqual(STATE["locked"], [self.request.name])

    def test_a_resent_link_needs_the_code_again(self):
        sid = self._begin()["esign_sid"]
        self._send(sid)
        self.assertTrue(_call(portal.verify_signing_code, esign_sid=sid, code=self._last_code())["ok"])
        self.request.token_hash = "d" * 64  # staff re-sent the link
        self._begin(esign_sid=sid)  # the page reopened on the new link, same session id
        self.assertEqual(self._sign(sid).get("code_required"), True)
        self.assertEqual(STATE["executed"], [])

    def test_declining_needs_the_code_too(self):
        sid = self._begin()["esign_sid"]
        refused = _call(portal.decline_contract, esign_sid=sid, reason="no")
        self.assertEqual(refused.get("code_required"), True)
        self.assertEqual(self.request.status, "Sent")
        self._send(sid)
        _call(portal.verify_signing_code, esign_sid=sid, code=self._last_code())
        self.assertTrue(_call(portal.decline_contract, esign_sid=sid, reason="no")["ok"])
        self.assertEqual(self.request.status, "Declined")

    def test_no_endpoint_takes_an_email_address(self):
        for fn in (portal.begin_signing, portal.send_signing_code, portal.verify_signing_code, portal.sign_contract, portal.decline_contract):
            self.assertFalse(
                [p for p in inspect.signature(fn).parameters if "email" in p], f"{fn.__name__} takes an address"
            )

    def test_the_new_endpoints_are_post_only_guest_endpoints_and_registered(self):
        source = Path(portal.__file__).read_text(encoding="utf-8")
        for name in ("send_signing_code", "verify_signing_code"):
            self.assertRegex(
                source,
                r'@frappe\.whitelist\(allow_guest=True, methods=\["POST"\]\)\s*\n@rate_limit\([^)]*\)\s*\ndef ' + name,
            )
        register = json.loads((APP_DIR / "tests" / "data" / "guest_endpoints.json").read_text(encoding="utf-8"))
        for name in ("send_signing_code", "verify_signing_code"):
            self.assertIn(f"erpnext_enhancements.project_enhancements.esign.portal.{name}", register)


# ---------------------------------------------------------------------------
# The page before the code
# ---------------------------------------------------------------------------


class PreviewBeforeTheCode(unittest.TestCase):
    CONTROLLER = APP_DIR / "www" / "contract_sign.py"

    def _context(self, request):
        """Run the real ``get_context`` and ``_state_for`` over stand-ins for what they call."""
        tree = ast.parse(self.CONTROLLER.read_text(encoding="utf-8"))
        wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("get_context", "_state_for")]
        namespace = {
            "frappe": sys.modules["frappe"],
            "contract_esign_public_page_enabled": lambda: True,
            "get_deploy_version": lambda: "1",
            "lifecycle": types.SimpleNamespace(
                resolve_request=lambda ref: request,
                request_expired=lambda r: False,
                is_signable=lambda r: True,
            ),
            "contract_style": types.SimpleNamespace(wrap=lambda html, ref: f"<header>PC</header>{html}<footer/>"),
            "email_code": email_code,
            "_contract_ref": lambda r: None,
            "_contract_css": lambda: "",
            "_safe_css": lambda css: css,
            "_boot": lambda r, state, ref: {},
        }
        exec(compile(ast.Module(body=wanted, type_ignores=[]), str(self.CONTROLLER), "exec"), namespace)
        return namespace["get_context"](_Dict())

    def test_the_agreement_shown_before_the_code_masks_the_signers_address(self):
        request = _Doc(
            status="Sent",
            signer_email=SIGNER,
            name="CSR-1",
            document_snapshot="<td>Email:</td><td>Jane.Doe@example.com</td>",
        )
        context = self._context(request)
        self.assertEqual(context.state, "signable")
        self.assertNotIn("jane.doe@example.com", context.agreement_html.lower())
        self.assertIn("j•••@example.com", context.agreement_html)

    def test_the_boot_hint_is_the_same_mask(self):
        source = self.CONTROLLER.read_text(encoding="utf-8")
        self.assertIn('"signer_email_hint": email_code.mask_email(request.signer_email)', source)
        self.assertNotIn("def _mask(", source)


# ---------------------------------------------------------------------------
# The evidence record
# ---------------------------------------------------------------------------


class EvidenceRecordsTheCode(unittest.TestCase):
    def _execute(self, evidence):
        request = _Doc(
            name="CSR-2026-00042",
            project_contract="PC-1",
            document_hash=None,
            document_snapshot="<p>x</p>",
            signer_email=SIGNER,
            provider_reference=None,
        )
        STATE["contract"] = _Doc(name="PC-1", render_body=lambda: "<p>executed</p>")
        lifecycle.execute_contract(request, evidence)
        return request

    def test_a_verified_code_and_its_time_are_stamped_on_the_request(self):
        request = self._execute(
            lifecycle.SignatureEvidence(signed_name="Jane", email_confirmed=1, email_code_verified_on="2026-11-19 10:05:00.000000")
        )
        self.assertEqual(request.email_code_verified, 1)
        self.assertEqual(request.email_code_verified_on, "2026-11-19 10:05:00.000000")
        self.assertEqual(request.email_confirmed, 1)
        self.assertEqual(request.status, "Signed")

    def test_no_code_is_recorded_as_none_when_none_was_asked_for(self):
        request = self._execute(lifecycle.SignatureEvidence(signed_name="Jane"))
        self.assertEqual(request.email_code_verified, 0)
        self.assertIsNone(request.email_code_verified_on)

    def test_the_doctype_carries_both_fields_read_only(self):
        doc = json.loads(
            (APP_DIR / "project_enhancements" / "doctype" / "contract_signature_request" / "contract_signature_request.json").read_text(
                encoding="utf-8"
            )
        )
        fields = {f["fieldname"]: f for f in doc["fields"] if "fieldname" in f}
        self.assertEqual(fields["email_code_verified"]["fieldtype"], "Check")
        self.assertEqual(fields["email_code_verified_on"]["fieldtype"], "Datetime")
        for name in ("email_code_verified", "email_code_verified_on"):
            self.assertTrue(fields[name].get("read_only"))
            self.assertIn(name, doc["field_order"])


if __name__ == "__main__":
    unittest.main()
