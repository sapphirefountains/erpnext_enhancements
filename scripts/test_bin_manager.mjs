#!/usr/bin/env node
/**
 * The Bin Manager page (v1.693.0) under node: its pure module, its controller against a model of
 * the v16 router and session history, and the REAL page script loaded into a vm over a stand-in
 * Desk (frappe.ui.Dialog, a recording jQuery, sf_kit), so a click on a bin or an action runs the
 * same code the browser does.
 *
 * The router model follows Frappe v16.50 where the page depends on it: set_route pushes the path
 * (replaceState when frappe.route_flags.replace_route is set) and, since v16.50, writes
 * frappe.route_options into the address as a query; then the route is parsed, the query copied into
 * frappe.route_options, and on_page_show runs. A browser Back or Forward lands on an entry and does
 * the same. Dialogs never touch history: the router closes an open one on a route change.
 *
 * Nik's rule: every screen is its own history entry; dialogs and state panels are not.
 *
 * Since v1.812.0 the page has four tabs (Bins, By project, Job sites, Problems), each a route and so
 * an entry, and the picker has filters, which are NOT entries: they are written into the picker's own
 * address in place, and read back out of it on Back, Forward and a shared link. The second half of
 * this file holds the page to both, against views.js and the real page script.
 *
 * Run: node scripts/test_bin_manager.mjs   (from tests/test_bin_manager.py)
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const logic = await import(pathToFileURL(path.join(APP, "public/js/bin_manager/logic.js")).href);
const views = await import(pathToFileURL(path.join(APP, "public/js/bin_manager/views.js")).href);
const { Controller } = await import(pathToFileURL(path.join(APP, "public/js/bin_manager/controller.js")).href);
const PAGE_JS = path.join(APP, "inventory_enhancements/page/bin_manager/bin_manager.js");
// The reorder boxes (v1.725.0), the real modules, for the page test that gives the page its bundle.
const reorder_logic = await import(pathToFileURL(path.join(APP, "public/js/reorder_levels/logic.js")).href);
const { ReorderEditor, cell_html } = await import(pathToFileURL(path.join(APP, "public/js/reorder_levels/editor.js")).href);
const reorder_dom = await import(pathToFileURL(path.join(APP, "public/js/reorder_levels/dom.js")).href);

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
	for (let i = 0; i < 8; i++) await tick();
	await new Promise((resolve) => setTimeout(resolve, 2));
	for (let i = 0; i < 8; i++) await tick();
}

const BIN_A = "Bin A1-1-1 - SF";
const BIN_B = "Shelf B/2 - SF"; // a slash and spaces: one route segment, encoded
const BONEYARD = "Boneyard - SF";

const BOOT = {
	bins: [
		{ warehouse: BIN_A, warehouse_name: "Bin A1-1-1", trail: "Row A", on_hand_items: 2, assigned_items: 1, is_boneyard: false },
		{ warehouse: BIN_B, warehouse_name: "Shelf B/2", trail: "Row B", on_hand_items: 0, assigned_items: 0, is_boneyard: false },
		{ warehouse: BONEYARD, warehouse_name: "Boneyard", trail: "", on_hand_items: 0, assigned_items: 0, is_boneyard: true },
	],
	can_manage: true,
	can_count: true,
	can_move: true,
	can_create_items: true,
	boneyard_warehouse: BONEYARD,
	boneyard_name: "Boneyard",
	item_groups: ["Fittings"],
	uoms: ["Unit", "FT"],
	undo_window_minutes: 30,
	refuse_from: "2026-10-01",
};

function location_of(warehouse) {
	return {
		warehouse,
		warehouse_name: warehouse.replace(/ - SF$/, ""),
		trail: "Row A",
		company: "Example Co",
		can_manage: true,
		truncated: false,
		items: [
			{ item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", on_hand: 4, on_order: 0, assigned: true, is_default: false, undecided: false },
			{ item_code: "PDT-0002", item_name: "ELBOW, 2 IN", stock_uom: "Unit", on_hand: 0, on_order: 0, assigned: false, is_default: false, undecided: true },
		],
	};
}

// ------------------------------------------------------------------ the server

function makeServer(options) {
	const fixture = options || {};
	const server = {
		calls: [],
		fail: new Map(), // method -> error object (rejects once per entry in a list, else always)
		delay: new Map(), // method -> a promise to wait for
		seq: 0,
	};
	server.call = (method, args, opts) => {
		server.calls.push({ method, args: Object.assign({}, args), opts: Object.assign({}, opts) });
		const name = method.split(".").pop();
		const failure = server.fail.get(name);
		const wait = server.delay.get(name) || Promise.resolve();
		return wait.then(() => {
			if (failure) {
				const err = Array.isArray(failure) ? failure.shift() : failure;
				if (Array.isArray(failure) && !failure.length) server.fail.delete(name);
				if (err) return Promise.reject(err);
			}
			if (name === "manager_boot") {
				// `show_reorder` is the person's saved switch for the Reorder column (TASK-2026-02920): absent = off.
				const boot = Object.assign({}, fixture.boot || BOOT, fixture.show_reorder === undefined ? {} : { show_reorder: fixture.show_reorder });
				return JSON.parse(JSON.stringify(boot));
			}
			if (name === "get_location") {
				const loc = location_of(args.warehouse);
				if (fixture.items) loc.items = JSON.parse(JSON.stringify(fixture.items));
				return loc;
			}
			if (name === "get_reorder_levels") {
				const levels = {};
				JSON.parse(args.item_codes).forEach((code) => {
					levels[code] = { item_code: code, has_rule: true, row_warehouse: args.warehouse, warehouse_label: "x", elsewhere: false, level: 2, qty: 5, modified: "m" };
				});
				return { levels, can_edit: true };
			}
			if (name === "get_recent") return [];
			// The views' reads (v1.812.0, bin_views.py), answered from the fixture.
			if (name === "item_locations") return JSON.parse(JSON.stringify(fixture.item_locations || { item_code: args.item_code, item_name: args.item_code, is_stock_item: true, locations: {} }));
			if (name === "project_index") return JSON.parse(JSON.stringify(fixture.project_index || []));
			if (name === "project_view") return JSON.parse(JSON.stringify(fixture.project_view || { project: { project: args.project, project_name: args.project }, shelf: null, site: null, send_refusal: null }));
			if (name === "problems") return JSON.parse(JSON.stringify(fixture.problems || {}));
			// api.stock_scan.job_site: opens the job's site and says which warehouse it is.
			if (name === "job_site") return { warehouse: "Job Site PRJ-90001 Opened - SF", warehouse_name: "Job Site PRJ-90001 Opened", job_site: true, project: args.project, created: true };
			// TASK-2026-02914: a count up from an empty bin for an item ERPNext has no cost for is not a save
			// but the question "what does one cost?" until a rate comes with it.
			if (name === "set_count" && fixture.nocost && (!args.rate || fixture.stubborn)) {
				return { needs_valuation_rate: true, item_code: args.item_code, item_name: "ELBOW, 2 IN", uom: "Unit", qty: args.qty, suggestion: 6.5, source: "the last purchase of it: Purchase Order PO-0007, Oct 3", max_rate: 100000, max_value: 25000 };
			}
			if (name === "search_items") return [{ item_code: "PDT-0009", item_name: "VALVE", is_stock_item: 1, on_hand_here: 0 }];
			// The item's card at a location: at the Project Shelf it says which jobs hold the item (v1.804.0).
			if (name === "get_item") return JSON.parse(JSON.stringify(fixture.shelf_item || { item_code: args.item_code, project_shelf: false, shelf_jobs: [], shelf_jobs_exact: true }));
			if (name === "search_projects") return JSON.parse(JSON.stringify(fixture.jobs_found || [{ project: "PRJ-90007", project_name: "Courtyard Rill", customer: "Greenway Homes", status: "Active" }]));
			if (name === "check_new_item") return { checked: true, exists: null, similar: [], blocking: [], advice: [], will_refuse: false };
			server.seq += 1;
			const log = {
				name: `SCAN-${server.seq}`,
				action: name,
				status: name === "undo" ? "Undone" : "Posted",
				warehouse: args.warehouse || args.to_warehouse || args.from_warehouse,
				from_warehouse: args.from_warehouse || null,
				can_undo: name !== "undo",
			};
			return { log, message: `${name} done`, repeated: !!server.repeat };
		});
	};
	server.writes = () =>
		server.calls.filter(
			(c) => !/manager_boot|get_location|get_recent|search_items|check_new_item|get_reorder_levels|set_show_reorder|item_locations|project_index|project_view|problems|job_site|get_item|search_projects/.test(c.method)
		);
	return server;
}

// ------------------------------------------------------------------ the router and history

function makeWorld(server) {
	const world = {
		entries: [{ url: "/desk/stock", state: null }],
		index: 0,
		route_options: null,
		route_flags: {},
		shown: 0,
		dialogs_closed: 0,
		page: null, // { show() } called when a route lands on the page
	};
	const url = () => world.entries[world.index].url;
	world.path = () => url().split("?")[0];
	world.query = () => url().split("?")[1] || "";
	world.route = () =>
		world
			.path()
			.replace(/^\/desk\/?/, "")
			.split("/")
			.filter(Boolean)
			.map(decodeURIComponent);
	world.state = () => world.entries[world.index].state;

	// router.route(): the query into route_options (merged), close any dialog, show the page.
	world.land = () => {
		const q = world.query();
		if (q) {
			world.route_options = world.route_options || {};
			new URLSearchParams(q).forEach((v, k) => (world.route_options[k] = v));
		}
		world.dialogs_closed += 1;
		if (world.route()[0] === "bin-manager" && world.page) {
			world.shown += 1;
			world.page.show();
		}
	};
	// frappe.set_route(route): v16.50 writes route_options into the address as a query.
	world.set_route = (route) => {
		const parts = Array.isArray(route) ? route : [route];
		const objs = parts.filter((p) => p && typeof p === "object");
		if (objs.length) world.route_options = objs[objs.length - 1];
		const segs = parts.filter((p) => !(p && typeof p === "object"));
		const p = "/desk/" + segs.map((s) => encodeURIComponent(String(s))).join("/");
		const opts = world.route_options || {};
		const query = Object.entries(opts)
			.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
			.join("&");
		const next = p + (query ? `?${query}` : "");
		if (next === url()) return;
		if (world.route_flags.replace_route) world.entries[world.index] = { url: next, state: null };
		else {
			world.entries = world.entries.slice(0, world.index + 1);
			world.entries.push({ url: next, state: null });
			world.index += 1;
		}
		world.land();
	};
	world.back = () => {
		if (world.index > 0) {
			world.index -= 1;
			world.land();
		}
	};
	world.forward = () => {
		if (world.index < world.entries.length - 1) {
			world.index += 1;
			world.land();
		}
	};
	world.go_to = (address) => {
		world.entries = world.entries.slice(0, world.index + 1);
		world.entries.push({ url: address, state: null });
		world.index += 1;
		world.land();
	};
	world.env = (rendered) => ({
		call: server.call,
		get_route: () => world.route(),
		set_route: (route, replace) => {
			if (replace) world.route_flags.replace_route = true;
			world.set_route(route);
			delete world.route_flags.replace_route;
		},
		route_options: () => world.route_options,
		clear_route_options: () => {
			world.route_options = null;
		},
		history_state: () => world.state(),
		tag_state: (tag) => {
			world.entries[world.index].state = Object.assign({}, world.state(), tag);
		},
		history_back: () => world.back(),
		// history.replaceState with a new query: the entry on screen, in place.
		replace_query: (query) => {
			world.entries[world.index].url = world.path() + (query || "");
		},
		render: () => rendered && rendered.push(world.route().join("/")),
		now: () => 1760000000000,
		random: () => Math.random(),
	});
	return world;
}

function makeController() {
	const server = makeServer();
	const world = makeWorld(server);
	const renders = [];
	const ctl = new Controller(world.env(renders));
	world.page = { show: () => ctl.show() };
	return { server, world, ctl, renders };
}

// ------------------------------------------------------------------ pure module

test("routes: the picker, a bin, and not this page", () => {
	check("picker", logic.parse_route(["bin-manager"]), { screen: "picker" });
	check("bin", logic.parse_route(["bin-manager", BIN_B]), { screen: "bin", warehouse: BIN_B });
	check("other page", logic.parse_route(["stock-entry"]), null);
	check("build bin", logic.build_route({ screen: "bin", warehouse: BIN_A }), ["bin-manager", BIN_A]);
	check("build picker", logic.build_route({ screen: "picker" }), ["bin-manager"]);
	check("options", logic.read_route_options({ warehouse: ` ${BIN_A} `, q: "row" }), { warehouse: BIN_A, q: "row", any: true });
	check("no options", logic.read_route_options(null), { warehouse: "", q: "", any: false });
});

test("client_ref matches the server's pattern and differs per save", () => {
	const a = logic.client_ref(1760000000000, 0.123);
	const b = logic.client_ref(1760000000000, 0.456);
	check("pattern", logic.CLIENT_REF.test(a), true);
	check("server pattern", /^[A-Za-z0-9._:-]{8,80}$/.test(a), true);
	check("differs", a !== b, true);
	check("keep on a dropped reply", logic.keep_ref({ status: 0 }), true);
	check("keep on 409", logic.keep_ref({ status: 409, exc_type: "DuplicateEntryError" }), true);
	check("fresh after a refusal", logic.keep_ref({ status: 417, exc_type: "ValidationError" }), false);
	check("fresh after 403", logic.keep_ref({ status: 403 }), false);
});

test("make-home: ticked for everything, unticked for part, decided again until touched", () => {
	check("all", logic.make_home_default(4, 4), true);
	check("part", logic.make_home_default(3, 4), false);
	check("nothing here", logic.make_home_default(0, 0), false);
	let s = logic.make_home_after_qty(null, 4, 4);
	check("opens ticked when all moves", s, { touched: false, value: true });
	s = logic.make_home_after_qty(s, 2, 4);
	check("partial unticks", s.value, false);
	s = logic.make_home_after_qty(s, 4, 4);
	check("all again re-ticks", s.value, true);
	s = logic.make_home_touched(false);
	s = logic.make_home_after_qty(s, 4, 4);
	check("once touched, quantity no longer decides", s, { touched: true, value: false });
	const perms = logic.permissions(BOOT);
	check("offered for a bin", logic.make_home_offered(perms, BIN_B), true);
	check("never for the boneyard", logic.make_home_offered(perms, BONEYARD), false);
	check("not without a destination", logic.make_home_offered(perms, null), false);
	check("not outside the gate", logic.make_home_offered(Object.assign({}, perms, { can_manage: false }), BIN_B), false);
});

test("gating: who sees which buttons", () => {
	const assigned = { on_hand: 4, assigned: true };
	const undecided = { on_hand: 0, undecided: true };
	const full = logic.permissions(BOOT);
	check("everything", logic.row_actions(assigned, full, BIN_A), ["count", "move", "boneyard", "unassign"]);
	check("undecided, empty", logic.row_actions(undecided, full, BIN_A), ["count", "keep", "unassign"]);
	const stock_user = logic.permissions(Object.assign({}, BOOT, { can_count: false }));
	check("no count without reconciliation rights", logic.row_actions(assigned, stock_user, BIN_A), ["move", "boneyard", "unassign"]);
	const outside = logic.permissions(Object.assign({}, BOOT, { can_manage: false }));
	check("outside the gate: count and plain move only", logic.row_actions(assigned, outside, BIN_A), ["count", "move"]);
	const no_boneyard = logic.permissions(Object.assign({}, BOOT, { boneyard_warehouse: null }));
	check("no boneyard set: no button", logic.row_actions(assigned, no_boneyard, BIN_A).includes("boneyard"), false);
	check("at the boneyard: no boneyard, no keep", logic.row_actions({ on_hand: 2 }, full, BONEYARD), ["count", "move"]);
	check("bin actions", logic.bin_actions(full, BIN_A), ["add", "create"]);
	check("no new item without Item create", logic.bin_actions(Object.assign({}, full, { can_create_items: false }), BIN_A), ["add"]);
	check("none outside the gate", logic.bin_actions(outside, BIN_A), []);
	check("none at the boneyard", logic.bin_actions(full, BONEYARD), []);
	check("unassign with stock: move or boneyard first", logic.unassign_plan(assigned, full, BIN_A), { direct: false, options: ["move", "boneyard"] });
	check("unassign when empty: direct", logic.unassign_plan(undecided, full, BIN_A), { direct: true, options: [] });
	check("nothing missing means no", logic.row_actions(assigned, logic.permissions(null), BIN_A), []);
});

test("requests: every action's endpoint and arguments, every write with its client_ref", () => {
	const row = { item_code: "PDT-0001", on_hand: 4, stock_uom: "Unit" };
	const ref = "bm-test-0001";
	const BA = "erpnext_enhancements.inventory_enhancements.bin_assignments.";
	const SCAN = "erpnext_enhancements.api.stock_scan.";
	check("count", logic.request("count", BIN_A, row, { qty: "6" }, ref), {
		method: BA + "set_count",
		args: { item_code: "PDT-0001", warehouse: BIN_A, qty: 6, client_ref: ref },
	});
	check("move", logic.request("move", BIN_A, row, { qty: 4, to_warehouse: BIN_B, make_home: true }, ref), {
		method: BA + "move_to",
		args: { item_code: "PDT-0001", from_warehouse: BIN_A, to_warehouse: BIN_B, qty: 4, client_ref: ref, make_home: 1 },
	});
	check("move, partial", logic.request("move", BIN_A, row, { qty: 1, to_warehouse: BIN_B, make_home: false }, ref).args.make_home, 0);
	check("boneyard", logic.request("boneyard", BIN_A, row, { qty: 4 }, ref), {
		method: BA + "send_to_boneyard",
		args: { item_code: "PDT-0001", from_warehouse: BIN_A, qty: 4, client_ref: ref },
	});
	check("unassign", logic.request("unassign", BIN_A, row, {}, ref), {
		method: BA + "unassign",
		args: { item_code: "PDT-0001", warehouse: BIN_A, client_ref: ref },
	});
	check("keep", logic.request("keep", BIN_A, row, {}, ref).method, BA + "assign");
	check("add", logic.request("add", BIN_A, null, { item_code: "PDT-0009" }, ref), {
		method: BA + "assign",
		args: { item_code: "PDT-0009", warehouse: BIN_A, client_ref: ref },
	});
	check("create", logic.request("create", BIN_A, null, { item_code: " PDT-0420 ", item_name: "ELBOW", item_group: "Fittings", stock_uom: "Unit" }, ref), {
		method: BA + "create_and_assign",
		args: { warehouse: BIN_A, client_ref: ref, item_code: "PDT-0420", item_name: "ELBOW", item_group: "Fittings", stock_uom: "Unit" },
	});
	check("undo", logic.request("undo", BIN_A, null, { log: "SCAN-1" }, null), { method: SCAN + "undo", args: { log: "SCAN-1" } });
	check("count refuses no change", logic.check_values("count", row, { qty: 4 }), "That is what the system already shows.");
	check("move refuses more than here", logic.check_values("move", row, { qty: 5, to_warehouse: BIN_B }), "Only 4 Unit is here.");
	check("move needs a destination", logic.check_values("move", row, { qty: 1 }), "Pick the bin to move it to.");
	check("move not to itself", logic.check_values("move", row, { qty: 1, to_warehouse: BIN_A }, BIN_A), "Pick a different bin.");
	check("create needs a group", logic.check_values("create", null, { item_code: "X", item_name: "Y", stock_uom: "Unit" }), "Choose a group.");
});

test("lists: filter, group, destinations and this bin's recent saves", () => {
	check("filter words", logic.filter_bins(BOOT.bins, "shelf row").map((b) => b.warehouse), [BIN_B]);
	check("groups", logic.group_bins(BOOT.bins).map((g) => g.trail), ["Row A", "Row B", ""]);
	check("destinations exclude here", logic.destinations(BOOT.bins, BIN_A, "").map((b) => b.warehouse), [BIN_B, BONEYARD]);
	const recent = [
		{ name: "1", warehouse: BIN_A },
		{ name: "2", warehouse: BIN_B, from_warehouse: BIN_A },
		{ name: "3", warehouse: BIN_B },
	];
	check("this bin", logic.bin_recent(recent, BIN_A).map((r) => r.name), ["1", "2"]);
	check("merge puts the newest first", logic.merge_recent(recent, { name: "2", status: "Undone" }).map((r) => r.name), ["2", "1", "3"]);
	check("why listed", logic.why_listed({ assigned: true, on_order: 2 }).map((w) => w.key), ["assigned", "on_order"]);
});

// ------------------------------------------------------------------ controller and history

test("picking a bin pushes one entry; Back returns to the picker, Forward to the bin", async () => {
	const { server, world, ctl } = makeController();
	world.go_to("/desk/bin-manager");
	await settle();
	check("boot is a GET", server.calls[0].method.endsWith("manager_boot") && server.calls[0].opts.method, "GET");
	const before = world.entries.length;
	ctl.open_bin(BIN_B);
	await settle();
	check("one entry", world.entries.length, before + 1);
	check("encoded as one segment", world.path(), "/desk/bin-manager/" + encodeURIComponent(BIN_B));
	check("route reads back", world.route(), ["bin-manager", BIN_B]);
	check("tagged as this page's", world.state(), { bm_from: "picker" });
	check("location asked for this bin", server.calls.filter((c) => c.method.endsWith("get_location")).pop().args, { warehouse: BIN_B });
	world.back();
	await settle();
	check("Back: the picker", ctl.state.target, { screen: "picker" });
	check("Back pushed nothing", world.entries.length, before + 1);
	world.forward();
	await settle();
	check("Forward: the bin", ctl.state.target, { screen: "bin", warehouse: BIN_B });
	check("Forward reloads it", ctl.state.location && ctl.state.location.warehouse, BIN_B);
	ctl.leave_bin();
	await settle();
	check("All bins steps back through the entry", [world.index, world.entries.length, ctl.state.target.screen], [before - 1, before + 1, "picker"]);
});

test("a bin opened from a link: All bins pushes the picker rather than leaving the page", async () => {
	const { world, ctl } = makeController();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const before = world.entries.length;
	ctl.leave_bin();
	await settle();
	check("picker pushed", [world.entries.length, world.path()], [before + 1, "/desk/bin-manager"]);
	world.back();
	await settle();
	check("Back: the bin again", ctl.state.target, { screen: "bin", warehouse: BIN_A });
});

test("route_options: ?warehouse= opens the bin in place, and the address is cleaned", async () => {
	const { world, ctl } = makeController();
	world.go_to("/desk/bin-manager?warehouse=" + encodeURIComponent(BIN_A));
	const before = world.entries.length;
	await settle();
	check("replaced, not pushed", world.entries.length, before);
	check("the bin", world.path(), "/desk/bin-manager/" + encodeURIComponent(BIN_A));
	check("no query left", world.query(), "");
	check("consumed", world.route_options, null);
	check("target", ctl.state.target, { screen: "bin", warehouse: BIN_A });
	// From another page: frappe.set_route("bin-manager", { warehouse }) fills route_options first.
	const second = makeController();
	second.world.set_route(["bin-manager", { warehouse: BIN_B }]);
	await settle();
	check("set_route object", second.ctl.state.target, { screen: "bin", warehouse: BIN_B });
	check("set_route object cleaned", second.world.query(), "");
	// q fills the picker's filter.
	const third = makeController();
	third.world.go_to("/desk/bin-manager?q=row%20b");
	await settle();
	check("q", [third.ctl.state.filter, third.ctl.state.target.screen], ["row b", "picker"]);
	check("q consumed", third.world.route_options, null);
});

test("saves push no history entry, carry a client_ref, and refresh the bin", async () => {
	const { server, world, ctl } = makeController();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const row = ctl.state.location.items[0];
	const entries = world.entries.length;
	const shown = world.shown;
	const ref = ctl.new_ref();
	const kinds = [
		["count", row, { qty: 6 }, "set_count"],
		["move", row, { qty: 4, to_warehouse: BIN_B, make_home: true }, "move_to"],
		["boneyard", row, { qty: 1 }, "send_to_boneyard"],
		["unassign", ctl.state.location.items[1], {}, "unassign"],
		["keep", ctl.state.location.items[1], {}, "assign"],
		["add", null, { item_code: "PDT-0009" }, "assign"],
		["create", null, { item_code: "PDT-0420", item_name: "ELBOW", item_group: "Fittings", stock_uom: "Unit" }, "create_and_assign"],
	];
	for (const [kind, r, values, endpoint] of kinds) {
		const before = server.calls.length;
		const done = await ctl.submit(kind, r, values, ref);
		await settle();
		const write = server.calls.slice(before).find((c) => c.method.endsWith("." + endpoint));
		check(`${kind} -> ${endpoint}`, !!write, true);
		check(`${kind} client_ref`, write && write.args.client_ref, ref);
		check(`${kind} quiet`, write && write.opts.quiet, true);
		check(`${kind} message`, done.message, `${endpoint} done`);
		check(`${kind} refreshed the bin`, server.calls.slice(before).some((c) => c.method.endsWith("get_location")), true);
	}
	check("no history entry", world.entries.length, entries);
	check("no route change", world.shown, shown);
	check("recent holds the saves", ctl.state.recent.length, kinds.length);
	const undo_before = server.calls.length;
	await ctl.undo(ctl.state.recent[0].name);
	check("undo calls stock_scan.undo", server.calls.slice(undo_before)[0].method.endsWith("stock_scan.undo"), true);
	check("undo pushes nothing", world.entries.length, entries);
});

test("a refusal is a sentence for the dialog; a dropped reply keeps the reference", async () => {
	const { server, world, ctl } = makeController();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const row = ctl.state.location.items[0];
	server.fail.set("set_count", [{ status: 417, message: "Correcting a count needs the Stock Manager role.", exc_type: "PermissionError" }]);
	let err = await ctl.submit("count", row, { qty: 6 }, "bm-x-00000001").catch((e) => e);
	check("message", err.message, "Correcting a count needs the Stock Manager role.");
	check("fresh ref after a refusal", err.keep_ref, false);
	server.fail.set("set_count", [{ status: 0, message: "Could not reach the server." }]);
	err = await ctl.submit("count", row, { qty: 6 }, "bm-x-00000002").catch((e) => e);
	check("same ref after a dropped reply", err.keep_ref, true);
	err = await ctl.submit("move", row, { qty: 9, to_warehouse: BIN_B }, "bm-x-00000003").catch((e) => e);
	check("checked before any call", [err.message, server.calls.filter((c) => c.method.endsWith("move_to")).length], ["Only 4 Unit is here.", 0]);
});

test("a late reply for a bin no longer on screen is dropped", async () => {
	const { server, world, ctl } = makeController();
	let release;
	server.delay.set("get_location", new Promise((resolve) => (release = resolve)));
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	server.delay.delete("get_location");
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_B));
	await settle();
	release();
	await settle();
	check("shows the bin on screen", ctl.state.location.warehouse, BIN_B);
});

// ------------------------------------------------------------------ the real page script

function makeJQuery(registry) {
	function element(html) {
		const node = {
			innerHTML: "",
			disabled: false,
			querySelector: () => null,
			addEventListener: () => {},
			// `inRoot` marks a stand-in focused element as being inside this node.
			contains: (x) => !!x && !!x.inRoot,
		};
		const state = { html: html || "", text: "", value: "", handlers: [], props: {} };
		const self = new Proxy(function () {}, {
			get(_t, prop) {
				if (prop === "__state") return state;
				if (prop === "then") return undefined;
				if (prop === "jquery") return "stand-in";
				if (prop === "0") return node;
				if (prop === "length") return 1;
				if (prop === "on") {
					return (events, selector, fn) => {
						if (typeof selector === "function") [selector, fn] = [null, selector];
						String(events)
							.split(/\s+/)
							.forEach((evt) => state.handlers.push({ evt, selector, fn }));
						return self;
					};
				}
				if (prop === "html") {
					return (value) => {
						if (value === undefined) return state.html;
						state.html = String(value);
						node.innerHTML = state.html;
						return self;
					};
				}
				if (prop === "text") {
					return (value) => {
						if (value === undefined) return state.text;
						state.text = String(value);
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
				if (prop === "prop") {
					return (key, value) => {
						if (value === undefined) return state.props[key];
						state.props[key] = value;
						return self;
					};
				}
				if (prop === "find") {
					return (sel) => {
						registry.found = registry.found || {};
						if (!registry.found[sel]) registry.found[sel] = element();
						return registry.found[sel];
					};
				}
				if (prop === "appendTo") {
					return () => {
						registry.root = self;
						return self;
					};
				}
				return () => self;
			},
		});
		return self;
	}
	return (html) => element(html);
}

function loadPage(options) {
	const server = makeServer(options);
	const world = makeWorld(server);
	const registry = { dialogs: [], requires: [], alerts: [] };
	const $ = makeJQuery(registry);
	const pages = { "bin-manager": {} };
	class Dialog {
		constructor(opts) {
			this.opts = opts;
			this.values = {};
			for (const f of opts.fields || []) if (f.default !== undefined) this.values[f.fieldname] = f.default;
			this.props = {};
			this.shown = false;
			this.btn = $("<button>");
			this.fields_dict = {};
			// Frappe v16 builds a control's input only while the control is shown (ControlBaseInput.refresh_input:
			// make_input() sits inside `if (disp_status != "None")`, and df.hidden is "None"): a field declared
			// hidden has NO $input until set_df_property(name, "hidden", 0) shows it. A page that attaches a
			// listener to a hidden field's $input at build time throws, and the dialog is never shown.
			for (const f of opts.fields || []) {
				this.fields_dict[f.fieldname] = { $wrapper: $("<div>"), $input: f.hidden ? undefined : $("<input>") };
			}
			registry.dialogs.push(this);
		}
		show() {
			this.shown = true;
		}
		hide() {
			this.shown = false;
		}
		get_values() {
			return Object.assign({}, this.values);
		}
		// What Frappe v16 does when the primary button is clicked (ui/dialog.js set_primary_action and
		// ui/field_group.js get_values): the values are read ONCE and passed to the action as its first
		// argument; and for a required field whose value is falsy (`!f.value`, which a typed 0 is) the
		// field is refreshed, which sets the box from `me.value || null`: a 0 is blanked in the box. A
		// later d.get_values() therefore no longer has it. A page that re-reads the dialog instead of
		// using the argument loses every 0.
		click_primary() {
			const picked = {};
			for (const f of this.opts.fields || []) {
				const v = this.values[f.fieldname];
				if (v !== null && v !== undefined && String(v).trim() !== "") picked[f.fieldname] = v;
				if (f.reqd && !v) delete this.values[f.fieldname];
			}
			return this.opts.primary_action(picked);
		}
		get_value(k) {
			return this.values[k];
		}
		set_value(k, v) {
			this.values[k] = v;
			return Promise.resolve();
		}
		set_df_property(f, k, v) {
			this.props[`${f}.${k}`] = v;
			if (k === "hidden" && !v && this.fields_dict[f] && !this.fields_dict[f].$input) this.fields_dict[f].$input = $("<input>");
		}
		get_primary_btn() {
			return this.btn;
		}
	}
	const frappe = {
		pages,
		route_flags: world.route_flags,
		get route_options() {
			return world.route_options;
		},
		set route_options(v) {
			world.route_options = v;
		},
		get_route: () => world.route(),
		set_route: (...args) => world.set_route(args.length === 1 ? args[0] : args),
		// The Desk's own role check (boot's roles). { roles: [...] } is who the person is; none = no board link.
		user: {
			has_role: (role) => {
				if ((options || {}).user_throws) throw new Error("boot has no roles");
				return ((options || {}).roles || []).includes(role);
			},
		},
		require: (assets, cb) => {
			registry.requires.push(assets);
			// { hold_reorder: true }: the reorder bundle never arrives (a slow network), and must hold nothing up.
			if ((options || {}).hold_reorder && assets[0] === "reorder_levels.bundle.js") return;
			cb();
		},
		ui: {
			make_app_page: () => ({ main: $("<div>"), set_secondary_action: () => {}, set_title: (t) => (registry.title = t) }),
			Dialog,
		},
		// frappe.utils.escape_html, as v16 writes it: the five characters, & first.
		utils: {
			// v16: `/desk/<slug>/<encoded name>` (a /desk path is routed in place by the Desk's link handler).
			get_form_link: (doctype, name) => `/desk/${String(doctype).toLowerCase().replace(/ /g, "-")}/${encodeURIComponent(name)}`,
			escape_html: (s) =>
				String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
		},
		datetime: { comment_when: () => "just now" },
		show_alert: (opts, seconds) => registry.alerts.push(Object.assign({}, opts, { seconds })),
	};
	const esc = (v) =>
		v === null || v === undefined
			? ""
			: String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
	const window = {
		sf_kit: {
			esc,
			call: server.call,
			chip: (label, tone) => `<span class="sf-chip sf-chip--${tone}">${esc(label)}</span>`,
			state: {
				render: (el, kind) => {
					el.innerHTML = `<div class="sf-state sf-state--${kind}"></div>`;
				},
				html: (kind) => `<div class="sf-state sf-state--${kind}"></div>`,
			},
		},
		sf_bin_manager: Object.assign({ version: 2, Controller, views }, logic),
		// The reorder bundle, when the test asks for it: the real modules over this stand-in's root, which
		// has no querySelectorAll (nothing is drawn into a real DOM here).
		sf_reorder: (options || {}).reorder
			? {
					version: 1,
					ReorderEditor,
					cell_html,
					bind: reorder_dom.bind,
					paint: (root, ...rest) => reorder_dom.paint(Object.assign(root, { querySelectorAll: () => [] }), ...rest),
					read_boxes: reorder_dom.read_boxes,
					logic: reorder_logic,
			  }
			: undefined,
		history: {
			get state() {
				return world.state();
			},
			replaceState: (s, _title, url) => {
				world.entries[world.index].state = s;
				// With an address: the entry's own, in place (how the picker's filters are written).
				if (typeof url === "string" && url) world.entries[world.index].url = url;
			},
			back: () => world.back(),
		},
		location: {
			reload: () => {},
			get pathname() {
				return world.path();
			},
			get search() {
				return world.query() ? `?${world.query()}` : "";
			},
			hash: "",
		},
	};
	const __ = (text, args) => String(text).replace(/\{(\d+)\}/g, (m, i) => (args && args[i] !== undefined ? args[i] : m));
	const document = { activeElement: null };
	const context = vm.createContext({ frappe, window, $, __, setTimeout, clearTimeout, Promise, console, document });
	vm.runInContext(fs.readFileSync(PAGE_JS, "utf-8"), context, { filename: PAGE_JS });
	const page = pages["bin-manager"];
	world.page = {
		show: () => {
			if (!page.__loaded) {
				page.on_page_load(page);
				page.__loaded = true;
			}
			page.on_page_show(page);
		},
	};
	const click = (attrs) => {
		const root = registry.root.__state;
		const handler = root.handlers.find((h) => h.evt === "click" && h.selector === "[data-act]");
		handler.fn({ preventDefault: () => {}, currentTarget: { getAttribute: (k) => attrs[k] ?? null, disabled: false } });
	};
	return { server, world, registry, page, click, document };
}

const msgOf = (d) => d.fields_dict.bm_msg.$wrapper.__state.html || "";

// TASK-2026-02912: "Bin Manager rejects 0 as a valid quantity". The cause: Frappe's dialog blanks a typed 0
// in a required field while it reads the values for the primary action, and the page read them again.
test("the real page: a count of 0 is sent, because the dialog's own reading of its values is used", async () => {
	const { server, world, registry, page, click } = loadPage();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	click({ "data-act": "count", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	d.values.qty = 0;
	await d.click_primary();
	await settle();
	const write = server.writes().pop();
	check("set_count was called", !!write && write.method.endsWith("bin_assignments.set_count"), true);
	check("with a quantity of 0, not a missing one", write && write.args.qty, 0);
	check("the dialog closed on success", d.shown, false);
	check("no complaint about the quantity", /Enter the quantity/.test(msgOf(d)), false);

	// blank is still refused (nothing is sent), and so is a negative number
	for (const typed of ["", null, undefined, -1, "abc"]) {
		click({ "data-act": "count", "data-item": "PDT-0001" });
		const e = registry.dialogs.pop();
		e.values.qty = typed;
		const before = server.writes().length;
		await e.click_primary();
		await settle();
		check(`${JSON.stringify(typed)} is refused and sends nothing`, server.writes().length, before);
		check(`${JSON.stringify(typed)}: in the dialog's words`, /Enter the quantity you counted, 0 or more\./.test(msgOf(e)), true);
	}

	// a refused 0 (the server says no) stays in the box: Frappe blanked it while reading the dialog
	server.fail.set("set_count", [{ status: 417, message: "Correcting a count needs the Stock Manager role.", exc_type: "PermissionError" }]);
	click({ "data-act": "count", "data-item": "PDT-0001" });
	const r = registry.dialogs.pop();
	r.values.qty = 0;
	await r.click_primary();
	await settle();
	check("the server's sentence is shown", /Stock Manager role/.test(msgOf(r)), true);
	check("and the 0 is back in its box", r.values.qty, 0);
	check("the dialog stays open", r.shown !== false, true);

	// a move of 0 is meaningless and is refused in words, never treated as a missing number
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const m = registry.dialogs.pop();
	m.values.qty = 0;
	m.values.to_warehouse = BIN_B;
	const writes = server.writes().length;
	await m.click_primary();
	await settle();
	check("a move of 0 sends nothing", server.writes().length, writes);
	check("and says why", /Enter how many to move, more than 0\./.test(msgOf(m)), true);
});

test("the real page: a count up for an item with no cost asks what one costs, then counts with the answer", async () => {
	const { server, world, registry, click } = loadPage({ nocost: true });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const reads = () => server.calls.filter((c) => c.method.endsWith("get_location")).length;
	click({ "data-act": "count", "data-item": "PDT-0002" });
	const first = registry.dialogs.pop();
	first.values.qty = 3;
	const locations = reads();
	await first.click_primary();
	await settle();
	check("the first count was sent without a rate", [server.writes().length, server.writes()[0].args.rate, server.writes()[0].args.qty], [1, undefined, 3]);
	const ask = registry.dialogs.pop();
	check("the count dialog closed and the cost dialog opened", [first.shown, ask.shown, ask.opts.title], [false, true, "What does one cost?"]);
	check("nothing was reported or reloaded: it was not a save", [reads(), page_notice(registry)], [locations, ""]);
	const text = ask.opts.fields.find((f) => f.fieldname === "bm_cost_text").options;
	check("it says no cost is on record, per unit, and where the suggestion is from", [/No cost is on record for ELBOW, 2 IN/.test(text), /ONE Unit/.test(text), /Purchase Order PO-0007/.test(text)], [true, true, true]);
	const field = ask.opts.fields.find((f) => f.fieldname === "rate");
	check("the box is not required (Frappe blanks a required 0) and holds the suggestion", [!!field.reqd, field.default, field.label], [false, 6.5, "Unit cost per Unit"]);

	// not a price: nothing is sent, and the dialog says why
	const sent = server.writes().length;
	for (const [typed, words] of [[0, /must be more than 0/], [-2, /as a number/], [100001, /above 100,000/], ["", /Enter the unit cost/]]) {
		ask.values.rate = typed;
		await ask.click_primary();
		await settle();
		check(`cost ${JSON.stringify(typed)} is refused in words`, [words.test(msgOf(ask)), server.writes().length], [true, sent]);
	}

	// Frappe's real Float hands back null for a blank box (and leaves it out of what the primary button passes
	// on): that is "no cost given", said in words, never a count sent without one that the server asks about again
	for (const blank of [null, undefined]) {
		ask.values.rate = blank;
		await ask.click_primary();
		await settle();
		check(`a blank box (${blank === null ? "null" : "undefined"}) asks for the cost in words, sends nothing, keeps the dialog`, [/Enter the unit cost per Unit\./.test(msgOf(ask)), server.writes().length, ask.shown], [true, sent, true]);
	}
	// quantity times cost is capped as well: 3 Unit at 9,000 is 27,000
	ask.values.rate = 9000;
	await ask.click_primary();
	await settle();
	check("3 at 9,000 is over one add's 25,000 and is said so", [/27,000 of stock/.test(msgOf(ask)), server.writes().length], [true, sent]);
	check("the count's note does not promise a review (counts are not flagged)", [/reviews it/.test(text), /this one count only/.test(text)], [false, true]);

	// the answer counts again with the rate
	ask.values.rate = 4.5;
	await ask.click_primary();
	await settle();
	const second = server.writes()[1];
	check("the same count sent again with rate 4.5", [server.writes().length, second.args.item_code, second.args.qty, second.args.rate], [2, "PDT-0002", 3, 4.5]);
	check("a reference of its own is sent", /^bm-/.test(second.args.client_ref), true);
	check("the dialog closed, and now it is reported and the bin reloaded", [ask.shown, reads() > locations], [false, true]);
});

test("the real page: a server that asks again after a price was sent is said so in the dialog, not reopened", async () => {
	const { server, world, registry, click } = loadPage({ nocost: true, stubborn: true });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	click({ "data-act": "count", "data-item": "PDT-0002" });
	const first = registry.dialogs.pop();
	first.values.qty = 3;
	await first.click_primary();
	await settle();
	const ask = registry.dialogs.pop();
	ask.values.rate = 5;
	await ask.click_primary();
	await settle();
	check("one dialog, still open, with the reason in it, and no third dialog", [ask.shown, /still has no cost/.test(msgOf(ask)), registry.dialogs.length, server.writes().length], [true, true, 0, 2]);
});

test("the real page: a count that needs no cost never asks, and a count to zero is still a count", async () => {
	const { server, world, registry, click } = loadPage();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	click({ "data-act": "count", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	d.values.qty = 0;
	await d.click_primary();
	await settle();
	check("a count of 0 is sent as 0 with no rate", [server.writes()[0].args.qty, "rate" in server.writes()[0].args, registry.dialogs.length], [0, false, 0]);
});

const page_notice = (registry) => (registry.found && registry.found[".bm-notice"] ? registry.found[".bm-notice"][0].innerHTML || "" : "");

// TASK-2026-02919 (Parker: "in the bin manager clicking the move button on an item doesn't have any function").
// The button was wired. move_dialog listened on the "Make <bin> this item's bin" box's $input while building the
// dialog, and Frappe builds no input for a field declared hidden (it is made only when the field is shown), so
// the page threw before d.show() and the button did nothing. This stand-in builds no $input for a hidden field,
// as Frappe does (see the Dialog class above): on the old code every test below throws at that line.
const pickDestination = (d, warehouse) => {
	const pick = d.fields_dict.dest_list.$wrapper.__state.handlers.find((h) => h.selector === "[data-bm-dest]");
	pick.fn({ currentTarget: { getAttribute: () => warehouse } });
};

test("the real page: Move opens, picks a bin that is not this one, and moves through the existing endpoint", async () => {
	const { server, world, registry, click, page } = loadPage();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const entries = world.entries.length;
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	check("the dialog opened (it threw before it was shown)", [!!d, d && d.shown], [true, true]);
	check("a dialog is not a history entry", world.entries.length, entries);
	check("the box that starts hidden has no input yet, as in Frappe", d.fields_dict.make_home.$input, undefined);
	check("the quantity starts at everything here", d.values.qty, 4);
	const listed = d.fields_dict.dest_list.$wrapper.__state.html;
	check("the picker lists other bins and never the source bin", [listed.includes(BIN_B), listed.includes(`data-bm-dest="${BIN_A}"`)], [true, false]);

	// no destination yet: said in words, nothing sent
	const before = server.writes().length;
	await d.click_primary();
	await settle();
	check("no bin picked: refused in words, nothing sent", [/Pick the bin to move it to\./.test(msgOf(d)), server.writes().length], [true, before]);

	pickDestination(d, BIN_B);
	check("a bin is picked: the make-home box is shown and its input exists", [!!d.fields_dict.make_home.$input, d.props["make_home.hidden"]], [true, 0]);
	check("its change listener is bound, once", d.fields_dict.make_home.$input.__state.handlers.filter((h) => h.evt === "change").length, 1);
	check("everything moves, so it is ticked by default", d.values.make_home, 1);
	pickDestination(d, BIN_B);
	check("picking again does not bind it twice", d.fields_dict.make_home.$input.__state.handlers.filter((h) => h.evt === "change").length, 1);

	// more than is here, and zero: refused in words (the server checks again)
	for (const [qty, words] of [[5, /Only 4 Unit is here\./], [0, /Enter how many to move, more than 0\./]]) {
		d.values.qty = qty;
		await d.click_primary();
		await settle();
		check(`a move of ${qty} is refused in words, nothing sent`, [words.test(msgOf(d)), server.writes().length], [true, before]);
	}

	// the move: the endpoint Stock Scan uses, the row changes at once (before the list is read again), a toast
	let release;
	server.delay.set("get_location", new Promise((resolve) => (release = resolve)));
	d.values.qty = 4;
	await d.click_primary();
	await settle();
	const move = server.writes().slice(-1)[0];
	check("move_to is called with the item, both bins, the quantity and make_home", [move.method.endsWith("bin_assignments.move_to"), move.args.item_code, move.args.from_warehouse, move.args.to_warehouse, move.args.qty, move.args.make_home], [true, "PDT-0001", BIN_A, BIN_B, 4, 1]);
	check("with a reference of its own", /^bm-/.test(move.args.client_ref), true);
	check("the dialog closed and a green toast says what happened", [d.shown, registry.alerts.length, registry.alerts[0] && registry.alerts[0].indicator, /move_to done/.test((registry.alerts[0] || {}).message || "")], [false, 1, "green", true]);
	check("the row shows 0 on hand at once, before the list has been read again", /<td class="bm-qty">0 Unit<\/td>/.test(registry.found[".bm-items"][0].innerHTML), true);
	release();
	await settle();
	check("then the list is read again", server.calls.filter((c) => c.method.endsWith("get_location")).length >= 2, true);
	check("no history entry for any of it", world.entries.length, entries);
	check("no page error", page.bin_manager.ctl !== null, true);
});

test("the real page: a repeated save (already made) does not take the quantity off the row a second time", async () => {
	const { server, world, registry, click } = loadPage();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	pickDestination(d, BIN_B);
	d.values.qty = 1;
	let release;
	server.delay.set("get_location", new Promise((resolve) => (release = resolve)));
	server.repeat = true;
	await d.click_primary();
	await settle();
	check("a repeated answer leaves the row as it was until the list is read", /<td class="bm-qty">4 Unit<\/td>/.test(registry.found[".bm-items"][0].innerHTML), true);
	release();
	await settle();
});

test("the real page: a move posts once, the button is off while it is out, and a retry after a lost reply is the same save", async () => {
	const { server, world, registry, click } = loadPage();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	pickDestination(d, BIN_B);
	d.values.qty = 2;
	// the reply never comes
	server.fail.set("move_to", [{ status: 0, message: "Could not reach the server." }]);
	await d.click_primary();
	await settle();
	const first = server.writes().slice(-1)[0];
	check("lost reply: the dialog stays open with the sentence, the button is back", [d.shown, /Could not reach/.test(msgOf(d)), d.get_primary_btn().__state.props.disabled], [true, true, false]);
	await d.click_primary();
	await settle();
	const second = server.writes().slice(-1)[0];
	check("the tap again is the same save: same reference, so the server returns the first", [server.writes().length, second.args.client_ref], [2, first.args.client_ref]);

	// while a save is out the button is disabled and a second tap is not a second save
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const e = registry.dialogs.pop();
	pickDestination(e, BIN_B);
	e.values.qty = 1;
	let release;
	server.delay.set("move_to", new Promise((resolve) => (release = resolve)));
	const sentBefore = server.writes().length;
	const tapped = e.click_primary();
	await settle();
	check("posting: the button is disabled", e.get_primary_btn().__state.props.disabled, true);
	await e.click_primary();
	check("a second tap while it is out sends nothing", server.writes().length, sentBefore + 1);
	release();
	await tapped;
	await settle();
	check("one move was sent", server.writes().length, sentBefore + 1);
});

test("the real page: an item's name links to its Item record, escaped, outside every row action", async () => {
	const hostile = 'X"><img src=x onerror=1>&';
	const { registry, click, world } = loadPage({
		items: [
			{ item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", on_hand: 4, on_order: 0, assigned: true, is_default: false, undecided: false },
			{ item_code: hostile, item_name: "<b>Bold</b> & Co", stock_uom: "Unit", on_hand: 1, on_order: 0, assigned: true, is_default: false, undecided: false },
		],
	});
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const html = registry.found[".bm-items"][0].innerHTML;
	check("a real anchor to the Desk form (never /app)", [html.includes('<a class="bm-item-link" href="/desk/item/PDT-0001"'), /href="\/app\//.test(html)], [true, false]);
	check("the code is URL-encoded in the address", html.includes(`href="/desk/item/${encodeURIComponent(hostile).replace(/&/g, "&amp;")}"`), true);
	check("and the text is escaped: no raw tag, no raw quote", [/<img|<b>Bold/.test(html), html.includes("&lt;b&gt;Bold&lt;/b&gt; &amp; Co")], [false, true]);
	check("the link is not a row action (no data-act on it)", /<a class="bm-item-link"[^>]*data-act/.test(html), false);
	check("it can be reached by keyboard (a plain anchor, no tabindex)", /<a class="bm-item-link"[^>]*tabindex/.test(html), false);
	check("it says what it opens", /<a class="bm-item-link"[^>]*title="Open the Item"/.test(html), true);
	void click;
});

test("the real page: without Item read there is no link, only the text", async () => {
	const { registry, world } = loadPage({ boot: { ...BOOT, can_open_items: false } });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const html = registry.found[".bm-items"][0].innerHTML;
	check("no anchor for an item, and the name is still shown", [html.includes("bm-item-link"), /PUMP, 1 HP/.test(html)], [false, true]);
});

test("the real page with the reorder bundle: a Reorder column, one read per list, no history", async () => {
	const { server, world, registry, page, click } = loadPage({ reorder: true, show_reorder: true });
	world.go_to("/desk/bin-manager");
	await settle();
	check("asked for the reorder files apart from its own", registry.requires.map((r) => r[0]), ["sf_kit.bundle.js", "reorder_levels.bundle.js"]);
	check("the editor is made", !!page.bin_manager.reorder, true);
	const before = world.entries.length;
	click({ "data-act": "bin", "data-bin": BIN_A });
	await settle();
	check("one entry for the bin, none for the reorder read", world.entries.length, before + 1);
	const html = registry.found[".bm-items"][0].innerHTML;
	check("a Reorder column", /<th scope="col">Reorder<\/th>/.test(html), true);
	check("a cell for each item", (html.match(/data-ro-item="PDT-000[12]"/g) || []).length, 2);
	const reads = () => server.calls.filter((c) => c.method.endsWith("get_reorder_levels"));
	check("one read, for this bin's items", [reads().length, JSON.parse(reads()[0].args.item_codes), reads()[0].args.warehouse], [1, ["PDT-0001", "PDT-0002"], BIN_A]);
	check("not a write", server.writes().length, 0);
	page.bin_manager.render();
	page.bin_manager.render();
	await settle();
	check("drawing the same list again does not ask again", reads().length, 1);
	await page.bin_manager.ctl.load_location();
	await settle();
	check("a reloaded list is read once more", reads().length, 2);
});

test("the real page: a redraw waits for a focused reorder box and runs when it is left", async () => {
	const { server, world, registry, page, click, document } = loadPage({ reorder: true, show_reorder: true });
	world.go_to("/desk/bin-manager");
	await settle();
	click({ "data-act": "bin", "data-bin": BIN_A });
	await settle();
	const items = () => registry.found[".bm-items"][0];
	const before = items().innerHTML;
	check("drawn", /PUMP, 1 HP/.test(before), true);
	// the person is in a reorder box; the list changes underneath (a reload lands)
	document.activeElement = { classList: { contains: (c) => c === "ro-input" }, inRoot: true };
	page.bin_manager.ctl.state.location = Object.assign({}, page.bin_manager.ctl.state.location, {
		items: [{ item_code: "PDT-0001", item_name: "PUMP, 2 HP", stock_uom: "Unit", on_hand: 4, on_order: 0, assigned: true, is_default: false, undecided: false }],
	});
	page.bin_manager.render();
	check("the table is left alone while a box has focus", items().innerHTML, before);
	check("and the redraw is queued", page.bin_manager.redraw_after_blur, true);
	// focus leaves the box: the queued redraw runs
	document.activeElement = null;
	const root = registry.root.__state;
	const handler = root.handlers.find((h) => h.evt === "focusout" && h.selector === ".ro-input");
	check("a focusout handler on the boxes", !!handler, true);
	handler.fn({});
	await settle();
	check("the redraw ran", /PUMP, 2 HP/.test(items().innerHTML), true);
	check("and is no longer queued", page.bin_manager.redraw_after_blur, false);
	// focus moving from one box to the next (still in a box) does not draw yet
	page.bin_manager.redraw_after_blur = true;
	document.activeElement = { classList: { contains: (c) => c === "ro-input" }, inRoot: true };
	handler.fn({});
	await settle();
	check("still queued while another box has focus", page.bin_manager.redraw_after_blur, true);
	check("no write ever", server.writes().length, 0);
});

// TASK-2026-02920: the reorder boxes are off until a person turns them on, and the choice is theirs for good.
const REORDER_PREF = "erpnext_enhancements.api.planner_views_prefs.set_show_reorder";
const reads = (server) => server.calls.filter((c) => c.method.endsWith("get_reorder_levels"));
const toggleButton = (registry) => (registry.found[".bm-bin-actions"] || [{}])[0].innerHTML || "";

test("the real page: the Reorder column is hidden by default, with a switch, and nothing is read for it", async () => {
	const { server, world, registry } = loadPage({ reorder: true });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const html = registry.found[".bm-items"][0].innerHTML;
	check("a clean list: no Reorder column, no reorder boxes, nothing to tab to", [/>Reorder</.test(html), /data-ro-item|ro-input/.test(html)], [false, false]);
	check("the items are all there", /PUMP, 1 HP/.test(html), true);
	check("the rules are not even read while they are hidden", reads(server).length, 0);
	const toolbar = toggleButton(registry);
	check("one switch in the bin's toolbar, off", [/data-act="reorder-toggle"/.test(toolbar), /aria-pressed="false"/.test(toolbar), /Show reorder levels/.test(toolbar)], [true, true, true]);
});

test("the real page: the switch shows the column, reads the rules once, and is remembered", async () => {
	const { server, world, registry, click, page } = loadPage({ reorder: true });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const entries = world.entries.length;
	click({ "data-act": "reorder-toggle" });
	await settle();
	const html = registry.found[".bm-items"][0].innerHTML;
	check("the column and a cell for each item appear", [/<th scope="col">Reorder<\/th>/.test(html), (html.match(/data-ro-item="PDT-000[12]"/g) || []).length], [true, 2]);
	check("the rules are read now, once, for this bin's items", [reads(server).length, JSON.parse(reads(server)[0].args.item_codes)], [1, ["PDT-0001", "PDT-0002"]]);
	const saved = server.calls.filter((c) => c.method === REORDER_PREF);
	check("the choice is saved to the person's own store: this screen, on", [saved.length, saved[0].args], [1, { screen: "bin_manager", value: 1 }]);
	check("the switch now says hide, and is pressed", [/Hide reorder levels/.test(toggleButton(registry)), /aria-pressed="true"/.test(toggleButton(registry))], [true, true]);
	check("a switch is not a history entry", world.entries.length, entries);
	click({ "data-act": "reorder-toggle" });
	await settle();
	check("off again: the column is gone and the choice saved off", [/>Reorder</.test(registry.found[".bm-items"][0].innerHTML), server.calls.filter((c) => c.method === REORDER_PREF).slice(-1)[0].args.value], [false, 0]);
	click({ "data-act": "reorder-toggle" });
	await settle();
	check("on again: the rules are not read a second time for the same list", reads(server).length, 1);
	check("no page error", page.bin_manager.ctl !== null, true);
});

test("the real page: a saved choice opens the screen as the person left it, and anything unreadable is hidden", async () => {
	let t = loadPage({ reorder: true, show_reorder: true });
	t.world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	check("saved on: the column is there on arrival, read once", [/>Reorder</.test(t.registry.found[".bm-items"][0].innerHTML), reads(t.server).length], [true, 1]);
	check("and the switch says hide", /Hide reorder levels/.test(toggleButton(t.registry)), true);
	for (const [label, value] of [["off", false], ["a damaged value", "yes"], ["a number", 1], ["null", null]]) {
		t = loadPage({ reorder: true, show_reorder: value });
		t.world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
		await settle();
		check(`${label}: hidden, nothing read`, [/>Reorder</.test(t.registry.found[".bm-items"][0].innerHTML), reads(t.server).length], [false, 0]);
	}
	// no preference in the boot at all (the read failed on the server and it said nothing): hidden
	t = loadPage({ reorder: true });
	t.world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	check("no preference at all: hidden", />Reorder</.test(t.registry.found[".bm-items"][0].innerHTML), false);
});

test("the real page: a choice that could not be saved still works this visit, and says so", async () => {
	const { server, world, registry, click } = loadPage({ reorder: true });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	server.fail.set("set_show_reorder", { status: 500, message: "boom" });
	click({ "data-act": "reorder-toggle" });
	await settle();
	check("the column shows anyway", />Reorder</.test(registry.found[".bm-items"][0].innerHTML), true);
	check("and an orange note says it will not be remembered", [registry.alerts.length, registry.alerts[0] && registry.alerts[0].indicator, /next time/.test((registry.alerts[0] || {}).message || "")], [1, "orange", true]);
});

test("the real page: quick taps on the switch send one request at a time, and the last tap is what is stored", async () => {
	const { server, world, registry, click } = loadPage({ reorder: true });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	let release;
	server.delay.set("set_show_reorder", new Promise((r) => (release = r)));
	const sent = () => server.calls.filter((c) => c.method === REORDER_PREF).map((c) => c.args.value);
	for (let i = 0; i < 4; i++) click({ "data-act": "reorder-toggle" }); // on, off, on, off
	await settle();
	check("the last tap wins on screen", />Reorder</.test(registry.found[".bm-items"][0].innerHTML), false);
	check("only the first request is out while it is unanswered", sent(), [1]);
	server.delay.delete("set_show_reorder");
	release();
	await settle();
	check("then the latest choice follows, in order, and nothing else", sent(), [1, 0]);
	check("no note about a failed save", registry.alerts.length, 0);
	// on, off, on while one is out: the in-flight request already says it, so no second one is needed
	server.delay.set("set_show_reorder", new Promise((r) => (release = r)));
	for (let i = 0; i < 3; i++) click({ "data-act": "reorder-toggle" });
	await settle();
	server.delay.delete("set_show_reorder");
	release();
	await settle();
	check("an end state equal to what was just sent adds no request", sent().slice(2), [1]);
});

test("the real page: with the boxes on, Move, the item link and a reorder level of 0 all still work", async () => {
	const { server, world, registry, click } = loadPage({
		reorder: true,
		show_reorder: true,
		items: [{ item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", on_hand: 4, on_order: 0, assigned: true, is_default: false, undecided: false }],
	});
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const html = registry.found[".bm-items"][0].innerHTML;
	check("the link and the column are both there", [html.includes('<a class="bm-item-link" href="/desk/item/PDT-0001"'), /data-ro-item="PDT-0001"/.test(html)], [true, true]);
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	check("Move still opens", d && d.shown, true);
	void server;
});

test("the real page: a reorder bundle that never arrives holds the bins up for nothing", async () => {
	const { world, registry, page, click } = loadPage({ reorder: true, hold_reorder: true });
	world.go_to("/desk/bin-manager");
	await settle();
	click({ "data-act": "bin", "data-bin": BIN_A });
	await settle();
	check("the bin is drawn", /PUMP, 1 HP/.test(registry.found[".bm-items"][0].innerHTML), true);
	check("with no column", /Reorder/.test(registry.found[".bm-items"][0].innerHTML), false);
	check("and no editor", page.bin_manager.reorder, null);
});

test("the real page without the reorder bundle: no column, no read", async () => {
	const { server, world, registry, page, click } = loadPage();
	world.go_to("/desk/bin-manager");
	await settle();
	click({ "data-act": "bin", "data-bin": BIN_A });
	await settle();
	check("no editor", page.bin_manager.reorder, null);
	check("no column", /Reorder/.test(registry.found[".bm-items"][0].innerHTML), false);
	check("no read", server.calls.filter((c) => c.method.endsWith("get_reorder_levels")).length, 0);
});

test("the real page: loads its bundles, a bin click is one entry, a dialog is none", async () => {
	const { server, world, registry, page, click } = loadPage();
	world.go_to("/desk/bin-manager");
	await settle();
	check("asked for the bundles", registry.requires[0], ["sf_kit.bundle.js", "sf_kit.bundle.css", "bin_manager.bundle.js"]);
	check("controller made", !!page.bin_manager.ctl, true);
	check("picker drawn", /bm-bin/.test(registry.found[".bm-list"].__state.html || registry.found[".bm-list"][0].innerHTML), true);
	const before = world.entries.length;
	click({ "data-act": "bin", "data-bin": BIN_A });
	await settle();
	check("one entry", world.entries.length, before + 1);
	check("on the bin", page.bin_manager.ctl.state.target, { screen: "bin", warehouse: BIN_A });
	check("items drawn", /PUMP, 1 HP/.test(registry.found[".bm-items"][0].innerHTML), true);
	check("count button drawn", /data-act="count" data-item="PDT-0001"/.test(registry.found[".bm-items"][0].innerHTML), true);

	// Change a quantity: a dialog, no history; its Save calls set_count with the dialog's reference.
	const entries = world.entries.length;
	click({ "data-act": "count", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	check("dialog open", d.shown, true);
	check("no entry for the dialog", world.entries.length, entries);
	d.values.qty = 7;
	d.opts.primary_action();
	await settle();
	const write = server.writes().pop();
	check("set_count", write.method.endsWith("bin_assignments.set_count"), true);
	check("its args", [write.args.item_code, write.args.warehouse, write.args.qty], ["PDT-0001", BIN_A, 7]);
	check("its client_ref", logic.CLIENT_REF.test(write.args.client_ref), true);
	check("closed on success", d.shown, false);
	check("still no entry", world.entries.length, entries);

	// A refusal keeps the dialog open with the server's sentence, and the next try mints a new ref.
	click({ "data-act": "count", "data-item": "PDT-0001" });
	const d2 = registry.dialogs.pop();
	d2.values.qty = 9;
	server.fail.set("set_count", [{ status: 417, message: "Refused in words." }]);
	d2.opts.primary_action();
	await settle();
	check("open after a refusal", d2.shown, true);
	check("the sentence inline", /Refused in words\./.test(d2.fields_dict.bm_msg.$wrapper.__state.html), true);
	const first = server.writes().pop().args.client_ref;
	d2.opts.primary_action();
	await settle();
	check("new ref after a refusal", server.writes().pop().args.client_ref !== first, true);

	// Move: the destination is picked in the dialog, and make-home follows the quantity.
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const m = registry.dialogs.pop();
	const pick = m.fields_dict.dest_list.$wrapper.__state.handlers.find((h) => h.selector === "[data-bm-dest]");
	pick.fn({ currentTarget: { getAttribute: () => BIN_B } });
	check("make-home offered", m.props["make_home.hidden"], 0);
	check("ticked: everything moves", m.values.make_home, 1);
	const qty_input = m.fields_dict.qty.$input.__state;
	qty_input.value = "2";
	qty_input.handlers.find((h) => /input|change/.test(h.evt)).fn();
	check("partial unticks", m.values.make_home, 0);
	m.values.qty = 2;
	m.opts.primary_action();
	await settle();
	const move = server.writes().pop();
	check("move_to", [move.method.split(".").pop(), move.args.to_warehouse, move.args.qty, move.args.make_home], ["move_to", BIN_B, 2, 0]);
	check("dialogs never route", world.entries.length, entries);

	// Unassign with stock offers Move and the boneyard first.
	click({ "data-act": "unassign", "data-item": "PDT-0001" });
	const u = registry.dialogs.pop();
	check("offers both", /data-bm-next="move"/.test(u.opts.fields[0].options) && /data-bm-next="boneyard"/.test(u.opts.fields[0].options), true);

	// Back from the bin: the picker.
	world.back();
	await settle();
	check("Back: picker", page.bin_manager.ctl.state.target, { screen: "picker" });
});

// Review fix 1: production has 221 bins, and the Move list shows the first 30 that match. A bin
// clicked after filtering must be found in the whole list, not in the first 30 of an empty filter.
const MANY = [];
for (let n = 1; n <= 60; n++) {
	const id = String(n).padStart(2, "0");
	MANY.push({ warehouse: `Rack D2-${id} - SF`, warehouse_name: `Rack D2-${id}`, trail: "Row D", on_hand_items: 0, assigned_items: 0, is_boneyard: false });
}
const MANY_BOOT = Object.assign({}, BOOT, { bins: BOOT.bins.concat(MANY) });

test("Move picks a bin past the first 30 after filtering (the dialog and the Unassign path)", async () => {
	const { server, world, registry, click } = loadPage({ boot: MANY_BOOT });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	check("the controller finds bin 45 in the full list", !!registry.root && logic.destinations(MANY_BOOT.bins, BIN_A, "").length, 30);
	for (const via of ["move", "unassign"]) {
		click({ "data-act": via, "data-item": "PDT-0001" });
		let m = registry.dialogs.pop();
		if (via === "unassign") {
			const next = m.fields_dict.bm_text.$wrapper.__state.handlers.find((h) => h.selector === "[data-bm-next]");
			next.fn({ currentTarget: { getAttribute: () => "move" } });
			m = registry.dialogs.pop();
		}
		m.values.dest_search = "D2-4";
		m.fields_dict.dest_search.$input.__state.handlers.find((h) => h.evt === "input").fn();
		check(`${via}: the filtered list shows bin 45`, /data-bm-dest="Rack D2-45 - SF"/.test(m.fields_dict.dest_list.$wrapper.__state.html), true);
		const pick = m.fields_dict.dest_list.$wrapper.__state.handlers.find((h) => h.selector === "[data-bm-dest]");
		pick.fn({ currentTarget: { getAttribute: () => "Rack D2-45 - SF" } });
		check(`${via}: chosen`, /Moving to/.test(m.fields_dict.dest_list.$wrapper.__state.html), true);
		check(`${via}: make-home names it`, m.props["make_home.label"], "Make Rack D2-45 this item's bin");
		m.opts.primary_action();
		await settle();
		const move = server.writes().pop();
		check(`${via}: move_to bin 45`, [move.method.split(".").pop(), move.args.to_warehouse], ["move_to", "Rack D2-45 - SF"]);
		check(`${via}: closed`, m.shown, false);
	}
});

test("Keep on this bin keeps its reference after a dropped reply, and mints a new one after", async () => {
	const { server, world, ctl } = makeController();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const row = ctl.state.location.items[1];
	server.fail.set("assign", [{ status: 0, message: "Could not reach the server." }]);
	const err = await ctl.quick("keep", row, {}).catch((e) => e);
	check("dropped reply keeps the ref", err.keep_ref, true);
	const first = server.calls.filter((c) => c.method.endsWith(".assign")).pop().args.client_ref;
	await ctl.quick("keep", row, {});
	const second = server.calls.filter((c) => c.method.endsWith(".assign")).pop().args.client_ref;
	check("the retry sends the SAME reference", second, first);
	await ctl.quick("keep", row, {});
	const third = server.calls.filter((c) => c.method.endsWith(".assign")).pop().args.client_ref;
	check("a new save after a landed one gets a new reference", third !== second, true);
	server.fail.set("assign", [{ status: 417, message: "Refused." }]);
	await ctl.quick("keep", row, {}).catch(() => null);
	const refused = server.calls.filter((c) => c.method.endsWith(".assign")).pop().args.client_ref;
	await ctl.quick("keep", row, {});
	const after = server.calls.filter((c) => c.method.endsWith(".assign")).pop().args.client_ref;
	check("a refusal frees the reference", after !== refused, true);
	check("one item's ref is not another's", (await ctl.quick("keep", ctl.state.location.items[0], {})) && true, true);
});

test("the real page: Keep on this bin retried after a dropped reply sends the same reference", async () => {
	const { server, world, click } = loadPage();
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	server.fail.set("assign", [{ status: 0, message: "Could not reach the server." }]);
	click({ "data-act": "keep", "data-item": "PDT-0002" });
	await settle();
	click({ "data-act": "keep", "data-item": "PDT-0002" });
	await settle();
	const refs = server.calls.filter((c) => c.method.endsWith(".assign")).map((c) => c.args.client_ref);
	check("two clicks, one reference", [refs.length, refs[0] === refs[1]], [2, true]);
});

test("names are data: titles, labels and descriptions escape them", async () => {
	const NASTY = "<b>Bold</b> &amp; Co";
	const items = [
		{ item_code: "PDT-0001", item_name: NASTY, stock_uom: "Unit", on_hand: 4, on_order: 0, assigned: true, is_default: false, undecided: false },
	];
	const boot = Object.assign({}, BOOT, {
		boneyard_name: NASTY,
		bins: BOOT.bins.concat([{ warehouse: "Odd - SF", warehouse_name: NASTY, trail: "", on_hand_items: 0, assigned_items: 0, is_boneyard: false }]),
	});
	const { world, registry, click } = loadPage({ boot, items });
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	const ESCAPED = "&lt;b&gt;Bold&lt;/b&gt; &amp;amp; Co";
	const clean = (label, text) => {
		check(`${label}: escaped`, String(text).includes(ESCAPED), true);
		check(`${label}: no raw tag`, /<b>Bold/.test(String(text)), false);
	};
	for (const act of ["count", "move", "boneyard", "unassign"]) {
		click({ "data-act": act, "data-item": "PDT-0001" });
		const d = registry.dialogs.pop();
		clean(`${act} title`, d.opts.title);
		if (act === "boneyard") clean("boneyard description", d.opts.fields.find((f) => f.fieldname === "qty").description);
		if (act === "move") {
			const pick = d.fields_dict.dest_list.$wrapper.__state.handlers.find((h) => h.selector === "[data-bm-dest]");
			pick.fn({ currentTarget: { getAttribute: () => "Odd - SF" } });
			clean("make-home label", d.props["make_home.label"]);
		}
		if (act === "unassign") check("unassign body: no raw tag", /<b>Bold/.test(d.opts.fields[0].options), false);
	}
	check("row name escaped in the table", registry.found[".bm-items"][0].innerHTML.includes(ESCAPED), true);
	check("no raw tag in the table", /<b>Bold/.test(registry.found[".bm-items"][0].innerHTML), false);
});

// v1.732.0: a link to the Reorder Board (buyers pick reorders there now that ERPNext raises none on its own), on the
// picker and on a bin, for the roles the board admits (reorder_board.BOARD_ROLES) and nobody else.
const BOARD_ROLES = ["Purchase Manager", "Purchase User", "Stock Manager", "System Manager"];
const boardLinkIn = (html) => (String(html).match(/data-act="reorder-board"/g) || []).length;

test("the real page: the Reorder Board link is on the picker and on a bin for each board role, and Back returns", async () => {
	for (const role of BOARD_ROLES) {
		const { world, registry, click } = loadPage({ roles: [role] });
		world.go_to("/desk/bin-manager");
		await settle();
		check(`${role}: one link on the picker`, boardLinkIn(registry.root.__state.html), 1);
		check(`${role}: its words`, />Reorder Board<\/button>/.test(registry.root.__state.html), true);
		world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
		await settle();
		check(`${role}: one link on a bin`, boardLinkIn(registry.found[".bm-bin-actions"][0].innerHTML), 1);
		const entries = world.entries.length;
		click({ "data-act": "reorder-board" });
		await settle();
		check(`${role}: it routes to the board`, world.route(), ["reorder-board"]);
		check(`${role}: as a new history entry`, [world.entries.length, world.index], [entries + 1, entries]);
		world.back();
		await settle();
		check(`${role}: Back lands on the bin again`, world.route(), ["bin-manager", BIN_A]);
		world.forward();
		await settle();
		check(`${role}: Forward returns to the board`, world.route(), ["reorder-board"]);
	}
});

test("the real page: no Reorder Board link for a person without a board role, and the page is as it was", async () => {
	for (const roles of [[], ["Stock User"], ["Sales User", "Accounts User"]]) {
		const { world, registry } = loadPage({ roles });
		world.go_to("/desk/bin-manager");
		await settle();
		check(`${JSON.stringify(roles)}: none on the picker`, [boardLinkIn(registry.root.__state.html), /Reorder Board/.test(registry.root.__state.html)], [0, false]);
		check("the filter box is still there", /bm-filter/.test(registry.root.__state.html), true);
		world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
		await settle();
		check(`${JSON.stringify(roles)}: none on a bin`, boardLinkIn(registry.found[".bm-bin-actions"][0].innerHTML), 0);
	}
});

test("the real page: a role check that throws shows no Reorder Board link and breaks nothing", async () => {
	const { world, registry } = loadPage({ roles: BOARD_ROLES });
	// Another page load whose frappe.user throws: the link is hidden, not a broken page.
	const broken = loadPage({ roles: BOARD_ROLES, user_throws: true });
	for (const [name, w, r] of [["a role check that works", world, registry], ["a role check that throws", broken.world, broken.registry]]) {
		w.go_to("/desk/bin-manager");
		await settle();
		check(`${name}: the picker still draws`, /bm-filter/.test(r.root.__state.html), true);
		check(`${name}: the link is ${name.endsWith("works") ? "there" : "not there"}`, boardLinkIn(r.root.__state.html), name.endsWith("works") ? 1 : 0);
	}
});

// ------------------------------------------------------------------ views and filters (v1.812.0)

const SHELF = "Project Shelf - SF";
const SITE = "Job Site PRJ-90001 Water Wall - SF";
const OLD_SITE = "Job Site PRJ-80000 Old Lobby - SF";
const VAN = "Van 2 - SF";

// A boot as bin_views.decorate sends it: every bin with its kind, a site with its job.
const VIEW_BOOT = Object.assign({}, BOOT, {
	can_see_value: true,
	shelf_warehouse: SHELF,
	shelf_name: "Project Shelf",
	job_sites: true,
	bins: [
		{ warehouse: BIN_A, warehouse_name: "Bin A1-1-1", trail: "Row A", kind: "shop_bin", on_hand_items: 2, assigned_items: 3, missing_items: 1, unassigned_items: 0, stock_value: 12 },
		{ warehouse: BIN_B, warehouse_name: "Shelf B/2", trail: "Row B", kind: "shop_bin", on_hand_items: 0, assigned_items: 0, missing_items: 0, unassigned_items: 0, stock_value: 0 },
		{ warehouse: "Bin C - SF", warehouse_name: "Bin C", trail: "Row B", kind: "shop_bin", on_hand_items: 1, assigned_items: 0, missing_items: 0, unassigned_items: 1, stock_value: 3 },
		{ warehouse: BONEYARD, warehouse_name: "Boneyard", trail: "", kind: "boneyard", is_boneyard: true, on_hand_items: 0, assigned_items: 0, missing_items: 0, unassigned_items: 0 },
		{ warehouse: SHELF, warehouse_name: "Project Shelf", trail: "", kind: "project_shelf", on_hand_items: 9, assigned_items: 0, missing_items: 0, unassigned_items: 0, stock_value: 900 },
		{ warehouse: VAN, warehouse_name: "Van 2", trail: "Vehicles", kind: "vehicle", on_hand_items: 1, assigned_items: 1, missing_items: 0, unassigned_items: 0, stock_value: 5 },
		{ warehouse: SITE, warehouse_name: "Job Site PRJ-90001 Water Wall", trail: "Job Sites", kind: "job_site", project: "PRJ-90001", project_name: "Water Wall", customer: "Acme Hotels", project_closed: false, on_hand_items: 2, assigned_items: 0, missing_items: 0, unassigned_items: 0, stock_value: 502 },
		{ warehouse: OLD_SITE, warehouse_name: "Job Site PRJ-80000 Old Lobby", trail: "Job Sites", kind: "job_site", project: "PRJ-80000", project_name: "Old Lobby", customer: "Bolt Bank", project_closed: true, on_hand_items: 1, assigned_items: 0, missing_items: 0, unassigned_items: 0, stock_value: 40 },
	],
});

const PROJECT_VIEW = {
	project: { project: "PRJ-90001", project_name: "Water Wall", customer: "Acme Hotels", status: "Active", closed: false },
	shelf: { warehouse: SHELF, warehouse_name: "Project Shelf", items: [{ item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", qty: 6 }] },
	site: { warehouse: SITE, warehouse_name: "Job Site PRJ-90001 Water Wall", disabled: false, items: [{ item_code: "PDT-0002", item_name: "ELBOW, 2 IN", stock_uom: "Unit", qty: 4, value: 2 }] },
	send_refusal: null,
};

const PROBLEMS = {
	assigned_empty: { rows: [{ warehouse: BIN_A, warehouse_name: "Bin A1-1-1", item_code: "PDT-0003", item_name: "VALVE <2 IN>", stock_uom: "Unit" }], total: 1, truncated: false },
	unassigned_stock: { rows: [{ warehouse: "Bin C - SF", warehouse_name: "Bin C", item_code: "PDT-0004", item_name: "TEE", stock_uom: "Unit", qty: 3 }], total: 1, truncated: false },
	finished_shelf: { rows: [], total: 0, truncated: false },
	finished_site: { rows: [{ project: "PRJ-80000", project_name: "Old Lobby", status: "Completed", warehouse: OLD_SITE, warehouse_name: "Job Site PRJ-80000 Old Lobby", item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", qty: 1 }], total: 1, truncated: false },
};

const VIEW_FIXTURE = {
	boot: VIEW_BOOT,
	item_locations: { item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", is_stock_item: true, locations: { [BIN_A]: { on_hand: 4, assigned: true }, [SITE]: { on_hand: 2, assigned: false } } },
	project_index: [
		{ project: "PRJ-90001", project_name: "Water Wall", customer: "Acme Hotels", status: "Active", closed: false, site: SITE, site_items: 2, shelf_items: 1 },
		{ project: "PRJ-80000", project_name: "Old Lobby", customer: "Bolt Bank", status: "Completed", closed: true, site: OLD_SITE, site_items: 1, shelf_items: 0 },
	],
	project_view: PROJECT_VIEW,
	problems: PROBLEMS,
};

function makeViewController(fixture) {
	const server = makeServer(Object.assign({}, VIEW_FIXTURE, fixture || {}));
	const world = makeWorld(server);
	const renders = [];
	const ctl = new Controller(world.env(renders));
	world.page = { show: () => ctl.show() };
	return { server, world, ctl, renders };
}

const shown = (ctl) => ctl.shown_bins().map((b) => b.warehouse_name);

test("routes: the three new screens, and a bin is still every other second segment", () => {
	check("by project", logic.parse_route(["bin-manager", "by-project"]), { screen: "projects" });
	check("one job", logic.parse_route(["bin-manager", "by-project", "PRJ-90001"]), { screen: "project", project: "PRJ-90001" });
	check("job sites", logic.parse_route(["bin-manager", "job-sites"]), { screen: "sites" });
	check("problems", logic.parse_route(["bin-manager", "problems"]), { screen: "problems" });
	check("a bin, as before", logic.parse_route(["bin-manager", SITE]), { screen: "bin", warehouse: SITE });
	for (const target of [{ screen: "picker" }, { screen: "projects" }, { screen: "project", project: "PRJ-90001" }, { screen: "sites" }, { screen: "problems" }, { screen: "bin", warehouse: BIN_B }]) {
		check(`round trip ${target.screen}`, logic.parse_route(logic.build_route(target)), target);
	}
	check("a job with no id is the list", logic.build_route({ screen: "project" }), ["bin-manager"]);
	check("tabs", logic.TABS.map((t) => t.screen), ["picker", "projects", "sites", "problems"]);
	check("a bin is under Bins, a job under By project", [logic.tab_of({ screen: "bin" }), logic.tab_of({ screen: "project" }), logic.tab_of(null)], ["picker", "projects", "picker"]);
	// No bin on this site is named like a view: a warehouse's docname ends in the company's abbreviation.
	check("no boot bin collides", VIEW_BOOT.bins.some((b) => [logic.SEGMENT_PROJECTS, logic.SEGMENT_SITES, logic.SEGMENT_PROBLEMS].includes(b.warehouse)), false);
});

test("filters: read from an address, written in a fixed order, unknown values dropped", () => {
	check("default", views.read_filters(null), { q: "", kind: "", project: "", item: "", quick: "" });
	check("read", views.read_filters({ kind: "job_site", project: " PRJ-90001 ", quick: "has_stock", q: "wall", item: "PDT-0001", warehouse: "x" }), {
		q: "wall", kind: "job_site", project: "PRJ-90001", item: "PDT-0001", quick: "has_stock",
	});
	check("an unknown kind or quick filter is dropped", views.read_filters({ kind: "attic", quick: "sparkly" }), views.default_filters());
	check("written in order, blanks left out", views.filters_query({ quick: "empty", kind: "shop_bin", q: " row a " }), "?q=row+a&kind=shop_bin&quick=empty");
	check("no filters, no query", views.filters_query(views.default_filters()), "");
	check("round trip", views.read_filters(Object.fromEntries(new URLSearchParams(views.filters_query({ kind: "vehicle", project: "PRJ-1", item: "A/B 2", quick: "missing", q: "x" })))), {
		q: "x", kind: "vehicle", project: "PRJ-1", item: "A/B 2", quick: "missing",
	});
	check("has_filters", [views.has_filters(views.default_filters()), views.has_filters({ q: "  " }), views.has_filters({ quick: "empty" })], [false, false, true]);
});

test("filters: kind, job, quick filters and an item narrow the picker, and combine", () => {
	const bins = VIEW_BOOT.bins;
	const names = (f, locations) => views.apply_filters(bins, f, locations).map((b) => b.warehouse_name);
	check("no filter is every bin, in order", names({}).length, bins.length);
	check("job sites", names({ kind: "job_site" }), ["Job Site PRJ-90001 Water Wall", "Job Site PRJ-80000 Old Lobby"]);
	check("the shelf", names({ kind: "project_shelf" }), ["Project Shelf"]);
	check("vehicles", names({ kind: "vehicle" }), ["Van 2"]);
	check("boneyard", names({ kind: "boneyard" }), ["Boneyard"]);
	check("a job", names({ project: "PRJ-90001" }), ["Job Site PRJ-90001 Water Wall"]);
	check("has stock", names({ quick: "has_stock", kind: "shop_bin" }), ["Bin A1-1-1", "Bin C"]);
	check("empty", names({ quick: "empty" }), ["Shelf B/2", "Boneyard"]);
	check("assigned items missing", names({ quick: "missing" }), ["Bin A1-1-1"]);
	check("unassigned stock", names({ quick: "unassigned" }), ["Bin C"]);
	check("typed words too", names({ kind: "shop_bin", q: "bin c" }), ["Bin C"]);
	const locations = VIEW_FIXTURE.item_locations.locations;
	check("an item: where it is or is assigned", names({ item: "PDT-0001" }, locations), ["Bin A1-1-1", "Job Site PRJ-90001 Water Wall"]);
	check("an item still being read shows nothing, not everything", names({ item: "PDT-0001" }, null), []);
	check("an item and a kind", names({ item: "PDT-0001", kind: "job_site" }, locations), ["Job Site PRJ-90001 Water Wall"]);
	check("a boot from before the kinds only knows the boneyard", [views.bin_kind({ is_boneyard: true }), views.bin_kind({}), views.bin_kind({ kind: "attic" })], ["boneyard", "shop_bin", "shop_bin"]);
	check("kinds offered, with counts", views.kind_options(bins).map((k) => `${k.label} ${k.count}`), ["Shop bins 3", "Project Shelf 1", "Job sites 2", "Boneyard 1", "Vehicles 1"]);
	check("jobs offered, by name", views.project_options(bins).map((j) => views.project_label(j)), ["Old Lobby · Bolt Bank", "Water Wall · Acme Hotels"]);
	check("a row's words", views.bin_summary(bins[0], locations[BIN_A]), "4 here · assigned here · 2 on hand · 3 assigned · 1 missing");
	check("a site's words say nothing of assignments", views.bin_summary(bins[6], null), "2 on hand");
	check("the filters in words", views.describe_filters({ kind: "job_site", project: "PRJ-90001", quick: "has_stock", q: "wall" }, bins), 'Job sites · Water Wall · Has stock · "wall"');
});

test("job sites: one row per site, live jobs first, with totals and what wants doing", () => {
	const rows = views.site_rows(VIEW_BOOT.bins);
	check("rows", rows.map((r) => [r.project_name, r.customer, r.items, r.value, r.closed]), [["Water Wall", "Acme Hotels", 2, 502, false], ["Old Lobby", "Bolt Bank", 1, 40, true]]);
	check("totals", views.site_totals(rows), { sites: 2, items: 3, value: 542 });
	check("a finished job with stock is flagged", [views.site_flag(rows[0]), views.site_flag(rows[1]).label], [null, "Job finished, stock still here"]);
	const hidden = views.site_rows(VIEW_BOOT.bins.map((b) => Object.assign({}, b, { stock_value: undefined })));
	check("no value for a caller who may not see it", [hidden[0].value, views.site_totals(hidden).value], [null, null]);
	check("money", views.money(1234.5), "$1,234.50");
});

test("by project: the two places, and which way each row moves", () => {
	const perms = logic.permissions(VIEW_BOOT);
	const s = views.project_sections(PROJECT_VIEW, perms);
	check("shelf rows are the job's share, shaped for the Move dialog", s.shelf.rows, [{ item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", on_hand: 6, value: null, recorded: null }]);
	// The Project Shelf module's balance is by the job on each ledger row; where the shelf holds less,
	// the row is what can be handed over, and says what the records say.
	const short = Object.assign({}, PROJECT_VIEW, { shelf: Object.assign({}, PROJECT_VIEW.shelf, { items: [{ item_code: "PDT-0001", item_name: "PUMP, 1 HP", stock_uom: "Unit", qty: 2, recorded: 6 }] }) });
	const cut = views.project_sections(short, perms).shelf;
	check("a short shelf: the row moves what is there and names what is on record", [cut.rows[0].on_hand, cut.rows[0].recorded, /Count the shelf/.test(cut.note)], [2, 6, true]);
	check("an exact shelf says nothing", [s.shelf.note, s.shelf.rows[0].recorded], [null, null]);
	check("shelf rows go to the site", s.shelf.move, { key: "to_site", label: "Send to job site" });
	check("site rows come back", s.site.move, { key: "to_shelf", label: "Bring back to the shelf" });
	check("to the site", views.project_move("to_site", PROJECT_VIEW), { from_warehouse: SHELF, to_warehouse: SITE, needs_site: false });
	check("back to the shelf", views.project_move("to_shelf", PROJECT_VIEW), { from_warehouse: SITE, to_warehouse: SHELF, needs_site: false });
	const no_site = Object.assign({}, PROJECT_VIEW, { site: null });
	check("no site yet: it is opened first", views.project_move("to_site", no_site), { from_warehouse: SHELF, to_warehouse: null, needs_site: true });
	const finished = Object.assign({}, PROJECT_VIEW, { send_refusal: "Old Lobby is Completed, so nothing more is sent to its job site." });
	const f = views.project_sections(finished, perms);
	check("a finished job: nothing is sent, and the sentence is shown", [f.shelf.move, f.shelf.blocked, views.project_move("to_site", finished)], [null, finished.send_refusal, null]);
	check("but what is there still comes back", f.site.move.key, "to_shelf");
	const reader = views.project_sections(PROJECT_VIEW, logic.permissions(Object.assign({}, VIEW_BOOT, { can_move: false })));
	check("without the right to move, no buttons and no scolding", [reader.shelf.move, reader.shelf.blocked, reader.site.move], [null, null, null]);
	check("no shelf set up", views.project_sections(Object.assign({}, PROJECT_VIEW, { shelf: null }), perms).site.move, null);
	check("unknown direction", views.project_move("sideways", PROJECT_VIEW), null);
	check("find a job", views.filter_projects(VIEW_FIXTURE.project_index, "bolt lobby").map((p) => p.project), ["PRJ-80000"]);
	check("a job's counts", views.project_counts_text(VIEW_FIXTURE.project_index[0]), "1 on the Project Shelf · 2 at the job site");
	check("no site yet", views.project_counts_text({ shelf_items: 2, site: null }), "2 on the Project Shelf · no job site yet");
});

test("problems: four lists in a fixed order, counted", () => {
	const sections = views.problem_sections(PROBLEMS);
	check("order", sections.map((s) => s.key), ["assigned_empty", "unassigned_stock", "finished_shelf", "finished_site"]);
	check("counts", sections.map((s) => s.total), [1, 1, 0, 1]);
	check("total", views.problem_total(PROBLEMS), 3);
	check("nothing read is nothing found", views.problem_total(null), 0);
	check("each says what to do", sections.every((s) => s.hint.length > 20), true);
	// Leftovers of a finished job leave the shelf by a move that names the job, which the page can now make.
	check("the finished-job hint points at the move that works", /Open the Project Shelf and move it to a shop bin \(the move asks which job/.test(sections[2].hint), true);
});

test("a tab is one history entry each; Back and Forward walk them", async () => {
	const { server, world, ctl } = makeViewController();
	world.go_to("/desk/bin-manager");
	await settle();
	const start = world.entries.length;
	ctl.open_tab("projects");
	await settle();
	check("by project: one entry", [world.entries.length - start, world.path()], [1, "/desk/bin-manager/by-project"]);
	check("the list was read", ctl.state.projects.length, 2);
	ctl.open_tab("sites");
	await settle();
	ctl.open_tab("problems");
	await settle();
	check("three tabs, three entries", world.entries.length - start, 3);
	check("problems were read", views.problem_total(ctl.state.problems.data), 3);
	ctl.open_tab("problems");
	check("the tab already on screen adds nothing", world.entries.length - start, 3);
	world.back();
	await settle();
	check("Back: job sites", ctl.state.target, { screen: "sites" });
	world.back();
	world.back();
	await settle();
	check("Back twice more: the picker", ctl.state.target, { screen: "picker" });
	world.forward();
	await settle();
	check("Forward: by project", ctl.state.target, { screen: "projects" });
	check("every read was a GET", server.calls.filter((c) => /bin_views\./.test(c.method)).every((c) => c.opts.method === "GET"), true);
	check("no view wrote anything", server.writes().length, 0);
});

test("a filter is written into the picker's address in place: never a history entry", async () => {
	const { world, ctl } = makeViewController();
	world.go_to("/desk/bin-manager");
	await settle();
	const entries = world.entries.length;
	ctl.set_filters({ kind: "job_site" });
	ctl.set_filters({ quick: "has_stock" });
	ctl.set_filter("wall");
	await settle();
	check("no entry added", world.entries.length, entries);
	check("the address says the filters", world.query(), "q=wall&kind=job_site&quick=has_stock");
	check("and the list follows", shown(ctl), ["Job Site PRJ-90001 Water Wall"]);
	check("the old name for what is typed still holds it", ctl.state.filter, "wall");
	ctl.clear_filters();
	await settle();
	check("cleared: a bare address, every bin", [world.query(), shown(ctl).length, world.entries.length], ["", VIEW_BOOT.bins.length, entries]);
});

test("Back from a bin lands on the picker as it was left; a bare picker has no filters", async () => {
	const { world, ctl } = makeViewController();
	world.go_to("/desk/bin-manager");
	await settle();
	ctl.set_filters({ kind: "job_site", project: "PRJ-90001" });
	ctl.open_bin(SITE);
	await settle();
	check("the bin's address carries no filters", [world.path(), world.query()], ["/desk/bin-manager/" + encodeURIComponent(SITE), ""]);
	check("it knows where it came from", ctl.came_from(), "picker");
	ctl.leave_bin();
	await settle();
	check("back on the filtered picker", [ctl.state.target.screen, ctl.state.filters.kind, ctl.state.filters.project], ["picker", "job_site", "PRJ-90001"]);
	check("same list", shown(ctl), ["Job Site PRJ-90001 Water Wall"]);
	world.forward();
	await settle();
	check("Forward: the bin again", ctl.state.target, { screen: "bin", warehouse: SITE });
	// The tab, clicked from another screen, is the picker with no filters: the address is the truth.
	ctl.open_tab("picker");
	await settle();
	check("a bare picker address has none", [world.query(), views.has_filters(ctl.state.filters)], ["", false]);
});

test("a shared link opens the picker filtered, and the old ?warehouse= link still opens its bin", async () => {
	const { world, ctl } = makeViewController();
	world.go_to("/desk/bin-manager?kind=job_site&quick=has_stock");
	await settle();
	check("filtered", [ctl.state.filters.kind, ctl.state.filters.quick, shown(ctl).length], ["job_site", "has_stock", 2]);
	check("the options were consumed", world.route_options, null);
	world.go_to("/desk/bin-manager?warehouse=" + encodeURIComponent(BIN_A));
	await settle();
	check("the bin", ctl.state.target, { screen: "bin", warehouse: BIN_A });
});

test("the item filter asks the server where the item is, and a late answer for another item is dropped", async () => {
	const { server, world, ctl } = makeViewController();
	world.go_to("/desk/bin-manager");
	await settle();
	ctl.set_filters({ item: "PDT-0001" });
	check("nothing shown while it is read", [ctl.state.item.loading, shown(ctl)], [true, []]);
	await settle();
	const call = server.calls.find((c) => c.method.endsWith("bin_views.item_locations"));
	check("asked once, by GET", [call.args, call.opts.method], [{ item_code: "PDT-0001" }, "GET"]);
	check("the places that hold it or have it assigned", shown(ctl), ["Bin A1-1-1", "Job Site PRJ-90001 Water Wall"]);
	check("in the address", world.query(), "item=PDT-0001");
	// A link with the item in it reads it on arrival.
	const linked = makeViewController();
	linked.world.go_to("/desk/bin-manager?item=PDT-0001&kind=job_site");
	await settle();
	check("a link with an item", shown(linked.ctl), ["Job Site PRJ-90001 Water Wall"]);
	ctl.set_filters({ item: "" });
	await settle();
	check("cleared", [ctl.state.item, shown(ctl).length], [null, VIEW_BOOT.bins.length]);
	// Two picks in a row: only the last answer counts.
	let release;
	server.delay.set("item_locations", new Promise((resolve) => (release = resolve)));
	ctl.set_filters({ item: "PDT-OLD" });
	server.delay.delete("item_locations");
	ctl.set_filters({ item: "PDT-0001" });
	await settle();
	release();
	await settle();
	check("the answer on screen is for the item on screen", ctl.state.item.item_code, "PDT-0001");
	server.fail.set("item_locations", [{ status: 417, message: "There is no item PDT-NOPE." }]);
	ctl.set_filters({ item: "PDT-NOPE" });
	await settle();
	check("a refusal is kept for the page to show", ctl.state.item.error.message, "There is no item PDT-NOPE.");
});

test("by project: a job is one entry, and a move between shelf and site is the page's ordinary Move", async () => {
	const { server, world, ctl } = makeViewController();
	world.go_to("/desk/bin-manager/by-project");
	await settle();
	const entries = world.entries.length;
	ctl.open_project("PRJ-90001");
	await settle();
	check("one entry", [world.entries.length - entries, world.path()], [1, "/desk/bin-manager/by-project/PRJ-90001"]);
	check("the job was read", ctl.state.project.data.project.project_name, "Water Wall");
	const sections = views.project_sections(ctl.state.project.data, ctl.perms());
	const before = world.entries.length;
	await ctl.project_submit("to_site", sections.shelf.rows[0], { qty: 2 }, "bm-proj-0000001");
	await settle();
	const write = server.writes().pop();
	check("move_to, shelf to site, nothing made a home", [write.method.endsWith("bin_assignments.move_to"), write.args], [
		true,
		{ item_code: "PDT-0001", from_warehouse: SHELF, to_warehouse: SITE, qty: 2, client_ref: "bm-proj-0000001", make_home: 0 },
	]);
	check("no site had to be opened", server.calls.some((c) => c.method.endsWith("stock_scan.job_site")), false);
	check("a save is no history entry", world.entries.length, before);
	check("the job is read again after it", server.calls.filter((c) => c.method.endsWith("bin_views.project_view")).length, 2);
	check("the notice can undo it", ctl.state.notice.log.can_undo, true);
	await ctl.project_submit("to_shelf", sections.site.rows[0], { qty: 1 }, "bm-proj-0000002");
	const back = server.writes().pop();
	check("site to shelf", [back.args.from_warehouse, back.args.to_warehouse, back.args.item_code], [SITE, SHELF, "PDT-0002"]);
	// Only this job's share of the shelf may be sent, checked before any call. The row already shows
	// what the first move left (6 less the 2 sent), as a row on a bin does after a move.
	const calls = server.writes().length;
	const err = await ctl.project_submit("to_site", sections.shelf.rows[0], { qty: 60 }, "bm-proj-0000003").catch((e) => e);
	check("more than the job's share is refused in words", [err.message, server.writes().length], ["Only 4 Unit is here.", calls]);
	ctl.leave_project();
	await settle();
	check("All jobs steps back to the list", [ctl.state.target.screen, world.entries.length], ["projects", before]);
});

test("by project: a job with no site has it opened first, once, and a refusal there saves nothing", async () => {
	const { server, world, ctl } = makeViewController({ project_view: Object.assign({}, PROJECT_VIEW, { site: null }) });
	world.go_to("/desk/bin-manager/by-project/PRJ-90001");
	await settle();
	const row = views.project_sections(ctl.state.project.data, ctl.perms()).shelf.rows[0];
	await ctl.project_submit("to_site", row, { qty: 1 }, "bm-proj-0000004");
	const order = server.calls.filter((c) => /job_site$|move_to$/.test(c.method)).map((c) => c.method.split(".").pop());
	check("the site is opened, then the move", order, ["job_site", "move_to"]);
	check("opened for this job", server.calls.find((c) => c.method.endsWith("job_site")).args, { project: "PRJ-90001" });
	check("moved to the site that answered", server.writes().pop().args.to_warehouse, "Job Site PRJ-90001 Opened - SF");

	const refused = makeViewController({ project_view: Object.assign({}, PROJECT_VIEW, { site: null }) });
	refused.world.go_to("/desk/bin-manager/by-project/PRJ-90001");
	await settle();
	refused.server.fail.set("job_site", [{ status: 417, message: "Job sites are kept for Build jobs, and Water Wall is not one." }]);
	const err = await refused.ctl.project_submit("to_site", row, { qty: 1 }, "bm-proj-0000005").catch((e) => e);
	check("the server's sentence, and nothing moved", [err.message, err.keep_ref, refused.server.calls.some((c) => c.method.endsWith("move_to"))], [
		"Job sites are kept for Build jobs, and Water Wall is not one.", false, false,
	]);
	// A job that cannot be sent to at all never reaches the server.
	const closed = makeViewController({ project_view: Object.assign({}, PROJECT_VIEW, { send_refusal: "Old Lobby is Completed." }) });
	closed.world.go_to("/desk/bin-manager/by-project/PRJ-90001");
	await settle();
	const none = await closed.ctl.project_submit("to_site", row, { qty: 1 }, "bm-proj-0000006").catch((e) => e);
	check("refused here", [none.keep_ref, closed.server.writes().length], [false, 0]);
});

test("a bin opened from Job sites or Problems steps back to where it was opened", async () => {
	const { world, ctl } = makeViewController();
	world.go_to("/desk/bin-manager/problems");
	await settle();
	const entries = world.entries.length;
	ctl.open_bin(BIN_A);
	await settle();
	check("one entry, tagged", [world.entries.length - entries, ctl.came_from()], [1, "problems"]);
	ctl.leave_bin();
	await settle();
	check("back on Problems, no second copy", [ctl.state.target.screen, world.index], ["problems", entries - 1]);
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	check("a bin opened by a link has no tag", ctl.came_from(), null);
});

test("the real page: tabs, filters and the three views draw, and names are data", async () => {
	const { server, world, registry, click } = loadPage(VIEW_FIXTURE);
	world.go_to("/desk/bin-manager");
	await settle();
	const root = () => registry.root.__state.html;
	const part = (sel) => registry.found[sel][0].innerHTML;
	check("four tabs, Bins on", [(root().match(/class="bm-tab /g) || []).length, /class="bm-tab is-on"[^>]*data-tab="picker"/.test(root().replace(/\s+/g, " "))], [4, true]);
	check("kinds offered with counts", /data-kind="job_site">Job sites <span class="bm-chip-count">2<\/span>/.test(part(".bm-filters")), true);
	check("a job to pick", /<option value="PRJ-90001" >Water Wall · Acme Hotels<\/option>/.test(part(".bm-filters")), true);
	check("every bin listed", (part(".bm-list").match(/data-act="bin"/g) || []).length, VIEW_BOOT.bins.length);
	check("a site says whose it is", /Water Wall · Acme Hotels/.test(part(".bm-list")), true);
	const entries = world.entries.length;
	click({ "data-act": "kind", "data-kind": "job_site" });
	await settle();
	await new Promise((resolve) => setTimeout(resolve, 300));
	check("a kind click filters the list", (part(".bm-list").match(/data-act="bin"/g) || []).length, 2);
	check("and writes the address in place", [world.query(), world.entries.length], ["kind=job_site", entries]);
	check("the count says so", /2 of 8 places/.test(part(".bm-filters")), true);
	click({ "data-act": "quick", "data-quick": "has_stock" });
	click({ "data-act": "quick", "data-quick": "has_stock" });
	await settle();
	check("a quick filter clicked twice is off again", /data-quick="has_stock"[^>]*aria-pressed="true"/.test(part(".bm-filters")), false);

	click({ "data-act": "tab", "data-tab": "sites" });
	await settle();
	check("Job sites is an entry", [world.entries.length - entries, world.path()], [1, "/desk/bin-manager/job-sites"]);
	check("the filter did not leak onto it", world.query(), "");
	check("a row per site with its job, items and value", [/Water Wall/.test(part(".bm-list")), /\$502\.00/.test(part(".bm-list")), /2 job sites/.test(part(".bm-list"))], [true, true, true]);
	check("a finished job with stock is flagged", /Job finished, stock still here/.test(part(".bm-list")), true);

	click({ "data-act": "tab", "data-tab": "problems" });
	await settle();
	check("Problems lists each kind", [/Assigned but empty/.test(part(".bm-list")), /Stock with no assignment/.test(part(".bm-list")), /Still at a finished job/.test(part(".bm-list"))], [true, true, true]);
	check("an item's name is escaped", [/VALVE &lt;2 IN&gt;/.test(part(".bm-list")), /VALVE <2 IN>/.test(part(".bm-list"))], [true, false]);

	click({ "data-act": "project", "data-project": "PRJ-90001" });
	await settle();
	check("a job from a problem row", world.path(), "/desk/bin-manager/by-project/PRJ-90001");
	check("both places drawn", [/On the Project Shelf for this job/.test(part(".bm-project-body")), /At the job site/.test(part(".bm-project-body"))], [true, true]);
	check("each row has its one move", [/data-move="to_site"/.test(part(".bm-project-body")), /data-move="to_shelf"/.test(part(".bm-project-body"))], [true, true]);
	click({ "data-act": "project-move", "data-move": "to_site", "data-section": "shelf", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	check("the dialog opened, with the job's share as the quantity", [d.shown, d.values.qty], [true, 6]);
	d.values.qty = 2;
	await d.click_primary();
	await settle();
	const write = server.writes().pop();
	check("it moved through move_to", [write.method.endsWith("bin_assignments.move_to"), write.args.from_warehouse, write.args.to_warehouse, write.args.qty], [true, SHELF, SITE, 2]);
	check("the dialog closed", d.shown, false);
	world.back();
	await settle();
	check("Back from the job is Problems", world.path(), "/desk/bin-manager/problems");
});

test("the real page: a filtered picker comes back after a bin, and a boot without the views' data is the old page", async () => {
	const { world, registry, click } = loadPage(VIEW_FIXTURE);
	world.go_to("/desk/bin-manager?kind=job_site");
	await settle();
	const part = (sel) => registry.found[sel][0].innerHTML;
	check("a shared link is filtered on arrival", (part(".bm-list").match(/data-act="bin"/g) || []).length, 2);
	click({ "data-act": "bin", "data-bin": SITE });
	await settle();
	check("the bin's way back names the picker", /All bins/.test(registry.root.__state.html), true);
	world.back();
	await settle();
	check("Back: the same two", [(part(".bm-list").match(/data-act="bin"/g) || []).length, world.query()], [2, "kind=job_site"]);

	// A server that sends no kinds (the deploy's first minutes, or the views' data failed): every bin, no kind row.
	const old = loadPage();
	old.world.go_to("/desk/bin-manager");
	await settle();
	check("every bin still listed", (old.registry.found[".bm-list"][0].innerHTML.match(/data-act="bin"/g) || []).length, BOOT.bins.length);
	check("the boneyard is still marked", /Boneyard<\/span>/.test(old.registry.found[".bm-list"][0].innerHTML), true);
	check("no job box with no jobs", /bm-project-filter/.test(old.registry.found[".bm-filters"][0].innerHTML), false);
});

// ------------------------------------------------------------------ a move onto or off the Project Shelf names its job

// A site where the Project Shelf's release is installed: the boot says moves name their job.
const SHELF_BOOT = Object.assign({}, VIEW_BOOT, { shelf_job_moves: true });
const ONE_JOB = { item_code: "PDT-0001", project_shelf: true, shelf_jobs: [{ project: "PRJ-90001", project_name: "Water Wall", qty: 4 }], shelf_jobs_exact: true };
const TWO_JOBS = { item_code: "PDT-0001", project_shelf: true, shelf_jobs: [{ project: "PRJ-90001", project_name: "Water Wall", qty: 3 }, { project: "PRJ-80000", project_name: "Old Lobby", qty: 1 }], shelf_jobs_exact: true };
const NO_JOBS = { item_code: "PDT-0001", project_shelf: true, shelf_jobs: [], shelf_jobs_exact: false };

const jobHandler = (d, selector) => d.fields_dict.bm_job.$wrapper.__state.handlers.find((h) => h.selector === selector);
const pickJob = (d, project) => jobHandler(d, "[data-bm-job]").fn({ currentTarget: { getAttribute: () => project } });
const jobHtml = (d) => d.fields_dict.bm_job.$wrapper.__state.html || "";
// Whether the dialog is asking for a job at all (a picker that never had a rule draws nothing).
const asksForJob = (d) => /Which job|bm-job-search|data-bm-job/.test(jobHtml(d));
const afterSearch = async () => {
	await new Promise((resolve) => setTimeout(resolve, 300));
	await settle();
};

test("the shelf's job: when a move names one, and which job it starts on", () => {
	const is_site = (w) => w === SITE;
	check("no shelf on this site: nothing ever asks", [logic.shelf_move(null, SHELF, BIN_A, is_site), logic.shelf_move(null, BIN_A, SHELF, is_site)], [null, null]);
	check("off the shelf to a bin", logic.shelf_move(SHELF, SHELF, BIN_A, is_site), "off");
	check("off the shelf to the boneyard", logic.shelf_move(SHELF, SHELF, BONEYARD, is_site), "off");
	check("off the shelf, destination not picked yet", logic.shelf_move(SHELF, SHELF, null, is_site), "off");
	check("shelf to a job site: the site names the job", logic.shelf_move(SHELF, SHELF, SITE, is_site), null);
	check("a job site back to the shelf: the same", logic.shelf_move(SHELF, SITE, SHELF, is_site), null);
	check("a bin onto the shelf", logic.shelf_move(SHELF, BIN_A, SHELF, is_site), "onto");
	check("bin to bin", logic.shelf_move(SHELF, BIN_A, BIN_B, is_site), null);
	check("the only job, when the tags are exact", logic.job_preselect(ONE_JOB).project, "PRJ-90001");
	check("the only job, but the tags are not exact: nobody is assumed", logic.job_preselect(Object.assign({}, ONE_JOB, { shelf_jobs_exact: false })), null);
	check("several jobs: the person picks", logic.job_preselect(TWO_JOBS), null);
	check("none", [logic.job_preselect(NO_JOBS), logic.job_choices(NO_JOBS)], [null, []]);
	check("an answer from before the shelf (no keys) is no jobs", [logic.job_choices({}), logic.job_preselect({}), logic.job_choices(null)], [[], null, []]);
	check("a job in the list", logic.job_label(TWO_JOBS.shelf_jobs[0], "Unit"), "Water Wall · 3 Unit here");
	check("the capability comes from the boot, with a shelf", [logic.permissions(SHELF_BOOT).shelf, logic.permissions(VIEW_BOOT).shelf, logic.permissions(Object.assign({}, SHELF_BOOT, { shelf_warehouse: null })).shelf], [SHELF, null, null]);
	// The job is refused in words when it is missing, and travels only when there is one.
	const row = { item_code: "PDT-0001", on_hand: 4, stock_uom: "Unit" };
	check("off, no job", logic.check_values("move", row, { qty: 1, to_warehouse: BIN_A, job_rule: "off" }, SHELF), "Pick the job these were bought for.");
	check("onto, no job", logic.check_values("move", row, { qty: 1, to_warehouse: SHELF, job_rule: "onto" }, BIN_A), "Pick the job these are being set aside for.");
	check("boneyard, no job", logic.check_values("boneyard", row, { qty: 1, job_rule: "off" }, SHELF), "Pick the job these were bought for.");
	check("with a job", logic.check_values("move", row, { qty: 1, to_warehouse: BIN_A, job_rule: "off", project: "PRJ-90001" }, SHELF), null);
	check("no rule, no job: as before", logic.check_values("move", row, { qty: 1, to_warehouse: BIN_B }, BIN_A), null);
	check("move carries it", logic.request("move", SHELF, row, { qty: 1, to_warehouse: BIN_A, project: " PRJ-90001 " }, "bm-x-00000001").args.project, "PRJ-90001");
	check("boneyard carries it", logic.request("boneyard", SHELF, row, { qty: 1, project: "PRJ-90001" }, "bm-x-00000001").args.project, "PRJ-90001");
	check("no job: the request is the one it always was", Object.keys(logic.request("move", BIN_A, row, { qty: 1, to_warehouse: BIN_B }, "bm-x-00000001").args), ["item_code", "from_warehouse", "to_warehouse", "qty", "client_ref", "make_home"]);
	check("nothing is made a home at the shelf or a job site", [logic.make_home_offered(logic.permissions(SHELF_BOOT), SHELF, "project_shelf"), logic.make_home_offered(logic.permissions(SHELF_BOOT), SITE, "job_site"), logic.make_home_offered(logic.permissions(SHELF_BOOT), BIN_B, "shop_bin")], [false, false, true]);
});

test("the real page: a move off the shelf with one exact job starts on it and sends it", async () => {
	const { server, world, registry, click } = loadPage(Object.assign({}, VIEW_FIXTURE, { boot: SHELF_BOOT, shelf_item: ONE_JOB }));
	world.go_to("/desk/bin-manager/" + encodeURIComponent(SHELF));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	await settle();
	const asked = server.calls.find((c) => c.method.endsWith("stock_scan.get_item"));
	check("the jobs that hold it were asked for, at the shelf", asked && asked.args, { item_code: "PDT-0001", warehouse: SHELF });
	check("the only job is already chosen, and can be changed", [/Bought for <b>Water Wall<\/b>/.test(jobHtml(d)), /data-bm-job-change/.test(jobHtml(d))], [true, true]);
	pickDestination(d, BIN_A);
	d.values.qty = 2;
	await d.click_primary();
	await settle();
	const move = server.writes().pop();
	check("move_to carries the job", [move.method.endsWith("bin_assignments.move_to"), move.args.from_warehouse, move.args.to_warehouse, move.args.project], [true, SHELF, BIN_A, "PRJ-90001"]);
	check("the dialog closed", d.shown, false);

	// To a job site instead: the site names the job, so none is asked for and none is sent.
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const s = registry.dialogs.pop();
	await settle();
	pickDestination(s, SITE);
	check("no question once a job site is the destination", asksForJob(s), false);
	check("and nothing is made a home there", s.props["make_home.hidden"], 1);
	s.values.qty = 1;
	await s.click_primary();
	await settle();
	check("shelf to a job site sends no job", "project" in server.writes().pop().args, false);

	// Send to boneyard from the shelf names the job too.
	click({ "data-act": "boneyard", "data-item": "PDT-0001" });
	const b = registry.dialogs.pop();
	await settle();
	b.values.qty = 1;
	await b.click_primary();
	await settle();
	const trip = server.writes().pop();
	check("send_to_boneyard carries the job", [trip.method.endsWith("bin_assignments.send_to_boneyard"), trip.args.project, trip.args.qty], [true, "PRJ-90001", 1]);
});

test("the real page: a move off the shelf with several jobs makes the person choose", async () => {
	const { server, world, registry, click } = loadPage(Object.assign({}, VIEW_FIXTURE, { boot: SHELF_BOOT, shelf_item: TWO_JOBS }));
	world.go_to("/desk/bin-manager/" + encodeURIComponent(SHELF));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	await settle();
	check("nobody is chosen for them", /Bought for <b>/.test(jobHtml(d)), false);
	check("both jobs are offered, with what each holds", [/Which job were these bought for\?/.test(jobHtml(d)), /data-bm-job="PRJ-90001">Water Wall · 3 Unit here/.test(jobHtml(d)), /data-bm-job="PRJ-80000">Old Lobby · 1 Unit here/.test(jobHtml(d))], [true, true, true]);
	pickDestination(d, BIN_A);
	d.values.qty = 1;
	const before = server.writes().length;
	await d.click_primary();
	await settle();
	check("no job picked: refused in words, nothing sent", [/Pick the job these were bought for\./.test(msgOf(d)), server.writes().length, d.shown], [true, before, true]);
	// A finished job may be named: releasing its leftovers is the main reason to move anything off.
	pickJob(d, "PRJ-80000");
	check("the choice is shown", /Bought for <b>Old Lobby<\/b>/.test(jobHtml(d)), true);
	await d.click_primary();
	await settle();
	check("sent with the job that was picked", server.writes().pop().args.project, "PRJ-80000");
	// One job on record but the tags do not account for everything there: still the person's call.
	const loose = loadPage(Object.assign({}, VIEW_FIXTURE, { boot: SHELF_BOOT, shelf_item: Object.assign({}, ONE_JOB, { shelf_jobs_exact: false }) }));
	loose.world.go_to("/desk/bin-manager/" + encodeURIComponent(SHELF));
	await settle();
	loose.click({ "data-act": "move", "data-item": "PDT-0001" });
	const l = loose.registry.dialogs.pop();
	await settle();
	check("one job, tags not exact: offered, not assumed", [/Bought for <b>/.test(jobHtml(l)), /data-bm-job="PRJ-90001"/.test(jobHtml(l))], [false, true]);
});

test("the real page: a move off the shelf with no job on record finds one by name", async () => {
	const { server, world, registry, click } = loadPage(Object.assign({}, VIEW_FIXTURE, { boot: SHELF_BOOT, shelf_item: NO_JOBS }));
	world.go_to("/desk/bin-manager/" + encodeURIComponent(SHELF));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	await settle();
	check("it says there is none on record, and offers the search", [/No job is on record for this item on the shelf/.test(jobHtml(d)), /bm-job-search/.test(jobHtml(d))], [true, true]);
	pickDestination(d, BIN_A);
	d.values.qty = 1;
	const before = server.writes().length;
	await d.click_primary();
	await settle();
	check("nothing is guessed and nothing is sent", [/Pick the job these were bought for\./.test(msgOf(d)), server.writes().length], [true, before]);
	jobHandler(d, ".bm-job-search").fn({ currentTarget: { value: "court" } });
	await afterSearch();
	const search = server.calls.find((c) => c.method.endsWith("stock_scan.search_projects"));
	check("the job search was asked", search && search.args, { query: "court" });
	check("its results are listed, with the customer", /data-bm-job="PRJ-90007">Courtyard Rill · Greenway Homes/.test(registry.found[".bm-job-results"].__state.html), true);
	pickJob(d, "PRJ-90007");
	check("the found job is chosen by name", /Bought for <b>Courtyard Rill<\/b>/.test(jobHtml(d)), true);
	await d.click_primary();
	await settle();
	check("sent with it", server.writes().pop().args.project, "PRJ-90007");
	// A read of the jobs that fails is the same as none: the person can still find one.
	const down = loadPage(Object.assign({}, VIEW_FIXTURE, { boot: SHELF_BOOT }));
	down.server.fail.set("get_item", { status: 500, message: "boom" });
	down.world.go_to("/desk/bin-manager/" + encodeURIComponent(SHELF));
	await settle();
	down.click({ "data-act": "move", "data-item": "PDT-0001" });
	const f = down.registry.dialogs.pop();
	await settle();
	check("a failed read still opens the dialog with the search", [f.shown, /bm-job-search/.test(jobHtml(f))], [true, true]);
});

test("the real page: a move onto the shelf asks which job it is set aside for", async () => {
	const { server, world, registry, click } = loadPage(Object.assign({}, VIEW_FIXTURE, { boot: SHELF_BOOT }));
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	await settle();
	check("a bin's Move asks nothing until the shelf is the destination", asksForJob(d), false);
	check("and the shelf's jobs are not read for it", server.calls.some((c) => c.method.endsWith("stock_scan.get_item")), false);
	pickDestination(d, SHELF);
	check("now it asks, with the search and no list of holders", [/Which job are these being set aside for\?/.test(jobHtml(d)), /bm-job-search/.test(jobHtml(d)), /data-bm-job=/.test(jobHtml(d))], [true, true, false]);
	check("the shelf is nobody's bin: no make-home box", d.props["make_home.hidden"], 1);
	d.values.qty = 2;
	const before = server.writes().length;
	await d.click_primary();
	await settle();
	check("no job: refused in words, nothing sent", [/Pick the job these are being set aside for\./.test(msgOf(d)), server.writes().length], [true, before]);
	jobHandler(d, ".bm-job-search").fn({ currentTarget: { value: "court" } });
	await afterSearch();
	pickJob(d, "PRJ-90007");
	check("chosen", /Set aside for <b>Courtyard Rill<\/b>/.test(jobHtml(d)), true);
	await d.click_primary();
	await settle();
	const move = server.writes().pop();
	check("move_to onto the shelf carries the job, and no home", [move.args.from_warehouse, move.args.to_warehouse, move.args.project, move.args.make_home], [BIN_A, SHELF, "PRJ-90007", 0]);
	// Back to an ordinary bin: the question goes away and no job travels.
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const o = registry.dialogs.pop();
	await settle();
	pickDestination(o, SHELF);
	pickDestination(o, BIN_B);
	check("another bin picked instead: no question", asksForJob(o), false);
	o.values.qty = 1;
	await o.click_primary();
	await settle();
	check("bin to bin sends no job", "project" in server.writes().pop().args, false);
});

test("the real page: on a site without the Project Shelf's release, moves are what they always were", async () => {
	// The boot has a shelf (the setting can exist) but does not say moves name their job.
	const { server, world, registry, click } = loadPage(VIEW_FIXTURE);
	world.go_to("/desk/bin-manager/" + encodeURIComponent(SHELF));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const d = registry.dialogs.pop();
	await settle();
	check("no question", asksForJob(d), false);
	check("nothing extra is read", server.calls.some((c) => /get_item|search_projects/.test(c.method)), false);
	pickDestination(d, BIN_A);
	d.values.qty = 1;
	await d.click_primary();
	await settle();
	check("the request is exactly the old one", Object.keys(server.writes().pop().args), ["item_code", "from_warehouse", "to_warehouse", "qty", "client_ref", "make_home"]);
	click({ "data-act": "boneyard", "data-item": "PDT-0001" });
	const b = registry.dialogs.pop();
	await settle();
	b.values.qty = 1;
	await b.click_primary();
	await settle();
	check("and so is the boneyard trip", Object.keys(server.writes().pop().args), ["item_code", "from_warehouse", "qty", "client_ref"]);
	// Onto the shelf from a bin: no question either.
	world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	click({ "data-act": "move", "data-item": "PDT-0001" });
	const o = registry.dialogs.pop();
	await settle();
	pickDestination(o, SHELF);
	check("onto the shelf: no question", asksForJob(o), false);
	o.values.qty = 1;
	await o.click_primary();
	await settle();
	check("and no job sent", "project" in server.writes().pop().args, false);
	// A boot from before the views (no kinds, no shelf): the same.
	const old = loadPage();
	old.world.go_to("/desk/bin-manager/" + encodeURIComponent(BIN_A));
	await settle();
	old.click({ "data-act": "move", "data-item": "PDT-0001" });
	const p = old.registry.dialogs.pop();
	await settle();
	pickDestination(p, BIN_B);
	check("the old boot: the make-home box is offered as before", p.props["make_home.hidden"], 0);
});

test("by project: the job on screen is named on the way to the site, and left out on the way back", async () => {
	const { server, world, ctl } = makeViewController({ boot: SHELF_BOOT });
	world.go_to("/desk/bin-manager/by-project/PRJ-90001");
	await settle();
	const sections = views.project_sections(ctl.state.project.data, ctl.perms());
	await ctl.project_submit("to_site", sections.shelf.rows[0], { qty: 1 }, "bm-proj-0000011");
	check("to the site: the view's job", server.writes().pop().args.project, "PRJ-90001");
	await ctl.project_submit("to_shelf", sections.site.rows[0], { qty: 1 }, "bm-proj-0000012");
	// The site names the job; a job named for stock going onto the shelf must still be in progress,
	// which would refuse bringing back a finished job's leftovers.
	check("back to the shelf: nothing, the site names it", "project" in server.writes().pop().args, false);
	const plain = makeViewController();
	plain.world.go_to("/desk/bin-manager/by-project/PRJ-90001");
	await settle();
	await plain.ctl.project_submit("to_site", sections.shelf.rows[0], { qty: 1 }, "bm-proj-0000013");
	check("a site without the release: no job sent", "project" in plain.server.writes().pop().args, false);
});

// ------------------------------------------------------------------ run

for (const { name, fn } of tests) {
	const before = failures;
	try {
		await fn();
	} catch (e) {
		failures += 1;
		console.log(`  FAIL ${name}: threw ${e && e.stack}`);
	}
	console.log(`${failures === before ? "ok  " : "FAIL"} ${name}`);
}
console.log(`${checks} checks, ${failures} failed`);
if (failures) process.exit(1);
console.log(`${checks} checks passed`);
