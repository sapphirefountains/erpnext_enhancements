"""Custom pages S3.5a (v1.659.0, TASK-2026-02824): the /review and /itinerary staff web apps on the
app design tokens.

Both are website pages (``www/``), not Desk pages, so neither gets ``sf_tokens.bundle.css``: that
file is ``app_include_css``, which only the Desk loads, and a website page gets only
``web_include_css``. Adding the tokens there would ship them to every public page (/pay,
/contract-sign, the login page), so each app's template links them itself, through
``bundled_asset`` (content-hashed: an edit is never served stale from the year-long ``/assets``
cache), **before** its own stylesheet.

This fails the build if either app:

* stops linking the tokens through ``bundled_asset``, links them after its own stylesheet, or if
  ``web_include_css`` starts carrying them;
* puts a literal colour (hex, ``rgb()``, ``hsl()``, a named colour) or a Frappe palette variable
  into its CSS or the template's inline ``<style>``, apart from the documented exceptions below;
* reads a ``var(--sf-...)`` that ``sf_tokens.bundle.css`` does not define, or one of its own
  ``--rr-*`` / ``--ti-*`` names that it never declares (either resolves to nothing, silently);
* declares a custom property outside its own prefix, or anywhere but ``:root`` (the one place the
  app's own vocabulary is mapped onto the tokens);
* uses a shadow, a radius that is not an ``--sf-radius-*`` token, or a width breakpoint other
  than the phone one (767px) and the layout ones documented here;
* paints text in a status fill (``--sf-warn``, ``--sf-good``, ``--sf-danger``) itself: the warn
  fill is 1.7:1 on white, and the guide's rule is a half-and-half mix with the strongest text;
* edges something in ``--sf-pillar-neutral`` (1.06:1 on a dark card) or fills one with
  ``--sf-pillar-build-deep`` (the dark card's own colour);
* keeps its own dark palette instead of the tokens', or switches dark mode on an attribute the
  tokens cannot see (the Review Room's THEME button wrote ``data-rr-theme`` until v1.659.0);
* shows a verdict or a noted part by colour alone;
* lets the itinerary's service worker precache its stylesheet but not the tokens' (the offline
  itinerary would have no colours).

Bench-free: reads files only, no frappe stub.
Run: python -m unittest erpnext_enhancements.tests.test_www_app_tokens
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
TOKENS = APP / "public" / "css" / "sf_tokens.bundle.css"
HOOKS = APP / "hooks.py"
CI = REPO / ".github" / "workflows" / "ci.yml"
TOKENS_LINK = "<link rel=\"stylesheet\" href=\"{{ bundled_asset('sf_tokens.bundle.css') }}\">"

APPS = {
	"review": {
		"template": APP / "www" / "review.html",
		"css": APP / "public" / "css" / "design_review.bundle.css",
		"own_link": "<link rel=\"stylesheet\" href=\"{{ bundled_asset('design_review.bundle.css') }}\">",
		"prefix": "--rr-",
		# Layout breakpoints besides the phone one: the option names drop out of the bar tabs at
		# 1599px and its subtitle at 1299px, and at 900px the 248px rail and 340px notes panel
		# float over the stage (a portrait tablet leaves it too little room beside them).
		"extra_breakpoints": {"1599px", "1299px", "900px"},
		"needs_phone_breakpoint": True,
	},
	"itinerary": {
		"template": APP / "www" / "itinerary.html",
		"css": APP / "public" / "css" / "travel" / "itinerary.css",
		"own_link": (
			'<link rel="stylesheet" href="/assets/erpnext_enhancements/css/travel/itinerary.css'
			'?v={{ deploy_version }}">'
		),
		"prefix": "--ti-",
		"extra_breakpoints": set(),
		# Mobile-first in a 640px column: one layout at every width, so no breakpoint at all.
		"needs_phone_breakpoint": False,
	},
}

# Literal colours an app's CSS may keep, by app, each with its reason. None today: the concept
# frames' outlines in design_review/frames.js are literal, but they live in a sandboxed frame
# document no stylesheet reaches (TestTheFrameColoursAreTokensByValue holds them to the palette).
EXCEPTIONS = {"review": {}, "itinerary": {}}

# Frappe's own colour scale and status tints (the same pattern test_s3_page_css uses).
FRAPPE_PALETTE = re.compile(
	r"var\(\s*--(?:"
	r"(?:red|green|yellow|orange|gray|grey|blue|purple|pink|cyan)-\d"
	r"|text-on-[\w-]+"
	r"|bg-(?!color\b)[\w-]+"
	r")"
)

NAMED_COLOURS = re.compile(
	r"(?<![\w-])(?:white|black|red|green|blue|yellow|orange|gray|grey|purple|pink|silver|navy"
	r"|teal|maroon|olive|lime|aqua|fuchsia|cyan|magenta|brown|gold|indigo|violet|crimson"
	r"|tomato|salmon|coral|khaki|beige|ivory|lavender|tan|turquoise|whitesmoke|gainsboro)"
	r"(?![\w-])",
	re.I,
)

STATUS_FILLS = ("--sf-warn", "--sf-good", "--sf-danger")


def _read(path):
	return path.read_text(encoding="utf-8")


def _css_code(text):
	return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _js_code(text):
	text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
	return re.sub(r"(^|\s)//[^\n]*", r"\1", text)


def _rules(css):
	"""(selector prelude, body) for every innermost rule, @media contents included."""
	return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css)]


def _declarations(body):
	out = []
	for decl in body.split(";"):
		if ":" in decl:
			prop, value = decl.split(":", 1)
			out.append((prop.strip().lower(), value.strip()))
	return out


def _all_declarations(css):
	return [d for _sel, body in _rules(css) for d in _declarations(body)]


def _defined_tokens():
	return set(re.findall(r"(--sf-[a-z0-9-]+)\s*:", _css_code(_read(TOKENS))))


def _head(template):
	start = template.index("{% block head_include %}")
	return template[start : template.index("{% endblock %}", start)]


def _aliases(css, prefix):
	"""The app's own names that are plain aliases of one other variable: --rr-pin -> --sf-warn."""
	out = {}
	for sel, body in _rules(css):
		if sel != ":root":
			continue
		for prop, value in _declarations(body):
			m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", value)
			if prop.startswith(prefix) and m:
				out[prop] = m.group(1)
	return out


def _resolve(value, aliases):
	for _ in range(5):
		new = re.sub(r"var\(\s*(--[\w-]+)\s*\)", lambda m: f"var({aliases.get(m.group(1), m.group(1))})", value)
		if new == value:
			break
		value = new
	return value


class TestTheTemplatesLinkTheTokens(unittest.TestCase):
	def test_each_template_links_the_tokens_before_its_own_stylesheet(self):
		for name, app in APPS.items():
			head = _head(_read(app["template"]))
			with self.subTest(app=name):
				self.assertEqual(head.count(TOKENS_LINK), 1, "link sf_tokens.bundle.css through bundled_asset")
				self.assertIn(app["own_link"], head)
				self.assertLess(head.index(TOKENS_LINK), head.index(app["own_link"]))

	def test_no_template_names_the_tokens_by_a_raw_path(self):
		"""A raw /assets path is cached for a year with no content hash."""
		for name, app in APPS.items():
			with self.subTest(app=name):
				self.assertNotIn("/assets/erpnext_enhancements/css/sf_tokens", _read(app["template"]))

	def test_the_tokens_are_not_a_website_wide_include(self):
		hooks = _read(HOOKS)
		for hook in ("web_include_css", "web_include_js"):
			m = re.search(rf"^{hook}\s*=\s*(\[[^\]]*\]|\"[^\"]*\")", hooks, flags=re.M)
			self.assertIsNotNone(m, hook)
			with self.subTest(hook=hook):
				self.assertNotIn("sf_tokens", m.group(1))

	def test_inline_styles_hold_no_literal_colour(self):
		for name, app in APPS.items():
			for block in re.findall(r"<style>(.*?)</style>", _read(app["template"]), flags=re.S):
				with self.subTest(app=name):
					self.assertNotRegex(block, r"#[0-9a-fA-F]{3,8}\b")
					self.assertNotRegex(block, r"(?i)\b(?:rgba?|hsla?)\(")


class TestTheCssIsOnTheTokens(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.raw = {name: _read(app["css"]) for name, app in APPS.items()}
		cls.css = {name: _css_code(raw) for name, raw in cls.raw.items()}
		cls.tokens = _defined_tokens()

	def test_the_token_file_was_read(self):
		self.assertIn("--sf-warn", self.tokens)
		self.assertIn("--sf-touch-min", self.tokens)

	def test_no_literal_colours(self):
		for name, css in self.css.items():
			allowed = EXCEPTIONS[name]
			for sel, body in _rules(css):
				for literal in re.findall(r"#[0-9a-fA-F]{3,8}\b|(?i:\b(?:rgba?|hsla?)\([^)]*\))", body):
					with self.subTest(app=name, rule=sel[:50], literal=literal):
						self.assertIn(literal, allowed, "a literal colour; read a --sf-* token instead")
				with self.subTest(app=name, rule=sel[:50]):
					self.assertNotRegex(body, FRAPPE_PALETTE)

	def test_no_named_colours(self):
		for name, css in self.css.items():
			for prop, value in _all_declarations(css):
				if prop == "content":
					continue
				with self.subTest(app=name, declaration=f"{prop}: {value}"):
					self.assertNotRegex(value, NAMED_COLOURS)

	def test_the_patterns_catch_what_they_name(self):
		for value in ("white", "1px solid Red", "var(--bg-orange)", "var(--text-on-red)"):
			with self.subTest(value=value):
				self.assertTrue(NAMED_COLOURS.search(value) or FRAPPE_PALETTE.search(value))
		for value in ("var(--sf-white)", "transparent", "currentColor", "var(--sf-violet-deep)"):
			with self.subTest(value=value):
				self.assertFalse(NAMED_COLOURS.search(value) or FRAPPE_PALETTE.search(value))

	def test_every_sf_token_read_is_defined(self):
		for name, css in self.css.items():
			used = set(re.findall(r"var\(\s*(--sf-[a-z0-9-]+)", css))
			with self.subTest(app=name):
				self.assertGreater(len(used), 20, f"{name} reads almost no --sf-* token")
				self.assertEqual(sorted(used - self.tokens), [], "not defined in sf_tokens.bundle.css")

	def test_its_own_names_are_declared_in_one_place(self):
		for name, css in self.css.items():
			prefix = APPS[name]["prefix"]
			declared = set()
			for sel, body in _rules(css):
				for prop, _value in _declarations(body):
					if not prop.startswith("--"):
						continue
					with self.subTest(app=name, prop=prop):
						self.assertTrue(prop.startswith(prefix), f"own properties are {prefix}*")
						self.assertEqual(sel, ":root", "declare the app's own names on :root only")
					declared.add(prop)
			read = set(re.findall(r"var\(\s*(" + re.escape(prefix) + r"[\w-]+)", css))
			with self.subTest(app=name):
				self.assertEqual(sorted(read - declared), [], "read but never declared")
				self.assertEqual(sorted(declared - read), [], "declared but never read")

	def test_no_shadows(self):
		for name, css in self.css.items():
			with self.subTest(app=name):
				self.assertNotIn("shadow", css)

	def test_every_radius_is_a_token(self):
		for name, css in self.css.items():
			for prop, value in _all_declarations(css):
				if not prop.startswith("border") or "radius" not in prop:
					continue
				with self.subTest(app=name, value=value):
					for part in re.findall(r"var\([^)]*\)|\S+", value):
						self.assertTrue(part == "0" or part.startswith("var(--sf-radius-"), f"{prop}: {value}")

	def test_the_breakpoints(self):
		for name, css in self.css.items():
			app = APPS[name]
			widths = set(re.findall(r"@media[^{]*\((?:max|min)-width\s*:\s*([^)]+)\)", css))
			with self.subTest(app=name):
				if app["needs_phone_breakpoint"]:
					self.assertIn("767px", widths)
				self.assertLessEqual(widths, {"767px"} | app["extra_breakpoints"])

	def test_no_status_fill_is_text(self):
		"""Warn text is color-mix(in srgb, var(--sf-warn) 50%, <the strongest text>), never the fill:
		yellow is 1.7:1 on white. The same holds for good and danger, read through any alias."""
		for name, css in self.css.items():
			aliases = _aliases(css, APPS[name]["prefix"])
			for prop, value in _all_declarations(css):
				if prop != "color":
					continue
				resolved = _resolve(value, aliases).replace(" ", "")
				with self.subTest(app=name, declaration=f"{prop}: {value}"):
					for fill in STATUS_FILLS:
						self.assertNotEqual(resolved, f"var({fill})", "a status fill used as text")

	def test_status_text_is_the_half_and_half_mix(self):
		"""Where a status colour is text, it is mixed 50% with the strongest text of its ground."""
		seen = 0
		for name, css in self.css.items():
			aliases = _aliases(css, APPS[name]["prefix"])
			declared = {
				prop: value for sel, body in _rules(css) if sel == ":root" for prop, value in _declarations(body)
			}
			for prop, value in _all_declarations(css):
				if prop != "color":
					continue
				resolved = _resolve(value, aliases)
				# One level of the app's own non-alias names (--ti-offline-text: color-mix(...)).
				resolved = re.sub(r"var\(\s*(--(?:rr|ti)-[\w-]+)\s*\)", lambda m: declared.get(m.group(1), m.group(0)), resolved)
				for fill in STATUS_FILLS:
					if f"var({fill})" not in resolved:
						continue
					seen += 1
					with self.subTest(app=name, declaration=f"{prop}: {value}"):
						self.assertIn(f"color-mix(in srgb, var({fill}) 50%, var(", resolved)
		self.assertGreater(seen, 3)

	def test_the_dark_mode_traps(self):
		"""Neutral's opening stop edges nothing (1.06:1 on a navy-800 card), and Build's closing
		stop fills nothing (it IS the navy-800 card). Chip edges come from --sf-chip-edge-*."""
		for name, css in self.css.items():
			with self.subTest(app=name):
				self.assertNotRegex(css, r"var\(\s*--sf-pillar-neutral\s*\)")
				self.assertNotRegex(css, r"var\(\s*--sf-pillar-build-deep\s*\)")

	def test_no_dark_palette_of_its_own(self):
		"""Dark values come from the tokens. A dark block here may set color-scheme and nothing else,
		and only on the attribute and media query the tokens themselves switch on."""
		for name, css in self.css.items():
			with self.subTest(app=name):
				self.assertNotIn("data-rr-theme", css)
			for sel, body in _rules(css):
				if "dark" not in sel:
					continue
				with self.subTest(app=name, rule=sel):
					self.assertIn(sel, (':root[data-theme="dark"]', ":root:not([data-theme])"))
					self.assertEqual([p for p, _v in _declarations(body)], ["color-scheme"])
			for media in re.findall(r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{(.*?\})\s*\}", css, flags=re.S):
				for sel, body in _rules(media):
					with self.subTest(app=name, media_rule=sel):
						self.assertEqual(sel, ":root:not([data-theme])")
						self.assertEqual([p for p, _v in _declarations(body)], ["color-scheme"])


class TestTheCommentsAreWellFormed(unittest.TestCase):
	def test_no_comment_opens_inside_a_comment(self):
		for name, app in APPS.items():
			for body in re.findall(r"/\*(.*?)\*/", _read(app["css"]), flags=re.S):
				with self.subTest(app=name, comment=body.strip()[:60]):
					self.assertNotIn("/*", body)

	def test_no_comment_closes_twice(self):
		for name, app in APPS.items():
			with self.subTest(app=name):
				self.assertNotIn("*/", _css_code(_read(app["css"])))


class TestDarkModeIsTheTokens(unittest.TestCase):
	"""The tokens switch on <html data-theme="light|dark"> and follow the device without it."""

	def test_the_review_theme_button_writes_data_theme(self):
		js = _js_code(_read(APP / "public" / "js" / "design_review" / "app.js"))
		start = js.index("applyTheme(mode) {")
		body = js[start : js.index("\n\t}", start)]
		self.assertIn('root.setAttribute("data-theme", mode)', body)
		self.assertIn('root.removeAttribute("data-theme")', body)
		self.assertNotIn('setAttribute("data-rr-theme"', js)

	def test_the_itinerary_writes_no_theme_attribute(self):
		"""So the tokens' own prefers-color-scheme block applies, as the old --ti-* one did."""
		js = _js_code(_read(APP / "public" / "js" / "travel" / "itinerary.js"))
		self.assertNotRegex(js, r"setAttribute\(\s*['\"]data-theme")
		self.assertNotRegex(js, r"dataset\.theme\s*=")


class TestNoStatusByColourAlone(unittest.TestCase):
	def test_a_verdict_and_a_noted_part_carry_their_word(self):
		js = _js_code(_read(APP / "public" / "js" / "design_review" / "app.js"))
		verdicts = re.findall(r"el\(\"span\", `rr-vd is-\$\{mine\.toLowerCase\(\)\}`(, mine)?\)", js)
		self.assertEqual(verdicts, [", mine", ", mine"])
		self.assertIn('el("i", "rr-dot2", "Noted")', js)


class TestTouchTargets(unittest.TestCase):
	def _min_height(self, css, selector):
		for sel, body in _rules(css):
			if selector in [s.strip() for s in sel.split(",")]:
				for prop, value in _declarations(body):
					if prop == "min-height":
						return value
		return None

	def test_the_review_phone_controls_are_44px(self):
		css = _css_code(_read(APPS["review"]["css"]))
		phone = re.search(r"@media \(max-width: 767px\) \{(.*?)\n\}", css, flags=re.S).group(1)
		for selector in (".rr-toggle", ".rr-tab", ".rr-btn", ".rr-btn.small", ".rr-srow", ".rr-vb", ".rr-fld"):
			with self.subTest(selector=selector):
				self.assertEqual(self._min_height(phone, selector), "var(--sf-touch-min)")
		self.assertIn("font-size: var(--sf-text-input)", phone)

	def test_the_itinerary_controls_are_44px(self):
		css = _css_code(_read(APPS["itinerary"]["css"]))
		for selector in (
			".ti-reload",
			".ti-all-chip",
			".ti-person-chip",
			".ti-map-btn",
			".ti-copy",
			".ti-maps-link",
			".ti-screen-tab",
			".ti-contacts-toggle",
			".ti-contact-link",
			".ti-print-link",
			".ti-list-more",
			".ti-viewer-close",
			".ti-viewer-original",
			".ti-footer-link",
		):
			value = self._min_height(css, selector)
			with self.subTest(selector=selector, value=value):
				self.assertIsNotNone(value)
				self.assertTrue(value == "var(--sf-touch-min)" or int(value.rstrip("px")) >= 44)


class TestTheOfflineItineraryKeepsTheTokens(unittest.TestCase):
	def test_the_worker_keeps_the_tokens_when_it_keeps_the_stylesheet(self):
		code = _js_code(_read(APP / "www" / "itinerary-sw.js"))
		block = code[code.index("const PRECACHE = [") : code.index("];", code.index("const PRECACHE = ["))]
		if ".css'" not in block:
			self.skipTest("the worker precaches no stylesheet")
		self.assertIn("sf_tokens", code)
		install = code[code.index("self.addEventListener('install'") :]
		self.assertIn("if (TOKENS) assets.push(", install[: install.index("self.addEventListener(", 10)])


class TestTheFrameColoursAreTokensByValue(unittest.TestCase):
	"""A concept frame is its own sandboxed document (CSP default-src 'none'), so the tokens cannot
	reach the outlines frames.js inserts into it. They stay literal, and each is a palette value."""

	def test_every_frame_colour_is_a_palette_value(self):
		js = _read(APP / "public" / "js" / "design_review" / "frames.js")
		frame_css = js[js.index("const FRAME_CSS = `") : js.index("`;", js.index("const FRAME_CSS = `"))]
		palette = {"#f8f8f8", "#00a0df", "#ffb819", "#b14fc5"}
		self.assertLessEqual(set(re.findall(r"#[0-9a-fA-F]{6}\b", frame_css)), palette)
		for r, g, b in re.findall(r"rgba\((\d+),(\d+),(\d+),", frame_css):
			with self.subTest(rgb=(r, g, b)):
				self.assertIn("#{:02x}{:02x}{:02x}".format(int(r), int(g), int(b)), palette)
		tokens = _css_code(_read(TOKENS))
		for hex_ in palette:
			with self.subTest(hex=hex_):
				self.assertIn(hex_, tokens)


class TestThisSuiteRunsInCi(unittest.TestCase):
	def test_registered(self):
		registered = "python -m unittest erpnext_enhancements.tests.test_www_app_tokens" in _read(CI)
		self.assertTrue(registered, "add a `python -m unittest` step for this suite to ci.yml")


if __name__ == "__main__":
	unittest.main()
