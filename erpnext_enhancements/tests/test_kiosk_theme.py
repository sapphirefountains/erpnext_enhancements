"""The kiosk palette is switched three ways, and the two dark blocks must agree.

``public/css/kiosk/kiosk.css`` declares its tokens once in ``:root`` (light), then
again inside ``@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }``
(system dark, unless the user forced light) and once more in
``:root[data-theme="dark"]`` (the user forced dark). ``<html data-theme>`` ABSENT
means "follow the system"; the inline script in ``www/kiosk.html`` sets or removes
it from ``localStorage.tk_theme`` before the stylesheet is parsed, so there is no
flash of the wrong colour.

A media block and a plain rule cannot share a declaration list, so the two dark
blocks are deliberate duplicates. The failure that matters is one being edited and
the other not — invisible until somebody opens the app in the mode nobody tested.
Same mechanism, same test shape as ``tests/test_training_desk_theme.py``.

Also pinned here: the theme script precedes the stylesheet in ``kiosk.html`` (or
the first paint flashes), ``body`` has an explicit background, and the manifest's
``theme_color`` / ``background_color`` are the light ``--tk-bg`` — the value the
page's own theme-color meta starts from.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_theme
"""

import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
CSS = APP / "public" / "css" / "kiosk" / "kiosk.css"
HTML = APP / "www" / "kiosk.html"
MANIFEST = APP / "www" / "kiosk-manifest.json"

LIGHT = ":root {"
DARK_MEDIA = ':root:not([data-theme="light"]) {'
DARK_ATTR = ':root[data-theme="dark"] {'

# Declared in the light palette and deliberately absent from the dark one, each
# because the value is not a colour. A name that drifts out of the dark block
# silently falls back to its light value, so this is an allow-list, not a tolerance.
LIGHT_ONLY = {
    "--tk-radius": "a length, not a colour; the same in both themes",
    "--tk-radius-sm": "a length, not a colour; the same in both themes",
    "--tk-font": "the system font stack; a theme does not change the typeface",
    "--tk-mono": "the monospace stack for the timers; a theme does not change it",
    "--tk-motion": "a duration; motion is governed by prefers-reduced-motion, not by theme",
    "--tk-ease": "a timing function, not a colour",
}


def css():
    return CSS.read_text(encoding="utf-8")


def strip_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def block_after(selector, text=None):
    """The declaration text of the first rule opened by ``selector``.

    The palette blocks contain no nested braces, so "up to the next closing brace"
    is exact. Comments are stripped first: they contain braces and colons, and this
    module asserts about declarations, not prose.
    """
    source = strip_comments(css() if text is None else text)
    start = source.index(selector) + len(selector)
    return source[start : source.index("}", start)]


def declarations(selector, text=None):
    """``{name: value}`` for the custom properties a block declares."""
    body = block_after(selector, text)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))


class TestTheBlocksAreThere(unittest.TestCase):
    """Anti-vacuity: every comparison below reads blocks out of one file, and a
    missing selector makes ``block_after`` raise — but an empty palette would not."""

    def test_the_stylesheet_is_found(self):
        self.assertTrue(CSS.is_file(), f"no stylesheet at {CSS}")
        self.assertGreater(len(css()), 10_000)

    def test_the_light_palette_is_substantial(self):
        self.assertGreater(len(declarations(LIGHT)), 25)

    def test_every_selector_appears_exactly_once(self):
        source = strip_comments(css())
        for selector in (LIGHT, DARK_MEDIA, DARK_ATTR):
            with self.subTest(selector):
                self.assertEqual(source.count(selector), 1, f"{selector} appears {source.count(selector)}x")

    def test_the_light_block_comes_first(self):
        source = strip_comments(css())
        self.assertLess(source.index(LIGHT), source.index(DARK_MEDIA))
        self.assertLess(source.index(DARK_MEDIA), source.index(DARK_ATTR))


class TestTheTwoDarkBlocksAgree(unittest.TestCase):
    """The duplication is deliberate; the drift is the bug."""

    def test_the_dark_palettes_are_identical(self):
        self.assertEqual(
            declarations(DARK_MEDIA),
            declarations(DARK_ATTR),
            "the prefers-color-scheme dark block and the data-theme=dark block have "
            "drifted; a technician sees one of them and never the other",
        )

    def test_the_dark_palette_is_substantial(self):
        self.assertGreater(len(declarations(DARK_ATTR)), 25)


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

    def test_every_excuse_is_for_a_real_token(self):
        stale = sorted(name for name in LIGHT_ONLY if name not in declarations(LIGHT))
        self.assertEqual(stale, [], f"{stale} are excused but no longer declared")

    def test_every_excuse_says_something(self):
        for name, reason in LIGHT_ONLY.items():
            self.assertGreater(len(reason.strip()), 20, f"{name}'s reason is a placeholder")


class TestTheMediaQueryIsGuarded(unittest.TestCase):
    """An unguarded ``:root`` inside the dark media query would override a user who
    chose Light on a dark phone — the very case the three-way switch exists for."""

    def test_no_unguarded_root_in_a_dark_media_block(self):
        source = strip_comments(css())
        blocks = re.findall(r"@media \(prefers-color-scheme: dark\) \{(.*?)\n\}", source, re.S)
        self.assertTrue(blocks, "no dark media block found")
        for body in blocks:
            with self.subTest(body.strip()[:60]):
                self.assertNotRegex(
                    body,
                    r"\n\s*:root\s*\{",
                    "an unguarded :root inside the dark media query overrides a forced "
                    'light theme; guard it with :not([data-theme="light"])',
                )

    def test_the_attribute_selector_exists_for_every_media_override(self):
        source = strip_comments(css())
        media = len(re.findall(r':root:not\(\[data-theme="light"\]\)', source))
        attr = len(re.findall(r':root\[data-theme="dark"\]', source))
        self.assertEqual(media, attr, "every guarded media override needs a data-theme=dark twin")

    def test_the_stylesheet_never_reads_the_old_bare_attribute_selector(self):
        """``[data-theme="dark"] .x`` rules were the pre-overhaul pattern: a second
        declaration site for dark values that the palette test could not see."""
        source = strip_comments(css())
        self.assertNotRegex(source, r'\n\[data-theme="dark"\]', "dark values belong in the palette blocks")


class TestTheBodyAndChromeAreExplicit(unittest.TestCase):
    def test_body_has_an_explicit_background(self):
        body = block_after("\nbody {")
        self.assertRegex(body, r"background\s*:\s*var\(--tk-bg\)")

    def test_the_theme_color_meta_starts_from_the_light_ground(self):
        html = HTML.read_text(encoding="utf-8")
        meta = re.search(r'<meta name="theme-color" content="([^"]+)">', html)
        self.assertIsNotNone(meta, "kiosk.html has no theme-color meta")
        self.assertEqual(meta.group(1), declarations(LIGHT)["--tk-bg"])

    def test_the_manifest_colours_match_the_light_palette(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        light_bg = declarations(LIGHT)["--tk-bg"]
        self.assertEqual(manifest.get("theme_color"), light_bg)
        self.assertEqual(manifest.get("background_color"), light_bg)


class TestTheInlineScriptRunsFirst(unittest.TestCase):
    """The stored theme must be on <html> before the stylesheet is parsed, or the
    first frame paints in the wrong palette and then snaps."""

    def html(self):
        # Jinja comments stripped: the template's header prose mentions the
        # stylesheet and the script in whatever order reads well, and a position
        # check against the raw text would measure the prose rather than the tags.
        return re.sub(r"\{#.*?#\}", "", HTML.read_text(encoding="utf-8"), flags=re.S)

    def test_the_theme_script_precedes_the_stylesheet(self):
        html = self.html()
        script = html.index("localStorage.getItem('tk_theme')")
        sheet = html.index("css/kiosk/kiosk.css")
        self.assertLess(script, sheet, "the theme script must come before the kiosk stylesheet")

    def test_the_theme_script_is_inline_and_inside_head_include(self):
        html = self.html()
        head = html[html.index("{% block head_include %}") : html.index("{% endblock %}", html.index("{% block head_include %}"))]
        self.assertIn("localStorage.getItem('tk_theme')", head)
        self.assertIn("<script>", head)

    def test_the_theme_script_is_guarded(self):
        html = self.html()
        start = html.index("localStorage.getItem('tk_theme')")
        self.assertIn("try", html[max(0, start - 200) : start])
        self.assertIn("catch", html[start : start + 400])

    def test_absent_attribute_means_system(self):
        html = self.html()
        self.assertIn("removeAttribute('data-theme')", html)
        self.assertRegex(html, r"setAttribute\('data-theme', t\)")


if __name__ == "__main__":
    unittest.main()
