"""A hidden sidebar stays hidden on every page (v1.717.0, Nik 2026-10-22).

Bench-free: reads files only. The behaviour is executed, not grepped, in
``scripts/test_sidebar_memory.js``; this suite holds the seams that harness cannot see -- that the
module ships, that the old always-expand script is gone, that the stylesheet's selectors are the
ones the script's class names produce, that it applies only where a phone cannot be stranded, and
that CI runs both.

Run: python -m unittest erpnext_enhancements.tests.test_sidebar_memory
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
PUBLIC = APP / "public"
MODULE = PUBLIC / "js" / "global_enhancements" / "sidebar_memory.js"
BUNDLE = PUBLIC / "js" / "erpnext_enhancements.bundle.js"
LEGACY = PUBLIC / "js" / "erpnext_enhancements.js"
CSS = PUBLIC / "css" / "desk_enhancements.bundle.css"
DESK_NAV = PUBLIC / "js" / "training" / "desk_nav.js"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def read(path):
    return path.read_text(encoding="utf-8")


def strip_js_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))


def js_const(name):
    match = re.search(rf'const {name}\s*=\s*"([^"]+)"', read(MODULE))
    assert match, f"const {name} not found in sidebar_memory.js"
    return match.group(1)


def media_block(css, query):
    """The body of the first @media block whose condition is `query`, braces balanced."""
    start = css.index("@media " + query)
    open_at = css.index("{", start)
    depth = 0
    for i in range(open_at, len(css)):
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        if depth == 0:
            return css[open_at + 1 : i]
    raise AssertionError("unbalanced @media block")


class TestShips(unittest.TestCase):
    def test_the_module_is_in_the_global_bundle(self):
        self.assertTrue(MODULE.is_file())
        self.assertIn('import "./global_enhancements/sidebar_memory.js";', read(BUNDLE))

    def test_the_always_expand_script_is_gone(self):
        """It stripped body.sidebar-collapsed on every class change and resize, so a person's
        collapse never held. Nothing may bring it back."""
        code = strip_js_comments(read(LEGACY))
        for token in ("enforce_sidebar_expanded", "check_and_expand_sidebar", "_sidebar_observer_installed"):
            self.assertNotIn(token, code)
        self.assertNotRegex(code, r"removeClass\(\s*[\"']sidebar-collapsed")

    def test_the_rail_tells_the_module_it_mounted(self):
        """The module listens for this; the Training rail is built after page-change on some paths."""
        self.assertIn('"ee-page-sidebar-ready"', read(DESK_NAV))
        self.assertIn('"ee-page-sidebar-ready"', read(MODULE))


class TestStylesheetMatchesScript(unittest.TestCase):
    def test_the_class_the_script_sets_is_the_class_the_css_keys_on(self):
        css = read(CSS)
        self.assertIn("html." + js_const("HTML_CLASS"), css)

    def test_the_button_class_matches(self):
        self.assertIn("." + js_const("BUTTON_CLASS"), read(CSS))

    def test_it_hides_the_record_sidebar_and_the_training_rail_on_wide_screens_only(self):
        wide = media_block(read(CSS), "(min-width: 992px)")
        self.assertIn(".layout-side-section.right", wide)
        self.assertIn(".tn-host .layout-side-section", wide)
        self.assertRegex(wide, r"display:\s*none\s*!important")
        self.assertIn(js_const("HTML_CLASS"), wide)

    def test_a_phone_never_loses_its_sidebar_or_sees_the_button(self):
        """Hiding below 992px would strand a phone: the form's sidebar is frappe's responsive layout
        there and the Training rail is a stacked disclosure. The button is out of sight there."""
        css = read(CSS)
        # Every rule that hides the sidebars lives inside the min-width block.
        outside = re.sub(re.escape("@media (min-width: 992px)") + r"\s*\{", "@@WIDE@@{", css)
        for selector in (".layout-side-section.right", ".tn-host .layout-side-section"):
            for hit in re.finditer(re.escape("html." + js_const("HTML_CLASS")) + r"\s+" + re.escape(selector), outside):
                before = outside[: hit.start()]
                self.assertGreater(before.rfind("@@WIDE@@"), before.rfind("}\n}"), selector)
        narrow = media_block(css, "(max-width: 991.98px)")
        self.assertIn("." + js_const("BUTTON_CLASS"), narrow)
        self.assertRegex(narrow, r"display:\s*none\s*!important")

    def test_the_governed_selectors_exist_in_the_markup_they_name(self):
        governed = re.search(r'const GOVERNED\s*=\s*"([^"]+)"', read(MODULE)).group(1)
        self.assertEqual({s.strip() for s in governed.split(",")}, {".form-sidebar", ".tn-rail"})
        self.assertIn('"tn-rail"', read(DESK_NAV))


class TestNotDrivenOnRoute(unittest.TestCase):
    def test_the_left_sidebar_is_only_driven_from_the_storage_event(self):
        """frappe restores the left sidebar from its own key on every redraw. A second writer on
        page-change or app_ready would fight it (an empty workspace is forced open on purpose)."""
        code = strip_js_comments(read(MODULE))
        self.assertEqual(len(re.findall(r"sync_nav\(", code)), 2)  # the definition and one call
        self.assertIn("function on_storage", code)
        for token in ("app_ready", "frappe.router", "set_route"):
            self.assertNotIn(token, code)

    def test_it_never_clicks_the_page_head_toggle(self):
        code = strip_js_comments(read(MODULE))
        self.assertNotIn("sidebar-toggle-btn", code)
        self.assertNotIn("toggle_width", code)

    def test_it_keeps_frappes_own_key_and_a_separate_one_for_the_rest(self):
        self.assertEqual(js_const("NAV_KEY"), "desk-sidebar-collapsed")
        self.assertEqual(js_const("PAGE_KEY"), "ee-page-sidebar-hidden")


class TestCI(unittest.TestCase):
    def test_both_suites_run_in_ci(self):
        ci = read(CI)
        self.assertIn("erpnext_enhancements.tests.test_sidebar_memory", ci)
        self.assertIn("node scripts/test_sidebar_memory.js", ci)


if __name__ == "__main__":
    unittest.main()
