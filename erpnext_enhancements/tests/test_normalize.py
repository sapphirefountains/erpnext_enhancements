"""Bench-free tests for the normalization rules and their JavaScript twin.

``utils/normalize.py`` and ``public/js/normalize/normalize.js`` implement the same rules for
the server and the Desk. Two implementations of one rule drift unless something holds them
together, so both are run over one case file, ``tests/fixtures/normalize_cases.json``, and
the JavaScript results are compared field by field with the Python ``Result`` — value, ok,
changed and reason — not only with the expected values.

The node half skips cleanly when ``node`` is not on PATH (CI's python job has it). The JS
module is copied to a temp dir as ``.mjs`` before importing it: the repo's package.json has
no ``"type": "module"``, and node 20 (CI) does not detect ES module syntax in a ``.js`` file.

Also pinned here, because each is a promise another module relies on:

* the :class:`Result` invariants — not ok means the input is returned exactly as given;
* ``utils/phone.normalize_phone`` (the party-matching key) still agrees with the stored
  E.164 form on every North American number, so normalizing a field never breaks a match;
* ``call_routing_match.to_e164`` agrees with :func:`normalize.phone` wherever the latter
  can normalize — see ``test_to_e164_agrees_where_phone_is_sure`` for why it is not a
  delegate.

Run: python -m unittest erpnext_enhancements.tests.test_normalize -v
"""

import datetime
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.ai_governance import call_routing_match
from erpnext_enhancements.utils import normalize as n
from erpnext_enhancements.utils import phone as party_phone

CASES_PATH = REPO_ROOT / "erpnext_enhancements" / "tests" / "fixtures" / "normalize_cases.json"
JS_PATH = REPO_ROOT / "erpnext_enhancements" / "public" / "js" / "normalize" / "normalize.js"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))
KIND_KEYS = ("phone", "email", "website", "text", "state", "zip")

# Runs every case through the JS twin and prints the results as JSON, in case-file order.
JS_RUNNER = r"""
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const [moduleFile, casesFile] = process.argv.slice(2);
const n = await import(pathToFileURL(moduleFile).href);
const cases = JSON.parse(readFileSync(casesFile, "utf8"));
const out = {};
for (const kind of ["phone", "email", "website", "text", "state", "zip"]) {
	out[kind] = cases[kind].map((c) => {
		const r = n.normalize(kind, c.in, c.country === undefined ? null : c.country);
		const row = { value: r.value, ok: r.ok, changed: r.changed, reason: r.reason };
		if (kind === "phone") row.display = n.phone_display(c.in);
		return row;
	});
}
out.dates = cases.dates.map((c) => n[c.fn](c.in));
let unknown = "";
try { n.normalize("fax", "1"); } catch (e) { unknown = "threw"; }
out.unknown_kind = unknown;
out.add_scheme = ["example.com", "  ", null, "https://x.com", "/a", "N/A"].map(n.add_scheme);
process.stdout.write(JSON.stringify(out));
"""


def _country(case):
    return case.get("country")


def _run(kind, case):
    return n.normalize(kind, case["in"], _country(case))


class TestCaseFile(unittest.TestCase):
    def test_enough_cases_and_every_kind(self):
        total = sum(len(CASES[k]) for k in KIND_KEYS) + len(CASES["dates"])
        self.assertGreaterEqual(total, 60)
        self.assertEqual(set(KIND_KEYS), set(n.KINDS))
        for kind in KIND_KEYS:
            with self.subTest(kind=kind):
                self.assertTrue(CASES[kind])
                self.assertTrue(any(c["ok"] for c in CASES[kind]) or kind == "text")

    def test_the_spec_shapes_are_covered(self):
        """The shapes the product owner named, so a later edit cannot quietly drop one."""
        phones = {c["in"] for c in CASES["phone"]}
        for shape in ("8015550100", "801-555-0100", "801.555.0100", "(801) 555-0100", "18015550100",
                      "+18015550100", "+44 20 7946 0958", "65-688-000-88", "555-0100", "1-800-FLOWERS",
                      "801-555-0100 / 801-555-0101", "801-555-0100 x123", "801-555-0100 x 123",
                      "801-555-0100 ext 123", "801-555-0100 ext. 123", "801-555-0100 extension 123",
                      "801-555-0100 #123", "801-555-0100,123", "+4930123456789 x123456",
                      "(101) 555-0100", "0015550100"):
            self.assertIn(shape, phones)


class TestPythonCases(unittest.TestCase):
    def test_every_kind(self):
        for kind in KIND_KEYS:
            for case in CASES[kind]:
                with self.subTest(kind=kind, value=case["in"], country=_country(case)):
                    result = _run(kind, case)
                    self.assertEqual((result.value, result.ok), (case["value"], case["ok"]), result.reason)

    def test_phone_display(self):
        for case in CASES["phone"]:
            with self.subTest(value=case["in"]):
                self.assertEqual(n.phone_display(case["in"]), case["display"])

    def test_dates(self):
        for case in CASES["dates"]:
            with self.subTest(fn=case["fn"], value=case["in"]):
                self.assertEqual(getattr(n, case["fn"])(case["in"]), case["out"])


class TestResultInvariants(unittest.TestCase):
    """Decided once, held everywhere: not ok returns the input exactly; ok has no reason."""

    def test_every_case(self):
        for kind in KIND_KEYS:
            for case in CASES[kind]:
                raw = case["in"]
                result = _run(kind, case)
                original = "" if raw is None else str(raw)
                with self.subTest(kind=kind, value=raw):
                    self.assertIsInstance(result, n.Result)
                    self.assertIsInstance(result.value, str)
                    self.assertEqual(result.changed, result.value != original)
                    if result.ok:
                        self.assertEqual(result.reason, "")
                    else:
                        self.assertEqual(result.value, original, "not ok must leave the value as typed")
                        self.assertFalse(result.changed)
                        self.assertTrue(result.reason)

    def test_normalizing_twice_changes_nothing(self):
        """What makes a re-run cleanup, and a save after a save, a no-op."""
        for kind in KIND_KEYS:
            for case in CASES[kind]:
                first = _run(kind, case)
                if not first.ok:
                    continue
                with self.subTest(kind=kind, value=case["in"]):
                    again = n.normalize(kind, first.value, _country(case))
                    self.assertTrue(again.ok)
                    self.assertEqual(again.value, first.value)
                    self.assertFalse(again.changed)

    def test_none_is_empty_and_clean(self):
        for kind in KIND_KEYS:
            with self.subTest(kind=kind):
                self.assertEqual(n.normalize(kind, None), n.Result("", False, True, ""))

    def test_stored_phones_fit_frappe_v16_phone_validation(self):
        """Frappe's Phone regex: no letters, at most 20 characters."""
        import re

        frappe_phone = re.compile(r"^([0-9\ \+\_\-\,\.\*\#\(\)]){1,20}$")
        for case in CASES["phone"]:
            if case["ok"] and case["value"]:
                with self.subTest(value=case["in"]):
                    self.assertRegex(case["value"], frappe_phone)


class TestDispatch(unittest.TestCase):
    def test_kinds_map_to_the_functions(self):
        self.assertIs(n.KINDS["phone"], n.phone)
        self.assertIs(n.KINDS["zip"], n.zip_code)
        self.assertEqual(n.normalize("email", "A@B.COM"), n.email("A@B.COM"))
        self.assertEqual(n.normalize("text", " a  b, "), n.text(" a  b, "))

    def test_country_reaches_state_and_zip_only(self):
        self.assertEqual(n.normalize("state", "Ontario", "Canada").ok, True)
        self.assertEqual(n.normalize("state", "Ontario", "United States").ok, False)
        self.assertEqual(n.normalize("zip", "M5V 3L9", "Canada").value, "M5V 3L9")
        self.assertFalse(n.normalize("zip", "M5V 3L9").ok)
        # A country passed to a kind that does not use one is ignored, not an error.
        self.assertEqual(n.normalize("phone", "8015550100", "Canada").value, "+18015550100")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            n.normalize("fax", "8015550100")


class TestPythonOnlyDateTypes(unittest.TestCase):
    """The objects Frappe hands Python: date, datetime, time, and timedelta for a Time field."""

    def test_date_and_datetime(self):
        self.assertEqual(n.format_date(datetime.date(2026, 10, 14)), "10/14/2026")
        self.assertEqual(n.format_date(datetime.datetime(2026, 10, 14, 23, 0)), "10/14/2026")
        self.assertEqual(n.format_datetime(datetime.datetime(2026, 10, 14, 14, 5, 59)), "10/14/2026 2:05 PM")
        self.assertEqual(n.format_datetime(datetime.datetime(2026, 10, 14, 0, 0)), "10/14/2026 12:00 AM")
        self.assertEqual(n.format_datetime(datetime.datetime(2026, 10, 14, 12, 0)), "10/14/2026 12:00 PM")
        self.assertEqual(n.format_datetime(datetime.date(2026, 10, 14)), "10/14/2026")

    def test_time_and_timedelta(self):
        self.assertEqual(n.format_time(datetime.time(14, 5)), "2:05 PM")
        self.assertEqual(n.format_time(datetime.timedelta(hours=14, minutes=5, seconds=30)), "2:05 PM")
        self.assertEqual(n.format_time(datetime.timedelta(0)), "12:00 AM")
        self.assertEqual(n.format_time(datetime.timedelta(hours=12)), "12:00 PM")
        self.assertEqual(n.format_time(datetime.timedelta(days=1, hours=2)), "2:00 AM")
        self.assertEqual(n.format_time(datetime.datetime(2026, 10, 14, 9, 7)), "9:07 AM")

    def test_the_string_a_timedelta_prints_as(self):
        """``str(timedelta)`` is what a Time value becomes on its way through JSON."""
        for delta in (datetime.timedelta(hours=9, minutes=5), datetime.timedelta(hours=14, minutes=5),
                      datetime.timedelta(days=1, hours=2)):
            with self.subTest(delta=str(delta)):
                self.assertEqual(n.format_time(str(delta)), n.format_time(delta))


class TestAgreementWithOtherPhoneHelpers(unittest.TestCase):
    def _nanp_cases(self, with_extension):
        for case in CASES["phone"]:
            value = case["value"]
            if case["ok"] and value.startswith("+1") and ("," in value) == with_extension:
                yield case

    def test_party_matching_key_agrees(self):
        """``utils/phone.normalize_phone`` is the exact-match key for party matching. On every
        North American number it must read the raw value and the stored E.164 value as the
        same ten digits, or normalizing a field would silently break a match."""
        cases = list(self._nanp_cases(with_extension=False))
        self.assertGreater(len(cases), 10)
        for case in cases:
            national = n.phone(case["in"]).value[2:12]
            with self.subTest(value=case["in"]):
                self.assertEqual(party_phone.normalize_phone(case["in"]), national)
                self.assertEqual(party_phone.normalize_phone(case["value"]), national)
                self.assertTrue(party_phone.is_nanp(case["value"]))

    def test_to_e164_agrees_where_phone_is_sure(self):
        """``to_e164`` is not a delegate of ``phone()``: ``call_routing_match`` is copied
        byte-for-byte into Triton and must stay stdlib-only and self-contained. It is also
        more lenient (any ten digits are North American to it), and the call-routing vectors
        pin that. What is pinned here is that the two never disagree about a number
        ``phone()`` is sure of."""
        for case in CASES["phone"]:
            if not case["ok"] or not case["value"]:
                continue
            main = case["value"].split(",")[0]
            if "," in case["value"]:
                continue
            with self.subTest(value=case["in"]):
                self.assertEqual(call_routing_match.to_e164(case["in"]), main)


class TestJavaScriptTwin(unittest.TestCase):
    """The same cases through ``normalize.js`` under node, compared with Python's results."""

    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node is not on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp) / "normalize.mjs"
            shutil.copyfile(JS_PATH, module)
            runner = Path(tmp) / "runner.mjs"
            runner.write_text(JS_RUNNER, encoding="utf-8")
            result = subprocess.run(
                [node, str(runner), str(module), str(CASES_PATH)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        cls.out = json.loads(result.stdout)

    def test_every_kind_matches_python_exactly(self):
        for kind in KIND_KEYS:
            for case, got in zip(CASES[kind], self.out[kind], strict=True):
                py = _run(kind, case)
                expected = {"value": py.value, "ok": py.ok, "changed": py.changed, "reason": py.reason}
                if kind == "phone":
                    expected["display"] = n.phone_display(case["in"])
                with self.subTest(kind=kind, value=case["in"], country=_country(case)):
                    self.assertEqual(got, expected)
                    self.assertEqual((got["value"], got["ok"]), (case["value"], case["ok"]))

    def test_dates_match(self):
        for case, got in zip(CASES["dates"], self.out["dates"], strict=True):
            with self.subTest(fn=case["fn"], value=case["in"]):
                self.assertEqual(got, case["out"])

    def test_dispatch_and_add_scheme(self):
        self.assertEqual(self.out["unknown_kind"], "threw")
        expected = [n.add_scheme(v) for v in ("example.com", "  ", None, "https://x.com", "/a", "N/A")]
        self.assertEqual(self.out["add_scheme"], expected)

    def test_the_module_parses_as_an_es_module(self):
        """No DOM, no frappe, no imports: the later bundle import must not pull anything in."""
        import re

        # Comments stripped first: the header prose names the things the code must not use.
        code = "\n".join(
            line for line in JS_PATH.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("//")
        )
        self.assertNotRegex(code, re.compile(r"^\s*import[\s{*]", re.MULTILINE))
        self.assertNotRegex(code, r"\brequire\(")
        for name in ("frappe.", "document.", "window.", "$("):
            self.assertNotIn(name, code)


if __name__ == "__main__":
    unittest.main()
