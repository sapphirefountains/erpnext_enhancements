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
	q: "pt_q",
};

export function default_state() {
	return { view: VIEW_DOCUMENTS, location: "", status: "", supplier: "", action: false, q: "" };
}

function text(value) {
	return value === null || value === undefined ? "" : String(value).trim();
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
	return !!(s.location || s.status || s.supplier || s.action);
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

// Whether a line (or a by-item row: same keys, in the plural) passes the four filters.
export function row_matches_filters(row, state) {
	const r = row || {};
	const s = state || {};
	if (s.action && !r.needs_action) return false;
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
export function filter_groups(groups, state) {
	const list = Array.isArray(groups) ? groups : [];
	const tokens = tokenize(state && state.q);
	const filtered = is_filtered(state);
	if (!tokens.length && !filtered) return list;
	const out = [];
	list.forEach((group) => {
		const docs = [];
		(group.documents || []).forEach((doc) => {
			const all = doc.items || [];
			const base = filtered ? all.filter((row) => row_matches_filters(row, state)) : all;
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

export function filter_items(items, state) {
	const list = Array.isArray(items) ? items : [];
	const tokens = tokenize(state && state.q);
	if (!tokens.length && !is_filtered(state)) return list;
	return list.filter((row) => row_matches_filters(row, state) && row_matches_search(row, tokens));
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

// Most urgent first: one click on Status puts what somebody has to chase at the top. The same
// order as procurement_overview.STATE_RANK, which decides the one state a line is shown as.
export const STATE_RANK = ["late", "to_order", "ordered", "partly_received", "received", "closed", "quote_only", "invoice_only"];

/**
 * Sortable columns of the item table inside a document. Declared once, outside the Vue options,
 * so the template and the comparator cannot drift apart: a header rendered against a key the
 * sorter does not know is a click that silently does nothing.
 *
 * Doc Chain is deliberately absent. It is one cell holding up to seven chain nodes; there is no
 * single value to order by, and a click handler would mean inventing an ordering the column
 * does not show.
 */
export const LINE_COLUMNS = {
	item: { label: "Item", type: "text", value: (r) => `${r.item_code || ""} ${r.item_name || ""}`.trim() },
	where: { label: "Where it is now", type: "text", value: (r) => r.where_now_text },
	requested: { label: "Requested", cls: "qty-col", type: "number", value: (r) => r.requested_qty },
	ordered: { label: "Ordered", cls: "qty-col", type: "number", value: (r) => r.ordered_qty },
	received: { label: "Received", cls: "qty-col", type: "number", value: (r) => r.received_qty },
	status: { label: "Status", type: "rank", value: (r) => r.line_state, ranks: STATE_RANK },
};

// The by-item table. One more column than a document's table: the date the item is due.
export const ITEM_COLUMNS = {
	item: LINE_COLUMNS.item,
	requested: LINE_COLUMNS.requested,
	ordered: LINE_COLUMNS.ordered,
	received: LINE_COLUMNS.received,
	status: { label: "Status", type: "rank", value: (r) => r.state, ranks: STATE_RANK },
	where: LINE_COLUMNS.where,
	due: { label: "Due", type: "text", value: (r) => r.expected_date },
};

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
export function sort_rows(rows, columns, sort) {
	const list = rows || [];
	const column = sort && columns[sort.key];
	if (!column) return list;
	const dir = sort.dir === "desc" ? -1 : 1;
	return list.slice().sort((a, b) => {
		const av = column.value(a);
		const bv = column.value(b);
		const blanks = blank_order(av, bv);
		// Not multiplied by dir: see blank_order.
		if (blanks !== null) return blanks;
		return dir * compare(av, bv, column);
	});
}

// The sort a click on a header leads to: ascending, descending, then none. Without the third
// step line order is lost until the form is reloaded, and line order means something on a
// request: it is how the job was grouped.
export function next_sort(current, key, columns) {
	if (!columns[key]) return current || null;
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
export function describe_filters(state, options) {
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
	if (tokenize(s.q).length) parts.push(`"${String(s.q).trim()}"`);
	return parts.join(" · ");
}

// ------------------------------------------------------------------ print

/**
 * The by-item list as one plain HTML page for the printer: the items on screen (filtered and
 * sorted as they are), with where each is now. `esc` is the caller's HTML escape; every value
 * goes through it. No script and no external file, so the page prints the same anywhere.
 */
export function print_html(items, opts, esc) {
	const o = opts || {};
	const cell = (value) => esc(value === null || value === undefined ? "" : String(value));
	const rows = (items || [])
		.map((r) => {
			const where = (r.where_now || []).length
				? r.where_now.map((p) => cell(p.text)).join("<br>")
				: cell(r.where_now_text || "");
			return `<tr>
				<td><b>${cell(r.item_code)}</b><br>${cell(r.item_name)}</td>
				<td class="n">${cell(fmt_qty(r.requested_qty))}</td>
				<td class="n">${cell(fmt_qty(r.ordered_qty))}</td>
				<td class="n">${cell(fmt_qty(r.received_qty))}</td>
				<td>${cell(r.state_label)}</td>
				<td>${where}</td>
				<td>${cell(r.expected_date || "")}</td>
				<td>${cell((r.suppliers || []).join(", "))}</td>
			</tr>`;
		})
		.join("");
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
</style></head><body>
<h1>${cell(o.title || "Materials")}</h1>
<p>${sub}</p>
<table><thead><tr>
<th>Item</th><th class="n">Requested</th><th class="n">Ordered</th><th class="n">Received</th>
<th>Status</th><th>Where it is now</th><th>Due</th><th>Supplier</th>
</tr></thead><tbody>${rows}</tbody></table>
</body></html>`;
}
