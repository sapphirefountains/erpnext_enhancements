// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.ui.form.on("Project Scope of Work", {
	refresh(frm) {
		// Say out loud what submitting means. "Submit" is the framework's word; "lock" is the
		// one the business uses, and the consequence -- scope moves only by Change Order after
		// this -- is not something a generic Submit button communicates.
		if (frm.doc.docstatus === 0 && !frm.is_new()) {
			frm.page.set_primary_action(__("Lock Scope"), () => frm.savesubmit());
		}
		if (frm.doc.docstatus === 1) {
			frm.dashboard.set_headline(
				__("Locked on {0}. Scope changes from here go through a Change Order.", [
					frappe.datetime.str_to_user(frm.doc.locked_on),
				])
			);
		}
	},
});
