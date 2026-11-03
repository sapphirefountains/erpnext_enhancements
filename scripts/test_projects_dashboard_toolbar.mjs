#!/usr/bin/env node
/**
 * The Projects Dashboard's head band, tabs and Priority Overview filters (Mock B, v1.781.0):
 * the live tiles, the stream chips, Ranked only, Group by stream, the remembered tab and
 * sort, Refresh with "Updated N min ago", the "+" menu, the Columns icon button and where
 * "Reset widths" went.
 *
 * Runs the REAL custom_html_blocks/projects_dashboard.js, and the real column_selector.js,
 * in a vm against a recording stand-in for jQuery (no jsdom: the repo's JS guards run on
 * plain node). The stand-in keeps one node per selector, so a click handler the script bound
 * to `$root.find(".pd-tile")` is the one the test fires, and what the script set on a node
 * (`attr("aria-pressed")`, `text()`, `prop("checked")`) reads back. Rows the script appends
 * to a table body are recorded as markup, so what a filter leaves on the page is what the
 * test reads.
 *
 * What this holds:
 *   - The four tiles are tallied from get_project_data's rows (active, past expected end,
 *     due in 30 days, no company rank); pressing one narrows the Priority Overview and marks
 *     itself pressed; Active clears it; pressing one from another tab lands on the Overview.
 *   - The stream chips carry counts and narrow the rows; Ranked only keeps the numbered
 *     company ranks; Group by stream draws header rows in the agreed order (Build, Design,
 *     Service, Events, then Internal) and moves the sort to the stream rank. All three are
 *     remembered per browser, in the dashboard's own keys, never frappe user_settings.
 *   - The tab chosen is remembered and restored; an unknown saved tab falls back to Priority
 *     Overview; nothing reads the retired View More key.
 *   - Ranks are pills, named values muted text, no hue scale; Budget replaced Spend %;
 *     a past-due row carries the Past due pill.
 *   - Refresh asks the server again and the band says "Updated just now".
 *   - A browser with storage blocked still draws.
 *   - The "+" opens a menu; New Project and New Master Project each do exactly what the old
 *     buttons did (frappe.new_doc), and close the menu; Escape closes it.
 *   - The list tabs' Columns control is an icon button (tooltip and aria-label "Columns")
 *     with "Reset column widths" in its menu, and no separate Reset widths button is drawn.
 *   - ColumnSelector itself: the icon markup carries title + aria-label, the word button is
 *     still the default, the menu action runs and closes the menu, Escape closes it.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "erpnext_enhancements");
const BLOCK_JS = fs.readFileSync(path.join(ROOT, "custom_html_blocks", "projects_dashboard.js"), "utf8");
const SELECTOR_JS = fs.readFileSync(
	path.join(ROOT, "public", "js", "project_enhancements", "dashboard_components", "column_selector.js"),
	"utf8"
);

let failed = 0;
async function test(name, fn) {
	try {
		await fn();
		console.log("  ok   " + name);
	} catch (err) {
		failed += 1;
		console.log("  FAIL " + name + "\n       " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join("\n       ") : err));
	}
}

// ------------------------------------------------------------------ the jQuery stand-in

const NODE = Symbol("node");

function makeDom() {
	const registry = new Map();
	const log = { appended: [], created: [] };

	function node(key) {
		if (registry.has(key)) return registry.get(key);
		const s = {
			key,
			attrs: {},
			props: {},
			text: "",
			data: {},
			handlers: {},
			classes: new Set(),
			display: null,
		};
		// what the shell's markup says before any script runs
		if (key === "#pg-new-list") s.props.hidden = true;
		const impl = {
			[NODE]: s,
			attr(k, v) {
				if (v === undefined) return s.attrs[k];
				s.attrs[k] = v;
				return proxy;
			},
			prop(k, v) {
				if (v === undefined) return s.props[k];
				s.props[k] = v;
				return proxy;
			},
			text(v) {
				if (v === undefined) return s.text;
				s.text = v;
				return proxy;
			},
			val(v) {
				if (v === undefined) return s.props.value === undefined ? "" : s.props.value;
				s.props.value = v;
				return proxy;
			},
			data(k, v) {
				if (v === undefined) return s.data[k];
				s.data[k] = v;
				return proxy;
			},
			on(evt, sel, fn) {
				const handler = typeof sel === "function" ? sel : fn;
				for (const e of String(evt).split(/\s+/)) (s.handlers[e] ||= []).push(handler);
				return proxy;
			},
			off() {
				return proxy;
			},
			trigger(evt, ...args) {
				fire(proxy, evt, ...args);
				return proxy;
			},
			find(sel) {
				return node(sel);
			},
			closest(sel) {
				return node("closest " + sel);
			},
			append(...items) {
				log.appended.push({ to: key, items });
				return proxy;
			},
			appendTo(target) {
				log.appended.push({ to: target && target[NODE] ? target[NODE].key : "?", items: [proxy] });
				return proxy;
			},
			addClass(c) {
				String(c).split(/\s+/).forEach((x) => s.classes.add(x));
				return proxy;
			},
			removeClass(c) {
				String(c).split(/\s+/).forEach((x) => s.classes.delete(x));
				// removing "active" from every .pd-tab clears each tab's own node too
				if (key === ".pd-tab") {
					for (const [k, other] of registry) {
						if (k.startsWith(".pd-tab[")) other[NODE].classes.clear();
					}
				}
				return proxy;
			},
			hasClass(c) {
				return s.classes.has(c);
			},
			toggle(on) {
				s.display = on ? "shown" : "none";
				return proxy;
			},
			show() {
				s.display = "shown";
				return proxy;
			},
			hide() {
				s.display = "none";
				return proxy;
			},
			is(sel) {
				if (sel === ":visible") return s.display === "shown";
				if (sel === ":checked") return !!s.props.checked;
				return false;
			},
			not() {
				return node("not-result");
			},
			click(fn) {
				return impl.on("click", fn);
			},
			get() {
				return [];
			},
			getRootNode: () => node("root-node"),
		};
		const element = { isConnected: true, getRootNode: () => node("root-node"), nodeType: 1 };
		const proxy = new Proxy(function () {}, {
			get(_, p) {
				if (p in impl) return impl[p];
				if (p === "then") return undefined;
				if (p === "length") return 1;
				if (p === Symbol.iterator) return function* () {};
				if (typeof p === "symbol") return undefined;
				if (/^\d+$/.test(p)) return element;
				return (...args) => proxy; // any other jQuery verb: chain
			},
		});
		registry.set(key, proxy);
		return proxy;
	}

	function fire(n, evt, extra) {
		const event = Object.assign(
			{ preventDefault() {}, stopPropagation() {}, key: "", target: {}, originalEvent: {} },
			extra || {}
		);
		for (const handler of (n[NODE].handlers[evt] || []).slice()) handler.call(n, event);
	}

	const documentNode = node("document");
	const $ = (arg) => {
		if (arg && arg[NODE]) return arg;
		if (arg === documentNode) return documentNode;
		if (typeof arg === "string") {
			log.created.push(arg);
			return node("html:" + arg);
		}
		return node("wrapped");
	};
	return { $, node, fire, log, documentNode };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle(times = 15) {
	for (let i = 0; i < times; i++) await tick();
}

// ------------------------------------------------------------------ run the real block

const TODAY = "2026-11-03";

// Six projects: four active (one per stream, one internal), one inactive, one completed.
//   A  Build     ranked 1, 50%, ended 2026-10-01  -> past due
//   B  Design    unranked, 0%, ends 2026-11-20    -> due in 30 days
//   C  Service   "Maintenance", 100%, ends 2026-12-31
//   D  Internal  "Not Assigned", no end           -> unranked
//   E  Build     inactive (Completed tab)
//   F  Events    status Completed (never on the Overview)
const PROJECTS = [
	{ name: "PRJ-A", project_name: "Alpha Plaza", project_type: "Build", is_active: "Yes", status: "Active", custom_company_priority: "1", custom_project_priority: "2", percent_complete: 50, expected_end_date: "2026-10-01", custom_project_dollar_amount: 30849 },
	{ name: "PRJ-B", project_name: "Bravo Water Wall", project_type: "Design", is_active: "Yes", status: "Working", custom_company_priority: "", custom_project_priority: "1", percent_complete: 0, expected_end_date: "2026-11-20", custom_project_dollar_amount: 0 },
	{ name: "PRJ-C", project_name: "Charlie Maintenance", project_type: "Service", is_active: "Yes", status: "Active", custom_company_priority: "Maintenance", custom_project_priority: "", percent_complete: 100, expected_end_date: "2026-12-31", custom_project_dollar_amount: 0 },
	{ name: "PRJ-D", project_name: "Delta Shop Tooling", project_type: "Internal", is_active: "Yes", status: "Active", custom_company_priority: "Not Assigned", custom_project_priority: "", percent_complete: 10, expected_end_date: null, custom_project_dollar_amount: 0 },
	{ name: "PRJ-E", project_name: "Echo Done", project_type: "Build", is_active: "No", status: "Paid", custom_company_priority: "3", percent_complete: 100, expected_end_date: "2026-01-01", completed_on: "2026-02-01" },
	{ name: "PRJ-F", project_name: "Foxtrot Gala", project_type: "Events", is_active: "Yes", status: "Completed", custom_company_priority: "4", percent_complete: 100, expected_end_date: "2026-10-20" },
];

async function runBlock({ stored = {}, projects = PROJECTS, storageThrows = false } = {}) {
	const dom = makeDom();
	const storage = new Map(Object.entries(stored));
	const writes = [];
	const reads = [];
	const newDocs = [];
	const selectors = [];
	const resets = [];
	const calls = [];
	const intervals = [];

	class FakeSelector {
		constructor(key) {
			this.key = key;
			selectors.push(this);
		}
		render_button(container, onChange, options) {
			this.rendered = { container, onChange, options };
			return null;
		}
		apply() {}
	}
	class FakeResizer {
		apply() {}
		attach_handles() {}
		reset() {
			resets.push("reset");
		}
	}

	const sandbox = {
		console,
		setTimeout,
		clearTimeout,
		setInterval: (fn, ms) => {
			intervals.push({ fn, ms });
			return intervals.length;
		},
		clearInterval() {},
		Promise,
		Date,
		// frappe's __ fills {0}, {1} from its second argument
		__: (t, args) => String(t).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] !== undefined ? String(args[i]) : m)),
		$: dom.$,
		document: dom.documentNode,
		localStorage: {
			getItem: (k) => {
				reads.push(k);
				if (storageThrows) throw new Error("storage blocked");
				return storage.has(k) ? storage.get(k) : null;
			},
			setItem: (k, v) => {
				if (storageThrows) throw new Error("storage blocked");
				storage.set(k, v);
				writes.push(k);
			},
			removeItem: (k) => storage.delete(k),
		},
		frappe: {
			provide() {},
			require: (assets, cb) => cb(),
			call: (opts) => {
				calls.push(opts.method);
				if (/get_priority_options$/.test(opts.method)) {
					return Promise.resolve({ message: { project_priority: ["1", "2", "3"], company_priority: ["1", "2", "3"] } });
				}
				if (/get_status_options$/.test(opts.method)) return Promise.resolve({ message: ["Active", "Working"] });
				return Promise.resolve({ message: projects });
			},
			new_doc: (doctype) => newDocs.push(doctype),
			utils: { escape_html: (v) => String(v) },
			datetime: { get_today: () => TODAY, str_to_user: (v) => String(v) },
			show_alert() {},
			set_route() {},
		},
		erpnext_enhancements: {
			dashboard_components: { ColumnSelector: FakeSelector, ColumnResizer: FakeResizer },
		},
		moment: () => ({ format: () => "" }),
		innerWidth: 1280,
	};
	sandbox.window = sandbox;
	vm.createContext(sandbox);
	new vm.Script("(function (root_element) {\n" + BLOCK_JS + "\n})", { filename: "projects_dashboard.js" }).runInContext(
		sandbox
	)({});
	await settle();
	return { dom, storage, writes, reads, newDocs, selectors, resets, calls, intervals };
}

/** Everything the script appended to a table body or built as markup since `from`. */
function drawn(run, from = 0) {
	return run.dom.log.appended
		.slice(from)
		.flatMap((a) => a.items)
		.filter((i) => typeof i === "string")
		.join("\n");
}
const mark = (run) => run.dom.log.appended.length;
const rowsOf = (html) => (html.match(/class="pd-pname">([^<]+)</g) || []).map((m) => m.replace(/.*>([^<]+)</, "$1"));

function clickTab(run, route) {
	const tabs = run.dom.node(".pd-tab");
	tabs.attr("data-route", route);
	run.dom.fire(tabs, "click");
}
function pressTile(run, key) {
	const tile = run.dom.node(".pd-tile");
	tile.attr("data-tile", key);
	run.dom.fire(tile, "click");
}
function pressChip(run, stream) {
	const chip = run.dom.node(".pd-fchip");
	chip.attr("data-stream", stream);
	run.dom.fire(chip, "click");
}
function check(run, id, on) {
	const box = run.dom.node(id);
	box.prop("checked", on);
	run.dom.fire(box, "change");
}
const tileCount = (run, key) => run.dom.node(`[data-tile-count="${key}"]`).text();
const chipCount = (run, stream) => run.dom.node(`[data-stream-count="${stream}"]`).text();
const isActiveTab = (run, route) => run.dom.node(`.pd-tab[data-route="${route}"]`)[NODE].classes.has("active");

console.log("\nprojects_dashboard.js: the head band's tiles");

await test("the four tiles are tallied from the rows the block already fetched", async () => {
	const run = await runBlock();
	assert.equal(tileCount(run, "active"), "4", "active: four live projects (not the inactive one, not the completed one)");
	assert.equal(tileCount(run, "past-due"), "1", "past expected end: Alpha, ended and 50%");
	assert.equal(tileCount(run, "due-30"), "1", "due in 30 days: Bravo, ends in 17 days");
	assert.equal(tileCount(run, "unranked"), "2", "no company rank: Bravo (blank) and Delta (Not Assigned)");
	assert.equal(run.dom.node('.pd-tile[data-tile="active"]').attr("aria-pressed"), "true");
	assert.equal(run.dom.node('.pd-tile[data-tile="past-due"]').attr("aria-pressed"), "false");
});

await test("the Priority Overview lists every active project by default, by company rank", async () => {
	const run = await runBlock();
	const rows = rowsOf(drawn(run));
	assert.deepEqual(rows, ["Alpha Plaza", "Bravo Water Wall", "Delta Shop Tooling", "Charlie Maintenance"]);
	assert.doesNotMatch(drawn(run), /Echo Done|Foxtrot Gala/, "inactive and completed projects stay off the Overview");
	assert.doesNotMatch(drawn(run), /pd-group/, "flat by default: no group rows");
});

await test("pressing Past expected end narrows the Overview and marks the tile pressed", async () => {
	const run = await runBlock();
	const from = mark(run);
	pressTile(run, "past-due");
	assert.deepEqual(rowsOf(drawn(run, from)), ["Alpha Plaza"]);
	assert.equal(run.dom.node('.pd-tile[data-tile="past-due"]').attr("aria-pressed"), "true");
	assert.equal(run.dom.node('.pd-tile[data-tile="active"]').attr("aria-pressed"), "false");
	assert.equal(chipCount(run, ""), "1", "the All chip counts what the tile left");
});

await test("Due in 30 days and No company rank narrow the same way; Active clears it", async () => {
	const run = await runBlock();
	let from = mark(run);
	pressTile(run, "due-30");
	assert.deepEqual(rowsOf(drawn(run, from)), ["Bravo Water Wall"]);
	from = mark(run);
	pressTile(run, "unranked");
	assert.deepEqual(rowsOf(drawn(run, from)).sort(), ["Bravo Water Wall", "Delta Shop Tooling"]);
	from = mark(run);
	pressTile(run, "active");
	assert.equal(rowsOf(drawn(run, from)).length, 4);
	assert.equal(run.dom.node('.pd-tile[data-tile="active"]').attr("aria-pressed"), "true");
});

await test("a tile pressed from another tab lands on the Priority Overview", async () => {
	const run = await runBlock();
	clickTab(run, "completed-projects");
	await settle();
	assert.ok(isActiveTab(run, "completed-projects"));
	const from = mark(run);
	pressTile(run, "past-due");
	assert.ok(isActiveTab(run, "priority-overview"), "went to the Overview");
	assert.deepEqual(rowsOf(drawn(run, from)), ["Alpha Plaza"]);
});

await test("the pressed tile is not remembered; the stream chip, Ranked only and grouping are", async () => {
	const run = await runBlock();
	pressTile(run, "past-due");
	assert.ok(!run.writes.some((k) => /tile/.test(k)), "no key for the tile");
	pressChip(run, "Build");
	check(run, "#pd-ranked-only", true);
	check(run, "#pd-group-by-stream", true);
	assert.deepEqual(JSON.parse(run.storage.get("chb_projects_dashboard_overview_v1")), {
		stream: "Build",
		ranked_only: true,
		group: true,
	});
});

console.log("\nprojects_dashboard.js: stream chips, Ranked only, Group by stream");

await test("the chips carry counts per stream, Internal being the internal project types", async () => {
	const run = await runBlock();
	assert.equal(chipCount(run, ""), "4");
	assert.equal(chipCount(run, "Build"), "1");
	assert.equal(chipCount(run, "Design"), "1");
	assert.equal(chipCount(run, "Service"), "1");
	assert.equal(chipCount(run, "Events"), "0");
	assert.equal(chipCount(run, "Internal"), "1");
	assert.equal(run.dom.node('.pd-fchip[data-stream=""]').attr("aria-pressed"), "true");
});

await test("a chip narrows the rows to its stream and is marked pressed", async () => {
	const run = await runBlock();
	const from = mark(run);
	pressChip(run, "Design");
	assert.deepEqual(rowsOf(drawn(run, from)), ["Bravo Water Wall"]);
	assert.equal(run.dom.node('.pd-fchip[data-stream="Design"]').attr("aria-pressed"), "true");
	assert.equal(run.dom.node('.pd-fchip[data-stream=""]').attr("aria-pressed"), "false");
});

await test("Ranked only keeps the numbered company ranks: not a word, not a blank", async () => {
	const run = await runBlock();
	const from = mark(run);
	check(run, "#pd-ranked-only", true);
	assert.deepEqual(rowsOf(drawn(run, from)), ["Alpha Plaza"], "Maintenance and Not Assigned are not ranks");
	assert.equal(chipCount(run, ""), "1");
});

await test("Group by stream draws header rows in the agreed order and moves the sort to the stream rank", async () => {
	const run = await runBlock();
	const from = mark(run);
	check(run, "#pd-group-by-stream", true);
	const html = drawn(run, from);
	const groups = (html.match(/class="pd-group"[^>]*><td colspan="8">([A-Za-z]+)/g) || []).map((m) => m.replace(/.*>/, ""));
	assert.deepEqual(groups, ["Build", "Design", "Service", "Internal"], "Build, Design, Service, Events, then Internal");
	assert.match(html, /--pd-group-color: var\(--sf-chip-edge-build\)/, "the group edge is the pillar's themed chip edge");
	assert.match(html, /1 projects · 1 ranked/, "the Build group's note");
	assert.equal(JSON.parse(run.storage.get("chb_projects_dashboard_sort_v1"))["priority-overview"].col, "project_priority");
	const from2 = mark(run);
	check(run, "#pd-group-by-stream", false);
	assert.doesNotMatch(drawn(run, from2), /pd-group/);
	assert.equal(JSON.parse(run.storage.get("chb_projects_dashboard_sort_v1"))["priority-overview"].col, "company_priority");
});

await test("a remembered stream, Ranked only and grouping are restored on load, without writing back", async () => {
	const run = await runBlock({
		stored: { chb_projects_dashboard_overview_v1: JSON.stringify({ stream: "Design", ranked_only: false, group: true }) },
	});
	const html = drawn(run);
	assert.deepEqual(rowsOf(html), ["Bravo Water Wall"]);
	assert.match(html, /pd-group/);
	assert.equal(run.dom.node('.pd-fchip[data-stream="Design"]').attr("aria-pressed"), "true");
	assert.equal(run.dom.node("#pd-group-by-stream").prop("checked"), true);
	assert.deepEqual(run.writes, [], "restoring a choice is not a new choice");
});

console.log("\nprojects_dashboard.js: the tabs");

await test("five tabs; the one clicked is marked and remembered", async () => {
	const run = await runBlock();
	assert.ok(isActiveTab(run, "priority-overview"));
	clickTab(run, "summary");
	await settle();
	assert.ok(isActiveTab(run, "summary"));
	assert.equal(run.dom.node('.pd-tab[data-route="summary"]').attr("aria-selected"), "true");
	assert.ok(!isActiveTab(run, "priority-overview"));
	assert.equal(run.storage.get("chb_projects_dashboard_tab_v1"), JSON.stringify("summary"));
});

await test("a remembered tab is restored on load; an unknown one falls back to Priority Overview", async () => {
	const run = await runBlock({ stored: { chb_projects_dashboard_tab_v1: JSON.stringify("completed-projects") } });
	assert.ok(isActiveTab(run, "completed-projects"));
	assert.match(drawn(run), /Echo Done/, "the Completed tab drew its rows");
	const odd = await runBlock({ stored: { chb_projects_dashboard_tab_v1: JSON.stringify("dashboard") } });
	assert.ok(isActiveTab(odd, "priority-overview"));
});

await test("nothing reads the retired View More key, and nothing is kept in frappe user_settings", async () => {
	const run = await runBlock();
	assert.ok(!run.reads.includes("chb_projects_dashboard_more_v1"));
	assert.equal(run.dom.node("#btn-view-more")[NODE].handlers.click, undefined, "no View More handler");
	const code = (source) =>
		source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'\\])\/\/[^\n]*/g, "$1");
	assert.ok(!/user_settings/.test(code(BLOCK_JS)), "the block must not use user_settings");
	assert.ok(!/user_settings/.test(code(SELECTOR_JS)));
});

await test("a browser with storage blocked still draws", async () => {
	const run = await runBlock({ storageThrows: true });
	assert.equal(tileCount(run, "active"), "4");
	assert.equal(rowsOf(drawn(run)).length, 4);
	clickTab(run, "summary");
	await settle();
	assert.ok(isActiveTab(run, "summary"));
});

console.log("\nprojects_dashboard.js: the one table");

await test("ranks are pills, named values muted text, and there is no hue scale", async () => {
	const run = await runBlock();
	const html = drawn(run);
	assert.match(html, /<span class="pd-rank">1<\/span>/, "Alpha's company rank, filled");
	assert.match(html, /<span class="pd-rank pd-rank-outline">2<\/span>/, "Alpha's stream rank, an outline");
	assert.match(html, /<span class="pd-named">Maintenance<\/span>/);
	assert.match(html, /<span class="pd-named">Not Assigned<\/span>/);
	assert.doesNotMatch(html, /hsl\(|badge/);
});

await test("Budget replaced Spend %: whole dollars, right-aligned, a dash when 0", async () => {
	const run = await runBlock();
	const html = drawn(run);
	assert.match(html, /dashcol-budget pd-num">\$30,849</);
	assert.match(html, /dashcol-budget pd-num"><span class="pd-dash">—<\/span>/);
	assert.doesNotMatch(html, /spend/i);
});

await test("a past-due row carries the Past due pill; the others do not", async () => {
	const run = await runBlock();
	const html = drawn(run);
	const alpha = html.slice(html.indexOf("Alpha Plaza"), html.indexOf("Bravo Water Wall"));
	assert.match(alpha, /<span class="pd-late">Past due<\/span>/);
	assert.equal((html.match(/pd-late/g) || []).length, 1);
	assert.match(html, /class="pd-bar" aria-hidden="true"><i style="width: 50%">/, "completion as a bar");
});

await test("the Completed and Internal tabs draw the same table style", async () => {
	const run = await runBlock();
	let from = mark(run);
	clickTab(run, "completed-projects");
	await settle();
	let html = drawn(run, from);
	assert.deepEqual(rowsOf(html), ["Echo Done"]);
	assert.match(html, /dashcol-project_type">Build</, "the type as a chip (the stand-in draws its word)");
	from = mark(run);
	clickTab(run, "active-internal-projects");
	await settle();
	html = drawn(run, from);
	assert.deepEqual(rowsOf(html), ["Delta Shop Tooling"]);
	assert.match(html, /class="pd-group"[^>]*><td colspan="6">Independent Projects/, "a header row per master project");
	assert.match(html, /class="pd-select project-inline-edit"/);
	assert.match(run.dom.log.created.join("\n"), /<table class="pd-table dashboard-resizable-table">/);
	assert.doesNotMatch(run.dom.log.created.join("\n"), /table-bordered|thead-light/);
});

console.log("\nprojects_dashboard.js: Refresh and Updated");

await test("Refresh asks the server again and the band says Updated just now", async () => {
	const run = await runBlock();
	assert.equal(run.dom.node("#pd-updated").text(), "Updated just now");
	const before = run.calls.length;
	run.dom.fire(run.dom.node("#btn-refresh"), "click");
	await settle();
	assert.equal(run.calls.length, before + 3);
	assert.equal(run.dom.node("#btn-refresh").prop("disabled"), false, "re-enabled once the answer landed");
	assert.deepEqual(run.intervals.map((i) => i.ms), [60000], "one tick a minute for the relative time");
});

console.log("\nprojects_dashboard.js: the + menu");

await test("the + menu opens with both items, aria-expanded true", async () => {
	const run = await runBlock();
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), true, "closed on load (markup is hidden)");
	run.dom.fire(run.dom.node("#btn-new"), "click");
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), false);
	assert.equal(run.dom.node("#btn-new").attr("aria-expanded"), "true");
});

await test("New Project opens a new Project and closes the menu", async () => {
	const run = await runBlock();
	run.dom.fire(run.dom.node("#btn-new"), "click");
	run.dom.fire(run.dom.node("#btn-new-project"), "click");
	assert.deepEqual(run.newDocs, ["Project"]);
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), true);
});

await test("New Master Project opens a new Master Project and closes the menu", async () => {
	const run = await runBlock();
	run.dom.fire(run.dom.node("#btn-new"), "click");
	run.dom.fire(run.dom.node("#btn-new-master-project"), "click");
	assert.deepEqual(run.newDocs, ["Master Project"]);
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), true);
});

await test("Escape closes an open + menu; the toggle opens it again", async () => {
	const run = await runBlock();
	run.dom.fire(run.dom.node("#btn-new"), "click");
	run.dom.fire(run.dom.node("#pg-new-menu"), "keydown", { key: "Escape", target: {} });
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), true);
	assert.equal(run.dom.node("#btn-new").attr("aria-expanded"), "false");
	run.dom.fire(run.dom.node("#btn-new"), "click");
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), false);
});

await test("a click outside closes the + menu; a click inside its own wrapper leaves it open", async () => {
	const run = await runBlock();
	const wrap = run.dom.node("#pg-new-menu")[0];
	const clickDocument = (path) =>
		run.dom.fire(run.dom.documentNode, "click", { originalEvent: { composedPath: () => path } });
	run.dom.fire(run.dom.node("#btn-new"), "click");
	clickDocument([wrap, {}]);
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), false, "a click inside the menu's own wrapper");
	clickDocument([{}, {}]);
	assert.equal(run.dom.node("#pg-new-list").prop("hidden"), true, "a click elsewhere closes it");
	assert.equal(run.dom.node("#btn-new").attr("aria-expanded"), "false");
});

console.log("\nprojects_dashboard.js: the Columns button");

await test("each list tab hands its ColumnSelector the icon option and a Reset column widths action", async () => {
	const run = await runBlock();
	const rendered = run.selectors.map((s) => s.rendered).filter(Boolean);
	assert.ok(rendered.length >= 1, "the Priority Overview drew its toolbar");
	for (const r of rendered) {
		assert.equal(r.options.icon, true, "Columns is an icon button");
		assert.deepEqual(Array.from(r.options.actions, (a) => a.label), ["Reset column widths"]);
	}
});

await test("the Reset column widths action resets the widths", async () => {
	const run = await runBlock();
	const r = run.selectors.map((s) => s.rendered).find(Boolean);
	assert.ok(r, "a toolbar was drawn");
	r.options.actions[0].onClick();
	assert.deepEqual(run.resets, ["reset"]);
});

await test("no separate Reset widths button is drawn into the toolbar", async () => {
	const run = await runBlock();
	const all = run.dom.log.created.concat(
		run.dom.log.appended.flatMap((a) => a.items.map((i) => (typeof i === "string" ? i : "")))
	);
	assert.ok(!all.some((h) => /Reset widths|dashboard-reset-widths/.test(h)), "found: " + all.filter((h) => /Reset/.test(h)));
});

// ------------------------------------------------------------------ the real ColumnSelector

console.log("\ncolumn_selector.js");

function loadSelector() {
	const dom = makeDom();
	const sandbox = {
		console,
		__: (t) => t,
		$: dom.$,
		document: dom.documentNode,
		localStorage: { getItem: () => null, setItem() {} },
		frappe: { provide() {}, utils: { escape_html: (v) => String(v).replace(/"/g, "&quot;") } },
		erpnext_enhancements: {},
	};
	sandbox.window = sandbox;
	vm.createContext(sandbox);
	// frappe.provide builds the namespace in real life; the stand-in just makes it
	sandbox.erpnext_enhancements.dashboard_components = {};
	new vm.Script(SELECTOR_JS, { filename: "column_selector.js" }).runInContext(sandbox);
	const Selector = sandbox.erpnext_enhancements.dashboard_components.ColumnSelector;
	return { dom, Selector, selector: new Selector("k", [{ key: "a", label: "A" }]) };
}

await test("the icon button is marked up with title and aria-label Columns, and a menu popup", () => {
	const { dom, selector } = loadSelector();
	selector.render_button(dom.node("toolbar"), () => {}, { icon: true });
	const html = dom.log.created.find((h) => h.includes("column-selector-toggle"));
	assert.ok(html, "the wrapper was built");
	assert.match(html, /title="Columns"/);
	assert.match(html, /aria-label="Columns"/);
	assert.match(html, /aria-haspopup/);
	assert.match(html, /<svg[^>]*aria-hidden="true"/, "an inline SVG, which resolves inside a shadow root");
	assert.ok(!/<i class="fa fa-columns/.test(html), "no word button in icon mode");
});

await test("without the icon option the word button is unchanged", () => {
	const { dom, selector } = loadSelector();
	selector.render_button(dom.node("toolbar"), () => {});
	const html = dom.log.created.find((h) => h.includes("column-selector-toggle"));
	assert.match(html, /fa fa-columns/);
	assert.match(html, /> Columns</);
	assert.ok(!/title="Columns"/.test(html), "the word button needs no tooltip");
});

await test("a menu action runs its callback and closes the menu; toggling flips aria-expanded", () => {
	const { dom, selector } = loadSelector();
	let ran = 0;
	selector.render_button(dom.node("toolbar"), () => {}, {
		icon: true,
		actions: [{ label: "Reset column widths", onClick: () => ran++ }],
	});
	const toggle = dom.node(".column-selector-toggle");
	const menu = dom.node(".column-selector-menu");
	dom.fire(toggle, "click");
	assert.equal(toggle.attr("aria-expanded"), "true");
	assert.equal(menu[NODE].display, "shown");
	// the action button is the last thing appended to the menu
	const action = dom.log.appended.filter((a) => a.to === ".column-selector-menu").flatMap((a) => a.items).pop();
	assert.ok(action, "the action was appended to the menu");
	dom.fire(action, "click");
	assert.equal(ran, 1);
	assert.equal(menu[NODE].display, "none");
	assert.equal(toggle.attr("aria-expanded"), "false");
});

await test("a click outside closes the Columns menu; a click on its button or checkboxes does not", () => {
	const { dom, selector } = loadSelector();
	const wrapper = selector.render_button(dom.node("toolbar"), () => {}, { icon: true });
	const toggle = dom.node(".column-selector-toggle");
	const menu = dom.node(".column-selector-menu");
	const clickDocument = (path) =>
		dom.fire(dom.documentNode, "click.columnSelector", { originalEvent: { composedPath: () => path } });
	dom.fire(toggle, "click");
	assert.equal(menu[NODE].display, "shown");
	clickDocument([wrapper[0], {}]);
	assert.equal(menu[NODE].display, "shown", "ticking a checkbox must not close the menu");
	clickDocument([{}, {}]);
	assert.equal(menu[NODE].display, "none", "a click elsewhere closes it");
	assert.equal(toggle.attr("aria-expanded"), "false");
});

await test("Escape closes an open column menu", () => {
	const { dom, selector } = loadSelector();
	const wrapper = selector.render_button(dom.node("toolbar"), () => {}, { icon: true });
	const toggle = dom.node(".column-selector-toggle");
	dom.fire(toggle, "click");
	dom.fire(wrapper, "keydown", { key: "Escape" });
	assert.equal(dom.node(".column-selector-menu")[NODE].display, "none");
});

console.log(failed ? "\n" + failed + " FAILED" : "\nall passed");
process.exit(failed ? 1 : 0);
