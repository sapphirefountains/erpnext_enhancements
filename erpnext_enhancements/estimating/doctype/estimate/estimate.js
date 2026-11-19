// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/*
 * The Estimate form. The lines editor itself is estimate_editor.bundle.js (public/js/estimating/editor.js);
 * this script loads it, mounts it into `lines_editor`, and wires the buttons and the fields that change
 * what it shows. The table view under "Phases and Lines" edits the same rows.
 */

// The editor bundle's API level this script needs; equal to VERSION in public/js/estimate_editor.bundle.js
// (scripts/test_estimate_math.mjs fails the build if they differ). A tab open across a deploy can pair this
// script with an older bundle, so it asks first and draws a "reload" note rather than throwing.
const REQUIRED_EDITOR_LEVEL = 1;

const RATE_FIELDS = [
	"labor_rate",
	"tax_pct",
	"markup_material",
	"markup_equipment",
	"markup_labor",
	"markup_subcontract",
	"markup_expense",
	"contingency_pct",
	"insurance_pct",
	"fee_pct",
	"commission_pct",
];

function with_editor(frm, fn) {
	frappe.require(["estimate_editor.bundle.js", "estimate_editor.bundle.css"], () => {
		const ed = window.sf_estimate_editor;
		if (!ed || !(ed.version >= REQUIRED_EDITOR_LEVEL)) {
			const wrap = frm.fields_dict.lines_editor && frm.fields_dict.lines_editor.$wrapper;
			if (wrap) {
				wrap.html(
					`<div class="alert alert-warning">${__(
						"The estimate editor was updated. Reload this page to use it."
					)}</div>`
				);
			}
			return;
		}
		fn(ed);
	});
}

frappe.ui.form.on("Estimate", {
	async onload(frm) {
		// A new estimate copies the default rate card at once, so the editor prices while you type
		// (the server would copy it on first save anyway).
		if (frm.is_new() && !RATE_FIELDS.some((f) => Number(frm.doc[f]))) {
			const card =
				frm.doc.rate_card ||
				(await frappe.db.get_value("Estimate Rate Card", { is_default: 1, disabled: 0 }, "name")).message?.name;
			if (card) {
				frm.doc.rate_card = card;
				with_editor(frm, (ed) => ed.copy_rates(frm, card));
			}
		}
	},

	refresh(frm) {
		with_editor(frm, (ed) => ed.mount(frm));
		const editable = frm.doc.status !== "Superseded" && frm.perm[0] && frm.perm[0].write;
		if (!frm.is_new() && editable) {
			frm.add_custom_button(__("Issue Quotation"), () => with_editor(frm, (ed) => ed.issue(frm)));
			frm.add_custom_button(__("Apply Template"), () => with_editor(frm, (ed) => ed.apply_template(frm, frm.doc.template)), __("Actions"));
			frm.add_custom_button(__("New Revision"), () => with_editor(frm, (ed) => ed.new_revision(frm)), __("Actions"));
		}
		if (frm.doc.quotation) {
			frm.add_custom_button(__("Quotation {0}", [frm.doc.quotation]), () =>
				frappe.set_route("Form", "Quotation", frm.doc.quotation)
			, __("View"));
		}
		if (frm.doc.previous_revision) {
			frm.add_custom_button(__("Previous Revision"), () =>
				frappe.set_route("Form", "Estimate", frm.doc.previous_revision)
			, __("View"));
		}
	},

	template(frm) {
		if (frm.doc.template && !(frm.doc.lines || []).length) {
			with_editor(frm, (ed) => ed.apply_template(frm, frm.doc.template));
		}
	},

	rate_card(frm) {
		if (!frm.doc.rate_card) return;
		frappe.confirm(
			__("Copy every rate from {0} onto this estimate? Rates typed here are replaced.", [frm.doc.rate_card]),
			() => with_editor(frm, (ed) => ed.copy_rates(frm, frm.doc.rate_card))
		);
	},

	opportunity(frm) {
		if (frm.doc.opportunity && !frm.doc.title) {
			frappe.db.get_value("Opportunity", frm.doc.opportunity, "title").then((r) => {
				if (r.message && r.message.title && !frm.doc.title) frm.set_value("title", r.message.title);
			});
		}
	},
});

// Any rate typed in the Rates section, or any row edited in the table view, moves the editor's numbers.
// Skipped while the editor is the one writing (frm.__estimate_editing): it updates itself, and a redraw
// mid-edit would take away the field the person just tapped.
const refresh_numbers = (frm) => !frm.__estimate_editing && with_editor(frm, (ed) => ed.refresh(frm));
const redraw = (frm) => !frm.__estimate_editing && with_editor(frm, (ed) => ed.redraw(frm));
const rate_handlers = {};
RATE_FIELDS.forEach((f) => (rate_handlers[f] = refresh_numbers));
frappe.ui.form.on("Estimate", rate_handlers);

const line_handlers = { lines_remove: redraw, lines_add: redraw };
["qty", "unit_cost", "sheet_price", "markup_override", "contingency_override", "insurance_override", "fee_override", "commission_override"].forEach(
	(f) => (line_handlers[f] = refresh_numbers)
);
["phase_number", "description", "cost_type", "cost_code", "basis", "by_others", "uom"].forEach((f) => (line_handlers[f] = redraw));
frappe.ui.form.on("Estimate Line", line_handlers);

const phase_handlers = { phases_remove: redraw, phase_number: redraw, label: redraw };
["contingency_override", "insurance_override", "fee_override", "commission_override"].forEach((f) => (phase_handlers[f] = refresh_numbers));
frappe.ui.form.on("Estimate Phase", phase_handlers);
