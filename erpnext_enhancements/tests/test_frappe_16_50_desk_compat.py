"""Desk customisations that frappe v16.50.0 broke without an error (v1.574.2).

Production went from frappe 16.36.1 to 16.50.0 on 2026-10-06 with this app unchanged,
and three of our desk customisations stopped working with nothing in the console, the
Error Log or the build output. Each guard below holds the fix to what 16.50.0 does,
statically, because none of the failures is visible to anything but a person looking:

1. **FontAwesome.** frappe removed ``@import ".../font-awesome.min.css"`` from its
   desk bundle, so every ``fa fa-*`` icon here drew nothing — icon-only buttons such
   as comment Reply/Edit/Delete became empty boxes. ``desk_addons.bundle.scss`` now
   imports the same file, and must keep doing so while any of that markup remains.
2. **The desk sidebar.** ``auto_collapse_sidebar.js`` clicked ``.sidebar-toggle-btn``
   on narrow screens to fold a form's sidebar; in 16.50.0 that button folds the DESK
   sidebar, persists it, and beside a pinned Dock hides it outright, Help menu and all.
   Deleted, and nothing in this app may drive that toggle again. The one-time heal for
   what it left behind is executed in ``scripts/test_sidebar_collapse_heal.js``.
3. **/desk tile names.** frappe scoped desktop.css to ``body.desktop-page``, which
   outranked our rules that let a tile's name wrap, so "Inventory E…" came back. The
   cascade is resolved here against frappe's pinned rules — asserting that our rule
   EXISTS is not asserting that it APPLIES, which is the lesson of
   ``test_activity_first_tab_only`` and ``test_dropdown_stacking``.

CI has no frappe checkout, so the frappe side of each contract is pinned below with the
version it was read from. Re-read them on the next frappe upgrade.

Run: python -m unittest erpnext_enhancements.tests.test_frappe_16_50_desk_compat
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
PUBLIC_JS = APP / "public" / "js"
SCSS_ENTRY = APP / "public" / "css" / "desk_addons.bundle.scss"
DESK_CSS = APP / "public" / "css" / "desk_enhancements.bundle.css"
JS_ENTRY = PUBLIC_JS / "erpnext_enhancements.bundle.js"
HEAL = PUBLIC_JS / "global_enhancements" / "sidebar_collapse_heal.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# Exactly the line frappe v16.36.1 had at desk.bundle.scss:1. The path still exists at
# v16.50.0 (frappe/public/css/fonts/fontawesome/, same blob 960587be), and its @font-face
# urls are absolute /assets/frappe/... paths. Keep the .css suffix: see the comment above
# the import in desk_addons.bundle.scss for why it resolves where a relative one cannot.
FA_IMPORT = '@import "frappe/public/css/fonts/fontawesome/font-awesome.min.css";'

# Markup or CSS that needs the FontAwesome font: an `fa fa-x` class pair, or the font
# family named directly (the Projects Dashboard sort arrows draw glyphs by code point).
FA_USAGE = re.compile(r"\bfa\s+fa-[a-z0-9-]+|font-family:\s*[\"']?FontAwesome\b")

# Desk-side sources; vendored libraries carry their own icon strings and are not ours.
SOURCE_SUFFIXES = (".js", ".css", ".scss", ".html", ".py")
SKIP_PARTS = {"node_modules", "dist", "lib", "tests"}
SKIP_NAMES = {"vue.global.js"}

# What frappe v16.50.0 frappe/desk/page/desktop/desktop.css sets on a tile's caption and
# name, which ours must override: (selector, property, value). It loads AFTER our bundle
# (a page stylesheet, injected when /desk opens), so it wins every tie.
FRAPPE_TILE_RULES = (
    ("body.desktop-page .icon-caption", "height", "35px"),
    ("body.desktop-page .icon-caption", "width", "100%"),
    ("body.desktop-page .icon-title", "white-space", "nowrap"),
    ("body.desktop-page .icon-title", "font-size", "var(--text-md)"),
)

# The overrides, by the class their rightmost compound names, and the value ours must set.
OUR_TILE_OVERRIDES = (
    (".icon-caption", "height", "auto"),
    (".icon-caption", "width", "calc(100% + 24px)"),
    (".icon-title", "white-space", "normal"),
    (".icon-title", "font-size", "var(--text-xs)"),
)


def read(path):
    return path.read_text(encoding="utf-8")


def strip_comments(text):
    """Block comments, and whole lines that are // comments. Code after a // on a line
    of code is kept, so a URL inside a string never truncates what is searched."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def desk_sources():
    for path in sorted(APP.rglob("*")):
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        relative = path.relative_to(APP)
        if SKIP_PARTS & set(relative.parts) or path.name in SKIP_NAMES:
            continue
        yield path


def fa_usages():
    found = []
    for path in desk_sources():
        if path == SCSS_ENTRY:
            continue
        for number, line in enumerate(read(path).splitlines(), start=1):
            if FA_USAGE.search(line):
                found.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{number}")
    return found


def specificity(selector):
    """(ids, classes + attributes + pseudo-classes, types). `:has()` adds nothing of its
    own and its argument's classes are counted with the rest, which is the spec's rule."""
    return (
        selector.count("#"),
        selector.count(".") + selector.count("[") + len(re.findall(r":(?!:)(?!has\b)\w", selector)),
        len(re.findall(r"(?:^|[\s>+~(])[a-z]", selector)),
    )


def rules(css):
    """(selector, declarations) for every rule, comments stripped. A flat scan: the inner
    rules of a plain @media block are yielded like any other."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors, body = match.group(1).strip(), match.group(2)
        if selectors.startswith("@"):
            continue
        for selector in selectors.split(","):
            if selector.strip():
                yield selector.strip(), body


def declared(body, prop):
    match = re.search(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;]+)", body)
    return match.group(1).strip() if match else None


class TestFontAwesomeStaysLoadedWhileWeUseIt(unittest.TestCase):
    def test_the_detector_recognises_both_forms(self):
        """Anti-vacuity that survives the lucide migration: checks the regex, not our code."""
        self.assertTrue(FA_USAGE.search('<i class="fa fa-pencil"></i>'))
        self.assertTrue(FA_USAGE.search('content: " \\f0de"; font-family: "FontAwesome";'))
        self.assertFalse(FA_USAGE.search('frappe.utils.icon("edit", "sm")'))

    def test_the_scan_reaches_the_desk_code(self):
        sources = {path.relative_to(APP).as_posix() for path in desk_sources()}
        self.assertIn("public/js/comments.js", sources)
        self.assertIn("custom_html_blocks/projects_dashboard.css", sources)

    def test_the_font_is_imported_while_any_fa_markup_remains(self):
        usages = fa_usages()
        if not usages:
            self.skipTest("no FontAwesome markup left; the stopgap import can go")
        scss = strip_comments(read(SCSS_ENTRY))
        self.assertIn(
            FA_IMPORT,
            scss,
            f"{len(usages)} FontAwesome usages draw blank without the font (frappe v16.50.0 "
            f"stopped loading it), e.g. {usages[:3]}. Restore the import in "
            "desk_addons.bundle.scss, or move them to frappe.utils.icon() first.",
        )

    def test_the_import_comes_before_our_own_styles(self):
        """A CSS @import after a rule is ignored; sass hoists it, but keep it first anyway so
        our own rules override FontAwesome's rather than the other way round."""
        scss = strip_comments(read(SCSS_ENTRY))
        if FA_IMPORT not in scss:
            self.skipTest("not imported")
        first = re.search(r"@import\s+[^;]+;", scss)
        self.assertEqual(first.group(0), FA_IMPORT)


class TestNothingDrivesTheDeskSidebarToggle(unittest.TestCase):
    # A jQuery or DOM selection of frappe's page-head toggle, or a call into the sidebar's
    # own toggle. Any of them folds the DESK sidebar in v16.50.0.
    DRIVES_TOGGLE = re.compile(
        r"""(\$|querySelector(All)?|closest|find)\(\s*["'][^"']*\.sidebar-toggle(-btn)?\b"""
        r"""|\.toggle_width\s*\("""
        r"""|\.sidebar\.(close|toggle_width)\s*\("""
    )

    def test_the_detector_catches_what_the_deleted_script_did(self):
        self.assertTrue(self.DRIVES_TOGGLE.search('$(".sidebar-toggle-btn, .sidebar-toggle").first()'))
        self.assertTrue(self.DRIVES_TOGGLE.search("frappe.app.sidebar.toggle_width();"))
        self.assertTrue(self.DRIVES_TOGGLE.search('document.querySelector(".sidebar-toggle-btn")'))
        self.assertFalse(self.DRIVES_TOGGLE.search("frappe.app.sidebar.open();"))

    def test_the_auto_collapse_script_is_gone(self):
        self.assertFalse((PUBLIC_JS / "global_enhancements" / "auto_collapse_sidebar.js").exists())
        imports = re.findall(r'^import "([^"]+)";', strip_comments(read(JS_ENTRY)), flags=re.M)
        self.assertGreater(len(imports), 30, "the bundle's imports were not found")
        self.assertNotIn("./global_enhancements/auto_collapse_sidebar.js", imports)

    def test_no_script_of_ours_drives_the_toggle(self):
        offenders = []
        for path in desk_sources():
            if path.suffix not in (".js", ".html"):
                continue
            for number, line in enumerate(strip_comments(read(path)).splitlines(), start=1):
                if self.DRIVES_TOGGLE.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "In frappe v16.50.0 .sidebar-toggle-btn folds the DESK module sidebar and saves "
            "that per browser; beside a pinned Dock it hides the sidebar and the Help menu.",
        )

    def test_the_heal_ships_and_its_executed_test_runs_in_ci(self):
        self.assertTrue(HEAL.is_file())
        self.assertIn('import "./global_enhancements/sidebar_collapse_heal.js";', read(JS_ENTRY))
        self.assertIn("node scripts/test_sidebar_collapse_heal.js", read(CI))


class TestDeskTileNamesOutrankFrappe(unittest.TestCase):
    def ours(self, target, prop):
        """The strongest of our rules setting `prop` on a tile's `target` element."""
        best = None
        for selector, body in rules(read(DESK_CSS)):
            if ".desktop-icon" not in selector or target not in selector.split()[-1]:
                continue
            value = declared(body, prop)
            if value is None:
                continue
            candidate = ("!important" in value, specificity(selector), selector, value)
            if best is None or candidate[:2] > best[:2]:
                best = candidate
        return best

    def test_the_specificity_rule_matches_the_spec(self):
        self.assertEqual(specificity("body.desktop-page .icon-title"), (0, 2, 1))
        self.assertEqual(specificity(".desktop-icon .icon-title"), (0, 2, 0))
        self.assertEqual(specificity("body.desktop-page .desktop-icon:has(> .icon-caption)"), (0, 3, 1))

    def test_every_override_still_exists(self):
        for target, prop, value in OUR_TILE_OVERRIDES:
            with self.subTest(target=target, prop=prop):
                found = self.ours(target, prop)
                self.assertIsNotNone(found, f"no rule of ours sets {prop} on {target}")
                self.assertEqual(found[3], value)

    def test_every_override_beats_frappes_rule(self):
        """frappe's page CSS loads after our bundle, so ours must be strictly stronger."""
        for frappe_selector, prop, frappe_value in FRAPPE_TILE_RULES:
            target = frappe_selector.split()[-1]
            with self.subTest(selector=frappe_selector, prop=prop):
                found = self.ours(target, prop)
                self.assertIsNotNone(found, f"no rule of ours overrides {frappe_selector} {{{prop}}}")
                important, ours, selector, _ = found
                theirs = (False, specificity(frappe_selector))
                self.assertGreater(
                    (important, ours),
                    theirs,
                    f"{selector!r} {ours} loses to frappe v16.50.0's {frappe_selector!r} "
                    f"{theirs[1]} ({prop}: {frappe_value}), which loads later and wins ties.",
                )

    def test_the_phone_size_rule_is_still_inside_its_media_query(self):
        css = re.sub(r"/\*.*?\*/", "", read(DESK_CSS), flags=re.S)
        block = re.search(
            r"@media screen and \(max-width: 380px\)\s*\{\s*([^{}]+)\{([^{}]*)\}\s*\}", css
        )
        self.assertIsNotNone(block, "the 380px media rule for the tile name is gone")
        self.assertIn(".icon-title", block.group(1))
        self.assertEqual(declared(block.group(2), "font-size"), "var(--text-xs)")


if __name__ == "__main__":
    unittest.main()
