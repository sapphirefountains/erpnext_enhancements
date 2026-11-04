#!/usr/bin/env node
/**
 * Nikolas's six planner picks of 2026-11-03 (v1.788.0), run under node against the REAL kit modules
 * (records.js, popover.js) and the REAL Project and Maintenance Planner page scripts:
 *
 *   1. The Lookahead's month band: PPL_PURE.month_bands, the band's cells over the day headings, the
 *      first of a month marked, the year said only when the window holds two.
 *   2. "Open … in new tab": records.desk_url and open_new_tab (the router's slug when there is one), the
 *      menu items after Open task / Open project / Open visit, Ctrl+Enter's target on both pages, and
 *      nothing for a drafted new task.
 *   3. The scroll a redraw loses: ppl_scroll_save / restore put the board's pane, a scrolling ancestor and
 *      the window back, and ppl_toggle redraws inside that.
 *   4. The tabs at the bar's right end are DOM order, pinned by tests/test_planner_six_picks.py.
 *   5. The Crew board's order (project name, then task name; a drive and a block first) and the hover card:
 *      popover_html escapes, place() flips, attach() shows after the delay for a mouse and never for a
 *      finger, holds the title aside, closes on leave and Esc; pph_task / pph_foreign / mph_card say the
 *      times, hours, crew and warnings.
 *   6. The pencil-only hours rule in avail_state (both planners), the Capacity heatmap and the kit's day_state.
 *
 * Run: node scripts/test_planner_six_picks.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const KIT = path.join(APP, "public", "js", "planner_kit");
const PP_JS = path.join(APP, "project_enhancements", "page", "project_planner", "project_planner.js");
const MP_JS = path.join(APP, "sapphire_maintenance", "page", "maintenance_planner", "maintenance_planner.js");

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

// ------------------------------------------------------------------ a small moment, the pages, the kit

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const LONG_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

function moment(value) {
	const text = String(value == null ? "" : value).slice(0, 10);
	const valid = /^\d{4}-\d{2}-\d{2}$/.test(text) && !Number.isNaN(Date.parse(`${text}T00:00:00Z`));
	const date = () => new Date(`${m.text}T00:00:00Z`);
	const m = {
		text,
		isValid: () => valid,
		day: () => date().getUTCDay(),
		month: () => date().getUTCMonth(),
		date: () => date().getUTCDate(),
		format(f) {
			const d = date();
			const parts = {
				ddd: DAYS[d.getUTCDay()],
				MMMM: LONG_MONTHS[d.getUTCMonth()],
				MMM: MONTHS[d.getUTCMonth()],
				D: String(d.getUTCDate()),
				YYYY: String(d.getUTCFullYear()),
			};
			if (f === "YYYY-MM-DD") return m.text;
			return f.replace(/YYYY|MMMM|MMM|ddd|D/g, (token) => parts[token]);
		},
		clone: () => moment(m.text),
		diff: (other) => Math.round((Date.parse(`${m.text}T00:00:00Z`) - Date.parse(`${other.text}T00:00:00Z`)) / 86400000),
		isAfter: (other) => m.text > other.text,
		add(amount, unit) {
			if (unit !== "days") throw new Error(`moment.add(${amount}, ${unit}) is not modelled`);
			const d = date();
			d.setUTCDate(d.getUTCDate() + Number(amount));
			m.text = d.toISOString().slice(0, 10);
			return m;
		},
	};
	return m;
}

function loadPage(file, cls) {
	const frappe = {
		pages: new Proxy({}, { get: (t, k) => (t[k] = t[k] || {}) }),
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		datetime: { get_today: () => "2026-11-03" },
		session: { user: "nik@example.com" },
		route_flags: {},
		utils: {
			escape_html: (v) =>
				String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"),
		},
	};
	const sandbox = {
		frappe,
		window: { localStorage: { getItem: () => null, setItem() {} }, matchMedia: () => ({ matches: false }), pageXOffset: 0, pageYOffset: 0, scrolled: [] },
		document: { elementFromPoint: () => null },
		$: () => ({}),
		__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] != null ? String(args[i]) : "")),
		moment,
		console,
		CSS: { escape: (s) => String(s) },
	};
	sandbox.window.scrollTo = (x, y) => sandbox.window.scrolled.push([x, y]);
	const context = vm.createContext(sandbox);
	vm.runInContext(
		`${fs.readFileSync(file, "utf-8")}\nthis.Page = ${cls};` +
			` this.PPL_PURE = typeof PPL_PURE === "undefined" ? null : PPL_PURE;` +
			` this.MP = typeof MP === "undefined" ? null : MP;`,
		context,
		{ filename: file }
	);
	return context;
}

function loadKit() {
	const strip = (file) =>
		fs
			.readFileSync(file, "utf8")
			.replace(/^import [\s\S]*?;\s*$/gm, "")
			.replace(/^export (const|function|class) /gm, "$1 ");
	const source = [path.join(APP, "public", "js", "sf_kit", "esc.js"), path.join(KIT, "escape.js"), path.join(KIT, "records.js")]
		.map(strip)
		.join("\n")
		.replace(/^export const escape = esc;$/m, "const escape = esc;");
	const ctx = { console, Promise, JSON };
	vm.createContext(ctx);
	vm.runInContext(`${source}\nthis.mod = { record_of, create_records, desk_url };`, ctx);
	// popover.js in a scope of its own: it has a `text` helper of its own, as records.js does.
	vm.runInContext(`(function () { ${strip(path.join(KIT, "popover.js"))}\nObject.assign(mod, { popover_html, place, create_popover }); })();`, ctx);
	return ctx.mod;
}

const kit = loadKit();
const ppCtx = loadPage(PP_JS, "ProjectPlanner");
const mpCtx = loadPage(MP_JS, "MaintenancePlanner");
const P = ppCtx.PPL_PURE;

// ------------------------------------------------------------------ a small DOM

function compound(spec) {
	const classes = Array.from(String(spec).matchAll(/\.([\w-]+)/g)).map((m) => m[1]);
	const attrs = Array.from(String(spec).matchAll(/\[([\w-]+)\]/g)).map((m) => m[1]);
	return { classes, attrs };
}

function matches(node, selector) {
	return String(selector)
		.split(",")
		.map((s) => s.trim())
		.filter(Boolean)
		.some((s) => {
			const c = compound(s);
			return c.classes.every((cls) => node.classes.has(cls)) && c.attrs.every((attr) => attr in node.attrs);
		});
}

class Node {
	constructor(spec, attrs, parent) {
		this.tagName = "DIV";
		this.classes = new Set(compound(spec || "").classes);
		this.attrs = Object.assign({}, attrs || {});
		this.parentNode = parent || null;
		this.children = [];
		this.listeners = [];
		this.style = {};
		this.innerHTML = "";
		this.isConnected = true;
		this.rect = { left: 100, top: 200, bottom: 230, width: 60, height: 30 };
		if (parent) parent.children.push(this);
	}
	get className() {
		return Array.from(this.classes).join(" ");
	}
	set className(value) {
		this.classes = new Set(String(value).split(/\s+/).filter(Boolean));
	}
	get id() {
		return this.attrs.id || "";
	}
	set id(value) {
		this.attrs.id = String(value);
	}
	getAttribute(k) {
		return k in this.attrs ? this.attrs[k] : null;
	}
	setAttribute(k, v) {
		this.attrs[k] = String(v);
	}
	hasAttribute(k) {
		return k in this.attrs;
	}
	removeAttribute(k) {
		delete this.attrs[k];
	}
	appendChild(child) {
		child.parentNode = this;
		this.children.push(child);
		return child;
	}
	removeChild(child) {
		this.children = this.children.filter((c) => c !== child);
		child.parentNode = null;
	}
	closest(selector) {
		for (let n = this; n; n = n.parentNode) if (n.classes && matches(n, selector)) return n;
		return null;
	}
	contains(other) {
		for (let n = other; n; n = n.parentNode) if (n === this) return true;
		return false;
	}
	getBoundingClientRect() {
		return this.rect;
	}
	addEventListener(type, fn) {
		this.listeners.push({ type, fn });
	}
	removeEventListener(type, fn) {
		this.listeners = this.listeners.filter((l) => !(l.type === type && l.fn === fn));
	}
	fire(type, extra) {
		const e = Object.assign({ type, target: this }, extra || {});
		this.listeners.filter((l) => l.type === type).forEach((l) => l.fn(e));
		return e;
	}
}

// ------------------------------------------------------------------ builders

// A desk the kit's records run on: where a new tab went, and a router with a slug of its own.
function desk(opts) {
	const o = opts || {};
	const opened = [];
	const frappe = { set_route: () => {}, model: { can_read: () => true } };
	if (o.router) frappe.router = { slug: (doctype) => `r-${doctype.toLowerCase().replace(/ /g, "-")}` };
	const env = { frappe, win: { open: (...args) => opened.push(args) } };
	return { env, opened, records: kit.create_records(env) };
}

function planner(extra) {
	const pp = Object.create(ppCtx.Page.prototype);
	const d = desk();
	const seen = [];
	Object.assign(pp, {
		view: "crew",
		anchor: "2026-11-03",
		project: "",
		pm: "",
		group: "",
		draft_on: false,
		data: { can_edit: true, days: {}, foreign: [] },
		by_task: {},
		by_resource: { "RES-1": { name: "RES-1", label: "Kyle Jones" }, "RES-2": { name: "RES-2", label: "Ana Hill" } },
		by_project: { "PRJ-1": { title: "Riverwalk" } },
		task_days: {},
		needs_crew: new Set(),
		modified: {},
		ppl: null,
		p6c: null,
		p6a: { kit: { records: d.records } },
		p6d_state: () => {},
		p4_card_chips: () => [],
		p4_tip_lines: () => [],
		p4_card_conflicts: () => [],
		weather_chips: () => [],
		weather_lines: () => [],
		as4_draft_project: () => false,
		p6b_next_free_item: () => ({ label: "Move to next free day" }),
		open_card: (card) => seen.push(["panel", card.name]),
		ppr_open_task: (name) => seen.push(["task", name]),
		p6a_open_project: (project) => seen.push(["glance", project]),
		ppg_open: (project) => seen.push(["gantt", project]),
		p6a_filter_project: (project) => seen.push(["only", project]),
		resource_label: (resource, label) => label || (pp.by_resource[resource] || {}).label || resource,
	});
	Object.assign(pp, extra || {});
	return { pp, d, seen };
}

function maintenance(extra) {
	const mp = Object.create(mpCtx.Page.prototype);
	const d = desk();
	const seen = [];
	Object.assign(mp, {
		view: "crew",
		data: { can_move_visits: true },
		draft_on: false,
		by_key: {},
		p6a: { kit: { records: d.records } },
		tech_name: (user) => ({ "austin@example.com": "Austin Reed", "jesse@example.com": "Jesse Cole" })[user] || user,
		visit_people: (card) => [card.technician].concat(card.crew || []).filter(Boolean),
		cell_of: () => null,
		open_card: (card) => seen.push(["panel", card.key]),
		mpr_open_card: (card) => seen.push(["record", card.key]),
		p6b_assign: () => {},
		p6b_next_free_item: () => ({ label: "Move to next free day" }),
		p6a_open_site: () => seen.push(["site"]),
		p6a_open_person: (user) => seen.push(["week", user]),
	});
	Object.assign(mp, extra || {});
	return { mp, d, seen };
}

const days_from = (ymd, count) => {
	const out = [];
	const m = moment(ymd);
	for (let i = 0; i < count; i++) {
		out.push(m.text);
		m.add(1, "days");
	}
	return out;
};

// ------------------------------------------------------------------ 1. the month band

test("PPL_PURE.month_bands: one entry a month, its column and its days; the year only across a year end", () => {
	const three = days_from("2026-10-18", 21);
	check(
		"October's two weeks, then November's one",
		P.month_bands(three).map((m) => [m.key, m.ymd, m.at, m.span, m.label, m.short]),
		[
			["2026-10", "2026-10-18", 0, 14, "October", "Oct"],
			["2026-11", "2026-11-01", 14, 7, "November", "Nov"],
		]
	);
	check("one month alone", P.month_bands(days_from("2026-11-01", 14)).map((m) => [m.label, m.span]), [["November", 14]]);
	check(
		"a window over New Year says the year",
		P.month_bands(days_from("2026-12-27", 21)).map((m) => [m.label, m.short, m.span]),
		[
			["December 2026", "Dec 2026", 5],
			["January 2027", "Jan 2027", 16],
		]
	);
	check("nothing from nothing", [P.month_bands([]), P.month_bands(null)], [[], []]);
});

test("The band over the headings: a cell a month across its days, the short name where few days show", () => {
	const { pp } = planner({ view: "lookahead", data: { lookahead: {}, day_notes: {}, tasks: [], unscheduled: [] } });
	const days = days_from("2026-10-18", 21);
	const band = pp.ppl_monthband_html(days);
	check("a sticky row of its own, with the corner first", band.startsWith('<div class="ppl-row ppl-months" role="row"><div class="ppl-label ppl-corner"'), true);
	check(
		"October spans columns 2–15, November 16–22; the first cell has no left rule",
		[
			band.includes('<div class="ppl-month ppl-month-first" role="columnheader" style="grid-column: 2 / span 14" title="October"><span>October</span></div>'),
			band.includes('<div class="ppl-month" role="columnheader" style="grid-column: 16 / span 7" title="November"><span>November</span></div>'),
		],
		[true, true]
	);
	const edge = pp.ppl_monthband_html(days_from("2026-10-30", 7));
	check("two days of October read Oct, with the full name on hover", edge.includes('title="October"><span>Oct</span>'), true);
	check("the first of a month is marked on its day cells, a week start still is", [pp.ppl_day_classes("2026-11-01"), pp.ppl_day_classes("2026-11-02"), pp.ppl_day_classes("2026-11-08")], [
		["ppl-weekend", "ppl-week-start", "ppl-month-start"],
		[],
		["ppl-weekend", "ppl-week-start"],
	]);
	const grid = pp.ppl_grid_html([], days);
	const at_sticky = grid.indexOf('<div class="ppl-sticky">');
	const at_band = grid.indexOf('class="ppl-row ppl-months"');
	const at_heads = grid.indexOf('class="ppl-row ppl-heads"');
	check("the board draws one sticky block: the band, then the day headings", [at_sticky > 0, at_band > at_sticky, at_heads > at_band], [true, true, true]);
	check("the block closes after the headings' row, before the first body row", grid.indexOf("</div></div>", at_heads) < grid.indexOf("ppl-empty"), true);
	check("the day headings still read the day", grid.includes("<span>Sun 18</span>"), true);
});

// ------------------------------------------------------------------ 2. open in a new tab

test("records.desk_url: the Desk address a tap would route to", () => {
	check("a Task", kit.desk_url({ doctype: "Task", name: "TASK-2026-00001" }), "/desk/task/TASK-2026-00001");
	check("a doctype with spaces", kit.desk_url({ doctype: "Sapphire Maintenance Record", name: "SMR-1" }), "/desk/sapphire-maintenance-record/SMR-1");
	check("a name is encoded", kit.desk_url({ doctype: "Project", name: "PRJ 1/a&b" }), "/desk/project/PRJ%201%2Fa%26b");
	check("the router's slug wins when there is one", kit.desk_url({ doctype: "Travel Trip", name: "T" }, (d) => `x-${d.length}`), "/desk/x-11/T");
	check("nothing from nothing", [kit.desk_url(null), kit.desk_url({ doctype: "Task" }), kit.desk_url({ name: "T" })], ["", "", ""]);
});

test("records.open_new_tab: window.open on that address, through the router's slug, and false with nothing", () => {
	let d = desk({ router: true });
	check("opens a new tab", d.records.open_new_tab({ doctype: "Task", name: "T1" }), true);
	check("with noopener, on the router's own slug", d.opened, [["/desk/r-task/T1", "_blank", "noopener"]]);
	check("url() says the same", d.records.url({ doctype: "Sapphire Maintenance Record", name: "SMR-1" }), "/desk/r-sapphire-maintenance-record/SMR-1");
	d = desk();
	check("no router: the plain spelling", [d.records.open_new_tab({ doctype: "Sapphire Maintenance Record", name: "SMR-1" }), d.opened], [true, [["/desk/sapphire-maintenance-record/SMR-1", "_blank", "noopener"]]]);
	d = desk();
	check("nothing to open: false, and no tab", [d.records.open_new_tab(null), d.records.open_new_tab({ doctype: "Task" }), d.opened], [false, false, []]);
});

test("Project Planner: Open task / Open project in new tab on the menus, Ctrl+Enter on a focused card or name", () => {
	const { pp, d, seen } = planner();
	pp.by_task = {
		T1: { name: "T1", subject: "Dig", project: "PRJ-1", movable: true, start: "2026-11-03", end: "2026-11-03", modified: "m" },
		"draft-new:PDC-1": { name: "draft-new:PDC-1", subject: "New", project: "PRJ-1", new_task: true, movable: true },
	};
	const labels = (items) => items.filter((item) => !item.divider).map((item) => item.label);
	const items = pp.p6b_card_items({ kind: "card", card: pp.by_task.T1, ymd: "2026-11-03" });
	check("after Open task, Open task in new tab with its key", [labels(items).slice(0, 3), items[2].hint], [["Quick edit", "Open task", "Open task in new tab"], "Ctrl+Enter"]);
	check("Open project in new tab follows Open project", labels(items).slice(-2), ["Open project", "Open project in new tab"]);
	items[2].on_click();
	items[items.length - 1].on_click();
	check("each opens its record in a new tab and nothing else", [d.opened, seen], [[["/desk/task/T1", "_blank", "noopener"], ["/desk/project/PRJ-1", "_blank", "noopener"]], []]);
	const fresh = pp.p6b_card_items({ kind: "card", card: pp.by_task["draft-new:PDC-1"], ymd: null });
	check("a drafted new task has no Task to open in a tab either", labels(fresh).filter((l) => l.includes("new tab")), ["Open project in new tab"]);
	const project = pp.ppr_project_items("PRJ-1");
	check("a project's name: Open project, then in a new tab", labels(project).slice(0, 2), ["Open project", "Open project in new tab"]);
	// Ctrl+Enter's target.
	const root = new Node(".pp-wrap");
	const card = new Node(".pp-card", { "data-task": "T1" }, root);
	const title = new Node(".pp-card-title", {}, card);
	const name = new Node(".ppl-project-name", { "data-pk-project": "PRJ-1" }, new Node(".ppl-label", {}, root));
	const label = new Node(".ppl-task-name", { "data-ppr-task": "T1" }, new Node(".ppl-label", {}, root));
	const draft = new Node(".pp-card", { "data-task": "draft-new:PDC-1" }, root);
	const phase = new Node(".ppl-phase-name", { "data-ppr-task": "PH-300" }, new Node(".ppl-label", {}, root));
	check(
		"a card (from inside it), a Lookahead task name, a project name, a phase name off the board; a drafted task and a bare spot are nothing",
		[pp.ppr_new_tab_for(title), pp.ppr_new_tab_for(label), pp.ppr_new_tab_for(name), pp.ppr_new_tab_for(phase), pp.ppr_new_tab_for(draft), pp.ppr_new_tab_for(root)],
		[true, true, true, true, false, false]
	);
	check("four tabs", d.opened.slice(2).map((a) => a[0]), ["/desk/task/T1", "/desk/task/T1", "/desk/project/PRJ-1", "/desk/task/PH-300"]);
});

test("Maintenance Planner: Open visit / Open contract in new tab, Ctrl+Enter's record of each card kind", () => {
	const { mp, d } = maintenance();
	const visit = { kind: "visit", name: "SMR-1", key: "v1", status: "draft", movable: true, technician: "austin@example.com" };
	const projected = { kind: "projected", key: "p1", contract: "CON-1", movable: false };
	const move = { kind: "move", name: "TASK-9", key: "m1" };
	const booking = { kind: "booking", key: "b1", ref: "TASK-5", booking_kind: "task", open: true };
	const trip = { kind: "booking", key: "b2", ref: "TRIP-1", booking_kind: "travel", open: false };
	const labels = (items) => items.filter((item) => !item.divider).map((item) => item.label);
	const items = mp.p6b_card_items({ kind: "card", card: visit });
	check("Quick edit, Open visit, Open visit in new tab", [labels(items).slice(0, 3), items[2].hint], [["Quick edit", "Open visit", "Open visit in new tab"], "Ctrl+Enter"]);
	items[2].on_click();
	check("the visit's record in a new tab", d.opened, [["/desk/sapphire-maintenance-record/SMR-1", "_blank", "noopener"]]);
	const contract = mp.p6b_card_items({ kind: "card", card: Object.assign({ contract: "CON-1" }, projected) });
	check("a projected visit: Open contract in new tab", labels(contract).slice(1, 3), ["Open contract", "Open contract in new tab"]);
	check(
		"what Ctrl+Enter opens for each kind of card",
		[visit, projected, move, booking, trip, { kind: "wat" }].map((card) => mp.mpr_record_of(card)),
		[
			{ doctype: "Sapphire Maintenance Record", name: "SMR-1" },
			{ doctype: "Sapphire Maintenance Contract", name: "CON-1" },
			{ doctype: "Task", name: "TASK-9" },
			{ doctype: "Task", name: "TASK-5" },
			null,
			null,
		]
	);
	check("a refused trip opens no tab", [mp.mpr_open_new_tab(trip), mp.mpr_open_new_tab(move), d.opened.length], [false, true, 2]);
});

// ------------------------------------------------------------------ 3. the scroll a redraw loses

test("ppl_scroll_save / restore: the board's pane, a scrolling ancestor and the window come back", () => {
	const { pp } = planner();
	const pane = { scrollTop: 120, scrollLeft: 40 };
	const page = { nodeType: 1, scrollTop: 300, scrollLeft: 0, parentNode: { nodeType: 9 } };
	const still = { nodeType: 1, scrollTop: 0, scrollLeft: 0, parentNode: page };
	const wrap = { querySelector: (sel) => (sel === ".pp-scroll" ? pane : null), parentNode: still };
	pp.$grid_wrap = [wrap];
	const win = ppCtx.window;
	win.pageYOffset = 500;
	win.pageXOffset = 0;
	win.scrolled = [];
	const saved = pp.ppl_scroll_save();
	check("what was taken", [saved.pane, saved.ancestors.map((a) => [a.top, a.left]), saved.win], [{ top: 120, left: 40 }, [[300, 0]], { x: 0, y: 500 }]);
	let redrawn = 0;
	pp.ppl_keep_scroll(() => {
		redrawn += 1;
		// What a redraw does: a new pane at 0, the page clamped to the top.
		pane.scrollTop = 0;
		pane.scrollLeft = 0;
		page.scrollTop = 0;
		win.pageYOffset = 0;
	});
	check("the redraw ran once, and everything is back", [redrawn, pane.scrollTop, pane.scrollLeft, page.scrollTop, still.scrollTop, win.scrolled], [1, 120, 40, 300, 0, [[0, 500]]]);
	win.scrolled = [];
	win.pageYOffset = 0;
	pp.ppl_keep_scroll(() => {});
	check("at the top already: the window is left alone", win.scrolled, []);
	pp.$grid_wrap = [{ querySelector: () => null, parentNode: null }];
	check("no pane, no ancestors: nothing to restore, nothing thrown", pp.ppl_scroll_save().pane, null);
	let threw = false;
	try {
		pp.ppl_keep_scroll(() => {
			throw new Error("x");
		});
	} catch (e) {
		threw = true;
	}
	check("a redraw that throws still restores and rethrows", threw, true);
});

test("ppl_toggle and ppl_set_all redraw inside the keeper", () => {
	const { pp } = planner({ view: "lookahead", data: { lookahead: { projects: [{ name: "PRJ-1", phases: [] }] } } });
	const log = [];
	pp.ppl_state = () => (pp.ppl = pp.ppl || { collapsed: new Set(), shown_all: new Set(), needs: new Set() });
	pp.ppl_save_collapsed = () => log.push("saved");
	pp.ppl_keep_scroll = (fn) => {
		log.push("keep");
		fn();
	};
	pp.render = () => log.push("render");
	pp.ppl_toggle("p:PRJ-1");
	pp.ppl_set_all(true);
	check("saved, then a kept redraw, each time", log, ["saved", "keep", "render", "saved", "keep", "render"]);
	check("the row is collapsed", Array.from(pp.ppl.collapsed), ["p:PRJ-1"]);
});

// ------------------------------------------------------------------ 5. the Crew board's order

test("booking_order: a drive and a block first, then by project name and task name, foreign bookings among them", () => {
	const { pp } = planner();
	pp.by_task = {
		T1: { name: "T1", subject: "Zed", project: "PRJ-2", project_title: "Beta" },
		T2: { name: "T2", subject: "Dig", project: "PRJ-1", project_title: "Alpha" },
		T3: { name: "T3", subject: "Backfill", project: "PRJ-1", project_title: "Alpha" },
	};
	const bookings = [
		{ kind: "task", ref: "T1" },
		{ kind: "travel", ref: "TRIP-1", label: "Denver trip", project: "Gamma" },
		{ kind: "block", ref: "PB-1" },
		{ kind: "task", ref: "T2" },
		{ kind: "visit", ref: "SMR-1", label: "Plaza visit" },
		{ kind: "drive" },
		{ kind: "task", ref: "T3" },
	];
	const sorted = bookings.slice().sort((a, b) => pp.booking_order(a, b));
	check(
		"drive, block, Alpha's Backfill then Dig, Beta's Zed, Gamma's trip, the visit with no project last",
		sorted.map((b) => b.ref || b.kind),
		["drive", "PB-1", "T3", "T2", "T1", "TRIP-1", "SMR-1"]
	);
	check("the Calendar's foreign items take the same order", [{ label: "B", kind: "visit" }, { label: "A", kind: "rental" }].sort((a, b) => pp.booking_order(a, b)).map((i) => i.label), ["A", "B"]);
	// Review of #1419: a trip's booking carries the project ID only; the page's own project list gives
	// its title, so it sorts among that project's tasks and never as "PRJ-…" between titles.
	pp.by_project = { "PRJ-1": { title: "Alpha" }, "PRJ-2": { title: "Beta" } };
	const mixed = [
		{ kind: "task", ref: "T1" },
		{ kind: "travel", ref: "TRIP-2", label: "Zion trip", project: "PRJ-1" },
		{ kind: "task", ref: "T3" },
		{ kind: "visit", ref: "SMR-9", label: "Plaza", project: "PRJ-9" },
	].sort((a, b) => pp.booking_order(a, b));
	check("an ID resolves to its title; an unknown ID stays an ID and sorts by it", mixed.map((b) => b.ref), ["T3", "TRIP-2", "T1", "SMR-9"]);
	check("project_name: the title, the known title for an ID, the ID, nothing", [pp.project_name({ project_title: "X", project: "PRJ-1" }), pp.project_name({ project: "PRJ-2" }), pp.project_name({ project: "PRJ-9" }), pp.project_name({}), pp.project_name(null)], ["X", "Beta", "PRJ-9", "", ""]);
	check("the sort is stable for equals", [{ ref: "T2", kind: "task", tag: 1 }, { ref: "T2", kind: "task", tag: 2 }].sort((a, b) => pp.booking_order(a, b)).map((b) => b.tag), [1, 2]);
});

// ------------------------------------------------------------------ 5. the hover card

test("popover_html: the card's parts, escaped; empty rows and lines left out", () => {
	const html = kit.popover_html({
		title: '<img src=x onerror="alert(1)">',
		subtitle: "Riverwalk & Co",
		tags: ["Pencil (tentative)", ""],
		rows: [
			["When", "Mon, Nov 2 – Wed, Nov 4"],
			["Time", ""],
			["Crew", "Kyle <Jones>"],
		],
		warnings: ["Over by 2h", null],
		notes: ["Hours: 6h of 8h"],
	});
	check("the title is text", [html.includes("<img"), html.includes('<div class="pk-pop-title">&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</div>')], [false, true]);
	check("the subtitle, the tag", [html.includes('<div class="pk-pop-sub">Riverwalk &amp; Co</div>'), html.includes('<span class="pk-pop-tag">Pencil (tentative)</span>')], [true, true]);
	check("rows as a definition list, the blank one dropped", html.includes('<dl class="pk-pop-rows"><dt>When</dt><dd>Mon, Nov 2 – Wed, Nov 4</dd><dt>Crew</dt><dd>Kyle &lt;Jones&gt;</dd></dl>'), true);
	check("warnings and notes", [html.includes('<ul class="pk-pop-warnings"><li>Over by 2h</li></ul>'), html.includes('<ul class="pk-pop-notes"><li>Hours: 6h of 8h</li></ul>')], [true, true]);
	check("nothing from nothing", [kit.popover_html(null), kit.popover_html({ rows: [["a", ""]] })], ["", ""]);
});

test("place: under the chip, inside the viewport, above it when there is no room below", () => {
	const view = { w: 1000, h: 600 };
	check("under, aligned left", kit.place({ left: 100, top: 200, bottom: 230 }, { width: 240, height: 100 }, view), { x: 100, y: 236 });
	check("kept off the right edge", kit.place({ left: 900, top: 200, bottom: 230 }, { width: 240, height: 100 }, view), { x: 752, y: 236 });
	check("flipped above near the bottom", kit.place({ left: 100, top: 560, bottom: 590 }, { width: 240, height: 100 }, view), { x: 100, y: 454 });
	check("no room either way: as high as it must be, never above the top", kit.place({ left: 100, top: 60, bottom: 590 }, { width: 240, height: 100 }, view), { x: 100, y: 492 });
	check("a card taller than the window starts at the top", kit.place({ left: 100, top: 60, bottom: 90 }, { width: 240, height: 900 }, view), { x: 100, y: 8 });
});

function hoverEnv() {
	const doc = new Node("body");
	doc.body = doc;
	doc.createElement = (tag) => {
		const node = new Node("");
		node.tagName = String(tag).toUpperCase();
		return node;
	};
	const timers = [];
	const routes = [];
	const env = {
		doc,
		win: { innerWidth: 1024, innerHeight: 768 },
		set_timeout: (fn, ms) => timers.push({ fn, ms, cleared: false }),
		clear_timeout: (id) => timers[id - 1] && (timers[id - 1].cleared = true),
		on_route_change: (fn) => routes.push(fn),
		warn: () => {},
	};
	const fire_timers = () => timers.splice(0).filter((t) => !t.cleared).forEach((t) => t.fn());
	return { env, doc, timers, routes, fire_timers };
}

test("popover.attach: shows after the delay for a mouse, holds the title aside, closes on leave and Esc; never for a finger", () => {
	const { env, doc, timers, routes, fire_timers } = hoverEnv();
	const pop = kit.create_popover(env);
	const root = new Node(".pp-wrap");
	const chip = new Node(".pp-card", { "data-task": "T1", title: "Dig\nRiverwalk" }, root);
	const inner = new Node(".pp-card-title", {}, chip);
	const described = [];
	const hover = pop.attach(root, {
		selector: ".pp-card[data-task], .pp-fcard",
		describe: (el) => (described.push(el.getAttribute("data-task")), { title: "Dig", rows: [["When", "Mon, Nov 2"]] }),
	});
	root.fire("pointerover", { target: inner, pointerType: "touch" });
	check("a finger asks for nothing", timers.length, 0);
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	check("a mouse waits the delay, and nothing shows yet", [timers.length, timers[0].ms, pop.current()], [1, 350, null]);
	fire_timers();
	const card = pop.current();
	check("then the card is in the document, described once", [!!card, card && card.id, card && card.getAttribute("role"), described, doc.children.includes(card)], [true, "pk-pop", "tooltip", ["T1"], true]);
	check("drawn from the description, placed under the chip", [card.innerHTML.includes('<dd>Mon, Nov 2</dd>'), card.style.left, card.style.top], [true, "100px", "236px"]);
	check("the chip's title is held aside and it points at the card", [chip.hasAttribute("title"), chip.getAttribute("aria-describedby")], [false, "pk-pop"]);
	root.fire("pointerout", { target: inner, relatedTarget: chip });
	check("moving within the chip keeps it", pop.current() === card, true);
	root.fire("pointerout", { target: inner, relatedTarget: root });
	check("leaving closes it and gives the title back", [pop.current(), chip.getAttribute("title"), chip.hasAttribute("aria-describedby"), doc.children.length], [null, "Dig\nRiverwalk", false, 0]);
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	root.fire("pointerout", { target: inner, relatedTarget: root });
	fire_timers();
	check("leaving before the delay shows nothing", pop.current(), null);
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	fire_timers();
	doc.fire("keydown", { key: "Escape" });
	check("Esc closes it", pop.current(), null);
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	fire_timers();
	root.fire("pointerdown", { target: inner });
	check("a press closes it (a drag or a menu is starting)", pop.current(), null);
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	fire_timers();
	check("and no card comes back for that chip while the mouse is still on it", [timers.length, pop.current()], [0, null]);
	root.fire("pointerout", { target: inner, relatedTarget: root });
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	fire_timers();
	check("once the mouse has left and come back, it does", pop.current() !== null, true);
	// The board is redrawn: the chip leaves the document and its card must not stay.
	chip.isConnected = false;
	root.fire("pointerover", { target: root, pointerType: "mouse" });
	check("the next move over anything takes a card whose chip is gone", pop.current(), null);
	chip.isConnected = true;
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	chip.isConnected = false;
	fire_timers();
	check("a timer that fires after the redraw draws nothing for a detached chip", pop.current(), null);
	chip.isConnected = true;
	root.fire("pointerout", { target: inner, relatedTarget: root });
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	fire_timers();
	routes.forEach((fn) => fn());
	check("a route change closes it", pop.current(), null);
	root.fire("pointerover", { target: root, pointerType: "mouse" });
	check("the mouse over nothing of ours asks for nothing", timers.length, 0);
	doc.className = "pp-drag-active";
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	fire_timers();
	check("nothing while a drag is on", pop.current(), null);
	doc.className = "";
	hover.destroy();
	root.fire("pointerover", { target: inner, pointerType: "mouse" });
	check("destroyed: it listens no more", timers.length, 0);
});

test("popover.attach: a describe that says null or throws shows nothing, and one card at a time", () => {
	const { env, fire_timers } = hoverEnv();
	const pop = kit.create_popover(env);
	const root = new Node(".pp-wrap");
	const a = new Node(".pp-card", { "data-task": "A", title: "a" }, root);
	const b = new Node(".pp-card", { "data-task": "B" }, root);
	const none = new Node(".pp-card", { "data-task": "N" }, root);
	pop.attach(root, {
		selector: ".pp-card",
		describe: (el) => {
			if (el === none) return null;
			if (el === b) throw new Error("boom");
			return { title: el.getAttribute("data-task") };
		},
	});
	root.fire("pointerover", { target: none, pointerType: "mouse" });
	fire_timers();
	check("null: nothing", pop.current(), null);
	root.fire("pointerover", { target: b, pointerType: "mouse" });
	fire_timers();
	check("a throw: nothing, and nothing thrown", pop.current(), null);
	root.fire("pointerover", { target: a, pointerType: "mouse" });
	fire_timers();
	const first = pop.current();
	root.fire("pointerover", { target: a, pointerType: "mouse" });
	check("hovering the same chip again does not redraw", [first !== null, pop.current() === first], [true, true]);
	root.fire("pointerover", { target: b, pointerType: "mouse" });
	fire_timers();
	check("another chip with nothing to say takes the first card down", [pop.current(), a.getAttribute("title")], [null, "a"]);
});

test("Project Planner: the card for a task chip says its days, time, hours, crew, conflicts and notes", () => {
	const { pp } = planner();
	const card = {
		name: "T1",
		subject: "Excavate basin",
		project: "PRJ-1",
		project_title: "Riverwalk",
		start: "2026-11-02",
		end: "2026-11-04",
		expected_time: 12,
		tentative: true,
		short: 1,
		crew: [
			{ resource: "RES-1", label: "Kyle Jones", is_lead: true },
			{ resource: "RES-2", label: "Ana Hill" },
		],
		blocked_by: [{ task: "T0", subject: "Survey", end: "2026-11-03" }],
		qualification_gaps: ["Confined Space Entry"],
	};
	pp.by_task = { T1: card };
	pp.data.days = { "RES-1": { "2026-11-03": { bookings: [{ kind: "drive" }, { kind: "task", ref: "T1", hours: 4, slot: ["8:00", "12:00"], tentative: true }] } } };
	pp.task_days = { T1: [{ resource: "RES-1", ymd: "2026-11-03", day: { conflicts: ["Over by 2h"] } }] };
	pp.p4_tip_lines = () => ["Hours: 6h of 12h"];
	const desc = pp.pph_task(card, "2026-11-03", "RES-1");
	check("title and project", [desc.title, desc.subtitle, desc.tags], ["Excavate basin", "Riverwalk", ["Pencil (tentative)"]]);
	check(
		"the rows",
		desc.rows,
		[
			["When", "Mon, Nov 2 – Wed, Nov 4"],
			["Time", "8:00–12:00"],
			["Hours", "4h on Tue, Nov 3 · 12h planned"],
			["Crew", "Kyle Jones (lead), Ana Hill, needs 1 more"],
			["Task", "T1"],
		]
	);
	check("conflicts and warnings, then the notes", [desc.warnings, desc.notes], [["Kyle Jones, Tue, Nov 3: Over by 2h", "Starts before T0 (Survey) ends on Tue, Nov 3", "No one on the crew holds: Confined Space Entry"], ["Hours: 6h of 12h"]]);
	const plain = pp.pph_task({ name: "T2", subject: "Pour", start: "2026-11-05", end: "2026-11-05", crew: [] }, "", "");
	check("a calendar card: one day, a full day, nobody yet, no booking line", plain.rows, [
		["When", "Thu, Nov 5"],
		["Time", "Full day"],
		["Hours", ""],
		["Crew", "Nobody yet"],
		["Task", "T2"],
	]);
	const undated = pp.pph_task({ name: "draft-new:1", subject: "New", new_task: true, crew: [] }, "", "");
	check("a drafted, undated task", [undated.rows[0], undated.rows[4], undated.tags], [["When", "Not scheduled"], ["Task", ""], ["Draft: not published yet"]]);
	const root = new Node(".pp-wrap");
	const chip = new Node(".pp-card", { "data-task": "T1", "data-date": "2026-11-03", "data-resource": "RES-1" }, root);
	check("pph_describe finds the card from inside the chip", pp.pph_describe(new Node(".pp-card-sub", {}, chip)).rows[2], ["Hours", "4h on Tue, Nov 3 · 12h planned"]);
	check("and nothing for a chip it does not know", pp.pph_describe(new Node(".pp-card", { "data-task": "T9" }, root)), null);
});

test("Project Planner: the card for another planner's booking, on the Calendar and on the Crew board", () => {
	const { pp } = planner();
	pp.data.foreign = [{ kind: "visit", key: "visit|SMR-1|RES-1|2026-11-03", ref: "SMR-1", label: "Plaza fountain", date: "2026-11-03", resource: "RES-1", hours: 3, tentative: true, project: "PRJ-1", slot: ["8:00", "11:00"] }];
	pp.data.days = { "RES-2": { "2026-11-04": { bookings: [{ kind: "travel", ref: "TRIP-1", label: "Denver trip", hours: 8, estimated: true, project: "PRJ-7" }] } } };
	const root = new Node(".pp-wrap");
	const cal = new Node(".pp-fcard", { "data-fkey": "visit|SMR-1|RES-1|2026-11-03" }, root);
	const desc = pp.pph_foreign(cal);
	check("a Calendar visit", [desc.title, desc.subtitle, desc.tags, desc.rows, desc.notes], [
		"Plaza fountain",
		"Maintenance visit",
		["Pencil (tentative)"],
		[
			["When", "Tue, Nov 3"],
			["Time", "8:00–11:00"],
			["Hours", "3h"],
			["Who", "Kyle Jones"],
			["Project", "Riverwalk"],
		],
		["Read-only here: another planner's booking."],
	]);
	const chip_html = pp.foreign_html(pp.data.foreign[0]);
	check("the Calendar chip says the project and the time slot too", chip_html.includes("Maintenance · Riverwalk · Kyle Jones · 8:00–11:00 · Pencil"), true);
	const cell = new Node(".pp-cell", { "data-date": "2026-11-04", "data-resource": "RES-2" }, root);
	const crew = new Node(".pp-fcard", { "data-ref": "TRIP-1", "data-kind": "travel", "data-date": "2026-11-04" }, cell);
	const trip = pp.pph_describe(crew);
	check("a Crew board trip, from the person-day's bookings", [trip.title, trip.subtitle, trip.tags, trip.rows.slice(0, 5)], [
		"Denver trip",
		"Travel",
		["Estimated"],
		[
			["When", "Wed, Nov 4"],
			["Time", ""],
			["Hours", "8h"],
			["Who", "Ana Hill"],
			["Project", "PRJ-7"],
		],
	]);
	check("an unknown chip is nothing", pp.pph_foreign(new Node(".pp-fcard", { "data-fkey": "nope" }, root)), null);
});

test("Maintenance Planner: the card for a visit, a projected visit and project work", () => {
	const { mp } = maintenance();
	const visit = {
		kind: "visit",
		key: "v1",
		name: "SMR-1",
		site: "Plaza",
		site_full: "Plaza fountain, Salt Lake City",
		project: "PRJ-3",
		label: "Startup",
		date: "2026-11-03",
		slot: ["9:00", "11:00"],
		planned_hours: 2,
		technician: "austin@example.com",
		crew: ["jesse@example.com"],
		status: "draft",
		tentative: true,
		overdue: true,
		flagged: true,
	};
	mp.by_key = { v1: visit };
	const desc = mp.mph_card(visit);
	check("the site, its full address, the marks", [desc.title, desc.subtitle, desc.tags], ["Plaza", "Plaza fountain, Salt Lake City", ["Pencil (tentative)"]]);
	check("the rows", desc.rows, [
		["Visit", "Startup"],
		["When", "Tue, Nov 3"],
		["Time", "9:00–11:00"],
		["Hours", "2h each"],
		["Crew", "Austin Reed, Jesse Cole"],
		["Record", "SMR-1"],
	]);
	check("what is wrong with it", desc.warnings, ["Overdue", "Chemistry flagged"]);
	const projected = mp.mph_card({ kind: "projected", key: "p1", site: "Tanner", frequency: "Monthly", hours: 4, movable: true, technician: "austin@example.com" });
	check("a projected visit", [projected.tags, projected.rows[0], projected.rows[1], projected.rows[3], projected.rows[4]], [["Next visit"], ["Visit", "Monthly"], ["When", "Not scheduled"], ["Hours", "4h"], ["Who", "Austin Reed"]]);
	const work = mp.mph_card({ kind: "booking", key: "b1", ref: "TASK-5", label: "Dig", project: "Riverwalk", booking_kind: "task", hours: 6, technician: "jesse@example.com", date: "2026-11-04" });
	check("project work, read-only", [work.title, work.subtitle, work.tags, work.rows[3], work.rows[5], work.notes], ["Dig", "Riverwalk", ["Project"], ["Hours", "6h"], ["Record", "TASK-5"], ["Read-only. Move it on the Project Planner."]]);
	const root = new Node(".mp-wrap");
	const chip = new Node(".mp-card", { "data-key": "v1" }, root);
	check("mph_describe from inside the card, nothing for a key it does not know", [mp.mph_describe(new Node(".mp-card-sub", {}, chip)).title, mp.mph_describe(new Node(".mp-card", { "data-key": "x" }, root))], ["Plaza", null]);
});

// ------------------------------------------------------------------ 6. the pencil-only hours rule

test("Project Planner avail_state: the pencil alone when it covers the free hours, else both without a plus", () => {
	const { pp } = planner();
	const text = (day) => pp.avail_state(day).text;
	check("all pencil", text({ capacity: 8, booked: 0, free: 8, soft_booked: 8 }), "8h pencil");
	check("pencil equal to the free hours", text({ capacity: 8, booked: 4, free: 4, soft_booked: 4 }), "4h pencil");
	check("pencil over the free hours", text({ capacity: 8, booked: 6, free: 2, soft_booked: 5 }), "5h pencil");
	check("pencil under the free hours: both, no plus", text({ capacity: 8, booked: 4, free: 4, soft_booked: 2 }), "4h free · 2h pencil");
	check("no pencil: as before", text({ capacity: 8, booked: 4, free: 4, soft_booked: 0 }), "4h free");
	check("full, with a pencil over it", text({ capacity: 8, booked: 8, free: 0, soft_booked: 2 }), "Full · 2h pencil");
	check("over, with a pencil", text({ capacity: 8, booked: 10, free: 0, soft_booked: 1 }), "Over 2h · 1h pencil");
	check("a holiday with a pencil", text({ capacity: 0, booked: 0, free: 0, soft_booked: 4, off: "Holiday" }), "Holiday · 4h pencil");
	check("half a day off keeps its note after the pencil", text({ capacity: 4, booked: 0, free: 4, soft_booked: 4, off: "Half day" }), "4h pencil · ½ day off");
	const state = pp.avail_state({ capacity: 8, booked: 4, free: 4, soft_booked: 6 });
	check("the class and the soft figures are what they were", [state.cls, state.soft, state.soft_ratio, "pencil_said" in state], ["pp-green pp-softover", 6, 0.5, false]);
});

test("The Capacity heatmap: an all-pencil week says only the pencil, otherwise the hours and the pencil without a plus", () => {
	const { pp } = planner();
	check("all pencil", pp.heat_state({ capacity: 40, booked: 0, soft_booked: 8 }).sub, "8h pencil");
	check("firm hours and a pencil", pp.heat_state({ capacity: 40, booked: 36, soft_booked: 4 }).sub, "36h of 40h · 4h pencil");
	check("over, with a day over", pp.heat_state({ capacity: 40, booked: 44, soft_booked: 4, over_days: 1 }).sub, "44h of 40h · 4h pencil · 1 day over");
	check("no pencil", pp.heat_state({ capacity: 40, booked: 20, soft_booked: 0 }).sub, "20h of 40h");
	check("an off week with a pencil", pp.heat_state({ capacity: 0, booked: 0, soft_booked: 3 }).sub, "3h pencil");
});

test("Maintenance Planner avail_state: the same rule, on the cell and not only in its tooltip", () => {
	const { mp } = maintenance();
	const text = (cell) => mp.avail_state(cell).text;
	check("all pencil", text({ capacity: 8, booked: 0, free: 8, soft_booked: 8 }), "8h pencil");
	check("pencil under the free hours", text({ capacity: 8, booked: 4, free: 4, soft_booked: 2 }), "4h free · 2h pencil");
	check("pencil equal to the free hours", text({ capacity: 8, booked: 4, free: 4, soft_booked: 4 }), "4h pencil");
	check("no pencil", text({ capacity: 8, booked: 2, free: 6 }), "6h free");
	check("full keeps the pencil after it", text({ capacity: 8, booked: 8, free: 0, soft_booked: 3 }), "Full · 3h pencil");
	const state = mp.avail_state({ capacity: 8, booked: 4, free: 4, soft_booked: 4 });
	check("kind, class and the soft hours it carries", [state.kind, state.cls, state.soft], ["free", "mp-green", 4]);
});

test("The kit's day_state (the quick looks) reads the same", () => {
	const src = fs.readFileSync(path.join(KIT, "peeks.js"), "utf8");
	const ctx = { console };
	vm.createContext(ctx);
	const t = (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] != null ? String(args[i]) : ""));
	const hours = (v) => String(Math.round((Number(v) || 0) * 100) / 100);
	const body = src.slice(src.indexOf("export function day_state"), src.indexOf("function drag_attrs")).replace("export function", "function");
	vm.runInContext(`const t = ${t.toString()}; const hours = ${hours.toString()}; ${body}; this.day_state = day_state;`, ctx);
	const text = (day) => ctx.day_state(day).text;
	check("all pencil", text({ capacity: 8, booked: 0, free: 8, soft_booked: 8 }), "8h pencil");
	check("pencil under the free hours", text({ capacity: 8, booked: 6, free: 2, soft_booked: 1 }), "2h free · 1h pencil");
	check("full with a pencil", text({ capacity: 8, booked: 8, free: 0, soft_booked: 2 }), "Full · 2h pencil");
	check("off with a pencil", text({ capacity: 0, booked: 0, free: 0, soft_booked: 2 }), "Off · 2h pencil");
	check("no leftover field", "pencil_said" in ctx.day_state({ capacity: 8, booked: 0, free: 8, soft_booked: 8 }), false);
});

// ------------------------------------------------------------------ run

(async () => {
	for (const t of tests) {
		try {
			await t.fn();
			console.log(`ok   ${t.name}`);
		} catch (e) {
			failures += 1;
			console.log(`  ERROR ${e && e.stack ? e.stack : e}`);
			console.log(`FAIL ${t.name}`);
		}
	}
	console.log(failures ? `\n${failures} of ${checks} checks failed` : `\nall ${checks} checks passed`);
	process.exit(failures ? 1 : 0);
})();
