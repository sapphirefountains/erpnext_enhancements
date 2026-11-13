/*
 * Warehouse Map (v1.818.0): the page's pure decisions. No DOM and no frappe;
 * scripts/test_warehouse_map.mjs runs it under node. The page script
 * (inventory_enhancements/page/warehouse_map/warehouse_map.js) draws and wires.
 *
 *   routes     /desk/warehouse-map                    the first map
 *              /desk/warehouse-map/<map>              one floor
 *              /desk/warehouse-map/<map>/<warehouse>  that floor with one box open (its front view)
 *              /desk/warehouse-map/<map>/edit         the editor
 *              each its own history entry. What is found or picked rides in the address's query
 *              (?item=, ?bin=) and ?w=<warehouse> is the "show on map" link from everywhere else.
 *   front view a box's shelves and bins as a person facing it sees them (shelf 1 at the top and
 *              bin 1 at the left unless the box says otherwise)
 *   editor     moving, sizing and turning a box, held inside the drawing; what is sent on Save
 *
 * The numbers, the colors' meaning and the warehouse tree are the server's
 * (api/warehouse_map.py); the words are the page's.
 */

export const PAGE = "warehouse-map";
export const API = "erpnext_enhancements.api.warehouse_map.";
export const SCAN = "erpnext_enhancements.api.stock_scan.";
export const BIN_MANAGER = "bin-manager";
export const SEGMENT_EDIT = "edit";

export const RACK = "Rack";
export const ROOM = "Room";
export const LABEL = "Label";
export const SHAPE_TYPES = [RACK, ROOM, LABEL];

export const MIN_SIZE = 6;
export const MAX_LABEL = 60;

function text(value) {
	return value === undefined || value === null ? "" : String(value).trim();
}

function num(value, fallback) {
	const n = Number(value);
	return Number.isFinite(n) ? n : fallback;
}

function round1(value) {
	return Math.round(num(value, 0) * 10) / 10;
}

// ------------------------------------------------------------------ routes

// The screen a route names, or null when it is not this page's. A warehouse's name ends in its
// company's abbreviation, so it can never be "edit".
export function parse_route(route) {
	const parts = Array.isArray(route) ? route : [];
	if (parts[0] !== PAGE) return null;
	const map = text(parts[1]);
	if (!map) return { screen: "floor", map: "", shape: "" };
	// A warehouse whose name holds a slash arrives as more than one part.
	const rest = parts.slice(2).map(text).filter(Boolean).join("/");
	if (rest === SEGMENT_EDIT) return { screen: "edit", map, shape: "" };
	return { screen: "floor", map, shape: rest };
}

export function build_route(target) {
	const t = target || {};
	const map = text(t.map);
	if (!map) return [PAGE];
	if (t.screen === "edit") return [PAGE, map, SEGMENT_EDIT];
	return text(t.shape) ? [PAGE, map, text(t.shape)] : [PAGE, map];
}

export function route_key(target) {
	return build_route(target).join("/");
}

// What the address's query (frappe.route_options) carries: `w` asks where a warehouse is, `item`
// is the item being looked for, `bin` the bin picked in the open box.
export function read_options(options) {
	const o = options && typeof options === "object" ? options : {};
	return { w: text(o.w), item: text(o.item), bin: text(o.bin) };
}

// The query a screen keeps: blanks left out, so the same screen always writes the same address.
export function options_for(state) {
	const s = state || {};
	const out = {};
	if (text(s.item)) out.item = text(s.item);
	if (text(s.bin)) out.bin = text(s.bin);
	return out;
}

// Spelled exactly as the Desk's router spells a query it writes itself (router.js set_route:
// `key=` + encodeURIComponent(value), read at v16.56.0). The router decides whether an address is
// new by comparing strings, so a query written here with a space as "+" would not match the
// router's "%20", and going to the screen already on show would add a second history entry.
export function query_string(options) {
	const parts = [];
	["item", "bin"].forEach((key) => {
		const value = text((options || {})[key]);
		if (value) parts.push(`${key}=${encodeURIComponent(value)}`);
	});
	return parts.length ? `?${parts.join("&")}` : "";
}

// The map a bare /desk/warehouse-map opens: the first one in the server's order.
export function default_map(boot) {
	const maps = (boot && boot.maps) || [];
	const open = maps.find((m) => !m.disabled) || maps[0];
	return open ? open.name : "";
}

// ------------------------------------------------------------------ words

export function shape_label(shape) {
	const s = shape || {};
	return text(s.label) || text(s.warehouse_name) || text(s.warehouse);
}

// "Bin K1-2-10" on the shelf "Shelf K1-2" is position "10": the part of the bin's code the
// shelf's code does not already say. Anything that does not fit that pattern keeps its own name
// (less a leading "Bin"), so "Nook 1 Left" and "Red-7" read as they are.
export function short_bin_label(bin_name, level_label) {
	const full = text(bin_name);
	const code = full.replace(/^bin\s+/i, "");
	const level = text(level_label).split(/\s+/).pop() || "";
	if (level && code.length > level.length + 1 && code.startsWith(`${level}-`)) {
		return code.slice(level.length + 1);
	}
	return code || full;
}

export const STATUS = {
	stock: { key: "stock", label: "Has stock" },
	empty: { key: "empty", label: "Empty" },
	problem: { key: "problem", label: "Has a problem" },
	none: { key: "none", label: "" },
};

export function shape_status(shape) {
	const s = shape || {};
	if (s.missing) return "missing";
	const status = s.summary && s.summary.status;
	return STATUS[status] ? status : "none";
}

function count_word(n, one, many) {
	return `${n} ${n === 1 ? one : many}`;
}

// Why a box's warehouse is not drawn, in a sentence, and in the few words the editor puts beside
// its name. The server says which (warehouse_map_rules.missing_reason): a deleted warehouse's
// box has to go before the map saves again; a disabled or Transit one's box may stay.
export const MISSING = {
	deleted: { sentence: "This warehouse was deleted", short: "deleted: remove this box or tie it to another warehouse" },
	disabled: { sentence: "This warehouse is disabled", short: "disabled: not shown on the map until it is enabled" },
	transit: { sentence: "This is a Transit warehouse", short: "a Transit warehouse: not shown on the map" },
};

export function missing_words(shape) {
	const s = shape || {};
	if (!s.missing) return null;
	return MISSING[s.missing_reason] || MISSING.deleted;
}

// "36 bins · 30 with stock · 2 with a problem"
export function summary_text(shape) {
	const s = shape || {};
	if (s.missing) return missing_words(s).sentence;
	const sum = s.summary;
	if (!sum) return "";
	if (!s.is_group) return bin_text({ on_hand_items: sum.items, status: sum.status });
	const parts = [count_word(sum.bins, "bin", "bins")];
	if (sum.bins) parts.push(`${sum.stock_bins} with stock`);
	if (sum.problem_bins) parts.push(`${sum.problem_bins} with a problem`);
	return parts.join(" · ");
}

// What one bin says under its name.
export function bin_text(bin) {
	const b = bin || {};
	const parts = [];
	const on_hand = num(b.on_hand_items, 0);
	parts.push(on_hand > 0 ? `${count_word(on_hand, "item", "items")} on hand` : "Nothing on hand");
	if (num(b.missing_items, 0) > 0) parts.push(`${count_word(b.missing_items, "assigned item", "assigned items")} missing`);
	if (num(b.unassigned_items, 0) > 0) parts.push(`${count_word(b.unassigned_items, "item", "items")} not assigned here`);
	return parts.join(" · ");
}

// ------------------------------------------------------------------ the front view

/**
 * A box's rows as a person facing it sees them: [{ warehouse, label, bins: [{ warehouse, name,
 * label, status, hit }] }], the top row first.
 *
 * The server sends the rows in the tree's own order (shelf 1 first, bin 1 first). A box whose
 * shelf 1 is at the bottom has its rows turned over; one whose bin 1 is at the right has each
 * row turned around. `bins` is the map's { warehouse: bin }; `hits` the warehouses the item
 * being looked for is at ({ warehouse: place }), or nothing.
 */
export function level_rows(shape, bins, hits) {
	const s = shape || {};
	const all = bins || {};
	const found = hits || {};
	let rows = (s.levels || []).map((level) => {
		let list = (level.bins || []).map((warehouse) => {
			const bin = all[warehouse] || {};
			const name = text(bin.warehouse_name) || warehouse;
			return {
				warehouse,
				name,
				label: short_bin_label(name, level.label),
				status: STATUS[bin.status] ? bin.status : "empty",
				hit: !!found[warehouse],
			};
		});
		if (s.bins_from === "Right") list = list.reverse();
		return { warehouse: level.warehouse, label: text(level.label), bins: list };
	});
	if (s.shelves_from === "Bottom") rows = rows.reverse();
	return rows;
}

// ---- a box's own order (the editor's "Order as you face it")
//
// The automatic order puts numbers and position words where they belong (the server's
// warehouse_map_rules.position_key). Where no rule can know (rows named for colors), the editor
// lets a person move a shelf up or down and a bin left or right. What is stored is the box's
// whole order as it then stands: each shelf, then its bins. These work on the rows AS SHOWN,
// which for a box whose shelf 1 is at the bottom or whose bin 1 is at the right is the stored
// order turned over, so the arrow a person presses always moves the thing the way it points.

function swapped(list, a, b) {
	const out = list.slice();
	const held = out[a];
	out[a] = out[b];
	out[b] = held;
	return out;
}

// A row that is a shelf of its own. The first row of a box whose warehouse holds bins directly
// has no name and always comes first; it cannot be moved, and nothing moves above it.
function is_shelf(shape, level) {
	return !!level && level.warehouse !== (shape || {}).warehouse;
}

/**
 * The box's levels with the shelf shown at `index` moved one place up (delta -1) or down (+1).
 * The same list back when it cannot move.
 */
export function move_level(shape, index, delta) {
	const s = shape || {};
	const levels = s.levels || [];
	const flip = s.shelves_from === "Bottom";
	const rows = flip ? levels.slice().reverse() : levels.slice();
	const to = index + delta;
	if (index < 0 || to < 0 || index >= rows.length || to >= rows.length) return levels;
	if (!is_shelf(s, rows[index]) || !is_shelf(s, rows[to])) return levels;
	const moved = swapped(rows, index, to);
	return flip ? moved.reverse() : moved;
}

/**
 * The box's levels with one bin of the shelf shown at `index` moved one place left (delta -1) or
 * right (+1) as shown. The same list back when it cannot move.
 */
export function move_bin(shape, index, warehouse, delta) {
	const s = shape || {};
	const levels = s.levels || [];
	const flip_rows = s.shelves_from === "Bottom";
	const at = flip_rows ? levels.length - 1 - index : index;
	const level = levels[at];
	if (!level) return levels;
	const flip = s.bins_from === "Right";
	const bins = flip ? (level.bins || []).slice().reverse() : (level.bins || []).slice();
	const from = bins.indexOf(warehouse);
	const to = from + delta;
	if (from < 0 || to < 0 || to >= bins.length) return levels;
	const moved = swapped(bins, from, to);
	const out = levels.slice();
	out[at] = Object.assign({}, level, { bins: flip ? moved.reverse() : moved });
	return out;
}

// The box's whole order as its levels now stand: each shelf, then its bins. This is what is
// stored (Warehouse Floor Map Shape.bin_order) once a person has moved anything.
export function order_from_levels(shape) {
	const s = shape || {};
	const out = [];
	(s.levels || []).forEach((level) => {
		if (is_shelf(s, level)) out.push(level.warehouse);
		(level.bins || []).forEach((w) => out.push(w));
	});
	return out;
}

// Every bin a box shows, as a flat list of warehouses.
export function shape_bins(shape) {
	const s = shape || {};
	if (s.warehouse && !s.is_group) return [s.warehouse];
	const out = [];
	(s.levels || []).forEach((level) => (level.bins || []).forEach((w) => out.push(w)));
	return out;
}

// ------------------------------------------------------------------ an item being looked for

/**
 * What an item search lights up on one map: { shapes: { warehouse: true }, bins: { warehouse:
 * place } }, the boxes and the bins. `places` is api.warehouse_map.find_item's list.
 */
export function highlight(places, map_name) {
	const out = { shapes: {}, bins: {} };
	(places || []).forEach((place) => {
		if (!place || place.map !== map_name) return;
		if (place.shape) out.shapes[place.shape] = true;
		out.bins[place.warehouse] = place;
	});
	return out;
}

// "4 here", "assigned here", or both.
export function place_text(place, uom) {
	const p = place || {};
	const parts = [];
	const qty = num(p.on_hand, 0);
	if (qty > 0) parts.push(`${Math.round(qty * 1000) / 1000}${uom ? ` ${uom}` : ""} here`);
	if (p.assigned) parts.push(qty > 0 ? "its bin" : "its bin, none on hand");
	return parts.join(" · ");
}

// The sentence above the map while an item is being looked for.
export function item_sentence(found) {
	const f = found || {};
	const places = f.places || [];
	const name = text(f.item_name) || text(f.item_code);
	if (!f.is_stock_item) return `${name} is not a stock item, so it has no place in the warehouse.`;
	if (!places.length) return `${name} is not on hand anywhere and has no bin.`;
	const shown = places.filter((p) => p.map).length;
	if (!shown) return `${name} is in ${count_word(places.length, "place", "places")}, none of them on a map yet.`;
	return `${name} is in ${count_word(places.length, "place", "places")}.`;
}

// ------------------------------------------------------------------ the drawing

// Sizes that follow the drawing, so a 4,000-wide plan and a 1,200-wide one read the same.
export function scale_of(map) {
	const width = num(map && map.width, 1200);
	return Math.max(width / 1200, 0.25);
}

/**
 * How a box's name is set: { size, vertical, lines }. A tall narrow box (a rack seen from above)
 * has its name turned to run up the box; the size is the largest that fits, between a floor and
 * a ceiling that follow the drawing.
 */
export function label_layout(shape, scale) {
	const s = shape || {};
	const k = num(scale, 1) || 1;
	const words = shape_label(s);
	const w = num(s.w, 0);
	const h = num(s.h, 0);
	const vertical = h > w * 1.4;
	const along = vertical ? h : w;
	const across = vertical ? w : h;
	const fit = (along * 0.9) / Math.max(words.length * 0.58, 1);
	const size = Math.max(7 * k, Math.min(fit, across * 0.55, 20 * k));
	return { size: round1(size), vertical, text: words };
}

// The view (an SVG viewBox) that shows the whole drawing.
export function fit_view(map) {
	return { x: 0, y: 0, w: num(map && map.width, 1200), h: num(map && map.height, 800) };
}

export function is_zoomed(view, map) {
	return !!view && view.w < num(map && map.width, 1200) - 0.5;
}

function hold_view(view, map) {
	const W = num(map && map.width, 1200);
	const H = num(map && map.height, 800);
	const w = Math.min(Math.max(view.w, W / 20), W);
	const h = (w * H) / W;
	return {
		w: round1(w),
		h: round1(h),
		x: round1(Math.min(Math.max(view.x, 0), W - w)),
		y: round1(Math.min(Math.max(view.y, 0), H - h)),
	};
}

// Zoom by `factor` (above 1 zooms in) keeping the point (cx, cy) of the drawing where it is.
export function zoom_view(view, factor, cx, cy, map) {
	const f = num(factor, 1) || 1;
	const w = view.w / f;
	const h = view.h / f;
	const px = cx === undefined || cx === null ? view.x + view.w / 2 : cx;
	const py = cy === undefined || cy === null ? view.y + view.h / 2 : cy;
	return hold_view({ w, h, x: px - ((px - view.x) / view.w) * w, y: py - ((py - view.y) / view.h) * h }, map);
}

export function pan_view(view, dx, dy, map) {
	return hold_view({ w: view.w, h: view.h, x: view.x - dx, y: view.y - dy }, map);
}

// A point on screen as a point of the drawing. `rect` is the SVG's box on screen.
export function to_map_point(view, rect, client_x, client_y) {
	const rw = num(rect && rect.width, 0) || 1;
	const rh = num(rect && rect.height, 0) || 1;
	return {
		x: view.x + ((client_x - num(rect && rect.left, 0)) / rw) * view.w,
		y: view.y + ((client_y - num(rect && rect.top, 0)) / rh) * view.h,
	};
}

// ------------------------------------------------------------------ the editor

export function snap(value, grid) {
	const g = num(grid, 0);
	return g > 0 ? Math.round(num(value, 0) / g) * g : num(value, 0);
}

// The grid boxes snap to: a hundredth of the drawing's width, never under one unit.
export function grid_of(map) {
	return Math.max(1, Math.round(num(map && map.width, 1200) / 240));
}

// A box held inside the drawing and no smaller than can be picked up again.
export function hold_shape(shape, map) {
	const W = num(map && map.width, 1200);
	const H = num(map && map.height, 800);
	const s = Object.assign({}, shape);
	s.w = round1(Math.min(Math.max(num(s.w, MIN_SIZE), MIN_SIZE), W));
	s.h = round1(Math.min(Math.max(num(s.h, MIN_SIZE), MIN_SIZE), H));
	s.x = round1(Math.min(Math.max(num(s.x, 0), 0), W - s.w));
	s.y = round1(Math.min(Math.max(num(s.y, 0), 0), H - s.h));
	s.rotation = round1(((num(s.rotation, 0) % 360) + 360) % 360);
	return s;
}

// `start` is the box as it was when the drag began; dx and dy how far the pointer has gone.
export function move_shape(start, dx, dy, map, grid) {
	return hold_shape(Object.assign({}, start, { x: snap(start.x + dx, grid), y: snap(start.y + dy, grid) }), map);
}

// Sizing by the bottom-right handle. A turned box is sized along its own sides, so the pointer's
// travel is first turned back by the box's angle.
export function resize_shape(start, dx, dy, map, grid) {
	const angle = (-num(start.rotation, 0) * Math.PI) / 180;
	const lx = dx * Math.cos(angle) - dy * Math.sin(angle);
	const ly = dx * Math.sin(angle) + dy * Math.cos(angle);
	return hold_shape(
		Object.assign({}, start, {
			w: Math.max(MIN_SIZE, snap(start.w + lx, grid)),
			h: Math.max(MIN_SIZE, snap(start.h + ly, grid)),
		}),
		map
	);
}

// Turn 90 degrees: the box's sides are swapped about its center. A rack is a rectangle, so this
// needs no angle; `rotation` is kept for a box that really sits at an angle.
export function turn_shape(shape, map) {
	const s = shape || {};
	const cx = num(s.x, 0) + num(s.w, 0) / 2;
	const cy = num(s.y, 0) + num(s.h, 0) / 2;
	return hold_shape(Object.assign({}, s, { w: s.h, h: s.w, x: cx - num(s.h, 0) / 2, y: cy - num(s.w, 0) / 2 }), map);
}

/**
 * A new box, in the middle of what is on screen, a little off from the last one added so two in
 * a row do not land on top of each other. `unit` is one of the editor's suggestions, or nothing
 * for an empty box of `kind`.
 */
export function new_shape(kind, unit, map, view, count) {
	const k = scale_of(map);
	const u = unit || {};
	const type = SHAPE_TYPES.includes(kind) ? kind : u.suggested_type || RACK;
	const size = type === RACK ? { w: 44 * k, h: 110 * k } : type === ROOM ? { w: 150 * k, h: 110 * k } : { w: 120 * k, h: 30 * k };
	const v = view || fit_view(map);
	const step = (num(count, 0) % 8) * 14 * k;
	return hold_shape(
		{
			shape_type: type,
			warehouse: text(u.warehouse) || "",
			warehouse_name: text(u.warehouse_name) || "",
			label: type === LABEL ? "Label" : "",
			x: snap(v.x + v.w / 2 - size.w / 2 + step, grid_of(map)),
			y: snap(v.y + v.h / 2 - size.h / 2 + step, grid_of(map)),
			w: size.w,
			h: size.h,
			rotation: 0,
			shelves_from: "Top",
			bins_from: "Left",
		},
		map
	);
}

// A new picture of another size: the drawing takes the picture's size and every box keeps its
// place over it.
export function rescale_shapes(shapes, from, to) {
	const sx = num(to.width, 1) / (num(from.width, 1) || 1);
	const sy = num(to.height, 1) / (num(from.height, 1) || 1);
	return (shapes || []).map((s) =>
		hold_shape(Object.assign({}, s, { x: s.x * sx, y: s.y * sy, w: s.w * sx, h: s.h * sy }), to)
	);
}

// Why the boxes cannot be saved, in the server's own words where it has them
// (warehouse_map_rules.clean_shape), or null. The server decides; this only saves a round trip.
export function check_shapes(shapes) {
	const seen = {};
	const list = shapes || [];
	for (let i = 0; i < list.length; i += 1) {
		const s = list[i] || {};
		const n = i + 1;
		const warehouse = text(s.warehouse);
		if (s.shape_type === LABEL) {
			if (!text(s.label)) return { index: i, message: `Box ${n}: a label needs its words.` };
			continue;
		}
		if (s.shape_type === RACK && !warehouse) return { index: i, message: `Box ${n}: a rack needs the row, bay or shelf it stands for.` };
		if (s.shape_type === ROOM && !warehouse && !text(s.label)) return { index: i, message: `Box ${n}: a room needs a name or a warehouse.` };
		if (warehouse) {
			if (seen[warehouse] !== undefined) {
				return { index: i, message: `${warehouse} is on this map twice (boxes ${seen[warehouse] + 1} and ${n}). Remove one of them.` };
			}
			seen[warehouse] = i;
		}
	}
	return null;
}

const SHAPE_FIELDS = ["shape_type", "warehouse", "label", "x", "y", "w", "h", "rotation", "shelves_from", "bins_from", "bin_order"];

// What Save sends: the map's own fields and the whole list of boxes, nothing the editor only
// uses to draw.
export function save_payload(edit) {
	const e = edit || {};
	const map = e.map || {};
	return {
		map_name: text(map.title),
		sort_order: Math.round(num(map.sort_order, 0)),
		disabled: map.disabled ? 1 : 0,
		width: round1(map.width),
		height: round1(map.height),
		background: text(map.background),
		shapes: (e.shapes || []).map((s) => {
			const out = {};
			SHAPE_FIELDS.forEach((key) => {
				out[key] = s[key] === undefined ? null : s[key];
			});
			out.warehouse = s.shape_type === LABEL ? "" : text(s.warehouse);
			out.label = text(s.label).slice(0, MAX_LABEL);
			// The box's own order, one warehouse per line; nothing when it follows the automatic one.
			out.bin_order = out.warehouse && Array.isArray(s.bin_order) ? s.bin_order.map(text).filter(Boolean).join("\n") : "";
			return out;
		}),
	};
}

// Whether the editor holds something Save has not sent: compared as what would be sent.
export function is_dirty(edit, saved) {
	return JSON.stringify(save_payload(edit)) !== saved;
}

// The editor's lists: what is still to place here, and what is on another map. The server's
// answer (`on_map`, and `via`, the boxes that show the unit) was read when the editor opened, so
// it is checked against the boxes the editor holds now: a unit whose box was removed since is to
// place again, and one shown by a box still here (its own, a group above it, a part of it) is not.
export function unit_lists(units, shapes, map_name) {
	const here = {};
	(shapes || []).forEach((s) => {
		if (text(s.warehouse)) here[text(s.warehouse)] = true;
	});
	const out = { todo: [], elsewhere: [] };
	(units || []).forEach((u) => {
		if (here[u.warehouse]) return;
		if (u.on_map && u.on_map !== map_name) {
			out.elsewhere.push(u);
			return;
		}
		if (u.on_map === map_name && (u.via || []).some((w) => here[w])) return;
		out.todo.push(u);
	});
	return out;
}

// The warehouses the picker offers whose name or trail holds every word typed.
export function filter_warehouses(warehouses, query, limit) {
	const want = text(query).toLowerCase().split(/\s+/).filter(Boolean);
	const list = Array.isArray(warehouses) ? warehouses : [];
	const out = [];
	for (let i = 0; i < list.length && out.length < (limit || 30); i += 1) {
		const row = list[i];
		const hay = `${row.warehouse_name || ""} ${row.trail || ""} ${row.name || ""}`.toLowerCase();
		if (want.every((word) => hay.includes(word))) out.push(row);
	}
	return out;
}
