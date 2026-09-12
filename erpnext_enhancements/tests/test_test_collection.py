# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every test in a suite must be collectable **both** ways it is actually run.

`python -m unittest erpnext_enhancements.tests.<module>` — what `ci.yml` does — imports
the module and collects every `TestCase` in it, wherever it is declared. `python
erpnext_enhancements/tests/<module>.py` — what a developer does while working — executes
the file top to bottom and calls `unittest.main()` *at the point the block appears*. So
anything declared **after** an `if __name__ == "__main__":` block is invisible to the
second one.

That is not a style question. It fails in the direction that looks fine:

* `test_training_boot_wire.py` had the block at line 609 of 1328 and collected **29 of
  100** tests when run directly.
* `test_training_runtime_regressions.py`: **31 of 91**.
* `test_training_canvas.py`: **62 of 94** — the 32 that vanished were every suite added
  after it, including the checkpoint model, AI drafting and the draft preview, i.e.
  exactly the work whose author was most likely to be running that file directly.

Across ten suites, **211 tests** were invisible this way (found and fixed in v1.416.0).
Nothing was unguarded *in CI*, which is why it survived so long — but a developer
checking their own change the obvious way got a green run over a third of nothing, and a
green run over nothing is worse than no run at all.

This is the same family as the `unittest` / `pytest` split recorded in `CLAUDE.md`: the
QuickBooks suite ran nowhere for weeks because `python -m unittest` silently cannot
collect pytest-style function tests. Both are collection failures that report success.

Bench-free, pure filesystem and `ast`. Runs in CI on its own step.

Run: python -m unittest erpnext_enhancements.tests.test_test_collection
"""

import ast
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent


def _main_guard_line(tree):
    """Line number of the top-level ``if __name__ == "__main__":``, or None.

    Matched on the AST rather than by string search, so a mention inside a docstring
    or comment — this module's own docstring names it repeatedly — cannot be mistaken
    for the block itself. That is the same trap as an absence assertion matching the
    comment that explains the absence.
    """
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1:
            continue
        if not isinstance(test.ops[0], ast.Eq):
            continue
        left, right = test.left, test.comparators[0]
        if isinstance(left, ast.Name) and left.id == "__name__":
            if isinstance(right, ast.Constant) and right.value == "__main__":
                return node.lineno
    return None


class TestNoTestIsStrandedAfterTheMainGuard(unittest.TestCase):
    def test_every_suite_collects_the_same_tests_both_ways(self):
        offenders = []
        for path in sorted(TESTS.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            guard = _main_guard_line(tree)
            if guard is None:
                continue
            stranded = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.lineno > guard
            ]
            if stranded:
                offenders.append(
                    "%s: %d declaration(s) after the __main__ guard at line %d — %s"
                    % (path.name, len(stranded), guard, ", ".join(stranded[:4]))
                )

        self.assertEqual(
            offenders,
            [],
            "These are collected by CI but NOT by `python <file>`, so a developer "
            "running the suite directly gets a green pass over a subset:\n  "
            + "\n  ".join(offenders),
        )


class TestTheGuardItselfIsFindable(unittest.TestCase):
    """A check that can never fire is worse than no check.

    If `_main_guard_line` silently stopped matching — a refactor to `sys.exit(main())`,
    say — the test above would find no guard anywhere, strand nothing, and pass forever
    over the exact defect it exists to catch. So assert the detector still detects.
    """

    def test_the_detector_finds_real_guards(self):
        found = [
            path.name
            for path in sorted(TESTS.glob("test_*.py"))
            if _main_guard_line(ast.parse(path.read_text(encoding="utf-8"))) is not None
        ]
        self.assertGreater(
            len(found),
            20,
            "The __main__ detector matched almost nothing, so the stranding check above "
            "is passing vacuously. Fix the detector, not this number.",
        )

    def test_the_detector_ignores_prose(self):
        """This module's own docstring contains the guard text several times."""
        tree = ast.parse('"""if __name__ == \'__main__\': see below."""\nx = 1\n')
        self.assertIsNone(_main_guard_line(tree))

    def test_the_detector_finds_a_guard_it_should(self):
        tree = ast.parse('if __name__ == "__main__":\n    pass\n')
        self.assertEqual(_main_guard_line(tree), 1)


# Runs LAST, deliberately — the rule this module enforces applies to this module.
if __name__ == "__main__":
    unittest.main()
