"""The learner palette answers "is it dark?" for two hosts that disagree about it.

The portal page carries no desk ``data-theme`` attribute and follows
``prefers-color-scheme``. The Desk always stamps ``data-theme="light"`` or
``"dark"`` on ``<html>`` — ``theme_switcher.js`` resolves its own "automatic" mode
through ``matchMedia`` before setting it, so the attribute is never absent there.

Honouring only the media query is therefore wrong in exactly one case, and it is a
common one: a learner whose OS is dark and whose desk theme is light got a dark
player inside a light desk. Nothing errors. It reads as the page being broken.

So the palette is switched three ways, and the two dark blocks are deliberate
duplicates — a media block and a plain rule cannot share one declaration list. The
failure that matters is one of them being edited and the other not, which is
invisible until somebody opens the page in the theme nobody tested. That is what
this module is for.

Run: python -m unittest erpnext_enhancements.tests.test_training_desk_theme
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
CSS = APP / "public" / "css" / "training" / "player.css"

LIGHT = ":root {"
DARK_MEDIA = ':root:not([data-theme="light"]) {'
DARK_ATTR = ':root[data-theme="dark"] {'
QUIZ_LIGHT = ":root .tr-quiz {"
QUIZ_DARK_MEDIA = ':root:not([data-theme="light"]) .tr-quiz {'
QUIZ_DARK_ATTR = ':root[data-theme="dark"] .tr-quiz {'

# Declared in the light palette and deliberately absent from the dark one, each
# because the light value is already correct on a dark ground. A name that drifts
# out of the dark block silently falls back to its light value, so this is an
# allow-list rather than a tolerance.
LIGHT_ONLY = {
    "--tr-cta-grad": (
        "white-safe on either ground, so it needs no dark variant -- stated in the "
        "stylesheet beside --tr-link"
    ),
    "--tr-radius": "a length, not a colour",
    "--tr-link": "declared in both; listed here only if that ever stops being true",
}


def css():
    return CSS.read_text(encoding="utf-8")


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def block_after(selector, text=None):
    """The declaration text of the first rule opened by ``selector``.

    These blocks contain no nested braces, so "up to the next closing brace" is
    exact. Comments are stripped first: several of them contain braces and colons,
    and more to the point this module asserts about declarations, not about prose.
    """
    source = strip_comments(css() if text is None else text)
    start = source.index(selector) + len(selector)
    return source[start : source.index("}", start)]


def declarations(selector, text=None):
    """``{name: value}`` for the custom properties a block declares."""
    body = block_after(selector, text)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))


class TestTheBlocksAreThere(unittest.TestCase):
    """Anti-vacuity: every comparison below reads two blocks out of one file, and a
    missing selector would make ``block_after`` raise rather than quietly return
    nothing — but the palette itself being empty would not."""

    def test_the_stylesheet_is_found(self):
        self.assertTrue(CSS.is_file(), f"no stylesheet at {CSS}")
        self.assertGreater(len(css()), 50_000)

    def test_the_light_palette_is_substantial(self):
        self.assertGreater(len(declarations(LIGHT)), 15)

    def test_every_selector_appears_exactly_once(self):
        source = strip_comments(css())
        for selector in (LIGHT, DARK_MEDIA, DARK_ATTR, QUIZ_LIGHT, QUIZ_DARK_MEDIA, QUIZ_DARK_ATTR):
            with self.subTest(selector):
                self.assertEqual(source.count(selector), 1, f"{selector} appears {source.count(selector)}x")


class TestTheTwoDarkBlocksAgree(unittest.TestCase):
    """The duplication is deliberate; the drift is the bug."""

    def test_the_root_palette_matches(self):
        self.assertEqual(
            declarations(DARK_MEDIA),
            declarations(DARK_ATTR),
            "the prefers-color-scheme dark block and the data-theme=dark block have "
            "drifted; a learner sees one of them and never the other",
        )

    def test_the_quiz_palette_matches(self):
        self.assertEqual(declarations(QUIZ_DARK_MEDIA), declarations(QUIZ_DARK_ATTR))


class TestDarkIsASubsetOfLight(unittest.TestCase):
    """A name declared dark and not light has no fallback; a name declared light and
    not dark silently keeps its light value in the dark theme."""

    def test_dark_declares_nothing_light_does_not(self):
        extra = sorted(set(declarations(DARK_ATTR)) - set(declarations(LIGHT)))
        self.assertEqual(extra, [], f"{extra} exist only in the dark palette")

    def test_every_light_name_is_either_overridden_or_excused(self):
        missing = sorted(
            name
            for name in declarations(LIGHT)
            if name not in declarations(DARK_ATTR) and name not in LIGHT_ONLY
        )
        self.assertEqual(
            missing,
            [],
            f"{missing} keep their light value in dark mode. Override them, or add "
            "them to LIGHT_ONLY with the reason they are already correct.",
        )

    def test_every_excuse_says_something(self):
        for name, reason in LIGHT_ONLY.items():
            self.assertGreater(len(reason.strip()), 20, f"{name}'s reason is a placeholder")


class TestTheMediaQueryIsGuarded(unittest.TestCase):
    """The whole point. An unguarded ``:root`` inside the dark media query is the
    bug this release fixed: it wins in a light desk on a dark OS."""

    def test_no_unguarded_root_in_a_dark_media_block(self):
        source = strip_comments(css())
        for match in re.finditer(r"@media \(prefers-color-scheme: dark\) \{(.*?)\n\}", source, re.S):
            body = match.group(1)
            with self.subTest(body.strip()[:60]):
                self.assertNotRegex(
                    body,
                    r"\n\t:root\s*\{",
                    "an unguarded :root inside the dark media query overrides a light "
                    'desk; guard it with :not([data-theme="light"])',
                )

    def test_the_attribute_selector_exists_for_every_media_override(self):
        source = strip_comments(css())
        media = len(re.findall(r':root:not\(\[data-theme="light"\]\)', source))
        attr = len(re.findall(r':root\[data-theme="dark"\]', source))
        self.assertEqual(media, attr, "every guarded media override needs a data-theme=dark twin")


class TestThePaletteHasOneHome(unittest.TestCase):
    """``--tr-*`` is declared in player.css and, for its injected fallback sheet
    only, in quiz.js. Nowhere else.

    This is what lets the authoring canvas inherit a palette fix for free, and it
    is the property the Desk host is most likely to break: a page stylesheet
    declaring ``--tr-surface`` to "match the desk" would make the authoring surface
    stop matching what the learner sees, which is the one thing the canvas exists
    to guarantee. Hence the ``tl-`` prefix for desk chrome.
    """

    ALLOWED = {
        "public/css/training/player.css",
        "public/js/training/quiz.js",
    }

    def test_only_two_files_declare_the_palette(self):
        declaring = set()
        for path in list(APP.rglob("*.css")) + list(APP.rglob("*.js")) + list(APP.rglob("*.html")):
            if "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if re.search(r"--tr-[\w-]+\s*:", text):
                declaring.add(path.relative_to(APP).as_posix())
        self.assertEqual(
            sorted(declaring),
            sorted(self.ALLOWED),
            "--tr-* must be declared in player.css (and quiz.js's injected fallback) "
            "and read everywhere else",
        )


if __name__ == "__main__":
    unittest.main()
