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
STATE = {"conf": {}, "cache": {}, "flags": {}}
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
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


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


if __name__ == "__main__":
    unittest.main()
