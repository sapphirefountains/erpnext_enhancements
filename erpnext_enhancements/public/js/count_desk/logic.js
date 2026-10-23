/*
 * Count Desk (v1.737.0): the page's pure decisions, no DOM and no frappe, so scripts/test_count_desk.mjs
 * runs them under node. The page script draws and wires (inventory_enhancements/page/count_desk), the
 * controller holds the screen state and the server calls (controller.js), and this file decides.
 *
 *   routes        /desk/count-desk is the plan (the warehouse tree, open and recent counts),
 *                 /desk/count-desk/<count> the sheet, /desk/count-desk/<count>/review the review.
 *                 Each is its own history entry.
 *   quantities    a count cell takes a number or a sum: 3*24+5 is three cases of 24 and five loose.
 *                 A barcode or a label typed or scanned into one is a scan, never a quantity.
 *   the tree      groups and bins into a tree with each node's bins, items, value and the bins
 *                 that have not been counted lately; the selection, its presets and overlaps.
 *   the sheet     its rows (the server's, count_desk_rules.sheet_rows), filters, the next row,
 *                 a save's answer merged in, a refused save put back, a pasted column (one value
 *                 per row on screen), "zero the rest", the summary.
 *   the review    the variances by value, and what stops a post: a reason the settings require,
 *                 or a save that did not land.
 *   export        CSV (formula-safe) and the printable count sheet.
 *
 * The server judges everything again. Where a rule exists in count_desk_rules.py too (the summary,
 * the row key), test_count_desk.py holds the two equal; tests/data/count_desk_vectors.json also
 * pins the paste, which only the page does, from both sides.
 */

export const PAGE = "count-desk";
export const API = "erpnext_enhancements.api.count_desk.";
export const INV = "erpnext_enhancements.api.inventory_scanner.";
export const SCAN = "erpnext_enhancements.api.stock_scan.";

export const READS = {
	boot: API + "boot",
	sheet: API + "get_sheet",
	resolve: INV + "resolve_scan",
	search_items: SCAN + "search_items",
};

export const WRITES = {
	start: API + "start_count",
	update: API + "update_count",
	save: API + "save_counts",
	post: API + "post_count",
	cancel: INV + "cancel_session",
};

// count_desk_rules.KEY_SEP: the ASCII unit separator, which no name typed on a form contains.
export const KEY_SEP = "\u001f";
export const TOLERANCE = 1e-9;
// stock_scan_rules.MAX_QTY: no single count is larger. A bigger "number" is a barcode.
export const MAX_QTY = 100000;
// Rows one save carries (api.count_desk.SAVE_LIMIT).
export const SAVE_LIMIT = 500;
// Changes Undo remembers.
export const UNDO_LIMIT = 100;

export const FILTERS = [
	{ key: "all", label: "All" },
	{ key: "todo", label: "To count" },
	{ key: "variance", label: "Variances" },
	{ key: "recount", label: "Recount" },
	{ key: "moved", label: "Moved since counted" },
	{ key: "added", label: "Not expected" },
];

// ------------------------------------------------------------------ routes

export function parse_route(route) {
	const parts = Array.isArray(route) ? route : [];
	if (parts[0] !== PAGE) return null;
	const count = typeof parts[1] === "string" ? parts[1].trim() : "";
	if (!count) return { screen: "plan" };
	if (parts[2] === "review") return { screen: "review", count };
	return { screen: "sheet", count };
}

export function build_route(target) {
	if (!target || !target.count || target.screen === "plan") return [PAGE];
	return target.screen === "review" ? [PAGE, target.count, "review"] : [PAGE, target.count];
}

export function route_key(target) {
	return build_route(target).join("/");
}

// ------------------------------------------------------------------ numbers

export function plain(value) {
	const n = Number(value);
	if (!Number.isFinite(n)) return "";
	return String(Math.round(n * 1000) / 1000);
}

export function signed(value) {
	const n = Number(value) || 0;
	if (Math.abs(n) <= TOLERANCE) return "0";
	return (n > 0 ? "+" : "−") + plain(Math.abs(n));
}

export function money(value, opts) {
	const n = Number(value) || 0;
	const o = opts || {};
	const abs = Math.abs(n).toLocaleString("en-US", {
		minimumFractionDigits: o.whole ? 0 : 2,
		maximumFractionDigits: o.whole ? 0 : 2,
	});
	if (o.signed) {
		if (Math.abs(n) < 0.005) return "$0" + (o.whole ? "" : ".00");
		return (n > 0 ? "+$" : "−$") + abs;
	}
	return (n < 0 ? "−$" : "$") + abs;
}

export function percent(value) {
	if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
	return `${Math.round(Number(value) * 10) / 10}%`;
}

// "3 days ago", "today", "never" for a last-counted date (count_desk_rules.days_since).
export function age(days) {
	if (days === null || days === undefined) return "never";
	const d = Number(days);
	if (d <= 0) return "today";
	if (d === 1) return "yesterday";
	if (d < 60) return `${d} days ago`;
	if (d < 730) return `${Math.round(d / 30)} months ago`;
	return `${Math.round(d / 365)} years ago`;
}

// A count cell's text as a quantity: a number, or a sum of them. The grammar is the four
// operators, parentheses and a leading minus; "x" and "×" are multiplication, because a case of
// 24 is written 3x24 on a clipboard. Returns { value } or { error }, never throws, never evals.
export function evaluate(text) {
	const src = String(text === null || text === undefined ? "" : text)
		.replace(/[×xX]/g, "*")
		.replace(/,/g, "")
		.trim();
	if (!src) return { empty: true };
	if (!/^[\d\s.+\-*/()]+$/.test(src)) return { error: "Type a number, or a sum like 3*24+5." };
	let pos = 0;
	const peek = () => {
		while (src[pos] === " ") pos += 1;
		return src[pos];
	};
	const fail = () => {
		throw new Error("bad");
	};
	function factor() {
		const ch = peek();
		if (ch === "-") {
			pos += 1;
			return -factor();
		}
		if (ch === "+") {
			pos += 1;
			return factor();
		}
		if (ch === "(") {
			pos += 1;
			const v = expr();
			if (peek() !== ")") fail();
			pos += 1;
			return v;
		}
		const m = /^\d*\.?\d+|^\d+\./.exec(src.slice(pos));
		if (!m) fail();
		pos += m[0].length;
		return Number(m[0]);
	}
	function term() {
		let v = factor();
		for (;;) {
			const ch = peek();
			if (ch === "*") {
				pos += 1;
				v *= factor();
			} else if (ch === "/") {
				pos += 1;
				const d = factor();
				if (Math.abs(d) <= TOLERANCE) fail();
				v /= d;
			} else return v;
		}
	}
	function expr() {
		let v = term();
		for (;;) {
			const ch = peek();
			if (ch === "+") {
				pos += 1;
				v += term();
			} else if (ch === "-") {
				pos += 1;
				v -= term();
			} else return v;
		}
	}
	let value;
	try {
		value = expr();
		if (peek() !== undefined) fail();
	} catch (e) {
		return { error: "That sum could not be worked out." };
	}
	if (!Number.isFinite(value)) return { error: "That sum could not be worked out." };
	value = Math.round(value * 1e6) / 1e6;
	return { value, expression: !/^\s*\d*\.?\d+\s*$/.test(src) };
}

// A count cell's value checked as count_desk_rules.check_entry -> stock_scan_rules.check_count will
// check it: 0 or more, whole for a whole-number unit, at most MAX_QTY.
export function check_count(value, row) {
	const r = row || {};
	if (value < -TOLERANCE) return "A count cannot be less than zero.";
	if (value > MAX_QTY) return `${plain(value)} is more than one count can record. Check the number.`;
	if (r.whole_number && Math.abs(value - Math.round(value)) > TOLERANCE) {
		return `${r.uom || "This unit"} is counted in whole numbers, so ${plain(value)} cannot be used.`;
	}
	return null;
}

// Whether what was typed or scanned into a count cell is a scan rather than a quantity. A wedge
// scanner types into whatever has focus and presses Enter, so a scanned bin label or part number
// lands in a count cell: anything with a letter (other than the x of 3x24), a URL, or a run of
// eight or more digits (a UPC: nobody counts 12 million of anything) is a scan.
export function looks_like_scan(text) {
	const s = String(text || "").trim();
	if (!s) return false;
	if (/:\/\/|^www\./i.test(s)) return true;
	if (/^\d{8,}$/.test(s)) return true;
	if (/^[\d\s.+\-*/()xX×,]+$/.test(s)) return false;
	return true;
}

// ------------------------------------------------------------------ keys

export function line_key(item_code, warehouse, storage_location) {
	return [warehouse || "", storage_location || "", item_code || ""].join(KEY_SEP);
}

export function split_key(key) {
	const parts = String(key || "").split(KEY_SEP);
	if (parts.length !== 3 || !parts[0] || !parts[2]) return null;
	return { warehouse: parts[0], storage_location: parts[1] || null, item_code: parts[2] };
}

export function group_of(row) {
	return `${row.item_code}${KEY_SEP}${row.warehouse}`;
}

// ------------------------------------------------------------------ natural order

export function natural_compare(a, b) {
	const split = (s) => String(s || "").toLowerCase().match(/\d+|\D+/g) || [];
	const x = split(a);
	const y = split(b);
	for (let i = 0; i < Math.max(x.length, y.length); i++) {
		if (x[i] === undefined) return -1;
		if (y[i] === undefined) return 1;
		const nx = /^\d/.test(x[i]);
		const ny = /^\d/.test(y[i]);
		if (nx && ny) {
			const d = Number(x[i]) - Number(y[i]);
			if (d) return d;
		} else if (nx !== ny) {
			return nx ? -1 : 1;
		} else if (x[i] !== y[i]) {
			return x[i] < y[i] ? -1 : 1;
		}
	}
	return 0;
}

// ------------------------------------------------------------------ the tree

// The warehouse tree with roll-ups. `groups` [{name, label, parent}], `bins` [{warehouse,
// warehouse_name, parent, on_hand_items, stock_value, days_since}]. A node's `bins` is every bin
// under it; `stale` the ones holding stock not counted in `days` (never counted included),
// `never` the ones holding stock never counted. Groups with no bins under them are dropped.
export function build_tree(groups, bins, days) {
	const window = Number(days) || 90;
	const nodes = new Map();
	for (const g of groups || []) {
		nodes.set(g.name, { id: g.name, label: g.label || g.name, parent: g.parent || null, is_bin: false, children: [] });
	}
	for (const b of bins || []) {
		nodes.set(b.warehouse, {
			id: b.warehouse,
			label: b.warehouse_name || b.warehouse,
			parent: b.parent || null,
			is_bin: true,
			children: [],
			bin: b,
		});
	}
	const roots = [];
	for (const node of nodes.values()) {
		const parent = node.parent && nodes.get(node.parent);
		if (parent && parent !== node) parent.children.push(node);
		else roots.push(node);
	}
	const roll = (node, depth, seen) => {
		if (seen.has(node.id)) return node;
		seen.add(node.id);
		node.depth = depth;
		node.children.sort((a, b) => natural_compare(a.label, b.label));
		node.bins = [];
		node.items = 0;
		node.value = 0;
		node.stale = [];
		node.never = [];
		node.oldest = 0;
		node.holding = 0;
		if (node.is_bin) {
			const b = node.bin;
			const holds = Number(b.on_hand_items) > 0;
			node.bins.push(b.warehouse);
			node.items = Number(b.on_hand_items) || 0;
			node.value = Number(b.stock_value) || 0;
			node.holding = holds ? 1 : 0;
			const d = b.days_since === null || b.days_since === undefined ? null : Number(b.days_since);
			if (holds && d === null) node.never.push(b.warehouse);
			if (holds && (d === null || d > window)) node.stale.push(b.warehouse);
			node.oldest = holds ? (d === null ? Infinity : d) : 0;
		}
		for (const child of node.children) {
			roll(child, depth + 1, seen);
			node.bins.push(...child.bins);
			node.items += child.items;
			node.value += child.value;
			node.holding += child.holding;
			node.stale.push(...child.stale);
			node.never.push(...child.never);
			node.oldest = Math.max(node.oldest, child.oldest);
		}
		return node;
	};
	const seen = new Set();
	roots.forEach((r) => roll(r, 0, seen));
	const prune = (list) =>
		list
			.filter((n) => n.bins.length > 0)
			.map((n) => {
				n.children = prune(n.children);
				return n;
			});
	const kept = prune(roots).sort((a, b) => natural_compare(a.label, b.label));
	const by_id = new Map();
	const index = (n) => {
		by_id.set(n.id, n);
		n.children.forEach(index);
	};
	kept.forEach(index);
	return { roots: kept, by_id };
}

// The node ids to show for a filter: a node whose label (or a bin's trail) holds every word, its
// ancestors, and everything under a matching group. Null means no filter: show what is expanded.
export function tree_matches(tree, query) {
	const want = String(query || "")
		.toLowerCase()
		.split(/\s+/)
		.filter(Boolean);
	if (!want.length) return null;
	const show = new Set();
	const all = (n) => {
		show.add(n.id);
		n.children.forEach(all);
	};
	const walk = (n, trail) => {
		const hay = `${trail} ${n.label} ${n.id}`.toLowerCase();
		if (want.every((w) => hay.includes(w))) {
			all(n);
			return true;
		}
		let any = false;
		for (const child of n.children) if (walk(child, `${trail} ${n.label}`)) any = true;
		if (any) show.add(n.id);
		return any;
	};
	(tree.roots || []).forEach((r) => walk(r, ""));
	return show;
}

// The rows the tree shows, in order: [{node, depth}]. A node's children show when it is
// expanded, or when a filter matched under it.
export function tree_rows(tree, expanded, matches) {
	const out = [];
	const walk = (n) => {
		if (matches && !matches.has(n.id)) return;
		out.push(n);
		const open = matches ? true : expanded.has(n.id);
		if (open) n.children.forEach(walk);
	};
	(tree.roots || []).forEach(walk);
	return out;
}

// "all", "some" or "none" of a node's bins are selected.
export function node_state(node, selected) {
	const bins = node.bins || [];
	let n = 0;
	for (const b of bins) if (selected.has(b)) n += 1;
	if (!n) return "none";
	return n === bins.length ? "all" : "some";
}

// Ticking a node selects every bin under it; ticking one already all selected clears them.
export function toggle_node(node, selected) {
	const next = new Set(selected);
	const state = node_state(node, selected);
	for (const b of node.bins || []) {
		if (state === "all") next.delete(b);
		else next.add(b);
	}
	return next;
}

// What the selection holds: bins, items on hand and value.
export function selection_totals(tree, selected) {
	let items = 0;
	let value = 0;
	let bins = 0;
	for (const name of selected) {
		const node = tree.by_id.get(name);
		if (!node || !node.is_bin) continue;
		bins += 1;
		items += node.items;
		value += node.value;
	}
	return { bins, items, value };
}

// The presets: bins holding stock never counted; not counted in the window; and the `limit`
// most valuable of those.
export function preset(bins, kind, days, limit) {
	const window = Number(days) || 90;
	const holding = (bins || []).filter((b) => Number(b.on_hand_items) > 0);
	const age_of = (b) => (b.days_since === null || b.days_since === undefined ? Infinity : Number(b.days_since));
	if (kind === "never") return holding.filter((b) => age_of(b) === Infinity).map((b) => b.warehouse);
	const stale = holding.filter((b) => age_of(b) > window);
	if (kind === "stale") return stale.map((b) => b.warehouse);
	if (kind === "value") {
		return stale
			.slice()
			.sort((a, b) => (Number(b.stock_value) || 0) - (Number(a.stock_value) || 0))
			.slice(0, Number(limit) || 10)
			.map((b) => b.warehouse);
	}
	return [];
}

// The selected bins as start_count should get them: a group whose every bin is selected is sent
// as the group (so the count is titled after it), the rest as bins. Of a group and its only child,
// which cover the same bins, the child is sent: ticking Shelf A1-1 in a Row A that holds nothing
// else is a count of Shelf A1-1, not of Row A.
export function selection_for_start(tree, selected) {
	const out = [];
	const walk = (n) => {
		const state = node_state(n, selected);
		if (state === "none") return;
		if (state === "all") {
			let node = n;
			while (node.children.length === 1 && node.children[0].bins.length === node.bins.length) node = node.children[0];
			out.push(node.id);
			return;
		}
		n.children.forEach(walk);
	};
	(tree.roots || []).forEach(walk);
	return out;
}

// Open counts already covering any selected bin.
export function overlaps(open_counts, selected) {
	return (open_counts || [])
		.map((c) => ({ count: c, shared: (c.scope || []).filter((b) => selected.has(b)).length }))
		.filter((o) => o.shared > 0);
}

// ------------------------------------------------------------------ the sheet

function words(text) {
	return String(text || "")
		.toLowerCase()
		.split(/\s+/)
		.filter(Boolean);
}

export function matches_query(row, query) {
	const want = words(query);
	if (!want.length) return true;
	const hay = `${row.item_code} ${row.item_name} ${row.warehouse_name} ${row.storage_location || ""}`.toLowerCase();
	return want.every((w) => hay.includes(w));
}

export function matches_filter(row, filter) {
	switch (filter) {
		case "todo":
			return !row.counted && !row.refusal;
		case "variance":
			return row.counted && Math.abs(Number(row.variance) || 0) > TOLERANCE;
		case "recount":
			return !!row.recount;
		case "moved":
			return row.counted && Math.abs(Number(row.moved) || 0) > TOLERANCE;
		case "added":
			return !row.expected;
		default:
			return true;
	}
}

// The keys the grid shows, in sheet order. Computed when the filter, the search or the bin
// changes, never on a save: a row counted under "To count" stays on screen until the filter is
// applied again, so the rows do not jump under the cursor.
export function visible_keys(rows, view) {
	const v = view || {};
	return (rows || [])
		.filter((r) => (!v.bin || r.warehouse === v.bin) && matches_filter(r, v.filter || "all") && matches_query(r, v.query))
		.map((r) => r.key);
}

// The next key to move to from `key` in `keys` (`step` +1 or -1). With `todo`, the next row not
// yet counted (and not refused), wrapping once; null when there is none.
export function next_key(keys, by_key, key, step, todo) {
	const list = keys || [];
	if (!list.length) return null;
	const at = list.indexOf(key);
	const dir = step < 0 ? -1 : 1;
	if (!todo) {
		if (at < 0) return list[dir > 0 ? 0 : list.length - 1];
		const next = at + dir;
		return next >= 0 && next < list.length ? list[next] : null;
	}
	const n = list.length;
	const start = at < 0 ? (dir > 0 ? -1 : n) : at;
	for (let i = 1; i <= n; i++) {
		const idx = (((start + dir * i) % n) + n) % n;
		const row = by_key.get(list[idx]);
		if (row && !row.counted && !row.refusal && list[idx] !== key) return list[idx];
	}
	return null;
}

// The system quantity a row is counted against, as the System column shows it: a counted row's
// snapshot; an uncounted one's ledger quantity at the count's Counted At (`as_of_qty`, a paper
// count typed in later), else what the system shows now.
export function system_of(row) {
	const r = row || {};
	if (r.counted) return Number(r.snapshot_qty) || 0;
	if (r.as_of_qty !== null && r.as_of_qty !== undefined) return Number(r.as_of_qty) || 0;
	return Number(r.system_qty) || 0;
}

// A row as the page shows it before the server answers: counted at `qty` against the on-hand it
// will be counted against (its snapshot when it already has one, else system_of), with its item's
// variance worked out over the group's rows.
export function optimistic(rows, key, patch) {
	const out = rows.map((r) => (r.key === key ? Object.assign({}, r) : r));
	const row = out.find((r) => r.key === key);
	if (!row) return out;
	const p = patch || {};
	if ("counted_qty" in p) {
		if (p.counted_qty === null) {
			Object.assign(row, uncounted(row));
		} else {
			if (!row.counted) row.snapshot_qty = system_of(row);
			row.counted = true;
			row.counted_qty = p.counted_qty;
		}
	}
	for (const field of ["reason_code", "reason", "recount"]) if (field in p) row[field] = p[field];
	const group = group_of(row);
	const members = out.filter((r) => r.counted && group_of(r) === group);
	const total = members.reduce((s, r) => s + (Number(r.counted_qty) || 0), 0);
	const snapshot = members.length ? Number(members[members.length - 1].snapshot_qty) || 0 : 0;
	for (let i = 0; i < out.length; i++) {
		const r = out[i];
		if (group_of(r) !== group || !r.counted) continue;
		const variance = Math.abs(total - snapshot) <= TOLERANCE ? 0 : total - snapshot;
		out[i] = Object.assign({}, r, { variance, value_variance: variance * (Number(r.rate) || 0) });
	}
	return out;
}

// An expected row with its count taken away: what the sheet shows before anyone counts it.
export function uncounted(row) {
	return Object.assign({}, row, {
		key: line_key(row.item_code, row.warehouse),
		storage_location: null,
		idx: null,
		counted: false,
		counted_qty: null,
		snapshot_qty: null,
		variance: null,
		value_variance: null,
		moved: 0,
		group_lines: 0,
		reason_code: "",
		reason: "",
		recount: false,
		scan_time: null,
		counted_by: null,
	});
}

// What the bin's listing says about an item (count_desk_rules._listing_flags): the sheet's to keep.
const LISTING = ["expected", "assigned", "on_order", "is_default", "undecided"];

// A save's answer (api.count_desk.save_counts `groups`) merged into the rows: each group's rows
// replaced where its first row stood. A group that came back empty was cleared: an expected item
// gets its uncounted row back, one that was not expected goes. Returns { rows, renamed } where
// `renamed` maps a key that left to the key that took its place (so the grid can keep its row).
// The answer is about the lines, not the bin's listing, so every row in it says expected: false
// (_group_rows lists nothing); the listing flags are taken from the rows on screen, or a saved row
// would read "Not expected here" and, cleared, vanish from the sheet.
export function merge_groups(rows, groups) {
	let out = (rows || []).slice();
	const renamed = new Map();
	for (const g of groups || []) {
		const group = `${g.item_code}${KEY_SEP}${g.warehouse}`;
		const at = out.findIndex((r) => group_of(r) === group);
		const old = out.filter((r) => group_of(r) === group);
		let fresh = (g.rows || []).slice();
		const listing = old.find((r) => r.expected);
		if (listing) fresh = fresh.map((r) => Object.assign({}, r, Object.fromEntries(LISTING.map((f) => [f, listing[f]]))));
		if (!fresh.length) {
			const expected = old.find((r) => r.expected);
			fresh = expected ? [uncounted(expected)] : [];
		}
		out = out.filter((r) => group_of(r) !== group);
		const where = at < 0 ? out.length : Math.min(at, out.length);
		out.splice(where, 0, ...fresh);
		const keys = new Set(fresh.map((r) => r.key));
		for (const r of old) if (!keys.has(r.key) && fresh.length) renamed.set(r.key, fresh[0].key);
	}
	return { rows: out, renamed };
}

// A row the server refused, put back as the server has it: its item's counted rows as the sheet or
// the last answer gave them (`server_rows`), or the row uncounted when the server has none. What is
// on screen is then what is saved; the refusal stays on the row as its status. Returns merge_groups'
// { rows, renamed }.
export function revert(rows, key, server_rows) {
	const row = (rows || []).find((r) => r.key === key);
	if (!row) return { rows: (rows || []).slice(), renamed: new Map() };
	const back = (server_rows || []).length ? server_rows.map((r) => Object.assign({}, r)) : [uncounted(row)];
	return merge_groups(rows, [{ item_code: row.item_code, warehouse: row.warehouse, rows: back }]);
}

// A row added from Find item: not expected here, not counted yet. Null when the sheet has it.
export function added_row(rows, item, warehouse, warehouse_name) {
	const key = line_key(item.item_code, warehouse);
	if ((rows || []).some((r) => r.key === key)) return null;
	return uncounted({
		warehouse,
		warehouse_name: warehouse_name || warehouse,
		item_code: item.item_code,
		item_name: item.item_name || item.item_code,
		uom: item.stock_uom || item.uom || "",
		whole_number: !!item.whole_number,
		system_qty: Number(item.on_hand_here) || 0,
		reserved: 0,
		rate: Number(item.valuation_rate) || 0,
		refusal: null,
		expected: false,
		assigned: false,
		on_order: 0,
		is_default: false,
		undecided: false,
	});
}

// Where a new row goes: after the last row of its bin, else at the end, keeping shelf order.
export function insert_row(rows, row) {
	const out = (rows || []).slice();
	let at = -1;
	for (let i = 0; i < out.length; i++) if (out[i].warehouse === row.warehouse) at = i;
	if (at < 0) {
		at = out.findIndex((r) => natural_compare(r.warehouse_name, row.warehouse_name) > 0) - 1;
		if (at < -1) at = out.length - 1;
	}
	out.splice(at + 1, 0, row);
	return out;
}

// A pasted column (from a spreadsheet, or a list typed elsewhere): the first cell of each line.
// One value is an ordinary paste; this returns [] for it.
export function parse_paste(text) {
	const lines = String(text || "")
		.replace(/\r\n?/g, "\n")
		.split("\n");
	while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
	if (lines.length < 2) return [];
	return lines.map((line) => line.split("\t")[0].trim());
}

// A pasted column laid down the grid from `key`: value n goes to the nth visible row from there,
// every row the grid shows counting, refused rows (serial, batch, template) included. The grid and
// the CSV both list a refused row, so a column copied from either has a cell for it; skipping the
// row before taking its value would put every count below it on the row above. A refused row takes
// its value and keeps nothing (`refused` says so when the cell was not blank); a blank cell is
// skipped; values past the last row are dropped.
// Returns { entries: [{key, counted_qty}], problems: [{key, message}], refused: [{key, message}] }.
export function fill_plan(keys, by_key, key, values) {
	const entries = [];
	const problems = [];
	const refused = [];
	const list = keys || [];
	let i = Math.max(list.indexOf(key), 0);
	for (const text of values || []) {
		if (i >= list.length) break;
		const k = list[i];
		const row = by_key.get(k);
		i += 1;
		if (!row || !String(text).trim()) continue;
		if (row.refusal) {
			refused.push({ key: k, message: row.refusal });
			continue;
		}
		const got = evaluate(text);
		const problem = got.error || check_count(got.value, row);
		if (problem) problems.push({ key: k, message: problem });
		else entries.push({ key: k, counted_qty: got.value });
	}
	return { entries, problems, refused };
}

// "Zero the rest": every row not yet counted (and not refused), in one bin or the whole sheet.
export function zero_plan(rows, warehouse) {
	return (rows || [])
		.filter((r) => !r.counted && !r.refusal && (!warehouse || r.warehouse === warehouse))
		.map((r) => ({ key: r.key, counted_qty: 0 }));
}

// A pending save, merged: the latest value of each field per key wins.
export function queue(pending, entry) {
	const next = new Map(pending || []);
	next.set(entry.key, Object.assign({}, next.get(entry.key) || {}, entry));
	return next;
}

// The first `limit` pending entries to send, and what stays.
export function take(pending, limit) {
	const batch = [];
	const rest = new Map();
	for (const [key, entry] of pending || []) {
		if (batch.length < (limit || SAVE_LIMIT)) batch.push(entry);
		else rest.set(key, entry);
	}
	return { batch, rest };
}

// What Undo puts back for a change to `key`: the fields it changed, as they were.
export function before(row, patch) {
	const out = { key: row.key };
	if ("counted_qty" in patch) out.counted_qty = row.counted ? row.counted_qty : null;
	for (const field of ["reason_code", "reason", "recount"]) if (field in patch) out[field] = row[field];
	return out;
}

// How a row reads at a glance.
export function tone(row) {
	if (row.refusal && !row.counted) return "refused";
	if (!row.counted) return "todo";
	const v = Number(row.variance) || 0;
	if (Math.abs(v) <= TOLERANCE) return "match";
	return v > 0 ? "over" : "short";
}

// The sheet's numbers, per item (count_desk_rules.summary; test_count_desk holds them equal).
export function summary(rows) {
	const seen = new Map();
	for (const r of rows || []) {
		const key = `${r.item_code}${KEY_SEP}${r.warehouse}`;
		if (seen.has(key)) {
			if (r.recount) seen.get(key).recount = true;
			continue;
		}
		seen.set(key, {
			counted: !!r.counted,
			variance: Number(r.variance) || 0,
			value: Number(r.value_variance) || 0,
			recount: !!r.recount,
			moved: Math.abs(Number(r.moved) || 0) > TOLERANCE,
			refused: !!r.refusal,
		});
	}
	const all = Array.from(seen.values());
	const counted = all.filter((g) => g.counted);
	const matched = counted.filter((g) => Math.abs(g.variance) <= TOLERANCE);
	const countable = all.filter((g) => !g.refused || g.counted);
	const round2 = (n) => Math.round(n * 100) / 100;
	return {
		expected: countable.length,
		counted: counted.length,
		uncounted: countable.filter((g) => !g.counted).length,
		matched: matched.length,
		variances: counted.length - matched.length,
		accuracy: counted.length ? Math.round((matched.length * 1000) / counted.length) / 10 : null,
		net_value: round2(counted.reduce((s, g) => s + g.value, 0)),
		gross_value: round2(counted.reduce((s, g) => s + Math.abs(g.value), 0)),
		recount: all.filter((g) => g.recount).length,
		moved: counted.filter((g) => g.moved).length,
	};
}

// Each bin on the sheet with how far its count has got, in sheet order.
export function bins_of(rows) {
	const out = [];
	const by = new Map();
	for (const r of rows || []) {
		if (!by.has(r.warehouse)) {
			const b = { warehouse: r.warehouse, warehouse_name: r.warehouse_name, rows: 0, counted: 0, todo: 0 };
			by.set(r.warehouse, b);
			out.push(b);
		}
		const b = by.get(r.warehouse);
		if (r.refusal && !r.counted) continue;
		b.rows += 1;
		if (r.counted) b.counted += 1;
		else b.todo += 1;
	}
	return out;
}

// ------------------------------------------------------------------ the review

// One row per item with a variance, largest value first, with what posting would do now
// (count_desk_rules.post_plan applies the variance to what is on hand now, never below zero).
export function review_rows(rows) {
	const seen = new Map();
	for (const r of rows || []) {
		if (!r.counted) continue;
		const key = group_of(r);
		if (!seen.has(key)) {
			seen.set(key, Object.assign({}, r, { counted_total: 0, reason_codes: [], recount_any: false, keys: [] }));
		}
		const g = seen.get(key);
		g.counted_total += Number(r.counted_qty) || 0;
		g.keys.push(r.key);
		if (r.recount) g.recount_any = true;
		if (r.reason_code && !g.reason_codes.includes(r.reason_code)) g.reason_codes.push(r.reason_code);
		if (!g.reason && r.reason) g.reason = r.reason;
		if (!g.reason_code && r.reason_code) g.reason_code = r.reason_code;
	}
	return Array.from(seen.values())
		.filter((g) => Math.abs(Number(g.variance) || 0) > TOLERANCE)
		.map((g) => {
			const now = Number(g.system_qty) || 0;
			const target = Math.max(now + (Number(g.variance) || 0), 0);
			return Object.assign(g, { will_post: target, clamped: now + (Number(g.variance) || 0) < -TOLERANCE });
		})
		.sort(
			(a, b) =>
				Math.abs(Number(b.value_variance) || 0) - Math.abs(Number(a.value_variance) || 0) ||
				Math.abs(Number(b.variance) || 0) - Math.abs(Number(a.variance) || 0) ||
				natural_compare(a.item_name, b.item_name)
		);
}

// What to say before posting: reasons the settings require and are missing (which the server
// refuses), recount flags still up, items not counted (left unchanged) and reserved stock.
// `save` is the save queue's state (Controller.save_state): rows whose save was refused (`errors`)
// and a save refused as a whole (`fatal`, its sentence). Either holds the post back (`blocked`):
// the review is worked out from the screen, and a count that did not save is not one the server has.
export function post_check(rows, require_reason, save) {
	const review = review_rows(rows);
	const sum = summary(rows);
	const s = save || {};
	const out = {
		needs_reason: require_reason ? review.filter((g) => !g.reason_code && !g.reason) : [],
		recount: sum.recount,
		uncounted: sum.uncounted,
		reserved: review.filter((g) => Number(g.reserved) > TOLERANCE),
		nothing: sum.counted === 0,
		not_saved: Number(s.errors) || 0,
		stuck: s.fatal || null,
	};
	out.blocked = out.nothing || out.needs_reason.length > 0 || out.not_saved > 0 || !!out.stuck;
	return out;
}

// ------------------------------------------------------------------ export

const FORMULA = /^[=+\-@\t\r]/;

function csv_cell(value, text) {
	if (value === null || value === undefined) return "";
	let s = String(value);
	// A text cell that a spreadsheet would run as a formula (=, +, -, @) is written as text.
	if (text && FORMULA.test(s)) s = `'${s}`;
	return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// The sheet as CSV, one line per row. A blind count leaves out the system and variance columns.
export function to_csv(rows, opts) {
	const blind = !!(opts && opts.blind);
	const head = ["Location", "Storage Location", "Item Code", "Item Name", "Unit"];
	if (!blind) head.push("System Qty");
	head.push("Counted Qty");
	if (!blind) head.push("Variance", "Variance Value");
	head.push("Reason", "Note", "Recount", "Counted At");
	const lines = [head.join(",")];
	for (const r of rows || []) {
		const cells = [
			csv_cell(r.warehouse_name || r.warehouse, true),
			csv_cell(r.storage_location || "", true),
			csv_cell(r.item_code, true),
			csv_cell(r.item_name, true),
			csv_cell(r.uom, true),
		];
		if (!blind) cells.push(csv_cell(system_of(r), false));
		cells.push(csv_cell(r.counted ? r.counted_qty : "", false));
		if (!blind) {
			cells.push(csv_cell(r.counted ? r.variance : "", false));
			cells.push(csv_cell(r.counted ? Math.round((Number(r.value_variance) || 0) * 100) / 100 : "", false));
		}
		cells.push(csv_cell(r.reason_code || "", true), csv_cell(r.reason || "", true), r.recount ? "Yes" : "", csv_cell(r.scan_time || "", true));
		lines.push(cells.join(","));
	}
	return lines.join("\r\n") + "\r\n";
}

export function esc(value) {
	return String(value === null || value === undefined ? "" : value)
		.replace(/&/g, "&amp;")
		.replace(/</g, "&lt;")
		.replace(/>/g, "&gt;")
		.replace(/"/g, "&quot;")
		.replace(/'/g, "&#39;");
}

// The printable count sheet: a whole HTML document for a print window, grouped by bin with a box
// to write each count in. `blank` leaves the counts out (a clipboard walk, typed in afterwards);
// `blind` leaves the system quantity out.
export function print_html(count, rows, opts) {
	const o = opts || {};
	const c = count || {};
	const show_system = !o.blind;
	const groups = [];
	const by = new Map();
	for (const r of rows || []) {
		if (r.refusal && !r.counted) continue;
		if (!by.has(r.warehouse)) {
			const g = { name: r.warehouse_name || r.warehouse, rows: [] };
			by.set(r.warehouse, g);
			groups.push(g);
		}
		by.get(r.warehouse).rows.push(r);
	}
	const trails = new Map((c.scope || []).map((s) => [s.warehouse_name, s.trail]));
	const body = groups
		.map((g) => {
			const trail = trails.get(g.name);
			const lines = g.rows
				.map((r) => {
					const counted = !o.blank && r.counted ? esc(plain(r.counted_qty)) : "";
					const system = show_system ? `<td class="n">${esc(plain(system_of(r)))}</td>` : "";
					return `<tr><td>${esc(r.item_name)}<div class="code">${esc(r.item_code)}${
						r.storage_location ? ` · ${esc(r.storage_location)}` : ""
					}</div></td><td>${esc(r.uom)}</td>${system}<td class="box">${counted}</td><td class="note"></td></tr>`;
				})
				.join("");
			return `<section><h2>${esc(g.name)}${trail ? ` <small>${esc(trail)}</small>` : ""}</h2>
<table><thead><tr><th>Item</th><th>Unit</th>${show_system ? '<th class="n">System</th>' : ""}<th>Counted</th><th>Note</th></tr></thead>
<tbody>${lines}</tbody></table></section>`;
		})
		.join("");
	const title = `${c.title || c.name || "Count"}`;
	return `<!doctype html><html><head><meta charset="utf-8"><title>${esc(title)}</title>
<style>
body{font:12px/1.4 -apple-system,"Segoe UI",Roboto,Arial,sans-serif;color:#111;margin:24px;}
h1{font-size:18px;margin:0 0 2px;}
.meta{color:#555;margin-bottom:16px;}
h2{font-size:14px;margin:18px 0 6px;break-after:avoid;}
h2 small{font-weight:normal;color:#666;}
table{width:100%;border-collapse:collapse;}
th,td{border:1px solid #999;padding:5px 6px;text-align:left;vertical-align:top;}
th{background:#eee;}
.n{text-align:right;width:70px;}
.box{width:90px;}
.note{width:28%;}
.code{color:#555;font-size:11px;}
tr{break-inside:avoid;}
.sign{margin-top:28px;display:flex;gap:48px;}
.sign div{border-top:1px solid #111;padding-top:4px;min-width:220px;}
@page{margin:14mm;}
</style></head><body>
<h1>${esc(title)}</h1>
<div class="meta">${esc(c.name || "")} · ${esc(o.printed_on || "")}${c.counted_by_name ? ` · ${esc(c.counted_by_name)}` : ""}${
		o.blind ? " · blind count" : ""
	}</div>
${body || "<p>Nothing to count.</p>"}
<div class="sign"><div>Counted by</div><div>Checked by</div><div>Date</div></div>
</body></html>`;
}
