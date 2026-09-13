# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The won-opportunity nag has to count the same thing a human would, and link to it.

This alert has now been wrong twice, in the same direction — it over-reported — and each
time the number looked plausible enough to go unchallenged in a daily email.

    228   the original. A `<=` on the nullable `custom_date_closed_won` swept in every
          undated Zoho import through frappe's ifnull sentinel. Fixed v1.426.5.
     31   after the date fix, and still wrong: it asked about ONE of the three fields
          that can link an Opportunity to a Project.
     11   the honest figure.

Measured on production 2026-09-13:

    Opportunity.custom_created_project    76 rows   the one it asked about
    Opportunity.custom_project            16 rows   never consulted
    Project.custom_opportunity           105 rows   never consulted, and the MOST used

Of the 31 it was nagging, 12 carried `custom_project`, 17 were the target of a `Project`
back-link, 20 by one or the other. So two thirds of a daily reminder were deals that had
already been converted.

**The back-link is the one that matters and the one that is awkward**, and those are not
a coincidence. `Project.custom_opportunity` is a Link on the *Project* side, so it cannot
appear in a filter on Opportunity at all — it needs a second query and a set difference.
The two fields that fitted in the existing filter list were the two that got consulted.

`test_status_alerts.py` could not have caught this: it subclasses `FrappeTestCase`, so it
needs a bench and is named nowhere in `ci.yml`. This suite is bench-free and installs its
own frappe stub, so the logic is RUN rather than grepped.

Run: python -m unittest erpnext_enhancements.tests.test_unconverted_nag
"""

import ast
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SOURCE = APP / "status_alerts.py"


def setUpModule():
    """Enough frappe to import `status_alerts` and call the two helpers for real."""
    fake = types.ModuleType("frappe")
    fake._ = lambda text: text
    fake.flags = types.SimpleNamespace(
        in_migrate=False, in_install=False, in_patch=False, in_import=False
    )
    fake.get_all = lambda *a, **k: []
    fake.db = types.SimpleNamespace(exists=lambda *a, **k: True)
    fake.get_single = lambda *a, **k: types.SimpleNamespace(get=lambda *x: None)
    fake.enqueue = lambda *a, **k: None
    sys.modules["frappe"] = fake

    utils = types.ModuleType("frappe.utils")
    utils.add_to_date = lambda *a, **k: "2026-09-12"
    utils.cint = lambda v: int(v or 0)
    utils.get_url = lambda path="": "https://example.invalid" + path
    utils.get_url_to_form = lambda dt, name: f"https://example.invalid/app/{dt}/{name}"
    utils.getdate = lambda d=None: d
    utils.now_datetime = lambda: "2026-09-13 00:00:00"
    utils.today = lambda: "2026-09-13"
    sys.modules["frappe.utils"] = utils
    fake.utils = utils

    style = types.ModuleType("erpnext_enhancements.email_style")
    sys.modules["erpnext_enhancements.email_style"] = style

    flags = types.ModuleType("erpnext_enhancements.feature_flags")
    flags.process_automation_enabled = lambda: False
    sys.modules["erpnext_enhancements.feature_flags"] = flags


def _module():
    from erpnext_enhancements import status_alerts

    return status_alerts


def _fn_source(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


class _Opp(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


class TestAllThreeLinkagePathsAreConsulted(unittest.TestCase):
    def test_both_opportunity_side_fields_are_in_the_filter(self):
        """`custom_created_project` was asked about and `custom_project` was not. Both
        are Link-to-Project on Opportunity; either one means the deal has a project."""
        body = _fn_source("nag_unconverted_opportunities")
        self.assertIn("custom_created_project", body)
        self.assertIn("custom_project", body)
        self.assertIn("is', 'not set'", body.replace('"', "'"))

    def test_the_back_link_is_consulted_too(self):
        """The third path, and the only one that cannot be a filter on Opportunity."""
        body = _fn_source("nag_unconverted_opportunities")
        self.assertIn("_drop_back_linked", body)


class TestDropBackLinked(unittest.TestCase):
    """RUN, not grepped. A set difference is the kind of thing that reads correct and
    is off by one direction."""

    def setUp(self):
        self.mod = _module()
        self.fake = sys.modules["frappe"]

    def _run(self, candidates, linked):
        self.fake.get_all = lambda *a, **k: list(linked)
        return self.mod._drop_back_linked([_Opp(name=n) for n in candidates])

    def test_it_removes_exactly_the_back_linked_ones(self):
        kept = self._run(["A", "B", "C"], ["B"])
        self.assertEqual([o["name"] for o in kept], ["A", "C"])

    def test_it_keeps_everything_when_no_project_points_at_any(self):
        kept = self._run(["A", "B"], [])
        self.assertEqual([o["name"] for o in kept], ["A", "B"])

    def test_it_can_empty_the_list(self):
        """The case that matters most: if every candidate is linked, the alert must not
        fire at all. `nag_unconverted_opportunities` returns on an empty list."""
        self.assertEqual(self._run(["A", "B"], ["A", "B"]), [])

    def test_an_empty_candidate_list_short_circuits(self):
        """No query at all — `["in", []]` is a filter worth never sending."""
        called = []
        self.fake.get_all = lambda *a, **k: called.append(1) or []
        self.assertEqual(self.mod._drop_back_linked([]), [])
        self.assertEqual(called, [])

    def test_it_asks_for_every_row(self):
        """`limit_page_length=0`. Without it frappe caps at 20 and the difference
        UNDER-drops — the alert would silently go back to over-reporting once more than
        20 Projects carry a back-link. 105 do."""
        seen = {}
        self.fake.get_all = lambda *a, **k: seen.update(k) or []
        self.mod._drop_back_linked([_Opp(name="A")])
        self.assertEqual(seen.get("limit_page_length"), 0)


class TestTheLinkShowsWhatWasCounted(unittest.TestCase):
    """A link that disagrees with the sentence above it teaches people to distrust both.
    That defect was fixed once already in v1.426.5, by a predicate URL — which this
    change would have re-broken, because the set is now decided partly in Python."""

    def setUp(self):
        self.mod = _module()

    def test_it_links_by_name_when_it_has_the_names(self):
        url = self.mod._unconverted_list_url(["CRM-OPP-1", "CRM-OPP-2"])
        self.assertIn("name=", url)
        self.assertIn("CRM-OPP-1", url)
        self.assertIn("CRM-OPP-2", url)

    def test_the_name_filter_is_frappe_list_view_syntax(self):
        """`list_view.js` JSON.parses a value that starts `[` and ends `]`, which is how
        an `["in", [...]]` deep link works at all."""
        from urllib.parse import parse_qs, urlparse

        url = self.mod._unconverted_list_url(["CRM-OPP-1"])
        value = parse_qs(urlparse(url).query)["name"][0]
        import json as _json

        self.assertEqual(_json.loads(value), ["in", ["CRM-OPP-1"]])

    def test_it_still_has_a_predicate_form_for_no_names(self):
        url = self.mod._unconverted_list_url(None)
        self.assertIn("status=", url)
        self.assertIn("custom_created_project", url)


class TestItIsWiredIntoCi(unittest.TestCase):
    def test_ci_runs_this_suite(self):
        """The bench-only `test_status_alerts.py` is named nowhere in ci.yml, which is
        why this file exists rather than an addition to that one."""
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_unconverted_nag", ci)


if __name__ == "__main__":
    unittest.main()
