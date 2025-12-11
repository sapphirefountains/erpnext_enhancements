frappe.provide('frappe.search');

$(document).on('app_ready', function() {
	if (frappe.search.AwesomeBar) {
		const original_make_global_search = frappe.search.AwesomeBar.prototype.make_global_search;
		frappe.search.AwesomeBar.prototype.make_global_search = function(txt) {
			// Call the original method to populate options
			original_make_global_search.call(this, txt);

			// Find the "Search for X" option and boost its index
			// The original implementation adds an option with default property "Search"
			if (this.options && this.options.length > 0) {
				const searchItem = this.options.find(opt => opt.default === "Search");
				if (searchItem) {
					// Set a very high index to ensure it is always at the top
					// Standard indices are around 10-100.
					searchItem.index = 100000;
				}
			}
		};
	}
});

// Global Event Listener for Link Field Navigation
// Strategy: "Proxy Click"
// If the user clicks the text input of a link field, we find the associated "Open Link" button (->)
// and click it programmatically. This ensures we support Link, Dynamic Link, and any other
// field type that Frappe renders with a navigation button, preserving the correct routing logic.

// Helper to find the control associated with an input element
function get_field_control(element) {
	// Try to find the control instance
	// 1. Check direct data attachment
	let $el = $(element);
	let field = $el.data('control'); // Often attached by custom scripts or framework
	if (field) return field;

	const fieldname = $el.closest('[data-fieldname]').attr('data-fieldname');

	// 2. Iterate cur_frm fields
	if (fieldname && window.cur_frm && window.cur_frm.fields_dict) {
		if (cur_frm.fields_dict[fieldname]) {
			return cur_frm.fields_dict[fieldname];
		}
	}

	// 3. Iterate cur_dialog fields (for modals)
	if (fieldname && window.cur_dialog && window.cur_dialog.fields_dict) {
		if (cur_dialog.fields_dict[fieldname]) {
			return cur_dialog.fields_dict[fieldname];
		}
	}

	// 4. Grid Row fields
	const grid_row = $el.closest('.grid-row');
	if (grid_row.length) {
		const grid_row_obj = grid_row.data('grid_row');
		if (grid_row_obj && grid_row_obj.docfields && fieldname) {
			// Logic to get the grid column definition
			const df = grid_row_obj.docfields.find(d => d.fieldname === fieldname);
			// For grids, we don't have a full control object for each cell usually,
			// but we might get the value from the doc.
			if (df) {
				return {
					df: df,
					get_value: () => {
						// Return value from the row doc
						return grid_row_obj.doc[fieldname];
					}
				};
			}
		}
	}

	return null;
}

document.addEventListener('click', function(e) {
	// 1. Basic Checks
	if (e.button !== 0) return; // Only allow Left Click
	const target = e.target;
	if (target.tagName !== 'INPUT') return;

	// 2. Check if this input belongs to a Frappe Control
	const controlElement = target.closest('.frappe-control');
	if (!controlElement) return;

	// 3. Determine if it is a Link-like field
	let isLink = false;
	const fieldtype = controlElement.getAttribute('data-fieldtype');

	// Primary check via attribute
	if (fieldtype && (fieldtype === 'Link' || fieldtype === 'Dynamic Link')) {
		isLink = true;
	} else {
		// Secondary check via control object
		const c = get_field_control(target);
		if (c && c.df && (c.df.fieldtype === 'Link' || c.df.fieldtype === 'Dynamic Link')) {
			isLink = true;
		}
	}

	if (!isLink) return;

	// 4. Check editability and state
	if (target.readOnly || target.disabled) {
		return;
	}

	// 5. Ensure the field has a value
	if (!target.value) {
		return;
	}

	// 6. Navigation Logic
	const $control = $(controlElement);
	let $linkBtn = $control.find('.btn-open');

	// Fallback selectors for the link button
	if ($linkBtn.length === 0) $linkBtn = $control.find('[data-action="open-link"]');
	if ($linkBtn.length === 0) $linkBtn = $control.find('.link-btn'); // Common wrapper in some versions
	if ($linkBtn.length === 0) $linkBtn = $control.find('a.btn-open'); // Specific tag check

	// If we found the button, we can just click it (Proxy Click)
	if ($linkBtn.length > 0) {
		// If user pressed Ctrl/Meta, we might want to open in new tab.
		// Most .btn-open handlers use frappe.set_route which stays in same tab.
		// If we want New Tab support, we have to construct the URL manually.
		if (e.ctrlKey || e.metaKey) {
			// Proceed to manual URL construction if possible, otherwise let it behave normally (often does nothing for div/button)
			// We will fall through to Manual Routing logic if we can resolve the doc.
		} else {
			e.preventDefault();
			e.stopPropagation();
			e.stopImmediatePropagation();
			$linkBtn[0].click();
			target.blur();
			return;
		}
	}

	// 7. Manual Routing (Fallback or New Tab)
	// If button missing OR New Tab requested
	const control = get_field_control(target);
	let doctype = null;
	let docname = null;

	if (control && control.df) {
		// Resolve DocType
		if (control.df.fieldtype === 'Link') {
			doctype = control.df.options;
		} else if (control.df.fieldtype === 'Dynamic Link') {
			// Dynamic Link requires resolving the 'options' field which points to another fieldname
			// that holds the actual DocType.
			if (control.df.options && window.cur_frm) {
				doctype = cur_frm.doc[control.df.options];
			} else if (control.df.options && control.get_model_value) {
				// Try to find it via model value if available
				// This is tricky for grids without full context
			}
		}

		// Resolve DocName (Value)
		// Use get_value() if available to get the ID, not the Title
		if (control.get_value) {
			docname = control.get_value();
		} else {
			// Fallback to target.value, but this risks being the Title
			docname = target.value;
		}
	}

	if (doctype && docname) {
		e.preventDefault();
		e.stopPropagation();
		e.stopImmediatePropagation();

		const url = frappe.utils.get_form_link(doctype, docname);

		if (e.ctrlKey || e.metaKey) {
			window.open(url, '_blank');
		} else {
			frappe.set_route('Form', doctype, docname);
		}
		target.blur();
	}

}, true); // Capture phase
