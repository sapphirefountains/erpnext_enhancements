"""Integration tests for the Morning Briefing (``api.briefing``).

Covers the deterministic fallback composer (shape + never-empty guarantee),
cache idempotency (one Daily Briefing per user/day; re-runs return the cached
row; force regenerates), the recipients-driven batch, and the master-switch
gates. Gemini is kept OFF throughout (``briefing_use_gemini = 0``) so no test
ever makes a network call.
"""
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from erpnext_enhancements.api.briefing import (
    compose_fallback,
    compose_prompt,
    gather_briefing_data,
    generate_briefing_for_user,
    generate_briefings_for_all_users,
    get_morning_briefing,
    purge_old_briefings,
)

TEST_USER = "Administrator"

SAMPLE_DATA = {
    "date": "2026-06-11",
    "user": TEST_USER,
    "overdue_tasks": [{"subject": "Fix the leak", "priority": "High", "project": "Pond A", "due": "2026-06-01"}],
    "today_tasks": [{"subject": "Site visit", "priority": "Medium", "project": "", "due": "2026-06-11"}],
    "events": [{"subject": "Standup", "starts_on": "2026-06-11 09:00:00", "all_day": 0}],
    "pipeline": [{"title": "Big fountain", "party": "ACME", "status": "Open", "amount": 50000.0}],
    "todos": [{"description": "Call supplier", "date": "2026-06-10"}],
}


class TestComposers(FrappeTestCase):
    def test_fallback_contains_all_sections(self):
        text = compose_fallback(SAMPLE_DATA, TEST_USER)
        for fragment in ("Today's Schedule", "Tasks Today", "Overdue", "Pipeline Pulse", "ToDos Due"):
            self.assertIn(fragment, text)
        self.assertIn("Fix the leak", text)
        self.assertIn("Big fountain", text)

    def test_fallback_empty_day_is_friendly_not_blank(self):
        empty = {**SAMPLE_DATA, "overdue_tasks": [], "today_tasks": [], "events": [], "pipeline": [], "todos": []}
        text = compose_fallback(empty, TEST_USER)
        self.assertTrue(text.strip())
        self.assertIn("Good morning", text)

    def test_prompt_carries_data_and_guardrails(self):
        prompt = compose_prompt(SAMPLE_DATA, TEST_USER)
        self.assertIn("do not invent items", prompt)
        self.assertIn("Fix the leak", prompt)
        self.assertIn("omit empty sections", prompt)


class TestBriefingLifecycle(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.settings = frappe.get_doc("ERPNext Enhancements Settings")
        self._old = {
            "briefing_enabled": self.settings.get("briefing_enabled"),
            "briefing_use_gemini": self.settings.get("briefing_use_gemini"),
            "recipients": [dict(user=r.user, send_email=r.send_email) for r in (self.settings.get("briefing_recipients") or [])],
        }
        self._configure(enabled=1, use_gemini=0, recipients=[TEST_USER])
        self._cleanup_rows()

    def tearDown(self):
        self._configure(
            enabled=self._old["briefing_enabled"],
            use_gemini=self._old["briefing_use_gemini"],
            recipients=[r["user"] for r in self._old["recipients"]],
        )
        self._cleanup_rows()

    def _configure(self, enabled, use_gemini, recipients):
        doc = frappe.get_doc("ERPNext Enhancements Settings")
        doc.briefing_enabled = enabled or 0
        doc.briefing_use_gemini = use_gemini or 0
        doc.set("briefing_recipients", [])
        for user in recipients:
            doc.append("briefing_recipients", {"user": user, "send_email": 0})
        doc.save(ignore_permissions=True)
        frappe.clear_document_cache("ERPNext Enhancements Settings", "ERPNext Enhancements Settings")

    def _cleanup_rows(self):
        for name in frappe.get_all("Daily Briefing", filters={"user": TEST_USER}, pluck="name"):
            frappe.delete_doc("Daily Briefing", name, force=True, ignore_permissions=True)

    def test_generate_is_cached_per_day(self):
        doc1 = generate_briefing_for_user(TEST_USER)
        self.assertEqual(doc1.narrative_source, "Fallback")
        self.assertTrue(doc1.content.strip())

        doc2 = generate_briefing_for_user(TEST_USER)
        self.assertEqual(doc1.name, doc2.name)
        self.assertEqual(
            frappe.db.count("Daily Briefing", {"user": TEST_USER, "date": nowdate()}), 1
        )

    def test_force_regenerates(self):
        doc1 = generate_briefing_for_user(TEST_USER)
        first_generated_at = doc1.generated_at
        doc2 = generate_briefing_for_user(TEST_USER, force=True)
        self.assertEqual(
            frappe.db.count("Daily Briefing", {"user": TEST_USER, "date": nowdate()}), 1
        )
        self.assertGreaterEqual(str(doc2.generated_at), str(first_generated_at))

    def test_batch_generates_for_recipients(self):
        generate_briefings_for_all_users()
        self.assertTrue(
            frappe.db.exists("Daily Briefing", {"user": TEST_USER, "date": nowdate()})
        )

    def test_master_switch_off_blocks_everything(self):
        self._configure(enabled=0, use_gemini=0, recipients=[TEST_USER])
        generate_briefings_for_all_users()
        self.assertFalse(
            frappe.db.exists("Daily Briefing", {"user": TEST_USER, "date": nowdate()})
        )
        result = get_morning_briefing()
        self.assertFalse(result["available"])

    def test_endpoint_returns_briefing(self):
        result = get_morning_briefing()
        self.assertTrue(result["available"])
        self.assertTrue(result["briefing"].strip())
        self.assertEqual(result["source"], "Fallback")

    def test_gather_shape(self):
        data = gather_briefing_data(TEST_USER)
        for key in ("overdue_tasks", "today_tasks", "events", "pipeline", "todos", "date"):
            self.assertIn(key, data)

    def test_purge_keeps_today(self):
        generate_briefing_for_user(TEST_USER)
        purge_old_briefings()
        self.assertTrue(
            frappe.db.exists("Daily Briefing", {"user": TEST_USER, "date": nowdate()})
        )
