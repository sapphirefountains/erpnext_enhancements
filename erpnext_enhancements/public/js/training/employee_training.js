// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// Employee form: how is this person doing on their training?
//
// The question a manager arrives with is about a PERSON, and until now every
// training surface answered about a course, a cohort or the whole org. Training
// Insights could tell you seven assignments were overdue and open the list; it
// could not tell you whether the technician whose form you already have open is
// current on anything.
//
// The button is drawn only for the manager set, and only when the Employee has a
// `user_id` — training records belong to a User, and an Employee with no linked
// account has none by construction. An entry point that opens an empty dialog is
// worse than no entry point, because it reads as the data being missing.
//
// The dialog carries NO scores and no attempt history, and not because this file
// filters them: `get_person_dashboard` never builds them. See that endpoint.

const EMP_TRAINING_MANAGERS = ["System Manager", "Training Manager", "HR Manager"];

frappe.ui.form.on("Employee", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (!frm.doc.user_id) return;
		const mine = frappe.user_roles || [];
		if (!EMP_TRAINING_MANAGERS.some((role) => mine.includes(role))) return;

		frm.add_custom_button(
			__("Training record"),
			() => {
				if (window.TR && typeof TR.openPersonRecord === "function") {
					TR.openPersonRecord(frm.doc.user_id);
				}
			},
			__("View")
		);
	},
});
