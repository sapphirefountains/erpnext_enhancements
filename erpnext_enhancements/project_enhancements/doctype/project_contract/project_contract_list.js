/**
 * @file Project Contract list view.
 * @description "Signed copy" on every Signed row (v1.744.0): the same button
 * the form carries, so the agreement a customer signed is one click from the
 * list. The list has no __onload, so it asks the server on click
 * (signed_copy.get_signed_copy); the shared opener lives in contract_viewer.js,
 * which hooks.py `doctype_list_js` loads on this list.
 */

frappe.listview_settings["Project Contract"] = {
	add_fields: ["status", "template_key"],

	button: {
		show(doc) {
			return doc.status === "Signed";
		},
		get_label() {
			return __("Signed copy");
		},
		get_description(doc) {
			return __("Open the signed copy of {0}", [doc.name]);
		},
		action(doc) {
			const viewer = erpnext_enhancements.contract_viewer;
			viewer
				.fetch_signed_copy(doc.name)
				.then((info) => viewer.open_signed_copy(info, { template_key: doc.template_key }));
		},
	},
};
