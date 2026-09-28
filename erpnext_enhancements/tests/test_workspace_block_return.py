"""Every Custom HTML Block that fetches data reloads when you come back to its workspace.

v16's ``Workspace.show()`` returns early for the workspace already on screen
(``frappe/public/js/frappe/views/workspace/workspace.js:91``,
``if (this._page?.name === page.name) return;``). So from a dashboard to a form and back ran no
block script at all, and every data block showed what it loaded first until the browser tab was
reloaded. Thirty block headers said the opposite, "the workspace re-runs this whole script with a
fresh root on every navigation", which is why a stale dashboard read as the data being stale.

Since v1.556.3 a block hands its load to the desk-wide helper,
``public/js/global_enhancements/workspace_block_return.js``, which reloads it when the route comes
back. ``scripts/test_workspace_block_return.mjs`` runs the helper and every registering block in
node. This suite holds the parts a run cannot see:

* every seeded block whose script asks the server anything registers, or is in
  ``NOT_RELOADED`` with a reason, and nothing in that list registers after all;
* a registering block guards the call, so a device holding a bundle from before the helper still
  draws it, and binds no router handler of its own;
* the function a block registers can take the root as its first argument (``load(force)``
  handed over bare would recompute the KPIs on every return);
* no block file claims the old lifecycle, comments included: the comment is the whole bug;
* the helper ships in the desk bundle, and CI runs the node harness.

Reads files only; no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_workspace_block_return
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BLOCKS_DIR = APP / "custom_html_blocks"
SEEDER = APP / "setup" / "custom_html_blocks.py"
HELPER = APP / "public" / "js" / "global_enhancements" / "workspace_block_return.js"
BUNDLE = APP / "public" / "js" / "erpnext_enhancements.bundle.js"
README = BLOCKS_DIR / "README.md"
CI = APP.parent / ".github" / "workflows" / "ci.yml"
HARNESS = APP.parent / "scripts" / "test_workspace_block_return.mjs"

#: Blocks that deliberately do not reload on a return, and why. Each reason has to survive being
#: read aloud; a block listed here that registers anyway fails the build, so the list cannot rot.
NOT_RELOADED = {
	"desk_shortcuts": (
		"Asks the server nothing: it paints frappe.boot.ee_desk_shortcuts, which does not change "
		"until the desk itself reloads, so a return could only draw the same tiles."
	),
	"finance_astrology": (
		"One horoscope per sign per day, cached on the server, and the sign is picked in the block "
		"itself: asking again on every return would redraw the same words."
	),
}

#: What a block may be called when the helper hands it back: its root.
ROOT_PARAMS = {"container", "root", "root_element"}

#: The lifecycle the headers used to claim. Matched against comment text with the ``//`` and
#: line breaks taken out, so a claim wrapped across two comment lines is still one claim.
FALSE_CLAIMS = (
	r"every (workspace )?navigation",
	r"re-?runs? (this|the) whole script with a fresh root",
	r"navigations don't stack",
)

#: A call to the server, or to anybody's server: frappe.call / xcall (written ``frappe`` /
#: newline / ``.call(`` in most blocks) and fetch.
SERVER_CALL = re.compile(r"\.(?:x?call)\(|\bfetch\(")


def source(path):
	return path.read_text(encoding="utf-8")


def js_code(path):
	"""JS with comments stripped: the comments explain why things are absent, so they name them."""
	src = re.sub(r"/\*.*?\*/", "", source(path), flags=re.S)
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def prose(path):
	"""The whole file as one line: ``//`` and ``*`` comment markers out, whitespace collapsed."""
	text = re.sub(r"(^|\n)\s*(//+|/?\*+/?)", " ", source(path))
	return re.sub(r"\s+", " ", text)


def seeded_blocks():
	for node in ast.parse(source(SEEDER)).body:
		if isinstance(node, ast.Assign) and any(
			isinstance(target, ast.Name) and target.id == "BLOCKS" for target in node.targets
		):
			return ast.literal_eval(node.value)
	raise AssertionError("BLOCKS not found in setup/custom_html_blocks.py")


def block_js(prefix):
	return BLOCKS_DIR / f"{prefix}.js"


def registers(prefix):
	return "onWorkspaceReturn(" in js_code(block_js(prefix))


def fetches(prefix):
	return bool(SERVER_CALL.search(js_code(block_js(prefix))))


class TestEveryDataBlockReloads(unittest.TestCase):
	def test_the_scanner_sees_blocks_both_ways(self):
		"""A regex that matched nothing would pass every test below vacuously."""
		prefixes = [prefix for _name, prefix in seeded_blocks()]
		self.assertGreaterEqual(len(prefixes), 40)
		self.assertGreaterEqual(sum(fetches(prefix) for prefix in prefixes), 39)
		self.assertTrue(fetches("task_dashboard"), 'frappe.call("...") written as a string')
		self.assertTrue(fetches("finance_weather"))
		self.assertFalse(fetches("desk_shortcuts"))

	def test_every_block_that_fetches_registers_or_says_why_not(self):
		missing = [
			prefix
			for _name, prefix in seeded_blocks()
			if fetches(prefix) and not registers(prefix) and prefix not in NOT_RELOADED
		]
		self.assertEqual(
			missing,
			[],
			"these blocks fetch data and never reload on a return to their workspace (v16's "
			"Workspace.show() returns early); register with the helper or add a reason to NOT_RELOADED",
		)

	def test_the_not_reloaded_list_is_true(self):
		prefixes = {prefix for _name, prefix in seeded_blocks()}
		for prefix, reason in NOT_RELOADED.items():
			with self.subTest(prefix):
				self.assertIn(prefix, prefixes, "not a seeded block")
				self.assertFalse(registers(prefix), "listed as not reloaded, and registers")
				self.assertGreater(len(reason), 40)

	def test_a_registering_block_guards_the_helper_and_binds_nothing_itself(self):
		"""A device holding a bundle cached from before the helper has no helper: an unguarded
		call would throw inside the block and leave it blank. And a block's own router handler
		cannot be removed (v16's ``frappe.router.off`` wraps before it unbinds), so one per render
		would pile up; the helper binds the only one."""
		for _name, prefix in seeded_blocks():
			if not registers(prefix):
				continue
			with self.subTest(prefix):
				code = js_code(block_js(prefix))
				self.assertIn(
					"const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;",
					code,
				)
				self.assertIn("if (blocks && blocks.onWorkspaceReturn)", code)
				self.assertNotIn("frappe.router.on(", code)

	def test_what_a_block_registers_takes_the_root_first(self):
		"""The helper calls ``load(root)``. A load whose first parameter means something else
		(``load(force)``, ``load(sign)``) must be registered through a wrapper: handed over bare,
		``force`` would be the root, truthy, and every return would recompute."""
		pattern = re.compile(r"onWorkspaceReturn\(\s*[\w.]+\s*,\s*([A-Za-z_]\w*)\s*\)")
		seen = 0
		for _name, prefix in seeded_blocks():
			code = js_code(block_js(prefix))
			for fn in pattern.findall(code):
				seen += 1
				with self.subTest(block=prefix, fn=fn):
					match = re.search(rf"function {fn}\(([^)]*)\)", code)
					self.assertIsNotNone(match, f"{fn} is not a function declared in {prefix}.js")
					params = [p.strip() for p in match.group(1).split(",") if p.strip()]
					self.assertTrue(not params or params[0] in ROOT_PARAMS, f"{fn}({', '.join(params)})")
		self.assertGreaterEqual(seen, 35)

	def test_the_two_that_must_never_force_register_a_plain_read(self):
		for prefix in ("kpi_cockpit", "morning_briefing"):
			with self.subTest(prefix):
				code = js_code(block_js(prefix))
				self.assertIn("blocks.onWorkspaceReturn(container, () => {", code)
				self.assertIn("if (!refresh_btn.disabled) load(false);", code)

	def test_bank_balances_reloads_the_snapshot_never_plaid(self):
		code = js_code(block_js("finance_bank_balances"))
		self.assertIn("blocks.onWorkspaceReturn(container, load);", code)
		self.assertNotIn("onWorkspaceReturn(container, refreshNow)", code)


class TestNoBlockClaimsTheOldLifecycle(unittest.TestCase):
	def test_no_block_file_says_every_navigation_reruns_it(self):
		"""Comments included: the comment IS the bug. It told everyone who read a block that it
		was fresh on every visit, so a stale number read as stale data."""
		files = [*sorted(BLOCKS_DIR.glob("*.js")), README]
		self.assertGreaterEqual(len(files), 40)
		for path in files:
			text = prose(path).lower()
			for claim in FALSE_CLAIMS:
				with self.subTest(file=path.name, claim=claim):
					self.assertIsNone(re.search(claim, text), f"{path.name} still claims: {claim}")

	def test_the_claim_matcher_matches_the_old_wording(self):
		"""The old header, wrapped across comment lines as it was, must be caught."""
		old = (
			"// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace\n"
			"// re-runs this whole script with a fresh root on every navigation — so nothing\n"
		)
		text = re.sub(r"\s+", " ", re.sub(r"(^|\n)\s*//+", " ", old)).lower()
		self.assertTrue(all(re.search(claim, text) for claim in FALSE_CLAIMS[:2]))


class TestTheHelperShips(unittest.TestCase):
	def test_it_is_in_the_desk_bundle(self):
		self.assertIn('import "./global_enhancements/workspace_block_return.js";', source(BUNDLE))

	def test_it_binds_one_handler_and_never_unbinds(self):
		code = js_code(HELPER)
		self.assertEqual(code.count('router.on("change"'), 1)
		self.assertNotIn(".off(", code)
		self.assertIn('frappe.provide("erpnext_enhancements.workspace_blocks")', code)

	def test_ci_runs_the_node_harness(self):
		self.assertTrue(HARNESS.exists())
		# assertTrue, not assertIn: a miss would print all of ci.yml.
		self.assertTrue("node scripts/test_workspace_block_return.mjs" in source(CI), "ci.yml never runs it")

	def test_the_readme_documents_it(self):
		text = source(README)
		self.assertIn("## When a block script runs", text)
		self.assertIn("workspace_block_return.js", text)
		for prefix in NOT_RELOADED:
			with self.subTest(prefix):
				label = {"desk_shortcuts": "Desk Shortcuts", "finance_astrology": "Finance Astrology"}[prefix]
				self.assertIn(label, text)


if __name__ == "__main__":
	unittest.main()
