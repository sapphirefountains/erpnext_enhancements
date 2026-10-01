// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// The form is the moderator's: participants and status. The review itself runs at /review.

frappe.ui.form.on("Design Review", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Open the review"), () => window.open(`/review/${encodeURIComponent(frm.doc.name)}`, "_blank"));
	},
});
