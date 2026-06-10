/**
 * @file "Generate Contract" buttons (Phase 4 contract generation).
 * @description
 * Adds a Create > Generate Contract action to Opportunity, Project, and
 * Supplier forms. The dialog offers the contract types whose party model
 * fits the source (Customer types on Opportunity/Project, Supplier types on
 * Supplier), then calls the whitelisted
 * `project_contract.create_contract`, which prefils the draft from the
 * source document and returns its name for routing.
 *
 * Sequencing rule from the Jun 9 follow-up: a Statement of Work requires a
 * Signed Master Subcontractor Agreement — the SOW option checks for one up
 * front (get_signed_msa) and offers to create the MSA instead when missing,
 * rather than letting the user fill a form that will refuse to save.
 */

(function () {
	const M = "erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract";

	const CUSTOMER_TYPES = [
		{ value: "owner", label: __("Owner Contract (Design / Build / Maintain)") },
		{ value: "rental", label: __("Rental Agreement") },
		{ value: "maintenance", label: __("Maintenance Services Agreement") },
	];
	const SUPPLIER_TYPES = [
		{ value: "msa", label: __("Master Subcontractor Agreement") },
		{ value: "sow", label: __("Statement of Work (requires signed MSA)") },
	];

	function create_contract(template, source_doctype, source_name, extra) {
		frappe
			.call(`${M}.create_contract`, {
				template: template,
				source_doctype: source_doctype,
				source_name: source_name,
				...(extra || {}),
			})
			.then((r) => frappe.set_route("Form", "Project Contract", r.message));
	}

	function add_generate_button(frm, types, source_doctype) {
		if (frm.is_new()) return;
		frm.add_custom_button(
			__("Generate Contract"),
			() => {
				const d = new frappe.ui.Dialog({
					title: __("Generate Contract"),
					fields: [
						{
							fieldname: "template",
							fieldtype: "Select",
							label: __("Contract Type"),
							reqd: 1,
							options: types.map((t) => ({ value: t.value, label: t.label })),
						},
					],
					primary_action_label: __("Create Draft"),
					primary_action(values) {
						d.hide();
						if (values.template === "sow" && source_doctype === "Supplier") {
							// gate early: an SOW needs a Signed MSA for this supplier
							frappe
								.call(`${M}.get_signed_msa`, { supplier: frm.doc.name })
								.then((r) => {
									if (r.message) {
										create_contract("sow", source_doctype, frm.doc.name);
									} else {
										frappe.confirm(
											__(
												"No signed Master Subcontractor Agreement exists for {0} yet — an SOW can only be issued under a signed MSA. Create the MSA now?",
												[frm.doc.name.bold()]
											),
											() => create_contract("msa", source_doctype, frm.doc.name)
										);
									}
								});
						} else {
							create_contract(values.template, source_doctype, frm.doc.name);
						}
					},
				});
				d.show();
			},
			__("Create")
		);
	}

	frappe.ui.form.on("Opportunity", {
		refresh(frm) {
			add_generate_button(frm, CUSTOMER_TYPES, "Opportunity");
		},
	});

	frappe.ui.form.on("Project", {
		refresh(frm) {
			add_generate_button(frm, CUSTOMER_TYPES, "Project");
		},
	});

	frappe.ui.form.on("Supplier", {
		refresh(frm) {
			add_generate_button(frm, SUPPLIER_TYPES, "Supplier");
		},
	});
})();
