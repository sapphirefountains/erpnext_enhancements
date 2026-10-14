"""Custom pages S4c (v1.679.0, TASK-2026-02806): the "Pages" search section and page shortcuts.

``public/js/global_enhancements/page_search.js`` lists this app's main pages under their titles
and short aliases in the awesomebar and the search dialog, and binds six Alt+Shift shortcuts.
Its behaviour runs in ``scripts/test_page_search.js`` (node, against a stubbed frappe). This
file holds what a run against stubs cannot see, all of it failing in silence otherwise:

* Every Desk page the map names exists. A renamed page drops out of ``frappe.boot.page_info``
  and so out of search, with nothing to say why.
* Every www page it names has a template, and Stock Scan's roles are the ones its controller
  checks (``stock_scan_rules.SCAN_ROLES``). A role the map has and the controller lacks offers a
  page that answers "not permitted"; the reverse hides it from people who may use it.
* The six chords are spelled the way ``frappe.ui.keys.get_key`` spells them, are distinct, and
  appear nowhere else in this app's JavaScript. ``add_shortcut`` replaces every global handler
  on its chord, so a second binding anywhere silently takes the key from the other.
* The module is in the global bundle, on the line after the sf_kit loader.
* The wraps of Frappe's search keep their guards: a missing hook point patches nothing, and
  anything that throws returns Frappe's own results.

Bench-free: no frappe import. Run: python -m unittest erpnext_enhancements.tests.test_custom_pages_s4c
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
JS = APP / "public" / "js"
MODULE = JS / "global_enhancements" / "page_search.js"
BUNDLE = JS / "erpnext_enhancements.bundle.js"
CI = REPO / ".github" / "workflows" / "ci.yml"
SCAN_RULES = APP / "inventory_enhancements" / "stock_scan_rules.py"

EXPECTED_SHORTCUTS = {
	"project-planner": "alt+shift+p",
	"maintenance-planner": "alt+shift+m",
	"maintenance-day-board": "alt+shift+d",
	"rental-planner": "alt+shift+r",
	"plan-a-trip": "alt+shift+j",
	"/stock-scan": "alt+shift+s",
}


def _source():
	return MODULE.read_text(encoding="utf-8")


def _entries_block():
	src = _source()
	start = src.index("const PAGE_ENTRIES = [")
	end = src.index("\n\t];", start)
	return src[start:end]


def _entries():
	"""Each entry as a dict: page or url, title, roles, aliases, shortcut constant."""
	block = _entries_block()
	out = []
	# Split on the opening brace of each entry object.
	for chunk in re.split(r"\n\t\t\{", block)[1:]:
		entry = {}
		m = re.search(r'\bpage: "([^"]+)"', chunk)
		if m:
			entry["page"] = m.group(1)
		m = re.search(r'\burl: "([^"]+)"', chunk)
		if m:
			entry["url"] = m.group(1)
		m = re.search(r"\broles: \[([^\]]*)\]", chunk)
		if m:
			entry["roles"] = re.findall(r'"([^"]+)"', m.group(1))
		m = re.search(r"\baliases: \[([^\]]*)\]", chunk)
		entry["aliases"] = re.findall(r'"([^"]+)"', m.group(1)) if m else []
		m = re.search(r"\bshortcut: (KEY_[A-Z_]+)", chunk)
		if m:
			entry["shortcut"] = m.group(1)
		out.append(entry)
	return out


def _key_constants():
	return dict(re.findall(r'const (KEY_[A-Z_]+) = "([^"]+)";', _source()))


def _strip_comments(text):
	text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
	return re.sub(r"(^|[^:])//[^\n]*", r"\1", text)


class TestTheMapNamesRealPages(unittest.TestCase):
	def test_entries_parse(self):
		entries = _entries()
		self.assertGreaterEqual(len(entries), 20)
		for entry in entries:
			with self.subTest(entry=entry):
				self.assertTrue(("page" in entry) != ("url" in entry), "an entry is a page or a url")
				self.assertTrue(entry["aliases"], "every entry has aliases")

	def test_every_desk_page_exists(self):
		pages = {p.parent.name.replace("_", "-") for p in APP.glob("*/page/*/*.json")}
		for entry in _entries():
			if "page" in entry:
				with self.subTest(page=entry["page"]):
					self.assertIn(entry["page"], pages)

	def test_every_www_page_has_a_template_and_roles(self):
		for entry in _entries():
			if "url" in entry:
				with self.subTest(url=entry["url"]):
					self.assertTrue((APP / "www" / (entry["url"].strip("/") + ".html")).exists())
					self.assertTrue(entry.get("roles"), "a www page carries the roles that may open it")

	def test_stock_scan_roles_match_its_controller(self):
		tree = ast.parse(SCAN_RULES.read_text(encoding="utf-8"))
		scan_roles = None
		for node in tree.body:
			if isinstance(node, ast.Assign) and any(
				getattr(t, "id", None) == "SCAN_ROLES" for t in node.targets
			):
				call = node.value
				scan_roles = set(ast.literal_eval(call.args[0]))
		self.assertTrue(scan_roles, "SCAN_ROLES not found")
		entry = next(e for e in _entries() if e.get("url") == "/stock-scan")
		self.assertEqual(set(entry["roles"]), scan_roles)

	def test_aliases_are_lower_case_and_unpadded(self):
		for entry in _entries():
			for alias in entry["aliases"]:
				with self.subTest(alias=alias):
					self.assertEqual(alias, alias.lower())
					self.assertEqual(alias, alias.strip())

	def test_the_acronyms_are_aliases(self):
		# Full-text search skips words under four letters; this section is where they work.
		qbo = [e for e in _entries() if "qbo" in e["aliases"]]
		self.assertEqual(
			{e.get("page") for e in qbo}, {"quickbooks-online-dashboard", "quickbooks-record-matching"}
		)


class TestTheShortcuts(unittest.TestCase):
	def test_targets_and_chords(self):
		keys = _key_constants()
		found = {}
		for entry in _entries():
			if "shortcut" in entry:
				found[entry.get("page") or entry.get("url")] = keys[entry["shortcut"]]
		self.assertEqual(found, EXPECTED_SHORTCUTS)

	def test_spelled_as_get_key_spells_them(self):
		chords = list(_key_constants().values())
		self.assertEqual(len(chords), len(set(chords)), "two shortcuts share a chord")
		for chord in chords:
			with self.subTest(chord=chord):
				# get_key prefixes ctrl, then shift, then alt, and lower-cases: "alt+shift+p".
				self.assertRegex(chord, r"^alt\+shift\+[a-z]$")
				# T is the Triton widget's (Alt+T, Shift not excluded) and Chrome's toolbar key.
				self.assertNotIn(chord[-1], "tia")

	def test_no_other_binding_in_the_app(self):
		chords = set(_key_constants().values())
		roots = [JS, *APP.glob("*/page"), APP / "custom_html_blocks"]
		for base in roots:
			for path in base.rglob("*.js"):
				if path == MODULE or "lib" in path.parts or path.name == "vue.global.js":
					continue
				text = _strip_comments(path.read_text(encoding="utf-8", errors="replace")).lower()
				for chord in chords:
					with self.subTest(file=str(path.relative_to(APP)), chord=chord):
						self.assertNotIn(f'"{chord}"', text)
						self.assertNotIn(f"'{chord}'", text)

	def test_registered_through_add_shortcut_with_a_description(self):
		src = _strip_comments(_source())
		self.assertIn("fr.ui.keys.add_shortcut({", src)
		self.assertIn('description: translate("Go to {0}", [title])', src)
		self.assertNotIn("ignore_inputs", src, "the shortcuts must keep add_shortcut's input guard")
		self.assertIn("make_function_searchable", src)
		# jQuery 3.7.1 does not copy `repeat` onto the event frappe.ui.keys passes along.
		self.assertIn("const oe = e && (e.originalEvent || e);", src)
		self.assertNotIn("e.repeat", src.replace("oe.repeat", ""))


class TestWiring(unittest.TestCase):
	def test_bundle_imports_it_after_the_sf_kit_loader(self):
		lines = BUNDLE.read_text(encoding="utf-8").splitlines()
		idx = lines.index('import "./sf_kit_loader.js";')
		self.assertTrue(lines[idx + 1].startswith('import "./global_enhancements/page_search.js";'))
		self.assertEqual(sum('page_search.js"' in line for line in lines), 1)

	def test_ci_runs_the_harness_and_this_file(self):
		ci = CI.read_text(encoding="utf-8")
		self.assertIn("node scripts/test_page_search.js", ci)
		self.assertIn("python -m unittest erpnext_enhancements.tests.test_custom_pages_s4c", ci)

	def test_the_wraps_keep_their_guards(self):
		src = _strip_comments(_source())
		self.assertIn('typeof proto.build_options !== "function"', src)
		self.assertIn('typeof proto.parse_results !== "function"', src)
		self.assertIn("proto._ee_page_search_patched", src)
		self.assertIn("proto._ee_page_nav_patched", src)
		# Frappe's list first, ours merged after, Frappe's returned on any error.
		self.assertRegex(
			src,
			r"const options = original\.apply\(this, arguments\);\s*try \{\s*return merge_options\(options, txt, fr\);"
			r"\s*\} catch \(e\) \{\s*return options;",
		)

	def test_desk_pages_route_with_set_route(self):
		src = _strip_comments(_source())
		self.assertIn("fr.set_route(entry.page);", src)
		self.assertNotIn("location.href =", src)


if __name__ == "__main__":
	unittest.main()
