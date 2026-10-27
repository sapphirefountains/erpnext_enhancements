#!/usr/bin/env node
/**
 * P9 of the planner UX redesign: the Project Planner's Overview tab (docs/planner-ux-redesign.md §4.7),
 * run under node against the REAL page script.
 *
 *   1. PPO_PURE: the window (whole calendar months), a month step, the address, the title, a span clipped to
 *      the window, the phase colour classes, where an exception line goes, and the filters on the lines.
 *   2. The chart's rows (chart_rows): one row per job with a bar for its whole span, its phases under it over
 *      a baseline ghost, milestones as diamonds, a Draft schedule marked, the project's own dates marked,
 *      everything clipped to the window, the page's filters applied; record text escaped where this block
 *      writes markup (the tooltip, the columns), left raw only where the widget owns it.
 *   3. The three parts of the page, from a get_overview-shaped answer: the header (months picker, Expand
 *      all, Print weekly summary, the Draft schedules with Publish…), Needs attention (a button per line,
 *      worst first, a line with no source disabled, "Show all", the one-line all-clear) and the foot (jobs
 *      with no dates, jobs outside the window, how many have a baseline, what the counts leave out).
 *   4. The widget it mounts: READ-ONLY (nothing to drag, no link to draw, no write hook), the baseline layer
 *      on, the month zoom, an export without CSV or Excel, rows from the live answer and the live filters,
 *      a click opening that job's Gantt.
 *   5. The whole render against a small DOM model: built once, rebound each time, the chart mounted once and
 *      refreshed (not remounted) when the answer or the filters change, another tab's answer never drawn,
 *      a failed load a note with Try again. And every control: a line, the months picker, Expand all,
 *      Publish… (P4's own confirmation), Print weekly summary (a window that opens on the click, the page
 *      from the server printed there, a blocked pop-up said, a failed request closing the window).
 *
 * Routing, the landing for the Executive Team and Back/Forward run against the router model in
 * scripts/test_planner_today.js.
 *
 * Run: node scripts/test_planner_overview.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const PP_JS = path.join(__dirname, "..", "erpnext_enhancements", "project_enhancements", "page", "project_planner", "project_planner.js");

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

// ------------------------------------------------------------------ a small DOM and the page

class FakeEl {
	constructor(name) {
		this.name = name;
		this._html = "";
		this.kids = {};
		this.attrs = {};
		this.handlers = [];
	}
	get innerHTML() {
		return this._html;
	}
	set innerHTML(value) {
		this._html = String(value);
	}
	all_html() {
		return this._html + Object.values(this.kids).map((kid) => kid.all_html()).join("");
	}
	querySelector(selector) {
		if (selector.startsWith(".")) return this.kids[selector.slice(1)] || null;
		return this.querySelectorAll(selector)[0] || null;
	}
	// Only [attribute] selectors: each attribute in the HTML becomes an element that records its click handlers.
	querySelectorAll(selector) {
		const match = /^\[([\w-]+)\]$/.exec(selector);
		if (!match) return [];
		const found = [];
		const re = new RegExp(`${match[1]}(?:="([^"]*)")?(?=[\\s>])`, "g");
		let hit;
		const html = this.all_html();
		while ((hit = re.exec(html))) {
			const el = {
				attrs: { [match[1]]: hit[1] === undefined ? "" : hit[1] },
				handlers: [],
				getAttribute(name) {
					return name in this.attrs ? this.attrs[name] : null;
				},
				addEventListener(type, fn) {
					this.handlers.push(fn);
				},
				click() {
					this.handlers.forEach((fn) => fn({ preventDefault() {} }));
				},
			};
			found.push(el);
		}
		(this.bound = this.bound || {})[selector] = found;
		return found;
	}
	// The elements the last querySelectorAll(selector) made, for a test to click.
	last(selector) {
		return (this.bound && this.bound[selector]) || [];
	}
	contains(el) {
		return el === this || Object.values(this.kids).includes(el);
	}
}

// What $(...) gives the page: a wrapper over a FakeEl, with the few jQuery calls the Overview makes.
function jq(el) {
	const wrapper = {
		0: el,
		el,
		hide() {
			el.hidden = true;
			return wrapper;
		},
		show() {
			el.hidden = false;
			return wrapper;
		},
		empty() {
			el.kidsList = [];
			el._html = "";
			return wrapper;
		},
		text(value) {
			el.textValue = value;
			return wrapper;
		},
		attr(name, value) {
			el.attrs[name] = value;
			return wrapper;
		},
		find(selector) {
			return jq(el.kids[selector.replace(/^\./, "")] || new FakeEl(selector));
		},
		appendTo(target) {
			const holder = target[0];
			(holder.kidsList = holder.kidsList || []).push(el);
			return wrapper;
		},
	};
	return wrapper;
}

// The root the page makes for the Overview: the four parts it looks up by class.
function makeRoot() {
	const root = new FakeEl("ppo-board");
	["ppo-head", "ppo-needs", "ppo-chart", "ppo-foot"].forEach((cls) => (root.kids[cls] = new FakeEl(cls)));
	return root;
}

function makeGrid() {
	const grid = new FakeEl("grid-wrap");
	grid.kidsList = [];
	grid.contains = (el) => grid.kidsList.includes(el);
	return grid;
}

function load(extra) {
	const state = { mounts: [], opened: [], alerts: [], msgprints: [], confirms: [], calls: [] };
	const frappe = {
		pages: { "project-planner": {} },
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		datetime: { get_today: () => "2026-10-21" },
		session: { user: "nik@example.com" },
		user: { has_role: (role) => (state.roles || ["Executive Team"]).includes(role) },
		utils: {
			escape_html: (v) =>
				String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"),
		},
		call(opts) {
			state.calls.push(opts);
			const answer = (state.answers || {})[opts.method.split(".").pop()];
			if (answer instanceof Error) return Promise.reject(answer);
			return Promise.resolve({ message: typeof answer === "function" ? answer(opts.args || {}) : answer });
		},
		msgprint: (text) => state.msgprints.push(text),
		show_alert: (a) => state.alerts.push(a),
		confirm: (text, yes) => state.confirms.push({ text, yes }),
	};
	const fakeWindow = {
		localStorage: { getItem: () => null, setItem() {} },
		matchMedia: () => ({ matches: false }),
		open: (...args) => {
			const win = state.popup === null ? null : state.popup || (state.popup = makePopup());
			state.opened.push(args);
			return win;
		},
	};
	const gantt = {
		mount(chart, config) {
			const widget = { chart, config, destroyed: false, refreshed: 0, destroy() { this.destroyed = true; }, refresh() { this.refreshed += 1; return Promise.resolve(); } };
			state.mounts.push(widget);
			return widget;
		},
	};
	fakeWindow.erpnext_enhancements = { gantt };
	const sandbox = {
		frappe,
		window: fakeWindow,
		erpnext_enhancements: fakeWindow.erpnext_enhancements,
		document: { elementFromPoint: () => null },
		$: (html) => {
			const el = String(html).includes("ppo-board") ? makeRoot() : new FakeEl("x");
			return jq(el);
		},
		__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] != null ? String(args[i]) : "")),
		moment: (value) => ({ isValid: () => /^\d{4}-\d{2}-\d{2}$/.test(String(value)) }),
		console,
		CSS: { escape: (s) => String(s) },
	};
	Object.assign(sandbox, extra || {});
	const context = vm.createContext(sandbox);
	vm.runInContext(`${fs.readFileSync(PP_JS, "utf-8")}\nthis.ProjectPlanner = ProjectPlanner; this.PPO = PPO; this.PPO_PURE = PPO_PURE; this.PPG = PPG;`, context, { filename: PP_JS });
	context.__state = state;
	return context;
}

function makePopup() {
	const popup = {
		closed: false,
		written: [],
		printed: 0,
		focused: 0,
		document: {
			fonts: { ready: Promise.resolve() },
			write(html) {
				popup.written.push(html);
			},
			open() {
				popup.written = [];
			},
			close() {},
		},
		close() {
			popup.closed = true;
		},
		focus() {
			popup.focused += 1;
		},
		setTimeout: (fn) => fn(),
		print() {
			popup.printed += 1;
		},
	};
	return popup;
}

const ctx = load();
const P = ctx.PPO_PURE;

// ------------------------------------------------------------------ a get_overview answer

function job(extra) {
	return Object.assign(
		{
			name: "PRJ-1",
			title: "Riverwalk Plaza",
			pm: "Clegg",
			schedule: { status: "Published", published_on: "2026-10-01 09:00:00" },
			draft: false,
			first: "2026-09-28",
			last: "2026-12-15",
			base_first: "2026-10-05",
			base_last: "2026-11-20",
			source: "tasks",
			counts: { tasks: 10, done: 4, baselined: 8 },
			phases: [
				{ key: "G100", name: "G100", subject: "Phase 100 Design", number: 100, start: "2026-09-28", end: "2026-10-09", baseline: { start: "2026-10-05", end: "2026-10-09" }, slip_days: null, tasks: 3, done: 3, open: 0, progress: 1 },
				{ key: "G300", name: "G300", subject: "Phase 300 — Rough-in", number: 300, start: "2026-10-12", end: "2026-11-30", baseline: { start: "2026-10-12", end: "2026-11-20" }, slip_days: 6, tasks: 5, done: 1, open: 4, progress: 0.2 },
				{ key: "", name: null, subject: "Other work", number: null, start: "2026-12-01", end: "2026-12-15", baseline: null, slip_days: null, tasks: 2, done: 0, open: 2, progress: 0 },
			],
			milestones: [{ name: "M1", subject: "Hand-off", date: "2026-12-15", done: false }],
		},
		extra
	);
}

function answer(extra) {
	const jobs = [
		job(),
		job({
			name: "PRJ-2",
			title: "Tanner Park",
			pm: "Nik",
			schedule: { status: "Draft" },
			draft: true,
			first: "2026-11-02",
			last: "2027-01-20",
			base_first: null,
			base_last: null,
			counts: { tasks: 4, done: 0, baselined: 0 },
			phases: [{ key: "G200", name: "G200", subject: "Phase 200", number: 200, start: "2026-11-02", end: "2027-01-20", baseline: null, slip_days: null, tasks: 4, done: 0, open: 4, progress: 0 }],
			milestones: [],
		}),
		job({ name: "PRJ-3", title: "Sanctuary", pm: "Clegg", first: "2026-10-01", last: "2027-03-31", source: "project", phases: [], milestones: [], base_first: null, base_last: null, counts: { tasks: 0, done: 0, baselined: 0 } }),
	];
	return Object.assign(
		{
			today: "2026-10-21",
			start: "2026-10-01",
			end: "2027-03-31",
			months: 6,
			choices: [3, 6, 9, 12],
			can_edit: true,
			jobs,
			no_dates: [{ name: "PRJ-9", title: "Myers-Roy", pm: "Nik" }],
			outside: 2,
			exceptions: [
				{ kind: "behind", severity: 2, project: "PRJ-1", project_title: "Riverwalk Plaza", phase: "G300", task: null, subject: "Phase 300 — Rough-in", days: 6, count: 1, date: null, text: "Riverwalk Plaza: Phase 300 — Rough-in is 6 working days behind its baseline" },
				{ kind: "over", severity: 2, project: "PRJ-2", project_title: "Tanner Park", phase: null, task: "T9", subject: "Set vault", days: null, count: 1, date: null, text: "Tanner Park: “Set vault” is running over (14h of 8h)" },
				{ kind: "conflicts", severity: 3, project: null, project_title: null, phase: null, task: null, subject: "x", days: null, count: 3, date: "2026-10-22", text: "3 unresolved conflicts in the next two weeks: Oct 22: Austin: double-booked" },
				{ kind: "overdue", severity: 1, project: "PRJ-1", project_title: "Riverwalk Plaza", phase: null, task: null, subject: "Order pumps", days: null, count: 6, date: "2026-10-03", text: "Riverwalk Plaza: 6 tasks overdue (the oldest was due Oct 3: Order pumps)" },
			],
			more_exceptions: 0,
			counts: { behind: 1, over: 1, conflicts: 3, overdue: 6, overdue_jobs: 1, lines: 4, jobs: 3, baselined: 1, drafts: 1 },
			conflicts_ok: true,
			behind_days: 2,
			projects: [
				{ name: "PRJ-1", title: "Riverwalk Plaza", pm: "Clegg", schedule: { status: "Published" } },
				{ name: "PRJ-2", title: "Tanner Park", pm: "Nik", schedule: { status: "Draft" } },
				{ name: "PRJ-3", title: "Sanctuary", pm: "Clegg", schedule: { status: "Published" } },
			],
			drafts: { count: 3, projects: { "PRJ-2": { count: 3, shared: 1 } } },
		},
		extra
	);
}

function planner(extra, options) {
	const context = (options && options.context) || ctx;
	const pp = Object.create(context.ProjectPlanner.prototype);
	Object.assign(pp, {
		view: "overview",
		anchor: "2026-10-21",
		project: "",
		pm: "",
		group: "",
		all_tasks: "",
		over_only: false,
		draft_on: false,
		request: 0,
		modified: {},
		task_days: {},
		needs_crew: new Set(),
		ppo: null,
		ppg: null,
		p6c: null,
		loads: [],
		renders: 0,
		$grid_wrap: jq(makeGrid()),
		$panel: jq(new FakeEl("panel")),
		$trays: jq(new FakeEl("trays")),
		$hint: jq(new FakeEl("hint")),
		$title: jq(new FakeEl("title")),
		$body: Object.assign(jq(new FakeEl("body")), { addClass() {}, removeClass() {} }),
	});
	pp.data = answer();
	pp.index();
	// Pieces from other blocks, recorded and not drawn.
	pp.render_phase3b = () => {};
	pp.render_phase6a = () => {};
	pp.tb_render = () => {};
	pp.ppl_sync_tabs = () => {};
	pp.take_title_focus = () => {};
	pp.fill_filters = () => {};
	pp.write_filter_url = () => {};
	pp.ppg_open = (project) => (pp.opened = (pp.opened || []).concat({ gantt: project }));
	pp.go = (view, date) => (pp.opened = (pp.opened || []).concat({ go: view, date }));
	pp.as4_confirm_publish = (entry) => (pp.opened = (pp.opened || []).concat({ publish: entry }));
	pp.as4_project_title = (project) => (pp.by_project[project] || {}).title || project;
	Object.assign(pp, extra || {});
	return pp;
}

const widgetRows = (pp) => {
	const state = pp.ppo_state();
	pp.ppo_mount({ innerHTML: "" });
	return { widget: ctx.__state.mounts[ctx.__state.mounts.length - 1], state };
};

// ------------------------------------------------------------------ 1. pure

test("PPO_PURE: the window is whole calendar months from the month holding the day", () => {
	check("six months from October", P.months_window("2026-10-21", 6), { start: "2026-10-01", end: "2027-03-31" });
	check("across a year, and a leap February", [P.months_window("2026-12-31", 3), P.months_window("2027-12-10", 3)], [{ start: "2026-12-01", end: "2027-02-28" }, { start: "2027-12-01", end: "2028-02-29" }]);
	check("3, 6, 9 and 12; anything else is 6", ["3", 9, 12, 5, "x", null, 0].map((m) => P.months(m)), [3, 9, 12, 6, 6, 6, 6]);
	check("the first of the month", [P.month_start("2026-10-21"), P.month_start("2026-10-01")], ["2026-10-01", "2026-10-01"]);
	check("a step is a month, over a year end", [P.step("2026-10-21", 1), P.step("2026-10-21", -1), P.step("2026-12-15", 1), P.step("2027-01-02", -1)], ["2026-11-01", "2026-09-01", "2027-01-01", "2026-12-01"]);
});

test("PPO_PURE: the address, the title, the labels", () => {
	check("six months is not written", [P.overview_address("2026-10-21", 6), P.overview_address("2026-10-21", 12), P.overview_address("2026-10-21", 5)], [["overview", "2026-10-21"], ["overview", "2026-10-21", "12"], ["overview", "2026-10-21"]]);
	check("titles", [P.heading({ start: "2026-10-01", end: "2027-03-31" }), P.heading({ start: "2026-10-01", end: "2026-12-31" }), P.heading({ start: "2026-10-01", end: "2026-10-31" })], ["Oct 2026 – Mar 2027", "Oct – Dec 2026", "Oct 2026"]);
	check("days and spans", [P.day_label("2026-10-21"), P.day_label("nope"), P.span_label("2026-10-21", "2026-11-04"), P.span_label("2026-10-21", "2026-10-21"), P.span_label("2026-10-21", null)], ["Oct 21", "", "Oct 21 – Nov 4", "Oct 21", "Oct 21"]);
});

test("PPO_PURE: a span clipped to the window", () => {
	const f = "2026-10-01";
	const l = "2027-03-31";
	check("inside", P.clip("2026-10-05", "2026-10-09", f, l), { start: "2026-10-05", end: "2026-10-09" });
	check("from before and past the end", [P.clip("2026-09-01", "2026-10-09", f, l), P.clip("2027-03-20", "2027-05-01", f, l)], [{ start: "2026-10-01", end: "2026-10-09" }, { start: "2027-03-20", end: "2027-03-31" }]);
	check("a missing or early end is one day", [P.clip("2026-10-05", null, f, l), P.clip("2026-10-05", "2026-10-01", f, l)], [{ start: "2026-10-05", end: "2026-10-05" }, { start: "2026-10-05", end: "2026-10-05" }]);
	check("outside, or undated", [P.clip("2027-04-01", null, f, l), P.clip("2026-08-01", "2026-09-30", f, l), P.clip(null, null, f, l)], [null, null, null]);
});

test("PPO_PURE: phase colours by hundreds, and where a line goes", () => {
	check("Phase 100 to 700, anything else neutral", [100, 300, 700, 800, null, "x", 50].map((n) => P.phase_class(n)), ["ppo-ph-1", "ppo-ph-3", "ppo-ph-7", "ppo-ph-0", "ppo-ph-0", "ppo-ph-0", "ppo-ph-0"]);
	const a = answer();
	check("a job's line opens its Gantt, a conflict the Crew board on its day", [P.target(a.exceptions[0]), P.target(a.exceptions[2]), P.target({ kind: "conflicts" }), P.target({ kind: "behind" }), P.target(null)], [
		{ view: "gantt", project: "PRJ-1" },
		{ view: "crew", date: "2026-10-22" },
		{ view: "crew", date: null },
		null,
		null,
	]);
	const seen = (project) => project === "PRJ-1";
	check("the filters narrow a job's lines; the conflicts are the week's", P.lines(a, seen).map((l) => l.kind), ["behind", "conflicts", "overdue"]);
	check("no filter keeps all", P.lines(a, null).length, 4);
});

// ------------------------------------------------------------------ 2. the chart's rows

const say = (pp) => pp.ppo_say();
const rowsFor = (a, visible, expanded) => P.chart_rows(a, visible, expanded, say(planner()));
const byId = (rows, id) => rows.tasks.find((t) => t.id === id);

test("Rows: a bar per job for its whole span, its phases under it, milestones as diamonds", () => {
	const rows = rowsFor(answer(), null, false);
	check("job, then its phases, then its milestone, in the server's order", rows.tasks.filter((t) => t.parent === 0 || String(t.parent).startsWith("job:PRJ-1")).map((t) => t.id), [
		"job:PRJ-1",
		"phase:PRJ-1:G100",
		"phase:PRJ-1:G300",
		"phase:PRJ-1:other",
		"ms:M1",
		"job:PRJ-2",
		"job:PRJ-3",
	].filter((id) => !id.startsWith("job:PRJ-2") && !id.startsWith("job:PRJ-3")).concat(["job:PRJ-2", "job:PRJ-3"]));
	const j = byId(rows, "job:PRJ-1");
	check("the job's own days (clipped to the window), as the widget's wire format", [j.start_date, j.end_date, j.parent, j.type], ["2026-10-01 00:00", "2026-12-16 00:00", 0, "task"]);
	check("read-only, its project, PM, dates and progress", [j.ee_readonly, j.ee_project, j.ee_pm, j.ee_dates, j.progress], [true, "PRJ-1", "Clegg", "Sep 28 – Dec 15", 0.4]);
	check("the job's baseline ghost", [j.ee_baseline_start, j.ee_baseline_end, j.ee_base], ["2026-10-05", "2026-11-20", "Baseline Oct 5 – Nov 20"]);
	const p = byId(rows, "phase:PRJ-1:G300");
	check("a phase is a bar under its job, with its own ghost", [p.parent, p.start_date, p.end_date, p.ee_baseline_start, p.ee_baseline_end], ["job:PRJ-1", "2026-10-12 00:00", "2026-12-01 00:00", "2026-10-12", "2026-11-20"]);
	check("coloured by its number, and slipped", p.ee_class, "ppo-bar ppo-ph-3 ppo-slipped");
	check("how far it slipped, and how many tasks are done", [p.ee_slip, p.ee_note, p.progress], ["+6d", "1 of 5 tasks done · 6 working days behind its baseline", 0.2]);
	const finished = byId(rows, "phase:PRJ-1:G100");
	check("a finished phase is faded and has no slip", [finished.ee_class, finished.ee_slip], ["ppo-bar ppo-ph-1 ppo-done", ""]);
	const other = byId(rows, "phase:PRJ-1:other");
	check("Other work is neutral, with no ghost", [other.ee_class, other.ee_baseline_start], ["ppo-bar ppo-ph-0", undefined]);
	const m = byId(rows, "ms:M1");
	check("a milestone is a diamond with its day", [m.type, m.start_date, m.parent, m.ee_dates, m.ee_class], ["milestone", "2026-12-15 00:00", "job:PRJ-1", "Dec 15", "ppo-milestone"]);
	// A tap opens the record (2026-10-27): a phase its group Task, a milestone its Task; a job and Other work have none.
	check("the Task a row opens", [j.ee_task, p.ee_task, other.ee_task, m.ee_task], [undefined, "G300", "", "M1"]);
	check("the job's worst slip is in its Behind column", [j.ee_slip, j.ee_note], ["+6d", "6 working days behind its baseline"]);
});

test("Rows: a Draft schedule is hatched and says so; the project's own dates say so too", () => {
	const rows = rowsFor(answer(), null, false);
	const draft = byId(rows, "job:PRJ-2");
	check("the draft job: a mark, a class, a note", [draft.text, draft.ee_class, draft.ee_note], ["◐ Tanner Park", "ppo-job ppo-draft", "Draft schedule"]);
	check("its phases are hatched too", byId(rows, "phase:PRJ-2:G200").ee_class, "ppo-bar ppo-ph-2 ppo-draft");
	const estimate = byId(rows, "job:PRJ-3");
	check("no dated tasks: the project's own dates, marked", [estimate.ee_class, estimate.ee_note], ["ppo-job ppo-estimate", "The project's own dates: no dated tasks yet"]);
	check("and no phase rows", rows.tasks.filter((t) => t.parent === "job:PRJ-3"), []);
});

test("Rows: clipped to the window, the filters apply, and expanding opens every job", () => {
	const a = answer({ start: "2026-11-01", end: "2026-11-30" });
	const rows = P.chart_rows(a, null, false, say(planner()));
	const j = byId(rows, "job:PRJ-1");
	check("the job is cut at the window's edges", [j.start_date, j.end_date], ["2026-11-01 00:00", "2026-12-01 00:00"]);
	check("a phase wholly outside is left out", byId(rows, "phase:PRJ-1:G100"), undefined);
	check("a milestone outside is left out", byId(rows, "ms:M1"), undefined);
	check("the ghost is cut as well", [byId(rows, "phase:PRJ-1:G300").ee_baseline_start, byId(rows, "phase:PRJ-1:G300").ee_baseline_end], ["2026-11-01", "2026-11-20"]);
	const none = P.chart_rows(answer({ start: "2028-01-01", end: "2028-06-30" }), null, false, say(planner()));
	check("a window with nothing has no rows", none.tasks, []);
	const only = P.chart_rows(answer(), (j2) => j2.pm === "Nik", false, say(planner()));
	check("the PM filter", only.tasks.filter((t) => t.parent === 0).map((t) => t.id), ["job:PRJ-2"]);
	check("collapsed by default, open when expanded", [rowsFor(answer(), null, false).tasks[0].open, rowsFor(answer(), null, true).tasks[0].open], [false, true]);
	check("nothing can be written from here", [rows.links, rows.meta], [[], { can_write: { Task: false } }]);
});

test("Rows: the tooltip escapes what is typed; the row text is the widget's own", () => {
	const evil = '<img src=x onerror="alert(1)">';
	const rows = P.chart_rows(answer({ jobs: [job({ title: evil, phases: [{ key: "G1", name: "G1", subject: evil, number: 100, start: "2026-10-05", end: "2026-10-09", baseline: null, slip_days: null, tasks: 1, done: 0, open: 1, progress: 0 }], milestones: [] })] }), null, false, say(planner()));
	check("text is left to the widget (as the Gantt tab's is)", rows.tasks.map((t) => t.text), [evil, evil]);
	const tip = P.tooltip(rows.tasks[1]);
	check("the tooltip escapes it", [tip.includes("<img"), tip.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;")], [false, true]);
	check("and carries the dates and the note", P.tooltip({ text: "A", ee_dates: "Oct 5 – Oct 9", ee_note: "1 of 1 tasks done" }), "<b>A</b><br/>Oct 5 – Oct 9<br/>1 of 1 tasks done");
});

// ------------------------------------------------------------------ 3. the three parts

test("The header: the title, the months picker, Expand all, Print weekly summary", () => {
	const pp = planner();
	const html = pp.ppo_head_html();
	check("title", html.includes('<span class="ppo-head-title">Overview · Oct 2026 – Mar 2027</span>'), true);
	check("the picker, six pressed", [3, 6, 9, 12].map((n) => html.includes(`data-ppo-months="${n}" aria-pressed="${n === 6 ? "true" : "false"}"`)), [true, true, true, true]);
	check("Expand all, then Collapse all", [html.includes('data-ppo-expand="1">Expand all'), (pp.ppo_state().expanded = true) && pp.ppo_head_html().includes('data-ppo-expand="0">Collapse all')], [true, true]);
	check("Print weekly summary", html.includes("data-ppo-print"), true);
});

test("The header: Draft schedules with Publish…, only for someone who can change tasks, narrowed by the filters", () => {
	const pp = planner();
	const html = pp.ppo_head_html();
	check("the draft job, hatched, with Publish…", [html.includes("◐ Tanner Park"), html.includes('data-ppo-publish="PRJ-2"')], [true, true]);
	check("the published jobs are not in it", html.includes("Riverwalk Plaza</span>"), false);
	pp.data.can_edit = false;
	check("no Publish… without the right to change tasks", pp.ppo_head_html().includes("data-ppo-publish"), false);
	pp.data.can_edit = true;
	pp.pm = "Clegg";
	check("a PM filter that leaves no draft shows no line", pp.ppo_head_html().includes("Draft schedules"), false);
});

test("The header: draft names are escaped and long lists are cut", () => {
	const jobs = Array.from({ length: 11 }, (_x, i) => job({ name: `D${i}`, title: i === 0 ? "<b>x</b>" : `Draft ${i}`, draft: true }));
	const pp = planner({ data: answer({ jobs }) });
	pp.index();
	const html = pp.ppo_head_html();
	check("escaped", [html.includes("<b>x</b>"), html.includes("&lt;b&gt;x&lt;/b&gt;")], [false, true]);
	check("eight named, the rest counted", [(html.match(/data-ppo-publish=/g) || []).length, html.includes("and 3 more")], [8, true]);
});

test("Needs attention: a button per line, worst first, with the kind read aloud", () => {
	const pp = planner();
	const html = pp.ppo_needs_html();
	check("the count", html.includes("<h5>Needs attention (4)</h5>"), true);
	check("one button per line, in order", [...html.matchAll(/data-kind="(\w+)" data-ppo-line="(\d+)"/g)].map((m) => [m[1], m[2]]), [["behind", "0"], ["over", "1"], ["conflicts", "2"], ["overdue", "3"]]);
	check("the kind is read aloud and the icon is decoration", [html.includes('<span class="pp-sr">Behind baseline: </span>'), html.includes('<span class="ppo-icon" aria-hidden="true">▲</span>')], [true, true]);
	check("the sentence", html.includes("Riverwalk Plaza: Phase 300 — Rough-in is 6 working days behind its baseline"), true);
	check("nothing disabled: every line has a source", html.includes("disabled"), false);
});

test("Needs attention: only a few lines until Show all; a line with no source cannot be pressed", () => {
	const lines = Array.from({ length: 9 }, (_x, i) => ({ kind: "overdue", project: `PRJ-${i}`, text: `Job ${i}: 1 task overdue` }));
	lines.push({ kind: "behind", project: null, text: "an orphan line" });
	const pp = planner({ data: answer({ exceptions: lines, more_exceptions: 5 }) });
	let html = pp.ppo_needs_html();
	check("six shown, the toggle offers all ten", [(html.match(/data-ppo-line=/g) || []).length, html.includes('data-ppo-lines aria-expanded="false">Show all 10')], [6, true]);
	pp.ppo_state().lines_all = true;
	html = pp.ppo_needs_html();
	check("all shown, fewer offered, and the rest not listed counted", [(html.match(/data-ppo-line=/g) || []).length, html.includes("Show fewer"), html.includes("and 5 more not listed"), html.includes("Needs attention (15)")], [10, true, true, true]);
	check("the orphan is disabled", /data-ppo-line="9" disabled/.test(html), true);
});

test("Needs attention: nothing wrong is one line; the filters narrow a job's lines; record text is escaped", () => {
	const pp = planner({ data: answer({ exceptions: [] }) });
	check("all clear", pp.ppo_needs_html().includes("Nothing needs a manager&#39;s attention."), true);
	check("not a list", pp.ppo_needs_html().includes("<ul"), false);
	const filtered = planner();
	filtered.project = "PRJ-2";
	check("one job's lines, and the week's conflicts", [...filtered.ppo_needs_html().matchAll(/data-kind="(\w+)"/g)].map((m) => m[1]), ["over", "conflicts"]);
	const evil = planner({ data: answer({ exceptions: [{ kind: "overdue", project: "PRJ-1", text: "<script>alert(1)</script>: 1 task overdue" }] }) });
	check("escaped", [evil.ppo_needs_html().includes("<script>"), evil.ppo_needs_html().includes("&lt;script&gt;")], [false, true]);
	const unread = planner({ data: answer({ conflicts_ok: false }) });
	check("unreadable conflicts are said", unread.ppo_needs_html().includes("The conflicts could not be read just now."), true);
	const none = planner({ data: answer({ exceptions: [], conflicts_ok: false }) });
	check("even when nothing else is wrong", none.ppo_needs_html().includes("could not be read"), true);
});

test("The foot: jobs with no dates, jobs outside, how many have a baseline, and what the counts leave out", () => {
	const html = planner().ppo_foot_html();
	check("no dates", html.includes("1 job has no dates yet: Myers-Roy."), true);
	check("outside the months", html.includes("2 jobs have nothing in these months."), true);
	check("the baseline hint", html.includes("1 of 3 jobs have a baseline. Set one from a job&#39;s Gantt to see how far each phase has slipped."), true);
	check("the rule for overdue and the display rule", html.includes("Overdue counts a task whose last day has passed; the Overdue status is not used."), true);
	const quiet = planner({ data: answer({ no_dates: [], outside: 0, counts: { jobs: 3, baselined: 3 } }) }).ppo_foot_html();
	check("a quiet foot is the rule alone", (quiet.match(/<p class="ppo-note">/g) || []).length, 1);
	check("a read that hit the task cap says so", [quiet.includes("Only part of the task list"), planner({ data: answer({ tasks_truncated: true }) }).ppo_foot_html().includes("Only part of the task list could be read, so some jobs may show less than they have.")], [false, true]);
	const names = planner({ data: answer({ no_dates: Array.from({ length: 10 }, (_x, i) => ({ name: `N${i}`, title: i ? `Job ${i}` : "<i>x</i>", pm: "Nik" })) }) }).ppo_foot_html();
	check("ten with no dates: eight named, escaped, the rest counted", [names.includes("10 jobs have no dates yet:"), names.includes("&lt;i&gt;x&lt;/i&gt;"), names.includes("and 2 more.")], [true, true, true]);
	const filtered = planner();
	filtered.pm = "Clegg";
	check("a PM filter narrows the no-date list", filtered.ppo_foot_html().includes("have no dates yet"), false);
});

// ------------------------------------------------------------------ 4. the widget

test("The chart: read-only, with the baseline layer, the month zoom and an export that has no CSV or Excel", () => {
	const pp = planner();
	const { widget } = widgetRows(pp);
	const c = widget.config;
	check("nothing to drag or link", [c.editable, "on_write" in c, "on_link" in c, "on_link_dblclick" in c], [{}, false, false, false]);
	check("a right-click is a menu of records and the Gantt", typeof c.on_task_context, "function");
	check("baseline, today, tooltip, month zoom", [c.baseline, c.today, c.tooltip, c.zoom], [true, true, true, "month"]);
	check("the export draws from the rows: print, PNG, SVG", c.toolbar.export.formats, ["print", "png", "svg"]);
	check("unscheduled rows are not shown", c.gantt.show_unscheduled, false);
	check("columns: the tree, PM, Behind", c.columns.map((col) => col.name), ["text", "ee_pm", "ee_slip"]);
	check("the column templates escape what they print", [c.columns[1].template({ ee_pm: "<b>Nik</b>" }), c.columns[2].template({ ee_slip: "+3d" })], ["&lt;b&gt;Nik&lt;/b&gt;", "+3d"]);
	check("the bar class and the tooltip are the page's", [c.templates.task_class(null, null, { ee_class: "ppo-job" }), c.templates.tooltip_text(null, null, { text: "A <i>", ee_dates: "Oct 5" })], ["ppo-job", "<b>A &lt;i&gt;</b><br/>Oct 5"]);
	check("a row class for jobs", [c.templates.grid_row_class(null, null, { ee_class: "ppo-job ppo-draft" }), c.templates.grid_row_class(null, null, { ee_class: "ppo-bar" })], ["ppo-job-row", ""]);
});

test("The chart: rows come from the live answer and the live filters, and a click opens the row's record", async () => {
	const pp = planner();
	const { widget } = widgetRows(pp);
	const rows = await widget.config.fetch();
	check("every job to begin with", rows.tasks.filter((t) => t.parent === 0).map((t) => t.id), ["job:PRJ-1", "job:PRJ-2", "job:PRJ-3"]);
	pp.pm = "Nik";
	check("the PM filter is read at fetch time", (await widget.config.fetch()).tasks.filter((t) => t.parent === 0).map((t) => t.id), ["job:PRJ-2"]);
	pp.pm = "";
	pp.ppo_state().expanded = true;
	check("Expand all is read at fetch time", (await widget.config.fetch()).tasks[0].open, true);
	widget.config.on_task_click("phase:PRJ-1:G300", { ee_project: "PRJ-1" });
	widget.config.on_task_click("x", null);
	widget.config.on_task_click("y", { ee_project: "" });
	check("before the kit has loaded: a row opens its job's Gantt, as before", pp.opened, [{ gantt: "PRJ-1" }]);
	// With the kit: a phase or a milestone opens its Task, a job (and Other work) the Project (Nik, 2026-10-27).
	pp.opened = [];
	const records = [];
	pp.p6a = { kit: { records: { open_item: (kind, item) => records.push([kind, item.name || item.project]) > 0 } } };
	widget.config.on_task_click("phase:PRJ-1:G300", { ee_project: "PRJ-1", ee_task: "G300" });
	widget.config.on_task_click("ms:M1", { ee_project: "PRJ-1", ee_task: "M1" });
	widget.config.on_task_click("phase:PRJ-1:other", { ee_project: "PRJ-1", ee_task: "" });
	widget.config.on_task_click("job:PRJ-2", { ee_project: "PRJ-2" });
	check("each row's record", records, [["task", "G300"], ["task", "M1"], ["project", "PRJ-1"], ["project", "PRJ-2"]]);
	check("and never the Gantt", pp.opened, []);
	// A right-click: the row's Task, the Project, Project at a glance and its Gantt.
	let menu = null;
	pp.p6a.kit.menu = (opts) => (menu = opts);
	let prevented = false;
	widget.config.on_task_context("ms:M1", { ee_project: "PRJ-1", ee_task: "M1", text: "Hand-off" }, { clientX: 5, clientY: 6, preventDefault: () => (prevented = true) });
	check("the row's menu", [menu.items.map((item) => item.label), menu.title, menu.anchor, prevented], [["Open task", "Open project", "Project at a glance", "Open in Gantt"], "Hand-off", { x: 5, y: 6 }, true]);
	menu.items[3].on_click();
	check("Open in Gantt", pp.opened, [{ gantt: "PRJ-1" }]);
});

test("The chart: a missing widget library says so instead of throwing", () => {
	const pp = planner();
	const chart = { innerHTML: "" };
	const saved = ctx.window.erpnext_enhancements;
	ctx.window.erpnext_enhancements = undefined;
	pp.ppo_mount(chart);
	ctx.window.erpnext_enhancements = saved;
	check("a sentence in the chart's place", chart.innerHTML.includes("The Gantt chart could not load. Refresh the page."), true);
});

// ------------------------------------------------------------------ 5. the whole render and the controls

function rendered(extra) {
	ctx.__state.mounts.length = 0;
	const pp = planner(extra);
	pp.ppo_render();
	const root = pp.ppo.$root[0];
	return { pp, root, widget: ctx.__state.mounts[ctx.__state.mounts.length - 1] };
}

test("Render: the root is built once with the four parts, the chart mounted once", () => {
	const { pp, root, widget } = rendered();
	check("head, needs, foot drawn", [root.kids["ppo-head"].innerHTML.includes("Print weekly summary"), root.kids["ppo-needs"].innerHTML.includes("Needs attention (4)"), root.kids["ppo-foot"].innerHTML.includes("1 job has no dates yet")], [true, true, true]);
	check("the chart is labelled for a screen reader", root.kids["ppo-chart"].attrs["aria-label"], "Portfolio timeline");
	check("mounted once", ctx.__state.mounts.length, 1);
	check("the Resources panel and the trays are not the Overview's", [pp.$panel.el.hidden, pp.$trays.el._html], [true, ""]);
	check("a hint that says what to do", pp.$hint.el.textValue.includes("Click a job or a phase to open that job's Gantt."), true);
	pp.ppo_render();
	check("drawn again: the same root, the same widget, not refreshed", [pp.ppo.$root[0] === root, ctx.__state.mounts.length, widget.refreshed], [true, 1, 0]);
});

test("Render: a new answer or a new filter refreshes the chart; it is never mounted twice", () => {
	const { pp, widget } = rendered();
	pp.data = answer({ jobs: [] });
	pp.ppo_render();
	check("a new answer", [ctx.__state.mounts.length, widget.refreshed], [1, 1]);
	pp.pm = "Clegg";
	pp.ppo_render();
	check("a new filter", widget.refreshed, 2);
	pp.ppo_render();
	check("nothing changed", widget.refreshed, 2);
	pp.ppo_state().expanded = true;
	pp.ppo_render();
	check("Expand all", widget.refreshed, 3);
});

test("Render: after the chart is taken down it is mounted again", () => {
	const { pp } = rendered();
	pp.ppo_destroy();
	check("destroyed, state cleared", [ctx.__state.mounts[0].destroyed, pp.ppo.widget], [true, null]);
	pp.ppo_render();
	check("a root that is not in the page is rebuilt and the chart mounted again", ctx.__state.mounts.length, 2);
});

test("Render: another tab's answer is never drawn, and a failed load is a note with Try again", () => {
	ctx.__state.mounts.length = 0;
	const stale = planner({ data: { tasks: [], unscheduled: [], lookahead: {} } });
	stale.ppo_render();
	check("no root for an answer without jobs", [stale.ppo.$root, ctx.__state.mounts.length], [null, 0]);
	ctx.__state.mounts.length = 0;
	const failed = planner();
	failed.ppo_state().trouble = true;
	failed.ppo_render();
	const chart = failed.ppo.$root[0].kids["ppo-chart"];
	check("a note, Try again, and no chart", [chart.innerHTML.includes("The Overview could not load."), chart.innerHTML.includes("data-ppo-retry"), ctx.__state.mounts.length], [true, true, 0]);
	failed.load = () => (failed.loads = failed.loads.concat("again"));
	chart.querySelector = (sel) => (sel === "[data-ppo-retry]" ? { addEventListener: (_t, fn) => fn() } : null);
	failed.ppo_render();
	check("Try again loads", failed.loads, ["again"]);
});

test("Controls: a line opens its source; the months picker, Expand all and Publish… do their one thing", () => {
	const { pp, root } = rendered();
	const lines = root.last("[data-ppo-line]");
	lines[0].click();
	lines[2].click();
	lines[3].click();
	check("behind: the Gantt; conflicts: the Crew board on that day; overdue: the Gantt", pp.opened, [{ gantt: "PRJ-1" }, { go: "crew", date: "2026-10-22" }, { gantt: "PRJ-1" }]);
	pp.opened = [];
	pp.ppo_pick_months = (n) => (pp.opened = pp.opened.concat({ months: n }));
	root.last("[data-ppo-months]").find((el) => el.getAttribute("data-ppo-months") === "12").click();
	check("the picker", pp.opened, [{ months: "12" }]);
	let renders = 0;
	pp.render = () => (renders += 1);
	root.last("[data-ppo-expand]")[0].click();
	check("Expand all is a page state and a redraw, never a route", [pp.ppo.expanded, renders, pp.opened.length], [true, 1, 1]);
		pp.opened = [];
	root.last("[data-ppo-publish]")[0].click();
	check("Publish… hands over P4's own confirmation with the drafts' counts", pp.opened, [{ publish: { project: "PRJ-2", title: "Tanner Park", count: 3, shared: 1, draft_schedule: true } }]);
});

test("Controls: a line from the filtered list is the filtered one", () => {
	const { pp, root } = rendered();
	pp.project = "PRJ-2";
	pp.ppo_render();
	const root2 = pp.ppo.$root[0];
	root2.last("[data-ppo-line]")[0].click();
	check("the first line now is Tanner Park's overrun", pp.opened, [{ gantt: "PRJ-2" }]);
});

test("Print weekly summary: a window opens on the click, the server's page is printed there", async () => {
	const context = load();
	const st = context.__state;
	st.answers = { weekly_summary_html: "<!doctype html><title>Weekly</title><body>page</body>" };
	const pp = planner({}, { context });
	pp.ppo_print_summary();
	check("opened on the click, before the request", [st.opened.length, st.popup.written[0].includes("Preparing the weekly summary…")], [1, true]);
	await settle();
	check("asked for the summary, no arguments", st.calls.map((c) => [c.method, c.args]), [["erpnext_enhancements.api.planner_overview.weekly_summary_html", {}]]);
	check("the page replaced the note, then printed once", [st.popup.written, st.popup.printed, st.popup.focused], [["<!doctype html><title>Weekly</title><body>page</body>"], 1, 1]);
	check("the menu item does the same", pp.ppo_print_items().map((i) => i.label), ["Weekly summary"]);
});

test("Print weekly summary: a blocked pop-up is said, an empty or failed answer closes the window", async () => {
	let context = load();
	context.__state.popup = null;
	planner({}, { context }).ppo_print_summary();
	check("a blocked pop-up", [context.__state.msgprints, context.__state.calls.length], [["Allow pop-ups for this site to print the weekly summary."], 0]);
	context = load();
	context.__state.answers = { weekly_summary_html: "" };
	planner({}, { context }).ppo_print_summary();
	await settle();
	check("an empty page closes the window", context.__state.popup.closed, true);
	context = load();
	context.__state.answers = { weekly_summary_html: new Error("500") };
	planner({}, { context }).ppo_print_summary();
	await settle();
	check("a failed request closes it", [context.__state.popup.closed, context.__state.popup.printed], [true, 0]);
	context = load();
	context.__state.answers = { weekly_summary_html: "<p>x</p>" };
	const pp = planner({}, { context });
	pp.ppo_print_summary();
	context.__state.popup.closed = true;
	await settle();
	check("a window closed meanwhile is left alone", context.__state.popup.printed, 0);
});

test("Landing: only the Executive Team's desktop first visit opens the Overview", () => {
	const as = (roles, link) => {
		const context = load();
		context.__state.roles = roles;
		return planner({}, { context }).ppo_first_tab(link);
	};
	check("an executive", as(["Executive Team"], false), "overview");
	check("not an executive", as(["Projects User"], false), "lookahead");
	check("a link that names a filter", as(["Executive Team"], true), "lookahead");
});

test("Load: the window, the draft flag and the months go to get_overview; a failure is a state, not a stale screen", async () => {
	const context = load();
	const st = context.__state;
	st.answers = { get_overview: answer() };
	const pp = planner({ view: "overview", anchor: "2026-10-21" }, { context });
	pp.ppo_state().months = 9;
	pp.render = () => (pp.renders += 1);
	pp.ppo_load();
	await settle();
	check("the call", st.calls.map((c) => [c.method.split(".").pop(), c.args]), [["get_overview", { start: "2026-10-21", months: 9, draft: 1 }]]);
	check("the answer is the page's data, indexed", [pp.data.jobs.length, Object.keys(pp.by_project).sort()], [3, ["PRJ-1", "PRJ-2", "PRJ-3"]]);
	check("drawn once", pp.renders, 1);
	st.answers = { get_overview: new Error("timeout") };
	pp.ppo_load();
	await settle();
	check("a failed request keeps the projects for the filters and draws the note", [pp.ppo.trouble, pp.data.jobs, Object.keys(pp.by_project).length, pp.renders], [true, [], 3, 2]);
	st.answers = { get_overview: answer() };
	pp.ppo_load();
	await settle();
	check("a good answer clears it", pp.ppo.trouble, false);
});

test("Load: an older answer never draws over a newer one", async () => {
	const context = load();
	const st = context.__state;
	const waiting = [];
	context.frappe.call = (opts) => new Promise((resolve) => waiting.push({ opts, resolve }));
	const pp = planner({}, { context });
	pp.render = () => (pp.renders += 1);
	pp.ppo_load();
	pp.ppo_load();
	waiting[1].resolve({ message: answer({ months: 3 }) });
	await settle();
	waiting[0].resolve({ message: answer({ months: 12 }) });
	await settle();
	check("the newer answer stands", [pp.data.months, pp.renders], [3, 1]);
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
