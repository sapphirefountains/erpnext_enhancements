// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// "Push to QuickBooks" button for intake-created Purchase Invoices / Payment
// Entries. Shown only on a submitted document that came from Accounting Document
// Intake and hasn't been pushed yet; once pushed it shows a synced indicator.
// The actual write-back + loop-guard live in quickbooks_online.core.writeback.
function qbo_writeback_button(frm) {
	if (frm.doc.docstatus !== 1) return;
	if (!frm.doc.custom_source_document_intake) return; // only intake-created docs

	if (frm.doc.custom_qbo_id) {
		frm.dashboard.add_indicator(__("Synced to QuickBooks · {0}", [frm.doc.custom_qbo_id]), "green");
		return;
	}

	frm.add_custom_button(
		__("Push to QuickBooks"),
		() => {
			frappe.confirm(__("Create this in QuickBooks Online and link it back?"), () => {
				frappe.call({
					method: "erpnext_enhancements.quickbooks_online.core.writeback.push_to_qbo",
					args: { doctype: frm.doc.doctype, name: frm.doc.name },
					freeze: true,
					freeze_message: __("Pushing to QuickBooks…"),
					callback: (r) => {
						if (!r.exc && r.message) {
							frappe.show_alert(
								{ message: __("Pushed to QuickBooks ({0} {1})", [r.message.qbo_entity, r.message.qbo_id]), indicator: "green" },
								7,
							);
							frm.reload_doc();
						}
					},
				});
			});
		},
		__("QuickBooks"),
	).addClass("btn-primary");
}

frappe.ui.form.on("Purchase Invoice", { refresh: qbo_writeback_button });
frappe.ui.form.on("Payment Entry", { refresh: qbo_writeback_button });
