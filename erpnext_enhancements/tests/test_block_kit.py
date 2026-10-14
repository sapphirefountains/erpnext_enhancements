"""Custom HTML Blocks on sf_kit carry no shell of their own (v1.649.0).

Every block used to repeat the same shell: an HTML escape (``function esc``), a ``muted()``
helper writing its loading / empty / error lines, a ``root.__ee_ticket`` counter so only the
newest answer draws, and ``-pill`` markup for its status labels. The blocks listed in
``ON_KIT`` now take all of it from one place:

* ``blocks.kit(root)`` (``public/js/global_enhancements/workspace_block_return.js``) loads
  ``sf_kit.bundle.js`` once, adopts ``sf_kit.CSS`` into the block's shadow root and resolves the
  kit, or a plain-text stand-in when it could not load;
* ``blocks.ticket(root)`` is the counter;
* panels are ``sf.state.render``, labels ``sf.chip``, escaping ``sf.esc``.

This fails the build when a converted block grows any of those copies back, writes a loading,
error or switched-off line by hand, or loses its guard for a device whose bundle predates the
helpers. It also makes every seeded block choose a side: on the kit, or in ``NOT_YET`` with the
reason it is still waiting. ``scripts/test_workspace_block_return.mjs`` runs the converted
blocks on the real kit.

Comments are stripped first: a header that explains why ``esc`` is gone names ``esc``.

Bench-free: the files are read as text.

Run: python -m unittest erpnext_enhancements.tests.test_block_kit
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BLOCKS_DIR = APP / "custom_html_blocks"
SEEDER = APP / "setup" / "custom_html_blocks.py"
HELPER = APP / "public" / "js" / "global_enhancements" / "workspace_block_return.js"
README = BLOCKS_DIR / "README.md"
CI = APP.parent / ".github" / "workflows" / "ci.yml"

#: The blocks on sf_kit: the 34 department dashboard widgets (v1.649.0), then the other six.
ON_KIT = (
	"design_handoff_readiness",
	"design_hydraulic_headroom",
	"design_signoff_queue",
	"design_wip_board",
	"executive_bookings_backlog",
	"executive_cash_runway",
	"executive_risk_queue",
	"executive_scorecard",
	"finance_astrology",
	"finance_bank_balances",
	"finance_calendar",
	"finance_new_jobs",
	"finance_weather",
	"finance_whos_working",
	"hr_headcount_movement",
	"hr_people_calendar",
	"hr_time_capture",
	"hr_training_compliance",
	"marketing_channel_spend",
	"marketing_funnel_cascade",
	"marketing_source_health",
	"marketing_unsourced_leads",
	"operations_fleet_health",
	"production_hours_variance",
	"production_material_readiness",
	"production_milestone_slippage",
	"production_wip_aging",
	"sales_handoff_backlog",
	"sales_renewal_radar",
	"sales_speed_to_lead",
	"sales_stalled_deals",
	"service_chemistry_alerts",
	"service_day_board",
	"service_labor_capture",
	# The rest, since v1.658.0: the cockpit, the briefing, the morning TV screen, the
	# Projects Dashboard, and the two per-viewer blocks.
	"kpi_cockpit",
	"morning_briefing",
	"projects_dashboard",
	"task_dashboard",
	"training_my_dashboard",
	"travel_home",
	# The site-made "Home Dashboard Tasks", on the kit from the day it came into the repo
	# (v1.681.0).
	"my_priority_tasks",
)

#: Blocks not converted yet, and why. Move one to ON_KIT in the change that converts it.
NOT_YET = {
	"desk_shortcuts": "Asks the server nothing and builds its tiles with createElement: no panel, no escape.",
	"dashboard_hub_links": (
		"Asks the server nothing: it reads frappe.workspaces and builds its links with createElement, "
		"so it has no panel to draw and nothing to escape."
	),
}

#: What a converted block must not contain, and why.
BANNED = (
	(r"\bfunction\s+esc\s*\(", "its own escape: use sf.esc"),
	(r"\b(?:const|let|var)\s+esc\s*=", "its own escape: use sf.esc"),
	(r"escape_html\s*\(", "a call to frappe.utils.escape_html: use sf.esc"),
	(r"\bmuted\s*\(", "a muted() helper: panels are sf.state.render"),
	(r"__ee_ticket", "its own ticket counter: use blocks.ticket"),
	(r"class=\"\w+-pill", "-pill markup: use sf.chip"),
	(
		r"(?:innerHTML|textContent)\s*=[^;]*?(?:Loading|Refreshing|Could not load|turned off|unavailable\.)",
		"a hand-written loading / error / switched-off line: use sf.state.render",
	),
)

#: What every converted block must contain.
REQUIRED = (
	"const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;",
	"if (blocks && blocks.kit && blocks.ticket) {",
	"blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));",
	# The older bundle's stand-in: it must draw (plain text) and not throw.
	"startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);",
	# ... in words, never the raw kind ("no-access") on screen.
	'const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || words[kind] || "");',
	'"no-access": __("No access"),',
	"sf.state.render(",
	'"no-access"',
	'"error", { title, on_action: ',
)


#: Joins of server values that are safe for a reason the scanner cannot see: (file, variable)
#: -> why. Empty today; add an entry only with a reason that survives being read aloud.
JOIN_ALLOWED = {}

# `const meta = [ ... ]` then a chain (.filter / .map ...) then `.join(`.
JOINED = re.compile(
	r"const\s+(\w+)\s*=\s*\[((?:[^\[\]]|\[[^\[\]]*\])*)\]((?:\s*\.(?!join\b)\w+\((?:[^()]|\([^()]*\))*\))*)\s*\.join\("
)
# Calls whose result is safe to put in markup; their arguments are not looked at.
SAFE_CALL = re.compile(r"\b(?:sf\.esc|sf\.chip|encodeURIComponent)\((?:[^()]|\([^()]*\))*\)")
# A test written on a value, which is not what reaches the markup: `d.x ? a : b`, `d.x && a`.
CONDITION = re.compile(r"[\w.]+\s*(?:\?|&&)")
# A field of a row (`d.customer`, `row.owner`) or of the answer (`message.reason`).
SERVER_VALUE = re.compile(r"\b(?:[a-z]|row|message)\.[A-Za-z_]\w*\b")


def unescaped_joins(text):
	"""(variable, field) for each joined list that puts a server value into markup unescaped."""
	found = []
	for match in JOINED.finditer(text):
		name, items, chain = match.groups()
		if ".map(sf.esc)" in chain or f"sf.esc({name})" in text or f"textContent = {name}" in text:
			continue
		bare = SERVER_VALUE.findall(CONDITION.sub(" ", SAFE_CALL.sub(" ", items)))
		found.extend((name, field) for field in bare)
	return found


def strip_comments(text):
	"""Block comments, then // line comments not preceded by ':' or a quote (so a URL stays)."""
	text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
	return re.sub(r"(^|[^:\"'\\])//[^\n]*", r"\1", text)


def code(prefix):
	return strip_comments((BLOCKS_DIR / f"{prefix}.js").read_text(encoding="utf-8"))


def seeded_prefixes():
	for node in ast.parse(SEEDER.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign) and any(
			isinstance(target, ast.Name) and target.id == "BLOCKS" for target in node.targets
		):
			return {prefix for _name, prefix in ast.literal_eval(node.value)}
	raise AssertionError("BLOCKS not found in setup/custom_html_blocks.py")


def violations(text):
	return [why for pattern, why in BANNED if re.search(pattern, text)]


class TestTheScanner(unittest.TestCase):
	"""Each rule must catch what it is for; a scanner that finds nothing proves nothing."""

	def test_each_banned_shape_is_caught(self):
		samples = {
			"function esc(value) { return value; }": "its own escape",
			"const esc = frappe.utils.escape_html;": "its own escape",
			"x = frappe.utils.escape_html(v);": "escape_html",
			'body.innerHTML = muted(__("No rows."));': "muted()",
			"const t = (root.__ee_ticket = (root.__ee_ticket || 0) + 1);": "ticket",
			'`<span class="ssd-pill ssd-pill-bad">${x}</span>`': "-pill",
			'body.innerHTML = `<div class="x-muted">${__("Loading…")}</div>`;': "loading",
			'body.innerHTML = `<div>${__("Could not load the board.")}</div>`;': "error",
			'body.textContent = __("Sales is turned off in Settings.");': "switched-off",
		}
		for sample, label in samples.items():
			with self.subTest(label):
				self.assertTrue(violations(strip_comments(sample)), sample)

	def test_the_kit_calls_are_not_mistaken_for_copies(self):
		clean = (
			'sf.state.render(body, "empty", { title: __("Sales is turned off.") });\n'
			'const title = __("Could not load the board.");\n'
			'sf.state.render(body, "error", { title, on_action: load });\n'
			"const meta = list.map(sf.esc).join(' ');\n"
			'const pill = sf.chip(__("Open"), "warn");\n'
			"const escape = frappe.utils.escape_html;\n"
		)
		self.assertEqual(violations(strip_comments(clean)), [])

	def test_an_unescaped_join_is_caught(self):
		# Design Sign-Off Queue before v1.649.0: the customer reached innerHTML as typed.
		bug = 'const meta = [d.customer, state].filter(Boolean).join(" · ");'
		self.assertEqual(unescaped_joins(bug), [("meta", "d.customer")])
		wrapped = 'const meta = [\n    row.owner,\n    __("{0} h", [n]),\n]\n    .filter(Boolean)\n    .join(" · ");'
		self.assertEqual(unescaped_joins(wrapped), [("meta", "row.owner")])
		self.assertEqual(
			unescaped_joins('const m = [message.reason, x].join(" ");'), [("m", "message.reason")]
		)

	def test_an_escaped_join_is_not(self):
		for safe in (
			'const meta = [d.customer && sf.esc(d.customer), state].filter(Boolean).join(" · ");',
			'const meta = [d.owner, money(d.amount)].filter(Boolean).map(sf.esc).join(" · ");',
			'const meta = [d.a, b]\n    .filter(Boolean)\n    .map(sf.esc)\n    .join(" · ");',
			'const note = [f.source, f.as_of].filter(Boolean).join(" · "); x = `${sf.esc(note)}`;',
			'const note = [trend, f.stale ? __("stale") : ""].filter(Boolean).join(" · ");',
			'const sub = [sf.esc(a.subtype || a.type || ""), mask].filter(Boolean).join(" ");',
		):
			with self.subTest(safe):
				self.assertEqual(unescaped_joins(safe), [])

	def test_a_comment_neither_trips_nor_satisfies_it(self):
		self.assertEqual(violations(strip_comments("// was: function esc(value) and muted()\n")), [])
		self.assertNotIn("blocks.kit(", strip_comments("// blocks.kit(container).then(...)\n"))


class TestEveryBlockChooses(unittest.TestCase):
	def test_every_seeded_block_is_on_the_kit_or_says_why_not(self):
		seeded = seeded_prefixes()
		self.assertGreaterEqual(len(seeded), 41)
		self.assertEqual(sorted(seeded - set(ON_KIT) - set(NOT_YET)), [], "in neither list")
		self.assertEqual(sorted(set(ON_KIT) & set(NOT_YET)), [], "in both lists")
		self.assertEqual(sorted((set(ON_KIT) | set(NOT_YET)) - seeded), [], "not a seeded block")

	def test_a_block_waiting_is_really_not_converted(self):
		for prefix in NOT_YET:
			with self.subTest(prefix):
				self.assertNotIn("blocks.kit(", code(prefix), "converted: move it to ON_KIT")


class TestConvertedBlocksCarryNoShell(unittest.TestCase):
	def test_no_converted_block_brings_its_own_copy_back(self):
		for prefix in ON_KIT:
			text = code(prefix)
			for why in violations(text):
				with self.subTest(block=prefix, rule=why):
					self.fail(f"{prefix}.js: {why}")

	def test_no_joined_list_puts_a_server_value_into_markup_unescaped(self):
		"""``[d.customer, state].join(" · ")`` into innerHTML is markup injection by a customer
		name. Escape each value (``.map(sf.esc)``, or ``sf.esc(...)`` on the one that is text) or
		the joined result."""
		seen = 0
		for prefix in ON_KIT:
			text = code(prefix)
			seen += len(JOINED.findall(text))
			for name, field in unescaped_joins(text):
				if (prefix, name) in JOIN_ALLOWED:
					continue
				with self.subTest(block=prefix, variable=name, field=field):
					self.fail(f"{prefix}.js: {field} is joined into `{name}` unescaped")
		self.assertGreaterEqual(seen, 20, "the join scanner found almost nothing to look at")

	def test_each_takes_the_kit_through_the_guarded_helper(self):
		for prefix in ON_KIT:
			text = code(prefix)
			for needle in REQUIRED:
				with self.subTest(block=prefix, needle=needle):
					self.assertTrue(needle in text, f"{prefix}.js lacks: {needle}")

	def test_each_error_title_is_handed_to_the_kit(self):
		"""Every "Could not load" sentence is the error panel's title, not markup."""
		for prefix in ON_KIT:
			for line in code(prefix).splitlines():
				# The older bundle's word for an error panel is the one exception.
				if "Could not load" in line and line.strip() != 'error: __("Could not load"),':
					with self.subTest(block=prefix, line=line.strip()):
						self.assertRegex(line, r"const title = __\(\"Could not load ")


class TestTheHelper(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.code = strip_comments(HELPER.read_text(encoding="utf-8"))

	def test_it_exports_kit_and_ticket(self):
		self.assertIn("api.kit = function (root)", self.code)
		self.assertIn("api.ticket = function (root)", self.code)

	def test_the_kit_is_required_once_by_its_bundle_name(self):
		# A bundle name, never a raw /assets path (the year-long immutable cache in CLAUDE.md).
		self.assertIn('const KIT_ASSET = "sf_kit.bundle.js";', self.code)
		self.assertEqual(self.code.count("frappe.require("), 1)
		self.assertIn("if (kitLoaded) return kitLoaded;", self.code)
		self.assertNotIn("/assets/", self.code)

	def test_the_ticket_keeps_the_counter_on_the_root(self):
		ticket = self.code[self.code.index("api.ticket = function (root)") :]
		ticket = ticket[: ticket.index("\n\t};\n")]
		self.assertIn("holder.__ee_ticket = (holder.__ee_ticket || 0) + 1", ticket)
		self.assertIn("return () => holder.__ee_ticket === mine;", ticket)

	def test_ci_and_the_readme_know_about_it(self):
		self.assertTrue(
			"python -m unittest erpnext_enhancements.tests.test_block_kit" in CI.read_text(encoding="utf-8"),
			"ci.yml never runs this suite",
		)
		readme = README.read_text(encoding="utf-8")
		self.assertIn("blocks.kit(", readme)
		self.assertIn("blocks.ticket(", readme)


if __name__ == "__main__":
	unittest.main()
