"""The activity feed shows on the first tab only (TASK-2026-00353).

Frappe renders the timeline into ``.form-footer``, a *sibling* of the tab panes
rather than a child of one, so it sits below whichever tab is open. The task asked
for one shared mechanism rather than per-doctype copies, and for the Details tab
itself to be left alone.

Static, like the other asset-contract suites here (``test_training_player_css_contract``
does the same job for the player): the JS and the CSS are two halves of one
behaviour, and the failure mode if they disagree is silent — a class nothing
styles, or a rule nothing sets.

Run: python -m unittest erpnext_enhancements.tests.test_activity_first_tab_only
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
SCRIPT = APP / "public/js/global_enhancements/activity_first_tab_only.js"
CSS = APP / "public/css/desk_enhancements.bundle.css"
BUNDLE = APP / "public/js/erpnext_enhancements.bundle.js"
HOOKS = APP / "hooks.py"
JS_DIR = APP / "public/js"

CLASS = "ee-activity-off-tab"


def script():
    return SCRIPT.read_text(encoding="utf-8")


class TestTheTwoHalvesAgree(unittest.TestCase):
    """A class nothing styles, or a rule nothing sets, both fail silently."""

    def test_the_script_sets_the_class(self):
        self.assertIn(f'"{CLASS}"', script())

    def test_the_stylesheet_hides_it(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertIn(f".{CLASS} .form-footer", css)
        self.assertRegex(css, rf"\.{re.escape(CLASS)} \.form-footer \{{\s*display: none")

    def test_the_hide_rule_actually_wins_the_cascade(self):
        """Asserting the rule EXISTS is not asserting it applies.

        v1.259.4 shipped this feature inert and the original of this test passed the
        whole time. `.form-footer, .timeline { display: block !important }` — the
        "Restore Missing Comment Box" fix earlier in the same stylesheet — beat the
        hide rule outright, because an important author declaration wins over a
        non-important one *no matter how specific the loser is*. The feature did
        nothing on any form, for six weeks, with a green test.

        So this resolves the cascade instead of grepping for a string: every rule in
        the file whose rightmost compound names `.form-footer` and which declares
        `display` is a competitor, and the winner is decided by importance first,
        then specificity, then source order — the real algorithm. The hide rule has
        to be that winner.
        """
        css = CSS.read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

        competitors = []
        for order, match in enumerate(re.finditer(r"([^{}]+)\{([^{}]*)\}", css)):
            selectors, body = match.group(1), match.group(2)
            display = re.search(r"(?<![\w-])display\s*:\s*([^;]+)", body)
            if not display:
                continue
            value = display.group(1).strip()
            important = "!important" in value
            for selector in selectors.split(","):
                selector = selector.strip()
                if not selector or ".form-footer" not in selector.split()[-1]:
                    continue
                specificity = (
                    selector.count("#"),
                    selector.count(".") + selector.count("["),
                    len(re.findall(r"(?:^|[\s>+~])[a-z]", selector)),
                )
                competitors.append((important, specificity, order, selector, value))

        self.assertTrue(competitors, "no `display` rule targets .form-footer at all")
        winner = max(competitors)
        self.assertIn(
            CLASS,
            winner[3],
            "the winning `display` declaration on .form-footer is "
            f"`{winner[3]} {{ display: {winner[4]} }}` — NOT the hide rule, so the "
            "activity feed is never hidden and this feature does nothing",
        )
        self.assertTrue(
            winner[4].startswith("none"),
            f"the hide rule wins but declares `display: {winner[4]}`",
        )

    def test_the_class_name_is_not_duplicated_elsewhere(self):
        """Two scripts toggling one class is a race, not a feature."""
        setters = [
            path.name
            for path in JS_DIR.rglob("*.js")
            if CLASS in path.read_text(encoding="utf-8", errors="ignore")
        ]
        self.assertEqual(setters, [SCRIPT.name], f"more than one script owns {CLASS}: {setters}")


class TestItIsOneMechanismNotN(unittest.TestCase):
    def test_it_ships_in_the_global_bundle(self):
        """Global assets ship as esbuild bundles, never raw /assets paths — raw
        ones are served immutable for a year with no content hash, so an edit
        never reaches a device that already cached it."""
        self.assertIn("global_enhancements/activity_first_tab_only.js", BUNDLE.read_text(encoding="utf-8"))

    def test_there_is_no_per_doctype_hook(self):
        """The task asked for a shared script rather than N copies."""
        self.assertNotIn("activity_first_tab_only", HOOKS.read_text(encoding="utf-8"))

    def test_it_registers_no_per_doctype_form_handlers(self):
        self.assertNotIn("frappe.ui.form.on", script())


class TestTheDetailsTabIsLeftAlone(unittest.TestCase):
    """"Must not break the timeline on the Details tab itself"."""

    def test_a_form_without_tabs_is_untouched(self):
        """The timeline is simply the bottom of that form; there is no other tab
        for it to be wrong on."""
        self.assertIn("links.length < 2", script())

    def test_the_first_tab_is_positional_not_by_name(self):
        """Several of our doctypes have already renamed Details to Overview or
        Summary, so a title check would silently stop matching."""
        body = script()
        self.assertIn("links[0]", body)
        self.assertNotIn('"Details"', body)

    def test_a_mid_render_form_defaults_to_showing_it(self):
        """Guessing "hidden" while the active tab is still being marked flashes
        the feed away and back on every paint."""
        self.assertIn("if (!active)", script())


class TestItSurvivesFrappeRerendering(unittest.TestCase):
    def test_it_hooks_the_tab_event(self):
        self.assertIn("shown.bs.tab", script())

    def test_it_defers_after_form_refresh(self):
        """`form-refresh` fires BEFORE refresh_fields, so the tabs may not exist
        yet — the same setTimeout(0) field_description_icons.js documents."""
        self.assertRegex(script(), r"form-refresh.*setTimeout\(apply, 0\)")

    def test_it_observes_the_rebuilt_footer(self):
        """Frappe rebuilds `.form-footer` after a save and when it constructs the
        tab list, neither of which fires an event we can hook."""
        body = script()
        self.assertIn("MutationObserver", body)
        self.assertIn(".form-footer", body)

    def test_the_observer_is_scoped(self):
        """Not a general-purpose DOM firehose: it only reacts to the two subtrees
        it cares about."""
        self.assertIn('closest(".form-tabs")', script())

    def test_the_work_is_debounced(self):
        self.assertIn("debounce", script())


if __name__ == "__main__":
    unittest.main()
