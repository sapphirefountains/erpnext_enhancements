#!/usr/bin/env node
/**
 * P6 of the planner UX redesign: the Project Planner's Gantt tab (docs/planner-ux-redesign.md §4.3),
 * and the embed hooks it added to the app's Gantt widget, run under node against the REAL files.
 *
 *   1. gantt_widget.js on a recording DHTMLX stub. A host's `fetch` replaces get_gantt_data, its
 *      `on_write` replaces update_gantt_row (a refusal puts the bar back; a refresh asked for during the
 *      write waits for it), a link drawn by drag goes to `on_link` and is never added by DHTMLX itself
 *      (finish-to-start only), a double-clicked link and a right-click go to the host, a row marked
 *      ee_readonly is never editable, and the baseline is a bar the task type's renderer appends,
 *      because the Standard build deletes gantt.addTaskLayer (checked in a browser on 2026-10-21).
 *      Without the hooks, the widget behaves as before (update_gantt_row, no links).
 *   2. The page's PPG block. PPG_PURE (wire dates, an exclusive end, a drag rounded to whole days, the
 *      rows and links the widget draws), the /gantt/<project> address, every drag through the page's
 *      commit() (Ctrl/⌘ moves one; a past day asks), a link through send() and Undo, the header handing
 *      Publish… and Reset baseline to P4, load() asking get_project_gantt, the chart taken down on any
 *      other screen, Display's Show all tasks and Print this view.
 *
 * Run: node scripts/test_planner_gantt.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.join(__dirname, "..", "erpnext_enhancements");
const WIDGET_JS = path.join(ROOT, "public", "js", "gantt_widget", "gantt_widget.js");
const PP_JS = path.join(ROOT, "project_enhancements", "page", "project_planner", "project_planner.js");

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

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const settle = async () => {
	for (let i = 0; i < 8; i++) await tick();
};
const strip = (src) =>
	src
		.replace(/\/\*[\s\S]*?\*\//g, "")
		.split("\n")
		.map((line) => line.replace(/(^|\s)\/\/.*$/, "$1"))
		.join("\n");

// ====================================================================== 1. the widget

class El {
	constructor(tag) {
		this.tagName = String(tag || "div").toUpperCase();
		this.children = [];
		this.style = {};
		this.attrs = {};
		this.className = "";
		this.textContent = "";
		this.nodeType = 1;
		this.listeners = {};
		const self = this;
		this.classList = {
			add: (c) => (self.className = `${self.className} ${c}`.trim()),
			remove: (c) => (self.className = self.className.split(" ").filter((x) => x !== c).join(" ")),
			contains: (c) => self.className.split(" ").includes(c),
		};
	}
	set innerHTML(value) {
		this.children = [];
		this._html = value;
	}
	get innerHTML() {
		return this._html || "";
	}
	appendChild(child) {
		this.children.push(child);
		if (child && typeof child.onload === "function") setTimeout(() => child.onload(), 0);
		return child;
	}
	setAttribute(k, v) {
		this.attrs[k] = String(v);
	}
	addEventListener(type, fn) {
		(this.listeners[type] = this.listeners[type] || []).push(fn);
	}
	removeEventListener() {}
	querySelectorAll() {
		return [];
	}
	getRootNode() {
		return DOCUMENT;
	}
	contains() {
		return false;
	}
}

const DOCUMENT = {
	head: new El("head"),
	createElement: (tag) => new El(tag),
	addEventListener() {},
	removeEventListener() {},
	querySelector: () => null,
};

const DAY = 86400000;
const pad = (n) => String(n).padStart(2, "0");
const to_wire = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
const from_wire = (s) => {
	const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(s || "");
	return m ? new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]) : null;
};

// A DHTMLX instance that records what the widget asks of it. Dates parse from the wire format, as
// DHTMLX's date_format does; posFromDate is 10px a day from 2026-01-01.
function fakeGantt(log) {
	const events = {};
	const tasks = new Map();
	const links = new Map();
	const g = {
		config: { types: { task: "task", project: "project" }, links: { finish_to_start: "0", start_to_start: "1" }, row_height: 34 },
		templates: { tooltip_date_format: (d) => to_wire(d) },
		events,
		tasks,
		date: { date_to_str: () => to_wire, add: (d, n) => new Date(d.getTime() + n * DAY) },
		attachEvent(name, fn) {
			(events[name] = events[name] || []).push(fn);
		},
		fire(name, ...args) {
			return (events[name] || []).map((fn) => fn(...args));
		},
		init(el) {
			log.push(["init", !!el]);
		},
		plugins() {},
		clearAll() {
			tasks.clear();
			links.clear();
		},
		parse({ data, links: ls }) {
			(data || []).forEach((row) => {
				const task = Object.assign({}, row);
				if (typeof task.start_date === "string") task.start_date = from_wire(task.start_date);
				if (typeof task.end_date === "string") task.end_date = from_wire(task.end_date);
				tasks.set(task.id, task);
			});
			(ls || []).forEach((l) => links.set(l.id, Object.assign({}, l)));
			log.push(["parse", (data || []).map((t) => t.id), (ls || []).map((l) => l.id)]);
		},
		isTaskExists: (id) => tasks.has(id),
		getTask: (id) => tasks.get(id),
		updateTask: (id) => log.push(["updateTask", id]),
		isLinkExists: (id) => links.has(id),
		getLink: (id) => links.get(id),
		getLinks: () => [...links.values()],
		eachTask: (fn) => tasks.forEach((t) => fn(t)),
		hasChild: () => false,
		getScrollState: () => ({ x: 0, y: 0 }),
		scrollTo: (x, y) => log.push(["scrollTo", x, y]),
		showDate: () => log.push(["showDate"]),
		render: () => log.push(["render"]),
		destructor: () => log.push(["destructor"]),
		posFromDate: (d) => Math.round((d.getTime() - new Date(2026, 0, 1).getTime()) / DAY) * 10,
		isReadonly: (t) => !t.ee_editable,
	};
	return g;
}

function loadWidget() {
	const log = [];
	const calls = [];
	const alerts = [];
	const instances = [];
	const window = {};
	const frappe = {
		provide(ns) {
			let o = context;
			ns.split(".").forEach((p) => {
				o[p] = o[p] || {};
				o = o[p];
			});
		},
		dom: {
			eval() {
				window.Gantt = { getGanttInstance: () => (instances.push(fakeGantt(log)), instances[instances.length - 1]) };
				window.gantt = {};
			},
		},
		assets: { bundled_asset: (x) => x },
		utils: { escape_html: (s) => String(s) },
		show_alert: (m) => alerts.push(m.message),
		call(opts) {
			calls.push(opts);
			if (opts.method.endsWith("update_gantt_row")) return Promise.resolve({ message: { status: "success", modified: "m2" } });
			return Promise.resolve({ message: { tasks: [], links: [], meta: {} } });
		},
	};
	const context = vm.createContext({
		window,
		document: DOCUMENT,
		frappe,
		__: (t, a) => String(t).replace(/\{(\d+)\}/g, (_m, i) => (a && a[i] != null ? String(a[i]) : "")),
		fetch: () => Promise.resolve({ ok: true, text: () => Promise.resolve("/* dhtmlx */") }),
		console,
		setTimeout,
		clearTimeout,
		Promise,
		Date,
		Math,
		Object,
		WeakMap,
		WeakSet,
		Set,
		Map,
	});
	context.erpnext_enhancements = undefined;
	vm.runInContext(fs.readFileSync(WIDGET_JS, "utf-8"), context, { filename: WIDGET_JS });
	return { NS: context.erpnext_enhancements.gantt, log, calls, alerts, instances };
}

const ROWS = () => ({
	tasks: [
		{ id: "G1", text: "Phase 300", type: "project", parent: 0, ref_doctype: "Task", ref_name: "G1", ee_readonly: true },
		{ id: "T1", text: "Dig", parent: "G1", start_date: "2026-10-20 00:00", end_date: "2026-10-23 00:00", ref_doctype: "Task", ref_name: "T1", ee_baseline_start: "2026-10-19", ee_baseline_end: "2026-10-21" },
		{ id: "T2", text: "Pour", parent: "G1", start_date: "2026-10-23 00:00", end_date: "2026-10-24 00:00", ref_doctype: "Task", ref_name: "T2" },
		{ id: "T3", text: "Done", parent: "G1", start_date: "2026-10-01 00:00", end_date: "2026-10-02 00:00", ref_doctype: "Task", ref_name: "T3", ee_readonly: true },
	],
	links: [{ id: "T1>T2", source: "T1", target: "T2", type: "0" }],
	meta: { can_write: { Task: true } },
});

async function mountWith(config) {
	const env = loadWidget();
	const el = new El("div");
	const fetched = [];
	const w = env.NS.mount(
		el,
		Object.assign(
			{
				doctype: "Task",
				fields: { text: "subject", start: "exp_start_date", end: "exp_end_date" },
				editable: { dates: true, links: true },
				fetch: (server_config) => (fetched.push(server_config), Promise.resolve(ROWS())),
			},
			config || {}
		)
	);
	await w.ready;
	await settle();
	return Object.assign(env, { w, g: env.instances[0], fetched });
}

// A drag as DHTMLX runs one: the snapshot, the task moved in place, then the drop with its event.
function drag(g, id, start, end, event) {
	g.fire("onBeforeTaskDrag", id, "move");
	const t = g.getTask(id);
	t.start_date = from_wire(start);
	t.end_date = from_wire(end);
	g.fire("onAfterTaskDrag", id, "move", event || { type: "mouseup", ctrlKey: false });
}

test("Widget: a host's fetch replaces get_gantt_data", async () => {
	const env = await mountWith();
	check("asked the host, with the config the export uses", env.fetched.map((c) => c.doctype), ["Task"]);
	check("never get_gantt_data", env.calls.filter((c) => c.method.endsWith("get_gantt_data")).length, 0);
	check("parsed the host's rows and links", env.log.find((e) => e[0] === "parse").slice(1), [["G1", "T1", "T2", "T3"], ["T1>T2"]]);
	const plain = await mountWith({ fetch: undefined });
	check("without it, get_gantt_data as before", plain.calls.map((c) => c.method), ["erpnext_enhancements.api.gantt.get_gantt_data"]);
});

test("Widget: a row marked ee_readonly is never editable", async () => {
	const { g } = await mountWith();
	check("editable", ["G1", "T1", "T2", "T3"].map((id) => !!g.getTask(id).ee_editable), [false, true, true, false]);
});

test("Widget: on_write replaces update_gantt_row; a refusal puts the bar back", async () => {
	const writes = [];
	let answer = { status: "success" };
	const env = await mountWith({ on_write: (task, changes, info) => (writes.push([task.id, changes, info.mode, info.event.ctrlKey]), Promise.resolve(answer)) });
	drag(env.g, "T2", "2026-10-26 00:00", "2026-10-27 00:00", { type: "mouseup", ctrlKey: true });
	await settle();
	check("the host was given the change and the drop's event", writes, [["T2", { start: "2026-10-26 00:00", end: "2026-10-27 00:00" }, "move", true]]);
	check("update_gantt_row never called", env.calls.filter((c) => c.method.endsWith("update_gantt_row")).length, 0);
	check("kept", to_wire(env.g.getTask("T2").start_date), "2026-10-26 00:00");
	answer = { status: "refused", message: "Pour cannot be changed from the planner." };
	drag(env.g, "T2", "2026-10-30 00:00", "2026-10-31 00:00");
	await settle();
	check("put back where it was", to_wire(env.g.getTask("T2").start_date), "2026-10-26 00:00");
	check("and the host's message shown", env.alerts.slice(-1), ["Pour cannot be changed from the planner."]);
	// The host backed out (Esc on a question, a refusal it already explained): back, and not a word.
	answer = { status: "cancelled" };
	const said = env.alerts.length;
	drag(env.g, "T2", "2026-11-02 00:00", "2026-11-03 00:00");
	await settle();
	check("cancelled: put back, no toast", [to_wire(env.g.getTask("T2").start_date), env.alerts.length], ["2026-10-26 00:00", said]);
	const plain = await mountWith({ fetch: undefined, on_write: undefined });
	plain.g.parse({ data: ROWS().tasks, links: [] });
	plain.g.getTask("T2").ee_editable = true;
	drag(plain.g, "T2", "2026-10-26 00:00", "2026-10-27 00:00");
	await settle();
	check("without it, update_gantt_row as before", plain.calls.filter((c) => c.method.endsWith("update_gantt_row")).length, 1);
});

test("Widget: a refresh asked for during a write waits for it", async () => {
	let release;
	const env = await mountWith({ on_write: () => new Promise((resolve) => (release = resolve)) });
	const before = env.fetched.length;
	drag(env.g, "T2", "2026-10-26 00:00", "2026-10-27 00:00");
	await settle();
	env.w.refresh();
	await settle();
	check("not while the write is out", env.fetched.length, before);
	release({ status: "success" });
	await settle();
	check("once it settles", env.fetched.length, before + 1);
});

test("Widget: a link drawn by drag goes to the host, finish-to-start only, and is never added by DHTMLX", async () => {
	const made = [];
	const env = await mountWith({ on_link: (link) => (made.push([link.source.id, link.target.id, link.type]), Promise.resolve(null)) });
	check("links can be drawn", env.g.config.drag_links, true);
	const kept = env.g.fire("onBeforeLinkAdd", "new", { source: "T1", target: "T2", type: "0" });
	await settle();
	check("DHTMLX is told no", kept, [false]);
	check("the host makes it", made, [["T1", "T2", "0"]]);
	env.g.fire("onBeforeLinkAdd", "new", { source: "T1", target: "T2", type: "1" });
	await settle();
	check("start-to-start is refused with a sentence", [made.length, env.alerts.slice(-1)[0]], [1, "Draw a link from the end of one bar to the start of the next."]);
	const none = await mountWith();
	check("no on_link, no drawing links", none.g.config.drag_links, false);
	const readonly = await mountWith({ on_link: () => null, editable: { dates: true } });
	check("nor without the links opt-in", readonly.g.config.drag_links, false);
});

test("Widget: a double-clicked link and a right-click go to the host", async () => {
	const seen = [];
	const env = await mountWith({
		on_link_dblclick: (link, source, target) => seen.push(["unlink", link.id, source.id, target.id]),
		on_task_context: (id, task, e) => seen.push(["menu", id, task.text, e.type]),
	});
	check("never DHTMLX's own delete confirm", env.g.fire("onLinkDblClick", "T1>T2"), [false]);
	check("never the browser's menu over a row", env.g.fire("onContextMenu", "T2", null, { type: "contextmenu" }), [false]);
	check("but the browser's over empty chart", env.g.fire("onContextMenu", null, null, { type: "contextmenu" }), [true]);
	check("the host was told", seen, [["unlink", "T1>T2", "T1", "T2"], ["menu", "T2", "Pour", "contextmenu"]]);
});

test("Widget: the baseline is a bar the task renderer appends, not addTaskLayer", async () => {
	const env = await mountWith({ baseline: true });
	const g = env.g;
	check("the bar leaves the row room for it", g.config.bar_height, 18);
	const small = await mountWith({ baseline: true, gantt: { row_height: 24 } });
	check("sized from the embed's own row height", small.g.config.bar_height, 12);
	const own = await mountWith({ baseline: true, gantt: { row_height: 40, bar_height: 30 } });
	check("an embed's own bar height wins", own.g.config.bar_height, 30);
	const render = g.config.type_renderers && g.config.type_renderers.task;
	check("a renderer for the task type", typeof render, "function");
	const node = render(g.getTask("T1"), () => new El("div"));
	const bar = node.children[0];
	check("appended to the task's own node", [node.children.length, bar.className], [1, "ee-gantt-baseline"]);
	// T1 starts on the 20th; its baseline runs the 19th to the 21st (inclusive): one day before it, 3 days long.
	check("on the baseline's days, measured from the bar", [bar.style.left, bar.style.width, bar.style.top], ["-10px", "30px", "21px"]);
	check("none for a task without a baseline", render(g.getTask("T2"), () => new El("div")).children.length, 0);
	const off = await mountWith();
	check("not asked for, not drawn", !!(off.g.config.type_renderers && off.g.config.type_renderers.task), false);
	// A baseline long before every task (and today) is inside the scale, or posFromDate pins it to the edge.
	const early = await mountWith({
		baseline: true,
		today: true,
		fetch: () =>
			Promise.resolve({
				tasks: [{ id: "T9", text: "Late", start_date: "2026-12-01 00:00", end_date: "2026-12-02 00:00", ref_doctype: "Task", ref_name: "T9", ee_baseline_start: "2026-06-01", ee_baseline_end: "2027-02-10" }],
				links: [],
				meta: { can_write: { Task: true } },
			}),
	});
	check("the scale starts a week before the baseline and ends a week after it", [to_wire(early.g.config.start_date), to_wire(early.g.config.end_date)], [
		"2026-05-25 00:00",
		"2027-02-17 23:59",
	]);
	check("and the layers API is never relied on", /addTaskLayer\s*\(/.test(strip(fs.readFileSync(WIDGET_JS, "utf-8"))), false);
});

// ====================================================================== 2. the page

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
	};
	return m;
}

function loadPage() {
	const routes = [];
	const confirms = [];
	const alerts = [];
	const calls = [];
	const frappe = {
		pages: { "project-planner": {} },
		boot: { sysdefaults: { first_day_of_the_week: "Sunday" } },
		session: { user: "nik@example.com" },
		datetime: { get_today: () => "2026-10-21", str_to_user: (d) => d },
		route: ["project-planner", "gantt", "PRJ-1"],
		get_route: () => frappe.route,
		set_route: (...args) => routes.push(args),
		route_options: null,
		utils: {
			escape_html: (v) =>
				String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"),
		},
		confirm: (message, yes, no) => confirms.push({ message, yes, no }),
		show_alert: (m) => alerts.push(m.message),
		call: (opts) => (calls.push(opts), Promise.resolve({ message: {} })),
	};
	const store = {};
	const sandbox = {
		frappe,
		window: {
			localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => (store[k] = String(v)) },
			matchMedia: () => ({ matches: false }),
			location: { pathname: "/desk/project-planner/gantt/PRJ-1", search: "", hash: "" },
			history: { state: null, replaceState() {} },
		},
		document: { elementFromPoint: () => null },
		$: () => ({}),
		__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args && args[i] != null ? String(args[i]) : "")),
		moment,
		console,
		CSS: { escape: (s) => String(s) },
		URLSearchParams,
	};
	const context = vm.createContext(sandbox);
	vm.runInContext(
		`${fs.readFileSync(PP_JS, "utf-8")}\nthis.ProjectPlanner = ProjectPlanner; this.PPG = PPG; this.PPG_PURE = PPG_PURE; this.PP = PP;`,
		context,
		{ filename: PP_JS }
	);
	return Object.assign(context, { routes, confirms, alerts, calls, store });
}

const ctx = loadPage();
const G = ctx.PPG_PURE;

const card = (name, extra) =>
	Object.assign(
		{ name, subject: name, project: "PRJ-1", project_title: "Riverwalk", crew: [], crew_size: 0, short: 0, movable: true, modified: "m-" + name, depends_on: [] },
		extra
	);

function answer() {
	return {
		today: "2026-10-21",
		can_edit: true,
		hidden_tasks: 3,
		tasks: [
			card("T3", { subject: "Survey", start: "2026-10-01", end: "2026-10-02", finished: true, movable: false, gantt_parent: "G300", span_days: 2 }),
			card("T1", {
				subject: "Excavate basin",
				start: "2026-10-20",
				end: "2026-10-23",
				gantt_parent: "G300",
				span_days: 4,
				baseline: { start: "2026-10-19", end: "2026-10-21" },
				crew: [{ resource: "RES-1", label: "Kyle Jones" }],
				drafted: true,
			}),
			card("T2", { subject: "Set forms", start: "2026-10-26", end: "2026-10-27", gantt_parent: "G300", depends_on: ["T1", "G300", "T-OFF"], span_days: 2, tentative: true }),
			card("D1", { subject: "Strip forms", start: "2026-10-28", end: "2026-10-28", gantt_parent: "G300", new_task: true, depends_on: ["T2"] }),
		],
		unscheduled: [card("U1", { subject: "Pull wire", gantt_parent: "G400", depends_on: ["T2"] })],
		resources: [{ name: "RES-1", label: "Kyle Jones", group: "Field" }],
		projects: [
			{ name: "PRJ-1", title: "Riverwalk", pm: "Clegg", schedule: { status: "Draft", published_on: null, baseline_set_on: null } },
			{ name: "PRJ-2", title: '<script>alert("x")</script>', pm: "Nik", schedule: { status: "Published" } },
		],
		drafts: { projects: { "PRJ-1": { count: 2, shared: 1 } } },
		gantt: {
			project: "PRJ-1",
			title: "Riverwalk",
			schedule: { status: "Draft", published_on: null, baseline_set_on: null },
			groups: [{ name: "G300", subject: "Phase 300 Rough-in", parent: null, number: 300 }],
		},
	};
}

function planner(extra) {
	const pp = Object.create(ctx.ProjectPlanner.prototype);
	Object.assign(pp, {
		view: "gantt",
		anchor: "2026-10-21",
		project: "",
		pm: "",
		group: "",
		all_tasks: "",
		draft_on: false,
		modified: {},
		task_days: {},
		needs_crew: new Set(),
		ppg: null,
		p6c: null,
		request: 0,
	});
	pp.data = answer();
	pp.index();
	pp.ppg_state().project = "PRJ-1";
	pp.t1_can = () => true;
	pp.p6a_initials = (resource, label) => String(label || resource).split(" ").map((w) => w[0]).join("");
	pp.p6a_route = (fn) => fn();
	Object.assign(pp, extra || {});
	return pp;
}

test("PPG_PURE: wire dates, an exclusive end, a drag rounded to whole days", () => {
	check("wire", [G.wire("2026-10-21"), G.wire_end("2026-10-21"), G.wire_end("2026-12-31")], ["2026-10-21 00:00", "2026-10-22 00:00", "2027-01-01 00:00"]);
	check("nearest day", [G.nearest_day("2026-10-21 11:59"), G.nearest_day("2026-10-21 12:00"), G.nearest_day("2026-10-31 18:00"), G.nearest_day("x")], [
		"2026-10-21",
		"2026-10-22",
		"2026-11-01",
		null,
	]);
	const c = { start: "2026-10-20", end: "2026-10-23" };
	check("a move", G.args_from(c, { start: "2026-10-22 00:00", end: "2026-10-26 00:00" }), { start: "2026-10-22", end: "2026-10-25" });
	check("a resize of the end", G.args_from(c, { start: "2026-10-20 00:00", end: "2026-10-27 00:00" }), { start: "2026-10-20", end: "2026-10-26" });
	check("a drag that stopped mid-day in the Week zoom", G.args_from(c, { start: "2026-10-21 14:00", end: "2026-10-25 02:00" }), { start: "2026-10-22", end: "2026-10-24" });
	check("no change is nothing", G.args_from(c, { start: "2026-10-20 00:00", end: "2026-10-24 00:00" }), null);
	check("never shorter than a day", G.args_from(c, { start: "2026-10-22 00:00", end: "2026-10-22 00:00" }), { start: "2026-10-22", end: "2026-10-22" });
	check("a one-day task with no end", G.args_from({ start: "2026-10-20" }, { start: "2026-10-20 00:00", end: "2026-10-21 00:00" }), null);
	check("addresses", [G.address("PRJ-1"), G.address("")], [["gantt", "PRJ-1"], ["gantt"]]);
	check("zoom", ["day", "month", "year", null].map(G.zoom), ["day", "month", "week", "week"]);
	check("link args: the bar the line starts at is waited on", G.link_args({ id: "T1", ref_name: "T1" }, { id: "T2", ref_name: "T2" }), { task: "T2", depends_on: "T1" });
	check("not onto itself", G.link_args({ id: "T1" }, { id: "T1" }), null);
});

test("PPG_PURE: the rows and links the widget draws", () => {
	const out = G.rows(answer(), { not_scheduled: "Not scheduled" }, (c) => (c.crew || []).map((m) => m.label[0]).join(""));
	const by = {};
	out.tasks.forEach((t) => (by[t.id] = t));
	check("groups first, then the dated work, then Not scheduled and the undated", out.tasks.map((t) => t.id), ["G300", "T3", "T1", "T2", "D1", "ppg:not-scheduled", "U1"]);
	check("a group is a collapsible container that never moves", [by.G300.type, by.G300.ee_readonly, by.G300.parent], ["project", true, 0]);
	check("a task's days, its end exclusive", [by.T1.start_date, by.T1.end_date, by.T1.parent], ["2026-10-20 00:00", "2026-10-24 00:00", "G300"]);
	check("its baseline, days and crew", [by.T1.ee_baseline_start, by.T1.ee_baseline_end, by.T1.ee_days, by.T1.ee_crew], ["2026-10-19", "2026-10-21", 4, "K"]);
	check("finished work and a bar the planner may not move are read-only", [by.T3.ee_readonly, by.T1.ee_readonly], [true, false]);
	check("their marks", [by.T1.ee_class, by.T2.ee_class, by.T3.ee_class, by.D1.ee_class], [
		"ppg-bar ppg-drafted",
		"ppg-bar ppg-pencil",
		"ppg-bar ppg-finished ppg-locked",
		"ppg-bar ppg-new",
	]);
	check("undated work has no bar, under Not scheduled", [by.U1.parent, by.U1.unscheduled, "start_date" in by.U1, by["ppg:not-scheduled"].unscheduled], ["ppg:not-scheduled", true, false, true]);
	check("a group above nothing on the chart puts its work at the top", G.rows({ tasks: [card("X", { start: "2026-10-20", gantt_parent: "GONE" })] }).tasks[0].parent, 0);
	check("links: both ends drawn, never from a group", out.links.map((l) => [l.source, l.target, l.type]), [
		["T1", "T2", "0"],
		["T2", "D1", "0"],
		["T2", "U1", "0"],
	]);
	check("editable as the server says", out.meta, { can_write: { Task: true } });
	check("no Not scheduled row with nothing undated", G.rows(Object.assign(answer(), { unscheduled: [] })).tasks.some((t) => t.id === "ppg:not-scheduled"), false);
	// A phase whose work is all undated would be an empty row with a made-up day stub: left out.
	const undated_phase = answer();
	undated_phase.gantt.groups.push({ name: "G400", subject: "Phase 400", parent: null, number: 400 }, { name: "G410", subject: "410 Wiring", parent: "G400", number: 410 });
	undated_phase.unscheduled.push(card("U2", { gantt_parent: "G410" }));
	const kept = G.rows(undated_phase).tasks.map((t) => t.id);
	check("a phase with only undated work is not drawn", [kept.includes("G400"), kept.includes("G410"), kept.includes("U2")], [false, false, true]);
	undated_phase.tasks.push(card("T7", { start: "2026-11-02", end: "2026-11-02", gantt_parent: "G410" }));
	const both = G.rows(undated_phase).tasks;
	check("once one task under it is dated, it and its parent phase are", [both.some((t) => t.id === "G400"), (both.find((t) => t.id === "G410") || {}).parent], [true, "G400"]);
});

test("The address: /gantt/<project> is the Gantt's, and the tab opens the project", () => {
	const pp = planner();
	check("a project", pp.ppg_route_target(["project-planner", "gantt", "PRJ-7"]), { view: "gantt", anchor: "2026-10-21", project: "PRJ-7" });
	check("none", pp.ppg_route_target(["project-planner", "gantt"]).project, "");
	check("another screen is not the Gantt's", pp.ppg_route_target(["project-planner", "lookahead", "2026-10-21"]), null);
	ctx.frappe.route = ["project-planner", "gantt", "PRJ-7"];
	check("route_target hands it over", pp.route_target(), { view: "gantt", anchor: "2026-10-21", project: "PRJ-7" });
	// The Gantt tab from the Lookahead: the Project filter's project, else this device's last one.
	ctx.frappe.route = ["project-planner", "lookahead", "2026-10-18"];
	ctx.routes.length = 0;
	const tab = planner();
	tab.ppg = null;
	tab.ppg_remember("PRJ-9");
	tab.go("gantt", "2026-10-21");
	check("this device's last project", ctx.routes.slice(-1)[0], ["project-planner", "gantt", "PRJ-9", {}]);
	tab.project = "PRJ-3";
	tab.go("gantt", "2026-10-21");
	check("the Project filter wins, and rides along", ctx.routes.slice(-1)[0], ["project-planner", "gantt", "PRJ-3", { project: "PRJ-3" }]);
	check("remembered per signed-in user", ctx.store["ee_project_planner_gantt:nik@example.com"], "PRJ-9");
	// On the Gantt, the Today button scrolls the chart rather than routing anywhere.
	ctx.frappe.route = ["project-planner", "gantt", "PRJ-1"];
	ctx.routes.length = 0;
	const on = planner();
	const scrolled = [];
	on.ppg.widget = { scroll_to_today: () => scrolled.push(1), destroy() {} };
	on.go("gantt", "2026-10-21");
	check("Today on the Gantt scrolls to today", [scrolled.length, ctx.routes.length], [1, 0]);
});

test("Any other screen takes the chart down; the Gantt's own project is taken from the address", () => {
	const pp = planner();
	const gone = [];
	pp.ppg.widget = { destroy: () => gone.push(1) };
	pp.ppg_take_target({ view: "gantt", project: "PRJ-2" });
	check("a Gantt keeps its chart", [gone.length, pp.ppg.project], [0, "PRJ-2"]);
	pp.ppg_take_target({ view: "lookahead" });
	check("another tab destroys it", [gone.length, pp.ppg.widget], [1, null]);
});

test("A drag goes through the page's commit(), as on every tab", async () => {
	const committed = [];
	let saved = {};
	const pp = planner({ commit: (c, method, args, message, opts) => (committed.push([c.name, method, args, message, opts]), Promise.resolve(saved)) });
	const result = await pp.ppg_write({ ref_name: "T1" }, { start: "2026-10-22 00:00", end: "2026-10-26 00:00" }, { mode: "move", event: { ctrlKey: false } });
	check("save_task with the new days", committed[0].slice(0, 3), ["T1", "save_task", { task: "T1", start: "2026-10-22", end: "2026-10-25" }]);
	check("its message and the tasks that follow", [committed[0][3], committed[0][4]], ["Excavate basin moved to Thu, Oct 22", { solo: false }]);
	check("the widget keeps the bar", result, { status: "success" });
	await pp.ppg_write({ ref_name: "T1" }, { start: "2026-10-20 00:00", end: "2026-10-27 00:00" }, { mode: "resize", event: { metaKey: true } });
	check("a resize, Cmd held: just that one", [committed[1][2], committed[1][3], committed[1][4]], [
		{ task: "T1", start: "2026-10-20", end: "2026-10-26" },
		"Excavate basin now ends Mon, Oct 26",
		{ solo: true },
	]);
	const refused = await pp.ppg_write({ ref_name: "T3" }, { start: "2026-10-05 00:00", end: "2026-10-07 00:00" }, { mode: "move" });
	check("finished work is refused, and the bar goes back", [refused.status, committed.length], ["refused", 2]);
	check("no change sends nothing", [await pp.ppg_write({ ref_name: "T1" }, { start: "2026-10-20 00:00", end: "2026-10-24 00:00" }, {}), committed.length], [{ status: "success" }, 2]);
	// commit() resolves null when nothing was saved (Esc on Notify crew?, a reason not given, a refusal).
	saved = null;
	const backed = await pp.ppg_write({ ref_name: "T1" }, { start: "2026-10-22 00:00", end: "2026-10-26 00:00" }, { mode: "move" });
	check("nothing saved: the widget puts the bar back at once", backed, { status: "cancelled" });
});

test("While Hold all my changes is on, links are refused rather than written live", async () => {
	const sent = [];
	const pp = planner({ draft_on: true, send: (method) => (sent.push(method), Promise.resolve({})), load: () => null });
	ctx.confirms.length = 0;
	await pp.ppg_link({ id: "T1", ref_name: "T1" }, { id: "U1", ref_name: "U1" });
	pp.ppg_unlink({ id: "T1", ref_name: "T1" }, { id: "T2", ref_name: "T2" });
	check("nothing sent, nothing asked", [sent, ctx.confirms.length], [[], 0]);
	check("and it says why", ctx.alerts.slice(-1)[0], "Hold all my changes is on, and a link saves straight to the tasks. Turn it off to change links.");
});

test("‹ and › scroll a step of the zoom, measured on the chart's own scale", () => {
	const pp = planner();
	const scrolled = [];
	// posFromDate gives 0 for a date off the scale, as DHTMLX does.
	const min = new Date(2027, 2, 1);
	const g = {
		getState: () => ({ min_date: min }),
		getScrollState: () => ({ x: 500, y: 0 }),
		posFromDate: (d) => (d < min ? 0 : Math.round((d - min) / 86400000) * 10),
		scrollTo: (x, y) => scrolled.push([x, y]),
	};
	pp.ppg.widget = { gantt: g, destroy() {} };
	pp.ppg.zoom = "week";
	pp.ppg_shift(1);
	pp.ppg_shift(-1);
	check("28 days either way, on a chart that starts in 2027", scrolled, [[780, null], [220, null]]);
});

test("A past day asks first, and backing out settles the widget", async () => {
	const committed = [];
	const pp = planner({ commit: (c, m, args) => (committed.push(args), Promise.resolve({ name: c.name })) });
	ctx.confirms.length = 0;
	const asked = pp.ppg_write({ ref_name: "T1" }, { start: "2026-10-12 00:00", end: "2026-10-16 00:00" }, { mode: "move" });
	check("asked", ctx.confirms.length, 1);
	ctx.confirms[0].no();
	check("No: nothing sent, the bar goes back", [await asked, committed.length], [{ status: "cancelled" }, 0]);
	const again = pp.ppg_write({ ref_name: "T1" }, { start: "2026-10-12 00:00", end: "2026-10-16 00:00" }, { mode: "move" });
	ctx.confirms[1].yes();
	ctx.confirms[1].no(); // frappe.confirm's hide after Yes: a second settle changes nothing
	check("Yes: sent", [await again, committed.length], [{ status: "success" }, 1]);
});

test("A link is add_dependency through send(), with Undo; a double-click removes one", async () => {
	const sent = [];
	const undos = [];
	const pp = planner({
		send: (method, args, opts) => (sent.push([method, args, opts]), Promise.resolve(method === "add_dependency" ? { linked: true } : { removed: true })),
		load: () => sent.push(["load"]),
		push_undo: (snapshot, message) => (undos.push([snapshot, message]), true),
	});
	await pp.ppg_link({ id: "T2", ref_name: "T2" }, { id: "U1", ref_name: "U1" });
	check("U1 now waits on T2... it already did, so nothing is sent", sent.length, 0);
	await pp.ppg_link({ id: "T1", ref_name: "T1" }, { id: "U1", ref_name: "U1" });
	check("add_dependency, with the task's modified", sent[0].slice(0, 2), ["add_dependency", { task: "U1", depends_on: "T1", modified: "m-U1" }]);
	check("send() is given no Undo entry of its own", [sent[0][2].snapshot, sent[0][2].message], [undefined, undefined]);
	check("the Undo entry and its message, once the server says it linked", undos.slice(-1), [
		[{ ppg: "link", task: "U1", depends_on: "T1", subject: "Pull wire", added: true }, "Pull wire now waits for Excavate basin"],
	]);
	check("then the chart reloads", sent[1], ["load"]);
	check("the writes are planner_actions'", [pp.p6_send_modules.add_dependency, pp.p6_send_modules.remove_dependency], [
		"erpnext_enhancements.api.planner_actions",
		"erpnext_enhancements.api.planner_actions",
	]);
	sent.length = 0;
	await pp.ppg_link({ id: "T1", ref_name: "T1" }, { id: "D1", ref_name: "D1" });
	check("a drafted new task cannot be linked yet", [sent.length, ctx.alerts.slice(-1)[0]], [0, "A task that is still a draft cannot be linked yet. Publish the schedule first."]);
	ctx.confirms.length = 0;
	pp.ppg_unlink({ id: "T1", ref_name: "T1" }, { id: "T2", ref_name: "T2" });
	check("a double-click asks", ctx.confirms[0].message, "Remove the link? Set forms will no longer wait for Excavate basin.");
	ctx.confirms[0].yes();
	await settle();
	check("then remove_dependency", sent[0].slice(0, 2), ["remove_dependency", { task: "T2", depends_on: "T1", modified: "m-T2" }]);
	check("its Undo entry", undos.slice(-1)[0][0], { ppg: "link", task: "T2", depends_on: "T1", subject: "Set forms", added: false });
});

test("No Undo entry for a link the server did not make or remove: an Undo of it would undo someone else's", async () => {
	const undos = [];
	let answer_ = { linked: false };
	const pp = planner({
		send: () => Promise.resolve(answer_),
		load: () => null,
		push_undo: (snapshot) => (undos.push(snapshot), true),
	});
	await pp.ppg_link({ id: "T1", ref_name: "T1" }, { id: "U1", ref_name: "U1" });
	check("linked: false (it was there already)", undos.length, 0);
	check("and it says nothing changed", ctx.alerts.slice(-1)[0], "Nothing changed: Pull wire already waited for it.");
	answer_ = { removed: false };
	ctx.confirms.length = 0;
	pp.ppg_unlink({ id: "T1", ref_name: "T1" }, { id: "T2", ref_name: "T2" });
	ctx.confirms[0].yes();
	await settle();
	check("removed: false (it was gone already)", undos.length, 0);
	answer_ = null;
	await pp.ppg_link({ id: "T1", ref_name: "T1" }, { id: "U1", ref_name: "U1" });
	check("nothing saved (a reason not given): no entry either", undos.length, 0);
	answer_ = { linked: true };
	await pp.ppg_link({ id: "T1", ref_name: "T1" }, { id: "U1", ref_name: "U1" });
	check("linked: one entry", undos.length, 1);
});

test("Undo of a link is the opposite call, with the planner's Undo reason", async () => {
	const sent = [];
	const pp = planner({ send: (method, args, opts) => (sent.push([method, args, opts.auto_reason]), Promise.resolve({ linked: true, removed: true })), load: () => null, push_undo: () => true });
	check("drawn: removed again", pp.ppg_undo({ ppg: "link", task: "U1", depends_on: "T1", subject: "Pull wire", added: true }), true);
	check("removed: drawn again", pp.ppg_undo({ ppg: "link", task: "T2", depends_on: "T1", subject: "Set forms", added: false }), true);
	check("the calls", sent, [
		["remove_dependency", { task: "U1", depends_on: "T1", modified: "m-U1" }, "Undo on the Project Planner"],
		["add_dependency", { task: "T2", depends_on: "T1", modified: "m-T2" }, "Undo on the Project Planner"],
	]);
	check("not ours", pp.ppg_undo({ task: "T1", start: "2026-10-20" }), false);
});

test("The header: the schedule, Publish… and Reset baseline handed to P4, Build out, the zoom", () => {
	const pp = planner();
	const html = pp.ppg_head_html(pp.data.gantt);
	check("a Draft schedule offers Publish…", [html.includes("◐ Draft schedule"), html.includes("data-ppg-publish")], [true, true]);
	check("Set baseline, Build out ▾ and the three zooms", [html.includes(">Set baseline<"), html.includes("Build out ▾"), (html.match(/data-ppg-zoom=/g) || []).length], [true, true, 3]);
	check("the tasks the display rule left out are counted", html.includes("3 task(s) not shown"), true);
	check("project names are escaped in the picker", [html.includes("<script>"), html.includes("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;")], [false, true]);
	check("the Gantt's project is the one picked", /<option value="PRJ-1" selected>Riverwalk \(PRJ-1\)<\/option>/.test(html), true);
	const published = Object.assign({}, pp.data.gantt, { schedule: { status: "Published", published_on: "2026-10-21 09:00:00", baseline_set_on: "2026-10-21" } });
	const after = pp.ppg_head_html(published);
	check("Published and when, the baseline's date, Reset baseline", [after.includes("● Published Wed, Oct 21"), after.includes("Baseline Wed, Oct 21"), after.includes(">Reset baseline<"), after.includes("data-ppg-publish")], [
		true,
		true,
		true,
		false,
	]);
	pp.data.can_edit = false;
	const viewer = pp.ppg_head_html(pp.data.gantt);
	check("someone who cannot edit gets neither button", [viewer.includes("data-ppg-publish"), viewer.includes("data-ppg-baseline")], [false, false]);
	check("no project: the picker alone", planner().ppg_head_html(null).startsWith('<select class="form-control input-sm ppg-project"'), true);
});

test("Build out ▾ offers the grid and the grid footer's own starting points, each opening the grid first", async () => {
	const menus = [];
	const opened = [];
	const pp = planner({
		p6b_kit: () => ({ menu: (opts) => menus.push(opts) }),
		t1_open_build_out: (project, title) => (opened.push([project, title]), (pp.t1 = { grid: { project } }), Promise.resolve()),
		t3_open_templates: (grid) => opened.push(["template", grid.project]),
	});
	pp.ppg_build_menu({ getBoundingClientRect: () => ({ left: 10, bottom: 20 }) }, null);
	const labels = menus[0].items.map((item) => item.label);
	check("the grid, then the footer's starting points, never Add row or Create", labels.slice(0, 4), [
		"Build out grid…",
		"Start from template…",
		"Paste a list…",
		"Copy from project…",
	]);
	check("nothing else of the footer", labels.some((label) => /Add row|Create/.test(label)), false);
	await menus[0].items[1].on_click();
	check("a template: the grid opens on the project, then the templates", opened, [["PRJ-1", "Riverwalk"], ["template", "PRJ-1"]]);
});

test("load() asks get_project_gantt for the project, with the drafts and Show all tasks", async () => {
	const drawn = [];
	const pp = planner({
		$title: { text() {} },
		take_title_focus() {},
		ppl_sync_tabs() {},
		$body: { addClass() {}, removeClass() {} },
		fill_filters() {},
		write_filter_url() {},
		render: () => drawn.push(pp.data.gantt ? pp.data.gantt.project : null),
	});
	ctx.calls.length = 0;
	ctx.frappe.call = (opts) => (ctx.calls.push(opts), Promise.resolve({ message: answer() }));
	await pp.load();
	check("the call", ctx.calls.map((c) => [c.method, c.args]), [["erpnext_enhancements.api.planner_gantt.get_project_gantt", { draft: 1, project: "PRJ-1" }]]);
	check("drawn from the answer", drawn, ["PRJ-1"]);
	pp.all_tasks = "1";
	pp.ppg.project = "";
	await pp.load();
	check("a bare /gantt asks for the picker's list; Show all tasks rides along", ctx.calls[1].args, { draft: 1, all_tasks: 1 });
	check("a drawn project is remembered", pp.ppg_remembered(), "PRJ-1");
	// A project the server refuses is an answer (never a 404 or 403, so no Frappe dialog): this device
	// forgets it, and the picker says why.
	pp.ppg.project = "PRJ-GONE";
	pp.ppg_remember("PRJ-GONE");
	ctx.frappe.call = (opts) => (ctx.calls.push(opts), Promise.resolve({ message: { refused: "forbidden", projects: [], tasks: [], unscheduled: [], gantt: null } }));
	await pp.load();
	check("refused: forgotten, marked, drawn without a chart", [pp.ppg_remembered(), pp.ppg.failed, pp.ppg.refused, drawn.slice(-1)[0]], ["", "PRJ-GONE", "forbidden", null]);
	// A request that fails (the network, a 500, a timeout) says nothing about the project: kept.
	pp.ppg.project = "PRJ-1";
	pp.ppg_remember("PRJ-1");
	ctx.frappe.call = (opts) => (ctx.calls.push(opts), Promise.reject({ status: 500 }));
	await pp.load();
	check("trouble: kept, marked for a retry, not refused", [pp.ppg_remembered(), pp.ppg.trouble, pp.ppg.failed], ["PRJ-1", "PRJ-1", "PRJ-GONE"]);
	// Opening a project does not make it this device's until it has drawn.
	pp.ppg_take_target({ view: "gantt", project: "PRJ-NEW" });
	check("not remembered on the way in", pp.ppg_remembered(), "PRJ-1");
	// A slip while drawing is not a refusal: the device keeps its project.
	pp.ppg.project = "PRJ-1";
	pp.ppg_remember("PRJ-1");
	pp.render = () => {
		throw new Error("draw");
	};
	ctx.frappe.call = (opts) => Promise.resolve({ message: answer() });
	await pp.load().catch(() => null);
	check("a drawing error keeps the remembered project", pp.ppg_remembered(), "PRJ-1");
	ctx.frappe.call = (opts) => (ctx.calls.push(opts), Promise.resolve({ message: {} }));
});

test("Display holds Show all tasks on the Gantt, and Print this view lists the project's tasks", () => {
	const pp = planner({ p6c_day_label: (d) => d, p6c_print_item: () => ({ color: null, marks: [] }), resource_label: (r, l) => l || r });
	check("Display", pp.tb_display_items().map((item) => item.label), ["Show all tasks"]);
	const model = pp.ppg_print();
	check("a list", [model.layout, model.columns.length], ["list", 7]);
	check("its rows", model.rows.map((r) => r.cols.slice(0, 5)), [
		["Phase 300 Rough-in", "Survey", "2026-10-01 – 2026-10-02", "2", ""],
		["Phase 300 Rough-in", "Excavate basin", "2026-10-20 – 2026-10-23", "4", "2026-10-19 – 2026-10-21"],
		["Phase 300 Rough-in", "Set forms", "2026-10-26 – 2026-10-27", "2", ""],
		["Phase 300 Rough-in", "Strip forms", "2026-10-28", "1", ""],
		["G400", "Pull wire", "Not scheduled", "", ""],
	]);
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
