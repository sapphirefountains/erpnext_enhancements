/**
 * @file Project Contract form behavior.
 * @description
 * - Filters the MSA link on SOWs to Signed MSAs of the selected supplier.
 * - SOW scope of work pulls from the linked source's Customer Requests /
 *   Deliverables tables (Project once it exists, else Opportunity — both
 *   carry the same scope model): automatically when the link is set on an
 *   empty-scope draft, and on demand via "Pull Scope from Source" (which
 *   overwrites after a confirm).
 * - "Mark as Signed" button on submitted contracts stamps status/signed_on
 *   (asking who signed), completing the paper flow until e-sign lands.
 * - "Preview / Print" jumps straight to the Project Contract Print format.
 * - Signed Maintenance Services Agreements get "Create > Maintenance
 *   Contract", mapping the legal terms into an operational Sapphire
 *   Maintenance Contract (visit scheduling + modular visit forms).
 */

function ee_scope_source(frm) {
	if (frm.doc.project) return ["Project", frm.doc.project];
	if (frm.doc.opportunity) return ["Opportunity", frm.doc.opportunity];
	return null;
}

function ee_pull_scope(frm, { quiet = false } = {}) {
	const source = ee_scope_source(frm);
	if (!source) {
		if (!quiet) frappe.msgprint(__("Link a Project or Opportunity first."));
		return;
	}
	frappe
		.call(
			"erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract.compose_scope_of_work",
			{ source_doctype: source[0], source_name: source[1] }
		)
		.then((r) => {
			if (r.message) {
				frm.set_value("scope_of_work", r.message);
				if (!quiet) {
					frappe.show_alert({
						message: __("Scope pulled from {0} {1}", [__(source[0]), source[1]]),
						indicator: "green",
					});
				}
			} else if (!quiet) {
				frappe.msgprint(
					__("{0} {1} has no Customer Requests or Deliverables to pull.", [
						__(source[0]),
						source[1],
					])
				);
			}
		});
}

function ee_maybe_autofill_scope(frm) {
	// only on an editable SOW draft whose scope is still empty
	if (frm.doc.template_key !== "sow" || frm.doc.docstatus !== 0) return;
	if (frm.doc.scope_of_work) return;
	ee_pull_scope(frm, { quiet: true });
}

frappe.ui.form.on("Project Contract", {
	setup(frm) {
		frm.set_query("msa_contract", () => ({
			filters: {
				template_key: "msa",
				party: frm.doc.party || "",
				docstatus: 1,
				status: "Signed",
			},
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 1 && frm.doc.status !== "Signed") {
			frm.add_custom_button(__("Mark as Signed"), () => {
				const d = new frappe.ui.Dialog({
					title: __("Mark as Signed"),
					fields: [
						{ fieldname: "signed_by", fieldtype: "Data", label: __("Signed By (name)"), reqd: 1 },
						{
							fieldname: "signed_on",
							fieldtype: "Date",
							label: __("Signed On"),
							default: frappe.datetime.get_today(),
							reqd: 1,
						},
					],
					primary_action_label: __("Save"),
					primary_action(values) {
						d.hide();
						frm.set_value("status", "Signed");
						frm.set_value("signed_by", values.signed_by);
						frm.set_value("signed_on", values.signed_on);
						frm.save("Update");
					},
				});
				d.show();
			}).addClass("btn-primary");
		}

		if (!frm.is_new()) {
			frm.add_custom_button(__("Preview / Print"), () => {
				frappe.set_route("print", "Project Contract", frm.doc.name);
			});
		}

		if (frm.doc.template_key === "maintenance" && frm.doc.status === "Signed") {
			frm.add_custom_button(
				__("Maintenance Contract"),
				() => {
					frappe.model.open_mapped_doc({
						method:
							"erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract.make_contract_from_project_contract",
						frm: frm,
					});
				},
				__("Create")
			);
		}

		if (frm.doc.template_key === "sow" && frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.add_custom_button(__("Pull Scope from Source"), () => {
				if (frm.doc.scope_of_work) {
					frappe.confirm(
						__("Replace the current Scope of Work with the source's Customer Requests and Deliverables?"),
						() => ee_pull_scope(frm)
					);
				} else {
					ee_pull_scope(frm);
				}
			});
		}
	},

	project(frm) {
		ee_maybe_autofill_scope(frm);
	},

	opportunity(frm) {
		ee_maybe_autofill_scope(frm);
	},
});
