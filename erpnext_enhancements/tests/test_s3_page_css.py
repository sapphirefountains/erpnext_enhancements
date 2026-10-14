"""Custom pages S3.4a/b (v1.646.0, v1.652.0, TASK-2026-02823): page CSS out of JS, on the tokens.

Five Desk pages used to style themselves from JS: a ``<style>`` block in a markup string, a
``QW_STYLE`` constant appended to the page, or inline ``style="color: ..."`` attributes. Their
CSS is now each page's own ``<page>.css``, which Frappe's page loader reads beside the page's
JS and sends with the page itself (``Page.load_assets`` in ``frappe/core/doctype/page/page.py``
on ``version-16``, through ``desk_page.getpage``), so an edit is never served stale from the
one-year immutable ``/assets`` cache. And the colours are the app's ``--sf-*`` tokens
(``docs/app-design-system.md``), which follow the Desk's light or dark theme.

S3.4b (v1.652.0) adds four more: Question Review and the Course Canvas, which already had
their own ``.css`` but painted it in Frappe's palette and the learner's Aurora palette; the
Sales Pipeline TV board; and the Location Timeline, whose stylesheet lived in
``public/css/workforce/`` and came in by ``frappe.require`` of a raw ``/assets`` path.

This fails the build if any of these pages:

* writes a ``<style>`` element from JS again, or paints a colour through an inline ``style``,
  a jQuery ``.css(...)`` colour write or an ``attr("style", ...)``;
* loses its ``.css`` file;
* puts a literal colour (hex, ``rgb()``, ``hsl()``, a named colour such as ``white``) or a
  Frappe palette variable (``--red-500``, ``--bg-orange``, ``--text-on-red`` and the like)
  into its CSS, where the status tokens belong;
* writes a selector that is not namespaced to the page. A Desk page's CSS is appended to
  ``<head>`` once and never removed, so a bare ``td.is-bad`` styles every table cell with
  that class on every page visited afterwards;
* reads a ``var(--sf-...)`` that ``sf_tokens.bundle.css`` does not define, which would resolve
  to nothing and fail silently;
* declares a custom property of its own, uses a shadow, a radius that is not an
  ``--sf-radius-*`` token, or a width breakpoint other than the one phone breakpoint, 767px
  (the two Training pages also keep Frappe's own 991.98px, where the Desk's two-column layout
  collapses, which ``test_training_review`` and ``test_training_canvas`` pin; the Location
  Timeline stacks its map and side panel there too);
* reads the learner's Aurora palette (``var(--tr-...)``), which belongs to ``player.css`` and
  the learner surfaces, not to Desk chrome;
* names a ``@keyframes`` without the page's prefix (animation names are as global as the
  stylesheet is);
* leaves a comment that opens inside another comment or closes twice. The Course Canvas had
  one: a section was pasted into the middle of the narrow-screens comment, so the comment
  ended early, its second half sat in the stylesheet as a selector, and the browser threw the
  whole ``@media (max-width: 991.98px)`` block away with it. The tablet layout never applied.

It also holds every Training page's ``TR.loadAssets`` call to passing the deploy version. The
Glossary Review page passed none, so it asked for ``desk_nav.css?v=`` (one unchanging URL) and a
device that had cached the shared rail never saw an edit to it again.

Bench-free: reads files only, no frappe stub.
Run: python -m unittest erpnext_enhancements.tests.test_s3_page_css
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
TOKENS = APP / "public" / "css" / "sf_tokens.bundle.css"
CI = REPO / ".github" / "workflows" / "ci.yml"

PAGES = {
	"drive_link_manager": APP / "google_drive" / "page" / "drive_link_manager",
	"ga4_dashboard": APP / "integration_hub" / "page" / "ga4_dashboard",
	"inspection_wizard": APP / "quality" / "page" / "inspection_wizard",
	"training_insights": APP / "training" / "page" / "training_insights",
	"training_glossary_review": APP / "training" / "page" / "training_glossary_review",
	# S3.4b
	"training_review": APP / "training" / "page" / "training_review",
	"training_canvas": APP / "training" / "page" / "training_canvas",
	"location_timeline": APP / "workforce" / "page" / "location_timeline",
	"sales_pipeline": APP / "crm_enhancements" / "page" / "sales_pipeline",
}

# What each page's selectors must start with: the first compound of every selector carries the
# page's own prefix or its wrapper. A Desk page's CSS stays in <head> for the rest of the
# session, so an unprefixed selector leaks onto every page visited after it.
NAMESPACES = {
	"drive_link_manager": re.compile(r"\.(?:dlm-[\w-]+|drive-link-manager\b)"),
	"ga4_dashboard": re.compile(r"\.(?:ga4-|gsc-)[\w-]+"),
	"inspection_wizard": re.compile(r"\.qw-[\w-]+"),
	"training_insights": re.compile(r"\.ti-[\w-]+"),
	"training_glossary_review": re.compile(r"\.gr-page\b"),
	"training_review": re.compile(r"\.tq-[\w-]+"),
	"training_canvas": re.compile(r"\.(?:tc-[\w-]+|training-canvas-fullbleed\b)"),
	"location_timeline": re.compile(r"\.lt-[\w-]+"),
	# The board's own class names (.pipeline-card, .rail-chip) are generic, so every rule sits
	# under the page body (.sales-pipeline-page) or the TV-mode body class.
	"sales_pipeline": re.compile(r"\.sales-pipeline-[\w-]+"),
}

# A @keyframes name is global in the same way a selector is: two pages that both define
# `pulse` animate with whichever loaded last.
KEYFRAME_PREFIXES = {
	"drive_link_manager": ("dlm-",),
	"ga4_dashboard": ("ga4-", "gsc-"),
	"inspection_wizard": ("qw-",),
	"training_insights": ("ti-",),
	"training_glossary_review": ("gr-",),
	"training_review": ("tq-",),
	"training_canvas": ("tc-",),
	"location_timeline": ("lt-",),
	"sales_pipeline": ("sales-pipeline-",),
}

# Width breakpoints a page may keep besides 767px. Both Training pages are two-column Desk pages
# (the Training rail is page.sidebar), and Frappe collapses that layout at its own
# media-breakpoint-down, 991.98px; their split panes stack at the same width. The .98 is
# load-bearing (991 leaves a gap at the fractional widths browser zoom produces), and
# test_training_review / test_training_canvas pin it.
EXTRA_BREAKPOINTS = {
	"training_review": {"991.98px"},
	"training_canvas": {"991.98px"},
	# The map and its 340px side panel stack at the same width, so a portrait tablet (768-991px)
	# does not get a map squeezed to a strip beside the panel.
	"location_timeline": {"991.98px"},
}

# Frappe's own colour scale and its status tints. Theme-aware, but not the app's status
# colours: a page that reads --red-500 for "failed" says something different from every page
# that reads --sf-danger. `--bg-color` is not in it: that is the Desk's structural page ground,
# which the Inspection Wizard's sticky tabs and fixed nav bar must match exactly.
FRAPPE_PALETTE = re.compile(
	r"var\(\s*--(?:"
	r"(?:red|green|yellow|orange|gray|grey|blue|purple|pink|cyan)-\d"
	r"|text-on-[\w-]+"
	r"|bg-(?!color\b)[\w-]+"
	r")"
)

# CSS named colours, as whole words in a declaration's value. `--sf-white` and a class such as
# `.qw-banner-red` are not matches (a hyphen joins them to a longer name); `transparent`,
# `currentColor` and `inherit` are not colours of their own and are allowed.
NAMED_COLOURS = re.compile(
	r"(?<![\w-])(?:white|black|red|green|blue|yellow|orange|gray|grey|purple|pink|silver|navy"
	r"|teal|maroon|olive|lime|aqua|fuchsia|cyan|magenta|brown|gold|indigo|violet|crimson"
	r"|tomato|salmon|coral|khaki|beige|ivory|lavender|tan|turquoise|whitesmoke|gainsboro)"
	r"(?![\w-])",
	re.I,
)

COLOUR_PROPS = r"(?:color|background|border|fill|stroke|outline)"


def _read(path):
	return path.read_text(encoding="utf-8")


def _css_code(text):
	return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _js_code(text):
	# Only a block comment that opens a line: "image/*" in a string is not one.
	text = re.sub(r"(?m)^\s*/\*.*?\*/", "", text, flags=re.S)
	# A line comment starts at the beginning of a line or after whitespace, never inside a URL.
	return re.sub(r"(^|\s)//[^\n]*", r"\1", text)


def _bodies(css):
	"""The declaration blocks: the innermost {...}, so an @media block's rules are included."""
	return re.findall(r"\{([^{}]*)\}", css)


def _declarations(css):
	out = []
	for body in _bodies(css):
		for decl in body.split(";"):
			if ":" in decl:
				prop, value = decl.split(":", 1)
				out.append((prop.strip().lower(), value.strip()))
	return out


def _selectors(css):
	"""Every rule's selector list, split on commas. @-rule preludes (@media, @keyframes) are
	wrappers, not selectors, and a keyframe's from / to / N% is a step, not a selector."""
	out = []
	for prelude in re.findall(r"([^{}]+)\{", css):
		prelude = prelude.strip()
		if not prelude or prelude.startswith("@"):
			continue
		for selector in prelude.split(","):
			selector = selector.strip()
			if selector and not re.fullmatch(r"from|to|\d+(?:\.\d+)?%", selector):
				out.append(selector)
	return out


def _first_compound(selector):
	return re.split(r"\s*[>+~]\s*|\s+", selector.strip(), maxsplit=1)[0]


def _js_colour_writes(js):
	"""jQuery colour writes and raw style attributes, which an inline-style check misses."""
	found = re.findall(r"\.css\(\s*\{[^}]*?\b" + COLOUR_PROPS + r"[\w-]*\s*:", js)
	found += re.findall(r"\.css\(\s*[\"']" + COLOUR_PROPS + r"[\w-]*[\"']", js)
	found += re.findall(r"\.attr\(\s*[\"']style[\"']", js)
	found += re.findall(r"setAttribute\(\s*[\"']style[\"']", js)
	found += re.findall(r"\.style\." + COLOUR_PROPS + r"\w*\s*=", js, flags=re.I)
	return found


def _defined_tokens():
	return set(re.findall(r"(--sf-[a-z0-9-]+)\s*:", _css_code(_read(TOKENS))))


class TestTheCssLeftTheJs(unittest.TestCase):
	def test_no_page_writes_a_style_element(self):
		for name, folder in PAGES.items():
			js = _js_code(_read(folder / f"{name}.js"))
			with self.subTest(page=name):
				self.assertNotRegex(js, r"(?i)<style\b", "a <style> block is written from JS")
				self.assertNotRegex(js, r"createElement\(\s*['\"]style['\"]")
				self.assertNotIn("_STYLE", js)

	def test_no_page_paints_a_colour_inline(self):
		"""Layout widths may stay inline (a meter's width is data); a colour may not."""
		for name, folder in PAGES.items():
			js = _js_code(_read(folder / f"{name}.js"))
			inline = re.findall(r"style=\"([^\"]*)\"", js) + re.findall(r"style='([^']*)'", js)
			painted = [s for s in inline if re.search(r"(?i)(color|background|border)\s*:", s)]
			with self.subTest(page=name):
				self.assertEqual(painted, [], f"inline colour in {name}.js")

	def test_no_page_writes_a_colour_through_jquery_or_a_style_attribute(self):
		for name, folder in PAGES.items():
			js = _js_code(_read(folder / f"{name}.js"))
			with self.subTest(page=name):
				self.assertEqual(_js_colour_writes(js), [], f"colour written from {name}.js")

	def test_the_js_colour_check_catches_each_form(self):
		"""Proof the check above is not vacuous: none of the five pages writes one today."""
		for sample in (
			'$el.css({ width: "10%", background: "#fff" });',
			'$el.css("color", "red");',
			'$el.attr("style", "x");',
			'el.setAttribute("style", "x");',
			'el.style.backgroundColor = "x";',
		):
			with self.subTest(sample=sample):
				self.assertTrue(_js_colour_writes(sample))
		self.assertEqual(_js_colour_writes('$wrap.css("width", `${value}%`);'), [])

	def test_every_page_has_its_css(self):
		for name, folder in PAGES.items():
			with self.subTest(page=name):
				css = folder / f"{name}.css"
				self.assertTrue(css.exists(), f"{css} is missing")
				self.assertTrue(_css_code(_read(css)).strip(), f"{css.name} has no rules")


class TestTheCssIsOnTheTokens(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.css = {name: _css_code(_read(folder / f"{name}.css")) for name, folder in PAGES.items()}
		cls.tokens = _defined_tokens()

	def test_the_token_file_was_read(self):
		self.assertIn("--sf-danger", self.tokens)
		self.assertIn("--sf-radius-none", self.tokens)

	def test_no_literal_colours(self):
		for name, css in self.css.items():
			for body in _bodies(css):
				with self.subTest(page=name, block=body.strip()[:60]):
					self.assertNotRegex(body, r"#[0-9a-fA-F]{3,8}\b")
					self.assertNotRegex(body, r"(?i)\b(?:rgba?|hsla?)\(")
					self.assertNotRegex(body, FRAPPE_PALETTE)

	def test_no_named_colours(self):
		for name, css in self.css.items():
			for prop, value in _declarations(css):
				if prop == "content":
					continue
				with self.subTest(page=name, declaration=f"{prop}: {value}"):
					self.assertNotRegex(value, NAMED_COLOURS)

	def test_the_colour_patterns_catch_what_they_name(self):
		for value in ("white", "1px solid Red", "var(--bg-orange, x)", "var(--text-on-red)"):
			with self.subTest(value=value):
				self.assertTrue(NAMED_COLOURS.search(value) or FRAPPE_PALETTE.search(value))
		for value in ("var(--sf-white)", "transparent", "currentColor", "var(--bg-color)", "normal"):
			with self.subTest(value=value):
				self.assertFalse(NAMED_COLOURS.search(value) or FRAPPE_PALETTE.search(value))

	def test_every_selector_is_namespaced_to_its_page(self):
		for name, css in self.css.items():
			namespace = NAMESPACES[name]
			selectors = _selectors(css)
			with self.subTest(page=name):
				self.assertTrue(selectors)
			for selector in selectors:
				with self.subTest(page=name, selector=selector):
					self.assertRegex(
						_first_compound(selector),
						namespace,
						"page CSS stays in <head> all session; prefix the selector or nest it under the page",
					)

	def test_every_sf_token_read_is_defined(self):
		for name, css in self.css.items():
			used = set(re.findall(r"var\(\s*(--sf-[a-z0-9-]+)", css))
			with self.subTest(page=name):
				self.assertTrue(used, f"{name}.css reads no --sf-* token at all")
				self.assertEqual(sorted(used - self.tokens), [], "not defined in sf_tokens.bundle.css")

	def test_no_custom_property_of_its_own(self):
		for name, css in self.css.items():
			own = [prop for prop, _ in _declarations(css) if prop.startswith("--")]
			with self.subTest(page=name):
				self.assertEqual(own, [])

	def test_no_shadows(self):
		for name, css in self.css.items():
			with self.subTest(page=name):
				self.assertNotIn("shadow", css)

	def test_every_radius_is_a_token(self):
		for name, css in self.css.items():
			for prop, value in _declarations(css):
				if not prop.startswith("border") or "radius" not in prop:
					continue
				parts = re.findall(r"var\([^)]*\)|\S+", value)
				with self.subTest(page=name, value=value):
					for part in parts:
						self.assertTrue(
							part == "0" or part.startswith("var(--sf-radius-"),
							f"{prop}: {value} is not an --sf-radius-* token",
						)

	def test_the_one_phone_breakpoint(self):
		for name, css in self.css.items():
			widths = re.findall(r"@media[^{]*\((?:max|min)-width\s*:\s*([^)]+)\)", css)
			with self.subTest(page=name):
				self.assertIn("767px", widths, f"{name}.css has no phone breakpoint")
				self.assertLessEqual(set(widths), {"767px"} | EXTRA_BREAKPOINTS.get(name, set()))

	def test_no_learner_palette(self):
		"""--tr-* is the learner's Aurora palette (player.css). The Course Canvas read it for its
		chrome, with a literal fallback beside every read; the Desk chrome is on --sf-* now. The
		learner render inside the canvas still takes player.css, which is the point of it."""
		for name, css in self.css.items():
			with self.subTest(page=name):
				self.assertNotRegex(css, r"var\(\s*--tr-")

	def test_every_keyframes_name_is_prefixed(self):
		for name, css in self.css.items():
			for frames in re.findall(r"@keyframes\s+([\w-]+)", css):
				with self.subTest(page=name, keyframes=frames):
					self.assertTrue(frames.startswith(KEYFRAME_PREFIXES[name]))


class TestTheCommentsAreWellFormed(unittest.TestCase):
	"""CSS comments do not nest. A `/*` inside a comment is text, so the comment ends at the
	first `*/`, and the rest of the prose becomes a selector that swallows the next rule. That
	is how training_canvas.css lost its whole tablet layout (see the module docstring), and the
	selector check above only catches it when the prose happens to land before a rule."""

	def test_no_comment_opens_inside_a_comment(self):
		for name, folder in PAGES.items():
			raw = _read(folder / f"{name}.css")
			for body in re.findall(r"/\*(.*?)\*/", raw, flags=re.S):
				with self.subTest(page=name, comment=body.strip()[:60]):
					self.assertNotIn("/*", body)

	def test_no_comment_closes_twice(self):
		for name, folder in PAGES.items():
			with self.subTest(page=name):
				self.assertNotIn("*/", _css_code(_read(folder / f"{name}.css")))


class TestTrainingAssetsCarryTheVersion(unittest.TestCase):
	def test_every_load_assets_call_passes_a_version(self):
		"""TR.loadAssets appends ``?v=<version>``. With no second argument the token is
		``?v=``, which passes its own guard and busts nothing."""
		calls = 0
		for path in sorted((APP / "training" / "page").glob("*/*.js")):
			for args in re.findall(r"TR\.loadAssets\(([^;]*?)\)\s*[;.\n]", _js_code(_read(path))):
				calls += 1
				with self.subTest(page=path.name, args=args):
					self.assertIn(",", args, "TR.loadAssets called without the deploy version")
		self.assertGreaterEqual(calls, 4)

	def test_the_glossary_review_passes_the_deploy_version(self):
		js = _js_code(_read(PAGES["training_glossary_review"] / "training_glossary_review.js"))
		self.assertIn("TR.loadAssets(GR_NAV_ASSETS, version)", js)
		self.assertIn("frappe.boot.versions.erpnext_enhancements", js)


class TestTheLocationTimelineStylesheetComesWithThePage(unittest.TestCase):
	"""It used to be public/css/workforce/location_timeline.css, pulled in by frappe.require of a
	raw /assets path. v16's frappe.require does append ?v=<build> to such a path (assets.js,
	AssetManager.execute), so it was not served stale; but it was a second request behind a
	frappe.dom.freeze(), on a stylesheet path the page loader would have delivered with the page
	for nothing. One way in, the same as every other page here."""

	JS = PAGES["location_timeline"] / "location_timeline.js"

	def test_the_old_stylesheet_is_gone(self):
		self.assertFalse((APP / "public" / "css" / "workforce" / "location_timeline.css").exists())

	def test_the_legend_and_the_map_read_the_same_tokens(self):
		"""The legend swatches are CSS and the map strokes are Maps API colour values; both read
		the same token, the script through getComputedStyle, so they cannot drift apart."""
		js = _js_code(_read(self.JS))
		css = _css_code(_read(PAGES["location_timeline"] / "location_timeline.css"))
		for const, token, swatch in (
			("GAP_COLOR", "--sf-danger", ".lt-swatch-gap"),
			("STOP_COLOR", "--sf-warn", ".lt-swatch-stop"),
			("SITE_COLOR", "--sf-pillar-service", ".lt-swatch-site"),
		):
			with self.subTest(const=const):
				self.assertRegex(js, rf"const {const} = tokenColor\('{token}', '#[0-9a-f]{{6}}'\)")
				rule = re.search(re.escape(swatch) + r"\s*\{([^}]*)\}", css).group(1)
				self.assertIn(f"var({token})", rule)
		self.assertIn("getComputedStyle(document.documentElement).getPropertyValue(name).trim()", js)

	def test_the_page_requires_no_stylesheet(self):
		js = _js_code(_read(self.JS))
		self.assertNotIn("location_timeline.css", js)
		self.assertNotRegex(js, r"['\"]/assets/[^'\"]+\.css")


class TestTheSalesPipelineWiringIsUntouched(unittest.TestCase):
	"""The new Opportunity board reuses the board's server method and its realtime event, and
	hooks.py calls publish_pipeline_update / stamp_stage_change. S3.4b restyled the board and must
	not have moved any of it."""

	def test_the_page_still_listens_and_fetches(self):
		js = _js_code(_read(PAGES["sales_pipeline"] / "sales_pipeline.js"))
		self.assertIn('frappe.realtime.on("sales_pipeline_updated", debounced)', js)
		self.assertIn("crm_enhancements.page.sales_pipeline.sales_pipeline.get_pipeline_data", js)

	def _rule(self, selector):
		css = _css_code(_read(PAGES["sales_pipeline"] / "sales_pipeline.css"))
		m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
		self.assertIsNotNone(m, f"no rule for {selector}")
		return m.group(1)

	def test_a_focused_stale_card_keeps_its_focus_ring(self):
		"""The red pulse animates outline-color, and the stale rule sets its own outline after the
		generic :focus-visible ring, so a keyboard user lost the ring on every red card."""
		body = self._rule(".sales-pipeline-page .pipeline-card.stale-red:focus-visible")
		self.assertIn("animation: none", body)
		self.assertIn("var(--sf-focus)", body)

	def test_a_stale_card_keeps_its_hairline(self):
		"""A chip edge on a whole card flips with the theme (near-black Aging in light, off-white
		Stale in dark). The stripe and the chip carry the status; the card border does not."""
		for selector in (
			".sales-pipeline-page .pipeline-card.stale-amber",
			".sales-pipeline-page .pipeline-card.stale-red",
			".sales-pipeline-page .rail-chip.rail-overdue",
		):
			with self.subTest(selector=selector):
				body = self._rule(selector)
				self.assertNotIn("chip-edge", body)
				self.assertNotIn("border-color", body)

	def test_the_hooks_still_name_the_server_functions(self):
		hooks = _read(APP / "hooks.py")
		for fn in ("sales_pipeline.publish_pipeline_update", "sales_pipeline.stamp_stage_change"):
			with self.subTest(fn=fn):
				self.assertIn(fn, hooks)


class TestThisSuiteRunsInCi(unittest.TestCase):
	def test_registered(self):
		registered = "python -m unittest erpnext_enhancements.tests.test_s3_page_css" in _read(CI)
		self.assertTrue(registered, "add a `python -m unittest` step for this suite to ci.yml")


if __name__ == "__main__":
	unittest.main()
