/*
 * ER-2026-520743, the Desk half: "Create a new Item" from a buying grid row brings the row along.
 *
 * Frappe's Link control asks its docfield for `get_route_options_for_new_doc(control)` just before
 * it opens Quick Entry (controls/link.js `new_doc()`), and for a grid it asks the form's per-document
 * copy of the child docfield (`frm.get_docfield("items", "item_code")`). That is the framework's own
 * extension point; ERPNext uses it the same way for Quality Inspection and Batch. This file sets it
 * on the `items` grid of each document in BUYING_DOCTYPES, and the values come from the pure
 * `new_item_values.js` beside it (what each key is for, and when a row gives nothing, is there).
 *
 * Things this file is careful about:
 *
 * - The copy is per document. `frappe.meta.get_docfield(doctype, fieldname, docname)` keeps one copy
 *   per docname, and a saved new document gets a new name, so the hook is set again on every
 *   `onload` and `refresh`. Setting it twice is harmless: a hook marked as ours is left alone.
 * - It never replaces someone else's hook. A hook already on the field (ERPNext's, a future one)
 *   is called first, and its values win wherever both name the same key.
 * - It fails open. If anything here throws, the Link control gets the other hook's values (or
 *   none) and "Create a new Item" works exactly as it did before this file.
 */

import { BUYING_DOCTYPES, new_item_values } from "./new_item_values.js";

const MARK = "__ee_new_item_prefill";

function install(frm) {
	const grid = frm && frm.fields_dict && frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) return;
	const df = frm.get_docfield("items", "item_code");
	if (!df) return;
	const previous = df.get_route_options_for_new_doc;
	if (previous && previous[MARK]) return;

	const hook = function (control) {
		const theirs = (previous && previous.call(this, control)) || {};
		let ours = {};
		try {
			const typed = control && control.get_label_value ? control.get_label_value() : "";
			ours = new_item_values(control && control.doc, frm.doc, typed);
		} catch (e) {
			console.warn("new item prefill skipped", e);
		}
		return Object.assign(ours, theirs);
	};
	hook[MARK] = true;
	df.get_route_options_for_new_doc = hook;
}

BUYING_DOCTYPES.forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		onload: install,
		refresh: install,
	});
});
