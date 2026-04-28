frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		frm.set_query("custom_asset", "items", function () {
			return {
				filters: {
					asset_category: "SF Water Feature",
				},
			};
		});
	},
});
