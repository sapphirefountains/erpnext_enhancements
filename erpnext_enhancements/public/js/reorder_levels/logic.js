/*
 * Reorder levels on the audit screens (v1.725.0): the decisions, with no DOM and no server, so
 * scripts/test_reorder_levels.mjs can run them under node. The editor (editor.js) holds the state
 * and makes the calls; the pages draw its cells (cell_html) and wire its inputs (dom.js).
 *
 * An entry is api.reorder_levels' payload for one item:
 *   { item_code, has_rule, row_warehouse, warehouse_label, elsewhere, level, qty,
 *     material_request_type, modified, stock_uom, whole_number }
 * `level` is the reorder level (buy more when the stock falls to it) and `qty` how many to buy.
 *
 * WHAT A RULE MAY BE (review of #1338). Automatic reordering is on, so a rule that is wrong orders
 * stock nobody wanted: a rule needs BOTH numbers and both above 0 (a level of 0 raises a request
 * whenever stock reaches 0); a rule is switched off by REMOVING it, which asks first; and neither
 * number may be above MAX_AMOUNT, because a barcode scanned into a focused box is twelve digits.
 */

export const READ = "erpnext_enhancements.api.reorder_levels.get_reorder_levels";
export const WRITE = "erpnext_enhancements.api.reorder_levels.set_reorder_level";
export const REMOVE = "erpnext_enhancements.api.reorder_levels.remove_reorder_rule";

// The most a level or a quantity may be: api.reorder_levels.MAX_AMOUNT, which is
// stock_scan_rules.MAX_QTY. A test holds the two equal.
export const MAX_AMOUNT = 100000;

export const QTY_NEEDED = "Say how many to buy each time the stock falls to the reorder level.";
export const BOTH_NEEDED = "Enter both to create a reorder rule.";
export const ABOVE_ZERO = "Use a number above 0, or clear a box to remove the rule.";
export const REMOVE_ASK = "Remove this reorder rule?";

// A number as the box shows it: 3 not 3.000000000, 2.5 as 2.5, nothing for "no rule".
export function number_text(value) {
	if (value === null || value === undefined || value === "") return "";
	const n = Number(value);
	if (!Number.isFinite(n)) return "";
	return String(Math.round(n * 1e6) / 1e6);
}

// What was typed: { ok: true, value: number | null } (null = blank) or { ok: false }. Digits with
// one optional point and properly grouped thousands commas; no sign, no exponent, no "Infinity".
export function parse_amount(text) {
	const raw = String(text === null || text === undefined ? "" : text).trim();
	if (raw === "") return { ok: true, value: null };
	// Commas only as thousands separators: "2,5" is not 25.
	if (raw.includes(",") && !/^\d{1,3}(,\d{3})+(\.\d*)?$/.test(raw)) return { ok: false };
	const plain = raw.replace(/,/g, "");
	if (!/^(\d+\.?\d*|\.\d+)$/.test(plain)) return { ok: false };
	const value = Number(plain);
	return Number.isFinite(value) ? { ok: true, value } : { ok: false };
}

// What a blur should do with the two boxes' text:
//   { kind: "none" }                                  nothing changed
//   { kind: "invalid", field, message }               say what is wrong; nothing is sent
//   { kind: "incomplete", message }                   a new rule has one box: say so, gently
//   { kind: "remove" }                                a box of an existing rule was cleared: ask
//   { kind: "save", level, qty }
// Every number is checked first (a number, 0 or more, at most MAX_AMOUNT, whole where the unit is);
// then the rules above. A number that is 0 is never saved.
export function plan_save(entry, level_text, qty_text, tr) {
	const say = tr || ((s) => s);
	const fields = [
		["level", parse_amount(level_text), say("The reorder level")],
		["qty", parse_amount(qty_text), say("The order quantity")],
	];
	for (const [field, parsed, label] of fields) {
		if (!parsed.ok) return { kind: "invalid", field, message: `${label} ${say("must be a number, 0 or more.")}` };
		if (parsed.value !== null && parsed.value > MAX_AMOUNT) {
			return { kind: "invalid", field, message: `${label} ${say("cannot be more than")} ${MAX_AMOUNT.toLocaleString("en-US")}.` };
		}
		if (parsed.value !== null && entry && entry.whole_number && !Number.isInteger(parsed.value)) {
			return { kind: "invalid", field, message: `${label} ${say("must be a whole number for this item.")}` };
		}
	}
	const level = fields[0][1].value;
	const qty = fields[1][1].value;
	const ruled = !!(entry && entry.has_rule);
	if (!ruled) {
		if (level === null && qty === null) return { kind: "none" };
		if (level === null || qty === null) return { kind: "incomplete", message: say(BOTH_NEEDED) };
	} else {
		if (level !== null && qty !== null && level === Number(entry.level) && qty === Number(entry.qty)) return { kind: "none" };
		if (level === null || qty === null) return { kind: "remove" };
	}
	if (level <= 0) return { kind: "invalid", field: "level", message: say(ABOVE_ZERO) };
	if (qty <= 0) return { kind: "invalid", field: "qty", message: say(ABOVE_ZERO) };
	return { kind: "save", level, qty };
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

// The server call that removes the rule an entry shows.
export function remove_request(entry, view_warehouse) {
	return {
		method: REMOVE,
		args: {
			item_code: entry.item_code,
			warehouse: entry.row_warehouse || view_warehouse || "",
			view_warehouse: view_warehouse || "",
			modified: entry.modified || "",
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
