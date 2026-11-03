"""The Projects Dashboard's head band, tabs and Priority Overview filters (Mock B, v1.781.0),
read as files.

Nikolas, 2026-11-03: the tabs' contents had "variable styles, difficult to read"; he picked Mock B
from the canvas. The shell gained a navy head band (title in the display face, the search field,
Refresh with "Updated N min ago", the "+" menu, four live tiles), the five tabs are always
visible ("View More" is retired), and the Priority Overview has stream chips, a Ranked-only box
and a Group-by-stream toggle. Spend % became Budget. The red-to-green hue scale on priority
badges went.

``scripts/test_projects_dashboard_toolbar.mjs`` runs the block's real script and drives the
buttons. This holds what a run cannot: that the shell, the script and the stylesheet still agree
with one another. The markup names the tabs, tiles and chips; the script lists the same routes,
tile keys and chip streams; the stylesheet reads tokens, not theme selectors, and keeps the Gantt
canvas on literals. If they drift, a tile counts something no button shows, or a tab has no
renderer.

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

TABS = re.findall(r'<button type="button" class="pd-tab[^"]*" role="tab"([^>]*)data-route="([^"]+)"', HTML_CODE)
FIVE_TABS = ["priority-overview", "portfolio-gantt", "active-internal-projects", "completed-projects", "summary"]


def _js_list(name):
	match = re.search(r"const " + re.escape(name) + r" = \[([^\]]*)\]", JS_CODE)
	assert match is not None, name + " is gone"
	return re.findall(r'"([^"]+)"', match.group(1))


class TestTabs(unittest.TestCase):
	def test_the_five_tabs_are_in_the_bar_in_order_and_none_is_hidden(self):
		self.assertEqual([route for _attrs, route in TABS], FIVE_TABS)
		for attrs, route in TABS:
			self.assertNotIn("hidden", attrs, route)
		self.assertEqual(_js_list("TAB_ROUTES"), FIVE_TABS)

	def test_the_tabs_are_buttons_with_their_short_labels(self):
		self.assertRegex(HTML_CODE, r'data-route="active-internal-projects">Internal</button>')
		self.assertRegex(HTML_CODE, r'data-route="completed-projects">Completed</button>')
		self.assertRegex(HTML_CODE, r'data-route="priority-overview">Priority Overview</button>')
		self.assertRegex(HTML_CODE, r'data-route="portfolio-gantt">Portfolio Gantt</button>')
		self.assertRegex(HTML_CODE, r'data-route="summary">Summary</button>')
		self.assertNotIn('class="nav-link', HTML_CODE)

	def test_view_more_is_retired_everywhere(self):
		for text in (HTML_CODE, JS_CODE, CSS_CODE):
			self.assertNotIn("btn-view-more", text)
			self.assertNotIn("pg-more-tab", text)
			self.assertNotIn("View More", text)
			self.assertNotIn("View Less", text)
		self.assertNotIn("MORE_TABS", JS_CODE)
		self.assertNotIn("chb_projects_dashboard_more_v1", JS_CODE)

	def test_there_is_no_dashboard_tab(self):
		self.assertNotIn('data-route="dashboard"', HTML_CODE)
		self.assertNotIn('current_tab === "dashboard"', JS_CODE)

	def test_every_tab_route_has_a_renderer(self):
		for route in FIVE_TABS:
			if route == "portfolio-gantt":
				continue
			self.assertIn(f'current_tab === "{route}"', JS_CODE, route + " has nothing drawing it")

	def test_the_summary_still_has_the_dashboard_tabs_cards_and_breakdowns(self):
		# Moved in v1.719.0, restyled now, never dropped: the six cards and the three breakdowns.
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

	def test_the_tab_on_screen_is_remembered_per_browser(self):
		self.assertIn('const TAB_KEY = "chb_projects_dashboard_tab_v1"', JS_CODE)
		self.assertIn("write_saved(TAB_KEY, route)", JS_CODE)


class TestHeadBand(unittest.TestCase):
	def test_the_band_carries_eyebrow_title_search_refresh_and_the_plus(self):
		band = HTML_CODE[HTML_CODE.index('class="pd-band"') : HTML_CODE.index('class="pd-tabs')]
		self.assertIn('<span class="pd-eyebrow">PROJECTS</span>', band)
		self.assertIn('<h2 class="pd-title">Projects Dashboard</h2>', band)
		self.assertIn('id="global-project-search"', band)
		self.assertRegex(band, r'id="btn-refresh"[^>]*aria-label="Refresh"')
		self.assertRegex(band, r'id="pd-updated"[^>]*aria-live="polite"')
		self.assertIn('id="btn-new"', band)

	def test_the_title_is_in_the_display_face_and_the_band_in_the_band_tokens(self):
		title = re.search(r"\.pd-title\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("var(--sf-font-display)", title)
		band = re.search(r"\.pd-band\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("var(--sf-band)", band)
		self.assertIn("var(--sf-on-band)", band)

	def test_the_search_field_stays_white_in_both_themes(self):
		search = re.search(r"\.pd-search\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("background: var(--sf-field-bg)", search)
		self.assertIn("color: var(--sf-field-text)", search)

	def test_the_four_tiles_are_buttons_and_the_script_counts_exactly_those(self):
		tiles = re.findall(r'<button type="button" class="pd-tile" data-tile="([^"]+)"', HTML_CODE)
		self.assertEqual(tiles, ["active", "past-due", "due-30", "unranked"])
		counts = re.findall(r'data-tile-count="([^"]+)"', HTML_CODE)
		self.assertEqual(counts, tiles)
		block = JS_CODE[JS_CODE.index("const TILE_TESTS = {") :]
		block = block[: block.index("};")]
		self.assertEqual(re.findall(r'"([a-z0-9-]+)":', block), tiles)
		# The labels Nikolas chose, and no "value in flight" tile (declined).
		for label in ("Active", "Past expected end", "Due in 30 days", "No company rank"):
			self.assertIn(f'<span class="pd-tile-l">{label}</span>', HTML_CODE)
		self.assertNotIn("in flight", HTML_CODE)

	def test_the_tile_rules_are_the_ones_he_stated(self):
		self.assertIn("date_part(p.expected_end_date) < today_iso() && pct_of(p) < 100", JS_CODE)
		self.assertIn("end >= today_iso() && end <= iso_plus_days(today_iso(), 30)", JS_CODE)
		self.assertIn('v.toLowerCase() === "not assigned"', JS_CODE)

	def test_only_the_active_tile_starts_pressed(self):
		self.assertRegex(HTML_CODE, r'data-tile="active" aria-pressed="true"')
		for key in ("past-due", "due-30", "unranked"):
			self.assertRegex(HTML_CODE, rf'data-tile="{key}" aria-pressed="false"')

	def test_the_counts_come_from_the_data_the_block_already_fetches(self):
		# No new endpoint: the tiles are tallied from get_project_data's rows.
		self.assertNotIn("get_tile_counts", JS_CODE)
		self.assertNotIn("get_project_counts", JS_CODE)
		self.assertIn("function render_tiles()", JS_CODE)
		self.assertIn("const pool = active_pool();", JS_CODE)

	def test_updated_text_comes_from_the_last_successful_fetch(self):
		self.assertIn("last_fetch_at = Date.now();", JS_CODE)
		self.assertIn('__("Updated just now")', JS_CODE)
		self.assertIn('__("Updated {0} min ago", [minutes])', JS_CODE)
		# a tick each minute, guarded so a sandbox without timers still draws
		self.assertIn('if (typeof setInterval === "function")', JS_CODE)
		self.assertIn("}, 60000);", JS_CODE)

	def test_a_return_to_the_workspace_still_reloads(self):
		self.assertIn("blocks.onWorkspaceReturn(container, fetch_data)", JS_CODE)


class TestPriorityOverviewFilters(unittest.TestCase):
	def test_the_six_chips_and_the_scripts_streams_agree(self):
		chips = re.findall(r'<button type="button" class="pd-fchip" data-stream="([^"]*)"', HTML_CODE)
		self.assertEqual(chips, ["", "Design", "Build", "Service", "Events", "Internal"])
		self.assertEqual(sorted(_js_list("STREAM_CHIPS")), sorted(c for c in chips if c))
		counts = re.findall(r'data-stream-count="([^"]*)"', HTML_CODE)
		self.assertEqual(counts, chips)

	def test_internal_is_one_stream_made_of_the_internal_project_types(self):
		self.assertIn('if (INTERNAL_PROJECT_TYPES.includes(p.project_type)) return INTERNAL_STREAM;', JS_CODE)
		self.assertEqual(
			_js_list("INTERNAL_PROJECT_TYPES"), ["Internal", "Organizational Projects", "Group Projects", "Other"]
		)

	def test_ranked_only_and_group_by_stream_start_off_and_are_remembered(self):
		self.assertRegex(HTML_CODE, r'<input type="checkbox" id="pd-ranked-only">')
		self.assertRegex(HTML_CODE, r'<input type="checkbox" id="pd-group-by-stream">')
		self.assertIn('const DEFAULT_OVERVIEW = { stream: "", ranked_only: false, group: false };', JS_CODE)
		self.assertIn('const OVERVIEW_KEY = "chb_projects_dashboard_overview_v1"', JS_CODE)
		self.assertIn("write_saved(OVERVIEW_KEY, overview)", JS_CODE)

	def test_ranked_means_the_company_priority_is_a_number(self):
		self.assertIn("const is_ranked = (p) => !isNaN(rank_number(p.custom_company_priority));", JS_CODE)

	def test_the_group_order_is_build_design_service_events_then_internal(self):
		self.assertEqual(_js_list("VALUE_STREAM_ORDER"), ["Build", "Design", "Service", "Events"])
		self.assertIn("if (a === INTERNAL_STREAM) return 1;", JS_CODE)

	def test_the_default_is_the_flat_list_by_company_rank(self):
		self.assertIn("'priority-overview': { col: 'company_priority', order: 'asc' }", JS_CODE)
		self.assertIn('const SORT_KEY = "chb_projects_dashboard_sort_v1"', JS_CODE)

	def test_the_sort_is_remembered_when_a_header_is_clicked(self):
		body = JS_CODE[JS_CODE.index("function bind_sortable_headers") :]
		body = body[: body.index("\n    }\n")]
		self.assertIn("save_sort_state();", body)


class TestSavedView(unittest.TestCase):
	def test_every_key_is_the_dashboards_own_and_never_user_settings(self):
		keys = re.findall(r'"(chb_projects_dashboard_[a-z_]+_v1)"', JS_CODE)
		self.assertEqual(
			sorted(set(keys)),
			["chb_projects_dashboard_overview_v1", "chb_projects_dashboard_sort_v1", "chb_projects_dashboard_tab_v1"],
		)
		self.assertNotIn("user_settings", JS_CODE)
		self.assertNotIn("user_settings", SELECTOR_CODE)

	def test_storage_is_wrapped_so_a_blocked_browser_still_draws(self):
		for fn in ("read_saved", "write_saved"):
			body = JS_CODE[JS_CODE.index(f"function {fn}") :]
			body = body[: body.index("\n    }\n") + 1]
			self.assertIn("try {", body, fn + " must not throw when storage is blocked")
		# every read and write goes through the two wrappers
		self.assertEqual(JS_CODE.count("localStorage.getItem("), 2)  # read_saved + the Gantt's own view
		self.assertEqual(JS_CODE.count("localStorage.setItem("), 2)  # write_saved + the Gantt's own view

	def test_nothing_is_deleted_from_storage(self):
		# The old View More key is simply no longer read; removeItem stays the Gantt reset's alone.
		self.assertEqual(JS_CODE.count("localStorage.removeItem("), 1)
		self.assertIn("localStorage.removeItem(GANTT_VIEW_KEY)", JS_CODE)


class TestOneTableStyle(unittest.TestCase):
	def test_every_list_table_is_the_shared_shell(self):
		self.assertIn('<table class="pd-table dashboard-resizable-table">', JS_CODE)
		for bootstrap in ("table-bordered", "thead-light", "table-hover", 'class="badge', "progress-bar"):
			self.assertNotIn(bootstrap, JS_CODE, bootstrap + " is the old Bootstrap look")
		self.assertEqual(JS_CODE.count("table_shell("), 3)  # the three list tabs, nothing else

	def test_the_header_and_row_measurements_are_the_picks(self):
		th = re.search(r"\.pd-table th\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("font-size: 11px", th)
		self.assertIn("text-transform: uppercase", th)
		self.assertIn("color: var(--sf-text-muted)", th)
		td = re.search(r"\.pd-table td\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("height: 40px", td)
		self.assertIn("border-bottom: var(--sf-border-hairline) solid var(--sf-rule)", td)
		table = re.search(r"\.pd-table\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("font-family: var(--sf-font-sans)", table)
		self.assertIn("font-size: var(--sf-text-sm)", table)
		self.assertRegex(CSS_CODE, r"\.pd-table tbody tr\.pd-row:hover td\s*\{")

	def test_the_hue_scale_is_gone_and_ranks_are_pills(self):
		self.assertNotIn("hsl(", JS_CODE)
		self.assertIn('<span class="pd-named">Not Assigned</span>', JS_CODE)
		self.assertIn('<span class="pd-rank${kind === "stream" ? " pd-rank-outline" : ""}">', JS_CODE)
		rank = re.search(r"\.pd-rank\s*\{([^}]*)\}", CSS_CODE).group(1)
		# deep-sea-blue / off-white in light, sky-200 / navy-900 in dark: the two tokens that flip so
		self.assertIn("background: var(--sf-chip-edge-neutral)", rank)
		self.assertIn("color: var(--sf-bg)", rank)

	def test_budget_replaced_spend_percent(self):
		self.assertNotIn("spend_percent", JS_CODE)
		self.assertNotIn("Spend %", JS_CODE)
		self.assertIn("{ key: 'budget', label: 'Budget' }", JS_CODE)
		self.assertIn("const budget_of = (p) => parseFloat(p.custom_project_dollar_amount) || 0;", JS_CODE)
		# right-aligned, a dash when 0
		self.assertIn("""<td class="dashcol dashcol-budget pd-num">${budget ? sf.esc(fmt_money(budget)) : '<span class="pd-dash">—</span>'}</td>""", JS_CODE)

	def test_completion_is_a_bar_and_past_due_is_a_danger_pill(self):
		bar = re.search(r"\.pd-bar\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("background: var(--sf-rule)", bar)
		self.assertIn("background: var(--sf-primary)", re.search(r"\.pd-bar i\s*\{([^}]*)\}", CSS_CODE).group(1))
		late = re.search(r"\.pd-late\s*\{([^}]*)\}", CSS_CODE).group(1)
		self.assertIn("background: var(--sf-danger)", late)
		self.assertIn("color: var(--sf-on-danger)", late)
		self.assertIn("""is_past_due(p) ? '<span class="pd-late">Past due</span>' : ''""", JS_CODE)

	def test_project_type_is_a_pillar_chip(self):
		self.assertIn("sf.chip(p.project_type, pillar_of(stream_of(p)))", JS_CODE)
		self.assertIn(
			'const STREAM_PILLAR = { Design: "design", Build: "build", Products: "build", Delivery: "build", Service: "service", Events: "rent" };',
			JS_CODE,
		)

	def test_inline_editing_and_the_column_tools_stayed(self):
		self.assertIn("function bind_editable_priorities(table)", JS_CODE)
		self.assertIn("api_call('update_project_details'", JS_CODE)
		self.assertIn("{ icon: true, actions }", JS_CODE)
		self.assertIn("column_resizers[current_tab].reset(container)", JS_CODE)
		self.assertIn("function bind_sortable_headers(table)", JS_CODE)
		self.assertIn("/desk/project/${encodeURIComponent(p.name)}", JS_CODE)


class TestThemes(unittest.TestCase):
	def test_the_chrome_reads_tokens_and_no_theme_selector(self):
		# The --sf-* tokens flip with the Desk's data-theme, so the block needs no rule of its own.
		self.assertNotIn("data-theme", CSS_CODE)
		self.assertNotIn("prefers-color-scheme", CSS_CODE)
		self.assertNotIn(":host-context", CSS_CODE)
		self.assertNotIn("var(--card-bg", CSS_CODE)
		self.assertNotIn("var(--text-muted", CSS_CODE)
		self.assertNotIn("var(--border-color", CSS_CODE)
		self.assertNotIn("var(--primary", CSS_CODE)

	def test_every_literal_colour_is_on_the_gantt_canvas(self):
		# Chrome = tokens; canvas = literals (the DHTMLX skin stays light in both themes).
		for line in CSS_CODE.splitlines():
			if re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", line):
				self.assertIn(".pg-", line, "a literal colour off the Gantt canvas: " + line.strip())

	def test_the_gantt_canvas_literals_are_unchanged(self):
		for literal in ("#1f6fb2", "#7c4dbd", "#2e9e5b", "#d98324", "#0f9aa8", "#6c757d", "#eef1f5", "#8d99a6", "#7fcf9f"):
			self.assertIn(literal, CSS_CODE, literal + " left the canvas rules")
		self.assertIn(".pg-widget-host .gantt_row.pg-master { background: #eef1f5; }", CSS_CODE)

	def test_no_drop_shadow_off_the_canvas(self):
		# The brand has none; the overdue ring on a Gantt bar (and its legend swatch) is the one use.
		for line in CSS_CODE.splitlines():
			if "shadow" in line:
				self.assertIn(".pg-", line, line.strip())
		self.assertNotIn("shadow-sm", HTML_CODE)

	def test_the_hidden_attribute_wins_over_any_display_rule(self):
		self.assertRegex(CSS_CODE, r"\.pd \[hidden\],\s*\.pg-new-menu \[hidden\]\s*\{\s*display:\s*none\s*!important")


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

	def test_the_menu_opens_inward_on_a_phone(self):
		# At 375px the + sits at the left edge; a right:0 menu would open off-screen to its left.
		phone = CSS_CODE[CSS_CODE.index("@media (max-width: 767px)") :]
		self.assertRegex(phone, r"\.pg-new-list\s*\{\s*left:\s*0;\s*right:\s*auto")

	def test_the_plus_is_labelled_and_announces_a_menu(self):
		self.assertRegex(HTML_CODE, r'id="btn-new"[^>]*aria-label="New"')
		self.assertRegex(HTML_CODE, r'id="btn-new"[^>]*aria-haspopup="menu"')
		self.assertRegex(HTML_CODE, r'id="pg-new-list"[^>]*role="menu"[^>]*hidden')

	def test_the_plus_is_the_one_primary_button(self):
		self.assertRegex(HTML_CODE, r'class="pd-btn pd-btn-primary pg-new-toggle" id="btn-new"')
		self.assertEqual(HTML_CODE.count("pd-btn-primary"), 1)

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

	def test_the_gantt_toolbar_is_still_the_whole_toolbar(self):
		for control in (
			"gantt-search",
			"ganttProjectDropdown",
			"ganttStatusDropdown",
			"ganttTypeDropdown",
			"ganttCustomerDropdown",
			"gantt-date-window",
			"gantt-at-risk",
			"gantt-today-btn",
			"ganttExportDropdown",
			"gantt-reset-view",
			"gantt-legend",
		):
			self.assertIn(f'id="{control}"', HTML_CODE, control)
		self.assertIn('data-export="range"', HTML_CODE)


if __name__ == "__main__":
	unittest.main()
