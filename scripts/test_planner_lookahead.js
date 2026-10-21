#!/usr/bin/env node
/**
 * P5 of the planner UX redesign: the Project Planner's Lookahead tab (docs/planner-ux-redesign.md
 * §4.2), run under node against the REAL page script.
 *
 *   1. PPL_PURE: the window (whole weeks from the site's first weekday), where a span sits in it (and
 *      whether it runs past an end), a collapsed row's runs, a task's size in working days, the slip
 *      past a baseline, the address of each screen and the tab a view is under.
 *   2. The board, drawn from a get_lookahead-shaped answer on an instance of the page's own class:
 *      project → phase → task rows in the server's order; each bar a planner card (.pp-card[data-task])
 *      across its days, with the crew's initials, its size in days, the baseline ghost and slip tail,
 *      6B's grips only where the task starts or ends on screen and only on a task the planner may move,
 *      a Fountain Move task read-only; the schedule chip (Published and when, Draft with Publish…, No
 *      tasks yet with Build out); the Not scheduled chips that drag onto a day; collapsing, the Needs
 *      crew chip and the Project, PM and Group filters; record text escaped.
 *   3. The phone's list by day, Print this view, the call load() makes, the day a drag is measured
 *      from, Publish… handing over to P4's own confirmation, and a Lookahead day as a drop target.
 *
 * Routing, the redirects of the old /week and /month addresses and Back/Forward run against the
 * router model in scripts/test_planner_url_filters.js.
 *
 * Run: node scripts/test_planner_lookahead.js
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

// ------------------------------------------------------------------ a small moment and the page

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function moment(value) {
	const text = String(value == null ? "" : value).slice(0, 10);
	const valid = /^\d{4}-\d{2}-\d{2}$/.test(text) && !Number.isNaN(Date.parse(`${text}T00:00:00Z`));
	const date = () => new Date(`${m.text}T00:00:00Z`);
	const m = {
		text,
		isValid: () => valid,
		day: () => date().getUTCDay(),
		format(f) {
			const d = date();
			const parts = { ddd: DAYS[d.getUTCDay()], MMM: MONTHS[d.getUTCMonth()], D: String(d.getUTCDate()) };
			if (f === "YYYY-MM-DD") return m.text;
			return f.replace(/ddd|MMM|D/g, (token) => parts[token]);
		},
		// What range_days walks with: whole days only.
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

function load() {
	const frappe = {
		pages: { "project-planner": {} },
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		datetime: { get_today: () => "2026-10-21" },
		utils: {
			escape_html: (v) =>
				String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"),
		},
	};
	const sandbox = {
		frappe,
		window: { localStorage: { getItem: () => null, setItem() {} }, matchMedia: () => ({ matches: false }) },
		document: { elementFromPoint: () => null },
		$: () => ({}),
		__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] != null ? String(args[i]) : "")),
		moment,
		console,
		CSS: { escape: (s) => String(s) },
	};
	const context = vm.createContext(sandbox);
	vm.runInContext(`${fs.readFileSync(PP_JS, "utf-8")}\nthis.ProjectPlanner = ProjectPlanner; this.PPL = PPL; this.PPL_PURE = PPL_PURE;`, context, {
		filename: PP_JS,
	});
	return context;
}

const ctx = load();
const P = ctx.PPL_PURE;

// ------------------------------------------------------------------ a get_lookahead answer

const card = (name, extra) =>
	Object.assign(
		{ name, subject: name, project: "PRJ-1", project_title: "Riverwalk", crew: [], crew_size: 0, short: 0, movable: true, modified: "m", color: null },
		extra
	);

function answer() {
	const tasks = [
		card("T2", { subject: "Set forms", start: "2026-10-15", end: "2026-10-20", tentative: true, depends_on: ["T1"], crew: [{ resource: "RES-1", label: "Kyle Jones" }] }),
		card("T1", {
			subject: "Excavate basin",
			start: "2026-10-19",
			end: "2026-10-22",
			crew: [
				{ resource: "RES-1", label: "Kyle Jones", is_lead: true },
				{ resource: "RES-2", label: "Ana Hill" },
			],
			baseline: { start: "2026-10-19", end: "2026-10-20" },
			span_days: 4,
		}),
		card("T3", { subject: "Pour slab", start: "2026-11-05", end: "2026-11-10", crew: [{ resource: "RES-1", label: "Kyle Jones" }], crew_size: 2, short: 1 }),
		card("T4", {
			subject: '<img src=x onerror="alert(1)">',
			project: "PRJ-2",
			project_title: "Tanner",
			start: "2026-10-27",
			end: "2026-10-27",
			movable: false,
			fountain_move: true,
			slot: ["09:00", "11:00"],
		}),
	];
	const unscheduled = [card("U1", { subject: "Pull wire" }), card("U2", { subject: "Set vault", crew: [{ resource: "RES-1", label: "Kyle Jones" }] })];
	return {
		today: "2026-10-21",
		can_edit: true,
		tasks,
		unscheduled,
		resources: [
			{ name: "RES-1", label: "Kyle Jones", group: "Field" },
			{ name: "RES-2", label: "Ana Hill", group: "PM" },
		],
		projects: [
			{ name: "PRJ-1", title: "Riverwalk", pm: "Clegg", schedule: { status: "Published", published_on: "2026-10-21 09:00:00" } },
			{ name: "PRJ-2", title: "Tanner", pm: "Nik", schedule: { status: "Draft" } },
			{ name: "PRJ-3", title: "Myers-Roy", pm: "Nik", schedule: { status: "Draft" } },
			{ name: "PRJ-5", title: "Aspen", pm: "Nik", schedule: { status: "Draft" } },
		],
		day_notes: { "2026-10-22": [{ note: "Inspection" }] },
		drafts: { projects: { "PRJ-5": { count: 3, shared: 1 } } },
		lookahead: {
			start: "2026-10-18",
			end: "2026-11-07",
			weeks: 3,
			needs_crew: ["U1"],
			projects: [
				{
					name: "PRJ-1",
					title: "Riverwalk",
					pm: "Clegg",
					no_tasks: false,
					fountain_move: false,
					phases: [
						{ name: "P300", subject: "Phase 300 — Rough-in", number: 300, tasks: ["T2", "T1", "T3"], not_scheduled: [] },
						{ name: "P400", subject: "Phase 400 — Mechanical", number: 400, tasks: [], not_scheduled: ["U1", "U2"] },
					],
					not_scheduled: ["U1", "U2"],
				},
				{
					name: "PRJ-2",
					title: "Tanner",
					pm: "Nik",
					no_tasks: false,
					fountain_move: true,
					phases: [{ name: null, subject: "Other work", number: null, tasks: ["T4"], not_scheduled: [] }],
					not_scheduled: [],
				},
				{ name: "PRJ-3", title: "Myers-Roy", pm: "Nik", no_tasks: true, phases: [], not_scheduled: [] },
			],
		},
	};
}

function planner(extra) {
	const pp = Object.create(ctx.ProjectPlanner.prototype);
	Object.assign(pp, {
		view: "lookahead",
		anchor: "2026-10-21",
		project: "",
		pm: "",
		group: "",
		all_tasks: "",
		over_only: false,
		draft_on: false,
		modified: {},
		task_days: {},
		needs_crew: new Set(),
		ppl: null,
		p6c: null,
	});
	pp.data = answer();
	pp.index();
	// Pieces from other blocks that read data this answer does not carry.
	pp.p4_card_chips = () => [];
	pp.p4_tip_lines = () => [];
	pp.p4_card_conflicts = () => [];
	pp.weather_chips = () => [];
	pp.weather_lines = () => [];
	pp.t1_can = () => true;
	pp.draft_args = () => ({ draft: 1 });
	Object.assign(pp, extra || {});
	return pp;
}

const grid = (pp) => pp.ppl_grid_html(pp.data.lookahead.projects.filter((p) => pp.ppl_project_visible(p)), pp.range_days());
const rows = (html) => html.split('<div class="ppl-row').slice(1).map((chunk) => `<div class="ppl-row${chunk}`);
const row_of = (html, name) => rows(html).find((r) => r.includes(`data-ppl-row="${name}"`)) || "";
const bar_of = (html, name) => {
	const r = row_of(html, name);
	const at = r.indexOf('class="pp-card ppl-bar');
	return at < 0 ? "" : r.slice(at);
};
const shown_rows = (html) => rows(html).map((r) => (/data-ppl-row="([^"]+)"/.exec(r) || [])[1]).filter(Boolean);
const project_rows = (html) => rows(html).filter((r) => r.startsWith('<div class="ppl-row ppl-project')).map((r) => (/data-pk-project="([^"]+)"/.exec(r) || [])[1]);

// ------------------------------------------------------------------ 1. pure

test("PPL_PURE: the window is whole weeks from the site's first weekday", () => {
	check("a Wednesday, Sunday first, 3 weeks", P.window("2026-10-21", 0, 3), { start: "2026-10-18", end: "2026-11-07" });
	check("a Sunday is its own first day", P.window("2026-10-18", 0, 3).start, "2026-10-18");
	check("Monday first", P.window("2026-10-21", 1, 3), { start: "2026-10-19", end: "2026-11-08" });
	check("2, 4 and 6 weeks", [2, 4, 6].map((w) => P.window("2026-10-21", 0, w).end), ["2026-10-31", "2026-11-14", "2026-11-28"]);
	check("anything else is 3", ["5", 0, "x", null, "4"].map((w) => P.weeks(w)), [3, 3, 3, 3, 4]);
});

test("PPL_PURE: where a span sits in the window", () => {
	const f = "2026-10-18";
	const l = "2026-11-07";
	check("inside", P.columns("2026-10-19", "2026-10-22", f, l), { from: 2, to: 5, cut_l: false, cut_r: false });
	check("from before the window", P.columns("2026-10-15", "2026-10-20", f, l), { from: 1, to: 3, cut_l: true, cut_r: false });
	check("past its end", P.columns("2026-11-05", "2026-11-10", f, l), { from: 19, to: 21, cut_l: false, cut_r: true });
	check("one day, no end", P.columns("2026-10-21", null, f, l), { from: 4, to: 4, cut_l: false, cut_r: false });
	check("an end before the start is one day", P.columns("2026-10-21", "2026-10-19", f, l), { from: 4, to: 4, cut_l: false, cut_r: false });
	check("outside, or undated", [P.columns("2026-11-08", null, f, l), P.columns("2026-10-01", "2026-10-17", f, l), P.columns(null, null, f, l)], [null, null, null]);
});

test("PPL_PURE: a collapsed row's runs, a task's size, its slip, its address and its tab", () => {
	check(
		"overlapping and touching spans merge; a gap splits",
		P.runs([["2026-10-19", "2026-10-20"], ["2026-10-21", "2026-10-21"], ["2026-10-24", "2026-10-25"], [null, null]], "2026-10-18", "2026-11-07"),
		[
			[2, 4],
			[7, 8],
		]
	);
	check("working days", [P.span_days("2026-10-19", "2026-10-23"), P.span_days("2026-10-23", "2026-10-26"), P.span_days(null)], [5, 2, 0]);
	// P11's duration_text counts the same way: a weekend-only task is 1 day, never 2 or 0.
	check("a weekend is one day, as the calendar says", [P.span_days("2026-10-24", "2026-10-25"), P.span_days("2026-10-24")], [1, 1]);
	const pp11 = Object.create(ctx.ProjectPlanner.prototype);
	check("the same as duration_text", pp11.duration_text({ start: "2026-10-24", end: "2026-10-25", crew: [] }), "1 day");
	check("a slip past the baseline", P.slip({ start: "2026-10-19", end: "2026-10-22", baseline: { start: "2026-10-19", end: "2026-10-20" } }), ["2026-10-21", "2026-10-22"]);
	check("ahead of it, or none", [P.slip({ start: "2026-10-19", end: "2026-10-19", baseline: { end: "2026-10-20" } }), P.slip({ start: "2026-10-19" })], [null, null]);
	check(
		"addresses",
		[P.route_parts("lookahead", "2026-10-21", 3), P.route_parts("lookahead", "2026-10-21", 4), P.route_parts("week", "2026-10-21"), P.route_parts("month", "2026-10-01"), P.route_parts("crew", "2026-10-21")],
		[["lookahead", "2026-10-21"], ["lookahead", "2026-10-21", "4"], ["calendar", "week", "2026-10-21"], ["calendar", "month", "2026-10-01"], ["crew", "2026-10-21"]]
	);
	check("tabs", ["week", "month", "lookahead", "crew"].map((v) => P.tab(v)), ["calendar", "calendar", "lookahead", "crew"]);
});

// ------------------------------------------------------------------ 2. the board

test("The board: projects, then phases, then tasks, in the server's order", () => {
	const pp = planner();
	const html = grid(pp);
	check("project rows", project_rows(html), ["PRJ-1", "PRJ-2", "PRJ-3"]);
	check("task rows", shown_rows(html), ["T2", "T1", "T3", "T4"]);
	check("the phases and Other work", ["Phase 300 — Rough-in", "Phase 400 — Mechanical", "Other work"].map((s) => html.includes(`<span class="ppl-phase-name" title="${s}">${s}</span>`)), [true, true, true]);
	check("21 day headings, a day note marked", [(html.match(/class="ppl-dayhead/g) || []).length, (html.match(/ppl-note-dot/g) || []).length], [21, 1]);
	check("today's heading", /class="ppl-dayhead ppl-today[^"]*" data-pp6a-day="2026-10-21"/.test(html), true);
	check("every task row's days are drop targets", (row_of(html, "T1").match(/class="ppl-day[^"]*" data-date="/g) || []).length, 21);
	const kind = (prefix) => rows(html).filter((r) => r.startsWith(`<div class="ppl-row ${prefix}`));
	check("so are a phase's and a project's", [...kind("ppl-phase"), ...kind("ppl-project\"")].map((r) => (r.match(/class="ppl-day[^"]*" data-date="/g) || []).length), [21, 21, 21, 21, 21]);
	check("a project with no tasks has none", kind("ppl-project ppl-no-tasks").map((r) => r.includes('class="ppl-day')), [false]);
});

test("A bar is the task's card across its days, with initials, size, baseline and slip", () => {
	const html = grid(planner());
	const bar = bar_of(html, "T1");
	check("a planner card, movable", [bar.startsWith('class="pp-card ppl-bar pp-movable"'), bar.includes('data-task="T1"')], [true, true]);
	check("its columns (label first)", /grid-column: 3 \/ 7"/.test(bar), true);
	check("measured from its first day until grabbed elsewhere", bar.includes('data-date="2026-10-19"'), true);
	check("the crew's initials, the lead ringed", [bar.includes(">KJ</span>"), bar.includes(">AH</span>"), bar.includes("pp-badge pp-lead")], [true, true, true]);
	check("its size in working days", bar.includes('<span class="ppl-size">4d</span>'), true);
	check("both grips", [bar.includes('data-p6b-edge="start"'), bar.includes('data-p6b-edge="end"')], [true, true]);
	const row = row_of(html, "T1");
	check("the baseline ghost behind it", /class="ppl-base" style="grid-column: 3 \/ 5"/.test(row), true);
	check("the slip as a tail", /class="ppl-slip" style="grid-column: 5 \/ 7"/.test(row), true);
});

test("Bars that run past the window, a pencil, a task short of crew", () => {
	const html = grid(planner());
	const early = bar_of(html, "T2");
	check("cut at the left, measured from the window's first day", [early.includes("ppl-cut-l"), early.includes('data-date="2026-10-18"')], [true, true]);
	check("no left grip where it does not start on screen", [early.includes('data-p6b-edge="start"'), early.includes('data-p6b-edge="end"')], [false, true]);
	check("a pencil: hatched bar, chip by the name", [early.includes("pp-tentative"), row_of(html, "T2").includes("pp-chip pp-pencil")], [true, true]);
	const late = bar_of(html, "T3");
	check("cut at the right, no right grip", [late.includes("ppl-cut-r"), late.includes('data-p6b-edge="end"'), late.includes('data-p6b-edge="start"')], [true, false, true]);
	check("short of crew", late.includes("Needs 1 more"), true);
	check("the size counts the whole task, not what is on screen", late.includes('<span class="ppl-size">4d</span>'), true);
});

test("A Fountain Move task is read-only, and record text is escaped", () => {
	const html = grid(planner());
	const bar = bar_of(html, "T4");
	check("not movable, no grips", [bar.includes("pp-movable"), bar.includes("p6b-grip")], [false, false]);
	check("the project says where it moves", rows(html).find((r) => r.includes('data-pk-project="PRJ-2"')).includes(">Fountain Move</span>"), true);
	check("no raw markup from a subject", [html.includes("<img"), html.includes("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;")], [false, true]);
	const pp = planner();
	pp.data.can_edit = false;
	check("nobody without write moves a bar", bar_of(grid(pp), "T1").includes("p6b-grip") || bar_of(grid(pp), "T1").includes("pp-movable"), false);
});

test("The project row: its schedule, Publish… through P4, Build out for no tasks", () => {
	const pp = planner();
	let html = grid(pp);
	const label = (h, name) => rows(h).find((r) => r.includes(`data-pk-project="${name}"`)) || "";
	check("published, and when", label(html, "PRJ-1").includes("● Published Oct 21"), true);
	check("the name opens the project's peek", label(html, "PRJ-1").includes('class="ppl-project-name" data-pk-project="PRJ-1"'), true);
	check("its PM", label(html, "PRJ-1").includes("PM Clegg"), true);
	check("no tasks yet, with Build out", [label(html, "PRJ-3").includes("No tasks yet"), label(html, "PRJ-3").includes('data-ppl-build="PRJ-3"')], [true, true]);
	// A Draft schedule offers Publish… (P4's own confirmation) to someone who may write.
	pp.data.lookahead.projects[1].fountain_move = false;
	html = grid(pp);
	check("Draft: the chip and Publish…", [label(html, "PRJ-2").includes("◐ Draft schedule"), label(html, "PRJ-2").includes('data-ppl-publish="PRJ-2"')], [true, true]);
	check("Published: no Publish…", label(html, "PRJ-1").includes("data-ppl-publish"), false);
	const seen = [];
	pp.as4_confirm_publish = (entry) => seen.push(entry);
	pp.as4_project_title = (name) => `${name} title`;
	pp.ppl_publish("PRJ-5");
	check("Publish… hands P4 the project and its drafts", seen, [{ project: "PRJ-5", title: "PRJ-5 title", count: 3, shared: 1, draft_schedule: true }]);
	pp.data.can_edit = false;
	check("not for someone who cannot write", label(grid(pp), "PRJ-2").includes("data-ppl-publish") || label(grid(pp), "PRJ-3").includes("data-ppl-build"), false);
});

test("Not scheduled: chips that drag onto a day, Needs crew on those nobody is on", () => {
	const pp = planner();
	const html = grid(pp);
	const line = rows(html).find((r) => r.startsWith('<div class="ppl-row ppl-undated-row'));
	check("one line for the project, counted", line.includes("Not scheduled (2)"), true);
	check("each a movable planner card with no day", ["U1", "U2"].map((n) => line.includes(`class="pp-card ppl-undated pp-movable" data-task="${n}" data-date=""`)), [true, true]);
	check("each with a place for P4's Draft / New chip", (line.match(/<span class="pp-card-chips">/g) || []).length, 2);
	check("Needs crew only on the one nobody is on", (line.match(/>Needs crew<\/span>/g) || []).length, 1);
	const many = planner();
	many.data.unscheduled = Array.from({ length: 15 }, (_v, i) => card(`X${i}`));
	many.index();
	many.data.lookahead.projects[0].phases[1].not_scheduled = many.data.unscheduled.map((c) => c.name);
	const long = rows(grid(many)).find((r) => r.startsWith('<div class="ppl-row ppl-undated-row'));
	check("twelve, then +N more", [(long.match(/ppl-undated pp-movable/g) || []).length, long.includes('data-ppl-more="PRJ-1"'), long.includes("+3 more")], [12, true, true]);
	many.ppl_state().shown_all.add("PRJ-1");
	check("all once asked", (grid(many).match(/ppl-undated pp-movable/g) || []).length, 15);
});

test("A project with only undated work still has days to drop on (the form link on a new build-out)", () => {
	const pp = planner({ project: "PRJ-1" });
	pp.data.tasks = [];
	pp.index();
	pp.data.lookahead.projects[0].phases[0].tasks = [];
	const html = grid(pp);
	check("no task rows", shown_rows(html), []);
	const targets = (html.match(/class="ppl-day[^"]*" data-date="/g) || []).length;
	check("the project's and its phase's days take a drop", targets, 42);
	pp.ppl_state().collapsed.add("p:PRJ-1");
	check("collapsed, the project row still does", (grid(pp).match(/class="ppl-day[^"]*" data-date="/g) || []).length, 21);
	const phone = pp.ppl_list_html(pp.data.lookahead.projects.filter((p) => pp.ppl_project_visible(p)), pp.range_days());
	check("on a phone, every day", (phone.match(/<section class="ppl-list-day ppl-day/g) || []).length, 21);
	// A chip dropped on one of them is scheduled that day, through the page's own drop.
	const plan = pp.plan_drop({ kind: "card", card: pp.by_task.U1, from_date: null }, { date: "2026-10-27" });
	check("the drop schedules the chip on the day", [plan.method, plan.args], ["save_task", { task: "U1", start: "2026-10-27" }]);
});

test("Collapsing a project or a phase draws its work as runs", () => {
	const pp = planner();
	pp.ppl_state().collapsed.add("p:PRJ-1");
	let html = grid(pp);
	check("its rows are gone", shown_rows(html), ["T4"]);
	const project = rows(html).find((r) => r.includes('data-pk-project="PRJ-1"'));
	check("the toggle says so", project.includes('aria-expanded="false"'), true);
	check("its days as runs", (project.match(/class="ppl-roll" style="grid-column: [^"]+"/g) || []).map((s) => s.split(": ")[1].replace('"', "")), ["2 / 7", "20 / 23"]);
	pp.ppl_state().collapsed.clear();
	pp.ppl_state().collapsed.add("f:PRJ-1|P300");
	html = grid(pp);
	check("a collapsed phase hides its tasks, keeps its undated line", [shown_rows(html), html.includes("Not scheduled (2)")], [["T4"], true]);
	check("Other work collapses by its own key", pp.ppl_toggle_html("f:PRJ-2|", true, "x").includes('data-ppl-toggle="f:PRJ-2|"'), true);
});

test("The filters: Needs crew, Project, PM and Group", () => {
	let pp = planner();
	pp.ppl_state().needs_only = true;
	let html = grid(pp);
	check("Needs crew: the tasks short of crew only", [shown_rows(html), html.includes('data-task="U1"'), html.includes('data-task="U2"')], [["T3"], true, false]);
	check("and no project with no tasks", project_rows(html), ["PRJ-1"]);
	pp = planner({ project: "PRJ-2" });
	check("Project", project_rows(grid(pp)), ["PRJ-2"]);
	pp = planner({ pm: "Clegg" });
	check("PM", project_rows(grid(pp)), ["PRJ-1"]);
	pp = planner({ group: "PM" });
	html = grid(pp);
	check("Group: work with someone of that group on it", [project_rows(html), shown_rows(html)], [["PRJ-1"], ["T1"]]);
	pp = planner({ project: "PRJ-9" });
	check("nothing left says so", grid(pp).includes("Nothing is scheduled in these weeks."), true);
});

test("The bar over the board: the weeks, and the Needs crew chip with its count", () => {
	const pp = planner();
	const head = pp.ppl_head_html();
	check("four lengths, three pressed", [(head.match(/data-ppl-weeks="/g) || []).length, /data-ppl-weeks="3" aria-pressed="true"/.test(head)], [4, true]);
	check("the chip counts the tasks short of crew", /data-ppl-needs aria-pressed="false"[^>]*><span class="pp-tb-count">2<\/span>/.test(head), true);
	// The count is what pressing it shows: with Group = PM, only the PM's short work (none here).
	const grouped = planner({ group: "PM" });
	check("the Group filter counts too", grouped.ppl_head_html().includes("data-ppl-needs"), false);
	const field = planner({ group: "Field" });
	check("Field: the one short task with a Field crew", /<span class="pp-tb-count">1<\/span>/.test(field.ppl_head_html()), true);
	// A new answer (each load is one) is read afresh.
	pp.data = Object.assign({}, pp.data, { lookahead: Object.assign({}, pp.data.lookahead, { needs_crew: [] }) });
	pp.data.tasks.forEach((c) => (c.short = 0));
	check("none, no chip", pp.ppl_head_html().includes("data-ppl-needs"), false);
});

test("Crossing the phone width redraws the board as a list or a grid", () => {
	const listeners = [];
	ctx.window.matchMedia = () => ({ matches: false, addEventListener: (type, fn) => listeners.push([type, fn]) });
	const pp = planner();
	let drawn = 0;
	pp.render = () => (drawn += 1);
	pp.ppl_state();
	check("one listener, for changes", listeners.map(([type]) => type), ["change"]);
	listeners[0][1]();
	check("a change on the Lookahead redraws", drawn, 1);
	pp.view = "week";
	listeners[0][1]();
	check("not on another tab", drawn, 1);
	ctx.window.matchMedia = () => ({ matches: false });
});

// ------------------------------------------------------------------ 3. the rest

test("The phone: a list by day, each day a drop target, then Not scheduled and No tasks yet", () => {
	const pp = planner();
	const html = pp.ppl_list_html(pp.data.lookahead.projects, pp.range_days());
	const days_of = (h) => (h.match(/<section class="ppl-list-day ppl-day[^"]*" data-date="([^"]+)"/g) || []).map((s) => s.slice(-11, -1));
	check("with undated work to place, every day of the window", days_of(html).length, 21);
	check("an empty day is a short line", (html.match(/ppl-list-free/g) || []).length, 12);
	pp.data.lookahead.projects[0].not_scheduled = [];
	const dated_only = pp.ppl_list_html(pp.data.lookahead.projects, pp.range_days());
	check("without any, only the days with work", days_of(dated_only), ["2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-27", "2026-11-05", "2026-11-06", "2026-11-07"]);
	check("a task on each of its days, as a card drawn on that day", (html.match(/data-task="T1" data-date="/g) || []).length, 4);
	check("the undated work, then the no-task projects", [html.includes("Not scheduled (2): drag onto a day"), html.includes("No tasks yet (1)")], [true, true]);
});

test("Print this view: the board's rows as a list", () => {
	const pp = planner();
	pp.p6c_day_label = (ymd) => ymd;
	pp.p6c_print_item = (c) => ({ color: c.color, marks: c.tentative ? ["Pencil"] : [] });
	const model = pp.ppl_print();
	check("columns", model.columns, ["Project", "Phase", "Task", "Dates", "Days", "Crew", "Notes"]);
	check(
		"rows in the board's order, undated last in their phase",
		model.rows.map((r) => [r.cols[0], r.cols[1], r.cols[2], r.cols[3], r.cols[4]]),
		[
			["Riverwalk", "Phase 300 — Rough-in", "Set forms", "2026-10-15 – 2026-10-20", "4"],
			["Riverwalk", "Phase 300 — Rough-in", "Excavate basin", "2026-10-19 – 2026-10-22", "4"],
			["Riverwalk", "Phase 300 — Rough-in", "Pour slab", "2026-11-05 – 2026-11-10", "4"],
			["Riverwalk", "Phase 400 — Mechanical", "Pull wire", "Not scheduled", ""],
			["Riverwalk", "Phase 400 — Mechanical", "Set vault", "Not scheduled", ""],
			["Tanner", "Other work", '<img src=x onerror="alert(1)">', "2026-10-27", "1"],
		]
	);
	check("a pencil says so", model.rows[0].cols[6], "Pencil");
});

test("load() asks get_lookahead for the window, with the draft and Show all tasks arguments", () => {
	const pp = planner({ all_tasks: "1" });
	pp.ppl_state().weeks = 4;
	const call = pp.ppl_call(pp.range().start);
	check("the method and arguments", [call.method, call.args], ["erpnext_enhancements.api.planner_lookahead.get_lookahead", { start: "2026-10-18", weeks: 4, draft: 1, all_tasks: 1 }]);
	check("its range is the window", [pp.range_days().length, pp.range_days()[27]], [28, "2026-11-14"]);
	pp.view = "week";
	check("every other view asks get_planner as before", pp.ppl_call(moment("2026-10-18")), null);
});

test("A drag is measured from the day under the pointer, and lands on a Lookahead day", () => {
	const pp = planner();
	const cell = (ymd, left) => ({ getAttribute: () => ymd, getBoundingClientRect: () => ({ left, right: left + 40 }) });
	const cells = [cell("2026-10-19", 300), cell("2026-10-20", 340), cell("2026-10-21", 380)];
	const attrs = {};
	const bar = { closest: () => ({ querySelectorAll: () => cells }), setAttribute: (k, v) => (attrs[k] = v) };
	pp.ppl_grab_day(bar, 355);
	check("the second day of the bar", attrs["data-date"], "2026-10-20");
	pp.ppl_grab_day(bar, 10);
	check("off the cells, unchanged", attrs["data-date"], "2026-10-20");
	// find_target: a Lookahead day is a day.
	const day = { getAttribute: () => "2026-10-27" };
	const el = { closest: (sel) => (sel.includes(".ppl-day[data-date]") && !sel.includes("pp-cell") ? day : null) };
	ctx.document.elementFromPoint = () => el;
	pp.drag = { source: { kind: "card" } };
	pp.$body = [{ contains: () => true }];
	check("the drop target", pp.find_target(1, 1), { el: day, date: "2026-10-27" });
});

test("The board waits for an answer of its own", () => {
	const pp = planner();
	delete pp.data.lookahead;
	const drawn = [];
	pp.ppl_render = () => drawn.push("lookahead");
	pp.render_panel = () => drawn.push("panel");
	pp.$panel = { hide: () => drawn.push("hide") };
	pp.render();
	check("nothing drawn from a get_planner answer", drawn, []);
});

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (error) {
			failures += 1;
			console.log(`  ERROR ${error && error.stack ? error.stack : error}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	if (failures) {
		console.log(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed`);
})();
