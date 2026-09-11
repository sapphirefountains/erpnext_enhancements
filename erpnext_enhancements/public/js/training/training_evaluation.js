// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Training Evaluation form — recording the outcome, which files the sign-off.
//
// The competency verdict is deliberately NOT a field edit here: `record_evaluation`
// creates and submits a real Training Signoff (the same attestation a manual
// sign-off produces), links it back, and marks the evaluation Completed. So the
// button, not a status change, is how an evaluation resolves — and it re-checks
// permission server-side, whatever this form chooses to show.

frappe.ui.form.on("Training Evaluation", {
	refresh(frm) {
		if (frm.is_new()) return;

		const is_manager = frappe.user.has_role(["Training Manager", "System Manager"]);
		// The evaluator (by their own login) or a Training Manager records it; never
		// the learner. The endpoint enforces this too.
		const may_record =
			frm.doc.status === "Scheduled" &&
			frm.doc.learner !== frappe.session.user &&
			(is_manager || frm.doc.evaluator_user === frappe.session.user);

		if (may_record) {
			frm.add_custom_button(__("Record Outcome"), () => record_outcome(frm)).addClass("btn-primary");
		}
		if (frm.doc.signoff) {
			frm.add_custom_button(__("Open Sign-off"), () =>
				frappe.set_route("Form", "Training Signoff", frm.doc.signoff)
			);
		}
	},
});

function record_outcome(frm) {
	frappe.prompt(
		[
			{
				fieldname: "outcome",
				fieldtype: "Select",
				label: __("Outcome"),
				reqd: 1,
				options: ["Competent", "Needs More Practice"].join("\n"),
				description: __(
					"Competent files the sign-off and lets the course complete — it means you would send them alone. Supervised Only records that they did the whole job with you there, which is real progress and does not complete the course. Needs More Practice is \"come back to me\"."
				),
			},
			{
				fieldname: "competency_notes",
				fieldtype: "Small Text",
				label: __("Competency notes"),
				description: __("What was actually observed. Required for anything but Competent."),
			},
		],
		(values) => {
			frappe.call({
				method: "erpnext_enhancements.training.evaluations.record_evaluation",
				args: {
					evaluation: frm.doc.name,
					outcome: values.outcome,
					competency_notes: values.competency_notes,
				},
				freeze: true,
				freeze_message: __("Recording…"),
				callback(r) {
					const out = r.message || {};
					frappe.show_alert({
						message: __("Recorded — sign-off {0} filed.", [out.signoff || ""]),
						indicator: "green",
					});
					frm.reload_doc();
				},
			});
		},
		__("Record Evaluation Outcome"),
		__("Record")
	);
}
