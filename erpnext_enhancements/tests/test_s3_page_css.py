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

S3.4c (v1.661.0) adds Plan a Trip, which wrote its whole stylesheet from the script (``TP_STYLE``)
and painted its Map markers inline. Its Map colours are the one place a page here still needs a
colour value (the Maps API takes no CSS variable), so the script reads them from the same tokens
its CSS uses; ``TestPlanATrip`` holds the two together. A ``style.cssText`` assignment that sets a
colour now counts as an inline colour too.

S3.4d (v1.664.0) adds the Project Planner, which wrote eleven ``<style>`` elements from its script
(``PP_STYLE`` ... ``PPT4_STYLE``) and painted people's and tasks' colors inline. Those two colors
are data from records people edit, so they are the one inline color a page here may write, and only
as the custom property ``style="--pp-color: ${pp_esc(...)}"`` that its CSS paints with;
``TestProjectPlanner`` holds the page to that and to the rest of the restyle.

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
	# S3.4c
	"plan_a_trip": APP / "travel_management" / "page" / "plan_a_trip",
	# S3.4d
	"project_planner": APP / "project_enhancements" / "page" / "project_planner",
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
	"plan_a_trip": re.compile(r"\.tp-[\w-]+"),
	# The planner's own families: pp- for the page, p6b- and p6e- for two of its phases, t1- for
	# Build out and t4- for AI draft. Short, so TestProjectPlanner checks no other file styles them.
	"project_planner": re.compile(r"\.(?:pp|p6b|p6e|t1|t4)-[\w-]+"),
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
	"plan_a_trip": ("tp-",),
	"project_planner": ("pp-", "p6b-", "p6e-", "t1-", "t4-"),
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
	# A whole declaration list in one assignment (Plan a Trip's map pin and popup, before S3.4c).
	# The statement runs to the first ";" that ends a line, past the semicolons inside the CSS.
	for statement in re.findall(r"\.style\.cssText\s*=(.*?);[ \t]*(?:\n|$)", js, flags=re.S):
		found += re.findall(r"(?i)\b" + COLOUR_PROPS + r"[\w-]*\s*:", statement)
	return found


# The one color a page may write inline: a color that is data (a task's or a person's own, from a
# record people edit), as the custom property --pp-color, escaped through pp_esc, which the page's
# CSS paints with var(--pp-color). The Project Planner is the only page that does (S3.4d).
DATA_COLOR = re.compile(r"(?<![\w-])--pp-color:\s*\$\{pp_esc\(")


def _without_data_color(style):
	return DATA_COLOR.sub("", style)


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
		"""Layout widths may stay inline (a meter's width is data); a color may not, except a color
		that is itself data, handed to the page's CSS as --pp-color (DATA_COLOR)."""
		for name, folder in PAGES.items():
			js = _js_code(_read(folder / f"{name}.js"))
			inline = re.findall(r"style=\"([^\"]*)\"", js) + re.findall(r"style='([^']*)'", js)
			painted = [
				s for s in inline if re.search(r"(?i)(color|background|border)\s*:", _without_data_color(s))
			]
			with self.subTest(page=name):
				self.assertEqual(painted, [], f"inline colour in {name}.js")

	def test_the_data_color_allowance_is_exactly_one_pattern(self):
		"""The allowance takes the custom property through pp_esc and nothing else: a literal, a
		second property, or the same property written raw is still an inline color."""
		for allowed in (
			"--pp-color: ${pp_esc(color)}",
			"--pp-color: ${pp_esc(\n\t\tthis.resource_color(x)\n\t)}",
		):
			with self.subTest(allowed=allowed):
				self.assertNotRegex(_without_data_color(allowed), r"(?i)(color|background|border)\s*:")
		for refused in (
			"--pp-color: #2563eb",
			"--pp-color: ${color}",
			"--x-color: ${pp_esc(color)}",
			"--pp-color: ${pp_esc(color)};background:#fff",
			"border-left-color:${pp_esc(color)}",
		):
			with self.subTest(refused=refused):
				self.assertRegex(_without_data_color(refused), r"(?i)(color|background|border)\s*:")

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
			'pin.style.cssText =\n\t"min-width:26px;height:26px;" +\n\t`background:${kind.color};color:#fff;`;\n',
			'box.style.cssText = "max-width:260px;color:#202124;font-size:13px;";\n',
		):
			with self.subTest(sample=sample):
				self.assertTrue(_js_colour_writes(sample))
		self.assertEqual(_js_colour_writes('$wrap.css("width", `${value}%`);'), [])
		self.assertEqual(_js_colour_writes('el.style.cssText = "width:10px;height:4px;";\n'), [])

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


class TestPlanATrip(unittest.TestCase):
	"""S3.4c (v1.661.0). Plan a Trip wrote its whole stylesheet from the script (TP_STYLE, about
	200 rules in Frappe's palette with literal hex for every status and a [data-theme="dark"]
	block of its own), painted the Map list's numbers and its markers inline, and showed a step
	tab's missing count in red alone."""

	FOLDER = PAGES["plan_a_trip"]

	@classmethod
	def setUpClass(cls):
		cls.js = _js_code(_read(cls.FOLDER / "plan_a_trip.js"))
		css = cls.FOLDER / "plan_a_trip.css"
		cls.css = _css_code(_read(css)) if css.exists() else ""

	def _rule(self, selector):
		m = re.search(r"(?m)^" + re.escape(selector) + r"\s*[,{]", self.css)
		self.assertIsNotNone(m, f"no rule for {selector} in plan_a_trip.css")
		return self.css[m.start() :].split("{", 1)[1].split("}", 1)[0]

	def test_no_theme_block_of_its_own(self):
		"""The tokens follow the Desk's theme; a dark block beside them is a second palette."""
		self.assertTrue(self.css)
		self.assertNotIn("data-theme", self.css)

	def test_the_map_kinds_read_one_token_each_in_the_script_and_the_css(self):
		"""The older Marker's fill is a value (read from the token), the list's number and the HTML
		marker are CSS (the same token, by data-kind), so the three cannot drift apart."""
		kinds = re.search(r"const TP_PLACE_KINDS = \{(.*?)\n\};", self.js, flags=re.S)
		self.assertIsNotNone(kinds)
		# One entry per kind, each opening on its own line ("\tairport: {"); a glyph such as
		# "\u{1F3E8}" has braces of its own, so the entries are split on their openings.
		parts = re.split(r"(?m)^\t(\w+): \{", kinds.group(1))[1:]
		found = {}
		for kind, body in zip(parts[0::2], parts[1::2], strict=True):
			m = re.search(r'color: tp_token_color\("(--sf-[a-z0-9-]+)", "#[0-9a-f]{6}"\)', body)
			with self.subTest(kind=kind):
				self.assertIsNotNone(m, f"{kind}'s colour is not read from a token")
			found[kind] = m.group(1) if m else None
		self.assertEqual(sorted(found), ["airport", "dropoff", "freight", "hotel", "pickup", "stop"])
		for kind, token in found.items():
			for selector in (f'.tp-map-num[data-kind="{kind}"]', f'.tp-map-pin[data-kind="{kind}"]'):
				with self.subTest(kind=kind, selector=selector):
					self.assertIn(f"var({token})", self._rule(selector))

	def test_the_lines_and_the_marker_ring_read_tokens(self):
		self.assertRegex(self.js, r'const TP_MARKER_WHITE = tp_token_color\("--sf-white", "#ffffff"\);')
		legs = re.search(r"const TP_LEG_COLORS = \{(.*?)\};", self.js, flags=re.S).group(1)
		self.assertEqual(
			sorted(re.findall(r"(\w+): tp_token_color\(\"--sf-[a-z0-9-]+\", \"#[0-9a-f]{6}\"\)", legs)),
			["drive", "flight", "walk"],
		)
		self.assertIn("getComputedStyle(document.documentElement).getPropertyValue(name).trim()", self.js)
		# No colour value is written anywhere else in the script ("&#128196;" is an icon).
		rest = re.sub(r'tp_token_color\("--sf-[a-z0-9-]+", "#[0-9a-f]{6}"\)', "", self.js)
		self.assertNotRegex(rest, r"(?<!&)#[0-9a-fA-F]{3,8}\b")
		self.assertNotRegex(rest, r"\brgba?\(")

	def test_the_number_and_the_pin_are_classes(self):
		self.assertIn(
			'<span class="tp-map-num" data-kind="${TP_PLACE_KINDS[place.kind] ? place.kind : "stop"}">',
			self.js,
		)
		self.assertIn('pin.className = "tp-map-pin";', self.js)
		self.assertIn('box.className = "tp-map-popup";', self.js)
		self.assertNotIn(".style.", self.js)

	def test_a_step_tabs_red_count_says_missing(self):
		self.assertIn(
			'<span class="tp-badge">${count}<span class="tp-sr"> ${__("missing")}</span></span>', self.js
		)
		self.assertIn('title="${tp_esc(__("{0} missing", [count]))}"', self.js)
		self.assertIn("clip: rect(0, 0, 0, 0)", self._rule(".tp-sr"))

	def test_warn_text_is_the_mix_never_yellow(self):
		"""Plain yellow is 1.7:1 on white: warn as text is mixed with --sf-text-strong."""
		for prop, value in _declarations(self.css):
			if prop == "color" and "--sf-warn" in value:
				with self.subTest(value=value):
					self.assertEqual(value, "color-mix(in srgb, var(--sf-warn) 50%, var(--sf-text-strong))")
		self.assertIn(
			"color-mix(in srgb, var(--sf-warn) 50%, var(--sf-text-strong))", self._rule(".tp-notice")
		)

	def test_controls_are_touch_sized(self):
		for selector in (".tp-tab", ".tp-btn", ".tp-seg button", ".tp-doc-x", ".tp-pdoc"):
			with self.subTest(selector=selector):
				self.assertIn("min-height: var(--sf-touch-min)", self._rule(selector))
		phone = self.css[self.css.index("@media (max-width: 767px)") :]
		self.assertRegex(phone, r"button\.tp-chip,\s*\.tp-vbtn,[^{]*\{[^}]*min-height: var\(--sf-touch-min\)")

	def test_every_class_the_script_draws_is_styled_and_every_rule_is_drawn(self):
		"""An unstyled element, or a rule for a class nothing draws any more, is a sign of a rename
		on one side only. The four left out are a container or a click hook on purpose, and a radio
		group's name."""
		hooks = {"tp-step", "tp-view", "tp-map-day", "tp-lead"}
		drawn = set(re.findall(r"\btp-[a-z0-9]+(?:-[a-z0-9]+)*", self.js)) - hooks
		styled = set(re.findall(r"\.(tp-[a-z0-9]+(?:-[a-z0-9]+)*)", self.css))
		self.assertEqual(sorted(drawn - styled), [], "drawn by the script, styled nowhere")
		self.assertEqual(sorted(styled - drawn), [], "styled, drawn by nothing")

	def test_a_link_field_and_the_person_search_stay_white_while_focused(self):
		"""A Link field's input is a Bootstrap .form-control, whose :focus rule (0,2,0) outranked
		.tp-field input (0,1,1) and turned it grey while typing; the person search on Who's going
		is a bare .form-control outside .tp-field."""
		self.assertIn('class="form-control tp-search"', self.js)
		for selector in (".tp-wrap .tp-field .form-control:focus", ".tp-wrap .tp-search.form-control:focus"):
			with self.subTest(selector=selector):
				body = self._rule(selector)
				self.assertIn("background: var(--sf-field-bg)", body)
				self.assertIn("color: var(--sf-field-text)", body)
				self.assertIn("font-size: var(--sf-text-input)", body)
		self.assertRegex(
			self.css,
			r"\.tp-wrap \.tp-field \.form-control:focus,\s*\.tp-wrap \.tp-search\.form-control:focus\s*"
			r"\{\s*border-color: var\(--sf-focus\);\s*\}",
		)

	def test_the_chosen_person_in_the_view_bar_does_not_shift(self):
		"""A wider border on the chosen select moved the bar by a pixel; only the colour changes,
		and the outline (outside the layout) doubles it."""
		body = self._rule(".tp-viewbar select.tp-active")
		self.assertNotRegex(body, r"(?m)^\s*border(?:-width)?\s*:")
		self.assertIn("border-color: var(--sf-focus)", body)
		self.assertIn("outline:", body)

	def test_a_changed_hue_falls_back_to_itself(self):
		"""Pick-up and the drive lines changed hue; their fallback is the new colour, not the old."""
		self.assertIn('tp_token_color("--sf-green", "#1e7e51")', self.js)
		self.assertEqual(self.js.count('tp_token_color("--sf-fresh-blue", "#00a0df")'), 2)


class TestProjectPlanner(unittest.TestCase):
	"""S3.4d (v1.664.0). The Project Planner wrote eleven <style> elements from its script, in
	Frappe's palette with a literal hex for every status, 17 shadows, three width breakpoints (760,
	761 and 1024px) and 32-40px touch targets, and painted a task's and a person's color inline."""

	FOLDER = PAGES["project_planner"]

	@classmethod
	def setUpClass(cls):
		cls.raw_js = _read(cls.FOLDER / "project_planner.js")
		cls.js = _js_code(cls.raw_js)
		css = cls.FOLDER / "project_planner.css"
		cls.css = _css_code(_read(css)) if css.exists() else ""

	def _rule(self, selector):
		m = re.search(r"(?m)^\t*" + re.escape(selector) + r"\s*[,{]", self.css)
		self.assertIsNotNone(m, f"no rule for {selector} in project_planner.css")
		return self.css[m.start() :].split("{", 1)[1].split("}", 1)[0]

	def test_the_script_writes_no_stylesheet(self):
		self.assertNotIn("pp-style", self.js)
		self.assertNotIn("document.head", self.js)
		self.assertNotRegex(self.js, r"const PP\w*_STYLE\b")

	def test_every_color_it_reads_is_a_token_or_the_data_color(self):
		"""No Frappe variable at all (--text-muted, --card-bg, --primary ...), not only the palette
		ones the shared check refuses: every var() is an --sf-* token or one of the two data
		colors the script sets."""
		self.assertTrue(self.css)
		read = set(re.findall(r"var\(\s*(--[\w-]+)", self.css))
		self.assertEqual(sorted(v for v in read if not v.startswith("--sf-")), ["--pp-color", "--pp-p6c-c"])

	def test_the_data_colors_go_in_as_the_custom_property_and_the_css_paints_them(self):
		# A person's dot (Resources available, the heatmap), a crew badge (a card, a quick look's
		# task row), a task card (calendar, crew cell), a Color-by swatch and a timeline bar.
		self.assertGreaterEqual(len(DATA_COLOR.findall(self.js)), 7)
		# A person's dot, a crew cell's card and a calendar card.
		self.assertEqual(len(re.findall(r'style="--pp-color: \$\{pp_esc\(color\)\}">', self.js)), 3)
		self.assertIn("color = `--pp-color: ${pp_esc(pp_color(card.color, PP.default_color))};`;", self.js)
		self.assertIn('classes.push("pp-tl-colored");', self.js)
		for selector in (".pp-dot", ".pp-badge", ".pp-card", ".pp-p6c-swatch", ".pk-tl-bar.pp-tl-colored"):
			with self.subTest(selector=selector):
				self.assertIn("var(--pp-color", self._rule(selector))
		self.assertIn("var(--pp-p6c-c)", self._rule(".pp-card.pp-p6c-colored"))
		self.assertIn('el.style.setProperty("--pp-p6c-c", color);', self.js)

	def test_the_route_map_reads_the_tokens_the_stop_list_paints(self):
		"""The map's markers and line take a color value, not a variable, so the script reads the
		same tokens through getComputedStyle; the list's numbers are CSS on those tokens."""
		helper = re.search(r"const PP_ROUTE_COLORS = \(\) => \(\{(.*?)\}\);", self.js, flags=re.S)
		self.assertIsNotNone(helper)
		found = dict(
			re.findall(r"(\w+): pp_token_color\(\"(--sf-[a-z0-9-]+)\", \"#[0-9a-f]{6}\"\)", helper.group(1))
		)
		self.assertEqual(sorted(found), ["line", "ring", "shop", "stop"])
		self.assertIn(f"var({found['stop']})", self._rule(".pp-stop-num"))
		self.assertIn(f"var({found['shop']})", self._rule(".pp-stop-num.pp-shop"))
		self.assertIn("getComputedStyle(document.documentElement).getPropertyValue(name).trim()", self.js)
		for use in (
			"color: colors.ring",
			"strokeColor: colors.ring",
			'"S", colors.shop',
			"colors.stop,",
			"PP_ROUTE_COLORS().line",
		):
			with self.subTest(use=use):
				self.assertIn(use, self.js)

	def test_no_other_color_value_is_left_in_the_script(self):
		"""What remains is data: the color of a task with none of its own and Color by's "nobody"
		grey (both the same kind of value as a record's color), a person's hashed hsl(), and the
		token reads' fallbacks."""
		rest = re.sub(r'pp_token_color\("--sf-[a-z0-9-]+", "#[0-9a-f]{6}"\)', "", self.js)
		for allowed in (
			'default_color: "#2563eb",',
			'none_color: "#94a3b8",',
			"return `hsl(${hash},55%,42%)`;",
		):
			self.assertIn(allowed, rest)
			rest = rest.replace(allowed, "")
		self.assertNotRegex(rest, r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])")
		self.assertNotRegex(rest, r"\b(?:rgba?|hsla?)\(")

	def test_warn_text_is_the_mix_never_yellow(self):
		"""Plain yellow is 1.7:1 on white: warn as text, or as a thin ring, is mixed with
		--sf-text-strong."""
		mix = "color-mix(in srgb, var(--sf-warn) 50%, var(--sf-text-strong))"
		checked = 0
		for prop, value in _declarations(self.css):
			if prop in ("color", "outline") and "--sf-warn" in value:
				checked += 1
				with self.subTest(prop=prop, value=value):
					self.assertIn(mix, value)
		self.assertGreaterEqual(checked, 5)

	def test_focused_fields_keep_their_own_rule(self):
		"""Bootstrap's .form-control:focus (0,2,0) outranks a plain field rule; the toolbar's fields
		and Build out's grid answer it with a focused rule of their own."""
		for selector in (".pp-wrap .pp-toolbar .form-control:focus", ".t1-grid .form-control:focus"):
			with self.subTest(selector=selector):
				self.assertRegex(self.css, re.escape(selector) + r"\s*\{\s*border-color: var\(--sf-focus\);")
		for selector in (".pp-wrap .pp-toolbar .form-control", ".t1-grid .form-control"):
			with self.subTest(selector=selector):
				body = self._rule(selector)
				self.assertIn("background-color: var(--sf-field-bg)", body)
				self.assertIn("color: var(--sf-field-text)", body)
		phone = self.css[self.css.index("@media (max-width: 767px)") :]
		self.assertRegex(
			phone, r"\.pp-wrap \.pp-toolbar \.form-control \{\s*font-size: var\(--sf-text-input\);"
		)

	def test_touch_targets_are_44px(self):
		coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", self.css, flags=re.S)
		self.assertIsNotNone(coarse, "no (pointer: coarse) block")
		heights = re.findall(r"min-(?:height|width):\s*([^;]+);", coarse.group(1))
		self.assertGreaterEqual(heights.count("var(--sf-touch-min)"), 8)
		# 64px is the move bar's button width; 32px a month day number's (a tall, narrow target).
		self.assertEqual([h for h in heights if h not in ("var(--sf-touch-min)", "64px", "32px")], [])
		for selector in (
			".pp-toolbar .btn",
			".pp-seg button",
			".pp-week .pp-card",
			".pp-day-num",
			".pp-p6c-sday",
		):
			with self.subTest(selector=selector):
				self.assertIn(selector, coarse.group(1))

	def test_the_sticky_headings_stop_on_a_phone(self):
		"""They were a min-width: 761px block. They are written for every width now and switched
		off at the one phone breakpoint, so a phone still scrolls the page as before."""
		self.assertNotIn("min-width: 761px", self.css)
		self.assertIn("position: sticky", self._rule(".pp-crew-view .pp-crew-head"))
		phones = re.findall(r"@media \(max-width: 767px\) \{(.*?)\n\}", self.css, flags=re.S)
		reset = [block for block in phones if ".pp-crew-view .pp-crew-head" in block]
		self.assertEqual(len(reset), 1)
		self.assertIn("position: static", reset[0])
		self.assertIn("max-height: none", reset[0])

	def test_the_s3_6_focus_ring_moved_with_the_css(self):
		self.assertRegex(
			self.css,
			r"\.pp-day-num:focus-visible,\s*\.pp-crew-head:focus-visible,\s*\.pp-title:focus-visible\s*"
			r"\{\s*outline: 2px solid var\(--sf-focus\);",
		)

	def test_a_highlight_leaves_the_outline_to_state(self):
		"""The highlight (0,3,0) used to be an outline, which outranked the selected, moving and
		drafted rings and the drop target, so on a highlighted card none of them showed."""
		body = self._rule(".pp-p6c-on .pp-card.pp-p6c-hi")
		self.assertNotIn("outline", body)
		self.assertIn("color-mix(in srgb, var(--sf-focus)", body)

	def test_keyboard_focus_on_a_card_is_never_hidden_by_its_state(self):
		"""A card's state is its outline; keyboard focus is a ring on ::after, so a selected or
		drafted card shows both, whatever the rules' order and specificity. No card rule puts focus
		on the outline, and nothing else draws a card's ::after."""
		for selector in (
			".pp-card.p6b-selected",
			".pp-card.pp-p6c-moving",
			".pp-card.pp-drafted",
			".pp-card.pp-over",
		):
			with self.subTest(selector=selector):
				self.assertIn("outline:", self._rule(selector))
		ring = self._rule(".pp-card:focus-visible::after")
		self.assertIn('content: ""', ring)
		self.assertIn("border: 2px solid var(--sf-focus)", ring)
		self.assertIn("pointer-events: none", ring)
		self.assertIn("position: relative", self._rule(".pp-card"))
		for prelude, body in re.findall(r"([^{}]+)\{([^{}]*)\}", self.css):
			if ".pp-card" not in prelude:
				continue
			for selector in prelude.split(","):
				selector = selector.strip()
				if ":focus-visible" in selector and "::after" not in selector:
					with self.subTest(selector=selector):
						self.assertNotIn("outline", body)
				if selector.endswith("::after") and ".p6b-grip" not in selector:
					with self.subTest(selector=selector):
						self.assertEqual(selector, ".pp-card:focus-visible::after")

	def test_a_month_day_number_is_a_tall_narrow_target(self):
		"""A month cell is about 53px wide on a phone; its number keeps 44px of height, not width."""
		coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", self.css, flags=re.S).group(1)
		month = re.search(r"\.pp-month \.pp-day-num \{([^}]*)\}", coarse)
		self.assertIsNotNone(month)
		self.assertIn("min-width: 32px", month.group(1))
		self.assertLess(coarse.index(".pp-day-num {"), coarse.index(".pp-month .pp-day-num {"))

	def test_a_month_cell_says_its_load(self):
		"""The month's one-line cells were a tint and nothing else; the words are read aloud now."""
		self.assertIn('state.text ? `<span class="pp-sr">${pp_esc(state.text)}</span>` : ""', self.js)
		self.assertIn("clip: rect(0, 0, 0, 0)", self._rule(".pp-sr"))

	def test_every_rule_styles_something_the_page_draws(self):
		"""A rule for a class nothing draws any more is a rename on one side only. pk- classes are
		planner_kit's (the day notes, a block, a timeline bar), which the page draws through it."""
		drawn = set(re.findall(r"(?<![\w-])((?:pp|p6b|p6e|t1|t4)-[a-z0-9_]+(?:-[a-z0-9_]+)*)", self.raw_js))
		styled = set(re.findall(r"\.((?:pp|p6b|p6e|t1|t4)-[a-z0-9_]+(?:-[a-z0-9_]+)*)", self.css))
		self.assertTrue(styled)
		self.assertEqual(sorted(styled - drawn), [], "styled, drawn by nothing")

	def test_its_short_prefixes_are_its_own(self):
		"""t1- and t4- are short enough for another page to pick; this page's CSS is global for the
		session, so nothing else may style them (the Maintenance Planner's are mp-p6b- and so on)."""
		selector = re.compile(r"(?<![\w-])\.(?:pp|p6b|p6e|t1|t4)-[a-z]")
		found = []
		for path in APP.rglob("*"):
			if path.suffix not in (".css", ".scss", ".js", ".html") or "tests" in path.parts:
				continue
			if path.parent == self.FOLDER or "node_modules" in path.parts:
				continue
			text = _read(path)
			code = _css_code(text) if path.suffix in (".css", ".scss") else _js_code(text)
			if selector.search(code):
				found.append(str(path.relative_to(APP)))
		self.assertEqual(found, [])


class TestThisSuiteRunsInCi(unittest.TestCase):
	def test_registered(self):
		registered = "python -m unittest erpnext_enhancements.tests.test_s3_page_css" in _read(CI)
		self.assertTrue(registered, "add a `python -m unittest` step for this suite to ci.yml")


if __name__ == "__main__":
	unittest.main()
