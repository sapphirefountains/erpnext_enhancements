# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""`@frappe.whitelist()` decorates whatever def comes next, and that is the whole risk.

On 2026-09-13 a refactor inserted two filter-builder helpers into `api/task_dashboard.py`
**between** an existing ``@frappe.whitelist()`` and the function it was written for. Python
allows blank lines between a decorator and its ``def``, so the file parsed, imported and
deployed with nothing anywhere reporting a problem. The result, live on production:

* ``get_task_dashboard_data`` — the Task Dashboard's only endpoint — was **no longer
  whitelisted**, so every call from the Desk failed;
* ``overdue_task_filters``, a pure helper returning a list of filter clauses, **was**
  whitelisted, and therefore callable by any signed-in user.

Confirmed against the running site by testing membership of ``frappe.whitelisted``:
``get_task_dashboard_data`` False, ``overdue_task_filters`` True, with
``get_wall_dashboard_data`` True as the control proving the mechanism worked.

Nothing caught it because every ordinary check still passed — the module imports, the tests
of the *builders* pass (they are plain functions), and the endpoint only fails when a
browser calls it. The decorator moved silently from a public endpoint to a private helper.

--------------------------------------------------------------------------------------
What this file asserts
--------------------------------------------------------------------------------------

Two rules, both cheap and both keyed on what actually went wrong:

1. **No helper is whitelisted.** A leading underscore, or a name ending ``_filters``,
   marks a function this app never intends to expose. If a decorator slides onto one, that
   is the signature of exactly this accident.
2. **Endpoints that must stay reachable, stay reachable.** A named inventory, because rule
   1 alone cannot notice a decorator that has gone missing entirely.

Bench-free: filesystem and `ast` only.

Run: python -m unittest erpnext_enhancements.tests.test_whitelist_placement
"""

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"tests", "node_modules", "__pycache__"}

#: Suffixes that mark a function as internal by this app's own convention.
HELPER_SUFFIXES = ("_filters",)

#: Endpoints whose loss would be silent and expensive. Not the full surface — these are
#: the ones where "the page is simply empty" is the only symptom.
MUST_STAY_WHITELISTED = {
    "api/task_dashboard.py": ("get_task_dashboard_data", "get_wall_dashboard_data"),
    "api/hr_dashboard.py": (
        "get_training_compliance",
        "get_timesheet_completeness",
        "get_headcount_movement",
        "get_people_calendar",
    ),
    # `get_morning_briefing` is the endpoint. `generate_briefing_for_user` is the
    # scheduler's entry point and is deliberately NOT whitelisted — the first draft of
    # this list named it from memory and failed here, which is the inventory doing its
    # job on its own author.
    "api/briefing.py": ("get_morning_briefing",),
    # The Record Matching page's whole surface (v1.474.0). `_entity_list`, a plain helper,
    # sits between the operator gate and these endpoints in that file -- exactly the shape
    # of the accident above -- so the endpoints are named here and the helper is caught
    # by rule 1 if a decorator ever slides onto it.
    "quickbooks_online/core/api.py": (
        "get_match_queue",
        "get_parked_transactions",
        "decide_match",
        "decide_matches",
        "confirm_match",
        "confirm_matches",
        "link_existing_record",
        "sync_entity",
    ),
}


def _whitelisted_functions(path):
    """Every function in this file carrying `@frappe.whitelist()`, by name."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover
        return set()
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            rendered = ast.unparse(decorator)
            if "whitelist" in rendered:
                out.add(node.name)
    return out


def _shipped_files():
    for path in sorted(APP.rglob("*.py")):
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


class TestNoHelperIsExposed(unittest.TestCase):
    def test_no_private_function_is_whitelisted(self):
        """A leading underscore says "not an endpoint" everywhere in this app."""
        offenders = []
        for path in _shipped_files():
            for name in _whitelisted_functions(path):
                if name.startswith("_"):
                    offenders.append(f"{path.relative_to(APP)}:{name}")
        self.assertEqual(offenders, [], f"private functions exposed: {offenders}")

    def test_no_filter_builder_is_whitelisted(self):
        """The exact shape of the 2026-09-13 accident: a `*_filters` helper inherited a
        decorator written for the endpoint below it."""
        offenders = []
        for path in _shipped_files():
            for name in _whitelisted_functions(path):
                if name.endswith(HELPER_SUFFIXES):
                    offenders.append(f"{path.relative_to(APP)}:{name}")
        self.assertEqual(offenders, [], f"filter builders exposed: {offenders}")


class TestTheEndpointsThatMustSurviveDo(unittest.TestCase):
    """Rule 1 cannot notice a decorator that vanished rather than moved."""

    def test_each_named_endpoint_is_still_whitelisted(self):
        for relative, names in MUST_STAY_WHITELISTED.items():
            found = _whitelisted_functions(APP / relative)
            for name in names:
                with self.subTest(endpoint=f"{relative}:{name}"):
                    self.assertIn(name, found, f"{name} lost its @frappe.whitelist()")


class TestTheGuardCannotPassVacuously(unittest.TestCase):
    def test_the_walk_finds_a_realistic_number_of_endpoints(self):
        total = sum(len(_whitelisted_functions(p)) for p in _shipped_files())
        self.assertGreater(total, 80, "the decorator scan is not finding endpoints")

    def test_it_would_catch_a_decorator_that_slid_onto_a_helper(self):
        """Reproduces the accident in miniature: a helper inserted between the decorator
        and the endpoint it was written for."""
        import tempfile

        source = (
            "import frappe\n\n\n"
            "@frappe.whitelist()\n\n"
            "def overdue_task_filters(today):\n    return []\n\n\n"
            "def get_task_dashboard_data():\n    return {}\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(source, encoding="utf-8")
            found = _whitelisted_functions(probe)
            self.assertEqual(found, {"overdue_task_filters"})
            self.assertNotIn("get_task_dashboard_data", found)

    def test_the_named_inventory_is_not_empty(self):
        self.assertTrue(MUST_STAY_WHITELISTED)
        for relative in MUST_STAY_WHITELISTED:
            with self.subTest(file=relative):
                self.assertTrue((APP / relative).is_file())

    def test_it_is_wired_into_ci(self):
        ci = (APP.parent / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("test_whitelist_placement", ci)


if __name__ == "__main__":
    unittest.main()
