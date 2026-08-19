// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// The supervisor's side of a sign-off.
//
// Same shape of gap as ask-the-author, and the same reason it lasted: the
// obvious path produces a correct record and says nothing. `TrainingSignoff`'s
// controller is thorough — it re-derives `supervisor_user` from the Employee,
// refuses a self-sign-off on both links, demands a note when the outcome is
// "Needs More Practice", and stamps `signed_on` — so filling the form in and
// pressing Submit files a completely valid attestation.
//
// It just never tells the learner. The notification lives in
// `training.signoff.record_signoff`, which had no caller anywhere until v1.334.0,
// so the learner sat on the "Waiting for sign-off" screen with no way to know
// they had been signed off.
//
// Hence the two things here, and as with the Q&A form the second matters more:
//
//   1. a "Record sign-off" action that routes through the endpoint;
//   2. a warning the moment `outcome` is set directly on the form, because that
//      is the path a supervisor will take and it is the one that goes quiet.

frappe.ui.form.on("Training Signoff", {
	refresh(frm) {
		frm.trigger("ee_record_action");
	},

	ee_record_action(frm) {
		// Draft only. A submitted sign-off is evidence; changing one is a
		// cancel-and-reissue, and the endpoint refuses it outright.
		if (frm.is_new() || frm.doc.docstatus !== 0) return;

		frm.add_custom_button(__("Record sign-off"), () => frm.trigger("ee_open_signoff_dialog"))
			.addClass("btn-primary");
	},

	ee_open_signoff_dialog(frm) {
		const dialog = new frappe.ui.Dialog({
			title: __("Record this sign-off"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "context",
					options: `<p class="text-muted">${__("Confirming that {0} can do this unsupervised.", [
						frappe.utils.escape_html(frm.doc.user || ""),
					])}</p>`,
				},
				{
					fieldtype: "Select",
					fieldname: "outcome",
					label: __("Outcome"),
					reqd: 1,
					options: ["Competent", "Needs More Practice"],
					default: frm.doc.outcome || "Competent",
				},
				{
					fieldtype: "Text",
					fieldname: "competency_notes",
					label: __("What you observed"),
					default: frm.doc.competency_notes || "",
					// Not `reqd` here even though the controller demands one for
					// "Needs More Practice": making it always-required would push
					// supervisors to type a full stop. The server asks for it exactly
					// when it is load-bearing, and its message says why.
					description: __(
						"Required if this is not a pass — the learner needs to know what to work on."
					),
				},
			],
			primary_action_label: __("Record"),
			primary_action(values) {
				dialog.hide();
				frappe.dom.freeze(__("Recording…"));
				frappe
					.call({
						method: "erpnext_enhancements.training.signoff.record_signoff",
						args: {
							signoff: frm.doc.name,
							outcome: values.outcome,
							competency_notes: values.competency_notes || null,
							// Whatever is already attached to the form. Deliberately not a
							// canvas in this dialog: `signature_image` is an Attach Image and
							// wants a file URL, and a data: URL stuffed into one renders
							// nowhere and survives no export. The portal's canvas writes a
							// real File; the Desk path uses the field that already works.
							signature_image: frm.doc.signature_image || null,
						},
					})
					.then((r) => {
						frappe.dom.unfreeze();
						const result = (r && r.message) || {};
						frm.reload_doc();
						// `notified` is reported rather than assumed, for the same reason
						// as the Q&A form: `_notify` is best-effort by design — a learner
						// with no email address must not fail an attestation — and a
						// supervisor told "sent" when nothing was sent will not follow up.
						if (result.notified) {
							frappe.show_alert({
								message: __("Recorded. The learner has been told."),
								indicator: "green",
							});
						} else {
							frappe.msgprint({
								title: __("Recorded, but not delivered"),
								indicator: "orange",
								message: __(
									"The sign-off is submitted and counts immediately. No notification went out — check that the learner has an email address on their User record, because they are sitting on a screen that says they are waiting for you."
								),
							});
						}
					})
					.catch(() => {
						frappe.dom.unfreeze();
					});
			},
		});
		dialog.show();
	},

	outcome(frm) {
		// Fires on any edit, including the one that matters: a supervisor setting the
		// outcome here and submitting. The record that produces is correct and the
		// learner is never told, because the notification lives in the endpoint the
		// button above calls and not in the DocType's controller.
		if (frm.is_new() || !frm.doc.outcome || frm.doc.docstatus !== 0) return;
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(
			__(
				"Submitting this form records the sign-off but does not notify the learner. Use <b>Record sign-off</b> to tell them."
			),
			"orange"
		);
	},
});
