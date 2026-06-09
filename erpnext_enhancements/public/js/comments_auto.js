// Auto-mounts the custom "Comments App" on every doctype that exposes a
// `custom_comments_field` HTML field.
//
// Targets: every doctype listed in COMMENT_APP_DOCTYPES (below).
// Loaded via: hooks.py `app_include_js` (global), after vue.global.js +
//   comments.js so erpnext_enhancements.render_comments_app is defined.
//
// This replaces ~two dozen near-identical per-doctype `*_comments.js` files,
// each of which did nothing but call render_comments_app on refresh. To give a
// new doctype the comments tab, add its name to the list below — no new file
// and no hooks.py doctype_js entry required.
//
// NOTE: doctypes whose own form script already calls render_comments_app
// (Project, Customer, Employee, Account, Timesheet, Contact) are deliberately
// omitted here so the app is not mounted twice.
frappe.provide("erpnext_enhancements");

erpnext_enhancements.COMMENT_APP_DOCTYPES = [
	"Item",
	"Task",
	"Sales Order",
	"Sales Invoice",
	"Journal Entry",
	"Payment Entry",
	"Purchase Invoice",
	"Production Plan",
	"Work Order",
	"Job Card",
	"Stock Entry",
	"Purchase Order",
	"Material Request",
	"Purchase Receipt",
	"Delivery Note",
	"Serial No",
	"Batch",
	"Supplier",
	"Supplier Quotation",
	"Quotation",
	"Lead",
	"Address",
	"Prospect",
];

erpnext_enhancements.COMMENT_APP_DOCTYPES.forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			// Match the previous per-doctype behaviour: only on saved documents.
			if (frm.is_new()) return;
			if (erpnext_enhancements && erpnext_enhancements.render_comments_app) {
				erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
			} else {
				console.error("erpnext_enhancements.render_comments_app is not defined.");
			}
		},
	});
});
