#!/usr/bin/env node
/**
 * Browser Back / Forward on the Time Kiosk (`/kiosk`): its bottom tabs and its bottom sheets.
 *
 * Loads the REAL `ui.js`, `myday.js`, `settings.js` and `app.js` from `public/js/kiosk/` into a
 * vm context over a small fake DOM and a fake session history, then taps, types nothing, and
 * presses Back and Forward. The history behaves the way a browser's does where it matters: a
 * traversal (the Back button, Forward, `history.back()`) is an asynchronous task that fires
 * `popstate` when it lands, `pushState` drops the forward entries, and Back from the page's first
 * entry leaves the page. Plain node, no runner and no npm install.
 *
 * What it pins, each one a way the kiosk used to fail or could quietly start failing:
 *
 *   - **the boot entry is stamped with replaceState, and nothing is pushed on load** — Back from
 *     the first entry still leaves the page, and Chrome skips entries a page pushes before
 *     anyone touched it;
 *   - **a tab tap is one entry**: Back walks the tabs, Forward restores them;
 *   - **each Back closes exactly one sheet**, through `close('dismiss')`, which every gate reads
 *     as cancel — so Back on "Review your day" or the photo gate posts nothing;
 *   - **no dead entries**: a sheet closed from the UI, or replaced by the next in a burst, leaves
 *     nothing behind, and the popstate of the page's own `back()` is never taken for the
 *     person's Back — not even when a sheet or a tab tap arrives while it is in flight;
 *   - **the clock state is never an entry**, and Back after a clock action does not undo it;
 *   - **"Report a problem" owns its own entry**: the kiosk does nothing while
 *     `ee_capture.isOpen()`, and re-stamps the panel's leftover entry (or one from an earlier
 *     load of the page) rather than interpreting it;
 *   - **no call passes a URL** — iOS Safari asks for camera and location again when it changes;
 *   - **every kiosk push spends a tap of its own** — Chrome's history intervention: a push made
 *     without a user activation (each push uses up the tap before it) marks every entry of the
 *     page skippable, and the next Back leaves the app. The fake history models it (see
 *     `makeHistory`) and `invariants()` counts every such push.
 *
 * Run: node scripts/test_kiosk_history.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const KIOSK = path.join(__dirname, "..", "erpnext_enhancements", "public", "js", "kiosk");
const SCRIPTS = ["ui.js", "myday.js", "settings.js", "app.js"];

let failures = 0;
let checks = 0;

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.error(`  FAIL ${label}: got ${a}, want ${e}`);
	}
}

function clone(v) {
	return v === undefined ? null : JSON.parse(JSON.stringify(v));
}

// ---------------------------------------------------------------------------- virtual clock

function makeClock() {
	const timers = new Map();
	let seq = 0;
	const clock = {
		now: 1_000_000,
		setTimeout(fn, ms) {
			const id = ++seq;
			timers.set(id, { id, at: clock.now + Math.max(0, Number(ms) || 0), fn, every: 0 });
			return id;
		},
		setInterval(fn, ms) {
			const id = ++seq;
			timers.set(id, { id, at: clock.now + Math.max(1, Number(ms) || 1), fn, every: Math.max(1, Number(ms) || 1) });
			return id;
		},
		clearTimeout(id) {
			timers.delete(id);
		},
		next(limit) {
			let best = null;
			for (const t of timers.values()) {
				if (t.at <= limit && (!best || t.at < best.at || (t.at === best.at && t.id < best.id))) best = t;
			}
			return best;
		},
		run(t) {
			if (t.every) t.at += t.every;
			else timers.delete(t.id);
			if (t.at > clock.now && !t.every) clock.now = t.at;
			t.fn();
		},
	};
	return clock;
}

async function drainMicrotasks() {
	for (let i = 0; i < 4; i++) await new Promise((resolve) => setImmediate(resolve));
}

// ---------------------------------------------------------------------------- fake DOM

function makeDom(env) {
	let active = null;

	class El {
		constructor(tag) {
			this.tagName = String(tag).toUpperCase();
			this.nodeType = tag === "#text" ? 3 : 1;
			this.childNodes = [];
			this.parentNode = null;
			this.attrs = {};
			this.listeners = {};
			this.style = {};
			this.dataset = {};
			this.className = "";
			this.id = "";
			this.hidden = false;
			this.disabled = false;
			this.value = "";
			this.checked = false;
			this.files = [];
			this.text = "";
		}
		get firstChild() {
			return this.childNodes[0] || null;
		}
		get children() {
			return this.childNodes.filter((c) => c.nodeType === 1);
		}
		get classList() {
			const self = this;
			const list = () => self.className.split(/\s+/).filter(Boolean);
			return {
				add(...names) {
					const l = list();
					names.forEach((n) => l.includes(n) || l.push(n));
					self.className = l.join(" ");
				},
				remove(...names) {
					self.className = list().filter((n) => !names.includes(n)).join(" ");
				},
				contains(n) {
					return list().includes(n);
				},
				toggle(n, force) {
					const on = force === undefined ? !list().includes(n) : !!force;
					if (on) this.add(n);
					else this.remove(n);
					return on;
				},
			};
		}
		get textContent() {
			return this.nodeType === 3 ? this.text : this.childNodes.map((c) => c.textContent).join("");
		}
		set textContent(v) {
			if (this.nodeType === 3) {
				this.text = String(v);
				return;
			}
			this.childNodes.forEach((c) => (c.parentNode = null));
			this.childNodes = [];
			if (v !== "" && v != null) this.appendChild(text(String(v)));
		}
		set innerHTML(html) {
			this.textContent = "";
			const m = /^<(\w+)/.exec(String(html));
			if (m) this.appendChild(new El(m[1]));
		}
		appendChild(c) {
			if (c.parentNode) c.parentNode.removeChild(c);
			c.parentNode = this;
			this.childNodes.push(c);
			return c;
		}
		removeChild(c) {
			const i = this.childNodes.indexOf(c);
			if (i !== -1) this.childNodes.splice(i, 1);
			c.parentNode = null;
			return c;
		}
		remove() {
			if (this.parentNode) this.parentNode.removeChild(this);
		}
		setAttribute(k, v) {
			v = String(v);
			this.attrs[k] = v;
			if (k === "id") this.id = v;
			else if (k === "class") this.className = v;
		}
		getAttribute(k) {
			return k in this.attrs ? this.attrs[k] : null;
		}
		removeAttribute(k) {
			delete this.attrs[k];
		}
		addEventListener(type, fn) {
			(this.listeners[type] = this.listeners[type] || []).push(fn);
		}
		removeEventListener(type, fn) {
			const l = this.listeners[type] || [];
			const i = l.indexOf(fn);
			if (i !== -1) l.splice(i, 1);
		}
		dispatch(type, ev) {
			ev = ev || {};
			ev.type = type;
			ev.target = ev.target || this;
			ev.preventDefault = ev.preventDefault || (() => {});
			(this.listeners[type] || []).slice().forEach((fn) => fn.call(this, ev));
		}
		click() {
			if (this.disabled) return;
			gesture(env);
			this.dispatch("click");
			if (typeof this.onclick === "function") this.onclick({ type: "click", target: this });
		}
		focus() {
			active = this;
		}
		blur() {}
		contains(n) {
			for (let x = n; x; x = x.parentNode) if (x === this) return true;
			return false;
		}
		get offsetParent() {
			return null;
		}
		querySelectorAll(sel) {
			const parts = String(sel)
				.split(",")
				.map((s) => s.trim());
			// Only the simple selectors the kiosk uses: #id, .class, tag. The focus-trap list is
			// all attribute and pseudo selectors, and nothing here is laid out, so it matches none.
			if (parts.some((p) => /[[\]:\s>]/.test(p))) return [];
			const test = (n) =>
				parts.some((p) => {
					if (p[0] === "#") return n.id === p.slice(1);
					if (p[0] === ".") return n.classList.contains(p.slice(1));
					return n.tagName === p.toUpperCase();
				});
			const out = [];
			const walk = (node) =>
				node.children.forEach((c) => {
					if (test(c)) out.push(c);
					walk(c);
				});
			walk(this);
			return out;
		}
		querySelector(sel) {
			return this.querySelectorAll(sel)[0] || null;
		}
	}

	function text(t) {
		const n = new El("#text");
		n.text = String(t);
		return n;
	}

	const html = new El("html");
	const head = html.appendChild(new El("head"));
	const body = html.appendChild(new El("body"));
	const root = body.appendChild(new El("div"));
	root.setAttribute("id", "kiosk-root");

	class Doc extends El {
		get activeElement() {
			return active || body;
		}
	}
	const document = new Doc("#document");
	Object.assign(document, {
		readyState: "loading",
		documentElement: html,
		head,
		body,
		visibilityState: "visible",
		createElement: (t) => new El(t),
		createTextNode: (t) => text(t),
		getElementById: (id) => html.querySelector("#" + id),
	});
	document.hidden = false;
	document.querySelector = (sel) => html.querySelector(sel);
	document.querySelectorAll = (sel) => html.querySelectorAll(sel);
	return document;
}

// ---------------------------------------------------------------------------- fake history

// Chrome's history-manipulation intervention, as far as it bites here. A tap gives the page one
// activation, and each pushState uses it up. A push made without one marks every entry of the
// page skippable (SetSkippableForSameDocumentEntries), and the Back and Forward buttons then step
// over them all, so Back leaves the app from wherever it is. The next tap clears the marks.
// Script traversals (`history.back()`) are not affected. Every push without an activation is
// recorded in `unactivated`; the skipping itself is on unless a test passes `intervention: false`.
function makeHistory(env, initial) {
	const h = {
		entries: initial || [
			{ page: "login", state: null },
			{ page: "kiosk", state: null },
		],
		index: 0,
		pushed: [], // every pushState's state, in order
		unactivated: [], // every pushState's state that no tap paid for
		replaces: 0,
		backCalls: 0,
		urlArgs: [],
		pushesBeforeTouch: 0,
		left: false,
		scrollRestoration: "auto",
	};
	h.index = h.entries.length - 1;
	const skipping = env.opts.intervention !== false;

	function traverse(delta) {
		if (h.left) return;
		const target = h.index + delta;
		if (target < 0 || target >= h.entries.length) return;
		h.index = target;
		if (h.entries[target].page !== "kiosk") {
			h.left = true;
			return;
		}
		if (h.losePopstate) {
			h.losePopstate = false; // a traversal whose event never comes
			return;
		}
		env.fireWindow("popstate", { state: clone(h.entries[target].state) });
	}
	function queue(delta) {
		env.clock.setTimeout(() => traverse(delta), 0);
	}

	h.api = {
		get length() {
			return h.entries.length;
		},
		get state() {
			return h.left ? null : clone(h.entries[h.index].state);
		},
		get scrollRestoration() {
			return h.scrollRestoration;
		},
		set scrollRestoration(v) {
			h.scrollRestoration = v;
		},
		pushState(state, title, url) {
			if (arguments.length > 2) h.urlArgs.push(["pushState", url]);
			if (!env.touched) h.pushesBeforeTouch += 1;
			if (!env.activation) {
				h.unactivated.push(clone(state));
				h.entries.forEach((e) => {
					if (e.page === "kiosk") e.skip = true;
				});
			}
			env.activation = false;
			h.pushed.push(clone(state));
			h.entries.splice(h.index + 1);
			h.entries.push({ page: "kiosk", state: clone(state) });
			h.index += 1;
		},
		replaceState(state, title, url) {
			if (arguments.length > 2) h.urlArgs.push(["replaceState", url]);
			h.replaces += 1;
			h.entries[h.index].state = clone(state);
		},
		back() {
			h.backCalls += 1;
			queue(-1);
		},
		forward() {
			queue(1);
		},
		go(n) {
			queue(Number(n) || 0);
		},
	};
	// The Back and Forward buttons: a step of one, over any entry the intervention marked.
	function userStep(dir) {
		env.clock.setTimeout(() => {
			if (h.left) return;
			let target = h.index + dir;
			while (skipping && target >= 0 && target < h.entries.length && h.entries[target].skip) target += dir;
			if (target < 0) target = 0; // nothing but skipped entries behind: the first one leaves too
			if (target >= h.entries.length) return;
			traverse(target - h.index);
		}, 0);
	}
	h.onActivation = () =>
		h.entries.forEach((e) => {
			e.skip = false;
		});
	h.userBack = () => userStep(-1);
	h.userForward = () => userStep(1);
	h.userGo = (n) => queue(n); // the long-press Back menu: several entries at once
	h.current = () => h.entries[h.index].state;
	h.kioskPushes = () => h.pushed.filter((s) => s && s.tk_nav).length;
	h.kioskUnactivated = () => h.unactivated.filter((s) => s && s.tk_nav);
	return h;
}

// A tap, as the page and Chrome's history intervention see it: El.click() makes one, and so does a
// test that opens a sheet directly "as if by a tap".
function gesture(env) {
	env.touched = true;
	env.activation = true;
	env.history.onActivation();
}

// ---------------------------------------------------------------------------- fake server

function makeServer(env) {
	const interval = {
		name: "JI-0001",
		status: "Open",
		project: "PRJ-0001",
		project_title: "Main Fountain",
		start_time: "2026-09-24 08:00:00",
		attachments: [],
		photo_count: env.opts.photoCount == null ? 1 : env.opts.photoCount,
		time_category: "Labor",
	};
	env.status = env.opts.clockedIn ? clone(interval) : null;
	return function answer(method, args) {
		switch (method) {
			case "get_kiosk_options":
				return {
					projects: [{ value: "PRJ-0001", label: "Main Fountain" }],
					activity_types: [{ value: "Labor", label: "Labor" }],
					recent_projects: [],
					radius_m: 0,
				};
			case "get_current_status":
				return clone(env.status);
			case "get_shift_summary":
				return { today_seconds: 3600, interval_seconds: 3600, photo_count: 1, sites: ["Main Fountain"] };
			case "get_maintenance_context":
				return { required: false };
			case "get_my_history":
				return { days: [] };
			case "get_my_day":
				return {
					intervals: [
						{
							name: "JI-0000",
							project: "PRJ-0001",
							project_title: "Main Fountain",
							start_time: "2026-09-24 07:00:00",
							end_time: "2026-09-24 07:30:00",
							worked_seconds: 1800,
							status: "Closed",
							time_category: "Labor",
							editable: 1,
						},
					],
				};
			case "get_my_correction_requests":
			case "get_my_visits_today":
			case "get_tasks_for_project":
				return [];
			case "log_time":
				if (args.action === "Start") env.status = clone(interval);
				if (args.action === "Stop") env.status = null;
				return { status: "success", message: "Done." };
			default:
				return null;
		}
	};
}

// ---------------------------------------------------------------------------- the capture panel

// A stand-in for capture/panel.js with the history behaviour the shared contract gives it: it
// pushes {ee_capture: id} with no URL when it opens, answers popstate while open (asking first
// when something is typed — `answer` is what the person picks), and when it closes any other
// way while its entry is current it removes it with one back() and counts as open until that
// back() has landed. `isOpen` can be withheld to stand for a recorder that predates it.
function installCapture(env, opts) {
	const cap = { opens: 0, open: false, armed: false, pending: null, hid: null, answer: "discard", asked: 0 };
	const win = env.win;
	function onPop(ev) {
		if (!cap.open || !cap.armed) return;
		const s = ev.state;
		if (s && s.ee_capture === cap.hid) return;
		cap.armed = false;
		cap.asked += 1;
		if (cap.answer === "stay") {
			push();
			return;
		}
		close();
	}
	function push() {
		win.history.pushState({ ee_capture: cap.hid }, "");
		cap.armed = true;
	}
	function close() {
		if (!cap.open) return;
		cap.open = false;
		env.removeWindowListener("popstate", onPop);
		if (!cap.armed) return;
		cap.armed = false;
		const st = win.history.state;
		if (st && st.ee_capture === cap.hid) {
			const landed = () => {
				cap.pending = null;
				env.removeWindowListener("popstate", landed);
			};
			cap.pending = landed;
			env.addWindowListener("popstate", landed, opts.listenerFirst);
			win.history.back();
		}
	}
	win.ee_capture = {
		open() {
			cap.opens += 1;
			cap.open = true;
			cap.hid = "cap-" + cap.opens;
			env.addWindowListener("popstate", onPop, opts.listenerFirst);
			push();
			return Promise.resolve({ close });
		},
	};
	if (opts.isOpen !== false) win.ee_capture.isOpen = () => cap.open || !!cap.pending;
	cap.closeFromUi = close;
	return cap;
}

// ---------------------------------------------------------------------------- boot

async function boot(opts) {
	opts = opts || {};
	const env = { opts, touched: false, calls: [], views: {} };
	env.clock = makeClock();
	const listeners = {};
	env.addWindowListener = (type, fn, first) => {
		const l = (listeners[type] = listeners[type] || []);
		if (first) l.unshift(fn);
		else l.push(fn);
	};
	env.removeWindowListener = (type, fn) => {
		const l = listeners[type] || [];
		const i = l.indexOf(fn);
		if (i !== -1) l.splice(i, 1);
	};
	env.fireWindow = (type, ev) => {
		ev = Object.assign({ type }, ev);
		(listeners[type] || []).slice().forEach((fn) => fn(ev));
	};
	env.history = makeHistory(env, opts.entries);
	const document = makeDom(env);
	env.document = document;
	const answer = makeServer(env);

	const storage = new Map();
	const win = {
		document,
		navigator: { userAgent: "Mozilla/5.0 (Linux; Android 14) Chrome/130", onLine: true, maxTouchPoints: 5 },
		localStorage: {
			getItem: (k) => (storage.has(k) ? storage.get(k) : null),
			setItem: (k, v) => storage.set(k, String(v)),
			removeItem: (k) => storage.delete(k),
		},
		matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
		getComputedStyle: () => ({ getPropertyValue: () => "" }),
		requestAnimationFrame: (fn) => env.clock.setTimeout(() => fn(env.clock.now), 16),
		setTimeout: env.clock.setTimeout,
		clearTimeout: env.clock.clearTimeout,
		setInterval: env.clock.setInterval,
		clearInterval: env.clock.clearTimeout,
		history: env.history.api,
		location: { pathname: "/kiosk", search: "", hash: "", reload() {} },
		scrollTo() {},
		addEventListener: (type, fn) => env.addWindowListener(type, fn),
		removeEventListener: (type, fn) => env.removeWindowListener(type, fn),
		URLSearchParams,
		console,
		KIOSK_BOOT: {
			employee: "EMP-0001",
			employee_name: "Pat Field",
			user: "pat@example.com",
			settings: {},
			status: null,
			photo_gate: opts.photoGate || {},
		},
		KIOSK_CSRF: "csrf",
		KIOSK_BUILD: "test",
		KioskGeo: {
			configure: () => ({ onStatus() {} }),
			warmup() {},
			lastFix: () => null,
			start() {},
			stop() {},
			anchorFix: () => Promise.resolve(null),
			distanceM: () => 0,
			queuedCount: () => Promise.resolve(0),
			getDiagnostics: () => ({ status: "off", permission: "prompt", enabled: true }),
		},
		// The map needs Google Maps, which is not the subject here.
		KioskViews: {
			map: {
				mount() {},
				show() {
					env.views.mapShown = (env.views.mapShown || 0) + 1;
				},
				hide() {},
			},
		},
		fetch(url, init) {
			const m = /\.(\w+)(?:\?|$)/.exec(String(url));
			const method = m ? m[1] : "";
			const args = init && init.body ? JSON.parse(init.body) : {};
			env.calls.push({ method, args });
			const body = { message: answer(method, args) };
			const res = { ok: true, status: 200, json: () => Promise.resolve(body) };
			const latency = opts.latency || 0;
			if (!latency) return Promise.resolve(res);
			return new Promise((resolve) => env.clock.setTimeout(() => resolve(res), latency));
		},
	};
	win.window = win;
	win.self = win;
	env.win = win;
	env.status = opts.clockedIn ? answer("get_current_status") : null;
	win.KIOSK_BOOT.status = clone(env.status);
	if (opts.capture) env.capture = installCapture(env, opts.capture);

	vm.createContext(win);
	for (const name of SCRIPTS) {
		const file = path.join(KIOSK, name);
		vm.runInContext(fs.readFileSync(file, "utf8"), win, { filename: file });
	}
	document.readyState = "complete";
	document.dispatch("DOMContentLoaded");
	env.UI = win.KioskUI;
	await settle(env);
	return env;
}

// Run every timer due within `ms` of virtual time, in order, with microtasks drained between.
async function settle(env, ms) {
	const horizon = env.clock.now + (ms == null ? 500 : ms);
	for (let i = 0; i < 2000; i++) {
		await drainMicrotasks();
		const t = env.clock.next(horizon);
		if (!t) break;
		env.clock.run(t);
	}
	await drainMicrotasks();
	env.clock.now = Math.max(env.clock.now, horizon);
}

// Run exactly the next due timer (for the races).
async function step(env) {
	await drainMicrotasks();
	const t = env.clock.next(env.clock.now);
	if (t) env.clock.run(t);
	await drainMicrotasks();
}

async function back(env) {
	env.history.userBack();
	await settle(env);
}

async function forward(env) {
	env.history.userForward();
	await settle(env);
}

function byId(env, id) {
	return env.document.getElementById(id);
}

function visibleTab(env) {
	for (const t of ["clock", "myday", "map", "settings"]) {
		const p = byId(env, "tk-panel-" + t);
		if (p && !p.hidden) return t;
	}
	return null;
}

function depth(env) {
	return env.UI.sheet.depth();
}

function topLayer(env) {
	const host = byId(env, "tk-sheets");
	const open = host ? host.children.filter((l) => l.classList.contains("is-open")) : [];
	return open[open.length - 1] || null;
}

function button(scope, label) {
	return (scope ? scope.querySelectorAll("button") : []).find((b) => b.textContent === label) || null;
}

async function tap(env, node) {
	if (!node) throw new Error("tap: nothing to tap");
	node.click();
	await settle(env);
}

async function tapTab(env, id) {
	await tap(env, byId(env, "tk-tab-" + id));
}

async function tapInSheet(env, label) {
	await tap(env, button(topLayer(env), label));
}

// A sheet opened as if by a tap on something that opens it.
async function openSheet(env, opts) {
	gesture(env);
	const handle = env.UI.sheet.open(opts);
	await settle(env);
	return handle;
}

function current(env) {
	const s = env.history.current();
	return s && s.tk_nav ? { tab: s.tab, sheet: s.sheet } : s;
}

function logTimeCalls(env) {
	return env.calls.filter((c) => c.method === "log_time").map((c) => c.args.action);
}

// Every test ends here: no URL passed, nothing pushed before a tap, no kiosk entry pushed without
// a tap of its own (Chrome would then skip every entry, and Back would leave the app), and every
// kiosk state is exactly {tk_nav, id, tab, sheet} — the clock state never rides along.
// `unpaid` is the number of activation-less kiosk pushes a test knowingly makes (default none).
function invariants(env, label, unpaid) {
	check(`${label}: no pushState/replaceState was given a URL`, env.history.urlArgs, []);
	check(`${label}: nothing was pushed before the first tap`, env.history.pushesBeforeTouch, 0);
	check(`${label}: every kiosk push spent a tap of its own`, env.history.kioskUnactivated().length, unpaid || 0);
	const keys = new Set();
	env.history.entries.forEach((e) => e.state && e.state.tk_nav && Object.keys(e.state).forEach((k) => keys.add(k)));
	check(`${label}: an entry holds only its tab and sheet flag`, [...keys].sort(), keys.size ? ["id", "sheet", "tab", "tk_nav"] : []);
}

// ---------------------------------------------------------------------------- the tests

const tests = [];
function test(name, fn) {
	tests.push({ name, fn });
}

test("boot stamps the entry it loaded into and pushes nothing", async () => {
	const env = await boot();
	check("still two entries (login, kiosk)", env.history.entries.length, 2);
	check("still on the kiosk entry", env.history.index, 1);
	check("nothing pushed", env.history.pushed.length, 0);
	check("the entry is stamped as Clock", current(env), { tab: "clock", sheet: 0 });
	check("Clock is on screen", visibleTab(env), "clock");
	check("the kiosk decides scrolling itself", env.history.scrollRestoration, "manual");
	await back(env);
	check("Back from the first entry leaves the page", env.history.left, true);
	invariants(env, "boot");
});

test("a tab tap is an entry: Back walks the tabs, Forward restores them", async () => {
	const env = await boot();
	await tapTab(env, "myday");
	await tapTab(env, "map");
	await tapTab(env, "settings");
	check("three taps, three entries", env.history.kioskPushes(), 3);
	check("the top entry is Settings", current(env), { tab: "settings", sheet: 0 });
	await tapTab(env, "settings");
	check("tapping the tab on screen pushes nothing", env.history.kioskPushes(), 3);
	await back(env);
	check("Back shows Map", visibleTab(env), "map");
	await back(env);
	check("Back shows My Day", visibleTab(env), "myday");
	await back(env);
	check("Back shows Clock", visibleTab(env), "clock");
	check("going back pushed nothing", env.history.kioskPushes(), 3);
	await forward(env);
	check("Forward shows My Day", visibleTab(env), "myday");
	await forward(env);
	check("Forward shows Map", visibleTab(env), "map");
	await tapTab(env, "clock");
	check("a tap after going back drops the forward entries", env.history.entries.length, env.history.index + 1);
	await back(env);
	check("Back returns to Map", visibleTab(env), "map");
	await back(env);
	await back(env);
	check("… then Clock", visibleTab(env), "clock");
	await back(env);
	check("and Back from the first entry leaves the page", env.history.left, true);
	invariants(env, "tabs");
});

test("Back closes a sheet, and a Forward onto its spent entry opens nothing", async () => {
	const env = await boot();
	await tap(env, byId(env, "tk-pick-project"));
	check("the project picker is open", depth(env), 1);
	check("one marker entry over Clock", current(env), { tab: "clock", sheet: 1 });
	await back(env);
	check("Back closed it", depth(env), 0);
	check("back on the Clock entry", env.history.index, 1);
	check("still on Clock", visibleTab(env), "clock");
	check("nothing re-pushed", env.history.kioskPushes(), 1);
	await forward(env);
	check("Forward reopens nothing", depth(env), 0);
	check("… and steps back off the spent entry", env.history.index, 1);
	await back(env);
	check("the next Back leaves the page: no dead entry", env.history.left, true);
	invariants(env, "sheet");
});

test("a sheet closed from the UI takes its entry with it (one back(), not read as a Back)", async () => {
	const env = await boot();
	await tapTab(env, "myday");
	await tap(env, byId(env, "tk-tab-clock"));
	await tap(env, byId(env, "tk-pick-project"));
	check("picker open over Clock", current(env), { tab: "clock", sheet: 1 });
	await tap(env, topLayer(env).querySelector(".tk-sheet-close"));
	check("× closed it", depth(env), 0);
	check("exactly one back() consumed the marker", env.history.backCalls, 1);
	check("the page is on its Clock entry again", current(env), { tab: "clock", sheet: 0 });
	check("… and the tab did not move", visibleTab(env), "clock");
	await tap(env, byId(env, "tk-pick-project"));
	const row = topLayer(env).querySelector(".tk-row");
	await tap(env, row);
	check("picking a project closes the picker", depth(env), 0);
	check("… and consumes its marker too", current(env), { tab: "clock", sheet: 0 });
	await back(env);
	check("so the next Back goes to the previous tab", visibleTab(env), "myday");
	env.touched = true;
	env.document.dispatch("keydown", { key: "Escape" });
	await tap(env, byId(env, "tk-tab-clock"));
	await tap(env, byId(env, "tk-pick-project"));
	env.document.dispatch("keydown", { key: "Escape" });
	await settle(env);
	check("Escape closes it the same way", [depth(env), current(env)], [0, { tab: "clock", sheet: 0 }]);
	invariants(env, "ui close");
});

test("sheets replacing each other in a burst share one entry", async () => {
	const env = await boot();
	await openSheet(env, {
		title: "A",
		actions: [
			{
				label: "Next",
				onClick(hdl) {
					hdl.close("action");
					env.UI.sheet.open({ title: "B" });
				},
			},
			{
				label: "Later",
				onClick(hdl) {
					hdl.close("action");
					Promise.resolve().then(() => env.UI.sheet.open({ title: "C" }));
				},
			},
		],
	});
	await tapInSheet(env, "Next");
	check("A was replaced by B synchronously", depth(env), 1);
	check("no second entry, no back()", [env.history.kioskPushes(), env.history.backCalls], [1, 0]);
	env.UI.sheet.closeAll();
	await settle(env);
	await openSheet(env, { title: "A2", actions: [{ label: "Later", onClick: (hdl) => { hdl.close("action"); Promise.resolve().then(() => env.UI.sheet.open({ title: "C" })); } }] });
	const pushes = env.history.kioskPushes();
	const backs = env.history.backCalls;
	await tapInSheet(env, "Later");
	check("replaced in a microtask", depth(env), 1);
	check("still one entry", [env.history.kioskPushes() - pushes, env.history.backCalls - backs], [0, 0]);
	await back(env);
	check("Back closes the replacement", [depth(env), current(env)], [0, { tab: "clock", sheet: 0 }]);
	invariants(env, "burst");
});

test("My Day: detail -> Edit times is one entry; Back closes the form and keeps the tab", async () => {
	const env = await boot();
	await tapTab(env, "myday");
	const row = byId(env, "tk-panel-myday").querySelector(".tk-row");
	await tap(env, row);
	check("the detail sheet is open", depth(env), 1);
	await tapInSheet(env, "Edit times");
	check("Edit times replaced it", depth(env), 1);
	check("one marker over My Day", current(env), { tab: "myday", sheet: 1 });
	check("pushes: the tab and one marker", env.history.kioskPushes(), 2);
	await back(env);
	check("Back closed the form", depth(env), 0);
	check("… on My Day, still", visibleTab(env), "myday");
	check("… at the My Day entry", current(env), { tab: "myday", sheet: 0 });
	await back(env);
	check("the next Back is Clock", visibleTab(env), "clock");
	invariants(env, "my day");
});

test("stacked sheets: each Back closes exactly one", async () => {
	const env = await boot();
	await tap(env, byId(env, "tk-forgot-clock-in"));
	check("Add missed time is open", depth(env), 1);
	await tap(env, byId(env, "tk-backdate-project"));
	check("the project picker is stacked on it", depth(env), 2);
	check("one marker covers the stack", env.history.kioskPushes(), 1);
	await back(env);
	check("Back closed the picker only", depth(env), 1);
	check("… and the marker is back for the form", current(env), { tab: "clock", sheet: 1 });
	// The re-push spends the tap that stacked the picker, which pushed nothing. One more sheet on
	// the stack and the next re-push would have no tap left (ui.js, "History"): none is that deep.
	check("… paid for by the tap that stacked the picker", env.history.kioskUnactivated().length, 0);
	await back(env);
	check("Back closed the form", depth(env), 0);
	check("… onto the Clock entry", current(env), { tab: "clock", sheet: 0 });
	await back(env);
	check("then Back leaves the page", env.history.left, true);
	invariants(env, "stack");
});

test("a dismissible:false sheet survives Back", async () => {
	const env = await boot();
	const handle = await openSheet(env, { title: "Hold on", dismissible: false });
	await back(env);
	check("still open", depth(env), 1);
	check("its marker is back", current(env), { tab: "clock", sheet: 1 });
	// The one push no tap pays for, so Chrome would let the next Back skip the page's entries.
	// No kiosk sheet is dismissible:false today; ui.js ("History") says so, and this counts it.
	check("that re-push had no tap behind it", env.history.kioskUnactivated().length, 1);
	handle.close("action");
	await settle(env);
	check("closed by its own button, the marker goes", [depth(env), current(env)], [0, { tab: "clock", sheet: 0 }]);
	invariants(env, "dismissible", 1);
});

test("Back answers ask() and askText() as cancel", async () => {
	const env = await boot();
	const reasons = [];
	await openSheet(env, { title: "Any sheet", onClose: (r) => reasons.push(r) });
	await back(env);
	check("Back closes a sheet the way Escape does: close('dismiss')", reasons, ["dismiss"]);
	gesture(env); // each gate opens from a tap of its own
	const asked = env.UI.ask({ title: "Sure?" });
	await settle(env);
	await back(env);
	check("ask() resolves false", await asked, false);
	gesture(env);
	const typed = env.UI.askText({ title: "Why?" });
	await settle(env);
	await back(env);
	check("askText() resolves null", await typed, null);
	check("nothing left open, nothing left over", [depth(env), current(env)], [0, { tab: "clock", sheet: 0 }]);
	invariants(env, "gates");
});

test("Back on 'Review your day' clocks nobody out", async () => {
	const env = await boot({ clockedIn: true });
	await tap(env, byId(env, "tk-clock-out"));
	check("the review sheet is open", depth(env), 1);
	await back(env);
	check("Back closed it", depth(env), 0);
	check("no clock action was sent", logTimeCalls(env), []);
	check("still working", byId(env, "tk-hero").classList.contains("is-working"), true);
	invariants(env, "review");
});

test("Back on the photo gate's 'Why no photo?' (after a slow server) posts nothing", async () => {
	const env = await boot({
		clockedIn: true,
		photoCount: 0,
		latency: 120,
		photoGate: { require_job_photos: 1, allow_photo_skip: 1, require_skip_reason: 1, min_photos_per_interval: 1 },
	});
	await tap(env, byId(env, "tk-clock-out"));
	await tapInSheet(env, "Confirm clock out");
	check("the photo gate opened after the maintenance check", depth(env), 1);
	check("… with its own marker", current(env), { tab: "clock", sheet: 1 });
	await tapInSheet(env, "Clock out without a photo");
	check("'Why no photo?' replaced it", depth(env), 1);
	await back(env);
	check("Back closed it", depth(env), 0);
	check("… and nothing was posted", logTimeCalls(env), []);
	check("… and no entry is left over", current(env), { tab: "clock", sheet: 0 });
	check("still working", byId(env, "tk-hero").classList.contains("is-working"), true);
	invariants(env, "photo gate");
});

test("a clock action is never an entry, and Back afterwards does not undo it", async () => {
	const env = await boot();
	await tap(env, byId(env, "tk-pick-project"));
	await tap(env, topLayer(env).querySelector(".tk-row"));
	await tap(env, byId(env, "tk-clock-in"));
	check("clocked in", logTimeCalls(env), ["Start"]);
	check("working", byId(env, "tk-hero").classList.contains("is-working"), true);
	await tap(env, byId(env, "tk-clock-out"));
	await tapInSheet(env, "Confirm clock out");
	check("clocked out", logTimeCalls(env), ["Start", "Stop"]);
	check("day complete", byId(env, "tk-hero").classList.contains("is-done"), true);
	check("only the two sheet markers were ever pushed", env.history.kioskPushes(), 2);
	check("and both are gone", [env.history.index, current(env)], [1, { tab: "clock", sheet: 0 }]);
	await back(env);
	check("Back leaves the page instead of undoing anything", env.history.left, true);
	check("… having posted nothing more", logTimeCalls(env), ["Start", "Stop"]);
	invariants(env, "clock");
});

test("race: a sheet that opens while the page's own back() is in flight", async () => {
	const env = await boot();
	await tap(env, byId(env, "tk-pick-project"));
	const base = env.history.index - 1;
	topLayer(env).querySelector(".tk-sheet-close").click();
	await step(env); // the reconcile: back() is now in flight
	check("the back() was made", env.history.backCalls, 1);
	env.UI.sheet.open({ title: "Arrived meanwhile" });
	await settle(env);
	check("the new sheet is still open: our own popstate was not read as Back", depth(env), 1);
	check("its marker is the current entry", [env.history.index, current(env)], [base + 1, { tab: "clock", sheet: 1 }]);
	check("the old marker was replaced, not left behind", env.history.entries.length, base + 2);
	await back(env);
	check("Back closes it", [depth(env), env.history.index], [0, base]);
	invariants(env, "race sheet");
});

test("race: a tab tap while the page's own back() is in flight", async () => {
	const env = await boot();
	await tap(env, byId(env, "tk-pick-project"));
	topLayer(env).querySelector(".tk-sheet-close").click();
	await step(env);
	byId(env, "tk-tab-myday").click();
	await settle(env);
	check("My Day is on screen", visibleTab(env), "myday");
	check("its entry replaced the spent marker", [env.history.entries.length, current(env)], [3, { tab: "myday", sheet: 0 }]);
	await back(env);
	check("Back returns to Clock", [visibleTab(env), env.history.index], ["clock", 1]);
	invariants(env, "race tab");
});

test("a back() whose popstate never arrives does not freeze the history", async () => {
	const env = await boot();
	await tap(env, byId(env, "tk-pick-project"));
	env.history.losePopstate = true;
	await tap(env, topLayer(env).querySelector(".tk-sheet-close"));
	check("the marker's back() was made", env.history.backCalls, 1);
	await tapTab(env, "myday");
	check("while it is awaited, a tab tap waits too", env.history.kioskPushes(), 1);
	await settle(env, 2500);
	check("after the wait the tab's entry is pushed", [visibleTab(env), current(env)], ["myday", { tab: "myday", sheet: 0 }]);
	await tap(env, byId(env, "tk-tab-clock"));
	await tap(env, byId(env, "tk-pick-project"));
	await back(env);
	check("and sheets still close on Back", [depth(env), visibleTab(env)], [0, "clock"]);
	await back(env);
	check("and tabs still walk back", visibleTab(env), "myday");
	invariants(env, "lost popstate");
});

test("closeAll() is one back()", async () => {
	const env = await boot();
	await openSheet(env, { title: "One" });
	await openSheet(env, { title: "Two" });
	env.UI.sheet.closeAll();
	await settle(env);
	check("both closed, one back()", [depth(env), env.history.backCalls, current(env)], [0, 1, { tab: "clock", sheet: 0 }]);
	invariants(env, "closeAll");
});

test("Report a problem owns its entry (the capture panel contract)", async () => {
	// Without Chrome's skipping: the panel's "stay" re-push follows a window.confirm, which is no
	// tap, so Chrome would hold it against the page. That push is the panel's, not the kiosk's
	// (the kiosk-side count in invariants() still has to be zero), and this test is about the
	// kiosk standing aside.
	const env = await boot({ capture: {}, intervention: false });
	const cap = env.capture;
	await tapTab(env, "settings");
	const settingsEntry = env.history.current().id;
	await tap(env, button(byId(env, "tk-panel-settings"), "Report a problem"));
	check("the panel pushed its own entry", env.history.current(), { ee_capture: "cap-1" });
	check("the kiosk pushed nothing for it", env.history.kioskPushes(), 1);
	cap.answer = "stay";
	await back(env);
	check("Back while open is the panel's: it asked", cap.asked, 1);
	check("… the person stayed, so the panel's entry is back", env.history.current(), { ee_capture: "cap-1" });
	check("… and the kiosk did nothing", [visibleTab(env), depth(env)], ["settings", 0]);
	cap.answer = "discard";
	await back(env);
	check("Back again closes the panel", cap.open, false);
	check("… onto the untouched Settings entry", env.history.current().id, settingsEntry);
	check("… still on Settings", visibleTab(env), "settings");

	await tap(env, button(byId(env, "tk-panel-settings"), "Report a problem"));
	cap.closeFromUi();
	await settle(env);
	check("closed from its own button, the panel removed its entry", env.history.current().id, settingsEntry);
	check("… and the kiosk read that back() as nothing", [visibleTab(env), depth(env)], ["settings", 0]);
	await forward(env);
	check("Forward onto the panel's leftover entry opens nothing", [cap.open, visibleTab(env)], [false, "settings"]);
	check("… and the kiosk re-stamped it as the screen it shows", current(env), { tab: "settings", sheet: 0 });
	await back(env);
	check("the re-stamped entry is only a dead Back", visibleTab(env), "settings");
	await back(env);
	check("then Back walks the tabs again", visibleTab(env), "clock");
	invariants(env, "capture");
});

test("Report a problem, with the panel's listener first and no isOpen() at all", async () => {
	for (const variant of [{ listenerFirst: true }, { isOpen: false }]) {
		const env = await boot({ capture: variant });
		const label = variant.isOpen === false ? "no isOpen()" : "panel listener first";
		await tapTab(env, "settings");
		const settingsEntry = env.history.current().id;
		await tap(env, button(byId(env, "tk-panel-settings"), "Report a problem"));
		await back(env);
		check(`${label}: Back closed the panel`, env.capture.open, false);
		check(`${label}: the kiosk stayed on Settings`, [visibleTab(env), env.history.current().id], ["settings", settingsEntry]);
		await tap(env, button(byId(env, "tk-panel-settings"), "Report a problem"));
		env.capture.closeFromUi();
		await settle(env);
		check(`${label}: a UI close leaves the kiosk where it was`, [visibleTab(env), env.history.current().id], ["settings", settingsEntry]);
		await back(env);
		check(`${label}: and Back then walks the tabs`, visibleTab(env), "clock");
		invariants(env, label);
	}
});

test("a jump several entries back while the report panel is open leaves the history working", async () => {
	const env = await boot({ capture: {} });
	await tapTab(env, "settings");
	await tap(env, button(byId(env, "tk-panel-settings"), "Report a problem"));
	env.history.userGo(-2); // past the Settings entry, onto Clock's
	await settle(env);
	check("the panel answered it and closed", [env.capture.asked, env.capture.open], [1, false]);
	check("the kiosk still shows Settings, under where the panel was", visibleTab(env), "settings");
	// The person was answering the panel, not walking the kiosk: the entry they landed on takes
	// the screen's tab. Pushing one instead would be a push with no tap behind it.
	check("… on the entry the jump landed on, re-stamped as Settings", [env.history.index, current(env)], [1, { tab: "settings", sheet: 0 }]);
	check("… and nothing pushed for it", env.history.kioskPushes(), 1);
	await tapTab(env, "myday");
	check("tab taps push again", current(env), { tab: "myday", sheet: 0 });
	await back(env);
	check("Back: Settings", visibleTab(env), "settings");
	await back(env);
	check("Back from the first entry leaves the page", env.history.left, true);
	invariants(env, "jump");
});

test("after that jump, Back still walks the kiosk (Chrome skips entries a tapless push leaves)", async () => {
	// The same jump from deeper in: a push there, with no tap to pay for it, would have made
	// Chrome skip every entry of the page, and this Back would have left the app from Settings.
	const env = await boot({ capture: {} });
	await tapTab(env, "myday");
	await tapTab(env, "map");
	await tapTab(env, "settings");
	await tap(env, button(byId(env, "tk-panel-settings"), "Report a problem"));
	env.history.userGo(-2); // onto Map's entry
	await settle(env);
	check("the panel closed and the kiosk kept Settings", [env.capture.open, visibleTab(env)], [false, "settings"]);
	check("Map's entry now says Settings", current(env), { tab: "settings", sheet: 0 });
	check("no kiosk push without a tap", env.history.kioskUnactivated().length, 0);
	await back(env);
	check("Back: My Day, still inside the app", [env.history.left, visibleTab(env)], [false, "myday"]);
	await back(env);
	check("Back: Clock", visibleTab(env), "clock");
	await back(env);
	check("then Back leaves the page", env.history.left, true);
	invariants(env, "deep jump");
});

test("entries left by an earlier load of the page are never interpreted", async () => {
	const old = (id, tab, sheet) => ({ page: "kiosk", state: { tk_nav: "tkOLD", id, tab, sheet } });
	const env = await boot({
		entries: [{ page: "login", state: null }, old(1, "clock", 0), old(2, "myday", 0), old(3, "myday", 1)],
	});
	check("the reloaded entry is stamped as this load's Clock", current(env), { tab: "clock", sheet: 0 });
	check("the new load has its own id", env.history.current().tk_nav !== "tkOLD", true);
	check("nothing pushed", env.history.pushed.length, 0);
	await back(env);
	check("Back onto an old My Day entry stays on Clock", [visibleTab(env), depth(env)], ["clock", 0]);
	check("… and re-stamps it", current(env), { tab: "clock", sheet: 0 });
	await back(env);
	await back(env);
	check("the first entry still leads out of the page", env.history.left, true);
	invariants(env, "reload");
});

(async () => {
	for (const t of tests) {
		console.log(t.name);
		try {
			await t.fn();
		} catch (err) {
			failures += 1;
			console.error(`  FAIL ${t.name}: threw ${(err && err.stack) || err}`);
		}
	}
	if (failures) {
		console.error(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed (${tests.length} scenarios)`);
})();
