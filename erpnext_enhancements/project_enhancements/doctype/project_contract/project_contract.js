/**
 * @file Project Contract form behavior.
 * @description
 * - Filters the MSA link on SOWs to Signed MSAs of the selected supplier.
 * - "Mark as Signed" button on submitted contracts stamps status/signed_on
 *   (asking who signed), completing the paper flow until e-sign lands.
 * - "Preview / Print" jumps straight to the Project Contract Print format.
 */

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
	},
});
