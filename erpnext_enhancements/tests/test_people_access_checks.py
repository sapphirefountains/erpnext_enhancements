"""Access checks on the people-facing endpoints (v1.826.2).

Two halves.

**The per-user rate limit** (``utils/user_rate_limit.py``) runs for real against a small
``frappe`` stub: it counts per signed-in user rather than per client address, refuses the call
past its limit with ``RateLimitExceededError``, does nothing outside a web request (a scheduler
job calling the same function in-process is never throttled), lets the call through when the
counter cannot be read, and keeps the wrapped function's signature, which Frappe filters
request arguments against.

**The endpoints** are pinned at source level (``ast``), the style ``test_hr_progression`` and
``test_time_correction_requests`` already use for these modules, because each sits behind a
heavy import graph (twilio, ERPNext doctypes, FAC). Each assertion names the rule the endpoint
now enforces:

- tier reviews: an active Employee (or HR), names arriving as strings and checked before any
  lookup, and refusals that name nobody;
- confined-space permit transitions: a Maintenance Manager, HR Manager or System Manager, or
  an active Employee named on the permit as its entrant, attendant or supervisor;
- incident and hazard reports: an active Employee, and a per-user rate limit;
- telephony: gateway-only endpoints behind ``validate_webhook_secret`` (never ``allow_guest``),
  an active Employee for the desk phone tools, no shared softphone token for a non-System
  Manager, and no ``frappe.set_user`` in a request handler that is not a webhook;
- the SMS reply drafter: a System User who can read the Communication;
- the time kiosk: the employee from the session, and Project read for the project lookups;
- time correction requests: filed for yourself unless you may review them;
- the two training assistant tools: the Desk's row scoping.

``frappe`` is a local stub installed in ``setUpModule`` and removed in ``tearDownModule``, so
this suite has its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_people_access_checks -v
"""

import ast
import importlib
import inspect
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

MODULES = ("frappe", "erpnext_enhancements.utils.user_rate_limit")
_saved = {}
rl = None
frappe = None


class RateLimitExceededError(Exception):
	pass


class _Cache:
	def __init__(self):
		self.counts = {}
		self.expiries = {}
		self.broken = False
		self.drop_expires = 0
		self.expire_calls = []

	def make_key(self, key):
		return f"site1|{key}"

	def incrby(self, key, amount):
		if self.broken:
			raise ConnectionError("redis is down")
		self.counts[key] = self.counts.get(key, 0) + amount
		return self.counts[key]

	def expire(self, key, seconds):
		if self.drop_expires:
			self.drop_expires -= 1
			return
		self.expiries[key] = seconds
		self.expire_calls.append(key)

	def ttl(self, key):
		if key not in self.counts:
			return -2
		return self.expiries.get(key, -1)


def setUpModule():
	global rl, frappe
	for name in MODULES:
		_saved[name] = sys.modules.pop(name, None)

	frappe = types.ModuleType("frappe")
	frappe._ = lambda text, *a, **k: text
	frappe.RateLimitExceededError = RateLimitExceededError

	def throw(message=None, exc=Exception, *a, **k):
		raise exc(message)

	frappe.throw = throw
	frappe.session = types.SimpleNamespace(user="tech@example.com")
	frappe.local = types.SimpleNamespace(request=object())
	frappe.cache = _Cache()
	sys.modules["frappe"] = frappe
	rl = importlib.import_module("erpnext_enhancements.utils.user_rate_limit")


def tearDownModule():
	for name in MODULES:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


# ---------------------------------------------------------------------------
# helpers for the source-level half
# ---------------------------------------------------------------------------
def _tree(rel):
	return ast.parse((APP / rel).read_text(encoding="utf-8"))


def _fn(rel, name):
	for node in ast.walk(_tree(rel)):
		if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
			return node
	raise AssertionError(f"{rel} has no function {name}")


def _src(rel, name):
	return ast.unparse(_fn(rel, name))


def _decorators(rel, name):
	"""Decorator names, outermost first: ``frappe.whitelist`` / ``user_rate_limit`` / ..."""
	out = []
	for dec in _fn(rel, name).decorator_list:
		target = dec.func if isinstance(dec, ast.Call) else dec
		out.append(ast.unparse(target))
	return out


def _whitelist_kwargs(rel, name):
	for dec in _fn(rel, name).decorator_list:
		if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "frappe.whitelist":
			return {kw.arg for kw in dec.keywords}
	raise AssertionError(f"{name} is not whitelisted")


def _calls(rel, name):
	"""Names of everything ``name`` calls, in source order (``a.b.c`` for attributes)."""
	return [ast.unparse(n.func) for n in ast.walk(_fn(rel, name)) if isinstance(n, ast.Call)]


def _first_line_of(rel, name, needle):
	"""Line number of the first statement in ``name`` whose source contains ``needle``."""
	for stmt in ast.walk(_fn(rel, name)):
		if (
			isinstance(stmt, ast.stmt)
			and needle in ast.unparse(stmt)
			and not isinstance(stmt, (ast.FunctionDef, ast.If, ast.Try))
		):
			return stmt.lineno
	raise AssertionError(f"{needle!r} not found in {name}")


def _annotation(rel, name, arg):
	for a in _fn(rel, name).args.args:
		if a.arg == arg:
			return ast.unparse(a.annotation) if a.annotation else None
	raise AssertionError(f"{name} has no argument {arg}")


TIER = "hr_enhancements/tier_review.py"
PERMITS = "hr_enhancements/permits.py"
SAFETY = "hr_enhancements/safety.py"
HAZARDS = "hr_enhancements/hazards.py"
TELEPHONY = "api/telephony.py"
COMMUNICATION = "api/communication.py"
KIOSK = "api/time_kiosk.py"
TASKS = "tasks.py"
TCR = "workforce/doctype/time_correction_request/time_correction_request.py"
LEARNER = "assistant_tools/training_learner_record.py"
COMPLIANCE = "assistant_tools/training_compliance_status.py"


# ---------------------------------------------------------------------------
# The per-user rate limit, run for real
# ---------------------------------------------------------------------------
class TestUserRateLimit(unittest.TestCase):
	def setUp(self):
		frappe.cache = _Cache()
		frappe.session.user = "tech@example.com"
		frappe.local.request = object()

	def _endpoint(self, limit=3):
		@rl.user_rate_limit(limit=limit, seconds=600)
		def endpoint(project: str, task=None):
			return ("ran", project, task)

		return endpoint

	def test_a_counter_found_without_a_window_gets_one(self):
		endpoint = self._endpoint(limit=5)
		frappe.cache.drop_expires = 1
		endpoint("PRJ-1")
		(key,) = frappe.cache.counts
		self.assertEqual(frappe.cache.ttl(key), -1)
		endpoint("PRJ-1")
		self.assertEqual(frappe.cache.ttl(key), 600)
		endpoint("PRJ-1")
		self.assertEqual(frappe.cache.expire_calls, [key])

	def test_the_call_past_the_limit_is_refused(self):
		endpoint = self._endpoint(limit=3)
		for _ in range(3):
			self.assertEqual(endpoint("PRJ-1"), ("ran", "PRJ-1", None))
		with self.assertRaises(RateLimitExceededError):
			endpoint("PRJ-1")

	def test_the_counter_belongs_to_the_person(self):
		endpoint = self._endpoint(limit=1)
		endpoint("PRJ-1")
		frappe.session.user = "office@example.com"
		self.assertEqual(endpoint("PRJ-1")[0], "ran")
		keys = sorted(frappe.cache.counts)
		self.assertEqual(len(keys), 2)
		self.assertTrue(all(k.startswith("site1|ee_user_rate:") for k in keys))
		self.assertTrue(any(k.endswith(":tech@example.com") for k in keys))

	def test_the_window_is_set_once(self):
		endpoint = self._endpoint(limit=5)
		endpoint("PRJ-1")
		endpoint("PRJ-1")
		(key,) = frappe.cache.expiries
		self.assertEqual(frappe.cache.expiries[key], 600)
		self.assertEqual(frappe.cache.counts[key], 2)

	def test_each_endpoint_has_its_own_bucket(self):
		first, second = self._endpoint(limit=1), self._endpoint(limit=1)
		first("PRJ-1")
		# Same qualname here, so name the bucket the way two real endpoints differ.
		limited = rl.user_rate_limit(limit=1, bucket="other")(lambda: "ran")
		self.assertEqual(limited(), "ran")
		with self.assertRaises(RateLimitExceededError):
			second("PRJ-1")

	def test_nothing_is_counted_outside_a_request(self):
		frappe.local.request = None
		endpoint = self._endpoint(limit=1)
		for _ in range(5):
			endpoint("PRJ-1")
		self.assertEqual(frappe.cache.counts, {})

	def test_an_unreadable_counter_is_not_a_refusal(self):
		frappe.cache.broken = True
		endpoint = self._endpoint(limit=1)
		for _ in range(3):
			self.assertEqual(endpoint("PRJ-1")[0], "ran")

	def test_the_signature_frappe_reads_is_the_original(self):
		"""Frappe drops request keys the endpoint's signature does not name, and validates
		annotated types, both through ``inspect.signature`` (which follows ``__wrapped__``)."""
		endpoint = self._endpoint()
		self.assertEqual(list(inspect.signature(endpoint).parameters), ["project", "task"])
		self.assertEqual(inspect.signature(endpoint).parameters["project"].annotation, str)


# ---------------------------------------------------------------------------
# Tier reviews
# ---------------------------------------------------------------------------
class TestTierReviewChecks(unittest.TestCase):
	ENDPOINTS = {"open_review": "employee", "reviewers_for": "employee", "send_to_reviewer": "review"}

	def test_names_are_annotated_strings(self):
		self.assertEqual(_annotation(TIER, "open_review", "employee"), "str")
		self.assertEqual(_annotation(TIER, "reviewers_for", "employee"), "str")
		self.assertEqual(_annotation(TIER, "send_to_reviewer", "review"), "str")
		self.assertEqual(_annotation(TIER, "send_to_reviewer", "reviewer"), "str")

	def test_the_string_check_rejects_anything_else(self):
		body = _src(TIER, "_name_arg")
		self.assertIn("isinstance(value, str)", body)
		self.assertIn("frappe.throw", body)

	def test_staff_and_string_checks_come_before_any_lookup(self):
		for name in self.ENDPOINTS:
			with self.subTest(endpoint=name):
				calls = _calls(TIER, name)
				self.assertIn("_require_staff", calls)
				self.assertIn("_name_arg", calls)
				staff = _first_line_of(TIER, name, "_require_staff(")
				name_check = _first_line_of(TIER, name, "_name_arg(")
				lookup = min(
					_first_line_of(TIER, name, needle)
					for needle in ("frappe.db.get_value", "frappe.get_doc")
					if needle in _src(TIER, name)
				)
				self.assertLess(staff, lookup)
				self.assertLess(name_check, lookup)

	def test_staff_means_an_active_employee_or_hr(self):
		body = _src(TIER, "_require_staff")
		self.assertIn("'status': 'Active'", body)
		self.assertIn("DECIDER_ROLES", body)
		self.assertIn("PermissionError", body)

	def test_refusals_name_nobody(self):
		"""No looked-up name or email is formatted into a permission refusal, so the
		wording is the same whether or not a record matched."""
		for name in ("open_review", "send_to_reviewer"):
			for node in ast.walk(_fn(TIER, name)):
				if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "frappe.throw"):
					continue
				if "PermissionError" not in ast.unparse(node):
					continue
				text = ast.unparse(node.args[0])
				with self.subTest(endpoint=name, refusal=text[:60]):
					self.assertNotIn("employee_name", text)
					self.assertNotIn("reviewer_user", text)
					self.assertNotIn("get_value", text)


# ---------------------------------------------------------------------------
# Confined-space permits
# ---------------------------------------------------------------------------
class TestPermitChecks(unittest.TestCase):
	def test_every_transition_goes_through_the_check(self):
		for name in ("open_permit", "close_permit", "cancel_permit"):
			with self.subTest(endpoint=name):
				self.assertIn("_permit_for_change", _calls(PERMITS, name))
				self.assertNotIn("frappe.get_doc", _calls(PERMITS, name))
				self.assertEqual(_annotation(PERMITS, name, "permit"), "str")

	def test_the_check_is_a_role_or_a_person_named_on_the_permit(self):
		body = _src(PERMITS, "_permit_for_change")
		self.assertIn("'status': 'Active'", body)
		self.assertIn("CHANGE_ROLES", body)
		for field in ("entrant", "attendant", "supervisor"):
			self.assertIn(f"doc.get('{field}')", body)
		self.assertIn("isinstance(permit, str)", body)
		self.assertNotIn("frappe.has_permission", body)
		# The role or employment is checked before the permit is even loaded.
		self.assertLess(body.index("'status': 'Active'"), body.index("frappe.get_doc"))

	def _check(self, roles=(), employee=None, permit=None):
		"""``_permit_for_change`` run against a fake ``frappe``: the caller's roles, their
		active Employee (or None) and the permit's three people."""

		class Refused(Exception):
			pass

		loaded = []
		permit = permit or {"entrant": "EMP-1", "attendant": "EMP-2", "supervisor": "EMP-3"}

		def throw(message, exc=Exception, **kwargs):
			raise Refused(message) if exc is fake.PermissionError else exc(message)

		def get_doc(doctype, name):
			loaded.append(name)
			return dict(permit)

		fake = types.SimpleNamespace(
			PermissionError=type("PermissionError", (Exception,), {}),
			ValidationError=ValueError,
			throw=throw,
			get_roles=lambda user=None: list(roles),
			get_doc=get_doc,
			db=types.SimpleNamespace(get_value=lambda *a, **k: employee),
		)
		namespace = {
			"frappe": fake,
			"_": lambda text: text,
			"DOCTYPE": "Confined Space Entry Permit",
			"CHANGE_ROLES": ("Maintenance Manager", "HR Manager", "System Manager"),
		}
		module = ast.fix_missing_locations(
			ast.Module(body=[_fn(PERMITS, "_permit_for_change")], type_ignores=[])
		)
		exec(compile(module, PERMITS, "exec"), namespace)
		return namespace["_permit_for_change"], Refused, loaded

	def test_each_person_named_on_the_permit_may_change_it(self):
		for employee in ("EMP-1", "EMP-2", "EMP-3"):
			with self.subTest(employee=employee):
				check, _refused, loaded = self._check(roles=["Employee"], employee=employee)
				self.assertEqual(check("CSEP-0001", "tech@example.com")["entrant"], "EMP-1")
				self.assertEqual(loaded, ["CSEP-0001"])

	def test_a_manager_role_may_change_any_permit_without_being_named(self):
		for role in ("Maintenance Manager", "HR Manager", "System Manager"):
			with self.subTest(role=role):
				check, _refused, _loaded = self._check(roles=[role], employee=None)
				self.assertTrue(check("CSEP-0001", "lead@example.com"))

	def test_staff_not_named_on_the_permit_are_refused(self):
		check, refused, loaded = self._check(roles=["Employee"], employee="EMP-9")
		with self.assertRaises(refused):
			check("CSEP-0001", "other@example.com")
		self.assertEqual(loaded, ["CSEP-0001"])

	def test_a_login_with_no_role_and_no_employee_never_loads_the_permit(self):
		check, refused, loaded = self._check(roles=[], employee=None)
		with self.assertRaises(refused):
			check("CSEP-0001", "portal@example.com")
		self.assertEqual(loaded, [])

	def test_the_permit_name_must_be_text(self):
		check, _refused, loaded = self._check(roles=["System Manager"])
		for bad in ({"name": ["like", "%"]}, "", None):
			with self.subTest(bad=bad), self.assertRaises(ValueError):
				check(bad, "lead@example.com")
		self.assertEqual(loaded, [])


# ---------------------------------------------------------------------------
# Incident and hazard reports
# ---------------------------------------------------------------------------
class TestSafetyReportChecks(unittest.TestCase):
	def test_report_incident(self):
		self.assertEqual(_decorators(SAFETY, "report_incident")[:2], ["frappe.whitelist", "user_rate_limit"])
		self.assertIn("_require_staff", _calls(SAFETY, "report_incident"))
		self.assertIn("'status': 'Active'", _src(SAFETY, "_require_staff"))

	def test_report_hazard(self):
		self.assertEqual(_decorators(HAZARDS, "report_hazard")[:2], ["frappe.whitelist", "user_rate_limit"])
		self.assertIn("_require_staff", _calls(HAZARDS, "report_hazard"))
		self.assertIn("'status': 'Active'", _src(HAZARDS, "_is_staff"))

	def test_the_brief_is_empty_for_non_staff_and_never_raises(self):
		body = _src(HAZARDS, "safety_brief")
		self.assertIn("_is_staff(me)", body)
		self.assertNotIn("frappe.throw", body)


# ---------------------------------------------------------------------------
# Telephony
# ---------------------------------------------------------------------------
class TestTelephonyChecks(unittest.TestCase):
	GATEWAY_ONLY = ("get_employee_number", "log_call_transcript", "send_voicemail_email")

	def test_gateway_only_endpoints_need_an_api_key_and_are_not_guest_endpoints(self):
		for name in self.GATEWAY_ONLY:
			with self.subTest(endpoint=name):
				decorators = _decorators(TELEPHONY, name)
				self.assertEqual(decorators[:2], ["frappe.whitelist", "validate_webhook_secret"])
				self.assertNotIn("allow_guest", _whitelist_kwargs(TELEPHONY, name))

	def test_no_desk_reachable_handler_switches_user(self):
		"""``frappe.set_user`` belongs only in a webhook handler, after its own
		authentication decorator; ``log_call_transcript`` sets owner and sender instead."""
		self.assertNotIn("frappe.set_user", _calls(TELEPHONY, "log_call_transcript"))
		src = _src(TELEPHONY, "log_call_transcript")
		# frappe stamps owner with the session user on insert, so it is set after the insert.
		hand_over = "comm.db_set('owner', 'triton@sapphirefountains.com', update_modified=False)"
		self.assertIn(hand_over, src)
		self.assertLess(src.index("comm.insert(ignore_permissions=True)"), src.index(hand_over))
		for node in ast.walk(_tree(TELEPHONY)):
			if not isinstance(node, ast.FunctionDef):
				continue
			if "frappe.set_user" not in [
				ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)
			]:
				continue
			decorators = [ast.unparse(d.func if isinstance(d, ast.Call) else d) for d in node.decorator_list]
			if "frappe.whitelist" not in decorators:
				continue
			with self.subTest(handler=node.name):
				self.assertTrue(
					{"validate_webhook_secret", "validate_twilio_request"} & set(decorators),
					f"{node.name} switches user without a webhook authentication decorator",
				)

	def test_the_name_lookup_escapes_like_wildcards(self):
		src = _src(TELEPHONY, "get_employee_number")
		self.assertIn("replace('%', '\\\\%')", src)
		self.assertIn("replace('_', '\\\\_')", src)
		self.assertIn("f'%{escaped}%'", src)

	def test_desk_phone_tools_need_an_active_employee(self):
		for name in ("trigger_outbound_call", "log_call_details"):
			with self.subTest(endpoint=name):
				self.assertIn("_require_active_employee", _calls(TELEPHONY, name))
		self.assertIn("'status': 'Active'", _src(TELEPHONY, "_active_employee"))
		# The number rung first comes from that Employee; no User-only fallback for a
		# login without one, because such a login was refused first.
		src = _src(TELEPHONY, "trigger_outbound_call")
		self.assertLess(src.index("_require_active_employee()"), src.index("'User', user"))
		self.assertNotIn("frappe.get_all('Employee'", src)
		self.assertEqual(_annotation(TELEPHONY, "trigger_outbound_call", "docname"), "str")

	def test_a_logged_call_attaches_only_to_a_readable_customer_contact_or_lead(self):
		src = _src(TELEPHONY, "log_call_details")
		self.assertIn("CALL_REFERENCE_DOCTYPES", src)
		self.assertIn("frappe.has_permission(reference_doctype, 'read'", src)
		self.assertNotIn("comm.reference_doctype", src)
		module = ast.unparse(_tree(TELEPHONY))
		self.assertIn("CALL_REFERENCE_DOCTYPES = ('Customer', 'Contact', 'Lead')", module)

	def test_no_shared_softphone_token_for_a_non_system_manager(self):
		src = _src(TELEPHONY, "get_softphone_token")
		self.assertIn("_active_employee()", src)
		self.assertIn("elif is_system_manager:", src)
		# The legacy identity is reached only in the System Manager branch.
		self.assertEqual(src.count("LEGACY_SOFTPHONE_IDENTITY"), 1)
		self.assertLess(src.index("elif is_system_manager:"), src.index("LEGACY_SOFTPHONE_IDENTITY"))
		self.assertNotIn("frappe.throw(_('Only", src)

	def test_outbound_tools_are_rate_limited_per_user(self):
		for name in ("trigger_outbound_call", "send_sms", "send_voicemail_email"):
			with self.subTest(endpoint=name):
				self.assertIn("user_rate_limit", _decorators(TELEPHONY, name))


# ---------------------------------------------------------------------------
# The SMS reply drafter
# ---------------------------------------------------------------------------
class TestSoftphoneTokenBehavior(unittest.TestCase):
	"""``get_softphone_token`` run against fakes: who gets a token and who gets None."""

	LISTED = "desk@example.com"

	def _token(self, user, roles=(), employee=None, listed=None):
		read_settings = []

		class Settings:
			twilio_api_key_sid = "SK-fake"
			twilio_twiml_app_sid = "AP-fake"
			twilio_account_sid = "AC-fake"

			def get_password(self, field, raise_exception=True):
				return "not-a-real-value"

		def get_doc(doctype):
			read_settings.append(doctype)
			return Settings()

		class AccessToken:
			def __init__(self, account_sid, key_sid, key_value, identity=None):
				self.identity = identity

			def add_grant(self, grant):
				self.grant = grant

			def to_jwt(self):
				return f"jwt-for-{self.identity}"

		fake = types.SimpleNamespace(
			session=types.SimpleNamespace(user=user),
			get_roles=lambda u=None: list(roles),
			get_doc=get_doc,
			conf={},
			throw=lambda message, *a, **k: (_ for _ in ()).throw(RuntimeError(message)),
		)
		namespace = {
			"frappe": fake,
			"os": types.SimpleNamespace(environ={}),
			"AccessToken": AccessToken,
			"VoiceGrant": lambda **kwargs: kwargs,
			"LEGACY_SOFTPHONE_IDENTITY": "shared_identity",
			"_active_employee": lambda u=None: employee,
			"_softphone_users": lambda settings: list(self.LISTED.split())
			if listed is None
			else list(listed),
			"_softphone_identity": lambda u: "erpnext_" + u.replace("@", "_").replace(".", "_"),
		}
		node = _fn(TELEPHONY, "get_softphone_token")
		node.decorator_list = []
		module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
		exec(compile(module, TELEPHONY, "exec"), namespace)
		return namespace["get_softphone_token"](), read_settings

	def test_a_listed_active_employee_gets_their_own_token(self):
		token, _read = self._token(self.LISTED, roles=["Employee"], employee={"name": "EMP-1"})
		self.assertEqual(token, "jwt-for-erpnext_desk_example_com")

	def test_an_account_that_is_not_staff_gets_none_and_reads_no_settings(self):
		token, read = self._token("portal@example.com", roles=[], employee=None)
		self.assertIsNone(token)
		self.assertEqual(read, [])

	def test_an_active_employee_who_is_not_listed_gets_none(self):
		token, _read = self._token("tech@example.com", roles=["Employee"], employee={"name": "EMP-2"})
		self.assertIsNone(token)

	def test_with_no_list_only_a_system_manager_gets_the_shared_identity(self):
		token, _read = self._token(
			"tech@example.com", roles=["Employee"], employee={"name": "EMP-2"}, listed=[]
		)
		self.assertIsNone(token)
		token, _read = self._token("admin@example.com", roles=["System Manager"], employee=None, listed=[])
		self.assertEqual(token, "jwt-for-shared_identity")


class TestSmsReplyChecks(unittest.TestCase):
	def test_system_user_read_permission_and_rate_limit(self):
		self.assertEqual(
			_decorators(COMMUNICATION, "suggest_sms_reply")[:2], ["frappe.whitelist", "user_rate_limit"]
		)
		src = _src(COMMUNICATION, "suggest_sms_reply")
		self.assertIn("is_system_user(frappe.session.user)", src)
		self.assertIn("inbound_sms.check_permission('read')", src)
		# Read permission is checked before the model is called.
		self.assertLess(src.index("check_permission('read')"), src.index("generate_content_with_vertex_ai("))


# ---------------------------------------------------------------------------
# The time kiosk
# ---------------------------------------------------------------------------
class TestKioskChecks(unittest.TestCase):
	def test_the_legacy_point_takes_the_employee_from_the_session(self):
		src = _src(KIOSK, "log_geolocation")
		self.assertIn("_session_employee()", src)
		self.assertIn("_is_manager()", src)
		self.assertIn("employee = session_emp", src)
		# Resolved before the try block, so a refusal is a refusal rather than an Error Log row.
		self.assertLess(src.index("_session_employee()"), src.index("try:"))

	def test_project_lookups_need_a_session_employee_and_project_read(self):
		for name in ("get_tasks_for_project", "get_maintenance_context"):
			with self.subTest(endpoint=name):
				calls = _calls(KIOSK, name)
				self.assertIn("_require_session_employee", calls)
				self.assertIn("_require_project_read", calls)
		body = _src(KIOSK, "_require_project_read")
		self.assertIn("frappe.has_permission('Project', 'read'", body)
		self.assertIn("isinstance(project, str)", body)

	def test_visit_lookups_answer_only_staff(self):
		for name in ("get_my_visits_today", "get_nearby_visit"):
			with self.subTest(endpoint=name):
				self.assertIn("_session_employee", _calls(KIOSK, name))

	def test_the_scheduler_uses_the_unchecked_helper(self):
		"""``nudge_unsubmitted_maintenance_forms`` runs as each employee in turn; one whose
		project they cannot open must not stop the loop."""
		self.assertNotIn("frappe.whitelist", _decorators(KIOSK, "maintenance_context"))
		tasks = (APP / TASKS).read_text(encoding="utf-8")
		self.assertIn("from erpnext_enhancements.api.time_kiosk import maintenance_context", tasks)
		self.assertNotIn("get_maintenance_context", tasks)


# ---------------------------------------------------------------------------
# Time correction requests
# ---------------------------------------------------------------------------
class TestCorrectionRequestFiler(unittest.TestCase):
	def test_a_new_request_is_for_yourself_unless_you_review(self):
		self.assertIn("self._check_filer", _calls(TCR, "validate"))
		body = _src(TCR, "_check_filer")
		self.assertIn("self.is_new()", body)
		self.assertIn("permissions.can_review(self.employee, user)", body)
		self.assertIn("permissions._session_employee(user)", body)
		self.assertIn("PermissionError", body)


# ---------------------------------------------------------------------------
# Training assistant tools
# ---------------------------------------------------------------------------
class TestTrainingToolScoping(unittest.TestCase):
	def test_learner_record_checks_before_answering(self):
		execute = _src(LEARNER, "execute")
		self.assertIn("self._may_read(user)", execute)
		self.assertLess(execute.index("self._may_read(user)"), execute.index("'learner':"))
		body = _src(LEARNER, "_may_read")
		self.assertIn("permissions._is_unscoped(me)", body)
		self.assertIn("permissions._own_row", body)

	def test_compliance_status_scopes_every_list(self):
		execute = _src(COMPLIANCE, "execute")
		self.assertIn("self._visible_users()", execute)
		self.assertIn("filters['user'] = ['in', visible]", execute)
		self.assertIn("self._by_course(filters.get('course'), visible)", execute)
		self.assertIn("cert_filters['user'] = filters['user']", _src(COMPLIANCE, "_expiring"))
		self.assertIn("filters['user'] = ['in', visible]", _src(COMPLIANCE, "_by_course"))
		body = _src(COMPLIANCE, "_visible_users")
		self.assertIn("permissions._is_unscoped(me)", body)
		self.assertIn("permissions._direct_report_users(me)", body)


if __name__ == "__main__":
	unittest.main()
