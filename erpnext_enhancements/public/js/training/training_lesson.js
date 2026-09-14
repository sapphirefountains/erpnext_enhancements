// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Training Lesson form: see this one lesson as a learner sees it.
//
// Until v1.432.0 this doctype had no form script. An author editing a lesson row in
// the Desk — fixing a typo in a summary, changing the estimated minutes, ticking
// "required to finish" on a block — had no way to look at the result short of
// opening the whole canvas and navigating back to it.
//
// WHY THIS OPENS THE EXISTING PREVIEW RATHER THAN MOUNTING A PLAYER IN A DIALOG.
// The obvious version of this feature puts the player in a tab on the form. That
// would make a FOURTH host of TR.Player (portal → desk page → canvas preview →
// here), and — much worse — it would need the draft's learner payload as JSON,
// which no endpoint returns: `/training_preview` builds it server-side with
// `training_author._split_lesson` and renders it into the template. The only way to
// get it client-side is to rebuild it in JavaScript, which is precisely the ~640
// lines the classic builder carried (`preview_boot`, `preview_lesson`,
// `preview_outline`, `preview_transport`, `preview_checkpoint`) whose whole job was
// to re-derive a payload the server already derives correctly, and which the canvas
// port deliberately did not carry over.
//
// So this is one button that opens the one preview. What an author sees is the
// bytes publish would write, produced by the code that will write them.

frappe.ui.form.on("Training Lesson", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Preview as a learner"), () => open_preview(frm));
	},
});

function open_preview(frm) {
	// The lesson knows its course_version; the preview is addressed by COURSE,
	// because that is what resolves an open draft. One read rather than storing a
	// denormalised course on the lesson, which would be a second place for it to be
	// wrong.
	if (!frm.doc.course_version) {
		frappe.msgprint(__("This lesson is not attached to a course version yet."));
		return;
	}

	frappe.db
		.get_value("Training Course Version", frm.doc.course_version, "course")
		.then((reply) => {
			const course = reply && reply.message && reply.message.course;
			if (!course) {
				frappe.msgprint(__("Could not find the course this lesson belongs to."));
				return;
			}
			// Hand-built because it leaves the desk: the preview is a website route,
			// deliberately, so it runs the player in the same frappe-free conditions a
			// learner gets. frappe.set_route cannot address it.
			const url =
				"/training_preview?course=" +
				encodeURIComponent(course) +
				(frm.doc.lesson_key ? "&lesson=" + encodeURIComponent(frm.doc.lesson_key) : "");
			window.open(url, "_blank");
		});
}
