"""Every DocType's controller class is named exactly what Frappe will look for.

Frappe does not title-case a DocType name to find its controller. It strips characters::

    classname = doctype.replace(" ", "").replace("-", "")
    class_ = getattr(module, classname, None)
    ...
    if class_ is None:
        raise ImportError(...)

So **"Project Scope of Work" resolves to ``ProjectScopeofWork``** — lower-case ``o``, because
"of" was lower-case in the DocType name and nothing upper-cases it. A class called
``ProjectScopeOfWork`` is invisible.

Why this is a build-failing test and not a lint

The consequence is not a broken form. ``remove_orphan_doctypes()`` runs on **every**
``bench migrate``: it calls ``get_controller()`` on every non-custom DocType and passes anything
raising ``ImportError`` or ``DoesNotExistError`` straight to
``frappe.delete_doc(doctype, name, force=True)``.

So a one-letter mismatch means the DocType is created by model sync and **force-deleted in the
same migrate**. That happened here twice — v1.452.1 and again on v1.452.2 — and each time:

* the deploy exited 0 and the version string read correctly;
* nothing reached the Error Log;
* the **table survived**, because MariaDB DDL auto-commits and outlives the rollback of the row
  that caused it, leaving a 26-column table with no DocType behind it;
* a Custom Field elsewhere was left as a Link to a target that no longer existed.

The only trace was a row in ``Deleted Document``.

It also resisted the obvious check. Importing the controller by hand looked fine, because the
hand-written check asked for the class name *we* chose rather than the one Frappe derives.
Asking the right question is the entire content of this file.

Run: python -m unittest erpnext_enhancements.tests.test_doctype_controller_names
"""

import json
import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Frappe's own derivation, copied rather than imported so this suite needs no bench.
#: If upstream ever changes it, this constant is the single place to follow.
def frappe_classname(doctype: str) -> str:
    return doctype.replace(" ", "").replace("-", "")


def _doctype_definitions():
    """(name, json path, controller path) for every DocType this app ships."""
    found = []
    for path in APP_ROOT.glob("*/doctype/*/*.json"):
        if path.stem != path.parent.name:
            continue
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("doctype") != "DocType":
            continue
        found.append((data["name"], path, path.with_suffix(".py")))
    return found


class TestControllerClassNames(unittest.TestCase):
    def test_the_corpus_is_not_empty(self):
        """Every check below is 'no DocType does X', which is vacuously true on an empty glob."""
        self.assertGreater(
            len(_doctype_definitions()), 200, "the DocType glob has stopped matching"
        )

    def test_every_controller_defines_the_class_frappe_will_look_for(self):
        offenders = []
        for name, _json_path, controller in _doctype_definitions():
            if not controller.exists():
                offenders.append(f"{name}: no {controller.name}")
                continue
            source = controller.read_text(encoding="utf-8")
            expected = frappe_classname(name)
            if not re.search(rf"^class\s+{re.escape(expected)}\b", source, re.M):
                defined = re.findall(r"^class\s+(\w+)", source, re.M)
                offenders.append(f"{name}: expected class {expected!r}, file defines {defined}")

        self.assertEqual(
            offenders,
            [],
            "Frappe resolves a controller as doctype.replace(' ', '').replace('-', '') and does "
            "NOT title-case. A mismatch makes get_controller raise ImportError, and "
            "remove_orphan_doctypes() force-deletes any DocType that raises -- on every "
            "migrate, silently, leaving its table behind.\n  " + "\n  ".join(offenders),
        )

    def test_the_lower_case_word_case_is_actually_covered(self):
        """Guards the guard: without a DocType whose name contains a lower-case word, the check
        above would pass even if it only compared title-cased names."""
        tricky = [n for n, _j, _c in _doctype_definitions() if re.search(r"\s(of|and|to|for|in|on)\s", n)]
        self.assertTrue(
            tricky,
            "No DocType name contains a lower-case connecting word any more. If that is "
            "deliberate, this test can go; if a rename removed the only one, the check above "
            "is no longer exercising the case it exists for.",
        )
        for name in tricky:
            self.assertNotEqual(
                frappe_classname(name),
                name.title().replace(" ", ""),
                f"{name}: derivation and title-casing agree, so it proves nothing",
            )

    def test_derivation_matches_frappe_for_known_shapes(self):
        """Pins the rule itself against examples, so a wrong 'fix' to the helper is caught."""
        self.assertEqual(frappe_classname("Project Scope of Work"), "ProjectScopeofWork")
        self.assertEqual(frappe_classname("Hand-Off Attendee Role"), "HandOffAttendeeRole")
        self.assertEqual(frappe_classname("Quality Action"), "QualityAction")
        self.assertEqual(frappe_classname("Non Conformance"), "NonConformance")


if __name__ == "__main__":
    unittest.main()
