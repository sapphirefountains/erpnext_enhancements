# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free checks for the Fountain Designer page (phase F of the rebuild, TASK-2026-03018).

The page is ``water_engineering/page/fountain_designer`` with its code in
``public/js/fountain_designer/`` (``fountain_designer.bundle.js``, the demo fixture in its own
``fountain_designer_demo.bundle.js``) and its styles in ``public/css/fountain_designer.bundle.css``.
Its server side, phase E's ``api/designer.py``, is built after it against the contract in
``public/js/fountain_designer/README.md``; until then ``?demo=1`` answers from the fixture.

Two halves:

* **Under node**, ``scripts/test_fountain_designer.mjs``: the bundle entry, the route parsing and the
  address it writes, the quick and full step lists, the debounced derive and its stale-reply guard,
  the model editing helpers (pinning, presets, merge_derived), the chart sanitizer, the verdict
  panel, every step rendered for every demo design, and the demo answering every endpoint. This runs
  it when node is on PATH; CI runs it as its own step too and has node, so there it never skips.
* **Here, from the files**: the Page record and where it sits; the bundles loaded by name and never
  globally; the stylesheet on the app's tokens (no literal color, no shadow, the two allowed media
  queries, every selector ``.fd-``); the workspace shortcut and Tools link and the patch that
  re-imports them; the Dock row and the Design profile; the README contract naming every endpoint
  and every key of the fixture's replies.

Run: python -m unittest erpnext_enhancements.tests.test_fountain_designer -v
"""

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
PAGE_DIR = APP / "water_engineering" / "page" / "fountain_designer"
PAGE_JSON = PAGE_DIR / "fountain_designer.json"
PAGE_JS = PAGE_DIR / "fountain_designer.js"
PUBLIC = APP / "public"
FD = PUBLIC / "js" / "fountain_designer"
BUNDLE = PUBLIC / "js" / "fountain_designer.bundle.js"
DEMO_BUNDLE = PUBLIC / "js" / "fountain_designer_demo.bundle.js"
CSS_FILE = PUBLIC / "css" / "fountain_designer.bundle.css"
TOKENS = PUBLIC / "css" / "sf_tokens.bundle.css"
README = FD / "README.md"
HOOKS = APP / "hooks.py"
WORKSPACE = APP / "water_engineering" / "workspace" / "water_engineering" / "water_engineering.json"
PATCH = APP / "patches" / "fountain_designer_workspace_link.py"
PATCHES_TXT = APP / "patches.txt"
DOCK = APP / "dock" / "erpnext_enhancements" / "erpnext_enhancements.json"
DOCK_PROFILES = APP / "setup" / "dock_profiles.py"
CI = REPO / ".github" / "workflows" / "ci.yml"
HARNESS = REPO / "scripts" / "test_fountain_designer.mjs"

ROUTE = "fountain-designer"
MODULES = ("model", "router", "api", "verdict", "chart", "ui", "app")
STEPS = ("start", "site", "features", "basins", "pump_room", "review", "outputs")
ENDPOINTS = (
	"boot",
	"derive",
	"get_design",
	"save_design",
	"push_quotation",
	"push_material_request",
	"catalog_search",
	"acknowledge_issue",
)
ALLOWED_MEDIA = {"(max-width: 767px)", "(prefers-reduced-motion: reduce)"}
COLOUR_PROPS = re.compile(
	r"^(color|background(-color)?|border(-(top|right|bottom|left))?(-color)?|outline(-color)?"
	r"|fill|stroke|text-decoration(-color)?|caret-color|accent-color)$"
)
ALLOWED_WORDS = {"solid", "dashed", "none", "transparent", "currentColor", "currentcolor", "inherit", "underline"}


def _strip_js_comments(text):
	text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
	return re.sub(r"(^|[^:\"'`])//[^\n]*", r"\1", text)


def _css():
	return re.sub(r"/\*.*?\*/", "", CSS_FILE.read_text(encoding="utf-8"), flags=re.S)


def _declarations(css):
	out = []
	for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
		for prop, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body):
			out.append((selector.strip(), prop.strip(), value.strip()))
	return out


def _json(path):
	return json.loads(path.read_text(encoding="utf-8"))


class TestThePage(unittest.TestCase):
	def test_the_page_record(self):
		page = _json(PAGE_JSON)
		self.assertEqual(page["doctype"], "Page")
		self.assertEqual(page["name"], ROUTE)
		self.assertEqual(page["module"], "Water Engineering")
		self.assertEqual(page["title"], "Fountain Designer", "the awesomebar indexes pages by title")
		self.assertEqual(page["standard"], "Yes")
		# Gated like the old wizard: on read of Water Feature Design, on the server, not by role here.
		self.assertEqual(page["roles"], [])

	def test_no_hyphen_in_a_file_frappe_imports(self):
		for path in PAGE_DIR.iterdir():
			with self.subTest(path.name):
				self.assertNotIn("-", path.name)
		self.assertTrue((PAGE_DIR / "__init__.py").exists())

	def test_the_page_loads_the_bundles_by_name(self):
		code = _strip_js_comments(PAGE_JS.read_text(encoding="utf-8"))
		for name in ("sf_kit.bundle.js", "sf_kit.bundle.css", "fountain_designer.bundle.js", "fountain_designer.bundle.css"):
			self.assertIn(f'"{name}"', code)
		self.assertIn('"fountain_designer_demo.bundle.js"', code, "the fixture is its own bundle, fetched in demo mode")
		self.assertNotIn("/assets/", code, "never a raw /assets path (CLAUDE.md)")
		self.assertIn(f'frappe.pages["{ROUTE}"].on_page_load', code)
		self.assertIn(f'frappe.pages["{ROUTE}"].on_page_show', code)

	def test_the_bundles_are_never_global(self):
		hooks = HOOKS.read_text(encoding="utf-8")
		for name in ("fountain_designer.bundle", "fountain_designer_demo.bundle"):
			self.assertNotIn(name, hooks, "loaded by the page alone, never app_include_*")

	def test_the_entry_imports_every_module(self):
		entry = BUNDLE.read_text(encoding="utf-8")
		for name in MODULES[:5]:
			self.assertIn(f'"./fountain_designer/{name}.js"', entry)
		self.assertIn('import { mount } from "./fountain_designer/app.js"', entry)
		app = (FD / "app.js").read_text(encoding="utf-8")
		for step in STEPS:
			self.assertIn(f'"./steps/{step}.js"', app)

	def test_a_module_per_step_and_a_sane_file_count(self):
		self.assertEqual(sorted(p.stem for p in (FD / "steps").glob("*.js")), sorted(STEPS))
		js = list(FD.rglob("*.js"))
		self.assertLessEqual(len(js), 16, "a module per step plus the shared few")

	def test_every_call_is_quiet_and_names_the_contract(self):
		api = _strip_js_comments((FD / "api.js").read_text(encoding="utf-8"))
		self.assertIn('"erpnext_enhancements.water_engineering.api.designer."', api)
		self.assertIn("quiet: true", api)
		for name in ENDPOINTS:
			self.assertRegex(api, rf'send\("{name}"', name)
		self.assertIn("api.water_design.check_permission", api, "the gate the wizard uses")
		for path in FD.rglob("*.js"):
			code = _strip_js_comments(path.read_text(encoding="utf-8"))
			with self.subTest(path.name):
				self.assertNotIn("frappe.call(", code, "every call goes through sf_kit's quiet path")
				self.assertNotIn("frappe.utils.cint", code)
				self.assertNotIn("innerHTML = reply", code)

	def test_a_design_not_made_here_is_never_opened_blank(self):
		"""A blank model in its place would be autosaved over the stored record."""
		code = _strip_js_comments((FD / "app.js").read_text(encoding="utf-8"))
		at = code.index("open_design(name) {")
		self.assertNotIn("blank_model", code[at : code.index("load_designs() {")])
		self.assertIn("this.cannot_open(name)", code)

	def test_modules_touch_no_globals_at_import(self):
		"""node runs them; window and document come in through env."""
		for name in (*MODULES[:6], *(f"steps/{s}" for s in STEPS), "demo/demo"):
			code = _strip_js_comments((FD / f"{name}.js").read_text(encoding="utf-8"))
			with self.subTest(name):
				self.assertNotRegex(code, r"\bwindow\.|\bdocument\.", "use env, so node can run it")

	def test_american_english(self):
		for path in [*FD.rglob("*.js"), CSS_FILE, README, PAGE_JS]:
			text = path.read_text(encoding="utf-8")
			with self.subTest(path.name):
				self.assertNotRegex(text, r"\b[Cc]olour|\bCancelled\b|\bbehaviour")


class TestTheStylesheet(unittest.TestCase):
	def setUp(self):
		self.css = _css()

	def test_every_selector_is_the_pages_own(self):
		css = re.sub(r"@media[^{]*\{", "", self.css)
		for selector, _prop, _value in _declarations(css):
			for part in selector.split(","):
				part = part.strip()
				if not part or part.startswith("}"):
					continue
				with self.subTest(selector=part):
					self.assertRegex(part, r"^\.fd-", "a Desk page's stylesheet is global once loaded")

	def test_colors_are_tokens(self):
		self.assertNotRegex(self.css, r"#[0-9a-fA-F]{3,8}\b", "no hex")
		self.assertNotRegex(self.css, r"\b(rgba?|hsla?|hwb|oklch|oklab)\(", "no color function")
		for selector, prop, value in _declarations(self.css):
			if not COLOUR_PROPS.match(prop):
				continue
			words = set(re.findall(r"[A-Za-z][\w-]*", re.sub(r"var\(--sf-[\w-]+\)", "", value)))
			words -= {"px", "calc"}
			words = {w for w in words if not re.fullmatch(r"\d*px|\d+", w)}
			with self.subTest(selector=selector, prop=prop):
				self.assertTrue(words <= ALLOWED_WORDS, f"{prop}: {value}")

	def test_every_token_exists(self):
		tokens = set(re.findall(r"(--sf-[\w-]+)\s*:", TOKENS.read_text(encoding="utf-8")))
		for name in sorted(set(re.findall(r"var\((--sf-[\w-]+)", self.css))):
			with self.subTest(name):
				self.assertIn(name, tokens)

	def test_no_shadow_and_square_structure(self):
		self.assertNotIn("shadow", self.css)
		for _selector, prop, value in _declarations(self.css):
			if prop == "border-radius":
				with self.subTest(value=value):
					self.assertRegex(value, r"^var\(--sf-radius-[\w-]+\)$")

	def test_only_the_two_media_queries(self):
		for query in re.findall(r"@media\s*([^{]+)\{", self.css):
			with self.subTest(query=query):
				self.assertIn(query.strip(), ALLOWED_MEDIA)

	def test_touch_targets_and_input_size(self):
		self.assertIn("min-height: var(--sf-touch-min)", self.css)
		self.assertRegex(self.css, r"\.fd-input\s*\{[^}]*font-size: var\(--sf-text-input\)", "iOS zooms below 16px")


class TestWhereItIsFound(unittest.TestCase):
	def test_the_workspace_has_a_shortcut_and_a_tools_link(self):
		ws = _json(WORKSPACE)
		self.assertIn({"label": "Fountain Designer", "link_to": ROUTE, "type": "Page"}, ws["shortcuts"])
		content = json.loads(ws["content"])
		self.assertIn("Fountain Designer", [b["data"].get("shortcut_name") for b in content if b["type"] == "shortcut"])
		links = ws["links"]
		at = next(i for i, l in enumerate(links) if l["type"] == "Card Break" and l["label"] == "Tools")
		tools = []
		for row in links[at + 1 :]:
			if row["type"] == "Card Break":
				break
			tools.append(row)
		self.assertEqual(links[at]["link_count"], len(tools))
		self.assertIn(ROUTE, [r["link_to"] for r in tools])
		self.assertGreaterEqual(ws["modified"], "2026-11-01 12:00:00.000000", "the age gate skips an unbumped file")

	def test_the_patch_reloads_it_and_cannot_raise(self):
		self.assertIn("erpnext_enhancements.patches.fountain_designer_workspace_link", PATCHES_TXT.read_text(encoding="utf-8"))
		code = PATCH.read_text(encoding="utf-8")
		self.assertIn('frappe.reload_doc("water_engineering", "workspace", "water_engineering", force=True)', code)
		self.assertEqual(code.count("try:"), code.count("except Exception"), "every step wrapped")
		self.assertIn("fountain_designer_workspace_link", (APP / "patches" / "README.md").read_text(encoding="utf-8"))

	def test_the_dock_row_and_the_design_profile(self):
		rows = _json(DOCK)["items"]
		self.assertIn(f"/desk/{ROUTE}", [r.get("url") for r in rows])
		code = DOCK_PROFILES.read_text(encoding="utf-8")
		self.assertIn(f'FOUNTAIN_DESIGNER = ("URL", "/desk/{ROUTE}")', code)
		self.assertRegex(code, r'"Design": \([^)]*FOUNTAIN_DESIGNER')


class TestTheContract(unittest.TestCase):
	def setUp(self):
		self.readme = README.read_text(encoding="utf-8")
		self.replies = [_json(FD / "demo" / f"derive_{n}.json") for n in ("lobby", "plaza", "pond")]

	def test_every_endpoint_is_written_down(self):
		for name in ENDPOINTS:
			with self.subTest(name):
				self.assertIn(f"`{name}(", self.readme)

	def test_every_reply_key_is_written_down(self):
		"""The README is what phase E builds against: a key the fixture uses and the README does not
		name is a key E will not send."""
		keys = set()
		for reply in self.replies:
			keys |= set(reply)
			keys |= set(reply["rollups"])
			keys |= set(reply["verdict"])
			keys |= set(reply["charts"])
			keys |= set(reply["outputs"])
			for issue in reply["issues"]:
				keys |= set(issue)
			for line in reply["bom"]:
				keys |= set(line)
		for key in sorted(keys):
			with self.subTest(key):
				self.assertIn(key, self.readme)

	def test_every_boot_key_is_written_down(self):
		boot = _json(FD / "demo" / "boot.json")
		for key in [*boot, *boot["can"], *boot["options"], *boot["catalogs"], *boot["catalogs"]["slot_classes"]]:
			with self.subTest(key):
				self.assertIn(key, self.readme)

	def test_the_fixture_has_a_duty_chart_per_design(self):
		for reply in self.replies:
			with self.subTest(reply["model"]["kit"]):
				self.assertTrue(reply["charts"]["duty_svg"].startswith("<svg"))
				self.assertEqual(reply["model"]["schema"], 2)

	def test_the_schema_additions_are_called_out(self):
		for addition in ('"preset"', "presets", "pump_room", "winterization"):
			with self.subTest(addition):
				self.assertIn(addition, self.readme)

	def test_ci_runs_both_halves(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("node scripts/test_fountain_designer.mjs", ci)
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_fountain_designer -v", ci)


class TestUnderNode(unittest.TestCase):
	def setUp(self):
		if not shutil.which("node"):
			if os.environ.get("CI"):
				self.fail("node is on every CI runner; a skip there would hide a broken page")
			self.skipTest("node is not on PATH")

	def test_the_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), "--no-warnings", str(HARNESS)],
			capture_output=True,
			text=True,
			encoding="utf-8",
			timeout=300,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
	unittest.main()
