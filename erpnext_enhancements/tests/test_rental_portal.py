"""The customer portal and its sign-in (v1.565.0), bench-free.

The email-link guard is EXECUTED against a small ``frappe`` stub installed in ``setUpModule`` and
removed in ``tearDownModule`` (own CI step, so it cannot cross-talk with another suite's stub). The
rest reads the source.

Run: python -m unittest erpnext_enhancements.tests.test_rental_portal -v

The ones that matter most:

* :class:`TestEmailLinkGuard` — frappe's ``login_via_key`` signs in ANY account with no password,
  2FA or Google, and the setting is site-wide. A staff address must never be sent a link, and a
  staff sign-in through one must be refused, or anyone who can read a staff inbox is staff.
* :meth:`TestTheRollout.test_the_setting_never_ships_without_the_guards` — the patch that turns the
  setting on and the hooks that make it safe have to land together.
* :meth:`TestPortalWrites.test_a_customer_can_only_write_site_prep` — a portal user must never move
  dates, lines, status or money.
"""

import ast
import importlib
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
if str(REPO) not in sys.path:
	sys.path.insert(0, str(REPO))

_SAVED = {}
STUBBED = ("frappe", "frappe.utils", "frappe.www", "frappe.www.login")


class AuthError(Exception):
	pass


def _install_stub():
	for name in STUBBED:
		_SAVED[name] = sys.modules.get(name)
	frappe = types.ModuleType("frappe")
	frappe.AuthenticationError = AuthError
	frappe.PermissionError = PermissionError
	frappe._ = lambda text: text
	frappe.whitelisted = set()
	frappe.users = {}
	frappe.overrides = {}
	frappe.local = types.SimpleNamespace(request=None, form_dict={})
	frappe.session = types.SimpleNamespace(user="Guest")

	def whitelist(**_kw):
		def wrap(fn):
			frappe.whitelisted.add(fn)
			return fn

		return wrap

	def throw(message, exc=Exception):
		raise exc(message)

	class DB:
		def get_value(self, _doctype, name, fields, as_dict=False):
			row = frappe.users.get(name)
			return types.SimpleNamespace(**row) if row else None

	frappe.whitelist = whitelist
	frappe.throw = throw
	frappe.db = DB()
	frappe.override_whitelisted_method = lambda path: frappe.overrides.get(path, path)
	frappe.log_error = lambda **_kw: None

	utils = types.ModuleType("frappe.utils")
	utils.validate_email_address = lambda email: email if "@" in email else ""
	www = types.ModuleType("frappe.www")
	login = types.ModuleType("frappe.www.login")
	login.sent = []

	def send_login_link(email):
		login.sent.append(email)

	login.send_login_link = send_login_link
	frappe.whitelisted.add(send_login_link)
	frappe.get_attr = lambda path: login.send_login_link
	frappe.utils = utils
	for name, module in (("frappe", frappe), ("frappe.utils", utils), ("frappe.www", www), ("frappe.www.login", login)):
		sys.modules[name] = module
	return frappe, login


def setUpModule():
	global frappe_stub, login_stub, portal_login
	frappe_stub, login_stub = _install_stub()
	sys.modules.pop("erpnext_enhancements.portal_login", None)
	portal_login = importlib.import_module("erpnext_enhancements.portal_login")


def tearDownModule():
	sys.modules.pop("erpnext_enhancements.portal_login", None)
	for name, module in _SAVED.items():
		if module is None:
			sys.modules.pop(name, None)
		else:
			sys.modules[name] = module


class TestEmailLinkGuard(unittest.TestCase):
	def setUp(self):
		login_stub.sent.clear()
		frappe_stub.users = {
			"planner@example.com": {"user_type": "Website User", "enabled": 1},
			"staff@sapphirefountains.com": {"user_type": "System User", "enabled": 1},
			"gone@example.com": {"user_type": "Website User", "enabled": 0},
		}
		frappe_stub.local.request = None
		frappe_stub.local.form_dict = {}

	def test_a_customer_gets_a_link(self):
		portal_login.send_login_link(" planner@example.com ")
		self.assertEqual(login_stub.sent, ["planner@example.com"])

	def test_staff_administrator_unknown_and_disabled_get_nothing(self):
		for email in ("staff@sapphirefountains.com", "Administrator", "nobody@example.com", "gone@example.com", ""):
			portal_login.send_login_link(email)
		self.assertEqual(login_stub.sent, [])

	def test_a_staff_email_link_sign_in_is_refused(self):
		frappe_stub.local.request = types.SimpleNamespace(path="/api/method/frappe.www.login.login_via_key")
		manager = types.SimpleNamespace(user="staff@sapphirefountains.com")
		with self.assertRaises(AuthError):
			portal_login.refuse_staff_email_link_login(manager)

	def test_an_aliased_path_and_the_legacy_cmd_are_refused_too(self):
		manager = types.SimpleNamespace(user="Administrator")
		frappe_stub.local.request = types.SimpleNamespace(path="/api/method/frappe.www.login.frappe.www.login.login_via_key")
		with self.assertRaises(AuthError):
			portal_login.refuse_staff_email_link_login(manager)
		frappe_stub.local.request = types.SimpleNamespace(path="/")
		frappe_stub.local.form_dict = {"cmd": "frappe.www.login.login_via_key"}
		with self.assertRaises(AuthError):
			portal_login.refuse_staff_email_link_login(manager)

	def test_a_customer_email_link_sign_in_passes(self):
		frappe_stub.local.request = types.SimpleNamespace(path="/api/method/frappe.www.login.login_via_key")
		portal_login.refuse_staff_email_link_login(types.SimpleNamespace(user="planner@example.com"))

	def test_other_sign_ins_are_untouched(self):
		"""Google, API keys and impersonation go through on_login too; only the email link is this guard's."""
		frappe_stub.local.request = types.SimpleNamespace(path="/api/method/frappe.integrations.oauth2_logins.login_via_google")
		portal_login.refuse_staff_email_link_login(types.SimpleNamespace(user="staff@sapphirefountains.com"))
		frappe_stub.local.request = None
		portal_login.refuse_staff_email_link_login(types.SimpleNamespace(user="staff@sapphirefountains.com"))

	def test_the_original_is_sealed_while_overridden_and_restored_after(self):
		original = login_stub.send_login_link
		frappe_stub.overrides = {portal_login.ORIGINAL: "erpnext_enhancements.portal_login.send_login_link"}
		portal_login.seal_original()
		self.assertNotIn(original, frappe_stub.whitelisted)
		frappe_stub.overrides = {}
		portal_login.seal_original()
		self.assertIn(original, frappe_stub.whitelisted)


def _functions(path):
	return {n.name: n for n in ast.parse(path.read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}


class TestTheRollout(unittest.TestCase):
	def test_the_setting_never_ships_without_the_guards(self):
		hooks = (APP / "hooks.py").read_text(encoding="utf-8")
		patches = (APP / "patches.txt").read_text(encoding="utf-8")
		self.assertIn("erpnext_enhancements.patches.enable_login_with_email_link", patches)
		self.assertIn('"frappe.www.login.send_login_link": "erpnext_enhancements.portal_login.send_login_link"', hooks)
		self.assertIn('"erpnext_enhancements.portal_login.seal_original"', hooks)
		self.assertIn('"erpnext_enhancements.portal_login.refuse_staff_email_link_login"', hooks)

	def test_the_override_keeps_frappes_signature(self):
		fn = _functions(APP / "portal_login.py")["send_login_link"]
		self.assertEqual([a.arg for a in fn.args.args], ["email"])
		self.assertIn("frappe.whitelist(allow_guest=True, methods=['POST'])", [ast.unparse(d) for d in fn.decorator_list])

	def test_portal_menu(self):
		self.assertIn('{"title": "My Rentals", "route": "/rentals", "role": "Customer"}', (APP / "hooks.py").read_text(encoding="utf-8"))


class TestPortalWrites(unittest.TestCase):
	PORTAL = APP / "asset_management" / "rental_portal.py"

	def test_a_customer_can_only_write_site_prep(self):
		fn = _functions(self.PORTAL)["save_site_prep"]
		body = "\n".join(ast.unparse(s) for s in fn.body[1:])
		self.assertIn("own_booking(booking)", body)
		self.assertIn("for field in SITE_PREP_FIELDS", body)
		self.assertIn("frappe.db.set_value('Rental Booking', booking, clean", body)
		self.assertNotIn(".save(", body)
		fields = ast.literal_eval(
			next(
				n.value
				for n in ast.parse(self.PORTAL.read_text(encoding="utf-8")).body
				if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SITE_PREP_FIELDS"
			)
		)
		for forbidden in ("status", "delivery_datetime", "takedown_datetime", "fountains", "total_amount", "customer"):
			self.assertNotIn(forbidden, fields)

	def test_every_portal_action_is_post_login_and_ownership_checked(self):
		fns = _functions(self.PORTAL)
		for name in ("save_site_prep", "request_change"):
			with self.subTest(fn=name):
				decorators = [ast.unparse(d) for d in fns[name].decorator_list]
				self.assertIn("frappe.whitelist(methods=['POST'])", decorators)
				self.assertTrue(any(d.startswith("rate_limit(") for d in decorators))
				body = ast.unparse(fns[name])
				self.assertLess(body.index("_require_login()"), body.index("own_booking(booking)"))

	def test_not_yours_and_does_not_exist_look_the_same(self):
		body = ast.unparse(_functions(self.PORTAL)["own_booking"])
		self.assertEqual(body.count("frappe.throw("), 1)

	def test_the_page_sends_guests_to_sign_in(self):
		source = (APP / "www" / "rentals.py").read_text(encoding="utf-8")
		self.assertIn('frappe.session.user == "Guest"', source)
		self.assertIn("raise frappe.Redirect", source)


class TestPublicRequest(unittest.TestCase):
	REQUESTS = APP / "asset_management" / "rental_requests.py"

	def test_guest_post_rate_limited_and_gated(self):
		fn = _functions(self.REQUESTS)["submit_request"]
		decorators = [ast.unparse(d) for d in fn.decorator_list]
		self.assertIn("frappe.whitelist(allow_guest=True, methods=['POST'])", decorators)
		self.assertTrue(any(d.startswith("rate_limit(") for d in decorators))
		body = ast.unparse(fn)
		self.assertLess(body.index("if not enabled()"), body.index("_verify_turnstile("))
		# The honeypot answers before any outbound Turnstile call.
		self.assertLess(body.index("HONEYPOT_FIELD"), body.index("_verify_turnstile("))

	def test_the_payload_is_never_splatted(self):
		body = ast.unparse(_functions(self.REQUESTS)["submit_request"])
		self.assertNotIn("**payload}", body)
		self.assertIn("for key, target in LEAD_FIELDS.items()", body)

	def test_no_availability_is_exposed(self):
		"""Nik, 2026-09-29: the fleet's schedule is not public."""
		for path in (self.REQUESTS, APP / "www" / "rent_a_fountain.py", APP / "www" / "rent-a-fountain.html"):
			text = path.read_text(encoding="utf-8")
			for token in ("get_availability", "Asset Booking", "rental_availability"):
				self.assertNotIn(token, text, path.name)

	def test_the_page_is_off_until_enabled(self):
		source = (APP / "www" / "rent_a_fountain.py").read_text(encoding="utf-8")
		self.assertIn("raise frappe.DoesNotExistError", source)
		self.assertNotIn("get_csrf_token()", source.split('"""', 2)[2])


if __name__ == "__main__":
	unittest.main()
