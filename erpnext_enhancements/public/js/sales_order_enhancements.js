/**
 * Sales Order form script.
 *
 * Targets: the "Sales Order" doctype form.
 * Loaded via: hooks.py `doctype_js["Sales Order"]` (with vue.global.js +
 *   comments.js; the Comments App is auto-mounted for Sales Order by
 *   comments_auto.js).
 *
 * Restricts the items child-table `custom_serial_no` link query to serial numbers
 * of the "Customer Water Feature" item.
 */
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
