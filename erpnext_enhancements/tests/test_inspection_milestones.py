"""Bench-free tests for the inspection milestone catalog (WI-075 sub-phase C).

The failure this exists for is the one with no error attached to it.

Build and Products milestones fire on values of ``Project.custom_build_status`` — an existing
Select, deliberately reused rather than shadowed by a parallel state machine. **If one of those
options is renamed, the trigger stops matching and nothing says so.** A milestone whose trigger
matches nothing looks exactly like a milestone that has not come round yet: the row is present,
the configuration reads correctly, and no inspection is ever generated. That is the same failure
direction as a trailing-space query written in SQL — it passes.

So the seed's trigger values are pinned here against the live Select options in
``fixtures/custom_field.json``, and a rename fails the build instead.

Run: python -m unittest erpnext_enhancements.tests.test_inspection_milestones
"""

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality.catalog import MILESTONES

# Column positions in a MILESTONES row, named so an assertion reads as a sentence.
PROJECT_TYPE, KEY, TITLE, SEQUENCE, GATE_KIND = 0, 1, 2, 3, 4
TRIGGER_BASIS, TRIGGER_VALUE, INTERVAL_DAYS, MULTI_DAY = 5, 6, 7, 8


def _build_status_options():
    """The live options of `Project.custom_build_status`, from the fixture that owns them."""
    with open(APP_ROOT / "fixtures" / "custom_field.json", encoding="utf-8") as handle:
        rows = json.load(handle)
    field = next(r for r in rows if r["name"] == "Project-custom_build_status")
    return {opt.strip() for opt in (field["options"] or "").split("\n") if opt.strip()}


class TestTriggersMatchTheLiveStateMachine(unittest.TestCase):
    def test_every_build_status_trigger_is_a_real_option(self):
        options = _build_status_options()
        self.assertTrue(options, "the fixture's options list is empty; this test checks nothing")

        bad = [
            (m[KEY], m[TRIGGER_VALUE])
            for m in MILESTONES
            if m[TRIGGER_BASIS] == "Build Status" and m[TRIGGER_VALUE] not in options
        ]
        self.assertEqual(
            bad,
            [],
            "These milestones trigger on a build status that is not an option of "
            f"Project.custom_build_status {sorted(options)}. They would never fire, and "
            f"nothing would report it: {bad}",
        )

    def test_at_least_one_milestone_actually_uses_a_build_status(self):
        """Guards the check above from passing vacuously if triggers are ever all Manual."""
        using = [m[KEY] for m in MILESTONES if m[TRIGGER_BASIS] == "Build Status"]
        self.assertGreaterEqual(len(using), 4, f"only {using} use a build-status trigger")


class TestEveryTriggerCanActuallyFire(unittest.TestCase):
    def test_build_status_triggers_name_a_value(self):
        missing = [m[KEY] for m in MILESTONES if m[TRIGGER_BASIS] == "Build Status" and not m[TRIGGER_VALUE]]
        self.assertEqual(missing, [], f"Build Status trigger with nothing to match: {missing}")

    def test_calendar_triggers_have_an_interval(self):
        missing = [
            m[KEY] for m in MILESTONES if m[TRIGGER_BASIS] == "Calendar Interval" and not m[INTERVAL_DAYS]
        ]
        self.assertEqual(missing, [], f"Calendar trigger with no interval: {missing}")

    def test_manual_triggers_carry_no_stale_trigger_value(self):
        """A Manual milestone holding a leftover trigger value reads as automated and is not."""
        stale = [m[KEY] for m in MILESTONES if m[TRIGGER_BASIS] == "Manual" and m[TRIGGER_VALUE]]
        self.assertEqual(stale, [], f"Manual milestone with a trigger value: {stale}")

    def test_trigger_basis_values_are_all_known(self):
        allowed = {"Manual", "Build Status", "Calendar Interval"}
        bad = sorted({m[TRIGGER_BASIS] for m in MILESTONES} - allowed)
        self.assertEqual(bad, [], f"unknown trigger basis: {bad}")


class TestCatalogShape(unittest.TestCase):
    def test_milestone_keys_are_unique(self):
        keys = [m[KEY] for m in MILESTONES]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(dupes, [], f"milestone_key is the seed's idempotency key: {dupes}")

    def test_sequences_are_unique_within_a_project_type(self):
        seen = {}
        for m in MILESTONES:
            seen.setdefault(m[PROJECT_TYPE], []).append(m[SEQUENCE])
        for project_type, sequences in seen.items():
            self.assertEqual(
                len(sequences), len(set(sequences)), f"{project_type} has duplicate sequences"
            )

    def test_all_five_project_stages_are_covered(self):
        """The build spec names five categories; Service is its Maintenance and Products its
        Controls Fab. A missing one would mean that stage silently never gets inspected."""
        self.assertEqual(
            sorted({m[PROJECT_TYPE] for m in MILESTONES}),
            ["Build", "Design", "Events", "Products", "Service"],
        )

    def test_design_milestones_are_review_gates(self):
        """Design reviews are not physical pass/fail checklists; the spec is explicit."""
        wrong = [m[KEY] for m in MILESTONES if m[PROJECT_TYPE] == "Design" and m[GATE_KIND] != "Review Gate"]
        self.assertEqual(wrong, [], f"Design milestone typed as a physical checklist: {wrong}")

    def test_only_the_mid_event_check_is_multi_day_only(self):
        flagged = [m[KEY] for m in MILESTONES if m[MULTI_DAY]]
        self.assertEqual(flagged, ["events_mid_check"])

    def test_gate_kinds_are_all_known(self):
        allowed = {"Physical Checklist", "Review Gate"}
        bad = sorted({m[GATE_KIND] for m in MILESTONES} - allowed)
        self.assertEqual(bad, [], f"unknown gate kind: {bad}")


class TestCommissioningSeed(unittest.TestCase):
    def test_it_targets_a_milestone_the_catalog_actually_creates(self):
        """The template hangs off `build_pre_final`; if that key is ever renamed the template
        is never created and the patch logs a line nobody reads."""
        from erpnext_enhancements.quality.catalog import COMMISSIONING_MILESTONE_KEY

        self.assertIn(COMMISSIONING_MILESTONE_KEY, [m[KEY] for m in MILESTONES])

    def test_every_commissioning_check_has_a_standard_to_pass_against(self):
        """A check with no acceptance criteria is an opinion, not an inspection."""
        from erpnext_enhancements.quality.catalog import COMMISSIONING_CHECKS

        self.assertTrue(COMMISSIONING_CHECKS)
        for label, criteria, check_type, _method, _uom, _photo in COMMISSIONING_CHECKS:
            self.assertTrue(label.strip(), "check with no label")
            self.assertTrue(criteria.strip(), f"{label!r} has no acceptance criteria")
            self.assertIn(check_type, {"Pass/Fail", "Measurement", "Document"})

    def test_it_covers_the_gap_the_kpi_document_named(self):
        """KPI #10 First-Pass Yield is only computable if these are the things recorded."""
        from erpnext_enhancements.quality.catalog import COMMISSIONING_CHECKS

        labels = " ".join(c[0].lower() for c in COMMISSIONING_CHECKS)
        for expected in ("fill", "leak", "flow", "gfci", "nozzle", "light"):
            self.assertIn(expected, labels, f"commissioning does not cover {expected!r}")


if __name__ == "__main__":
    unittest.main()
