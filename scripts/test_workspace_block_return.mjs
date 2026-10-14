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
import { fileURLToPath, pathToFileURL } from "node:url";
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

await test("ticket: only the newest ticket on a root is current, and roots do not share one", () => {
	const desk = makeDesk();
	const ticket = desk.api.ticket;
	assert.equal(typeof ticket, "function", "not exported on erpnext_enhancements.workspace_blocks");
	const a = {};
	const b = {};
	const first = ticket(a);
	assert.equal(first(), true, "a lone ticket is the newest");
	const second = ticket(a);
	assert.equal(first(), false, "an older answer must not draw");
	assert.equal(second(), true);
	const other = ticket(b);
	assert.equal(second(), true, "another root's ticket does not retire this one");
	assert.equal(other(), true);
	assert.equal(ticket(null)(), true, "no root: every answer draws, nothing throws");
});

// A CSSStyleSheet that records its text, for the adoption checks.
class FakeSheet {
	replaceSync(text) {
		this.text = text;
	}
}

function kitDesk(require, globals = {}) {
	const desk = makeDesk({ globals: Object.assign({ CSSStyleSheet: FakeSheet }, globals) });
	desk.requires = [];
	if (require) {
		desk.sandbox.frappe.require = (assets, callback) => {
			desk.requires.push(assets);
			return require(assets, callback, desk);
		};
	}
	return desk;
}

const shadowRoot = () => ({ host: { isConnected: true }, adoptedStyleSheets: [] });

await test("kit: a failed load (v16 calls back anyway) resolves a plain-text stand-in, and asks once", async () => {
	const desk = kitDesk((assets, callback) => setImmediate(callback));
	const roots = [shadowRoot(), shadowRoot(), shadowRoot()];
	const kits = await Promise.all(roots.map((root) => desk.api.kit(root)));
	await desk.api.kit(roots[0]);
	// JSON, not deepEqual: the array was built in the desk's realm.
	assert.equal(JSON.stringify(desk.requires), '[["sf_kit.bundle.js"]]', "one frappe.require per page load, memoized");
	const sf = kits[0];
	assert.ok(kits.every((kit) => kit === sf), "one stand-in");
	assert.equal(sf.stand_in, true);
	assert.equal(sf.esc(`<b x='1'>&\``), "&lt;b x=&#39;1&#39;&gt;&amp;&#96;");
	assert.equal(sf.esc(null), "");
	assert.equal(sf.esc(0), "0");
	assert.equal(sf.chip(" <i>Open</i> ", "warn"), "&lt;i&gt;Open&lt;/i&gt;", "a chip is its escaped word");
	const el = { textContent: "" };
	sf.state.render(el, "error", { title: "Could not load <x>.", on_action() {} });
	assert.equal(el.textContent, "Could not load <x>.", "plain text: textContent, never markup");
	sf.state.render(el, "no-access");
	assert.equal(el.textContent, "You do not have access to this.");
	sf.state.render(el, "loading");
	assert.equal(el.textContent, "Loading…");
	assert.equal(sf.state.render(null, "loading"), null, "no element: nothing, not a throw");

	// The body as text and a plain Try again: without it a failed block with no refresh
	// button (Weather, Astrology) could never retry while the kit is missing.
	const nodes = [];
	const doc = {
		createTextNode: (text) => ({ text }),
		createElement: (tag) => ({
			tag,
			listeners: {},
			addEventListener(type, fn) {
				this.listeners[type] = fn;
			},
		}),
	};
	const panel = { textContent: "", ownerDocument: doc, appendChild: (node) => nodes.push(node) };
	let retried = 0;
	sf.state.render(panel, "error", { title: "Could not load.", body: "The <server> said no.", on_action: () => retried++ });
	assert.equal(panel.textContent, "Could not load.");
	assert.equal(nodes[0].text, " The <server> said no.", "the body, as a text node");
	const button = nodes.find((node) => node.tag === "button");
	assert.ok(button, "no button for on_action");
	assert.equal(button.textContent, "Try again");
	assert.equal(button.type, "button");
	button.listeners.click({ preventDefault() {} });
	assert.equal(retried, 1, "the button does not call on_action");
	nodes.length = 0;
	sf.state.render(panel, "error", { title: "x", action_label: "Reload", on_action() {} });
	assert.equal(nodes.find((node) => node.tag === "button").textContent, "Reload", "action_label is the label");
	nodes.length = 0;
	sf.state.render(panel, "empty", { title: "Nothing." });
	assert.equal(nodes.length, 0, "no action, no body: the title alone");
	assert.ok(roots.every((root) => root.adoptedStyleSheets.length === 0), "a stand-in adopts no styles");
});

await test("kit: a loaded kit is returned, its CSS adopted once per shadow root and never into a document", async () => {
	const kit = {
		esc: (v) => String(v),
		chip: (v) => String(v),
		state: { render() {} },
		CSS: ".sf-state { display: flex; }",
	};
	const desk = kitDesk((assets, callback, d) => {
		setImmediate(() => {
			d.sandbox.sf_kit = kit;
			callback();
		});
	});
	const a = shadowRoot();
	const b = shadowRoot();
	const doc = { adoptedStyleSheets: [] };
	assert.equal(await desk.api.kit(a), kit);
	assert.equal(await desk.api.kit(a), kit);
	assert.equal(await desk.api.kit(b), kit);
	assert.equal(await desk.api.kit(doc), kit);
	assert.equal(a.adoptedStyleSheets.length, 1, "adopted once per root, however often it asks");
	assert.equal(a.adoptedStyleSheets[0].text, kit.CSS);
	assert.equal(b.adoptedStyleSheets[0], a.adoptedStyleSheets[0], "one constructed sheet for every block");
	assert.equal(doc.adoptedStyleSheets.length, 0, "a block that fell back to document styles nothing");
	assert.equal(desk.requires.length, 1);
});

await test("kit: a kit already on the page is used without a require; no frappe.require is a stand-in", async () => {
	const kit = { esc: String, chip: String, state: { render() {} } };
	const present = kitDesk(() => assert.fail("required a kit the page already had"));
	present.sandbox.sf_kit = kit;
	assert.equal(await present.api.kit(shadowRoot()), kit);
	const bare = kitDesk(null);
	delete bare.sandbox.frappe.require;
	assert.equal((await bare.api.kit(shadowRoot())).stand_in, true);
});

await test("kit: a require that never calls back still resolves (the ten-second wait), and a throw too", async () => {
	const silent = kitDesk(() => undefined, {
		setTimeout: (fn, ms) => (ms === 10000 ? setImmediate(fn) : setTimeout(fn, ms)),
	});
	assert.equal((await silent.api.kit(shadowRoot())).stand_in, true);
	const throws = kitDesk(() => {
		throw new Error("assets.js broke");
	});
	assert.equal((await throws.api.kit(shadowRoot())).stand_in, true);
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

// ============================================== part three: the blocks on the real kit

/**
 * The blocks on sf_kit draw their panels with the REAL kit: sf_kit.bundle.js imported the way
 * scripts/test_sf_kit.mjs imports it (it installs window.sf_kit), then handed to each block's
 * desk as `sf_kit`, which is what blocks.kit() resolves to on a page. The body a block draws
 * into keeps its markup, so the panel itself is checked: a 500 is an error panel with Try again
 * (and Try again asks again), a 403 is no access, an empty or switched-off answer the empty one.
 * And on a bundle from before blocks.kit, the block still draws, as plain text.
 */
async function importRealKit() {
	globalThis.window = { setTimeout, clearTimeout, console };
	globalThis.document = { cookie: "", body: null, head: null };
	try {
		await import(pathToFileURL(path.join(APP, "public", "js", "sf_kit.bundle.js")).href);
		const sf = globalThis.window.sf_kit;
		assert.ok(sf && sf.state && typeof sf.chip === "function", "sf_kit.bundle.js installed no kit");
		return sf;
	} finally {
		delete globalThis.window;
		delete globalThis.document;
	}
}

const REAL_KIT = await importRealKit();

/** An element that keeps what is written into it, enough for state.render and its button. */
function element() {
	let html = "";
	let text = "";
	let button = null;
	const el = {
		children: [],
		ownerDocument: FAKE_DOC,
		disabled: false,
		listeners: {},
		style: {},
		addEventListener(type, fn) {
			(el.listeners[type] = el.listeners[type] || []).push(fn);
		},
		querySelector(selector) {
			if (selector !== ".sf-state-action" || !html.includes("sf-state-action")) return null;
			if (!button) button = element();
			return button;
		},
		querySelectorAll: () => [],
		appendChild(node) {
			el.children.push(node);
			return node;
		},
		click() {
			for (const fn of el.listeners.click || []) fn({ preventDefault() {} });
		},
	};
	Object.defineProperty(el, "innerHTML", {
		get: () => html,
		set: (value) => {
			html = String(value);
			button = null;
			el.children = [];
		},
	});
	// As in a browser, writing textContent replaces whatever was appended.
	Object.defineProperty(el, "textContent", {
		get: () => text,
		set: (value) => {
			text = String(value);
			el.children = [];
		},
	});
	return el;
}

const FAKE_DOC = {
	createElement: (tag) => Object.assign(element(), { tag }),
	createTextNode: (value) => ({ text: value }),
};

const onKit = fs
	.readdirSync(BLOCKS_DIR)
	.filter((name) => name.endsWith(".js"))
	.filter((name) => stripComments(fs.readFileSync(path.join(BLOCKS_DIR, name), "utf8")).includes("blocks.kit("))
	.sort();

console.log("\nevery block on the kit (" + onKit.length + "), drawn with the real sf_kit");

await test("the blocks on the kit are found", () => {
	assert.ok(onKit.length >= 34, "only " + onKit.length + " blocks use blocks.kit");
	assert.ok(onKit.includes("sales_stalled_deals.js") && onKit.includes("finance_whos_working.js"));
});

/** Run a block on the real kit (or, with `old`, on a bundle from before blocks.kit). */
async function runOnKit(file, answer, options = {}) {
	const source = fs.readFileSync(path.join(BLOCKS_DIR, file), "utf8");
	const desk = makeBlockDesk();
	desk.sandbox.CSSStyleSheet = FakeSheet;
	if (!options.standIn) desk.sandbox.sf_kit = REAL_KIT;
	if (options.old) {
		delete desk.api.kit;
		delete desk.api.ticket;
	}
	desk.sandbox.frappe.call = (opts) => {
		desk.calls.push(opts && opts.method);
		return answer();
	};
	desk.navigate(["Workspaces", "Dashboard"]);
	const body = element();
	const refresh = element();
	const root = {
		host: { isConnected: true },
		adoptedStyleSheets: [],
		querySelector: (selector) =>
			/-body$/.test(selector) ? body : /-refresh$/.test(selector) ? refresh : anything(),
		querySelectorAll: () => [],
	};
	new vm.Script("(function (root_element) {\n" + source + "\n})", { filename: file }).runInContext(desk.sandbox)(root);
	await settle(20);
	return { desk, body, refresh, root };
}

const fail = (status) => () => Promise.reject({ status, responseText: "" });

for (const file of onKit) {
	await test(file + ": a 500 is an error panel whose Try again asks again", async () => {
		const { desk, body, root } = await runOnKit(file, fail(500));
		assert.match(body.innerHTML, /sf-state--error/, "no error panel: " + body.innerHTML.slice(0, 120));
		assert.match(body.innerHTML, /role="alert"/);
		assert.match(body.innerHTML, /Try again/);
		assert.equal(root.adoptedStyleSheets.length, 1, "the kit's styles were not adopted into the block");
		const before = desk.calls.length;
		body.querySelector(".sf-state-action").click();
		await settle(20);
		assert.ok(desk.calls.length > before, "Try again asked the server nothing");
	});

	await test(file + ": a 403 is the no-access panel", async () => {
		const { body } = await runOnKit(file, fail(403));
		assert.match(body.innerHTML, /sf-state--no-access/, "no no-access panel: " + body.innerHTML.slice(0, 120));
		assert.doesNotMatch(body.innerHTML, /Try again/);
	});

	await test(file + ": an empty or switched-off answer is the empty panel", async () => {
		for (const message of [null, { enabled: false }]) {
			const { body } = await runOnKit(file, () => Promise.resolve({ message }));
			assert.match(body.innerHTML, /sf-state--empty/, JSON.stringify(message) + ": " + body.innerHTML.slice(0, 120));
		}
	});

	await test(file + ": on a bundle from before blocks.kit it still draws, as plain text", async () => {
		const failed = await runOnKit(file, fail(500), { old: true });
		assert.match(failed.body.textContent, /\S/, "a failed load drew nothing");
		assert.equal(failed.body.innerHTML, "", "the stand-in writes text, never markup");
		const off = await runOnKit(file, () => Promise.resolve({ message: null }), { old: true });
		assert.match(off.body.textContent, /turned off/i, "a switched-off block said nothing");
		// Words, never the raw kind: a 403 reads "No access", not "no-access".
		const refused = await runOnKit(file, fail(403), { old: true });
		assert.equal(refused.body.textContent, "No access");
		const waiting = await runOnKit(file, () => new Promise(() => {}), { old: true });
		assert.equal(waiting.body.textContent, "Loading…");
	});

	await test(file + ": when the kit did not load, a failed call still offers Try again", async () => {
		const { desk, body, root } = await runOnKit(file, fail(500), { standIn: true });
		assert.match(body.textContent, /\S/, "the stand-in drew no title");
		assert.equal(root.adoptedStyleSheets.length, 0, "a stand-in adopts no styles");
		const button = body.children.find((node) => node.tag === "button");
		assert.ok(button, "no Try again: a block with no refresh button could never retry");
		assert.equal(button.textContent, "Try again");
		const before = desk.calls.length;
		button.click();
		await settle(20);
		assert.ok(desk.calls.length > before, "Try again asked the server nothing");
	});
}

// An answer with nothing in it: the lists a department widget draws, all empty. Each block's
// own empty sentence, in the kit's empty panel. (The switched-off case above covers the rest:
// snapshot widgets, Weather, Bank Balances.)
const EMPTY_LISTS = {
	"sales_speed_to_lead.js": { leads: [] },
	"sales_stalled_deals.js": { deals: [], threshold_days: 14 },
	"design_handoff_readiness.js": { designs: [] },
	"executive_scorecard.js": { cards: [] },
	"finance_new_jobs.js": { jobs: [] },
	"finance_calendar.js": { events: [] },
	"finance_whos_working.js": { workers: [] },
	"service_day_board.js": { visits: [] },
};
for (const [file, lists] of Object.entries(EMPTY_LISTS)) {
	await test(file + ": an answer with nothing in it is the empty panel", async () => {
		const { body } = await runOnKit(file, () => Promise.resolve({ message: Object.assign({ enabled: true }, lists) }));
		assert.match(body.innerHTML, /sf-state--empty/, body.innerHTML.slice(0, 160));
		assert.doesNotMatch(body.innerHTML, /turned off/, "drew the switched-off sentence for an empty list");
	});
}

await settle(20);
await test("no block left a promise rejection unhandled", () => {
	assert.deepEqual(unhandled.map((r) => String(r && r.message ? r.message : r)), []);
});

console.log("\n" + (checks - failures) + "/" + checks + " passed");
if (failures) process.exit(1);
