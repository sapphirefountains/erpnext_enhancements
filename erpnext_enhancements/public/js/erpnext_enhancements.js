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

// Make text inside Link Fields clickable for navigation
// This is outside app_ready to ensure it applies before controls are created
if (frappe.ui && frappe.ui.form && frappe.ui.form.ControlLink) {
	const original_make_input = frappe.ui.form.ControlLink.prototype.make_input;
	frappe.ui.form.ControlLink.prototype.make_input = function() {
		original_make_input.apply(this, arguments);

		if (this.$input) {
			this.$input.on('click', (e) => {
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
				}
			});
		}
	};
}
