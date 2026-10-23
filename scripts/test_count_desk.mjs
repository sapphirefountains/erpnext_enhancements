#!/usr/bin/env node
/**
 * The Count Desk (v1.737.0) under node: its pure module (public/js/count_desk/logic.js) and its
 * controller (controller.js) against a model of the v16 router, a fake server and fake timers.
 *
 * What it pins, in the terms that matter to the person counting:
 *   - a count cell takes 3*24+5 and 3x24; a barcode or a label scanned into it is never a quantity;
 *   - the tree's roll-ups, the presets and "a whole group is sent as the group";
 *   - Enter goes to the next row (the next uncounted under "To count"), and rows counted under a
 *     filter do not vanish under the cursor;
 *   - a pasted column fills down one value per row on screen, a refused row taking its value and
 *     keeping nothing; Zero the rest only touches uncounted rows;
 *   - one save in flight at a time, edits made meanwhile merged into the next batch (last value
 *     wins), a dropped connection retried with backoff and never losing a newer edit, a refusal not
 *     retried; Undo puts the previous value back;
 *   - a refused row goes back to what the server has, marked, and a refused save holds Post back;
 *   - under a Counted At an uncounted row is counted against the ledger then (as_of_qty);
 *   - the summary, the row key and the paste agree with count_desk_rules.py and its vectors
 *     (tests/data/count_desk_vectors.json);
 *   - CSV is formula-safe and leaves the system out of a blind count; the print sheet escapes.
 *
 * Run: node scripts/test_count_desk.mjs   (from tests/test_count_desk.py)
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const L = await import(pathToFileURL(path.join(APP, "public/js/count_desk/logic.js")).href);
const { Controller } = await import(pathToFileURL(path.join(APP, "public/js/count_desk/controller.js")).href);
const VECTORS = JSON.parse(fs.readFileSync(path.join(APP, "tests/data/count_desk_vectors.json"), "utf8"));

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
	for (let i = 0; i < 12; i++) await tick();
}

// ------------------------------------------------------------------ fixtures

const W1 = "Bin A1-1-1 - SF";
const W2 = "Bin A1-1-2 - SF";
const K = (item, wh) => L.line_key(item, wh || W1);

function row(item, wh, extra) {
	return Object.assign(
		{
			key: K(item, wh),
			warehouse: wh || W1,
			warehouse_name: (wh || W1).replace(/ - SF$/, ""),
			storage_location: null,
			item_code: item,
			item_name: `ITEM ${item}`,
			uom: "Unit",
			whole_number: true,
			system_qty: 10,
			reserved: 0,
			rate: 2,
			refusal: null,
			idx: null,
			counted: false,
			counted_qty: null,
			snapshot_qty: null,
			variance: null,
			group_lines: 0,
			moved: 0,
			value_variance: null,
			reason_code: "",
			reason: "",
			recount: false,
			scan_time: null,
			counted_by: null,
			expected: true,
			assigned: true,
			on_order: 0,
			is_default: false,
			undecided: false,
		},
		extra || {}
	);
}

function counted(item, wh, qty, snapshot, extra) {
	const variance = qty - snapshot;
	return row(item, wh, Object.assign({ counted: true, counted_qty: qty, snapshot_qty: snapshot, variance, value_variance: variance * 2, group_lines: 1 }, extra || {}));
}

const BOOT = {
	user: "parker@example.com",
	today: "2026-10-20",
	supervisor: false,
	can_post: true,
	coverage_days: 90,
	groups: [
		{ name: "All Warehouses - SF", label: "All Warehouses", parent: null },
		{ name: "Row A - SF", label: "Row A", parent: "All Warehouses - SF" },
		{ name: "Shelf A1-1 - SF", label: "Shelf A1-1", parent: "Row A - SF" },
		{ name: "Row Empty - SF", label: "Row Empty", parent: "All Warehouses - SF" },
	],
	bins: [
		{ warehouse: W1, warehouse_name: "Bin A1-1-1", parent: "Shelf A1-1 - SF", on_hand_items: 3, stock_value: 100, days_since: null },
		{ warehouse: W2, warehouse_name: "Bin A1-1-2", parent: "Shelf A1-1 - SF", on_hand_items: 2, stock_value: 500, days_since: 120 },
		{ warehouse: "Bin A1-1-10 - SF", warehouse_name: "Bin A1-1-10", parent: "Shelf A1-1 - SF", on_hand_items: 1, stock_value: 50, days_since: 5 },
		{ warehouse: "Stores - SF", warehouse_name: "Stores", parent: "All Warehouses - SF", on_hand_items: 0, stock_value: 0, days_since: null },
	],
	open_counts: [{ name: "INV-CNT-2026-00001", title: "Shelf A1-1", counted_by_name: "Sam", scope: [W2], lines: 3 }],
	recent_counts: [],
	stats: { coverage: { bins: 3, counted: 1, pct: 33.3, never: 1 }, accuracy: null, items_counted: 0, counts: 0, adjusted_value: 0, days: 90 },
	reasons: ["Earlier count was wrong", "Other"],
	require_reason: false,
};

function sheet_payload(rows, extra) {
	return {
		count: Object.assign(
			{
				name: "INV-CNT-2026-00007",
				title: "Shelf A1-1",
				status: "Open",
				counted_by: "parker@example.com",
				counted_by_name: "Parker Bailey",
				mine: true,
				blind: false,
				scoped: true,
				scope: [
					{ warehouse: W1, warehouse_name: "Bin A1-1-1", trail: "Row A › Shelf A1-1" },
					{ warehouse: W2, warehouse_name: "Bin A1-1-2", trail: "Row A › Shelf A1-1" },
				],
				editable: true,
				notes: "",
			},
			extra || {}
		),
		rows,
		can_post: true,
		require_reason: false,
		reasons: ["Earlier count was wrong", "Other"],
	};
}

// ------------------------------------------------------------------ the world: router, server, timers

function makeWorld(options) {
	const o = options || {};
	const world = {
		entries: [{ route: ["count-desk"], state: null }],
		index: 0,
		calls: [],
		timers: [],
		renders: [],
		rows: (o.rows || [row("A"), row("B"), row("C", W2)]).map((r) => Object.assign({}, r)),
		fail: new Map(),
		hold: new Map(),
	};
	world.server = (method, args, opts) => {
		world.calls.push({ method, args: Object.assign({}, args), opts: Object.assign({}, opts) });
		const name = method.split(".").pop();
		const run = () => {
			const failure = world.fail.get(name);
			if (failure && failure.length) return Promise.reject(failure.shift());
			if (name === "boot") return Promise.resolve(JSON.parse(JSON.stringify(BOOT)));
			if (name === "get_sheet") return Promise.resolve(sheet_payload(JSON.parse(JSON.stringify(world.rows)), o.sheet));
			if (name === "start_count") return Promise.resolve({ name: "INV-CNT-2026-00007", bins: 2, problems: [], overlaps: o.overlaps || [] });
			if (name === "save_counts") {
				const entries = JSON.parse(args.rows);
				const results = [];
				const groups = [];
				for (const e of entries) {
					const r = world.rows.find((x) => x.key === e.key);
					if (o.refuse && o.refuse(e)) {
						results.push({ key: e.key, ok: false, message: "No." });
						continue;
					}
					if ("counted_qty" in e) {
						if (e.counted_qty === null) {
							Object.assign(r, L.uncounted(r));
						} else {
							if (!r.counted) r.snapshot_qty = r.system_qty;
							r.counted = true;
							r.counted_qty = e.counted_qty;
							r.variance = r.counted_qty - r.snapshot_qty;
							r.value_variance = r.variance * r.rate;
						}
					}
					for (const f of ["reason_code", "reason", "recount"]) if (f in e) r[f] = e[f];
					results.push({ key: e.key, ok: true });
					// As api.count_desk._group_rows answers: the lines, with no word on the bin's listing,
					// so every row says expected: false (merge_groups keeps the sheet's flags).
					const answer = Object.assign({}, r, { expected: false, assigned: false, on_order: 0, is_default: false, undecided: false });
					groups.push({ item_code: r.item_code, warehouse: r.warehouse, rows: r.counted ? [answer] : [] });
				}
				return Promise.resolve({ results, groups });
			}
			if (name === "post_count") return Promise.resolve({ message: "Posted.", posted: ["MAT-RECO-1"], drafts: [], skipped: [], count: { status: "Finalized" } });
			if (name === "cancel_session") return Promise.resolve({ status: "Cancelled" });
			if (name === "resolve_scan") {
				if (args.code === "LABEL-W2") return Promise.resolve({ type: "location", warehouse: W2, location_name: "Bin A1-1-2" });
				if (args.code === "LABEL-OUT") return Promise.resolve({ type: "location", warehouse: "Bin Z - SF", location_name: "Bin Z" });
				if (args.code === "0123456789012") return Promise.resolve({ type: "item", item_code: "B", item_name: "ITEM B", uom: "Unit" });
				if (args.code === "0999999999999") return Promise.resolve({ type: "item", item_code: "NEW", item_name: "NEW ITEM", uom: "Unit" });
				return Promise.resolve({ type: "unknown", code: args.code });
			}
			if (name === "update_count") {
				if ("counted_at" in args) {
					const rows = JSON.parse(JSON.stringify(world.rows)).map((r) => Object.assign(r, { as_of_qty: r.counted ? r.snapshot_qty : 6 }));
					return Promise.resolve(sheet_payload(rows, Object.assign({}, o.sheet, { counted_at: args.counted_at || null })));
				}
				return Promise.resolve({ count: { title: args.title } });
			}
			return Promise.resolve({});
		};
		const held = world.hold.get(name);
		if (held) return held.then(run);
		return run();
	};
	world.env = {
		call: (m, a, op) => world.server(m, a, op),
		get_route: () => world.entries[world.index].route.slice(),
		set_route: (route, replace) => {
			if (replace) world.entries[world.index] = { route: route.slice(), state: null };
			else {
				world.entries = world.entries.slice(0, world.index + 1);
				world.entries.push({ route: route.slice(), state: null });
				world.index += 1;
			}
			world.pending_show = true;
		},
		route_options: () => null,
		clear_route_options: () => {},
		history_state: () => world.entries[world.index].state,
		tag_state: (tag) => {
			world.entries[world.index].state = Object.assign({}, world.entries[world.index].state, tag);
		},
		history_back: () => {
			if (world.index > 0) world.index -= 1;
			world.pending_show = true;
		},
		render: (change) => world.renders.push(change || null),
		set_timeout: (fn, ms) => {
			world.timers.push({ fn, ms });
			return world.timers.length;
		},
		clear_timeout: (id) => {
			if (world.timers[id - 1]) world.timers[id - 1].fn = null;
		},
	};
	world.ctl = new Controller(world.env);
	// on_page_show after every route change, as frappe's router does.
	world.route = async () => {
		await settle();
		while (world.pending_show) {
			world.pending_show = false;
			world.ctl.show();
			await settle();
		}
	};
	world.fire_timers = async () => {
		const due = world.timers.filter((t) => t.fn);
		world.timers = world.timers.map(() => ({ fn: null }));
		for (const t of due) t.fn();
		await settle();
	};
	world.saves = () => world.calls.filter((c) => c.method.endsWith("save_counts")).map((c) => JSON.parse(c.args.rows));
	return world;
}

async function open_sheet(options) {
	const world = makeWorld(options);
	world.entries = [{ route: ["count-desk", "INV-CNT-2026-00007"], state: null }];
	world.ctl.show();
	await settle();
	return world;
}

// ------------------------------------------------------------------ logic: routes and numbers

test("routes", () => {
	check("plan", L.parse_route(["count-desk"]), { screen: "plan" });
	check("sheet", L.parse_route(["count-desk", "INV-1"]), { screen: "sheet", count: "INV-1" });
	check("review", L.parse_route(["count-desk", "INV-1", "review"]), { screen: "review", count: "INV-1" });
	check("not ours", L.parse_route(["bin-manager"]), null);
	check("round trip", L.build_route(L.parse_route(["count-desk", "INV-1", "review"])), ["count-desk", "INV-1", "review"]);
	check("plan route", L.build_route({ screen: "plan" }), ["count-desk"]);
});

test("a count cell takes a number or a sum", () => {
	check("plain", L.evaluate("12"), { value: 12, expression: false });
	check("cases", L.evaluate("3x24+5").value, 77);
	check("times sign", L.evaluate("3 × 24").value, 72);
	check("star", L.evaluate("3*24").value, 72);
	check("brackets", L.evaluate("2*(3+4)").value, 14);
	check("divide", L.evaluate("10/4").value, 2.5);
	check("thousands", L.evaluate("1,200").value, 1200);
	check("decimal", L.evaluate(".5").value, 0.5);
	check("minus is a number the check refuses", L.evaluate("-2").value, -2);
	check("empty", L.evaluate("  "), { empty: true });
	check("letters", !!L.evaluate("abc").error, true);
	check("divide by zero", !!L.evaluate("4/0").error, true);
	check("dangling", !!L.evaluate("3+").error, true);
	check("unbalanced", !!L.evaluate("(3+4").error, true);
	check("never evals", !!L.evaluate("alert(1)").error, true);
});

test("the count check matches stock_scan_rules.check_count", () => {
	const r = { whole_number: true, uom: "Unit" };
	check("negative", L.check_count(-1, r), "A count cannot be less than zero.");
	check("whole", L.check_count(2.5, r), "Unit is counted in whole numbers, so 2.5 cannot be used.");
	check("fraction ok in FT", L.check_count(2.5, { whole_number: false, uom: "FT" }), null);
	check("zero ok", L.check_count(0, r), null);
	check("too big", !!L.check_count(100001, r), true);
});

test("a scan into a count cell is a scan, not a quantity", () => {
	for (const [text, want] of [
		["12", false],
		["3x24", false],
		["3 * 24 + 5", false],
		["1234567", false],
		["12345678", true],
		["012345678905", true],
		["PDT-0042", true],
		["806-020A", true],
		["https://erp.example.com/stock-scan?w=Bin%20A", true],
		["www.example.com", true],
		["", false],
	]) {
		check(`looks_like_scan(${JSON.stringify(text)})`, L.looks_like_scan(text), want);
	}
});

test("formatting", () => {
	check("plain", L.plain(2.50000001), "2.5");
	check("signed +", L.signed(3), "+3");
	check("signed −", L.signed(-2), "−2");
	check("signed 0", L.signed(0), "0");
	check("money", L.money(-1234.5), "−$1,234.50");
	check("money signed", L.money(4.256, { signed: true }), "+$4.26");
	check("money whole", L.money(999.6, { whole: true }), "$1,000");
	check("percent", L.percent(33.333), "33.3%");
	check("percent none", L.percent(null), "—");
	check("age", [L.age(null), L.age(0), L.age(1), L.age(12), L.age(90), L.age(800)], ["never", "today", "yesterday", "12 days ago", "3 months ago", "2 years ago"]);
});

test("the row key agrees with count_desk_rules.line_key", () => {
	for (const v of VECTORS.keys) {
		check(`key ${v.item_code}`, L.line_key(v.item_code, v.warehouse, v.storage_location), v.key);
		check(`split ${v.item_code}`, L.split_key(v.key), { warehouse: v.warehouse, storage_location: v.storage_location, item_code: v.item_code });
	}
	check("bad key", L.split_key("nope"), null);
});

test("the summary agrees with count_desk_rules.summary", () => {
	for (const v of VECTORS.summaries) check(v.name, L.summary(v.rows), v.summary);
});

test("natural order", () => {
	const names = ["Bin B1-2-10", "Bin B1-2-9", "Bin B1-10-1", "Bin B1-2-1"];
	check("shelf order", names.slice().sort(L.natural_compare), ["Bin B1-2-1", "Bin B1-2-9", "Bin B1-2-10", "Bin B1-10-1"]);
});

// ------------------------------------------------------------------ logic: the tree

test("the tree rolls up bins, items, value and what is due", () => {
	const tree = L.build_tree(BOOT.groups, BOOT.bins, 90);
	check("roots", tree.roots.map((n) => n.id), ["All Warehouses - SF"]);
	const all = tree.by_id.get("All Warehouses - SF");
	check("children in natural order, empty group dropped", all.children.map((n) => n.id), ["Row A - SF", "Stores - SF"]);
	const shelf = tree.by_id.get("Shelf A1-1 - SF");
	check("bins in shelf order", shelf.children.map((n) => n.label), ["Bin A1-1-1", "Bin A1-1-2", "Bin A1-1-10"]);
	check("bins", all.bins.length, 4);
	check("items", all.items, 6);
	check("value", all.value, 650);
	check("stale: never and 120 days, not 5 days, not the empty Stores", shelf.stale.sort(), [W1, W2].sort());
	check("never", shelf.never, [W1]);
	check("holding", all.holding, 3);
	check("depth", tree.by_id.get(W1).depth, 3);
	check("no empty group", tree.by_id.has("Row Empty - SF"), false);
});

test("the tree filter keeps ancestors and descendants", () => {
	const tree = L.build_tree(BOOT.groups, BOOT.bins, 90);
	const m = L.tree_matches(tree, "a1-1-2");
	check("match and ancestors", Array.from(m).sort(), ["All Warehouses - SF", "Row A - SF", "Shelf A1-1 - SF", W2].sort());
	const g = L.tree_matches(tree, "shelf");
	check("a group brings its bins", g.has("Bin A1-1-10 - SF"), true);
	check("no filter", L.tree_matches(tree, " "), null);
	const rows = L.tree_rows(tree, new Set(["All Warehouses - SF"]), null);
	check("collapsed rows", rows.map((n) => n.id), ["All Warehouses - SF", "Row A - SF", "Stores - SF"]);
});

test("ticking a node, its state and what start_count is sent", () => {
	const tree = L.build_tree(BOOT.groups, BOOT.bins, 90);
	const shelf = tree.by_id.get("Shelf A1-1 - SF");
	let sel = new Set([W1]);
	check("some", L.node_state(shelf, sel), "some");
	sel = L.toggle_node(shelf, sel);
	check("some -> all", L.node_state(shelf, sel), "all");
	check("a whole group is sent as the group", L.selection_for_start(tree, sel), ["Shelf A1-1 - SF"]);
	sel = L.toggle_node(shelf, sel);
	check("all -> none", sel.size, 0);
	sel = new Set([W1, "Stores - SF"]);
	check("a part is sent as bins", L.selection_for_start(tree, sel), [W1, "Stores - SF"]);
	check("totals", L.selection_totals(tree, new Set([W1, W2])), { bins: 2, items: 5, value: 600 });
	check("overlaps", L.overlaps(BOOT.open_counts, new Set([W1, W2])).map((o) => [o.count.name, o.shared]), [["INV-CNT-2026-00001", 1]]);
});

test("presets", () => {
	check("never", L.preset(BOOT.bins, "never", 90), [W1]);
	check("stale", L.preset(BOOT.bins, "stale", 90), [W1, W2]);
	check("most valuable stale", L.preset(BOOT.bins, "value", 90, 1), [W2]);
	check("unknown", L.preset(BOOT.bins, "x", 90), []);
});

// ------------------------------------------------------------------ logic: the sheet

test("filters and the search", () => {
	const rows = [counted("A", W1, 10, 10), counted("B", W1, 8, 10, { recount: true, moved: -1 }), row("C", W2), row("D", W2, { expected: false }), row("E", W2, { refusal: "No." })];
	const keys = (view) => L.visible_keys(rows, view).map((k) => L.split_key(k).item_code);
	check("all", keys({ filter: "all" }), ["A", "B", "C", "D", "E"]);
	check("to count skips refused", keys({ filter: "todo" }), ["C", "D"]);
	check("variance", keys({ filter: "variance" }), ["B"]);
	check("recount", keys({ filter: "recount" }), ["B"]);
	check("moved", keys({ filter: "moved" }), ["B"]);
	check("not expected", keys({ filter: "added" }), ["D"]);
	check("bin", keys({ bin: W2 }), ["C", "D", "E"]);
	check("query, every word", keys({ query: "item  c" }), ["C"]);
});

test("Enter goes to the next row; under To count, the next uncounted", () => {
	const rows = [counted("A", W1, 1, 1), row("B"), counted("C", W1, 1, 1), row("D", W1, { refusal: "No." }), row("E")];
	const by = new Map(rows.map((r) => [r.key, r]));
	const keys = rows.map((r) => r.key);
	check("next", L.next_key(keys, by, K("A"), 1, false), K("B"));
	check("prev", L.next_key(keys, by, K("B"), -1, false), K("A"));
	check("last", L.next_key(keys, by, K("E"), 1, false), null);
	check("todo skips counted and refused", L.next_key(keys, by, K("B"), 1, true), K("E"));
	check("todo wraps", L.next_key(keys, by, K("E"), 1, true), K("B"));
	check("todo backwards", L.next_key(keys, by, K("E"), -1, true), K("B"));
	check("todo none left", L.next_key([K("A")], by, K("A"), 1, true), null);
	check("from nowhere", L.next_key(keys, by, "x", 1, false), K("A"));
});

test("what the page shows before the server answers", () => {
	let rows = [row("A"), row("B")];
	rows = L.optimistic(rows, K("A"), { counted_qty: 7 });
	const a = rows.find((r) => r.key === K("A"));
	check("counted against the system now", [a.counted, a.counted_qty, a.snapshot_qty, a.variance, a.value_variance], [true, 7, 10, -3, -6]);
	rows = L.optimistic(rows, K("A"), { counted_qty: 10 });
	check("a match", rows.find((r) => r.key === K("A")).variance, 0);
	rows = L.optimistic(rows, K("A"), { counted_qty: null });
	check("cleared", rows.find((r) => r.key === K("A")).counted, false);
	// Two storage locations of one item: the variance is the item's.
	const l1 = counted("A", W1, 3, 10, { key: L.line_key("A", W1, "L1"), storage_location: "L1" });
	const l2 = counted("A", W1, 4, 10, { key: L.line_key("A", W1, "L2"), storage_location: "L2" });
	const both = L.optimistic([l1, l2], l2.key, { counted_qty: 5 });
	check("group variance", both.map((r) => r.variance), [-2, -2]);
});

test("a save's answer merged in", () => {
	const rows = [row("A"), row("B"), row("Z", W1, { expected: false })];
	const fresh = counted("B", W1, 4, 10);
	let out = L.merge_groups(rows, [{ item_code: "B", warehouse: W1, rows: [fresh] }]);
	check("replaced in place", out.rows.map((r) => [r.item_code, r.counted]), [["A", false], ["B", true], ["Z", false]]);
	out = L.merge_groups(out.rows, [{ item_code: "B", warehouse: W1, rows: [] }]);
	check("cleared and expected: back to uncounted", out.rows.map((r) => [r.item_code, r.counted]), [["A", false], ["B", false], ["Z", false]]);
	out = L.merge_groups(out.rows, [{ item_code: "Z", warehouse: W1, rows: [] }]);
	check("cleared and not expected: gone", out.rows.map((r) => r.item_code), ["A", "B"]);
	const legacy = counted("A", W1, 2, 2, { key: L.line_key("A", W1, "L1"), storage_location: "L1" });
	out = L.merge_groups([row("A")], [{ item_code: "A", warehouse: W1, rows: [legacy] }]);
	check("renamed", Array.from(out.renamed.entries()), [[K("A"), legacy.key]]);
});

test("an added row lands after its bin", () => {
	const rows = [row("A"), row("C", W2)];
	const added = L.added_row(rows, { item_code: "N", item_name: "NEW", stock_uom: "FT", on_hand_here: 2 }, W1, "Bin A1-1-1");
	check("not expected, uncounted", [added.expected, added.counted, added.system_qty, added.uom], [false, false, 2, "FT"]);
	check("after its bin", L.insert_row(rows, added).map((r) => r.item_code), ["A", "N", "C"]);
	check("already on the sheet", L.added_row(rows, { item_code: "A" }, W1), null);
	const other = L.added_row(rows, { item_code: "Q" }, "Bin A1-1-3 - SF", "Bin A1-1-3");
	check("a new bin in shelf order", L.insert_row(rows, other).map((r) => r.item_code), ["A", "C", "Q"]);
});

test("a pasted column fills down, one value per row on screen", () => {
	check("one value is a normal paste", L.parse_paste("12"), []);
	check("lines, tabs, CRLF, trailing blank", L.parse_paste("1\t a\r\n2\n\n3\n\n"), ["1", "2", "", "3"]);
	const rows = [row("A"), row("B", W1, { refusal: "No." }), row("C"), row("D"), row("E")];
	const by = new Map(rows.map((r) => [r.key, r]));
	const plan = L.fill_plan(rows.map((r) => r.key), by, K("A"), ["5", "2x3", "", "oops", "9", "10"]);
	check("the refused row took 2x3 and kept nothing, C's blank kept its row, extras dropped", plan.entries, [
		{ key: K("A"), counted_qty: 5 },
		{ key: K("E"), counted_qty: 9 },
	]);
	check("problems named", plan.problems.map((p) => L.split_key(p.key).item_code), ["D"]);
	check("refused said", plan.refused, [{ key: K("B"), message: "No." }]);
	check("zero the rest", L.zero_plan([counted("A", W1, 1, 1), row("B"), row("C", W2), row("D", W1, { refusal: "No." })], W1), [{ key: K("B"), counted_qty: 0 }]);
});

test("the paste agrees with count_desk_vectors.json (held to the rule by count_desk_rules' side too)", () => {
	for (const v of VECTORS.pastes) {
		const rows = v.rows.map((r) => row(r.item_code, W1, { refusal: r.refusal || null }));
		const by = new Map(rows.map((r) => [r.key, r]));
		const plan = L.fill_plan(rows.map((r) => r.key), by, K(v.start), v.values);
		const item = (k) => L.split_key(k).item_code;
		check(`${v.name}: entries`, plan.entries.map((e) => [item(e.key), e.counted_qty]), v.entries);
		check(`${v.name}: problems`, plan.problems.map((p) => item(p.key)), v.problems);
		check(`${v.name}: refused`, plan.refused.map((p) => item(p.key)), v.refused);
	}
});

test("Counted At: an uncounted row is counted against the ledger then", () => {
	check("system_of", [L.system_of(row("A")), L.system_of(row("A", W1, { as_of_qty: 7 })), L.system_of(counted("A", W1, 3, 5, { as_of_qty: 7 }))], [10, 7, 5]);
	const rows = L.optimistic([row("A", W1, { as_of_qty: 7 })], K("A"), { counted_qty: 7 });
	check("a match against 9:00, not against now", [rows[0].snapshot_qty, rows[0].variance], [7, 0]);
	const back = L.uncounted(rows[0]);
	check("cleared, it keeps its baseline", back.as_of_qty, 7);
	check("CSV shows the baseline", L.to_csv([row("A", W1, { as_of_qty: 7 })]).split("\r\n")[1], "Bin A1-1-1,,A,ITEM A,Unit,7,,,,,,,");
});

test("a refused row goes back to what the server has", () => {
	const typed = counted("A", W1, 3, 10);
	let out = L.revert([typed, row("B")], K("A"), []);
	check("never saved: uncounted again", out.rows.map((r) => [r.item_code, r.counted]), [["A", false], ["B", false]]);
	const added = Object.assign(counted("N", W1, 2, 0), { expected: false });
	out = L.revert([added], K("N"), []);
	check("an added row stays, uncounted", out.rows.map((r) => [r.item_code, r.counted, r.expected]), [["N", false, false]]);
	out = L.revert([counted("A", W1, 9, 10)], K("A"), [counted("A", W1, 5, 10)]);
	check("saved before: the saved count", out.rows.map((r) => r.counted_qty), [5]);
});

test("a save that did not land holds the post back", () => {
	const rows = [counted("A", W1, 9, 10)];
	check("clean", L.post_check(rows, false, { errors: 0, fatal: null }).blocked, false);
	check("a refused row", L.post_check(rows, false, { errors: 1 }).blocked, true);
	const stuck = L.post_check(rows, false, { fatal: "Not saved: gone." });
	check("a save refused whole", [stuck.blocked, stuck.stuck], [true, "Not saved: gone."]);
	check("nothing counted", L.post_check([row("A")], false).blocked, true);
	check("a reason missing", L.post_check(rows, true).blocked, true);
});

test("the queue merges per row", () => {
	let q = new Map();
	q = L.queue(q, { key: "a", counted_qty: 1 });
	q = L.queue(q, { key: "b", counted_qty: 2 });
	q = L.queue(q, { key: "a", counted_qty: 3, reason_code: "Other" });
	check("merged", Array.from(q.values()), [{ key: "a", counted_qty: 3, reason_code: "Other" }, { key: "b", counted_qty: 2 }]);
	const { batch, rest } = L.take(q, 1);
	check("take", [batch.length, rest.size], [1, 1]);
	check("before (uncounted)", L.before(row("A"), { counted_qty: 4 }), { key: K("A"), counted_qty: null });
	check("before (counted)", L.before(counted("A", W1, 3, 3), { counted_qty: 4, recount: 1 }), { key: K("A"), counted_qty: 3, recount: false });
	check("tone", [L.tone(row("A")), L.tone(counted("A", W1, 3, 3)), L.tone(counted("A", W1, 2, 3)), L.tone(counted("A", W1, 4, 3)), L.tone(row("A", W1, { refusal: "x" }))], [
		"todo",
		"match",
		"short",
		"over",
		"refused",
	]);
});

// ------------------------------------------------------------------ logic: the review and export

test("the review is by value, and posts the variance on what is on hand now", () => {
	const rows = [
		counted("A", W1, 9, 10, { value_variance: -1, system_qty: 8 }),
		counted("B", W1, 0, 5, { value_variance: -50, system_qty: 3 }),
		counted("C", W1, 12, 10, { value_variance: 4, system_qty: 10 }),
		counted("D", W1, 5, 5, { value_variance: 0 }),
	];
	const review = L.review_rows(rows);
	check("largest value first, matches left out", review.map((g) => g.item_code), ["B", "C", "A"]);
	check("will post", review.map((g) => [g.item_code, g.will_post, g.clamped]), [["B", 0, true], ["C", 12, false], ["A", 7, false]]);
	const pc = L.post_check(rows.concat([row("E"), counted("F", W1, 1, 2, { recount: true })]), true);
	check("needs a reason", pc.needs_reason.map((g) => g.item_code).sort(), ["A", "B", "C", "F"]);
	check("recount and uncounted", [pc.recount, pc.uncounted, pc.nothing], [1, 1, false]);
	check("reason satisfies", L.post_check([counted("A", W1, 1, 2, { reason_code: "Other" })], true).needs_reason, []);
});

test("CSV is formula-safe, and a blind count leaves the system out", () => {
	const rows = [counted("=CMD()", W1, 3, 5, { item_name: 'PIPE, 2" PVC', reason: "-oops" }), row("B")];
	const csv = L.to_csv(rows);
	const lines = csv.trim().split("\r\n");
	check("header", lines[0], "Location,Storage Location,Item Code,Item Name,Unit,System Qty,Counted Qty,Variance,Variance Value,Reason,Note,Recount,Counted At");
	check("a formula is text, a quote is doubled, a minus number is a number", lines[1], `Bin A1-1-1,,'=CMD(),"ITEM =CMD()",Unit,5,3,-2,-4,,'-oops,,`.replace('"ITEM =CMD()"', '"PIPE, 2"" PVC"'));
	check("uncounted", lines[2], "Bin A1-1-1,,B,ITEM B,Unit,10,,,,,,,");
	const blind = L.to_csv(rows, { blind: true }).split("\r\n")[0];
	check("blind header", blind, "Location,Storage Location,Item Code,Item Name,Unit,Counted Qty,Reason,Note,Recount,Counted At");
});

test("the print sheet escapes, and leaves out what it is told to", () => {
	const count = { name: "INV-1", title: "<b>Shelf</b>", scope: [{ warehouse: W1, warehouse_name: "Bin A1-1-1", trail: "Row A" }] };
	const rows = [counted("A", W1, 3, 5, { item_name: "<script>x</script>" }), row("B", W1, { refusal: "No." })];
	const html = L.print_html(count, rows, { blank: false });
	check("escaped", html.includes("<script>x</script>"), false);
	check("title escaped", html.includes("&lt;b&gt;Shelf&lt;/b&gt;"), true);
	check("trail", html.includes("Row A"), true);
	check("counts shown", html.includes('<td class="box">3</td>'), true);
	check("refused uncounted left off", html.includes("ITEM B"), false);
	const blank = L.print_html(count, rows, { blank: true, blind: true });
	check("blank", blank.includes('<td class="box"></td>'), true);
	check("blind", blank.includes("System"), false);
});

// ------------------------------------------------------------------ the controller

test("the plan loads the tree, and Start opens the sheet as its own entry", async () => {
	const world = makeWorld();
	world.ctl.show();
	await settle();
	const boot = world.calls.find((c) => c.method.endsWith(".boot"));
	check("boot is a GET", boot.opts.method, "GET");
	check("opened down to the shelves", Array.from(world.ctl.state.expanded).sort(), ["All Warehouses - SF", "Row A - SF"]);
	world.ctl.toggle_node("Shelf A1-1 - SF");
	check("selected", world.ctl.state.selected.size, 3);
	await world.ctl.start("", true);
	await world.route();
	const start = world.calls.find((c) => c.method.endsWith("start_count"));
	check("sent the group", JSON.parse(start.args.warehouses), ["Shelf A1-1 - SF"]);
	check("blind", start.args.blind, 1);
	check("one new entry", [world.entries.length, world.index], [2, 1]);
	check("on the sheet", world.ctl.state.target, { screen: "sheet", count: "INV-CNT-2026-00007" });
	check("sheet loaded with GET", world.calls.find((c) => c.method.endsWith("get_sheet")).opts.method, "GET");
	check("rows", world.ctl.state.rows.length, 3);
	world.ctl.to_plan();
	await world.route();
	check("Counts steps back, no new entry", [world.entries.length, world.index, world.ctl.state.target.screen], [2, 0, "plan"]);
	check("the plan reloads after a start", world.calls.filter((c) => c.method.endsWith(".boot")).length, 2);
});

test("overlaps are said on the sheet", async () => {
	const world = makeWorld({ overlaps: [{ name: "INV-CNT-2026-00001", title: "Shelf A1-1", counted_by_name: "Sam", bins: 1 }] });
	world.ctl.show();
	await settle();
	world.ctl.toggle_node(W2);
	await world.ctl.start("Mine", false);
	await world.route();
	check("notice", (world.ctl.state.notice || {}).message, "1 of these locations are also in Shelf A1-1, open by Sam.");
});

test("review and back are entries of their own", async () => {
	const world = await open_sheet();
	world.ctl.open_review();
	await world.route();
	check("review", world.ctl.state.target.screen, "review");
	check("no reload of the sheet", world.calls.filter((c) => c.method.endsWith("get_sheet")).length, 1);
	world.ctl.back_to_sheet();
	await world.route();
	check("back", [world.ctl.state.target.screen, world.index, world.entries.length], ["sheet", 0, 2]);
});

test("one save in flight, edits made meanwhile merged into the next", async () => {
	const world = await open_sheet();
	let release;
	world.hold.set("save_counts", new Promise((r) => (release = r)));
	world.ctl.set_count(K("A"), 8);
	await settle();
	check("first save sent", world.saves().length, 1);
	world.ctl.set_count(K("B"), 3);
	world.ctl.set_count(K("B"), 4);
	world.ctl.change(K("A"), { reason_code: "Other" });
	await settle();
	check("still one in flight", world.saves().length, 1);
	check("shown at once", world.ctl.row(K("B")).counted_qty, 4);
	world.hold.delete("save_counts");
	release();
	await settle();
	check("then one batch, the last value of each", world.saves()[1], [{ key: K("B"), counted_qty: 4 }, { key: K("A"), reason_code: "Other" }]);
	check("saved", [world.ctl.unsaved(), world.ctl.state.status.size], [0, 0]);
	check("the server's rows", world.ctl.row(K("A")).reason_code, "Other");
	check("a patch render, not a redraw", world.renders.some((r) => r && Array.isArray(r.rows) && r.rows.includes(K("B"))), true);
});

test("an unchanged value sends nothing", async () => {
	const world = await open_sheet();
	world.ctl.set_count(K("A"), 8);
	await settle();
	world.ctl.set_count(K("A"), 8);
	world.ctl.set_count(K("C", W2), null);
	await settle();
	check("one save", world.saves().length, 1);
});

test("a dropped connection retries with backoff and never loses a newer edit", async () => {
	const world = await open_sheet();
	world.fail.set("save_counts", [{ status: 0, message: "" }]);
	world.ctl.set_count(K("A"), 8);
	await settle();
	check("retrying", !!(world.ctl.state.save_error && world.ctl.state.save_error.retrying), true);
	check("timer at 2s", world.timers.filter((t) => t.fn).map((t) => t.ms), [2000]);
	world.ctl.set_count(K("A"), 9);
	await settle();
	check("nothing sent while waiting", world.saves().length, 1);
	await world.fire_timers();
	check("retried with the newer value", world.saves()[1], [{ key: K("A"), counted_qty: 9 }]);
	check("landed", [world.ctl.unsaved(), world.ctl.state.save_error], [0, null]);
	check("the server has the newer value", world.rows.find((r) => r.key === K("A")).counted_qty, 9);
});

test("a refusal is said on the row and not retried", async () => {
	const world = await open_sheet({ refuse: (e) => e.key === K("B") });
	world.ctl.set_count(K("A"), 8);
	world.ctl.set_count(K("B"), 3);
	await settle();
	await settle();
	check("A saved, B refused", [world.ctl.state.status.has(K("A")), (world.ctl.state.status.get(K("B")) || {}).message], [false, "Not saved: No."]);
	check("B sent once, no retry", [world.saves().flat().filter((e) => e.key === K("B")).length, world.timers.filter((t) => t.fn).length], [1, 0]);
	const w2 = await open_sheet();
	w2.fail.set("save_counts", [{ status: 417, message: "Count INV is Finalized; it can no longer be changed." }]);
	w2.ctl.set_count(K("A"), 8);
	await settle();
	check("whole save refused: fatal, no timer", [!!w2.ctl.state.save_error.fatal, w2.timers.filter((t) => t.fn).length], [true, 0]);
	check("row says why", w2.ctl.state.status.get(K("A")).message, "Not saved: Count INV is Finalized; it can no longer be changed.");
});

test("a refused row is put back and holds Post until it is dealt with", async () => {
	// The review's repro: B refused on its own, the queue empty, unsaved() 0; B's 3 must not post.
	const world = await open_sheet({ refuse: (e) => e.key === K("B"), rows: [row("A"), counted("B", W1, 5, 10), row("C", W2)] });
	world.ctl.set_count(K("A"), 8);
	world.ctl.set_count(K("B"), 3);
	await settle();
	await settle();
	check("queue empty", world.ctl.unsaved(), 0);
	check("B shows what is saved, marked", [world.ctl.row(K("B")).counted_qty, world.ctl.errors()], [5, 1]);
	check("the review leaves the typed 3 out", L.review_rows(world.ctl.state.rows).find((g) => g.item_code === "B").counted_total, 5);
	check("held back", L.post_check(world.ctl.state.rows, false, world.ctl.save_state()).blocked, true);
	let message = "";
	await world.ctl.post("post").catch((e) => (message = e.message));
	check("refused in words", message, "1 row(s) were not saved and show what the server has. Count them again, or clear the red marks, then post.");
	check("no post", world.calls.some((c) => c.method.endsWith("post_count")), false);
	check("clearing the marks", world.ctl.clear_errors(), 1);
	await world.ctl.post("post");
	check("then it posts", world.calls.some((c) => c.method.endsWith("post_count")), true);
	// A row never saved goes back to uncounted; another row saving leaves its mark; counting it again
	// (accepted this time) clears it.
	let refusing = true;
	const w2 = await open_sheet({ refuse: (e) => refusing && e.key === K("A") });
	w2.ctl.set_count(K("A"), 4);
	await settle();
	check("never saved: uncounted, marked", [w2.ctl.row(K("A")).counted, w2.ctl.errors()], [false, 1]);
	w2.ctl.set_count(K("C", W2), 2);
	await settle();
	check("another row saves meanwhile, the mark stays", w2.ctl.errors(), 1);
	refusing = false;
	w2.ctl.set_count(K("A"), 4);
	await settle();
	check("counted again: saved, unmarked", [w2.ctl.row(K("A")).counted_qty, w2.ctl.errors()], [4, 0]);
});

test("a save refused whole holds Post until the page is reloaded", async () => {
	const world = await open_sheet();
	world.fail.set("save_counts", [{ status: 403, message: "No access." }]);
	world.ctl.set_count(K("A"), 8);
	await settle();
	check("dropped from the queue", world.ctl.unsaved(), 0);
	check("what was typed stays, marked", [world.ctl.row(K("A")).counted_qty, world.ctl.errors()], [8, 1]);
	check("the marks do not clear", world.ctl.clear_errors(), 0);
	let message = "";
	await world.ctl.post("post").catch((e) => (message = e.message));
	check("refused", message, "Some counts are not saved yet. Fix the rows marked in red, or retry, then post.");
	check("the review says why", L.post_check(world.ctl.state.rows, false, world.ctl.save_state()).stuck, "Not saved: No access. Reload the page to see the count as it is saved.");
	check("no post", world.calls.some((c) => c.method.endsWith("post_count")), false);
});

test("a pasted column with a refused row lands every value on its own row", async () => {
	const world = await open_sheet({ rows: [row("A"), row("R", W1, { refusal: "Serialized." }), row("B"), row("C")] });
	const res = world.ctl.fill(K("A"), ["5", "", "7", "9"]);
	await settle();
	check("one save, each on its row", world.saves()[0], [
		{ key: K("A"), counted_qty: 5 },
		{ key: K("B"), counted_qty: 7 },
		{ key: K("C"), counted_qty: 9 },
	]);
	check("said", [res.done, res.problems.length, res.refused.length], [3, 0, 0]);
	const r2 = world.ctl.fill(K("A"), ["1", "2"]);
	check("a value on the refused row is said, not saved", [r2.refused.map((p) => p.key), world.ctl.state.status.has(K("R"))], [[K("R")], false]);
});

test("a saved row keeps its bin's listing; undone, it is back, and a paste lands on every row", async () => {
	const world = await open_sheet({ rows: [row("A"), row("B", W1, { assigned: true, on_order: 2 }), row("C"), row("D")] });
	world.ctl.set_count(K("B"), 3);
	await settle();
	const b = world.ctl.row(K("B"));
	check("still expected, flags kept", [b.expected, b.assigned, b.on_order], [true, true, 2]);
	world.ctl.undo();
	await settle();
	check("undone: back on the sheet, uncounted", [world.ctl.row(K("B")) && world.ctl.row(K("B")).counted, world.ctl.state.rows.length], [false, 4]);
	world.ctl.fill(K("A"), ["1", "2", "3"]);
	await settle();
	check("one value per row", world.saves().pop(), [
		{ key: K("A"), counted_qty: 1 },
		{ key: K("B"), counted_qty: 2 },
		{ key: K("C"), counted_qty: 3 },
	]);
	// A row that was not expected does go when cleared, and leaves no key behind for a paste.
	const key = world.ctl.add_row({ item_code: "N", item_name: "NEW", stock_uom: "Unit" }, W1);
	world.rows.splice(4, 0, L.added_row([], { item_code: "N", item_name: "NEW", stock_uom: "Unit" }, W1));
	world.ctl.set_count(key, 5);
	await settle();
	world.ctl.set_count(key, null);
	await settle();
	check("gone from the rows and the grid's keys", [world.ctl.row(key), world.ctl.state.visible.includes(key)], [null, false]);
});

test("a save refused whole stays refused after a later save lands", async () => {
	const world = await open_sheet();
	world.fail.set("save_counts", [{ status: 403, message: "No access." }]);
	world.ctl.set_count(K("A"), 8);
	await settle();
	world.ctl.set_count(K("B"), 3);
	await settle();
	check("the later save landed", [world.ctl.state.save_error, world.ctl.row(K("B")).counted_qty], [null, 3]);
	check("A still is not on the server", world.rows.find((r) => r.key === K("A")).counted, false);
	check("still stuck, marks stay", [world.ctl.save_state().fatal, world.ctl.clear_errors(), world.ctl.errors()], ["Not saved: No access. Reload the page to see the count as it is saved.", 0, 1]);
	let message = "";
	await world.ctl.post("post").catch((e) => (message = e.message));
	check("Post refused", [message.startsWith("Some counts are not saved yet."), world.calls.some((c) => c.method.endsWith("post_count"))], [true, false]);
	await world.ctl.load_sheet();
	check("the sheet loaded again: the server's rows, unstuck", [world.ctl.save_state().fatal, world.ctl.row(K("A")).counted], [null, false]);
});

test("a 429 is retried, not refused", async () => {
	const world = await open_sheet();
	world.fail.set("save_counts", [{ status: 429, message: "Too many requests." }]);
	world.ctl.set_count(K("A"), 8);
	await settle();
	check("retrying", [!!(world.ctl.state.save_error && world.ctl.state.save_error.retrying), world.ctl.unsaved()], [true, 1]);
	await world.fire_timers();
	check("landed", [world.ctl.unsaved(), world.ctl.errors()], [0, 0]);
});

test("changing Counted At waits for the queue, then takes the server's sheet", async () => {
	const world = await open_sheet();
	let release;
	world.hold.set("save_counts", new Promise((r) => (release = r)));
	world.ctl.set_count(K("A"), 8);
	const changed = world.ctl.update({ counted_at: "2026-10-20 09:00:00" });
	await settle();
	check("not sent while a save is in flight", world.calls.some((c) => c.method.endsWith("update_count")), false);
	world.hold.delete("save_counts");
	release();
	await changed;
	const call = world.calls.find((c) => c.method.endsWith("update_count"));
	check("sent", call.args.counted_at, "2026-10-20 09:00:00");
	check("the header says it", world.ctl.state.sheet.counted_at, "2026-10-20 09:00:00");
	check("the rows are the server's", world.ctl.row(K("B")).as_of_qty, 6);
});

test("Undo puts the previous value back", async () => {
	const world = await open_sheet();
	world.ctl.set_count(K("A"), 8);
	await settle();
	world.ctl.set_count(K("A"), 6);
	await settle();
	world.ctl.undo();
	await settle();
	check("back to 8", world.ctl.row(K("A")).counted_qty, 8);
	world.ctl.undo();
	await settle();
	check("back to uncounted", world.ctl.row(K("A")).counted, false);
	check("the last save cleared it", world.saves().pop(), [{ key: K("A"), counted_qty: null }]);
	check("nothing left to undo", world.ctl.undo(), 0);
});

test("a pasted column and Zero the rest are one save and one undo each", async () => {
	const world = await open_sheet();
	const res = world.ctl.fill(K("A"), ["1", "2", "3"]);
	await settle();
	check("filled", [res.done, world.saves().length, world.saves()[0].length], [3, 1, 3]);
	world.ctl.set_count(K("A"), null);
	await settle();
	const n = world.ctl.zero_rest(W1);
	await settle();
	check("zero only the uncounted in the bin", [n, world.saves().pop()], [1, [{ key: K("A"), counted_qty: 0 }]]);
	world.ctl.undo();
	await settle();
	check("one undo for the zeroing", world.ctl.row(K("A")).counted, false);
});

test("filters stay put while counting, and a scan finds its row", async () => {
	const world = await open_sheet();
	world.ctl.set_view({ filter: "todo" });
	check("todo", world.ctl.state.visible.length, 3);
	world.ctl.set_count(K("A"), 10);
	await settle();
	check("a counted row stays on screen until the filter is applied again", world.ctl.state.visible.length, 3);
	world.ctl.set_view({ filter: "todo" });
	check("reapplied", world.ctl.state.visible.length, 2);
	check("a local item", await world.ctl.resolve("b"), { focus: K("B") });
	check("a bin by name", await world.ctl.resolve("Bin A1-1-2"), { bin: W2 });
	check("a label in scope", await world.ctl.resolve("LABEL-W2"), { bin: W2 });
	check("a label outside", (await world.ctl.resolve("LABEL-OUT")).message, "Bin Z is not part of this count. Add it with Add locations.");
	check("a barcode on the sheet", await world.ctl.resolve("0123456789012"), { focus: K("B") });
	check("a barcode not on the sheet", (await world.ctl.resolve("0999999999999")).add.item_code, "NEW");
	check("unknown", (await world.ctl.resolve("ZZZ")).message, "Nothing on this count or in ERPNext matches ZZZ.");
	world.ctl.set_view({ filter: "variance" });
	world.ctl.find_item("B");
	check("finding a hidden row clears the filter", world.ctl.state.view.filter, "all");
});

test("an added row is counted like any other", async () => {
	const world = await open_sheet();
	const key = world.ctl.add_row({ item_code: "N", item_name: "NEW", stock_uom: "Unit" }, W1);
	check("added after its bin", world.ctl.state.rows.map((r) => r.item_code), ["A", "B", "N", "C"]);
	check("visible", world.ctl.state.visible.includes(key), true);
	world.rows.splice(2, 0, L.added_row([], { item_code: "N", item_name: "NEW", stock_uom: "Unit" }, W1));
	world.ctl.set_count(key, 2);
	await settle();
	check("saved", world.saves()[0], [{ key, counted_qty: 2 }]);
});

test("posting waits for the queue and says the result", async () => {
	const world = await open_sheet();
	let release;
	world.hold.set("save_counts", new Promise((r) => (release = r)));
	world.ctl.set_count(K("A"), 8);
	const posted = world.ctl.post("post");
	await settle();
	check("not posted while a save is in flight", world.calls.some((c) => c.method.endsWith("post_count")), false);
	world.hold.delete("save_counts");
	release();
	await posted;
	await settle();
	const post = world.calls.find((c) => c.method.endsWith("post_count"));
	check("posted after", post.args, { session: "INV-CNT-2026-00007", mode: "post" });
	check("result kept", world.ctl.state.post_result.posted, ["MAT-RECO-1"]);
	check("the plan reloads next time", world.ctl.state.boot_stale, true);
});

test("posting refuses while a save is stuck on a refusal", async () => {
	const world = await open_sheet();
	world.fail.set("save_counts", [{ status: 403, message: "No access." }]);
	world.ctl.set_count(K("A"), 8);
	await settle();
	world.ctl.state.pending.set(K("B"), { key: K("B"), counted_qty: 1 });
	let message = "";
	await world.ctl.post("post").catch((e) => (message = e.message));
	check("refused in words", message, "Some counts are not saved yet. Fix the rows marked in red, or retry, then post.");
	check("no post", world.calls.some((c) => c.method.endsWith("post_count")), false);
});

test("a read-only count takes no edits", async () => {
	const world = await open_sheet({ sheet: { status: "Finalized", editable: false } });
	check("no change", world.ctl.set_count(K("A"), 3), 0);
	await settle();
	check("no save", world.saves().length, 0);
});

test("cancel goes back to the plan in place of the sheet", async () => {
	const world = makeWorld();
	world.ctl.show();
	await settle();
	world.ctl.toggle_node(W1);
	await world.ctl.start("", false);
	await world.route();
	await world.ctl.cancel();
	await world.route();
	check("plan, replacing the sheet entry", [world.ctl.state.target.screen, world.entries.length, world.index], ["plan", 2, 1]);
	check("said", (world.ctl.state.notice || {}).message, "Count INV-CNT-2026-00007 was canceled. Nothing was posted.");
});

test("opening another count starts clean", async () => {
	const world = await open_sheet();
	world.ctl.set_view({ filter: "todo", query: "x" });
	world.ctl.state.undo.push([{ key: K("A"), counted_qty: null }]);
	world.entries.push({ route: ["count-desk", "INV-CNT-2026-00009"], state: null });
	world.index = 1;
	world.ctl.show();
	await settle();
	check("view and undo reset", [world.ctl.state.view.filter, world.ctl.state.undo.length], ["all", 0]);
	check("loaded the other", world.calls.filter((c) => c.method.endsWith("get_sheet")).pop().args.session, "INV-CNT-2026-00009");
});

// ------------------------------------------------------------------ run

for (const t of tests) {
	const before = failures;
	try {
		await t.fn();
	} catch (e) {
		failures += 1;
		console.log(`  FAIL ${t.name}: threw ${e && e.stack ? e.stack : e}`);
	}
	console.log(`${failures === before ? "ok  " : "FAIL"} ${t.name}`);
}
console.log(`\n${checks} checks, ${failures} failed`);
if (failures) process.exit(1);
console.log("All count desk checks passed");
