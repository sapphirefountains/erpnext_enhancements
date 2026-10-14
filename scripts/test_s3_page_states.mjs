#!/usr/bin/env node
/**
 * Custom pages S3.1 (v1.645.0): four Desk pages run against the REAL sf_kit (six since S3.2).
 *
 * The Maintenance Day Board, the Stripe and QuickBooks dashboards and the Water Engineering Wizard
 * are loaded into a vm context the way the Desk loads them: the global bundle's loader
 * (public/js/sf_kit_loader.js) is installed first, and frappe.require installs the real
 * sf_kit.bundle.js, so every state panel is drawn by the kit's own state.render and every quiet
 * call is the kit's own fetch, rejecting with the kit's own {status, message, exc_type,
 * session_expired}. A page that reads a field the kit does not set, or calls the panel with an
 * argument it does not take, fails here; the other harnesses feed pages the loader's plain stand-in.
 * (The Device Console, the Scanner Audit and the Rental Planner get the same in their own harnesses:
 * scripts/test_desk_page_history.js and scripts/test_rental_planner_history.js.)
 *
 * What this pins, per page: a first load that fails 500 draws the kit's error panel with the page's
 * title and the kit's sentence, and its Try again asks again in place; a 403 draws no-access with
 * the server's own words; a lapsed session (401 SessionExpired) draws the signed-out panel; frappe
 * puts up no dialog for any of it. And the review fixes of v1.645.0:
 *   - the Day Board draws its loading panel on the first attempt only, keeps the board (and says so
 *     once in a toast) when a timer refresh fails, and stops its timer on a 403 or a lapsed session;
 *   - the QuickBooks resync poll retries a network or server failure, stops on a 4xx with a toast,
 *     and stops on a lapsed session with the signed-out panel;
 *   - the Water wizard drops a design's reply that lands after another design was picked, and puts
 *     a design name with a quote in it into the picker as one option with that exact value;
 *   - with no loader and no kit (an old Desk tab whose kit file is gone), each page still says what
 *     failed and retries; and the loader's own stand-in reads a lapsed session as the kit does.
 *
 * S3.2 (v1.651.0, TASK-2026-02821) adds the tiles. Integrations Health and the Device Fleet
 * Dashboard join through the shared health board (public/js/health_board.bundle.js, run in the
 * page's context as the Desk's script tag would): the same first-load states, a Refresh that fails
 * keeps the tiles, a stale reply is dropped, and the Drive test answers in its tile. On all four
 * dashboards every tile is the kit's, every status a chip that says its word (a toned metric
 * included), every server string escaped, links through the kit's safe_href rule, and no
 * hand-drawn colour or tile markup survives; on the loader's stand-in a tile is a line of words.
 *
 * jQuery is a small stand-in over plain objects (each remembers its markup, text, value and
 * handlers); the kit only needs innerHTML and querySelector on the element it is handed.
 *
 * Run: node scripts/test_s3_page_states.mjs
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const LOADER = path.join(APP, "public", "js", "sf_kit_loader.js");
const BUNDLE = path.join(APP, "public", "js", "sf_kit.bundle.js");
const HEALTH_BOARD = path.join(APP, "public", "js", "health_board.bundle.js");
const PAGES = {
	"maintenance-day-board": "sapphire_maintenance/page/maintenance_day_board/maintenance_day_board.js",
	"stripe-payments-dashboard": "stripe_payments/page/stripe_payments_dashboard/stripe_payments_dashboard.js",
	"quickbooks-online-dashboard": "quickbooks_online/page/quickbooks_online_dashboard/quickbooks_online_dashboard.js",
	"water-engineering-wizard": "water_engineering/page/water_engineering_wizard/water_engineering_wizard.js",
	"integrations-health": "integration_hub/page/integrations_health/integrations_health.js",
	"device-fleet-dashboard": "device_management/page/device_fleet_dashboard/device_fleet_dashboard.js",
};

let checks = 0;
let failures = 0;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const unhandled = [];
process.on("unhandledRejection", (reason) => unhandled.push(reason));

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a === e) {
		console.log(`  ok   ${label}`);
	} else {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${e}\n    actual   ${a}`);
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
	for (let i = 0; i < 12; i++) await tick();
}

// ------------------------------------------------------------------ replies

const NETWORK_DOWN = Symbol("network down");
const reply = (status, body) => ({ __reply: true, status, body });
const SERVER_ERROR = reply(500, { exc_type: "Exception", exception: "pymysql.err.OperationalError: (1054, 'col')" });
const REFUSED = reply(403, {
	exc_type: "PermissionError",
	_server_messages: JSON.stringify([JSON.stringify({ message: "<b>Not permitted</b> to read this." })]),
});
const SIGNED_OUT = reply(401, { exc_type: "SessionExpired", session_expired: 1 });

// A reply the test hands over later: `answer` it with anything a respond handler may return.
function deferred() {
	let answer;
	const promise = new Promise((resolve) => (answer = resolve));
	return { promise, answer };
}

// ------------------------------------------------------------------ elements and a jQuery stand-in

class El {
	constructor(markup) {
		this.markup = markup || "";
		this.history = []; // every innerHTML written, in order
		this._html = "";
		this.textContent = "";
		this.children = [];
		this.handlers = {};
		this.listeners = {};
		this.attrs = {};
		this.options = []; // a select's option values, in order
		this._value = undefined;
		this.className = "";
		this.parentNode = null;
		this.hidden = false;
		this.found = {};
	}
	get innerHTML() {
		return this._html;
	}
	set innerHTML(value) {
		this._html = String(value);
		this.history.push(this._html);
	}
	// A select's value is its first option until one is picked.
	get value() {
		if (this._value !== undefined) return this._value;
		return this.options.length ? this.options[0] : "";
	}
	set value(v) {
		this._value = v;
	}
	setAttribute(k, v) {
		this.attrs[k] = String(v);
	}
	getAttribute(k) {
		return k in this.attrs ? this.attrs[k] : null;
	}
	appendChild(node) {
		node.parentNode = this;
		this.children.push(node);
		if (this.onappend) this.onappend(node);
		return node;
	}
	removeChild(node) {
		this.children = this.children.filter((c) => c !== node);
		node.parentNode = null;
		return node;
	}
	addEventListener(type, fn) {
		(this.listeners[type] = this.listeners[type] || []).push(fn);
	}
	removeEventListener() {}
	contains(node) {
		for (let n = node; n; n = n.parentNode) if (n === this) return true;
		return false;
	}
	getClientRects() {
		return [{}];
	}
	focus() {}
	get firstElementChild() {
		return null;
	}
	// The kit's state panel looks up its one action button in what it just wrote: a fresh button
	// for each panel, which the test presses through its listeners.
	querySelector(selector) {
		if (selector !== ".sf-state-action" || !this._html.includes("sf-state-action")) return null;
		if (this._actionFor !== this._html) {
			this._action = new El("button.sf-state-action");
			this._actionFor = this._html;
		}
		return this._action;
	}
}

function makeJQuery() {
	function wrap(el) {
		if (el.__jq) return el.__jq;
		const api = {
			jquery: "3.7.1-stand-in",
			length: 1,
			0: el,
			find(sel) {
				if (!el.found[sel]) el.found[sel] = new El(sel);
				return wrap(el.found[sel]);
			},
			html(value) {
				if (value === undefined) return el.innerHTML;
				el.innerHTML = value;
				el.children = [];
				el.found = {};
				el.options = [];
				for (const m of String(value).matchAll(/<option value="([^"]*)"/g)) el.options.push(m[1]);
				return proxy;
			},
			text(value) {
				if (value === undefined) return el.textContent;
				el.textContent = String(value);
				return proxy;
			},
			val(value) {
				if (value === undefined) return el.value;
				el.value = value;
				return proxy;
			},
			empty() {
				el.innerHTML = "";
				el.textContent = "";
				el.children = [];
				el.options = [];
				el._value = undefined;
				return proxy;
			},
			append(...items) {
				for (const x of items) {
					if (typeof x === "string") {
						el.innerHTML = el.innerHTML + x;
						const m = /^\s*<option value="([^"]*)"/.exec(x);
						if (m) el.options.push(m[1]);
					} else if (x) {
						wrap(x[0] || x).appendTo(proxy);
					}
				}
				return proxy;
			},
			appendTo(target) {
				const host = target && target.jquery ? target[0] : target;
				if (host && typeof host.appendChild === "function") {
					host.appendChild(el);
					if (el.markup.startsWith("<option")) host.options.push(el.value);
				}
				return proxy;
			},
			on(types, sel, fn) {
				if (typeof sel === "function") {
					fn = sel;
					sel = null;
				}
				for (const type of String(types).split(/\s+/)) {
					(el.handlers[type] = el.handlers[type] || []).push({ sel, fn });
				}
				return proxy;
			},
			trigger(type) {
				for (const h of el.handlers[type] || []) {
					h.fn.call(el, { type, target: el, currentTarget: el, preventDefault() {}, stopPropagation() {} });
				}
				return proxy;
			},
			hide() {
				el.hidden = true;
				return proxy;
			},
			show() {
				el.hidden = false;
				return proxy;
			},
			attr(k, v) {
				if (v === undefined && typeof k === "string") return el.attrs[k];
				el.attrs[k] = v;
				return proxy;
			},
			data() {
				return undefined;
			},
			each(fn) {
				fn.call(el, 0, el);
				return proxy;
			},
		};
		// Anything else jQuery has (addClass, toggle, css, prop ...) is a no-op that chains.
		const proxy = new Proxy(api, {
			get(target, key) {
				if (key in target) return target[key];
				if (key === "then" || typeof key === "symbol") return undefined;
				return () => proxy;
			},
		});
		el.__jq = proxy;
		return proxy;
	}
	function $(x) {
		if (x && x.jquery) return x;
		if (x instanceof El) return wrap(x);
		return wrap(new El(typeof x === "string" ? x.trim() : "?"));
	}
	return $;
}

// ------------------------------------------------------------------ the Desk, one page at a time

let kitCopies = 0;

function openPage(name, options) {
	const opts = options || {};
	const world = {
		respond: Object.assign({}, opts.respond || {}), // method's last dotted part -> reply
		sent: [],
		dialogs: [],
		alerts: [],
		toasts: [],
		timeouts: [],
		intervals: [],
		frappe_calls: [],
	};
	const $ = makeJQuery();

	const document = new El("document");
	document.cookie = "sid=abc; user_id=tester%40example.com";
	document.hidden = false;
	document.activeElement = null;
	document.createElement = (tag) => new El(`<${tag}>`);
	document.body = new El("body");
	// The kit's toast goes to the body: what it says is what a person sees.
	document.body.onappend = (node) => {
		if (/\bsf-toast\b/.test(node.className)) world.toasts.push(node.children[0].textContent);
	};

	const body = new El("page-body");
	const page = new Proxy(
		{
			body,
			main: body,
			// The page's Refresh, so a test can press it.
			set_primary_action: (label, fn) => (world.primary = fn),
		},
		{
			get(target, key) {
				if (key in target) return target[key];
				return () => {};
			},
		}
	);

	const escape_html = (s) =>
		String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

	const frappe = {
		pages: {},
		session: { user: "tester@example.com", logged_in_user: "tester@example.com" },
		csrf_token: "token",
		ui: { make_app_page: () => page },
		get_route: () => [world.route === undefined ? name : world.route],
		set_route() {},
		new_doc() {},
		confirm() {},
		msgprint(m) {
			world.dialogs.push(typeof m === "object" ? m.message : m);
		},
		show_alert(m) {
			world.alerts.push(typeof m === "object" ? m.message : m);
		},
		// Answered from `respond` too: the button paths, and every call on a stand-in. A failure
		// rejects the way frappe.call's jqXHR does, with the status and the parsed body.
		call(o) {
			const short = String(o.method).split(".").pop();
			world.sent.push(short);
			world.frappe_calls.push(short);
			const handler = world.respond[short];
			const answer = typeof handler === "function" ? handler() : handler;
			return Promise.resolve(answer).then((value) => {
				if (value === NETWORK_DOWN) throw { status: 0 };
				if (value && value.__reply) throw { status: value.status, responseJSON: value.body };
				return { message: value === undefined ? null : value };
			});
		},
		// v16's frappe.require: the files arrive (the real bundle installs window.sf_kit), then the
		// callback runs. Another copy of the module per page, as each page load evaluates it afresh.
		// `kit: false`: the file failed to load, and v16 runs the callback anyway.
		require(paths, cb) {
			// The shared health board (S3.2) is a plain script: run it in the page's context, as the
			// Desk's <script> tag would. `board: false`: it failed to load, and v16 runs cb anyway.
			if (paths.includes("health_board.bundle.js")) {
				if (opts.board !== false) vm.runInContext(fs.readFileSync(HEALTH_BOARD, "utf8"), ctx, { filename: HEALTH_BOARD });
				Promise.resolve().then(() => cb && cb());
				return;
			}
			if (opts.kit === false) {
				Promise.resolve().then(() => cb && cb());
				return;
			}
			globalThis.window = ctx;
			globalThis.document = document;
			import(pathToFileURL(BUNDLE).href + `?page=${++kitCopies}`).then(() => cb && cb());
		},
		utils: {
			escape_html,
			get_form_link: (doctype, docname) => `/desk/${doctype.toLowerCase().replace(/ /g, "-")}/${docname}`,
		},
		datetime: { now_time: () => "12:00:00", str_to_user: (s) => String(s || "") },
	};
	frappe.pages[name] = new El("wrapper");

	// The kit's quiet path: a fetch to /api/method/<method>, answered from `respond`.
	function fetch(url) {
		const method = decodeURIComponent(String(url).split("/api/method/")[1].split("?")[0]);
		const short = method.split(".").pop();
		world.sent.push(short);
		const handler = world.respond[short];
		const answer = typeof handler === "function" ? handler() : handler;
		return Promise.resolve(answer).then((value) => {
			if (value === NETWORK_DOWN) throw new TypeError("Failed to fetch");
			const status = value && value.__reply ? value.status : 200;
			const json = value && value.__reply ? value.body : { message: value === undefined ? null : value };
			return { status, text: () => Promise.resolve(JSON.stringify(json)) };
		});
	}

	const ctx = {
		console,
		frappe,
		$,
		jQuery: $,
		__: (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args && i in args ? String(args[i]) : m)),
		document,
		fetch,
		location: { reload() {} },
		format_currency: (v) => String(v),
		// Ids from 1, as a browser's are: a page may test `if (timer)`.
		setTimeout: (fn, ms) => world.timeouts.push({ fn, ms, done: false }),
		clearTimeout: (id) => {
			if (world.timeouts[id - 1]) world.timeouts[id - 1].done = true;
		},
		setInterval: (fn, ms) => world.intervals.push({ fn, ms, cleared: false }),
		clearInterval: (id) => {
			if (world.intervals[id - 1]) world.intervals[id - 1].cleared = true;
		},
	};
	ctx.window = ctx;
	vm.createContext(ctx);
	// `loader: false`: a Desk opened before the deploy that added sf_kit_ready.
	if (opts.loader !== false) vm.runInContext(fs.readFileSync(LOADER, "utf8"), ctx, { filename: LOADER });
	const file = path.join(APP, PAGES[name]);
	vm.runInContext(fs.readFileSync(file, "utf8"), ctx, { filename: file });
	const wrapper = frappe.pages[name];
	wrapper.on_page_load(wrapper);
	if (wrapper.on_page_show) wrapper.on_page_show(wrapper);

	return {
		world,
		ctx,
		body,
		$,
		// The child of the page body whose markup has `fragment` in it.
		part: (fragment) => body.children.find((c) => c.markup.includes(fragment)),
		// The page's timeouts of `ms` not yet run, run once.
		async runTimeouts(ms) {
			const due = world.timeouts.filter((t) => !t.done && t.ms === ms);
			due.forEach((t) => {
				t.done = true;
				t.fn();
			});
			await settle();
			return due.length;
		},
		pending: (ms) => world.timeouts.filter((t) => !t.done && t.ms === ms).length,
		async press(el) {
			const button = el.querySelector(".sf-state-action");
			if (!button) throw new Error("no action button on the panel");
			(button.listeners.click || []).forEach((fn) => fn({ preventDefault() {} }));
			await settle();
		},
	};
}

const count = (list, item) => list.filter((x) => x === item).length;

// ------------------------------------------------------------------ the first load, every page

const FIRST = {
	"maintenance-day-board": {
		method: "get_day_board_data",
		ok: { scheduled: [], in_progress: [], submitted_today: [], flagged: [] },
		host: (p) => p.part("mdb-board"),
		failed: "Could not load the day board.",
		refused: "You do not have access to the Maintenance Day Board.",
		drawn: "mdb-col",
	},
	"stripe-payments-dashboard": {
		method: "get_dashboard_status",
		ok: { settings: { status: "Connected" }, counts: {}, recent: [] },
		host: (p) => p.part("stripe-dashboard"),
		failed: "Could not load the Stripe Payments dashboard.",
		refused: "You do not have access to the Stripe Payments dashboard.",
		drawn: 'sf-chip sf-chip--good">Connected<',
	},
	"quickbooks-online-dashboard": {
		method: "get_dashboard_status",
		ok: { settings: { status: "Connected" }, latest_logs: [] },
		host: (p) => p.part("qbo-dashboard").found[".qbo-log-list"],
		failed: "Could not load the QuickBooks status.",
		refused: "You do not have access to the QuickBooks Online dashboard.",
		drawn: "sf-state--empty",
	},
	"water-engineering-wizard": {
		method: "check_permission",
		ok: true,
		extra: { get_design_state: { designs: [] } },
		host: (p) => p.body,
		failed: "Could not open the Water Engineering Wizard.",
		refused: "You do not have access to Water Feature Designs.",
		drawn: "we-wrap",
	},
	// S3.2 (v1.651.0): the two health pages are drawn by the shared board. It has no no-loader
	// fallback of the S3.1 kind (`board_only`): see "without the loader" below.
	"integrations-health": {
		method: "get_health",
		ok: { generated_at: "2026-10-13 09:00:00", integrations: [], scheduler: {}, errors: {} },
		host: (p) => boardContent(p),
		failed: "Could not load the integration status.",
		refused: "You do not have access to Integrations Health.",
		drawn: '<div class="sf-tiles">',
		board_only: true,
	},
	"device-fleet-dashboard": {
		method: "get_fleet_health",
		ok: { generated_at: "2026-10-13 09:00:00", total: 0, tiles: [] },
		host: (p) => boardContent(p),
		failed: "Could not load the fleet status.",
		refused: "You do not have access to the Device Fleet Dashboard.",
		drawn: '<div class="sf-tiles">',
		board_only: true,
	},
};

// The health board's parts: its root under the page body, then the meta line and the content.
const boardRoot = (p) => p.part("sf-health-board");
const boardMeta = (p) => boardRoot(p).children.find((c) => c.markup.includes("sf-health-meta"));
const boardContent = (p) => boardRoot(p).children.find((c) => c.markup.includes("sf-health-content"));

for (const [name, spec] of Object.entries(FIRST)) {
	test(`${name}: a 500 draws the kit's error panel, and Try again asks again in place`, async () => {
		const p = openPage(name, { respond: Object.assign({ [spec.method]: SERVER_ERROR }, spec.extra) });
		await settle();
		const host = spec.host(p);
		check("the kit was asked for once", p.world.sent.filter((m) => m === spec.method).length, 1);
		check("the kit's error panel", host.innerHTML.includes("sf-state--error"), true);
		check("with the page's title", host.innerHTML.includes(spec.failed), true);
		check(
			"and the kit's sentence, never the exception",
			[host.innerHTML.includes("The server could not finish this request."), host.innerHTML.includes("pymysql")],
			[true, false]
		);
		check("frappe put up no dialog", p.world.dialogs, []);
		p.world.respond[spec.method] = spec.ok;
		await p.press(host);
		check("Try again asked again", count(p.world.sent, spec.method), 2);
		check("and drew what came back", spec.host(p).innerHTML.includes(spec.drawn), true);
	});

	test(`${name}: a 403 draws no-access with the server's own words`, async () => {
		const p = openPage(name, { respond: Object.assign({ [spec.method]: REFUSED }, spec.extra) });
		await settle();
		const host = spec.host(p);
		check("the kit's no-access panel", host.innerHTML.includes("sf-state--no-access"), true);
		check("with the page's title", host.innerHTML.includes(spec.refused), true);
		check("and the server's words, markup taken out", host.innerHTML.includes("Not permitted to read this."), true);
		check("frappe put up no dialog", p.world.dialogs, []);
	});

	test(`${name}: a lapsed session draws the signed-out panel with Reload`, async () => {
		const p = openPage(name, { respond: Object.assign({ [spec.method]: SIGNED_OUT }, spec.extra) });
		await settle();
		const host = spec.host(p);
		check("signed out", host.innerHTML.includes("You have been signed out."), true);
		check("with Reload", host.innerHTML.includes(">Reload</button>"), true);
	});
}

// ------------------------------------------------------------------ the Day Board's timer

const BOARD = "get_day_board_data";
const tickOf = (p) => p.world.intervals.filter((i) => i.ms === 60000 && !i.cleared).pop();

test("Day Board: a timer refresh that fails keeps the board, says so once, and never draws loading", async () => {
	const p = openPage("maintenance-day-board", { respond: { [BOARD]: FIRST["maintenance-day-board"].ok } });
	await settle();
	const board = p.part("mdb-board");
	check("the board is up", board.innerHTML.includes("mdb-col"), true);
	p.world.respond[BOARD] = SERVER_ERROR;
	tickOf(p).fn();
	await settle();
	check("still the board", board.innerHTML.includes("mdb-col"), true);
	check("one toast", p.world.toasts, ["Could not refresh the board. Showing it as of 12:00:00."]);
	tickOf(p).fn();
	await settle();
	check("not a toast a minute", p.world.toasts.length, 1);
	check("still the board after two", board.innerHTML.includes("mdb-col"), true);
	check(
		"the loading panel was drawn once, on the first load",
		board.history.filter((h) => h.includes("sf-state--loading")).length,
		1
	);
	check("the timer is still running", !!tickOf(p), true);
});

test("Day Board: a board that never loaded keeps its error panel through the timer's retries", async () => {
	const p = openPage("maintenance-day-board", { respond: { [BOARD]: SERVER_ERROR } });
	await settle();
	const board = p.part("mdb-board");
	tickOf(p).fn();
	await settle();
	check("asked again", count(p.world.sent, BOARD), 2);
	check("the error panel, not the loading one", board.innerHTML.includes("sf-state--error"), true);
	check("loading drawn once", board.history.filter((h) => h.includes("sf-state--loading")).length, 1);
});

test("Day Board: a 403 on a refresh stops the timer", async () => {
	const p = openPage("maintenance-day-board", { respond: { [BOARD]: FIRST["maintenance-day-board"].ok } });
	await settle();
	p.world.respond[BOARD] = REFUSED;
	tickOf(p).fn();
	await settle();
	check("no timer left", !!tickOf(p), false);
	check("the board stays", p.part("mdb-board").innerHTML.includes("mdb-col"), true);
	check("and says access is gone, not a hiccup", p.world.toasts, [
		"You no longer have access to the Day Board. Showing it as of 12:00:00.",
	]);
});

test("Day Board: a lapsed session on the first load stops the timer", async () => {
	const p = openPage("maintenance-day-board", { respond: { [BOARD]: SIGNED_OUT } });
	await settle();
	check("no timer left", !!tickOf(p), false);
	check("signed out", p.part("mdb-board").innerHTML.includes("You have been signed out."), true);
});

// ------------------------------------------------------------------ the QuickBooks resync poll

const POLL = "get_sync_log_summary";
const QBO_OK = FIRST["quickbooks-online-dashboard"].ok;

test("QuickBooks: the resync poll retries a 500 and a dropped connection", async () => {
	const p = openPage("quickbooks-online-dashboard", { respond: { get_dashboard_status: QBO_OK, [POLL]: SERVER_ERROR } });
	await settle();
	p.ctx.pollResyncPreview("QBSL-1", 0);
	await settle();
	check("a 500 is tried again in 3 s", p.pending(3000), 1);
	p.world.respond[POLL] = NETWORK_DOWN;
	await p.runTimeouts(3000);
	check("and so is a dropped connection", p.pending(3000), 1);
	p.world.respond[POLL] = { status: "Running" };
	await p.runTimeouts(3000);
	check("a running preview keeps polling", p.pending(3000), 1);
	check("no dialog, no toast", [p.world.dialogs, p.world.toasts], [[], []]);
});

test("QuickBooks: a 4xx stops the resync poll and says why", async () => {
	const p = openPage("quickbooks-online-dashboard", {
		respond: {
			get_dashboard_status: QBO_OK,
			[POLL]: reply(404, { exc_type: "DoesNotExistError", _server_messages: JSON.stringify([JSON.stringify({ message: "Sync Log QBSL-1 not found" })]) }),
		},
	});
	await settle();
	p.ctx.pollResyncPreview("QBSL-1", 0);
	await settle();
	check("no retry", p.pending(3000), 0);
	check("asked once", count(p.world.sent, POLL), 1);
	check("a toast with the server's words", p.world.toasts, [
		"Stopped checking the resync preview: Sync Log QBSL-1 not found Review Sync Log QBSL-1.",
	]);
});

test("QuickBooks: a lapsed session stops the resync poll with the signed-out panel", async () => {
	const p = openPage("quickbooks-online-dashboard", { respond: { get_dashboard_status: QBO_OK, [POLL]: SIGNED_OUT } });
	await settle();
	p.ctx.pollResyncPreview("QBSL-1", 0);
	await settle();
	const list = FIRST["quickbooks-online-dashboard"].host(p);
	check("no retry", p.pending(3000), 0);
	check("the signed-out panel where the logs go", list.innerHTML.includes("You have been signed out."), true);
	check("and a toast", p.world.toasts.length, 1);
});

// ------------------------------------------------------------------ the Water wizard's designs

test("Water: a design's reply that lands after another was picked is dropped", async () => {
	const first = deferred();
	const second = deferred();
	const p = openPage("water-engineering-wizard", {
		respond: {
			check_permission: true,
			get_design_state: { designs: [{ name: "WFD-1", status: "Draft" }, { name: "WFD-2", status: "Draft" }] },
		},
	});
	await settle();
	const pick = p.$(p.body).find("#we-design-pick");
	const state = p.body.found["#we-design-state"];
	p.world.respond.get_design_state = () => first.promise;
	pick.val("WFD-1").trigger("change");
	await settle();
	p.world.respond.get_design_state = () => second.promise;
	pick.val("WFD-2").trigger("change");
	await settle();
	second.answer({ design: "WFD-2", design_title: "Second", rollups: {} });
	await settle();
	check("the second design is drawn", state.innerHTML.includes("<b>WFD-2</b>"), true);
	first.answer({ design: "WFD-1", design_title: "First", rollups: {} });
	await settle();
	check("the first reply did not paint over it", [state.innerHTML.includes("WFD-1"), state.innerHTML.includes("<b>WFD-2</b>")], [false, true]);
});

test("Water: a late failure for a design no longer picked draws no error", async () => {
	const first = deferred();
	const p = openPage("water-engineering-wizard", {
		respond: { check_permission: true, get_design_state: { designs: [{ name: "WFD-1" }, { name: "WFD-2" }] } },
	});
	await settle();
	const pick = p.$(p.body).find("#we-design-pick");
	const state = p.body.found["#we-design-state"];
	p.world.respond.get_design_state = () => first.promise;
	pick.val("WFD-1").trigger("change");
	await settle();
	p.world.respond.get_design_state = { design: "WFD-2", rollups: {} };
	pick.val("WFD-2").trigger("change");
	await settle();
	first.answer(SERVER_ERROR);
	await settle();
	check("no error panel", state.innerHTML.includes("sf-state--error"), false);
	check("the second design stays", state.innerHTML.includes("<b>WFD-2</b>"), true);
});

test("Water: a design name with a quote is one option with that exact value", async () => {
	const odd = 'WFD-"3" <b>';
	const p = openPage("water-engineering-wizard", {
		respond: { check_permission: true, get_design_state: { designs: [{ name: odd, status: "Draft" }] } },
	});
	await settle();
	const pick = p.body.found["#we-design-pick"];
	check("the placeholder, then the design", pick.children.map((o) => o.value), ["", odd]);
	check("its label is text", pick.children[1].textContent.startsWith(odd), true);
	check("no option written as markup", pick.innerHTML.includes("<option"), false);
});

// ------------------------------------------------------------------ without the kit

// The text and buttons a stand-in drew into `host` (it writes elements, not markup).
const said = (host) => host.children.map((c) => c.textContent);
const pressButton = async (host) => {
	const button = host.children.find((c) => c.handlers.click);
	if (!button) throw new Error("no button");
	button.handlers.click.forEach((h) => h.fn({ preventDefault() {} }));
	await settle();
};

for (const [name, spec] of Object.entries(FIRST).filter(([, s]) => !s.board_only)) {
	test(`${name}: no loader and no kit (an old Desk tab after a deploy) still says what failed`, async () => {
		const p = openPage(name, { loader: false, kit: false, respond: Object.assign({ [spec.method]: SERVER_ERROR }, spec.extra) });
		await settle();
		const host = spec.host(p);
		check("the page's title, as text", said(host).includes(spec.failed), true);
		check("asked through frappe.call", count(p.world.frappe_calls, spec.method), 1);
		p.world.respond[spec.method] = spec.ok;
		await pressButton(host);
		check("Try again asked again", count(p.world.frappe_calls, spec.method), 2);
	});
}

test("Loader stand-in: a 401 is a lapsed session, so the Day Board's timer stops", async () => {
	const p = openPage("maintenance-day-board", { kit: false, respond: { [BOARD]: SIGNED_OUT } });
	await settle();
	check("signed out", said(p.part("mdb-board")).includes("You have been signed out."), true);
	check("no timer left", !!tickOf(p), false);
});

test("Loader stand-in: rejects in the kit's shape, expiry read as the kit reads it", async () => {
	const p = openPage("stripe-payments-dashboard", { kit: false, respond: { get_dashboard_status: {} } });
	await settle();
	const sf = await p.ctx.sf_kit_ready();
	const failure = async (answer, cookie) => {
		p.world.respond.probe = answer;
		if (cookie !== undefined) p.ctx.document.cookie = cookie;
		return sf.call("x.probe", {}, { quiet: true }).then(() => null, (err) => err);
	};
	check("the kit did not load", p.ctx.sf_kit, undefined);
	check("a 401", await failure(SIGNED_OUT), { status: 401, message: "", exc_type: "SessionExpired", session_expired: true });
	check("AuthenticationError in a server message", (await failure(reply(417, { _server_messages: JSON.stringify([JSON.stringify({ message: "x", exc_type: "AuthenticationError" })]) }))).session_expired, true);
	check("a 403 with the server's words, signed in", await failure(REFUSED), { status: 403, message: "Not permitted to read this.", exc_type: "PermissionError", session_expired: false });
	check("a 403 once v16 has deleted the user_id cookie", (await failure(REFUSED, "sid=Guest")).session_expired, true);
	check("a 500", await failure(SERVER_ERROR, "sid=abc; user_id=tester%40example.com"), { status: 500, message: "", exc_type: "Exception", session_expired: false });
	check("a dropped connection", (await failure(NETWORK_DOWN)).status, 0);
});

// ------------------------------------------------------------------ S3.2: the tiles (v1.651.0)

// One tile's markup out of a grid, by its data-key.
function section(html, key) {
	const at = html.indexOf(`data-key="${key}"`);
	if (at < 0) return "";
	const end = html.indexOf("</section>", at);
	return html.slice(at, end);
}

// Markup a page must never draw: a hand-built status colour, or the tile classes it used to own.
const HAND_DRAWN = /style="background|indicator-pill|alert-(success|warning|danger)|#[0-9a-fA-F]{6}\b|ih-card|dfd-card|qbo-status-item|ih-dot|dfd-dot|mini-dot/;

const HEALTH = {
	generated_at: "2026-10-13 09:00:00",
	integrations: [
		{
			key: "quickbooks",
			label: "QuickBooks <b>Online</b>",
			status: "red",
			headline: "Failed",
			notes: ["Reconnect <script>x()</script>"],
			metrics: [
				{ label: "Failed syncs (7d)", value: 3, tone: "red" },
				{ label: "Last CDC poll", value: "<2 h ago>", tone: "amber" },
				{ label: "Plain", value: 9, tone: "neutral" },
			],
			links: [
				{ label: "Settings", route: "/desk/quickbooks-online-settings" },
				{ label: "Evil", route: "javascript:alert(1)" },
				{ label: "Old", route: "/app/quickbooks-sync-log" },
			],
			actions: [],
		},
		{ key: "drive", label: "Google Drive", status: "green", headline: "Configured", metrics: [{ label: "Failed (24h)", value: 0, tone: "green" }], links: [], actions: ["drive_test"] },
		{ key: "ga4", label: "Analytics", status: "neutral", headline: "Not configured", notes: ["Add the GA4 property ID."], metrics: [], links: [] },
	],
	scheduler: { status: "red", enabled: false, app_job_count: 12, failed_24h: 2, recent_failures: [{ job: "erpnext_enhancements.x<y>", when: "2 h ago" }] },
	errors: { status: "amber", total_24h: 4, top: [{ title: "Boom <i>", count: 4 }] },
};

test("Integrations Health: every tile is the kit's, every status a chip with its word, every string escaped", async () => {
	const p = openPage("integrations-health", { respond: { get_health: HEALTH } });
	await settle();
	const html = boardContent(p).innerHTML;
	check("asked quietly, not through frappe.call", [count(p.world.sent, "get_health"), count(p.world.frappe_calls, "get_health")], [1, 0]);
	check("one kit grid of five tiles", [html.startsWith('<div class="sf-tiles">'), (html.match(/<section class="sf-card sf-tile"/g) || []).length], [true, 5]);
	check("the meta line", boardMeta(p).textContent, "As of 2026-10-13 09:00:00");
	check("no raw server markup", /<b>Online|<script>|<y>|<i>/.test(html), false);
	check("the title, escaped", html.includes("QuickBooks &lt;b&gt;Online&lt;/b&gt;"), true);
	check("a red tile is a Failing chip", section(html, "quickbooks").includes('<span class="sf-chip sf-chip--danger">Failing</span>'), true);
	check("a green tile is a Healthy chip", section(html, "drive").includes('<span class="sf-chip sf-chip--good">Healthy</span>'), true);
	check("a neutral tile has no chip, not a grey dot", section(html, "ga4").includes("sf-chip"), false);
	check(
		"a toned metric is a chip of its value; a neutral one is text",
		[
			html.includes('<dt>Failed syncs (7d)</dt><dd><span class="sf-chip sf-chip--danger">3</span></dd>'),
			html.includes('<dt>Last CDC poll</dt><dd><span class="sf-chip sf-chip--warn">&lt;2 h ago&gt;</span></dd>'),
			html.includes("<dt>Plain</dt><dd>9</dd>"),
		],
		[true, true, true]
	);
	check(
		"the headline and notes are the detail line",
		[html.includes("Failed. Reconnect &lt;script&gt;x()&lt;/script&gt;"), html.includes("Not configured. Add the GA4 property ID.")],
		[true, true]
	);
	check(
		"links go through the kit's safe_href rule",
		[
			html.includes('<a class="sf-tile-link" href="/desk/quickbooks-online-settings">Settings</a>'),
			html.includes('<span class="sf-tile-link sf-tile-link--plain">Evil</span>'),
			html.includes('<span class="sf-tile-link sf-tile-link--plain">Old</span>'),
			/javascript:|href="\/app\//.test(html),
		],
		[true, true, true, false]
	);
	const scheduler = section(html, "scheduler");
	check(
		"the scheduler tile",
		[
			scheduler.includes('sf-chip--danger">Failing<'),
			scheduler.includes('<dt>Scheduler</dt><dd><span class="sf-chip sf-chip--danger">disabled</span></dd>'),
			scheduler.includes("The Frappe scheduler is disabled"),
			scheduler.includes("erpnext_enhancements.x&lt;y&gt;"),
		],
		[true, true, true, true]
	);
	const errors = section(html, "errors");
	check("the error digest tile", [errors.includes('sf-chip--warn">Needs attention<'), errors.includes("<dt>Boom &lt;i&gt;</dt><dd>4</dd>")], [true, true]);
	check("no hand-drawn colour or tile markup", HAND_DRAWN.test(html), false);
	check("frappe put up no dialog", p.world.dialogs, []);
});

test("Device Fleet Dashboard: the same board draws the fleet's tiles", async () => {
	const fleet = {
		generated_at: "2026-10-13 09:00:00",
		total: 5,
		tiles: [
			{ key: "fleet", label: "Fleet", status: "red", headline: "4 active · 5 total", metrics: [{ label: "Lost/Stolen", value: 1, tone: "red" }, { label: "In Stock", value: 2, tone: "neutral" }], links: [{ label: "All devices", route: "/desk/managed-device" }] },
			{ key: "platforms", label: "Platforms <x>", status: "neutral", headline: "2 in use", metrics: [{ label: "iOS", value: 3 }] },
		],
	};
	const p = openPage("device-fleet-dashboard", { respond: { get_fleet_health: fleet } });
	await settle();
	const html = boardContent(p).innerHTML;
	check("the meta line", boardMeta(p).textContent, "5 devices · as of 2026-10-13 09:00:00");
	check("two kit tiles", (html.match(/<section class="sf-card sf-tile"/g) || []).length, 2);
	check("the red tile's chip says so", section(html, "fleet").includes('sf-chip--danger">Failing<'), true);
	check("the lost device is a chip of its count", html.includes('<dt>Lost/Stolen</dt><dd><span class="sf-chip sf-chip--danger">1</span></dd>'), true);
	check("the detail line", html.includes('<p class="sf-tile-detail">4 active · 5 total</p>'), true);
	check("the link", html.includes('href="/desk/managed-device">All devices</a>'), true);
	check("escaped", [html.includes("Platforms &lt;x&gt;"), html.includes("<x>")], [true, false]);
	check("no hand-drawn colour or tile markup", HAND_DRAWN.test(html), false);
});

test("Health board: a Refresh that fails keeps the tiles and says so once", async () => {
	const p = openPage("integrations-health", { respond: { get_health: HEALTH } });
	await settle();
	const content = boardContent(p);
	p.world.respond.get_health = SERVER_ERROR;
	p.world.primary();
	await settle();
	check("the tiles stay", content.innerHTML.includes('data-key="quickbooks"'), true);
	check("a toast", p.world.toasts, ["Could not refresh. Showing what was last loaded."]);
	check("loading was drawn on the first load only", content.history.filter((h) => h.includes("sf-state--loading")).length, 1);
	p.world.respond.get_health = SIGNED_OUT;
	p.world.primary();
	await settle();
	check("a lapsed session says so, tiles kept", [p.world.toasts[1], content.innerHTML.includes('data-key="quickbooks"')], ["You have been signed out. Reload the page to sign in again.", true]);
});

test("Health board: a reply that lands after a newer Refresh is dropped", async () => {
	const first = deferred();
	const p = openPage("device-fleet-dashboard", { respond: { get_fleet_health: () => first.promise } });
	await settle();
	p.world.respond.get_fleet_health = { total: 1, tiles: [{ key: "new", label: "New" }] };
	p.world.primary();
	await settle();
	first.answer({ total: 9, tiles: [{ key: "old", label: "Old" }] });
	await settle();
	const html = boardContent(p).innerHTML;
	check("the newer reply is on screen", [html.includes('data-key="new"'), html.includes('data-key="old"')], [true, false]);
});

test("Health board: on the loader's stand-in, each tile is a line of text with its words", async () => {
	const p = openPage("integrations-health", { kit: false, respond: { get_health: HEALTH } });
	await settle();
	const html = boardContent(p).innerHTML;
	check("asked through frappe.call", count(p.world.frappe_calls, "get_health"), 1);
	check("a line per tile", (html.match(/<p>/g) || []).length, 5);
	check("title, status word, detail and metric", html.includes("<p>Google Drive · Healthy · Configured · Failed (24h): 0</p>"), true);
	check(
		"a red tile still says Failing, with its metrics and link labels",
		html.includes("Failing · Failed. Reconnect &lt;script&gt;x()&lt;/script&gt; · Failed syncs (7d): 3 · Last CDC poll: &lt;2 h ago&gt; · Plain: 9 · Settings · Evil · Old</p>"),
		true
	);
	check("a neutral tile has no word", html.includes("<p>Analytics · Not configured. Add the GA4 property ID.</p>"), true);
	check("escaped", [html.includes("QuickBooks &lt;b&gt;Online&lt;/b&gt;"), html.includes("<b>")], [true, false]);
});

test("QuickBooks and Stripe: with no loader and no kit, the tiles are lines of words, statuses included", async () => {
	const qbo = openPage("quickbooks-online-dashboard", {
		loader: false,
		kit: false,
		respond: { get_dashboard_status: { settings: { status: "Connected", realm_id: "<9130>" }, failed_records: 2, latest_logs: [] } },
	});
	await settle();
	const tiles = qbo.part("qbo-dashboard").found[".qbo-tiles"].innerHTML;
	check("QuickBooks: the connection and the tone-only failed count both say their word", [tiles.includes("<p>Connection · Connected</p>"), tiles.includes("<p>Failed Logs · Failing · 2</p>"), tiles.includes("&lt;9130&gt;")], [true, true, true]);
	const stripe = openPage("stripe-payments-dashboard", {
		loader: false,
		kit: false,
		respond: { get_dashboard_status: { settings: { status: "Connected", has_secret_key: 0 }, counts: { Paid: 3 }, recent: [] } },
	});
	await settle();
	const html = stripe.part("stripe-dashboard").innerHTML;
	check("Stripe: the connection tile with its metrics, and a count tile", [html.includes("<p>Connection · Connected · Environment: — · Enabled: No · Secret key: Missing"), html.includes("<p>Paid · 3</p>")], [true, true]);
});

test("Health board: without the loader, or without the board, the page says so in a line", async () => {
	const noLoader = openPage("device-fleet-dashboard", { loader: false, respond: { get_fleet_health: {} } });
	await settle();
	check("no loader", boardContent(noLoader).textContent, "This page could not load. Reload the page to try again.");
	check("and asked nothing", noLoader.world.sent, []);
	const noBoard = openPage("integrations-health", { board: false, respond: { get_health: HEALTH } });
	await settle();
	check("no board", noBoard.body.textContent, "This page could not load. Reload the page to try again.");
});

test("Integrations Health: the Drive test answers in the Drive tile, each check a chip with its word", async () => {
	const p = openPage("integrations-health", {
		respond: {
			get_health: HEALTH,
			run_drive_test: { checks: [{ check: "Credentials", ok: true }, { check: "Folder <root>", ok: false, detail: "403" }, { check: "Shared drive", ok: null }] },
		},
	});
	await settle();
	// The board finds the Drive tile by its key and the page appends the test to it.
	const tile = boardContent(p).found[".sf-tile"];
	const box = tile.children.find((c) => c.markup.includes("ih-drive-test"));
	check("the test is in the tile", !!box, true);
	const button = box.children.find((c) => c.markup.includes("<button"));
	const out = box.children.find((c) => c.markup.includes("ih-drive-result"));
	check("its label", button.textContent, "Test connection");
	button.handlers.click.forEach((h) => h.fn({ preventDefault() {} }));
	await settle();
	check("asked quietly", [count(p.world.sent, "run_drive_test"), count(p.world.frappe_calls, "run_drive_test")], [1, 0]);
	check(
		"pass, fail and not set up, as chips with words",
		[
			out.innerHTML.includes('<span class="sf-chip sf-chip--good">Pass</span> Credentials'),
			out.innerHTML.includes('<span class="sf-chip sf-chip--danger">Fail</span> Folder &lt;root&gt; — 403'),
			out.innerHTML.includes('<span class="sf-chip sf-chip--neutral">Not set up</span> Shared drive'),
		],
		[true, true, true]
	);
	check("the button is back", button.textContent, "Test connection");
	p.world.respond.run_drive_test = SERVER_ERROR;
	button.handlers.click.forEach((h) => h.fn({ preventDefault() {} }));
	await settle();
	check("a failed test says so in the tile", out.innerHTML.includes('sf-chip--danger">Failed</span> The server could not finish this request.'), true);
	check("frappe put up no dialog", p.world.dialogs, []);
});

test("QuickBooks: the status tiles are the kit's, the connection a chip that says its status", async () => {
	const answer = deferred();
	const p = openPage("quickbooks-online-dashboard", { respond: { get_dashboard_status: () => answer.promise } });
	await settle();
	const tiles = p.part("qbo-dashboard").found[".qbo-tiles"];
	check("before the answer, four tiles of dashes and no chip", [(tiles.innerHTML.match(/sf-tile"/g) || []).length, tiles.innerHTML.includes("sf-chip"), (tiles.innerHTML.match(/>—</g) || []).length], [4, false, 4]);
	answer.answer({ settings: { status: "Connected", environment: "production", realm_id: "<9130>" }, failed_records: 2, latest_logs: [] });
	await settle();
	const html = tiles.innerHTML;
	check("the connection chip", section(html, "status").includes('<span class="sf-chip sf-chip--good">Connected</span>'), true);
	check("the realm id, escaped", [html.includes("&lt;9130&gt;"), html.includes("<9130>")], [true, false]);
	check("failed logs: a value and a chip", [section(html, "failed_records").includes('<div class="sf-tile-value">2</div>'), section(html, "failed_records").includes('sf-chip--danger">Failing<')], [true, true]);
	check("no hand-drawn colour or tile markup", HAND_DRAWN.test(html), false);
});

test("QuickBooks: an unknown connection status is a neutral chip with its word", async () => {
	const p = openPage("quickbooks-online-dashboard", { respond: { get_dashboard_status: { settings: { status: "Disconnected" }, failed_records: 0, latest_logs: [] } } });
	await settle();
	const html = p.part("qbo-dashboard").found[".qbo-tiles"].innerHTML;
	check("neutral, worded", section(html, "status").includes('<span class="sf-chip sf-chip--neutral">Disconnected</span>'), true);
	check("no failures is good news", section(html, "failed_records").includes('sf-chip--good">Healthy<'), true);
});

test("Stripe: the connection and counts are kit tiles, every status a chip with its word", async () => {
	const p = openPage("stripe-payments-dashboard", {
		respond: {
			get_dashboard_status: {
				settings: { status: "Pending <b>", status_message: "Add the <secret>", environment: "Test", enabled: 1, has_secret_key: 0, has_webhook_secret: 1 },
				counts: { Paid: 3, Failed: 1 },
				recent: [
					{ name: "SP-1", customer: "<Acme>", status: "Failed", amount: 5, currency: "USD" },
					{ name: "SP-2", customer: "Beta", status: "Link Sent", amount: 6, currency: "USD" },
				],
			},
		},
	});
	await settle();
	const html = p.part("stripe-dashboard").innerHTML;
	check("two kit grids", (html.match(/<div class="sf-tiles">/g) || []).length, 2);
	const connection = section(html, "connection");
	check(
		"the connection tile",
		[
			connection.includes('<span class="sf-chip sf-chip--warn">Pending &lt;b&gt;</span>'),
			connection.includes("Add the &lt;secret&gt;"),
			connection.includes('<dt>Secret key</dt><dd><span class="sf-chip sf-chip--danger">Missing</span></dd>'),
			connection.includes('<dt>Webhook secret</dt><dd><span class="sf-chip sf-chip--good">Set</span></dd>'),
		],
		[true, true, true, true]
	);
	check(
		"a tile per status: its word in a chip, then its count",
		[
			section(html, "Paid").includes('<span class="sf-chip sf-chip--good">Paid</span></div><div class="sf-tile-value">3</div>'),
			section(html, "Failed").includes('<span class="sf-chip sf-chip--danger">Failed</span></div><div class="sf-tile-value">1</div>'),
			section(html, "Link Sent").includes('<span class="sf-chip sf-chip--neutral">Link Sent</span></div><div class="sf-tile-value">0</div>'),
			(html.match(/<section class="sf-card sf-tile"/g) || []).length,
		],
		[true, true, true, 7]
	);
	check(
		"the recent payments' statuses are chips",
		[html.includes('<td><span class="sf-chip sf-chip--danger">Failed</span></td>'), html.includes('<td><span class="sf-chip sf-chip--neutral">Link Sent</span></td>')],
		[true, true]
	);
	check("escaped", [html.includes("&lt;Acme&gt;"), /<Acme>|<secret>|Pending <b>/.test(html)], [true, false]);
	check("no hand-drawn colour or tile markup", HAND_DRAWN.test(html), false);
});

// ------------------------------------------------------------------ run

for (const { name, fn } of tests) {
	console.log(name);
	try {
		await fn();
	} catch (err) {
		failures += 1;
		console.log(`  FAIL threw: ${err && err.stack ? err.stack : err}`);
	}
}
if (unhandled.length) {
	failures += unhandled.length;
	unhandled.forEach((r) => console.log(`  FAIL unhandled rejection: ${r && r.stack ? r.stack : r}`));
}
console.log(failures ? `\n${failures} of ${checks} checks failed` : `\nall ${checks} checks passed`);
process.exit(failures ? 1 : 0);
