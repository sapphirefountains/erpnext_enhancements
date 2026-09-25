#!/usr/bin/env node
/**
 * Browser Back / Forward on six Desk pages, run against a model of Frappe v16's router.
 *
 * Loads the REAL page scripts — Device Console, Inventory Scanner Audit, Sales Pipeline,
 * QuickBooks Record Matching, Question Review and Location Timeline — into a vm context over a
 * small fake Desk, then taps, scans and presses Back and Forward. Plain node, no runner, no
 * npm install.
 *
 * The Desk is modelled on `git show origin/version-16:frappe/public/js/frappe/…`, never the
 * develop tree, in the places these pages depend on:
 *
 *   - router.js: `popstate` runs `frappe.router.route()`, which awaits `parse()`, sets
 *     `current_route`, then `set_history()` (which closes the open dialog), `render()` and
 *     triggers "change". `set_route()` pushes the PATH only (`history.pushState(null, null,
 *     path)`), replaces instead when `frappe.route_flags.replace_route` is set, skips an
 *     unchanged path, and resolves after a 100 ms timeout and `frappe.after_ajax`, clearing
 *     `route_flags` only then. `after_ajax` runs at once here unless the desk is made with
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

const APP = path.join(__dirname, "..", "erpnext_enhancements");
const SCRIPTS = {
	"device-console": "device_management/page/device_console/device_console.js",
	"inventory-scanner-audit": "inventory_enhancements/page/inventory_scanner_audit/inventory_scanner_audit.js",
	"sales-pipeline": "crm_enhancements/page/sales_pipeline/sales_pipeline.js",
	"quickbooks-record-matching": "quickbooks_online/page/quickbooks_record_matching/quickbooks_record_matching.js",
	"training-review": "training/page/training_review/training_review.js",
	"location-timeline": "workforce/page/location_timeline/location_timeline.js",
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
		createElement: (tag) => makeElement(tag),
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
	function server(method, args) {
		const req = { method: method.split(".").pop(), args: args || {}, settled: false };
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
	// request.js's waiting_for_ajax: run once no request is in flight (see `after_ajax`).
	const ajaxWaiting = [];
	function flushAjax() {
		if (desk.requests.some((r) => !r.settled)) return;
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
					route = route[0].split("/").map((c) => decodeURIComponent(c));
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
				const query = Object.entries(route_options)
					.map(([k, v]) => `${k}=` + encodeURIComponent(JSON.stringify(v)))
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
				history[method](null, null, path);
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
			if (opts.ajax && desk.requests.some((r) => !r.settled)) ajaxWaiting.push(fn);
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
			get_today: () => "2026-09-24",
			now_time: () => "12:00",
			str_to_user: (s) => s,
		},
		require(paths, cb) {
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
					req.deny = () => reject(new Error("NotAllowedError"));
				});
				desk.cameras.push(req);
				if (desk.cameraAuto === "allow") setTimeout_(() => req.allow(), 10);
				if (desk.cameraAuto === "deny") setTimeout_(() => req.deny(), 10);
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
		setInterval: () => 0,
		clearInterval() {},
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
	ctx.window = ctx;
	vm.createContext(ctx);
	desk.ctx = ctx;
	desk.frappe = frappe;

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
			if (opts.onLoad && opts.onLoad[name]) wrapper.on_page_load = opts.onLoad[name];
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
	// A tab discarded with the camera open: the console's camera has no visibilitychange close.
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

async function inventory(settings) {
	const desk = makeDesk();
	desk.respond.get_bootstrap = () => ({
		settings: Object.assign({ enable_camera_scan: 1, allow_unknown_item: 1, default_warehouse: "Stores" }, settings || {}),
		session: { name: "ICS-1", lines: [], summary: { lines: 0, with_variance: 0 } },
	});
	desk.respond.resolve_scan = ({ code }) => {
		if (code === "FAIL") return new Error("boom");
		if (code.startsWith("ITEM")) return { type: "item", item_code: code, item_name: code, system_qty: 3, uom: "Nos" };
		if (code.startsWith("LOC")) return { type: "location", storage_location: code, warehouse: "Stores" };
		return { type: "unknown" };
	};
	desk.respond.lookup_item = () => [];
	await desk.load("/desk/home");
	await desk.tap(() => desk.frappe.set_route("inventory-scanner-audit"));
	return desk;
}

const isa = (desk) => desk.wrapper("inventory-scanner-audit").inventory_scanner;

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
		map: null,
		data: null,
		silence: 0,
		employees: [{ value: "E1", label: "E1" }],
		live: { timer: null, data: { employees: [{ employee: "E2", employee_name: "Bo" }] } },
		$root: desk.$("lt-root"),
		employeeField: desk.makeField({ fieldname: "employee" }),
		fromField: desk.makeField({ fieldname: "from_date" }),
		toField: desk.makeField({ fieldname: "to_date" }),
		log: [],
		gate: Promise.resolve(),
	});
	lt.init = () => lt.gate;
	["pausePlayback", "renderTrail", "renderHint", "applyAccuracyLayer", "clearMapLayers", "loadTrail", "startLivePolling", "stopLivePolling"].forEach((name) => {
		lt[name] = () => lt.log.push(name);
	});
	lt.applyRouteOptions = (o) => {
		lt.log.push("applyRouteOptions:" + o.employee);
		lt.setMode("trail");
	};
	return lt;
}

async function locationTimeline(url) {
	const desk = makeDesk({
		onLoad: {
			"location-timeline": (wrapper) => {
				wrapper.location_timeline = timeline(desk);
			},
		},
	});
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
	check("applied, in Trail", [lt(desk).log.includes("applyRouteOptions:E7"), lt(desk).mode], [true, "trail"]);
});

// ---------------------------------------------------------------------------- run

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
