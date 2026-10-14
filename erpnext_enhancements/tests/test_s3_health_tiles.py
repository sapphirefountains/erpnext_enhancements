"""Custom pages S3.2 (v1.651.0, TASK-2026-02821): the four health dashboards share one tile renderer.

Before this, each drew its own tiles and its own status colours:

* Integrations Health and the Device Fleet Dashboard were one page written twice (class prefix
  ``ih-`` and ``dfd-``), each with a green / amber / red map onto Frappe palette variables, and a
  coloured dot as the only sign of a tile's status or a metric's: nothing for a colour-blind reader
  or a screen reader.
* The QuickBooks Online dashboard drew four status boxes by hand, from a stylesheet that
  ``desk_addons.bundle.scss`` shipped to every Desk page.
* The Stripe dashboard mapped each payment status to a Frappe indicator colour, and its count tiles
  and connection banner were hand-built Bootstrap.

Now every tile is ``sf_kit.tiles``: each status a chip that says its word, every colour a ``--sf-*``
token, every server string escaped by the kit, and links through the kit's ``safe_href`` rule. The
two health pages are thin callers of one shared module, ``public/js/health_board.bundle.js``.

These are the static guards; the behaviour, against the real kit, is
``scripts/test_s3_page_states.mjs`` (run by ``test_s3_page_states``). Bench-free: the scripts are
read as text, comments stripped.

Run: python -m unittest erpnext_enhancements.tests.test_s3_health_tiles
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
BOARD = APP / "public/js/health_board.bundle.js"
LOADER = APP / "public/js/sf_kit_loader.js"
DESK_ADDONS = APP / "public/css/desk_addons.bundle.scss"
QBO_DIR = APP / "quickbooks_online/page/quickbooks_online_dashboard"
QBO_CSS = QBO_DIR / "quickbooks_online_dashboard.css"
CI = REPO / ".github/workflows/ci.yml"

PAGES = {
	"integrations-health": APP / "integration_hub/page/integrations_health/integrations_health.js",
	"device-fleet-dashboard": APP / "device_management/page/device_fleet_dashboard/device_fleet_dashboard.js",
	"quickbooks-online-dashboard": QBO_DIR / "quickbooks_online_dashboard.js",
	"stripe-payments-dashboard": APP
	/ "stripe_payments/page/stripe_payments_dashboard/stripe_payments_dashboard.js",
}
HEALTH_PAGES = ("integrations-health", "device-fleet-dashboard")
# Every script that draws on these pages: the four, and the board two of them call.
SCANNED = [*PAGES.items(), ("health-board", BOARD)]

KIT_TONES = {"good", "warn", "danger", "neutral"}

# A status colour written into a page: a hex literal, a Frappe palette variable, a Frappe
# indicator class, a Bootstrap alert colour, an inline background, or rgb()/hsl().
COLOUR = re.compile(
	r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?(?:[0-9a-fA-F]{2})?\b"
	r"|var\(--(?:green|red|yellow|orange|blue|gray|grey|purple|pink|cyan|dark-green)\b"
	r"|indicator-pill|\bindicator\s+(?:green|red|orange|yellow|blue|gray|grey|purple)\b"
	r"|alert-(?:success|warning|danger|info)"
	r"|background\s*:"
	r"|\b(?:rgb|rgba|hsl|hsla)\("
)
# A colour NAME as a value: the shape of a status-to-colour map ({ Paid: "green" }).
COLOUR_VALUE = re.compile(r""":\s*(['"])(?:green|red|orange|yellow|amber|blue|gray|grey|purple|pink)\1""")
# Tile markup written by hand: the kit's own classes in a class attribute, or the classes and
# Bootstrap scaffolding the pages used to draw their tiles with.
HAND_TILE = re.compile(
	r"""class=(['"])[^'"]*\b(?:sf-tiles?|sf-card|sf-chip|sf-tile-[\w-]+)\b"""
	r"|ih-card|dfd-card|ih-dot|dfd-dot|mini-dot|qbo-status-(?:grid|item)|qbo-label|qbo-value"
	r"""|class=(['"])(?:card|col-sm-\d+)\2"""
	r"|data-field="
)


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped, so an absence assertion is not tripped (or satisfied) by
	the comment that explains it."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _without_dialog_indicators(code):
	"""frappe.msgprint / frappe.show_alert take an `indicator` colour for their own dialog or alert
	(the QuickBooks buttons answer a click that way). That is frappe's chrome, not a status the page
	draws, so it is taken out before the colour checks."""
	return re.sub(r"indicator:\s*[^,\n}]*", "", code)


def _css_rules(css):
	"""[(selector list, body)] for every rule outside an at-rule's own braces, and inside them."""
	css = re.sub(r"/\*[\s\S]*?\*/", "", css)
	return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css)]


class TestOneTileRenderer(unittest.TestCase):
	def test_every_page_draws_its_tiles_through_the_kit(self):
		for name in ("quickbooks-online-dashboard", "stripe-payments-dashboard"):
			with self.subTest(page=name):
				self.assertIn("sf.tiles.html(", _code(PAGES[name]))
		board = _code(BOARD)
		self.assertIn("sf.tiles.html(", board)
		for name in HEALTH_PAGES:
			with self.subTest(page=name):
				code = _code(PAGES[name])
				self.assertIn("frappe.require(['health_board.bundle.js']", code)
				self.assertIn("board.mount(page, {", code)
				self.assertIn(".map(board.tile)", code)

	def test_no_page_renders_tile_markup_by_hand(self):
		for name, path in SCANNED:
			with self.subTest(page=name):
				self.assertIsNone(HAND_TILE.search(_code(path)), "draw tiles with sf.tiles.html")

	def test_no_page_keeps_a_status_colour(self):
		for name, path in SCANNED:
			with self.subTest(page=name):
				code = _without_dialog_indicators(_code(path))
				self.assertIsNone(COLOUR.search(code), "a status colour: the kit's tokens draw it")
				self.assertIsNone(COLOUR_VALUE.search(code), "a status-to-colour map")
				self.assertNotIn("statusColor", code)
				self.assertNotIn("const TONE", code)

	def test_a_status_map_names_kit_tones_only(self):
		"""A page may say what a status means (good news, bad news); how that looks is the kit's."""
		found = 0
		for name, path in PAGES.items():
			for match in re.finditer(r"const (\w*TONE\w*) = \{([^}]*)\}", _code(path)):
				found += 1
				with self.subTest(page=name, map=match.group(1)):
					values = set(re.findall(r""":\s*['"]([\w-]+)['"]""", match.group(2)))
					self.assertTrue(values, "an empty map")
					self.assertLessEqual(values, KIT_TONES)
		self.assertEqual(found, 2, "QBO_CONNECTION_TONE and Stripe's STATUS_TONE")

	def test_no_server_text_reaches_markup_unescaped(self):
		"""The Stripe table and the QuickBooks log rows are the only markup the pages still build;
		every interpolated server value goes through esc() or a kit chip."""
		stripe = _code(PAGES["stripe-payments-dashboard"])
		rows = stripe[stripe.index("const rows = (data.recent || [])") : stripe.index("$body.html(`")]
		for raw in re.findall(r"\$\{([^}]*)\}", rows):
			with self.subTest(interpolation=raw):
				if "r." not in raw:
					continue
				self.assertRegex(raw, r"esc\(|sf\.chip\(|encodeURIComponent\(|\?\s*`")
		self.assertIn("esc(format_currency(", rows)
		self.assertIn("sf.chip(r.status,", rows)

	def test_no_status_is_colour_alone(self):
		"""Each health check's tone goes to the kit, which draws a chip with a word for it."""
		board = _code(BOARD)
		tile = board[board.index("function tile(check) {") : board.index("function mount(page, opts) {")]
		self.assertIn("status: c.status,", tile)
		self.assertIn("metrics: list(c.metrics),", tile)
		health = _code(PAGES["integrations-health"])
		for word in ("__('Pass')", "__('Fail')", "__('Not set up')", "__('Failed')"):
			with self.subTest(word=word):
				self.assertIn(f"sf.chip({word}", health)
		self.assertNotIn("'✓'", health)


class TestTheDeviceFleetIsAThinCaller(unittest.TestCase):
	def setUp(self):
		self.code = _code(PAGES["device-fleet-dashboard"])

	def test_it_draws_nothing_of_its_own(self):
		for token in ("function tile(", "function render(", "frappe.call(", "<style", "metrics", "links"):
			with self.subTest(token=token):
				self.assertNotIn(token, self.code)

	def test_it_is_short(self):
		lines = [ln for ln in self.code.splitlines() if ln.strip()]
		self.assertLessEqual(len(lines), 30, "a thin caller of the health board")


class TestTheHealthBoard(unittest.TestCase):
	def setUp(self):
		self.code = _code(BOARD)

	def test_it_gets_the_kit_from_the_shared_loader(self):
		self.assertIn("window.sf_kit_ready().then((sf) => {", self.code)
		self.assertNotIn("sf_kit.bundle", self.code, "the loader loads the kit, once")

	def test_its_first_load_is_quiet_with_the_s31_states(self):
		self.assertIn("sf.call(opts.method, {}, { quiet: true })", self.code)
		self.assertRegex(self.code, r'state\.render\(\$content, "loading"')
		self.assertIn('state.render($content, "no-access"', self.code)
		self.assertIn("err.status === 403", self.code)
		self.assertIn("err.session_expired", self.code)
		self.assertIn("on_action: () => load()", self.code)
		self.assertIn("on_action: () => window.location.reload()", self.code)

	def test_a_failed_refresh_keeps_the_tiles(self):
		self.assertIn("if (!loaded) return failed(sf, err);", self.code)
		self.assertIn("sf.toast(", self.code)

	def test_a_stale_reply_never_paints(self):
		self.assertIn("const seq = ++asked;", self.code)
		self.assertGreaterEqual(self.code.count("if (seq !== asked) return;"), 3)

	def test_a_tile_is_found_without_a_selector_built_from_server_text(self):
		self.assertIn('this.getAttribute("data-key") === String(key)', self.code)
		self.assertNotRegex(self.code, r"data-key=\"\$\{|data-key='\$\{|\[data-key=\" \+")

	def test_it_is_loaded_only_by_the_two_pages(self):
		hits = sorted(
			p.relative_to(APP).as_posix()
			for p in APP.rglob("*.js")
			if "health_board.bundle.js" in _text(p) and p != BOARD and "node_modules" not in p.parts
		)
		self.assertEqual(hits, sorted(PAGES[n].relative_to(APP).as_posix() for n in HEALTH_PAGES))
		hooks = _text(APP / "hooks.py")
		self.assertNotIn("health_board", hooks, "never app_include_js")


def _plain_tiles_body(code, name):
	"""The body of a page's (or the loader's) plain tile renderer, indentation removed."""
	start = code.index(f"function {name}(items, esc) {{")
	lines = code[start:].splitlines()
	end = next(i for i, ln in enumerate(lines) if ln.strip() == "}")
	return [ln.strip() for ln in lines[1:end]]


class TestTheStandInTiles(unittest.TestCase):
	"""With no kit (the loader's stand-in, or a page's bare kit when the loader is missing too), a
	tile is a line of words, and its status still says itself."""

	def test_the_loaders_stand_in_draws_tiles_as_words(self):
		code = _code(LOADER)
		stand_in = code[code.index("function plain_kit() {") : code.index("window.sf_kit_ready")]
		self.assertIn("chip: (label) => escape(label),", stand_in)
		self.assertIn("tiles: { html: (items) => plain_tiles(items, escape) },", stand_in)
		body = "\n".join(_plain_tiles_body(code, "plain_tiles"))
		for token in (
			"words[i.status ?? i.tone]",
			'good: __("Healthy")',
			'warn: __("Needs attention")',
			'danger: __("Failing")',
			"rows(i.metrics)",
			"rows(i.links).map((l) => l.label)",
			".map(esc)",
		):
			with self.subTest(token=token):
				self.assertIn(token, body)
		self.assertNotIn("neutral", body, "a neutral status has no word, as in the kit")

	def test_a_bare_page_kit_draws_the_same_lines(self):
		loader = _plain_tiles_body(_code(LOADER), "plain_tiles")
		for name, helper in (
			("quickbooks-online-dashboard", "qboPlainTiles"),
			("stripe-payments-dashboard", "plainTiles"),
		):
			with self.subTest(page=name):
				code = _code(PAGES[name])
				self.assertIn(f"tiles: {{ html: (items) => {helper}(items, esc) }}", code)
				self.assertEqual(
					_plain_tiles_body(code, helper), loader, "a copy of the loader's plain_tiles"
				)


class TestTheQuickBooksStylesheet(unittest.TestCase):
	def test_it_no_longer_ships_on_every_desk_page(self):
		scss = _text(DESK_ADDONS)
		self.assertNotIn("qbo_dashboard", re.sub(r"//[^\n]*", "", scss))
		self.assertFalse((APP / "public/css/quickbooks_online/qbo_dashboard.css").exists())
		self.assertIn('@import "./quickbooks_online/qbo_matching";', scss, "the matching page keeps its own")

	def test_the_page_carries_its_own(self):
		self.assertTrue(QBO_CSS.is_file())

	def test_every_selector_is_scoped_to_the_page(self):
		"""getpage's CSS is appended to <head> once and then applies to every Desk page."""
		rules = _css_rules(_text(QBO_CSS))
		self.assertGreater(len(rules), 5)
		for selectors, _body in rules:
			for selector in selectors.split(","):
				with self.subTest(selector=selector.strip()):
					self.assertRegex(selector.strip(), r"^\.qbo-dashboard(?:\s|$)")

	def test_it_follows_the_app_design_rules(self):
		css = re.sub(r"/\*[\s\S]*?\*/", "", _text(QBO_CSS))
		self.assertEqual(re.findall(r"@media[^{]*", css), ["@media (max-width: 767px) "])
		self.assertNotIn("shadow", css)
		for value in re.findall(r"border-radius\s*:\s*([^;]+);", css):
			self.assertEqual(value.strip(), "var(--sf-radius-none)")
		for prop, value in re.findall(r"(color|background|border(?:-\w+)?)\s*:\s*([^;]+);", css):
			with self.subTest(prop=prop, value=value):
				if prop.startswith("border") and value.strip() == "0":
					continue
				if prop == "border-radius":
					continue
				self.assertRegex(value, r"var\(--sf-[\w-]+\)")
				self.assertNotRegex(value, r"#[0-9a-fA-F]{3}|var\(--(?!sf-)")

	def test_nothing_else_uses_its_rules(self):
		classes = set(re.findall(r"\.(qbo-[\w-]+)", _text(QBO_CSS))) - {"qbo-dashboard"}
		self.assertTrue(classes)
		for path in APP.rglob("*"):
			if path.suffix not in {".js", ".html", ".py", ".css", ".scss"} or path in (
				QBO_CSS,
				PAGES["quickbooks-online-dashboard"],
			):
				continue
			if "node_modules" in path.parts or path.name == Path(__file__).name:
				continue
			text = _text(path)
			for cls in classes:
				with self.subTest(file=path.relative_to(APP).as_posix(), cls=cls):
					self.assertNotRegex(text, rf"\b{re.escape(cls)}\b")


class TestThePagesStayInTheDesk(unittest.TestCase):
	def test_no_hard_coded_app_paths(self):
		for name, path in SCANNED:
			with self.subTest(page=name):
				self.assertNotRegex(_code(path), r"['\"`(]/app/")

	def test_no_history_writes(self):
		for name, path in SCANNED:
			with self.subTest(page=name):
				for token in ("pushState", "replaceState", "popstate"):
					self.assertNotIn(token, _code(path))


class TestWiring(unittest.TestCase):
	def test_it_runs_in_ci(self):
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_s3_health_tiles -v", _text(CI))


if __name__ == "__main__":
	unittest.main()
