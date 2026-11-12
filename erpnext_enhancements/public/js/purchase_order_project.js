/**
 * Purchase Order — Project attribution on item lines (WI-014 follow-through).
 *
 * Targets: the "Purchase Order" doctype form.
 * Loaded via: hooks.py `doctype_js["Purchase Order"]` (alongside vue.global.js,
 *   comments.js and procurement_links.js).
 *
 * `Purchase Order Item.project` is mandatory so material and subcontract cost
 * lands on the job. ERPNext, though, never pushes the PO's *header* Project down
 * to the item rows, so a PO that plainly names its job still refused to save
 * until someone re-picked the same project on every line. This makes the header
 * the one place a purchaser has to say it:
 *
 *   - picking a header Project fills every line that hasn't got one;
 *   - a newly added row inherits it;
 *   - one last sweep on `validate` catches rows pulled in by "Get Items From"
 *     (Material Request / Supplier Quotation), which bypass the row-add event.
 *
 * Blank rows only — a PO that legitimately spans two jobs keeps its per-line
 * values. When there is no header Project at all, `refresh` shows an inline
 * hint listing the overhead buckets, so a non-job purchase (office supplies,
 * shop consumables, fuel) has an obvious home instead of a dead end.
 *
 * erpnext_enhancements.procurement_project repeats the fill server-side for the
 * REST API / data-import paths that never load a form script.
 *
 * Where a line is received (v1.809.0, Nik 2026-11-12). The same project decides the line's
 * Warehouse: a line for a job defaults to the Project Shelf, a line for an Overhead project
 * to the item's own bin (the rule is the server's, `procurement_project.line_warehouses`).
 * It is a default, not a lock:
 *
 *   - it is asked for when a line first has both its item and its project, and again when
 *     either changes;
 *   - **a warehouse a person picked by hand stays.** The server says which that is (`keep`):
 *     when a line gets its item, ERPNext has just filled the warehouse itself, so only one
 *     of ERPNext's own values (the shelf, the item's default, the Company's default) is
 *     replaced; when only the project changes, only the rule's own earlier value is
 *     replaced. This file sends what the rule last set on the line (`applied`) and the item
 *     and project the line had before, and does not decide anything itself;
 *   - a line that came from a Material Request keeps the request's warehouse.
 *
 * ERPNext fills the Warehouse from the Item Default when an item is picked, in the same
 * event. Its handler runs after this file's (form-script handlers run before the
 * controller's), so the rule waits for that request to come back before it writes.
 *
 * Nothing here is needed for the result to be right. The one thing that must hold, an
 * Overhead line never saved on the Project Shelf, is enforced by the server on every save
 * (`procurement_project.default_line_warehouses`). This file only shows it sooner.
 */
frappe.provide("erpnext_enhancements.procurement");

erpnext_enhancements.procurement.fill_blank_line_projects = function (frm) {
	const project = frm.doc.project;
	if (!project) return;

	let changed = false;
	(frm.doc.items || []).forEach((row) => {
		if (!row.project) {
			row.project = project;
			changed = true;
		}
	});
	if (changed) frm.refresh_field("items");
};

const LINE_WAREHOUSES = "erpnext_enhancements.procurement_project.line_warehouses";

/** What a line's default location depends on; null until the line has both. */
function line_key(row) {
	return row && row.item_code && row.project ? { item_code: row.item_code, project: row.project } : null;
}

function same_key(a, b) {
	return !!a && !!b && a.item_code === b.item_code && a.project === b.project;
}

/**
 * Take every line on the form as it stands: its item and project are the ones its location
 * was decided for. Called when a document opens and after each save (a save renames new
 * rows, so their memory has to be taken again). What the rule set before is forgotten with
 * it; for such a line the server works out the rule's earlier value from the line's
 * previous project.
 */
function remember_line_warehouses(frm) {
	const keys = {};
	(frm.doc.items || []).forEach((row) => {
		const key = line_key(row);
		if (key) keys[row.name] = key;
	});
	frm._ee_line_keys = keys;
	frm._ee_line_applied = {};
	frm._ee_line_doc = frm.doc.name;
}

/**
 * Ask the server for the default location of each line whose item or project has just
 * changed, and set it unless the server says the line's location is a person's pick.
 * Returns a promise that never rejects: a failed lookup leaves the line as it is, and the
 * server still moves an Overhead line off the Project Shelf on save.
 */
erpnext_enhancements.procurement.apply_line_warehouses = function (frm) {
	if (frm.doc.docstatus !== 0) return Promise.resolve();
	if (frm._ee_line_doc !== frm.doc.name || !frm._ee_line_keys) remember_line_warehouses(frm);
	const keys = frm._ee_line_keys;
	const applied = frm._ee_line_applied;
	const todo = (frm.doc.items || []).filter((row) => {
		const key = line_key(row);
		return key && !same_key(keys[row.name], key) && !row.material_request_item;
	});
	if (!todo.length) return Promise.resolve();
	return frappe
		.call({
			method: LINE_WAREHOUSES,
			args: {
				rows: todo.map((row) => {
					const was = keys[row.name] || {};
					return {
						key: row.name,
						item_code: row.item_code,
						project: row.project,
						warehouse: row.warehouse || "",
						was_item_code: was.item_code || "",
						was_project: was.project || "",
						applied: applied[row.name] || "",
					};
				}),
				company: frm.doc.company,
			},
		})
		.then((r) => {
			const answers = (r && r.message) || {};
			todo.forEach((row) => {
				const live = (locals[row.doctype] || {})[row.name];
				const answer = answers[row.name];
				// The line was removed or changed while the request was out: its next event asks again.
				if (!live || !answer || answer.item_code !== live.item_code || answer.project !== live.project) return;
				keys[live.name] = line_key(live);
				// A place a person picked stays (the server's call), and so does a line the rule has no place for.
				if (answer.keep || !answer.warehouse) return;
				applied[live.name] = answer.warehouse;
				if (live.warehouse !== answer.warehouse) {
					frappe.model.set_value(live.doctype, live.name, "warehouse", answer.warehouse);
				}
			});
		})
		.catch(() => {});
};

/** Apply the rule once the requests an event started (ERPNext's item details) have come back. */
function apply_line_warehouses_later(frm) {
	clearTimeout(frm._ee_line_timer);
	frm._ee_line_timer = setTimeout(() => {
		frappe.after_ajax(() => erpnext_enhancements.procurement.apply_line_warehouses(frm));
	}, 300);
}

frappe.ui.form.on("Purchase Order", {
	refresh: function (frm) {
		show_overhead_hint(frm);
		// A document that has just opened, or just been saved: every line it has is decided.
		if (frm._ee_line_doc !== frm.doc.name || !frm.is_dirty()) remember_line_warehouses(frm);
	},

	project: function (frm) {
		erpnext_enhancements.procurement.fill_blank_line_projects(frm);
		show_overhead_hint(frm);
		apply_line_warehouses_later(frm);
	},

	// Parent-level event: fires when a row is added to the `items` grid.
	items_add: function (frm, cdt, cdn) {
		if (!frm.doc.project) return;
		frappe.model.set_value(cdt, cdn, "project", frm.doc.project);
	},

	validate: function (frm) {
		erpnext_enhancements.procurement.fill_blank_line_projects(frm);
		// The last sweep, awaited by the save: a line an event missed still gets its default.
		return erpnext_enhancements.procurement.apply_line_warehouses(frm);
	},

	after_save: function (frm) {
		remember_line_warehouses(frm);
	},
});

frappe.ui.form.on("Purchase Order Item", {
	item_code: function (frm) {
		apply_line_warehouses_later(frm);
	},

	project: function (frm) {
		apply_line_warehouses_later(frm);
	},
});

/**
 * Nudge toward the overhead buckets when a draft PO has no Project at all —
 * without it, a purchaser buying office supplies hits the mandatory error on
 * every line with no hint that a legitimate non-job target exists.
 */
function show_overhead_hint(frm) {
	if (frm.doc.docstatus !== 0 || frm.doc.project) {
		// The dashboard headline is a single shared slot — only take it back if
		// this script is what put something there.
		if (frm._ee_overhead_hint) {
			frm.dashboard.clear_comment();
			frm._ee_overhead_hint = false;
		}
		return;
	}

	overhead_projects().then((projects) => {
		// The list is fetched once per page load, so re-check what changed while
		// it was in flight before writing to the dashboard.
		if (!projects.length || frm.doc.project || frm.doc.docstatus !== 0) return;

		const names = projects.map((p) => frappe.utils.escape_html(p.project_name)).join(", ");
		frm.dashboard.add_comment(
			__(
				"Every line needs a Project. Not buying for a job? Set the Project above to one of the overhead buckets ({0}) and every line will inherit it.",
				[names]
			),
			"blue",
			true
		);
		frm._ee_overhead_hint = true;
	});
}

/** The overhead buckets, fetched once per page load (they change ~never). */
let overhead_projects_promise = null;

function overhead_projects() {
	if (!overhead_projects_promise) {
		overhead_projects_promise = frappe
			.call({ method: "erpnext_enhancements.procurement_project.get_overhead_projects" })
			.then((r) => (r && r.message) || [])
			// Never let a failed lookup break the form; just show no hint.
			.catch(() => []);
	}
	return overhead_projects_promise;
}
