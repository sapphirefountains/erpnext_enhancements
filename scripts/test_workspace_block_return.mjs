#!/usr/bin/env node
/**
 * The workspace-return helper, and every Custom HTML Block that uses it — executed,
 * not grepped.
 *
 * THE BUG
 * =======
 *
 * The desk keeps one Workspaces page, and v16's Workspace.show() returns early when
 * the workspace it would show is the one already shown:
 *
 *     if (this._page?.name === page.name) return; // already shown
 *
 * So a dashboard -> a form -> Back left every block on the dashboard exactly as it was,
 * with the numbers it loaded first, until the browser tab was reloaded. Thirty block
 * headers claimed the opposite ("the workspace re-runs this whole script with a fresh
 * root on every navigation"), which is why nobody went looking.
 *
 * public/js/global_enhancements/workspace_block_return.js reloads a block when the route
 * comes back to its workspace. The order of v16's events is what makes that delicate
 * (router.js route(): a NEW route array, render(), then trigger("change"); the blocks of
 * a render draw asynchronously after that), so this runs the helper against a stand-in
 * router that fires "change" the way v16 does, and checks:
 *
 *   - coming back to the same workspace reloads each block on it, once;
 *   - a fresh render does not load a block twice, whichever side of "change" it registers;
 *   - a block no longer in the document is dropped, not merely skipped;
 *   - one block's throwing (or rejecting) load does not stop the others, or the router;
 *   - a route that is not this workspace does nothing;
 *   - no router, or no frappe at all, is a no-op, not an exception.
 *
 * WHY EVERY BLOCK TOO
 * ===================
 *
 * Adopting the helper was one edit repeated in some forty files, each of which could
 * hand it the wrong function, a function that asks nothing, or register before its
 * DOM exists. So part two runs each registering block's REAL script against a
 * stand-in desk (DOM nodes that accept anything, a frappe.call that answers at once),
 * and counts what it asks the server: leaving for a form asks nothing, coming back
 * asks again, and after the page is rendered again only the new block reloads.
 *
 * Run: node scripts/test_workspace_block_return.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const HELPER = path.join(APP, "public", "js", "global_enhancements", "workspace_block_return.js");
const BLOCKS_DIR = path.join(APP, "custom_html_blocks");
const HELPER_SRC = fs.readFileSync(HELPER, "utf8");

let failures = 0;
let checks = 0;
const unhandled = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));

async function test(name, fn) {
	checks += 1;
	try {
		await fn();
		console.log("  ok   " + name);
	} catch (err) {
		failures += 1;
		console.log("  FAIL " + name);
		console.log("       " + (err && err.message ? err.message : String(err)));
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle(rounds = 8) {
	for (let i = 0; i < rounds; i += 1) await tick();
}

const quiet = { log() {}, info() {}, warn() {}, error() {}, debug() {} };

// ------------------------------------------------------------------ a stand-in desk

/**
 * frappe with a router that behaves like v16's for what the helper reads: get_route()
 * returns the current route ARRAY, a navigation makes a NEW array, and "change" fires
 * after it (router.js route(): `this.current_route = await this.parse()`, render(),
 * trigger("change")). Handlers run in turn and, as under jQuery, an exception from one
 * propagates out of the trigger and stops the rest.
 */
function makeDesk(options = {}) {
	const handlers = [];
	const desk = { route: ["Workspaces", "Home"] };
	const sandbox = { console: quiet, setTimeout, clearTimeout, Promise };
	sandbox.window = sandbox;
	if (options.frappe !== false) {
		const frappe = {
			provide(namespace) {
				return namespace.split(".").reduce((obj, key) => (obj[key] = obj[key] || {}), sandbox);
			},
			get_route: () => desk.route,
		};
		if (options.router !== false) {
			frappe.router = { on: (evt, fn) => handlers.push([evt, fn]) };
		}
		sandbox.frappe = frappe;
	}
	Object.assign(sandbox, options.globals || {});
	vm.createContext(sandbox);
	vm.runInContext(HELPER_SRC, sandbox, { filename: HELPER });
	desk.sandbox = sandbox;
	desk.handlers = handlers;
	desk.api = sandbox.erpnext_enhancements && sandbox.erpnext_enhancements.workspace_blocks;
	desk.setRoute = (route) => {
		desk.route = route.slice();
	};
	desk.fire = () => {
		for (const [evt, fn] of handlers) if (evt === "change") fn(sandbox.frappe.router);
	};
	desk.navigate = (route) => {
		desk.setRoute(route);
		desk.fire();
	};
	desk.evalHelperAgain = () => vm.runInContext(HELPER_SRC, sandbox, { filename: HELPER });
	return desk;
}

/** A block as the helper sees it: a shadow root whose host is (or is not) in the document. */
function makeBlock(desk, options = {}) {
	const root = { host: { isConnected: true }, loads: 0, seen: [] };
	const load = (arg) => {
		root.loads += 1;
		root.seen.push(arg);
		if (options.throws) throw new Error("this block's load is broken");
		if (options.rejects) return Promise.reject(new Error("this block's answer failed"));
		return undefined;
	};
	root.load = load;
	// A block loads itself on its first run, then registers (every block in this app).
	root.start = () => {
		load(root);
		return desk.api.onWorkspaceReturn(root, load);
	};
	return root;
}

const FORM = ["Form", "Project", "PROJ-0001"];

// ================================================================== part one: the helper

console.log("workspace return helper");

await test("it exposes onWorkspaceReturn on erpnext_enhancements.workspace_blocks", () => {
	const desk = makeDesk();
	assert.equal(typeof desk.api.onWorkspaceReturn, "function");
	assert.equal(desk.handlers.length, 0, "binds nothing until a block registers");
});

await test("coming back to the same workspace reloads each block on it, once", () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "Home"]);
	const a = makeBlock(desk);
	const b = makeBlock(desk);
	assert.equal(a.start(), true);
	assert.equal(b.start(), true);
	assert.deepEqual([a.loads, b.loads], [1, 1], "each loaded itself on the render");
	desk.navigate(FORM);
	assert.deepEqual([a.loads, b.loads], [1, 1], "leaving for a form asks nothing");
	desk.navigate(["Workspaces", "Home"]);
	assert.deepEqual([a.loads, b.loads], [2, 2], "coming back reloads each block once");
	desk.navigate(["List", "Task", "List"]);
	desk.navigate(["Workspaces", "Home"]);
	assert.deepEqual([a.loads, b.loads], [3, 3], "and again on the next return");
	assert.equal(a.seen[a.seen.length - 1], a, "load is handed the block's own root");
	assert.equal(desk.handlers.length, 1, "one router handler, however many blocks");
});

await test("a private workspace's route counts as that workspace", () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "private", "Mine"]);
	const a = makeBlock(desk);
	a.start();
	desk.navigate(FORM);
	desk.navigate(["Workspaces", "private", "Mine"]);
	assert.equal(a.loads, 2);
});

await test("a fresh render loads once: the block registers after its own change (v16's order)", () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "Home"]);
	const home = makeBlock(desk);
	home.start();
	// To Travel: "change" fires while Travel's blocks are still being drawn.
	desk.navigate(["Workspaces", "Travel"]);
	const travel = makeBlock(desk);
	travel.start();
	assert.equal(travel.loads, 1, "the new block's own load is the only one");
	assert.equal(home.loads, 1, "Home's block, still in the document, was not reloaded for Travel");
});

await test("a fresh render loads once even if a block registers BEFORE its change", () => {
	// Not v16's order today; the guard that keeps it from costing a second request.
	const desk = makeDesk();
	desk.setRoute(["Workspaces", "Home"]);
	const a = makeBlock(desk);
	a.start();
	desk.fire();
	assert.equal(a.loads, 1, "registered under the route still current: same navigation");
	desk.navigate(FORM);
	desk.navigate(["Workspaces", "Home"]);
	assert.equal(a.loads, 2, "and the next real return still reloads it");
});

await test("a block no longer in the document is dropped, not merely skipped", () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "Home"]);
	const old = makeBlock(desk);
	old.start();
	// The workspace renders the page again: the old block leaves, a new one runs.
	old.host.isConnected = false;
	desk.navigate(["Workspaces", "Other"]);
	desk.navigate(["Workspaces", "Home"]);
	const fresh = makeBlock(desk);
	fresh.start();
	desk.navigate(FORM);
	desk.navigate(["Workspaces", "Home"]);
	assert.equal(old.loads, 1, "the replaced block is never reloaded");
	assert.equal(fresh.loads, 2, "the block on the page is");
	// Were it only skipped, a host that reads connected again would bring it back.
	old.host.isConnected = true;
	desk.navigate(FORM);
	desk.navigate(["Workspaces", "Home"]);
	assert.equal(old.loads, 1, "it was dropped from the registry");
});

await test("registering the same root again replaces it rather than adding a second", () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "Home"]);
	const a = makeBlock(desk);
	a.start();
	a.start();
	desk.navigate(FORM);
	desk.navigate(["Workspaces", "Home"]);
	assert.equal(a.loads, 3, "two self-loads, then ONE reload");
});

await test("a throwing or rejecting load does not stop the others, or the router", async () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "Home"]);
	// Registered first, so the broken ones run before the good one.
	const throws = makeBlock(desk, { throws: true });
	const rejects = makeBlock(desk, { rejects: true });
	const fine = makeBlock(desk);
	assert.equal(desk.api.onWorkspaceReturn(throws, throws.load), true);
	assert.equal(desk.api.onWorkspaceReturn(rejects, rejects.load), true);
	fine.start();
	const before = unhandled.length;
	desk.navigate(FORM);
	assert.doesNotThrow(() => desk.navigate(["Workspaces", "Home"]), "an exception reached the router");
	await settle();
	assert.equal(throws.loads, 1);
	assert.equal(rejects.loads, 1);
	assert.equal(fine.loads, 2, "the block after a broken one still reloaded");
	assert.equal(unhandled.length, before, "a rejected reload was left unhandled");
});

await test("a route that is not this workspace does nothing", () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "Home"]);
	const a = makeBlock(desk);
	a.start();
	for (const route of [FORM, ["List", "Task", "List"], ["plan-a-trip"], [""], ["Workspaces"], ["Workspaces", "Travel"]]) {
		desk.navigate(route);
	}
	assert.equal(a.loads, 1);
});

await test("a block that registers after you left is filed under the workspace view's page", () => {
	const desk = makeDesk();
	desk.sandbox.frappe.workspace = { _page: { name: "Home" } };
	desk.navigate(["Workspaces", "Home"]);
	desk.navigate(FORM);
	const late = makeBlock(desk);
	late.start();
	desk.navigate(["Workspaces", "Home"]);
	assert.equal(late.loads, 2);
});

await test("onScreen: a hidden or detached block is off screen, and what it cannot judge fails open", () => {
	// One copy of the check the polling blocks ask before each fetch (v1.641.0).
	const desk = makeDesk();
	const onScreen = desk.api.onScreen;
	assert.equal(typeof onScreen, "function", "not exported on erpnext_enhancements.workspace_blocks");
	const el = (connected, rects) => ({ isConnected: connected, getClientRects: () => ({ length: rects }) });
	assert.equal(onScreen({ host: el(true, 1) }), true, "a block on screen");
	assert.equal(onScreen({ host: el(true, 0) }), false, "a block under a display:none ancestor");
	assert.equal(onScreen({ host: el(false, 1) }), false, "a detached block");
	assert.equal(onScreen(el(true, 1)), true, "an element answers for itself");
	assert.equal(onScreen(el(true, 0)), false, "a hidden element");
	const anchor = { host: "erp.example.com", isConnected: true, getClientRects: () => [] };
	assert.equal(onScreen(anchor), false, "a string `host` (an anchor's) is not taken for a shadow host");
	assert.equal(onScreen({ host: { isConnected: true } }), true, "a host without layout APIs fails open");
	assert.equal(onScreen({ querySelector() {} }), true, "no host (a block's document fallback) fails open");
	assert.equal(onScreen(null), true, "nothing at all fails open");
	assert.equal(onScreen(undefined), true, "nothing at all fails open");
	const broken = {
		host: {
			isConnected: true,
			getClientRects() {
				throw new Error("layout");
			},
		},
	};
	assert.equal(onScreen(broken), true, "a throwing layout read fails open");
});

await test("no router: a no-op that says so, not an exception", () => {
	const desk = makeDesk({ router: false });
	const a = makeBlock(desk);
	assert.equal(a.start(), false);
	assert.equal(desk.handlers.length, 0);
});

await test("no frappe at all: the file evaluates and defines nothing", () => {
	const desk = makeDesk({ frappe: false });
	assert.equal(desk.api, undefined);
});

await test("bad arguments are refused, not thrown", () => {
	const desk = makeDesk();
	assert.equal(desk.api.onWorkspaceReturn(null, () => {}), false);
	assert.equal(desk.api.onWorkspaceReturn({ host: { isConnected: true } }, "load"), false);
});

await test("evaluated twice, it keeps one registry and one handler", () => {
	const desk = makeDesk();
	desk.navigate(["Workspaces", "Home"]);
	const a = makeBlock(desk);
	a.start();
	desk.evalHelperAgain();
	const b = makeBlock(desk);
	b.start();
	desk.navigate(FORM);
	desk.navigate(["Workspaces", "Home"]);
	assert.equal(desk.handlers.length, 1);
	assert.deepEqual([a.loads, b.loads], [2, 2]);
});

await test("a /desk link inside a block routes in place; the ones Desk leaves alone stay alone", () => {
	// router.js's body delegate never sees an anchor inside a shadow root, so the helper
	// routes them, by that delegate's rules (router.js:25-70).
	const routes = [];
	const desk = makeDesk({ globals: { location: { hostname: "erp.example.com" }, URLSearchParams } });
	desk.sandbox.frappe.router.is_app_route = (path) => String(path || "").split("/")[1] === "desk";
	desk.sandbox.frappe.set_route = (route) => routes.push(route);
	const listeners = [];
	const root = { host: { isConnected: true }, addEventListener: (type, fn) => listeners.push([type, fn]) };
	desk.api.onWorkspaceReturn(root, () => {});
	desk.api.onWorkspaceReturn(root, () => {});
	assert.equal(listeners.length, 1, "registering a root twice must not add a second click listener");
	assert.equal(listeners[0][0], "click");
	const anchor = (href, attrs = {}, at = {}) => {
		const url = new URL(href, "https://erp.example.com/desk/home");
		return {
			getAttribute: (name) => (name === "href" ? href : attrs[name] == null ? null : attrs[name]),
			hasAttribute: (name) => name in attrs,
			hostname: at.hostname || url.hostname,
			pathname: url.pathname,
			search: url.search,
			hash: url.hash,
		};
	};
	const click = (a, extra = {}) => {
		let prevented = false;
		const event = Object.assign(
			{ button: 0, target: { closest: () => a }, preventDefault: () => (prevented = true) },
			extra,
		);
		listeners[0][1](event);
		return prevented;
	};

	assert.equal(click(anchor("/desk/task/TASK-1")), true);
	assert.deepEqual(routes, ["/desk/task/TASK-1"]);
	assert.equal(click(anchor("/desk/task?status=Open")), true);
	assert.equal(desk.sandbox.frappe.route_options.status, "Open", "a query string becomes route_options");
	const before = routes.length;
	assert.equal(click(anchor("/desk/task/X", { target: "_blank" })), false, "a new-tab link is the browser's");
	assert.equal(click(anchor("/desk/task/X"), { ctrlKey: true }), false, "ctrl-click opens a tab");
	assert.equal(click(anchor("#tdb-top")), false, "a bare hash is not a route");
	assert.equal(click(anchor("/kiosk")), false, "a www page is a full load");
	assert.equal(click(anchor("https://example.org/x", {}, { hostname: "example.org" })), false, "external");
	assert.equal(click(null), false, "a click on no link");
	assert.equal(routes.length, before, "none of those routed");
});

// ============================================================ part two: every block

/**
 * An object that accepts anything a block does to a DOM node, a jQuery set or a
 * ColumnSelector: any property is itself, any call returns itself, and whatever is
 * assigned reads back (so `refresh.disabled` means what the block last set). It is
 * not a thenable, and it iterates as empty.
 */
function anything() {
	const store = new Map();
	const target = function () {};
	const proxy = new Proxy(target, {
		get(_, prop) {
			if (store.has(prop)) return store.get(prop);
			if (prop === "then") return undefined;
			if (prop === Symbol.toPrimitive) return () => "";
			if (prop === Symbol.iterator) return function* () {};
			if (typeof prop === "symbol") return undefined;
			if (prop === "length") return 0;
			if (prop === "toString" || prop === "valueOf") return () => "";
			return proxy;
		},
		set(_, prop, value) {
			store.set(prop, value);
			return true;
		},
		apply: () => proxy,
		construct: () => proxy,
	});
	return proxy;
}

// Answers a block needs before it goes on to load anything: the KPI Cockpit lists the
// viewer's departments first, and registers only once it has one.
const ANSWERS = {
	"erpnext_enhancements.api.kpi.visible_departments": ["Finance"],
};

function makeBlockDesk() {
	const calls = [];
	const desk = makeDesk({
		globals: {
			__: (text) => text,
			document: { hidden: false, querySelector: () => null },
			localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
			setInterval: () => 0,
			clearInterval() {},
			innerWidth: 1280,
			$: anything(),
			fetch: (url) => {
				calls.push("fetch " + url);
				return Promise.resolve({ json: () => ({}) });
			},
		},
	});
	const frappe = desk.sandbox.frappe;
	frappe.call = (opts) => {
		const method = typeof opts === "string" ? opts : opts && opts.method;
		calls.push(method);
		const r = { message: Object.prototype.hasOwnProperty.call(ANSWERS, method) ? ANSWERS[method] : null };
		if (opts && typeof opts.callback === "function") setImmediate(() => opts.callback(r));
		return Promise.resolve(r);
	};
	frappe.xcall = (method) => {
		calls.push(method);
		return Promise.resolve(null);
	};
	frappe.require = (assets, callback) => setImmediate(callback);
	frappe.utils = { escape_html: (value) => String(value), debounce: (fn) => fn, icon: () => "" };
	frappe.realtime = { on() {}, off() {} };
	frappe.format = (value) => String(value);
	frappe.markdown = (value) => String(value);
	frappe.datetime = { str_to_user: (value) => String(value), get_today: () => "2026-09-28" };
	frappe.boot = {};
	frappe.show_alert = () => {};
	frappe.set_route = () => {};
	frappe.new_doc = () => {};
	// What ships alongside the Projects Dashboard in the desk bundle.
	desk.sandbox.erpnext_enhancements.dashboard_components = {
		ColumnSelector: anything(),
		ColumnResizer: anything(),
	};
	desk.sandbox.erpnext_enhancements.gantt = anything();
	desk.calls = calls;
	return desk;
}

/** Run a block's real script the way create_shadow_element does: a fresh root, once. */
function render(desk, file, source) {
	const root = { host: { isConnected: true }, querySelector: () => anything(), querySelectorAll: () => [] };
	const script = new vm.Script("(function (root_element) {\n" + source + "\n})", { filename: file });
	script.runInContext(desk.sandbox)(root);
	return root;
}

function stripComments(source) {
	return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'\\])\/\/[^\n]*/g, "$1");
}

const registering = fs
	.readdirSync(BLOCKS_DIR)
	.filter((name) => name.endsWith(".js"))
	.filter((name) => stripComments(fs.readFileSync(path.join(BLOCKS_DIR, name), "utf8")).includes("onWorkspaceReturn("))
	.sort();

console.log("\nevery block that registers (" + registering.length + ")");

await test("the blocks that register are found (a scanner that finds none proves nothing)", () => {
	assert.ok(registering.length >= 35, "only " + registering.length + " blocks register");
	assert.ok(registering.includes("travel_home.js"));
	assert.ok(registering.includes("task_dashboard.js"));
});

for (const file of registering) {
	const source = fs.readFileSync(path.join(BLOCKS_DIR, file), "utf8");
	await test(file + ": leaving asks nothing, coming back asks again, only the block on the page", async () => {
		const desk = makeBlockDesk();
		const HOME = ["Workspaces", "Dashboard"];
		desk.navigate(HOME);
		const first = render(desk, file, source);
		await settle(20);
		const firstLoad = desk.calls.length;
		assert.ok(firstLoad >= 1, "its first run asked the server nothing");
		assert.equal(desk.handlers.length, 1, "it did not register (or bound a handler of its own)");

		desk.navigate(FORM);
		await settle(20);
		assert.equal(desk.calls.length, firstLoad, "leaving for a form asked the server again");

		desk.navigate(HOME);
		await settle(20);
		const reload = desk.calls.length - firstLoad;
		assert.ok(reload >= 1, "coming back asked the server nothing: the stale-dashboard bug");

		desk.navigate(["Workspaces", "Elsewhere"]);
		await settle(20);
		assert.equal(desk.calls.length, firstLoad + reload, "another workspace reloaded this one");

		// The page rendered again: the old block leaves, the new one loads itself.
		first.host.isConnected = false;
		desk.navigate(HOME);
		render(desk, file, source);
		await settle(20);
		const beforeReturn = desk.calls.length;
		desk.navigate(FORM);
		desk.navigate(HOME);
		await settle(20);
		assert.equal(desk.calls.length - beforeReturn, reload, "the replaced block was reloaded too");
		assert.equal(desk.handlers.length, 1, "a second render bound a second handler");
	});
}

// Bank Balances' Refresh spends one Plaid call per linked bank and has no lock. A return
// that ran load() mid-refresh would switch the button back on, so a second click could
// start a second billed refresh while the first was still out.
await test("finance_bank_balances.js: a return while Refresh is out reads nothing and keeps Refresh off", async () => {
	const file = "finance_bank_balances.js";
	const source = fs.readFileSync(path.join(BLOCKS_DIR, file), "utf8");
	const desk = makeBlockDesk();
	const plain = desk.sandbox.frappe.call;
	let finishRefresh = null;
	desk.sandbox.frappe.call = (opts) => {
		if (opts && /\.refresh_now$/.test(opts.method)) {
			desk.calls.push(opts.method);
			return new Promise((resolve) => {
				finishRefresh = () => resolve({ message: { ok: true } });
			});
		}
		return plain(opts);
	};
	const button = {
		disabled: false,
		listeners: {},
		addEventListener(type, fn) {
			this.listeners[type] = fn;
		},
	};
	const HOME = ["Workspaces", "Finance"];
	desk.navigate(HOME);
	const root = {
		host: { isConnected: true },
		querySelector: (selector) => (selector === "#fbb-refresh" ? button : anything()),
		querySelectorAll: () => [],
	};
	new vm.Script("(function (root_element) {\n" + source + "\n})", { filename: file }).runInContext(desk.sandbox)(root);
	await settle(20);
	assert.equal(button.disabled, false, "the first load left Refresh switched off");
	assert.equal(typeof button.listeners.click, "function", "Refresh has no click handler");

	button.listeners.click();
	assert.equal(button.disabled, true, "Refresh did not switch itself off");
	const mid = desk.calls.length;
	desk.navigate(FORM);
	desk.navigate(HOME);
	await settle(20);
	assert.equal(desk.calls.length, mid, "a return mid-refresh asked the server again");
	assert.equal(button.disabled, true, "a return mid-refresh switched Refresh back on");

	finishRefresh();
	await settle(20);
	assert.equal(button.disabled, false, "Refresh stayed off after the refresh finished");
	const after = desk.calls.length;
	desk.navigate(FORM);
	desk.navigate(HOME);
	await settle(20);
	assert.ok(desk.calls.length > after, "a return after the refresh asked nothing: the stale-dashboard bug");
	assert.ok(!desk.calls.slice(after).some((m) => /\.refresh_now$/.test(m)), "a return spent a Plaid call");
});

// The two polling blocks ask the helper's onScreen before a timer fetches, and a device
// whose bundle predates it (no onScreen) must keep polling: off screen is only skipped when
// something can say so. Each case renders the block on a host whose layout says shown or
// hidden, then fires the block's own poll timer and counts what it asks the server.
const POLLERS = [
	// The Task Dashboard's 5-minute interval goes through refresh_if_visible.
	{ file: "task_dashboard.js", ms: 5 * 60 * 1000, prime() {} },
	// Who's Working's 15 s heartbeat loads once the minute is up: make it up.
	{
		file: "finance_whos_working.js",
		ms: 15000,
		prime(desk) {
			desk.sandbox.__ee_fww.last = 0;
		},
	},
];
for (const poller of POLLERS) {
	const source = fs.readFileSync(path.join(BLOCKS_DIR, poller.file), "utf8");
	for (const [label, helper, shown, asks] of [
		["on screen, polls", true, true, true],
		["hidden, skips the poll", true, false, false],
		["hidden, with an older bundle (no onScreen), still polls", false, false, true],
	]) {
		await test(poller.file + ": " + label, async () => {
			const desk = makeBlockDesk();
			const timers = [];
			desk.sandbox.setInterval = (fn, ms) => timers.push([fn, ms]);
			if (!helper) delete desk.api.onScreen;
			desk.navigate(["Workspaces", "Dashboard"]);
			const host = { isConnected: true, getClientRects: () => ({ length: shown ? 1 : 0 }) };
			const root = { host, querySelector: () => anything(), querySelectorAll: () => [] };
			new vm.Script("(function (root_element) {\n" + source + "\n})", { filename: poller.file }).runInContext(
				desk.sandbox,
			)(root);
			await settle(20);
			const poll = timers.find(([, ms]) => ms === poller.ms);
			assert.ok(poll, "no " + poller.ms + " ms timer was started");
			poller.prime(desk);
			const before = desk.calls.length;
			poll[0]();
			await settle(20);
			assert.equal(desk.calls.length > before, asks, asks ? "the poll asked nothing" : "a hidden block polled");
		});
	}
}

await settle(20);
await test("no block left a promise rejection unhandled", () => {
	assert.deepEqual(unhandled.map((r) => String(r && r.message ? r.message : r)), []);
});

console.log("\n" + (checks - failures) + "/" + checks + " passed");
if (failures) process.exit(1);
