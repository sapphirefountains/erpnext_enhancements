"""Does every dotted path in ``hooks.py`` point at something that exists?

One did not, and it took a production deploy down::

    Executing `after_migrate` hooks...
    AttributeError: module 'erpnext_enhancements.training.gamification'
                    has no attribute 'ensure_training_badges'

The hook was registered in Phase 4; the function was never written. Nothing
noticed, because a hook path is just a string until Frappe resolves it — and
Frappe resolves ``after_migrate`` at the very end of ``bench migrate``, so the
first thing to find out was the deploy, after every schema change had already
been applied.

This module resolves every path in ``hooks.py`` **statically**: it parses the
target module's AST and looks for the attribute. No import, so no bench, no site
and no side effects — which is what lets it run in CI on every push instead of
on the box at deploy time.

That last part is the point. A hook that names a missing function is not a subtle
bug; it is a typo that only a full migrate could surface. It should cost seconds
in CI, not a failed build step.

Run: python -m unittest erpnext_enhancements.tests.test_hook_targets_resolve
"""

import ast
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
HOOKS = APP / "hooks.py"
APP_NAME = "erpnext_enhancements"

# Paths that name something other than a python callable in this app.
IGNORE_PREFIXES = (
    # Other apps' modules — not ours to guarantee, and not importable here.
    "frappe.",
    "erpnext.",
)

# Asset bundle names live in the same list syntax and look exactly like dotted
# paths — `erpnext_enhancements.bundle.js` is a file, not a module. Extension is
# the honest discriminator; a python hook target never ends in one.
ASSET_SUFFIXES = (".js", ".css", ".json", ".html", ".svg", ".png", ".ico", ".woff2")


def _hook_source():
    return HOOKS.read_text(encoding="utf-8")


def _dotted_paths():
    """Every ``erpnext_enhancements.*`` string literal in hooks.py.

    Read from string literals rather than by importing hooks.py, because
    importing it pulls in ``frappe`` and there is no bench here.
    """
    tree = ast.parse(_hook_source())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip()
            if not value.startswith(APP_NAME + ".") or " " in value:
                continue
            if value.endswith(ASSET_SUFFIXES) or "/" in value:
                continue
            found.add(value)
    return found


def _module_path(dotted):
    """Filesystem path for a dotted module, or None."""
    parts = dotted.split(".")
    if parts[0] != APP_NAME:
        return None
    candidate = REPO_ROOT.joinpath(*parts).with_suffix(".py")
    if candidate.exists():
        return candidate
    package = REPO_ROOT.joinpath(*parts) / "__init__.py"
    if package.exists():
        return package
    return None


def _defines(path, attribute):
    """Whether a module's source defines a top-level name.

    Static: functions, classes, assignments and imports all count, because any of
    them makes ``getattr(module, name)`` succeed — which is exactly what Frappe
    does with these strings.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            if node.name == attribute:
                return True
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == attribute:
                    return True
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == attribute:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.asname or alias.name.split(".")[0]) == attribute:
                    return True
    return False


def _resolve(dotted):
    """``(ok, reason)`` for one hook target."""
    if _module_path(dotted) is not None:
        return True, ""  # the whole path is a module (e.g. an override module)
    module_part, _, attribute = dotted.rpartition(".")
    path = _module_path(module_part)
    if path is None:
        return False, f"no module {module_part}"
    if not _defines(path, attribute):
        return False, f"{module_part} has no attribute {attribute!r}"
    return True, ""


class TestTheScanWorks(unittest.TestCase):
    """Guards the assertions below from passing on an empty set."""

    def test_hooks_is_found(self):
        self.assertTrue(HOOKS.exists())
        self.assertGreater(len(_hook_source()), 5000)

    def test_paths_are_extracted(self):
        paths = _dotted_paths()
        self.assertGreater(len(paths), 40, "the extractor found almost nothing")

    def test_a_known_good_path_resolves(self):
        ok, reason = _resolve("erpnext_enhancements.training.setup.ensure_training_categories")
        self.assertTrue(ok, reason)

    def test_a_missing_attribute_is_detected(self):
        """The check itself, against the exact string that broke the deploy."""
        ok, _ = _resolve("erpnext_enhancements.training.gamification.ensure_training_badges")
        self.assertFalse(ok, "the resolver would not have caught the deploy failure")


class TestEveryHookTargetResolves(unittest.TestCase):
    def test_no_hook_names_something_that_does_not_exist(self):
        broken = []
        for dotted in sorted(_dotted_paths()):
            if dotted.startswith(IGNORE_PREFIXES):
                continue
            ok, reason = _resolve(dotted)
            if not ok:
                broken.append(f"{dotted} — {reason}")
        self.assertEqual(
            broken,
            [],
            "hooks.py names targets that do not exist; each of these fails a "
            "migrate at the moment Frappe resolves it:\n  " + "\n  ".join(broken),
        )

    def test_after_migrate_targets_resolve(self):
        """Called out separately because of *when* these run.

        `after_migrate` is the last thing `bench migrate` does, so a bad path here
        fails the deploy **after** every schema change has been applied — the most
        expensive possible moment to discover a typo.
        """
        source = _hook_source()
        # `after_migrate = [`, not the first mention of the word — it appears in
        # the module docstring 650 lines above the list, and slicing from there
        # found a `]` belonging to something else entirely and read an empty list.
        # A guard that silently matches nothing is worse than no guard.
        start = source.index("after_migrate = [")
        block = source[start : source.index("\n]", start)]
        targets = re.findall(rf'"({APP_NAME}\.[\w.]+)"', block)
        self.assertTrue(targets, "could not read the after_migrate list")
        broken = [t for t in targets if not _resolve(t)[0]]
        self.assertEqual(broken, [], f"after_migrate would fail on: {broken}")



class TestStarterBadgesAreEarnable(unittest.TestCase):
    """A seeded badge whose criterion nothing evaluates is permanently unearnable.

    ``_badge_is_earned`` answers a closed set of ``criteria_type`` values and
    returns ``False`` for anything else — deliberately, so a rule the module
    cannot answer never becomes a badge everybody has. That makes the seeder's
    strings a real contract with the awarder rather than free text.

    Also checked against the DocType's own Select options, since a value outside
    them cannot be saved at all.
    """

    @staticmethod
    def _seeded():
        source = (APP / "training/setup.py").read_text(encoding="utf-8")
        block = source[source.index("STARTER_BADGES = (") :]
        block = block[: block.index("\n)")]
        # Third element of each tuple is the criteria_type.
        return [
            match.group(1)
            for match in re.finditer(r'\("[^"]+",\s*"[^"]*",\s*"([^"]+)"', block)
        ]

    def test_the_seeder_is_readable(self):
        self.assertGreaterEqual(len(self._seeded()), 3)

    def test_every_seeded_criterion_is_one_the_awarder_evaluates(self):
        gamification = (APP / "training/gamification.py").read_text(encoding="utf-8")
        known = set(re.findall(r'^[A-Z_]+ = "([^"]+)"', gamification, re.M))
        unknown = sorted(set(self._seeded()) - known)
        self.assertEqual(
            unknown,
            [],
            f"seeded badges use {unknown}, which _badge_is_earned never matches — "
            f"they would sit in the list forever, unearnable",
        )

    def test_every_seeded_criterion_is_a_valid_select_option(self):
        import json

        doctype = json.loads(
            (APP / "training/doctype/training_badge/training_badge.json").read_text(
                encoding="utf-8"
            )
        )
        options = set()
        for field in doctype["fields"]:
            if field["fieldname"] == "criteria_type":
                options = {o for o in (field.get("options") or "").split("\n") if o.strip()}
        self.assertTrue(options, "could not read the criteria_type options")
        invalid = sorted(set(self._seeded()) - options)
        self.assertEqual(invalid, [], f"{invalid} are not valid criteria_type options")

    def test_it_does_not_seed_the_link_based_criteria(self):
        """`Course Completed` and `Category Completed` need a course or category
        that may not exist on a fresh site, and a badge pointing at a missing link
        is worse than no badge."""
        for kind in ("Course Completed", "Category Completed"):
            self.assertNotIn(kind, self._seeded())

if __name__ == "__main__":
    unittest.main()
