// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

// Asset Out of Service: rentable fountains only, and a Return to Service button while open.
frappe.ui.form.on("Asset Out of Service", {
	setup(frm) {
		frm.set_query("asset", () => ({
			query: "erpnext_enhancements.asset_management.rental_availability.rentable_asset_query",
		}));
	},
	refresh(frm) {
		if (frm.is_new() || frm.doc.status !== "Out of Service") {
			return;
		}
		frm.add_custom_button(__("Return to Service"), () => {
			frappe.confirm(__("Is {0} fixed and ready to rent?", [frm.doc.asset_name || frm.doc.asset]), () =>
				frm.call("return_to_service").then(() => frm.reload_doc())
			);
		}).addClass("btn-primary");
	},
});
