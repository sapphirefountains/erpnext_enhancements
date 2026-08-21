/**
 * Supplier list-view enhancements.
 *
 * Targets: the Supplier DocType list view.
 * Loaded via: hooks.py `doctype_list_js["Supplier"]`.
 *
 * Adds an "On Hold" red indicator, and supports the custom "additional supplier
 * groups" model: the `custom_additional_supplier_groups_list` column is only
 * shown when a supplier-group filter is active, and an overridden `get_args`
 * rewrites any `supplier_group =` filter into a `LIKE` against the searchable
 * `custom_supplier_groups_search` field so suppliers belonging to a group via the
 * additional-groups list also match.
 *
 * Also carries the **Pick Sheet** bulk action (v1.338.0): tick the suppliers a
 * crew is driving to and get one run covering every open job at those counters.
 * The action lives here rather than in `procurement/supplier_pick_sheet.js`
 * because `listview_settings.onload` is a single slot — two files assigning it
 * would silently clobber each other, and the `get_args` override below is the
 * one that must not be lost.
 */
// Extend standard listview settings for Supplier
frappe.listview_settings['Supplier'] = frappe.listview_settings['Supplier'] || {};

// Preserve existing indicators if any
const original_sg_indicator_v6 = frappe.listview_settings['Supplier'].get_indicator;

$.extend(frappe.listview_settings['Supplier'], {
	add_fields: ["supplier_name", "supplier_group", "image", "on_hold", "custom_additional_supplier_groups_list", "custom_supplier_groups_search"],
	
	get_indicator: function (doc) {
		if (original_sg_indicator_v6) {
			const indicator = original_sg_indicator_v6(doc);
			if (indicator) return indicator;
		}
		
		if (cint(doc.on_hold)) {
			return [__("On Hold"), "red"];
		}
	},

	formatters: {
		custom_additional_supplier_groups_list: function(value, df, doc) {
			if (!value) return value;
			
			// Use standard font color and normal weight
			return `<span>${value}</span>`;
		}
	},
	
	refresh: function(listview) {
		// Toggle visibility of the "Additional Groups" column based on filters
		const filters = listview.filter_area.get_filters();
		const has_sg_filter = filters.some(f => f[1] === 'supplier_group' || f[1] === 'custom_supplier_groups_search');
		
		if (listview.toggle_column) {
			listview.toggle_column('custom_additional_supplier_groups_list', has_sg_filter);
		} else {
			const field = 'custom_additional_supplier_groups_list';
			const $header = listview.$wrapper.find(`.list-row-head [data-fieldname="${field}"]`);
			const $cells = listview.$wrapper.find(`.list-row-col [data-fieldname="${field}"]`);
			
			if (has_sg_filter) {
				$header.show();
				$cells.show();
			} else {
				$header.hide();
				$cells.hide();
			}
		}
	},
	
	onload: function(listview) {
		// Appears in the Actions menu once rows are ticked. The dialog, the
		// optimiser and the printed sheet all come from
		// `project_enhancements/pick_routing_map.js`, which hooks.py loads ahead
		// of this file for exactly this reason — see its header.
		listview.page.add_actions_menu_item(__('Pick Sheet'), () => {
			const api =
				(window.erpnext_enhancements && window.erpnext_enhancements.pick_routing) || null;
			if (!api || !api.openSupplierPickSheet) {
				frappe.msgprint(__('The pick sheet script did not load. Try a hard refresh.'));
				return;
			}
			api.openSupplierPickSheet(listview.get_checked_items(true));
		}, false);

		if (!listview._original_get_args_v6) {
			listview._original_get_args_v6 = listview.get_args;
			
			listview.get_args = function() {
				const args = listview._original_get_args_v6.apply(this, arguments);
				
				if (args && args.filters && Array.isArray(args.filters)) {
					args.filters.forEach(filter => {
						// filter = [doctype, fieldname, operator, value]. Only rewrite a
						// supplier_group filter onto the denormalized multi-group field, and only
						// for the operators that translate — the previous code rewrote EVERY
						// operator to `LIKE %value%`, which silently inverted `!=`, turned
						// `in [A,B]` into the useless pattern `%A,B%`, and made `is set` search for
						// the literal "set". custom_supplier_groups_search stores the groups
						// comma-PADDED (", A, B, ") for whole-token matching, so `=` is a padded
						// LIKE, not a bare substring (else "Steel" matches "Stainless Steel").
						if (filter[1] !== 'supplier_group' || !filter[3]) {
							return;
						}
						const operator = filter[2];
						const value = filter[3];
						if (operator === '=') {
							filter[1] = 'custom_supplier_groups_search';
							filter[2] = 'like';
							filter[3] = `%, ${value}, %`;
						} else if (operator === '!=') {
							filter[1] = 'custom_supplier_groups_search';
							filter[2] = 'not like';
							filter[3] = `%, ${value}, %`;
						} else if (operator === 'like') {
							// The user asked for a substring explicitly; keep it bare.
							filter[1] = 'custom_supplier_groups_search';
							filter[3] = `%${value}%`;
						}
						// in / not in / is (set|not set) stay on the real supplier_group Link
						// field: their array/keyword semantics do not map to a single LIKE.
					});
				}
				return args;
			};
		}
	}
});
