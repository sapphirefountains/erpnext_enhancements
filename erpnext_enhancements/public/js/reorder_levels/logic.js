/*
 * Reorder levels on the audit screens (v1.720.0): the decisions, with no DOM and no server, so
 * scripts/test_reorder_levels.mjs can run them under node. The editor (editor.js) holds the state
 * and makes the calls; the pages draw its cells (cell_html) and wire its inputs (dom.js).
 *
 * An entry is api.reorder_levels' payload for one item:
 *   { item_code, has_rule, row_warehouse, warehouse_label, elsewhere, level, qty,
 *     material_request_type, modified }
 * `level` is the reorder level (buy more when the stock falls to it) and `qty` how many to buy.
 */

export const READ = "erpnext_enhancements.api.reorder_levels.get_reorder_levels";
export const WRITE = "erpnext_enhancements.api.reorder_levels.set_reorder_level";

export const QTY_NEEDED = "Say how many to buy each time the stock falls to the reorder level.";

// A number as the box shows it: 3 not 3.000000000, 2.5 as 2.5, nothing for "no rule".
export function number_text(value) {
	if (value === null || value === undefined || value === "") return "";
	const n = Number(value);
	if (!Number.isFinite(n)) return "";
	return String(Math.round(n * 1e6) / 1e6);
}

// What was typed: { ok: true, value: number | null } (null = blank) or { ok: false }. Digits with
// one optional point and thousands commas; no sign, no exponent, no "Infinity".
export function parse_amount(text) {
	const raw = String(text === null || text === undefined ? "" : text).trim();
	if (raw === "") return { ok: true, value: null };
	const plain = raw.replace(/,/g, "");
	if (!/^(\d+\.?\d*|\.\d+)$/.test(plain)) return { ok: false };
	const value = Number(plain);
	return Number.isFinite(value) ? { ok: true, value } : { ok: false };
}

// What a blur should do with the two boxes' text: nothing, say what is wrong, or save.
//   { kind: "none" }
//   { kind: "invalid", field: "level" | "qty", message }
//   { kind: "save", level, qty }
// On an item with a rule a blank box means 0 (the person cleared it); on one with none, a blank
// box is simply not filled in yet, and nothing is saved until a box has a number.
export function plan_save(entry, level_text, qty_text, tr) {
	const say = tr || ((s) => s);
	const level = parse_amount(level_text);
	const qty = parse_amount(qty_text);
	if (!level.ok) return { kind: "invalid", field: "level", message: say("The reorder level must be a number, 0 or more.") };
	if (!qty.ok) return { kind: "invalid", field: "qty", message: say("The order quantity must be a number, 0 or more.") };
	const ruled = !!(entry && entry.has_rule);
	if (!ruled && level.value === null && qty.value === null) return { kind: "none" };
	const new_level = level.value === null ? 0 : level.value;
	const new_qty = qty.value === null ? 0 : qty.value;
	if (ruled && new_level === Number(entry.level) && new_qty === Number(entry.qty)) return { kind: "none" };
	if (new_level > 0 && new_qty <= 0) return { kind: "invalid", field: "qty", message: say(QTY_NEEDED) };
	return { kind: "save", level: new_level, qty: new_qty };
}

// The server call for a save. A rule is edited where it is (`row_warehouse`); an item with none gets
// one on the warehouse the screen shows. `modified` is the Item's stamp from the read.
export function save_request(entry, view_warehouse, plan) {
	return {
		method: WRITE,
		args: {
			item_code: entry.item_code,
			warehouse: entry.row_warehouse || view_warehouse || "",
			view_warehouse: view_warehouse || "",
			modified: entry.modified || "",
			level: plan.level,
			qty: plan.qty,
		},
	};
}

// Whether the item can show boxes: it must have somewhere for a rule to be (its own, or the
// warehouse on screen).
export function has_place(entry) {
	return !!(entry && entry.row_warehouse);
}

// A refused save the page should answer by reading the item again: the item changed under it.
export function is_conflict(err) {
	if (!err) return false;
	if (err.status === 409) return true;
	return /changed by someone else/i.test(String(err.message || ""));
}

export function error_text(err, fallback) {
	const text = String((err && err.message) || "").replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
	return text || fallback;
}
