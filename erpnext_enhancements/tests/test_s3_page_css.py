"""Custom pages S3.4a (v1.643.0, TASK-2026-02823): page CSS out of JS, onto the tokens.

Five Desk pages used to style themselves from JS: a ``<style>`` block in a markup string, a
``QW_STYLE`` constant appended to the page, or inline ``style="color: ..."`` attributes. Their
CSS is now each page's own ``<page>.css``, which Frappe's page loader reads beside the page's
JS and sends with the page itself (``Page.load_assets`` in ``frappe/core/doctype/page/page.py``
on ``version-16``, through ``desk_page.getpage``), so an edit is never served stale from the
one-year immutable ``/assets`` cache. And the colours are the app's ``--sf-*`` tokens
(``docs/app-design-system.md``), which follow the Desk's light or dark theme.

This fails the build if any of these pages:

* writes a ``<style>`` element from JS again, or paints a colour through an inline ``style``;
* loses its ``.css`` file;
* puts a literal colour (hex, ``rgb()``, ``hsl()``) or a Frappe palette variable
  (``--red-500`` and the like) into its CSS, where the status tokens belong;
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

# Frappe's own colour scale. Theme-aware, but not the app's status colours: a page that reads
# --red-500 for "failed" says something different from every page that reads --sf-danger.
FRAPPE_PALETTE = re.compile(r"var\(\s*--(?:red|green|yellow|orange|gray|grey|blue|purple|pink|cyan)-\d")


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
