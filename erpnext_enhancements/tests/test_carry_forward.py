"""Bench-free tests for carry-forward and re-verification (WI-075 sub-phase F).

This is the mechanism that stops "we fixed it" being taken on faith, so the things that can go
wrong with it are all quiet ones:

* **A fix that closes without being looked at.** If `PM Resolved` were treated as done, the
  two-step closure the whole programme is built on would be one step wearing a longer name, and
  nothing would look different.
* **An escalation that ratchets twice.** A cancelled-and-resubmitted inspection must not raise a
  priority two steps for one failure, and priority is exactly the field nobody audits.
* **Two inspections carrying the same item.** Both answer it, the second submitted silently
  overwrites the first one's verdict, and the record shows one clean answer.

Run: python -m unittest erpnext_enhancements.tests.test_carry_forward
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import carry_forward as cf
from erpnext_enhancements.quality import lifecycle as L
from erpnext_enhancements.quality import merge


def _action(name="QA-1", subject="Reseal the north joint", **over):
    row = {
        "name": name,
        "custom_subject": subject,
        "status": L.ACTION_PM_RESOLVED,
        "custom_priority": "Medium",
        "custom_reopen_count": 0,
        "custom_punch_list": 0,
        "custom_verifying_inspection": "",
        "date": "2026-09-01",
    }
    row.update(over)
    return row


class TestTheDecisionTable(unittest.TestCase):
    def test_pass_closes_it(self):
        status, priority, reopens, keep = cf.decide("Pass", L.ACTION_PM_RESOLVED, "Medium", 0)
        self.assertEqual(status, L.ACTION_CLOSED)
        self.assertEqual(priority, "Medium")
        self.assertEqual(reopens, 0)

    def test_pass_keeps_the_claim_as_the_record_of_where_it_was_verified(self):
        """Also what makes re-submitting an amended inspection a no-op."""
        *_, keep = cf.decide("Pass", L.ACTION_PM_RESOLVED, "Medium", 0)
        self.assertTrue(keep)

    def test_fail_reopens_to_in_progress_not_to_open(self):
        """Somebody has already worked on this. Going back to Open loses that."""
        status, *_ = cf.decide("Fail", L.ACTION_PM_RESOLVED, "Medium", 0)
        self.assertEqual(status, L.ACTION_IN_PROGRESS)

    def test_fail_escalates_exactly_one_step(self):
        _s, priority, *_ = cf.decide("Fail", L.ACTION_PM_RESOLVED, "Medium", 0)
        self.assertEqual(priority, "High")

    def test_fail_counts_the_reopen(self):
        *_rest, reopens, _keep = cf.decide("Fail", L.ACTION_PM_RESOLVED, "Medium", 2)
        self.assertEqual(reopens, 3)

    def test_fail_releases_the_claim_so_it_is_carried_again(self):
        *_, keep = cf.decide("Fail", L.ACTION_PM_RESOLVED, "Medium", 0)
        self.assertFalse(keep)

    def test_fail_at_critical_does_not_wrap_around(self):
        _s, priority, *_ = cf.decide("Fail", L.ACTION_PM_RESOLVED, "Critical", 0)
        self.assertEqual(priority, "Critical")

    def test_no_answer_decides_nothing(self):
        status, priority, reopens, keep = cf.decide("", L.ACTION_PM_RESOLVED, "Medium", 4)
        self.assertEqual(status, L.ACTION_PM_RESOLVED)
        self.assertEqual(priority, "Medium")
        self.assertEqual(reopens, 4)
        self.assertFalse(keep, "an unanswered item must be carried again")

    def test_whitespace_is_not_an_answer(self):
        """PAD SPACE collation would treat '  ' as empty in SQL; it is stripped here too."""
        status, *_ = cf.decide("   ", L.ACTION_PM_RESOLVED, "Medium", 0)
        self.assertEqual(status, L.ACTION_PM_RESOLVED)

    def test_none_is_not_an_answer(self):
        status, *_ = cf.decide(None, L.ACTION_PM_RESOLVED, "Medium", 0)
        self.assertEqual(status, L.ACTION_PM_RESOLVED)

    def test_every_outcome_stays_inside_the_option_list(self):
        """A status outside the Select would make the action permanently unsaveable."""
        for result in ("Pass", "Fail", "", None, "Nonsense"):
            for priority in L.PRIORITIES:
                status, new_priority, _r, _k = cf.decide(result, L.ACTION_PM_RESOLVED, priority, 0)
                self.assertIn(status, L.ACTION_STATUSES)
                self.assertIn(new_priority, L.PRIORITIES)

    def test_repeated_failures_escalate_once_each(self):
        """The ratchet, stepped deliberately rather than by accident."""
        status, priority, reopens = L.ACTION_PM_RESOLVED, "Low", 0
        seen = []
        for _ in range(4):
            status, priority, reopens, _keep = cf.decide("Fail", status, priority, reopens)
            seen.append((priority, reopens))
            status = L.ACTION_PM_RESOLVED  # the PM fixes it again between inspections
        self.assertEqual(seen, [("Medium", 1), ("High", 2), ("Critical", 3), ("Critical", 4)])


class TestCarriedRows(unittest.TestCase):
    def test_a_row_is_produced_per_action(self):
        rows = cf.carried_rows([_action("QA-1"), _action("QA-2", "Re-aim the light")])
        self.assertEqual(len(rows), 2)

    def test_provenance_points_at_the_action(self):
        rows = cf.carried_rows([_action("QA-7")])
        self.assertEqual(rows[0]["source_key"], "C:QA-7")
        self.assertEqual(rows[0]["source"], merge.SOURCE_CARRIED)

    def test_a_punch_item_is_marked_as_one(self):
        """A punch item IS a Quality Action with a flag, and inherits this machinery."""
        rows = cf.carried_rows([_action(custom_punch_list=1)])
        self.assertTrue(rows[0]["label"].startswith("[Punch] "))

    def test_carried_rows_are_not_mandatory(self):
        """A mandatory row cannot be left blank, so an inspection at the pump vault would be
        unsubmittable because a fix in the plant room could not be checked from there. The spec
        is explicit that sign-off must not be blocked from moving a project forward."""
        rows = cf.carried_rows([_action()])
        self.assertEqual(rows[0]["is_mandatory"], 0)

    def test_a_failed_re_verification_owes_a_photo(self):
        rows = cf.carried_rows([_action()])
        self.assertEqual(rows[0]["requires_photo"], 1)
        self.assertEqual(merge.missing_evidence([dict(rows[0], outcome="Fail", photo="")]), [1])

    def test_a_passed_re_verification_does_not_owe_a_photo(self):
        rows = cf.carried_rows([_action()])
        self.assertEqual(merge.missing_evidence([dict(rows[0], outcome="Pass", photo="")]), [])

    def test_the_only_answers_are_pass_and_fail(self):
        """No N/A: 'not applicable' on a re-verification reads as neither closed nor failed, and
        the item would sit forever. Blank is the honest escape hatch and releases the claim."""
        rows = cf.carried_rows([_action()])
        self.assertEqual(merge.allowed_answers(rows[0]), ("Pass", "Fail"))

    def test_they_merge_after_the_template_and_the_contracted_criteria(self):
        master = [{"source": merge.SOURCE_MASTER, "source_key": "M:a:1", "label": "Std"}]
        addendum = [{"source": merge.SOURCE_ADDENDUM, "source_key": "A:s:1", "label": "Sold"}]
        rows = merge.merge(master, addendum, cf.carried_rows([_action()]))
        self.assertEqual(
            [r["source"] for r in rows],
            [merge.SOURCE_MASTER, merge.SOURCE_ADDENDUM, merge.SOURCE_CARRIED],
        )
        self.assertEqual([r["sequence"] for r in rows], [1, 2, 3])

    def test_carried_rows_do_not_collide_with_each_other(self):
        rows = merge.merge([], [], cf.carried_rows([_action("QA-1"), _action("QA-2")]))
        self.assertEqual(merge.duplicate_source_keys(rows), [])

    def test_no_actions_produces_no_rows(self):
        self.assertEqual(cf.carried_rows([]), [])
        self.assertEqual(cf.carried_rows(None), [])


class TestClaiming(unittest.TestCase):
    def test_only_pm_resolved_actions_are_selected(self):
        """An action still being worked on has nothing to verify; a closed one is finished."""
        filters = cf.claimable_filters("PRJ-1")
        self.assertEqual(filters["status"], L.ACTION_PM_RESOLVED)
        self.assertEqual(filters["custom_project"], "PRJ-1")

    def test_the_filter_carries_no_date_comparison(self):
        """A `<` on a nullable datetime silently matches every NULL row via the coalesce
        sentinel — and 'resolved a while ago' is not the rule anyway."""
        for value in filters_values(cf.claimable_filters("PRJ-1")):
            self.assertNotIn("<", str(value))
            self.assertNotIn(">", str(value))

    def test_an_unclaimed_action_is_claimable(self):
        self.assertTrue(cf.is_claimable(_action(), live_inspections=("QIR-1",)))

    def test_an_action_claimed_by_a_live_inspection_is_not(self):
        """Otherwise two inspections carry it, both answer it, and the second submitted
        silently overwrites the first one's verdict."""
        action = _action(custom_verifying_inspection="QIR-1")
        self.assertFalse(cf.is_claimable(action, live_inspections=("QIR-1", "QIR-2")))

    def test_a_stale_claim_from_a_cancelled_inspection_is_released(self):
        action = _action(custom_verifying_inspection="QIR-OLD")
        self.assertTrue(cf.is_claimable(action, live_inspections=("QIR-1",)))

    def test_no_live_inspections_means_every_claim_is_stale(self):
        action = _action(custom_verifying_inspection="QIR-OLD")
        self.assertTrue(cf.is_claimable(action, live_inspections=()))
        self.assertTrue(cf.is_claimable(action, live_inspections=None))


def filters_values(filters):
    for value in filters.values():
        if isinstance(value, (list, tuple)):
            yield from value
        else:
            yield value


if __name__ == "__main__":
    unittest.main()
