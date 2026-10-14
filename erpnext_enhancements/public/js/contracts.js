/**
 * @file "Generate Contract" buttons (Phase 4 contract generation).
 * @description
 * Adds a Create > Generate Contract action to Opportunity, Project, and
 * Supplier forms. Customer agreements (Owner / Rental / Maintenance)
 * generate from Opportunity or Project; subcontractor agreements (MSA /
 * SOW) generate from Supplier — and an SOW can also be generated from a
 * Project/Opportunity by picking the subcontractor in the dialog, which
 * prefils the scope of work from the source's request/deliverable tables.
 *
 * Sequencing rule from the Jun 9 follow-up: a Statement of Work requires a
 * Signed Master Subcontractor Agreement — every SOW path checks for one up
 * front (get_signed_msa) and offers to create the MSA instead when missing,
 * rather than letting the user fill a form that will refuse to save.
 *
 * v1.686.0: the dialog, the SOW gate and the create call moved to
 * global_enhancements/record_actions.js (erpnext_enhancements.record_actions),
 * unchanged, shared with the guided page's Actions menu, which generates a
 * contract the same way. This file only puts the button on the three forms.
 */

(function () {
	function add_generate_button(frm, source_doctype) {
		const actions = window.erpnext_enhancements && erpnext_enhancements.record_actions;
		// A tab still holding the bundle from before v1.686.0 has no record_actions:
		// no button until it reloads, rather than one that throws. The master switch
		// (frappe.boot.ee_process_automation) is checked inside can_generate_contract.
		if (!actions || !actions.can_generate_contract(source_doctype)) return;
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Generate Contract"),
			() => actions.open_generate_contract(source_doctype, frm.doc.name),
			__("Create")
		);
	}

	frappe.ui.form.on("Opportunity", {
		refresh(frm) {
			add_generate_button(frm, "Opportunity");
		},
	});

	frappe.ui.form.on("Project", {
		refresh(frm) {
			add_generate_button(frm, "Project");
		},
	});

	frappe.ui.form.on("Supplier", {
		refresh(frm) {
			add_generate_button(frm, "Supplier");
		},
	});
})();
