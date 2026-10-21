// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Project Planner: who is free, and which project tasks they are booked on, moved by dragging.
//
//   /desk/project-planner                     this week
//   /desk/project-planner/week/2026-10-12     the week holding that day
//   /desk/project-planner/month/2026-10-01    the month holding that day
//   /desk/project-planner/crew/2026-10-12     the crew board for the week holding that day: bars in days,
//                                             hours optional (P7 of the planner UX redesign)
//   /desk/project-planner/crew/2026-10-12?capacity=1
//                                             Capacity, the Crew tab's other half: eight weeks of load per
//                                             person (the arrows step four weeks; a cell opens its week)
//                                             with the Crew Utilization report's figures under it. It
//                                             replaces the Heatmap and Utilization views, whose old
//                                             addresses (/heatmap/<date>, /utilization/<date>) redirect here.
//   /desk/project-planner/route/<person>/2026-10-12
//                                             one person's driving day: stops in order, drive
//                                             time between them, and a map
//
// The Project, Project manager and Group filters ride along on every one of those as the query
// (S5c): /desk/project-planner/week/2026-10-12?project=PRJ-00001&pm=…&group=Field. A link with them
// opens the planner as its sender saw it, and a reload keeps them. Picking a filter rewrites the
// address in place; it is not a screen of its own. See "filters in the address" below.
//
// Every move between weeks, months and views is a route, so Back and Forward step through them
// (Nik's rule: never break Back/Forward). The page moves only with frappe.set_route; Back and
// Forward re-render through on_page_show -> handle_route, exactly like the Maintenance Planner.
//
// Everything comes from erpnext_enhancements.api.project_planner.get_planner, which reads the
// shared availability engine (project_enhancements/crew_availability.py), which also pads each
// person-day with the driving it takes (shop -> stops -> shop). get_route draws one such day;
// suggest_dates ranks the days a task could go by how little driving it adds. The Maintenance Planner
// reads the same engine, so a person's free hours are the same number in both planners.
// Maintenance visits, rental crew tasks and travel use people's hours too; they show here
// read-only ("Maintenance, rentals, travel") and are moved in their own planners.
//
// What a planner does here:
//   - drag a task card to another day             save_task(start)   (duration is kept)
//   - drag a person from Resources available
//     onto a task card                            add_crew
//   - in the crew view, drag a task chip to
//     another day on the same row                 save_task(start)
//     or onto another person's row                swap_crew          (and the day, if it changed)
//   - click a card                                a dialog for dates, hours, crew and
//                                                 qualifications                       save_task
//   - "Suggest dates" on a card or its dialog     suggest_dates, then Book -> save_task
//   - the route icon / drive line on a person's
//     day                                         the route view (get_route)
// Planning helpers (Phase 3A):
//   - a Tentative (pencil) task is hatched; its hours are "soft" load, drawn apart from the firm
//     hours, and "Firm up" turns it into a real booking (checked like any other change)
//   - a task that starts before a predecessor ends carries a warning, and moving a task moves the
//     tasks that follow it by the same working days, later or earlier (P4: dependents=1 on the save;
//     hold Ctrl/Cmd to move just the one)
//   - a task whose required qualifications nobody on the crew holds says so (never a block)
//   - the Overdue tray lists late open tasks by project: reschedule, mark done or cancel them in
//     bulk (bulk_update), or drag one onto a day
//   - "Copy week..." copies chosen tasks into another week as new tasks (copy_week), previewed first
// Overbooking warns and never blocks: when a change would put someone over their hours, on a day
// off or in two places at once, the server answers needs_reason instead of saving, and the page
// asks for a reason and sends the change again with it. The reason lands on the task's timeline.
//
// Dragging is done with pointer events rather than HTML5 drag and drop, which phones and
// tablets do not support. A touch has to rest on a card for a moment before it lifts, so a
// swipe still scrolls the page.

const PP = {
	route: "project-planner",
	api: "erpnext_enhancements.api.project_planner",
	views: ["week", "month", "crew"],
	route_view: "route",
	// Capacity (P7): the Crew tab's second screen, which merged the Heatmap and Utilization views. It is
	// a view of its own inside the page (this.view), and a route of the Crew tab's (/crew/<date>?capacity=1).
	capacity_view: "capacity",
	capacity_weeks: 8,
	// The views that used to be tabs and are now Capacity: their old addresses and saved views land there.
	// "utilization" opens Capacity on the Utilization table.
	capacity_aliases: { heatmap: "capacity", utilization: "capacity" },
	// What ?capacity= may say: anything that reads as on, and "utilization" for the old Utilization address.
	capacity_param_on: ["1", "true", "yes", "on", "heatmap", "utilization"],
	prefs_crew_hours: "ee_project_planner_crew_hours",
	groups: ["Field", "PM", "Design", "Subcontractor"],
	prefs: {
		project: "ee_project_planner_project",
		pm: "ee_project_planner_pm",
		group: "ee_project_planner_group",
		foreign: "ee_project_planner_foreign",
		panel: "ee_project_planner_panel",
	},
	// The filters the address carries (S5c), in the order they are written: ?project=&pm=&group=.
	// all_tasks (P2, D6) is a filter like the rest, "1" when "Show all tasks" is on and "" (the default,
	// phase and crew work only) when not; the server filters, so a change of it loads the week again.
	url_filters: ["project", "pm", "group", "all_tasks"],
	// The longest value the address may give one of them: a document name's limit.
	url_value_max: 140,
	hold_ms: 300,
	drag_px: 6,
	undo_max: 20,
	tray_limit: 60,
	// The color of a task with none of its own. It is data, the same kind of value as Task.color, so it
	// travels as --pp-color like the rest; the page's own design colors are in project_planner.css.
	default_color: "#2563eb",
	undo_reason: "Undo on the Project Planner",
	// The route map's road line (draw_road): the most stops Google's Directions service takes
	// between the two ends, and a latch set once the Routes library has failed on this page.
	road_max_stops: 23,
	routes_unavailable: false,
};

const pp_ymd = (m) => m.format("YYYY-MM-DD");
const pp_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));
// 2 -> "2", 2.5 -> "2.5", 1.25 -> "1.25": the engine's fmt_hours, so both sides print alike.
const pp_hours = (value) => String(Math.round((Number(value) || 0) * 100) / 100);
// Colors come from records people edit; only a plain hex value goes into a style attribute.
// A task's or a person's own color is the one color the page writes inline, and only ever as the
// custom property `style="--pp-color: #hex"`, which project_planner.css paints with (S3.4d);
// tests/test_s3_page_css.py allows exactly that pattern and no other inline color.
const pp_color = (value, fallback) => (/^#[0-9a-fA-F]{3,8}$/.test(String(value || "")) ? value : fallback);
// A design token's value, for the Google map, whose markers and lines take a color value and
// cannot read a CSS variable. The fallback is the token's own value, for a page drawn before
// sf_tokens.bundle.css (a global include) has loaded.
const pp_token_color = (name, fallback) => {
	try {
		const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
		return value || fallback;
	} catch (e) {
		return fallback;
	}
};
// The route map's colors: a stop's number (bahama-blue) and the shop's "S" (green), each a white
// number in a white ring, and the road line (fresh-blue, as on Plan a Trip's map).
const PP_ROUTE_COLORS = () => ({
	stop: pp_token_color("--sf-bahama-blue", "#00609c"),
	shop: pp_token_color("--sf-green", "#1e7e51"),
	ring: pp_token_color("--sf-white", "#ffffff"),
	line: pp_token_color("--sf-fresh-blue", "#00a0df"),
});
const pp_when = (ymd) => moment(ymd, "YYYY-MM-DD").format("ddd, MMM D");
// 70 -> "1h 10m", 25 -> "25m", 120 -> "2h": drive time as people say it.
const pp_drive = (minutes) => {
	const total = Math.max(0, Math.round(Number(minutes) || 0));
	const h = Math.floor(total / 60);
	const m = total % 60;
	if (!h) return `${m}m`;
	return m ? `${h}h ${m}m` : `${h}h`;
};
// Distances arrive in km (routing measures in km) and are shown in miles (Nik, 2026-10-09).
const pp_mi = (km) => String(Math.round(((Number(km) || 0) / 1.609344) * 10) / 10);
const pp_valid_point = (lat, lng) =>
	Number.isFinite(Number(lat)) && Number.isFinite(Number(lng)) && (Number(lat) !== 0 || Number(lng) !== 0);

frappe.pages[PP.route].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Project Planner"),
		single_column: true,
	});
	wrapper.project_planner = new ProjectPlanner(page);
};

frappe.pages[PP.route].on_page_show = function (wrapper) {
	if (wrapper.project_planner) {
		wrapper.project_planner.handle_route();
	}
};

class ProjectPlanner {
	constructor(page) {
		this.page = page;
		this.view = "week";
		this.anchor = frappe.datetime.get_today();
		this.project = this.load_pref(PP.prefs.project, "");
		this.pm = this.load_pref(PP.prefs.pm, "");
		this.group = this.load_pref(PP.prefs.group, "");
		this.show_foreign = this.load_pref(PP.prefs.foreign, "1") !== "0";
		// "Show all tasks" (P2): a saved view's, not the browser's, so it follows the person to any device.
		this.all_tasks = "";
		this.panel_open = this.load_pref(PP.prefs.panel, "1") !== "0";
		this.show_all_unscheduled = false;
		this.data = null;
		this.by_task = {};
		this.by_resource = {};
		this.by_project = {};
		this.by_foreign = {};
		this.task_days = {};
		this.needs_crew = new Set();
		// The newest `modified` seen for each task: a save answers with one, and Undo needs it
		// even after the task has moved out of the range on screen.
		this.modified = {};
		this.undo_stack = [];
		this.request = 0;
		this.drag = null;
		this.click_blocked_until = 0;
		// The route view: one person's day. `route_map` is the live Google map (rebuilt on every
		// render; a theme flip is picked up the next time the view opens).
		this.route_resource = "";
		this.route_date = "";
		this.route_data = null;
		this.route_map = null;
		// Phase 3A: the Overdue tray (collapsed until opened; the tasks ticked in it), and the
		// heatmap view's data.
		this.overdue = null;
		this.overdue_open = false;
		this.overdue_selected = new Set();
		this.overdue_request = 0;
		this.heatmap = null;
		// Capacity (P7): the Utilization rows under the heatmap (undefined until asked for), and the two
		// boxes the sections draw into (built by capacity_shell, dropped when another screen takes the grid).
		this.util = undefined;
		this.$cap_heat = null;
		this.$cap_util = null;
		// The Crew board counts in days; "Show hours" adds each bar's hours (a browser preference).
		this.crew_hours = this.load_pref(PP.prefs_crew_hours, "0") === "1";

		this.page.add_menu_item(__("Planner Resources"), () => frappe.set_route("List", "Planner Resource"));
		this.page.add_menu_item(__("Project Planner Settings"), () =>
			frappe.set_route("Form", "Project Planner Settings")
		);
		this.$body = $('<div class="pp-wrap"></div>').appendTo(page.main);
		this.build_shell();
		// The person's own filters, and those the address gave while they are on screen (S5c).
		this.own_filters = this.filters();
		this.url_filters = null;
		this.watch_filter_url();
		this.init_phase3b();
		this.init_phase4();
		this.init_phase5();
		this.init_phase6a();
		this.init_phase6b();
		this.init_phase6c();
		this.init_phase6d();
		this.init_t1();
		this.init_toolbar();
		this.bind_drag();
	}

	// ------------------------------------------------------------------ routing

	route_target() {
		const route = frappe.get_route() || [];
		if (route[0] !== PP.route) return null;
		const mine = this.my_week_target(route);
		if (mine) return mine;
		// /project-planner/route/<person>/<date>
		if (route[1] === PP.route_view && route[2]) {
			const date = route[3] && moment(route[3], "YYYY-MM-DD", true).isValid() ? route[3] : frappe.datetime.get_today();
			return { view: PP.route_view, resource: route[2], anchor: date };
		}
		const day = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
		// /project-planner/heatmap/<date> and /utilization/<date> (the old tabs) are Capacity now: the
		// router replaces them with /crew/<date>?capacity=… (handle_route), so bookmarks, the digest and the
		// forms' links still land on the same content.
		if (PP.capacity_aliases[route[1]]) {
			return { view: PP.capacity_aliases[route[1]], anchor: day, redirect: true, capacity: route[1] === "utilization" ? "utilization" : "1" };
		}
		// /project-planner/crew/<date>?capacity=1: Capacity, the Crew tab's other screen.
		if (route[1] === "crew") {
			const capacity = this.capacity_param();
			if (capacity) return { view: PP.capacity_view, anchor: day, capacity };
		}
		const view = PP.views.includes(route[1]) ? route[1] : "week";
		return { view, anchor: day };
	}

	// ?capacity= on the address, as "1" (on), "utilization" (on, for the old Utilization address) or ""
	// (Board). Read from route_options, where the router copies the query.
	capacity_param() {
		const opts = frappe.route_options;
		if (!opts || typeof opts !== "object" || !Object.prototype.hasOwnProperty.call(opts, "capacity")) return "";
		const text = String(opts.capacity == null ? "" : opts.capacity).trim().toLowerCase();
		if (!PP.capacity_param_on.includes(text)) return "";
		return text === "utilization" ? "utilization" : "1";
	}

	// The route options a planner screen's address carries: the filters, and Capacity's flag.
	route_options_for(view) {
		const out = this.filter_options();
		if (view === PP.capacity_view) out.capacity = "1";
		return out;
	}

	handle_route() {
		if (this.p6c_before_route()) return;
		const target = this.route_target();
		if (!target) return;
		if (target.redirect) {
			// Replace, never push: Back from here leaves the planner, as it would have from the old address.
			// The old address's filters travel with it (take_route_filters has not read them yet).
			const options = {};
			const opts = frappe.route_options;
			if (opts && typeof opts === "object") {
				PP.url_filters.forEach((key) => {
					if (Object.prototype.hasOwnProperty.call(opts, key)) options[key] = opts[key];
				});
			}
			options.capacity = target.capacity;
			frappe.route_options = null;
			frappe.route_flags = frappe.route_flags || {};
			frappe.route_flags.replace_route = true;
			frappe.set_route(PP.route, "crew", target.anchor, options);
			return;
		}
		// The flag is the address's, like the filters: consumed, so no later route writes it again.
		this.capacity_asked = target.capacity === "utilization" ? "utilization" : "";
		this.capacity_scrolled = false;
		if (frappe.route_options && typeof frappe.route_options === "object") {
			delete frappe.route_options.capacity;
		}
		this.take_route_filters();
		this.view = target.view;
		this.anchor = target.anchor;
		if (target.view === PP3B.my_week) {
			this.show_my_week(target.anchor);
			return;
		}
		if (target.view === PP.route_view) {
			this.route_resource = target.resource;
			this.route_date = target.anchor;
			this.set_mode("route");
			this.load_route();
			return;
		}
		if (target.view === PP.capacity_view) {
			this.set_mode(PP.capacity_view);
			this.load_capacity();
			return;
		}
		this.set_mode("");
		this.load();
	}

	// ------------------------------------------------------------------ filters in the address (S5c)
	//
	// The Project, Project manager and Group filters are part of the address, as ?project=, ?pm= and
	// ?group=, on every planner screen. The rules, in one place:
	//   - Read on every landing (take_route_filters), after the "last used" view is restored, so the
	//     address wins over it. An address that names any of them is the whole filter: one it does not
	//     name is All, so a link shows exactly what its sender saw. A form's "Open in Project Planner"
	//     (S5b) is such a link: ?project= alone, which also leaves the PM filter at All, where it
	//     cannot hide the project's cards. An address that names none of them (the sidebar, another
	//     page's path-only link) keeps the filters on screen.
	//   - What the address gave is that visit's only (url_filters: the keys it decided, those it named
	//     and those it left at All). None is saved to the remembered filters, and "last used" keeps the
	//     person's own value for each of them (p6c_save_last). A bare address after them brings back
	//     the person's own, except on the planner's own next screen (self_route), which carries what
	//     is on screen. A filter the person picks (pick_filter) is theirs and remembered as before;
	//     the address's other keys stay one-off. A key the loaded planner resets to All (a PM it does
	//     not list) stops being the address's, and so do they all once the filters on screen are the
	//     person's own again (a Back onto one of their own entries).
	//   - Back onto an entry the planner left bare (every filter All, and the person's own) names
	//     nothing, so it keeps whatever filters are on screen by then, the newer ones.
	//   - Picking a filter rewrites the address in place (write_filter_url, replaceState). A filter is
	//     not a screen: pushing would make Back step through every pick in a select before it reached
	//     the week before. The entry keeps saying what it shows, so Back onto it, a reload or a copied
	//     link all show it again. A week, a view or a person's route is a screen, and moves by
	//     set_route as it always has, carrying the filters (filter_options).
	//   - The keys are consumed from route_options, so none is left to be written into the next
	//     route's address (v16.50's set_route writes whatever is left there). Keys that are not the
	//     planner's are not its to take.

	filters() {
		return {
			project: this.project || "",
			pm: this.pm || "",
			group: this.group || "",
			all_tasks: this.all_tasks ? "1" : "",
		};
	}

	// The filters set_route carries to another planner screen: only those that are not All.
	filter_options() {
		const out = {};
		const filters = this.filters();
		PP.url_filters.forEach((key) => {
			if (filters[key]) out[key] = filters[key];
		});
		return out;
	}

	// A value the address gives a filter, or null when the filter cannot take it, which ignores it.
	filter_from_address(key, value) {
		if (value == null || typeof value === "object") return null;
		const text = String(value).trim();
		if (text.length > PP.url_value_max) return null;
		if (key === "group" && text && !PP.groups.includes(text)) return null;
		if (key === "all_tasks") {
			if (["1", "true", "yes", "on"].includes(text.toLowerCase())) return "1";
			return ["", "0", "false", "no", "off"].includes(text.toLowerCase()) ? "" : null;
		}
		return text;
	}

	take_route_filters() {
		const opts = frappe.route_options;
		const named = {};
		if (opts && typeof opts === "object") {
			PP.url_filters.forEach((key) => {
				if (!Object.prototype.hasOwnProperty.call(opts, key)) return;
				const value = this.filter_from_address(key, opts[key]);
				if (value !== null) named[key] = value;
				delete opts[key];
			});
			if (!Object.keys(opts).length) frappe.route_options = null;
		}
		// The planner's own next screen (go, go_route, go_my_week) carries what is on screen; with every
		// filter at All it carries nothing, and is no bare address for all that.
		const self_step = !!this.self_route && this.self_route === (frappe.get_route() || []).join("/");
		this.self_route = null;
		const now = this.filters();
		const own = this.own_filter_values();
		const same = (a, b) => PP.url_filters.every((key) => a[key] === b[key]);
		let next = null;
		if (Object.keys(named).length) {
			next = { project: named.project || "", pm: named.pm || "", group: named.group || "", all_tasks: named.all_tasks || "" };
			// The planner's own screens carry the filters on screen: nothing changes, nothing is marked.
			if (same(next, now)) next = null;
			else this.url_filters = Object.assign({}, next);
		} else if (this.url_filters && !self_step) {
			next = own;
			this.url_filters = null;
		}
		// The person's own filters again (a Back onto one of their own entries): theirs, not a link's.
		if (this.url_filters && same(next || now, own)) this.url_filters = null;
		if (next) {
			if (next.project !== now.project) this.show_all_unscheduled = false;
			Object.assign(this, next);
			if (this.$project) this.$project.val(this.project);
			if (this.$pm) this.$pm.val(this.pm);
			if (this.$group) this.$group.val(this.group);
		}
		this.write_filter_url();
	}

	own_filter_values() {
		return Object.assign({ project: "", pm: "", group: "", all_tasks: "" }, this.own_filters);
	}

	// A key the address gave stops being the address's: the person picked it, or the loaded planner
	// reset it to All. With none left, the filters on screen are no longer a link's.
	release_filter(key) {
		if (!this.url_filters) return;
		delete this.url_filters[key];
		if (!Object.keys(this.url_filters).length) this.url_filters = null;
	}

	// A filter picked in the bar, or "Filter to this project" in a project's drawer. Only that one
	// becomes the person's, remembered and in "last used"; the address's other keys stay one-off.
	pick_filter(key, value) {
		this[key] = value || "";
		if (key === "project") this.show_all_unscheduled = false;
		this.own_filters = Object.assign(this.own_filter_values(), { [key]: this[key] });
		this.release_filter(key);
		if (this.url_filters && PP.url_filters.every((name) => this[name] === this.own_filters[name])) {
			this.url_filters = null;
		}
		// all_tasks is remembered with the person's view (p6c_state), not in this browser.
		if (PP.prefs[key]) this.save_pref(PP.prefs[key], this[key]);
		this.write_filter_url();
		// The server decides which tasks it sends, so that one asks again; the rest filter what is here.
		if (key === "all_tasks") {
			this.load();
			// The kit's menu is outside the page body, whose clicks save "last used" (p6c_state_soon).
			if (this.p6c) this.p6c_state_soon();
		} else this.render();
	}

	// The page's one history write: the filters on screen into the entry on screen, in place. Only
	// while the router is on the planner's own path (a late render must not write another page's
	// address) and never over filters the page has not read yet (a Back the router is still routing).
	// The path as the router wrote it (a shell segment included), any key that is not the planner's,
	// the hash and history.state are kept. Values are encoded as v16.50's set_route encodes them, so
	// the router reads the address it would have written and pushes no duplicate, except that an
	// apostrophe is written as the browser itself writes it in a query (%27): left bare, the address
	// read back would never equal the one written, and every landing would write it again.
	write_filter_url() {
		const path = window.location.pathname;
		if ((frappe.get_route() || [])[0] !== PP.route || !path.split("/").includes(PP.route)) return;
		const opts = frappe.route_options;
		if (opts && PP.url_filters.some((key) => Object.prototype.hasOwnProperty.call(opts, key))) return;
		const enc = (text) => encodeURIComponent(text).replace(/'/g, "%27");
		const pairs = [];
		const filters = this.filters();
		PP.url_filters.forEach((key) => {
			if (filters[key]) pairs.push(`${key}=${enc(filters[key])}`);
		});
		new URLSearchParams(window.location.search).forEach((value, key) => {
			if (!PP.url_filters.includes(key)) pairs.push(`${enc(key)}=${enc(value)}`);
		});
		const search = pairs.length ? `?${pairs.join("&")}` : "";
		if (search === window.location.search) return;
		window.history.replaceState(window.history.state, "", path + search + window.location.hash);
	}

	// A Back or Forward the router does not route (the planner kit closing its drawer) lands on an
	// entry that may still hold the filters from before a pick made while the drawer was open. Once
	// the router has had its turn, the address is brought up to what is on screen.
	watch_filter_url() {
		window.addEventListener("popstate", () => setTimeout(() => this.write_filter_url(), 0));
	}

	// The route view replaces the toolbar, panel, trays and calendar; the others replace it.
	// Capacity keeps the toolbar but swaps the calendar for its own grids, and drops the controls that
	// only mean something on the calendar (the menus leave out theirs too: tb_calendar). (The panel is
	// shown and hidden by render_panel, so leaving a view does not reveal a stale one.)
	set_mode(mode) {
		const route = mode === "route";
		const board = mode === PP.capacity_view;
		// Another screen takes the grid: Capacity's boxes are gone with it, and the next visit builds them.
		if (!board) {
			this.$cap_heat = null;
			this.$cap_util = null;
		}
		// The Crew board has its tray beside it (P7); every other screen keeps it above.
		if (this.$main) this.$main.toggleClass("pp-main-crew", !route && this.view === "crew");
		this.render_crew_bar();
		this.$toolbar.toggle(!route);
		this.$trays.toggle(!route && !board);
		this.$grid_wrap.toggle(!route);
		this.$hint.toggle(!route);
		this.$route.toggle(!!route);
		if (this.$my_week) this.$my_week.hide();
		this.p4_set_mode(mode);
		this.p6c_set_mode(mode);
		if (route && this.$draft_bar) this.$draft_bar.hide();
		[this.$project, this.$pm].forEach(($el) => $el && $el.toggle(!board));
		// A class, not toggle(): the icon's CSS also hides it while there is nothing to undo.
		if (this.$undo) this.$undo.toggleClass("pp-tb-off", board);
		if (route || board) this.$panel.hide();
		if (!route) this.clear_route_map();
		this.tb_render();
	}

	go_route(resource, ymd) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === PP.route_view && route[2] === resource && route[3] === ymd) {
			this.load_route();
			return;
		}
		this.self_route = [PP.route, PP.route_view, resource, ymd].join("/");
		this.p6a_route(() => frappe.set_route(PP.route, PP.route_view, resource, ymd, this.filter_options()), false);
	}

	// Capacity is the Crew tab's other screen: its route is /crew/<date>?capacity=1, so a click on Board or
	// Capacity is a history entry like any other (Back steps between them). A saved view or old link that
	// still says heatmap or utilization lands on Capacity.
	go(view, anchor) {
		view = PP.capacity_aliases[view] || view;
		if (view === PP.capacity_view) return this.go_capacity(anchor);
		const route = frappe.get_route() || [];
		// Leaving Capacity for the Board is a move even though the path is the same: the query is what differs.
		if (route[0] === PP.route && route[1] === view && route[2] === anchor && this.view !== PP.capacity_view) {
			this.load();
			return;
		}
		this.self_route = [PP.route, view, anchor].join("/");
		this.p6a_route(() => frappe.set_route(PP.route, view, anchor, this.filter_options()), true);
	}

	go_capacity(anchor) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === "crew" && route[2] === anchor && this.view === PP.capacity_view) {
			this.load();
			return;
		}
		this.self_route = [PP.route, "crew", anchor].join("/");
		this.p6a_route(() => frappe.set_route(PP.route, "crew", anchor, this.route_options_for(PP.capacity_view)), true);
	}

	// ------------------------------------------------------------------ prefs

	load_pref(key, fallback) {
		try {
			const value = window.localStorage.getItem(key);
			return value == null ? fallback : value;
		} catch (e) {
			return fallback;
		}
	}

	save_pref(key, value) {
		try {
			window.localStorage.setItem(key, value);
		} catch (e) {
			// A remembered filter is a convenience.
		}
	}

	// ------------------------------------------------------------------ dates

	today() {
		return (this.data && this.data.today) || frappe.datetime.get_today();
	}

	first_weekday() {
		const name = (frappe.boot.sysdefaults && frappe.boot.sysdefaults.first_day_of_the_week) || "Sunday";
		const index = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].indexOf(name);
		return index < 0 ? 0 : index;
	}

	range() {
		const first = this.first_weekday();
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		if (this.view === PP.capacity_view) {
			// The same weeks as the calendar: they begin on the site's first weekday, which is also
			// what get_heatmap, copy_week and the Crew Utilization report's weeks snap to. The heatmap
			// and the Utilization table under it both cover these eight weeks.
			const start = anchor.clone().subtract((anchor.day() - first + 7) % 7, "days");
			return { start, end: start.clone().add(PP.capacity_weeks * 7 - 1, "days") };
		}
		if (this.view !== "month") {
			const start = anchor.clone().subtract((anchor.day() - first + 7) % 7, "days");
			return { start, end: start.clone().add(6, "days") };
		}
		const month_start = anchor.clone().startOf("month");
		const month_end = anchor.clone().endOf("month").startOf("day");
		return {
			start: month_start.clone().subtract((month_start.day() - first + 7) % 7, "days"),
			end: month_end.clone().add((first + 6 - month_end.day() + 7) % 7, "days"),
		};
	}

	range_days() {
		const { start, end } = this.range();
		const days = [];
		for (let day = start.clone(); !day.isAfter(end, "day"); day.add(1, "days")) days.push(pp_ymd(day));
		return days;
	}

	shift(sign) {
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		// Capacity steps half its span, so each step keeps four weeks in common.
		const next =
			this.view === "month"
				? anchor.startOf("month").add(sign, "months")
				: anchor.add(sign * (this.view === PP.capacity_view ? (PP.capacity_weeks / 2) * 7 : 7), "days");
		this.go(this.view, pp_ymd(next));
	}

	title() {
		const { start, end } = this.range();
		if (this.view === "month") return moment(this.anchor, "YYYY-MM-DD").format("MMMM YYYY");
		if (start.year() !== end.year()) return `${start.format("MMM D, YYYY")} – ${end.format("MMM D, YYYY")}`;
		if (start.month() !== end.month()) return `${start.format("MMM D")} – ${end.format("MMM D, YYYY")}`;
		return `${start.format("MMM D")} – ${end.format("D, YYYY")}`;
	}

	// ------------------------------------------------------------------ shell

	build_shell() {
		const $bar = $('<div class="pp-toolbar"></div>').appendTo(this.$body);
		this.$toolbar = $bar;
		$('<button type="button" class="btn btn-default btn-sm">‹</button>')
			.attr("title", __("Earlier"))
			.attr("aria-label", __("Earlier"))
			.on("click", () => this.shift(-1))
			.appendTo($bar);
		$('<button class="btn btn-default btn-sm"></button>')
			.text(__("Today"))
			.on("click", () => this.go(this.view, frappe.datetime.get_today()))
			.appendTo($bar);
		$('<button type="button" class="btn btn-default btn-sm">›</button>')
			.attr("title", __("Later"))
			.attr("aria-label", __("Later"))
			.on("click", () => this.shift(1))
			.appendTo($bar);
		this.$title = $('<span class="pp-title" tabindex="-1"></span>').appendTo($bar);

		const $seg = $('<span class="pp-seg"></span>').appendTo($bar);
		this.$seg = $seg;
		this.$view_buttons = {};
		[
			["month", __("Month")],
			["week", __("Week")],
			["crew", __("Crew")],
		].forEach(([view, label]) => {
			this.$view_buttons[view] = $('<button type="button"></button>')
				.attr("data-tb-tab", view)
				.text(label)
				.on("click", () => this.go(view, this.anchor))
				.appendTo($seg);
		});

		$('<span class="pp-spacer"></span>').appendTo($bar);
		// The filters (and 6B's search, which joins them) are one group: on a phone the group moves
		// into the "Filters and tools" sheet and back (toolbar redesign, v1.687.0).
		this.$filters = $('<span class="pp-tb-filters"></span>').appendTo($bar);
		this.$project = this.make_select(this.$filters, __("Project"), (value) => this.pick_filter("project", value));
		this.$pm = this.make_select(this.$filters, __("Project manager"), (value) => this.pick_filter("pm", value));
		this.$group = this.make_select(this.$filters, __("Group"), (value) => this.pick_filter("group", value));
		$("<option></option>").val("").text(__("All groups")).appendTo(this.$group);
		PP.groups.forEach((group) => $("<option></option>").val(group).text(__(group)).appendTo(this.$group));
		this.$group.val(PP.groups.includes(this.group) ? this.group : "");
		this.group = this.$group.val() || "";

		// The alert chips, then + Add and More (both filled by the toolbar block, PPTB), then Undo as
		// a small icon while there is something to undo (its menu item is in More ▸ Tools), ? and refresh.
		this.$alerts = $('<span class="pp-tb-alerts" role="group"></span>').attr("aria-label", __("Alerts")).appendTo($bar);
		this.$tools = $('<span class="pp-tb-tools"></span>').appendTo($bar);
		this.$undo = $('<button type="button" class="btn btn-default btn-sm pp-undo pp-empty">↶</button>')
			.attr("aria-label", __("Undo"))
			.on("click", () => this.undo())
			.appendTo($bar);
		$('<button type="button" class="btn btn-default btn-sm pp-refresh">↻</button>')
			.attr("title", __("Refresh"))
			.attr("aria-label", __("Refresh"))
			.on("click", () => this.load())
			.appendTo($bar);
		this.update_undo_button();

		this.$panel = $('<div class="pp-section pp-res"></div>').appendTo(this.$body);
		// The Crew tab's header (Board | Capacity, Show hours), filled by render_crew_bar.
		this.$crewbar = $('<div class="pp-crewbar"></div>').hide().appendTo(this.$body);
		// The trays and the grid share one box so the Crew board can set its tray beside it (P7).
		this.$main = $('<div class="pp-main"></div>').appendTo(this.$body);
		this.$trays = $('<div class="pp-trays"></div>').appendTo(this.$main);
		this.$grid_wrap = $('<div class="pp-grid-wrap"></div>').appendTo(this.$main);
		this.$hint = $('<div class="pp-hint"></div>').appendTo(this.$body);
		this.$route = $('<div class="pp-route-view"></div>').hide().appendTo(this.$body);
	}

	// The tab a screen sits under: Capacity is the Crew tab's second screen.
	tab_of_view(view) {
		return (view || this.view) === PP.capacity_view ? "crew" : view || this.view;
	}

	mark_view_buttons() {
		const tab = this.tab_of_view();
		Object.entries(this.$view_buttons).forEach(([view, $button]) => $button.toggleClass("pp-on", view === tab));
	}

	// "Maintenance, rentals, travel" in Display: the other planners' bookings, read-only.
	set_show_foreign(on) {
		this.show_foreign = !!on;
		this.save_pref(PP.prefs.foreign, this.show_foreign ? "1" : "0");
		this.render();
	}

	// "Show all tasks" in Display: remembered with the person's view and carried in the address.
	set_all_tasks(on) {
		this.pick_filter("all_tasks", on ? "1" : "");
	}

	make_select($bar, label, on_change) {
		const $select = $('<select class="form-control input-sm"></select>')
			.attr("aria-label", label)
			.attr("title", label)
			.appendTo($bar);
		$select.on("change", () => on_change($select.val() || ""));
		return $select;
	}

	// ------------------------------------------------------------------ data

	load() {
		if (this.view === PP.capacity_view) return this.load_capacity();
		const { start, end } = this.range();
		const token = ++this.request;
		this.$title.text(this.title());
		this.take_title_focus();
		this.mark_view_buttons();
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.get_planner`,
				// P4: always with the drafts, so a Draft schedule's (anyone's) and held changes show.
				args: Object.assign({ start: pp_ymd(start), end: pp_ymd(end), draft: 1 }, this.all_tasks ? { all_tasks: 1 } : {}),
			})
		)
			.then((r) => {
				if (token !== this.request) return;
				this.data = (r && r.message) || {};
				this.$body.removeClass("pp-loading");
				this.index();
				this.fill_filters();
				this.write_filter_url();
				this.load_phase4();
				this.render();
				this.load_overdue();
				this.t1_load_needs();
			})
			.catch(() => {
				if (token === this.request) this.$body.removeClass("pp-loading");
			});
	}

	index() {
		const data = this.data;
		this.by_task = {};
		this.by_resource = {};
		this.by_project = {};
		this.by_foreign = {};
		this.task_days = {};
		[].concat(data.tasks || [], data.unscheduled || []).forEach((card) => {
			this.by_task[card.name] = card;
			if (card.modified) this.modified[card.name] = card.modified;
		});
		(data.resources || []).forEach((resource) => (this.by_resource[resource.name] = resource));
		(data.projects || []).forEach((project) => (this.by_project[project.name] = project));
		(data.foreign || []).forEach((item) => (this.by_foreign[item.key] = item));
		// Which person-days each task is booked on, so a card can say it is in a conflict.
		Object.entries(data.days || {}).forEach(([resource, days]) => {
			Object.entries(days || {}).forEach(([ymd, day]) => {
				(day.bookings || []).forEach((booking) => {
					if (!booking.ref || !this.by_task[booking.ref]) return;
					(this.task_days[booking.ref] = this.task_days[booking.ref] || []).push({ resource, ymd, day, booking });
				});
			});
		});
		this.needs_crew = new Set(data.needs_crew || []);
	}

	fill_filters() {
		const projects = (this.data.projects || [])
			.slice()
			.sort((a, b) => String(a.title || a.name).localeCompare(String(b.title || b.name)));
		this.$project.empty();
		$("<option></option>").val("").text(__("All projects")).appendTo(this.$project);
		projects.forEach((project) => {
			const label = project.title && project.title !== project.name ? `${project.title} (${project.name})` : project.name;
			$("<option></option>").val(project.name).text(label).appendTo(this.$project);
		});
		// A project the address gave stays the filter even when the planner does not list it (S5b, a
		// form's link; S5c, any link): the calendar says it has no work here (render) rather than
		// quietly showing every project. A remembered project the planner no longer lists still resets.
		const linked = this.url_filters && this.project === this.url_filters.project;
		if (this.project && linked && !this.by_project[this.project]) {
			$("<option></option>").val(this.project).text(this.project).appendTo(this.$project);
		} else if (this.project && !this.by_project[this.project]) this.project = "";
		this.$project.val(this.project);

		const pms = [...new Set(projects.map((project) => project.pm).filter(Boolean))].sort();
		this.$pm.empty();
		$("<option></option>").val("").text(__("All project managers")).appendTo(this.$pm);
		pms.forEach((pm) => $("<option></option>").val(pm).text(pm).appendTo(this.$pm));
		if (this.pm && !pms.includes(this.pm)) {
			// A PM a link named that is not listed is ignored: All, and no longer the link's (S5c).
			if (this.url_filters && this.url_filters.pm === this.pm) this.release_filter("pm");
			this.pm = "";
		}
		this.$pm.val(this.pm);
	}

	// ------------------------------------------------------------------ filters

	task_visible(card) {
		if (this.over_only && !card.over_plan) return false;
		if (this.project && card.project !== this.project) return false;
		if (this.pm) {
			const project = card.project && this.by_project[card.project];
			if (!project || project.pm !== this.pm) return false;
		}
		return true;
	}

	resources() {
		const order = (group) => {
			const index = PP.groups.indexOf(group);
			return index < 0 ? PP.groups.length : index;
		};
		// this.data is null until the first load answers, and the toolbar's Color by key (Display, "person") asks for the people on every tb_render.
		return ((this.data && this.data.resources) || [])
			.filter((resource) => !this.group || resource.group === this.group)
			.slice()
			.sort((a, b) => order(a.group) - order(b.group) || String(a.label).localeCompare(String(b.label)));
	}

	resource_label(name, fallback) {
		const resource = this.by_resource[name];
		return (resource && resource.label) || fallback || name;
	}

	resource_color(name) {
		const resource = this.by_resource[name];
		if (resource && pp_color(resource.color, "")) return resource.color;
		let hash = 0;
		for (const ch of String(name || "")) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
		return `hsl(${hash},55%,42%)`;
	}

	day_of(resource, ymd) {
		const days = (this.data.days || {})[resource];
		return (days && days[ymd]) || null;
	}

	// ------------------------------------------------------------------ render

	render() {
		if (this.view === PP.capacity_view) {
			this.render_capacity();
			return;
		}
		if (!this.data) return;
		this.render_panel();
		this.render_trays();
		if (this.view === "crew") this.render_crew();
		else this.render_calendar();
		// A project filter with nothing in this range or unscheduled says so by name (S5b).
		const cards = (this.data.tasks || []).concat(this.data.unscheduled || []);
		if (this.project && !cards.some((card) => card.project === this.project)) {
			const named = this.by_project[this.project];
			$('<div class="pp-section pp-empty-note" role="status"></div>')
				.text(__("No work for {0} in this range.", [(named && named.title) || this.project]))
				.prependTo(this.$grid_wrap);
		}
		this.$hint.text(
			this.view === "crew"
				? __(
						"A task is a bar along a person's row, counted in days (Show hours adds the hours). Drag it to another day on the same row to move it, or onto someone else's row to hand their place on the crew to that person. Tasks still to schedule, and tasks that need a crew, wait in the tray beside the board. On a touch screen, hold for a moment first."
				  )
				: __(
						"Drag a task card to another day to move it; it keeps its length. Drag a person from Resources available onto a task to add them to its crew. On a touch screen, hold for a moment first. Click a card to change its dates, hours and crew."
				  )
		);
		this.render_phase3b();
		this.render_phase4();
		this.render_phase6a();
		this.render_phase6b();
		this.render_phase6c();
		this.render_phase6d();
		this.tb_render();
	}

	// The availability of one person on one day, as a class and a short text.
	avail_state(day) {
		if (!day) return { cls: "pp-idle", text: "", ratio: 0, soft: 0, soft_ratio: 0 };
		const capacity = Number(day.capacity) || 0;
		const booked = Number(day.booked) || 0;
		// Pencilled (tentative) hours: soft load. `booked`, `free` and the colors count firm work only.
		const soft = Math.max(0, Number(day.soft_booked) || 0);
		const over = booked - capacity;
		const conflict = (day.conflicts || []).length > 0;
		let state;
		if (capacity <= 0 && booked <= 0) {
			const holiday = String(day.off || "").startsWith("Holiday");
			state = { cls: "pp-offday", text: holiday ? __("Holiday") : __("Off"), ratio: 0 };
		} else if (capacity <= 0) {
			state = { cls: "pp-red pp-offday", text: __("Off, {0}h booked", [pp_hours(booked)]), ratio: 1 };
		} else if (over > 0.01) {
			state = { cls: "pp-red", text: __("Over {0}h", [pp_hours(over)]), ratio: 1 };
		} else {
			const ratio = booked / capacity;
			const free = Math.max(0, Number(day.free != null ? day.free : capacity - booked));
			state = {
				cls: ratio <= 0.75 ? "pp-green" : "pp-amber",
				text: free > 0.01 ? __("{0}h free", [pp_hours(free)]) : __("Full"),
				ratio,
			};
			if (day.off) state.text += ` · ${__("½ day off")}`;
		}
		this.p6d_state(day, state);
		if (conflict) state.cls += " pp-conflict";
		state.soft = soft;
		state.soft_ratio = capacity > 0 ? Math.min(Math.max(0, 1 - state.ratio), soft / capacity) : 0;
		if (soft > 0.01) {
			state.text += ` · ${__("+{0}h pencil", [pp_hours(soft)])}`;
			if (capacity > 0 && booked + soft - capacity > 0.01) state.cls += " pp-softover";
		}
		return state;
	}

	// The bar under a person-day: firm hours solid, pencilled hours hatched after them.
	bar_html(state) {
		const width = Math.round(Math.min(1, state.ratio) * 100);
		const soft = Math.round(Math.min(1, state.soft_ratio || 0) * 100);
		return `<div class="pp-bar"><i style="width:${width}%"></i>${
			soft > 0 ? `<b style="left:${width}%;width:${soft}%"></b>` : ""
		}</div>`;
	}

	day_tip(resource, ymd, day) {
		const lines = [`${this.resource_label(resource)} · ${pp_when(ymd)}`];
		if (!day) return lines.join("\n");
		lines.push(__("{0}h of {1}h booked", [pp_hours(day.booked), pp_hours(day.capacity)]));
		if (Number(day.soft_booked) > 0) {
			lines.push(__("{0}h pencilled (tentative, not counted as booked)", [pp_hours(day.soft_booked)]));
		}
		if (day.off) lines.push(__(day.off));
		if (Number(day.drive_minutes) > 0) {
			lines.push(
				`${__("Driving")}: ${pp_drive(day.drive_minutes)} (${this.source_text(day.drive_source)})${
					day.long_drive ? ` · ${__("Long drive")}` : ""
				}`
			);
		}
		if (Number(day.unlocated) > 0) {
			lines.push(__("{0} stop(s) with no location: not counted in the drive", [day.unlocated]));
		}
		(day.bookings || []).forEach((booking) => {
			// The drive is summarized above; it is only listed when the day carries no figure for it.
			if (booking.kind === "drive" && Number(day.drive_minutes) > 0) return;
			const parts = [`${pp_hours(booking.hours)}h`];
			if (booking.slot) parts.push(booking.slot.join("–"));
			if (booking.estimated) parts.push(__("estimated"));
			if (booking.tentative) parts.push(__("pencil"));
			lines.push(`• ${booking.label || booking.ref} (${parts.join(", ")})`);
		});
		(day.conflicts || []).forEach((text) => lines.push(`${__("Conflict")}: ${text}`));
		(day.warnings || []).forEach((text) => lines.push(`${__("Note")}: ${text}`));
		return lines.join("\n");
	}

	// "Google", "Estimate", "Google + estimate": where a drive time came from.
	source_text(source) {
		if (source === "google") return __("Google");
		if (source === "mixed") return __("Google + estimate");
		return __("estimate");
	}

	// Does this person-day have anything to drive to (so a route is worth opening)?
	has_route(day) {
		if (!day) return false;
		if (Number(day.drive_minutes) > 0) return true;
		return (day.bookings || []).some((b) => b.kind && b.kind !== "drive" && b.kind !== "travel");
	}

	// The drive line of a person-day: "1h 10m drive", a Long drive chip when it is long, and the
	// route icon. Clicking either opens the route view. Empty when there is nothing to show.
	drive_html(resource, ymd, day) {
		if (!this.has_route(day)) return "";
		const minutes = Number(day.drive_minutes) || 0;
		// With no figure for the day the icon stands alone: it still opens the route.
		const text = minutes > 0 ? __("{0} drive", [pp_drive(minutes)]) : "";
		const tip = minutes > 0 ? `${pp_drive(minutes)} · ${this.source_text(day.drive_source)}` : __("Open this day's route");
		const long = day.long_drive ? `<span class="pp-chip pp-red">${pp_esc(__("Long drive"))}</span>` : "";
		const icon =
			'<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5">' +
			'<circle cx="3.5" cy="12.5" r="1.8"/><circle cx="12.5" cy="3.5" r="1.8"/>' +
			'<path d="M5.3 12.5H10a2.5 2.5 0 0 0 0-5H6a2.5 2.5 0 0 1 0-4h4.7"/></svg>';
		return `
			<span class="pp-drive">
				<span class="pp-route" role="button" tabindex="0" data-route-resource="${pp_esc(resource)}"
					data-route-date="${pp_esc(ymd)}" title="${pp_esc(tip)}" aria-label="${pp_esc(
			__("Open the route for {0}", [pp_when(ymd)])
		)}">${icon}${text ? `<span class="pp-drive-text">${pp_esc(text)}</span>` : ""}</span>${long}
			</span>`;
	}

	person_html(resource, compact) {
		const color = this.resource_color(resource.name);
		const group = resource.group ? __(resource.group) : "";
		return `
			<div class="pp-person" data-resource="${pp_esc(resource.name)}"
				title="${pp_esc(__("Drag {0} onto a task to add them to its crew", [resource.label]))}">
				${compact ? "" : `<span class="pp-dot" style="--pp-color: ${pp_esc(color)}"></span>`}
				<span class="pp-person-name">${pp_esc(resource.label)}</span>
				<span class="pp-person-group">${pp_esc(group)}</span>
			</div>`;
	}

	render_panel() {
		this.$panel.toggle(this.view !== "crew").empty();
		if (this.view === "crew") return;
		const resources = this.resources();
		const $head = $('<div class="pp-section-head"></div>').appendTo(this.$panel);
		$("<span></span>").text(__("Resources available")).appendTo($head);
		$('<span class="pp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(this.panel_open ? __("Hide") : __("Show"))
			.on("click", () => {
				this.panel_open = !this.panel_open;
				this.save_pref(PP.prefs.panel, this.panel_open ? "1" : "0");
				this.render_panel();
			})
			.appendTo($head);
		if (!this.panel_open) return;
		if (!resources.length) {
			$('<div class="pp-empty-note"></div>')
				.text(
					this.group
						? __("Nobody in this group. Add people under Planner Resources.")
						: __("No planner resources yet. Add the people you schedule under Planner Resources.")
				)
				.appendTo(this.$panel);
			return;
		}
		const days = this.range_days();
		const today = this.today();
		const month = this.view === "month";
		const columns = month ? `150px repeat(${days.length},minmax(20px,1fr))` : "170px repeat(7,minmax(74px,1fr))";
		const html = [];
		html.push(`<div class="pp-res-head"></div>`);
		days.forEach((ymd) => {
			const m = moment(ymd, "YYYY-MM-DD");
			const label = month ? m.format("D") : m.format("ddd D");
			html.push(`<div class="pp-res-head${ymd === today ? " pp-today" : ""}">${pp_esc(label)}</div>`);
		});
		resources.forEach((resource) => {
			html.push(this.person_html(resource, false));
			days.forEach((ymd) => {
				const day = this.day_of(resource.name, ymd);
				const state = this.avail_state(day);
				const tip = this.day_tip(resource.name, ymd, day);
				if (month) {
					const hatch = state.soft > 0.01 ? " pp-soft-mini" : "";
					// The month's cell is a tint and nothing else on screen, so its words are read aloud.
					html.push(
						`<div class="pp-avail pp-mini ${state.cls}${hatch}" title="${pp_esc(tip)}">${
							state.text ? `<span class="pp-sr">${pp_esc(state.text)}</span>` : ""
						}</div>`
					);
					return;
				}
				html.push(`
					<div class="pp-avail ${state.cls}" title="${pp_esc(tip)}">
						<span class="pp-avail-text">${pp_esc(state.text)}</span>
						${this.bar_html(state)}
						${this.drive_html(resource.name, ymd, day)}
					</div>`);
			});
		});
		const $scroll = $('<div class="pp-scroll"></div>').appendTo(this.$panel);
		$('<div class="pp-res-grid"></div>')
			.css("grid-template-columns", columns)
			.css("min-width", month ? `${150 + days.length * 22}px` : "700px")
			.html(html.join(""))
			.appendTo($scroll);
	}

	render_trays() {
		this.$trays.empty();
		this.render_overdue_tray();
		const needs = (this.data.tasks || [])
			.filter((card) => this.needs_crew.has(card.name) && this.task_visible(card))
			.sort((a, b) => this.compare(a, b));
		if (needs.length) {
			const $tray = $('<div class="pp-tray pp-tray-needs"></div>').appendTo(this.$trays);
			$("<h5></h5>")
				.text(__("Needs crew ({0}): drag a person onto a task", [needs.length]))
				.appendTo($tray);
			const $cards = $('<div class="pp-tray-cards"></div>').appendTo($tray);
			$cards.html(needs.map((card) => this.card_html(card, null)).join(""));
		}

		const unscheduled = (this.data.unscheduled || [])
			.filter((card) => this.task_visible(card))
			.sort((a, b) => this.compare(a, b));
		if (unscheduled.length) {
			const $tray = $('<div class="pp-tray pp-tray-unscheduled"></div>').appendTo(this.$trays);
			// The Crew board's side tray (P7) calls it by its name there.
			const heading =
				this.view === "crew"
					? __("Not scheduled ({0}): drag onto a person's day", [unscheduled.length])
					: __("Unscheduled ({0}): drag onto a day", [unscheduled.length]);
			const $h = $("<h5></h5>")
				.append(document.createTextNode(heading))
				.appendTo($tray);
			const shown = this.show_all_unscheduled ? unscheduled : unscheduled.slice(0, PP.tray_limit);
			if (unscheduled.length > shown.length) {
				$('<button type="button" class="btn btn-default btn-xs"></button>')
					.text(__("Show all"))
					.on("click", () => {
						this.show_all_unscheduled = true;
						this.render_trays();
					})
					.appendTo($h);
			}
			const $cards = $('<div class="pp-tray-cards"></div>').appendTo($tray);
			$cards.html(shown.map((card) => this.card_html(card, null)).join(""));
		}
		this.t1_render_tray();
	}

	// The days of the range a dated task covers.
	covered(card, days) {
		if (!card.start) return [];
		const end = card.end && card.end >= card.start ? card.end : card.start;
		return days.filter((ymd) => ymd >= card.start && ymd <= end);
	}

	render_calendar() {
		const days = this.range_days();
		const today = this.today();
		const month = moment(this.anchor, "YYYY-MM-DD").month();
		const resources = this.resources();
		const visible_people = new Set(resources.map((resource) => resource.name));
		const by_day = {};
		(this.data.tasks || []).forEach((card) => {
			if (!this.task_visible(card)) return;
			this.covered(card, days).forEach((ymd) => (by_day[ymd] = by_day[ymd] || []).push(card));
		});
		const foreign_by_day = {};
		if (this.show_foreign) {
			(this.data.foreign || []).forEach((item) => {
				// A rental crew task is a Task: when it is on the board as a card, it is not repeated.
				if (item.ref && this.by_task[item.ref]) return;
				if (item.resource && !visible_people.has(item.resource)) return;
				if (item.date) (foreign_by_day[item.date] = foreign_by_day[item.date] || []).push(item);
			});
		}

		const html = [];
		const first = this.first_weekday();
		for (let i = 0; i < 7; i++) {
			html.push(`<div class="pp-dow">${pp_esc(moment().day((first + i) % 7).format("ddd"))}</div>`);
		}
		days.forEach((ymd) => {
			const day = moment(ymd, "YYYY-MM-DD");
			const classes = ["pp-day"];
			if (ymd === today) classes.push("pp-today");
			if (ymd < today) classes.push("pp-past");
			if (this.view === "month" && day.month() !== month) classes.push("pp-out");
			const label =
				this.view === "week" ? day.format("ddd, MMM D") : day.date() === 1 ? day.format("MMM D") : day.format("D");
			let free = 0;
			resources.forEach((resource) => {
				const data = this.day_of(resource.name, ymd);
				if (data) free += Math.max(0, Number(data.free) || 0);
			});
			const cards = (by_day[ymd] || []).sort((a, b) => this.compare(a, b));
			html.push(`
				<div class="${classes.join(" ")}" data-date="${pp_esc(ymd)}">
					<div class="pp-day-head">
						<span class="pp-day-num" role="button" tabindex="0" data-week="${pp_esc(ymd)}" title="${pp_esc(
							__("Show this week")
						)}" aria-label="${pp_esc(__("Show the week of {0}", [this.day_words(ymd)]))}">${pp_esc(label)}</span>
						${
							this.view === "week" && resources.length
								? `<span class="pp-day-free" title="${pp_esc(__("Free hours of the people shown"))}">${pp_esc(
										__("{0}h free", [pp_hours(free)])
								  )}</span>`
								: ""
						}
					</div>
					${this.p6d_day_html(ymd, resources)}
					${cards.map((card) => this.card_html(card, ymd)).join("")}
					${(foreign_by_day[ymd] || []).map((item) => this.foreign_html(item)).join("")}
				</div>`);
		});
		const $grid = $('<div class="pp-grid"></div>').html(html.join(""));
		this.bind_week_links($grid);
		this.$grid_wrap.empty().removeClass("pp-month pp-week pp-crew-view").addClass(`pp-${this.view}`).append($grid);
	}

	render_crew() {
		const days = this.range_days();
		const today = this.today();
		const resources = this.resources();
		const html = [`<div class="pp-crew-corner"></div>`];
		days.forEach((ymd) => {
			html.push(
				`<div class="pp-crew-head${ymd === today ? " pp-today" : ""}" role="button" tabindex="0" data-week="${pp_esc(ymd)}" title="${pp_esc(
					__("Show this week")
				)}" aria-label="${pp_esc(__("Show the week of {0}", [this.day_words(ymd)]))}">${pp_esc(pp_when(ymd))}</div>`
			);
		});
		html.push(this.p6d_crew_notes_row(days));
		const booked_on = this.crew_booked_days(resources, days);
		resources.forEach((resource) => {
			html.push(this.person_html(resource, true));
			days.forEach((ymd) => {
				const day = this.day_of(resource.name, ymd);
				const state = this.avail_state(day);
				const chips = ((day && day.bookings) || [])
					.map((booking) =>
						this.booking_html(resource.name, ymd, booking, this.crew_span_ctx(booked_on, resource.name, days, ymd, booking))
					)
					.join("");
				html.push(`
					<div class="pp-cell${ymd < today ? " pp-past" : ""}" data-date="${pp_esc(ymd)}" data-resource="${pp_esc(
					resource.name
				)}">
						<div class="pp-cap ${state.cls}" title="${pp_esc(this.day_tip(resource.name, ymd, day))}">
							<span class="pp-cap-text">${pp_esc(state.text)}</span>
							${this.bar_html(state)}
							${this.drive_html(resource.name, ymd, day)}
						</div>
						${chips}
					</div>`);
			});
		});
		html.push(this.p4_equipment_rows(days));
		this.$grid_wrap.empty().removeClass("pp-month pp-week").addClass("pp-crew-view");
		if (!resources.length) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("No planner resources to show. Add the people you schedule under Planner Resources."))
				.appendTo(this.$grid_wrap);
			return;
		}
		const $scroll = $('<div class="pp-scroll"></div>').appendTo(this.$grid_wrap);
		const $crew = $('<div class="pp-crew"></div>').html(html.join("")).appendTo($scroll);
		this.bind_week_links($crew);
	}

	// ------------------------------------------------------------------ crew board in days (P7)
	//
	// A task is a bar along a person's row: its days, side by side, one chip a day (a chip is the unit
	// that is dragged, resized and tapped, so every move still works on it). The chips of one task on
	// neighboring days are drawn joined (pp-span-prev / pp-span-next), and each says "Day 2 of 5" where the
	// old chip said its hours. "Show hours" puts the hours (or the time slot) back beside the days.

	// {person: {task: Set of the days of this range it is booked}}.
	crew_booked_days(resources, days) {
		const out = {};
		resources.forEach((resource) => {
			const mine = (out[resource.name] = {});
			days.forEach((ymd) => {
				const day = this.day_of(resource.name, ymd);
				((day && day.bookings) || []).forEach((booking) => {
					if (!booking.ref || booking.kind === "drive" || booking.kind === "block" || !this.by_task[booking.ref]) return;
					(mine[booking.ref] = mine[booking.ref] || new Set()).add(ymd);
				});
			});
		});
		return out;
	}

	// How a chip sits in its task's bar: joined to the day before and the day after, and which day of how
	// many it is. null for what is not a task of this planner (a block, a visit, a drive).
	crew_span_ctx(booked_on, resource, days, ymd, booking) {
		const card = booking && booking.ref && this.by_task[booking.ref];
		if (!card || booking.kind === "drive" || booking.kind === "block") return null;
		const set = (booked_on[resource] || {})[booking.ref] || new Set();
		const at = days.indexOf(ymd);
		return {
			prev: at > 0 && set.has(days[at - 1]),
			next: at >= 0 && at < days.length - 1 && set.has(days[at + 1]),
			count: this.crew_day_count(card, ymd, set, days),
		};
	}

	// Day n of N for one chip. When the whole task is inside the days on screen it is counted from this
	// person's bookings (so a day off inside it is not a day of work); a task that runs past the edge of
	// the range is counted in weekdays from the task's own dates, which is the same number for a plain
	// Monday-to-Friday crew.
	crew_day_count(card, ymd, set, days) {
		const start = card.start;
		const end = card.end && card.end >= start ? card.end : start;
		if (!start) return { n: 1, total: 1 };
		if (set.size && start >= days[0] && end <= days[days.length - 1]) {
			const list = Array.from(set).sort();
			return { n: Math.max(1, list.indexOf(ymd) + 1), total: list.length };
		}
		const utc = (ymd_text) => Date.UTC(Number(ymd_text.slice(0, 4)), Number(ymd_text.slice(5, 7)) - 1, Number(ymd_text.slice(8, 10)));
		const weekdays = (a, b) => {
			let count = 0;
			for (let t = utc(a); t <= utc(b); t += 86400000) {
				const dow = new Date(t).getUTCDay();
				if (dow !== 0 && dow !== 6) count += 1;
			}
			return count;
		};
		return { n: Math.max(1, weekdays(start, ymd)), total: Math.max(1, weekdays(start, end)) };
	}

	crew_days_text(count) {
		return count.total > 1 ? __("Day {0} of {1}", [count.n, count.total]) : __("1 day");
	}

	set_crew_hours(on) {
		this.crew_hours = !!on;
		this.save_pref(PP.prefs_crew_hours, this.crew_hours ? "1" : "0");
		// The header is not redrawn: the checkbox already shows the state, and redrawing it would take the
		// keyboard's focus away from the box that was just toggled.
		if (this.view === "crew" && this.data) this.render();
	}

	// The Crew tab's header: Board | Capacity, and on the Board "Show hours". Its own box under the
	// toolbar, so it stays put while the grids under it are redrawn.
	render_crew_bar() {
		if (!this.$crewbar) return;
		const capacity = this.view === PP.capacity_view;
		const here = this.view === "crew" || capacity;
		this.$crewbar.toggle(here).empty();
		if (!here) return;
		const $seg = $('<span class="pp-seg pp-crewbar-seg" role="group"></span>')
			.attr("aria-label", __("Crew screens"))
			.appendTo(this.$crewbar);
		[
			["crew", __("Board"), __("Who is where, a bar a task, in days")],
			[PP.capacity_view, __("Capacity"), __("Eight weeks of load per person, and the Crew Utilization figures")],
		].forEach(([view, label, title]) => {
			const on = view === "crew" ? !capacity : capacity;
			$('<button type="button"></button>')
				.attr("data-crew-screen", view)
				.attr("aria-pressed", on ? "true" : "false")
				.attr("title", title)
				.toggleClass("pp-on", on)
				.text(label)
				.on("click", () => {
					if (!on) this.go(view, this.anchor);
				})
				.appendTo($seg);
		});
		if (capacity) return;
		const $hours = $('<label class="pp-crewbar-hours"></label>').appendTo(this.$crewbar);
		$('<input type="checkbox">')
			.prop("checked", !!this.crew_hours)
			.on("change", (e) => this.set_crew_hours(!!e.currentTarget.checked))
			.appendTo($hours);
		$("<span></span>").text(__("Show hours")).appendTo($hours);
	}

	// The words a day link's label reads its date in (S3.6).
	day_words(ymd) {
		return moment(ymd, "YYYY-MM-DD").format("dddd, MMMM D");
	}

	// A week opened from the keyboard (S3.6): the day link that had focus is redrawn away, so the
	// toolbar title takes it rather than <body>. A mouse click leaves focus where the browser put it.
	take_title_focus() {
		if (!(this.focus_title_until > Date.now())) return;
		this.focus_title_until = 0;
		const title = this.$title && this.$title[0];
		if (title && typeof title.focus === "function") title.focus();
	}

	// A day number or a crew-view date head opens that week. Enter and Space do what a click does
	// (S3.6); with the quick looks on, their own capture-phase handlers take both first.
	bind_week_links($root) {
		const open = (e) => this.go("week", e.currentTarget.getAttribute("data-week"));
		$root
			.find("[data-week]")
			.on("click", open)
			.on("keydown", (e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				e.preventDefault();
				this.focus_title_until = Date.now() + 2000;
				open(e);
			});
	}

	// One booking in a crew-view cell: a task the planner can move, or someone else's (static).
	// `span` (the crew board's, P7): how the chip sits in its task's bar, see crew_span_ctx.
	booking_html(resource, ymd, booking, span) {
		// Driving is padding on the day's hours, not a thing to open: the cell's drive line shows it.
		if (booking.kind === "drive") return "";
		if (booking.kind === "block") return this.p6d_block_html(resource, ymd, booking);
		const card = booking.ref && this.by_task[booking.ref];
		const hours = booking.slot ? booking.slot.join("–") : `${pp_hours(booking.hours)}h`;
		if (card) {
			const classes = ["pp-card"];
			if (span) {
				classes.push("pp-span");
				if (span.prev) classes.push("pp-span-prev");
				if (span.next) classes.push("pp-span-next");
			}
			if (card.movable && this.data.can_edit) classes.push("pp-movable");
			if (card.saving) classes.push("pp-saving");
			if (!this.task_visible(card)) classes.push("pp-dim");
			if (card.overdue) classes.push("pp-overdue");
			const pencil = !!(card.tentative || booking.tentative);
			if (pencil) classes.push("pp-tentative");
			const color = pp_color(card.color, PP.default_color);
			const warned = this.dependency_lines(card).length > 0 || this.gap_lines(card).length > 0;
			const tip = [
				card.subject,
				card.project_title || card.project,
				hours,
				pencil ? __("Pencil (tentative)") : "",
				...this.dependency_lines(card),
				...this.gap_lines(card),
			]
				.filter(Boolean)
				.join("\n");
			// The crew board counts in days and shows the hours only when asked for (P7).
			const sub = [
				span ? this.crew_days_text(span.count) : "",
				!span || this.crew_hours ? hours : "",
				pencil ? __("Pencil") : "",
				warned ? "⚠" : "",
			]
				.filter(Boolean)
				.join(" · ");
			return `
				<div class="${classes.join(" ")}" data-task="${pp_esc(card.name)}" data-date="${pp_esc(ymd)}"
					data-resource="${pp_esc(resource)}" tabindex="0" title="${pp_esc(
					[tip, ...this.p4_tip_lines(card)].filter(Boolean).join("\n")
				)}" style="--pp-color: ${pp_esc(color)}">
					<div class="pp-card-title">${pp_esc(card.subject || card.name)}</div>
					<div class="pp-card-sub">${pp_esc(sub)}</div>
					<div class="pp-card-chips">${this.p4_card_chips(card).join("")}</div>
				</div>`;
		}
		if (!this.show_foreign) return "";
		// v1.601.0: a pencilled maintenance visit is hatched, like a pencil task.
		const pencil = booking.tentative ? " pp-tentative" : "";
		const sub = [hours, booking.tentative ? __("Pencil") : ""].filter(Boolean).join(" · ");
		return `
			<div class="pp-fcard pp-k-${pp_esc(booking.kind || "other")}${pencil}" data-ref="${pp_esc(booking.ref || "")}"
				data-kind="${pp_esc(booking.kind || "")}" data-date="${pp_esc(ymd)}" tabindex="0"
				title="${pp_esc([booking.label, sub].filter(Boolean).join("\n"))}">
				<div class="pp-fcard-title">${pp_esc(booking.label || booking.ref)}</div>
				<div class="pp-fcard-sub">${pp_esc(sub)}</div>
			</div>`;
	}

	compare(a, b) {
		return (
			String(a.project_title || a.project || "").localeCompare(String(b.project_title || b.project || "")) ||
			String(a.subject || "").localeCompare(String(b.subject || ""))
		);
	}

	// What is wrong on the days this task is booked: "Austin, Tue Oct 13: Over by 2h".
	task_conflicts(card) {
		const out = [];
		(this.task_days[card.name] || []).forEach(({ resource, ymd, day }) => {
			(day.conflicts || []).forEach((text) => out.push(`${this.resource_label(resource)}, ${pp_when(ymd)}: ${text}`));
		});
		// A vehicle or asset used twice, in the shop or retired (the card's own `conflicts` list).
		return out.concat(this.p4_card_conflicts(card));
	}

	// Planning-helper warnings on a card, as sentences. They never block anything.
	dependency_lines(card) {
		return (card.blocked_by || []).map((item) => {
			const who = item.subject ? `${item.task} (${item.subject})` : item.task;
			return item.end
				? __("Starts before {0} ends on {1}", [who, pp_when(item.end)])
				: __("Starts before {0} ends", [who]);
		});
	}

	// One qualification gap as the credential type it names ("Confined Space Entry").
	gap_type(gap) {
		const raw = gap && typeof gap === "object" ? gap.credential_type || gap.type || gap.message || "" : gap;
		return String(raw == null ? "" : raw).replace(/^No one on the crew holds:\s*/i, "");
	}

	gap_lines(card) {
		return (card.qualification_gaps || [])
			.map((gap) => this.gap_type(gap))
			.filter(Boolean)
			.map((type) => __("No one on the crew holds: {0}", [type]));
	}

	hours_text(card) {
		if (card.slot && card.slot.length === 2) return card.slot.join("–");
		if (Number(card.expected_time) > 0) return __("{0}h", [pp_hours(card.expected_time)]);
		return __("Full day");
	}

	// The calendar's label for a task: "3 days · 2 people", then the time slot or the hours when they
	// are set (only 96 of 1,764 open tasks have hours, and a task without them is a full day per person).
	// Days are working days (Monday to Friday) across the card's span, at least one.
	duration_text(card) {
		const parts = [];
		if (card.start) {
			const span = card.end && card.end > card.start ? PP6E_PURE.working_between(PP6B_PURE.ymd_add(card.start, -1), card.end) : 0;
			const days = Math.max(span, 1);
			parts.push(days === 1 ? __("1 day") : __("{0} days", [days]));
		}
		const people = (card.crew || []).length;
		if (people) parts.push(people === 1 ? __("1 person") : __("{0} people", [people]));
		if (card.slot && card.slot.length === 2) parts.push(card.slot.join("–"));
		else if (Number(card.expected_time) > 0) parts.push(__("{0}h", [pp_hours(card.expected_time)]));
		return parts.join(" · ");
	}

	badges_html(card) {
		const crew = card.crew || [];
		const shown = crew.slice(0, 4);
		const badges = shown.map((member) => {
			const name = this.resource_label(member.resource, member.label);
			const parts = String(name).split(/\s+/).filter(Boolean);
			const initials = ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
			const tip = member.is_lead ? `${name} (${__("lead")})` : name;
			const cls = member.is_lead ? "pp-badge pp-lead" : "pp-badge";
			return `<span class="${cls}" style="--pp-color: ${pp_esc(
				this.resource_color(member.resource)
			)}" title="${pp_esc(tip)}">${pp_esc(initials)}</span>`;
		});
		if (crew.length > shown.length) {
			badges.push(`<span class="pp-badge pp-more">+${pp_esc(crew.length - shown.length)}</span>`);
		}
		return badges.length ? `<span class="pp-badges">${badges.join("")}</span>` : "";
	}

	// A task card. `ymd` is the day it is drawn on (null in a tray): a drag measures the move from it.
	card_html(card, ymd) {
		const chips = [];
		const red = (text) => `<span class="pp-chip pp-red">${pp_esc(text)}</span>`;
		const plain = (text) => `<span class="pp-chip">${pp_esc(text)}</span>`;
		if (ymd && card.start && card.end && card.end > card.start) {
			const length = moment(card.end, "YYYY-MM-DD").diff(moment(card.start, "YYYY-MM-DD"), "days") + 1;
			const index = moment(ymd, "YYYY-MM-DD").diff(moment(card.start, "YYYY-MM-DD"), "days") + 1;
			chips.push(plain(__("Day {0} of {1}", [index, length])));
		}
		if (Number(card.short) > 0) chips.push(`<span class="pp-chip pp-amber">${pp_esc(__("Needs {0} more", [card.short]))}</span>`);
		else if (!(card.crew || []).length && this.needs_crew.has(card.name)) {
			chips.push(`<span class="pp-chip pp-amber">${pp_esc(__("No crew"))}</span>`);
		}
		const conflicts = this.task_conflicts(card);
		if (conflicts.length) chips.push(red(__("Conflict")));
		if (card.overdue) chips.push(red(__("Overdue")));
		if (card.rental_kind) chips.push(plain(__(card.rental_kind)));
		if (!card.start) chips.push(plain(__("No dates")));
		this.p4_card_chips(card).forEach((chip) => chips.push(chip));
		if (card.tentative) chips.unshift(`<span class="pp-chip pp-pencil">${pp_esc(__("Pencil"))}</span>`);
		const amber = (text) => `<span class="pp-chip pp-amber">${pp_esc(text)}</span>`;
		const blocked = card.blocked_by || [];
		if (blocked.length) {
			const first = blocked[0].subject || blocked[0].task;
			chips.push(
				amber(
					blocked.length > 1
						? __("Starts before {0} +{1}", [first, blocked.length - 1])
						: __("Starts before {0}", [first])
				)
			);
		}
		const gaps = (card.qualification_gaps || []).map((gap) => this.gap_type(gap)).filter(Boolean);
		if (gaps.length) {
			chips.push(
				amber(gaps.length > 1 ? __("Missing {0} +{1}", [gaps[0], gaps.length - 1]) : __("Missing {0}", [gaps[0]]))
			);
		}
		chips.push(...this.weather_chips(card, ymd));

		const classes = ["pp-card"];
		if (card.movable && this.data.can_edit) classes.push("pp-movable");
		if (card.overdue) classes.push("pp-overdue");
		if (card.tentative) classes.push("pp-tentative");
		if (card.saving) classes.push("pp-saving");
		const color = pp_color(card.color, PP.default_color);
		const sub = [card.project_title || card.project, this.duration_text(card)].filter(Boolean).join(" · ");
		const crew_names = (card.crew || []).map((member) => this.resource_label(member.resource, member.label));
		const tip = [
			card.subject,
			sub,
			card.tentative ? __("Pencil (tentative): not counted as booked") : "",
			crew_names.join(", "),
			...conflicts,
			...this.dependency_lines(card),
			...this.gap_lines(card),
			...this.p4_tip_lines(card),
			...this.weather_lines(card),
		]
			.filter(Boolean)
			.join("\n");
		// Cards in the Needs crew and Unscheduled trays offer to find a day (ymd is null there).
		const suggest =
			!ymd && card.movable && this.data.can_edit
				? `<div class="pp-suggest-row"><button type="button" class="btn btn-default btn-xs pp-suggest" data-suggest="${pp_esc(
						card.name
				  )}">${pp_esc(__("Suggest dates"))}</button></div>`
				: "";
		// A pencilled task can be made firm from its card (the server checks it like any change).
		const firm =
			card.tentative && card.movable && this.data.can_edit && this.view !== "month"
				? `<div class="pp-firm-row"><button type="button" class="btn btn-default btn-xs pp-firm" data-firm="${pp_esc(
						card.name
				  )}">${pp_esc(__("Firm up"))}</button></div>`
				: "";
		return `
			<div class="${classes.join(" ")}" data-task="${pp_esc(card.name)}" data-date="${pp_esc(ymd || "")}"
				tabindex="0" title="${pp_esc(tip)}" style="--pp-color: ${pp_esc(color)}">
				<div class="pp-card-top">
					<span class="pp-card-title">${pp_esc(card.subject || card.name)}</span>
					${this.badges_html(card)}
				</div>
				<div class="pp-card-sub">${pp_esc(sub)}</div>
				<div class="pp-card-chips">${chips.join("")}</div>
				${suggest}${firm}
			</div>`;
	}

	foreign_html(item) {
		const kinds = { visit: __("Maintenance"), rental: __("Rental"), travel: __("Travel") };
		const who = item.resource ? this.resource_label(item.resource) : "";
		// v1.601.0: a pencilled maintenance visit is hatched and says so, like a pencil task.
		const sub = [
			kinds[item.kind] || item.kind,
			who,
			item.hours ? `${pp_hours(item.hours)}h` : "",
			item.tentative ? __("Pencil") : "",
		]
			.filter(Boolean)
			.join(" · ");
		const pencil = item.tentative ? " pp-tentative" : "";
		return `
			<div class="pp-fcard pp-k-${pp_esc(item.kind || "other")}${pencil}" data-fkey="${pp_esc(item.key)}" tabindex="0"
				title="${pp_esc([item.label, sub].filter(Boolean).join("\n"))}">
				<div class="pp-fcard-title">${pp_esc(item.label || item.ref)}</div>
				<div class="pp-fcard-sub">${pp_esc(sub)}</div>
			</div>`;
	}

	open_foreign(kind, ref, ymd) {
		if (kind === "travel" && ref) frappe.set_route("Form", "Travel Trip", ref);
		else if (kind === "rental" && ref) frappe.set_route("Form", "Task", ref);
		else if (kind === "visit") {
			// The Maintenance Planner page is not open to Projects Users.
			const roles = ["System Manager", "Projects Manager", "Maintenance Supervisor", "Maintenance User"];
			if (frappe.user && frappe.user.has_role && frappe.user.has_role(roles)) {
				frappe.set_route("maintenance-planner", "week", ymd || this.anchor);
			} else {
				frappe.show_alert({ message: __("A maintenance visit: it moves on the Maintenance Planner."), indicator: "blue" });
			}
		}
	}

	// ------------------------------------------------------------------ dragging

	bind_drag() {
		const root = this.$body[0];
		root.addEventListener("pointerdown", (e) => this.on_down(e));
		document.addEventListener("pointermove", (e) => this.on_move(e));
		document.addEventListener("pointerup", (e) => this.on_up(e));
		document.addEventListener("pointercancel", (e) => this.on_cancel(e));
		document.addEventListener("keydown", (e) => {
			if (e.key === "Escape" && this.drag) this.end_drag();
		});
		// Once a card has lifted, a finger moving it must not scroll the page. Non-passive, or
		// the browser ignores preventDefault here.
		document.addEventListener(
			"touchmove",
			(e) => {
				if (this.drag && this.drag.active) e.preventDefault();
			},
			{ passive: false }
		);
		root.addEventListener("contextmenu", (e) => {
			if (this.drag) e.preventDefault();
		});
		root.addEventListener("click", (e) => {
			if (Date.now() < this.click_blocked_until) return;
			this.activate(e.target);
		});
		root.addEventListener("keydown", (e) => {
			if (e.key !== "Enter" && e.key !== " ") return;
			if (!e.target.closest || !e.target.closest(".pp-card, .pp-fcard, .pp-route, .pp-hm-cell, .pp-ecard")) return;
			e.preventDefault();
			this.activate(e.target);
		});
	}

	activate(target) {
		if (!target || !target.closest) return;
		const route_el = target.closest(".pp-route[data-route-resource]");
		if (route_el) {
			this.go_route(route_el.getAttribute("data-route-resource"), route_el.getAttribute("data-route-date"));
			return;
		}
		const cell_el = target.closest(".pp-hm-cell[data-hm-week]");
		if (cell_el) {
			this.go("week", cell_el.getAttribute("data-hm-week"));
			return;
		}
		const equip_el = target.closest(".pp-ecard[data-etask]");
		if (equip_el) {
			const name = equip_el.getAttribute("data-etask");
			const task = this.by_task[name];
			if (task) this.open_card(task);
			else frappe.set_route("Form", "Task", name);
			return;
		}
		const firm_el = target.closest(".pp-firm[data-firm]");
		if (firm_el) {
			const task = this.by_task[firm_el.getAttribute("data-firm")];
			if (task) this.firm_up(task);
			return;
		}
		const od_el = target.closest(".pp-od-card[data-od-task]");
		if (od_el) {
			if (target.closest("input, button")) return;
			const name = od_el.getAttribute("data-od-task");
			const task = this.by_task[name];
			if (task) this.open_card(task);
			else frappe.set_route("Form", "Task", name);
			return;
		}
		const suggest_el = target.closest(".pp-suggest[data-suggest]");
		if (suggest_el) {
			const task = this.by_task[suggest_el.getAttribute("data-suggest")];
			if (task) this.suggest_dates(task);
			return;
		}
		const card_el = target.closest(".pp-card[data-task]");
		if (card_el) {
			const card = this.by_task[card_el.getAttribute("data-task")];
			if (card) this.open_card(card);
			return;
		}
		const foreign_el = target.closest(".pp-fcard");
		if (!foreign_el) return;
		const item = this.by_foreign[foreign_el.getAttribute("data-fkey")];
		if (item) this.open_foreign(item.kind, item.ref, item.date);
		else
			this.open_foreign(
				foreign_el.getAttribute("data-kind"),
				foreign_el.getAttribute("data-ref"),
				foreign_el.getAttribute("data-date")
			);
	}

	// What is under the pointer when it went down: a task card, or a person.
	drag_source(el) {
		if (!this.data || !this.data.can_edit) return null;
		if (el.closest(".p6b-grip")) return null; // Phase 6B: an edge resizes, it never moves
		if (el.closest(".pk-drawer")) return this.p6a_drag_source(el);
		const person_el = el.closest(".pp-person[data-resource]");
		if (person_el) return { kind: "person", el: person_el, resource: person_el.getAttribute("data-resource") };
		// One overdue row at a time; its tick box and buttons stay clickable.
		const od_el = el.closest(".pp-od-card[data-od-task]");
		if (od_el) {
			if (el.closest("input, button")) return null;
			const row = this.overdue_row(od_el.getAttribute("data-od-task"));
			return row ? { kind: "overdue", el: od_el, row, from_date: null, from_resource: null } : null;
		}
		const card_el = el.closest(".pp-card[data-task]");
		if (!card_el) return null;
		const card = this.by_task[card_el.getAttribute("data-task")];
		if (!card || !card.movable || card.saving) return null;
		return {
			kind: "card",
			el: card_el,
			card,
			from_date: card_el.getAttribute("data-date") || null,
			from_resource: card_el.getAttribute("data-resource") || null,
		};
	}

	on_down(e) {
		if (e.button > 0 || this.drag || !e.target.closest) return;
		const source = this.drag_source(e.target);
		if (!source) return;
		this.drag = { source, el: source.el, x: e.clientX, y: e.clientY, id: e.pointerId, touch: e.pointerType === "touch" };
		if (this.drag.touch) {
			this.drag.timer = setTimeout(() => this.lift(), PP.hold_ms);
		} else {
			// No mousedown, so a drag does not select the text of every day it crosses. The
			// click that opens the card still fires.
			e.preventDefault();
		}
	}

	on_move(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const far = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > PP.drag_px;
		if (!drag.active) {
			if (drag.touch) {
				// Moved before the hold finished: that is a scroll, not a drag.
				if (far) this.end_drag();
				return;
			}
			if (!far) return;
			this.lift();
		}
		e.preventDefault();
		this.place_ghost(e.clientX, e.clientY);
		this.mark_target(this.find_target(e.clientX, e.clientY));
		this.edge_scroll(e.clientX, e.clientY);
	}

	// Near the top or bottom of the window, scroll so a far week is reachable; near the side of
	// a sideways-scrolling grid (the crew view on a phone), scroll that.
	edge_scroll(x, y) {
		this.p6a_edge_scroll(x, y);
		const edge = 56;
		if (y < edge) window.scrollBy(0, -14);
		else if (y > window.innerHeight - edge) window.scrollBy(0, 14);
		const el = document.elementFromPoint(x, y);
		const scroller = el && el.closest(".pp-scroll");
		if (!scroller) return;
		const rect = scroller.getBoundingClientRect();
		if (x < rect.left + 40) scroller.scrollLeft -= 14;
		else if (x > rect.right - 40) scroller.scrollLeft += 14;
	}

	on_up(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const was_active = drag.active;
		const target = was_active ? this.find_target(e.clientX, e.clientY) : null;
		if (target) target.solo = this.as4_solo(e);
		this.end_drag();
		if (!was_active) return;
		this.click_blocked_until = Date.now() + 400;
		if (target) this.drop(drag.source, target);
	}

	on_cancel(e) {
		if (this.drag && e.pointerId === this.drag.id) this.end_drag();
	}

	lift() {
		const drag = this.drag;
		if (!drag || drag.active) return;
		drag.active = true;
		const rect = drag.el.getBoundingClientRect();
		drag.dx = drag.x - rect.left;
		drag.dy = drag.y - rect.top;
		drag.ghost = drag.el.cloneNode(true);
		drag.ghost.classList.add("pp-ghost");
		drag.ghost.style.width = `${Math.max(rect.width, 120)}px`;
		document.body.appendChild(drag.ghost);
		drag.el.classList.add("pp-dragging");
		document.body.classList.add("pp-drag-active");
		this.place_ghost(drag.x, drag.y);
		if (drag.touch && navigator.vibrate) navigator.vibrate(12);
		this.p6b_lift(drag);
	}

	place_ghost(x, y) {
		const drag = this.drag;
		if (!drag || !drag.ghost) return;
		drag.ghost.style.left = `${x - drag.dx}px`;
		drag.ghost.style.top = `${y - drag.dy}px`;
	}

	// Where a drag would land. A person lands on a task card; a task lands on a crew-view cell
	// (a person and a day) or a calendar day.
	find_target(x, y) {
		const drag = this.drag;
		const el = document.elementFromPoint(x, y);
		if (!drag || !el || !el.closest || !this.$body[0].contains(el)) return null;
		if (drag.source.kind === "person") {
			const card_el = el.closest(".pp-card[data-task]");
			return card_el ? { el: card_el, task: card_el.getAttribute("data-task") } : null;
		}
		const cell = el.closest(".pp-cell[data-date][data-resource]");
		if (cell) return { el: cell, date: cell.getAttribute("data-date"), resource: cell.getAttribute("data-resource") };
		const day = el.closest(".pp-day[data-date]");
		if (day) return { el: day, date: day.getAttribute("data-date") };
		return null;
	}

	mark_target(target) {
		const drag = this.drag;
		if (!drag) return;
		if (drag.target_el && (!target || drag.target_el !== target.el)) {
			drag.target_el.classList.remove("pp-over", "pp-over-past");
		}
		drag.target_el = target ? target.el : null;
		if (!target) return;
		target.el.classList.add(target.date && target.date < this.today() ? "pp-over-past" : "pp-over");
	}

	end_drag() {
		const drag = this.drag;
		this.drag = null;
		if (!drag) return;
		clearTimeout(drag.timer);
		if (drag.ghost) drag.ghost.remove();
		if (drag.target_el) drag.target_el.classList.remove("pp-over", "pp-over-past");
		drag.el.classList.remove("pp-dragging");
		document.body.classList.remove("pp-drag-active");
	}

	// ------------------------------------------------------------------ dropping

	// A task dragged from the day it was drawn on to another keeps its length and moves by the
	// same number of days, so dragging day 2 of a 3-day job one day later moves the whole job
	// one day later. From a tray there is no "from" day; the task starts on the drop day.
	shifted_start(card, from_date, to_date) {
		if (!card.start || !from_date) return to_date;
		const delta = moment(to_date, "YYYY-MM-DD").diff(moment(from_date, "YYYY-MM-DD"), "days");
		return pp_ymd(moment(card.start, "YYYY-MM-DD").add(delta, "days"));
	}

	crew_has(card, resource) {
		return (card.crew || []).some((member) => member.resource === resource);
	}

	// The crew as save_task takes it. Blank and 0 hours mean the same thing to the engine (an
	// even share, or a full day), and the server stores a blank as 0, so both become null here.
	crew_rows(card) {
		return (card.crew || []).map((member) => ({
			resource: member.resource,
			hours: Number(member.hours) > 0 ? Number(member.hours) : null,
			is_lead: member.is_lead ? 1 : 0,
		}));
	}

	// Work out what a drop means, as one API call. Returns null when it changes nothing.
	plan_drop(source, target) {
		if (source.kind === "person") {
			const card = this.by_task[target.task];
			if (!card) return null;
			const name = this.resource_label(source.resource);
			if (!card.movable) {
				return { refuse: __("{0} cannot be changed from the planner.", [card.subject || card.name]) };
			}
			if (this.crew_has(card, source.resource)) {
				return { refuse: __("{0} is already on {1}.", [name, card.subject || card.name]) };
			}
			return {
				card,
				method: "add_crew",
				args: { task: card.name, resource: source.resource },
				message: __("{0} added to {1}", [name, card.subject || card.name]),
			};
		}

		const card = source.card;
		const start = this.shifted_start(card, source.from_date, target.date);
		const moved = start !== card.start;
		const subject = card.subject || card.name;
		const moved_message = __("{0} moved to {1}", [subject, pp_when(start)]);

		if (target.resource && source.from_resource && target.resource !== source.from_resource) {
			const from = this.resource_label(source.from_resource);
			const to = this.resource_label(target.resource);
			if (this.crew_has(card, target.resource)) {
				return { refuse: __("{0} is already on {1}.", [to, subject]) };
			}
			const args = { task: card.name, from_resource: source.from_resource, to_resource: target.resource };
			if (moved) args.date = start;
			return {
				card,
				method: "swap_crew",
				args,
				past: moved && target.date < this.today() ? target.date : null,
				message: moved
					? __("{0}: {1} in place of {2}, moved to {3}", [subject, to, from, pp_when(start)])
					: __("{0}: {1} in place of {2}", [subject, to, from]),
			};
		}

		const args = { task: card.name, start };
		// A task dropped from a tray onto a person's row in the crew view is booked for them too.
		const adds = target.resource && !source.from_resource && !this.crew_has(card, target.resource);
		if (adds) {
			args.crew = JSON.stringify(
				this.crew_rows(card).concat([{ resource: target.resource, hours: null, is_lead: 0 }])
			);
		}
		if (!moved && !adds) return null;
		return {
			card,
			method: "save_task",
			args,
			past: moved && target.date < this.today() ? target.date : null,
			message: adds
				? __("{0} booked for {1} on {2}", [subject, this.resource_label(target.resource), pp_when(start)])
				: moved_message,
		};
	}

	drop(source, target) {
		if (this.p6b_drop(source, target)) return;
		if (source.kind === "overdue") {
			this.drop_overdue(source.row, target);
			return;
		}
		const plan = this.plan_drop(source, target);
		if (!plan) return;
		if (plan.refuse) {
			frappe.show_alert({ message: plan.refuse, indicator: "orange" }, 6);
			return;
		}
		const run = () => this.commit(plan.card, plan.method, plan.args, plan.message, { solo: !!target.solo });
		if (plan.past) {
			// Tasks, unlike visits, may be recorded after the fact; a past day only needs a yes.
			frappe.confirm(__("{0} is before today. Schedule the task on a past day?", [pp_when(plan.past)]), run);
			return;
		}
		run();
	}

	// ------------------------------------------------------------------ saving

	snapshot(card) {
		return {
			task: card.name,
			subject: card.subject || card.name,
			start: card.start || null,
			end: card.end || null,
			expected_time: Number(card.expected_time) || 0,
			crew_size: Number(card.crew_size) || 0,
			crew: this.crew_rows(card),
			credentials: (card.credentials || []).slice(),
			tentative: card.tentative ? 1 : 0,
			equipment: this.p4_equipment_of(card),
		};
	}

	// Save one change to a task: fade its card, send it, ask for a reason if the server reports
	// conflicts, then reload, which is the truth either way. P4: a change of days takes the tasks that
	// follow along (not with `opts.solo`, Ctrl/Cmd held), and a published schedule asks "Notify crew?"
	// first (as4_prepare); backing out of that question sends nothing.
	commit(card, method, args, message, opts) {
		const snapshot = this.snapshot(card);
		const go = (extra) => {
			card.saving = true;
			this.render();
			snapshot.notify = extra.notify;
			snapshot.dates_only = method === "save_task" && Object.keys(args).every((key) => ["task", "start", "end"].includes(key));
			return this.send(method, Object.assign({ modified: this.modified[card.name] || card.modified }, args, extra), {
				snapshot,
				message,
				decorate: (result, sent) => this.as4_decorate(result, sent),
			});
		};
		return Promise.resolve(this.as4_prepare([this.as4_change(card, method, args)], opts, go)).finally(() => this.load());
	}

	// Resolves either way: a refusal has already shown the server's own message.
	send(method, args, opts) {
		opts = opts || {};
		args = this.with_draft(method, args);
		return Promise.resolve(frappe.call({ method: `${(this.p6_send_modules || {})[method] || PP.api}.${method}`, args }))
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.needs_reason) {
					if (args.reason) {
						// The server asked again although a reason was sent: stop rather than loop.
						frappe.msgprint(__("The change was not saved. Refresh the planner and try again."));
						return null;
					}
					const ask = opts.auto_reason
						? Promise.resolve(opts.auto_reason)
						: this.ask_reason(result.conflicts || {});
					return ask.then((reason) =>
						reason ? this.send(method, Object.assign({}, args, { reason }), opts) : null
					);
				}
				if (opts.decorate) opts.decorate(result, opts);
				const toasted = opts.snapshot ? this.push_undo(opts.snapshot, opts.message) : false;
				if (result.name && result.modified) this.modified[result.name] = result.modified;
				if (opts.message && !toasted) frappe.show_alert({ message: opts.message, indicator: "green" }, 5);
				(result.warnings || []).forEach((warning) =>
					frappe.show_alert({ message: warning, indicator: "orange" }, 10)
				);
				return result;
			})
			.catch(() => null);
	}

	// The conflicts a change would cause, per person, and a required reason. Resolves with the
	// reason, or null when the planner backs out.
	ask_reason(conflicts) {
		return new Promise((resolve) => {
			let settled = false;
			const settle = (value) => {
				if (settled) return;
				settled = true;
				resolve(value);
			};
			const people = Object.entries(conflicts || {})
				.map(
					([who, list]) =>
						`<div class="pp-why"><b>${pp_esc(this.p4_conflict_heading(who))}</b><ul>${(list || [])
							.map((text) => `<li>${pp_esc(text)}</li>`)
							.join("")}</ul></div>`
				)
				.join("");
			const dialog = new frappe.ui.Dialog({
				title: __("This booking has conflicts"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "conflicts",
						options: `<p>${pp_esc(
							__("You can save it anyway. Say why: the reason is added to the task's timeline.")
						)}</p>${people}`,
					},
					{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason"), reqd: 1 },
				],
				primary_action_label: __("Save anyway"),
				primary_action: (values) => {
					const reason = String((values && values.reason) || "").trim();
					if (!reason) return;
					settle(reason);
					dialog.hide();
				},
			});
			dialog.onhide = () => settle(null);
			dialog.show();
		});
	}

	push_undo(snapshot, message) {
		this.undo_stack.push(snapshot);
		while (this.undo_stack.length > PP.undo_max) this.undo_stack.shift();
		this.update_undo_button();
		return this.p6a_undo_toast(snapshot, message);
	}

	update_undo_button() {
		if (!this.$undo) return;
		const last = this.undo_stack[this.undo_stack.length - 1];
		this.$undo
			.toggleClass("pp-empty", !last)
			.attr("title", last ? __("Undo the last change to {0}", [last.subject]) : __("Nothing to undo"));
	}

	// Put the task back the way it was before the last change. Every field is sent, so an undo
	// restores dates, hours and crew together. save_task reads a blank date as "unchanged", so a
	// task dragged out of the Unscheduled tray keeps its new dates and the planner says so.
	undo() {
		this.p6a_close_toast();
		const snap = this.undo_stack.pop();
		this.update_undo_button();
		if (!snap) {
			frappe.show_alert({ message: __("Nothing to undo"), indicator: "blue" }, 4);
			return;
		}
		if (this.t1_undo(snap)) return;
		if (this.p6b_undo(snap)) return;
		if (this.as4_undo(snap)) return;
		const card = this.by_task[snap.task];
		if (card) {
			card.saving = true;
			this.render();
		}
		const args = {
			task: snap.task,
			modified: this.modified[snap.task] || (card && card.modified) || "",
			expected_time: snap.expected_time,
			crew_size: snap.crew_size,
			crew: JSON.stringify(snap.crew),
			credentials: JSON.stringify(snap.credentials),
		};
		if (snap.tentative != null) args.tentative = snap.tentative;
		if (snap.equipment) args.equipment = JSON.stringify(snap.equipment);
		// P4: the change's "Notify crew?" answer, again: whoever was told hears it went back.
		if (snap.notify != null) args.notify = snap.notify;
		if (snap.start) {
			args.start = snap.start;
			args.end = snap.end || snap.start;
		}
		this.send("save_task", args, {
			message: __("Undone: {0}", [snap.subject]),
			auto_reason: __(PP.undo_reason),
		})
			.then((result) => {
				if (result && !snap.start && card && card.start) {
					frappe.show_alert(
						{
							message: __("{0} keeps its dates: clear them on the task form to unschedule it.", [snap.subject]),
							indicator: "orange",
						},
						10
					);
				}
			})
			.finally(() => this.load());
	}

	// ------------------------------------------------------------------ dialog

	open_card(card) {
		const editable = !!(card.movable && this.data && this.data.can_edit);
		const rows = [];
		const add = (label, value) => {
			if (value) rows.push(`<tr><th>${pp_esc(label)}</th><td>${pp_esc(value)}</td></tr>`);
		};
		add(__("Project"), card.project_title && card.project ? `${card.project_title} (${card.project})` : card.project);
		add(__("Status"), card.status ? __(card.status) : "");
		if (card.start) {
			const span =
				card.end && card.end !== card.start
					? `${frappe.datetime.str_to_user(card.start)} – ${frappe.datetime.str_to_user(card.end)}`
					: frappe.datetime.str_to_user(card.start);
			add(__("Dates"), span);
		} else {
			add(__("Dates"), __("Not scheduled"));
		}
		if (card.slot && card.slot.length === 2) add(__("Time"), card.slot.join("–"));
		if (card.rental_kind) add(__("Rental"), __(card.rental_kind));
		const booked = (card.crew || [])
			.map((member) => {
				const name = this.resource_label(member.resource, member.label);
				const lead = member.is_lead ? ` (${__("lead")})` : "";
				return member.booked != null ? `${name}${lead}: ${pp_hours(member.booked)}h` : `${name}${lead}`;
			})
			.join(", ");
		add(__("Booked"), booked || __("Nobody yet"));
		if (Number(card.short) > 0) add(__("Short"), __("Needs {0} more", [card.short]));
		this.task_conflicts(card).forEach((text) => add(__("Conflict"), text));
		if (card.overdue) add(__("Overdue"), __("The task's end date has passed."));
		if (card.tentative) {
			add(__("Pencil"), __("Tentative: pencilled in, shown as soft load and not counted as booked until firmed up."));
		}
		this.dependency_lines(card).forEach((text) => add(__("Dependency"), text));
		if ((card.depends_on || []).length) add(__("Depends on"), card.depends_on.join(", "));
		this.gap_lines(card).forEach((text) => add(__("Qualification"), text));
		rows.push(...this.p4_dialog_rows(card));
		this.weather_lines(card).forEach((text) => add(__("Weather"), text));
		if (!editable) add(__("Note"), __("This task is read-only here."));

		// v1.603.0: a drafted new task is not a Task yet: there is no form to open, and the lookups
		// below (suggestions, flags, history, print) all read the Task, so only the project is offered.
		const fresh = !!card.new_task;
		if (fresh) add(__("Draft"), __("New task: it is created when you publish your drafts."));
		const links = fresh ? [] : [["task", __("Open task")]];
		if (card.project) links.push(["project", __("Open project")]);
		if (editable && !fresh) links.push(["suggest", __("Suggest dates")]);
		if (editable && card.tentative) links.push(["firm", __("Firm up")]);
		if (!fresh) {
			links.push(...this.phase5_links(card));
			links.push(...this.p6a_links(card));
			links.push(...this.p6c_links(card));
		}
		const fields = [
			{
				fieldtype: "HTML",
				fieldname: "summary",
				options:
					`<table class="pp-summary">${rows.join("")}</table>` +
					`<div class="pp-links">${links
						.map(
							([action, label]) =>
								`<button type="button" class="btn btn-default btn-xs" data-action="${action}">${pp_esc(label)}</button>`
						)
						.join("")}</div>`,
			},
		];
		if (editable) {
			fields.push(
				{ fieldtype: "Section Break", label: __("Schedule") },
				{ fieldtype: "Date", fieldname: "start", label: __("Start date"), default: card.start || "" },
				{ fieldtype: "Date", fieldname: "end", label: __("End date"), default: card.end || "" },
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Float",
					fieldname: "expected_time",
					label: __("Expected hours"),
					default: Number(card.expected_time) || 0,
					description: __("Blank or 0: each person is booked a full day for each day of the task."),
				},
				{
					fieldtype: "Int",
					fieldname: "crew_size",
					label: __("Crew needed"),
					default: Number(card.crew_size) || 0,
				},
				{
					fieldtype: "Check",
					fieldname: "tentative",
					label: __("Tentative (pencil)"),
					default: card.tentative ? 1 : 0,
					description: __("A pencilled task is soft load: it never needs a reason and is not counted as booked."),
				},
				...this.phase5_dialog_fields(card),
				{ fieldtype: "Section Break", label: __("Crew") },
				{
					fieldtype: "Table",
					fieldname: "crew",
					label: __("Crew"),
					cannot_add_rows: false,
					in_place_edit: true,
					data: this.crew_rows(card).map((row) => Object.assign({}, row, { hours: row.hours == null ? "" : row.hours })),
					fields: [
						{
							fieldtype: "Link",
							fieldname: "resource",
							options: "Planner Resource",
							label: __("Person"),
							in_list_view: 1,
							reqd: 1,
							columns: 5,
						},
						{
							fieldtype: "Float",
							fieldname: "hours",
							label: __("Hours"),
							in_list_view: 1,
							columns: 3,
							description: __("Blank: an even share of the expected hours, or a full day."),
						},
						{ fieldtype: "Check", fieldname: "is_lead", label: __("Lead"), in_list_view: 1, columns: 2 },
					],
				},
				{
					fieldtype: "MultiSelectPills",
					fieldname: "credentials",
					label: __("Qualifications needed"),
					get_data: (txt) => frappe.db.get_link_options("Credential Type", txt),
				}
			);
			fields.push(...this.p4_equipment_fields(card));
		}

		const dialog = this.p6a_dialog({ title: card.subject || card.name, fields, size: "large" }, card);
		if (editable) {
			dialog.set_primary_action(__("Save"), (values) => this.save_dialog(card, dialog, values));
		}
		dialog.$wrapper.find("[data-action]").on("click", (e) => {
			const action = e.currentTarget.getAttribute("data-action");
			dialog.hide();
			if (this.p6a_action(action, card)) return;
			if (this.p6c_action(action, card)) return;
			if (action === "task") frappe.set_route("Form", "Task", card.name);
			else if (action === "project") frappe.set_route("Form", "Project", card.project);
			else if (action === "suggest") this.suggest_dates(card);
			else if (action === "firm") this.firm_up(card);
			else this.phase5_action(action, card);
		});
		dialog.show();
		if (editable && (card.credentials || []).length) dialog.set_value("credentials", card.credentials.slice());
		this.p4_refine_actuals(card, dialog);
	}

	save_dialog(card, dialog, values) {
		values = values || {};
		const args = { task: card.name };
		const start = values.start || "";
		const end = values.end || "";
		// save_task reads a blank date as "unchanged", so a dated task cannot be sent back to
		// the Unscheduled tray from here; say so rather than appear to save it.
		if (!start && card.start) {
			frappe.msgprint(__("To unschedule a task, clear its dates on the task form."));
			return;
		}
		if (!start && end) {
			frappe.msgprint(__("Set a start date as well."));
			return;
		}
		if (start && end && end < start) {
			frappe.msgprint(__("The end date is before the start date."));
			return;
		}
		// The dialog shows both dates, so both are sent when either changed: what the planner
		// typed is what is saved, rather than the end following the start as it does on a drag.
		const last = start ? end || start : "";
		if (start !== (card.start || "") || last !== (card.end || card.start || "")) {
			args.start = start;
			args.end = last;
		}
		const expected = Number(values.expected_time) || 0;
		if (expected !== (Number(card.expected_time) || 0)) args.expected_time = expected;
		const crew_size = Math.max(0, parseInt(values.crew_size, 10) || 0);
		if (crew_size !== (Number(card.crew_size) || 0)) args.crew_size = crew_size;
		const tentative = values.tentative ? 1 : 0;
		if (tentative !== (card.tentative ? 1 : 0)) args.tentative = tentative;

		const crew = (values.crew || [])
			.filter((row) => row && row.resource)
			.map((row) => ({
				resource: row.resource,
				hours: Number(row.hours) > 0 ? Number(row.hours) : null,
				is_lead: row.is_lead ? 1 : 0,
			}));
		if (JSON.stringify(crew) !== JSON.stringify(this.crew_rows(card))) args.crew = JSON.stringify(crew);
		const credentials = (values.credentials || []).filter(Boolean);
		const before = (card.credentials || []).slice().sort();
		if (JSON.stringify(credentials.slice().sort()) !== JSON.stringify(before)) {
			args.credentials = JSON.stringify(credentials);
		}
		this.p4_equipment_arg(card, values, args);

		dialog.hide();
		// Phase 5: Outdoor work / Customer-facing visit are saved first, on their own (see save_phase5_flags).
		const flags = this.save_phase5_flags(card, values);
		if (Object.keys(args).length === 1) {
			flags.then((saved) => saved && this.load());
			return;
		}
		flags.then(() => this.commit(card, "save_task", args, __("{0} updated", [card.subject || card.name])));
	}

	// ------------------------------------------------------------------ suggestions

	// Ask the server which days suit this task by drive time, then offer them in a dialog.
	suggest_dates(card) {
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.suggest_dates`,
				args: { task: card.name },
				freeze: true,
				freeze_message: __("Looking for the best days…"),
			})
		)
			.then((r) => this.show_suggestions(card, (r && r.message) || {}))
			.catch(() => null);
	}

	show_suggestions(card, result) {
		const suggestions = result.suggestions || [];
		const subject = card.subject || card.name;
		const parts = [];
		if (result.note) parts.push(`<div class="pp-route-note">${pp_esc(result.note)}</div>`);
		if (result.site && result.site.label) {
			parts.push(`<p class="pp-stop-sub">${pp_esc(__("Site: {0}", [result.site.label]))}</p>`);
		}
		if (!suggestions.length) {
			parts.push(
				`<p>${pp_esc(
					__("No day in the next few weeks has someone free for this task. Try another crew or shorten the task.")
				)}</p>`
			);
		} else {
			const rows = suggestions.map((item, index) => {
				const added = Number(item.added_minutes);
				const drive =
					item.added_minutes == null || !Number.isFinite(added)
						? ""
						: `<span class="pp-chip">${pp_esc(__("+{0} driving", [pp_drive(added)]))}</span>`;
				const long = item.long_drive ? `<span class="pp-chip pp-red">${pp_esc(__("Long drive"))}</span>` : "";
				const free = item.free_hours == null ? "" : pp_esc(__("{0}h free", [pp_hours(item.free_hours)]));
				return `
					<tr>
						<td class="pp-sug-when">${pp_esc(pp_when(item.date))}</td>
						<td>
							<div><b>${pp_esc(item.label || item.resource)}</b> <span class="pp-stop-sub">${free}</span></div>
							<div class="pp-sug-reason">${pp_esc(item.reason || "")}</div>
							<div>${drive}${long}${this.suggestion_weather_html(item)}</div>
						</td>
						<td><button type="button" class="btn btn-primary btn-xs" data-book="${pp_esc(index)}">${pp_esc(
					__("Book")
				)}</button></td>
					</tr>`;
			});
			parts.push(`<table class="pp-sug">${rows.join("")}</table>`);
		}
		const dialog = new frappe.ui.Dialog({
			title: __("Suggested days for {0}", [subject]),
			fields: [{ fieldtype: "HTML", fieldname: "suggestions", options: parts.join("") }],
		});
		dialog.$wrapper.find("[data-book]").on("click", (e) => {
			const item = suggestions[parseInt(e.currentTarget.getAttribute("data-book"), 10)];
			if (!item) return;
			dialog.hide();
			this.book_suggestion(card, item);
		});
		dialog.show();
	}

	// Book one suggestion exactly as a drag from a tray onto a person's row would: one save_task
	// for the day (the person is added to the crew in the same call when they are not on it), so a
	// conflict asks for a reason once, through the same flow.
	book_suggestion(card, item) {
		const args = { task: card.name, start: item.date };
		if (item.resource && !this.crew_has(card, item.resource)) {
			args.crew = JSON.stringify(this.crew_rows(card).concat([{ resource: item.resource, hours: null, is_lead: 0 }]));
		}
		const who = this.resource_label(item.resource, item.label);
		return this.commit(
			card,
			"save_task",
			args,
			__("{0} booked for {1} on {2}", [card.subject || card.name, who, pp_when(item.date)])
		);
	}

	// ------------------------------------------------------------------ tentative and dependencies

	// Turn a pencilled task into a firm booking. The server checks it like any other change, so a
	// conflict asks for a reason through the same dialog.
	firm_up(card) {
		return this.commit(
			card,
			"save_task",
			{ task: card.name, tentative: 0 },
			__("{0} is now firm", [card.subject || card.name])
		);
	}

	// P4: the old "Shift N tasks that follow?" offer (offer_shift / shift_successors) is gone. A change
	// of days takes the tasks that follow along in the same request; see as4_prepare.

	// ------------------------------------------------------------------ overdue tray

	load_overdue() {
		const token = ++this.overdue_request;
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.get_overdue`, args: { limit: PP.overdue_limit } })
		)
			.then((r) => {
				if (token !== this.overdue_request) return;
				this.overdue = this.normalize_overdue((r && r.message) || {});
				const known = new Set();
				this.overdue.groups.forEach((group) => group.rows.forEach((row) => known.add(row.name)));
				[...this.overdue_selected].forEach((name) => {
					if (!known.has(name)) this.overdue_selected.delete(name);
				});
				// The trays are the calendar views' (not Capacity's).
				if (PP.views.includes(this.view) && this.data) this.render_trays();
				this.tb_render();
			})
			.catch(() => null);
	}

	// get_overdue answers one group per project; read it forgivingly (a list or an object keyed
	// by project, rows under `tasks` or `rows`, crew as labels or as objects).
	normalize_overdue(raw) {
		const list = raw.projects || raw.groups || [];
		const entries = Array.isArray(list)
			? list
			: Object.entries(list).map(([project, group]) => Object.assign({ project }, group));
		const groups = entries.map((group) => {
			const rows = (group.tasks || group.rows || []).map((row) => ({
				name: row.name || row.task,
				subject: row.subject || row.name || row.task,
				end: row.end || row.exp_end_date || "",
				modified: row.modified || "",
				crew: (row.crew_labels || row.crew || []).map((member) =>
					typeof member === "string" ? member : member.label || member.resource || ""
				),
			}));
			return {
				project: group.project || "",
				title: group.project_title || group.title || group.project || "",
				count: Number(group.count) || rows.length,
				rows: rows.filter((row) => row.name),
			};
		});
		const total = Number(raw.total != null ? raw.total : raw.count);
		return { groups, total: Number.isFinite(total) ? total : groups.reduce((sum, group) => sum + group.count, 0) };
	}

	overdue_group_visible(group) {
		if (this.project && group.project !== this.project) return false;
		if (this.pm) {
			const project = group.project && this.by_project[group.project];
			if (!project || project.pm !== this.pm) return false;
		}
		return true;
	}

	overdue_row(name) {
		for (const group of (this.overdue && this.overdue.groups) || []) {
			const row = group.rows.find((item) => item.name === name);
			if (row) return row;
		}
		return null;
	}

	overdue_selection() {
		const rows = [];
		((this.overdue && this.overdue.groups) || []).forEach((group) =>
			group.rows.forEach((row) => {
				if (this.overdue_selected.has(row.name)) rows.push(row);
			})
		);
		return rows;
	}

	// The overdue groups the filters show, and how many tasks they hold (the tray's heading and the
	// toolbar's Overdue chip say the same number).
	overdue_visible() {
		if (!this.overdue) return { groups: [], total: 0 };
		const groups = this.overdue.groups.filter((group) => this.overdue_group_visible(group) && group.rows.length);
		return { groups, total: groups.reduce((sum, group) => sum + group.rows.length, 0) };
	}

	render_overdue_tray() {
		if (!this.overdue) return;
		const { groups, total } = this.overdue_visible();
		if (!total) return;
		const editable = !!(this.data && this.data.can_edit);
		const $tray = $('<div class="pp-od"></div>').appendTo(this.$trays);
		const $h = $("<h5></h5>").appendTo($tray);
		$("<span></span>").text(__("Overdue ({0})", [total])).appendTo($h);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(this.overdue_open ? __("Hide") : __("Show"))
			.on("click", () => {
				this.overdue_open = !this.overdue_open;
				this.render_trays();
			})
			.appendTo($h);
		if (!this.overdue_open) return;

		if (editable) {
			const $actions = $('<div class="pp-od-actions"></div>').appendTo($tray);
			[
				["reschedule", __("Reschedule to…"), "btn-default"],
				["complete", __("Mark done"), "btn-default"],
				["cancel", __("Cancel"), "btn-default"],
			].forEach(([action, label, cls]) => {
				$(`<button type="button" class="btn ${cls} btn-xs pp-od-act"></button>`)
					.attr("data-od-action", action)
					.text(label)
					.on("click", () => this.overdue_act(action))
					.appendTo($actions);
			});
			$('<span class="pp-od-count"></span>').appendTo($actions);
		}
		const html = [];
		groups.forEach((group) => {
			const all = group.rows.every((row) => this.overdue_selected.has(row.name));
			html.push(`<div class="pp-od-project">${
				editable
					? `<input type="checkbox" data-od-project="${pp_esc(group.project)}" ${all ? "checked" : ""} aria-label="${pp_esc(
							__("Select all overdue tasks of {0}", [group.title || group.project])
					  )}">`
					: ""
			}<span>${pp_esc(group.title || group.project || __("No project"))}</span><span class="pp-od-count">${pp_esc(
				group.rows.length
			)}</span></div>`);
			group.rows.forEach((row) => {
				const crew = row.crew.filter(Boolean).join(", ");
				html.push(`
					<div class="pp-od-card" data-od-task="${pp_esc(row.name)}" title="${pp_esc(
					[row.subject, crew, editable ? __("Drag onto a day to reschedule it") : ""].filter(Boolean).join("\n")
				)}">
						${
							editable
								? `<input type="checkbox" data-od-pick="${pp_esc(row.name)}" ${
										this.overdue_selected.has(row.name) ? "checked" : ""
								  } aria-label="${pp_esc(__("Select {0}", [row.subject]))}">`
								: ""
						}
						<div class="pp-od-main">
							<div class="pp-od-subject">${pp_esc(row.subject)}</div>
							<div class="pp-od-sub">${pp_esc(crew || __("No crew"))}</div>
						</div>
						<span class="pp-od-end">${pp_esc(row.end ? __("Ended {0}", [pp_when(row.end)]) : "")}</span>
					</div>`);
			});
		});
		const $list = $('<div class="pp-od-list"></div>').html(html.join("")).appendTo($tray);
		$tray.on("change", "input[data-od-pick], input[data-od-project]", (e) => {
			const input = e.target;
			if (input.hasAttribute("data-od-pick")) {
				const name = input.getAttribute("data-od-pick");
				if (input.checked) this.overdue_selected.add(name);
				else this.overdue_selected.delete(name);
			} else {
				const project = input.getAttribute("data-od-project");
				const group = groups.find((item) => item.project === project);
				((group && group.rows) || []).forEach((row) => {
					if (input.checked) this.overdue_selected.add(row.name);
					else this.overdue_selected.delete(row.name);
				});
			}
			this.sync_overdue($tray, groups);
		});
		this.sync_overdue($tray, groups);
		return $list;
	}

	// Keep the ticks, the count and the action buttons in step with the selection.
	sync_overdue($tray, groups) {
		groups.forEach((group) => {
			const picked = group.rows.filter((row) => this.overdue_selected.has(row.name)).length;
			const box = $tray.find("input[data-od-project]").filter((i, el) => el.getAttribute("data-od-project") === group.project)[0];
			if (box) {
				box.checked = picked === group.rows.length;
				box.indeterminate = picked > 0 && picked < group.rows.length;
			}
			group.rows.forEach((row) => {
				const pick = $tray.find("input[data-od-pick]").filter((i, el) => el.getAttribute("data-od-pick") === row.name)[0];
				if (pick) pick.checked = this.overdue_selected.has(row.name);
			});
		});
		const chosen = this.overdue_selection().length;
		$tray.find(".pp-od-act").prop("disabled", !chosen);
		$tray.find(".pp-od-actions .pp-od-count").text(chosen ? __("{0} selected", [chosen]) : __("Tick tasks to act on them"));
	}

	overdue_list_html(rows) {
		return `<ul>${rows
			.slice(0, 30)
			.map((row) => `<li>${pp_esc(row.subject)} <span class="pp-stop-sub">(${pp_esc(row.name)})</span></li>`)
			.join("")}${rows.length > 30 ? `<li>${pp_esc(__("and {0} more", [rows.length - 30]))}</li>` : ""}</ul>`;
	}

	overdue_act(action) {
		const rows = this.overdue_selection();
		if (!rows.length) return;
		if (action === "reschedule") {
			this.ask_reschedule(rows);
			return;
		}
		const done = action === "complete";
		const head = done
			? __("Mark these {0} task(s) as Completed?", [rows.length])
			: __("Cancel these {0} task(s)? They are set to Canceled and leave the planner.", [rows.length]);
		frappe.confirm(`<p>${pp_esc(head)}</p>${this.overdue_list_html(rows)}`, () =>
			this.run_bulk(
				rows.map((row) => row.name),
				action,
				null
			)
		);
	}

	ask_reschedule(rows) {
		const dialog = new frappe.ui.Dialog({
			title: __("Reschedule {0} overdue task(s)", [rows.length]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "tasks",
					options: `<p>${pp_esc(
						__("Each task starts on the day you pick and keeps its length. Conflicts ask for a reason.")
					)}</p>${this.overdue_list_html(rows)}`,
				},
				{ fieldtype: "Date", fieldname: "date", label: __("Start on"), reqd: 1, default: this.today() },
			],
			primary_action_label: __("Reschedule"),
			primary_action: (values) => {
				const date = (values && values.date) || "";
				if (!date) return;
				if (date < this.today()) {
					frappe.msgprint(__("Pick today or a later day: the tasks would still be overdue."));
					return;
				}
				dialog.hide();
				this.run_bulk(
					rows.map((row) => row.name),
					"reschedule",
					date
				);
			},
		});
		dialog.show();
	}

	// One bulk_update. Conflicts (rescheduling) ask for a reason once for the whole batch; a task
	// that fails is reported and never stops the rest.
	run_bulk(names, action, date) {
		const args = { tasks: JSON.stringify(names), action };
		if (date) args.date = date;
		return this.send("bulk_update", args)
			.then((result) => {
				if (!result) return;
				const failed = result.failed || result.errors || [];
				const failed_names = new Set(
					failed.map((item) => (typeof item === "string" ? item : item.task || item.name))
				);
				names.forEach((name) => {
					if (!failed_names.has(name)) this.overdue_selected.delete(name);
				});
				const ok = Array.isArray(result.updated) ? result.updated.length : Math.max(names.length - failed.length, 0);
				const label = {
					reschedule: __("{0} task(s) rescheduled to {1}", [ok, date ? pp_when(date) : ""]),
					complete: __("{0} task(s) marked Completed", [ok]),
					cancel: __("{0} task(s) set to Canceled", [ok]),
				}[action];
				if (ok) frappe.show_alert({ message: label, indicator: "green" }, 6);
				if (failed.length) {
					const lines = failed.map((item) => {
						if (typeof item === "string") return `<li>${pp_esc(item)}</li>`;
						const why = item.error || item.message || item.reason || "";
						return `<li>${pp_esc(item.task || item.name)}${why ? `: ${pp_esc(why)}` : ""}</li>`;
					});
					frappe.msgprint({
						title: __("Some tasks were not changed"),
						message: `<p>${pp_esc(__("{0} of {1} task(s) failed:", [failed.length, names.length]))}</p><ul>${lines.join("")}</ul>`,
						indicator: "orange",
					});
				}
			})
			.finally(() => this.load());
	}

	// One overdue row dragged onto a day: reschedule it to start there.
	drop_overdue(row, target) {
		if (!target.date) return;
		const run = () => this.run_bulk([row.name], "reschedule", target.date);
		if (target.date < this.today()) {
			frappe.confirm(__("{0} is before today. Schedule the task on a past day?", [pp_when(target.date)]), run);
			return;
		}
		run();
	}

	// ------------------------------------------------------------------ Capacity (P7)
	//
	// The Crew tab's second screen, one view where the Heatmap and Utilization were two: eight weeks of
	// load per person on top (get_heatmap), and the Crew Utilization report's rows under them for the same
	// eight weeks (the PPU block below). Each section loads and draws on its own, so a slow report never
	// holds the heatmap back, and the report's own gate (PPU's util_allowed) decides whether its section is
	// there at all.

	load_capacity() {
		const token = ++this.request;
		this.$title.text(this.title());
		this.take_title_focus();
		this.mark_view_buttons();
		this.capacity_shell();
		this.$body.addClass("pp-loading");
		return Promise.all([this.load_heatmap(token), this.load_utilization(token)]).then(() => {
			if (token === this.request) this.$body.removeClass("pp-loading");
		});
	}

	// The two boxes the sections draw into, built once per visit to Capacity.
	capacity_shell() {
		if (this.$cap_heat && this.$cap_util) return;
		this.$grid_wrap.empty().removeClass("pp-month pp-week pp-crew-view");
		this.$cap_heat = $('<div class="pp-cap-heat"></div>').appendTo(this.$grid_wrap);
		this.$cap_util = $('<div class="pp-cap-util"></div>').appendTo(this.$grid_wrap);
	}

	render_capacity() {
		this.capacity_shell();
		this.$hint.text(
			__(
				"Each square is one person's week: firm booked hours against their hours. Hatching is pencilled (tentative) work. Driving here uses saved and estimated times only. Click a week to open it. Under the squares, the Crew Utilization report's figures for the same weeks: what was booked, and what each person clocked at the kiosk on customer jobs."
			)
		);
		this.render_heatmap();
		this.render_utilization();
	}

	load_heatmap(token) {
		const { start } = this.range();
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.get_heatmap`, args: { start: pp_ymd(start), weeks: PP.capacity_weeks } })
		)
			.then((r) => {
				if (token !== this.request) return;
				this.heatmap = this.normalize_heatmap((r && r.message) || {}, start);
				this.render_heatmap();
			})
			.catch(() => {
				if (token !== this.request) return;
				this.heatmap = null;
				this.render_heatmap();
			});
	}

	// get_heatmap answers the weeks and, per person, a cell per week. Read it forgivingly: weeks as
	// dates or objects, a person's cells as a list in week order or keyed by the week's first day.
	normalize_heatmap(raw, start) {
		let weeks = (raw.weeks || []).map((week) => (typeof week === "string" ? { start: week } : week || {}));
		weeks = weeks.map((week) => ({ start: week.start || week.week_start || week.date || "" })).filter((week) => week.start);
		if (!weeks.length) {
			// Without a week list, count on from the server's week_start (else the week asked for).
			const first = moment(raw.week_start || pp_ymd(start), "YYYY-MM-DD");
			weeks = Array.from({ length: PP.capacity_weeks }, (v, i) => ({ start: pp_ymd(first.clone().add(i * 7, "days")) }));
		}
		const people = raw.resources || raw.rows || raw.people || [];
		const rows = (Array.isArray(people) ? people : Object.entries(people).map(([name, row]) => Object.assign({ name }, row))).map(
			(person) => {
				const name = person.resource || person.name || "";
				const source = person.weeks || person.cells || (raw.cells && raw.cells[name]) || [];
				const by_start = {};
				const ordered = [];
				if (Array.isArray(source)) {
					source.forEach((cell, i) => {
						const key = (cell && (cell.start || cell.week_start || cell.week)) || (weeks[i] && weeks[i].start);
						by_start[key] = cell;
						ordered.push(cell);
					});
				} else {
					Object.entries(source).forEach(([key, cell]) => {
						by_start[key] = cell;
					});
				}
				return { name, label: person.label || name, group: person.group || "", by_start, ordered };
			}
		);
		return { weeks, rows };
	}

	// A week cell as a color class, a percentage and a short text. Firm hours only decide the
	// color; pencilled hours are drawn as a hatched stretch after them.
	heat_state(cell) {
		const capacity = Number(cell.capacity) || 0;
		const booked = Number(cell.booked) || 0;
		const soft = Math.max(0, Number(cell.soft_booked) || 0);
		const over_days = Number(cell.over_days) || 0;
		const off_days = Number(cell.off_days) || 0;
		if (capacity <= 0 && booked <= 0) {
			return { cls: "pp-idle pp-offday", pct: "", sub: soft > 0 ? __("+{0}h pencil", [pp_hours(soft)]) : __("Off"), ratio: 0, soft_ratio: 0, capacity, booked, soft, over_days, off_days };
		}
		const ratio = capacity > 0 ? booked / capacity : 2;
		// The server's band (get_heatmap `level`) decides the color; the same rule is the fallback.
		const level = ["green", "amber", "red"].includes(cell.level) ? cell.level : null;
		const cls = level ? `pp-${level}` : ratio > 1.0001 ? "pp-red" : ratio <= 0.75 ? "pp-green" : "pp-amber";
		const sub = [__("{0}h of {1}h", [pp_hours(booked), pp_hours(capacity)])];
		if (soft > 0.01) sub.push(__("+{0}h pencil", [pp_hours(soft)]));
		if (over_days > 0) sub.push(over_days === 1 ? __("1 day over") : __("{0} days over", [over_days]));
		return {
			cls,
			pct: capacity > 0 ? `${Math.round(ratio * 100)}%` : __("Off"),
			sub: sub.join(" · "),
			ratio,
			soft_ratio: capacity > 0 ? Math.min(Math.max(0, 1 - ratio), soft / capacity) : 0,
			capacity,
			booked,
			soft,
			over_days,
			off_days,
		};
	}

	heat_tip(label, week, cell, state) {
		const lines = [`${label} · ${__("week of {0}", [pp_when(week.start)])}`];
		lines.push(__("{0}h of {1}h booked", [pp_hours(state.booked), pp_hours(state.capacity)]));
		if (cell.free != null) lines.push(__("{0}h free", [pp_hours(cell.free)]));
		if (state.soft > 0.01) lines.push(__("{0}h pencilled (tentative, not counted as booked)", [pp_hours(state.soft)]));
		if (state.over_days > 0) lines.push(__("{0} day(s) over their hours", [state.over_days]));
		if (state.off_days > 0) lines.push(__("{0} day(s) off", [state.off_days]));
		lines.push(__("Click to open this week"));
		return lines.join("\n");
	}

	render_heatmap() {
		const data = this.heatmap;
		this.capacity_shell();
		const $box = this.$cap_heat.empty();
		if (!data) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("The heatmap could not be loaded. Refresh the planner and try again."))
				.appendTo($box);
			return;
		}
		const order = (group) => {
			const index = PP.groups.indexOf(group);
			return index < 0 ? PP.groups.length : index;
		};
		const rows = data.rows
			.filter((row) => !this.group || !row.group || row.group === this.group)
			.slice()
			.sort((a, b) => order(a.group) - order(b.group) || String(a.label).localeCompare(String(b.label)));
		if (!rows.length) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("No planner resources to show. Add the people you schedule under Planner Resources."))
				.appendTo($box);
			return;
		}
		const today = this.today();
		const html = [`<div class="pp-hm-head"></div>`];
		data.weeks.forEach((week) => {
			const end = pp_ymd(moment(week.start, "YYYY-MM-DD").add(6, "days"));
			const current = today >= week.start && today <= end;
			html.push(
				`<div class="pp-hm-head${current ? " pp-today" : ""}">${pp_esc(moment(week.start, "YYYY-MM-DD").format("MMM D"))}</div>`
			);
		});
		rows.forEach((row) => {
			html.push(
				`<div class="pp-hm-name"><span class="pp-dot" style="--pp-color: ${pp_esc(
					this.resource_color(row.name)
				)}"></span><span title="${pp_esc(row.label)}">${pp_esc(row.label)}</span></div>`
			);
			data.weeks.forEach((week, index) => {
				const cell = row.by_start[week.start] || row.ordered[index] || {};
				const state = this.heat_state(cell);
				const bar = state.capacity > 0 ? this.bar_html(state) : "";
				html.push(`
					<div>
						<div class="pp-hm-cell ${state.cls}${state.soft > 0.01 && state.capacity <= 0 ? " pp-soft-mini" : ""}" role="button" tabindex="0"
							data-hm-week="${pp_esc(week.start)}" title="${pp_esc(this.heat_tip(row.label, week, cell, state))}"
							aria-label="${pp_esc(`${row.label}, ${pp_when(week.start)}: ${state.pct || state.sub}`)}">
							<span class="pp-hm-pct">${pp_esc(state.pct)}</span>
							<span class="pp-hm-sub">${pp_esc(state.sub)}</span>
							${bar}
						</div>
					</div>`);
			});
		});
		const columns = `170px repeat(${data.weeks.length},minmax(78px,1fr))`;
		const $scroll = $('<div class="pp-section pp-hm-scroll"></div>').appendTo($box);
		$('<div class="pp-hm"></div>').css("grid-template-columns", columns).html(html.join("")).appendTo($scroll);
		$(`<div class="pp-hm-legend">
				<span class="pp-hm-key"><i class="pp-green"></i>${pp_esc(__("Up to 75% booked"))}</span>
				<span class="pp-hm-key"><i class="pp-amber"></i>${pp_esc(__("Up to 100%"))}</span>
				<span class="pp-hm-key"><i class="pp-red"></i>${pp_esc(__("Over (or a day over)"))}</span>
				<span class="pp-hm-key"><i class="pp-soft-key"></i>${pp_esc(__("Pencilled (tentative) hours"))}</span>
			</div>`).appendTo($box);
	}

	// ------------------------------------------------------------------ copy week

	// The first day of the week holding `ymd`, by the site's first weekday: the weeks that
	// copy_week and get_heatmap use.
	week_start_of(ymd) {
		const day = moment(ymd, "YYYY-MM-DD");
		return pp_ymd(day.clone().subtract((day.day() - this.first_weekday() + 7) % 7, "days"));
	}

	open_copy_week() {
		if (!this.data || !this.data.can_edit) return;
		// The source week is the one on screen (the site's first weekday to the day before it).
		const first = this.week_start_of(this.anchor);
		const last = pp_ymd(moment(first, "YYYY-MM-DD").add(6, "days"));
		const tasks = (this.data.tasks || [])
			.filter((card) => card.movable && card.start && card.start >= first && card.start <= last && this.task_visible(card))
			.sort((a, b) => String(a.start).localeCompare(String(b.start)) || this.compare(a, b));
		if (!tasks.length) {
			frappe.msgprint(
				__("No task starts in the week of {0}. (Tasks that began in an earlier week are not copied.)", [pp_when(first)])
			);
			return;
		}
		const rows = tasks.map((card) => {
			const when = card.end && card.end !== card.start ? `${pp_when(card.start)} – ${pp_when(card.end)}` : pp_when(card.start);
			const sub = [card.project_title || card.project, when, this.hours_text(card)].filter(Boolean).join(" · ");
			const pencil = card.tentative ? ` <span class="pp-chip pp-pencil">${pp_esc(__("Pencil"))}</span>` : "";
			return `
				<label class="pp-copy-row">
					<input type="checkbox" data-copy="${pp_esc(card.name)}" checked>
					<span><b>${pp_esc(card.subject || card.name)}</b>${pencil}
						<div class="pp-copy-sub">${pp_esc(sub)}</div></span>
				</label>`;
		});
		const dialog = new frappe.ui.Dialog({
			title: __("Copy week of {0}", [pp_when(first)]),
			size: "large",
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "tasks",
					options:
						`<p>${pp_esc(
							__("Copies become new tasks with the same crew, hours and qualifications, shifted by whole weeks. The originals stay where they are.")
						)}</p>` +
						`<div class="pp-links">
							<button type="button" class="btn btn-default btn-xs" data-copy-all="1">${pp_esc(__("Select all"))}</button>
							<button type="button" class="btn btn-default btn-xs" data-copy-all="0">${pp_esc(__("Select none"))}</button>
						</div>` +
						`<div class="pp-copy-list">${rows.join("")}</div>`,
				},
				{
					fieldtype: "Date",
					fieldname: "target",
					label: __("Copy into the week holding"),
					reqd: 1,
					default: pp_ymd(moment(first, "YYYY-MM-DD").add(7, "days")),
				},
			],
			primary_action_label: __("Preview"),
			primary_action: (values) => {
				const target_start = this.week_start_of((values && values.target) || "");
				const names = [];
				dialog.$wrapper.find("input[data-copy]").each((i, el) => {
					if (el.checked) names.push(el.getAttribute("data-copy"));
				});
				if (!values || !values.target) return;
				if (!names.length) {
					frappe.msgprint(__("Tick at least one task to copy."));
					return;
				}
				if (target_start === first) {
					frappe.msgprint(__("Pick a different week to copy into."));
					return;
				}
				dialog.hide();
				this.preview_copy(first, target_start, names);
			},
		});
		dialog.$wrapper.on("click", "[data-copy-all]", (e) => {
			const on = e.currentTarget.getAttribute("data-copy-all") === "1";
			dialog.$wrapper.find("input[data-copy]").prop("checked", on);
		});
		dialog.show();
	}

	// A dry run first: what would be created, and which conflicts it would cause. Nothing is
	// written until the planner confirms.
	preview_copy(source_start, target_start, names) {
		const base = { source_start, target_start, tasks: JSON.stringify(names) };
		return this.send("copy_week", Object.assign({ dry_run: 1 }, base)).then((result) => {
			if (!result) return;
			const copies = result.copies || [];
			if (!copies.length) {
				frappe.msgprint(__("Nothing to copy."));
				return;
			}
			const conflicts = result.conflicts || {};
			const clashes = Object.entries(conflicts).filter(([who, list]) => (list || []).length);
			const table = copies
				.map((item) => {
					const known = this.by_task[item.task];
					const was = known && known.start ? `${pp_when(known.start)} → ` : "";
					const to =
						item.to_end && item.to_end !== item.to_start
							? `${pp_when(item.to_start)} – ${pp_when(item.to_end)}`
							: pp_when(item.to_start);
					return `<tr><td><b>${pp_esc(item.subject || item.task)}</b></td><td>${pp_esc(was)}${pp_esc(to)}</td></tr>`;
				})
				.join("");
			const skipped = (result.skipped || []).map(
				(entry) => `<li>${pp_esc(entry.task)}${entry.reason ? `: ${pp_esc(entry.reason)}` : ""}</li>`
			);
			const skip_note = skipped.length
				? `<div class="pp-route-note"><b>${pp_esc(__("Not copied"))}</b><ul>${skipped.join("")}</ul></div>`
				: "";
			const warn = clashes.length
				? `<div class="pp-route-note"><b>${pp_esc(__("These copies would cause conflicts"))}</b>${clashes
						.map(
							([who, list]) =>
								`<div class="pp-why"><b>${pp_esc(this.p4_conflict_heading(who))}</b><ul>${list.map((text) => `<li>${pp_esc(text)}</li>`).join("")}</ul></div>`
						)
						.join("")}<div>${pp_esc(__("You can copy anyway; you will be asked for a reason."))}</div></div>`
				: "";
			const dialog = new frappe.ui.Dialog({
				title: __("Copy {0} task(s) into the week of {1}?", [copies.length, pp_when(target_start)]),
				size: "large",
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "preview",
						options: `${warn}<table class="pp-copy-table">${table}</table>${skip_note}`,
					},
				],
				primary_action_label: clashes.length ? __("Copy anyway") : __("Copy"),
				primary_action: () => {
					dialog.hide();
					this.send("copy_week", Object.assign({ dry_run: 0 }, base)).then((done) => {
						if (!done) return;
						const made = (done.copies || done.created || []).length || copies.length;
						frappe.show_alert(
							{ message: __("{0} task(s) copied into the week of {1}", [made, pp_when(target_start)]), indicator: "green" },
							6
						);
						this.go(this.view, target_start);
					});
				},
			});
			dialog.show();
		});
	}

	// ------------------------------------------------------------------ route view

	load_route() {
		const token = ++this.request;
		this.$title.text("");
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.get_route`,
				args: { resource: this.route_resource, date: this.route_date },
			})
		)
			.then((r) => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.route_data = (r && r.message) || null;
				this.render_route(token);
			})
			.catch(() => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.route_data = null;
				this.render_route(token);
			});
	}

	clear_route_map() {
		const state = this.route_map;
		this.route_map = null;
		if (!state) return;
		(Object.values(state.markers || {}) || []).forEach((marker) => {
			if (marker && marker.setMap) marker.setMap(null);
		});
		if (state.line && state.line.setMap) state.line.setMap(null);
	}

	route_back_to_week() {
		this.go("week", this.route_date || frappe.datetime.get_today());
	}

	render_route(token) {
		this.clear_route_map();
		const data = this.route_data;
		const date = this.route_date;
		const label = (data && data.label) || this.route_resource;
		const stops = (data && data.stops) || [];
		const head = [];
		const step = (sign) =>
			this.go_route(this.route_resource, pp_ymd(moment(date, "YYYY-MM-DD").add(sign, "days")));

		this.$route.empty();
		const $head = $('<div class="pp-route-head"></div>').appendTo(this.$route);
		$('<button type="button" class="btn btn-default btn-sm">‹</button>')
			.attr("title", __("Previous day"))
			.attr("aria-label", __("Previous day"))
			.on("click", () => step(-1))
			.appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm">›</button>')
			.attr("title", __("Next day"))
			.attr("aria-label", __("Next day"))
			.on("click", () => step(1))
			.appendTo($head);
		$('<span class="pp-route-title"></span>')
			.text(`${label} · ${moment(date, "YYYY-MM-DD").format("dddd, MMM D, YYYY")}`)
			.appendTo($head);
		$('<span class="pp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Back to week"))
			.on("click", () => this.route_back_to_week())
			.appendTo($head);
		const maps_url = data && data.maps_url;
		if (maps_url && /^https:\/\/www\.google\.com\/maps\//.test(maps_url)) {
			$('<button type="button" class="btn btn-primary btn-sm"></button>')
				.text(__("Open in Google Maps"))
				.on("click", () => window.open(maps_url, "_blank", "noopener"))
				.appendTo($head);
		}

		if (!data) {
			$('<div class="pp-route-note"></div>')
				.text(__("The route could not be loaded. Check that this person is on the Planner Resources list, then refresh."))
				.appendTo(this.$route);
			return;
		}

		// Totals.
		const sum_legs = stops.reduce((total, stop) => total + (Number(stop.drive_minutes) || 0), 0);
		const totals = [];
		totals.push(`<b>${pp_esc(__("Driving"))}</b> ${pp_esc(pp_drive(data.drive_minutes))}`);
		if (Number(data.km) > 0) totals.push(pp_esc(`${pp_mi(data.km)} mi`));
		if (Number(data.drive_minutes) > 0 && data.source) totals.push(pp_esc(this.source_text(data.source)));
		totals.push(pp_esc(__("{0} stop(s)", [stops.length])));
		const long = data.long_drive ? `<span class="pp-chip pp-red">${pp_esc(__("Long drive"))}</span>` : "";
		$(`<div class="pp-route-totals">${totals.join(" · ")}${long}</div>`).appendTo(this.$route);

		// Notices.
		if (data.travel) head.push(data.travel);
		if (data.off) head.push(data.off);
		if (!data.start) head.push(__("The shop address could not be located, so the route starts at the first stop."));
		if (data.source && data.source !== "google" && Number(data.drive_minutes) > 0) {
			head.push(__("Some drive times are straight-line estimates, not Google's."));
		}
		if (!stops.length) head.push(__("Nothing to drive to on this day."));
		head.forEach((text) => $('<div class="pp-route-note"></div>').text(text).appendTo(this.$route));
		const $map_note = $('<div class="pp-route-note"></div>').hide().appendTo(this.$route);

		// The ordered list, then the map: on a phone the list comes first.
		const $cols = $('<div class="pp-route-cols"></div>').appendTo(this.$route);
		const html = [];
		if (data.start) {
			html.push(`
				<div class="pp-stop">
					<span class="pp-stop-num pp-shop">S</span>
					<div class="pp-stop-body">
						<div class="pp-stop-label">${pp_esc(data.start.label || __("Shop"))}</div>
						<div class="pp-stop-sub">${pp_esc(data.start.address || "")}</div>
					</div>
				</div>`);
		}
		let previous_located = !!data.start;
		stops.forEach((stop) => {
			const located = !!stop.located && pp_valid_point(stop.lat, stop.lng);
			const when =
				stop.arrive || stop.depart ? [stop.arrive, stop.depart].filter(Boolean).join("–") : "";
			const sub = [
				stop.project_title || stop.project,
				stop.slot && stop.slot.length === 2 ? stop.slot.join("–") : "",
				Number(stop.hours) > 0 ? `${pp_hours(stop.hours)}h` : "",
			]
				.filter(Boolean)
				.join(" · ");
			let leg = "";
			if (located && previous_located && Number(stop.drive_minutes) >= 0 && stop.drive_minutes != null) {
				const dist = Number(stop.km) > 0 ? ` · ${pp_mi(stop.km)} mi` : "";
				leg = `<div class="pp-stop-leg">${pp_esc(`${pp_drive(stop.drive_minutes)}${dist}`)}</div>`;
			}
			if (located) previous_located = true;
			const waits =
				Number(stop.wait_minutes) >= 5
					? `<div class="pp-stop-leg">${pp_esc(__("Waits {0} for the start time", [pp_drive(stop.wait_minutes)]))}</div>`
					: "";
			const missing = located
				? ""
				: `<div class="pp-stop-missing">${pp_esc(__("No location — set the task's address"))}</div>`;
			const open =
				(stop.kind === "task" || stop.kind === "rental") && stop.ref
					? `<span class="pp-stop-link" role="button" tabindex="0" data-open-task="${pp_esc(stop.ref)}">${pp_esc(
							__("Open task")
					  )}</span>`
					: "";
			html.push(`
				<div class="pp-stop${located ? "" : " pp-unlocated"}" data-marker="${pp_esc(located ? stop.order : "")}">
					<span class="pp-stop-num">${pp_esc(stop.order)}</span>
					<div class="pp-stop-body">
						${when ? `<div class="pp-stop-time">${pp_esc(when)}</div>` : ""}
						<div class="pp-stop-label">${pp_esc(stop.label || stop.ref)}</div>
						${sub ? `<div class="pp-stop-sub">${pp_esc(sub)}</div>` : ""}
						${stop.address ? `<div class="pp-stop-sub">${pp_esc(stop.address)}</div>` : ""}
						${leg}${waits}${missing}${this.stop_weather_html(stop)}${open ?`<div class="pp-stop-leg">${open}</div>` : ""}
					</div>
				</div>`);
		});
		// The way home: the server prices it (end.drive_minutes); the difference is the fallback.
		const back =
			data.end && data.end.drive_minutes != null
				? Number(data.end.drive_minutes) || 0
				: (Number(data.drive_minutes) || 0) - sum_legs;
		if (data.end && stops.some((stop) => stop.located) && back > 0.5) {
			html.push(`
				<div class="pp-stop">
					<span class="pp-stop-num pp-shop">S</span>
					<div class="pp-stop-body">
						<div class="pp-stop-label">${pp_esc(__("Back at {0}", [data.end.label || __("Shop")]))}</div>
						<div class="pp-stop-leg">${pp_esc(pp_drive(back))}</div>
					</div>
				</div>`);
		}
		const $list = $('<div class="pp-stops"></div>').html(html.join("")).appendTo($cols);
		$list.find("[data-open-task]").on("click keydown", (e) => {
			if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
			e.stopPropagation();
			frappe.set_route("Form", "Task", e.currentTarget.getAttribute("data-open-task"));
		});
		$list.find(".pp-stop[data-marker]").on("click", (e) => {
			const order = e.currentTarget.getAttribute("data-marker");
			if (order) this.focus_marker(order);
		});
		if (!stops.length && !data.start) $list.hide();

		const $map = $('<div class="pp-map"></div>').appendTo($cols);
		this.draw_route_map(token, data, $map, $map_note, $cols);
	}

	// Never throws, never leaves a blank pane: when Google cannot be used the list stands alone
	// with a one-line notice.
	draw_route_map(token, data, $map, $note, $cols) {
		const fail = (text) => {
			if (token !== this.request) return;
			$map.hide();
			$cols.addClass("pp-nomap");
			$note.text(text).show();
		};
		const points = (data.stops || []).filter((stop) => stop.located && pp_valid_point(stop.lat, stop.lng));
		if (!(data.stops || []).length) {
			$map.hide();
			$cols.addClass("pp-nomap");
			return;
		}
		if (!points.length) {
			fail(__("No stop on this route has a location yet, so there is nothing to draw on a map."));
			return;
		}
		if (!data.maps_key) {
			fail(__("Add a Google Maps API key in Travel Settings to see this route on a map."));
			return;
		}
		if (!window.EEGoogleMaps) {
			fail(__("The map could not be loaded, so only the list is shown."));
			return;
		}
		const start = data.start && pp_valid_point(data.start.lat, data.start.lng) ? data.start : null;
		const theme = (document.documentElement.dataset && document.documentElement.dataset.theme) || "light";
		// The markers are the list's own numbers (.pp-stop-num in project_planner.css): the same tokens,
		// read as values because the map cannot read a CSS variable.
		const colors = PP_ROUTE_COLORS();
		window.EEGoogleMaps.load({ apiKey: data.maps_key, libraries: ["maps"] })
			.then((maps) => {
				if (token !== this.request || !document.body.contains($map[0])) return;
				const first = start || points[0];
				const options = Object.assign(
					{
						zoom: 10,
						center: { lat: Number(first.lat), lng: Number(first.lng) },
						mapTypeControl: false,
						streetViewControl: false,
						fullscreenControl: true,
						gestureHandling: "cooperative",
					},
					window.EEGoogleMaps.mapOptions(data.map_ids || {}, theme)
				);
				const map = new maps.Map($map[0], options);
				const info = new maps.InfoWindow();
				const bounds = new maps.LatLngBounds();
				const markers = {};
				const add = (key, point, text, color, title, detail) => {
					const position = { lat: Number(point.lat), lng: Number(point.lng) };
					const marker = new maps.Marker({
						position,
						map,
						title,
						label: { text: String(text), color: colors.ring, fontWeight: "700", fontSize: "12px" },
						icon: {
							path: maps.SymbolPath.CIRCLE,
							scale: 13,
							fillColor: color,
							fillOpacity: 1,
							strokeColor: colors.ring,
							strokeWeight: 2,
						},
					});
					marker.addListener("click", () => {
						// Text nodes only: stop and task names are typed by people.
						const box = document.createElement("div");
						const head = document.createElement("b");
						head.textContent = title;
						box.appendChild(head);
						if (detail) {
							const line = document.createElement("div");
							line.textContent = detail;
							box.appendChild(line);
						}
						info.setContent(box);
						info.open({ map, anchor: marker });
					});
					bounds.extend(position);
					markers[key] = marker;
				};
				const path = [];
				if (start) {
					add("shop", start, "S", colors.shop, start.label || __("Shop"), start.address || "");
					path.push({ lat: Number(start.lat), lng: Number(start.lng) });
				}
				points.forEach((stop) => {
					add(
						String(stop.order),
						stop,
						stop.order,
						colors.stop,
						stop.label || stop.ref || "",
						[stop.arrive, stop.address].filter(Boolean).join(" · ")
					);
					path.push({ lat: Number(stop.lat), lng: Number(stop.lng) });
				});
				if (start) path.push({ lat: Number(start.lat), lng: Number(start.lng) });
				if (path.length > 1) map.fitBounds(bounds, 56);
				else map.setZoom(14);
				this.route_map = { map, info, markers, lines: [] };
				if (path.length > 1) {
					this.draw_road(token, data, maps, path, $note).catch((err) =>
						console.warn("[project-planner] the route line could not be drawn", err)
					);
				}
			})
			.catch(() => fail(__("The map could not be loaded, so only the list is shown.")));
	}

	// The road between the stops, not a straight line from pin to pin (v1.600.1). Google draws it
	// in the browser on the map's own key, trying in turn:
	//   1. the Routes library, when Travel Settings → "Use Routes API" says the key may call it;
	//   2. the older Directions service, which every key that already draws the pick-up map has;
	//   3. straight segments, with a one-line notice, so the map is never left without a line.
	// The stops are already in the planner's driving order, so neither engine is asked to reorder
	// them, and every time and mile in the list stays the server's: this changes only the line.
	// A Routes failure is remembered for the page's life (PP.routes_unavailable): every re-draw
	// would otherwise pay for another doomed request before falling back.
	async draw_road(token, data, maps, path, $note) {
		const state = this.route_map;
		const stale = () => token !== this.request || this.route_map !== state;
		const style = { strokeColor: PP_ROUTE_COLORS().line, strokeOpacity: 0.8, strokeWeight: 4 };
		const origin = path[0];
		const destination = path[path.length - 1];
		const middle = path.slice(1, -1);
		// Directions takes 23 stops between the two ends; Routes documents 25. The smaller cap
		// covers both rungs, and a day with more stops than that is drawn straight with the notice.
		if (middle.length <= PP.road_max_stops) {
			// Both engines live in the "routes" library; the map itself loaded only "maps". It comes
			// through the shared loader like everything else. Loading a library is not a billable
			// request, and a key without the Routes API still loads it.
			let lib = maps;
			try {
				lib = await window.EEGoogleMaps.load({ apiKey: data.maps_key, libraries: ["routes"] });
			} catch (err) {
				lib = maps;
			}
			if (stale()) return;
			const Route = lib.routes && lib.routes.Route;
			if (Route && data.use_routes_api && !PP.routes_unavailable) {
				try {
					const response = await Route.computeRoutes({
						origin,
						destination,
						intermediates: middle.map((point) => ({ location: point })),
						travelMode: "DRIVING",
						// Required: computeRoutes refuses a request without a field list. Never "*".
						fields: ["path"],
					});
					if (stale()) return;
					const route = response && response.routes && response.routes[0];
					if (!route) throw new Error("computeRoutes returned no route");
					// RoutePolylineOptions has two members; the stroke goes inside polylineOptions.
					state.lines = route.createPolylines({ polylineOptions: Object.assign({ map: state.map }, style) });
					return;
				} catch (err) {
					if (stale()) return;
					PP.routes_unavailable = true;
					console.warn("[project-planner] Routes could not draw the road, trying Directions", err);
				}
			}
			try {
				const Directions = lib.DirectionsService || maps.DirectionsService;
				// No callback, so route() returns a Promise that rejects on any status but OK.
				const result = await new Directions().route({
					origin,
					destination,
					waypoints: middle.map((point) => ({ location: point, stopover: true })),
					travelMode: "DRIVING",
					optimizeWaypoints: false,
				});
				if (stale()) return;
				const road = result && result.routes && result.routes[0] && result.routes[0].overview_path;
				if (!road || !road.length) throw new Error("Directions returned no route");
				state.lines = [new maps.Polyline(Object.assign({ path: road, map: state.map }, style))];
				return;
			} catch (err) {
				if (stale()) return;
				console.warn("[project-planner] Directions could not draw the road either", err);
			}
		}
		state.lines = [new maps.Polyline(Object.assign({ path, map: state.map }, style))];
		$note
			.text(__("Google could not draw the roads, so the stops are joined by straight lines. The times and miles are still Google's."))
			.show();
	}

	// Clicking a stop in the list shows it on the map.
	focus_marker(order) {
		const state = this.route_map;
		const marker = state && state.markers[String(order)];
		if (!marker) return;
		state.map.panTo(marker.getPosition());
		window.google.maps.event.trigger(marker, "click");
	}
}

// ====================================================================== Phase 3B: telling people
//
//   /desk/project-planner/my-week/2026-10-12  the signed-in person's own week, phone-first: each
//                                             day's stops in route order with time, site, address
//                                             (tap for Google Maps), crewmates, hours and notes,
//                                             and a link to that day's route view
//
// "Hold all my changes" (Draft mode until Nik named it on 2026-10-21) is a switch in the toolbar's
// More ▸ Tools, remembered per browser. While it is on, every drag, dialog save and Undo is sent with
// draft=1, so it lands on the planner's own drafts (Planner Draft Change) instead of the Task: nobody's
// assignments move and nobody is told. Drafted cards get a dashed outline and a "Draft" chip, and a
// strip under the toolbar says "Holding your changes: N not published yet · Publish · Discard". P4: with
// the switch off, writes go with draft="auto" and a project whose schedule is Draft drafts them anyway;
// the read always asks for the drafts, and the strip lists each project's (see the P4 block at the
// end of the file). Publish asks once for a reason when the drafts conflict
// together (all or nothing) and then tells each person whose days moved. "My week" is in the Views
// menu and "Print crew sheet" in Print (toolbar redesign, v1.687.0); the crew sheet opens in a new
// window and prints there (server PDF is broken on production).
//
// Everything Phase 3B adds to the page is in this block, mixed into ProjectPlanner below, so the
// Phase 3A work on the same file merges cleanly. The hooks into the class above are one line each:
// init_phase3b (constructor), my_week_target (route_target), show_my_week (handle_route),
// draft_args (the creating writes), with_draft (send) and render_phase3b (render).
//
// v1.603.0: a NEW task can be a draft too. Quick add, duplicate and the build-out grid send draft=1 in
// Draft mode and the server keeps the task-to-be as a "New task" Draft row, not a Task: it comes back
// as a card named `draft-new:<row>` with `new_task: true`, drawn in its day (or in the Unscheduled
// tray) with a "New" chip, and counted by the engine so free hours and conflicts include it. Dragging,
// resizing, pencilling and re-crewing one go through the same save_task/add_crew/swap_crew drafts; an
// Undo discards its row; Publish creates the Task. It has no Task to open, copy or split until then.

const PP3B = {
	my_week: "my-week",
	pref: "ee_project_planner_draft",
	// The writes that go to the drafts while "Hold all my changes" is on, and (P4) on a Draft schedule.
	draft_methods: ["save_task", "add_crew", "swap_crew", "move_many"],
};

const PP3B_METHODS = {
	init_phase3b() {
		this.draft_on = this.load_pref(PP3B.pref, "0") === "1";
		this.my_week_date = "";
		this.my_week_data = null;

		// The Draft mode switch, My week and Print crew sheet are items of the toolbar's More menu (PPTB). The strip sits right under the toolbar, where the switch's state shows.
		this.$draft_bar = $('<div class="pp-draft-bar" role="region"></div>')
			.attr("aria-label", __("Draft mode"))
			.hide()
			.insertAfter(this.$toolbar);
		this.$my_week = $('<div class="pp-my-week"></div>').hide().appendTo(this.$body);
	},

	// ------------------------------------------------------------------ draft mode

	// A write that creates a task (quick add, duplicate, build out): drafted while the hold is on, and
	// (P4) when the project's schedule is Draft. The page decides here: those endpoints take draft=1 only.
	draft_args(project) {
		return this.draft_on || this.as4_draft_project(project) ? { draft: 1 } : {};
	},

	// P4: every other write says draft="auto" unless the hold is on, and the server drafts it when the
	// task's project schedule is Draft. A write that already says (an explicit draft) is left alone.
	with_draft(method, args) {
		if (!PP3B.draft_methods.includes(method) || args.draft !== undefined) return args;
		return Object.assign({}, args, { draft: PPAS_PURE.draft_value(this.draft_on) });
	},

	set_draft_mode(on) {
		this.draft_on = !!on;
		this.save_pref(PP3B.pref, this.draft_on ? "1" : "0");
		// An undo recorded in one mode must not be replayed in the other.
		this.undo_stack = [];
		this.update_undo_button();
		frappe.show_alert(
			{
				message: this.draft_on
					? __("Holding your changes until you publish them.")
					: __(
							"Hold all my changes is off: changes save straight to the tasks, except on a project whose schedule is still Draft. Your held changes are kept."
					  ),
				indicator: "blue",
			},
			7
		);
		this.load();
	},

	render_phase3b() {
		this.decorate_drafts();
		this.render_draft_bar();
	},

	// A drafted card is drawn by the same card code as any other; the outline and chip go on after.
	decorate_drafts() {
		if (!this.data) return;
		this.$body.find(".pp-card[data-task]").each((_index, el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			if (!card || !card.drafted) return;
			el.classList.add("pp-drafted");
			if (el.querySelector(".pp-draft-chip")) return;
			const holder = el.querySelector(".pp-card-chips") || el.querySelector(".pp-card-sub");
			if (holder) {
				// A task that does not exist yet says so: "New" rather than a change to a task.
				let label = card.new_task ? __("New") : __("Draft");
				// P4: another scheduler's draft on a Draft schedule says whose it is.
				if (card.drafted_by) label = __("{0} · {1}", [label, this.as4_drafter(card.drafted_by)]);
				holder.insertAdjacentHTML("afterbegin", `<span class="pp-chip pp-draft-chip">${pp_esc(label)}</span>`);
			}
		});
	},

	// The strip under the toolbar. While the hold is on, its line comes first, as before; then (P4) a
	// line per project with drafts, and its Publish (as4_render_strip). Hidden when there is nothing.
	render_draft_bar() {
		const $bar = this.$draft_bar;
		if (!$bar) return;
		if (!this.data || this.view === PP.route_view || this.view === PP3B.my_week) {
			$bar.hide().empty();
			return;
		}
		const drafts = this.data.drafts || {};
		const count = Number(drafts.count) || 0;
		const stale = drafts.stale || [];
		$bar.empty();
		let shown = false;
		if (this.draft_on || stale.length) {
			shown = true;
			const $line = $('<div class="pp-draft-line"></div>').appendTo($bar);
			if (this.draft_on) {
				$('<span class="pp-draft-text"></span>')
					.text(
						count
							? __("Holding your changes: {0} not published yet", [count])
							: __("Holding your changes: nothing held yet. They stay yours until you publish them.")
					)
					.appendTo($line);
			}
			if (stale.length) {
				$('<span class="pp-chip pp-amber"></span>')
					.text(__("{0} out of date", [stale.length]))
					.attr("title", stale.map((entry) => `${entry.subject || entry.task}: ${entry.reason}`).join("\n"))
					.appendTo($line);
			}
			$('<span class="pp-spacer"></span>').appendTo($line);
			if (stale.length) {
				$('<button type="button" class="btn btn-default btn-xs"></button>')
					.text(__("Discard out-of-date"))
					.on("click", () => this.discard_drafts(stale.map((entry) => entry.task)))
					.appendTo($line);
			}
			if (this.draft_on && count > stale.length) {
				$('<button type="button" class="btn btn-primary btn-xs"></button>')
					.text(__("Publish"))
					.on("click", () => this.publish_drafts())
					.appendTo($line);
			}
			if (this.draft_on && count) {
				$('<button type="button" class="btn btn-default btn-xs"></button>')
					.text(__("Discard"))
					.on("click", () => this.discard_drafts())
					.appendTo($line);
			}
		}
		if (this.as4_render_strip($bar)) shown = true;
		if (shown) $bar.show();
		else $bar.hide();
	},

	// Publish every draft. Conflicts across the batch come back as needs_reason; the same reason
	// dialog as a drag asks once, and the batch is sent again with it (all or nothing).
	publish_drafts(reason) {
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.publish_drafts`,
				args: reason ? { reason } : {},
				freeze: true,
				freeze_message: __("Publishing your changes…"),
			})
		)
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.needs_reason) {
					if (reason) {
						frappe.msgprint(__("Nothing was published. Refresh the planner and try again."));
						return null;
					}
					return this.ask_reason(result.conflicts || {}).then((given) =>
						given ? this.publish_drafts(given) : null
					);
				}
				this.show_publish_result(result);
				this.undo_stack = [];
				this.update_undo_button();
				return this.load();
			})
			.catch(() => null);
	},

	show_publish_result(result) {
		const published = result.published || [];
		const problems = [].concat(result.skipped || [], result.failed || []);
		const told = Number(result.notified) || 0;
		const created = published.filter((entry) => entry.new).length;
		if (!problems.length) {
			const message = created
				? __("{0} change(s) published, {1} of them new tasks; {2} people told.", [published.length, created, told])
				: __("{0} change(s) published; {1} people told.", [published.length, told]);
			frappe.show_alert({ message, indicator: "green" }, 8);
			return;
		}
		const list = problems
			.map((entry) => `<li><b>${pp_esc(entry.subject || entry.task)}</b>: ${pp_esc(entry.reason)}</li>`)
			.join("");
		frappe.msgprint({
			title: __("Published {0} of {1}", [published.length, published.length + problems.length]),
			message:
				`<p>${pp_esc(__("{0} people were told about their changes.", [told]))}</p>` +
				`<p>${pp_esc(__("Not published (still in your drafts):"))}</p><ul>${list}</ul>`,
			indicator: "orange",
		});
	},

	// Discard every draft, or only `tasks` (the out-of-date ones). The tasks stay as they are.
	discard_drafts(tasks) {
		const drafts = (this.data && this.data.drafts) || {};
		const count = tasks ? tasks.length : Number(drafts.count) || 0;
		frappe.confirm(__("Discard {0} unpublished change(s)? The tasks stay as they are.", [count]), () =>
			Promise.resolve(
				frappe.call({
					method: `${PP.api}.discard_drafts`,
					args: tasks ? { tasks: JSON.stringify(tasks) } : {},
				})
			)
				.then((r) => {
					const done = ((r && r.message) || {}).discarded || 0;
					frappe.show_alert({ message: __("{0} draft(s) discarded", [done]), indicator: "blue" }, 5);
					this.undo_stack = [];
					this.update_undo_button();
					return this.load();
				})
				.catch(() => null)
		);
	},

	// ------------------------------------------------------------------ My week

	my_week_target(route) {
		if (route[1] !== PP3B.my_week) return null;
		const date = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
		return { view: PP3B.my_week, anchor: date };
	},

	go_my_week(ymd) {
		const route = frappe.get_route() || [];
		if (route[0] === PP.route && route[1] === PP3B.my_week && route[2] === ymd) {
			this.load_my_week();
			return;
		}
		this.self_route = [PP.route, PP3B.my_week, ymd].join("/");
		this.p6a_route(() => frappe.set_route(PP.route, PP3B.my_week, ymd, this.filter_options()), false);
	},

	show_my_week(anchor) {
		this.my_week_date = anchor;
		this.set_mode(true);
		this.$route.hide();
		this.$my_week.show();
		this.load_my_week();
	},

	load_my_week() {
		const token = ++this.request;
		this.$body.addClass("pp-loading");
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.get_my_week`, args: { date: this.my_week_date } })
		)
			.then((r) => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.my_week_data = (r && r.message) || null;
				this.render_my_week();
			})
			.catch(() => {
				if (token !== this.request) return;
				this.$body.removeClass("pp-loading");
				this.my_week_data = null;
				this.render_my_week();
			});
	},

	render_my_week() {
		const data = this.my_week_data;
		const anchor = this.my_week_date || frappe.datetime.get_today();
		const step = (sign) => this.go_my_week(pp_ymd(moment(anchor, "YYYY-MM-DD").add(sign * 7, "days")));
		const $root = this.$my_week.empty();
		const $head = $('<div class="pp-route-head"></div>').appendTo($root);
		$('<button type="button" class="btn btn-default btn-sm">‹</button>')
			.attr("title", __("Previous week"))
			.attr("aria-label", __("Previous week"))
			.on("click", () => step(-1))
			.appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm">›</button>')
			.attr("title", __("Next week"))
			.attr("aria-label", __("Next week"))
			.on("click", () => step(1))
			.appendTo($head);
		const $title = $('<span class="pp-mw-title"></span>').appendTo($head);
		$('<span class="pp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Back to planner"))
			.on("click", () => this.go("week", anchor))
			.appendTo($head);

		if (!data) {
			$title.text(__("My week"));
			$('<div class="pp-route-note"></div>').text(__("Your week could not be loaded. Refresh to try again.")).appendTo($root);
			return;
		}
		const start = moment(data.start, "YYYY-MM-DD");
		const end = moment(data.end, "YYYY-MM-DD");
		$title.text(`${data.label || __("My week")} · ${start.format("MMM D")} – ${end.format("MMM D, YYYY")}`);
		if (!data.resource) {
			$('<div class="pp-route-note"></div>').text(data.message || __("There is no week to show.")).appendTo($root);
			return;
		}
		(data.days || []).forEach((entry) => {
			const items = entry.items || [];
			const $day = $('<div class="pp-mw-day"></div>').appendTo($root);
			const $dh = $('<div class="pp-mw-day-head"></div>').appendTo($day);
			if (entry.date === data.today) $dh.addClass("pp-mw-today");
			$("<span></span>").text(moment(entry.date, "YYYY-MM-DD").format("dddd, MMM D")).appendTo($dh);
			if (entry.off) $('<span class="pp-chip"></span>').text(__(entry.off)).appendTo($dh);
			$('<span class="pp-spacer"></span>').appendTo($dh);
			if (items.length) {
				$('<button type="button" class="btn btn-default btn-xs"></button>')
					.text(__("Route"))
					.attr("title", __("This day's stops in order, with drive times and a map"))
					.on("click", () => this.go_route(data.resource, entry.date))
					.appendTo($dh);
			}
			if (entry.travel) $('<div class="pp-mw-empty"></div>').text(entry.travel).appendTo($day);
			if (!items.length && !entry.travel) {
				$('<div class="pp-mw-empty"></div>').text(entry.off ? __("Off") : __("Nothing booked")).appendTo($day);
			}
			items.forEach((stop) => $day.append(this.my_week_item_html(stop)));
			this.p6d_my_week_day($day, $dh, entry, data);
		});
	},

	// One stop. Everything is escaped; the address links to Google Maps only when the server sent a
	// Google Maps URL, and opens without an opener.
	my_week_item_html(stop) {
		const when = stop.slot && stop.slot.length === 2 ? stop.slot.join("–") : stop.time || "";
		const place = stop.project_title && stop.project_title !== stop.label ? stop.project_title : "";
		const maps = stop.maps_url && /^https:\/\/www\.google\.com\/maps\//.test(stop.maps_url) ? stop.maps_url : "";
		const facts = [place, Number(stop.hours) > 0 ? `${pp_hours(stop.hours)}h` : ""].filter(Boolean).join(" · ");
		let address = "";
		if (stop.address && maps) {
			address = `<a class="pp-mw-addr" href="${pp_esc(maps)}" target="_blank" rel="noopener noreferrer">${pp_esc(
				stop.address
			)}</a>`;
		} else if (stop.address) {
			address = `<div class="pp-mw-addr">${pp_esc(stop.address)}</div>`;
		}
		const crew = (stop.crew || []).length ? `<div class="pp-mw-sub">${pp_esc(__("With {0}", [stop.crew.join(", ")]))}</div>` : "";
		const notes = stop.notes ? `<div class="pp-mw-notes">${pp_esc(stop.notes)}</div>` : "";
		return `
			<div class="pp-mw-item">
				${when ? `<div class="pp-mw-time">${pp_esc(when)}</div>` : ""}
				<div class="pp-mw-label">${pp_esc(stop.label || stop.ref)}</div>
				${facts ? `<div class="pp-mw-sub">${pp_esc(facts)}</div>` : ""}
				${address}${crew}${notes}
			</div>`;
	},

	// ------------------------------------------------------------------ crew sheet

	// The server renders the sheet; the browser prints it. The window opens on the click itself
	// (a window opened after the request returns is a pop-up the browser blocks) and fills in when
	// the sheet arrives.
	print_crew_sheet() {
		const win = window.open("", "_blank");
		if (!win) {
			frappe.msgprint(__("Allow pop-ups for this site to print the crew sheet."));
			return;
		}
		win.document.write(
			`<!doctype html><title>${pp_esc(__("Crew sheet"))}</title><p style="font-family:sans-serif">${pp_esc(
				__("Preparing the crew sheet…")
			)}</p>`
		);
		const start = this.view === "month" ? this.anchor : pp_ymd(this.range().start);
		Promise.resolve(frappe.call({ method: `${PP.api}.crew_sheet_html`, args: { start, group: this.group || "" } }))
			.then((r) => {
				const html = r && r.message;
				if (win.closed) return;
				if (!html) {
					win.close();
					return;
				}
				win.document.open();
				win.document.write(html);
				win.document.close();
				win.focus();
				const fonts = win.document.fonts && win.document.fonts.ready ? win.document.fonts.ready : Promise.resolve();
				// Let the sheet lay out and its display face arrive before the print dialog measures it.
				fonts.then(() => win.setTimeout(() => win.print(), 250));
			})
			.catch(() => {
				if (!win.closed) win.close();
			});
	},
};

Object.assign(ProjectPlanner.prototype, PP3B_METHODS);

// ====================================================================== Phase 4: tracking
//
// Planned against worked hours, the labor forecast of the project on screen, and the vehicles and
// assets a task uses. Everything here is read from the server's numbers; nothing is computed on
// this side that decides anything:
//   - a card that has clock-in hours says "14h of 12h" (red when the server marks it over_plan);
//     the dialog lists each person's hours (card.crew[].actual, refined by get_actuals); the
//     toolbar's "Running over" alert chip (PPTB) shows only the over_plan tasks
//   - with a project picked, a strip shows its labor forecast (get_labor_forecast): hours always;
//     cost only when the answer says can_see_cost, and the rate it is at ("base rate" until the
//     burdened rates are in) - a wage is never worked out here from hours
//   - card.equipment shows as chips, the dialog has an Equipment table (Fleet Vehicle / Asset) sent
//     to save_task as `equipment`, an equipment conflict arrives in needs_reason under the
//     "Equipment" key and is asked about like any other, and the crew view gains an Equipment row
//     group (get_equipment): each vehicle or asset, the tasks using it each day
//   - the Crew Utilization report's figures are the table under Capacity's heatmap (PPU, v1.687.0, merged into Capacity by P7)
//
// Everything Phase 4 adds is in this block, mixed into ProjectPlanner below. The hooks into the
// class above are one line each: init_phase4 (constructor), p4_set_mode (set_mode), load_phase4
// (load), render_phase4 (render), p4_equipment_rows (render_crew), p4_card_chips / p4_tip_lines /
// p4_card_conflicts (the cards), p4_conflict_heading (the reason dialogs), p4_equipment_of
// (snapshot) and p4_dialog_rows / p4_equipment_fields / p4_refine_actuals / p4_equipment_arg
// (the card dialog).

const PP4 = {
	report: "Crew Utilization",
	equipment_key: "Equipment",
	equipment_chips: 2,
	// A vehicle in either state cannot go out, whatever is booked on it.
	unavailable: ["In Shop", "Retired"],
};

const PP4_METHODS = {
	init_phase4() {
		this.over_only = false;
		this.forecast = null;
		this.forecast_for = "";
		this.forecast_request = 0;
		this.equip = null;
		this.equip_request = 0;

		// Running over is an alert chip in the toolbar (PPTB), shown while any task on screen is past
		// its plan. The filter is for this visit to the page: a chip that quietly hides most of the
		// board the next morning would be a trap, so it is not remembered.
		this.$forecast = $('<div class="pp-forecast"></div>').hide().insertBefore(this.$panel);
	},

	// The route, Capacity and My week views have none of this; the calendar views draw it.
	p4_set_mode(mode) {
		const calendar = mode === "";
		if (this.$forecast && !calendar) this.$forecast.hide();
	},

	// A reload refreshes the forecast in place (the strip stays up until the new figures arrive).
	load_phase4() {
		this.forecast_for = this.project;
		this.p4_fetch_forecast();
		this.p4_load_equipment();
	},

	render_phase4() {
		this.p4_sync_forecast();
		this.p4_render_forecast();
	},

	// ------------------------------------------------------------------ running over

	// How many tasks the filters show are past their plan, the Running over filter itself aside: the
	// toolbar's chip says it (PPTB's tb_alerts), and stays up while the filter is on, to turn it off.
	p4_over_count() {
		if (!this.data) return 0;
		const saved = this.over_only;
		this.over_only = false;
		const count = []
			.concat(this.data.tasks || [], this.data.unscheduled || [])
			.filter((card) => card.over_plan && this.task_visible(card)).length;
		this.over_only = saved;
		return count;
	},

	// The chip: show only the running-over tasks, or all of them again.
	p4_toggle_over() {
		this.over_only = !this.over_only;
		this.render();
	},

	// What a task was planned for: its estimate, else what the engine booked for its crew.
	p4_planned(card) {
		const estimate = Number(card.expected_time) || 0;
		if (estimate > 0) return estimate;
		return (card.crew || []).reduce((sum, member) => sum + (Number(member.booked) || 0), 0);
	},

	// "14h of 12h": clocked hours against the plan. The server decides whether that is too far over.
	p4_actual_text(card) {
		const actual = Number(card.actual_hours) || 0;
		if (!(actual > 0)) return "";
		const planned = this.p4_planned(card);
		return planned > 0
			? __("{0}h of {1}h", [pp_hours(actual), pp_hours(planned)])
			: __("{0}h worked", [pp_hours(actual)]);
	},

	p4_equipment_list(card) {
		return (Array.isArray(card.equipment) ? card.equipment : []).filter((item) => item && (item.name || item.label));
	},

	p4_equipment_label(item) {
		return item.label || item.name || "";
	},

	p4_card_chips(card) {
		const out = [];
		const text = this.p4_actual_text(card);
		if (text) {
			const tip = card.over_plan
				? __("Worked {0}h against {1}h planned: running over", [
						pp_hours(card.actual_hours),
						pp_hours(this.p4_planned(card)),
				  ])
				: __("Hours clocked so far against the plan");
			const tone = card.over_plan ? " pp-red" : "";
			out.push(`<span class="pp-chip${tone}" title="${pp_esc(tip)}">${pp_esc(text)}</span>`);
		}
		const equipment = this.p4_equipment_list(card);
		equipment.slice(0, PP4.equipment_chips).forEach((item) => {
			const tip = `${__(item.type || "Vehicle")}: ${this.p4_equipment_label(item)}`;
			out.push(`<span class="pp-chip pp-equip" title="${pp_esc(tip)}">${pp_esc(this.p4_equipment_label(item))}</span>`);
		});
		if (equipment.length > PP4.equipment_chips) {
			const rest = equipment.slice(PP4.equipment_chips).map((item) => this.p4_equipment_label(item));
			out.push(`<span class="pp-chip pp-equip" title="${pp_esc(rest.join(", "))}">+${pp_esc(rest.length)}</span>`);
		}
		return out;
	},

	p4_tip_lines(card) {
		const lines = [];
		const text = this.p4_actual_text(card);
		if (text) lines.push(`${__("Hours")}: ${text}${card.over_plan ? ` (${__("running over")})` : ""}`);
		const equipment = this.p4_equipment_list(card);
		if (equipment.length) {
			lines.push(__("Equipment: {0}", [equipment.map((item) => this.p4_equipment_label(item)).join(", ")]));
		}
		return lines;
	},

	// An equipment problem the server put on the card itself (a vehicle used twice, in the shop).
	p4_card_conflicts(card) {
		if (!Array.isArray(card.conflicts)) return [];
		return card.conflicts
			.map((entry) => (entry && typeof entry === "object" ? entry.text || entry.message || "" : entry))
			.filter((text) => typeof text === "string" && text);
	},

	// The reason dialogs list conflicts under a heading per person; equipment has its own.
	p4_conflict_heading(who) {
		return who === PP4.equipment_key ? __("Equipment") : who;
	},

	// ------------------------------------------------------------------ card dialog

	// The rows save_task takes, from a card: [{equipment_type, vehicle | asset}], or null when the
	// server did not send equipment (an older server), so nothing is ever sent for it.
	p4_equipment_of(card) {
		if (!Array.isArray(card.equipment)) return null;
		return this.p4_equipment_list(card).map((item) => this.p4_equipment_row(item.type, item.name));
	},

	p4_equipment_row(type, name) {
		return type === "Asset" ? { equipment_type: "Asset", asset: name } : { equipment_type: "Vehicle", vehicle: name };
	},

	p4_equipment_key(rows) {
		return (rows || [])
			.map((row) => `${row.equipment_type}:${row.asset || row.vehicle}`)
			.sort()
			.join("|");
	},

	// What the dialog's Equipment table holds, as save_task rows: one per vehicle or asset, blank
	// rows dropped. A row without a type is read from whichever link it filled in.
	p4_rows_from_dialog(rows) {
		const seen = new Set();
		const out = [];
		(rows || []).forEach((row) => {
			if (!row) return;
			const type = row.equipment_type || (row.asset && !row.vehicle ? "Asset" : "Vehicle");
			const name = type === "Asset" ? row.asset : row.vehicle;
			if (!name || seen.has(`${type}:${name}`)) return;
			seen.add(`${type}:${name}`);
			out.push(this.p4_equipment_row(type, name));
		});
		return out;
	},

	p4_equipment_arg(card, values, args) {
		const before = this.p4_equipment_of(card);
		if (!before || !Array.isArray(values.equipment)) return;
		const next = this.p4_rows_from_dialog(values.equipment);
		if (this.p4_equipment_key(next) !== this.p4_equipment_key(before)) args.equipment = JSON.stringify(next);
	},

	p4_equipment_fields(card) {
		const before = this.p4_equipment_of(card);
		if (!before) return [];
		return [
			{ fieldtype: "Section Break", label: __("Equipment") },
			{
				fieldtype: "Table",
				fieldname: "equipment",
				label: __("Vehicles and assets"),
				cannot_add_rows: false,
				in_place_edit: true,
				data: before.map((row) => Object.assign({ vehicle: "", asset: "" }, row)),
				fields: [
					{
						fieldtype: "Select",
						fieldname: "equipment_type",
						label: __("Type"),
						options: "Vehicle\nAsset",
						default: "Vehicle",
						in_list_view: 1,
						columns: 2,
					},
					{
						fieldtype: "Link",
						fieldname: "vehicle",
						label: __("Vehicle"),
						options: "Fleet Vehicle",
						depends_on: "eval:doc.equipment_type=='Vehicle'",
						in_list_view: 1,
						columns: 4,
						get_query: () => ({ filters: { status: "Active" } }),
					},
					{
						fieldtype: "Link",
						fieldname: "asset",
						label: __("Asset"),
						options: "Asset",
						depends_on: "eval:doc.equipment_type=='Asset'",
						in_list_view: 1,
						columns: 4,
						get_query: () => ({ filters: { docstatus: 1, status: ["not in", ["Scrapped", "Sold"]] } }),
					},
				],
			},
			{
				fieldtype: "HTML",
				fieldname: "equipment_note",
				options: `<p class="pp-equip-sub">${pp_esc(
					__("A vehicle or asset can be on one task at a time. A double booking asks for a reason, like a crew one.")
				)}</p>`,
			},
		];
	},

	// Each person's worked hours against their planned share, as lines. `people` are get_actuals'
	// by_person rows; without them the card's own crew figures are used.
	p4_people_html(card, people) {
		const rows = Array.isArray(people)
			? people.map((row) => ({
					label: row.label || row.resource,
					planned: Number(row.planned) || 0,
					actual: Number(row.actual) || 0,
			  }))
			: (card.crew || []).map((member) => ({
					label: this.resource_label(member.resource, member.label),
					planned: Number(member.booked) || 0,
					actual: Number(member.actual) || 0,
			  }));
		return rows
			.filter((row) => row.actual > 0 || row.planned > 0)
			.map((row) => {
				const text =
					row.planned > 0
						? __("{0}h of {1}h", [pp_hours(row.actual), pp_hours(row.planned)])
						: __("{0}h worked", [pp_hours(row.actual)]);
				return `<div class="pp-actual-row"><span>${pp_esc(row.label)}</span><span>${pp_esc(text)}</span></div>`;
			})
			.join("");
	},

	// Rows for the dialog's summary table: hours worked (with each person's) and equipment.
	p4_dialog_rows(card) {
		const out = [];
		const text = this.p4_actual_text(card);
		if (text) {
			const flag = card.over_plan ? ` <span class="pp-chip pp-red">${pp_esc(__("Running over"))}</span>` : "";
			out.push(
				`<tr><th>${pp_esc(__("Hours worked"))}</th><td>${pp_esc(text)}${flag}` +
					`<div class="pp-actuals-people">${this.p4_people_html(card, null)}</div></td></tr>`
			);
		}
		const equipment = this.p4_equipment_list(card);
		if (equipment.length) {
			out.push(
				`<tr><th>${pp_esc(__("Equipment"))}</th><td>${pp_esc(
					equipment.map((item) => this.p4_equipment_label(item)).join(", ")
				)}</td></tr>`
			);
		}
		return out;
	},

	// get_actuals knows each person's plan as well as their clock-ins; swap its lines in when they
	// arrive. The card's own figures are already showing, so a failure costs nothing.
	p4_refine_actuals(card, dialog) {
		if (!(Number(card.actual_hours) > 0)) return;
		Promise.resolve(frappe.call({ method: `${PP.api}.get_actuals`, args: { tasks: JSON.stringify([card.name]) } }))
			.then((r) => {
				const entry = this.p4_actuals_entry((r && r.message) || {}, card.name);
				if (!entry || !Array.isArray(entry.by_person)) return;
				dialog.$wrapper.find(".pp-actuals-people").html(this.p4_people_html(card, entry.by_person));
			})
			.catch(() => null);
	},

	// get_actuals answers per task; read it forgivingly (keyed by task, or under `tasks`, or a list).
	p4_actuals_entry(raw, name) {
		const table = raw.tasks || raw;
		if (Array.isArray(table)) return table.find((item) => item && (item.task === name || item.name === name)) || null;
		return (table && table[name]) || null;
	},

	// ------------------------------------------------------------------ labor forecast

	p4_money(value) {
		const number = Number(value);
		if (!Number.isFinite(number)) return "";
		return typeof format_currency === "function" ? format_currency(number) : number.toFixed(2);
	},

	// A different project in the toolbar: drop the old figures and fetch the new ones.
	p4_sync_forecast() {
		if (this.forecast_for === this.project) return;
		this.forecast_for = this.project;
		this.forecast = null;
		this.p4_fetch_forecast();
	},

	p4_fetch_forecast() {
		if (!this.project) {
			this.forecast = null;
			return;
		}
		const project = this.project;
		const token = ++this.forecast_request;
		Promise.resolve(frappe.call({ method: `${PP.api}.get_labor_forecast`, args: { project } }))
			.then((r) => {
				if (token !== this.forecast_request || project !== this.project) return;
				this.forecast = (r && r.message) || null;
				this.p4_render_forecast();
			})
			.catch(() => {
				if (token !== this.forecast_request) return;
				this.forecast = null;
				this.p4_render_forecast();
			});
	},

	p4_render_forecast() {
		const forecast = this.forecast;
		if (!this.$forecast) return;
		if (!forecast || !this.project || !PP.views.includes(this.view) || this.forecast_for !== this.project) {
			this.$forecast.hide().empty();
			return;
		}
		const project = this.by_project[this.project];
		const title = (project && (project.title || project.name)) || this.project;
		const parts = [`<span><b>${pp_esc(__("Labor forecast"))}</b> ${pp_esc(title)}</span>`];
		const hours = [];
		if (forecast.booked_hours != null) hours.push(__("{0}h booked ahead", [pp_hours(forecast.booked_hours)]));
		if (forecast.actual_hours != null) hours.push(__("{0}h worked so far", [pp_hours(forecast.actual_hours)]));
		if (hours.length) parts.push(`<span>${pp_esc(hours.join(" · "))}</span>`);
		let note = "";
		// Money only when the server says this person may see it, and only the figures it sent.
		if (forecast.can_see_cost === true) {
			if (forecast.forecast_cost != null) {
				parts.push(`<span><b>${pp_esc(this.p4_money(forecast.forecast_cost))}</b> ${pp_esc(__("forecast"))}</span>`);
			}
			const split = [];
			if (forecast.actual_cost != null) split.push(__("{0} worked", [this.p4_money(forecast.actual_cost)]));
			if (forecast.booked_cost != null) split.push(__("{0} booked ahead", [this.p4_money(forecast.booked_cost)]));
			if (split.length) parts.push(`<span>${pp_esc(split.join(" + "))}</span>`);
			const unrated = (forecast.by_person || []).filter((row) => row.rate == null).length;
			note = forecast.burdened ? __("At burdened rates.") : __("At base pay rates: payroll burden is not included yet.");
			if (unrated) note += ` ${__("{0} without a rate are counted in hours only.", [unrated])}`;
		}
		const $strip = this.$forecast.empty().show();
		$strip.html(parts.join(""));
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("By person"))
			.on("click", () => this.p4_forecast_dialog())
			.appendTo($strip);
		if (note) $('<div class="pp-forecast-note"></div>').text(note).appendTo($strip);
	},

	p4_forecast_dialog() {
		const forecast = this.forecast;
		if (!forecast) return;
		const money = forecast.can_see_cost === true;
		const rows = (forecast.by_person || [])
			.map((row) => {
				const cost = money
					? `<td>${pp_esc(row.rate == null ? __("no rate") : `${this.p4_money(row.rate)}/h`)}</td><td>${pp_esc(
							row.cost == null ? "" : this.p4_money(row.cost)
					  )}</td>`
					: "";
				return `<tr><td>${pp_esc(row.label)}</td><td>${pp_esc(pp_hours(row.booked))}h</td><td>${pp_esc(
					pp_hours(row.actual)
				)}h</td>${cost}</tr>`;
			})
			.join("");
		const head = `<tr><th>${pp_esc(__("Person"))}</th><th>${pp_esc(__("Booked ahead"))}</th><th>${pp_esc(__("Worked"))}</th>${
			money ? `<th>${pp_esc(__("Rate"))}</th><th>${pp_esc(__("Cost"))}</th>` : ""
		}</tr>`;
		const dialog = new frappe.ui.Dialog({
			title: __("Labor forecast by person"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "people",
					options: rows
						? `<table class="pp-fc-table">${head}${rows}</table>`
						: `<p>${pp_esc(__("Nobody is booked on this project yet."))}</p>`,
				},
			],
		});
		dialog.show();
	},

	// ------------------------------------------------------------------ equipment in the crew view

	p4_load_equipment() {
		if (this.view !== "crew") return;
		const { start, end } = this.range();
		const first = pp_ymd(start);
		const token = ++this.equip_request;
		Promise.resolve(frappe.call({ method: `${PP.api}.get_equipment`, args: { start: first, end: pp_ymd(end) } }))
			.then((r) => {
				if (token !== this.equip_request) return;
				this.equip = { start: first, items: this.p4_normalize_equipment((r && r.message) || {}) };
				if (this.view === "crew" && this.data) this.render();
			})
			.catch(() => {
				if (token === this.equip_request) this.equip = null;
			});
	},

	// get_equipment answers each vehicle or asset with, per day, the tasks using it. Read it
	// forgivingly: a list or an object keyed by name, days keyed by date, tasks as names or objects.
	p4_normalize_equipment(raw) {
		const list = Array.isArray(raw) ? raw : raw.equipment || raw.items || raw.rows || [];
		const entries = Array.isArray(list) ? list : Object.entries(list).map(([name, item]) => Object.assign({ name }, item));
		return entries
			.map((item) => {
				const name = item.name || item.vehicle || item.asset || "";
				const days = {};
				const source = item.days || {};
				const pairs = Array.isArray(source)
					? source.map((day) => [day && (day.date || day.day), day])
					: Object.entries(source);
				pairs.forEach(([ymd, value]) => {
					if (!ymd) return;
					const used = Array.isArray(value) ? value : (value && (value.in_use_by || value.tasks || value.used_by)) || [];
					days[ymd] = used
						.map((use) =>
							typeof use === "string"
								? { task: use, subject: "" }
								: { task: use.task || use.name || use.ref || "", subject: use.subject || use.label || "" }
						)
						.filter((use) => use.task);
				});
				return {
					type: item.type || item.equipment_type || "Vehicle",
					name,
					label: item.label || name,
					status: item.status || "",
					days,
				};
			})
			.filter((item) => item.name);
	},

	// The grid cells of the Equipment group: a header across the row, then each vehicle or asset
	// with the tasks using it on each day. Two tasks on one day, or any on a vehicle that is in
	// the shop, are outlined red. Empty when the data is not here for this week.
	p4_equipment_rows(days) {
		const equip = this.equip;
		if (!equip || equip.start !== days[0] || !equip.items.length) return "";
		const html = [`<div class="pp-crew-group">${pp_esc(__("Equipment"))}</div>`];
		equip.items.forEach((item) => {
			const down = PP4.unavailable.includes(item.status);
			const status = down ? ` · <span class="pp-chip pp-red">${pp_esc(__(item.status))}</span>` : "";
			html.push(
				`<div class="pp-equip-name"><b title="${pp_esc(item.label)}">${pp_esc(item.label)}</b>` +
					`<span class="pp-equip-sub">${pp_esc(__(item.type))}${status}</span></div>`
			);
			days.forEach((ymd) => {
				const used = item.days[ymd] || [];
				const clash = used.length > 1 || (down && used.length > 0);
				const cards = used
					.map((use) => {
						const known = this.by_task[use.task];
						const subject = use.subject || (known && known.subject) || use.task;
						const tip = [subject, item.label, clash ? __("Conflict") : ""].filter(Boolean).join("\n");
						return `<div class="pp-ecard${clash ? " pp-clash" : ""}" data-etask="${pp_esc(use.task)}" tabindex="0" title="${pp_esc(
							tip
						)}">${pp_esc(subject)}</div>`;
					})
					.join("");
				html.push(`<div class="pp-ecell${down ? " pp-unavailable" : ""}">${cards}</div>`);
			});
		});
		return html.join("");
	},
};

Object.assign(ProjectPlanner.prototype, PP4_METHODS);

// ====================================================================== Phase 5: extras
//
// Weather (P5.2): a task ticked "Outdoor work" carries `weather` from get_planner, the forecast
// days of its span that have rain, freezing or high wind ([] when clear, null when there is no
// forecast for it). The card shows that day's flags as a chip (a tray card, its first flagged day),
// the dialog lists them, the route view puts them on the stop, and suggest_dates ranks flagged days
// lower and shows why.
//
// Customer-facing visits (P5.4): the dialog's "Customer-facing visit" box marks a task whose
// customer is emailed the date once it is firm, when Project Planner Settings has customer date
// confirmations switched on (they ship off). "Preview customer email" renders exactly what would go
// out, and to whom, and sends nothing; it opens in a sandboxed frame so the email's own styles and
// links stay inside it.
//
// The two boxes are saved by set_task_flags, straight to the task and never drafted: they are not
// bookings, and the server leaves the task's `modified` alone so the save_task that may follow in
// the same dialog is not refused as out of date.
//
// Everything Phase 5 adds to the page is in this block, mixed into ProjectPlanner below, so the
// Phase 4 work on the same file merges cleanly. The hooks into the class above are one line each:
// init_phase5 (constructor), weather_chips and weather_lines (card_html), weather_lines,
// phase5_links, phase5_dialog_fields and phase5_action (open_card), save_phase5_flags
// (save_dialog), suggestion_weather_html (show_suggestions) and stop_weather_html (render_route).

const PP5 = {
	// Who may preview a customer email (the endpoint checks the same roles).
	preview_roles: ["System Manager", "Projects Manager"],
};

const PP5_METHODS = {
	// The phase's setup hook. It only wrote the phase's stylesheet, which is project_planner.css now
	// (S3.4d), so there is nothing left to set up; the hook stays so the class calls it as before.
	init_phase5() {},

	// ------------------------------------------------------------------ weather

	// One forecast day of a card: the day it is drawn on, or (in a tray) its first flagged day.
	weather_day(card, ymd) {
		const days = Array.isArray(card.weather) ? card.weather : [];
		if (!days.length) return null;
		if (ymd) return days.find((entry) => entry.date === ymd) || null;
		return days[0];
	},

	weather_chips(card, ymd) {
		const entry = this.weather_day(card, ymd);
		const flags = (entry && entry.flags) || [];
		if (!flags.length) return [];
		const text = ymd ? flags.join(" · ") : `${flags[0]} · ${pp_when(entry.date)}`;
		return [`<span class="pp-chip pp-weather" title="${pp_esc(flags.join(", "))}">${pp_esc(text)}</span>`];
	},

	// "Thu, Oct 15: Rain 70%, Wind 28 mph" for every flagged day of the task.
	weather_lines(card) {
		return (Array.isArray(card.weather) ? card.weather : [])
			.filter((entry) => (entry.flags || []).length)
			.map((entry) => `${pp_when(entry.date)}: ${entry.flags.join(", ")}`);
	},

	suggestion_weather_html(item) {
		const flags = (item && item.weather) || [];
		return flags.length ? `<span class="pp-chip pp-weather">${pp_esc(flags.join(" · "))}</span>` : "";
	},

	stop_weather_html(stop) {
		const flags = (stop && stop.weather) || [];
		return flags.length
			? `<div class="pp-stop-leg"><span class="pp-chip pp-weather">${pp_esc(flags.join(" · "))}</span></div>`
			: "";
	},

	// ------------------------------------------------------------------ the card dialog

	phase5_dialog_fields(card) {
		// The two boxes are saved on the Task by set_task_flags; a drafted new task has no Task yet.
		if (card.new_task) return [];
		return [
			{
				fieldtype: "Check",
				fieldname: "outdoor",
				label: __("Outdoor work"),
				default: card.outdoor ? 1 : 0,
				description: __("Flag days whose forecast has rain, freezing or high wind, and avoid them in suggestions."),
			},
			{
				fieldtype: "Check",
				fieldname: "customer_visit",
				label: __("Customer-facing visit"),
				default: card.customer_visit ? 1 : 0,
				description: __(
					"The customer is emailed the date once it is firm, when customer date confirmations are switched on."
				),
			},
		];
	},

	can_preview_customer() {
		return PP5.preview_roles.some((role) => frappe.user.has_role(role));
	},

	phase5_links(card) {
		return card.customer_visit && this.can_preview_customer()
			? [["customer_preview", __("Preview customer email")]]
			: [];
	},

	phase5_action(action, card) {
		if (action === "customer_preview") this.preview_customer_email("Task", card.name);
	},

	// Outdoor / Customer-facing go to the task on their own, before any save_task from the same
	// dialog. Resolves true when something was saved.
	save_phase5_flags(card, values) {
		values = values || {};
		const args = { task: card.name };
		const outdoor = values.outdoor ? 1 : 0;
		const customer_visit = values.customer_visit ? 1 : 0;
		if (outdoor !== (card.outdoor ? 1 : 0)) args.outdoor = outdoor;
		if (customer_visit !== (card.customer_visit ? 1 : 0)) args.customer_visit = customer_visit;
		if (Object.keys(args).length === 1) return Promise.resolve(false);
		return Promise.resolve(frappe.call({ method: `${PP.api}.set_task_flags`, args }))
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.queued) {
					frappe.show_alert({ message: __("The customer will be emailed the date."), indicator: "blue" }, 6);
				}
				return true;
			})
			.catch(() => false);
	},

	// ------------------------------------------------------------------ customer email preview

	preview_customer_email(doctype, name) {
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.preview_customer_confirmation`,
				args: { doctype, name },
				freeze: true,
				freeze_message: __("Rendering the customer email…"),
			})
		)
			.then((r) => this.show_customer_preview((r && r.message) || {}))
			.catch(() => null);
	},

	// What would go out, to whom, and why it would not go out now. Nothing here sends.
	show_customer_preview(answer) {
		const status = answer.would_send ? __("This email would be sent.") : __("This email would not be sent now.");
		const to = answer.recipient ? __("To: {0}", [answer.recipient]) : __("To: nobody (no email address found)");
		const notes = (answer.notes || []).map((text) => `<li>${pp_esc(text)}</li>`).join("");
		const parts = [
			`<div class="pp-p5-meta"><b>${pp_esc(status)}</b></div>`,
			`<div class="pp-p5-meta">${pp_esc(to)}</div>`,
		];
		if (answer.subject) parts.push(`<div class="pp-p5-meta">${pp_esc(__("Subject: {0}", [answer.subject]))}</div>`);
		if (notes) parts.push(`<ul class="pp-p5-notes">${notes}</ul>`);
		if (answer.error) parts.push(`<div class="pp-route-note">${pp_esc(answer.error)}</div>`);
		parts.push(`<iframe class="pp-p5-frame" sandbox="" title="${pp_esc(__("Email preview"))}"></iframe>`);
		const dialog = new frappe.ui.Dialog({
			title: __("Customer email preview"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "preview", options: parts.join("") }],
		});
		dialog.show();
		const $frame = dialog.$wrapper.find("iframe.pp-p5-frame");
		if (answer.html) $frame.attr("srcdoc", answer.html);
		else $frame.hide();
	},
};

Object.assign(ProjectPlanner.prototype, PP5_METHODS);

// ====================================================================== Phase 6A: quick looks and polish
//
// Nik, 2026-10-09: "click the Technicians name or something and see what their specific schedule is
// in a pop up or something so as not to lose context overall." Everything here opens in the planner
// kit's side drawer (public/js/planner_kit, loaded with frappe.require below), which leaves the
// calendar visible and usable behind it (cards still move by drag); Esc and Back close it without
// leaving the planner.
//
//   - click a person's name (Resources available, a crew-view row, a crew badge on a card, the route
//     view's title)                          their week: every booking, free hours, days off,
//                                            conflicts, the chosen day's stops in driving order,
//                                            call/text/email (planner_views.get_person_schedule)
//   - click a date (week and month day numbers, Resources available's day heads, crew-view column
//     heads)                                 everyone's day side by side (get_day_overview)
//   - click a project's name on a card, "At a glance" beside the project filter, or "Project at a
//     glance" in a task's panel             the project: its open tasks on a timeline, planned vs
//                                            worked, the labor forecast (get_project_overview)
//   - click a card                          its editor opens in the side panel (planner_kit.panel
//                                            hosts the same FieldGroup the dialog had; save_dialog,
//                                            the reason prompt and every field are unchanged)
//   - drag a task out of a person, day or project drawer onto a day or a person's row: the same
//     drag_source / plan_drop / drop as a card on the board, so it asks for a reason and can be undone
//   - every change that can be undone shows a toast with Undo; hovering a name lights up that
//     person's bookings (pk-glow), a project's name its cards; the crew view's and the week view's
//     headers and the people column stay put while scrolling; "?" opens the legend
//
// Everything Phase 6A adds to the page is in this block, mixed into ProjectPlanner below, so the work
// of other phases on this file merges cleanly. The hooks into the class above are one line each:
// init_phase6a (constructor), render_phase6a (render), p6a_route (go, go_route and go_my_week),
// p6a_drag_source (drag_source), p6a_edge_scroll (edge_scroll), p6a_undo_toast (push_undo, whose
// result send() reads so the green alert is not shown twice), p6a_close_toast (undo), and p6a_dialog,
// p6a_links and p6a_action (open_card).

const PP6A = {
	// The kit and its stylesheet, both content-hashed bundles (S3.4e). The stylesheet's <link> lands
	// after this page's CSS, so the kit's rules come after the page's, as they always have.
	kit: ["planner_kit.bundle.js", "planner_kit.bundle.css"],
	views: "erpnext_enhancements.api.planner_views",
	owner: "pp",
	// Drawer widths on a wide screen; a phone gets the whole screen.
	widths: { person: 460, day: 760, project: 620, panel: 620 },
	// The first-time tips. They used to be banners under the toolbar on load; since the toolbar redesign
	// (v1.687.0) they are the first section of the "?" help, and take no row of their own.
	hints: [
		["pp-person-peek", "Click a person's name to see their week without leaving the calendar."],
		["pp-day-peek", "Click a date to see everyone's day side by side."],
		["pp-glow", "Hover over a name to light up all of that person's bookings."],
	],
	route_icon:
		'<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" width="14" height="14">' +
		'<circle cx="3.5" cy="12.5" r="1.8"/><circle cx="12.5" cy="3.5" r="1.8"/>' +
		'<path d="M5.3 12.5H10a2.5 2.5 0 0 0 0-5H6a2.5 2.5 0 0 1 0-4h4.7"/></svg>',
};

const PP6A_METHODS = {
	init_phase6a() {
		this.p6a = { kit: null, peek: null, panel: null, panel_card: null, seen: null, glow: [] };
		this.$p6a_glance = $('<button type="button" class="btn btn-default btn-sm pp-p6a-glance"></button>')
			.text(__("At a glance"))
			.attr("title", __("The project picked here, at a glance: its tasks, hours and forecast"))
			.hide()
			.on("click", () => {
				if (this.project) this.p6a_open_project(this.project);
			})
			.insertAfter(this.$project);
		this.$p6a_help = $('<button type="button" class="btn btn-default btn-sm pp-p6a-help">?</button>')
			.attr("title", __("How to read the planner"))
			.attr("aria-label", __("How to read the planner"))
			.on("click", () => this.p6a_legend())
			.insertAfter(this.$undo);
		const root = this.$body[0];
		// Capture phase: a name, a date or a project inside a card is a quick look, not the card's
		// own click (which opens the task) and not the day number's old "show this week".
		root.addEventListener("click", (e) => this.p6a_click(e), true);
		root.addEventListener(
			"keydown",
			(e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				const el = e.target && e.target.closest ? e.target.closest(".pp-p6a-name, [data-pp6a-day]") : null;
				if (!el || e.target !== el) return;
				e.preventDefault();
				e.stopPropagation();
				el.click();
			},
			true
		);
		this.p6a_require(() => this.p6a_ready());
	},

	// The kit and its stylesheet, asked for once (S5c). v16.50's frappe.require counts a file as loaded
	// only when it has finished loading (assets.js pushes to `_executed` after onload), so a second call
	// in the same tick appends a second <script> and a second <link>. 6A and 6D each asked from the
	// constructor, and the kit's stylesheet was on the page twice. One request per Desk, shared by both
	// planners (MP6A.kit is the same two files), so opening the other planner while it is still on its
	// way does not ask again either. frappe.require settles on a failed file too, and so does this.
	p6a_require(callback) {
		if (!window.planner_kit_request) window.planner_kit_request = Promise.resolve(frappe.require(PP6A.kit));
		return window.planner_kit_request.then(callback);
	},

	p6a_ready() {
		const kit = window.planner_kit;
		if (!kit || (this.p6a && this.p6a.kit)) return;
		this.p6a.kit = kit;
		// A booking dragged out of a drawer this page opened starts the page's own drag.
		kit.drawer.element().addEventListener("pointerdown", (e) => {
			const current = kit.drawer.current();
			if (current && current.owner === PP6A.owner) this.on_down(e);
		});
		this.p6a.glow = [kit.hover_glow(this.$body[0], "person"), kit.hover_glow(this.$body[0], "project")];
		this.p6c_ready();
		this.tb_ready();
	},

	// ------------------------------------------------------------------ hooks

	// A route change of the planner's own: with `keep` (another week or view) an open drawer stays
	// open over it; otherwise it closes. Either way Back does not land on a drawer that has gone.
	p6a_route(fn, keep) {
		const kit = this.p6a && this.p6a.kit;
		return kit ? kit.drawer.route(fn, keep) : fn();
	},

	render_phase6a() {
		if (!this.p6a) return;
		this.p6a_decorate();
		if (this.$p6a_glance) this.$p6a_glance.toggle(!!this.project && PP.views.includes(this.view));
		this.p6a_refresh_open();
	},

	// The undo toast. True when it was shown, so send() leaves out the green alert it replaces.
	p6a_undo_toast(snapshot, message) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !message) return false;
		kit.toast(message, {
			action_label: __("Undo"),
			tone: "success",
			on_action: () => {
				// Only the change this toast announced: the Undo button may have been used since.
				if (this.undo_stack[this.undo_stack.length - 1] !== snapshot) {
					frappe.show_alert({ message: __("That change has already been undone."), indicator: "blue" }, 5);
					return;
				}
				this.undo();
			},
		});
		return true;
	},

	p6a_close_toast() {
		const kit = this.p6a && this.p6a.kit;
		if (kit) kit.toast.close();
	},

	// The card editor in the side panel, or the old dialog until the kit has loaded.
	p6a_dialog(opts, card) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return new frappe.ui.Dialog(opts);
		const panel = kit.panel(
			Object.assign({}, opts, {
				subtitle: [card.project_title || card.project, card.name].filter(Boolean).join(" · "),
				width: PP6A.widths.panel,
				key: `task:${card.name}`,
				owner: PP6A.owner,
				push: this.$body[0],
				reopen: () => {
					const fresh = this.by_task[card.name];
					if (fresh) this.open_card(fresh);
				},
			})
		);
		this.p6a.panel = panel;
		this.p6a.panel_card = card;
		this.p6a.peek = null;
		panel.onhide = () => {
			if (this.p6a.panel !== panel) return;
			this.p6a.panel = null;
			this.p6a.panel_card = null;
		};
		return panel;
	},

	p6a_links(card) {
		return this.p6a && this.p6a.kit && card.project ? [["p6a_project", __("Project at a glance")]] : [];
	},

	p6a_action(action, card) {
		if (action !== "p6a_project") return false;
		if (card.project) this.p6a_open_project(card.project);
		return true;
	},

	// A drag that starts in a drawer: a task the page owns and may move, whether or not it is on the
	// board (a later week in a person's drawer; the server sent its full card for exactly this).
	p6a_drag_source(el) {
		const kit = this.p6a && this.p6a.kit;
		const current = kit && kit.drawer.current();
		if (!current || current.owner !== PP6A.owner) return null;
		const item = el.closest("[data-pk-drag]");
		if (!item || el.closest("button, a, input, select, textarea")) return null;
		if (item.getAttribute("data-pk-kind") !== "task") return null;
		const card = this.p6a_card(item.getAttribute("data-pk-ref"));
		if (!card || !card.movable || card.saving) return null;
		return {
			kind: "card",
			el: item,
			card,
			from_date: item.getAttribute("data-pk-date") || card.start || null,
			from_resource: item.getAttribute("data-pk-resource") || null,
		};
	},

	// The crew view, the week grid and the people panel scroll inside themselves on a wide screen
	// (so their headers can stay put): near their top or bottom edge, a drag scrolls them too.
	p6a_edge_scroll(x, y) {
		const el = document.elementFromPoint(x, y);
		const box = el && el.closest ? el.closest(".pp-crew-view .pp-scroll, .pp-week .pp-grid, .pp-res .pp-scroll") : null;
		if (!box || box.scrollHeight <= box.clientHeight) return;
		const rect = box.getBoundingClientRect();
		if (y < rect.top + 40) box.scrollTop -= 14;
		else if (y > rect.bottom - 40) box.scrollTop += 14;
	},

	// ------------------------------------------------------------------ markup the quick looks need

	// Drawn after every render, as Phase 3B's draft outline is: names and badges get the person's
	// key, cards the keys of their people and project (hover glow), and the date heads a day key.
	p6a_decorate() {
		if (!this.data) return;
		const root = this.$body[0];
		const key = (value) => encodeURIComponent(String(value == null ? "" : value));
		root.querySelectorAll(".pp-person[data-resource]").forEach((el) => {
			el.setAttribute("data-pk-person", key(el.getAttribute("data-resource")));
			el.classList.add("pp-p6a-name");
			el.setAttribute("role", "button");
			el.setAttribute("tabindex", "0");
			if (!el.getAttribute("data-pp6a-tip")) {
				el.setAttribute("data-pp6a-tip", "1");
				el.setAttribute("title", `${el.getAttribute("title") || ""}\n${__("Click to see their week.")}`.trim());
			}
		});
		root.querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			if (!card) return;
			const row = el.getAttribute("data-resource");
			const crew = (card.crew || []).map((member) => member.resource).filter(Boolean);
			el.setAttribute("data-pk-persons", (row ? [row] : crew).map(key).join(" "));
			if (card.project) el.setAttribute("data-pk-projects", key(card.project));
			el.querySelectorAll(".pp-badges .pp-badge:not(.pp-more)").forEach((badge, index) => {
				const member = (card.crew || [])[index];
				if (member && member.resource) badge.setAttribute("data-pk-person", key(member.resource));
			});
			// The project's name in the sub line becomes its own target (the crew view's chips have
			// no project in theirs).
			if (!row && card.project) {
				const sub = el.querySelector(".pp-card-sub");
				if (sub && !sub.querySelector("[data-pk-project]")) {
					const rest = this.duration_text(card);
					sub.innerHTML =
						`<span class="pp-p6a-proj" data-pk-project="${pp_esc(key(card.project))}" title="${pp_esc(
							__("See this project at a glance")
						)}">${pp_esc(card.project_title || card.project)}</span>` + (rest ? ` · ${pp_esc(rest)}` : "");
				}
			}
		});
		root.querySelectorAll(".pp-fcard").forEach((el) => {
			const item = this.by_foreign[el.getAttribute("data-fkey")];
			const cell = el.closest(".pp-cell[data-resource]");
			const who = (item && item.resource) || (cell && cell.getAttribute("data-resource"));
			if (who) el.setAttribute("data-pk-persons", key(who));
		});
		root.querySelectorAll("[data-week]").forEach((el) => {
			el.setAttribute("data-pp6a-day", el.getAttribute("data-week"));
			el.setAttribute("title", __("See everyone's day"));
			el.setAttribute("aria-label", __("See everyone's day on {0}", [this.day_words(el.getAttribute("data-week"))]));
			el.setAttribute("tabindex", "0");
		});
		const heads = root.querySelectorAll(".pp-res-grid > .pp-res-head");
		const days = this.range_days();
		heads.forEach((el, index) => {
			const ymd = days[index - 1];
			if (!ymd) return;
			el.setAttribute("data-pp6a-day", ymd);
			el.setAttribute("title", __("See everyone's day"));
		});
	},

	// ------------------------------------------------------------------ clicks

	p6a_click(e) {
		if (!this.p6a || !this.p6a.kit || Date.now() < this.click_blocked_until) return;
		const target = e.target;
		if (!target || !target.closest || target.closest("button, a, input, select, textarea, .pp-route")) return;
		const take = () => {
			e.stopPropagation();
			e.preventDefault();
		};
		const person = target.closest("[data-pk-person]");
		if (person) {
			take();
			this.p6a_open_person(decodeURIComponent(person.getAttribute("data-pk-person")));
			return;
		}
		const project = target.closest("[data-pk-project]");
		if (project) {
			take();
			this.p6a_open_project(decodeURIComponent(project.getAttribute("data-pk-project")));
			return;
		}
		const day = target.closest("[data-pp6a-day]");
		if (day) {
			take();
			this.p6a_open_day(day.getAttribute("data-pp6a-day"));
			return;
		}
		if (target.closest(".pp-route-title") && this.route_resource) {
			take();
			this.p6a_open_person(this.route_resource, this.route_date);
		}
	},

	// ------------------------------------------------------------------ the quick looks

	// The first day of the week the person drawer opens on: the week on screen (the site's first
	// weekday), the route view's day's week, or this week when the month on screen holds today.
	p6a_week_start() {
		const today = this.today();
		const { start, end } = this.range();
		let base = this.anchor;
		if (this.view === PP.route_view && this.route_date) base = this.route_date;
		else if (this.view === "month" && today >= pp_ymd(start) && today <= pp_ymd(end)) base = today;
		return this.week_start_of(base || today);
	},

	p6a_card(name) {
		if (!name) return null;
		if (this.by_task[name]) return this.by_task[name];
		const peek = this.p6a && this.p6a.peek;
		const data = peek && typeof peek.data === "function" ? peek.data() : null;
		if (!data) return null;
		if (data.cards && data.cards[name]) return data.cards[name];
		return [].concat(data.tasks || [], data.undated || []).find((card) => card && card.name === name) || null;
	},

	// The page owns project tasks: only those it may move can leave a drawer by drag.
	p6a_can_drag(booking, data) {
		if (!this.data || !this.data.can_edit || booking.kind !== "task") return false;
		const card = this.by_task[booking.ref] || (data && data.cards && data.cards[booking.ref]);
		return !!(card && card.movable);
	},

	p6a_open_person(resource, selected) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !resource) return;
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.person({
			resource,
			label: this.resource_label(resource),
			start: this.p6a_week_start(),
			selected: selected || null,
			days: 7,
			owner: PP6A.owner,
			push: this.$body[0],
			width: PP6A.widths.person,
			can_drag: (booking, day, person, data) => this.p6a_can_drag(booking, data),
			on_full_route: (who, ymd) => kit.drawer.navigate(() => this.go_route(who, ymd)),
			actions: this.p6d_person_actions(resource),
		});
	},

	p6a_open_day(ymd) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !ymd) return;
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.day({
			date: ymd,
			group: this.group || "",
			owner: PP6A.owner,
			push: this.$body[0],
			width: PP6A.widths.day,
			person_key: (person) => person.resource,
			can_drag: (booking, day, person, data) => this.p6a_can_drag(booking, data),
			on_person: (person) => this.p6a_open_person(person.resource, ymd),
			on_week: (date) => kit.drawer.navigate(() => this.go("week", date)),
			...this.p6d_day_peek_opts(),
		});
	},

	p6a_open_project(project) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !project) return;
		const known = this.by_project[project];
		const state = { data: null, token: 0 };
		this.p6a.seen = this.data;
		const actions = [
			{
				label: __("Open project"),
				on_click: () => kit.drawer.navigate(() => frappe.set_route("Form", "Project", project)),
			},
		];
		if (known && PP.views.includes(this.view)) {
			actions.unshift({ label: __("Show only this project"), on_click: () => this.p6a_filter_project(project) });
		}
		actions.push(...this.t1_project_actions(project));
		actions.push(...this.as4_project_actions(project));
		const handle = kit.drawer.open({
			title: (known && known.title) || project,
			subtitle: project,
			body: `<p class="pk-empty">${pp_esc(__("Loading…"))}</p>`,
			width: PP6A.widths.project,
			key: `project:${project}`,
			owner: PP6A.owner,
			push: this.$body[0],
			actions,
			reopen: () => this.p6a_open_project(project),
			on_click: (e) => {
				const row = e.target && e.target.closest ? e.target.closest("[data-pp6a-task]") : null;
				if (!row || Date.now() < this.click_blocked_until) return;
				const card = this.p6a_card(row.getAttribute("data-pp6a-task"));
				if (card) this.open_card(card);
			},
		});
		const load = () => {
			const token = ++state.token;
			if (state.data) handle.body.classList.add("pk-busy");
			return Promise.resolve(frappe.call({ method: `${PP6A.views}.get_project_overview`, args: { project } }))
				.then((r) => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					state.data = (r && r.message) || null;
					const data = state.data || {};
					handle.set_title(data.title || (known && known.title) || project);
					handle.set_subtitle([project, data.customer_name].filter(Boolean).join(" · "));
					handle.set_body(this.p6a_project_html(state.data));
				})
				.catch(() => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					handle.set_body(this.p6a_project_html(null));
				});
		};
		this.p6a.peek = { data: () => state.data, refresh: load, is_open: () => handle.is_open() };
		load();
		this.p6c_project_drawer(project, handle);
	},

	p6a_filter_project(project) {
		if (this.$project) this.$project.val(project);
		this.pick_filter("project", project);
	},

	// After every load, a drawer that shows what changed is refreshed: the task in the side panel is
	// reopened from its new card (so its `modified` lock and its fields are current), and a person,
	// day or project drawer asks for its numbers again.
	p6a_refresh_open() {
		const p6a = this.p6a;
		if (!p6a.kit || !this.data) return;
		if (p6a.panel && p6a.panel.is_open() && p6a.panel_card) {
			const fresh = this.by_task[p6a.panel_card.name];
			if (fresh && !fresh.saving && this.p6a_signature(fresh) !== this.p6a_signature(p6a.panel_card)) {
				this.open_card(fresh);
				return;
			}
		}
		if (p6a.peek && p6a.peek.is_open() && p6a.seen !== this.data) {
			p6a.seen = this.data;
			p6a.peek.refresh();
		}
	},

	p6a_signature(card) {
		return JSON.stringify([
			card.modified,
			card.start,
			card.end,
			card.expected_time,
			card.tentative,
			card.drafted,
			(card.crew || []).map((member) => [member.resource, member.hours, member.is_lead]),
		]);
	},

	// ------------------------------------------------------------------ the project drawer

	p6a_initials(resource, label) {
		const parts = String(this.resource_label(resource, label) || "?").split(/\s+/).filter(Boolean);
		return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
	},

	p6a_hours_line(card) {
		const actual = Number(card.actual_hours) || 0;
		const planned = Number(card.planned_hours) || 0;
		if (actual > 0) {
			return planned > 0
				? __("{0}h of {1}h", [pp_hours(actual), pp_hours(planned)])
				: __("{0}h worked", [pp_hours(actual)]);
		}
		return planned > 0 ? __("{0}h planned", [pp_hours(planned)]) : "";
	},

	p6a_task_row(card, data, track) {
		const grab = !!(card.movable && data.can_edit && this.data && this.data.can_edit);
		const crew = (card.crew || [])
			.slice(0, 3)
			.map(
				(member) =>
					`<span class="pp-badge" style="--pp-color: ${pp_esc(this.resource_color(member.resource))}" title="${pp_esc(
						this.resource_label(member.resource, member.label)
					)}">${pp_esc(this.p6a_initials(member.resource, member.label))}</span>`
			)
			.join("");
		const when = card.start
			? card.end && card.end !== card.start
				? `${pp_when(card.start)} – ${pp_when(card.end)}`
				: pp_when(card.start)
			: __("No dates");
		const sub = [when, this.p6a_hours_line(card)].filter(Boolean).join(" · ");
		const chips = [];
		if (card.over_plan) chips.push(`<span class="pp-chip pp-red">${pp_esc(__("Running over"))}</span>`);
		if (card.tentative) chips.push(`<span class="pp-chip pp-pencil">${pp_esc(__("Pencil"))}</span>`);
		if (card.overdue) chips.push(`<span class="pp-chip pp-red">${pp_esc(__("Overdue"))}</span>`);
		if (card.rental_kind) chips.push(`<span class="pp-chip">${pp_esc(__(card.rental_kind))}</span>`);
		const drag = grab
			? ` data-pk-drag="1" data-pk-kind="task" data-pk-ref="${pp_esc(card.name)}" data-pk-key="${pp_esc(
					card.name
			  )}" data-pk-date="${pp_esc(card.start || "")}" data-pk-resource=""`
			: "";
		const tip = [card.subject, sub, grab ? __("Drag onto the calendar to move it; click to edit it.") : ""]
			.filter(Boolean)
			.join("\n");
		return `
			<div class="pk-tl-row pp-p6a-row${grab ? " pk-grab" : ""}" data-pp6a-task="${pp_esc(card.name)}"${drag}
				title="${pp_esc(tip)}">
				<div class="pk-tl-label"><b>${pp_esc(card.subject || card.name)}<span class="pp-p6a-initials">${crew}</span></b>
					<span>${pp_esc(sub)}</span><div>${chips.join("")}</div></div>
				${track}
			</div>`;
	},

	p6a_track(card, data) {
		const bar = card.bar || {};
		if (bar.outside) {
			return `<div class="pk-tl-off">${pp_esc(bar.outside === "before" ? __("← earlier") : __("later →"))}</div>`;
		}
		const classes = ["pk-tl-bar"];
		if (card.tentative) classes.push("pk-tl-pencil");
		else if (card.over_plan) classes.push("pk-tl-over");
		if (card.overdue) classes.push("pk-tl-late");
		if (bar.clipped_start) classes.push("pk-tl-clip-start");
		if (bar.clipped_end) classes.push("pk-tl-clip-end");
		const left = Math.max(0, Math.min(100, Number(bar.left_pct) || 0));
		const width = Math.max(0, Math.min(100 - left, Number(bar.width_pct) || 0));
		// The task's own color is data: it rides in as --pp-color, which .pp-tl-colored paints
		// (project_planner.css). A pencilled or running-over bar keeps the kit's own fill.
		let color = "";
		if (!card.tentative && !card.over_plan) {
			classes.push("pp-tl-colored");
			color = `--pp-color: ${pp_esc(pp_color(card.color, PP.default_color))};`;
		}
		return `<div class="pk-tl-track">${this.p6a_today_mark(data)}<span class="${classes.join(" ")}" style="left:${left}%;width:${width}%;${color}"></span></div>`;
	},

	p6a_today_mark(data) {
		const window_ = data.window;
		if (!window_ || !data.today || data.today < window_.start || data.today > window_.end) return "";
		const total = moment(window_.end, "YYYY-MM-DD").diff(moment(window_.start, "YYYY-MM-DD"), "days") + 1;
		const at = (moment(data.today, "YYYY-MM-DD").diff(moment(window_.start, "YYYY-MM-DD"), "days") + 0.5) / total;
		return `<span class="pk-tl-today" style="left:${Math.round(at * 10000) / 100}%" title="${pp_esc(__("Today"))}"></span>`;
	},

	p6a_forecast_html(forecast) {
		if (!forecast) return "";
		const parts = [];
		if (forecast.booked_hours != null) parts.push(__("{0}h booked ahead", [pp_hours(forecast.booked_hours)]));
		if (forecast.actual_hours != null) parts.push(__("{0}h worked so far", [pp_hours(forecast.actual_hours)]));
		let note = "";
		// Money only when the server says this person may see it, and only the figures it sent.
		if (forecast.can_see_cost === true) {
			if (forecast.forecast_cost != null) parts.push(__("{0} forecast", [this.p4_money(forecast.forecast_cost)]));
			note = forecast.burdened ? __("At burdened rates.") : __("At base pay rates: payroll burden is not included yet.");
		}
		if (!parts.length) return "";
		return (
			`<div class="pk-section-title">${pp_esc(__("Labor forecast"))}</div>` +
			`<div class="pp-p6a-forecast">${parts.map((text) => `<span>${pp_esc(text)}</span>`).join("")}${
				note ? `<span class="pp-p6a-forecast-note">${pp_esc(note)}</span>` : ""
			}</div>`
		);
	},

	p6a_project_html(data) {
		if (!data) return `<p class="pk-empty">${pp_esc(__("The project could not be loaded. Try again."))}</p>`;
		if (!data.on_planner) {
			return `<div class="pk-note pk-note-warn">${pp_esc(data.message || __("This project is not on the planner."))}</div>`;
		}
		const facts = [];
		const fact = (label, value) => {
			if (value) facts.push(`<tr><th>${pp_esc(label)}</th><td>${pp_esc(value)}</td></tr>`);
		};
		fact(__("Project"), data.project);
		fact(__("Account"), data.customer_name);
		fact(__("Project manager"), data.pm);
		fact(__("Status"), data.status ? __(data.status) : "");
		fact(__("Type"), data.project_type ? __(data.project_type) : "");
		if (data.expected_start || data.expected_end) {
			fact(__("Expected"), [data.expected_start, data.expected_end].filter(Boolean).map((ymd) => pp_when(ymd)).join(" – "));
		}
		const tasks = data.tasks || [];
		const undated = data.undated || [];
		const parts = [`<table class="pk-facts">${facts.join("")}</table>`, this.p6a_forecast_html(data.forecast)];
		parts.push(`<div class="pk-section-title">${pp_esc(__("Scheduled tasks ({0})", [tasks.length]))}</div>`);
		if (tasks.length && data.window) {
			parts.push(
				`<div class="pk-tl"><div class="pk-tl-scale"><span>${pp_esc(pp_when(data.window.start))}</span><span>${pp_esc(
					pp_when(data.window.end)
				)}</span></div>${tasks.map((card) => this.p6a_task_row(card, data, this.p6a_track(card, data))).join("")}</div>`
			);
		} else {
			parts.push(`<p class="pk-empty">${pp_esc(__("No open task has dates yet."))}</p>`);
		}
		if (undated.length) {
			parts.push(`<div class="pk-section-title">${pp_esc(__("Not scheduled yet ({0})", [undated.length]))}</div>`);
			parts.push(`<div class="pk-tl">${undated.map((card) => this.p6a_task_row(card, data, "")).join("")}</div>`);
		}
		if (data.truncated) {
			parts.push(`<p class="pk-note">${pp_esc(__("Showing the first {0} open tasks.", [data.limit]))}</p>`);
		}
		if (data.can_edit && (tasks.length || undated.length)) {
			parts.push(`<p class="pk-note">${pp_esc(__("Drag a task onto a day or a person's row to move it. Click it to edit it."))}</p>`);
		}
		return `<div class="pp-p6a-project">${parts.join("")}</div>`;
	},

	// ------------------------------------------------------------------ legend and hints

	// The tips, as the help's first section.
	p6a_tip_section() {
		return { title: __("Tips"), items: PP6A.hints.map(([, text]) => ({ sample_html: "", text: __(text) })) };
	},

	p6a_legend() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return;
		const chip = (cls, text) => `<span class="pp-chip${cls ? ` ${cls}` : ""}">${pp_esc(__(text))}</span>`;
		const card = (cls, text) => `<span class="pp-p6a-sample-card${cls ? ` ${cls}` : ""}">${pp_esc(__(text))}</span>`;
		const bar = (cls, width, soft) =>
			`<span class="pp-p6a-bar ${cls}"><span class="pp-bar"><i style="width:${width}%"></i>${
				soft ? `<b style="left:${width}%;width:${soft}%"></b>` : ""
			}</span></span>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		kit.legend(
			[
				this.p6a_tip_section(),
				...this.p6c_legend_sections(),
				{
					title: __("Quick looks"),
					items: [
						item("", "Click a person's name (in the panel, a crew row or a crew badge) to see their week: bookings, free hours, days off and the day's stops in driving order."),
						item("", "Click a date to see everyone's day side by side."),
						item("", "Click a project's name on a card to see the whole project on a timeline, with hours and the labor forecast."),
						item("", "Drag a task out of any of these onto a day or a person's row to move it. Esc or Back closes the side panel."),
						item("", "Hover over a name to light up all of that person's bookings, or over a project's name to light up its cards."),
					],
				},
				{
					title: __("Task cards"),
					items: [
						item(card("", "Dig"), "A project task. The left edge is the task's color. Drag it to another day to move it; it keeps its length."),
						item(card("pp-overdue", "Dig"), "Overdue: the end date has passed and the task is not done."),
						item(card("pp-tentative", "Dig"), "Pencil (tentative): soft load, not counted as booked until you firm it up."),
						item(card("pp-drafted", "Dig"), "Draft: a change kept to yourself until you publish it (Draft mode)."),
					],
				},
				{
					title: __("Chips on a card"),
					items: [
						item(chip("", "Day 2 of 3"), "Which day of a multi-day task this is."),
						item(chip("pp-amber", "Needs 1 more"), "Fewer people than the task's Crew needed."),
						item(chip("pp-amber", "No crew"), "Nobody is on it yet."),
						item(chip("pp-red", "Conflict"), "Someone on it is over their hours, double-booked or booked on a day off."),
						item(chip("pp-red", "Overdue"), "Its end date has passed."),
						item(chip("pp-pencil", "Pencil"), "Pencilled in, not firm yet."),
						item(chip("pp-amber", "Starts before Dig"), "It starts before a task it depends on ends."),
						item(chip("pp-amber", "Missing Forklift"), "Nobody on the crew holds a qualification it needs."),
						item(chip("pp-weather", "Rain 70%"), "Outdoor work on a day with rain (60% or more), a low of 32 °F or colder, or wind of 25 mph or more in the forecast."),
						item(chip("", "14h of 12h"), "Hours clocked against the plan. Red when it is running over."),
						item(chip("pp-equip", "Truck 3"), "A vehicle or asset the task uses."),
						item(chip("", "Delivery"), "A rental crew task. Its dates follow its Rental Booking."),
						item(chip("", "No dates"), "Not scheduled yet: drag it onto a day."),
						item(chip("pp-draft-chip", "Draft"), "Changed in Draft mode and not published yet."),
					],
				},
				{
					title: __("People"),
					items: [
						item(`<span class="pp-badge pp-p6a-sample-badge">AH</span>`, "Crew initials. Click one to see that person's week."),
						item(`<span class="pp-badge pp-lead pp-p6a-sample-lead">JD</span>`, "The crew lead (ringed)."),
						item(`<span class="pp-badge pp-more">+2</span>`, "More people than fit on the card."),
					],
				},
				{
					title: __("A person's day"),
					note: __("In Resources available and the crew view. Hours count driving when Settings say so."),
					items: [
						item(bar("pp-green", 50), "Up to 75% of their hours booked."),
						item(bar("pp-amber", 90), "More than 75% booked."),
						item(bar("pp-red", 100), "Over their hours."),
						item(bar("pp-green", 40, 30), "Hatched: pencilled hours, drawn after the firm ones."),
						item(`<span class="pp-p6a-sample pp-offday">${pp_esc(__("Off"))}</span>`, "Not working: time off, a holiday or not a work day. The type and reason of time off are never shown."),
						item(`<span class="pp-p6a-sample pp-conflict">${pp_esc(__("2h free"))}</span>`, "Red outline: a conflict on that day."),
						item(`<span class="pp-p6a-sample pp-softover">${pp_esc(__("Full"))}</span>`, "Amber outline: pencilled work would put them over."),
						item(chip("pp-red", "Long drive"), "More driving than the Settings limit."),
						item(`<span class="pp-p6a-sample">${PP6A.route_icon}</span>`, "Open the day's route: stops in driving order, drive times and a map."),
					],
				},
				{
					title: __("Other bookings (read-only)"),
					items: [
						item(`<span class="pp-fcard pp-k-visit pp-p6a-sample">${pp_esc(__("Visit"))}</span>`, "A maintenance visit. It moves on the Maintenance Planner."),
						item(`<span class="pp-fcard pp-k-rental pp-p6a-sample">${pp_esc(__("Rental"))}</span>`, "A rental crew task. It moves with its Rental Booking."),
						item(`<span class="pp-fcard pp-k-travel pp-p6a-sample">${pp_esc(__("Travel"))}</span>`, "A travel day. The whole day is taken."),
						item(`<span class="pp-ecard pp-clash pp-p6a-sample">${pp_esc(__("Truck 3"))}</span>`, "Crew view, equipment rows: red when a vehicle or asset is on two tasks at once, striped when it is in the shop."),
					],
				},
				{
					title: __("Dragging"),
					items: [
						item(`<span class="pp-p6a-sample pp-over">${pp_esc(__("Drop here"))}</span>`, "Where the card will land."),
						item(`<span class="pp-p6a-sample pp-over-past">${pp_esc(__("Past day"))}</span>`, "A day before today: the planner asks before it schedules work there."),
						item("", "On a touch screen, hold a card for a moment before you drag it."),
						item("", "A change that causes a conflict asks for a reason, never blocks. Undo puts the last change back."),
					],
				},
				...this.p6d_legend_sections(),
			].concat(this.p6b_legend_sections()),
			{ title: __("How to read the Project Planner"), owner: PP6A.owner }
		);
	},
};

Object.assign(ProjectPlanner.prototype, PP6A_METHODS);

// ====================================================================== Phase 6B: faster scheduling
//
// Nik picked four things on 2026-10-09 (TASK-2026-02468), and they all live here:
//
//   - right-click a card, an empty spot or a name (a long press of about half a second on a touch
//     screen, when the finger does not move)  a menu (planner_kit.menu): Edit, Assign to..., Move to
//                                            next free day, Pencil / Firm up, Duplicate, Duplicate
//                                            to..., Split, Project at a glance; Add task here... on an
//                                            empty spot; See their week on a name. Other phases add
//                                            items through this.p6_menu_providers
//   - drag the left or right edge of a card (week and crew views)
//                                            its start or end day moves (save_task start + end)
//   - double-click an empty spot             quick add for that person and day (quick_add_task)
//   - Shift-, Ctrl- or Cmd-click cards        a selection (this.p6_selection); drag one and they all
//                                            move by the same number of calendar days (move_many,
//                                            one reason, one Undo); a bar offers Move, Pencil, Clear
//   - the search box (or /)                  tasks, projects and people on screen first, then the
//                                            server (search_planner); a task jumps to its week
//   - T, the arrows, 1 2 3, Ctrl/Cmd+Z, ?, Esc keyboard shortcuts, listed in the legend
//
// Nothing here picks a person: Assign to... lists who is free on the day (who_is_free) and the
// planner clicks one ("suggest a crew" was declined). Every write goes through send(), so the reason
// prompt, draft mode and Undo apply; the planner_actions endpoints are routed there through
// this.p6_send_modules. A new task can be a draft (v1.603.0: duplicate, quick add and the build-out
// grid send draft=1 in Draft mode and the server keeps a "New task" Draft row); only a split cannot,
// and says so in draft mode instead of asking the server.
//
// Hooks into the class above, one line each: init_phase6b (constructor), render_phase6b (render),
// p6_send_modules (send), the grip check (drag_source), p6b_lift (lift), p6b_drop (drop), p6b_undo
// (undo), and p6b_legend_sections (p6a_legend).

const PP6B = {
	actions: "erpnext_enhancements.api.planner_actions",
	conflicts: "erpnext_enhancements.api.planner_conflicts",
	// The planner_actions writes. send() looks their module up in this.p6_send_modules.
	action_methods: ["duplicate_task", "split_task", "quick_add_task", "move_many", "remove_created_task"],
	long_press_ms: 500,
	search_min: 2,
	search_wait_ms: 250,
	search_limit: 20,
	flash_ms: 2600,
	widths: { assign: 440, panel: 460 },
};

// Pure helpers: no frappe, no moment, no page. tests/test_planner_phase6b.py runs them under node
// (and the Maintenance Planner keeps an identical copy, MP6B_PURE, run through the same cases).
const PP6B_PURE = {
	// "2026-10-12" + 3 -> "2026-10-15", by the calendar (in UTC, so no daylight-saving step).
	ymd_add(ymd, days) {
		const date = new Date(`${ymd}T00:00:00Z`);
		date.setUTCDate(date.getUTCDate() + (Number(days) || 0));
		return date.toISOString().slice(0, 10);
	},
	// Days from `a` to `b`, negative when `b` is earlier.
	ymd_diff(a, b) {
		return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
	},
	// Monday to Friday from `first` to `last` inclusive (0 when `last` is earlier).
	working_days(first, last) {
		let count = 0;
		for (let day = first, i = 0; day <= last && i < 400; day = PP6B_PURE.ymd_add(day, 1), i++) {
			const weekday = new Date(`${day}T00:00:00Z`).getUTCDay();
			if (weekday !== 0 && weekday !== 6) count++;
		}
		return count;
	},
	// How a split shares hours: api/planner_actions.split_weights, kept in step (both are tested).
	split_weights(start, end, split) {
		const first_last = PP6B_PURE.ymd_add(split, -1);
		const first = PP6B_PURE.working_days(start, first_last);
		const second = PP6B_PURE.working_days(split, end);
		if (first && second) return [first, second];
		return [PP6B_PURE.ymd_diff(start, first_last) + 1, PP6B_PURE.ymd_diff(split, end) + 1];
	},
	split_hours(total, first_weight, second_weight) {
		const weight = (Number(first_weight) || 0) + (Number(second_weight) || 0);
		const amount = Math.round((Number(total) || 0) * 100) / 100;
		if (amount <= 0 || weight <= 0) return [0, 0];
		const first = Math.round(((amount * first_weight) / weight) * 100) / 100;
		return [first, Math.round((amount - first) * 100) / 100];
	},
	// A multi-select move: every card the same calendar days later (or earlier), keeping its length.
	offset_moves(cards, days, modified_of) {
		return (cards || [])
			.filter((card) => card && card.start)
			.map((card) => ({
				task: card.name,
				modified: modified_of ? modified_of(card.name) : card.modified,
				start: PP6B_PURE.ymd_add(card.start, days),
				end: PP6B_PURE.ymd_add(card.end && card.end >= card.start ? card.end : card.start, days),
			}));
	},
	// The hours one person needs free on a day of this task: who_is_free's `hours`. A slot is its
	// length; no estimate is a full day; otherwise the estimate shared by the crew (one more when
	// someone is added) over the task's working days.
	need_hours(card, mode, day_hours) {
		const full = Number(day_hours) > 0 ? Number(day_hours) : 8;
		if (card.slot && card.slot.length === 2) {
			const minutes = card.slot.map((text) => {
				const parts = String(text).split(":");
				return Number(parts[0]) * 60 + Number(parts[1] || 0);
			});
			if (minutes[1] > minutes[0]) return Math.max(0.25, Math.round(((minutes[1] - minutes[0]) / 60) * 100) / 100);
		}
		const expected = Number(card.expected_time) || 0;
		if (expected <= 0) return Math.min(24, full);
		const people = Math.max(1, (card.crew || []).length + (mode === "add" ? 1 : 0));
		const last = card.end && card.end >= card.start ? card.end : card.start;
		const days = (card.start && PP6B_PURE.working_days(card.start, last)) || 1;
		return Math.min(24, Math.max(0.25, Math.round((expected / people / days) * 100) / 100));
	},
	// Toggle one key in a selection Set; true when it is selected afterwards.
	toggle(set, key) {
		if (set.has(key)) {
			set.delete(key);
			return false;
		}
		set.add(key);
		return true;
	},
	// A keydown as a planner shortcut, or null. `info`: key, ctrl, meta, shift, alt, editable (focus in
	// an input, textarea, select or contenteditable) and dialog (a frappe dialog is open).
	shortcut(info) {
		if (!info || info.editable || info.dialog) return null;
		const key = String(info.key || "");
		if ((info.ctrl || info.meta) && !info.alt && !info.shift && key.toLowerCase() === "z") return "undo";
		// Ctrl+S, Ctrl+K, Ctrl+G, Alt+... stay frappe's.
		if (info.ctrl || info.meta || info.alt) return null;
		if (key === "Escape" || key === "Esc") return "escape";
		if (key === "?") return "legend";
		// Shift+T is frappe's console.
		if (info.shift) return null;
		if (key === "t" || key === "T") return "today";
		if (key === "/") return "search";
		if (key === "ArrowLeft") return "prev";
		if (key === "ArrowRight") return "next";
		if (key === "1" || key === "2" || key === "3") return `view${key}`;
		return null;
	},
	// The menu: the page's own items, then each provider's group, a divider before each group.
	with_providers(items, providers, target) {
		const out = (items || []).slice();
		(providers || []).forEach((provider) => {
			let group = [];
			try {
				group = (provider(target) || []).filter(Boolean);
			} catch (e) {
				group = [];
			}
			if (!group.length) return;
			if (out.length && !out[out.length - 1].divider) out.push({ divider: true });
			out.push(...group);
		});
		return out;
	},
	// Search what is loaded: every word somewhere in an entry's label or keys. A label that starts
	// with the first word comes first, then people, projects (sites) and tasks (visits), then by label.
	search_local(q, entries, limit) {
		const words = String(q || "")
			.toLowerCase()
			.split(/\s+/)
			.filter(Boolean);
		if (!words.length) return [];
		const order = { person: 0, project: 1, site: 1, task: 2, visit: 2 };
		const rank = (kind) => (kind in order ? order[kind] : 3);
		const found = [];
		(entries || []).forEach((entry) => {
			const hay = [entry.label].concat(entry.keys || []).join(" ").toLowerCase();
			if (!words.every((word) => hay.includes(word))) return;
			const starts = String(entry.label || "").toLowerCase().startsWith(words[0]) ? 0 : 1;
			found.push({ entry, starts });
		});
		found.sort(
			(a, b) =>
				a.starts - b.starts ||
				rank(a.entry.kind) - rank(b.entry.kind) ||
				String(a.entry.label || "").localeCompare(String(b.entry.label || ""))
		);
		return found.slice(0, limit || 20).map((item) => item.entry);
	},
};

const PP6B_METHODS = {
	init_phase6b() {
		// The registries other phases add to (phase6-wave2): menu items, legend sections, endpoint modules.
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_legend_providers = this.p6_legend_providers || [];
		this.p6_send_modules = this.p6_send_modules || {};
		PP6B.action_methods.forEach((method) => (this.p6_send_modules[method] = PP6B.actions));
		// The multi-selection: task names. 6C's print reads it.
		this.p6_selection = this.p6_selection || new Set();
		this.p6b = {
			press: null,
			resize: null,
			flash: null,
			seen: null,
			next_free: {},
			keys_on: false,
			search: { q: "", token: 0, timer: null, local: [], remote: [], loading: false, active: 0, shown: [], list_id: "" },
		};
		this.p6b_build_search();
		this.$p6b_bar = $('<div class="p6b-bar" role="toolbar"></div>')
			.attr("aria-label", __("Selected tasks"))
			.hide()
			.appendTo(this.$body);
		const root = this.$body[0];
		// Capture on the page's own container: a modified click on a card selects it, ahead of the
		// card's own click (which opens it) and of 6A's quick-look clicks.
		(root.parentElement || root).addEventListener("click", (e) => this.p6b_modifier_click(e), true);
		root.addEventListener("click", (e) => this.p6b_plain_click(e));
		root.addEventListener("contextmenu", (e) => this.p6b_contextmenu(e));
		root.addEventListener("dblclick", (e) => this.p6b_dblclick(e));
		root.addEventListener("pointerdown", (e) => this.p6b_pointer_down(e), true);
		document.addEventListener("pointermove", (e) => this.p6b_pointer_move(e));
		document.addEventListener("pointerup", (e) => this.p6b_pointer_up(e));
		document.addEventListener("pointercancel", (e) => this.p6b_pointer_cancel(e));
		document.addEventListener(
			"touchmove",
			(e) => {
				if (this.p6b.resize) e.preventDefault();
			},
			{ passive: false }
		);
		this.p6b_bind_keys();
		// The route view, My week and Capacity draw no cards: the selection bar and an open search
		// list must not stay on top of them.
		if (frappe.router && typeof frappe.router.on === "function") {
			frappe.router.on("change", () => this.p6b_route_changed());
		}
	},

	p6b_route_changed() {
		const route = frappe.get_route() || [];
		// Capacity is the Crew address with ?capacity=: no cards either.
		const capacity = route[1] === "crew" && !!this.capacity_param();
		const calendar = route[0] === PP.route && (!route[1] || PP.views.includes(route[1])) && !capacity;
		if (!calendar && this.$p6b_bar) this.$p6b_bar.hide();
		if (this.p6b.resize) this.p6b_resize_end(false);
		this.p6b_close_results();
	},

	render_phase6b() {
		if (!this.p6b) return;
		if (this.p6b.seen !== this.data) {
			// A new answer from the server: the next-free-day answers may be out of date.
			this.p6b.seen = this.data;
			this.p6b.next_free = {};
		}
		this.p6b_prune_selection();
		this.p6b_decorate_selection();
		this.p6b_add_grips();
		this.p6b_render_bar();
		this.p6b_flash_pending();
	},

	// Writes to api/planner_actions go through send() like every other write.
	p6b_send(method, args, opts) {
		return this.send(method, args, opts);
	},

	p6b_kit() {
		return (this.p6a && this.p6a.kit) || null;
	},

	p6b_user(resource) {
		const person = resource && this.by_resource[resource];
		return (person && person.user) || null;
	},

	p6b_span_text(start, end) {
		if (!start) return __("No dates");
		return end && end !== start ? `${pp_when(start)} – ${pp_when(end)}` : pp_when(start);
	},

	// A route change from a menu item or search result: the kit closes its drawer first.
	p6b_navigate(fn) {
		const kit = this.p6b_kit();
		return kit ? kit.drawer.navigate(fn) : fn();
	},

	// The kit's side panel (or a Dialog until the kit has loaded) for the small forms here.
	p6b_panel(opts) {
		const kit = this.p6b_kit();
		if (!kit) return new frappe.ui.Dialog({ title: opts.title, fields: opts.fields });
		return kit.panel({
			title: opts.title,
			subtitle: opts.subtitle || "",
			fields: opts.fields,
			width: PP6B.widths.panel,
			key: opts.key || null,
			owner: PP6A.owner,
			push: this.$body[0],
		});
	},

	p6b_panel_open(panel) {
		return panel && (typeof panel.is_open === "function" ? panel.is_open() : !!panel.display);
	},

	// A split changes a task that exists and creates another at once, which one draft row cannot hold.
	// (Duplicate, quick add and the build-out grid are drafted: v1.603.0.)
	p6b_refuse_in_draft() {
		if (!this.draft_on) return false;
		frappe.msgprint(
			__(
				"Draft mode holds back changes to tasks that already exist, and a new task can be drafted; but a split changes a task and creates another at once, which cannot be a draft. Turn Draft mode off to split it."
			)
		);
		return true;
	},

	// ------------------------------------------------------------------ the right-click menu

	// What a right-click or long press landed on, in the shape the menu providers take.
	p6b_target(el) {
		if (!el || !el.closest || !this.$body[0].contains(el)) return null;
		if (el.closest("button, a, input, select, textarea, .pp-toolbar, .pp-fcard, .pp-route, .p6b-bar, .pp-draft-bar")) {
			return null;
		}
		const card_el = el.closest(".pp-card[data-task]");
		if (card_el) {
			const card = this.by_task[card_el.getAttribute("data-task")];
			if (!card) return null;
			return {
				kind: "card",
				card,
				ymd: card_el.getAttribute("data-date") || card.start || null,
				resource: card_el.getAttribute("data-resource") || null,
				el: card_el,
			};
		}
		const person_el = el.closest(".pp-person[data-resource]");
		if (person_el) {
			const resource = person_el.getAttribute("data-resource");
			return { kind: "person", resource, user: this.p6b_user(resource), el: person_el };
		}
		const cell = el.closest(".pp-cell[data-date][data-resource]");
		if (cell) {
			const resource = cell.getAttribute("data-resource");
			return { kind: "cell", resource, user: this.p6b_user(resource), ymd: cell.getAttribute("data-date"), el: cell };
		}
		const day = el.closest(".pp-day[data-date]");
		if (day) return { kind: "cell", resource: null, user: null, ymd: day.getAttribute("data-date"), el: day };
		return null;
	},

	p6b_contextmenu(e) {
		if (this.drag || this.p6b.resize) return;
		if (!this.p6b_open_for(e.target, { x: e.clientX, y: e.clientY }, !!this.p6b.press)) return;
		e.preventDefault();
		if (this.p6b.press) this.p6b.press.opened = true;
	},

	// Opens the menu for whatever is under `el`. False (and the browser's own menu) when there is
	// nothing of ours there or the kit has not loaded.
	p6b_open_for(el, anchor, from_touch) {
		if (!this.p6b_kit() || !this.data || !PP.views.includes(this.view)) return false;
		const target = this.p6b_target(el);
		if (!target) return false;
		// The page's own items, then every provider's (6C, 6D-UI): a target with none of ours can still
		// carry theirs, and a provider that throws is skipped.
		const items = this.p6b_menu_items(target);
		const all = PP6B_PURE.with_providers(items, this.p6_menu_providers, target);
		if (!all.length) return false;
		if (from_touch) {
			// The finger lifting is a click on whatever the menu now covers.
			this.click_blocked_until = Date.now() + 600;
			this.p6b_swallow_click();
		}
		this.p6b_open_menu(target, all, anchor);
		return true;
	},

	p6b_swallow_click() {
		// The click a lifting finger makes comes at once; a later one is the planner's own.
		const until = Date.now() + 350;
		const swallow = (e) => {
			document.removeEventListener("click", swallow, true);
			if (Date.now() < until) {
				e.stopPropagation();
				e.preventDefault();
			}
		};
		document.addEventListener("click", swallow, true);
		setTimeout(() => document.removeEventListener("click", swallow, true), 500);
	},

	p6b_menu_title(target) {
		if (target.kind === "card") return target.card.subject || target.card.name;
		if (target.kind === "person") return this.resource_label(target.resource);
		return [target.resource ? this.resource_label(target.resource) : "", target.ymd ? pp_when(target.ymd) : ""]
			.filter(Boolean)
			.join(" · ");
	},

	p6b_menu_items(target) {
		if (target.kind === "card") return this.p6b_card_items(target);
		if (target.kind === "person") {
			return [{ label: __("See their week"), on_click: () => this.p6a_open_person(target.resource) }];
		}
		if (target.kind === "cell") return this.p6b_cell_items(target);
		return [];
	},

	// `all`: the page's items with the providers' already appended (p6b_open_for).
	p6b_open_menu(target, all, anchor) {
		const kit = this.p6b_kit();
		const handle = kit.menu({ anchor, items: all, title: this.p6b_menu_title(target), owner: PP6A.owner });
		// The next free day is asked for as the menu opens; its hint fills in when the answer arrives.
		all.forEach((item) => {
			if (!item || !item.p6b_lookup) return;
			item.p6b_lookup.then((answer) => {
				const state = this.p6b_free_state(answer);
				this.p6b_menu_hint(handle, item.label, state.hint, state.disabled);
			});
		});
		return handle;
	},

	p6b_menu_hint(handle, label, hint, disabled) {
		if (!handle || !handle.el || !handle.el.isConnected) return;
		const button = Array.from(handle.el.querySelectorAll(".pk-menu-item")).find(
			(node) => node.firstChild && node.firstChild.textContent === label
		);
		if (!button) return;
		let span = button.querySelector(".pk-menu-hint");
		if (!span) {
			span = document.createElement("span");
			span.className = "pk-menu-hint";
			button.appendChild(span);
		}
		span.textContent = hint || "";
		button.disabled = !!disabled;
	},

	p6b_card_items(target) {
		const card = target.card;
		const can_edit = !!(this.data && this.data.can_edit);
		const editable = !!(card.movable && can_edit);
		const why = !can_edit
			? __("You cannot change tasks")
			: card.rental_kind
			? __("Follows its Rental Booking")
			: __("Read-only here");
		// v1.603.0: a drafted new task is not a Task yet, so it cannot be copied or split, and the
		// server has nothing to look up for "next free day".
		const fresh = !!card.new_task;
		const fresh_why = __("Not published yet");
		const copying = editable && !fresh;
		const copy_why = !editable ? why : fresh ? fresh_why : "";
		const creating = editable && !this.draft_on && !fresh;
		const create_why = !editable ? why : fresh ? fresh_why : this.draft_on ? __("Not in Draft mode") : "";
		const end = card.end && card.start && card.end > card.start ? card.end : card.start;
		const multi = !!(card.start && end > card.start);
		const items = [
			{ label: __("Edit"), on_click: () => this.open_card(card) },
			{
				label: __("Assign to…"),
				hint: editable ? "" : why,
				disabled: !editable,
				on_click: () => this.p6b_assign(card, target.ymd),
			},
			this.p6b_next_free_item(target, editable && !fresh, fresh ? fresh_why : why),
			card.tentative
				? { label: __("Firm up"), hint: editable ? "" : why, disabled: !editable, on_click: () => this.firm_up(card) }
				: {
						label: __("Pencil"),
						hint: editable ? __("Make it tentative") : why,
						disabled: !editable,
						on_click: () => this.p6b_pencil(card),
				  },
			{
				label: __("Duplicate"),
				hint: copy_why || __("Same days"),
				disabled: !copying,
				on_click: () => this.p6b_duplicate(card, null),
			},
			{ label: __("Duplicate to…"), hint: copy_why, disabled: !copying, on_click: () => this.p6b_duplicate_to(card) },
			{
				label: multi ? __("Split across days…") : __("Split in two…"),
				hint: create_why || (card.start ? "" : __("No dates")),
				disabled: !creating || !card.start,
				on_click: () => this.p6b_split(card, target),
			},
		];
		if (card.project) {
			items.push(
				{ divider: true },
				{ label: __("Project at a glance"), on_click: () => this.p6a_open_project(card.project) },
				{
					label: __("Open project"),
					on_click: () => this.p6b_navigate(() => frappe.set_route("Form", "Project", card.project)),
				}
			);
		}
		return items;
	},

	p6b_cell_items(target) {
		const can = !!(this.data && this.data.can_edit);
		const items = [
			{
				label: __("Add task here…"),
				hint: !can ? __("You cannot add tasks") : this.draft_on ? __("Kept as a draft") : __("Double-click"),
				disabled: !can,
				on_click: () => this.p6b_quick_add(target),
			},
		];
		if (target.ymd) items.push({ label: __("Everyone's day"), on_click: () => this.p6a_open_day(target.ymd) });
		return items;
	},

	// The person a "next free day" or "split in two" is about: the crew-view row the card sits in, the
	// lead, or the only person on it. None when several are on it and none leads.
	p6b_lead(card, row) {
		const crew = card.crew || [];
		if (row && crew.some((member) => member.resource === row)) return row;
		const lead = crew.find((member) => member.is_lead);
		if (lead) return lead.resource;
		return crew.length === 1 ? crew[0].resource : null;
	},

	p6b_next_free(card, resource, hours) {
		const key = [card.name, resource, hours || "", this.modified[card.name] || card.modified].join("|");
		if (!this.p6b.next_free[key]) {
			const args = { resource, task: card.name };
			if (card.start) args.after = card.start;
			if (hours) args.hours = hours;
			this.p6b.next_free[key] = Promise.resolve(
				frappe.call({ method: `${PP6B.conflicts}.get_next_free_day`, args })
			)
				.then((r) => (r && r.message) || null)
				.catch(() => null);
		}
		return this.p6b.next_free[key];
	},

	p6b_free_state(answer) {
		if (!answer) return { hint: __("Could not check"), disabled: false };
		if (answer.date) return { hint: pp_when(answer.date), disabled: false };
		return { hint: __("Nothing free in {0} days", [answer.horizon || 30]), disabled: true };
	},

	p6b_next_free_item(target, editable, why) {
		const card = target.card;
		const item = { label: __("Move to next free day"), hint: __("Looking…"), disabled: false };
		if (!editable) return Object.assign(item, { hint: why, disabled: true });
		const resource = this.p6b_lead(card, target.resource);
		if (!resource) {
			return Object.assign(item, { hint: (card.crew || []).length ? __("Mark a lead first") : __("No crew"), disabled: true });
		}
		const who = this.resource_label(resource);
		const lookup = this.p6b_next_free(card, resource);
		item.p6b_lookup = lookup;
		item.on_click = () =>
			lookup.then((answer) => {
				if (answer && answer.date) {
					this.p6b_move_to(card, answer.date);
					return;
				}
				frappe.show_alert(
					{ message: (answer && answer.note) || __("No free day found for {0}.", [who]), indicator: "orange" },
					7
				);
			});
		return item;
	},

	p6b_move_to(card, date) {
		const subject = card.subject || card.name;
		return this.commit(card, "save_task", { task: card.name, start: date }, __("{0} moved to {1}", [subject, pp_when(date)]));
	},

	p6b_pencil(card) {
		return this.commit(
			card,
			"save_task",
			{ task: card.name, tentative: 1 },
			__("{0} is now pencilled in", [card.subject || card.name])
		);
	},

	// ------------------------------------------------------------------ assign to...

	// Who has the hours free that day, from who_is_free. The planner picks; nothing is picked for them.
	p6b_assign(card, ymd) {
		const kit = this.p6b_kit();
		const day = ymd || card.start;
		if (!kit) {
			this.open_card(card);
			return;
		}
		if (!day) {
			frappe.msgprint(__("Give the task a date first: who is free depends on the day."));
			return;
		}
		const state = { mode: "add", data: null, token: 0 };
		const handle = kit.drawer.open({
			title: __("Assign to…"),
			subtitle: [card.subject || card.name, pp_when(day)].join(" · "),
			body: `<p class="pk-empty">${pp_esc(__("Finding who is free…"))}</p>`,
			width: PP6B.widths.assign,
			key: `assign:${card.name}`,
			owner: PP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6b_assign(this.by_task[card.name] || card, ymd),
			on_click: (e, h) => this.p6b_assign_click(e, h, card, day, state),
		});
		this.p6b_assign_load(handle, card, day, state);
	},

	p6b_assign_need(card, state) {
		const day_hours = this.data && this.data.settings && this.data.settings.default_day_hours;
		return PP6B_PURE.need_hours(card, state.mode === "add" ? "add" : "replace", day_hours);
	},

	p6b_assign_load(handle, card, day, state) {
		const token = ++state.token;
		const hours = this.p6b_assign_need(card, state);
		return Promise.resolve(frappe.call({ method: `${PP.api}.who_is_free`, args: { start: day, hours } }))
			.then((r) => {
				if (token !== state.token || !handle.is_open()) return;
				state.data = (r && r.message) || null;
				handle.set_body(this.p6b_assign_html(card, day, state));
			})
			.catch(() => {
				if (token !== state.token || !handle.is_open()) return;
				state.data = null;
				handle.set_body(this.p6b_assign_html(card, day, state));
			});
	},

	p6b_assign_html(card, day, state) {
		const crew = card.crew || [];
		const on = new Set(crew.map((member) => member.resource));
		const parts = [
			`<p class="pk-note">${pp_esc(
				__("Pick who should do it: nobody is chosen for you. Someone who is not free can still be booked; you will be asked for a reason.")
			)}</p>`,
		];
		if (crew.length) {
			const option = (value, text) =>
				`<label><input type="radio" name="p6b-mode" value="${pp_esc(value)}"${state.mode === value ? " checked" : ""}> ${pp_esc(text)}</label>`;
			parts.push(
				`<div class="p6b-modes" role="radiogroup" aria-label="${pp_esc(__("How to assign"))}">${option(
					"add",
					__("Add to the crew")
				)}${crew
					.map((member) => option(member.resource, __("Replace {0}", [this.resource_label(member.resource, member.label)])))
					.join("")}</div>`
			);
		}
		const data = state.data;
		if (!data) {
			parts.push(`<p class="pk-empty">${pp_esc(__("Who is free could not be loaded. Close this and try again."))}</p>`);
			return parts.join("");
		}
		const entry = (data.days || [])[0] || { free: [], not_free: [] };
		const row = (person, busy) =>
			`<button type="button" class="p6b-pick${busy ? " p6b-pick-busy" : ""}" data-p6b-pick="${pp_esc(person.resource)}">` +
			`<span><b>${pp_esc(person.label || person.resource)}</b>${
				person.group ? ` <span class="p6b-pick-why">${pp_esc(__(person.group))}</span>` : ""
			}</span>` +
			`<span class="p6b-pick-why">${pp_esc(busy ? person.reason || "" : __("{0}h free", [pp_hours(person.free_hours)]))}</span></button>`;
		const free = (entry.free || []).filter((person) => !on.has(person.resource));
		const busy = (entry.not_free || []).filter((person) => !on.has(person.resource));
		parts.push(
			`<div class="pk-section-title">${pp_esc(
				__("Free on {0} ({1}h or more)", [pp_when(entry.date || day), pp_hours(data.hours)])
			)}</div>`
		);
		parts.push(
			free.length
				? free.map((person) => row(person, false)).join("")
				: `<p class="pk-empty">${pp_esc(__("Nobody has those hours free that day."))}</p>`
		);
		if (busy.length) {
			parts.push(`<div class="pk-section-title">${pp_esc(__("Not free"))}</div>`);
			parts.push(busy.map((person) => row(person, true)).join(""));
		}
		if (crew.length) {
			parts.push(
				`<div class="pk-section-title">${pp_esc(__("Already on it"))}</div><p class="pk-note">${pp_esc(
					crew.map((member) => this.resource_label(member.resource, member.label)).join(", ")
				)}</p>`
			);
		}
		if (data.note) parts.push(`<p class="pk-note">${pp_esc(data.note)}</p>`);
		return parts.join("");
	},

	p6b_assign_click(e, handle, card, day, state) {
		const radio = e.target && e.target.closest ? e.target.closest("input[name='p6b-mode']") : null;
		if (radio) {
			const before = this.p6b_assign_need(card, state);
			state.mode = radio.value;
			if (this.p6b_assign_need(card, state) !== before) this.p6b_assign_load(handle, card, day, state);
			return;
		}
		const pick = e.target && e.target.closest ? e.target.closest("[data-p6b-pick]") : null;
		if (!pick) return;
		const resource = pick.getAttribute("data-p6b-pick");
		const mode = state.mode;
		handle.close();
		this.p6b_assign_to(card, resource, mode);
	},

	p6b_assign_to(card, resource, mode) {
		const fresh = this.by_task[card.name] || card;
		const who = this.resource_label(resource);
		const subject = fresh.subject || fresh.name;
		if (this.crew_has(fresh, resource)) {
			frappe.show_alert({ message: __("{0} is already on {1}.", [who, subject]), indicator: "orange" }, 6);
			return null;
		}
		if (mode && mode !== "add" && this.crew_has(fresh, mode)) {
			return this.commit(
				fresh,
				"swap_crew",
				{ task: fresh.name, from_resource: mode, to_resource: resource },
				__("{0}: {1} in place of {2}", [subject, who, this.resource_label(mode)])
			);
		}
		return this.commit(fresh, "add_crew", { task: fresh.name, resource }, __("{0} added to {1}", [who, subject]));
	},

	// ------------------------------------------------------------------ duplicate, split, quick add

	// A new task from api/planner_actions. Undo deletes it again (remove_created_task), and only when
	// nothing has been attached to it since; `extra` shapes the Undo entry (a split's puts the first
	// half back too).
	p6b_create(method, args, subject, message, extra) {
		return this.p6b_send(method, args, {})
			.then((result) => {
				if (!result || !result.name) return result;
				const created = extra ? extra(result) : { task: result.name, modified: result.modified };
				if (created.task && created.modified) this.modified[created.task] = created.modified;
				const snapshot = Object.assign({ p6b: "created", subject }, created);
				if (!this.push_undo(snapshot, message)) frappe.show_alert({ message, indicator: "green" }, 5);
				const card = result.second ? result.second.card : result.card;
				this.p6b.flash = { name: created.task, date: (card && card.start) || null, wait: false };
				return result;
			})
			.finally(() => this.load());
	},

	p6b_duplicate(card, date) {
		const subject = card.subject || card.name;
		const args = Object.assign({ task: card.name }, this.draft_args(card.project));
		if (date) args.date = date;
		const message = date ? __("{0} duplicated to {1}", [subject, pp_when(date)]) : __("{0} duplicated", [subject]);
		return this.p6b_create("duplicate_task", args, subject, message);
	},

	p6b_duplicate_to(card) {
		const panel = this.p6b_panel({
			title: __("Duplicate to…"),
			subtitle: card.subject || card.name,
			key: `p6b-duplicate:${card.name}`,
			fields: [
				{
					fieldtype: "Date",
					fieldname: "date",
					label: __("The copy starts on"),
					reqd: 1,
					default: card.start || this.today(),
					description: __("The copy keeps the task's length, crew, hours, qualifications, equipment and pencil flag."),
				},
			],
		});
		panel.set_primary_action(__("Duplicate"), (values) => {
			const date = values && values.date;
			if (!date) return;
			panel.hide();
			this.p6b_duplicate(card, date);
		});
		panel.show();
	},

	p6b_split(card, target) {
		if (this.p6b_refuse_in_draft() || !card.start) return;
		const end = card.end && card.end >= card.start ? card.end : card.start;
		const multi = end > card.start;
		const subject = card.subject || card.name;
		const middle = PP6B_PURE.ymd_add(card.start, Math.ceil((PP6B_PURE.ymd_diff(card.start, end) + 1) / 2));
		const panel = this.p6b_panel({
			title: multi ? __("Split across days") : __("Split in two"),
			subtitle: subject,
			key: `p6b-split:${card.name}`,
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "p6b_split_note",
					options: `<p class="pk-note">${pp_esc(
						multi
							? __("The task keeps the days before the day you pick. A new task does the rest, depends on it and has the same crew, qualifications and equipment. Hours are shared by working days.")
							: __("The task keeps its day with half the hours. A new task with the other half goes on the day you pick, with the same crew, qualifications and equipment.")
					)}</p>`,
				},
				{
					fieldtype: "Date",
					fieldname: "split_date",
					label: multi ? __("The second part starts on") : __("The second half goes on"),
					reqd: 1,
					default: multi ? middle : "",
				},
				{ fieldtype: "HTML", fieldname: "p6b_split_preview", options: '<p class="pk-note p6b-split-preview"></p>' },
			],
		});
		const preview = () => this.p6b_split_preview(panel, card, end, multi);
		panel.set_primary_action(__("Split"), (values) => {
			const date = values && values.split_date;
			if (!date) return;
			if (multi && !(date > card.start && date <= end)) {
				frappe.msgprint(__("Pick a day from {0} to {1}.", [pp_when(PP6B_PURE.ymd_add(card.start, 1)), pp_when(end)]));
				return;
			}
			if (!multi && date <= card.start) {
				frappe.msgprint(__("Pick a day after {0}.", [pp_when(card.start)]));
				return;
			}
			panel.hide();
			this.p6b_split_save(card, date);
		});
		panel.show();
		const field = panel.fields_dict && panel.fields_dict.split_date;
		if (field && field.$input) field.$input.on("change", preview);
		preview();
		if (multi) return;
		// Split in two: the second half goes on the person's next free day, unless the planner picks one.
		const resource = this.p6b_lead(card, target && target.resource);
		if (!resource) return;
		const who = this.resource_label(resource);
		const day_hours = this.data && this.data.settings && this.data.settings.default_day_hours;
		const half = Math.max(0.25, Math.round((PP6B_PURE.need_hours(card, "replace", day_hours) / 2) * 100) / 100);
		this.p6b_next_free(card, resource, half).then((answer) => {
			if (!this.p6b_panel_open(panel)) return;
			if (answer && answer.date && !panel.get_value("split_date")) panel.set_value("split_date", answer.date);
			const note =
				answer && answer.date
					? __("{0} is {1}'s next free day.", [pp_when(answer.date), who])
					: __("{0} has no free day in the next 30 days: pick one.", [who]);
			panel.$wrapper.find(".p6b-split-preview").attr("data-p6b-free", note);
			preview();
		});
	},

	p6b_split_preview(panel, card, end, multi) {
		const $box = panel.$wrapper.find(".p6b-split-preview");
		if (!$box.length) return;
		const date = panel.get_value("split_date");
		const free = $box.attr("data-p6b-free") || "";
		let text = "";
		if (!date) {
			text = free;
		} else if (multi) {
			if (!(date > card.start && date <= end)) {
				text = __("Pick a day from {0} to {1}.", [pp_when(PP6B_PURE.ymd_add(card.start, 1)), pp_when(end)]);
			} else {
				const [a, b] = PP6B_PURE.split_weights(card.start, end, date);
				const [first_hours, second_hours] = PP6B_PURE.split_hours(card.expected_time, a, b);
				const first = this.p6b_span_text(card.start, PP6B_PURE.ymd_add(date, -1));
				const second = this.p6b_span_text(date, end);
				text =
					Number(card.expected_time) > 0
						? __("First part: {0}, {1}h. Second part: {2}, {3}h.", [first, pp_hours(first_hours), second, pp_hours(second_hours)])
						: __("First part: {0}. Second part: {1}. With no estimate, each still books a full day per day.", [first, second]);
			}
		} else {
			text = [__("First half: {0}. Second half: {1}. The hours are shared equally.", [pp_when(card.start), pp_when(date)]), free]
				.filter(Boolean)
				.join(" ");
		}
		$box.text(text);
	},

	p6b_split_save(card, date) {
		const subject = card.subject || card.name;
		const restore = {
			task: card.name,
			start: card.start,
			end: card.end || card.start,
			expected_time: Number(card.expected_time) || 0,
			crew: this.crew_rows(card),
		};
		return this.p6b_create(
			"split_task",
			{ task: card.name, split_date: date, modified: this.modified[card.name] || card.modified },
			subject,
			__("{0} split in two", [subject]),
			(result) => ({
				p6b: "split",
				task: result.second.name,
				modified: result.second.modified,
				restore: Object.assign({}, restore, { modified: result.modified }, this.p6e_dependents(result)),
			})
		);
	},

	p6b_dblclick(e) {
		if (!this.data || !this.data.can_edit || !PP.views.includes(this.view)) return;
		const el = e.target;
		if (!el || !el.closest) return;
		if (el.closest(".pp-card, .pp-fcard, .pp-day-head, button, a, input, select, textarea, .pp-route, [data-pp6a-day], .p6b-grip")) {
			return;
		}
		const target = this.p6b_target(el);
		if (!target || target.kind !== "cell") return;
		e.preventDefault();
		if (window.getSelection) window.getSelection().removeAllRanges();
		this.p6b_quick_add(target);
	},

	p6b_quick_add(target) {
		if (!this.data || !this.data.can_edit) return;
		// The project list is the planner's own: Active customer jobs (engine.PLANNER_PROJECT_TYPES).
		const names = (this.data.projects || []).map((project) => project.name);
		// v1.602.1: Phase and Depends on offer the chosen project's tasks. A MultiSelectPills field calls
		// its get_data while the panel is still being built (autocomplete's make_input), before
		// p6b_panel returns, so a `const panel` read there threw "Cannot access 'panel' before
		// initialization" and the dialog never opened. Until it exists, the project is the planner's own.
		let panel = null;
		const chosen_project = () => (panel ? panel.get_value("project") : this.project) || "";
		panel = this.p6b_panel({
			title: __("Add a task"),
			subtitle: [target.resource ? this.resource_label(target.resource) : "", target.ymd ? pp_when(target.ymd) : ""]
				.filter(Boolean)
				.join(" · "),
			key: `p6b-add:${target.resource || ""}:${target.ymd || ""}`,
			fields: [
				{
					fieldtype: "Link",
					fieldname: "project",
					label: __("Project"),
					options: "Project",
					reqd: 1,
					default: this.project || "",
					description: __("Customer jobs only."),
					get_query: () => ({ filters: { name: ["in", names.length ? names : [""]] } }),
				},
				{ fieldtype: "Data", fieldname: "subject", label: __("Subject"), reqd: 1 },
				{
					fieldtype: "Float",
					fieldname: "hours",
					label: __("Hours"),
					description: __("Blank: a full day for the person. More hours than a day holds run over the next weekdays."),
				},
				{
					fieldtype: "Link",
					fieldname: "resource",
					label: __("Person"),
					options: "Planner Resource",
					default: target.resource || "",
					get_query: () => ({ filters: { is_active: 1 } }),
				},
				{ fieldtype: "Date", fieldname: "date", label: __("Date"), reqd: 1, default: target.ymd || this.today() },
				{ fieldtype: "Check", fieldname: "tentative", label: __("Pencil (tentative)") },
				// v1.602.0: the rest of a task, folded away so the common add stays four fields long.
				{ fieldtype: "Section Break", fieldname: "more", label: __("More about the task"), collapsible: 1, collapsed: 1 },
				{
					fieldtype: "Link",
					fieldname: "parent_task",
					label: __("Phase"),
					options: "Task",
					description: __("A phase (group task) of the same project."),
					get_query: () => ({ filters: { project: chosen_project(), is_group: 1 } }),
				},
				{
					fieldtype: "MultiSelectPills",
					fieldname: "depends_on",
					label: __("Depends on"),
					description: __("Tasks of the same project that come first."),
					get_data: (txt) => frappe.db.get_link_options("Task", txt, { project: chosen_project(), is_group: 0 }),
				},
				{ fieldtype: "Time", fieldname: "start_time", label: __("Starts at") },
				{ fieldtype: "Time", fieldname: "end_time", label: __("Ends at"), description: __("A time slot on the date above; blank is an all-day task.") },
				{
					fieldtype: "MultiSelectPills",
					fieldname: "crew",
					label: __("Others on the crew"),
					description: __("The person above is the lead. Nobody is picked for you."),
					get_data: (txt) => frappe.db.get_link_options("Planner Resource", txt, { is_active: 1 }),
				},
				{ fieldtype: "Int", fieldname: "crew_size", label: __("Crew size") },
				{ fieldtype: "Link", fieldname: "address", label: __("Address"), options: "Address" },
				{ fieldtype: "Check", fieldname: "outdoor", label: __("Outdoor work") },
				{ fieldtype: "Check", fieldname: "customer_visit", label: __("Customer-facing visit") },
				{ fieldtype: "Small Text", fieldname: "description", label: __("Description") },
			],
		});
		panel.set_primary_action(__("Add"), (values) => this.p6b_quick_add_save(panel, values || {}));
		panel.show();
	},

	p6b_quick_add_save(panel, values) {
		const subject = String(values.subject || "").trim();
		if (!values.project || !subject || !values.date) return null;
		// draft=1 in Draft mode (v1.603.0): the task is kept as a draft, not created.
		const args = Object.assign({ project: values.project, subject, date: values.date, tentative: values.tentative ? 1 : 0 }, this.draft_args(values.project));
		if (Number(values.hours) > 0) args.hours = Number(values.hours);
		if (values.resource) args.resource = values.resource;
		// v1.602.0: only what was filled in is sent, so a plain add sends what it always sent.
		if (values.parent_task) args.parent_task = values.parent_task;
		const needs = (values.depends_on || []).filter(Boolean);
		if (needs.length) args.depends_on = JSON.stringify(needs);
		if (values.start_time || values.end_time) {
			if (!values.start_time || !values.end_time || String(values.end_time) <= String(values.start_time)) {
				frappe.msgprint(__("Give the time slot a start and an end, with the end after the start."));
				return null;
			}
			args.start_time = values.start_time;
			args.end_time = values.end_time;
		}
		const others = (values.crew || []).filter((name) => name && name !== values.resource);
		if (others.length) args.crew = JSON.stringify(others);
		if (values.address) args.address = values.address;
		if (values.outdoor) args.outdoor = 1;
		if (values.customer_visit) args.customer_visit = 1;
		if (String(values.description || "").trim()) args.description = String(values.description).trim();
		if (parseInt(values.crew_size, 10) > 0) args.crew_size = parseInt(values.crew_size, 10);
		panel.hide();
		return this.p6b_create("quick_add_task", args, subject, __("{0} added on {1}", [subject, pp_when(values.date)]));
	},

	// ------------------------------------------------------------------ resize

	// The left and right edges of a card become grips: on the first and last day of a multi-day task
	// in the week view, and on every card in the crew view. Never on a rental crew task (its dates
	// follow its booking), a time-slot task (the slot decides its day) or in the month view.
	p6b_add_grips() {
		if (!this.data || !this.data.can_edit || (this.view !== "week" && this.view !== "crew")) return;
		const tip_start = pp_esc(__("Drag to change the first day"));
		const tip_end = pp_esc(__("Drag to change the last day"));
		this.$grid_wrap[0].querySelectorAll(".pp-card[data-task]").forEach((el) => {
			if (el.querySelector(".p6b-grip")) return;
			const card = this.by_task[el.getAttribute("data-task")];
			const ymd = el.getAttribute("data-date");
			if (!card || !card.movable || !card.start || !ymd || (card.slot && card.slot.length === 2)) return;
			const end = card.end && card.end >= card.start ? card.end : card.start;
			if (end === card.start && this.view !== "crew") return;
			if (ymd === card.start) {
				el.insertAdjacentHTML("beforeend", `<span class="p6b-grip p6b-grip-l" data-p6b-edge="start" title="${tip_start}"></span>`);
			}
			if (ymd === end) {
				el.insertAdjacentHTML("beforeend", `<span class="p6b-grip p6b-grip-r" data-p6b-edge="end" title="${tip_end}"></span>`);
			}
		});
	},

	p6b_pointer_down(e) {
		const grip = e.target && e.target.closest ? e.target.closest(".p6b-grip[data-p6b-edge]") : null;
		if (grip && !(e.button > 0)) {
			this.p6b_resize_start(e, grip);
			return;
		}
		// A touch press is remembered for the long-press menu.
		this.p6b.press =
			e.pointerType === "touch" && !(e.button > 0)
				? { id: e.pointerId, x: e.clientX, y: e.clientY, at: Date.now(), target: e.target, moved: false, opened: false }
				: null;
	},

	p6b_pointer_move(e) {
		const resize = this.p6b.resize;
		if (resize && e.pointerId === resize.id) {
			this.p6b_resize_move(e);
			return;
		}
		const press = this.p6b.press;
		if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > PP.drag_px) {
			press.moved = true;
		}
	},

	p6b_pointer_up(e) {
		const resize = this.p6b.resize;
		if (resize && e.pointerId === resize.id) {
			resize.solo = this.as4_solo(e);
			this.p6b_resize_end(true);
			return;
		}
		const press = this.p6b.press;
		this.p6b.press = null;
		if (!press || e.pointerId !== press.id || press.moved || press.opened) return;
		// A long press that did not become a drag: the card was lifted but never moved.
		if (Date.now() - press.at < PP6B.long_press_ms) return;
		this.p6b_open_for(press.target, { x: e.clientX, y: e.clientY }, true);
	},

	p6b_pointer_cancel(e) {
		if (this.p6b.resize && e.pointerId === this.p6b.resize.id) this.p6b_resize_end(false);
		if (this.p6b.press && e.pointerId === this.p6b.press.id) this.p6b.press = null;
	},

	p6b_resize_start(e, grip) {
		if (this.drag || this.p6b.resize || !this.data || !this.data.can_edit) return;
		const card_el = grip.closest(".pp-card[data-task]");
		const card = card_el && this.by_task[card_el.getAttribute("data-task")];
		if (!card || !card.movable || card.saving || !card.start) return;
		// Never a move: the page's own pointerdown (bubble) never sees this one.
		e.preventDefault();
		e.stopPropagation();
		const end = card.end && card.end >= card.start ? card.end : card.start;
		const label = document.createElement("div");
		label.className = "p6b-span-label";
		document.body.appendChild(label);
		this.p6b.resize = {
			id: e.pointerId,
			edge: grip.getAttribute("data-p6b-edge") === "start" ? "start" : "end",
			card,
			row: card_el.getAttribute("data-resource") || null,
			start: card.start,
			end,
			new_start: card.start,
			new_end: end,
			label,
		};
		document.body.classList.add("p6b-resizing");
		this.p6b_resize_paint(e.clientX, e.clientY);
	},

	p6b_resize_move(e) {
		const resize = this.p6b.resize;
		e.preventDefault();
		const el = document.elementFromPoint(e.clientX, e.clientY);
		const spot = el && el.closest ? el.closest(".pp-cell[data-date], .pp-day[data-date]") : null;
		if (spot && this.$grid_wrap[0].contains(spot)) {
			const ymd = spot.getAttribute("data-date");
			// At least one day: an edge never passes the other one.
			if (resize.edge === "end") resize.new_end = ymd < resize.start ? resize.start : ymd;
			else resize.new_start = ymd > resize.end ? resize.end : ymd;
		}
		this.edge_scroll(e.clientX, e.clientY);
		this.p6b_resize_paint(e.clientX, e.clientY);
	},

	// The live ghost: the days of the new span lit up (in the card's own row in the crew view) and a
	// label by the pointer.
	p6b_resize_paint(x, y) {
		const resize = this.p6b.resize;
		if (!resize) return;
		const grid = this.$grid_wrap[0];
		grid.querySelectorAll(".p6b-span").forEach((el) => el.classList.remove("p6b-span"));
		const spots = grid.querySelectorAll(this.view === "crew" ? ".pp-cell[data-date][data-resource]" : ".pp-day[data-date]");
		spots.forEach((el) => {
			if (this.view === "crew" && resize.row && el.getAttribute("data-resource") !== resize.row) return;
			const ymd = el.getAttribute("data-date");
			if (ymd >= resize.new_start && ymd <= resize.new_end) el.classList.add("p6b-span");
		});
		const days = PP6B_PURE.ymd_diff(resize.new_start, resize.new_end) + 1;
		resize.label.textContent =
			days === 1
				? __("{0} · 1 day", [pp_when(resize.new_start)])
				: __("{0} – {1} · {2} days", [pp_when(resize.new_start), pp_when(resize.new_end), days]);
		resize.label.style.left = `${Math.round(x + 14)}px`;
		resize.label.style.top = `${Math.round(y + 14)}px`;
	},

	p6b_resize_end(save) {
		const resize = this.p6b.resize;
		this.p6b.resize = null;
		if (!resize) return;
		document.body.classList.remove("p6b-resizing");
		if (resize.label) resize.label.remove();
		this.$grid_wrap[0].querySelectorAll(".p6b-span").forEach((el) => el.classList.remove("p6b-span"));
		if (!save || (resize.new_start === resize.start && resize.new_end === resize.end)) return;
		this.click_blocked_until = Date.now() + 400;
		const card = resize.card;
		const subject = card.subject || card.name;
		const message =
			resize.new_start === resize.new_end
				? __("{0} is now on {1}", [subject, pp_when(resize.new_start)])
				: __("{0} now runs {1} – {2}", [subject, pp_when(resize.new_start), pp_when(resize.new_end)]);
		// The same save as the dialog's: both dates, through send(), so a conflict asks for a reason
		// and Undo puts the old span back.
		const run = () =>
			this.commit(card, "save_task", { task: card.name, start: resize.new_start, end: resize.new_end }, message, {
				solo: !!resize.solo,
			});
		if (resize.new_start !== resize.start && resize.new_start < this.today()) {
			frappe.confirm(__("{0} is before today. Schedule the task on a past day?", [pp_when(resize.new_start)]), run);
			return;
		}
		run();
	},

	// ------------------------------------------------------------------ selection

	p6b_selectable(card) {
		return !!(card && card.movable && card.start && this.data && this.data.can_edit);
	},

	p6b_selected_cards() {
		return [...this.p6_selection].map((name) => this.by_task[name]).filter((card) => this.p6b_selectable(card));
	},

	p6b_modifier_click(e) {
		if (!(e.shiftKey || e.ctrlKey || e.metaKey) || e.button > 0 || Date.now() < this.click_blocked_until) return;
		const el = e.target && e.target.closest ? e.target.closest(".pp-card[data-task]") : null;
		if (!el || !this.$body[0].contains(el) || e.target.closest("button, a, input, select, textarea, .p6b-grip")) return;
		e.preventDefault();
		e.stopPropagation();
		this.p6b_toggle(el.getAttribute("data-task"));
	},

	p6b_toggle(name) {
		const card = this.by_task[name];
		if (!card) return;
		if (!this.p6_selection.has(name) && !this.p6b_selectable(card)) {
			frappe.show_alert(
				{ message: __("{0} cannot be moved from the planner, so it cannot be selected.", [card.subject || name]), indicator: "orange" },
				5
			);
			return;
		}
		PP6B_PURE.toggle(this.p6_selection, name);
		this.p6b_selection_changed();
	},

	// A plain click on empty space clears the selection.
	p6b_plain_click(e) {
		if (!this.p6_selection.size || e.shiftKey || e.ctrlKey || e.metaKey) return;
		const el = e.target;
		if (!el || !el.closest) return;
		if (el.closest(".pp-card, .pp-fcard, .p6b-bar, .pp-toolbar, .pp-draft-bar, button, a, input, select, textarea, label")) return;
		this.p6b_clear_selection();
	},

	p6b_clear_selection() {
		if (!this.p6_selection.size) return false;
		this.p6_selection.clear();
		this.p6b_selection_changed();
		return true;
	},

	p6b_selection_changed() {
		this.p6b_decorate_selection();
		this.p6b_render_bar();
		$(document).trigger("p6-selection-changed", [this]);
	},

	p6b_prune_selection() {
		if (!this.p6_selection.size || !this.data) return;
		let changed = false;
		[...this.p6_selection].forEach((name) => {
			if (!this.p6b_selectable(this.by_task[name])) {
				this.p6_selection.delete(name);
				changed = true;
			}
		});
		if (changed) $(document).trigger("p6-selection-changed", [this]);
	},

	p6b_decorate_selection() {
		this.$body[0].querySelectorAll(".pp-card[data-task]").forEach((el) => {
			el.classList.toggle("p6b-selected", this.p6_selection.has(el.getAttribute("data-task")));
		});
	},

	p6b_render_bar() {
		const $bar = this.$p6b_bar;
		if (!$bar) return;
		const cards = this.p6b_selected_cards();
		if (!cards.length || !PP.views.includes(this.view)) {
			$bar.hide().empty();
			return;
		}
		$bar.empty().show();
		$('<span class="p6b-bar-count"></span>').text(__("{0} selected", [cards.length])).appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Move…"))
			.on("click", () => this.p6b_move_panel())
			.appendTo($bar);
		const all_pencil = cards.every((card) => card.tentative);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(all_pencil ? __("Firm up") : __("Pencil"))
			.attr("title", all_pencil ? __("Make them all firm bookings") : __("Make them all tentative"))
			.on("click", () => this.p6b_pencil_many(all_pencil ? 0 : 1))
			.appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Clear"))
			.on("click", () => this.p6b_clear_selection())
			.appendTo($bar);
		$('<span class="p6b-bar-note"></span>').text(this.p6e_bar_note()).appendTo($bar);
	},

	p6b_lift(drag) {
		if (!drag || !drag.ghost || !drag.source || drag.source.kind !== "card") return;
		if (!this.p6_selection.has(drag.source.card.name)) return;
		const count = this.p6b_selected_cards().length;
		if (count < 2) return;
		const badge = document.createElement("span");
		badge.className = "p6b-count";
		badge.textContent = String(count);
		drag.ghost.appendChild(badge);
	},

	// A drop of one selected card when several are selected: they all move by the same calendar days.
	// True when it was handled here (drop() then does nothing more).
	p6b_drop(source, target) {
		if (!source || source.kind !== "card" || !source.card || this.p6_selection.size < 2) return false;
		if (!this.p6_selection.has(source.card.name) || this.p6b_selected_cards().length < 2) return false;
		if (target.resource && source.from_resource && target.resource !== source.from_resource) {
			frappe.show_alert(
				{
					message: __(
						"Several tasks are selected: drop them on the same person's row to change their days. To hand one task to someone else, clear the selection first."
					),
					indicator: "orange",
				},
				8
			);
			return true;
		}
		const from = source.from_date || source.card.start;
		if (!from || !target.date) return true;
		const days = this.p6e_drop_days(from, target.date);
		if (days) this.p6b_move_selection(days, { solo: !!target.solo });
		return true;
	},

	p6b_move_panel() {
		const cards = this.p6b_selected_cards();
		if (!cards.length) return;
		let panel = null;
		panel = this.p6b_panel({
			title: __("Move {0} tasks", [cards.length]),
			key: "p6b-move",
			fields: [
				// Move by (calendar or working days) lived on the selection bar until the planner UX
				// redesign (D12); it is a field here now, and a drag still uses the choice it makes.
				{
					fieldtype: "Select",
					fieldname: "unit",
					label: __("Move by"),
					options: this.p6e_unit_options(),
					default: this.p6e_unit_label(this.p6e_unit()),
					description: __("Also what dragging several selected tasks counts in. Remembered on this device."),
					change: () => this.p6e_panel_unit(panel),
				},
				{
					fieldtype: "Int",
					fieldname: "days",
					label: this.p6e_move_label(),
					reqd: 1,
					default: this.p6e_move_default(),
					description: this.p6e_move_note(),
				},
			],
		});
		panel.set_primary_action(__("Move"), (values) => {
			const days = parseInt(values && values.days, 10) || 0;
			if (!days) return;
			panel.hide();
			this.p6b_move_selection(days);
		});
		panel.show();
	},

	p6b_move_selection(days, opts) {
		const cards = this.p6b_selected_cards();
		if (!cards.length || !days) return null;
		const moves = this.p6e_moves(cards, days, (name) => this.modified[name] || (this.by_task[name] || {}).modified);
		const snapshot = {
			p6b: "many",
			what: "dates",
			subject: __("{0} tasks", [cards.length]),
			items: cards.map((card) => ({ task: card.name, start: card.start, end: card.end || card.start })),
		};
		const message = this.p6e_message(cards.length, days);
		const run = () => this.p6b_commit_many(cards, moves, snapshot, message, opts);
		if (moves.some((move) => move.start < this.today())) {
			frappe.confirm(__("Some of these tasks would start before today. Move them anyway?"), run);
			return null;
		}
		return run();
	},

	p6b_pencil_many(on) {
		const cards = this.p6b_selected_cards().filter((card) => (card.tentative ? 1 : 0) !== on);
		if (!cards.length) return null;
		const moves = cards.map((card) => ({ task: card.name, modified: this.modified[card.name] || card.modified, tentative: on }));
		const snapshot = {
			p6b: "many",
			what: "pencil",
			subject: __("{0} tasks", [cards.length]),
			items: cards.map((card) => ({ task: card.name, tentative: card.tentative ? 1 : 0 })),
		};
		const message = on ? __("{0} tasks pencilled in", [cards.length]) : __("{0} tasks firmed up", [cards.length]);
		return this.p6b_commit_many(cards, moves, snapshot, message);
	},

	// One move_many through send(): one reason prompt for the lot, all or nothing, one Undo entry. P4:
	// the tasks that follow move too (not with `opts.solo`), one "Notify crew?" for the lot, and send()
	// adds draft (the hold's 1, or "auto" for a Draft schedule).
	p6b_commit_many(cards, moves, snapshot, message, opts) {
		const changes = moves.map((move) => {
			const card = this.by_task[move.task] || cards.find((one) => one.name === move.task) || { name: move.task };
			return this.as4_change(card, "save_task", move.start || move.end ? { start: move.start, end: move.end } : {});
		});
		const go = (extra) => {
			cards.forEach((card) => (card.saving = true));
			this.render();
			snapshot.notify = extra.notify;
			const args = Object.assign({ moves: JSON.stringify(moves) }, extra);
			return this.p6b_send("move_many", args, {
				snapshot,
				message,
				decorate: (result, sent) => this.as4_decorate(result, sent),
			}).then((result) => {
				((result && result.moved) || []).forEach((move) => {
					if (move.modified) this.modified[move.task] = move.modified;
				});
				return result;
			});
		};
		return Promise.resolve(this.as4_prepare(changes, opts, go)).finally(() => this.load());
	},

	// ------------------------------------------------------------------ undo

	// Undo of a Phase 6B change. False for any other entry, which undo() handles as before.
	p6b_undo(snap) {
		if (!snap || !snap.p6b) return false;
		const message = __("Undone: {0}", [snap.subject]);
		if (snap.p6b === "many") {
			const moves = (snap.items || []).map((item) => {
				const move = { task: item.task, modified: this.modified[item.task] || (this.by_task[item.task] || {}).modified || "" };
				if (snap.what === "pencil") move.tentative = item.tentative;
				else Object.assign(move, { start: item.start, end: item.end });
				return move;
			});
			const args = { moves: JSON.stringify(moves) };
			// P4: the move's "Notify crew?" answer, again (send() adds the draft argument).
			if (snap.notify != null) args.notify = snap.notify;
			this.p6b_send("move_many", args, { message, auto_reason: __(PP.undo_reason) }).finally(() => this.load());
			return true;
		}
		// A task created here (a duplicate, a quick add, a split's second half): delete it again, and
		// put a split's first half back. The server refuses when anything was attached since.
		const args = { task: snap.task, modified: this.modified[snap.task] || snap.modified };
		if (snap.restore) {
			const first = snap.restore.task;
			args.restore = JSON.stringify(Object.assign({}, snap.restore, { modified: this.modified[first] || snap.restore.modified }));
		}
		this.p6b_send("remove_created_task", args, { message }).finally(() => this.load());
		return true;
	},

	// ------------------------------------------------------------------ search

	p6b_build_search() {
		const $wrap = $('<span class="p6b-search-wrap"></span>');
		// First among the filters, so a phone's "Filters and tools" sheet takes it with them (PPTB).
		if (this.$filters && this.$filters.length) $wrap.prependTo(this.$filters);
		else $wrap.appendTo(this.$toolbar);
		const list_id = `p6b-results-${Math.random().toString(36).slice(2, 9)}`;
		this.p6b.search.list_id = list_id;
		this.$p6b_search = $('<input type="search" class="form-control input-sm p6b-search" autocomplete="off" spellcheck="false">')
			.attr({
				placeholder: __("Search  ( / )"),
				title: __("Find a task, project or person. Press / to come here."),
				"aria-label": __("Search tasks, projects and people"),
				role: "combobox",
				"aria-autocomplete": "list",
				"aria-expanded": "false",
				"aria-controls": list_id,
			})
			.appendTo($wrap);
		this.$p6b_results = $('<div class="p6b-results" role="listbox"></div>').attr("id", list_id).hide().appendTo($wrap);
		this.$p6b_search.on("input", () => this.p6b_search_input());
		this.$p6b_search.on("keydown", (e) => this.p6b_search_key(e));
		this.$p6b_search.on("focus", () => {
			if (this.p6b.search.q.length >= PP6B.search_min) this.p6b_render_results();
		});
		this.$p6b_search.on("blur", () => setTimeout(() => this.p6b_close_results(), 150));
		// Keep the focus in the box while a result is clicked.
		this.$p6b_results.on("mousedown", (e) => e.preventDefault());
		this.$p6b_results.on("click", "[data-p6b-result]", (e) => {
			const item = this.p6b.search.shown[Number(e.currentTarget.getAttribute("data-p6b-result"))];
			if (item) this.p6b_pick(item);
		});
	},

	p6b_search_input() {
		const search = this.p6b.search;
		search.q = String(this.$p6b_search.val() || "").trim();
		search.active = 0;
		search.remote = [];
		clearTimeout(search.timer);
		const token = ++search.token;
		if (search.q.length < PP6B.search_min) {
			search.local = [];
			search.loading = false;
			this.p6b_close_results();
			return;
		}
		search.local = PP6B_PURE.search_local(search.q, this.p6b_local_entries(), PP6B.search_limit);
		search.loading = true;
		this.p6b_render_results();
		const q = search.q;
		search.timer = setTimeout(() => this.p6b_remote_search(q, token), PP6B.search_wait_ms);
	},

	// What is on screen: every task card, the projects of the filter and the people.
	p6b_local_entries() {
		const out = [];
		Object.values(this.by_task || {}).forEach((card) => {
			out.push({
				kind: "task",
				name: card.name,
				label: card.subject || card.name,
				keys: [card.name, card.project_title || "", card.project || ""],
				sub: [card.project_title || card.project, this.p6b_span_text(card.start, card.end)].filter(Boolean).join(" · "),
				start: card.start || null,
				end: card.end || null,
			});
		});
		((this.data && this.data.projects) || []).forEach((project) => {
			out.push({
				kind: "project",
				name: project.name,
				label: project.title || project.name,
				keys: [project.name],
				sub: project.title && project.title !== project.name ? project.name : "",
			});
		});
		((this.data && this.data.resources) || []).forEach((person) => {
			out.push({
				kind: "person",
				resource: person.name,
				user: person.user || null,
				label: person.label || person.name,
				keys: [],
				sub: person.group ? __(person.group) : "",
			});
		});
		return out;
	},

	p6b_result_key(item) {
		return `${item.kind}|${item.name || item.resource || ""}`;
	},

	p6b_from_server(item) {
		if (!item || !item.kind) return null;
		if (item.kind === "task") {
			return {
				kind: "task",
				name: item.name,
				label: item.label || item.name,
				sub: [item.project_title || item.project, this.p6b_span_text(item.start, item.end)].filter(Boolean).join(" · "),
				start: item.start || null,
				end: item.end || null,
			};
		}
		if (item.kind === "project") {
			return { kind: "project", name: item.name, label: item.label || item.name, sub: [item.name, item.status ? __(item.status) : ""].filter(Boolean).join(" · ") };
		}
		if (item.kind === "person") {
			return { kind: "person", resource: item.resource, user: item.user || null, label: item.label || item.resource, sub: item.group ? __(item.group) : "" };
		}
		return null;
	},

	// Tasks outside the range on screen (and projects or people the page has not loaded) come from
	// the server; what the page already listed is not repeated.
	p6b_remote_search(q, token) {
		return Promise.resolve(frappe.call({ method: `${PP6B.actions}.search_planner`, args: { q, start: this.anchor } }))
			.then((r) => {
				const search = this.p6b.search;
				if (token !== search.token) return;
				const seen = new Set(search.local.map((item) => this.p6b_result_key(item)));
				search.remote = (((r && r.message) || {}).results || [])
					.map((item) => this.p6b_from_server(item))
					.filter((item) => item && !seen.has(this.p6b_result_key(item)));
				search.loading = false;
				if (this.$p6b_search.is(":focus")) this.p6b_render_results();
			})
			.catch(() => {
				if (token !== this.p6b.search.token) return;
				this.p6b.search.loading = false;
				if (this.$p6b_search.is(":focus")) this.p6b_render_results();
			});
	},

	p6b_render_results() {
		const search = this.p6b.search;
		if (search.q.length < PP6B.search_min) {
			this.p6b_close_results();
			return;
		}
		const shown = search.local.concat(search.remote).slice(0, PP6B.search_limit);
		search.shown = shown;
		if (search.active >= shown.length) search.active = 0;
		const kinds = { task: __("Task"), project: __("Project"), person: __("Person") };
		const rows = shown.map(
			(item, index) =>
				`<div class="p6b-result${index === search.active ? " p6b-active" : ""}" role="option" id="${pp_esc(
					`${search.list_id}-${index}`
				)}" aria-selected="${index === search.active ? "true" : "false"}" data-p6b-result="${index}">` +
				`<span class="p6b-result-kind">${pp_esc(kinds[item.kind] || item.kind)}</span>` +
				`<span class="p6b-result-label">${pp_esc(item.label)}</span>` +
				(item.sub ? `<span class="p6b-result-sub">${pp_esc(item.sub)}</span>` : "") +
				`</div>`
		);
		const note = search.loading ? __("Searching the rest…") : shown.length ? "" : __("Nothing found.");
		this.$p6b_results.html(rows.join("") + (note ? `<div class="p6b-results-note">${pp_esc(note)}</div>` : "")).show();
		this.$p6b_search.attr("aria-expanded", "true");
		if (shown.length) this.$p6b_search.attr("aria-activedescendant", `${search.list_id}-${search.active}`);
		else this.$p6b_search.removeAttr("aria-activedescendant");
	},

	p6b_close_results() {
		if (!this.$p6b_results) return;
		this.$p6b_results.hide().empty();
		this.$p6b_search.attr("aria-expanded", "false").removeAttr("aria-activedescendant");
	},

	p6b_search_key(e) {
		const search = this.p6b.search;
		if (e.key === "ArrowDown" || e.key === "ArrowUp") {
			if (!search.shown.length) return;
			e.preventDefault();
			search.active = (search.active + (e.key === "ArrowDown" ? 1 : -1) + search.shown.length) % search.shown.length;
			this.p6b_render_results();
		} else if (e.key === "Enter") {
			e.preventDefault();
			const item = search.shown[search.active] || search.shown[0];
			if (item) this.p6b_pick(item);
		} else if (e.key === "Escape") {
			// The box's own Esc: it never reaches the drawer or frappe behind it.
			e.preventDefault();
			e.stopPropagation();
			if (this.$p6b_results.is(":visible")) this.p6b_close_results();
			else this.$p6b_search.val("").trigger("blur");
		}
	},

	p6b_pick(item) {
		this.tb_close_sheet();
		this.p6b_close_results();
		this.$p6b_search.trigger("blur");
		if (item.kind === "person") this.p6a_open_person(item.resource);
		else if (item.kind === "project") this.p6a_open_project(item.name);
		else if (item.kind === "task") this.p6b_jump_to_task(item);
	},

	// A task: the calendar goes to its week (a real route, so Back comes back) and the card flashes.
	p6b_jump_to_task(item) {
		const card = this.by_task[item.name];
		const start = item.start || (card && card.start) || null;
		if (!start) {
			if (card && PP.views.includes(this.view)) {
				this.p6b.flash = { name: card.name, date: null, wait: false };
				this.p6b_flash_pending();
				return;
			}
			this.p6b_navigate(() => frappe.set_route("Form", "Task", item.name));
			return;
		}
		this.p6b.flash = { name: item.name, date: start, wait: true };
		if (PP.views.includes(this.view) && this.range_days().includes(start)) {
			this.p6b_flash_pending();
			return;
		}
		this.go(this.view === "week" || this.view === "crew" ? this.view : "week", start);
	},

	p6b_flash_pending() {
		const flash = this.p6b.flash;
		if (!flash || !this.data || !PP.views.includes(this.view)) return;
		if (flash.date && !(this.data.start <= flash.date && flash.date <= this.data.end)) {
			// A jump waits for its week to load; a new task drawn elsewhere is simply not flashed.
			if (!flash.wait) this.p6b.flash = null;
			return;
		}
		this.p6b.flash = null;
		const el = Array.from(this.$body[0].querySelectorAll(".pp-card[data-task]")).find(
			(node) => node.getAttribute("data-task") === flash.name
		);
		if (!el) {
			if (flash.wait) {
				// A task the schedule leaves out (no phase, no crew; P2) is found by search all the same:
				// say why it is not drawn, and offer the switch that draws it.
				const show = () => {
					// Flash it once it is drawn: the jump's own flash was spent on this load.
					this.p6b.flash = { name: flash.name, date: flash.date, wait: false };
					this.set_all_tasks(true);
				};
				const actions = this.all_tasks ? {} : { [__("Show all tasks")]: show };
				const message = this.all_tasks
					? __("{0} is hidden by the filters on screen.", [flash.name])
					: __("{0} is hidden by the filters on screen, or is not phase or crew work.", [flash.name]);
				frappe.show_alert({ message, indicator: "orange" }, 10, actions);
			}
			return;
		}
		el.classList.add("p6b-flash");
		try {
			el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
		} catch (e) {
			el.scrollIntoView();
		}
		setTimeout(() => el.classList.remove("p6b-flash"), PP6B.flash_ms);
	},

	// ------------------------------------------------------------------ keyboard

	// One keydown listener on the document (capture, so frappe's window handler does not also act on
	// a key used here), added while the page shows and removed when it hides. frappe.ui.keys does not
	// fit: its "shift+/" handler is global and would open frappe's own shortcut list next to ours.
	p6b_bind_keys() {
		const handler = (e) => this.p6b_key(e);
		const on = () => {
			if (this.p6b.keys_on) return;
			document.addEventListener("keydown", handler, true);
			this.p6b.keys_on = true;
		};
		const off = () => {
			if (!this.p6b.keys_on) return;
			document.removeEventListener("keydown", handler, true);
			this.p6b.keys_on = false;
			this.p6b_close_results();
		};
		const wrapper = this.page && this.page.wrapper;
		if (wrapper && typeof wrapper.on === "function") {
			// frappe triggers these on the page itself; a Bootstrap dropdown's or collapse's show/hide
			// inside the page bubbles up as the same event name and must not switch the keys off.
			wrapper.on("show", (e) => e.target === wrapper[0] && on()).on("hide", (e) => e.target === wrapper[0] && off());
		}
		on();
	},

	p6b_page_live() {
		const route = frappe.get_route() || [];
		if (route[0] !== PP.route) return false;
		const wrapper = this.page && this.page.wrapper;
		return !wrapper || typeof wrapper.is !== "function" || wrapper.is(":visible");
	},

	p6b_key(e) {
		if (e.defaultPrevented || !this.p6b_page_live()) return;
		const target = e.target;
		const editable = !!(
			target &&
			((target.closest && target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])")) ||
				target.isContentEditable)
		);
		const action = PP6B_PURE.shortcut({
			key: e.key,
			ctrl: e.ctrlKey,
			meta: e.metaKey,
			shift: e.shiftKey,
			alt: e.altKey,
			editable,
			dialog: !!(window.cur_dialog && window.cur_dialog.display),
		});
		if (!action) return;
		// A menu's own keys (arrows, Enter, Esc) are the menu's.
		if (target && target.closest && target.closest(".pk-menu")) return;
		if (action === "escape") {
			// Never swallowed: the kit closes its overlay and frappe blurs as usual.
			this.p6b_escape();
			return;
		}
		if (!this.p6b_run_key(action)) return;
		e.preventDefault();
		e.stopPropagation();
	},

	p6b_run_key(action) {
		// The route view and My week keep their own buttons.
		if (!PP.views.includes(this.view) && this.view !== PP.capacity_view) return false;
		if (action === "today") {
			this.go(this.view, frappe.datetime.get_today());
			return true;
		}
		if (action === "prev" || action === "next") {
			this.shift(action === "prev" ? -1 : 1);
			return true;
		}
		if (action.startsWith("view")) {
			const view = PP.views[Number(action.slice(4)) - 1];
			if (!view) return false;
			this.go(view, this.anchor);
			return true;
		}
		if (action === "undo") {
			// Capacity changes nothing and offers no Undo (v1.687.0): the key is not the planner's there.
			if (this.view === PP.capacity_view) return false;
			this.undo();
			return true;
		}
		if (action === "legend") {
			if (!this.p6b_kit()) return false;
			this.p6a_legend();
			return true;
		}
		if (action === "search") {
			if (!this.$p6b_search || !this.$p6b_search.is(":visible")) return false;
			this.$p6b_search.trigger("focus").trigger("select");
			return true;
		}
		return false;
	},

	p6b_escape() {
		if (this.drag) return;
		if (this.p6b.resize) {
			this.p6b_resize_end(false);
			return;
		}
		const kit = this.p6b_kit();
		// An open menu or drawer closes first; the selection stays.
		if (kit && kit.guard && typeof kit.guard.top === "function" && kit.guard.top()) return;
		this.p6b_clear_selection();
	},

	// The legend's Keyboard and Faster scheduling sections, then any other phase's (p6_legend_providers).
	p6b_legend_sections() {
		const key = (text) => `<kbd class="p6b-kbd">${pp_esc(text)}</kbd>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		const own = [
			{
				title: __("Keyboard"),
				note: __("Not while typing in a box or with a dialog open."),
				items: [
					item(key("T"), "Go to today."),
					item(key("←") + key("→"), "The previous or next week (month, or four weeks on Capacity)."),
					item(key("1") + key("2") + key("3"), "Week, month or crew view."),
					item(key("Ctrl") + key("Z"), "Undo the last change (⌘Z on a Mac)."),
					item(key("/"), "Search tasks, projects and people."),
					item(key("?"), "This help."),
					item(key("Esc"), "Close a menu or side panel, or clear the selection."),
				],
			},
			{
				title: __("Faster scheduling"),
				items: [
					item("", "Right-click a card, an empty spot or a name (on a touch screen, hold it still for a moment) for a menu: assign someone, move to the next free day, pencil, duplicate, split."),
					item(
						`<span class="pp-p6a-sample-card p6b-selected">${pp_esc(__("Dig"))}</span>`,
						"Shift-click (or Ctrl- or ⌘-click) cards to select several. Drag one and they all move by the same number of calendar days, or of working days if you switch the bar to that."
					),
					item('<span class="p6b-legend-grip"></span>', "Drag a card's left or right edge to change its first or last day (week and crew views)."),
					item("", "Double-click an empty spot to add a task for that person and day."),
					item("", "Assign to… lists who is free that day. Nobody is picked for you."),
				],
			},
		];
		const extra = (this.p6_legend_providers || []).flatMap((provider) => {
			try {
				return provider() || [];
			} catch (e) {
				return [];
			}
		});
		return own.concat(extra);
	},
};

Object.assign(ProjectPlanner.prototype, PP6B_METHODS);

// ====================================================================== Phase 6C: big picture, tablet mode and print
//
// TASK-2026-02469. Nik picked all six on 2026-10-09; each is on both planners (the Maintenance Planner
// has the same block, MP6C_METHODS):
//
//   - Color by (toolbar's More ▸ Display)      the cards' left edge by Task color (today's look, the
//                                            default), Person (the lead, or the row in the crew view),
//                                            Job type, Project, Project manager or Status; the key in
//                                            Display and in the "?" legend. Job type and status
//                                            use fixed palettes; project and PM a stable hash
//                                            (planner_kit.big_picture), so a project is always the
//                                            same color on every device; a person their own color,
//                                            as on the dots and crew badges
//   - click a project's name on a card, "Highlight" in the project drawer, "Highlight this project"
//     in a task's panel or the right-click menu (6B's p6_menu_providers)
//                                            every card of that project lights up and the rest fade,
//                                            in every view and week; a toolbar pill and Esc clear it.
//                                            Not remembered: a reload starts clear
//   - Views (toolbar's More ▸ Views)           named views ("Field crew, Build only") saved on the
//                                            server per person (api/planner_views_prefs), switch and
//                                            delete; the view last used is saved a moment after any
//                                            change and restored when the planner opens, on any
//                                            device. The route always wins: only a bare
//                                            /project-planner takes the saved view, and it replaces
//                                            that history entry (route_flags.replace_route), so
//                                            Back never lands on a second copy
//   - the team strip                         over the week and crew views: per day the free hours of
//                                            everyone the group filter shows, amber at 90% booked,
//                                            red past capacity, pencil hatched; click a day for
//                                            everyone's day (6A's day peek)
//   - Tap to move (More ▸ Tools; on by default on a touch screen)
//                                            tap a card, then the day (or a person's day in the crew
//                                            view): the move goes through drop -> plan_drop ->
//                                            commit -> send, exactly like a drag, so the reason
//                                            prompt, the past-day question and Undo all apply. A tap
//                                            on a card with it off still opens the card
//   - Print (More ▸ Print)                     exactly what is on screen (view, range, filters, colors,
//                                            highlight), or only 6B's selection, built from the
//                                            loaded data (never the live DOM) on the print design
//                                            system's chrome, printed from a browser window
//
// Everything Phase 6C adds is in this block, mixed into ProjectPlanner below. The hooks into the
// class and the blocks above are one line each: init_phase6c (constructor), p6c_before_route
// (handle_route), p6c_set_mode (set_mode), render_phase6c (render), p6c_links and p6c_action
// (open_card), p6c_ready (p6a_ready), p6c_project_drawer (p6a_open_project) and p6c_legend_sections
// (p6a_legend).

const PP6C = {
	prefs: "erpnext_enhancements.api.planner_views_prefs",
	planner: "project",
	default_view: "week",
	tap_pref: "ee_project_planner_tap_move",
	save_delay: 1500,
	restore_wait: 4000,
	key_limit: 12,
	legend_limit: 40,
	// What a saved view may hold: the same rules as api/planner_views_prefs.SCHEMA["project"].
	schema: {
		// "heatmap" is what a view saved before P7 says for Capacity (the Heatmap and Utilization tabs merged);
		// it is still accepted, and the page treats it as Capacity (PP.capacity_aliases).
		view: ["enum", ["week", "month", "crew", "heatmap", "capacity"]],
		project: ["text"],
		pm: ["text"],
		group: ["enum", ["", "Field", "PM", "Design", "Subcontractor"]],
		foreign: ["bool"],
		over_only: ["bool"],
		panel: ["bool"],
		color_by: ["enum", ["default", "person", "job_type", "project", "pm", "status"]],
		color_chosen: ["bool"],
		// Show all tasks (P2, D6): off is phase and crew work only.
		all_tasks: ["bool"],
	},
	// What the cards are colored by until someone chooses (D12, the planner UX redesign): Job type. The
	// stored value "default" is the old Task color, which every "last used" view saved before this carries
	// whether or not anyone picked it. So a view says whether its color was picked (color_chosen, kept on
	// the server with the view, so it follows the person to any device), and a saved "default" counts only
	// when it was.
	default_color: "job_type",
	// Phase 4's Running over chip is never restored on its own the next morning (a named view may hold it).
	not_remembered: ["over_only"],
	color_modes: [
		["default", "Task color"],
		["person", "Person"],
		["job_type", "Job type"],
		["project", "Project"],
		["pm", "Project manager"],
		["status", "Status"],
	],
	view_labels: { week: "Week", month: "Month", crew: "Crew", heatmap: "Capacity", capacity: "Capacity" },
	none_color: "#94a3b8",
};

const PP6C_METHODS = {
	init_phase6c() {
		this.p6c = {
			color_by: PP6C.default_color,
			color_chosen: false,
			highlight: null,
			moving: null,
			tap: this.p6c_tap_default(),
			views: [],
			last: null,
			loaded: false,
			loading: null,
			waiting: false,
			restored: false,
			saved_text: null,
			save_timer: null,
			drawer_tool: null,
		};
		// 6B's right-click menu asks every provider for items; on this branch alone nobody does, and
		// each action here has its own way in as well (a project name, the panel's link, the toolbar).
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.p6c_menu_items(target));

		this.$p6c_pill = $('<span class="pp-p6c-pill" role="status"></span>').hide().insertAfter(this.$title);
		$('<span class="pp-p6c-pill-text"></span>').appendTo(this.$p6c_pill);
		$('<button type="button" class="pp-p6c-pill-x">×</button>')
			.attr("title", __("Stop highlighting (Esc)"))
			.attr("aria-label", __("Stop highlighting"))
			.on("click", () => this.p6c_clear_highlight())
			.appendTo(this.$p6c_pill);
		// Color by and its key are in the toolbar's More ▸ Display, Tap to move in Tools, the saved views in
		// Views and Print in Print (PPTB, v1.687.0, v2); this block gives each section its items
		// (p6c_color_items, p6c_view_items, p6c_print_items).
		this.$p6c_movebar = $('<div class="pp-p6c-movebar" role="status"></div>').hide().appendTo(this.$body);

		// Capture on the document: a tap that moves a card is taken before 6A's quick looks and the
		// card's own click see it.
		document.addEventListener("click", (e) => this.p6c_tap_click(e), true);
		window.addEventListener("keydown", (e) => this.p6c_keydown(e), true);
		// After 6A's capture listener on the same element: a project's name on a card highlights it
		// (6A opens its drawer from the same click).
		this.$body[0].addEventListener("click", (e) => this.p6c_body_click(e), true);
		this.$body.on("change click", () => this.p6c_state_soon());
		this.p6c_sync_controls();
		this.p6c_fetch_views();
	},

	// The kit has loaded (6A's p6a_ready): paint what needed it.
	p6c_ready() {
		if (this.p6c && this.data && PP.views.includes(this.view)) this.render_phase6c();
	},

	render_phase6c() {
		if (!this.p6c || !this.data || !PP.views.includes(this.view)) return;
		this.p6c_sync_controls();
		this.p6c_paint();
		this.p6c_apply_highlight();
		this.p6c_render_strip();
		this.p6c_mark_moving();
		this.p6c_state_soon();
	},

	// The route, Capacity and My week views have no cards to color, highlight or move (the
	// menus leave Color by and Tap to move out there: PPTB's tb_calendar).
	p6c_set_mode(mode) {
		if (!this.p6c) return;
		const calendar = mode === "";
		if (!calendar) {
			this.p6c_disarm();
			if (this.$p6c_pill) this.$p6c_pill.hide();
		}
		this.p6c_state_soon();
	},

	p6c_kit() {
		const kit = this.p6a && this.p6a.kit;
		return kit && kit.big_picture ? kit : null;
	},

	p6c_pick(state) {
		const kit = this.p6c_kit();
		if (kit) return kit.big_picture.pick_view(state, PP6C.schema);
		// Before the kit: the keys, unchecked (the server has already cleaned what it sends).
		const out = {};
		Object.keys(PP6C.schema).forEach((key) => {
			if (state && Object.prototype.hasOwnProperty.call(state, key)) out[key] = state[key];
		});
		return out;
	},

	p6c_here() {
		return (frappe.get_route() || [])[0] === PP.route && this.$body.is(":visible");
	},

	// ------------------------------------------------------------------ controls

	p6c_sync_controls() {
		const p6c = this.p6c;
		if (!p6c) return;
		// A value whose option is not there yet (before the first load) is picked up by fill_filters.
		if (this.$project) this.$project.val(this.project || "");
		if (this.$pm) this.$pm.val(this.pm || "");
		if (this.$group) this.$group.val(PP.groups.includes(this.group) ? this.group : "");
		if (this.$p6c_pill) {
			const on = !!p6c.highlight && PP.views.includes(this.view);
			this.$p6c_pill.toggle(on);
			if (on) {
				const text = __("Highlighting {0}", [p6c.highlight.label || p6c.highlight.key]);
				this.$p6c_pill.find(".pp-p6c-pill-text").text(text).attr("title", text);
			}
		}
	},

	// ------------------------------------------------------------------ color by

	p6c_set_color(mode) {
		const known = PP6C.color_modes.map(([value]) => value);
		this.p6c.color_by = known.includes(mode) ? mode : PP6C.default_color;
		// A choice, even of Task color: the view says so from now on (p6c_before_route).
		this.p6c.color_chosen = true;
		this.render();
	},

	p6c_mode_label(mode) {
		const found = PP6C.color_modes.find(([value]) => value === mode);
		return __(found ? found[1] : "Task color");
	},

	p6c_lead(card) {
		const crew = card.crew || [];
		return crew.find((member) => member.is_lead) || crew[0] || null;
	},

	// The color of a card in the current mode; null in the default mode (the card keeps its own).
	// `row` is the crew-view row the card sits in.
	p6c_card_color(card, row) {
		const kit = this.p6c_kit();
		const mode = this.p6c.color_by;
		if (!kit || mode === "default" || !card) return null;
		const bp = kit.big_picture;
		if (mode === "person") {
			const lead = row || (this.p6c_lead(card) || {}).resource;
			return lead ? this.resource_color(lead) : PP6C.none_color;
		}
		if (mode === "job_type") return bp.palette_color(bp.JOB_TYPES, bp.job_type(card.project_type, card.rental_kind));
		if (mode === "project") return card.project ? bp.hash_color(card.project) : PP6C.none_color;
		if (mode === "pm") {
			const project = card.project && this.by_project[card.project];
			return project && project.pm ? bp.hash_color(project.pm) : PP6C.none_color;
		}
		if (mode === "status") return bp.palette_color(bp.TASK_STATUSES, bp.task_status(card.status, card.overdue));
		return null;
	},

	p6c_paint() {
		const kit = this.p6c_kit();
		if (!kit || this.p6c.color_by === "default") return;
		this.$body[0].querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			const color = kit.big_picture.safe_paint(this.p6c_card_color(card, el.getAttribute("data-resource")), "");
			if (!color) return;
			el.style.setProperty("--pp-p6c-c", color);
			el.classList.add("pp-p6c-colored");
		});
	},

	// The cards a key or a legend describes: the ones the filters let through.
	p6c_visible_cards() {
		if (!this.data) return [];
		return [].concat(this.data.tasks || [], this.data.unscheduled || []).filter((card) => this.task_visible(card));
	},

	// What the current colors mean: [{key, label, color, hi}] (hi: a project a click can highlight).
	p6c_legend_entries() {
		const kit = this.p6c_kit();
		const mode = this.p6c.color_by;
		if (!kit || mode === "default") return [];
		const bp = kit.big_picture;
		if (mode === "job_type") return bp.palette_legend(bp.JOB_TYPES);
		if (mode === "status") return bp.palette_legend(bp.TASK_STATUSES);
		if (mode === "person") {
			return this.resources().map((resource) => ({
				key: resource.name,
				label: resource.label || resource.name,
				color: this.resource_color(resource.name),
			}));
		}
		const seen = {};
		let none = false;
		this.p6c_visible_cards().forEach((card) => {
			if (mode === "project") {
				if (!card.project) none = true;
				else if (!seen[card.project]) {
					seen[card.project] = {
						key: card.project,
						label: card.project_title && card.project_title !== card.project ? card.project_title : card.project,
						color: bp.hash_color(card.project),
						hi: card.project,
					};
				}
				return;
			}
			const project = card.project && this.by_project[card.project];
			if (!project || !project.pm) none = true;
			else if (!seen[project.pm]) seen[project.pm] = { key: project.pm, label: project.pm, color: bp.hash_color(project.pm) };
		});
		const out = Object.values(seen).sort((a, b) => String(a.label).localeCompare(String(b.label)));
		if (none) {
			out.push({
				key: "",
				label: mode === "project" ? __("No project") : __("No project manager"),
				color: PP6C.none_color,
			});
		}
		return out;
	},

	p6c_swatch_html(color) {
		const kit = this.p6c_kit();
		const paint = kit ? kit.big_picture.safe_paint(color, PP6C.none_color) : PP6C.none_color;
		return `<i class="pp-p6c-swatch" style="--pp-color: ${pp_esc(paint)}"></i>`;
	},

	// Display's Color by: one radio item a mode, then the key of what the colors mean on screen (it
	// used to be a row of its own under the toolbar). A project in the key highlights its cards.
	p6c_color_items() {
		if (!this.p6c) return [];
		const items = [{ heading: __("Color by") }];
		PP6C.color_modes.forEach(([value, label]) => {
			items.push({
				label: __("Color: {0}", [__(label)]),
				radio: true,
				checked: this.p6c.color_by === value,
				on_click: () => this.p6c_set_color(value),
			});
		});
		const entries = PP.views.includes(this.view) ? this.p6c_legend_entries() : [];
		if (!entries.length) return items;
		const shown = entries.slice(0, PP6C.key_limit);
		items.push({ divider: true }, { heading: __("Colors: {0}", [this.p6c_mode_label(this.p6c.color_by)]) });
		shown.forEach((entry) => {
			const on = !!(entry.hi && this.p6c.highlight && this.p6c.highlight.key === entry.hi);
			items.push(
				entry.hi
					? {
							label: entry.label,
							sample_html: this.p6c_swatch_html(entry.color),
							checked: on,
							title: __("Highlight {0}", [entry.label]),
							on_click: () => this.p6c_toggle_highlight(entry.hi),
					  }
					: { label: entry.label, sample_html: this.p6c_swatch_html(entry.color), info: true }
			);
		});
		if (entries.length > shown.length) {
			items.push({ label: __("+{0} more (see ?)", [entries.length - shown.length]), info: true });
		}
		return items;
	},

	// The "?" legend's sections (6A's p6a_legend lists them first).
	p6c_legend_sections() {
		if (!this.p6c) return [];
		const mode = this.p6c.color_by;
		const entries = this.p6c_legend_entries().slice(0, PP6C.legend_limit);
		const colors = {
			title: __("Card colors: {0}", [this.p6c_mode_label(mode)]),
			note:
				mode === "default"
					? __("Each card's left edge is the task's own color. More ▸ Display ▸ Color by colors the cards by person, job type, project, project manager or status instead.")
					: "",
			items:
				mode === "default"
					? [{ sample_html: this.p6c_swatch_html(PP.default_color), text: __("A task with no color of its own.") }]
					: entries.map((entry) => ({ sample_html: this.p6c_swatch_html(entry.color), text: entry.label })),
		};
		const strip = `<span class="pp-p6c-sday pp-amber pp-p6a-sample"><span class="pp-p6c-stext">${pp_esc(__("46h free"))}</span></span>`;
		const pill = `<span class="pp-p6c-pill"><span class="pp-p6c-pill-text">${pp_esc(__("Highlighting"))}</span></span>`;
		return [
			colors,
			{
				title: __("The big picture"),
				items: [
					{
						sample_html: pill,
						text: __("Click a project's name on a card, or Highlight this project, to light up all its tasks in every view and fade the rest. Esc or × clears it."),
					},
					{
						sample_html: strip,
						text: __("The team row over the week and crew views: the free hours of everyone the group filter shows. Amber at 90% booked, red when the team is booked past its hours, pencil hatched. Click a day to see everyone's day."),
					},
					{
						sample_html: "",
						text: __("Tap to move: tap a card, then tap the day (or a person's day in the crew view) where it goes. On by default on a touch screen; it asks for a reason and can be undone like a drag."),
					},
					{
						sample_html: "",
						text: __("Views: save your filters, view and colors under a name and switch between them. The planner opens the way you last left it, on any device."),
					},
					{
						sample_html: "",
						text: __("Print: exactly what is on screen, with your filters and colors, or only the cards you selected."),
					},
				],
			},
		];
	},

	// ------------------------------------------------------------------ highlight a project

	p6c_project_label(project) {
		const known = this.by_project[project];
		return known && known.title && known.title !== project ? `${known.title} (${project})` : project;
	},

	p6c_highlight(project) {
		if (!project) return;
		this.p6c.highlight = { key: project, label: this.p6c_project_label(project) };
		this.p6c_apply_highlight();
		this.p6c_sync_controls();
		this.p6c_refresh_drawer_tool();
	},

	p6c_clear_highlight() {
		if (!this.p6c.highlight) return;
		this.p6c.highlight = null;
		this.p6c_apply_highlight();
		this.p6c_sync_controls();
		this.p6c_refresh_drawer_tool();
	},

	p6c_toggle_highlight(project) {
		if (this.p6c.highlight && this.p6c.highlight.key === project) this.p6c_clear_highlight();
		else this.p6c_highlight(project);
	},

	p6c_apply_highlight() {
		const root = this.$body[0];
		const hi = this.p6c.highlight;
		root.classList.toggle("pp-p6c-on", !!hi && PP.views.includes(this.view));
		root.querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const card = this.by_task[el.getAttribute("data-task")];
			el.classList.toggle("pp-p6c-hi", !!(hi && card && card.project === hi.key));
		});
	},

	// A project's name on a card (6A made it a target), or an entry of the color key.
	p6c_body_click(e) {
		const target = e.target;
		if (!this.p6c || !target || !target.closest || Date.now() < this.click_blocked_until) return;
		const chip = target.closest(".pp-card [data-pk-project]");
		if (chip) this.p6c_highlight(decodeURIComponent(chip.getAttribute("data-pk-project")));
	},

	p6c_links(card) {
		if (!this.p6c || !card || !card.project) return [];
		const on = this.p6c.highlight && this.p6c.highlight.key === card.project;
		return [["p6c_highlight", on ? __("Stop highlighting this project") : __("Highlight this project")]];
	},

	p6c_action(action, card) {
		if (action !== "p6c_highlight") return false;
		if (card && card.project) this.p6c_toggle_highlight(card.project);
		return true;
	},

	p6c_menu_items(target) {
		if (!this.p6c || !target || target.kind !== "card" || !target.card) return [];
		const card = target.card;
		const items = [];
		if (card.project) {
			const on = this.p6c.highlight && this.p6c.highlight.key === card.project;
			items.push({
				label: on ? __("Stop highlighting this project") : __("Highlight this project"),
				on_click: () => this.p6c_toggle_highlight(card.project),
			});
		}
		const source = target.el ? this.drag_source(target.el) : null;
		if (source && source.kind === "card") {
			items.push({ label: __("Move with taps…"), hint: __("then tap a day"), on_click: () => this.p6c_arm(source) });
		}
		return items;
	},

	// The project drawer (6A) gets a Highlight switch in its header.
	p6c_project_drawer(project, handle) {
		if (!this.p6c || !handle || typeof handle.set_tools !== "function") return;
		const button = document.createElement("button");
		button.type = "button";
		button.className = "btn btn-default btn-xs pp-p6c-drawer-hi";
		button.title = __("Light up this project's tasks on the calendar and fade the rest");
		button.addEventListener("click", () => this.p6c_toggle_highlight(project));
		handle.set_tools(button);
		this.p6c.drawer_tool = { project, handle, button };
		this.p6c_refresh_drawer_tool();
	},

	p6c_refresh_drawer_tool() {
		const tool = this.p6c.drawer_tool;
		if (!tool) return;
		if (!tool.handle.is_open()) {
			this.p6c.drawer_tool = null;
			return;
		}
		const on = !!(this.p6c.highlight && this.p6c.highlight.key === tool.project);
		tool.button.textContent = on ? __("Highlighted ✓") : __("Highlight");
		tool.button.setAttribute("aria-pressed", on ? "true" : "false");
	},

	// Esc: a move being placed first, then the highlight. A drawer or menu on top takes Esc itself.
	p6c_keydown(e) {
		if (e.key !== "Escape" || e.defaultPrevented || !this.p6c || this.drag) return;
		if (!this.p6c.moving && !this.p6c.highlight) return;
		// A toolbar menu or the phone's sheet closes on its own Esc; the highlight stays.
		if (e.target && e.target.closest && e.target.closest(".pk-menu, .pk-sheet")) return;
		if (!this.p6c_here()) return;
		const kit = this.p6a && this.p6a.kit;
		if (kit && kit.guard && kit.guard.top()) return;
		if (window.cur_dialog && window.cur_dialog.display) return;
		const tag = (e.target && e.target.tagName) || "";
		if (/^(INPUT|SELECT|TEXTAREA)$/.test(tag)) return;
		if (this.p6c.moving) this.p6c_disarm();
		else this.p6c_clear_highlight();
	},

	// ------------------------------------------------------------------ the team strip

	// One day of the people the group filter shows, from the payload (no request).
	p6c_team_sum(ymd) {
		const kit = this.p6c_kit();
		if (!kit) return null;
		return kit.big_picture.team_day(this.resources().map((resource) => this.day_of(resource.name, ymd)));
	},

	p6c_team_label() {
		return this.group ? __("{0} crew", [__(this.group)]) : __("Team");
	},

	p6c_strip_cells() {
		const kit = this.p6c_kit();
		if (!kit) return [];
		const bp = kit.big_picture;
		const label = this.p6c_team_label();
		return this.range_days().map((ymd) => {
			const sum = this.p6c_team_sum(ymd);
			const weekday = (kit.format.day_label(ymd) || "").split(",")[0];
			const tone = { off: "pp-offday", red: "pp-red", amber: "pp-amber", green: "pp-green" }[sum.level] || "";
			return {
				ymd,
				weekday,
				sum,
				tone,
				text: bp.team_text(sum),
				tip: `${bp.team_tip(label, weekday, sum)}\n${__("Click to see everyone's day")}`,
			};
		});
	},

	p6c_strip_cell_html(cell) {
		const bar = cell.sum.capacity > 0 ? this.bar_html({ ratio: Math.min(1, cell.sum.ratio), soft_ratio: cell.sum.soft_ratio }) : "";
		return `
			<div class="pp-p6c-sday ${cell.tone}" role="button" tabindex="0" data-pp6a-day="${pp_esc(cell.ymd)}"
				title="${pp_esc(cell.tip)}" aria-label="${pp_esc(cell.tip.split("\n")[0])}">
				<span class="pp-p6c-slabel">${pp_esc(cell.weekday)}</span>
				<span class="pp-p6c-stext">${pp_esc(cell.text)}</span>${bar}
			</div>`;
	},

	p6c_render_strip() {
		this.$grid_wrap.find(".pp-p6c-strip, .pp-crew > .pp-p6c-sday, .pp-crew > .pp-p6c-scorner").remove();
		if (this.view !== "week" && this.view !== "crew") return;
		const cells = this.p6c_strip_cells();
		if (!cells.length || !this.resources().length) return;
		const html = cells.map((cell) => this.p6c_strip_cell_html(cell)).join("");
		if (this.view === "week") {
			const $strip = $('<div class="pp-p6c-strip" role="group"></div>').attr("aria-label", __("Team capacity"));
			$strip[0].innerHTML = html;
			this.$grid_wrap.prepend($strip);
			return;
		}
		const heads = this.$grid_wrap[0].querySelectorAll(".pp-crew > .pp-crew-head");
		if (!heads.length) return;
		const corner = `<div class="pp-p6c-scorner"><b>${pp_esc(this.p6c_team_label())}</b><span>${pp_esc(
			__("free hours")
		)}</span></div>`;
		heads[heads.length - 1].insertAdjacentHTML("afterend", corner + html);
	},

	// ------------------------------------------------------------------ tap to move

	p6c_coarse() {
		try {
			return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
		} catch (e) {
			return false;
		}
	},

	// This device's choice (a tablet stays a tablet), else on for a touch screen.
	p6c_tap_default() {
		const saved = this.load_pref(PP6C.tap_pref, "");
		if (saved === "1") return true;
		if (saved === "0") return false;
		return this.p6c_coarse();
	},

	p6c_set_tap(on) {
		this.p6c.tap = !!on;
		this.save_pref(PP6C.tap_pref, on ? "1" : "0");
		if (!on) this.p6c_disarm();
		this.p6c_sync_controls();
		frappe.show_alert(
			{
				message: on
					? __("Tap to move: tap a card, then tap the day where it goes.")
					: __("Tap to move is off: tap a card to open it, drag it to move it."),
				indicator: "blue",
			},
			5
		);
	},

	// Where a tap puts the card: a person's day in the crew view, or a day of the calendar.
	p6c_tap_target(el) {
		const cell = el.closest(".pp-cell[data-date][data-resource]");
		if (cell) return { el: cell, date: cell.getAttribute("data-date"), resource: cell.getAttribute("data-resource") };
		const day = el.closest(".pp-day[data-date]");
		if (day) return { el: day, date: day.getAttribute("data-date") };
		return null;
	},

	p6c_tap_click(e) {
		const p6c = this.p6c;
		if (!p6c || !this.data || (!p6c.tap && !p6c.moving)) return;
		const target = e.target;
		if (!target || !target.closest || !this.$body[0].contains(target)) return;
		if (e.button > 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
		if (!PP.views.includes(this.view) || Date.now() < this.click_blocked_until) return;
		if (target.closest(".pp-p6c-movebar, button, a, input, select, textarea, label, .pp-route, [data-pk-person], [data-pp6a-day]")) {
			return;
		}
		const take = () => {
			e.preventDefault();
			e.stopPropagation();
		};
		const moving = p6c.moving;
		if (!moving) {
			const source = this.drag_source(target);
			if (!source || (source.kind !== "card" && source.kind !== "overdue")) return;
			take();
			this.p6c_arm(source);
			return;
		}
		const card_el = target.closest(".pp-card[data-task], .pp-od-card[data-od-task]");
		if (card_el && this.p6c_is_moving_el(card_el)) {
			take();
			this.p6c_disarm();
			return;
		}
		const place = this.p6c_tap_target(target);
		if (!place) {
			// Another card outside the calendar (a tray, the Overdue list): pick that one instead.
			const other = p6c.tap ? this.drag_source(target) : null;
			if (other && (other.kind === "card" || other.kind === "overdue")) {
				take();
				this.p6c_arm(other);
			}
			return;
		}
		take();
		this.p6c_tap_drop(place);
	},

	// The move goes through the drag's own path: drop -> plan_drop -> commit -> send (reason, Undo).
	p6c_tap_drop(place) {
		const moving = this.p6c.moving;
		if (!moving) return;
		this.p6c_disarm();
		this.drop(moving.source, place);
	},

	p6c_arm(source) {
		if (!source) return;
		const label =
			source.kind === "overdue"
				? (source.row && (source.row.subject || source.row.name)) || ""
				: (source.card && (source.card.subject || source.card.name)) || "";
		this.p6c.moving = {
			source,
			label,
			task: source.kind === "overdue" ? source.row.name : source.card.name,
			date: source.from_date || "",
			resource: source.from_resource || "",
		};
		this.p6c_render_movebar();
		this.p6c_mark_moving();
	},

	p6c_disarm() {
		if (!this.p6c || !this.p6c.moving) return;
		this.p6c.moving = null;
		this.p6c_render_movebar();
		this.p6c_mark_moving();
	},

	p6c_is_moving_el(el) {
		const moving = this.p6c.moving;
		if (!moving || !el) return false;
		if (moving.source.kind === "overdue") return el.getAttribute("data-od-task") === moving.task;
		return (
			el.getAttribute("data-task") === moving.task &&
			(el.getAttribute("data-date") || "") === moving.date &&
			(el.getAttribute("data-resource") || "") === moving.resource
		);
	},

	p6c_mark_moving() {
		const root = this.$body[0];
		root.classList.toggle("pp-p6c-armed", !!(this.p6c && this.p6c.moving));
		root.querySelectorAll(".pp-p6c-moving").forEach((el) => el.classList.remove("pp-p6c-moving"));
		if (!this.p6c || !this.p6c.moving) return;
		root.querySelectorAll(".pp-card[data-task], .pp-od-card[data-od-task]").forEach((el) => {
			if (this.p6c_is_moving_el(el)) el.classList.add("pp-p6c-moving");
		});
	},

	p6c_render_movebar() {
		const $movebar = this.$p6c_movebar;
		if (!$movebar) return;
		const moving = this.p6c.moving;
		if (!moving) {
			$movebar.hide().empty();
			return;
		}
		$movebar.empty().show();
		const $text = $('<span class="pp-p6c-movebar-text"></span>').appendTo($movebar);
		$("<b></b>").text(__("Moving: {0}", [moving.label])).appendTo($text);
		$('<span class="pp-p6c-movebar-hint"></span>')
			.text(
				this.view === "crew"
					? __("Tap a day on a person's row to put it there.")
					: __("Tap a day to put it there; it keeps its length.")
			)
			.appendTo($text);
		const card = moving.source.kind === "card" ? moving.source.card : null;
		if (card) {
			$('<button type="button" class="btn btn-default btn-sm"></button>')
				.text(__("Open"))
				.on("click", () => {
					this.p6c_disarm();
					const fresh = this.by_task[card.name] || card;
					this.open_card(fresh);
				})
				.appendTo($movebar);
		}
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Cancel"))
			.on("click", () => this.p6c_disarm())
			.appendTo($movebar);
	},

	// ------------------------------------------------------------------ saved views

	p6c_fetch_views() {
		const p6c = this.p6c;
		if (p6c.loading) return p6c.loading;
		const call = Promise.resolve(
			frappe.call({ method: `${PP6C.prefs}.get_views`, args: { planner: PP6C.planner } })
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				p6c.views = Array.isArray(answer.views) ? answer.views : [];
				p6c.last = answer.last && typeof answer.last === "object" ? answer.last : null;
			})
			.catch(() => null);
		// A slow answer must not keep the planner blank: after a few seconds it opens without it.
		const late = new Promise((resolve) => setTimeout(resolve, PP6C.restore_wait));
		p6c.loading = Promise.race([call, late]).then(() => {
			p6c.loaded = true;
		});
		return p6c.loading;
	},

	// Called first thing in handle_route. True: it has taken this route over (it is waiting for the
	// saved views, or it has replaced a bare /project-planner with the saved view's route) and
	// handle_route runs again when the route lands. The URL always wins: a route that names a view is
	// used as it is, and only the filters are restored on the first open.
	p6c_before_route() {
		const p6c = this.p6c;
		if (!p6c) return false;
		const route = frappe.get_route() || [];
		if (route[0] !== PP.route) return false;
		if (!p6c.loaded) {
			if (!p6c.waiting) {
				p6c.waiting = true;
				this.p6c_fetch_views().then(() => {
					p6c.waiting = false;
					this.handle_route();
				});
			}
			return true;
		}
		const last = p6c.last ? this.p6c_pick(p6c.last) : null;
		// The server never remembers these; nor does the page, whatever an older row says.
		if (last) PP6C.not_remembered.forEach((key) => delete last[key]);
		// "Task color" saved before it was a choice is the old default, not a wish: Job type instead.
		if (last && last.color_by === "default" && !last.color_chosen) delete last.color_by;
		if (!p6c.restored) {
			p6c.restored = true;
			if (last) {
				this.p6c_apply_state(last, { render: false });
				p6c.saved_text = JSON.stringify(last);
			}
		}
		// A "last used" view saved before P7 may say heatmap: Capacity is the Crew tab's second screen.
		const view = last && last.view && (PP.capacity_aliases[last.view] || last.view);
		if (!route[1] && view && view !== PP6C.default_view && PP6C.schema.view[1].includes(view)) {
			// Replace, never push: Back from here leaves the planner, as it would have from the bare URL.
			frappe.route_flags = frappe.route_flags || {};
			frappe.route_flags.replace_route = true;
			if (view === PP.capacity_view) frappe.set_route(PP.route, "crew", frappe.datetime.get_today(), { capacity: "1" });
			else frappe.set_route(PP.route, view, frappe.datetime.get_today());
			return true;
		}
		return false;
	},

	// The page's state as a view. `remembered`: as "last used" (without the Running over chip).
	p6c_state(remembered) {
		const views = PP6C.schema.view[1];
		const state = {
			project: this.project || "",
			pm: this.pm || "",
			group: this.group || "",
			foreign: !!this.show_foreign,
			all_tasks: !!this.all_tasks,
			panel: !!this.panel_open,
			color_by: this.p6c.color_by,
			color_chosen: !!this.p6c.color_chosen,
		};
		if (views.includes(this.view)) state.view = this.view;
		if (!remembered) state.over_only = !!this.over_only;
		return this.p6c_pick(state);
	},

	// Apply a view: filters, colors, and (with `route`) its calendar view as a normal route change.
	p6c_apply_state(state, opts) {
		opts = opts || {};
		const view = this.p6c_pick(state);
		if ("project" in view) this.project = view.project;
		if ("pm" in view) this.pm = view.pm;
		if ("group" in view) this.group = view.group;
		if ("foreign" in view) this.show_foreign = !!view.foreign;
		// A view that does not say (one saved before P2) is the default, phase and crew work only.
		const all_tasks = "all_tasks" in view ? (view.all_tasks ? "1" : "") : "";
		const reload = all_tasks !== (this.all_tasks ? "1" : "");
		this.all_tasks = all_tasks;
		if ("panel" in view) this.panel_open = !!view.panel;
		if ("over_only" in view) this.over_only = !!view.over_only;
		if ("color_by" in view) {
			this.p6c.color_by = view.color_by;
			// A view saved before the flag existed and holding Task color is a snapshot someone took, so a
			// choice; "last used" views without the flag were dropped of it in p6c_before_route.
			this.p6c.color_chosen = "color_chosen" in view ? !!view.color_chosen : view.color_by === "default";
		}
		if (this.data) {
			// A project or PM no longer on the planner would filter out everything.
			if (this.project && !this.by_project[this.project]) this.project = "";
			if (this.pm && !(this.data.projects || []).some((project) => project.pm === this.pm)) this.pm = "";
		}
		this.show_all_unscheduled = false;
		// A view of their own (restored or picked) is the person's filters, not an address's (S5c).
		this.url_filters = null;
		this.own_filters = this.filters();
		this.p6c_sync_controls();
		if (opts.route && view.view && (PP.capacity_aliases[view.view] || view.view) !== this.view) {
			this.go(view.view, this.anchor);
			return;
		}
		if (opts.render === false) return;
		this.write_filter_url();
		if (!this.data) return;
		// A view that shows another set of tasks asks the server again (a week that is not on screen yet
		// loads anyway).
		if (reload && PP.views.includes(this.view)) this.load();
		else this.render();
	},

	p6c_state_soon() {
		const p6c = this.p6c;
		if (!p6c || !p6c.restored) return;
		clearTimeout(p6c.save_timer);
		p6c.save_timer = setTimeout(() => this.p6c_save_last(), PP6C.save_delay);
	},

	p6c_save_last() {
		const p6c = this.p6c;
		if (!PP6C.schema.view[1].includes(this.view)) return;
		const state = this.p6c_state(true);
		// Filters the address gave (S5b's form link, S5c's any link) are that visit's, not "last used":
		// each keeps the person's own value until they pick it themselves (pick_filter).
		Object.keys(this.url_filters || {}).forEach((key) => {
			if (key in state) state[key] = this.own_filter_values()[key];
		});
		// The address's filters are text; a view's all_tasks is a flag.
		if ("all_tasks" in state) state.all_tasks = !!state.all_tasks;
		const text = JSON.stringify(state);
		if (text === p6c.saved_text) return;
		p6c.saved_text = text;
		Promise.resolve(
			frappe.call({ method: `${PP6C.prefs}.save_last_view`, args: { planner: PP6C.planner, data: text } })
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				p6c.last = answer.last && typeof answer.last === "object" ? answer.last : state;
			})
			.catch(() => {
				p6c.saved_text = null;
			});
	},

	// The saved view the page shows right now, if any.
	p6c_matching_view() {
		// Whether the color was picked is no part of what a view shows, so views saved without it still match.
		const key = (state) => {
			const view = Object.assign({}, state);
			delete view.color_chosen;
			// A view saved as Heatmap before P7 is Capacity.
			if (view.view) view.view = PP.capacity_aliases[view.view] || view.view;
			// A view saved before P2 does not say, which is off.
			if (!view.all_tasks) delete view.all_tasks;
			return JSON.stringify(Object.keys(view).sort().map((name) => [name, view[name]]));
		};
		const now = key(this.p6c_state(false));
		return (this.p6c.views || []).find((entry) => key(this.p6c_pick(entry.data || {})) === now) || null;
	},

	// The saved views of More ▸ Views: one radio item each (the one on screen is ticked), then Save and
	// Manage.
	p6c_view_items() {
		if (!this.p6c) return [];
		const current = this.p6c_matching_view();
		const items = [{ heading: __("Saved views") }];
		(this.p6c.views || []).forEach((entry) =>
			items.push({
				label: entry.name,
				radio: true,
				checked: !!current && current.name === entry.name,
				hint: this.p6c_view_summary(entry.data || {}),
				on_click: () => this.p6c_use_view(entry),
			})
		);
		if (!this.p6c.loaded) items.push({ label: __("Loading your views…"), info: true });
		else if (!(this.p6c.views || []).length) items.push({ label: __("No saved views yet"), info: true });
		items.push({ divider: true });
		items.push({ label: __("Save current view as…"), on_click: () => this.p6c_save_as() });
		if ((this.p6c.views || []).length) items.push({ label: __("Manage views…"), on_click: () => this.p6c_manage_views() });
		return items;
	},

	p6c_use_view(entry) {
		if (!entry) return;
		this.p6c_apply_state(entry.data || {}, { route: true });
		frappe.show_alert({ message: __("View: {0}", [pp_esc(entry.name)]), indicator: "green" }, 4);
	},

	// "Crew · Field · Job type colors": what a saved view holds, in a few words.
	p6c_view_summary(view) {
		const parts = [];
		view = this.p6c_pick(view || {});
		if (view.view) parts.push(__(PP6C.view_labels[view.view] || view.view));
		if (view.group) parts.push(__(view.group));
		if (view.project) parts.push(this.p6c_project_label(view.project));
		if (view.pm) parts.push(view.pm);
		if (view.over_only) parts.push(__("Running over"));
		if (view.all_tasks) parts.push(__("All tasks"));
		if (view.foreign === false) parts.push(__("No maintenance, rentals, travel"));
		if (view.color_by && view.color_by !== PP6C.default_color) {
			parts.push(view.color_by === "default" ? __("Task colors") : __("{0} colors", [this.p6c_mode_label(view.color_by)]));
		}
		return parts.join(" · ");
	},

	p6c_save_as() {
		const current = this.p6c_matching_view();
		frappe.prompt(
			[
				{
					fieldtype: "Data",
					fieldname: "name",
					label: __("Name"),
					reqd: 1,
					default: current ? current.name : "",
					description: __("For example: Field crew, Build only. Only you see your views."),
				},
			],
			(values) => {
				const name = String((values && values.name) || "")
					.replace(/\s+/g, " ")
					.trim()
					.slice(0, 60);
				if (!name) return;
				const exists = (this.p6c.views || []).some((entry) => entry.name.toLowerCase() === name.toLowerCase());
				const run = () => this.p6c_store_view(name);
				if (exists) frappe.confirm(__("Replace your saved view {0}?", [pp_esc(name)]), run);
				else run();
			},
			__("Save view as"),
			__("Save")
		);
	},

	p6c_store_view(name) {
		return Promise.resolve(
			frappe.call({
				method: `${PP6C.prefs}.save_view`,
				args: { planner: PP6C.planner, name, data: JSON.stringify(this.p6c_state(false)) },
			})
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				if (Array.isArray(answer.views)) this.p6c.views = answer.views;
				frappe.show_alert({ message: __("Saved view {0}", [pp_esc(answer.saved || name)]), indicator: "green" }, 5);
			})
			.catch(() => null);
	},

	p6c_views_html() {
		const views = this.p6c.views || [];
		if (!views.length) return `<p class="pk-empty">${pp_esc(__("No saved views yet. Save one from More ▸ Views."))}</p>`;
		const rows = views.map(
			(entry) => `
			<li class="pp-p6c-views-row">
				<div class="pp-p6c-views-main">
					<div class="pp-p6c-views-name">${pp_esc(entry.name)}</div>
					<div class="pp-p6c-views-sub">${pp_esc(this.p6c_view_summary(entry.data || {}))}</div>
				</div>
				<button type="button" class="btn btn-default btn-xs" data-p6c-use="${pp_esc(entry.name)}">${pp_esc(__("Use"))}</button>
				<button type="button" class="btn btn-default btn-xs" data-p6c-delete="${pp_esc(entry.name)}">${pp_esc(__("Delete"))}</button>
			</li>`
		);
		return `<ul class="pp-p6c-views-list">${rows.join("")}</ul><p class="pk-note">${pp_esc(
			__("Your views are saved for you on the server, so they follow you to any device. Nobody else sees them.")
		)}</p>`;
	},

	p6c_manage_views() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return;
		kit.drawer.open({
			title: __("Saved views"),
			body: this.p6c_views_html(),
			width: 420,
			key: "p6c-views",
			owner: PP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6c_manage_views(),
			on_click: (e, handle) => {
				const el = e.target && e.target.closest ? e.target.closest("[data-p6c-use], [data-p6c-delete]") : null;
				if (!el) return;
				const use = el.getAttribute("data-p6c-use");
				const name = use || el.getAttribute("data-p6c-delete");
				const entry = (this.p6c.views || []).find((item) => item.name === name);
				if (!entry) return;
				if (use) {
					kit.drawer.navigate(() => this.p6c_use_view(entry));
					return;
				}
				frappe.confirm(__("Delete your saved view {0}? This cannot be undone.", [pp_esc(entry.name)]), () =>
					Promise.resolve(
						frappe.call({ method: `${PP6C.prefs}.delete_view`, args: { planner: PP6C.planner, name: entry.name } })
					)
						.then((r) => {
							const answer = (r && r.message) || {};
							if (Array.isArray(answer.views)) this.p6c.views = answer.views;
							handle.set_body(this.p6c_views_html());
						})
						.catch(() => null)
				);
			},
		});
	},

	// ------------------------------------------------------------------ print

	// 6B's multi-selection (task names), read defensively: empty when 6B is not there.
	p6c_selection() {
		const selection = this.p6_selection;
		return selection && typeof selection.has === "function" && selection.size ? selection : new Set();
	},

	// The More ▸ Print items: the view on screen, and 6B's selection when there is one. Each runs
	// in the menu item's click, so its window opens on the person's own tap.
	p6c_print_items() {
		const selected = this.p6c_selection();
		const items = [
			{
				label: __("Print this view"),
				title: __("Print exactly what is on screen, with your filters and colors"),
				on_click: () => this.p6c_print(false),
			},
		];
		if (selected.size) items.push({ label: __("Print selection only ({0})", [selected.size]), on_click: () => this.p6c_print(true) });
		return items;
	},

	// The window opens on the click itself (one opened after a request is a blocked pop-up) and fills
	// in when the print design system's chrome arrives; the body is built from the data now.
	p6c_print(selection_only) {
		const kit = this.p6c_kit();
		if (!kit || (!this.data && this.view !== PP.capacity_view)) {
			frappe.show_alert({ message: __("One moment: the planner is still loading."), indicator: "blue" }, 4);
			return;
		}
		if (!PP6C.schema.view[1].includes(this.view)) {
			frappe.msgprint(__("Open the week, month, crew or capacity view to print it."));
			return;
		}
		const win = window.open("", "_blank");
		if (!win) {
			frappe.msgprint(__("Allow pop-ups for this site to print the planner."));
			return;
		}
		win.document.write(
			`<!doctype html><title>${pp_esc(__("Print"))}</title><p style="font-family:sans-serif">${pp_esc(
				__("Preparing the page…")
			)}</p>`
		);
		const model = this.p6c_print_model(!!selection_only);
		Promise.resolve(
			frappe.call({
				method: `${PP6C.prefs}.get_print_chrome`,
				args: { planner: PP6C.planner, title: model.heading, lines: JSON.stringify(model.lines) },
			})
		)
			.then((r) => (r && r.message) || null)
			.catch(() => null)
			.then((chrome) => {
				if (win.closed) return;
				const html = kit.big_picture.print_document(
					Object.assign({}, model, { chrome: chrome && typeof chrome.open === "string" ? chrome : null })
				);
				win.document.open();
				win.document.write(html);
				win.document.close();
				win.focus();
				const fonts = win.document.fonts && win.document.fonts.ready ? win.document.fonts.ready : Promise.resolve();
				fonts.then(() => win.setTimeout(() => win.print(), 250));
			});
	},

	p6c_day_label(ymd, short) {
		const kit = this.p6c_kit();
		const label = kit ? kit.format.day_label(ymd) : ymd;
		return short ? label.split(", ").slice(1).join(", ") || label : label;
	},

	// Whole days from `a` to `b` ("YYYY-MM-DD"), without the Desk's moment (the print model runs in tests).
	p6c_days_between(a, b) {
		const day = (ymd) => Date.UTC(Number(ymd.slice(0, 4)), Number(ymd.slice(5, 7)) - 1, Number(ymd.slice(8, 10)));
		return Math.round((day(b) - day(a)) / 86400000);
	},

	// The color a printed card carries: the current mode's, else the card's own edge as on screen.
	p6c_print_color(card, row) {
		const color = this.p6c_card_color(card, row);
		if (color) return color;
		// Overdue is the danger edge, the token .pp-card.pp-overdue paints on screen.
		if (card.overdue) return pp_token_color("--sf-red", "#bd2e2b");
		return pp_color(card.color, PP.default_color);
	},

	p6c_print_item(card, ymd, row) {
		const hi = this.p6c.highlight;
		const marks = [];
		if (card.tentative) marks.push(__("Pencil"));
		if (card.overdue) marks.push({ text: __("Overdue"), tone: "red" });
		if (this.task_conflicts(card).length) marks.push({ text: __("Conflict"), tone: "red" });
		if (card.drafted) marks.push(__("Draft"));
		if (ymd && card.start && card.end && card.end > card.start) {
			marks.push(
				__("Day {0} of {1}", [this.p6c_days_between(card.start, ymd) + 1, this.p6c_days_between(card.start, card.end) + 1])
			);
		}
		const crew = (card.crew || []).map((member) => this.resource_label(member.resource, member.label)).join(", ");
		return {
			title: card.subject || card.name,
			sub: [row ? "" : card.project_title || card.project, this.hours_text(card), row ? "" : crew].filter(Boolean).join(" · "),
			color: this.p6c_print_color(card, row),
			dashed: !!card.tentative,
			marks,
			faded: !!(hi && card.project !== hi.key) || !!(row && !this.task_visible(card)),
			highlight: !!(hi && card.project === hi.key),
		};
	},

	p6c_foreign_item(label, sub) {
		return { title: label, sub, color: PP6C.none_color, dashed: true, faded: !!this.p6c.highlight };
	},

	p6c_print_lines(selection_only, count) {
		const lines = [];
		if (this.view === PP.capacity_view) {
			// Capacity answers to the group filter only.
			if (this.group) lines.push(__("Group: {0}", [__(this.group)]));
			return lines;
		}
		if (this.project) lines.push(__("Project: {0}", [this.p6c_project_label(this.project)]));
		if (this.pm) lines.push(__("Project manager: {0}", [this.pm]));
		if (this.group) lines.push(__("Group: {0}", [__(this.group)]));
		if (this.over_only) lines.push(__("Only tasks running over"));
		if (!this.all_tasks && this.data && this.data.hidden_tasks) {
			lines.push(__("Phase and crew work only ({0} tasks not shown)", [this.data.hidden_tasks]));
		}
		if (!this.show_foreign && this.view !== PP.capacity_view) lines.push(__("Maintenance, rentals and travel hidden"));
		if (this.p6c.color_by !== "default") lines.push(__("Colored by {0}", [this.p6c_mode_label(this.p6c.color_by)]));
		if (this.p6c.highlight) lines.push(__("Highlighting {0}", [this.p6c.highlight.label]));
		if (selection_only) lines.push(__("Selected tasks only ({0})", [count]));
		if (this.draft_on || Number((this.data && this.data.drafts && this.data.drafts.count) || 0)) {
			lines.push(__("With unpublished drafts"));
		}
		return lines;
	},

	// Exactly what is on screen, as a model for planner_kit.big_picture.print_document.
	p6c_print_model(selection_only) {
		const view = this.view;
		const heading = `${__(PP6C.view_labels[view] || view)} · ${this.title()}`;
		const legend = this.p6c_legend_entries().map((entry) => ({ color: entry.color, label: entry.label }));
		const model = {
			doc_title: `${__("Project Planner")} · ${heading}`,
			heading,
			legend_title: __("Colors: {0}", [this.p6c_mode_label(this.p6c.color_by)]),
			legend,
			notes: [],
		};
		const selected = this.p6c_selection();
		if (selection_only && selected.size && this.data) {
			const cards = [].concat(this.data.tasks || [], this.data.unscheduled || []).filter((card) => selected.has(card.name));
			cards.sort((a, b) => String(a.start || "9999").localeCompare(String(b.start || "9999")) || this.compare(a, b));
			model.layout = "list";
			model.columns = [__("Dates"), __("Task"), __("Project"), __("Crew"), __("Hours"), __("Notes")];
			model.rows = cards.map((card) => {
				const item = this.p6c_print_item(card, null, null);
				const when = card.start
					? card.end && card.end !== card.start
						? `${this.p6c_day_label(card.start)} – ${this.p6c_day_label(card.end)}`
						: this.p6c_day_label(card.start)
					: __("Not scheduled");
				return {
					color: item.color,
					cols: [
						when,
						card.subject || card.name,
						card.project_title || card.project || "",
						(card.crew || []).map((member) => this.resource_label(member.resource, member.label)).join(", "),
						this.hours_text(card),
						item.marks.map((mark) => (typeof mark === "string" ? mark : mark.text)).join(", "),
					],
				};
			});
			model.lines = [__("Selected tasks")].concat(this.p6c_print_lines(true, cards.length));
			model.empty = __("None of the selected tasks is on screen.");
			return model;
		}
		model.lines = this.p6c_print_lines(false, 0);
		if (view === PP.capacity_view) return Object.assign(model, this.p6c_print_heatmap());
		if (view === "crew") return Object.assign(model, this.p6c_print_crew());
		return Object.assign(model, this.p6c_print_calendar());
	},

	p6c_print_calendar() {
		const days = this.range_days();
		const people = this.resources();
		const visible_people = new Set(people.map((resource) => resource.name));
		const month = this.view === "month" ? String(this.anchor).slice(0, 7) : "";
		const rows = [];
		for (let index = 0; index < days.length; index += 7) {
			const cells = days.slice(index, index + 7).map((ymd) => {
				const cards = (this.data.tasks || [])
					.filter((card) => this.task_visible(card) && this.covered(card, [ymd]).length)
					.sort((a, b) => this.compare(a, b));
				const items = cards.map((card) => this.p6c_print_item(card, ymd, null));
				if (this.show_foreign) {
					(this.data.foreign || []).forEach((item) => {
						if (item.date !== ymd || (item.ref && this.by_task[item.ref])) return;
						if (item.resource && !visible_people.has(item.resource)) return;
						const who = item.resource ? this.resource_label(item.resource) : "";
						const hours = item.hours ? `${pp_hours(item.hours)}h` : "";
						const kind = { visit: __("Maintenance"), rental: __("Rental"), travel: __("Travel") }[item.kind] || "";
						items.push(this.p6c_foreign_item(item.label || item.ref, [kind, who, hours].filter(Boolean).join(" · ")));
					});
				}
				const sum = this.p6c_team_sum(ymd);
				const kit = this.p6c_kit();
				return {
					head: this.view === "week" ? this.p6c_day_label(ymd) : this.p6c_day_label(ymd, true),
					sub: people.length && sum && kit ? kit.big_picture.team_text(sum) : "",
					tone: month && ymd.slice(0, 7) !== month ? "out" : "",
					items,
				};
			});
			rows.push({ cells });
		}
		return {
			layout: "grid",
			label_column: false,
			columns: days.slice(0, 7).map((ymd) => this.p6c_day_label(ymd).split(",")[0]),
			rows,
		};
	},

	p6c_print_crew() {
		const days = this.range_days();
		const rows = this.resources().map((resource) => ({
			label: resource.label || resource.name,
			sub: resource.group ? __(resource.group) : "",
			cells: days.map((ymd) => {
				const day = this.day_of(resource.name, ymd);
				const state = this.avail_state(day);
				const items = [];
				((day && day.bookings) || []).forEach((booking) => {
					if (booking.kind === "drive") return;
					const card = booking.ref && this.by_task[booking.ref];
					if (card) {
						items.push(this.p6c_print_item(card, null, resource.name));
						return;
					}
					if (!this.show_foreign) return;
					const length = booking.slot ? booking.slot.join("–") : `${pp_hours(booking.hours)}h`;
					items.push(this.p6c_foreign_item(booking.label || booking.ref, length));
				});
				return { sub: state.text, tone: String(state.cls).includes("pp-offday") ? "off" : "", items };
			}),
		}));
		const kit = this.p6c_kit();
		if (kit && rows.length) {
			rows.unshift({
				label: this.p6c_team_label(),
				sub: __("free hours"),
				cells: days.map((ymd) => ({ head: kit.big_picture.team_text(this.p6c_team_sum(ymd)) })),
			});
		}
		return {
			layout: "grid",
			label_column: true,
			columns: [__("Person")].concat(days.map((ymd) => this.p6c_day_label(ymd))),
			rows,
			empty: __("No planner resources to show."),
		};
	},

	p6c_print_heatmap() {
		const heat = this.heatmap;
		if (!heat) return { layout: "grid", columns: [], rows: [], empty: __("The heatmap could not be loaded.") };
		const rows = heat.rows
			.filter((row) => !this.group || !row.group || row.group === this.group)
			.map((row) => ({
				label: row.label,
				sub: row.group ? __(row.group) : "",
				cells: heat.weeks.map((week, index) => {
					const state = this.heat_state(row.by_start[week.start] || row.ordered[index] || {});
					return { head: state.pct, sub: state.sub, tone: String(state.cls).includes("pp-offday") ? "off" : "" };
				}),
			}));
		return {
			layout: "grid",
			label_column: true,
			columns: [__("Person")].concat(heat.weeks.map((week) => __("Week of {0}", [this.p6c_day_label(week.start, true)]))),
			rows,
			legend: [],
			empty: __("No planner resources to show."),
		};
	},
};

Object.assign(ProjectPlanner.prototype, PP6C_METHODS);

// ====================================================================== Phase 6D: Conflict center, personal blocks, day notes
//
// Nik, 2026-10-09, for both planners: one list of everything wrong right now with one-click fixes
// (the Conflict center); personal blocks ("unavailable 2–4 pm", "shop day") that planners put on
// anyone and each person on their own time, which others see as "Unavailable" and planners with the
// note; and day notes for the whole crew ("Shop meeting 7 am"). The backend is api/planner_blocks.py
// and api/planner_conflicts.py (Phase 6D); the drawer, the two forms and the markup are the planner
// kit's (planner_kit/conflicts.js and blocks.js), shared with the Maintenance Planner.
//
//   - the "N Conflicts" chip on the toolbar the Conflict center for the dates on screen. N is what
//     (and Conflicts… in More ▸ Tools)      nobody has kept; the chip shows only above 0 (PPTB), read
//                                            after each load (one call, debounced), never on a plain
//                                            re-render. A card in
//                                            a conflict has its Conflict chip turned into the way in
//                                            (a small marker when it had no chip): it opens the list
//                                            at that conflict.
//   - a fix in the list                     exactly the method and arguments the server worked out,
//                                            sent through commit()/send() like a drag, so a conflict
//                                            asks for a reason, Draft mode drafts it and Undo puts it
//                                            back. "Pick someone who's free" lists who_is_free and
//                                            picks nobody; "Keep it with a reason" needs a reason.
//   - Block time… (More ▸ Tools), "Block       the block form. A block shows in the crew view as a
//     time…" in a person's drawer and the    hatched "Unavailable 2–4 pm" bar, in week and month as a
//     right-click menu                       chip; its note only when block_notes has it. Blocks are
//                                            never dragged.
//   - a day's notes                         a thin row under each day head (week), a row under the
//                                            crew view's column heads, a small marker (month); "+ note"
//                                            for planners; the day drawer shows them in full on top
//   - My week                               your own blocks with their notes, the notes for your
//                                            group, and "Block my time" on each day (only your own)
//
// Hooks into the class above, one line each: init_phase6d (constructor), render_phase6d (render),
// p6d_state (avail_state), p6d_block_html (booking_html), p6d_day_html
// (render_calendar), p6d_crew_notes_row (render_crew), p6d_my_week_day (render_my_week), and in
// Phase 6A's block p6d_person_actions (p6a_open_person), p6d_day_peek_opts (p6a_open_day) and
// p6d_legend_sections (p6a_legend).

const PP6D = {
	planner: "project",
	width: 540,
	max_days: 60,
	unavailable: "Unavailable",
	// The Conflict center runs only these, each the page's own write sent through commit()/send().
	fix_methods: {
		[`${PP.api}.save_task`]: "save_task",
		[`${PP.api}.swap_crew`]: "swap_crew",
		[`${PP.api}.add_crew`]: "add_crew",
	},
	// A maintenance visit opens on the Maintenance Planner, which Projects Users cannot open.
	maintenance_roles: ["System Manager", "Projects Manager", "Maintenance Supervisor", "Maintenance User"],
	// What this block's capture listener answers: a block, a note, a conflict marker.
	targets: "[data-pk-block], [data-pk-note-open], [data-p6d-conflict]",
};

const PP6D_METHODS = {
	init_phase6d() {
		this.p6d = { center: null, counted: null, notes: {}, asked: {}, notes_for: null };
		// The right-click menu registry (phase6-wave2): 6B opens the menu, this adds its items.
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.p6d_menu_items(target));
		// The Conflicts count is an alert chip on the toolbar and Block time an item of its More ▸ Tools
		// (PPTB, v1.687.0): p6d_conflict_count and p6d_toolbar_block say when.
		const root = this.$body[0];
		// Capture phase: a block, a note or a conflict marker inside a card or a day is its own click,
		// not the card's (which opens the task) and not the day's.
		root.addEventListener("click", (e) => this.p6d_click(e), true);
		root.addEventListener(
			"keydown",
			(e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				const el = e.target && e.target.closest ? e.target.closest(PP6D.targets) : null;
				if (!el || e.target !== el) return;
				e.preventDefault();
				e.stopPropagation();
				el.click();
			},
			true
		);
		this.p6a_require(() => this.p6d_ready());
	},

	p6d_ready() {
		const kit = window.planner_kit;
		if (!kit || !kit.conflicts || !kit.blocks || (this.p6d && this.p6d.center)) return;
		this.p6d.center = kit.conflicts.center({
			planner: PP6D.planner,
			owner: PP6A.owner,
			push: this.$body[0],
			width: PP6D.width,
			range: () => this.p6d_range(),
			can_schedule: () => !!(this.data && this.data.can_schedule),
			draft: () => !!this.draft_on,
			block_note: (name) => this.p6d_block_note(name),
			run_fix: (job) => this.p6d_run_fix(job),
			open_item: (conflict, item) => this.p6d_open_item(conflict, item),
			exclude: (conflict, fix) => this.p6d_exclude(conflict, fix),
			pick_context: (conflict, fix) => this.p6d_pick_context(conflict, fix),
			on_list: () => this.p6d_after_list(),
		});
		// The calendar may have been drawn before the kit arrived: draw its blocks and notes now.
		if (this.data && PP.views.includes(this.view)) this.render();
	},

	p6d_kit() {
		return this.p6d && this.p6d.center ? window.planner_kit : null;
	},

	// ------------------------------------------------------------------ hooks

	render_phase6d() {
		if (!this.p6d || !this.data) return;
		if (this.p6d.notes_for !== this.data) {
			// Notes read for days off the screen are as old as the last load.
			this.p6d.notes = {};
			this.p6d.asked = {};
			this.p6d.notes_for = this.data;
		}
		this.p6d_decorate();
		// One Conflict center read per load (debounced), never one per render.
		const center = this.p6d.center;
		if (center && this.p6d.counted !== this.data) {
			this.p6d.counted = this.data;
			center.schedule();
		}
	},

	// The toolbar reads these two (PPTB); it offers neither off the calendar views (the route,
	// heatmap, Utilization and My week views have no Conflicts or Block time).
	//
	// What nobody has kept in the dates on screen, or null before the first count.
	p6d_conflict_count() {
		const center = this.p6d && this.p6d.center;
		const count = center ? center.count() : null;
		return count == null ? null : Number(count) || 0;
	},

	// Block time is the scheduling planners', on the calendar views.
	p6d_toolbar_block() {
		return !!(this.data && this.data.can_schedule && PP.views.includes(this.view));
	},

	// An all-day personal block makes the day read "Unavailable" rather than "Off".
	p6d_state(day, state) {
		if (!day || day.off !== PP6D.unavailable || Number(day.capacity) > 0) return;
		const booked = Number(day.booked) || 0;
		state.text = booked > 0.01 ? __("Unavailable, {0}h booked", [pp_hours(booked)]) : __("Unavailable");
	},

	// A block in the crew view: a hatched "Unavailable 2–4 pm" bar, never a card that drags.
	p6d_block_html(resource, ymd, booking) {
		const kit = this.p6d_kit();
		if (!kit) return "";
		return kit.blocks.chip_html(booking, {
			note: this.p6d_block_note(booking.ref),
			date: ymd,
			resource,
			person: resource,
			editable: this.p6d_can_edit_block(resource) && !booking.from_google,
		});
	},

	// Under a day's head in the week and month views: its notes, then everyone shown who is blocked.
	p6d_day_html(ymd, resources) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return "";
		const month = this.view === "month";
		const can = !!this.data.can_schedule;
		const parts = [
			kit.blocks.notes_html(this.p6d_notes_of(ymd), { date: ymd, compact: month, can_add: can && !month, project_link: true }),
		];
		(resources || []).forEach((person) => {
			this.p6d_blocks_of(person.name, ymd).forEach((booking) => {
				parts.push(
					kit.blocks.chip_html(booking, {
						who: month ? this.p6a_initials(person.name, person.label) : person.label,
						note: month ? "" : this.p6d_block_note(booking.ref),
						date: ymd,
						resource: person.name,
						person: person.name,
						compact: month,
						editable: this.p6d_can_edit_block(person.name) && !booking.from_google,
					})
				);
			});
		});
		return parts.join("");
	},

	// The crew view's notes row under its column heads; left out when there is nothing to show or add.
	p6d_crew_notes_row(days) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return "";
		const can = !!this.data.can_schedule;
		if (!can && !days.some((ymd) => this.p6d_notes_of(ymd).length)) return "";
		const cells = days.map(
			(ymd) =>
				`<div class="pp-p6d-crew-notes">${kit.blocks.notes_html(this.p6d_notes_of(ymd), {
					date: ymd,
					can_add: can,
					project_link: true,
				})}</div>`
		);
		return `<div class="pp-p6d-crew-notes pp-p6d-crew-corner">${pp_esc(__("Notes"))}</div>${cells.join("")}`;
	},

	// ------------------------------------------------------------------ what the page knows

	p6d_notes_of(ymd) {
		return ((this.data && this.data.day_notes) || {})[ymd] || [];
	},

	p6d_blocks_of(resource, ymd) {
		const day = resource ? this.day_of(resource, ymd) : null;
		return ((day && day.bookings) || []).filter((booking) => booking && booking.kind === "block" && booking.ref);
	},

	// A block's note: ONLY from the payload's block_notes, which the server fills for the viewer (the
	// planners read every note, a person their own). Every block this page draws reads it here.
	p6d_block_note(name) {
		const notes = (this.data && this.data.block_notes) || {};
		return name && notes[name] ? String(notes[name]) : "";
	},

	p6d_block_of(booking, resource, ymd) {
		return { name: booking.ref, date: ymd, resource, all_day: !!booking.all_day, slot: booking.slot || null };
	},

	p6d_own_resource() {
		const me = frappe.session && frappe.session.user;
		const own = ((this.data && this.data.resources) || []).find((person) => person.user && person.user === me);
		return own ? own.name : "";
	},

	// A planner edits anyone's block; everyone else only their own.
	p6d_can_edit_block(resource) {
		if (this.data && this.data.can_schedule) return true;
		return !!resource && resource === this.p6d_own_resource();
	},

	// Who may be offered "Block time…" for `resource` (none: the person themselves, if on the planner).
	p6d_can_block(resource) {
		if (this.data && this.data.can_schedule) return true;
		const own = this.p6d_own_resource();
		return !!own && (!resource || resource === own);
	},

	p6d_default_date() {
		const today = this.today();
		const { start, end } = this.range();
		return today >= pp_ymd(start) && today <= pp_ymd(end) ? today : pp_ymd(start);
	},

	p6d_reload() {
		if (this.view === PP3B.my_week) return this.load_my_week();
		if (PP.views.includes(this.view)) return this.load();
		return null;
	},

	// ------------------------------------------------------------------ clicks

	p6d_click(e) {
		if (!this.p6d || Date.now() < this.click_blocked_until) return;
		const target = e.target;
		if (!target || !target.closest) return;
		const take = () => {
			e.stopPropagation();
			e.preventDefault();
		};
		const marker = target.closest("[data-p6d-conflict]");
		if (marker) {
			take();
			this.p6d_open_conflicts({ key: marker.getAttribute("data-p6d-conflict") });
			return;
		}
		const add = target.closest("[data-pk-note-add]");
		if (add) {
			take();
			this.p6d_note_form({ date: add.getAttribute("data-pk-note-add") });
			return;
		}
		const note = target.closest("[data-pk-note-open]");
		if (note) {
			take();
			this.p6a_open_day(note.getAttribute("data-pk-note-open"));
			return;
		}
		const block = target.closest("[data-pk-block]");
		if (block) {
			take();
			this.p6d_block_click(block);
		}
	},

	p6d_block_click(el) {
		const kit = this.p6d_kit();
		const name = el.getAttribute("data-pk-block");
		const ymd = el.getAttribute("data-pk-block-date");
		if (!kit || !name) return;
		if (this.view === PP3B.my_week) {
			const mine = ((this.my_week_data && this.my_week_data.blocks) || []).find((block) => block && block.name === name);
			if (mine) this.p6d_my_week_block(mine.date, mine);
			return;
		}
		const resource = el.getAttribute("data-pk-block-resource");
		const booking = this.p6d_blocks_of(resource, ymd).find((entry) => entry.ref === name);
		if (!booking) return;
		// C4: a block read in from the Google vacations calendar changes in Google, never here.
		if (this.p6d_can_edit_block(resource) && !booking.from_google) {
			this.p6d_block_form({ block: this.p6d_block_of(booking, resource, ymd), resource, date: ymd });
			return;
		}
		const where = booking.from_google ? ` (${__("from the Google vacations calendar: change it there")})` : "";
		kit.toast(`${this.resource_label(resource)}: ${kit.blocks.block_text(booking)}${where}`, { tone: "info" });
	},

	// ------------------------------------------------------------------ blocks and notes

	// The block form: a planner picks anyone; everyone else blocks only their own time (the form then
	// sends no person at all, and the server makes it the caller's own).
	p6d_block_form(opts) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return;
		opts = opts || {};
		const can = !!this.data.can_schedule;
		const own = this.p6d_own_resource();
		const resource = opts.resource || (can ? "" : own);
		if (!can && resource && resource !== own) {
			frappe.show_alert({ message: __("You can only block your own time."), indicator: "orange" }, 6);
			return;
		}
		const people = (this.data.resources || [])
			.slice()
			.sort((a, b) => String(a.label).localeCompare(String(b.label)))
			.map((person) => ({ value: person.name, label: person.label }));
		kit.blocks.form({
			block: opts.block || null,
			note: opts.block ? this.p6d_block_note(opts.block.name) : "",
			date: opts.date || this.p6d_default_date(),
			resource,
			resource_label: resource ? this.resource_label(resource) : "",
			people: can ? people : null,
			self_only: !can,
			on_saved: () => this.p6d_reload(),
			on_deleted: () => this.p6d_reload(),
		});
	},

	p6d_note_form(opts) {
		const kit = this.p6d_kit();
		if (!kit || !this.data || !this.data.can_schedule) return;
		opts = opts || {};
		kit.blocks.note_form({
			note: opts.note || null,
			date: opts.date || this.p6d_default_date(),
			on_saved: () => this.load(),
			on_deleted: () => this.load(),
		});
	},

	// "Block time…" in a person's drawer (6A), for a planner or for the person themselves.
	p6d_person_actions(resource) {
		if (!this.data || !this.p6d_can_block(resource)) return [];
		return [
			{
				label: __("Block time…"),
				on_click: (info) => this.p6d_block_form({ resource: (info && info.resource) || resource, date: info && info.date }),
			},
		];
	},

	// The day drawer (6A): the day's notes in full at the top, Edit on each for a planner, "Add a note".
	p6d_day_peek_opts() {
		const can = !!(this.data && this.data.can_schedule);
		return {
			head_html: (ymd) => this.p6d_peek_head(ymd),
			on_head_click: (e, ymd) => this.p6d_peek_click(e, ymd),
			actions: can ? [{ label: __("Add a note"), on_click: (ymd) => this.p6d_note_form({ date: ymd }) }] : [],
		};
	},

	p6d_peek_head(ymd) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return "";
		const notes = this.p6d_peek_notes(ymd);
		return kit.blocks.note_list_html(notes || [], { loading: notes === null, project_link: true });
	},

	// The notes of a day on screen come with the planner; a day the drawer stepped past them is read
	// once (get_day_notes) and drawn when it arrives.
	p6d_peek_notes(ymd) {
		const { start, end } = this.range();
		if (ymd >= pp_ymd(start) && ymd <= pp_ymd(end)) return this.p6d_notes_of(ymd);
		const p6d = this.p6d;
		if (p6d.notes[ymd]) return p6d.notes[ymd];
		if (!p6d.asked[ymd]) {
			p6d.asked[ymd] = true;
			const kit = this.p6d_kit();
			if (!kit) return [];
			kit.blocks
				.get_notes(ymd)
				.then((notes) => {
					p6d.notes[ymd] = notes || [];
					const peek = this.p6a && this.p6a.peek;
					if (peek && peek.is_open() && typeof peek.render === "function" && peek.date && peek.date() === ymd) peek.render();
				})
				.catch(() => {
					p6d.notes[ymd] = [];
				});
		}
		return null;
	},

	p6d_peek_click(e, ymd) {
		const target = e.target;
		if (!target || !target.closest) return false;
		const edit = target.closest("[data-pk-note-edit]");
		if (edit) {
			const name = edit.getAttribute("data-pk-note-edit");
			const note = (this.p6d_peek_notes(ymd) || []).find((entry) => entry.name === name);
			if (note) this.p6d_note_form({ note, date: ymd });
			return true;
		}
		const project = target.closest("[data-pk-note-project]");
		if (project) {
			this.p6a_open_project(project.getAttribute("data-pk-note-project"));
			return true;
		}
		return false;
	},

	// ------------------------------------------------------------------ My week

	// The phone view: the person's own blocks with their notes, the notes for their group, and "Block
	// my time" on each day, which only ever blocks their own time.
	p6d_my_week_day($day, $dh, entry, data) {
		const kit = this.p6d_kit();
		if (!kit || !data || !data.resource || !entry) return;
		$('<button type="button" class="btn btn-default btn-xs pp-p6d-mw-btn"></button>')
			.text(__("Block my time"))
			.attr("title", __("Mark yourself unavailable for part of this day or all of it"))
			.on("click", () => this.p6d_my_week_block(entry.date, null))
			.appendTo($dh);
		const notes = (data.day_notes || {})[entry.date] || [];
		const mine = (data.blocks || []).filter((block) => block && block.date === entry.date);
		if (!notes.length && !mine.length) return;
		const $box = $('<div class="pp-p6d-mw"></div>');
		if (notes.length) $box.append(kit.blocks.notes_html(notes, { date: entry.date, full: true }));
		// get_my_week returns only the person's own blocks, with the note it read through block_notes.
		mine.forEach((block) =>
			$box.append(kit.blocks.chip_html(block, { note: block.note, date: entry.date, editable: !!block.can_edit }))
		);
		$box.insertAfter($dh);
		// An all-day block says so itself; the plain "Off" line under it would only repeat it.
		if (mine.some((block) => block.all_day) && !(entry.items || []).length && !entry.travel) {
			$day.children(".pp-mw-empty").remove();
		}
	},

	p6d_my_week_block(ymd, block) {
		const kit = this.p6d_kit();
		const data = this.my_week_data;
		if (!kit || !data || !data.resource) return;
		kit.blocks.form({
			block: block || null,
			note: block ? block.note : "",
			date: ymd,
			resource_label: data.label,
			self_only: true,
			can_delete: block ? !!block.can_edit : false,
			on_saved: () => this.load_my_week(),
			on_deleted: () => this.load_my_week(),
		});
	},

	// ------------------------------------------------------------------ the Conflict center

	p6d_open_conflicts(focus) {
		const center = this.p6d && this.p6d.center;
		if (center) center.open(focus || null);
	},

	p6d_range() {
		if (!this.data || !PP.views.includes(this.view)) return null;
		const { start, end } = this.range();
		const last = moment.min(end.clone(), start.clone().add(PP6D.max_days - 1, "days"));
		return { start: pp_ymd(start), end: pp_ymd(last), label: `${pp_when(pp_ymd(start))} – ${pp_when(pp_ymd(last))}` };
	},

	p6d_after_list() {
		this.tb_render();
		this.p6d_decorate();
	},

	// A fix from the Conflict center: exactly the method and arguments the server worked out, sent
	// through commit() (or send() for a task that is not on the board) like any drag, so the reason
	// prompt, Draft mode and Undo all apply. Nothing but this page's own writes may run from here.
	p6d_run_fix(job) {
		const method = PP6D.fix_methods[job.fix.method];
		if (!method || !job.args || !job.args.task) {
			frappe.show_alert({ message: __("That fix cannot run from this planner."), indicator: "orange" }, 6);
			return Promise.resolve(null);
		}
		const card = this.by_task[job.args.task] || null;
		const message = this.p6d_fix_message(job, card);
		if (card) return this.commit(card, method, job.args, message);
		return this.send(method, job.args, { message }).finally(() => this.load());
	},

	p6d_fix_message(job, card) {
		const fix = job.fix;
		const args = job.args;
		const item = (job.conflict.items || []).find((entry) => entry.name === args.task) || {};
		const subject = (card && (card.subject || card.name)) || item.title || args.task;
		const picked = (job.picked && job.picked.label) || "";
		if (fix.type === "next_free_day") return __("{0} moved to {1}", [subject, pp_when(args.start)]);
		if (fix.type === "pencil") return __("{0} is now pencil", [subject]);
		if (fix.type === "pick_free" && args.to_resource) {
			return __("{0}: {1} in place of {2}", [subject, this.resource_label(args.to_resource, picked), this.resource_label(args.from_resource)]);
		}
		if (fix.type === "pick_free") return __("{0} added to {1}", [this.resource_label(args.resource, picked), subject]);
		return __("{0} updated", [subject]);
	},

	// A record in a conflict: this planner's task opens in its side panel; anything else is a link.
	p6d_open_item(conflict, item) {
		const kit = this.p6d_kit();
		const go = (fn) => (kit ? kit.drawer.navigate(fn) : fn());
		if (item.own && item.doctype === "Task") {
			const card = this.by_task[item.name];
			if (card) this.open_card(card);
			else go(() => frappe.set_route("Form", "Task", item.name));
			return;
		}
		if (item.planner === "maintenance") {
			if (frappe.user && frappe.user.has_role && frappe.user.has_role(PP6D.maintenance_roles)) {
				go(() => frappe.set_route("maintenance-planner", "week", conflict.date));
			} else {
				frappe.show_alert({ message: __("A maintenance visit: it moves on the Maintenance Planner."), indicator: "blue" });
			}
		} else if (item.planner === "travel") {
			go(() => frappe.set_route("Form", "Travel Trip", item.name));
		} else if (item.doctype === "Task") {
			go(() => frappe.set_route("Form", "Task", item.name));
		}
	},

	// Who cannot be picked to take a task: whoever it is booked on now and everyone already on it.
	p6d_exclude(conflict, fix) {
		const out = {};
		const card = this.by_task[fix.name];
		((card && card.crew) || []).forEach((member) => {
			if (member && member.resource) out[member.resource] = __("Already on it");
		});
		if (fix.resource) out[fix.resource] = __("Booked on it now");
		return out;
	},

	p6d_pick_context(conflict, fix) {
		const card = this.by_task[fix.name];
		const item = (conflict.items || []).find((entry) => entry.name === fix.name) || {};
		const subject = (card && (card.subject || card.name)) || item.title || fix.name;
		if (fix.args && fix.args.from_resource) {
			return __("In place of {0} on {1}", [this.resource_label(fix.args.from_resource), subject]);
		}
		if ((fix.credentials || []).length) return __("Add someone to {0}. It needs: {1}", [subject, fix.credentials.join(", ")]);
		return __("Add someone to {0}", [subject]);
	},

	// A card in a conflict nobody kept: its red Conflict chip (or, for a missing qualification, the
	// amber chip that says so) becomes the way into the Conflict center; a card with neither gets one
	// small marker. Never two marks for one conflict.
	p6d_decorate() {
		const center = this.p6d && this.p6d.center;
		if (!center || !this.data) return;
		const root = this.$body[0];
		root.querySelectorAll("[data-p6d-conflict]").forEach((node) => this.p6d_undecorate(node));
		const index = center.index();
		const missing = __("Missing {0}", ["|"]).split("|")[0];
		root.querySelectorAll(".pp-card[data-task]").forEach((el) => {
			const found = index[`Task|${el.getAttribute("data-task")}`];
			if (!found) return;
			const row = el.getAttribute("data-resource");
			const ymd = el.getAttribute("data-date");
			const hit = row ? found.find((entry) => entry.resource === row && entry.date === ymd) : found[0];
			const holder = el.querySelector(".pp-card-chips");
			if (!hit || !holder) return;
			const chips = Array.from(holder.querySelectorAll(".pp-chip"));
			let chip = chips.find((node) => node.classList.contains("pp-red") && node.textContent === __("Conflict"));
			if (!chip && hit.kind === "qualification") {
				chip = chips.find((node) => node.classList.contains("pp-amber") && missing && node.textContent.indexOf(missing) === 0);
			}
			if (!chip) {
				chip = document.createElement("span");
				chip.className = "pp-chip pp-red pp-p6d-mark";
				chip.textContent = __("Conflict");
				holder.appendChild(chip);
			}
			chip.classList.add("pp-p6d-link");
			chip.setAttribute("data-p6d-conflict", hit.key);
			chip.setAttribute("role", "button");
			chip.setAttribute("tabindex", "0");
			chip.setAttribute("title", __("Open the Conflict center at this conflict"));
		});
	},

	p6d_undecorate(node) {
		if (node.classList.contains("pp-p6d-mark")) {
			node.remove();
			return;
		}
		node.classList.remove("pp-p6d-link");
		["data-p6d-conflict", "role", "tabindex", "title"].forEach((name) => node.removeAttribute(name));
	},

	// ------------------------------------------------------------------ the right-click menu

	// 6B's menu calls this with what was right-clicked (phase6-wave2's registry).
	p6d_menu_items(target) {
		if (!this.p6d || !this.data || !target) return [];
		const out = [];
		const center = this.p6d.center;
		if (target.kind === "card" && target.card && center) {
			const hits = center.find("Task", target.card.name);
			if (hits && hits.length) {
				out.push({ label: __("Show conflicts"), on_click: () => this.p6d_open_conflicts({ key: hits[0].key }) });
			}
		}
		if (target.kind === "cell" && target.ymd) {
			if (center) out.push({ label: __("Conflicts this day"), on_click: () => this.p6d_open_conflicts({ date: target.ymd }) });
			if (this.data.can_schedule) {
				out.push({ label: __("Add a day note…"), on_click: () => this.p6d_note_form({ date: target.ymd }) });
			}
		}
		if ((target.kind === "cell" || target.kind === "person") && this.p6d_can_block(target.resource)) {
			out.push({
				label: __("Block time…"),
				on_click: () => this.p6d_block_form({ resource: target.resource || "", date: target.ymd || this.p6d_default_date() }),
			});
		}
		return out;
	},

	// ------------------------------------------------------------------ legend

	p6d_legend_sections() {
		const kit = this.p6d_kit();
		if (!kit) return [];
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		return [
			{
				title: __("Unavailable time, day notes and conflicts"),
				items: [
					item(
						kit.blocks.chip_html({ name: "", slot: ["14:00", "16:00"] }, {}),
						"A personal block: the person is unavailable then. The planners and the person see its note; everyone else sees “Unavailable”. Click it to change it, if it is yours to change."
					),
					item(kit.blocks.chip_html({ name: "", all_day: 1 }, {}), "Unavailable all day: the day counts like time off."),
					item(
						kit.blocks.notes_html([{ note: __("Shop meeting 7 am") }], {}),
						"A note for the crew on that day. Hover over it or tap it to read it all; planners add one with + note."
					),
					item(
						`<span class="pp-chip pp-red pp-p6d-link">${pp_esc(__("Conflict"))}</span>`,
						"Click a Conflict chip to open the Conflict center at that conflict."
					),
					item(
						"",
						"The Conflicts chip on the toolbar (Conflicts… in Edit) lists everything wrong in the dates on screen, with one-click fixes. A fix that causes a conflict still asks for a reason, like a drag, and Undo puts it back."
					),
				],
			},
		];
	},
};

Object.assign(ProjectPlanner.prototype, PP6D_METHODS);

// ====================================================================== Phase 6E: working days, and split follow-ups
//
// Two small follow-ups to Phase 6B (Nik, 2026-10-09; TASK-2026-02471), both for this page:
//
//   - "Move by: Calendar days | Working days" in the selection bar. Dragging one of several selected
//     cards (or the bar's Move...) moves them all by the same number of days: calendar days as before
//     (the default), or working days (Monday to Friday) counted from each task's own start, a weekend
//     landing going to the next weekday and each task keeping its length in WORKING days (a task of 3
//     working days stays 3). The choice is remembered per device with save_pref. The offsets are worked
//     out here and sent to move_many as explicit starts and ends, so the server contract is unchanged.
//   - A split's second part now carries the tasks that waited on the original (planner_actions.
//     split_task re-points them); the Undo has to hand the list back (p6e_dependents), because
//     remove_created_task puts exactly those back on the first part.
//
// Hooks into Phase 6B's block, one line each: p6e_bar_note (p6b_render_bar),
// p6e_drop_days (p6b_drop), p6e_move_label / p6e_move_default / p6e_move_note (p6b_move_panel),
// p6e_moves and p6e_message (p6b_move_selection) and p6e_dependents (p6b_split_save). The first use of
// the switch adds its style, so the constructor needs no hook of its own.

const PP6E = {
	pref: "ee_project_planner_move_by",
	units: ["calendar", "working"],
};

// Pure helpers: no frappe, no moment, no page. tests/test_planner_phase6e.py runs them under node, and
// the Maintenance Planner keeps an identical copy of the shared ones (MP6E_PURE). The weekday maths
// is api/project_planner.add_working_days / working_days_between, kept in step and tested.
const PP6E_PURE = {
	is_weekend(ymd) {
		const weekday = new Date(`${ymd}T00:00:00Z`).getUTCDay();
		return weekday === 0 || weekday === 6;
	},
	// A Saturday or Sunday becomes the Monday after; any other day stays.
	next_weekday(ymd) {
		let day = ymd;
		for (let i = 0; i < 3 && PP6E_PURE.is_weekend(day); i++) day = PP6B_PURE.ymd_add(day, 1);
		return day;
	},
	// A Saturday or Sunday becomes the Friday before; any other day stays.
	previous_weekday(ymd) {
		let day = ymd;
		for (let i = 0; i < 3 && PP6E_PURE.is_weekend(day); i++) day = PP6B_PURE.ymd_add(day, -1);
		return day;
	},
	// Where a drop on `to` lands when it is a weekend: the Friday before when the move is backwards
	// (`to` is earlier than `from`), the Monday after when it is forwards. Nik, 2026-10-09.
	snap_weekday(from, to) {
		return to < from ? PP6E_PURE.previous_weekday(to) : PP6E_PURE.next_weekday(to);
	},
	// `count` working days later (negative: earlier). Each step lands on the next weekday, so a Friday
	// plus one is the Monday and a Saturday plus one is the Monday too.
	add_working_days(ymd, count) {
		const step = count < 0 ? -1 : 1;
		let day = ymd;
		for (let i = 0, n = Math.min(Math.abs(Math.trunc(Number(count) || 0)), 4000); i < n; i++) {
			day = PP6B_PURE.ymd_add(day, step);
			while (PP6E_PURE.is_weekend(day)) day = PP6B_PURE.ymd_add(day, step);
		}
		return day;
	},
	// Signed weekdays after `from` up to and including `to` (negative when `to` is earlier).
	working_between(from, to) {
		if (!from || !to || from === to) return 0;
		const sign = to > from ? 1 : -1;
		const low = sign > 0 ? from : to;
		const high = sign > 0 ? to : from;
		let count = 0;
		for (let day = PP6B_PURE.ymd_add(low, 1), i = 0; day <= high && i < 800; day = PP6B_PURE.ymd_add(day, 1), i++) {
			if (!PP6E_PURE.is_weekend(day)) count++;
		}
		return sign * count;
	},
	// How many days a drop moved the card in hand: by the calendar, or in working days to the weekday
	// the drop lands on (a drop on a weekend is the Monday after it going forwards, the Friday before
	// it going backwards).
	drop_days(from, to, unit) {
		if (unit !== "working") return PP6B_PURE.ymd_diff(from, to);
		return PP6E_PURE.working_between(from, PP6E_PURE.snap_weekday(from, to));
	},
	// The day a date lands on after moving `days` (calendar, or working days).
	date_for(ymd, days, unit) {
		return unit === "working" ? PP6E_PURE.add_working_days(ymd, days) : PP6B_PURE.ymd_add(ymd, days);
	},
	// The explicit moves for move_many. Working days: each task starts `days` working days from its own
	// start and runs as many working days as it did (a task that was only a weekend keeps its calendar
	// length). Calendar days is Phase 6B's offset_moves, unchanged.
	moves_for(cards, days, unit, modified_of) {
		if (unit !== "working") return PP6B_PURE.offset_moves(cards, days, modified_of);
		return (cards || [])
			.filter((card) => card && card.start)
			.map((card) => {
				const end = card.end && card.end >= card.start ? card.end : card.start;
				const start = PP6E_PURE.add_working_days(card.start, days);
				const length = PP6B_PURE.working_days(card.start, end);
				return {
					task: card.name,
					modified: modified_of ? modified_of(card.name) : card.modified,
					start,
					end:
						length > 0
							? PP6E_PURE.add_working_days(start, length - 1)
							: PP6B_PURE.ymd_add(start, PP6B_PURE.ymd_diff(card.start, end)),
				};
			});
	},
};

const PP6E_METHODS = {
	// The unit of a multi-select move, read once from the device's preference. Calendar days unless
	// the person chose working days.
	p6e_unit() {
		if (!this.p6e_unit_value) {
			this.p6e_unit_value = this.load_pref(PP6E.pref, "calendar") === "working" ? "working" : "calendar";
		}
		return this.p6e_unit_value;
	},

	p6e_set_unit(unit) {
		if (!PP6E.units.includes(unit) || unit === this.p6e_unit()) return;
		this.p6e_unit_value = unit;
		this.save_pref(PP6E.pref, unit);
		this.p6b_render_bar();
	},

	// "Move by: Calendar days | Working days", a field of the Move… panel (it was a switch on the
	// selection bar until the planner UX redesign, D12). The Select shows the words; the unit is the key.
	p6e_unit_label(unit) {
		return unit === "working" ? __("Working days") : __("Calendar days");
	},

	p6e_unit_options() {
		return PP6E.units.map((unit) => this.p6e_unit_label(unit)).join("\n");
	},

	// The panel's Select changed: remember the unit, and say what the number below counts now.
	p6e_panel_unit(panel) {
		if (!panel) return;
		const picked = PP6E.units.find((unit) => this.p6e_unit_label(unit) === panel.get_value("unit"));
		if (!picked || picked === this.p6e_unit()) return;
		this.p6e_set_unit(picked);
		const field = panel.fields_dict && panel.fields_dict.days;
		if (field) {
			if (field.df) Object.assign(field.df, { label: this.p6e_move_label(), description: this.p6e_move_note() });
			if (typeof field.set_label === "function") field.set_label(this.p6e_move_label());
			if (typeof field.set_description === "function") field.set_description(this.p6e_move_note());
		}
		panel.set_value("days", this.p6e_move_default());
	},

	p6e_bar_note() {
		return this.p6e_unit() === "working"
			? __("Drag any of them and they all move by the same number of working days (Monday to Friday), each keeping its length in working days. Move… changes the unit. Esc clears the selection.")
			: __("Drag any of them and they all move by the same number of calendar days, each keeping its length. Move… changes the unit. Esc clears the selection.");
	},

	p6e_drop_days(from, to) {
		return PP6E_PURE.drop_days(from, to, this.p6e_unit());
	},

	p6e_moves(cards, days, modified_of) {
		return PP6E_PURE.moves_for(cards, days, this.p6e_unit(), modified_of);
	},

	p6e_move_label() {
		return this.p6e_unit() === "working" ? __("Move by (working days)") : __("Move by (days)");
	},

	p6e_move_default() {
		return this.p6e_unit() === "working" ? 5 : 7;
	},

	p6e_move_note() {
		return this.p6e_unit() === "working"
			? __("Working days (Monday to Friday); a negative number moves them earlier. Each task keeps its length in working days and its time of day.")
			: __("Calendar days; a negative number moves them earlier. Each task keeps its length and time of day.");
	},

	p6e_message(count, days) {
		if (this.p6e_unit() === "working") {
			return days > 0
				? __("{0} tasks moved {1} working day(s) later", [count, days])
				: __("{0} tasks moved {1} working day(s) earlier", [count, -days]);
		}
		return days > 0
			? __("{0} tasks moved {1} day(s) later", [count, days])
			: __("{0} tasks moved {1} day(s) earlier", [count, -days]);
	},

	// A split's answer lists the tasks it pointed at the second part; the Undo needs them back.
	p6e_dependents(result) {
		const names = (result && result.repointed) || [];
		return names.length ? { dependents: names.slice() } : {};
	},
};

Object.assign(ProjectPlanner.prototype, PP6E_METHODS);

// ====================================================================== T1: build out tasks, Needs tasks, richer quick add
//
// Nik, 2026-10-12: "I definitely want to be able to easily create tasks for projects in the project
// planner screen so I don't have to leave it to build out tasks for a project." Three things:
//
//   - Build out tasks       a spreadsheet-style grid in the side panel: one row per task, with
//                           Subject, Hours, Phase, Depends on and Crew size. Enter drops to the same
//                           cell of the next row (a new row at the bottom, in the same phase), Tab
//                           goes along the row, pasting lines from a spreadsheet fills rows, and
//                           Ctrl/Cmd+Enter creates them. One build_out_tasks call makes all of them;
//                           the tasks are UNDATED, so they appear in the Unscheduled tray, ready to
//                           drag onto days. Nobody is picked and no day is chosen for anyone. Undo
//                           (remove_built_out_tasks) takes the lot back, unless something has been
//                           attached to one of them since. Opened from a project's side panel, the
//                           Needs tasks tray, or a card's right-click menu.
//   - Needs tasks           a tray of Active customer jobs that have no task at all, each with a
//                           "Build out" button (get_projects_needing_tasks: one query). It follows
//                           the page's project and manager filters.
//   - Richer quick add      is part of Phase 6B's dialog above (p6b_quick_add): phase, depends on, a
//                           time slot, several crew, address, outdoor and customer-visit, a
//                           description and a crew size.
//
// Back and Forward: the grid is a drawer of the planner kit, which owns its history entry (Back closes
// it, Forward reopens it through `reopen`), and what was typed is kept per project in this.t1.drafts
// so a Back that closes it does not lose the rows. In Draft mode (v1.603.0) Create sends draft=1: the
// tasks are kept as drafts (cards named `draft-new:<row>` in the Unscheduled tray), nothing is created
// until Publish, and its Undo discards them.
//
// Two ways to fill the grid besides typing and pasting from a spreadsheet (v1.603.0):
//   - Paste a list          free-form lines ("Excavate basin, 16h", "Pour slab - 8 hours", "• Pull wire
//                           (6 hrs)", "3. Commission system", "Electrical: Pull wire, 6h") are read by
//                           PPT1_PURE.parse_lines into rows: a subject, hours, and a phase before a colon.
//   - Copy from project...  get_copy_source returns another project's leaf tasks (any project the user
//                           can read) as rows: subject, hours, phase, crew size and the dependencies
//                           between them. Never dates, people, notes or addresses. The grid replaces
//                           its rows when empty, otherwise asks Append or Replace, and the tasks are
//                           created on THIS project like any others.
//
// Hooks into the class above, one line each: init_t1 (constructor), t1_load_needs (load),
// t1_render_tray (render_trays), t1_undo (undo), and t1_project_actions in Phase 6A's
// p6a_open_project. The right-click menu and the legend take their items through the registries.

const PPT1 = {
	actions: "erpnext_enhancements.api.planner_actions",
	// The writes: send() looks their module up in this.p6_send_modules.
	methods: ["build_out_tasks", "remove_built_out_tasks"],
	width: 920,
	start_rows: 5,
	max_rows: 100,
	// The Needs tasks tray shows this many projects until "Show all".
	needs_show: 30,
	// A planner load does not ask for the list again within this long (a build-out forces it).
	needs_wait_ms: 15000,
	// Grid columns, left to right; Enter moves down the column it is in.
	columns: ["subject", "hours", "phase", "needs", "crew_size"],
	check_wait_ms: 150,
};

// Pure helpers: no page, no frappe beyond __(). tests/test_planner_build_out.py runs them under node,
// and checks build_order against the server's (api/planner_actions.build_order).
const PPT1_PURE = {
	// `needs_more` (a copy from another project only) holds the ids of the other rows a row waits on,
	// beyond the one the Depends on cell shows.
	blank_row(id, phase) {
		return { id, subject: "", hours: "", phase: phase || "", needs: "", crew_size: "" };
	},
	// A row nobody has typed in. Its phase alone does not count: a new row inherits the one above.
	is_blank(row) {
		return !String(row.subject || "").trim() && !String(row.hours || "").trim() && !row.needs && !String(row.crew_size || "").trim();
	},
	clean(text) {
		return String(text == null ? "" : text).replace(/\s+/g, " ").trim();
	},
	// What the server will do with a typed phase: an existing open phase of that name (or subject, in
	// any capitals) is that phase; anything else is a new one, spelt as typed.
	// T3 (v1.608.0): a name starting with a phase number ("Phase 300", "300 Rough-in") is the project's
	// open phase with that number (its "Phase 300" first), as api/planner_actions._plan_build_out does.
	phase_match(text, phases) {
		const typed = PPT1_PURE.clean(text);
		if (!typed) return null;
		const key = typed.toLowerCase();
		let found = (phases || []).find((phase) => phase.name === typed || PPT1_PURE.clean(phase.subject).toLowerCase() === key);
		const number = PPT1_PURE.phase_number(typed);
		if (!found && number !== null) {
			const same = (phases || []).filter((phase) => PPT1_PURE.phase_number(phase.subject) === number);
			found = same.find((phase) => PPT1_PURE.clean(phase.subject).toLowerCase() === `phase ${number}`) || same[0];
		}
		return found ? { parent_task: found.name } : { phase: typed };
	},
	// The phase number a phase name starts with, or null: "Phase 300", "300 Rough-in", "phase 300 - design"
	// (planner_templates.PHASE_NUMBER, the same pattern).
	phase_number(text) {
		const found = /^\s*(?:phase\s*)?(\d{3})\b/i.exec(String(text == null ? "" : text));
		return found ? parseInt(found[1], 10) : null;
	},
	// How two phase names compare: by number when they have one, else by the name in any capitals.
	phase_key(text) {
		const number = PPT1_PURE.phase_number(text);
		return number !== null ? `#${number}` : PPT1_PURE.clean(text).toLowerCase();
	},
	// The order rows can be created in: a row after the rows it waits on, else the grid's own order.
	// {order, cycle}; with a loop `order` is null and `cycle` lists the rows (0-based) going round.
	// Kept in step with api/planner_actions.build_order (both are tested).
	build_order(count, waits) {
		const need = {};
		for (let row = 0; row < count; row++) need[row] = new Set((waits && waits[row]) || []);
		const done = new Set();
		const order = [];
		while (order.length < count) {
			let ready = -1;
			for (let row = 0; row < count; row++) {
				if (!done.has(row) && [...need[row]].every((other) => done.has(other))) {
					ready = row;
					break;
				}
			}
			if (ready < 0) {
				const seen = [];
				let row = [...Array(count).keys()].find((r) => !done.has(r));
				while (!seen.includes(row)) {
					seen.push(row);
					row = Math.min(...[...need[row]].filter((other) => !done.has(other)));
				}
				return { order: null, cycle: seen.slice(seen.indexOf(row)) };
			}
			done.add(ready);
			order.push(ready);
		}
		return { order, cycle: [] };
	},
	// The call's rows from the grid's, and what is wrong with them. Blank rows are left out; a row's
	// number is its place among the rest (the server counts the same way). `needs` is "" , "row:<id>"
	// (another row of the grid) or a task's name.
	rows_payload(rows, phases) {
		const kept = (rows || []).filter((row) => !PPT1_PURE.is_blank(row));
		const place = {};
		kept.forEach((row, index) => (place[row.id] = index + 1));
		const problems = [];
		const waits = {};
		const fresh = [];
		const out = kept.map((row, index) => {
			const number = index + 1;
			const entry = { subject: PPT1_PURE.clean(row.subject) };
			if (!entry.subject) problems.push(__("Row {0}: give the task a subject.", [number]));
			else if (entry.subject.length > 140) problems.push(__("Row {0}: keep the subject to 140 characters.", [number]));
			const typed = String(row.hours == null ? "" : row.hours).trim();
			if (typed) {
				const hours = parseFloat(typed);
				if (!Number.isFinite(hours) || hours < 0 || hours > 400) problems.push(__("Row {0}: hours must be a number from 0 to 400.", [number]));
				else if (hours > 0) entry.hours = hours;
			}
			const phase = PPT1_PURE.phase_match(row.phase, phases);
			if (phase) {
				Object.assign(entry, phase);
				if (phase.phase && !fresh.some((title) => PPT1_PURE.phase_key(title) === PPT1_PURE.phase_key(phase.phase))) fresh.push(phase.phase);
			}
			const size = String(row.crew_size == null ? "" : row.crew_size).trim();
			if (size) {
				const people = parseInt(size, 10);
				if (!(people >= 0 && people <= 50)) problems.push(__("Row {0}: crew size must be a number from 0 to 50.", [number]));
				else if (people > 0) entry.crew_size = people;
			}
			if (row.needs) {
				if (String(row.needs).indexOf("row:") === 0) {
					const other = place[String(row.needs).slice(4)];
					if (!other) problems.push(__("Row {0}: it waits on a row that is empty or gone.", [number]));
					else if (other === number) problems.push(__("Row {0}: a task cannot wait on itself.", [number]));
					else {
						entry.depends_on = [`row:${other}`];
						waits[index] = new Set([other - 1]);
					}
				} else {
					entry.depends_on = [row.needs];
				}
			}
			// The other rows a copied task waits on: a row that is gone is simply no longer waited on.
			(row.needs_more || []).forEach((id) => {
				const other = place[id];
				if (!other || other === number) return;
				entry.depends_on = (entry.depends_on || []).concat(`row:${other}`);
				if (!waits[index]) waits[index] = new Set();
				waits[index].add(other - 1);
			});
			return entry;
		});
		const sorted = PPT1_PURE.build_order(kept.length, waits);
		if (sorted.cycle.length) {
			problems.push(__("Rows {0} wait on each other, round and round. Take one of those dependencies out.", [sorted.cycle.map((i) => i + 1).join(", ")]));
		}
		return { rows: out, problems, new_phases: fresh, cycle: sorted.cycle.map((i) => kept[i].id) };
	},
	// Lines pasted from a spreadsheet as rows: Subject, Hours, Phase, Crew size, tab-separated.
	// null for a single plain line, which the browser pastes into the cell as usual.
	parse_paste(text) {
		const raw = String(text == null ? "" : text);
		const lines = raw.split(/\r?\n/).filter((line) => line.trim());
		if (lines.length < 2 && raw.indexOf("\t") < 0) return null;
		return lines.map((line) => {
			const cells = line.split("\t");
			const cell = (i) => String(cells[i] == null ? "" : cells[i]).trim();
			return { subject: cell(0), hours: cell(1), phase: cell(2), crew_size: cell(3) };
		});
	},
	// A pasted list as rows: one task per line. Reads "Excavate basin, 16h", "Pour slab - 8 hours",
	// "Set pumps 4h", "• Pull wire (6 hrs)", "3. Commission system" and "Electrical: Pull wire, 6h".
	//   - blank lines are skipped, and so is a line with no subject left;
	//   - a bullet (• - * and friends), a checkbox ([ ] [x]) or a number (3.  3)  (3) ) is stripped;
	//   - a trailing "4h", "4 hrs", "4 hours" (after a space, a comma, a dash or in brackets), or a bare
	//     number after a comma ("Pull wire, 6"), is the hours, and is taken off the subject;
	//   - "Phase: task" (a colon and a space) puts the task under that phase.
	// Rows are {subject, hours, phase, crew_size}: hours as text, "" when none.
	parse_lines(text) {
		const unit = /(?:\s*[,;:\u2013\u2014-]\s*|\s+|\s*\(\s*)\(?\s*(\d+(?:\.\d+)?|\.\d+)\s*(?:h|hrs?|hours?)\s*\)?\s*$/i;
		const bare = /\s*,\s*(\d+(?:\.\d+)?|\.\d+)\s*$/;
		const out = [];
		String(text == null ? "" : text)
			.split(/\r?\n/)
			.forEach((raw) => {
				let line = PPT1_PURE.clean(raw);
				line = line.replace(/^(?:[\u2022\u00b7\u25aa\u25ab\u25e6\u25cb\u25cf\u25a0\u25a1\u2023\u2043]|[-\u2013\u2014*+](?=\s))\s*/, "");
				line = line.replace(/^\[[ xX\u2713]?\]\s*/, "");
				line = line.replace(/^\(?\d+[.)]\s+/, "");
				let hours = "";
				const found = line.match(unit) || line.match(bare);
				if (found && found.index > 0) {
					hours = String(parseFloat(found[1]));
					line = line.slice(0, found.index);
				}
				line = PPT1_PURE.clean(line);
				// "8h" on a line of its own is hours with nothing to do: no task.
				if (/^(?:\d+(?:\.\d+)?|\.\d+)\s*(?:h|hrs?|hours?)$/i.test(line)) return;
				let phase = "";
				const prefixed = line.match(/^([^:]{1,60}?):\s+(\S.*)$/);
				if (prefixed && /[A-Za-z]/.test(prefixed[1]) && /[A-Za-z]/.test(prefixed[2])) {
					phase = PPT1_PURE.clean(prefixed[1]);
					line = PPT1_PURE.clean(prefixed[2]);
				}
				if (!line) return;
				out.push({ subject: line, hours, phase, crew_size: "" });
			});
		return out;
	},
	// get_copy_source's rows as grid rows: new ids, hours and crew size as text, and each "row:N" (the
	// N-th row copied, from 1) as the id of that row. The first dependency is the row's Depends on, the
	// rest are kept in `needs_more`.
	copy_rows(source, new_id) {
		const list = source || [];
		const made = list.map((row) =>
			Object.assign(PPT1_PURE.blank_row(new_id(), row.phase || ""), {
				subject: row.subject || "",
				hours: Number(row.hours) > 0 ? String(Number(row.hours)) : "",
				crew_size: Number(row.crew_size) > 0 ? String(Number(row.crew_size)) : "",
			})
		);
		list.forEach((row, index) => {
			const ids = (row.needs || [])
				.map((token) => {
					const target = made[parseInt(String(token).slice(4), 10) - 1];
					return target ? target.id : null;
				})
				.filter((id) => id && id !== made[index].id);
			if (ids.length) made[index].needs = `row:${ids[0]}`;
			if (ids.length > 1) made[index].needs_more = ids.slice(1);
		});
		return made;
	},
	// The procurement checklists (T3) of the phases Create will MAKE, as build_out_tasks takes them:
	// {phase as the grid spells it: [items]}, matched in any capitals; null when there are none. A
	// checklist of a phase no row uses, or of one the project already has, is never sent.
	checklists_for(checklists, new_phases) {
		const known = {};
		Object.keys(checklists || {}).forEach((title) => (known[PPT1_PURE.phase_key(title)] = checklists[title]));
		const out = {};
		(new_phases || []).forEach((title) => {
			const lines = known[PPT1_PURE.phase_key(title)];
			if (lines && lines.length) out[title] = lines;
		});
		return Object.keys(out).length ? out : null;
	},
	// "5 tasks and 2 phases" (the phases only when there are some).
	counts(count, phases) {
		const tasks = count === 1 ? __("1 task") : __("{0} tasks", [count]);
		if (!phases) return tasks;
		return phases === 1 ? __("{0} and 1 phase", [tasks]) : __("{0} and {1} phases", [tasks, phases]);
	},
	// The Create button: "Create 5 tasks and 2 phases".
	summary(count, phases) {
		return count ? __("Create {0}", [PPT1_PURE.counts(count, phases)]) : __("Create tasks");
	},
	// Does a Needs tasks row pass the page's project and manager filters.
	needs_visible(row, project, pm) {
		if (project && row.name !== project) return false;
		if (pm && row.pm !== pm) return false;
		return true;
	},
};

const PPT1_METHODS = {
	init_t1() {
		this.t1 = { needs: null, needs_token: 0, needs_at: 0, needs_open: false, show_all: false, drafts: {}, ids: 0, grid: null };
		this.p6_send_modules = this.p6_send_modules || {};
		PPT1.methods.forEach((method) => (this.p6_send_modules[method] = PPT1.actions));
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.t1_menu_items(target));
		this.p6_legend_providers = this.p6_legend_providers || [];
		this.p6_legend_providers.push(() => this.t1_legend_sections());
	},

	t1_can() {
		return !!(this.t1 && this.data && this.data.can_edit);
	},

	// ------------------------------------------------------------------ where it opens from

	t1_project_actions(project) {
		if (!this.t1_can() || !project) return [];
		return [
			{
				label: __("Build out tasks"),
				primary: true,
				title: __("Add many tasks to this project at once"),
				on_click: () => this.t1_open_build_out(project),
			},
		];
	},

	t1_menu_items(target) {
		if (!this.t1_can() || !target || target.kind !== "card" || !target.card || !target.card.project) return [];
		const project = target.card.project;
		return [{ label: __("Build out tasks for this project…"), on_click: () => this.t1_open_build_out(project) }];
	},

	t1_legend_sections() {
		return [
			{
				title: __("Building out a project"),
				items: [
					{
						sample_html: "",
						text: __("Build out tasks (a project's side panel, the Needs tasks tray, or a card's right-click menu) opens a grid: type a task on each row, Enter for the next row, and Create. They land in the Unscheduled tray, ready to drag onto days."),
					},
					{
						sample_html: "",
						text: __("Phase: pick an existing phase of the project, or type a new name to make one. Depends on: another row of the grid, or a task the project already has. You can paste lines from a spreadsheet (Subject, Hours, Phase, Crew size)."),
					},
					{
						sample_html: "",
						text: __("Needs tasks lists the active projects that have no tasks yet; Build out starts their plan."),
					},
				],
			},
		];
	},

	// ------------------------------------------------------------------ the Needs tasks tray

	t1_load_needs(force) {
		const t1 = this.t1;
		if (!t1 || !this.data || !this.data.can_edit) return Promise.resolve(null);
		if (!force && t1.needs && Date.now() - t1.needs_at < PPT1.needs_wait_ms) return Promise.resolve(null);
		const token = ++t1.needs_token;
		return Promise.resolve(frappe.call({ method: `${PPT1.actions}.get_projects_needing_tasks` }))
			.then((r) => {
				if (token !== t1.needs_token) return;
				t1.needs = (r && r.message) || null;
				t1.needs_at = Date.now();
				if (PP.views.includes(this.view) && this.data) this.render_trays();
			})
			.catch(() => null);
	},

	t1_needs_rows() {
		const needs = this.t1 && this.t1.needs;
		if (!needs || !needs.can_create || !this.data || !this.data.can_edit) return [];
		return (needs.projects || []).filter((row) => PPT1_PURE.needs_visible(row, this.project, this.pm));
	},

	t1_render_tray() {
		const rows = this.t1_needs_rows();
		if (!rows.length) return;
		const t1 = this.t1;
		const $tray = $('<div class="pp-tray t1-tray"></div>').appendTo(this.$trays);
		const $h = $("<h5></h5>").appendTo($tray);
		$("<span></span>").text(__("Needs tasks ({0}): projects with nothing to schedule yet", [rows.length])).appendTo($h);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(t1.needs_open ? __("Hide") : __("Show"))
			.attr("aria-expanded", t1.needs_open ? "true" : "false")
			.on("click", () => {
				t1.needs_open = !t1.needs_open;
				this.render_trays();
			})
			.appendTo($h);
		if (!t1.needs_open) return;
		const shown = t1.show_all ? rows : rows.slice(0, PPT1.needs_show);
		shown.forEach((row) => {
			const $row = $('<div class="t1-tray-row"></div>').appendTo($tray);
			const $main = $('<div class="t1-tray-main"></div>').appendTo($row);
			$('<span class="t1-tray-title" role="button" tabindex="0"></span>')
				.text(row.title || row.name)
				.attr("title", __("See the project at a glance"))
				.on("click", () => this.p6a_open_project(row.name))
				.on("keydown", (e) => {
					if (e.key === "Enter" || e.key === " ") {
						e.preventDefault();
						this.p6a_open_project(row.name);
					}
				})
				.appendTo($main);
			const note = __("No tasks yet");
			$('<span class="t1-tray-sub"></span>')
				.text([row.name, row.pm, note].filter(Boolean).join(" · "))
				.appendTo($main);
			$('<button type="button" class="btn btn-primary btn-xs"></button>')
				.text(__("Build out"))
				.on("click", () => this.t1_open_build_out(row.name, row.title))
				.appendTo($row);
		});
		if (rows.length > shown.length) {
			$('<button type="button" class="btn btn-default btn-xs"></button>')
				.text(__("Show all"))
				.on("click", () => {
					t1.show_all = true;
					this.render_trays();
				})
				.appendTo($tray);
		}
	},

	// ------------------------------------------------------------------ the grid

	// The planner_actions writes go through send() like every other write (reason prompt, Undo).
	t1_send(method, args, opts) {
		return this.send(method, args, opts);
	},

	t1_el(tag, props, kids) {
		const el = document.createElement(tag);
		Object.entries(props || {}).forEach(([key, value]) => {
			if (value == null || value === false) return;
			if (key === "class") el.className = value;
			else if (key === "text") el.textContent = value;
			else el.setAttribute(key, value === true ? "" : String(value));
		});
		(kids || []).forEach((kid) => kid && el.appendChild(kid));
		return el;
	},

	t1_new_id() {
		this.t1.ids += 1;
		return `r${this.t1.ids}`;
	},

	t1_open_build_out(project, title) {
		if (!this.t1_can()) return null;
		const kit = this.p6b_kit();
		if (!kit) {
			frappe.msgprint(__("The side panel is still loading. Try again in a moment."));
			return null;
		}
		const known = this.by_project[project] || {};
		const label = title || known.title || project;
		const saved = this.t1.drafts[project];
		const body = this.t1_el("div", { class: "t1-wrap", text: __("Loading…") });
		const state = {
			project,
			label,
			body,
			handle: null,
			phases: [],
			tasks: [],
			rows: saved && saved.length ? saved : null,
			busy: false,
			timer: null,
		};
		this.t1.grid = state;
		state.handle = kit.drawer.open({
			title: __("Build out tasks"),
			subtitle: [label, project !== label ? project : ""].filter(Boolean).join(" · "),
			body,
			width: PPT1.width,
			key: `t1-build:${project}`,
			owner: PP6A.owner,
			push: this.$body[0],
			actions: this.t1_actions(state, 0, 0),
			reopen: () => this.t1_open_build_out(project, title),
			on_close: () => {
				clearTimeout(state.timer);
				// What was typed is kept, so Back and Forward (or a stray click) do not lose it.
				if (!state.done && state.rows) this.t1.drafts[project] = state.rows.filter((row) => !PPT1_PURE.is_blank(row));
				if (this.t1.grid === state) this.t1.grid = null;
			},
		});
		// In Draft mode the grid is also offered the phases and tasks an earlier drafted build-out made.
		return Promise.resolve(frappe.call({ method: `${PPT1.actions}.get_build_out_context`, args: Object.assign({ project }, this.draft_args(project)) }))
			.then((r) => {
				const context = (r && r.message) || {};
				state.phases = context.phases || [];
				state.tasks = context.tasks || [];
				state.truncated = !!context.truncated;
				if (!state.rows) state.rows = this.t1_start_rows();
				else state.rows = state.rows.map((row) => Object.assign(PPT1_PURE.blank_row(this.t1_new_id()), row, { id: row.id || this.t1_new_id() }));
				if (!state.handle.is_open()) return;
				this.t1_build_grid(state);
			})
			.catch(() => {
				if (!state.handle.is_open()) return;
				body.textContent = __("The project could not be loaded. Try again.");
			});
	},

	t1_start_rows() {
		return Array.from({ length: PPT1.start_rows }, () => PPT1_PURE.blank_row(this.t1_new_id()));
	},

	t1_build_grid(state) {
		const body = state.body;
		while (body.firstChild) body.removeChild(body.firstChild);
		body.appendChild(
			this.t1_el("p", {
				class: "t1-intro",
				text: this.draft_on
					? __("One task per row. They are kept as drafts without dates, in the Unscheduled tray, and created when you publish. Enter moves to the next row; you can start from a template, paste lines from a spreadsheet, paste a list, or copy another project's tasks.")
					: __("One task per row. They are created without dates and appear in the Unscheduled tray. Enter moves to the next row; you can start from a template, paste lines from a spreadsheet, paste a list, or copy another project's tasks."),
			})
		);
		const heads = [
			["", "t1-num"],
			[__("Subject"), "t1-c-subject"],
			[__("Hours"), "t1-c-hours"],
			[__("Phase / parent"), "t1-c-phase"],
			[__("Depends on"), "t1-c-needs"],
			[__("Crew size"), "t1-c-crew_size"],
			["", "t1-c-del"],
		];
		const head = this.t1_el("tr", {}, heads.map(([text, cls]) => this.t1_el("th", { class: cls, scope: "col", text })));
		state.tbody = this.t1_el("tbody", {});
		state.list_id = `t1-phases-${this.t1.ids}-${Math.random().toString(36).slice(2, 7)}`;
		state.datalist = this.t1_el("datalist", { id: state.list_id });
		state.table = this.t1_el("table", { class: "t1-grid", role: "grid" }, [this.t1_el("thead", {}, [head]), state.tbody]);
		body.appendChild(this.t1_el("div", { class: "t1-scroll" }, [state.table, state.datalist]));
		state.status = this.t1_el("div", { class: "t1-status", "aria-live": "polite" });
		state.problems = this.t1_el("ul", { class: "t1-problems" });
		body.appendChild(state.status);
		body.appendChild(state.problems);
		if (state.truncated) {
			body.appendChild(this.t1_el("p", { class: "t1-status", text: __("Only the first tasks of this project are offered under Depends on.") }));
		}
		state.table.addEventListener("input", (e) => this.t1_input(state, e));
		state.table.addEventListener("change", (e) => this.t1_input(state, e));
		state.table.addEventListener("keydown", (e) => this.t1_key(state, e));
		state.table.addEventListener("paste", (e) => this.t1_paste(state, e));
		state.table.addEventListener("click", (e) => {
			const del = e.target && e.target.closest ? e.target.closest("[data-t1-del]") : null;
			if (del) this.t1_remove_row(state, del.getAttribute("data-t1-del"));
		});
		this.t1_rebuild_rows(state, { row: 0, col: "subject" });
	},

	// Draw every row again (adding, deleting or pasting changes them all); typing never does this.
	t1_rebuild_rows(state, focus) {
		while (state.tbody.firstChild) state.tbody.removeChild(state.tbody.firstChild);
		state.rows.forEach((row, index) => state.tbody.appendChild(this.t1_row_el(state, row, index)));
		this.t1_refresh_options(state);
		this.t1_check(state);
		if (focus) this.t1_focus(state, focus.row, focus.col);
	},

	t1_row_el(state, row, index) {
		const cell = (col, kid) => this.t1_el("td", { class: `t1-c-${col}` }, [kid]);
		const label = (name) => __("{0}, row {1}", [name, index + 1]);
		const field = (col, props) => {
			const input = this.t1_el("input", Object.assign({ class: "form-control input-sm", "data-t1-col": col, "data-t1-row": row.id, autocomplete: "off" }, props));
			input.value = row[col] == null ? "" : String(row[col]);
			return input;
		};
		const select = this.t1_el("select", { class: "form-control input-sm", "data-t1-col": "needs", "data-t1-row": row.id, "aria-label": label(__("Depends on")) });
		const tr = this.t1_el("tr", { "data-t1-id": row.id }, [
			this.t1_el("td", { class: "t1-num", text: String(index + 1) }),
			cell("subject", field("subject", { type: "text", maxlength: 140, "aria-label": label(__("Subject")) })),
			cell("hours", field("hours", { type: "number", min: 0, max: 400, step: "0.25", inputmode: "decimal", "aria-label": label(__("Hours")) })),
			cell("phase", field("phase", { type: "text", maxlength: 140, list: state.list_id, "aria-label": label(__("Phase / parent")), placeholder: __("None") })),
			cell("needs", select),
			cell("crew_size", field("crew_size", { type: "number", min: 0, max: 50, step: "1", inputmode: "numeric", "aria-label": label(__("Crew size")) })),
			cell(
				"del",
				this.t1_el("button", { type: "button", class: "t1-del", "data-t1-del": row.id, title: __("Remove this row"), "aria-label": label(__("Remove")), text: "×" })
			),
		]);
		// A row the AI drafted (T4) carries a quiet mark until the planner edits it away or creates it.
		if (row.ai) {
			tr.classList.add("t4-ai");
			tr.setAttribute("title", __("AI draft — review before creating"));
		}
		return tr;
	},

	// The Depends on lists (the other rows, and the project's own tasks) and the Phase suggestions.
	t1_refresh_options(state) {
		const rows = state.rows;
		const phases = state.phases.map((phase) => phase.subject || phase.name);
		const seen = new Set(phases.map((name) => name.toLowerCase()));
		rows.forEach((row) => {
			const typed = PPT1_PURE.clean(row.phase);
			if (typed && !seen.has(typed.toLowerCase())) {
				seen.add(typed.toLowerCase());
				phases.push(typed);
			}
		});
		while (state.datalist.firstChild) state.datalist.removeChild(state.datalist.firstChild);
		phases.forEach((name) => state.datalist.appendChild(this.t1_el("option", { value: name })));
		const live = new Set(rows.filter((row) => !PPT1_PURE.is_blank(row) && PPT1_PURE.clean(row.subject)).map((row) => row.id));
		rows.forEach((row, index) => {
			const select = state.tbody.querySelector(`select[data-t1-row="${row.id}"]`);
			if (!select) return;
			if (row.needs && String(row.needs).indexOf("row:") === 0 && !live.has(String(row.needs).slice(4))) row.needs = "";
			while (select.firstChild) select.removeChild(select.firstChild);
			select.appendChild(this.t1_el("option", { value: "", text: __("Nothing") }));
			const here = this.t1_el("optgroup", { label: __("Rows in this grid") });
			rows.forEach((other, at) => {
				if (other.id === row.id || !live.has(other.id)) return;
				const text = PPT1_PURE.clean(other.subject);
				here.appendChild(this.t1_el("option", { value: `row:${other.id}`, text: `${at + 1}. ${text.length > 40 ? `${text.slice(0, 39)}…` : text}` }));
			});
			if (here.firstChild) select.appendChild(here);
			if (state.tasks.length) {
				const there = this.t1_el("optgroup", { label: __("Tasks the project already has") });
				state.tasks.forEach((task) => {
					const text = PPT1_PURE.clean(task.subject);
					const shown = text.length > 40 ? `${text.slice(0, 39)}…` : text;
					there.appendChild(this.t1_el("option", { value: task.name, text: task.draft ? __("{0} (drafted)", [shown]) : `${shown} (${task.name})` }));
				});
				select.appendChild(there);
			}
			select.value = row.needs || "";
			if (select.value !== (row.needs || "")) row.needs = select.value;
			// A copied task can wait on several rows; the cell shows the first, and says about the rest.
			row.needs_more = (row.needs_more || []).filter((id) => live.has(id) && id !== row.id);
			const places = row.needs_more.map((id) => rows.findIndex((other) => other.id === id) + 1);
			const cell = select.parentNode;
			let more = cell.querySelector(".t1-more");
			if (places.length) {
				if (!more) {
					more = this.t1_el("div", { class: "t1-more" });
					cell.appendChild(more);
				}
				more.textContent = __("Also waits on row(s) {0}", [places.join(", ")]);
			} else if (more) {
				more.remove();
			}
		});
	},

	t1_focus(state, index, col) {
		const row = state.rows[index];
		if (!row) return;
		const el = state.tbody.querySelector(`[data-t1-row="${row.id}"][data-t1-col="${col}"]`);
		if (!el) return;
		el.focus();
		if (el.select && el.type !== "number") el.select();
	},

	t1_input(state, e) {
		const el = e.target;
		if (!el || !el.getAttribute) return;
		const id = el.getAttribute("data-t1-row");
		const col = el.getAttribute("data-t1-col");
		const row = id && state.rows.find((item) => item.id === id);
		if (!row || !col) return;
		row[col] = el.value;
		state.rows_changed = true;
		clearTimeout(state.timer);
		state.timer = setTimeout(() => {
			if (col === "subject" || col === "phase") this.t1_refresh_options(state);
			this.t1_check(state);
		}, PPT1.check_wait_ms);
	},

	t1_key(state, e) {
		const el = e.target;
		if (!el || !el.getAttribute || !el.getAttribute("data-t1-col")) return;
		if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
			e.preventDefault();
			this.t1_create(state);
			return;
		}
		if (e.key !== "Enter" || e.isComposing) return;
		e.preventDefault();
		const id = el.getAttribute("data-t1-row");
		const col = el.getAttribute("data-t1-col");
		const index = state.rows.findIndex((row) => row.id === id);
		if (index < 0) return;
		const target = index + (e.shiftKey ? -1 : 1);
		if (target < 0) return;
		if (target >= state.rows.length) {
			if (state.rows.length >= PPT1.max_rows) return;
			this.t1_add_row(state, { inherit: state.rows[index].phase, col });
			return;
		}
		this.t1_focus(state, target, col);
	},

	t1_add_row(state, opts) {
		opts = opts || {};
		if (state.rows.length >= PPT1.max_rows) {
			frappe.show_alert({ message: __("Build out {0} tasks or fewer at once.", [PPT1.max_rows]), indicator: "orange" }, 5);
			return;
		}
		state.rows.push(PPT1_PURE.blank_row(this.t1_new_id(), opts.inherit || ""));
		this.t1_rebuild_rows(state, { row: state.rows.length - 1, col: opts.col || "subject" });
	},

	t1_remove_row(state, id) {
		const index = state.rows.findIndex((row) => row.id === id);
		if (index < 0) return;
		state.rows.splice(index, 1);
		if (!state.rows.length) state.rows.push(PPT1_PURE.blank_row(this.t1_new_id()));
		this.t1_rebuild_rows(state, { row: Math.min(index, state.rows.length - 1), col: "subject" });
	},

	t1_paste(state, e) {
		const el = e.target;
		if (!el || !el.getAttribute || el.getAttribute("data-t1-col") !== "subject") return;
		const text = e.clipboardData && e.clipboardData.getData ? e.clipboardData.getData("text") : "";
		const lines = PPT1_PURE.parse_paste(text);
		if (!lines) return;
		e.preventDefault();
		const at = Math.max(state.rows.findIndex((row) => row.id === el.getAttribute("data-t1-row")), 0);
		this.t1_fill_rows(state, lines, at);
	},

	// Rows from `at` down take the lines; a row with something in it is not overwritten, and the grid
	// grows (to the most it holds). Shared by the spreadsheet paste and "Paste a list".
	t1_fill_rows(state, lines, at) {
		let index = at;
		let left = 0;
		lines.forEach((line) => {
			while (index < state.rows.length && !PPT1_PURE.is_blank(state.rows[index])) index += 1;
			if (index >= PPT1.max_rows) {
				left += 1;
				return;
			}
			const row = index < state.rows.length ? state.rows[index] : null;
			if (row) Object.assign(row, line);
			else state.rows.push(Object.assign(PPT1_PURE.blank_row(this.t1_new_id()), line));
			index += 1;
		});
		if (left) {
			frappe.show_alert({ message: __("The grid holds {0} tasks; {1} lines were left out.", [PPT1.max_rows, left]), indicator: "orange" }, 7);
		}
		state.note = "";
		this.t1_rebuild_rows(state, { row: Math.min(index, state.rows.length - 1), col: "subject" });
	},

	// ------------------------------------------------------------------ paste a list

	t1_open_paste_list(state) {
		if (!state.rows || !state.table) return null;
		let dialog = null;
		dialog = new frappe.ui.Dialog({
			title: __("Paste a list"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "t1_paste_help",
					options: `<p class="text-muted small">${pp_esc(
						__("One task per line. Bullets and numbers are dropped. Hours at the end (4h, 8 hrs, - 8 hours, (6 hrs) or , 6) go in the Hours column, and \"Electrical: Pull wire, 6h\" files the task under the phase Electrical.")
					)}</p>`,
				},
				{ fieldtype: "Small Text", fieldname: "t1_list", label: __("The list"), reqd: 1 },
			],
			primary_action_label: __("Add to the grid"),
			primary_action: (values) => {
				const lines = PPT1_PURE.parse_lines((values && values.t1_list) || "");
				if (!lines.length) {
					frappe.show_alert({ message: __("Nothing to add: type one task per line."), indicator: "orange" }, 5);
					return;
				}
				if (dialog) dialog.hide();
				let at = state.rows.findIndex((row) => PPT1_PURE.is_blank(row));
				if (at < 0) at = state.rows.length;
				this.t1_fill_rows(state, lines, at);
				frappe.show_alert({ message: __("{0} tasks added to the grid.", [lines.length]), indicator: "green" }, 4);
			},
		});
		dialog.show();
		return dialog;
	},

	// ------------------------------------------------------------------ copy from another project

	t1_open_copy(state) {
		if (!state.rows || !state.table) return null;
		let dialog = null;
		dialog = new frappe.ui.Dialog({
			title: __("Copy from project"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "t1_copy_help",
					options: `<p class="text-muted small">${pp_esc(
						__("Starts the grid from another project's tasks: their names, hours, phases, crew sizes and what waits on what. Dates, people, notes and addresses are not copied. You can edit everything before you create.")
					)}</p>`,
				},
				{ fieldtype: "Link", fieldname: "t1_source", label: __("Copy tasks from"), options: "Project", reqd: 1 },
			],
			primary_action_label: __("Load tasks"),
			primary_action: (values) => {
				const source = values && values.t1_source;
				if (!source) return;
				if (dialog) dialog.hide();
				this.t1_load_copy(state, source);
			},
		});
		dialog.show();
		return dialog;
	},

	t1_load_copy(state, source) {
		return Promise.resolve(frappe.call({ method: `${PPT1.actions}.get_copy_source`, args: { project: source } }))
			.then((r) => {
				const plan = (r && r.message) || {};
				if (!state.handle || !state.handle.is_open()) return null;
				if (!(plan.rows || []).length) {
					frappe.msgprint(__("{0} has no tasks to copy. {1}", [pp_esc(source), pp_esc(plan.note || "")]));
					return null;
				}
				return this.t1_apply_copy(state, plan);
			})
			.catch(() => null);
	},

	// Replace the grid when it is empty; otherwise ask whether to add to it or replace it.
	t1_apply_copy(state, plan) {
		if (state.rows.every((row) => PPT1_PURE.is_blank(row))) return this.t1_set_rows(state, plan, "replace");
		let dialog = null;
		dialog = new frappe.ui.Dialog({
			title: __("The grid already has tasks"),
			fields: [
				{ fieldtype: "HTML", fieldname: "t1_how_help", options: `<p>${pp_esc(__("Add the {0} copied tasks below what you typed, or replace what you typed?", [plan.count]))}</p>` },
				{ fieldtype: "Select", fieldname: "t1_how", label: __("What to do"), options: `Append\nReplace`, default: "Append" },
			],
			primary_action_label: __("Load tasks"),
			primary_action: (values) => {
				if (dialog) dialog.hide();
				this.t1_set_rows(state, plan, (values && values.t1_how) === "Replace" ? "replace" : "append");
			},
		});
		dialog.show();
		return dialog;
	},

	t1_set_rows(state, plan, how) {
		const copied = PPT1_PURE.copy_rows(plan.rows, () => this.t1_new_id());
		const kept = how === "append" ? state.rows.filter((row) => !PPT1_PURE.is_blank(row)) : [];
		// A template's phases bring their procurement checklists (T3); Replace drops the earlier ones.
		if (plan.checklists) state.checklists = Object.assign({}, how === "append" ? state.checklists : {}, plan.checklists);
		let rows = kept.concat(copied);
		if (rows.length > PPT1.max_rows) {
			frappe.show_alert({ message: __("The grid holds {0} tasks; the last {1} copied tasks were left out.", [PPT1.max_rows, rows.length - PPT1.max_rows]), indicator: "orange" }, 8);
			rows = rows.slice(0, PPT1.max_rows);
		}
		state.rows = rows;
		state.note = plan.note || "";
		this.t1_rebuild_rows(state, { row: 0, col: "subject" });
		if (plan.note) frappe.show_alert({ message: plan.note, indicator: "blue" }, 10);
		return rows;
	},

	// The footer's buttons, with the count of what Create would make.
	t1_actions(state, count, phases) {
		return [
			{ label: __("Start from template…"), title: __("Phases and task bundles kept as Project Templates"), on_click: () => this.t3_open_templates(state) },
			{ label: __("Paste a list…"), title: __("Lines like “Pour slab, 8h” become rows"), on_click: () => this.t1_open_paste_list(state) },
			{ label: __("Copy from project…"), title: __("Start from another project's tasks"), on_click: () => this.t1_open_copy(state) },
			// T4's "AI draft…" (defined after this block; absent where only the T1 block is loaded).
			...(this.t4_actions ? this.t4_actions(state) : []),
			{ label: __("Add row"), title: __("A new row at the bottom"), on_click: () => state.rows && state.table && this.t1_add_row(state) },
			{ label: PPT1_PURE.summary(count, phases), primary: true, title: __("Ctrl or Cmd + Enter"), on_click: () => this.t1_create(state) },
		];
	},

	// Check the grid as it stands: the rows with a mistake are marked, the count is on the button.
	t1_check(state) {
		if (!state.rows || !state.tbody) return null;
		const built = PPT1_PURE.rows_payload(state.rows, state.phases);
		state.tbody.querySelectorAll("tr").forEach((tr) => tr.classList.toggle("t1-bad", built.cycle.indexOf(tr.getAttribute("data-t1-id")) >= 0));
		while (state.problems.firstChild) state.problems.removeChild(state.problems.firstChild);
		built.problems.forEach((text) => state.problems.appendChild(this.t1_el("li", { text })));
		const status = built.rows.length
			? __("Ready to create {0}. Rows with nothing typed in them are skipped.", [PPT1_PURE.counts(built.rows.length, built.new_phases.length)])
			: __("Type a subject on a row to begin.");
		state.status.textContent = state.note ? `${status} ${state.note}` : status;
		if (state.handle && state.handle.is_open() && !state.busy) {
			state.handle.set_actions(this.t1_actions(state, built.rows.length, built.new_phases.length));
		}
		return built;
	},

	// ------------------------------------------------------------------ create and undo

	t1_create(state) {
		if (state.busy || !state.rows) return Promise.resolve(null);
		// Drop the blank rows first, so the numbers in any message are the numbers on the screen.
		state.rows = state.rows.filter((row) => !PPT1_PURE.is_blank(row));
		if (!state.rows.length) state.rows.push(PPT1_PURE.blank_row(this.t1_new_id()));
		this.t1_rebuild_rows(state, null);
		const built = PPT1_PURE.rows_payload(state.rows, state.phases);
		if (!built.rows.length) {
			frappe.show_alert({ message: __("Type at least one task first."), indicator: "orange" }, 5);
			return Promise.resolve(null);
		}
		if (built.problems.length) {
			frappe.msgprint({ title: __("Fix these first"), message: built.problems.map((text) => pp_esc(text)).join("<br>"), indicator: "orange" });
			return Promise.resolve(null);
		}
		state.busy = true;
		// draft=1 in Draft mode: the tasks are kept as drafts and created when the planner publishes.
		const args = Object.assign({ project: state.project, rows: JSON.stringify(built.rows) }, this.draft_args(state.project));
		// A new phase that came from a template takes its procurement checklist along (T3).
		const checklists = PPT1_PURE.checklists_for(state.checklists, built.new_phases);
		if (checklists) args.phase_checklists = JSON.stringify(checklists);
		return this.t1_send("build_out_tasks", args, {})
			.then((result) => {
				state.busy = false;
				if (!result || !result.tasks) {
					if (state.handle && state.handle.is_open()) this.t1_check(state);
					return result;
				}
				state.done = true;
				delete this.t1.drafts[state.project];
				result.tasks.concat(result.phases || []).forEach((item) => {
					if (item.name && item.modified) this.modified[item.name] = item.modified;
				});
				const count = result.tasks.length;
				const message = result.drafted
					? __("{0} tasks drafted on {1}. They are in the Unscheduled tray as drafts; publish to create them.", [count, state.label])
					: __("{0} tasks created on {1}. They are in the Unscheduled tray.", [count, state.label]);
				const snapshot = {
					t1: "built",
					subject: count === 1 ? result.tasks[0].subject : __("{0} tasks", [count]),
					project: state.project,
					tasks: result.tasks.map((item) => ({ task: item.name, modified: item.modified })),
					phases: (result.phases || []).map((item) => ({ task: item.name, modified: item.modified })),
				};
				if (!this.push_undo(snapshot, message)) frappe.show_alert({ message, indicator: "green" }, 6);
				if (state.handle && state.handle.is_open()) state.handle.close();
				return result;
			})
			.finally(() => {
				state.busy = false;
				this.t1_load_needs(true);
				this.load();
			});
	},

	// Undo of a build-out. False for any other entry. The server refuses (and says why) when
	// anything has been attached to one of the tasks since, and then removes none of them.
	t1_undo(snap) {
		if (!snap || snap.t1 !== "built") return false;
		const args = {
			tasks: JSON.stringify(snap.tasks.map((item) => ({ task: item.task, modified: this.modified[item.task] || item.modified }))),
			phases: JSON.stringify((snap.phases || []).map((item) => ({ task: item.task, modified: this.modified[item.task] || item.modified }))),
		};
		this.t1_send("remove_built_out_tasks", args, { message: __("Undone: {0}", [snap.subject]) }).finally(() => {
			this.t1_load_needs(true);
			this.load();
		});
		return true;
	},
};

Object.assign(ProjectPlanner.prototype, PPT1_METHODS);

// ====================================================================== T3: phase templates and task bundles
//
// Nik, 2026-10-12: start a job's plan from DOC-0041's phases instead of typing it, and keep reusable
// bundles of tasks ("Control panel design + build" and its subtasks) to pick from and compose. Both
// are ERPNext Project Templates, edited in the Desk (api/planner_templates.py,
// project_enhancements/planner_templates.py).
//
//   - Start from template…  a Build out footer action (t1_actions). The dialog offers a phase template
//                           with its phases ticked (800 is never offered: maintenance contracts own
//                           it) and/or bundles, and the phase the bundles go under. get_template_rows
//                           answers rows in get_copy_source's shape (subject, default hours, phase, crew
//                           size, "row:N" dependencies), so they land through the same t1_apply_copy:
//                           an empty grid is replaced, otherwise Append or Replace. Bundles can be added
//                           to a grid that already has rows. Each phase's procurement checklist rides
//                           in state.checklists, and t1_create sends the ones of the phases it makes.
//
// The catalog (get_planner_templates) is read BEFORE the dialog is built: a MultiSelectPills field asks
// its get_data while Frappe is still constructing the dialog, so get_data reads `catalog`, never the
// dialog (the v1.602.1 trap: a `const` read during its own construction throws).

const PPT3 = {
	templates: "erpnext_enhancements.api.planner_templates",
};

// Pure helpers: no page, no frappe beyond __() and pp_esc() (and PPT1_PURE.clean, the grid's own).
// tests/test_planner_templates.py runs them under node.
const PPT3_PURE = {
	// A MultiSelectPills value (a list, or a comma list) as names, blanks and repeats dropped.
	names(value) {
		const list = Array.isArray(value) ? value : String(value == null ? "" : value).split(",");
		const out = [];
		list.forEach((item) => {
			const name = PPT1_PURE.clean(item);
			if (name && out.indexOf(name) < 0) out.push(name);
		});
		return out;
	},
	// The bundles a typed text matches, as MultiSelectPills options.
	bundle_options(catalog, txt) {
		const key = PPT1_PURE.clean(txt).toLowerCase();
		return ((catalog && catalog.bundles) || [])
			.filter((bundle) => !key || bundle.name.toLowerCase().indexOf(key) >= 0)
			.map((bundle) => ({
				value: bundle.name,
				description: bundle.needs_review ? __("{0} tasks · needs review", [bundle.tasks]) : __("{0} tasks", [bundle.tasks]),
			}));
	},
	// The phases the bundles may go under: the project's open phases, the phases typed in the grid, and
	// the phase template's own; once each, in any capitals, and one per phase number (the project's own
	// spelling first: "300 Rough-in" and "Phase 300" are the same phase).
	phase_choices(phases, rows, catalog) {
		const out = [];
		const seen = new Set();
		const add = (name) => {
			const text = PPT1_PURE.clean(name);
			const key = PPT1_PURE.phase_key(text);
			if (text && !seen.has(key)) {
				seen.add(key);
				out.push(text);
			}
		};
		(phases || []).forEach((phase) => add(phase.subject || phase.name));
		(rows || []).forEach((row) => add(row.phase));
		((catalog && catalog.phase_templates) || []).forEach((template) => (template.phases || []).forEach((phase) => add(phase.title)));
		return out;
	},
	// The templates still marked "Needs review", for the dialog to say so.
	review_note(catalog) {
		const names = []
			.concat((catalog && catalog.phase_templates) || [], (catalog && catalog.bundles) || [])
			.filter((entry) => entry.needs_review)
			.map((entry) => entry.name);
		return names.length ? __("Not reviewed yet: {0}. Edit them in Project Template.", [names.join(", ")]) : "";
	},
	// The dialog's fields. One phase checklist per phase template, shown while that template is chosen.
	fields(catalog, choices) {
		const templates = (catalog && catalog.phase_templates) || [];
		const bundles = (catalog && catalog.bundles) || [];
		const help = [__("Pick the phases this job needs and/or bundles of tasks. The rows land in the grid for you to edit before you create them; phases run in order and each phase's tasks after the phase before.")];
		const review = PPT3_PURE.review_note(catalog);
		if (review) help.push(review);
		const fields = [{ fieldtype: "HTML", fieldname: "t3_help", options: help.map((text) => `<p class="text-muted small">${pp_esc(text)}</p>`).join("") }];
		if (templates.length) {
			fields.push({
				fieldtype: "Select",
				fieldname: "t3_template",
				label: __("Phase template"),
				options: [""].concat(templates.map((template) => template.name)).join("\n"),
				default: templates[0].name,
				description: __("Blank: bundles only."),
			});
			templates.forEach((template, index) => {
				fields.push({
					fieldtype: "MultiCheck",
					fieldname: `t3_phases_${index}`,
					label: __("Phases"),
					columns: 2,
					depends_on: `eval:doc.t3_template==${JSON.stringify(template.name)}`,
					options: (template.phases || []).map((phase) => ({ label: phase.label || phase.title, value: String(phase.id), checked: 1 })),
				});
			});
		}
		if (bundles.length) {
			fields.push({
				fieldtype: "MultiSelectPills",
				fieldname: "t3_bundles",
				label: __("Bundles"),
				get_data: (txt) => PPT3_PURE.bundle_options(catalog, txt),
			});
			fields.push({
				fieldtype: "Autocomplete",
				fieldname: "t3_bundle_phase",
				label: __("Put the bundles under the phase"),
				options: choices || [],
				ignore_validation: 1,
				description: __("A phase of the grid or of the project, or a new name. Blank: no phase."),
			});
		}
		return fields;
	},
	// The dialog's values as get_template_rows' arguments; null when nothing was picked.
	request(values, catalog, project) {
		const templates = (catalog && catalog.phase_templates) || [];
		const name = PPT1_PURE.clean(values && values.t3_template);
		const index = templates.findIndex((template) => template.name === name);
		const args = {};
		if (index >= 0) {
			const ticked = values[`t3_phases_${index}`];
			const ids = (Array.isArray(ticked) ? ticked : []).map((id) => String(id)).filter(Boolean);
			if (ids.length) {
				args.template = name;
				args.phases = JSON.stringify(ids);
			}
		}
		const bundles = PPT3_PURE.names(values && values.t3_bundles);
		if (bundles.length) {
			args.bundles = JSON.stringify(bundles);
			const phase = PPT1_PURE.clean(values && values.t3_bundle_phase);
			if (phase) args.bundle_phase = phase;
		}
		if (!args.template && !args.bundles) return null;
		if (project) args.project = project;
		return args;
	},
};

const PPT3_METHODS = {
	// Read the catalog first, then build the dialog from it.
	t3_open_templates(state) {
		if (!state || !state.rows || !state.table) return Promise.resolve(null);
		return Promise.resolve(frappe.call({ method: `${PPT3.templates}.get_planner_templates` }))
			.then((r) => {
				const catalog = (r && r.message) || {};
				if (!state.handle || !state.handle.is_open()) return null;
				if (!(catalog.phase_templates || []).length && !(catalog.bundles || []).length) {
					frappe.msgprint(__("There are no planner templates yet. A System Manager or Projects Manager makes them in Project Template, with a Planner Template Kind of Phase template or Bundle."));
					return null;
				}
				return this.t3_dialog(state, catalog);
			})
			.catch(() => null);
	},

	t3_dialog(state, catalog) {
		const choices = PPT3_PURE.phase_choices(state.phases, state.rows, catalog);
		let dialog = null;
		dialog = new frappe.ui.Dialog({
			title: __("Start from template"),
			fields: PPT3_PURE.fields(catalog, choices),
			primary_action_label: __("Load tasks"),
			primary_action: (values) => {
				const args = PPT3_PURE.request(values, catalog, state.project);
				if (!args) {
					frappe.show_alert({ message: __("Tick at least one phase, or pick a bundle."), indicator: "orange" }, 5);
					return;
				}
				if (dialog) dialog.hide();
				this.t3_load(state, args);
			},
		});
		dialog.show();
		return dialog;
	},

	// The rows go in through Copy from project's own path: replaced when the grid is empty, otherwise
	// Append or Replace; the phases' checklists with them.
	t3_load(state, args) {
		return Promise.resolve(frappe.call({ method: `${PPT3.templates}.get_template_rows`, args }))
			.then((r) => {
				const plan = (r && r.message) || {};
				if (!state.handle || !state.handle.is_open() || !(plan.rows || []).length) return null;
				return this.t1_apply_copy(state, plan);
			})
			.catch(() => null);
	},
};

Object.assign(ProjectPlanner.prototype, PPT3_METHODS);

// ====================================================================== T4: AI-drafted task breakdown
//
// Nik, 2026-10-12: in the Build out grid, an "AI draft…" action proposes a task breakdown for the project,
// which the planner then edits and creates as usual. This block only asks for it and fills the grid:
//
//   - AI draft…   a footer action of the grid (t4_actions, hooked into t1_actions). A small dialog takes an
//                 optional "Describe the job" and, when the grid already has tasks, Append or Replace. It
//                 shows a spinner while planner_ai_draft.draft_task_breakdown works (one server call, which
//                 reads the project and asks the model), then fills the grid through t1_set_rows -- the very
//                 path "Copy from project…" uses, so the AI's `row:N` dependencies land as the grid's own.
//   - It writes nothing. The call only returns rows; nothing is a Task until the planner presses Create
//     (or, in Draft mode, creates drafts). It returns no person and no date, so the rows have none.
//   - Rows are marked quietly (a coloured edge and a tooltip, `row.ai`) and the status line says "AI draft —
//     review before creating". If the AI cannot help (not set up, too slow, nothing to propose) the dialog
//     stays open and says so in plain words; the grid is left as it was.
//
// Kept apart from the T1 block so it can be merged beside other build-out work: the only lines outside it
// are `...this.t4_actions(state)` in t1_actions and the `row.ai` mark in t1_row_el.

const PPT4 = {
	actions: "erpnext_enhancements.api.planner_ai_draft",
	brief_max: 1500,
};

// Pure helpers: no page, no frappe beyond __(). Run under node by tests/test_planner_ai_draft.py.
const PPT4_PURE = {
	// The subjects typed in the grid, so an Append is not offered what is already there.
	typed_subjects(rows) {
		return (rows || [])
			.map((row) => PPT1_PURE.clean(row.subject))
			.filter((subject) => subject)
			.slice(0, PPT1.max_rows);
	},
	// The arguments of draft_task_breakdown. Replace tells the AI nothing of the grid (it is going away).
	call_args(project, brief, rows, how, draft_args) {
		const args = Object.assign({ project, brief: PPT1_PURE.clean(brief).slice(0, PPT4.brief_max) }, draft_args || {});
		if (how !== "replace") args.grid_subjects = JSON.stringify(PPT4_PURE.typed_subjects(rows));
		return args;
	},
	// What the grid is given from the server's answer: {rows, note}, or null when there is nothing to add.
	plan_from(answer) {
		if (!answer || !answer.ok || !Array.isArray(answer.rows) || !answer.rows.length) return null;
		return { rows: answer.rows, note: answer.note || __("AI draft — review before creating."), count: answer.rows.length };
	},
	// The words under the dialog when the AI could not help.
	failure(answer) {
		return (answer && answer.message) || __("The AI draft could not be made. Try again, or build the tasks by hand.");
	},
};

const PPT4_METHODS = {
	t4_actions(state) {
		return [
			{
				label: __("AI draft…"),
				title: __("Ask the AI for a first list of tasks for this project. Nothing is created until you press Create."),
				on_click: () => this.t4_open(state),
			},
		];
	},

	// The status line under the dialog's fields: `html` is already escaped text, `bad` colours it.
	t4_say(dialog, html, bad) {
		const field = dialog && dialog.fields_dict && dialog.fields_dict.t4_status;
		if (field && field.$wrapper) field.$wrapper.html(`<div class="t4-status${bad ? " t4-bad" : ""}" aria-live="polite">${html}</div>`);
	},

	t4_busy(dialog, on) {
		const button = dialog && dialog.get_primary_btn ? dialog.get_primary_btn() : null;
		if (button && button.prop) button.prop("disabled", !!on);
	},

	t4_open(state) {
		if (!state || !state.rows || !state.table || state.t4_busy) return null;
		const has_rows = state.rows.some((row) => !PPT1_PURE.is_blank(row));
		const fields = [
			{
				fieldtype: "HTML",
				fieldname: "t4_help",
				options: `<p class="text-muted small">${pp_esc(
					__("Drafts a first list of tasks from what the project holds: its scope, notes and the tasks it already has. It only fills the grid; it never picks a person or a day, and nothing is created until you press Create.")
				)}</p>`,
			},
			{ fieldtype: "Small Text", fieldname: "t4_brief", label: __("Describe the job (optional)"), description: __("A sentence or two: what is being built or fixed, and anything the notes leave out.") },
		];
		if (has_rows) {
			fields.push({ fieldtype: "Select", fieldname: "t4_how", label: __("The grid already has tasks"), options: `Append\nReplace`, default: "Append" });
		}
		fields.push({ fieldtype: "HTML", fieldname: "t4_status", options: "" });
		let dialog = null;
		dialog = new frappe.ui.Dialog({
			title: __("AI draft"),
			fields,
			primary_action_label: __("Draft tasks"),
			primary_action: (values) => this.t4_run(state, dialog, values || {}),
		});
		dialog.show();
		return dialog;
	},

	t4_run(state, dialog, values) {
		if (state.t4_busy) return Promise.resolve(null);
		// Replace (chosen, or nothing typed to keep) tells the AI nothing of the grid: it is going away.
		const replacing = values.t4_how === "Replace" || !state.rows.some((row) => !PPT1_PURE.is_blank(row));
		const how = replacing ? "replace" : "append";
		const args = PPT4_PURE.call_args(state.project, values.t4_brief, state.rows, how, this.draft_args(state.project));
		state.t4_busy = true;
		this.t4_busy(dialog, true);
		this.t4_say(dialog, `<span class="t4-spin"></span>${pp_esc(__("Drafting… this can take up to a minute."))}`, false);
		return Promise.resolve(frappe.call({ method: `${PPT4.actions}.draft_task_breakdown`, args }))
			.then((r) => {
				const answer = (r && r.message) || null;
				const plan = PPT4_PURE.plan_from(answer);
				if (!plan) {
					this.t4_say(dialog, pp_esc(PPT4_PURE.failure(answer)), true);
					return null;
				}
				if (!state.handle || !state.handle.is_open()) return null;
				if (dialog && dialog.hide) dialog.hide();
				return this.t4_fill(state, plan, how);
			})
			.catch(() => {
				this.t4_say(dialog, pp_esc(__("The AI draft could not be reached. Nothing was changed. Try again.")), true);
				return null;
			})
			.finally(() => {
				state.t4_busy = false;
				this.t4_busy(dialog, false);
			});
	},

	// Fill the grid with the drafted rows (the same path as "Copy from project…"), then mark them as the AI's.
	t4_fill(state, plan, how) {
		const before = new Set(state.rows.map((row) => row.id));
		const rows = this.t1_set_rows(state, plan, how);
		let first = -1;
		rows.forEach((row, index) => {
			if (before.has(row.id)) return;
			row.ai = true;
			if (first < 0) first = index;
		});
		this.t1_rebuild_rows(state, { row: Math.max(first, 0), col: "subject" });
		return rows;
	},
};

Object.assign(ProjectPlanner.prototype, PPT4_METHODS);

// ====================================================================== The toolbar (v1.687.0, v2)
//
// Nik picked the toolbar redesign from a mockup on 2026-10-14, and on 2026-10-21 approved the next
// step of it (the planner UX redesign, P1: "Approve as is"). Every phase used to append its own
// buttons to the bar, so it had grown into one long flat row. v1.687.0 folded them into four menus;
// v2 folds those into two, so the bar is the same on every screen of both planners:
//
//   ‹ Today ›  title  [highlight]  Month Week Crew Heatmap Utilization   search  Project  PM  Group
//   [alert chips]  + Add ▾  More ▾  [↶]  ?  ↻
//
//   - + Add ▾     Quick add task… (the double-click dialog, on today or the first day on screen),
//                 Build out tasks… (the T1 grid; a project is asked for when none is filtered) and
//                 Day note… (6D)
//   - More ▾      four sections:
//       Display   Color by (6C; the default is now Job type) and the key of its colors; Highlight a
//                 project…; Maintenance, rentals, travel
//       Views     My week; the saved views (6C), Save current view as…, Manage views…
//       Tools     Copy week… (week and crew views); Block time… (6D); Conflicts… (6D, even at none);
//                 Tap to move (6C); Hold all my changes (the old Draft mode, 3B); Undo
//       Print     Print this view and Print selection only (6C); Print crew sheet (3B)
//   - alert chips Conflicts (6D) and Overdue in the danger tone, Running over (4) in warn, each a
//                 count and its word, there only above zero (Running over also while it filters).
//                 A chip opens what its button did: the Conflict center, the Running over filter,
//                 the Overdue tray (opened and scrolled to)
//   - ↶           Undo, as a small icon only while there is something to undo
//   - ?           the help, whose first section is the tips that used to be banners on load
//   - Draft mode  a strip under the bar, "Draft mode on: N changes not published yet", with
//                 Publish and Discard (3B's render_draft_bar, publish_drafts, discard_drafts)
//   - the tabs    Month Week Crew Heatmap Utilization are a WAI-ARIA tablist (planner_kit
//                 toolbar.tabs): arrow keys move focus, Enter opens. Later PRs of the redesign swap
//                 the list for Lookahead / Gantt / Crew / Calendar / Overview; the control stays.
//
// Gone from the bar and kept elsewhere (D12, "nothing familiar disappears"): Split is in the
// right-click menu and the task panel only; the Move by switch (calendar or working days) is a field
// of the Move… panel on the selection bar (p6b_move_panel); Color by, the saved views, Copy week and
// Print are in More.
//
// The menus are planner_kit.toolbar.menu (a menu button: arrow keys, Esc gives focus back, a click
// elsewhere closes it, and opening one pushes no history entry). Each menu's items are built when it
// opens, from the page's own state, through the methods the phases already had: nothing is clicked
// on someone's behalf, and an item whose control was hidden on a screen (Copy week in the month view,
// Block time for someone who cannot schedule) is left out of the menu there. A section with nothing
// in it is left out with its heading.
//
// On a phone (767px and under) the bar is ‹ Today ›, the title, the view switcher and "Filters and
// tools", which opens planner_kit.toolbar.sheet: the filters (moved in, and back when it closes), the
// alert chips, the same sections as sheet sections (Add, Display, Views, Tools, Print) and the help
// and refresh. The sheet is a screen, so it takes the kit's one history entry, and the phone's Back
// closes it (Nik's rule: never break Back/Forward).
//
// Hooks into the class above, one line each: init_toolbar (constructor), tb_ready (p6a_ready),
// tb_render (render, set_mode, load_overdue, p6d_after_list) and tb_close_sheet (p6b_pick).

const PPTB = {
	// The phone breakpoint, as the CSS writes it (--sf-phone-max cannot be read in a media query).
	phone_max: 767,
	// An alert chip's tone, as the class that paints it.
	tones: { danger: "pp-tb-danger", warn: "pp-tb-warn" },
};

const PPTB_METHODS = {
	// The two menus: [name, label, what is in it, the button's text]. Written out as __() calls, so
	// they are translated.
	tb_menu_list() {
		return [
			["add", __("Add"), __("Add a task, build out a project's tasks, or add a day note"), __("+ Add")],
			["more", __("More"), __("Display, Views, Tools and Print"), __("More")],
		];
	},

	init_toolbar() {
		this.tb = { kit: null, menus: {}, $menus: {}, sheet: null, tabs: null };
		this.tb_menu_list().forEach(([name, label, title, text]) => {
			const $button = $('<button type="button" class="btn btn-default btn-sm pp-tb-menu"></button>')
				.attr("data-tb-menu", name)
				.attr("title", title)
				.on("click", () => {
					// The menu itself is the kit's; until the kit has loaded, say so.
					if (!this.tb.kit) frappe.show_alert({ message: __("One moment: the planner is still loading."), indicator: "blue" }, 4);
				})
				.appendTo(this.$tools);
			$("<span></span>").text(text || label).appendTo($button);
			$('<span class="pp-tb-caret" aria-hidden="true">▾</span>').appendTo($button);
			this.tb.$menus[name] = $button;
		});
		this.$tb_more = $('<button type="button" class="btn btn-default btn-sm pp-tb-more"></button>')
			.attr("aria-haspopup", "dialog")
			.attr("aria-label", __("Filters and tools"))
			.on("click", () => this.tb_open_sheet())
			.appendTo(this.$toolbar);
		$("<span></span>").text(__("Filters and tools")).appendTo(this.$tb_more);
		this.$tb_more_count = $('<span class="pp-tb-count" aria-hidden="true"></span>').hide().appendTo(this.$tb_more);
		// A menu or the sheet left open when the page hides (another page) closes with it; a route
		// change of the planner's own closes a menu through the kit (toolbar.js).
		const wrapper = this.page && this.page.wrapper;
		if (wrapper && typeof wrapper.on === "function") {
			wrapper.on("hide", (e) => {
				if (e.target === wrapper[0]) this.tb_close_menus();
			});
		}
	},

	tb_close_menus() {
		const kit = this.tb && this.tb.kit;
		if (kit && kit.toolbar) kit.toolbar.close_all("route");
	},

	// The kit has loaded (6A's p6a_ready): the buttons become menus, the view buttons tabs.
	tb_ready() {
		const kit = this.p6a && this.p6a.kit;
		if (!this.tb || this.tb.kit || !kit || !kit.toolbar) return;
		this.tb.kit = kit;
		this.tb_menu_list().forEach(([name, label]) => {
			this.tb.menus[name] = kit.toolbar.menu(this.tb.$menus[name][0], {
				label,
				items: () => this.tb_items(name),
			});
		});
		if (this.$seg && this.$seg[0] && typeof kit.toolbar.tabs === "function") {
			this.tb.tabs = kit.toolbar.tabs(this.$seg[0], { label: __("Planner views"), selected: this.tab_of_view() });
		}
		this.tb_render();
	},

	// The calendar views: week, month and crew (not the route, heatmap, Utilization or My week).
	tb_calendar() {
		return PP.views.includes(this.view);
	},

	// One menu's items, as planner_kit.toolbar takes them.
	tb_items(name) {
		if (name === "add") return this.tb_add_items();
		if (name === "more") return this.tb_sections_items(this.tb_more_sections());
		return [];
	},

	// Named sections as one list (the kit's), or, before the kit has loaded, plainly one after another.
	tb_sections_items(sections) {
		const kit = this.tb && this.tb.kit;
		if (kit && kit.toolbar && typeof kit.toolbar.sections_to_items === "function") return kit.toolbar.sections_to_items(sections);
		return [].concat(...sections.map((section) => this.tb_kit_tidy(section.items)));
	},

	// More's four sections; the phone's sheet draws the same ones.
	tb_more_sections() {
		return [
			{ key: "display", title: __("Display"), items: this.tb_display_items() },
			{ key: "views", title: __("Views"), items: this.tb_views_items() },
			{ key: "tools", title: __("Tools"), items: this.tb_tools_items() },
			{ key: "print", title: __("Print"), items: this.tb_print_items() },
		];
	},

	// + Add: the ways to make a task or a note without touching a card. The task dialog is the
	// double-click's (p6b_quick_add), started on the first day on screen (today when it is there).
	tb_add_items() {
		const items = [];
		const data = this.data;
		if (this.tb_calendar() && data && data.can_edit) {
			items.push({
				label: __("Quick add task…"),
				title: __("Add one task: the project, a subject, a person and a day. Double-click an empty spot does the same."),
				on_click: () => this.p6b_quick_add({ kind: "cell", resource: "", ymd: this.p6d_default_date() }),
			});
		}
		if (this.t1_can && this.t1_can()) {
			items.push({
				label: __("Build out tasks…"),
				title: __("Add many tasks to one project at once: a grid, a pasted list, another project's tasks, a template or an AI draft"),
				on_click: () => this.tb_build_out(),
			});
		}
		if (this.tb_calendar() && data && data.can_schedule) {
			items.push({
				label: __("Day note…"),
				title: __("A note for everyone on a day, such as a shop meeting"),
				on_click: () => this.p6d_note_form({ date: this.p6d_default_date() }),
			});
		}
		return items;
	},

	// Build out tasks… from the bar: for the project in the filter, or for the one picked first.
	tb_build_out() {
		if (!this.t1_can()) return;
		const open = (project) => {
			if (project) this.t1_open_build_out(project, (this.by_project[project] || {}).title);
		};
		if (this.project && this.by_project[this.project]) {
			open(this.project);
			return;
		}
		// The planner's own list of projects: Active customer jobs.
		const names = (this.data.projects || []).map((project) => project.name);
		frappe.prompt(
			[
				{
					fieldtype: "Link",
					fieldname: "project",
					label: __("Project"),
					options: "Project",
					reqd: 1,
					description: __("Customer jobs only."),
					get_query: () => ({ filters: { name: ["in", names.length ? names : [""]] } }),
				},
			],
			(values) => open(values && values.project),
			__("Build out tasks for"),
			__("Open the grid")
		);
	},

	tb_views_items() {
		const items = [
			{
				label: __("My week"),
				title: __("Your own week: each day's stops in route order, crewmates and notes"),
				on_click: () => this.go_my_week(frappe.datetime.get_today()),
			},
		];
		if (this.tb_calendar() || this.view === PP.capacity_view) items.push({ divider: true }, ...this.p6c_view_items());
		return items;
	},

	tb_display_items() {
		if (!this.tb_calendar()) return [];
		return this.p6c_color_items().concat([{ divider: true }], this.tb_highlight_items(), [
			{
				label: __("Maintenance, rentals, travel"),
				checked: !!this.show_foreign,
				title: __("Maintenance visits, rental crew work and travel, read-only: they move on their own planners"),
				on_click: () => this.set_show_foreign(!this.show_foreign),
			},
			this.tb_all_tasks_item(),
		]);
	},

	// "Show all tasks" (P2, D6): the schedule shows phase and crew work only, and this brings back the
	// template admin and billing checklist tasks. The server does the filtering, so a hidden task is not
	// on the page at all; the count says how many this view left out.
	tb_all_tasks_item() {
		const hidden = (this.data && this.data.hidden_tasks) || 0;
		return {
			label: __("Show all tasks"),
			checked: !!this.all_tasks,
			hint: !this.all_tasks && hidden ? __("{0} not shown", [hidden]) : "",
			title: __(
				"The schedule shows tasks under a phase and tasks with a crew. Turn this on for every open task, such as the template admin and billing checklists."
			),
			on_click: () => this.set_all_tasks(!this.all_tasks),
		};
	},

	// Highlight a project: on, it can be switched off here (Esc and the pill's × do too); off, a project
	// is asked for. A project's name on a card, its peek and a project in the color key start it as well.
	tb_highlight_items() {
		if (!this.p6c) return [];
		const hi = this.p6c.highlight;
		if (hi) {
			return [
				{
					label: __("Highlighting {0}", [hi.label || hi.key]),
					checked: true,
					title: __("Stop highlighting (Esc)"),
					on_click: () => this.p6c_clear_highlight(),
				},
			];
		}
		if (!((this.data && this.data.projects) || []).length) return [];
		return [
			{
				label: __("Highlight a project…"),
				title: __("Light up one project's tasks on the calendar and fade the rest"),
				on_click: () => this.tb_highlight_prompt(),
			},
		];
	},

	tb_highlight_prompt() {
		const projects = (this.data && this.data.projects) || [];
		if (!projects.length) return;
		frappe.prompt(
			[
				{
					fieldtype: "Autocomplete",
					fieldname: "project",
					label: __("Project"),
					reqd: 1,
					options: projects.map((project) => ({ value: project.name, label: this.p6c_project_label(project.name) })),
				},
			],
			(values) => values && values.project && this.p6c_highlight(values.project),
			__("Highlight a project"),
			__("Highlight")
		);
	},

	tb_tools_items() {
		const items = [];
		if (this.tb_calendar() && this.view !== "month" && this.data && this.data.can_edit) {
			items.push({
				label: __("Copy week…"),
				title: __("Copy tasks from this week into another week"),
				on_click: () => this.open_copy_week(),
			});
		}
		if (this.p6d_toolbar_block()) {
			items.push({
				label: __("Block time…"),
				title: __("Mark someone unavailable for part of a day or all of it"),
				on_click: () => this.p6d_block_form({ date: this.p6d_default_date() }),
			});
		}
		if (this.tb_calendar()) {
			const count = this.p6d_conflict_count();
			items.push({
				label: __("Conflicts…"),
				hint: count ? String(count) : "",
				title: __("Everything wrong in the dates on screen, with one-click fixes"),
				on_click: () => this.p6d_open_conflicts(null),
			});
		}
		if (this.tb_calendar() && this.p6c) {
			items.push({
				label: __("Tap to move"),
				checked: !!this.p6c.tap,
				title: __("Tap a card, then tap the day where it goes, instead of dragging"),
				on_click: () => this.p6c_set_tap(!this.p6c.tap),
			});
		}
		items.push({
			label: __("Hold all my changes"),
			checked: !!this.draft_on,
			title: __("Keep every change to yourself until you publish it, on any project"),
			on_click: () => this.set_draft_mode(!this.draft_on),
		});
		if (this.view !== PP.capacity_view) {
			const last = this.undo_stack[this.undo_stack.length - 1];
			items.push(
				{ divider: true },
				{
					label: __("Undo"),
					hint: __("Ctrl+Z"),
					disabled: !last,
					title: last ? __("Undo the last change to {0}", [last.subject]) : __("Nothing to undo"),
					on_click: () => this.undo(),
				}
			);
		}
		return items;
	},

	tb_print_items() {
		const items = this.tb_calendar() || this.view === PP.capacity_view ? this.p6c_print_items() : [];
		return items.concat([
			{
				label: __("Print crew sheet"),
				title: __("A one-page sheet of who is where this week, to print"),
				on_click: () => this.print_crew_sheet(),
			},
		]);
	},

	// ------------------------------------------------------------------ alert chips

	// The counts that need someone's attention, on the calendar views: each only above zero.
	tb_alerts() {
		if (!this.tb_calendar() || !this.data) return [];
		const out = [];
		const conflicts = this.p6d_conflict_count();
		if (conflicts > 0) {
			out.push({
				key: "conflicts",
				tone: "danger",
				count: conflicts,
				label: __("Conflicts"),
				title: __("{0} conflict(s) nobody has kept in the dates on screen", [conflicts]),
				open: () => this.p6d_open_conflicts(null),
			});
		}
		const over = this.p4_over_count();
		if (over > 0 || this.over_only) {
			out.push({
				key: "over",
				tone: "warn",
				count: over,
				label: __("Running over"),
				pressed: !!this.over_only,
				title: this.over_only
					? __("Showing only the tasks past their plan. Click to show every task again.")
					: __("Show only the tasks whose clocked hours are past their plan"),
				open: () => this.p4_toggle_over(),
			});
		}
		const overdue = this.overdue_visible().total;
		if (overdue > 0) {
			out.push({
				key: "overdue",
				tone: "danger",
				count: overdue,
				label: __("Overdue"),
				title: __("Late open tasks: open the Overdue list"),
				open: () => this.tb_open_overdue(),
			});
		}
		return out;
	},

	// The Overdue chip: the tray opens and comes into view, its Hide button focused.
	tb_open_overdue() {
		this.overdue_open = true;
		this.render_trays();
		const tray = this.$trays[0] && this.$trays[0].querySelector(".pp-od");
		if (!tray) return;
		try {
			tray.scrollIntoView({ block: "start", behavior: "smooth" });
		} catch (e) {
			tray.scrollIntoView();
		}
		const button = tray.querySelector("h5 button");
		if (button) button.focus({ preventScroll: true });
	},

	// The chips, the phone button's count and an open sheet, after anything that changes them. A
	// chip that had focus has it again once redrawn (Running over redraws the board under it).
	tb_render() {
		if (!this.tb || !this.$alerts) return;
		const alerts = this.tb_alerts();
		const active = document.activeElement;
		const had = active && this.$alerts[0].contains(active) ? active.getAttribute("data-tb-alert") : null;
		this.$alerts.empty();
		alerts.forEach((alert) => {
			const $chip = $('<button type="button" class="pp-tb-alert"></button>')
				.addClass(PPTB.tones[alert.tone] || "")
				.attr("data-tb-alert", alert.key)
				.attr("title", alert.title)
				.on("click", () => alert.open())
				.appendTo(this.$alerts);
			if ("pressed" in alert) $chip.attr("aria-pressed", alert.pressed ? "true" : "false");
			$('<span class="pp-tb-count"></span>').text(String(alert.count)).appendTo($chip);
			$("<span></span>").text(alert.label).appendTo($chip);
		});
		// A class, not toggle(): on a phone the CSS hides the chips in the bar (they are in the sheet).
		this.$alerts.toggleClass("pp-tb-empty", !alerts.length);
		if (had) {
			const again = this.$alerts.find(`[data-tb-alert="${had}"]`)[0];
			if (again) again.focus();
		}
		const total = alerts.reduce((sum, alert) => sum + (Number(alert.count) || 0), 0);
		this.$tb_more_count.text(total ? String(total) : "").toggle(!!total);
		this.$tb_more.attr("aria-label", total ? __("Filters and tools, {0} alerts", [total]) : __("Filters and tools"));
		// + Add with nothing for this screen is not offered (Utilization, or no right to add). More is never
		// empty (My week, the crew sheet), so it is not built here just to be counted.
		const $add = this.tb.$menus.add;
		if ($add) $add.toggleClass("pp-tb-off", !this.tb_add_items().length);
		if (this.tb.tabs) this.tb.tabs.sync(this.tab_of_view());
		if (this.tb.sheet && this.tb.sheet.is_open()) this.tb.sheet.refresh();
	},

	// The items a menu would show (headings and dividers with nothing under them dropped).
	tb_kit_tidy(items) {
		const kit = this.tb && this.tb.kit;
		if (kit && kit.toolbar && typeof kit.toolbar.tidy === "function") return kit.toolbar.tidy(items);
		return (items || []).filter((item) => item && !item.divider && !item.heading);
	},

	// ------------------------------------------------------------------ the phone's sheet

	tb_open_sheet() {
		const kit = this.tb && this.tb.kit;
		if (!kit) {
			frappe.show_alert({ message: __("One moment: the planner is still loading."), indicator: "blue" }, 4);
			return;
		}
		this.tb.sheet = kit.toolbar.sheet({
			title: __("Filters and tools"),
			max_width: PPTB.phone_max,
			return_focus: this.$tb_more[0],
			// The bar's own filters (and search) move in, so they keep their values and handlers.
			top: (slot) => {
				this.$filters.addClass("pp-tb-in-sheet").appendTo(slot);
			},
			sections: () => this.tb_sheet_sections(),
			on_close: () => {
				this.$filters.removeClass("pp-tb-in-sheet").insertBefore(this.$alerts);
				this.tb.sheet = null;
			},
		});
	},

	tb_close_sheet() {
		if (this.tb && this.tb.sheet) this.tb.sheet.close("user");
	},

	// The sheet's lists: the chips, + Add, More's four sections and the page's own buttons. A switch
	// (a check mark or a radio) keeps the sheet open and redraws it; anything else closes it first.
	tb_sheet_sections() {
		const keep = (items) => items.map((item) => Object.assign({}, item, { keep_open: typeof item.checked === "boolean" }));
		const alerts = this.tb_alerts().map((alert) => ({
			label: `${alert.count} ${alert.label}`,
			tone: alert.tone,
			title: alert.title,
			checked: "pressed" in alert ? alert.pressed : undefined,
			on_click: alert.open,
		}));
		return [
			{ title: __("Alerts"), items: keep(alerts) },
			{ title: __("Add"), items: this.tb_add_items() },
		]
			.concat(this.tb_more_sections().map((section) => ({ title: section.title, items: keep(section.items) })))
			.concat([
				{
					title: __("This page"),
					items: [
						{ label: __("How to read the planner"), on_click: () => this.p6a_legend() },
						{ label: __("Refresh"), on_click: () => this.load() },
					],
				},
			]);
	},
};

Object.assign(ProjectPlanner.prototype, PPTB_METHODS);

// ====================================================================== Utilization (v1.687.0, Capacity's table since P7)
//
//   /desk/project-planner/crew/2026-10-12?capacity=1
//
// Utilization was a view of its own (v1.687.0, after a button that left the planner for the Crew
// Utilization script report); the planner UX redesign (P7, D12) merged it with the Heatmap into Capacity,
// so this is now the table under the heatmap. It reads the report itself (frappe.desk.query_report.run, so
// the report's own roles decide who may see it, and both always show the same figures): the heatmap's
// eight weeks, a row per person per week, with capacity, booked and clocked hours and the two percentages.
// A week booked past capacity carries an "Over" chip and one at 75% or more "Near full", so the color is
// never the only sign. Every column the view had is here. The weeks are whole weeks now (they begin on the
// site's first weekday, as the report's do), so the month view's partial first and last weeks are gone.
//
// The Project, Project manager and Group filters ride along in the address as everywhere: Group narrows
// the table to that group's people; Project and Project manager do not apply to a person's whole week, so
// they are hidden on Capacity and kept in the address for the next screen. "Open the full report" opens
// the report on the same weeks and group, for its chart and its split of booked hours.
//
// Who may see it is the report's own rule (query_report.get_report_doc: the Report's roles, then
// "report" permission on its ref doctype, Task). The table's section is there only for someone the boot
// says passes both (util_allowed): everyone else sees the heatmap alone, with nothing to ask for. Someone
// whose roles the server then refuses, or who arrives by the old /utilization address without access, gets
// the no-access panel in its place, not frappe's red dialog: the rows are asked for through the kit's
// quiet call, which shows no dialog of its own, and a refusal is drawn in the page.

const PPU = {
	run: "frappe.desk.query_report.run",
	granularity: "Week",
	ref_doctype: "Task",
};

const PPU_METHODS = {
	// The report's two gates, as the boot knows them (v16.50): allowed_reports lists the reports the
	// user's roles open, and can_get_report the doctypes they may report on. A boot without either
	// (an older Desk) leaves the server to decide.
	util_allowed() {
		const boot = frappe.boot || {};
		const reports = boot.allowed_reports;
		if (reports && typeof reports === "object" && !Object.prototype.hasOwnProperty.call(reports, PP4.report)) {
			return false;
		}
		try {
			if (frappe.model && typeof frappe.model.can_get_report === "function") {
				return !!frappe.model.can_get_report(PPU.ref_doctype);
			}
		} catch (e) {
			// No can_get_report list on this boot: the server's answer decides.
		}
		return true;
	},

	// `token`: the load this belongs to (load_capacity's), so an answer that arrives after the person has
	// moved on draws nothing.
	load_utilization(token) {
		const { start, end } = this.range();
		const filters = { from_date: pp_ymd(start), to_date: pp_ymd(end), granularity: PPU.granularity };
		if (!this.util_allowed()) {
			// Without access there is no section to draw, and the server is asked nothing; arrived at by the
			// old Utilization address (?capacity=utilization), the no-access panel says why it is not there.
			this.util =
				this.capacity_asked === "utilization"
					? { denied: true, filters, rows: [] }
					: { hidden: true, filters, rows: [] };
			this.render_utilization();
			return Promise.resolve();
		}
		const args = { report_name: PP4.report, filters: JSON.stringify(filters) };
		// The kit's quiet call (sf_kit's call on its quiet path) shows no dialog: a refusal is the
		// page's to draw. Before the kit has loaded it is waited for; without it, a plain call.
		return this.p6a_require(() => {
			const kit = window.planner_kit;
			return kit && typeof kit.quiet_call === "function"
				? kit.quiet_call(PPU.run, args)
				: Promise.resolve(frappe.call({ method: PPU.run, args })).then((r) => r && r.message);
		})
			.then((answer) => {
				if (token !== this.request) return;
				answer = answer || {};
				this.util = { filters, rows: Array.isArray(answer.result) ? answer.result : [] };
				this.render_utilization();
			})
			.catch((err) => {
				if (token !== this.request) return;
				const refused = !!err && (err.exc_type === "PermissionError" || err.status === 403);
				this.util = refused ? { denied: true, filters, rows: [] } : null;
				this.render_utilization();
			});
	},

	// A percentage as text. With `flag` (Planned %), a chip with its word when it needs attention,
	// decided on the raw value as the report decides it: 100.4% is over, though it reads "100%".
	util_pct_html(value, flag) {
		if (value == null || value === "") return `<span class="pp-util-none">${pp_esc(__("No hours"))}</span>`;
		const raw = Number(value) || 0;
		const pct = Math.round(raw);
		if (flag && raw > 100) return `<span class="pp-chip pp-red">${pp_esc(__("{0}% · Over", [pct]))}</span>`;
		if (flag && raw >= 75) return `<span class="pp-chip pp-amber">${pp_esc(__("{0}% · Near full", [pct]))}</span>`;
		return pp_esc(`${pct}%`);
	},

	render_utilization() {
		const data = this.util;
		// Nothing asked for yet (a filter picked while the first answer is on its way): it draws then.
		if (data === undefined) return;
		this.capacity_shell();
		const $box = this.$cap_util.empty();
		if (data && data.hidden) {
			// No access to the report and nobody asked for it: the heatmap stands alone.
			$box.hide();
			return;
		}
		$box.show();
		if (!data) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("Utilization could not be loaded. Refresh the planner and try again."))
				.appendTo($box);
			return;
		}
		if (data.denied) {
			// The kit's no-access panel (sf_kit's state markup), or the same words without the kit.
			const kit = window.planner_kit;
			const title = __("You don't have access to the Crew Utilization report.");
			const $note = $('<div class="pp-section pp-empty-note pp-util-denied"></div>').appendTo($box);
			if (kit && typeof kit.state_html === "function") $note[0].innerHTML = kit.state_html("no-access", { title });
			else $note.attr("role", "status").text(title);
			return;
		}
		const rows = data.rows.filter((row) => row && (!this.group || row.group === this.group));
		const report = { from_date: data.filters.from_date, to_date: data.filters.to_date, granularity: PPU.granularity };
		if (this.group) report.group = this.group;
		const $head = $('<div class="pp-util-head"></div>').appendTo($box);
		$('<h5 class="pp-util-title"></h5>').text(__("Utilization")).appendTo($head);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Open the full report"))
			.attr("title", __("The Crew Utilization report for these weeks: its chart and booked hours by kind"))
			.on("click", () => this.p6a_route(() => frappe.set_route("query-report", PP4.report, report), false))
			.appendTo($head);
		if (!rows.length) {
			$('<div class="pp-section pp-empty-note"></div>')
				.text(__("Nobody to show for these weeks. Add the people you schedule under Planner Resources."))
				.appendTo($box);
			return;
		}
		const hours = (value) => pp_esc(`${pp_hours(value)}h`);
		const body = rows.map((row, index) => {
			const first = index === 0 || rows[index - 1].resource !== row.resource;
			return `<tr class="${first ? "pp-util-first" : ""}">
				<th scope="row">${first ? pp_esc(row.label || row.resource) : `<span class="pp-sr">${pp_esc(row.label || row.resource)}</span>`}</th>
				<td>${pp_esc(row.period || "")}</td>
				<td class="pp-util-num">${hours(row.capacity)}</td>
				<td class="pp-util-num">${hours(row.booked)}</td>
				<td class="pp-util-num">${hours(row.actual)}</td>
				<td class="pp-util-num">${this.util_pct_html(row.utilization, true)}</td>
				<td class="pp-util-num">${this.util_pct_html(row.actual_pct, false)}</td>
			</tr>`;
		});
		const th = (text, num) => `<th scope="col"${num ? ' class="pp-util-num"' : ""}>${pp_esc(text)}</th>`;
		const $scroll = $('<div class="pp-section pp-util-scroll"></div>').appendTo($box);
		$scroll[0].innerHTML = `<table class="pp-util">
			<caption class="pp-sr">${pp_esc(__("Utilization, {0}", [this.title()]))}</caption>
			<thead><tr>${th(__("Person"))}${th(__("Week"))}${th(__("Capacity"), true)}${th(__("Booked"), true)}${th(
				__("Actual (kiosk)"),
				true
			)}${th(__("Planned %"), true)}${th(__("Actual %"), true)}</tr></thead>
			<tbody>${body.join("")}</tbody></table>`;
		$('<p class="pp-util-note"></p>')
			.text(
				__(
					"Planned % is booked hours against capacity; Over is past 100%, Near full 75% or more. Actual is the hours clocked at the kiosk on customer jobs."
				)
			)
			.appendTo($box);
		// Arrived by the old Utilization address: the table is what they came for.
		if (this.capacity_asked === "utilization" && !this.capacity_scrolled) {
			this.capacity_scrolled = true;
			if ($box[0] && typeof $box[0].scrollIntoView === "function") $box[0].scrollIntoView({ block: "start" });
		}
	},
};

Object.assign(ProjectPlanner.prototype, PPU_METHODS);

// ====================================================================== P4: the tasks that follow, Draft schedules, "Notify crew?"
//
// P4 of the planner UX redesign (docs/planner-ux-redesign.md; Nik's D8, D10, D11 and D15, approved
// 2026-10-21). Three things, all on the page's one write path (commit, send, p6b_commit_many):
//
//   - **The tasks that follow move with a task** (D10, D15). A drag, a resize, a dialog's dates, Next
//     free day, a booked suggestion or Tap to move sends dependents=1, and the server moves every task
//     that waits on it by the working days its end moved, later or earlier, in the same request: one
//     conflict check, one reason, one Undo. Holding Ctrl (Cmd on a Mac) as the card or its edge is
//     dropped moves just that one task. A multi-select move does the same through move_many. The old
//     "Shift N tasks that follow?" offer is gone.
//   - **A Draft schedule drafts every write** (D8). A project whose schedule is not published (P3's
//     Project.custom_schedule_status; blank reads Draft) keeps every change to its tasks as a draft:
//     send() adds draft="auto" and the server decides per task, so nothing needs switching on. The page
//     always reads with draft=1, so drafts show, another scheduler's too ("Draft · Clegg"). The strip
//     under the toolbar lists each project with drafts: Publish… on a Draft schedule runs
//     publish_project_schedule (one reason for the lot, the baseline set, the crew told once), and
//     Discard drops its drafts. The project peek offers Publish schedule… and Set/Reset baseline….
//     The board-wide switch, More ▸ Tools ▸ "Hold all my changes" (Nik named it on 2026-10-21), still
//     holds every change, on any project.
//   - **"Notify crew?"** (D11). On a published schedule, a change to the dates or crew of a task
//     somebody is on asks once per gesture, however many tasks it moves. Notify is the default when the
//     change touches today through the 14th day (old days or new), Don't notify beyond that; Enter
//     takes the default and Esc puts the change back. The answer goes as notify=1 or 0, and Undo sends
//     the same answer again.
//
// Hooks into the class above, one line each: as4_prepare (commit, p6b_commit_many), as4_decorate
// (send), as4_undo (undo), as4_solo (on_up, p6b_pointer_up), as4_project_actions
// (p6a_open_project); render_draft_bar and decorate_drafts in the Phase 3B block draw the strip and
// the chips for every draft now, not only in hold mode.

const PPAS = {
	// D11: Notify is the default for a change that touches one of the next 14 days, today included.
	notify_days: 14,
	// The writes that can move a task's days, and so carry the tasks that follow it.
	date_methods: ["save_task", "swap_crew"],
	// The most projects the strip names before it says "and N more".
	strip_max: 4,
	published: "Published",
};

// Pure helpers: no frappe, no page. scripts/test_planner_autoshift.js runs them under node, and the
// Maintenance Planner keeps an identical copy of notify_default (MPAS_PURE). The window is
// api/project_planner.notify_window, kept in step.
const PPAS_PURE = {
	// The first and last day of "the next 14 days".
	notify_window(today) {
		return [today, PP6B_PURE.ymd_add(today, PPAS.notify_days - 1)];
	},
	// Whether "Notify crew?" defaults to Notify: one of `spans` ([first, last], old days or new)
	// touches the window. A task moved from next week to March is news to its crew too.
	notify_default(spans, today) {
		const [first, last] = PPAS_PURE.notify_window(today);
		return (spans || []).some((span) => !!(span && span[0] && span[0] <= last && (span[1] || span[0]) >= first));
	},
	// A card's days as [first, last], or null for an undated one.
	span_of(card) {
		if (!card || !card.start) return null;
		return [card.start, card.end && card.end >= card.start ? card.end : card.start];
	},
	// The days a write gives the card, as api/project_planner.plan_span works them out: a start alone
	// keeps the length (an undated task becomes one day here), an end alone keeps the start.
	new_span(card, args) {
		const old = PPAS_PURE.span_of(card);
		const start = (args && (args.start || args.date)) || "";
		const end = (args && args.end) || "";
		if (start && end) return [start, end];
		if (start) return old ? [start, PP6B_PURE.ymd_add(start, PP6B_PURE.ymd_diff(old[0], old[1]))] : [start, start];
		if (end) return [old ? old[0] : end, end];
		return old;
	},
	// The board-wide hold drafts everything; otherwise the server drafts per project (auto).
	draft_value(hold) {
		return hold ? 1 : "auto";
	},
	// The Undo entries for the tasks that moved along: each back to the days it had.
	undo_items(moves) {
		return (moves || [])
			.filter((move) => move && move.task && move.from)
			.map((move) => ({ task: move.task, start: move.from, end: move.from_end || move.from }));
	},
	// The people on a write's task after it: the crew it had, the crew it sends, the person it adds.
	people_after(card, method, args) {
		const people = new Set(((card && card.crew) || []).map((member) => member.resource).filter(Boolean));
		if (args && args.crew !== undefined) {
			people.clear();
			let rows = args.crew;
			if (typeof rows === "string") {
				try {
					rows = JSON.parse(rows);
				} catch (e) {
					rows = [];
				}
			}
			(rows || []).forEach((row) => row && row.resource && people.add(row.resource));
		}
		if (method === "add_crew" && args && args.resource) people.add(args.resource);
		if (method === "swap_crew" && args) {
			if (args.from_resource) people.delete(args.from_resource);
			if (args.to_resource) people.add(args.to_resource);
		}
		return people;
	},
};

const PPAS_METHODS = {
	// ------------------------------------------------------------------ schedule state

	// The project's schedule as get_planner gives it ({status, published_on, baseline_set_on}), or null
	// before the P3 fields exist (then nothing here changes how a write is sent).
	as4_schedule(project) {
		const known = project && this.by_project && this.by_project[project];
		return (known && known.schedule) || null;
	},

	as4_draft_project(project) {
		const schedule = this.as4_schedule(project);
		return !!(schedule && schedule.status !== PPAS.published);
	},

	// Whether a write to this card saves live on a published schedule: the writes that ask "Notify crew?".
	as4_published(card) {
		if (!card || this.draft_on || card.new_task) return false;
		const schedule = this.as4_schedule(card.project);
		return !!(schedule && schedule.status === PPAS.published);
	},

	as4_project_title(project) {
		const known = this.by_project && this.by_project[project];
		return (known && known.title) || project;
	},

	// ------------------------------------------------------------------ the write path

	// What one write changes, for as4_prepare: the card, its days before and after, whether those
	// moved (and so carry the tasks that follow) and who is on it before and after.
	as4_change(card, method, args) {
		const from = PPAS_PURE.span_of(card);
		const to = PPAS_PURE.new_span(card, args);
		const moved = !!(to && (!from || to[0] !== from[0] || to[1] !== from[1]));
		const before = new Set(((card && card.crew) || []).map((member) => member.resource).filter(Boolean));
		const after = PPAS_PURE.people_after(card, method, args);
		const recrewed = [...after].some((r) => !before.has(r)) || [...before].some((r) => !after.has(r));
		const moves = moved && PPAS.date_methods.includes(method);
		// The tasks that follow move too (unless Ctrl/Cmd): whoever is on the ones on screen is asked
		// about as well (as4_question).
		const followers = moves ? this.as4_follower_people(card && card.name) : new Set();
		return { card, from, to, moves, changes: moved || recrewed, people: new Set([...before, ...after]), followers };
	},

	// The people on the tasks on screen that wait on `name`, directly or through others (the server
	// moves those off screen too; they cannot be named here).
	as4_follower_people(name) {
		const people = new Set();
		if (!name) return people;
		const waiting = {};
		Object.values(this.by_task || {}).forEach((other) =>
			(other.depends_on || []).forEach((on) => (waiting[on] = waiting[on] || []).push(other))
		);
		const seen = new Set([name]);
		const queue = [name];
		while (queue.length) {
			(waiting[queue.shift()] || []).forEach((other) => {
				if (seen.has(other.name)) return;
				seen.add(other.name);
				queue.push(other.name);
				(other.crew || []).forEach((member) => member.resource && people.add(member.resource));
			});
		}
		return people;
	},

	// Run `go(extra)` with the extra arguments of one gesture's write: dependents=1 when it moves days
	// (unless Ctrl/Cmd was held, `opts.solo`), and the "Notify crew?" answer. With nothing to ask, `go`
	// runs at once (no flicker, and the write is sent in the same turn as before P4); after a question,
	// once it is answered. Backing out of the question runs nothing and resolves null.
	as4_prepare(changes, opts, go) {
		opts = opts || {};
		const extra = {};
		if (!opts.solo && (changes || []).some((change) => change.moves)) extra.dependents = 1;
		const question = this.as4_question(changes || [], !!opts.solo);
		if (!question) return go(extra);
		return this.as4_notify_dialog(question.notify, question.people, question.count).then((notify) =>
			notify === null ? null : go(Object.assign(extra, { notify }))
		);
	},

	// What to ask about the gesture: {notify (the default), people, count}, or null when there is
	// nothing to ask (a Draft schedule, the hold, nobody on the tasks, nothing that moves anyone).
	as4_question(changes, solo) {
		const live = changes.filter((change) => change.changes && this.as4_published(change.card));
		const people = new Set();
		live.forEach((change) => {
			change.people.forEach((resource) => people.add(resource));
			if (!solo) (change.followers || []).forEach((resource) => people.add(resource));
		});
		if (!people.size) return null;
		const spans = [];
		live.forEach((change) => {
			if (change.from) spans.push(change.from);
			if (change.to) spans.push(change.to);
		});
		return { notify: PPAS_PURE.notify_default(spans, this.today()), people: [...people], count: live.length };
	},

	as4_notify_dialog(notify, people, count) {
		return new Promise((resolve) => {
			let settled = false;
			const settle = (value) => {
				if (settled) return;
				settled = true;
				resolve(value);
			};
			const names = people.slice(0, 6).map((resource) => this.resource_label(resource));
			if (people.length > 6) names.push(__("{0} more", [people.length - 6]));
			const ask =
				count > 1
					? __("Tell {0} about these {1} changes?", [names.join(", "), count])
					: __("Tell {0} about this change?", [names.join(", ")]);
			const why = notify
				? __("It touches the next two weeks, so Notify is the default.")
				: __("It is more than two weeks out, so Don't notify is the default.");
			const dialog = new frappe.ui.Dialog({
				title: __("Notify crew?"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "notify",
						options: `<p>${pp_esc(ask)}</p><p class="pp-stop-sub">${pp_esc(why)} ${pp_esc(
							__("Esc puts the change back.")
						)}</p>`,
					},
				],
				primary_action_label: notify ? __("Notify") : __("Don't notify"),
				primary_action: () => {
					settle(notify ? "1" : "0");
					dialog.hide();
				},
				secondary_action_label: notify ? __("Don't notify") : __("Notify"),
				secondary_action: () => {
					settle(notify ? "0" : "1");
					dialog.hide();
				},
			});
			dialog.onhide = () => settle(null);
			dialog.show();
			// The default has the focus, so Enter takes it (and a keyboard user lands on it).
			if (typeof dialog.get_primary_btn === "function") dialog.get_primary_btn().trigger("focus");
		});
	},

	// Ctrl (Cmd on a Mac) held as a card or an edge is dropped: just that task moves.
	as4_solo(e) {
		return !!(e && (e.ctrlKey || e.metaKey));
	},

	// send()'s answer, before its toast: the tasks that moved along go into the Undo entry and the
	// message, the ones left where they were are named, and a drafted change says it is a draft.
	as4_decorate(result, opts) {
		if (!result) return;
		const many = !!(opts.snapshot && opts.snapshot.p6b === "many");
		const shifted = many ? (result.moved || []).filter((move) => move.dependent) : result.shifted || [];
		shifted.forEach((move) => {
			if (move.modified) this.modified[move.task] = move.modified;
		});
		if (shifted.length && opts.snapshot) {
			const items = PPAS_PURE.undo_items(shifted);
			if (many) opts.snapshot.items = (opts.snapshot.items || []).concat(items);
			else opts.snapshot.shifted = items;
		}
		if (shifted.length && opts.message) {
			opts.message += ` ${
				shifted.length === 1 ? __("and 1 task that follows") : __("and {0} tasks that follow", [shifted.length])
			}`;
		}
		const left = result.shift_skipped || [];
		if (left.length) {
			const named = left
				.slice(0, 3)
				.map((entry) => `${entry.subject || entry.task}: ${entry.reason}`)
				.join("; ");
			frappe.show_alert(
				{ message: __("{0} task(s) that follow stayed where they were. {1}", [left.length, named]), indicator: "orange" },
				10
			);
		}
		if (result.drafted && !this.draft_on && opts.message) {
			opts.message += ` (${__("a draft: the schedule is not published yet")})`;
		}
	},

	// Undo of a change that moved the tasks that follow: one move_many puts every task back on its
	// days (the same Notify answer as the change), then, when the change was more than dates (a crew
	// handover with a move, a dialog save), the plain Undo's save puts the rest of the task back.
	as4_undo(snap) {
		if (!snap || snap.p6b || !(snap.shifted || []).length) return false;
		const modified = (name) => this.modified[name] || (this.by_task[name] || {}).modified || "";
		const items = (snap.start ? [{ task: snap.task, start: snap.start, end: snap.end || snap.start }] : []).concat(snap.shifted);
		const args = {
			moves: JSON.stringify(items.map((item) => ({ task: item.task, modified: modified(item.task), start: item.start, end: item.end }))),
		};
		if (snap.notify != null) args.notify = snap.notify;
		const opts = { message: __("Undone: {0}", [snap.subject]), auto_reason: __(PP.undo_reason) };
		this.p6b_send("move_many", args, opts)
			.then((result) => {
				if (!result) return null;
				(result.moved || []).forEach((move) => {
					if (move.modified) this.modified[move.task] = move.modified;
				});
				if (snap.dates_only) return result;
				const rest = {
					task: snap.task,
					modified: modified(snap.task),
					expected_time: snap.expected_time,
					crew_size: snap.crew_size,
					crew: JSON.stringify(snap.crew),
					credentials: JSON.stringify(snap.credentials),
				};
				if (snap.tentative != null) rest.tentative = snap.tentative;
				if (snap.equipment) rest.equipment = JSON.stringify(snap.equipment);
				if (snap.notify != null) rest.notify = snap.notify;
				return this.send("save_task", rest, { auto_reason: __(PP.undo_reason) });
			})
			.finally(() => this.load());
		return true;
	},

	// ------------------------------------------------------------------ the strip and Publish

	// The strip's lines: the hold first (as Phase 3B drew it), then each project with drafts. A Draft
	// schedule offers Publish… (the project's own publish: baseline, status, one notice per person); a
	// published project with held changes offers Publish (its drafts only). Both offer Discard.
	as4_strip_entries() {
		const drafts = (this.data && this.data.drafts) || {};
		const entries = Object.entries(drafts.projects || {})
			.filter(([, entry]) => entry && Number(entry.count) > 0)
			.map(([project, entry]) => ({
				project,
				title: this.as4_project_title(project),
				count: Number(entry.count) || 0,
				shared: Number(entry.shared) || 0,
				draft_schedule: this.as4_draft_project(project),
			}))
			// While the hold is on, its own line carries the held changes; only Draft schedules get theirs.
			.filter((entry) => !this.draft_on || entry.draft_schedule)
			.sort((a, b) => Number(b.draft_schedule) - Number(a.draft_schedule) || String(a.title).localeCompare(String(b.title)));
		// One project on screen whose schedule is Draft: say so even before anything is drafted, so
		// Publish… is one click away.
		const only = this.project;
		if (only && this.as4_draft_project(only) && !entries.some((entry) => entry.project === only)) {
			entries.unshift({ project: only, title: this.as4_project_title(only), count: 0, shared: 0, draft_schedule: true });
		}
		return entries;
	},

	as4_strip_line(entry) {
		const counted =
			entry.shared > 0
				? __("{0} change(s), {1} by others", [entry.count, entry.shared])
				: __("{0} change(s)", [entry.count]);
		if (entry.draft_schedule) {
			return entry.count
				? __("{0} · Draft schedule · {1}", [entry.title, counted])
				: __("{0} · Draft schedule: changes here stay drafts until you publish", [entry.title]);
		}
		return __("{0} · {1} held, not published yet", [entry.title, counted]);
	},

	as4_render_strip($bar) {
		const entries = this.as4_strip_entries();
		if (!entries.length) return false;
		const shown = entries.slice(0, PPAS.strip_max);
		shown.forEach((entry) => {
			const $line = $('<div class="pp-draft-line"></div>').appendTo($bar);
			$('<span class="pp-draft-text"></span>').text(this.as4_strip_line(entry)).appendTo($line);
			$('<span class="pp-spacer"></span>').appendTo($line);
			if (!this.data || !this.data.can_edit) return;
			$('<button type="button" class="btn btn-primary btn-xs"></button>')
				.text(entry.draft_schedule ? __("Publish…") : __("Publish"))
				.attr(
					"title",
					entry.draft_schedule
						? __("Publish this schedule: the drafts go live, the baseline is set and each person is told once")
						: __("Publish these held changes")
				)
				.on("click", () => (entry.draft_schedule ? this.as4_confirm_publish(entry) : this.as4_publish_held(entry)))
				.appendTo($line);
			if (entry.count) {
				$('<button type="button" class="btn btn-default btn-xs"></button>')
					.text(__("Discard"))
					.on("click", () => this.as4_discard(entry))
					.appendTo($line);
			}
		});
		if (entries.length > shown.length) {
			$('<div class="pp-draft-line pp-stop-sub"></div>')
				.text(__("and {0} more project(s) with drafts", [entries.length - shown.length]))
				.appendTo($bar);
		}
		return true;
	},

	as4_confirm_publish(entry) {
		const lines = [
			entry.count
				? entry.shared
					? __("{0} drafted change(s) go live, {1} of them by other schedulers.", [entry.count, entry.shared])
					: __("{0} drafted change(s) go live.", [entry.count])
				: __("Nothing is drafted; the schedule is published as it stands."),
			__("Each task gets its baseline (its days now), the crew are told once, and calendars pick the tasks up."),
			__("After this, changes save straight away and ask whether to notify the crew."),
		];
		frappe.confirm(
			`<p>${pp_esc(__("Publish the schedule of {0}?", [entry.title]))}</p><ul>${lines
				.map((line) => `<li>${pp_esc(line)}</li>`)
				.join("")}</ul>`,
			() => this.as4_publish(entry.project)
		);
	},

	// publish_project_schedule: one conflict check and one reason for the project's drafts.
	as4_publish(project, reason) {
		const args = reason ? { project, reason } : { project };
		return Promise.resolve(
			frappe.call({
				method: `${PP.api}.publish_project_schedule`,
				args,
				freeze: true,
				freeze_message: __("Publishing the schedule…"),
			})
		)
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.needs_reason) {
					if (reason) {
						frappe.msgprint(__("Nothing was published. Refresh the planner and try again."));
						return null;
					}
					return this.ask_reason(result.conflicts || {}).then((given) => (given ? this.as4_publish(project, given) : null));
				}
				this.show_publish_result(result);
				const title = this.as4_project_title(project);
				const schedule = result.schedule || {};
				if (schedule.status === PPAS.published) {
					frappe.show_alert(
						{
							message: __("{0}: schedule published, {1} task(s) given a baseline.", [title, Number(result.baselined) || 0]),
							indicator: "green",
						},
						8
					);
				} else if (Number(result.drafts_left) > 0) {
					frappe.show_alert(
						{
							message: __("{0} stays Draft: {1} draft(s) are still to publish.", [title, result.drafts_left]),
							indicator: "orange",
						},
						10
					);
				}
				this.undo_stack = [];
				this.update_undo_button();
				return this.load();
			})
			.catch(() => null);
	},

	// A published project's held changes: publish_drafts for that project only.
	as4_publish_held(entry, reason) {
		const args = reason ? { project: entry.project, reason } : { project: entry.project };
		return Promise.resolve(
			frappe.call({ method: `${PP.api}.publish_drafts`, args, freeze: true, freeze_message: __("Publishing your changes…") })
		)
			.then((r) => {
				const result = (r && r.message) || {};
				if (result.needs_reason) {
					if (reason) {
						frappe.msgprint(__("Nothing was published. Refresh the planner and try again."));
						return null;
					}
					return this.ask_reason(result.conflicts || {}).then((given) => (given ? this.as4_publish_held(entry, given) : null));
				}
				this.show_publish_result(result);
				this.undo_stack = [];
				this.update_undo_button();
				return this.load();
			})
			.catch(() => null);
	},

	as4_discard(entry) {
		const question =
			entry.shared > 0
				? __("Discard the {0} drafted change(s) on {1}, {2} of them by other schedulers? The tasks stay as they are.", [
						entry.count,
						entry.title,
						entry.shared,
				  ])
				: __("Discard the {0} drafted change(s) on {1}? The tasks stay as they are.", [entry.count, entry.title]);
		frappe.confirm(question, () =>
			Promise.resolve(frappe.call({ method: `${PP.api}.discard_drafts`, args: { project: entry.project } }))
				.then((r) => {
					const done = ((r && r.message) || {}).discarded || 0;
					frappe.show_alert({ message: __("{0} draft(s) discarded", [done]), indicator: "blue" }, 5);
					this.undo_stack = [];
					this.update_undo_button();
					return this.load();
				})
				.catch(() => null)
		);
	},

	// ------------------------------------------------------------------ baseline

	// Reset baseline (or the first Set baseline of a project published without one): ask the server how
	// many tasks change, say so, then copy.
	as4_reset_baseline(project) {
		const title = this.as4_project_title(project);
		return Promise.resolve(frappe.call({ method: `${PP.api}.reset_baseline`, args: { project, dry_run: 1 } }))
			.then((r) => {
				const preview = (r && r.message) || {};
				const count = Number(preview.changed) || 0;
				if (!count) {
					frappe.show_alert({ message: __("{0}: the baseline already matches every task's days.", [title]), indicator: "blue" }, 6);
					return null;
				}
				frappe.confirm(
					__("Reset the baseline of {0}? {1} task(s) get their days now as their baseline.", [title, count]),
					() =>
						Promise.resolve(frappe.call({ method: `${PP.api}.reset_baseline`, args: { project } }))
							.then((done) => {
								const changed = Number(((done && done.message) || {}).changed) || 0;
								frappe.show_alert({ message: __("{0}: baseline set for {1} task(s).", [title, changed]), indicator: "green" }, 6);
								return this.load();
							})
							.catch(() => null)
				);
				return null;
			})
			.catch(() => null);
	},

	// The project peek's schedule actions: Publish schedule… on a Draft schedule, and Set or Reset
	// baseline. None before the P3 fields exist, or for someone who cannot change tasks.
	as4_project_actions(project) {
		const schedule = this.as4_schedule(project);
		if (!schedule || !this.data || !this.data.can_edit) return [];
		const actions = [];
		if (schedule.status !== PPAS.published) {
			const drafts = ((this.data.drafts || {}).projects || {})[project] || {};
			actions.push({
				label: __("Publish schedule…"),
				on_click: () =>
					this.as4_confirm_publish({
						project,
						title: this.as4_project_title(project),
						count: Number(drafts.count) || 0,
						shared: Number(drafts.shared) || 0,
					}),
			});
		}
		actions.push({
			label: schedule.baseline_set_on ? __("Reset baseline…") : __("Set baseline…"),
			on_click: () => this.as4_reset_baseline(project),
		});
		return actions;
	},

	// The name on another scheduler's draft: their first name, from the boot's user info when it has it.
	as4_drafter(user) {
		let name = user;
		try {
			if (frappe.user && typeof frappe.user.full_name === "function") name = frappe.user.full_name(user) || user;
		} catch (e) {
			name = user;
		}
		return String(name || "").split(/[\s@]/)[0] || user;
	},
};

Object.assign(ProjectPlanner.prototype, PPAS_METHODS);
