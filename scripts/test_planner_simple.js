#!/usr/bin/env node
/**
 * Simple mode for both planners (docs/planner-simple-mode.md; Nik, 2026-10-22), run under node against the REAL
 * kit module (erpnext_enhancements/public/js/planner_kit/simple.js) and the REAL Maintenance Planner script.
 *
 *   1. The pure pieces: initials, a day and a span as people read them, one item (its mark, its words, who, the
 *      record it opens), one card (Done grey and Coming up bold, three of each, Show all / Show fewer, the
 *      not-scheduled count, Coming up first on a phone) and the "?" help for jobs and for sites. Everything a
 *      record says is escaped.
 *   2. The mounted screen on a small fake DOM: the first read, "N of M jobs", Show more appending, an answer
 *      for an older request dropped, the search read again after a pause (and Show all closed), a failed read
 *      saying so with Try again, the switch, "?", an item and a card's name each handed to the page.
 *   3. The Maintenance Planner's half: the device flag is written only when it changes, a bare address on a
 *      device that used Simple mode last opens it in place (never for a link that names a technician), the
 *      switch off clears the flag and opens the bare address, and the shortcuts stand down on Simple.
 *
 * The Project Planner's landing, Back and Forward run in scripts/test_planner_today.js (a model of v16.50's
 * router), beside the other tabs'.
 *
 * Run: node scripts/test_planner_simple.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const KIT = path.join(APP, "public", "js", "planner_kit");
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

const tick = () => new Promise((resolve) => setImmediate(resolve));
const settle = async () => {
	for (let i = 0; i < 6; i++) await tick();
};

// ------------------------------------------------------------------ the kit module as a script

function loadSimple(timers) {
	const strip = (file) =>
		fs
			.readFileSync(file, "utf8")
			.replace(/^import .*?;\s*$/gm, "")
			.replace(/^export (const|function|class) /gm, "$1 ");
	const source = [path.join(APP, "public", "js", "sf_kit", "esc.js"), path.join(KIT, "escape.js"), path.join(KIT, "simple.js")]
		.map(strip)
		.join("\n")
		.replace(/^export const escape = esc;$/m, "const escape = esc;");
	const ctx = {
		console,
		Promise,
		setTimeout: (fn) => {
			timers.push(fn);
			return timers.length;
		},
		clearTimeout: (id) => {
			if (id) timers[id - 1] = null;
		},
	};
	vm.createContext(ctx);
	vm.runInContext(
		`${source}\nthis.mod = { create_simple, card_html, item_html, initials, day_text, span_text, legend_sections, SHOWN, SEARCH_MS };`,
		ctx
	);
	return ctx.mod;
}

const flush = (timers) => {
	const due = timers.splice(0);
	due.forEach((fn) => fn && fn());
};

// ------------------------------------------------------------------ a small DOM

class FakeEl {
	constructor(cls) {
		this.cls = cls;
		this._html = "";
		this.textContent = "";
		this.value = "";
		this.attrs = {};
		this.handlers = {};
		this.classes = new Set();
		this.classList = { add: (c) => this.classes.add(c) };
		this.kids = {};
	}
	set innerHTML(value) {
		this._html = String(value);
		this.kids = {};
	}
	get innerHTML() {
		return this._html;
	}
	setAttribute(k, v) {
		this.attrs[k] = String(v);
	}
	getAttribute(k) {
		return k in this.attrs ? this.attrs[k] : null;
	}
	addEventListener(type, fn) {
		(this.handlers[type] = this.handlers[type] || []).push(fn);
	}
	fire(type, ev) {
		(this.handlers[type] || []).forEach((fn) => fn(ev || {}));
	}
	// The screen's fixed parts, by class, created once per innerHTML write that holds them.
	querySelector(selector) {
		if (!selector.startsWith(".")) return null;
		const cls = selector.slice(1);
		if (!this._html.includes(`class="${cls}`) && !this._html.includes(` ${cls}"`) && !this._html.includes(` ${cls} `)) return null;
		if (!this.kids[cls]) this.kids[cls] = new FakeEl(cls);
		return this.kids[cls];
	}
}

// A click target inside the screen: `closest` answers for the selectors given, with their attributes.
function target(matches) {
	return {
		closest: (selector) => {
			const attrs = matches[selector];
			if (!attrs) return null;
			return { getAttribute: (k) => (k in attrs ? attrs[k] : null) };
		},
	};
}

function mounted(mod, load, extra) {
	const root = new FakeEl("root");
	const seen = { off: 0, help: 0, opened: [] };
	const view = mod.create_simple({ warn: () => {} }).mount(
		root,
		Object.assign(
			{
				title: "Project Planner",
				placeholder: "Search jobs…",
				noun: ["job", "jobs"],
				load,
				open: (doctype, name) => seen.opened.push([doctype, name]),
				off: () => (seen.off += 1),
				help: () => (seen.help += 1),
				is_phone: () => false,
			},
			extra || {}
		)
	);
	return { root, view, seen, cards: () => root.querySelector(".pk-simple-cards"), line: () => root.querySelector(".pk-simple-status-line"), tail: () => root.querySelector(".pk-simple-tail") };
}

const item = (over) =>
	Object.assign({ kind: "task", doctype: "Task", name: "TASK-1", label: "Pour slab", phase: null, date: "2026-10-24", end: null, state: "open", who: [] }, over || {});

const card = (over) =>
	Object.assign(
		{
			name: "PRJ-1",
			title: "Riverwalk Plaza",
			customer: "City of Riverwalk",
			pm: "Clegg Mabey",
			status: "Active",
			finished: false,
			first: "2026-10-01",
			last: "2027-04-28",
			done: [],
			coming: [],
			unscheduled: [],
		},
		over || {}
	);

const answer = (cards, over) => Object.assign({ today: "2026-10-22", total: cards.length, more: false, cards }, over || {});

// ------------------------------------------------------------------ 1. the pure pieces

test("words: initials, a day, a span", () => {
	const mod = loadSimple([]);
	check("initials", ["Korben Jessop", "Nik", "jesse.griffin@x.com", "Mary Ann de la Cruz", "", null].map(mod.initials), ["KJ", "N", "JG", "MC", "", ""]);
	check("a day this year, and another year's", [mod.day_text("2026-10-20", "2026-10-22"), mod.day_text("2025-03-02", "2026-10-22"), mod.day_text("", "2026-10-22")], ["Oct 20", "Mar 2, 2025", ""]);
	check("a span, always with the year", [mod.span_text("2026-10-01", "2027-04-28"), mod.span_text("2026-10-01", "2026-10-01"), mod.span_text(null, null)], ["Oct 1, 2026 – Apr 28, 2027", "Oct 1, 2026", ""]);
});

test("an item: its mark, its words, who, and the record it opens; everything escaped", () => {
	const mod = loadSimple([]);
	const done = mod.item_html(item({ state: "done", date: "2026-10-20", label: "Excavate <b>basin</b>", who: ["Korben Jessop", "Jesse \"JG\" Griffin"] }), "2026-10-22");
	check("done is ✓, grey by its class", [done.includes("pk-simple-done"), done.includes(">✓<")], [true, true]);
	check("the label is escaped", [done.includes("&lt;b&gt;basin&lt;/b&gt;"), done.includes("<b>")], [true, false]);
	check("who as initials, the names on hover, escaped", [done.includes(">KJ JG<"), done.includes('title="Korben Jessop, Jesse &quot;JG&quot; Griffin"')], [true, true]);
	check("it opens its Task", done.includes('data-pk-simple-open="Task" data-pk-simple-name="TASK-1"'), true);
	const late = mod.item_html(item({ state: "late", date: "2026-10-01", end: "2026-10-20" }), "2026-10-22");
	check("late says so, and its days", [late.includes("pk-simple-late"), late.includes(">late<"), late.includes("Oct 1 – Oct 20")], [true, true, true]);
	check("closed: never done", mod.item_html(item({ state: "closed" }), "2026-10-22").includes("not done"), true);
	check("a group: how much of it is done", mod.item_html(item({ kind: "group", of: [2, 5] }), "2026-10-22").includes("2 of 5 done"), true);
	check("a projected visit is planned", mod.item_html(item({ kind: "projected", doctype: "Sapphire Maintenance Contract", name: "MNT-CON-1" }), "2026-10-22").includes("planned"), true);
	check("a visit in review says so", mod.item_html(item({ kind: "visit", review: true, state: "done" }), "2026-10-22").includes("in review"), true);
	check("an item with no record is not a button", mod.item_html(item({ doctype: "", name: "" }), "2026-10-22").includes("<button"), false);
	check("an unknown state reads as open", mod.item_html(item({ state: "<x>" }), "2026-10-22").includes("pk-simple-open"), true);
	check("the phase, escaped", mod.item_html(item({ phase: "Phase 300 <Rough-in>" }), "2026-10-22").includes("Phase 300 &lt;Rough-in&gt;"), true);
});

test("a card: Done and Coming up, three of each, Show all and Show fewer, Coming up first on a phone", () => {
	const mod = loadSimple([]);
	const done = [1, 2, 3, 4, 5].map((n) => item({ name: `D${n}`, label: `Done ${n}`, state: "done", date: `2026-10-0${n}` }));
	const coming = [1, 2].map((n) => item({ name: `C${n}`, label: `Next ${n}`, date: `2026-10-2${n + 2}` }));
	const waiting = [item({ name: "U1", label: "Pull wire", state: "unscheduled", date: null })];
	const c = card({ title: "Riverwalk <Plaza>", done, coming, unscheduled: waiting });
	const html = mod.card_html(c, "2026-10-22", new Set(), false);
	check("three done shown, two left for Show all", [["Done 1", "Done 2", "Done 3", "Done 4"].map((t) => html.includes(t)), html.includes("Show all 5")], [[true, true, true, false], true]);
	check("coming has room for its two; the waiting one is counted, not listed", [html.includes("Next 2"), html.includes("Pull wire"), html.includes("1 not scheduled"), html.includes("Show all 3")], [true, false, true, true]);
	check("the title escaped, the facts", [html.includes("Riverwalk &lt;Plaza&gt;"), html.includes("PM Clegg Mabey · City of Riverwalk · Oct 1, 2026 – Apr 28, 2027")], [true, true]);
	check("the name opens the Project", html.includes('data-pk-simple-open="Project" data-pk-simple-name="PRJ-1"'), true);
	check("Done comes first on a desktop", html.indexOf("pk-simple-done\"") < html.indexOf("pk-simple-coming\""), true);
	const phone = mod.card_html(c, "2026-10-22", new Set(), true);
	check("Coming up comes first on a phone", phone.indexOf('class="pk-simple-half pk-simple-coming"') < phone.indexOf('class="pk-simple-half pk-simple-done"'), true);
	const open = mod.card_html(c, "2026-10-22", new Set(["PRJ-1|done", "PRJ-1|coming"]), false);
	check("opened: every item, the waiting one too, and Show fewer on each half", [open.includes("Done 5"), open.includes("Pull wire"), open.split("Show fewer").length - 1], [true, true, 2]);
	const empty = mod.card_html(card({ finished: true, status: "Completed" }), "2026-10-22", new Set(), false);
	check("a finished card: its class, Nothing done yet, Finished", [empty.includes("pk-simple-card pk-simple-finished"), empty.includes("Nothing done yet"), empty.includes("Finished")], [true, true, true]);
	check("an open card with nothing ahead", mod.card_html(card(), "2026-10-22", new Set(), false).includes("Nothing booked"), true);
	// One item and one not scheduled: Show all 2 opens it, and Show fewer puts it away again.
	const small = card({ coming: [item({ name: "C1" })], unscheduled: [item({ name: "U1", label: "Pull wire", state: "unscheduled", date: null })] });
	check("a small half: Show all 2", mod.card_html(small, "2026-10-22", new Set(), false).includes("Show all 2"), true);
	const opened = mod.card_html(small, "2026-10-22", new Set(["PRJ-1|coming"]), false);
	check("opened, it lists the waiting one and offers Show fewer", [opened.includes("Pull wire"), opened.includes("Show fewer")], [true, true]);
	const draft = mod.card_html(card({ draft: true }), "2026-10-22", new Set(), false);
	check("a Draft schedule is marked, with why on hover", [draft.includes('class="pk-simple-draft"'), draft.includes("◐ Draft schedule"), draft.includes("crew has not been told")], [true, true, true]);
	check("and nothing is said when it is published", mod.card_html(card(), "2026-10-22", new Set(), false).includes("pk-simple-draft"), false);
	check("a half with nothing hidden offers neither", [/Show (all|fewer)/.test(mod.card_html(card({ coming: [item()] }), "2026-10-22", new Set(["PRJ-1|coming"]), false))], [false]);
});

test("the help: for jobs and for sites, words only", () => {
	const mod = loadSimple([]);
	const jobs = mod.legend_sections("jobs");
	const sites = mod.legend_sections("sites");
	check("two sections each", [jobs.map((s) => s.title), sites.map((s) => s.title)], [["Simple mode", "What the marks mean"], ["Simple mode", "What the marks mean"]]);
	const text = (sections) => sections.map((s) => s.items.map((i) => i.text).join(" ")).join(" ");
	check("jobs are told about old jobs' groups; sites about planned visits", [text(jobs).includes("phase templates"), text(sites).includes("Planned is a visit")], [true, true]);
	check("the samples are the marks' own markup", jobs[1].items[0].sample_html, '<span class="pk-simple-done"><span class="pk-simple-mark">✓</span></span>');
});

// ------------------------------------------------------------------ 2. the mounted screen

test("mounted: the bar, the first read, the count, Show more appending", async () => {
	const mod = loadSimple([]);
	const reads = [];
	const pages = [answer([card({ name: "A" }), card({ name: "B" })], { total: 3, more: true }), answer([card({ name: "C" })], { total: 3, more: false })];
	const s = mounted(mod, (search, start, limit) => {
		reads.push([search, start, limit]);
		return Promise.resolve(pages.shift());
	});
	check("the bar", [s.root.classes.has("pk-simple"), s.root.querySelector(".pk-simple-heading").textContent, s.root.querySelector(".pk-simple-search").getAttribute("placeholder")], [true, "Project Planner", "Search jobs…"]);
	const sw = s.root.querySelector(".pk-simple-switch");
	check("the switch is on", [sw.textContent, s.root.innerHTML.includes('class="btn btn-default btn-sm pk-simple-switch" role="switch" aria-checked="true"')], ["Simple", true]);
	check("nothing read before the page asks", reads, []);
	s.view.refresh();
	check("loading says so", s.line().textContent, "Loading…");
	await settle();
	check("one read: no search, from the start, the default length", reads, [["", 0, null]]);
	check("two cards and the count", [s.cards().innerHTML.includes('data-pk-simple-card="A"'), s.cards().innerHTML.includes('data-pk-simple-card="B"'), s.line().textContent], [true, true, "2 of 3 jobs"]);
	check("Show more", s.tail().innerHTML.includes("Show more jobs"), true);
	s.root.fire("click", { target: target({ ".pk-simple-more": {} }) });
	await settle();
	check("the next page, after the two shown", reads[1], ["", 2, null]);
	check("appended, and no more to show", [s.view.state().cards.map((c) => c.name), s.tail().innerHTML, s.line().textContent], [["A", "B", "C"], "", "3 of 3 jobs"]);
	s.view.refresh();
	await settle();
	check("a refresh reads as many as are shown, from the start", reads[2], ["", 0, 3]);
});

test("mounted: an older answer is dropped; the search reads again after a pause and closes Show all", async () => {
	const timers = [];
	const mod = loadSimple(timers);
	const pending = [];
	const reads = [];
	const s = mounted(mod, (search, start, limit) => {
		reads.push([search, start, limit]);
		return new Promise((resolve) => pending.push(resolve));
	});
	s.view.refresh();
	s.view.refresh();
	pending[1](answer([card({ name: "NEW" })]));
	await settle();
	pending[0](answer([card({ name: "OLD" })]));
	await settle();
	check("the newer answer stands", s.view.state().cards.map((c) => c.name), ["NEW"]);
	s.root.fire("click", { target: target({ "[data-pk-simple-all]": { "data-pk-simple-all": "NEW", "data-pk-simple-half": "done" } }) });
	check("Show all opens that half, without a read", [[...s.view.state().open], reads.length], [["NEW|done"], 2]);
	const box = s.root.querySelector(".pk-simple-search");
	box.value = "  sonnen ";
	box.fire("input");
	check("nothing read while typing", reads.length, 2);
	flush(timers);
	check("after the pause: the search, from the start", reads[2], ["sonnen", 0, null]);
	check("and Show all closed", [...s.view.state().open], []);
	box.fire("input");
	flush(timers);
	check("the same words again read nothing", reads.length, 3);
	let stopped = false;
	box.fire("keydown", { key: "w", stopPropagation: () => (stopped = true) });
	check("keys typed in the box never reach the planner's shortcuts", stopped, true);
	stopped = false;
	box.fire("keydown", { key: "Escape", stopPropagation: () => (stopped = true) });
	check("Esc still reaches the kit and the Desk (a drawer closes)", stopped, false);
	pending[2](answer([]));
	await settle();
	check("nothing matched, said with the search", s.cards().innerHTML.includes("Nothing matches “sonnen”."), true);
});

test("mounted: a failed read says so with Try again, and Try again reads", async () => {
	const mod = loadSimple([]);
	let fail = true;
	const reads = [];
	const s = mounted(mod, (search, start) => {
		reads.push(start);
		return fail ? Promise.reject(new Error("503")) : Promise.resolve(answer([card()]));
	});
	s.view.refresh();
	await settle();
	check("the note and the button", [s.cards().innerHTML.includes("Simple mode could not be loaded."), s.cards().innerHTML.includes("pk-simple-retry")], [true, true]);
	fail = false;
	s.root.fire("click", { target: target({ ".pk-simple-retry": {} }) });
	await settle();
	check("read again, and drawn", [reads, s.view.state().cards.length, s.view.state().error], [[0, 0], 1, false]);
});

test("mounted: a failed search never leaves the old search's cards, so Show more cannot mix two searches", async () => {
	const timers = [];
	const mod = loadSimple(timers);
	const reads = [];
	let fail = false;
	const s = mounted(mod, (search, start) => {
		reads.push([search, start]);
		if (fail) return Promise.reject(new Error("timeout"));
		return Promise.resolve(answer([card({ name: `${search || "all"}-${start}` })], { total: 40, more: true }));
	});
	s.view.refresh();
	await settle();
	check("the first page of everything, with Show more", [s.view.state().cards.map((c) => c.name), s.tail().innerHTML.includes("pk-simple-more")], [["all-0"], true]);
	s.root.fire("click", { target: target({ "[data-pk-simple-all]": { "data-pk-simple-all": "all-0", "data-pk-simple-half": "done" } }) });
	fail = true;
	const box = s.root.querySelector(".pk-simple-search");
	box.value = "sonnen";
	box.fire("input");
	flush(timers);
	await settle();
	const st = s.view.state();
	check("the old search's cards, count, paging and Show all are gone", [st.cards, st.total, st.more, [...st.open]], [[], 0, false, []]);
	check("the failure names the search, with Try again and no Show more", [s.cards().innerHTML.includes("The search for “sonnen” could not be loaded."), s.cards().innerHTML.includes("pk-simple-retry"), s.tail().innerHTML], [true, true, ""]);
	check("no count left on the status line", s.line().textContent, "");
	fail = false;
	s.root.fire("click", { target: target({ ".pk-simple-retry": {} }) });
	await settle();
	check("Try again reads page one of the search in the box", [reads.pop(), s.view.state().cards.map((c) => c.name)], [["sonnen", 0], ["sonnen-0"]]);
	// A Show more that fails keeps the cards it would have added to (the same search) and says so instead.
	fail = true;
	s.root.fire("click", { target: target({ ".pk-simple-more": {} }) });
	await settle();
	check("a failed Show more keeps that search's cards, with Try again in place of Show more", [s.view.state().cards.map((c) => c.name), s.tail().innerHTML.includes("More could not be loaded."), s.tail().innerHTML.includes("pk-simple-more")], [["sonnen-0"], true, false]);
	fail = false;
	s.root.fire("click", { target: target({ ".pk-simple-retry": {} }) });
	await settle();
	check("Try again there reads the next page of the same search", [reads.pop(), s.view.state().cards.map((c) => c.name)], [["sonnen", 1], ["sonnen-0", "sonnen-1"]]);
});

test("mounted: the switch, ?, an item and a card's name are the page's", async () => {
	const mod = loadSimple([]);
	const s = mounted(mod, () => Promise.resolve(answer([card()])));
	s.root.querySelector(".pk-simple-switch").fire("click");
	s.root.querySelector(".pk-simple-help").fire("click");
	s.root.fire("click", { target: target({ "[data-pk-simple-open]": { "data-pk-simple-open": "Task", "data-pk-simple-name": "TASK-9" } }) });
	s.root.fire("click", { target: target({ "[data-pk-simple-open]": { "data-pk-simple-open": "Project", "data-pk-simple-name": "PRJ-1" } }) });
	check("off, help, and two opens", [s.seen.off, s.seen.help, s.seen.opened], [1, 1, [["Task", "TASK-9"], ["Project", "PRJ-1"]]]);
	s.root.fire("click", { target: null });
	s.root.fire("click", { target: target({}) });
	check("a click on nothing of its own does nothing", [s.seen.off, s.seen.opened.length], [1, 2]);
});

// ------------------------------------------------------------------ 3. the Maintenance Planner's half

function maintenancePage() {
	const prefs = {};
	const routes = [];
	const flags = [];
	const frappe = {
		pages: new Proxy({}, { get: (t, k) => (t[k] = t[k] || {}) }),
		session: { user: "lisa@example.com" },
		route_options: null,
		route_flags: {},
		datetime: { get_today: () => "2026-10-22" },
		get_route: () => frappe._route,
		_route: ["maintenance-planner"],
		set_route: (...args) => {
			routes.push(args);
			flags.push(!!(frappe.route_flags && frappe.route_flags.replace_route));
			frappe.route_flags = {};
		},
		utils: { escape_html: (v) => String(v) },
	};
	const ctx = { frappe, window: { localStorage: null }, $: () => ({}), __: (t) => t, moment: () => ({}), console };
	vm.createContext(ctx);
	vm.runInContext(fs.readFileSync(MP_JS, "utf8"), ctx, { filename: MP_JS });
	const MaintenancePlanner = vm.runInContext("MaintenancePlanner", ctx);
	const mp = Object.create(MaintenancePlanner.prototype);
	const writes = [];
	mp.load_pref = (key, fallback) => (key in prefs ? prefs[key] : fallback);
	mp.save_pref = (key, value) => {
		writes.push([key, value]);
		prefs[key] = value;
	};
	mp.filters = () => ({ technician: "" });
	mp.filter_options = () => ({});
	mp.p6a_route = (fn) => fn();
	const classes = [];
	const $el = () => ({ addClass: (c) => classes.push(["+", c]), removeClass: (c) => classes.push(["-", c]), show() {}, hide() {}, empty() { return this; }, append() { return this; } });
	mp.$body = $el();
	mp.$simple = $el();
	mp.mps = { view: { refresh: () => (mp.refreshed = (mp.refreshed || 0) + 1) } };
	return { mp, prefs, writes, routes, flags, frappe, classes };
}

const KEY = "ee_maintenance_planner_simple:lisa@example.com";

test("Maintenance Planner: the device flag is written only when it changes", () => {
	const { mp, writes, classes } = maintenancePage();
	check("a calendar on a device that never used Simple: nothing stored", [mp.mps_take("month"), writes], [false, []]);
	check("Simple: shown, and remembered", [mp.mps_take("simple"), writes, mp.refreshed], [true, [[KEY, "1"]], 1]);
	check("its class on the page", classes.some(([op, c]) => op === "+" && c === "mp-simple-on"), true);
	mp.mps_take("simple");
	check("Simple again: no second write", writes.length, 1);
	mp.mps_take("week");
	check("a calendar again: forgotten, once", writes, [[KEY, "1"], [KEY, "0"]]);
});

test("Maintenance Planner: a bare address on a device that used Simple last opens it, in place; never for a link", () => {
	const page = maintenancePage();
	check("no flag: the planner's own landing", [page.mp.mps_landing(), page.routes], [false, []]);
	page.prefs[KEY] = "1";
	check("the flag: Simple, replacing the bare address", [page.mp.mps_landing(), page.routes, page.flags], [true, [["maintenance-planner", "simple"]], [true]]);
	page.frappe.route_options = { technician: "austin@example.com" };
	check("a link that names a technician is a calendar's", page.mp.mps_landing(), false);
});

test("Maintenance Planner: the switch, and the shortcuts on Simple", () => {
	const page = maintenancePage();
	page.mp.mps_enter();
	check("on: /simple, a new entry", [page.routes, page.flags], [[["maintenance-planner", "simple", {}]], [false]]);
	page.frappe._route = ["maintenance-planner", "simple"];
	page.mp.mps_enter();
	check("on Simple itself: nothing", page.routes.length, 1);
	page.prefs[KEY] = "1";
	page.mp.mps_leave();
	check("off: the flag cleared, the bare address (the person's own last view), a new entry", [page.prefs[KEY], page.routes[1], page.flags[1]], ["0", ["maintenance-planner"], false]);
	page.mp.view = "simple";
	check("the shortcuts stand down", ["today", "prev", "view1", "undo", "legend"].map((a) => page.mp.p6b_run_key(a)), [false, false, false, false, false]);
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
