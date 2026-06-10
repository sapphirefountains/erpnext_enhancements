/**
 * Sapphire Maintenance Contract form script.
 *
 * - Restricts covered-feature Serial Nos to the configured water-feature Item
 *   (ERPNext Enhancements Settings > Water Feature Item) and warehouses to
 *   non-group leaves.
 * - List/status indicator colors.
 */
frappe.ui.form.on("Sapphire Maintenance Contract", {
	setup(frm) {
		frm.set_query("serial_no", "covered_features", function () {
			const filters = {};
			if (frm._ee_water_feature_item) {
				filters.item_code = frm._ee_water_feature_item;
			}
			return { filters: filters };
		});
		frm.set_query("default_warehouse", "covered_features", function () {
			return { filters: { is_group: 0, disabled: 0 } };
		});
		frappe.db
			.get_single_value("ERPNext Enhancements Settings", "water_feature_item")
			.then((item) => {
				frm._ee_water_feature_item = item;
			});
	},

	refresh(frm) {
		if (frm.doc.status === "Active") {
			frm.page.set_indicator(__("Active"), "green");
		} else if (frm.doc.status === "Expired" || frm.doc.status === "Cancelled") {
			frm.page.set_indicator(__(frm.doc.status), "red");
		}
	},
});
