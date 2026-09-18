"""Bench-free contract for the server-side geocoding key (v1.483.2).

The bug this pins is a *consequence of doing the right thing*. `google_maps_api_key` is
handed to browsers, so it must be restricted by HTTP referrer — and the moment it was,
every server-side geocode began failing:

    workforce.sites: geocode PRJ-00151 -> REQUEST_DENIED
    API keys with referer restrictions cannot be used with this API.

Google refuses a referrer-restricted key on any server-side web-service call, so no amount
of configuration makes one key serve both. The only two exits are a second, IP-restricted
key, or loosening the browser key's restriction — and the second publishes an unrestricted
key to every device that loads a map. Hence `google_geocoding_api_key`.

Why this matters beyond a log line: `sites.py` resolves the coordinates that the Time Kiosk's
geofence measures against. A silently failing geocoder means projects without site
coordinates, which means no off-site verdict at all — the check does not fail loudly, it
simply stops having an opinion.

Read as text and JSON; `sites.py` imports frappe at module scope, so this suite does not
import it.

Run: python -m unittest erpnext_enhancements.tests.test_geocoding_key
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SITES = APP / "workforce/sites.py"
SETTINGS = APP / "travel_management/doctype/travel_settings/travel_settings.json"

SOURCE = SITES.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _segment(node):
    return ast.get_source_segment(SOURCE, node) or ""


def _settings_fields():
    return {f.get("fieldname"): f for f in json.loads(SETTINGS.read_text(encoding="utf-8"))["fields"]}


class TestTheServerKeyExists(unittest.TestCase):
    def test_travel_settings_declares_it(self):
        field = _settings_fields().get("google_geocoding_api_key")
        self.assertIsNotNone(field, "google_geocoding_api_key is missing from Travel Settings")

    def test_it_is_a_password_field(self):
        """It never leaves the server, so there is nothing gained by storing it in
        the clear where any Travel Settings reader (or a report view, or
        /api/resource) can see it. The browser key is deliberately NOT a Password:
        it is handed to browsers by design."""
        self.assertEqual(_settings_fields()["google_geocoding_api_key"].get("fieldtype"), "Password")
        self.assertEqual(_settings_fields()["google_maps_api_key"].get("fieldtype"), "Data")

    def test_a_password_field_is_read_with_get_password(self):
        """The trap that makes this worth a test: a Password value lives in `__Auth`,
        NOT in `tabSingles`, so `get_single_value` returns None for it -- every time,
        on every site. Reading it that way would silently fall through to the browser
        key and reproduce the REQUEST_DENIED this whole mechanism exists to stop.
        `Triton Settings.maps_api_key` was stranded in exactly this shape before."""
        fn = _function("_geocoding_api_key")
        self.assertIsNotNone(fn)
        body = _segment(fn)
        self.assertIn("get_password(", body)
        # and the server key must NOT be read with get_single_value
        for line in body.splitlines():
            if "google_geocoding_api_key" in line:
                self.assertNotIn("get_single_value", line)

    def test_it_carries_no_default(self):
        """Travel Settings is a Single: a default on a new field never reaches the row that
        already exists, so a default here would assert a key the site does not have."""
        field = _settings_fields()["google_geocoding_api_key"]
        self.assertIn(field.get("default"), (None, ""))

    def test_its_description_explains_the_restriction_that_forces_it(self):
        """A reader who does not know WHY there are two keys will merge them again."""
        text = (_settings_fields()["google_geocoding_api_key"].get("description") or "").lower()
        self.assertTrue(text, "the field needs a description")
        self.assertIn("referrer", text)
        self.assertIn("ip", text)


class TestSitesUsesIt(unittest.TestCase):
    def test_the_helper_prefers_the_server_key(self):
        """Order is asserted over the EXECUTABLE body, not the source text.

        The docstring explains why two keys exist and necessarily names the browser key
        while doing so, which would satisfy a naive text search and invert the result —
        the same trap as an absence assertion matching the comment that explains the
        absence.
        """
        fn = _function("_geocoding_api_key")
        self.assertIsNotNone(fn, "_geocoding_api_key is missing")

        statements = [n for n in fn.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
        code = "\n".join(_segment(n) for n in statements)

        self.assertIn("google_geocoding_api_key", code)
        self.assertIn("google_maps_api_key", code, "the fallback must remain")
        self.assertLess(
            code.index("google_geocoding_api_key"),
            code.index("google_maps_api_key"),
            "the server key must be read first; the browser key is only the fallback",
        )

    def test_every_key_read_goes_through_the_helper(self):
        """No call site may reach past it to the browser key — that is the regression."""
        for fn_name in ("_geocode_project", "backfill_missing_site_coordinates"):
            fn = _function(fn_name)
            if fn is None:
                continue
            with self.subTest(function=fn_name):
                self.assertNotIn(
                    "google_maps_api_key",
                    _segment(fn),
                    f"{fn_name} reads the browser key directly instead of _geocoding_api_key()",
                )

    def test_the_geocode_call_uses_the_helper(self):
        body = _segment(_function("_geocode_project"))
        self.assertIn("_geocoding_api_key()", body)

    def test_the_old_name_still_resolves(self):
        """`_maps_api_key` was the published name; keep it working rather than breaking an
        importer to rename a private helper."""
        self.assertRegex(SOURCE, r"_maps_api_key\s*=\s*_geocoding_api_key")

    def test_a_refused_key_is_logged_and_not_retried(self):
        """REQUEST_DENIED is a configuration fact, not a transient error. Retrying it just
        burns quota against a key that will refuse every time."""
        body = _segment(_function("_geocode_project"))
        self.assertIn("log_error", body)
        self.assertIn("return None", body)


class TestOnlyOneServerSideCaller(unittest.TestCase):
    def test_no_other_python_calls_a_google_web_service(self):
        """Every other key read in Python hands the key to a BROWSER, where a referrer
        restriction is correct. If a second server-side caller appears it needs this key
        too, and will otherwise fail the same silent way."""
        offenders = []
        for path in (APP).rglob("*.py"):
            if "tests" in path.parts or path.name == "sites.py":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            stripped = re.sub(r"#.*", "", text)
            if "maps.googleapis.com" in stripped:
                offenders.append(str(path.relative_to(APP)))
        self.assertEqual(
            offenders,
            [],
            "these make server-side Google calls and must use _geocoding_api_key(): "
            f"{offenders}",
        )


if __name__ == "__main__":
    unittest.main()
