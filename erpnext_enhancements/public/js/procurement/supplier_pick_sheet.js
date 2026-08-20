/**
 * Supplier form — Pick Sheet button.
 *
 * Targets: the Supplier form toolbar.
 * Loaded via: hooks.py `doctype_js["Supplier"]`, after
 *   `public/js/project_enhancements/pick_routing_map.js`, which owns the dialog,
 *   the optimiser and the printed sheet and exports the launcher on
 *   `erpnext_enhancements.pick_routing`.
 *
 * "We are going to Harrington anyway — what else is sitting there?" One button
 * that answers it for every open job at once, so a crew clears the counter in
 * one trip instead of one trip per project.
 *
 * A toolbar button rather than a Custom Field Button like the Project one. The
 * Project version needed a specific home (the Budget tab, beside the procurement
 * actions) and paid a migration for it. This one has no such anchor — Supplier's
 * layout is stock — and a custom field would buy nothing but a fixture, a patch
 * and a deletion procedure.
 */
frappe.ui.form.on('Supplier', {
	refresh: function (frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__('Pick Sheet'), () => {
			// Namespaced launcher, not a local copy: see that file's header on
			// why the two scopes must not fork. Read off `window` and guarded,
			// because hooks.py load order is the only thing that puts it there —
			// and a bare `erpnext_enhancements.…` would be a ReferenceError, not
			// a falsy value, if the bundle ever stopped defining the global.
			const api =
				(window.erpnext_enhancements && window.erpnext_enhancements.pick_routing) || null;
			if (!api || !api.openSupplierPickSheet) {
				frappe.msgprint(__('The pick sheet script did not load. Try a hard refresh.'));
				return;
			}
			api.openSupplierPickSheet([frm.doc.name]);
		});
	},
});
