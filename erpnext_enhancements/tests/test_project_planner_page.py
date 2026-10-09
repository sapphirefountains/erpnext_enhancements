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
	# Phase 2: one person's driving day, and the days that suit a task by drive time.
	"get_route": {"resource", "date"},
	"suggest_dates": {"task"},
}

# What the route view reads from get_route (spec section B) and the per-day keys the engine adds
# to a day cell. The page only ever reads these; a rename on the server must be a conscious one.
ROUTE_KEYS = (
	"stops",
	"start",
	"end",
	"maps_url",
	"maps_key",
	"map_ids",
	"drive_minutes",
	"km",
	"source",
	"long_drive",
	"travel",
	"off",
)
STOP_KEYS = (
	"order",
	"kind",
	"ref",
	"label",
	"project_title",
	"address",
	"lat",
	"lng",
	"slot",
	"hours",
	"arrive",
	"depart",
	"located",
)
DAY_KEYS = ("drive_minutes", "drive_source", "long_drive", "unlocated")
SUGGESTION_KEYS = ("date", "resource", "label", "free_hours", "added_minutes", "long_drive", "reason")


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

	def test_the_route_shape(self):
		# /desk/project-planner/route/<resource>/<date>: a full view, re-rendered from handle_route
		# so Back and Forward step through it, and stepping a day is itself a route change.
		code = _code_without_comments()
		self.assertIn('route_view: "route"', code)
		self.assertIn("route[1] === PP.route_view && route[2]", code)
		self.assertIn("frappe.set_route(PP.route, PP.route_view, resource, ymd)", code)
		self.assertRegex(code, r"step = \(sign\) =>\s*this\.go_route\(")
		self.assertIn("this.load_route()", code)
		# The route view is not one of the three calendar views.
		self.assertIn('views: ["week", "month", "crew"]', code)
		self.assertNotIn('"route"', code.split("views:")[1].split("\n")[0])

	def test_the_route_view_degrades_when_the_map_cannot_load(self):
		code = _code_without_comments()
		self.assertIn("No location — set the task's address", code)
		self.assertIn("Add a Google Maps API key in Travel Settings", code)
		self.assertIn("only the list is shown", code)
		# The list is built before the map, and a map failure only hides the map.
		self.assertLess(code.index("const $list ="), code.index("this.draw_route_map("))
		self.assertIn("$map.hide()", code)


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

	def test_the_api_returns_the_keys_the_page_reads(self):
		if not API.exists() or "def get_route" not in API.read_text(encoding="utf-8"):
			self.skipTest("api/project_planner.py has no get_route yet")
		source = API.read_text(encoding="utf-8")
		engine = (APP / "project_enhancements" / "crew_availability.py").read_text(encoding="utf-8")

		def built(text, key):
			# A key is built as a dict literal ("key": ...) or as a keyword (key=...).
			return f'"{key}"' in text or re.search(rf"\b{key}\s*=", text) is not None

		for key in ROUTE_KEYS + STOP_KEYS + SUGGESTION_KEYS + ("suggestions", "note"):
			self.assertTrue(built(source, key), key)
		for key in DAY_KEYS:
			self.assertTrue(built(engine, key), key)
		code = _code()
		for key in ROUTE_KEYS + DAY_KEYS + SUGGESTION_KEYS:
			self.assertRegex(code, rf"\b{key}\b", key)

	def test_nothing_is_sent_as_a_get(self):
		code = _code_without_comments()
		self.assertNotRegex(code, r"type:\s*[\"']GET[\"']")
		self.assertNotIn("frappe.xcall", code)

	def test_lists_go_over_the_wire_as_json(self):
		code = _code()
		for key in ("crew", "credentials"):
			self.assertRegex(code, rf"{key}: JSON\.stringify\(")


class TestMapsAndSuggestions(unittest.TestCase):
	def test_the_only_maps_loader_is_the_shared_one(self):
		code = _code_without_comments()
		self.assertIn("window.EEGoogleMaps.load(", code)
		self.assertIn("window.EEGoogleMaps.mapOptions(", code)
		self.assertIn('libraries: ["maps"]', code)
		# No second script tag, no hand-built Maps URL, no second bootstrap.
		for forbidden in ("<script", 'createElement("script")', "createElement('script')", "maps.googleapis.com", "importLibrary"):
			self.assertNotIn(forbidden, code)
		# The theme is read once per render, from the page itself.
		self.assertIn("document.documentElement.dataset.theme", code)

	def test_open_in_google_maps_is_a_new_tab_without_an_opener(self):
		code = _code_without_comments()
		self.assertIn('window.open(maps_url, "_blank", "noopener")', code)
		# Only a Google Maps URL is ever opened, whatever the server sent.
		self.assertIn(r"/^https:\/\/www\.google\.com\/maps\//.test(maps_url)", code)

	def test_markers_and_the_info_window_use_text_not_html(self):
		code = _code_without_comments()
		self.assertIn("textContent", code)
		self.assertNotRegex(code, r"info\.setContent\(`")
		self.assertNotRegex(code, r"setContent\(\w*html")

	def test_the_route_list_escapes_what_people_typed(self):
		code = _code()
		for field in ("stop.label", "stop.address", "stop.order", "stop.ref", "data.start.address", "when"):
			self.assertIn(f"pp_esc({field}", code)
		for field in ("stop.project_title", "data.travel", "data.off"):
			self.assertIn(field, code)
		# The person's name in the header, and the notices (a travel purpose, an off label), are
		# free text: they reach the page through .text(), not through markup.
		self.assertRegex(code, r"pp-route-title[^\n]*\n\s*\.text\(")
		self.assertRegex(code, r"pp-route-note\"></div>'\)\s*\.text\(text\)")

	def test_drive_bookings_are_hours_not_cards(self):
		code = _code_without_comments()
		self.assertIn('booking.kind === "drive"', code)
		# The cell offers the route; the drive line and a Long drive chip come from the day cell.
		self.assertIn("drive_html(", code)
		self.assertIn("Long drive", code)
		self.assertIn("data-route-resource", code)

	def test_suggest_dates_books_through_the_same_save_path_as_a_drag(self):
		code = _code_without_comments()
		self.assertIn("${PP.api}.suggest_dates", code)
		self.assertIn("${PP.api}.get_route", code)
		# Book is a commit() of save_task, so a conflict asks for a reason exactly as a drop does.
		self.assertRegex(code, r'(?s)book_suggestion\(card, item\) \{.*?this\.commit\(\s*card,\s*"save_task"')
		# The person joins the crew in the same call (as a tray card dropped on a row does), so a
		# conflict asks for a reason once.
		self.assertIn("this.crew_has(card, item.resource)", code)
		# On a tray card and in the task dialog.
		self.assertIn('class="btn btn-default btn-xs pp-suggest"', code)
		self.assertIn('links.push(["suggest", __("Suggest dates")])', code)


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
