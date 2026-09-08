// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Training Submission form — grading a learner's hand-in.
//
// Grading goes through `submissions.grade_submission`, not a raw field edit, so the
// one place that stamps the grader, times it and mails the learner is the endpoint.
// The button re-checks the Training Manager role server-side whatever this form
// chooses to show — a learner holding the Training Learner role can call any
// whitelisted method directly.

frappe.ui.form.on("Training Submission", {
	refresh(frm) {
		if (frm.is_new()) return;

		// The queue lives in the list view (status = Submitted / Under Review). This
		// button is the single-record action from inside one of them.
		const is_manager = frappe.user.has_role(["Training Manager", "System Manager"]);
		if (is_manager) {
			frm.add_custom_button(__("Grade"), () => grade(frm)).addClass("btn-primary");
		}

		// The submitted file is private and attached to this record, so the standard
		// attachment/preview already gates it. This is just a one-click open.
		if (frm.doc.file) {
			frm.add_custom_button(__("Open submitted file"), () => window.open(frm.doc.file, "_blank"));
		}
	},
});

function grade(frm) {
	frappe.prompt(
		[
			{
				fieldname: "status",
				fieldtype: "Select",
				label: __("Verdict"),
				reqd: 1,
				default: "Passed",
				options: ["Passed", "Needs Rework", "Under Review"].join("\n"),
				description: __(
					"Passed accepts the work. Needs Rework sends it back — the learner can submit again. Under Review just claims it so a second grader knows it is being looked at."
				),
			},
			{
				fieldname: "grade",
				fieldtype: "Data",
				label: __("Grade (optional)"),
				description: __("A short mark if you use one — 'Pass', '8/10'."),
			},
			{
				fieldname: "feedback",
				fieldtype: "Text",
				label: __("Feedback"),
				description: __("Shown to the learner. Required for Needs Rework."),
			},
		],
		(values) => {
			frappe.call({
				method: "erpnext_enhancements.training.submissions.grade_submission",
				args: {
					submission: frm.doc.name,
					status: values.status,
					feedback: values.feedback,
					grade: values.grade,
				},
				freeze: true,
				freeze_message: __("Saving…"),
				callback(r) {
					const out = r.message || {};
					frappe.show_alert({
						message: __("Graded {0}.", [out.status || ""]),
						indicator: out.status === "Needs Rework" ? "orange" : "green",
					});
					frm.reload_doc();
				},
			});
		},
		__("Grade submission"),
		__("Save grade")
	);
}
