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
 * Rendering is replaced by a recorder (the DOM is not what is under test), except for a Plan a
 * Trip view: its real render_view runs and the markup it builds is kept (ui.drawn), because a
 * view's own lines ("not saved yet", "Loading...") are the only way it tells anyone anything.
 * jQuery is a chainable stub that keeps click handlers so the page's own buttons can be pressed; the
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
let ui = { alerts: [], msgprints: [], renders: [], dialog: null, save_state: null, drawn: [] };

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
		// tp_html_to_text's parser (the Overview's "Not on any day yet"): the text of the markup.
		DOMParser: class {
			parseFromString(html) {
				return { body: { textContent: String(html).replace(/<[^>]+>/g, "") } };
			}
		},
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
			// `view` / `as`: the look at the whole trip drawn over the step (Overview, Crew grid,
			// Side by side, View as) — "" while the step itself is on screen.
			const entry = {
				trip: this.state.name || "(new)",
				step: TP_KEYS[this.step],
				purpose: this.state.trip.purpose,
				view: this.view || "",
				as: this.view_as || "",
			};
			// A view's own lines are all it has to tell the person looking something: that the
			// latest changes are not in it (a view has no "Not saved" pill, which lives in the
			// Back/Next bar a view does not draw), that it is still loading, who is missing. So
			// the real render_view runs, and every piece of markup it builds is kept (ui.drawn).
			// An earlier version checked set_save_state("error") instead, which does nothing on
			// a view, and deleting the notice people actually see left every test green.
			if (this.view && this.state.name) {
				ui.drawn = drawn(() => this.render_view(this.view));
				entry.unsaved = ui.drawn.some((html) => html.includes("latest changes are not saved yet"));
				entry.loading = ui.drawn.some((html) => html.includes('class="tp-empty">Loading...'));
			}
			ui.renders.push(entry);
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

const TP_KEYS = ["trip", "crew", "there", "back", "lodging", "around", "freight", "schedule", "files", "review"];

// Every piece of markup `fn` hands to $(): the page builds each element from a template
// string, so this is what it drew. The page reads `$` as a global on every call.
function drawn(fn) {
	const seen = [];
	const real = globalThis.$;
	globalThis.$ = (arg) => {
		if (typeof arg === "string") seen.push(arg);
		return real(arg);
	};
	try {
		fn();
	} finally {
		globalThis.$ = real;
	}
	return seen;
}

// ------------------------------------------------------------------ harness

let browser;
let F;

function boot(page, startUrl) {
	for (const key of Object.keys(PAGES)) delete globalThis[`__klass_${PAGES[key].klass}`];
	clicks.length = 0;
	ui = { alerts: [], msgprints: [], renders: [], dialog: null, save_state: null, drawn: [] };
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
	Object.entries(trips).forEach(([name, trip]) => (db[name] = { ...clone(trip), name, modified: `${name}@1` }));
	let seq = 0;
	let rows = 0;
	let ids = 0;
	// A trip's files (Trip Document rows) are in the state only once the trip has any record of
	// them: a fixture without `documents` is a server that sends none, which the page must take.
	const state = (rec) =>
		Object.assign(
			{
				name: rec.name,
				modified: rec.modified,
				status: "Planning",
				can_write: true,
				trip: { ...rec.trip },
				travelers: clone(rec.travelers || []),
				bookings: clone(rec.bookings || { flights: [], accommodations: [], ground_transport: [] }),
				freight: clone(rec.freight || []),
				stops: [],
				gaps: clone(rec.gaps || []),
				// planner._viewer: whether the Trip Sheet print format exists on the site.
				sheet_available: rec.sheet_available === undefined ? true : rec.sheet_available,
			},
			rec.documents === undefined ? {} : { documents: clone(rec.documents) }
		);
	server.handlers.get_recent_plans = () => Object.keys(db).map((name) => ({ name, purpose: db[name].trip.purpose }));
	server.handlers.get_plan = (args) => ({
		lookups: { default_company: "SF", employees: [], currency: "USD" },
		state: args && args.trip ? state(db[args.trip]) : undefined,
	});
	// planner.save_plan, as far as keys go: a stored group id is kept and a page key
	// ("new:<n>", "row:<name>") is given an id (normalize_group), for cards and shipments alike;
	// then each file's booking, named by the page key, is remapped to that id (merge_documents),
	// and a file naming no booking the trip has is refused.
	server.handlers.save_plan = ({ plan, trip, modified }) => {
		const p = JSON.parse(plan);
		if (trip && db[trip].modified !== modified) return new Error("changed elsewhere");
		const name = trip || `TRIP-NEW-${++seq}`;
		const map = {};
		const labels = {};
		const stored = (key) => {
			if (/^[0-9a-f]{12}$/.test(key || "")) return key;
			if (!map[key]) map[key] = (++ids).toString(16).padStart(12, "0");
			return map[key];
		};
		const bookings = { flights: [], accommodations: [], ground_transport: [] };
		Object.entries(p.bookings || {}).forEach(([table, cards]) => {
			bookings[table] = cards.map((card) => {
				const group = stored(card.group);
				labels[group] = card.label;
				return {
					group,
					values: card.values,
					members: card.members.map((m) => ({ name: m.name || `ROW-${++rows}`, traveler: m.traveler, ref: m.ref })),
					protected: false,
				};
			});
		});
		// apply_plan's crew checks: a shipment's receiver and a file's person must be on the trip.
		const crew = new Set((p.travelers || []).map((t) => t.employee));
		if ((p.freight || []).some((item) => item.traveler && !crew.has(item.traveler))) {
			return new Error("receives a shipment but is not in the crew");
		}
		if ((p.documents || []).some((doc) => doc.traveler && !crew.has(doc.traveler))) {
			return new Error("is not on this trip");
		}
		const freight = (p.freight || []).map((item) => {
			const booking_group = stored(item.booking_group || `row:${item.name}`);
			labels[booking_group] = item.carrier;
			return { ...item, name: item.name || `FRT-${++rows}`, booking_group, protected: false };
		});
		const documents = [];
		for (const doc of p.documents || []) {
			const group = doc.booking_group ? stored(doc.booking_group) : "";
			if (group && !(group in labels)) return new Error("That file is on a booking this trip does not have.");
			documents.push({
				...doc,
				name: doc.name || `TDOC-${++rows}`,
				booking_group: group,
				booking_label: group ? labels[group] : null,
				file_name: String(doc.file).split("/").pop(),
				is_image: /\.(png|jpe?g)$/i.test(doc.file) ? 1 : 0,
			});
		}
		db[name] = {
			name,
			trip: p.trip,
			travelers: p.travelers,
			bookings,
			freight,
			documents,
			modified: `${name}@${++seq + 1}`,
		};
		return state(db[name]);
	};
	// api.travel.get_trip_views: the saved trip, as every view draws it. Minimal — the views'
	// drawing is not under test, where they sit in history is.
	server.handlers.get_trip_views = ({ trip }) => {
		const rec = db[trip];
		const crew = (rec.travelers || []).map((t) => ({
			employee: t.employee,
			employee_name: t.employee_name || t.employee,
			from_date: rec.trip.start_date,
			to_date: rec.trip.end_date,
			is_trip_lead: 0,
		}));
		const sheet = `/api/method/frappe.utils.print_format.download_pdf?doctype=Travel%20Trip&name=${encodeURIComponent(
			trip
		)}&format=Trip%20Sheet&no_letterhead=1&pdf_generator=chrome`;
		return {
			trip,
			purpose: rec.trip.purpose,
			start_date: rec.trip.start_date,
			end_date: rec.trip.end_date,
			is_coordinator: false,
			viewer_employee: null,
			itinerary_url: `/itinerary?trip=${encodeURIComponent(trip)}`,
			days: clone(rec.days || [rec.trip.start_date, rec.trip.end_date]),
			crew,
			whole: [],
			people: Object.fromEntries(crew.map((c) => [c.employee, []])),
			gaps: [],
			money: null,
			// PR 3 (2026-09-26): the Map's places and legs, the Maps key, who to call and the trip
			// sheet's address — a fixture that sets none gets what a trip with nothing to show gets.
			places: clone(rec.places || []),
			legs: clone(rec.legs || []),
			maps: clone(rec.maps || { api_key: "", map_id_light: "", map_id_dark: "" }),
			contacts: rec.contacts === undefined ? null : clone(rec.contacts),
			// The hotels on each person's card, by /itinerary's rule (api.travel._hotel_contacts);
			// a fixture that sets none is an answer without it.
			...(rec.people_hotels === undefined ? {} : { people_hotels: clone(rec.people_hotels) }),
			sheet_url: rec.sheet_url !== undefined ? rec.sheet_url : sheet,
			// views.trip_sheet_url(trip, employee): each person's own sheet, for View as.
			people_sheet_urls:
				rec.people_sheet_urls ||
				Object.fromEntries(crew.map((c) => [c.employee, `${sheet}&as=${encodeURIComponent(c.employee)}`])),
		};
	};
	// api.travel.preview_itinerary_email: rendered, never sent.
	server.handlers.preview_itinerary_email = ({ trip, employee }) => ({
		subject: `Your itinerary: ${db[trip].trip.purpose}`,
		to_name: employee,
		to_email: null,
		html: "<p>Your itinerary</p>",
		ics: "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n",
		ics_filename: "trip.ics",
		events: [],
		no_email: false,
	});
	return db;
}

const TRIP = {
	trip: { purpose: "Install", travel_type: "Domestic", start_date: "2026-10-01", end_date: "2026-10-03", trip_description: "" },
	travelers: [{ employee: "E1", employee_name: "Ana" }],
};

const CREW_TRIP = {
	trip: { ...TRIP.trip, purpose: "Crew install" },
	travelers: [
		{ employee: "E1", employee_name: "Ana" },
		{ employee: "E2", employee_name: "Ben" },
	],
};

// A trip with a file of its own (a Trip Document with no booking), as get_state sends it.
const SITE_MAP = {
	name: "TDOC-1",
	title: "Site map",
	kind: "Site map",
	file: "/private/files/site-map.pdf",
	traveler: "",
	booking_group: "",
	booking_label: null,
	file_name: "site-map.pdf",
	is_image: 0,
};

const DOC_TRIP = {
	trip: { ...TRIP.trip, purpose: "Install with files" },
	travelers: TRIP.travelers,
	documents: [SITE_MAP],
};

// frappe.ui.FileUploader, which the page may construct only from "Attach a file": records each
// uploader opened, so a test can hand it the files it "uploaded" (on_success, once per file).
function fakeUploader() {
	const opened = [];
	F.ui.FileUploader = class {
		constructor(opts) {
			opened.push(opts);
		}
	};
	return opened;
}

// The methods the page has called, in order, by their short name.
const called = () => server.calls.map((c) => c.method.split(".").pop());

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

	// ---- the views: Overview, Crew grid, Side by side, View as (&view=, &as=)

	await test("a view is one entry: opening it pushes once, Back returns to the step, Forward restores it", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review");
		await settle();
		const p = planner();
		const before = browser.hist.length;
		p.open_view("grid");
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review&view=grid");
		assert.equal(browser.hist.length, before + 1, browser.hist.urls().join(" | "));
		assert.equal(last().view, "grid");
		assert.equal(last().step, "review");
		assert.equal(server.sent("get_trip_views").length, 1);
		assert.ok(p.views && p.views.data, "the views' answer is kept for the other views");
		await back();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review");
		assert.equal(last().view, "", "Back must put the step back");
		assert.equal(last().step, "review");
		assert.equal(browser.hist.length, before + 1, "Back must not push");
		await forward();
		assert.equal(last().view, "grid", "Forward must restore the view");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review&view=grid");
		assert.equal(browser.hist.length, before + 1, "Forward must not push");
		p.open_view("overview"); // another view, from a view: its own entry
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review&view=overview");
		assert.equal(browser.hist.length, before + 2);
		assert.equal(server.sent("get_trip_views").length, 1, "one fetch per version of the trip, shared by every view");
		await back();
		assert.equal(last().view, "grid");
		await back();
		assert.equal(last().view, "");
		assert.deepEqual(ui.msgprints, []);
	});

	await test("'Back to planning' goes back through history onto its step, and pushes the step when nothing is behind", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review");
		await settle();
		const p = planner();
		p.open_view("overview");
		await settle();
		const entries = browser.hist.length;
		p.leave_view();
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review");
		assert.equal(last().view, "");
		assert.equal(browser.hist.length, entries, "view, Back to planning, view must pile nothing up");
		await forward();
		assert.equal(last().view, "overview");

		// A view opened from a link or a reload has nothing of the page's behind it.
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=lodging&view=compare");
		await settle();
		assert.equal(last().view, "compare");
		planner().leave_view();
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=lodging");
		assert.equal(last().view, "");
		assert.equal(last().step, "lodging");
		assert.equal(browser.hist.length, 2, "the step is a new entry");
		await back();
		assert.equal(last().view, "compare", "and Back returns to the view");
	});

	await test("a reload or deep link of a view's address lands on that view, and corrects an unknown person in place", async () => {
		tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review&view=grid");
		await settle();
		assert.equal(last().view, "grid");
		assert.equal(last().step, "review");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=review&view=grid");
		assert.equal(browser.hist.length, 1, "a reload adds no entry");
		assert.equal(server.sent("get_trip_views").length, 1);

		const says_missing = () => ui.drawn.some((html) => html.includes("not on the saved trip, so this shows"));
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=trip&view=person&as=E2");
		await settle();
		assert.equal(last().view, "person");
		assert.equal(last().as, "E2");
		assert.equal(says_missing(), false);

		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=trip&view=person&as=NOBODY");
		await settle();
		assert.equal(last().as, "E1", "someone not on the trip: the first person instead");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=trip&view=person&as=E1");
		assert.equal(browser.hist.length, 1);
		assert.equal(says_missing(), true, "...and it says so, rather than swapping people silently");
		assert.ok(!ui.drawn.some((html) => html.includes("NOBODY")), "an id from the address is never drawn");
		planner().open_view("person", "E2"); // a person picked on the page: nobody is missing
		await settle();
		assert.equal(says_missing(), false);

		const fetched = server.sent("get_trip_views").length;
		boot("plan-a-trip", "/desk/plan-a-trip?new=1&step=trip&view=grid");
		await settle();
		assert.equal(last().view, "", "a trip not saved yet has no views");
		assert.equal(server.sent("get_trip_views").length, fetched);
		planner().open_view("grid");
		await settle();
		assert.equal(last().view, "");
		assert.equal(browser.hist.length, 1, "nor a view entry");
	});

	await test("the form's 'Trip views' (frappe.set_route, v16: no query string) open a view, consumed once", async () => {
		tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/travel-trip/TRIP-2");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-2", view: "person", as: "E2" });
		await settle();
		assert.equal(last().view, "person");
		assert.equal(last().as, "E2");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=trip&view=person&as=E2", "frappe's bare entry is named");
		assert.equal(browser.hist.length, 2, "named in place, not pushed");
		// The trip already on screen: the form's button again, for another view.
		F.set_route("travel-trip", "TRIP-2");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-2", view: "grid" });
		await settle();
		assert.equal(last().view, "grid");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=trip&view=grid");
		// ...and "Plan step by step" onto it puts the steps back.
		F.set_route("travel-trip", "TRIP-2");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-2" });
		await settle();
		assert.equal(last().view, "");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=trip");
		// Consumed: a later plain visit shows the list, not a view.
		F.set_route("plan-a-trip");
		await settle();
		assert.deepEqual(last(), { landing: true });
	});

	await test("picking another person for View as is a new entry; Back walks back through the people", async () => {
		tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review");
		await settle();
		const p = planner();
		const before = browser.hist.length;
		p.open_view("person"); // "View as" with nobody picked: the first person
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=review&view=person&as=E1");
		p.open_view("person", "E2");
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=review&view=person&as=E2");
		assert.equal(browser.hist.length, before + 2);
		p.open_view("person", "E2"); // the same person again: nothing new
		await settle();
		assert.equal(browser.hist.length, before + 2);
		p.open_view("grid");
		await settle();
		p.open_view("person", "E1"); // a name tapped on the crew grid
		await settle();
		assert.equal(browser.hist.length, before + 4);
		await back();
		assert.equal(last().view, "grid");
		await back();
		assert.equal(last().view, "person");
		assert.equal(last().as, "E2");
		await back();
		assert.equal(last().as, "E1");
		await back();
		assert.equal(last().view, "");
		assert.equal(last().step, "review");
		assert.equal(server.sent("get_trip_views").length, 1);
	});

	await test("a get_trip_views answer that lands after the page moved on is dropped, not drawn or kept", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review");
		await settle();
		const p = planner();
		server.hold.add("get_trip_views");
		p.open_view("grid");
		await settle();
		assert.equal(last().view, "grid");
		await back(); // before the views arrived
		assert.equal(last().view, "");
		const renders = ui.renders.length;
		server.release("get_trip_views");
		await settle();
		assert.equal(ui.renders.length, renders, "the stale answer drew over the step");
		assert.equal(last().view, "");
		assert.equal(p.views, null, "the stale answer was kept");
		server.hold.delete("get_trip_views");
		await forward();
		assert.equal(last().view, "grid");
		assert.equal(server.sent("get_trip_views").length, 2, "the view on screen asks again");
		assert.ok(p.views && p.views.data);
	});

	await test("opening a view saves what was typed first, then shows the saved trip", async () => {
		const db = tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=trip");
		await settle();
		const p = planner();
		const before = browser.hist.length;
		p.state.trip.purpose = "Install, phase 2";
		p.open_view("overview");
		await settle();
		assert.equal(last().view, "overview");
		assert.equal(browser.hist.length, before + 1);
		assert.equal(db["TRIP-1"].trip.purpose, "Install, phase 2");
		assert.equal(p.is_dirty(), false);
		const order = called();
		assert.ok(order.indexOf("save_plan") >= 0 && order.indexOf("save_plan") < order.indexOf("get_trip_views"), order.join(","));
		assert.equal(p.views.key, `TRIP-1@${db["TRIP-1"].modified}`, "the views are the saved version's");
	});

	await test("a refused save never holds a view up: it opens, and the edit stays on the page, not saved", async () => {
		const db = tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review");
		await settle();
		const p = planner();
		const before = browser.hist.length;
		p.state.trip.purpose = ""; // problems(): refused before it reaches the server
		p.open_view("grid");
		await settle();
		assert.equal(last().view, "grid");
		assert.equal(browser.hist.length, before + 1);
		assert.deepEqual(ui.msgprints, [], "a view asked 'A few things to fill in first'");
		assert.equal(server.sent("save_plan").length, 0);
		assert.ok(p.is_dirty(), "the edit is still on the page");
		assert.equal(last().unsaved, true, "the view says the latest changes are not in it");
		assert.equal(last().loading, false, "the view is drawn");
		ui.save_state = null;
		await back();
		assert.equal(last().view, "");
		assert.equal(ui.save_state, "error", "back on the step, the edit is marked Not saved again");

		// Refused by the server (saved on the form meanwhile): the view still opens.
		p.state.trip.purpose = "Install, phase 2";
		db["TRIP-1"].modified = "TRIP-1@form";
		p.open_view("compare");
		await settle();
		assert.equal(last().view, "compare");
		assert.equal(server.sent("save_plan").length, 1);
		assert.ok(p.is_dirty());
		assert.equal(last().unsaved, true);

		// A view drawn with nothing unsaved says nothing of the kind.
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review&view=grid");
		await settle();
		assert.equal(last().view, "grid");
		assert.equal(last().unsaved, false);
	});

	await test("a tab or a checklist 'Fix' link in a view puts the step back as a new entry", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review");
		await settle();
		const p = planner();
		p.open_view("grid");
		await settle();
		const entries = browser.hist.length;
		p.go(p.step); // the tab of the step the view is drawn over
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=review");
		assert.equal(last().view, "");
		assert.equal(browser.hist.length, entries + 1);
		await back();
		assert.equal(last().view, "grid");
		p.jump_to("lodging"); // a "Fix" link on a missing bed
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=lodging");
		assert.equal(last().view, "");
		assert.equal(last().step, "lodging");
		assert.equal(browser.hist.length, entries + 1, "the entry after the view is replaced by the new move");
		await back();
		assert.equal(last().view, "grid");
		assert.equal(last().step, "review");
		await forward();
		assert.equal(last().view, "");
		assert.equal(last().step, "lodging");
	});

	await test("a Forward refused from a view's entry goes back to the view and says why there", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=crew");
		await settle();
		const p = planner();
		p.open_view("grid");
		await settle();
		p.jump_to("review"); // a "Fix" link to a later step: a move forward, saved
		await settle();
		assert.equal(last().step, "review");
		assert.equal(last().view, "");
		await back();
		assert.equal(last().view, "grid");
		assert.equal(last().step, "crew");
		p.state.trip.purpose = ""; // a later step is refused now
		const entries = browser.hist.urls();
		const at = browser.hist.index;
		await forward();
		assert.equal(last().view, "grid", "the refused Forward must come back to the view");
		assert.equal(browser.hist.index, at);
		assert.deepEqual(browser.hist.urls(), entries, "the entry asked for stays, to go Forward to once fixed");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=crew&view=grid");
		assert.equal(ui.dialog, "A few things to fill in first", "the reason must be open after the return");
	});

	await test("View as's email preview is part of its screen: no entry, never sends, and a late answer is dropped", async () => {
		tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review&view=person&as=E1");
		await settle();
		const p = planner();
		const entries = browser.hist.length;
		p.toggle_preview("E1");
		await settle();
		assert.deepEqual(server.sent("preview_itinerary_email").map((c) => c.args), [{ trip: "TRIP-2", employee: "E1" }]);
		assert.equal(p.preview.data.subject, "Your itinerary: Crew install");
		assert.equal(browser.hist.length, entries, "opening the preview must not push");
		assert.equal(server.sent("send_itinerary_email").length, 0, "a preview must never send");
		p.toggle_preview("E1"); // closed
		p.toggle_preview("E1"); // open again: the answer is kept
		await settle();
		assert.equal(server.sent("preview_itinerary_email").length, 1);

		server.hold.add("preview_itinerary_email");
		p.open_view("person", "E2");
		await settle();
		p.toggle_preview("E2");
		await settle();
		p.open_view("person", "E1"); // someone else before the preview arrived
		await settle();
		const renders = ui.renders.length;
		server.release("preview_itinerary_email");
		await settle();
		assert.equal(ui.renders.length, renders, "the late preview drew over the next person");
		assert.equal(p.preview, null);
	});

	// ---- the trip on screen, saved elsewhere meanwhile

	await test("the form's Trip views onto the trip on screen show what the form saved, not the page's old copy", async () => {
		const db = tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review");
		await settle();
		const p = planner();
		p.open_view("overview");
		await settle();
		assert.equal(server.sent("get_trip_views").length, 1);
		// "Open the full form", where Cy is added and saved.
		F.set_route("travel-trip", "TRIP-2");
		await settle();
		db["TRIP-2"].travelers = [...db["TRIP-2"].travelers, { employee: "E3", employee_name: "Cy" }];
		db["TRIP-2"].modified = "TRIP-2@form";
		F.set_route("plan-a-trip", { trip: "TRIP-2", view: "grid" }); // Trip views > Crew grid
		await settle();
		assert.equal(last().view, "grid");
		assert.equal(last().step, "review", "the step showing is kept");
		assert.equal(p.state.modified, "TRIP-2@form", "the trip was loaded again");
		assert.equal(server.sent("get_trip_views").length, 2, "the grid drew the old version's cached answer");
		assert.deepEqual(p.views.data.crew.map((c) => c.employee), ["E1", "E2", "E3"]);
		F.set_route("travel-trip", "TRIP-2");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-2", view: "person", as: "E3" }); // Trip views > View as Cy
		await settle();
		assert.equal(last().as, "E3", "View as swapped in the default person for Cy");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=review&view=person&as=E3");
		assert.ok(!ui.drawn.some((html) => html.includes("not on the saved trip")));

		// Back from the form onto one of the page's own entries, after another save there.
		F.set_route("travel-trip", "TRIP-2");
		await settle();
		db["TRIP-2"].travelers = db["TRIP-2"].travelers.filter((t) => t.employee !== "E2");
		db["TRIP-2"].modified = "TRIP-2@form2";
		await back();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-2&step=review&view=person&as=E3");
		assert.equal(last().as, "E3");
		assert.equal(p.state.modified, "TRIP-2@form2");
		assert.deepEqual(p.views.data.crew.map((c) => c.employee), ["E1", "E3"]);
		// A later save from the page is not refused as changed elsewhere.
		p.state.trip.purpose = "Crew install, phase 2";
		p.leave_view();
		await settle();
		p.jump_to("crew");
		await settle();
		assert.equal(db["TRIP-2"].trip.purpose, "Crew install, phase 2");
		assert.deepEqual(ui.msgprints, []);
	});

	await test("with unsaved changes on the page, the form's Trip views keep them and ask only for the views again", async () => {
		const db = tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review");
		await settle();
		const p = planner();
		p.open_view("grid");
		await settle();
		p.state.trip.purpose = ""; // half-finished: leaving the page cannot save it
		F.set_route("travel-trip", "TRIP-2");
		await settle();
		db["TRIP-2"].travelers = [...db["TRIP-2"].travelers, { employee: "E3", employee_name: "Cy" }];
		db["TRIP-2"].modified = "TRIP-2@form";
		F.set_route("plan-a-trip", { trip: "TRIP-2", view: "overview" });
		await settle();
		assert.equal(last().view, "overview");
		assert.equal(p.state.trip.purpose, "", "what was typed is still on the page");
		assert.equal(server.sent("get_plan").length, 1, "the trip was loaded again over what was typed");
		assert.equal(server.sent("get_trip_views").length, 2, "the views were not asked for again");
		assert.deepEqual(p.views.data.crew.map((c) => c.employee), ["E1", "E2", "E3"]);
		assert.equal(last().unsaved, true);
	});

	await test("a move that lands back on a view still waiting for its answer asks again: never Loading... for good", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=crew");
		await settle();
		const p = planner();
		p.open_view("grid");
		await settle();
		p.state.trip.purpose = "Changed";
		p.jump_to("review"); // a Fix link forward: saved, so the trip has a new version
		await settle();
		assert.equal(last().step, "review");
		server.hold.add("get_trip_views");
		await back(); // onto the grid's entry: the new version's views are asked for, and held
		assert.equal(last().view, "grid");
		assert.equal(last().loading, true);
		p.state.trip.purpose = ""; // the next Forward is refused, and comes straight back here
		await forward();
		assert.equal(last().view, "grid");
		assert.equal(ui.dialog, "A few things to fill in first");
		assert.equal(server.sent("get_trip_views").length, 3, "the view on screen did not ask again");
		server.release("get_trip_views"); // the first answer: for a move since overtaken, dropped
		await settle();
		server.release("get_trip_views"); // the one asked again once the page was back here
		await settle();
		assert.equal(p.views && p.views.key, p.views_key());
		assert.equal(last().view, "grid");
		assert.equal(last().loading, false, "the view was left saying Loading...");
	});

	await test("an open preview still waiting for its answer is asked for again when a move comes straight back to it", async () => {
		tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=crew");
		await settle();
		const p = planner();
		p.open_view("person", "E1");
		await settle();
		p.jump_to("review");
		await settle();
		await back(); // onto View as Ana
		assert.equal(last().as, "E1");
		server.hold.add("preview_itinerary_email");
		p.toggle_preview("E1");
		await settle();
		p.state.trip.purpose = "";
		await forward(); // refused: straight back to this view
		assert.equal(last().as, "E1");
		assert.equal(server.sent("preview_itinerary_email").length, 2, "the preview did not ask again");
		server.release("preview_itinerary_email");
		await settle();
		server.release("preview_itinerary_email");
		await settle();
		assert.equal(p.preview && p.preview.data && p.preview.data.subject, "Your itinerary: Crew install");
	});

	// ---- what the views draw

	await test("people left out of Side by side on one trip are not left out of another's", async () => {
		tripServer({ "TRIP-2": CREW_TRIP, "TRIP-3": { ...CREW_TRIP, trip: { ...CREW_TRIP.trip, purpose: "Second crew" } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review&view=compare");
		await settle();
		const chip = (name) => ui.drawn.find((html) => html.includes('class="tp-chip ') && html.includes(name));
		assert.ok(chip("Ana").includes("tp-on"));
		press("&#10003; Ana"); // leave Ana out
		assert.ok(!chip("Ana").includes("tp-on"), "Ana is left out on this trip");
		F.set_route("plan-a-trip", { trip: "TRIP-3", view: "compare" });
		await settle();
		assert.equal(last().trip, "TRIP-3");
		assert.equal(last().view, "compare");
		assert.ok(chip("Ana").includes("tp-on"), "...and was left out on the next trip too");
	});

	await test("the Crew grid counts no night in a room with no check-out day: the missing bed stands alone", async () => {
		tripServer({ "TRIP-1": TRIP });
		const views = server.handlers.get_trip_views;
		server.handlers.get_trip_views = (args) => {
			const data = views(args);
			data.days = ["2026-10-01", "2026-10-02", "2026-10-03"];
			data.people.E1 = [
				{
					date: "2026-10-01",
					items: [{ type: "hotel_checkin", date: "2026-10-01", group: "g7", hotel: "Half Booked Inn" }],
				},
			];
			// What completeness.lodging_gaps says about it: no bed either night, and the room's
			// check-out day missing.
			data.gaps = [
				{ check: "lodging", kind: "nights", step: "lodging", employee: "E1", nights: ["2026-10-01", "2026-10-02"] },
				{ check: "lodging", kind: "dates", step: "lodging", group: "g7", label: "Half Booked Inn" },
			];
			return data;
		};
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review&view=grid");
		await settle();
		const grid = ui.drawn.find((html) => html.includes("Check in: Half Booked Inn"));
		assert.ok(grid, "the grid drew the check-in");
		assert.ok(grid.includes("No bed tonight"));
		assert.ok(!grid.includes("Night in a room"), "the same cell said 'Night in a room' and 'No bed tonight'");
	});

	await test("the Overview's tiles count bookings like Review does, and a trip's days as the trip's own", async () => {
		tripServer({ "TRIP-1": TRIP });
		const views = server.handlers.get_trip_views;
		server.handlers.get_trip_views = (args) => {
			const data = views(args);
			// A flight the day before the trip starts, and one of two rooms with no dates yet,
			// which is on no day at all.
			data.days = ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03"];
			data.whole = [
				{ date: "2026-09-30", items: [{ type: "flight", date: "2026-09-30", group: "g1", members: [] }] },
				{ date: "2026-10-01", items: [{ type: "hotel_checkin", date: "2026-10-01", group: "g3", members: [] }] },
			];
			data.bookings = { flights: 1, accommodations: 2, ground_transport: 0, freight: 0 };
			return data;
		};
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review&view=overview");
		await settle();
		const tiles = ui.drawn.find((html) => html.includes('class="tp-sum"'));
		const tile = (label) => (tiles.match(new RegExp(`${label}</span><b>([^<]*)</b>`)) || [])[1];
		assert.equal(tile("Rooms"), "2", "the room with no dates went uncounted");
		assert.equal(tile("Flights"), "1");
		assert.equal(tile("Days"), "3", "the day before the trip made it a day longer");
	});

	await test("Review offers 'Email everyone' only once the server has said the viewer is a coordinator", async () => {
		tripServer({ "TRIP-2": CREW_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review");
		await settle();
		const p = planner();
		const offered = (viewer) => {
			p.viewer = viewer;
			return drawn(() => p.step_review($stub("review")))
				.filter((html) => /Email (everyone|me)/.test(html))
				.map((html) => html.replace(/<[^>]+>/g, ""));
		};
		// "Email everyone" fails every time for anyone but a coordinator (send_itinerary_email).
		assert.deepEqual(offered({ is_coordinator: null, employee: "E1" }), [], "not known yet: neither");
		assert.deepEqual(offered({ is_coordinator: false, employee: "E1" }), ["Email me my itinerary"]);
		assert.deepEqual(offered({ is_coordinator: false, employee: "E9" }), [], "not on the crew: nothing to send");
		assert.deepEqual(offered({ is_coordinator: true, employee: null }), ["Email everyone their itinerary"]);
	});

	// ---- files: the paperwork on each booking and the trip's own (Trip Document rows)

	await test("a trip's files survive a save, and a server that sends none is taken as none", async () => {
		const db = tripServer({ "TRIP-1": TRIP, "TRIP-4": DOC_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=trip");
		await settle();
		let p = planner();
		assert.deepEqual(p.state.documents, [], "no documents in the answer: none");
		assert.deepEqual(p.payload().documents, []);
		assert.equal(p.is_dirty(), false);

		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-4&step=trip");
		await settle();
		p = planner();
		assert.equal(p.is_dirty(), false, "a file as loaded is not an edit");
		const row = { name: "TDOC-1", title: "Site map", kind: "Site map", file: "/private/files/site-map.pdf", booking_group: "" };
		assert.deepEqual(p.payload().documents, [{ ...row, traveler: "" }]);
		p.state.documents[0].traveler = "E1"; // For: only Ana
		assert.ok(p.is_dirty(), "who a file is for is an edit");
		p.go(1);
		await settle();
		assert.equal(last().step, "crew");
		const sent = JSON.parse(server.sent("save_plan")[0].args.plan).documents;
		assert.deepEqual(sent, [{ ...row, traveler: "E1" }], "sent as a row: name, title, kind, file, traveler, booking");
		assert.equal(db["TRIP-4"].documents[0].traveler, "E1");
		assert.deepEqual(
			p.state.documents.map((d) => [d.name, d.traveler, d.booking_group]),
			[["TDOC-1", "E1", ""]],
			"the saved trip's files are on the page"
		);
		assert.equal(p.is_dirty(), false);
		p.go(2); // nothing changed since: no second save
		await settle();
		assert.equal(server.sent("save_plan").length, 1);
	});

	await test("files attached to a booking not saved yet follow it to the id its first save gives it", async () => {
		const db = tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=there");
		await settle();
		const p = planner();
		const opened = fakeUploader();
		const card = p.new_card("flights", "Outbound", ["E1"]);
		Object.assign(card.values, { airline: "Southwest", flight_number: "WN 1422" });
		p.cards("flights").push(card);
		const key = card.group;
		assert.match(key, /^new:\d+$/);
		p.attach_files(p.booking_target(card)); // "Attach a file" on the card
		assert.equal(opened.length, 1);
		const opts = opened[0];
		assert.deepEqual(
			[opts.doctype, opts.docname, opts.folder, opts.make_attachments_public, opts.allow_multiple],
			["Travel Trip", "TRIP-1", "Home/Attachments", false, true]
		);
		// frappe offers a "Private" box per file and "Set all public" unless told not to; a
		// public File is served to anyone at /files/<name>. And a trip's files are uploads.
		assert.deepEqual([opts.allow_toggle_private, opts.allow_web_link], [false, false]);
		opts.on_success({ name: "F-1", file_url: "/private/files/ana-bp.pdf", file_name: "ana-bp.pdf" });
		opts.on_success({ name: "F-2", file_url: "/private/files/ana-bp-2.png", file_name: "ana-bp-2.png" });
		assert.deepEqual(
			p.state.documents.map((d) => [d.name, d.booking_group, d.kind, d.traveler, d.title, d.is_image]),
			[
				[null, key, "Boarding pass", "E1", "ana-bp.pdf", 0],
				[null, key, "Boarding pass", "E1", "ana-bp-2.png", 1],
			],
			"one file per upload, on the card, for the flight's one person, named after the file"
		);
		assert.ok(p.is_dirty(), "a file attached is an edit, saved with the next save");
		assert.equal(browser.hist.length, 1, "attaching a file is not a screen");

		// A shipment added now, with its bill of lading.
		p.state.freight.push({ name: null, carrier: "Old Dominion", booking_group: `new:${++p.card_seq}` });
		const shipment = p.state.freight[0];
		p.attach_files(p.freight_target(shipment));
		opened[1].on_success({ name: "F-3", file_url: "/private/files/bol.pdf", file_name: "bol.pdf" });
		assert.deepEqual(p.state.documents[2].booking_group, shipment.booking_group);
		assert.equal(p.state.documents[2].kind, "Bill of lading");

		p.go(3); // Next: the save
		await settle();
		assert.equal(last().step, "back");
		assert.deepEqual(ui.msgprints, []);
		const sent = JSON.parse(server.sent("save_plan")[0].args.plan);
		assert.equal(sent.bookings.flights[0].group, key);
		assert.equal(sent.freight[0].booking_group, shipment.booking_group, "a shipment sends its page key, like a card");
		assert.deepEqual(
			sent.documents.map((d) => d.booking_group),
			[key, key, shipment.booking_group],
			"each file names its booking by the page key"
		);
		const flight = p.cards("flights")[0];
		const freight = p.state.freight[0];
		assert.match(flight.group, /^[0-9a-f]{12}$/, "the card has its id");
		assert.match(freight.booking_group, /^[0-9a-f]{12}$/, "the shipment has its id");
		assert.equal(p.documents_for(flight.group).length, 2, "the boarding passes came back on the flight");
		assert.equal(p.documents_for(p.freight_key(freight)).length, 1, "the bill of lading came back on the shipment");
		assert.deepEqual(p.documents_for(key), []);
		assert.equal(p.is_dirty(), false);
		assert.equal(db["TRIP-1"].documents.length, 3);
	});

	await test("a file that finishes uploading after its booking changed is listed with the whole trip, never lost", async () => {
		tripServer({ "TRIP-1": TRIP, "TRIP-4": DOC_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=crew");
		await settle();
		const p = planner();
		p.go(2);
		await settle();
		const opened = fakeUploader();
		const card = p.new_card("flights", "Outbound", ["E1"]);
		Object.assign(card.values, { airline: "Southwest", flight_number: "WN 1422" });
		p.cards("flights").push(card);
		p.attach_files(p.booking_target(card));
		await back(); // the phone's Back while it uploads: saved quietly, the cards are new ones
		assert.equal(last().step, "crew");
		assert.equal(server.sent("save_plan").length, 1);
		assert.ok(!p.cards("flights").includes(card));
		opened[0].on_success({ name: "F-1", file_url: "/private/files/late.pdf", file_name: "late.pdf" });
		assert.deepEqual(
			p.state.documents.map((d) => [d.booking_group, d.title]),
			[["", "late.pdf"]],
			"listed with the trip's own files"
		);
		assert.ok(ui.alerts.some((a) => a.includes("whole trip's files")), "and it says so");

		// Another trip on screen by the time it lands: nothing is added to that one.
		p.attach_files(p.trip_target());
		F.set_route("plan-a-trip", { trip: "TRIP-4" });
		await settle();
		assert.equal(p.state.name, "TRIP-4");
		opened[1].on_success({ name: "F-2", file_url: "/private/files/elsewhere.pdf", file_name: "elsewhere.pdf" });
		assert.deepEqual(p.state.documents.map((d) => d.name), ["TDOC-1"]);
		assert.ok(ui.alerts.some((a) => a.includes("no longer open here")));
	});

	await test("a file that finishes uploading after a save redrew its saved booking stays on that booking", async () => {
		const flight = {
			group: "aaaaaaaaaaaa",
			values: { leg: "Outbound", airline: "Southwest", flight_number: "WN 1", departure_time: "", arrival_time: "" },
			members: [{ name: "R1", traveler: "E1", ref: "" }],
		};
		tripServer({ "TRIP-2": { ...CREW_TRIP, bookings: { flights: [flight], accommodations: [], ground_transport: [] } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=crew");
		await settle();
		const p = planner();
		p.go(2);
		await settle();
		const opened = fakeUploader();
		const card = p.cards("flights")[0];
		p.attach_files(p.booking_target(card));
		p.set_value(card, "flight_number", "WN 2"); // an edit, so Back saves
		const alerts = ui.alerts.length;
		await back(); // saved quietly while it uploads: the cards are new ones
		assert.equal(server.sent("save_plan").length, 1);
		assert.ok(!p.cards("flights").includes(card));
		opened[0].on_success({ name: "F-1", file_url: "/private/files/late.pdf", file_name: "late.pdf" });
		assert.deepEqual(
			p.state.documents.map((d) => [d.booking_group, d.title]),
			[["aaaaaaaaaaaa", "late.pdf"]],
			"still on the flight: it had its id before the save"
		);
		assert.equal(ui.alerts.length, alerts, "nothing to say");
	});

	// An upload keeps going when its dialog is closed: by the phone's Back (frappe closes every
	// dialog on a route change), its X, or a tap outside it. So a file can land while a save is
	// in flight. That save sent the page before the file was on it, and its answer replaces the
	// page's trip, so the file's row was dropped without a word, the page read as saved, and the
	// File was left attached to the trip but on no list.
	const SHARED_FLIGHT = {
		group: "aaaaaaaaaaaa",
		values: { leg: "Outbound", airline: "Southwest", flight_number: "WN 1", departure_time: "", arrival_time: "" },
		members: [
			{ name: "R1", traveler: "E1", ref: "" },
			{ name: "R2", traveler: "E2", ref: "" },
		],
	};
	const SHARED_TRIP = {
		...CREW_TRIP,
		bookings: { flights: [SHARED_FLIGHT], accommodations: [], ground_transport: [] },
		documents: [],
	};
	const filed = (p) => p.state.documents.map((d) => [d.booking_group, d.title, d.traveler]);
	const lost = () => ui.alerts.filter((a) => /no longer open here|whole trip's files/.test(a));

	await test("a file that lands while a save is in flight is kept: the phone's Back between steps", async () => {
		tripServer({ "TRIP-2": SHARED_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=crew");
		await settle();
		const p = planner();
		p.go(2);
		await settle();
		const opened = fakeUploader();
		p.attach_files(p.booking_target(p.cards("flights")[0]));
		p.set_value(p.cards("flights")[0], "flight_number", "WN 2"); // an edit, so Back saves
		server.hold.add("save_plan");
		await back(); // closes the uploader; the upload goes on
		assert.equal(server.held.length, 1, "the save is in flight");
		opened[0].on_success({ name: "F-1", file_url: "/private/files/mid.pdf", file_name: "mid.pdf" });
		server.hold.delete("save_plan");
		server.release("save_plan");
		await settle();
		assert.equal(last().step, "crew");
		assert.equal(p.cards("flights")[0].values.flight_number, "WN 2", "the save's answer is on the page");
		assert.deepEqual(filed(p), [["aaaaaaaaaaaa", "mid.pdf", "E1"]], "on its flight, for the first person on it without one");
		assert.ok(p.is_dirty(), "and saved with the next save, not taken for saved");
		assert.deepEqual(lost(), []);
		p.go(2);
		await settle();
		assert.equal(server.sent("save_plan").length, 2);
		assert.deepEqual(JSON.parse(server.sent("save_plan")[1].args.plan).documents.map((d) => d.file), ["/private/files/mid.pdf"]);
	});

	await test("a file that lands while a save is in flight is kept: leaving for the form, and Back onto the page", async () => {
		tripServer({ "TRIP-2": SHARED_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=there");
		await settle();
		const p = planner();
		const opened = fakeUploader();
		p.attach_files(p.booking_target(p.cards("flights")[0]));
		p.state.trip.purpose = "Crew install, phase 2";
		server.hold.add("save_plan");
		F.set_route("travel-trip", "TRIP-2"); // "Open the full form": on_hide saves quietly
		await settle();
		assert.equal(server.held.length, 1, "the save is in flight");
		opened[0].on_success({ name: "F-1", file_url: "/private/files/mid.pdf", file_name: "mid.pdf" });
		server.hold.delete("save_plan");
		server.release("save_plan");
		await settle();
		assert.deepEqual(filed(p), [["aaaaaaaaaaaa", "mid.pdf", "E1"]]);
		assert.ok(p.is_dirty());
		await back(); // onto the page: something unsaved, so what is on it stays
		assert.equal(last().step, "there");
		assert.deepEqual(filed(p), [["aaaaaaaaaaaa", "mid.pdf", "E1"]]);
		assert.deepEqual(lost(), []);
	});

	await test("files that land during a Next's save are kept, each for the next person on the flight without one", async () => {
		tripServer({ "TRIP-2": SHARED_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=there");
		await settle();
		const p = planner();
		const opened = fakeUploader();
		p.attach_files(p.booking_target(p.cards("flights")[0]));
		// Two boarding passes picked at once: the first lands, the dialog is closed with its X
		// while the second uploads, and Next saves.
		opened[0].on_success({ name: "F-1", file_url: "/private/files/ana-bp.pdf", file_name: "ana-bp.pdf" });
		server.hold.add("save_plan");
		p.go(3);
		await settle();
		opened[0].on_success({ name: "F-2", file_url: "/private/files/ben-bp.pdf", file_name: "ben-bp.pdf" });
		assert.deepEqual(
			JSON.parse(server.held[0].call.args.plan).documents.map((d) => d.title),
			["ana-bp.pdf"],
			"the save in flight was sent before the second one landed"
		);
		server.hold.delete("save_plan");
		server.release("save_plan");
		await settle();
		assert.equal(last().step, "back");
		assert.deepEqual(filed(p), [
			["aaaaaaaaaaaa", "ana-bp.pdf", "E1"],
			["aaaaaaaaaaaa", "ben-bp.pdf", "E2"],
		]);
		assert.ok(p.is_dirty());
		// Everyone on it has theirs: a third file is for everyone on the flight (the ticket).
		opened[0].on_success({ name: "F-3", file_url: "/private/files/ticket.pdf", file_name: "ticket.pdf" });
		assert.deepEqual(filed(p)[2], ["aaaaaaaaaaaa", "ticket.pdf", ""]);
		assert.deepEqual(lost(), []);
	});

	await test("a file that lands once its trip was put aside unsaved goes on that trip, and Carry on brings it back", async () => {
		tripServer({ "TRIP-2": SHARED_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		press("tp-list-item", { currentTarget: { __attrs: { "data-name": "TRIP-2" } } });
		await settle();
		const p = planner();
		const opened = fakeUploader();
		p.attach_files(p.booking_target(p.cards("flights")[0]));
		p.state.trip.purpose = "Changed";
		server.fail.add("save_plan"); // no answer: the trip is kept, not dropped
		await back();
		assert.deepEqual(last(), { landing: true });
		assert.deepEqual(Object.keys(p.kept), ["TRIP-2"]);
		opened[0].on_success({ name: "F-1", file_url: "/private/files/late.pdf", file_name: "late.pdf" });
		assert.deepEqual(lost(), [], "it is not gone: it is kept here");
		assert.ok(ui.alerts.some((a) => a.includes("late.pdf is on Changed's files")));
		server.fail.delete("save_plan");
		p.carry_on("TRIP-2");
		await settle();
		assert.equal(p.state.trip.purpose, "Changed");
		assert.deepEqual(filed(p), [["aaaaaaaaaaaa", "late.pdf", "E1"]]);
		assert.ok(p.is_dirty());
	});

	await test("a file that lands while its trip is loading again (Back from the form) goes on it once it is here", async () => {
		tripServer({ "TRIP-2": SHARED_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=there");
		await settle();
		const p = planner();
		const opened = fakeUploader();
		p.attach_files(p.booking_target(p.cards("flights")[0]));
		F.set_route("travel-trip", "TRIP-2"); // nothing unsaved
		await settle();
		server.hold.add("get_plan");
		await back(); // onto the page: it may have been saved on the form, so it loads again
		assert.equal(p.state, null, "loading");
		opened[0].on_success({ name: "F-1", file_url: "/private/files/late.pdf", file_name: "late.pdf" });
		server.hold.delete("get_plan");
		server.release("get_plan");
		await settle();
		assert.equal(last().step, "there");
		assert.deepEqual(filed(p), [["aaaaaaaaaaaa", "late.pdf", "E1"]]);
		assert.ok(p.is_dirty());
		assert.deepEqual(lost(), []);
	});

	await test("unticking someone on a booking or with files of their own asks first; ticking them stays until yes", async () => {
		tripServer({
			"TRIP-2": { ...SHARED_TRIP, documents: [{ ...SITE_MAP, name: "TDOC-2", traveler: "E2" }] },
		});
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=crew");
		await settle();
		const p = planner();
		p.lookups.employees = [
			{ name: "E1", employee_name: "Ana" },
			{ name: "E2", employee_name: "Ben" },
			{ name: "E3", employee_name: "Cy" },
		];
		const asked = [];
		let yes = null;
		F.confirm = (message, ok) => {
			asked.push(message);
			yes = ok;
		};
		const untick = (name) => {
			clicks.length = 0;
			// The stand-in jQuery holds no values: the "Find a person" box reads as empty.
			const real = globalThis.$;
			globalThis.$ = (arg) => {
				const out = real(arg);
				if (typeof arg !== "string" || !arg.includes("Find a person")) return out;
				const search = new Proxy(out, {
					get: (t, prop) => (prop === "val" ? () => "" : prop === "appendTo" ? () => search : Reflect.get(t, prop)),
				});
				return search;
			};
			try {
				p.step_crew($stub("crew"));
			} finally {
				globalThis.$ = real;
			}
			const box = clicks.find((c) => c.event === "change" && c.src.includes(name) && c.src.endsWith('input[type="checkbox"]'));
			const target = { checked: false };
			box.fn({ target });
			return target;
		};
		const box = untick("Ben");
		assert.deepEqual(asked, [
			"Take Ben off the trip? They come off 1 booking, and their confirmation number on it. 1 file for them comes off the trip's list; it stays attached to the trip on the full form.",
		]);
		assert.equal(box.checked, true, "still ticked until they say yes");
		assert.deepEqual(p.crew().map((t) => t.employee), ["E1", "E2"], "nothing taken yet");
		yes();
		assert.deepEqual(p.crew().map((t) => t.employee), ["E1"]);
		assert.deepEqual(p.cards("flights")[0].members.map((m) => m.traveler), ["E1"]);
		assert.deepEqual(p.state.documents, []);
		// Nothing to lose: no question.
		p.add_traveler({ name: "E3", employee_name: "Cy" });
		asked.length = 0;
		untick("Cy");
		assert.deepEqual(asked, []);
		assert.deepEqual(p.crew().map((t) => t.employee), ["E1"]);
		assert.equal(browser.hist.length, 1, "a question is not a screen");
	});

	await test("the Files step is a step like the others, and nothing on the way to it touches the uploader", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=files");
		// Watched from before the page loads: a load, a route or a step's drawing that reaches
		// for frappe.ui.FileUploader would find it missing on a desk that has not loaded it.
		let touched = 0;
		Object.defineProperty(F.ui, "FileUploader", {
			configurable: true,
			get() {
				touched += 1;
				return undefined;
			},
		});
		await settle();
		assert.equal(last().step, "files");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=files");
		const p = planner();
		p.go(9); // Next: Review
		await settle();
		assert.equal(last().step, "review");
		await back();
		assert.equal(last().step, "files");
		p.go(7); // the Schedule tab
		await settle();
		p.go(8); // Next: Files
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-1&step=files");
		await back();
		assert.equal(last().step, "schedule");
		await forward();
		assert.equal(last().step, "files");
		assert.equal(browser.hist.length, 3, browser.hist.urls().join(" | "));
		const html = drawn(() => p.step_files($stub("files")));
		assert.ok(html.some((h) => h.includes("Attach a file")), "a saved trip offers Attach a file");
		assert.equal(touched, 0, "frappe.ui.FileUploader was touched outside a click");
		assert.deepEqual(ui.msgprints, []);
	});

	await test("Attach a file is inert on a trip not saved yet, and for someone who may not change the trip", async () => {
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip");
		await settle();
		press('data-action="new"');
		await settle();
		let p = planner();
		const opened = fakeUploader();
		Object.assign(p.state.trip, { purpose: "Survey", start_date: "2026-11-01", end_date: "2026-11-02" });
		const html = drawn(() => p.step_files($stub("files")));
		assert.ok(html.some((h) => h.includes("Save the trip first to attach files")));
		assert.ok(!html.some((h) => h.includes("Attach a file")), "no button without a saved trip");
		p.attach_files(p.trip_target());
		p.attach_files(p.booking_target(p.new_card("flights", "Outbound", [])));
		assert.equal(opened.length, 0, "a File is attached to a trip by its name: there is none yet");
		assert.deepEqual(p.state.documents, []);

		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=files");
		await settle();
		p = planner();
		const later = fakeUploader();
		p.state.can_write = false;
		const view_only = drawn(() => p.step_files($stub("files")));
		assert.ok(!view_only.some((h) => h.includes("Attach a file")));
		p.attach_files(p.trip_target());
		assert.equal(later.length, 0);
	});

	// Paperwork (completeness.document_gaps, check "documents") is a separate, quieter tally (Nik,
	// 2026-09-26): shown on the card, the Files step and Review, counted as files, muted, and never
	// in a tab's badge, "Mark as booked"'s count, a red card or the Overview's "Still missing".
	const PAPER = {
		check: "documents",
		step: "there",
		table: "flights",
		group: "0123456789ab",
		label: "Southwest WN 1422",
		employee_names: ["Ana"],
		kinds: ["Boarding pass", "Booking confirmation"],
	};
	const NO_NUMBER = {
		check: "confirmation",
		step: "there",
		table: "flights",
		group: "fedcba987654",
		label: "Delta DL 88",
		employee_names: ["Ana"],
	};

	await test("Review, the Files step and a booking's card show paperwork quietly, counted as files", async () => {
		// Two people on one flight without their boarding passes are two files; a room is one.
		const room = { ...PAPER, step: "lodging", table: "accommodations", group: "abcdefabcdef", label: "Hilton", employee_names: [] };
		const pair = { ...PAPER, employee_names: ["Ana", "Ben"] };
		tripServer({ "TRIP-4": { ...DOC_TRIP, gaps: [PAPER] } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-4&step=review");
		await settle();
		const p = planner();
		const review = drawn(() => p.step_review($stub("review")));
		assert.ok(review.some((h) => h.includes("<h5>Paperwork</h5>")), "a Paperwork section");
		const line = review.find((h) => h.includes("Southwest WN 1422: no boarding pass or ticket for Ana."));
		assert.ok(line, "the booking and who is missing theirs");
		assert.ok(line.includes('class="tp-paper"') && !line.includes("tp-gap"), "a muted note, not a missing item");
		assert.ok(review.some((h) => h.includes("tp-paper-count") && h.includes("1 file not attached yet")), "its own count");
		assert.ok(review.some((h) => h.includes("Attach") && h.includes("tp-btn-link")), "and a way to the card that takes it");
		assert.ok(!review.some((h) => h.includes('class="tp-gap"')), "nothing on Review reads as missing");
		assert.ok(review.some((h) => /Files<\/span><b>1<\/b>/.test(h)), "the Files tile counts the trip's files");
		const files = drawn(() => p.step_files($stub("files")));
		assert.ok(files.some((h) => h.includes("1 file not attached yet")), "the Files step lists it, counted as files");
		assert.ok(!files.some((h) => h.includes("Still missing")), "...and not as missing");
		assert.ok(files.some((h) => h.includes('class="tp-paper"') && h.includes("Southwest WN 1422")));
		const wording = { accommodations: "no confirmation attached.", ground_transport: "no rental agreement attached.", freight: "no bill of lading attached." };
		Object.entries(wording).forEach(([table, words]) => {
			assert.equal(p.gap_text({ ...PAPER, table, employee_names: [] }), `Southwest WN 1422: ${words}`);
		});

		p.state.gaps = [pair, room];
		const count = drawn(() => p.step_review($stub("review"))).find((h) => h.includes("tp-paper-count"));
		assert.ok(count.includes("3 files not attached yet"), `each person's boarding pass, and the room's confirmation: ${count}`);

		// A booking's card: paperwork alone leaves it unframed, with a quiet note; a real gap
		// frames it red.
		const card = { group: PAPER.group, table: "flights", values: {}, members: [] };
		p.state.gaps = [PAPER];
		let shell = drawn(() => p.card_shell($stub("there"), card, "Flight"));
		assert.ok(shell.some((h) => h.includes('class="tp-card "')), "paperwork alone does not make the card red");
		const notes = drawn(() => p.card_gaps($stub("there"), card));
		assert.ok(notes.some((h) => h.includes('class="tp-paper"') && h.includes("no boarding pass or ticket for Ana")));
		assert.ok(!notes.some((h) => h.includes('class="tp-gap"')));
		p.state.gaps = [PAPER, { ...NO_NUMBER, group: PAPER.group }];
		shell = drawn(() => p.card_shell($stub("there"), card, "Flight"));
		assert.ok(shell.some((h) => h.includes("tp-card tp-bad")), "a real gap still does");

		p.state.gaps = [];
		const clear = drawn(() => p.step_review($stub("review")));
		assert.ok(clear.some((h) => h.includes("Every booking that's made has its paperwork attached.")));
		assert.ok(!clear.some((h) => h.includes("not attached yet")));
	});

	await test("a step's badge and Mark as booked count every gap but paperwork", async () => {
		tripServer({ "TRIP-4": { ...DOC_TRIP, gaps: [PAPER] } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-4&step=review");
		await settle();
		const p = planner();
		const badges = () =>
			drawn(() => p.render_tabs())
				.filter((h) => h.includes('class="tp-tab'))
				.map((h) => [h.replace(/<span class="tp-badge">.*<\/span>/, "").replace(/<[^>]+>/g, ""), (h.match(/tp-badge">(\d+)</) || [])[1]])
				.filter(([, count]) => count);
		assert.deepEqual(p.gaps_for_step("there"), [], "paperwork is on Getting there, and not in its badge");
		assert.deepEqual(badges(), [], "no tab carries a badge for paperwork alone");
		p.state.gaps = [NO_NUMBER, PAPER];
		assert.deepEqual(p.gaps_for_step("there"), [NO_NUMBER]);
		assert.deepEqual(badges(), [["Getting there", "1"]], "the missing number is counted, the paperwork beside it is not");

		// Mark as booked: a trip whose only open items are files is booked without a question...
		const asked = [];
		F.confirm = (message, yes) => {
			asked.push(message);
			yes();
		};
		p.state.gaps = [PAPER];
		p.mark_booked();
		await settle();
		assert.deepEqual(asked, [], "paperwork alone asks nothing");
		assert.equal(JSON.parse(server.sent("save_plan")[0].args.plan).status, "Booked");
		// ...and one with a real gap is asked about that gap alone.
		p.state.status = "Planning";
		p.state.gaps = [NO_NUMBER, PAPER, { ...PAPER, group: "abcdefabcdef" }];
		p.mark_booked();
		await settle();
		assert.deepEqual(asked, ["1 things on the checklist are still missing. Mark the trip as booked anyway?"]);
	});

	await test("the Overview counts paperwork apart: its own muted tile and notes, never a red flag", async () => {
		tripServer({ "TRIP-1": TRIP });
		const views = server.handlers.get_trip_views;
		let gaps = [];
		server.handlers.get_trip_views = (args) => {
			const data = views(args);
			data.days = ["2026-10-01", "2026-10-02", "2026-10-03"];
			data.whole = [
				{
					date: "2026-10-01",
					items: [
						{ type: "flight", date: "2026-10-01", group: PAPER.group, airline: "Southwest", flight_number: "WN 1422", members: [] },
						{ type: "flight", date: "2026-10-01", group: NO_NUMBER.group, airline: "Delta", flight_number: "DL 88", members: [] },
					],
				},
			];
			data.gaps = gaps;
			return data;
		};
		gaps = [NO_NUMBER, PAPER, { ...PAPER, group: "abcdefabcdef", label: "Hilton", table: "accommodations", employee_names: [] }];
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review&view=overview");
		await settle();
		const tiles = ui.drawn.find((html) => html.includes('class="tp-sum"'));
		const tile = (label) => (tiles.match(new RegExp(`${label}</span><b>([^<]*)</b>`)) || [])[1];
		assert.equal(tile("Still missing"), "1", "the missing number only");
		assert.equal(tile("Files not attached"), "2", "the paperwork, counted as files");
		assert.ok(tiles.includes('<div class="tp-quiet"><span class="tp-muted">Files not attached'), "a muted tile");
		const southwest = ui.drawn.find((h) => h.includes("tp-tl-item") && h.includes("WN 1422"));
		const delta = ui.drawn.find((h) => h.includes("tp-tl-item") && h.includes("DL 88"));
		assert.ok(southwest && !southwest.includes("tp-bad"), "paperwork alone does not frame the booking red");
		assert.ok(delta.includes("tp-bad"), "a missing number does");
		const flags = ui.drawn.filter((h) => h.includes('class="tp-flag"'));
		const notes = ui.drawn.filter((h) => h.includes('class="tp-paper"'));
		assert.deepEqual(flags.length, 1, flags.join("\n"));
		assert.ok(flags[0].includes("No confirmation number for Ana."));
		assert.ok(notes.some((h) => h.includes("No boarding pass or ticket for Ana.")), "a muted note on the booking");
		assert.ok(notes.some((h) => h.includes("Hilton")), "the room on no day: a note under 'Not on any day yet'");

		// No paperwork, no tile.
		gaps = [NO_NUMBER];
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review&view=overview");
		await settle();
		const plain = ui.drawn.find((html) => html.includes('class="tp-sum"'));
		assert.ok(!plain.includes("Files not attached"));
	});

	await test("a booking taken off takes its files off the list; so does someone taken off the crew, for their own", async () => {
		const flight = {
			group: "aaaaaaaaaaaa",
			values: { leg: "Outbound", airline: "Southwest", flight_number: "WN 1", departure_time: "", arrival_time: "" },
			members: [
				{ name: "R1", traveler: "E1", ref: "" },
				{ name: "R2", traveler: "E2", ref: "" },
			],
		};
		const doc = (name, booking_group, traveler) => ({ ...SITE_MAP, name, booking_group, traveler });
		const db = tripServer({
			"TRIP-2": {
				...CREW_TRIP,
				bookings: { flights: [flight], accommodations: [], ground_transport: [] },
				// Ben receives this shipment and paid for it: taking him off the crew must not
				// leave the save refused for a receiver who is not on the trip.
				freight: [
					{
						name: "S1",
						carrier: "ODFL",
						tracking_number: "PRO-1",
						traveler: "E2",
						paid_by: "Employee",
						paid_by_traveler: "E2",
						cost: 0,
						booking_group: "cccccccccccc",
					},
				],
				documents: [
					doc("TDOC-1", "aaaaaaaaaaaa", ""),
					doc("TDOC-2", "", "E2"),
					// On a booking deleted on the form: the server would refuse every save that
					// still named it, so the page lists it with the trip's own files.
					doc("TDOC-3", "bbbbbbbbbbbb", "E1"),
				],
			},
		});
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=there");
		await settle();
		const p = planner();
		assert.deepEqual(
			p.state.documents.map((d) => [d.name, d.booking_group, d.traveler]),
			[
				["TDOC-1", "aaaaaaaaaaaa", ""],
				["TDOC-2", "", "E2"],
				["TDOC-3", "", "E1"],
			]
		);
		assert.equal(p.is_dirty(), false, "a file moved to the trip's own list is not an edit by itself");
		p.remove_traveler("E2");
		assert.deepEqual(p.state.documents.map((d) => d.name), ["TDOC-1", "TDOC-3"]);
		assert.deepEqual(
			p.state.freight.map((f) => [f.traveler, f.paid_by, f.paid_by_traveler]),
			[["", "Company", ""]],
			"their shipment goes to the whole crew, paid by the company"
		);
		p.card_shell($stub("there"), p.cards("flights")[0], "Flight"); // its Remove link
		press("Remove");
		assert.deepEqual(p.cards("flights"), []);
		assert.deepEqual(p.state.documents.map((d) => d.name), ["TDOC-3"]);
		p.go(3);
		await settle();
		assert.deepEqual(ui.msgprints, [], "the save was refused");
		assert.deepEqual(
			db["TRIP-2"].documents.map((d) => [d.name, d.booking_group, d.traveler]),
			[["TDOC-3", "", "E1"]]
		);
	});

	await test("the views show each booking's files, a room's guest, and never a receipt", async () => {
		tripServer({ "TRIP-2": CREW_TRIP });
		const views = server.handlers.get_trip_views;
		// shape_itinerary's file: `group` is its booking's key (null for the whole trip's), and
		// `for_employee` / `for_name` who it is for (null for everyone).
		const file = (name, title, kind, url, for_employee, for_name, group, booking_label) => ({
			name,
			title,
			kind,
			url,
			file_name: url.split("/").pop(),
			is_image: 0,
			for_name,
			for_employee,
			group,
			booking_label,
		});
		const pass = file("TDOC-1", "Ana boarding pass", "Boarding pass", "/private/files/ana-bp.pdf", "E1", "Ana", "g1", "Southwest WN 1422");
		const hotel = file("TDOC-2", "Hotel confirmation", "Booking confirmation", "/private/files/hotel.pdf", null, null, "g2", "Hilton");
		const map = file("TDOC-3", "Site map", "Site map", "/private/files/site.pdf", null, null, null, null);
		const ben = file("TDOC-4", "Ben's badge", "Other", "/private/files/ben.pdf", "E2", "Ben", null, null);
		// A booking whose label is blank (no airline or flight number yet) is still a booking:
		// its file is not one of the whole trip's.
		const unnamed = file("TDOC-5", "Unnamed flight pass", "Boarding pass", "/private/files/unnamed.pdf", null, null, "g9", null);
		const flight = {
			type: "flight",
			date: "2026-10-01",
			group: "g1",
			airline: "Southwest",
			flight_number: "WN 1422",
			members: [{ employee: "E1", employee_name: "Ana", ref: "ABC123" }],
			documents: [pass],
		};
		server.handlers.get_trip_views = (args) => {
			const data = views(args);
			data.days = ["2026-10-01", "2026-10-02", "2026-10-03"];
			data.whole = [
				{
					date: "2026-10-01",
					items: [
						flight,
						{
							type: "hotel_checkin",
							date: "2026-10-01",
							group: "g2",
							hotel: "Hilton",
							members: [
								{ employee: "E1", employee_name: "Ana", ref: "H1", guest: false },
								{ employee: "E2", employee_name: "Ben", ref: "H1", guest: true, check_in_date: "2026-10-02", check_out_date: "2026-10-03" },
							],
							documents: [hotel],
						},
					],
				},
			];
			// Even if a receipt ever reached an item, View as must not draw it: it is money.
			data.people.E1 = [{ date: "2026-10-01", items: [{ ...flight, attachment: "/private/files/receipt.pdf" }] }];
			data.documents = [map, ben, pass, hotel, unnamed];
			// What each person's /itinerary lists (shape_itinerary per person): Ana has the
			// trip's map and her own pass, never Ben's badge.
			data.people_documents = { E1: [map, pass], E2: [map, ben, hotel] };
			return data;
		};
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=review&view=overview");
		await settle();
		let html = ui.drawn.join("\n");
		assert.ok(html.includes('href="/private/files/ana-bp.pdf"'), "a booking's file links to it");
		assert.ok(html.includes("Ana boarding pass · for Ana"));
		assert.ok(html.includes("Hotel confirmation · everyone"));
		assert.ok(html.includes("Ben (guest"), "the guest is marked on the room");
		assert.ok(html.includes("Files for the whole trip") && html.includes("Site map · everyone"));
		assert.ok(html.includes("Ben's badge · for Ben"), "the Overview lists every file for the whole trip");
		assert.ok(!html.includes("Unnamed flight pass"), "a file on a booking with a blank label is not the whole trip's");

		planner().open_view("person", "E1");
		await settle();
		html = ui.drawn.join("\n");
		assert.ok(html.includes('href="/private/files/ana-bp.pdf"'), "their boarding pass");
		assert.ok(html.includes("Site map"), "the trip's file for everyone");
		assert.ok(!html.includes("Ben's badge"), "not a trip file for someone else");
		assert.ok(!html.includes("receipt.pdf"), "a receipt is money: never drawn");
		assert.ok(html.includes("&as=E1&view=docs"), "a link to their phone's Documents screen");

		planner().open_view("compare");
		await settle();
		assert.ok(ui.drawn.join("\n").includes("&#128196; 1"), "Side by side counts a booking's files");
	});

	// ---- PR 3 (Nik, 2026-09-26): the Map, who to call, the trip sheet, and bookings of one name

	await test("the Map is a view: one entry to open it; Back, Forward, a reload and the form's button come back to it", async () => {
		tripServer({ "TRIP-5": MAP_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		const p = planner();
		const before = browser.hist.length;
		p.open_view("map");
		await settle();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-5&step=review&view=map");
		assert.equal(browser.hist.length, before + 1, browser.hist.urls().join(" | "));
		assert.equal(last().view, "map");
		assert.equal(last().step, "review");
		await back();
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-5&step=review");
		assert.equal(last().view, "", "Back puts the step back");
		await forward();
		assert.equal(last().view, "map", "Forward restores the map");
		assert.equal(browser.hist.length, before + 1, "Back/Forward add no entries");
		p.open_view("overview"); // from the map to another view: its own entry
		await settle();
		assert.equal(browser.hist.length, before + 2);
		await back();
		assert.equal(last().view, "map");
		assert.equal(server.sent("get_trip_views").length, 1, "the map draws from the views' one answer");

		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=lodging&view=map");
		await settle();
		assert.equal(last().view, "map", "a reload of the map's address is the map");
		assert.equal(last().step, "lodging");
		assert.equal(browser.hist.length, 1, "a reload adds no entry");

		boot("plan-a-trip", "/desk/travel-trip/TRIP-5");
		await settle();
		F.set_route("plan-a-trip", { trip: "TRIP-5", view: "map" }); // the form's Trip views > Map
		await settle();
		assert.equal(last().view, "map");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-5&step=trip&view=map", "frappe's bare entry is named");
		assert.equal(browser.hist.length, 2, "named in place, not pushed");
	});

	await test("without Google Maps the Map still lists every place by day, numbered, each with its Google Maps link", async () => {
		tripServer({ "TRIP-5": MAP_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review&view=map");
		await settle();
		assert.equal(last().view, "map");
		let html = ui.drawn.join("\n");
		assert.ok(html.includes("Add the Google Maps key in Travel Settings to see the map."), "no key: it says so");
		assert.ok(!ui.drawn.some((h) => h.includes('<div class="tp-map">')), "and draws no empty map");
		// Numbered in the order the trip reaches them (first day, then first time; no day last),
		// and a place on three days is listed on each of them with its one number.
		assert.deepEqual(map_rows(ui.drawn), [
			["1", "PHX airport"],
			["2", "Harborview Suites"],
			["2", "Harborview Suites"],
			["3", "Harbor Fountain job site"],
			["4", "Enterprise pick-up"],
			["1", "PHX airport"],
			["5", "ODFL delivery"],
		]);
		assert.ok(html.includes("Day 1 · 2026-10-01") && html.includes("Day 3 · 2026-10-03"), "each day, by its number");
		assert.ok(html.includes("No date yet"), "a place with no day yet is still listed");
		const links = map_links(ui.drawn);
		assert.equal(links.length, 7);
		links.forEach((href) => assert.ok(href.startsWith("https://www.google.com/maps/search/?api=1&query="), href));
		assert.ok(links.includes("https://www.google.com/maps/search/?api=1&query=32.71%2C-117.16"), "a place with a point opens at it");
		assert.ok(links.includes("https://www.google.com/maps/search/?api=1&query=PHX%20airport"), "one without, by its words");
		assert.ok(!html.includes("Booked so far") && !html.includes("Cost"), "nothing about money");

		// A day chip is a choice on this screen: no entry, no fetch, the list shows that day.
		const entries = browser.hist.length;
		const chips = drawn(() => press('data-day="2026-10-02"'));
		assert.deepEqual(map_rows(chips), [
			["2", "Harborview Suites"],
			["3", "Harbor Fountain job site"],
			["4", "Enterprise pick-up"],
		]);
		assert.ok(chips.some((h) => h.includes('tp-map-day tp-active" data-day="2026-10-02"')), "the chip shows it is on");
		assert.ok(!chips.some((h) => h.includes("No date yet")), "one day's list is that day's");
		await settle();
		assert.equal(browser.hist.length, entries, "a day chip makes no history entry");
		assert.equal(url(), "/desk/plan-a-trip?trip=TRIP-5&step=review&view=map");
		assert.equal(server.sent("get_trip_views").length, 1);
		planner().render(); // redrawn, for this trip: the same day
		assert.deepEqual(map_rows(ui.drawn).map(([n]) => n), ["2", "3", "4"]);
		drawn(() => press('data-day=""'));

		// A key, and no loader (the desk bundle did not load): still the list, and why.
		tripServer({ "TRIP-5": { ...MAP_TRIP, maps: { api_key: "browser-key", map_id_light: "", map_id_dark: "" } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review&view=map");
		await settle();
		html = ui.drawn.join("\n");
		assert.ok(html.includes("The map could not be loaded right now. The places are listed below."));
		assert.equal(map_rows(ui.drawn).length, 7);

		// Nothing with a place yet.
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review&view=map");
		await settle();
		assert.ok(ui.drawn.join("\n").includes("Nothing on this trip has a place yet."));
	});

	await test("with Google Maps: each place looked up once and remembered, numbered markers, roads, flights, and the day chips", async () => {
		tripServer({ "TRIP-5": { ...MAP_TRIP, maps: { api_key: "browser-key", map_id_light: "", map_id_dark: "" } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		const store = fakeStorage();
		const g = fakeGoogleMaps();
		const p = planner();
		p.open_view("map");
		await settle();
		assert.deepEqual(g.loads, [{ apiKey: "browser-key", libraries: ["marker", "geocoding", "routes"] }]);
		assert.ok(ui.drawn.some((h) => h.includes('<div class="tp-map">')), "the map's box");
		// The job site has a point; the airport, the hotel and the delivery are looked up by
		// their words, each once.
		assert.deepEqual(g.geocoded.sort(), [
			"1 Harbor Way, San Diego, CA",
			"123 Dock St, San Diego",
			"Enterprise Rent-A-Car, 5th Ave, San Diego",
			"PHX airport",
		]);
		assert.deepEqual(g.markers.map((m) => m.label.text).sort(), ["1", "2", "3", "4", "5"], "one numbered marker a place");
		assert.ok(g.markers.every((m) => m.on === g.map), "every day: every place");
		assert.ok(g.markers.find((m) => m.label.text === "1").title.startsWith("1. PHX airport"));
		assert.equal(g.markers.find((m) => m.label.text === "3").position.lat, 32.71, "a place with a point is put at it");
		// The drive from the airport to the hotel follows the road; the flight is dashed, along the
		// curve of the earth, and asks Google for no road.
		assert.equal(g.routed.length, 1, "one road asked for: the drive");
		const road = g.lines.find((l) => !l.icons);
		assert.ok(road && road.path.length === 3, "the drive is drawn along the road Google gave");
		const flight = g.lines.find((l) => l.icons);
		assert.ok(flight && flight.geodesic === true, "the flight is a dashed great-circle line");
		assert.ok(road.on && flight.on);
		// A marker's popup is built from elements: the place, the day, who, and its Maps link.
		const popup = g.popup(g.markers.find((m) => m.label.text === "2"));
		assert.ok(popup.text.includes("2. Harborview Suites") && popup.text.includes("Ana"), popup.text);
		assert.ok(popup.links.length === 1 && popup.links[0].startsWith("https://www.google.com/maps/"), popup.links.join(" "));

		// A day chip shows that day's places and lines, and nothing else; no history.
		const entries = browser.hist.length;
		drawn(() => press('data-day="2026-10-03"'));
		assert.deepEqual(
			g.markers.filter((m) => m.on).map((m) => m.label.text),
			["1"],
			"the airport, on the last day"
		);
		assert.ok(flight.on && !road.on, "the last day's flight, not the first day's drive");
		assert.equal(browser.hist.length, entries);
		drawn(() => press('data-day=""'));
		assert.equal(g.markers.filter((m) => m.on).length, 5, "All days: every place again");

		// Drawn again (another view and back): nothing is looked up or routed again, and the
		// browser kept the points it looked up, for 30 days.
		p.open_view("overview");
		await settle();
		p.open_view("map");
		await settle();
		assert.equal(g.geocoded.length, 4, "looked up again");
		assert.equal(g.routed.length, 1, "routed again");
		const kept = JSON.parse(store.getItem("tp_map_geocode_v1"));
		assert.deepEqual(Object.keys(kept).sort(), [
			"1 harbor way, san diego, ca",
			"123 dock st, san diego",
			"enterprise rent-a-car, 5th ave, san diego",
			"phx airport",
		]);
		// A new page load reads them back instead of asking Google.
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		window.localStorage = store;
		const again = fakeGoogleMaps();
		planner().open_view("map");
		await settle();
		assert.deepEqual(again.geocoded, [], "the browser's copy was used");
		assert.equal(again.markers.length, 5);
	});

	await test("the Map drops Google's late answers, works with storage blocked, and falls back to a straight line", async () => {
		tripServer({ "TRIP-5": { ...MAP_TRIP, maps: { api_key: "browser-key", map_id_light: "", map_id_dark: "" } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		// Storage that throws on every touch (a private window, blocked site data).
		Object.defineProperty(window, "localStorage", {
			configurable: true,
			get() {
				throw new Error("SecurityError");
			},
		});
		const g = fakeGoogleMaps({ road: "REQUEST_DENIED", hold: true });
		const p = planner();
		p.open_view("map");
		await settle();
		assert.equal(g.markers.length, 1, "the place with a point is up; the others are being looked up");
		await back(); // before Google answered
		assert.equal(last().view, "");
		g.release();
		await settle();
		assert.equal(g.markers.length, 1, "a map no longer on screen drew nothing more");
		assert.ok(g.markers.every((m) => !m.on));
		await forward();
		assert.equal(last().view, "map");
		assert.deepEqual(g.geocoded.length, 4, "what was looked up meanwhile is kept, storage or not");
		assert.equal(g.markers.filter((m) => m.on).length, 5, "the map on screen drew every place");
		const drive = g.lines.find((l) => !l.geodesic);
		assert.ok(drive && drive.icons, "no road from Google: a straight dashed line, never a solid road");
		assert.equal(drive.path.length, 2);
		assert.equal(g.routed.length, 1, "one drive, asked once");
		assert.deepEqual(ui.msgprints, []);
	});

	await test("a day chip tapped while the places are still being looked up is the day the map shows", async () => {
		// Every trip's first open: nothing is in this browser's lookups yet. The draw that
		// finished after the tap used to put back the day it started with (every day), so the
		// chip and the list said Oct 3 while the map showed the whole trip.
		tripServer({ "TRIP-5": { ...MAP_TRIP, maps: { api_key: "browser-key", map_id_light: "", map_id_dark: "" } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		fakeStorage();
		const g = fakeGoogleMaps({ hold: true });
		planner().open_view("map");
		await settle();
		assert.equal(g.markers.length, 1, "the place with a point is up; the others are being looked up");
		const chips = drawn(() => press('data-day="2026-10-03"'));
		assert.ok(chips.some((h) => h.includes('tp-map-day tp-active" data-day="2026-10-03"')));
		g.release();
		await settle();
		assert.equal(g.markers.length, 5, "every place is on the map");
		assert.deepEqual(
			g.markers.filter((m) => m.on).map((m) => m.label.text),
			["1"],
			"only Oct 3's place: the airport"
		);
		const flight = g.lines.find((l) => l.icons);
		assert.ok(flight && flight.on, "Oct 3's flight");
		assert.ok(g.lines.filter((l) => !l.icons).every((l) => !l.on), "not Oct 1's drive");
	});

	await test("the Map numbers its days from the trip's first day, as the Trip Sheet does", async () => {
		// A flight the evening before the trip starts: the views show that day, and counting
		// the days they show made every chip one ahead of the printed sheet.
		const early = {
			key: "airport:SAN",
			kind: "airport",
			label: "SAN airport",
			query: "SAN airport",
			lat: 32.73,
			lng: -117.19,
			days: ["2026-09-30"],
			first_time: "18:00",
			who: ["Ana"],
			group: "fl0",
		};
		tripServer({ "TRIP-5": { ...MAP_TRIP, days: ["2026-09-30", ...MAP_TRIP.days], places: [...MAP_TRIP.places, early] } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review&view=map");
		await settle();
		const html = ui.drawn.join("\n");
		assert.ok(html.includes('data-day="2026-09-30">2026-09-30</button>'), "the evening before has no number");
		assert.ok(html.includes('data-day="2026-10-01">Day 1 · 2026-10-01</button>'), "the trip's first day is Day 1");
		assert.ok(html.includes('data-day="2026-10-03">Day 3 · 2026-10-03</button>'));
		assert.ok(html.includes('<div class="tp-map-day-head">2026-09-30</div>'), "the list says the same");
		assert.ok(html.includes('<div class="tp-map-day-head">Day 2 · 2026-10-02</div>'));
	});

	await test("the Map is drawn again for the desk's theme when it flips, and only while it is on screen", async () => {
		// A Map ID and a colorScheme are fixed when a Google map is made; setOptions restyles
		// neither. The desk flips data-theme in place (its toggle, or Automatic following the
		// device), so the map is made again, as the location timeline and the kiosk map are.
		tripServer({ "TRIP-5": { ...MAP_TRIP, maps: { api_key: "browser-key", map_id_light: "light-id", map_id_dark: "dark-id" } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		const theme = { value: "light" };
		globalThis.document.documentElement = { getAttribute: (name) => (name === "data-theme" ? theme.value : null) };
		const observers = [];
		globalThis.MutationObserver = class {
			constructor(callback) {
				this.callback = callback;
				this.on = false;
				observers.push(this);
			}
			observe(target, options) {
				this.on = true;
				this.target = target;
				this.options = options;
			}
			disconnect() {
				this.on = false;
			}
		};
		const flip = (value) => {
			theme.value = value;
			observers.filter((o) => o.on).forEach((o) => o.callback([{ type: "attributes", attributeName: "data-theme" }]));
		};
		const watching = () => observers.filter((o) => o.on).length;
		try {
			fakeStorage();
			const g = fakeGoogleMaps();
			const p = planner();
			p.open_view("map");
			await settle();
			assert.deepEqual(g.themes, ["light"]);
			assert.equal(watching(), 1, "one watch");
			assert.equal(observers[0].target, globalThis.document.documentElement);
			assert.deepEqual(observers[0].options, { attributes: true, attributeFilter: ["data-theme"] });
			drawn(() => press('data-day="2026-10-02"'));
			const entries = browser.hist.length;
			flip("dark");
			await settle();
			assert.deepEqual(g.themes, ["light", "dark"], "a new map, made for the dark theme");
			assert.equal(watching(), 1, "still one watch: the first stopped when the map was drawn again");
			assert.ok(ui.drawn.some((h) => h.includes('tp-map-day tp-active" data-day="2026-10-02"')), "the day chosen stays");
			assert.deepEqual(
				g.markers.slice(-5).filter((m) => m.on).map((m) => m.label.text).sort(),
				["2", "3", "4"],
				"the new map shows that day"
			);
			assert.equal(browser.hist.length, entries, "no history entry");
			assert.equal(server.sent("get_trip_views").length, 1, "nothing fetched again");
			flip("dark"); // written again with the same value: nothing to redraw
			await settle();
			assert.equal(g.themes.length, 2);
			// Off the map, a flip draws nothing, and the watch ends.
			p.open_view("overview");
			await settle();
			flip("light");
			await settle();
			assert.equal(g.themes.length, 2, "no map is made for a view that is not the map");
			assert.equal(watching(), 0);
			assert.equal(last().view, "overview");
		} finally {
			delete globalThis.MutationObserver;
		}
	});

	await test("a get_trip_views answer for the Map that lands after the page moved on is dropped", async () => {
		tripServer({ "TRIP-5": MAP_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		const p = planner();
		server.hold.add("get_trip_views");
		p.open_view("map");
		await settle();
		assert.equal(last().view, "map");
		assert.equal(last().loading, true);
		await back();
		const renders = ui.renders.length;
		server.release("get_trip_views");
		await settle();
		assert.equal(ui.renders.length, renders, "the stale answer drew over the step");
		assert.equal(p.views, null, "the stale answer was kept");
		server.hold.delete("get_trip_views");
		await forward();
		assert.equal(last().view, "map");
		assert.equal(map_rows(ui.drawn).length, 7, "the map on screen asked again and drew");
	});

	await test("who to call: 911, the office, who booked it, the lead, the site and the hotels, phones as digits-only tel: links", async () => {
		tripServer({ "TRIP-5": MAP_TRIP });
		const views = server.handlers.get_trip_views;
		server.handlers.get_trip_views = (args) => {
			const data = views(args);
			// Each person's itinerary: Ana sleeps by the harbor, Ben at the Hilton.
			const stay = (hotel, group) => [{ date: "2026-10-01", items: [{ type: "hotel_checkin", date: "2026-10-01", hotel, group }] }];
			data.people = { E1: stay("Harborview Suites", "h1"), E2: stay("Hilton", "h2") };
			return data;
		};
		const contact_cards = (html) => html.filter((h) => h.startsWith('<div class="tp-contact '));
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review&view=overview");
		await settle();
		const cards = contact_cards(ui.drawn);
		const html = cards.join("\n");
		assert.ok(ui.drawn.some((h) => h.includes("<h5>Who to call</h5>")));
		assert.ok(cards[0].includes('class="tp-contact tp-sos"') && cards[0].includes('href="tel:911"'), "911 first");
		assert.ok(html.includes('href="tel:8015550100"'), "the travel desk, its extension left off the link");
		assert.ok(html.includes("(801) 555-0100 ext. 2"), "...and shown as stored");
		assert.ok(html.includes('href="tel:+18015550111"') && html.includes("Olivia Office"), "who booked it");
		assert.ok(html.includes('href="tel:8015550122"') && html.includes("Trip lead"), "the lead's work mobile");
		assert.ok(html.includes('href="mailto:sam@example.test"') && html.includes("Sam Site"), "the site's contact");
		assert.ok(html.includes("https://www.google.com/maps/dir/?api=1&destination=1%20Harbor%20Way%2C%20San%20Diego%2C%20CA"));
		assert.ok(html.includes("Nearest urgent care") && html.includes("urgent%20care%20near%2010%20Bay%20St"));
		assert.ok(html.includes("Hilton"), "the Overview lists every hotel");
		const hrefs = [...html.matchAll(/href="([^"]*)"/g)].map((m) => m[1]);
		hrefs.forEach((href) => assert.ok(/^(tel:\+?\d+|mailto:[^?&]+|https:\/\/)/.test(href), href));
		assert.ok(!html.includes("javascript:") && !html.includes("http://example.test"), "a server link that is not https is text at most");
		assert.ok(!html.includes('href="tel:"'), "a hotel with no phone has no empty link");

		// View as: the hotels that person sleeps in, not everyone's.
		planner().open_view("person", "E2");
		await settle();
		const ben = contact_cards(ui.drawn);
		assert.ok(ben.some((h) => h.includes("Hilton")), "Ben's hotel");
		assert.ok(!ben.some((h) => h.includes("Harborview Suites")), "not Ana's");
		assert.ok(ben.some((h) => h.includes('href="tel:911"')));

		// The server's list for each person (people_hotels, /itinerary's rule) is what View as
		// shows: a room with no dates yet is on no day of their itinerary, but it is on the card
		// their phone shows. Ana has no check-in at all here.
		tripServer({ "TRIP-5": { ...MAP_TRIP, people_hotels: { E1: ["Harborview Suites", "Hilton"], E2: [] } } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review&view=person&as=E1");
		await settle();
		const ana = contact_cards(ui.drawn);
		assert.ok(ana.some((h) => h.includes("Harborview Suites")) && ana.some((h) => h.includes("Hilton")), "both of Ana's rooms");
		planner().open_view("person", "E2");
		await settle();
		assert.ok(!contact_cards(ui.drawn).some((h) => h.includes("<b>Hilton</b>") || h.includes("Harborview")), "none for Ben");

		// An answer with no contacts draws no panel.
		tripServer({ "TRIP-1": TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-1&step=review&view=overview");
		await settle();
		assert.ok(!ui.drawn.some((h) => h.includes("Who to call")));
	});

	await test("the trip sheet opens in a new tab from Review, the Overview and View as, and makes no history entry", async () => {
		tripServer({ "TRIP-5": MAP_TRIP });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		const p = planner();
		const sheet = "/api/method/frappe.utils.print_format.download_pdf?doctype=Travel%20Trip&name=TRIP-5&format=Trip%20Sheet&no_letterhead=1&pdf_generator=chrome";
		const review = drawn(() => p.step_review($stub("review"))).find((h) => h.includes("Print the trip sheet"));
		assert.ok(review, "Review offers the sheet");
		assert.ok(review.includes(`href="${sheet}"`) && review.includes('target="_blank"'), review);
		const entries = browser.hist.length;
		p.open_view("overview");
		await settle();
		const overview = ui.drawn.find((h) => h.includes("Print the trip sheet"));
		assert.ok(overview.includes(`href="${sheet}"`) && overview.includes('target="_blank"'));
		p.open_view("person", "E2");
		await settle();
		const ben = ui.drawn.find((h) => h.includes("Print Ben's sheet"));
		assert.ok(ben && ben.includes(`href="${sheet}&as=E2"`), "one person's sheet, by `as`");
		assert.equal(browser.hist.length, entries + 2, "the two views only");

		// A server address that is not this site's own is never a link, and the page spells none
		// in its place: a view draws only the server's address. (Changed on purpose, 2026-09-27:
		// the page used to spell its own, which printed Standard, costs included, whenever the
		// server had sent no address because the format was missing.)
		tripServer({
			// "/\host" is "//host" to a browser: another site, though it starts with one slash.
			"TRIP-5": { ...MAP_TRIP, sheet_url: "javascript:alert(1)", people_sheet_urls: { E1: "//evil.example/a.pdf", E2: "/\\evil.example/sheet.pdf" } },
		});
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review&view=overview");
		await settle();
		assert.ok(!ui.drawn.some((h) => h.includes("Print the trip sheet")), "no link, and none spelled by the page");
		assert.ok(!ui.drawn.join("\n").includes("javascript:"));
		planner().open_view("person", "E2");
		await settle();
		assert.ok(!ui.drawn.some((h) => h.includes("Print Ben's sheet")));
		assert.ok(!ui.drawn.join("\n").includes("evil.example"));

		// The site has no Trip Sheet format (its upsert failed and logged, or no migrate since the
		// app was installed): frappe would print Standard, every cost on it, so there is no link
		// anywhere. The views' answer sends no address, and get_plan says so for Review.
		tripServer({ "TRIP-5": { ...MAP_TRIP, sheet_available: false, sheet_url: null, people_sheet_urls: {} } });
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-5&step=review");
		await settle();
		assert.ok(!drawn(() => planner().step_review($stub("review"))).some((h) => h.includes("Print the trip sheet")), "Review");
		planner().open_view("overview");
		await settle();
		assert.ok(!ui.drawn.some((h) => h.includes("Print the trip sheet")), "the Overview");
		planner().open_view("person", "E2");
		await settle();
		assert.ok(ui.drawn.some((h) => h.includes("What Ben sees")), "View as is drawn");
		assert.ok(!ui.drawn.some((h) => h.includes("Print Ben's sheet")), "View as");
		assert.ok(!ui.drawn.join("\n").includes("download_pdf"));
		// The same for a file's link: "/\host" is not this site's.
		const chips = planner().doc_chips([
			{ title: "Odd", kind: "Other", url: "/\\evil.example/x.pdf" },
			{ title: "Map", kind: "Site map", url: "/private/files/site.pdf" },
		]);
		assert.ok(!chips.includes('href="/\\evil.example'), chips);
		assert.ok(chips.includes('href="/private/files/site.pdf"'));
	});

	await test("two bookings with the same name are told apart by who is on each, then by their dates; their keys never change", async () => {
		const room = (group, name, people, check_in, check_out) => ({
			group,
			values: { hotel_lodging: name, check_in_date: check_in, check_out_date: check_out },
			members: people.map((traveler, i) => ({ name: `${group}-${i}`, traveler, ref: "" })),
		});
		const paper = (group, label) => ({ check: "documents", step: "lodging", table: "accommodations", group, label, employee_names: [], kinds: [] });
		const rooms = [
			room("aaaaaaaaaaaa", "Harborview Suites", ["E1"], "2026-10-01", "2026-10-03"),
			room("bbbbbbbbbbbb", "Harborview Suites", ["E2"], "2026-10-01", "2026-10-03"),
			room("cccccccccccc", "Hilton", ["E1"], "2026-10-03", "2026-10-04"),
		];
		tripServer({
			"TRIP-2": {
				...CREW_TRIP,
				bookings: { flights: [], accommodations: rooms, ground_transport: [] },
				documents: [{ ...SITE_MAP, name: "TDOC-9", title: "Ana's confirmation", booking_group: "aaaaaaaaaaaa", booking_label: "Harborview Suites" }],
				gaps: [paper("bbbbbbbbbbbb", "Harborview Suites"), paper("cccccccccccc", "Hilton")],
			},
		});
		boot("plan-a-trip", "/desk/plan-a-trip?trip=TRIP-2&step=files");
		await settle();
		const p = planner();
		const files = drawn(() => p.step_files($stub("files"))).join("\n");
		assert.ok(files.includes("Harborview Suites · Ana</span>"), "the Files step names whose room it is");
		assert.ok(files.includes("Harborview Suites · Ben: no confirmation attached."), "so does the paperwork note");
		assert.ok(files.includes("Hilton: no confirmation attached."), "a name nothing shares is left as it is");
		assert.equal(p.gap_text(paper("bbbbbbbbbbbb", "Harborview Suites")), "Harborview Suites · Ben: no confirmation attached.");
		assert.equal(p.gap_text({ ...NO_NUMBER, group: "not-on-this-trip" }), "Delta DL 88: no confirmation number for Ana.");

		// One person's split stay: the same hotel, the same person, so the dates tell them apart.
		p.cards("accommodations")[1].members = [{ name: "bbbbbbbbbbbb-0", traveler: "E1", ref: "" }];
		p.cards("accommodations")[1].values.check_in_date = "2026-10-03";
		p.cards("accommodations")[1].values.check_out_date = "2026-10-04";
		const real_moment = globalThis.moment;
		globalThis.moment = (value) => ({ format: () => String(value || ""), isSameOrBefore: () => false, add() {} });
		try {
			const headings = p.booking_headings();
			assert.equal(headings.aaaaaaaaaaaa.heading, "Harborview Suites · Ana · 2026-10-01 – 2026-10-03");
			assert.equal(headings.bbbbbbbbbbbb.heading, "Harborview Suites · Ana · 2026-10-03 – 2026-10-04");
			assert.equal(headings.cccccccccccc.heading, "Hilton");
		} finally {
			globalThis.moment = real_moment;
		}
		// Only the words changed: every file keeps its booking's key, and what is saved says so.
		assert.deepEqual(p.state.documents.map((d) => [d.booking_group, d.booking_label]), [["aaaaaaaaaaaa", "Harborview Suites"]]);
		assert.deepEqual(p.payload().bookings.accommodations.map((c) => [c.group, c.label]), [
			["aaaaaaaaaaaa", "Harborview Suites"],
			["bbbbbbbbbbbb", "Harborview Suites"],
			["cccccccccccc", "Hilton"],
		]);
	});
}

// The Map view's fixture: a trip with places and legs as views.build_trip_views sends them, and
// who to call as api/travel._trip_contacts does. Ana has the hotel by the harbor, Ben the Hilton.
const MAP_TRIP = {
	...CREW_TRIP,
	trip: { ...CREW_TRIP.trip, purpose: "Harbor install" },
	days: ["2026-10-01", "2026-10-02", "2026-10-03"],
	places: [
		{
			key: "freight:1",
			kind: "freight",
			label: "ODFL delivery",
			query: "123 Dock St, San Diego",
			lat: null,
			lng: null,
			days: [],
			first_time: null,
			who: [],
			group: "f1",
		},
		// No time yet: after the timed places of its day, though it is listed first.
		{
			key: "pickup:1",
			kind: "pickup",
			label: "Enterprise pick-up",
			query: "Enterprise Rent-A-Car, 5th Ave, San Diego",
			lat: null,
			lng: null,
			days: ["2026-10-02"],
			first_time: null,
			who: ["Ben"],
			group: "g2",
		},
		{
			key: "stop:1",
			kind: "stop",
			label: "Harbor Fountain job site",
			query: "Harbor Fountain job site",
			lat: 32.71,
			lng: -117.16,
			days: ["2026-10-02"],
			first_time: "08:00",
			who: [],
			group: null,
		},
		{
			key: "hotel:1",
			kind: "hotel",
			label: "Harborview Suites",
			query: "1 Harbor Way, San Diego, CA",
			lat: null,
			lng: null,
			days: ["2026-10-02", "2026-10-01"],
			first_time: "15:00",
			who: ["Ana"],
			group: "h1",
		},
		{
			key: "airport:PHX",
			kind: "airport",
			label: "PHX airport",
			query: "PHX airport",
			lat: null,
			lng: null,
			days: ["2026-10-01", "2026-10-03"],
			first_time: "07:15",
			who: ["Ana", "Ben"],
			group: "fl1",
		},
	],
	legs: [
		{ day: "2026-10-01", from: "airport:PHX", to: "hotel:1", kind: "drive", who: ["Ana"] },
		{ day: "2026-10-03", from: "stop:1", to: "airport:PHX", kind: "flight", who: ["Ana", "Ben"] },
	],
	contacts: {
		emergency: "911",
		office: { label: "Sapphire travel desk", phone: "(801) 555-0100 ext. 2", email: "travel@sapphire.test" },
		booked_by: { name: "Olivia Office", phone: "+1 801-555-0111", email: "olivia@sapphire.test" },
		lead: { name: "Ana", phone: "801.555.0122" },
		site: {
			label: "Harbor Fountain",
			contact_name: "Sam Site",
			phone: "619-555-0133",
			email: "sam@example.test",
			address: "1 Harbor Way, San Diego, CA",
		},
		hotels: [
			{
				name: "Harborview Suites",
				phone: "619-555-0144",
				address: "10 Bay St, San Diego",
				urgent_care_url: "https://www.google.com/maps/search/?api=1&query=urgent%20care%20near%2010%20Bay%20St",
				directions_url: "https://www.google.com/maps/dir/?api=1&destination=10%20Bay%20St",
			},
			{ name: "Hilton", phone: "", address: "", urgent_care_url: "javascript:alert(1)", directions_url: "http://example.test/x" },
		],
	},
};

// The Map list's rows as [number, place], in the order drawn.
function map_rows(html) {
	return html
		.filter((h) => h.includes('class="tp-map-row"'))
		.map((h) => {
			const match = h.match(/tp-map-num"[^>]*>(\d+)<\/span>[\s\S]*?<b>\S+ ([^<]*)<\/b>/);
			return match ? [match[1], match[2]] : [h];
		});
}

function map_links(html) {
	return html
		.filter((h) => h.includes('class="tp-map-row"'))
		.map((h) => (h.match(/class="tp-map-open"[^>]*href="([^"]*)"/) || [])[1])
		.filter(Boolean);
}

// window.localStorage, as a browser keeps it: strings by key.
function fakeStorage() {
	const data = new Map();
	const store = {
		getItem: (key) => (data.has(key) ? data.get(key) : null),
		setItem: (key, value) => data.set(key, String(value)),
		removeItem: (key) => data.delete(key),
	};
	window.localStorage = store;
	return store;
}

// window.EEGoogleMaps and the google.maps it hands back: just enough of Map, Marker, Polyline,
// Geocoder and DirectionsService to see what the Map view asks Google for and draws. Answers
// arrive a moment later (the harness clock), as Google's do. `road`: DirectionsService's status;
// `hold`: the Geocoder's answers wait for log.release().
function fakeGoogleMaps(options) {
	options = options || {};
	const log = { loads: [], geocoded: [], routed: [], markers: [], lines: [], map: null, held: [], themes: [] };
	const point = (lat, lng) => ({ lat: () => lat, lng: () => lng });
	log.release = () => log.held.splice(0).forEach((answer) => answer());
	const maps = {
		Map: class {
			constructor() {
				log.map = this;
			}
			fitBounds() {}
			setCenter() {}
			setZoom() {}
		},
		InfoWindow: class {
			setContent(content) {
				this.content = content;
			}
			open() {
				log.opened = this.content;
			}
			close() {}
		},
		LatLngBounds: class {
			extend() {}
			getCenter() {
				return { lat: 0, lng: 0 };
			}
		},
		Marker: class {
			constructor(opts) {
				Object.assign(this, opts);
				this.on = null;
				this.handlers = {};
				log.markers.push(this);
			}
			setMap(map) {
				this.on = map;
			}
			addListener(event, fn) {
				this.handlers[event] = fn;
			}
		},
		Polyline: class {
			constructor(opts) {
				Object.assign(this, opts);
				this.on = null;
				log.lines.push(this);
			}
			setMap(map) {
				this.on = map;
			}
		},
		SymbolPath: { CIRCLE: 0 },
		TravelMode: { DRIVING: "DRIVING", WALKING: "WALKING" },
		Geocoder: class {
			geocode(request, callback) {
				log.geocoded.push(request.address);
				const found = point(33.4 + log.geocoded.length, -112);
				const answer = () => callback([{ geometry: { location: found } }], "OK");
				if (options.hold) log.held.push(answer);
				else fakeSetTimeout(answer, 1);
			}
		},
		DirectionsService: class {
			route(request, callback) {
				log.routed.push(request);
				const status = options.road || "OK";
				fakeSetTimeout(
					() =>
						callback(
							status === "OK" ? { routes: [{ overview_path: [point(1, 1), point(2, 2), point(3, 3)] }] } : null,
							status
						),
					1
				);
			}
		},
		importLibrary: () => Promise.resolve({}),
	};
	window.EEGoogleMaps = {
		load: (opts) => {
			log.loads.push(clone(opts));
			return Promise.resolve(maps);
		},
		// The theme each map was built for (a Map ID and a colorScheme are fixed at construction).
		mapOptions: (config, theme) => {
			log.themes.push(theme);
			return { styles: [] };
		},
	};
	// A popup's text and links (it is built from elements: a minimal document for it).
	log.popup = (marker) => {
		const made = [];
		globalThis.document.createElement = (tag) => {
			const el = { tag, style: {}, children: [], textContent: "", appendChild: (child) => el.children.push(child) };
			made.push(el);
			return el;
		};
		try {
			marker.handlers.click();
		} finally {
			delete globalThis.document.createElement;
		}
		const box = log.opened;
		return {
			text: box.children.map((c) => c.textContent).join("\n"),
			links: box.children.filter((c) => c.tag === "a").map((c) => c.href),
		};
	};
	return log;
}

// ------------------------------------------------------------------ main

const which = process.argv[2];
if (!which || which === "visit-wizard") await visitWizardSuite();
if (!which || which === "plan-a-trip") await planATripSuite();

console.log(`${checks - failures} passed, ${failures} failed`);
process.exit(failures ? 1 : 0);
