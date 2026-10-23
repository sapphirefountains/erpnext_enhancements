// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Maintenance Planner: a month/week calendar of maintenance visits, moved by dragging cards.
//
//   /desk/maintenance-planner                    this month
//   /desk/maintenance-planner/month/2026-10-01   the month holding that day
//   /desk/maintenance-planner/week/2026-10-05    the week holding that day
//   /desk/maintenance-planner/crew/2026-10-05    the crew timeline for the week holding that day
//   /desk/maintenance-planner/simple             Simple mode: every site, a card each, Done and Coming up,
//                                                one search box (the MPS block, at the end)
//
// Every move between months, weeks and views is a route, so Back and Forward step through them
// (Nik's rule: never break Back/Forward). The page moves only with frappe.set_route; Back and
// Forward re-render through on_page_show -> handle_route.
//
// The Technician filter rides along on every one of those as the query (S5c):
// /desk/maintenance-planner/week/2026-10-05?technician=<user> (__none__ for Unassigned). A link
// with it opens the planner on that technician, and a reload keeps it. Picking a technician rewrites
// the address in place; it is not a screen of its own. See "the filter in the address" below. The
// two boxes beside it (Projected visits, Project work) are display switches and stay remembered only.
//
// Cards come from erpnext_enhancements.api.maintenance_planner.get_planner:
//   visit      a Sapphire Maintenance Record. It carries `technician` (the lead, who fills it in),
//              `crew` ([{user, name, hours}]: the others booked on it), `planned_hours`, `full_day` and
//              `hours` (the length each person is booked for). A draft nobody has started can be dragged; that
//              rewrites its Scheduled Visit Date and/or its technician (move_visit).
//   projected  a visit the nightly scheduler has not drafted yet, worked out from the contract.
//              The first one of a series is the contract's stored next visit and can be dragged
//              (move_projected); the ones after it follow whenever that visit actually happens.
//              A projected visit follows the technician on the site's Maintenance Profile, so only
//              its day moves.
// Visits with no date at all wait in the Unscheduled tray until somebody drags them onto a day.
//
// Maintenance and Projects share technicians, so the same response also carries `bookings`: each
// technician's project tasks, rental crew tasks and travel days, their free hours and days off,
// and (when the engine provides them) their drive time, all from the shared availability engine
// (project_enhancements.crew_availability). The bookings are read-only here. Since P10 of the planner
// UX redesign (D13) they are one thin grey busy strip on each technician's day ("Project: 8h", the
// tasks on hover, a click opens the person's week); More > Display > Project work as cards brings
// back a card each, shown as Project / Rental / Travel cards on their pillar's edge (Build, Rent,
// Neutral; maintenance_planner.css) that open the Task or Travel Trip. Either way they are never
// dragged. The free hours are the engine's figure and must match the Project Planner's, so this page
// never works them out itself. Projects moves its own bookings on the Project Planner.
//
// What a scheduler sees and can do, the same as on the Project Planner:
//   - "Resources available" above the calendar    one summary line ("Austin: 3h free Tue · Off Thu–Fri");
//                                                 Show opens each technician's hours free, Full, Over,
//                                                 Holiday, Time off or Not a work day, per day
//   - days off on the calendar                    one technician picked: their days off are shaded
//                                                 and labeled; All: a badge for each person off
//   - Crew view (a route, /crew/<date>)           a row per technician, a column per day of the
//                                                 week, their visits, bookings and hours in each
//   - drag a visit to another day                 move_visit(date)
//   - in the crew view, drag a visit to another
//     technician's row                            move_visit(technician and date)
//   - drag a technician from the panel onto a
//     visit card                                  add_crew (the visit's crew grows); a visit
//                                                 with nobody on it gets them as technician
//   - a multi-person visit                        shows in every crew member's row; dragging
//                                                 one person's chip to another row moves only
//                                                 that person (move_visit with from_user when
//                                                 the dragged person is a helper)
//   - the card's dialog                           technician, crew (with optional hours each),
//                                                 planned hours and Full day
//   - the route icon on a technician's day        the Project Planner's route view for that day
// Overbooking warns and never blocks: when a move would put a technician over their hours, on a
// day off or in two places at once, the server answers needs_reason instead of saving, and the
// page asks for a reason and sends the move again with it. The reason lands on the visit's (or,
// for a projected visit, the contract's) timeline. Undo steps back through the last 20 moves.
//
// Dragging is done with pointer events rather than HTML5 drag and drop, which phones and
// tablets do not support. A touch has to rest on a card for a moment before it lifts, so a
// swipe still scrolls the page. Clicking or pressing Enter on a card opens it, and its dialog
// can move it too, for anyone who would rather type a date than drag.

const MP = {
	route: "maintenance-planner",
	api: "erpnext_enhancements.api.maintenance_planner",
	views: ["month", "week", "crew"],
	tech_key: "ee_maintenance_planner_technician",
	projected_key: "ee_maintenance_planner_projected",
	project_key: "ee_maintenance_planner_project_work",
	// "Resources available" starts collapsed to a summary line (P10, §4.9). A new key, so the open state the
	// old default left in a browser is not mistaken for a choice.
	resources_key: "ee_maintenance_planner_resources",
	// Project work as one card each, instead of the busy strip (P10, D13). Off until it is ticked.
	cards_key: "ee_maintenance_planner_project_cards",
	// The filter the address carries (S5c): ?technician=<user>, or __none__ for Unassigned.
	url_filters: ["technician"],
	// The longest value the address may give it: a document name's limit.
	url_value_max: 140,
	hold_ms: 300,
	drag_px: 6,
	undo_max: 20,
	undo_reason: "Undo on the Maintenance Planner",
	// The Project Planner page the route icon opens: /project-planner/route/<resource>/<date>.
	project_route: "project-planner",
	// Labels for one-off visits; any other label is a seasonal visit.
	extra_labels: ["Extra Visit", "Chemistry Follow-Up"],
	status_order: { draft: 0, move: 0, pending: 1, projected: 2, done: 3, booking: 4 },
	// Engine booking kind -> chip text. The engine calls a project task "task".
	booking_kinds: { task: "Project", rental: "Rental", travel: "Travel" },
};

// The endpoints the page calls, and no others.
MP.methods = {
	get_planner: `${MP.api}.get_planner`,
	move_visit: `${MP.api}.move_visit`,
	move_projected: `${MP.api}.move_projected`,
	// Adding a helper to a visit's crew is its own POST: dropping a chip on a visit sends it.
	add_crew: `${MP.api}.add_crew`,
	// A Fountain Move task's day and crew (MPFM below): the Project Planner's write, on this page.
	move_fountain_move: `${MP.api}.move_fountain_move`,
};

const mp_ymd = (m) => m.format("YYYY-MM-DD");
const mp_esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));
// 3 -> "3", 2.5 -> "2.5", 1.25 -> "1.25": the engine's fmt_hours, so both sides print alike.
const mp_hours = (value) => String(Math.round((Number(value) || 0) * 100) / 100);
const mp_when = (ymd) => moment(ymd, "YYYY-MM-DD").format("ddd, MMM D");
// 70 -> "1h 10m", 25 -> "25m", 120 -> "2h": drive time as people say it.
const mp_drive = (minutes) => {
	const total = Math.max(0, Math.round(Number(minutes) || 0));
	const h = Math.floor(total / 60);
	const m = total % 60;
	if (!h) return `${m}m`;
	return m ? `${h}h ${m}m` : `${h}h`;
};
// Colors come from records people edit; only a plain hex value goes into a style attribute.
// A technician's color (or Color by's) is the one color the page writes inline, and only ever as the
// custom property `style="--mp-color: <hex or hsl>"`, which maintenance_planner.css paints with
// (S3.4e); tests/test_s3_page_css.py allows exactly that pattern and no other inline color.
const mp_color = (value, fallback) => (/^#[0-9a-fA-F]{3,8}$/.test(String(value || "")) ? value : fallback);
// A design token's value, for the printed page, which is drawn in a window of its own and takes a
// color value rather than a CSS variable. The fallback is the token's own value, for a page drawn
// before sf_tokens.bundle.css (a global include) has loaded.
const mp_token_color = (name, fallback) => {
	try {
		const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
		return value || fallback;
	} catch (e) {
		return fallback;
	}
};
// The same pin-and-route glyph the Project Planner uses on a person's day.
const MP_ROUTE_ICON =
	'<svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5">' +
	'<circle cx="3.5" cy="12.5" r="1.8"/><circle cx="12.5" cy="3.5" r="1.8"/>' +
	'<path d="M5.3 12.5H10a2.5 2.5 0 0 0 0-5H6a2.5 2.5 0 0 1 0-4h4.7"/></svg>';

frappe.pages[MP.route].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Maintenance Planner"),
		single_column: true,
	});
	wrapper.maintenance_planner = new MaintenancePlanner(page);
};

frappe.pages[MP.route].on_page_show = function (wrapper) {
	if (wrapper.maintenance_planner) {
		wrapper.maintenance_planner.handle_route();
	}
};

class MaintenancePlanner {
	constructor(page) {
		this.page = page;
		this.view = "month";
		this.anchor = frappe.datetime.get_today();
		this.technician = this.load_pref(MP.tech_key, "");
		this.show_projected = this.load_pref(MP.projected_key, "1") !== "0";
		this.show_project_work = this.load_pref(MP.project_key, "1") !== "0";
		this.show_project_cards = this.load_pref(MP.cards_key, "0") === "1";
		this.panel_open = this.load_pref(MP.resources_key, "0") === "1";
		this.data = null;
		this.by_key = {};
		this.tech_by_user = {};
		this.tech_names = {};
		this.day_cards = {};
		// The newest `modified` seen for each visit: a save answers with one, and Undo needs it even
		// after the visit has moved out of the range on screen.
		this.modified = {};
		this.undo_stack = [];
		this.request = 0;
		this.drag = null;
		this.click_blocked_until = 0;

		this.page.set_secondary_action(__("Day Board"), () => frappe.set_route("maintenance-day-board"));
		this.$body = $('<div class="mp-wrap"></div>').appendTo(page.main);
		this.build_shell();
		this.init_simple(); // Simple mode: the switch at the start of the bar, and its screen
		// The person's own filter, and the one the address gave while it is on screen (S5c).
		this.own_filters = this.filters();
		this.url_filters = null;
		this.watch_filter_url();
		this.init_phase6a();
		this.init_phase6b();
		this.init_phase6c();
		this.init_phase6d();
		this.init_phase6e();
		this.init_phase_c1();
		this.init_toolbar();
		this.init_tour();
		this.bind_drag();
	}

	// ------------------------------------------------------------------ routing

	route_target() {
		const route = frappe.get_route() || [];
		if (route[0] !== MP.route) return null;
		if (route[1] === MPS.view) return { view: MPS.view, anchor: frappe.datetime.get_today() }; // Simple mode
		const view = MP.views.includes(route[1]) ? route[1] : "month";
		const day = route[2] && moment(route[2], "YYYY-MM-DD", true).isValid() ? route[2] : frappe.datetime.get_today();
		return { view, anchor: day };
	}

	handle_route() {
		if (this.p6c_before_route()) return;
		const target = this.route_target();
		if (!target) return;
		this.tour_route(); // P12: a tour from the last screen closes, and the first-visit check is made again
		this.take_route_filters();
		this.view = target.view;
		this.anchor = target.anchor;
		if (this.mps_take(target.view)) return; // Simple mode: its own screen, remembered on this device
		this.load();
	}

	go(view, anchor) {
		const route = frappe.get_route() || [];
		if (route[0] === MP.route && route[1] === view && route[2] === anchor) {
			this.load();
			return;
		}
		this.self_route = [MP.route, view, anchor].join("/");
		this.p6a_route(() => frappe.set_route(MP.route, view, anchor, this.filter_options()), true);
	}

	// ------------------------------------------------------------------ the filter in the address (S5c)
	//
	// The Technician filter is part of the address, as ?technician=, on every planner screen. The same
	// rules as the Project Planner's filters (its "filters in the address" says why each is so):
	//   - Read on every landing (take_route_filters), after the "last used" view is restored, so the
	//     address wins over it. An address that does not name it keeps the technician on screen.
	//   - What the address gave is that visit's only: not remembered, and "last used" keeps the
	//     person's own technician while it is on screen (p6c_save_last). A bare address after it
	//     brings back the person's own, except on the planner's own next screen (self_route). A
	//     technician the person picks (pick_filter) is theirs, remembered as before. One the loaded
	//     planner does not list becomes All and stops being the address's, and so does one that is
	//     the person's own anyway (a Back onto one of their own entries).
	//   - Back onto an entry the planner left bare (All, and the person's own) names nothing, so it
	//     keeps whatever technician is on screen by then, the newer one.
	//   - Picking one rewrites the address in place (write_filter_url, no new entry): a filter is not
	//     a screen, and Back steps through months, weeks and views, not through every pick. A month,
	//     a week or a view moves by set_route as it always has, carrying the filter (filter_options).
	//   - The key is consumed from route_options, so it is never written into the next route's
	//     address. Keys that are not the planner's are not its to take.

	filters() {
		return { technician: this.technician || "" };
	}

	filter_options() {
		return this.technician ? { technician: this.technician } : {};
	}

	// A value the address gives the filter, or null when it cannot take it, which ignores it. Whether
	// that person is on the list is only known once the planner has loaded (fill_technicians).
	filter_from_address(key, value) {
		if (value == null || typeof value === "object") return null;
		const text = String(value).trim();
		return text.length > MP.url_value_max ? null : text;
	}

	take_route_filters() {
		const opts = frappe.route_options;
		const named = {};
		if (opts && typeof opts === "object") {
			MP.url_filters.forEach((key) => {
				if (!Object.prototype.hasOwnProperty.call(opts, key)) return;
				const value = this.filter_from_address(key, opts[key]);
				if (value !== null) named[key] = value;
				delete opts[key];
			});
			if (!Object.keys(opts).length) frappe.route_options = null;
		}
		// The planner's own next screen (go) carries the technician on screen; with All it carries
		// nothing, and is no bare address for all that.
		const self_step = !!this.self_route && this.self_route === (frappe.get_route() || []).join("/");
		this.self_route = null;
		const own = this.own_filter_values();
		let next = null;
		if (Object.keys(named).length) {
			next = { technician: named.technician || "" };
			// The planner's own screens carry the filter on screen: nothing changes, nothing is marked.
			if (next.technician === this.filters().technician) next = null;
			else this.url_filters = Object.assign({}, next);
		} else if (this.url_filters && !self_step) {
			next = own;
			this.url_filters = null;
		}
		// The person's own technician again (a Back onto one of their own entries): theirs, not a link's.
		if (this.url_filters && (next || this.filters()).technician === own.technician) this.url_filters = null;
		if (next) {
			this.technician = next.technician;
			if (this.$tech) this.$tech.val(this.technician);
		}
		this.write_filter_url();
	}

	own_filter_values() {
		return Object.assign({ technician: "" }, this.own_filters);
	}

	// The key the address gave stops being the address's: the person picked one, or the loaded planner
	// reset it to All.
	release_filter(key) {
		if (!this.url_filters) return;
		delete this.url_filters[key];
		if (!Object.keys(this.url_filters).length) this.url_filters = null;
	}

	// A technician picked in the bar.
	pick_filter(value) {
		this.technician = value || "";
		this.own_filters = Object.assign(this.own_filter_values(), { technician: this.technician });
		this.release_filter("technician");
		this.save_pref(MP.tech_key, this.technician);
		this.write_filter_url();
		this.render();
	}

	// The page's one history write: the filter on screen into the entry on screen, in place. Only
	// while the router is on the planner's own path and never over a filter the page has not read
	// yet. The path as the router wrote it, any key that is not the planner's, the hash and
	// history.state are kept; values are encoded as v16.50's set_route encodes them, an apostrophe as
	// the browser writes it in a query (%27), or the address read back would never match.
	write_filter_url() {
		const path = window.location.pathname;
		if ((frappe.get_route() || [])[0] !== MP.route || !path.split("/").includes(MP.route)) return;
		const opts = frappe.route_options;
		if (opts && MP.url_filters.some((key) => Object.prototype.hasOwnProperty.call(opts, key))) return;
		const enc = (text) => encodeURIComponent(text).replace(/'/g, "%27");
		const pairs = [];
		const filters = this.filters();
		MP.url_filters.forEach((key) => {
			if (filters[key]) pairs.push(`${key}=${enc(filters[key])}`);
		});
		new URLSearchParams(window.location.search).forEach((value, key) => {
			if (!MP.url_filters.includes(key)) pairs.push(`${enc(key)}=${enc(value)}`);
		});
		const search = pairs.length ? `?${pairs.join("&")}` : "";
		if (search === window.location.search) return;
		window.history.replaceState(window.history.state, "", path + search + window.location.hash);
	}

	// A Back the router does not route (the planner kit closing its drawer) may land on an entry that
	// still holds the technician from before a pick; once the router has had its turn, it is updated.
	watch_filter_url() {
		window.addEventListener("popstate", () => setTimeout(() => this.write_filter_url(), 0));
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

	// The week and crew views show the week holding the anchor; the month view, the six weeks around it.
	range() {
		const first = this.first_weekday();
		const anchor = moment(this.anchor, "YYYY-MM-DD");
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
		for (let day = start.clone(); !day.isAfter(end, "day"); day.add(1, "days")) days.push(mp_ymd(day));
		return days;
	}

	shift(sign) {
		const anchor = moment(this.anchor, "YYYY-MM-DD");
		const next =
			this.view === "month" ? anchor.startOf("month").add(sign, "months") : anchor.add(sign * 7, "days");
		this.go(this.view, mp_ymd(next));
	}

	title() {
		const { start, end } = this.range();
		if (this.view === "month") return moment(this.anchor, "YYYY-MM-DD").format("MMMM YYYY");
		if (start.year() !== end.year()) return `${start.format("MMM D, YYYY")} – ${end.format("MMM D, YYYY")}`;
		if (start.month() !== end.month()) return `${start.format("MMM D")} – ${end.format("MMM D, YYYY")}`;
		return `${start.format("MMM D")} – ${end.format("D, YYYY")}`;
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

	// ------------------------------------------------------------------ shell

	build_shell() {
		const $bar = $('<div class="mp-toolbar"></div>').appendTo(this.$body);
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
		this.$title = $('<span class="mp-title" tabindex="-1"></span>').appendTo($bar);

		const $seg = $('<span class="mp-seg"></span>').appendTo($bar);
		this.$seg = $seg;
		// P10: a technician's way to their Today screen on the Project Planner (shown by p10_sync_myday).
		this.$myday = $('<button type="button" class="btn btn-default btn-sm mp-tb-myday"></button>')
			.text(__("My day"))
			.attr("title", __("Your stops and route for today and tomorrow (the Today screen)"))
			.on("click", () => this.p10_open_today())
			.hide()
			.insertAfter($seg);
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

		$('<span class="mp-spacer"></span>').appendTo($bar);
		// The filter (and 6B's search, which joins it) is one group: on a phone the group moves into
		// the "Filters and tools" sheet and back (toolbar redesign, v1.687.0). Projected visits and
		// Project work are switches in More ▸ Display (MPTB).
		this.$filters = $('<span class="mp-tb-filters"></span>').appendTo($bar);
		this.$tech = $('<select class="form-control input-sm"></select>')
			.attr("aria-label", __("Technician"))
			.on("change", () => this.pick_filter(this.$tech.val() || ""))
			.appendTo(this.$filters);
		// The alert chips, then + Add and More (both filled by MPTB), then Undo as a small icon while
		// there is something to undo (its menu item is in More ▸ Tools), ? and refresh.
		this.$alerts = $('<span class="mp-tb-alerts mp-tb-empty" role="group"></span>').attr("aria-label", __("Alerts")).appendTo($bar);
		this.$tools = $('<span class="mp-tb-tools"></span>').appendTo($bar);
		this.$undo = $('<button type="button" class="btn btn-default btn-sm mp-undo mp-empty">↶</button>')
			.attr("aria-label", __("Undo"))
			.on("click", () => this.undo())
			.appendTo($bar);
		$('<button type="button" class="btn btn-default btn-sm mp-refresh">↻</button>')
			.attr("title", __("Refresh"))
			.attr("aria-label", __("Refresh"))
			.on("click", () => this.load())
			.appendTo($bar);
		this.update_undo_button();

		// The key of the visit kinds is in the "?" help only (legend_sections); it was a row of its own
		// under the toolbar, then a section of More ▸ Display.
		this.$panel = $('<div class="mp-section mp-res"></div>').appendTo(this.$body);
		this.$tray = $('<div class="mp-tray"></div>').hide().appendTo(this.$body);
		this.$grid_wrap = $("<div></div>").appendTo(this.$body);
		this.$hint = $('<div class="mp-hint"></div>').appendTo(this.$body);
	}

	// Display's two switches: the projected visits, and the project work of the same people.
	set_show_projected(on) {
		this.show_projected = !!on;
		this.save_pref(MP.projected_key, this.show_projected ? "1" : "0");
		this.render();
	}

	set_show_project_work(on) {
		this.show_project_work = !!on;
		this.save_pref(MP.project_key, this.show_project_work ? "1" : "0");
		this.render();
	}

	// Project work as a card each (P10, D13). Asking for cards asks for the work itself, so it turns Project
	// work on; asking for the strip again leaves Project work as it is.
	set_show_project_cards(on) {
		this.show_project_cards = !!on;
		this.save_pref(MP.cards_key, this.show_project_cards ? "1" : "0");
		if (this.show_project_cards && !this.show_project_work) {
			this.show_project_work = true;
			this.save_pref(MP.project_key, "1");
		}
		this.render();
	}

	// How project work is drawn right now: nothing, a busy strip a technician-day, or a card each.
	project_work_mode() {
		if (!this.show_project_work) return "off";
		return this.show_project_cards ? "cards" : "strip";
	}

	// ------------------------------------------------------------------ data

	load() {
		if (this.view === MPS.view) return this.mps_refresh(); // Simple mode: get_simple_sites
		const { start, end } = this.range();
		const token = ++this.request;
		this.$title.text(this.title());
		this.take_title_focus();
		Object.entries(this.$view_buttons).forEach(([view, $button]) => $button.toggleClass("mp-on", view === this.view));
		this.$body.addClass("mp-loading");
		return Promise.resolve(
			frappe.call({
				method: `${MP.api}.get_planner`,
				args: Object.assign({ start: mp_ymd(start), end: mp_ymd(end) }, this.c1_draft_args()),
			})
		)
			.then((r) => {
				if (token !== this.request) return;
				this.data = (r && r.message) || {};
				this.$body.removeClass("mp-loading");
				this.index();
				this.fill_technicians();
				// A technician the planner does not list has just become All: the address says so too.
				this.write_filter_url();
				this.render();
			})
			.catch(() => {
				if (token === this.request) this.$body.removeClass("mp-loading");
			});
	}

	index() {
		const data = this.data || {};
		this.tech_by_user = {};
		this.tech_names = {};
		(data.technicians || []).forEach((person) => {
			this.tech_by_user[person.user] = person;
			this.tech_names[person.user] = person.name;
		});
		[].concat(data.visits || [], data.unscheduled || [], data.projected || [], this.fm_cards()).forEach((card) => {
			if (card.modified) this.modified[card.name] = card.modified;
			// A helper who is not on the technician list still has a name on the card.
			(card.crew || []).forEach((member) => {
				if (member && member.user && member.name && !this.tech_names[member.user]) {
					this.tech_names[member.user] = member.name;
				}
			});
		});
	}

	fill_technicians() {
		const people = (this.data && this.data.technicians) || [];
		const keep = this.technician;
		this.$tech.empty();
		$("<option></option>").val("").text(__("All technicians")).appendTo(this.$tech);
		$("<option></option>").val("__none__").text(__("Unassigned")).appendTo(this.$tech);
		people.forEach((person) => {
			$("<option></option>")
				.val(person.user)
				.text(person.enabled ? person.name : `${person.name} (${__("disabled")})`)
				.appendTo(this.$tech);
		});
		const known = !keep || keep === "__none__" || people.some((person) => person.user === keep);
		// One a link named that is not listed is ignored: All, and no longer the link's (S5c).
		if (!known && this.url_filters && this.url_filters.technician === keep) this.release_filter("technician");
		this.technician = known ? keep : "";
		this.$tech.val(this.technician);
	}

	all_cards() {
		const data = this.data || {};
		return [].concat(data.visits || [], data.unscheduled || [], data.projected || [], this.fm_cards(), this.booking_cards());
	}

	// The engine's read-only bookings as cards, one per booking per day. Never movable: they carry
	// no `movable` flag, which is also what keeps them out of the drag path (on_down).
	booking_cards() {
		const out = [];
		const bookings = (this.data && this.data.bookings) || {};
		Object.keys(bookings).forEach((user) => {
			Object.keys(bookings[user] || {}).forEach((ymd) => {
				((bookings[user][ymd] || {}).items || []).forEach((item, index) => {
					out.push({
						kind: "booking",
						booking_kind: item.kind,
						key: `bk|${user}|${ymd}|${item.kind}|${item.ref}|${index}`,
						date: ymd,
						technician: user,
						ref: item.ref,
						name: item.ref,
						project: item.project,
						label: item.label,
						hours: item.hours,
						slot: item.slot,
						tentative: !!item.tentative,
					});
				});
			});
		});
		return out;
	}

	// 3 -> "3", 2.5 -> "2.5", 1.25 -> "1.25"
	fmt_hours(hours) {
		return String(+Number(hours || 0).toFixed(2));
	}

	cell_of(user, ymd) {
		return (((this.data && this.data.bookings) || {})[user] || {})[ymd] || null;
	}

	// No hours that day: a holiday, time off, or a day their work pattern leaves out.
	is_off(cell) {
		return !!cell && !(Number(cell.capacity) > 0);
	}

	// The short name of a day off: "Holiday", "Time off" or "Not a work day".
	off_label(cell) {
		const off = String((cell && cell.off) || "");
		if (off.startsWith("Holiday")) return __("Holiday");
		if (off === "Time off") return __("Time off");
		if (off === "Unavailable") return __("Unavailable"); // Phase 6D: an all-day personal block
		return __("Not a work day");
	}

	// The availability of one technician on one day, as a class and a short text. `kind` is one of
	// off, over, full and free. `ratio` fills the capacity bar.
	avail_state(cell) {
		if (!cell) return { kind: "none", cls: "mp-idle", text: "", ratio: 0 };
		const capacity = Number(cell.capacity) || 0;
		const booked = Number(cell.booked) || 0;
		const over = booked - capacity;
		let state;
		if (capacity <= 0) {
			const label = this.off_label(cell);
			state =
				booked > 0.01
					? { kind: "off", cls: "mp-red mp-offday", text: __("Off, {0}h booked", [this.fmt_hours(booked)]), ratio: 1 }
					: { kind: "off", cls: "mp-offday", text: label, ratio: 0 };
		} else if (over > 0.01) {
			state = { kind: "over", cls: "mp-red", text: __("Over {0}h", [this.fmt_hours(over)]), ratio: 1 };
		} else {
			const ratio = booked / capacity;
			const free = Math.max(0, Number(cell.free != null ? cell.free : capacity - booked));
			state = {
				kind: free > 0.01 ? "free" : "full",
				cls: ratio <= 0.75 ? "mp-green" : "mp-amber",
				text: free > 0.01 ? __("{0}h free", [this.fmt_hours(free)]) : __("Full"),
				ratio,
			};
			if (cell.off) state.text += ` · ${__("½ day off")}`;
		}
		if ((cell.conflicts || []).length) state.cls += " mp-conflict";
		return state;
	}

	// "~1h 10m drive": the engine's drive time for the day. A "~" marks an estimate.
	drive_line(cell) {
		const minutes = Number(cell && cell.drive_minutes) || 0;
		if (minutes <= 0) return "";
		const approx = cell.drive_source === "estimate" || cell.drive_source === "mixed" ? "~" : "";
		return __("{0} drive", [approx + mp_drive(minutes)]);
	}

	drive_source_text(source) {
		if (source === "google") return __("Google");
		if (source === "mixed") return __("Google + estimate");
		return __("estimate");
	}

	// Does this technician-day have anything to drive to (so a route is worth opening)?
	has_route(user, ymd, cell) {
		if (!cell) return false;
		if (Number(cell.drive_minutes) > 0) return true;
		if ((this.day_cards[`${user}|${ymd}`] || []).length) return true;
		return (cell.items || []).some((item) => item.kind && item.kind !== "travel");
	}

	// The drive text, a Long drive chip, and the route icon of one technician-day. Clicking the icon
	// opens the Project Planner's route view for that person and day.
	drive_html(person, ymd, cell) {
		if (!person || !person.resource || !this.has_route(person.user, ymd, cell)) return "";
		const line = this.drive_line(cell);
		const tip = line
			? `${line} · ${this.drive_source_text(cell.drive_source)}`
			: __("Open this day's route");
		const long = cell.long_drive ? `<span class="mp-chip mp-red">${mp_esc(__("Long drive"))}</span>` : "";
		return `
			<span class="mp-drive">
				<button type="button" class="mp-route" data-route-resource="${mp_esc(person.resource)}"
					data-route-date="${mp_esc(ymd)}" title="${mp_esc(tip)}" aria-label="${mp_esc(
			__("Open the route for {0}", [mp_when(ymd)])
		)}">${MP_ROUTE_ICON}<span class="mp-drive-text">${mp_esc(line || __("Route"))}</span></button>${long}
			</span>`;
	}

	// The tooltip of one technician-day: hours, the day off, what is booked, drive and conflicts.
	cell_tip(user, ymd, cell) {
		const lines = [`${this.tech_name(user)} · ${mp_when(ymd)}`];
		if (!cell) return lines.join("\n");
		lines.push(
			__("{0}h of {1}h booked on projects, rentals, travel and visits", [
				this.fmt_hours(cell.booked),
				this.fmt_hours(cell.capacity),
			])
		);
		if (cell.off) lines.push(cell.off);
		// C1: pencilled tasks and visits are soft hours, as on the Project Planner.
		if (Number(cell.soft_booked) > 0.01) {
			lines.push(__("{0}h pencilled (tentative, not counted as booked)", [this.fmt_hours(cell.soft_booked)]));
		}
		(this.day_cards[`${user}|${ymd}`] || []).forEach((card) => {
			const what = card.kind === "projected" ? __("projected visit") : card.tentative ? __("pencilled visit") : __("visit");
			lines.push(`• ${card.site || card.project || card.name} (${what})`);
		});
		(cell.items || []).forEach((item) => {
			const parts = [item.slot ? item.slot.join("–") : `${this.fmt_hours(item.hours)}h`];
			lines.push(`• ${item.label || item.ref} (${parts.join(", ")})`);
		});
		const drive = this.drive_line(cell);
		if (drive) lines.push(`${__("Driving")}: ${drive} (${this.drive_source_text(cell.drive_source)})`);
		if (cell.long_drive) lines.push(__("Long drive"));
		if (Number(cell.unlocated) > 0) {
			lines.push(__("{0} stop(s) with no location: not counted in the drive", [cell.unlocated]));
		}
		(cell.conflicts || []).forEach((text) => lines.push(`${__("Conflict")}: ${text}`));
		(cell.warnings || []).forEach((text) => lines.push(`${__("Note")}: ${text}`));
		return lines.join("\n");
	}

	// The day header's free-hours text for the selected technician, from the engine's numbers.
	free_info(user, ymd) {
		const cell = this.cell_of(user, ymd);
		if (!cell) return null;
		const state = this.avail_state(cell);
		const cls = { off: "mp-free-off", over: "mp-free-over", full: "mp-free-full" }[state.kind] || "";
		const lines = [
			__("{0}h of {1}h booked on projects, rentals, travel and visits", [
				this.fmt_hours(cell.booked),
				this.fmt_hours(cell.capacity),
			]),
		];
		if (cell.off) lines.push(cell.off);
		const drive = this.drive_line(cell);
		if (drive) lines.push(`${__("Driving")}: ${drive}`);
		(cell.conflicts || []).forEach((conflict) => lines.push(conflict));
		return { text: state.text, cls, title: lines.join("\n") };
	}

	visible(card) {
		if (card.kind === "booking") {
			// A card each only in cards mode; the strip mode draws the same bookings as a strip (MP10).
			if (this.project_work_mode() !== "cards" || this.technician === "__none__") return false;
			return !this.technician || card.technician === this.technician;
		}
		if (card.kind === "projected" && !this.show_projected) return false;
		if (!this.technician) return true;
		if (this.technician === "__none__") return !card.technician;
		return this.visit_people(card).includes(this.technician);
	}

	// Everyone booked on a visit: the technician (the lead) first, then the crew, each once.
	visit_people(card) {
		const out = [];
		const add = (user) => {
			if (user && !out.includes(user)) out.push(user);
		};
		add(card.technician);
		(card.crew || []).forEach((member) => add(member && member.user));
		return out;
	}

	crew_has(card, user) {
		return !!user && (card.crew || []).some((member) => member && member.user === user);
	}

	// The crew as move_visit takes it: the users besides the lead, with their own hours when set.
	crew_rows(card) {
		return (card.crew || [])
			.filter((member) => member && member.user && member.user !== card.technician)
			.map((member) => ({ user: member.user, hours: Number(member.hours) > 0 ? Number(member.hours) : null }));
	}

	// Order does not matter to the crew, so compare it sorted.
	crew_key(rows) {
		return JSON.stringify(
			(rows || [])
				.map((row) => [row.user, row.hours == null ? null : Number(row.hours)])
				.sort((a, b) => String(a[0]).localeCompare(String(b[0])))
		);
	}

	// ------------------------------------------------------------------ render

	render() {
		if (!this.data) return;
		this.by_key = {};
		this.day_cards = {};
		const cards = this.all_cards();
		cards.forEach((card) => {
			this.by_key[card.key] = card;
			if ((card.kind === "visit" || card.kind === "projected") && card.date) {
				// A multi-person visit is on every crew member's day.
				this.visit_people(card).forEach((user) => {
					(this.day_cards[`${user}|${card.date}`] = this.day_cards[`${user}|${card.date}`] || []).push(card);
				});
			}
		});
		this.$tech.toggle(this.view !== "crew");
		this.render_panel();
		if (this.view === "crew") this.render_crew(cards);
		else this.render_calendar(cards);
		this.$hint.text(
			(this.view === "crew"
				? __(
						"Drag a visit to another day on the same row to move it, or onto another technician's row to hand it over. On a touch screen, hold for a moment first. A visit with a crew shows in every crew member's row: drag one person's card to another row to swap only that person. Drag a technician onto a visit to add them to its crew. Dashed visits follow the technician on the site's Maintenance Profile, so they only change day."
				  )
				: __(
						"Drag a card to another day to move the visit. Drag a technician from Resources available onto a visit to add them to its crew (a visit with nobody on it gets them as its technician). The ringed initials are the technician who fills the visit in. On a touch screen, hold the card for a moment first. A dashed card with a blue edge is a contract's next visit: moving it moves the contract's next visit date, and the dashed cards after it follow."
				  )) + this.p10_hint_note()
		);
		this.render_phase6a();
		this.render_phase6b();
		this.render_phase6c();
		this.render_phase6d();
		this.render_phase6e();
		this.render_phase_c1();
		this.tb_render();
	}

	render_calendar(cards) {
		const { start, end } = this.range();
		const today = this.today();
		const month = moment(this.anchor, "YYYY-MM-DD").month();
		const by_day = {};
		const unscheduled = [];
		cards.forEach((card) => {
			if (!this.visible(card)) return;
			if (!card.date) unscheduled.push(card);
			else (by_day[card.date] = by_day[card.date] || []).push(card);
		});

		this.render_tray(unscheduled);

		const single = !!this.technician && this.technician !== "__none__";
		const everyone = !this.technician;
		const planned = (this.data.technicians || []).filter((person) => person.enabled && person.resource);
		const $grid = $('<div class="mp-grid"></div>');
		const first = this.first_weekday();
		for (let i = 0; i < 7; i++) {
			$('<div class="mp-dow"></div>')
				.text(moment().day((first + i) % 7).format("ddd"))
				.appendTo($grid);
		}
		for (let day = start.clone(); !day.isAfter(end, "day"); day.add(1, "days")) {
			const ymd = mp_ymd(day);
			const day_cards = (by_day[ymd] || []).sort((a, b) => this.compare(a, b));
			const $day = $('<div class="mp-day"></div>')
				.attr("data-date", ymd)
				.toggleClass("mp-today", ymd === today)
				.toggleClass("mp-past", ymd < today)
				.toggleClass("mp-out", this.view === "month" && day.month() !== month)
				.appendTo($grid);
			const $head = $('<div class="mp-day-head"></div>').appendTo($day);
			const label =
				this.view === "week"
					? day.format("ddd, MMM D")
					: day.date() === 1
					? day.format("MMM D")
					: day.format("D");
			// Enter and Space do what a click does (S3.6); with the quick looks on, their own
			// capture-phase handlers take both first.
			$('<span class="mp-day-num" role="button" tabindex="0"></span>')
				.text(label)
				.attr("title", __("Show this week"))
				.attr("aria-label", __("Show the week of {0}", [this.day_words(ymd)]))
				.on("click", () => this.go("week", ymd))
				.on("keydown", (e) => {
					if (e.key !== "Enter" && e.key !== " ") return;
					e.preventDefault();
					this.focus_title_until = Date.now() + 2000;
					this.go("week", ymd);
				})
				.appendTo($head);
			const cell = single ? this.cell_of(this.technician, ymd) : null;
			const free = single ? this.free_info(this.technician, ymd) : null;
			if (free) {
				$('<span class="mp-day-free"></span>')
					.addClass(free.cls)
					.text(free.text)
					.attr("title", free.title)
					.appendTo($head);
			}
			if (cell && cell.long_drive) {
				$(`<span class="mp-chip mp-red"></span>`)
					.text(__("Long drive"))
					.attr("title", this.drive_line(cell))
					.appendTo($head);
			}
			const visit_count = day_cards.filter((card) => card.kind !== "booking" && card.kind !== "move").length;
			if (visit_count > 1) {
				$('<span class="mp-day-count"></span>').text(__("{0} visits", [visit_count])).appendTo($head);
			}
			// Days off: shaded and labeled for one technician; a badge for each person off for All.
			if (this.is_off(cell)) {
				$day.addClass("mp-off-day");
				$('<div class="mp-off-label"></div>')
					.text(this.off_label(cell))
					.attr("title", cell.off || "")
					.appendTo($day);
			} else if (everyone) {
				this.append_off_badges($day, planned, ymd);
			}
			this.p6d_day_head($day, ymd);
			day_cards.forEach((card) => $day.append(this.card_html(card)));
			// P10: the project work of the people on screen, one thin strip each (the cards' place when the
			// strip is on; nothing here in cards mode).
			const strips = this.p10_day_strips_html(ymd);
			if (strips) $day.append(strips);
		}
		this.$grid_wrap.empty().removeClass("mp-month mp-week mp-crew-view").addClass(`mp-${this.view}`).append($grid);
	}

	// Everyone with hours on the planner who is off on `ymd`, as small badges; one label when nobody works.
	append_off_badges($day, planned, ymd) {
		const off = planned.filter((person) => this.is_off(this.cell_of(person.user, ymd)));
		if (!off.length) return;
		if (planned.length > 1 && off.length === planned.length) {
			$day.addClass("mp-off-day");
			$('<div class="mp-off-label"></div>').text(__("Everyone off")).appendTo($day);
			return;
		}
		const $row = $('<div class="mp-off-badges"></div>').appendTo($day);
		off.forEach((person) => {
			const cell = this.cell_of(person.user, ymd);
			const badge = $(this.tech_badge(person.user, "mp-tech-off")).attr(
				"title",
				`${person.name}: ${this.off_label(cell)}`
			);
			badge.appendTo($row);
		});
	}

	render_tray(cards) {
		this.$tray.empty().toggle(cards.length > 0);
		if (!cards.length) return;
		$("<h5></h5>")
			.text(__("Unscheduled ({0}): drag onto a day", [cards.length]))
			.appendTo(this.$tray);
		const $cards = $('<div class="mp-tray-cards"></div>').appendTo(this.$tray);
		cards.sort((a, b) => this.compare(a, b)).forEach((card) => $cards.append(this.card_html(card)));
	}

	// ------------------------------------------------------------------ resources panel

	tech_color(user) {
		const person = this.tech_by_user[user];
		const own = person && mp_color(person.color, "");
		if (own) return own;
		let hash = 0;
		for (const ch of String(user || "")) hash = (hash * 31 + ch.charCodeAt(0)) % 360;
		return `hsl(${hash},55%,42%)`;
	}

	// A technician's name chip. Dragging it onto a visit card assigns them to the visit.
	person_html(person, compact) {
		const group = person.group ? __(person.group) : "";
		const tip = this.data.can_move_visits
			? __("Drag {0} onto a visit to add them to its crew", [person.name])
			: person.name;
		const selected = this.technician === person.user ? " mp-selected" : "";
		return `
			<div class="mp-person${selected}" data-user="${mp_esc(person.user)}" title="${mp_esc(tip)}">
				${compact ? "" : `<span class="mp-dot" style="--mp-color: ${mp_esc(this.tech_color(person.user))}"></span>`}
				<span class="mp-person-name">${mp_esc(person.name)}</span>
				<span class="mp-person-group">${mp_esc(group)}</span>
			</div>`;
	}

	render_panel() {
		this.$panel.toggle(this.view !== "crew").empty();
		if (this.view === "crew") return;
		const people = (this.data.technicians || []).filter((person) => person.enabled);
		const $head = $('<div class="mp-section-head"></div>').appendTo(this.$panel);
		$('<span class="mp-res-title"></span>').text(__("Resources available")).appendTo($head);
		// P10: closed, the panel is one summary line of who has hours free and who is off this week.
		// The line grows to fill the head; with nothing to say the spacer does.
		const summarised = !this.panel_open && this.p10_summary_line($head, people);
		if (!summarised) $('<span class="mp-spacer"></span>').appendTo($head);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(this.panel_open ? __("Hide") : __("Show"))
			.attr("aria-expanded", this.panel_open ? "true" : "false")
			.attr(
				"title",
				this.panel_open ? __("Collapse to a summary line") : __("Each technician's free hours, day by day")
			)
			.on("click", () => {
				this.panel_open = !this.panel_open;
				this.save_pref(MP.resources_key, this.panel_open ? "1" : "0");
				this.render_panel();
				// The names in the redrawn panel need 6A's markup (a click for their week, the hover glow), which
				// only a full render gave them. Closed by default since P10, Show is now how most people see them.
				if (this.p6a) this.p6a_decorate();
			})
			.appendTo($head);
		if (!this.panel_open) return;
		if (!people.length) {
			$('<div class="mp-empty-note"></div>')
				.text(__("No technicians yet. Technicians are active employees with a Technician designation."))
				.appendTo(this.$panel);
			return;
		}
		const days = this.range_days();
		const today = this.today();
		const month = this.view === "month";
		const columns = month ? `150px repeat(${days.length},minmax(20px,1fr))` : "170px repeat(7,minmax(74px,1fr))";
		const html = [`<div class="mp-res-head"></div>`];
		days.forEach((ymd) => {
			const m = moment(ymd, "YYYY-MM-DD");
			const label = month ? m.format("D") : m.format("ddd D");
			html.push(`<div class="mp-res-head${ymd === today ? " mp-today" : ""}">${mp_esc(label)}</div>`);
		});
		people.forEach((person) => {
			html.push(this.person_html(person, false));
			days.forEach((ymd) => {
				const cell = this.cell_of(person.user, ymd);
				const state = this.avail_state(cell);
				const tip = this.cell_tip(person.user, ymd, cell);
				if (month) {
					// Too small for an icon: the whole mini cell opens the route when there is one.
					const route =
						person.resource && this.has_route(person.user, ymd, cell)
							? ` data-route-resource="${mp_esc(person.resource)}" data-route-date="${mp_esc(ymd)}"`
							: "";
					// The month's cell is a tint and nothing else on screen, so its words are read aloud.
					html.push(
						`<div class="mp-avail mp-mini ${state.cls}"${route} title="${mp_esc(tip)}">${
							state.text ? `<span class="mp-sr">${mp_esc(state.text)}</span>` : ""
						}</div>`
					);
					return;
				}
				const width = Math.round(Math.min(1, state.ratio) * 100);
				html.push(`
					<div class="mp-avail ${state.cls}" title="${mp_esc(tip)}">
						<span class="mp-avail-text">${mp_esc(state.text)}</span>
						<div class="mp-bar"><i style="width:${width}%"></i></div>
						${this.drive_html(person, ymd, cell)}
					</div>`);
			});
		});
		const $scroll = $('<div class="mp-scroll"></div>').appendTo(this.$panel);
		$('<div class="mp-res-grid"></div>')
			.css("grid-template-columns", columns)
			.css("min-width", month ? `${150 + days.length * 22}px` : "700px")
			.html(html.join(""))
			.appendTo($scroll);
	}

	// ------------------------------------------------------------------ crew view

	render_crew(cards) {
		const days = this.range_days();
		const today = this.today();
		const by_cell = {};
		const unscheduled = [];
		cards.forEach((card) => {
			// A booking is a card in cards mode only; the strip mode draws it as a strip in the cell (P10).
			if (card.kind === "booking" && this.project_work_mode() !== "cards") return;
			if (card.kind === "projected" && !this.show_projected) return;
			if (!card.date) {
				if (card.kind === "visit" || card.kind === "move") unscheduled.push(card);
				return;
			}
			// A multi-person visit sits in every crew member's row.
			const owners = card.kind === "booking" ? [card.technician || ""] : this.visit_people(card);
			(owners.length ? owners : [""]).forEach((user) => {
				const key = `${user}|${card.date}`;
				(by_cell[key] = by_cell[key] || []).push(card);
			});
		});
		this.render_tray(unscheduled);

		// Every technician, plus a disabled one who still has visits this week, plus Unassigned when
		// any visit has nobody. The technician filter is for the calendar: here every row is a drop target.
		const people = (this.data.technicians || []).filter(
			(person) =>
				person.enabled ||
				days.some((ymd) => (by_cell[`${person.user}|${ymd}`] || []).length || this.p10_has_strip(person.user, ymd))
		);
		const rows = people.map((person) => ({ person, user: person.user }));
		if (days.some((ymd) => (by_cell[`|${ymd}`] || []).length)) rows.push({ person: null, user: "" });

		const html = [`<div class="mp-crew-corner"></div>`];
		days.forEach((ymd) => {
			html.push(
				`<div class="mp-crew-head${ymd === today ? " mp-today" : ""}" role="button" tabindex="0" data-week="${mp_esc(ymd)}" title="${mp_esc(
					__("Show this week")
				)}" aria-label="${mp_esc(__("Show the week of {0}", [this.day_words(ymd)]))}">${mp_esc(mp_when(ymd))}</div>`
			);
		});
		html.push(this.p6d_crew_notes_row(days));
		rows.forEach(({ person, user }) => {
			html.push(
				person
					? this.person_html(person, true)
					: `<div class="mp-person mp-person-static"><span class="mp-person-name">${mp_esc(__("Unassigned"))}</span></div>`
			);
			days.forEach((ymd) => {
				const cell = person ? this.cell_of(user, ymd) : null;
				const state = this.avail_state(cell);
				const width = Math.round(Math.min(1, state.ratio) * 100);
				const list = (by_cell[`${user}|${ymd}`] || []).sort((a, b) => this.compare(a, b));
				const cap = person
					? `<div class="mp-cap ${state.cls}" title="${mp_esc(this.cell_tip(user, ymd, cell))}">
							<span class="mp-cap-text">${mp_esc(state.text)}</span>
							<div class="mp-bar"><i style="width:${width}%"></i></div>
							${this.drive_html(person, ymd, cell)}
						</div>`
					: "";
				html.push(`
					<div class="mp-cell${ymd < today ? " mp-past" : ""}${this.is_off(cell) ? " mp-off-day" : ""}"
						data-date="${mp_esc(ymd)}" data-user="${mp_esc(user)}">
						${cap}
						${this.p6d_cell_blocks(person, ymd)}
						${list.map((card) => this.card_row_html(card, user)).join("")}
						${person ? this.p10_cell_strip_html(user, ymd) : ""}
					</div>`);
			});
		});
		this.$grid_wrap.empty().removeClass("mp-month mp-week").addClass("mp-crew-view");
		if (!rows.length) {
			$('<div class="mp-section mp-empty-note"></div>')
				.text(__("No technicians to show."))
				.appendTo(this.$grid_wrap);
			return;
		}
		const $scroll = $('<div class="mp-scroll"></div>').appendTo(this.$grid_wrap);
		const $crew = $('<div class="mp-crew"></div>').html(html.join("")).appendTo($scroll);
		const open_week = (e) => this.go("week", e.currentTarget.getAttribute("data-week"));
		$crew
			.find("[data-week]")
			.on("click", open_week)
			.on("keydown", (e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				e.preventDefault();
				this.focus_title_until = Date.now() + 2000;
				open_week(e);
			});
	}

	compare(a, b) {
		const order = (card) =>
			MP.status_order[card.kind === "projected" || card.kind === "booking" ? card.kind : card.status] || 0;
		return order(a) - order(b) || String(a.site || "").localeCompare(String(b.site || ""));
	}

	kind_class(card) {
		const classes = [card.kind === "projected" ? "mp-projected" : `mp-${card.status}`];
		if (card.label) classes.push(MP.extra_labels.includes(card.label) ? "mp-extra" : "mp-seasonal");
		if (card.overdue && card.kind === "visit") classes.push("mp-overdue");
		if (card.movable) classes.push("mp-movable");
		if (card.saving) classes.push("mp-saving");
		// C1 (v1.601.0): a pencilled visit is hatched like the Project Planner's pencil cards, and an
		// unpublished draft gets the dashed outline Draft mode uses there.
		if (card.tentative) classes.push("mp-pencil");
		if (card.drafted && this.draft_on) classes.push("mp-drafted");
		return classes.join(" ");
	}

	// A project task, rental crew task or travel day of a technician. Read-only: no mp-movable.
	booking_html(card) {
		const kind = card.booking_kind in MP.booking_kinds ? card.booking_kind : "task";
		const sub = [];
		if (card.project) sub.push(card.project);
		sub.push(card.slot ? card.slot.join("–") : `${this.fmt_hours(card.hours)}h`);
		const who = card.technician ? this.tech_name(card.technician) : "";
		const tip = [
			card.label,
			sub.join(" · "),
			who,
			card.tentative ? __("Pencil (tentative)") : "",
			__("Read-only. Move it on the Project Planner."),
		]
			.filter(Boolean)
			.join("\n");
		// In a technician's row the row says who it is.
		const badge = this.technician || this.view === "crew" ? "" : this.tech_badge(card.technician);
		const pencil = card.tentative ? " mp-pencil" : "";
		return `
			<div class="mp-card mp-booking mp-bk-${kind}${pencil}" data-key="${mp_esc(card.key)}" tabindex="0" title="${mp_esc(tip)}">
				<div class="mp-card-top">
					<span class="mp-card-title">${mp_esc(card.label || card.ref)}</span>
					${badge}
				</div>
				<div class="mp-card-sub">${mp_esc(sub.join(" · "))}</div>
				<div class="mp-card-who"><span class="mp-chip">${mp_esc(__(MP.booking_kinds[kind]))}</span></div>
			</div>`;
	}

	card_html(card) {
		return this.card_row_html(card, undefined);
	}

	// `row_user` is the crew-view row the card sits in: the same visit shows in every crew member's
	// row, and a drag needs to know which person's chip was picked up. The calendar passes none.
	card_row_html(card, row_user) {
		if (card.kind === "booking") return this.booking_html(card);
		if (card.kind === "move") return this.fm_card_html(card, row_user);
		const sub = [];
		if (card.label) sub.push(__(card.label));
		if (card.feature) sub.push(card.feature);
		if (!sub.length) sub.push(card.kind === "projected" ? __(card.frequency || "") : __("Regular visit"));

		const chips = [];
		// C1: what the card is before anything else, as on the Project Planner's cards.
		if (card.drafted && this.draft_on) chips.push(`<span class="mp-chip mp-draft-chip">${mp_esc(__("Draft"))}</span>`);
		if (card.tentative) chips.push(`<span class="mp-chip mp-pencil-chip">${mp_esc(__("Pencil"))}</span>`);
		if (card.kind === "projected") chips.push(card.movable ? __("Next visit") : __("Projected"));
		if (card.status === "pending") chips.push(__("Pending review"));
		if (card.status === "done") chips.push(__("Done"));
		if (card.overdue) chips.push(`<span class="mp-chip mp-red">${mp_esc(__("Overdue"))}</span>`);
		// How long it books each person, when that is not just the default.
		const people = this.visit_people(card);
		if (card.full_day) chips.push(__("Full day"));
		else if (Number(card.planned_hours) > 0 || (people.length > 1 && Number(card.hours) > 0)) {
			chips.push(__("{0}h each", [mp_hours(Number(card.planned_hours) > 0 ? card.planned_hours : card.hours)]));
		}
		if (card.on_hold) chips.push(`<span class="mp-chip mp-red">${mp_esc(__("On hold"))}</span>`);
		if (card.flagged) chips.push(`<span class="mp-chip mp-red">${mp_esc(__("Chemistry"))}</span>`);
		// A visit on a day its technician does not work still gets done by somebody: say so.
		const day_cell =
			card.technician && card.date && card.status !== "done" && card.status !== "pending"
				? this.cell_of(card.technician, card.date)
				: null;
		if (this.is_off(day_cell)) {
			chips.push(
				`<span class="mp-chip mp-red" title="${mp_esc(day_cell.off || "")}">${mp_esc(__("Day off"))}: ${mp_esc(
					this.off_label(day_cell)
				)}</span>`
			);
		}

		// In a technician's row the row says who it is.
		const in_row = this.view === "crew" && !!card.date;
		const who = people.length ? people.map((user) => this.tech_name(user)).join(", ") : __("Unassigned");
		const crew_line = people.length > 1 ? `${__("Crew")}: ${who}` : "";
		const tip = [
			card.site_full,
			sub.join(" · "),
			crew_line || who,
			day_cell && this.is_off(day_cell) ? this.off_label(day_cell) : "",
			card.tentative ? __("Pencil (tentative): soft hours, nobody assigned or told yet") : "",
			card.drafted && this.draft_on ? __("Draft: not published yet") : "",
		]
			.filter(Boolean)
			.join("\n");
		const row_attr = row_user === undefined ? "" : ` data-row-user="${mp_esc(row_user)}"`;
		return `
			<div class="mp-card ${this.kind_class(card)}" data-key="${mp_esc(card.key)}"${row_attr} tabindex="0" title="${mp_esc(tip)}">
				<div class="mp-card-top">
					<span class="mp-card-title">${mp_esc(card.site || card.project || card.name)}</span>
					${this.people_badges(card, in_row)}
				</div>
				<div class="mp-card-sub">${mp_esc(sub.join(" · "))}</div>
				<div class="mp-card-who">${in_row ? "" : mp_esc(who)} ${chips
					.map((chip) => (chip.startsWith("<span") ? chip : `<span class="mp-chip">${mp_esc(chip)}</span>`))
					.join("")}</div>
			</div>`;
	}

	tech_name(user) {
		return (this.tech_names && this.tech_names[user]) || user;
	}

	// The initials of everyone on a visit, the lead first and ringed when there is a crew. In a crew-view
	// row a lone person is the row, so only a multi-person visit shows them.
	people_badges(card, in_row) {
		const people = this.visit_people(card);
		if (!people.length || (in_row && people.length < 2)) return "";
		const shown = people.slice(0, 4);
		const badges = shown
			.map((user) => {
				const lead = people.length > 1 && user === card.technician;
				return this.tech_badge(user, lead ? "mp-lead" : "", lead ? __("Technician") : "");
			})
			.join("");
		const more = people.length > shown.length ? `<span class="mp-more">+${people.length - shown.length}</span>` : "";
		return `<span class="mp-techs">${badges}${more}</span>`;
	}

	tech_badge(user, extra, role) {
		if (!user) return "";
		const name = this.tech_name(user);
		const parts = String(name).split(/\s+/).filter(Boolean);
		const initials = (parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "");
		return `<span class="mp-tech${extra ? ` ${mp_esc(extra)}` : ""}" style="--mp-color: ${mp_esc(
			this.tech_color(user)
		)}" title="${mp_esc(role ? `${name} (${role})` : name)}">${mp_esc(initials.toUpperCase())}</span>`;
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
			if (this.open_route(e.target)) return;
			const card_el = e.target.closest(".mp-card");
			if (!card_el) return;
			const card = this.by_key[card_el.getAttribute("data-key")];
			if (card) this.open_card(card);
		});
		root.addEventListener("keydown", (e) => {
			if (e.key !== "Enter" && e.key !== " ") return;
			const card_el = e.target.closest && e.target.closest(".mp-card");
			if (!card_el) return;
			e.preventDefault();
			const card = this.by_key[card_el.getAttribute("data-key")];
			if (card) this.open_card(card);
		});
	}

	// The route icon (or a month-panel mini cell) of a technician-day: the Project Planner's route view.
	open_route(target) {
		const el = target && target.closest && target.closest("[data-route-resource]");
		if (!el) return false;
		const resource = el.getAttribute("data-route-resource");
		const ymd = el.getAttribute("data-route-date");
		if (!resource || !ymd) return false;
		this.p6a_route(() => frappe.set_route(MP.project_route, "route", resource, ymd), false);
		return true;
	}

	// What is under the pointer when it went down: a visit card, or a technician.
	drag_source(el) {
		if (!this.data) return null;
		if (el.closest(".pk-drawer")) return this.p6a_drag_source(el);
		const person_el = el.closest(".mp-person[data-user]");
		if (person_el) {
			if (!this.data.can_move_visits) return null;
			return { kind: "person", el: person_el, user: person_el.getAttribute("data-user") };
		}
		const card_el = el.closest(".mp-card");
		if (!card_el) return null;
		const card = this.by_key[card_el.getAttribute("data-key")];
		if (!card || !card.movable || card.saving) return null;
		// In the crew view the same visit sits in each crew member's row: remember whose chip it is.
		const row = card_el.getAttribute("data-row-user");
		return { kind: "card", el: card_el, card, from_user: row == null ? null : row };
	}

	on_down(e) {
		if (e.button > 0 || this.drag || !e.target.closest) return;
		const source = this.drag_source(e.target);
		if (!source) return;
		this.drag = { source, el: source.el, x: e.clientX, y: e.clientY, id: e.pointerId, touch: e.pointerType === "touch" };
		if (this.drag.touch) {
			this.drag.timer = setTimeout(() => this.lift(), MP.hold_ms);
		} else {
			// No mousedown, so a drag does not select the text of every day it crosses. The
			// click that opens the card still fires.
			e.preventDefault();
		}
	}

	on_move(e) {
		const drag = this.drag;
		if (!drag || e.pointerId !== drag.id) return;
		const far = Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > MP.drag_px;
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
		const scroller = el && el.closest(".mp-scroll");
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
		drag.ghost.classList.add("mp-ghost");
		drag.ghost.style.width = `${Math.max(rect.width, 120)}px`;
		document.body.appendChild(drag.ghost);
		drag.el.classList.add("mp-dragging");
		document.body.classList.add("mp-drag-active");
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

	// Where a drag would land. A technician lands on a visit card; a visit lands on a crew-view cell
	// (a technician and a day) or a calendar day.
	find_target(x, y) {
		const drag = this.drag;
		const el = document.elementFromPoint(x, y);
		if (!drag || !el || !el.closest || !this.$body[0].contains(el)) return null;
		if (drag.source.kind === "person") {
			const card_el = el.closest(".mp-card[data-key]");
			return card_el ? { el: card_el, key: card_el.getAttribute("data-key") } : null;
		}
		const cell = el.closest(".mp-cell[data-date]");
		if (cell) return { el: cell, date: cell.getAttribute("data-date"), user: cell.getAttribute("data-user") || "", row: true };
		const day = el.closest(".mp-grid .mp-day[data-date]");
		if (day) return { el: day, date: day.getAttribute("data-date") };
		return null;
	}

	mark_target(target) {
		const drag = this.drag;
		if (!drag) return;
		if (drag.target_el && (!target || drag.target_el !== target.el)) {
			drag.target_el.classList.remove("mp-over", "mp-over-no");
		}
		drag.target_el = target ? target.el : null;
		if (!target) return;
		target.el.classList.add(target.date && target.date < this.today() ? "mp-over-no" : "mp-over");
	}

	end_drag() {
		const drag = this.drag;
		this.drag = null;
		if (!drag) return;
		clearTimeout(drag.timer);
		if (drag.ghost) drag.ghost.remove();
		if (drag.target_el) drag.target_el.classList.remove("mp-over", "mp-over-no");
		drag.el.classList.remove("mp-dragging");
		document.body.classList.remove("mp-drag-active");
	}

	// ------------------------------------------------------------------ moving

	site_of(card) {
		return card.site || card.site_full || card.name;
	}

	// Work out what a drop means. `{ card, change }` is a move to send (`change` holds a date, a
	// technician, and for a helper's chip the `from_user` it replaces), `{ card, add_crew }` a person
	// added to the visit's crew, `{ refuse }` a reason it cannot be done, and null changes nothing.
	plan_drop(source, target) {
		if (source.kind === "person") {
			const card = this.by_key[target.key];
			const name = this.tech_name(source.user);
			if (!card || card.kind === "booking") return { refuse: __("Drop {0} onto a visit.", [name]) };
			if (card.kind === "move") return this.fm_plan_person(card, source.user);
			const site = this.site_of(card);
			if (card.kind === "projected") {
				return {
					refuse: __(
						"{0} is a projected visit: it follows the technician on the site's Maintenance Profile, so {1} cannot be added to it here. Set the site's default crew on its Maintenance Profile, or add them once the visit is drafted.",
						[site, name]
					),
				};
			}
			if (!card.movable) {
				return { refuse: __("{0} is started or finished, so its crew stays as it is.", [site]) };
			}
			if ((card.technician || "") === source.user || this.crew_has(card, source.user)) {
				return { refuse: __("{0} is already on {1}.", [name, site]) };
			}
			// Nobody on the visit yet: the person becomes its technician. Otherwise they join the crew.
			if (!card.technician) return { card, change: { technician: source.user } };
			return { card, add_crew: source.user };
		}

		const card = source.card;
		if (card.kind === "move") return this.fm_plan_move(source, target);
		// C1: Draft mode holds back changes to drafted visits only; a projected one moves its contract.
		if (card.kind === "projected" && this.draft_on) return { refuse: __(MPC1.refusals.move_projected) };
		if (target.date !== card.date && target.date < this.today()) {
			return { refuse: __("Visits can only be moved to today or a later day.") };
		}
		const change = {};
		if (target.date !== card.date) change.date = target.date;
		// The row the chip was picked up from: a helper's chip sits in the helper's own row.
		const row_user = source.from_user == null ? card.technician || "" : source.from_user;
		const helper = !!row_user && row_user !== (card.technician || "") && this.crew_has(card, row_user);
		const hands_over = target.row && (target.user || "") !== row_user;
		if (hands_over && card.kind === "projected") {
			return {
				refuse: __(
					"{0} is a projected visit: it follows the technician on the site's Maintenance Profile, so it cannot move to another technician's row. Drop it on a day of its own row to change the date.",
					[this.site_of(card)]
				),
			};
		}
		if (hands_over && helper) {
			if (!target.user) {
				return {
					refuse: __("Drop {0} on a technician's row to hand their place on the visit over.", [
						this.tech_name(row_user),
					]),
				};
			}
			if (target.user === (card.technician || "") || this.crew_has(card, target.user)) {
				return { refuse: __("{0} is already on {1}.", [this.tech_name(target.user), this.site_of(card)]) };
			}
			// Only the helper whose chip it is changes; the technician and the rest of the crew stay.
			change.technician = target.user;
			change.from_user = row_user;
		} else if (target.row && (target.user || "") !== (card.technician || "") && !helper) {
			change.technician = target.user;
		}
		return Object.keys(change).length ? { card, change } : null;
	}

	drop(source, target) {
		if (this.p6b_drop(source, target)) return;
		const plan = this.plan_drop(source, target);
		if (!plan) return;
		if (plan.refuse) {
			frappe.show_alert({ message: plan.refuse, indicator: "orange" }, 6);
			return;
		}
		if (plan.add_crew) {
			this.add_to_crew(plan.card, plan.add_crew);
			return;
		}
		if (plan.fm) {
			this.fm_apply(plan.card, plan.fm);
			return;
		}
		this.apply(plan.card, plan.change);
	}

	// What a visit looks like before a change, for Undo: date, technician, crew, length and Full day.
	snapshot_of(card) {
		const before = { date: card.date || null, technician: card.technician || "" };
		before.crew = this.crew_rows(card);
		before.planned_hours = Number(card.planned_hours) || 0;
		before.full_day = card.full_day ? 1 : 0;
		before.tentative = card.tentative ? 1 : 0;
		return before;
	}

	// Show the card on its new day straight away; the reload afterwards is the truth.
	apply(card, change) {
		const before = { date: card.date || null, technician: card.technician || "" };
		Object.assign(before, this.snapshot_of(card));
		if (change.date) card.date = change.date;
		if (change.from_user) {
			// A helper's place is handed over: the technician and the rest of the crew stay.
			card.crew = (card.crew || []).map((member) =>
				member.user === change.from_user
					? Object.assign({}, member, { user: change.technician, name: this.tech_name(change.technician) })
					: member
			);
		} else if (change.technician !== undefined) {
			card.technician = change.technician || null;
			card.crew = (card.crew || []).filter((member) => member.user !== change.technician);
		}
		if (change.crew !== undefined) {
			card.crew = change.crew.map((row) => ({ user: row.user, name: this.tech_name(row.user), hours: row.hours }));
		}
		if (change.planned_hours !== undefined) card.planned_hours = change.planned_hours || null;
		if (change.full_day !== undefined) card.full_day = change.full_day ? 1 : 0;
		if (change.tentative !== undefined) card.tentative = change.tentative ? 1 : 0;
		card.saving = true;
		this.render();
		return this.save_move(card, change, before).finally(() => this.load());
	}

	// A technician chip dropped on a visit that has a technician already: add_crew. The conflict
	// check, the reason dialog and Undo are the same as for a move.
	add_to_crew(card, user) {
		const before = this.snapshot_of(card);
		const site = this.site_of(card);
		card.crew = (card.crew || []).concat([{ user, name: this.tech_name(user), hours: null }]);
		card.saving = true;
		this.render();
		return this.send(
			"add_crew",
			{ record: card.name, user, modified: this.modified[card.name] || card.modified },
			{
				snapshot: Object.assign({ kind: "visit", site, record: card.name }, before),
				message: __("{0} added to {1}", [this.tech_name(user), site]),
				noun: "visit",
			}
		).finally(() => this.load());
	}

	// Resolves either way: a refusal has already shown the server's own message.
	save_move(card, change, before) {
		const site = this.site_of(card);
		const visit = card.kind === "visit";
		const args = visit
			? Object.assign({ record: card.name, modified: this.modified[card.name] || card.modified }, change)
			: {
					contract: card.contract,
					from_date: card.from_date,
					to_date: change.date,
					serial_no: card.serial_no || null,
			  };
		// The crew goes as a JSON list of {user, hours} rows; Full day as 1 or 0.
		if (visit && args.crew !== undefined) args.crew = JSON.stringify(args.crew);
		if (visit && args.full_day !== undefined) args.full_day = args.full_day ? 1 : 0;
		if (visit && args.tentative !== undefined) args.tentative = args.tentative ? 1 : 0;
		const snapshot = visit
			? {
					kind: "visit",
					site,
					record: card.name,
					date: before.date, technician: before.technician,
					crew: before.crew,
					planned_hours: before.planned_hours,
					full_day: before.full_day,
					tentative: before.tentative,
			  }
			: {
					kind: "projected",
					site,
					contract: card.contract,
					serial_no: card.serial_no || null,
					from_date: change.date,
					to_date: before.date,
			  };
		let message;
		if (visit && change.from_user) {
			message = __("{0}: {1} handed over to {2}", [
				site,
				this.tech_name(change.from_user),
				this.tech_name(change.technician),
			]);
			if (change.date) message += `, ${__("moved to {0}", [mp_when(change.date)])}`;
		} else if (change.date && change.technician !== undefined) {
			message = __("{0} moved to {1}, assigned to {2}", [
				site,
				mp_when(change.date),
				change.technician ? this.tech_name(change.technician) : __("nobody"),
			]);
		} else if (change.date) {
			message = __("{0} moved to {1}", [site, mp_when(change.date)]);
		} else if (change.technician !== undefined) {
			message = change.technician
				? __("{0} assigned to {1}", [site, this.tech_name(change.technician)])
				: __("{0} is now unassigned", [site]);
		} else if (change.tentative !== undefined && Object.keys(change).length === 1) {
			message = change.tentative ? __("{0} is now pencilled in", [site]) : __("{0} is now firm", [site]);
		} else {
			message = __("{0} updated", [site]);
		}
		return this.send(visit ? "move_visit" : "move_projected", args, {
			snapshot,
			message,
			noun: visit ? "visit" : "contract",
		});
	}

	// Send one call. When the server answers needs_reason, ask why and send it again with the reason.
	// Resolves with the result, or null when it was refused, backed out of or failed.
	send(method, args, opts) {
		opts = opts || {};
		// C1 draft mode: a write it cannot hold back is refused here; the rest go with draft=1.
		if (this.c1_refuse(method)) return Promise.resolve(null);
		args = this.c1_with_draft(method, args);
		// P4: a live change to who is on a visit, or when, asks "Notify crew?" first.
		const asked = this.mpas_with_notify(method, args, opts);
		if (asked) return asked;
		return Promise.resolve(frappe.call({ method: MP.methods[method], args }))
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
						: this.ask_reason(result.conflicts || {}, opts.noun);
					return ask.then((reason) =>
						reason ? this.send(method, Object.assign({}, args, { reason }), opts) : null
					);
				}
				let message = opts.message;
				if (message && result.drafted && !result.draft_saved) {
					message += ". " + __("The visit is drafted and on the technician's list.");
				}
				if (message && result.draft_saved) message += ` (${__("draft, not published")})`;
				let toasted = false;
				if (opts.snapshot) {
					// A projected visit moved into the drafting window becomes a record at once, and its
					// own undo is moving that record, which the page cannot yet name.
					toasted = this.push_undo(
						result.drafted && opts.snapshot.kind === "projected"
							? { kind: "drafted", site: opts.snapshot.site }
							: opts.snapshot,
						message
					);
				}
				if (result.name && result.modified) this.modified[result.name] = result.modified;
				if (message && !toasted) frappe.show_alert({ message, indicator: "green" }, 5);
				(result.warnings || []).forEach((warning) =>
					frappe.show_alert({ message: warning, indicator: "orange" }, 10)
				);
				return result;
			})
			.catch(() => null);
	}

	// The conflicts a move would cause, per technician, and a required reason. Resolves with the
	// reason, or null when the planner backs out.
	ask_reason(conflicts, noun) {
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
						`<div class="mp-why"><b>${mp_esc(who)}</b><ul>${(list || [])
							.map((text) => `<li>${mp_esc(text)}</li>`)
							.join("")}</ul></div>`
				)
				.join("");
			const where =
				noun === "contract"
					? __("the contract's timeline")
					: noun === "task"
					? __("the task's timeline")
					: __("the visit's timeline");
			const dialog = new frappe.ui.Dialog({
				title: __("This booking has conflicts"),
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "conflicts",
						options: `<p>${mp_esc(
							__("You can save it anyway. Say why: the reason is added to {0}.", [where])
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

	// ------------------------------------------------------------------ undo

	push_undo(snapshot, message) {
		this.undo_stack.push(snapshot);
		while (this.undo_stack.length > MP.undo_max) this.undo_stack.shift();
		this.update_undo_button();
		return this.p6a_undo_toast(snapshot, message);
	}

	update_undo_button() {
		if (!this.$undo) return;
		const last = this.undo_stack[this.undo_stack.length - 1];
		this.$undo
			.toggleClass("mp-empty", !last)
			.attr("title", last ? __("Undo the last change to {0}", [last.site]) : __("Nothing to undo"));
	}

	// Put the visit back the way it was before the last change: a visit goes back through move_visit
	// (its date and technician), a projected visit through move_projected. A date already in the past
	// cannot be returned to, and a visit cannot be unscheduled, so those stay and the planner says so.
	undo() {
		this.p6a_close_toast();
		const snap = this.undo_stack.pop();
		this.update_undo_button();
		if (!snap) {
			frappe.show_alert({ message: __("Nothing to undo"), indicator: "blue" }, 4);
			return;
		}
		if (this.p6b_undo(snap)) return;
		if (this.p6e_undo(snap)) return;
		if (this.fm_undo(snap)) return;
		const today = this.today();
		const reason = __(MP.undo_reason);
		if (snap.kind === "drafted") {
			frappe.show_alert(
				{
					message: __("{0} was drafted when it moved: move the visit card to change it.", [snap.site]),
					indicator: "orange",
				},
				8
			);
			return;
		}
		if (snap.kind === "projected") {
			if (!snap.to_date || snap.to_date < today) {
				frappe.show_alert(
					{ message: __("{0} was on a day that has passed, so it stays where it is.", [snap.site]), indicator: "orange" },
					8
				);
				return;
			}
			this.send(
				"move_projected",
				{
					contract: snap.contract,
					from_date: snap.from_date,
					to_date: snap.to_date,
					serial_no: snap.serial_no || null,
				},
				{ message: __("Undone: {0}", [snap.site]), auto_reason: reason, noun: "contract" }
			).finally(() => this.load());
			return;
		}
		const card = this.by_key[snap.record];
		const args = {
			record: snap.record,
			modified: this.modified[snap.record] || (card && card.modified) || "",
			technician: snap.technician || "",
		};
		const date_back = !!(snap.date && snap.date >= today);
		if (date_back) args.date = snap.date;
		// P4: the change's "Notify crew?" answer, again. null, not undefined: an Undo is never asked.
		args.notify = snap.notify != null ? snap.notify : null;
		// Crew, length and Full day go back too, but only what differs now (everything, when the visit is
		// not on screen to compare with).
		if (snap.crew && (!card || this.crew_key(this.crew_rows(card)) !== this.crew_key(snap.crew))) {
			args.crew = JSON.stringify(snap.crew);
		}
		if (snap.planned_hours !== undefined && (!card || (Number(card.planned_hours) || 0) !== snap.planned_hours)) {
			args.planned_hours = snap.planned_hours;
		}
		if (snap.full_day !== undefined && (!card || (card.full_day ? 1 : 0) !== snap.full_day)) {
			args.full_day = snap.full_day;
		}
		if (snap.tentative !== undefined && (!card || (card.tentative ? 1 : 0) !== snap.tentative)) {
			args.tentative = snap.tentative;
		}
		if (card) {
			card.saving = true;
			this.render();
		}
		this.send("move_visit", args, {
			message: __("Undone: {0}", [snap.site]),
			auto_reason: reason,
			noun: "visit",
		})
			.then((result) => {
				if (result && !date_back && card && card.date !== snap.date) {
					frappe.show_alert(
						{
							message: __("{0} keeps its day: a visit cannot go back to a day that has passed or to unscheduled.", [
								snap.site,
							]),
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
		if (card.kind === "move") {
			frappe.set_route("Form", "Task", card.name);
			return;
		}
		if (card.kind === "booking") {
			// Travel days open their trip; project and rental bookings are Tasks.
			if (card.booking_kind === "travel") frappe.set_route("Form", "Travel Trip", card.ref);
			else frappe.set_route("Form", "Task", card.ref);
			return;
		}
		const rows = [];
		const add = (label, value) => {
			if (value) rows.push(`<tr><th>${mp_esc(label)}</th><td>${mp_esc(value)}</td></tr>`);
		};
		const status = {
			draft: __("Scheduled"),
			pending: __("Pending review"),
			done: __("Done"),
		};
		add(__("Site"), card.site_full);
		add(__("Visit"), card.label ? __(card.label) : card.kind === "visit" ? __("Regular visit") : "");
		add(__("Water feature"), card.feature);
		add(__("Technician"), card.technician ? this.tech_name(card.technician) : __("Unassigned"));
		const helpers = (card.crew || []).filter((member) => member && member.user && member.user !== card.technician);
		add(
			__("Crew"),
			helpers
				.map((member) => {
					const hours = Number(member.hours) > 0 ? ` (${__("{0}h", [mp_hours(member.hours)])})` : "";
					return `${this.tech_name(member.user)}${hours}`;
				})
				.join(", ")
		);
		if (card.full_day) add(__("Length"), __("Full day for each person"));
		else if (Number(card.planned_hours) > 0) add(__("Length"), __("{0}h for each person", [mp_hours(card.planned_hours)]));
		else if (this.visit_people(card).length > 1 && Number(card.hours) > 0) {
			add(__("Length"), __("{0}h for each person", [mp_hours(card.hours)]));
		}
		add(
			__("Status"),
			card.kind === "projected"
				? card.movable
					? __("Not drafted yet. This is the contract's next visit.")
					: __("Projected. It follows whenever the visit before it is done.")
				: status[card.status]
		);
		add(__("Date"), card.date ? frappe.datetime.str_to_user(card.date) : __("Not scheduled"));
		if (card.tentative) {
			add(__("Pencil"), __("Tentative: soft hours, never a conflict; nobody is assigned, texted or told the date until it is firmed up."));
		}
		if (card.drafted && this.draft_on) add(__("Draft"), __("Changed in Draft mode and not published yet."));
		// How that technician's day looks: free hours, a day off, anything wrong with it.
		const cell = card.technician && card.date ? this.cell_of(card.technician, card.date) : null;
		if (cell) {
			add(__("Their day"), this.avail_state(cell).text);
			(cell.conflicts || []).forEach((text) => add(__("Conflict"), text));
		}
		if (card.kind === "projected") add(__("Frequency"), card.frequency);
		if (card.kind === "visit" && card.status !== "done") add(__("Filled in"), `${Math.round(card.completion || 0)}%`);
		if (card.started) add(__("Note"), __("This visit has been started, so its date is changed on the visit form."));
		add(__("Contract"), card.contract);

		const links = [];
		if (card.kind === "visit") {
			links.push(["form", __("Open visit form")]);
			if (card.status !== "done") links.push(["wizard", __("Open in Visit Wizard")]);
		}
		if (card.contract) links.push(["contract", __("Open contract")]);
		links.push(...this.phase5_links(card));
		links.push(...this.p6a_links(card));
		links.push(...this.p6c_links(card));

		const fields = [
			{
				fieldtype: "HTML",
				fieldname: "summary",
				options:
					`<table class="mp-summary">${rows.join("")}</table>` +
					`<div class="mp-links">${links
						.map(
							([action, label]) =>
								`<button type="button" class="btn btn-default btn-xs" data-action="${action}">${mp_esc(label)}</button>`
						)
						.join("")}</div>`,
			},
		];
		if (card.movable) {
			fields.push({
				fieldtype: "Date",
				fieldname: "date",
				label: card.kind === "visit" ? __("Scheduled date") : __("Next visit date"),
				default: card.date || "",
				reqd: card.kind === "projected" ? 1 : 0,
			});
			if (card.kind === "visit") {
				fields.push({
					fieldtype: "Select",
					fieldname: "technician",
					label: __("Technician"),
					options: [{ label: __("Unassigned"), value: "" }].concat(
						((this.data && this.data.technicians) || [])
							.filter((person) => person.enabled || person.user === card.technician)
							.map((person) => ({ label: person.name, value: person.user }))
					),
					default: card.technician || "",
				});
				// Who else goes: filled in below as a checkbox and optional hours for each person.
				fields.push(
					{ fieldtype: "Section Break", label: __("Crew") },
					{ fieldtype: "HTML", fieldname: "crew_editor", options: '<div class="mp-cm-list"></div>' },
					{ fieldtype: "Section Break" },
					{
						fieldtype: "Float",
						fieldname: "planned_hours",
						label: __("Planned hours"),
						default: Number(card.planned_hours) || 0,
						description: __("Hours each person is booked. Blank or 0: the site's default length."),
					},
					{ fieldtype: "Column Break" },
					{
						fieldtype: "Check",
						fieldname: "full_day",
						label: __("Full day"),
						default: card.full_day ? 1 : 0,
						description: __("The visit takes each person's whole day."),
					},
					{
						fieldtype: "Check",
						fieldname: "tentative",
						label: __("Tentative (pencil)"),
						default: card.tentative ? 1 : 0,
						description: __("Pencilled in: soft hours that never need a reason. Nobody is assigned, texted or told the date until it is firmed up."),
					}
				);
			}
		}

		const dialog = this.p6a_dialog({ title: card.site || card.site_full || card.name, fields }, card);
		if (card.movable) {
			dialog.set_primary_action(card.kind === "visit" ? __("Save") : __("Move next visit"), (values) => {
				const change = {};
				if (values.date && values.date !== card.date) change.date = values.date;
				if (card.kind === "visit" && (values.technician || "") !== (card.technician || "")) {
					change.technician = values.technician || "";
				}
				if (card.kind === "visit") {
					// Only what changed goes to the server.
					const lead = values.technician || "";
					const crew = this.read_crew_editor(dialog, lead);
					if (this.crew_key(crew) !== this.crew_key(this.crew_rows(card))) change.crew = crew;
					const planned = Math.max(0, Number(values.planned_hours) || 0);
					if (planned !== (Number(card.planned_hours) || 0)) change.planned_hours = planned;
					const full_day = values.full_day ? 1 : 0;
					if (full_day !== (card.full_day ? 1 : 0)) change.full_day = full_day;
					const tentative = values.tentative ? 1 : 0;
					if (tentative !== (card.tentative ? 1 : 0)) change.tentative = tentative;
				}
				if (!Object.keys(change).length) {
					dialog.hide();
					return;
				}
				const today = this.today();
				if (change.date && change.date < today) {
					frappe.msgprint(__("Pick today or a later day."));
					return;
				}
				if (card.kind === "projected" && !change.date) {
					dialog.hide();
					return;
				}
				dialog.hide();
				this.apply(card, change);
			});
		}
		dialog.show();
		if (card.movable && card.kind === "visit") this.build_crew_editor(dialog, card);
		dialog.$wrapper.find("[data-action]").on("click", (e) => {
			const action = e.currentTarget.getAttribute("data-action");
			dialog.hide();
			if (this.p6a_action(action, card)) return;
			if (this.p6c_action(action, card)) return;
			if (action === "form") frappe.set_route("Form", "Sapphire Maintenance Record", card.name);
			else if (action === "wizard") frappe.set_route("visit-wizard", { record: card.name });
			else if (action === "contract") frappe.set_route("Form", "Sapphire Maintenance Contract", card.contract);
			else this.phase5_action(action, card);
		});
	}

	// The people who can go on a visit: every active technician and Field helper, plus anyone already
	// on it. A checkbox for each, and an hours box that is blank when they just share the visit's length.
	build_crew_editor(dialog, card) {
		const $list = dialog.$wrapper.find(".mp-cm-list");
		const on = {};
		(card.crew || []).forEach((member) => {
			if (member && member.user) on[member.user] = member;
		});
		const people = [];
		const seen = {};
		const add = (user, name) => {
			if (!user || seen[user]) return;
			seen[user] = true;
			people.push({ user, name: name || this.tech_name(user) });
		};
		((this.data && this.data.technicians) || []).forEach((person) => {
			if (person.enabled || on[person.user]) add(person.user, person.name);
		});
		Object.keys(on).forEach((user) => add(user, on[user].name));
		if (!people.length) {
			$("<div class='mp-empty-note'></div>").text(__("No technicians to add.")).appendTo($list);
			return;
		}
		people.forEach((person) => {
			const $row = $("<div class='mp-cm-row'></div>").attr("data-user", person.user).appendTo($list);
			const $label = $("<label></label>").appendTo($row);
			$("<input type='checkbox' class='mp-cm-on'>").prop("checked", !!on[person.user]).appendTo($label);
			$("<span></span>").text(person.name).appendTo($label);
			const hours = on[person.user] && Number(on[person.user].hours) > 0 ? Number(on[person.user].hours) : "";
			$("<input type='number' min='0' step='0.25' class='form-control input-sm mp-cm-hours'>")
				.attr("placeholder", __("hours"))
				.attr("aria-label", __("{0}: hours", [person.name]))
				.val(hours)
				.appendTo($row);
		});
		// The technician is not also a crew member: grey that row out as the technician changes.
		const sync = () => {
			const lead = dialog.get_value("technician") || "";
			$list.find(".mp-cm-row").each((i, el) => {
				const is_lead = el.getAttribute("data-user") === lead;
				$(el).toggleClass("mp-cm-lead", is_lead);
				$(el).find("input").prop("disabled", is_lead);
			});
		};
		const technician = dialog.fields_dict.technician;
		if (technician && technician.$input) technician.$input.on("change", sync);
		sync();
	}

	// The crew as ticked in the dialog, without the technician: [{user, hours}].
	read_crew_editor(dialog, lead) {
		const rows = [];
		dialog.$wrapper.find(".mp-cm-list .mp-cm-row").each((i, el) => {
			const user = el.getAttribute("data-user");
			if (!user || user === lead) return;
			if (!$(el).find(".mp-cm-on").prop("checked")) return;
			const hours = Number($(el).find(".mp-cm-hours").val());
			rows.push({ user, hours: hours > 0 ? hours : null });
		});
		return rows;
	}
}

// ====================================================================== Project Planner Phase 5
//
// "Preview customer email" in a visit's dialog: what the customer date confirmation would say for
// this visit, and to whom, rendered by the Project Planner's preview endpoint (the confirmation
// covers both planners). It never sends, works while confirmations are switched off (they ship
// off), and opens in a sandboxed frame so the email's styles and links stay inside it. Only stored
// visits have one: a projected visit has no record yet. The endpoint lives in the Project Planner's
// API, so it is named here rather than in MP.methods, which lists this page's own endpoints.
// The hooks into the class above are one line each: phase5_links and phase5_action (open_card).

const MP5 = {
	preview_method: "erpnext_enhancements.api.project_planner.preview_customer_confirmation",
	preview_roles: ["System Manager", "Projects Manager"],
	record: "Sapphire Maintenance Record",
};

const MP5_METHODS = {
	phase5_links(card) {
		const can = MP5.preview_roles.some((role) => frappe.user.has_role(role));
		return card.kind === "visit" && card.name && can ? [["customer_preview", __("Preview customer email")]] : [];
	},

	phase5_action(action, card) {
		if (action === "customer_preview") this.preview_customer_email(card.name);
	},

	preview_customer_email(name) {
		return Promise.resolve(
			frappe.call({
				method: MP5.preview_method,
				args: { doctype: MP5.record, name },
				freeze: true,
				freeze_message: __("Rendering the customer email…"),
			})
		)
			.then((r) => this.show_customer_preview((r && r.message) || {}))
			.catch(() => null);
	},

	show_customer_preview(answer) {
		const status = answer.would_send ? __("This email would be sent.") : __("This email would not be sent now.");
		const to = answer.recipient ? __("To: {0}", [answer.recipient]) : __("To: nobody (no email address found)");
		const notes = (answer.notes || []).map((text) => `<li>${mp_esc(text)}</li>`).join("");
		const parts = [
			`<div class="mp-p5-meta"><b>${mp_esc(status)}</b></div>`,
			`<div class="mp-p5-meta">${mp_esc(to)}</div>`,
		];
		if (answer.subject) parts.push(`<div class="mp-p5-meta">${mp_esc(__("Subject: {0}", [answer.subject]))}</div>`);
		if (notes) parts.push(`<ul class="mp-p5-meta">${notes}</ul>`);
		if (answer.error) parts.push(`<div class="mp-p5-meta"><b>${mp_esc(answer.error)}</b></div>`);
		// The frame's look is .mp-p5-frame in maintenance_planner.css (S3.4e): white, as an email is.
		parts.push(`<iframe class="mp-p5-frame" sandbox="" title="${mp_esc(__("Email preview"))}"></iframe>`);
		const dialog = new frappe.ui.Dialog({
			title: __("Customer email preview"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "preview", options: parts.join("") }],
		});
		dialog.show();
		const $frame = dialog.$wrapper.find("iframe.mp-p5-frame");
		if (answer.html) $frame.attr("srcdoc", answer.html);
		else $frame.hide();
	},
};

Object.assign(MaintenancePlanner.prototype, MP5_METHODS);

// ====================================================================== Phase 6A: quick looks and polish
//
// The Maintenance Planner's half of Phase 6A (the Project Planner has the same, see its own block).
// Nik, 2026-10-09: "click the Technicians name or something and see what their specific schedule is
// in a pop up or something so as not to lose context overall." The quick looks open in the planner
// kit's side drawer (public/js/planner_kit, loaded with frappe.require below); the calendar stays
// visible and usable behind it, and Esc or Back closes it without leaving the planner.
//
//   - click a technician's name (Resources available, a crew-view row, the initials on a visit, an
//     "off" badge)                            their week (planner_views.get_person_schedule, by user)
//   - click a date (day numbers, Resources available's day heads, crew-view column heads)
//                                             every technician's day side by side (get_day_overview)
//   - "Site at a glance" in a visit's panel  the site's upcoming, projected and recent visits, its
//                                             default technician and crew (get_site_overview)
//   - click a visit                          its editor opens in the side panel (planner_kit.panel
//                                             hosts the same fields; the crew editor is unchanged)
//   - drag a visit out of a person, day or site drawer onto a day or a technician's row: the page's
//     own drag_source / plan_drop / drop, so a conflict asks for a reason and Undo works
//   - every change that can be undone shows a toast with Undo; hovering a name lights up that
//     person's visits and bookings (pk-glow); the crew view's and the week view's headers stay put
//     while scrolling; "?" opens the legend
//
// Hooks into the class above, one line each: init_phase6a (constructor), render_phase6a (render),
// p6a_route (go, open_route), p6a_drag_source (drag_source), p6a_edge_scroll (edge_scroll),
// p6a_undo_toast (push_undo, whose answer send() reads so the alert is not shown twice),
// p6a_close_toast (undo), and p6a_dialog, p6a_links and p6a_action (open_card).

const MP6A = {
	// The kit and its stylesheet, both content-hashed bundles (S3.4e). The stylesheet's <link> lands
	// after maintenance_planner.css, so the kit's rules come after the page's, as they always have.
	kit: ["planner_kit.bundle.js", "planner_kit.bundle.css"],
	site_method: "erpnext_enhancements.api.planner_views.get_site_overview",
	owner: "mp",
	widths: { person: 460, day: 760, site: 520, panel: 540 },
	// The first-time tips: since the toolbar redesign (v1.687.0) the first section of the "?" help,
	// not banners under the toolbar.
	hints: [
		["mp-person-peek", "Click a technician's name to see their week without leaving the calendar."],
		["mp-day-peek", "Click a date to see every technician's day side by side."],
		["mp-glow", "Hover over a name to light up all of that person's visits."],
	],
	status: { draft: "Scheduled", pending: "Pending review", done: "Done", projected: "Projected" },
};

const MP6A_METHODS = {
	init_phase6a() {
		this.p6a = { kit: null, peek: null, panel: null, panel_card: null, seen: null, glow: [] };
		this.$p6a_help = $('<button type="button" class="btn btn-default btn-sm mp-p6a-help">?</button>')
			.attr("title", __("How to read the planner"))
			.attr("aria-label", __("How to read the planner"))
			.on("click", () => this.p6a_legend())
			.insertAfter(this.$undo);
		const root = this.$body[0];
		// Capture phase: a name or a date inside a card or a header is a quick look, not the card's
		// own click (which opens the visit) and not the day number's old "show this week".
		root.addEventListener("click", (e) => this.p6a_click(e), true);
		root.addEventListener(
			"keydown",
			(e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				const el = e.target && e.target.closest ? e.target.closest(".mp-p6a-name, [data-pp6a-day]") : null;
				if (!el || e.target !== el) return;
				e.preventDefault();
				e.stopPropagation();
				el.click();
			},
			true
		);
		this.p6a_require(() => this.p6a_ready());
	},

	// The kit and its stylesheet, asked for once (S5c); see the Project Planner's twin for why. Both
	// planners share the one request (window.planner_kit_request), since PP6A.kit is the same two files.
	p6a_require(callback) {
		if (!window.planner_kit_request) window.planner_kit_request = Promise.resolve(frappe.require(MP6A.kit));
		return window.planner_kit_request.then(callback);
	},

	p6a_ready() {
		const kit = window.planner_kit;
		if (!kit || (this.p6a && this.p6a.kit)) return;
		this.p6a.kit = kit;
		kit.drawer.element().addEventListener("pointerdown", (e) => {
			const current = kit.drawer.current();
			if (current && current.owner === MP6A.owner) this.on_down(e);
		});
		this.p6a.glow = [kit.hover_glow(this.$body[0], "person")];
		this.p6c_ready();
		this.tb_ready();
		this.mps_kit_ready(); // Simple mode draws with the kit
	},

	// ------------------------------------------------------------------ hooks

	p6a_route(fn, keep) {
		const kit = this.p6a && this.p6a.kit;
		return kit ? kit.drawer.route(fn, keep) : fn();
	},

	render_phase6a() {
		if (!this.p6a) return;
		this.p6a_decorate();
		this.p6a_refresh_open();
	},

	p6a_undo_toast(snapshot, message) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !message) return false;
		// A projected visit drafted by its move cannot be undone from here (undo() says why).
		if (snapshot && snapshot.kind === "drafted") {
			kit.toast(message, { tone: "success" });
			return true;
		}
		kit.toast(message, {
			action_label: __("Undo"),
			tone: "success",
			on_action: () => {
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

	p6a_dialog(opts, card) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return new frappe.ui.Dialog(opts);
		const people = this.visit_people(card).map((user) => this.tech_name(user));
		const panel = kit.panel(
			Object.assign({}, opts, {
				subtitle: [card.date ? mp_when(card.date) : __("Not scheduled"), people.join(", ")].filter(Boolean).join(" · "),
				width: MP6A.widths.panel,
				key: `visit:${card.key}`,
				owner: MP6A.owner,
				push: this.$body[0],
				reopen: () => {
					const fresh = this.by_key[card.key];
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
		if (!this.p6a || !this.p6a.kit) return [];
		const out = [];
		if (card.project) out.push(["p6a_site", __("Site at a glance")]);
		if (card.technician) out.push(["p6a_person", __("{0}'s week", [this.tech_name(card.technician)])]);
		return out;
	},

	p6a_action(action, card) {
		if (action === "p6a_site") {
			this.p6a_open_site(card);
			return true;
		}
		if (action === "p6a_person") {
			this.p6a_open_person(card.technician, card.date);
			return true;
		}
		return false;
	},

	// A drag that starts in a drawer this page opened: a visit on the calendar that may move.
	p6a_drag_source(el) {
		const kit = this.p6a && this.p6a.kit;
		const current = kit && kit.drawer.current();
		if (!current || current.owner !== MP6A.owner) return null;
		const item = el.closest("[data-pk-drag]");
		if (!item || el.closest("button, a, input, select, textarea")) return null;
		if (item.getAttribute("data-pk-kind") !== "visit") return null;
		const card = this.by_key[item.getAttribute("data-pk-key")];
		if (!card || !card.movable || card.saving) return null;
		return { kind: "card", el: item, card, from_user: item.getAttribute("data-pk-user") || null };
	},

	p6a_edge_scroll(x, y) {
		const el = document.elementFromPoint(x, y);
		const box = el && el.closest ? el.closest(".mp-crew-view .mp-scroll, .mp-week .mp-grid, .mp-res .mp-scroll") : null;
		if (!box || box.scrollHeight <= box.clientHeight) return;
		const rect = box.getBoundingClientRect();
		if (y < rect.top + 40) box.scrollTop -= 14;
		else if (y > rect.bottom - 40) box.scrollTop += 14;
	},

	// ------------------------------------------------------------------ markup the quick looks need

	p6a_decorate() {
		if (!this.data) return;
		const root = this.$body[0];
		const key = (value) => encodeURIComponent(String(value == null ? "" : value));
		root.querySelectorAll(".mp-person[data-user]").forEach((el) => {
			el.setAttribute("data-pk-person", key(el.getAttribute("data-user")));
			el.classList.add("mp-p6a-name");
			el.setAttribute("role", "button");
			el.setAttribute("tabindex", "0");
			if (!el.getAttribute("data-pp6a-tip")) {
				el.setAttribute("data-pp6a-tip", "1");
				el.setAttribute("title", `${el.getAttribute("title") || ""}\n${__("Click to see their week.")}`.trim());
			}
		});
		root.querySelectorAll(".mp-card[data-key]").forEach((el) => {
			const card = this.by_key[el.getAttribute("data-key")];
			if (!card) return;
			const row = el.getAttribute("data-row-user");
			const people = card.kind === "booking" ? [card.technician] : row ? [row] : this.visit_people(card);
			el.setAttribute("data-pk-persons", people.filter(Boolean).map(key).join(" "));
			if (card.kind === "booking") {
				const badge = el.querySelector(".mp-tech");
				if (badge && card.technician) badge.setAttribute("data-pk-person", key(card.technician));
				return;
			}
			const everyone = this.visit_people(card);
			el.querySelectorAll(".mp-techs .mp-tech").forEach((badge, index) => {
				if (everyone[index]) badge.setAttribute("data-pk-person", key(everyone[index]));
			});
		});
		const planned = (this.data.technicians || []).filter((person) => person.enabled && person.resource);
		root.querySelectorAll(".mp-day[data-date]").forEach((el) => {
			const ymd = el.getAttribute("data-date");
			const num = el.querySelector(".mp-day-num");
			if (num) {
				num.setAttribute("data-pp6a-day", ymd);
				num.setAttribute("title", __("See everyone's day"));
				num.setAttribute("aria-label", __("See everyone's day on {0}", [this.day_words(ymd)]));
				num.setAttribute("tabindex", "0");
			}
			const off = planned.filter((person) => this.is_off(this.cell_of(person.user, ymd)));
			el.querySelectorAll(".mp-off-badges .mp-tech").forEach((badge, index) => {
				if (off[index]) badge.setAttribute("data-pk-person", key(off[index].user));
			});
		});
		root.querySelectorAll(".mp-crew-head[data-week]").forEach((el) => {
			el.setAttribute("data-pp6a-day", el.getAttribute("data-week"));
			el.setAttribute("title", __("See everyone's day"));
			el.setAttribute("aria-label", __("See everyone's day on {0}", [this.day_words(el.getAttribute("data-week"))]));
			el.setAttribute("tabindex", "0");
		});
		const days = this.range_days();
		root.querySelectorAll(".mp-res-grid > .mp-res-head").forEach((el, index) => {
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
		if (!target || !target.closest) return;
		if (target.closest("button, a, input, select, textarea, .mp-route, [data-route-resource]")) return;
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
		const day = target.closest("[data-pp6a-day]");
		if (day) {
			take();
			this.p6a_open_day(day.getAttribute("data-pp6a-day"));
		}
	},

	// ------------------------------------------------------------------ the quick looks

	// The week the quick looks open on: the week of `on` when a day is asked for (a click on Thursday the 29th
	// in the month view opens that week, not today's), else the week on screen, or in the month view the one
	// holding today.
	p6a_week_start(on) {
		const today = this.today();
		const { start, end } = this.range();
		let base = this.anchor;
		if (this.view === "month" && today >= mp_ymd(start) && today <= mp_ymd(end)) base = today;
		if (on && moment(on, "YYYY-MM-DD", true).isValid()) base = on;
		const day = moment(base || today, "YYYY-MM-DD");
		return mp_ymd(day.clone().subtract((day.day() - this.first_weekday() + 7) % 7, "days"));
	},

	// The page owns visits: only a visit card on the calendar that may move leaves a drawer by drag.
	p6a_can_drag(booking) {
		if (booking.kind !== "visit") return false;
		const card = this.by_key[booking.key];
		return !!(card && card.movable && card.kind !== "booking");
	},

	p6a_open_person(user, selected) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !user) return;
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.person({
			user,
			label: this.tech_name(user),
			start: this.p6a_week_start(selected),
			selected: selected || null,
			days: 7,
			owner: MP6A.owner,
			push: this.$body[0],
			width: MP6A.widths.person,
			can_drag: (booking) => this.p6a_can_drag(booking),
			on_full_route: (resource, ymd) =>
				kit.drawer.navigate(() => frappe.set_route(MP.project_route, "route", resource, ymd)),
			actions: this.p6d_person_actions(user),
		});
	},

	p6a_open_day(ymd) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !ymd) return;
		const listed = new Set(((this.data && this.data.technicians) || []).map((person) => person.user));
		this.p6a.seen = this.data;
		this.p6a.peek = kit.peeks.day({
			date: ymd,
			owner: MP6A.owner,
			push: this.$body[0],
			width: MP6A.widths.day,
			only: (person) => !listed.size || (person.user && listed.has(person.user)),
			person_key: (person) => person.user || person.resource,
			can_drag: (booking) => this.p6a_can_drag(booking),
			on_person: (person) => {
				if (person.user) this.p6a_open_person(person.user, ymd);
			},
			on_week: (date) => kit.drawer.navigate(() => this.go("week", date)),
			...this.p6d_day_peek_opts(),
		});
	},

	p6a_open_site(card) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !card || !card.project) return;
		const project = card.project;
		const state = { data: null, token: 0 };
		this.p6a.seen = this.data;
		const handle = kit.drawer.open({
			title: card.site || card.site_full || project,
			subtitle: __("Loading…"),
			body: `<p class="pk-empty">${mp_esc(__("Loading…"))}</p>`,
			width: MP6A.widths.site,
			key: `site:${project}`,
			owner: MP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6a_open_site(card),
			on_click: (e) => {
				const row = e.target && e.target.closest ? e.target.closest("[data-mp6a-visit]") : null;
				if (!row || Date.now() < this.click_blocked_until) return;
				const found = this.by_key[row.getAttribute("data-mp6a-visit")];
				if (found) this.open_card(found);
				else if (row.getAttribute("data-mp6a-record")) {
					const name = row.getAttribute("data-mp6a-record");
					kit.drawer.navigate(() => frappe.set_route("Form", "Sapphire Maintenance Record", name));
				}
			},
		});
		const load = () => {
			const token = ++state.token;
			if (state.data) handle.body.classList.add("pk-busy");
			return Promise.resolve(frappe.call({ method: MP6A.site_method, args: { project } }))
				.then((r) => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					state.data = (r && r.message) || null;
					const data = state.data || {};
					handle.set_title(data.site || data.title || card.site || project);
					handle.set_subtitle([data.customer_name, project].filter(Boolean).join(" · "));
					handle.set_body(this.p6a_site_html(state.data));
					const actions = [];
					if (data.profile && data.profile.name) {
						actions.push({
							label: __("Open Maintenance Profile"),
							on_click: () =>
								kit.drawer.navigate(() => frappe.set_route("Form", "Sapphire Maintenance Profile", data.profile.name)),
						});
					}
					const contract = (data.contracts || [])[0];
					if (contract && contract.name) {
						actions.push({
							label: __("Open contract"),
							on_click: () =>
								kit.drawer.navigate(() => frappe.set_route("Form", "Sapphire Maintenance Contract", contract.name)),
						});
					}
					handle.set_actions(actions);
				})
				.catch(() => {
					if (token !== state.token || !handle.is_open()) return;
					handle.body.classList.remove("pk-busy");
					handle.set_body(this.p6a_site_html(null));
				});
		};
		this.p6a.peek = { data: () => state.data, refresh: load, is_open: () => handle.is_open() };
		load();
		this.p6c_site_drawer(card, handle);
	},

	p6a_visit_row(visit) {
		const card = this.by_key[visit.key];
		const grab = !!(card && card.movable && card.kind !== "booking");
		const people = [visit.technician_name || __("Unassigned")].concat((visit.crew || []).map((member) => member.name));
		const what = visit.label ? __(visit.label) : visit.feature || (visit.kind === "projected" ? __("Projected visit") : __("Regular visit"));
		const status = __(MP6A.status[visit.status] || visit.status || "");
		const meta = [status, people.filter(Boolean).join(", ")].filter(Boolean).join(" · ");
		const chips = visit.overdue ? `<span class="pk-chip pk-chip-red">${mp_esc(__("Overdue"))}</span>` : "";
		const drag = grab
			? ` data-pk-drag="1" data-pk-kind="visit" data-pk-ref="${mp_esc(visit.name || "")}" data-pk-key="${mp_esc(
					visit.key
			  )}" data-pk-date="${mp_esc(visit.date || "")}" data-pk-user=""`
			: "";
		const record = visit.kind === "visit" && visit.name ? ` data-mp6a-record="${mp_esc(visit.name)}"` : "";
		const tip = [what, meta, grab ? __("Drag onto the calendar to move it; click to open it.") : ""].filter(Boolean).join("\n");
		return (
			`<li class="pk-item pk-k-visit mp-p6a-row${grab ? " pk-grab" : ""}" data-mp6a-visit="${mp_esc(visit.key)}"${record}${drag}` +
			` title="${mp_esc(tip)}"><span class="pk-kind">${mp_esc(visit.date ? mp_when(visit.date) : __("No date"))}</span>` +
			`<span class="pk-item-main"><span class="pk-item-label">${mp_esc(what)}</span>` +
			`<span class="pk-item-meta">${mp_esc(meta)}</span></span>${chips}</li>`
		);
	},

	p6a_site_html(data) {
		if (!data) return `<p class="pk-empty">${mp_esc(__("The site could not be loaded. Try again."))}</p>`;
		const facts = [];
		const fact = (label, value) => {
			if (value) facts.push(`<tr><th>${mp_esc(label)}</th><td>${mp_esc(value)}</td></tr>`);
		};
		fact(__("Site"), data.title);
		fact(__("Account"), data.customer_name);
		fact(
			__("Contract"),
			(data.contracts || []).map((row) => `${row.name}${row.status ? ` (${__(row.status)})` : ""}`).join(", ")
		);
		const profile = data.profile;
		if (profile) {
			fact(__("Default technician"), profile.default_technician_name);
			fact(
				__("Default crew"),
				(profile.crew || [])
					.map((member) => (Number(member.hours) > 0 ? `${member.name} (${mp_hours(member.hours)}h)` : member.name))
					.join(", ")
			);
			fact(
				__("Visit length"),
				profile.full_day ? __("Full day") : Number(profile.visit_hours) > 0 ? __("{0}h", [mp_hours(profile.visit_hours)]) : ""
			);
		} else {
			fact(__("Maintenance Profile"), __("None yet"));
		}
		const section = (title, visits, empty) =>
			`<div class="pk-section-title">${mp_esc(title)}</div>` +
			(visits.length
				? `<ul class="pk-items pk-day">${visits.map((visit) => this.p6a_visit_row(visit)).join("")}</ul>`
				: `<p class="pk-empty">${mp_esc(empty)}</p>`);
		return (
			`<div class="mp-p6a-site"><table class="pk-facts">${facts.join("")}</table>` +
			section(__("Coming up"), data.upcoming || [], __("No visit is scheduled.")) +
			section(__("Projected from the contract"), data.projected || [], __("Nothing projected in the next 90 days.")) +
			section(__("Recent visits"), data.recent || [], __("No visits yet.")) +
			`</div>`
		);
	},

	// After a load: the visit in the side panel is reopened from its new card (fresh `modified`) when
	// it changed; one no longer on the calendar (another week on screen) stays as it is. An open
	// person, day or site drawer asks for its numbers again.
	p6a_refresh_open() {
		const p6a = this.p6a;
		if (!p6a.kit || !this.data) return;
		if (p6a.panel && p6a.panel.is_open() && p6a.panel_card) {
			const fresh = this.by_key[p6a.panel_card.key];
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
			card.date,
			card.technician,
			card.planned_hours,
			card.full_day,
			(card.crew || []).map((member) => [member.user, member.hours]),
		]);
	},

	// ------------------------------------------------------------------ legend and hints

	// The tips, as the help's first section.
	p6a_tip_section() {
		return { title: __("Tips"), items: MP6A.hints.map(([, text]) => ({ sample_html: "", text: __(text) })) };
	},

	p6a_legend() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return;
		kit.legend(this.legend_sections(), {
			title: __("How to read the Maintenance Planner"),
			owner: MP6A.owner,
			actions: this.tour_legend_actions(), // P12: Take the tour
		});
	},

	// The "?" help, section by section: the ONE provider function. Everything the help says is built here
	// or by a provider the sections below call, so the key of the visit kinds, the project-work strip and
	// Resources available live in "?" and nowhere on the toolbar (P10, section 4.9). A later change (P12's
	// tips and tutorial) adds a section here, or registers a function that returns sections:
	//
	//   this.p6_legend_providers.push(() => [{ title: __("..."), items: [{ sample_html: "", text: __("...") }] }]);
	//
	// which lands after Keyboard and Faster scheduling (p6b_legend_sections); a provider that throws is skipped.
	legend_sections() {
		const chip = (cls, text) => `<span class="mp-chip${cls ? ` ${cls}` : ""}">${mp_esc(__(text))}</span>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		// A sample visit is a real card with its kind's class (S3.4e), so the legend shows exactly the
		// edge and ground the calendar draws (maintenance_planner.css), in either theme.
		const card = (cls, label, text) =>
			item(`<span class="mp-card mp-p6a-sample-card${cls ? ` ${cls}` : ""}">${mp_esc(__(label))}</span>`, text);
		const bar = (cls, width) => `<span class="mp-p6a-bar ${cls}"><span class="mp-bar"><i style="width:${width}%"></i></span></span>`;
		return [
			this.p6a_tip_section(),
			...this.p12_legend_sections(), // P12: the views, moving a visit, Draft mode and Notify crew?
			...this.p6c_legend_sections(),
			{
				title: __("Quick looks"),
				items: [
					item("", "Click a technician's name (in the panel, a crew row or the initials on a visit) to see their week: visits, project work, free hours, days off and the day's stops in driving order."),
					item("", "Click a date to see every technician's day side by side."),
					item("", "In a visit's side panel, Site at a glance shows the site's coming, projected and recent visits and its default crew."),
					item("", "Drag a visit out of any of these onto a day or a technician's row to move it. Esc or Back closes the side panel."),
					item("", "Hover over a name to light up all of that person's visits and bookings."),
				],
			},
			// The key of the visit kinds: the toolbar's legend row, then More > Display, now only here (P10).
			{
				title: __("Visit cards"),
				items: [
					card("", "Visit", "A scheduled visit. Drag it to another day; only a draft nobody has started moves."),
					card("mp-seasonal", "Startup", "A seasonal visit (startup, winterization). Before it is drafted it sits on the 1st of its month."),
					card("mp-extra", "Follow-up", "An extra visit or a chemistry follow-up."),
					card("mp-pending", "Visit", "Pending review: the technician has sent it to the office."),
					card("mp-done", "Visit", "Done."),
					card("mp-projected", "Projected", "Projected from the contract, not drafted yet. It follows whenever the visit before it is done."),
					card("mp-projected mp-movable", "Next visit", "The contract's next visit (dashed, blue edge): moving it moves the contract's next visit date."),
					card("mp-overdue", "Visit", "Overdue: a draft whose day has passed."),
				],
			},
			...this.p10_legend_sections(),
			{
				title: __("Chips on a visit"),
				items: [
					item(chip("", "Next visit"), "The contract's stored next visit date."),
					item(chip("", "Pending review"), "Waiting for the office to approve it."),
					item(chip("", "Full day"), "It takes each person's whole day."),
					item(chip("", "2h each"), "How long it books each person, when that is not the default."),
					item(chip("mp-red", "On hold"), "The account is on a service hold."),
					item(chip("mp-red", "Chemistry"), "A reading on the visit was out of range."),
					item(chip("mp-red", "Day off: Holiday"), "The technician does not work that day: move it or give it to someone else."),
				],
			},
			{
				title: __("People"),
				items: [
					item(`<span class="mp-tech mp-p6a-sample-badge">AH</span>`, "Everyone on the visit. Click one to see their week."),
					item(`<span class="mp-tech mp-lead mp-p6a-sample-lead">JD</span>`, "Ringed: the technician who fills the visit in."),
					item(`<span class="mp-tech mp-tech-off">KF</span>`, "Faded and striped: off that day (shown with All technicians)."),
				],
			},
			{
				title: __("A technician's day"),
				items: [
					item(bar("mp-green", 50), "Up to 75% of their hours booked."),
					item(bar("mp-amber", 90), "More than 75% booked."),
					item(bar("mp-red", 100), "Over their hours."),
					item(`<span class="mp-p6a-sample mp-offday">${mp_esc(__("Holiday"))}</span>`, "Not working: a holiday, time off or not a work day. The type and reason of time off are never shown."),
					item(`<span class="mp-p6a-sample mp-conflict">${mp_esc(__("1h free"))}</span>`, "Red outline: a conflict on that day."),
					item(chip("mp-red", "Long drive"), "More driving than the Settings limit."),
					item(`<span class="mp-p6a-sample">${MP_ROUTE_ICON}</span>`, "Open the day's route on the Project Planner. A ~ before a drive time means it is an estimate."),
				],
			},
			{
				title: __("Dragging"),
				items: [
					item(`<span class="mp-p6a-sample mp-over">${mp_esc(__("Drop here"))}</span>`, "Where the visit will land."),
					item(`<span class="mp-p6a-sample mp-over-no">${mp_esc(__("Past day"))}</span>`, "A day before today: visits cannot move there."),
					item("", "On a touch screen, hold a card for a moment before you drag it. Drag a technician's name onto a visit to add them to its crew."),
					item("", "A change that causes a conflict asks for a reason, never blocks. Undo puts the last change back."),
				],
			},
			...this.p6d_legend_sections(),
		].concat(this.p6b_legend_sections());
	},
};

Object.assign(MaintenancePlanner.prototype, MP6A_METHODS);

// ====================================================================== Phase 6B: faster scheduling
//
// The Maintenance Planner's half of Phase 6B (TASK-2026-02468; the Project Planner has the rest, see
// its own block). What applies to visits:
//
//   - right-click a visit, an empty spot or a name (a long press of about half a second on a touch
//     screen, when the finger does not move)  a menu (planner_kit.menu): Edit, Assign to..., Move to
//                                            next free day, Site at a glance, the technician's week;
//                                            Everyone's day on an empty spot; See their week on a
//                                            name. Other phases add items through this.p6_menu_providers
//   - Shift-, Ctrl- or Cmd-click draft visits a selection (this.p6_selection, visit keys); drag one and
//                                            they all move by the same number of calendar days, one
//                                            move_visit each after a single confirmation, one reason
//                                            for the lot and one Undo
//   - the search box (or /)                  visits, Fountain Move tasks, sites and technicians on screen
//                                            first, then the server (search_planner, planner="maintenance")
//   - T, the arrows, 1 2 3, Ctrl/Cmd+Z, ?, Esc keyboard shortcuts, listed in the legend
//
// Left out on purpose: Duplicate and Split (a visit has neither) and resizing (no visit spans several
// days). Pencil was left out of 6B because a visit had none; C1 (v1.601.0) added it, through
// this.p6_menu_providers in its own block. "Add visit here" was left out too; Phase 6E added it.
//
// Nothing here picks a person: Assign to... lists the technicians who are free that day
// (project_planner.who_is_free) and the planner clicks one. Every write is the page's own: move_visit
// and add_crew through send(), so the reason prompt and Undo apply.
//
// Hooks into the class above, one line each: init_phase6b (constructor), render_phase6b (render),
// p6b_lift (lift), p6b_drop (drop), p6b_undo (undo), and p6b_legend_sections (p6a_legend).

const MP6B = {
	who_is_free: "erpnext_enhancements.api.project_planner.who_is_free",
	next_free_day: "erpnext_enhancements.api.planner_conflicts.get_next_free_day",
	search: "erpnext_enhancements.api.planner_actions.search_planner",
	long_press_ms: 500,
	search_min: 2,
	search_wait_ms: 250,
	search_limit: 20,
	flash_ms: 2600,
	default_visit_hours: 2,
	widths: { assign: 440, panel: 460 },
};

// Pure helpers, the same as the Project Planner's PP6B_PURE (tests/test_planner_phase6b.py runs the
// same cases through both under node).
const MP6B_PURE = {
	ymd_add(ymd, days) {
		const date = new Date(`${ymd}T00:00:00Z`);
		date.setUTCDate(date.getUTCDate() + (Number(days) || 0));
		return date.toISOString().slice(0, 10);
	},
	ymd_diff(a, b) {
		return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
	},
	toggle(set, key) {
		if (set.has(key)) {
			set.delete(key);
			return false;
		}
		set.add(key);
		return true;
	},
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
	search_local(q, entries, limit) {
		const words = String(q || "")
			.toLowerCase()
			.split(/\s+/)
			.filter(Boolean);
		if (!words.length) return [];
		const order = { person: 0, project: 1, site: 1, task: 2, visit: 2, move: 2 };
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

const MP6B_METHODS = {
	init_phase6b() {
		// The registries other phases add to (phase6-wave2): menu items and legend sections.
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_legend_providers = this.p6_legend_providers || [];
		// The multi-selection: visit keys. 6C's print reads it.
		this.p6_selection = this.p6_selection || new Set();
		this.p6b = {
			press: null,
			flash: null,
			seen: null,
			next_free: {},
			keys_on: false,
			search: { q: "", token: 0, timer: null, local: [], remote: [], loading: false, active: 0, shown: [], list_id: "" },
		};
		this.$p6b_toolbar = this.$body.find(".mp-toolbar").first();
		this.p6b_build_search();
		this.$p6b_bar = $('<div class="mp-p6b-bar" role="toolbar"></div>')
			.attr("aria-label", __("Selected visits"))
			.hide()
			.appendTo(this.$body);
		const root = this.$body[0];
		// Capture on the page's own container: a modified click on a visit selects it, ahead of the
		// card's own click (which opens it) and of 6A's quick-look clicks.
		(root.parentElement || root).addEventListener("click", (e) => this.p6b_modifier_click(e), true);
		root.addEventListener("click", (e) => this.p6b_plain_click(e));
		root.addEventListener("contextmenu", (e) => this.p6b_contextmenu(e));
		root.addEventListener("pointerdown", (e) => this.p6b_pointer_down(e), true);
		document.addEventListener("pointermove", (e) => this.p6b_pointer_move(e));
		document.addEventListener("pointerup", (e) => this.p6b_pointer_up(e));
		document.addEventListener("pointercancel", (e) => this.p6b_pointer_cancel(e));
		this.p6b_bind_keys();
	},

	render_phase6b() {
		if (!this.p6b) return;
		if (this.p6b.seen !== this.data) {
			this.p6b.seen = this.data;
			this.p6b.next_free = {};
		}
		this.p6b_prune_selection();
		this.p6b_decorate_selection();
		this.p6b_render_bar();
		this.p6b_flash_pending();
	},

	p6b_kit() {
		return (this.p6a && this.p6a.kit) || null;
	},

	p6b_navigate(fn) {
		const kit = this.p6b_kit();
		return kit ? kit.drawer.navigate(fn) : fn();
	},

	p6b_panel(opts) {
		const kit = this.p6b_kit();
		if (!kit) return new frappe.ui.Dialog({ title: opts.title, fields: opts.fields });
		return kit.panel({
			title: opts.title,
			subtitle: opts.subtitle || "",
			fields: opts.fields,
			width: MP6B.widths.panel,
			key: opts.key || null,
			owner: MP6A.owner,
			push: this.$body[0],
		});
	},

	p6b_resource(user) {
		const person = user && this.tech_by_user[user];
		return (person && person.resource) || null;
	},

	// The hours one person spends on a visit: the lead's effective length, else Planned hours, else 2.
	p6b_visit_hours(card) {
		return Number(card.hours) > 0
			? Number(card.hours)
			: Number(card.planned_hours) > 0
			? Number(card.planned_hours)
			: MP6B.default_visit_hours;
	},

	// ------------------------------------------------------------------ the right-click menu

	p6b_target(el) {
		if (!el || !el.closest || !this.$body[0].contains(el)) return null;
		if (el.closest("button, a, input, select, textarea, .mp-toolbar, .mp-draft-bar, .mp-route, [data-route-resource], .mp-p6b-bar")) {
			return null;
		}
		const card_el = el.closest(".mp-card[data-key]");
		if (card_el) {
			const card = this.by_key[card_el.getAttribute("data-key")];
			if (!card) return null;
			const row = card_el.getAttribute("data-row-user");
			const user = row == null ? card.technician || null : row || null;
			return { kind: "card", card, ymd: card.date || null, user, resource: this.p6b_resource(user), el: card_el };
		}
		const person_el = el.closest(".mp-person[data-user]");
		if (person_el) {
			const user = person_el.getAttribute("data-user");
			return { kind: "person", user, resource: this.p6b_resource(user), el: person_el };
		}
		const cell = el.closest(".mp-cell[data-date]");
		if (cell) {
			const user = cell.getAttribute("data-user") || null;
			return { kind: "cell", user, resource: this.p6b_resource(user), ymd: cell.getAttribute("data-date"), el: cell };
		}
		const day = el.closest(".mp-grid .mp-day[data-date]");
		if (day) return { kind: "cell", user: null, resource: null, ymd: day.getAttribute("data-date"), el: day };
		return null;
	},

	p6b_contextmenu(e) {
		if (this.drag) return;
		if (!this.p6b_open_for(e.target, { x: e.clientX, y: e.clientY }, !!this.p6b.press)) return;
		e.preventDefault();
		if (this.p6b.press) this.p6b.press.opened = true;
	},

	p6b_open_for(el, anchor, from_touch) {
		if (!this.p6b_kit() || !this.data) return false;
		const target = this.p6b_target(el);
		if (!target) return false;
		// The page's own items, then every provider's (6C, 6D-UI): a target with none of ours can still
		// carry theirs, and a provider that throws is skipped.
		const items = this.p6b_menu_items(target);
		const all = MP6B_PURE.with_providers(items, this.p6_menu_providers, target);
		if (!all.length) return false;
		if (from_touch) {
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
		if (target.kind === "card") return this.site_of(target.card);
		if (target.kind === "person") return this.tech_name(target.user);
		return [target.user ? this.tech_name(target.user) : "", target.ymd ? mp_when(target.ymd) : ""].filter(Boolean).join(" · ");
	},

	p6b_menu_items(target) {
		if (target.kind === "card") return this.p6b_card_items(target);
		if (target.kind === "person") {
			return [{ label: __("See their week"), on_click: () => this.p6a_open_person(target.user) }];
		}
		if (target.kind === "cell" && target.ymd) {
			return [{ label: __("Everyone's day"), on_click: () => this.p6a_open_day(target.ymd) }];
		}
		return [];
	},

	// `all`: the page's items with the providers' already appended (p6b_open_for).
	p6b_open_menu(target, all, anchor) {
		const kit = this.p6b_kit();
		const handle = kit.menu({ anchor, items: all, title: this.p6b_menu_title(target), owner: MP6A.owner });
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

	// A visit's items. A project task, rental crew task or travel day shown here is read-only: none of
	// ours (another phase's provider may still add some; with none at all the browser's menu shows).
	p6b_card_items(target) {
		const card = target.card;
		if (card.kind === "booking") return [];
		if (card.kind === "move") return this.fm_card_items(card);
		const visit = card.kind === "visit";
		const can = !!(this.data && this.data.can_move_visits);
		const movable = !!(visit && card.movable && can);
		const why = !visit
			? __("Follows the site's Maintenance Profile")
			: !can
			? __("You cannot move visits")
			: card.started
			? __("Started")
			: card.status !== "draft"
			? __("Finished")
			: __("Read-only");
		const items = [
			{ label: __("Edit"), on_click: () => this.open_card(card) },
			{ label: __("Assign to…"), hint: movable ? "" : why, disabled: !movable, on_click: () => this.p6b_assign(card) },
			this.p6b_next_free_item(card, movable, why),
		];
		if (card.project || card.technician) items.push({ divider: true });
		if (card.project) items.push({ label: __("Site at a glance"), on_click: () => this.p6a_open_site(card) });
		if (card.technician) {
			items.push({
				label: __("{0}'s week", [this.tech_name(card.technician)]),
				on_click: () => this.p6a_open_person(card.technician, card.date),
			});
		}
		return items;
	},

	p6b_next_free(card, resource, hours) {
		const key = [card.key, resource, hours, this.modified[card.name] || card.modified].join("|");
		if (!this.p6b.next_free[key]) {
			const args = { resource, hours, after: card.date || this.today() };
			this.p6b.next_free[key] = Promise.resolve(frappe.call({ method: MP6B.next_free_day, args }))
				.then((r) => (r && r.message) || null)
				.catch(() => null);
		}
		return this.p6b.next_free[key];
	},

	p6b_free_state(answer) {
		if (!answer) return { hint: __("Could not check"), disabled: false };
		if (answer.date) return { hint: mp_when(answer.date), disabled: false };
		return { hint: __("Nothing free in {0} days", [answer.horizon || 30]), disabled: true };
	},

	// The visit's technician's next day with its hours free (planner_conflicts.get_next_free_day), then
	// move_visit through apply(): draft visits nobody has started only.
	p6b_next_free_item(card, movable, why) {
		const item = { label: __("Move to next free day"), hint: __("Looking…"), disabled: false };
		if (!movable) return Object.assign(item, { hint: why, disabled: true });
		if (!card.technician) return Object.assign(item, { hint: __("No technician"), disabled: true });
		const resource = this.p6b_resource(card.technician);
		if (!resource) return Object.assign(item, { hint: __("Not a Planner Resource"), disabled: true });
		const lookup = this.p6b_next_free(card, resource, this.p6b_visit_hours(card));
		item.p6b_lookup = lookup;
		item.on_click = () =>
			lookup.then((answer) => {
				if (answer && answer.date) {
					this.apply(card, { date: answer.date });
					return;
				}
				frappe.show_alert(
					{
						message: (answer && answer.note) || __("No free day found for {0}.", [this.tech_name(card.technician)]),
						indicator: "orange",
					},
					7
				);
			});
		return item;
	},

	// ------------------------------------------------------------------ assign to...

	p6b_assign(card) {
		const kit = this.p6b_kit();
		if (!kit) {
			this.open_card(card);
			return;
		}
		if (!card.date) {
			frappe.msgprint(__("Give the visit a date first: who is free depends on the day."));
			return;
		}
		const state = { mode: card.technician ? "add" : "tech", data: null };
		const handle = kit.drawer.open({
			title: __("Assign to…"),
			subtitle: [this.site_of(card), mp_when(card.date)].join(" · "),
			body: `<p class="pk-empty">${mp_esc(__("Finding who is free…"))}</p>`,
			width: MP6B.widths.assign,
			key: `assign:${card.key}`,
			owner: MP6A.owner,
			push: this.$body[0],
			reopen: () => this.p6b_assign(this.by_key[card.key] || card),
			on_click: (e, h) => this.p6b_assign_click(e, h, card, state),
		});
		Promise.resolve(
			frappe.call({ method: MP6B.who_is_free, args: { start: card.date, hours: this.p6b_visit_hours(card) } })
		)
			.then((r) => {
				if (!handle.is_open()) return;
				state.data = (r && r.message) || null;
				handle.set_body(this.p6b_assign_html(card, state));
			})
			.catch(() => {
				if (handle.is_open()) handle.set_body(this.p6b_assign_html(card, state));
			});
	},

	p6b_assign_html(card, state) {
		const on = new Set(this.visit_people(card));
		const listed = new Set(((this.data && this.data.technicians) || []).filter((person) => person.enabled).map((person) => person.user));
		const parts = [
			`<p class="pk-note">${mp_esc(
				__("Pick who should go: nobody is chosen for you. Someone who is not free can still be booked; you will be asked for a reason.")
			)}</p>`,
		];
		if (card.technician) {
			const option = (value, text) =>
				`<label><input type="radio" name="mp-p6b-mode" value="${mp_esc(value)}"${state.mode === value ? " checked" : ""}> ${mp_esc(text)}</label>`;
			parts.push(
				`<div class="mp-p6b-modes" role="radiogroup" aria-label="${mp_esc(__("How to assign"))}">${option(
					"add",
					__("Add to the crew")
				)}${option("tech", __("Make them the technician, in place of {0}", [this.tech_name(card.technician)]))}</div>`
			);
		} else {
			parts.push(`<p class="pk-note">${mp_esc(__("The visit has no technician: the person you pick fills it in."))}</p>`);
		}
		const data = state.data;
		if (!data) {
			parts.push(`<p class="pk-empty">${mp_esc(__("Who is free could not be loaded. Close this and try again."))}</p>`);
			return parts.join("");
		}
		const entry = (data.days || [])[0] || { free: [], not_free: [] };
		// Technicians only (the people this planner can put on a visit), by user, not already on it.
		const keep = (person) => person.user && listed.has(person.user) && !on.has(person.user);
		const row = (person, busy) =>
			`<button type="button" class="mp-p6b-pick${busy ? " mp-p6b-pick-busy" : ""}" data-mp-p6b-pick="${mp_esc(person.user)}">` +
			`<span><b>${mp_esc(this.tech_name(person.user) || person.label)}</b></span>` +
			`<span class="mp-p6b-pick-why">${mp_esc(busy ? person.reason || "" : __("{0}h free", [mp_hours(person.free_hours)]))}</span></button>`;
		const free = (entry.free || []).filter(keep);
		const busy = (entry.not_free || []).filter(keep);
		parts.push(
			`<div class="pk-section-title">${mp_esc(__("Free on {0} ({1}h or more)", [mp_when(entry.date || card.date), mp_hours(data.hours)]))}</div>`
		);
		parts.push(
			free.length
				? free.map((person) => row(person, false)).join("")
				: `<p class="pk-empty">${mp_esc(__("No technician has those hours free that day."))}</p>`
		);
		if (busy.length) {
			parts.push(`<div class="pk-section-title">${mp_esc(__("Not free"))}</div>`);
			parts.push(busy.map((person) => row(person, true)).join(""));
		}
		if (on.size) {
			parts.push(
				`<div class="pk-section-title">${mp_esc(__("Already on it"))}</div><p class="pk-note">${mp_esc(
					[...on].map((user) => this.tech_name(user)).join(", ")
				)}</p>`
			);
		}
		return parts.join("");
	},

	p6b_assign_click(e, handle, card, state) {
		const radio = e.target && e.target.closest ? e.target.closest("input[name='mp-p6b-mode']") : null;
		if (radio) {
			state.mode = radio.value;
			return;
		}
		const pick = e.target && e.target.closest ? e.target.closest("[data-mp-p6b-pick]") : null;
		if (!pick) return;
		const user = pick.getAttribute("data-mp-p6b-pick");
		const mode = state.mode;
		handle.close();
		const fresh = this.by_key[card.key] || card;
		if (!fresh.movable) return;
		if (mode === "add" && fresh.technician) this.add_to_crew(fresh, user);
		else this.apply(fresh, { technician: user });
	},

	// ------------------------------------------------------------------ long press

	p6b_pointer_down(e) {
		this.p6b.press =
			e.pointerType === "touch" && !(e.button > 0)
				? { id: e.pointerId, x: e.clientX, y: e.clientY, at: Date.now(), target: e.target, moved: false, opened: false }
				: null;
	},

	p6b_pointer_move(e) {
		const press = this.p6b.press;
		if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > MP.drag_px) {
			press.moved = true;
		}
	},

	p6b_pointer_up(e) {
		const press = this.p6b.press;
		this.p6b.press = null;
		if (!press || e.pointerId !== press.id || press.moved || press.opened) return;
		if (Date.now() - press.at < MP6B.long_press_ms) return;
		this.p6b_open_for(press.target, { x: e.clientX, y: e.clientY }, true);
	},

	p6b_pointer_cancel(e) {
		if (this.p6b.press && e.pointerId === this.p6b.press.id) this.p6b.press = null;
	},

	// ------------------------------------------------------------------ selection

	p6b_selectable(card) {
		return !!(card && card.kind === "visit" && card.movable && card.date && this.data && this.data.can_move_visits);
	},

	p6b_selected_cards() {
		return [...this.p6_selection].map((key) => this.by_key[key]).filter((card) => this.p6b_selectable(card));
	},

	p6b_modifier_click(e) {
		if (!(e.shiftKey || e.ctrlKey || e.metaKey) || e.button > 0 || Date.now() < this.click_blocked_until) return;
		const el = e.target && e.target.closest ? e.target.closest(".mp-card[data-key]") : null;
		if (!el || !this.$body[0].contains(el) || e.target.closest("button, a, input, select, textarea")) return;
		e.preventDefault();
		e.stopPropagation();
		const key = el.getAttribute("data-key");
		const card = this.by_key[key];
		if (!card) return;
		if (!this.p6_selection.has(key) && !this.p6b_selectable(card)) {
			frappe.show_alert(
				{
					message: __("Only draft visits nobody has started can be moved together; {0} cannot.", [this.site_of(card)]),
					indicator: "orange",
				},
				6
			);
			return;
		}
		MP6B_PURE.toggle(this.p6_selection, key);
		this.p6b_selection_changed();
	},

	p6b_plain_click(e) {
		if (!this.p6_selection.size || e.shiftKey || e.ctrlKey || e.metaKey) return;
		const el = e.target;
		if (!el || !el.closest) return;
		if (el.closest(".mp-card, .mp-person, .mp-toolbar, .mp-p6b-bar, button, a, input, select, textarea, label")) return;
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
		[...this.p6_selection].forEach((key) => {
			if (!this.p6b_selectable(this.by_key[key])) {
				this.p6_selection.delete(key);
				changed = true;
			}
		});
		if (changed) $(document).trigger("p6-selection-changed", [this]);
	},

	p6b_decorate_selection() {
		this.$body[0].querySelectorAll(".mp-card[data-key]").forEach((el) => {
			el.classList.toggle("mp-p6b-selected", this.p6_selection.has(el.getAttribute("data-key")));
		});
	},

	p6b_render_bar() {
		const $bar = this.$p6b_bar;
		if (!$bar) return;
		const cards = this.p6b_selected_cards();
		if (!cards.length) {
			$bar.hide().empty();
			return;
		}
		$bar.empty().show();
		$('<span class="mp-p6b-bar-count"></span>').text(__("{0} selected", [cards.length])).appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Move…"))
			.on("click", () => this.p6b_move_panel())
			.appendTo($bar);
		$('<button type="button" class="btn btn-default btn-xs"></button>')
			.text(__("Clear"))
			.on("click", () => this.p6b_clear_selection())
			.appendTo($bar);
		$('<span class="mp-p6b-bar-note"></span>').text(this.p6e_bar_note()).appendTo($bar);
	},

	p6b_lift(drag) {
		if (!drag || !drag.ghost || !drag.source || drag.source.kind !== "card") return;
		if (!this.p6_selection.has(drag.source.card.key)) return;
		const count = this.p6b_selected_cards().length;
		if (count < 2) return;
		const badge = document.createElement("span");
		badge.className = "mp-p6b-count";
		badge.textContent = String(count);
		drag.ghost.appendChild(badge);
	},

	// A drop of one selected visit when several are selected. True when it was handled here.
	p6b_drop(source, target) {
		if (!source || source.kind !== "card" || !source.card || this.p6_selection.size < 2) return false;
		if (!this.p6_selection.has(source.card.key) || this.p6b_selected_cards().length < 2) return false;
		const row_user = source.from_user == null ? source.card.technician || "" : source.from_user;
		if (target.row && (target.user || "") !== row_user) {
			frappe.show_alert(
				{
					message: __(
						"Several visits are selected: drop them on the same technician's row to change their days. To hand one visit to someone else, clear the selection first."
					),
					indicator: "orange",
				},
				8
			);
			return true;
		}
		if (!source.card.date || !target.date) return true;
		const days = this.p6e_drop_days(source.card.date, target.date);
		if (days) this.p6b_move_selection(days);
		return true;
	},

	p6b_move_panel() {
		const cards = this.p6b_selected_cards();
		if (!cards.length) return;
		let panel = null;
		panel = this.p6b_panel({
			title: __("Move {0} visits", [cards.length]),
			key: "mp-p6b-move",
			fields: [
				// Move by (calendar or working days) lived on the selection bar until the planner UX
				// redesign (D12); it is a field here now, and a drag still uses the choice it makes.
				{
					fieldtype: "Select",
					fieldname: "unit",
					label: __("Move by"),
					options: this.p6e_unit_options(),
					default: this.p6e_unit_label(this.p6e_unit()),
					description: __("Also what dragging several selected visits counts in. Remembered on this device."),
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

	// One confirmation, then one move_visit per visit through send(): the first conflict asks for a
	// reason and that reason answers the rest; backing out stops the rest. A visit that fails is listed
	// at the end and the others stay moved (each save is its own).
	p6b_move_selection(days) {
		const cards = this.p6b_selected_cards();
		if (!cards.length || !days) return;
		const today = this.today();
		const plans = cards.map((card) => ({ card, date: this.p6e_date(card.date, days) }));
		if (plans.some((plan) => plan.date < today)) {
			frappe.show_alert({ message: __("Visits can only be moved to today or a later day."), indicator: "orange" }, 6);
			return;
		}
		const question = this.p6e_question(plans.length, days);
		frappe.confirm(
			`<p>${mp_esc(question)}</p><p class="text-muted">${mp_esc(
				__("Each visit is saved on its own: if one cannot move, the others still do, and you are told which.")
			)}</p>`,
			() => this.p6b_run_moves(plans)
		);
	},

	p6b_run_moves(plans) {
		// P4: one "Notify crew?" for every visit in the move, before the first is sent.
		return this.mpas_batch(plans, (notify) => this.p6b_send_moves(plans, notify));
	},

	p6b_send_moves(plans, notify) {
		plans.forEach((plan) => (plan.card.saving = true));
		this.render();
		const moved = [];
		const missed = [];
		return this.p6b_one_reason((batch) =>
			plans.reduce(
				(chain, plan) =>
					chain.then(() => {
						if (batch.declined) {
							missed.push(plan);
							return null;
						}
						const before = plan.card.date;
						const args = { record: plan.card.name, modified: this.modified[plan.card.name] || plan.card.modified, date: plan.date };
						if (notify !== undefined) args.notify = notify;
						return this.send("move_visit", args, { noun: "visit" }).then((result) => {
							if (result) moved.push({ record: plan.card.name, site: this.site_of(plan.card), date: before });
							else missed.push(plan);
						});
					}),
				Promise.resolve()
			)
		)
			.then(() => {
				if (moved.length) {
					const message = __("{0} visits moved", [moved.length]);
					const snapshot = { p6b: "many", site: __("{0} visits", [moved.length]), items: moved, notify };
					if (!this.push_undo(snapshot, message)) frappe.show_alert({ message, indicator: "green" }, 5);
				}
				if (missed.length) {
					frappe.msgprint({
						title: __("Moved {0} of {1}", [moved.length, plans.length]),
						message:
							`<p>${mp_esc(__("These visits were not moved; the others were saved:"))}</p>` +
							`<ul>${missed.map((plan) => `<li>${mp_esc(this.site_of(plan.card))}</li>`).join("")}</ul>`,
						indicator: "orange",
					});
				}
			})
			.finally(() => this.load());
	},

	// While `work` runs, the first reason given answers every later conflict of the same batch, and
	// backing out of it stops the rest. Only for the batch: the page's own ask_reason (the prototype's)
	// is back as soon as it ends, and send() itself is untouched.
	p6b_one_reason(work) {
		const base = MaintenancePlanner.prototype.ask_reason;
		const batch = { reason: null, declined: false };
		this.ask_reason = (conflicts, noun) => {
			if (batch.declined) return Promise.resolve(null);
			if (batch.reason) return Promise.resolve(batch.reason);
			return base.call(this, conflicts, noun).then((reason) => {
				if (reason) batch.reason = reason;
				else batch.declined = true;
				return reason;
			});
		};
		return Promise.resolve()
			.then(() => work(batch))
			.finally(() => {
				delete this.ask_reason;
			});
	},

	// ------------------------------------------------------------------ undo

	p6b_undo(snap) {
		if (!snap || snap.p6b !== "many") return false;
		const today = this.today();
		const back = (snap.items || []).filter((item) => item.date && item.date >= today);
		const kept = (snap.items || []).length - back.length;
		back.reduce(
			(chain, item) =>
				chain.then(() =>
					this.send(
						"move_visit",
						{
							record: item.record,
							modified: this.modified[item.record] || (this.by_key[item.record] || {}).modified || "",
							date: item.date,
							// P4: the move's "Notify crew?" answer, again. null, not undefined: an Undo is never asked.
							notify: snap.notify != null ? snap.notify : null,
						},
						{ auto_reason: __(MP.undo_reason), noun: "visit" }
					)
				),
			Promise.resolve()
		)
			.then(() => {
				frappe.show_alert({ message: __("Undone: {0}", [snap.site]), indicator: "green" }, 5);
				if (kept) {
					frappe.show_alert(
						{ message: __("{0} visit(s) were on a day that has passed, so they stay where they are.", [kept]), indicator: "orange" },
						8
					);
				}
			})
			.finally(() => this.load());
		return true;
	},

	// ------------------------------------------------------------------ search

	p6b_build_search() {
		const $wrap = $('<span class="mp-p6b-search-wrap"></span>');
		// First among the filters, so a phone's "Filters and tools" sheet takes it with them (MPTB).
		if (this.$filters && this.$filters.length) $wrap.prependTo(this.$filters);
		else $wrap.appendTo(this.$p6b_toolbar);
		const list_id = `mp-p6b-results-${Math.random().toString(36).slice(2, 9)}`;
		this.p6b.search.list_id = list_id;
		this.$p6b_search = $('<input type="search" class="form-control input-sm mp-p6b-search" autocomplete="off" spellcheck="false">')
			.attr({
				placeholder: __("Search  ( / )"),
				title: __("Find a visit, move, site or technician. Press / to come here."),
				"aria-label": __("Search visits, moves, sites and technicians"),
				role: "combobox",
				"aria-autocomplete": "list",
				"aria-expanded": "false",
				"aria-controls": list_id,
			})
			.appendTo($wrap);
		this.$p6b_results = $('<div class="mp-p6b-results" role="listbox"></div>').attr("id", list_id).hide().appendTo($wrap);
		this.$p6b_search.on("input", () => this.p6b_search_input());
		this.$p6b_search.on("keydown", (e) => this.p6b_search_key(e));
		this.$p6b_search.on("focus", () => {
			if (this.p6b.search.q.length >= MP6B.search_min) this.p6b_render_results();
		});
		this.$p6b_search.on("blur", () => setTimeout(() => this.p6b_close_results(), 150));
		this.$p6b_results.on("mousedown", (e) => e.preventDefault());
		this.$p6b_results.on("click", "[data-mp-p6b-result]", (e) => {
			const item = this.p6b.search.shown[Number(e.currentTarget.getAttribute("data-mp-p6b-result"))];
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
		if (search.q.length < MP6B.search_min) {
			search.local = [];
			search.loading = false;
			this.p6b_close_results();
			return;
		}
		search.local = MP6B_PURE.search_local(search.q, this.p6b_local_entries(), MP6B.search_limit);
		search.loading = true;
		this.p6b_render_results();
		const q = search.q;
		search.timer = setTimeout(() => this.p6b_remote_search(q, token), MP6B.search_wait_ms);
	},

	// What is on screen: every visit (drafted, unscheduled, projected), their sites, the Fountain Move
	// tasks (the cards of MPFM, dated or in the tray) and the technicians.
	p6b_local_entries() {
		const data = this.data || {};
		const out = [];
		const sites = {};
		[].concat(data.visits || [], data.unscheduled || [], data.projected || []).forEach((card) => {
			const site = this.site_of(card);
			out.push({
				kind: "visit",
				key: card.key,
				name: card.name,
				label: site,
				keys: [card.name, card.site_full || "", card.feature || "", card.label || "", ...this.visit_people(card).map((user) => this.tech_name(user))],
				sub: [card.date ? mp_when(card.date) : __("Not scheduled"), card.label ? __(card.label) : ""].filter(Boolean).join(" · "),
				date: card.date || null,
			});
			if (card.project && !sites[card.project]) {
				sites[card.project] = true;
				out.push({ kind: "site", project: card.project, label: site, keys: [card.project, card.site_full || ""], sub: card.project });
			}
		});
		// A move job's task: found by its project (number or name), subject, customer or crew. Its city is
		// only known to the server (the project's address), so that one comes from the remote search.
		this.fm_cards().forEach((card) => {
			out.push({
				kind: "move",
				key: card.key,
				name: card.name,
				label: this.site_of(card),
				keys: [
					card.name,
					card.project || "",
					card.subject || "",
					card.customer || "",
					card.site_full || "",
					...this.visit_people(card).map((user) => this.tech_name(user)),
				],
				sub: [card.date ? mp_when(card.date) : __("Not scheduled"), card.subject || ""].filter(Boolean).join(" · "),
				date: card.date || null,
			});
		});
		(data.technicians || [])
			.filter((person) => person.enabled)
			.forEach((person) => {
				out.push({ kind: "person", user: person.user, label: person.name, keys: [], sub: person.group ? __(person.group) : "" });
			});
		return out;
	},

	p6b_result_key(item) {
		return `${item.kind}|${item.key || item.name || item.project || item.user || ""}`;
	},

	p6b_from_server(item) {
		if (!item || !item.kind) return null;
		if (item.kind === "visit") {
			return {
				kind: "visit",
				key: item.name,
				name: item.name,
				label: item.label || item.name,
				sub: [item.date ? mp_when(item.date) : __("Not scheduled"), item.visit_label ? __(item.visit_label) : "", item.name]
					.filter(Boolean)
					.join(" · "),
				date: item.date || null,
			};
		}
		if (item.kind === "move" && item.name) {
			// The key is the card's (MPFM), so a move the page already lists is not listed twice.
			return {
				kind: "move",
				key: `move|${item.name}`,
				name: item.name,
				label: item.label || item.name,
				sub: [item.date ? mp_when(item.date) : __("Not scheduled"), item.subject || "", item.city || ""].filter(Boolean).join(" · "),
				date: item.date || null,
			};
		}
		if (item.kind === "site") return { kind: "site", project: item.project, label: item.label || item.project, sub: item.project };
		if (item.kind === "person" && item.user) {
			return { kind: "person", user: item.user, label: item.label || item.user, sub: item.group ? __(item.group) : "" };
		}
		return null;
	},

	p6b_remote_search(q, token) {
		return Promise.resolve(
			frappe.call({ method: MP6B.search, args: { q, start: this.anchor, planner: "maintenance" } })
		)
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
		if (search.q.length < MP6B.search_min) {
			this.p6b_close_results();
			return;
		}
		const shown = search.local.concat(search.remote).slice(0, MP6B.search_limit);
		search.shown = shown;
		if (search.active >= shown.length) search.active = 0;
		const kinds = { visit: __("Visit"), move: __("Move"), site: __("Site"), person: __("Technician") };
		const rows = shown.map(
			(item, index) =>
				`<div class="mp-p6b-result${index === search.active ? " mp-p6b-active" : ""}" role="option" id="${mp_esc(
					`${search.list_id}-${index}`
				)}" aria-selected="${index === search.active ? "true" : "false"}" data-mp-p6b-result="${index}">` +
				`<span class="mp-p6b-result-kind">${mp_esc(kinds[item.kind] || item.kind)}</span>` +
				`<span class="mp-p6b-result-label">${mp_esc(item.label)}</span>` +
				(item.sub ? `<span class="mp-p6b-result-sub">${mp_esc(item.sub)}</span>` : "") +
				`</div>`
		);
		const note = search.loading ? __("Searching the rest…") : shown.length ? "" : __("Nothing found.");
		this.$p6b_results.html(rows.join("") + (note ? `<div class="mp-p6b-results-note">${mp_esc(note)}</div>` : "")).show();
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
		if (item.kind === "person") this.p6a_open_person(item.user);
		else if (item.kind === "site") this.p6a_open_site({ project: item.project, site: item.label, site_full: item.label });
		else if (item.kind === "visit") this.p6b_jump_to_visit(item);
		else if (item.kind === "move") this.p6b_jump_to_move(item);
	},

	// A visit: the calendar goes to its week (a real route) and the card flashes.
	p6b_jump_to_visit(item) {
		const card = this.by_key[item.key];
		this.p6b_jump_to_card(item, item.date || (card && card.date) || null, () =>
			frappe.set_route("Form", "Sapphire Maintenance Record", item.name)
		);
	},

	// A Fountain Move task (MPFM): the same jump. A dated one goes to the day its card is drawn on (a task
	// that began before these weeks sits on the first day shown, so it is not left for its real first day);
	// one in the Unscheduled tray is scrolled to and flashed there. With no card to find, the task opens.
	p6b_jump_to_move(item) {
		const card = this.by_key[item.key];
		this.p6b_jump_to_card(item, (card && card.date) || item.date || null, () =>
			this.open_card({ kind: "move", name: item.name })
		);
	},

	// `date` is the day to go to (null: it is in the tray); `open_record` opens it when the page has no card.
	p6b_jump_to_card(item, date, open_record) {
		const card = this.by_key[item.key];
		const move = item.kind === "move";
		if (!date) {
			if (card) {
				// The tray is always on screen when it has cards. A move says so when the filters hide it.
				this.p6b.flash = { key: card.key, date: null, wait: move, move };
				this.p6b_flash_pending();
				return;
			}
			this.p6b_navigate(open_record);
			return;
		}
		this.p6b.flash = { key: item.key, date, wait: true, move };
		if (this.range_days().includes(date)) {
			this.p6b_flash_pending();
			return;
		}
		this.go(this.view === "week" || this.view === "crew" ? this.view : "week", date);
	},

	p6b_flash_pending() {
		const flash = this.p6b.flash;
		if (!flash || !this.data) return;
		if (flash.date && !(this.data.start <= flash.date && flash.date <= this.data.end)) {
			if (!flash.wait) this.p6b.flash = null;
			return;
		}
		this.p6b.flash = null;
		const el = Array.from(this.$body[0].querySelectorAll(".mp-card[data-key]")).find(
			(node) => node.getAttribute("data-key") === flash.key
		);
		if (!el) {
			if (flash.wait) {
				const message = flash.move ? __("That move is hidden by the filters on screen.") : __("That visit is hidden by the filters on screen.");
				frappe.show_alert({ message, indicator: "orange" }, 6);
			}
			return;
		}
		el.classList.add("mp-p6b-flash");
		try {
			el.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
		} catch (e) {
			el.scrollIntoView();
		}
		setTimeout(() => el.classList.remove("mp-p6b-flash"), MP6B.flash_ms);
	},

	// ------------------------------------------------------------------ keyboard

	// One keydown listener on the document (capture), added while the page shows and removed when it
	// hides; see the Project Planner's p6b_bind_keys for why not frappe.ui.keys.
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
		if (route[0] !== MP.route) return false;
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
		const action = MP6B_PURE.shortcut({
			key: e.key,
			ctrl: e.ctrlKey,
			meta: e.metaKey,
			shift: e.shiftKey,
			alt: e.altKey,
			editable,
			// P12: the first-visit tour is a dialog, and its keys are its own.
			dialog: !!(window.cur_dialog && window.cur_dialog.display) || this.tour_open(),
		});
		if (!action) return;
		if (target && target.closest && target.closest(".pk-menu")) return;
		if (action === "escape") {
			this.p6b_escape();
			return;
		}
		if (!this.p6b_run_key(action)) return;
		e.preventDefault();
		e.stopPropagation();
	},

	p6b_run_key(action) {
		if (this.view === MPS.view) return false; // Simple mode has no calendar to step or switch
		if (action === "today") {
			this.go(this.view, frappe.datetime.get_today());
			return true;
		}
		if (action === "prev" || action === "next") {
			this.shift(action === "prev" ? -1 : 1);
			return true;
		}
		if (action.startsWith("view")) {
			// The Maintenance Planner's own order: month, week, crew.
			const view = MP.views[Number(action.slice(4)) - 1];
			if (!view) return false;
			this.go(view, this.anchor);
			return true;
		}
		if (action === "undo") {
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
		const kit = this.p6b_kit();
		if (kit && kit.guard && typeof kit.guard.top === "function" && kit.guard.top()) return;
		this.p6b_clear_selection();
	},

	p6b_legend_sections() {
		const key = (text) => `<kbd class="mp-p6b-kbd">${mp_esc(text)}</kbd>`;
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		const own = [
			{
				title: __("Keyboard"),
				note: __("Not while typing in a box or with a dialog open."),
				items: [
					item(key("T"), "Go to today."),
					item(key("←") + key("→"), "The previous or next month or week."),
					item(key("1") + key("2") + key("3"), "Month, week or crew view."),
					item(key("Ctrl") + key("Z"), "Undo the last change (⌘Z on a Mac)."),
					item(key("/"), "Search visits, moves, sites and technicians."),
					item(key("?"), "This help."),
					item(key("Esc"), "Close a menu or side panel, or clear the selection."),
				],
			},
			{
				title: __("Faster scheduling"),
				items: [
					item("", "Right-click a visit, an empty spot or a name (on a touch screen, hold it still for a moment) for a menu: assign someone, move to the technician's next free day, the site at a glance."),
					item(
						`<span class="mp-p6a-sample mp-p6b-selected">${mp_esc(__("Visit"))}</span>`,
						"Shift-click (or Ctrl- or ⌘-click) draft visits to select several. Drag one and they all move by the same number of calendar days, or of weekdays if you switch the bar to working days."
					),
					item("", "Assign to… lists the technicians who are free that day. Nobody is picked for you."),
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

Object.assign(MaintenancePlanner.prototype, MP6B_METHODS);

// ====================================================================== Phase 6C: big picture, tablet mode and print
//
// The Maintenance Planner's half of Phase 6C (TASK-2026-02469; the Project Planner has the same, see
// PP6C_METHODS there for the long version):
//
//   - Color by (toolbar's More ▸ Display)      Visit type (today's colors, the default), Technician,
//                                            Site, Visit status or Contract; the key in Display and
//                                            in the "?" legend. Site and contract use the kit's
//                                            stable hash, so a site is always the same color
//   - "Highlight this site" in a visit's panel, the site drawer's header or the right-click menu, or
//     a site in the color key                every visit of that site lights up and the rest fade;
//                                            a toolbar pill and Esc clear it
//   - Views (toolbar's More ▸ Views)           named views and "last used", saved per person on the
//                                            server (api/planner_views_prefs). Only a bare
//                                            /maintenance-planner takes the saved view, by replacing
//                                            its history entry
//   - the team strip                         over the week and crew views: the technicians' free hours
//                                            per day (one technician when the filter picks one),
//                                            amber at 90%, red past capacity; click a day for
//                                            everyone's day
//   - Tap to move                            tap a visit, then the day or a technician's day: the
//                                            drag's own drop -> plan_drop -> apply -> send path
//   - Print (More ▸ Print)                     the view on screen or 6B's selection, from the loaded
//                                            data, on the print design system's chrome (the Service
//                                            pillar), in a browser window
//
// Hooks, one line each: init_phase6c (constructor), p6c_before_route (handle_route), render_phase6c
// (render), p6c_links and p6c_action (open_card), p6c_ready (p6a_ready), p6c_site_drawer
// (p6a_open_site) and p6c_legend_sections (p6a_legend).

const MP6C = {
	prefs: "erpnext_enhancements.api.planner_views_prefs",
	planner: "maintenance",
	default_view: "month",
	tap_pref: "ee_maintenance_planner_tap_move",
	save_delay: 1500,
	restore_wait: 4000,
	key_limit: 12,
	legend_limit: 40,
	// What a saved view may hold: the same rules as api/planner_views_prefs.SCHEMA["maintenance"].
	schema: {
		view: ["enum", ["month", "week", "crew"]],
		technician: ["text"],
		projected: ["bool"],
		project_work: ["bool"],
		// P10: Project work as cards (off: the busy strip), and Resources available open (it starts collapsed).
		// "resources" replaces the old "panel": what that key stored, from when the panel started open, is
		// not a choice anyone made, so it is not read.
		project_cards: ["bool"],
		resources: ["bool"],
		color_by: ["enum", ["default", "technician", "site", "status", "contract"]],
	},
	color_modes: [
		["default", "Visit type"],
		["technician", "Technician"],
		["site", "Site"],
		["status", "Visit status"],
		["contract", "Contract"],
	],
	view_labels: { month: "Month", week: "Week", crew: "Crew" },
	// Color by's "nobody" grey: data, the same kind of value as a technician's color (the Project
	// Planner keeps the same one). A booking's printed color is read from the tokens (p6c_kind_color).
	none_color: "#94a3b8",
};

const MP6C_METHODS = {
	init_phase6c() {
		this.p6c = {
			color_by: "default",
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
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.p6c_menu_items(target));

		this.$p6c_pill = $('<span class="mp-p6c-pill" role="status"></span>').hide().insertAfter(this.$title);
		$('<span class="mp-p6c-pill-text"></span>').appendTo(this.$p6c_pill);
		$('<button type="button" class="mp-p6c-pill-x">×</button>')
			.attr("title", __("Stop highlighting (Esc)"))
			.attr("aria-label", __("Stop highlighting"))
			.on("click", () => this.p6c_clear_highlight())
			.appendTo(this.$p6c_pill);
		// Color by and its key are in the toolbar's More ▸ Display, Tap to move in Tools, the saved views in
		// Views and Print in Print (MPTB, v1.687.0); this block gives each menu its items
		// (p6c_color_items, p6c_view_items, p6c_print_items).
		this.$p6c_movebar = $('<div class="mp-p6c-movebar" role="status"></div>').hide().appendTo(this.$body);

		document.addEventListener("click", (e) => this.p6c_tap_click(e), true);
		window.addEventListener("keydown", (e) => this.p6c_keydown(e), true);
		// A site in the color key highlights from More ▸ Display now (p6c_color_items); the key's own
		// click listener went with the key's row (v1.687.0).
		this.$body.on("change click", () => this.p6c_state_soon());
		this.p6c_sync_controls();
		this.p6c_fetch_views();
	},

	p6c_ready() {
		if (this.p6c && this.data) this.render_phase6c();
	},

	render_phase6c() {
		if (!this.p6c || !this.data) return;
		this.p6c_sync_controls();
		this.p6c_paint();
		this.p6c_apply_highlight();
		this.p6c_render_strip();
		this.p6c_mark_moving();
		this.p6c_state_soon();
	},

	p6c_kit() {
		const kit = this.p6a && this.p6a.kit;
		return kit && kit.big_picture ? kit : null;
	},

	p6c_pick(state) {
		const kit = this.p6c_kit();
		if (kit) return kit.big_picture.pick_view(state, MP6C.schema);
		const out = {};
		Object.keys(MP6C.schema).forEach((key) => {
			if (state && Object.prototype.hasOwnProperty.call(state, key)) out[key] = state[key];
		});
		return out;
	},

	p6c_here() {
		return (frappe.get_route() || [])[0] === MP.route && this.$body.is(":visible");
	},

	// ------------------------------------------------------------------ controls

	p6c_sync_controls() {
		const p6c = this.p6c;
		if (!p6c || !this.$body) return;
		if (this.$tech) this.$tech.val(this.technician || "");
		if (this.$p6c_pill) {
			this.$p6c_pill.toggle(!!p6c.highlight);
			if (p6c.highlight) {
				const text = __("Highlighting {0}", [p6c.highlight.label || p6c.highlight.key]);
				this.$p6c_pill.find(".mp-p6c-pill-text").text(text).attr("title", text);
			}
		}
	},

	// ------------------------------------------------------------------ color by

	p6c_set_color(mode) {
		const known = MP6C.color_modes.map(([value]) => value);
		this.p6c.color_by = known.includes(mode) ? mode : "default";
		this.render();
	},

	p6c_mode_label(mode) {
		const found = MP6C.color_modes.find(([value]) => value === mode);
		return __(found ? found[1] : "Visit type");
	},

	p6c_site_key(card) {
		return (card && (card.project || card.customer || card.site)) || "";
	},

	// The color of a card in the current mode; null keeps the card's own (and a read-only project
	// booking keeps its pillar's edge unless the mode is Technician). `row`: the crew-view row.
	p6c_card_color(card, row) {
		const kit = this.p6c_kit();
		const mode = this.p6c.color_by;
		if (!kit || mode === "default" || !card) return null;
		const bp = kit.big_picture;
		if (mode === "technician") {
			const user = row || card.technician;
			return user ? this.tech_color(user) : MP6C.none_color;
		}
		if (card.kind === "booking") return null;
		if (mode === "site") {
			const key = this.p6c_site_key(card);
			return key ? bp.hash_color(key) : MP6C.none_color;
		}
		if (mode === "status") return bp.palette_color(bp.VISIT_STATUSES, bp.visit_status(card));
		if (mode === "contract") return card.contract ? bp.hash_color(card.contract) : MP6C.none_color;
		return null;
	},

	// A card's edge color as on screen (maintenance_planner.css), for the printed page in the default
	// mode. Paper is light whatever the Desk theme is, so these are the palette tokens behind the
	// screen's edges, read as values (the print window cannot read a CSS variable): a visit the
	// Service pillar's fresh-blue, a seasonal or pending one yellow, an extra one violet, a done one
	// green, an overdue one red, a projected one the muted grey; a booking its pillar's opening stop.
	p6c_kind_color(card) {
		if (card.kind === "booking") {
			if (card.booking_kind === "rental") return mp_token_color("--sf-teal", "#62cbc9");
			if (card.booking_kind === "travel") return mp_token_color("--sf-deep-sea-blue", "#00263e");
			return mp_token_color("--sf-bahama-blue", "#00609c");
		}
		if (card.kind === "visit" && card.overdue) return mp_token_color("--sf-red", "#bd2e2b");
		const labelled = card.label
			? MP.extra_labels.includes(card.label)
				? mp_token_color("--sf-violet", "#b14fc5")
				: mp_token_color("--sf-yellow", "#ffb819")
			: "";
		const visit = mp_token_color("--sf-fresh-blue", "#00a0df");
		if (card.kind === "projected") return labelled || (card.movable ? visit : mp_token_color("--sf-ink-500", "#5c6066"));
		if (card.status === "pending") return mp_token_color("--sf-yellow", "#ffb819");
		if (card.status === "done") return mp_token_color("--sf-green", "#1e7e51");
		return labelled || visit;
	},

	p6c_paint() {
		const kit = this.p6c_kit();
		if (!kit || this.p6c.color_by === "default") return;
		this.$body[0].querySelectorAll(".mp-card[data-key]").forEach((el) => {
			const card = this.by_key[el.getAttribute("data-key")];
			const color = kit.big_picture.safe_paint(this.p6c_card_color(card, el.getAttribute("data-row-user")), "");
			if (!color) return;
			el.style.setProperty("--mp-p6c-c", color);
			el.classList.add("mp-p6c-colored");
		});
	},

	// The visits the filters let through (no read-only bookings).
	p6c_visible_cards() {
		if (!this.data) return [];
		return this.all_cards().filter((card) => card.kind !== "booking" && this.visible(card));
	},

	p6c_legend_entries() {
		const kit = this.p6c_kit();
		const mode = this.p6c.color_by;
		if (!kit || mode === "default") return [];
		const bp = kit.big_picture;
		if (mode === "status") return bp.palette_legend(bp.VISIT_STATUSES);
		if (mode === "technician") {
			return ((this.data && this.data.technicians) || [])
				.filter((person) => person.enabled)
				.map((person) => ({ key: person.user, label: person.name, color: this.tech_color(person.user) }));
		}
		const seen = {};
		let none = false;
		this.p6c_visible_cards().forEach((card) => {
			if (mode === "site") {
				const key = this.p6c_site_key(card);
				if (!key) none = true;
				else if (!seen[key]) {
					seen[key] = {
						key,
						label: card.site_full || card.site || key,
						color: bp.hash_color(key),
						hi: card.project || "",
					};
				}
				return;
			}
			if (!card.contract) none = true;
			else if (!seen[card.contract]) {
				seen[card.contract] = {
					key: card.contract,
					label: card.site ? `${card.contract} · ${card.site}` : card.contract,
					color: bp.hash_color(card.contract),
				};
			}
		});
		const out = Object.values(seen).sort((a, b) => String(a.label).localeCompare(String(b.label)));
		if (none) {
			out.push({ key: "", label: mode === "site" ? __("No site") : __("No contract"), color: MP6C.none_color });
		}
		return out;
	},

	p6c_swatch_html(color) {
		const kit = this.p6c_kit();
		const paint = kit ? kit.big_picture.safe_paint(color, MP6C.none_color) : MP6C.none_color;
		return `<i class="mp-p6c-swatch" style="--mp-color: ${mp_esc(paint)}"></i>`;
	},

	// Display's Color by: one radio item a mode, then the key of what the colors mean on screen (it
	// used to be a row of its own under the toolbar). A site in the key highlights its visits.
	p6c_color_items() {
		if (!this.p6c) return [];
		const items = [{ heading: __("Color by") }];
		MP6C.color_modes.forEach(([value, label]) => {
			items.push({
				label: __("Color: {0}", [__(label)]),
				radio: true,
				checked: this.p6c.color_by === value,
				on_click: () => this.p6c_set_color(value),
			});
		});
		const entries = this.p6c_legend_entries();
		if (!entries.length) return items;
		const shown = entries.slice(0, MP6C.key_limit);
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

	p6c_legend_sections() {
		if (!this.p6c) return [];
		const mode = this.p6c.color_by;
		const entries = this.p6c_legend_entries().slice(0, MP6C.legend_limit);
		const strip = `<span class="mp-p6c-sday mp-amber mp-p6a-sample"><span class="mp-p6c-stext">${mp_esc(__("6h free"))}</span></span>`;
		const pill = `<span class="mp-p6c-pill"><span class="mp-p6c-pill-text">${mp_esc(__("Highlighting"))}</span></span>`;
		const sections = [];
		if (mode !== "default") {
			sections.push({
				title: __("Card colors: {0}", [this.p6c_mode_label(mode)]),
				note: mode === "technician" ? "" : __("Project, rental and travel bookings keep their own colors."),
				items: entries.map((entry) => ({ sample_html: this.p6c_swatch_html(entry.color), text: entry.label })),
			});
		}
		sections.push({
			title: __("The big picture"),
			note:
				mode === "default"
					? __("More ▸ Display ▸ Color by colors the visits by technician, site, visit status or contract instead of the visit type below.")
					: "",
			items: [
				{
					sample_html: pill,
					text: __("Highlight this site (in a visit's panel, the site drawer or the color key) lights up all of a site's visits and fades the rest. Esc or × clears it."),
				},
				{
					sample_html: strip,
					text: __("The team row over the week and crew views: the technicians' free hours per day. Amber at 90% booked, red when they are booked past their hours. Click a day to see everyone's day."),
				},
				{
					sample_html: "",
					text: __("Tap to move: tap a visit, then tap the day (or a technician's day in the crew view) where it goes. On by default on a touch screen; it asks for a reason and can be undone like a drag."),
				},
				{
					sample_html: "",
					text: __("Views: save your filters, view and colors under a name and switch between them. The planner opens the way you last left it, on any device."),
				},
				{ sample_html: "", text: __("Print: exactly what is on screen, with your filters and colors, or only the visits you selected.") },
			],
		});
		return sections;
	},

	// ------------------------------------------------------------------ highlight a site

	p6c_site_label(project) {
		const card = this.all_cards().find((item) => item.kind !== "booking" && item.project === project);
		return (card && (card.site_full || card.site)) || project;
	},

	p6c_highlight(project) {
		if (!project) return;
		this.p6c.highlight = { key: project, label: this.p6c_site_label(project) };
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

	p6c_is_lit(card) {
		const hi = this.p6c.highlight;
		return !!(hi && card && card.kind !== "booking" && card.project === hi.key);
	},

	p6c_apply_highlight() {
		const root = this.$body[0];
		root.classList.toggle("mp-p6c-on", !!this.p6c.highlight);
		root.querySelectorAll(".mp-card[data-key]").forEach((el) => {
			el.classList.toggle("mp-p6c-hi", this.p6c_is_lit(this.by_key[el.getAttribute("data-key")]));
		});
	},

	p6c_links(card) {
		if (!this.p6c || !card || card.kind === "booking" || !card.project) return [];
		const on = this.p6c.highlight && this.p6c.highlight.key === card.project;
		return [["p6c_highlight", on ? __("Stop highlighting this site") : __("Highlight this site")]];
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
		if (card.kind !== "booking" && card.project) {
			const on = this.p6c.highlight && this.p6c.highlight.key === card.project;
			items.push({
				label: on ? __("Stop highlighting this site") : __("Highlight this site"),
				on_click: () => this.p6c_toggle_highlight(card.project),
			});
		}
		const source = target.el ? this.drag_source(target.el) : null;
		if (source && source.kind === "card") {
			items.push({ label: __("Move with taps…"), hint: __("then tap a day"), on_click: () => this.p6c_arm(source) });
		}
		return items;
	},

	// The site drawer (6A) gets a Highlight switch in its header.
	p6c_site_drawer(card, handle) {
		if (!this.p6c || !card || !card.project || !handle || typeof handle.set_tools !== "function") return;
		const button = document.createElement("button");
		button.type = "button";
		button.className = "btn btn-default btn-xs mp-p6c-drawer-hi";
		button.title = __("Light up this site's visits on the calendar and fade the rest");
		button.addEventListener("click", () => this.p6c_toggle_highlight(card.project));
		handle.set_tools(button);
		this.p6c.drawer_tool = { project: card.project, handle, button };
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

	// The technicians the strip sums: everyone with hours on the planner, or the one the filter picks
	// (the calendar views; the crew view always shows every row).
	p6c_team() {
		const people = ((this.data && this.data.technicians) || []).filter((person) => person.enabled && person.resource);
		const one = this.view !== "crew" && this.technician && this.technician !== "__none__";
		return one ? people.filter((person) => person.user === this.technician) : people;
	},

	p6c_team_label() {
		const one = this.view !== "crew" && this.technician && this.technician !== "__none__";
		return one ? this.tech_name(this.technician) : __("Technicians");
	},

	p6c_team_sum(ymd) {
		const kit = this.p6c_kit();
		if (!kit) return null;
		return kit.big_picture.team_day(this.p6c_team().map((person) => this.cell_of(person.user, ymd)));
	},

	p6c_strip_cells() {
		const kit = this.p6c_kit();
		if (!kit) return [];
		const bp = kit.big_picture;
		const label = this.p6c_team_label();
		return this.range_days().map((ymd) => {
			const sum = this.p6c_team_sum(ymd);
			const weekday = (kit.format.day_label(ymd) || "").split(",")[0];
			const tone = { off: "mp-offday", red: "mp-red", amber: "mp-amber", green: "mp-green" }[sum.level] || "";
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

	p6c_bar_html(sum) {
		const width = Math.round(Math.min(1, sum.ratio) * 100);
		const soft = Math.round(Math.min(1, sum.soft_ratio || 0) * 100);
		return `<div class="mp-bar"><i style="width:${width}%"></i>${
			soft > 0 ? `<b style="left:${width}%;width:${soft}%"></b>` : ""
		}</div>`;
	},

	p6c_strip_cell_html(cell) {
		return `
			<div class="mp-p6c-sday ${cell.tone}" role="button" tabindex="0" data-pp6a-day="${mp_esc(cell.ymd)}"
				title="${mp_esc(cell.tip)}" aria-label="${mp_esc(cell.tip.split("\n")[0])}">
				<span class="mp-p6c-slabel">${mp_esc(cell.weekday)}</span>
				<span class="mp-p6c-stext">${mp_esc(cell.text)}</span>${cell.sum.capacity > 0 ? this.p6c_bar_html(cell.sum) : ""}
			</div>`;
	},

	p6c_render_strip() {
		this.$grid_wrap.find(".mp-p6c-strip, .mp-crew > .mp-p6c-sday, .mp-crew > .mp-p6c-scorner").remove();
		if (this.view !== "week" && this.view !== "crew") return;
		if (!this.p6c_team().length) return;
		const cells = this.p6c_strip_cells();
		if (!cells.length) return;
		const html = cells.map((cell) => this.p6c_strip_cell_html(cell)).join("");
		if (this.view === "week") {
			const $strip = $('<div class="mp-p6c-strip" role="group"></div>').attr("aria-label", __("Team capacity"));
			$strip[0].innerHTML = html;
			this.$grid_wrap.prepend($strip);
			return;
		}
		const heads = this.$grid_wrap[0].querySelectorAll(".mp-crew > .mp-crew-head");
		if (!heads.length) return;
		const corner = `<div class="mp-p6c-scorner"><b>${mp_esc(this.p6c_team_label())}</b><span>${mp_esc(
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

	p6c_tap_default() {
		const saved = this.load_pref(MP6C.tap_pref, "");
		if (saved === "1") return true;
		if (saved === "0") return false;
		return this.p6c_coarse();
	},

	p6c_set_tap(on) {
		this.p6c.tap = !!on;
		this.save_pref(MP6C.tap_pref, on ? "1" : "0");
		if (!on) this.p6c_disarm();
		this.p6c_sync_controls();
		frappe.show_alert(
			{
				message: on
					? __("Tap to move: tap a visit, then tap the day where it goes.")
					: __("Tap to move is off: tap a visit to open it, drag it to move it."),
				indicator: "blue",
			},
			5
		);
	},

	// Where a tap puts the visit: a technician's day in the crew view, or a day of the calendar.
	p6c_tap_target(el) {
		const cell = el.closest(".mp-cell[data-date]");
		if (cell) return { el: cell, date: cell.getAttribute("data-date"), user: cell.getAttribute("data-user") || "", row: true };
		const day = el.closest(".mp-grid .mp-day[data-date]");
		if (day) return { el: day, date: day.getAttribute("data-date") };
		return null;
	},

	p6c_tap_click(e) {
		const p6c = this.p6c;
		if (!p6c || !this.data || (!p6c.tap && !p6c.moving)) return;
		const target = e.target;
		if (!target || !target.closest || !this.$body[0].contains(target)) return;
		if (e.button > 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
		if (Date.now() < this.click_blocked_until) return;
		if (
			target.closest(
				".mp-p6c-movebar, button, a, input, select, textarea, label, [data-route-resource], [data-pk-person], [data-pp6a-day]"
			)
		) {
			return;
		}
		const take = () => {
			e.preventDefault();
			e.stopPropagation();
		};
		const moving = p6c.moving;
		if (!moving) {
			const source = this.drag_source(target);
			if (!source || source.kind !== "card") return;
			take();
			this.p6c_arm(source);
			return;
		}
		const card_el = target.closest(".mp-card[data-key]");
		if (card_el && this.p6c_is_moving_el(card_el)) {
			take();
			this.p6c_disarm();
			return;
		}
		const place = this.p6c_tap_target(target);
		if (!place) {
			// Another visit outside the calendar (the Unscheduled tray): pick that one instead.
			const other = p6c.tap ? this.drag_source(target) : null;
			if (other && other.kind === "card") {
				take();
				this.p6c_arm(other);
			}
			return;
		}
		take();
		this.p6c_tap_drop(place);
	},

	// The move goes through the drag's own path: drop -> plan_drop -> apply -> send (reason, Undo).
	p6c_tap_drop(place) {
		const moving = this.p6c.moving;
		if (!moving) return;
		this.p6c_disarm();
		this.drop(moving.source, place);
	},

	p6c_arm(source) {
		if (!source || !source.card) return;
		const card = source.card;
		this.p6c.moving = {
			source,
			label: card.site || card.site_full || card.name,
			key: card.key,
			row_user: source.from_user === undefined ? null : source.from_user,
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
		return el.getAttribute("data-key") === moving.key && el.getAttribute("data-row-user") === moving.row_user;
	},

	p6c_mark_moving() {
		const root = this.$body[0];
		root.classList.toggle("mp-p6c-armed", !!(this.p6c && this.p6c.moving));
		root.querySelectorAll(".mp-p6c-moving").forEach((el) => el.classList.remove("mp-p6c-moving"));
		if (!this.p6c || !this.p6c.moving) return;
		root.querySelectorAll(".mp-card[data-key]").forEach((el) => {
			if (this.p6c_is_moving_el(el)) el.classList.add("mp-p6c-moving");
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
		const $text = $('<span class="mp-p6c-movebar-text"></span>').appendTo($movebar);
		$("<b></b>").text(__("Moving: {0}", [moving.label])).appendTo($text);
		$('<span class="mp-p6c-movebar-hint"></span>')
			.text(
				this.view === "crew"
					? __("Tap a day on a technician's row to put it there.")
					: __("Tap a day to put it there.")
			)
			.appendTo($text);
		$('<button type="button" class="btn btn-default btn-sm"></button>')
			.text(__("Open"))
			.on("click", () => {
				const card = this.by_key[moving.key] || moving.source.card;
				this.p6c_disarm();
				this.open_card(card);
			})
			.appendTo($movebar);
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
			frappe.call({ method: `${MP6C.prefs}.get_views`, args: { planner: MP6C.planner } })
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				p6c.views = Array.isArray(answer.views) ? answer.views : [];
				p6c.last = answer.last && typeof answer.last === "object" ? answer.last : null;
				this.tour_take(answer.tour); // P12: whether this person has been shown each tour
			})
			.catch(() => null);
		const late = new Promise((resolve) => setTimeout(resolve, MP6C.restore_wait));
		p6c.loading = Promise.race([call, late]).then(() => {
			p6c.loaded = true;
		});
		return p6c.loading;
	},

	// First thing in handle_route; see the Project Planner's twin. The URL always wins: only a bare
	// /maintenance-planner takes the saved view, and it replaces that history entry.
	p6c_before_route() {
		const p6c = this.p6c;
		if (!p6c) return false;
		const route = frappe.get_route() || [];
		if (route[0] !== MP.route) return false;
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
		if (!p6c.restored) {
			p6c.restored = true;
			if (last) {
				this.p6c_apply_state(last, { render: false });
				p6c.saved_text = JSON.stringify(last);
			}
		}
		if (!route[1] && this.mps_landing()) return true; // Simple mode, when this device used it last
		const view = last && last.view;
		if (!route[1] && view && view !== MP6C.default_view && MP6C.schema.view[1].includes(view)) {
			frappe.route_flags = frappe.route_flags || {};
			frappe.route_flags.replace_route = true;
			frappe.set_route(MP.route, view, frappe.datetime.get_today());
			return true;
		}
		return false;
	},

	p6c_state() {
		const state = {
			technician: this.technician || "",
			projected: !!this.show_projected,
			project_work: !!this.show_project_work,
			project_cards: !!this.show_project_cards,
			resources: !!this.panel_open,
			color_by: this.p6c.color_by,
		};
		if (MP6C.schema.view[1].includes(this.view)) state.view = this.view;
		return this.p6c_pick(state);
	},

	p6c_apply_state(state, opts) {
		opts = opts || {};
		const view = this.p6c_pick(state);
		if ("technician" in view) this.technician = view.technician;
		if ("projected" in view) this.show_projected = !!view.projected;
		if ("project_work" in view) this.show_project_work = !!view.project_work;
		if ("project_cards" in view) this.show_project_cards = !!view.project_cards;
		if ("resources" in view) this.panel_open = !!view.resources;
		if ("color_by" in view) this.p6c.color_by = view.color_by;
		if (this.data && this.technician && this.technician !== "__none__") {
			const known = ((this.data && this.data.technicians) || []).some((person) => person.user === this.technician);
			if (!known) this.technician = "";
		}
		// A view of their own (restored or picked) is the person's filter, not an address's (S5c).
		this.url_filters = null;
		this.own_filters = this.filters();
		this.p6c_sync_controls();
		if (opts.route && view.view && view.view !== this.view) {
			this.go(view.view, this.anchor);
			return;
		}
		if (opts.render === false) return;
		this.write_filter_url();
		if (this.data) this.render();
	},

	p6c_state_soon() {
		const p6c = this.p6c;
		if (!p6c || !p6c.restored) return;
		clearTimeout(p6c.save_timer);
		p6c.save_timer = setTimeout(() => this.p6c_save_last(), MP6C.save_delay);
	},

	p6c_save_last() {
		const p6c = this.p6c;
		if (!MP6C.schema.view[1].includes(this.view)) return;
		const state = this.p6c_state();
		// A technician the address gave (S5c) is that visit's, not "last used": it keeps the person's
		// own until they pick one themselves (pick_filter).
		Object.keys(this.url_filters || {}).forEach((key) => {
			if (key in state) state[key] = this.own_filter_values()[key];
		});
		const text = JSON.stringify(state);
		if (text === p6c.saved_text) return;
		p6c.saved_text = text;
		Promise.resolve(
			frappe.call({ method: `${MP6C.prefs}.save_last_view`, args: { planner: MP6C.planner, data: text } })
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				p6c.last = answer.last && typeof answer.last === "object" ? answer.last : state;
			})
			.catch(() => {
				p6c.saved_text = null;
			});
	},

	// The saved view that says what is on screen. A view saved before P10 has no say about project work as
	// cards or Resources available being open (the newer keys), so those two are compared only when the
	// view holds them; everything else must be equal, as it always was.
	p6c_matching_view() {
		const now = this.p6c_state();
		return (
			(this.p6c.views || []).find((entry) => {
				const saved = this.p6c_pick(entry.data || {});
				const keys = Object.keys(now).filter((key) => key in saved || !MP10.newer_view_keys.includes(key));
				return JSON.stringify(keys.map((key) => now[key])) === JSON.stringify(keys.map((key) => saved[key]));
			}) || null
		);
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
		frappe.show_alert({ message: __("View: {0}", [mp_esc(entry.name)]), indicator: "green" }, 4);
	},

	p6c_view_summary(view) {
		const parts = [];
		view = this.p6c_pick(view || {});
		if (view.view) parts.push(__(MP6C.view_labels[view.view] || view.view));
		if (view.technician === "__none__") parts.push(__("Unassigned"));
		else if (view.technician) parts.push(this.tech_name(view.technician));
		if (view.projected === false) parts.push(__("No projected visits"));
		if (view.project_work === false) parts.push(__("No project work"));
		else if (view.project_cards === true) parts.push(__("Project work as cards"));
		if (view.color_by && view.color_by !== "default") parts.push(__("{0} colors", [this.p6c_mode_label(view.color_by)]));
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
					description: __("For example: Austin, this week. Only you see your views."),
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
				if (exists) frappe.confirm(__("Replace your saved view {0}?", [mp_esc(name)]), run);
				else run();
			},
			__("Save view as"),
			__("Save")
		);
	},

	p6c_store_view(name) {
		return Promise.resolve(
			frappe.call({
				method: `${MP6C.prefs}.save_view`,
				args: { planner: MP6C.planner, name, data: JSON.stringify(this.p6c_state()) },
			})
		)
			.then((r) => {
				const answer = (r && r.message) || {};
				if (Array.isArray(answer.views)) this.p6c.views = answer.views;
				frappe.show_alert({ message: __("Saved view {0}", [mp_esc(answer.saved || name)]), indicator: "green" }, 5);
			})
			.catch(() => null);
	},

	p6c_views_html() {
		const views = this.p6c.views || [];
		if (!views.length) return `<p class="pk-empty">${mp_esc(__("No saved views yet. Save one from More ▸ Views."))}</p>`;
		const rows = views.map(
			(entry) => `
			<li class="mp-p6c-views-row">
				<div class="mp-p6c-views-main">
					<div class="mp-p6c-views-name">${mp_esc(entry.name)}</div>
					<div class="mp-p6c-views-sub">${mp_esc(this.p6c_view_summary(entry.data || {}))}</div>
				</div>
				<button type="button" class="btn btn-default btn-xs" data-p6c-use="${mp_esc(entry.name)}">${mp_esc(__("Use"))}</button>
				<button type="button" class="btn btn-default btn-xs" data-p6c-delete="${mp_esc(entry.name)}">${mp_esc(__("Delete"))}</button>
			</li>`
		);
		return `<ul class="mp-p6c-views-list">${rows.join("")}</ul><p class="pk-note">${mp_esc(
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
			owner: MP6A.owner,
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
				frappe.confirm(__("Delete your saved view {0}? This cannot be undone.", [mp_esc(entry.name)]), () =>
					Promise.resolve(
						frappe.call({ method: `${MP6C.prefs}.delete_view`, args: { planner: MP6C.planner, name: entry.name } })
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

	// 6B's multi-selection (visit keys), read defensively: empty when 6B is not there.
	p6c_selection() {
		const selection = this.p6_selection;
		return selection && typeof selection.has === "function" && selection.size ? selection : new Set();
	},

	// The More ▸ Print items: the view on screen, and 6B's selection when there is one. Each runs in
	// the menu item's click, so its window opens on the person's own tap.
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

	p6c_print(selection_only) {
		const kit = this.p6c_kit();
		if (!kit || !this.data) {
			frappe.show_alert({ message: __("One moment: the planner is still loading."), indicator: "blue" }, 4);
			return;
		}
		const win = window.open("", "_blank");
		if (!win) {
			frappe.msgprint(__("Allow pop-ups for this site to print the planner."));
			return;
		}
		win.document.write(
			`<!doctype html><title>${mp_esc(__("Print"))}</title><p style="font-family:sans-serif">${mp_esc(
				__("Preparing the page…")
			)}</p>`
		);
		const model = this.p6c_print_model(!!selection_only);
		Promise.resolve(
			frappe.call({
				method: `${MP6C.prefs}.get_print_chrome`,
				args: { planner: MP6C.planner, title: model.heading, lines: JSON.stringify(model.lines) },
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

	p6c_print_item(card, row) {
		const booking = card.kind === "booking";
		const people = booking ? [card.technician].filter(Boolean) : this.visit_people(card);
		const names = people.map((user) => this.tech_name(user)).join(", ");
		const marks = [];
		let title;
		let sub;
		if (booking) {
			title = card.label || card.ref;
			const length = card.slot ? card.slot.join("–") : `${this.fmt_hours(card.hours)}h`;
			sub = [card.project, length, row ? "" : names].filter(Boolean).join(" · ");
			marks.push(__(MP.booking_kinds[card.booking_kind] || "Project"));
		} else {
			title = card.site || card.site_full || card.name;
			const what =
				card.kind === "move"
					? card.subject
					: card.label
					? __(card.label)
					: card.feature || (card.kind === "projected" ? __(card.frequency || "") : __("Regular visit"));
			sub = [what, row ? "" : names || __("Unassigned")].filter(Boolean).join(" · ");
			if (card.kind === "move") marks.push(__("Move"));
			if (card.kind === "projected") marks.push(card.movable ? __("Next visit") : __("Projected"));
			if (card.status === "pending") marks.push(__("Pending review"));
			if (card.status === "done") marks.push(__("Done"));
			if (card.overdue) marks.push({ text: __("Overdue"), tone: "red" });
			if (card.full_day) marks.push(__("Full day"));
			if (card.on_hold) marks.push({ text: __("On hold"), tone: "red" });
			if (card.flagged) marks.push({ text: __("Chemistry"), tone: "red" });
		}
		const lit = this.p6c_is_lit(card);
		return {
			title,
			sub,
			color: this.p6c_card_color(card, row) || this.p6c_kind_color(card),
			dashed: card.kind === "projected",
			marks,
			faded: !!this.p6c.highlight && !lit,
			highlight: lit,
		};
	},

	p6c_print_lines(selection_only, count) {
		const lines = [];
		if (this.technician === "__none__") lines.push(__("Technician: Unassigned"));
		else if (this.technician && this.view !== "crew") lines.push(__("Technician: {0}", [this.tech_name(this.technician)]));
		if (!this.show_projected) lines.push(__("Projected visits hidden"));
		if (!this.show_project_work) lines.push(__("Project work hidden"));
		if (this.p6c.color_by !== "default") lines.push(__("Colored by {0}", [this.p6c_mode_label(this.p6c.color_by)]));
		if (this.p6c.highlight) lines.push(__("Highlighting {0}", [this.p6c.highlight.label]));
		if (selection_only) lines.push(__("Selected visits only ({0})", [count]));
		return lines;
	},

	// Exactly what is on screen, as a model for planner_kit.big_picture.print_document.
	p6c_print_model(selection_only) {
		const heading = `${__(MP6C.view_labels[this.view] || this.view)} · ${this.title()}`;
		const model = {
			doc_title: `${__("Maintenance Planner")} · ${heading}`,
			heading,
			legend_title: __("Colors: {0}", [this.p6c_mode_label(this.p6c.color_by)]),
			legend: this.p6c_legend_entries().map((entry) => ({ color: entry.color, label: entry.label })),
			notes: [],
		};
		const selected = this.p6c_selection();
		if (selection_only && selected.size) {
			const cards = this.all_cards().filter((card) => card.kind !== "booking" && selected.has(card.key));
			cards.sort((a, b) => String(a.date || "9999").localeCompare(String(b.date || "9999")) || this.compare(a, b));
			model.layout = "list";
			model.columns = [__("Date"), __("Site"), __("Visit"), __("People"), __("Notes")];
			model.rows = cards.map((card) => {
				const item = this.p6c_print_item(card, null);
				return {
					color: item.color,
					cols: [
						card.date ? this.p6c_day_label(card.date) : __("Not scheduled"),
						card.site_full || card.site || card.name,
						card.label ? __(card.label) : card.feature || __("Regular visit"),
						this.visit_people(card).map((user) => this.tech_name(user)).join(", ") || __("Unassigned"),
						item.marks.map((mark) => (typeof mark === "string" ? mark : mark.text)).join(", "),
					],
				};
			});
			model.lines = [__("Selected visits")].concat(this.p6c_print_lines(true, cards.length));
			model.empty = __("None of the selected visits is on screen.");
			return model;
		}
		model.lines = this.p6c_print_lines(false, 0);
		if (this.view === "crew") return Object.assign(model, this.p6c_print_crew());
		return Object.assign(model, this.p6c_print_calendar());
	},

	p6c_print_calendar() {
		const days = this.range_days();
		const kit = this.p6c_kit();
		const month = this.view === "month" ? String(this.anchor).slice(0, 7) : "";
		const by_day = {};
		this.all_cards().forEach((card) => {
			if (!card.date || !this.visible(card)) return;
			(by_day[card.date] = by_day[card.date] || []).push(card);
		});
		const team = this.p6c_team().length > 0;
		const rows = [];
		for (let index = 0; index < days.length; index += 7) {
			rows.push({
				cells: days.slice(index, index + 7).map((ymd) => {
					const sum = team ? this.p6c_team_sum(ymd) : null;
					return {
						head: this.view === "week" ? this.p6c_day_label(ymd) : this.p6c_day_label(ymd, true),
						sub: sum && kit ? kit.big_picture.team_text(sum) : "",
						tone: month && ymd.slice(0, 7) !== month ? "out" : "",
						items: (by_day[ymd] || [])
							.sort((a, b) => this.compare(a, b))
							.map((card) => this.p6c_print_item(card, null))
							.concat(this.p10_print_items(ymd, null)),
					};
				}),
			});
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
		const by_cell = {};
		this.all_cards().forEach((card) => {
			if (!card.date) return;
			if (card.kind === "booking" && this.project_work_mode() !== "cards") return;
			if (card.kind === "projected" && !this.show_projected) return;
			const owners = card.kind === "booking" ? [card.technician || ""] : this.visit_people(card);
			(owners.length ? owners : [""]).forEach((user) => {
				const key = `${user}|${card.date}`;
				(by_cell[key] = by_cell[key] || []).push(card);
			});
		});
		const people = ((this.data && this.data.technicians) || []).filter(
			(person) =>
				person.enabled ||
				days.some((ymd) => (by_cell[`${person.user}|${ymd}`] || []).length || this.p10_has_strip(person.user, ymd))
		);
		const entries = people.map((person) => ({ person, user: person.user }));
		if (days.some((ymd) => (by_cell[`|${ymd}`] || []).length)) entries.push({ person: null, user: "" });
		const rows = entries.map(({ person, user }) => ({
			label: person ? person.name : __("Unassigned"),
			sub: person && person.group ? __(person.group) : "",
			cells: days.map((ymd) => {
				const cell = person ? this.cell_of(user, ymd) : null;
				const state = this.avail_state(cell);
				return {
					sub: state.text,
					tone: this.is_off(cell) ? "off" : "",
					items: (by_cell[`${user}|${ymd}`] || [])
						.sort((a, b) => this.compare(a, b))
						.map((card) => this.p6c_print_item(card, user || null))
						.concat(person ? this.p10_print_items(ymd, user) : []),
				};
			}),
		}));
		const kit = this.p6c_kit();
		if (kit && rows.length && this.p6c_team().length) {
			rows.unshift({
				label: this.p6c_team_label(),
				sub: __("free hours"),
				cells: days.map((ymd) => ({ head: kit.big_picture.team_text(this.p6c_team_sum(ymd)) })),
			});
		}
		return {
			layout: "grid",
			label_column: true,
			columns: [__("Technician")].concat(days.map((ymd) => this.p6c_day_label(ymd))),
			rows,
			empty: __("No technicians to show."),
		};
	},
};

Object.assign(MaintenancePlanner.prototype, MP6C_METHODS);

// ====================================================================== Phase 6D: Conflict center, personal blocks, day notes
//
// The Maintenance Planner's half of Phase 6D's page work (the Project Planner has the same; see its
// own block). The backend is api/planner_blocks.py and api/planner_conflicts.py; the drawer, the two
// forms and the markup are the planner kit's (planner_kit/conflicts.js and blocks.js).
//
//   - "Conflicts (N)" on the toolbar        the Conflict center for the dates on screen, with
//                                            planner="maintenance": only visits (and the contract
//                                            behind the next projected visit) have fixes here; project
//                                            tasks show with "Open in Project Planner". A visit in a
//                                            conflict gets a Conflict marker (its "Day off" chip when
//                                            it has one) that opens the list there.
//   - a fix                                 move_visit / move_projected exactly as the server worked
//                                            them out, through send() with the same Undo snapshot a
//                                            drag takes; "Pick someone who's free" hands the visit to
//                                            the person tapped (by user) and picks nobody itself
//   - personal blocks                       a hatched chip in each technician's day (calendar and crew
//                                            view, from each day cell's `blocks`); its note only when
//                                            block_notes has it; never dragged. "Block time" for the
//                                            planners, "Block time…" in a person's drawer and the menu
//   - day notes                             under each day's head, under the crew view's column heads,
//                                            and at the top of the day drawer
//
// Hooks into the class above, one line each: init_phase6d (constructor), render_phase6d (render),
// off_label ("Unavailable" for an all-day block), p6d_day_head (render_calendar),
// p6d_crew_notes_row and p6d_cell_blocks (render_crew), and in Phase 6A's block p6d_person_actions
// (p6a_open_person), p6d_day_peek_opts (p6a_open_day) and p6d_legend_sections (p6a_legend).

const MP6D = {
	planner: "maintenance",
	width: 540,
	max_days: 60,
	visit: "Sapphire Maintenance Record",
	// The Conflict center runs only these, each the page's own write sent through send().
	fix_methods: {
		[`${MP.api}.move_visit`]: "move_visit",
		[`${MP.api}.move_projected`]: "move_projected",
	},
	targets: "[data-pk-block], [data-pk-note-open], [data-p6d-conflict]",
};

const MP6D_METHODS = {
	init_phase6d() {
		this.p6d = { center: null, counted: null, notes: {}, asked: {}, notes_for: null };
		// The right-click menu registry (phase6-wave2): 6B opens the menu, this adds its items.
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.p6d_menu_items(target));
		// The Conflicts count is an alert chip on the toolbar and Block time an item of its More ▸ Tools
		// (MPTB, v1.687.0): p6d_conflict_count and data.can_schedule say when.
		const root = this.$body[0];
		// Capture phase: a block, a note or a conflict marker inside a card or a day is its own click.
		root.addEventListener("click", (e) => this.p6d_click(e), true);
		root.addEventListener(
			"keydown",
			(e) => {
				if (e.key !== "Enter" && e.key !== " ") return;
				const el = e.target && e.target.closest ? e.target.closest(MP6D.targets) : null;
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
			planner: MP6D.planner,
			owner: MP6A.owner,
			push: this.$body[0],
			width: MP6D.width,
			range: () => this.p6d_range(),
			can_schedule: () => !!(this.data && this.data.can_schedule),
			draft: () => false,
			block_note: (name) => this.p6d_block_note(name),
			run_fix: (job) => this.p6d_run_fix(job),
			open_item: (conflict, item) => this.p6d_open_item(conflict, item),
			exclude: (conflict, fix) => this.p6d_exclude(conflict, fix),
			pick_context: (conflict, fix) => this.p6d_pick_context(conflict, fix),
			on_list: () => this.p6d_after_list(),
		});
		if (this.data) this.render();
	},

	p6d_kit() {
		return this.p6d && this.p6d.center ? window.planner_kit : null;
	},

	// ------------------------------------------------------------------ hooks

	render_phase6d() {
		if (!this.p6d || !this.data) return;
		if (this.p6d.notes_for !== this.data) {
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

	// Under a day's head in the month and week views: its notes, then the blocked technicians shown.
	p6d_day_head($day, ymd) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return;
		const month = this.view === "month";
		const can = !!this.data.can_schedule;
		const parts = [kit.blocks.notes_html(this.p6d_notes_of(ymd), { date: ymd, compact: month, can_add: can && !month })];
		const single = !!this.technician && this.technician !== "__none__";
		const people =
			this.technician === "__none__"
				? []
				: (this.data.technicians || []).filter((person) => (single ? person.user === this.technician : person.enabled));
		people.forEach((person) => {
			this.p6d_blocks_of(person.user, ymd).forEach((block) => {
				const who = single ? "" : month ? this.p6d_initials(person.name) : person.name;
				parts.push(this.p6d_chip(block, person, ymd, { who, compact: month }));
			});
		});
		const html = parts.join("");
		if (html) $day.append(html);
	},

	// The crew view: one technician's blocks in their day's cell, under the hours.
	p6d_cell_blocks(person, ymd) {
		if (!person || !this.p6d_kit()) return "";
		return this.p6d_blocks_of(person.user, ymd)
			.map((block) => this.p6d_chip(block, person, ymd, {}))
			.join("");
	},

	p6d_chip(block, person, ymd, extra) {
		const kit = this.p6d_kit();
		return kit.blocks.chip_html(
			block,
			Object.assign(
				{
					note: extra.compact ? "" : this.p6d_block_note(block.ref),
					date: ymd,
					user: person.user,
					resource: person.resource || "",
					person: person.user,
					// C4: a block read in from the Google vacations calendar changes in Google only.
					editable: this.p6d_can_edit_block(person.user) && !block.from_google,
				},
				extra
			)
		);
	},

	p6d_crew_notes_row(days) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return "";
		const can = !!this.data.can_schedule;
		if (!can && !days.some((ymd) => this.p6d_notes_of(ymd).length)) return "";
		const cells = days.map(
			(ymd) =>
				`<div class="mp-p6d-crew-notes">${kit.blocks.notes_html(this.p6d_notes_of(ymd), { date: ymd, can_add: can })}</div>`
		);
		return `<div class="mp-p6d-crew-notes mp-p6d-crew-corner">${mp_esc(__("Notes"))}</div>${cells.join("")}`;
	},

	// ------------------------------------------------------------------ what the page knows

	p6d_notes_of(ymd) {
		return ((this.data && this.data.day_notes) || {})[ymd] || [];
	},

	// A technician's blocks on a day: the day cell's `blocks` (maintenance_planner._booking_cell).
	p6d_blocks_of(user, ymd) {
		const cell = user ? this.cell_of(user, ymd) : null;
		return ((cell && cell.blocks) || []).filter((block) => block && block.ref);
	},

	// A block's note: ONLY from the payload's block_notes (filled for the viewer by the server).
	p6d_block_note(name) {
		const notes = (this.data && this.data.block_notes) || {};
		return name && notes[name] ? String(notes[name]) : "";
	},

	p6d_initials(name) {
		const parts = String(name || "?").split(/\s+/).filter(Boolean);
		return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
	},

	p6d_can_edit_block(user) {
		if (this.data && this.data.can_schedule) return true;
		return !!user && user === (frappe.session && frappe.session.user);
	},

	p6d_can_block(user) {
		if (this.data && this.data.can_schedule) return true;
		return !user || user === (frappe.session && frappe.session.user);
	},

	p6d_default_date() {
		const today = this.today();
		const { start, end } = this.range();
		return today >= mp_ymd(start) && today <= mp_ymd(end) ? today : mp_ymd(start);
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
		const user = el.getAttribute("data-pk-block-user");
		if (!kit || !name) return;
		const block = this.p6d_blocks_of(user, ymd).find((entry) => entry.ref === name);
		if (!block) return;
		if (this.p6d_can_edit_block(user) && !block.from_google) {
			this.p6d_block_form({
				block: { name, date: ymd, all_day: !!block.all_day, slot: block.slot || null },
				user,
				date: ymd,
			});
			return;
		}
		const where = block.from_google ? ` (${__("from the Google vacations calendar: change it there")})` : "";
		kit.toast(`${this.tech_name(user)}: ${kit.blocks.block_text(block)}${where}`, { tone: "info" });
	},

	// ------------------------------------------------------------------ blocks and notes

	// The block form: a planner picks anyone on the planner; a technician blocks only their own time
	// (the form then sends no person, and the server makes it the caller's own).
	p6d_block_form(opts) {
		const kit = this.p6d_kit();
		if (!kit || !this.data) return;
		opts = opts || {};
		const can = !!this.data.can_schedule;
		const me = frappe.session && frappe.session.user;
		const user = opts.user || (can ? "" : me);
		if (!can && user && user !== me) {
			frappe.show_alert({ message: __("You can only block your own time."), indicator: "orange" }, 6);
			return;
		}
		const person = (this.tech_by_user || {})[user] || null;
		const resource = (person && person.resource) || "";
		if (can && user && !resource) {
			frappe.msgprint(
				__("{0} is not on the planner's list of people (Planner Resources), so their time cannot be blocked here.", [
					this.tech_name(user),
				])
			);
			return;
		}
		const people = (this.data.technicians || [])
			.filter((entry) => entry.enabled && entry.resource)
			.map((entry) => ({ value: entry.resource, label: entry.name }));
		kit.blocks.form({
			block: opts.block || null,
			note: opts.block ? this.p6d_block_note(opts.block.name) : "",
			date: opts.date || this.p6d_default_date(),
			resource,
			resource_label: user ? this.tech_name(user) : "",
			people: can ? people : null,
			self_only: !can,
			on_saved: () => this.load(),
			on_deleted: () => this.load(),
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

	p6d_person_actions(user) {
		if (!this.data || !this.p6d_can_block(user)) return [];
		return [{ label: __("Block time…"), on_click: (info) => this.p6d_block_form({ user, date: info && info.date }) }];
	},

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
		return kit.blocks.note_list_html(notes || [], { loading: notes === null });
	},

	p6d_peek_notes(ymd) {
		const { start, end } = this.range();
		if (ymd >= mp_ymd(start) && ymd <= mp_ymd(end)) return this.p6d_notes_of(ymd);
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
		const edit = target && target.closest ? target.closest("[data-pk-note-edit]") : null;
		if (!edit) return false;
		const name = edit.getAttribute("data-pk-note-edit");
		const note = (this.p6d_peek_notes(ymd) || []).find((entry) => entry.name === name);
		if (note) this.p6d_note_form({ note, date: ymd });
		return true;
	},

	// ------------------------------------------------------------------ the Conflict center

	p6d_open_conflicts(focus) {
		const center = this.p6d && this.p6d.center;
		if (center) center.open(focus || null);
	},

	p6d_range() {
		if (!this.data || !MP.views.includes(this.view)) return null;
		const { start, end } = this.range();
		const last = moment.min(end.clone(), start.clone().add(MP6D.max_days - 1, "days"));
		return { start: mp_ymd(start), end: mp_ymd(last), label: `${mp_when(mp_ymd(start))} – ${mp_when(mp_ymd(last))}` };
	},

	p6d_after_list() {
		this.tb_render();
		this.p6d_decorate();
	},

	// What nobody has kept in the dates on screen, or null before the first count (the toolbar's chip).
	p6d_conflict_count() {
		const center = this.p6d && this.p6d.center;
		const count = center ? center.count() : null;
		return count == null ? null : Number(count) || 0;
	},

	p6d_projected_card(args) {
		return (
			Object.values(this.by_key || {}).find(
				(card) => card && card.kind === "projected" && card.movable && card.contract === args.contract && card.from_date === args.from_date
			) || null
		);
	},

	// A fix from the Conflict center: exactly the method and arguments the server worked out, sent
	// through send() with the Undo snapshot save_move takes for a drag, so the reason prompt and Undo
	// apply. Nothing but this page's own writes (move_visit, move_projected) may run from here.
	p6d_run_fix(job) {
		const method = MP6D.fix_methods[job.fix.method];
		if (!method || !job.args) {
			frappe.show_alert({ message: __("That fix cannot run from this planner."), indicator: "orange" }, 6);
			return Promise.resolve(null);
		}
		const visit = method === "move_visit";
		const card = visit ? this.by_key[job.args.record] || null : this.p6d_projected_card(job.args);
		const item = (job.conflict.items || []).find((entry) => entry.name === job.fix.name) || {};
		const site = card ? this.site_of(card) : item.title || job.fix.name;
		const snapshot = card ? this.p6d_snapshot(card, job.args, visit, site) : null;
		if (card) {
			card.saving = true;
			this.render();
		}
		return this.send(method, job.args, { snapshot, message: this.p6d_fix_message(job, site), noun: visit ? "visit" : "contract" }).finally(
			() => this.load()
		);
	},

	// What Undo needs, in the shape save_move() records for the same move.
	p6d_snapshot(card, args, visit, site) {
		const before = this.snapshot_of(card);
		if (visit) {
			return {
				kind: "visit",
				site,
				record: card.name,
				date: before.date,
				technician: before.technician,
				crew: before.crew,
				planned_hours: before.planned_hours,
				full_day: before.full_day,
			};
		}
		return {
			kind: "projected",
			site,
			contract: card.contract,
			serial_no: card.serial_no || null,
			from_date: args.to_date,
			to_date: before.date,
		};
	},

	p6d_fix_message(job, site) {
		const args = job.args;
		if (job.fix.type === "next_free_day") return __("{0} moved to {1}", [site, mp_when(args.date || args.to_date)]);
		if (job.fix.type === "pick_free") {
			const to = (job.picked && job.picked.label) || this.tech_name(args.technician);
			return __("{0}: {1} handed over to {2}", [site, this.tech_name(args.from_user), to]);
		}
		return __("{0} updated", [site]);
	},

	// A record in a conflict: a visit of this planner opens in its side panel; a project task opens on
	// the Project Planner; a trip or a rental crew task opens its form.
	p6d_open_item(conflict, item) {
		const kit = this.p6d_kit();
		const go = (fn) => (kit ? kit.drawer.navigate(fn) : fn());
		if (item.own) {
			const card = (item.key && this.by_key[item.key]) || this.by_key[item.name];
			if (card && card.kind !== "booking") this.open_card(card);
			else go(() => frappe.set_route("Form", item.doctype, item.name));
			return;
		}
		if (item.planner === "project") go(() => frappe.set_route(MP.project_route, "week", conflict.date));
		else if (item.planner === "travel") go(() => frappe.set_route("Form", "Travel Trip", item.name));
		else if (item.doctype === "Task") go(() => frappe.set_route("Form", "Task", item.name));
	},

	// Who cannot take a visit: whoever has it now and everyone already on it.
	p6d_exclude(conflict, fix) {
		const out = {};
		const card = this.by_key[fix.name];
		if (card && card.kind === "visit") this.visit_people(card).forEach((user) => (out[user] = __("Already on it")));
		const from = (fix.args && fix.args.from_user) || fix.user;
		if (from) out[from] = __("Booked on it now");
		return out;
	},

	p6d_pick_context(conflict, fix) {
		const card = this.by_key[fix.name];
		const item = (conflict.items || []).find((entry) => entry.name === fix.name) || {};
		const site = card ? this.site_of(card) : item.title || fix.name;
		const from = (fix.args && fix.args.from_user) || fix.user;
		return from ? __("Hand {0} over from {1}", [site, this.tech_name(from)]) : site;
	},

	// A visit in a conflict nobody kept: its "Day off" chip (for a day off or a block) becomes the way
	// into the Conflict center, else one small Conflict marker. Never two marks for one conflict.
	p6d_decorate() {
		const center = this.p6d && this.p6d.center;
		if (!center || !this.data) return;
		const root = this.$body[0];
		root.querySelectorAll("[data-p6d-conflict]").forEach((node) => this.p6d_undecorate(node));
		const index = center.index();
		const day_off = __("Day off");
		root.querySelectorAll(".mp-card[data-key]").forEach((el) => {
			const card = this.by_key[el.getAttribute("data-key")];
			if (!card || card.kind === "booking") return;
			const found = card.kind === "projected" ? index[`key|${card.key}`] : index[`${MP6D.visit}|${card.name}`];
			if (!found) return;
			const row = el.getAttribute("data-row-user");
			const hit = row ? found.find((entry) => entry.user === row) : found[0];
			const holder = el.querySelector(".mp-card-who");
			if (!hit || !holder) return;
			let chip = null;
			if (hit.kind === "day_off" || hit.kind === "blocked") {
				chip = Array.from(holder.querySelectorAll(".mp-chip.mp-red")).find((node) => node.textContent.indexOf(day_off) === 0);
			}
			if (!chip) {
				chip = document.createElement("span");
				chip.className = "mp-chip mp-red mp-p6d-mark";
				chip.textContent = __("Conflict");
				holder.appendChild(chip);
			}
			chip.classList.add("mp-p6d-link");
			chip.setAttribute("data-p6d-conflict", hit.key);
			chip.setAttribute("role", "button");
			chip.setAttribute("tabindex", "0");
			chip.setAttribute("title", __("Open the Conflict center at this conflict"));
		});
	},

	p6d_undecorate(node) {
		if (node.classList.contains("mp-p6d-mark")) {
			node.remove();
			return;
		}
		node.classList.remove("mp-p6d-link");
		["data-p6d-conflict", "role", "tabindex", "title"].forEach((name) => node.removeAttribute(name));
	},

	// ------------------------------------------------------------------ the right-click menu

	p6d_menu_items(target) {
		if (!this.p6d || !this.data || !target) return [];
		const out = [];
		const center = this.p6d.center;
		const card = target.kind === "card" ? target.card : null;
		if (card && center && card.kind !== "booking") {
			const hits = card.kind === "projected" ? center.find("", "", card.key) : center.find(MP6D.visit, card.name);
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
		if ((target.kind === "cell" || target.kind === "person") && this.p6d_can_block(target.user)) {
			out.push({
				label: __("Block time…"),
				on_click: () => this.p6d_block_form({ user: target.user || "", date: target.ymd || this.p6d_default_date() }),
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
						"A personal block: the technician is unavailable then. The planners and the person see its note; everyone else sees “Unavailable”."
					),
					item(kit.blocks.chip_html({ name: "", all_day: 1 }, {}), "Unavailable all day: the day counts like time off."),
					item(
						kit.blocks.notes_html([{ note: __("Shop meeting 7 am") }], {}),
						"A note for the crew on that day. Hover over it or tap it to read it all."
					),
					item(
						`<span class="mp-chip mp-red mp-p6d-link">${mp_esc(__("Conflict"))}</span>`,
						"Click it to open the Conflict center at that conflict."
					),
					item(
						"",
						"The Conflicts chip on the toolbar (Conflicts… in Edit) lists everything wrong in the dates on screen, with one-click fixes for visits. A fix that causes a conflict still asks for a reason, like a drag."
					),
				],
			},
		];
	},
};

Object.assign(MaintenancePlanner.prototype, MP6D_METHODS);

// ====================================================================== Phase 6E: working days, and Add visit here
//
// Two follow-ups to Phase 6B (Nik, 2026-10-09; TASK-2026-02471), for this page:
//
//   - "Move by: Calendar days | Working days" in the Move… panel, remembered per device. A visit is
//     one day, so with working days it moves by that many weekdays (a Friday plus one is the Monday,
//     and a drop on a weekend lands on the Monday after it). Calendar days stay the default.
//   - "Add visit here...": a draft visit for a site, a day and a technician, made from an empty spot
//     (the right-click menu, through this.p6_menu_providers, or a double-click) or the toolbar's
//     "Add visit". The form pre-fills from the site (get_visit_defaults: default technician, crew,
//     length and Full day from its Maintenance Profile; the active contract's fountain and checklist
//     template) and every field is editable. create_visit makes the draft exactly as the nightly
//     scheduler would and links it to the site's active contract when there is one. Nobody is picked
//     for the planner beyond what the site already says.
//
// The write goes through the page's own send(): the server asks for a reason when the visit overbooks
// someone (needs_reason), and the Undo toast deletes the new draft again (remove_created_visit), which
// the server allows only while nothing has been attached to it. MP.methods is where send() looks a
// method up, so init_phase6e adds the two endpoints there.
//
// Hooks into the class above, one line each: init_phase6e (constructor), render_phase6e (render) and
// p6e_undo (undo). Phase 6B's block calls p6e_bar_note, p6e_drop_days, p6e_date,
// p6e_question and p6e_move_label / p6e_move_default / p6e_move_note for the working-days switch.

const MP6E = {
	api: "erpnext_enhancements.api.maintenance_actions",
	pref: "ee_maintenance_planner_move_by",
	units: ["calendar", "working"],
	widths: { panel: 460 },
};
MP6E.methods = {
	create_visit: `${MP6E.api}.create_visit`,
	remove_created_visit: `${MP6E.api}.remove_created_visit`,
};
MP6E.defaults = `${MP6E.api}.get_visit_defaults`;
MP6E.sites_query = `${MP6E.api}.sites_query`;

// Pure helpers: no frappe, no moment, no page. The weekday maths is the Project Planner's PP6E_PURE
// (identical copies, run through the same cases by tests/test_planner_phase6e.py) and the server's
// api/project_planner.add_working_days / working_days_between.
const MP6E_PURE = {
	is_weekend(ymd) {
		const weekday = new Date(`${ymd}T00:00:00Z`).getUTCDay();
		return weekday === 0 || weekday === 6;
	},
	// A Saturday or Sunday becomes the Monday after; any other day stays.
	next_weekday(ymd) {
		let day = ymd;
		for (let i = 0; i < 3 && MP6E_PURE.is_weekend(day); i++) day = MP6B_PURE.ymd_add(day, 1);
		return day;
	},
	// A Saturday or Sunday becomes the Friday before; any other day stays.
	previous_weekday(ymd) {
		let day = ymd;
		for (let i = 0; i < 3 && MP6E_PURE.is_weekend(day); i++) day = MP6B_PURE.ymd_add(day, -1);
		return day;
	},
	// Where a drop on `to` lands when it is a weekend: the Friday before when the move is backwards
	// (`to` is earlier than `from`), the Monday after when it is forwards. Nik, 2026-10-09.
	snap_weekday(from, to) {
		return to < from ? MP6E_PURE.previous_weekday(to) : MP6E_PURE.next_weekday(to);
	},
	// `count` working days later (negative: earlier). Each step lands on the next weekday, so a Friday
	// plus one is the Monday and a Saturday plus one is the Monday too.
	add_working_days(ymd, count) {
		const step = count < 0 ? -1 : 1;
		let day = ymd;
		for (let i = 0, n = Math.min(Math.abs(Math.trunc(Number(count) || 0)), 4000); i < n; i++) {
			day = MP6B_PURE.ymd_add(day, step);
			while (MP6E_PURE.is_weekend(day)) day = MP6B_PURE.ymd_add(day, step);
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
		for (let day = MP6B_PURE.ymd_add(low, 1), i = 0; day <= high && i < 800; day = MP6B_PURE.ymd_add(day, 1), i++) {
			if (!MP6E_PURE.is_weekend(day)) count++;
		}
		return sign * count;
	},
	// How many days a drop moved the visit in hand: by the calendar, or in weekdays to the weekday the
	// drop lands on (a drop on a weekend is the Monday after it going forwards, the Friday before it
	// going backwards).
	drop_days(from, to, unit) {
		if (unit !== "working") return MP6B_PURE.ymd_diff(from, to);
		return MP6E_PURE.working_between(from, MP6E_PURE.snap_weekday(from, to));
	},
	// The day a visit lands on after moving `days` (calendar, or working days).
	date_for(ymd, days, unit) {
		return unit === "working" ? MP6E_PURE.add_working_days(ymd, days) : MP6B_PURE.ymd_add(ymd, days);
	},
	// The create_visit arguments from the form: the technician as shown ("" is nobody), the crew as ticked,
	// the length (0: Settings' visit length), Full day, and the fountain and checklist when set.
	create_args(values, crew) {
		const args = {
			project: values.project,
			date: values.date,
			technician: values.technician || "",
			crew: JSON.stringify(crew || []),
			hours: Math.max(0, Number(values.hours) || 0),
			full_day: values.full_day ? 1 : 0,
		};
		if (values.serial_no) args.serial_no = values.serial_no;
		if (values.template) args.template = values.template;
		return args;
	},
};

const MP6E_METHODS = {
	init_phase6e() {
		// send() looks a method up in MP.methods.
		Object.assign(MP.methods, MP6E.methods);
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.p6e_menu_items(target));
		this.p6_legend_providers = this.p6_legend_providers || [];
		this.p6_legend_providers.push(() => this.p6e_legend_sections());
		// "Add visit…" is an item of the toolbar's + Add menu (MPTB, v2), for whoever may create
		// visits (data.can_create_visits).
		this.$body[0].addEventListener("dblclick", (e) => this.p6e_dblclick(e));
	},

	render_phase6e() {
		// Nothing of its own to draw: Add visit… is offered by the + Add menu (tb_add_items).
	},

	// ------------------------------------------------------------------ move by: calendar or working days

	p6e_unit() {
		if (!this.p6e_unit_value) {
			this.p6e_unit_value = this.load_pref(MP6E.pref, "calendar") === "working" ? "working" : "calendar";
		}
		return this.p6e_unit_value;
	},

	p6e_set_unit(unit) {
		if (!MP6E.units.includes(unit) || unit === this.p6e_unit()) return;
		this.p6e_unit_value = unit;
		this.save_pref(MP6E.pref, unit);
		this.p6b_render_bar();
	},

	// "Move by: Calendar days | Working days", a field of the Move… panel (it was a switch on the
	// selection bar until the planner UX redesign, D12). The Select shows the words; the unit is the key.
	p6e_unit_label(unit) {
		return unit === "working" ? __("Working days") : __("Calendar days");
	},

	p6e_unit_options() {
		return MP6E.units.map((unit) => this.p6e_unit_label(unit)).join("\n");
	},

	// The panel's Select changed: remember the unit, and say what the number below counts now.
	p6e_panel_unit(panel) {
		if (!panel) return;
		const picked = MP6E.units.find((unit) => this.p6e_unit_label(unit) === panel.get_value("unit"));
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
			? __("Drag any of them and they all move by the same number of working days (Monday to Friday). Each visit is saved on its own. Move… changes the unit. Esc clears the selection.")
			: __("Drag any of them and they all move by the same number of calendar days. Each visit is saved on its own. Move… changes the unit. Esc clears the selection.");
	},

	p6e_drop_days(from, to) {
		return MP6E_PURE.drop_days(from, to, this.p6e_unit());
	},

	p6e_date(ymd, days) {
		return MP6E_PURE.date_for(ymd, days, this.p6e_unit());
	},

	p6e_move_label() {
		return this.p6e_unit() === "working" ? __("Move by (working days)") : __("Move by (days)");
	},

	p6e_move_default() {
		return this.p6e_unit() === "working" ? 5 : 7;
	},

	p6e_move_note() {
		return this.p6e_unit() === "working"
			? __("Working days (Monday to Friday); a negative number moves them earlier. Visits cannot move to a day that has passed.")
			: __("Calendar days; a negative number moves them earlier. Visits cannot move to a day that has passed.");
	},

	p6e_question(count, days) {
		if (this.p6e_unit() === "working") {
			return days > 0
				? __("Move {0} visits {1} working day(s) later?", [count, days])
				: __("Move {0} visits {1} working day(s) earlier?", [count, -days]);
		}
		return days > 0
			? __("Move {0} visits {1} day(s) later?", [count, days])
			: __("Move {0} visits {1} day(s) earlier?", [count, -days]);
	},

	// ------------------------------------------------------------------ add visit here

	p6e_menu_items(target) {
		if (!target || target.kind !== "cell" || !target.ymd) return [];
		const can = !!(this.data && this.data.can_create_visits);
		const past = target.ymd < this.today();
		const drafting = !!this.draft_on;
		return [
			{
				label: __("Add visit here…"),
				hint: !can
					? __("You cannot add visits")
					: past
					? __("Not on a past day")
					: drafting
					? __("Not in Draft mode")
					: __("Double-click"),
				disabled: !can || past || drafting,
				on_click: () => this.p6e_add_visit(target),
			},
		];
	},

	p6e_legend_sections() {
		return [
			{
				title: __("Adding a visit"),
				items: [
					{
						sample_html: "",
						text: __("Double-click an empty spot, or + Add ▸ Add visit… in the toolbar, to draft a visit for a site. It is linked to the site's active contract when there is one, so it counts as a scheduled visit; with none, it is a plain visit."),
					},
				],
			},
		];
	},

	p6e_dblclick(e) {
		if (!this.data || !this.data.can_create_visits) return;
		const el = e.target;
		if (!el || !el.closest) return;
		const skip =
			".mp-card, .mp-busy, .mp-day-head, .mp-off-badges, .mp-chip, .mp-person, .mp-toolbar, .mp-tray, .mp-res, .mp-route, " +
			".mp-p6b-bar, [data-route-resource], [class*='pk-'], button, a, input, select, textarea, label";
		if (el.closest(skip)) return;
		const target = this.p6b_target(el);
		if (!target || target.kind !== "cell" || !target.ymd) return;
		e.preventDefault();
		if (window.getSelection) window.getSelection().removeAllRanges();
		this.p6e_add_visit(target);
	},

	// The technician the form starts with: the crew-view row, else the toolbar's technician filter.
	p6e_start_user(target) {
		const person = (target && target.user) || (this.technician && this.technician !== "__none__" ? this.technician : "");
		const known = ((this.data && this.data.technicians) || []).some((tech) => tech.user === person && tech.enabled);
		return known ? person : "";
	},

	// The form. `target` is an empty spot ({ymd, user}) or null from the toolbar.
	p6e_add_visit(target) {
		if (!this.data) return null;
		// C1: a visit that does not exist yet cannot be a draft; say so before the form opens.
		if (this.c1_refuse && this.c1_refuse("create_visit")) return null;
		if (!this.data.can_create_visits) {
			frappe.show_alert({ message: __("You cannot add visits."), indicator: "orange" }, 5);
			return null;
		}
		const today = this.today();
		const start_date = target && target.ymd ? target.ymd : this.anchor > today ? this.anchor : today;
		if (start_date < today) {
			frappe.show_alert({ message: __("Visits can only be added to today or a later day."), indicator: "orange" }, 6);
			return null;
		}
		const state = { token: 0, defaults: null, cell_user: this.p6e_start_user(target), serials: [] };
		const technicians = ((this.data && this.data.technicians) || []).filter((person) => person.enabled);
		const panel = this.p6b_panel({
			title: __("Add a visit"),
			subtitle: [target && target.user ? this.tech_name(target.user) : "", mp_when(start_date)].filter(Boolean).join(" · "),
			key: `mp-p6e-add:${(target && target.user) || ""}:${start_date}`,
			fields: [
				{
					fieldtype: "Link",
					fieldname: "project",
					label: __("Site"),
					options: "Project",
					reqd: 1,
					description: __("Sites that have a Maintenance Profile."),
					get_query: () => ({ query: MP6E.sites_query }),
					onchange: () => this.p6e_load_defaults(panel, state),
				},
				{ fieldtype: "Date", fieldname: "date", label: __("Date"), reqd: 1, default: start_date },
				{
					fieldtype: "Select",
					fieldname: "technician",
					label: __("Technician"),
					options: [{ label: __("Unassigned"), value: "" }].concat(
						technicians.map((person) => ({ label: person.name, value: person.user }))
					),
					default: state.cell_user,
				},
				{ fieldtype: "Section Break", label: __("Crew") },
				{ fieldtype: "HTML", fieldname: "crew_editor", options: '<div class="mp-cm-list"></div>' },
				{ fieldtype: "Section Break" },
				{
					fieldtype: "Float",
					fieldname: "hours",
					label: __("Planned hours"),
					description: __("Hours each person is booked. Blank or 0: Settings' visit length."),
				},
				{ fieldtype: "Column Break" },
				{ fieldtype: "Check", fieldname: "full_day", label: __("Full day"), description: __("The visit takes each person's whole day.") },
				{ fieldtype: "Section Break", label: __("Contract") },
				{ fieldtype: "HTML", fieldname: "contract_note", options: '<div class="mp-p6e-note"></div>' },
				{
					fieldtype: "Link",
					fieldname: "serial_no",
					label: __("Fountain"),
					options: "Serial No",
					description: __("Blank for a visit to the whole site."),
					get_query: () => (state.serials.length ? { filters: { name: ["in", state.serials] } } : {}),
					onchange: () => this.p6e_fountain_changed(panel, state),
				},
				{ fieldtype: "Link", fieldname: "template", label: __("Checklist template"), options: "Sapphire Maintenance Template" },
			],
		});
		panel.set_primary_action(__("Add visit"), (values) => this.p6e_add_visit_save(panel, state, values || {}));
		panel.show();
		this.build_crew_editor(panel, { crew: [] });
		return panel;
	},

	// The site was picked: its default technician (unless the spot named one), crew, length, Full day,
	// fountain and checklist.
	p6e_load_defaults(panel, state) {
		const project = panel.get_value("project");
		if (!project) return null;
		const token = ++state.token;
		return Promise.resolve(frappe.call({ method: MP6E.defaults, args: { project } }))
			.then((r) => {
				const found = r && r.message;
				if (token !== state.token || !found) return;
				state.defaults = found;
				this.p6e_apply_defaults(panel, state, found);
			})
			.catch(() => null);
	},

	p6e_apply_defaults(panel, state, found) {
		state.serials = (found.features || []).map((feature) => feature.serial_no);
		const known = ((this.data && this.data.technicians) || []).filter((person) => person.enabled);
		const lead = state.cell_user || found.technician || "";
		const options = [{ label: __("Unassigned"), value: "" }].concat(
			known.map((person) => ({ label: person.name, value: person.user }))
		);
		if (lead && !known.some((person) => person.user === lead)) {
			options.push({ label: found.technician_name || this.tech_name(lead), value: lead });
		}
		// The Select re-reads df.options when a value is set on it.
		if (panel.fields_dict.technician) panel.fields_dict.technician.df.options = options;
		panel.set_value("technician", lead);
		panel.$wrapper.find(".mp-cm-list").empty();
		this.build_crew_editor(panel, {
			crew: (found.crew || []).map((member) => ({ user: member.user, name: member.name, hours: member.hours })),
		});
		panel.set_value("hours", Number(found.hours) || 0);
		panel.set_value("full_day", found.full_day ? 1 : 0);
		panel.set_value("serial_no", found.serial_no || "");
		panel.set_value("template", found.template || "");
		const note = found.contract
			? __("Linked to the active contract {0}, so this counts as a scheduled visit.", [mp_esc(found.contract)])
			: __("No active contract for this site: a plain visit, not linked to a contract.");
		const open = found.open_visit
			? ` ${__("{0} is already the open regular visit for it.", [mp_esc(found.open_visit)])}`
			: "";
		panel.$wrapper.find(".mp-p6e-note").html(`${note}${open}`);
	},

	// A different fountain: the checklist template follows it.
	p6e_fountain_changed(panel, state) {
		const found = state.defaults;
		const serial_no = panel.get_value("serial_no");
		if (!found || !serial_no) return;
		const feature = (found.features || []).find((row) => row.serial_no === serial_no);
		if (feature && feature.template && panel.get_value("template") !== feature.template) {
			panel.set_value("template", feature.template);
		}
	},

	p6e_add_visit_save(panel, state, values) {
		if (!values.project || !values.date) return null;
		if (values.date < this.today()) {
			frappe.msgprint(__("Pick today or a later day."));
			return null;
		}
		const lead = values.technician || "";
		const crew = this.read_crew_editor(panel, lead);
		const args = MP6E_PURE.create_args(values, crew);
		const site = (state.defaults && state.defaults.title) || values.project;
		panel.hide();
		return this.p6e_create(args, site);
	},

	// One create_visit through send(): a conflict asks for a reason, and the Undo toast deletes the new
	// draft again. The snapshot is filled in from the answer; send() only pushes it on success.
	p6e_create(args, site) {
		const snapshot = { p6e: "created", site, record: null, modified: null };
		const message = __("{0} visit added on {1}", [site, mp_when(args.date)]);
		let navigated = false;
		return this.send("create_visit", args, { snapshot, message, noun: "visit" })
			.then((result) => {
				if (!result || !result.name) return result;
				snapshot.record = result.name;
				snapshot.modified = result.modified;
				this.p6b.flash = { key: result.name, date: result.date || args.date, wait: true };
				const date = result.date || args.date;
				if (date && !this.range_days().includes(date)) {
					navigated = true;
					this.go(this.view, date);
				}
				return result;
			})
			.finally(() => {
				if (!navigated) this.load();
			});
	},

	// Undo of a visit that was just added: delete the draft. The server refuses once anything was
	// attached to it, and says why.
	p6e_undo(snap) {
		if (!snap || snap.p6e !== "created") return false;
		if (!snap.record) {
			frappe.show_alert({ message: __("That visit was not added, so there is nothing to undo."), indicator: "orange" }, 5);
			return true;
		}
		const modified = this.modified[snap.record] || snap.modified;
		this.send("remove_created_visit", { record: snap.record, modified }, { message: __("Undone: {0}", [snap.site]), noun: "visit" })
			.finally(() => this.load());
		return true;
	},
};

Object.assign(MaintenancePlanner.prototype, MP6E_METHODS);

// ====================================================================== Fountain Move jobs
//
// Nik, 2026-10-21: "the Maintenance planner also handles Fountain Moves". A move job is a Service
// project with Fountain Move ticked (project_enhancements/fountain_move_jobs.py). Its tasks with
// hours (the Move day; the office tasks have none) are cards here, `kind: "move"`, and they move
// here: the Project Planner shows them read-only. A deliberate exception to "each planner moves only
// its own work".
//
//   - get_planner's `moves` (dated, on the task's first day) and `unscheduled_moves` (the tray). A card
//     has a visit card's shape where it matters (technician = the lead, crew = the rest, date, site,
//     movable), so the technician filter, the crew view rows and the badges read it as a visit.
//   - Dragging one to a day moves the task; to another person's row in the crew view hands that
//     person's place to them; dropping a technician on it adds them. All three go to
//     move_fountain_move, which is the Project Planner's own write (conflicts ask for a reason,
//     overbooking warns and never blocks). Undo puts the day and the crew back.
//   - Draft mode does not hold them back (MPC1.refusals.move_fountain_move): drafts are visits.
//   - Their hours are already in each person's day through the shared engine; the read-only Project
//     booking of the same task is left out (maintenance_planner._drop_move_bookings).
//
// Hooks into the class above, one line each: index, all_cards, render_crew (the tray), card_row_html,
// plan_drop (twice), drop, ask_reason (noun "task"), undo, open_card and p6b_card_items.

const MPFM_METHODS = {
	fm_cards() {
		const data = this.data || {};
		return [].concat(data.moves || [], data.unscheduled_moves || []);
	},

	fm_card_html(card, row_user) {
		const people = this.visit_people(card);
		const in_row = this.view === "crew" && !!card.date;
		const sub = [card.subject, __("{0}h", [mp_hours(card.hours)])];
		if (card.start && card.start !== card.date) sub.push(__("from {0}", [mp_when(card.start)]));
		if (card.end) sub.push(__("until {0}", [mp_when(card.end)]));
		const chips = [`<span class="mp-chip mp-move-chip">${mp_esc(__("Move"))}</span>`];
		if (card.tentative) chips.push(`<span class="mp-chip mp-pencil-chip">${mp_esc(__("Pencil"))}</span>`);
		if (card.short > 0) {
			chips.push(`<span class="mp-chip mp-red">${mp_esc(__("Needs {0} more", [card.short]))}</span>`);
		}
		if (card.overdue) chips.push(`<span class="mp-chip mp-red">${mp_esc(__("Overdue"))}</span>`);
		const who = people.length ? people.map((user) => this.tech_name(user)).join(", ") : __("Unassigned");
		const tip = [
			card.site_full,
			sub.join(" · "),
			people.length > 1 ? `${__("Crew")}: ${who}` : who,
			__("Fountain Move: moved here, read-only on the Project Planner."),
		]
			.filter(Boolean)
			.join("\n");
		const classes = ["mp-card", "mp-move"];
		if (card.movable) classes.push("mp-movable");
		if (card.saving) classes.push("mp-saving");
		if (card.tentative) classes.push("mp-pencil");
		if (card.overdue) classes.push("mp-overdue");
		const row_attr = row_user === undefined ? "" : ` data-row-user="${mp_esc(row_user)}"`;
		return `
			<div class="${classes.join(" ")}" data-key="${mp_esc(card.key)}"${row_attr} tabindex="0" title="${mp_esc(tip)}">
				<div class="mp-card-top">
					<span class="mp-card-title">${mp_esc(card.site || card.project || card.name)}</span>
					${this.people_badges(card, in_row)}
				</div>
				<div class="mp-card-sub">${mp_esc(sub.join(" · "))}</div>
				<div class="mp-card-who">${in_row ? "" : mp_esc(who)} ${chips.join("")}</div>
			</div>`;
	},

	// A technician dropped on a move card joins its crew (the first one on an empty task leads it).
	fm_plan_person(card, user) {
		const name = this.tech_name(user);
		const site = this.site_of(card);
		if (!card.movable) return { refuse: __("You cannot change {0}.", [site]) };
		if (this.draft_on) return { refuse: __(MPC1.refusals.move_fountain_move) };
		const people = this.visit_people(card);
		if (people.includes(user)) return { refuse: __("{0} is already on {1}.", [name, site]) };
		return { card, fm: { crew: people.concat([user]), added: user } };
	},

	// A move card dropped on a day (and, in the crew view, on a person's row).
	fm_plan_move(source, target) {
		const card = source.card;
		if (this.draft_on) return { refuse: __(MPC1.refusals.move_fountain_move) };
		if (target.date !== card.date && target.date < this.today()) {
			return { refuse: __("Move jobs can only be moved to today or a later day.") };
		}
		const change = {};
		if (target.date && target.date !== card.date) change.date = target.date;
		const people = this.visit_people(card);
		const row_user = source.from_user == null ? card.technician || "" : source.from_user;
		if (target.row && (target.user || "") !== row_user) {
			if (!target.user) {
				return { refuse: __("Drop {0} on a technician's row to hand it over.", [this.site_of(card)]) };
			}
			if (people.includes(target.user)) {
				return { refuse: __("{0} is already on {1}.", [this.tech_name(target.user), this.site_of(card)]) };
			}
			// That person's place goes to the new one; from the Unassigned row, they join.
			change.crew = row_user
				? people.map((user) => (user === row_user ? target.user : user))
				: people.concat([target.user]);
			change.from_user = row_user;
			change.added = target.user;
		}
		return Object.keys(change).length ? { card, fm: change } : null;
	},

	// Show it straight away, save it, reload: the reload is the truth.
	fm_apply(card, change) {
		const before = { date: card.start || card.date || null, crew: this.visit_people(card) };
		const site = this.site_of(card);
		const args = { task: card.name, modified: this.modified[card.name] || card.modified };
		if (change.date) {
			args.date = change.date;
			card.date = change.date;
		}
		if (change.crew) {
			args.crew = JSON.stringify(change.crew);
			card.technician = change.crew[0] || null;
			card.crew = change.crew.slice(1).map((user) => ({ user, name: this.tech_name(user), hours: null }));
		}
		let message;
		if (change.from_user && change.added) {
			message = __("{0}: {1} handed over to {2}", [site, this.tech_name(change.from_user), this.tech_name(change.added)]);
		} else if (change.added) {
			message = __("{0} added to {1}", [this.tech_name(change.added), site]);
		} else {
			message = __("{0} moved to {1}", [site, mp_when(change.date)]);
		}
		if (change.date && change.crew) message += `, ${__("moved to {0}", [mp_when(change.date)])}`;
		card.saving = true;
		this.render();
		return this.send("move_fountain_move", args, {
			// The crew goes back only when this change touched it: sending it otherwise would turn a
			// crew read from assignments into crew rows.
			snapshot: {
				kind: "move",
				site,
				task: card.name,
				date: change.date ? before.date : null,
				crew: change.crew ? before.crew : null,
			},
			message,
			noun: "task",
		}).finally(() => this.load());
	},

	// Undo a move-job change: its day (unless that day has passed, or it was unscheduled) and its crew.
	fm_undo(snap) {
		if (!snap || snap.kind !== "move") return false;
		const card = this.by_key[`move|${snap.task}`];
		if (!snap.crew && !(snap.date && snap.date >= this.today())) {
			// Only a day to undo, and it is gone (scheduled from the tray, or a day that has passed).
			frappe.show_alert(
				{
					message: __("{0} keeps its day: a task cannot go back to a day that has passed or to unscheduled.", [
						snap.site,
					]),
					indicator: "orange",
				},
				8
			);
			return true;
		}
		const args = { task: snap.task, modified: this.modified[snap.task] || (card && card.modified) || "" };
		if (snap.crew) args.crew = JSON.stringify(snap.crew);
		const date_back = !!(snap.date && snap.date >= this.today());
		if (date_back) args.date = snap.date;
		if (card) {
			card.saving = true;
			this.render();
		}
		this.send("move_fountain_move", args, {
			message: __("Undone: {0}", [snap.site]),
			auto_reason: __(MP.undo_reason),
			noun: "task",
		})
			.then((result) => {
				if (result && snap.date && !date_back) {
					frappe.show_alert(
						{
							message: __(
								"{0} keeps its day: a task cannot go back to a day that has passed or to unscheduled.",
								[snap.site]
							),
							indicator: "orange",
						},
						10
					);
				}
			})
			.finally(() => this.load());
		return true;
	},

	// The right-click menu of a move card: open the task or its project.
	fm_card_items(card) {
		const items = [{ label: __("Open task"), on_click: () => this.open_card(card) }];
		if (card.project) {
			items.push({ label: __("Open project"), on_click: () => frappe.set_route("Form", "Project", card.project) });
		}
		return items;
	},
};

Object.assign(MaintenancePlanner.prototype, MPFM_METHODS);

// ====================================================================== C1: Draft mode and pencil visits
//
// The Project Planner's draft mode and pencil, on the Maintenance Planner (v1.601.0). A Google
// Calendar sync that pushes only committed work comes next, so the two planners need the same line
// between "trying things" and "the schedule".
//
//   - "Draft mode", a switch in the toolbar's More ▸ Tools remembered per browser (ee_maintenance_planner_draft). While it
//     is on, every visit write that send() makes (move_visit and add_crew: drags, the dialog, the
//     right-click menu, the selection bar, the Conflict center's fixes, Undo) goes with draft=1 and
//     lands on the planner's own Planner Draft Change rows (api/maintenance_drafts.py). Nobody's
//     ToDos move, nobody is texted, no customer is emailed. get_planner(draft=1) shows the visits
//     as drafted: a dashed outline and a "Draft" chip, and the free hours counted with them.
//   - A bar along the bottom: "N unpublished change(s) · Publish · Discard" (and "Discard out-of-date"
//     for a draft of a visit somebody else changed since). Publish asks once for a reason when the
//     drafts conflict together (all or nothing), saves each through move_visit's own path, and
//     tells each person whose visits moved. Task drafts are the Project Planner's: this bar never
//     counts, publishes or discards them.
//   - Refused in Draft mode, on the page before the server is asked (the server refuses too):
//     Add visit (a visit that does not exist cannot be a draft) and moving a projected visit (it
//     rewrites the contract's next visit date at once).
//   - Pencil: "Pencil" / "Firm up" in a visit's right-click menu and "Tentative (pencil)" in its
//     dialog (move_visit's tentative, drafted like anything else in Draft mode). A pencilled visit is
//     hatched like a pencil task on the Project Planner: soft hours that never need a reason, nobody
//     assigned, no 6 AM text, no customer date email until it is firmed up.
//
// Hooks into the class above, one line each: init_phase_c1 (constructor), c1_draft_args (load),
// c1_refuse and c1_with_draft (send), render_phase_c1 (render), MPC1.refusals (plan_drop and
// p6e_add_visit), plus the pencil and draft classes, chips and fields in kind_class, booking_html,
// card_row_html, apply, snapshot_of, save_move, undo and open_card.

const MPC1 = {
	pref: "ee_maintenance_planner_draft",
	api: "erpnext_enhancements.api.maintenance_drafts",
	// The writes that go to the drafts while Draft mode is on.
	draft_methods: ["move_visit", "add_crew"],
	// The writes Draft mode cannot hold back (the server's maintenance_drafts.PROJECTED_REFUSAL and
	// DRAFT_REFUSAL say the same).
	refusals: {
		move_fountain_move:
			"Draft mode holds back changes to visits only. A Fountain Move task is saved at once, as it is on the Project Planner. Turn Draft mode off to move it.",
		move_projected:
			"Draft mode holds back changes to drafted visits only. This visit is projected from its contract, and moving it changes the contract's next visit date at once. Turn Draft mode off to move it.",
		create_visit:
			"Draft mode holds back changes to visits that already exist; a new visit cannot be a draft. Turn Draft mode off to add it.",
	},
};

const MPC1_METHODS = {
	init_phase_c1() {
		this.draft_on = this.load_pref(MPC1.pref, "0") === "1";
		// The switch is an item of the toolbar's More ▸ Tools (MPTB, v1.687.0); the strip sits right under
		// the toolbar, where the switch's state shows.
		this.$draft_bar = $('<div class="mp-draft-bar" role="region"></div>')
			.attr("aria-label", __("Draft mode"))
			.hide()
			.insertAfter(this.$body.find(".mp-toolbar").first());
		this.p6_menu_providers = this.p6_menu_providers || [];
		this.p6_menu_providers.push((target) => this.c1_menu_items(target));
		this.p6_legend_providers = this.p6_legend_providers || [];
		this.p6_legend_providers.push(() => this.c1_legend_sections());
	},

	// ------------------------------------------------------------------ draft mode

	c1_draft_args() {
		return this.draft_on ? { draft: 1 } : {};
	},

	c1_with_draft(method, args) {
		return this.draft_on && MPC1.draft_methods.includes(method) ? Object.assign({}, args, { draft: 1 }) : args;
	},

	// True (and the planner told why) when Draft mode cannot hold this write back.
	c1_refuse(method) {
		const text = this.draft_on ? MPC1.refusals[method] : null;
		if (!text) return false;
		frappe.msgprint({ title: __("Not in Draft mode"), message: __(text), indicator: "orange" });
		return true;
	},

	set_draft_mode(on) {
		this.draft_on = !!on;
		this.save_pref(MPC1.pref, this.draft_on ? "1" : "0");
		// An undo recorded in one mode must not be replayed in the other.
		this.undo_stack = [];
		this.update_undo_button();
		frappe.show_alert(
			{
				message: this.draft_on
					? __("Draft mode: your changes to visits are kept for you until you publish them.")
					: __("Draft mode off: changes save straight to the visits. Your unpublished drafts are kept."),
				indicator: "blue",
			},
			7
		);
		this.load();
	},

	render_phase_c1() {
		this.c1_render_bar();
	},

	c1_render_bar() {
		const $bar = this.$draft_bar;
		if (!$bar) return;
		if (!this.draft_on || !this.data) {
			$bar.hide().empty();
			return;
		}
		const drafts = this.data.drafts || {};
		const count = Number(drafts.count) || 0;
		const stale = drafts.stale || [];
		$bar.empty().show();
		$('<span class="mp-draft-text"></span>')
			.text(
				count
					? __("Draft mode on: {0} change(s) not published yet", [count])
					: __("Draft mode on: nothing drafted yet. Your changes to visits stay yours until you publish them.")
			)
			.appendTo($bar);
		if (stale.length) {
			$('<span class="mp-chip mp-red"></span>')
				.text(__("{0} out of date", [stale.length]))
				.attr("title", stale.map((entry) => `${entry.subject || entry.visit}: ${entry.reason}`).join("\n"))
				.appendTo($bar);
		}
		$('<span class="mp-spacer"></span>').appendTo($bar);
		if (stale.length) {
			$('<button type="button" class="btn btn-default btn-xs"></button>')
				.text(__("Discard out-of-date"))
				.on("click", () => this.c1_discard(stale.map((entry) => entry.visit)))
				.appendTo($bar);
		}
		if (count > stale.length) {
			$('<button type="button" class="btn btn-primary btn-xs"></button>')
				.text(__("Publish"))
				.on("click", () => this.c1_publish())
				.appendTo($bar);
		}
		if (count) {
			$('<button type="button" class="btn btn-default btn-xs"></button>')
				.text(__("Discard"))
				.on("click", () => this.c1_discard())
				.appendTo($bar);
		}
	},

	// Publish every visit draft. Conflicts across the batch come back as needs_reason; the same reason
	// dialog as a drag asks once, and the batch is sent again with it (all or nothing).
	c1_publish(reason) {
		return Promise.resolve(
			frappe.call({
				method: `${MPC1.api}.publish_drafts`,
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
					return this.ask_reason(result.conflicts || {}, "visit").then((given) =>
						given ? this.c1_publish(given) : null
					);
				}
				this.c1_show_publish_result(result);
				this.undo_stack = [];
				this.update_undo_button();
				return this.load();
			})
			.catch(() => null);
	},

	c1_show_publish_result(result) {
		const published = result.published || [];
		const problems = [].concat(result.skipped || [], result.failed || []);
		const told = Number(result.notified) || 0;
		if (!problems.length) {
			frappe.show_alert(
				{ message: __("{0} change(s) published; {1} people told.", [published.length, told]), indicator: "green" },
				8
			);
			return;
		}
		const list = problems
			.map((entry) => `<li><b>${mp_esc(entry.subject || entry.visit)}</b>: ${mp_esc(entry.reason)}</li>`)
			.join("");
		frappe.msgprint({
			title: __("Published {0} of {1}", [published.length, published.length + problems.length]),
			message:
				`<p>${mp_esc(__("{0} people were told about their changes.", [told]))}</p>` +
				`<p>${mp_esc(__("Not published (still in your drafts):"))}</p><ul>${list}</ul>`,
			indicator: "orange",
		});
	},

	// Discard every visit draft, or only `visits` (the out-of-date ones). The visits stay as they are.
	c1_discard(visits) {
		const drafts = (this.data && this.data.drafts) || {};
		const count = visits ? visits.length : Number(drafts.count) || 0;
		frappe.confirm(__("Discard {0} unpublished change(s)? The visits stay as they are.", [count]), () =>
			Promise.resolve(
				frappe.call({
					method: `${MPC1.api}.discard_drafts`,
					args: visits ? { visits: JSON.stringify(visits) } : {},
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

	// ------------------------------------------------------------------ pencil

	// "Pencil" or "Firm up" on a visit nobody has started. A normal write through apply(), so the reason
	// prompt (firming up checks everyone on it), Undo and Draft mode all apply.
	c1_menu_items(target) {
		if (!target || target.kind !== "card" || !target.card || target.card.kind !== "visit") return [];
		const card = target.card;
		const can = !!(card.movable && this.data && this.data.can_move_visits);
		const why = !(this.data && this.data.can_move_visits)
			? __("You cannot move visits")
			: card.started
			? __("Started")
			: card.status !== "draft"
			? __("Finished")
			: __("Read-only");
		return [
			card.tentative
				? {
						label: __("Firm up"),
						hint: can ? __("Assign and tell everyone") : why,
						disabled: !can,
						on_click: () => this.apply(card, { tentative: 0 }),
				  }
				: {
						label: __("Pencil"),
						hint: can ? __("Make it tentative") : why,
						disabled: !can,
						on_click: () => this.apply(card, { tentative: 1 }),
				  },
		];
	},

	c1_legend_sections() {
		const sample = (classes, title) =>
			`<span class="mp-card mp-c1-sample ${classes}">${mp_esc(__(title))}</span>`;
		return [
			{
				title: __("Pencil and Draft mode"),
				items: [
					{
						sample_html: sample("mp-pencil", "Visit"),
						text: __("Pencil (tentative): soft hours that never need a reason; nobody is assigned, texted or told the date until you firm it up."),
					},
					{
						sample_html: sample("mp-drafted", "Visit"),
						text: __("Draft: a change kept to yourself until you publish it (Draft mode)."),
					},
				],
			},
		];
	},
};

Object.assign(MaintenancePlanner.prototype, MPC1_METHODS);

// ====================================================================== The toolbar (v1.687.0, v2)
//
// The Maintenance Planner's half of the toolbar redesign Nik picked on 2026-10-14, and of the next
// step he approved on 2026-10-21 (the planner UX redesign, P1). The Project Planner's PPTB block has
// the long version. One bar, the same structure as that planner's:
//
//   ‹ Today ›  title  [highlight]  Month Week Crew   search  Technician
//   [alert chips]  + Add ▾  More ▾  [↶]  ?  ↻
//
//   - + Add ▾     Add visit… (6E, for whoever may create visits) and Day note… (6D, schedulers)
//   - More ▾      four sections:
//       Display   Color by (6C; the default is Visit type) and the key of its colors; Projected
//                 visits, Project work and Project work as cards (P10), each with a check mark. The
//                 key of the visit kinds, which was the legend row under the toolbar and then a
//                 section of Display, is in "?" only now
//       Views     the saved views (6C), Save current view as…, Manage views…
//       Tools     Block time… (6D, schedulers); Conflicts… (6D, even at none); Tap to move (6C);
//                 Draft mode (C1); My day (P10, for a technician who has one); Undo
//       Print     Print this view and Print selection only (6C); Day Board
//   - alert chips Conflicts (6D) and Overdue, in the danger tone, a count and a word each, there only
//                 above zero. Conflicts opens the Conflict center; Overdue opens a side panel listing
//                 the overdue visits on screen, each of which opens its visit (the planner had no
//                 overdue list of its own; only a red card and its chip). There is no Running over:
//                 visits carry no clocked-against-planned hours here
//   - ↶, ?, ↻     Undo as a small icon only while there is something to undo; the help, whose first
//                 section is the tips that used to be banners on load; refresh
//   - Draft mode  a strip under the bar, "Draft mode on: N changes not published yet", with
//                 Publish and Discard (C1's c1_render_bar, c1_publish, c1_discard)
//   - the tabs    Month Week Crew are a WAI-ARIA tablist (planner_kit toolbar.tabs)
//
// The Day Board stays where it was too, the page head's secondary button, and is also in More ▸ Print.
// The Move by switch (calendar or working days) left the selection bar for a field of the Move…
// panel (D12). There is no Utilization view here: the Crew Utilization report is the Project
// Planner's view, and it counts maintenance visits in every person's booked hours already.
//
// On a phone the bar is ‹ Today ›, the title, the view switcher and "Filters and tools", which opens
// planner_kit.toolbar.sheet with the filter, the chips and the same sections; Back closes it.
//
// Hooks into the class above, one line each: init_toolbar (constructor), tb_ready (p6a_ready),
// tb_render (render, p6d_after_list) and tb_close_sheet (p6b_pick). P10's MP10 block at the end of the
// page adds p10_tools_items (tb_tools_items) and p10_sync_myday (tb_render).

const MPTB = {
	phone_max: 767,
	tones: { danger: "mp-tb-danger", warn: "mp-tb-warn" },
	overdue_width: 460,
};

const MPTB_METHODS = {
	// The two menus: [name, label, what is in it, the button's text]. Written out as __() calls, so
	// they are translated.
	tb_menu_list() {
		return [
			["add", __("Add"), __("Add a visit or a day note"), __("+ Add")],
			["more", __("More"), __("Display, Views, Tools and Print"), __("More")],
		];
	},

	init_toolbar() {
		this.tb = { kit: null, menus: {}, $menus: {}, sheet: null, tabs: null };
		this.tb_menu_list().forEach(([name, label, title, text]) => {
			const $button = $('<button type="button" class="btn btn-default btn-sm mp-tb-menu"></button>')
				.attr("data-tb-menu", name)
				.attr("title", title)
				.on("click", () => {
					if (!this.tb.kit) frappe.show_alert({ message: __("One moment: the planner is still loading."), indicator: "blue" }, 4);
				})
				.appendTo(this.$tools);
			$("<span></span>").text(text || label).appendTo($button);
			$('<span class="mp-tb-caret" aria-hidden="true">▾</span>').appendTo($button);
			this.tb.$menus[name] = $button;
		});
		this.$tb_more = $('<button type="button" class="btn btn-default btn-sm mp-tb-more"></button>')
			.attr("aria-haspopup", "dialog")
			.attr("aria-label", __("Filters and tools"))
			.on("click", () => this.tb_open_sheet())
			.appendTo(this.$body.find(".mp-toolbar").first());
		$("<span></span>").text(__("Filters and tools")).appendTo(this.$tb_more);
		this.$tb_more_count = $('<span class="mp-tb-count" aria-hidden="true"></span>').hide().appendTo(this.$tb_more);
		// A menu or the sheet left open when the page hides (another page) closes with it; a route
		// change of the planner's own closes a menu through the kit (toolbar.js).
		const wrapper = this.page && this.page.wrapper;
		if (wrapper && typeof wrapper.on === "function") {
			wrapper.on("hide", (e) => {
				if (e.target === wrapper[0]) this.tb_close_menus();
			});
		}
		this.init_p10();
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
			this.tb.tabs = kit.toolbar.tabs(this.$seg[0], { label: __("Planner views"), selected: this.view });
		}
		this.tb_render();
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
		return [].concat(...sections.map((section) => (section.items || []).filter((item) => item && !item.divider && !item.heading)));
	},

	// More's four sections; the phone's sheet draws the same ones.
	tb_more_sections() {
		return [
			{ key: "display", title: __("Display"), items: this.tb_display_items() },
			{ key: "views", title: __("Views"), items: this.p6c_view_items() },
			{ key: "tools", title: __("Tools"), items: this.tb_tools_items() },
			{ key: "print", title: __("Print"), items: this.tb_print_items() },
		];
	},

	// + Add: a visit (the double-click's form, started on the first day on screen) or a day note.
	tb_add_items() {
		const items = [];
		if (this.data && this.data.can_create_visits) {
			items.push({
				label: __("Add visit…"),
				title: __("Draft a visit for a site, a day and a technician. Double-click an empty spot does the same."),
				on_click: () => this.p6e_add_visit(null),
			});
		}
		if (this.data && this.data.can_schedule) {
			items.push({
				label: __("Day note…"),
				title: __("A note for everyone on a day, such as a shop meeting"),
				on_click: () => this.p6d_note_form({ date: this.p6d_default_date() }),
			});
		}
		return items;
	},

	// Display: Color by and the key of its colors, then what to draw. The key of the visit kinds is in "?"
	// (legend_sections) and nowhere here since P10.
	tb_display_items() {
		return this.p6c_color_items().concat([
			{ divider: true },
			{
				label: __("Projected visits"),
				checked: !!this.show_projected,
				title: __("The visits a contract will draft next, before the scheduler drafts them"),
				on_click: () => this.set_show_projected(!this.show_projected),
			},
			{
				label: __("Project work"),
				checked: !!this.show_project_work,
				title: __(
					"Project tasks, rental crew tasks and travel, read-only: a thin grey strip on each technician's day. Move them on the Project Planner."
				),
				on_click: () => this.set_show_project_work(!this.show_project_work),
			},
			{
				label: __("Project work as cards"),
				checked: this.project_work_mode() === "cards",
				title: __("Draw each project task, rental and travel day as a card instead of the strip"),
				on_click: () => this.set_show_project_cards(this.project_work_mode() !== "cards"),
			},
		]);
	},

	tb_tools_items() {
		const items = [];
		if (this.data && this.data.can_schedule) {
			items.push({
				label: __("Block time…"),
				title: __("Mark someone unavailable for part of a day or all of it"),
				on_click: () => this.p6d_block_form({ date: this.p6d_default_date() }),
			});
		}
		const count = this.p6d_conflict_count();
		items.push({
			label: __("Conflicts…"),
			hint: count ? String(count) : "",
			title: __("Everything wrong in the dates on screen, with one-click fixes"),
			on_click: () => this.p6d_open_conflicts(null),
		});
		if (this.p6c) {
			items.push({
				label: __("Tap to move"),
				checked: !!this.p6c.tap,
				title: __("Tap a visit, then tap the day where it goes, instead of dragging"),
				on_click: () => this.p6c_set_tap(!this.p6c.tap),
			});
		}
		items.push({
			label: __("Draft mode"),
			checked: !!this.draft_on,
			title: __("Keep your changes to yourself until you publish them"),
			on_click: () => this.set_draft_mode(!this.draft_on),
		});
		items.push(...this.p10_tools_items());
		const last = this.undo_stack[this.undo_stack.length - 1];
		items.push(
			{ divider: true },
			{
				label: __("Undo"),
				hint: __("Ctrl+Z"),
				disabled: !last,
				title: last ? __("Undo the last change to {0}", [last.site]) : __("Nothing to undo"),
				on_click: () => this.undo(),
			}
		);
		return items;
	},

	tb_print_items() {
		return this.p6c_print_items().concat([
			{
				label: __("Day Board"),
				title: __("Today's visits, technician by technician, to print or hand out"),
				on_click: () => frappe.set_route("maintenance-day-board"),
			},
		]);
	},

	// ------------------------------------------------------------------ alert chips

	// The overdue visits the filter shows, in date order.
	tb_overdue_cards() {
		if (!this.data) return [];
		return this.all_cards()
			.filter((card) => card.kind === "visit" && card.overdue && this.visible(card))
			.sort((a, b) => String(a.date || "").localeCompare(String(b.date || "")));
	},

	tb_alerts() {
		if (!this.data) return [];
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
		const overdue = this.tb_overdue_cards().length;
		if (overdue > 0) {
			out.push({
				key: "overdue",
				tone: "danger",
				count: overdue,
				label: __("Overdue"),
				title: __("Drafted visits whose day has passed: list them"),
				open: () => this.tb_open_overdue(),
			});
		}
		return out;
	},

	// The Overdue chip: the overdue visits on screen in a side panel; one opens its visit.
	// The Overdue panel's list, from the visits on screen now.
	tb_overdue_html() {
		const cards = this.tb_overdue_cards();
		if (!cards.length) return `<p class="pk-empty">${mp_esc(__("No overdue visits on screen."))}</p>`;
		const rows = cards.map(
			(card) => `
			<li>
				<button type="button" class="mp-tb-overdue-row" data-tb-overdue="${mp_esc(card.key)}">
					<span class="mp-tb-overdue-site">${mp_esc(card.site || card.project || card.name || "")}</span>
					<span class="mp-tb-overdue-sub">${mp_esc(
						[card.date ? mp_when(card.date) : "", card.technician ? this.tech_name(card.technician) : __("Unassigned")]
							.filter(Boolean)
							.join(" · ")
					)}</span>
				</button>
			</li>`
		);
		return `<ul class="mp-tb-overdue">${rows.join("")}</ul><p class="pk-note">${mp_esc(
			__("Drafted visits whose day has passed. Open one to move it or fill it in.")
		)}</p>`;
	},

	tb_open_overdue() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit) return;
		// Kept, so a reload while it is open (a visit moved on the calendar behind it, or moved and
		// saved from its own panel and the list opened again) redraws it: tb_render.
		this.tb.overdue = kit.drawer.open({
			title: __("Overdue visits"),
			body: this.tb_overdue_html(),
			width: MPTB.overdue_width,
			key: "mp-tb-overdue",
			owner: MP6A.owner,
			push: this.$body[0],
			reopen: () => this.tb_open_overdue(),
			on_click: (e) => {
				const el = e.target && e.target.closest ? e.target.closest("[data-tb-overdue]") : null;
				if (!el) return;
				const card = this.by_key[el.getAttribute("data-tb-overdue")];
				if (card) this.open_card(card);
			},
		});
	},

	tb_render() {
		if (!this.tb || !this.$alerts) return;
		const overdue = this.tb.overdue;
		if (overdue && typeof overdue.is_open === "function" && overdue.is_open()) overdue.set_body(this.tb_overdue_html());
		const alerts = this.tb_alerts();
		const active = document.activeElement;
		const had = active && this.$alerts[0].contains(active) ? active.getAttribute("data-tb-alert") : null;
		this.$alerts.empty();
		alerts.forEach((alert) => {
			const $chip = $('<button type="button" class="mp-tb-alert"></button>')
				.addClass(MPTB.tones[alert.tone] || "")
				.attr("data-tb-alert", alert.key)
				.attr("title", alert.title)
				.on("click", () => alert.open())
				.appendTo(this.$alerts);
			$('<span class="mp-tb-count"></span>').text(String(alert.count)).appendTo($chip);
			$("<span></span>").text(alert.label).appendTo($chip);
		});
		// A class, not toggle(): on a phone the CSS hides the chips in the bar (they are in the sheet).
		this.$alerts.toggleClass("mp-tb-empty", !alerts.length);
		if (had) {
			const again = this.$alerts.find(`[data-tb-alert="${had}"]`)[0];
			if (again) again.focus();
		}
		const total = alerts.reduce((sum, alert) => sum + (Number(alert.count) || 0), 0);
		this.$tb_more_count.text(total ? String(total) : "").toggle(!!total);
		this.$tb_more.attr("aria-label", total ? __("Filters and tools, {0} alerts", [total]) : __("Filters and tools"));
		if (this.tb.tabs) this.tb.tabs.sync(this.view);
		this.p10_sync_myday();
		if (this.tb.sheet && this.tb.sheet.is_open()) this.tb.sheet.refresh();
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
			max_width: MPTB.phone_max,
			return_focus: this.$tb_more[0],
			top: (slot) => {
				this.$filters.addClass("mp-tb-in-sheet").appendTo(slot);
			},
			sections: () => this.tb_sheet_sections(),
			on_close: () => {
				this.$filters.removeClass("mp-tb-in-sheet").insertBefore(this.$alerts);
				this.tb.sheet = null;
			},
		});
	},

	tb_close_sheet() {
		if (this.tb && this.tb.sheet) this.tb.sheet.close("user");
	},

	tb_sheet_sections() {
		const keep = (items) => items.map((item) => Object.assign({}, item, { keep_open: typeof item.checked === "boolean" }));
		const alerts = this.tb_alerts().map((alert) => ({
			label: `${alert.count} ${alert.label}`,
			tone: alert.tone,
			title: alert.title,
			on_click: alert.open,
		}));
		return [
			{ title: __("Alerts"), items: alerts },
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

Object.assign(MaintenancePlanner.prototype, MPTB_METHODS);

// ====================================================================== P4: "Notify crew?"
//
// P4 of the planner UX redesign (docs/planner-ux-redesign.md, D11, Nik 2026-10-21), the Maintenance
// Planner's part: a change to a visit's day, technician or crew asks "Notify crew?" once per gesture
// (a multi-select move asks once for all of them) before move_visit or add_crew (a technician chip
// dropped on a visit) is sent. Notify is the default
// when the change touches today through the 14th day (the old day or the new one), Don't notify beyond
// that; Enter takes the default and Esc puts the change back. The answer goes as notify=1 or 0, the
// Undo entry keeps it, and Undo sends the same answer again. Nothing is asked in Draft mode (a draft
// tells nobody until it is published) or for a visit nobody is on.
//
// Hooks into the class above, one line each: mpas_with_notify (send), mpas_batch (p6b_run_moves,
// whose moves then carry the answer), and the Undo entries' `notify` (undo, p6b_undo).

const MPAS = {
	notify_days: 14,
	// The live writes that change who is on a visit, or when: both take `notify`.
	notify_methods: ["move_visit", "add_crew"],
};

// Pure helpers, an identical copy of the Project Planner's PPAS_PURE.notify_window/notify_default
// (scripts/test_planner_autoshift.js runs both through the same cases).
const MPAS_PURE = {
	notify_window(today) {
		return [today, MP6B_PURE.ymd_add(today, MPAS.notify_days - 1)];
	},
	notify_default(spans, today) {
		const [first, last] = MPAS_PURE.notify_window(today);
		return (spans || []).some((span) => !!(span && span[0] && span[0] <= last && (span[1] || span[0]) >= first));
	},
	// The users a move_visit leaves on the visit and the ones it had: who a notice could reach.
	// `before` is a visit card or its Undo snapshot: {technician, crew: [{user}]}.
	people(before, args) {
		const people = new Set();
		if (before) {
			if (before.technician) people.add(before.technician);
			(before.crew || []).forEach((row) => row && row.user && people.add(row.user));
		}
		if (args) {
			if (args.technician) people.add(args.technician);
			if (args.user) people.add(args.user); // add_crew's person
			let rows = args.crew;
			if (typeof rows === "string") {
				try {
					rows = JSON.parse(rows);
				} catch (e) {
					rows = [];
				}
			}
			(rows || []).forEach((row) => {
				const user = row && typeof row === "object" ? row.user : row;
				if (user) people.add(user);
			});
		}
		return people;
	},
	// Whether a move_visit or add_crew changes what a notice is about: the day, the technician or the
	// crew.
	changes(args) {
		return !!(
			args &&
			(args.date || args.technician !== undefined || args.crew !== undefined || args.from_user || args.user)
		);
	},
};

const MPAS_METHODS = {
	// send()'s hook: a live move_visit with no answer yet gets one first. Resolves to send()'s answer;
	// null when the planner backed out. Returns null (nothing to ask) to let send() carry on.
	mpas_with_notify(method, args, opts) {
		if (!MPAS.notify_methods.includes(method) || args.draft || args.notify !== undefined || !MPAS_PURE.changes(args)) return null;
		const card = this.by_key && this.by_key[args.record];
		const before = opts.snapshot && opts.snapshot.kind === "visit" ? opts.snapshot : card ? this.snapshot_of(card) : null;
		const people = MPAS_PURE.people(before, args);
		if (!people.size) return null;
		const old_day = (before && before.date) || (card && card.date) || null;
		const spans = [old_day, args.date || old_day].filter(Boolean).map((day) => [day, day]);
		return this.mpas_ask(MPAS_PURE.notify_default(spans, this.today()), [...people], 1).then((notify) => {
			if (notify === null) return null;
			if (opts.snapshot) opts.snapshot.notify = notify;
			return this.send(method, Object.assign({}, args, { notify }), opts);
		});
	},

	// A multi-select move (p6b_run_moves): one question for every visit in it, asked before the first
	// is sent, then `go(answer)`. With nobody to tell (or Draft mode) `go(undefined)` runs at once;
	// backing out of the question runs nothing and resolves null.
	mpas_batch(plans, go) {
		const people = new Set();
		const spans = [];
		(plans || []).forEach((plan) => {
			MPAS_PURE.people(plan.card, null).forEach((user) => people.add(user));
			if (plan.card.date) spans.push([plan.card.date, plan.card.date]);
			if (plan.date) spans.push([plan.date, plan.date]);
		});
		if (this.draft_on || !people.size) return go(undefined);
		return this.mpas_ask(MPAS_PURE.notify_default(spans, this.today()), [...people], (plans || []).length).then(
			(notify) => (notify === null ? null : go(notify))
		);
	},

	mpas_ask(notify, people, count) {
		return new Promise((resolve) => {
			let settled = false;
			const settle = (value) => {
				if (settled) return;
				settled = true;
				resolve(value);
			};
			const names = people.slice(0, 6).map((user) => this.tech_name(user));
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
						options: `<p>${mp_esc(ask)}</p><p class="text-muted">${mp_esc(why)} ${mp_esc(__("Esc puts the change back."))}</p>`,
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
};

Object.assign(MaintenancePlanner.prototype, MPAS_METHODS);

// ====================================================================== Simple mode
//
//   /desk/maintenance-planner/simple     every maintenance site, a card each: Done and Coming up
//
// docs/planner-simple-mode.md (Nik, 2026-10-22; S5 "Both planners", S12 "One row per site", S14 "Visits + old
// service tasks"). A switch, first in the bar; turned on, it opens /simple, a screen of its own, so Back returns
// to where the person was. The screen is the kit's (planner_kit/simple.js, the Project Planner's too), drawn
// from api/planner_simple.get_simple_sites: a card per site (a contract's project), its Maintenance Records,
// its contract's projected visits and its project's completed tasks. It writes nothing, and the planner's
// shortcuts do nothing on it (p6b_run_key).
//
// Remembered per device (S7). This planner keeps its last view per person (6C's "last used"); Simple mode is
// the device's instead, as the Project Planner's tabs are: a flag in this browser for the signed-in user, set
// when /simple opens and cleared when any other screen does. A bare /maintenance-planner on a device whose
// flag is set opens Simple mode, in place (replace), so Back leaves the planner as it would have. Turning it off
// clears the flag and opens the bare address, which lands on the person's own last view as any visit does.
//
// Hooks into the class above, one line each: init_simple (constructor), route_target, mps_take (handle_route),
// mps_landing (p6c_before_route), mps_refresh (load), mps_kit_ready (p6a_ready), p6b_run_key.

const MPS = {
	view: "simple",
	method: "erpnext_enhancements.api.planner_simple.get_simple_sites",
	// A read: sent as a GET.
	http: "GET",
	// This device's "Simple mode was the last screen", per user.
	device_pref: "ee_maintenance_planner_simple",
};

const MPS_METHODS = {
	init_simple() {
		this.mps = { view: null };
		this.$simple = $('<div class="mp-simple"></div>').hide().appendTo(this.$body);
		// The switch, first in the bar. It is off wherever the bar shows; Simple mode draws its own, on.
		this.$simple_switch = $('<button type="button" class="btn btn-default btn-sm mp-simple-switch" role="switch" aria-checked="false"></button>')
			.text(__("Simple"))
			.attr("title", __("Simple mode: every site on one quiet page, visits done and coming up"))
			.on("click", () => this.mps_enter())
			.prependTo(this.$body.children(".mp-toolbar").first());
	},

	mps_key() {
		return `${MPS.device_pref}:${(frappe.session && frappe.session.user) || ""}`;
	},

	// Every landing: Simple mode shows (true, nothing else to do) or goes, and this device remembers which. The
	// flag is written only when it changes, so a device that never used Simple mode stores nothing.
	mps_take(view) {
		const simple = view === MPS.view;
		const was = this.load_pref(this.mps_key(), "0") === "1";
		if (simple !== was) this.save_pref(this.mps_key(), simple ? "1" : "0");
		if (!simple) {
			this.mps_hide();
			return false;
		}
		if (!this.mps) this.init_simple();
		this.$body.addClass("mp-simple-on");
		this.$simple.show();
		this.mps_mount();
		this.mps_refresh();
		return true;
	},

	mps_hide() {
		if (this.$body) this.$body.removeClass("mp-simple-on");
		if (this.$simple) this.$simple.hide();
	},

	// A bare address on a device that used Simple mode last: Simple mode, in place of the bare entry. A link that
	// names a technician is a calendar's, so it never lands here.
	mps_landing() {
		if (this.load_pref(this.mps_key(), "0") !== "1") return false;
		const opts = frappe.route_options || {};
		if (MP.url_filters.some((key) => Object.prototype.hasOwnProperty.call(opts, key))) return false;
		frappe.route_flags = frappe.route_flags || {};
		frappe.route_flags.replace_route = true;
		frappe.set_route(MP.route, MPS.view);
		return true;
	},

	// The switch, on: a screen, so it is a history entry. The technician filter rides along as on every screen.
	mps_enter() {
		const route = frappe.get_route() || [];
		if (route[0] === MP.route && route[1] === MPS.view) return;
		this.self_route = [MP.route, MPS.view].join("/");
		this.p6a_route(() => frappe.set_route(MP.route, MPS.view, this.filter_options()), false);
	},

	// The switch, off: the bare address, which lands on the person's own last view (6C), one entry after Simple.
	mps_leave() {
		this.save_pref(this.mps_key(), "0");
		this.p6a_route(() => frappe.set_route(MP.route), false);
	},

	mps_refresh() {
		if (this.mps && this.mps.view) return this.mps.view.refresh();
		return null;
	},

	mps_mount() {
		if (this.mps.view) return;
		const kit = this.p6a && this.p6a.kit;
		if (!kit) {
			this.$simple.empty().append($('<div class="mp-simple-note"></div>').text(__("Loading…")));
			return;
		}
		if (!kit.simple) {
			// An older kit already on the Desk: a reload brings the new one.
			this.$simple.empty().append($('<div class="mp-simple-note"></div>').text(__("Reload the page to use Simple mode.")));
			return;
		}
		this.$simple.empty();
		this.mps.view = kit.simple.mount(this.$simple[0], {
			title: __("Maintenance Planner"),
			placeholder: __("Search sites and customers…"),
			noun: [__("site"), __("sites")],
			load: (search, start, limit) => kit.quiet_call(MPS.method, { search, start, limit: limit || "" }, { method: MPS.http }),
			open: (doctype, name) => frappe.set_route("Form", doctype, name),
			off: () => this.mps_leave(),
			help: () => kit.legend(kit.simple.legend_sections("sites"), { title: __("How Simple mode works"), owner: MP6A.owner }),
			is_phone: () => this.tour_is_phone(),
		});
	},

	// The kit loads after the first landing: a Simple screen already showing draws once it is here.
	mps_kit_ready() {
		if (!this.mps || this.view !== MPS.view || this.mps.view) return;
		this.mps_mount();
		this.mps_refresh();
	},
};

Object.assign(MaintenancePlanner.prototype, MPS_METHODS);

// ====================================================================== P10: Maintenance Planner simplify
//
// P10 of the planner UX redesign (docs/planner-ux-redesign.md section 4.9, D9 "Keep as is, simplify"
// and D13 "Busy strip per day"; Nik, 2026-10-22: "Start"). The same Month, Week and Crew views, the
// same cards, drags, projected and seasonal visits, draft mode and pencil; what changes is how much
// the page puts in front of the person:
//
//   - Project work is a busy strip, not a card each. Each technician-day with project, rental or
//     travel bookings gets ONE thin grey bar ("Project: 8h"; "Rental" or "Travel" when that is all
//     there is; "+ 2h pencil" for soft hours). Hover for the tasks; a click opens that person's week
//     (6A's person quick look, the day selected). With All technicians the strip names the person.
//     More > Display > "Project work as cards" brings back the old read-only card each (booking_html),
//     More > Display > "Project work" still turns the whole thing off. Free hours do not change: they
//     are the engine's, which counts the work whether it is drawn as a strip, as cards or not at all.
//     A Fountain Move task is not a booking here (the server drops it from `items`): its card is
//     unchanged by any of this.
//   - "Resources available" starts collapsed to one line for the week ("Austin: 3h free Tue · Off
//     Thu–Fri"); Show opens the full table exactly as it was. The names in the line are the table's
//     own chips, so a click still opens a person's week and a drag onto a visit still adds them.
//   - The key of the visit kinds lives in "?" only (legend_sections, in 6A's block), with the strip
//     and Resources available beside it (p10_legend_sections). Display no longer carries it.
//   - "My day" takes a technician to their Today screen on the Project Planner (P8):
//     /project-planner/today/<date>. In the bar for someone who cannot schedule (a technician), and in
//     More > Tools for anyone who has a day of their own (a Planner Resource on the technician list).
//
// Saved views: `project_cards` (Project work as cards) and `resources` (Resources available open) join
// the view; `resources` replaces `panel`, which the old default (open) filled in for every user, so an
// old "last used" row cannot hold the panel open against the new default (api/planner_views_prefs).
//
// History: nothing here is a screen. The strip's click opens the kit's drawer (its one entry); My day
// is a set_route, one entry, and Back returns to the planner. Showing, hiding or switching cards pushes
// nothing.
//
// Hooks into the class above, one line each: constructor (the two prefs), visible and render_crew
// (project_work_mode), render_calendar (p10_day_strips_html), render_crew (p10_cell_strip_html),
// render_panel (p10_summary_line), render (p10_hint_note), p6c_print_calendar and p6c_print_crew
// (p10_print_items), legend_sections (p10_legend_sections), tb_tools_items (p10_tools_items), tb_render
// (p10_sync_myday), init_toolbar (init_p10) and p6e_dblclick (the strip is no empty spot).

const MP10 = {
	// The Project Planner's Today screen (P8): /desk/project-planner/today/<date>.
	today_view: "today",
	// What a view saved before P10 does not say: compared only when the saved view holds them.
	newer_view_keys: ["project_cards", "resources"],
	// How many separate "Nh free" stretches the summary line names for one person before "+N more".
	summary_runs: 3,
	// Saturday and Sunday are left out of "Off ..." (off for nearly everyone; the table still shows them).
	weekend_days: [0, 6],
};

const MP10_PURE = {
	// The engine's bookings of one technician-day (`items`) as one figure: the hours that are firm and the
	// hours that are pencilled, how many there are, and the kinds that are in it (project task, rental,
	// travel), in that order whatever order the engine listed them.
	busy(items) {
		let firm = 0;
		let soft = 0;
		let count = 0;
		const seen = [];
		(items || []).forEach((item) => {
			if (!item) return;
			const hours = Math.max(0, Number(item.hours) || 0);
			if (item.tentative) soft += hours;
			else firm += hours;
			const kind = item.kind in MP.booking_kinds ? item.kind : "task";
			if (!seen.includes(kind)) seen.push(kind);
			count += 1;
		});
		const kinds = Object.keys(MP.booking_kinds).filter((kind) => seen.includes(kind));
		const round = (value) => Math.round(value * 100) / 100;
		return { firm: round(firm), soft: round(soft), count, kinds, kind: kinds.length === 1 ? kinds[0] : "task" };
	},

	// Indexes as runs of consecutive ones: [0, 1, 3] -> [[0, 1], [3]].
	runs(indexes) {
		const out = [];
		(indexes || []).forEach((index) => {
			const last = out[out.length - 1];
			if (last && index === last[last.length - 1] + 1) last.push(index);
			else out.push([index]);
		});
		return out;
	},

	// Days of a window as people say them: "Tue", "Thu–Fri", "Mon–Wed", "Sun, Tue".
	range_text(indexes, names) {
		return MP10_PURE.runs(indexes)
			.map((run) => (run.length === 1 ? names[run[0]] : `${names[run[0]]}–${names[run[run.length - 1]]}`))
			.join(", ");
	},

	// One technician's days (`{name, weekend, capacity, booked, free}`, or null for a day the engine sent
	// nothing for) as the summary line says them: the days with hours free, a fully booked week, the days
	// over, work booked on a day off, then the days off. Weekends are left out of the days off.
	summary(days) {
		const free = {};
		const over = {};
		const booked_off = [];
		const off = [];
		let working = 0;
		const note = (bucket, hours, index) => {
			const key = mp_hours(hours);
			(bucket[key] = bucket[key] || []).push(index);
		};
		(days || []).forEach((day, index) => {
			if (!day) return;
			const capacity = Number(day.capacity) || 0;
			const booked = Number(day.booked) || 0;
			if (capacity <= 0) {
				if (booked > 0.01) booked_off.push(index);
				else if (!day.weekend) off.push(index);
				return;
			}
			working += 1;
			if (booked - capacity > 0.01) {
				note(over, booked - capacity, index);
				return;
			}
			const spare = Math.max(0, Number(day.free != null ? day.free : capacity - booked));
			if (spare > 0.01) note(free, spare, index);
		});
		const names = (days || []).map((day) => (day ? day.name : ""));
		const stretches = (bucket, label) =>
			Object.keys(bucket)
				.map((hours) => ({ first: bucket[hours][0], text: label(hours, MP10_PURE.range_text(bucket[hours], names)) }))
				.sort((a, b) => a.first - b.first)
				.map((entry) => entry.text);
		let parts = stretches(free, (hours, when) => __("{0}h free {1}", [hours, when]));
		if (parts.length > MP10.summary_runs) {
			const hidden = parts.length - MP10.summary_runs;
			parts = parts.slice(0, MP10.summary_runs).concat([__("+{0} more", [hidden])]);
		}
		const over_parts = stretches(over, (hours, when) => __("Over {0}h {1}", [hours, when]));
		if (!parts.length && !over_parts.length && working) parts.push(__("Fully booked"));
		parts = parts.concat(over_parts);
		if (booked_off.length) parts.push(__("Booked on a day off {0}", [MP10_PURE.range_text(booked_off, names)]));
		if (off.length) parts.push(__("Off {0}", [MP10_PURE.range_text(off, names)]));
		return { text: parts.join(" · "), danger: over_parts.length > 0 || booked_off.length > 0 };
	},
};

const MP10_METHODS = {
	// Listens for a click on a busy strip (and Enter or Space on one with the keyboard). A strip is a
	// div, not a button, so Tap to move can still drop a visit on the day when a finger lands on it.
	init_p10() {
		const root = this.$body && this.$body[0];
		if (!root) return;
		root.addEventListener("click", (e) => {
			if (Date.now() < this.click_blocked_until) return;
			const el = e.target && e.target.closest ? e.target.closest(".mp-busy") : null;
			if (el) this.p10_open_busy(el);
		});
		root.addEventListener("keydown", (e) => {
			if (e.key !== "Enter" && e.key !== " ") return;
			const el = e.target && e.target.closest ? e.target.closest(".mp-busy") : null;
			if (!el) return;
			e.preventDefault();
			this.p10_open_busy(el);
		});
	},

	// ------------------------------------------------------------------ the busy strip

	// The people with project work on `ymd` that the technician filter lets through, in the technician
	// list's order. Unassigned shows none: project work belongs to a person.
	p10_busy_users(ymd) {
		if (this.technician === "__none__") return [];
		const bookings = (this.data && this.data.bookings) || {};
		let users = Object.keys(bookings).filter((user) => {
			const cell = (bookings[user] || {})[ymd];
			return !!(cell && (cell.items || []).length);
		});
		if (this.technician) users = users.filter((user) => user === this.technician);
		const order = ((this.data && this.data.technicians) || []).map((person) => person.user);
		const rank = (user) => (order.includes(user) ? order.indexOf(user) : order.length);
		return users.sort((a, b) => rank(a) - rank(b) || String(this.tech_name(a)).localeCompare(String(this.tech_name(b))));
	},

	// One technician-day's strip: its figure, its words, and the tooltip that lists the tasks. null when
	// the engine sent no bookings for it.
	p10_busy(user, ymd) {
		const cell = this.cell_of(user, ymd);
		const items = (cell && cell.items) || [];
		if (!items.length) return null;
		const busy = MP10_PURE.busy(items);
		const text = this.p10_busy_text(busy);
		return {
			user,
			ymd,
			items,
			busy,
			text,
			tip: this.p10_busy_tip(user, ymd, items, text),
			aria: __("{0}: {1}. Open their week.", [this.tech_name(user), text]),
		};
	},

	// "Project: 8h", "Rental: 8h", "Travel: 3h"; pencilled hours after the firm ones.
	// What the strip calls the work: Project, Rental or Travel when that is all there is; Project when a project
	// task is among it (rental crew tasks and travel are part of that job's work); else the kinds that are
	// there, "Rental + Travel". Never "Project" for a day that holds no project task.
	p10_busy_label(busy) {
		const names = busy.kinds.map((kind) => __(MP.booking_kinds[kind]));
		if (busy.kinds.length === 1) return names[0];
		return busy.kinds.includes("task") ? __(MP.booking_kinds.task) : names.join(" + ");
	},

	// "Project: 8h", "Rental: 8h", "Rental + Travel: 8h"; pencilled hours after the firm ones. A day the engine
	// books no hours for (the weekend of a trip) is just what it is: "Travel", or "Travel: 2 items".
	p10_busy_text(busy) {
		const label = this.p10_busy_label(busy);
		const hours = (value) => `${mp_hours(value)}h`;
		if (busy.firm > 0 && busy.soft > 0) return __("{0}: {1} + {2} pencil", [label, hours(busy.firm), hours(busy.soft)]);
		if (busy.firm > 0) return __("{0}: {1}", [label, hours(busy.firm)]);
		if (busy.soft > 0) return __("{0}: {1} pencil", [label, hours(busy.soft)]);
		const named = busy.kinds.map((kind) => __(MP.booking_kinds[kind])).join(" + ");
		return busy.count > 1 ? __("{0}: {1} items", [named, busy.count]) : named;
	},

	p10_busy_tip(user, ymd, items, text) {
		const lines = [`${this.tech_name(user)} · ${mp_when(ymd)}`, text];
		items.forEach((item) => {
			const kind = item.kind in MP.booking_kinds ? item.kind : "task";
			const length = item.slot ? item.slot.join("–") : `${this.fmt_hours(item.hours)}h`;
			const bits = [__(MP.booking_kinds[kind]), length];
			if (item.project) bits.push(item.project);
			if (item.tentative) bits.push(__("pencil"));
			lines.push(`• ${item.label || item.ref} (${bits.join(", ")})`);
		});
		lines.push(__("Read-only here. Click for their week; move the work on the Project Planner."));
		return lines.join("\n");
	},

	p10_first_name(user) {
		return String(this.tech_name(user) || "").split(/\s+/)[0];
	},

	// The strip as markup. `data-pk-persons` lets the kit's hover glow light it with the person's name.
	// With All technicians it names the person (a picked technician's column already says who).
	p10_busy_html(entry, with_name) {
		const pencil = entry.busy.firm <= 0 && entry.busy.soft > 0 ? " mp-busy-pencil" : "";
		const name = with_name ? `<span class="mp-busy-name">${mp_esc(this.p10_first_name(entry.user))}</span>` : "";
		return (
			`<div class="mp-busy${pencil}" role="button" tabindex="0" data-busy-user="${mp_esc(entry.user)}" ` +
			`data-busy-date="${mp_esc(entry.ymd)}" data-pk-persons="${mp_esc(encodeURIComponent(String(entry.user)))}" ` +
			`title="${mp_esc(entry.tip)}" aria-label="${mp_esc(entry.aria)}">${name}` +
			`<span class="mp-busy-text">${mp_esc(entry.text)}</span></div>`
		);
	},

	// The strips of one day in the month and the week: one a technician with project work. Nothing unless
	// the strip is how project work is drawn.
	p10_day_strips_html(ymd) {
		if (this.project_work_mode() !== "strip") return "";
		const with_name = !this.technician;
		return this.p10_busy_users(ymd)
			.map((user) => this.p10_busy(user, ymd))
			.filter(Boolean)
			.map((entry) => this.p10_busy_html(entry, with_name))
			.join("");
	},

	// Whether this person's day has a strip. A disabled technician's crew row is there only while they have
	// something on the week, and in strip mode their project work is no card in the cell, so the row asks.
	p10_has_strip(user, ymd) {
		if (this.project_work_mode() !== "strip" || !user) return false;
		const cell = this.cell_of(user, ymd);
		return !!(cell && (cell.items || []).length);
	},

	// A crew-view cell is one person's day: the strip needs no name there.
	p10_cell_strip_html(user, ymd) {
		if (this.project_work_mode() !== "strip" || !user) return "";
		const entry = this.p10_busy(user, ymd);
		return entry ? this.p10_busy_html(entry, false) : "";
	},

	// What the printed page draws for the strips: the calendar's day (`row_user` null: everyone the filter
	// lets through) or the crew sheet's cell (that person).
	p10_print_items(ymd, row_user) {
		if (this.project_work_mode() !== "strip") return [];
		const users = row_user === null || row_user === undefined ? this.p10_busy_users(ymd) : [row_user];
		return users
			.map((user) => this.p10_busy(user, ymd))
			.filter(Boolean)
			.map((entry) => ({
				title: entry.text,
				sub: row_user === null || row_user === undefined ? (this.technician ? "" : this.tech_name(entry.user)) : "",
				color: mp_token_color("--sf-ink-500", "#5c6066"),
				dashed: false,
				marks: [],
				// A site's highlight fades the cards on screen and not the strip (it is no card of that site), so
				// the printed page does the same.
				faded: false,
				highlight: false,
			}));
	},

	// A click on a strip: that person's week with the day selected (6A's quick look). Before the kit has
	// loaded, a day with a single task opens the task itself; a longer list waits for the kit.
	p10_open_busy(el) {
		const user = el && el.getAttribute ? el.getAttribute("data-busy-user") : "";
		const ymd = el && el.getAttribute ? el.getAttribute("data-busy-date") : "";
		if (!user) return;
		if (this.p6a && this.p6a.kit) {
			this.p6a_open_person(user, ymd || null);
			return;
		}
		const cell = this.cell_of(user, ymd);
		const items = (cell && cell.items) || [];
		if (items.length === 1) this.open_card({ kind: "booking", booking_kind: items[0].kind, ref: items[0].ref });
	},

	// One sentence under the grid about how project work is drawn now.
	p10_hint_note() {
		const mode = this.project_work_mode();
		if (mode === "strip") {
			return ` ${__(
				"A thin grey strip is the same people's project work that day: hover for the tasks, click for their week. It is moved on the Project Planner."
			)}`;
		}
		if (mode === "cards") {
			return ` ${__(
				"Cards marked Project, Rental and Travel are bookings of the same people: they open the task but are moved on the Project Planner."
			)}`;
		}
		return "";
	},

	// ------------------------------------------------------------------ Resources available, collapsed

	// The week the summary speaks of: the week on screen, or in the month view the week holding today.
	// Days already gone are left out of a week that is still going; a past week is shown whole.
	p10_summary_window() {
		const start = moment(this.p6a_week_start(), "YYYY-MM-DD");
		const days = [];
		for (let i = 0; i < 7; i++) days.push(mp_ymd(start.clone().add(i, "days")));
		const today = this.today();
		const upcoming = days.filter((ymd) => ymd >= today);
		const label = days[0] <= today && today <= days[6] ? __("This week") : __("Week of {0}", [start.format("MMM D")]);
		return { days: upcoming.length ? upcoming : days, label };
	},

	// Who the line speaks of: everyone on the list, or the one technician the filter picks. A person the
	// engine sent no days for has no hours on the planner and nothing honest to say.
	p10_summary_items(people) {
		const win = this.p10_summary_window();
		const only = this.technician && this.technician !== "__none__" ? this.technician : "";
		const names = win.days.map((ymd) => moment(ymd, "YYYY-MM-DD").format("ddd"));
		const items = [];
		people.forEach((person) => {
			if (only && person.user !== only) return;
			const days = win.days.map((ymd, index) => {
				const cell = this.cell_of(person.user, ymd);
				if (!cell) return null;
				const weekday = moment(ymd, "YYYY-MM-DD").day();
				return {
					name: names[index],
					weekend: MP10.weekend_days.includes(weekday),
					capacity: cell.capacity,
					booked: cell.booked,
					free: cell.free,
				};
			});
			if (!days.some(Boolean)) return;
			const said = MP10_PURE.summary(days);
			if (said.text) items.push({ person, text: said.text, danger: said.danger });
		});
		return { label: win.label, items };
	},

	// The closed panel's one line, inside the heading. Returns whether there was anything to say.
	p10_summary_line($head, people) {
		const { label, items } = this.p10_summary_items(people);
		if (!items.length) return false;
		const html = items
			.map(
				(item) =>
					`<span class="mp-res-sum-item${item.danger ? " mp-res-sum-danger" : ""}">${this.person_html(item.person, false)}` +
					`<span class="mp-res-sum-text">${mp_esc(item.text)}</span></span>`
			)
			.join("");
		const full = items.map((item) => `${item.person.name}: ${item.text}`).join("\n");
		$('<span class="mp-res-sum"></span>')
			.attr("title", `${label}\n${full}`)
			.html(`<span class="mp-res-sum-lead">${mp_esc(label)}</span>${html}`)
			.appendTo($head);
		return true;
	},

	// ------------------------------------------------------------------ My day

	// Whether the signed-in person has a day of their own on the Project Planner's Today screen: they are
	// a technician on this planner's list with a Planner Resource. Anyone else would only get its note.
	p10_has_day() {
		const me = (this.tech_by_user || {})[(frappe.session && frappe.session.user) || ""];
		return !!(me && me.resource && me.enabled !== false);
	},

	// One entry in the history; Back returns to the planner.
	p10_open_today() {
		if (!this.p10_has_day()) return;
		const day = this.today();
		this.p6a_route(() => frappe.set_route(MP.project_route, MP10.today_view, day), false);
	},

	p10_tools_items() {
		if (!this.p10_has_day()) return [];
		return [
			{
				label: __("My day"),
				title: __("Your stops and route for today and tomorrow (the Today screen on the Project Planner)"),
				on_click: () => this.p10_open_today(),
			},
		];
	},

	// The bar's own link is for a technician, who cannot schedule and has little else to do here.
	p10_sync_myday() {
		if (!this.$myday) return;
		this.$myday.toggle(!!(this.data && !this.data.can_schedule && this.p10_has_day()));
	},

	// ------------------------------------------------------------------ the "?" help

	// Sections for "?" (legend_sections calls this one): the project-work strip, Resources available and,
	// for someone who has one, My day. Providers other phases register (p6_legend_providers) come after.
	p10_legend_sections() {
		const item = (sample_html, text) => ({ sample_html, text: __(text) });
		const card = (cls, label, text) =>
			item(`<span class="mp-card mp-p6a-sample-card${cls ? ` ${cls}` : ""}">${mp_esc(__(label))}</span>`, text);
		const sections = [
			{
				title: __("Project work"),
				items: [
					item(
						`<span class="mp-busy mp-p10-sample">${mp_esc(__("Project: 8h"))}</span>`,
						"The project, rental and travel work of the same people is one thin grey strip on their day: the hours it books, the tasks when you hover. Click it for their week. It is read-only here; move it on the Project Planner. Free hours count it however it is drawn."
					),
					item(
						"",
						"More ▸ Display ▸ Project work turns the strips off. Project work as cards draws each task as a card instead:"
					),
					card("mp-booking mp-bk-task", "Project", "A project task of the same person (read-only here; moved on the Project Planner)."),
					card("mp-booking mp-bk-rental", "Rental", "A rental crew task (read-only here)."),
					card("mp-booking mp-bk-travel", "Travel", "A travel day (read-only here)."),
				],
			},
			{
				title: __("Resources available"),
				items: [
					item(
						"",
						"Closed, it is one line for the week: who has hours free which days, and who is off (Austin: 3h free Tue · Off Thu–Fri). Show opens each technician's free hours day by day."
					),
					item("", "Drag a name from the line or the table onto a visit to add them to its crew. Click a name to see their week."),
				],
			},
		];
		if (this.p10_has_day()) {
			sections.push({
				title: __("Your day"),
				items: [
					item(
						"",
						"My day (in the bar, and in More ▸ Tools) opens Today on the Project Planner: your stops and route for today and tomorrow."
					),
				],
			});
		}
		return sections;
	},
};

Object.assign(MaintenancePlanner.prototype, MP10_METHODS);

// ====================================================================== P12: the first-visit tour, and "?" for the views
//
// P12 of the planner UX redesign (docs/planner-ux-redesign.md section 7; Nik, 2026-10-21: "a brief and
// simple tutorial on the planner for every first time visitor"). This planner's half of what the Project
// Planner's PPTOUR block does (its comment says how it works, and why each rule is so); the differences:
//
//   - **The steps.** A desktop's: Month, Week and Crew; dragging a visit (schedulers); the project-work
//     strip; Resources available; who hears about a change, Notify crew? and Draft mode (schedulers);
//     My day (a technician who has a day of their own); and "?". A phone's: your visits, the strip, My
//     day, Filters and tools, and "?". Four to six steps either way.
//   - **When.** On any of the three views, once the planner's data is drawn, the kit and the toolbar are
//     built and the saved views (which carry the flags: planner_views_prefs.get_views) have answered; a
//     phone's too, since this planner has no screen of its own for a phone.
//   - **"?"** gets the views, moving a visit, and Draft mode with Notify crew? (p12_legend_sections),
//     right after the tips; P10's legend_sections() calls it. The help drawer's footer has Take the tour.
//
// Hooks into the class above, one line each: init_tour (constructor), tour_route (handle_route), tour_take
// (p6c_fetch_views), tour_open (p6b_key's dialog test), p12_legend_sections and tour_legend_actions
// (legend_sections, p6a_legend).

const MPTOUR = {
	prefs: "erpnext_enhancements.api.planner_views_prefs",
	planner: "maintenance",
	kinds: ["desktop", "phone"],
	local_pref: "ee_maintenance_planner_tour",
	// The three views; every one is a screen the tour may open on.
	screens: ["month", "week", "crew"],
	settle_ms: 700,
	retry_ms: 1000,
	retries: 60,
};

// Pure: no frappe, no page. scripts/test_planner_tour.js runs it under node.
const MPTOUR_PURE = {
	kind(is_phone) {
		return is_phone ? "phone" : "desktop";
	},
	auto_screen(view) {
		return MPTOUR.screens.includes(view);
	},
	// The steps for `kind`, written out as __() calls so they are translated. `who`: {can_schedule, has_day}.
	steps(kind, who) {
		const w = who || {};
		const steps = [];
		if (kind === "phone") {
			steps.push(
				{
					title: __("Your visits"),
					text: __("The calendar shows visits by day. Tap one to open it. Month, Week and Crew along the top change the view."),
				},
				{
					title: __("Project work is a strip"),
					text: __("A thin grey strip on a day is project work for the same people. Tap it for their week. It is moved on the Project Planner."),
				}
			);
			if (w.has_day) {
				steps.push({
					title: __("My day"),
					text: __("My day opens Today: your stops and the route for today and tomorrow."),
				});
			}
			steps.push(
				{
					title: __("Filters and tools"),
					text: __("Filters and tools opens a sheet from the bottom: the technician filter, alerts, + Add and More."),
				},
				{
					title: __("Help is one tap away"),
					text: __("In Filters and tools, How to read the planner explains the cards and colors, and has Take the tour to see this again. Simple, at the start of the bar, shows every site on one page."),
				}
			);
			return steps;
		}
		steps.push({
			title: __("Month, Week and Crew"),
			text: __("The tabs along the top show the same visits three ways. Month is the big picture, Week is for planning, and Crew gives each technician a row."),
		});
		if (w.can_schedule) {
			steps.push({
				title: __("Drag to reschedule"),
				text: __("Drag a visit to another day, or onto another technician's row to hand it over. Double-click an empty spot, or use + Add, to draft a new visit."),
				keys: __("Ctrl+Z (⌘Z on a Mac) undoes the last change."),
			});
		}
		steps.push(
			{
				title: __("Project work is a thin strip"),
				text: __("A grey strip on a day is that technician's project work. Hover for the tasks, click for their week. It is read-only here: move it on the Project Planner."),
			},
			{
				title: __("Resources available"),
				text: w.can_schedule
					? __("Closed, it is one line for the week: who has hours free and who is off. Show opens the full table. Click a name for their week, or drag it onto a visit to add them to its crew.")
					: __("Closed, it is one line for the week: who has hours free and who is off. Show opens the full table. Click a name to see their week."),
			}
		);
		if (w.can_schedule) {
			steps.push({
				title: __("Who hears about a change"),
				text: __("A change to a visit asks Notify crew?, which starts on Notify for the next two weeks. To rehearse a reshuffle, turn on Draft mode in More, then Publish when it looks right."),
				keys: __("Esc puts a change back."),
			});
		} else if (w.has_day) {
			steps.push({
				title: __("My day"),
				text: __("My day, in the bar, opens Today: your stops and the route for today and tomorrow."),
			});
		}
		steps.push({
			title: __("Help is one click away"),
			text: __("? explains the cards, chips and colors, and has Take the tour to see this again. More holds Display, Views, Tools and Print. Simple, at the start of the bar, shows every site on one quiet page."),
		});
		return steps;
	},
};

const MPTOUR_METHODS = {
	init_tour() {
		this.tour = { seen: null, timer: null, handle: null };
		const wrapper = this.page && this.page.wrapper;
		if (wrapper && typeof wrapper.on === "function") {
			wrapper.on("hide", (e) => {
				if (e.target === wrapper[0]) this.tour_close("route");
			});
		}
	},

	tour_state() {
		if (!this.tour) this.tour = { seen: null, timer: null, handle: null };
		return this.tour;
	},

	// True while the tour is on screen: the page's shortcut handlers (p6b_key) treat it as a dialog.
	tour_open() {
		const kit = this.p6a && this.p6a.kit;
		return !!(kit && kit.tour && typeof kit.tour.is_open === "function" && kit.tour.is_open());
	},

	tour_is_phone() {
		return !!(window.matchMedia && window.matchMedia(`(max-width: ${MPTB.phone_max}px)`).matches);
	},

	tour_kind() {
		return MPTOUR_PURE.kind(this.tour_is_phone());
	},

	tour_close(how) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !kit.tour) return;
		const state = this.tour_state();
		if (state.handle) {
			try {
				state.handle.close(how || "api");
			} catch (e) {
				state.handle = null;
			}
		}
	},

	tour_take(answer) {
		try {
			if (answer && typeof answer === "object") {
				this.tour_state().seen = { desktop: answer.desktop === true, phone: answer.phone === true };
			}
			this.tour_nudge();
		} catch (e) {
			// A tour is never a reason for the planner to fail.
		}
	},

	tour_route() {
		try {
			this.tour_close("route");
			this.tour_nudge();
		} catch (e) {
			// As above.
		}
	},

	tour_nudge(wait, tries) {
		try {
			const state = this.tour_state();
			if (state.timer) clearTimeout(state.timer);
			state.timer = setTimeout(() => this.tour_check(tries || 0), wait == null ? MPTOUR.settle_ms : wait);
		} catch (e) {
			// No timer, no tour.
		}
	},

	tour_check(tries) {
		const state = this.tour_state();
		state.timer = null;
		let verdict = "";
		try {
			verdict = this.tour_verdict();
		} catch (e) {
			return;
		}
		if (verdict === "ok") {
			try {
				this.tour_start(false);
			} catch (e) {
				// Not shown, not marked: the next visit tries again.
			}
		} else if (verdict === "wait" && (tries || 0) < MPTOUR.retries) this.tour_nudge(MPTOUR.retry_ms, (tries || 0) + 1);
	},

	// "ok": open it now. "wait": not yet. "": not on this screen, already seen, or no way to show it.
	tour_verdict() {
		if (!this.p6b_page_live()) return "";
		const state = this.tour_state();
		if (state.handle || this.tour_open()) return "";
		const kit = this.p6a && this.p6a.kit;
		if (!state.seen || !kit) return "wait";
		if (!kit.tour) return "";
		const kind = this.tour_kind();
		if (state.seen[kind] || this.tour_local_seen(kind)) return "";
		if (!MPTOUR_PURE.auto_screen(this.view)) return "";
		if (!this.tb || !this.tb.kit) return "wait";
		if (!this.data || this.tour_loading()) return "wait";
		if (this.tour_busy(kit)) return "wait";
		return "ok";
	},

	tour_loading() {
		const $body = this.$body;
		return !!($body && typeof $body.hasClass === "function" && $body.hasClass("mp-loading"));
	},

	// Anything on screen that is waiting for a decision, or in the way (see the Project Planner's twin).
	tour_busy(kit) {
		if (this.drag) return true;
		if (kit.drawer && typeof kit.drawer.current === "function" && kit.drawer.current()) return true;
		if (kit.guard && typeof kit.guard.top === "function" && kit.guard.top()) return true;
		if (window.cur_dialog && window.cur_dialog.display) return true;
		try {
			if (document.querySelector && document.querySelector(".modal.show")) return true;
		} catch (e) {
			return false;
		}
		return false;
	},

	tour_local_key(kind) {
		const user = (frappe.session && frappe.session.user) || "";
		return `${MPTOUR.local_pref}:${user}:${kind}`;
	},

	tour_local_seen(kind) {
		return this.load_pref(this.tour_local_key(kind), "") === "1";
	},

	tour_who() {
		return { can_schedule: this.data ? !!this.data.can_schedule : false, has_day: this.p10_has_day() };
	},

	tour_start(replay) {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !kit.tour) return false;
		const kind = this.tour_kind();
		const state = this.tour_state();
		const steps = MPTOUR_PURE.steps(kind, this.tour_who());
		if (!steps.length) return false;
		const handle = kit.tour.open({
			title: __("Maintenance Planner tour"),
			steps,
			owner: MP6A.owner,
			return_focus: replay && this.$p6a_help ? this.$p6a_help[0] : null,
			on_close: () => {
				if (state.handle === handle) state.handle = null;
			},
		});
		state.handle = handle || null;
		if (handle && !replay) this.tour_mark(kind);
		return !!handle;
	},

	tour_mark(kind) {
		const state = this.tour_state();
		state.seen = Object.assign({ desktop: false, phone: false }, state.seen, { [kind]: true });
		const args = { planner: MPTOUR.planner, kind };
		const method = `${MPTOUR.prefs}.mark_tour_seen`;
		const kit = this.p6a && this.p6a.kit;
		const failed = () => this.save_pref(this.tour_local_key(kind), "1");
		try {
			const call =
				kit && typeof kit.quiet_call === "function"
					? kit.quiet_call(method, args)
					: frappe.call({ method, args, silent: true });
			Promise.resolve(call).then(null, failed);
		} catch (e) {
			failed();
		}
	},

	tour_replay() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !kit.tour) return;
		const open = () => {
			try {
				this.tour_start(true);
			} catch (e) {
				// Nothing to show.
			}
		};
		if (kit.drawer && typeof kit.drawer.navigate === "function") kit.drawer.navigate(open);
		else open();
	},

	tour_legend_actions() {
		const kit = this.p6a && this.p6a.kit;
		if (!kit || !kit.tour) return [];
		return [
			{
				label: __("Take the tour"),
				title: __("A few short steps through the planner"),
				on_click: () => this.tour_replay(),
			},
		];
	},

	// ------------------------------------------------------------------ "?" for the views

	p12_legend_sections() {
		const item = (text) => ({ sample_html: "", text });
		const chip = (label, text, tone) => ({ chip: { text: label, tone: tone || "plain" }, text });
		return [
			{
				title: __("The views"),
				note: __("Three tabs over the same visits. The planner opens on the view you used last, on any device."),
				items: [
					chip(__("Month"), __("The whole month, a card for each visit.")),
					chip(__("Week"), __("One week, day by day: the view for planning visits.")),
					chip(__("Crew"), __("A row for each technician. Drag a visit to another day on the same row, or onto another technician's row to hand it over.")),
					chip(__("Simple"), __("The switch at the start of the bar. Every site, one card each: the visits done and the visits coming up, with one search box. Turn it off for the calendar.")),
				],
			},
			{
				title: __("Moving a visit"),
				items: [
					item(__("Drag a visit to another day. Only a draft nobody has started moves.")),
					item(__("Double-click an empty spot, or use + Add ▸ Add visit…, to draft a new visit there.")),
					item(__("Undo (Ctrl+Z, or the button in the notice) puts the last change back.")),
				],
			},
			{
				title: __("Draft mode and Notify crew?"),
				items: [
					chip(__("Draft mode"), __("More ▸ Tools ▸ Draft mode keeps your changes to yourself until you Publish them. Nobody is told before then, and nothing asks Notify crew?."), "amber"),
					chip(__("Notify crew?"), __("In live mode, a change to a visit's day, technician or crew asks once. Notify tells the people affected (never you); Don't notify keeps it quiet. It starts on Notify when the change touches the next 14 days. Esc puts the change back.")),
				],
			},
		];
	},
};

Object.assign(MaintenancePlanner.prototype, MPTOUR_METHODS);
