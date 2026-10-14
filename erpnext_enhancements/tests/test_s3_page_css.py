"""Custom pages S3.4a (v1.646.0, TASK-2026-02823): page CSS out of JS, onto the tokens.

Five Desk pages used to style themselves from JS: a ``<style>`` block in a markup string, a
``QW_STYLE`` constant appended to the page, or inline ``style="color: ..."`` attributes. Their
CSS is now each page's own ``<page>.css``, which Frappe's page loader reads beside the page's
JS and sends with the page itself (``Page.load_assets`` in ``frappe/core/doctype/page/page.py``
on ``version-16``, through ``desk_page.getpage``), so an edit is never served stale from the
one-year immutable ``/assets`` cache. And the colours are the app's ``--sf-*`` tokens
(``docs/app-design-system.md``), which follow the Desk's light or dark theme.

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
  ``--sf-radius-*`` token, or a width breakpoint other than the one phone breakpoint, 767px.

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
				self.assertEqual(set(widths), {"767px"})


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


class TestThisSuiteRunsInCi(unittest.TestCase):
	def test_registered(self):
		registered = "python -m unittest erpnext_enhancements.tests.test_s3_page_css" in _read(CI)
		self.assertTrue(registered, "add a `python -m unittest` step for this suite to ci.yml")


if __name__ == "__main__":
	unittest.main()
