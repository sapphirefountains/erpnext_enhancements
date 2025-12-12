frappe.provide("frappe.search");

console.log("[ERPNext Enhancements] Script loaded");

$(document).on("app_ready", function () {
	console.log("[ERPNext Enhancements] app_ready");
	if (frappe.search.AwesomeBar) {
		const original_make_global_search = frappe.search.AwesomeBar.prototype.make_global_search;
		frappe.search.AwesomeBar.prototype.make_global_search = function (txt) {
			// Call the original method to populate options
			original_make_global_search.call(this, txt);

			// Find the "Search for X" option and boost its index
			// The original implementation adds an option with default property "Search"
			if (this.options && this.options.length > 0) {
				const searchItem = this.options.find((opt) => opt.default === "Search");
				if (searchItem) {
					// Set a very high index to ensure it is always at the top
					// Standard indices are around 10-100.
					searchItem.index = 100000;
				}
			}
		};
	}

	// Global Map Placeholder Logic
	// Automatically renders a Google Map in 'custom_map_placeholder' field
	// if the DocType has exactly one Link field to 'Address'.
	if (frappe.ui && frappe.ui.form && frappe.ui.form.Controller) {
		const original_form_refresh = frappe.ui.form.Controller.prototype.refresh;
		frappe.ui.form.Controller.prototype.refresh = function () {
			// Run the original refresh
			let ret;
			if (original_form_refresh) {
				ret = original_form_refresh.apply(this, arguments);
			}

			// Run our custom map logic
			try {
				render_address_map(this.frm || this);
			} catch (e) {
				console.error("Error in Global Map Placeholder logic:", e);
			}

			return ret;
		};
	}

	setup_global_autosave();
});

function render_address_map(frm) {
	// 0. Safety Check
	if (!frm || !frm.fields_dict) return;

	// 1. Check if the target placeholder field exists
	if (!frm.fields_dict.custom_map_placeholder) {
		return;
	}

	// 2. Find Link fields pointing to "Address"
	// We use frm.meta.fields to get the doctype definition
	if (!frm.meta || !frm.meta.fields) return;

	const address_link_fields = frm.meta.fields.filter(
		(df) => df.fieldtype === "Link" && df.options === "Address"
	);

	// 3. Ensure exactly one link field exists to avoid ambiguity
	if (address_link_fields.length !== 1) {
		// If 0, we can't do anything.
		// If >1, we avoid conflicts by doing nothing.
		return;
	}

	const link_field = address_link_fields[0];
	const address_name = frm.doc[link_field.fieldname];

	// 4. If we have a linked address, fetch details and render
	if (address_name) {
		frappe.db.get_value("Address", address_name, "custom_full_address").then((r) => {
			if (r && r.message && r.message.custom_full_address) {
				const full_address = r.message.custom_full_address;
				const map_html = `
						<div class="map-wrapper" style="width: 100%; height: 400px; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
							<iframe
								width="100%"
								height="100%"
								frameborder="0"
								scrolling="no"
								marginheight="0"
								marginwidth="0"
								src="https://maps.google.com/maps?q=${encodeURIComponent(full_address)}&output=embed">
							</iframe>
						</div>
					`;
				if (frm.fields_dict.custom_map_placeholder.$wrapper) {
					frm.fields_dict.custom_map_placeholder.$wrapper.html(map_html);
				}
			} else {
				// Address linked but no full address found
				if (frm.fields_dict.custom_map_placeholder.$wrapper) {
					frm.fields_dict.custom_map_placeholder.$wrapper.html("");
				}
			}
		});
	} else {
		// No address selected yet
		if (frm.fields_dict.custom_map_placeholder.$wrapper) {
			frm.fields_dict.custom_map_placeholder.$wrapper.html("");
		}
	}
}

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
	let field = $el.data("control"); // Often attached by custom scripts or framework
	if (field) return field;

	const fieldname = $el.closest("[data-fieldname]").attr("data-fieldname");

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
	const grid_row = $el.closest(".grid-row");
	if (grid_row.length) {
		const grid_row_obj = grid_row.data("grid_row");
		if (grid_row_obj && grid_row_obj.docfields && fieldname) {
			// Logic to get the grid column definition
			const df = grid_row_obj.docfields.find((d) => d.fieldname === fieldname);
			// For grids, we don't have a full control object for each cell usually,
			// but we might get the value from the doc.
			if (df) {
				return {
					df: df,
					get_value: () => {
						// Return value from the row doc
						return grid_row_obj.doc[fieldname];
					},
				};
			}
		}
	}

	return null;
}

document.addEventListener(
	"click",
	function (e) {
		// 1. Basic Checks
		if (e.button !== 0) return; // Only allow Left Click
		const target = e.target;
		if (target.tagName !== "INPUT") return;

		// 2. Check if this input belongs to a Frappe Control
		const controlElement = target.closest(".frappe-control");
		if (!controlElement) return;

		// 3. Determine if it is a Link-like field
		let isLink = false;
		const fieldtype = controlElement.getAttribute("data-fieldtype");

		// Primary check via attribute
		if (fieldtype && (fieldtype === "Link" || fieldtype === "Dynamic Link")) {
			isLink = true;
		} else {
			// Secondary check via control object
			const c = get_field_control(target);
			if (c && c.df && (c.df.fieldtype === "Link" || c.df.fieldtype === "Dynamic Link")) {
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
		let $linkBtn = $control.find(".btn-open");

		// Fallback selectors for the link button
		if ($linkBtn.length === 0) $linkBtn = $control.find('[data-action="open-link"]');
		if ($linkBtn.length === 0) $linkBtn = $control.find(".link-btn"); // Common wrapper in some versions
		if ($linkBtn.length === 0) $linkBtn = $control.find("a.btn-open"); // Specific tag check

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
			if (control.df.fieldtype === "Link") {
				doctype = control.df.options;
			} else if (control.df.fieldtype === "Dynamic Link") {
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
				window.open(url, "_blank");
			} else {
				frappe.set_route("Form", doctype, docname);
			}
			target.blur();
		}
	},
	true
); // Capture phase

/* Global Autosave Implementation */
const AUTOSAVE_INTERVAL = 10000;
const AUTOSAVE_DEBOUNCE = 3000;
const EXCLUDED_DOCTYPES = [];
let autosave_debounce_timer = null;

function setup_global_autosave() {
	console.log("[ERPNext Enhancements] setup_global_autosave");

	// 1. Interval Listener
	setInterval(() => {
		try_autosave_if_dirty();
	}, AUTOSAVE_INTERVAL);

	// 2. Blur/FocusOut Listener
	document.addEventListener(
		"focusout",
		(e) => {
			if (should_trigger_autosave(e.target)) {
				clearTimeout(autosave_debounce_timer);
				autosave_debounce_timer = setTimeout(() => {
					try_autosave_if_dirty();
				}, AUTOSAVE_DEBOUNCE);
			}
		},
		true
	); // Capture phase
}

function should_trigger_autosave(target) {
	if (!window.cur_frm || !cur_frm.doc) return false;

	// Must be an input element
	const tag = target.tagName;
	if (!["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return false;

	// Must be inside the form wrapper
	if (!$(target).closest(".frappe-control").length) return false;

	return true;
}

function try_autosave_if_dirty() {
	if (!window.cur_frm || !cur_frm.doc) return;
	if (cur_frm.is_new()) return;
	console.log("[Autosave Debug] Checking...", { is_dirty: cur_frm.is_dirty(), saving: cur_frm.saving, doc: cur_frm.doc.name });
	if (!cur_frm.is_dirty()) return;
	if (cur_frm.saving) return;
	if (EXCLUDED_DOCTYPES.includes(cur_frm.doc.doctype)) return;

	// Check mandatory fields before saving to prevent UI freeze/validation errors
	if (!check_mandatory_fields(cur_frm)) {
		console.log("[Autosave Debug] Skipping save: Mandatory fields missing.");
		return;
	}

    console.log("[Autosave Debug] Attempting silent save...");

    // 1. Save original globals
    const _original_msgprint = frappe.msgprint;
    const _original_throw = frappe.throw;
    const _original_show_alert = frappe.show_alert;
    const _original_freeze = frappe.dom.freeze;
    const _original_unfreeze = frappe.dom.unfreeze;

    // 2. Define restoration
    const restore_globals = () => {
        frappe.msgprint = _original_msgprint;
        frappe.throw = _original_throw;
        frappe.show_alert = _original_show_alert;
        frappe.dom.freeze = _original_freeze;
        frappe.dom.unfreeze = _original_unfreeze;
    };

    // 3. Mock globals to silence UI
    frappe.msgprint = () => {};
    frappe.throw = (msg) => { throw new Error(msg); };
    frappe.show_alert = () => {};
    // Prevent UI freezing
    frappe.dom.freeze = () => {};
    frappe.dom.unfreeze = () => {};

    // 4. Call Save
    // We use cur_frm.save but rely on our mocks to suppress validation errors/dialogs
    cur_frm.save(
        'Save',
        null, // callback - usually handles UI update, but we want to stay silent or let frappe handle it
        null, // btn
        null  // on_error - if validation fails, it calls this. We don't need to do anything as msgprint is mocked.
    ).finally(() => {
        // Always restore globals after the attempt
        restore_globals();
        console.log("[Autosave Debug] Finished attempt, globals restored.");
    });
}

function check_mandatory_fields(frm) {
	if (!frm || !frm.doc || !frm.meta || !frm.meta.fields) return true;

	for (let i = 0; i < frm.meta.fields.length; i++) {
		const df = frm.meta.fields[i];
		const fieldname = df.fieldname;

		// Use the runtime docfield if available (handles dynamic properties like Mandatory Depends On)
		const effective_df = (frm.fields_dict && frm.fields_dict[fieldname] && frm.fields_dict[fieldname].df) ? frm.fields_dict[fieldname].df : df;

		if (effective_df.reqd && !effective_df.hidden) {
			const value = frm.doc[fieldname];

			// Table Check
			if (effective_df.fieldtype === 'Table') {
				if (!value || value.length === 0) {
					return false;
				}
			}
			// Standard Check
			else if (value === null || value === undefined || value === "") {
				if (value !== 0) {
					return false;
				}
			}
		}
	}
	return true;
}
