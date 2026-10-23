#!/usr/bin/env node
/**
 * Browser Back / Forward on seven Desk pages, run against a model of Frappe v16's router.
 *
 * Loads the REAL page scripts — Device Console, Inventory Scanner Audit, Sales Pipeline,
 * QuickBooks Record Matching, Question Review, Location Timeline and the Maintenance Day Board —
 * into a vm context over a small fake Desk, then taps, scans and presses Back and Forward. Plain
 * node, no runner, no npm install.
 * The two scanner pages run on the REAL scanner kit (public/js/scanner_kit, v1.666.0),
 * installed on the fake Desk's window when they ask frappe.require for it; a Desk can be made
 * without it ({ scanner: false }), with it arriving late ({ scannerLate: true }), or as an
 * iPhone ({ iphone: true }: no BarcodeDetector, so the kit loads the vendored jsQR).
 *
 * The Desk is modelled on `git show origin/version-16:frappe/public/js/frappe/…`, never the
 * develop tree, in the places these pages depend on:
 *
 *   - router.js: `popstate` runs `frappe.router.route()`, which awaits `parse()`, sets
 *     `current_route`, then `set_history()` (which closes the open dialog), `render()` and
 *     triggers "change". `parse()` copies the address's query into `frappe.route_options` (merged
 *     into what is there) on every route change. `set_route()` moves a plain-object argument into
 *     `frappe.route_options` (`make_url`) and pushes the PATH only (`history.pushState(null, null,
 *     path)`), replaces instead when `frappe.route_flags.replace_route` is set, skips an unchanged
 *     path and query (the query JSON-encoded, quotes and all), and resolves after a 100 ms timeout
 *     and `frappe.after_ajax`, clearing `route_flags` only then. v16.50 changed that (frappe PR
 *     #40040, `fb2e7370ba`): a plain value is encoded as itself and the query is pushed WITH the
 *     path. `makeDesk({ queryInUrl: true })` models the newer router; the deep-link scenarios run
 *     under both, since a site may be on either side of it. `after_ajax` runs at once here unless the desk is made with
 *     `{ ajax: true }`: then it waits, as request.js's does, until no request is in flight, so a
 *     flag set for one route change is still set for the next while any reply is outstanding.
 *     It is opt-in because most scenarios hold a reply open and still tap a sheet open meanwhile.
 *   - views/container.js `change_to()`: hides a displayed dialog, triggers "hide" on the page
 *     it leaves and "show" on every show, the same page included. pageview.js runs
 *     `on_page_load` once per page and `on_page_show` on every "show".
 *   - ui/dialog.js on Bootstrap 4.6: a modal fades in over 300 ms; `display` and `cur_dialog`
 *     are set only once it is shown, and `hide()` on a modal still fading in is ignored.
 *     `is_visible` is set by `show()` itself and cleared only by `hide()`: the dialog's X is
 *     `data-dismiss="modal"` (dom.js), which Bootstrap handles with no `hide()` around it, so
 *     `closeDialog` leaves `is_visible` set. Bootstrap's own state, `$wrapper.data("bs.modal")
 *     ._isShown`, is set as a show starts and cleared as any hide does. frappe's `onhide` runs
 *     on "hide"; Bootstrap's "hidden.bs.modal" fires once the 300 ms fade-out is over.
 *     messages.js makes msgprint's dialog once and keeps it as `frappe.msg_dialog`, and
 *     request.js has put a refusal up in it before the call's promise rejects; a request that
 *     never reached the server (status 0) has no handler there and puts no dialog up.
 *   - A reload (`desk.reload()`) is a new Desk, page scripts and all, over the same session
 *     history: the browser keeps every entry's URL and `history.state` across it.
 *   - A browser traversal (Back, Forward, `history.back()`) is an asynchronous task that fires
 *     `popstate` when it lands; Back from the first entry leaves the page.
 *
 * And one rule from the browser: Chrome's history intervention. An entry pushed with no user
 * activation since the one before it makes Back skip entries, so every push here must follow
 * a tap or a key press. `violations` counts the pushes that did not.
 *
 * Run: node scripts/test_desk_page_history.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { pathToFileURL } = require("url");

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const LOADER = path.join(APP, "public", "js", "sf_kit_loader.js");
const SF_BUNDLE = path.join(APP, "public", "js", "sf_kit.bundle.js");
let kitCopies = 0;
// The scanner kit both scanner pages load (v1.666.0): its index module, imported once before the
// scenarios run, and installed on each Desk's window when a page asks frappe.require for
// "scanner_kit.bundle.js", as the bundle installs it on a live Desk. Each install is bound to its
// own Desk's window, so a reload's new Desk gets a copy of its own.
const SCANNER_KIT = path.join(APP, "public", "js", "scanner_kit", "index.js");
const JSQR_PATH = "/assets/erpnext_enhancements/js/stock_scan/lib/jsQR-1.4.0.min.js";
let scannerKit = null;
// The reorder boxes (v1.725.0), the real modules (imported once before the scenarios), installed on a
// Desk made with { reorder: true } when a page asks for "reorder_levels.bundle.js". The DOM wiring is
// recorded, not run: this Desk has no real elements (see scripts/test_reorder_levels.mjs for those).
const REORDER_DIR = path.join(APP, "public", "js", "reorder_levels");
let reorderMods = null;
const SCRIPTS = {
	"device-console": "device_management/page/device_console/device_console.js",
	"inventory-scanner-audit": "inventory_enhancements/page/inventory_scanner_audit/inventory_scanner_audit.js",
	"sales-pipeline": "crm_enhancements/page/sales_pipeline/sales_pipeline.js",
	"quickbooks-record-matching": "quickbooks_online/page/quickbooks_record_matching/quickbooks_record_matching.js",
	"training-review": "training/page/training_review/training_review.js",
	"location-timeline": "workforce/page/location_timeline/location_timeline.js",
	"maintenance-day-board": "sapphire_maintenance/page/maintenance_day_board/maintenance_day_board.js",
};

// Page code swallows a failed server call the way the Desk does; a rejection nobody handles is
// not this harness's business, and node would otherwise stop on it.
process.on("unhandledRejection", () => {});

// A `respond` handler returns this for a request that never reached the server.
const NETWORK_DOWN = Symbol("network down");

let failures = 0;
let checks = 0;
const tests = [];

function test(name, fn) {
	tests.push({ name, fn });
}

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

async function drainMicrotasks() {
	for (let i = 0; i < 12; i++) await new Promise((resolve) => setImmediate(resolve));
}

// ---------------------------------------------------------------------------- the fake Desk

function makeDesk(options) {
	const opts = options || {};
	const desk = {
		calls: [], // every jQuery method called: {label, method, args}
		handlers: [], // every .on()/.one(): {el, type, ns, sel, fn}
		dialogs: [],
		requests: [],
		alerts: [],
		messages: [],
		cameras: [],
		required: [], // every path frappe.require was asked for
		violations: [],
		respond: {}, // server method (last dotted part) -> (args) => message | Error | undefined
		cameraAuto: "allow",
		nextCode: null,
		left: false,
		scrolls: 0,
		fullscreen: { asked: 0, exited: 0 },
		loaded: {},
	};

	// ---- clock
	const timers = new Map();
	let timerSeq = 0;
	let now = 0;
	function setTimeout_(fn, ms) {
		const id = ++timerSeq;
		timers.set(id, { id, at: now + Math.max(0, Number(ms) || 0), fn });
		return id;
	}
	function clearTimeout_(id) {
		timers.delete(id);
	}
	// A repeating timer on the same clock; its ids are its own, apart from the timeouts'.
	const intervals = new Map();
	let intervalSeq = 1000000;
	function setInterval_(fn, ms) {
		const id = ++intervalSeq;
		const tick = () => {
			if (!intervals.has(id)) return;
			intervals.set(id, setTimeout_(tick, ms));
			fn();
		};
		intervals.set(id, setTimeout_(tick, ms));
		return id;
	}
	function clearInterval_(id) {
		if (intervals.has(id)) clearTimeout_(intervals.get(id));
		intervals.delete(id);
	}
	desk.liveIntervals = () => intervals.size;
	desk.today = "2026-09-24";
	desk.wait = async function (ms) {
		const end = now + (ms || 0);
		for (;;) {
			await drainMicrotasks();
			let next = null;
			for (const t of timers.values()) {
				if (t.at <= end && (!next || t.at < next.at || (t.at === next.at && t.id < next.id))) next = t;
			}
			if (!next) break;
			timers.delete(next.id);
			now = next.at;
			next.fn();
		}
		now = end;
		await drainMicrotasks();
	};
	// Long enough for set_route's 100 ms, a modal's 300 ms fade and a page's own timers.
	desk.settle = () => desk.wait(2000);

	// ---- elements and a jQuery that records what it is asked to do
	function makeElement(label, extra) {
		return Object.assign(
			{
				__label: label,
				__events: [],
				__classes: new Set(),
				__attrs: {},
				__data: {},
				__props: {},
				style: {},
				dataset: {},
				readyState: 4,
				videoWidth: 640,
				videoHeight: 480,
				play: () => Promise.resolve(),
				focus() {
					desk.calls.push({ label, method: "focus", args: [] });
				},
				scrollIntoView() {},
				// Question Review's editor puts the caret at the end of the stem it opens on.
				value: "",
				setSelectionRange() {},
				// The real sf_kit (makeDesk({ kit: true })) writes its state panel into innerHTML,
				// then looks up the panel's one action button in it: a fresh button per panel, whose
				// click listeners the test calls.
				querySelector(sel) {
					const html = String(this.innerHTML || "");
					if (sel !== ".sf-state-action" || !html.includes("sf-state-action")) return null;
					if (this.__actionFor !== html) {
						const button = { clicks: [], addEventListener: (type, fn) => type === "click" && button.clicks.push(fn) };
						this.__action = button;
						this.__actionFor = html;
					}
					return this.__action;
				},
			},
			extra || {}
		);
	}

	function record(el, method, args) {
		desk.calls.push({ label: el.__label, method, args });
	}

	function jq(el) {
		if (el.__jq) return el.__jq;
		const self = new Proxy(function () {}, {
			get(target, key) {
				if (key === "then" || typeof key === "symbol") {
					if (key === Symbol.iterator) return function* () { yield el; };
					return undefined;
				}
				if (key === "__jq") return self;
				if (key === "__label") return el.__label;
				if (key === "length") return 1;
				if (/^\d+$/.test(key)) return el;
				switch (key) {
					case "find":
					case "closest":
					case "children":
					case "parent":
					case "parents":
					case "siblings":
					case "next":
					case "prev":
						return (sel) => {
							record(el, key, [sel]);
							return jq(makeElement(typeof sel === "string" ? sel : key));
						};
					case "on":
					case "one":
						return (types, ...rest) => {
							const fn = rest.filter((a) => typeof a === "function").pop();
							const sel = typeof rest[0] === "string" ? rest[0] : null;
							String(types)
								.split(/\s+/)
								.forEach((type) => {
									const dot = type.indexOf(".");
									const h = {
										el,
										label: el.__label,
										type: dot < 0 ? type : type.slice(0, dot),
										ns: dot < 0 ? null : type.slice(dot + 1),
										sel,
										fn,
										once: key === "one",
									};
									el.__events.push(h);
									desk.handlers.push(h);
								});
							return self;
						};
					case "off":
						return (types) => {
							if (!types) {
								el.__events = [];
								return self;
							}
							String(types)
								.split(/\s+/)
								.forEach((type) => {
									const dot = type.indexOf(".");
									const base = dot < 0 ? type : type.slice(0, dot);
									const ns = dot < 0 ? null : type.slice(dot + 1);
									el.__events = el.__events.filter(
										(h) => !((!base || h.type === base) && (!ns || h.ns === ns))
									);
								});
							return self;
						};
					case "trigger":
						return (type, data) => {
							const base = String(type).split(".")[0];
							const hs = el.__events.filter((h) => h.type === base && !h.sel);
							el.__events = el.__events.filter((h) => !(h.once && hs.includes(h)));
							const event = { type: base, currentTarget: el, target: el, preventDefault() {}, stopPropagation() {} };
							hs.forEach((h) => h.fn.call(el, event, data));
							return self;
						};
					case "toggleClass":
					case "addClass":
					case "removeClass":
						return (cls, on) => {
							record(el, key, [cls, on]);
							String(cls || "")
								.split(/\s+/)
								.filter(Boolean)
								.forEach((c) => {
									let want = key === "addClass";
									if (key === "toggleClass") want = on === undefined ? !el.__classes.has(c) : !!on;
									if (want) el.__classes.add(c);
									else el.__classes.delete(c);
								});
							return self;
						};
					case "hasClass":
						return (c) => el.__classes.has(c);
					case "attr":
						return (name, value) => {
							if (typeof name === "string" && value === undefined) return el.__attrs[name];
							record(el, key, [name, value]);
							if (typeof name === "object") Object.assign(el.__attrs, name);
							else el.__attrs[name] = value;
							return self;
						};
					case "removeAttr":
						return (name) => {
							record(el, key, [name]);
							delete el.__attrs[name];
							return self;
						};
					case "data":
						return (k, v) => {
							if (typeof k === "string" && v === undefined) {
								return k in el.__data ? el.__data[k] : el.__attrs["data-" + k];
							}
							el.__data[k] = v;
							return self;
						};
					case "prop":
						return (k, v) => {
							if (typeof k === "string" && v === undefined) return el.__props[k] || false;
							record(el, key, [k, v]);
							if (typeof k === "object") Object.assign(el.__props, k);
							else el.__props[k] = v;
							return self;
						};
					case "val":
						return (...a) => {
							if (!a.length) return el.__val == null ? "" : el.__val;
							record(el, key, a);
							el.__val = a[0];
							return self;
						};
					case "text":
					case "html":
						return (...a) => {
							if (!a.length) return "";
							record(el, key, a);
							return self;
						};
					case "is":
						return () => false;
					case "get":
						return () => el;
					case "toArray":
						return () => [el];
					case "index":
					case "width":
					case "height":
					case "outerHeight":
					case "outerWidth":
					case "scrollTop":
						return (...a) => (a.length ? self : 0);
					default:
						return (...args) => {
							record(el, key, args);
							return self;
						};
				}
			},
			apply() {
				return self;
			},
		});
		el.__jq = self;
		return self;
	}

	const body = makeElement("body");
	const docListeners = {};
	const document = makeElement("document", {
		hidden: false,
		visibilityState: "visible",
		fullscreenElement: null,
		head: makeElement("head"),
		body,
		getElementById: () => null,
		createElement: (tag) =>
			makeElement(
				tag,
				tag === "canvas"
					? { getContext: () => ({ drawImage() {}, getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }) }) }
					: {}
			),
		createTextNode: (text) => makeElement("#text", { textContent: String(text) }),
		addEventListener(type, fn) {
			(docListeners[type] = docListeners[type] || []).push(fn);
		},
		removeEventListener(type, fn) {
			docListeners[type] = (docListeners[type] || []).filter((f) => f !== fn);
		},
		exitFullscreen() {
			desk.fullscreen.exited += 1;
			document.fullscreenElement = null;
			setTimeout_(() => desk.fire("fullscreenchange"), 0);
			return Promise.resolve();
		},
	});
	document.documentElement = makeElement("html", {
		getAttribute: () => "light",
		requestFullscreen() {
			desk.fullscreen.asked += 1;
			document.fullscreenElement = document.documentElement;
			setTimeout_(() => desk.fire("fullscreenchange"), 0);
			return Promise.resolve();
		},
	});
	desk.fire = (type) => (docListeners[type] || []).slice().forEach((fn) => fn({ type }));
	desk.document = document;
	desk.body = body;

	function $(x) {
		if (x && (typeof x === "object" || typeof x === "function") && x.__jq) return x.__jq;
		if (x === "body") return jq(body);
		if (typeof x === "string") {
			const s = x.trim();
			return jq(makeElement(s.charAt(0) === "<" ? "html:" + s.slice(0, 80) : s));
		}
		if (x && typeof x === "object") {
			// An element the test made (an event's currentTarget): keep what it carries.
			if (!x.__events) Object.assign(x, makeElement(x.__label || "object"), Object.assign({}, x));
			return jq(x);
		}
		return jq(makeElement("?"));
	}
	desk.$ = $;
	desk.makeElement = makeElement;

	// ---- session history
	const entries = [];
	let index = -1;
	let activation = false;
	const history = {
		scrollRestoration: "auto",
		get state() {
			return entries[index].state;
		},
		get length() {
			return entries.length;
		},
		pushState(state, title, url) {
			if (!activation) desk.violations.push(`pushState ${url} with no tap since the last entry`);
			activation = false;
			entries.splice(index + 1);
			entries.push({ url: url == null ? entries[index].url : String(url), state });
			index = entries.length - 1;
		},
		replaceState(state, title, url) {
			entries[index] = { url: url == null ? entries[index].url : String(url), state };
		},
		back() {
			setTimeout_(() => traverse(-1), 0);
		},
		forward() {
			setTimeout_(() => traverse(1), 0);
		},
	};
	function traverse(delta) {
		const to = index + delta;
		if (to < 0) {
			desk.left = true;
			return;
		}
		if (to >= entries.length) return;
		index = to;
		activation = false;
		router.route(); // v16's popstate listener
	}
	const location = {
		get pathname() {
			return entries[index].url.split("?")[0];
		},
		get search() {
			const q = entries[index].url.split("?")[1];
			return q ? "?" + q : "";
		},
		get href() {
			return "https://erp.test" + entries[index].url;
		},
		hostname: "erp.test",
	};
	desk.history = history;
	desk.at = () => ({ index, length: entries.length, url: entries[index].url });
	desk.urls = () => entries.map((e) => e.url);

	// ---- dialogs (Bootstrap 4.6 modal with fade, frappe.ui.Dialog's own handlers)
	const open_dialogs = [];
	class Dialog {
		constructor(o) {
			this.opts = o || {};
			this.title = this.opts.title || "";
			this.$body = jq(makeElement("dialog-body:" + this.title));
			this.$wrapper = jq(makeElement("dialog:" + this.title));
			this.display = false;
			this.is_visible = false;
			this.shown_ = false; // Bootstrap's `_isShown`: set as show() starts, cleared as hide() does
			this.fading = false;
			const dialog = this;
			this.$wrapper.data("bs.modal", {
				get _isShown() {
					return dialog.shown_;
				},
			});
			desk.dialogs.push(this);
		}
		show() {
			this.is_visible = true;
			if (this.shown_ || this.fading) return this;
			this.shown_ = true;
			this.fading = true;
			setTimeout_(() => {
				this.fading = false;
				this.display = true;
				ctx.cur_dialog = this;
				open_dialogs.push(this);
				this.$wrapper.trigger("shown.bs.modal");
			}, 300);
			return this;
		}
		// frappe's hide(): Bootstrap's hide, then `is_visible` cleared.
		hide() {
			this.dismiss();
			this.is_visible = false;
		}
		// Bootstrap's hide alone: what the X's `data-dismiss="modal"` runs, with no Dialog.hide().
		dismiss() {
			if (!this.shown_ || this.fading) return; // Bootstrap: not shown, or still transitioning
			this.shown_ = false;
			this.display = false;
			if (open_dialogs[open_dialogs.length - 1] === this) {
				open_dialogs.pop();
				ctx.cur_dialog = open_dialogs.length ? open_dialogs[open_dialogs.length - 1] : null;
			}
			if (this.onhide) this.onhide();
			if (this.on_hide) this.on_hide();
			// Bootstrap's "hidden" comes once the fade-out is over; frappe's onhide runs on "hide".
			setTimeout_(() => this.$wrapper.trigger("hidden.bs.modal"), 300);
		}
		set_primary_action(label, fn) {
			this.primary = fn;
		}
		get_values() {
			return this.values || {};
		}
	}
	desk.shown = () => desk.dialogs.filter((d) => d.display).map((d) => d.title);
	desk.dialog = (title) => desk.dialogs.filter((d) => d.title === title && d.display).pop() || null;
	// The dialog's X, as a person closes it: Bootstrap's data-dismiss, so `is_visible` stays set.
	desk.closeDialog = (title) => desk.dialog(title).dismiss();

	// ---- server calls
	// `quiet`: the real kit's quiet call (a fetch), which frappe's after_ajax does not wait on.
	function server(method, args, quiet) {
		const req = { method: method.split(".").pop(), args: args || {}, settled: false, quiet: !!quiet };
		req.promise = new Promise((resolve, reject) => {
			req.resolve = (m) => {
				if (req.settled) return;
				req.settled = true;
				resolve(m);
				flushAjax();
			};
			req.reject = (e) => {
				if (req.settled) return;
				req.settled = true;
				reject(e || {});
				flushAjax();
			};
		});
		desk.requests.push(req);
		const answer = desk.respond[req.method];
		if (answer) {
			setTimeout_(() => {
				const m = answer(req.args);
				if (m === NETWORK_DOWN) req.reject({ status: 0 });
				else if (m instanceof Error) req.reject(m);
				else if (m !== undefined) req.resolve(m);
			}, 5);
		}
		return req;
	}
	desk.sent = (method) => desk.requests.filter((r) => r.method === method);
	// The real kit's quiet path (makeDesk({ kit: true })): a fetch to /api/method/<method>, answered
	// by `respond` like any request. A reply() rejection is that HTTP answer; anything else that
	// fails is a fetch that never reached the server.
	function fetch_(url) {
		const method = decodeURIComponent(String(url).split("/api/method/")[1].split("?")[0]);
		return server(method, {}, true).promise.then(
			(m) => ({ status: 200, text: () => Promise.resolve(JSON.stringify({ message: m })) }),
			(e) => {
				if (!e || !e.status) throw new TypeError("Failed to fetch");
				return { status: e.status, text: () => Promise.resolve(JSON.stringify(e.body || {})) };
			}
		);
	}
	// request.js's waiting_for_ajax: run once no request is in flight (see `after_ajax`).
	const ajaxWaiting = [];
	const inflight = () => desk.requests.some((r) => !r.settled && !r.quiet);
	function flushAjax() {
		if (inflight()) return;
		ajaxWaiting.splice(0).forEach((fn) => fn());
	}

	// ---- frappe
	const router = {
		current_route: null,
		async route() {
			this.current_sub_path = this.get_sub_path();
			this.current_route = await this.parse();
			this.set_history();
			this.render();
			this.trigger("change");
		},
		async parse() {
			const route = this.get_sub_path().split("/");
			this.set_route_options_from_url();
			return route;
		},
		strip_prefix(route) {
			if (route.charAt(0) === "/") route = route.slice(1);
			if (route === "desk") route = "";
			if (route.startsWith("desk/")) route = route.slice(5);
			return route;
		},
		get_sub_path() {
			return this.strip_prefix(location.pathname)
				.split("/")
				.map((c) => decodeURIComponent(c))
				.join("/");
		},
		set_route_options_from_url() {
			if (!frappe.route_options) frappe.route_options = {};
			new URLSearchParams(location.search).forEach((value, key) => {
				frappe.route_options[key] = value;
			});
		},
		set_history() {
			frappe.route_history.push(this.current_route);
			frappe.ui.hide_open_dialog();
		},
		render() {
			showPage(this.current_route[0] || "home");
		},
		set_route(...args) {
			let route = args;
			return new Promise((resolve) => {
				if (route.length === 1 && Array.isArray(route[0])) route = route[0];
				if (route.length === 1 && typeof route[0] === "string" && route[0].includes("/")) {
					let given = route[0];
					// v16.50+ (`queryInUrl`): "/desk/a/b?x=1" puts x into route_options.
					const q = given.indexOf("?");
					if (opts.queryInUrl && q >= 0) {
						frappe.route_options = frappe.route_options || {};
						new URLSearchParams(given.slice(q + 1)).forEach((v, k) => (frappe.route_options[k] = v));
						given = given.slice(0, q);
					}
					route = given.split("/").map((c) => decodeURIComponent(c));
				}
				if (route[0] === "") route.shift();
				if (route[0] === "desk" || route[0] === "app") route.shift();
				const view = route[0] ? String(route[0]).toLowerCase() : "";
				if (view === "list") {
					if (route[2] && typeof route[2] === "object") frappe.route_options = route[2];
					route = [slug(route[1])];
				} else if (view === "form") {
					route = [slug(route[1])].concat(route[2] ? [route[2]] : []);
				}
				const parts = [];
				route.forEach((a) => {
					if (a && typeof a === "object") frappe.route_options = a;
					else parts.push(encodeURIComponent(String(a)));
				});
				const sub_path = parts.length ? "/desk/" + parts.join("/") : "/desk";
				const route_options = frappe.route_options || {};
				// Before v16.50 every value was JSON-encoded, quotes and all, and the query only decided
				// whether to push; from v16.50 (frappe PR #40040) a plain value is written as itself
				// and the query goes into the address with the path.
				const encode = (v) => (opts.queryInUrl && (v === null || typeof v !== "object") ? String(v) : JSON.stringify(v));
				const query = Object.entries(route_options)
					.map(([k, v]) => `${k}=` + encodeURIComponent(encode(v)))
					.join("&");
				this.push_state(sub_path, query ? `?${query}` : "");
				setTimeout_(() => frappe.after_ajax(() => resolve()), 100);
			}).finally(() => {
				frappe.route_flags = {};
			});
		},
		push_state(path, query) {
			if (location.pathname !== path || location.search !== (query || "")) {
				const method = frappe.route_flags.replace_route ? "replaceState" : "pushState";
				history[method](null, null, opts.queryInUrl ? path + (query || "") : path);
				this.route();
			}
		},
		listeners: {},
		on(evt, fn) {
			(this.listeners[evt] = this.listeners[evt] || []).push({ fn, once: false });
		},
		once(evt, fn) {
			(this.listeners[evt] = this.listeners[evt] || []).push({ fn, once: true });
		},
		trigger(evt, data) {
			const ls = this.listeners[evt] || [];
			this.listeners[evt] = ls.filter((l) => !l.once);
			ls.forEach((l) => l.fn(data));
		},
	};

	function slug(name) {
		return String(name || "").toLowerCase().replace(/ /g, "-");
	}

	function escape_html(s) {
		return String(s == null ? "" : s)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;")
			.replace(/'/g, "&#39;");
	}

	function makeField(df) {
		return {
			df: df || {},
			$wrapper: jq(makeElement("field:" + ((df && df.fieldname) || "?"))),
			value: (df && df.default) || "",
			get_value() {
				return this.value;
			},
			set_value(v) {
				this.value = v;
				return Promise.resolve();
			},
			set_input(v) {
				this.value = v;
			},
			set_data() {},
			refresh() {},
		};
	}

	function makeAppPage() {
		const page = {
			body: makeElement("page-body"),
			main: makeElement("page-main"),
			sidebar: makeElement("page-sidebar"),
			buttons: {},
			menu: {},
			set_title() {},
			set_indicator() {},
			clear_indicator() {},
			set_secondary_action(label, fn) {
				page.buttons[label] = fn;
			},
			set_primary_action(label, fn) {
				page.buttons[label] = fn;
			},
			add_inner_button(label, fn) {
				page.buttons[label] = fn;
			},
			add_menu_item(label, fn) {
				page.menu[label] = fn;
			},
			add_action_item(label, fn) {
				page.menu[label] = fn;
			},
			add_field(df) {
				return makeField(df);
			},
		};
		desk.page = page;
		return page;
	}
	desk.makeAppPage = makeAppPage;
	desk.makeField = makeField;

	const frappe = {
		app: true,
		pages: {},
		route_options: null,
		route_flags: {},
		route_history: [],
		boot: { ee_process_automation: 0, versions: {} },
		router,
		get_route: () => router.current_route,
		set_route: (...a) => router.set_route(...a),
		after_ajax(fn) {
			if (opts.ajax && inflight()) ajaxWaiting.push(fn);
			else fn();
		},
		provide(ns) {
			let o = ctx;
			ns.split(".").forEach((part) => {
				o[part] = o[part] || {};
				o = o[part];
			});
			return o;
		},
		ui: {
			Dialog,
			make_app_page: makeAppPage,
			hide_open_dialog() {
				if (ctx.cur_dialog) ctx.cur_dialog.hide();
			},
			form: { make_control: () => makeField({}) },
		},
		call(o) {
			const req = server(o.method, o.args);
			return req.promise.then(
				(message) => {
					const r = { message };
					if (o.callback) o.callback(r);
					return r;
				},
				(err) => {
					if (o.error) o.error(err);
					// request.js puts the server's refusal up as a message. A request that never
					// reached the server has no status handler there, so no dialog is put up.
					if (!(err && err.status === 0)) {
						frappe.msgprint(__("The server refused: {0}", [(err && err.message) || "error"]));
					}
					throw err;
				}
			);
		},
		xcall(method, args) {
			return server(method, args).promise;
		},
		confirm(message, yes, no) {
			const d = new Dialog({ title: "Confirm" });
			d.message = message;
			d.answer = (ok) => {
				if (ok) {
					d.primary_action_fulfilled = true;
					if (yes) yes();
				}
				d.hide();
			};
			d.show();
			if (no) {
				d.onhide = () => {
					if (!d.primary_action_fulfilled) no();
				};
			}
			return d;
		},
		prompt(fields, callback, title) {
			const d = new Dialog({ title: title || "Prompt" });
			d.submit = (values) => {
				d.hide();
				callback(values);
			};
			d.show();
			return d;
		},
		msgprint(m) {
			const message = typeof m === "object" ? m.message : m;
			desk.messages.push(message);
			// messages.js makes its dialog once, keeps it here and shows that one every time; a
			// refusal is up in it before the call rejects.
			if (!frappe.msg_dialog) frappe.msg_dialog = new Dialog({ title: "Message" });
			const d = frappe.msg_dialog;
			d.message = message;
			d.show();
			return d;
		},
		show_alert(m) {
			desk.alerts.push(typeof m === "object" ? m.message : m);
		},
		utils: {
			escape_html,
			debounce: (fn) => fn,
			scroll_to() {},
			add_link_title() {},
			get_form_link(doctype, name) {
				return `/desk/${encodeURIComponent(slug(doctype))}/${encodeURIComponent(name)}`;
			},
		},
		datetime: {
			// The clock's date; a test moves it past midnight by setting desk.today.
			get_today: () => desk.today,
			now_time: () => "12:00",
			str_to_user: (s) => s,
		},
		// The scanner kit arrives on request (unless the Desk is made with { scanner: false }, a file
		// that failed to load, or { scannerLate: true }, which holds it until desk.scannerArrives()),
		// and so does the vendored jsQR, as window.jsQR. v16 runs the callback either way.
		require(paths, cb) {
			const list = [].concat(paths);
			desk.required.push(...list);
			if (list.includes("scanner_kit.bundle.js")) {
				if (opts.scannerLate) {
					desk.scannerArrives = () => {
						scannerKit.install(ctx);
						if (typeof cb === "function") cb();
					};
					return new Promise(() => {});
				}
				// { scannerThrows: true }: a kit that arrives but throws as a page starts it.
				if (opts.scannerThrows) {
					ctx.sf_scanner = {
						version: 1,
						sheets() {
							throw new Error("scanner kit broke");
						},
						camera: { available: () => true },
					};
				} else if (opts.scanner !== false) scannerKit.install(ctx);
			}
			if (list.includes("reorder_levels.bundle.js") && opts.reorder) {
				const install = () => {
					ctx.sf_reorder = {
						version: 1,
						ReorderEditor: reorderMods.editor.ReorderEditor,
						cell_html: reorderMods.editor.cell_html,
						bind: (root, editor, bindOpts) => {
							desk.reorderBind = { root, editor, opts: bindOpts };
						},
						paint: () => {},
						read_boxes: () => ({ level: "", qty: "" }),
						logic: reorderMods.logic,
					};
				};
				if (opts.reorderLate) {
					desk.reorderArrives = () => {
						install();
						if (typeof cb === "function") cb();
					};
					return new Promise(() => {});
				}
				install();
			}
			if (list.includes(JSQR_PATH)) ctx.jsQR = jsQR;
			if (typeof cb === "function") cb();
			return Promise.resolve();
		},
		realtime: { on() {}, off() {} },
		new_doc(doctype, values) {
			desk.newDoc = [doctype, values];
			return frappe.set_route(slug(doctype), "new");
		},
		user: { has_role: () => true, full_name: (u) => u },
		dom: { freeze() {}, unfreeze() {} },
		request: { cleanup() {} },
		model: {},
	};

	function __(s, args) {
		let out = String(s);
		(args || []).forEach((a, i) => {
			out = out.split(`{${i}}`).join(String(a));
		});
		return out;
	}

	// An iPhone's decoder (makeDesk({ iphone: true }): no BarcodeDetector): jsQR reads what the
	// camera shows, the code the test put in front of it.
	function jsQR() {
		const code = desk.nextCode;
		if (!code) return null;
		desk.nextCode = null;
		return { data: code };
	}

	class BarcodeDetector {
		static getSupportedFormats() {
			return Promise.resolve(["qr_code", "code_128"]);
		}
		detect() {
			const code = desk.nextCode;
			if (code) {
				desk.nextCode = null;
				return Promise.resolve([{ rawValue: code }]);
			}
			return Promise.resolve([]);
		}
	}

	const navigator = {
		mediaDevices: {
			getUserMedia(constraints) {
				const req = { constraints, stream: null };
				req.promise = new Promise((resolve, reject) => {
					req.allow = () => {
						req.stream = { stopped: 0, getTracks: () => [{ stop: () => (req.stream.stopped += 1) }] };
						resolve(req.stream);
					};
					// A bare Error by default; with a name, getUserMedia's own DOMException name.
					req.deny = (name) => {
						const err = new Error(name || "NotAllowedError");
						if (name) err.name = name;
						reject(err);
					};
				});
				desk.cameras.push(req);
				if (desk.cameraAuto === "allow") setTimeout_(() => req.allow(), 10);
				if (desk.cameraAuto === "deny") setTimeout_(() => req.deny(), 10);
				if (desk.cameraAuto === "blocked") setTimeout_(() => req.deny("NotAllowedError"), 10);
				if (desk.cameraAuto === "none") setTimeout_(() => req.deny("NotFoundError"), 10);
				return req.promise;
			},
		},
	};
	desk.cameraLive = () => desk.cameras.filter((c) => c.stream && !c.stream.stopped).length;

	const store = {};
	const ctx = {
		console,
		URLSearchParams,
		setTimeout: setTimeout_,
		clearTimeout: clearTimeout_,
		// Intervals are opt-in (`makeDesk({ intervals: true })`): a page's poll then runs on this clock.
		setInterval: opts.intervals ? setInterval_ : () => 0,
		clearInterval: opts.intervals ? clearInterval_ : () => {},
		$,
		jQuery: $,
		frappe,
		__,
		document,
		history,
		location,
		navigator,
		cur_dialog: null,
		BarcodeDetector,
		MutationObserver: class {
			observe() {}
		},
		localStorage: {
			getItem: (k) => (k in store ? store[k] : null),
			setItem: (k, v) => (store[k] = String(v)),
			removeItem: (k) => delete store[k],
		},
		google: { maps: { event: { trigger() {} } } },
		format_currency: (v) => String(v),
		format_number: (v) => String(v),
		flt: (v) => parseFloat(v) || 0,
		cint: (v) => parseInt(v, 10) || 0,
	};
	if (opts.kit) ctx.fetch = fetch_;
	if (opts.iphone) delete ctx.BarcodeDetector;
	ctx.window = ctx;
	vm.createContext(ctx);
	// The global bundle's sf_kit loader (window.sf_kit_ready), as the Desk boots with it. With no
	// window.sf_kit after frappe.require (opts.kit unset), pages get its plain stand-in.
	vm.runInContext(fs.readFileSync(LOADER, "utf8"), ctx, { filename: LOADER });
	desk.ctx = ctx;
	desk.frappe = frappe;
	// The real sf_kit.bundle.js, installed on this page before it loads (a fresh copy of the module
	// per Desk, so it installs again): frappe.require then finds window.sf_kit, as on a live Desk.
	desk.installKit = async function () {
		globalThis.window = ctx;
		globalThis.document = document;
		await import(pathToFileURL(SF_BUNDLE).href + `?desk=${++kitCopies}`);
	};

	// ---- pages: views/pageview.js + views/container.js
	let current = null;
	function showPage(name) {
		let wrapper = frappe.pages[name];
		if (!wrapper || !wrapper.__desk_page) {
			wrapper = makeElement("page:" + name, { __desk_page: true });
			frappe.pages[name] = wrapper;
			if (SCRIPTS[name]) {
				const file = path.join(APP, SCRIPTS[name]);
				vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
				desk.loaded[name] = true;
			}
			if (opts.onLoad && opts.onLoad[name]) wrapper.on_page_load = (w) => opts.onLoad[name](w, desk);
			if (wrapper.on_page_load) wrapper.on_page_load(wrapper);
			jq(wrapper).on("show", () => {
				if (wrapper.on_page_show) wrapper.on_page_show(wrapper);
				if (wrapper.refresh) wrapper.refresh(wrapper);
			});
		}
		const cd = ctx.cur_dialog;
		if (cd && cd.display && !cd.keep_open) cd.hide();
		if (current && current !== wrapper) jq(current).trigger("hide");
		current = wrapper;
		jq(wrapper).trigger("show");
		desk.scrolls += 1;
	}
	desk.current = () => (current ? current.__label.replace(/^page:/, "") : null);
	desk.wrapper = (name) => frappe.pages[name];

	// ---- what a person does
	desk.load = async function (url) {
		await desk.restore([{ url, state: null }], 0);
	};
	desk.restore = async function (list, at) {
		entries.length = 0;
		list.forEach((e) => entries.push(e));
		index = at;
		router.route();
		await desk.settle();
	};
	// Pull-to-refresh, or Android bringing back a discarded tab: a new Desk, its page scripts run
	// afresh, over the same session history. The browser keeps each entry's URL and its
	// history.state (a structured clone) across it; nothing else survives. Use the desk this
	// returns from here on.
	desk.reload = async function () {
		const next = makeDesk(opts);
		Object.assign(next.respond, desk.respond);
		next.cameraAuto = desk.cameraAuto;
		const kept = entries.map((e) => ({ url: e.url, state: e.state == null ? null : JSON.parse(JSON.stringify(e.state)) }));
		await next.restore(kept, index);
		return next;
	};
	desk.tap = async function (fn) {
		desk.press(fn);
		await desk.settle();
	};
	// A tap, and nothing waited for: for what happens inside a route change or a fade.
	desk.press = function (fn) {
		activation = true;
		fn();
	};
	desk.key = desk.tap; // a key press is a user activation too
	desk.back = async function () {
		history.back();
		await desk.settle();
	};
	desk.forward = async function () {
		history.forward();
		await desk.settle();
	};
	desk.route = () => (router.current_route || []).join("/");
	desk.focused = (label) => desk.calls.filter((c) => c.label === label && c.method === "focus").length;
	desk.handler = (type, sel) => {
		const live = desk.handlers.filter((h) => h.type === type && h.sel === sel && h.el.__events.includes(h));
		return live.length ? live[live.length - 1].fn : null;
	};
	return desk;
}

// Hold a server method's replies until the function this returns lets them land.
function hold(desk, method) {
	const answer = desk.respond[method];
	delete desk.respond[method];
	return () => {
		desk.respond[method] = answer;
		desk.requests.filter((r) => r.method === method && !r.settled).forEach((r) => r.resolve(answer(r.args)));
	};
}

// ---------------------------------------------------------------------------- Device Console

async function deviceConsole(extra) {
	const desk = makeDesk();
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 1, counts: {} });
	desk.respond.resolve_device_scan = ({ code }) =>
		code.startsWith("DEV")
			? { type: "device", device: { name: code, device_name: "Phone " + code, status: "In Stock" } }
			: { type: "unknown" };
	desk.respond.check_out = (args) => ({ name: args.device, status: "Assigned", assigned_to_employee: args.employee });
	Object.assign(desk.respond, extra || {});
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("device-console"));
	return desk;
}

const dc = (desk) => desk.wrapper("device-console").device_console;

// The console on a Desk made with `opts` (iphone, scanner, scannerLate), reached from Home.
async function deviceConsoleWith(opts) {
	const desk = makeDesk(opts);
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 1, counts: {} });
	desk.respond.resolve_device_scan = ({ code }) =>
		code.startsWith("DEV")
			? { type: "device", device: { name: code, device_name: "Phone " + code, status: "In Stock" } }
			: { type: "unknown" };
	desk.respond.check_out = (args) => ({ name: args.device, status: "Assigned", assigned_to_employee: args.employee });
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("device-console"));
	return desk;
}

// The last show/hide the console's camera button was given.
const camButton = (desk, label) => {
	const toggles = desk.calls.filter((c) => c.label === label && c.method === "toggle");
	return toggles.length ? toggles[toggles.length - 1].args[0] : undefined;
};

test("Device Console: Back closes the camera and stays; Forward opens it again", async () => {
	const desk = await deviceConsole();
	check("on the console", [desk.current(), desk.route()], ["device-console", "device-console"]);
	const before = desk.focused(".dc-scan");
	await desk.tap(() => dc(desk).openCamera());
	check("the camera is its own entry", desk.route(), "device-console/camera");
	check("the sheet is up once the route settled", desk.shown(), ["Camera Scan"]);
	check("the camera started after the sheet", desk.cameraLive(), 1);
	await desk.back();
	check("Back closes the sheet …", desk.shown(), []);
	check("… stops the camera …", desk.cameraLive(), 0);
	check("… and stays on the console", [desk.current(), desk.route()], ["device-console", "device-console"]);
	check("closing a sheet does not pull focus (and a phone's keyboard) up", desk.focused(".dc-scan"), before);
	await desk.forward();
	check("Forward opens the camera again on its entry", [desk.route(), desk.shown()], ["device-console/camera", ["Camera Scan"]]);
	check("… and nothing was pushed for it", desk.at().length, 3);
	await desk.back();
	await desk.back();
	check("then Back leaves the console", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Device Console: a camera read steps back off the sheet, then shows the device", async () => {
	const desk = await deviceConsole();
	await desk.tap(() => dc(desk).openCamera());
	desk.nextCode = "DEV-7";
	await desk.settle();
	check("the read closed the sheet and stepped back off its entry", [desk.shown(), desk.route(), desk.at().index], [[], "device-console", 1]);
	check("the scan was looked up once the console was back on its entry", desk.sent("resolve_device_scan").length, 1);
	check("the device is on the card", dc(desk).state.device && dc(desk).state.device.name, "DEV-7");
	check("the camera is off", desk.cameraLive(), 0);
	await desk.back();
	check("a scan is not an entry: Back leaves the console", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Device Console: an unknown read's enroll prompt survives the step back", async () => {
	const desk = await deviceConsole();
	await desk.tap(() => dc(desk).openCamera());
	desk.nextCode = "MYSTERY";
	await desk.settle();
	check("the enroll prompt is up and stays up", desk.shown(), ["Confirm"]);
	check("… on the console's own entry", desk.route(), "device-console");
});

test("Device Console: X closes the camera and leaves no dead entry", async () => {
	const desk = await deviceConsole();
	await desk.tap(() => dc(desk).openCamera());
	desk.closeDialog("Camera Scan");
	await desk.settle();
	check("closed, back on the console's entry", [desk.shown(), desk.route(), desk.at().index], [[], "device-console", 1]);
	await desk.back();
	check("one Back leaves the console", desk.current(), "home");
});

test("Device Console: Back while the camera is still fading in closes it once it is shown", async () => {
	const desk = await deviceConsole();
	desk.cameraAuto = "manual";
	desk.press(() => dc(desk).openCamera());
	await desk.wait(150); // the route has settled (100 ms) and the sheet is fading in (300 ms)
	const d = desk.dialogs.filter((x) => x.title === "Camera Scan").pop();
	check("fading in: not cur_dialog yet, so the router cannot see it", [d.fading, d.display, desk.ctx.cur_dialog], [true, false, null]);
	desk.ctx.history.back();
	await desk.settle();
	check("closed once it was shown, and still on the console", [d.display, desk.route(), desk.current()], [false, "device-console", "device-console"]);
	desk.cameras.slice(-1)[0].allow(); // the permission prompt answered late
	await desk.settle();
	check("the camera allowed late is stopped", desk.cameraLive(), 0);
});

test("Device Console: Back pressed before the sheet's route settles never shows it", async () => {
	const desk = await deviceConsole();
	desk.press(() => dc(desk).openCamera());
	await desk.wait(40); // pushed, not yet resolved
	desk.ctx.history.back();
	await desk.settle();
	check("no sheet, no camera, on the console", [desk.shown(), desk.cameras.length, desk.route()], [[], 0, "device-console"]);
});

test("Device Console: a reload on the camera's URL opens the console, not the camera", async () => {
	const desk = makeDesk();
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 1, counts: {} });
	await desk.load("/desk/device-console/camera");
	check("the entry became the console", [desk.route(), desk.at()], ["device-console", { index: 0, length: 1, url: "/desk/device-console" }]);
	check("no camera, no sheet", [desk.cameras.length, desk.shown()], [0, []]);
	check("the scan box has focus, as on any first show", desk.focused(".dc-scan") > 0, true);
});

test("Device Console: a reload on the camera's own entry steps back onto the console, leaving no duplicate", async () => {
	let desk = await deviceConsole();
	await desk.tap(() => dc(desk).openCamera());
	check("the camera's entry is marked as the console's own", desk.ctx.history.state, { dc_sheet: "camera" });
	// A tab discarded with the camera open, before it could close (the kit closes the camera on
	// visibilitychange and pagehide, but a discard need not fire either).
	desk = await desk.reload();
	check("no camera nobody tapped for", [desk.shown(), desk.cameras.length], [[], 0]);
	check("stepped back onto the console's own entry, the camera's still ahead", [desk.route(), desk.at(), desk.urls()], [
		"device-console",
		{ index: 1, length: 3, url: "/desk/device-console" },
		["/desk/home", "/desk/device-console", "/desk/device-console/camera"],
	]);
	await desk.back();
	check("so one Back leaves the console", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Device Console: the camera tapped while the first show's replace waits on the bootstrap keeps the console's entry", async () => {
	// v16's set_route clears route_flags only once every request in flight has landed.
	const desk = makeDesk({ ajax: true });
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 1, counts: {} });
	await desk.load("/desk/home");
	const release = hold(desk, "get_console_bootstrap");
	// The awesome bar's frequently-visited link, with the console not yet opened this session.
	await desk.tap(() => desk.frappe.set_route("device-console/camera"));
	check("the first show made the entry the console", [desk.route(), desk.urls()], ["device-console", ["/desk/home", "/desk/device-console"]]);
	check("… and left no replace behind for the next route change", !!desk.frappe.route_flags.replace_route, false);
	await desk.tap(() => dc(desk).openCamera());
	release();
	await desk.settle();
	check("the camera got an entry of its own", [desk.route(), desk.shown(), desk.urls()], [
		"device-console/camera",
		["Camera Scan"],
		["/desk/home", "/desk/device-console", "/desk/device-console/camera"],
	]);
	await desk.back();
	check("Back from the camera stays on the console", [desk.current(), desk.route(), desk.shown()], ["device-console", "device-console", []]);
	check("no push without a tap", desk.violations, []);
});

// A failed start-up (v1.645.0): the page's own panel says why, and its Try again asks again in
// place. The harness has no sf_kit, so this is the plain stand-in each page falls back to.
const saidText = (desk, text) => desk.calls.some((c) => c.method === "text" && c.args[0] === text);
const tryAgain = (desk) =>
	desk.handlers.filter((h) => h.type === "click" && String(h.label).includes("btn btn-default btn-sm")).pop();

test("Device Console: a start-up that failed says so, and Try again boots it in place", async () => {
	const desk = makeDesk();
	desk.respond.get_console_bootstrap = () => NETWORK_DOWN;
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("device-console"));
	check("the panel says why", saidText(desk, "Could not load the Device Console."), true);
	const retry = tryAgain(desk);
	check("with Try again", !!retry, true);
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 1, counts: {} });
	await desk.tap(() => retry.fn({ preventDefault() {} }));
	check("asked again", desk.sent("get_console_bootstrap").length, 2);
	check("still the console's one entry", [desk.route(), desk.urls()], ["device-console", ["/desk/home", "/desk/device-console"]]);
	check("no push without a tap", desk.violations, []);
});

test("Device Console: Choose Employee is an entry; a pick checks out after the step back", async () => {
	const desk = await deviceConsole();
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	check("scanned; still one entry for the console", [desk.route(), desk.at().index], ["device-console", 1]);
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	check("the picker is its own entry", [desk.route(), desk.shown()], ["device-console/employee", ["Choose Employee"]]);
	await desk.back();
	check("Back closes the picker and stays", [desk.shown(), desk.route()], [[], "device-console"]);
	await desk.forward();
	check("Forward reopens it for the same device", [desk.shown(), desk.route()], [["Choose Employee"], "device-console/employee"]);
	const pick = desk.handler("click", ".dc-emp-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { emp: "EMP-9" } } }));
	check("the pick closed the sheet and stepped back off it", [desk.shown(), desk.route(), desk.at().index], [[], "device-console", 1]);
	check("check_out went with the picked employee", desk.sent("check_out").map((r) => r.args.employee), ["EMP-9"]);
	check("no push without a tap", desk.violations, []);
});

test("Device Console: Forward onto the picker after another scan steps back, leaving no duplicate", async () => {
	const desk = await deviceConsole();
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	await desk.back();
	await desk.key(() => dc(desk).handleScan("DEV-2"));
	await desk.forward();
	check("no picker for a device no longer on the card", desk.shown(), []);
	check("back on the console's own entry, the picker's still ahead", [desk.route(), desk.at()], ["device-console", { index: 1, length: 3, url: "/desk/device-console" }]);
	check("no second console entry", desk.urls(), ["/desk/home", "/desk/device-console", "/desk/device-console/employee"]);
	await desk.back();
	check("so one Back leaves the console", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Device Console: an unknown code whose reply lands after Back left the console prompts nothing there", async () => {
	const desk = await deviceConsole();
	const release = hold(desk, "resolve_device_scan");
	await desk.key(() => dc(desk).handleScan("MYSTERY"));
	await desk.back();
	check("Back left the console", desk.current(), "home");
	release();
	await desk.settle();
	check("no enroll prompt over the page they went to", [desk.current(), desk.shown()], ["home", []]);
	check("the code is reported instead", desk.alerts.slice(-1), ["No device matches “MYSTERY”."]);
	check("nothing routed", desk.urls(), ["/desk/home", "/desk/device-console"]);
});

test("Device Console: an unknown code whose reply lands under the picker leaves the picker's device alone", async () => {
	const desk = await deviceConsole();
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	const release = hold(desk, "resolve_device_scan");
	await desk.key(() => dc(desk).handleScan("MYSTERY"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	release();
	await desk.settle();
	check("the picker is still up, no prompt over it", desk.shown(), ["Choose Employee"]);
	check("the code is reported instead", desk.alerts.slice(-1), ["No device matches “MYSTERY”."]);
	const pick = desk.handler("click", ".dc-emp-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { emp: "EMP-9" } } }));
	check("the pick checks out the device the picker opened for", desk.sent("check_out").map((r) => r.args), [{ device: "DEV-1", employee: "EMP-9" }]);
});

test("Device Console: a scan landing while the picker is open does not change whose check-out it is", async () => {
	const desk = await deviceConsole();
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	const release = hold(desk, "resolve_device_scan");
	await desk.key(() => dc(desk).handleScan("DEV-2"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	release();
	await desk.settle();
	check("DEV-2's reply landed on the card", dc(desk).state.device.name, "DEV-2");
	const pick = desk.handler("click", ".dc-emp-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { emp: "EMP-9" } } }));
	check("the check-out is still DEV-1's", desk.sent("check_out").map((r) => r.args.device), ["DEV-1"]);
});

test("Device Console: a camera refused shows why, after the step back", async () => {
	const desk = await deviceConsole();
	desk.cameraAuto = "deny";
	await desk.tap(() => dc(desk).openCamera());
	check("the message is up, on the console's entry", [desk.shown(), desk.route()], [["Message"], "device-console"]);
	check("it says the camera", desk.messages.slice(-1), ["Could not access the camera."]);
});

// S6b (v1.666.0): the console's camera is the scanner kit's, the same as the count's.

test("Device Console: on an iPhone (no BarcodeDetector) the camera reads a QR code through jsQR", async () => {
	const desk = await deviceConsoleWith({ iphone: true });
	check("the camera button shows", camButton(desk, ".dc-cam"), true);
	check("jsQR is not loaded until the camera is opened", desk.required.filter((p) => p.includes("jsQR")), []);
	await desk.tap(() => dc(desk).openCamera());
	check("opening it loads the vendored jsQR, by its bare path", desk.required.filter((p) => p.includes("jsQR")), [
		"/assets/erpnext_enhancements/js/stock_scan/lib/jsQR-1.4.0.min.js",
	]);
	check("the camera is its own entry, and running", [desk.route(), desk.shown(), desk.cameraLive()], ["device-console/camera", ["Camera Scan"], 1]);
	check("the back camera, no microphone", desk.cameras[0].constraints, { video: { facingMode: "environment" }, audio: false });
	desk.nextCode = "DEV-7";
	await desk.settle();
	check("the read closed the sheet and stepped back off its entry", [desk.shown(), desk.route(), desk.at().index], [[], "device-console", 1]);
	check("the device is on the card", dc(desk).state.device && dc(desk).state.device.name, "DEV-7");
	check("the camera is off", desk.cameraLive(), 0);
	check("no push without a tap", desk.violations, []);
});

test("Device Console: where BarcodeDetector reads QR, jsQR is never loaded", async () => {
	const desk = await deviceConsole();
	await desk.tap(() => dc(desk).openCamera());
	desk.nextCode = "DEV-3";
	await desk.settle();
	check("read natively", dc(desk).state.device && dc(desk).state.device.name, "DEV-3");
	check("no jsQR", desk.required.filter((p) => p.includes("jsQR")), []);
});

test("Device Console: another Desk page shown while the camera fades in stops it", async () => {
	const desk = await deviceConsole();
	desk.press(() => dc(desk).openCamera());
	await desk.wait(150); // the route has settled (100 ms), the camera started, the sheet fading in
	check("the camera is running under a sheet still fading in", [desk.cameraLive(), desk.ctx.cur_dialog], [1, null]);
	desk.press(() => desk.frappe.set_route("home"));
	await desk.settle();
	check("on Home, with no sheet left over it and the camera off", [desk.current(), desk.shown(), desk.cameraLive()], ["home", [], 0]);
});

test("Device Console: the phone locking with the camera open turns it off and closes the sheet", async () => {
	const desk = await deviceConsole();
	await desk.tap(() => dc(desk).openCamera());
	check("running", desk.cameraLive(), 1);
	desk.document.hidden = true;
	desk.fire("visibilitychange");
	check("the light goes off at once", desk.cameraLive(), 0);
	desk.document.hidden = false;
	await desk.settle();
	check("the sheet closed and stepped back off its entry", [desk.shown(), desk.route(), desk.at().index], [[], "device-console", 1]);
	check("nothing said: nothing failed", desk.messages, []);
});

test("Device Console: a camera blocked for the site says how to allow it", async () => {
	const desk = await deviceConsole();
	desk.cameraAuto = "blocked";
	await desk.tap(() => dc(desk).openCamera());
	check("the message is up, on the console's entry", [desk.shown(), desk.route()], [["Message"], "device-console"]);
	check("it says what to do", desk.messages.slice(-1), [
		"Camera access is blocked for this site. Allow the camera in the browser's settings, then try again.",
	]);
});

test("Device Console: a device with no camera says so", async () => {
	const desk = await deviceConsole();
	desk.cameraAuto = "none";
	await desk.tap(() => dc(desk).openCamera());
	check("it says there is none", desk.messages.slice(-1), ["No camera was found on this device."]);
});

test("Device Console: with no scanner kit the console still scans, picks and checks out", async () => {
	const desk = await deviceConsoleWith({ scanner: false });
	check("no camera button", camButton(desk, ".dc-cam"), false);
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	check("the picker opens as a plain dialog, with no entry of its own", [desk.shown(), desk.route(), desk.at().length], [["Choose Employee"], "device-console", 2]);
	const pick = desk.handler("click", ".dc-emp-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { emp: "EMP-9" } } }));
	check("check_out went with the picked employee", desk.sent("check_out").map((r) => r.args.employee), ["EMP-9"]);
	check("no step back off an entry it never had", [desk.route(), desk.at().index], ["device-console", 1]);
	await desk.tap(() => dc(desk).openCamera());
	check("the camera says it could not be loaded", desk.messages.slice(-1), [
		"Camera scanning could not be loaded. Reload the page, or use a hardware/Bluetooth scanner.",
	]);
	check("no push without a tap", desk.violations, []);
});

test("Device Console: a kit that throws as it starts leaves the console usable, on plain sheets", async () => {
	const desk = await deviceConsoleWith({ scannerThrows: true });
	check("on the console, its counts loaded", [desk.current(), desk.route(), desk.sent("get_console_bootstrap").length], ["device-console", "device-console", 1]);
	check("no camera button", camButton(desk, ".dc-cam"), false);
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	check("the picker opens as a plain dialog", [desk.shown(), desk.route()], [["Choose Employee"], "device-console"]);
	const pick = desk.handler("click", ".dc-emp-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { emp: "EMP-9" } } }));
	check("and checks out", desk.sent("check_out").map((r) => r.args.employee), ["EMP-9"]);
});

test("Scanner Audit: a kit that throws as it starts leaves the count usable, on plain sheets", async () => {
	const desk = await inventory(null, { scannerThrows: true });
	check("on the count", [desk.current(), desk.route()], ["inventory-scanner-audit", "inventory-scanner-audit"]);
	check("no camera button", camButton(desk, ".isa-cam"), false);
	await desk.tap(() => isa(desk).openItemSearch(""));
	check("Find Item opens as a plain dialog", [desk.shown(), desk.route()], [["Find Item"], "inventory-scanner-audit"]);
});

test("Device Console: a first visit waits for the kit before it decides what a sheet URL is", async () => {
	const desk = makeDesk({ scannerLate: true });
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 1, counts: {} });
	await desk.load("/desk/device-console/camera");
	check("still the camera's URL while the kit loads, no camera", [desk.route(), desk.cameras.length, desk.shown()], ["device-console/camera", 0, []]);
	check("the counts came anyway", desk.sent("get_console_bootstrap").length, 1);
	check("the camera button waits for the kit", camButton(desk, ".dc-cam"), false);
	desk.scannerArrives();
	await desk.settle();
	check("then the entry became the console", [desk.route(), desk.at()], ["device-console", { index: 0, length: 1, url: "/desk/device-console" }]);
	check("and the camera button shows", camButton(desk, ".dc-cam"), true);
	check("no camera nobody tapped for", desk.cameras.length, 0);
});

test("Device Console: a sheet tapped open while the kit loads opens once it has arrived", async () => {
	const desk = makeDesk({ scannerLate: true });
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 1, counts: {} });
	desk.respond.resolve_device_scan = ({ code }) => ({ type: "device", device: { name: code, device_name: code, status: "In Stock" } });
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("device-console"));
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	check("nothing pushed yet", [desk.route(), desk.shown()], ["device-console", []]);
	desk.scannerArrives();
	await desk.settle();
	check("the picker is up on its own entry", [desk.route(), desk.shown(), desk.at().index], ["device-console/employee", ["Choose Employee"], 2]);
	await desk.back();
	check("Back closes it and stays", [desk.route(), desk.shown()], ["device-console", []]);
});

test("Device Console: a sheet's URL reached from another page opens the console, not the sheet", async () => {
	const desk = await deviceConsole();
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	await desk.back(); // the picker closed with no pick: Forward could still reopen it
	await desk.tap(() => desk.frappe.set_route("List", "Managed Device"));
	// The awesome bar's frequently-visited links: frappe's Route History keeps every route
	// with a second segment. The console is mounted, so this is not a first show.
	await desk.tap(() => desk.frappe.set_route("device-console/camera"));
	check("no camera: the entry became the console", [desk.route(), desk.shown(), desk.cameras.length], ["device-console", [], 0]);
	await desk.back();
	check("Back returns to the page it came from", desk.current(), "managed-device");
	await desk.tap(() => desk.frappe.set_route("device-console/employee"));
	check("no picker either", [desk.route(), desk.shown()], ["device-console", []]);
	check("each became the console in place, the list still behind it", desk.urls(), [
		"/desk/home",
		"/desk/device-console",
		"/desk/managed-device",
		"/desk/device-console",
	]);
	await desk.back();
	check("one Back returns to the list", desk.current(), "managed-device");
	check("no push without a tap", desk.violations, []);
});

test("Device Console: a sheet's URL pushed while the console is showing steps back, leaving no duplicate", async () => {
	const desk = await deviceConsole();
	// The awesome bar's link to a sheet, picked on the console itself: frappe pushes it over the
	// console's own entry, so the entry behind it is already the console.
	await desk.tap(() => desk.frappe.set_route("device-console/camera"));
	check("no camera: back on the console's own entry", [desk.route(), desk.shown(), desk.cameras.length, desk.at().index], ["device-console", [], 0, 1]);
	await desk.tap(() => desk.frappe.set_route("device-console/employee"));
	check("no picker either", [desk.route(), desk.shown(), desk.at().index], ["device-console", [], 1]);
	check("no second console entry", desk.urls(), ["/desk/home", "/desk/device-console", "/desk/device-console/employee"]);
	await desk.back();
	check("so one Back leaves the console", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Device Console: back on the console from another page, a sheet's URL pushed there steps back as well", async () => {
	const desk = await deviceConsole();
	await desk.tap(() => desk.frappe.set_route("List", "Managed Device"));
	await desk.back();
	check("back on the console's own entry", [desk.current(), desk.at().index], ["device-console", 1]);
	await desk.tap(() => desk.frappe.set_route("device-console/camera"));
	check("no camera, and no second console entry", [desk.route(), desk.shown(), desk.cameras.length, desk.at().index, desk.urls()], [
		"device-console",
		[],
		0,
		1,
		["/desk/home", "/desk/device-console", "/desk/device-console/camera"],
	]);
	await desk.back();
	check("one Back leaves the console", desk.current(), "home");
});

test("Device Console: Back from another page onto the picker's own entry reopens it; X returns to the console", async () => {
	const desk = await deviceConsole();
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	await desk.tap(() => desk.frappe.set_route("List", "Managed Device")); // the awesome bar, over the picker
	check("the router closed the picker on the way out", [desk.current(), desk.shown()], ["managed-device", []]);
	await desk.back();
	check("the entry is the page's own, so the picker opens there again", [desk.route(), desk.shown()], ["device-console/employee", ["Choose Employee"]]);
	desk.closeDialog("Choose Employee");
	await desk.settle();
	check("X steps back onto the console, not the list", [desk.current(), desk.route(), desk.at().index], ["device-console", "device-console", 1]);
	check("no push without a tap", desk.violations, []);
});

test("Device Console: Forward after a completed Check Out opens no second check-out", async () => {
	const desk = await deviceConsole();
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	const pick = desk.handler("click", ".dc-emp-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { emp: "EMP-9" } } }));
	check("checked out: the device is Assigned", dc(desk).state.device.status, "Assigned");
	await desk.forward();
	check("no picker", desk.shown(), []);
	check("back on the console's own entry, no duplicate", [desk.route(), desk.at(), desk.urls()], [
		"device-console",
		{ index: 1, length: 3, url: "/desk/device-console" },
		["/desk/home", "/desk/device-console", "/desk/device-console/employee"],
	]);
	check("one check-out", desk.sent("check_out").length, 1);
	await desk.back();
	check("one Back leaves the console", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Device Console: Forward after a Transfer offers no second one", async () => {
	const desk = await deviceConsole({
		resolve_device_scan: ({ code }) => ({
			type: "device",
			device: { name: code, device_name: "Phone " + code, status: "Assigned", assigned_to_employee: "EMP-1" },
		}),
		transfer: (args) => ({ name: args.device, status: "Assigned", assigned_to_employee: args.new_employee }),
	});
	await desk.key(() => dc(desk).handleScan("DEV-4"));
	await desk.tap(() => dc(desk).pickEmployeeAndTransfer());
	const pick = desk.handler("click", ".dc-emp-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { emp: "EMP-9" } } }));
	check("transferred, and still Assigned", [dc(desk).state.device.status, desk.sent("transfer").length], ["Assigned", 1]);
	await desk.forward();
	check("Forward opens no picker, and steps back onto the console", [desk.shown(), desk.route(), desk.at().index], [[], "device-console", 1]);
	await desk.back();
	check("one Back leaves the console", desk.current(), "home");
});

test("Device Console: Forward onto a picker whose action no longer applies steps back", async () => {
	const desk = await deviceConsole({ mark_lost: ({ device }) => ({ name: device, status: "Lost/Stolen" }) });
	await desk.key(() => dc(desk).handleScan("DEV-1"));
	await desk.tap(() => dc(desk).pickEmployeeAndCheckOut());
	await desk.back(); // closed with no pick
	await desk.tap(() => dc(desk).act("mark_lost", { device: "DEV-1" }));
	check("the same device, now lost", [dc(desk).state.device.name, dc(desk).state.device.status], ["DEV-1", "Lost/Stolen"]);
	await desk.forward();
	check("no check-out picker for a lost device", desk.shown(), []);
	check("back on the console's own entry, no duplicate", [desk.route(), desk.at().index, desk.at().length], ["device-console", 1, 3]);
	await desk.back();
	check("one Back leaves the console", desk.current(), "home");
});

// ---------------------------------------------------------------------------- Inventory Scanner Audit

async function inventory(settings, opts) {
	const desk = makeDesk(opts);
	desk.respond.get_bootstrap = () => ({
		settings: Object.assign({ enable_camera_scan: 1, allow_unknown_item: 1, default_warehouse: "Stores" }, settings || {}),
		session: { name: "ICS-1", lines: [], summary: { lines: 0, with_variance: 0 } },
		// The person's saved switch for the card's reorder boxes (TASK-2026-02920); absent = the server said nothing.
		...(opts && opts.prefs !== undefined ? { prefs: opts.prefs } : {}),
	});
	desk.respond.resolve_scan = ({ code }) => {
		if (code === "FAIL") return new Error("boom");
		if (code.startsWith("ITEM")) return { type: "item", item_code: code, item_name: code, system_qty: 3, uom: "Nos" };
		if (code.startsWith("LOC")) return { type: "location", storage_location: code, warehouse: "Stores" };
		return { type: "unknown" };
	};
	desk.respond.lookup_item = () => [];
	// { holdBoot: true }: the start-up reply (get_bootstrap) is held until desk.releaseBoot() lets it land.
	if (opts && opts.holdBoot) desk.releaseBoot = hold(desk, "get_bootstrap");
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit"));
	return desk;
}

const isa = (desk) => desk.wrapper("inventory-scanner-audit").inventory_scanner;

// The reorder boxes on the item card (v1.725.0): the page asks the editor for the scanned item's rule at
// the active location, draws its cell in the card's own slot, hands focus back to the counted-quantity
// box after Enter, and never makes a history entry of any of it.
const reorderAnswer = ({ item_codes, warehouse }) => ({
	can_edit: true,
	levels: Object.fromEntries(
		JSON.parse(item_codes).map((code) => [
			code,
			{ item_code: code, has_rule: true, row_warehouse: warehouse, warehouse_label: warehouse, elsewhere: false, level: 3, qty: 10, modified: "m", stock_uom: "Nos", whole_number: true },
		])
	),
});
const cardHtml = (desk) =>
	desk.calls.filter((c) => c.label === ".isa-pending" && c.method === "html").map((c) => String(c.args[0] || ""));

test("Scanner Audit, reorder bundle: a scanned item asks for its rule once, at the active location, and draws the cell", async () => {
	const desk = await inventory(null, { reorder: true, prefs: { show_reorder: true } });
	desk.respond.get_reorder_levels = reorderAnswer;
	const entries = desk.at().length;
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	const asks = desk.sent("get_reorder_levels");
	check("one read, for the item, at the default warehouse", [asks.length, asks[0].args.warehouse, asks[0].args.item_codes], [1, "Stores", JSON.stringify(["ITEM-1"])]);
	check("the card has its reorder slot and the cell in it", cardHtml(desk).some((h) => h.includes('class="isa-reorder"') && h.includes('data-ro-item="ITEM-1"')), true);
	check("the pending card is ITEM-1", isa(desk).state.pendingItem.item_code, "ITEM-1");
	check("no history entry for it", desk.at().length, entries);
	check("no write", desk.requests.filter((r) => /set_reorder|remove_reorder/.test(r.method)).length, 0);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit, reorder bundle: Enter in a reorder box hands focus to the counted-quantity box", async () => {
	const desk = await inventory(null, { reorder: true, prefs: { show_reorder: true } });
	desk.respond.get_reorder_levels = reorderAnswer;
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	check("the wiring was given an after-Enter hook", typeof (desk.reorderBind && desk.reorderBind.opts && desk.reorderBind.opts.after_enter), "function");
	const before = desk.focused(".isa-qty");
	desk.reorderBind.opts.after_enter("ITEM-1");
	check("the counted-quantity box is focused again", desk.focused(".isa-qty"), before + 1);
	check("the scan box did not take it", desk.focused(".isa-scan") <= before + 1, true);
	check("a clearing question is a dialog (frappe.confirm), not a route", typeof desk.reorderBind.editor.env.confirm, "function");
});

test("Scanner Audit, reorder bundle arriving after the card: its slot is filled, the card is not redrawn", async () => {
	const desk = await inventory(null, { reorder: true, reorderLate: true, prefs: { show_reorder: true } });
	desk.respond.get_reorder_levels = reorderAnswer;
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	check("no read yet", desk.sent("get_reorder_levels").length, 0);
	const cards = cardHtml(desk).length;
	desk.reorderArrives();
	await desk.settle();
	check("then the item rule is asked for", desk.sent("get_reorder_levels").length, 1);
	check("the card itself was not drawn again", cardHtml(desk).filter((h) => h.includes("isa-card")).length, cardHtml(desk).slice(0, cards).filter((h) => h.includes("isa-card")).length);
	const slot = desk.calls.filter((c) => c.label === ".isa-reorder" && c.method === "html").map((c) => String(c.args[0] || ""));
	check("the slot got the cell", slot.some((h) => h.includes('data-ro-item="ITEM-1"')), true);
});

// TASK-2026-02920: the card's reorder boxes are off until a person turns them on, and the choice is theirs for good.
const toggleCalls = (desk) => desk.calls.filter((c) => c.label === ".isa-toggle");
const slotHtml = (desk) => desk.calls.filter((c) => c.label === ".isa-reorder" && c.method === "html").map((c) => String(c.args[0] || ""));

test("Scanner Audit: the reorder boxes are hidden by default, with a switch, and nothing is read for them", async () => {
	const desk = await inventory(null, { reorder: true });
	check("the switch is offered once the editor is here, and says show", [toggleCalls(desk).some((c) => c.method === "toggle" && c.args[0] === true), toggleCalls(desk).some((c) => c.method === "text" && c.args[0] === "Show reorder levels")], [true, true]);
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	check("no read for the rule, and nothing drawn in the card's slot", [desk.sent("get_reorder_levels").length, cardHtml(desk).every((h) => !h.includes("data-ro-item")), cardHtml(desk).some((h) => h.includes('class="isa-reorder"></div>'))], [0, true, true]);
	check("the card is the card it was: counted-quantity box focused", desk.focused(".isa-qty"), 1);
});

test("Scanner Audit: the switch shows the boxes, reads the rule once, keeps the focus on the count, and is remembered", async () => {
	const desk = await inventory(null, { reorder: true });
	desk.respond.get_reorder_levels = reorderAnswer;
	desk.respond.set_show_reorder = ({ screen, value }) => ({ screen, show_reorder: !!value });
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	const mousedown = desk.handlers.find((h) => h.el.__label === ".isa-toggle" && h.type === "mousedown");
	let prevented = false;
	mousedown.fn({ preventDefault() { prevented = true; } });
	check("a tap on the switch cannot take the focus off the count", prevented, true);
	const entries = desk.at().length;
	const qty = desk.focused(".isa-qty");
	await desk.tap(() => isa(desk).toggleReorder());
	await desk.settle();
	check("the rule is read now, once, at the active location", [desk.sent("get_reorder_levels").length, desk.sent("get_reorder_levels")[0].args.warehouse], [1, "Stores"]);
	check("the card's slot got the cell", slotHtml(desk).some((h) => h.includes('data-ro-item="ITEM-1"')), true);
	check("the focus is put back on the counted-quantity box", desk.focused(".isa-qty"), qty + 1);
	const saved = desk.sent("set_show_reorder");
	check("the choice is saved to the person's own store: this screen, on", [saved.length, saved[0].args.screen, saved[0].args.value], [1, "scanner_audit", 1]);
	check("the switch says hide", toggleCalls(desk).filter((c) => c.method === "text").slice(-1)[0].args[0], "Hide reorder levels");
	check("a switch is not a history entry", desk.at().length, entries);
	await desk.tap(() => isa(desk).toggleReorder());
	await desk.settle();
	check("off again: the slot is emptied and the choice saved off", [slotHtml(desk).slice(-1)[0], desk.sent("set_show_reorder").slice(-1)[0].args.value], ["", 0]);
	await desk.tap(() => isa(desk).toggleReorder());
	await desk.settle();
	check("on again for the same card: its rule is asked for again only because the card is the one on screen", desk.sent("get_reorder_levels").length >= 1, true);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a saved choice opens the card with the boxes, and anything unreadable is hidden", async () => {
	let desk = await inventory(null, { reorder: true, prefs: { show_reorder: true } });
	desk.respond.get_reorder_levels = reorderAnswer;
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	check("saved on: the boxes are in the card and the rule was read", [slotHtml(desk).some((h) => h.includes("data-ro-item")) || cardHtml(desk).some((h) => h.includes("data-ro-item")), desk.sent("get_reorder_levels").length], [true, 1]);
	for (const [label, prefs] of [["off", { show_reorder: false }], ["a damaged value", { show_reorder: "yes" }], ["a number", { show_reorder: 1 }], ["no prefs at all", undefined], ["prefs without the key", {}]]) {
		desk = await inventory(null, { reorder: true, prefs });
		desk.respond.get_reorder_levels = reorderAnswer;
		await desk.tap(() => isa(desk).handleScan("ITEM-1"));
		await desk.settle();
		check(`${label}: hidden, nothing read`, [desk.sent("get_reorder_levels").length, cardHtml(desk).every((h) => !h.includes("data-ro-item"))], [0, true]);
	}
});

test("Scanner Audit: a choice that could not be saved still works this visit, and says so", async () => {
	const desk = await inventory(null, { reorder: true });
	desk.respond.get_reorder_levels = reorderAnswer;
	desk.respond.set_show_reorder = () => new Error("boom");
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	await desk.tap(() => isa(desk).toggleReorder());
	await desk.settle();
	check("the boxes show anyway", slotHtml(desk).some((h) => h.includes("data-ro-item")), true);
	check("and a note says it will not be remembered", desk.alerts.some((a) => /next time/.test(a)), true);
});

test("Scanner Audit: a tap on the switch before the start-up reply lands is not put back by that reply", async () => {
	// The reorder bundle can be cached, so the switch is there before get_bootstrap answers; the stored value
	// the reply carries is older than the tap.
	for (const stored of [false, true]) {
		const desk = await inventory(null, { reorder: true, holdBoot: true, prefs: { show_reorder: stored } });
		desk.respond.set_show_reorder = ({ screen, value }) => ({ screen, show_reorder: !!value });
		await desk.settle();
		check(`stored ${stored}: the switch is up before boot`, isa(desk).booted, false);
		await desk.tap(() => isa(desk).toggleReorder());
		await desk.settle();
		const tapped = true; // before the reply the switch says "Show", whatever was stored: a tap is "show"
		check("the tap took effect on screen", isa(desk).state.showReorder, tapped);
		desk.releaseBoot();
		await desk.settle();
		check("the reply landed", isa(desk).booted, true);
		check("and the screen is as the person left it, not as the reply stored it", isa(desk).state.showReorder, tapped);
		check("the switch says what the screen does", toggleCalls(desk).filter((c) => c.method === "text").slice(-1)[0].args[0], tapped ? "Hide reorder levels" : "Show reorder levels");
		const saved = desk.sent("set_show_reorder");
		check("what was stored is what is on screen", [saved.length, saved[0].args.value], [1, tapped ? 1 : 0]);
	}
});

test("Scanner Audit: quick taps on the switch send one request at a time, and the last tap is what is stored", async () => {
	const desk = await inventory(null, { reorder: true });
	desk.respond.get_reorder_levels = reorderAnswer;
	desk.respond.set_show_reorder = ({ screen, value }) => ({ screen, show_reorder: !!value });
	await desk.settle();
	const release = hold(desk, "set_show_reorder");
	for (let i = 0; i < 4; i++) await desk.tap(() => isa(desk).toggleReorder()); // on, off, on, off
	check("the last tap wins on screen", isa(desk).state.showReorder, false);
	check("only the first request is out while it is unanswered", desk.sent("set_show_reorder").map((r) => r.args.value), [1]);
	release();
	await desk.settle();
	check("then the latest choice follows, in order, and nothing else", desk.sent("set_show_reorder").map((r) => r.args.value), [1, 0]);
	check("the store ends where the screen is", [desk.sent("set_show_reorder").slice(-1)[0].args.value, isa(desk).state.showReorder], [0, false]);
	check("no note about a failed save", desk.alerts.length, 0);
	check("no push without a tap", desk.violations, []);
	// three taps ending where it began: the in-flight request already says it, so no second one is needed
	const again = hold(desk, "set_show_reorder");
	for (let i = 0; i < 3; i++) await desk.tap(() => isa(desk).toggleReorder()); // on, off, on
	again();
	await desk.settle();
	check("an end state equal to what was just sent adds no request", desk.sent("set_show_reorder").map((r) => r.args.value).slice(2), [1]);
});

test("Scanner Audit without the reorder bundle: the card is the card it was, and nothing is asked", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).handleScan("ITEM-1"));
	await desk.settle();
	check("no read", desk.sent("get_reorder_levels").length, 0);
	check("an empty slot", cardHtml(desk).every((h) => !h.includes("data-ro-item")), true);
	check("the card is drawn and the counted-quantity box focused", [isa(desk).state.pendingItem.item_code, desk.focused(".isa-qty")], ["ITEM-1", 1]);
});

test("Scanner Audit: a start-up that failed says so, and Try again boots it in place", async () => {
	const desk = makeDesk();
	desk.respond.get_bootstrap = () => NETWORK_DOWN;
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit"));
	check("the panel says why", saidText(desk, "Could not load the count."), true);
	const retry = tryAgain(desk);
	check("with Try again", !!retry, true);
	desk.respond.get_bootstrap = () => ({ settings: {}, session: null });
	await desk.tap(() => retry.fn({ preventDefault() {} }));
	check("asked again", desk.sent("get_bootstrap").length, 2);
	check("still the count's one entry", [desk.route(), desk.urls()], ["inventory-scanner-audit", ["/desk/home", "/desk/inventory-scanner-audit"]]);
	check("no push without a tap", desk.violations, []);
});

// The REAL sf_kit (v1.645.0): the bundle installed on the page, its quiet call answered by the same
// `respond`, every panel drawn by its own state.render and every failure in its own rejection
// shape. A page that reads the kit wrong fails here, where the plain stand-in above would not.
const reply = (status, body) => Object.assign(new Error(`HTTP ${status}`), { status, body });
const pressAction = (el) => el.querySelector(".sf-state-action").clicks.forEach((fn) => fn({ preventDefault() {} }));

test("Device Console, real kit: a 500 draws the kit's error panel, and Try again boots it in place", async () => {
	const desk = makeDesk({ kit: true });
	await desk.installKit();
	desk.respond.get_console_bootstrap = () => reply(500, { exc_type: "Exception", exception: "pymysql.err.OperationalError" });
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("device-console"));
	const panel = dc(desk).$load[0];
	check("the kit's error panel, with the page's title", [panel.innerHTML.includes("sf-state--error"), panel.innerHTML.includes("Could not load the Device Console.")], [true, true]);
	check("the kit's sentence, never the exception", [panel.innerHTML.includes("The server could not finish this request."), panel.innerHTML.includes("pymysql")], [true, false]);
	check("no frappe dialog", desk.shown(), []);
	desk.respond.get_console_bootstrap = () => ({ enable_camera_scan: 0, counts: {} });
	await desk.tap(() => pressAction(panel));
	check("asked again", desk.sent("get_console_bootstrap").length, 2);
	check("still the console's one entry", [desk.route(), desk.urls()], ["device-console", ["/desk/home", "/desk/device-console"]]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit, real kit: a 403 draws no-access, and a lapsed session the signed-out panel", async () => {
	const refused = makeDesk({ kit: true });
	await refused.installKit();
	refused.respond.get_bootstrap = () =>
		reply(403, { exc_type: "PermissionError", _server_messages: JSON.stringify([JSON.stringify({ message: "<b>Not permitted</b> to count." })]) });
	await refused.load("/desk/home");
	await refused.tap(() => refused.frappe.set_route("inventory-scanner-audit"));
	const boot = isa(refused).$boot[0].innerHTML;
	check("no access, with the page's title", [boot.includes("sf-state--no-access"), boot.includes("You do not have access to the Inventory Scanner Audit.")], [true, true]);
	check("and the server's words", boot.includes("Not permitted to count."), true);
	check("no frappe dialog", refused.shown(), []);

	const lapsed = makeDesk({ kit: true });
	await lapsed.installKit();
	lapsed.respond.get_bootstrap = () => reply(401, { exc_type: "SessionExpired", session_expired: 1 });
	await lapsed.load("/desk/home");
	await lapsed.tap(() => lapsed.frappe.set_route("inventory-scanner-audit"));
	const signedOut = isa(lapsed).$boot[0].innerHTML;
	check("signed out, with Reload", [signedOut.includes("You have been signed out."), signedOut.includes(">Reload</button>")], [true, true]);
});

// v1.645.0 review: the start-up has its own slot. A count started while it loads keeps its lines and
// its session, whether the start-up then fails or answers with no session.
test("Scanner Audit, real kit: a start-up that lands after a count began neither paints over it nor replaces it", async () => {
	const session = { name: "ICS-9", lines: [], summary: { lines: 0, with_variance: 0 } };
	for (const late of ["fails", "answers"]) {
		const desk = makeDesk({ kit: true });
		await desk.installKit();
		desk.respond.start_session = () => session;
		await desk.load("/desk/home");
		await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit"));
		await desk.tap(() => isa(desk).startSession());
		check(`${late}: the count began`, isa(desk).state.session && isa(desk).state.session.name, "ICS-9");
		const req = desk.sent("get_bootstrap")[0];
		if (late === "fails") req.reject(reply(500, {}));
		else req.resolve({ settings: {}, session: null });
		await desk.settle();
		check(`${late}: the session stands`, isa(desk).state.session && isa(desk).state.session.name, "ICS-9");
		check(`${late}: the kit never wrote where the lines go`, isa(desk).$list[0].innerHTML, undefined);
		if (late === "fails") check("the error is in the start-up's slot", isa(desk).$boot[0].innerHTML.includes("Could not load the count."), true);
	}
});

test("Scanner Audit, real kit: a start-up that lands after a count was started and cancelled does not bring it back", async () => {
	const desk = makeDesk({ kit: true });
	await desk.installKit();
	desk.respond.start_session = () => ({ name: "ICS-9", lines: [], summary: { lines: 0, with_variance: 0 } });
	desk.respond.cancel_session = () => ({});
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit"));
	await desk.tap(() => isa(desk).startSession());
	await desk.tap(() => isa(desk).cancelSession());
	await desk.tap(() => desk.dialog("Confirm").answer(true));
	check("the count was cancelled", isa(desk).state.session, null);
	// The start-up was asked for before all that, and still names the count open at the time.
	desk.sent("get_bootstrap")[0].resolve({ settings: {}, session: { name: "ICS-9", lines: [], summary: { lines: 0, with_variance: 0 } } });
	await desk.settle();
	check("the late start-up leaves it cancelled", isa(desk).state.session, null);
});

test("Scanner Audit: Back closes Find Item and stays; Forward reopens it", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openItemSearch(""));
	check("Find Item is its own entry", [desk.route(), desk.shown()], ["inventory-scanner-audit/find", ["Find Item"]]);
	await desk.back();
	check("Back closes it and stays on the count", [desk.shown(), desk.current(), desk.route()], [[], "inventory-scanner-audit", "inventory-scanner-audit"]);
	await desk.forward();
	check("Forward opens it again", [desk.shown(), desk.route(), desk.at().length], [["Find Item"], "inventory-scanner-audit/find", 3]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a Find Item pick is looked up after the step back and focuses the count box", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openItemSearch(""));
	const scanFocus = desk.focused(".isa-scan");
	const pick = desk.handler("click", ".isa-pick");
	await desk.tap(() => pick({ currentTarget: { __data: { item: "ITEM-5" } } }));
	check("closed and stepped back", [desk.shown(), desk.route(), desk.at().index], [[], "inventory-scanner-audit", 1]);
	check("the pending card is ITEM-5", isa(desk).state.pendingItem && isa(desk).state.pendingItem.item_code, "ITEM-5");
	check("the counted-qty box has focus", desk.focused(".isa-qty"), 1);
	check("the scan box did not take it back", desk.focused(".isa-scan"), scanFocus);
});

test("Scanner Audit: on an iPhone the camera reads a location's QR label through jsQR (the kit's)", async () => {
	const desk = await inventory(null, { iphone: true });
	check("the camera button shows", camButton(desk, ".isa-cam"), true);
	await desk.tap(() => isa(desk).openCamera());
	check("jsQR loaded once, on first use", desk.required.filter((p) => p.includes("jsQR")).length, 1);
	check("the camera is its own entry", [desk.route(), desk.shown(), desk.cameraLive()], ["inventory-scanner-audit/camera", ["Camera Scan"], 1]);
	desk.nextCode = "LOC-A";
	await desk.settle();
	check("the location is set, back on the count's own entry", [desk.route(), desk.at().index, desk.alerts.slice(-1)], ["inventory-scanner-audit", 1, ["Location: LOC-A"]]);
	check("the camera is off", desk.cameraLive(), 0);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a camera read of an item is looked up on the camera's entry, then steps back", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	check("the camera is its own entry", [desk.route(), desk.shown()], ["inventory-scanner-audit/camera", ["Camera Scan"]]);
	desk.nextCode = "ITEM-1";
	await desk.settle();
	check("one lookup", desk.sent("resolve_scan").length, 1);
	check("stepped back off the camera's entry", [desk.shown(), desk.route(), desk.at().index], [[], "inventory-scanner-audit", 1]);
	check("the pending card is drawn, counted-qty box focused", [isa(desk).state.pendingItem.item_code, desk.focused(".isa-qty")], ["ITEM-1", 1]);
	check("the camera is off", desk.cameraLive(), 0);
	await desk.back();
	check("a scan is not an entry: Back leaves the page", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: an unknown camera read hands the camera's entry to Find Item", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	desk.nextCode = "WHAT-IS-THIS";
	await desk.settle();
	check("Find Item took the camera's entry over", [desk.shown(), desk.route(), desk.at()], [["Find Item"], "inventory-scanner-audit/find", { index: 2, length: 3, url: "/desk/inventory-scanner-audit/find" }]);
	await desk.back();
	check("Back closes it and stays", [desk.shown(), desk.route()], [[], "inventory-scanner-audit"]);
	await desk.back();
	check("no dead entry: the next Back leaves", desk.current(), "home");
	check("no push without a tap (the camera read pushed nothing)", desk.violations, []);
});

test("Scanner Audit: a failed camera lookup steps off the camera's entry once its message is closed", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	desk.nextCode = "FAIL";
	await desk.settle();
	check("the server's refusal is up and stays up, over the camera's entry", [desk.shown(), desk.route()], [["Message"], "inventory-scanner-audit/camera"]);
	desk.closeDialog("Message");
	await desk.settle();
	check("closing it stepped back onto the count's own entry", [desk.shown(), desk.route(), desk.at()], [
		[],
		"inventory-scanner-audit",
		{ index: 1, length: 3, url: "/desk/inventory-scanner-audit" },
	]);
	await desk.back();
	check("so one Back leaves the page", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a failed camera lookup's message closed by Back costs that one Back", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	desk.nextCode = "FAIL";
	await desk.settle();
	await desk.back();
	check("Back closed the message and moved onto the count", [desk.shown(), desk.route(), desk.at().index], [[], "inventory-scanner-audit", 1]);
	check("the step waiting on the message stepped over nothing", desk.current(), "inventory-scanner-audit");
	await desk.back();
	check("the next Back leaves the page", desk.current(), "home");
});

test("Scanner Audit: while a camera read is looked up, Forward onto its entry starts no camera", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	hold(desk, "resolve_scan"); // never answered
	desk.nextCode = "ITEM-1";
	await desk.settle();
	const cameras = desk.cameras.length;
	await desk.back();
	await desk.forward();
	check("the entry being looked up on starts no camera nobody tapped for", [desk.route(), desk.shown(), desk.cameras.length], ["inventory-scanner-audit/camera", [], cameras]);
	await desk.tap(() => isa(desk).openItemSearch(""));
	check("Find Item took that entry over", [desk.route(), desk.at().length], ["inventory-scanner-audit/find", 3]);
});

test("Scanner Audit: a sheet's URL reached from another page opens the count, not the sheet", async () => {
	const desk = await inventory();
	await desk.tap(() => desk.frappe.set_route("List", "Item"));
	// The awesome bar's frequently-visited links; the scanner is mounted, so not a first show.
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit/camera"));
	check("no camera: the entry became the count", [desk.route(), desk.shown(), desk.cameras.length], ["inventory-scanner-audit", [], 0]);
	await desk.back();
	check("Back returns to the page it came from", desk.current(), "item");
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit/find"));
	check("no Find Item either", [desk.route(), desk.shown()], ["inventory-scanner-audit", []]);
	check("each became the count in place, the list still behind it", desk.urls(), [
		"/desk/home",
		"/desk/inventory-scanner-audit",
		"/desk/item",
		"/desk/inventory-scanner-audit",
	]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a sheet's URL pushed while the count is showing steps back, leaving no duplicate", async () => {
	const desk = await inventory();
	// The awesome bar's link to a sheet, picked on the scanner itself: pushed over the count's own entry.
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit/camera"));
	check("no camera: back on the count's own entry", [desk.route(), desk.shown(), desk.cameras.length, desk.at().index], ["inventory-scanner-audit", [], 0, 1]);
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit/find"));
	check("no Find Item either", [desk.route(), desk.shown(), desk.at().index], ["inventory-scanner-audit", [], 1]);
	check("no second count entry", desk.urls(), ["/desk/home", "/desk/inventory-scanner-audit", "/desk/inventory-scanner-audit/find"]);
	await desk.back();
	check("so one Back leaves the page", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a camera read's reply leaves a camera tapped open after Back alone", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	const release = hold(desk, "resolve_scan");
	desk.nextCode = "ITEM-1";
	await desk.settle();
	await desk.back();
	await desk.tap(() => isa(desk).openCamera());
	check("the clerk's new camera is up on an entry of its own", [desk.route(), desk.shown(), desk.at().index], ["inventory-scanner-audit/camera", ["Camera Scan"], 2]);
	release();
	await desk.settle();
	check("the old read's reply stepped nothing off: the camera is still up", [desk.route(), desk.shown(), desk.at().index, desk.cameraLive()], ["inventory-scanner-audit/camera", ["Camera Scan"], 2, 1]);
	check("the read is drawn beneath it", isa(desk).state.pendingItem && isa(desk).state.pendingItem.item_code, "ITEM-1");
});

test("Scanner Audit: a camera read's reply leaves a camera reopened on the read's entry alone", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	const release = hold(desk, "resolve_scan");
	desk.nextCode = "ITEM-1";
	await desk.settle();
	check("looked up on the camera's entry, no sheet on it", [desk.route(), desk.shown()], ["inventory-scanner-audit/camera", []]);
	await desk.tap(() => isa(desk).openCamera());
	check("the clerk's camera is up on that same entry", [desk.route(), desk.shown(), desk.at().length], ["inventory-scanner-audit/camera", ["Camera Scan"], 3]);
	release();
	await desk.settle();
	check("the camera is still up, on its entry", [desk.route(), desk.shown(), desk.at().index, desk.cameraLive()], ["inventory-scanner-audit/camera", ["Camera Scan"], 2, 1]);
	check("the read is drawn beneath it", isa(desk).state.pendingItem && isa(desk).state.pendingItem.item_code, "ITEM-1");
});

test("Scanner Audit: an unknown camera read's reply never turns a camera reopened on its entry into Find Item", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	const release = hold(desk, "resolve_scan");
	desk.nextCode = "WHAT-IS-THIS";
	await desk.settle();
	await desk.tap(() => isa(desk).openCamera());
	release();
	await desk.settle();
	check("no Find Item for the old code: the camera stays up", [desk.route(), desk.shown(), desk.at()], [
		"inventory-scanner-audit/camera",
		["Camera Scan"],
		{ index: 2, length: 3, url: "/desk/inventory-scanner-audit/camera" },
	]);
	check("the code is reported instead", desk.alerts.slice(-1), ["Unknown barcode: WHAT-IS-THIS"]);
});

test("Scanner Audit: with two camera reads looked up, each reply acts only on its own read's entry", async () => {
	const desk = await inventory();
	const answer = desk.respond.resolve_scan;
	delete desk.respond.resolve_scan; // both lookups are held, and let land one at a time
	await desk.tap(() => isa(desk).openCamera());
	desk.nextCode = "ITEM-1";
	await desk.settle();
	await desk.back();
	await desk.tap(() => isa(desk).openCamera());
	desk.nextCode = "WHAT-IS-THIS";
	await desk.settle();
	check("the second read is looked up on the new camera's entry", [desk.route(), desk.shown(), desk.at().index], ["inventory-scanner-audit/camera", [], 2]);
	const [first, second] = desk.sent("resolve_scan");
	first.resolve(answer(first.args));
	await desk.settle();
	check("the first read's reply is drawn, and steps nothing off the second's entry", [isa(desk).state.pendingItem.item_code, desk.route(), desk.at().index], ["ITEM-1", "inventory-scanner-audit/camera", 2]);
	second.resolve(answer(second.args));
	await desk.settle();
	check("the second's hands its own entry to Find Item", [desk.shown(), desk.route(), desk.at()], [
		["Find Item"],
		"inventory-scanner-audit/find",
		{ index: 2, length: 3, url: "/desk/inventory-scanner-audit/find" },
	]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: an unknown code typed at the scan box opens Find Item as an entry", async () => {
	const desk = await inventory();
	await desk.key(() => isa(desk).handleScan("NOPE"));
	check("Find Item is up on its own entry", [desk.shown(), desk.route()], [["Find Item"], "inventory-scanner-audit/find"]);
	await desk.back();
	check("Back closes it and stays", [desk.shown(), desk.route()], [[], "inventory-scanner-audit"]);
	check("the key press was the activation", desk.violations, []);
});

test("Scanner Audit: an unknown code whose reply lands after Back left the page routes nowhere", async () => {
	const desk = await inventory();
	const release = hold(desk, "resolve_scan");
	await desk.key(() => isa(desk).handleScan("NOPE"));
	await desk.back();
	check("Back left the scanner", desk.current(), "home");
	release();
	await desk.settle();
	check("not dragged back to the scanner", [desk.current(), desk.shown()], ["home", []]);
	check("nothing pushed", [desk.urls(), desk.at().index], [["/desk/home", "/desk/inventory-scanner-audit"], 0]);
	check("the code is reported instead", desk.alerts.slice(-1), ["Unknown barcode: NOPE"]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: an unknown code whose reply lands after Back and Forward pushes nothing", async () => {
	const desk = await inventory();
	const release = hold(desk, "resolve_scan");
	await desk.key(() => isa(desk).handleScan("NOPE"));
	await desk.back();
	await desk.forward();
	release();
	await desk.settle();
	check("on the count, no Find Item", [desk.route(), desk.shown()], ["inventory-scanner-audit", []]);
	check("nothing pushed", desk.urls(), ["/desk/home", "/desk/inventory-scanner-audit"]);
	check("the code is reported instead", desk.alerts.slice(-1), ["Unknown barcode: NOPE"]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: an unknown camera read whose reply lands after Back pushes nothing", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	const release = hold(desk, "resolve_scan");
	desk.nextCode = "WHAT-IS-THIS";
	await desk.settle();
	check("looked up on the camera's entry", [desk.route(), desk.shown()], ["inventory-scanner-audit/camera", []]);
	await desk.back();
	check("Back: the count", desk.route(), "inventory-scanner-audit");
	release();
	await desk.settle();
	check("no Find Item, and still on the count's entry", [desk.shown(), desk.route(), desk.at().index], [[], "inventory-scanner-audit", 1]);
	check("nothing pushed", desk.urls(), ["/desk/home", "/desk/inventory-scanner-audit", "/desk/inventory-scanner-audit/camera"]);
	check("the code is reported instead", desk.alerts.slice(-1), ["Unknown barcode: WHAT-IS-THIS"]);
	check("no push without a tap", desk.violations, []);
	await desk.back();
	check("one Back leaves the page", desk.current(), "home");
});

test("Scanner Audit: an unknown camera read whose reply lands after Back left the page routes nowhere", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	const release = hold(desk, "resolve_scan");
	desk.nextCode = "WHAT-IS-THIS";
	await desk.settle();
	await desk.back();
	await desk.back();
	check("Back twice left the scanner", desk.current(), "home");
	release();
	await desk.settle();
	check("not dragged back to the scanner", [desk.current(), desk.shown(), desk.at().index], ["home", [], 0]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: an unknown code whose reply lands after a sheet was tapped open leaves that sheet alone", async () => {
	const desk = await inventory();
	const release = hold(desk, "resolve_scan");
	await desk.key(() => isa(desk).handleScan("NOPE"));
	await desk.tap(() => isa(desk).openCamera());
	release();
	await desk.settle();
	check("the camera the clerk opened is still up", [desk.route(), desk.shown()], ["inventory-scanner-audit/camera", ["Camera Scan"]]);
	check("the code is reported instead", desk.alerts.slice(-1), ["Unknown barcode: NOPE"]);
});

test("Scanner Audit: Forward onto a camera that cannot open again steps back, leaving no duplicate", async () => {
	const desk = await inventory();
	await desk.tap(() => isa(desk).openCamera());
	await desk.back();
	isa(desk).state.settings.enable_camera_scan = 0; // turned off since
	const cameras = desk.cameras.length;
	await desk.forward();
	check("no camera", [desk.shown(), desk.cameras.length], [[], cameras]);
	check("back on the count's own entry, the camera's still ahead", [desk.route(), desk.at()], ["inventory-scanner-audit", { index: 1, length: 3, url: "/desk/inventory-scanner-audit" }]);
	check("no second count entry", desk.urls(), ["/desk/home", "/desk/inventory-scanner-audit", "/desk/inventory-scanner-audit/camera"]);
	await desk.back();
	check("so one Back leaves the page", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a reload on a sheet's URL opens the count", async () => {
	const desk = makeDesk();
	desk.respond.get_bootstrap = () => ({ settings: { enable_camera_scan: 1 }, session: null });
	await desk.load("/desk/inventory-scanner-audit/camera");
	check("replaced with the page, no camera", [desk.at().url, desk.at().length, desk.cameras.length, desk.shown()], ["/desk/inventory-scanner-audit", 1, 0, []]);
});

test("Scanner Audit: a reload on Find Item's own entry steps back onto the count, leaving no duplicate", async () => {
	let desk = await inventory();
	await desk.tap(() => isa(desk).openItemSearch(""));
	check("Find Item's entry is marked as the count's own", desk.ctx.history.state, { isa_sheet: "find" });
	desk = await desk.reload(); // pull-to-refresh
	check("no sheet reopened by the reload", desk.shown(), []);
	check("stepped back onto the count's own entry, Find Item's still ahead", [desk.route(), desk.at(), desk.urls()], [
		"inventory-scanner-audit",
		{ index: 1, length: 3, url: "/desk/inventory-scanner-audit" },
		["/desk/home", "/desk/inventory-scanner-audit", "/desk/inventory-scanner-audit/find"],
	]);
	await desk.back();
	check("so one Back leaves the page", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: Find tapped while the first show's replace waits on the bootstrap keeps the count's entry", async () => {
	// v16's set_route clears route_flags only once every request in flight has landed.
	const desk = makeDesk({ ajax: true });
	desk.respond.get_bootstrap = () => ({
		settings: { enable_camera_scan: 1, allow_unknown_item: 1, default_warehouse: "Stores" },
		session: { name: "ICS-1", lines: [], summary: { lines: 0, with_variance: 0 } },
	});
	desk.respond.lookup_item = () => [];
	await desk.load("/desk/home");
	const release = hold(desk, "get_bootstrap");
	// The awesome bar's frequently-visited link, with the scanner not yet opened this session.
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit/find"));
	check("the first show made the entry the count", [desk.route(), desk.urls()], ["inventory-scanner-audit", ["/desk/home", "/desk/inventory-scanner-audit"]]);
	check("… and left no replace behind for the next route change", !!desk.frappe.route_flags.replace_route, false);
	await desk.tap(() => isa(desk).openItemSearch(""));
	release();
	await desk.settle();
	check("Find Item got an entry of its own", [desk.route(), desk.shown(), desk.urls()], [
		"inventory-scanner-audit/find",
		["Find Item"],
		["/desk/home", "/desk/inventory-scanner-audit", "/desk/inventory-scanner-audit/find"],
	]);
	desk.closeDialog("Find Item");
	await desk.settle();
	check("X on Find Item stays on the count", [desk.current(), desk.route(), desk.at().index], ["inventory-scanner-audit", "inventory-scanner-audit", 1]);
	check("no push without a tap", desk.violations, []);
});

test("Scanner Audit: a camera lookup lost to the network steps off the camera's entry, after a message closed by its X", async () => {
	const desk = await inventory();
	// Any message closed with its X earlier in the session: frappe's msgprint dialog is one dialog
	// for the whole session, and its X (data-dismiss) never calls hide(), so `is_visible` stays set.
	await desk.key(() => isa(desk).handleScan("FAIL"));
	check("a refusal is up", desk.shown(), ["Message"]);
	desk.closeDialog("Message");
	await desk.settle();
	check("closed by its X, `is_visible` still set", [desk.shown(), desk.frappe.msg_dialog.is_visible], [[], true]);
	const lookup = desk.respond.resolve_scan;
	desk.respond.resolve_scan = (args) => (args.code === "OFFLINE" ? NETWORK_DOWN : lookup(args));
	await desk.tap(() => isa(desk).openCamera());
	desk.nextCode = "OFFLINE";
	await desk.settle();
	check("no message: request.js shows none for a request that never got through", desk.shown(), []);
	check("stepped back off the camera's entry", [desk.route(), desk.at(), isa(desk).leftover], [
		"inventory-scanner-audit",
		{ index: 1, length: 3, url: "/desk/inventory-scanner-audit" },
		null,
	]);
	await desk.back();
	check("so one Back leaves the page", desk.current(), "home");
});

test("Scanner Audit: Back during the camera's fade-in still closes it", async () => {
	const desk = await inventory();
	desk.cameraAuto = "manual";
	// Tap the camera, and press Back 50 ms after its sheet starts to fade in.
	desk.press(() => isa(desk).openCamera());
	await desk.wait(150); // route pushed and settled (100 ms), sheet fading
	const d = desk.dialogs.filter((x) => x.title === "Camera Scan").pop();
	check("fading in, not yet shown", [d.fading, d.display], [true, false]);
	desk.ctx.history.back();
	await desk.settle();
	check("closed once it was shown", [d.display, desk.route()], [false, "inventory-scanner-audit"]);
	desk.cameras.slice(-1)[0].allow();
	await desk.settle();
	check("the camera allowed late is stopped", desk.cameraLive(), 0);
});

// ---------------------------------------------------------------------------- Sales Pipeline

async function pipeline() {
	const desk = makeDesk();
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("sales-pipeline"));
	return desk;
}

const tvOn = (desk) => desk.body.__classes.has("sales-pipeline-tv");

test("Sales Pipeline: the TV Mode button is an entry; Back returns to the board with its navbar", async () => {
	const desk = await pipeline();
	check("the board, not TV", [desk.route(), tvOn(desk)], ["sales-pipeline", false]);
	await desk.tap(() => desk.page.buttons["TV Mode"]());
	check("TV mode is /tv, fullscreen asked in the click", [desk.route(), tvOn(desk), desk.fullscreen.asked], ["sales-pipeline/tv", true, 1]);
	await desk.back();
	check("Back: the board, chrome back, fullscreen ended", [desk.route(), tvOn(desk), desk.fullscreen.exited], ["sales-pipeline", false, 1]);
	await desk.forward();
	check("Forward: TV mode again", [desk.route(), tvOn(desk)], ["sales-pipeline/tv", true]);
	await desk.back();
	await desk.back();
	check("leaving the page drops TV mode", [desk.current(), tvOn(desk)], ["home", false]);
	await desk.tap(() => desk.frappe.set_route("sales-pipeline"));
	check("coming back to the plain route is not TV mode (it used to stick)", tvOn(desk), false);
	check("no push without a tap", desk.violations, []);
});

test("Sales Pipeline: Escape out of fullscreen leaves TV mode the way Back does", async () => {
	const desk = await pipeline();
	await desk.tap(() => desk.page.buttons["TV Mode"]());
	desk.document.exitFullscreen(); // the browser's own Escape
	await desk.settle();
	check("back on the board, navbar back", [desk.route(), tvOn(desk), desk.at().index], ["sales-pipeline", false, 1]);
});

test("Sales Pipeline: a kiosk loading /tv gets TV mode with no fullscreen request", async () => {
	const desk = makeDesk();
	await desk.load("/desk/sales-pipeline/tv");
	check("TV mode, nothing asked", [tvOn(desk), desk.fullscreen.asked], [true, 0]);
	desk.fire("fullscreenchange");
	await desk.settle();
	check("a fullscreenchange it did not cause moves nothing", [desk.route(), desk.left], ["sales-pipeline/tv", false]);
});

test("Sales Pipeline: every show replaces its hide handler rather than adding one", async () => {
	const desk = await pipeline();
	await desk.tap(() => desk.page.buttons["TV Mode"]());
	await desk.back();
	await desk.forward();
	const hides = desk.wrapper("sales-pipeline").__events.filter((h) => h.type === "hide" && h.ns === "sales_pipeline_tv");
	check("one namespaced hide handler", hides.length, 1);
});

// ---------------------------------------------------------------------------- QuickBooks Record Matching

async function matching(url) {
	const desk = makeDesk();
	desk.respond.get_match_queue = () => ({ rows: [], total: 0, counts: {}, page_length: 50 });
	desk.respond.get_parked_transactions = () => ({
		rows: [{ entity_type: "Invoice", qbo_id: "1", reason: "x" }],
		total: 1,
		counts: { Invoice: 1 },
		page_length: 50,
	});
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route(url || "quickbooks-record-matching"));
	return desk;
}

const panelShown = (desk) =>
	desk.calls.filter((c) => c.method === "removeAttr" && /^\[data-panel='/.test(c.label)).map((c) => c.label.match(/'(\w+)'/)[1]).pop();

test("Record Matching: the tabs are entries; Back and Forward move between them", async () => {
	const desk = await matching();
	check("Masters on the bare route", [desk.route(), panelShown(desk)], ["quickbooks-record-matching", "masters"]);
	const click = desk.handler("click", "[data-tab]");
	await desk.tap(() => click({ currentTarget: { __attrs: { "data-tab": "transactions" } } }));
	check("Parked transactions is a route", [desk.route(), panelShown(desk)], ["quickbooks-record-matching/transactions", "transactions"]);
	check("loaded once", desk.sent("get_parked_transactions").length, 1);
	await desk.back();
	check("Back: Masters", [desk.route(), panelShown(desk)], ["quickbooks-record-matching", "masters"]);
	await desk.forward();
	check("Forward: Parked transactions, rows kept (no refetch)", [panelShown(desk), desk.sent("get_parked_transactions").length], ["transactions", 1]);
	check("the queue was never refetched by a tab", desk.sent("get_match_queue").length, 1);
	await desk.tap(() => click({ currentTarget: { __attrs: { "data-tab": "transactions" } } }));
	check("the tab already showing adds no entry", [desk.at().index, desk.at().length], [2, 3]);
	await desk.back();
	await desk.back();
	check("then Back leaves the page", desk.current(), "home");
	check("no push without a tap", desk.violations, []);
});

test("Record Matching: a reload on /transactions opens that tab", async () => {
	const desk = makeDesk();
	desk.respond.get_match_queue = () => ({ rows: [], total: 0, counts: {}, page_length: 50 });
	desk.respond.get_parked_transactions = () => ({ rows: [], total: 0, counts: {}, page_length: 50 });
	await desk.load("/desk/quickbooks-record-matching/transactions");
	check("Parked transactions, loaded", [panelShown(desk), desk.sent("get_parked_transactions").length], ["transactions", 1]);
});

// ---------------------------------------------------------------------------- Question Review

async function review(url) {
	const desk = makeDesk();
	desk.respond.get_review_lesson = (args) => ({
		queue: { total: 3, reviewed: 0, courses: [{ course: "C1", course_title: "One", pending: 2, lessons: 1 }] },
		lesson: {
			lesson: args.lesson || (args.course ? "L-" + args.course : "L-HEAD"),
			course: args.course || "C1",
			course_title: "One",
			questions: [],
			blocks: [],
		},
	});
	desk.respond.get_review_queue = () => ({ total: 3, reviewed: 0, courses: [] });
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route(url || "training-review"));
	return desk;
}

const tr = (desk) => desk.wrapper("training-review").training_review;
const lessons = (desk) => desk.sent("get_review_lesson").map((r) => r.args.lesson || "course:" + (r.args.course || ""));

test("Question Review: a course filter and a jumped-to lesson are entries", async () => {
	const desk = await review();
	check("the whole queue", [desk.route(), lessons(desk)], ["training-review", ["course:"]]);
	await desk.tap(() => tr(desk).pick_course("C2"));
	check("the filter is a route", [desk.route(), lessons(desk).slice(-1)], ["training-review/course/C2", ["course:C2"]]);
	await desk.tap(() => tr(desk).jump_to_lesson());
	await desk.tap(() => desk.dialog("Which lesson?").submit({ lesson: "LES-9" }));
	check("the lesson is a route", [desk.route(), lessons(desk).slice(-1)], ["training-review/lesson/LES-9", ["LES-9"]]);
	await desk.back();
	check("Back: C2 again, and its filter", [desk.route(), lessons(desk).slice(-1), tr(desk).course_filter], ["training-review/course/C2", ["course:C2"], "C2"]);
	await desk.back();
	check("Back: the whole queue", [desk.route(), lessons(desk).slice(-1), tr(desk).course_filter], ["training-review", ["course:"], null]);
	await desk.forward();
	check("Forward: C2", lessons(desk).slice(-1), ["course:C2"]);
	check("no push without a tap", desk.violations, []);
});

test("Question Review: a show that changes nothing only tops the numbers up", async () => {
	const desk = await review();
	const loads = lessons(desk).length;
	await desk.tap(() => desk.frappe.set_route("List", "Training Course"));
	await desk.back();
	check("back on the page: no reload, a queue top-up", [lessons(desk).length, desk.sent("get_review_queue").length], [loads, 1]);
});

test("Question Review: an emptied jumped-to lesson steps back to where it was opened from", async () => {
	const desk = await review();
	await desk.tap(() => tr(desk).pick_course("C2"));
	await desk.tap(() => tr(desk).jump_to_lesson());
	await desk.tap(() => desk.dialog("Which lesson?").submit({ lesson: "LES-9" }));
	check("the lesson's entry is marked as this page's", desk.ctx.history.state, { tq_opened: "LES-9" });
	tr(desk).maybe_advance();
	await desk.settle();
	check("back on C2's own entry, nothing added", [desk.route(), desk.at().index, desk.at().length], ["training-review/course/C2", 2, 4]);
	check("and the queue loaded", lessons(desk).slice(-1), ["course:C2"]);
	await desk.back();
	check("Back goes on to the view before, not to a copy of C2", desk.route(), "training-review");
	check("no push without a tap", desk.violations, []);
});

test("Question Review: an emptied lesson from a pasted link hands its entry to the queue", async () => {
	const desk = makeDesk();
	desk.respond.get_review_lesson = (args) => ({
		queue: { total: 1, reviewed: 0, courses: [] },
		lesson: { lesson: args.lesson || "L-HEAD", course: "C1", questions: [], blocks: [] },
	});
	await desk.load("/desk/training-review/lesson/LES-4");
	check("loaded LES-4", desk.sent("get_review_lesson").map((r) => r.args), [{ lesson: "LES-4" }]);
	tr(desk).maybe_advance();
	await desk.settle();
	check("replaced by the queue, one entry, nothing left behind", [desk.route(), desk.at().length, desk.left], ["training-review", 1, false]);
	check("and the queue loaded", desk.sent("get_review_lesson").map((r) => r.args).slice(-1), [{}]);
});

test("Question Review: Back while a lesson is loading is caught up when it lands", async () => {
	const desk = await review();
	await desk.tap(() => tr(desk).pick_course("C2"));
	const answer = desk.respond.get_review_lesson;
	delete desk.respond.get_review_lesson; // the next load hangs
	await desk.tap(() => tr(desk).pick_course("C3"));
	check("C3 is loading", [desk.route(), tr(desk).loading], ["training-review/course/C3", true]);
	await desk.back();
	check("Back found a load running", tr(desk).loading, true);
	desk.respond.get_review_lesson = answer;
	desk.requests.filter((r) => r.method === "get_review_lesson" && !r.settled).forEach((r) => r.resolve(answer(r.args)));
	await desk.settle();
	check("the route won", [desk.route(), lessons(desk).slice(-1), tr(desk).course_filter], ["training-review/course/C2", ["course:C2"], "C2"]);
});

test("Question Review: Back asks before it throws away a half-written edit", async () => {
	const desk = await review();
	await desk.tap(() => tr(desk).pick_course("C2"));
	await desk.tap(() => tr(desk).pick_course("C3"));
	tr(desk).cards = [{ editing: true }];
	const loads = lessons(desk).length;
	await desk.back();
	check("asked", desk.shown(), ["Confirm"]);
	await desk.tap(() => desk.dialog("Confirm").answer(false));
	check("staying loads nothing: the edit is still on screen", [lessons(desk).length, tr(desk).view.course], [loads, "C3"]);
	check("… and the entry Back moved to is left as it was", [desk.route(), desk.urls()], ["training-review/course/C2", ["/desk/home", "/desk/training-review", "/desk/training-review/course/C2", "/desk/training-review/course/C3"]]);
	await desk.forward();
	check("Forward returns to the entry that matches: nothing to ask", [desk.shown(), lessons(desk).length], [[], loads]);
	await desk.back();
	check("Back asks again", desk.shown(), ["Confirm"]);
	await desk.tap(() => desk.dialog("Confirm").answer(true));
	check("leaving loads the view Back asked for", [lessons(desk).slice(-1), desk.route(), tr(desk).course_filter], [["course:C2"], "training-review/course/C2", "C2"]);
});

test("Question Review: a verdict landing after the reviewer left does not route them back", async () => {
	const desk = await review();
	await desk.tap(() => tr(desk).jump_to_lesson());
	await desk.tap(() => desk.dialog("Which lesson?").submit({ lesson: "LES-9" }));
	await desk.tap(() => desk.frappe.set_route("List", "Training Course"));
	tr(desk).maybe_advance();
	await desk.settle();
	check("still where the reviewer went", desk.current(), "training-course");
});

// C2's lesson holds one question, whose card has corrections typed into it, and a
// save-and-accept of it is sent. The reply is held until the test lets it land.
async function reviewSavingAnEdit() {
	const desk = await review();
	const lessonFor = desk.respond.get_review_lesson;
	desk.respond.get_review_lesson = (args) => {
		const data = lessonFor(args);
		if (args.course === "C2") {
			data.lesson.questions = [{ question: "Q1", question_type: "Multiple Choice", question_text: "Why?", options: [] }];
		}
		return data;
	};
	await desk.tap(() => tr(desk).pick_course("C2"));
	tr(desk).cards[0].editing = true;
	desk.press(() => tr(desk).accept("Q1", { question: "Q1", question_text: "Why not?" }));
	await desk.settle();
	return desk;
}

test("Question Review: Back during a save-and-accept waits for it, then follows the route", async () => {
	const desk = await reviewSavingAnEdit();
	check("the card is out of the pane while its verdict is in flight", [tr(desk).cards.length, tr(desk).inflight], [0, 1]);
	const loads = lessons(desk).length;
	await desk.back();
	check("Back to the whole queue loads nothing yet, and asks nothing", [desk.route(), lessons(desk).length, desk.shown()], ["training-review", loads, []]);
	desk.requests.filter((r) => r.method === "accept_question").forEach((r) => r.resolve({ remaining: { reviewed: 1 } }));
	await desk.settle();
	check("once it has landed, the view Back asked for loads", [lessons(desk).length, lessons(desk).slice(-1), tr(desk).course_filter], [loads + 1, ["course:"], null]);
});

test("Question Review: a refused save-and-accept comes back, and the held Back asks before painting over it", async () => {
	const desk = await reviewSavingAnEdit();
	const loads = lessons(desk).length;
	await desk.back();
	desk.requests.filter((r) => r.method === "accept_question").forEach((r) => r.reject(new Error("refused")));
	await desk.settle();
	check("the card is back, still being edited", [tr(desk).cards.length, tr(desk).cards[0] && tr(desk).cards[0].editing], [1, true]);
	check("Back's view is asked about, not loaded over it", [desk.shown(), lessons(desk).length], [["Confirm"], loads]);
	await desk.tap(() => desk.dialog("Confirm").answer(false));
	check("staying keeps C2 and the edit on screen", [lessons(desk).length, tr(desk).view.course, tr(desk).cards.length], [loads, "C2", 1]);
});

// C2's lesson holds Q1 and Q2, and a lesson opened by name holds one question of its own. All are
// Short Answer with an answer, so nothing blocks an accept.
async function reviewWithQuestions() {
	const desk = await review();
	const lessonFor = desk.respond.get_review_lesson;
	desk.respond.get_review_lesson = (args) => {
		const data = lessonFor(args);
		const names = args.lesson ? ["Q-" + args.lesson] : args.course === "C2" ? ["Q1", "Q2"] : [];
		data.lesson.questions = names.map((question) => ({
			question,
			question_type: "Short Answer",
			question_text: "Why?",
			correct_text_answers: "Because",
		}));
		return data;
	};
	return desk;
}

const verdictsLand = (desk) =>
	desk.requests.filter((r) => r.method === "accept_question" && !r.settled).forEach((r) => r.resolve({ remaining: { reviewed: 1 } }));

// Back from C2 with Q1 being edited, and "Stay"; then Forward to C2 and the edit given up.
async function reviewAfterAStay() {
	const desk = await reviewWithQuestions();
	await desk.tap(() => tr(desk).pick_course("C2"));
	tr(desk).edit("Q1");
	check("Q1 is being edited", tr(desk).cards.map((rec) => rec.editing), [true, false]);
	await desk.back();
	await desk.tap(() => desk.dialog("Confirm").answer(false));
	check("stayed: C2 and the edit on screen, under the queue's entry", [desk.route(), tr(desk).view.course], ["training-review", "C2"]);
	await desk.forward();
	check("Forward: the route is C2 again, nothing asked", [desk.route(), desk.shown()], ["training-review/course/C2", []]);
	tr(desk).cancel_edit(tr(desk).cards[0]);
	return desk;
}

test("Question Review: a Stay is forgotten once the route moves on, so a later Back held for a verdict is followed", async () => {
	const desk = await reviewAfterAStay();
	desk.press(() => tr(desk).accept("Q2"));
	await desk.settle();
	check("Q2's verdict is in flight", tr(desk).inflight, 1);
	const loads = lessons(desk).length;
	await desk.back();
	check("Back to the queue waits for it", [desk.route(), lessons(desk).length], ["training-review", loads]);
	verdictsLand(desk);
	await desk.settle();
	check("once it lands, the queue Back asked for loads", [lessons(desk).length, lessons(desk).slice(-1), tr(desk).course_filter], [loads + 1, ["course:"], null]);
});

test("Question Review: a Stay is forgotten once the route moves on, so a later Back during a load is followed", async () => {
	const desk = await reviewAfterAStay();
	const release = hold(desk, "get_review_lesson");
	await desk.tap(() => desk.page.buttons["Refresh"]());
	check("C2 is loading again", [tr(desk).loading, lessons(desk).slice(-1)], [true, ["course:C2"]]);
	const loads = lessons(desk).length;
	await desk.back();
	check("Back found the load running", [desk.route(), tr(desk).loading], ["training-review", true]);
	release();
	await desk.settle();
	check("once it lands, the queue Back asked for loads", [lessons(desk).length, lessons(desk).slice(-1), tr(desk).course_filter], [loads + 1, ["course:"], null]);
});

test("Question Review: a lesson stayed on that empties opens the view the route names, not the one behind it", async () => {
	const desk = await reviewWithQuestions();
	for (const lesson of ["L0", "L1"]) {
		await desk.tap(() => tr(desk).jump_to_lesson());
		await desk.tap(() => desk.dialog("Which lesson?").submit({ lesson }));
	}
	check("two lessons, each an entry", [desk.route(), desk.at().index, desk.at().length], ["training-review/lesson/L1", 3, 4]);
	tr(desk).edit("Q-L1");
	await desk.back();
	await desk.tap(() => desk.dialog("Confirm").answer(false));
	check("stayed on L1 under L0's entry", [desk.route(), tr(desk).view.lesson], ["training-review/lesson/L0", "L1"]);
	tr(desk).cancel_edit(tr(desk).cards[0]);
	desk.press(() => tr(desk).accept("Q-L1"));
	await desk.settle();
	verdictsLand(desk);
	await desk.settle();
	check("L1 has emptied, and L0 (the route) loads", [lessons(desk).slice(-1), tr(desk).view.lesson], [["L0"], "L0"]);
	check("on L0's own entry: nothing stepped back past it", [desk.route(), desk.at().index, desk.at().length], ["training-review/lesson/L0", 2, 4]);
	check("no push without a tap", desk.violations, []);
});

// ---------------------------------------------------------------------------- Location Timeline

function timeline(desk) {
	const LT = desk.ctx.erpnext_enhancements.workforce.LocationTimeline;
	const lt = Object.create(LT.prototype);
	Object.assign(lt, {
		page: desk.makeAppPage(),
		wrapper: null,
		mode: "trail",
		// A map that has loaded: the pickers' changes load a trail only once there is one.
		map: {},
		data: null,
		filters: null,
		loadSeq: 0,
		silence: 0,
		employees: [{ value: "E1", label: "E1" }],
		live: { timer: null, data: { employees: [{ employee: "E2", employee_name: "Bo" }] } },
		$root: desk.$("lt-root"),
		employeeField: desk.makeField({ fieldname: "employee" }),
		fromField: desk.makeField({ fieldname: "from_date", default: "2026-09-24" }),
		toField: desk.makeField({ fieldname: "to_date", default: "2026-09-24" }),
		log: [],
		gate: Promise.resolve(),
	});
	lt.init = () => lt.gate;
	["pausePlayback", "renderTrail", "renderHint", "applyAccuracyLayer", "clearMapLayers", "startLivePolling", "stopLivePolling"].forEach((name) => {
		lt[name] = () => lt.log.push(name);
	});
	// The real loadTrail's bookkeeping, without the server: what it asked for, and (the reply
	// having landed) a trail drawn for it. No one picked is the hint, and nothing is asked.
	lt.loadTrail = () => {
		const employee = lt.employeeField.get_value();
		if (!employee) return lt.log.push("hint");
		const from = lt.fromField.get_value();
		const to = lt.toField.get_value() || from;
		lt.filters = { employee, from_datetime: from + " 00:00:00", to_datetime: to + " 23:59:59" };
		lt.data = { intervals: [] };
		lt.log.push(`loadTrail:${employee}:${from}:${to}`);
	};
	lt.loads = () => lt.log.filter((x) => x.startsWith("loadTrail:"));
	return lt;
}

async function locationTimeline(url, options) {
	const desk = makeDesk(
		Object.assign({}, options || {}, {
			onLoad: {
				// The desk the page is loading on: after a reload, that is the new one.
				"location-timeline": (wrapper, on) => {
					wrapper.location_timeline = timeline(on);
				},
			},
		})
	);
	await desk.load(url || "/desk/home");
	if (!url) await desk.tap(() => desk.frappe.set_route("location-timeline"));
	return desk;
}

const lt = (desk) => desk.wrapper("location-timeline").location_timeline;

test("Location Timeline: the modes are entries; Back from a Live row's trail returns to Live", async () => {
	const desk = await locationTimeline();
	check("Trail on the bare route", [desk.route(), lt(desk).mode], ["location-timeline", "trail"]);
	await desk.tap(() => lt(desk).pickMode("live"));
	check("Live is a route", [desk.route(), lt(desk).mode], ["location-timeline/live", "live"]);
	await desk.tap(() => lt(desk).openTrailFor(0));
	check("a Live row opens the trail as an entry", [desk.route(), lt(desk).mode, lt(desk).employeeField.value], ["location-timeline", "trail", "E2"]);
	await desk.back();
	check("Back: the Live list", [desk.route(), lt(desk).mode], ["location-timeline/live", "live"]);
	await desk.forward();
	check("Forward: the trail", [desk.route(), lt(desk).mode], ["location-timeline", "trail"]);
	await desk.tap(() => lt(desk).pickMode("trail"));
	check("the mode already showing adds no entry", desk.at().length, 4);
	check("no push without a tap", desk.violations, []);
});

test("Location Timeline: a reload on /live opens Live", async () => {
	const desk = await locationTimeline("/desk/location-timeline/live");
	check("Live", lt(desk).mode, "live");
});

test("Location Timeline: the route is read when the map finishes loading, not before", async () => {
	const desk = await locationTimeline();
	let open;
	lt(desk).gate = new Promise((resolve) => (open = resolve));
	await desk.tap(() => lt(desk).pickMode("live"));
	await desk.back();
	check("still loading", lt(desk).mode, "live");
	open();
	await desk.settle();
	check("the route at that moment wins", [desk.route(), lt(desk).mode], ["location-timeline", "trail"]);
});

test("Location Timeline: the Employee form's route_options still open a trail", async () => {
	const desk = await locationTimeline();
	await desk.tap(() => lt(desk).pickMode("live"));
	await desk.tap(() => desk.frappe.set_route("employee", "E7"));
	desk.frappe.route_options = { employee: "E7" };
	await desk.tap(() => desk.frappe.set_route("location-timeline"));
	check("applied, in Trail", [lt(desk).employeeField.value, lt(desk).loads(), lt(desk).mode], ["E7", ["loadTrail:E7:2026-09-24:2026-09-24"], "trail"]);
	check("the trail is in the address, corrected in place", desk.at().url, "/desk/location-timeline?employee=E7&from_date=2026-09-24&to_date=2026-09-24");
	check("route_options consumed", desk.frappe.route_options, null);
});

// The trail in the address (v1.662.0). Each runs under the router before v16.50, which pushes
// the path only, and after, which pushes the query with it.
const ROUTERS = [
	["v16 before 50", {}],
	["v16.50+", { queryInUrl: true }],
];
const TRAIL = (e, from, to) => `/desk/location-timeline?employee=${e}&from_date=${from}&to_date=${to || from}`;

ROUTERS.forEach(([router, options]) => {
	test(`Location Timeline (${router}): a pasted trail address opens that trail; a reload keeps it`, async () => {
		let desk = await locationTimeline(TRAIL("E1", "2026-09-20", "2026-09-21"), options);
		check("the trail", [lt(desk).employeeField.value, lt(desk).fromField.value, lt(desk).toField.value], ["E1", "2026-09-20", "2026-09-21"]);
		check("loaded once", lt(desk).loads(), ["loadTrail:E1:2026-09-20:2026-09-21"]);
		check("the address untouched, no entry added", [desk.at().url, desk.at().length], [TRAIL("E1", "2026-09-20", "2026-09-21"), 1]);
		check("route_options consumed", desk.frappe.route_options, null);
		desk = await desk.reload();
		check("a reload: the same trail", [lt(desk).employeeField.value, lt(desk).fromField.value, lt(desk).toField.value], ["E1", "2026-09-20", "2026-09-21"]);
	});

	test(`Location Timeline (${router}): each trail picked is an entry; Back and Forward walk them`, async () => {
		const desk = await locationTimeline(null, options);
		check("bare Trail", [desk.at().url, lt(desk).employeeField.value], ["/desk/location-timeline", ""]);
		lt(desk).employeeField.value = "E1";
		await desk.tap(() => lt(desk).onFilterChange());
		check("a person picked: an entry with the trail in it", [desk.at().url, desk.at().length], [TRAIL("E1", "2026-09-24"), 3]);
		lt(desk).fromField.value = "2026-09-20";
		await desk.tap(() => lt(desk).onFilterChange());
		check("a date picked: another", [desk.at().url, desk.at().length], [TRAIL("E1", "2026-09-20", "2026-09-24"), 4]);
		lt(desk).setMode("trail", { force: true }); // puts up the Load button
		await desk.tap(() => lt(desk).page.buttons.Load());
		check("Load on the trail already shown adds nothing", desk.at().length, 4);
		await desk.back();
		check("Back: the trail before", [lt(desk).fromField.value, desk.at().url], ["2026-09-24", TRAIL("E1", "2026-09-24")]);
		await desk.back();
		check("Back onto the bare address: no one's trail", [lt(desk).employeeField.value, lt(desk).data, lt(desk).log.includes("hint")], ["", null, true]);
		await desk.forward();
		check("Forward: the trail again", [lt(desk).employeeField.value, lt(desk).fromField.value], ["E1", "2026-09-24"]);
		check("no push without a tap", desk.violations, []);
	});

	test(`Location Timeline (${router}): a Live row's trail is in its address; Back and Forward reload nothing`, async () => {
		const desk = await locationTimeline(null, options);
		await desk.tap(() => lt(desk).pickMode("live"));
		await desk.tap(() => lt(desk).openTrailFor(0));
		check("the trail's own entry", [desk.at().url, desk.at().length, lt(desk).mode], [TRAIL("E2", "2026-09-24"), 4, "trail"]);
		await desk.back();
		check("Back: Live", [desk.route(), lt(desk).mode], ["location-timeline/live", "live"]);
		await desk.tap(() => lt(desk).pickMode("trail"));
		check("the Trail button returns to that trail, as a new entry", [desk.at().url, desk.at().length, lt(desk).employeeField.value], [TRAIL("E2", "2026-09-24"), 4, "E2"]);
		await desk.back();
		await desk.forward();
		check("Forward onto it: shown, not loaded again", [lt(desk).mode, lt(desk).loads().length], ["trail", 1]);
		check("no push without a tap", desk.violations, []);
	});

	test(`Location Timeline (${router}): a malformed date is today, and the address is corrected in place`, async () => {
		const desk = await locationTimeline("/desk/location-timeline?employee=E1&from_date=2026-02-30&to_date=yesterday", options);
		check("today, quietly", [lt(desk).fromField.value, lt(desk).toField.value, desk.messages], ["2026-09-24", "2026-09-24", []]);
		check("corrected in place", [desk.at().url, desk.at().length], [TRAIL("E1", "2026-09-24"), 1]);
	});

	test(`Location Timeline (${router}): another page's route options open the trail and reach the address`, async () => {
		const desk = await locationTimeline(null, options);
		await desk.tap(() => desk.frappe.set_route("job-interval", "JI-1"));
		await desk.tap(() => desk.frappe.set_route("location-timeline", { employee: "E1", from_date: "2026-09-10", to_date: "2026-09-11" }));
		check("the trail", [lt(desk).employeeField.value, lt(desk).fromField.value, lt(desk).toField.value], ["E1", "2026-09-10", "2026-09-11"]);
		check("in the address", desk.at().url, TRAIL("E1", "2026-09-10", "2026-09-11"));
		check("route_options consumed", desk.frappe.route_options, null);
		await desk.tap(() => desk.frappe.set_route("home"));
		await desk.tap(() => desk.frappe.set_route("location-timeline"));
		check("a later plain route replays nothing", [lt(desk).employeeField.value, desk.at().url], ["", "/desk/location-timeline"]);
	});
});

test("Location Timeline: an employee param carrying markup never reaches the picker", async () => {
	const evil = '<img src=x onerror="alert(1)">';
	const desk = await locationTimeline(`/desk/location-timeline?employee=${encodeURIComponent(evil)}&from_date=2026-09-20`);
	const lti = lt(desk);
	check("not picked", lti.employeeField.value, "");
	check("not admitted to the list", lti.employees.map((e) => e.value), ["E1"]);
	check("no label anywhere carries markup", lti.employees.some((e) => /[<>"]/.test(e.label)), false);
	check("no request for it", lti.loads(), []);
	check("the address corrected to the bare one, in place", [desk.at().url, desk.at().length], ["/desk/location-timeline", 1]);
	const html = desk.calls.filter((c) => c.method === "html" || c.method === "append").map((c) => String(c.args[0]));
	check("nothing written to the page holds it", html.some((h) => h.includes("onerror")), false);
	check("quietly", desk.messages, []);
});

test("Location Timeline: an unlisted employee that looks like a name is admitted, its label plain text", async () => {
	const desk = await locationTimeline("/desk/location-timeline?employee=HR-EMP-00042&from_date=2026-09-20");
	const lti = lt(desk);
	check("picked and loaded", [lti.employeeField.value, lti.loads()], ["HR-EMP-00042", ["loadTrail:HR-EMP-00042:2026-09-20:2026-09-20"]]);
	check("admitted with its name as the label", lti.employees.slice(-1), [{ value: "HR-EMP-00042", label: "HR-EMP-00042" }]);
});

// ---------------------------------------------------------------------------- Maintenance Day Board

// The REAL page script, on the loader's plain stand-in kit (so its calls go through frappe.call).
// Each reply names the day it is for, so a test can tell which day's board was drawn.
function dayBoardDesk(options) {
	const desk = makeDesk(options);
	desk.respond.get_day_board_data = (args) => ({
		scheduled: [{ name: `SMR-${args.date || "today"}` }],
		in_progress: [],
		submitted_today: [],
		flagged: [],
	});
	return desk;
}

const mdb = (desk) => desk.wrapper("maintenance-day-board").mdb_day_board;
const asked = (desk) => desk.sent("get_day_board_data").map((r) => r.args.date || "today");
// The board as last drawn: the markup of the last html() written into it.
const drawn = (desk) => {
	const writes = desk.calls.filter((c) => c.method === "html" && /mdb-board/.test(c.label));
	return writes.length ? String(writes[writes.length - 1].args[0]) : "";
};
const BOARD = "/desk/maintenance-day-board";

ROUTERS.forEach(([router, options]) => {
	test(`Day Board (${router}): a pasted ?date= opens that day; a reload keeps it`, async () => {
		let desk = dayBoardDesk(options);
		await desk.load(`${BOARD}?date=2026-09-20`);
		check("that day", [mdb(desk).day(), asked(desk)], ["2026-09-20", ["2026-09-20"]]);
		check("its board", drawn(desk).includes("SMR-2026-09-20"), true);
		check("the address untouched, no entry added", [desk.at().url, desk.at().length], [`${BOARD}?date=2026-09-20`, 1]);
		check("route_options consumed", desk.frappe.route_options, null);
		desk = await desk.reload();
		check("a reload: the same day", [mdb(desk).day(), asked(desk)], ["2026-09-20", ["2026-09-20"]]);
	});

	test(`Day Board (${router}): no date is today; each day stepped to is an entry; Back and Forward walk them`, async () => {
		const desk = dayBoardDesk(options);
		await desk.load("/desk/home");
		await desk.tap(() => desk.frappe.set_route("maintenance-day-board"));
		check("today, on the bare address, asked of the server unnamed", [mdb(desk).day(), desk.at().url, asked(desk)], ["2026-09-24", BOARD, ["today"]]);
		await desk.tap(() => mdb(desk).step(-1));
		check("‹: the day before, its own entry", [mdb(desk).day(), desk.at().url, desk.at().length], ["2026-09-23", `${BOARD}?date=2026-09-23`, 3]);
		await desk.tap(() => mdb(desk).step(-1));
		check("‹ again", [desk.at().url, desk.at().length, drawn(desk).includes("SMR-2026-09-22")], [`${BOARD}?date=2026-09-22`, 4, true]);
		await desk.back();
		check("Back: the day before it", [mdb(desk).day(), desk.at().url, drawn(desk).includes("SMR-2026-09-23")], ["2026-09-23", `${BOARD}?date=2026-09-23`, true]);
		await desk.back();
		check("Back: today", [mdb(desk).day(), desk.at().url, drawn(desk).includes("SMR-today")], ["2026-09-24", BOARD, true]);
		await desk.forward();
		check("Forward: the 23rd again", [mdb(desk).day(), desk.at().index], ["2026-09-23", 2]);
		await desk.tap(() => mdb(desk).go("2026-09-24"));
		check("Today is the bare address, as an entry", [desk.at().url, desk.at().length], [BOARD, 4]);
		await desk.tap(() => mdb(desk).go("2026-09-24"));
		check("the day already shown adds nothing", desk.at().length, 4);
		await desk.tap(() => mdb(desk).step(1));
		check("›: tomorrow", desk.at().url, `${BOARD}?date=2026-09-25`);
		await desk.back();
		await desk.back();
		await desk.back();
		await desk.back();
		check("and Back leaves the board", desk.current(), "home");
		check("no push without a tap", desk.violations, []);
	});

	test(`Day Board (${router}): a malformed date or today's own is today, corrected in place`, async () => {
		for (const bad of ["2026-02-30", "garbage", "2026-9-1", "2026-09-24"]) {
			const desk = dayBoardDesk(options);
			await desk.load(`${BOARD}?date=${bad}`);
			check(`?date=${bad}: today, quietly`, [mdb(desk).day(), asked(desk), desk.messages, desk.alerts], ["2026-09-24", ["today"], [], []]);
			check(`?date=${bad}: the bare address, no entry`, [desk.at().url, desk.at().length], [BOARD, 1]);
		}
	});

	test(`Day Board (${router}): another page's route options open that day and reach the address`, async () => {
		const desk = dayBoardDesk(options);
		await desk.load("/desk/home");
		await desk.tap(() => desk.frappe.set_route("maintenance-day-board", { date: "2026-09-10" }));
		check("that day", [mdb(desk).day(), drawn(desk).includes("SMR-2026-09-10")], ["2026-09-10", true]);
		check("in the address", desk.at().url, `${BOARD}?date=2026-09-10`);
		check("route_options consumed", desk.frappe.route_options, null);
		await desk.tap(() => desk.frappe.set_route("home"));
		await desk.tap(() => desk.frappe.set_route("maintenance-day-board"));
		check("a later plain route replays nothing: today", [mdb(desk).day(), desk.at().url], ["2026-09-24", BOARD]);
		await desk.back();
		await desk.back();
		check("Back to the dated entry: that day again", [mdb(desk).day(), desk.at().url], ["2026-09-10", `${BOARD}?date=2026-09-10`]);
	});

	test(`Day Board (${router}): a slow reply for the day left never paints over the day shown`, async () => {
		const desk = dayBoardDesk(options);
		await desk.load(BOARD);
		const release = hold(desk, "get_day_board_data");
		await desk.tap(() => mdb(desk).step(-1));
		await desk.tap(() => mdb(desk).step(-1));
		check("both days asked for, the new one not held up by the old", asked(desk), ["today", "2026-09-23", "2026-09-22"]);
		release();
		await desk.settle();
		check("the day shown is the one drawn", [mdb(desk).day(), drawn(desk).includes("SMR-2026-09-22")], ["2026-09-22", true]);
		check("one entry per day", desk.at().length, 3);
	});
});

// The Today button's state as last drawn: disabled on today.
const todayDisabled = (desk) => {
	const props = desk.calls.filter((c) => c.label === ".mdb-today" && c.method === "prop" && c.args[0] === "disabled");
	return props.length ? props[props.length - 1].args[1] : null;
};

test("Day Board: a past day is not polled; today and a later day are, and the poll comes back with them", async () => {
	const desk = dayBoardDesk({ intervals: true });
	await desk.load(`${BOARD}?date=2026-09-20`);
	check("a past day: no poll", desk.liveIntervals(), 0);
	await desk.wait(180000);
	check("and three minutes ask nothing more", asked(desk), ["2026-09-20"]);
	await desk.tap(() => mdb(desk).go("2026-09-24"));
	check("back to today: polled again", desk.liveIntervals(), 1);
	await desk.wait(60000);
	check("the poll asks for today", asked(desk).slice(-1), ["today"]);
	await desk.tap(() => mdb(desk).step(1));
	check("a later day is polled (drafts can still be scheduled for it)", desk.liveIntervals(), 1);
	await desk.tap(() => mdb(desk).step(-2));
	check("a past day stops it", desk.liveIntervals(), 0);
	await desk.back();
	check("Back onto the later day starts it again", [mdb(desk).day(), desk.liveIntervals()], ["2026-09-25", 1]);
});

test("Day Board: a later day left on screen past midnight becomes the bare today", async () => {
	const desk = dayBoardDesk({ intervals: true });
	await desk.load(`${BOARD}?date=2026-09-25`);
	check("the 25th, Today enabled, Submitted", [mdb(desk).day(), todayDisabled(desk), /Submitted Today/.test(drawn(desk))], ["2026-09-25", false, false]);
	desk.today = "2026-09-25"; // midnight
	await desk.wait(60000);
	check("now today, on the bare address, corrected in place", [mdb(desk).day(), desk.at().url, desk.at().length], ["2026-09-25", BOARD, 1]);
	check("asked of the server as today", asked(desk).slice(-1), ["today"]);
	check("Today disabled, Submitted Today", [todayDisabled(desk), /Submitted Today/.test(drawn(desk))], [true, true]);
	desk.today = "2026-09-26";
	await desk.wait(60000);
	check("and it follows the clock from here", mdb(desk).day(), "2026-09-26");
	check("no push without a tap", desk.violations, []);
});

test("Day Board: a draft visit links to its record and to the Visit Wizard, both in the Desk", async () => {
	const desk = dayBoardDesk();
	await desk.load(BOARD);
	const html = drawn(desk);
	check("the record", html.includes('href="/desk/sapphire-maintenance-record/SMR-today"'), true);
	check("the wizard", html.includes('href="/desk/visit-wizard/SMR-today"'), true);
	check("no /app link", /href="\/app\//.test(html), false);
});

// ---------------------------------------------------------------------------- run

(async () => {
	scannerKit = await import(pathToFileURL(SCANNER_KIT).href);
	reorderMods = {
		logic: await import(pathToFileURL(path.join(REORDER_DIR, "logic.js")).href),
		editor: await import(pathToFileURL(path.join(REORDER_DIR, "editor.js")).href),
	};
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
