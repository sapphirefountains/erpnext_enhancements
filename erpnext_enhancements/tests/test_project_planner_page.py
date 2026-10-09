# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Project Planner desk page (``project-planner``), read as files.

The page is a drag-and-drop calendar, so most of what can break it is wiring rather than logic,
and every one of these breaks silently: a Page whose roles leave out the Maintenance team just
is not in their sidebar; a Workspace named like the Page stops the Page from rendering at all; a
workspace JSON whose ``modified`` did not move is skipped by ``bench migrate`` so the shortcut
never appears; a view change that is not a route breaks Back and Forward (Nik's rule); a call to
an endpoint the API does not have fails only when somebody drags something; and HTML5 drag and
drop looks fine on a desktop and does nothing on the phones the crew leads plan from.

Nothing here needs a bench or a ``frappe`` import: it reads the page's JSON and JavaScript, the
workspace JSON and, for the endpoint contract, the API module's source through ``ast``.

Run: python -m unittest erpnext_enhancements.tests.test_project_planner_page
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
PAGE_DIR = APP / "project_enhancements" / "page" / "project_planner"
PAGE_JSON = PAGE_DIR / "project_planner.json"
PAGE_JS = PAGE_DIR / "project_planner.js"
WORKSPACE = APP / "project_enhancements" / "workspace" / "project_enhancements" / "project_enhancements.json"
API = APP / "api" / "project_planner.py"

PLANNER_ROLES = {
	"System Manager",
	"Projects Manager",
	"Projects User",
	"Maintenance Supervisor",
	"Maintenance User",
}

# The endpoints the page may call and the arguments it may send them: the shared Phase 1 contract.
API_CONTRACT = {
	"get_planner": {"start", "end"},
	"save_task": {
		"task",
		"modified",
		"start",
		"end",
		"expected_time",
		"crew_size",
		"crew",
		"credentials",
		"reason",
	},
	"add_crew": {"task", "resource", "modified", "reason"},
	"swap_crew": {"task", "from_resource", "to_resource", "modified", "date", "reason"},
}


def _code():
	return PAGE_JS.read_text(encoding="utf-8")


def _code_without_comments():
	# A comment that explains why something is absent names it; strip comments before asserting
	# that the code does not use it.
	code = _code()
	code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
	return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in code.splitlines())


class TestPageRecord(unittest.TestCase):
	def test_the_page_json_names_the_route_module_and_roles(self):
		page = json.loads(PAGE_JSON.read_text(encoding="utf-8"))
		self.assertEqual(page["doctype"], "Page")
		self.assertEqual(page["name"], "project-planner")
		self.assertEqual(page["page_name"], "project-planner")
		self.assertEqual(page["module"], "Project Enhancements")
		self.assertEqual(page["title"], "Project Planner")
		self.assertEqual(page["standard"], "Yes")
		self.assertEqual({row["role"] for row in page["roles"]}, PLANNER_ROLES)
		self.assertTrue((PAGE_DIR / "__init__.py").exists())

	def test_the_api_gate_matches_the_page_roles(self):
		if not API.exists():
			self.skipTest("api/project_planner.py is not written yet")
		tree = ast.parse(API.read_text(encoding="utf-8"))
		for node in tree.body:
			if isinstance(node, ast.Assign) and any(
				isinstance(t, ast.Name) and t.id == "PLANNER_ROLES" for t in node.targets
			):
				self.assertEqual(set(ast.literal_eval(node.value)), PLANNER_ROLES)
				return
		self.fail("api/project_planner.py has no PLANNER_ROLES")

	def test_no_workspace_shares_the_page_slug(self):
		# A Workspace named "Project Planner" slugs to /app/project-planner and the Page under it
		# never renders.
		for path in APP.glob("*/workspace/*/*.json"):
			workspace = json.loads(path.read_text(encoding="utf-8"))
			for key in ("name", "label", "title"):
				self.assertNotEqual(
					str(workspace.get(key) or "").strip().lower(), "project planner", f"{path} {key}"
				)


class TestRouting(unittest.TestCase):
	def test_views_change_only_through_frappe_set_route(self):
		code = _code_without_comments()
		for forbidden in ("pushState", "replaceState", "window.location", "location.href", "history."):
			self.assertNotIn(forbidden, code)
		self.assertIn("frappe.set_route(PP.route, view, anchor)", code)
		self.assertIn("on_page_show", code)
		self.assertRegex(
			code,
			r"on_page_show = function \(wrapper\) \{\s*if \(wrapper\.project_planner\) \{\s*wrapper\.project_planner\.handle_route\(\);",
		)

	def test_the_four_route_shapes(self):
		# /desk/project-planner, and /week|month|crew/<date>.
		code = _code()
		self.assertIn('route: "project-planner"', code)
		self.assertIn('views: ["week", "month", "crew"]', code)
		self.assertIn('PP.views.includes(route[1]) ? route[1] : "week"', code)
		self.assertIn('moment(route[2], "YYYY-MM-DD", true).isValid()', code)
		for view in ("month", "week", "crew"):
			self.assertIn(f'["{view}", __(', code)


class TestApiCalls(unittest.TestCase):
	def _called(self):
		code = _code_without_comments()
		called = set(re.findall(r"\$\{PP\.api\}\.(\w+)", code))
		called |= set(re.findall(r'method:\s*"(\w+)"', code))
		called |= set(re.findall(r'this\.send\("(\w+)"', code))
		called |= set(re.findall(r'this\.commit\([^,()]+,\s*"(\w+)"', code))
		return called

	def test_every_method_called_is_in_the_contract(self):
		called = self._called()
		self.assertEqual(called, set(API_CONTRACT))
		self.assertIn('api: "erpnext_enhancements.api.project_planner"', _code())

	def test_the_api_defines_each_method_with_the_arguments_sent(self):
		if not API.exists():
			self.skipTest("api/project_planner.py is not written yet")
		tree = ast.parse(API.read_text(encoding="utf-8"))
		functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
		for method, args in API_CONTRACT.items():
			self.assertIn(method, functions, method)
			params = {a.arg for a in functions[method].args.args + functions[method].args.kwonlyargs}
			self.assertTrue(args <= params, f"{method} lacks {sorted(args - params)}")

	def test_nothing_is_sent_as_a_get(self):
		code = _code_without_comments()
		self.assertNotRegex(code, r"type:\s*[\"']GET[\"']")
		self.assertNotIn("frappe.xcall", code)

	def test_lists_go_over_the_wire_as_json(self):
		code = _code()
		for key in ("crew", "credentials"):
			self.assertRegex(code, rf"{key}: JSON\.stringify\(")


class TestDragging(unittest.TestCase):
	def test_dragging_uses_pointer_events_not_html5_drag_and_drop(self):
		code = _code_without_comments()
		for event in ("pointerdown", "pointermove", "pointerup", "pointercancel"):
			self.assertIn(f'"{event}"', code)
		self.assertIn("{ passive: false }", code)
		self.assertIn("hold_ms: 300", code)
		for html5 in ("draggable", "dragstart", "dragover", "dataTransfer"):
			self.assertNotIn(html5, code)

	def test_drop_targets_keep_their_contract_names(self):
		code = _code()
		self.assertIn('".pp-day[data-date]"', code)
		self.assertIn('".pp-cell[data-date][data-resource]"', code)
		self.assertIn('".pp-card[data-task]"', code)

	def test_a_past_day_asks_and_undo_is_bounded(self):
		code = _code()
		self.assertIn("frappe.confirm(", code)
		self.assertIn("undo_max: 20", code)
		self.assertIn('undo_reason: "Undo on the Project Planner"', code)
		self.assertIn("needs_reason", code)


class TestEscaping(unittest.TestCase):
	def test_text_is_escaped_before_it_becomes_html(self):
		code = _code()
		self.assertIn("frappe.utils.escape_html", code)
		# Spot check: no record field is interpolated raw on a line that builds markup. (Plain
		# text such as a tooltip line or an <option> label set through .text() may use them;
		# those are escaped where they meet HTML, or never become HTML at all.)
		raw = [
			line.strip()
			for line in code.splitlines()
			if "<" in line and re.search(r"\$\{(?:card|item|booking|resource|member|project|day)\.\w+", line)
		]
		self.assertEqual(raw, [])
		for field in ("card.subject", "card.name", "item.label", "resource.label"):
			self.assertIn(f"pp_esc({field}", code)

	def test_styles_are_namespaced_and_prefs_are_guarded(self):
		code = _code()
		self.assertIn("id='pp-style'", code)
		style = code[code.index("const PP_STYLE = `") : code.index("`;", code.index("const PP_STYLE = `"))]
		classes = set(re.findall(r"\.([a-zA-Z][\w-]*)", style))
		# Frappe's own button classes are styled inside the planner's sections, nothing else.
		self.assertEqual({c for c in classes if not c.startswith("pp-")}, {"btn"})
		self.assertIn("@media (max-width:760px)", style)
		self.assertEqual(code.count("window.localStorage"), 2)
		for accessor in ("window.localStorage.getItem", "window.localStorage.setItem"):
			before = code[: code.index(accessor)]
			self.assertGreater(before.rfind("try {"), before.rfind("}"), accessor)


class TestWorkspace(unittest.TestCase):
	def test_the_workspace_has_the_shortcut_and_the_link(self):
		workspace = json.loads(WORKSPACE.read_text(encoding="utf-8"))
		shortcuts = [s for s in workspace["shortcuts"] if s.get("link_to") == "project-planner"]
		self.assertEqual(len(shortcuts), 1)
		self.assertEqual(shortcuts[0]["type"], "Page")
		self.assertEqual(shortcuts[0]["label"], "Project Planner")
		blocks = json.loads(workspace["content"])
		self.assertIn(
			"Project Planner",
			[b["data"].get("shortcut_name") for b in blocks if b["type"] == "shortcut"],
		)
		links = [link for link in workspace["links"] if link.get("link_to") == "project-planner"]
		self.assertEqual(len(links), 1)
		self.assertEqual(links[0]["link_type"], "Page")

	def test_every_card_counts_its_links(self):
		workspace = json.loads(WORKSPACE.read_text(encoding="utf-8"))
		card, counted = None, {}
		for link in workspace["links"]:
			if link["type"] == "Card Break":
				card = link
				counted[card["label"]] = 0
			else:
				counted[card["label"]] += 1
		for link in workspace["links"]:
			if link["type"] == "Card Break":
				self.assertEqual(link["link_count"], counted[link["label"]], link["label"])
		# The planner sits in the Projects card.
		labels = [link["label"] for link in workspace["links"]]
		projects = labels.index("Projects")
		self.assertIn("Project Planner", labels[projects + 1 : projects + 1 + counted["Projects"]])

	def test_the_workspace_modified_moved(self):
		# A Workspace JSON whose `modified` is not newer than the site's row is skipped on migrate.
		workspace = json.loads(WORKSPACE.read_text(encoding="utf-8"))
		self.assertGreater(workspace["modified"], "2026-08-03 22:00:00.000000")


if __name__ == "__main__":
	unittest.main()
