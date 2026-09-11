# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Chemicals, PPE and site hazards — WI-073 H.

All three land on the visit wizard's **existing** safety step. That step already
exists, technicians already cannot proceed past it, and a second safety screen is
one people learn to click through twice as fast.

Two properties carry the weight:

* **everything is derived from a record, never typed.** A hand-kept list of what
  is at a site is wrong within a month, and a wrong SDS list is worse than none
  because it reads as authoritative — somebody checks it, finds the product in
  their hand is not on it, and concludes it is harmless;
* **reporting a hazard warns the next person**, which is the half that makes it
  worth thirty seconds. A hazard report that only files a ticket protects nobody
  standing at that hatch tomorrow.

Run: python -m unittest erpnext_enhancements.tests.test_hr_hazards
"""

import ast
import io
import json
import re
import tokenize
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
MODULE = APP / "hr_enhancements"
DT = MODULE / "doctype"

HAZARDS = MODULE / "hazards.py"
HAZARD_JSON = DT / "site_hazard/site_hazard.json"
HAZARD_PY = DT / "site_hazard/site_hazard.py"
PPE_JSON = DT / "ppe_hazard_assessment/ppe_hazard_assessment.json"
PPE_PY = DT / "ppe_hazard_assessment/ppe_hazard_assessment.py"
PPE_ROW_JSON = DT / "ppe_requirement/ppe_requirement.json"
WIZARD = APP / "sapphire_maintenance/page/visit_wizard/visit_wizard.js"
FIXTURES = APP / "fixtures/custom_field.json"


def _text(path):
    return path.read_text(encoding="utf-8")


def _js(path):
    return re.sub(r"//.*$", "", _text(path), flags=re.M)


def _src(path):
    kept = [
        t
        for t in tokenize.generate_tokens(io.StringIO(_text(path)).readline)
        if t.type != tokenize.COMMENT
    ]
    tree = ast.parse(tokenize.untokenize(kept))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = ""
    return ast.unparse(tree)


def _fn(name, path):
    src = _text(path)
    lines = src.splitlines()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            stmts = node.body
            if (
                stmts
                and isinstance(stmts[0], ast.Expr)
                and isinstance(stmts[0].value, ast.Constant)
                and isinstance(stmts[0].value.value, str)
            ):
                stmts = stmts[1:]
            return "\n".join(lines[stmts[0].lineno - 1 : node.end_lineno]) if stmts else ""
    raise AssertionError(f"{name} not found in {path.name}")


def _fields(path):
    return {f["fieldname"]: f for f in json.loads(_text(path))["fields"]}


class TestTheChemicalListIsDerived(unittest.TestCase):
    """A hand-maintained list of what is at a site is wrong within a month, and a
    wrong SDS list is worse than none because it reads as authoritative.
    """

    def test_it_comes_from_the_visit_consumables(self):
        body = _fn("_sheets_for", HAZARDS)
        self.assertIn('visit.get("consumables")', body)
        self.assertIn("custom_is_chemical", body)

    def test_there_is_no_hand_kept_site_chemical_list(self):
        """No `Site Chemical` doctype, no child table of products per site."""
        import glob

        names = set()
        for path in glob.glob(str(MODULE / "doctype/*/*.json")):
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            if doc.get("doctype") == "DocType":
                names.add(doc["name"])
        for invented in ("Site Chemical", "Chemical Register", "Site Chemical List"):
            with self.subTest(doctype=invented):
                self.assertNotIn(invented, names)

    def test_a_missing_sheet_is_said_out_loud(self):
        """The common case at first, and pretending otherwise makes the whole panel
        untrustworthy."""
        body = _fn("_sheets_for", HAZARDS)
        self.assertIn("missing_document", body)
        self.assertIn("no safety sheet on file", _js(WIZARD))

    def test_the_sds_fields_exist_on_item(self):
        names = {f.get("name") for f in json.loads(_text(FIXTURES))}
        for field in (
            "Item-custom_is_chemical",
            "Item-custom_sds_document",
            "Item-custom_hazard_summary",
            "Item-custom_sds_reviewed_on",
        ):
            with self.subTest(field=field):
                self.assertIn(field, names)

    def test_the_hazard_summary_is_one_line_not_the_sheet(self):
        """Nobody reads sixteen pages of SDS standing in the sun."""
        field = next(
            f for f in json.loads(_text(FIXTURES)) if f.get("name") == "Item-custom_hazard_summary"
        )
        self.assertEqual(field["fieldtype"], "Small Text")


class TestThePPEAssessmentIsWorthKeeping(unittest.TestCase):
    def test_it_is_per_kind_of_work_not_per_site(self):
        """Draining a basin is the same job at every fountain, and a per-site copy
        is sixteen documents that drift."""
        self.assertIn("work_type", _fields(PPE_JSON))
        self.assertEqual(json.loads(_text(PPE_JSON))["autoname"], "field:work_type")

    def test_it_names_who_assessed(self):
        """The rule asks for a written CERTIFICATION, and one with nobody's name on
        it certifies nothing."""
        fields = _fields(PPE_JSON)
        self.assertEqual(fields["assessed_by"].get("reqd"), 1)
        self.assertEqual(fields["assessed_on"].get("reqd"), 1)

    def test_hazards_without_ppe_is_refused(self):
        """Half a document, and the half that reads as complete."""
        body = _fn("_require_ppe", PPE_PY)
        self.assertIn("frappe.throw", body)

    def test_each_row_states_the_hazard_it_is_for(self):
        """PPE with no stated hazard is PPE somebody talks themselves out of."""
        self.assertIn("because", _fields(PPE_ROW_JSON))

    def test_the_list_reaches_the_screen_it_is_needed_on(self):
        self.assertIn("_ppe_for", _fn("safety_brief", HAZARDS))
        self.assertIn("PPE for this work", _js(WIZARD))


class TestAHazardWarnsTheNextPerson(unittest.TestCase):
    """The half that makes reporting one worth thirty seconds."""

    def test_open_hazards_are_returned_for_the_site(self):
        body = _fn("_hazards_at", HAZARDS)
        self.assertIn('"customer": customer', body)
        self.assertIn("Open", body)

    def test_accepted_risk_still_shows(self):
        """A hazard nobody is going to fix is still one the next person needs to
        know about — dropping it is how "accepted" becomes "forgotten"."""
        self.assertIn("Accepted risk", _fn("_hazards_at", HAZARDS))

    def test_the_or_filter_is_a_list_not_a_dict(self):
        """`{"serial_no": a, "serial_no": b}` is a dict literal with a repeated key
        — the second silently replaces the first, so the feature-specific arm would
        never have matched. The same bug `test_hooks_integrity` caught in the
        scheduler earlier in this release, and Python warns about neither."""
        body = _fn("_hazards_at", HAZARDS)
        at = body.index("or_filters=")
        self.assertIn("[", body[at : at + 30])
        self.assertNotIn("or_filters={", body)

    def test_it_is_drawn_first_on_the_step(self):
        """The most perishable thing on the screen."""
        body = _js(WIZARD)
        at = body.index("paint_safety_brief")
        block = body[at : at + 2500]
        self.assertLess(block.index("brief.hazards"), block.index("brief.ppe"))

    def test_anybody_can_report_one_and_everybody_can_read_them(self):
        """A hazard report only managers can see cannot warn the next technician,
        which is the entire purpose."""
        perms = {p["role"]: p for p in json.loads(_text(HAZARD_JSON))["permissions"]}
        self.assertEqual(perms["Employee"].get("create"), 1)
        self.assertEqual(perms["Employee"].get("read"), 1)

    def test_the_reporter_and_time_are_stamped_once(self):
        self.assertIn("self.reported_by = frappe.session.user", _fn("before_insert", HAZARD_PY))

    def test_reporting_asks_for_one_thing_it_cannot_reconstruct(self):
        body = _fn("report_hazard", HAZARDS)
        self.assertEqual(body.count("frappe.throw"), 1)

    def test_the_notification_failing_does_not_lose_the_warning(self):
        """The banner warns the next technician either way — the half that does not
        depend on anybody reading an email."""
        body = _fn("_notify_hazard", HAZARDS)
        self.assertIn("except Exception", body)


class TestItLandsOnTheStepThatAlreadyExists(unittest.TestCase):
    def test_the_brief_is_on_the_safety_step(self):
        body = _js(WIZARD)
        at = body.index("render_safety_brief()")
        self.assertIn("$ack", body[max(0, at - 400) : at])

    def test_it_draws_async_and_cannot_block_the_step(self):
        """The step must draw immediately with the red banner; the brief is an
        addition to it rather than a precondition for it."""
        body = _js(WIZARD)
        at = body.index("render_safety_brief() {")
        block = body[at : at + 1200]
        self.assertIn(".catch(", block)

    def test_every_endpoint_has_a_caller(self):
        tree = ast.parse(_text(HAZARDS))
        whitelisted = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if getattr(target, "attr", None) == "whitelist":
                    whitelisted.add(node.name)
        self.assertTrue(whitelisted)
        callers = _js(WIZARD)
        for name in sorted(whitelisted):
            with self.subTest(endpoint=name):
                self.assertIn(f"hazards.{name}", callers)

    def test_every_section_fails_independently(self):
        """A brief with two of three parts is still worth reading, and an exception
        would take the safety step down with it."""
        for fn in ("_sheets_for", "_ppe_for", "_hazards_at"):
            with self.subTest(fn=fn):
                self.assertIn("except Exception", _fn(fn, HAZARDS))


if __name__ == "__main__":
    unittest.main()
