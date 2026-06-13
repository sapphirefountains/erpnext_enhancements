"""Bench-free test: every custom DocType sits in its declared module.

For each DocType JSON shipped by this app, assert that:
  * it lives under its module's directory — Frappe maps a doctype's ``module``
    to ``<app>/<scrub(module)>/doctype/<name>/`` — and
  * that module is registered in ``modules.txt`` (i.e. owned by this app).

This guards against a new DocType being dropped in the wrong module folder, or
carrying a stale/typo'd ``module`` field, either of which makes ``bench migrate``
place or own it incorrectly. Pure filesystem + json — no frappe/bench needed.

Run: python -m unittest erpnext_enhancements.tests.test_doctype_modules
"""

import json
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]  # the erpnext_enhancements/ package


def _scrub(name):
    """Mirror frappe.scrub: spaces and hyphens to underscores, lowercased."""
    return name.replace(" ", "_").replace("-", "_").lower()


def _registered_modules():
    text = (APP_DIR / "modules.txt").read_text(encoding="utf-8")
    return {line.strip() for line in text.splitlines() if line.strip()}


def _iter_doctypes():
    """Yield (name, module, dir_module, path) for every DocType JSON in the app."""
    for path in APP_DIR.glob("**/doctype/*/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("doctype") != "DocType":
            continue
        parts = path.relative_to(APP_DIR).parts
        dir_module = parts[parts.index("doctype") - 1]
        yield data.get("name", path.stem), data.get("module", ""), dir_module, path


MODULES = _registered_modules()
DOCTYPES = list(_iter_doctypes())


class TestDoctypeModules(unittest.TestCase):
    def test_found_doctypes(self):
        # Guard against a broken glob silently passing the checks below.
        self.assertGreater(len(DOCTYPES), 0, "no DocType JSONs discovered under %s" % APP_DIR)

    def test_module_registered_in_modules_txt(self):
        for name, module, _dir, path in DOCTYPES:
            with self.subTest(doctype=name):
                self.assertIn(
                    module,
                    MODULES,
                    "%s declares module %r, which is not in modules.txt (%s)" % (name, module, path),
                )

    def test_module_matches_directory(self):
        for name, module, dir_module, path in DOCTYPES:
            with self.subTest(doctype=name):
                self.assertEqual(
                    _scrub(module),
                    dir_module,
                    "%s declares module %r (-> %s) but lives under '%s/' (%s)"
                    % (name, module, _scrub(module), dir_module, path),
                )


if __name__ == "__main__":
    unittest.main()
