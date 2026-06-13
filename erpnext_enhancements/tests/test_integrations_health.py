"""Bench-free unit tests for the Integrations Health tone/age helpers.

The tile-tone logic (worst-of, staleness, expiry countdown) is pure and carries
no frappe dependency, so it runs under the same stub environment as the AI gate
unit tests — fast, and green in CI without a bench. The frappe-backed checks
(``_check_quickbooks`` …) and ``get_health`` need a live site and are exercised
on a real bench, not here.

Run: python -m unittest erpnext_enhancements.tests.test_integrations_health
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_ih = None


def setUpModule():
    global _ih
    from erpnext_enhancements.tests.test_assistant_tools_schema import install_stubs

    install_stubs()  # provides the frappe.utils names imported at module top
    from erpnext_enhancements.api import integrations_health

    _ih = integrations_health


class TestWorstTone(unittest.TestCase):
    def test_ordering(self):
        self.assertEqual(_ih.worst_tone(["green", "amber", "red"]), "red")
        self.assertEqual(_ih.worst_tone(["green", "amber"]), "amber")
        self.assertEqual(_ih.worst_tone(["green", "green"]), "green")
        self.assertEqual(_ih.worst_tone(["neutral", "green"]), "green")

    def test_empty_and_unknown(self):
        self.assertEqual(_ih.worst_tone([]), "neutral")
        # an unrecognised tone ranks as neutral, never escalates
        self.assertEqual(_ih.worst_tone(["bogus"]), "neutral")
        self.assertEqual(_ih.worst_tone(["bogus", "amber"]), "amber")


class TestHumanizeAge(unittest.TestCase):
    def test_buckets(self):
        self.assertEqual(_ih.humanize_age(None), "never")
        self.assertEqual(_ih.humanize_age(10), "just now")
        self.assertEqual(_ih.humanize_age(59), "just now")
        self.assertEqual(_ih.humanize_age(120), "2 min ago")
        self.assertEqual(_ih.humanize_age(3 * 3600), "3 h ago")
        self.assertEqual(_ih.humanize_age(2 * 86400), "2 d ago")


class TestAgeTone(unittest.TestCase):
    def test_thresholds(self):
        # older is worse
        self.assertEqual(_ih.age_tone(None, 60, 120), "neutral")
        self.assertEqual(_ih.age_tone(30, 60, 120), "green")
        self.assertEqual(_ih.age_tone(60, 60, 120), "amber")
        self.assertEqual(_ih.age_tone(90, 60, 120), "amber")
        self.assertEqual(_ih.age_tone(120, 60, 120), "red")
        self.assertEqual(_ih.age_tone(500, 60, 120), "red")


class TestCountdownTone(unittest.TestCase):
    def test_thresholds(self):
        # sooner is worse; negative = already expired
        self.assertEqual(_ih.countdown_tone(None, 900), "neutral")
        self.assertEqual(_ih.countdown_tone(-5, 900), "red")
        self.assertEqual(_ih.countdown_tone(0, 900), "red")
        self.assertEqual(_ih.countdown_tone(300, 900), "amber")
        self.assertEqual(_ih.countdown_tone(900, 900), "amber")
        self.assertEqual(_ih.countdown_tone(5000, 900), "green")


class TestTileShape(unittest.TestCase):
    def test_tile_has_contract_keys(self):
        tile = _ih._tile(
            "drive", "Google Drive", "green", "Connected",
            configured=True, metrics=[_ih._metric("Failed (24h)", 0, "green")],
            links=[{"label": "Settings", "route": "/app/x"}], actions=["drive_test"],
        )
        for key in ("key", "label", "status", "headline", "configured", "metrics", "links", "notes", "actions"):
            self.assertIn(key, tile)
        self.assertEqual(tile["metrics"][0], {"label": "Failed (24h)", "value": 0, "tone": "green"})
        self.assertEqual(tile["actions"], ["drive_test"])


if __name__ == "__main__":
    unittest.main()
