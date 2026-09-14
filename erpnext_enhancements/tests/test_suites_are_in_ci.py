"""Every bench-free test suite must be named in ``.github/workflows/ci.yml``.

A suite this repo never runs is not "skipped", it is **invisible**: it stays green
in the reviewer's head, it is counted in "175 test files", and it asserts nothing.
CLAUDE.md records one instance already — the QuickBooks suite "ran nowhere and
broke unnoticed for weeks" because it was appended to a ``unittest`` module list
while being plain pytest functions, which ``python -m unittest`` silently collects
nothing from.

**Why this cannot be a test inside each suite.** Several modules here carry their
own ``test_it_is_wired_into_ci``. That idiom is sound for a suite already running
and *circular* for one that is not: if the file is absent from ``ci.yml`` then CI
never runs the file, so the assertion inside it never executes. The check has to
live somewhere that does run, and assert about everyone.

It found two on the day it was written, both green and both running nowhere:
``test_travel_ics`` (nine pytest-style functions) and
``test_product_configurator_engine`` (twenty-seven unittest tests).

**The rule, and why it is measured rather than listed.** Most suites in this app
genuinely need a real bench — Frappe v16's test-record auto-generation walks the
whole ERPNext doctype dependency graph, which is why the integration job was
removed. Those belong out of CI. Rather than keep a hand-written list of which,
this asks the question directly: a suite absent from ``ci.yml`` must **fail to
import**. That is the ground truth of "needs a bench", it cannot rot, and it
cannot be satisfied by filing a name somewhere.

Run: python -m unittest erpnext_enhancements.tests.test_suites_are_in_ci
"""

import ast
import importlib
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
TESTS = APP / "tests"
CI = APP.parent / ".github" / "workflows" / "ci.yml"

# Suites that import cleanly without a bench and still, deliberately, are not run
# in CI. Each needs a reason, and the reason has to survive being read aloud.
ALLOWED_ABSENT = {
    "test_ai_gating_integration": (
        "Imports without a bench but every one of its nine tests self-skips "
        "without one, so a CI step would assert nothing while reporting green. "
        "Left out rather than wired in to say so honestly."
    ),
    "test_assistant_tools_integration": (
        "Same shape: nine tests, all self-skipping without a bench."
    ),
}


def suites():
    return sorted(path.stem for path in TESTS.glob("test_*.py"))


def ci_text():
    return CI.read_text(encoding="utf-8")


def ci_lines():
    return ci_text().splitlines()


def runner_for(module):
    """'pytest' | 'unittest' | None — how ci.yml actually invokes ``module``.

    Walks back from each mention to the nearest ``python -m <runner>``; a step's
    command may span several lines, so the mention and the runner are rarely on
    the same one.
    """
    lines = ci_lines()
    found = set()
    for index, line in enumerate(lines):
        if module not in line:
            continue
        for back in range(index, max(index - 20, -1), -1):
            if "python -m pytest" in lines[back]:
                found.add("pytest")
                break
            if "python -m unittest" in lines[back]:
                found.add("unittest")
                break
    if "pytest" in found:
        return "pytest"
    if "unittest" in found:
        return "unittest"
    return None


def is_pytest_style(module):
    """True when the module's tests are top-level functions and nothing else.

    ``python -m unittest`` collects ``TestCase`` subclasses only, so a module of
    bare ``def test_*`` functions run under unittest reports success having run
    nothing at all.
    """
    tree = ast.parse((TESTS / f"{module}.py").read_text(encoding="utf-8"))
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    ]
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any("TestCase" in ast.unparse(base) for base in node.bases)
    ]
    return bool(functions) and not classes


def imports_without_a_bench(module):
    try:
        importlib.import_module(f"erpnext_enhancements.tests.{module}")
        return True
    except Exception:
        return False


class TestTheScanWorks(unittest.TestCase):
    """Anti-vacuity. Every assertion below iterates a list; an empty one passes."""

    def test_the_suites_are_found(self):
        self.assertGreater(len(suites()), 100, f"only {len(suites())} suites found under {TESTS}")

    def test_the_workflow_is_found(self):
        self.assertTrue(CI.is_file(), f"no workflow at {CI}")
        self.assertGreater(len(ci_text()), 5000)

    def test_most_suites_are_wired(self):
        """If the substring match broke, everything would look unwired and the
        real assertion would drown in 175 failures rather than naming the one
        that matters."""
        wired = [name for name in suites() if name in ci_text()]
        self.assertGreater(len(wired), 100, f"only {len(wired)} of {len(suites())} matched")


class TestEveryBenchFreeSuiteRuns(unittest.TestCase):
    def test_an_absent_suite_really_does_need_a_bench(self):
        text = ci_text()
        stowaways = []
        for name in suites():
            if name in text or name in ALLOWED_ABSENT:
                continue
            if imports_without_a_bench(name):
                stowaways.append(name)
        self.assertEqual(
            stowaways,
            [],
            f"{stowaways} import without a bench and are named nowhere in ci.yml, "
            "so they run nowhere. Add a step for each (a pytest step if its tests "
            "are plain functions), or add it to ALLOWED_ABSENT with a reason.",
        )

    def test_the_exceptions_still_exist(self):
        """An allowlist that outlives its subject stops being a record."""
        gone = sorted(set(ALLOWED_ABSENT) - set(suites()))
        self.assertEqual(gone, [], f"ALLOWED_ABSENT names {gone}, which no longer exist")

    def test_every_exception_says_something(self):
        for name, reason in ALLOWED_ABSENT.items():
            self.assertGreater(len(reason.strip()), 40, f"{name}'s reason is a placeholder")

    def test_an_exception_is_not_also_wired(self):
        """Being run is the opposite of the claim being made."""
        both = sorted(name for name in ALLOWED_ABSENT if name in ci_text())
        self.assertEqual(both, [], f"{both} are in ci.yml AND in ALLOWED_ABSENT")


class TestPytestSuitesAreRunByPytest(unittest.TestCase):
    """The split between the two runners is load-bearing, not stylistic.

    ``python -m unittest`` cannot collect a module of plain ``def test_*``
    functions. It does not error — it reports success having run zero tests,
    which is indistinguishable in a CI log from a suite that passed.
    """

    def test_the_detector_finds_the_known_cases(self):
        """Anti-vacuity: the QuickBooks suite is the documented example."""
        self.assertTrue(is_pytest_style("test_quickbooks_online"))
        self.assertFalse(is_pytest_style("test_workspaces"))

    def test_no_pytest_suite_is_run_by_unittest(self):
        wrong = []
        for name in suites():
            if name not in ci_text():
                continue
            if is_pytest_style(name) and runner_for(name) == "unittest":
                wrong.append(name)
        self.assertEqual(
            wrong,
            [],
            f"{wrong} are plain pytest functions but ci.yml runs them with "
            "unittest, which collects nothing from them and still exits 0.",
        )


if __name__ == "__main__":
    unittest.main()
