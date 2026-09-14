"""Bench-free tests for the merge and the freeze (WI-075 sub-phase D).

**The property this file exists for:** an inspection that has already been generated does not
change when the template it came from changes.

That is the promise the whole programme rests on — the build spec calls it "frozen snapshots",
and its reason is worth restating, because it is the reason this is a test and not a comment:
*templates and standards will evolve, but a specific inspection that already happened shouldn't
change retroactively because the master template changed the following week.*

It is also invisible. An implementation that re-reads the master at render looks completely
normal: the form populates, the checks are right, and nothing is wrong until the day somebody
tightens a tolerance and last year's passed inspections quietly become failures — or, worse,
last year's failures quietly become passes. Nothing logs it. Nobody notices until an insurer
or a subcontractor asks what was actually checked.

So the freeze is asserted directly, by mutating the source after generation and demanding the
generated rows and their hash are untouched.

Run: python -m unittest erpnext_enhancements.tests.test_inspection_merge
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import merge

MILESTONE = "Build — Pre-Final (Systems Startup)"
SCOPE = "SCOPE-2026-0001"


def _section_row(section, location_note=""):
    return {"section": section, "location_note": location_note}


def _item(item_key, label, **over):
    row = {
        "item_key": item_key,
        "label": label,
        "acceptance_criteria": f"{label} meets spec",
        "check_type": "Pass/Fail",
        "method": "Visual",
        "is_mandatory": 1,
        "requires_photo": 0,
    }
    row.update(over)
    return row


def _criterion(criterion_key, text, milestone=MILESTONE, **over):
    row = {
        "criterion_key": criterion_key,
        "criterion": text,
        "pass_standard": f"{text}, measured",
        "verification_method": "Measurement",
        "inspect_at_milestone": milestone,
        "is_hold_point": 0,
    }
    row.update(over)
    return row


def _library(**sections):
    """A stand-in for the section catalog: name -> list of checks."""
    return lambda name: sections.get(name, [])


def _generate(template_sections, library, criteria):
    """What the endpoint does, minus frappe: merge, then hash."""
    rows = merge.merge(
        merge.master_rows(template_sections, library),
        merge.addendum_rows(criteria, SCOPE, MILESTONE),
    )
    return rows, merge.spec_hash(rows)


class TestTheFreeze(unittest.TestCase):
    """The reason this module is pure. Each test mutates a source and demands nothing moved."""

    def setUp(self):
        self.checks = [_item("k1", "Basin holds water"), _item("k2", "GFCI trips")]
        self.sections = [_section_row("Commissioning")]
        self.criteria = [_criterion("c1", "Nozzle height 1.8m")]
        self.rows, self.hash = _generate(
            self.sections, _library(Commissioning=self.checks), self.criteria
        )

    def test_rewording_a_master_check_afterwards_changes_nothing(self):
        self.checks[0]["label"] = "Basin holds water (revised 2027)"
        self.assertEqual(self.rows[0]["label"], "Basin holds water")
        self.assertEqual(merge.spec_hash(self.rows), self.hash)

    def test_tightening_a_tolerance_afterwards_changes_nothing(self):
        """The one that would silently turn old passes into fails."""
        self.checks[0]["acceptance_criteria"] = "No drop over 72h"
        self.assertEqual(self.rows[0]["acceptance_criteria"], "Basin holds water meets spec")
        self.assertEqual(merge.spec_hash(self.rows), self.hash)

    def test_deleting_a_master_check_afterwards_changes_nothing(self):
        self.checks.pop()
        self.assertEqual(len(self.rows), 3)
        self.assertEqual(merge.spec_hash(self.rows), self.hash)

    def test_adding_a_master_check_afterwards_changes_nothing(self):
        self.checks.append(_item("k9", "Added later"))
        self.assertEqual(len(self.rows), 3)
        self.assertEqual(merge.spec_hash(self.rows), self.hash)

    def test_editing_the_locked_scope_afterwards_changes_nothing(self):
        self.criteria[0]["pass_standard"] = "2.4m"
        self.assertEqual(self.rows[2]["acceptance_criteria"], "Nozzle height 1.8m, measured")
        self.assertEqual(merge.spec_hash(self.rows), self.hash)

    def test_rows_are_copies_and_not_views(self):
        """Belt and braces: nothing in a generated row aliases a source dict."""
        for row in self.rows:
            self.assertNotIn(row, self.checks)
            self.assertNotIn(row, self.criteria)


class TestMergeOrderAndProvenance(unittest.TestCase):
    def test_master_comes_first_then_contracted_criteria(self):
        rows, _ = _generate(
            [_section_row("Commissioning")],
            _library(Commissioning=[_item("k1", "A")]),
            [_criterion("c1", "B")],
        )
        self.assertEqual([r["source"] for r in rows], [merge.SOURCE_MASTER, merge.SOURCE_ADDENDUM])

    def test_sequence_is_one_based_and_dense(self):
        rows, _ = _generate(
            [_section_row("Commissioning")],
            _library(Commissioning=[_item("k1", "A"), _item("k2", "B")]),
            [_criterion("c1", "C")],
        )
        self.assertEqual([r["sequence"] for r in rows], [1, 2, 3])

    def test_source_keys_identify_where_each_row_came_from(self):
        rows, _ = _generate(
            [_section_row("Commissioning")],
            _library(Commissioning=[_item("k1", "A")]),
            [_criterion("c1", "B")],
        )
        self.assertEqual(rows[0]["source_key"], "M:Commissioning:k1")
        self.assertEqual(rows[1]["source_key"], f"A:{SCOPE}:c1")

    def test_sections_are_walked_in_template_order(self):
        rows, _ = _generate(
            [_section_row("Second"), _section_row("First")],
            _library(First=[_item("f", "F")], Second=[_item("s", "S")]),
            [],
        )
        self.assertEqual([r["label"] for r in rows], ["S", "F"])

    def test_location_note_rides_along_from_the_template(self):
        rows, _ = _generate(
            [_section_row("Commissioning", "North vault")],
            _library(Commissioning=[_item("k1", "A")]),
            [],
        )
        self.assertEqual(rows[0]["location_note"], "North vault")

    def test_a_carried_row_can_be_appended_without_disturbing_the_others(self):
        """Sub-phase F writes these. The vocabulary exists now because adding a Select option
        once rows exist is a data migration, not an edit."""
        master = merge.master_rows([_section_row("C")], _library(C=[_item("k1", "A")]))
        carried = [{"source": merge.SOURCE_CARRIED, "source_key": merge.carried_key("QA-1"),
                    "label": "Fix the leak", "is_mandatory": 1}]
        rows = merge.merge(master, [], carried)
        self.assertEqual([r["source"] for r in rows], [merge.SOURCE_MASTER, merge.SOURCE_CARRIED])
        self.assertEqual(rows[1]["sequence"], 2)


class TestCriteriaSelection(unittest.TestCase):
    def test_only_criteria_for_this_milestone_are_included(self):
        criteria = [_criterion("c1", "Mine"), _criterion("c2", "Someone else's", milestone="Build — Final")]
        rows = merge.addendum_rows(criteria, SCOPE, MILESTONE)
        self.assertEqual([r["label"] for r in rows], ["Mine"])

    def test_a_criterion_with_no_milestone_is_reported_not_dropped(self):
        """A contracted promise that quietly never gets inspected is the exact failure this
        programme exists to end, so it is surfaced rather than filtered away in silence."""
        criteria = [_criterion("c1", "Assigned"), _criterion("c2", "Orphan", milestone="")]
        self.assertEqual(len(merge.addendum_rows(criteria, SCOPE, MILESTONE)), 1)
        self.assertEqual(merge.unassigned_criteria(criteria), [("c2", "Orphan")])

    def test_contracted_criteria_are_always_mandatory(self):
        rows = merge.addendum_rows([_criterion("c1", "Sold")], SCOPE, MILESTONE)
        self.assertEqual(rows[0]["is_mandatory"], 1)

    def test_a_hold_point_demands_a_photo(self):
        rows = merge.addendum_rows([_criterion("c1", "Gate", is_hold_point=1)], SCOPE, MILESTONE)
        self.assertEqual(rows[0]["requires_photo"], 1)


class TestSpecHash(unittest.TestCase):
    def test_identical_merges_hash_identically(self):
        a, _ = _generate([_section_row("C")], _library(C=[_item("k", "A")]), [])
        b, _ = _generate([_section_row("C")], _library(C=[_item("k", "A")]), [])
        self.assertEqual(merge.spec_hash(a), merge.spec_hash(b))

    def test_reordering_changes_the_hash(self):
        """Order is part of what was inspected, not presentation."""
        a = merge.merge(merge.master_rows([_section_row("C")], _library(C=[_item("1", "A"), _item("2", "B")])), [])
        b = merge.merge(merge.master_rows([_section_row("C")], _library(C=[_item("2", "B"), _item("1", "A")])), [])
        self.assertNotEqual(merge.spec_hash(a), merge.spec_hash(b))

    def test_a_changed_standard_changes_the_hash(self):
        a = merge.merge(merge.master_rows([_section_row("C")], _library(C=[_item("k", "A")])), [])
        b = merge.merge(
            merge.master_rows([_section_row("C")], _library(C=[_item("k", "A", acceptance_criteria="Tighter")])), []
        )
        self.assertNotEqual(merge.spec_hash(a), merge.spec_hash(b))

    def test_moving_a_location_note_does_not_change_the_hash(self):
        """Presentation is excluded on purpose: a moved note is not a different inspection."""
        a = merge.merge(merge.master_rows([_section_row("C", "North")], _library(C=[_item("k", "A")])), [])
        b = merge.merge(merge.master_rows([_section_row("C", "South")], _library(C=[_item("k", "A")])), [])
        self.assertEqual(merge.spec_hash(a), merge.spec_hash(b))

    def test_numeric_shape_does_not_change_the_hash(self):
        """Frappe hands back 0, 0.0 or Decimal depending on how the row was loaded. A hash that
        moved with that would report every unaltered inspection as altered."""
        a = merge.merge(merge.master_rows([_section_row("C")], _library(C=[_item("k", "A", min_value=0)])), [])
        b = merge.merge(merge.master_rows([_section_row("C")], _library(C=[_item("k", "A", min_value=0.0)])), [])
        self.assertEqual(merge.spec_hash(a), merge.spec_hash(b))

    def test_hash_is_stable_across_runs(self):
        """Pinned literal. If this ever changes, every stored hash in production stopped
        matching its own rows, and the failure would be silent."""
        rows = merge.merge(
            merge.master_rows([_section_row("Commissioning")], _library(Commissioning=[_item("k1", "Basin holds water")])),
            merge.addendum_rows([_criterion("c1", "Nozzle height 1.8m")], SCOPE, MILESTONE),
        )
        self.assertEqual(
            merge.spec_hash(rows),
            "57bf2e19fbbf1bfe8378e686f2ce4bd8a603438c84ec157f6f3d9e04084fc47e",
        )

    def test_empty_merge_still_hashes(self):
        self.assertTrue(merge.spec_hash([]))


class TestIntegrityChecks(unittest.TestCase):
    def test_a_section_listed_twice_produces_duplicate_provenance(self):
        rows = merge.merge(
            merge.master_rows([_section_row("C"), _section_row("C")], _library(C=[_item("k", "A")])), []
        )
        self.assertEqual(merge.duplicate_source_keys(rows), ["M:C:k"])

    def test_a_sound_merge_has_no_duplicates(self):
        rows, _ = _generate([_section_row("C")], _library(C=[_item("k1", "A"), _item("k2", "B")]), [_criterion("c1", "X")])
        self.assertEqual(merge.duplicate_source_keys(rows), [])

    def test_mandatory_count_matches_the_rows(self):
        rows, _ = _generate(
            [_section_row("C")],
            _library(C=[_item("k1", "A"), _item("k2", "B", is_mandatory=0)]),
            [_criterion("c1", "X")],
        )
        self.assertEqual(merge.mandatory_count(rows), 2)


class TestAnsweringAFrozenRow(unittest.TestCase):
    """What counts as a failure decides whether an NCR is raised, so it is asserted rather
    than left to a controller CI never runs."""

    def test_default_options_when_the_row_names_none(self):
        self.assertEqual(merge.allowed_answers({"options": ""}), ("Pass", "Fail", "N/A"))

    def test_a_rows_own_options_win(self):
        row = {"options": "Pass\nReplace\nDefer"}
        self.assertEqual(merge.allowed_answers(row), ("Pass", "Replace", "Defer"))

    def test_blank_lines_and_padding_in_options_are_ignored(self):
        row = {"options": "  Pass  \n\n Fail \n"}
        self.assertEqual(merge.allowed_answers(row), ("Pass", "Fail"))

    def test_na_is_not_a_failure(self):
        """The reason FAILING_ANSWERS is explicit rather than 'anything that is not Pass':
        N/A would otherwise raise a non-conformance for a check that did not apply."""
        rows = [{"outcome": "N/A", "is_mandatory": 1}]
        self.assertEqual(merge.tally(rows)["fail_count"], 0)

    def test_a_fail_counts_once_even_when_also_out_of_range(self):
        """Two reasons for one finding is still one finding; double-counting would inflate the
        failure rate that first-pass yield is computed from."""
        rows = [{"outcome": "Fail", "check_type": "Measurement", "measured_value": 99,
                 "max_value": 10, "is_mandatory": 1}]
        self.assertEqual(merge.tally(rows)["fail_count"], 1)

    def test_out_of_range_is_a_failure_whatever_was_selected(self):
        rows = [{"outcome": "Pass", "check_type": "Measurement", "measured_value": 99,
                 "max_value": 10, "is_mandatory": 1}]
        self.assertEqual(merge.tally(rows)["fail_count"], 1)

    def test_a_zero_bound_means_unbounded_not_a_limit_of_zero(self):
        rows = [{"check_type": "Measurement", "measured_value": 5, "min_value": 0, "max_value": 0}]
        self.assertFalse(merge.is_out_of_range(rows[0]))

    def test_a_non_measurement_row_is_never_out_of_range(self):
        row = {"check_type": "Pass/Fail", "measured_value": 99, "max_value": 1}
        self.assertFalse(merge.is_out_of_range(row))

    def test_completion_is_over_mandatory_rows_only(self):
        rows = [{"is_mandatory": 1, "outcome": "Pass"}, {"is_mandatory": 0, "outcome": ""}]
        self.assertEqual(merge.tally(rows)["completion_percent"], 100)

    def test_no_mandatory_rows_does_not_divide_by_zero(self):
        self.assertEqual(merge.tally([{"is_mandatory": 0}])["completion_percent"], 0)

    def test_unanswered_mandatory_reports_grid_positions(self):
        rows = [{"is_mandatory": 1, "outcome": "Pass"}, {"is_mandatory": 1, "outcome": "  "}]
        self.assertEqual(merge.unanswered_mandatory(rows), [2])

    def test_evidence_is_only_demanded_for_failures(self):
        """Demanding a photo of every passing check trains people to attach anything."""
        rows = [
            {"requires_photo": 1, "outcome": "Pass", "photo": ""},
            {"requires_photo": 1, "outcome": "Fail", "photo": ""},
            {"requires_photo": 1, "outcome": "Fail", "photo": "/files/x.jpg"},
        ]
        self.assertEqual(merge.missing_evidence(rows), [2])

if __name__ == "__main__":
    unittest.main()
