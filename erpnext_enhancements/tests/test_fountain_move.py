"""Pure-Python (no Frappe site) unit tests for the fountain-move intake feature.

Plain pytest functions, following ``test_stripe_payments`` / ``test_quickbooks_online``:
:func:`install_frappe_stub` puts a minimal fake ``frappe`` into ``sys.modules`` so
the modules import without a bench. These are the tests that actually run in CI —
the bench-backed conversion tests live in ``test_fountain_move_conversion.py`` and
need a site.

What is covered here is deliberately the logic that is (a) pure, and (b) the sort
of thing that fails silently in production: phone normalisation, the customer-name
rule, the guest field allowlist, the Turnstile decision table, honeypot semantics,
image sniffing, and URL building.

The import-smoke test at the top exists because the highest-severity bug found
during design review was an import-time one: ``frappe.rate_limit`` does not exist
(the decorator is ``frappe.rate_limiter.rate_limit``), which would have taken the
whole module down on first request rather than failing a test.
"""

import importlib
import sys
import types


def _stub_throw(message=None, *args, **kwargs):
	raise Exception(message if isinstance(message, str) else "frappe.throw")


def install_frappe_stub():
	"""Install a minimal fake ``frappe`` into sys.modules for import."""
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")

	def _flt(value=0, precision=None):
		try:
			number = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(number, precision) if precision is not None else number

	def _escape_html(text):
		mapping = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}
		return "".join(mapping.get(char, char) for char in str(text))

	frappe_utils.flt = _flt
	frappe_utils.cint = lambda value=0, *a, **k: int(_flt(value))
	frappe_utils.escape_html = _escape_html
	frappe_utils.now_datetime = lambda: None
	frappe_utils.nowdate = lambda: "2026-07-21"
	frappe_utils.today = lambda: "2026-07-21"
	frappe_utils.get_datetime = lambda value=None, *a, **k: value
	frappe_utils.add_to_date = lambda value=None, **k: value
	frappe_utils.add_days = lambda value=None, days=0: value
	frappe_utils.get_url = lambda path=None, *a, **k: f"https://erp.example.com{path or ''}"
	frappe_utils.get_url_to_form = lambda dt, dn: f"https://erp.example.com/app/{dt}/{dn}"
	frappe_utils.validate_email_address = lambda value, *a, **k: value
	frappe.utils = frappe_utils

	frappe.throw = _stub_throw
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.only_for = lambda roles, *a, **k: None
	frappe.generate_hash = lambda length=32, **k: "a" * length
	frappe.as_json = lambda value, **k: __import__("json").dumps(value, default=str)
	frappe.db = types.SimpleNamespace(
		get_value=lambda *a, **k: None,
		get_single_value=lambda *a, **k: None,
		exists=lambda *a, **k: None,
		has_column=lambda *a, **k: True,
		set_value=lambda *a, **k: None,
		sql=lambda *a, **k: [],
		commit=lambda: None,
	)
	frappe.session = types.SimpleNamespace(user="operator@example.com")
	frappe.local = types.SimpleNamespace(request_ip="203.0.113.7")
	frappe.request = None
	frappe.get_all = lambda *a, **k: []
	frappe.get_doc = lambda *a, **k: None
	frappe.get_cached_doc = lambda *a, **k: types.SimpleNamespace(get=lambda *a, **k: None)
	frappe.get_meta = lambda *a, **k: types.SimpleNamespace(has_field=lambda f: True)
	frappe.enqueue = lambda *a, **k: None
	frappe.sendmail = lambda *a, **k: None
	frappe.render_template = lambda *a, **k: ""
	frappe.new_doc = lambda *a, **k: None
	frappe.delete_doc = lambda *a, **k: None
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: ""
	frappe.get_roles = lambda *a, **k: []
	frappe.has_permission = lambda *a, **k: True
	frappe.clear_cache = lambda *a, **k: None
	frappe.defaults = types.SimpleNamespace(get_defaults=lambda: {})
	frappe.cache = types.SimpleNamespace(
		make_key=lambda key, **k: key,
		incrby=lambda key, amount=1: 1,
		expire=lambda key, ttl: None,
		get_value=lambda key, **k: None,
		set_value=lambda key, value, **k: None,
		delete_value=lambda key, **k: None,
	)
	frappe.PermissionError = type("PermissionError", (Exception,), {})
	frappe.ValidationError = type("ValidationError", (Exception,), {})
	frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
	frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
	frappe.NameError = type("NameError", (Exception,), {})

	sys.modules.setdefault("frappe", frappe)
	sys.modules.setdefault("frappe.utils", frappe_utils)

	# frappe.rate_limiter — the decorator the intake endpoints stack under
	# @frappe.whitelist(). NOTE: this lives at frappe.rate_limiter.rate_limit,
	# NOT frappe.rate_limit; a design pass assumed the latter and would have
	# crashed at import.
	limiter = sys.modules.get("frappe.rate_limiter") or types.ModuleType("frappe.rate_limiter")
	limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
	sys.modules["frappe.rate_limiter"] = limiter

	model = sys.modules.get("frappe.model") or types.ModuleType("frappe.model")
	document = sys.modules.get("frappe.model.document") or types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	sys.modules.setdefault("frappe.model", model)
	sys.modules.setdefault("frappe.model.document", document)

	sys.modules.setdefault("requests", types.ModuleType("requests"))
	return frappe


install_frappe_stub()

FM = "erpnext_enhancements.crm_enhancements.fountain_move"


# --- 1. import smoke --------------------------------------------------------


def test_every_module_imports():
	"""Catches import-time errors — wrong decorator paths, circular imports.

	`notify` importing MAX_CONVERSION_ATTEMPTS back from `conversion` (which
	imports `notify`) was a real circular import caught by exactly this test.
	"""
	for module in ("", ".intake", ".matching", ".conversion", ".photos", ".notify", ".invites", ".api"):
		assert importlib.import_module(f"{FM}{module}") is not None


def test_rate_limit_comes_from_rate_limiter():
	"""The decorator must be imported from frappe.rate_limiter, not frappe."""
	import frappe

	assert not hasattr(frappe, "rate_limit"), "frappe.rate_limit does not exist in v16"
	from frappe.rate_limiter import rate_limit

	assert callable(rate_limit)


# --- 2. phone normalisation -------------------------------------------------


def test_normalize_phone_handles_real_world_formats():
	from erpnext_enhancements.utils.phone import normalize_phone

	for raw in ("(801) 555-1212", "801-555-1212", "+1 801 555 1212", "18015551212", "801.555.1212"):
		assert normalize_phone(raw) == "8015551212", raw


def test_normalize_phone_short_and_empty():
	from erpnext_enhancements.utils.phone import normalize_phone

	assert normalize_phone("5551212") == "5551212"
	assert normalize_phone("") == ""
	assert normalize_phone(None) == ""


def test_is_nanp_gates_phone_matching():
	"""Phone matching must only trust a full 10-digit number."""
	from erpnext_enhancements.utils.phone import is_nanp

	assert is_nanp("(801) 555-1212")
	assert not is_nanp("5551212")
	assert not is_nanp("1234")


# --- 3. customer naming (the operator's rule) -------------------------------


def _request(**overrides):
	base = {
		"first_name": "Jane",
		"last_name": "Doe",
		"property_type": "Residential",
		"name": "FMR-2026-00001",
		"city": "Draper",
		"email": "jane@example.com",
		"phone": "(801) 555-1212",
		"address_line1": "12 Elm St",
		"address_line2": "",
		"pincode": "84020",
		"state": "UT",
		"purchase_location": "Cactus & Tropicals Draper",
		"purchase_location_address": "",
		"fountain_weight_lbs": 400,
		"water_access": 1,
		"electricity_access": 0,
		"contact_consent": 1,
		"terms_accepted": 1,
		"terms_accepted_on": "2026-07-21 10:00:00",
		"address_autocompleted": 1,
		"submitted_on": "2026-07-21 10:00:00",
	}
	base.update(overrides)
	obj = types.SimpleNamespace(**base)
	obj.get = lambda key, default=None: getattr(obj, key, default)
	return obj


def test_residential_customer_name_gets_residence_suffix():
	from erpnext_enhancements.crm_enhancements.fountain_move.conversion import build_customer_name

	assert build_customer_name(_request()) == "Jane Doe Residence"


def test_commercial_customer_name_is_just_the_person():
	from erpnext_enhancements.crm_enhancements.fountain_move.conversion import build_customer_name

	assert build_customer_name(_request(property_type="Commercial")) == "Jane Doe"


def test_customer_name_collapses_stray_whitespace():
	"""The QBO import taught us a double space becomes part of the docname and
	then matches nothing."""
	from erpnext_enhancements.crm_enhancements.fountain_move.conversion import build_customer_name

	name = build_customer_name(_request(first_name="  Jane ", last_name=" Doe  "))
	assert name == "Jane Doe Residence"
	assert "  " not in name


def test_customer_name_is_bounded():
	"""customer_name becomes the docname, so unbounded guest input here becomes
	unbounded input to validate_name."""
	from erpnext_enhancements.crm_enhancements.fountain_move.conversion import build_customer_name

	name = build_customer_name(_request(first_name="A" * 200, last_name="B" * 200))
	assert len(name) <= 130


def test_customer_name_survives_a_missing_last_name():
	from erpnext_enhancements.crm_enhancements.fountain_move.conversion import build_customer_name

	assert build_customer_name(_request(last_name="")) == "Jane Residence"


# --- 4. the guest field allowlist -------------------------------------------


def test_allowlist_excludes_every_privileged_field():
	"""read_only is a UI hint, not enforcement: under ignore_permissions frappe's
	higher-permlevel check returns early. The allowlist is the real boundary."""
	from erpnext_enhancements.crm_enhancements.fountain_move import INTAKE_FIELD_MAP

	forbidden = {
		"status",
		"owner",
		"docstatus",
		"name",
		"created_customer",
		"created_lead",
		"created_opportunity",
		"created_contact",
		"created_address",
		"turnstile_verdict",
		"honeypot_tripped",
		"spam_reason",
		"invite",
		"source_channel",
		"referral_partner",
		"conversion_attempts",
		"submission_fingerprint",
		"raw_payload",
	}
	assert forbidden.isdisjoint(INTAKE_FIELD_MAP.keys())


def test_allowlist_covers_every_field_the_form_asks_for():
	from erpnext_enhancements.crm_enhancements.fountain_move import INTAKE_FIELD_MAP

	required = {
		"first_name",
		"last_name",
		"email",
		"phone",
		"address_line1",
		"address_line2",
		"city",
		"state",
		"pincode",
		"property_type",
		"purchase_location",
		"fountain_weight_lbs",
		"water_access",
		"electricity_access",
		"contact_consent",
		"terms_accepted",
	}
	assert required.issubset(INTAKE_FIELD_MAP.keys())


# --- 5. input sanitation ----------------------------------------------------


def test_control_and_bidi_characters_are_rejected():
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import CONTROL_CHARS_RE

	assert CONTROL_CHARS_RE.search("Jane‮Doe")  # right-to-left override
	assert CONTROL_CHARS_RE.search("Jane⁦Doe")  # isolate
	assert CONTROL_CHARS_RE.search("Jane\x00Doe")  # NUL


def test_legitimate_customer_prose_survives():
	"""Real answers contain angle brackets and accents. Rejecting those would
	make the form unusable for exactly the details we most need."""
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import CONTROL_CHARS_RE

	for text in ("gate is < 3 ft wide", "path is > 20 ft", "Café Müller", "O'Brien-Smith"):
		assert not CONTROL_CHARS_RE.search(text), text


def test_lead_details_html_escapes_and_keeps_meaning():
	"""escape_html, not strip_html: strip_html leaves `<img src=x onerror=...>`
	as text-with-attributes AND destroys legitimate '< 3 ft' prose."""
	from erpnext_enhancements.crm_enhancements.fountain_move import conversion

	html = conversion.build_lead_details_html(_request(city="<script>alert(1)</script>"))
	assert "<script>" not in html
	assert "&lt;script&gt;" in html


def test_lead_details_html_reports_every_unmapped_field():
	from erpnext_enhancements.crm_enhancements.fountain_move import conversion

	html = conversion.build_lead_details_html(_request())
	for expected in ("Purchased at", "Fountain weight", "Water at destination", "Electricity"):
		assert expected in html
	assert "400" in html


def test_append_details_block_is_bounded():
	"""An anonymous submitter must not be able to grow a column on a
	pre-existing record without limit."""
	from erpnext_enhancements.crm_enhancements.fountain_move import conversion

	blob = ""
	for index in range(12):
		blob = conversion.append_details_block(blob, f"<p>block {index}</p>")
	assert blob.count("<hr>") <= 4
	assert len(blob) <= 40000


# --- 6. store locations -----------------------------------------------------


def test_store_locations_fall_back_when_settings_are_empty():
	"""An empty dropdown is an unsubmittable public form with no server-side
	symptom, so the built-in list is the floor."""
	from erpnext_enhancements.crm_enhancements.fountain_move import CT_LOCATIONS, get_store_locations

	locations = get_store_locations()
	assert len(locations) == len(CT_LOCATIONS)
	assert all(loc["location_name"] for loc in locations)


def test_the_three_cactus_and_tropicals_stores_are_present():
	from erpnext_enhancements.crm_enhancements.fountain_move import CT_LOCATIONS

	names = [loc["location_name"] for loc in CT_LOCATIONS]
	assert "Cactus & Tropicals Midvale" in names
	assert "Cactus & Tropicals Draper" in names
	assert "Cactus & Tropicals Salt Lake City" in names
	for loc in CT_LOCATIONS:
		assert ", UT " in loc["store_address"]


# --- 7. image sniffing ------------------------------------------------------


def test_magic_bytes_identify_real_images():
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import _sniff_image

	assert _sniff_image(b"\xff\xd8\xff\xe0" + b"\x00" * 32)[0] == "jpg"
	assert _sniff_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)[0] == "png"
	assert _sniff_image(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 32)[0] == "webp"


def test_svg_and_heic_and_riff_impostors_are_rejected():
	"""SVG is XML (an XML parser is a liability); HEIC cannot be decoded without
	pillow-heif; a bare RIFF container is not a WebP."""
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import _sniff_image

	for content in (
		b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
		b"\x00\x00\x00\x18ftypheic",
		b"RIFF\x00\x00\x00\x00AVI ",
		b"GIF89a",
		b"%PDF-1.4",
		b"MZ\x90\x00",
	):
		try:
			_sniff_image(content)
			raise AssertionError(f"should have rejected {content[:12]!r}")
		except Exception as exc:
			assert "JPEG" in str(exc) or "frappe.throw" in str(exc)


def test_pixel_cap_is_below_a_decompression_bomb():
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import MAX_TOTAL_PIXELS

	assert MAX_TOTAL_PIXELS < 12000 * 12000  # a classic bomb's dimensions
	assert MAX_TOTAL_PIXELS > 12 * 1000 * 1000  # but above any real phone camera


# --- 8. turnstile decision table --------------------------------------------


def _turnstile_response(status=200, payload=None):
	class Response:
		status_code = status

		def json(self):
			if payload is None:
				raise ValueError("no json")
			return payload

	return Response()


def _verify_with(monkeypatch_target, response=None, raises=None, secret="secret"):
	"""Run _verify_turnstile with requests.post stubbed."""
	import requests

	from erpnext_enhancements.crm_enhancements.fountain_move import intake

	def fake_post(*args, **kwargs):
		if raises:
			raise raises
		return response

	requests.post = fake_post
	intake._turnstile_secret = lambda: secret
	intake._hostname_allowed = lambda hostname: hostname in (None, "erp.example.com")
	intake._challenge_fresh = lambda ts: True
	return intake._verify_turnstile(monkeypatch_target)


def test_turnstile_pass():
	verdict, _detail = _verify_with(
		"tok",
		_turnstile_response(
			payload={"success": True, "action": "fountain-move-intake", "hostname": "erp.example.com"}
		),
	)
	assert verdict == "Passed"


def test_turnstile_rejects_a_token_minted_for_another_action_or_host():
	"""A valid token proves a human solved *a* widget; these assertions prove
	they solved OURS. Without them a token is replayable across sites."""
	verdict, _ = _verify_with(
		"tok",
		_turnstile_response(
			payload={"success": True, "action": "some-other-form", "hostname": "erp.example.com"}
		),
	)
	assert verdict == "Failed"

	verdict, _ = _verify_with(
		"tok",
		_turnstile_response(
			payload={"success": True, "action": "fountain-move-intake", "hostname": "evil.example"}
		),
	)
	assert verdict == "Failed"


def test_turnstile_outage_is_unavailable_not_passed():
	"""Fail-closed. An outage must cost a delay, not open the floodgates —
	but the submission is still kept, so a customer never loses their work."""
	verdict, _ = _verify_with("tok", _turnstile_response(status=503))
	assert verdict == "Unavailable"

	verdict, _ = _verify_with("tok", raises=Exception("connection refused"))
	assert verdict == "Unavailable"

	verdict, _ = _verify_with(
		"tok", _turnstile_response(payload={"success": False, "error-codes": ["internal-error"]})
	)
	assert verdict == "Unavailable"


def test_turnstile_client_error_is_failed():
	verdict, _ = _verify_with("tok", _turnstile_response(status=403))
	assert verdict == "Failed"


def test_malformed_token_is_rejected_without_an_outbound_call():
	from erpnext_enhancements.crm_enhancements.fountain_move import intake

	intake._turnstile_secret = lambda: "secret"
	called = []
	import requests

	requests.post = lambda *a, **k: called.append(1)

	for bad in ("", None, "tok en", "<script>", "x" * 3000):
		verdict, _ = intake._verify_turnstile(bad)
		assert verdict == "Failed", bad
	assert not called, "a malformed token must not cost an outbound request"


def test_unconfigured_turnstile_is_not_checked():
	from erpnext_enhancements.crm_enhancements.fountain_move import intake

	intake._turnstile_secret = lambda: None
	verdict, _ = intake._verify_turnstile("tok")
	assert verdict == "Not Checked"


def test_challenge_freshness_compares_in_utc():
	"""``challenge_ts`` is UTC (trailing Z); the first release compared it
	against site-local ``now_datetime()``, so on a UTC-6 site every legitimate
	solve measured six hours out and every verdict came back Failed. Surfaced
	only after the reserved-``sid`` fix, as "Please complete the verification
	check first." on every photo upload. Reloaded so the real function is
	tested even after the decision-table harness stubbed it out.
	"""
	from datetime import datetime, timedelta, timezone

	intake = importlib.reload(importlib.import_module(f"{FM}.intake"))

	def stamp(delta):
		return (datetime.now(timezone.utc) + delta).strftime("%Y-%m-%dT%H:%M:%S.000Z")

	# Solved moments ago — the exact case production rejected on a UTC-6 site.
	assert intake._challenge_fresh(stamp(timedelta(seconds=-45)))
	# Inside the window, both directions of clock skew.
	assert intake._challenge_fresh(stamp(timedelta(seconds=-280)))
	assert intake._challenge_fresh(stamp(timedelta(seconds=90)))
	# Genuinely stale or future tokens still fail — replay stays closed.
	assert not intake._challenge_fresh(stamp(timedelta(seconds=-330)))
	assert not intake._challenge_fresh(stamp(timedelta(hours=-6)))
	assert not intake._challenge_fresh(stamp(timedelta(hours=6)))
	# Absent/unparseable stays permissive: CF format drift is not an attack.
	assert intake._challenge_fresh(None)
	assert intake._challenge_fresh("")
	assert intake._challenge_fresh("not-a-timestamp")


# --- 9. honeypot semantics --------------------------------------------------


def test_honeypot_name_is_shared_between_page_and_endpoint():
	"""If the template and the check ever disagree the honeypot silently stops
	catching anything, and nothing looks wrong."""
	from erpnext_enhancements.crm_enhancements.fountain_move import HONEYPOT_FIELD_NAME

	assert HONEYPOT_FIELD_NAME
	template = _read("erpnext_enhancements/www/fountain-move.html")
	assert "boot.honeypot_field" in template, "template must render the name from the constant"


def test_timing_floor_is_plausible_for_a_human():
	from erpnext_enhancements.crm_enhancements.fountain_move.intake import MIN_FILL_SECONDS

	assert 2 <= MIN_FILL_SECONDS <= 10


# --- 9b. the contact phone number -------------------------------------------


def test_contact_phone_is_the_real_company_line():
	"""Regression guard. An earlier revision shipped a MADE-UP number in five
	customer-facing places — the no-JS notice, two error messages, the success
	screen and the invite email. A wrong number here is worse than none: it is
	handed to someone at the exact moment the form has already failed them."""
	from erpnext_enhancements.crm_enhancements.fountain_move import CONTACT_PHONE

	assert CONTACT_PHONE == "(801) 837-2199"


def test_customer_facing_copy_does_not_hardcode_a_number():
	"""The number must come from get_contact_phone()/FM_BOOT, not be re-typed.

	Scoped to the three surfaces a customer actually reads. Server-side modules
	are excluded on purpose: ``matching.py``'s docstring legitimately shows
	"(801) 555-1212" as an example of the phone FORMATS it has to normalise, and
	a guard that fires on documentation would just get deleted.

	Two literals are allowed: the constant itself, and the JS last-resort
	fallback for a boot payload that failed to render at all.
	"""
	import pathlib
	import re

	root = pathlib.Path(__file__).resolve().parents[1]
	phone_like = re.compile(r"\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}")

	surfaces = [
		root / "www" / "fountain-move.html",
		root / "public" / "js" / "fountain_move" / "fountain_move.js",
		root / "templates" / "emails" / "crm_enhancements" / "fountain_intake_invite.html",
		root / "crm_enhancements" / "fountain_move" / "__init__.py",
	]

	offenders = []
	for path in surfaces:
		if not path.exists():
			continue
		for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
			if not phone_like.search(line):
				continue
			allowed = (
				path.name == "__init__.py" and line.strip().startswith("CONTACT_PHONE =")
			) or (path.suffix == ".js" and "BOOT.contact_phone ||" in line)
			if not allowed:
				offenders.append(f"{path.name}:{number}: {line.strip()}")

	assert not offenders, "phone number hardcoded in customer-facing copy:\n" + "\n".join(offenders)


# --- 9c. CSRF ----------------------------------------------------------------


def test_page_emits_the_existing_session_token_only():
	"""Regression: the page shipped with NO csrf_token in its boot payload.

	Guests have no saved token so frappe skips CSRF for them — but a LOGGED-IN
	staff member previewing the page does have one, frappe then enforces it, and
	every endpoint returned HTTP 400 (CSRFTokenError). The form was broken
	outright for anyone signed in.
	"""
	import ast

	source = _read("erpnext_enhancements/www/fountain_move.py")
	tree = ast.parse(source)

	# The token must be in the BOOT DICT specifically — a substring check on the
	# file passes even when the boot entry is deleted, because the assignment
	# above it still mentions csrf_token.
	boot_keys = set()
	for node in ast.walk(tree):
		if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
			continue
		targets = [
			t.attr for t in node.targets if isinstance(t, ast.Attribute)
		]
		if "boot" not in targets:
			continue
		boot_keys = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}

	assert boot_keys, "could not find the context.boot dict"
	assert "csrf_token" in boot_keys, (
		f"context.boot must carry csrf_token so the client can send the header; got {sorted(boot_keys)}"
	)

	# Must read the EXISTING token, never mint one: get_csrf_token() creates a
	# token that Session.update never persists for Guest, so the guest would
	# send one the server has never stored.
	#
	# Checked via AST, not substring: the comments in that file legitimately
	# mention get_csrf_token() while explaining why it must not be used, and a
	# substring check would trip on its own documentation.
	called = {
		node.func.attr
		for node in ast.walk(tree)
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
	}
	assert "get_csrf_token" not in called, (
		"must not call frappe.sessions.get_csrf_token() — it mints a token that "
		"is never persisted for Guest"
	)


def test_client_sends_the_csrf_header_on_every_post():
	"""Both fetch call sites — JSON and multipart upload — must send it."""
	js = _read("erpnext_enhancements/public/js/fountain_move/fountain_move.js")

	assert "X-Frappe-CSRF-Token" in js, "client never sends the CSRF header"
	assert js.count("withCsrf(") >= 3, (
		"expected withCsrf() to be defined and used at both fetch call sites"
	)

	# The header must be conditional. An empty header is worse than none.
	assert "if (BOOT.csrf_token)" in js, "CSRF header must only be sent when non-empty"

	# The multipart upload must NOT set Content-Type — the browser has to pick
	# the boundary — but must still carry the token.
	start = js.index("function upload(")
	body = js[start : js.index("function preview(", start)]
	assert "withCsrf(" in body, "the photo upload does not send the CSRF header"
	# Match the object-key form, not the bare word — the comment above that line
	# legitimately mentions Content-Type while explaining why it is absent.
	assert '"Content-Type"' not in body, (
		"the multipart upload must not set Content-Type; the browser sets the boundary"
	)


# --- 10. invite URLs --------------------------------------------------------


def test_bare_intake_url_has_no_query_string():
	"""The bare URL is the canonical, permanent, QR-able form."""
	from erpnext_enhancements.crm_enhancements.fountain_move.invites import build_intake_url

	assert "?" not in build_intake_url()
	assert build_intake_url().endswith("/fountain-move")


def test_token_is_percent_encoded():
	from erpnext_enhancements.crm_enhancements.fountain_move.invites import build_intake_url

	url = build_intake_url("a b&c/d")
	assert "?ref=" in url
	assert " " not in url
	assert "&c" not in url.split("ref=")[1] or "%26" in url


def test_resolve_invite_never_raises():
	"""The public form calls this on its render path — an exception here means a
	customer sees an error page because someone forwarded a stale link."""
	from erpnext_enhancements.crm_enhancements.fountain_move.invites import resolve_invite

	for value in (None, "", "  ", "x" * 500, "not-a-token!", 12345, {"a": 1}):
		assert resolve_invite(value) is None


# --- 11. email validation ---------------------------------------------------


def test_multiple_recipients_are_rejected():
	"""validate_email_address is an extractor, not a predicate: it comma-splits
	and returns the valid parts joined, so a truthiness check would let
	'a@b.com, evil@x.com' become two recipients."""
	from erpnext_enhancements.crm_enhancements.fountain_move.invites import _valid_single_email

	for bad in ("a@b.com, c@d.com", "a@b.com;c@d.com", "a@b.com c@d.com", "", "   "):
		try:
			_valid_single_email(bad)
			raise AssertionError(f"should have rejected {bad!r}")
		except Exception as exc:
			assert "single" in str(exc).lower() or "valid" in str(exc).lower()


def test_html_in_a_personal_note_is_rejected():
	from erpnext_enhancements.crm_enhancements.fountain_move.invites import _valid_message

	try:
		_valid_message("<img src=x onerror=alert(1)>")
		raise AssertionError("should have rejected HTML")
	except Exception as exc:
		assert "HTML" in str(exc)

	assert _valid_message("See you Tuesday!") == "See you Tuesday!"
	assert len(_valid_message("x" * 900)) == 500


# --- 12. role gates ---------------------------------------------------------


def test_triage_excludes_sales_user():
	"""Retry re-runs a job that creates CRM masters; marking spam hides a real
	customer. Neither belongs to the read-mostly role."""
	from erpnext_enhancements.crm_enhancements.fountain_move.api import TRIAGE_ROLES

	assert "Sales User" not in TRIAGE_ROLES
	assert "System Manager" in TRIAGE_ROLES


def test_sms_roles_are_narrower_than_send_roles():
	from erpnext_enhancements.crm_enhancements.fountain_move.invites import INTAKE_ROLES, SMS_ROLES

	assert set(SMS_ROLES).issubset(set(INTAKE_ROLES))
	assert len(SMS_ROLES) < len(INTAKE_ROLES)


# --- 13. naming series ------------------------------------------------------


def test_request_autoname_uses_a_prefixed_dot_series():
	"""NOT `format:...{#####}`: frappe's _format_autoname parses each braced param
	separately, so {#####} resolves with an EMPTY prefix and every format:-named
	doctype on the site shares one global counter."""
	import json

	doc = json.loads(_read("erpnext_enhancements/crm_enhancements/doctype/fountain_move_request/fountain_move_request.json"))
	assert doc["autoname"] == "FMR-.YYYY.-.#####."
	assert not doc["autoname"].startswith("format:")
	assert doc["naming_rule"] == "Expression"


def test_guest_has_no_docperm_on_the_request():
	"""A Guest DocPerm would expose every submission through /api/resource."""
	import json

	doc = json.loads(_read("erpnext_enhancements/crm_enhancements/doctype/fountain_move_request/fountain_move_request.json"))
	roles = {perm["role"] for perm in doc["permissions"]}
	assert "Guest" not in roles
	assert "All" not in roles


# --- reserved request parameter names ---------------------------------------


def test_no_endpoint_parameter_is_named_sid():
	"""``sid`` is frappe's reserved login-session parameter: sessions.py
	(Session.__init__) pops it out of form_dict during auth, tries to resume a
	USER session with it, flags session_expired, and the endpoint receives None.
	Shipping the intake session id as ``sid`` made every submission "expire"
	instantly, for guests and staff alike. The wire name is ``intake_sid``, and
	nothing whitelisted here may ever take a parameter called ``sid`` again.
	"""
	import inspect

	intake = importlib.import_module(f"{FM}.intake")
	for endpoint in (intake.begin_intake, intake.upload_intake_photo, intake.submit_intake):
		fn = inspect.unwrap(endpoint)
		params = set(inspect.signature(fn).parameters)
		assert "sid" not in params, f"{endpoint.__name__} exposes reserved parameter 'sid'"

	assert "intake_sid" in inspect.signature(inspect.unwrap(intake.begin_intake)).parameters
	assert "intake_sid" in inspect.signature(inspect.unwrap(intake.upload_intake_photo)).parameters


def test_client_never_posts_a_key_named_sid():
	"""The JS must send intake_sid on every request — a body key "sid" (JSON,
	multipart or query) is intercepted by frappe's auth before arg binding."""
	js = _read("erpnext_enhancements/public/js/fountain_move/fountain_move.js")
	for banned in ('"sid":', "'sid':", 'append("sid"', "append('sid'", "sid=", "?sid"):
		assert banned not in js, f"client posts reserved key: {banned}"
	assert "intake_sid" in js


# --- removed photos must stay removed ---------------------------------------


def test_photos_present_parses_and_only_shrinks():
	intake = importlib.import_module(f"{FM}.intake")

	# Absent key: old-client behaviour, attach everything.
	assert intake._photos_present({}) is None
	assert intake._photos_present({"photos_present": None}) is None
	# Empty string is meaningful: attach none.
	assert intake._photos_present({"photos_present": ""}) == set()
	assert intake._photos_present({"photos_present": "fountain"}) == {"fountain"}
	assert intake._photos_present({"photos_present": "fountain,path"}) == {"fountain", "path"}
	assert intake._photos_present({"photos_present": " fountain , "}) == {"fountain"}
	assert intake._photos_present({"photos_present": ["path", "fountain"]}) == {"fountain", "path"}
	# Garbage kinds pass through harmlessly: they are only ever intersected
	# with the session's own record, never used to look anything up.
	assert intake._photos_present({"photos_present": 42}) is None


# --- helpers ----------------------------------------------------------------


def _read(relative_path):
	import pathlib

	root = pathlib.Path(__file__).resolve().parents[2]
	return (root / relative_path).read_text(encoding="utf-8")
