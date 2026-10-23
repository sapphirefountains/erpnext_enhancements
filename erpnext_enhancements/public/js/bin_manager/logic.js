/*
 * Bin Manager (v1.693.0, ER-2026-1320545): the page's pure decisions, no DOM and no frappe, so
 * scripts/test_bin_manager.mjs runs them under node. The page script draws and wires
 * (inventory_enhancements/page/bin_manager/bin_manager.js); controller.js holds the screen state
 * and the server calls; this file decides.
 *
 *   routes          /desk/bin-manager is the picker, /desk/bin-manager/<warehouse> one bin. Each is
 *                   its own history entry. ?warehouse=<name> on the bare route (frappe.route_options,
 *                   since v16.50 also in the address) opens that bin in place of the picker.
 *   actions         which buttons a row shows, from what the server said the caller may do
 *                   (manager_boot: can_manage, can_count, can_move, can_create_items, the boneyard)
 *   make-home       "Make <bin> this item's bin" is ticked when the whole quantity on hand moves,
 *                   and decided again on every change of quantity until the person touches it
 *   requests        each action's endpoint and arguments, every write with its client_ref
 *
 * The endpoints are the ones /stock-scan already calls, plus bin_assignments.create_and_assign
 * and bin_assignments.manager_boot, which this page added.
 */

// The unit-cost question (TASK-2026-02914): the same pure pieces /stock-scan asks it with, so the two
// screens word it, check it and cap it alike. A count up from an empty bin, for an item ERPNext has
// no cost for, is answered with {needs_valuation_rate: true, ...} instead of a save.
export { MAX_UNIT_COST, costPromptText, isCostPrompt, unitCostProblem } from "../stock_scan/logic.js";
import { unitCostProblem } from "../stock_scan/logic.js";

export const PAGE = "bin-manager";
export const BA = "erpnext_enhancements.inventory_enhancements.bin_assignments.";
export const SCAN = "erpnext_enhancements.api.stock_scan.";

// stock_scan_rules.TOLERANCE: what counts as "nothing left".
export const TOLERANCE = 1e-6;

// The page's reads.
export const READS = {
	boot: BA + "manager_boot",
	location: SCAN + "get_location",
	recent: SCAN + "get_recent",
	search_items: SCAN + "search_items",
	check_new_item: SCAN + "check_new_item",
};

// The kinds of save, each one endpoint. "keep" and "add" are both an assign: "keep" puts an item
// already listed here on the bin (an undecided or default-only row), "add" one found by search.
export const ENDPOINTS = {
	count: BA + "set_count",
	move: BA + "move_to",
	boneyard: BA + "send_to_boneyard",
	unassign: BA + "unassign",
	keep: BA + "assign",
	add: BA + "assign",
	create: BA + "create_and_assign",
	undo: SCAN + "undo",
};

export const ACTION_LABELS = {
	count: "Change quantity",
	move: "Move",
	keep: "Keep on this bin",
	boneyard: "Send to boneyard",
	unassign: "Unassign",
	add: "Add existing item",
	create: "New item",
};

// ------------------------------------------------------------------ routes

// The screen a route names, or null when it is not this page's.
export function parse_route(route) {
	const parts = Array.isArray(route) ? route : [];
	if (parts[0] !== PAGE) return null;
	const warehouse = typeof parts[1] === "string" ? parts[1].trim() : "";
	return warehouse ? { screen: "bin", warehouse } : { screen: "picker" };
}

export function build_route(target) {
	return target && target.screen === "bin" && target.warehouse ? [PAGE, target.warehouse] : [PAGE];
}

export function route_key(target) {
	return build_route(target).join("/");
}

// What a deep link carried in frappe.route_options (the address's query, or a set_route object):
// `warehouse` opens that bin, `q` fills the picker's filter. Anything else is ignored.
export function read_route_options(options) {
	const opts = options && typeof options === "object" ? options : {};
	const text = (value) => (value === undefined || value === null ? "" : String(value).trim());
	const out = { warehouse: text(opts.warehouse), q: text(opts.q) };
	out.any = Object.keys(opts).length > 0;
	return out;
}

// ------------------------------------------------------------------ saves

// A save's reference: unique per save, the same on a retry (api.stock_scan._CLIENT_REF).
export function client_ref(now, random) {
	const time = Math.floor(Number(now) || 0).toString(36);
	const tail = Math.floor((Number(random) || 0) * 36 ** 8)
		.toString(36)
		.padStart(8, "0");
	return `bm-${time}-${tail}`;
}

export const CLIENT_REF = /^[A-Za-z0-9._:-]{8,80}$/;

// Whether a failed save keeps its reference for the next try. A reply that never arrived (status
// 0) or "still being recorded" (409, DuplicateEntryError) may have saved: the next tap must send
// the same reference, so the server answers with the first save instead of posting a second. Any
// other refusal saved nothing, and a fresh reference is safe.
export function keep_ref(err) {
	const e = err || {};
	return !e.status || e.status === 409 || e.exc_type === "DuplicateEntryError";
}

function number(value) {
	if (value === null || value === undefined) return NaN;
	const text = String(value).trim();
	if (!text) return NaN;
	return Number(text);
}

export function plain(value) {
	const n = Number(value) || 0;
	return String(Math.round(n * 1000) / 1000);
}

export function amount(qty, uom) {
	return `${plain(qty)} ${uom || ""}`.trim();
}

// A sentence when the values cannot be sent, or null. The server checks all of it again and says
// why in its own words; this only catches what does not need a round trip.
export function check_values(kind, row, values, current) {
	const v = values || {};
	if (kind === "count") {
		const qty = number(v.qty);
		if (!Number.isFinite(qty) || qty < 0) return "Enter the quantity you counted, 0 or more.";
		if (row && Math.abs(qty - (Number(row.on_hand) || 0)) <= TOLERANCE) {
			return "That is what the system already shows.";
		}
		// A unit cost, when the server asked for one (the key is there, and Frappe's own Float hands back null
		// for a blank box, so null is checked too): a positive number, within the caps.
		if ("rate" in v) {
			const cost = unitCostProblem(v.rate === null || v.rate === undefined ? "" : v.rate, row && row.stock_uom, qty, v.max_value);
			if (cost.problem) return cost.problem;
		}
		return null;
	}
	if (kind === "move" || kind === "boneyard") {
		const qty = number(v.qty);
		const on_hand = Number(row && row.on_hand) || 0;
		if (!Number.isFinite(qty) || qty <= 0) return "Enter how many to move, more than 0.";
		if (qty > on_hand + TOLERANCE) return `Only ${amount(on_hand, row && row.stock_uom)} is here.`;
		if (kind === "move") {
			if (!v.to_warehouse) return "Pick the bin to move it to.";
			if (v.to_warehouse === current) return "Pick a different bin.";
		}
		return null;
	}
	if (kind === "add" && !(v.item_code || (row && row.item_code))) return "Pick an item.";
	if (kind === "create") {
		for (const [field, words] of [
			["item_code", "Enter the part or model number."],
			["item_name", "Enter a name for the item."],
			["item_group", "Choose a group."],
			["stock_uom", "Choose a unit."],
		]) {
			if (!String(v[field] || "").trim()) return words;
		}
	}
	return null;
}

// The server call a save makes: { method, args }. Every write carries its client_ref; Undo is
// keyed on the log row it reverses.
export function request(kind, warehouse, row, values, ref) {
	const v = values || {};
	const item_code = (row && row.item_code) || v.item_code;
	const method = ENDPOINTS[kind];
	if (!method) throw new Error(`unknown action ${kind}`);
	if (kind === "undo") return { method, args: { log: v.log } };
	if (kind === "count") {
		const args = { item_code, warehouse, qty: number(v.qty), client_ref: ref };
		// Sent only with a unit cost the person gave when asked; the server uses it only for an item with none.
		if ("rate" in v && v.rate !== null && v.rate !== undefined && String(v.rate).trim() !== "") args.rate = unitCostProblem(v.rate).rate;
		return { method, args };
	}
	if (kind === "move") {
		return {
			method,
			args: {
				item_code,
				from_warehouse: warehouse,
				to_warehouse: v.to_warehouse,
				qty: number(v.qty),
				client_ref: ref,
				make_home: v.make_home ? 1 : 0,
			},
		};
	}
	if (kind === "boneyard") {
		return { method, args: { item_code, from_warehouse: warehouse, qty: number(v.qty), client_ref: ref } };
	}
	if (kind === "create") {
		return {
			method,
			args: {
				warehouse,
				client_ref: ref,
				item_code: String(v.item_code || "").trim(),
				item_name: String(v.item_name || "").trim(),
				item_group: v.item_group || "",
				stock_uom: v.stock_uom || "",
			},
		};
	}
	// unassign, keep, add
	return { method, args: { item_code, warehouse, client_ref: ref } };
}

// ------------------------------------------------------------------ who sees what

// What the caller may do, from manager_boot. Missing means no.
export function permissions(boot) {
	const b = boot || {};
	return {
		can_manage: !!b.can_manage,
		can_count: !!b.can_count,
		can_move: !!b.can_move,
		can_create_items: !!b.can_create_items,
		// Whether an item's name links to its Item record: Item read. A boot that does not say is "yes".
		can_open_items: b.can_open_items !== false,
		boneyard: b.boneyard_warehouse || null,
		boneyard_name: b.boneyard_name || b.boneyard_warehouse || null,
	};
}

// What a save has already done to the row on screen, before the list is read again (the read follows and is
// the truth): a move or a boneyard trip takes the quantity off, a count sets it. Mutates and returns the row
// the page holds, so the list's identity (and with it the reorder boxes' read) does not change.
export function apply_local(kind, row, values) {
	if (!row) return row;
	const qty = number((values || {}).qty);
	if (!Number.isFinite(qty)) return row;
	const on_hand = Number(row.on_hand) || 0;
	if (kind === "move" || kind === "boneyard") row.on_hand = Math.max(on_hand - qty, 0);
	else if (kind === "count") row.on_hand = Math.max(qty, 0);
	return row;
}

// The buttons a row shows, in order. `warehouse` is the bin on screen.
//   count     Stock Reconciliation create + submit (set_count posts one as the user)
//   move      Stock Entry create + submit, and something here to move
//   keep      the module gate, for an item listed here that is not on the bin
//   boneyard  the module gate, a transfer, a boneyard set, something here, and not the boneyard
//   unassign  the module gate, for an item this bin holds by rule, default or nobody's decision
export function row_actions(row, perms, warehouse) {
	const p = perms || {};
	const r = row || {};
	const here = Number(r.on_hand) > TOLERANCE;
	const is_boneyard = !!p.boneyard && p.boneyard === warehouse;
	const out = [];
	if (p.can_count) out.push("count");
	if (p.can_move && here) out.push("move");
	if (p.can_manage && !r.assigned && !is_boneyard) out.push("keep");
	if (p.can_manage && p.can_move && p.boneyard && !is_boneyard && here) out.push("boneyard");
	if (p.can_manage && (r.assigned || r.is_default || r.undecided)) out.push("unassign");
	return out;
}

// The bin's own buttons: add an existing item, or create one and put it here.
export function bin_actions(perms, warehouse) {
	const p = perms || {};
	if (!p.can_manage || (p.boneyard && p.boneyard === warehouse)) return [];
	return p.can_create_items ? ["add", "create"] : ["add"];
}

// Unassign is refused while stock is here (stock_scan_rules.unassign_refusal), so with stock it
// first offers what empties the bin: Move, and Send to boneyard when that is set up.
export function unassign_plan(row, perms, warehouse) {
	const on_hand = Number(row && row.on_hand) || 0;
	if (on_hand <= TOLERANCE) return { direct: true, options: [] };
	const offered = row_actions(row, perms, warehouse).filter((a) => a === "move" || a === "boneyard");
	return { direct: false, options: offered };
}

// Why a row is listed here, as short labels (api.stock_scan._items_at's reasons).
export function why_listed(row) {
	const r = row || {};
	const out = [];
	if (r.assigned) out.push({ key: "assigned", label: "On this bin", tone: "good" });
	if (r.is_default) out.push({ key: "default", label: "Default bin", tone: "service" });
	if (Number(r.on_order) > TOLERANCE) out.push({ key: "on_order", label: `${plain(r.on_order)} on order`, tone: "warn" });
	if (r.undecided) out.push({ key: "undecided", label: "Not decided", tone: "warn" });
	if (!r.assigned && !r.is_default && !r.undecided && Number(r.on_hand) > TOLERANCE) {
		out.push({ key: "stock_only", label: "Stock only", tone: "neutral" });
	}
	return out;
}

// ------------------------------------------------------------------ make it the item's bin

// Ticked when the move takes everything on hand: the item lives where its stock went. A partial
// move leaves stock behind, which keeps this bin's claim on it (bin_assignments._plan_source).
export function make_home_default(qty, on_hand) {
	const q = number(qty);
	const have = Number(on_hand) || 0;
	return have > TOLERANCE && Number.isFinite(q) && q >= have - TOLERANCE;
}

// The box's state after the quantity changes: decided again until the person has touched it.
export function make_home_after_qty(state, qty, on_hand) {
	if (state && state.touched) return state;
	return { touched: false, value: make_home_default(qty, on_hand) };
}

export function make_home_touched(value) {
	return { touched: true, value: !!value };
}

// Whether the box shows at all: it changes assignments (the module gate), and nothing is ever
// assigned to the boneyard (bin_assignments._refuse_boneyard).
export function make_home_offered(perms, to_warehouse) {
	const p = perms || {};
	return !!p.can_manage && !!to_warehouse && to_warehouse !== p.boneyard;
}

// ------------------------------------------------------------------ the picker and lists

function words(text) {
	return String(text || "")
		.toLowerCase()
		.split(/\s+/)
		.filter(Boolean);
}

// Bins whose name or trail holds every word typed.
export function filter_bins(bins, query) {
	const want = words(query);
	const list = Array.isArray(bins) ? bins : [];
	if (!want.length) return list.slice();
	return list.filter((b) => {
		const hay = `${b.warehouse_name || ""} ${b.trail || ""} ${b.warehouse || ""}`.toLowerCase();
		return want.every((w) => hay.includes(w));
	});
}

// The picker's sections: bins under the same parent together, in the order they first appear.
export function group_bins(bins) {
	const groups = [];
	const by = new Map();
	for (const b of bins || []) {
		const key = b.trail || "";
		if (!by.has(key)) {
			const group = { trail: key, bins: [] };
			by.set(key, group);
			groups.push(group);
		}
		by.get(key).bins.push(b);
	}
	return groups;
}

// Where Move can send an item: every other bin, filtered by what is typed, the first `limit`.
export function destinations(bins, current, query, limit) {
	return filter_bins(bins, query)
		.filter((b) => b.warehouse !== current)
		.slice(0, limit || 30);
}

// This bin's saves among the caller's recent ones: to it, or out of it.
export function bin_recent(recent, warehouse) {
	return (recent || []).filter((r) => r && (r.warehouse === warehouse || r.from_warehouse === warehouse));
}

// A save's log row at the top of the list, replacing an older copy of itself (an undo).
export function merge_recent(recent, row) {
	if (!row || !row.name) return (recent || []).slice();
	return [row].concat((recent || []).filter((r) => r && r.name !== row.name));
}

// A recent row in words: what was done, for the list under the items.
export function describe_log(row) {
	const r = row || {};
	const what = r.item_name || r.item_code || "";
	const qty = amount(r.qty, r.stock_uom);
	switch (r.action) {
		case "Assign":
			return r.created_item ? `New item ${what}, put on ${r.warehouse_name}` : `Put ${what} on ${r.warehouse_name}`;
		case "Unassign":
			return `Took ${what} off ${r.warehouse_name}`;
		case "Move To":
			return `Moved ${qty} of ${what} to ${r.warehouse_name}${r.moved_home ? " (its bin now)" : ""}`;
		case "Boneyard":
			return `Sent ${qty} of ${what} to the boneyard`;
		case "Count":
			return `Counted ${qty} of ${what} (was ${amount(r.qty_before, r.stock_uom)})`;
		case "Move":
			return `Moved ${qty} of ${what} here`;
		case "Take":
			return `Took ${qty} of ${what}`;
		default:
			return `${r.action || "Saved"}: ${qty} of ${what}`;
	}
}
