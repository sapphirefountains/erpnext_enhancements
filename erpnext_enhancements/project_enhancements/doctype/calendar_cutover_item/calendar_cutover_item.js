// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Calendar Cutover Item (C5): approve, ignore or send back one review row. The status field is
// read-only; these buttons are the only way it moves (api/calendar_cutover.set_status, System Manager
// only). Unsaved edits to the proposal are saved first, so what is approved is what is on screen.

const CCI_API = "erpnext_enhancements.api.calendar_cutover.";
const CCI_GOOGLE = ["https://www.google.com/calendar/", "https://calendar.google.com/"];

frappe.ui.form.on("Calendar Cutover Item", {
	refresh(frm) {
		if (frm.is_new()) return;
		const status = frm.doc.status;
		if (status !== "Applied") {
			if (status !== "Approved") frm.add_custom_button(__("Approve"), () => cci_set_status(frm, "Approved"));
			if (status !== "Ignored") frm.add_custom_button(__("Ignore"), () => cci_set_status(frm, "Ignored"));
			if (status !== "Pending") frm.add_custom_button(__("Back to Pending"), () => cci_set_status(frm, "Pending"));
		}
		const link = frm.doc.html_link || "";
		if (CCI_GOOGLE.some((prefix) => link.startsWith(prefix))) {
			frm.add_custom_button(__("Open in Google Calendar"), () => window.open(link, "_blank", "noopener"));
		}
		frm.add_custom_button(__("All cutover items"), () => frappe.set_route("List", "Calendar Cutover Item"));
	},
});

function cci_set_status(frm, status) {
	const send = () =>
		frappe
			.call({ method: CCI_API + "set_status", type: "POST", args: { names: JSON.stringify([frm.doc.name]), status } })
			.then((r) => {
				const skipped = (r.message && r.message.skipped) || [];
				if (skipped.length) frappe.msgprint(skipped.map((s) => frappe.utils.escape_html(s[1])).join("<br>"));
				return frm.reload_doc();
			});
	return frm.is_dirty() ? frm.save().then(send) : send();
}
