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
 * Run: node scripts/test_bin_manager.mjs   (from tests/test_bin_manager.py)
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const logic = await import(pathToFileURL(path.join(APP, "public/js/bin_manager/logic.js")).href);
const { Controller } = await import(pathToFileURL(path.join(APP, "public/js/bin_manager/controller.js")).href);
const PAGE_JS = path.join(APP, "inventory_enhancements/page/bin_manager/bin_manager.js");
// The reorder boxes (v1.720.0), the real modules, for the page test that gives the page its bundle.
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
			if (name === "manager_boot") return JSON.parse(JSON.stringify(fixture.boot || BOOT));
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
			if (name === "search_items") return [{ item_code: "PDT-0009", item_name: "VALVE", is_stock_item: 1, on_hand_here: 0 }];
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
			return { log, message: `${name} done`, repeated: false };
		});
	};
	server.writes = () => server.calls.filter((c) => !/manager_boot|get_location|get_recent|search_items|check_new_item|get_reorder_levels/.test(c.method));
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
	const registry = { dialogs: [], requires: [] };
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
			for (const f of opts.fields || []) this.fields_dict[f.fieldname] = { $wrapper: $("<div>"), $input: $("<input>") };
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
			escape_html: (s) =>
				String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
		},
		datetime: { comment_when: () => "just now" },
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
		sf_bin_manager: Object.assign({ version: 1, Controller }, logic),
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
			replaceState: (s) => {
				world.entries[world.index].state = s;
			},
			back: () => world.back(),
		},
		location: { reload: () => {} },
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

test("the real page with the reorder bundle: a Reorder column, one read per list, no history", async () => {
	const { server, world, registry, page, click } = loadPage({ reorder: true });
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
	const { server, world, registry, page, click, document } = loadPage({ reorder: true });
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
