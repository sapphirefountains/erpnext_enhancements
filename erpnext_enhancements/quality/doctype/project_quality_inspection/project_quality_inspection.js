// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Desk form for Project Quality Inspection.
 *
 * "Open in Inspection Wizard" (custom pages S5b, v1.667.0) opens this inspection in the field
 * wizard at its first section: /desk/inspection-wizard/<inspection>/0, the path form the wizard
 * reads from its route (quality/page/inspection_wizard). frappe.set_route makes that a history
 * entry of its own, so Back returns to this form. route_options is cleared first, as the wizard's
 * own route_to does: the router copies every address's query into it and nothing clears it, and
 * on v16.50 set_route writes whatever is left there into the new address.
 *
 * Only on a saved draft: the wizard fills in a draft and shows a submitted one as a banner with
 * nothing to step through. Only for those whose roles open the page: frappe.boot.page_info lists
 * the Pages a person's roles reach, and anyone else would only be refused by it.
 *
 * Frappe loads this file for this DocType on its own. It must never also be listed in hooks.py
 * doctype_js, which appends to the same script and would load it twice.
 */

frappe.ui.form.on("Project Quality Inspection", {
	refresh(frm) {
		const pages = (frappe.boot && frappe.boot.page_info) || {};
		if (frm.is_new() || frm.doc.docstatus !== 0 || !pages["inspection-wizard"]) return;
		frm.add_custom_button(__("Open in Inspection Wizard"), () => {
			frappe.route_options = null;
			frappe.set_route("inspection-wizard", frm.doc.name, "0");
		});
	},
});
