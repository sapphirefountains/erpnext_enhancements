// "Find suppliers" on the Item and Purchase Order forms (v1.726.0): opens the Find a Supplier page
// (/desk/supplier-finder) on an Item, so the buyer sees everyone who has sold it to us, ranked by
// what we bought, and other suppliers in the same line. On a Purchase Order with several items it
// asks which one first. Shown only to someone who may read Supplier; the page and its endpoint
// check again on the server.

(() => {
	// doctype_js loads this file once for Item and again for Purchase Order. Registered twice, the
	// handlers would run twice and add the button twice.
	if (window.__sfn_buttons_registered) return;
	window.__sfn_buttons_registered = true;

	function open_finder(item_code) {
		if (item_code) frappe.set_route("supplier-finder", { item: item_code });
	}

	frappe.ui.form.on("Item", {
		refresh(frm) {
			if (frm.is_new() || !frappe.model.can_read("Supplier")) return;
			frm.add_custom_button(__("Find suppliers"), () => open_finder(frm.doc.name));
		},
	});

	frappe.ui.form.on("Purchase Order", {
		refresh(frm) {
			if (!frappe.model.can_read("Supplier")) return;
			frm.add_custom_button(__("Find suppliers"), () => {
				const codes = [...new Set((frm.doc.items || []).map((r) => r.item_code).filter(Boolean))];
				if (!codes.length) {
					frappe.set_route("supplier-finder");
					return;
				}
				if (codes.length === 1) {
					open_finder(codes[0]);
					return;
				}
				const names = {};
				(frm.doc.items || []).forEach((r) => {
					if (r.item_code && !names[r.item_code]) names[r.item_code] = r.item_name || r.item_code;
				});
				frappe.prompt(
					{
						fieldname: "item_code",
						fieldtype: "Select",
						label: __("Which item?"),
						options: codes.map((c) => ({ value: c, label: `${names[c]} (${c})` })),
						default: codes[0],
						reqd: 1,
					},
					(v) => open_finder(v.item_code),
					__("Find suppliers"),
					__("Search")
				);
			});
		},
	});
})();
