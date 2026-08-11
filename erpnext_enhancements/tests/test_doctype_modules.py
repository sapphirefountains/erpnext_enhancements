"""Bench-free test: every custom DocType sits in its declared module, and has a controller.

For each DocType JSON shipped by this app, assert that:
  * it lives under its module's directory — Frappe maps a doctype's ``module``
    to ``<app>/<scrub(module)>/doctype/<name>/`` — and
  * that module is registered in ``modules.txt`` (i.e. owned by this app), and
  * a sibling ``<name>.py`` and ``__init__.py`` exist beside the JSON.

The first two guard against a new DocType being dropped in the wrong module folder,
or carrying a stale/typo'd ``module`` field, either of which makes ``bench migrate``
place or own it incorrectly.

The third is here because its absence took production's deploy pipeline down.
Frappe calls ``load_doctype_module`` for **every** DocType during migrate, child
tables included, and raises ``ModuleNotFoundError`` when the controller file is
missing. v1.268.0 shipped ``chat_retrieval_audit_room.json`` with an ``__init__.py``
and no ``chat_retrieval_audit_room.py``: the migrate aborted partway, leaving the
child DocType registered without its parent, and **every subsequent deploy of any
change failed the same way** — because the orphaned DocType is re-walked on every
migrate. Nothing in CI noticed, because nothing in CI runs a migrate.

A child table is the easy one to get wrong: it needs no logic, so an empty
controller feels redundant right up until the deploy fails.

Pure filesystem + json — no frappe/bench needed.

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

    def test_every_doctype_has_a_controller_module(self):
        """The one that would have caught the v1.268.0 deploy failure.

        ``bench migrate`` imports a controller for every DocType, child tables
        included, and aborts the whole migrate when one is missing — leaving a
        half-applied schema and a pipeline that fails identically on every
        subsequent deploy until the file appears. CI never runs a migrate, so this
        filesystem check is the only place it can be caught before production.
        """
        for name, _module, _dir, path in DOCTYPES:
            with self.subTest(doctype=name):
                controller = path.with_suffix(".py")
                self.assertTrue(
                    controller.exists(),
                    "%s has no controller at %s. bench migrate calls "
                    "load_doctype_module() for every DocType -- including child tables, "
                    "which is the case that looks redundant -- and raises "
                    "ModuleNotFoundError without it, aborting the migrate partway and "
                    "blocking every later deploy. An empty Document subclass is enough."
                    % (name, controller.name),
                )
                self.assertTrue(
                    (path.parent / "__init__.py").exists(),
                    "%s is missing __init__.py, so its directory is not an importable "
                    "package and the controller cannot be found even once it exists (%s)"
                    % (name, path.parent),
                )


if __name__ == "__main__":
    unittest.main()
