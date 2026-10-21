#!/usr/bin/env node
/**
 * Today on the phone (P8 of the Planner UX redesign, docs/planner-ux-redesign.md §4.4), run against a
 * model of Frappe v16.50's router.
 *
 * Loads the REAL Project Planner script into a vm context over a fake Desk and drives its real routing
 * (the landing rule, go_today, go_my_week, Back and Forward), its real set_mode and its real
 * render_today over a recording jQuery. Pins what a phone must never get wrong:
 *
 *   - the landing: a bare /project-planner on a phone opens Today for a person who is not a scheduler,
 *     replacing the bare entry (Back leaves the planner), over "last used"; a desktop, a scheduler and
 *     a link that names a filter keep the screen they had
 *   - history: Today, its day arrows, "This week" and "Planner" each take one entry; Back and Forward
 *     step through them; Block my time takes none (the kit's form owns its own Back)
 *   - Today is read-only: its only write is the personal block, through the kit, for the person alone
 *   - distances are miles; every value a person typed is escaped; a Fountain Move task is marked
 *
 * Run: node scripts/test_planner_today.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const PAGE = path.join(APP, "project_enhancements", "page", "project_planner", "project_planner.js");

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

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
	for (let i = 0; i < 8; i++) await tick();
}

// ------------------------------------------------------------------ a recording jQuery

// An element remembers its tag, classes, text, html, attributes, handlers and children; enough to find
// a button by its text and click it, and to read what a render wrote.
function makeJQuery() {
	function parse(markup) {
		const m = /^<(\w+)(?:\s+class="([^"]*)")?[^>]*>([^<]*)/.exec(markup);
		return m ? { tag: m[1], classes: (m[2] || "").split(/\s+/).filter(Boolean), text: m[3] } : { tag: "div", classes: [] };
	}
	function El(spec) {
		this.tag = spec.tag;
		this.classes = new Set(spec.classes);
		this.children = [];
		this.attrs = {};
		this.handlers = {};
		this.textValue = spec.text || "";
		this.htmlValue = "";
		this.shown = true;
	}
	Object.assign(El.prototype, {
		appendTo(parent) {
			parent.children.push(this);
			return this;
		},
		prependTo(parent) {
			parent.children.unshift(this);
			return this;
		},
		append(child) {
			this.children.push(child);
			return this;
		},
		empty() {
			this.children = [];
			this.textValue = "";
			this.htmlValue = "";
			return this;
		},
		text(value) {
			if (value === undefined) return this.textValue;
			this.textValue = String(value);
			return this;
		},
		html(value) {
			if (value === undefined) return this.htmlValue;
			this.htmlValue = String(value);
			return this;
		},
		attr(key, value) {
			if (value === undefined) return this.attrs[key];
			this.attrs[key] = value;
			return this;
		},
		on(event, fn) {
			this.handlers[event] = fn;
			return this;
		},
		addClass(name) {
			String(name)
				.split(/\s+/)
				.forEach((n) => n && this.classes.add(n));
			return this;
		},
		removeClass(name) {
			this.classes.delete(name);
			return this;
		},
		toggleClass(name, on) {
			return on ? this.addClass(name) : this.removeClass(name);
		},
		hide() {
			this.shown = false;
			return this;
		},
		show() {
			this.shown = true;
			return this;
		},
		toggle(on) {
			this.shown = on === undefined ? !this.shown : !!on;
			return this;
		},
		is() {
			return this.shown;
		},
		val(value) {
			if (value === undefined) return this.attrs.value || "";
			this.attrs.value = value;
			return this;
		},
		// A selector inside the html string: a recorder, so a handler can be attached and ignored.
		find() {
			return new El({ tag: "find", classes: [] });
		},
		click() {
			if (this.handlers.click) this.handlers.click({ currentTarget: this });
		},
		all() {
			return [this].concat(...this.children.map((child) => child.all()));
		},
	});
	const $ = (arg) => (arg instanceof El ? arg : new El(typeof arg === "string" && arg[0] === "<" ? parse(arg) : { tag: "div", classes: [] }));
	$.El = El;
	return $;
}

// The element in a render tree whose own text is `text`.
const byText = (root, text) => root.all().find((el) => el.textValue === text);

// ------------------------------------------------------------------ the Desk and its router

function makeDesk(options) {
	const o = Object.assign({}, options || {});
	const desk = {
		entries: [{ url: o.start || "/desk/project/PRJ-00001", state: null }],
		index: 0,
		today: o.today || "2026-10-21",
		route: [],
		calls: [],
		answers: {},
		handlers: {},
		prefs: {},
		timers: [],
		listeners: [],
		replaces: 0,
		phone: o.phone !== false,
		roles: o.roles || ["Maintenance User"],
		opened: [],
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
	const history = {
		get state() {
			return current().state;
		},
		get length() {
			return desk.entries.length;
		},
		pushState(state, _title, url) {
			desk.entries = desk.entries.slice(0, desk.index + 1);
			desk.entries.push({ url: url == null ? current().url : url, state });
			desk.index += 1;
		},
		replaceState(state, _title, url) {
			desk.replaces += 1;
			desk.entries[desk.index] = { url: url == null ? current().url : url, state };
		},
	};
	const isPlainObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
	const frappe = {
		route_options: null,
		route_flags: {},
		pages: new Proxy(
			{},
			{
				get(target, key) {
					if (!(key in target)) target[key] = {};
					return target[key];
				},
			}
		),
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		datetime: { get_today: () => desk.today },
		utils: {
			escape_html: (value) =>
				String(value == null ? "" : value)
					.replace(/&/g, "&amp;")
					.replace(/</g, "&lt;")
					.replace(/>/g, "&gt;")
					.replace(/"/g, "&quot;")
					.replace(/'/g, "&#39;"),
		},
		user: { has_role: (role) => desk.roles.includes(role) },
		// Server calls are recorded and answered from desk.answers[method] (a function or a value).
		call(opts) {
			desk.calls.push({ method: opts.method.split(".").pop(), args: opts.args || {} });
			const answer = desk.answers[opts.method.split(".").pop()];
			const message = typeof answer === "function" ? answer(opts.args || {}) : answer;
			return Promise.resolve({ message: message === undefined ? {} : message });
		},
		show_alert() {},
		get_route: () => desk.route,
		set_route(...args) {
			const segments = [];
			args.forEach((part) => {
				if (isPlainObject(part)) frappe.route_options = part;
				else segments.push(encodeURIComponent(String(part)));
			});
			const options = frappe.route_options || {};
			const query = Object.entries(options)
				.map(([key, value]) => `${key}=${encodeURIComponent(String(value))}`)
				.join("&");
			const pathname = "/desk/" + segments.join("/");
			const search = query ? `?${query}` : "";
			if (location.pathname !== pathname || location.search !== search) {
				history[frappe.route_flags.replace_route ? "replaceState" : "pushState"](null, null, pathname + search);
				route();
			}
			frappe.route_flags = {};
			return Promise.resolve();
		},
	};

	function route() {
		desk.route = location.pathname
			.replace(/^\/desk\/?/, "")
			.split("/")
			.filter(Boolean)
			.map(decodeURIComponent);
		if (!frappe.route_options) frappe.route_options = {};
		new URLSearchParams(location.search).forEach((value, key) => {
			frappe.route_options[key] = value;
		});
		const show = desk.handlers[desk.route[0]];
		if (show) show();
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
		matchMedia: (query) => ({
			matches: /max-width:\s*(\d+)px/.test(query) ? desk.phone && Number(/max-width:\s*(\d+)px/.exec(query)[1]) >= 767 : false,
		}),
		open: (...args) => desk.opened.push(args),
		addEventListener(type, fn) {
			if (type === "popstate") desk.listeners.push(fn);
		},
	};
	Object.assign(desk, {
		frappe,
		window,
		urls: () => desk.entries.map((entry) => entry.url),
		land: route,
		go(...args) {
			return frappe.set_route(...args);
		},
		back() {
			desk.index -= 1;
			route();
		},
		forward() {
			desk.index += 1;
			route();
		},
		flush() {
			desk.timers.splice(0).forEach((fn) => fn());
		},
		called: (method) => desk.calls.filter((c) => c.method === method),
	});
	return desk;
}

function translate(text, args) {
	return String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] !== undefined ? String(args[i]) : ""));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// moment, as far as the page asks of it here: a strict YYYY-MM-DD parse, add(n, "days"), and format().
function fakeMoment(value) {
	const text = String(value == null ? "" : value);
	const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
	const date = m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) : new Date(NaN);
	const valid = !!m && date.getUTCMonth() === +m[2] - 1 && date.getUTCDate() === +m[3];
	const api = {
		isValid: () => valid,
		add: (n) => fakeMoment(new Date(date.getTime() + n * 86400000).toISOString().slice(0, 10)),
		format: (fmt) =>
			String(fmt || "YYYY-MM-DD").replace(/YYYY|dddd|ddd|MMM|MM|DD|D/g, (token) => {
				const tokens = {
					YYYY: String(date.getUTCFullYear()),
					dddd: DAYS[date.getUTCDay()],
					ddd: DAYS[date.getUTCDay()].slice(0, 3),
					MMM: MONTHS[date.getUTCMonth()],
					MM: String(date.getUTCMonth() + 1).padStart(2, "0"),
					DD: String(date.getUTCDate()).padStart(2, "0"),
					D: String(date.getUTCDate()),
				};
				return tokens[token];
			}),
	};
	return api;
}

function load(desk) {
	const $ = makeJQuery();
	const sandbox = {
		frappe: desk.frappe,
		window: desk.window,
		$,
		__: translate,
		moment: fakeMoment,
		console,
		setTimeout: (fn) => {
			desk.timers.push(fn);
			return desk.timers.length;
		},
		clearTimeout: () => {},
		document: { hidden: false, getElementById: () => null, addEventListener() {}, body: { contains: () => true } },
		Promise,
		URLSearchParams,
		encodeURIComponent,
	};
	const context = vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(PAGE, "utf-8"), context, { filename: PAGE });
	return context;
}

// ------------------------------------------------------------------ the page

const KIT_BLOCKS = () => {
	const forms = [];
	return {
		forms,
		blocks: {
			form: (opts) => forms.push(opts),
			notes_html: () => "",
			chip_html: () => "",
		},
	};
};

// The Project Planner with its drawing parts stood in for, and its real routing, set_mode and Today.
function planner(desk, init) {
	const context = load(desk);
	const ProjectPlanner = vm.runInContext("ProjectPlanner", context);
	const $ = context.$;
	const pp = Object.create(ProjectPlanner.prototype);
	const kit = KIT_BLOCKS();
	Object.assign(pp, {
		view: "week",
		anchor: desk.today,
		request: 0,
		loads: [],
		renders: 0,
		kit,
		p6c: { loaded: true, restored: true, last: null, saved_text: null, views: [], color_by: "default" },
		project: "",
		pm: "",
		group: "",
		show_foreign: true,
		all_tasks: "",
		panel_open: true,
	});
	Object.assign(pp, {
		$body: $("<div></div>"),
		$toolbar: $("<div></div>"),
		$trays: $("<div></div>"),
		$grid_wrap: $("<div></div>"),
		$hint: $("<div></div>"),
		$route: $("<div></div>"),
		$panel: $("<div></div>"),
		$draft_bar: $("<div></div>"),
		$project: $(),
		$pm: $(),
		$group: $(),
		$my_week: $("<div></div>"),
	});
	pp.$today = $("<div></div>").hide();
	pp.own_filters = pp.filters();
	pp.url_filters = null;
	Object.assign(pp, init || {});
	pp.tb_render = () => {};
	pp.p6c_kit = () => null;
	pp.p6c_sync_controls = () => {};
	pp.p6d_kit = () => kit;
	pp.clear_route_map = () => {};
	// The calendar's loads and the other screens are recorded, not drawn.
	pp.load = () => pp.loads.push({ view: pp.view, anchor: pp.anchor });
	pp.load_route = () => pp.loads.push({ view: "route", resource: pp.route_resource });
	pp.load_my_week = () => pp.loads.push({ view: "my-week", anchor: pp.my_week_date });
	pp.watch_filter_url();
	desk.handlers["project-planner"] = () => pp.handle_route();
	return pp;
}

const ME = {
	resource: "RES-1",
	label: "Austin Healey",
	date: "2026-10-21",
	today: "2026-10-21",
	tomorrow: "2026-10-22",
	can_schedule: false,
	blocks: [],
	day_notes: {},
	route: { stops: [], maps_url: null, maps_key: "" },
	days: [
		{
			date: "2026-10-21",
			off: null,
			travel: null,
			drive_minutes: 100,
			miles: 62.1,
			items: [
				{
					kind: "task",
					ref: "TASK-1",
					order: 1,
					label: "Excavate basin",
					project_title: "Riverwalk Plaza",
					arrive: "08:10",
					leave_by: "07:35",
					drive_minutes: 35,
					miles: 12.4,
					hours: 8,
					address: "123 Main St",
					maps_url: "https://www.google.com/maps/search/?api=1&query=123+Main+St",
					crew: ["Jesse G"],
					equipment: ["Skid steer"],
					weather: ["Wind 28 mph"],
					day_of: [2, 3],
					located: true,
					fountain_move: false,
					notes: "Shop meeting 7 am",
				},
			],
		},
		{ date: "2026-10-22", off: null, travel: null, drive_minutes: 0, items: [] },
	],
};

const phoneUser = (desk) => desk.roles.splice(0, desk.roles.length, "Maintenance User");

// ------------------------------------------------------------------ the pure pieces

test("clock text: 24-hour times as people read them", () => {
	const ctx = load(makeDesk());
	const clock = vm.runInContext("pp8_clock", ctx);
	check("morning", clock("07:35"), "7:35 am");
	check("noon", clock("12:00"), "12:00 pm");
	check("afternoon", clock("17:05"), "5:05 pm");
	check("midnight", clock("00:10"), "12:10 am");
	check("past midnight keeps counting", clock("25:10"), "1:10 am");
	check("nothing", clock(null), "");
});

test("a stop: leave and arrival, the work, miles, escaped", () => {
	const ctx = load(makeDesk());
	const html = vm.runInContext("pp8_stop_html", ctx);
	const stop = ME.days[0].items[0];
	const text = html(stop);
	check("leave then arrive", text.includes("Leave 7:35 am · Arrive 8:10 am"), true);
	check("the site is the project", text.includes('<div class="pp-td-site">Riverwalk Plaza</div>'), true);
	check("the work and which day of its days", text.includes("Excavate basin · day 2/3 · 8h"), true);
	check("miles, never km", text.includes("12.4 mi") && !/\bkm\b/.test(text), true);
	check("the address links to Maps and opens without an opener", text.includes('rel="noopener noreferrer"'), true);
	check("crew, equipment and weather", ["With Jesse G", "Skid steer", "Wind 28 mph"].every((s) => text.includes(s)), true);
	check("the note", text.includes("Shop meeting 7 am"), true);

	const visit = html({ kind: "visit", order: 2, label: "Sequoia fountain", hours: 2, arrive: "11:00", located: true });
	check("a visit says visit and its hours", visit.includes("Visit, 2h"), true);
	check("a visit's site is its label", visit.includes('<div class="pp-td-site">Sequoia fountain</div>'), true);

	const hostile = html({
		kind: "task",
		order: 1,
		label: '<img src=x onerror="boom()">',
		project_title: "<b>P</b>",
		address: "<script>x</script>",
		maps_url: "javascript:alert(1)",
		crew: ["<i>c</i>"],
		notes: "<u>n</u>",
		located: false,
	});
	check("nothing a person typed is markup", /<(img|script|b>|i>|u>)/.test(hostile), false);
	check("only a Google Maps URL becomes a link", hostile.includes("<a "), false);
	check("a stop with no location says so by class", hostile.includes("pp-td-unlocated"), true);

	const move = html(Object.assign({}, stop, { fountain_move: true }));
	check("a Fountain Move task is marked", move.includes(">Move</span>"), true);
	check("and offers nothing to edit", /draggable|data-task|data-open-task|onclick/.test(move), false);
});

// ------------------------------------------------------------------ the landing

test("landing: a phone, nothing saved yet, a bare address -> Today, in place", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	desk.answers.get_today = ME;
	const pp = planner(desk);
	desk.go("project-planner");
	await settle();
	check("the address is Today on today's date", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/today/2026-10-21"]);
	check("the bare entry was replaced, not kept", desk.replaces, 1);
	check("asked for today", desk.called("get_today").map((c) => c.args.date), ["2026-10-21"]);
	check("Back leaves the planner", (desk.back(), desk.urls()[desk.index]), "/desk/project/PRJ-00001");
	check("the planner was not loaded", pp.loads, []);
});

test("landing: someone the planner does not list gets the planner, in place", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	desk.answers.get_today = { resource: null, message: "not listed", days: [], date: "2026-10-21" };
	const pp = planner(desk);
	desk.go("project-planner");
	await settle();
	check("the week, not a note", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/week/2026-10-21"]);
	check("the week loaded", pp.loads, [{ view: "week", anchor: "2026-10-21" }]);
	// A deliberate visit to Today by someone unlisted still says why there is nothing.
	const desk2 = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk2.answers.get_today = { resource: null, message: "not listed", days: [], date: "2026-10-21" };
	const pp2 = planner(desk2);
	desk2.land();
	await settle();
	check("stays on Today with the note", [desk2.urls(), pp2.$today.all().some((el) => el.textValue === "not listed")], [["/desk/project-planner/today/2026-10-21"], true]);
});

test("landing: a phone goes back to the screen used last, not to Today", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	desk.answers.get_today = ME;
	const last = { view: "crew" };
	const pp = planner(desk, { p6c: { loaded: true, restored: false, last, saved_text: null, views: [], color_by: "default" } });
	desk.go("project-planner");
	await settle();
	check("the crew view, in place of the bare entry", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/crew/2026-10-21"]);
	check("the planner loaded it", pp.loads, [{ view: "crew", anchor: "2026-10-21" }]);
	check("no Today request", desk.called("get_today"), []);
});

test("landing: a saved week view (the default) stays bare and is not Today", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	desk.answers.get_today = ME;
	const pp = planner(desk, { p6c: { loaded: true, restored: false, last: { view: "week" }, saved_text: null, views: [], color_by: "default" } });
	desk.go("project-planner");
	await settle();
	check("the address is left", desk.urls()[desk.index], "/desk/project-planner");
	check("the week loads", pp.loads, [{ view: "week", anchor: "2026-10-21" }]);
	check("no Today request", desk.called("get_today"), []);
});

test("landing: saved filters alone do not stop the first-time Today", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	desk.answers.get_today = ME;
	planner(desk, { p6c: { loaded: true, restored: false, last: { project: "PRJ-1" }, saved_text: null, views: [], color_by: "default" } });
	desk.go("project-planner");
	await settle();
	// The remembered filters ride in the address as they do on every planner screen.
	check("Today", desk.urls()[desk.index].split("?")[0], "/desk/project-planner/today/2026-10-21");
});

test("landing: it waits for the saved views before it decides", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	desk.answers.get_today = ME;
	desk.answers.get_views = { last: { view: "month" }, views: [] };
	const pp = planner(desk, { p6c: { loaded: false, restored: false, last: null, saved_text: null, views: [], color_by: "default" } });
	let release;
	pp.p6c_fetch_views = () =>
		new Promise((resolve) => {
			release = () => {
				pp.p6c.loaded = true;
				pp.p6c.last = { view: "month" };
				resolve();
			};
		});
	desk.go("project-planner");
	await settle();
	check("nothing decided while the views are on their way", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner"]);
	check("no Today flash", desk.called("get_today"), []);
	release();
	await settle();
	check("then the month they used last", desk.urls()[desk.index], "/desk/project-planner/month/2026-10-21");
});

test("landing: a desktop keeps the screen it had", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp = planner(desk);
	desk.go("project-planner");
	await settle();
	check("the bare address is left", desk.urls()[desk.index], "/desk/project-planner");
	check("the week loads", pp.loads, [{ view: "week", anchor: "2026-10-21" }]);
	check("no Today request", desk.called("get_today"), []);
});

test("landing: a scheduler on a phone is no different: Today the first time", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", roles: ["Projects User"] });
	desk.answers.get_today = ME;
	planner(desk);
	desk.go("project-planner");
	await settle();
	check("Today, in place", [desk.urls(), desk.replaces], [["/desk/project/PRJ-00001", "/desk/project-planner/today/2026-10-21"], 1]);
});

test("landing: a link that names a filter is the sender's", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	const pp = planner(desk);
	desk.go("project-planner", { project: "PRJ-00001" });
	await settle();
	check("the planner opens on it", pp.loads.length, 1);
	check("no Today request", desk.called("get_today"), []);
});

test("landing: a screen the address names is never replaced", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	const pp = planner(desk);
	desk.go("project-planner", "week", "2026-10-12");
	await settle();
	check("the week the address names", pp.loads, [{ view: "week", anchor: "2026-10-12" }]);
});

// ------------------------------------------------------------------ the screen and its history

test("Today is a screen of its own: the calendar's chrome goes, the day shows", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk.answers.get_today = ME;
	const pp = planner(desk);
	desk.land();
	await settle();
	check("the toolbar, trays, grid, hint, panel and draft strip are hidden", [pp.$toolbar, pp.$trays, pp.$grid_wrap, pp.$hint, pp.$panel, pp.$draft_bar].map((el) => el.shown), [false, false, false, false, false, false]);
	check("Today shows, the route and My week do not", [pp.$today.shown, pp.$route.shown, pp.$my_week.shown], [true, false, false]);
	check("the view", pp.view, "today");
	const heads = pp.$today.all().filter((el) => el.classes.has("pp-td-head"));
	check("two days, today first", heads.map((h) => h.children[0].textValue), ["TODAY", "TOMORROW"]);
	check("a stop count and the driving", heads[0].children[1].textValue, " · Wed Oct 21 · 1 stop(s) · 1h 40m driving");
	check("nothing booked tomorrow", byText(pp.$today, "Nothing booked") !== undefined, true);
	// Leaving for any other screen puts the calendar's chrome back and takes Today away.
	pp.set_mode("");
	check("set_mode gives the calendar back", [pp.$toolbar.shown, pp.$grid_wrap.shown, pp.$today.shown], [true, true, false]);
});

test("Today is remembered as the last used view, without a date", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk.answers.get_today = ME;
	const pp = planner(desk);
	desk.land();
	await settle();
	desk.flush();
	const saves = desk.called("save_last_view");
	check("one save", saves.length, 1);
	const saved = JSON.parse(saves[0].args.data);
	check("the screen", saved.view, "today");
	check("never a date", Object.keys(saved).filter((key) => /date|anchor|day/.test(key)), []);
});

test("landing, in sequence: Today first, then the tab used last, wherever that was", async () => {
	// A stand-in for the planner's per-person store (planner_views_prefs): what save_last_view keeps.
	const store = { last: null };
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: true });
	desk.answers.get_today = ME;
	desk.answers.save_last_view = (args) => {
		store.last = JSON.parse(args.data);
		return { last: store.last };
	};
	const open = (initial) => {
		// A new page on the same Desk, as a reload is: the person's saved views come from the store.
		desk.timers.length = 0;
		return planner(desk, { p6c: { loaded: true, restored: false, last: store.last, saved_text: null, views: [], color_by: "default" } });
	};
	let pp = open();
	desk.go("project-planner");
	await settle();
	check("1. nothing saved: Today, in place", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/today/2026-10-21"]);
	desk.flush();
	check("1. and Today is what is saved", store.last.view, "today");

	byText(pp.$today, "Planner").click();
	await settle();
	check("2. the week, one entry on", desk.urls().pop(), "/desk/project-planner/week/2026-10-21");
	pp.p6c_state_soon();
	desk.flush();
	check("2. the week is what is saved", store.last.view, "week");

	desk.go("project", "PRJ-00001");
	pp = open();
	desk.go("project-planner");
	await settle();
	check("3. a bare open after the week is the week", [desk.urls().pop(), pp.loads.pop()], ["/desk/project-planner", { view: "week", anchor: "2026-10-21" }]);

	pp.go_today("2026-10-21");
	await settle();
	desk.flush();
	check("4. Today again is saved", store.last.view, "today");

	desk.go("project", "PRJ-00001");
	pp = open();
	desk.go("project-planner");
	await settle();
	check("4. a bare open after Today is Today", desk.urls().pop(), "/desk/project-planner/today/2026-10-21");

	// Back from the landing leaves the planner: the bare entry was replaced, not kept under it.
	desk.back();
	check("5. Back is the page before", desk.urls()[desk.index], "/desk/project/PRJ-00001");
});

test("landing: a saved Today is restored on a desktop too, and a first time there stays the week", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	desk.answers.get_today = ME;
	planner(desk, { p6c: { loaded: true, restored: false, last: { view: "today" }, saved_text: null, views: [], color_by: "default" } });
	desk.go("project-planner");
	await settle();
	check("Today", desk.urls().pop(), "/desk/project-planner/today/2026-10-21");

	const desk2 = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp2 = planner(desk2);
	desk2.go("project-planner");
	await settle();
	check("the week, bare", [desk2.urls().pop(), pp2.loads], ["/desk/project-planner", [{ view: "week", anchor: "2026-10-21" }]]);
});

test("history: the day arrows, This week and Planner each take one entry; Back steps off them", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk.answers.get_today = ME;
	const pp = planner(desk);
	desk.land();
	await settle();
	byText(pp.$today, "›").click();
	await settle();
	check("tomorrow is an entry", desk.urls().pop(), "/desk/project-planner/today/2026-10-22");
	check("and was asked for", desk.called("get_today").map((c) => c.args.date), ["2026-10-21", "2026-10-22"]);
	byText(pp.$today, "‹").click();
	await settle();
	check("back a day is an entry", desk.urls().pop(), "/desk/project-planner/today/2026-10-21");
	check("three entries on top of the first", desk.entries.length, 3);
	byText(pp.$today, "This week").click();
	await settle();
	check("This week is My week, one entry", desk.urls().pop(), "/desk/project-planner/my-week/2026-10-21");
	check("My week loads", pp.loads.pop(), { view: "my-week", anchor: "2026-10-21" });
	desk.back();
	await settle();
	check("Back lands on Today again", desk.urls()[desk.index], "/desk/project-planner/today/2026-10-21");
	check("and asks for it", desk.called("get_today").pop().args.date, "2026-10-21");
	byText(pp.$today, "Planner").click();
	await settle();
	check("Planner is the week view, one entry", desk.urls().pop(), "/desk/project-planner/week/2026-10-21");
	desk.back();
	await settle();
	check("Back is Today", desk.urls()[desk.index], "/desk/project-planner/today/2026-10-21");
	desk.forward();
	await settle();
	check("Forward is the week", desk.urls()[desk.index], "/desk/project-planner/week/2026-10-21");
});

test("Full route opens the route view, one entry", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	const answer = JSON.parse(JSON.stringify(ME));
	answer.route = { stops: [{ order: 1, located: true, lat: 40.1, lng: -111.6 }], maps_url: "https://www.google.com/maps/dir/?api=1", maps_key: "" };
	desk.answers.get_today = answer;
	const pp = planner(desk);
	pp.draw_route_map = () => {};
	desk.land();
	await settle();
	byText(pp.$today, "Full route").click();
	await settle();
	check("the route view for the person and the day", desk.urls().pop(), "/desk/project-planner/route/RES-1/2026-10-21");
	byText(pp.$today, "Open in Google Maps").click();
	check("Maps opens without an opener", desk.opened, [["https://www.google.com/maps/dir/?api=1", "_blank", "noopener"]]);
});

test("the map is drawn from the route get_today carries, today only", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	const answer = JSON.parse(JSON.stringify(ME));
	answer.route = { stops: [{ order: 1, located: true }], maps_url: null, maps_key: "k" };
	desk.answers.get_today = answer;
	const pp = planner(desk);
	const drawn = [];
	pp.draw_route_map = (token, data) => drawn.push({ token, order: data.stops.map((s) => s.order) });
	desk.land();
	await settle();
	check("one map", drawn.length, 1);
	check("of today's stops", drawn[0].order, [1]);
	check("under the request that asked", drawn[0].token, pp.request);
});

test("a day with nothing booked draws no map, and says so", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	const answer = JSON.parse(JSON.stringify(ME));
	answer.days[0].items = [];
	desk.answers.get_today = answer;
	const pp = planner(desk);
	let drawn = 0;
	pp.draw_route_map = () => (drawn += 1);
	desk.land();
	await settle();
	check("no map", drawn, 0);
	check("Nothing booked, twice", pp.$today.all().filter((el) => el.textValue === "Nothing booked").length, 2);
});

// ------------------------------------------------------------------ Block my time

test("Block my time opens the personal-block form for the person alone, with no history entry", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk.answers.get_today = ME;
	const pp = planner(desk);
	desk.land();
	await settle();
	const entries = desk.entries.length;
	byText(pp.$today, "Block my time").click();
	check("one form", pp.kit.forms.length, 1);
	const form = pp.kit.forms[0];
	check("for the day on screen", form.date, "2026-10-21");
	check("their own time only", form.self_only, true);
	check("a new block", form.block, null);
	check("named for them", form.resource_label, "Austin Healey");
	check("no history entry of ours", desk.entries.length, entries);
	form.on_saved();
	await settle();
	check("saving reloads the day", desk.called("get_today").length, 2);
});

test("a block on the day opens for editing from its chip", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	const answer = JSON.parse(JSON.stringify(ME));
	answer.blocks = [{ name: "PB-1", date: "2026-10-21", note: "dentist", can_edit: true }];
	desk.answers.get_today = answer;
	const pp = planner(desk);
	desk.land();
	await settle();
	pp.p6d_block_click({ getAttribute: (key) => (key === "data-pk-block" ? "PB-1" : "2026-10-21") });
	check("the block's form", [pp.kit.forms.length, pp.kit.forms[0].block.name, pp.kit.forms[0].can_delete], [1, "PB-1", true]);
});

// ------------------------------------------------------------------ nothing to show

test("someone not on the planner's list is told so, and can still reach the planner", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk.answers.get_today = { resource: null, message: "You are not on the planner's list of people yet.", days: [], date: "2026-10-21" };
	const pp = planner(desk);
	desk.land();
	await settle();
	const notes = pp.$today.all().filter((el) => el.classes.has("pp-route-note"));
	check("the message", notes.map((n) => n.textValue), ["You are not on the planner's list of people yet."]);
	check("the Planner button is there", byText(pp.$today, "Planner") !== undefined, true);
	check("and no Block my time", byText(pp.$today, "Block my time"), undefined);
});

test("a failed request leaves a note, not a blank pane", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk.frappe.call = () => Promise.reject(new Error("offline"));
	const pp = planner(desk);
	desk.land();
	await settle();
	check("a note", pp.$today.all().some((el) => el.classes.has("pp-route-note") && /could not be loaded/.test(el.textValue)), true);
});

test("a stale answer never draws over a newer one", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	const slow = [];
	desk.frappe.call = () => new Promise((resolve) => slow.push(resolve));
	const pp = planner(desk);
	desk.land();
	pp.go_today("2026-10-22");
	await settle();
	slow[1]({ message: ME });
	await settle();
	slow[0]({ message: { resource: null, message: "old", days: [] } });
	await settle();
	check("the newer answer stands", pp.today_data.resource, "RES-1");
});

// ------------------------------------------------------------------ More ▸ Views

test("More > Views offers Today on a phone, so a scheduler can reach it, and not on a desktop", () => {
	const phone = planner(makeDesk({ roles: ["Projects User"] }));
	phone.tb_calendar = () => false;
	phone.p6c_view_items = () => [];
	check("phone", phone.tb_views_items().map((item) => item.label), ["Today", "My week"]);
	const desktop = planner(makeDesk({ roles: ["Projects User"], phone: false }));
	desktop.tb_calendar = () => false;
	desktop.p6c_view_items = () => [];
	check("desktop", desktop.tb_views_items().map((item) => item.label), ["My week"]);
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
