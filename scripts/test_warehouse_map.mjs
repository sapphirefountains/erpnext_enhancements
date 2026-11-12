#!/usr/bin/env node
/**
 * The Warehouse Map page's pure decisions (v1.815.0) under node:
 * erpnext_enhancements/public/js/warehouse_map/logic.js.
 *
 * What is held:
 *
 *   routes     every screen is its own address (a floor, a floor with one box open, the editor),
 *              a route built from a target parses back to it, and what is being looked for is a
 *              query that blanks never appear in
 *   front view shelf 1 at the top and bin 1 at the left unless the box says otherwise; a bin's
 *              short name is its position on its shelf
 *   editor     a box moved, sized or turned stays inside the drawing; Save sends the named fields
 *              and nothing the editor only uses to draw; the refusals are the server's sentences
 *   the doors  the page's copy of the Bin Manager's tabs is the Bin Manager's own list, and Stock
 *              Scan's link is the page's own address
 *
 * The page script itself (it draws and wires) is checked in a browser; see the module README.
 *
 * Run: node scripts/test_warehouse_map.mjs   (from tests/test_warehouse_map.py)
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.join(HERE, "..", "erpnext_enhancements");
const L = await import(pathToFileURL(path.join(APP, "public/js/warehouse_map/logic.js")).href);
const bin_manager = await import(pathToFileURL(path.join(APP, "public/js/bin_manager/logic.js")).href);
const scan = await import(pathToFileURL(path.join(APP, "public/js/stock_scan/logic.js")).href);
const PAGE_JS = fs.readFileSync(path.join(APP, "inventory_enhancements/page/warehouse_map/warehouse_map.js"), "utf8");
const RULES_PY = fs.readFileSync(path.join(APP, "inventory_enhancements/warehouse_map_rules.py"), "utf8");

let checks = 0;
let failures = 0;

function check(label, actual, expected) {
	checks += 1;
	const a = JSON.stringify(actual);
	const e = JSON.stringify(expected);
	if (a !== e) {
		failures += 1;
		console.log(`  FAIL ${label}\n    expected ${e}\n    actual   ${a}`);
	}
}

function truthy(label, value) {
	check(label, !!value, true);
}

const MAP = { name: "Shop Floor", title: "Shop Floor", width: 1200, height: 800 };
const BAY = "Bay K1 - ZZ";

// ------------------------------------------------------------------ routes

check("not this page's route", L.parse_route(["bin-manager"]), null);
check("the bare page", L.parse_route(["warehouse-map"]), { screen: "floor", map: "", shape: "" });
check("a floor", L.parse_route(["warehouse-map", "Shop Floor"]), { screen: "floor", map: "Shop Floor", shape: "" });
check("a floor with a box open", L.parse_route(["warehouse-map", "Shop Floor", BAY]), { screen: "floor", map: "Shop Floor", shape: BAY });
check("the editor", L.parse_route(["warehouse-map", "Shop Floor", "edit"]), { screen: "edit", map: "Shop Floor", shape: "" });
check(
	"a warehouse with a slash in its name arrives in parts and is put back together",
	L.parse_route(["warehouse-map", "Shop Floor", "Rack 1", "2 - ZZ"]),
	{ screen: "floor", map: "Shop Floor", shape: "Rack 1/2 - ZZ" }
);
check("junk is the bare page", L.parse_route(["warehouse-map", null, undefined]), { screen: "floor", map: "", shape: "" });

for (const target of [
	{ screen: "floor", map: "Shop Floor", shape: "" },
	{ screen: "floor", map: "Shop Floor", shape: BAY },
	{ screen: "edit", map: "Shop Floor", shape: "" },
	{ screen: "floor", map: "", shape: "" },
]) {
	check(`a route built from ${JSON.stringify(target)} parses back to it`, L.parse_route(L.build_route(target)), target);
}
check("no map is the bare page, whatever else is asked", L.build_route({ screen: "edit", map: "" }), ["warehouse-map"]);
truthy("each screen has its own key", new Set([{ map: "A" }, { map: "A", shape: BAY }, { map: "A", screen: "edit" }, { map: "B" }].map(L.route_key)).size === 4);

check("the query's three keys, trimmed", L.read_options({ w: " Bin 1 ", item: "ELBOW", bin: null, other: "x" }), { w: "Bin 1", item: "ELBOW", bin: "" });
check("no options at all", L.read_options(null), { w: "", item: "", bin: "" });
check("blanks are never carried", L.options_for({ item: "", bin: "  " }), {});
check("what a screen keeps", L.options_for({ item: "ELBOW", bin: "Bin 1", w: "never" }), { item: "ELBOW", bin: "Bin 1" });
check("the query in a fixed order", L.query_string({ bin: "Bin K1-2-1 - ZZ", item: "A&B" }), "?item=A%26B&bin=Bin%20K1-2-1%20-%20ZZ");
check("nothing looked for is no query", L.query_string({}), "");
// The Desk's router spells a query it writes itself with encodeURIComponent and decides whether an
// address is new by comparing strings (router.js set_route and push_state, v16.56.0). A space as "+"
// here would not match its "%20", and going to the screen already on show would add a history entry.
const as_router = (options) =>
	"?" +
	Object.entries(options)
		.map(([key, value]) => `${key}=` + encodeURIComponent(String(value)))
		.join("&");
for (const options of [{ item: "A B" }, { item: "PVC+ELBOW 2\"", bin: "Bin K1-2-1 - ZZ" }, { bin: "Nook 1 Left - ZZ" }]) {
	check(`the query for ${JSON.stringify(options)} is the router's own spelling`, L.query_string(options), as_router(L.options_for(options)));
}

check("the first map that is not hidden opens", L.default_map({ maps: [{ name: "Draft", disabled: true }, { name: "Shop Floor" }] }), "Shop Floor");
check("an editor with only hidden maps gets the first", L.default_map({ maps: [{ name: "Draft", disabled: true }] }), "Draft");
check("no maps", L.default_map({ maps: [] }), "");
check("no boot", L.default_map(null), "");

// ------------------------------------------------------------------ words

check("a box's own label wins", L.shape_label({ label: "K1", warehouse_name: "Bay K1" }), "K1");
check("else the warehouse's name", L.shape_label({ label: " ", warehouse_name: "Bay K1", warehouse: BAY }), "Bay K1");
check("else its docname", L.shape_label({ warehouse: BAY }), BAY);

check("a bin's short name is its position on its shelf", L.short_bin_label("Bin K1-2-10", "Shelf K1-2"), "10");
check("a name that does not extend the shelf's code is kept", L.short_bin_label("Nook 1 Left", "Nook Row 1"), "Nook 1 Left");
check("so is one on a row with no code", L.short_bin_label("Red-7", "Red Row"), "Red-7");
check("K1-20 is not on shelf K1-2", L.short_bin_label("Bin K1-20-1", "Shelf K1-2"), "K1-20-1");
check("a row with no name keeps the bin's", L.short_bin_label("Bin J1-3-1", ""), "J1-3-1");
check("a bin named only Bin", L.short_bin_label("Bin", "Shelf"), "Bin");

check("a box's color is its summary's", L.shape_status({ summary: { status: "problem" } }), "problem");
check("a label has none", L.shape_status({ summary: null }), "none");
check("a box whose warehouse is gone", L.shape_status({ missing: true, summary: { status: "stock" } }), "missing");
check("an unknown status is none", L.shape_status({ summary: { status: "purple" } }), "none");

check(
	"what a bay says",
	L.summary_text({ is_group: true, summary: { bins: 36, stock_bins: 30, problem_bins: 2, items: 80, status: "problem" } }),
	"36 bins · 30 with stock · 2 with a problem"
);
check("one bin, no problems", L.summary_text({ is_group: true, summary: { bins: 1, stock_bins: 0, problem_bins: 0 } }), "1 bin · 0 with stock");
check("a group with no bins yet", L.summary_text({ is_group: true, summary: { bins: 0, stock_bins: 0, problem_bins: 0 } }), "0 bins");
check("a room that is one bin", L.summary_text({ is_group: false, summary: { bins: 1, items: 55, status: "stock" } }), "55 items on hand");
check("a deleted warehouse", L.summary_text({ missing: true, missing_reason: "deleted" }), "This warehouse was deleted");
check("a disabled one", L.summary_text({ missing: true, missing_reason: "disabled" }), "This warehouse is disabled");
check("a Transit one", L.summary_text({ missing: true, missing_reason: "transit" }), "This is a Transit warehouse");
check("a reason the page does not know reads as the one that needs doing something", L.summary_text({ missing: true }), "This warehouse was deleted");
check("what the editor puts beside a disabled box's name", L.missing_words({ missing: true, missing_reason: "disabled" }).short, "disabled: not shown on the map until it is enabled");
check("a box that can be drawn has no such words", L.missing_words({ missing: false, missing_reason: "disabled" }), null);
check("a label says nothing", L.summary_text({ summary: null }), "");
check("an empty bin", L.bin_text({}), "Nothing on hand");
check(
	"a bin with both kinds of problem",
	L.bin_text({ on_hand_items: 1, missing_items: 2, unassigned_items: 1 }),
	"1 item on hand · 2 assigned items missing · 1 item not assigned here"
);

// ------------------------------------------------------------------ the front view

const SHAPE = {
	warehouse: BAY,
	is_group: true,
	shelves_from: "Top",
	bins_from: "Left",
	levels: [
		{ warehouse: "Shelf K1-1 - ZZ", label: "Shelf K1-1", bins: [] },
		{ warehouse: "Shelf K1-2 - ZZ", label: "Shelf K1-2", bins: ["Bin K1-2-1 - ZZ", "Bin K1-2-2 - ZZ", "Bin K1-2-10 - ZZ"] },
	],
};
const BINS = {
	"Bin K1-2-1 - ZZ": { warehouse_name: "Bin K1-2-1", status: "stock" },
	"Bin K1-2-2 - ZZ": { warehouse_name: "Bin K1-2-2", status: "problem" },
	// No status: a bin whose counts could not be read is drawn empty.
	"Bin K1-2-10 - ZZ": { warehouse_name: "Bin K1-2-10" },
};

let rows = L.level_rows(SHAPE, BINS, { "Bin K1-2-2 - ZZ": { on_hand: 4 } });
check("shelf 1 at the top", rows.map((r) => r.label), ["Shelf K1-1", "Shelf K1-2"]);
check("an empty shelf is a row with no bins", rows[0].bins, []);
check("bin 1 at the left, each by its position", rows[1].bins.map((b) => b.label), ["1", "2", "10"]);
check("each bin's color; one with no counts is empty", rows[1].bins.map((b) => b.status), ["stock", "problem", "empty"]);
check("the bin the item is at is lit", rows[1].bins.map((b) => b.hit), [false, true, false]);
check(
	"a bin the map's list does not have at all keeps its docname and is drawn empty",
	L.level_rows({ levels: [{ label: "Shelf Z", bins: ["Bin Z-1 - ZZ"] }] }, {}, null)[0].bins[0],
	{ warehouse: "Bin Z-1 - ZZ", name: "Bin Z-1 - ZZ", label: "1 - ZZ", status: "empty", hit: false }
);

rows = L.level_rows(Object.assign({}, SHAPE, { shelves_from: "Bottom", bins_from: "Right" }), BINS, null);
check("shelf 1 at the bottom turns the rows over", rows.map((r) => r.label), ["Shelf K1-2", "Shelf K1-1"]);
check("bin 1 at the right turns each row around", rows[0].bins.map((b) => b.label), ["10", "2", "1"]);
check("the server's order is not disturbed", SHAPE.levels[1].bins[0], "Bin K1-2-1 - ZZ");
check("a box with nothing inside", L.level_rows({ levels: [] }, BINS, null), []);
check("no box", L.level_rows(null, null, null), []);

// ---- a box's own order: the arrows move things the way they point, whatever way the box faces

const NOOKS = {
	warehouse: "Nook Wall - ZZ",
	is_group: true,
	shelves_from: "Top",
	bins_from: "Left",
	levels: [
		{ warehouse: "Nook Wall - ZZ", label: "", bins: ["top"] },
		{ warehouse: "r1", label: "Red Row", bins: ["a", "b", "c"] },
		{ warehouse: "r2", label: "Amber Row", bins: ["x"] },
		{ warehouse: "r3", label: "Teal Row", bins: [] },
	],
};
const row_names = (levels) => levels.map((l) => l.warehouse);
check("a shelf moved down", row_names(L.move_level(NOOKS, 1, 1)), ["Nook Wall - ZZ", "r2", "r1", "r3"]);
check("a shelf moved up", row_names(L.move_level(NOOKS, 3, -1)), ["Nook Wall - ZZ", "r1", "r3", "r2"]);
check("the last shelf cannot move down", L.move_level(NOOKS, 3, 1) === NOOKS.levels, true);
check("nothing moves above the row of bins that have no shelf", L.move_level(NOOKS, 1, -1) === NOOKS.levels, true);
check("and that row does not move", L.move_level(NOOKS, 0, 1) === NOOKS.levels, true);
check("a move does not disturb the box it started from", row_names(NOOKS.levels), ["Nook Wall - ZZ", "r1", "r2", "r3"]);
check("a bin moved right", L.move_bin(NOOKS, 1, "a", 1)[1].bins, ["b", "a", "c"]);
check("a bin moved left", L.move_bin(NOOKS, 1, "c", -1)[1].bins, ["a", "c", "b"]);
check("the first bin cannot move left", L.move_bin(NOOKS, 1, "a", -1) === NOOKS.levels, true);
check("a bin that is not on that shelf", L.move_bin(NOOKS, 2, "a", 1) === NOOKS.levels, true);
check("a row that is not there", L.move_bin(NOOKS, 9, "a", 1) === NOOKS.levels, true);

// Shelf 1 at the bottom and bin 1 at the right: what is shown is the stored order turned over, and
// the arrow still moves the thing the way it points ON SCREEN.
const TURNED = Object.assign({}, NOOKS, { shelves_from: "Bottom", bins_from: "Right" });
check("as shown, the rows are", L.level_rows(TURNED, {}, null).map((r) => r.warehouse), ["r3", "r2", "r1", "Nook Wall - ZZ"]);
let nudged = Object.assign({}, TURNED, { levels: L.move_level(TURNED, 0, 1) });
check("the top row as shown, moved down, is second as shown", L.level_rows(nudged, {}, null).map((r) => r.warehouse), ["r2", "r3", "r1", "Nook Wall - ZZ"]);
check("as shown, r1's bins are", L.level_rows(TURNED, {}, null)[2].bins.map((b) => b.warehouse), ["c", "b", "a"]);
nudged = Object.assign({}, TURNED, { levels: L.move_bin(TURNED, 2, "c", 1) });
check("the left bin as shown, moved right, is second as shown", L.level_rows(nudged, {}, null)[2].bins.map((b) => b.warehouse), ["b", "c", "a"]);
check("in a turned box too, nothing moves past the row with no shelf", L.move_level(TURNED, 2, 1) === TURNED.levels, true);

check("the order that is stored: each shelf, then its bins", L.order_from_levels(NOOKS), ["top", "r1", "a", "b", "c", "r2", "x", "r3"]);
check("a box with nothing inside has no order", L.order_from_levels({ levels: [] }), []);
check("no box", L.order_from_levels(null), []);

check("every bin a bay shows", L.shape_bins(SHAPE), ["Bin K1-2-1 - ZZ", "Bin K1-2-2 - ZZ", "Bin K1-2-10 - ZZ"]);
check("a room is its own bin", L.shape_bins({ warehouse: "Parts Room - ZZ", is_group: false, levels: [] }), ["Parts Room - ZZ"]);
check("a label shows none", L.shape_bins({ levels: [] }), []);

// ------------------------------------------------------------------ an item being looked for

const PLACES = [
	{ warehouse: "Bin K1-2-2 - ZZ", map: "Shop Floor", shape: BAY, on_hand: 4, assigned: false },
	{ warehouse: "Nook 1 Left - ZZ", map: "Upstairs", shape: "Nook Wall - ZZ", on_hand: 0, assigned: true },
	{ warehouse: "Van Shelf - ZZ", map: null, shape: null, on_hand: 9, assigned: false },
	{ warehouse: "Shelf M1-1 - ZZ", map: "Shop Floor", shape: null, on_hand: 1, assigned: false },
];
let lit = L.highlight(PLACES, "Shop Floor");
check("only this map's boxes are lit", Object.keys(lit.shapes), [BAY]);
check("and this map's bins", Object.keys(lit.bins), ["Bin K1-2-2 - ZZ", "Shelf M1-1 - ZZ"]);
lit = L.highlight(PLACES, "Upstairs");
check("another floor lights its own", Object.keys(lit.shapes), ["Nook Wall - ZZ"]);
check("nothing looked for lights nothing", L.highlight(null, "Shop Floor"), { shapes: {}, bins: {} });

check("how many are there", L.place_text(PLACES[0], "Unit"), "4 Unit here");
check("its bin, empty", L.place_text(PLACES[1], "Unit"), "its bin, none on hand");
check("its bin, with stock", L.place_text({ on_hand: 2.5, assigned: true }, ""), "2.5 here · its bin");
check("a float's dust is not shown", L.place_text({ on_hand: 0.1 + 0.2 }, "m"), "0.3 m here");

check("an item in several places", L.item_sentence({ item_name: "ELBOW", is_stock_item: true, places: PLACES }), "ELBOW is in 4 places.");
check("in one", L.item_sentence({ item_code: "E1", is_stock_item: true, places: [PLACES[0]] }), "E1 is in 1 place.");
check("nowhere", L.item_sentence({ item_name: "ELBOW", is_stock_item: true, places: [] }), "ELBOW is not on hand anywhere and has no bin.");
check(
	"only where no map shows",
	L.item_sentence({ item_name: "ELBOW", is_stock_item: true, places: [PLACES[2]] }),
	"ELBOW is in 1 place, none of them on a map yet."
);
check(
	"not a stock item",
	L.item_sentence({ item_name: "Install labor", is_stock_item: false, places: [] }),
	"Install labor is not a stock item, so it has no place in the warehouse."
);

// ------------------------------------------------------------------ the drawing

check("a 1,200-wide drawing is the measure", L.scale_of(MAP), 1);
check("a 4,800-wide one is four times it", L.scale_of({ width: 4800 }), 4);
check("a tiny one has a floor", L.scale_of({ width: 10 }), 0.25);

let lay = L.label_layout({ label: "K1", w: 44, h: 110 }, 1);
check("a tall narrow box has its name turned", lay.vertical, true);
lay = L.label_layout({ label: "Parts Room", w: 150, h: 110 }, 1);
check("a wide box does not", lay.vertical, false);
truthy("a long name on a small box still has a size that can be read", L.label_layout({ label: "x".repeat(60), w: 20, h: 20 }, 1).size >= 7);
truthy("and a short name on a huge box is not huge", L.label_layout({ label: "A", w: 900, h: 600 }, 1).size <= 20);
truthy("the sizes follow the drawing", L.label_layout({ label: "A", w: 3600, h: 2400 }, 4).size <= 80);

const FIT = L.fit_view(MAP);
check("the whole drawing", FIT, { x: 0, y: 0, w: 1200, h: 800 });
check("which is not zoomed", L.is_zoomed(FIT, MAP), false);
let view = L.zoom_view(FIT, 2, null, null, MAP);
check("zooming in about the middle", view, { w: 600, h: 400, x: 300, y: 200 });
check("is zoomed", L.is_zoomed(view, MAP), true);
check("zooming out past the whole drawing stops at it", L.zoom_view(FIT, 0.1, null, null, MAP), { w: 1200, h: 800, x: 0, y: 0 });
check("zooming in has an end", L.zoom_view(FIT, 1000, 0, 0, MAP), { w: 60, h: 40, x: 0, y: 0 });
view = L.zoom_view(FIT, 2, 0, 0, MAP);
check("zooming about a corner keeps the corner", view, { w: 600, h: 400, x: 0, y: 0 });
check("a pan moves the view the other way", L.pan_view(view, -100, -50, MAP), { w: 600, h: 400, x: 100, y: 50 });
check("and never off the drawing", L.pan_view(view, 5000, -5000, MAP), { w: 600, h: 400, x: 0, y: 400 });
check(
	"a point on screen is a point of the drawing",
	L.to_map_point({ x: 300, y: 200, w: 600, h: 400 }, { left: 10, top: 20, width: 300, height: 200 }, 160, 120),
	{ x: 600, y: 400 }
);

// ------------------------------------------------------------------ the editor

check("snap to the grid", [L.snap(12.4, 5), L.snap(12.6, 5), L.snap(12.6, 0)], [10, 15, 12.6]);
check("the grid follows the drawing", [L.grid_of(MAP), L.grid_of({ width: 4800 }), L.grid_of({ width: 100 })], [5, 20, 1]);

const BOX = { shape_type: "Rack", warehouse: BAY, x: 100, y: 100, w: 40, h: 110, rotation: 0, shelves_from: "Top", bins_from: "Left" };
check("a move", [L.move_shape(BOX, 33, -18, MAP, 5).x, L.move_shape(BOX, 33, -18, MAP, 5).y], [135, 80]);
check("a move stops at the drawing's edge", [L.move_shape(BOX, 9999, 9999, MAP, 5).x, L.move_shape(BOX, 9999, 9999, MAP, 5).y], [1160, 690]);
check("and at the other", [L.move_shape(BOX, -9999, -9999, MAP, 5).x, L.move_shape(BOX, -9999, -9999, MAP, 5).y], [0, 0]);
check("a move does not change the box it started from", BOX.x, 100);
check("a move keeps everything else", L.move_shape(BOX, 5, 5, MAP, 5).warehouse, BAY);

check("sizing by the corner", [L.resize_shape(BOX, 22, -31, MAP, 5).w, L.resize_shape(BOX, 22, -31, MAP, 5).h], [60, 80]);
check("never smaller than can be picked up", [L.resize_shape(BOX, -500, -500, MAP, 5).w, L.resize_shape(BOX, -500, -500, MAP, 5).h], [L.MIN_SIZE, L.MIN_SIZE]);
const turned = Object.assign({}, BOX, { rotation: 90 });
check("a box turned 90 is sized along its own sides", [L.resize_shape(turned, -20, 30, MAP, 0).w, L.resize_shape(turned, -20, 30, MAP, 0).h], [70, 130]);

const quarter = L.turn_shape(BOX, MAP);
check("a quarter turn swaps the sides", [quarter.w, quarter.h], [110, 40]);
check("about the box's center", [quarter.x + quarter.w / 2, quarter.y + quarter.h / 2], [120, 155]);
check("two quarter turns are where it began", [L.turn_shape(quarter, MAP).x, L.turn_shape(quarter, MAP).y], [100, 100]);

check("a turn is kept between 0 and 360", [L.hold_shape({ x: 0, y: 0, w: 10, h: 10, rotation: 450 }, MAP).rotation, L.hold_shape({ x: 0, y: 0, w: 10, h: 10, rotation: -90 }, MAP).rotation], [90, 270]);
check("a box with no numbers is given some", L.hold_shape({}, MAP), { w: L.MIN_SIZE, h: L.MIN_SIZE, x: 0, y: 0, rotation: 0 });

let fresh = L.new_shape(null, { warehouse: BAY, warehouse_name: "Bay K1", suggested_type: "Rack" }, MAP, FIT, 0);
check("a new box from the list is tied and typed", [fresh.shape_type, fresh.warehouse, fresh.warehouse_name], ["Rack", BAY, "Bay K1"]);
truthy("it lands in the middle of what is on screen", fresh.x > 500 && fresh.x < 700 && fresh.y > 300 && fresh.y < 400);
const second = L.new_shape(null, { warehouse: "Bay K2 - ZZ", suggested_type: "Rack" }, MAP, FIT, 1);
truthy("the next one does not land on top of it", second.x !== fresh.x && second.y !== fresh.y);
fresh = L.new_shape("Label", null, MAP, FIT, 0);
check("an empty label has words to change", [fresh.shape_type, fresh.label, fresh.warehouse], ["Label", "Label", ""]);
fresh = L.new_shape("Room", null, { width: 4800, height: 3200 }, null, 0);
check("a new box's size follows the drawing", [fresh.w, fresh.h], [600, 440]);
check("an unknown kind is a rack", L.new_shape("Circle", null, MAP, FIT, 0).shape_type, "Rack");

const scaled = L.rescale_shapes([BOX], MAP, { width: 2400, height: 1600 });
check("a picture twice the size keeps each box over its place", [scaled[0].x, scaled[0].y, scaled[0].w, scaled[0].h], [200, 200, 80, 220]);

check("boxes that can be saved", L.check_shapes([BOX, { shape_type: "Label", label: "Dock" }, { shape_type: "Room", label: "Office" }]), null);
const refusals = [
	[[{ shape_type: "Rack", warehouse: "" }], "Box 1: a rack needs the row, bay or shelf it stands for."],
	[[BOX, { shape_type: "Label", label: " " }], "Box 2: a label needs its words."],
	[[{ shape_type: "Room", warehouse: "", label: "" }], "Box 1: a room needs a name or a warehouse."],
	[[BOX, { shape_type: "Label", label: "x" }, Object.assign({}, BOX)], `${BAY} is on this map twice (boxes 1 and 3). Remove one of them.`],
];
for (const [shapes, message] of refusals) {
	const problem = L.check_shapes(shapes);
	check(`refused: ${message}`, problem && problem.message, message);
	// The same sentence the server gives (warehouse_map_rules.py), so the two never read differently.
	const tail = message.replace(/^Box \d+: /, "").replace(BAY, "").replace(/\(boxes \d+ and \d+\)/, "").trim().split(". ")[0].replace(/\.$/, "");
	truthy(`the server says it in the same words: ${tail}`, RULES_PY.includes(tail));
}
check("the box to fix is picked", L.check_shapes([BOX, { shape_type: "Rack" }]).index, 1);
check("a label tied to a warehouse by an earlier kind is not a duplicate", L.check_shapes([BOX, { shape_type: "Label", label: "x", warehouse: BAY }]), null);

const EDIT = {
	map: { name: "Shop Floor", title: " Shop Floor ", sort_order: "2", disabled: false, width: 1200.04, height: 800, background: "/private/files/plan.png", modified: "stamp" },
	shapes: [
		Object.assign({ id: "row1", warehouse_name: "Bay K1", is_group: true, levels: [{ bins: ["x"] }], summary: { status: "stock" }, missing: false, trail: "Lane 7" }, BOX),
		{ shape_type: "Label", label: "Dock door", warehouse: BAY, x: 1, y: 2, w: 3, h: 4 },
	],
};
const payload = L.save_payload(EDIT);
check("Save sends the map's own fields", [payload.map_name, payload.sort_order, payload.disabled, payload.width, payload.background], ["Shop Floor", 2, 0, 1200, "/private/files/plan.png"]);
check(
	"and each box as its stored fields only",
	Object.keys(payload.shapes[0]).sort(),
	["bin_order", "bins_from", "h", "label", "rotation", "shape_type", "shelves_from", "w", "warehouse", "x", "y"]
);
check("a box with no order of its own sends none", payload.shapes[0].bin_order, "");
check(
	"a box's own order is sent one warehouse per line",
	L.save_payload({ map: EDIT.map, shapes: [Object.assign({}, EDIT.shapes[0], { bin_order: ["Red Row - ZZ", " ", "Red-2 - ZZ"] })] }).shapes[0].bin_order,
	"Red Row - ZZ\nRed-2 - ZZ"
);
check(
	"a label has no order to send, whatever it carries",
	L.save_payload({ map: EDIT.map, shapes: [{ shape_type: "Label", label: "x", warehouse: BAY, bin_order: ["a"] }] }).shapes[0].bin_order,
	""
);
check("a label carries no warehouse", payload.shapes[1].warehouse, "");
check("a field the box never had is sent as nothing, not left out", payload.shapes[1].rotation, null);
check("the editor's stamp is not part of the map", "modified" in payload, false);
const saved = JSON.stringify(payload);
check("just saved is not dirty", L.is_dirty(EDIT, saved), false);
const moved = Object.assign({}, EDIT, { shapes: [L.move_shape(EDIT.shapes[0], 5, 0, MAP, 5), EDIT.shapes[1]] });
check("a moved box is", L.is_dirty(moved, saved), true);
const redrawn = Object.assign({}, EDIT, { shapes: [Object.assign({}, EDIT.shapes[0], { summary: { status: "problem" } }), EDIT.shapes[1]] });
check("what the editor only draws with is not", L.is_dirty(redrawn, saved), false);

const UNITS = [
	{ warehouse: BAY, on_map: "Shop Floor", via: [BAY] },
	{ warehouse: "Bay K2 - ZZ", on_map: null, via: [] },
	{ warehouse: "Nook Wall - ZZ", on_map: "Upstairs", via: ["Nook Row 1 - ZZ"] },
	{ warehouse: "Bay M1 - ZZ", on_map: "Shop Floor", via: ["Shelf M1-1 - ZZ"] },
	{ warehouse: "Bay N1 - ZZ", on_map: "Shop Floor", via: ["Lane 9 - ZZ"] },
];
let lists = L.unit_lists(UNITS, [{ warehouse: BAY }, { warehouse: "Shelf M1-1 - ZZ" }, { warehouse: "Lane 9 - ZZ" }], "Shop Floor");
check("to place: what nothing shows", lists.todo.map((u) => u.warehouse), ["Bay K2 - ZZ"]);
check("on another map", lists.elsewhere.map((u) => u.warehouse), ["Nook Wall - ZZ"]);
lists = L.unit_lists(UNITS, [], "Shop Floor");
check("a box removed since the editor opened is to place again", lists.todo.map((u) => u.warehouse), [BAY, "Bay K2 - ZZ", "Bay M1 - ZZ", "Bay N1 - ZZ"]);
lists = L.unit_lists(UNITS, [{ warehouse: "Bay K2 - ZZ" }], "Shop Floor");
check("a box added since is not offered twice", lists.todo.map((u) => u.warehouse).includes("Bay K2 - ZZ"), false);
check("no units", L.unit_lists(null, null, "x"), { todo: [], elsewhere: [] });

const WAREHOUSES = [
	{ name: "Bay K1 - ZZ", warehouse_name: "Bay K1", trail: "Stock › Lane 7" },
	{ name: "Bay K2 - ZZ", warehouse_name: "Bay K2", trail: "Stock › Lane 7" },
	{ name: "Shelf K1-2 - ZZ", warehouse_name: "Shelf K1-2", trail: "Stock › Lane 7 › Bay K1" },
];
check("every word typed has to be there", L.filter_warehouses(WAREHOUSES, "bay lane", 30).map((w) => w.name), ["Bay K1 - ZZ", "Bay K2 - ZZ", "Shelf K1-2 - ZZ"]);
check("by name", L.filter_warehouses(WAREHOUSES, "k1-2", 30).map((w) => w.name), ["Shelf K1-2 - ZZ"]);
check("the list has a limit", L.filter_warehouses(WAREHOUSES, "b", 2).length, 2);
check("nothing typed lists from the top", L.filter_warehouses(WAREHOUSES, "", 30).length, 3);
check("not a list", L.filter_warehouses(null, "b", 30), []);

// ------------------------------------------------------------------ the doors

// The page draws the Bin Manager's tabs so it reads as one more of its views. They are written out
// in the page script; this holds them to the Bin Manager's own list, in its order.
const tabs_block = PAGE_JS.slice(PAGE_JS.indexOf("const WM_TABS = ["), PAGE_JS.indexOf("];", PAGE_JS.indexOf("const WM_TABS = [")));
const page_labels = Array.from(tabs_block.matchAll(/label: "([^"]+)"/g)).map((m) => m[1]);
check("the page's tabs are the Bin Manager's, then Map", page_labels, bin_manager.TABS.map((t) => t.label).concat(["Map"]));
const page_routes = Array.from(tabs_block.matchAll(/route: (\[[^\]]*\]|null)/g)).map((m) => (m[1] === "null" ? null : JSON.parse(m[1])));
check(
	"and each goes to the Bin Manager's own route",
	page_routes,
	bin_manager.TABS.map((t) => bin_manager.build_route({ screen: t.screen })).concat([null])
);
check("the Bin Manager's page is the one the map sends bins to", L.BIN_MANAGER, bin_manager.PAGE);

check("Stock Scan's link to a place", scan.mapUrl({ w: "Bin K1-2-10 - ZZ" }), "/desk/warehouse-map?w=Bin%20K1-2-10%20-%20ZZ");
check("to an item", scan.mapUrl({ item: "A&B/1" }), "/desk/warehouse-map?item=A%26B%2F1");
check("a place wins over an item", scan.mapUrl({ w: "X", item: "Y" }), "/desk/warehouse-map?w=X");
check("nothing to point at is no link", [scan.mapUrl({}), scan.mapUrl(null), scan.mapUrl({ w: "  " })], ["", "", ""]);
truthy("the link's page is this page", scan.mapUrl({ w: "X" }).startsWith(`/desk/${L.PAGE}?`));
// The keys Stock Scan's link writes are the ones this page reads.
check("and its keys are read here", L.read_options({ w: "X", item: "Y" }), { w: "X", item: "Y", bin: "" });

console.log(`${checks - failures} of ${checks} checks passed`);
if (failures) process.exit(1);
