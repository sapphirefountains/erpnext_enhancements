/*
 * Bin Manager (v1.693.0, ER-2026-1320545): the page's pure decisions, no DOM and no frappe, so
 * scripts/test_bin_manager.mjs runs them under node. The page script draws and wires
 * (inventory_enhancements/page/bin_manager/bin_manager.js); controller.js holds the screen state
 * and the server calls; this file decides.
 *
 *   routes          /desk/bin-manager is the picker, /desk/bin-manager/<warehouse> one bin. Each is
 *                   its own history entry. ?warehouse=<name> on the bare route (frappe.route_options,
 *                   since v16.50 also in the address) opens that bin in place of the picker.
 *                   Since v1.812.0 three more screens have a route of their own:
 *                   /desk/bin-manager/by-project (and /by-project/<project>), /job-sites and
 *                   /problems. What each shows is views.js.
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
export const VIEWS = "erpnext_enhancements.inventory_enhancements.bin_views.";

// stock_scan_rules.TOLERANCE: what counts as "nothing left".
export const TOLERANCE = 1e-6;

// The page's reads.
export const READS = {
	boot: BA + "manager_boot",
	location: SCAN + "get_location",
	recent: SCAN + "get_recent",
	search_items: SCAN + "search_items",
	check_new_item: SCAN + "check_new_item",
	// The views beyond one bin (v1.812.0, inventory_enhancements/bin_views.py): all reads.
	item_locations: VIEWS + "item_locations",
	project_index: VIEWS + "project_index",
	project_view: VIEWS + "project_view",
	problems: VIEWS + "problems",
	// For a move onto or off the Project Shelf: the jobs that hold an item there (get_item's
	// shelf_jobs and shelf_jobs_exact, the Project Shelf's v1.804.0), and a job found by name.
	item: SCAN + "get_item",
	search_projects: SCAN + "search_projects",
	// A count at the Project Shelf names its jobs (v1.843.0): who the change would belong to, and an
	// item's latest count there with whose it was (for Change whose).
	count_plan: BA + "count_plan",
	count_whose: BA + "count_whose",
};

// Opens a job's site warehouse the first time something is sent to the job (v1.801.0). The By
// project view calls it before a move to a job that has no site yet.
export const OPEN_SITE = SCAN + "job_site";

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
	whose: BA + "change_whose",
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
	whose: "Change whose",
};

// ------------------------------------------------------------------ routes

// The screens with a word of their own after the page's name. A warehouse's docname ends in
// its company's abbreviation ("Bin A1-1-1 - SF"), so none can be one of these words; a bin is
// every other second segment, as it always was.
export const SEGMENT_PROJECTS = "by-project";
export const SEGMENT_SITES = "job-sites";
export const SEGMENT_PROBLEMS = "problems";

// The tabs across the top, in order. A bin belongs to Bins and one job to By project.
export const TABS = [
	{ screen: "picker", label: "Bins" },
	{ screen: "projects", label: "By project" },
	{ screen: "sites", label: "Job sites" },
	{ screen: "problems", label: "Problems" },
];

export function tab_of(target) {
	const screen = target && target.screen;
	if (screen === "bin") return "picker";
	if (screen === "project") return "projects";
	return TABS.some((t) => t.screen === screen) ? screen : "picker";
}

// The screen a route names, or null when it is not this page's.
export function parse_route(route) {
	const parts = Array.isArray(route) ? route : [];
	if (parts[0] !== PAGE) return null;
	const first = typeof parts[1] === "string" ? parts[1].trim() : "";
	if (!first) return { screen: "picker" };
	if (first === SEGMENT_PROJECTS) {
		const project = typeof parts[2] === "string" ? parts[2].trim() : "";
		return project ? { screen: "project", project } : { screen: "projects" };
	}
	if (first === SEGMENT_SITES) return { screen: "sites" };
	if (first === SEGMENT_PROBLEMS) return { screen: "problems" };
	return { screen: "bin", warehouse: first };
}

export function build_route(target) {
	const t = target || {};
	if (t.screen === "bin" && t.warehouse) return [PAGE, t.warehouse];
	if (t.screen === "project" && t.project) return [PAGE, SEGMENT_PROJECTS, t.project];
	if (t.screen === "projects") return [PAGE, SEGMENT_PROJECTS];
	if (t.screen === "sites") return [PAGE, SEGMENT_SITES];
	if (t.screen === "problems") return [PAGE, SEGMENT_PROBLEMS];
	return [PAGE];
}

export function route_key(target) {
	return build_route(target).join("/");
}

// The Warehouse Transactions report (v1.831.0, the 2026-11-19 operations review: "reach it from the Bin
// Manager"). The route that opens it for what is on screen: a bin's movements, a job's (its Project
// filter: rows carrying the job and everything at its site), or from the picker the job or item the
// picker is filtered to. Every filter this page can set is sent, blank unless this screen names it:
// the report keeps its filters while the tab is open (query_report.js set_route_filters sets only the
// ones named), so a person filtered to on a last visit would otherwise silently narrow this link.
//
// The dates: a job opens from the day its first stock moved (project_view's movements_since) to today,
// so parts moved six weeks ago are not hidden behind the report's default 30 days. A bin and the picker
// leave the dates alone: the report shows them, and a range somebody chose is not stale. `extra` is
// { since, today }; without `since` (no movement yet, or an older server) the dates are left alone too.
export const TRANSACTIONS_REPORT = "Warehouse Transactions";

export function transactions_route(target, filters, extra) {
	const t = target || {};
	const f = filters || {};
	const x = extra || {};
	const options = { view: "Movements", item_code: "", warehouse: "", project: "", user: "", kind: "" };
	if (t.screen === "bin" && t.warehouse) options.warehouse = t.warehouse;
	else if (t.screen === "project" && t.project) {
		options.project = t.project;
		if (x.since) {
			options.from_date = String(x.since).slice(0, 10);
			if (x.today) options.to_date = String(x.today).slice(0, 10);
		}
	}
	else if (t.screen === "picker") {
		if (f.project) options.project = String(f.project);
		if (f.item) options.item_code = String(f.item);
	}
	return ["query-report", TRANSACTIONS_REPORT, options];
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

// A copy of the route options, taken before they are cleared: the picker's filters are read out
// of it (views.read_filters).
export function copy_route_options(options) {
	return options && typeof options === "object" ? Object.assign({}, options) : {};
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
	if (kind === "whose") {
		if (!v.voucher_no) return "There is no count of this item on the Project Shelf to change.";
		return split_problem(v.jobs, v.change);
	}
	if (kind === "count") {
		const qty = number(v.qty);
		if (!Number.isFinite(qty) || qty < 0) return "Enter the quantity you counted, 0 or more.";
		if (row && Math.abs(qty - (Number(row.on_hand) || 0)) <= TOLERANCE) {
			return "That is what the system already shows.";
		}
		// At the Project Shelf the shares of the change must add up to it (v1.843.0).
		if (v.jobs) {
			const problem = split_problem(v.jobs, qty - (Number(row && row.on_hand) || 0));
			if (problem) return problem;
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
		// A move onto or off the Project Shelf names its job (shelf_move says when).
		if (v.job_rule && JOB_REFUSALS[v.job_rule] && !String(v.project || "").trim()) return JOB_REFUSALS[v.job_rule];
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
	if (kind === "whose") return { method, args: { item_code, voucher_no: v.voucher_no, jobs: JSON.stringify(v.jobs || {}) } };
	if (kind === "count") {
		const args = { item_code, warehouse, qty: number(v.qty), client_ref: ref };
		// Whose the change at the Project Shelf was (v1.843.0). Sent only from the shelf's dialog.
		if (v.jobs) args.jobs = JSON.stringify(v.jobs);
		// Sent only with a unit cost the person gave when asked; the server uses it only for an item with none.
		if ("rate" in v && v.rate !== null && v.rate !== undefined && String(v.rate).trim() !== "") args.rate = unitCostProblem(v.rate).rate;
		return { method, args };
	}
	// The job a move onto or off the Project Shelf is for. Sent only when there is one: on a site
	// whose move_to takes no project, no dialog ever gives one, and the request is the one it was.
	const project = String(v.project || "").trim();
	if (kind === "move") {
		const args = {
			item_code,
			// `warehouse` is the place the row is at: the bin on screen, or (By project) the
			// shelf or the site the row was listed under.
			from_warehouse: warehouse,
			to_warehouse: v.to_warehouse,
			qty: number(v.qty),
			client_ref: ref,
			make_home: v.make_home ? 1 : 0,
		};
		if (project) args.project = project;
		return { method, args };
	}
	if (kind === "boneyard") {
		const args = { item_code, from_warehouse: warehouse, qty: number(v.qty), client_ref: ref };
		if (project) args.project = project;
		return { method, args };
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

// The kinds of place nothing is ever put on as an item's bin, besides the boneyard: the Project Shelf
// (v1.817.0) and a job site (v1.801.0). The server refuses both (bin_assignments._assignable_location).
export const NOT_BIN_KINDS = ["project_shelf", "job_site"];

// Those places by name. Read from the kind the boot gives each bin (bin_views.decorate), which counts every
// job site the server refuses, the hand-made one with no job included. The bin on screen also answers for
// itself (get_location's project_shelf and job_site), for a boot whose kinds could not be read.
function not_bins(boot, location) {
	const b = boot || {};
	const out = (Array.isArray(b.bins) ? b.bins : [])
		.filter((bin) => bin && bin.warehouse && NOT_BIN_KINDS.includes(bin.kind))
		.map((bin) => bin.warehouse);
	if (b.shelf_warehouse) out.push(b.shelf_warehouse);
	const loc = location || {};
	if (loc.warehouse && (loc.project_shelf || loc.job_site)) out.push(loc.warehouse);
	return out;
}

// What the caller may do, from manager_boot. Missing means no. `location` is the bin on screen's read, or
// nothing.
export function permissions(boot, location) {
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
		// The Project Shelf, when a move onto or off it names its job on this site
		// (bin_views.shelf_job_moves): else null, and the page moves stock as it always did.
		shelf: b.shelf_job_moves === true && b.shelf_warehouse ? b.shelf_warehouse : null,
		// The Project Shelf, when a count there names its jobs (v1.843.0, bin_assignments.count_plan):
		// else null, and a count is the one it always was.
		shelf_counts: b.shelf_count_jobs === true && b.shelf_warehouse ? b.shelf_warehouse : null,
		// The Project Shelf and the job sites: nothing is put on them (assignable).
		not_bins: not_bins(b, location),
	};
}

// Whether an item may be put on `warehouse`: not the boneyard, the Project Shelf or a job site. Keep on this
// bin, Add existing item and New item are all an assign, and the server refuses each one at those places, so
// the page does not offer them there.
export function assignable(perms, warehouse) {
	const p = perms || {};
	if (!warehouse) return false;
	if (p.boneyard && p.boneyard === warehouse) return false;
	return !(Array.isArray(p.not_bins) && p.not_bins.includes(warehouse));
}

// ------------------------------------------------------------------ whose a count is (v1.843.0)

// A count at the Project Shelf is a Stock Reconciliation, which has no job, so the page says whose the
// change was: a share per job (and "" for the part that is no job's), adding up to the change. The
// server works out the default (bin_assignments.count_plan, project_shelf_rules.count_split) and checks
// the person's split again; these only draw it and catch what needs no round trip.

// Whether a count at `warehouse` names its jobs.
export function count_names_jobs(perms, warehouse) {
	const p = perms || {};
	return !!p.shelf_counts && !!warehouse && p.shelf_counts === warehouse;
}

// The split a plan starts on: { project: qty } with "" for no job, from the server's default (or a
// count's current shares). Shares of nothing are left out.
export function split_from_plan(plan) {
	const out = {};
	((plan && plan.rows) || []).forEach((row) => {
		const qty = Number(row.qty) || 0;
		if (qty > TOLERANCE) out[row.project] = qty;
	});
	const none = Number(plan && plan.no_job) || 0;
	if (none > TOLERANCE) out[""] = none;
	return out;
}

export function split_total(jobs) {
	return Object.values(jobs || {}).reduce((sum, qty) => sum + (Number(qty) || 0), 0);
}

// A sentence when a split cannot be sent, or null: a share below nothing, or shares that do not add up to
// the change. What each job holds is the server's to check.
export function split_problem(jobs, change) {
	const values = Object.values(jobs || {}).map((qty) => Number(qty));
	if (values.some((qty) => !Number.isFinite(qty) || qty < 0)) return "Each share is a number, 0 or more.";
	const want = Math.abs(Number(change) || 0);
	const total = split_total(jobs);
	if (Math.abs(total - want) > TOLERANCE) return `The shares add up to ${plain(total)}; the count changes the shelf by ${plain(want)}.`;
	return null;
}

// The heading over the split: a count down takes units off the shelf, a count up finds some.
export function split_question(change, uom) {
	const n = Number(change) || 0;
	if (n < -TOLERANCE) return `This count takes ${amount(-n, uom)} off the Project Shelf. Whose were they?`;
	if (n > TOLERANCE) return `This count finds ${amount(n, uom)} more on the Project Shelf. Whose are they?`;
	return "";
}

// ------------------------------------------------------------------ the Project Shelf's job

// Everything on the Project Shelf belongs to a job, and whose it is is read from the job on each
// stock ledger row. So a move onto or off the shelf must carry the job, or the shelf's by-project
// numbers drift (the Project Shelf's v1.804.0; the server refuses a move that needs one and has none).

// What the dialog asks, and what it says when the answer is missing.
export const JOB_QUESTIONS = {
	off: "Which job were these bought for?",
	onto: "Which job are these being set aside for?",
};
export const JOB_REFUSALS = {
	off: "Pick the job these were bought for.",
	onto: "Pick the job these are being set aside for.",
};

/**
 * Whether a move must name a job: "off" (leaving the shelf for a bin or the boneyard), "onto"
 * (going onto the shelf from a bin), or null.
 *
 * `shelf` is permissions(boot).shelf: null on a site that does not ask, and then nothing ever
 * does. `is_site(warehouse)` says whether a place is a job's site. A move between the shelf and
 * a job site names nothing: the site already says whose it is. With the destination not picked
 * yet, a move off the shelf asks (it is dropped again if a site is then picked).
 */
export function shelf_move(shelf, from_warehouse, to_warehouse, is_site) {
	if (!shelf) return null;
	const site = typeof is_site === "function" ? is_site : () => false;
	if (from_warehouse === shelf) {
		if (to_warehouse === shelf) return null;
		return to_warehouse && site(to_warehouse) ? null : "off";
	}
	if (to_warehouse === shelf) return site(from_warehouse) ? null : "onto";
	return null;
}

// The jobs that hold an item on the shelf, from get_item's answer: [{ project, project_name, qty }].
export function job_choices(answer) {
	const jobs = answer && Array.isArray(answer.shelf_jobs) ? answer.shelf_jobs : [];
	return jobs
		.filter((j) => j && j.project)
		.map((j) => ({ project: j.project, project_name: j.project_name || j.project, qty: Number(j.qty) || 0 }));
}

// The job to start on, or null: the only job that holds the item, and only when the server says
// those jobs account for every unit of it (shelf_jobs_exact). Where they do not, part of what is
// there has no job on record, and hanging it on the one job that has a tag would be a guess.
export function job_preselect(answer) {
	const jobs = job_choices(answer);
	return answer && answer.shelf_jobs_exact === true && jobs.length === 1 ? jobs[0] : null;
}

// A job in a list: "Water Wall · 4 here".
export function job_label(job, uom) {
	const j = job || {};
	const name = j.project_name || j.project || "";
	return Number(j.qty) > TOLERANCE ? `${name} · ${amount(j.qty, uom)} here` : name;
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
//   whose     the same, at the Project Shelf when a count there names its jobs (change_whose)
//   move      Stock Entry create + submit, and something here to move
//   keep      the module gate, for an item listed here that is not on the bin, at a place items may be
//             put on (assignable: not the boneyard, the Project Shelf or a job site)
//   boneyard  the module gate, a transfer, a boneyard set, something here, and not the boneyard
//   unassign  the module gate, for an item this bin holds by rule, default or nobody's decision
export function row_actions(row, perms, warehouse) {
	const p = perms || {};
	const r = row || {};
	const here = Number(r.on_hand) > TOLERANCE;
	const is_boneyard = !!p.boneyard && p.boneyard === warehouse;
	const out = [];
	if (p.can_count) out.push("count");
	// Say again whose the item's latest count at the Project Shelf was (v1.843.0).
	if (p.can_count && count_names_jobs(p, warehouse)) out.push("whose");
	if (p.can_move && here) out.push("move");
	if (p.can_manage && !r.assigned && assignable(p, warehouse)) out.push("keep");
	if (p.can_manage && p.can_move && p.boneyard && !is_boneyard && here) out.push("boneyard");
	if (p.can_manage && (r.assigned || r.is_default || r.undecided)) out.push("unassign");
	return out;
}

// The bin's own buttons: add an existing item, or create one and put it here. None where nothing is put on
// a bin (assignable).
export function bin_actions(perms, warehouse) {
	const p = perms || {};
	if (!p.can_manage || !assignable(p, warehouse)) return [];
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
export function make_home_offered(perms, to_warehouse, to_kind) {
	const p = perms || {};
	// Nor to the Project Shelf or a job site: nothing is kept at either as an item's bin, and the
	// server refuses both as a home.
	if (to_kind === "project_shelf" || to_kind === "job_site") return false;
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
