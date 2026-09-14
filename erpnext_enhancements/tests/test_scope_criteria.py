"""Bench-free tests for acceptance-criterion identity (WI-075 sub-phase B1).

Every downstream record in the quality chain — the inspection template row, the inspection
result, the NCR, the Quality Action — joins on ``criterion_key``. The property that matters is
not that keys exist; it is that **a key, once minted, never changes**, because the alternative
is a closed NCR silently repointing at a different standard a year later.

That property is invisible in a diff and invisible in a smoke test: re-minting on every save
looks completely normal until somebody inserts a row in the middle of the list. Hence this
suite. There is no Frappe integration-test job in CI, which is why the logic lives in
``quality/scope_criteria.py`` — importing no ``frappe`` — rather than inside the DocType
controller where it could not be reached. It sits under ``quality/`` and not beside the DocType
that uses it because ``project_enhancements/__init__.py`` imports ``frappe`` at module scope,
which would make the module unreachable here however pure it was.

Run: python -m unittest erpnext_enhancements.tests.test_scope_criteria
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements.quality import scope_criteria


class _Counter:
    """A deterministic stand-in for ``frappe.generate_hash``.

    The generator is injected precisely so this is possible: a function that reaches for a
    global random source cannot be asserted on, only observed.
    """

    def __init__(self):
        self.calls = 0

    def __call__(self, length=10):
        self.calls += 1
        return f"key{self.calls:0{max(length - 3, 1)}d}"


class _Row:
    """Stands in for a Frappe child row: attribute access, not dict access."""

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


def _criterion(**overrides):
    row = {
        "criterion": "Basin holds water",
        "pass_standard": "No measurable drop over 24h",
        "verification_method": "Test",
        "criterion_key": None,
    }
    row.update(overrides)
    return row


class TestMintMissingKeys(unittest.TestCase):
    def test_mints_a_key_for_every_row_that_lacks_one(self):
        rows = [_criterion(), _criterion(), _criterion()]
        minted = scope_criteria.mint_missing_keys(rows, _Counter())

        self.assertEqual(len(minted), 3)
        self.assertEqual(len({r["criterion_key"] for r in rows}), 3, "keys must be distinct")

    def test_is_a_no_op_on_rows_that_already_have_keys(self):
        """The whole contract. Re-running on a saved document must change nothing."""
        rows = [_criterion(criterion_key="existing-1"), _criterion(criterion_key="existing-2")]
        generator = _Counter()

        minted = scope_criteria.mint_missing_keys(rows, generator)

        self.assertEqual(minted, [])
        self.assertEqual(generator.calls, 0, "the generator must not even be consulted")
        self.assertEqual([r["criterion_key"] for r in rows], ["existing-1", "existing-2"])

    def test_mints_only_the_new_row_when_one_is_inserted_mid_list(self):
        """The scenario the stable key exists for: insert in the middle, keys above and below
        must not move, or every record pointing at them repoints silently."""
        rows = [
            _criterion(criterion_key="alpha"),
            _criterion(),  # freshly inserted between two saved rows
            _criterion(criterion_key="omega"),
        ]

        minted = scope_criteria.mint_missing_keys(rows, _Counter())

        self.assertEqual(len(minted), 1)
        self.assertEqual(rows[0]["criterion_key"], "alpha")
        self.assertEqual(rows[2]["criterion_key"], "omega")
        self.assertNotIn(rows[1]["criterion_key"], ("alpha", "omega"))

    def test_repeated_calls_converge(self):
        rows = [_criterion(), _criterion()]
        generator = _Counter()

        scope_criteria.mint_missing_keys(rows, generator)
        snapshot = [r["criterion_key"] for r in rows]
        scope_criteria.mint_missing_keys(rows, generator)
        scope_criteria.mint_missing_keys(rows, generator)

        self.assertEqual([r["criterion_key"] for r in rows], snapshot)
        self.assertEqual(generator.calls, 2, "two rows, two keys, ever")

    def test_works_on_frappe_style_rows_not_only_dicts(self):
        """Production passes Document children; the tests pass dicts. Both must work, or the
        suite would be exercising a code path production never takes."""
        rows = [_Row(criterion_key=None), _Row(criterion_key="kept")]

        scope_criteria.mint_missing_keys(rows, _Counter())

        self.assertTrue(rows[0].criterion_key)
        self.assertEqual(rows[1].criterion_key, "kept")

    def test_empty_and_none_are_tolerated(self):
        self.assertEqual(scope_criteria.mint_missing_keys([], _Counter()), [])
        self.assertEqual(scope_criteria.mint_missing_keys(None, _Counter()), [])

    def test_blank_string_counts_as_missing(self):
        """A key field cleared by hand reads '' rather than None and must be re-minted."""
        rows = [_criterion(criterion_key="")]
        self.assertEqual(len(scope_criteria.mint_missing_keys(rows, _Counter())), 1)


class TestDuplicateKeys(unittest.TestCase):
    def test_clean_table_reports_nothing(self):
        rows = [_criterion(criterion_key="a"), _criterion(criterion_key="b")]
        self.assertEqual(scope_criteria.duplicate_keys(rows), [])

    def test_catches_a_duplicated_row(self):
        """Frappe's grid row-duplicate action copies read-only fields too, so this is two
        clicks away rather than hypothetical."""
        rows = [
            _criterion(criterion_key="a"),
            _criterion(criterion_key="a"),
            _criterion(criterion_key="b"),
        ]
        self.assertEqual(scope_criteria.duplicate_keys(rows), ["a"])

    def test_unkeyed_rows_are_not_duplicates_of_each_other(self):
        rows = [_criterion(criterion_key=None), _criterion(criterion_key=None)]
        self.assertEqual(scope_criteria.duplicate_keys(rows), [])


class TestIncompleteRows(unittest.TestCase):
    def test_complete_rows_report_nothing(self):
        self.assertEqual(scope_criteria.incomplete_rows([_criterion()]), [])

    def test_reports_each_missing_field(self):
        rows = [_criterion(pass_standard="", verification_method=None)]
        problems = scope_criteria.incomplete_rows(rows)

        self.assertEqual(len(problems), 1)
        _idx, missing = problems[0]
        self.assertEqual(missing, ["pass_standard", "verification_method"])

    def test_whitespace_only_is_missing(self):
        """The reason this check is in Python and not SQL.

        Under MariaDB's default PAD SPACE collation a non-binary comparison ignores trailing
        spaces, so ``WHERE pass_standard <> ''`` treats ``"   "`` as present and the obvious
        SQL form of this test reports clean on data that is genuinely blank. Note the failure
        direction: it passes.
        """
        rows = [_criterion(criterion="   ", pass_standard="\t")]
        _idx, missing = scope_criteria.incomplete_rows(rows)[0]
        self.assertIn("criterion", missing)
        self.assertIn("pass_standard", missing)

    def test_uses_grid_position_when_idx_is_absent(self):
        rows = [_criterion(), _criterion(criterion="")]
        problems = scope_criteria.incomplete_rows(rows)
        self.assertEqual(problems[0][0], 2, "1-based, matching what the grid shows")

    def test_prefers_a_real_idx_when_present(self):
        rows = [_criterion(criterion="", idx=7)]
        self.assertEqual(scope_criteria.incomplete_rows(rows)[0][0], 7)


class TestRequiredFieldsAreDeclaredOnTheChildTable(unittest.TestCase):
    def test_every_lock_requirement_exists_as_a_field(self):
        """Guards a rename: REQUIRED_FOR_LOCK naming a field the JSON does not have would make
        every row look incomplete, and naming one it dropped would check nothing at all."""
        import json

        path = (
            REPO_ROOT
            / "erpnext_enhancements"
            / "project_enhancements"
            / "doctype"
            / "scope_acceptance_criterion"
            / "scope_acceptance_criterion.json"
        )
        with open(path, encoding="utf-8") as handle:
            fieldnames = {f["fieldname"] for f in json.load(handle)["fields"]}

        missing = [f for f in scope_criteria.REQUIRED_FOR_LOCK if f not in fieldnames]
        self.assertEqual(missing, [], f"REQUIRED_FOR_LOCK names fields the DocType lacks: {missing}")
        self.assertIn("criterion_key", fieldnames)


if __name__ == "__main__":
    unittest.main()
