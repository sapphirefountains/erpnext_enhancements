"""Bench-free tests for change orders (WI-075 sub-phase K).

A change order is a commercial instrument, so every failure here is a wrong number somebody
argues about months later rather than a crash somebody fixes the same afternoon.

* **A credit totalled as an addition.** The tempting shape is a positive magnitude plus an
  Addition/Credit Select. The first time those two disagree, the change-order value on the
  dashboard is simply wrong and nothing says so.
* **A global counter.** A naming series would make the third change order on one job `CO-047`.
* **A stored status beside `docstatus`.** Two states for one document, free to disagree.
* **An amended change order.** Frappe appends `-1`, producing a second commercial instrument
  with almost the same name as the first.

Run: python -m unittest erpnext_enhancements.tests.test_change_orders
"""

import io
import json
import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import change_orders as co

DOCTYPE_DIR = APP_ROOT / "project_enhancements" / "doctype" / "change_order"


class TestNumbering(unittest.TestCase):
    def test_the_first_change_order_on_a_project_is_one(self):
        self.assertEqual(co.next_number([]), 1)
        self.assertEqual(co.next_number(None), 1)

    def test_it_continues_from_the_highest_used(self):
        self.assertEqual(co.next_number([1, 2, 3]), 4)

    def test_a_gap_is_preserved_rather_than_filled(self):
        """If CO-002 was voided, reusing 2 puts two different documents behind one number in
        somebody's inbox."""
        self.assertEqual(co.next_number([1, 3]), 4)

    def test_unusable_values_are_skipped_not_crashed_on(self):
        self.assertEqual(co.next_number([1, None, "", "abc", 2]), 3)

    def test_string_numbers_are_accepted(self):
        self.assertEqual(co.next_number(["1", "2"]), 3)

    def test_the_name_is_zero_padded_so_a_list_sorts_readably(self):
        self.assertEqual(co.co_name("PRJ-00580", 3), "PRJ-00580-CO-003")
        self.assertEqual(co.co_name("PRJ-00580", 12), "PRJ-00580-CO-012")

    def test_the_name_carries_the_project_not_a_global_counter(self):
        self.assertIn("PRJ-00580", co.co_name("PRJ-00580", 1))
        self.assertTrue(co.co_name("PRJ-00001", 1).endswith("-CO-001"))
        self.assertTrue(co.co_name("PRJ-99999", 1).endswith("-CO-001"))


class TestStatusIsDerived(unittest.TestCase):
    def test_a_new_one_is_draft(self):
        self.assertEqual(co.derive_status(0), co.STATUS_DRAFT)

    def test_an_approval_moves_it_to_pending(self):
        self.assertEqual(co.derive_status(0, pm_approved=True), co.STATUS_PENDING)
        self.assertEqual(co.derive_status(0, customer_approved=True), co.STATUS_PENDING)

    def test_submitting_locks_it(self):
        self.assertEqual(co.derive_status(1, True, True), co.STATUS_LOCKED)

    def test_executing_it_is_a_separate_fact_from_locking_it(self):
        """Locked means the scope changed; Executed means the work happened and was billed."""
        self.assertEqual(co.derive_status(1, True, True, executed_on="2026-09-14"), co.STATUS_EXECUTED)

    def test_cancelling_beats_everything(self):
        self.assertEqual(co.derive_status(2, True, True, "2026-09-14"), co.STATUS_CANCELED)

    def test_the_cancelled_spelling_is_the_house_one(self):
        """One 'l', matching every other cancelled-state label in this app."""
        self.assertEqual(co.STATUS_CANCELED, "Canceled")
        self.assertNotIn("Cancelled", co.STATUSES)

    def test_every_derived_status_is_one_the_select_offers(self):
        seen = {
            co.derive_status(0),
            co.derive_status(0, True),
            co.derive_status(1, True, True),
            co.derive_status(1, True, True, "2026-09-14"),
            co.derive_status(2),
        }
        self.assertEqual(seen - set(co.STATUSES), set())


class TestMoneyCarriesItsOwnSign(unittest.TestCase):
    def test_a_positive_impact_is_an_addition(self):
        self.assertEqual(co.impact_type(1500), co.IMPACT_ADDITION)

    def test_a_negative_impact_is_a_credit(self):
        """A customer removing scope. Stored negative, so it cannot be totalled as an addition."""
        self.assertEqual(co.impact_type(-1500), co.IMPACT_CREDIT)

    def test_zero_is_a_no_cost_change(self):
        """A real category: scope swapped like for like, schedule moved, price unchanged."""
        self.assertEqual(co.impact_type(0), co.IMPACT_NONE)
        self.assertEqual(co.impact_type(None), co.IMPACT_NONE)

    def test_an_unreadable_value_does_not_crash_a_save(self):
        self.assertEqual(co.impact_type("abc"), co.IMPACT_NONE)


class TestTotals(unittest.TestCase):
    def test_additions_and_credits_are_reported_separately(self):
        """A job that added 50k and credited 48k is a different job from one that barely
        changed, and a single net figure hides the difference."""
        rows = [{"cost_impact": 50000}, {"cost_impact": -48000}]
        out = co.totals(rows)
        self.assertEqual(out["addition"], 50000.0)
        self.assertEqual(out["credit"], 48000.0)
        self.assertEqual(out["net"], 2000.0)

    def test_a_credit_is_reported_as_a_positive_magnitude(self):
        self.assertEqual(co.totals([{"cost_impact": -1500}])["credit"], 1500.0)

    def test_schedule_days_add_up(self):
        rows = [{"schedule_impact_days": 3}, {"schedule_impact_days": 2}]
        self.assertEqual(co.totals(rows)["schedule_days"], 5)

    def test_our_own_causes_are_counted_separately(self):
        """'How much change did we cause' is a different question from 'how much did the job
        change', and only the first is a quality signal."""
        rows = [
            {"cause": "Customer Request", "cost_impact": 100},
            {"cause": "Design Error", "cost_impact": 200},
            {"cause": "Sapphire Error", "cost_impact": 300},
        ]
        out = co.totals(rows)
        self.assertEqual(out["internal_count"], 2)
        self.assertEqual(out["count"], 3)

    def test_an_unreadable_figure_does_not_poison_the_total(self):
        out = co.totals([{"cost_impact": "abc"}, {"cost_impact": 100}])
        self.assertEqual(out["net"], 100.0)

    def test_nothing_totals_to_zero(self):
        self.assertEqual(co.totals([])["count"], 0)
        self.assertEqual(co.totals(None)["net"], 0.0)

    def test_internal_causes_are_all_real_cause_options(self):
        """A typo here would silently count nothing as internal, forever."""
        self.assertEqual(set(co.INTERNAL_CAUSES) - set(co.CAUSES), set())


class TestBlockingReasons(unittest.TestCase):
    def _complete(self, **over):
        doc = {
            "project": "PRJ-00580",
            "cause": "Customer Request",
            "client_request_description": "Add two more nozzles to the east basin.",
            "pm_approved": 1,
            "customer_approved": 1,
        }
        doc.update(over)
        return doc

    def test_a_complete_change_order_is_not_blocked(self):
        self.assertEqual(co.blocking_reasons(self._complete()), [])

    def test_both_approvals_are_required_before_it_locks(self):
        """Submit is the lock: after it the criteria start appearing on inspections."""
        self.assertEqual(len(co.blocking_reasons(self._complete(pm_approved=0))), 1)
        self.assertEqual(len(co.blocking_reasons(self._complete(customer_approved=0))), 1)

    def test_the_cause_is_required_because_attribution_is_the_point(self):
        reasons = co.blocking_reasons(self._complete(cause=""))
        self.assertTrue(any("caused" in r for r in reasons))

    def test_a_blank_description_blocks(self):
        self.assertTrue(co.blocking_reasons(self._complete(client_request_description="   ")))

    def test_the_reasons_read_as_sentences_a_person_can_act_on(self):
        for reason in co.blocking_reasons({}):
            self.assertTrue(reason.endswith("."), reason)
            self.assertTrue(reason[0].isupper(), reason)


class TestUnassignedAddedCriteria(unittest.TestCase):
    def test_a_criterion_naming_no_milestone_is_reported(self):
        """Sold, and inspected by nothing. It matters more here than on the Scope of Work: a
        change order is the scope most likely to be agreed in a hurry and remembered by nobody."""
        rows = [{"criterion_key": "abc", "criterion": "Two extra nozzles", "inspect_at_milestone": ""}]
        self.assertEqual(co.unassigned_added_criteria(rows), [("abc", "Two extra nozzles")])

    def test_an_assigned_criterion_is_not_reported(self):
        rows = [{"criterion_key": "abc", "criterion": "x", "inspect_at_milestone": "build_pre_final"}]
        self.assertEqual(co.unassigned_added_criteria(rows), [])

    def test_whitespace_is_not_a_milestone(self):
        rows = [{"criterion_key": "abc", "criterion": "x", "inspect_at_milestone": "   "}]
        self.assertEqual(len(co.unassigned_added_criteria(rows)), 1)

    def test_empty_is_tolerated(self):
        self.assertEqual(co.unassigned_added_criteria([]), [])
        self.assertEqual(co.unassigned_added_criteria(None), [])


class TestTheDocTypeMatchesTheModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with io.open(DOCTYPE_DIR / "change_order.json", encoding="utf-8") as handle:
            cls.schema = json.load(handle)
        cls.fields = {f["fieldname"]: f for f in cls.schema["fields"]}
        cls.controller = (DOCTYPE_DIR / "change_order.py").read_text(encoding="utf-8")
        cls.code = re.sub(r'"""[\s\S]*?"""', "", cls.controller)

    def test_it_is_submittable_because_submit_is_the_lock(self):
        self.assertEqual(self.schema.get("is_submittable"), 1)

    def test_the_status_select_offers_exactly_the_derived_statuses(self):
        offered = [o for o in (self.fields["status"]["options"] or "").split("\n") if o.strip()]
        self.assertEqual(offered, list(co.STATUSES))

    def test_the_status_is_read_only_because_it_is_derived(self):
        self.assertEqual(self.fields["status"].get("read_only"), 1)

    def test_the_cause_select_offers_exactly_the_declared_causes(self):
        offered = [o for o in (self.fields["cause"]["options"] or "").split("\n") if o.strip()]
        self.assertEqual(offered, list(co.CAUSES))

    def test_the_impact_type_is_read_only_because_it_is_derived_from_the_sign(self):
        self.assertEqual(self.fields["cost_impact_type"].get("read_only"), 1)

    def test_the_cost_impact_is_not_forced_positive(self):
        """non_negative would make a credit impossible to record."""
        self.assertNotEqual(self.fields["cost_impact"].get("non_negative"), 1)

    def test_the_approval_stamps_are_read_only(self):
        """Server-stamped from the session and the server clock. The old client path on
        `complete_step` let the browser propose a timestamp, and the audit found retroactive
        box-checking."""
        for name in ("pm_approved", "pm_approved_by", "pm_approved_on", "customer_approved_on"):
            self.assertEqual(self.fields[name].get("read_only"), 1, name)

    def test_an_amendment_is_refused(self):
        """Frappe's amend appends -1, producing a second commercial instrument with almost the
        same name as the first."""
        self.assertIn("amended_from", self.code)
        self.assertRegex(self.code, r"amended_from[\s\S]{0,400}?frappe\.throw")

    def test_the_numbering_retry_catches_both_collision_exceptions(self):
        """A primary-key collision and a unique-index collision raise different exceptions in
        this framework, and catching only one makes the retry fail open."""
        self.assertIn("DuplicateEntryError", self.code)
        self.assertIn("UniqueValidationError", self.code)

    def test_the_controller_class_is_the_name_frappe_derives(self):
        self.assertIn("class ChangeOrder(", self.controller)
        self.assertEqual(self.schema["name"].replace(" ", "").replace("-", ""), "ChangeOrder")

    def test_it_lives_in_project_enhancements_not_quality(self):
        """The contract machinery is here, and a second contract system is exactly what this
        design exists to prevent."""
        self.assertEqual(self.schema["module"], "Project Enhancements")


if __name__ == "__main__":
    unittest.main()
