"""The safety check that reported clean because it had nothing to look at.

`training/compliance.py` fires on `Task` and `Sapphire Maintenance Record` validate
and warns when somebody is scheduled onto work they are not certified for. It has
been wired up since v1.215.0 and, on this site, **it has never produced a single
finding** — not because everybody is certified, but because of how `_uncertified`
was built.

It drew findings from three sources: an **open assignment**, a **revoked or
expired completion**, and a **completion past its expiry**. Every one of those
requires the person to have *already been assigned* the course. A Required course
somebody was never assigned yielded nothing at all. Prod carried zero
`Training Assignment Rule` rows and five assignments in total, ever — so the set
of people any of those three could describe was almost empty, and the advisory
returned "all clear" for everybody.

**Note the failure direction. It passes.** Same family as the PAD SPACE
trailing-space queries and the emptiness-keyed backfill: a check written the
obvious way reports clean forever and nobody goes looking, because a green result
is exactly what you were hoping for.

The fix is a fourth source — a Required course this person's rules say they owe,
with nothing to show for it — and the narrowing that makes it usable: scoped by
the **assignment engine's own targeting**, so it asks "does this person owe it?"
rather than "does the course exist?". Without that, every technician would be
warned about "Accounting in ERPNext" and the advisory would go from useless to
ignored, which is worse.

It stays **warn-only**. A hard gate on dispatch does not stop the visit; it moves
the visit off the books and into somebody's truck where nobody can see it.

Run: python -m unittest erpnext_enhancements.tests.test_training_dispatch_advisory
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
COMPLIANCE = APP / "training/compliance.py"
PATCH = APP / "patches/enable_uncertified_dispatch_warning.py"
PATCHES_TXT = APP / "patches.txt"


def _text(path):
    return path.read_text(encoding="utf-8")


def _fn(name, path=COMPLIANCE):
    """A top-level function's executable source, docstring stripped.

    Stripped because these docstrings quote the very strings being asserted — the
    account of the defect names every source it used to have. A raw scan reads the
    explanation as the code.
    """
    src = _text(path)
    lines = src.splitlines()
    for node in ast.parse(src).body:
        if not (isinstance(node, ast.FunctionDef) and node.name == name):
            continue
        stmts = node.body
        if (
            stmts
            and isinstance(stmts[0], ast.Expr)
            and isinstance(stmts[0].value, ast.Constant)
            and isinstance(stmts[0].value.value, str)
        ):
            stmts = stmts[1:]
        return "\n".join(lines[stmts[0].lineno - 1 : node.end_lineno]) if stmts else ""
    raise AssertionError(f"{name} not found in {path.name}")


class TestItCanActuallyFire(unittest.TestCase):
    def test_there_is_a_never_assigned_source(self):
        """The three original sources all require an existing assignment, so on a
        site with no assignment rules the advisory could describe nobody."""
        self.assertIn("_courses_owed_by", _fn("_uncertified"))

    def test_the_never_assigned_source_is_narrowed_per_person(self):
        """Unnarrowed, every technician gets warned about 'Accounting in ERPNext'
        and the advisory goes from useless to ignored, which is worse."""
        self.assertIn("def _courses_owed_by(", _text(COMPLIANCE))

    def test_the_narrowing_asks_the_assignment_engine(self):
        """'Who owes this course' is decided in one place. A parallel rule here
        would drift from it quietly, since the two are only ever compared by
        somebody wondering why a warning did or did not appear."""
        body = _fn("_courses_owed_by")
        self.assertIn("_matching_rule", body)

    def test_it_fails_loud_rather_than_silent(self):
        """A compliance check that narrows to nothing because an import failed is
        worse than one that is briefly noisy."""
        body = _fn("_courses_owed_by")
        self.assertIn("return set(required)", body)

    def test_optional_courses_are_never_a_finding(self):
        self.assertIn('"weight": "Required"', _fn("_required_courses"))

    def test_a_live_completion_still_silences_everything(self):
        """The narrowing must not resurrect a course somebody has genuinely passed."""
        body = _fn("_uncertified")
        self.assertIn("valid = _currently_valid_courses(user)", body)
        self.assertIn("course in valid", body)


class TestItStaysAdvisory(unittest.TestCase):
    def test_nothing_in_the_module_throws(self):
        """A hard gate does not stop the visit, it moves the visit off the books.
        Asserted through the AST so a `frappe.throw` cannot arrive later disguised
        as a helper call."""
        tree = ast.parse(_text(COMPLIANCE))
        throws = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "throw"
        ]
        self.assertEqual(
            throws, [], "compliance.py can throw, which would block a dispatch rather than warn"
        )

    def test_the_switch_is_turned_on_now_that_it_means_something(self):
        """Enabling it before now would have changed nothing at all. A switch left
        off is the check not firing."""
        self.assertTrue(PATCH.exists())
        self.assertIn(
            "erpnext_enhancements.patches.enable_uncertified_dispatch_warning",
            _text(PATCHES_TXT),
        )

    def test_the_patch_never_overwrites_a_deliberate_setting(self):
        """Writes only over the value this app shipped. A Single with no row for a
        field is the same as 0 — `bench migrate` adds no row and `load_from_db`
        applies no defaults — so both are what it writes over."""
        body = _fn("execute", PATCH)
        self.assertIn('if current not in (None, "", "0", 0):', body)
        self.assertIn("return", body)


if __name__ == "__main__":
    unittest.main()
