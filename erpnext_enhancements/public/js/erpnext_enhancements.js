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
// This replaces the monkey-patching approach to be more robust across all Link fields
// and ensure compatibility with dynamic loading and different contexts (Form, Grid, etc.)

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

	// 3. Filter for Link-like fields
    // We check for "Link" or "Dynamic Link" in the fieldtype.
    // Using check for "Link" at the end of the string covers both "Link" and "Dynamic Link".
    const fieldtype = controlElement.getAttribute('data-fieldtype');
    if (!fieldtype || !fieldtype.endsWith('Link')) {
        return;
    }

	// 4. Check editability
    // If it's read-only, Frappe handles it (and the input might not be the click target anyway).
    // If disabled, do nothing.
	// 2. Quick check: Must be inside a frappe-control or have input-with-feedback class
    // and ideally should be a Link field type.
    const controlElement = target.closest('.frappe-control');
    // If we can't determine it's a link field from DOM, we might skip, but let's check deeper.

    let isLink = false;
    if (controlElement && controlElement.getAttribute('data-fieldtype') === 'Link') {
        isLink = true;
    }

    if (!isLink) {
        // Double check via control object if possible
        const c = get_field_control(target);
        if (c && c.df && c.df.fieldtype === 'Link') {
            isLink = true;
        }
    }

    if (!isLink) return;

	// 3. Check editability
    // If it's read-only, we let default behavior happen
	if (target.readOnly || target.disabled) {
		return;
	}

    // 5. Ensure the field has a value
    if (!target.value) {
        return;
    }

    // 6. Find the "Open Link" button
    // Frappe standard renders a button with class 'btn-open' or similar logic inside the control-input-wrapper.
    // Structure: .control-input-wrapper -> .link-btn (which is the button)
    // Or sometimes it's an 'a' tag.

    // We look for the specific button that Frappe uses to navigate.
    // In V13/V14/V15, it's often an element with class `btn-open` or `link-btn`
    // or an anchor with data-action.

    const $control = $(controlElement);
    let $linkBtn = $control.find('.btn-open');

    // Fallback search if .btn-open isn't used (varies by version/theme)
    if ($linkBtn.length === 0) {
        $linkBtn = $control.find('[data-action="open-link"]');
    }

    // Another fallback: Look for the element containing the 'arrow-right' icon
    if ($linkBtn.length === 0) {
        // This is looser but might catch it if classes changed
        $linkBtn = $control.find('.icon-sm use[*|href*="arrow-right"]').closest('a, button');
    }

    // 7. Execute Navigation
    if ($linkBtn.length > 0 && $linkBtn.is(':visible')) {
        // Prevent default focus/edit
    // 4. Get Control to check options
    const control = get_field_control(target);
    let doctype = null;
    let isReadOnlyField = false;

    if (control && control.df) {
        doctype = control.df.options;
        isReadOnlyField = control.df.read_only;
    } else {
        // Fallback: try data-target
        doctype = target.getAttribute('data-target');
    }

    if (isReadOnlyField) return;

	// 5. Navigate if value exists
	const value = target.value;
	if (value && doctype) {
        // Prevent default focus/edit behavior
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();

        // Trigger the navigation
        $linkBtn[0].click();

        // Blur the input to prevent focus state artifacts
        target.blur();
    } else {
        // If we can't find the button, we shouldn't guess.
        // The user might have a custom field that doesn't link anywhere.
        // However, for standard Link fields without a button (e.g. sometimes in Grid),
        // we might want to fall back to the previous logic?
        // Current decision: Safe "Proxy Click" only. If no arrow, no click.
        // This aligns with "it should be the same process of how a Read Only link field works".
    }
        frappe.set_route('Form', doctype, value);
        target.blur();
	}

}, true); // Capture phase is crucial
