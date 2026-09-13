"""Bench-free test: a module this app ships can actually install on an existing site.

The failure this exists for shipped on 2026-08-09. v1.261.0 added the `Chat` module —
`modules.txt` entry, ten DocType JSONs, three patches — merged, deployed, and `bench migrate`
exited **0** having installed none of it. No table, no `Module Def`, no exception, no failed
step. Two of the three patches were written to `Patch Log` as executed while doing nothing,
which in Frappe means they can never run again.

**Adding a module to `modules.txt` is not sufficient on an already-installed site.**
`frappe.model.sync.sync_for()` does not read `modules.txt`; it iterates
`frappe.local.app_modules`, a snapshot taken once in `frappe.init()` from the Redis key
`app_modules`. Nothing in `bench migrate` rebuilds it (`SiteMigration.setUp`'s
`frappe.clear_cache()` deletes the key but does not call `setup_module_map`). A migrate that
starts with a stale snapshot walks the *previous release's* module list and silently skips the
whole new module folder. `add_module_defs` — the only thing in the framework that creates
`Module Def` rows en masse — runs on `install-app` and never on migrate; on migrate a
`Module Def` appears only as a side effect of `DocType.on_update` → `make_module_and_roles`,
i.e. it is a *consequence* of the DocType import, never a precondition for it. So a
declarative `<module>/module_def/<name>.json` fixes nothing: `module_def` is not in
`IMPORTABLE_DOCTYPES`, and it would have to be discovered by walking the very folder that is
being skipped.

The invariant this file asserts, therefore:

1. every module folder that ships DocType JSONs is registered in `modules.txt`;
2. every module in `modules.txt` is an importable package (`sync_for` calls
   `frappe.get_module(app + "." + module)` on each one and dies if it is not);
3. **the app rebuilds the module map before model sync on every migrate** — the
   `before_migrate` hook, plus its one-shot `pre_model_sync` twin — and does it in the one
   order that works: delete the cache key *first*, because `setup_module_map` re-reads that
   key and only falls back to `modules.txt` when it comes back empty;
(A fourth assertion — that the two spent chat bootstrap patches had a successor and an
`after_migrate` backstop each — went in v1.426.0 with the chat module itself, ADR 0011. The
`Chat` module no longer exists; the 2026-08-09 failure described above is still the reason
this file exists, and is still reachable by the next module anyone adds.)

Companion suites, deliberately separate: `test_doctype_modules.py` asserts each DocType's
declared `module` against its directory; `test_hook_targets_resolve.py` asserts the dotted
paths below point at real functions. This one asserts that the module gets *installed at all*.

Filesystem + `ast` only. No bench, no `frappe` import.

Run: python -m unittest erpnext_enhancements.tests.test_module_installability
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]  # the erpnext_enhancements/ package
MODULES_TXT = APP_DIR / "modules.txt"
HOOKS_PY = APP_DIR / "hooks.py"
PATCHES_TXT = APP_DIR / "patches.txt"
MODULE_MAP_PY = APP_DIR / "setup" / "module_map.py"
APP_NAME = "erpnext_enhancements"

#: The every-migrate rebuild. Without it, model sync can be shown a module list from the
#: previous release and skip a whole module without saying so.
REFRESHER = f"{APP_NAME}.setup.module_map.refresh_app_module_map"

#: The one-shot twin, in `[pre_model_sync]` — the only patch section that runs before
#: `sync_all()`. `[post_model_sync]` would be a whole migrate too late.
MAP_PATCH = f"{APP_NAME}.patches.refresh_module_map"


def _scrub(name):
    """Mirror frappe.scrub: spaces and hyphens to underscores, lowercased."""
    return name.replace(" ", "_").replace("-", "_").lower()


def _registered_modules():
    text = MODULES_TXT.read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def _module_dirs_with_doctypes():
    """Directory names (scrubbed module names) that ship at least one DocType JSON."""
    found = set()
    for path in APP_DIR.glob("**/doctype/*/*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if data.get("doctype") != "DocType":
            continue
        parts = path.relative_to(APP_DIR).parts
        found.add(parts[parts.index("doctype") - 1])
    return found


def _hook_list(name):
    """The app-dotted strings inside a top-level ``<name> = [ ... ]`` list in hooks.py.

    Read as text rather than by importing hooks.py, which would need frappe. Anchored on
    ``<name> = [`` and not on the first mention of the word — these hook names appear in
    prose comments hundreds of lines earlier, and slicing from a comment finds a ``]``
    belonging to something else and silently reads an empty list.
    """
    source = HOOKS_PY.read_text(encoding="utf-8")
    start = source.index(f"{name} = [")
    block = source[start : source.index("\n]", start)]
    # Strip comment lines before matching. These hook blocks are mostly prose in this repo,
    # and a dotted path quoted inside a comment would register as a real entry -- which
    # would keep this guard green after someone deleted the entry it exists to protect.
    block = "\n".join(line for line in block.splitlines() if not line.lstrip().startswith("#"))
    return re.findall(rf'"({APP_NAME}\.[\w.]+)"', block)


def _patch_sections():
    """``{"pre_model_sync": [...], "post_model_sync": [...]}`` from patches.txt.

    Comment lines are dropped the same way ``frappe.get_file_items`` drops them: a line
    whose first character is ``#``. A trailing comment on a patch line is *not* stripped by
    Frappe — it becomes part of the Patch Log key — so lines are kept whole here too.
    """
    sections = {}
    current = None
    for raw in PATCHES_TXT.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections[current] = []
            continue
        if line.startswith("#") or current is None:
            continue
        sections[current].append(line)
    return sections


def _function(path, name):
    """The top-level FunctionDef named ``name``, or None."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _function_source(path, name):
    """The source lines of one function — **not** the whole module.

    Module docstrings here quote the framework code they are about, so a naive
    ``source.find(...)`` matches the prose and reads the wrong order.
    """
    function = _function(path, name)
    if function is None:
        return ""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[function.lineno - 1 : function.end_lineno])


MODULES = _registered_modules()
DOCTYPE_DIRS = _module_dirs_with_doctypes()
SECTIONS = _patch_sections()


class TestTheScanWorks(unittest.TestCase):
    """Guards every assertion below from passing on an empty set."""

    def test_modules_txt_is_read(self):
        self.assertGreater(len(MODULES), 20, f"only {len(MODULES)} modules read from {MODULES_TXT}")

    def test_doctype_folders_are_found(self):
        self.assertGreater(len(DOCTYPE_DIRS), 15, "the DocType glob found almost nothing")

    def test_hook_lists_are_read(self):
        self.assertTrue(_hook_list("before_migrate"), "could not read the before_migrate list")
        self.assertTrue(_hook_list("after_migrate"), "could not read the after_migrate list")

    def test_patch_sections_are_read(self):
        self.assertTrue(SECTIONS.get("pre_model_sync"), "no [pre_model_sync] entries read")
        self.assertTrue(SECTIONS.get("post_model_sync"), "no [post_model_sync] entries read")


class TestEveryShippedModuleCanInstall(unittest.TestCase):
    def test_every_doctype_folder_is_registered_in_modules_txt(self):
        registered = {_scrub(module) for module in MODULES}
        orphans = sorted(DOCTYPE_DIRS - registered)
        self.assertEqual(
            orphans,
            [],
            f"{orphans} ship DocType JSONs but are not in modules.txt. sync_for() only walks "
            f"modules in the app's module list, so every DocType in those folders is invisible "
            f"to `bench migrate` — no table, no error, exit 0.",
        )

    def test_every_registered_module_is_an_importable_package(self):
        missing = [
            module
            for module in MODULES
            if not (APP_DIR / _scrub(module) / "__init__.py").exists()
        ]
        self.assertEqual(
            missing,
            [],
            f"{missing} are listed in modules.txt but have no package directory. sync_for() "
            f"calls frappe.get_module('{APP_NAME}.<module>') for every entry and the migrate "
            f"dies on the ModuleNotFoundError.",
        )

    def test_model_sync_is_shown_the_current_module_list(self):
        """THE invariant. Every module above installs only if this hook is wired.

        `frappe.local.app_modules` is snapshotted at `frappe.init()` out of Redis and is
        never rebuilt during a migrate, so a module added in the release being deployed can
        be invisible to `sync_all()`. `before_migrate` is Frappe's `pre_schema_updates` —
        before both patch phases and before `sync_all()` — which is the only window in which
        rebuilding it helps.
        """
        self.assertIn(
            REFRESHER,
            _hook_list("before_migrate"),
            f"{REFRESHER} is not registered in before_migrate. Without it, a module that is "
            f"new in a release installs only if the deploy happens to start with a cold "
            f"app_modules cache — which is how the Chat module shipped and installed nothing "
            f"on 2026-08-09 while the deploy reported success.",
        )

    def test_the_one_shot_twin_runs_before_model_sync(self):
        pre = SECTIONS.get("pre_model_sync", [])
        post = SECTIONS.get("post_model_sync", [])
        self.assertTrue(
            any(line.split()[0] == MAP_PATCH for line in pre),
            f"{MAP_PATCH} is not in [pre_model_sync].",
        )
        self.assertFalse(
            any(line.split()[0] == MAP_PATCH for line in post),
            f"{MAP_PATCH} is in [post_model_sync], which runs AFTER sync_all() — a whole "
            f"migrate too late to make the module visible.",
        )

    def test_the_refresher_deletes_the_cache_key_before_rebuilding(self):
        """Order is load-bearing, and getting it wrong is a silent no-op.

        `setup_module_map(include_all_apps=True)` starts with `cache.get_value("app_modules")`
        and only falls back to reading `modules.txt` when that comes back empty. Call it
        without deleting the key first and it re-seats the same stale map.
        """
        source = _function_source(MODULE_MAP_PY, "refresh_app_module_map")
        self.assertTrue(source, "module_map.py defines no refresh_app_module_map()")
        delete_at = source.find("delete_value(CACHE_KEY)")
        rebuild_at = source.find("setup_module_map(include_all_apps=True)")
        self.assertNotEqual(delete_at, -1, "module_map.py never deletes the app_modules key")
        self.assertNotEqual(rebuild_at, -1, "module_map.py never calls setup_module_map")
        self.assertLess(
            delete_at,
            rebuild_at,
            "module_map.py calls setup_module_map before deleting the cached snapshot, so it "
            "re-reads the stale value and changes nothing.",
        )

if __name__ == "__main__":
    unittest.main()
