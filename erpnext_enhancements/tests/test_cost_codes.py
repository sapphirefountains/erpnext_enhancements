"""Bench-free tests for the MasterFormat cost code catalog (WI-077).

Every failure guarded here is a **code that looks right and points somewhere else**, which is the
only kind of defect this catalog produces. A wrong cost code does not raise; it prints on a
Schedule of Values or an AIA G702/G703 that a general contractor reads, and it comes back as a
rejected submittal weeks later.

That is not hypothetical. The catalog this replaces was the retired MasterFormat 1995 16-division
list, mechanically reformatted, and it had shipped for years:

* ``22 3100``, used for Pumps, is **Domestic Water Softeners**.
* ``22 5100``, used for Nozzles, is **Swimming Pool Plumbing Systems** - a whole sibling block.
* ``00 5000``, used for Fee, is **Contracting Forms and Supplements**, so a pay application showed
  money against the number for the agreement form.
* ``02 0300``, used for Earthwork, is **Conservation Treatment for Existing Period Conditions**.

Two of those collisions were **newer than the codes**: ``02 03 00`` and ``09 03 00`` do not exist in
MasterFormat 2011 and do in 2016. A number that is merely meaningless today can become a live
collision without anybody touching it, which is why the list check below is the real gate and the
pattern check is only a shape check.

``01 5433`` is the case proving a regex cannot do this job: it has a perfectly legal Level-4 shape
inside a real family (01 54 00 Construction Aids, whose children stop at 01 54 26), so it passes
any pattern test and fails only against a published section list.

Run: python -m unittest erpnext_enhancements.tests.test_cost_codes
"""

import re
import unittest

from erpnext_enhancements.quality import budgets
from erpnext_enhancements.quality import cost_codes as cc

BUDGET_CATEGORIES = {
    budgets.CATEGORY_LABOR,
    budgets.CATEGORY_MATERIALS,
    budgets.CATEGORY_EQUIPMENT,
    budgets.CATEGORY_SUBCONTRACTORS,
    budgets.CATEGORY_GENERAL_CONDITIONS,
    budgets.CATEGORY_CONTINGENCY,
    budgets.CATEGORY_FEE,
}

COST_TYPES = {"Labor", "Material", "Equipment", "Subcontract", "Expense"}

SECTION_RE = re.compile(r"^\d{2} \d{2} \d{2}$")

#: Numbers the old catalog used that mean something else in MasterFormat. None may ever come back.
POISONED = {
    "22 3100": "Domestic Water Softeners",
    "22 5100": "Swimming Pool Plumbing Systems",
    "22 3400": "Fuel-Fired Domestic Water Heaters",
    "00 5000": "Contracting Forms and Supplements",
    "02 0300": "Conservation Treatment for Existing Period Conditions",
    "26 0500": "Common Work Results for Electrical",
    "26 0800": "Commissioning of Electrical Systems",
    "26 1000": "Medium-Voltage Electrical Distribution",
    "01 1000": "Summary",
    "01 1100": "Summary of Work",
}


def display_to_section(code):
    """``22 5213.29`` -> ``22 52 13``. The section a display code names."""
    head = code.split(".")[0]
    div, rest = head.split(" ")
    return "%s %s %s" % (div, rest[:2], rest[2:])


class TestSectionList(unittest.TestCase):
    def test_every_section_is_well_formed(self):
        for section in cc.MASTERFORMAT_SECTIONS:
            self.assertRegex(section, SECTION_RE, "%s is not a six-digit section" % section)

    def test_every_section_has_a_nonempty_title(self):
        for section, title in cc.MASTERFORMAT_SECTIONS.items():
            self.assertTrue(title.strip(), "%s has no title" % section)

    def test_recheck_entries_name_real_sections(self):
        for section in cc.SECTIONS_NEEDING_RECHECK:
            self.assertIn(section, cc.MASTERFORMAT_SECTIONS)


class TestCostCodes(unittest.TestCase):
    def test_every_code_matches_the_display_pattern(self):
        for row in cc.COST_CODES:
            self.assertRegex(row["cost_code"], cc.COST_CODE_RE)

    def test_every_code_names_a_published_section(self):
        """The real gate. A regex cannot catch 01 5433; only this can."""
        for row in cc.COST_CODES:
            self.assertIn(
                row["section"],
                cc.MASTERFORMAT_SECTIONS,
                "%s points at %s, which is not in the verified section list"
                % (row["cost_code"], row["section"]),
            )

    def test_the_display_code_and_the_section_agree(self):
        """A code whose digits disagree with its declared section is the 1995 bug returning."""
        for row in cc.COST_CODES:
            self.assertEqual(
                display_to_section(row["cost_code"]),
                row["section"],
                "%s does not spell %s" % (row["cost_code"], row["section"]),
            )

    def test_a_section_level_code_carries_the_csi_title_verbatim(self):
        """Only a user-assigned Level 4 (the decimal) may invent a title."""
        for row in cc.COST_CODES:
            if "." in row["cost_code"]:
                continue
            self.assertEqual(
                row["code_title"],
                cc.MASTERFORMAT_SECTIONS[row["section"]],
                "%s must use CSI's own title for %s" % (row["cost_code"], row["section"]),
            )

    def test_no_poisoned_number_is_reachable_undeclared(self):
        """A poisoned string may only survive if it is now used for its REAL section, and the
        reuse is declared. 26 0500 is the case: it meant General Lighting and now correctly means
        Common Work Results for Electrical, so the string persists with a new meaning."""
        live = {row["cost_code"] for row in cc.COST_CODES}
        for bad, means in POISONED.items():
            if bad in live:
                self.assertIn(
                    bad,
                    cc.REUSED_CODES,
                    "%s is live and means %s; if that is deliberate it must be declared in "
                    "REUSED_CODES so the migration cannot match on the string" % (bad, means),
                )
                self.assertEqual(cc.MASTERFORMAT_SECTIONS[display_to_section(bad)], means)

    def test_every_reused_string_is_declared_with_both_meanings(self):
        """The migration-order hazard: un-migrated data holding one of these silently changes
        meaning instead of failing to resolve. Migrate by old_code, never by matching the string."""
        for code, facts in cc.REUSED_CODES.items():
            self.assertIn(code, {r["cost_code"] for r in cc.COST_CODES})
            self.assertTrue(facts["was"].strip() and facts["was"] != "?", code)
            self.assertNotEqual(facts["was"], facts["now"], code)
            self.assertEqual(cc.OLD_TO_NEW[code], facts["old_moved_to"])

    def test_codes_are_unique(self):
        codes = [row["cost_code"] for row in cc.COST_CODES]
        self.assertEqual(len(codes), len(set(codes)))

    def test_cost_types_and_budget_categories_are_known(self):
        for row in cc.COST_CODES:
            self.assertIn(row["cost_type"], COST_TYPES)
            self.assertIn(row["budget_category"], BUDGET_CATEGORIES)

    def test_overhead_categories_are_never_keyable_work(self):
        """Contingency and Fee are computed; a keyed line against them double-counts."""
        for row in cc.COST_CODES:
            if row["budget_category"] in (budgets.CATEGORY_CONTINGENCY, budgets.CATEGORY_FEE):
                self.assertEqual(row["is_overhead"], 1, row["cost_code"])


class TestResourceCodes(unittest.TestCase):
    def test_a_resource_can_never_be_mistaken_for_a_section(self):
        """00 2200 was indistinguishable from a real section reference. That is the whole bug."""
        for row in cc.RESOURCE_CODES:
            self.assertIsNone(
                cc.COST_CODE_RE.match(row["resource_code"]),
                "%s is MasterFormat-shaped and must not be" % row["resource_code"],
            )

    def test_resource_categories_are_known(self):
        for row in cc.RESOURCE_CODES:
            self.assertIn(row["cost_type"], COST_TYPES)
            self.assertIn(row["budget_category"], BUDGET_CATEGORIES)

    def test_resource_codes_are_unique(self):
        codes = [row["resource_code"] for row in cc.RESOURCE_CODES]
        self.assertEqual(len(codes), len(set(codes)))


class TestMigration(unittest.TestCase):
    def test_every_old_code_reads_forward(self):
        """A historical estimate must always resolve."""
        declared = {row["old_code"] for row in cc.COST_CODES}
        declared |= {row["old_code"] for row in cc.RESOURCE_CODES}
        declared |= set(cc.RETIRED_CODES)
        for old in declared:
            self.assertIn(old, cc.OLD_TO_NEW, "%s does not read forward" % old)

    def test_the_map_is_injective_except_where_a_split_is_declared(self):
        seen = {}
        for old, new in cc.OLD_TO_NEW.items():
            if old in cc.SPLIT_OLD_CODES or new in cc.MERGED_OLD_CODES:
                continue
            self.assertNotIn(new, seen, "%s and %s both map to %s" % (old, seen.get(new), new))
            seen[new] = old

    def test_every_declared_merge_really_has_several_sources(self):
        for new, olds in cc.MERGED_OLD_CODES.items():
            self.assertGreater(len(olds), 1, new)
            for old in olds:
                self.assertEqual(cc.OLD_TO_NEW[old], new)

    def test_a_split_resolves_to_one_of_its_declared_homes(self):
        for old, homes in cc.SPLIT_OLD_CODES.items():
            self.assertIn(cc.SPLIT_PRIMARY[old], homes)
            self.assertEqual(cc.OLD_TO_NEW[old], cc.SPLIT_PRIMARY[old])

    def test_retired_codes_point_at_something_live(self):
        live = {r["cost_code"] for r in cc.COST_CODES}
        live |= {r["resource_code"] for r in cc.RESOURCE_CODES}
        for old, (new, _why) in cc.RETIRED_CODES.items():
            self.assertIn(new, live, "%s is superseded by %s, which does not exist" % (old, new))


if __name__ == "__main__":
    unittest.main()
