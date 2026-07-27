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

frappe.ui.form.on("Purchase Order", {
	refresh: function (frm) {
		show_overhead_hint(frm);
	},

	project: function (frm) {
		erpnext_enhancements.procurement.fill_blank_line_projects(frm);
		show_overhead_hint(frm);
	},

	// Parent-level event: fires when a row is added to the `items` grid.
	items_add: function (frm, cdt, cdn) {
		if (!frm.doc.project) return;
		frappe.model.set_value(cdt, cdn, "project", frm.doc.project);
	},

	validate: function (frm) {
		erpnext_enhancements.procurement.fill_blank_line_projects(frm);
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
