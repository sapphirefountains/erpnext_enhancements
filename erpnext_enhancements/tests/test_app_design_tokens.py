"""Bench-free guards for the Sapphire app design tokens (`public/css/sf_tokens.bundle.css`).

Form kit P1 (TASK-2026-02742; P1.1 02744, P1.2 02745). The file translates the Sapphire
Fountains Design System from the website to the Desk, and it loads on every Desk page
(hooks.py `app_include_css`), so two failures matter more than any other:

* **it must style nothing.** It is custom properties and @font-face rules. A selector that
  reached an element would restyle the whole Desk the day it deployed;
* **a colour pairing must not quietly fail contrast in one theme.** A value that reads in
  light can vanish in dark (deep-sea-blue rules on a navy card are 1.06:1), and nobody
  checks the dark theme by eye on every change.

What it pins:

* every hex in the file is a design-system token, a 2016-guide status colour, or one of
  the two app additions listed here with a reason;
* the palette, the pillar stops and the gradients equal `print_style`, the one record
  print, email and the project brief already share, so the app cannot drift from paper;
* every text pairing the guide names clears 4.5:1, and every boundary 3:1, in light and
  dark;
* the device-dark @media block is identical to the Desk-dark block;
* `print_style.STREAM_PILLAR` names every prod value stream and only real pillars.

Run: python -m unittest erpnext_enhancements.tests.test_app_design_tokens
"""

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from erpnext_enhancements import print_style as ps  # frappe-free by design

APP = REPO_ROOT / "erpnext_enhancements"
CSS_PATH = APP / "public" / "css" / "sf_tokens.bundle.css"
CSS_RAW = CSS_PATH.read_text(encoding="utf-8")
CSS = re.sub(r"/\*.*?\*/", "", CSS_RAW, flags=re.S)
HOOKS = (APP / "hooks.py").read_text(encoding="utf-8")
GUIDE = REPO_ROOT / "docs" / "app-design-system.md"

# Every colour token of the Sapphire Fountains Design System (claude.ai artifact
# QSMwzGdAyPVaZ1i3BRe1XS, tokens.json, read 2026-10-13), by value.
DESIGN_SYSTEM = {
    "#00263e",  # deep-sea-blue
    "#00609c",  # bahama-blue
    "#00a0df",  # fresh-blue
    "#b14fc5",  # violet
    "#62cbc9",  # teal
    "#00111c",  # navy-900
    "#101820",  # navy-850
    "#002136",  # navy-800
    "#005779",  # fresh-blue-deep
    "#55265f",  # violet-deep
    "#316564",  # teal-deep
    "#99bfd7",  # sky-200
    "#f8f8f8",  # off-white
    "#ffffff",  # white
    "#151515",  # ink-900
    "#363636",  # ink-700
    "#393939",  # ink-650
    "#dadbdd",  # border-100
}
# The 2016 brand guide's supportive set: the system says to take a status colour from
# here rather than invent one. print_style uses the same two.
GUIDE_STATUS = {"#bd2e2b", "#ffb819"}
# Values the system has no token for. Adding one here needs the reason in the CSS
# comment beside it and in docs/app-design-system.md.
APP_ADDITIONS = {
    "#5c6066",  # ink-500: muted text; the system's only greys are body inks
    "#80858b",  # border-300: a field's edge at 3:1; border-100 is 1.4:1 on white
}

PROD_VALUE_STREAMS = {"Design", "Build", "Products", "Service", "Events", "Delivery"}


def _luminance(hex_value):
    channels = []
    for i in (1, 3, 5):
        c = int(hex_value[i : i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    lighter, darker = max(_luminance(fg), _luminance(bg)), min(_luminance(fg), _luminance(bg))
    return (lighter + 0.05) / (darker + 0.05)


def _block(selector):
    """The declarations of the first rule whose selector is exactly `selector`."""
    match = re.search(r"(?:^|[{}\s])" + re.escape(selector) + r"\s*\{([^{}]*)\}", CSS)
    if not match:
        raise AssertionError(f"no {selector} block in sf_tokens.bundle.css")
    return dict(re.findall(r"(--sf-[\w-]+)\s*:\s*([^;]+);", match.group(1)))


BASE = _block(":root")
DESK_DARK = _block(':root[data-theme="dark"]')
DEVICE_DARK = _block(":root:not([data-theme])")


def resolve(name, theme="light"):
    """A token's final value in a theme, following var() references."""
    seen = set()
    while True:
        if name in seen:
            raise AssertionError(f"var() cycle at {name}")
        seen.add(name)
        value = (DESK_DARK.get(name) if theme == "dark" else None) or BASE.get(name)
        if value is None:
            raise AssertionError(f"{name} is not defined")
        value = value.strip()
        ref = re.fullmatch(r"var\((--sf-[\w-]+)\)", value)
        if not ref:
            return value
        name = ref.group(1)


class TestItStylesNothing(unittest.TestCase):
    def test_only_token_blocks_and_the_font_face(self):
        selectors = {s.strip() for s in re.findall(r"([^{};]+)\{", CSS)}
        self.assertEqual(
            selectors,
            {
                "@font-face",
                ":root",
                ':root[data-theme="dark"]',
                "@media (prefers-color-scheme: dark)",
                ":root:not([data-theme])",
            },
        )

    def test_every_property_outside_the_font_face_is_an_sf_variable(self):
        outside = re.sub(r"@font-face\s*\{[^}]*\}", "", CSS)
        for prop in re.findall(r"([\w-]+)\s*:\s*[^;{}]+;", outside):
            with self.subTest(prop):
                self.assertTrue(prop.startswith("--sf-"), f"{prop} would style the Desk")

    def test_it_loads_on_every_desk_page(self):
        match = re.search(r"app_include_css\s*=\s*\[(.*?)\]", HOOKS, flags=re.S)
        self.assertIsNotNone(match)
        self.assertIn('"sf_tokens.bundle.css"', match.group(1))


class TestPalette(unittest.TestCase):
    def test_every_hex_is_accounted_for(self):
        allowed = DESIGN_SYSTEM | GUIDE_STATUS | APP_ADDITIONS
        for value in re.findall(r"#[0-9a-fA-F]{3,8}\b", CSS):
            with self.subTest(value):
                self.assertEqual(value, value.lower(), "write hex in lower case")
                self.assertIn(value, allowed, "a colour the design system doesn't have")

    def test_the_palette_equals_print_style(self):
        for token, constant in (
            ("--sf-deep-sea-blue", "DEEP_SEA_BLUE"),
            ("--sf-bahama-blue", "BAHAMA_BLUE"),
            ("--sf-fresh-blue", "FRESH_BLUE"),
            ("--sf-fresh-blue-deep", "FRESH_BLUE_DEEP"),
            ("--sf-violet", "VIOLET"),
            ("--sf-violet-deep", "VIOLET_DEEP"),
            ("--sf-teal", "TEAL"),
            ("--sf-teal-deep", "TEAL_DEEP"),
            ("--sf-navy-900", "NAVY_900"),
            ("--sf-navy-800", "NAVY_800"),
            ("--sf-off-white", "OFF_WHITE"),
            ("--sf-ink-900", "INK_900"),
            ("--sf-ink-700", "INK_700"),
            ("--sf-border-100", "BORDER_100"),
            ("--sf-red", "RED"),
            ("--sf-yellow", "YELLOW"),
        ):
            with self.subTest(token):
                self.assertEqual(resolve(token), getattr(ps, constant))

    def test_the_app_additions_are_documented(self):
        guide = GUIDE.read_text(encoding="utf-8")
        for value in APP_ADDITIONS:
            with self.subTest(value):
                self.assertIn(value, CSS_RAW)
                self.assertIn(value, guide, "an app addition the guide doesn't explain")


class TestPillars(unittest.TestCase):
    def test_the_stops_equal_print_style(self):
        for key, record in ps.PILLARS.items():
            with self.subTest(key):
                self.assertEqual(resolve(f"--sf-pillar-{key}"), record["open"])
                self.assertEqual(resolve(f"--sf-pillar-{key}-deep"), record["deep"])
        self.assertEqual(resolve("--sf-pillar-neutral"), ps.NEUTRAL["open"])
        self.assertEqual(resolve("--sf-pillar-neutral-deep"), ps.NEUTRAL["deep"])

    def test_each_gradient_opens_on_the_opening_stop(self):
        for key, record in ps.PILLARS.items():
            with self.subTest(key):
                self.assertEqual(
                    resolve(f"--sf-gradient-{key}"),
                    f"linear-gradient(90deg, {record['open']} 0%, {record['deep']} 100%)",
                )
        self.assertEqual(
            resolve("--sf-gradient-neutral"),
            f"linear-gradient(90deg, {ps.DEEP_SEA_BLUE} 0%, {ps.NAVY_900} 100%)",
        )

    def test_every_prod_value_stream_has_a_pillar_or_is_neutral(self):
        self.assertEqual(set(ps.STREAM_PILLAR), PROD_VALUE_STREAMS)
        for stream, key in ps.STREAM_PILLAR.items():
            with self.subTest(stream):
                self.assertTrue(key is None or key in ps.PILLARS, f"{stream} -> {key}")


class TestThemes(unittest.TestCase):
    def test_device_dark_matches_desk_dark(self):
        self.assertEqual(DEVICE_DARK, DESK_DARK)

    def test_dark_only_overrides_semantic_tokens(self):
        """The palette and pillars are the brand, and the brand has one palette. Only what a
        token is *used for* changes with the theme."""
        for name in DESK_DARK:
            with self.subTest(name):
                self.assertIn(name, BASE, "dark defines a token light doesn't")
                self.assertNotRegex(
                    name,
                    r"^--sf-(pillar|gradient|space|radius|display|font|weight|leading|text-(xs|sm|md|lg|input)$)",
                )

    def test_fields_stay_white_in_both_themes(self):
        for theme in ("light", "dark"):
            self.assertEqual(resolve("--sf-field-bg", theme), "#ffffff")


TEXT_PAIRS = (
    # (foreground, background) — text, 4.5:1
    ("--sf-text", "--sf-bg"),
    ("--sf-text", "--sf-surface"),
    ("--sf-text-strong", "--sf-bg"),
    ("--sf-text-strong", "--sf-surface"),
    ("--sf-text-muted", "--sf-bg"),
    ("--sf-text-muted", "--sf-surface"),
    ("--sf-heading", "--sf-bg"),
    ("--sf-heading", "--sf-surface"),
    ("--sf-eyebrow", "--sf-surface"),
    ("--sf-link", "--sf-bg"),
    ("--sf-link", "--sf-surface"),
    ("--sf-label", "--sf-surface"),
    ("--sf-hint", "--sf-surface"),
    ("--sf-field-text", "--sf-field-bg"),
    ("--sf-on-primary", "--sf-primary"),
    ("--sf-on-band", "--sf-band-top"),
    ("--sf-on-band", "--sf-band-bottom"),
    ("--sf-on-band-muted", "--sf-band-top"),
    ("--sf-on-band-muted", "--sf-band-bottom"),
    ("--sf-on-danger", "--sf-danger"),
    ("--sf-on-warn", "--sf-warn"),
    ("--sf-on-pillar", "--sf-pillar-design-deep"),
    ("--sf-on-pillar", "--sf-pillar-build-deep"),
    ("--sf-on-pillar", "--sf-pillar-service-deep"),
    ("--sf-on-pillar", "--sf-pillar-rent-deep"),
    ("--sf-on-pillar", "--sf-pillar-neutral-deep"),
)
BOUNDARY_PAIRS = (
    # a boundary that carries meaning, or a focus ring — 3:1
    ("--sf-field-border", "--sf-field-bg"),
    ("--sf-focus", "--sf-surface"),
    ("--sf-focus", "--sf-bg"),
)


class TestContrast(unittest.TestCase):
    def test_text_clears_aa_in_both_themes(self):
        for theme in ("light", "dark"):
            for fg, bg in TEXT_PAIRS:
                a, b = resolve(fg, theme), resolve(bg, theme)
                with self.subTest(theme=theme, pair=f"{fg} on {bg}"):
                    self.assertGreaterEqual(contrast(a, b), 4.5, f"{a} on {b} is {contrast(a, b):.2f}:1")

    def test_boundaries_clear_3_to_1_in_both_themes(self):
        for theme in ("light", "dark"):
            for fg, bg in BOUNDARY_PAIRS:
                a, b = resolve(fg, theme), resolve(bg, theme)
                with self.subTest(theme=theme, pair=f"{fg} against {bg}"):
                    self.assertGreaterEqual(contrast(a, b), 3.0, f"{a} on {b} is {contrast(a, b):.2f}:1")

    def test_a_white_field_on_a_dark_card_is_its_own_boundary(self):
        self.assertGreaterEqual(contrast(resolve("--sf-field-bg", "dark"), resolve("--sf-surface", "dark")), 3.0)

    def test_the_pairings_the_system_flags_are_not_used(self):
        """off-white on fresh-blue (the website's primary button) is 2.8:1 and off-white on
        teal 1.8:1; the tokens must not reintroduce either."""
        self.assertLess(contrast(ps.OFF_WHITE, ps.FRESH_BLUE), 4.5, "the ban would be stale")
        for theme in ("light", "dark"):
            self.assertNotEqual(resolve("--sf-on-primary", theme), ps.OFF_WHITE)


class TestTypeAndShape(unittest.TestCase):
    def test_the_display_face_is_the_repo_file(self):
        face = re.search(r"@font-face\s*\{([^}]*)\}", CSS).group(1)
        self.assertIn('font-family: "Big Noodle Titling"', face)
        self.assertIn('url("/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2")', face)
        self.assertIn("font-display: swap", face)
        self.assertTrue(Path(ps.FONT_PATH).is_file())

    def test_lato_regular_and_bold_ship_with_their_licence(self):
        """The brand's body face, vendored 2026-10-13 from Google Fonts (Lato v25, latin
        subset). A face declared with a missing file fails silently: every screen just
        falls back to the system sans and nobody notices."""
        fonts = APP / "public" / "fonts"
        faces = re.findall(r"@font-face\s*\{([^}]*)\}", CSS)
        lato = {
            re.search(r"font-weight:\s*(\d+)", face).group(1): face
            for face in faces
            if 'font-family: "Lato"' in face
        }
        self.assertEqual(set(lato), {"400", "700"})
        for weight, filename in (("400", "lato_regular.woff2"), ("700", "lato_bold.woff2")):
            with self.subTest(weight):
                self.assertIn(f'url("/assets/erpnext_enhancements/fonts/{filename}")', lato[weight])
                self.assertIn("font-display: swap", lato[weight])
                path = fonts / filename
                self.assertTrue(path.is_file(), path)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(4), b"wOF2", f"{filename} is not a woff2")
        licence = (fonts / "LATO_OFL.txt").read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE", licence)
        self.assertTrue(resolve("--sf-font-sans").startswith("Lato,"))

    def test_inputs_are_never_small_enough_to_make_ios_zoom(self):
        self.assertGreaterEqual(int(resolve("--sf-text-input").rstrip("px")), 16)

    def test_touch_targets_are_at_least_44px(self):
        self.assertGreaterEqual(int(resolve("--sf-touch-min").rstrip("px")), 44)

    def test_structure_is_square_and_only_controls_round(self):
        self.assertEqual(resolve("--sf-radius-none"), "0")
        self.assertEqual(resolve("--sf-radius-control"), "10px")
        self.assertNotIn("shadow", CSS, "the brand has no shadows in its UI")


if __name__ == "__main__":
    unittest.main()
