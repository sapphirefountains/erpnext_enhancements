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
		session: { user: "nik@example.com" },
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

// ------------------------------------------------------------------ the landing: the tab is the device's

const TAB_KEY = "ee_project_planner_tab:nik@example.com";
const nothingSaved = () => ({ loaded: true, restored: false, last: null, saved_text: null, views: [], color_by: "default" });
// A stand-in for the planner's per-person "last used" row (planner_views_prefs): what save_last_view keeps.
function serverRow(desk, store) {
	desk.answers.save_last_view = (args) => {
		store.last = JSON.parse(args.data);
		return { last: store.last };
	};
}
// A page on `desk` with the person's saved row, as a reload opens one.
const openPage = (desk, store) =>
	planner(desk, { p6c: { loaded: true, restored: false, last: store.last, saved_text: null, views: [], color_by: "default" } });

test("landing: a phone, nothing remembered on it -> Today, in place", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001" });
	desk.answers.get_today = ME;
	const pp = planner(desk);
	desk.go("project-planner");
	await settle();
	check("the address is Today on today's date", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/today/2026-10-21"]);
	check("the bare entry was replaced, not kept", desk.replaces, 1);
	check("asked for today", desk.called("get_today").map((c) => c.args.date), ["2026-10-21"]);
	check("Today is now this device's tab", desk.prefs[TAB_KEY], "today");
	check("the planner was not loaded", pp.loads, []);
	desk.back();
	check("Back leaves the planner", desk.urls()[desk.index], "/desk/project/PRJ-00001");
});

test("landing: a desktop with nothing remembered opens the default view and remembers it", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp = planner(desk);
	desk.go("project-planner");
	await settle();
	// P5: the default view is the Lookahead, at its own address in place of the bare one.
	check("the Lookahead's own address, in place", [desk.urls().pop(), desk.urls().length], ["/desk/project-planner/lookahead/2026-10-21", 2]);
	check("the Lookahead loads", pp.loads, [{ view: "lookahead", anchor: "2026-10-21" }]);
	check("and is the device's tab", desk.prefs[TAB_KEY], "lookahead");
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

test("landing: each device keeps its own tab for the same person", async () => {
	const store = { last: null };
	const phone = makeDesk({ start: "/desk/project/PRJ-00001", phone: true });
	const desktop = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	for (const desk of [phone, desktop]) {
		desk.answers.get_today = ME;
		serverRow(desk, store);
	}
	// The phone: Today the first time, then the planner (P5: the Lookahead).
	let ph = openPage(phone, store);
	phone.go("project-planner");
	await settle();
	check("phone, first time", phone.urls().pop(), "/desk/project-planner/today/2026-10-21");
	byText(ph.$today, "Planner").click();
	await settle();
	// P5: Today's Planner button opens the planner's home tab, the Lookahead.
	check("phone remembers the Lookahead", phone.prefs[TAB_KEY], "lookahead");
	// The desktop meanwhile: the Lookahead by default (P5), then the crew board.
	let dt = openPage(desktop, store);
	desktop.go("project-planner");
	await settle();
	dt.go("crew", "2026-10-21");
	await settle();
	check("desktop remembers the crew board", desktop.prefs[TAB_KEY], "crew");
	check("the phone's tab did not move", phone.prefs[TAB_KEY], "lookahead");
	// Both come back to their own.
	phone.go("project", "PRJ-00001");
	ph = openPage(phone, store);
	phone.go("project-planner");
	await settle();
	check("phone: its Lookahead", [phone.urls().pop(), ph.loads.pop().view], ["/desk/project-planner/lookahead/2026-10-21", "lookahead"]);
	desktop.go("project", "PRJ-00001");
	dt = openPage(desktop, store);
	desktop.go("project-planner");
	await settle();
	check("desktop: its crew board, in place", [desktop.urls().slice(-2), dt.loads.pop().view], [["/desk/project/PRJ-00001", "/desk/project-planner/crew/2026-10-21"], "crew"]);
	// And the phone can come back to Today by choosing it.
	ph.go_today("2026-10-21");
	await settle();
	phone.go("project", "PRJ-00001");
	ph = openPage(phone, store);
	phone.go("project-planner");
	await settle();
	check("phone: Today, because Today was its last", phone.urls().pop(), "/desk/project-planner/today/2026-10-21");
});

test("landing: filters are the person's, on every device; the tab is not", async () => {
	const store = { last: null };
	const desktop = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const phone = makeDesk({ start: "/desk/project/PRJ-00001", phone: true });
	for (const desk of [desktop, phone]) {
		desk.answers.get_today = ME;
		serverRow(desk, store);
	}
	const dt = openPage(desktop, store);
	desktop.go("project-planner");
	await settle();
	dt.project = "PRJ-1";
	dt.p6c.restored = true;
	dt.view = "crew";
	dt.p6c_state_soon();
	desktop.flush();
	check("the person's row holds the filter", store.last.project, "PRJ-1");
	check("and no tab", "view" in store.last, false);
	const ph = openPage(phone, store);
	phone.go("project-planner");
	await settle();
	check("the phone applied the desktop's filter", ph.project, "PRJ-1");
	check("and opened on its own tab, Today", phone.urls().pop().split("?")[0], "/desk/project-planner/today/2026-10-21");
});

test("landing: an older row's saved view is ignored", async () => {
	const store = { last: { view: "crew", project: "PRJ-1" } };
	const phone = makeDesk({ start: "/desk/project/PRJ-00001", phone: true });
	phone.answers.get_today = ME;
	openPage(phone, store);
	phone.go("project-planner");
	await settle();
	check("phone: Today, not the row's crew", phone.urls().pop().split("?")[0], "/desk/project-planner/today/2026-10-21");
	const desktop = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const dt = openPage(desktop, store);
	desktop.go("project-planner");
	await settle();
	check("desktop: the default Lookahead, not the row's crew", [desktop.urls().pop().split("?")[0], dt.loads.pop().view], ["/desk/project-planner/lookahead/2026-10-21", "lookahead"]);
});

test("landing: a link that names a filter keeps the device's calendar tab but never opens Today", async () => {
	for (const phone of [true, false]) {
		// Today has no filters to show: the link opens the default view with the project.
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone });
		desk.answers.get_today = ME;
		desk.prefs[TAB_KEY] = "today";
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go("project-planner", { project: "PRJ-00001" });
		await settle();
		const where = phone ? "phone" : "desktop";
		check(`${where}, stored Today: the planner opens, not Today`, desk.called("get_today"), []);
		check(`${where}, stored Today: on the project the link named`, [pp.loads.length, pp.filters().project], [1, "PRJ-00001"]);
		// P5: the default view, the Lookahead, at its own address with the link's filter.
		check(`${where}, stored Today: the Lookahead with the link's filter`, desk.urls().pop(), "/desk/project-planner/lookahead/2026-10-21?project=PRJ-00001");
		// A first visit on a phone is Today by default, which a filter link also skips.
		const first = makeDesk({ start: "/desk/project/PRJ-00001", phone });
		first.answers.get_today = ME;
		const fp = planner(first, { p6c: nothingSaved() });
		first.go("project-planner", { project: "PRJ-00001" });
		await settle();
		check(`${where}, first visit: not Today`, [first.called("get_today"), fp.filters().project], [[], "PRJ-00001"]);
		// The device's calendar tab still applies, with the filter.
		const crew = makeDesk({ start: "/desk/project/PRJ-00001", phone });
		crew.prefs[TAB_KEY] = "crew";
		const cp = planner(crew, { p6c: nothingSaved() });
		crew.go("project-planner", { project: "PRJ-00001" });
		await settle();
		check(`${where}, stored crew: the crew view with the filter`, [crew.urls().pop(), cp.filters().project], ["/desk/project-planner/crew/2026-10-21?project=PRJ-00001", "PRJ-00001"]);
	}
});

test("landing: someone the planner does not list, on a device whose tab is Today, gets the planner", async () => {
	for (const phone of [true, false]) {
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone });
		desk.prefs[TAB_KEY] = "today";
		desk.answers.get_today = { resource: null, message: "not listed", days: [], date: "2026-10-21" };
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go("project-planner");
		await settle();
		// P5: the planner they get is its home tab, the Lookahead.
		check(`${phone ? "phone" : "desktop"}: the Lookahead, in place of Today`, desk.urls().slice(-2), ["/desk/project/PRJ-00001", "/desk/project-planner/lookahead/2026-10-21"],);
		check("it loaded", pp.loads, [{ view: "lookahead", anchor: "2026-10-21" }]);
		check("and the tab moves off Today", desk.prefs[TAB_KEY], "lookahead");
	}
	// A deliberate visit to Today by someone unlisted still says why there is nothing.
	const desk2 = makeDesk({ start: "/desk/project-planner/today/2026-10-21" });
	desk2.answers.get_today = { resource: null, message: "not listed", days: [], date: "2026-10-21" };
	const pp2 = planner(desk2);
	desk2.land();
	await settle();
	check("stays on Today with the note", [desk2.urls(), pp2.$today.all().some((el) => el.textValue === "not listed")], [["/desk/project-planner/today/2026-10-21"], true]);
});

test("landing: a store that throws is a first visit, and never breaks the page", async () => {
	for (const phone of [true, false]) {
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone });
		desk.answers.get_today = ME;
		const boom = () => {
			throw new Error("SecurityError");
		};
		desk.window.localStorage.getItem = boom;
		desk.window.localStorage.setItem = boom;
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go("project-planner");
		await settle();
		if (phone) check("phone: Today", desk.urls().pop(), "/desk/project-planner/today/2026-10-21");
		else check("desktop: the Lookahead (P5's default)", [desk.urls().pop(), pp.loads.length], ["/desk/project-planner/lookahead/2026-10-21", 1]);
	}
});

test("landing (P5): a fresh desktop opens the Lookahead, replacing the bare address once; Back leaves", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	check("the Lookahead at its own address, in place", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/lookahead/2026-10-21"]);
	check("replaced exactly once, loaded once", [desk.replaces, pp.loads.length], [1, 1]);
	check("remembered as this device's tab", desk.prefs[TAB_KEY], "lookahead");
	desk.back();
	check("Back leaves the planner", desk.urls()[desk.index], "/desk/project/PRJ-00001");
});

test("landing (P5): a device whose tab is the Lookahead opens it, on a phone too", async () => {
	for (const phone of [false, true]) {
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone });
		desk.answers.get_today = ME;
		desk.prefs[TAB_KEY] = "lookahead";
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go("project-planner");
		await settle();
		const where = phone ? "phone" : "desktop";
		check(`${where}: the Lookahead, replaced once`, [desk.urls().pop(), desk.replaces, pp.loads.map((l) => l.view)], [
			"/desk/project-planner/lookahead/2026-10-21",
			1,
			["lookahead"],
		]);
		check(`${where}: no Today request`, desk.called("get_today"), []);
	}
});

test("landing (P5): an old row with a view and P5's tabbed flag still loads its filters, and no tab", async () => {
	const store = { last: { view: "crew", tabbed: true, project: "PRJ-1", group: "Field" } };
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp = openPage(desk, store);
	desk.go("project-planner");
	await settle();
	check("the person's filters", [pp.filters().project, pp.filters().group], ["PRJ-1", "Field"]);
	check("the default Lookahead, not the row's crew", desk.urls().pop().split("?")[0], "/desk/project-planner/lookahead/2026-10-21");
	check("a saved row no longer carries a view or the flag", ["view", "tabbed"].map((key) => key in pp.p6c_state(true)), [false, false]);
});

test("landing: two people on one browser keep separate tabs", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: true });
	desk.answers.get_today = ME;
	desk.prefs["ee_project_planner_tab:other@example.com"] = "crew";
	planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	check("this person is on their first visit: Today", desk.urls().pop(), "/desk/project-planner/today/2026-10-21");
	check("the other's tab is untouched", desk.prefs["ee_project_planner_tab:other@example.com"], "crew");
});

test("landing: a remembered month view is restored, in place, after the saved views are in", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	desk.prefs[TAB_KEY] = "month";
	const pp = planner(desk, { p6c: { loaded: false, restored: false, last: null, saved_text: null, views: [], color_by: "default" } });
	let release;
	pp.p6c_fetch_views = () =>
		new Promise((resolve) => {
			release = () => {
				pp.p6c.loaded = true;
				resolve();
			};
		});
	desk.go("project-planner");
	await settle();
	check("nothing decided while the views are on their way", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner"]);
	release();
	await settle();
	check("then the month (P5: under the Calendar tab)", [desk.urls().pop(), desk.replaces], ["/desk/project-planner/calendar/month/2026-10-21", 1]);
	desk.back();
	check("Back leaves the planner", desk.urls()[desk.index], "/desk/project/PRJ-00001");
});

test("landing: Back and Forward step through the tabs, each its own entry", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	pp.go("crew", "2026-10-21");
	await settle();
	pp.go("month", "2026-10-21");
	await settle();
	check("three entries on top of the page", desk.urls().slice(1), ["/desk/project-planner/lookahead/2026-10-21", "/desk/project-planner/crew/2026-10-21", "/desk/project-planner/calendar/month/2026-10-21"]);
	desk.back();
	await settle();
	check("Back is the crew board", [desk.urls()[desk.index], desk.prefs[TAB_KEY]], ["/desk/project-planner/crew/2026-10-21", "crew"]);
	desk.forward();
	await settle();
	check("Forward is the month", desk.urls()[desk.index], "/desk/project-planner/calendar/month/2026-10-21");
});

// ------------------------------------------------------------------ P6: the Gantt is a remembered tab too
//
// Approved by the Sapphire Manager on 2026-10-21 (Nik's "last-used tab per device", no exception): the
// Gantt is one of the device's tabs, and the device keeps its project beside it.

const GANTT_KEY = "ee_project_planner_gantt:nik@example.com";

// The page's real load for the Gantt, against desk.answers.get_project_gantt (other tabs stay recorded).
function ganttLoads(pp) {
	const recorded = pp.load;
	const real = Object.getPrototypeOf(pp).load;
	Object.assign(pp, { $title: { text() {} }, take_title_focus() {}, ppl_sync_tabs() {}, fill_filters() {}, render() {} });
	pp.load = function () {
		return this.view === "gantt" ? real.call(this) : recorded.call(this);
	};
	return pp;
}


test("landing (P6): a device whose tab is the Gantt reopens its project, in place; the other device keeps its own", async () => {
	const store = { last: null };
	const desktop = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const phone = makeDesk({ start: "/desk/project/PRJ-00001", phone: true });
	const drawn = (args) => ({ projects: [], tasks: [], unscheduled: [], gantt: { project: args.project, title: args.project, groups: [] } });
	for (const desk of [desktop, phone]) {
		desk.answers.get_today = ME;
		desk.answers.get_project_gantt = drawn;
		serverRow(desk, store);
	}
	let dt = ganttLoads(openPage(desktop, store));
	desktop.go("project-planner", "gantt", "PRJ-00754");
	await settle();
	check("the desktop remembers the Gantt and its project", [desktop.prefs[TAB_KEY], desktop.prefs[GANTT_KEY]], ["gantt", "PRJ-00754"]);
	// The phone, for the same person, has its own store: Today the first time, then a Gantt of its own.
	let ph = ganttLoads(openPage(phone, store));
	phone.go("project-planner");
	await settle();
	check("the phone's first visit is still Today", phone.urls().pop(), "/desk/project-planner/today/2026-10-21");
	ph.ppg_open("PRJ-00597");
	await settle();
	check("the phone's Gantt is its own", [phone.prefs[TAB_KEY], phone.prefs[GANTT_KEY]], ["gantt", "PRJ-00597"]);
	check("the desktop's did not move", desktop.prefs[GANTT_KEY], "PRJ-00754");
	// Both come back to a bare address.
	desktop.go("project", "PRJ-00001");
	dt = ganttLoads(openPage(desktop, store));
	desktop.go("project-planner");
	await settle();
	check("desktop: its project's Gantt, in place of the bare address", desktop.urls().slice(-2), ["/desk/project/PRJ-00001", "/desk/project-planner/gantt/PRJ-00754"]);
	check("loaded on the Gantt, that project", [dt.view, dt.ppg.project, desktop.called("get_project_gantt").pop().args.project], ["gantt", "PRJ-00754", "PRJ-00754"]);
	desktop.back();
	await settle();
	check("Back leaves the planner", desktop.urls()[desktop.index], "/desk/project/PRJ-00001");
	phone.go("project", "PRJ-00001");
	ph = ganttLoads(openPage(phone, store));
	phone.go("project-planner");
	await settle();
	check("phone: its own project's Gantt", phone.urls().slice(-2), ["/desk/project/PRJ-00001", "/desk/project-planner/gantt/PRJ-00597"]);
	phone.back();
	await settle();
	check("Back leaves the planner on the phone too", phone.urls()[phone.index], "/desk/project/PRJ-00001");
});

test("landing (P6): a remembered project the server will not open is forgotten, and the Lookahead replaces it once", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	desk.prefs[TAB_KEY] = "gantt";
	desk.prefs[GANTT_KEY] = "PRJ-GONE";
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	check("first the Gantt, in place of the bare address", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/gantt/PRJ-GONE"]);
	// get_project_gantt refuses (deleted, or not the person's to read).
	pp.ppg_refused();
	await settle();
	check("then the Lookahead, in place again: still one entry past the page", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/lookahead/2026-10-21"]);
	check("the project is forgotten on this device", desk.prefs[GANTT_KEY], "");
	check("and the Lookahead is the tab now", desk.prefs[TAB_KEY], "lookahead");
	desk.back();
	await settle();
	check("Back leaves the planner", desk.urls()[desk.index], "/desk/project/PRJ-00001");
	// The next bare address goes straight to the Lookahead.
	desk.go("project-planner");
	await settle();
	check("nothing stored: the Lookahead", desk.urls().pop(), "/desk/project-planner/lookahead/2026-10-21");
});

test("landing (P6): the Gantt remembered with no project is the Lookahead, in place", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	desk.prefs[TAB_KEY] = "gantt";
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	check("the Lookahead, one entry", [desk.urls(), pp.loads.map((l) => l.view)], [["/desk/project/PRJ-00001", "/desk/project-planner/lookahead/2026-10-21"], ["lookahead"]]);
});

test("landing (P6): a forbidden project (a 403's case) is answered quietly: no dialog, one replace to the Lookahead", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	desk.prefs[TAB_KEY] = "gantt";
	desk.prefs[GANTT_KEY] = "PRJ-SECRET";
	// The server's answer for a project the person may not read (a User Permission): never a 403, whose
	// msgprint would re_route the address back here and load it a second time.
	desk.answers.get_project_gantt = (args) => ({ refused: "forbidden", projects: [], tasks: [], unscheduled: [], gantt: null, asked: args.project });
	const pp = ganttLoads(planner(desk, { p6c: nothingSaved() }));
	desk.go("project-planner");
	await settle();
	check("the Lookahead, in place of the bare address: one entry past the page", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/lookahead/2026-10-21"]);
	check("asked once", desk.called("get_project_gantt").map((c) => c.args.project), ["PRJ-SECRET"]);
	check("no dialog (this harness has no msgprint: one would have thrown)", typeof desk.frappe.msgprint, "undefined");
	check("forgotten on this device", desk.prefs[GANTT_KEY], "");
	check("the Lookahead loaded", pp.loads.map((l) => l.view), ["lookahead"]);
	desk.back();
	await settle();
	check("Back leaves the planner", desk.urls()[desk.index], "/desk/project/PRJ-00001");
});

test("landing (P6): a request that fails keeps the device's project and stays put with a retry", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	desk.prefs[TAB_KEY] = "gantt";
	desk.prefs[GANTT_KEY] = "PRJ-00754";
	const call = desk.frappe.call;
	desk.frappe.call = (opts) => (opts.method.endsWith("get_project_gantt") ? Promise.reject({ status: 0 }) : call(opts));
	const pp = ganttLoads(planner(desk, { p6c: nothingSaved() }));
	desk.go("project-planner");
	await settle();
	check("still the Gantt, in place", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/gantt/PRJ-00754"]);
	check("the project is kept", desk.prefs[GANTT_KEY], "PRJ-00754");
	check("marked for a retry", pp.ppg.trouble, "PRJ-00754");
});

test("landing (P6): a link to a project that will not open never replaces the device's last good one", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	desk.prefs[TAB_KEY] = "gantt";
	desk.prefs[GANTT_KEY] = "PRJ-00754";
	desk.answers.get_project_gantt = (args) =>
		args.project === "PRJ-GONE" ? { refused: "missing", projects: [], tasks: [], unscheduled: [], gantt: null } : { projects: [], tasks: [], unscheduled: [], gantt: { project: args.project, title: "Riverwalk", groups: [] } };
	const pp = ganttLoads(planner(desk, { p6c: nothingSaved() }));
	desk.go("project-planner", "gantt", "PRJ-GONE");
	await settle();
	check("the link's address stays, with its sentence", [desk.urls().pop(), pp.ppg.failed, pp.ppg.refused], ["/desk/project-planner/gantt/PRJ-GONE", "PRJ-GONE", "missing"]);
	check("the device's last good project is untouched", desk.prefs[GANTT_KEY], "PRJ-00754");
	desk.go("project", "PRJ-00001");
	ganttLoads(planner(desk, { p6c: nothingSaved() }));
	desk.go("project-planner");
	await settle();
	check("the next bare address reopens it", desk.urls().pop(), "/desk/project-planner/gantt/PRJ-00754");
});

test("landing (P6): a project opened by link or picker that the server refuses offers the picker; it never redirects", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp = planner(desk, { p6c: nothingSaved() });
	pp.render = () => null;
	desk.go("project-planner", "gantt", "PRJ-GONE");
	await settle();
	pp.ppg_refused();
	await settle();
	check("stays on its address, marked", [desk.urls().pop(), pp.ppg.failed], ["/desk/project-planner/gantt/PRJ-GONE", "PRJ-GONE"]);
});

test("landing (P6): a link that names a filter beats a remembered Gantt: the Lookahead with the filter, never Today", async () => {
	for (const phoneDevice of [false, true]) {
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: phoneDevice });
		desk.answers.get_today = ME;
		desk.prefs[TAB_KEY] = "gantt";
		desk.prefs[GANTT_KEY] = "PRJ-00754";
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go("project-planner", { pm: "Clegg" });
		await settle();
		check(`${phoneDevice ? "phone" : "desktop"}: the Lookahead with the link's filter, in place`, desk.urls(), [
			"/desk/project/PRJ-00001",
			"/desk/project-planner/lookahead/2026-10-21?pm=Clegg",
		]);
		check(`${phoneDevice ? "phone" : "desktop"}: no Gantt, no Today`, [pp.loads.map((l) => l.view), desk.called("get_today").length], [["lookahead"], 0]);
		desk.back();
		await settle();
		check(`${phoneDevice ? "phone" : "desktop"}: Back leaves the planner`, desk.urls()[desk.index], "/desk/project/PRJ-00001");
	}
});

test("landing (P6): Back and Forward step between the Gantt and the other tabs", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	pp.ppg_open("PRJ-00754");
	await settle();
	pp.go("crew", "2026-10-21");
	await settle();
	pp.go("gantt", "2026-10-21");
	await settle();
	check("each its own entry; the Gantt tab reopens the last project", desk.urls().slice(1), [
		"/desk/project-planner/lookahead/2026-10-21",
		"/desk/project-planner/gantt/PRJ-00754",
		"/desk/project-planner/crew/2026-10-21",
		"/desk/project-planner/gantt/PRJ-00754",
	]);
	desk.back();
	await settle();
	check("Back: the crew board, and the chart is taken down", [desk.urls()[desk.index], pp.view, pp.ppg.widget], ["/desk/project-planner/crew/2026-10-21", "crew", null]);
	desk.back();
	await settle();
	check("Back: the Gantt of that project", [desk.urls()[desk.index], pp.view, pp.ppg.project], ["/desk/project-planner/gantt/PRJ-00754", "gantt", "PRJ-00754"]);
	desk.back();
	await settle();
	check("Back: the Lookahead", [desk.urls()[desk.index], pp.view], ["/desk/project-planner/lookahead/2026-10-21", "lookahead"]);
	desk.forward();
	await settle();
	check("Forward: the Gantt again", [pp.view, pp.ppg.project], ["gantt", "PRJ-00754"]);
	check("no entry was added or replaced on the way", desk.urls().length, 5);
});

// ------------------------------------------------------------------ P9: the Executive Team's first visit is the Overview
//
// Nik, 2026-10-22: an Executive Team member's desktop FIRST visit opens the Overview instead of the
// Lookahead. After that the per-device last-used tab applies like everyone else's. The Overview is one of
// the device's tabs (PP8.tabs); redirects replace, never push; a link that names a filter never lands on it.

const EXEC = ["Projects User", "Executive Team"];

test("landing (P9): an Executive Team member's desktop first visit opens the Overview, in place; Back leaves", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	check("the Overview at its own address, in place of the bare one", desk.urls(), ["/desk/project/PRJ-00001", "/desk/project-planner/overview/2026-10-21"]);
	check("replaced once, loaded once, on the Overview", [desk.replaces, pp.loads], [1, [{ view: "overview", anchor: "2026-10-21" }]]);
	check("and it is the device's tab from now on", desk.prefs[TAB_KEY], "overview");
	check("no Today request", desk.called("get_today"), []);
	desk.back();
	check("Back leaves the planner", desk.urls()[desk.index], "/desk/project/PRJ-00001");
});

test("landing (P9): after the first visit the device's last-used tab applies to them like anyone else", async () => {
	for (const [tab, address] of [
		["crew", "/desk/project-planner/crew/2026-10-21"],
		["lookahead", "/desk/project-planner/lookahead/2026-10-21"],
		["month", "/desk/project-planner/calendar/month/2026-10-21"],
		["overview", "/desk/project-planner/overview/2026-10-21"],
	]) {
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
		desk.prefs[TAB_KEY] = tab;
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go("project-planner");
		await settle();
		check(`${tab}: its own address, replaced once`, [desk.urls().pop(), desk.replaces], [address, 1]);
		check(`${tab}: loaded once`, pp.loads.length, 1);
	}
});

test("landing (P9): the Overview they leave is not the one they return to, and the Lookahead stays reachable", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	pp.go("lookahead", "2026-10-21");
	await settle();
	check("the Lookahead is now this device's tab", desk.prefs[TAB_KEY], "lookahead");
	desk.go("project", "PRJ-00001");
	desk.go("project-planner");
	await settle();
	check("a later bare visit opens the Lookahead, not the Overview", desk.urls().pop(), "/desk/project-planner/lookahead/2026-10-21");
});

test("landing (P9): on a phone the first visit is Today, executive or not; an ordinary desktop gets the Lookahead", async () => {
	const phone = makeDesk({ start: "/desk/project/PRJ-00001", phone: true, roles: EXEC });
	phone.answers.get_today = ME;
	planner(phone, { p6c: nothingSaved() });
	phone.go("project-planner");
	await settle();
	check("phone: Today", phone.urls().pop(), "/desk/project-planner/today/2026-10-21");
	const plain = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: ["Projects User"] });
	planner(plain, { p6c: nothingSaved() });
	plain.go("project-planner");
	await settle();
	check("desktop, no Executive Team role: the Lookahead", plain.urls().pop(), "/desk/project-planner/lookahead/2026-10-21");
});

test("landing (P9): a link that names a filter never opens the Overview from the landing", async () => {
	for (const remembered of [null, "overview"]) {
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
		if (remembered) desk.prefs[TAB_KEY] = remembered;
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go("project-planner", { pm: "Clegg" });
		await settle();
		check(`${remembered || "first visit"}: the Lookahead with the filter, in place`, desk.urls(), [
			"/desk/project/PRJ-00001",
			"/desk/project-planner/lookahead/2026-10-21?pm=Clegg",
		]);
		check(`${remembered || "first visit"}: loaded the Lookahead`, pp.loads.map((l) => l.view), ["lookahead"]);
	}
});

test("landing (P9): a store that throws is a first visit: an executive gets the Overview and nothing breaks", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
	const boom = () => {
		throw new Error("SecurityError");
	};
	desk.window.localStorage.getItem = boom;
	desk.window.localStorage.setItem = boom;
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	check("the Overview, loaded", [desk.urls().pop(), pp.loads.map((l) => l.view)], ["/desk/project-planner/overview/2026-10-21", ["overview"]]);
});

test("landing (P9): a role check that fails is not an executive", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
	desk.frappe.user.has_role = () => {
		throw new Error("no boot");
	};
	planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	check("the Lookahead", desk.urls().pop(), "/desk/project-planner/lookahead/2026-10-21");
});

test("P9: the Overview is a screen: a step, the months picker and a tab each take one entry; Back and Forward step through them", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	pp.ppo_shift(1);
	await settle();
	pp.ppo_pick_months(12);
	await settle();
	pp.go("crew", "2026-11-01");
	await settle();
	check("each its own entry; 6 months is not written, 12 is", desk.urls().slice(1), [
		"/desk/project-planner/overview/2026-10-21",
		"/desk/project-planner/overview/2026-11-01",
		"/desk/project-planner/overview/2026-11-01/12",
		"/desk/project-planner/crew/2026-11-01",
	]);
	check("the picker's choice is this browser's next time", desk.prefs.ee_project_planner_portfolio_months, "12");
	desk.back();
	await settle();
	check("Back: the Overview, twelve months", [desk.urls()[desk.index], pp.view, pp.ppo.months], ["/desk/project-planner/overview/2026-11-01/12", "overview", 12]);
	desk.back();
	await settle();
	check("Back: the address says six, so six", [desk.urls()[desk.index], pp.ppo.months, pp.anchor], ["/desk/project-planner/overview/2026-11-01", 6, "2026-11-01"]);
	desk.back();
	await settle();
	desk.forward();
	await settle();
	desk.forward();
	await settle();
	check("Forward again: twelve months", [pp.view, pp.ppo.months], ["overview", 12]);
	check("no entry was added or replaced on the way", [desk.urls().length, desk.replaces], [5, 1]);
});

test("P9: the Today button and the tab keep the window's months; the address's date is the month's", async () => {
	const desk = makeDesk({ start: "/desk/project-planner/overview/2026-12-15/9", phone: false, roles: EXEC });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.land();
	await settle();
	check("nine months from December", [pp.view, pp.anchor, pp.ppo.months], ["overview", "2026-12-15", 9]);
	check("the title is the window", pp.ppo_title(), "Overview · Dec 2026 – Aug 2027");
	pp.go("overview", "2026-10-21");
	await settle();
	check("the same months, today's month", desk.urls().pop(), "/desk/project-planner/overview/2026-10-21/9");
});

test("P9: a bare or garbled /overview opens today's month; a wild months segment is six", async () => {
	for (const [route, anchor, months] of [
		[["project-planner", "overview"], "2026-10-21", 6],
		[["project-planner", "overview", "garbage"], "2026-10-21", 6],
		[["project-planner", "overview", "2026-13-45", "4"], "2026-10-21", 6],
		[["project-planner", "overview", "2027-01-10", "3"], "2027-01-10", 3],
	]) {
		const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
		const pp = planner(desk, { p6c: nothingSaved() });
		desk.go(...route);
		await settle();
		check(route.join("/"), [pp.view, pp.anchor, pp.ppo.months, desk.replaces], ["overview", anchor, months, 0]);
	}
});

test("P9: leaving the Overview takes the chart down, and a tab on screen is not pushed twice", async () => {
	const desk = makeDesk({ start: "/desk/project/PRJ-00001", phone: false, roles: EXEC });
	const pp = planner(desk, { p6c: nothingSaved() });
	desk.go("project-planner");
	await settle();
	let destroyed = 0;
	pp.ppo.widget = { destroyed: false, destroy: () => (destroyed += 1) };
	pp.go("overview", "2026-10-21");
	await settle();
	check("the same screen is a reload, not an entry", [desk.urls().length, pp.loads.length], [2, 2]);
	pp.go("crew", "2026-10-21");
	await settle();
	check("the chart was destroyed on the way out", [destroyed, pp.ppo.widget], [1, null]);
});

test("what is saved for the person has no tab in it", async () => {
	const store = { last: null };
	const desk = makeDesk({ start: "/desk/project-planner/week/2026-10-21", phone: false });
	serverRow(desk, store);
	const pp = openPage(desk, store);
	desk.land();
	await settle();
	pp.project = "PRJ-1";
	pp.p6c_state_soon();
	desk.flush();
	check("filters and colors are saved", store.last.project, "PRJ-1");
	check("the tab is not", "view" in store.last, false);
	// A named view is an explicit choice and still carries its view.
	check("a named view keeps its view", pp.p6c_state(false).view, "week");
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
	check("Planner is the planner's home tab (P5), one entry", desk.urls().pop(), "/desk/project-planner/lookahead/2026-10-21");
	desk.back();
	await settle();
	check("Back is Today", desk.urls()[desk.index], "/desk/project-planner/today/2026-10-21");
	desk.forward();
	await settle();
	check("Forward is the Lookahead", desk.urls()[desk.index], "/desk/project-planner/lookahead/2026-10-21");
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
