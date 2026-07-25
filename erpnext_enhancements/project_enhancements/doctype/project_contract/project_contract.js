/**
 * @file Project Contract form behavior.
 * @description
 * - Filters the MSA link on SOWs to Signed MSAs of the selected supplier.
 * - SOW scope of work pulls from the linked source's Customer Requests /
 *   Deliverables tables (Project once it exists, else Opportunity — both
 *   carry the same scope model): automatically when the link is set on an
 *   empty-scope draft, and on demand via "Pull Scope from Source" (which
 *   overwrites after a confirm).
 * - "Send for Signature" emails the customer a tokenised signing link and shows
 *   the live request's state (opened / expires / resend / void). Gated by
 *   frappe.boot.ee_contract_esign; with it off the form behaves exactly as it
 *   did before online signing shipped.
 * - "Mark as Signed" remains for paper and in-person signatures, recording how
 *   it was signed so every executed contract carries an evidence trail.
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
		const signable = frm.doc.docstatus === 1 && frm.doc.status !== "Signed";

		// Online signing. With the flag off this whole block is inert and the form
		// is byte-identical to what it was before e-signature shipped.
		if (signable && frappe.boot.ee_contract_esign) {
			render_signature_state(frm);
			frm.add_custom_button(__("Send for Signature"), () => send_for_signature(frm)).addClass(
				"btn-primary"
			);
		}

		if (signable) {
			// The paper fallback: still here for in-person and wet signatures, but
			// demoted under a Manual group now that online signing is the norm.
			frm.add_custom_button(
				__("Mark as Signed"),
				() => {
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
							{
								fieldname: "how",
								fieldtype: "Select",
								label: __("Signed How"),
								options: ["Paper / in person", "Scanned copy returned", "Other"],
								default: "Paper / in person",
								reqd: 1,
							},
						],
						primary_action_label: __("Save"),
						primary_action(values) {
							d.hide();
							frm.set_value("status", "Signed");
							frm.set_value("signed_by", values.signed_by);
							frm.set_value("signed_on", values.signed_on);
							frm.save("Update").then(() => {
								// Every signed contract gets an evidence trail, whichever
								// route it came in by.
								frm.timeline &&
									frm.timeline.insert_comment &&
									frm.timeline.insert_comment(
										__("Marked signed manually — {0}, recorded by {1}.", [
											values.how,
											frappe.session.user,
										])
									);
							});
						},
					});
					d.show();
				},
				frappe.boot.ee_contract_esign ? __("Manual") : null
			);
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

// ---------------------------------------------------------------------------
// Online signing (gated by frappe.boot.ee_contract_esign)
// ---------------------------------------------------------------------------

function send_for_signature(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Send for Signature"),
		fields: [
			{
				fieldname: "recipient_email",
				fieldtype: "Data",
				options: "Email",
				label: __("Send to"),
				default: frm.doc.contact_email,
				reqd: 1,
				description: __("They'll be asked to re-type this address to confirm it's them."),
			},
			{
				fieldname: "message",
				fieldtype: "Small Text",
				label: __("Note (optional)"),
				description: __("Included in the email above the signing button."),
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			d.hide();
			frappe.call({
				method: "erpnext_enhancements.project_enhancements.esign.api.send_for_signature",
				args: { project_contract: frm.doc.name, ...values },
				freeze: true,
				freeze_message: __("Sending…"),
				callback(r) {
					if (!r.message) return;
					frappe.show_alert(
						{ message: __("Sent to {0}", [r.message.to]), indicator: "green" },
						5
					);
					frm.reload_doc();
				},
			});
		},
	});
	d.show();
}

function render_signature_state(frm) {
	frappe.call({
		method: "erpnext_enhancements.project_enhancements.esign.api.get_signature_state",
		args: { project_contract: frm.doc.name },
		callback(r) {
			const state = r.message;
			if (!state || !state.name) return;

			const live = ["Sent", "Viewed"].includes(state.status);
			const seen = state.view_count
				? __("opened {0}×", [state.view_count])
				: __("not opened yet");
			frm.dashboard.add_comment(
				live
					? __("Out for signature to {0} — {1}. Link expires {2}.", [
							state.signer_email,
							seen,
							frappe.datetime.str_to_user(state.expires_on),
					  ])
					: __("Signature request {0} ({1}).", [state.name, state.status]),
				live ? "blue" : "gray",
				true
			);

			if (!live) return;
			frm.add_custom_button(
				__("Resend Link"),
				() =>
					frappe.call({
						method: "erpnext_enhancements.project_enhancements.esign.api.resend_for_signature",
						args: { request_name: state.name },
						freeze: true,
						callback(res) {
							if (!res.message) return;
							frappe.show_alert(
								{ message: __("A fresh link is on its way — the old one no longer works."), indicator: "green" },
								7
							);
							frm.reload_doc();
						},
					}),
				__("Signature")
			);
			frm.add_custom_button(
				__("Void Request"),
				() =>
					frappe.prompt(
						{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (optional)") },
						(values) =>
							frappe.call({
								method:
									"erpnext_enhancements.project_enhancements.esign.api.void_signature_request",
								args: { request_name: state.name, reason: values.reason },
								freeze: true,
								callback() {
									frm.reload_doc();
								},
							}),
						__("Void Signing Link"),
						__("Void")
					),
				__("Signature")
			);
		},
	});
}
