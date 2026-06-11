"""Integration tests for Call Intelligence (``api.call_intelligence``).

Verifies the Call Log upsert contract: idempotency by call SID, status /
direction / sentiment mapping, partial updates never blanking stored fields,
manual-summary preservation, Telephony Call Type get-or-create, and the
invalid-SID guard. Pure-helper behaviour (_as_list / _as_dict / mappers) is
covered without any fixture data.
"""
import json

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_enhancements.api.call_intelligence import (
    _as_dict,
    _as_list,
    _get_or_create_call_type,
    _map_direction,
    _map_status,
    upsert_call_log,
)

TEST_SID = "CA_test_call_intelligence_0001"


class TestHelpers(FrappeTestCase):
    def test_as_list(self):
        self.assertEqual(_as_list(None), [])
        self.assertEqual(_as_list(["a", " b ", ""]), ["a", "b"])
        self.assertEqual(_as_list('["x", "y"]'), ["x", "y"])
        self.assertEqual(_as_list("line one\nline two\n"), ["line one", "line two"])

    def test_as_dict(self):
        self.assertEqual(_as_dict(None), {})
        self.assertEqual(_as_dict({"a": 1}), {"a": 1})
        self.assertEqual(_as_dict('{"a": 1}'), {"a": 1})
        self.assertEqual(_as_dict("not json"), {})
        self.assertEqual(_as_dict("[1, 2]"), {})

    def test_status_mapping(self):
        self.assertEqual(_map_status("completed"), "Completed")
        self.assertEqual(_map_status("MISSED"), "No Answer")
        self.assertEqual(_map_status("no-answer"), "No Answer")
        self.assertEqual(_map_status("failed"), "Failed")
        self.assertIsNone(_map_status("weird"))
        self.assertIsNone(_map_status(None))

    def test_direction_mapping(self):
        self.assertEqual(_map_direction("Inbound"), "Incoming")
        self.assertEqual(_map_direction("outbound"), "Outgoing")
        self.assertEqual(_map_direction("incoming"), "Incoming")
        self.assertIsNone(_map_direction("sideways"))


class TestUpsertCallLog(FrappeTestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self._cleanup()

    def tearDown(self):
        self._cleanup()

    def _cleanup(self):
        if frappe.db.exists("Call Log", TEST_SID):
            frappe.delete_doc("Call Log", TEST_SID, force=True, ignore_permissions=True)

    def test_invalid_sid_is_skipped(self):
        for sid in (None, "", "undefined", "null", "None"):
            self.assertIsNone(upsert_call_log(sid, status="completed"))

    def test_create_then_idempotent_update(self):
        name = upsert_call_log(
            TEST_SID,
            direction="Inbound",
            from_number="+18015551234",
            to_number="+18018200044",
            status="completed",
            duration=185,
            caller_name="Jane Doe",
            summary="Asked about pond maintenance pricing.",
            follow_up_actions=["Send quote", "Schedule visit"],
            sentiment="positive",
            escalation_risk="low",
            analysis={
                "customer_satisfaction": 4,
                "topics": ["pricing", "maintenance"],
                "compliance_flags": [],
            },
            ivr_selection="Service",
        )
        self.assertEqual(name, TEST_SID)

        doc = frappe.get_doc("Call Log", TEST_SID)
        self.assertEqual(doc.type, "Incoming")
        self.assertEqual(doc.status, "Completed")
        self.assertEqual(doc.get("from"), "+18015551234")
        self.assertEqual(frappe.utils.cint(doc.duration), 185)
        self.assertEqual(doc.custom_sentiment, "Positive")
        self.assertEqual(doc.custom_escalation_risk, "Low")
        self.assertEqual(doc.custom_csat_score, 4)
        self.assertEqual(doc.custom_topics, "pricing, maintenance")
        self.assertEqual(doc.custom_follow_up_actions, "Send quote\nSchedule visit")
        self.assertFalse(doc.custom_has_compliance_flags)
        self.assertEqual(doc.type_of_call, frappe.db.get_value(
            "Telephony Call Type", {"call_type": "Service"}))

        # identical re-delivery: still exactly one record, values unchanged
        again = upsert_call_log(TEST_SID, status="completed", sentiment="positive")
        self.assertEqual(again, TEST_SID)
        self.assertEqual(
            frappe.db.count("Call Log", {"id": TEST_SID}), 1
        )

    def test_partial_update_never_blanks_fields(self):
        upsert_call_log(
            TEST_SID,
            direction="Inbound",
            from_number="+18015551234",
            status="completed",
            sentiment="negative",
            escalation_risk="high",
            summary="Angry about a leak.",
        )
        # later status-only correction
        upsert_call_log(TEST_SID, status="failed")

        doc = frappe.get_doc("Call Log", TEST_SID)
        self.assertEqual(doc.status, "Failed")
        self.assertEqual(doc.custom_sentiment, "Negative")
        self.assertEqual(doc.custom_escalation_risk, "High")
        self.assertEqual(doc.summary, "Angry about a leak.")

    def test_manual_summary_preserved(self):
        upsert_call_log(TEST_SID, direction="Inbound", status="completed")
        frappe.db.set_value("Call Log", TEST_SID, "summary", "Manually curated summary")

        upsert_call_log(TEST_SID, summary="AI summary that must not clobber")
        self.assertEqual(
            frappe.db.get_value("Call Log", TEST_SID, "summary"),
            "Manually curated summary",
        )

    def test_compliance_flags_set_flag_field(self):
        upsert_call_log(
            TEST_SID,
            direction="Inbound",
            status="completed",
            analysis=json.dumps({"compliance_flags": ["Recording disclosure missing"]}),
        )
        doc = frappe.get_doc("Call Log", TEST_SID)
        self.assertTrue(doc.custom_has_compliance_flags)
        self.assertIn("Recording disclosure missing", doc.custom_compliance_flags)
        self.assertTrue(doc.custom_analysis_json)

    def test_call_type_get_or_create_is_stable(self):
        first = _get_or_create_call_type("Service")
        second = _get_or_create_call_type("Service")
        self.assertEqual(first, second)
        self.assertIsNone(_get_or_create_call_type(""))
        self.assertIsNone(_get_or_create_call_type(None))

    def test_voicemail_missed_call(self):
        upsert_call_log(
            TEST_SID,
            direction="Inbound",
            from_number="+18015550000",
            status="missed",
            voicemail_url="https://example.com/vm.mp3",
        )
        doc = frappe.get_doc("Call Log", TEST_SID)
        self.assertEqual(doc.status, "No Answer")
        self.assertEqual(doc.custom_voicemail_url, "https://example.com/vm.mp3")
