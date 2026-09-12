# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Guards on the two WI-074 F patches: position requirements, and the leaver backfill.

`tabPosition Requirement` held **zero rows across all twenty Positions** against fifteen
seeded Credential Types, so the Skills Matrix and Qualification Coverage were correctly
reporting that nobody holds anything — nothing had ever said what a position needs.

**The load-bearing fact, and the reason the seed looks repetitive:** requirements do *not*
inherit down the Position tree. `progression.requirements_for` reads one doc's own
`requirements` child table and `skills_matrix` does the same; neither walks
`parent_position`. So a baseline placed on the **All Positions** root would apply to nobody,
and every leaf has to spell out its whole set. `Driver's License` appearing nineteen times is
the only shape the consumers can see.

That fact is asserted here rather than trusted, because if inheritance were ever added the
seed would become nineteen-fold duplication overnight and nothing else would notice.

Bench-free: filesystem and `ast` only. Asserts the patches' shape, never live data.

Run: python -m unittest erpnext_enhancements.tests.test_position_requirements_seed
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SEED = APP / "patches/seed_position_credential_requirements.py"
BACKFILL = APP / "patches/backfill_leaving_checklists.py"
PROGRESSION = APP / "hr_enhancements/progression.py"
SKILLS = APP / "hr_enhancements/report/skills_matrix/skills_matrix.py"
ONBOARDING = APP / "hr_enhancements/onboarding.py"
PATCHES_TXT = APP / "patches.txt"

GROUP_POSITIONS = {"All Positions", "Technician"}
NEVER_ASSIGNED = {"CDL (Commercial Driver's License)", "DOT Medical Card"}


def _code(path):
    """Executable source: comments and docstrings stripped, quotes normalised."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(ast.fix_missing_locations(tree)).replace("'", '"')


_TYPES_SOURCE = APP / "patches/seed_credential_types.py"


def _table_names(path, const, first_element_only=False):
    """The first element of each tuple in a module-level constant, as a set."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == const for t in node.targets
        ):
            rows = ast.literal_eval(node.value)
            return {r[0] for r in rows} if first_element_only else set(rows)
    return set()


def _table():
    """The seed's REQUIREMENTS dict, read from source rather than imported (no `frappe`)."""
    tree = ast.parse(SEED.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "REQUIREMENTS" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("REQUIREMENTS not found")


class TestTheSeedMatchesHowRequirementsAreRead(unittest.TestCase):
    def test_requirements_do_not_inherit_down_the_tree(self):
        """THE assertion this file exists for. Both consumers read one position's own child
        table; neither walks `parent_position`. If that ever changes, the seed becomes
        nineteen-fold duplication and this is the only thing that would say so."""
        for path in (PROGRESSION, SKILLS):
            with self.subTest(reader=path.name):
                code = _code(path)
                self.assertIn('get("requirements")', code)
                at = code.index('get("requirements")')
                window = code[max(0, at - 400) : at + 200]
                self.assertNotIn("parent_position", window)
                self.assertNotIn("lft", window)

    def test_the_group_positions_get_nothing(self):
        """A job family is not a job. Nobody holds one, and with no inheritance a row there
        would be invisible to every reader."""
        table = _table()
        for group in GROUP_POSITIONS:
            with self.subTest(position=group):
                self.assertNotIn(group, table)

    def test_every_leaf_position_is_covered(self):
        """Nik chose the full twenty-position mapping over a field-roles-only subset. Twenty
        Positions minus the two groups is eighteen leaves."""
        self.assertEqual(len(_table()), 18)

    def test_the_shared_lines_are_repeated_rather_than_assumed(self):
        """The direct consequence of no inheritance: the common credential appears on each
        leaf that needs it, not once on a parent."""
        table = _table()
        holders = [p for p, rows in table.items() if any(c == "Driver's License" for c, _ in rows)]
        self.assertGreaterEqual(len(holders), 15)


class TestTheSeedIsCalibratedRatherThanBlanket(unittest.TestCase):
    def test_is_mandatory_is_used_both_ways(self):
        """Flagging everything mandatory makes the distinction worthless and the roster
        uniformly red — the mistake the pipeline thresholds made in v1.419.0."""
        flags = {m for rows in _table().values() for _, m in rows}
        self.assertEqual(flags, {0, 1})

    def test_the_field_ladder_is_mandatory_on_entry_tickets(self):
        """A technician who has not done confined-space entry may not enter a drained basin.
        That is not a matter of degree."""
        table = _table()
        for rung in ("Junior Technician", "Senior Technician", "Master Technician"):
            with self.subTest(position=rung):
                rows = dict((c, m) for c, m in table[rung])
                self.assertEqual(rows.get("Confined Space Entry"), 1)
                self.assertEqual(rows.get("Lockout / Tagout"), 1)

    def test_commercial_driving_credentials_go_to_nobody(self):
        """Seeded in the taxonomy for completeness, but they apply to vehicles over 26,000 lb
        and nothing in this fleet qualifies. Assigning them would manufacture a permanent gap
        against a rule that does not apply to this company."""
        used = {c for rows in _table().values() for c, _ in rows}
        self.assertEqual(used & NEVER_ASSIGNED, set())

    def test_office_roles_are_not_given_entry_tickets(self):
        """A CEO who has not done confined-space entry is not a compliance gap."""
        table = _table()
        for office in ("Chief Executive Officer", "Marketing Specialist", "Software Engineer"):
            with self.subTest(position=office):
                creds = {c for c, _ in table[office]}
                self.assertNotIn("Confined Space Entry", creds)
                self.assertNotIn("Lockout / Tagout", creds)


class TestTheSeedCannotOverwriteAHandEdit(unittest.TestCase):
    def test_it_only_seeds_an_empty_requirements_table(self):
        """This puts in a starting point that was never there; it does not enforce a policy.
        The same predicate makes it safe to run twice."""
        code = _code(SEED)
        self.assertIn('doc.get("requirements")', code)
        at = code.index('doc.get("requirements")')
        self.assertIn("return False", code[at : at + 120])

    def test_it_saves_through_the_doc_api(self):
        """`Position` is a NestedSet; its `on_update` maintains lft/rgt and repairs
        descendants. Writing child rows directly to save milliseconds is how a tree gets
        corrupted."""
        code = _code(SEED)
        self.assertIn("doc.save(ignore_permissions=True)", code)
        self.assertNotIn("tabPosition Requirement", code)

    def test_it_cannot_abort_the_deploy(self):
        code = _code(SEED)
        self.assertIn("except Exception", code)
        self.assertIn('frappe.db.exists("DocType", "Position")', code)


class TestTheLeaverBackfillIsLeaversOnly(unittest.TestCase):
    def test_it_never_raises_a_joining_checklist(self):
        """Fifteen active employees times eight items is ~120 permanently-red rows nobody can
        honestly close, on the very dashboards this module built to surface outstanding work.
        A checklist nobody can complete teaches people to ignore checklists."""
        code = _code(BACKFILL)
        self.assertIn("LEAVING", code)
        self.assertNotIn("JOINING", code)

    def test_it_goes_through_the_sanctioned_seam(self):
        """`ensure_checklist` is idempotent on (employee, kind) and its own docstring names a
        patch as one of its three callers."""
        code = _code(BACKFILL)
        self.assertIn("ensure_checklist", code)
        self.assertIn('frappe.db.exists("DocType", "Onboarding Checklist")', code)

    def test_existence_is_checked_before_the_call_not_after(self):
        """`ensure_checklist` hands back the EXISTING name when there already is one, so a
        count taken from its return value reports every leaver as newly raised — the same
        shape as a backfill that matches nothing and logs itself a success."""
        code = _code(BACKFILL)
        at_check = code.index('frappe.db.exists("Onboarding Checklist"')
        at_call = code.index("ensure_checklist(employee")
        self.assertLess(at_check, at_call)

    def test_it_is_not_hard_coded_to_the_four(self):
        """The four are what this site has today. A site restored from an older backup, or
        one that loses somebody between writing and deploying, should get the same
        treatment."""
        code = _code(BACKFILL)
        self.assertIn('filters={"status": ("!=", "Active")}', code)
        for person in ("Farris", "Brimley", "Shefchik", "Larsen"):
            with self.subTest(name=person):
                self.assertNotIn(person, code)

    def test_the_joining_guard_it_relies_on_still_exists(self):
        """`ensure_checklist` refuses a JOINING checklist for a non-Active employee. The
        backfill's reasoning cites that; if it were removed, a future edit passing JOINING
        here would silently start creating them."""
        code = _code(ONBOARDING)
        self.assertIn('if row.get("status") and row.status != "Active":', code)


class TestBothAreRegistered(unittest.TestCase):
    def test_they_run_post_model_sync(self):
        text = PATCHES_TXT.read_text(encoding="utf-8")
        for module in (
            "erpnext_enhancements.patches.seed_position_credential_requirements",
            "erpnext_enhancements.patches.backfill_leaving_checklists",
        ):
            with self.subTest(patch=module):
                self.assertIn(module, text)
                self.assertIn("[post_model_sync]", text[: text.index(module)])


class TestTheAssertionsCannotPassVacuously(unittest.TestCase):
    def test_the_table_parser_finds_the_entries(self):
        table = _table()
        self.assertGreaterEqual(len(table), 18)
        self.assertTrue(all(rows for rows in table.values()))

    def test_the_stripper_keeps_the_code_and_drops_the_prose(self):
        code = _code(SEED)
        self.assertIn("def execute", code)
        self.assertNotIn("load-bearing", code)

    def test_every_credential_named_is_a_real_seeded_type(self):
        """A typo here writes a Link to a Credential Type that does not exist. The seed skips
        it silently — deliberately, so a missing type narrows the set rather than failing a
        migrate — which means a misspelling would cost that position a requirement and say
        nothing. Cross-checked against `seed_credential_types.TYPES`, the one place the
        taxonomy is defined.

        The first version of this test read JSON fixtures, found none (the types come from a
        patch, not a fixture) and **skipped** — passing over exactly the check it exists to
        make. Read from the real source instead."""
        self.assertEqual(
            _table_names(_TYPES_SOURCE, "TYPES", first_element_only=True) & {None},
            set(),
            "could not parse seed_credential_types.TYPES",
        )
        types = _table_names(_TYPES_SOURCE, "TYPES", first_element_only=True)
        self.assertGreaterEqual(len(types), 15)
        used = {c for rows in _table().values() for c, _ in rows}
        self.assertEqual(used - types, set(), f"unknown Credential Type(s): {used - types}")

    def test_the_unassigned_pair_really_is_in_the_taxonomy(self):
        """`test_commercial_driving_credentials_go_to_nobody` asserts two names are absent
        from the seed. If either were misspelled there, that assertion would pass for the
        wrong reason — so pin that both are real types."""
        types = _table_names(_TYPES_SOURCE, "TYPES", first_element_only=True)
        self.assertEqual(NEVER_ASSIGNED - types, set())


# Runs LAST, deliberately — see tests/test_test_collection.py.
if __name__ == "__main__":
    unittest.main()
