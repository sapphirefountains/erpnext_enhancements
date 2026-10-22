#!/usr/bin/env node
/**
 * The Projects Dashboard's tab bar and toolbar (v1.719.0): View More, the "+" menu, the
 * Columns icon button and where "Reset widths" went.
 *
 * Runs the REAL custom_html_blocks/projects_dashboard.js, and the real column_selector.js,
 * in a vm against a recording stand-in for jQuery (no jsdom: the repo's JS guards run on
 * plain node). The stand-in keeps one node per selector, so a click handler the script bound
 * to `$root.find("#btn-view-more")` is the one the test fires, and what the script set on a
 * node (`prop("hidden")`, `attr("aria-expanded")`, `text()`) reads back.
 *
 * What this holds:
 *   - View More shows the three extra tabs, flips aria-expanded and its label, remembers
 *     the choice in localStorage (never frappe user_settings), and collapsing while one of
 *     those tabs is on screen lands on Priority Overview rather than stranding the user.
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
		if (key === "#pg-new-list" || key === ".pg-more-tab") s.props.hidden = true;
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
				// removing "active" from every .nav-link clears each tab's own node too
				if (key === ".nav-link") {
					for (const [k, other] of registry) {
						if (k.startsWith(".nav-link[")) other[NODE].classes.clear();
					}
				}
				return proxy;
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
				return sel === ":visible" ? s.display === "shown" : false;
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

async function runBlock({ stored = {}, projects = [] } = {}) {
	const dom = makeDom();
	const storage = new Map(Object.entries(stored));
	const writes = [];
	const newDocs = [];
	const selectors = [];
	const resets = [];

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
		Promise,
		__: (t) => t,
		$: dom.$,
		document: dom.documentNode,
		localStorage: {
			getItem: (k) => (storage.has(k) ? storage.get(k) : null),
			setItem: (k, v) => {
				storage.set(k, v);
				writes.push(k);
			},
			removeItem: (k) => storage.delete(k),
		},
		frappe: {
			provide() {},
			require: (assets, cb) => cb(),
			call: () => Promise.resolve({ message: projects }),
			new_doc: (doctype) => newDocs.push(doctype),
			utils: { escape_html: (v) => String(v) },
			datetime: { get_today: () => "2026-10-22", str_to_user: (v) => String(v) },
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
	return { dom, storage, writes, newDocs, selectors, resets };
}

function navClick(run, route) {
	const links = run.dom.node(".nav-link");
	links[NODE].data.route = route;
	run.dom.fire(links, "click");
}

console.log("\nprojects_dashboard.js: View More");

await test("View More starts collapsed: the extra tabs are hidden and the button says View More", async () => {
	const run = await runBlock();
	assert.equal(run.dom.node(".pg-more-tab").prop("hidden"), true);
	const btn = run.dom.node("#btn-view-more");
	assert.equal(btn.attr("aria-expanded"), "false");
	assert.equal(btn.text(), "View More");
});

await test("pressing View More shows the extra tabs, says View Less, and remembers it", async () => {
	const run = await runBlock();
	const btn = run.dom.node("#btn-view-more");
	run.dom.fire(btn, "click");
	assert.equal(run.dom.node(".pg-more-tab").prop("hidden"), false);
	assert.equal(btn.attr("aria-expanded"), "true");
	assert.equal(btn.text(), "View Less");
	assert.equal(run.storage.get("chb_projects_dashboard_more_v1"), "1");
});

await test("pressing it again collapses, and the collapsed choice is remembered too", async () => {
	const run = await runBlock();
	const btn = run.dom.node("#btn-view-more");
	run.dom.fire(btn, "click");
	run.dom.fire(btn, "click");
	assert.equal(run.dom.node(".pg-more-tab").prop("hidden"), true);
	assert.equal(btn.attr("aria-expanded"), "false");
	assert.equal(run.storage.get("chb_projects_dashboard_more_v1"), "0");
});

await test("a remembered open state is restored on load, without writing anything back", async () => {
	const run = await runBlock({ stored: { chb_projects_dashboard_more_v1: "1" } });
	assert.equal(run.dom.node(".pg-more-tab").prop("hidden"), false);
	assert.equal(run.dom.node("#btn-view-more").text(), "View Less");
	assert.deepEqual(run.writes, [], "restoring a choice is not a new choice");
});

await test("collapsing while a hidden tab is showing lands on Priority Overview", async () => {
	const run = await runBlock({ stored: { chb_projects_dashboard_more_v1: "1" } });
	navClick(run, "completed-projects");
	await settle();
	const active = run.dom.node('.nav-link[data-route="completed-projects"]');
	assert.ok(active[NODE].classes.has("active"), "the Completed tab is the active one");
	run.dom.fire(run.dom.node("#btn-view-more"), "click");
	await settle();
	assert.ok(
		run.dom.node('.nav-link[data-route="priority-overview"]')[NODE].classes.has("active"),
		"fell back to Priority Overview"
	);
});

await test("collapsing while Priority Overview or the Gantt is showing leaves the tab alone", async () => {
	const run = await runBlock({ stored: { chb_projects_dashboard_more_v1: "1" } });
	navClick(run, "priority-overview");
	await settle();
	const before = run.dom.node('.nav-link[data-route="priority-overview"]')[NODE].classes.has("active");
	assert.ok(before);
	run.dom.fire(run.dom.node("#btn-view-more"), "click");
	assert.ok(run.dom.node('.nav-link[data-route="priority-overview"]')[NODE].classes.has("active"));
});

await test("nothing is kept in frappe user_settings (Redis only, flushed on every deploy)", () => {
	// comments stripped: the block's own header names user_settings to say it is not used
	const code = (source) =>
		source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'\\])\/\/[^\n]*/g, "$1");
	assert.ok(!/user_settings/.test(code(BLOCK_JS)), "the block must not use user_settings");
	assert.ok(!/user_settings/.test(code(SELECTOR_JS)));
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
	const run = await runBlock({
		projects: [{ name: "PROJ-1", project_name: "P", project_type: "Build", is_active: "Yes", status: "Active" }],
	});
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
	const drawn = run.dom.log.created.concat(
		run.dom.log.appended.flatMap((a) => a.items.map((i) => (typeof i === "string" ? i : "")))
	);
	assert.ok(!drawn.some((h) => /Reset widths|dashboard-reset-widths/.test(h)), "found: " + drawn.filter((h) => /Reset/.test(h)));
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
