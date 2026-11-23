/*
 * Procurement Tracker (v1.811.0): the widget's pure decisions, no DOM, no Vue and no frappe, so
 * scripts/test_procurement_tracker.mjs runs them under node. The Project form script
 * (public/js/project_enhancements.js) draws and wires; this file decides.
 *
 *   view state   which grouping is on screen (by document, by item) and the filters: where the
 *                item is now, what the line is waiting on, the supplier, "needs action only",
 *                and the search box. It lives in the ADDRESS (?pt_view=items&pt_where=...), so
 *                Back, Forward, a reload and a shared link all land on the same view. The widget
 *                writes it with history.replaceState: a filter is not a new history entry.
 *   filters      which lines, documents and items a view state shows
 *   sorting      the column registries and the comparator (blanks last in both directions)
 *   labels       the Desk badge theme for a line's state, a document's status and a kind of place
 *   print        the by-item list as a plain page for the printer
 *   prices       what things cost (v1.814.0): the price columns, "No price" / "No charge", a
 *                document's total for the job's lines, the total under the by-item table
 *
 * The words a line is filtered by (line_states, location_kinds, where_now_text) are decided on
 * the server, once, by procurement_overview.py, so this file and the assistant tool cannot
 * disagree about what "late" means.
 */

// ------------------------------------------------------------------ view state

export const VIEW_DOCUMENTS = "documents";
export const VIEW_ITEMS = "items";
export const VIEWS = [VIEW_DOCUMENTS, VIEW_ITEMS];

// Every key the widget owns in the address. Prefixed, so nothing here can be mistaken for a field
// of the Project (frappe copies an address's query into frappe.route_options).
export const QUERY_KEYS = {
	view: "pt_view",
	location: "pt_where",
	status: "pt_status",
	supplier: "pt_supplier",
	action: "pt_action",
	noprice: "pt_noprice",
	q: "pt_q",
	// The saved view the layout started as (v1.816.0). Absent for the standard views.
	layout: "pt_layout",
	// The filters on columns (JSON), written only where they differ from the view's own.
	rules: "pt_rules",
};

export function default_state() {
	return { view: VIEW_DOCUMENTS, location: "", status: "", supplier: "", action: false, noprice: false, q: "" };
}

// Text, trimmed. Only text and numbers are text: an object from storage or an address is nothing, never
// "[object Object]", and never a call into its own toString.
function text(value) {
	if (typeof value === "string") return value.trim();
	if (typeof value === "number" || typeof value === "boolean") return String(value);
	return "";
}

// The view state an address's query names. Anything it does not say is the default; a view it
// does not know is the default view. Filter values are kept as given: a filter for a supplier
// this job no longer has shows nothing and says so, which is truer than silently dropping it.
export function read_query(search) {
	const params = new URLSearchParams(typeof search === "string" ? search : "");
	const state = default_state();
	const view = text(params.get(QUERY_KEYS.view));
	if (VIEWS.includes(view)) state.view = view;
	state.location = text(params.get(QUERY_KEYS.location));
	state.status = text(params.get(QUERY_KEYS.status));
	state.supplier = text(params.get(QUERY_KEYS.supplier));
	state.action = text(params.get(QUERY_KEYS.action)) === "1";
	state.noprice = text(params.get(QUERY_KEYS.noprice)) === "1";
	state.q = text(params.get(QUERY_KEYS.q));
	return state;
}

// The address's query with the view state written into it: "?a=1&pt_view=items", or "". Other
// people's keys are kept in place, defaults are left out (a plain address is the plain view), and
// the result is stable, so writing the same state twice changes nothing.
export function write_query(search, state) {
	const params = new URLSearchParams(typeof search === "string" ? search : "");
	const s = Object.assign(default_state(), state || {});
	const set = (key, value) => {
		if (value) params.set(key, value);
		else params.delete(key);
	};
	set(QUERY_KEYS.view, s.view === VIEW_DOCUMENTS || !VIEWS.includes(s.view) ? "" : s.view);
	set(QUERY_KEYS.location, text(s.location));
	set(QUERY_KEYS.status, text(s.status));
	set(QUERY_KEYS.supplier, text(s.supplier));
	set(QUERY_KEYS.action, s.action ? "1" : "");
	set(QUERY_KEYS.noprice, s.noprice ? "1" : "");
	set(QUERY_KEYS.q, text(s.q));
	const out = params.toString();
	return out ? `?${out}` : "";
}

// frappe.route_options with the widget's keys taken out, or null when nothing is left. frappe
// fills route_options from the address on every route change and never empties it; left alone,
// the next list view the person opens would try to filter on "pt_view".
export function without_own_keys(options) {
	if (!options || typeof options !== "object") return options || null;
	const own = Object.values(QUERY_KEYS);
	const out = {};
	Object.keys(options).forEach((key) => {
		if (!own.includes(key)) out[key] = options[key];
	});
	return Object.keys(out).length ? out : null;
}

export function is_filtered(state) {
	const s = state || {};
	return !!(s.location || s.status || s.supplier || s.action || s.noprice);
}

export function is_narrowed(state) {
	return is_filtered(state) || tokenize(state && state.q).length > 0;
}

export function cleared(state) {
	return Object.assign(default_state(), { view: (state && state.view) || VIEW_DOCUMENTS });
}

// ------------------------------------------------------------------ filters

export function tokenize(term) {
	return String(term || "")
		.toLowerCase()
		.split(/\s+/)
		.filter(Boolean);
}

const LINE_SEARCH_FIELDS = [
	"item_code",
	"item_name",
	"warehouse",
	"where_now_text",
	"supplier",
	"line_state_label",
	"state_label",
	"mr",
	"rfq",
	"sq",
	"po",
	"pr",
	"pi",
	"stock_entry",
	"order_status",
	"receive_status",
];

function haystack(row) {
	const parts = LINE_SEARCH_FIELDS.map((f) => row[f]).filter(Boolean);
	(row.suppliers || []).forEach((s) => parts.push(s));
	(row.received_at instanceof Array ? row.received_at : []).forEach((w) => parts.push(w));
	const docs = row.documents || {};
	Object.keys(docs).forEach((dt) => (docs[dt] || []).forEach((name) => parts.push(name)));
	return parts.join(" ").toLowerCase();
}

export function row_matches_search(row, tokens) {
	if (!tokens || !tokens.length) return true;
	const hay = haystack(row || {});
	return tokens.every((t) => hay.includes(t));
}

// Whether a line (or a by-item row) has an order line with no price on it: the lines purchasing has
// to fix. A line says it with price_state; an item says how many of its lines (on a submitted order,
// or on a draft one that will be). Nothing for a person who was sent no prices.
export function has_no_price(row) {
	const r = row || {};
	if (r.price_state === "no_price") return true;
	return (Number(r.no_price_lines) || 0) > 0 || (Number(r.draft_no_price_lines) || 0) > 0;
}

// Whether a line (or a by-item row: same keys, in the plural) passes the filters. `ctx` is optional:
// { rules, mode, prices, doctype } adds the view's column filters (see rule_matches), which are
// separate from the state because the address does not carry them.
export function row_matches_filters(row, state, ctx) {
	const r = row || {};
	const s = state || {};
	if (ctx && ctx.rules && ctx.rules.length && !rules_match(ctx.rules, r, ctx)) return false;
	if (s.action && !r.needs_action) return false;
	if (s.noprice && !has_no_price(r)) return false;
	if (s.status && !(r.line_states || r.states || []).includes(s.status)) return false;
	if (s.location && !(r.location_kinds || []).includes(s.location)) return false;
	if (s.supplier) {
		const suppliers = r.suppliers || (r.supplier ? [r.supplier] : []);
		if (!suppliers.includes(s.supplier)) return false;
	}
	return true;
}

function doc_level_matches(doc, tokens) {
	const hay = [doc.name, doc.supplier, doc.status].filter(Boolean).join(" ").toLowerCase();
	return tokens.every((t) => hay.includes(t));
}

// The document tree a view state shows. The filters apply to lines: a document with no line
// left is not shown. The search then works as it always has: a document whose own name, supplier
// or status matches keeps all its (filtered) lines, otherwise only the lines that match.
// Returns new group and document objects; the lines are the same objects, never copies.
export function filter_groups(groups, state, ctx) {
	const list = Array.isArray(groups) ? groups : [];
	const tokens = tokenize(state && state.q);
	const filtered = is_filtered(state) || !!(ctx && ctx.rules && ctx.rules.length);
	if (!tokens.length && !filtered) return list;
	const out = [];
	list.forEach((group) => {
		const docs = [];
		(group.documents || []).forEach((doc) => {
			const all = doc.items || [];
			// A line's price cells depend on the document it sits in, so the group's type goes along.
			const line_ctx = ctx && ctx.rules && ctx.rules.length ? Object.assign({}, ctx, { doctype: group.doctype }) : ctx;
			const base = filtered ? all.filter((row) => row_matches_filters(row, state, line_ctx)) : all;
			if (filtered && !base.length) return;
			let items = base;
			if (tokens.length) {
				const hits = base.filter((row) => row_matches_search(row, tokens));
				if (!hits.length && !doc_level_matches(doc, tokens)) return;
				items = hits.length ? hits : base;
			}
			docs.push(Object.assign({}, doc, { items, total_items: all.length }));
		});
		if (docs.length) out.push(Object.assign({}, group, { documents: docs }));
	});
	return out;
}

export function filter_items(items, state, ctx) {
	const list = Array.isArray(items) ? items : [];
	const tokens = tokenize(state && state.q);
	const ruled = !!(ctx && ctx.rules && ctx.rules.length);
	if (!tokens.length && !is_filtered(state) && !ruled) return list;
	return list.filter((row) => row_matches_filters(row, state, ctx) && row_matches_search(row, tokens));
}

// How many distinct lines a tree holds. The same line sits under every document its chain
// touches, so lines are counted by their row_id, once.
export function count_lines(groups) {
	const seen = new Set();
	(groups || []).forEach((g) =>
		(g.documents || []).forEach((d) => (d.items || []).forEach((row) => seen.add(row.row_id)))
	);
	return seen.size;
}

// "3 of 7 items" on a document a filter has narrowed, else "7 item(s)".
export function item_count_text(doc) {
	const shown = (doc.items || []).length;
	const total = doc.total_items;
	if (total !== undefined && total !== shown) return `${shown} of ${total} items`;
	return `${shown} item(s)`;
}

// `value` as HTML with every search word wrapped in <mark>. The text is split on the words
// first and each piece escaped on its own, so a name is never read as markup and a search for
// "amp" cannot cut an escaped "&amp;" in half. `esc` is the caller's HTML escape.
export function highlight_html(value, term, esc) {
	const raw = value === null || value === undefined ? "" : String(value);
	const tokens = tokenize(term);
	if (!tokens.length || !raw) return esc(raw);
	const pattern = tokens.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
	// A capturing group: split() then returns the matches too, at the odd positions.
	return raw
		.split(new RegExp(`(${pattern})`, "gi"))
		.map((part, i) => (i % 2 ? `<mark>${esc(part)}</mark>` : esc(part)))
		.join("");
}

// ------------------------------------------------------------------ sorting

// What Spent is and is not, in the words of the column's tooltip (the same as the CHANGELOG and the
// tracker map). It is the sum of the order lines' own amounts: a discount applied to the whole order,
// taxes and freight are not in it, and an order closed short counts in full. Example: 10 units at
// $10 with a 10% discount on the whole order is $100 here, where the order's net is $90.
export const SPENT_NOTE =
	"What was ordered for this job: the sum of the order lines' own amounts. It leaves out a discount on the whole order, taxes and freight, and counts an order closed short in full. ERPNext holds no bills, so it is not billed or paid.";
export const AMOUNT_NOTE =
	"The line's own amount. It leaves out a discount on the whole order, taxes and freight.";

// Most urgent first: one click on Status puts what somebody has to chase at the top. The same
// order as procurement_overview.STATE_RANK, which decides the one state a line is shown as.
export const STATE_RANK = ["late", "to_order", "ordered", "partly_received", "received", "closed", "quote_only", "invoice_only"];

// ------------------------------------------------------------------ the columns

/**
 * Every column of the two tables, declared once, outside the Vue options, so the template, the
 * comparator, the picker and the filters cannot drift apart: a header rendered against a key the
 * sorter does not know is a click that silently does nothing. public/data (the server's catalog,
 * data/procurement_tracker_views.json) lists the same keys, and scripts/test_procurement_tracker.mjs
 * holds the two to each other.
 *
 * A column is:
 *   label, cls, hint   what the header says, its CSS class, its tooltip
 *   type               how a sort compares it: "text", "number" or "rank"
 *   value(row, ctx)    what a sort orders by (ctx is the document's type, for a line's price)
 *   sortable: false    a cell with no single value to order by (Doc Chain, Documents)
 *   price: true        a price column: it exists only for a person who was sent prices
 *   filter             what a filter on this column can be: "text", "choice", "number" or "date"
 *   fv(row, ctx)       what a filter reads: the text, the choices (an array), the number, the date
 *   ft(row)            (choice columns) the text a "contains" test reads, when it is not the choices
 *   options            (choice columns) the filter list that names the choices: "locations", "states", "suppliers"
 *
 * Doc Chain is deliberately not sortable. It is one cell holding up to seven chain nodes; there
 * is no single value to order by, and a click handler would mean inventing an ordering the column
 * does not show.
 */
const joined = (list) => (Array.isArray(list) ? list.filter(Boolean).join(", ") : list || "");
const document_names = (r) => {
	const names = [];
	Object.keys(r.documents || {}).forEach((dt) => (r.documents[dt] || []).forEach((n) => names.push(n)));
	["mr", "rfq", "sq", "po", "pr", "pi", "stock_entry"].forEach((k) => r[k] && names.push(r[k]));
	return names.join(" ");
};
const qty_column = (label, field) => ({ label, cls: "qty-col", type: "number", filter: "number", value: (r) => r[field], fv: (r) => r[field] });

// Both registries are made without a prototype: a column key read from storage or a link is looked up here,
// and "constructor", "toString" or "__proto__" must not find anything.
export const LINE_COLUMNS = Object.assign(Object.create(null), {
	item: { label: "Item", type: "text", filter: "text", value: (r) => `${r.item_code || ""} ${r.item_name || ""}`.trim(), fv: (r) => `${r.item_code || ""} ${r.item_name || ""}`.trim() },
	where: { label: "Where it is now", type: "text", filter: "choice", options: "locations", value: (r) => r.where_now_text, fv: (r) => r.location_kinds || [], ft: (r) => r.where_now_text || "" },
	requested: qty_column("Requested", "requested_qty"),
	ordered: qty_column("Ordered", "ordered_qty"),
	received: qty_column("Received", "received_qty"),
	to_order: qty_column("Still to order", "to_order_qty"),
	waiting: qty_column("Still to arrive", "waiting_qty"),
	// The price columns exist only for a person who was sent prices (`price: true`, see
	// shown_columns). What a cell holds depends on the document it sits in (an order's rate, a
	// receipt's value, a quote), so the comparator gets that document's type as its second argument.
	// A line with no price has no rate to order by: it is blank, and blanks sort last in both directions.
	rate: { label: "Rate", cls: "money-col", type: "number", filter: "number", price: true, value: (r, doctype) => price_value(r, doctype, "rate"), fv: (r, doctype) => price_value(r, doctype, "rate") },
	amount: { label: "Amount", cls: "money-col", type: "number", filter: "number", price: true, hint: AMOUNT_NOTE, value: (r, doctype) => price_value(r, doctype, "amount"), fv: (r, doctype) => price_value(r, doctype, "amount") },
	status: { label: "Status", type: "rank", filter: "choice", options: "states", value: (r) => r.line_state, ranks: STATE_RANK, fv: (r) => r.line_states || [], ft: (r) => r.line_state_label || "" },
	due: { label: "Due", type: "text", filter: "date", value: (r) => r.expected_date, fv: (r) => r.expected_date },
	supplier: { label: "Supplier", type: "text", filter: "choice", options: "suppliers", value: (r) => r.supplier, fv: (r) => (r.supplier ? [r.supplier] : []), ft: (r) => r.supplier || "" },
	received_at: { label: "Received at", type: "text", filter: "text", value: (r) => r.received_at, fv: (r) => joined(r.received_at) },
	unit: { label: "Unit", type: "text", filter: "text", value: (r) => r.stock_uom, fv: (r) => r.stock_uom || "" },
	doc_chain: { label: "Doc Chain", type: "text", filter: "text", sortable: false, value: () => null, fv: (r) => document_names(r) },
});

// The by-item table. More columns than a document's: the date the item is due, its suppliers and documents.
export const ITEM_COLUMNS = Object.assign(Object.create(null), {
	item: LINE_COLUMNS.item,
	requested: LINE_COLUMNS.requested,
	ordered: LINE_COLUMNS.ordered,
	received: LINE_COLUMNS.received,
	to_order: LINE_COLUMNS.to_order,
	waiting: LINE_COLUMNS.waiting,
	// Sorted by what was paid per unit over the lines that have a price (their average), and by what
	// was spent. An item with no priced line is blank: last in both directions.
	unit_price: { label: "Unit price", cls: "money-col", type: "number", filter: "number", price: true, value: (r) => r.unit_price_avg, fv: (r) => r.unit_price_avg },
	spent: { label: "Spent", cls: "money-col", type: "number", filter: "number", price: true, hint: SPENT_NOTE, value: (r) => r.spent, fv: (r) => r.spent },
	// What arrived, at the order lines' rates (the figure Spent's small print already carries).
	received_value: { label: "Received value", cls: "money-col", type: "number", filter: "number", price: true, value: (r) => r.received_value, fv: (r) => r.received_value },
	// How many of the item's order lines have no rate: what purchasing has to fix.
	no_price: { label: "Lines with no price", cls: "qty-col", type: "number", filter: "number", price: true, value: (r) => no_price_count(r), fv: (r) => no_price_count(r) },
	status: { label: "Status", type: "rank", filter: "choice", options: "states", value: (r) => r.state, ranks: STATE_RANK, fv: (r) => r.states || [], ft: (r) => r.state_label || "" },
	where: LINE_COLUMNS.where,
	due: LINE_COLUMNS.due,
	supplier: { label: "Supplier", type: "text", filter: "choice", options: "suppliers", value: (r) => joined(r.suppliers), fv: (r) => r.suppliers || [], ft: (r) => joined(r.suppliers) },
	documents: { label: "Documents", type: "text", filter: "text", sortable: false, value: () => null, fv: (r) => document_names(r) },
	received_at: LINE_COLUMNS.received_at,
	unit: LINE_COLUMNS.unit,
	lines: { label: "Lines on this job", cls: "qty-col", type: "number", filter: "number", value: (r) => r.line_count, fv: (r) => r.line_count },
});

function no_price_count(r) {
	if (r.no_price_lines === undefined && r.draft_no_price_lines === undefined) return null;
	return (Number(r.no_price_lines) || 0) + (Number(r.draft_no_price_lines) || 0);
}

/**
 * Blanks sort last in BOTH directions, so the return value is never multiplied by the sort
 * direction. A missing value is not "before A": it is absent information, and absent
 * information belongs at the bottom whichever way you sorted.
 *
 * Zero is not blank. An ordered quantity of 0 is a real value and sorts with the numbers, which
 * is why these are strict comparisons and not a falsy check.
 *
 * Returns null when both values are present, meaning "no opinion, go and compare them".
 */
export function blank_order(a, b) {
	const a_blank = a === null || a === undefined || a === "";
	const b_blank = b === null || b === undefined || b === "";
	if (!a_blank && !b_blank) return null;
	if (a_blank && b_blank) return 0;
	return a_blank ? 1 : -1;
}

export function compare(a, b, column) {
	if (column.type === "number") return Number(a) - Number(b);
	if (column.type === "rank") {
		// An unrecognised value sorts after every known one rather than at -1, which would
		// silently put it first.
		const ai = column.ranks.indexOf(a);
		const bi = column.ranks.indexOf(b);
		return (ai < 0 ? column.ranks.length : ai) - (bi < 0 ? column.ranks.length : bi);
	}
	// `numeric` matters: this site's item codes are 417-080, 417-100, 2622-010, which a plain
	// string compare orders wrong.
	return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

// The rows in the order a sort asks for, as a NEW array: Array.prototype.sort mutates, and
// mutating a list Vue is rendering is an infinite reactivity loop. No sort, or a column the
// registry does not know, returns the rows as they came.
export function sort_rows(rows, columns, sort, context) {
	const list = rows || [];
	const column = sort && columns[sort.key];
	if (!column || column.sortable === false) return list;
	const dir = sort.dir === "desc" ? -1 : 1;
	return list.slice().sort((a, b) => {
		const av = column.value(a, context);
		const bv = column.value(b, context);
		const blanks = blank_order(av, bv);
		// Not multiplied by dir: see blank_order.
		if (blanks !== null) return blanks;
		return dir * compare(av, bv, column);
	});
}

function column_view(registry, key) {
	const c = registry[key];
	return { key: key, label: c.label, cls: c.cls || "", hint: c.hint || "", sortable: c.sortable !== false };
}

// Every column of a registry, less the price columns when the person was sent no prices: the picker's
// list, and what the tracker had before views.
export function visible_columns(registry, prices) {
	return Object.keys(registry)
		.filter((key) => prices || !registry[key].price)
		.map((key) => column_view(registry, key));
}

// What a view puts on screen: its columns, in its order, less any the person may not see.
//   { columns: [{ key, label, cls, hint, sortable }], withheld: ["Unit price", ...] }
// A price column is withheld from a person who was sent no prices: the server leaves the prices out of
// the response for them, so there is nothing to draw, and the view says so instead of showing an empty
// column. Unknown keys (a column a later release removed) are skipped. A view left with nothing to show
// falls back to every column the person may see in the standard order.
export function shown_columns(keys, mode, prices) {
	const registry = registry_for(mode);
	const out = [];
	const withheld = [];
	const seen = new Set();
	(Array.isArray(keys) ? keys : []).forEach((key) => {
		if (typeof key !== "string" || !registry[key] || seen.has(key)) return;
		seen.add(key);
		if (registry[key].price && !prices) withheld.push(registry[key].label);
		else out.push(column_view(registry, key));
	});
	if (!out.length) {
		STANDARD_COLUMNS[mode === VIEW_ITEMS ? VIEW_ITEMS : VIEW_DOCUMENTS].forEach((key) => {
			if (registry[key] && (prices || !registry[key].price)) out.push(column_view(registry, key));
		});
	}
	return { columns: out, withheld: withheld };
}

// The sentence for a view whose price columns are not shown to this person.
export function withheld_text(labels) {
	if (!labels || !labels.length) return "";
	return `${labels.join(", ")} ${labels.length === 1 ? "is" : "are"} not shown to you: prices are for people who may read Purchase Orders.`;
}

// The sort a click on a header leads to: ascending, descending, then none. Without the third
// step line order is lost until the form is reloaded, and line order means something on a
// request: it is how the job was grouped.
export function next_sort(current, key, columns) {
	if (!columns[key] || columns[key].sortable === false) return current || null;
	if (!current || current.key !== key) return { key, dir: "asc" };
	if (current.dir === "asc") return { key, dir: "desc" };
	return null;
}

// ------------------------------------------------------------------ labels

// The words for a line's state: procurement_overview.STATE_LABELS, the same list.
export const STATE_LABELS = {
	late: "Late",
	to_order: "To order",
	ordered: "Ordered",
	partly_received: "Partly received",
	received: "Received",
	closed: "Closed",
	quote_only: "Quote only",
	invoice_only: "Bill only",
};

// The Desk badge theme (frappe's .es-badge data-theme) for the state a line is shown as.
const STATE_THEMES = {
	late: "red",
	to_order: "orange",
	ordered: "blue",
	partly_received: "amber",
	received: "green",
	closed: "gray",
	quote_only: "gray",
	invoice_only: "gray",
};

export function state_theme(state) {
	return STATE_THEMES[state] || "gray";
}

/**
 * The badge theme for a status string: ours first, by exact match, then ERPNext's own.
 *
 * The exact match is not decoration. A substring test cannot separate "Partially Ordered" from
 * "Ordered" (both contain "ordered") or "Partially Received" from "Received", so it once painted
 * a half-done line and a finished one alike (v1.194.0). The fallback, kept for ERPNext's status
 * strings on the chain's other documents, tests `partial` before the general terms for the same
 * reason.
 */
const STATUS_THEMES = {
	"Not Ordered": "orange",
	"Partially Ordered": "amber",
	Ordered: "blue",
	"Over Ordered": "violet",
	"Not Received": "orange",
	"Partially Received": "amber",
	Received: "green",
	"Over Received": "violet",
};

export function status_theme(status) {
	if (!status) return "gray";
	if (STATUS_THEMES[status]) return STATUS_THEMES[status];
	const s = String(status).toLowerCase();
	if (s.includes("draft")) return "gray";
	if (s.includes("partial") || s.includes("partly")) return "amber";
	if (s.includes("cancel")) return "red";
	if (s.includes("overdue")) return "red";
	if (s.includes("closed") || s.includes("stopped")) return "gray";
	if (s.includes("received") || s === "completed" || s === "paid" || s === "delivered") return "green";
	if (s.includes("to receive") || s.includes("to bill") || s.includes("unpaid") || s.includes("pending")) return "orange";
	if (s.includes("submit") || s.includes("ordered")) return "blue";
	return "orange";
}

// A kind of place, as a tone for its chip: somewhere the stock can be picked up, somewhere it
// went, or a gap in the record.
const PLACE_TONES = {
	project_shelf: "shelf",
	job_site: "site",
	shop_bin: "bin",
	vehicle: "bin",
	other_site: "site",
	boneyard: "bin",
	taken: "gone",
	returned: "gone",
	unrecorded: "gap",
};

export function place_tone(kind) {
	return PLACE_TONES[kind] || "bin";
}

// Quantities arrive as floats. "4" reads as a quantity; "4.0" reads as a rounding artefact, and
// a column of them is noise. "-" is "nobody asked", which is not the same fact as 0.
export function fmt_qty(value) {
	if (value === null || value === undefined || value === "") return "-";
	const n = Number(value);
	if (!isFinite(n)) return "-";
	return Number.isInteger(n) ? String(n) : String(parseFloat(n.toFixed(3)));
}

// A sentence under the where-now of a line whose item is on more than one line of the job: the
// answer is the item's, not the line's (ERPNext keeps stock by item).
export function shared_where_note(row) {
	const lines = Number(row && row.where_now_lines) || 0;
	if (lines < 2 || !(row.where_now || []).length) return "";
	return `For all ${lines} lines of this item on the job.`;
}

// A Supplier Return under an item's places, after its name and quantity: where it stands. The
// goods of one that is still being arranged are still here, which the status alone does not say.
export function return_words(entry) {
	const e = entry || {};
	const status = String(e.status || "").trim();
	if (e.gone) return status ? `sent back (${status})` : "sent back";
	return status ? `to go back (${status})` : "to go back";
}

// What the filters say is on screen, in words, for the line under the toolbar and the printed
// list's heading: "Late · Project Shelf · needs action".
export function describe_filters(state, options, rules, mode) {
	const s = state || {};
	const o = options || {};
	const label = (list, value) => {
		const hit = (list || []).find((x) => x.value === value);
		return hit ? hit.label : value;
	};
	const parts = [];
	if (s.status) parts.push(label(o.states, s.status));
	if (s.location) parts.push(label(o.locations, s.location));
	if (s.supplier) parts.push(s.supplier);
	if (s.action) parts.push("needs action");
	if (s.noprice) parts.push("no price");
	(rules || []).forEach((rule) => parts.push(describe_rule(rule, mode, o)));
	if (tokenize(s.q).length) parts.push(`"${String(s.q).trim()}"`);
	return parts.join(" · ");
}

// ------------------------------------------------------------------ prices

export const NO_PRICE_TEXT = "No price";
export const NO_CHARGE_TEXT = "No charge";

// An amount in the company's currency: "$1,234.50", and a rate to four places when it has them
// ("$0.0125"). Always en-US, as the rest of this tracker's words are; "-" for a value that is not a
// number. Money is never shown through fmt_qty: "4" is a quantity, "$4.00" is a price.
export function fmt_money(value, currency) {
	if (value === null || value === undefined || value === "") return "-";
	const n = Number(value);
	if (!isFinite(n)) return "-";
	try {
		return new Intl.NumberFormat("en-US", {
			style: "currency",
			currency: currency || "USD",
			minimumFractionDigits: 2,
			maximumFractionDigits: 4,
		}).format(n);
	} catch (e) {
		return (currency ? currency + " " : "") + n.toFixed(2);
	}
}

function plural(n, one, many) {
	return `${n} ${n === 1 ? one : many}`;
}

// What a line's price cells hold, by the kind of document it is shown in. `null` is "nothing to say":
// a Material Request line, a Request for Quotation line and a bill line have no price, and their cells
// stay empty. An order shows its own line; a receipt shows what arrived, at the ORDER's rate; a
// quotation shows what it quoted (a quote, never a spend).
//   { kind: "order"|"receipt"|"quote", state: "priced"|"no_price"|"no_charge", rate, amount, uom, received }
export function price_cells(row, doctype) {
	const r = row || {};
	if (doctype === "Purchase Order" && r.price_state) {
		return { kind: "order", state: r.price_state, rate: r.po_rate, amount: r.po_amount, uom: r.po_rate_uom, received: r.received_value, draft: false };
	}
	if (doctype === "Purchase Receipt" && r.receipt_state) {
		return { kind: "receipt", state: r.receipt_state, rate: r.receipt_rate, amount: r.receipt_value, uom: r.po_rate_uom, received: null, draft: r.receipt_docstatus === 0 };
	}
	if (doctype === "Supplier Quotation" && r.sq_item) {
		return { kind: "quote", state: "priced", rate: r.quote_rate, amount: r.quote_amount, uom: null, received: null, draft: r.quote_docstatus === 0 };
	}
	return null;
}

// The number a sort uses: only a priced line has one. "No price" is blank, not 0.
function price_value(row, doctype, field) {
	const cells = price_cells(row, doctype);
	if (!cells || cells.state !== "priced") return null;
	const value = cells[field];
	return value === undefined ? null : value;
}

// The text of one price cell: the amount, "No price", "No charge", or "" for a line that has no price
// to speak of.
export function price_text(cells, field, currency) {
	if (!cells) return "";
	if (cells.state === "no_price") return NO_PRICE_TEXT;
	if (cells.state === "no_charge") return NO_CHARGE_TEXT;
	const value = field === "amount" ? cells.amount : cells.rate;
	return value === null || value === undefined ? "" : fmt_money(value, currency);
}

// The tooltip of a line's amount cell: what it is the amount of, and what has arrived at that rate.
export function price_title(cells, currency) {
	if (!cells) return "";
	if (cells.kind === "quote") {
		return (cells.draft ? "A quote on a draft quotation. " : "A quote. ") + "It is not added into what was spent.";
	}
	if (cells.kind === "receipt") {
		if (cells.draft) return "A draft receipt: nothing has been received on it yet. Valued at the order line's rate.";
		return "What this receipt brought in, at the order line's rate. ERPNext holds no bills, so this is not what was billed or paid.";
	}
	if (cells.state === "no_price") return "The order line has no rate. It is left out of every total.";
	if (cells.state === "no_charge") return "A free line. It is left out of every total.";
	const parts = ["The order line's amount"];
	if (cells.uom) parts[0] += `, rate per ${cells.uom}`;
	if (cells.received !== null && cells.received !== undefined && Number(cells.received) > 0) {
		parts.push(`received so far: ${fmt_money(cells.received, currency)} at that rate`);
	}
	parts.push(AMOUNT_NOTE);
	parts.push("ERPNext holds no bills, so this is what was ordered, not what was billed or paid");
	return parts.join(" · ");
}

// A document's total in its header: "$1,200.00", "$12.00 received", "$80.00 quoted", with what it
// leaves out ("2 lines with no price") and, where the document has other lines than this job's, that
// it is this job's lines only. `title` spells it out, including the whole document's own total.
export function doc_price_text(total, doctype, currency) {
	if (!total) return { text: "", title: "" };
	const draft = total.docstatus === 0;
	const suffix =
		doctype === "Purchase Receipt"
			? draft ? " on a draft receipt" : " received"
			: doctype === "Supplier Quotation"
				? draft ? " quoted (draft)" : " quoted"
				: "";
	const parts = [];
	if (total.lines > 0) parts.push(fmt_money(total.amount, currency) + suffix);
	else if (total.no_price_lines > 0) parts.push(NO_PRICE_TEXT);
	else if (total.no_charge_lines > 0) parts.push(NO_CHARGE_TEXT);
	if (total.lines > 0 && total.no_price_lines > 0) parts.push(plural(total.no_price_lines, "line", "lines") + " with no price");
	if (total.partial) parts.push("this job's lines");
	const title = [];
	if (total.partial) {
		title.push(`This job's ${total.job_lines} of the document's ${total.doc_lines} lines`);
		if (total.doc_total !== null && total.doc_total !== undefined) {
			title.push(`the whole document is ${fmt_money(total.doc_total, currency)}`);
		}
	}
	if (total.no_price_lines > 0) title.push("a line with no price is left out of the total");
	if (doctype === "Supplier Quotation") title.push("a quote, not a spend");
	if (doctype === "Purchase Receipt") title.push("valued at the order line's rate");
	return { text: parts.join(" · "), title: title.join(" · ") };
}

// The Unit price and Spent cells of a by-item row, and the lines of small print under them.
//   { unit, unit_notes: [], spent, spent_notes: [] }
// Unit price is the price per stock unit across the lines that have one: one price when they
// agree, "low – high" when they differ. A missing price is never a zero: it reads "No price" and is
// left out, and where a sum leaves lines out it says how many.
export function item_price_view(row, currency) {
	const r = row || {};
	const priced = Number(r.spent_lines) || 0;
	const none = (Number(r.no_price_lines) || 0) + (Number(r.draft_no_price_lines) || 0);
	const free = Number(r.no_charge_lines) || 0;
	const out = { unit: "", unit_notes: [], spent: "", spent_notes: [] };
	if (priced > 0) {
		const low = r.unit_price_low;
		const high = r.unit_price_high;
		out.unit = low === high ? fmt_money(low, currency) : `${fmt_money(low, currency)} – ${fmt_money(high, currency)}`;
		out.spent = fmt_money(r.spent, currency);
		if (low !== high) out.unit_notes.push(`${fmt_money(r.unit_price_avg, currency)} on average`);
		// Lines written in different units: the range may be two prices for two things.
		const uoms = Array.isArray(r.price_uoms) ? r.price_uoms : [];
		if (uoms.length > 1) out.unit_notes.push(`Priced per ${uoms.join(" and per ")}`);
		if ((Number(r.no_price_lines) || 0) > 0) {
			out.spent_notes.push(plural(r.no_price_lines, "line", "lines") + " with no price");
		}
		if (free > 0) out.spent_notes.push(plural(free, "line", "lines") + " no charge");
	} else if (none > 0) {
		out.unit = NO_PRICE_TEXT;
		out.spent = NO_PRICE_TEXT;
		out.spent_notes.push(plural(none, "line", "lines") + " with no price");
	} else if (free > 0) {
		out.unit = NO_CHARGE_TEXT;
		out.spent = NO_CHARGE_TEXT;
	}
	if (r.received_value !== null && r.received_value !== undefined && Number(r.received_value) > 0) {
		out.spent_notes.push(`Received ${fmt_money(r.received_value, currency)}`);
	}
	if (r.draft_spent !== null && r.draft_spent !== undefined) {
		out.spent_notes.push(`${fmt_money(r.draft_spent, currency)} on draft orders, not counted`);
	}
	if ((Number(r.quote_count) || 0) > 0) {
		const low = r.quote_low;
		const high = r.quote_high;
		const range = low === high ? fmt_money(low, currency) : `${fmt_money(low, currency)} – ${fmt_money(high, currency)}`;
		const drafts = Number(r.quote_draft_count) || 0;
		const label = drafts === 0 ? "" : drafts >= Number(r.quote_count) ? " (draft)" : ` (${drafts} draft)`;
		out.unit_notes.push(`Quoted ${range}${label}`);
	}
	return out;
}

// What the by-item view totals under its table, over the rows it is SHOWING (so it follows every
// filter and the search): the amount spent, and how many lines the sum leaves out.
//   { amount, lines, no_price_lines, no_charge_lines, text, notes: [] }
export function spent_summary(items, currency) {
	const rows = Array.isArray(items) ? items : [];
	const sum = (key) => rows.reduce((total, r) => total + (Number(r[key]) || 0), 0);
	const amount = Math.round(sum("spent") * 1e6) / 1e6;
	const out = { amount: amount, lines: sum("spent_lines"), no_price_lines: sum("no_price_lines"), no_charge_lines: sum("no_charge_lines") };
	out.text = fmt_money(amount, currency);
	out.notes = [];
	if (out.no_price_lines > 0) out.notes.push(plural(out.no_price_lines, "line", "lines") + " with no price");
	if (out.no_charge_lines > 0) out.notes.push(plural(out.no_charge_lines, "line", "lines") + " no charge");
	return out;
}

// ------------------------------------------------------------------ saved views (v1.816.0)

/*
 * A VIEW is a named layout for one of the two groupings: which columns show and in what order, the
 * sort, and the filters (the five of the toolbar, and any number of filters on a column). Three kinds
 * of view reach the widget:
 *
 *   the standard views   the tracker as it was before views (data/procurement_tracker_views.json,
 *                        ids std-documents and std-items; built in here as the fallback, so the tracker
 *                        still draws when the views call fails)
 *   the other presets    shipped with the app, read-only, from the same file
 *   a person's own       stored on the server per user (Procurement Tracker View), never shared
 *
 * The widget keeps two objects: `view`, the flat state the address always carried (grouping and the
 * five toolbar filters and the search), and `layout`, { id, columns, sort, rules }: the view the person
 * picked, with any changes made on top of it. The two together are the unsaved layout; layout_of() writes
 * them as a view's layout and view_changed() says whether they differ from the view's own.
 *
 * Where a layout comes from when the tracker opens (resolve_start): the ADDRESS wins when it names a view
 * or a filter (a shared link, a reload, Back and Forward); changes made on top of that same view come
 * back from what the browser remembered; with no address at all, the browser's last layout is used.
 */

export const STANDARD_VIEW_IDS = { documents: "std-documents", items: "std-items" };

// The columns of the standard views, in order: the tracker as it has always been.
export const STANDARD_COLUMNS = {
	documents: ["item", "where", "requested", "ordered", "received", "rate", "amount", "status", "doc_chain"],
	items: ["item", "requested", "ordered", "received", "unit_price", "spent", "status", "where", "due", "supplier", "documents"],
};

// The server's limits (data/procurement_tracker_views.json); the browser holds a layout to the same ones.
export const LIMITS = { max_name_length: 60, max_rules: 12, max_text_length: 120, max_choices: 25, max_choice_length: 140 };

export const OPERATORS = {
	text: ["contains", "blank", "not_blank"],
	choice: ["any_of", "contains", "blank", "not_blank"],
	number: ["between", "blank", "not_blank"],
	date: ["between", "blank", "not_blank"],
};

export const OPERATOR_LABELS = {
	contains: "contains",
	any_of: "is any of",
	between: "is between",
	blank: "is blank",
	not_blank: "is not blank",
};

const FILTERS_OFF = { location: "", status: "", supplier: "", action: false, noprice: false };
const BASIC_FILTERS = ["location", "status", "supplier", "action", "noprice"];
const STORAGE_PREFIX = "sf_pt_layout_v1:";
const STORAGE_LIMIT = 60000;
const RULES_ADDRESS_LIMIT = 1500;

export function mode_of(mode) {
	return VIEWS.includes(mode) ? mode : VIEW_DOCUMENTS;
}

export function registry_for(mode) {
	return mode_of(mode) === VIEW_ITEMS ? ITEM_COLUMNS : LINE_COLUMNS;
}

export function standard_layout(mode) {
	const m = mode_of(mode);
	return { columns: STANDARD_COLUMNS[m].slice(), sort: null, filters: Object.assign({}, FILTERS_OFF, { rules: [] }) };
}

export function standard_view(mode) {
	const m = mode_of(mode);
	return { id: STANDARD_VIEW_IDS[m], name: "Standard", mode: m, preset: true, description: "", layout: standard_layout(m) };
}

// What the tracker draws from when the views could not be fetched: the two standard views and none of
// the person's own.
export function fallback_views() {
	return [standard_view(VIEW_DOCUMENTS), standard_view(VIEW_ITEMS)];
}

// The presets (from the catalog) and the person's own, as one list of views.
export function merge_views(catalog, mine) {
	const out = [];
	((catalog && catalog.presets) || []).forEach((p) => out.push(Object.assign({}, p, { preset: true })));
	(mine || []).forEach((v) => out.push(Object.assign({}, v, { preset: false })));
	VIEWS.forEach((m) => {
		if (!out.some((v) => v.id === STANDARD_VIEW_IDS[m])) out.push(standard_view(m));
	});
	return out;
}

// The views for the picker: the built-in ones in the order they ship, then the person's own by grouping and
// name. With a grouping, only that grouping's.
export function views_for(views, mode) {
	const list = (views || []).filter((v) => !mode || v.mode === mode);
	const by_name = (a, b) =>
		String(a.mode).localeCompare(String(b.mode)) ||
		String(a.name).localeCompare(String(b.name), undefined, { sensitivity: "base" });
	return { presets: list.filter((v) => v.preset), mine: list.filter((v) => !v.preset).sort(by_name) };
}

// ---- cleaning a layout

function num_or_null(value) {
	if (value === null || value === undefined || value === "") return null;
	if (typeof value === "boolean") return NaN;
	const n = Number(value);
	return isFinite(n) && Math.abs(n) <= 1e12 ? n : NaN;
}

function date_or_null(value) {
	if (value === null || value === undefined || value === "") return null;
	return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && !isNaN(Date.parse(value)) ? value : undefined;
}

// A column filter in its stored form, or null when it is not one the tracker can apply: an unknown
// column, a column that cannot be filtered, a test that does not suit the column, or no value yet.
// The same rule the server holds a saved view to (procurement_view_rules._rule).
export function clean_rule(raw, mode) {
	if (!raw || typeof raw !== "object") return null;
	const col = registry_for(mode)[raw.column];
	if (!col || !col.filter) return null;
	if (!OPERATORS[col.filter].includes(raw.op)) return null;
	const out = { column: raw.column, op: raw.op };
	if (raw.op === "contains") {
		const value = text(raw.value).slice(0, LIMITS.max_text_length);
		if (!value) return null;
		out.value = value;
	} else if (raw.op === "any_of") {
		const values = [];
		(Array.isArray(raw.values) ? raw.values : []).forEach((v) => {
			const t = text(v).slice(0, LIMITS.max_choice_length);
			if (t && !values.includes(t) && values.length < LIMITS.max_choices) values.push(t);
		});
		if (!values.length) return null;
		out.values = values;
	} else if (raw.op === "between") {
		const read = col.filter === "date" ? date_or_null : num_or_null;
		const low = read(raw.min);
		const high = read(raw.max);
		if (low === undefined || high === undefined || Number.isNaN(low) || Number.isNaN(high)) return null;
		if (low === null && high === null) return null;
		if (low !== null && high !== null && low > high) return null;
		out.min = low;
		out.max = high;
	}
	return out;
}

// The column filters of a layout that are complete and valid. A half-made filter in the editor is not
// one of them: it filters nothing and is not saved.
export function ready_rules(rules, mode) {
	return (Array.isArray(rules) ? rules : []).map((r) => clean_rule(r, mode)).filter(Boolean).slice(0, LIMITS.max_rules);
}

/**
 * A layout the tracker can draw from whatever it was handed (a stored view, the browser's memory, a
 * link): `{ layout, repaired }`. Unknown columns are dropped (a column a later release removed), so are
 * column filters that no longer fit, and a sort on a column that is not shown; a layout left with no
 * column becomes the standard one. `repaired` says it was not used as given. Never throws.
 */
export function clean_layout(raw, mode) {
	const m = mode_of(mode);
	const registry = registry_for(m);
	if (!raw || typeof raw !== "object" || Array.isArray(raw)) return { layout: standard_layout(m), repaired: true };
	let repaired = false;
	const columns = [];
	(Array.isArray(raw.columns) ? raw.columns : []).forEach((key) => {
		if (typeof key === "string" && registry[key] && !columns.includes(key)) columns.push(key);
		else repaired = true;
	});
	if (!columns.length) return { layout: standard_layout(m), repaired: true };
	let sort = null;
	if (raw.sort) {
		const key = raw.sort.key;
		if (columns.includes(key) && registry[key].sortable !== false && (raw.sort.dir === "asc" || raw.sort.dir === "desc")) sort = { key: key, dir: raw.sort.dir };
		else repaired = true;
	}
	const f = raw.filters && typeof raw.filters === "object" ? raw.filters : {};
	const filters = {
		location: text(f.location).slice(0, LIMITS.max_choice_length),
		status: text(f.status).slice(0, LIMITS.max_choice_length),
		supplier: text(f.supplier).slice(0, LIMITS.max_choice_length),
		action: f.action === true,
		noprice: f.noprice === true,
	};
	const given = Array.isArray(f.rules) ? f.rules : [];
	filters.rules = ready_rules(given, m);
	if (filters.rules.length !== given.length) repaired = true;
	return { layout: { columns: columns, sort: sort, filters: filters }, repaired: repaired };
}

// ---- layout <-> state

/**
 * The widget's two objects for a layout: `view` (grouping, the five toolbar filters, the search) and
 * `layout` ({ id, columns, sort, rules }). `opts`: { id, q, prices }. For a person who is sent no prices
 * the "no price only" filter is left off: there is no checkbox for it, and it would hide rows for a
 * reason they cannot see.
 */
export function state_from_layout(layout, mode, opts) {
	const o = opts || {};
	const m = mode_of(mode);
	const clean = clean_layout(layout, m).layout;
	const f = clean.filters;
	const view = Object.assign(default_state(), {
		view: m,
		location: f.location,
		status: f.status,
		supplier: f.supplier,
		action: f.action,
		noprice: o.prices === false ? false : f.noprice,
		q: text(o.q),
	});
	return {
		view: view,
		layout: {
			id: o.id || STANDARD_VIEW_IDS[m],
			columns: clean.columns.slice(),
			sort: clean.sort ? { key: clean.sort.key, dir: clean.sort.dir } : null,
			rules: clean.filters.rules.map((r) => Object.assign({}, r)),
		},
	};
}

// The layout the current state is, in a view's stored form. The search box is not part of a view.
export function layout_of(view, layout) {
	const v = view || {};
	const l = layout || {};
	return clean_layout(
		{
			columns: l.columns,
			sort: l.sort,
			filters: {
				location: v.location,
				status: v.status,
				supplier: v.supplier,
				action: !!v.action,
				noprice: !!v.noprice,
				rules: l.rules,
			},
		},
		v.view
	).layout;
}

// Whether two layouts are the same view (the way they are stored: order and spelling settled).
export function same_layout(a, b, mode) {
	return JSON.stringify(clean_layout(a, mode).layout) === JSON.stringify(clean_layout(b, mode).layout);
}

// Whether the state differs from the saved view it started as. Without price access the "no price only"
// filter is not the person's to set, so it does not count as a change.
export function view_changed(view, layout, base, mode, prices) {
	const strip = (l) => (prices ? l : Object.assign({}, l, { filters: Object.assign({}, l.filters, { noprice: false }) }));
	const now = strip(layout_of(view, layout));
	const was = strip(clean_layout(base, mode).layout);
	return JSON.stringify(now) !== JSON.stringify(was);
}

// ---- columns

// Show or hide a column. A new one goes at the end; the last one shown cannot be hidden.
export function with_column(columns, key, show, mode) {
	const list = Array.isArray(columns) ? columns.slice() : [];
	if (!registry_for(mode)[key]) return list;
	const has = list.includes(key);
	if (show && !has) list.push(key);
	if (!show && has && list.length > 1) return list.filter((k) => k !== key);
	return list;
}

// Move a column one place (delta -1 earlier, +1 later). The ends stay where they are.
export function move_column(columns, key, delta) {
	const list = Array.isArray(columns) ? columns.slice() : [];
	const from = list.indexOf(key);
	const to = from + delta;
	if (from < 0 || to < 0 || to >= list.length) return list;
	list.splice(to, 0, list.splice(from, 1)[0]);
	return list;
}

// A sort on a column that is no longer shown (or cannot be sorted) is no sort.
export function fix_sort(sort, columns, mode) {
	const registry = registry_for(mode);
	if (!sort || !(columns || []).includes(sort.key) || !registry[sort.key] || registry[sort.key].sortable === false) return null;
	return sort;
}

// ---- column filters

function blank_value(value) {
	return value === null || value === undefined || (typeof value === "string" && !value.trim()) || (Array.isArray(value) && !value.length);
}

export function rule_is_blank(col, row, doctype) {
	if (col.ft) return !String(col.ft(row) || "").trim();
	const raw = col.fv(row, doctype);
	if (col.filter === "number") return blank_value(raw) || !isFinite(Number(raw));
	return blank_value(raw);
}

/**
 * Whether a line (or a by-item row) passes one column filter. `ctx`: { mode, prices, doctype }. A filter
 * on a price column passes everything for a person who was sent no prices (there is nothing to filter
 * on, and the filter bar says so); an unknown column, or a filter with no value yet, passes everything.
 */
export function rule_matches(rule, row, ctx) {
	const c = ctx || {};
	const r = clean_rule(rule, c.mode);
	const col = registry_for(c.mode)[rule && rule.column];
	if (!r || !col || (col.price && !c.prices)) return true;
	const raw = col.fv(row || {}, c.doctype);
	if (r.op === "blank") return rule_is_blank(col, row || {}, c.doctype);
	if (r.op === "not_blank") return !rule_is_blank(col, row || {}, c.doctype);
	if (r.op === "contains") {
		const hay = col.ft ? col.ft(row || {}) : Array.isArray(raw) ? raw.join(" ") : raw;
		return String(hay === null || hay === undefined ? "" : hay).toLowerCase().includes(r.value.toLowerCase());
	}
	if (r.op === "any_of") return (Array.isArray(raw) ? raw : [raw]).some((v) => r.values.includes(v));
	// between
	if (col.filter === "date") {
		const d = blank_value(raw) ? "" : String(raw).slice(0, 10);
		if (!d) return false;
		return !((r.min && d < r.min) || (r.max && d > r.max));
	}
	if (blank_value(raw) || !isFinite(Number(raw))) return false;
	const n = Number(raw);
	return !((r.min !== null && n < r.min) || (r.max !== null && n > r.max));
}

export function rules_match(rules, row, ctx) {
	return (rules || []).every((rule) => rule_matches(rule, row, ctx));
}

// The column filters that apply to this person, and the labels of those that do not (a filter on a
// price column, for a person who was sent no prices).
//   { active: [rule], withheld: ["Rate", ...] }
export function usable_rules(rules, mode, prices) {
	const registry = registry_for(mode);
	const active = [];
	const withheld = [];
	ready_rules(rules, mode).forEach((rule) => {
		if (registry[rule.column].price && !prices) withheld.push(registry[rule.column].label);
		else active.push(rule);
	});
	return { active: active, withheld: withheld };
}

// The choices a "is any of" filter on a column offers: states and kinds of place come from the
// server's filter lists ({value, label}), suppliers are names.
export function choice_options(column, options) {
	const o = options || {};
	if (column.options === "suppliers") return (o.suppliers || []).map((s) => ({ value: s, label: s }));
	return (o[column.options] || []).map((x) => ({ value: x.value, label: x.label }));
}

// A new, empty column filter for a column, for the editor to fill in. It filters nothing until it is
// complete (ready_rules).
export function new_rule(column, mode) {
	const col = registry_for(mode)[column];
	const kind = col && col.filter;
	if (!kind) return null;
	if (kind === "number" || kind === "date") return { column: column, op: "between", min: null, max: null };
	if (kind === "choice") return { column: column, op: "any_of", values: [] };
	return { column: column, op: "contains", value: "" };
}

// A rule moved to another test, with what no longer fits dropped.
export function rule_with_op(rule, op) {
	const out = { column: rule.column, op: op };
	if (op === "contains") out.value = rule.value || "";
	if (op === "any_of") out.values = Array.isArray(rule.values) ? rule.values.slice() : [];
	if (op === "between") {
		out.min = rule.min === undefined ? null : rule.min;
		out.max = rule.max === undefined ? null : rule.max;
	}
	return out;
}

// One column filter in words: 'Received is between 1 and 5', 'Status is Late or To order'.
export function describe_rule(rule, mode, options) {
	const col = registry_for(mode)[rule && rule.column];
	const label = col ? col.label : (rule && rule.column) || "";
	const r = rule || {};
	if (r.op === "blank") return `${label} is blank`;
	if (r.op === "not_blank") return `${label} is not blank`;
	if (r.op === "contains") return `${label} contains "${r.value}"`;
	if (r.op === "any_of") {
		const names = (r.values || []).map((v) => {
			const hit = col && col.filter === "choice" ? choice_options(col, options).find((o) => o.value === v) : null;
			return hit ? hit.label : v;
		});
		return `${label} is ${names.join(" or ")}`;
	}
	const date = col && col.filter === "date";
	const low = r.min !== null && r.min !== undefined && r.min !== "";
	const high = r.max !== null && r.max !== undefined && r.max !== "";
	if (low && high) return `${label} is between ${r.min} and ${r.max}`;
	if (low) return `${label} is ${date ? "on or after" : "at least"} ${r.min}`;
	if (high) return `${label} is ${date ? "on or before" : "at most"} ${r.max}`;
	return label;
}

// The line under the toolbar: what is filtering, and what it hides. `shown` and `total` count rows
// (items, or lines). Empty when nothing narrows.
export function filter_bar_text(state, rules, mode, options, shown, total, noun) {
	const words = describe_filters(state, options, rules, mode);
	if (!words) return "";
	const hidden = Math.max(0, (Number(total) || 0) - (Number(shown) || 0));
	const what = noun || "rows";
	const hides = hidden === 0 ? "hiding nothing" : `hiding ${hidden} of ${total} ${what}`;
	return `${words} (${hides})`;
}

// ---- the address

/**
 * Whether the address names a view or a filter: any key of the widget's own. A plain address names
 * nothing, and the tracker opens on what the browser remembered.
 */
export function address_names_view(search) {
	const params = new URLSearchParams(typeof search === "string" ? search : "");
	return Object.values(QUERY_KEYS).some((key) => params.has(key));
}

/**
 * The address's query for a layout: write_query's, plus the view's id (`pt_layout`, left out for the
 * standard views, which a plain address already means) and a way to say "this filter is OFF" where the
 * view itself has it on: without that, a reload would bring a cleared filter back from the saved view.
 * `base` is the view's own filters ({ location, status, supplier, action, noprice }).
 */
// Whether a link to the page can carry this person's filters on columns: they travel in the address only up to
// RULES_ADDRESS_LIMIT characters, and only where they differ from the view's own. The widget says so when not,
// because the address is what a person copies, and the person they send it to would otherwise see more rows.
export function rules_fit_address(rules, base_rules, mode) {
	const now = JSON.stringify(ready_rules(rules, mode));
	const was = JSON.stringify(ready_rules(base_rules, mode));
	return now === was || now.length <= RULES_ADDRESS_LIMIT;
}

export const LINK_RULES_NOTE =
	"These column filters are too long to fit in a link, so a link to this page will not carry them: whoever you send it to will see more rows.";

export function write_view_query(search, state, layout_id, base, rules) {
	const params = new URLSearchParams(write_query(search, state));
	const s = Object.assign(default_state(), state || {});
	const b = base || {};
	// The filters on columns, where they differ from the view's own: a link then shows the rows its sender saw.
	// Too long for an address (more than RULES_ADDRESS_LIMIT characters): left out, and a reload goes back to the
	// view's own.
	if (rules !== undefined) {
		const now = JSON.stringify(ready_rules(rules, s.view));
		const was = JSON.stringify(ready_rules(b.rules, s.view));
		if (now !== was && now.length <= RULES_ADDRESS_LIMIT) params.set(QUERY_KEYS.rules, now);
		else params.delete(QUERY_KEYS.rules);
	}
	["location", "status", "supplier"].forEach((key) => {
		if (!text(s[key]) && text(b[key])) params.set(QUERY_KEYS[key], "");
	});
	["action", "noprice"].forEach((key) => {
		if (!s[key] && b[key]) params.set(QUERY_KEYS[key], "0");
	});
	const standard = Object.values(STANDARD_VIEW_IDS).includes(layout_id);
	if (layout_id && !standard) params.set(QUERY_KEYS.layout, layout_id);
	else params.delete(QUERY_KEYS.layout);
	const out = params.toString();
	return out ? `?${out}` : "";
}

// ---- what the browser remembers

export function storage_key(user) {
	return STORAGE_PREFIX + (user || "");
}

// The layout in use, added to what was stored: what was stored for the other grouping stays, so going to
// By item and back brings By document back as it was.
//   { v: 1, mode, modes: { documents: { id, layout }, items: { id, layout } } }
export function remember(existing, view, layout) {
	const v = view || {};
	const mode = mode_of(v.view);
	const out = { v: 1, mode: mode, modes: {} };
	VIEWS.forEach((m) => {
		const old = existing && existing.modes && existing.modes[m];
		if (old) out.modes[m] = old;
	});
	out.modes[mode] = { id: (layout && layout.id) || STANDARD_VIEW_IDS[mode], layout: layout_of(v, layout) };
	return out;
}

export function remembered_text(memory) {
	try {
		return JSON.stringify(memory);
	} catch (e) {
		return "";
	}
}

// What was stored, if it can still be read: a layout per grouping, each cleaned (an outdated or
// damaged one is mended or dropped). Anything unreadable is null: the tracker opens on the standard view.
export function parse_remembered(textValue) {
	if (typeof textValue !== "string" || !textValue || textValue.length > STORAGE_LIMIT) return null;
	let data;
	try {
		data = JSON.parse(textValue);
	} catch (e) {
		return null;
	}
	if (!data || typeof data !== "object" || data.v !== 1 || !data.modes || typeof data.modes !== "object") return null;
	const out = { v: 1, mode: mode_of(data.mode), modes: {} };
	VIEWS.forEach((m) => {
		const entry = data.modes[m];
		if (!entry || typeof entry !== "object" || typeof entry.id !== "string") return;
		out.modes[m] = { id: entry.id.slice(0, 80), layout: clean_layout(entry.layout, m).layout };
	});
	if (out.modes[out.mode]) return out;
	const first = Object.keys(out.modes)[0];
	return first ? Object.assign(out, { mode: first }) : null;
}

/**
 * The layout the tracker opens with: `{ view, layout, notes }`.
 *
 *   opts.search    the address's query
 *   opts.views     every view the person can use (merge_views)
 *   opts.kept      the layout the form kept across a refresh, { mode, id, layout }, or null
 *   opts.memory    what the browser remembered (parse_remembered), or null
 *   opts.prices    whether the person was sent prices
 *
 * The address wins when it names a view or a filter; the changes the person made on top of the same view
 * come back from `kept` or the browser's memory; an address that names a view the person does not have
 * (somebody else's, or deleted) opens the standard view and says so. With no address, the last layout used
 * is the one. Never throws and always returns something drawable.
 */
export function resolve_start(opts) {
	const o = opts || {};
	const views = o.views || [];
	// False when the saved views could not be fetched: a view that is not in the list is then not known to be
	// gone, so its id is kept (and written back) instead of being replaced by the standard view.
	const views_ok = o.views_ok !== false;
	const find = (id) => views.find((v) => v.id === id) || null;
	const prices = !!o.prices;
	const notes = [];
	const entry_for = (mode) => {
		if (o.kept && o.kept.mode === mode) return o.kept;
		const m = o.memory && o.memory.modes && o.memory.modes[mode];
		return m ? { mode: mode, id: m.id, layout: m.layout } : null;
	};
	if (address_names_view(o.search)) {
		const params = new URLSearchParams(typeof o.search === "string" ? o.search : "");
		const address = read_query(o.search);
		const wanted = text(params.get(QUERY_KEYS.layout));
		let mode = address.view;
		let id = STANDARD_VIEW_IDS[mode];
		let base = (find(id) || standard_view(mode)).layout;
		if (wanted) {
			const hit = find(wanted);
			if (hit) {
				mode = hit.mode;
				id = hit.id;
				base = hit.layout;
			} else if (views_ok) {
				notes.push("The view named in this link is not one of yours, so the standard view is shown.");
			} else {
				id = wanted;
				notes.push("Your saved views could not be loaded, so the view named in this link is not shown.");
			}
		}
		// The address says WHICH rows: every toolbar filter and every filter on a column comes from it, over the
		// view's own, and never from this person's memory (a link must show the rows its sender saw). Only what
		// cannot hide a row, the columns and the sort, comes back from the memory, for the same view.
		const kept = entry_for(mode);
		const shown = kept && kept.id === id ? kept.layout : base;
		let rules = base.filters.rules;
		if (params.has(QUERY_KEYS.rules)) {
			try {
				rules = ready_rules(JSON.parse(params.get(QUERY_KEYS.rules)), mode);
			} catch (e) {
				rules = base.filters.rules;
			}
		}
		const layout = { columns: shown.columns, sort: shown.sort, filters: Object.assign({}, base.filters, { rules: rules }) };
		const state = state_from_layout(layout, mode, { id: id, q: address.q, prices: prices });
		BASIC_FILTERS.forEach((key) => {
			if (params.has(QUERY_KEYS[key])) state.view[key] = address[key];
		});
		state.view.view = mode;
		if (!prices) state.view.noprice = false;
		return { view: state.view, layout: state.layout, notes: notes };
	}
	const entry = o.kept || (o.memory && entry_for(o.memory.mode));
	if (entry) {
		const mode = mode_of(entry.mode);
		const known = find(entry.id);
		let id = entry.id;
		let layout = entry.layout;
		if (!(known && known.mode === mode) && views_ok) {
			// The view last used is gone (deleted on another device): its layout goes with it.
			id = STANDARD_VIEW_IDS[mode];
			layout = (find(id) || standard_view(mode)).layout;
			notes.push("The saved view you last used is no longer available, so the standard view is shown.");
		}
		const state = state_from_layout(layout, mode, { id: id, q: "", prices: prices });
		return { view: state.view, layout: state.layout, notes: notes };
	}
	const state = state_from_layout(standard_layout(VIEW_DOCUMENTS), VIEW_DOCUMENTS, { id: STANDARD_VIEW_IDS.documents, prices: prices });
	return { view: state.view, layout: state.layout, notes: notes };
}

/**
 * The state after a click on By item / By document: the layout last used in that grouping (or its standard
 * view), with **every** toolbar filter and the search as they stand now, an unticked one included: a filter a
 * person turned off stays off. `opts`: { views, views_ok, memory, view (the state now), name, prices }.
 */
export function switch_grouping(opts) {
	const o = opts || {};
	const name = mode_of(o.name);
	const views = o.views || [];
	const std = STANDARD_VIEW_IDS[name];
	const kept = o.memory && o.memory.modes && o.memory.modes[name];
	const known = kept && views.find((v) => v.id === kept.id && v.mode === name);
	const use_kept = !!kept && (!!known || o.views_ok === false);
	const id = use_kept ? kept.id : std;
	const layout = use_kept ? kept.layout : (views.find((v) => v.id === std) || standard_view(name)).layout;
	const current = o.view || {};
	const next = state_from_layout(layout, name, { id: id, q: current.q, prices: o.prices });
	BASIC_FILTERS.forEach((key) => {
		next.view[key] = current[key] === undefined ? next.view[key] : current[key];
	});
	if (o.prices === false) next.view.noprice = false;
	return next;
}

// The sentence for a view that filters on "no price" when the person is not shown prices: the filter is off for
// them (there is nothing to filter on), so the view lists every item.
export function noprice_note(filters, prices) {
	if (prices || !filters || !filters.noprice) return "";
	return "This view lists the items with no price, which needs permission to read Purchase Orders, so it lists every item.";
}

// ------------------------------------------------------------------ print

/**
 * The by-item list as one plain HTML page for the printer: the items on screen (filtered and
 * sorted as they are), with where each is now. `esc` is the caller's HTML escape; every value
 * goes through it. No script and no external file, so the page prints the same anywhere.
 */
export function print_html(items, opts, esc) {
	const o = opts || {};
	// The two price columns, only when the person was sent prices (opts.prices). Ten columns do not
	// fit a portrait page, so the page asks for landscape; the print dialog can still change it.
	const money = !!o.prices;
	const cell = (value) => esc(value === null || value === undefined ? "" : String(value));
	const rows = (items || [])
		.map((r) => {
			const where = (r.where_now || []).length
				? r.where_now.map((p) => cell(p.text)).join("<br>")
				: cell(r.where_now_text || "");
			const view = money ? item_price_view(r, o.currency) : null;
			const small = (notes) => notes.map((n) => `<br><small>${cell(n)}</small>`).join("");
			const priced = view
				? `<td class="n">${cell(view.unit)}${small(view.unit_notes)}</td>
				<td class="n">${cell(view.spent)}${small(view.spent_notes)}</td>`
				: "";
			return `<tr>
				<td><b>${cell(r.item_code)}</b><br>${cell(r.item_name)}</td>
				<td class="n">${cell(fmt_qty(r.requested_qty))}</td>
				<td class="n">${cell(fmt_qty(r.ordered_qty))}</td>
				<td class="n">${cell(fmt_qty(r.received_qty))}</td>
				${priced}
				<td>${cell(r.state_label)}</td>
				<td>${where}</td>
				<td>${cell(r.expected_date || "")}</td>
				<td>${cell((r.suppliers || []).join(", "))}</td>
			</tr>`;
		})
		.join("");
	const total = money ? spent_summary(items, o.currency) : null;
	const totalRow = total
		? `<tfoot><tr><td colspan="4"><b>Total spent</b> (ordered, not billed or paid)</td>
<td class="n"></td><td class="n"><b>${cell(total.text)}</b>${total.notes.map((n) => `<br><small>${cell(n)}</small>`).join("")}</td>
<td colspan="4"></td></tr></tfoot>`
		: "";
	const sub = [o.subtitle, o.filters ? `Showing: ${o.filters}` : "", o.printed ? `Printed ${o.printed}` : ""]
		.filter(Boolean)
		.map(cell)
		.join(" &middot; ");
	return `<!doctype html><html><head><meta charset="utf-8"><title>${cell(o.title || "Materials")}</title>
<style>
body{font-family:Arial,Helvetica,sans-serif;font-size:11px;color:#111;margin:16px}
h1{font-size:16px;margin:0 0 2px}
p{margin:0 0 10px;color:#444}
table{width:100%;border-collapse:collapse}
th,td{border:1px solid #999;padding:4px 6px;text-align:left;vertical-align:top}
th{background:#eee}
.n{text-align:right;white-space:nowrap}
tr{page-break-inside:avoid}
tfoot td{background:#eee}
${money ? "@page{size:landscape}" : ""}
</style></head><body>
<h1>${cell(o.title || "Materials")}</h1>
<p>${sub}</p>
<table><thead><tr>
<th>Item</th><th class="n">Requested</th><th class="n">Ordered</th><th class="n">Received</th>
${money ? '<th class="n">Unit price</th><th class="n">Spent</th>' : ""}
<th>Status</th><th>Where it is now</th><th>Due</th><th>Supplier</th>
</tr></thead><tbody>${rows}</tbody>${totalRow}</table>
</body></html>`;
}
