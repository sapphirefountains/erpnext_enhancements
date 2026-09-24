"""Bench-free tests for ``workforce/client_time.py`` — browser timestamps onto the site clock.

Regression guard for v1.526.1. The kiosk registers a camera capture with
``captured_on = new Date().toISOString()`` (``2026-09-24T03:04:24.049Z``). The server
parsed that into a tz-aware datetime, Frappe stringified it with ``+00:00`` on insert,
and MariaDB refused it — ``(1292, "Incorrect datetime value ...")`` — so no camera
capture ever wrote its ``Job Interval Photo`` row. The fix converts to the site's zone
and only then drops the offset; stripping the offset alone would store the photo six
hours late. Both halves are pinned here.

The module's imports need a ``frappe`` stub (``setUpModule``), so this suite gets its
own CI step.

Run: python -m unittest erpnext_enhancements.tests.test_workforce_client_time
"""

import ast
import re
import sys
import types
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TIME_KIOSK = REPO_ROOT / "erpnext_enhancements" / "api" / "time_kiosk.py"

client_time = None

NOW = datetime(2026, 9, 23, 21, 16, 1)
SITE_ZONE = {"name": "America/Denver"}

# What str(datetime) must look like for MariaDB to take it: no offset, ever.
MARIADB_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d{6})?$")


def _get_datetime(v):
    # Mirrors frappe v16 ``get_datetime`` for what the kiosk sends: a datetime passes
    # through, a string goes to ``fromisoformat`` (which returns an AWARE value for a
    # trailing ``Z`` or offset — the whole bug). v16 falls back to dateutil on
    # ValueError, which raises OverflowError on an epoch-ms string; raising here is the
    # same outcome for the caller.
    if v is None:
        return NOW
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s

    utils = types.ModuleType("frappe.utils")
    utils.get_datetime = _get_datetime
    utils.now_datetime = lambda: NOW
    utils.get_system_timezone = lambda: SITE_ZONE["name"]
    frappe.utils = utils

    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


def setUpModule():
    global client_time
    _install_frappe_stub()
    for name in list(sys.modules):
        if name.startswith("erpnext_enhancements.workforce"):
            del sys.modules[name]
    from erpnext_enhancements.workforce import client_time as mod

    client_time = mod


class TestParseClientTimestamp(unittest.TestCase):
    def setUp(self):
        SITE_ZONE["name"] = "America/Denver"

    def test_the_prod_payload_lands_on_site_time(self):
        # The exact value from the 2026-09-23 21:16 Error Log. 03:04:24 UTC is
        # 21:04:24 MDT (UTC-6) on the 23rd — the evening the photo was taken.
        got = client_time.parse_client_timestamp("2026-09-24T03:04:24.049Z")
        self.assertEqual(got, datetime(2026, 9, 23, 21, 4, 24, 49000))
        self.assertIsNone(got.tzinfo)

    def test_result_stringifies_the_way_mariadb_accepts(self):
        # Frappe inserts str(value). The old code produced
        # '2026-09-24 03:04:24.049000+00:00', which MariaDB rejects with 1292.
        got = client_time.parse_client_timestamp("2026-09-24T03:04:24.049Z")
        self.assertRegex(str(got), MARIADB_DATETIME)
        self.assertEqual(str(got), "2026-09-23 21:04:24.049000")

    def test_offset_is_converted_not_stripped(self):
        # Stripping would give 03:04 on the 24th: six hours late, next calendar day.
        got = client_time.parse_client_timestamp("2026-09-24T03:04:24Z")
        self.assertNotEqual(got, datetime(2026, 9, 24, 3, 4, 24))
        self.assertEqual(got, datetime(2026, 9, 23, 21, 4, 24))

    def test_standard_time_uses_the_winter_offset(self):
        # MST is UTC-7; a fixed -6 would be an hour off from November to March.
        got = client_time.parse_client_timestamp("2026-01-15T18:00:00Z")
        self.assertEqual(got, datetime(2026, 1, 15, 11, 0, 0))

    def test_non_utc_offset_is_honoured(self):
        # 23:04 at -04:00 is 03:04 UTC is 21:04 in Denver.
        got = client_time.parse_client_timestamp("2026-09-23T23:04:24-04:00")
        self.assertEqual(got, datetime(2026, 9, 23, 21, 4, 24))

    def test_zone_comes_from_the_site_not_a_constant(self):
        SITE_ZONE["name"] = "Asia/Kolkata"  # +05:30, no DST
        got = client_time.parse_client_timestamp("2026-09-24T03:04:24Z")
        self.assertEqual(got, datetime(2026, 9, 24, 8, 34, 24))

    def test_naive_site_local_string_is_untouched(self):
        # geo.js sends nowLocal(): device wall-clock, no offset. Must not be shifted.
        got = client_time.parse_client_timestamp("2026-09-23 21:04:24")
        self.assertEqual(got, datetime(2026, 9, 23, 21, 4, 24))
        self.assertIsNone(got.tzinfo)

    def test_datetime_objects(self):
        naive = datetime(2026, 9, 23, 21, 4, 24)
        self.assertEqual(client_time.parse_client_timestamp(naive), naive)
        aware = datetime(2026, 9, 24, 3, 4, 24, tzinfo=UTC)
        self.assertEqual(client_time.parse_client_timestamp(aware), naive)
        aware_other = datetime(2026, 9, 24, 8, 34, 24, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(client_time.parse_client_timestamp(aware_other), naive)

    def test_epoch_ms_is_an_instant_not_server_local(self):
        # Plain datetime.fromtimestamp() reads the epoch in the server PROCESS's zone
        # (UTC on prod), which is the same six-hour shift by another route.
        instant = datetime(2026, 9, 24, 3, 4, 24, 49000, tzinfo=UTC)
        ms = instant.timestamp() * 1000
        expected = datetime(2026, 9, 23, 21, 4, 24, 49000)
        for value in (ms, int(ms), str(int(ms))):
            with self.subTest(value=value):
                got = client_time.parse_client_timestamp(value)
                self.assertEqual(got, expected)
                self.assertIsNone(got.tzinfo)

    def test_empty_is_now(self):
        self.assertEqual(client_time.parse_client_timestamp(None), NOW)
        self.assertEqual(client_time.parse_client_timestamp(""), NOW)

    def test_to_site_naive_passes_none_and_naive_through(self):
        self.assertIsNone(client_time.to_site_naive(None))
        naive = datetime(2026, 9, 23, 21, 4, 24)
        self.assertIs(client_time.to_site_naive(naive), naive)


class TestTimeKioskUsesIt(unittest.TestCase):
    """The endpoint must route through the helper. Source-level, because importing
    ``api/time_kiosk.py`` needs a bench."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(TIME_KIOSK.read_text(encoding="utf-8"))
        cls.funcs = {n.name: n for n in ast.walk(cls.tree) if isinstance(n, ast.FunctionDef)}

    def test_parse_timestamp_delegates_to_client_time(self):
        body = ast.unparse(self.funcs["_parse_timestamp"])
        self.assertIn("client_time.parse_client_timestamp(", body)

    def test_record_job_photo_parses_captured_on(self):
        body = ast.unparse(self.funcs["record_job_photo"])
        self.assertIn("row.captured_on = _parse_timestamp(captured_on)", body)

    def test_no_naive_fromtimestamp_left(self):
        # An epoch read without tz= lands in the process's zone, not the site's.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "fromtimestamp":
                self.assertTrue(any(k.arg == "tz" for k in node.keywords),
                                f"naive fromtimestamp at time_kiosk.py:{node.lineno}")


if __name__ == "__main__":
    unittest.main()
