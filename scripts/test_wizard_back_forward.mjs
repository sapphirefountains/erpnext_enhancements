#!/usr/bin/env node
/**
 * Back and Forward on the Visit Wizard and Plan a Trip — executed, not grepped.
 *
 * THE RULE
 * ========
 *
 * Back returns to the previous step, screen or tab of the page, and Forward restores it; Back
 * from the page's first screen leaves it normally. Before this, neither page ever made a
 * history entry: the Visit Wizard rewrote its one entry with history.replaceState (and wrote
 * it as /app/visit-wizard, which the v16 router cannot parse — Back or Forward onto it showed
 * "Page not found"), and Plan a Trip rewrote its one entry on every load. So a technician's
 * phone Back from step 4 of 6 closed the tab.
 *
 * WHY A RUNNING TEST
 * ==================
 *
 * Every one of the failures this guards reads correctly in a diff: a pushState that is really
 * a replaceState, an entry that is pushed twice, a Back that lands on the right URL and draws
 * the wrong visit because a slow response arrived last, an autosave that is "flushed" into a
 * record that is no longer on screen. So this runs the REAL page scripts, unmodified, against
 * a port of the frappe v16 router (origin/version-16:frappe/public/js/frappe/router.js):
 *
 *   - set_route() moves an object argument into frappe.route_options and pushes the PATH ONLY
 *     (push_state, router.js:495-503) — the query string is dropped. Plan a Trip v1.520.0
 *     shipped green on a harness that faked set_route to keep the query; this one does not.
 *   - popstate calls router.route() (router.js:18-23), which parses the path, MERGES the
 *     query into route_options without clearing it (set_route_options_from_url, 532-549),
 *     and shows the page — container.change_to triggers "hide" on the page being left and
 *     "show" on the new one, the same page included (views/container.js:42-86), which is
 *     what calls on_page_show.
 *   - frappe's own entries carry null history.state.
 *   - every route change closes the open dialog (router.set_history -> hide_open_dialog), so
 *     a message a page shows and then routes away from is never read. Tests check ui.dialog,
 *     the dialog still open, not just that a message was shown.
 *   - a call the server refuses rejects the way jQuery does, with the jqXHR: status 417 and
 *     _server_messages, which frappe.request.cleanup shows as a dialog unless the call was
 *     `silent`. No answer at all is status 0. A page that retries has to tell them apart.
 *
 * Rendering is replaced by a recorder (the DOM is not what is under test); jQuery is a
 * chainable stub that keeps click handlers so the page's own buttons can be pressed; the
 * server is a fake with an optimistic lock on `modified`, like the real save endpoints; time
 * is a virtual clock, so a 4-second autosave and a retry backoff run in microseconds.
 *
 * Run: node scripts/test_wizard_back_forward.mjs [visit-wizard|plan-a-trip]   (default: both)
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const PAGES = {
	"visit-wizard": {
		file: path.join(APP, "sapphire_maintenance", "page", "visit_wizard", "visit_wizard.js"),
		klass: "VisitWizard",
	},
	"plan-a-trip": {
		file: path.join(APP, "travel_management", "page", "plan_a_trip", "plan_a_trip.js"),
		klass: "TripPlanner",
	},
};

// ------------------------------------------------------------------ virtual clock

const realSetImmediate = setImmediate;
const clock = { now: 0, seq: 0, timers: new Map() };

function fakeSetTimeout(fn, ms) {
	const id = ++clock.seq;
	clock.timers.set(id, { id, at: clock.now + (Number(ms) || 0), fn });
	return id;
}

function fakeClearTimeout(id) {
	clock.timers.delete(id);
}

async function drain() {
	// Every pending microtask runs before a macrotask; loop a few times so promise chains
	// that schedule zero-delay timers settle too (those are run by tick()).
	for (let i = 0; i < 3; i++) await new Promise((resolve) => realSetImmediate(resolve));
}

async function tick(ms) {
	const until = clock.now + (ms || 0);
	for (let guard = 0; guard < 10000; guard++) {
		await drain();
		const due = [...clock.timers.values()]
			.filter((t) => t.at <= until)
			.sort((a, b) => a.at - b.at || a.id - b.id)[0];
		if (!due) break;
		clock.timers.delete(due.id);
		clock.now = Math.max(clock.now, due.at);
		due.fn();
	}
	clock.now = until;
	await drain();
}

// Run until nothing is due within `ms` (network answers take 1ms here).
const settle = () => tick(50);

// ------------------------------------------------------------------ browser: history, location, window

const ORIGIN = "https://erp.test";

function makeBrowser(startUrl) {
	const listeners = {};
	const hist = {
		entries: [{ url: new URL(startUrl, ORIGIN).href, state: null }],
		index: 0,
		left: false,
		get length() {
			return this.entries.length;
		},
		get state() {
			return clone(this.entries[this.index].state);
		},
		pushState(state, _title, url) {
			this.entries = this.entries.slice(0, this.index + 1);
			this.entries.push({ url: new URL(url, this.current()).href, state: clone(state) });
			this.index += 1;
		},
		replaceState(state, _title, url) {
			const entry = this.entries[this.index];
			entry.state = clone(state);
			if (url !== undefined && url !== null) entry.url = new URL(url, this.current()).href;
		},
		back() {
			this.go(-1);
		},
		forward() {
			this.go(1);
		},
		go(delta) {
			const to = this.index + delta;
			if (to < 0) {
				// Nothing behind: the browser leaves the page (or closes the tab the kiosk opened).
				this.left = true;
				return;
			}
			if (to >= this.entries.length) return;
			// Asynchronous, like a browser: the entry changes, then popstate fires.
			fakeSetTimeout(() => {
				this.index = to;
				(listeners.popstate || []).forEach((fn) => fn({ state: this.state }));
			}, 0);
		},
		current() {
			return this.entries[this.index].url;
		},
		urls() {
			return this.entries.map((e) => {
				const u = new URL(e.url);
				return u.pathname + u.search;
			});
		},
	};
	const location = {
		get href() {
			return hist.current();
		},
		get pathname() {
			return new URL(hist.current()).pathname;
		},
		get search() {
			return new URL(hist.current()).search;
		},
	};
	const window = {
		__ev: {},
		history: hist,
		location,
		addEventListener(type, fn) {
			(listeners[type] = listeners[type] || []).push(fn);
		},
		scrollTo() {},
		dispatch(type, event) {
			return (listeners[type] || []).map((fn) => fn(event || {}));
		},
	};
	return { hist, location, window };
}

// history.state is structured-cloned by the browser; every state here is plain JSON.
function clone(state) {
	return state == null ? null : JSON.parse(JSON.stringify(state));
}

// ------------------------------------------------------------------ jQuery stub

const clicks = [];
const FAKE_EL = { contains: (el) => !!(el && el.__in_page) };

function $stub(src) {
	let proxy;
	const target = function () {};
	proxy = new Proxy(target, {
		get(_t, prop) {
			if (prop === "__src") return src;
			if (prop === "length") return 0;
			if (prop === "0") return FAKE_EL;
			if (prop === Symbol.toPrimitive) return () => "";
			if (prop === "then") return undefined;
			// Carries the selector, so a handler bound through find() can be pressed by it.
			if (prop === "find") return (selector) => $stub(`${src} ${selector}`);
			if (prop === "on") {
				return (event, a, b) => {
					const fn = typeof a === "function" ? a : b;
					clicks.push({ src: String(src || ""), event, fn });
					return proxy;
				};
			}
			return () => proxy;
		},
		apply() {
			return proxy;
		},
	});
	return proxy;
}

function $(arg) {
	if (arg && typeof arg === "object" && arg.__ev) {
		// A page wrapper or window: a real event registry, as frappe triggers "show"/"hide".
		const api = {
			on(event, fn) {
				const name = String(event).split(".")[0];
				(arg.__ev[name] = arg.__ev[name] || []).push(fn);
				return api;
			},
			trigger(event) {
				(arg.__ev[event] || []).forEach((fn) => fn());
				return api;
			},
			hide: () => api,
			show: () => api,
			off: () => api,
		};
		return api;
	}
	if (arg && typeof arg === "object" && arg.__attrs) {
		return { attr: (key) => arg.__attrs[key] };
	}
	return $stub(arg);
}

function press(text, event) {
	const found = clicks.filter((c) => c.event === "click" && c.src.includes(text)).pop();
	assert.ok(found, `no click handler on anything showing "${text}"`);
	found.fn(event || {});
}

// ------------------------------------------------------------------ fake server

const server = {
	handlers: {},
	hold: new Set(),
	fail: new Set(),
	held: [],
	calls: [],
	reset() {
		this.handlers = {};
		this.hold = new Set();
		this.fail = new Set();
		this.held = [];
		this.calls = [];
	},
	release(method, ok = true) {
		const index = this.held.findIndex((h) => h.call.method.endsWith(method));
		assert.ok(index >= 0, `nothing held for ${method}`);
		const [held] = this.held.splice(index, 1);
		ok ? held.respond() : held.reject(noAnswer());
	},
	// The call reached the server and committed, and its answer was lost on the way back.
	lose(method) {
		const index = this.held.findIndex((h) => h.call.method.endsWith(method));
		assert.ok(index >= 0, `nothing held for ${method}`);
		const [held] = this.held.splice(index, 1);
		this.handlers[held.call.method.split(".").pop()](held.call.args);
		held.reject(noAnswer());
	},
	sent(method) {
		return this.calls.filter((c) => c.method.endsWith(method));
	},
};

// frappe.call rejects with the jqXHR. No answer at all (a bad signal) is status 0.
const noAnswer = () => ({ status: 0, statusText: "error" });

// A handler returning an Error is a frappe.throw on the server: HTTP 417, the message in
// _server_messages, which frappe.request.cleanup shows as a dialog unless the call was silent.
function refusal(message, opts) {
	const messages = [JSON.stringify({ message, title: message, indicator: "red", raise_exception: 1 })];
	if (!opts.silent) globalThis.frappe.msgprint(messages);
	return { status: 417, responseJSON: { exc_type: "ValidationError", _server_messages: JSON.stringify(messages) } };
}

function call(opts, args) {
	if (typeof opts === "string") opts = { method: opts, args };
	const entry = { method: opts.method, args: opts.args || {}, silent: !!opts.silent };
	server.calls.push(entry);
	const short = opts.method.split(".").pop();
	return new Promise((resolve, reject) => {
		const respond = () => {
			if (server.fail.has(short)) return reject(noAnswer());
			try {
				const handler = server.handlers[short];
				const out = handler ? handler(entry.args) : null;
				if (out instanceof Error) reject(refusal(out.message, opts));
				else resolve({ message: out });
			} catch (err) {
				reject(err);
			}
		};
		if (server.hold.has(short)) server.held.push({ call: entry, respond, reject });
		else fakeSetTimeout(respond, 1);
	});
}

// ------------------------------------------------------------------ frappe (v16 router port)

// `dialog` is the message dialog open now: frappe.msgprint opens it, and every route change
// closes it (router.set_history -> frappe.ui.hide_open_dialog), as on the real desk.
let ui = { alerts: [], msgprints: [], renders: [], dialog: null, save_state: null };

function installFrappe(browser) {
	const { hist, location, window } = browser;
	const frappe = {
		pages: {},
		route_options: null,
		route_flags: {},
		route_history: [],
		boot: {},
		call,
		ui: {
			make_app_page: () => ({
				body: $stub("page.body"),
				main: $stub("page.main"),
				set_title() {},
				set_indicator() {},
				clear_indicator() {},
				set_secondary_action() {},
			}),
			hide_open_dialog() {
				ui.dialog = null;
			},
		},
		utils: {
			escape_html: (s) => String(s == null ? "" : s),
			get_url_arg: (key) => new URLSearchParams(location.search).get(key) || "",
			scroll_to() {},
			get_random: (() => {
				let n = 0;
				return () => `rnd${++n}`;
			})(),
			get_form_link: (dt, dn) => `/desk/${dt}/${dn}`,
		},
		datetime: {
			get_today: () => "2026-09-24",
			str_to_user: (d) => d,
			add_minutes: (d) => d,
			now_datetime: () => "2026-09-24 09:00:00",
		},
		show_alert: (opts) => ui.alerts.push(typeof opts === "string" ? opts : opts.message),
		msgprint: (opts) => {
			// An array is _server_messages, shown one dialog per message (ui/messages.js).
			const shown = Array.isArray(opts) ? opts.map((m) => JSON.parse(m)) : [opts];
			shown.forEach((m) => {
				const text = typeof m === "string" ? m : m.title || m.message;
				ui.msgprints.push(text);
				ui.dialog = text;
			});
		},
		confirm: (_msg, yes) => yes && yes(),
	};

	// router.js, version-16 — the parts a page route goes through.
	frappe.router = {
		current_route: null,
		async route() {
			const sub_path = this.get_sub_path();
			this.current_sub_path = sub_path;
			this.current_route = await this.parse();
			frappe.route_history.push(this.current_route);
			frappe.ui.hide_open_dialog();
			this.render();
		},
		async parse(route) {
			route = this.get_sub_path_string(route).split("/");
			route = route.map(this.decode_component);
			this.set_route_options_from_url();
			return await this.convert_to_standard_route(route);
		},
		async convert_to_standard_route(route) {
			// No workspace or doctype is called "visit-wizard" / "plan-a-trip".
			return route;
		},
		render() {
			pageview.show(this.current_route[0] || "");
		},
		set_route(...args) {
			let route = this.get_route_from_arguments(args);
			const sub_path = this.make_url(route);
			const route_options = frappe.route_options || {};
			const query_params = Object.entries(route_options)
				.map(([key, value]) => `${key}=` + encodeURIComponent(JSON.stringify(value)))
				.join("&");
			this.push_state(sub_path, query_params ? `?${query_params}` : "");
			return new Promise((resolve) => fakeSetTimeout(resolve, 100)).finally(() => (frappe.route_flags = {}));
		},
		get_route_from_arguments(route) {
			if (route.length === 1 && Array.isArray(route[0])) route = route[0];
			if (route.length === 1 && route[0] && String(route[0]).includes("/")) {
				route = String(route[0]).split("/").map(this.decode_component);
			}
			if (route && route[0] === "") route.shift();
			if (route && ["desk", "app"].includes(route[0])) route.shift();
			return route;
		},
		make_url(params) {
			const path_string = params
				.map((a) => {
					if (a && typeof a === "object" && !Array.isArray(a)) {
						frappe.route_options = a;
						return null;
					}
					return encodeURIComponent(String(a));
				})
				.filter((a) => a !== null)
				.join("/");
			return path_string ? "/desk/" + path_string : "/desk";
		},
		push_state(path, query_params = "") {
			if (location.pathname !== path || location.search !== query_params) {
				const method = frappe.route_flags.replace_route ? "replaceState" : "pushState";
				hist[method](null, null, path);
				this.route();
			}
		},
		get_sub_path_string(route) {
			if (!route) route = location.pathname;
			return this.strip_prefix(route);
		},
		strip_prefix(route) {
			if (route.substr(0, 1) == "/") route = route.substr(1);
			if (route == "desk") route = route.substr(4);
			if (route.startsWith("desk/")) route = route.substr(4);
			if (route.substr(0, 1) == "/") route = route.substr(1);
			return route;
		},
		get_sub_path(route) {
			return this.get_sub_path_string(route).split("/").map(this.decode_component).join("/");
		},
		set_route_options_from_url() {
			if (!frappe.route_options) frappe.route_options = {};
			for (const [key, value] of new URLSearchParams(location.search)) frappe.route_options[key] = value;
		},
		decode_component(r) {
			try {
				return decodeURIComponent(r);
			} catch (e) {
				return r;
			}
		},
	};
	frappe.get_route = () => frappe.router.current_route;
	frappe.set_route = (...args) => frappe.router.set_route(...args);

	// views/container.js + views/pageview.js
	const container = {
		page: null,
		change_to(label) {
			const page = frappe.pages[label];
			if (!page) return;
			if (this.page && this.page !== page) $(this.page).trigger("hide");
			this.page = page;
			$(this.page).trigger("show");
		},
	};
	const pageview = {
		show(name) {
			if (!frappe.pages[name]) new_page(name);
			container.change_to(name);
		},
	};
	function new_page(name) {
		const wrapper = { __ev: {}, label: name };
		frappe.pages[name] = wrapper;
		const spec = PAGES[name];
		if (spec) {
			// A function scope per load, so each test boots a fresh copy of the page — frappe
			// evaluates a page script once per page load, the same thing.
			const source = fs.readFileSync(spec.file, "utf8");
			vm.runInThisContext(`(function () {${source}\n;globalThis.__klass_${spec.klass} = ${spec.klass};\n})();`, {
				filename: spec.file,
			});
			patchRender(name, globalThis[`__klass_${spec.klass}`]);
			if (wrapper.on_page_load) wrapper.on_page_load(wrapper);
		}
		$(wrapper).on("show", () => wrapper.on_page_show && wrapper.on_page_show(wrapper));
	}

	window.addEventListener("popstate", () => frappe.router.route());

	Object.assign(globalThis, {
		frappe,
		$,
		window,
		document: { activeElement: null, body: {} },
		setTimeout: fakeSetTimeout,
		clearTimeout: fakeClearTimeout,
		__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_m, i) => (args || [])[i]),
		flt: (v) => parseFloat(v) || 0,
		moment: () => ({ format: () => "", isSameOrBefore: () => false, add() {} }),
		format_currency: (v) => String(v),
	});
	globalThis.document.activeElement = globalThis.document.body;
	return frappe;
}

// The DOM is not under test: record what each render showed instead of drawing it.
function patchRender(name, klass) {
	if (name === "visit-wizard") {
		klass.prototype.render = function () {
			const step = this.steps[this.step_index];
			ui.renders.push({
				record: this.doc.name,
				step: step.key,
				feature: step.table && this.features.length ? this.feature : "",
				readonly: this.doc.docstatus !== 0,
			});
			// The nav bar's Back and Next, pressable by press("Back") / press("Next").
			if (this.step_index > 0) $stub(`vz-nav Back`).on("click", () => this.back_one_step());
			if (this.step_index < this.steps.length - 1) {
				$stub(`vz-nav Next`).on("click", () => this.go(this.step_index + 1));
			}
		};
		const show_picker = klass.prototype.show_picker;
		klass.prototype.show_picker = function () {
			ui.renders.push({ picker: true });
			return show_picker.call(this);
		};
	}
	if (name === "plan-a-trip") {
		klass.prototype.render = function () {
			if (!this.state) return;
			ui.renders.push({ trip: this.state.name || "(new)", step: TP_KEYS[this.step], purpose: this.state.trip.purpose });
		};
		const render_landing = klass.prototype.render_landing;
		klass.prototype.render_landing = function () {
			ui.renders.push({ landing: true });
			return render_landing.call(this);
		};
		// The "Saving... / Saved / Not saved" pill, which lives in the nav bar render() draws.
		const set_save_state = klass.prototype.set_save_state;
		klass.prototype.set_save_state = function (kind) {
			ui.save_state = kind;
			return set_save_state.call(this, kind);
		};
	}
}

const TP_KEYS = ["trip", "crew", "there", "back", "lodging", "around", "freight", "schedule", "review"];

// ------------------------------------------------------------------ harness

let browser;
let F;

function boot(page, startUrl) {
	for (const key of Object.keys(PAGES)) delete globalThis[`__klass_${PAGES[key].klass}`];
	clicks.length = 0;
	ui = { alerts: [], msgprints: [], renders: [], dialog: null, save_state: null };
	clock.timers.clear();
	browser = makeBrowser(startUrl);
	F = installFrappe(browser);
	// The desk boots by routing whatever URL it was opened on.
	F.router.route();
}

const url = () => {
	const u = new URL(browser.location.href);
	return u.pathname + u.search;
};
const last = () => ui.renders[ui.renders.length - 1];
const wizard = () => F.pages["visit-wizard"].visit_wizard;
const planner = () => F.pages["plan-a-trip"].trip_planner;

async function back() {
	browser.hist.back();
	await settle();
}
async function forward() {
	browser.hist.forward();
	await settle();
}

let failures = 0;
let checks = 0;
async function test(name, fn) {
	checks += 1;
	server.reset();
	try {
		await fn();
		console.log("  ok   " + name);
	} catch (err) {
		failures += 1;
		console.log("  FAIL " + name);
		console.log("       " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join("\n       ") : String(err)));
	}
}

// ------------------------------------------------------------------ Visit Wizard fixtures

function visitServer(records) {
	const db = {};
	Object.entries(records).forEach(([name, rec]) => {
		db[name] = { ...rec, name, modified: rec.modified || `${name}@1`, docstatus: rec.docstatus || 0 };
	});
	let bump = 1;
	server.handlers.get_my_visits_today = () => Object.keys(db).map((name) => ({ name, project: "P" }));
	server.handlers.get_upcoming_visits = () => [];
	server.handlers.get_visit_bootstrap = ({ record }) => {
		const rec = clone(db[record]);
		return { record: rec, state: { modified: rec.modified, docstatus: rec.docstatus, completion_percent: 10 } };
	};
	server.handlers.save_visit = ({ record, patch, modified }) => {
		const rec = db[record];
		// The real endpoint's optimistic lock (_check_not_stale).
		if (modified !== rec.modified) return new Error("Visit Out of Date");
		const p = JSON.parse(patch);
		Object.assign(rec, p.fields);
		Object.entries(p.rows).forEach(([table, rows]) =>
			rows.forEach((row) => {
				const hit = (rec[table] || []).find((r) => r.name === row.name);
				if (hit) Object.assign(hit, row);
				else (rec[table] = rec[table] || []).push({ ...row, name: `${table}-${++bump}` });
			})
		);
		rec.modified = `${record}@${++bump}`;
		return { modified: rec.modified, docstatus: 0, completion_percent: 50 };
	};
	server.handlers.finish_visit = ({ record, modified }) => {
		const rec = db[record];
		if (modified !== rec.modified) return new Error("Visit Out of Date");
		rec.docstatus = 1;
		rec.modified = `${record}@${++bump}`;
		return { modified: rec.modified, docstatus: 1, workflow_state: "Completed" };
	};
	return db;
}

function visit(extra) {
	return {
		safety_acknowledged: 1,
		chemistry_readings: [
			{ name: "r1", serial_no: "SN-A", reading: "pH" },
			{ name: "r2", serial_no: "SN-B", reading: "pH" },
		],
		consumables: [{ name: "c1", serial_no: "SN-A", item: "Chlorine" }],
		maintenance_results: [],
		cleaning_tasks: [{ name: "t1", serial_no: "SN-A", task: "Skim" }],
		...(extra || {}),
	};
}

async function visitWizardSuite() {
	console.log("Visit Wizard");

	await test("picker -> visit -> steps: Back walks back one screen at a time, Forward restores each", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		assert.deepEqual(last(), { picker: true });
		press("MNT-1"); // the Today's Visits card
		await settle();
		assert.equal(url(), "/desk/visit-wizard/MNT-1");
		assert.equal(last().step, "safety");
		press("vz-nav Next");
		await settle();
		assert.equal(url(), "/desk/visit-wizard/MNT-1/readings/SN-A");
		press("vz-nav Next");
		await settle();
		assert.equal(url(), "/desk/visit-wizard/MNT-1/consumables/SN-A");
		assert.equal(browser.hist.length, 4, browser.hist.urls().join(" | "));

		await back();
		assert.equal(last().step, "readings");
		await back();
		assert.equal(last().step, "safety");
		await back();
		assert.deepEqual(last(), { picker: true });
		assert.equal(wizard().doc, null);

		await forward();
		assert.equal(last().record, "MNT-1");
		assert.equal(last().step, "safety");
		await forward();
		assert.equal(last().step, "readings");
		await forward();
		assert.equal(last().step, "consumables");
		assert.equal(browser.hist.length, 4, "Back/Forward must not add entries");
	});

	await test("no history entry is ever an /app/ path (the v16 router cannot parse one)", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		press("MNT-1"); // the list's card: where the old code wrote /app/visit-wizard?record=
		await settle();
		press("vz-nav Next");
		await settle();
		wizard().go(wizard().step_index, { feature: "SN-B" });
		await settle();
		const w = wizard();
		w.go(w.steps.length - 1);
		await settle();
		w.finish();
		await settle();
		press("Back to Today's Visits"); // and where it wrote /app/visit-wizard
		await settle();
		for (const entry of browser.hist.urls()) assert.ok(!entry.startsWith("/app/"), entry);
		assert.deepEqual(last(), { picker: true });
	});

	await test("the kiosk's link (a new tab): Back from step 2 returns to step 1, Back from step 1 leaves", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/app/visit-wizard?record=MNT-1".replace("/app/", "/desk/"));
		await settle();
		assert.equal(last().step, "safety");
		assert.equal(url(), "/desk/visit-wizard?record=MNT-1", "the kiosk's own entry is not rewritten");
		press("vz-nav Next");
		await settle();
		await back();
		assert.equal(last().step, "safety");
		assert.equal(browser.hist.left, false);
		await back();
		assert.equal(browser.hist.left, true, "Back from the first screen leaves the page");
	});

	await test("a feature tab is its own entry, and Back returns to the tab before", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1/readings");
		await settle();
		assert.equal(last().feature, "SN-A");
		wizard().go(wizard().step_index, { feature: "SN-B" });
		await settle();
		assert.equal(url(), "/desk/visit-wizard/MNT-1/readings/SN-B");
		assert.equal(last().feature, "SN-B");
		await back();
		assert.equal(last().feature, "SN-A");
		assert.equal(last().step, "readings");
	});

	await test("the wizard's own Back button steps back through history: Next, Back, Next piles nothing up", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1");
		await settle();
		press("vz-nav Next");
		await settle();
		press("vz-nav Next");
		await settle();
		const before = browser.hist.length;
		press("vz-nav Back");
		await settle();
		assert.equal(last().step, "readings");
		assert.equal(browser.hist.length, before, "in-app Back must not push");
		press("vz-nav Next");
		await settle();
		assert.equal(last().step, "consumables");
		assert.equal(browser.hist.length, before, "Next after Back replaces the forward entry");
		await back();
		await back();
		assert.equal(last().step, "safety");
	});

	await test("the safety gate holds for the step dots and for Forward", async () => {
		visitServer({ "MNT-1": visit({ safety_acknowledged: 0 }) });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1");
		await settle();
		wizard().go(3); // a step dot
		await settle();
		assert.equal(last().step, "safety");
		assert.ok(ui.alerts.some((a) => /safety/i.test(a)));
		const entries = browser.hist.length;
		// Forward onto (or a typed address for) a later step before the tick.
		F.set_route("visit-wizard", "MNT-1", "readings");
		await settle();
		assert.equal(last().step, "safety");
		assert.equal(url(), "/desk/visit-wizard/MNT-1/safety", "the address is corrected, not left lying");
		assert.equal(browser.hist.length, entries + 1, "corrected in place: one entry for the move, none for the fix");
	});

	await test("a Back that changes step commits the field being typed in, then saves it", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1");
		await settle();
		press("vz-nav Next");
		await settle();
		const w = wizard();
		const row = w.doc.chemistry_readings[0];
		// A number typed and not yet blurred: its "change" fires only on blur.
		globalThis.document.activeElement = {
			__in_page: true,
			blur() {
				w.set_row("chemistry_readings", row, "reading_value", 7.2);
			},
		};
		await back();
		globalThis.document.activeElement = globalThis.document.body;
		assert.equal(last().step, "safety");
		const saves = server.sent("save_visit");
		assert.equal(saves.length, 1);
		assert.deepEqual(JSON.parse(saves[0].args.patch).rows.chemistry_readings[0], { name: "r1", reading_value: 7.2 });
	});

	await test("a pending 4-second autosave is sent, not lost, when Back leaves the visit for the list", async () => {
		const db = visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		press("MNT-1");
		await settle();
		wizard().set_field("visit_notes", "gate was open");
		await back();
		assert.deepEqual(last(), { picker: true });
		await tick(5000);
		assert.equal(db["MNT-1"].visit_notes, "gate was open");
		assert.equal(server.sent("save_visit").length, 1);
	});

	await test("a save that fails after Back is parked on its own visit, never merged into the next one", async () => {
		const db = visitServer({ "MNT-1": visit(), "MNT-2": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		press("MNT-1");
		await settle();
		server.hold.add("save_visit");
		wizard().set_field("visit_notes", "from MNT-1");
		wizard().flush_save().catch(() => {});
		await back(); // to the list, while that save is on the wire
		press("MNT-2");
		await settle();
		assert.equal(wizard().doc.name, "MNT-2");
		server.release("save_visit", false); // the MNT-1 save fails now
		await settle();
		assert.equal(Object.keys(wizard().dirty.fields).length, 0, "MNT-2 must not inherit MNT-1's edits");
		assert.ok(wizard()._parked["MNT-1"], "MNT-1's edits are parked against MNT-1");
		server.hold.delete("save_visit");
		await tick(3000); // the parked retry
		assert.equal(db["MNT-1"].visit_notes, "from MNT-1");
		assert.equal(db["MNT-2"].visit_notes, undefined);
		assert.ok(ui.alerts.some((a) => /Saved your changes to MNT-1/.test(a)));
		assert.equal(wizard()._parked["MNT-1"], undefined);
	});

	await test("parked edits come back on screen when their visit is opened again before they land", async () => {
		const db = visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		press("MNT-1");
		await settle();
		server.fail.add("save_visit");
		wizard().set_field("visit_notes", "typed offline");
		await back(); // leave_record's flush fails -> parked
		await settle();
		assert.ok(wizard()._parked["MNT-1"]);
		server.fail.delete("save_visit");
		await forward(); // straight back into MNT-1, before the retry timer
		assert.equal(wizard().doc.visit_notes, "typed offline", "the edit is on screen again");
		assert.equal(wizard()._parked["MNT-1"], undefined);
		await tick(5000); // the normal autosave sends it, with the freshly loaded `modified`
		assert.equal(db["MNT-1"].visit_notes, "typed offline");
	});

	await test("one save of a visit at a time: the second waits and carries the first's `modified`", async () => {
		const db = visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1");
		await settle();
		server.hold.add("save_visit");
		const w = wizard();
		w.set_field("visit_notes", "one");
		w.flush_save().catch(() => {});
		w.set_field("visit_notes", "two");
		w.flush_save().catch(() => {});
		await settle();
		assert.equal(server.sent("save_visit").length, 1, "the second must wait for the first");
		server.hold.delete("save_visit");
		server.release("save_visit");
		await settle();
		assert.equal(server.sent("save_visit").length, 2);
		assert.equal(db["MNT-1"].visit_notes, "two", "no 'Visit Out of Date' refusal, newest value wins");
	});

	await test("a list that answers after a visit was opened does not paint over it (and vice versa)", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1/readings");
		await settle();
		server.hold.add("get_my_visits_today");
		F.set_route("visit-wizard"); // the list, whose fetch hangs
		await settle();
		await back(); // back to the visit before the list answered
		assert.equal(last().step, "readings");
		const cards = clicks.length;
		server.release("get_my_visits_today");
		await settle();
		assert.equal(clicks.length, cards, "the stale list drew cards over the visit");

		server.hold.add("get_visit_bootstrap");
		await forward(); // the list again
		await back(); // the visit: its bootstrap hangs
		await forward(); // the list, before the visit arrived
		server.hold.delete("get_visit_bootstrap");
		server.release("get_visit_bootstrap");
		await settle();
		assert.deepEqual(last(), { picker: true }, "the stale visit painted over the list");
		assert.equal(wizard().doc, null);
	});

	await test("Back onto a step of a visit that is not loaded opens that visit on that step", async () => {
		visitServer({ "MNT-1": visit(), "MNT-2": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		press("MNT-1");
		await settle();
		press("vz-nav Next");
		await settle();
		F.set_route("visit-wizard"); // Done -> list, or the sidebar
		await settle();
		press("MNT-2");
		await settle();
		await back();
		await back();
		assert.equal(last().record, "MNT-1");
		assert.equal(last().step, "readings");
	});

	await test("finishing: 'Back to Today's Visits' is a new entry, and Back shows the visit read-only", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1");
		await settle();
		const w = wizard();
		w.go(w.steps.length - 1);
		await settle();
		w.finish();
		await settle();
		press("Back to Today's Visits");
		await settle();
		assert.deepEqual(last(), { picker: true });
		await back();
		assert.equal(last().step, "wrapup");
		assert.equal(last().readonly, true, "a finished visit must not reopen as an editable draft");
	});

	await test("a signature drawn on Wrap-up survives leaving the step and coming back", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1");
		await settle();
		const w = wizard();
		w.go(w.steps.length - 1);
		await settle();
		w._signature_drawn = true;
		w._signature_data = "data:image/png;base64,SIGNED";
		await back();
		await forward();
		w.finish();
		await settle();
		assert.equal(server.sent("finish_visit")[0].args.signature, "data:image/png;base64,SIGNED");
	});

	await test("?record= arriving in route_options is consumed: a later plain visit shows the list", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/todo");
		await settle();
		F.set_route("visit-wizard", { record: "MNT-1" }); // the desk form's button, done the v16 way
		await settle();
		assert.equal(last().record, "MNT-1");
		F.set_route("todo");
		await settle();
		F.set_route("visit-wizard");
		await settle();
		assert.deepEqual(last(), { picker: true });
	});

	await test("leaving for another Desk page sends what was typed, and Back returns to the same step", async () => {
		const db = visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard/MNT-1");
		await settle();
		press("vz-nav Next");
		await settle();
		wizard().set_field("visit_notes", "left for the sidebar");
		F.set_route("todo");
		await settle();
		assert.equal(db["MNT-1"].visit_notes, "left for the sidebar");
		await back();
		assert.equal(last().step, "readings");
	});

	await test("a visit opened through route_options alone gets an address: Back and a reload find it", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/todo");
		await settle();
		// v16 pushes the bare path for this, the query going to route_options only.
		F.set_route("visit-wizard", { record: "MNT-1" });
		await settle();
		assert.equal(last().step, "safety");
		assert.equal(url(), "/desk/visit-wizard/MNT-1/safety", "frappe's bare entry must name the visit");
		assert.equal(browser.hist.length, 2, "named in place, not pushed");
		press("vz-nav Next");
		await settle();
		await back();
		assert.equal(last().record, "MNT-1", "Back onto the bare entry showed the picker");
		assert.equal(last().step, "safety");
		boot("visit-wizard", url()); // a reload of that entry
		await settle();
		assert.equal(last().record, "MNT-1", "a reload of the bare entry showed the picker");
	});

	await test("an in-desk ?record= link (routed as the path alone) gets an address too", async () => {
		visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		// router.js's link handler: the query into route_options, then set_route(pathname).
		F.route_options = { record: "MNT-1" };
		F.set_route("/desk/visit-wizard");
		await settle();
		assert.equal(last().step, "safety");
		assert.equal(url(), "/desk/visit-wizard/MNT-1/safety");
		press("vz-nav Next");
		await settle();
		await back();
		assert.equal(last().step, "safety");
		await back();
		assert.deepEqual(last(), { picker: true });
	});

	await test("a parked save refused as out of date is not retried and pops nothing over the next visit", async () => {
		const db = visitServer({ "MNT-1": visit(), "MNT-2": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		press("MNT-1");
		await settle();
		server.hold.add("save_visit");
		wizard().set_field("visit_notes", "from MNT-1");
		wizard().flush_save().catch(() => {});
		await back();
		press("MNT-2");
		await settle();
		// The save committed, and its answer was lost on a bad signal: the parked retry
		// carries the old `modified`, so the server refuses it every time.
		server.hold.delete("save_visit");
		server.lose("save_visit");
		await tick(200000);
		assert.equal(db["MNT-1"].visit_notes, "from MNT-1");
		assert.deepEqual(ui.msgprints, [], "a dialog about MNT-1 popped up over MNT-2");
		assert.equal(server.sent("save_visit").length, 2, "one retry, then stop: it can never succeed");
		assert.ok(wizard()._parked["MNT-1"], "refused, not dropped: the edits are kept");
		assert.ok(ui.alerts.some((a) => /MNT-1 were turned down/.test(a)), ui.alerts.join(" | "));
		assert.equal(wizard().doc.name, "MNT-2");
		assert.equal(Object.keys(wizard().dirty.fields).length, 0);
		// Opening MNT-1 again puts them back and saves them against the fresh version.
		F.set_route("visit-wizard", "MNT-1");
		await settle();
		assert.equal(wizard().doc.visit_notes, "from MNT-1");
		assert.equal(wizard()._parked["MNT-1"], undefined);
		await tick(5000);
		assert.equal(server.sent("save_visit").length, 3);
		assert.deepEqual(ui.msgprints, []);
	});

	await test("Back from a visit saved elsewhere meanwhile: refused silently, named, kept — no loop", async () => {
		const db = visitServer({ "MNT-1": visit() });
		boot("visit-wizard", "/desk/visit-wizard");
		await settle();
		press("MNT-1");
		await settle();
		wizard().set_field("visit_notes", "typed here");
		db["MNT-1"].modified = "MNT-1@desk"; // the office saved it on the desk form
		await back(); // leave_record's flush answers after the list is on screen
		await tick(200000);
		assert.deepEqual(last(), { picker: true });
		assert.deepEqual(ui.msgprints, [], "'Visit Out of Date' over the list, naming no visit");
		assert.equal(server.sent("save_visit").length, 1);
		assert.ok(server.sent("save_visit")[0].silent);
		assert.ok(wizard()._parked["MNT-1"]);
		assert.ok(ui.alerts.some((a) => /MNT-1 were turned down/.test(a)));
	});
}

// ------------------------------------------------------------------ Plan a Trip fixtures

function tripServer(trips) {
	const db = {};
	Object.entries(trips).forEach(([name, trip]) => (db[name] = { ...trip, name, modified: `${name}@1` }));
	let seq = 0;
	const state = (rec) => ({
		name: rec.name,
		modified: rec.modified,
		status: "Planning",
		can_write: true,
		trip: { ...rec.trip },
		travelers: clone(rec.travelers || []),
		bookings: { flights: [], accommodations: [], ground_transport: [] },
		freight: [],
		stops: [],
		gaps: [],
	});
	server.handlers.get_recent_plans = () => Object.keys(db).map((name) => ({ name, purpose: db[name].trip.purpose }));
	server.handlers.get_plan = (args) => ({
		lookups: { default_company: "SF", employees: [], currency: "USD" },
		state: args && args.trip ? state(db[args.trip]) : undefined,
	});
	server.handlers.save_plan = ({ plan, trip, modified }) => {
		const p = JSON.parse(plan);
		if (trip && db[trip].modified !== modified) return new Error("changed elsewhere");
		const name = trip || `TRIP-NEW-${++seq}`;
		db[name] = { name, trip: p.trip, travelers: p.travelers, modified: `${name}@${++seq + 1}` };
		return state(db[name]);
	};
	return db;
}

const TRIP = {
	trip: { purpose: "Install", travel_type: "Domestic", start_date: "2026-10-01", end_date: "2026-10-03", trip_description: "" },
	travelers: [{ employee: "E1", employee_name: "Ana" }],
};

async function planATripSuite() {
	console.log("Plan a Trip");

	await test("list -> trip -> steps: Back walks back a step at a time to the list, Forward restores", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		assert.deepEqual(last(), { landing: true });
		press("tp-list-item", { currentTarget: { __attrs: { "data-name": "TRIP-1" } } });
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=trip");
		planner().go(1);
		await settle();
		planner().go(2);
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=there");
		assert.equal(browser.hist.length, 4, browser.hist.urls().join(" | "));
		await back();
		assert.equal(last().step, "crew");
		await back();
		assert.equal(last().step, "trip");
		await back();
		assert.deepEqual(last(), { landing: true });
		await forward();
		assert.equal(last().trip, "TRIP-1");
		assert.equal(last().step, "trip");
		await forward();
		await forward();
		assert.equal(last().step, "there");
		assert.equal(browser.hist.length, 4, "Back/Forward must not add entries");
		for (const entry of browser.hist.urls()) assert.ok(!entry.startsWith("/app/"), entry);
	});

	await test("a deep link with &step= (frappe.set_route, v16: no query string) opens on that step, once", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/travel-trip/TRIP-1");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-1", step: "review" });
		await settle();
		assert.equal(last().step, "review");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review");
		assert.equal(browser.hist.length, 2, "the load corrects its entry rather than adding one");
		await back();
		assert.equal(url(), "/desk/travel-trip/TRIP-1", "Back from the first screen leaves the page");
	});

	await test("a new trip: Back onto its '?new=1' entries after it was saved reopens it, never a blank trip", async () => {
		const db = tripServer({});
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		press('data-action="new"');
		await settle();
		const p = planner();
		assert.equal(url(), "/desk/plan-a-trip?new=1&step=trip");
		Object.assign(p.state.trip, { purpose: "Survey", start_date: "2026-11-01", end_date: "2026-11-02" });
		p.go(1);
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?new=1&step=crew");
		p.state.travelers.push({ employee: "E1", employee_name: "Ana" });
		p.go(2); // the first save: the trip gets a name
		await settle();
		const name = Object.keys(db)[0];
		assert.ok(name, "the trip was saved");
		assert.equal(url(), `/desk/plan-a-trip?trip=${name}&step=there`);
		await back();
		assert.equal(url(), `/desk/plan-a-trip?trip=${name}&step=crew`, "the entry it was saved on now names it");
		await back();
		assert.equal(last().trip, name, "Back onto ?new=1 must show the saved trip");
		assert.equal(last().purpose, "Survey");
		assert.equal(last().step, "trip");
		assert.equal(url(), `/desk/plan-a-trip?trip=${name}&step=trip`, "and that entry is corrected to name it");
		await back();
		assert.deepEqual(last(), { landing: true });
		await forward();
		assert.equal(last().trip, name);
		assert.equal(last().step, "trip");
	});

	await test("a '?new=1' entry reached straight from the list (history menu) reopens the trip it became", async () => {
		const db = tripServer({});
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		press('data-action="new"');
		await settle();
		const p = planner();
		Object.assign(p.state.trip, { purpose: "Survey", start_date: "2026-11-01", end_date: "2026-11-02" });
		p.go(1);
		await settle();
		p.state.travelers.push({ employee: "E1", employee_name: "Ana" });
		p.go(2);
		await settle();
		const name = Object.keys(db)[0];
		F.set_route("plan-a-trip"); // the sidebar: the list, a fresh frappe entry
		await settle();
		assert.deepEqual(last(), { landing: true });
		browser.hist.go(-3); // long-press Back, pick the "new trip" entry
		await settle();
		assert.equal(last().trip, name, "never a blank trip");
		assert.equal(last().step, "trip");
	});

	await test("an unsaved new trip (nobody on it yet) survives Back to the list and returns on Forward", async () => {
		tripServer({});
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		press('data-action="new"');
		await settle();
		planner().state.trip.purpose = "Half typed";
		await back();
		assert.deepEqual(last(), { landing: true });
		assert.equal(planner().unsaved_kept().length, 1, "kept, and offered on the list");
		await forward();
		assert.equal(last().purpose, "Half typed");
		assert.equal(planner().unsaved_kept().length, 0);
	});

	await test("a Forward that fails a check goes back to the step showing, keeps the entry, says why there", async () => {
		tripServer({});
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		press('data-action="new"');
		await settle();
		const p = planner();
		Object.assign(p.state.trip, { purpose: "X", start_date: "2026-11-01", end_date: "2026-11-02" });
		p.go(1);
		await settle();
		await back();
		assert.equal(last().step, "trip");
		p.state.trip.purpose = ""; // step 0 no longer passes
		const entries = browser.hist.urls();
		const at = browser.hist.index;
		await forward();
		assert.equal(last().step, "trip");
		assert.equal(url(), "/desk/plan-a-trip?new=1&step=trip", "the address follows the step still showing");
		assert.equal(browser.hist.index, at, "back on the entry it came from");
		assert.deepEqual(browser.hist.urls(), entries, "the entry asked for must stay, to go Forward to once fixed");
		assert.equal(ui.dialog, "The trip first", "the reason must still be open after the return");
		p.state.trip.purpose = "X";
		await forward();
		assert.equal(last().step, "crew");
	});

	await test("Back is never held up by a half-finished step: no dialog, and no entry rewritten", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/travel-trip/TRIP-1");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-1" }); // the form's "Plan step by step"
		await settle();
		const p = planner();
		p.go(1);
		await settle();
		p.go(2);
		await settle();
		p.go(3);
		await settle();
		assert.equal(last().step, "back");
		const entries = browser.hist.urls();
		p.state.trip.purpose = ""; // problems() finds something: a save would be refused
		await back();
		assert.equal(last().step, "there");
		await back();
		assert.equal(last().step, "crew");
		await back();
		assert.equal(last().step, "trip");
		assert.deepEqual(ui.msgprints, [], "Back showed 'A few things to fill in first'");
		assert.equal(ui.save_state, "error", "what was not saved is marked Not saved");
		assert.deepEqual(browser.hist.urls(), entries, "a refused Back rewrote the entry it was going to");
		await back();
		assert.equal(url(), "/desk/travel-trip/TRIP-1", "the fourth Back leaves the page");
		assert.deepEqual(ui.msgprints, []);
	});

	await test("the page's own Back button is not held up either", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=lodging"); // nothing behind it
		await settle();
		const p = planner();
		p.go(5);
		await settle();
		p.state.trip.purpose = "";
		p.back_one_step(); // the entry behind is lodging: back through history
		await settle();
		assert.equal(last().step, "lodging");
		p.back_one_step(); // nothing behind for "back": a new entry
		await settle();
		assert.equal(last().step, "back");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=back");
		assert.deepEqual(ui.msgprints, []);
	});

	await test("Back onto a later step (a tab jumped back from it) is not held up", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1");
		await settle();
		const p = planner();
		p.go(1);
		await settle();
		p.go(2);
		await settle();
		p.go(0); // the "The trip" tab: an entry of its own
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=trip");
		p.state.trip.purpose = "";
		await back();
		assert.equal(last().step, "there", "Back returns to where the tab was tapped");
		assert.deepEqual(ui.msgprints, []);
		await forward(); // a Forward onto a step before it: never held
		assert.equal(last().step, "trip");
		assert.deepEqual(ui.msgprints, []);
	});

	await test("a Forward the server refuses goes back, and the server's message is shown once there", async () => {
		const db = tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1");
		await settle();
		const p = planner();
		p.go(1);
		await settle();
		await back();
		p.state.trip.purpose = "Install, phase 2";
		db["TRIP-1"].modified = "TRIP-1@form"; // saved on the form meanwhile
		const entries = browser.hist.urls();
		await forward();
		assert.equal(last().step, "trip");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=trip");
		assert.deepEqual(browser.hist.urls(), entries);
		assert.equal(ui.dialog, "changed elsewhere", "the refusal must be readable after the return");
		assert.deepEqual(ui.msgprints, ["changed elsewhere"], "shown once, after the return — not flashed and closed");
		assert.equal(ui.save_state, "error");
	});

	await test("the form's 'Plan step by step' onto the trip on screen names it in frappe's bare entry", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1");
		await settle();
		planner().go(1);
		await settle();
		planner().go(2);
		await settle();
		F.set_route("travel-trip", "TRIP-1"); // "Open the full form"
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-1" }); // travel_trip.js "Plan step by step"
		await settle();
		assert.equal(last().step, "there");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=there", "frappe's bare entry must name the step");
		planner().go(3);
		await settle();
		await back();
		assert.equal(last().step, "there", "Back found the list");
		assert.equal(last().trip, "TRIP-1");
	});

	await test("a checklist link to the step already showing names it in frappe's bare entry", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review");
		await settle();
		assert.equal(last().step, "review");
		F.set_route("travel-trip", "TRIP-1");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-1", step: "review" });
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review");
	});

	await test("the page's own Back button steps back through history: Next, Back, Next piles nothing up", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1");
		await settle();
		planner().go(1);
		await settle();
		planner().go(2);
		await settle();
		const before = browser.hist.length;
		planner().back_one_step();
		await settle();
		assert.equal(last().step, "crew");
		assert.equal(browser.hist.length, before);
		planner().go(2);
		await settle();
		assert.equal(browser.hist.length, before);
		await back();
		await back();
		assert.equal(last().step, "trip");
	});

	await test("a trip that loads after Back already showed the list does not paint over it", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		server.hold.add("get_plan");
		press("tp-list-item", { currentTarget: { __attrs: { "data-name": "TRIP-1" } } });
		await settle();
		await back();
		server.release("get_plan");
		await settle();
		assert.deepEqual(last(), { landing: true });
		assert.equal(planner().state, null);
	});

	await test("the list's + Add (an unmarked ?new) keeps a new trip that is still unsaved", async () => {
		tripServer({});
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		press('data-action="new"');
		await settle();
		const p = planner();
		Object.assign(p.state.trip, { purpose: "Keep me", start_date: "2026-11-01", end_date: "2026-11-02" });
		F.set_route("travel-trip");
		await settle();
		F.set_route("plan-a-trip", { new: 1 });
		await settle();
		assert.equal(p.state.trip.purpose, "Keep me");
		assert.equal(url(), "/desk/plan-a-trip?new=1&step=trip", "frappe's bare entry must become the trip's own");
		p.go(1);
		await settle();
		await back();
		assert.equal(last().purpose, "Keep me", "Back onto frappe's entry found the list");
		assert.equal(last().step, "trip");
	});
}

// ------------------------------------------------------------------ main

const which = process.argv[2];
if (!which || which === "visit-wizard") await visitWizardSuite();
if (!which || which === "plan-a-trip") await planATripSuite();

console.log(`${checks - failures} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
