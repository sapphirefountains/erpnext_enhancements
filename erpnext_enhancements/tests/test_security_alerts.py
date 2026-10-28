"""Bench-free unit tests for the Administrator login alert (security_alerts.py).

Stubs a minimal ``frappe`` (no site/bench) so the pure decision logic runs under
plain unittest. The stub is installed in ``setUpModule`` (execution time), not at
import, so it never fools the bench-only suites' ``import frappe`` skip-guards.

Run: python -m unittest erpnext_enhancements.tests.test_security_alerts
"""

import sys
import types
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Mutable state the frappe stub reads at call time.
STATE = {"conf": {}, "cache": {}, "flags": {}, "settings": {}, "singles": {}, "covered": set()}
security_alerts = None


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")

    frappe.conf = STATE["conf"]
    frappe.flags = types.SimpleNamespace(
        in_migrate=False, in_install=False, in_patch=False, in_import=False
    )
    frappe.local = types.SimpleNamespace(request=None)
    frappe.log_error = lambda *args, **kwargs: None
    frappe.sendmail = lambda **kwargs: None
    frappe.enqueue = lambda *args, **kwargs: None
    frappe.get_request_header = lambda name: None
    frappe.get_all = lambda *args, **kwargs: []

    class _Cache:
        def get_value(self, key):
            return STATE["cache"].get(key)

        def set_value(self, key, value, expires_in_sec=None):
            STATE["cache"][key] = value

    frappe.cache = _Cache()

    # security_alerts builds its body with email_style, which renders the shared
    # macros through frappe's template loader. Give the stub a real jinja
    # environment rooted at the repo rather than mocking email_style out: the
    # escaping assertions below are only worth anything if the real macros run.
    _jenv = Environment(loader=FileSystemLoader(str(REPO_ROOT)), autoescape=False)
    frappe.get_template = _jenv.get_template
    frappe.render_template = lambda path, ctx=None: _jenv.get_template(path).render(**(ctx or {}))

    utils = types.ModuleType("frappe.utils")
    utils.get_url = lambda path="": "https://erp.example.com" + (path or "")
    utils.escape_html = lambda s: (
        str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    utils.cint = lambda v: int(v or 0)
    frappe.utils = utils

    # The two-factor coverage check reads System Settings and asks frappe's own
    # per-user rule; both answer from STATE so each test sets the site it needs.
    frappe.get_system_settings = lambda key: STATE["settings"].get(key)
    frappe.db = types.SimpleNamespace(
        get_single_value=lambda doctype, field: STATE["singles"].get((doctype, field))
    )
    twofactor = types.ModuleType("frappe.twofactor")
    twofactor.two_factor_is_enabled_for_ = lambda user: user in STATE["covered"]
    frappe.twofactor = twofactor

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.twofactor"] = twofactor


def setUpModule():
    _install_frappe_stub()
    global security_alerts
    from erpnext_enhancements import security_alerts as module

    security_alerts = module


class TestWatchedLogin(unittest.TestCase):
    def test_administrator_login_is_watched(self):
        self.assertTrue(security_alerts.is_watched_login("Login", "Administrator"))

    def test_other_users_are_ignored(self):
        # The whole point is a low-noise alert; ordinary staff logins must not fire.
        self.assertFalse(security_alerts.is_watched_login("Login", "nikolas@example.com"))

    def test_non_login_operations_are_ignored(self):
        # Activity Log carries far more than authentication rows.
        self.assertFalse(security_alerts.is_watched_login("Logout", "Administrator"))
        self.assertFalse(security_alerts.is_watched_login(None, "Administrator"))

    def test_failure_detection_is_case_and_space_tolerant(self):
        self.assertTrue(security_alerts.is_failure("Failed"))
        self.assertTrue(security_alerts.is_failure(" failed "))
        self.assertFalse(security_alerts.is_failure("Success"))
        self.assertFalse(security_alerts.is_failure(None))


class TestFormatAlert(unittest.TestCase):
    def test_success_subject_and_remediation_note(self):
        subject, body = security_alerts.format_alert(
            "Success", "203.0.113.9", "curl/8.0", "Administrator logged in",
            "2026-08-02 06:54:00", "https://erp.example.com",
        )
        self.assertIn("successful login", subject)
        self.assertIn("203.0.113.9", body)
        # A success on a break-glass account is the alarming case, so the mail
        # has to say what to do about it.
        self.assertIn("rotate the Administrator password", body)

    def test_failure_subject_is_distinguishable_and_omits_remediation(self):
        subject, body = security_alerts.format_alert(
            "Failed", "203.0.113.9", None, None, "2026-08-02 06:54:00", None,
        )
        self.assertIn("FAILED", subject)
        self.assertIn("Failed", body)
        self.assertNotIn("rotate the Administrator password", body)

    def test_missing_fields_render_as_unknown_not_none(self):
        _, body = security_alerts.format_alert("Failed", None, None, None, None, None)
        self.assertIn("unknown", body)
        self.assertNotIn("None", body)

    def test_user_agent_is_escaped(self):
        # The user agent is attacker-controlled and lands in an HTML email.
        _, body = security_alerts.format_alert(
            "Failed", "1.2.3.4", "<script>alert(1)</script>", None, "t", "u",
        )
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)


class TestThrottle(unittest.TestCase):
    def setUp(self):
        STATE["cache"].clear()

    def test_failures_are_throttled_after_the_first(self):
        # A scanner sent ~30 failures in 24 seconds on 2026-08-02; one email is
        # the signal, thirty is a self-inflicted flood.
        self.assertTrue(security_alerts._should_send_now(failed=True))
        self.assertFalse(security_alerts._should_send_now(failed=True))
        self.assertFalse(security_alerts._should_send_now(failed=True))

    def test_successes_are_never_throttled(self):
        for _ in range(3):
            self.assertTrue(security_alerts._should_send_now(failed=False))


class TestRecipients(unittest.TestCase):
    def setUp(self):
        STATE["conf"].clear()

    def test_comma_separated_string_is_split(self):
        STATE["conf"]["admin_alert_recipients"] = "a@example.com, b@example.com"
        self.assertEqual(
            security_alerts._recipients(), ["a@example.com", "b@example.com"]
        )

    def test_list_is_passed_through_and_blanks_dropped(self):
        STATE["conf"]["admin_alert_recipients"] = ["a@example.com", "", None]
        self.assertEqual(security_alerts._recipients(), ["a@example.com"])

    def test_falls_back_when_unset(self):
        # No site_config entry and no System Managers -> empty, which the caller
        # logs rather than silently dropping the alert.
        self.assertEqual(security_alerts._recipients(), [])


class TestHookIsNonFatal(unittest.TestCase):
    def test_exceptions_never_escape_into_the_login_request(self):
        # This hook runs inside login. A raise here would lock everyone out of
        # the account used to fix lockouts.
        class Exploding:
            @property
            def operation(self):
                raise RuntimeError("boom")

        security_alerts.notify_administrator_login(Exploding())

    def test_maintenance_context_is_skipped(self):
        import frappe

        frappe.flags.in_migrate = True
        try:
            sent = []
            original = security_alerts._should_send_now
            security_alerts._should_send_now = lambda failed: sent.append(failed) or True
            doc = types.SimpleNamespace(
                operation="Login", user="Administrator", status="Success"
            )
            security_alerts.notify_administrator_login(doc)
            self.assertEqual(sent, [])
        finally:
            security_alerts._should_send_now = original
            frappe.flags.in_migrate = False


class TestTwoFactorCoverage(unittest.TestCase):
    """The daily backstop for staff sign-in (v1.755.0).

    frappe asks for a second factor per role, so a staff account holding no
    2FA role signs in without one by every route that reaches 2FA at all. The
    check must report that account, report 2FA switched off site-wide, stay
    quiet on a healthy site, and never raise from the scheduler.
    """

    USERS = ["admin@example.com", "new.hire@example.com", "tech@example.com"]

    def setUp(self):
        import frappe

        self.frappe = frappe
        self.saved = {
            name: getattr(frappe, name) for name in ("get_all", "sendmail", "log_error")
        }
        self.sent, self.logged, self.queries = [], [], []
        STATE["conf"].clear()
        STATE["conf"]["admin_alert_recipients"] = "security@example.net"
        STATE["settings"].clear()
        STATE["settings"].update(
            {
                "enable_two_factor_auth": "1",
                "bypass_2fa_for_retricted_ip_users": "0",
                "disable_user_pass_login": "1",
            }
        )
        STATE["singles"].clear()
        STATE["singles"][("LDAP Settings", "enabled")] = "0"
        STATE["covered"].clear()
        STATE["covered"].update(self.USERS)

        def get_all(doctype, filters=None, pluck=None, order_by=None, **_kw):
            self.queries.append((doctype, filters))
            return list(self.USERS) if doctype == "User" else []

        frappe.get_all = get_all
        frappe.sendmail = lambda **kwargs: self.sent.append(kwargs)
        frappe.log_error = lambda *args, **kwargs: self.logged.append(kwargs.get("title"))

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.frappe, name, value)
        STATE["conf"].clear()
        STATE["settings"].clear()
        STATE["singles"].clear()
        STATE["covered"].clear()

    def test_a_healthy_site_sends_nothing(self):
        security_alerts.check_two_factor_coverage()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.logged, [])

    def test_only_enabled_system_users_are_asked_about(self):
        security_alerts.two_factor_gaps()
        self.assertIn(("User", {"enabled": 1, "user_type": "System User"}), self.queries)

    def test_a_staff_account_with_no_two_factor_role_is_reported(self):
        STATE["covered"].discard("new.hire@example.com")
        site_wide, users = security_alerts.two_factor_gaps()
        self.assertEqual((site_wide, users), ([], ["new.hire@example.com"]))
        security_alerts.check_two_factor_coverage()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["recipients"], ["security@example.net"])
        self.assertIn("1 staff account can sign in without two-factor", self.sent[0]["subject"])
        self.assertIn("new.hire@example.com", self.sent[0]["message"])
        self.assertNotIn("tech@example.com", self.sent[0]["message"])

    def test_two_factor_off_site_wide_is_reported(self):
        STATE["settings"]["enable_two_factor_auth"] = "0"
        site_wide, _users = security_alerts.two_factor_gaps()
        self.assertEqual(len(site_wide), 1)
        security_alerts.check_two_factor_coverage()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("need attention", self.sent[0]["subject"])
        self.assertIn("off for the whole site", self.sent[0]["message"])

    def test_the_restricted_ip_bypass_is_reported(self):
        STATE["settings"]["bypass_2fa_for_retricted_ip_users"] = 1
        site_wide, _users = security_alerts.two_factor_gaps()
        self.assertEqual(len(site_wide), 1)
        self.assertIn("restricted IP", site_wide[0])

    def test_password_sign_in_switched_on_is_reported(self):
        """Staff are Google-only; the password form is where 2FA is all that stands behind a password."""
        STATE["settings"]["disable_user_pass_login"] = "0"
        site_wide, users = security_alerts.two_factor_gaps()
        self.assertEqual(users, [])
        self.assertEqual(len(site_wide), 1)
        self.assertIn("username and password is on", site_wide[0])

    def test_ldap_sign_in_switched_on_is_reported(self):
        """LDAP sign-in ignores disable_user_pass_login, so it is a password route even when that is set."""
        STATE["singles"][("LDAP Settings", "enabled")] = 1
        site_wide, _users = security_alerts.two_factor_gaps()
        self.assertEqual(len(site_wide), 1)
        self.assertIn("LDAP sign-in is on", site_wide[0])

    def test_each_finding_names_its_settings_page(self):
        STATE["settings"].update(
            {
                "enable_two_factor_auth": "0",
                "bypass_2fa_for_retricted_ip_users": "1",
                "disable_user_pass_login": "0",
            }
        )
        STATE["singles"][("LDAP Settings", "enabled")] = "1"
        site_wide, _users = security_alerts.two_factor_gaps()
        self.assertEqual(len(site_wide), 4)
        for line in site_wide:
            with self.subTest(line=line):
                self.assertRegex(line, r"\((System|LDAP) Settings\)\.$")

    def test_no_recipients_is_logged_not_dropped(self):
        STATE["conf"].clear()
        STATE["covered"].clear()
        security_alerts.check_two_factor_coverage()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.logged, ["Two-factor coverage alert has no recipients"])

    def test_a_failure_is_logged_and_never_raised(self):
        def explode(*_args, **_kwargs):
            raise RuntimeError("database gone")

        self.frappe.get_all = explode
        security_alerts.check_two_factor_coverage()
        self.assertEqual(self.logged, ["Two-factor coverage check failed"])

    def test_account_names_are_escaped_in_the_email(self):
        _subject, body = security_alerts.format_two_factor_alert(
            [], ["<b>x</b>@example.com"], "https://erp.example.com"
        )
        self.assertNotIn("<b>x</b>", body)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", body)

    def test_it_is_scheduled_daily(self):
        import ast

        tree = ast.parse((REPO_ROOT / "erpnext_enhancements" / "hooks.py").read_text(encoding="utf-8"))
        events = next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and getattr(node.targets[0], "id", None) == "scheduler_events"
        )
        self.assertIn("erpnext_enhancements.security_alerts.check_two_factor_coverage", events["daily"])


if __name__ == "__main__":
    unittest.main()
