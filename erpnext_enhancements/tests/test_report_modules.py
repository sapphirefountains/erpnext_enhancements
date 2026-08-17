"""Bench-free test: every Script Report is where Frappe will look for it.

Frappe does not read the directory to find a script report — it *computes* the
import path from the record: ``<app>/<scrub(module)>/report/<scrub(report_name)>/
<scrub(report_name)>.py``, and loads the ``.js`` from the matching path. Nothing
validates that at install time, so a folder named plausibly-but-wrongly installs
cleanly via ``bench migrate``, shows up in the Reports list, and only fails when
somebody opens it:

    ModuleNotFoundError: No module named
    'erpnext_enhancements.project_enhancements.report.hand_off_sla_compliance'

That is exactly how "Hand-Off SLA Compliance" and "Hand-Off Process Coverage"
shipped broken: ``frappe.scrub`` turns the hyphen into its own underscore
(``hand_off_``), while the obvious hand-written folder name is ``handoff_``.

So for every Report JSON shipped by this app, assert that:
  * ``report_name`` matches the record ``name`` (Report is named by its title —
    a mismatch means the JSON was hand-edited into an unreachable state),
  * the module is registered in ``modules.txt`` and the report sits under that
    module's directory,
  * the folder and the ``.py`` / ``.js`` / ``.json`` stems all equal
    ``scrub(report_name)``, and
  * a Script Report's module defines a top-level ``execute``.

Pure filesystem + json + ast — no frappe/bench needed.

Run: python -m unittest erpnext_enhancements.tests.test_report_modules
"""

import ast
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


def _iter_reports():
    """Yield (data, dir_module, folder, path) for every Report JSON in the app."""
    for path in APP_DIR.glob("**/report/*/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("doctype") != "Report":
            continue
        parts = path.relative_to(APP_DIR).parts
        dir_module = parts[parts.index("report") - 1]
        yield data, dir_module, path.parent.name, path


MODULES = _registered_modules()
REPORTS = list(_iter_reports())


class TestReportModules(unittest.TestCase):
    def test_found_reports(self):
        # Guard against a broken glob silently passing the checks below.
        self.assertGreater(len(REPORTS), 0, "no Report JSONs discovered under %s" % APP_DIR)

    def test_report_name_matches_record_name(self):
        for data, _dir, _folder, path in REPORTS:
            with self.subTest(report=path.stem):
                self.assertEqual(
                    data.get("report_name"),
                    data.get("name"),
                    "report_name %r != name %r (%s)" % (data.get("report_name"), data.get("name"), path),
                )

    def test_module_registered_in_modules_txt(self):
        for data, _dir, _folder, path in REPORTS:
            with self.subTest(report=data.get("report_name")):
                self.assertIn(
                    data.get("module"),
                    MODULES,
                    "%s declares module %r, which is not in modules.txt (%s)"
                    % (data.get("report_name"), data.get("module"), path),
                )

    def test_module_matches_directory(self):
        for data, dir_module, _folder, path in REPORTS:
            with self.subTest(report=data.get("report_name")):
                self.assertEqual(
                    _scrub(data.get("module", "")),
                    dir_module,
                    "%s declares module %r (-> %s) but lives under '%s/' (%s)"
                    % (
                        data.get("report_name"),
                        data.get("module"),
                        _scrub(data.get("module", "")),
                        dir_module,
                        path,
                    ),
                )

    def test_folder_and_filenames_are_scrubbed_report_name(self):
        for data, _dir, folder, path in REPORTS:
            report_name = data.get("report_name") or ""
            expected = _scrub(report_name)
            with self.subTest(report=report_name):
                self.assertEqual(
                    folder,
                    expected,
                    "%r must live in report/%s/, not report/%s/ — Frappe computes the "
                    "import path from the report name (%s)" % (report_name, expected, folder, path),
                )
                # Every sibling file Frappe loads by path must carry the same stem.
                # A Query Report has no controller — its whole definition is the
                # ``query`` in the JSON and Frappe never imports a module for it —
                # so the ``.py`` is required of Script Reports only.
                required = [".json"]
                if data.get("report_type") == "Script Report":
                    required.append(".py")
                if any(path.parent.glob("*.js")):
                    required.append(".js")
                for suffix in required:
                    sibling = path.parent / (expected + suffix)
                    self.assertTrue(
                        sibling.exists(),
                        "%r is missing %s (expected %s)" % (report_name, sibling.name, sibling),
                    )

    def test_script_reports_define_execute(self):
        for data, _dir, _folder, path in REPORTS:
            if data.get("report_type") != "Script Report":
                continue
            report_name = data.get("report_name") or ""
            controller = path.parent / (_scrub(report_name) + ".py")
            with self.subTest(report=report_name):
                self.assertTrue(controller.exists(), "%r has no controller at %s" % (report_name, controller))
                tree = ast.parse(controller.read_text(encoding="utf-8"))
                names = {
                    node.name
                    for node in tree.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                self.assertIn(
                    "execute",
                    names,
                    "%r is a Script Report but %s defines no top-level execute()"
                    % (report_name, controller),
                )

    def test_query_reports_define_query(self):
        """The counterpart of the ``execute`` check, for the other report type.

        ``Report.execute_query_report`` throws "Must specify a Query to run" when
        ``query`` is blank — which, like a missing controller, is a failure that
        only appears when somebody opens the report.
        """
        for data, _dir, _folder, path in REPORTS:
            if data.get("report_type") != "Query Report":
                continue
            with self.subTest(report=data.get("report_name")):
                query = (data.get("query") or "").strip()
                self.assertTrue(
                    query,
                    "%r is a Query Report with no query (%s)" % (data.get("report_name"), path),
                )
                # frappe.utils.safe_exec.check_safe_sql_query allows only these.
                self.assertTrue(
                    query.lower().startswith(("select", "explain", "with")),
                    "%r must start with SELECT/EXPLAIN/WITH — check_safe_sql_query "
                    "rejects anything else at run time (%s)" % (data.get("report_name"), path),
                )


if __name__ == "__main__":
    unittest.main()
