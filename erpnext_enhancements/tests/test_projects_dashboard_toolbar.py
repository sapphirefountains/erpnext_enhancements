"""The Projects Dashboard's tab bar and toolbar (v1.719.0), read as files.

Nik, 2026-10-22: take the Dashboard tab off the Projects Dashboard, put Active Internal Projects
and Completed Projects behind a "View More" button, make New Project / New Master Project one "+"
menu, drop the Reset widths button, and make Columns an icon. Nik's rule for the Dashboard tab
was "we shouldn't have lost any fidelity", and it held numbers nothing else on the page shows, so
its cards and breakdowns moved to a **Summary** tab behind the same View More button.

``scripts/test_projects_dashboard_toolbar.mjs`` runs the block's real script and drives the
buttons. This holds what a run cannot: that the shell, the script and the stylesheet still agree
with one another. The markup names the hidden tabs; the script lists the same routes to know
which tabs a collapse must leave; the stylesheet must let the ``hidden`` attribute win. If they
drift, a tab shows when it should not, or a collapse strands the user on a tab with no tab.

Comments are stripped before anything is looked for, so a note that names an old control neither
trips these checks nor satisfies them. Bench-free: the files are read as text.

Run: python -m unittest erpnext_enhancements.tests.test_projects_dashboard_toolbar
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BLOCKS = APP / "custom_html_blocks"
HTML = (BLOCKS / "projects_dashboard.html").read_text(encoding="utf-8")
JS = (BLOCKS / "projects_dashboard.js").read_text(encoding="utf-8")
CSS = (BLOCKS / "projects_dashboard.css").read_text(encoding="utf-8")
SELECTOR = (
	APP / "public" / "js" / "project_enhancements" / "dashboard_components" / "column_selector.js"
).read_text(encoding="utf-8")


def _strip_js(source):
	source = re.sub(r"/\*[\s\S]*?\*/", "", source)
	return re.sub(r'(^|[^:"\'\\])//[^\n]*', r"\1", source)


def _strip_html(source):
	return re.sub(r"<!--[\s\S]*?-->", "", source)


HTML_CODE = _strip_html(HTML)
JS_CODE = _strip_js(JS)
SELECTOR_CODE = _strip_js(SELECTOR)
CSS_CODE = re.sub(r"/\*[\s\S]*?\*/", "", CSS)

TABS = re.findall(r'<li class="nav-item([^"]*)"([^>]*)>\s*<a class="nav-link[^"]*"[^>]*data-route="([^"]+)"', HTML_CODE)


class TestTabs(unittest.TestCase):
	def test_there_is_no_dashboard_tab(self):
		self.assertNotIn('data-route="dashboard"', HTML_CODE)
		self.assertNotRegex(HTML_CODE, r">\s*Dashboard\s*</a>")
		self.assertNotIn('current_tab === "dashboard"', JS_CODE)

	def test_the_lead_tabs_are_priority_overview_then_gantt_and_both_are_shown(self):
		routes = [route for _cls, _attrs, route in TABS]
		self.assertEqual(routes[:2], ["priority-overview", "portfolio-gantt"])
		for cls, attrs, route in TABS[:2]:
			self.assertNotIn("pg-more-tab", cls, route)
			self.assertNotIn("hidden", attrs, route)

	def test_the_three_extra_tabs_start_hidden(self):
		extra = [(cls, attrs, route) for cls, attrs, route in TABS if "pg-more-tab" in cls]
		self.assertEqual(
			[route for _c, _a, route in extra],
			["active-internal-projects", "completed-projects", "summary"],
		)
		for _cls, attrs, route in extra:
			self.assertIn("hidden", attrs, route + " must start hidden")

	def test_the_scripts_hidden_tab_list_is_the_markups(self):
		markup = [route for cls, _a, route in TABS if "pg-more-tab" in cls]
		match = re.search(r"const MORE_TABS = \[([^\]]*)\]", JS_CODE)
		self.assertIsNotNone(match, "MORE_TABS is gone")
		script = re.findall(r'"([^"]+)"', match.group(1))
		self.assertEqual(sorted(script), sorted(markup))

	def test_every_tab_route_has_a_renderer(self):
		for _cls, _attrs, route in TABS:
			if route == "portfolio-gantt":
				continue
			self.assertIn(f'current_tab === "{route}"', JS_CODE, route + " has nothing drawing it")

	def test_the_summary_still_has_the_dashboard_tabs_cards_and_breakdowns(self):
		# Moved, not dropped: the six cards and the three breakdowns.
		for label in (
			"Active Projects",
			"Overdue",
			"Avg % Complete",
			"Open Tasks",
			"Master Projects",
			"Completed",
			"Active Projects by Status",
			"Active Projects by Type",
			"Active Projects by Completion",
		):
			self.assertIn(f'"{label}"', JS_CODE, label + " was lost with the Dashboard tab")

	def test_the_list_tabs_keep_the_definitions_they_always_had(self):
		self.assertIn('p.is_active === "Yes" && INTERNAL_PROJECT_TYPES.includes(p.project_type)', JS_CODE)
		self.assertIn('project_data.filter(p => p.is_active === "No")', JS_CODE)


class TestViewMore(unittest.TestCase):
	def test_the_button_is_in_the_tab_bar_and_collapsed(self):
		self.assertRegex(HTML_CODE, r'<button[^>]*id="btn-view-more"[^>]*aria-expanded="false"')

	def test_the_choice_is_kept_in_the_browser_and_never_in_user_settings(self):
		self.assertIn('const VIEW_MORE_KEY = "chb_projects_dashboard_more_v1"', JS_CODE)
		self.assertNotIn("user_settings", JS_CODE)
		self.assertNotIn("user_settings", SELECTOR_CODE)

	def test_storage_is_wrapped_so_a_blocked_browser_still_draws(self):
		for fn in ("load_view_more", "save_view_more"):
			body = JS_CODE[JS_CODE.index(f"function {fn}") :]
			body = body[: body.index("\n    }\n") + 1]
			self.assertIn("try {", body, fn + " must not throw when storage is blocked")

	def test_collapsing_falls_back_to_priority_overview(self):
		self.assertRegex(
			JS_CODE,
			r'if \(!open && MORE_TABS\.includes\(current_tab\)\) show_tab\("priority-overview"\)',
		)

	def test_the_hidden_attribute_wins_over_any_display_rule(self):
		self.assertRegex(CSS_CODE, r"\.dashboard-tabs \[hidden\][^{]*\{\s*display:\s*none\s*!important")


class TestNewMenu(unittest.TestCase):
	def test_the_two_buttons_are_gone_and_one_plus_remains(self):
		self.assertEqual(HTML_CODE.count('id="btn-new"'), 1)
		# the old pair were visible buttons in the actions cell; both ids now live in the menu
		actions = HTML_CODE[HTML_CODE.index('id="dashboard-actions"') :]
		actions = actions[: actions.index('id="gantt-controls"')]
		menu = actions[actions.index('id="pg-new-list"') :]
		for item in ("btn-new-project", "btn-new-master-project"):
			self.assertEqual(actions.count(f'id="{item}"'), 1, item)
			self.assertIn(f'id="{item}"', menu, item + " must sit inside the menu")
		self.assertNotRegex(actions, r'class="btn[^"]*"[^>]*id="btn-new-(master-)?project"')

	def test_the_plus_is_labelled_and_announces_a_menu(self):
		self.assertRegex(HTML_CODE, r'id="btn-new"[^>]*aria-label="New"')
		self.assertRegex(HTML_CODE, r'id="btn-new"[^>]*aria-haspopup="menu"')
		self.assertRegex(HTML_CODE, r'id="pg-new-list"[^>]*role="menu"[^>]*hidden')

	def test_each_item_does_exactly_what_its_old_button_did(self):
		self.assertIn("frappe.new_doc('Project')", JS_CODE)
		self.assertIn("frappe.new_doc('Master Project')", JS_CODE)

	def test_the_menu_does_not_swallow_back_and_forward(self):
		# frappe.new_doc makes the form's own history entry; the menu must not push one of its own.
		self.assertNotIn("history.pushState", JS_CODE)
		self.assertNotIn("frappe.set_route", _menu_source())


def _menu_source():
	start = JS_CODE.index("function init_new_menu")
	return JS_CODE[start : JS_CODE.index("})();", start)]


class TestColumnsButton(unittest.TestCase):
	def test_reset_widths_is_not_a_button_any_more(self):
		self.assertNotIn("dashboard-reset-widths", JS_CODE)
		self.assertNotIn("Reset widths", JS_CODE)
		self.assertNotIn("Reset widths", HTML_CODE)

	def test_reset_is_still_possible_from_the_column_menu(self):
		self.assertIn("Reset column widths", JS_CODE)
		self.assertIn("column_resizers[current_tab].reset(container)", JS_CODE)
		self.assertIn("{ icon: true, actions }", JS_CODE)

	def test_the_icon_button_has_a_tooltip_and_an_accessible_name(self):
		self.assertIn("title=\"${frappe.utils.escape_html(label)}\" aria-label=\"${frappe.utils.escape_html(label)}\"", SELECTOR_CODE)
		self.assertIn('const label = __("Columns")', SELECTOR_CODE)

	def test_the_icon_is_inline_svg_because_a_sprite_cannot_cross_the_shadow_root(self):
		self.assertIn("<svg", SELECTOR_CODE)
		self.assertNotIn("<use", SELECTOR_CODE)

	def test_the_word_button_is_still_the_default(self):
		self.assertIn('<i class="fa fa-columns mr-1"></i>', SELECTOR_CODE)

	def test_the_gantts_own_columns_picker_is_untouched(self):
		self.assertIn('id="ganttColumnsDropdown">Columns</button>', HTML_CODE)


class TestWidgetUntouched(unittest.TestCase):
	def test_the_portfolio_gantt_still_mounts_through_the_shared_widget(self):
		self.assertIn("erpnext_enhancements.gantt.mount(host, gantt_widget_config())", JS_CODE)


if __name__ == "__main__":
	unittest.main()
