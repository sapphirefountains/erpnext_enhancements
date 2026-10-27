#!/usr/bin/env node
/**
 * A tap on a planner item opens its record (Nik, 2026-10-27: "tapping the task should take you to the
 * task, tapping the project should take you to the project, and so on"), run under node against the REAL
 * kit modules (planner_kit/records.js, panel.js, peeks.js) and the REAL Project and Maintenance Planner
 * page scripts.
 *
 *   1. records.record_of, the one map from what a planner draws to its record: a task, a Fountain Move and
 *      a rental's work are Tasks, a drafted new task is none, a visit is its Sapphire Maintenance Record and
 *      a projected one its contract (however the page carries it), a person their Employee else their
 *      Planner Resource, and a record this person cannot read is none.
 *   2. records.open: through drawer.navigate (so a drawer's history entry is replaced, not stacked on),
 *      in the same tab; and when the side panel has unsaved edits, only after the person agrees.
 *   3. The side panel's unsaved edits: counted from the person's first touch, never from a page's own
 *      set_value; closed, it has none.
 *   4. The quick looks: a booking with a record is marked to open it; drive time and a block are not; the
 *      drawer hands the tap to the page's on_open, else opens it itself.
 *   5. The Project Planner: a card, a Lookahead label, a Gantt row, an overdue or equipment row, a
 *      maintenance visit (projected or not), a rental, travel and a hidden task, a person's name and a
 *      project's name each open their record; a drafted new task, no kit, or no permission does what a
 *      tap did before; the menu starts with Quick edit and Open task; a project's name has a menu of its
 *      own; Tap to move is off unless chosen; a press on a resize grip is never a tap.
 *   6. The Maintenance Planner: a visit, a projected visit, a Fountain Move, project work and a
 *      technician's name; the menu; Tap to move.
 *
 * Run: node scripts/test_planner_tap_opens.js
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

// ------------------------------------------------------------------ the kit modules as one script

function loadKit() {
	const strip = (file) =>
		fs
			.readFileSync(file, "utf8")
			.replace(/^import [\s\S]*?;\s*$/gm, "")
			.replace(/^export (const|function|class) /gm, "$1 ");
	const source = [
		path.join(APP, "public", "js", "sf_kit", "esc.js"),
		path.join(KIT, "escape.js"),
		path.join(KIT, "records.js"),
		path.join(KIT, "panel.js"),
		path.join(KIT, "peeks.js"),
	]
		.map(strip)
		.join("\n")
		.replace(/^export const escape = esc;$/m, "const escape = esc;");
	const ctx = { console, Promise, JSON };
	vm.createContext(ctx);
	vm.runInContext(
		`${source}\nthis.mod = { record_of, create_records, DOCTYPES, is_draft_name, create_panel, create_peeks, person_week_html, day_overview_html };`,
		ctx
	);
	return ctx.mod;
}

const kit = loadKit();

// A desk for records.open: the routes it asks for, the drawer it closes first, the confirm it shows.
function desk(opts) {
	const o = opts || {};
	const log = [];
	const readable = o.readable || null;
	const frappe = {
		set_route: (...args) => log.push(["route"].concat(args)),
		confirm: (message, yes) => {
			log.push(["confirm", message]);
			if (o.agree) yes();
		},
		model: { can_read: (doctype) => !readable || readable.includes(doctype) },
	};
	const env = {
		frappe,
		drawer: {
			navigate(fn) {
				log.push(["navigate"]);
				return fn();
			},
		},
		unsaved: () => !!o.unsaved,
	};
	return { env, log, records: kit.create_records(env) };
}

// ------------------------------------------------------------------ 1. the map

test("record_of: what each thing a planner draws opens", () => {
	const r = (kind, item) => kit.record_of(kind, item);
	check("a task card", r("task", { name: "TASK-1", subject: "Dig" }), { doctype: "Task", name: "TASK-1" });
	check("a booking's ref wins over its name", r("task", { ref: "TASK-2", name: "x" }), { doctype: "Task", name: "TASK-2" });
	check("a Fountain Move and a rental's work are Tasks", [r("move", { name: "TASK-3" }), r("rental", { ref: "TASK-4" })], [
		{ doctype: "Task", name: "TASK-3" },
		{ doctype: "Task", name: "TASK-4" },
	]);
	check("a drafted new task is no Task yet", [r("task", { name: "draft-new:PDC-1" }), r("task", { name: "T9", new_task: true })], [null, null]);
	check("a visit", r("visit", { ref: "SMR-1" }), { doctype: "Sapphire Maintenance Record", name: "SMR-1" });
	check("a Maintenance Planner visit card (its name)", r("visit", { name: "SMR-2" }), { doctype: "Sapphire Maintenance Record", name: "SMR-2" });
	check(
		"a projected visit is its contract, however it is carried",
		[
			r("visit", { ref: "CON-1", projected: true }),
			r("visit", { ref: "CON-x", projected: { contract: "CON-2", movable: true } }),
			r("projected", { contract: "CON-3", key: "CON-3|SN|2026-11-02" }),
		],
		[
			{ doctype: "Sapphire Maintenance Contract", name: "CON-1" },
			{ doctype: "Sapphire Maintenance Contract", name: "CON-2" },
			{ doctype: "Sapphire Maintenance Contract", name: "CON-3" },
		]
	);
	check("travel", r("travel", { ref: "TRIP-1" }), { doctype: "Travel Trip", name: "TRIP-1" });
	check("a project, by its project or its name", [r("project", { project: "PRJ-1" }), r("project", { name: "PRJ-2" })], [
		{ doctype: "Project", name: "PRJ-1" },
		{ doctype: "Project", name: "PRJ-2" },
	]);
	check(
		"a person: the Employee, else the Planner Resource, else nothing",
		[r("person", { employee: "HR-EMP-1", resource: "RES-1" }), r("person", { employee: null, resource: "RES-2" }), r("person", {})],
		[{ doctype: "Employee", name: "HR-EMP-1" }, { doctype: "Planner Resource", name: "RES-2" }, null]
	);
	check("an asset", r("asset", { asset: "ACC-ASS-1" }), { doctype: "Asset", name: "ACC-ASS-1" });
	check("driving, a block, a note and anything else are no record", [r("drive", { ref: "x" }), r("block", { ref: "PB-1" }), r("note", { ref: "N" }), r("wat", { name: "x" })], [null, null, null, null]);
	check("a blank name is none, and a name is trimmed", [r("task", { name: "  " }), r("task", { name: " T1 " })], [null, { doctype: "Task", name: "T1" }]);
	check("nothing at all is none", [r("task", null), r("task")], [null, null]);
	const reader = (doctype) => doctype !== "Employee" && doctype !== "Sapphire Maintenance Record";
	check(
		"a record this person cannot read is none (a person falls to their Planner Resource)",
		[kit.record_of("visit", { ref: "SMR-1" }, reader), kit.record_of("person", { employee: "E", resource: "RES-1" }, reader), kit.record_of("task", { name: "T" }, reader)],
		[null, { doctype: "Planner Resource", name: "RES-1" }, { doctype: "Task", name: "T" }]
	);
	check("draft names", [kit.is_draft_name("draft-new:1"), kit.is_draft_name("TASK-1"), kit.is_draft_name(null)], [true, false, false]);
});

// ------------------------------------------------------------------ 2. open

test("records.open: the same tab, through the drawer, asking first only over unsaved edits", () => {
	let d = desk();
	check("a record", d.records.open({ doctype: "Task", name: "T1" }), true);
	check("closes the drawer first, then routes to the Form", d.log, [["navigate"], ["route", "Form", "Task", "T1"]]);
	d = desk();
	check("nothing to open", [d.records.open(null), d.records.open({ doctype: "Task" }), d.log], [false, false, []]);
	d = desk({ readable: ["Task"] });
	check("open_item uses the person's permissions", [d.records.open_item("visit", { ref: "SMR-1" }), d.records.open_item("task", { name: "T2" })], [false, true]);
	check("and the record_of it carries does too", d.records.record_of("visit", { ref: "SMR-1" }), null);
	d = desk({ unsaved: true });
	check("unsaved edits: it asks and waits", [d.records.open({ doctype: "Project", name: "PRJ-1" }), d.log.map((e) => e[0])], [true, ["confirm"]]);
	check("in plain words", d.log[0][1], "The side panel has changes that are not saved. Leave without saving them?");
	d = desk({ unsaved: true, agree: true });
	d.records.open({ doctype: "Project", name: "PRJ-1" });
	check("and goes once the person agrees", d.log.map((e) => e[0]), ["confirm", "navigate", "route"]);
	const plain = kit.create_records({ frappe: { set_route: (...a) => (plain.routed = a) } });
	plain.open({ doctype: "Task", name: "T3" });
	check("no drawer, no permission model: still opens", plain.routed, ["Form", "Task", "T3"]);
});

// ------------------------------------------------------------------ 3. the side panel's unsaved edits

class Node {
	constructor(tag) {
		this.tagName = String(tag || "div").toUpperCase();
		this.children = [];
		this.parentNode = null;
		this.listeners = [];
		this.hidden = false;
		this.className = "";
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
	addEventListener(type, fn) {
		this.listeners.push({ type, fn });
	}
	fire(type, extra) {
		const e = Object.assign({ type, isTrusted: true }, extra || {});
		this.listeners.filter((l) => l.type === type).forEach((l) => l.fn(e));
	}
}

function panelEnv() {
	const doc = { body: new Node("body"), createElement: (tag) => new Node(tag) };
	const opened = [];
	const env = {
		doc,
		$: (el) => ({ el }),
		frappe: {
			ui: {
				FieldGroup: class {
					constructor(opts) {
						this.opts = opts;
						this.values = {};
					}
					make() {
						this.fields_dict = {};
						this.fields_list = [];
					}
					get_values() {
						return Object.assign({}, this.values);
					}
					get_value(k) {
						return this.values[k];
					}
					set_value(k, v) {
						this.values[k] = v;
					}
					set_values(v) {
						Object.assign(this.values, v);
					}
				},
			},
		},
		drawer: {
			open(opts) {
				let open = true;
				const handle = {
					is_open: () => open,
					close() {
						open = false;
						opts.on_close("user");
					},
					set_actions() {},
					set_title() {},
				};
				opened.push(handle);
				return handle;
			},
		},
	};
	return { env, opened };
}

test("The side panel: unsaved edits are the person's own, from their first touch", () => {
	const { env } = panelEnv();
	const panel = kit.create_panel(env);
	check("nothing open", panel.unsaved(), false);
	const p = panel({ title: "Dig", fields: [] });
	p.set_value("start", "2026-10-27"); // the page fills it in before it is shown
	p.show();
	p.set_value("crew", "RES-1"); // a late reply filling in a row
	check("shown and filled in by the page: nothing unsaved", panel.unsaved(), false);
	p.container.fire("pointerdown", { isTrusted: false });
	p.set_value("crew", "RES-2");
	check("a synthetic event is not a touch", panel.unsaved(), false);
	p.container.fire("pointerdown");
	check("touched, nothing changed", panel.unsaved(), false);
	p.set_value("start", "2026-10-28");
	check("touched and changed", panel.unsaved(), true);
	p.set_value("start", "2026-10-27");
	check("changed back", panel.unsaved(), false);
	p.set_value("start", "2026-10-29");
	p.hide();
	check("closed: none", panel.unsaved(), false);
	const q = panel({ title: "Pour", fields: [] });
	q.show();
	q.container.fire("keydown");
	q.set_value("hours", 4);
	check("a key counts as a touch, and the open panel is the one asked", panel.unsaved(), true);
	q.show();
	check("shown again: counted afresh", panel.unsaved(), false);
});

// ------------------------------------------------------------------ 4. the quick looks

function week(bookings) {
	return {
		resource: "RES-1",
		user: "kyle@example.com",
		label: "Kyle",
		today: "2026-10-27",
		days: [{ date: "2026-10-27", capacity: 8, booked: 6, bookings }],
	};
}

test("The quick looks: a booking with a record opens it", () => {
	const d = desk();
	const bookings = [
		{ kind: "task", ref: "TASK-1", key: "TASK-1", label: "Dig", hours: 4 },
		{ kind: "visit", ref: "CON-9", key: "CON-9|SN|2026-10-27", label: "Projected visit: Plaza", hours: 2, projected: { contract: "CON-9" } },
		{ kind: "travel", ref: "TRIP-1", label: "Away", hours: 8 },
		{ kind: "block", ref: "PB-1", label: "Unavailable", hours: 8 },
		{ kind: "task", ref: 'T"><img src=x>', label: "evil", hours: 1 },
	];
	const html = kit.person_week_html(week(bookings), { record_of: d.records.record_of });
	check("a task", html.includes('data-pk-open="Task" data-pk-open-name="TASK-1" role="button" tabindex="0"'), true);
	check("a projected visit opens its contract", html.includes('data-pk-open="Sapphire Maintenance Contract" data-pk-open-name="CON-9"'), true);
	check("a trip", html.includes('data-pk-open="Travel Trip" data-pk-open-name="TRIP-1"'), true);
	check("a block is not a record to open", (html.match(/data-pk-open=/g) || []).length, 4);
	check("the name is escaped", [html.includes('data-pk-open-name="T&quot;&gt;&lt;img src=x&gt;"'), html.includes("<img src=x>")], [true, false]);
	check("it says so on hover", html.includes("Click to open it"), true);
	const before = kit.person_week_html(week(bookings), {});
	check("without record_of (an older page): nothing changes", [before.includes("data-pk-open"), before.includes("pk-open")], [false, false]);
	const day = kit.day_overview_html({ people: [{ resource: "RES-1", label: "Kyle", capacity: 8, booked: 4, bookings: [bookings[0]] }] }, { record_of: d.records.record_of });
	check("everyone's day too", day.includes('data-pk-open="Task" data-pk-open-name="TASK-1"'), true);
});

function peekEnv(records) {
	const drawers = [];
	const doc = {
		createElement: (tag) => Object.assign(new Node(tag), { setAttribute() {} }),
	};
	const env = {
		doc,
		records,
		call: () => Promise.resolve(week([{ kind: "task", ref: "TASK-1", label: "Dig", hours: 4 }])),
		drawer: {
			open(opts) {
				const body = { classList: { add() {}, remove() {} } };
				const handle = { body, is_open: () => true, set_title() {}, set_subtitle() {}, set_body() {} };
				drawers.push(opts);
				return handle;
			},
		},
	};
	return { env, drawers };
}

const target = (attrs) => ({
	closest: (selector) => {
		if (selector === "[data-pk-open][data-pk-open-name]" && attrs["data-pk-open"]) return { getAttribute: (k) => attrs[k] };
		if (selector === "[data-pk-person-open]" && attrs["data-pk-person-open"]) return { getAttribute: (k) => attrs[k] };
		return null;
	},
});

test("The quick looks: the drawer hands a tap to the page, else opens it itself", () => {
	const d = desk();
	const { env, drawers } = peekEnv(d.records);
	const peeks = kit.create_peeks(env);
	const handed = [];
	peeks.person({ resource: "RES-1", start: "2026-10-25", on_open: (record) => handed.push(record) });
	drawers[0].on_click({ target: target({ "data-pk-open": "Task", "data-pk-open-name": "TASK-1" }) });
	check("the person's week: to the page's on_open", [handed, d.log], [[{ doctype: "Task", name: "TASK-1" }], []]);
	peeks.person({ resource: "RES-1", start: "2026-10-25" });
	drawers[1].on_click({ target: target({ "data-pk-open": "Travel Trip", "data-pk-open-name": "TRIP-1" }) });
	check("no on_open: the kit opens it", d.log, [["navigate"], ["route", "Form", "Travel Trip", "TRIP-1"]]);
	const people = [];
	peeks.day({ date: "2026-10-27", on_open: (record) => handed.push(record), on_person: (p) => people.push(p.resource) });
	drawers[2].on_click({ target: target({ "data-pk-open": "Sapphire Maintenance Record", "data-pk-open-name": "SMR-1" }) });
	drawers[2].on_click({ target: target({ "data-pk-person-open": "RES-2" }) });
	check("everyone's day: a booking opens, a name still opens their week", [handed[1], people], [{ doctype: "Sapphire Maintenance Record", name: "SMR-1" }, ["RES-2"]]);
});

// ------------------------------------------------------------------ a small DOM for the pages

function compound(text) {
	const out = { tag: null, classes: [], attrs: [] };
	const re = /^([a-z][a-z0-9-]*)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/gi;
	let m;
	let used = 0;
	while ((m = re.exec(text))) {
		if (m.index !== used) throw new Error(`selector not modelled: ${text}`);
		used = re.lastIndex;
		if (m[1]) out.tag = m[1].toUpperCase();
		else if (m[2]) out.classes.push(m[2]);
		else out.attrs.push([m[3], m[4]]);
	}
	if (used !== text.length) throw new Error(`selector not modelled: ${text}`);
	return out;
}

function matchOne(node, c) {
	if (c.tag && node.tagName !== c.tag) return false;
	if (!c.classes.every((k) => node.classes.has(k))) return false;
	return c.attrs.every(([k, v]) => (v === undefined ? k in node.attrs : node.attrs[k] === v));
}

function matches(node, selector) {
	return String(selector)
		.split(",")
		.map((s) => s.trim())
		.some((one) => {
			const parts = one.split(/\s+/).map(compound);
			if (!matchOne(node, parts[parts.length - 1])) return false;
			let i = parts.length - 2;
			let up = node.parent;
			while (i >= 0 && up) {
				if (matchOne(up, parts[i])) i -= 1;
				up = up.parent;
			}
			return i < 0;
		});
}

class El {
	constructor(spec, attrs, parent) {
		const c = compound(spec);
		this.tagName = c.tag || "DIV";
		this.classes = new Set(c.classes);
		this.attrs = Object.assign({}, attrs || {});
		this.parent = parent || null;
	}
	getAttribute(k) {
		return k in this.attrs ? this.attrs[k] : null;
	}
	setAttribute(k, v) {
		this.attrs[k] = String(v);
	}
	closest(selector) {
		for (let n = this; n; n = n.parent) if (matches(n, selector)) return n;
		return null;
	}
	contains(other) {
		for (let n = other; n; n = n.parent) if (n === this) return true;
		return false;
	}
}

function event(target) {
	const e = { target, stopped: false, immediate: false, prevented: false };
	e.stopPropagation = () => (e.stopped = true);
	e.stopImmediatePropagation = () => (e.immediate = true);
	e.preventDefault = () => (e.prevented = true);
	return e;
}

// ------------------------------------------------------------------ 5. the Project Planner

function loadPage(file, cls) {
	const frappe = {
		pages: new Proxy({}, { get: (t, k) => (t[k] = t[k] || {}) }),
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		datetime: { get_today: () => "2026-10-27" },
		session: { user: "nik@example.com" },
		route_flags: {},
		utils: {
			escape_html: (v) =>
				String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"),
		},
	};
	const sandbox = {
		frappe,
		window: { localStorage: { getItem: () => null, setItem() {} }, matchMedia: () => ({ matches: true }) },
		document: { elementFromPoint: () => null },
		$: () => ({}),
		__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] != null ? String(args[i]) : "")),
		moment: () => ({ isValid: () => false }),
		console,
		CSS: { escape: (s) => String(s) },
	};
	const context = vm.createContext(sandbox);
	vm.runInContext(`${fs.readFileSync(file, "utf-8")}\nthis.Page = ${cls};`, context, { filename: file });
	return context;
}

const ppCtx = loadPage(PP_JS, "ProjectPlanner");

// A planner on the week view, with the kit's real records over a desk that records where it went.
function planner(opts) {
	const o = opts || {};
	const d = desk(o);
	// The page's own router, for what a tap did before the kit (the desk above records the kit's).
	ppCtx.frappe.set_route = (...args) => d.log.push(["page-route"].concat(args));
	const pp = Object.create(ppCtx.Page.prototype);
	const seen = [];
	const root = new El("div.pp-wrap");
	Object.assign(pp, {
		view: "week",
		data: { can_edit: true },
		by_task: {
			T1: { name: "T1", subject: "Dig", project: "PRJ-1", movable: true, start: "2026-10-27", end: "2026-10-27" },
			"draft-new:PDC-1": { name: "draft-new:PDC-1", subject: "New", project: "PRJ-1", new_task: true, movable: true },
		},
		by_resource: { "RES-1": { name: "RES-1", employee: "HR-EMP-1" }, "RES-2": { name: "RES-2", employee: null } },
		by_project: { "PRJ-1": { title: "Riverwalk" } },
		by_foreign: {
			"visit|SMR-1|RES-1|2026-10-27": { kind: "visit", ref: "SMR-1", date: "2026-10-27", projected: false },
			"visit|CON-1|RES-1|2026-10-28": { kind: "visit", ref: "CON-1", date: "2026-10-28", projected: true },
			"rental|TASK-R|RES-1|2026-10-27": { kind: "rental", ref: "TASK-R", date: "2026-10-27" },
			"travel|TRIP-1|RES-1|2026-10-27": { kind: "travel", ref: "TRIP-1", date: "2026-10-27" },
		},
		click_blocked_until: 0,
		$body: [root],
		p6a: o.no_kit ? { kit: null } : { kit: { records: d.records, menu: (m) => seen.push(["menu", m]) } },
		p6c: { highlight: null },
		open_card: (card) => seen.push(["panel", card.name]),
		p6a_open_person: (resource) => seen.push(["week", resource]),
		p6a_open_project: (project) => seen.push(["glance", project]),
		p6a_open_day: (ymd) => seen.push(["day", ymd]),
		ppg_open: (project) => seen.push(["gantt", project]),
		p6a_filter_project: (project) => seen.push(["only", project]),
	});
	return { pp, root, d, seen, routes: () => d.log.filter((e) => e[0] === "route").map((e) => e.slice(2)) };
}

test("Project Planner: a card, a label, a row, a booking each open their record", () => {
	const { pp, root, routes, seen } = planner();
	const card = new El("div.pp-card", { "data-task": "T1", "data-date": "2026-10-27" }, root);
	pp.activate(new El("span.pp-card-title", {}, card));
	pp.activate(new El("span", {}, new El("div.pp-od-card", { "data-od-task": "T-LATE" }, root)));
	pp.activate(new El("div.pp-ecard", { "data-etask": "T1" }, root));
	pp.activate(new El("span.ppl-phase-name", { "data-ppr-task": "PH-300" }, new El("div.ppl-label", {}, root)));
	pp.activate(new El("span.ppl-task-name", { "data-ppr-task": "T1" }, new El("div.ppl-label", {}, root)));
	check("a card, an overdue row, an equipment row, a phase and a task's name", routes(), [["Task", "T1"], ["Task", "T-LATE"], ["Task", "T1"], ["Task", "PH-300"], ["Task", "T1"]]);
	check("and never the side panel", seen, []);
	pp.activate(new El("div.pp-card", { "data-task": "draft-new:PDC-1" }, root));
	check("a drafted new task is no Task yet: its side panel", seen, [["panel", "draft-new:PDC-1"]]);
});

test("Project Planner: maintenance, rentals, travel and a hidden task open their own records", () => {
	const { pp, root, routes } = planner();
	const fcard = (key) => new El("div.pp-fcard", { "data-fkey": key }, root);
	pp.activate(fcard("visit|SMR-1|RES-1|2026-10-27"));
	pp.activate(fcard("visit|CON-1|RES-1|2026-10-28"));
	pp.activate(fcard("rental|TASK-R|RES-1|2026-10-27"));
	pp.activate(fcard("travel|TRIP-1|RES-1|2026-10-27"));
	// The crew view's own markup carries the booking instead of a key.
	const crew = (kind, ref, projected) => new El("div.pp-fcard", { "data-kind": kind, "data-ref": ref, "data-date": "2026-10-27", "data-projected": projected ? "1" : "" }, root);
	pp.activate(crew("task", "T-HIDDEN"));
	pp.activate(crew("visit", "CON-2", true));
	check("each its own record", routes(), [
		["Sapphire Maintenance Record", "SMR-1"],
		["Sapphire Maintenance Contract", "CON-1"],
		["Task", "TASK-R"],
		["Travel Trip", "TRIP-1"],
		["Task", "T-HIDDEN"],
		["Sapphire Maintenance Contract", "CON-2"],
	]);
	// Somebody who cannot read a maintenance record is told where it moves, as before.
	const reader = planner({ readable: ["Task", "Project", "Travel Trip"] });
	const alerts = [];
	ppCtx.frappe.show_alert = (m) => alerts.push(m.message);
	ppCtx.frappe.user = { has_role: () => false };
	reader.pp.activate(new El("div.pp-fcard", { "data-fkey": "visit|SMR-1|RES-1|2026-10-27" }, reader.root));
	check("no permission: the old note, no route", [reader.routes(), alerts], [[], ["A maintenance visit: it moves on the Maintenance Planner."]]);
});

test("Project Planner: a person's name opens their Employee record, a project's name the Project", () => {
	const { pp, root, routes, seen } = planner();
	const card = new El("div.pp-card", { "data-task": "T1" }, root);
	const badge = new El("span.pp-badge", { "data-pk-person": "RES-1" }, card);
	let e = event(badge);
	pp.p6a_click(e);
	check("initials on a card: the Employee, and nothing else answers the tap", [routes(), e.immediate, e.prevented], [[["Employee", "HR-EMP-1"]], true, true]);
	pp.p6a_click(event(new El("div.pp-person", { "data-resource": "RES-2", "data-pk-person": "RES-2" }, root)));
	check("someone with no Employee: their Planner Resource", routes()[1], ["Planner Resource", "RES-2"]);
	e = event(new El("span.pp-p6a-proj", { "data-pk-project": encodeURIComponent("PRJ-1") }, card));
	pp.p6a_click(e);
	const note = new El("div.pk-note-line", { "data-pk-note-open": "2026-10-27" }, root);
	pp.p6a_click(event(new El("span.pk-note-project", { "data-pk-project": "PRJ-1" }, note)));
	pp.p6a_click(event(new El("span.ppl-project-name", { "data-pk-project": "PRJ-1" }, root)));
	check("a card's, a day note's and the Lookahead's project name", routes().slice(2), [["Project", "PRJ-1"], ["Project", "PRJ-1"], ["Project", "PRJ-1"]]);
	pp.p6a_click(event(new El("div.pp-day-num", { "data-pp6a-day": "2026-10-27" }, root)));
	check("a date has no record: everyone's day, as before", seen, [["day", "2026-10-27"]]);
	const reader = planner({ readable: ["Task", "Project"] });
	reader.pp.p6a_click(event(new El("span", { "data-pk-person": "RES-1" }, reader.root)));
	check("no permission for Employee or Planner Resource: their week, as before", [reader.routes(), reader.seen], [[], [["week", "RES-1"]]]);
});

test("Project Planner: before the kit has loaded a tap does what it did", () => {
	const { pp, root, d, seen } = planner({ no_kit: true });
	pp.activate(new El("div.pp-card", { "data-task": "T1" }, root));
	pp.activate(new El("div.pp-od-card", { "data-od-task": "T-OFF" }, root));
	check("the card's side panel; an off-board row still opens its Task", [seen, d.log], [[["panel", "T1"]], [["page-route", "Form", "Task", "T-OFF"]]]);
});

test("Project Planner: the Gantt tab and the project drawer", () => {
	const { pp, routes, seen } = planner();
	pp.ppg_click({ ref_doctype: "Task", ref_name: "T1" });
	pp.ppg_click({ ref_doctype: "Task", ref_name: "PH-GROUP", type: "project" });
	pp.ppg_click({ id: "ppg:not-scheduled" });
	pp.ppg_click(null);
	pp.ppg_click({ ref_doctype: "Task", ref_name: "draft-new:PDC-1" });
	check("a bar and a phase open their Task; Not scheduled is none; a drafted task its panel", [routes(), seen], [[["Task", "T1"], ["Task", "PH-GROUP"]], [["panel", "draft-new:PDC-1"]]]);
});

test("Project Planner: the menu starts with Quick edit, and a project's name has its own", () => {
	const { pp, root, seen, routes } = planner();
	const labels = (items) => items.filter((item) => !item.divider).map((item) => item.label);
	const items = pp.p6b_card_items({ kind: "card", card: pp.by_task.T1, ymd: "2026-10-27" });
	check("Quick edit, then Open task", labels(items).slice(0, 2), ["Quick edit", "Open task"]);
	items[0].on_click();
	items[1].on_click();
	check("Quick edit is the side panel; Open task the Task", [seen, routes()], [[["panel", "T1"]], [["Task", "T1"]]]);
	const fresh = pp.p6b_card_items({ kind: "card", card: pp.by_task["draft-new:PDC-1"], ymd: null });
	check("a drafted new task has no Task to open", labels(fresh).includes("Open task"), false);
	const name = new El("span.ppl-project-name", { "data-pk-project": "PRJ-1" }, new El("div.ppl-label", {}, root));
	const target = pp.p6b_target(name);
	check("a project's name off a card is a project target", [target.kind, target.project], ["project", "PRJ-1"]);
	check("titled by the project", pp.p6b_menu_title(target), "Riverwalk");
	check("its items", labels(pp.p6b_menu_items(target)), ["Open project", "Project at a glance", "Show only this project", "Open in Gantt"]);
	check("and 6C's highlight", labels(pp.p6c_menu_items(target)), ["Highlight this project"]);
	const label = pp.p6b_target(new El("span.ppl-task-name", { "data-ppr-task": "T1" }, root));
	check("a task's name has its card's menu", [label.kind, label.card.name], ["card", "T1"]);
	check("a phase's name (not a card) has none", pp.p6b_target(new El("span.ppl-phase-name", { "data-ppr-task": "PH-300" }, root)), null);
	const inside = pp.p6b_target(new El("span.pp-p6a-proj", { "data-pk-project": "PRJ-1" }, new El("div.pp-card", { "data-task": "T1" }, root)));
	check("a project's name on a card keeps the card's menu", inside.kind, "card");
});

test("Project Planner: Tap to move is off unless chosen; a grip is never a tap", () => {
	const { pp } = planner();
	const prefs = {};
	pp.load_pref = (key, fallback) => (key in prefs ? prefs[key] : fallback);
	check("a touch screen with nothing chosen: off", pp.p6c_tap_default(), false);
	prefs.ee_project_planner_tap_move = "1";
	check("turned on: on", pp.p6c_tap_default(), true);
	prefs.ee_project_planner_tap_move = "0";
	check("turned off: off", pp.p6c_tap_default(), false);
	const removed = [];
	pp.p6b = { resize: { card: pp.by_task.T1, start: "2026-10-27", end: "2026-10-27", new_start: "2026-10-27", new_end: "2026-10-27", label: null } };
	pp.$grid_wrap = [{ querySelectorAll: () => [] }];
	ppCtx.document.body = { classList: { remove: (c) => removed.push(c) } };
	const before = Date.now();
	pp.p6b_resize_end(true);
	check("a press on a grip that changed nothing still blocks the click after it", pp.click_blocked_until >= before + 400, true);
});

// ------------------------------------------------------------------ 6. the Maintenance Planner

const mpCtx = loadPage(MP_JS, "MaintenancePlanner");

function maintenance(opts) {
	const o = opts || {};
	const d = desk(o);
	const mp = Object.create(mpCtx.Page.prototype);
	const seen = [];
	Object.assign(mp, {
		data: { can_move_visits: true },
		click_blocked_until: 0,
		tech_by_user: { "austin@example.com": { user: "austin@example.com", employee: "HR-EMP-7", resource: "RES-7" }, "lisa@example.com": { user: "lisa@example.com" } },
		p6a: o.no_kit ? { kit: null } : { kit: { records: d.records } },
		open_card: (card) => seen.push(["panel", card.kind, card.name || card.key]),
		p6a_open_person: (user) => seen.push(["week", user]),
		p6a_open_site: () => seen.push(["site"]),
		p6b_next_free_item: () => ({ label: "Move to next free day" }),
		tech_name: (user) => user,
	});
	return { mp, d, seen, routes: () => d.log.filter((e) => e[0] === "route").map((e) => e.slice(2)) };
}

test("Maintenance Planner: a visit, a projected visit, a move and project work open their records", () => {
	const { mp, routes, seen } = maintenance();
	mp.mpr_open_card({ kind: "visit", name: "SMR-1" });
	mp.mpr_open_card({ kind: "projected", key: "CON-1|SN|2026-11-02", contract: "CON-1" });
	mp.mpr_open_card({ kind: "move", name: "TASK-FM" });
	mp.mpr_open_card({ kind: "booking", booking_kind: "rental", ref: "TASK-R" });
	mp.mpr_open_card({ kind: "booking", booking_kind: "travel", ref: "TRIP-1" });
	check("each its own record", routes(), [
		["Sapphire Maintenance Record", "SMR-1"],
		["Sapphire Maintenance Contract", "CON-1"],
		["Task", "TASK-FM"],
		["Task", "TASK-R"],
		["Travel Trip", "TRIP-1"],
	]);
	check("and never the side panel", seen, []);
	const old = maintenance({ no_kit: true });
	old.mp.mpr_open_card({ kind: "visit", name: "SMR-1" });
	check("before the kit: the side panel, as before", old.seen, [["panel", "visit", "SMR-1"]]);
});

test("Maintenance Planner: a technician's name opens their Employee record", () => {
	const { mp, routes, seen } = maintenance();
	mp.mpr_open_person("austin@example.com");
	mp.mpr_open_person("lisa@example.com");
	check("the Employee; someone not on the planner's resources: their week", [routes(), seen], [[["Employee", "HR-EMP-7"]], [["week", "lisa@example.com"]]]);
	const e = event(new El("span.mp-tech", { "data-pk-person": encodeURIComponent("austin@example.com") }, new El("div.mp-card", { "data-key": "SMR-1" })));
	mp.p6a_click(e);
	check("initials on a visit go there", routes()[1], ["Employee", "HR-EMP-7"]);
});

test("Maintenance Planner: Quick edit first, the record next; Tap to move is off unless chosen", () => {
	const { mp, routes, seen } = maintenance();
	const labels = (items) => items.filter((item) => !item.divider).map((item) => item.label);
	const visit = { kind: "visit", name: "SMR-1", movable: true, status: "draft", project: "PRJ-1", technician: "austin@example.com", date: "2026-10-27" };
	const items = mp.p6b_card_items({ kind: "card", card: visit });
	check("a visit", labels(items).slice(0, 2), ["Quick edit", "Open visit"]);
	items[1].on_click();
	items[0].on_click();
	check("Open visit is the record; Quick edit the side panel", [routes(), seen], [[["Sapphire Maintenance Record", "SMR-1"]], [["panel", "visit", "SMR-1"]]]);
	const projected = mp.p6b_card_items({ kind: "card", card: { kind: "projected", key: "CON-1|SN|2026-11-02", contract: "CON-1", project: "PRJ-1" } });
	check("a projected visit: Open contract", labels(projected).slice(0, 2), ["Quick edit", "Open contract"]);
	const prefs = {};
	mp.load_pref = (key, fallback) => (key in prefs ? prefs[key] : fallback);
	check("a touch screen with nothing chosen: off", mp.p6c_tap_default(), false);
	prefs.ee_maintenance_planner_tap_move = "1";
	check("turned on: on", mp.p6c_tap_default(), true);
});

// ------------------------------------------------------------------ run

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (e) {
			failures += 1;
			console.log(`  FAIL ${name}: threw ${e && e.stack ? e.stack : e}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	console.log(failures ? `\n${failures} of ${checks} checks failed` : `\nall ${checks} checks passed`);
	process.exit(failures ? 1 : 0);
})();
