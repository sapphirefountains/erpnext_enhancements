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

// Patch Link Field to be clickable for navigation
// We use a function to allow retrying if ControlLink is not yet available
const patchLinkField = function(retry_count = 0) {
	if (frappe.ui && frappe.ui.form && frappe.ui.form.ControlLink) {
		if (frappe.ui.form.ControlLink.prototype.make_input.patched) return;

		const original_make_input = frappe.ui.form.ControlLink.prototype.make_input;
		frappe.ui.form.ControlLink.prototype.make_input = function() {
			original_make_input.apply(this, arguments);

			if (this.$input && this.$input[0]) {
				// Use native addEventListener with capture=true to ensure we catch the event
				// before any other handlers (like Awesomplete) can stop it.
				this.$input[0].addEventListener('click', (e) => {
					// 0. Ensure this is explicitly a Link field (prevents regression with Dynamic Link)
					if (this.df.fieldtype !== 'Link') {
						return;
					}

					// 1. Check if the field is editable (not read-only)
					// We check df.read_only and also the input properties just in case
					if (this.df.read_only || this.$input.prop('readonly') || this.$input.prop('disabled')) {
						return;
					}

					// 2. Check if the field has a value
					const value = this.get_value();

					// 3. Navigate if value exists and it's a valid link field
					if (value && this.df.options) {
						frappe.set_route('Form', this.df.options, value);

						// 4. Prevent default focus/edit behavior
						// The user explicitly requested this behavior, acknowledging they will use the 'X' button to clear/edit.
						e.preventDefault();
						e.stopPropagation();
						e.stopImmediatePropagation();
					}
				}, true); // Capture phase
			}
		};
		frappe.ui.form.ControlLink.prototype.make_input.patched = true;
		console.log("ERPNext Enhancements: ControlLink patched successfully.");
	} else {
		// Retry a few times if ControlLink is not yet loaded
		if (retry_count < 5) {
			setTimeout(() => patchLinkField(retry_count + 1), 1000);
		}
	}
};

// Attempt to patch immediately (for cases where script loads late)
patchLinkField();

// Attempt to patch on app_ready (for cases where script loads early)
$(document).on('app_ready', function() {
	patchLinkField();
});
