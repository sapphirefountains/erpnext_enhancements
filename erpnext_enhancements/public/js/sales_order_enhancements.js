/**
 * Sales Order form script.
 *
 * Targets: the "Sales Order" doctype form.
 * Loaded via: hooks.py `doctype_js["Sales Order"]` (with vue.global.js +
 *   comments.js; the Comments App is auto-mounted for Sales Order by
 *   comments_auto.js).
 *
 * - Restricts the items child-table `custom_serial_no` link query to serial
 *   numbers of the configured water-feature Item (ERPNext Enhancements
 *   Settings > Water Feature Item).
 * - On submitted orders carrying water-feature items, adds
 *   "Create > Maintenance Contract" which maps the order into a draft
 *   Sapphire Maintenance Contract.
 */
frappe.ui.form.on("Sales Order", {
	setup: function (frm) {
		frappe.db
			.get_single_value("ERPNext Enhancements Settings", "water_feature_item")
			.then((item) => {
				frm._ee_water_feature_item = item;
			});
	},

	refresh: function (frm) {
		frm.set_query("custom_serial_no", "items", function () {
			const filters = {};
			if (frm._ee_water_feature_item) {
				filters.item_code = frm._ee_water_feature_item;
			}
			return { filters: filters };
		});

		const has_water_feature = (frm.doc.items || []).some((row) => row.custom_serial_no);
		if (frm.doc.docstatus === 1 && has_water_feature) {
			frm.add_custom_button(
				__("Maintenance Contract"),
				() => {
					frappe.model.open_mapped_doc({
						method:
							"erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract.make_contract_from_sales_order",
						frm: frm,
					});
				},
				__("Create")
			);
		}
	},
});
