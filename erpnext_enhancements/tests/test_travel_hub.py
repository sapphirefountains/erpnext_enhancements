"""The Travel hub (/desk/travel): built so someone with no idea where to look can find their trip.

Four things make the hub, and each can drift in silence:

* **the "My Travel" block** (``custom_html_blocks/travel_home.*``) — the viewer's own trip, first
  on the page. A block because nothing else on a workspace can be different for every viewer.
  It renders what ``travel_management.home.get_travel_home`` sends and decides nothing: no date
  arithmetic (a browser in another time zone would count days differently from the email), and
  no money anywhere (crew see everything about a trip except what it cost);
* **the workspace JSON** — the block first, six "I want to…" shortcuts, a four-step "How a work
  trip works", then the records cards with Setup last. A placement with no seeded block renders
  an empty div; a coordinator-only link in a shortcut row leaves a column-sized hole for crew;
* **the sidebar** (``workspace_sidebar/travel.json``) — replacing the one production generated
  for itself, whose two URL items had no URL at all;
* **the reload patches** — both records are timestamp-gated on import, and a patch that raises
  aborts ``bench migrate``, which on this repo is the deploy. A patch runs once per site, so each
  release that changes the hub brings its own.

Since v1.556.0 the hub's "Trips" (the shortcut, the sidebar item and a link in the block) opens
everyone's trips on the web itinerary, ``/itinerary?view=trips``, where any staff member may open
any trip with the money left out. The desk Travel Trip list stays scoped to the viewer's own
trips, so "My trips" pointing at it could never show a colleague's (Nik, 2026-09-28).

Reads files only, except for runs of the patches against a stub ``frappe`` that is removed again
afterwards — which is why it has a CI step of its own — and runs of the block's script in node,
against a stub desk (skipped where node is not installed).

Run: python -m unittest erpnext_enhancements.tests.test_travel_hub
"""

import ast
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BLOCKS_DIR = APP / "custom_html_blocks"
BLOCK_JS = BLOCKS_DIR / "travel_home.js"
BLOCK_CSS = BLOCKS_DIR / "travel_home.css"
BLOCK_HTML = BLOCKS_DIR / "travel_home.html"
#: The desk-wide helper that reloads a block when you come back to its workspace (v1.556.3).
#: This block's own route handler was its first version.
RETURN_HELPER_JS = APP / "public" / "js" / "global_enhancements" / "workspace_block_return.js"
SEEDER = APP / "setup" / "custom_html_blocks.py"
HOME_PY = APP / "travel_management" / "home.py"
WORKSPACE = APP / "travel_management" / "workspace" / "travel_management" / "travel_management.json"
SIDEBAR_DIR = APP / "workspace_sidebar"
SIDEBAR = SIDEBAR_DIR / "travel.json"
PATCHES_TXT = APP / "patches.txt"
PATCH_MODULE = "erpnext_enhancements.patches.reload_travel_hub"
TRIPS_PATCH_MODULE = "erpnext_enhancements.patches.reload_travel_hub_trips"

METHOD = "erpnext_enhancements.travel_management.home.get_travel_home"
BLOCK_NAME = "My Travel"

#: Everyone's trips on the web itinerary. The shortcut, the sidebar item and the block all open it.
ALL_TRIPS_URL = "/itinerary?view=trips"

#: The stamps v1.554.0 shipped, and the ones production holds since its reload_travel_hub ran. A
#: workspace or sidebar JSON no newer than the stored row is skipped on import, silently.
OLD_WORKSPACE_STAMP = "2026-09-28 18:00:00.000000"
OLD_SIDEBAR_STAMP = "2026-09-28 18:00:00.000000"

#: label -> what the shortcut must be. All six are open to an Employee, so the row has no holes.
SHORTCUTS = {
	"See my itinerary": {"type": "URL", "url": "/itinerary"},
	"Plan a Trip": {"type": "Page", "link_to": "plan-a-trip"},
	"Trips": {"type": "URL", "url": ALL_TRIPS_URL},
	"Who's away when": {
		"type": "DocType",
		"link_to": "Travel Trip",
		"doc_view": "Calendar",
		"format": "{} on the calendar",
		"stats_filter": {"status": ["in", ["Planning", "Booked", "In Progress"]]},
	},
	"Travel rules & per diem": {"type": "URL", "url": "/travel_guidelines"},
	"Places & job sites": {"type": "DocType", "link_to": "Travel POI"},
}

#: Readable only by coordinators (Travel Settings) or by coordinator report roles. In a shortcut
#: row each would leave an empty, column-wide wrapper on an Employee's page.
COORDINATOR_ONLY = {"Travel Settings", "Travel Spend by Category", "Unclaimed Travel Expenses"}

#: Keys and words that only ever name money. The block has nowhere to put any.
MONEY_TOKENS = ("cost", "paid_by", "per_diem", "amount", "unclaimed", "mileage", "currency")


def source(path):
	return path.read_text(encoding="utf-8")


def js_code(path):
	"""JS with comments stripped: the comments explain why things are absent, so they name them."""
	src = re.sub(r"/\*.*?\*/", "", source(path), flags=re.S)
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def css_code(path):
	return re.sub(r"/\*.*?\*/", "", source(path), flags=re.S)


def css_rule(css, selector):
	"""The declarations of the first rule whose selector list names ``selector`` exactly."""
	for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
		if selector in [part.strip() for part in selectors.split(",")]:
			return body
	return None


def literal(path, name):
	"""One module-level literal assignment, read without importing the module."""
	for node in ast.parse(source(path)).body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return ast.literal_eval(node.value)
	raise AssertionError(f"{name} not found in {path}")


def workspace():
	return json.loads(source(WORKSPACE))


def content():
	return json.loads(workspace()["content"])


def sidebar():
	return json.loads(source(SIDEBAR))


#: The block's script run twice in node, as the workspace runs it for two renders of the page,
#: against a stub desk carrying the real return helper: ``frappe.router`` fires "change" (each
#: navigation a new route array, as v16's router makes one), ``frappe.call`` answers when told
#: to. Prints what it saw as JSON. ``__BLOCK_JS__`` and ``__HELPER_JS__`` are the two scripts'
#: paths, as JSON strings.
_RETURN_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(__BLOCK_JS__, "utf8");
const helper = fs.readFileSync(__HELPER_JS__, "utf8");
const handlers = [];
const pending = [];
let route = ["Workspaces", "Travel"];
const win = {};
const frappe = {
	utils: { escape_html: (value) => String(value) },
	router: { on: (evt, fn) => handlers.push([evt, fn]) },
	get_route: () => route,
	call: () => new Promise((resolve) => pending.push(resolve)),
	provide: (ns) => ns.split(".").reduce((obj, key) => (obj[key] = obj[key] || {}), win),
};
// The desk bundle evaluates the helper once, before any block runs.
new Function("frappe", "window", helper)(frappe, win);
function makeRoot() {
	const els = {
		"#tvh-body": { innerHTML: "", querySelectorAll: () => [] },
		"#tvh-sub": { textContent: "" },
		"#tvh-refresh": { addEventListener: () => {} },
	};
	return { host: { isConnected: true }, querySelector: (sel) => els[sel] || null, sub: els["#tvh-sub"] };
}
function run(root) {
	new Function("root_element", "frappe", "window", "document", src)(root, frappe, win, {});
}
function change(to) {
	route = to;
	handlers.forEach(([evt, fn]) => evt === "change" && fn());
}
const tick = () => new Promise((resolve) => setImmediate(resolve));
const answer = async (i, name) => {
	pending[i]({ message: { viewer: { first_name: name } } });
	await tick();
};
(async () => {
	const seen = {};
	const a = makeRoot();
	run(a);
	seen.first_load = pending.length;
	await answer(0, "A0");
	seen.a_drawn = a.sub.textContent;
	change(["Form", "Travel Trip", "TRIP-1"]);
	change(["plan-a-trip"]);
	seen.away = pending.length;
	change(["Workspaces", "Travel"]);
	seen.back = pending.length;
	await answer(1, "A1");
	seen.a_reloaded = a.sub.textContent;
	change(["Workspaces", "Home"]);
	seen.other_workspace = pending.length;
	change(["Workspaces", "private", "Travel"]);
	seen.private_travel = pending.length;
	// The workspace renders the page again: the old block leaves the page, a new one runs.
	a.host.isConnected = false;
	const b = makeRoot();
	run(b);
	seen.handlers = handlers.length;
	seen.b_load = pending.length;
	change(["Workspaces", "Travel"]);
	seen.b_back = pending.length;
	await answer(4, "B-new");
	await answer(3, "B-old");
	seen.b_drawn = b.sub.textContent;
	seen.a_untouched = a.sub.textContent;
	await answer(2, "A-late");
	seen.b_after_a_late = b.sub.textContent;
	b.host.isConnected = false;
	change(["Workspaces", "Travel"]);
	seen.gone = pending.length;
	console.log(JSON.stringify(seen));
})();
"""

#: The block's script run once per answer, as the workspace runs it, and the body it draws for each:
#: nothing of the viewer's, no Employee record, and a coordinator's full page. Prints the three
#: bodies as JSON. ``__BLOCK_JS__`` is the script's path, as a JSON string.
_DRAW_JS = r"""
const src = require("fs").readFileSync(__BLOCK_JS__, "utf8");
const pending = [];
const frappe = {
	utils: { escape_html: (value) => String(value) },
	router: { on: () => {} },
	get_route: () => [],
	call: () => new Promise((resolve) => pending.push(resolve)),
};
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function draw(message) {
	const els = {
		"#tvh-body": { innerHTML: "", querySelectorAll: () => [] },
		"#tvh-sub": { textContent: "" },
		"#tvh-refresh": { addEventListener: () => {} },
	};
	const root = { host: { isConnected: true }, querySelector: (sel) => els[sel] || null };
	new Function("root_element", "frappe", "window", "document", src)(root, frappe, {}, {});
	pending[pending.length - 1]({ message });
	await tick();
	return els["#tvh-body"].innerHTML;
}
(async () => {
	const out = {};
	out.empty = await draw({ viewer: {}, trips: [] });
	out.unlinked = await draw({ viewer: {}, message: "Your user account isn't linked to an employee record." });
	out.full = await draw({
		viewer: { first_name: "Ann", is_coordinator: true },
		featured: { trip: "TRIP-1", purpose: "Install", status: "Booked", itinerary_url: "/itinerary?trip=TRIP-1" },
		trips: [{ trip: "TRIP-2", purpose: "Service", status: "Planning", relation: "traveling" }],
		attention: { trips: [{ trip: "TRIP-3", purpose: "Survey", status: "Planning", reasons: ["No hotel"] }] },
	});
	console.log(JSON.stringify(out));
})();
"""


# --------------------------------------------------------------------------- the block


class TestTheBlock(unittest.TestCase):
	def test_all_three_files_exist(self):
		for path in (BLOCK_HTML, BLOCK_JS, BLOCK_CSS):
			with self.subTest(path.name):
				self.assertTrue(path.exists(), f"{path} is missing")

	def test_it_is_seeded(self):
		"""A placement naming an unseeded block renders an empty div with no other symptom."""
		self.assertIn('("My Travel", "travel_home")', source(SEEDER))
		self.assertIn((BLOCK_NAME, "travel_home"), literal(SEEDER, "BLOCKS"))

	def test_it_is_placed_by_the_workspace_json_and_nowhere_else(self):
		"""Not on Home, and not in DEPARTMENT_DASHBOARD_BLOCKS: that map's own test finds a
		workspace by `*/workspace/<scrub(name)>/`, and this one lives in `travel_management/`,
		so a "Travel" key there fails the build."""
		self.assertNotIn(BLOCK_NAME, literal(SEEDER, "HOME_BLOCKS"))
		placement = literal(SEEDER, "DEPARTMENT_DASHBOARD_BLOCKS")
		self.assertNotIn("Travel", placement)
		self.assertNotIn(BLOCK_NAME, {name for names in placement.values() for name in names})

	def test_it_calls_the_travel_home_endpoint_once(self):
		code = js_code(BLOCK_JS)
		self.assertIn(f'const METHOD = "{METHOD}";', code)
		# Written `frappe` / newline / `.call({...})`, so count the invocation itself.
		self.assertEqual(code.count(".call({ method: METHOD })"), 1)
		self.assertEqual(code.count(".call("), 1)
		self.assertNotIn("frappe.xcall", code)

	def test_the_endpoint_it_calls_exists_and_is_whitelisted(self):
		"""Nothing else checks a block's method: a typo here is a block that says "Could not
		load your travel." to everybody, for ever."""
		tree = ast.parse(source(HOME_PY))
		found = [
			node
			for node in tree.body
			if isinstance(node, ast.FunctionDef) and node.name == METHOD.rsplit(".", 1)[1]
		]
		self.assertEqual(len(found), 1, f"{HOME_PY} has no get_travel_home")
		decorators = [ast.unparse(d) for d in found[0].decorator_list]
		self.assertTrue(any("whitelist" in d for d in decorators), decorators)

	def test_it_does_no_date_arithmetic(self):
		"""Every sentence about time ("Starts in 5 days", "Ended 3 days ago") comes from the
		server. A second answer in a browser, in the viewer's own time zone, would disagree with
		the email and the itinerary on the day it matters."""
		code = js_code(BLOCK_JS)
		for token in ("Date(", "Date.now", "getTime", "moment(", "frappe.datetime", "getDate"):
			with self.subTest(token):
				self.assertNotIn(token, code)

	def test_it_names_no_money(self):
		code = js_code(BLOCK_JS).lower()
		for token in MONEY_TOKENS:
			with self.subTest(token):
				self.assertNotIn(token, code)

	def test_it_escapes_everything_it_injects(self):
		"""It builds HTML strings, so every interpolation goes through esc(), and there are no
		template literals for one to slip past it in."""
		code = js_code(BLOCK_JS)
		self.assertIn("frappe.utils.escape_html", code)
		self.assertNotIn("${", code)
		self.assertNotIn("`", code)

	def test_it_lives_in_the_shadow_root(self):
		"""The workspace runs the whole script again, with a fresh root, each time it renders the
		page — which is not every time you come back to it (the next test)."""
		code = js_code(BLOCK_JS)
		self.assertIn("root_element", code)
		self.assertIn("function waitForDOM()", code)

	def test_it_reloads_when_you_come_back_to_the_hub(self):
		"""v16's ``Workspace.show()`` returns early when the workspace asked for is the one already
		shown (``if (this._page?.name === page.name) return;``), so from the hub to a trip form or
		Plan a Trip and back left the block as it was, with the trip just changed out of date. The
		block reloads when the route comes back to its workspace. Since v1.556.3 it does that
		through the desk-wide return helper, which every data block shares, rather than a router
		handler of its own: ``frappe.router.off`` wraps the handler in a new function before
		unbinding, so it can never remove one, and the helper binds the one for the whole desk."""
		code = js_code(BLOCK_JS)
		self.assertNotIn("frappe.router", code)
		self.assertNotIn("__tvh_route_bound", code)
		self.assertIn("window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks", code)
		# Guarded, so a device holding a bundle from before the helper still draws the block.
		self.assertIn(
			"if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);", code
		)
		# The helper reads v16's route for a workspace, ["Workspaces", name] or
		# ["Workspaces", "private", name], and reloads only a block still on the page.
		helper = js_code(RETURN_HELPER_JS)
		self.assertIn('route[0] !== "Workspaces"', helper)
		self.assertIn('route[1] === "private" ? route[2] : route[1]', helper)
		self.assertIn("host.isConnected", helper)
		self.assertEqual(helper.count('router.on("change"'), 1)
		# The claim that sent it stale: that every navigation re-runs the script.
		self.assertNotIn("on every navigation", source(BLOCK_JS))
		self.assertNotIn("re-runs this whole script", source(BLOCK_JS))

	def test_coming_back_reloads_the_newest_block_and_only_the_newest_answer_draws(self):
		"""The script itself, run in node against a stub desk carrying the real return helper: two
		runs (the workspace rendered twice), a router that fires "change", and answers that arrive
		out of order."""
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		script = _RETURN_JS.replace("__BLOCK_JS__", json.dumps(str(BLOCK_JS))).replace(
			"__HELPER_JS__", json.dumps(str(RETURN_HELPER_JS))
		)
		result = subprocess.run(
			[node, "-e", script],
			capture_output=True,
			text=True,
			encoding="utf-8",
			check=False,
			timeout=60,
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		seen = json.loads(result.stdout)
		# The first run loads once; leaving for a form or Plan a Trip asks nothing.
		self.assertEqual((seen["first_load"], seen["away"]), (1, 1))
		self.assertIn("Hi A0.", seen["a_drawn"])
		# Back to Travel reloads it, and the answer draws.
		self.assertEqual(seen["back"], 2)
		self.assertIn("Hi A1.", seen["a_reloaded"])
		# Another workspace is not this one; a private workspace named Travel is.
		self.assertEqual((seen["other_workspace"], seen["private_travel"]), (2, 3))
		# The page rendered again: still one handler, and it reloads the new block, not the old.
		self.assertEqual(seen["handlers"], 1)
		self.assertEqual((seen["b_load"], seen["b_back"]), (4, 5))
		self.assertIn("Hi A1.", seen["a_untouched"])
		# Answers arriving newest first: the older one never paints over it, and a late answer
		# for the replaced block never reaches the new one.
		self.assertIn("Hi B-new.", seen["b_drawn"])
		self.assertIn("Hi B-new.", seen["b_after_a_late"])
		# A block no longer on the page is never reloaded.
		self.assertEqual(seen["gone"], 5)

	def test_web_pages_open_in_a_new_tab(self):
		"""/itinerary has no way back to the desk, so it never replaces the desk tab."""
		code = js_code(BLOCK_JS)
		self.assertIn('window.open(safe, "_blank", "noopener")', code)
		self.assertNotIn("window.location", code)

	def test_it_links_to_everyones_trips(self):
		"""The way to a colleague's trip: the desk list shows a crew member only their own, and
		/itinerary?view=trips shows everybody's with the money left out. A web page, so it goes
		through openButton like every other one: a new tab, and past the webUrl() guard."""
		code = js_code(BLOCK_JS)
		self.assertIn(f'const ALL_TRIPS_URL = "{ALL_TRIPS_URL}";', code)
		self.assertIn('openButton("👥", "See everyone\'s trips", ALL_TRIPS_URL, "is-quiet")', code)
		self.assertEqual(code.count("ALL_TRIPS_URL"), 2, "declared once, used once")
		# Right after the viewer's own trips, and before the coordinators' list.
		self.assertIn(
			"parts.push(tripsSection(data));\n        parts.push(allTripsLink());\n"
			"        parts.push(attentionSection(data.attention));",
			code,
		)
		self.assertIsNotNone(css_rule(css_code(BLOCK_CSS), ".tvh-all-trips"))

	def test_the_link_to_everyones_trips_is_drawn_for_every_viewer(self):
		"""Run, not grepped: with no trips (under the empty state), with no Employee record (the
		page, not the block, decides who may see the list), and on a coordinator's full page (under
		their own trips and above "Needs attention")."""
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not installed")
		result = subprocess.run(
			[node, "-e", _DRAW_JS.replace("__BLOCK_JS__", json.dumps(str(BLOCK_JS)))],
			capture_output=True,
			text=True,
			encoding="utf-8",
			check=False,
			timeout=60,
		)
		self.assertEqual(result.returncode, 0, result.stderr)
		bodies = json.loads(result.stdout)
		self.assertEqual(set(bodies), {"empty", "unlinked", "full"})
		link = f'data-action="open" data-url="{ALL_TRIPS_URL}"'
		for shape, body in bodies.items():
			with self.subTest(shape):
				self.assertEqual(body.count('class="tvh-all-trips"'), 1)
				self.assertEqual(body.count(link), 1)
				self.assertIn("See everyone's trips", body)
				self.assertIn('class="tvh-btn is-quiet"', body)
				# Below whatever the viewer reads first.
				first = "tvh-feature" if shape == "full" else "tvh-notice"
				self.assertLess(body.index(first), body.index(link))
		full = bodies["full"]
		self.assertLess(full.index('class="tvh-trips"'), full.index(link))
		self.assertLess(full.index(link), full.index("tvh-attention"))

	def test_keep_planning_hands_the_trip_over_the_way_plan_a_trip_reads_it(self):
		"""v16 delivers a page's arguments in frappe.route_options, not the address bar."""
		code = js_code(BLOCK_JS)
		self.assertIn('frappe.route_options = { trip: trip, step: "review" };', code)
		self.assertIn('frappe.set_route("plan-a-trip");', code)
		self.assertIn('frappe.set_route("travel-trip", trip);', code)

	def test_it_fails_with_a_sentence_not_a_blank(self):
		code = js_code(BLOCK_JS)
		self.assertIn("Could not load your travel.", code)
		self.assertIn("Loading&hellip;", source(BLOCK_HTML))

	def test_every_class_it_renders_has_a_rule(self):
		css = css_code(BLOCK_CSS)
		emitted = set(re.findall(r"\btvh-[a-z-]+", js_code(BLOCK_JS) + source(BLOCK_HTML)))
		self.assertGreater(len(emitted), 40)
		missing = sorted(c for c in emitted if f".{c}" not in css)
		self.assertEqual(missing, [], f"{missing} render unstyled")

	def test_a_long_job_name_wraps_and_the_dates_do_not(self):
		"""``.tvh-meta-item`` keeps the dates on one line. The featured card's job or customer name
		sat in one too, and a long one ran off the side of the card on a phone."""
		css = css_code(BLOCK_CSS)
		self.assertIn("white-space: nowrap", css_rule(css, ".tvh-meta-item"))
		wrap = css_rule(css, ".tvh-meta-for")
		for declaration in ("min-width: 0", "white-space: normal", "overflow-wrap: anywhere"):
			with self.subTest(declaration):
				self.assertIn(declaration, wrap)
		# Same specificity as the nowrap rule, so it must come after it to win.
		self.assertGreater(css.index(".tvh-meta-for"), css.index(".tvh-meta-item"))
		self.assertIn('class="tvh-meta-item tvh-meta-for">\' + esc(trip.travel_for)', js_code(BLOCK_JS))

	def test_every_modifier_it_renders_has_a_rule(self):
		code = js_code(BLOCK_JS)
		css = css_code(BLOCK_CSS)
		tones = set(re.findall(r'"(\w+)"', re.search(r"STATUS_TONE = \{(.*?)\};", code, re.S).group(1)))
		tones -= {"Planning", "Booked", "Completed", "Closed"}
		self.assertEqual(tones, {"orange", "blue", "green", "gray"})
		for modifier in [f"tone-{tone}" for tone in tones] + ["is-primary", "is-quiet"]:
			with self.subTest(modifier):
				self.assertIn(f".{modifier}", css)

	def test_the_primary_button_follows_the_dark_theme(self):
		"""v16's `--primary` is Bootstrap's compile-time #171717 in BOTH themes, so a button
		painted with it is black on a black card in dark mode. v16's own primary button uses
		`--btn-primary` on `--neutral`, which the dark theme swaps."""
		css = css_code(BLOCK_CSS)
		self.assertNotIn("var(--primary", css)
		primary = css_rule(css, ".tvh-btn.is-primary")
		self.assertIsNotNone(primary)
		self.assertIn("var(--btn-primary", primary)
		self.assertIn("var(--neutral", primary)

	def test_tap_targets_are_at_least_40px_tall(self):
		css = css_code(BLOCK_CSS)
		for selector in (".tvh-btn", ".tvh-link-btn", ".tvh-attn-row", ".tvh-contacts-summary", ".tvh-refresh"):
			with self.subTest(selector):
				body = css_rule(css, selector)
				self.assertIsNotNone(body, f"no rule for {selector}")
				self.assertRegex(body, r"(min-)?height:\s*40px")

	def test_the_layout_widens_with_its_column_not_the_window(self):
		"""The desk sidebar makes the window and the workspace column different widths."""
		css = css_code(BLOCK_CSS)
		self.assertIn("container-type: inline-size", css_rule(css, ".tvh-card"))
		self.assertIn("@container", css)


# --------------------------------------------------------------------------- the workspace


class TestTheWorkspace(unittest.TestCase):
	def test_it_keeps_its_identity(self):
		"""The name is the Desk tile's key and the /desk/travel route; the module keeps it
		reachable for anyone with an Employee DocPerm on Travel Trip."""
		ws = workspace()
		self.assertEqual((ws["name"], ws["label"], ws["title"]), ("Travel", "Travel", "Travel"))
		self.assertEqual(ws["module"], "Travel Management")
		self.assertEqual((ws["public"], ws["roles"], ws["sequence_id"]), (1, [], 11))
		self.assertEqual(ws["icon"], "milestone")

	def test_it_is_curated_and_restamped(self):
		ws = workspace()
		# hide_custom 0 appends automatic "Custom Documents" and "Custom Reports" cards.
		self.assertEqual(ws["hide_custom"], 1)
		self.assertGreater(ws["modified"], OLD_WORKSPACE_STAMP)

	def test_my_travel_is_the_first_thing_on_the_page(self):
		first = content()[0]
		self.assertEqual(first["type"], "custom_block")
		self.assertEqual(first["data"], {"custom_block_name": BLOCK_NAME, "col": 12})
		# v16 builds the block payload from this child table and joins on its label.
		self.assertEqual(
			workspace()["custom_blocks"], [{"custom_block_name": BLOCK_NAME, "label": BLOCK_NAME}]
		)

	def test_the_six_shortcuts_follow_i_want_to(self):
		blocks = content()
		heads = [b["data"]["text"] for b in blocks if b["type"] == "header"]
		self.assertIn("I want to", heads[0])
		placed = [b for b in blocks if b["type"] == "shortcut"]
		self.assertEqual([b["data"]["shortcut_name"] for b in placed], list(SHORTCUTS))
		self.assertEqual({b["data"]["col"] for b in placed}, {4})
		start = blocks.index(placed[0])
		self.assertEqual(blocks[start - 1]["type"], "header")
		self.assertEqual(blocks[start : start + 6], placed, "the six shortcuts are one run")

	def test_each_shortcut_is_what_its_label_says(self):
		rows = {row["label"]: row for row in workspace()["shortcuts"]}
		self.assertEqual(set(rows), set(SHORTCUTS))
		for label, want in SHORTCUTS.items():
			row = rows[label]
			with self.subTest(label):
				for key, value in want.items():
					if key == "stats_filter":
						# An object, not frappe's array form: the hub rule every other hub keeps.
						self.assertEqual(json.loads(row["stats_filter"]), value)
					else:
						self.assertEqual(row.get(key), value)
				if row["type"] == "URL":
					# A count and its filter belong to a DocType shortcut; on a URL one they
					# count nothing, and a link_to sends the importer looking for a doctype.
					for key in ("link_to", "stats_filter", "format", "doc_view"):
						self.assertNotIn(key, row)

	def test_trips_opens_everyones_trips_not_the_desk_list(self):
		"""v1.556.0 (Nik, 2026-09-28): "Change it from My Trips to just Trips so anyone can see
		anyone's trips". The desk list shows a crew member only the trips they own or travel on, and
		its form carries the money, so "Trips" is the web itinerary's list of every trip instead."""
		rows = {row["label"]: row for row in workspace()["shortcuts"]}
		self.assertNotIn("My trips", rows)
		self.assertEqual((rows["Trips"]["type"], rows["Trips"]["url"]), ("URL", ALL_TRIPS_URL))
		placed = {b["id"]: b["data"].get("shortcut_name") for b in content() if b["type"] == "shortcut"}
		self.assertEqual(placed.get("travel_sc_trips"), "Trips")
		self.assertNotIn("travel_sc_my_trips", placed)
		self.assertNotIn("My trips", source(WORKSPACE))
		# The desk list is still one click away for the office, on the Trips card.
		links = workspace()["links"]
		trip_list = [(row["link_type"], row["link_to"]) for row in links if row.get("label") == "Trip list"]
		self.assertEqual(trip_list, [("DocType", "Travel Trip")])

	def test_no_shortcut_is_coordinator_only(self):
		for row in workspace()["shortcuts"]:
			with self.subTest(row["label"]):
				self.assertNotIn(row.get("link_to"), COORDINATOR_ONLY)

	def test_how_a_work_trip_works(self):
		blocks = content()
		index = next(
			i for i, b in enumerate(blocks) if b["type"] == "header" and "How a work trip works" in b["data"]["text"]
		)
		paragraph = blocks[index + 1]
		self.assertEqual(paragraph["type"], "paragraph")
		text = paragraph["data"]["text"]
		for step in ("1.", "2.", "3.", "4."):
			self.assertIn(f"<b>{step}", text)
		self.assertIn('href="/itinerary"', text)
		self.assertIn('href="/travel_guidelines"', text)
		self.assertIn("within a week", text)
		# The Desk's editor keeps only these tags; anything else is lost on the first edit.
		tags = set(re.findall(r"</?\s*([a-zA-Z0-9]+)", text))
		self.assertLessEqual(tags, {"b", "i", "br", "a", "span"})

	def test_the_cards_end_with_setup(self):
		"""An Employee cannot read Travel Settings, so its card renders empty — and an empty card
		still takes its columns. Last in the row, the hole is trailing space."""
		cards = [b["data"]["card_name"] for b in content() if b["type"] == "card"]
		self.assertEqual(cards, ["Trips", "Reports", "Setup"])
		self.assertEqual(content()[-1]["data"].get("card_name"), "Setup")
		links = workspace()["links"]
		setup = next(i for i, row in enumerate(links) if row["type"] == "Card Break" and row["label"] == "Setup")
		self.assertEqual([row["link_to"] for row in links[setup + 1 :]], ["Travel Settings"])

	def test_the_cards_carry_what_the_design_says(self):
		groups, current = {}, None
		for row in workspace()["links"]:
			if row["type"] == "Card Break":
				current = groups[row["label"]] = []
			else:
				current.append((row["link_type"], row["link_to"], row["is_query_report"]))
		self.assertEqual(
			groups,
			{
				"Trips": [("DocType", "Travel Trip", 0), ("DocType", "Travel POI", 0), ("Page", "plan-a-trip", 0)],
				"Reports": [
					("Report", "Travel Trip Cost Summary", 1),
					("Report", "Travel Spend by Category", 1),
					("Report", "Unclaimed Travel Expenses", 1),
				],
				"Setup": [("DocType", "Travel Settings", 0)],
			},
		)

	def test_the_dead_expense_claim_type_link_is_gone(self):
		"""HRMS is not installed on production: the link rendered for nobody."""
		self.assertNotIn("Expense Claim", source(WORKSPACE))

	def test_every_content_id_is_the_hubs_own(self):
		ids = [b["id"] for b in content()]
		self.assertEqual(len(ids), len(set(ids)))
		for block_id in ids:
			self.assertTrue(block_id.startswith("travel_"), block_id)


# --------------------------------------------------------------------------- the sidebar


class TestTheSidebar(unittest.TestCase):
	def test_it_is_an_app_owned_travel_sidebar(self):
		doc = sidebar()
		self.assertEqual(doc["doctype"], "Workspace Sidebar")
		self.assertEqual((doc["name"], doc["title"]), ("Travel", "Travel"))
		self.assertEqual((doc["module"], doc["app"], doc["standard"]), ("Travel Management", "erpnext_enhancements", 1))

	def test_it_is_newer_than_the_row_it_replaces(self):
		self.assertGreater(sidebar()["modified"], OLD_SIDEBAR_STAMP)

	def test_it_opens_on_the_hub(self):
		first = sidebar()["items"][0]
		self.assertEqual((first["type"], first["link_type"], first["link_to"]), ("Link", "Workspace", "Travel"))

	def test_the_items_are_the_designed_ones(self):
		items = [(row["type"], row["label"]) for row in sidebar()["items"]]
		self.assertEqual(
			items,
			[
				("Link", "Home"),
				("Link", "My itinerary"),
				("Link", "Plan a Trip"),
				("Link", "Trips"),
				("Link", "Travel rules & per diem"),
				("Link", "Places & job sites"),
				("Section Break", "Office"),
				("Link", "Trip list"),
				("Link", "Trip Cost Summary"),
				("Link", "Spend by Category"),
				("Link", "Unclaimed Travel Expenses"),
				("Link", "Travel Settings"),
			],
		)

	def test_every_url_item_has_a_url(self):
		"""Production's generated sidebar carried two URL items whose url was NULL: links to
		nowhere."""
		urls = [row for row in sidebar()["items"] if row.get("link_type") == "URL"]
		self.assertEqual([row["label"] for row in urls], ["My itinerary", "Trips", "Travel rules & per diem"])
		for row in urls:
			with self.subTest(row["label"]):
				self.assertTrue((row.get("url") or "").startswith("/"), row)
				self.assertNotIn("link_to", row)

	def test_trips_opens_everyones_trips_like_the_shortcut(self):
		"""The same destination as the workspace's "Trips" shortcut, so the two cannot disagree."""
		items = {row["label"]: row for row in sidebar()["items"]}
		self.assertNotIn("My trips", items)
		trips = items["Trips"]
		self.assertEqual((trips["type"], trips["link_type"], trips["url"]), ("Link", "URL", ALL_TRIPS_URL))
		self.assertEqual(trips["child"], 0)
		shortcut = next(row for row in workspace()["shortcuts"] if row["label"] == "Trips")
		self.assertEqual(trips["url"], shortcut["url"])

	def test_a_travel_trip_page_keeps_this_sidebar(self):
		"""v16 picks the sidebar for a Travel Trip list, form or calendar by the sidebars that have an
		item linking to Travel Trip (sidebar.js resolve_sidebar), else the module's auto-generated
		one. When "My trips" became a URL (v1.556.0) this sidebar lost its only such item, and every
		trip page swapped it for frappe's "Travel Management" sidebar; "Trip list" under Office is the
		item that keeps it."""
		links = [
			row
			for row in sidebar()["items"]
			if row.get("link_type") == "DocType" and row.get("link_to") == "Travel Trip"
		]
		self.assertEqual([row["label"] for row in links], ["Trip list"])
		self.assertNotIn("report_ref_doctype", links[0])

	def test_plan_a_trip_is_there_and_new_travel_trip_is_not(self):
		items = sidebar()["items"]
		plan = [row for row in items if row["label"] == "Plan a Trip"]
		self.assertEqual(len(plan), 1)
		self.assertEqual((plan[0]["link_type"], plan[0]["link_to"]), ("Page", "plan-a-trip"))
		self.assertNotIn("New Travel Trip", [row["label"] for row in items])

	def test_the_office_group_nests_under_its_section(self):
		"""v16 attaches `child` items to the preceding Section Break, and only to one."""
		items = sidebar()["items"]
		office = next(i for i, row in enumerate(items) if row["type"] == "Section Break")
		self.assertEqual(items[office]["indent"], 1)
		self.assertEqual({row["child"] for row in items[:office]}, {0})
		self.assertEqual({row["child"] for row in items[office + 1 :]}, {1})

	def test_every_item_has_an_icon(self):
		"""An icon naming nothing renders as blank space; these two were tried and are absent."""
		for row in sidebar()["items"]:
			with self.subTest(row["label"]):
				self.assertTrue(row.get("icon"))
				self.assertNotIn(row["icon"], {"sitemap", "certificate"})

	def test_the_folder_holds_only_json(self):
		"""Frappe's model sync orjson-parses EVERY file in workspace_sidebar/: a README there
		once took production's migrate down."""
		stray = sorted(p.name for p in SIDEBAR_DIR.iterdir() if p.suffix != ".json")
		self.assertEqual(stray, [])


# --------------------------------------------------------------------------- the reload patch


def _install_frappe_stub(fail=()):
	"""A `frappe` just big enough for the patch; `fail` names the calls that raise."""
	state = {"reload": [], "imports": [], "errors": [], "cleared": 0}

	def reload_doc(*args, **kwargs):
		state["reload"].append((args, kwargs))
		if "reload" in fail:
			raise RuntimeError("reload refused")

	def import_file_by_path(path, **kwargs):
		state["imports"].append((path, kwargs))
		if "import" in fail:
			raise RuntimeError("bad json")

	def clear_cache(*args, **kwargs):
		state["cleared"] += 1
		if "cache" in fail:
			raise RuntimeError("redis gone")

	def log_error(title=None, message=None, **kwargs):
		state["errors"].append(title)

	frappe = types.ModuleType("frappe")
	frappe.reload_doc = reload_doc
	frappe.clear_cache = clear_cache
	frappe.log_error = log_error
	frappe.get_traceback = lambda *args, **kwargs: "Traceback (most recent call last): ..."
	frappe.get_app_path = lambda app, *parts: os.path.join("apps", app, app, *parts)
	modules = types.ModuleType("frappe.modules")
	import_file = types.ModuleType("frappe.modules.import_file")
	import_file.import_file_by_path = import_file_by_path
	modules.import_file = import_file
	frappe.modules = modules
	sys.modules["frappe"] = frappe
	sys.modules["frappe.modules"] = modules
	sys.modules["frappe.modules.import_file"] = import_file
	return state


class _PatchHarness:
	"""Runs ``MODULE``'s execute() against the stub, and puts sys.modules back afterwards."""

	MODULE = None

	def setUp(self):
		stubbed = ("frappe", "frappe.modules", "frappe.modules.import_file", self.MODULE)
		self._saved = {name: sys.modules.get(name) for name in stubbed}

	def tearDown(self):
		for name, module in self._saved.items():
			if module is None:
				sys.modules.pop(name, None)
			else:
				sys.modules[name] = module

	def _run(self, fail=()):
		state = _install_frappe_stub(fail)
		sys.modules.pop(self.MODULE, None)
		patch = importlib.import_module(self.MODULE)
		return patch.execute(), state


class TestTheReloadPatch(_PatchHarness, unittest.TestCase):
	MODULE = PATCH_MODULE

	def test_it_is_registered_after_the_patch_that_already_ran(self):
		"""reload_travel_workspace_for_plan_a_trip has run on production and never runs again:
		one workspace edit, one new patch."""
		text = source(PATCHES_TXT)
		lines = [line.strip() for line in text.splitlines()]
		self.assertIn(PATCH_MODULE, lines)
		self.assertLess(text.index("[post_model_sync]"), text.index(PATCH_MODULE))
		self.assertLess(
			text.index("erpnext_enhancements.patches.reload_travel_workspace_for_plan_a_trip"),
			text.index(PATCH_MODULE),
		)
		self.assertEqual(lines.count(PATCH_MODULE), 1)

	def test_it_reloads_the_workspace_and_imports_the_sidebar(self):
		result, state = self._run()
		self.assertIsNone(result)
		self.assertEqual(
			state["reload"],
			[
				(("travel_management", "workspace", "travel_management"), {"force": True}),
				(("travel_management", "report", "travel_trip_cost_summary"), {"force": True}),
			],
		)
		self.assertEqual(len(state["imports"]), 1)
		path, kwargs = state["imports"][0]
		self.assertEqual(kwargs, {"force": True})
		self.assertEqual(Path(path).parts[-2:], ("workspace_sidebar", "travel.json"))
		self.assertEqual(state["cleared"], 1)
		self.assertEqual(state["errors"], [])

	def test_nothing_it_calls_can_abort_the_migrate(self):
		"""A patch that raises aborts `bench migrate`, which on this repo IS the deploy."""
		result, state = self._run(fail=("reload", "import", "cache"))
		self.assertIsNone(result)
		# Anti-vacuity: each step was tried, and each failure was recorded rather than lost.
		self.assertEqual(len(state["reload"]), 2)
		self.assertEqual(len(state["imports"]), 1)
		self.assertEqual(state["cleared"], 1)
		self.assertEqual(
			state["errors"],
			[
				"Workspace reload failed: Travel",
				"Workspace sidebar reload failed: Travel",
				"Report reload failed: Travel Trip Cost Summary",
			],
		)

	def test_a_failed_workspace_does_not_skip_the_sidebar(self):
		_, state = self._run(fail=("reload",))
		self.assertEqual(len(state["imports"]), 1)
		self.assertEqual(
			state["errors"], ["Workspace reload failed: Travel", "Report reload failed: Travel Trip Cost Summary"]
		)

	def test_the_sidebar_is_not_reloaded_with_reload_doc(self):
		"""reload_doc takes a MODULE and looks for `<module>/workspace_sidebar/travel/travel.json`;
		app-level sidebars are flat files, so it fails — inside an except, in silence."""
		code = source(APP / "patches" / "reload_travel_hub.py")
		body = code[code.index("def execute") :]
		# Two reload_doc calls, the workspace and the cost report; neither is the sidebar.
		calls = re.findall(r"frappe\.reload_doc\(([^)]*)\)", body)
		self.assertEqual(len(calls), 2, calls)
		for args in calls:
			self.assertNotIn("workspace_sidebar", args)
		self.assertIn("import_file_by_path(", body)


class TestTheTripsReloadPatch(_PatchHarness, unittest.TestCase):
	"""v1.556.0 turned "My trips" into "Trips" on both the workspace and the sidebar. Both are
	timestamp-gated on import, and reload_travel_hub has already run on production, so the release
	carries a patch of its own: that patch's workspace and sidebar steps, and not its report step
	(the report did not change)."""

	MODULE = TRIPS_PATCH_MODULE

	def test_it_is_registered_once_after_reload_travel_hub(self):
		text = source(PATCHES_TXT)
		lines = [line.strip() for line in text.splitlines()]
		self.assertEqual(lines.count(TRIPS_PATCH_MODULE), 1)
		self.assertLess(text.index("[post_model_sync]"), text.index(TRIPS_PATCH_MODULE))
		self.assertLess(lines.index(PATCH_MODULE), lines.index(TRIPS_PATCH_MODULE))
		# Its comment says which release it belongs to and what it forces.
		index = lines.index(TRIPS_PATCH_MODULE)
		start = index
		while start > 0 and lines[start - 1].startswith("#"):
			start -= 1
		comment = lines[start:index]
		self.assertTrue(2 <= len(comment) <= 4, comment)
		self.assertTrue(comment[0].startswith("# v1.556.0"), comment[0])

	def test_both_files_it_forces_are_newer_than_the_rows_production_holds(self):
		"""The stamp is the half that does the work on its own on a site whose rows are older."""
		self.assertGreater(workspace()["modified"], OLD_WORKSPACE_STAMP)
		self.assertGreater(sidebar()["modified"], OLD_SIDEBAR_STAMP)

	def test_it_reloads_the_workspace_and_imports_the_sidebar(self):
		result, state = self._run()
		self.assertIsNone(result)
		workspace_reload = (("travel_management", "workspace", "travel_management"), {"force": True})
		self.assertEqual(state["reload"], [workspace_reload])
		self.assertEqual(len(state["imports"]), 1)
		path, kwargs = state["imports"][0]
		self.assertEqual(kwargs, {"force": True})
		self.assertEqual(Path(path).parts[-2:], ("workspace_sidebar", "travel.json"))
		self.assertEqual(state["cleared"], 1)
		self.assertEqual(state["errors"], [])

	def test_nothing_it_calls_can_abort_the_migrate(self):
		"""A patch that raises aborts `bench migrate`, which on this repo IS the deploy."""
		result, state = self._run(fail=("reload", "import", "cache"))
		self.assertIsNone(result)
		# Anti-vacuity: each step was tried, and each failure was recorded rather than lost.
		self.assertEqual(len(state["reload"]), 1)
		self.assertEqual(len(state["imports"]), 1)
		self.assertEqual(state["cleared"], 1)
		self.assertEqual(
			state["errors"], ["Workspace reload failed: Travel", "Workspace sidebar reload failed: Travel"]
		)

	def test_a_failed_workspace_does_not_skip_the_sidebar(self):
		_, state = self._run(fail=("reload",))
		self.assertEqual(len(state["imports"]), 1)
		self.assertEqual(state["cleared"], 1)
		self.assertEqual(state["errors"], ["Workspace reload failed: Travel"])

	def test_the_sidebar_is_not_reloaded_with_reload_doc(self):
		code = source(APP / "patches" / "reload_travel_hub_trips.py")
		body = code[code.index("def execute") :]
		calls = re.findall(r"frappe\.reload_doc\(([^)]*)\)", body)
		self.assertEqual(len(calls), 1, calls)
		self.assertNotIn("workspace_sidebar", calls[0])
		self.assertIn("import_file_by_path(", body)


# --------------------------------------------------------------------------- the cost report


class TestTheCostReportIsForCoordinators(unittest.TestCase):
	"""Crew see every part of a trip but its money, and Travel Trip Cost Summary is nothing but
	money: estimated, actual, variance, who paid, claimed, unclaimed. Its roles included Employee
	until v1.554.0, so any crew member could open it from the hub's Reports card (scoped to their own
	trips). Nik, 2026-09-28: "remove Trip Cost Summary from crew"."""

	PATH = APP / "travel_management" / "report" / "travel_trip_cost_summary" / "travel_trip_cost_summary.json"

	def test_only_the_travel_coordinators_open_it(self):
		report = json.loads(source(self.PATH))
		roles = {row["role"] for row in report["roles"]}
		self.assertNotIn("Employee", roles)
		# Exactly the roles api.travel._is_coordinator() treats as coordinators (Administrator is
		# every role). An empty list would NOT mean "nobody": frappe then falls back to read
		# permission on the ref_doctype, which every Employee has.
		self.assertEqual(roles, {"System Manager", "HR Manager", "Travel Coordinator"})
		init = source(APP / "travel_management" / "__init__.py")
		for role in roles:
			self.assertIn(f'"{role}"', init[init.index("TRAVEL_COORDINATOR_ROLES") :].split("}")[0])

	def test_its_stamp_is_newer_than_the_row_production_holds(self):
		"""A Report is timestamp-gated on import; production's row reads 2026-06-11 10:00:00."""
		report = json.loads(source(self.PATH))
		self.assertGreater(report["modified"], "2026-06-11 10:00:00.000000")


if __name__ == "__main__":
	unittest.main()
