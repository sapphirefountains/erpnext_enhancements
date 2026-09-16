"""Desk menus are not trapped inside a stacking context of our own making.

The glass theme blurs `.page-head` and `.btn`, and `backdrop-filter` creates a
STACKING CONTEXT on whatever carries it -- unconditionally, whatever that
element's `position` or `z-index` is. Frappe opens the page "..."/Actions menus
*inside* `.page-head` (page.html) and the list view's sort menu *inside* its own
`<button>` (sort_selector.html), so both menus were sealed into a context that
the page body then painted over. On a query report the page head is
`position: unset` (report.scss), which makes its `z-index: 6` inert, so the menu
landed under the filter row and the datatable: the General Ledger bug.

Static, like the other asset-contract suites here, and for the same reason
`test_activity_first_tab_only` gives: **asserting that a rule EXISTS is not
asserting that it APPLIES.** Every rule this file guards is an override of an
earlier rule in the same stylesheet, three of them `!important`, and an override
that loses the cascade fails silently -- the menu is simply behind the page
again, exactly as it was. So the cascade is resolved here rather than grepped,
and the deleted band-aid is asserted *absent*: re-adding `position: relative`
with a z-index to `.sort-selector` puts the trap straight back.

The behavioural check that found this is `scripts/probe_menu_stacking.mjs`:
Frappe v16's real page markup and stylesheet values rendered in Chromium, reading
`document.elementFromPoint` over the open menu in every button state. It reports
5 failures against main (`--ref origin/main`) and none against this change, and
it cannot be a CI job because the runners have no browser engine. What is left
here is the part of that contract which survives without one.

Run: python -m unittest erpnext_enhancements.tests.test_dropdown_stacking
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CSS = REPO_ROOT / "erpnext_enhancements/public/css/desk_enhancements.bundle.css"


def stylesheet():
    return CSS.read_text(encoding="utf-8")


def rules(css):
    """Every (selector, declarations, source_order) in the file, comments stripped.

    Good enough for this file: it holds no nested at-rules whose braces would
    confuse a flat scan except plain `@media` blocks, whose inner rules this
    still yields individually.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for order, match in enumerate(re.finditer(r"([^{}]+)\{([^{}]*)\}", css)):
        selectors, body = match.group(1).strip(), match.group(2)
        if selectors.startswith("@"):
            continue
        for selector in selectors.split(","):
            selector = selector.strip()
            if selector:
                yield selector, body, order


def specificity(selector):
    return (
        selector.count("#"),
        selector.count(".") + selector.count("[") + len(re.findall(r":(?!:)(?!has\b)\w", selector)),
        len(re.findall(r"(?:^|[\s>+~])[a-z]", selector)),
    )


def winner(css, prop, matches):
    """Resolve `prop` the way the cascade does, over the rules `matches` accepts.

    Importance first, then specificity, then source order -- the real algorithm,
    which is the only way to catch an override that is present and losing.
    """
    competitors = []
    for selector, body, order in rules(css):
        if not matches(selector):
            continue
        declaration = re.search(rf"(?<![\w-]){re.escape(prop)}\s*:\s*([^;]+)", body)
        if not declaration:
            continue
        value = declaration.group(1).strip()
        competitors.append(("!important" in value, specificity(selector), order, selector, value))
    return max(competitors) if competitors else None


class TestTheQueryReportPageHeadCarriesItsMenu(unittest.TestCase):
    """report.scss makes `.page-head` static, so `z-index: 6` stops applying."""

    SELECTOR = "#page-query-report .page-head"

    def test_the_rule_is_there(self):
        self.assertIn(self.SELECTOR, stylesheet())

    def test_position_is_restored_and_beats_frappes_position_unset(self):
        """`position: unset` from frappe's report.scss:2-5 ties this on
        specificity (both are one id + one class), so this wins only on source
        order -- app CSS loads after frappe's desk bundle. Nothing in THIS file
        may therefore declare `position` on `.page-head` after it."""
        win = winner(stylesheet(), "position", lambda s: s.endswith(".page-head"))
        self.assertIsNotNone(win, "nothing in this stylesheet positions .page-head")
        self.assertEqual(win[3], self.SELECTOR, f"`position` on .page-head is won by `{win[3]}`")
        self.assertTrue(
            win[4].startswith("relative"),
            f"expected `position: relative`, found `{win[4]}` -- `sticky` would give the "
            "report back a sticky toolbar frappe removed on purpose",
        )

    def test_it_declares_a_level_that_clears_the_report_body(self):
        """Inheriting frappe's `z-index: 6` is not enough: a query report body
        holds `.dt-dropdown__list` at 10. 1000 is bootstrap's $zindex-dropdown."""
        win = winner(stylesheet(), "z-index", lambda s: s.endswith(".page-head"))
        self.assertIsNotNone(win, "no z-index on .page-head in this stylesheet")
        self.assertEqual(win[3], self.SELECTOR)
        self.assertGreaterEqual(int(win[4].replace("!important", "").strip()), 11)

    def test_it_stays_under_the_navbar_and_everything_above_it(self):
        """Above the page body, below the chrome: the navbar and
        `.filter-popover` (1019), the desk sidebar (1020-1023), `.frappe-menu`
        (1030), the modal backdrop (1040), `#freeze`/`#alert-container` (2000)."""
        win = winner(stylesheet(), "z-index", lambda s: s == self.SELECTOR)
        self.assertIsNotNone(win, f"`{self.SELECTOR}` declares no z-index")
        self.assertLess(int(win[4].replace("!important", "").strip()), 1019)


class TestAButtonHoldingAMenuIsNotAStackingContext(unittest.TestCase):
    """frappe's sort_selector.html nests the <ul> INSIDE the <button>."""

    BASE = ".btn:has(.dropdown-menu)"

    def matches(self, selector):
        return selector.startswith(".btn") and ":has(.dropdown-menu)" in selector

    def test_the_rule_is_there(self):
        self.assertIn(self.BASE, stylesheet())

    def test_it_kills_the_blur_and_outranks_the_important_one_on_btn_primary(self):
        """`.btn-primary` declares `backdrop-filter: ... !important`, so an
        override without `!important` loses to it however specific it is."""
        css = stylesheet()
        for prop in ("backdrop-filter", "-webkit-backdrop-filter"):
            win = winner(css, prop, lambda s: self.matches(s) or s in (".btn", ".btn-primary", ".btn-danger"))
            self.assertIsNotNone(win, f"nothing declares {prop} on a button")
            self.assertTrue(self.matches(win[3]), f"`{prop}` on a menu-holding button is won by `{win[3]}`")
            self.assertTrue(win[4].startswith("none"), f"`{prop}` resolves to `{win[4]}`")
            self.assertTrue(win[0], f"`{prop}: none` needs !important to beat .btn-primary's important one")

    def test_it_kills_the_hover_lift_that_fires_while_you_click(self):
        """`.btn-default:hover { transform: translateY(-1px) }` is a stacking
        context for the duration of the hover -- i.e. exactly while the menu is
        open under a mouse."""
        win = winner(
            stylesheet(),
            "transform",
            lambda s: self.matches(s) or s in (".btn-default:hover", ".btn-secondary:hover", ".btn-primary:hover"),
        )
        self.assertIsNotNone(win)
        self.assertTrue(self.matches(win[3]), f"`transform` on a menu-holding button is won by `{win[3]}`")
        self.assertTrue(win[4].startswith("none"), f"`transform` resolves to `{win[4]}`")

    def test_it_kills_bootstraps_focus_z_index(self):
        """`.btn-group > .btn:focus { z-index: 1 }` is bootstrap's, and it fires
        on the very click that opens the menu.

        Completing the invariant rather than fixing a live break. Bootstrap
        declares `z-index: 1` on `.btn-group > .btn` for FOUR states -- `:hover`,
        `:focus`, `:active`, `.active` (_button-group.scss:14-23) -- and frappe
        answers most of them in two separate places: `common/buttons.scss:106-115`
        for `:hover`/`:active` on every `.btn-default`, and list.scss:547-554 for
        `:focus`, but only inside `.page-form`. The declaration stays, and this
        pins it, so nobody trims it as redundant without first reading BOTH of
        those upstream scopes.
        """
        css = stylesheet()
        self.assertIn(f"{self.BASE}:focus", css)
        self.assertIn(f"{self.BASE}:active", css)
        win = winner(css, "z-index", self.matches)
        self.assertIsNotNone(win, "the menu-holding-button rule declares no z-index")
        self.assertTrue(win[4].startswith("auto"), f"`z-index` resolves to `{win[4]}`, expected auto")


class TestTheOldBandAidIsGoneAndStaysGone(unittest.TestCase):
    """It was the second cause of the symptom it was filed as fixing.

    `position: relative` plus a z-index made `.sort-selector` and
    `.filter-section` two more stacking contexts pinned at 2 -- a tie with
    frappe's sticky first list row, lost on tree order -- so the
    `z-index: 1005` it paired them with was clamped to 2 and could never apply.
    """

    def test_no_rule_positions_the_sort_selector_or_filter_section(self):
        css = stylesheet()
        for target in (".sort-selector", ".filter-section", ".list-filter-main"):
            for prop in ("position", "z-index"):
                win = winner(css, prop, lambda s, t=target: s == t or s.startswith(f"{t} ") or s.endswith(f" {t}"))
                self.assertIsNone(
                    win,
                    f"`{prop}` is declared on `{target}` by `{win[3] if win else ''}` -- that is the "
                    "stacking context the sort menu was trapped in",
                )

    def test_the_selectors_that_never_existed_are_not_reintroduced(self):
        """`.list-filter-main` and `.list-sort-dropdown` appear nowhere in
        Frappe v16; a rule naming them reads as coverage that is not there.

        Comments are stripped first -- the banner above the fix names both, as
        the record of what was removed and why, and that prose is the point.
        """
        css = re.sub(r"/\*.*?\*/", "", stylesheet(), flags=re.S)
        for dead in (".list-filter-main", ".list-sort-dropdown"):
            self.assertNotIn(dead, css, f"{dead} does not exist in Frappe v16")


if __name__ == "__main__":
    unittest.main()
