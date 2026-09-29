#!/usr/bin/env node
/**
 * Browser Back / Forward on the Rental Planner (v1.563.0), run against a model of the router.
 *
 * Loads the REAL page script (asset_management/page/rental_planner/rental_planner.js) into a vm
 * context over a small fake Desk and session history, then clicks Next, the flow's own Back
 * button, and the browser's Back and Forward. Plain node, no runner, no npm install.
 *
 * The model follows Frappe v16's router where the page depends on it: set_route pushes the path
 * (or replaces it when frappe.route_flags.replace_route is set) and shows the page, which runs
 * on_page_show; a browser traversal lands on an entry and shows the page for it; Back from the
 * first entry leaves the page. jQuery is a recording stand-in: every element remembers its text
 * and its click handler, so a test clicks a button by its label. FieldGroup holds values the
 * test sets.
 *
 * Nik's rule: every screen of a multi-screen page is its own history entry, Back never loses
 * work, and Forward restores the screen.
 *
 * Run: node scripts/test_rental_planner_history.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPT = path.join(
	__dirname,
	"..",
	"erpnext_enhancements",
	"asset_management",
	"page",
	"rental_planner",
	"rental_planner.js"
);

let checks = 0;
let failures = 0;
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a !== e) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${e}\n    actual   ${a}`);
	}
}

const tick = () => new Promise((resolve) => setImmediate(resolve));
async function settle() {
	for (let i = 0; i < 6; i++) await tick();
}

// ------------------------------------------------------------------ fake jQuery

function makeJQuery(registry) {
	function element() {
		const state = { text: "", handlers: {}, value: "" };
		const self = new Proxy(function () {}, {
			get(_target, prop) {
				if (prop === "__state") return state;
				if (prop === "then") return undefined;
				if (prop === "text") {
					return (value) => {
						if (value === undefined) return state.text;
						state.text = String(value);
						return self;
					};
				}
				if (prop === "on") {
					return (events, fn) => {
						String(events)
							.split(/\s+/)
							.forEach((evt) => (state.handlers[evt] = fn));
						return self;
					};
				}
				if (prop === "val") {
					return (value) => {
						if (value === undefined) return state.value;
						state.value = value;
						return self;
					};
				}
				if (prop === "get") return () => ({});
				if (prop === "offset") return () => ({ left: 0, top: 0 });
				if (prop === "prop") return (_k, v) => (v === undefined ? false : self);
				if (prop === "data") return () => undefined;
				if (prop === "find") return () => element();
				return () => self;
			},
		});
		registry.push(self);
		return self;
	}
	const $ = () => element();
	return $;
}

// ------------------------------------------------------------------ fake desk + history

function makeWorld() {
	const world = {
		entries: ["/desk/somewhere-else"],
		index: 0,
		session: new Map(),
		local: new Map(),
		requests: [],
		created: [],
		confirmAnswer: true,
	};
	world.route = () => world.entries[world.index].replace(/^\/desk\//, "").split("/").filter(Boolean);
	return world;
}

function loadPage(world) {
	const registry = [];
	const $ = makeJQuery(registry);
	// Frappe's pageview creates the page wrapper as frappe.pages[name] before it evaluates the
	// script, and passes that same object to on_page_load / on_page_show.
	const pages = { "rental-planner": {} };
	const routeFlags = {};

	const show = () => {
		const route = world.route();
		const page = pages[route[0]];
		if (!page) return;
		if (!page.__loaded) {
			page.on_page_load(page);
			page.__loaded = true;
		}
		page.on_page_show(page);
	};

	class FieldGroup {
		constructor({ fields }) {
			this.fields = fields;
			this.values = {};
			this.fields_dict = new Proxy({}, { get: () => ({ $input: $() }) });
			world.fg = this;
		}
		make() {}
		set_values(values) {
			Object.assign(this.values, values);
		}
		set_value(key, value) {
			this.values[key] = value;
		}
		get_value(key) {
			return this.values[key];
		}
		get_values(ignoreErrors) {
			if (!ignoreErrors) {
				const missing = this.fields.filter((f) => f.reqd && !this.values[f.fieldname]);
				if (missing.length) return null;
			}
			return Object.assign({}, this.values);
		}
	}

	const storage = (map) => ({
		getItem: (k) => (map.has(k) ? map.get(k) : null),
		setItem: (k, v) => map.set(k, String(v)),
		removeItem: (k) => map.delete(k),
	});

	const addDays = (date, n) => {
		const d = new Date(`${date}T00:00:00Z`);
		d.setUTCDate(d.getUTCDate() + n);
		return d.toISOString().slice(0, 10);
	};

	const responders = {
		"erpnext_enhancements.asset_management.rental_planner.get_timeline": () => ({
			start: "2026-10-01 00:00:00",
			days: 31,
			fountains: [],
			pools: [],
		}),
		"erpnext_enhancements.asset_management.rental_availability.get_availability": () => ({
			fountains: [{ asset: "F-1", asset_name: "Tiered Fountain", item_code: "TF", available: true, conflicts: [] }],
			pools: [],
		}),
		"erpnext_enhancements.asset_management.rental_planner.create_rental": (args) => {
			world.created.push(args.data);
			return { name: "RNT-2026-00001", project: null };
		},
	};

	const frappe = {
		pages,
		route_flags: routeFlags,
		route_options: null,
		get_route: () => world.route(),
		set_route(...args) {
			const route = Array.isArray(args[0]) ? args[0] : args;
			const target = "/desk/" + route.join("/");
			if (routeFlags.replace_route) {
				world.entries[world.index] = target;
			} else if (world.entries[world.index] !== target) {
				world.entries = world.entries.slice(0, world.index + 1);
				world.entries.push(target);
				world.index += 1;
			}
			show();
			return Promise.resolve();
		},
		call({ method, args }) {
			world.requests.push(method);
			const respond = responders[method];
			return Promise.resolve({ message: respond ? respond(args || {}) : null });
		},
		confirm(_message, yes, no) {
			(world.confirmAnswer ? yes : no)();
		},
		msgprint: (m) => (world.lastMessage = typeof m === "string" ? m : m.message),
		show_alert: () => {},
		ui: {
			make_app_page: () => ({
				main: $(),
				set_title: (t) => (world.title = t),
				clear_actions: () => {},
				set_primary_action: (label, fn) => (world.primary = { label, fn }),
				set_secondary_action: () => {},
			}),
			FieldGroup,
			form: {
				make_control: () => ({ set_value() {}, get_value: () => "" }),
				make_quick_entry: () => {},
			},
		},
		datetime: {
			get_today: () => "2026-10-01",
			add_days: addDays,
			str_to_user: (v) => String(v || ""),
			str_to_obj: (v) => new Date(String(v).replace(" ", "T")),
			get_day_diff: () => 0,
		},
		utils: { escape_html: (s) => s },
	};

	const window = {
		sessionStorage: storage(world.session),
		localStorage: storage(world.local),
		history: {
			back() {
				world.pendingTraversal = -1;
			},
		},
	};

	const context = {
		frappe,
		window,
		document: { getElementById: () => null, head: {}, createTextNode: (t) => t },
		$,
		__: (text, args) => (args ? text.replace(/\{(\d+)\}/g, (_m, i) => args[i]) : text),
		moment: (d) => ({ format: () => "2026-10-01", add: () => ({ toDate: () => new Date(d) }) }),
		format_currency: (v) => String(v),
		console,
	};
	vm.createContext(context);
	vm.runInContext(fs.readFileSync(SCRIPT, "utf8"), context, { filename: SCRIPT });

	const desk = {
		world,
		registry,
		show,
		planner: () => pages["rental-planner"].rental_planner,
		open(pathname) {
			world.entries = world.entries.slice(0, world.index + 1);
			world.entries.push(pathname);
			world.index += 1;
			show();
		},
		async back() {
			if (world.index === 0) return;
			world.index -= 1;
			show();
			await settle();
		},
		async forward() {
			if (world.index >= world.entries.length - 1) return;
			world.index += 1;
			show();
			await settle();
		},
		// The page's own history.back() lands like the browser's.
		async flush() {
			await settle();
			if (world.pendingTraversal) {
				world.pendingTraversal = 0;
				await desk.back();
			}
			await settle();
		},
		async click(label) {
			const el = [...registry].reverse().find((e) => e.__state.text === label && e.__state.handlers.click);
			if (!el) throw new Error(`no clickable "${label}"`);
			el.__state.handlers.click({ preventDefault() {}, stopPropagation() {} });
			await desk.flush();
		},
		here: () => world.entries[world.index],
		length: () => world.entries.length,
	};
	return desk;
}

async function throughDates(desk) {
	desk.world.fg.values = {
		delivery_datetime: "2026-10-03 09:00:00",
		takedown_datetime: "2026-10-04 17:00:00",
		event_name: "Gala",
	};
	await desk.click("Next: Fountains");
}

// ------------------------------------------------------------------ scenarios

test("New Rental is an entry; Back returns to the board", async () => {
	const desk = loadPage(makeWorld());
	desk.open("/desk/rental-planner");
	await settle();
	check("board title", desk.world.title, "Rental Planner");
	desk.world.primary.fn();
	await desk.flush();
	check("on step 1", desk.here(), "/desk/rental-planner/new/dates");
	await desk.back();
	check("Back to the board", desk.here(), "/desk/rental-planner");
	check("board shown again", desk.world.title, "Rental Planner");
});

test("Each Next is an entry; Back and Forward walk the steps", async () => {
	const desk = loadPage(makeWorld());
	desk.open("/desk/rental-planner/new/dates");
	await settle();
	await throughDates(desk);
	check("on step 2", desk.here(), "/desk/rental-planner/new/fountains");
	await desk.back();
	check("Back to step 1", desk.here(), "/desk/rental-planner/new/dates");
	check("dates kept", desk.world.fg.values.event_name, "Gala");
	await desk.forward();
	check("Forward to step 2", desk.here(), "/desk/rental-planner/new/fountains");
});

test("The flow's Back button steps back through history, adding no entry", async () => {
	const desk = loadPage(makeWorld());
	desk.open("/desk/rental-planner/new/dates");
	await settle();
	await throughDates(desk);
	const before = desk.length();
	await desk.click("Back");
	check("on step 1", desk.here(), "/desk/rental-planner/new/dates");
	check("no new entry", desk.length(), before);
	await desk.forward();
	check("Forward still reaches step 2", desk.here(), "/desk/rental-planner/new/fountains");
});

test("A step whose earlier steps are unfinished is corrected in place", async () => {
	const desk = loadPage(makeWorld());
	desk.open("/desk/rental-planner/new/review");
	await settle();
	check("corrected to step 1", desk.here(), "/desk/rental-planner/new/dates");
	check("no extra entry", desk.length(), 2);
	await desk.back();
	check("Back leaves the page", desk.here(), "/desk/somewhere-else");
});

test("A reload keeps the draft and the step", async () => {
	const world = makeWorld();
	const first = loadPage(world);
	first.open("/desk/rental-planner/new/dates");
	await settle();
	await throughDates(first);
	// Pick a fountain on step 2 by writing the draft as the checkbox does.
	first.planner().draft.fountains.push({ asset: "F-1", asset_name: "Tiered Fountain", rate: 0 });
	first.planner().save_draft();
	const reloaded = loadPage(world);
	reloaded.show();
	await settle();
	check("still on step 2", reloaded.here(), "/desk/rental-planner/new/fountains");
	check("fountain kept", reloaded.planner().draft.fountains.length, 1);
	check("dates kept", reloaded.planner().draft.event_name, "Gala");
});

test("After booking, Back cannot resubmit the old draft", async () => {
	const desk = loadPage(makeWorld());
	desk.open("/desk/rental-planner/new/dates");
	await settle();
	await throughDates(desk);
	desk.planner().draft.fountains.push({ asset: "F-1", asset_name: "Tiered Fountain", rate: 0 });
	await desk.click("Next: Customer");
	check("on step 3", desk.here(), "/desk/rental-planner/new/customer");
	desk.world.fg.values = { customer: "CUST-1", create_project: 1 };
	await desk.click("Next: Review");
	check("on step 4", desk.here(), "/desk/rental-planner/new/review");
	await desk.click("Place Hold");
	check("one booking created", desk.world.created.length, 1);
	check("as a hold", desk.world.created[0].status, "Tentative");
	check("confirmation shown", desk.world.title, "Rental Booked");
	await desk.back();
	check("Back from the confirmation lands on step 1, not a filled step 3", desk.here(), "/desk/rental-planner/new/dates");
	check("draft cleared", desk.planner().draft.fountains.length, 0);
	check("still one booking", desk.world.created.length, 1);
});

test("Starting over after a started draft asks first", async () => {
	const world = makeWorld();
	world.confirmAnswer = false; // "No" = start over
	const desk = loadPage(world);
	desk.open("/desk/rental-planner/new/dates");
	await settle();
	await throughDates(desk);
	desk.open("/desk/rental-planner");
	await settle();
	desk.world.primary.fn();
	await desk.flush();
	check("fresh draft", desk.planner().draft.delivery_datetime, undefined);
	check("on step 1", desk.here(), "/desk/rental-planner/new/dates");
});

(async () => {
	for (const { name, fn } of tests) {
		const before = failures;
		try {
			await fn();
		} catch (error) {
			failures += 1;
			console.log(`  ERROR ${error && error.stack}`);
		}
		console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
	}
	if (failures) {
		console.log(`\n${failures} of ${checks} checks failed`);
		process.exit(1);
	}
	console.log(`\nall ${checks} checks passed`);
})();
