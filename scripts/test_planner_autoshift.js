#!/usr/bin/env node
/**
 * P4 of the planner UX redesign (docs/planner-ux-redesign.md; Nik's D8, D10, D11 and D15), run under
 * node against the REAL page scripts, each loaded whole into a sandbox with frappe stubbed:
 *
 *   1. The pure helpers: the "Notify crew?" default by the 14-day window (today through the 14th day,
 *      the old days counting as much as the new), on both pages and in step; the days a write gives a
 *      card; who is on a task after a write; the Undo entries for the tasks that moved along.
 *   2. The Project Planner's write path (commit, p6b_commit_many, send's decorate, undo): a change of
 *      days sends dependents=1 unless Ctrl/Cmd was held; a published schedule asks once per gesture,
 *      a multi-select move included, with Notify the default inside the window; Esc sends nothing; a
 *      Draft schedule, the hold, a task nobody is on and a pencil ask nothing; send() says
 *      draft="auto" (or the hold's 1); Undo puts the task and its followers back with the same answer.
 *   3. The drafts strip and the project peek: a line per project with drafts, Publish… running
 *      publish_project_schedule (and asking for a reason when it must), Set/Reset baseline.
 *   4. The Maintenance Planner: move_visit asks once per gesture, a multi-select move once for all,
 *      never in Draft mode, for a visit nobody is on, or for an Undo.
 *
 * Run: node scripts/test_planner_autoshift.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
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

const flush = () => new Promise((resolve) => setImmediate(resolve));
const translate = (text, args) => String(text).replace(/\{(\d+)\}/g, (all, i) => (args && args[i] !== undefined ? args[i] : all));

// ------------------------------------------------------------------ a small recording jQuery

class Node {
	constructor(html) {
		const m = /^<([a-z0-9]+)([^>]*)>/i.exec(String(html).trim());
		this.tag = m ? m[1] : "div";
		this.cls = ((m && /class="([^"]*)"/.exec(m[2])) || [])[1] || "";
		this.children = [];
		this.textContent = "";
		this.attrs = {};
		this.handlers = {};
		this.shown = true;
	}
	all() {
		return [this].concat(...this.children.map((child) => child.all()));
	}
}

function makeJQuery() {
	const wrap = (node) => ({
		node,
		appendTo(parent) {
			(parent.node || parent).children.push(node);
			return this;
		},
		text(value) {
			if (value === undefined) return node.all().map((n) => n.textContent).join("");
			node.textContent = String(value);
			return this;
		},
		attr(key, value) {
			node.attrs[key] = value;
			return this;
		},
		on(event, fn) {
			node.handlers[event] = fn;
			return this;
		},
		show() {
			node.shown = true;
			return this;
		},
		hide() {
			node.shown = false;
			return this;
		},
		empty() {
			node.children = [];
			return this;
		},
	});
	const $ = (arg) => (arg && arg.node ? arg : wrap(arg instanceof Node ? arg : new Node(arg)));
	return $;
}

// ------------------------------------------------------------------ loading a page

function loadPage(file, today) {
	const calls = [];
	const alerts = [];
	const confirms = [];
	const dialogs = [];
	const frappe = {
		pages: new Proxy({}, { get: (target, k) => (target[k] = target[k] || {}) }),
		utils: {
			escape_html: (value) =>
				String(value == null ? "" : value)
					.replace(/&/g, "&amp;")
					.replace(/</g, "&lt;")
					.replace(/>/g, "&gt;")
					.replace(/"/g, "&quot;"),
		},
		datetime: { get_today: () => today },
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		get_route: () => [],
		set_route: () => null,
		show_alert: (opts) => alerts.push(opts.message),
		msgprint: (m) => alerts.push(typeof m === "string" ? m : m.message),
		confirm: (message, yes) => {
			confirms.push(message);
			yes();
		},
		call: (opts) => {
			calls.push(opts);
			const answer = frappe.answers.length ? frappe.answers.shift() : {};
			return Promise.resolve({ message: answer });
		},
		answers: [],
		user: { full_name: (user) => ({ "clegg@example.com": "Clegg Jones" })[user] || user },
		ui: {
			Dialog: class {
				constructor(opts) {
					this.opts = opts;
					dialogs.push(this);
				}
				show() {}
				hide() {
					if (this.onhide) this.onhide();
				}
			},
		},
		route_options: null,
	};
	const ctx = {
		frappe,
		$: makeJQuery(),
		__: translate,
		window: { localStorage: null },
		document: { addEventListener: () => null, body: { classList: { add: () => null, remove: () => null } } },
		console,
		Promise,
		setTimeout: () => 0,
		clearTimeout: () => null,
		moment: (value) => ({ format: () => String(value), isValid: () => true }),
	};
	vm.createContext(ctx);
	vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
	return { ctx, frappe, calls, alerts, confirms, dialogs };
}

// A Project Planner with no DOM: the methods under test, the rest recorded or no-ops.
function projectPlanner(opts) {
	opts = opts || {};
	const env = loadPage(PP_JS, "2026-10-14");
	const ProjectPlanner = vm.runInContext("ProjectPlanner", env.ctx);
	const pp = Object.create(ProjectPlanner.prototype);
	const sent = [];
	const asked = [];
	Object.assign(pp, {
		view: "week",
		project: "",
		draft_on: false,
		undo_stack: [],
		modified: {},
		by_task: {},
		by_project: {
			"PRJ-P": { name: "PRJ-P", title: "Plaza", schedule: { status: "Published", baseline_set_on: "2026-10-01 09:00:00" } },
			"PRJ-D": { name: "PRJ-D", title: "Depot", schedule: { status: "Draft", baseline_set_on: null } },
			"PRJ-OLD": { name: "PRJ-OLD", title: "Old site" },
		},
		data: { can_edit: true, drafts: { count: 0, stale: [], projects: {} } },
		loads: 0,
		load() {
			this.loads += 1;
			return Promise.resolve();
		},
		render: () => null,
		update_undo_button: () => null,
		resource_label: (resource) => ({ "RES-1": "Austin", "RES-2": "Lisa" })[resource] || resource,
		// The question, answered as the test says (null is Esc).
		as4_notify_dialog(notify, people, count) {
			asked.push({ notify, people, count });
			return Promise.resolve(opts.answer === undefined ? (notify ? "1" : "0") : opts.answer);
		},
	});
	// send() as the page has it, down to the wire: frappe.call is the recorder.
	const real_send = pp.send;
	pp.send = function (method, args, o) {
		sent.push([method, args]);
		return real_send.call(this, method, args, o);
	};
	pp.p6_send_modules = { move_many: "erpnext_enhancements.api.planner_actions" };
	pp.p6a_undo_toast = () => true;
	return Object.assign(env, { pp, sent, asked });
}

const card = (name, values) =>
	Object.assign(
		{ name, subject: `Subject ${name}`, project: "PRJ-P", start: "2026-10-15", end: "2026-10-16", crew: [{ resource: "RES-1", hours: null, is_lead: 1 }], modified: "m0" },
		values || {}
	);

// ------------------------------------------------------------------ 1. pure helpers

test("The Notify default: today through the 14th day, old days or new, on both pages alike", () => {
	const { ctx } = projectPlanner();
	const mp = loadPage(MP_JS, "2026-10-14");
	const PURE = vm.runInContext("PPAS_PURE", ctx);
	const MPURE = vm.runInContext("MPAS_PURE", mp.ctx);
	const today = "2026-10-14";
	check("the window", PURE.notify_window(today), ["2026-10-14", "2026-10-27"]);
	const cases = [
		[[["2026-10-14", "2026-10-14"]], true], // today
		[[["2026-10-27", "2026-10-28"]], true], // day 14
		[[["2026-10-28", "2026-10-29"]], false], // day 15
		[[["2026-10-01", "2026-10-13"]], false], // all past
		[[["2026-10-10", "2026-10-14"]], true], // runs into it
		[[["2026-10-20", "2026-10-20"], ["2027-03-02", "2027-03-03"]], true], // next week to March
		[[["2027-03-02", "2027-03-03"]], false],
		[[null], false],
		[[], false],
	];
	cases.forEach(([spans, want], i) => {
		check(`case ${i} (project)`, PURE.notify_default(spans, today), want);
		check(`case ${i} (maintenance)`, MPURE.notify_default(spans, today), want);
	});
	check("same window", MPURE.notify_window(today), PURE.notify_window(today));
});

test("The days a write gives a card, who is on it after, and the Undo entries", () => {
	const { ctx } = projectPlanner();
	const PURE = vm.runInContext("PPAS_PURE", ctx);
	const c = card("T1");
	check("a start alone keeps the length", PURE.new_span(c, { start: "2026-10-20" }), ["2026-10-20", "2026-10-21"]);
	check("both as given", PURE.new_span(c, { start: "2026-10-20", end: "2026-10-23" }), ["2026-10-20", "2026-10-23"]);
	check("an end alone keeps the start", PURE.new_span(c, { end: "2026-10-19" }), ["2026-10-15", "2026-10-19"]);
	check("a crew handover's date", PURE.new_span(c, { date: "2026-10-22" }), ["2026-10-22", "2026-10-23"]);
	check("an undated card becomes one day", PURE.new_span(card("T2", { start: null, end: null }), { start: "2026-10-20" }), ["2026-10-20", "2026-10-20"]);
	check("nothing about days", PURE.new_span(c, { tentative: 1 }), ["2026-10-15", "2026-10-16"]);
	check("crew sent", [...PURE.people_after(c, "save_task", { crew: JSON.stringify([{ resource: "RES-2" }]) })], ["RES-2"]);
	check("a person added", [...PURE.people_after(c, "add_crew", { resource: "RES-2" })], ["RES-1", "RES-2"]);
	check("a handover", [...PURE.people_after(c, "swap_crew", { from_resource: "RES-1", to_resource: "RES-2" })], ["RES-2"]);
	check(
		"undo items",
		PURE.undo_items([{ task: "T2", from: "2026-10-15", from_end: "2026-10-16" }, { task: "T3", from: "2026-10-19" }, { task: "T4" }]),
		[{ task: "T2", start: "2026-10-15", end: "2026-10-16" }, { task: "T3", start: "2026-10-19", end: "2026-10-19" }]
	);
	check("draft value", [PURE.draft_value(true), PURE.draft_value(false)], [1, "auto"]);
});

// ------------------------------------------------------------------ 2. the write path

test("A drag on a published schedule asks once, Notify inside the window, and takes the followers", async () => {
	const { pp, sent, asked, calls } = projectPlanner();
	const c = card("T1");
	await pp.commit(c, "save_task", { task: "T1", start: "2026-10-19" }, "moved");
	check("one question, Notify the default, Austin named", asked, [{ notify: true, people: ["RES-1"], count: 1 }]);
	const [method, args] = sent[0];
	check("the save", [method, args.task, args.start, args.dependents, args.notify], ["save_task", "T1", "2026-10-19", 1, "1"]);
	check("send() says auto: the server drafts a Draft schedule's task", calls[0].args.draft, "auto");
	check("on the wire", calls[0].method, "erpnext_enhancements.api.project_planner.save_task");
	check("then a reload", pp.loads, 1);
});

test("Ctrl/Cmd moves just the one task; a far-out change defaults to Don't notify", async () => {
	const { pp, sent, asked } = projectPlanner();
	const far = card("T1", { start: "2026-11-16", end: "2026-11-17" });
	await pp.commit(far, "save_task", { task: "T1", start: "2026-11-23" }, "moved", { solo: true });
	check("Don't notify is the default", asked[0].notify, false);
	check("no followers, the answer sent", [sent[0][1].dependents, sent[0][1].notify], [undefined, "0"]);
});

test("Esc on the question sends nothing and puts the card back", async () => {
	const { pp, sent, asked } = projectPlanner({ answer: null });
	await pp.commit(card("T1"), "save_task", { task: "T1", start: "2026-10-19" }, "moved");
	check("asked, then nothing sent", [asked.length, sent.length, pp.loads], [1, 0, 1]);
});

test("Nothing is asked on a Draft schedule, in the hold, for a task nobody is on, or for a pencil", async () => {
	const { pp, sent, asked, calls } = projectPlanner();
	await pp.commit(card("T1", { project: "PRJ-D" }), "save_task", { task: "T1", start: "2026-10-19" }, "m");
	check("a Draft schedule: auto, followers, no answer", [calls[0].args.draft, sent[0][1].dependents, "notify" in sent[0][1]], ["auto", 1, false]);
	await pp.commit(card("T2", { crew: [] }), "save_task", { task: "T2", start: "2026-10-19" }, "m");
	check("nobody on it", "notify" in sent[1][1], false);
	await pp.commit(card("T3"), "save_task", { task: "T3", tentative: 1 }, "m");
	check("a pencil moves nobody's days", ["notify" in sent[2][1], "dependents" in sent[2][1]], [false, false]);
	await pp.commit(card("T4", { project: "PRJ-OLD" }), "save_task", { task: "T4", start: "2026-10-19" }, "m");
	check("before the schedule fields exist", "notify" in sent[3][1], false);
	pp.draft_on = true;
	await pp.commit(card("T5"), "save_task", { task: "T5", start: "2026-10-19" }, "m");
	check("the hold drafts everything", [calls[4].args.draft, "notify" in sent[4][1]], [1, false]);
	check("never asked", asked.length, 0);
});

test("The crews of the tasks that follow (on screen) are asked about too, even when the task has none", async () => {
	const { pp, sent, asked } = projectPlanner();
	const phase = card("T1", { crew: [] });
	pp.by_task = {
		T1: phase,
		T2: card("T2", { depends_on: ["T1"], crew: [{ resource: "RES-2" }] }),
		T3: card("T3", { depends_on: ["T2"], crew: [{ resource: "RES-1" }] }),
		T9: card("T9", { crew: [{ resource: "RES-9" }] }),
	};
	await pp.commit(phase, "save_task", { task: "T1", start: "2026-10-19" }, "moved");
	check("Lisa and Austin, through two levels; not the unrelated task", asked.map((a) => a.people.slice().sort()), [["RES-1", "RES-2"]]);
	check("the answer sent", sent[0][1].notify, "1");
	await pp.commit(phase, "save_task", { task: "T1", start: "2026-10-19" }, "moved", { solo: true });
	check("Ctrl/Cmd: they do not move, so nobody to ask", asked.length, 1);
});

test("The question's default button has the focus, so Enter takes it", () => {
	const { ctx, dialogs } = projectPlanner();
	const ProjectPlanner = vm.runInContext("ProjectPlanner", ctx);
	const pp = Object.create(ProjectPlanner.prototype);
	pp.resource_label = (r) => r;
	const focused = [];
	ctx.frappe.ui.Dialog.prototype.get_primary_btn = function () {
		return { trigger: (event) => focused.push([event, this.opts.primary_action_label]) };
	};
	pp.as4_notify_dialog(false, ["RES-1"], 1);
	check("Don't notify is primary and focused", focused, [["focus", "Don't notify"]]);
	check("Notify is the other button", dialogs[0].opts.secondary_action_label, "Notify");
});

test("A crew change on a published schedule asks too, and carries no followers", async () => {
	const { pp, sent, asked } = projectPlanner();
	await pp.commit(card("T1"), "add_crew", { task: "T1", resource: "RES-2" }, "added");
	check("Austin and Lisa", asked[0].people, ["RES-1", "RES-2"]);
	check("add_crew with the answer", [sent[0][0], sent[0][1].notify, "dependents" in sent[0][1]], ["add_crew", "1", false]);
});

test("A multi-select move asks one question for all of them, and moves their followers", async () => {
	const { pp, sent, asked, calls } = projectPlanner();
	const cards = [card("T1"), card("T2", { crew: [{ resource: "RES-2" }] }), card("T3", { project: "PRJ-D" })];
	cards.forEach((c) => (pp.by_task[c.name] = c));
	pp.p6_selection = new Set(["T1", "T2", "T3"]);
	const moves = cards.map((c) => ({ task: c.name, modified: "m0", start: "2026-10-22", end: "2026-10-23" }));
	const snapshot = { p6b: "many", what: "dates", subject: "3 tasks", items: [] };
	await pp.p6b_commit_many(cards, moves, snapshot, "3 tasks moved");
	check("one question, the two published tasks", asked, [{ notify: true, people: ["RES-1", "RES-2"], count: 2 }]);
	check("one move_many", sent.map(([m]) => m), ["move_many"]);
	check("followers, the answer, auto", [sent[0][1].dependents, sent[0][1].notify, calls[0].args.draft], [1, "1", "auto"]);
	check("to planner_actions", calls[0].method, "erpnext_enhancements.api.planner_actions.move_many");
	check("the Undo entry keeps the answer", snapshot.notify, "1");
});

test("The answer is decorated: followers go into the Undo entry and the message", async () => {
	const { pp, frappe, alerts } = projectPlanner();
	const c = card("T1");
	frappe.answers.push({
		name: "T1",
		modified: "m1",
		shifted: [
			{ task: "T3", from: "2026-10-19", from_end: "2026-10-19", to: "2026-10-21", modified: "m3" },
			{ task: "T2", from: "2026-10-15", from_end: "2026-10-16", to: "2026-10-19", modified: "m2" },
		],
		shift_skipped: [{ task: "T9", subject: "Inspect", reason: "It is Completed." }],
	});
	let message = null;
	pp.p6a_undo_toast = (snap, text) => {
		message = text;
		return true;
	};
	await pp.commit(c, "save_task", { task: "T1", start: "2026-10-19" }, "Subject T1 moved");
	const snap = pp.undo_stack[0];
	check("the message says so", message, "Subject T1 moved and 2 tasks that follow");
	check("the Undo entry", [snap.task, snap.start, snap.dates_only, snap.notify, snap.shifted], [
		"T1",
		"2026-10-15",
		true,
		"1",
		[{ task: "T3", start: "2026-10-19", end: "2026-10-19" }, { task: "T2", start: "2026-10-15", end: "2026-10-16" }],
	]);
	check("their modified is remembered", [pp.modified.T2, pp.modified.T3], ["m2", "m3"]);
	check("the one left behind is named", alerts.some((a) => a.includes("Inspect: It is Completed.")), true);
});

test("Undo puts the task and its followers back with the same answer, and the rest of a crew change", async () => {
	const { pp, sent } = projectPlanner();
	pp.modified = { T1: "m1", T2: "m2" };
	pp.undo_stack.push({ task: "T1", subject: "S", start: "2026-10-15", end: "2026-10-16", dates_only: true, notify: "1", shifted: [{ task: "T2", start: "2026-10-19", end: "2026-10-20" }], crew: [], credentials: [] });
	pp.p6a_close_toast = () => null;
	pp.t1_undo = () => false;
	pp.undo();
	await flush();
	await flush();
	check("one move_many", sent.map(([m]) => m), ["move_many"]);
	check(
		"both back, the answer again, no followers of their own",
		[JSON.parse(sent[0][1].moves), sent[0][1].notify, "dependents" in sent[0][1]],
		[[{ task: "T1", modified: "m1", start: "2026-10-15", end: "2026-10-16" }, { task: "T2", modified: "m2", start: "2026-10-19", end: "2026-10-20" }], "1", false]
	);
	sent.length = 0;
	pp.undo_stack.push({ task: "T1", subject: "S", start: "2026-10-15", end: "2026-10-16", dates_only: false, notify: "0", expected_time: 4, crew_size: 0, crew: [{ resource: "RES-1" }], credentials: [], shifted: [{ task: "T2", start: "2026-10-19", end: "2026-10-20" }] });
	pp.undo();
	await flush();
	await flush();
	await flush();
	check("then the rest of the task, no dates", sent.map(([m, a]) => [m, a.start, a.notify]), [["move_many", undefined, "0"], ["save_task", undefined, "0"]]);
});

test("A plain Undo sends the change's answer again", async () => {
	const { pp, sent } = projectPlanner();
	pp.undo_stack.push({ task: "T1", subject: "S", start: "2026-10-15", end: "2026-10-16", notify: "1", crew: [], credentials: [] });
	pp.p6a_close_toast = () => null;
	pp.t1_undo = () => false;
	pp.undo();
	await flush();
	check("save_task with notify", [sent[0][0], sent[0][1].notify], ["save_task", "1"]);
});

test("Ctrl/Cmd at the drop is read from the pointer event", () => {
	const { pp } = projectPlanner();
	const dropped = [];
	pp.find_target = () => ({ date: "2026-10-19" });
	pp.end_drag = () => null;
	pp.drop = (source, target) => dropped.push(target.solo);
	[{ ctrlKey: true }, { metaKey: true }, {}].forEach((mods) => {
		pp.drag = { id: 1, active: true, source: { kind: "card" } };
		pp.on_up(Object.assign({ pointerId: 1, clientX: 0, clientY: 0 }, mods));
	});
	check("Ctrl, Cmd, neither", dropped, [true, true, false]);
});

test("Creating writes draft on a Draft schedule; the read always asks for drafts", () => {
	const { pp } = projectPlanner();
	check("Draft schedule", pp.draft_args("PRJ-D"), { draft: 1 });
	check("published", pp.draft_args("PRJ-P"), {});
	check("unknown", pp.draft_args("PRJ-X"), {});
	pp.draft_on = true;
	check("the hold", pp.draft_args("PRJ-P"), { draft: 1 });
	check("an explicit draft is left alone", pp.with_draft("save_task", { draft: 0 }), { draft: 0 });
	check("other writes are not touched", pp.with_draft("bulk_update", { tasks: "[]" }), { tasks: "[]" });
	const source = fs.readFileSync(PP_JS, "utf8");
	check("load reads with draft=1", source.includes("args: { start: pp_ymd(start), end: pp_ymd(end), draft: 1 },"), true);
});

// ------------------------------------------------------------------ 3. the strip and the peek

test("The strip: a line per project with drafts, Publish… on a Draft schedule", async () => {
	const { pp, ctx, frappe, calls, confirms } = projectPlanner();
	const $ = ctx.$;
	pp.data.drafts = { count: 4, stale: [], projects: { "PRJ-D": { count: 3, shared: 1 }, "PRJ-P": { count: 1, shared: 0 } } };
	const $bar = $("<div></div>");
	check("drawn", pp.as4_render_strip($bar), true);
	const lines = $bar.node.children;
	check("the lines", lines.map((line) => line.children[0].textContent), ["Depot · Draft schedule · 3 change(s), 1 by others", "Plaza · 1 change(s) held, not published yet"]);
	const buttons = (line) => line.children.filter((n) => n.tag === "button").map((n) => n.textContent);
	check("buttons", lines.map(buttons), [["Publish…", "Discard"], ["Publish", "Discard"]]);
	// Publish…: a confirmation, then the project's publish; a reason when it must.
	frappe.answers.push({ needs_reason: true, conflicts: { Lisa: ["Over"] } }, { published: [{ task: "T1" }], schedule: { status: "Published" }, baselined: 4, notified: 2 });
	pp.ask_reason = () => Promise.resolve("Fine");
	pp.show_publish_result = () => null;
	lines[0].children.find((n) => n.textContent === "Publish…").handlers.click();
	await flush();
	await flush();
	await flush();
	check("asked first", confirms.length, 1);
	check(
		"publish_project_schedule, then again with the reason",
		calls.map((c) => [c.method.split(".").pop(), c.args]),
		[["publish_project_schedule", { project: "PRJ-D" }], ["publish_project_schedule", { project: "PRJ-D", reason: "Fine" }]]
	);
	check("then a reload", pp.loads, 1);
	// The held changes of a published project: publish_drafts for that project only.
	calls.length = 0;
	frappe.answers.push({ published: [] });
	lines[1].children.find((n) => n.textContent === "Publish").handlers.click();
	await flush();
	check("publish_drafts(project)", [calls[0].method.split(".").pop(), calls[0].args], ["publish_drafts", { project: "PRJ-P" }]);
	// The hold on: only Draft schedules get their own line.
	pp.draft_on = true;
	const $held = $("<div></div>");
	pp.as4_render_strip($held);
	check("hold: Draft schedules only", $held.node.children.length, 1);
	// One project on screen whose schedule is Draft, nothing drafted yet: Publish… is still there.
	pp.draft_on = false;
	pp.project = "PRJ-D";
	pp.data.drafts = { count: 0, stale: [], projects: {} };
	const $one = $("<div></div>");
	pp.as4_render_strip($one);
	check("say so before anything is drafted", [$one.node.children[0].children[0].textContent, buttons($one.node.children[0])], ["Depot · Draft schedule: changes here stay drafts until you publish", ["Publish…"]]);
	pp.project = "";
	check("nothing to show", pp.as4_render_strip($("<div></div>")), false);
});

test("Another scheduler's draft says whose it is", () => {
	const { pp } = projectPlanner();
	check("first name", pp.as4_drafter("clegg@example.com"), "Clegg");
	check("an unknown user", pp.as4_drafter("pat@example.com"), "pat");
});

test("The project peek: Publish schedule… and Set or Reset baseline", async () => {
	const { pp, frappe, calls, confirms } = projectPlanner();
	check("a Draft schedule", pp.as4_project_actions("PRJ-D").map((a) => a.label), ["Publish schedule…", "Set baseline…"]);
	check("a published one", pp.as4_project_actions("PRJ-P").map((a) => a.label), ["Reset baseline…"]);
	check("before the fields exist", pp.as4_project_actions("PRJ-OLD"), []);
	pp.data.can_edit = false;
	check("nobody who cannot change tasks", pp.as4_project_actions("PRJ-P"), []);
	pp.data.can_edit = true;
	frappe.answers.push({ changed: 3 }, { changed: 3 });
	pp.as4_project_actions("PRJ-P")[0].on_click();
	await flush();
	await flush();
	await flush();
	check("a dry run, a confirmation naming the count, then the reset", [calls.map((c) => c.args), confirms[0]], [
		[{ project: "PRJ-P", dry_run: 1 }, { project: "PRJ-P" }],
		"Reset the baseline of Plaza? 3 task(s) get their days now as their baseline.",
	]);
});

// ------------------------------------------------------------------ 4. the Maintenance Planner

function maintenancePlanner(answer) {
	const env = loadPage(MP_JS, "2026-10-14");
	const MaintenancePlanner = vm.runInContext("MaintenancePlanner", env.ctx);
	const mp = Object.create(MaintenancePlanner.prototype);
	const asked = [];
	Object.assign(mp, {
		draft_on: false,
		modified: {},
		data: { today: "2026-10-14" },
		by_key: {
			"SMR-1": { kind: "visit", name: "SMR-1", date: "2026-10-16", technician: "austin@x.com", crew: [] },
			"SMR-2": { kind: "visit", name: "SMR-2", date: "2026-10-16", technician: "", crew: [] },
		},
		tech_name: (user) => user.split("@")[0],
		c1_refuse: () => false,
		mpas_ask(notify, people, count) {
			asked.push({ notify, people, count });
			return Promise.resolve(answer === undefined ? (notify ? "1" : "0") : answer);
		},
	});
	return Object.assign(env, { mp, asked });
}

test("Maintenance: move_visit asks once, Notify inside the window; never in Draft mode, for nobody, or for an Undo", async () => {
	const { mp, asked, calls } = maintenancePlanner();
	const snapshot = { kind: "visit", date: "2026-10-16", technician: "austin@x.com", crew: [] };
	await mp.send("move_visit", { record: "SMR-1", date: "2026-11-30" }, { snapshot });
	check("asked once, Notify (the old day is this week)", asked, [{ notify: true, people: ["austin@x.com"], count: 1 }]);
	check("sent with the answer", [calls.length, calls[0].args.notify], [1, "1"]);
	check("the Undo entry keeps it", snapshot.notify, "1");
	await mp.send("move_visit", { record: "SMR-1", date: "2026-11-30", notify: null }, {});
	check("an Undo is never asked", asked.length, 1);
	await mp.send("move_visit", { record: "SMR-2", date: "2026-10-20" }, {});
	check("a visit nobody is on", asked.length, 1);
	mp.draft_on = true;
	mp.c1_with_draft = (method, args) => Object.assign({}, args, { draft: 1 });
	await mp.send("move_visit", { record: "SMR-1", date: "2026-10-20" }, {});
	check("Draft mode", asked.length, 1);
	check("every one sent", calls.length, 4);
});

test("Maintenance: a technician chip dropped on a visit (add_crew) asks too", async () => {
	const { mp, asked, calls } = maintenancePlanner();
	await mp.send("add_crew", { record: "SMR-1", user: "jesse@x.com" }, { snapshot: { kind: "visit", date: "2026-10-16", technician: "austin@x.com", crew: [] } });
	check("Austin and Jesse", asked[0].people, ["austin@x.com", "jesse@x.com"]);
	check("sent with the answer", calls[0].args.notify, "1");
});

test("Maintenance: Esc sends nothing; a far-out move defaults to Don't notify", async () => {
	const esc = maintenancePlanner(null);
	await esc.mp.send("move_visit", { record: "SMR-1", date: "2026-10-20" }, {});
	check("nothing sent", esc.calls.length, 0);
	const far = maintenancePlanner();
	far.mp.by_key["SMR-1"].date = "2026-12-01";
	await far.mp.send("move_visit", { record: "SMR-1", date: "2026-12-08" }, {});
	check("Don't notify", [far.asked[0].notify, far.calls[0].args.notify], [false, "0"]);
});

test("Maintenance: a multi-select move asks once for every visit", async () => {
	const { mp, asked } = maintenancePlanner();
	const went = [];
	const plans = [
		{ card: mp.by_key["SMR-1"], date: "2026-10-20" },
		{ card: Object.assign({}, mp.by_key["SMR-1"], { name: "SMR-3", technician: "jesse@x.com" }), date: "2026-10-21" },
	];
	await mp.mpas_batch(plans, (notify) => went.push(notify));
	check("one question, both technicians", asked, [{ notify: true, people: ["austin@x.com", "jesse@x.com"], count: 2 }]);
	check("then the moves, with the answer", went, ["1"]);
	mp.draft_on = true;
	await mp.mpas_batch(plans, (notify) => went.push(notify));
	check("Draft mode: straight on, nothing asked", [asked.length, went], [1, ["1", undefined]]);
	const source = fs.readFileSync(MP_JS, "utf8");
	check("the moves carry it", source.includes("if (notify !== undefined) args.notify = notify;"), true);
});

// ------------------------------------------------------------------ run

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (e) {
			failures += 1;
			console.log(`  ERROR ${e && e.stack ? e.stack : e}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	if (failures) {
		console.log(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed`);
})();
