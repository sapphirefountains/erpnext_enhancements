frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		frm.set_query("custom_serial_no", "items", function () {
			return {
				filters: {
					item_code: "Customer Water Feature",
				},
			};
		});
	},
});
