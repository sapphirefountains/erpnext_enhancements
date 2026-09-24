#!/usr/bin/env node
/**
 * The Inspection Wizard's Back and Forward -- executed, not grepped.
 *
 * THE BUG
 * =======
 *
 * The wizard walks an inspection one section at a time, on a phone, and the phone's Back
 * jumped from ANY section straight to the list. Three things combined:
 *
 *   * the list click called `frappe.set_route("inspection-wizard", {inspection})`. v16 moves
 *     an object argument into `frappe.route_options` and pushes the bare path -- no query
 *     string -- so it pushed an entry identical to the list's own;
 *   * the section tabs and the Back/Next bar changed `section_index` and repainted, and
 *     never touched history;
 *   * `handle_route` read only `?inspection=`, which was never written, so the router's own
 *     "show" called render_list() and raced the record fetch the click had started.
 *
 * And the list nulled `this.doc` under a pending autosave, so `flush()` threw on
 * `this.doc.header.name`: the answer was lost, `saving` stayed true for the rest of the
 * session, and every later save on every inspection returned early. A save already in
 * flight was worse -- its retry went out under the NEXT inspection's name and lock.
 *
 * WHY A RUNNING TEST
 * ==================
 *
 * Every one of those failures looked fine to a static check: the code called set_route, it
 * had a flush, it had a retry. What matters is the ORDER things happen in when the router
 * calls back, so this loads the real inspection_wizard.js into a vm with a fake of the parts
 * of Frappe v16 it touches -- the router (push_state writes the path only, route() is async,
 * route_options is filled and never cleared, route_flags resets late), a browser history
 * stack, frappe.call with responses the test releases in any order, a fake clock, and just
 * enough jQuery to paint -- then presses Back and Forward.
 *
 * The router fake follows frappe origin/version-16 frappe/public/js/frappe/router.js.
 *
 * Run: node scripts/test_inspection_wizard_nav.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// Overridable so the suite can be pointed at an older copy of the page, to confirm it fails
// there: `INSPECTION_WIZARD_JS=/tmp/old.js node scripts/test_inspection_wizard_nav.mjs`.
const WIZARD_JS =
	process.env.INSPECTION_WIZARD_JS ||
	path.join(HERE, "..", "erpnext_enhancements", "quality", "page", "inspection_wizard", "inspection_wizard.js");
const SOURCE = fs.readFileSync(WIZARD_JS, "utf8");

const drain = async () => {
	for (let i = 0; i < 4; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

// ---------------------------------------------------------------------------
// Just enough jQuery. Markup is parsed flat -- every start tag becomes an element -- which is
// all `find(".class")` and `find("input")` need. The page is appended to, emptied, repainted
// and searched; nothing here lays anything out.
// ---------------------------------------------------------------------------

class El {
	constructor(tag, attrs, source) {
		this.tag = tag;
		this.attrs = attrs || {};
		this.source = source || "";
		this.classes = new Set((this.attrs.class || "").split(/\s+/).filter(Boolean));
		this.children = [];
		this.parent = null;
		this.handlers = {};
		this.props = {};
		this.markup = "";
		this.value = this.attrs.value !== undefined ? this.attrs.value : "";
		this.document = null;
	}
	get tagName() {
		return this.tag.toUpperCase();
	}
	contains(node) {
		for (let n = node; n; n = n.parent) if (n === this) return true;
		return false;
	}
	// A browser's order: "change" on the input, then "focusout", which bubbles.
	blur() {
		if (this.document && this.document.activeElement === this) this.document.activeElement = null;
		fire(this, "change");
		for (let n = this; n; n = n.parent) fire(n, "focusout", { currentTarget: n, target: this });
	}
}

function fire(el, type, event) {
	const payload = event || { currentTarget: el, target: el };
	for (const key of Object.keys(el.handlers)) {
		if (key === type || key.startsWith(type + ".")) {
			for (const fn of el.handlers[key]) fn(payload);
		}
	}
}

const TAG = /<([a-zA-Z][\w-]*)((?:\s+[\w-]+(?:="[^"]*")?)*)\s*\/?>/g;
const ATTR = /([\w-]+)(?:="([^"]*)")?/g;

function parse_tags(markup) {
	const out = [];
	for (const match of String(markup).matchAll(TAG)) {
		const attrs = {};
		for (const attr of (match[2] || "").matchAll(ATTR)) attrs[attr[1]] = attr[2] ?? "";
		out.push(new El(match[1].toLowerCase(), attrs));
	}
	return out;
}

function walk(el, fn) {
	for (const child of el.children) {
		fn(child);
		walk(child, fn);
	}
}

function detach(el) {
	if (!el.parent) return;
	el.parent.children = el.parent.children.filter((c) => c !== el);
	el.parent = null;
}

function matches(el, selector) {
	if (selector.startsWith(".")) return el.classes.has(selector.slice(1));
	return el.tag === selector;
}

class Q {
	constructor(els) {
		this.els = els.filter(Boolean);
		this[0] = this.els[0];
	}
	get length() {
		return this.els.length;
	}
	appendTo(target) {
		const parent = target.els[0];
		for (const el of this.els) {
			detach(el);
			el.parent = parent;
			parent.children.push(el);
		}
		return this;
	}
	html(markup) {
		if (markup === undefined) return this.els[0] ? this.els[0].markup : "";
		for (const el of this.els) {
			for (const child of el.children) child.parent = null;
			el.children = [];
			el.markup = String(markup);
			for (const child of parse_tags(el.markup)) {
				child.parent = el;
				el.children.push(child);
			}
		}
		return this;
	}
	empty() {
		return this.html("");
	}
	find(selector) {
		const out = [];
		for (const el of this.els) walk(el, (n) => matches(n, selector) && out.push(n));
		return new Q(out);
	}
	on(type, fn) {
		for (const el of this.els) (el.handlers[type] = el.handlers[type] || []).push(fn);
		return this;
	}
	remove() {
		for (const el of this.els) detach(el);
		return this;
	}
	not(other) {
		const skip = new Set(other.els);
		return new Q(this.els.filter((el) => !skip.has(el)));
	}
	prop(key, value) {
		for (const el of this.els) el.props[key] = value;
		return this;
	}
	addClass(name) {
		for (const el of this.els) el.classes.add(name);
		return this;
	}
	removeClass(name) {
		for (const el of this.els) el.classes.delete(name);
		return this;
	}
	toggleClass(name, on) {
		for (const el of this.els) {
			const want = on === undefined ? !el.classes.has(name) : !!on;
			if (want) el.classes.add(name);
			else el.classes.delete(name);
		}
		return this;
	}
	toggle() {
		return this;
	}
	data(key) {
		return this.els[0] ? this.els[0].attrs["data-" + key] : undefined;
	}
	val(value) {
		if (value === undefined) return this.els[0] ? this.els[0].value : undefined;
		for (const el of this.els) el.value = value;
		return this;
	}
}

// ---------------------------------------------------------------------------
// One fresh Desk per test: history, router, clock, server, page.
// ---------------------------------------------------------------------------

function boot() {
	const env = { calls: [], errors: [], dialogs_hidden: 0, confirms: [], messages: [] };

	// -- clock -------------------------------------------------------------
	const clock = { now: 0, seq: 0, timers: new Map() };
	clock.setTimeout = (fn, ms) => {
		clock.seq += 1;
		clock.timers.set(clock.seq, { at: clock.now + (ms || 0), fn });
		return clock.seq;
	};
	clock.clearTimeout = (id) => clock.timers.delete(id);
	env.advance = async (ms) => {
		const end = clock.now + ms;
		for (;;) {
			let next = null;
			for (const [id, timer] of clock.timers) {
				if (timer.at <= end && (!next || timer.at < next[1].at)) next = [id, timer];
			}
			if (!next) break;
			clock.timers.delete(next[0]);
			clock.now = next[1].at;
			next[1].fn();
			await drain();
		}
		clock.now = end;
		await drain();
	};

	// -- the browser -------------------------------------------------------
	const browser = { entries: [], index: -1 };
	const here = () => browser.entries[browser.index] || { pathname: "/desk", search: "" };
	env.browser = browser;
	env.url = () => here().pathname + here().search;

	const document = { activeElement: null };
	const window = {};

	// -- the page ----------------------------------------------------------
	const proxies = new WeakMap();
	const proxy_for = (obj) => {
		if (!proxies.has(obj)) proxies.set(obj, new El("object"));
		return proxies.get(obj);
	};
	const main = new El("div", { class: "layout-main-section" });
	main.document = document;
	const wrapper = {
		contains: (node) => main.contains(node),
	};

	const $ = (arg) => {
		if (arg instanceof Q) return arg;
		if (arg instanceof El) return new Q([arg]);
		if (typeof arg === "string") {
			const [root, ...rest] = parse_tags(arg);
			root.source = arg;
			root.document = document;
			for (const child of rest) {
				child.parent = root;
				child.document = document;
				root.children.push(child);
			}
			return new Q([root]);
		}
		return new Q([proxy_for(arg)]);
	};

	// -- the router, after frappe origin/version-16 router.js --------------
	let shown = null;
	let loaded = false;
	const router = { current_route: null };

	async function route() {
		// route() is async in v16: it awaits parse() before anything renders.
		await Promise.resolve();
		const loc = here();
		const sub = loc.pathname.replace(/^\/desk\/?/, "");
		const segments = sub ? sub.split("/").map((s) => decodeURIComponent(s)) : [];
		// set_route_options_from_url: fills route_options from the query, never clears it.
		if (!frappe.route_options) frappe.route_options = {};
		for (const [key, value] of new URLSearchParams(loc.search)) frappe.route_options[key] = value;
		router.current_route = segments;
		// set_history -> frappe.ui.hide_open_dialog()
		env.dialogs_hidden += 1;
		const page = segments[0] || "";
		if (shown === "inspection-wizard" && page !== "inspection-wizard") {
			fire(proxy_for(wrapper), "hide", { target: wrapper, currentTarget: wrapper });
		}
		shown = page;
		if (page === "inspection-wizard") {
			if (!loaded) {
				loaded = true;
				wrapper.on_page_load(wrapper);
			}
			wrapper.on_page_show(wrapper);
		}
	}
	const route_safely = () =>
		route().catch((error) => {
			env.errors.push(error);
		});

	function push_state(pathname, query) {
		const loc = here();
		if (loc.pathname !== pathname || loc.search !== query) {
			// The path only: v16 compares the query string and never writes it.
			const entry = { pathname, search: "" };
			if (frappe.route_flags.replace_route) {
				browser.entries[browser.index] = entry;
			} else {
				browser.entries = browser.entries.slice(0, browser.index + 1);
				browser.entries.push(entry);
				browser.index += 1;
			}
			route_safely();
		}
	}

	function set_route(...args) {
		let parts = args.length === 1 && Array.isArray(args[0]) ? args[0] : args;
		if (parts.length === 1 && typeof parts[0] === "string" && parts[0].includes("/")) {
			parts = parts[0].split("/").filter(Boolean);
		}
		if (parts[0] === "desk" || parts[0] === "app") parts = parts.slice(1);
		const segments = [];
		for (const part of parts) {
			if (part && typeof part === "object") frappe.route_options = part;
			else segments.push(encodeURIComponent(String(part)));
		}
		const pathname = "/desk/" + segments.join("/");
		const options = frappe.route_options || {};
		const query = Object.entries(options)
			.map(([key, value]) => `${key}=` + encodeURIComponent(JSON.stringify(value)))
			.join("&");
		push_state(pathname, query ? `?${query}` : "");
		// v16 resets route_flags in set_route's finally, 100ms and an ajax-idle later.
		clock.setTimeout(() => {
			frappe.route_flags = {};
		}, 100);
		return Promise.resolve();
	}

	// A typed URL or a full page load: a new entry, then the router.
	env.visit = async (url) => {
		const [pathname, search] = url.split("?");
		browser.entries = browser.entries.slice(0, browser.index + 1);
		browser.entries.push({ pathname, search: search ? `?${search}` : "" });
		browser.index += 1;
		await route_safely();
		await drain();
	};
	env.go = async (...args) => {
		frappe.set_route(...args);
		await drain();
	};
	env.back = async () => {
		if (browser.index > 0) {
			browser.index -= 1;
			await route_safely();
		}
		await drain();
	};
	env.forward = async () => {
		if (browser.index < browser.entries.length - 1) {
			browser.index += 1;
			await route_safely();
		}
		await drain();
	};

	// -- the server --------------------------------------------------------
	function call(opts) {
		let settle;
		const promise = new Promise((resolve, reject) => {
			settle = { resolve, reject };
		});
		promise.catch(() => {});
		const entry = {
			method: opts.method.split(".").pop(),
			args: opts.args || {},
			done: false,
			respond(message) {
				entry.done = true;
				const r = { message };
				if (opts.callback) opts.callback(r);
				if (opts.always) opts.always(r);
				settle.resolve(r);
			},
			fail() {
				entry.done = true;
				if (opts.error) opts.error({});
				if (opts.always) opts.always();
				settle.reject(new Error("417 EXPECTATION FAILED"));
			},
		};
		env.calls.push(entry);
		return promise;
	}
	env.open = (method) => env.calls.filter((c) => !c.done && c.method === method);
	env.respond = async (method, message) => {
		const [entry] = env.open(method);
		assert.ok(entry, `expected an open ${method} call`);
		entry.respond(message);
		await drain();
		return entry;
	};
	env.fail = async (method) => {
		const [entry] = env.open(method);
		assert.ok(entry, `expected an open ${method} call`);
		entry.fail();
		await drain();
		return entry;
	};

	// -- frappe ------------------------------------------------------------
	const escape_html = (s) =>
		String(s)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	const frappe = {
		pages: { "inspection-wizard": wrapper },
		route_options: null,
		route_flags: {},
		call,
		set_route,
		get_route: () => router.current_route,
		router: {
			make_url: (parts) => "/desk/" + parts.map((p) => encodeURIComponent(String(p))).join("/"),
		},
		ui: {
			make_app_page: () => ({ main: new Q([main]) }),
			Dialog: class {
				constructor(opts) {
					this.opts = opts;
				}
				show() {}
				hide() {}
			},
			FileUploader: class {
				constructor(opts) {
					env.uploader = opts;
				}
			},
		},
		utils: {
			escape_html,
			get_url_arg: (name) => new URLSearchParams(here().search).get(name) || "",
			get_form_link: (doctype, name) =>
				`/desk/${doctype.toLowerCase().replace(/ /g, "-")}/${encodeURIComponent(name)}`,
		},
		datetime: { get_today: () => "2026-09-24" },
		msgprint: (message) => env.messages.push(message),
		confirm: (message, yes) => env.confirms.push(yes),
	};
	const __ = (text, args) => (args ? text.replace(/\{(\d+)\}/g, (_, i) => args[i]) : text);

	const context = vm.createContext({
		frappe,
		__,
		$,
		window,
		document,
		console,
		setTimeout: clock.setTimeout,
		clearTimeout: clock.clearTimeout,
	});
	vm.runInContext(SOURCE, context, { filename: WIZARD_JS });

	env.frappe = frappe;
	env.document = document;
	env.main = new Q([main]);
	env.wrapper = wrapper;
	env.wiz = () => wrapper.inspection_wizard;
	env.body = () => wrapper.inspection_wizard.body;
	env.painted_list = () => env.body().html().includes("Your open inspections");
	env.painted_record = () => env.body().find(".qw-tabs").length === 1;
	env.button = (label) => {
		const found = [];
		walk(main, (n) => n.tag === "button" && n.source.includes(`>${label}<`) && found.push(n));
		assert.equal(found.length, 1, `expected one "${label}" button, found ${found.length}`);
		return found[0];
	};
	env.click = async (el) => {
		fire(el, "click", { currentTarget: el, target: el });
		await drain();
	};
	env.inputs = (placeholder) => {
		const found = [];
		walk(main, (n) => n.tag === "input" && n.attrs.placeholder === placeholder && found.push(n));
		return found;
	};
	env.measurements = () => {
		const found = [];
		walk(main, (n) => n.tag === "input" && n.attrs.type === "number" && found.push(n));
		return found;
	};
	// Whether anything painted on the page carries `text` -- what the screen says, not what
	// this.doc says it ought to.
	env.painted = (text) => {
		let hit = false;
		walk(main, (n) => {
			if (n.source.includes(text)) hit = true;
		});
		return hit;
	};
	return env;
}

// A generated inspection: one required Pass/Fail row per section.
function inspection(name, sections = 3, modified = `${name}@1`) {
	return {
		enabled: 1,
		header: {
			name,
			project: "PRJ-00001",
			project_name: "Plaza Fountain",
			milestone: "Rough-in",
			inspection_date: null,
			remarks: null,
			inspector_sign_off: null,
			client_sign_off: null,
			generation_note: null,
		},
		instructions: { safety: null, wrapup: null },
		sections: Array.from({ length: sections }, (_, s) => ({
			title: `Section ${s + 1}`,
			location_note: null,
			rows: [
				{
					name: `${name}-row-${s}`,
					label: `Check ${s + 1}`,
					check_type: "Pass/Fail",
					options: ["Pass", "Fail", "N/A"],
					outcome: null,
					measured_value: null,
					notes: "",
					photo: null,
					is_mandatory: 1,
					requires_photo: 0,
					source: "Template",
					out_of_range: 0,
				},
			],
		})),
		state: {
			name,
			modified,
			docstatus: 0,
			mandatory_total: sections,
			mandatory_answered: 0,
			fail_count: 0,
			completion_percent: 0,
			rows: [],
		},
	};
}

function saved_state(name, modified, docstatus = 0) {
	return {
		name,
		modified,
		docstatus,
		mandatory_total: 3,
		mandatory_answered: 1,
		fail_count: 0,
		completion_percent: 33,
		rows: [],
	};
}

const LIST = { enabled: 1, inspections: [{ name: "QIR-A", project_name: "Plaza Fountain" }] };

// Opens the Desk on another page, then the wizard's list, then inspection A from the list.
async function open_a_from_the_list(env) {
	await env.visit("/desk/todo");
	await env.go("inspection-wizard");
	await env.respond("get_open_inspections", LIST);
	const [item] = env.body().find(".qw-list-item").els;
	await env.click(item);
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
}

// ---------------------------------------------------------------------------

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test("Back steps to the previous section, Forward restores it, Back from the list leaves", async () => {
	const env = boot();
	await env.visit("/desk/todo");
	await env.go("inspection-wizard");
	assert.equal(env.url(), "/desk/inspection-wizard");
	assert.equal(env.open("get_open_inspections").length, 1, "the first open fetches the list once");
	await env.respond("get_open_inspections", LIST);
	assert.ok(env.painted_list());

	await env.click(env.body().find(".qw-list-item").els[0]);
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/0");
	assert.equal(env.browser.entries.length, 3, "the list click pushes exactly one entry");
	assert.equal(env.open("get_open_inspections").length, 0, "and no second list fetch races it");
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.equal(env.wiz().section_index, 0);
	assert.ok(env.painted_record());

	await env.click(env.button("Next"));
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/1");
	assert.equal(env.wiz().section_index, 1);
	await env.click(env.button("Next"));
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/2");
	assert.equal(env.wiz().section_index, 2);
	env.button("Finish and submit");

	await env.back();
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/1");
	assert.equal(env.wiz().section_index, 1, "Back from section 3 shows section 2, not the list");
	assert.ok(env.painted_record());
	await env.back();
	assert.equal(env.wiz().section_index, 0);
	await env.back();
	assert.equal(env.url(), "/desk/inspection-wizard");
	assert.equal(env.wiz().doc, null);
	assert.equal(env.main.find(".qw-nav").length, 0, "no nav bar floats over the list");
	await env.respond("get_open_inspections", LIST);
	assert.ok(env.painted_list());
	await env.back();
	assert.equal(env.url(), "/desk/todo", "Back from the list leaves the page");

	await env.forward();
	assert.equal(env.url(), "/desk/inspection-wizard");
	await env.respond("get_open_inspections", LIST);
	assert.ok(env.painted_list(), "coming back refetches the list");
	await env.forward();
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/0");
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.equal(env.wiz().section_index, 0);
	await env.forward();
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/1");
	assert.equal(env.wiz().section_index, 1, "Forward restores the section it names");
	assert.equal(env.open("get_inspection_bootstrap").length, 0, "a section change fetches nothing");
	assert.deepEqual(env.errors, []);
});

test("section tabs are history entries too", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	const tabs = env.main.find(".qw-tab").els;
	assert.equal(tabs.length, 3);
	await env.click(tabs[2]);
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/2");
	assert.equal(env.wiz().section_index, 2);
	await env.back();
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/0");
	assert.equal(env.wiz().section_index, 0);
	assert.deepEqual(env.errors, []);
});

test("Previous and Next only route; the screen changes when the router calls back", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	const next = env.button("Next");
	env.frappe.set_route = (...args) => {
		env.routed = args;
		return Promise.resolve();
	};
	next.handlers.click[0]({ currentTarget: next, target: next });
	// Compared as JSON: the array was built in the page's vm realm, with its own prototype.
	assert.equal(
		JSON.stringify(env.routed),
		JSON.stringify([["inspection-wizard", "QIR-A", "1"]]),
		"an array of segments, no object"
	);
	assert.equal(env.frappe.route_flags.replace_route, false, "a section change pushes");
	assert.equal(env.wiz().section_index, 0, "nothing is painted before the route lands");
	assert.equal(env.frappe.route_options, null, "route_options is cleared before routing");
});

test("a list fetch and a record fetch racing never paint the wrong screen", async () => {
	// The record lands first, then the stale list.
	let env = boot();
	await env.visit("/desk/todo");
	await env.go("inspection-wizard");
	await env.go("inspection-wizard", "QIR-A", "0");
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	await env.respond("get_open_inspections", LIST);
	assert.ok(env.painted_record(), "a late list response must not paint over the inspection");
	assert.ok(!env.painted_list());
	assert.equal(env.wiz().doc.header.name, "QIR-A");

	// The stale list lands first, then the record.
	env = boot();
	await env.visit("/desk/todo");
	await env.go("inspection-wizard");
	await env.go("inspection-wizard", "QIR-A", "0");
	await env.respond("get_open_inspections", LIST);
	assert.ok(!env.painted_list(), "a list response for a screen already left is dropped");
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.ok(env.painted_record());

	// Back to the list while the record is still loading: the record is dropped.
	env = boot();
	await env.visit("/desk/todo");
	await env.go("inspection-wizard");
	await env.respond("get_open_inspections", LIST);
	await env.click(env.body().find(".qw-list-item").els[0]);
	await env.back();
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.equal(env.wiz().doc, null);
	await env.respond("get_open_inspections", LIST);
	assert.ok(env.painted_list());
	assert.equal(env.main.find(".qw-nav").length, 0);
	assert.deepEqual(env.errors, []);
});

test("Back to the list under a pending autosave saves the answer to its own inspection", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	assert.ok(env.wiz().has_pending("QIR-A"), "the tap is queued behind the debounce");
	assert.equal(env.open("save_inspection").length, 0);

	await env.back();
	assert.equal(env.url(), "/desk/inspection-wizard");
	const [save] = env.open("save_inspection");
	assert.ok(save, "leaving the inspection sends its answer at once");
	assert.equal(save.args.inspection, "QIR-A");
	assert.equal(save.args.modified, "QIR-A@1");
	assert.deepEqual(JSON.parse(save.args.patch).rows, [{ name: "QIR-A-row-0", outcome: "Pass" }]);
	assert.equal(env.open("get_open_inspections").length, 0, "the list waits for the save");

	await env.respond("save_inspection", saved_state("QIR-A", "QIR-A@2"));
	assert.equal(env.wiz().saving, false, "saving is released");
	assert.equal(env.wiz().modified["QIR-A"], "QIR-A@2");
	await env.respond("get_open_inspections", LIST);
	assert.ok(env.painted_list());

	await env.advance(20000);
	assert.equal(env.open("save_inspection").length, 0, "nothing left to save, nothing retried");
	assert.ok(!env.wiz().has_pending());
	assert.deepEqual(env.errors, []);
});

test("a save that fails after the screen moved on is retried for its own inspection only", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	await env.advance(900);
	assert.equal(env.open("save_inspection").length, 1, "A's save is on the wire");

	await env.go("inspection-wizard", "QIR-B", "0");
	assert.equal(env.open("get_inspection_bootstrap").length, 0, "B's bootstrap waits for A's save");
	await env.fail("save_inspection");
	assert.ok(env.wiz().has_pending("QIR-A"), "A's failed patch is put back under A");
	assert.ok(!env.wiz().has_pending("QIR-B"));
	await env.respond("get_inspection_bootstrap", inspection("QIR-B", 3, "QIR-B@1"));
	assert.equal(env.wiz().doc.header.name, "QIR-B");

	await env.click(env.button("Fail"));
	await env.advance(900);
	const [b_save] = env.open("save_inspection");
	assert.equal(b_save.args.inspection, "QIR-B", "the one on screen goes first");
	assert.equal(b_save.args.modified, "QIR-B@1", "under B's own lock");
	assert.deepEqual(
		JSON.parse(b_save.args.patch).rows.map((r) => r.name),
		["QIR-B-row-0"],
		"and carries none of A's rows"
	);
	await env.respond("save_inspection", saved_state("QIR-B", "QIR-B@2"));
	assert.equal(env.wiz().doc.state.modified, "QIR-B@2");

	await env.advance(900);
	const [a_save] = env.open("save_inspection");
	assert.equal(a_save.args.inspection, "QIR-A", "A's patch is retried under A's name");
	assert.equal(a_save.args.modified, "QIR-A@1", "with A's lock");
	assert.deepEqual(JSON.parse(a_save.args.patch).rows, [{ name: "QIR-A-row-0", outcome: "Pass" }]);
	await env.respond("save_inspection", saved_state("QIR-A", "QIR-A@2"));
	assert.equal(env.wiz().doc.state.modified, "QIR-B@2", "A's answer never lands on B");
	assert.equal(env.wiz().modified["QIR-A"], "QIR-A@2");
	assert.ok(!env.wiz().has_pending());
	assert.deepEqual(env.errors, []);
});

test("a save that succeeds after the screen moved on updates only its own lock", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	await env.advance(900);
	await env.go("inspection-wizard", "QIR-B", "0");
	await env.respond("save_inspection", saved_state("QIR-A", "QIR-A@2"));
	await env.respond("get_inspection_bootstrap", inspection("QIR-B", 3, "QIR-B@1"));
	assert.equal(env.wiz().doc.state.modified, "QIR-B@1");
	assert.equal(env.wiz().modified["QIR-A"], "QIR-A@2");
	assert.equal(env.wiz().modified["QIR-B"], "QIR-B@1");
	assert.deepEqual(env.errors, []);
});

test("a bootstrap read before the last save landed cannot roll the lock back", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	// Real stamps, as str(doc.modified) writes them -- the fixtures' "QIR-A@1" are not.
	const wiz = env.wiz();
	wiz.note_modified("QIR-Z", "2026-09-24 10:00:05.000001");
	wiz.note_modified("QIR-Z", "2026-09-24 10:00:04.999999");
	assert.equal(wiz.modified["QIR-Z"], "2026-09-24 10:00:05.000001", "an older stamp is ignored");
	wiz.note_modified("QIR-Z", "2026-09-24 10:00:06");
	assert.equal(wiz.modified["QIR-Z"], "2026-09-24 10:00:06", "a newer one, even without microseconds, wins");
	wiz.note_modified("QIR-Z", "2026-09-24 10:00:06.5");
	assert.equal(wiz.modified["QIR-Z"], "2026-09-24 10:00:06.5");
});

// The lock a queued answer is sent under is the one it was GIVEN against. Reopening the
// inspection reads a fresh `modified`; sent under that, a queued answer overwrote whatever
// somebody else had saved in between, and the course promises that is refused, not merged.
test("a reopen never moves a queued answer's lock past somebody else's save", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	await env.advance(900);
	// Refused: a supervisor saved A in the Desk meanwhile (A@1 -> A@9).
	await env.fail("save_inspection");
	assert.ok(env.wiz().has_pending("QIR-A"));

	await env.back();
	assert.equal(env.url(), "/desk/inspection-wizard");
	assert.equal(env.open("save_inspection")[0].args.modified, "QIR-A@1");
	await env.fail("save_inspection");
	await env.respond("get_open_inspections", LIST);

	await env.click(env.body().find(".qw-list-item").els[0]);
	assert.equal(env.open("save_inspection")[0].args.modified, "QIR-A@1", "the reopen flushes first");
	await env.fail("save_inspection");
	const theirs = inspection("QIR-A", 3, "QIR-A@9");
	theirs.sections[0].rows[0].outcome = "Fail";
	await env.respond("get_inspection_bootstrap", theirs);
	assert.equal(env.wiz().modified["QIR-A"], "QIR-A@1", "the bootstrap does not move a queued answer's lock");
	assert.equal(
		env.wiz().doc.sections[0].rows[0].outcome,
		"Fail",
		"the screen shows the other editor's answer, not the queued one that cannot land"
	);

	await env.advance(8000);
	const [retry] = env.open("save_inspection");
	assert.ok(retry, "the queued answer is still retried, and still says so");
	assert.equal(retry.args.modified, "QIR-A@1", "under its own lock, so the server refuses it again");
	assert.deepEqual(JSON.parse(retry.args.patch).rows, [{ name: "QIR-A-row-0", outcome: "Pass" }]);
	await env.fail("save_inspection");

	// An answer given on the reopened screen joins the refused queue; it does not slip through
	// under the other editor's stamp either.
	await env.click(env.button("N/A"));
	await env.advance(900);
	assert.equal(env.open("save_inspection")[0].args.modified, "QIR-A@1");
	assert.deepEqual(env.errors, []);
});

// The same, when the retry is on the wire as the bootstrap lands: nothing is queued at that
// instant, so the reopen takes the other editor's stamp as the newest known -- and the refused
// patch, put back, must still carry its own.
test("a queued answer refused while the reopen's bootstrap is out keeps its own lock", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	await env.advance(900);
	await env.fail("save_inspection");
	await env.back();
	await env.fail("save_inspection");
	await env.respond("get_open_inspections", LIST);

	await env.click(env.body().find(".qw-list-item").els[0]);
	await env.fail("save_inspection");
	assert.equal(env.open("get_inspection_bootstrap").length, 1);
	await env.advance(8000);
	assert.equal(env.open("save_inspection").length, 1, "the retry goes out while the bootstrap is out");
	assert.ok(!env.wiz().has_pending("QIR-A"));
	const theirs = inspection("QIR-A", 3, "QIR-A@9");
	theirs.sections[0].rows[0].outcome = "Fail";
	await env.respond("get_inspection_bootstrap", theirs);
	assert.equal(env.wiz().doc.sections[0].rows[0].outcome, "Fail");
	await env.fail("save_inspection");

	await env.advance(8000);
	const [retry] = env.open("save_inspection");
	assert.equal(retry.args.modified, "QIR-A@1", "not the stamp the reopen read");
	assert.deepEqual(JSON.parse(retry.args.patch).rows, [{ name: "QIR-A-row-0", outcome: "Pass" }]);
	assert.deepEqual(env.errors, []);
});

test("a reopen after a save lost to a bad signal lays the queued answer back over it", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	await env.advance(900);
	await env.fail("save_inspection");
	await env.back();
	await env.fail("save_inspection");
	await env.respond("get_open_inspections", LIST);

	await env.click(env.body().find(".qw-list-item").els[0]);
	await env.fail("save_inspection");
	// Nobody else saved it: the stamp read now is the one the answer was given against.
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.equal(env.wiz().doc.sections[0].rows[0].outcome, "Pass", "the queued answer is shown");

	await env.advance(8000);
	const [retry] = env.open("save_inspection");
	assert.equal(retry.args.modified, "QIR-A@1");
	await env.respond("save_inspection", saved_state("QIR-A", "QIR-A@2"));
	assert.equal(env.wiz().modified["QIR-A"], "QIR-A@2");
	assert.ok(!env.wiz().has_pending());
	assert.deepEqual(env.errors, []);
});

test("an answer queued while a save is on the wire takes that save's new stamp", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	await env.advance(900);
	assert.equal(env.open("save_inspection").length, 1);
	await env.click(env.button("Next"));
	await env.click(env.button("Fail"));
	// Given on a screen that already showed the save on the wire: that save's stamp is our own
	// write, so the answer's lock moves with it rather than being refused as stale.
	await env.respond("save_inspection", saved_state("QIR-A", "QIR-A@2"));
	await env.advance(900);
	const [second] = env.open("save_inspection");
	assert.equal(second.args.modified, "QIR-A@2");
	assert.deepEqual(JSON.parse(second.args.patch).rows, [{ name: "QIR-A-row-1", outcome: "Fail" }]);
	assert.deepEqual(env.errors, []);
});

// A save's answer repaints the section, and a repaint replaces every input. The section change
// now sends its save at once, so the answer lands one round trip after the inspector has started
// typing the next section's first measurement -- which nothing has queued yet ("change" fires
// on blur), so repainting it away lost it.
test("a save that lands while an input has focus leaves what is being typed alone", async () => {
	const env = boot();
	await env.visit("/desk/todo");
	await env.go("inspection-wizard");
	await env.respond("get_open_inspections", LIST);
	await env.click(env.body().find(".qw-list-item").els[0]);
	const doc = inspection("QIR-A");
	Object.assign(doc.sections[1].rows[0], {
		check_type: "Measurement",
		options: [],
		min_value: 40,
		max_value: 45,
		uom: "in",
	});
	await env.respond("get_inspection_bootstrap", doc);
	await env.click(env.button("Pass"));
	await env.click(env.button("Next"));
	assert.equal(env.open("save_inspection").length, 1, "the section change sends the answer at once");

	const [measure] = env.measurements();
	measure.value = "42.5";
	env.document.activeElement = measure;
	await env.respond("save_inspection", saved_state("QIR-A", "QIR-A@2"));
	assert.equal(env.measurements()[0], measure, "the input being typed into is not replaced");
	assert.equal(measure.value, "42.5");
	assert.equal(env.wiz().doc.state.modified, "QIR-A@2", "the save's answer is still taken");

	measure.blur();
	await env.advance(300);
	assert.notEqual(env.measurements()[0], measure, "the repaint that waited runs once it lets go");
	assert.equal(env.measurements()[0].value, "42.5");
	assert.ok(env.painted("Required checks: 1/3"), "and shows the save it waited for");
	await env.advance(900);
	const [save] = env.open("save_inspection");
	assert.deepEqual(JSON.parse(save.args.patch).rows, [{ name: "QIR-A-row-1", measured_value: 42.5 }]);

	// The same for a note, while that save is on the wire.
	const [note] = env.inputs("Note (optional)");
	note.value = "reads high at the weir";
	env.document.activeElement = note;
	await env.respond("save_inspection", saved_state("QIR-A", "QIR-A@3"));
	assert.equal(env.inputs("Note (optional)")[0], note);
	assert.equal(note.value, "reads high at the weir");
	note.blur();
	await env.advance(900);
	assert.deepEqual(JSON.parse(env.open("save_inspection")[0].args.patch).rows, [
		{ name: "QIR-A-row-1", notes: "reads high at the weir" },
	]);
	assert.deepEqual(env.errors, []);
});

test("a remark or date typed on the wrap-up survives the next repaint", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.main.find(".qw-tab").els[2]);
	const [remarks] = env.inputs("Remarks (optional)");
	remarks.value = "client walked it with us";
	env.document.activeElement = remarks;
	remarks.blur();
	const date = [];
	walk(env.main.els[0], (n) => n.tag === "input" && n.attrs.type === "date" && date.push(n));
	date[0].value = "2026-09-25";
	date[0].blur();
	// A tap repaints the section at once.
	await env.click(env.button("Pass"));
	assert.equal(env.inputs("Remarks (optional)")[0].value, "client walked it with us");
	const after = [];
	walk(env.main.els[0], (n) => n.tag === "input" && n.attrs.type === "date" && after.push(n));
	assert.equal(after[0].value, "2026-09-25");
	assert.deepEqual(env.errors, []);
});

test("a legacy ?inspection= link opens the record and replaces its own entry", async () => {
	const env = boot();
	await env.visit("/desk/todo");
	await env.visit("/desk/inspection-wizard?inspection=QIR-A");
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/0");
	assert.equal(env.browser.entries.length, 2, "replaced, not pushed");
	assert.equal(env.open("get_open_inspections").length, 0, "no list fetch races it");
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.ok(env.painted_record());
	assert.ok(!(env.frappe.route_options || {}).inspection, "the legacy argument is consumed");

	// Back to the list by a link afterwards shows the list, not the consumed inspection.
	await env.go("inspection-wizard");
	assert.equal(env.url(), "/desk/inspection-wizard");
	await env.respond("get_open_inspections", LIST);
	assert.ok(env.painted_list());

	await env.back();
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/0");
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	await env.back();
	assert.equal(env.url(), "/desk/todo", "Back from the first section leaves the page");
	assert.deepEqual(env.errors, []);
});

test("a /desk link carrying ?inspection= (route_options) is replaced the same way", async () => {
	const env = boot();
	await env.visit("/desk/todo");
	// What the router's anchor interception does with /desk/inspection-wizard?inspection=QIR-A.
	env.frappe.route_options = { inspection: "QIR-A" };
	await env.go("/desk/inspection-wizard");
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/0");
	assert.equal(env.browser.entries.length, 2);
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.ok(env.painted_record());
	assert.deepEqual(env.errors, []);
});

test("an out-of-range or missing section is clamped and the address corrected in place", async () => {
	const env = boot();
	await env.visit("/desk/todo");
	await env.visit("/desk/inspection-wizard/QIR-A/9");
	await env.respond("get_inspection_bootstrap", inspection("QIR-A"));
	assert.equal(env.wiz().section_index, 2);
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/2");
	assert.equal(env.browser.entries.length, 2, "corrected by replace");

	await env.visit("/desk/inspection-wizard/QIR-A");
	assert.equal(env.wiz().section_index, 0);
	assert.equal(env.url(), "/desk/inspection-wizard/QIR-A/0");
	assert.equal(env.browser.entries.length, 3);
	assert.deepEqual(env.errors, []);
});

test("a second show on the same route changes nothing", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Next"));
	const before = { calls: env.calls.length, entries: env.browser.entries.length };
	env.wrapper.on_page_show(env.wrapper);
	await drain();
	assert.equal(env.calls.length, before.calls);
	assert.equal(env.browser.entries.length, before.entries);
	assert.equal(env.wiz().section_index, 1);
});

test("a note typed but never blurred is saved when Back replaces its input", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Next"));
	const [note] = env.inputs("Note (optional)");
	note.value = "grout cracked at the weir";
	env.document.activeElement = note;
	await env.back();
	assert.equal(env.wiz().section_index, 0);
	const [save] = env.open("save_inspection");
	assert.ok(save, "the step away sends it");
	assert.deepEqual(JSON.parse(save.args.patch).rows, [
		{ name: "QIR-A-row-1", notes: "grout cracked at the weir" },
	]);
	assert.deepEqual(env.errors, []);
});

test("leaving the page sends what is queued instead of leaving it to a hidden timer", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.button("Pass"));
	await env.go("todo");
	assert.equal(env.open("save_inspection").length, 1);
	assert.equal(env.open("save_inspection")[0].args.inspection, "QIR-A");
	assert.deepEqual(env.errors, []);
});

test("the submitted screen drops the nav bar and links through the router", async () => {
	const env = boot();
	await open_a_from_the_list(env);
	await env.click(env.main.find(".qw-tab").els[2]);
	await env.click(env.button("Finish and submit"));
	assert.equal(env.confirms.length, 1);
	env.confirms[0]();
	await env.respond("finish_inspection", saved_state("QIR-A", "QIR-A@3", 1));
	assert.equal(env.main.find(".qw-nav").length, 0);
	const html = env.body().html();
	assert.ok(html.includes('href="/desk/project-quality-inspection/QIR-A"'), html);
	assert.ok(html.includes('href="/desk/inspection-wizard"'), html);
	assert.ok(!html.includes("/app/"));
	assert.equal(env.main.find(".qw-savestate").length, 1, "one save pill, for the page's lifetime");
	assert.deepEqual(env.errors, []);
});

let failures = 0;
const unhandled = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));
for (const { name, fn } of tests) {
	try {
		await fn();
		await drain();
		if (unhandled.length) throw unhandled.splice(0)[0];
		console.log("  ok   " + name);
	} catch (err) {
		failures += 1;
		console.log("  FAIL " + name);
		console.log("       " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join("\n       ") : err));
	}
}
console.log(`\n${tests.length - failures}/${tests.length} passed`);
process.exit(failures ? 1 : 0);
