// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Training Assignment form: the door, on the record that says you owe a course.
//
// Until v1.429.1 this doctype had no form script at all, which meant a learner
// could open the row telling them they owe "Using the Training Module" by
// 2026-09-26 and there was nothing on it that would take them there. 20 of 26
// assignments sat at "Not Started"; the only link into the player lived inside an
// email.
//
// The button routes rather than linking. `frappe.set_route("learn", course)` is an
// in-desk navigation; an <a href="/app/learn/..."> would not be intercepted by the
// router and would cost a full page reload plus a redirect hop, because /app is a
// website_redirect to /desk in v16.

frappe.ui.form.on("Training Assignment", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.course) return;

		// Closed assignments keep the button, deliberately. A completed course is
		// still readable -- revision before a re-cert, or checking what was actually
		// signed off -- and the server decides what it will serve; the label is what
		// changes, so the button never promises more than it does.
		const closed = ["Completed", "Waived", "Cancelled"].indexOf(frm.doc.status) !== -1;
		const label = closed
			? __("Review this course")
			: frm.doc.status === "In Progress"
			? __("Continue this course")
			: __("Start this course");

		frm.page.set_primary_action(label, () => {
			frappe.set_route("learn", frm.doc.course);
		});
	},
});
