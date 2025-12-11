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

        frappe.set_route('Form', doctype, value);
        target.blur();
	}

}, true); // Capture phase is crucial
