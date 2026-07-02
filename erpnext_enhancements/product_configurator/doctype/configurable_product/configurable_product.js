// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

frappe.ui.form.on("Configurable Product", {
	refresh(frm) {
		if (frm.is_new()) return;

		if (frappe.boot.ee_product_configurator) {
			frm.add_custom_button(__("Create Component Items"), () => {
				frappe.call({
					method:
						"erpnext_enhancements.product_configurator.api.configurator.ensure_component_items",
					args: { product: frm.doc.name },
					freeze: true,
					freeze_message: __("Creating Suppliers and component Items…"),
					callback(r) {
						if (!r.message) return;
						const m = r.message;
						frappe.msgprint(
							__(
								"Items created: {0}, reused: {1}. Suppliers created: {2}.",
								[
									(m.items_created || []).length,
									(m.items_reused || []).length,
									(m.suppliers_created || []).length,
								]
							)
						);
					},
				});
			});
		} else {
			frm.set_intro(
				__(
					"The Product Configurator's ERPNext generation is switched off " +
						"(ERPNext Enhancements Settings → Product Configurator)."
				),
				"orange"
			);
		}

		frm.add_custom_button(__("New Configuration"), () => {
			frappe.new_doc("Product Configuration", { product: frm.doc.name });
		});
	},
});
