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
 */

(function () {
	const M = "erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract";

	const CUSTOMER_TYPES = [
		{ value: "owner", label: __("Owner Contract (Design / Build / Maintain)") },
		{ value: "rental", label: __("Rental Agreement") },
		{ value: "maintenance", label: __("Maintenance Services Agreement") },
		{ value: "sow", label: __("Statement of Work (subcontractor — requires signed MSA)") },
	];
	const SUPPLIER_TYPES = [
		{ value: "msa", label: __("Master Subcontractor Agreement") },
		{ value: "sow", label: __("Statement of Work (requires signed MSA)") },
		{ value: "nda", label: __("Mutual Non-Disclosure Agreement") },
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

	// SOWs are gated on a Signed MSA: proceed when one exists, otherwise
	// offer to create the MSA for that supplier instead.
	function create_sow_with_gate(supplier, source_doctype, source_name) {
		frappe.call(`${M}.get_signed_msa`, { supplier: supplier }).then((r) => {
			if (r.message) {
				create_contract("sow", source_doctype, source_name, { party: supplier });
			} else {
				frappe.confirm(
					__(
						"No signed Master Subcontractor Agreement exists for {0} yet — an SOW can only be issued under a signed MSA. Create the MSA now?",
						[supplier.bold()]
					),
					() => create_contract("msa", "Supplier", supplier)
				);
			}
		});
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
						{
							fieldname: "supplier",
							fieldtype: "Link",
							label: __("Subcontractor (Supplier)"),
							options: "Supplier",
							depends_on: 'eval:doc.template=="sow"',
							mandatory_depends_on: 'eval:doc.template=="sow"',
							description: __(
								"The SOW's scope of work prefils from this {0}'s Customer Requests and Deliverables tables.",
								[__(source_doctype)]
							),
						},
					],
					primary_action_label: __("Create Draft"),
					primary_action(values) {
						d.hide();
						if (values.template === "sow") {
							const supplier =
								source_doctype === "Supplier" ? frm.doc.name : values.supplier;
							create_sow_with_gate(supplier, source_doctype, frm.doc.name);
						} else {
							create_contract(values.template, source_doctype, frm.doc.name);
						}
					},
				});
				if (source_doctype === "Supplier") {
					d.set_df_property("supplier", "hidden", 1);
					d.set_df_property("supplier", "mandatory_depends_on", "");
				}
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
