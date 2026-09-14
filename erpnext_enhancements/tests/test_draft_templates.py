"""Bench-free guards on the strawman inspection checklists (WI-075 sub-phase I follow-up).

These are drafts written to be corrected, so the interesting assertions are not about the
wording. They are about the two properties that make seeding a strawman safe at all:

1. **Every template is created `Draft`.** A Draft template generates nothing — the endpoint
   refuses a non-Active template and the due sweep counts only Active ones — so a milestone goes
   on reporting *due and blocked* until a person reads the template and adopts it. If that ever
   became `Active`, a checklist nobody has reviewed would silently start carrying the authority
   of the company that issued it, and an inspector would work through it assuming somebody chose
   those items on purpose.
2. **Every check has a pass standard and a way to verify it.** The design principle of the whole
   programme is that a criterion that cannot be inspected should not be in the contract. A
   checklist row with no standard is the same failure on the other side of the wall.

Plus the shape checks that would otherwise be discovered by a patch failing on a live migrate.

Run: python -m unittest erpnext_enhancements.tests.test_draft_templates
"""

import io
import re
import sys
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality.catalog import COMMISSIONING_TEMPLATE_NAME, MILESTONES
from erpnext_enhancements.quality.draft_catalog import (
    DRAFT_NOTICE,
    DRAFT_SECTIONS,
    DRAFT_TEMPLATES,
    REQUIRED_UOMS,
    UNSOURCED_SECTIONS,
)

PATCH = APP_ROOT / "patches" / "seed_draft_inspection_templates.py"

# Column positions, named so an assertion reads as a sentence.
S_TITLE, S_TYPE, S_DESC, S_INSTR, S_ITEMS = 0, 1, 2, 3, 4
T_NAME, T_PROJECT_TYPE, T_MILESTONE, T_SECTIONS = 0, 1, 2, 3
I_LABEL, I_CRITERIA, I_CHECK_TYPE, I_METHOD, I_UOM = 0, 1, 2, 3, 4
I_MIN, I_MAX, I_MANDATORY, I_PHOTO, I_REFERENCE = 5, 6, 7, 8, 9

SECTION_TYPES = {"Visual Inspection", "Measurement", "Functional Test", "Document Review", "Safety Check"}
CHECK_TYPES = {"Pass/Fail", "Measurement", "Document"}


def _items():
    for section in DRAFT_SECTIONS:
        for item in section[S_ITEMS]:
            yield section[S_TITLE], item


class TestNothingIsSeededActive(unittest.TestCase):
    """The property the whole approach rests on."""

    @classmethod
    def setUpClass(cls):
        with io.open(PATCH, encoding="utf-8") as handle:
            cls.source = handle.read()
        cls.code = re.sub(r'"""[\s\S]*?"""', "", cls.source)
        cls.code = re.sub(r"^\s*#.*$", "", cls.code, flags=re.MULTILINE)

    def test_the_patch_creates_templates_as_draft(self):
        self.assertIn(
            '"status": "Draft"',
            self.code,
            "the strawman seed must create templates Draft; an Active one would put a checklist "
            "nobody reviewed into an inspector's hands carrying the company's authority",
        )

    def test_the_patch_never_creates_an_active_template(self):
        self.assertNotIn(
            '"status": "Active"',
            self.code,
            "this patch seeds drafts for review; adoption is a person setting one Active",
        )

    def test_the_patch_is_insert_only(self):
        """Re-running must not reset a template somebody has already corrected and adopted."""
        self.assertEqual(
            self.code.count("frappe.db.exists(\"Project Inspection Template\", name)"),
            1,
            "the template path must skip anything that already exists",
        )
        self.assertIn('frappe.db.exists("Inspection Section", title)', self.code)
        for forbidden in ("db_set(", "set_value(", "delete_doc("):
            self.assertNotIn(forbidden, self.code, f"{forbidden} makes this patch not insert-only")

    def test_a_template_with_no_sections_is_not_created_at_all(self):
        """A sectionless template somebody sets Active generates an empty inspection, and an
        empty inspection that can be submitted reads as a clean pass."""
        self.assertRegex(self.code, r"if not present:[\s\S]{0,300}?return None")


class TestEveryCheckCanActuallyBeInspected(unittest.TestCase):
    def test_the_corpus_is_not_empty(self):
        """Every check below is 'no item does X', vacuously true on an empty list."""
        self.assertGreater(len(list(_items())), 80)
        self.assertGreater(len(DRAFT_TEMPLATES), 10)

    def test_every_item_has_a_pass_standard(self):
        """The programme's own principle: if a criterion cannot be inspected it should not be in
        the contract. A row with no standard is the same failure on the checklist side."""
        bad = [f"{s}: {i[I_LABEL]}" for s, i in _items() if not (i[I_CRITERIA] or "").strip()]
        self.assertEqual(bad, [], f"items with no acceptance criteria: {bad}")

    def test_every_item_says_how_to_verify_it(self):
        bad = [f"{s}: {i[I_LABEL]}" for s, i in _items() if not (i[I_METHOD] or "").strip()]
        self.assertEqual(bad, [], f"items with no verification method: {bad}")

    def test_a_pass_standard_is_not_just_the_label_again(self):
        """'Pump basket cleaned' / 'Pump basket cleaned' is a row that looks complete and
        decides nothing."""
        bad = [
            f"{s}: {i[I_LABEL]}"
            for s, i in _items()
            if (i[I_CRITERIA] or "").strip().lower().rstrip(".") == (i[I_LABEL] or "").strip().lower()
        ]
        self.assertEqual(bad, [], f"acceptance criteria that only restate the label: {bad}")

    def test_check_types_are_all_known(self):
        bad = sorted({i[I_CHECK_TYPE] for _s, i in _items()} - CHECK_TYPES)
        self.assertEqual(bad, [], f"unknown check_type: {bad}")

    def test_section_types_are_all_known(self):
        bad = sorted({s[S_TYPE] for s in DRAFT_SECTIONS} - SECTION_TYPES)
        self.assertEqual(bad, [], f"unknown section_type: {bad}")

    def test_a_measurement_names_its_unit(self):
        """A measurement with no unit cannot have its bounds compared, so an out-of-range
        reading never flags and the check silently becomes an opinion."""
        bad = [
            f"{s}: {i[I_LABEL]}"
            for s, i in _items()
            if i[I_CHECK_TYPE] == "Measurement" and not i[I_UOM]
        ]
        self.assertEqual(bad, [], f"measurement checks with no UOM: {bad}")

    def test_every_uom_used_is_one_the_patch_creates_or_erpnext_ships(self):
        shipped = {"Gallon", "Minute", "Nos", "Percent", "Ampere", "Foot", "Inch"}
        used = {i[I_UOM] for _s, i in _items() if i[I_UOM]}
        unknown = sorted(used - set(REQUIRED_UOMS) - shipped)
        self.assertEqual(
            unknown,
            [],
            f"these UOMs are neither created by the patch nor shipped by ERPNext: {unknown}. "
            "A Link to a UOM that does not exist makes the row unsaveable.",
        )

    def test_a_bounded_measurement_has_a_sane_range(self):
        bad = [
            f"{s}: {i[I_LABEL]} ({i[I_MIN]}..{i[I_MAX]})"
            for s, i in _items()
            if i[I_CHECK_TYPE] == "Measurement" and i[I_MIN] and i[I_MAX] and i[I_MIN] >= i[I_MAX]
        ]
        self.assertEqual(bad, [], f"measurement whose minimum is not below its maximum: {bad}")

    def test_the_service_chemistry_ranges_are_sapphires_own(self):
        """Pinned because these are the one set of numbers taken verbatim from a live Sapphire
        record. If somebody 'tidies' them, the draft stops being sourced and becomes invented."""
        section = next(s for s in DRAFT_SECTIONS if s[S_TITLE] == "Water Chemistry (Service)")
        ranges = {i[I_LABEL]: (i[I_UOM], i[I_MIN], i[I_MAX]) for i in section[S_ITEMS]}
        self.assertEqual(ranges["pH"], ("pH", 7.2, 7.8))
        self.assertEqual(ranges["Free chlorine"], ("ppm", 1.0, 3.0))
        self.assertEqual(ranges["ORP"], ("mV", 650.0, 750.0))
        self.assertEqual(ranges["Total alkalinity"], ("ppm", 80.0, 120.0))


class TestProvenanceIsRecorded(unittest.TestCase):
    def test_every_section_description_carries_the_draft_notice(self):
        """The patch prefixes it, so this asserts the patch does and not the data."""
        with io.open(PATCH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('f"{DRAFT_NOTICE} {description}"', source)
        self.assertIn("not yet Sapphire's standard of care", DRAFT_NOTICE)

    def test_the_unsourced_sections_say_so_in_their_own_description(self):
        """Events has no source inside this system. That fact must travel with the record, not
        live only in a module docstring somebody may never open."""
        for title in UNSOURCED_SECTIONS:
            section = next(s for s in DRAFT_SECTIONS if s[S_TITLE] == title)
            self.assertIn(
                "WITHOUT AN INTERNAL SOURCE",
                section[S_DESC],
                f"{title} is drafted from nothing and does not say so",
            )

    def test_the_sourced_sets_actually_cite_something(self):
        """Guards the claim that three of the four sets are lifted rather than invented."""
        for title in ("Water Chemistry (Service)", "Safety and Electrical (Service)"):
            section = next(s for s in DRAFT_SECTIONS if s[S_TITLE] == title)
            cited = [i for i in section[S_ITEMS] if i[I_REFERENCE]]
            self.assertEqual(
                len(cited), len(section[S_ITEMS]), f"{title} has items with no reference_standard"
            )

    def test_unsourced_sections_are_not_silently_pinned_to_a_reference(self):
        """A reference_standard on an invented item would be a fabricated citation."""
        for title in UNSOURCED_SECTIONS:
            section = next(s for s in DRAFT_SECTIONS if s[S_TITLE] == title)
            cited = [i[I_LABEL] for i in section[S_ITEMS] if i[I_REFERENCE]]
            self.assertEqual(cited, [], f"{title} cites a source it does not have: {cited}")


class TestTemplatesLineUpWithTheCatalog(unittest.TestCase):
    def test_every_template_names_a_milestone_the_catalog_creates(self):
        keys = {m[1] for m in MILESTONES}
        bad = [(t[T_NAME], t[T_MILESTONE]) for t in DRAFT_TEMPLATES if t[T_MILESTONE] not in keys]
        self.assertEqual(bad, [], f"templates hung off a milestone that is never seeded: {bad}")

    def test_every_template_names_sections_that_exist(self):
        titles = {s[S_TITLE] for s in DRAFT_SECTIONS}
        bad = [
            (t[T_NAME], title)
            for t in DRAFT_TEMPLATES
            for title in t[T_SECTIONS]
            if title not in titles
        ]
        self.assertEqual(bad, [], f"templates naming a section that is never seeded: {bad}")

    def test_no_template_is_empty(self):
        bad = [t[T_NAME] for t in DRAFT_TEMPLATES if not t[T_SECTIONS]]
        self.assertEqual(bad, [], f"templates with no sections: {bad}")

    def test_it_does_not_collide_with_the_commissioning_template(self):
        """`Build — Pre-Final (Systems Startup)` is Active and is NOT a strawman: its checks came
        from a document written by somebody who knew the trade. Re-seeding it as a Draft would
        be a downgrade of a real checklist."""
        names = [t[T_NAME] for t in DRAFT_TEMPLATES]
        self.assertNotIn(COMMISSIONING_TEMPLATE_NAME, names)
        self.assertNotIn("build_pre_final", [t[T_MILESTONE] for t in DRAFT_TEMPLATES])

    def test_template_names_are_unique(self):
        names = [t[T_NAME] for t in DRAFT_TEMPLATES]
        dupes = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(dupes, [], f"template_name is the autoname, so a duplicate collides: {dupes}")

    def test_section_titles_are_unique(self):
        titles = [s[S_TITLE] for s in DRAFT_SECTIONS]
        dupes = sorted({t for t in titles if titles.count(t) > 1})
        self.assertEqual(dupes, [], f"section_title is the autoname: {dupes}")

    def test_one_template_per_milestone(self):
        keys = [t[T_MILESTONE] for t in DRAFT_TEMPLATES]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(dupes, [], f"two strawmen for one milestone: {dupes}")

    def test_all_five_project_types_are_covered(self):
        self.assertEqual(
            sorted({t[T_PROJECT_TYPE] for t in DRAFT_TEMPLATES}),
            ["Build", "Design", "Events", "Products", "Service"],
        )

    def test_design_gates_ask_for_documents_rather_than_measurements(self):
        """A design review is a document review. A measurement check there would be somebody
        re-deriving the calculation on a form, which is not what the gate is for."""
        for title in ("Concept Against the Brief", "Design Development — Engineering",
                      "Final Design Sign-Off"):
            section = next(s for s in DRAFT_SECTIONS if s[S_TITLE] == title)
            self.assertEqual(section[S_TYPE], "Document Review")
            bad = [i[I_LABEL] for i in section[S_ITEMS] if i[I_CHECK_TYPE] == "Measurement"]
            self.assertEqual(bad, [], f"{title} has measurement checks: {bad}")


if __name__ == "__main__":
    unittest.main()
