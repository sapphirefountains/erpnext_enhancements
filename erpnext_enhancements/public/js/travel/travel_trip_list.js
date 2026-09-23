/**
 * Travel Trip list view.
 *
 * Targets: the Travel Trip list.
 * Loaded via: hooks.py `doctype_list_js["Travel Trip"]`.
 *
 * The list's "+ Add Travel Trip" button (and Ctrl+B) open the Plan a Trip page
 * instead of a blank form: the page walks through the trip one step at a time
 * and ends on the checklist of what is missing. The form itself is unchanged —
 * every trip still opens in it, and the page links back to it.
 *
 * `primary_action` is frappe's own list-settings hook (list_view.js
 * set_primary_action), so the button keeps its label, icon and permission check.
 */
frappe.listview_settings['Travel Trip'] = Object.assign(frappe.listview_settings['Travel Trip'] || {}, {
	primary_action() {
		frappe.set_route('plan-a-trip', { new: 1 });
	},
});
