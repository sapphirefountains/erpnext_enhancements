/**
 * The Desk module sidebar as an icon rail (v1.793.0; Nik, 2026-11-03, Mock C of the sidebar round).
 *
 * Targets: the left Desk sidebar (the module's rows beside the dock) on every desk page, and a
 * bottom bar on phones. Loaded via: erpnext_enhancements.bundle.js (global), after
 * sidebar_memory.js, whose per-user copy of frappe's collapse key this reads. The CSS half is in
 * desk_enhancements.bundle.css under "Sidebar rail". Server: api/sidebar_rail.py.
 *
 * Nik: "I'd be okay with just the icon being visible and then when you hover over the icon it
 * displays the name of the link." He picked, from the mocks at claude.ai/artifact/QUt39tdosD4nAyJuogtfbW:
 * the rail with hover labels and a pin (A), the rows a person uses most first with count badges
 * and a Recent list (C), the rail by default, and a phone bottom bar.
 *
 * 1. THE RAIL. frappe v16.53.0 already draws one: `.body-sidebar-container:not(.expanded)` is the
 *    48px panel with the words folded away (sidebar.scss 317), `Sidebar.label_rail_rows` names each
 *    row for assistive tech, and the edge, the arrow and Ctrl+/ fold and unfold it. What stops it
 *    here is one method: `Sidebar.hides_when_collapsed()` (sidebar.js 721) answers `dock.is_pinned`,
 *    every user here has the dock pinned, and `apply_expanded_state` then adds `.sidebar-hidden`
 *    (width 0 in dock.scss 385) and sets the panel inert. The wrapper below answers false on a
 *    desktop, so a collapse is a rail; on a phone frappe's own answer stands (the panel is a drawer
 *    there, `panel_can_close`). `dock.apply_pin` re-asks through `refit_collapsed_sidebar`, so the
 *    wrapper is consulted on every dock refresh too. A frappe without the method is left alone.
 *
 * 2. HOVER LABELS. In the rail each row gets the tooltip the dock's own tiles use
 *    (`frappe.ui.Tooltip`, side right, `es-tooltip--plain`, dock.js name_tile), and the `title`
 *    attribute `label_rail_rows` set is removed so the browser does not draw a second one; the
 *    aria-label stays. Expanded, the tooltips are destroyed and the titles are frappe's business.
 *
 * 3. THE PIN. One button at the top of the standard-items band: in the rail it pins the panel open
 *    (`sidebar.open()`), expanded it folds it back (`sidebar.close()`). One name and one state
 *    (`aria-expanded`), sidebar_memory's rule. Both are frappe's own
 *    methods, which save the choice in `desk-sidebar-collapsed` and, through sidebar_memory's
 *    wrapper, in this person's copy (`ee-nav-collapsed@<user>`). tests/test_frappe_16_50_desk_compat.py
 *    forbids every other file of ours from calling them, because until this file a collapse meant a
 *    hidden sidebar; this file is the one allowance, named there with the reason.
 *
 * 4. RAIL BY DEFAULT. Once per person per browser (`ee-rail-intro-v1@<user>`), the sidebar starts
 *    folded whatever frappe's key says: frappe writes "0" into it on every `open()`, including the
 *    one every dock click used to make, so an existing "0" is not a choice anyone made. After that
 *    first time the person's own toggles are the only thing that moves it.
 *
 * 5. USED-MOST FIRST. The module's top-level rows are redrawn with the ones this person opens most
 *    first, at most four, each needing at least three visits, then a hairline, then the rest in
 *    frappe's order. Section groups and their children never move, and nothing is removed. The
 *    counts come from the server once per browser day (90 days of this person's own `tabRoute
 *    History`, which records lists, workspaces, pages and reports but never forms) and are then
 *    kept up by this file on every route change. A sidebar row is matched to Route History by what
 *    it opens (`route_key`), never its label. The module's landing page is unaffected:
 *    `module_landing_route` reads the boot order, not the drawn one. "Keep frappe's order" in the
 *    user menu turns it off (`ee-rail-adaptive@<user>` = 0).
 *
 * 6. BADGES. The Task row shows the open tasks assigned to this person (red when one is overdue),
 *    the Pending Confirmations row the pending AI actions, and the My Work row its own count
 *    (`erpnext_enhancements.my_work.refresh_count`), drawn on the icon so the rail shows them too
 *    (frappe folds `.sidebar-item-suffix` to width 0 there). Read with My Work's own cadence: once
 *    on load, then at most once a minute on navigation, a gap that holds whether or not the day's
 *    seed succeeded (a failing endpoint must not be asked on every route change). Once seeded,
 *    the poll asks for badges only (`usage=0`). A failed read shows nothing. frappe's
 *    `label_rail_rows` rewrites every aria-label on each fold, so the counts are painted again
 *    after `apply_expanded_state`.
 *
 * 7. RECENT. A row after My Work that opens the last six pages and records opened in this module,
 *    kept per person per browser, forms included (the one thing Route History cannot tell).
 *
 * 8. PHONE BOTTOM BAR. Below 768px the module's first four rows, in the used-most order, sit in a
 *    bar at the foot of the screen with their badges, and a fifth slot opens the drawer. It is
 *    rebuilt from `sidebar.sidebar_items` on every sidebar draw and re-checked on every page
 *    change, so there is nothing to configure. It stays off a page that hides the sidebar
 *    (`page.hide_sidebar`, read directly: on a phone frappe's `page_allows_sidebar()` is always
 *    true because the drawer "can close"), off a page that draws its own bar at the foot of the
 *    screen (`OWN_BOTTOM_BARS`, counted visible and inside the page on screen: the visit wizard's
 *    Next button sits at z-index 5), under a dialog, and while an input has focus.
 *
 * 9. TRAINING OPENS ON THE COURSE LIST. The dock's Training tile is a Workspace row, and a
 *    Workspace row opens its workspace; the Training hub to Training Course list hop happened 33
 *    times in 90 days with ten bounces straight back. `dock_entry_route` is wrapped to answer the
 *    Course list for that one entry; the hub stays one row away in the HR sidebar. Nothing in the
 *    dock's layers changes, so nobody's own arrangement is touched.
 *
 * Every wrapper runs frappe's original first (or around it) and acts only on a desktop unless
 * said otherwise; every DOM step is idempotent and never throws past a console warning. A missing
 * anchor in frappe's markup (`.standard-items-band`, `.item-anchor`, `.sidebar-item-icon`) is
 * warned once per page load as `sidebar_rail: ...`, so an upstream rename shows on the prod
 * checklist instead of silently dropping the pin, the tooltips or a badge.
 */
(function () {
	if (typeof window === "undefined") return;

	const NAV_KEY = "desk-sidebar-collapsed"; // frappe's own
	const NAV_USER_KEY = "ee-nav-collapsed"; // + "@" + user, sidebar_memory's per-person copy
	const INTRO_KEY = "ee-rail-intro-v1"; // + "@" + user: rail-by-default applied once
	const USE_KEY = "ee-rail-use"; // + "@" + user: {route: n}
	const SEED_KEY = "ee-rail-seed"; // + "@" + user: the day the server seed was read
	const RECENT_KEY = "ee-rail-recent"; // + "@" + user: {module: [{route, label, at}]}
	const ADAPTIVE_KEY = "ee-rail-adaptive"; // + "@" + user: "0" keeps frappe's order
	const USAGE_FLOOR = 3;
	const TOP_BAND_MAX = 4;
	const RECENT_MAX = 6;
	const BAR_ROWS = 4;
	const BADGE_MIN_GAP = 60 * 1000;
	const API = "erpnext_enhancements.api.sidebar_rail.get_rail_data";
	const BADGE_POLL = API + "?usage=0"; // once seeded: the badges only, no Route History tally
	const PIN_CLASS = "ee-rail-pin";
	const RECENT_CLASS = "ee-rail-recent-item";
	const BADGE_CLASS = "ee-rail-badge";
	const BAR_CLASS = "ee-quick-bar";
	// Dock entries that open somewhere other than their own page, keyed like `dock_entry_key`.
	const LANDINGS = { "Workspace|Training|": "/desk/training-course" };
	// The rows that carry a count, by what they open (link_type|link_to|url; never a label, so the
	// site-wide Custom Sidebar layer's unlabelled Task row matches too).
	const BADGED = { "DocType|Task|": "tasks", "DocType|AI Pending Action|": "pending_actions" };
	// Pages that draw a bar of their own at the foot of a phone screen; the quick bar stays out of
	// their way (the visit wizard's Next button sits at z-index 5, under anything of ours). Only
	// a visible one inside the page on screen counts: frappe's container hides the page it leaves
	// and keeps it in the DOM, and the board's close bar and the planner's move bar exist hidden.
	const OWN_BOTTOM_BARS = ".vz-nav, .qw-nav, .tp-nav, .isa-finbar.show, .ob-closebar, .mp-p6c-movebar";

	// Inline, so no icon sprite is needed (a name the sprite lacks draws nothing and raises nothing).
	const ICON_PIN =
		'<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
		'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 17v5"></path>' +
		'<path d="M9 3h6l-1 7 3 3H7l3-3z"></path></svg>';
	const ICON_HISTORY =
		'<svg class="icon icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
		'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7L3 8"></path>' +
		'<path d="M3 3v5h5"></path><path d="M12 7v5l3 2"></path></svg>';
	const ICON_MENU =
		'<svg class="icon icon-md" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
		'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"></path></svg>';

	const state = {
		usage: null, // {route: n} for this person
		badges: null, // {tasks: {count, overdue}, pending_actions: n, my_work: {count, overdue}}
		last_badges_at: 0,
		badges_inflight: null,
		tooltips: [],
		band: [], // the items drawn first
		popover: null,
		warned: {}, // canaries already raised this page load
	};

	// ------------------------------------------------------------------ storage

	function read(key) {
		try {
			return localStorage.getItem(key);
		} catch (e) {
			return null;
		}
	}

	function write(key, value) {
		try {
			localStorage.setItem(key, value);
		} catch (e) {
			// nothing to keep it in; it lasts until the next reload
		}
	}

	function read_json(key, fallback) {
		try {
			const raw = read(key);
			return raw ? JSON.parse(raw) : fallback;
		} catch (e) {
			return fallback;
		}
	}

	function current_user() {
		let user = "";
		try {
			user =
				(window.frappe && frappe.session && frappe.session.user) ||
				(window.frappe && frappe.boot && frappe.boot.user && frappe.boot.user.name) ||
				"";
			if (!user && typeof document !== "undefined" && typeof document.cookie === "string") {
				const match = document.cookie.match(/(?:^|;\s*)user_id=([^;]*)/);
				if (match) user = decodeURIComponent(match[1]);
			}
		} catch (e) {
			user = "";
		}
		return user === "Guest" ? "" : user;
	}

	function user_key(base) {
		const user = current_user();
		return user ? base + "@" + user : base;
	}

	function text(s, args) {
		return window.__ ? window.__(s, args) : s;
	}

	function esc(value) {
		if (window.frappe && frappe.utils && frappe.utils.escape_html) return frappe.utils.escape_html(value == null ? "" : String(value));
		return String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
	}

	// One warning per missing anchor per page load: an upstream markup rename must show in the
	// console (prod checklist item 8) without spamming it on every draw.
	function warn_once(key, message) {
		if (state.warned[key]) return;
		state.warned[key] = true;
		console.warn("sidebar_rail: " + message);
	}

	// A Recent entry's href comes back out of localStorage, so only a Desk path is ever drawn.
	function safe_route(route) {
		if (typeof route !== "string") return null;
		// The Desk here is /desk (the repo's desk-links guard forbids a hard-coded /app path).
		return route === "/desk" || route.startsWith("/desk/") ? route : null;
	}

	function is_phone() {
		try {
			if (window.frappe && typeof frappe.is_mobile === "function") return !!frappe.is_mobile();
			return window.matchMedia("(max-width: 767.98px)").matches;
		} catch (e) {
			return false;
		}
	}

	function adaptive_on() {
		return read(user_key(ADAPTIVE_KEY)) !== "0";
	}

	// ------------------------------------------------------------------ pure rules (also tested)

	// A row's identity, as frappe's `item_key` and `dock_entry_key` read it: what it opens, never its
	// label.
	function item_key(item) {
		return [item && item.link_type, item && item.link_to, item && item.url].map((v) => v || "").join("|");
	}

	function is_plain_link(item) {
		return !!item && item.type === "Link" && !item.child && !item.hidden && !item.standard;
	}

	// The Route History spelling of what a sidebar row opens, or null for a row it cannot record
	// (frappe's router_history.js skips forms). `lookup(segment)` says what a desk path segment is:
	// {kind: "doctype", name} | {kind: "workspace", name} | {kind: "page"} | null.
	function route_key(item, lookup) {
		if (!item || item.type !== "Link") return null;
		const to = item.link_to || "";
		switch (item.link_type) {
			case "Workspace":
				return to ? "Workspaces/" + to : null;
			case "DocType":
				return to ? "List/" + to : null;
			case "Page":
				return to || null;
			case "Dashboard":
				return to ? "dashboard-view/" + to : null;
			case "Report": {
				if (!to) return null;
				const report = item.report || {};
				const query = ["Query Report", "Script Report"].includes(report.report_type);
				if (!query && report.ref_doctype) return "List/" + report.ref_doctype + "/Report/" + to;
				return "query-report/" + to;
			}
			case "URL":
				return url_route_key(item.url, lookup);
			default:
				return null;
		}
	}

	function title_case(word) {
		return word ? word.charAt(0).toUpperCase() + word.slice(1).toLowerCase() : word;
	}

	function url_route_key(url, lookup) {
		if (!url || typeof url !== "string") return null;
		let path = url.replace(/[?#].*$/, "");
		if (!path.startsWith("/desk/")) return null;
		const parts = path
			.slice("/desk/".length)
			.split("/")
			.filter(Boolean)
			.map((p) => {
				try {
					return decodeURIComponent(p);
				} catch (e) {
					return p;
				}
			});
		if (!parts.length) return null;
		const what = typeof lookup === "function" ? lookup(parts[0]) : null;
		if (!what) return parts.join("/");
		if (what.kind === "workspace") return "Workspaces/" + what.name;
		if (what.kind === "doctype") {
			if (parts.length === 1) return "List/" + what.name;
			if (parts[1] !== "view") return null; // a form
			const view = parts[2] ? title_case(parts[2]) : "List";
			if (view === "Tree") return "Tree/" + what.name;
			const rest = parts.slice(3);
			return ["List", what.name, view].concat(rest).join("/");
		}
		return parts.join("/");
	}

	// How many times `usage` ({route: n}) opened what `key` names: the route itself or anything under it.
	function count_for(key, usage) {
		if (!key || !usage) return 0;
		let n = 0;
		const prefix = key + "/";
		for (const route of Object.keys(usage)) {
			if (route === key || route.startsWith(prefix)) n += Number(usage[route]) || 0;
		}
		return n;
	}

	// The module's rows redrawn: the top-level links with at least `floor` visits, most first (ties
	// keep frappe's order), at most `max`, then everything else exactly as it was. Groups and their
	// children never move.
	function arrange(items, count_of, options) {
		const floor = (options && options.floor) || USAGE_FLOOR;
		const max = (options && options.max) || TOP_BAND_MAX;
		const list = Array.isArray(items) ? items : [];
		const scored = list.map((item, i) => ({ item, i, n: is_plain_link(item) ? count_of(item) || 0 : 0 }));
		const band = scored
			.filter((s) => s.n >= floor)
			.sort((a, b) => b.n - a.n || a.i - b.i)
			.slice(0, max)
			.map((s) => s.item);
		if (!band.length) return { items: list.slice(), band: [] };
		const chosen = new Set(band);
		return { items: band.concat(list.filter((item) => !chosen.has(item))), band };
	}

	// The phone bar's rows: the first `max` top-level links of the (arranged) list.
	function pick_bar_rows(items, max) {
		const limit = max || BAR_ROWS;
		return (Array.isArray(items) ? items : []).filter(is_plain_link).slice(0, limit);
	}

	// What a row's badge shows, or null. `badges` is the server's answer plus My Work's.
	function badge_for(item, badges) {
		if (!badges) return null;
		const field = BADGED[item_key(item)];
		if (!field) return null;
		const value = badges[field];
		if (value == null) return null;
		const count = typeof value === "object" ? Number(value.count) || 0 : Number(value) || 0;
		const overdue = typeof value === "object" ? Number(value.overdue) || 0 : 0;
		return count > 0 ? { count, overdue } : null;
	}

	function badge_text(count) {
		return count > 99 ? "99+" : String(count);
	}

	// The Recent list after one more visit: newest first, one entry per route, at most `max`.
	function push_recent(list, entry, max) {
		const limit = max || RECENT_MAX;
		if (!entry || !entry.route) return Array.isArray(list) ? list.slice(0, limit) : [];
		const kept = (Array.isArray(list) ? list : []).filter((e) => e && e.route !== entry.route);
		return [entry].concat(kept).slice(0, limit);
	}

	// Rail by default, once per person per browser (see 4 above). Returns whether it folded.
	function apply_intro(storage, user) {
		if (!user) return false;
		const intro = INTRO_KEY + "@" + user;
		if (storage.getItem(intro) === "1") return false;
		storage.setItem(intro, "1");
		storage.setItem(NAV_KEY, "1");
		storage.setItem(NAV_USER_KEY + "@" + user, "1");
		return true;
	}

	// ------------------------------------------------------------------ usage and recents

	function usage_counts() {
		if (state.usage) return state.usage;
		state.usage = read_json(user_key(USE_KEY), {}) || {};
		return state.usage;
	}

	function bump_usage(route) {
		if (!route) return;
		const usage = usage_counts();
		usage[route] = (Number(usage[route]) || 0) + 1;
		write(user_key(USE_KEY), JSON.stringify(usage));
	}

	function today_stamp() {
		return new Date().toISOString().slice(0, 10);
	}

	// A read that never shows a dialog (the same request frappe.call's GET makes, without its
	// error handlers): a background read must not put "Server Error" in front of someone who
	// asked for nothing.
	function quiet_get(method) {
		return fetch("/api/method/" + method, {
			method: "GET",
			credentials: "same-origin",
			cache: "no-store",
			headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
		})
			.then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
			.then((body) => body && body.message);
	}

	// Seeds the counts from the server once per browser day and refreshes the badges, at most once
	// a minute unless forced. The gap holds whether or not the seed succeeded: a failing endpoint
	// is not asked again on every route change. Once seeded, the request is the badges only.
	function refresh_data(force) {
		if (!current_user()) return Promise.resolve(null);
		const now = Date.now();
		if (!force && now - state.last_badges_at < BADGE_MIN_GAP) {
			return state.badges_inflight || Promise.resolve(state.badges);
		}
		state.last_badges_at = now;
		const seeded_today = read(user_key(SEED_KEY)) === today_stamp();
		state.badges_inflight = quiet_get(seeded_today ? BADGE_POLL : API)
			.then((data) => {
				if (!data) return null;
				if (data.usage && !seeded_today) {
					state.usage = Object.assign({}, data.usage);
					write(user_key(USE_KEY), JSON.stringify(state.usage));
					write(user_key(SEED_KEY), today_stamp());
				}
				state.badges = Object.assign({}, state.badges || {}, data.badges || {});
				paint_badges();
				return data;
			})
			.catch(() => null)
			.finally(() => {
				state.badges_inflight = null;
			});
		refresh_my_work_badge();
		return state.badges_inflight;
	}

	function refresh_my_work_badge() {
		try {
			const my_work = window.erpnext_enhancements && window.erpnext_enhancements.my_work;
			if (!my_work || typeof my_work.refresh_count !== "function") return;
			const result = my_work.refresh_count();
			if (result && typeof result.then === "function") {
				result.then((data) => {
					if (!data) return;
					state.badges = Object.assign({}, state.badges || {}, { my_work: data });
					paint_badges();
				});
			}
		} catch (e) {
			// the count is My Work's; a badge is a convenience
		}
	}

	function recents() {
		return read_json(user_key(RECENT_KEY), {}) || {};
	}

	function remember_recent(module, entry) {
		if (!module || !entry || !safe_route(entry.route)) return;
		const all = recents();
		all[module] = push_recent(all[module], entry);
		write(user_key(RECENT_KEY), JSON.stringify(all));
	}

	// What the current route is called, for the Recent list: the sidebar row that leads here, else
	// the form's title, else the route.
	function current_label(route) {
		try {
			const sidebar = frappe.app && frappe.app.sidebar;
			const active = sidebar && sidebar.active_item && sidebar.active_item.find(".sidebar-item-label").first().text().trim();
			if (active) return active;
			if (route[0] === "Form" && window.cur_frm && cur_frm.doc && cur_frm.doc.name === route[2]) {
				const field = cur_frm.meta && cur_frm.meta.title_field;
				const title = field && cur_frm.doc[field];
				return title && title !== route[2] ? route[2] + " " + title : route[2];
			}
			if (route[0] === "Form") return route[2] || route[1];
			if (frappe.container && frappe.container.page && frappe.container.page.page && frappe.container.page.page.title) {
				return frappe.container.page.page.title;
			}
		} catch (e) {
			// fall through to the route
		}
		return route.slice(-1)[0] || route.join("/");
	}

	function on_route_change() {
		try {
			const route = frappe.get_route ? frappe.get_route() || [] : [];
			if (!route.length) return;
			const key = route.join("/");
			close_popover();
			if (route[0] !== "Form") bump_usage(key);
			const sidebar = frappe.app && frappe.app.sidebar;
			const module = sidebar && sidebar.current_module;
			// A moment later, so the form's title and the sidebar's active row exist.
			setTimeout(() => {
				try {
					remember_recent(module, { route: window.location.pathname + window.location.search, label: current_label(route), at: Date.now() });
					build_bar();
				} catch (e) {
					console.warn("sidebar_rail:", e);
				}
			}, 250);
			refresh_data(false);
		} catch (e) {
			console.warn("sidebar_rail:", e);
		}
	}

	// ------------------------------------------------------------------ the sidebar (wrappers)

	function segment_lookup(segment) {
		try {
			const slug = String(segment || "").toLowerCase();
			const routes = frappe.router && frappe.router.routes;
			if (routes && routes[slug] && routes[slug].doctype) return { kind: "doctype", name: routes[slug].doctype };
			const workspace = frappe.workspaces && frappe.workspaces[slug];
			if (workspace) return { kind: "workspace", name: workspace.name };
			return { kind: "page" };
		} catch (e) {
			return null;
		}
	}

	function count_of(item) {
		return count_for(route_key(item, segment_lookup), usage_counts());
	}

	function sidebar() {
		return window.frappe && frappe.app && frappe.app.sidebar;
	}

	function is_rail(sb) {
		return !!sb && !!sb.wrapper && !sb.sidebar_expanded && !sb.wrapper.hasClass("sidebar-hidden");
	}

	function drop_tooltips() {
		state.tooltips.forEach((tip) => {
			try {
				tip.destroy();
			} catch (e) {
				// already gone
			}
		});
		state.tooltips = [];
	}

	// Each rail row gets the dock tiles' tooltip, and loses the browser's own.
	function label_rows(sb) {
		drop_tooltips();
		if (!sb || !sb.wrapper || !is_rail(sb) || !(frappe.ui && frappe.ui.Tooltip)) return;
		const $anchors = sb.wrapper.find(".body-sidebar .item-anchor, .body-sidebar ." + PIN_CLASS + " button, .body-sidebar ." + RECENT_CLASS + " .item-anchor");
		if (!$anchors.length && Array.isArray(sb.items) && sb.items.length) {
			warn_once("anchor", "no .item-anchor in the rail: frappe's sidebar_item.html changed, so the hover labels are off");
		}
		$anchors.each(function () {
			// The row's name, not its badge sentence: the tooltip is the dock tiles' plain one.
			const label = $(this).find(".sidebar-item-label").first().text().trim() || $(this).attr("aria-label");
			if (!label) return;
			this.removeAttribute("title");
			state.tooltips.push(new frappe.ui.Tooltip(this, { text: label, side: "right", delay: 0, offset: 10, class: "es-tooltip--plain" }));
		});
	}

	function paint_pin(sb) {
		const $pin = sb && sb.wrapper ? sb.wrapper.find("." + PIN_CLASS + " button") : $();
		if (!$pin.length) return;
		const expanded = !!sb.sidebar_expanded;
		// One name, one state (sidebar_memory's rule): a label that changed with the state was read
		// twice by a screen reader, once as the name and once as the state.
		$pin.attr({ "aria-label": text("Pin sidebar"), "aria-expanded": expanded ? "true" : "false" });
	}

	function standard_row(cls, icon, label, href) {
		return $(
			'<div class="sidebar-item-container ' + cls + '" item-name="' + esc(label) + '" data-id="' + esc(label) + '">' +
				'<div class="standard-sidebar-item">' +
				(href
					? '<a href="' + esc(href) + '" class="item-anchor" data-link-type="" aria-label="' + esc(label) + '">'
					: '<button type="button" class="item-anchor btn-reset" aria-label="' + esc(label) + '">') +
				'<span class="sidebar-item-icon">' + icon + "</span>" +
				'<span class="sidebar-item-label">' + esc(label) + "</span>" +
				'<div class="sidebar-item-control"></div>' +
				(href ? "</a>" : "</button>") +
				"</div>" +
				'<div class="sidebar-child-item nested-container"></div>' +
				"</div>"
		);
	}

	// The pin at the top of the standard band and the Recent row at its end; idempotent.
	function ensure_rows(sb) {
		if (!sb || !sb.wrapper) return;
		const $band = sb.wrapper.find(".standard-items-band");
		if (!$band.length) {
			if (Array.isArray(sb.items) && sb.items.length) {
				warn_once("band", "no .standard-items-band: frappe's sidebar.html changed, so the pin and Recent rows are off");
			}
			return;
		}
		if (!$band.find("." + PIN_CLASS).length) {
			const $pin = standard_row(PIN_CLASS, ICON_PIN, text("Pin sidebar"));
			$pin.find("button").on("click", function () {
				try {
					const current = sidebar();
					if (!current || is_phone()) return;
					if (current.sidebar_expanded) current.close();
					else current.open();
				} catch (e) {
					console.warn("sidebar_rail:", e);
				}
			});
			$band.prepend($pin);
		}
		if (!$band.find("." + RECENT_CLASS).length) {
			const $recent = standard_row(RECENT_CLASS, ICON_HISTORY, text("Recent"));
			$recent.find("button").attr({ "aria-haspopup": "menu", "aria-expanded": "false" }).on("click", function (event) {
				event.preventDefault();
				toggle_popover(this);
			});
			$band.append($recent);
		}
		paint_pin(sb);
	}

	function close_popover() {
		if (!state.popover) return;
		state.popover.remove();
		state.popover = null;
		$("." + RECENT_CLASS + " button").attr("aria-expanded", "false");
		$(document).off("click.ee-rail-popover keydown.ee-rail-popover");
	}

	function toggle_popover(button) {
		if (state.popover) return close_popover();
		const sb = sidebar();
		if (!sb || !sb.wrapper) return;
		const module = sb && sb.current_module;
		const list = (recents()[module] || []).filter((e) => e && safe_route(e.route));
		const $pop = $('<div class="ee-rail-popover" role="menu"></div>');
		$pop.append('<div class="ee-rail-popover-title">' + esc(text("Recent in {0}", [module || ""])) + "</div>");
		if (!list.length) $pop.append('<div class="ee-rail-popover-empty">' + esc(text("Nothing opened here yet")) + "</div>");
		list.forEach((entry) => {
			const $a = $('<a role="menuitem" class="ee-rail-popover-row"></a>')
				.attr("href", safe_route(entry.route))
				.append($('<span class="ee-rail-popover-label"></span>').text(entry.label || entry.route))
				.append($('<span class="ee-rail-popover-when"></span>').text(ago(entry.at)));
			$a.on("click", () => {
				close_popover();
				// A drawer covers the page it just opened (what frappe does for its own rows).
				const current = sidebar();
				if (is_phone() && current && typeof current.close === "function") current.close();
			});
			$pop.append($a);
		});
		// "Order by what I use" is this person's own switch; it lives here rather than in the user
		// menu, whose markup is frappe's.
		const $order = $('<button type="button" class="ee-rail-popover-order btn-reset"></button>')
			.text(adaptive_on() ? text("Keep the standard order instead") : text("Put the rows I use first"))
			.on("click", () => {
				write(user_key(ADAPTIVE_KEY), adaptive_on() ? "0" : "1");
				close_popover();
				redraw_sidebar();
			});
		$pop.append($order);
		// Mounted beside the panel, not in it: `.body-sidebar` clips what overhangs it
		// (overflow-x hidden), and a 48px rail overhangs by design. Same reason frappe's own
		// `.sidebar-panel` mounts on the container.
		const $row = $(button).closest(".sidebar-item-container");
		if (!sb || !sb.wrapper || !$row.length) return;
		const container = sb.wrapper[0].getBoundingClientRect();
		const row = $row[0].getBoundingClientRect();
		const panel = sb.wrapper.find(".body-sidebar").first();
		$pop.css({ top: Math.max(0, row.top - container.top) + "px", left: (panel.length ? panel.outerWidth() : 48) + 8 + "px" });
		sb.wrapper.append($pop);
		state.popover = $pop;
		$(button).attr("aria-expanded", "true");
		setTimeout(() => {
			$(document)
				.on("click.ee-rail-popover", (event) => {
					if (!$(event.target).closest(".ee-rail-popover, ." + RECENT_CLASS).length) close_popover();
				})
				.on("keydown.ee-rail-popover", (event) => {
					if (event.key === "Escape") close_popover();
				});
		}, 0);
	}

	function ago(at) {
		const ms = Date.now() - (Number(at) || 0);
		if (!at || ms < 0) return "";
		const minutes = Math.round(ms / 60000);
		if (minutes < 1) return text("now");
		if (minutes < 60) return text("{0} min", [minutes]);
		const hours = Math.round(minutes / 60);
		if (hours < 24) return text("{0} h", [hours]);
		const days = Math.round(hours / 24);
		return days === 1 ? text("yesterday") : text("{0} d", [days]);
	}

	// Marks the drawn rows of the used-most band so the stylesheet can draw the hairline after it.
	function mark_band(sb) {
		if (!sb || !sb.wrapper) return;
		sb.wrapper
			.find(".ee-rail-top, .ee-rail-top-first, .ee-rail-top-last")
			.removeClass("ee-rail-top ee-rail-top-first ee-rail-top-last")
			.removeAttr("data-ee-caption");
		const band = state.band || [];
		if (!band.length || !Array.isArray(sb.items)) return;
		const drawn = sb.items.filter((row) => row && row.wrapper && band.includes(row.item));
		drawn.forEach((row, i) => {
			row.wrapper.addClass("ee-rail-top");
			// The expanded panel's caption, drawn by the stylesheet from this attribute so it is
			// translated here rather than hard-coded in CSS.
			if (i === 0) row.wrapper.addClass("ee-rail-top-first").attr("data-ee-caption", text("Used most"));
			if (i === drawn.length - 1) row.wrapper.addClass("ee-rail-top-last");
		});
	}

	function paint_badge($anchor, badge, label, key) {
		const $icon = $anchor.find(".sidebar-item-icon").first();
		if (!$icon.length) {
			// frappe omits the icon span for a row without an icon (or with show_arrow), and a
			// row the rail draws blank cannot carry a count either.
			if (badge && $anchor.length) warn_once("icon:" + key, "a badged row has no .sidebar-item-icon, so its count is not drawn: " + key);
			return;
		}
		let $badge = $icon.find("." + BADGE_CLASS);
		if (!badge) {
			$badge.remove();
			return;
		}
		if (!$badge.length) $badge = $('<span class="' + BADGE_CLASS + '" aria-hidden="true"></span>').appendTo($icon);
		$badge.text(badge_text(badge.count)).toggleClass(BADGE_CLASS + "--overdue", badge.overdue > 0);
		if (label) $anchor.attr("aria-label", label);
	}

	function badge_label(item, badge) {
		const base = (item && item.label) || "";
		if (!badge) return base;
		if (badge.overdue) return text("{0}, {1} assigned to you, {2} overdue", [base, badge.count, badge.overdue]);
		return text("{0}, {1}", [base, badge.count]);
	}

	function paint_badges() {
		try {
			const sb = sidebar();
			if (!sb || !sb.wrapper || !Array.isArray(sb.items)) return;
			sb.items.forEach((row) => {
				if (!row || !row.wrapper || !row.item) return;
				if (!BADGED[item_key(row.item)]) return;
				const badge = badge_for(row.item, state.badges);
				paint_badge(row.wrapper.find(".item-anchor").first(), badge, badge_label(row.item, badge), item_key(row.item));
			});
			const my_work = state.badges && state.badges.my_work;
			const $mine = sb.wrapper.find(".ee-my-work-item .item-anchor").first();
			if ($mine.length) {
				const count = my_work && Number(my_work.count) ? { count: Number(my_work.count), overdue: Number(my_work.overdue) || 0 } : null;
				paint_badge($mine, count, null, "my-work");
			}
			paint_bar_badges();
		} catch (e) {
			console.warn("sidebar_rail:", e);
		}
	}

	// ------------------------------------------------------------------ the phone bar

	function bar_items(sb) {
		const items = sb && Array.isArray(sb.sidebar_items) ? sb.sidebar_items : [];
		const arranged = adaptive_on() ? arrange(items, count_of).items : items;
		return pick_bar_rows(arranged, BAR_ROWS);
	}

	function bar_href(item, module) {
		try {
			return frappe.ui.sidebar_item.get_route(item, false, module) || null;
		} catch (e) {
			return null;
		}
	}

	// The bar stays off a page that hides the sidebar (it opted out of the module shell; the flag is
	// read directly because on a phone frappe's page_allows_sidebar() is always true, the drawer
	// "can close") and off a page that draws its own bar at the foot of the screen.
	function bar_suppressed(sb) {
		try {
			const page =
				sb && typeof sb.current_page === "function"
					? sb.current_page()
					: window.frappe && frappe.container && frappe.container.page && frappe.container.page.page;
			if (!page || page.hide_sidebar) return true;
			if (sb && typeof sb.page_allows_sidebar === "function" && !sb.page_allows_sidebar()) return true;
			// Visible bars inside the page on screen only (see OWN_BOTTOM_BARS); page-change
			// re-runs build_bar, so a bar shown or hidden later is seen on the next page.
			const page_el = window.frappe && frappe.container && frappe.container.page;
			if (!page_el) return false;
			return $(page_el).find(OWN_BOTTOM_BARS).filter(":visible").length > 0;
		} catch (e) {
			return false;
		}
	}

	function build_bar() {
		try {
			const sb = sidebar();
			let $bar = $("." + BAR_CLASS);
			if (!is_phone() || !sb || !sb.current_module || bar_suppressed(sb)) {
				$bar.remove();
				$("body").removeClass("ee-quick-bar-on");
				return;
			}
			const rows = bar_items(sb).map((item) => ({ item, href: bar_href(item, sb.current_module) })).filter((r) => r.href);
			if (!rows.length) {
				$bar.remove();
				$("body").removeClass("ee-quick-bar-on");
				return;
			}
			if (!$bar.length) $bar = $('<nav class="' + BAR_CLASS + '"></nav>').appendTo("body");
			$bar.attr("aria-label", text("{0}, quick bar", [sb.current_module])).empty();
			rows.forEach((r) => {
				const icon = r.item.icon && frappe.utils && frappe.utils.icon ? frappe.utils.icon(r.item.icon, "md", "", "", "current-color", true) : "";
				const $a = $('<a class="ee-quick-bar-item"></a>')
					.attr({ href: r.href, "data-key": item_key(r.item), "aria-label": r.item.label })
					.append('<span class="ee-quick-bar-icon">' + icon + "</span>")
					.append($('<span class="ee-quick-bar-label"></span>').text(r.item.label));
				$bar.append($a);
			});
			const $more = $('<button type="button" class="ee-quick-bar-item ee-quick-bar-more"></button>')
				.attr("aria-label", text("More, opens the module menu"))
				.append('<span class="ee-quick-bar-icon">' + ICON_MENU + "</span>")
				.append($('<span class="ee-quick-bar-label"></span>').text(text("More")))
				.on("click", () => {
					const current = sidebar();
					if (current && typeof current.open === "function") current.open();
				});
			$bar.append($more);
			$("body").addClass("ee-quick-bar-on");
			paint_bar_active();
			paint_bar_badges();
		} catch (e) {
			console.warn("sidebar_rail:", e);
		}
	}

	function paint_bar_active() {
		const $bar = $("." + BAR_CLASS);
		if (!$bar.length) return;
		const here = window.location.pathname;
		$bar.find("a.ee-quick-bar-item").each(function () {
			const href = (this.getAttribute("href") || "").replace(/[?#].*$/, "");
			const on = !!href && (here === href || here.startsWith(href + "/"));
			$(this).toggleClass("active", on).attr("aria-current", on ? "page" : null);
		});
	}

	function paint_bar_badges() {
		const $bar = $("." + BAR_CLASS);
		if (!$bar.length) return;
		$bar.find("a.ee-quick-bar-item").each(function () {
			const key = this.getAttribute("data-key");
			const field = BADGED[key];
			const value = field && state.badges ? state.badges[field] : null;
			const badge = value == null ? null : typeof value === "object" ? { count: Number(value.count) || 0, overdue: Number(value.overdue) || 0 } : { count: Number(value) || 0, overdue: 0 };
			const $icon = $(this).find(".ee-quick-bar-icon");
			let $badge = $icon.find("." + BADGE_CLASS);
			if (!badge || !badge.count) return $badge.remove();
			if (!$badge.length) $badge = $('<span class="' + BADGE_CLASS + '" aria-hidden="true"></span>').appendTo($icon);
			$badge.text(badge_text(badge.count)).toggleClass(BADGE_CLASS + "--overdue", badge.overdue > 0);
		});
	}

	function watch_keyboard() {
		$(document)
			.on("focusin.ee-quick-bar", (event) => {
				if ($(event.target).is("input, textarea, select, [contenteditable]")) $("body").addClass("ee-quick-bar-hidden");
			})
			.on("focusout.ee-quick-bar", () => {
				setTimeout(() => {
					if (!$(document.activeElement).is("input, textarea, select, [contenteditable]")) $("body").removeClass("ee-quick-bar-hidden");
				}, 50);
			});
		$(window).on("resize.ee-quick-bar", () => build_bar());
		// After frappe shows a page: its hide_sidebar flag and its own bottom bar exist by now.
		$(document).on("page-change.ee-quick-bar", () => {
			if (is_phone()) build_bar();
		});
	}

	// ------------------------------------------------------------------ install

	function decorate(sb) {
		ensure_rows(sb);
		mark_band(sb);
		paint_badges();
		label_rows(sb);
		build_bar();
	}

	function patch_sidebar() {
		try {
			const proto = window.frappe && frappe.ui && frappe.ui.Sidebar && frappe.ui.Sidebar.prototype;
			if (!proto) return false;
			if (proto._ee_rail_patched) return true;
			if (typeof proto.hides_when_collapsed !== "function" || typeof proto.apply_expanded_state !== "function") return false;

			// 1. A collapse is a rail on a desktop; a phone keeps frappe's answer.
			const hides = proto.hides_when_collapsed;
			proto.hides_when_collapsed = function () {
				if (is_phone()) return hides.apply(this, arguments);
				return false;
			};

			// 5. The used-most band, before frappe draws the rows.
			if (typeof proto.create_sidebar === "function") {
				const create = proto.create_sidebar;
				proto.create_sidebar = function (items) {
					let list = items;
					state.band = [];
					try {
						// On a phone too: the drawer and the bottom bar read the same order.
						if (adaptive_on() && Array.isArray(items)) {
							const result = arrange(items, count_of);
							list = result.items;
							state.band = result.band;
						}
					} catch (e) {
						console.warn("sidebar_rail:", e);
						list = items;
					}
					return create.call(this, list);
				};
			}

			// The rows are drawn: add ours, mark the band, badge, label.
			if (typeof proto.make_sidebar === "function") {
				const make = proto.make_sidebar;
				proto.make_sidebar = function () {
					const result = make.apply(this, arguments);
					try {
						decorate(this);
					} catch (e) {
						console.warn("sidebar_rail:", e);
					}
					return result;
				};
			}

			// 2 and 3. Tooltips only in the rail; the pin reads the state.
			const apply = proto.apply_expanded_state;
			proto.apply_expanded_state = function () {
				const result = apply.apply(this, arguments);
				try {
					paint_pin(this);
					// frappe's label_rail_rows (inside the original) rewrote every aria-label to the
					// bare row name, so the badge sentences are painted back before the tooltips read them.
					paint_badges();
					label_rows(this);
					close_popover();
				} catch (e) {
					console.warn("sidebar_rail:", e);
				}
				return result;
			};

			// 9. One dock entry opens somewhere else.
			if (typeof proto.dock_entry_route === "function") {
				const route_of = proto.dock_entry_route;
				proto.dock_entry_route = function (entry) {
					try {
						const key = entry ? item_key(entry) : "";
						if (key && LANDINGS[key]) return LANDINGS[key];
					} catch (e) {
						// frappe's own answer
					}
					return route_of.apply(this, arguments);
				};
			}

			proto._ee_rail_patched = true;
			return true;
		} catch (e) {
			console.warn("sidebar_rail:", e);
			return false;
		}
	}

	// Redraws the module's rows (frappe's `setup` skips a module already on screen, so it is
	// cleared first), after the order switch changes.
	function redraw_sidebar() {
		const current = sidebar();
		if (!current || !current.current_module || typeof current.setup !== "function") return;
		const module = current.current_module;
		try {
			current.current_module = null;
			current.setup(module);
		} catch (e) {
			// A failed redraw must not leave the sidebar without a module.
			if (!current.current_module) current.current_module = module;
			console.warn("sidebar_rail:", e);
		}
	}

	function start() {
		if (!patch_sidebar()) $(document).on("app_ready", patch_sidebar);
		// 4. Rail by default, before frappe reads its key (app_include_js runs before start_app).
		try {
			if (!is_phone()) apply_intro(localStorage, current_user());
		} catch (e) {
			// storage refused; the panel opens as frappe decides
		}
		$(document).on("app_ready", function () {
			setTimeout(() => {
				try {
					if (frappe.router && typeof frappe.router.on === "function") frappe.router.on("change", on_route_change);
					watch_keyboard();
					refresh_data(true);
					const sb = sidebar();
					if (sb && sb.wrapper) decorate(sb);
				} catch (e) {
					console.warn("sidebar_rail:", e);
				}
			}, 0);
		});
	}

	window.erpnext_enhancements = window.erpnext_enhancements || {};
	window.erpnext_enhancements.sidebar_rail = {
		item_key,
		route_key,
		url_route_key,
		count_for,
		arrange,
		pick_bar_rows,
		badge_for,
		badge_text,
		push_recent,
		apply_intro,
		patch_sidebar,
		refresh: () => refresh_data(true),
		refresh_data,
		redraw_sidebar,
		safe_route,
		bar_suppressed,
		warn_once,
		LANDINGS,
		USAGE_FLOOR,
		TOP_BAND_MAX,
		RECENT_MAX,
		BAR_ROWS,
	};

	if (typeof $ === "function") start();
})();
