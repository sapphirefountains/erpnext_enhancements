#!/usr/bin/env node
/**
 * The planners' filters in the address (custom pages S5c, v1.676.0), and the planner kit's
 * stylesheet linked once, run against a model of Frappe v16.50's router and assets.js.
 *
 * Loads the REAL Project Planner and Maintenance Planner scripts into vm contexts over one fake
 * Desk and drives their real routing and filter methods:
 *
 *   Project Planner       ?project=, ?pm=, ?group=
 *   Maintenance Planner   ?technician=
 *
 * The router model follows v16.50 (`git show v16.50.0:frappe/public/js/frappe/router.js`) where
 * the planners depend on it. set_route moves a plain object among its parts into
 * frappe.route_options (make_url) and writes route_options as the query with the path (frappe PR
 * #40040, new in v16.50; `{ queryInUrl: false }` models the router before it, which pushed the path
 * alone). It pushes an entry, or replaces the one on screen when frappe.route_flags.replace_route is
 * set, and does nothing when the path and query are already on screen. Every route change copies
 * the address's query into frappe.route_options, creating the object when it is null
 * (set_route_options_from_url), then shows the page, which runs on_page_show. Back and Forward land
 * on an entry the same way, and then run the page's own popstate listeners (frappe's listener was
 * added at Desk boot, before any page's). A Back the planner kit owns (it closes a drawer) does not
 * route at all. Timers run only when a scenario flushes them.
 *
 * The assets model follows v16.50's assets.js: frappe.require maps each name through
 * boot.assets_json, and AssetManager.load_asset appends a <script> or <link> unless the path is in
 * `_executed`, which it pushes to only once the element has loaded.
 *
 * The pages are not built: each page's class is taken from its own script and its routing and
 * filter methods run on an instance whose drawing methods are recorders.
 *
 * Run: node scripts/test_planner_url_filters.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const FILES = {
	projectPlanner: path.join(APP, "project_enhancements", "page", "project_planner", "project_planner.js"),
	maintenancePlanner: path.join(APP, "sapphire_maintenance", "page", "maintenance_planner", "maintenance_planner.js"),
};

let checks = 0;
let failures = 0;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a !== e) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${e}\n    actual   ${a}`);
	}
}

// The three filters these scenarios were written for. all_tasks (P2, D6) is the fourth key of the
// Project Planner's address; its own scenarios are at the end, and elsewhere it stays at its default.
const f3 = (page) => {
	const all = page.filters();
	return { project: all.project, pm: all.pm, group: all.group };
};

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
	for (let i = 0; i < 8; i++) await tick();
}

// ------------------------------------------------------------------ the Desk, its router and assets

function makeDesk(options) {
	const o = Object.assign({ queryInUrl: true }, options || {});
	const desk = {
		entries: [{ url: o.start || "/desk/project/PRJ-00001", state: null }],
		index: 0,
		today: o.today || "2026-10-14",
		route: [],
		shows: [],
		handlers: {},
		prefs: {},
		calls: [],
		timers: [],
		listeners: [],
		head: [],
		replaces: 0,
	};
	const current = () => desk.entries[desk.index];
	const location = {
		get pathname() {
			const url = current().url;
			const q = url.indexOf("?");
			return q < 0 ? url : url.slice(0, q);
		},
		get search() {
			const url = current().url;
			const q = url.indexOf("?");
			return q < 0 ? "" : url.slice(q);
		},
		hash: "",
	};
	// The browser percent-encodes an apostrophe in a query (the URL standard's special-query set), so
	// location.search reads back %27 for a ' written bare.
	const parsed = (url) => {
		const q = url.indexOf("?");
		return q < 0 ? url : url.slice(0, q) + url.slice(q).replace(/'/g, "%27");
	};
	const history = {
		get state() {
			return current().state;
		},
		get length() {
			return desk.entries.length;
		},
		pushState(state, _title, url) {
			desk.entries = desk.entries.slice(0, desk.index + 1);
			desk.entries.push({ url: url == null ? current().url : parsed(url), state });
			desk.index += 1;
		},
		replaceState(state, _title, url) {
			desk.replaces += 1;
			desk.entries[desk.index] = { url: url == null ? current().url : parsed(url), state };
		},
	};
	const isPlainObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
	const pages = new Proxy(
		{},
		{
			get(target, key) {
				if (!(key in target)) target[key] = {};
				return target[key];
			},
		}
	);

	// assets.js (v16.50): `_executed` is pushed to only after the element has loaded.
	const executed = [];
	function load_asset(asset) {
		if (executed.includes(asset)) return Promise.resolve();
		const css = asset.split("?")[0].endsWith(".css");
		desk.head.push(css ? { tag: "link", href: asset } : { tag: "script", src: asset });
		return new Promise((resolve) => setImmediate(resolve)).then(() => {
			executed.push(asset);
		});
	}

	const frappe = {
		route_options: null,
		route_flags: {},
		boot: {
			page_info: {},
			sysdefaults: { first_day_of_the_week: "Sunday" },
			assets_json: {
				"planner_kit.bundle.js": "/assets/erpnext_enhancements/dist/js/planner_kit.bundle.ABC123.js",
				"planner_kit.bundle.css": "/assets/erpnext_enhancements/dist/css/planner_kit.bundle.DEF456.css",
			},
		},
		pages,
		datetime: {
			get_today: () => desk.today,
			now_time: () => "09:00:00",
			str_to_user: (value) => String(value),
		},
		utils: {
			escape_html: (value) =>
				String(value == null ? "" : value)
					.replace(/&/g, "&amp;")
					.replace(/</g, "&lt;")
					.replace(/>/g, "&gt;")
					.replace(/"/g, "&quot;")
					.replace(/'/g, "&#39;"),
		},
		// Server calls are recorded, and answer nothing.
		call(opts) {
			desk.calls.push({ method: opts.method, args: opts.args || {} });
			return Promise.resolve({});
		},
		show_alert() {},
		require(items, callback) {
			if (typeof items === "string") items = [items];
			const assets = items.map((item) => frappe.boot.assets_json[item] || item);
			return new Promise((resolve) => {
				Promise.all(assets.map(load_asset)).then(() => {
					resolve();
					if (callback) callback();
				});
			});
		},
		get_route: () => desk.route,
		set_route(...args) {
			let parts = args.length === 1 && Array.isArray(args[0]) ? args[0] : args;
			// convert_from_standard_route: ["Form", "Task", name] is /desk/task/<name>.
			if (parts[0] === "Form") parts = [String(parts[1]).toLowerCase().replace(/ /g, "-")].concat(parts.slice(2));
			// make_url: a plain object among the parts becomes frappe.route_options.
			const segments = [];
			parts.forEach((part) => {
				if (isPlainObject(part)) frappe.route_options = part;
				else segments.push(encodeURIComponent(String(part)));
			});
			const route_options = frappe.route_options || {};
			const query = Object.entries(route_options)
				.map(
					([key, value]) =>
						`${key}=` + encodeURIComponent(value !== null && typeof value === "object" ? JSON.stringify(value) : String(value))
				)
				.join("&");
			push_state("/desk/" + segments.join("/"), query ? `?${query}` : "");
			frappe.route_flags = {};
			return Promise.resolve();
		},
	};

	function push_state(pathname, query) {
		if (location.pathname !== pathname || location.search !== query) {
			const url = o.queryInUrl ? pathname + query : pathname;
			history[frappe.route_flags.replace_route ? "replaceState" : "pushState"](null, null, url);
			route();
		}
	}

	function route() {
		desk.route = location.pathname
			.replace(/^\/desk\/?/, "")
			.split("/")
			.filter(Boolean)
			.map(decodeURIComponent);
		// set_route_options_from_url
		if (!frappe.route_options) frappe.route_options = {};
		new URLSearchParams(location.search).forEach((value, key) => {
			frappe.route_options[key] = value;
		});
		desk.shows.push({ route: desk.route.slice(), options: Object.assign({}, frappe.route_options) });
		const show = desk.handlers[desk.route[0]];
		if (show) show();
	}

	function popstate(routed) {
		if (routed) route();
		desk.listeners.forEach((fn) => fn({ type: "popstate", state: history.state }));
	}

	const storage = {
		getItem: (key) => (key in desk.prefs ? desk.prefs[key] : null),
		setItem: (key, value) => {
			desk.prefs[key] = String(value);
		},
		removeItem: (key) => {
			delete desk.prefs[key];
		},
	};
	const window = {
		location,
		history,
		localStorage: storage,
		addEventListener(type, fn) {
			if (type === "popstate") desk.listeners.push(fn);
		},
	};

	Object.assign(desk, {
		frappe,
		window,
		here: () => current().url,
		urls: () => desk.entries.map((entry) => entry.url),
		land: route,
		// A tap on a link the router follows (the sidebar, another page's button).
		go(...args) {
			return frappe.set_route(...args);
		},
		back() {
			desk.index -= 1;
			popstate(true);
		},
		forward() {
			desk.index += 1;
			popstate(true);
		},
		// The planner kit's own Back, closing a drawer: its router gate keeps the router from routing.
		kit_back() {
			desk.index -= 1;
			popstate(false);
		},
		flush() {
			desk.timers.splice(0).forEach((fn) => fn());
		},
		// A reload: the same history, a new Desk (route_options, listeners and the kit's request go).
		reload() {
			frappe.route_options = null;
			frappe.route_flags = {};
			desk.handlers = {};
			desk.listeners = [];
			desk.timers = [];
			delete window.planner_kit_request;
		},
		links: () => desk.head.filter((el) => el.tag === "link").map((el) => el.href),
		scripts: () => desk.head.filter((el) => el.tag === "script").map((el) => el.src),
	});
	return desk;
}

function translate(text, args) {
	return String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] !== undefined ? String(args[i]) : ""));
}

// A strict YYYY-MM-DD check is all the routing code asks of moment.
function fakeMoment(value) {
	const text = String(value == null ? "" : value);
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
	const valid =
		!!m &&
		(() => {
			const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
			return d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
		})();
	const m_obj = { text, isValid: () => valid, format: () => m_obj.text };
	// P5's › steps the Lookahead by days (shift): moment's add, mutating, for whole days only.
	m_obj.add = (amount, unit) => {
		if (unit !== "days" || !valid) throw new Error(`fakeMoment.add(${amount}, ${unit}) is not modelled`);
		const d = new Date(`${m_obj.text}T00:00:00Z`);
		d.setUTCDate(d.getUTCDate() + Number(amount));
		m_obj.text = d.toISOString().slice(0, 10);
		return m_obj;
	};
	return m_obj;
}

// A recording stand-in for jQuery: an element remembers its value; anything else returns it.
function makeJQuery() {
	function element() {
		const state = { value: "" };
		const self = new Proxy(function () {}, {
			get(_target, prop) {
				if (prop === "__state") return state;
				if (prop === "then") return undefined;
				if (prop === "val") {
					return (value) => {
						if (value === undefined) return state.value;
						state.value = value;
						return self;
					};
				}
				return () => self;
			},
		});
		return self;
	}
	return () => element();
}

function load(desk, file) {
	const sandbox = {
		frappe: desk.frappe,
		window: desk.window,
		$: makeJQuery(),
		__: translate,
		moment: fakeMoment,
		console,
		setTimeout: (fn) => {
			desk.timers.push(fn);
			return desk.timers.length;
		},
		clearTimeout: () => {},
		document: { hidden: false, getElementById: () => null, addEventListener() {} },
		Promise,
		URLSearchParams,
		encodeURIComponent,
	};
	const context = vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(file, "utf-8"), context, { filename: file });
	return context;
}

// ------------------------------------------------------------------ the pages

const PP_PREFS = { project: "ee_project_planner_project", pm: "ee_project_planner_pm", group: "ee_project_planner_group" };
const MP_TECH = "ee_maintenance_planner_technician";

// The Project Planner as its constructor leaves it, from the prefs this browser remembers.
function projectPlanner(desk, init) {
	const context = load(desk, FILES.projectPlanner);
	const ProjectPlanner = vm.runInContext("ProjectPlanner", context);
	const $ = context.$;
	const pp = Object.create(ProjectPlanner.prototype);
	Object.assign(pp, { view: "week", anchor: desk.today, show_all_unscheduled: true, p6c: null, loads: [], renders: 0 });
	pp.project = pp.load_pref(PP_PREFS.project, "");
	pp.pm = pp.load_pref(PP_PREFS.pm, "");
	pp.group = pp.load_pref(PP_PREFS.group, "");
	Object.assign(pp, { $project: $(), $pm: $(), $group: $() });
	pp.own_filters = f3(pp);
	pp.url_filters = null;
	Object.assign(pp, init || {});
	pp.watch_filter_url();
	pp.set_mode = () => {};
	pp.load = () => {
		pp.loads.push({ view: pp.view, anchor: pp.anchor, filters: f3(pp) });
		// The real load's order once its answer is in: the filters' options, then the address.
		if (pp.answer) {
			pp.data = pp.answer;
			pp.index();
			pp.fill_filters();
			pp.write_filter_url();
		}
	};
	pp.render = () => {
		pp.renders += 1;
	};
	pp.load_route = () => pp.loads.push({ view: "route", resource: pp.route_resource, filters: f3(pp) });
	// The Utilization view (v1.687.0) loads the Crew Utilization report's rows; recorded like the rest.
	pp.load_utilization = () => pp.loads.push({ view: pp.view, anchor: pp.anchor, filters: f3(pp) });
	pp.p6c_kit = () => null;
	pp.p6c_sync_controls = () => {};
	desk.handlers["project-planner"] = () => pp.handle_route();
	return pp;
}

function maintenancePlanner(desk, init) {
	const context = load(desk, FILES.maintenancePlanner);
	const MaintenancePlanner = vm.runInContext("MaintenancePlanner", context);
	const $ = context.$;
	const mp = Object.create(MaintenancePlanner.prototype);
	Object.assign(mp, { view: "month", anchor: desk.today, p6c: null, loads: [], renders: 0 });
	mp.technician = mp.load_pref(MP_TECH, "");
	mp.$tech = $();
	mp.own_filters = mp.filters();
	mp.url_filters = null;
	Object.assign(mp, init || {});
	mp.watch_filter_url();
	mp.load = () => {
		mp.loads.push({ view: mp.view, anchor: mp.anchor, technician: mp.technician });
		if (mp.answer) {
			mp.data = mp.answer;
			mp.index();
			mp.fill_technicians();
			mp.write_filter_url();
		}
	};
	mp.render = () => {
		mp.renders += 1;
	};
	mp.p6c_kit = () => null;
	mp.p6c_sync_controls = () => {};
	desk.handlers["maintenance-planner"] = () => mp.handle_route();
	return mp;
}

const ANSWER = {
	projects: [
		{ name: "PRJ-00001", title: "Plaza fountain", pm: "Pat Lee" },
		{ name: "PRJ-00002", title: "Hotel lobby", pm: "Sam Roe" },
		{ name: "PRJ-MINE", title: "Mine", pm: "me@example.com" },
	],
	tasks: [],
	unscheduled: [],
	resources: [],
	days: {},
	foreign: [],
};
const MINE = { ee_project_planner_project: "PRJ-MINE", ee_project_planner_pm: "me@example.com", ee_project_planner_group: "" };
const SAVE_LAST = "erpnext_enhancements.api.planner_views_prefs.save_last_view";
const saved = (desk) => desk.calls.filter((c) => c.method === SAVE_LAST).map((c) => JSON.parse(c.args.data));

// ------------------------------------------------------------------ Project Planner

test("Project Planner: a link with all three filters opens the planner on them", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&pm=Pat%20Lee&group=Field" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk);
	desk.land();
	check("the filters are the link's", f3(pp), { project: "PRJ-00001", pm: "Pat Lee", group: "Field" });
	check("the bar shows them", [pp.$project.val(), pp.$pm.val(), pp.$group.val()], ["PRJ-00001", "Pat Lee", "Field"]);
	check("on the week the path names", pp.loads, [
		{ view: "week", anchor: "2026-10-12", filters: { project: "PRJ-00001", pm: "Pat Lee", group: "Field" } },
	]);
	check("route_options consumed", desk.frappe.route_options, null);
	check("the address is left as it came", desk.urls(), ["/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&pm=Pat%20Lee&group=Field"]);
	check("the remembered filters are untouched", desk.prefs, MINE);
});

test("Project Planner: one filter in the address is the whole filter", () => {
	const desk = makeDesk({ start: "/desk/project-planner?group=Design" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk);
	desk.land();
	check("the others are All, not the remembered ones", f3(pp), { project: "", pm: "", group: "Design" });
	check("the address says exactly that", desk.here(), "/desk/project-planner?group=Design");
});

test("Project Planner: a bare address writes the remembered filters into it, in place", () => {
	const desk = makeDesk({ start: "/desk/project-planner" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk);
	desk.land();
	check("the remembered filters", f3(pp), { project: "PRJ-MINE", pm: "me@example.com", group: "" });
	check("now in the address", desk.here(), "/desk/project-planner?project=PRJ-MINE&pm=me%40example.com");
	check("no entry added", desk.entries.length, 1);
});

test("Project Planner: picking a filter rewrites the address in place and is remembered", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&keep=1" });
	const pp = projectPlanner(desk);
	desk.land();
	pp.pick_filter("group", "Design");
	check("the group joins the address", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&group=Design&keep=1");
	pp.pick_filter("project", "");
	check("All leaves the address", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?group=Design&keep=1");
	check("no history entry for either", desk.entries.length, 1);
	check("rendered each time", pp.renders, 2);
	check(
		"only the filters picked are remembered; the link's PM was never picked",
		[desk.prefs[PP_PREFS.project], desk.prefs[PP_PREFS.pm], desk.prefs[PP_PREFS.group]],
		["", undefined, "Design"]
	);
	pp.pick_filter("pm", "Pat Lee");
	check("a name with a space, encoded as set_route encodes it", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?pm=Pat%20Lee&group=Design&keep=1");
});

test("Project Planner: a reload keeps the address's filters over the remembered and the last used", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00002" });
	Object.assign(desk.prefs, MINE);
	projectPlanner(desk);
	desk.go("project-planner", "calendar", "week", "2026-10-12", { project: "PRJ-00002", group: "Field" });
	desk.reload();
	const last = { view: "week", project: "PRJ-OLD", pm: "pm@example.com", group: "PM" };
	const pp = projectPlanner(desk, { p6c: { loaded: true, restored: false, last, saved_text: null, views: [] } });
	desk.land();
	check("the address wins", f3(pp), { project: "PRJ-00002", pm: "", group: "Field" });
	check("the same address", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00002&group=Field");
	check("one entry past the form", desk.entries.length, 2);
	pp.p6c_save_last();
	check(
		"last used keeps the person's own (the restored view), not the address's",
		saved(desk).map((s) => [s.project, s.pm, s.group]),
		[["PRJ-OLD", "pm@example.com", "PM"]]
	);
	check("not remembered", desk.prefs, MINE);
});

test("Project Planner: Back and Forward walk the screens, each with the filters it showed", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	const pp = projectPlanner(desk);
	desk.go("project-planner", "calendar", "week", "2026-10-12");
	pp.pick_filter("project", "PRJ-00001");
	pp.go("month", "2026-10-12");
	check("the month carries the filter", desk.here(), "/desk/project-planner/calendar/month/2026-10-12?project=PRJ-00001");
	pp.pick_filter("group", "Field");
	pp.go("crew", "2026-10-12");
	check("the crew view carries both", desk.here(), "/desk/project-planner/crew/2026-10-12?project=PRJ-00001&group=Field");
	check("a picked filter is no screen: one entry per view", desk.urls(), [
		"/desk/project/PRJ-00001",
		"/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001",
		"/desk/project-planner/calendar/month/2026-10-12?project=PRJ-00001&group=Field",
		"/desk/project-planner/crew/2026-10-12?project=PRJ-00001&group=Field",
	]);
	const prefs = Object.assign({}, desk.prefs);
	desk.back();
	check("Back: the month, as it was left", [pp.view, f3(pp)], ["month", { project: "PRJ-00001", pm: "", group: "Field" }]);
	desk.back();
	check("Back: the week, before the group was picked", [pp.view, f3(pp)], ["week", { project: "PRJ-00001", pm: "", group: "" }]);
	check("the bar follows", pp.$group.val(), "");
	desk.forward();
	check("Forward: the month with its group again", [pp.view, f3(pp)], ["month", { project: "PRJ-00001", pm: "", group: "Field" }]);
	check("walking the entries rewrote none of them", desk.urls().slice(1, 3), [
		"/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001",
		"/desk/project-planner/calendar/month/2026-10-12?project=PRJ-00001&group=Field",
	]);
	check("and remembered nothing", desk.prefs, prefs);
	desk.back();
	desk.back();
	check("Back to the form", desk.route, ["project", "PRJ-00001"]);
	check("with no query", desk.here(), "/desk/project/PRJ-00001");
});

test("Project Planner: Utilization is a view, a route of its own with the filters in the address", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?group=Field" });
	const pp = projectPlanner(desk);
	desk.land();
	pp.go("utilization", "2026-10-12");
	check("its own screen, carrying the filters", desk.here(), "/desk/project-planner/utilization/2026-10-12?group=Field");
	check("the view is Utilization", [pp.view, pp.anchor], ["utilization", "2026-10-12"]);
	check("loaded as Utilization, for the group", pp.loads[pp.loads.length - 1], {
		view: "utilization",
		anchor: "2026-10-12",
		filters: { project: "", pm: "", group: "Field" },
	});
	pp.pick_filter("group", "Design");
	check("a filter picked there rewrites its address in place", desk.here(), "/desk/project-planner/utilization/2026-10-12?group=Design");
	check("one entry for the week, one for Utilization", desk.entries.length, 2);
	desk.back();
	check("Back: the week, with the group it had", [pp.view, f3(pp).group], ["week", "Field"]);
	desk.forward();
	check("Forward: Utilization again, with its group", [pp.view, f3(pp).group], ["utilization", "Design"]);
});

test("Project Planner: a link to Utilization opens it on the link's filters", () => {
	const desk = makeDesk({ start: "/desk/project-planner/utilization/2026-10-01?project=PRJ-00001&group=PM" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk);
	desk.land();
	check("the view and the month", [pp.view, pp.anchor], ["utilization", "2026-10-01"]);
	check("the link's filters, the others All", f3(pp), { project: "PRJ-00001", pm: "", group: "PM" });
	check("the address as it came", desk.here(), "/desk/project-planner/utilization/2026-10-01?project=PRJ-00001&group=PM");
	pp.go("week", "2026-10-01");
	check("the next calendar view keeps the project the link named", desk.here(), "/desk/project-planner/calendar/week/2026-10-01?project=PRJ-00001&group=PM");
});

test("Project Planner: malformed or unknown values are ignored", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?group=Bogus&foo=1" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk);
	desk.land();
	check("an unknown group names nothing: the remembered filters stay", f3(pp), {
		project: "PRJ-MINE",
		pm: "me@example.com",
		group: "",
	});
	check("the bad group leaves the address; a key that is not the planner's stays", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-MINE&pm=me%40example.com&foo=1");
	check("and stays in route_options, not the planner's to take", desk.frappe.route_options, { foo: "1" });

	const long = "P".repeat(141);
	desk.go("project-planner", "calendar", "week", "2026-10-12", { project: long, group: "PM" });
	check("an overlong project is ignored, the group taken", f3(pp), { project: "", pm: "", group: "PM" });
	// The router before v16.50 left an object in route_options as it was given.
	desk.frappe.route_options = { project: { nested: 1 }, pm: null };
	pp.take_route_filters();
	check("an object or a null names nothing: after a link, the person's own come back", f3(pp), {
		project: "PRJ-MINE",
		pm: "me@example.com",
		group: "",
	});
	check("and is consumed all the same", desk.frappe.route_options, null);

	// A PM the planner does not list: All once the planner has loaded, and the address says so.
	pp.answer = ANSWER;
	desk.go("project-planner", "calendar", "week", "2026-10-26", { project: "PRJ-00001", pm: "Nobody" });
	check("the project kept, the PM reset", f3(pp), { project: "PRJ-00001", pm: "", group: "" });
	check("the address follows", desk.here(), "/desk/project-planner/calendar/week/2026-10-26?project=PRJ-00001");
});

test("Project Planner: a link's project the planner does not list stays the filter", () => {
	const desk = makeDesk({ start: "/desk/project-planner?project=PRJ-ELSEWHERE" });
	const pp = projectPlanner(desk, { answer: ANSWER });
	desk.land();
	check("kept", pp.project, "PRJ-ELSEWHERE");
	check("in the address", desk.here(), "/desk/project-planner?project=PRJ-ELSEWHERE");
});

test("Project Planner: a link's filters stay one-off; a pick saves only the filter picked", () => {
	// Remembered PRJ-MINE and me@, opened from the Project form on PRJ-00001, then Group: Field.
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	Object.assign(desk.prefs, MINE);
	const last = { view: "week", project: "PRJ-MINE", pm: "me@example.com", group: "" };
	const pp = projectPlanner(desk, { p6c: { loaded: true, restored: true, last, saved_text: null, views: [] } });
	pp.own_filters = { project: "PRJ-MINE", pm: "me@example.com", group: "" };
	desk.go("project-planner", { project: "PRJ-00001" });
	check("the link's filters on screen", f3(pp), { project: "PRJ-00001", pm: "", group: "" });
	pp.p6c_save_last();
	pp.go("month", "2026-10-14");
	pp.p6c_save_last();
	check("no remembered filter", desk.prefs, MINE);
	pp.pick_filter("group", "Field");
	check("the screen shows the link's project with the picked group", f3(pp), {
		project: "PRJ-00001",
		pm: "",
		group: "Field",
	});
	pp.p6c_save_last();
	check("only the group is remembered", desk.prefs, Object.assign({}, MINE, { [PP_PREFS.group]: "Field" }));
	check(
		"last used never takes the link's project or its All PM",
		saved(desk).map((s) => [s.project, s.pm, s.group]),
		[
			["PRJ-MINE", "me@example.com", ""],
			["PRJ-MINE", "me@example.com", ""],
			["PRJ-MINE", "me@example.com", "Field"],
		]
	);
	check("the link's other filters are still the link's", Object.keys(pp.url_filters).sort(), ["all_tasks", "pm", "project"]);
	desk.go("Form", "Task", "TASK-0001");
	desk.go("project-planner");
	check("a bare address later brings back theirs, with the group they picked", f3(pp), {
		project: "PRJ-MINE",
		pm: "me@example.com",
		group: "Field",
	});
});

test("Project Planner: a link's PM the planner does not list is let go, so the next week keeps All", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk, { answer: ANSWER });
	desk.go("project-planner", "calendar", "week", "2026-10-12", { pm: "Nobody" });
	check("All once loaded", f3(pp), { project: "", pm: "", group: "" });
	check("the address says so", desk.here(), "/desk/project-planner/calendar/week/2026-10-12");
	// The › button: shift(1) is go(view, the next week).
	pp.go("week", "2026-10-19");
	check("the next week is still All, not the person's own", f3(pp), { project: "", pm: "", group: "" });
	check("on the next week", desk.here(), "/desk/project-planner/calendar/week/2026-10-19");
});

test("Project Planner: Back onto the person's own entry makes the filters theirs again", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk, { p6c: { loaded: true, restored: true, last: null, saved_text: null, views: [] } });
	desk.land();
	check("their own, in the address", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-MINE&pm=me%40example.com");
	desk.go("project-planner", "calendar", "week", "2026-10-12", { project: "PRJ-00002" });
	check("a link's", pp.url_filters !== null, true);
	desk.back();
	check("Back: their own entry", f3(pp), { project: "PRJ-MINE", pm: "me@example.com", group: "" });
	check("theirs, not a link's", pp.url_filters, null);
	pp.p6c.color_by = "pm";
	pp.p6c_save_last();
	check("so last used saves again, colors and all", saved(desk).map((s) => [s.project, s.color_by]), [["PRJ-MINE", "pm"]]);
});

test("Project Planner: an apostrophe is written as the browser reads it back, once", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12" });
	const pp = projectPlanner(desk);
	desk.land();
	pp.pick_filter("pm", "o'neil@example.com");
	check("written as %27", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?pm=o%27neil%40example.com");
	const writes = desk.replaces;
	pp.write_filter_url();
	pp.go("month", "2026-10-12");
	desk.back();
	check("read back as the same name", pp.pm, "o'neil@example.com");
	check("and never rewritten", desk.replaces, writes);
});

test("Project Planner: a bare address after a link brings back the person's own filters", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	Object.assign(desk.prefs, MINE);
	const pp = projectPlanner(desk);
	desk.go("project-planner", { project: "PRJ-00001" });
	check("the link's", f3(pp), { project: "PRJ-00001", pm: "", group: "" });
	desk.go("Form", "Task", "TASK-0001");
	desk.go("project-planner");
	check("the sidebar's bare planner: the person's own", f3(pp), { project: "PRJ-MINE", pm: "me@example.com", group: "" });
	check("and its address says so", desk.here(), "/desk/project-planner?project=PRJ-MINE&pm=me%40example.com");
	desk.back();
	desk.back();
	check("Back onto the link's entry shows the link's again", f3(pp), { project: "PRJ-00001", pm: "", group: "" });
});

test("Project Planner: no filter is left in route_options to leak into the next route", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&pm=Pat%20Lee&group=Field" });
	const pp = projectPlanner(desk);
	desk.land();
	check("consumed on landing", desk.frappe.route_options, null);
	desk.go("Form", "Task", "TASK-0001");
	check("a task opened from the planner has a clean address", desk.here(), "/desk/task/TASK-0001");
	desk.back();
	check("Back reads them again", f3(pp), { project: "PRJ-00001", pm: "Pat Lee", group: "Field" });
	check("and consumes them again", desk.frappe.route_options, null);
	desk.go("maintenance-planner", "week", "2026-10-12");
	check("another planner's path-only link stays clean", desk.here(), "/desk/maintenance-planner/week/2026-10-12");
	desk.back();
	// Whatever a page left in route_options, the planner's own screens replace it.
	desk.frappe.route_options = { stale: "1" };
	pp.go("month", "2026-10-12");
	check("the planner's own route sends only its filters", desk.here(), "/desk/project-planner/calendar/month/2026-10-12?project=PRJ-00001&pm=Pat%20Lee&group=Field");
	check("and leaves nothing behind", desk.frappe.route_options, null);
	pp.go_route("RES-1", "2026-10-13");
	check("a person's route carries them too", desk.here(), "/desk/project-planner/route/RES-1/2026-10-13?project=PRJ-00001&pm=Pat%20Lee&group=Field");
});

test("Project Planner: the last-used view replaces a bare address, and the address's filters still win", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	// P5: saved since the tabs (tabbed), so its view is a choice of tab.
	const last = { view: "month", project: "PRJ-OLD", pm: "pm@example.com", group: "PM", tabbed: true };
	const pp = projectPlanner(desk, { p6c: { loaded: true, restored: false, last, saved_text: null, views: [] } });
	desk.go("project-planner", { project: "PRJ-00001" });
	check("the month replaces the entry, the query kept", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/calendar/month/2026-10-14?project=PRJ-00001"]);
	check("the link's project, the rest All", f3(pp), { project: "PRJ-00001", pm: "", group: "" });
	check("loaded once", pp.loads.length, 1);

	// With no filter in the address, the restored ones are written into it.
	const fresh = makeDesk({ start: "/desk/project/PRJ-00001" });
	const own = projectPlanner(fresh, { p6c: { loaded: true, restored: false, last, saved_text: null, views: [] } });
	fresh.go("project-planner");
	check("the last used filters", f3(own), { project: "PRJ-OLD", pm: "pm@example.com", group: "PM" });
	check("in the address of the month", fresh.here(), "/desk/project-planner/calendar/month/2026-10-14?project=PRJ-OLD&pm=pm%40example.com&group=PM");
});

test("Project Planner: a last-used view saved before the tabs opens the Lookahead, its filters kept (P5)", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	const last = { view: "week", project: "PRJ-OLD", pm: "pm@example.com", group: "PM" };
	const pp = projectPlanner(desk, { p6c: { loaded: true, restored: false, last, saved_text: null, views: [] } });
	desk.go("project-planner");
	check("the bare address stays, with the filters written in", desk.urls(), [
		"/desk/project/PRJ-00001",
		"/desk/project-planner?project=PRJ-OLD&pm=pm%40example.com&group=PM",
	]);
	check("the Lookahead, on today's weeks", [pp.view, pp.anchor], ["lookahead", desk.today]);
	check("loaded once", pp.loads.length, 1);
});

test("Project Planner: a pick made under a drawer reaches the entry below once the kit's Back lands", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001" });
	const pp = projectPlanner(desk);
	desk.land();
	// The kit's URL-less entry while a drawer is open.
	desk.window.history.pushState({ planner_kit: "pk1" }, "");
	pp.pick_filter("group", "Field");
	check("the drawer's entry says it, its state kept", desk.entries[1], {
		url: "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&group=Field",
		state: { planner_kit: "pk1" },
	});
	desk.kit_back();
	check("the entry below until the router has had its turn", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001");
	desk.flush();
	check("then brought up to the screen", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&group=Field");
	check("still one planner entry under it", desk.index, 0);
});

test("Project Planner: never writes another page's address, or over filters it has not read", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001" });
	const pp = projectPlanner(desk);
	desk.land();
	desk.go("Form", "Task", "TASK-0001");
	pp.group = "Field";
	pp.write_filter_url();
	check("a late write on a form does nothing", desk.here(), "/desk/task/TASK-0001");
	desk.back();
	desk.frappe.route_options = { project: "PRJ-00002" };
	pp.write_filter_url();
	check("nor while the router holds a filter the page has not read", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001");
});

test("Project Planner: on the router before v16.50 the address still gets the filters", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", queryInUrl: false });
	const pp = projectPlanner(desk);
	desk.go("project-planner", "calendar", "week", "2026-10-12", { project: "PRJ-00001" });
	check("read from route_options", pp.project, "PRJ-00001");
	check("and written by the planner", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001");
	pp.go("month", "2026-10-12");
	check("carried to the next screen", desk.here(), "/desk/project-planner/calendar/month/2026-10-12?project=PRJ-00001");
	desk.back();
	check("Back reads it from the address", f3(pp), { project: "PRJ-00001", pm: "", group: "" });
});

// ------------------------------------------------------------------ Show all tasks (P2, D6)

test("Project Planner: Show all tasks is a filter of the address, and asks the server again", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&all_tasks=1" });
	const pp = projectPlanner(desk);
	desk.land();
	check("the link's: on", [pp.all_tasks, pp.filters().all_tasks], ["1", "1"]);
	check("the address is left as it came", desk.urls(), ["/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&all_tasks=1"]);
	pp.set_all_tasks(false);
	check("off leaves the address, in place", [desk.here(), desk.entries.length], ["/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001", 1]);
	check("and the week is loaded again, not just drawn", [pp.loads.length, pp.renders], [2, 0]);
	pp.set_all_tasks(true);
	check("on joins it, last, in place", desk.here(), "/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&all_tasks=1");
	check("a pick is not a screen: no history entry, and nothing in the browser's prefs", [desk.entries.length, Object.keys(desk.prefs).filter((k) => /all_tasks/.test(k))], [1, []]);
});

test("Project Planner: Show all tasks follows Back and Forward with the week it was picked on", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	const pp = projectPlanner(desk);
	desk.go("project-planner", "calendar", "week", "2026-10-12");
	pp.set_all_tasks(true);
	pp.go("month", "2026-10-12");
	check("the month carries it", desk.here(), "/desk/project-planner/calendar/month/2026-10-12?all_tasks=1");
	desk.back();
	check("Back: the week, still showing all", [pp.view, pp.all_tasks], ["week", "1"]);
	desk.forward();
	check("Forward: the month", [pp.view, pp.all_tasks], ["month", "1"]);
	check("every landing asked for the week or month it shows", pp.loads.map((l) => l.view), ["week", "week", "month", "week", "month"]);
});

test("Project Planner: a link that does not name Show all tasks is the default, and the person's own comes back", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	const pp = projectPlanner(desk);
	pp.all_tasks = "1";
	pp.own_filters = Object.assign(pp.filters());
	desk.go("project-planner", "calendar", "week", "2026-10-12", { project: "PRJ-00001" });
	check("a link naming only the project is the whole filter: phase and crew work", pp.all_tasks, "");
	desk.go("project-planner", "calendar", "week", "2026-10-19");
	check("a bare address after it brings back theirs", pp.all_tasks, "1");
});

test("Project Planner: a value Show all tasks cannot take is ignored", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/week/2026-10-12?all_tasks=banana" });
	const pp = projectPlanner(desk);
	pp.all_tasks = "1";
	pp.own_filters = Object.assign(pp.filters());
	desk.land();
	check("nothing named: what is on screen stays", pp.all_tasks, "1");
	for (const [text, want] of [["true", "1"], ["TRUE", "1"], ["1", "1"], ["0", ""], ["false", ""], ["", ""]]) {
		check(`${JSON.stringify(text)}`, pp.filter_from_address("all_tasks", text), want);
	}
	check("an object", pp.filter_from_address("all_tasks", { a: 1 }), null);
});

// ------------------------------------------------------------------ Maintenance Planner

// ------------------------------------------------------------------ P5: the Lookahead and the Calendar tab
//
// The planner UX redesign's P5: the Lookahead is the home tab (/lookahead/<date>[/<weeks>]), the week
// and month views moved under the Calendar tab (/calendar/week|month/<date>), and the old /week and
// /month addresses are replaced by their new ones, never pushed, with the filters and any other keys.

const weeks_of = (pp) => pp.ppl_state().weeks;

test("P5: an old /week address is replaced by /calendar/week, its filters and other keys kept, no new entry", () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	const pp = projectPlanner(desk);
	desk.go("project-planner", "week", "2026-10-12", { project: "PRJ-00001", foo: "1" });
	check("one entry for the planner, at its new address", desk.urls(), [
		"/desk/project/PRJ-00001",
		"/desk/project-planner/calendar/week/2026-10-12?project=PRJ-00001&foo=1",
	]);
	check("the week, on that day, with the link's project", [pp.view, pp.anchor, f3(pp).project], ["week", "2026-10-12", "PRJ-00001"]);
	check("loaded once, after the redirect", pp.loads.map((l) => l.view), ["week"]);
	desk.back();
	check("Back leaves the planner, as it would have from the old address", desk.here(), "/desk/project/PRJ-00001");
});

test("P5: an old /month address lands on the Calendar tab's month", () => {
	const desk = makeDesk({ start: "/desk/project-planner/month/2026-10-01?group=Field" });
	const pp = projectPlanner(desk);
	desk.land();
	check("replaced in place", desk.urls(), ["/desk/project-planner/calendar/month/2026-10-01?group=Field"]);
	check("the month with its group", [pp.view, pp.anchor, f3(pp).group], ["month", "2026-10-01", "Field"]);
	check("the Calendar tab is the one on screen", pp.ppl_tab(), "calendar");
});

test("P5: /calendar/<date> alone is the calendar view used last; a bad date is today", () => {
	const desk = makeDesk({ start: "/desk/project-planner/calendar/month/2026-10-01" });
	const pp = projectPlanner(desk);
	desk.land();
	desk.go("project-planner", "calendar", "2026-10-20");
	check("the month again, on that day", [pp.view, pp.anchor], ["month", "2026-10-20"]);
	desk.go("project-planner", "lookahead", "not-a-date");
	check("a bad date is today", [pp.view, pp.anchor], ["lookahead", desk.today]);
});

test("P5: the range is a screen: Back and Forward step through the weeks shown and the days", () => {
	const desk = makeDesk({ start: "/desk/project-planner/lookahead/2026-10-14?project=PRJ-00001" });
	const pp = projectPlanner(desk);
	desk.land();
	check("three weeks by default", [pp.view, weeks_of(pp)], ["lookahead", 3]);
	pp.ppl_pick_weeks(4);
	check("four weeks is a new entry, the filters carried", desk.here(), "/desk/project-planner/lookahead/2026-10-14/4?project=PRJ-00001");
	check("remembered for the next bare /project-planner", desk.prefs.ee_project_planner_lookahead_weeks, "4");
	pp.shift(1);
	check("› steps one week, keeping four", desk.here(), "/desk/project-planner/lookahead/2026-10-21/4?project=PRJ-00001");
	pp.ppl_calendar_tab();
	check("the Calendar tab: the week holding the day", desk.here(), "/desk/project-planner/calendar/week/2026-10-21?project=PRJ-00001");
	desk.back();
	check("Back: the four weeks from the 21st", [pp.view, pp.anchor, weeks_of(pp)], ["lookahead", "2026-10-21", 4]);
	desk.back();
	desk.back();
	check("Back twice: three weeks, as that entry showed", [pp.view, pp.anchor, weeks_of(pp)], ["lookahead", "2026-10-14", 3]);
	desk.forward();
	check("Forward: four again", [pp.view, weeks_of(pp)], ["lookahead", 4]);
	check("every entry kept its address", desk.urls(), [
		"/desk/project-planner/lookahead/2026-10-14?project=PRJ-00001",
		"/desk/project-planner/lookahead/2026-10-14/4?project=PRJ-00001",
		"/desk/project-planner/lookahead/2026-10-21/4?project=PRJ-00001",
		"/desk/project-planner/calendar/week/2026-10-21?project=PRJ-00001",
	]);
	check("an unknown length in the address is three weeks", (desk.go("project-planner", "lookahead", "2026-10-14", "5"), weeks_of(pp)), 3);
});

test("P5: the tabs are routes, each with its own entry; the Lookahead loads get_lookahead's window", () => {
	const desk = makeDesk({ start: "/desk/project-planner/lookahead/2026-10-14" });
	const pp = projectPlanner(desk);
	desk.land();
	const range = pp.range();
	check("whole weeks from the Sunday", [range.start.format("YYYY-MM-DD"), range.end.format("YYYY-MM-DD")], ["2026-10-11", "2026-10-31"]);
	pp.go("crew", pp.anchor);
	pp.go("week", pp.anchor);
	pp.go("month", pp.anchor);
	check("one entry each", desk.urls(), [
		"/desk/project-planner/lookahead/2026-10-14",
		"/desk/project-planner/crew/2026-10-14",
		"/desk/project-planner/calendar/week/2026-10-14",
		"/desk/project-planner/calendar/month/2026-10-14",
	]);
	check("the same screen again only loads", (pp.go("month", pp.anchor), desk.entries.length), 4);
	check("what each landing loaded", pp.loads.map((l) => l.view), ["lookahead", "crew", "week", "month", "month"]);
});

const TECHS = {
	technicians: [
		{ user: "tech1@example.com", name: "Tech One", enabled: true },
		{ user: "tech2@example.com", name: "Tech Two", enabled: true },
	],
	visits: [],
	unscheduled: [],
	projected: [],
};

test("Maintenance Planner: a link with a technician opens the planner on them", () => {
	const desk = makeDesk({ start: "/desk/maintenance-planner/week/2026-10-12?technician=tech2%40example.com" });
	desk.prefs[MP_TECH] = "tech1@example.com";
	const mp = maintenancePlanner(desk);
	desk.land();
	check("the link's technician", mp.technician, "tech2@example.com");
	check("in the bar", mp.$tech.val(), "tech2@example.com");
	check("loaded on that week", mp.loads, [{ view: "week", anchor: "2026-10-12", technician: "tech2@example.com" }]);
	check("route_options consumed", desk.frappe.route_options, null);
	check("the address as it came", desk.here(), "/desk/maintenance-planner/week/2026-10-12?technician=tech2%40example.com");
	check("not remembered", desk.prefs, { [MP_TECH]: "tech1@example.com" });
	mp.p6c = { loaded: true, restored: true, last: null, saved_text: null, views: [], color_by: "default" };
	mp.p6c_save_last();
	check("last used keeps the person's own technician", saved(desk).map((s) => s.technician), ["tech1@example.com"]);
});

test("Maintenance Planner: picking a technician rewrites the address in place", () => {
	const desk = makeDesk({ start: "/desk/maintenance-planner" });
	const mp = maintenancePlanner(desk);
	desk.land();
	check("All: a bare address", desk.here(), "/desk/maintenance-planner");
	mp.pick_filter("__none__");
	check("Unassigned", desk.here(), "/desk/maintenance-planner?technician=__none__");
	mp.pick_filter("tech1@example.com");
	check("a technician", desk.here(), "/desk/maintenance-planner?technician=tech1%40example.com");
	check("no entries added", desk.entries.length, 1);
	check("remembered", desk.prefs[MP_TECH], "tech1@example.com");
	mp.pick_filter("");
	check("All again", desk.here(), "/desk/maintenance-planner");
});

test("Maintenance Planner: Back and Forward walk months and weeks with the technician each showed", () => {
	const desk = makeDesk({ start: "/desk/maintenance-day-board" });
	const mp = maintenancePlanner(desk);
	desk.go("maintenance-planner", "month", "2026-10-01");
	mp.pick_filter("tech1@example.com");
	mp.go("week", "2026-10-12");
	mp.pick_filter("tech2@example.com");
	mp.go("crew", "2026-10-12");
	check("one entry per screen", desk.urls(), [
		"/desk/maintenance-day-board",
		"/desk/maintenance-planner/month/2026-10-01?technician=tech1%40example.com",
		"/desk/maintenance-planner/week/2026-10-12?technician=tech2%40example.com",
		"/desk/maintenance-planner/crew/2026-10-12?technician=tech2%40example.com",
	]);
	desk.back();
	desk.back();
	check("Back to the month, on its technician", [mp.view, mp.technician], ["month", "tech1@example.com"]);
	desk.forward();
	check("Forward to the week, on its own", [mp.view, mp.technician], ["week", "tech2@example.com"]);
	desk.back();
	desk.back();
	check("Back to the Day Board", desk.here(), "/desk/maintenance-day-board");
});

test("Maintenance Planner: an unknown technician is ignored once the planner has loaded", () => {
	const desk = makeDesk({ start: "/desk/maintenance-planner/week/2026-10-12?technician=gone%40example.com&foo=1" });
	const mp = maintenancePlanner(desk, { answer: TECHS });
	desk.land();
	check("All", mp.technician, "");
	check("the address follows, other keys kept", desk.here(), "/desk/maintenance-planner/week/2026-10-12?foo=1");
	const long = "t".repeat(141);
	desk.go("maintenance-planner", "week", "2026-10-19", { technician: long });
	check("an overlong value names nothing", mp.technician, "");
});

test("Maintenance Planner: a link's technician the planner does not list is let go, so the next week keeps All", () => {
	const desk = makeDesk({ start: "/desk/maintenance-day-board" });
	desk.prefs[MP_TECH] = "tech1@example.com";
	const mp = maintenancePlanner(desk, { answer: TECHS });
	desk.go("maintenance-planner", "week", "2026-10-12", { technician: "gone@example.com" });
	check("All once loaded", mp.technician, "");
	check("no longer the link's", mp.url_filters, null);
	// The › button: shift(1) is go(view, the next week).
	mp.go("week", "2026-10-19");
	check("the next week is still All, not tech1", mp.technician, "");
	check("on the next week", desk.here(), "/desk/maintenance-planner/week/2026-10-19");
});

test("Maintenance Planner: a reload keeps the address's technician over the last used", () => {
	const desk = makeDesk({ start: "/desk/maintenance-planner/month/2026-10-01?technician=tech2%40example.com" });
	desk.prefs[MP_TECH] = "tech1@example.com";
	const last = { view: "month", technician: "tech1@example.com" };
	const p6c = () => ({ loaded: true, restored: false, last, saved_text: null, views: [], color_by: "default" });
	maintenancePlanner(desk, { p6c: p6c() });
	desk.land();
	desk.reload();
	const mp = maintenancePlanner(desk, { p6c: p6c() });
	desk.land();
	check("the address wins", mp.technician, "tech2@example.com");
	check("the remembered one is untouched", desk.prefs[MP_TECH], "tech1@example.com");
});

test("Maintenance Planner: no technician leaks into the next route, and a bare address brings back their own", () => {
	const desk = makeDesk({ start: "/desk/maintenance-planner/week/2026-10-12?technician=tech2%40example.com" });
	desk.prefs[MP_TECH] = "tech1@example.com";
	const mp = maintenancePlanner(desk);
	desk.land();
	desk.go("project-planner", "route", "RES-1", "2026-10-12");
	check("the route icon's Project Planner address is clean", desk.here(), "/desk/project-planner/route/RES-1/2026-10-12");
	desk.go("maintenance-planner");
	check("the bare planner: their own technician", mp.technician, "tech1@example.com");
	check("in its address", desk.here(), "/desk/maintenance-planner?technician=tech1%40example.com");
});

// ------------------------------------------------------------------ the kit's stylesheet, once

test("assets.js v16.50: two requires in one tick link a stylesheet twice (the bug)", async () => {
	const desk = makeDesk();
	desk.frappe.require(["planner_kit.bundle.js", "planner_kit.bundle.css"]);
	desk.frappe.require(["planner_kit.bundle.js", "planner_kit.bundle.css"]);
	await settle();
	check("two <link>s", desk.links().length, 2);
	desk.frappe.require(["planner_kit.bundle.js", "planner_kit.bundle.css"]);
	await settle();
	check("a require once it has loaded adds none", desk.links().length, 2);
});

test("Project Planner: 6A and 6D ask for the kit in one tick, and it is linked once", async () => {
	const desk = makeDesk({ start: "/desk/project-planner" });
	const pp = projectPlanner(desk);
	const ready = [];
	pp.p6a_require(() => ready.push("6A"));
	pp.p6a_require(() => ready.push("6D"));
	await settle();
	check("one <link>, the hashed bundle", desk.links(), ["/assets/erpnext_enhancements/dist/css/planner_kit.bundle.DEF456.css"]);
	check("one <script>", desk.scripts(), ["/assets/erpnext_enhancements/dist/js/planner_kit.bundle.ABC123.js"]);
	check("both called back, 6A first", ready, ["6A", "6D"]);
	const mp = maintenancePlanner(desk);
	mp.p6a_require(() => ready.push("MP 6A"));
	mp.p6a_require(() => ready.push("MP 6D"));
	await settle();
	check("the Maintenance Planner after it adds nothing", [desk.links().length, desk.scripts().length], [1, 1]);
	check("and is called back", ready.slice(2), ["MP 6A", "MP 6D"]);
});

test("Maintenance Planner: opened while the Project Planner's request is on its way, still one link", async () => {
	const desk = makeDesk({ start: "/desk/maintenance-planner" });
	const pp = projectPlanner(desk);
	const mp = maintenancePlanner(desk);
	const ready = [];
	pp.p6a_require(() => ready.push("pp"));
	mp.p6a_require(() => ready.push("mp"));
	mp.p6a_require(() => ready.push("mp 6D"));
	await settle();
	check("one <link>", desk.links().length, 1);
	check("one <script>", desk.scripts().length, 1);
	check("everyone called back", ready, ["pp", "mp", "mp 6D"]);
});

// The Opportunity board's side panel (v1.675.0) asks for the kit and its stylesheet itself, in
// OB_PANEL_ASSETS, once per board. assets.js's `_executed` is what keeps either order to one link.
const OB_PANEL_ASSETS = ["planner_kit.bundle.js", "planner_kit.bundle.css", "guided_entry.bundle.js", "guided_entry.bundle.css"];

test("The Opportunity board's side panel first, then both planners: still one link", async () => {
	const desk = makeDesk({ start: "/desk/opportunity-board" });
	desk.frappe.require(OB_PANEL_ASSETS);
	await settle();
	const pp = projectPlanner(desk);
	pp.p6a_require(() => null);
	pp.p6a_require(() => null);
	const mp = maintenancePlanner(desk);
	mp.p6a_require(() => null);
	await settle();
	check("one planner_kit <link>", desk.links().filter((href) => href.includes("planner_kit")).length, 1);
	check("one planner_kit <script>", desk.scripts().filter((src) => src.includes("planner_kit")).length, 1);
});

test("A planner first, then the Opportunity board's side panel: still one link", async () => {
	const desk = makeDesk({ start: "/desk/project-planner" });
	const pp = projectPlanner(desk);
	pp.p6a_require(() => null);
	pp.p6a_require(() => null);
	await settle();
	desk.frappe.require(OB_PANEL_ASSETS);
	await settle();
	check("one planner_kit <link>", desk.links().filter((href) => href.includes("planner_kit")).length, 1);
	check("one planner_kit <script>", desk.scripts().filter((src) => src.includes("planner_kit")).length, 1);
});

// ------------------------------------------------------------------ run

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (error) {
			failures += 1;
			console.log(`  ERROR ${error && error.stack}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	if (failures) {
		console.log(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed`);
})();
