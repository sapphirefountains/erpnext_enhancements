"""Bench-free tests for the NCR / Quality Action state machines (WI-075 sub-phase E).

Two failures this guards, and neither is subtle once it happens — which is the problem, because
both are invisible right up until they are total.

**The Select and the Python must agree.** A status vocabulary lives in a Property Setter's
``options`` string and in the code that writes a status, and nothing keeps them in sync. When
they disagree the doctype does not misbehave a little: a value outside a Select's options fails
``_validate_selects``, no ``ignore_*`` flag bypasses it, and the row cannot be saved by anybody
ever again. So the fixture is checked against :mod:`erpnext_enhancements.quality.lifecycle`.

**Core's one-liner must stay replaced.** ERPNext's ``QualityAction.validate`` is a single line
that writes ``"Completed"`` whenever the resolutions table is empty. An empty resolutions table
is exactly what a punch-list item is, so under core every punch item is born closed — and once
the Property Setter lands, ``"Completed"`` is not an option and *every save of the doctype
raises*. The override and the Property Setter are one indivisible change, and this file asserts
both halves are present.

Run: python -m unittest erpnext_enhancements.tests.test_quality_lifecycle
"""

import json
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import lifecycle as L


def _property_setters():
    with open(APP_ROOT / "fixtures" / "property_setter.json", encoding="utf-8") as handle:
        rows = json.load(handle)
    assert len(rows) > 100, "property_setter.json looks truncated; this suite would check nothing"
    return {r["name"]: r for r in rows}


def _custom_fields():
    with open(APP_ROOT / "fixtures" / "custom_field.json", encoding="utf-8") as handle:
        rows = json.load(handle)
    assert len(rows) > 400, "custom_field.json looks truncated; this suite would check nothing"
    return {r["name"]: r for r in rows}


class TestFixtureMatchesTheStateMachine(unittest.TestCase):
    def test_quality_action_options_match_the_code(self):
        ps = _property_setters()["Quality Action-status-options"]
        self.assertEqual(ps["value"], L.options_string(L.ACTION_STATUSES))

    def test_non_conformance_options_match_the_code(self):
        ps = _property_setters()["Non Conformance-status-options"]
        self.assertEqual(ps["value"], L.options_string(L.NCR_STATUSES))

    def test_core_completed_is_not_an_option(self):
        """The literal core writes. If it were an option the override would be optional, and
        somebody would eventually remove it."""
        ps = _property_setters()["Quality Action-status-options"]
        self.assertNotIn(L.CORE_COMPLETED, ps["value"].split("\n"))

    def test_every_status_the_code_can_write_is_offered(self):
        """The direction that matters: code writing a value the Select lacks strands the row."""
        offered = set(_property_setters()["Quality Action-status-options"]["value"].split("\n"))
        writable = set(L.ACTION_STATUSES) | set(L.ACTION_LEGACY_MAP.values())
        self.assertEqual(writable - offered, set())

    def test_legacy_targets_are_real_options(self):
        ncr = set(_property_setters()["Non Conformance-status-options"]["value"].split("\n"))
        self.assertEqual(set(L.NCR_LEGACY_MAP.values()) - ncr, set())


class TestTheOverrideIsWired(unittest.TestCase):
    """The Property Setter alone bricks the doctype, so its partner has to be present."""

    def test_hooks_registers_the_quality_action_override(self):
        source = (APP_ROOT / "hooks.py").read_text(encoding="utf-8")
        self.assertIn(
            '"Quality Action": "erpnext_enhancements.quality.overrides.quality_action.QualityAction"',
            source,
            "The Quality Action status Property Setter is shipped. Without this override, core's "
            "validate writes 'Completed' and every save of the doctype raises.",
        )

    def test_the_override_module_exists_where_hooks_says(self):
        self.assertTrue((APP_ROOT / "quality" / "overrides" / "quality_action.py").exists())
        self.assertTrue((APP_ROOT / "quality" / "overrides" / "__init__.py").exists())

    def test_the_override_does_not_call_super_validate(self):
        """Calling it would run the one line being replaced, which is the whole bug."""
        source = (APP_ROOT / "quality" / "overrides" / "quality_action.py").read_text(encoding="utf-8")
        body = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        self.assertNotIn("super().validate()", body)


class TestDeriveActionStatus(unittest.TestCase):
    def test_no_resolutions_is_not_done(self):
        """Core reads an empty table as Completed. A punch-list item has an empty table."""
        self.assertEqual(L.derive_action_status(L.ACTION_OPEN, []), L.ACTION_OPEN)
        self.assertEqual(L.derive_action_status(L.ACTION_OPEN, None), L.ACTION_OPEN)

    def test_a_brand_new_action_defaults_to_open(self):
        self.assertEqual(L.derive_action_status(None, []), L.ACTION_OPEN)
        self.assertEqual(L.derive_action_status("", []), L.ACTION_OPEN)

    def test_an_open_resolution_means_in_progress(self):
        self.assertEqual(
            L.derive_action_status(L.ACTION_OPEN, [{"status": "Open"}]), L.ACTION_IN_PROGRESS
        )

    def test_all_resolutions_done_means_pm_resolved_not_closed(self):
        """PM Resolved, never Closed. A self-reported fix and a re-inspected fix are different
        levels of confidence, and only the second may close the record."""
        self.assertEqual(
            L.derive_action_status(L.ACTION_OPEN, [{"status": "Completed"}]), L.ACTION_PM_RESOLVED
        )

    def test_mixed_resolutions_means_in_progress(self):
        rows = [{"status": "Completed"}, {"status": "Open"}]
        self.assertEqual(L.derive_action_status(L.ACTION_OPEN, rows), L.ACTION_IN_PROGRESS)

    def test_a_verified_action_is_never_moved_by_its_child_table(self):
        """Editing a child table is not confirmation that a fix was re-inspected."""
        self.assertEqual(
            L.derive_action_status(L.ACTION_VERIFIED, [{"status": "Open"}]), L.ACTION_VERIFIED
        )

    def test_a_closed_action_is_never_reopened_by_its_child_table(self):
        self.assertEqual(
            L.derive_action_status(L.ACTION_CLOSED, [{"status": "Open"}]), L.ACTION_CLOSED
        )

    def test_cores_completed_is_mapped_rather_than_stored(self):
        """A row arriving with core's literal becomes saveable instead of raising forever."""
        self.assertEqual(L.derive_action_status(L.CORE_COMPLETED, []), L.ACTION_CLOSED)

    def test_it_never_writes_a_value_outside_the_options(self):
        """The property that makes the doctype saveable at all."""
        resolution_sets = [[], None, [{"status": "Open"}], [{"status": "Completed"}]]
        starting = [*L.ACTION_STATUSES, None, "", L.CORE_COMPLETED, "Nonsense"]
        for current in starting:
            for rows in resolution_sets:
                self.assertIn(L.derive_action_status(current, rows), L.ACTION_STATUSES)

    def test_it_works_on_objects_as_well_as_dicts(self):
        """Production passes Document children; the tests pass dicts."""

        class Row:
            status = "Open"

        self.assertEqual(L.derive_action_status(L.ACTION_OPEN, [Row()]), L.ACTION_IN_PROGRESS)


class TestEscalate(unittest.TestCase):
    def test_it_steps_up_one(self):
        self.assertEqual(L.escalate("Low"), "Medium")
        self.assertEqual(L.escalate("Medium"), "High")
        self.assertEqual(L.escalate("High"), "Critical")

    def test_it_caps_rather_than_wrapping(self):
        """Critical quietly becoming Low on a second failure is the opposite of 'escalate'."""
        self.assertEqual(L.escalate("Critical"), "Critical")

    def test_an_unknown_priority_lands_somewhere_sane(self):
        self.assertEqual(L.escalate(None), "Medium")
        self.assertEqual(L.escalate("Urgent"), "Medium")


class TestCustomFieldsAreConsistentWithTheCode(unittest.TestCase):
    def test_severity_options_match(self):
        cf = _custom_fields()["Non Conformance-custom_severity"]
        self.assertEqual(cf["options"], L.options_string(L.SEVERITIES))

    def test_critical_is_a_real_severity(self):
        """Sub-phase G's alert keys on this constant. A typo would mean it never fires."""
        self.assertIn(L.SEVERITY_CRITICAL, L.SEVERITIES)
        cf = _custom_fields()["Non Conformance-custom_severity"]
        self.assertIn(L.SEVERITY_CRITICAL, cf["options"].split("\n"))

    def test_responsible_party_options_match(self):
        cf = _custom_fields()["Non Conformance-custom_responsible_party"]
        self.assertEqual(cf["options"], L.options_string(L.RESPONSIBLE_PARTIES))

    def test_action_source_options_match(self):
        cf = _custom_fields()["Quality Action-custom_source_type"]
        self.assertEqual(cf["options"], L.options_string(L.ACTION_SOURCES))

    def test_punch_list_is_a_source_and_a_flag(self):
        """Both halves of decision 5: a punch item IS a Quality Action, filtered by a flag."""
        self.assertIn("Punch List", L.ACTION_SOURCES)
        self.assertIn("Quality Action-custom_punch_list", _custom_fields())

    def test_priority_options_match(self):
        cf = _custom_fields()["Quality Action-custom_priority"]
        self.assertEqual(cf["options"], L.options_string(L.PRIORITIES))

    def test_the_subcontractor_link_is_conditionally_mandatory(self):
        """An NCR blaming a subcontractor that names none cannot reach a vendor scorecard."""
        cf = _custom_fields()["Non Conformance-custom_supplier"]
        self.assertIn("Subcontractor", cf["mandatory_depends_on"] or "")


if __name__ == "__main__":
    unittest.main()
