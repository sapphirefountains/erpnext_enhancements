"""Bench-free tests for what a new Project copies from its Opportunity, and the Lead warning.

Nik's answers in the Opportunity field review (2026-10-13):

* Primary Address is the job site and is copied onto the Project; a Billing Address is kept beside
  it (ERPNext's own ``customer_address``, relabeled) and copied too;
* Materials Budget, Time Budget, Time & Materials and Notes Relating to Budget are copied when the
  Project is created;
* a deal still on a Lead is warned about at Closed Won, because its Project would take the Lead as
  its customer.

Checked: the copy rule (``crm_enhancements/project_copy.py``); that both ways of making a Project
call it; that every field it names exists on both doctypes; the Billing Address placement on both
forms; and the Lead flag in ``handoff_state`` and the Closed Won prompt.

Run: python -m unittest erpnext_enhancements.tests.test_opportunity_project_copy -v
"""

import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.crm_enhancements import project_copy

APP = REPO_ROOT / "erpnext_enhancements"
CUSTOM_FIELDS = json.loads((APP / "fixtures" / "custom_field.json").read_text(encoding="utf-8"))
SETTERS = json.loads((APP / "fixtures" / "property_setter.json").read_text(encoding="utf-8"))
ERPNEXT_OPPORTUNITY = json.loads(
    (APP / "tests" / "fixtures" / "form_kit" / "erpnext_v16_opportunity.json").read_text(encoding="utf-8")
)["doctypes"]["Opportunity"]


def custom_fieldnames(doctype):
    return {row["fieldname"] for row in CUSTOM_FIELDS if row["dt"] == doctype}


def setter(name):
    return next((row for row in SETTERS if row["name"] == name), None)


def field_order(doctype):
    return json.loads(setter(f"{doctype}-main-field_order")["value"])


class FakeProject(dict):
    def set(self, key, value):
        self[key] = value


class TestTheCopy(unittest.TestCase):
    def test_copies_what_the_opportunity_has(self):
        opp = {
            "primary_address": "SITE-1",
            "customer_address": "BILL-1",
            "custom_materials_budget": 1200.0,
            "custom_time_budget_in_hours": "40",
            "custom_time__materials": 1,
            "custom_notes_relating_to_budget": "<p>Deposit 50%</p>",
        }
        project = FakeProject()
        copied = project_copy.copy_site_and_budget(opp, project)
        self.assertEqual(project["primary_address"], "SITE-1")
        self.assertEqual(project["custom_billing_address"], "BILL-1")
        self.assertEqual(project["custom_materials_budget"], 1200.0)
        self.assertEqual(project["custom_time__materials"], 1)
        self.assertEqual(len(copied), 6)

    def test_never_overwrites_and_never_copies_a_blank(self):
        project = FakeProject(primary_address="FROM-TEMPLATE", custom_materials_budget=500)
        project_copy.copy_site_and_budget(
            {"primary_address": "SITE-1", "customer_address": "", "custom_materials_budget": 0, "custom_time__materials": 0},
            project,
        )
        self.assertEqual(project, {"primary_address": "FROM-TEMPLATE", "custom_materials_budget": 500})

    def test_every_field_exists_on_both_doctypes(self):
        opportunity = custom_fieldnames("Opportunity") | {f["fieldname"] for f in ERPNEXT_OPPORTUNITY["fields"]}
        # primary_address is created by setup/custom_fields.create_unified_tabs on both doctypes.
        opportunity.add("primary_address")
        project = custom_fieldnames("Project") | {"primary_address"}
        setup = (APP / "setup" / "custom_fields.py").read_text(encoding="utf-8")
        self.assertIn('"primary_address"', setup)
        for source, target in project_copy.SITE_AND_BUDGET.items():
            with self.subTest(source=source):
                self.assertIn(source, opportunity)
                self.assertIn(target, project)


class TestBothCreationPathsCopy(unittest.TestCase):
    def test_the_background_creator(self):
        source = (APP / "crm_enhancements" / "api.py").read_text(encoding="utf-8")
        loop = source.index("for source_field, target_field in direct_mappings.items():")
        self.assertIn("copy_site_and_budget(opp, project)", source[loop : loop + 600])

    def test_the_desk_create_project(self):
        source = (APP / "opportunity_enhancements.py").read_text(encoding="utf-8")
        body = source[source.index("def make_project") :]
        self.assertIn("copy_site_and_budget(source, target)", body)
        self.assertIn("SITE_AND_BUDGET", body)


class TestBillingAddressOnTheForms(unittest.TestCase):
    def test_the_opportunity_shows_customer_address_as_billing_address_beside_the_site(self):
        self.assertEqual(setter("Opportunity-customer_address-label")["value"], "Billing Address")
        self.assertEqual(setter("Opportunity-customer_address-hidden")["value"], "0")
        order = field_order("Opportunity")
        self.assertEqual(order[order.index("primary_address") + 1], "customer_address")

    def test_the_project_has_a_billing_address_beside_its_site(self):
        (row,) = [r for r in CUSTOM_FIELDS if r["name"] == "Project-custom_billing_address"]
        self.assertEqual((row["fieldtype"], row["options"], row["hidden"]), ("Link", "Address", 0))
        order = field_order("Project")
        self.assertEqual(order[order.index("primary_address") + 1], "custom_billing_address")

    def test_the_help_and_description_agree(self):
        help_file = json.loads((APP / "form_kit" / "help_text" / "opportunity.json").read_text(encoding="utf-8"))
        hint = help_file["fields"]["customer_address"]["hint"]
        self.assertEqual(setter("Opportunity-customer_address-description")["value"], hint)
        for fieldname in ("primary_address", "custom_materials_budget", "custom_time_budget_in_hours"):
            with self.subTest(fieldname=fieldname):
                self.assertIn("copied onto the Project", help_file["fields"][fieldname]["help"])


class TestTheLeadWarning(unittest.TestCase):
    def test_handoff_state_reports_on_lead_on_both_branches(self):
        source = (APP / "crm_enhancements" / "handoff.py").read_text(encoding="utf-8")
        body = source[source.index("def handoff_state") :]
        body = body[: body.index("\ndef ", 10)]
        self.assertIn('"opportunity_from") == "Lead"', body)
        self.assertIn('return {"available": False, "is_historical": historical, "on_lead": on_lead}', body)
        self.assertIn('"on_lead": on_lead,', body)

    def test_the_closed_won_prompt_warns_above_its_question(self):
        source = (APP / "public" / "js" / "crm_enhancements" / "create_project_prompt.js").read_text(encoding="utf-8")
        self.assertIn("state && state.on_lead", source)
        self.assertIn("This deal is still on a Lead.", source)
        self.assertIn("const message = lead_warning + body;", source)


if __name__ == "__main__":
    unittest.main()
