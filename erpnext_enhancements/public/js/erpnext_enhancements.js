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

	// 2. Iterate cur_frm fields
	if (window.cur_frm && window.cur_frm.fields_dict) {
		const fieldname = $el.closest('[data-fieldname]').attr('data-fieldname');
		if (fieldname && cur_frm.fields_dict[fieldname]) {
			return cur_frm.fields_dict[fieldname];
		}
	}

	// 3. Grid Row fields
	const grid_row = $el.closest('.grid-row');
	if (grid_row.length) {
		const grid_row_obj = grid_row.data('grid_row');
		const fieldname = $el.closest('[data-fieldname]').attr('data-fieldname');
		if (grid_row_obj && grid_row_obj.docfields) {
			const df = grid_row_obj.docfields.find(d => d.fieldname === fieldname);
			if (df) return { df: df }; // Return an object mimicking the control with just df
		}
	}

	return null;
}

document.addEventListener('click', function(e) {
	// 1. Identify if the click target is an input field
	const target = e.target;
	if (target.tagName !== 'INPUT') return;

	// 2. Check if this input belongs to a Frappe Control
	const controlElement = target.closest('.frappe-control');
	if (!controlElement) return;

	// 3. Determine if it is a Link-like field
	let isLink = false;
	const fieldtype = controlElement.getAttribute('data-fieldtype');

	if (fieldtype && (fieldtype === 'Link' || fieldtype === 'Dynamic Link')) {
		isLink = true;
	} else {
		// Double check via control object if possible
		const c = get_field_control(target);
		if (c && c.df && (c.df.fieldtype === 'Link' || c.df.fieldtype === 'Dynamic Link')) {
			isLink = true;
		}
	}

	if (!isLink) return;

	// 4. Check editability and state
	if (target.readOnly || target.disabled) {
		// If it's read-only/disabled, default behavior might be fine, or handled by Frappe
		return;
	}

	// 5. Ensure the field has a value
	if (!target.value) {
		return;
	}

	// 6. Find the "Open Link" button
	// Frappe standard renders a button with class 'btn-open' or similar logic inside the control-input-wrapper.
	const $control = $(controlElement);
	let $linkBtn = $control.find('.btn-open');

	// Fallback search if .btn-open isn't used (varies by version/theme)
	if ($linkBtn.length === 0) {
		$linkBtn = $control.find('[data-action="open-link"]');
	}

	// Another fallback: Look for the element containing the 'arrow-right' icon
	// Note: 'use[*|href*="arrow-right"]' selector handles namespaced href (xlink:href)
	if ($linkBtn.length === 0) {
		$linkBtn = $control.find('.icon-sm use[*|href*="arrow-right"]').closest('a, button');
	}

    // Fallback for Dynamic Link where button might be obscure or different
    // In some versions, the button is hidden until hover, but we can still click it.

	// 7. Execute Navigation
	if ($linkBtn.length > 0) {
		// Prevent default focus/edit behavior
		e.preventDefault();
		e.stopPropagation();
		e.stopImmediatePropagation();

		// Trigger the navigation
		$linkBtn[0].click();

		// Blur the input to prevent focus state artifacts
		target.blur();
	} else {
		// If we cannot find the button, we attempt to route manually ONLY if we are certain about the doctype.
		const control = get_field_control(target);
		let doctype = null;

		if (control && control.df && control.df.options && control.df.fieldtype === 'Link') {
			doctype = control.df.options;
		}
        // Note: Dynamic Link requires looking at another field for doctype,
        // which makes manual routing risky without the button logic.
        // We will skip manual routing for Dynamic Link if button is missing.

		if (doctype && target.value) {
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();

			frappe.set_route('Form', doctype, target.value);
			target.blur();
		}
	}

}, true); // Capture phase is crucial
