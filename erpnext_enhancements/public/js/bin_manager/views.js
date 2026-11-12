/*
 * Bin Manager (v1.812.0): the pure decisions behind its filters and its views beyond one bin. No
 * DOM and no frappe; scripts/test_bin_manager.mjs runs it under node. logic.js keeps the routes
 * and the saves; this file is what a view shows.
 *
 *   filters     the picker's: what is typed, the kind of place, a job, an item, and one quick
 *               filter. They live in the ADDRESS (?kind=job_site&project=PRJ-00597), written in
 *               place, so a filter is never a history entry and Back from a bin lands on the
 *               picker as it was left.
 *   kinds       shop bin, Project Shelf, job site, boneyard, vehicle: decided by the server
 *               (bin_views.decorate); a boot that does not say is a shop bin (or the boneyard)
 *   job sites   one row per site with its job, item count and value
 *   by project  one job's Project Shelf and job site side by side, and which way each row moves
 *   problems    the lists, in a fixed order, with what to do about each
 *
 * The words here are the page's; the numbers and the kinds are the server's.
 */

import { TOLERANCE, filter_bins, plain } from "./logic.js";

// ------------------------------------------------------------------ kinds and filters

export const KIND_SHOP = "shop_bin";
export const KIND_SHELF = "project_shelf";
export const KIND_SITE = "job_site";
export const KIND_BONEYARD = "boneyard";
export const KIND_VEHICLE = "vehicle";

// In the order the picker offers them.
export const KINDS = [
	{ key: KIND_SHOP, label: "Shop bins" },
	{ key: KIND_SHELF, label: "Project Shelf" },
	{ key: KIND_SITE, label: "Job sites" },
	{ key: KIND_BONEYARD, label: "Boneyard" },
	{ key: KIND_VEHICLE, label: "Vehicles" },
];

export const QUICK = [
	{ key: "has_stock", label: "Has stock" },
	{ key: "empty", label: "Empty" },
	{ key: "missing", label: "Assigned items missing" },
	{ key: "unassigned", label: "Unassigned stock" },
];

// Every key the picker owns in the address. `warehouse` is the old deep link (opens that bin) and
// is read by logic.read_route_options; it is never one of these.
export const FILTER_KEYS = ["q", "kind", "project", "item", "quick"];

export function default_filters() {
	return { q: "", kind: "", project: "", item: "", quick: "" };
}

function text(value) {
	return value === undefined || value === null ? "" : String(value).trim();
}

// The filters an address's query (frappe.route_options) names. A kind or a quick filter the page
// does not know is dropped; a job or an item is kept as given, so a link to a job with nothing
// anywhere shows an empty list that says which job it was for.
export function read_filters(options) {
	const o = options && typeof options === "object" ? options : {};
	const out = default_filters();
	out.q = text(o.q);
	out.project = text(o.project);
	out.item = text(o.item);
	const kind = text(o.kind);
	if (KINDS.some((k) => k.key === kind)) out.kind = kind;
	const quick = text(o.quick);
	if (QUICK.some((k) => k.key === quick)) out.quick = quick;
	return out;
}

// The address's query for a set of filters: "?kind=job_site&project=PRJ-1", or "". Keys in a
// fixed order and blanks left out, so the same filters always write the same address.
export function filters_query(filters) {
	const f = Object.assign(default_filters(), filters || {});
	const params = new URLSearchParams();
	FILTER_KEYS.forEach((key) => {
		const value = text(f[key]);
		if (value) params.set(key, value);
	});
	const out = params.toString();
	return out ? `?${out}` : "";
}

export function has_filters(filters) {
	const f = filters || {};
	return FILTER_KEYS.some((key) => !!text(f[key]));
}

export function same_filters(a, b) {
	return filters_query(a) === filters_query(b);
}

// A bin's kind. A boot from before the kinds were sent only marks the boneyard.
export function bin_kind(bin) {
	const b = bin || {};
	if (KINDS.some((k) => k.key === b.kind)) return b.kind;
	return b.is_boneyard ? KIND_BONEYARD : KIND_SHOP;
}

// How many bins there are of each kind, for the kind buttons. A kind with none is not offered.
export function kind_counts(bins) {
	const out = {};
	(bins || []).forEach((b) => {
		const kind = bin_kind(b);
		out[kind] = (out[kind] || 0) + 1;
	});
	return out;
}

export function kind_options(bins) {
	const counts = kind_counts(bins);
	return KINDS.filter((k) => counts[k.key]).map((k) => ({ key: k.key, label: k.label, count: counts[k.key] }));
}

// The jobs that have a site, for the job filter: one entry per job, by name.
export function project_options(bins) {
	const seen = new Map();
	(bins || []).forEach((b) => {
		if (b.project && !seen.has(b.project)) {
			seen.set(b.project, { project: b.project, project_name: b.project_name || b.project, customer: b.customer || "" });
		}
	});
	return Array.from(seen.values()).sort((a, b) =>
		a.project_name.localeCompare(b.project_name, undefined, { numeric: true, sensitivity: "base" })
	);
}

// "Water Wall · Acme Hotels": a job's name and, when the caller may see it, its customer.
export function project_label(row) {
	const r = row || {};
	const name = r.project_name || r.project || "";
	return r.customer ? `${name} · ${r.customer}` : name;
}

function quick_match(bin, quick) {
	if (quick === "has_stock") return Number(bin.on_hand_items) > 0;
	if (quick === "empty") return !(Number(bin.on_hand_items) > 0);
	if (quick === "missing") return Number(bin.missing_items) > 0;
	if (quick === "unassigned") return Number(bin.unassigned_items) > 0;
	return true;
}

/**
 * The bins a set of filters shows, in the order they came.
 *
 * `locations` is the item filter's answer (bin_views.item_locations: { warehouse: { on_hand,
 * assigned } }) for the item in `filters.item`, or null while it is being read: an item filter
 * with no answer yet shows nothing rather than every bin.
 */
export function apply_filters(bins, filters, locations) {
	const f = Object.assign(default_filters(), filters || {});
	let list = filter_bins(bins, f.q);
	if (f.kind) list = list.filter((b) => bin_kind(b) === f.kind);
	if (f.project) list = list.filter((b) => b.project === f.project);
	if (f.quick) list = list.filter((b) => quick_match(b, f.quick));
	if (f.item) {
		const here = locations || {};
		list = locations ? list.filter((b) => !!here[b.warehouse]) : [];
	}
	return list;
}

// What a bin's row says under its name. With an item filter on, what that item is doing there
// comes first.
export function bin_summary(bin, location) {
	const b = bin || {};
	const parts = [];
	if (location) {
		if (Number(location.on_hand) > TOLERANCE) parts.push(`${plain(location.on_hand)} here`);
		if (location.assigned) parts.push("assigned here");
	}
	parts.push(`${Number(b.on_hand_items) || 0} on hand`);
	if (bin_kind(b) === KIND_SHOP || bin_kind(b) === KIND_VEHICLE) parts.push(`${Number(b.assigned_items) || 0} assigned`);
	if (Number(b.missing_items) > 0) parts.push(`${b.missing_items} missing`);
	if (Number(b.unassigned_items) > 0) parts.push(`${b.unassigned_items} unassigned`);
	return parts.join(" · ");
}

// The filters in words, for the line that says why the list is short: "Job sites · Water Wall".
export function describe_filters(filters, bins, item) {
	const f = Object.assign(default_filters(), filters || {});
	const parts = [];
	const kind = KINDS.find((k) => k.key === f.kind);
	if (kind) parts.push(kind.label);
	if (f.project) {
		const job = project_options(bins).find((p) => p.project === f.project);
		parts.push(job ? job.project_name : f.project);
	}
	if (f.item) parts.push((item && item.item_code === f.item && item.item_name) || f.item);
	const quick = QUICK.find((k) => k.key === f.quick);
	if (quick) parts.push(quick.label);
	if (text(f.q)) parts.push(`"${text(f.q)}"`);
	return parts.join(" · ");
}

// ------------------------------------------------------------------ job sites

// One row per job site, jobs still in progress first, then by job name.
export function site_rows(bins) {
	return (bins || [])
		.filter((b) => bin_kind(b) === KIND_SITE)
		.map((b) => ({
			warehouse: b.warehouse,
			warehouse_name: b.warehouse_name || b.warehouse,
			project: b.project || "",
			project_name: b.project_name || b.project || "",
			customer: b.customer || "",
			closed: !!b.project_closed,
			items: Number(b.on_hand_items) || 0,
			value: b.stock_value === undefined || b.stock_value === null ? null : Number(b.stock_value) || 0,
		}))
		.sort((a, b) => {
			if (a.closed !== b.closed) return a.closed ? 1 : -1;
			const an = a.project_name || a.warehouse_name;
			const bn = b.project_name || b.warehouse_name;
			return an.localeCompare(bn, undefined, { numeric: true, sensitivity: "base" });
		});
}

// The totals under the list. `value` is null when the caller may not see values.
export function site_totals(rows) {
	const list = rows || [];
	const priced = list.filter((r) => r.value !== null);
	return {
		sites: list.length,
		items: list.reduce((n, r) => n + r.items, 0),
		value: priced.length ? priced.reduce((n, r) => n + r.value, 0) : null,
	};
}

// A flag on a site's row, or null: stock still at a finished job's site is the one thing on this
// list that wants doing.
export function site_flag(row) {
	const r = row || {};
	if (r.closed && r.items > 0) return { label: "Job finished, stock still here", tone: "warn" };
	if (r.closed) return { label: "Job finished", tone: "neutral" };
	if (!r.project) return { label: "No job", tone: "neutral" };
	return null;
}

// ------------------------------------------------------------------ by project

// The jobs in the By project list whose name, id or customer holds every word typed.
export function filter_projects(projects, query) {
	const want = String(query || "")
		.toLowerCase()
		.split(/\s+/)
		.filter(Boolean);
	const list = Array.isArray(projects) ? projects : [];
	if (!want.length) return list.slice();
	return list.filter((p) => {
		const hay = `${p.project || ""} ${p.project_name || ""} ${p.customer || ""}`.toLowerCase();
		return want.every((w) => hay.includes(w));
	});
}

export function project_counts_text(row) {
	const r = row || {};
	const parts = [];
	parts.push(`${Number(r.shelf_items) || 0} on the Project Shelf`);
	parts.push(r.site ? `${Number(r.site_items) || 0} at the job site` : "no job site yet");
	return parts.join(" · ");
}

/**
 * One job's two places as the page draws them: the Project Shelf and the job site, each with its
 * rows and the one move a row can make (shelf rows go to the site, site rows come back to the
 * shelf). `view` is bin_views.project_view's answer; `perms` is logic.permissions(boot).
 *
 * A row is the shape the page's dialogs already take (item_code, item_name, stock_uom, on_hand),
 * so the existing Move dialog's quantity check works on it unchanged: on_hand is THIS JOB's
 * share of the shelf, which is all that may be sent from here.
 */
export function project_sections(view, perms) {
	const v = view || {};
	const p = perms || {};
	const row = (r) => {
		const qty = Number(r.qty) || 0;
		const recorded = Number(r.recorded);
		return {
			item_code: r.item_code,
			item_name: r.item_name || r.item_code,
			stock_uom: r.stock_uom || "",
			on_hand: qty,
			value: r.value === undefined ? null : r.value,
			// The job's balance on the records, when the shelf holds less than that (the Project
			// Shelf's numbers come from the project on each ledger row; a count or a move made with
			// no job on it makes them drift). Null when the two agree.
			recorded: Number.isFinite(recorded) && recorded > qty + TOLERANCE ? recorded : null,
		};
	};
	const shelf = v.shelf
		? {
				key: "shelf",
				title: "On the Project Shelf for this job",
				warehouse: v.shelf.warehouse,
				warehouse_name: v.shelf.warehouse_name,
				rows: (v.shelf.items || []).map(row),
				empty: "Nothing is on the Project Shelf for this job.",
				// Said once above the rows when any of them was cut to what the shelf holds.
				note: (v.shelf.items || []).some((r) => Number(r.recorded) > (Number(r.qty) || 0) + TOLERANCE)
					? "The shelf holds less of some items than the records say is this job's. A count or a move made with no job on it does that. Count the shelf to put it right."
					: null,
				// To the job's site: needs the right to move, and a site that can take it.
				move: p.can_move && !v.send_refusal ? { key: "to_site", label: "Send to job site" } : null,
				blocked: p.can_move ? v.send_refusal || null : null,
		  }
		: null;
	const site = v.site
		? {
				key: "site",
				title: "At the job site",
				warehouse: v.site.warehouse,
				warehouse_name: v.site.warehouse_name,
				rows: (v.site.items || []).map(row),
				empty: "Nothing is at the job site.",
				// Back to the shelf: never refused for a finished job (that is how leftovers return).
				move: p.can_move && v.shelf ? { key: "to_shelf", label: "Bring back to the shelf" } : null,
				blocked: null,
		  }
		: null;
	return { shelf, site };
}

/**
 * The transfer a row's move makes: { from_warehouse, to_warehouse, needs_site }. `needs_site`
 * means the job has no site yet, and the page opens it first (api.stock_scan.job_site) and sends
 * to the warehouse that answers. Null when the move cannot be made.
 */
export function project_move(direction, view) {
	const v = view || {};
	if (direction === "to_site") {
		if (!v.shelf || v.send_refusal) return null;
		return { from_warehouse: v.shelf.warehouse, to_warehouse: v.site ? v.site.warehouse : null, needs_site: !v.site };
	}
	if (direction === "to_shelf") {
		if (!v.shelf || !v.site) return null;
		return { from_warehouse: v.site.warehouse, to_warehouse: v.shelf.warehouse, needs_site: false };
	}
	return null;
}

// ------------------------------------------------------------------ problems

// The Problems view's lists, in the order they are shown, each with what it means and what to do.
export const PROBLEM_SECTIONS = [
	{
		key: "assigned_empty",
		title: "Assigned but empty",
		hint: "The item is on this bin's list and nothing is on hand there. Restock it, or open the bin and take the item off it.",
		by: "place",
	},
	{
		key: "unassigned_stock",
		title: "Stock with no assignment",
		hint: "Something is on hand at a bin the item is not assigned to. Open the bin and keep it there, or move it to its own bin.",
		by: "place",
	},
	{
		key: "finished_shelf",
		title: "On the Project Shelf for a finished job",
		hint: "The job is finished and this is still held for it. Open the Project Shelf and move it to a shop bin (the move asks which job it was bought for), or return it to the supplier.",
		by: "job",
	},
	{
		key: "finished_site",
		title: "Still at a finished job's site",
		hint: "The job is finished and the records say this is still at its site. Bring it back, or count the site.",
		by: "job",
	},
];

export function problem_sections(data) {
	const d = data || {};
	return PROBLEM_SECTIONS.map((section) => {
		const list = d[section.key] || {};
		const rows = Array.isArray(list.rows) ? list.rows : [];
		return Object.assign({}, section, {
			rows,
			total: Number(list.total) || rows.length,
			truncated: !!list.truncated,
		});
	});
}

export function problem_total(data) {
	return problem_sections(data).reduce((n, s) => n + s.total, 0);
}

// A dollar amount for a list: "$1,234.50". The page uses frappe's own formatter when it has one.
export function money(value) {
	const n = Number(value) || 0;
	return `$${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
