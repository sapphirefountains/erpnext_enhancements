"""Bench-free tests for ``workforce/sweeper.py`` — the auto-close end-time decision.

Guards the class of bug where a forgotten clock-out is closed at the wrong time:
a Paused interval closed at "now" instead of at the pause, a location fix from
BEFORE the start (or from a phone whose clock is in the future) chosen as the end,
an end time before the start, or the limit rule producing an end in the future.
Each of those becomes a payroll number nobody typed. The decision is a pure
function; the module's imports need a ``frappe`` stub (``setUpModule``), so this
suite gets its own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_workforce_sweeper
"""

import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sweeper = None

START = datetime(2026, 9, 17, 7, 0, 0)
NOW = datetime(2026, 9, 18, 3, 0, 0)  # 20 h later; the 14 h limit has passed


def _get_datetime(v):
    if isinstance(v, datetime):
        return v
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.flags = types.SimpleNamespace(in_migrate=False, in_install=False, in_patch=False)
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(Exception(a[0] if a else ""))
    frappe.log_error = lambda *a, **k: None
    frappe.db = types.SimpleNamespace()
    frappe.local = types.SimpleNamespace()

    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(float(v or 0))
    utils.flt = lambda v, p=None: float(v or 0)
    utils.get_datetime = _get_datetime
    utils.now_datetime = lambda: NOW
    utils.get_url_to_form = lambda dt, dn: f"/app/{dt}/{dn}"
    utils.get_url = lambda p="": p
    frappe.utils = utils

    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        pass

    document.Document = Document
    model.document = document

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document


def setUpModule():
    global sweeper
    _install_frappe_stub()
    for name in list(sys.modules):
        if name.startswith("erpnext_enhancements.workforce") or name.startswith("erpnext_enhancements.email_style") \
                or name.startswith("erpnext_enhancements.utils"):
            del sys.modules[name]
    from erpnext_enhancements.workforce import sweeper as mod

    sweeper = mod


class TestDecideEndTime(unittest.TestCase):
    def test_paused_closes_at_the_pause(self):
        paused_at = START + timedelta(hours=5)
        end, reason = sweeper.decide_end_time("Paused", START, paused_at, START + timedelta(hours=8), 14, NOW)
        self.assertEqual((end, reason), (paused_at, sweeper.REASON_PAUSED))

    def test_open_closes_at_the_last_fix(self):
        fix = START + timedelta(hours=9, minutes=12)
        end, reason = sweeper.decide_end_time("Open", START, None, fix, 14, NOW)
        self.assertEqual((end, reason), (fix, sweeper.REASON_LAST_FIX))

    def test_no_evidence_closes_at_the_limit(self):
        end, reason = sweeper.decide_end_time("Open", START, None, None, 14, NOW)
        self.assertEqual((end, reason), (START + timedelta(hours=14), sweeper.REASON_LIMIT))

    def test_a_fix_before_the_start_is_not_an_end(self):
        end, reason = sweeper.decide_end_time("Open", START, None, START - timedelta(minutes=5), 14, NOW)
        self.assertEqual(reason, sweeper.REASON_LIMIT)

    def test_a_fix_at_the_start_is_not_an_end(self):
        end, reason = sweeper.decide_end_time("Open", START, None, START, 14, NOW)
        self.assertEqual(reason, sweeper.REASON_LIMIT)

    def test_a_future_fix_is_not_an_end(self):
        # a phone with a wrong clock
        end, reason = sweeper.decide_end_time("Open", START, None, NOW + timedelta(hours=2), 14, NOW)
        self.assertEqual((end, reason), (START + timedelta(hours=14), sweeper.REASON_LIMIT))

    def test_paused_without_a_pause_time_falls_through(self):
        fix = START + timedelta(hours=3)
        end, reason = sweeper.decide_end_time("Paused", START, None, fix, 14, NOW)
        self.assertEqual((end, reason), (fix, sweeper.REASON_LAST_FIX))

    def test_a_pause_before_the_start_falls_through(self):
        end, reason = sweeper.decide_end_time("Paused", START, START - timedelta(hours=1), None, 14, NOW)
        self.assertEqual(reason, sweeper.REASON_LIMIT)

    def test_the_limit_never_lands_in_the_future(self):
        soon = START + timedelta(hours=15)  # only an hour past the limit... limit itself is in the past
        end, _ = sweeper.decide_end_time("Open", START, None, None, 14, soon)
        self.assertLessEqual(end, soon)
        # a zero limit degenerates to the start, never before it
        end, _ = sweeper.decide_end_time("Open", START, None, None, 0, NOW)
        self.assertEqual(end, START)

    def test_the_end_is_never_before_the_start(self):
        for status, pause, fix in (("Paused", START - timedelta(days=1), None), ("Open", None, START - timedelta(days=1))):
            end, _ = sweeper.decide_end_time(status, START, pause, fix, 14, NOW)
            self.assertGreaterEqual(end, START)

    def test_accepts_frappe_strings(self):
        end, reason = sweeper.decide_end_time("Open", "2026-09-17 07:00:00", None, "2026-09-17 16:00:00", 14, NOW)
        self.assertEqual((end, reason), (datetime(2026, 9, 17, 16), sweeper.REASON_LAST_FIX))

    def test_pause_beats_fix(self):
        paused_at = START + timedelta(hours=5)
        end, reason = sweeper.decide_end_time("Paused", START, paused_at, START + timedelta(hours=9), 14, NOW)
        self.assertEqual((end, reason), (paused_at, sweeper.REASON_PAUSED))


class TestReasonText(unittest.TestCase):
    def test_every_reason_has_a_sentence_and_the_limit_is_named(self):
        for reason in (sweeper.REASON_PAUSED, sweeper.REASON_LAST_FIX, sweeper.REASON_LIMIT):
            text = sweeper.reason_text(reason, 14)
            self.assertTrue(text.startswith("End time = "), text)
        self.assertIn("14 h", sweeper.reason_text(sweeper.REASON_LIMIT, 14))

    def test_unknown_reason_falls_back_to_the_limit_sentence(self):
        self.assertEqual(sweeper.reason_text("???", 14), sweeper.reason_text(sweeper.REASON_LIMIT, 14))


if __name__ == "__main__":
    unittest.main()
