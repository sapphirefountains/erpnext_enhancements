# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free guards for sf_kit, the shared page pieces (`public/js/sf_kit/`,
`public/js/sf_kit.bundle.js`, `public/css/sf_kit.bundle.css`).

Every custom Desk page and Custom HTML Block is to draw its loading / empty / error panel, its
chips, cards, buttons, toasts and status tiles from this kit, and form kit P4 builds on it. So a
mistake here is repeated on every screen, and most of the mistakes it could make look fine on
the one screen somebody checks:

* **a literal colour** reads in one theme and vanishes in the other. Every colour is a `--sf-*`
  token (or transparent / currentColor), and every token the kit reads must exist in
  `sf_tokens.bundle.css`, or the declaration silently drops to the browser default;
* **a shadow, a rounded card, a stray media query** is a different brand. The rules of
  docs/app-design-system.md, "Space and shape" and "Phones", are checked here;
* **a selector outside `.sf-`** would restyle the Desk on every page that loads the kit;
* **the CSS has two copies**, the bundle a page loads and the text a block adopts into its
  shadow root (`sf_kit/styles.js`). They must be identical, character for character;
* **a global include**: the kit is loaded only by the pages that use it, with
  `frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"])`, never `app_include_*`.

The behaviour (escaping, call()'s settling, the state panel, chips, tiles, the toast and its
byte-for-byte planner markup, the bundle's install) is executed by `scripts/test_sf_kit.mjs`,
which this runs when node is on PATH, and CI also runs as its own step.

Run: python -m unittest erpnext_enhancements.tests.test_sf_kit
"""

import ast
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "erpnext_enhancements"
PUBLIC = APP / "public"
KIT_DIR = PUBLIC / "js" / "sf_kit"
BUNDLE = PUBLIC / "js" / "sf_kit.bundle.js"
CSS_FILE = PUBLIC / "css" / "sf_kit.bundle.css"
STYLES_JS = KIT_DIR / "styles.js"
TOKENS = PUBLIC / "css" / "sf_tokens.bundle.css"
HOOKS = APP / "hooks.py"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
HARNESS = REPO_ROOT / "scripts" / "test_sf_kit.mjs"
GUIDE = REPO_ROOT / "docs" / "app-design-system.md"
PUBLIC_README = PUBLIC / "README.md"
PLANNER_ESCAPE = PUBLIC / "js" / "planner_kit" / "escape.js"
PLANNER_TOAST = PUBLIC / "js" / "planner_kit" / "toast.js"

CSS_RAW = CSS_FILE.read_text(encoding="utf-8")
CSS = re.sub(r"/\*.*?\*/", "", CSS_RAW, flags=re.S)

MODULES = ("esc", "t", "call", "state", "chip", "card", "tiles", "toast", "styles", "index")

# The two media queries docs/app-design-system.md allows a screen: the phone width (written as a
# number, since CSS cannot read --sf-phone-max in a query) and reduced motion.
ALLOWED_MEDIA = {"(max-width: 767px)", "(prefers-reduced-motion: reduce)"}

# Properties that can carry a colour. Every identifier left in their value once the --sf-*
# tokens are taken out must be one of ALLOWED_WORDS: that refuses named colours (red, white,
# navy ...) without needing the full list of 148.
COLOUR_PROPS = re.compile(
	r"^(color|background(-color)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?"
	r"|fill|stroke|text-decoration(-color)?|caret-color|accent-color|column-rule(-color)?)$"
)
ALLOWED_WORDS = {"solid", "none", "transparent", "currentColor", "currentcolor", "inherit", "underline"}

# A last net for colour literals anywhere, whatever the property.
COLOUR_FUNCTIONS = re.compile(r"\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(", re.I)


def _declarations(css):
	"""(selector, property, value) for every declaration outside @keyframes."""
	css = re.sub(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", css)
	out = []
	for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
		for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body):
			out.append((selector.strip(), prop.strip(), value.strip()))
	return out


def _strip_js_comments(text):
	text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
	return re.sub(r"(^|[^:\"'`])//[^\n]*", r"\1", text)


def _styles_js_css():
	text = STYLES_JS.read_text(encoding="utf-8")
	start = text.index("export const CSS = `") + len("export const CSS = `")
	end = text.rindex("`;")
	return text[start:end]


class TestTheStylesheet(unittest.TestCase):
	def test_no_colour_literal(self):
		self.assertNotRegex(CSS, r"#[0-9a-fA-F]{3,8}\b", "a hex colour; use a --sf-* token")
		self.assertNotRegex(CSS, COLOUR_FUNCTIONS, "a colour function; use a --sf-* token")
		for selector, prop, value in _declarations(CSS):
			if not COLOUR_PROPS.match(prop):
				continue
			bare = re.sub(r"var\(--sf-[\w-]+\)", " ", value)
			bare = re.sub(r"color-mix\(\s*in\s+srgb\s*,", " ", bare)
			words = set(re.findall(r"(?<![\w-])[a-zA-Z][\w-]*", bare))
			with self.subTest(selector=selector, prop=prop):
				self.assertLessEqual(words, ALLOWED_WORDS, f"{prop}: {value}")

	def test_every_colour_property_reads_a_token_or_a_keyword(self):
		for selector, prop, value in _declarations(CSS):
			if prop in ("color", "background", "background-color", "border-color", "outline-color"):
				with self.subTest(selector=selector, prop=prop):
					self.assertRegex(value, r"var\(--sf-|^(transparent|inherit|currentColor)$|color-mix\(")

	def test_no_shadow(self):
		self.assertNotIn("shadow", CSS.lower(), "the brand has no shadows in its UI")

	def test_only_the_two_media_queries(self):
		queries = {re.sub(r"\s+", " ", q.strip()) for q in re.findall(r"@media\s*([^{]+)\{", CSS)}
		self.assertEqual(queries, ALLOWED_MEDIA)
		self.assertEqual(set(re.findall(r"@([\w-]+)", CSS)), {"media", "keyframes"})

	def test_radii_come_only_from_the_radius_tokens(self):
		radii = [(s, v) for s, p, v in _declarations(CSS) if re.fullmatch(r"border(-\w+)*-radius", p)]
		self.assertTrue(radii)
		for selector, value in radii:
			with self.subTest(selector):
				self.assertRegex(value, r"^var\(--sf-radius-[\w-]+\)$")
		# Structure is square, controls round, chips are pills: keep the three groups apart.
		by_selector = {s: v for s, v in radii}
		self.assertEqual(by_selector[".sf-card"], "var(--sf-radius-none)")
		self.assertEqual(by_selector[".sf-btn"], "var(--sf-radius-control)")
		self.assertEqual(by_selector[".sf-chip"], "var(--sf-radius-pill)")

	def test_every_selector_is_an_sf_class(self):
		body = re.sub(r"@keyframes\s+[\w-]+\s*\{(?:[^{}]*\{[^{}]*\})*\s*\}", "", CSS)
		body = re.sub(r"@media[^{]+\{", "", body)
		selectors = [s.strip() for group in re.findall(r"([^{}]+)\{", body) for s in group.split(",")]
		self.assertTrue(selectors)
		for selector in selectors:
			with self.subTest(selector):
				self.assertTrue(selector.startswith(".sf-"), "would style something outside the kit")
				for cls in re.findall(r"\.([a-zA-Z_][\w-]*)", selector):
					self.assertTrue(cls.startswith("sf-"), cls)
		for name in re.findall(r"@keyframes\s+([\w-]+)", CSS):
			self.assertTrue(name.startswith("sf-"), name)

	def test_the_font_is_the_brand_body_face(self):
		fonts = {v for s, p, v in _declarations(CSS) if p == "font-family"}
		self.assertLessEqual(fonts, {"var(--sf-font-sans)", "inherit"})
		self.assertNotIn("--sf-font-display", CSS, "the display face sets titles only, never kit parts")
		self.assertNotIn("text-transform: uppercase", CSS)

	def test_every_token_the_kit_reads_exists(self):
		tokens = TOKENS.read_text(encoding="utf-8")
		defined = set(re.findall(r"(--sf-[\w-]+)\s*:", tokens))
		read = set(re.findall(r"var\((--sf-[\w-]+)", CSS))
		self.assertTrue(read)
		self.assertEqual(read - defined, set(), "read but never defined: the rule would silently drop")

	def test_the_chip_and_button_spec(self):
		decls = {(s, p): v for s, p, v in _declarations(CSS)}
		for pillar in ("design", "build", "service", "rent"):
			with self.subTest(pillar):
				self.assertEqual(decls[(f".sf-chip--{pillar}", "background")], f"var(--sf-pillar-{pillar}-deep)")
				# The edge is the tone's chip-edge token (the opening stop in light, lighter in dark
				# where the opening stop sinks into the navy card): without it a Build chip vanishes
				# on a dark card. test_app_design_tokens measures it.
				self.assertEqual(decls[(f".sf-chip--{pillar}", "border-color")], f"var(--sf-chip-edge-{pillar})")
			self.assertEqual(
				decls[(f".sf-card--pillar-{pillar}", "border-top")],
				f"var(--sf-space-5) solid var(--sf-pillar-{pillar}-deep)",
			)
		self.assertEqual(decls[(".sf-chip", "border")], "var(--sf-border-hairline) solid var(--sf-chip-edge-neutral)")
		self.assertEqual(decls[(".sf-chip", "background")], "var(--sf-pillar-neutral-deep)")
		for status in ("danger", "warn", "good"):
			self.assertEqual(decls[(f".sf-chip--{status}", "background")], f"var(--sf-{status})")
			self.assertEqual(decls[(f".sf-chip--{status}", "color")], f"var(--sf-on-{status})")
			self.assertEqual(decls[(f".sf-chip--{status}", "border-color")], f"var(--sf-chip-edge-{status})")
		self.assertEqual(decls[(".sf-btn", "min-height")], "var(--sf-touch-min)")
		self.assertEqual(decls[(".sf-btn", "text-transform")], "none")
		self.assertEqual(decls[(".sf-btn--primary", "background")], "var(--sf-primary)")
		self.assertEqual(decls[(".sf-btn--primary", "color")], "var(--sf-on-primary)")
		self.assertEqual(decls[(".sf-btn--secondary", "border-color")], "var(--sf-text)")
		self.assertEqual(decls[(".sf-btn:focus-visible", "outline")], "var(--sf-border-control) solid var(--sf-focus)")
		self.assertEqual(decls[(".sf-tile-link:focus-visible", "outline")], "var(--sf-border-control) solid var(--sf-focus)")
		# The toast is inverted, so it shows on the dark page too.
		self.assertEqual(decls[(".sf-toast", "background")], "var(--sf-text-strong)")
		self.assertEqual(decls[(".sf-toast", "color")], "var(--sf-surface)")

	def test_link_colours_beat_the_desks_a_hover(self):
		"""Desk's `a:hover, a:focus` (0,1,1; color var(--text-color), plus bootstrap's underline)
		outranks one class, so an sf-btn or tile link written as an <a> lost its colour on hover
		(v1.632.0). Each needs a rule of at least (0,2,0) for hover AND focus that sets its colour,
		and the variants must come after `.sf-btn`'s own, which they override at equal weight."""
		rules = {}
		order = []
		for selector, prop, value in _declarations(CSS):
			for one in (s.strip() for s in selector.split(",")):
				rules.setdefault(one, {})[prop] = value
				if one not in order:
					order.append(one)
		expected = {
			".sf-btn": "var(--sf-text)",
			".sf-btn--primary": "var(--sf-on-primary)",
			".sf-btn--secondary": "var(--sf-text)",
			".sf-btn--quiet": "var(--sf-link)",
			".sf-tile-link": "var(--sf-link)",
		}
		for cls, colour in expected.items():
			for state in ("hover", "focus"):
				selector = f"{cls}:{state}"
				with self.subTest(selector):
					self.assertIn(selector, rules, f"no {selector} rule: Desk's a:{state} wins")
					self.assertEqual(rules[selector].get("color"), colour)
		for variant in (".sf-btn--primary", ".sf-btn--secondary", ".sf-btn--quiet"):
			self.assertGreater(order.index(f"{variant}:hover"), order.index(".sf-btn:hover"), variant)
		self.assertEqual(rules[".sf-btn:hover"].get("text-decoration"), "none")
		self.assertEqual(rules[".sf-btn--quiet:hover"].get("text-decoration"), "underline")
		# The plain-text stand-in shares .sf-tile-link and must not light up like a link.
		self.assertEqual(rules[".sf-tile-link--plain:hover"].get("color"), "var(--sf-text-muted)")
		self.assertGreater(order.index(".sf-tile-link--plain:hover"), order.index(".sf-tile-link:hover"))

	def test_the_spinner_stops_for_reduced_motion(self):
		block = CSS[CSS.index("@media (prefers-reduced-motion: reduce)") :]
		self.assertRegex(block, r"\.sf-state-spinner\s*\{\s*animation:\s*none;")


class TestTheTwoCopies(unittest.TestCase):
	def test_styles_js_is_the_css_file_character_for_character(self):
		self.assertEqual(
			_styles_js_css(),
			CSS_RAW,
			"sf_kit/styles.js differs from css/sf_kit.bundle.css: edit the .css, then paste the "
			"whole file between the backticks in styles.js",
		)

	def test_the_css_survives_a_template_literal(self):
		for needle in ("`", "${", "\\"):
			self.assertNotIn(needle, CSS_RAW, f"{needle!r} would change inside styles.js's template literal")


class TestLoading(unittest.TestCase):
	def test_the_files_exist_and_are_lf(self):
		for path in [*(KIT_DIR / f"{name}.js" for name in MODULES), BUNDLE, CSS_FILE, HARNESS]:
			with self.subTest(path.name):
				self.assertTrue(path.is_file(), path)
				self.assertNotIn("\r", path.read_text(encoding="utf-8"))

	def test_it_is_never_a_global_include(self):
		tree = ast.parse(HOOKS.read_text(encoding="utf-8"))
		found = {}
		for node in tree.body:
			if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
				name = node.targets[0].id
				if re.fullmatch(r"(app|web)_include_(js|css)", name):
					value = ast.literal_eval(node.value)
					found[name] = [value] if isinstance(value, str) else list(value)
		self.assertIn("app_include_js", found)
		self.assertIn("app_include_css", found)
		for name, values in found.items():
			for value in values:
				with self.subTest(name=name, value=value):
					self.assertNotIn("sf_kit", value)

	def test_the_bundle_injects_no_css_and_the_modules_touch_no_globals(self):
		bundle = _strip_js_comments(BUNDLE.read_text(encoding="utf-8"))
		self.assertIn('from "./sf_kit/index.js"', bundle)
		self.assertIn("window.sf_kit.version >= VERSION", bundle)
		for name in MODULES:
			code = _strip_js_comments((KIT_DIR / f"{name}.js").read_text(encoding="utf-8"))
			with self.subTest(name):
				self.assertNotRegex(code, r"\bwindow\b|\bdocument\b", "use env, so node can run it")
		for text in [bundle] + [(KIT_DIR / f"{n}.js").read_text(encoding="utf-8") for n in MODULES]:
			self.assertNotIn('createElement("style")', text, "CSS ships in sf_kit.bundle.css, not from JS")

	def test_the_kits_own_words_are_extractable(self):
		"""frappe v16's JS extractor (frappe/gettext/extractors/javascript.py) keeps a call only
		when the name before `(` is `__` and the first argument is a string literal, so a word
		the kit says through t("...") or env.t("...") never reaches the translation files."""
		for name in MODULES:
			if name == "t":
				continue
			code = _strip_js_comments((KIT_DIR / f"{name}.js").read_text(encoding="utf-8"))
			with self.subTest(name):
				self.assertNotRegex(code, r"(?<![\w.])t\(\s*[\"'`]", "a literal through t(): use __()")
				self.assertNotRegex(code, r"env\.t\(", "env.t(): use __()")
				if re.search(r"\b__\(", code):
					self.assertIn('import { __ } from "./t.js";', code)
		words = {
			"state": ("Loading…", "Something went wrong.", "You do not have access to this.", "Nothing here yet.", "Try again"),
			"tiles": ("Healthy", "Needs attention", "Failing"),
			"toast": ("Dismiss",),
			"call": (
				"Could not reach the server.",
				"You do not have permission to do that.",
				"The server could not finish this request. Please try again.",
				"The server refused this request.",
				"Your session has expired. Please sign in again.",
			),
		}
		for name, sentences in words.items():
			code = (KIT_DIR / f"{name}.js").read_text(encoding="utf-8")
			for sentence in sentences:
				with self.subTest(name=name, sentence=sentence):
					self.assertIn(f'__("{sentence}")', code)

	def test_no_irregular_whitespace(self):
		"""CI's eslint gate (`npx eslint . --quiet`, eslint:recommended) errors on
		no-irregular-whitespace, and eslint is not installed locally, so a literal no-break space
		in a regex shipped once (v1.632.0). This is that rule's character set, checked everywhere
		in the file (eslint skips string literals by default; this does not, so write the escape)."""
		# Built from code points, so this file holds none of them (and no escape a shell can eat).
		points = (0x0B, 0x0C, 0xA0, 0x1680, 0x180E, *range(0x2000, 0x200C), 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF)
		irregular = re.compile("[" + "".join(map(chr, points)) + "]")
		for path in [*(KIT_DIR / f"{name}.js" for name in MODULES), BUNDLE, PLANNER_TOAST, HARNESS]:
			for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
				for match in irregular.finditer(line):
					with self.subTest(file=path.name, line=number):
						self.fail(f"{path.name}:{number}:{match.start() + 1} holds U+{ord(match.group()):04X}")

	def test_esc_stays_import_free(self):
		"""Three planner suites load sf_kit/esc.js as plain script text in front of
		planner_kit/escape.js; an import line in it would be a SyntaxError there."""
		self.assertNotRegex(_strip_js_comments((KIT_DIR / "esc.js").read_text(encoding="utf-8")), r"^\s*import\b")

	def test_planner_kit_reexports_rather_than_copies(self):
		escape = _strip_js_comments(PLANNER_ESCAPE.read_text(encoding="utf-8"))
		self.assertIn('import { esc } from "../sf_kit/esc.js";', escape)
		self.assertIn("export const escape = esc;", escape)
		self.assertNotIn("&amp;", escape, "a second escaping table")
		toast = _strip_js_comments(PLANNER_TOAST.read_text(encoding="utf-8"))
		self.assertIn('from "../sf_kit/toast.js"', toast)
		self.assertIn('{ prefix: "pk" }', toast)
		self.assertNotIn("createElement", toast, "a second toast implementation")


class TestWiringAndDocs(unittest.TestCase):
	def test_ci_runs_both_halves(self):
		ci = CI.read_text(encoding="utf-8")
		for step in ("python -m unittest erpnext_enhancements.tests.test_sf_kit -v", "node scripts/test_sf_kit.mjs"):
			self.assertTrue(f"run: {step}" in ci, f"ci.yml has no step running {step}")

	def test_the_guide_and_the_readme_describe_it(self):
		guide = GUIDE.read_text(encoding="utf-8")
		section = guide.split("## sf_kit: the shared page pieces", 1)
		self.assertEqual(len(section), 2, "docs/app-design-system.md has no sf_kit section")
		section = section[1].split("\n## ", 1)[0]
		for needle in (
			'frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"]',
			"adoptedStyleSheets",
			"sf_kit.call(",
			"sf_kit.state.render(",
			"sf_kit.chip(",
			"sf-card",
			"sf-btn--primary",
			"sf_kit.toast(",
			"sf_kit.tiles.html(",
			"sf_kit.esc(",
		):
			self.assertTrue(needle in section, f"the sf_kit section never shows {needle}")
		self.assertTrue("`sf_kit/`" in PUBLIC_README.read_text(encoding="utf-8"), "public/README.md")


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestUnderNode(unittest.TestCase):
	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=300,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

	def test_every_file_parses(self):
		for path in [*(KIT_DIR / f"{name}.js" for name in MODULES), BUNDLE, PLANNER_ESCAPE, PLANNER_TOAST, HARNESS]:
			result = subprocess.run(
				[shutil.which("node"), "--check", str(path)], capture_output=True, text=True, timeout=60
			)
			with self.subTest(path.name):
				self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
	unittest.main()
