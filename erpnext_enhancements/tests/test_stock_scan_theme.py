# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Stock Scan palette is switched three ways, and the two dark blocks must agree.

``public/css/stock_scan.bundle.css`` declares its ``--ss-*`` tokens once in ``:root``
(light), again inside ``@media (prefers-color-scheme: dark) {
:root:not([data-theme="light"]) }`` (system dark, unless the user forced light), and once
more in ``:root[data-theme="dark"]`` (the user forced dark). ``<html data-theme>`` ABSENT
means "follow the system"; the inline script at the top of ``www/stock-scan.html``'s
``head_include`` sets or removes it from ``localStorage.ee_ss_theme`` before the stylesheet
is parsed, so the first frame is not painted in the wrong palette.

A media block and a plain rule cannot share one declaration list, so the two dark blocks
are deliberate duplicates. The failure that matters is one being edited and the other not
— invisible until somebody opens the page in the mode nobody tested, which on this page is
a technician in a dim stock room with the phone in dark mode.

A clone of ``tests/test_kiosk_theme.py`` (same mechanism, same shape), adapted in three
places:

* The palette may carry tokens that are not colors (a radius, a duration, a font stack).
  Those need no dark twin, so "every light token is overridden in dark" is asserted for
  every token whose light value **is a color** — plus :data:`LIGHT_ONLY`, an allow-list with
  a reason for a color that is deliberately the same in both themes. A pure alias
  (``--ss-x: var(--ss-y)``) re-resolves against the dark ``--ss-y`` on the same element and
  needs no twin either.
* Selectors are matched on whitespace and quote style, not one spelling.
* The sheet host portalled to ``<body>`` must carry ``ee-ss-root`` (the v1.483.3 kiosk bug:
  a sheet outside the shell kept Frappe's dark heading color on a dark sheet).

The stylesheet and the shell are written by the front end; until they exist every test
here fails with a sentence naming the missing file (never a skip).

Run: python -m unittest erpnext_enhancements.tests.test_stock_scan_theme -v
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
CSS = APP / "public" / "css" / "stock_scan.bundle.css"
HTML = APP / "www" / "stock-scan.html"
UI_JS = APP / "public" / "js" / "stock_scan" / "ui.js"

PREFIX = "--ss-"

LIGHT = re.compile(r"(?m)^:root\s*\{")
DARK_MEDIA = re.compile(r""":root:not\(\[data-theme=["']light["']\]\)\s*\{""")
DARK_ATTR = re.compile(r""":root\[data-theme=["']dark["']\]\s*\{""")

#: Color tokens declared in the light palette and deliberately absent from the dark one,
#: each with the reason its light value is already right in dark. A name that drifts out of
#: the dark block silently keeps its light value, so this is an allow-list, not a tolerance.
LIGHT_ONLY: dict[str, str] = {}

_COLOR = re.compile(
	r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(|\b(?:white|black)\b",
	re.I,
)
_ALIAS = re.compile(r"^var\(\s*--[\w-]+\s*\)$")


def _read(path):
	if not path.is_file():
		raise AssertionError(
			f"{path.relative_to(APP.parent).as_posix()} does not exist. It is written by the front "
			"end to the Stock Scan build spec; this check cannot run until it is."
		)
	return path.read_text(encoding="utf-8")


def css():
	return _read(CSS)


def strip_comments(text):
	return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def block_after(pattern, text=None):
	"""The declaration text of the first rule opened by ``pattern``.

	The palette blocks contain no nested braces, so "up to the next closing brace" is exact.
	Comments are stripped first: they contain braces and colons, and this module asserts
	about declarations, not prose.
	"""
	source = strip_comments(css() if text is None else text)
	match = pattern.search(source)
	if not match:
		raise AssertionError(f"stock_scan.bundle.css has no rule matching {pattern.pattern}")
	return source[match.end() : source.index("}", match.end())]


def declarations(pattern, text=None):
	"""``{name: value}`` for the custom properties a block declares."""
	return {
		name: value.strip()
		for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", block_after(pattern, text))
	}


def is_color(value):
	return bool(_COLOR.search(value)) and not _ALIAS.match(value.strip())


def rules_for(selector_test):
	"""``[(selectors, body)]`` for every innermost rule whose selector list passes ``selector_test``."""
	source = strip_comments(css())
	out = []
	for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", source):
		names = [s.strip() for s in selectors.split(",")]
		if selector_test(names):
			out.append((names, body))
	return out


class TestTheBlocksAreThere(unittest.TestCase):
	"""Anti-vacuity: every comparison below reads blocks out of one file, and a missing
	selector makes ``block_after`` fail — but an empty palette would not."""

	def test_the_stylesheet_is_found(self):
		self.assertGreater(len(css()), 3_000)

	def test_the_light_palette_is_substantial(self):
		light = declarations(LIGHT)
		self.assertGreater(len(light), 12)
		self.assertIn("color-scheme: light", re.sub(r"\s+", " ", block_after(LIGHT)))

	def test_every_token_uses_the_pages_prefix(self):
		"""``--ss-*`` only: ``test_training_desk_theme`` owns ``--tr-*`` and the kiosk ``--tk-*``,
		and a shared name would let one page's palette reach into another's."""
		for pattern in (LIGHT, DARK_ATTR):
			with self.subTest(block=pattern.pattern):
				foreign = sorted(name for name in declarations(pattern) if not name.startswith(PREFIX))
				self.assertEqual(foreign, [])

	def test_every_selector_appears_exactly_once(self):
		source = strip_comments(css())
		for pattern in (LIGHT, DARK_MEDIA, DARK_ATTR):
			with self.subTest(selector=pattern.pattern):
				count = len(pattern.findall(source))
				self.assertEqual(count, 1, f"{pattern.pattern} appears {count}x")

	def test_the_light_block_comes_first(self):
		source = strip_comments(css())
		self.assertLess(LIGHT.search(source).start(), DARK_MEDIA.search(source).start())
		self.assertLess(DARK_MEDIA.search(source).start(), DARK_ATTR.search(source).start())

	def test_the_background_token_exists(self):
		"""The body, the theme-color meta and every surface start from it."""
		self.assertIn("--ss-bg", declarations(LIGHT))


class TestTheTwoDarkBlocksAgree(unittest.TestCase):
	"""The duplication is deliberate; the drift is the bug."""

	def test_the_dark_palettes_are_identical(self):
		self.assertEqual(
			declarations(DARK_MEDIA),
			declarations(DARK_ATTR),
			"the prefers-color-scheme dark block and the data-theme=dark block have drifted; "
			"a phone sees one of them and never the other",
		)

	def test_the_dark_palette_declares_dark(self):
		for pattern in (DARK_MEDIA, DARK_ATTR):
			with self.subTest(block=pattern.pattern):
				self.assertIn("color-scheme: dark", re.sub(r"\s+", " ", block_after(pattern)))

	def test_the_dark_palette_is_substantial(self):
		self.assertGreater(len(declarations(DARK_ATTR)), 8)


class TestDarkIsASubsetOfLight(unittest.TestCase):
	"""A name declared dark and not light has no fallback; a color declared light and not dark
	silently keeps its light value in the dark theme."""

	def test_dark_declares_nothing_light_does_not(self):
		extra = sorted(set(declarations(DARK_ATTR)) - set(declarations(LIGHT)))
		self.assertEqual(extra, [], f"{extra} exist only in the dark palette")

	def test_the_color_detector_works(self):
		"""Control for the assertion below: an empty or blind classifier passes anything."""
		for value in (
			"#fff",
			"#0077A8",
			"rgb(0 0 0 / 0.4)",
			"rgba(0,0,0,.12)",
			"hsl(200 50% 40%)",
			"color-mix(in srgb, #fff 10%, transparent)",
			"0 8px 24px rgba(0,0,0,.2)",
			"white",
		):
			with self.subTest(value=value):
				self.assertTrue(is_color(value))
		for value in (
			"12px",
			"200ms",
			"cubic-bezier(.2,.8,.2,1)",
			"system-ui, sans-serif",
			"var(--ss-green)",
			"1.5",
		):
			with self.subTest(value=value):
				self.assertFalse(is_color(value))

	def test_the_light_palette_is_mostly_colors(self):
		colors = [name for name, value in declarations(LIGHT).items() if is_color(value)]
		self.assertGreater(len(colors), 8, "the light palette has almost no colors?")

	def test_every_light_color_is_either_overridden_or_excused(self):
		dark = declarations(DARK_ATTR)
		missing = sorted(
			name
			for name, value in declarations(LIGHT).items()
			if is_color(value) and name not in dark and name not in LIGHT_ONLY
		)
		self.assertEqual(
			missing,
			[],
			f"{missing} keep their light color in dark mode. Override them in BOTH dark blocks, "
			"or add them to LIGHT_ONLY with the reason they are already correct.",
		)

	def test_every_excuse_is_for_a_real_token(self):
		stale = sorted(name for name in LIGHT_ONLY if name not in declarations(LIGHT))
		self.assertEqual(stale, [], f"{stale} are excused but no longer declared")

	def test_every_excuse_says_something(self):
		for name, reason in LIGHT_ONLY.items():
			self.assertGreater(len(reason.strip()), 20, f"{name}'s reason is a placeholder")


class TestTheMediaQueryIsGuarded(unittest.TestCase):
	"""An unguarded ``:root`` inside the dark media query would override a user who chose
	Light on a dark phone — the very case the three-way switch exists for."""

	def test_no_unguarded_root_in_a_dark_media_block(self):
		source = strip_comments(css())
		blocks = re.findall(r"@media\s*\(\s*prefers-color-scheme\s*:\s*dark\s*\)\s*\{(.*?)\n\}", source, re.S)
		self.assertTrue(blocks, "no dark media block found")
		for body in blocks:
			with self.subTest(body.strip()[:60]):
				self.assertNotRegex(
					body,
					r"(^|\n)\s*:root\s*\{",
					"an unguarded :root inside the dark media query overrides a forced light "
					'theme; guard it with :not([data-theme="light"])',
				)

	def test_the_attribute_selector_exists_for_every_media_override(self):
		source = strip_comments(css())
		media = len(re.findall(r""":root:not\(\[data-theme=["']light["']\]\)""", source))
		attr = len(re.findall(r""":root\[data-theme=["']dark["']\]""", source))
		self.assertEqual(media, attr, "every guarded media override needs a data-theme=dark twin")

	def test_dark_values_live_only_in_the_palette(self):
		"""A bare ``[data-theme="dark"] .x`` rule is a second declaration site for dark values
		that the palette tests above cannot see."""
		source = strip_comments(css())
		self.assertNotRegex(source, r"""(^|\n)\s*\[data-theme=["']dark["']\]""")


class TestTheBodyAndChromeAreExplicit(unittest.TestCase):
	def test_body_has_an_explicit_background(self):
		"""Frappe's website CSS paints its own body; without this the page's letterbox shows
		white around a dark app."""
		bodies = rules_for(lambda names: "body" in names)
		self.assertTrue(bodies, "no rule targets body")
		self.assertTrue(
			any(re.search(r"background(-color)?\s*:\s*var\(--ss-bg\)", body) for _n, body in bodies),
			"body must get background: var(--ss-bg)",
		)

	def test_headings_set_their_own_color(self):
		"""Frappe's website CSS colors h1-h6 with a fixed ``--heading-color`` that ignores the
		scheme: a heading that does not set its own color is dark-on-dark."""
		headings = rules_for(lambda names: any(re.search(r"(^|[\s>(,])h[1-6]\b", n) for n in names))
		self.assertTrue(
			any(re.search(r"(^|[;\s{])color\s*:\s*var\(--ss-", body) for _n, body in headings),
			"no heading rule sets color: var(--ss-...)",
		)

	def test_the_theme_color_meta_starts_from_the_light_ground(self):
		html = _read(HTML)
		meta = re.search(r"""<meta\s+name=["']theme-color["']\s+content=["']([^"']+)["']""", html)
		self.assertIsNotNone(meta, "stock-scan.html has no theme-color meta")
		self.assertEqual(meta.group(1).strip().lower(), declarations(LIGHT)["--ss-bg"].lower())


class TestTheInlineScriptRunsFirst(unittest.TestCase):
	"""The stored theme must be on ``<html>`` before the stylesheet is parsed, or the first
	frame paints in the wrong palette and then snaps."""

	STORAGE = re.compile(r"""localStorage\.getItem\(\s*["']ee_ss_theme["']\s*\)""")

	def html(self):
		# Jinja comments stripped: the template's prose may mention the stylesheet and the
		# script in whatever order reads well, and a position check against the raw text
		# would measure the prose rather than the tags.
		return re.sub(r"\{#.*?#\}", "", _read(HTML), flags=re.S)

	def head_include(self):
		html = self.html()
		start = re.search(r"\{%\s*block\s+head_include\s*%\}", html)
		self.assertIsNotNone(start, "stock-scan.html has no head_include block")
		end = re.search(r"\{%\s*endblock\s*%\}", html[start.end() :])
		return html[start.end() : start.end() + end.start()]

	def test_the_theme_script_is_the_first_thing_in_head_include(self):
		head = self.head_include()
		self.assertTrue(head.lstrip().startswith("<script"), "head_include must open with the theme script")
		first_script = head[: head.index("</script>")]
		self.assertRegex(first_script, self.STORAGE)

	def test_the_theme_script_precedes_the_stylesheet(self):
		head = self.head_include()
		script = self.STORAGE.search(head)
		sheet = re.search(r"""bundled_asset\(\s*["']stock_scan\.bundle\.css["']\s*\)""", head)
		self.assertIsNotNone(script)
		self.assertIsNotNone(sheet, "the stylesheet is not loaded in head_include")
		self.assertLess(script.start(), sheet.start())

	def test_the_theme_script_is_guarded(self):
		"""Storage throws in some private modes; the page must still paint (in the system theme)."""
		head = self.head_include()
		start = self.STORAGE.search(head).start()
		self.assertIn("try", head[max(0, start - 200) : start])
		self.assertIn("catch", head[start : start + 400])

	def test_absent_attribute_means_system(self):
		head = self.head_include()
		self.assertRegex(head, r"""removeAttribute\(\s*["']data-theme["']\s*\)""")
		self.assertRegex(head, r"""setAttribute\(\s*["']data-theme["']\s*,""")


class TestPortalledSheetsKeepThePalette(unittest.TestCase):
	"""A bottom sheet's host is appended to ``document.body`` so it can escape the app's
	stacking and overflow, which also puts it outside ``#ee-stock-scan-root``. The tokens and
	the heading colors hang on ``.ee-ss-root``, so the host carries that class too — else the
	sheet's title keeps Frappe's dark heading color on a dark sheet (the kiosk's v1.483.3)."""

	def test_the_sheet_host_carries_the_root_class(self):
		code = re.sub(r"//[^\n]*", "", strip_comments(_read(UI_JS)))
		self.assertIn("ee-ss-root", code, "ui.js never gives a portalled host the ee-ss-root class")
		self.assertIn("document.body", code, "the sheet host is expected to be portalled to <body>")


if __name__ == "__main__":
	unittest.main()
