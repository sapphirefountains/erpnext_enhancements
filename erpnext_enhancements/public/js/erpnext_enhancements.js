frappe.provide("frappe.search");

console.log("[ERPNext Enhancements] Script loaded");

// Auto-Save Configuration Cache
let auto_save_config = {
    doctypes: [],
    users: [],
    loaded: false
};

// Global Debounce Timer
let auto_save_timer = null;

$(document).on("app_ready", function () {
	console.log("[ERPNext Enhancements] app_ready");

    // Load Auto-Save Settings
    frappe.call({
        method: "frappe.client.get",
        args: {
            doctype: "ERPNext Enhancements Settings",
            name: "ERPNext Enhancements Settings"
        },
        callback: function(r) {
            if (r.message) {
                const settings = r.message;
                // Parse allowed doctypes
                if (settings.auto_save_doctypes && Array.isArray(settings.auto_save_doctypes)) {
                    auto_save_config.doctypes = settings.auto_save_doctypes.map(d => d.dt);
                }
                // Parse allowed users
                if (settings.auto_save_users && Array.isArray(settings.auto_save_users)) {
                    auto_save_config.users = settings.auto_save_users.map(u => u.user);
                }
                auto_save_config.loaded = true;
                console.log("[ERPNext Enhancements] Auto-Save Config Loaded:", auto_save_config);
            }
        }
    });

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

            const frm = this.frm || this;

			// Run our custom map logic
			try {
				render_address_map(frm);
			} catch (e) {
				console.error("Error in Global Map Placeholder logic:", e);
			}

            // Init Auto-Save Listener
            try {
                init_auto_save(frm);
            } catch (e) {
                console.error("Error in Auto-Save logic:", e);
            }

			return ret;
		};
	}
});

function init_auto_save(frm) {
    if (!frm || !frm.wrapper) return;

    // Remove existing listeners to prevent duplicates if refresh is called multiple times
    const $wrapper = $(frm.wrapper);
    $wrapper.off('keydown.autosave click.autosave change.autosave');

    $wrapper.on('keydown.autosave click.autosave change.autosave', function() {
        // Reset timer on any activity
        if (auto_save_timer) {
            clearTimeout(auto_save_timer);
        }

        // Start new timer (15 seconds)
        auto_save_timer = setTimeout(function() {
            trigger_auto_save(frm);
        }, 15000);
    });
}

function trigger_auto_save(frm) {
    // 0. Config Loaded?
    if (!auto_save_config.loaded) return;

    // 1. Check Whitelists
    if (!auto_save_config.doctypes.includes(frm.doctype)) {
        // console.log("[Auto-Save] DocType not whitelisted:", frm.doctype);
        return;
    }
    if (!auto_save_config.users.includes(frappe.session.user)) {
        // console.log("[Auto-Save] User not whitelisted:", frappe.session.user);
        return;
    }

    // 2. Document State Checks
    if (frm.is_new()) return; // Only saved docs
    if (frm.doc.docstatus !== 0) return; // Only Drafts
    if (!frm.is_dirty()) return; // Only if modified

    // 3. Silent Mandatory Check
    // We manually check reqd fields to avoid triggering the alert popup from frm.save()
    let missing_mandatory = false;

    // Check main doc fields
    $.each(frm.fields_dict, function(fieldname, field) {
        if (field.df.reqd && !field.get_value()) {
            missing_mandatory = true;
            // console.log("[Auto-Save] Missing mandatory:", fieldname);
            return false; // break loop
        }
    });

    if (missing_mandatory) return;

    // Check Child Tables?
    // Usually frm.save() validates child tables too.
    // Implementing deep silent validation for child tables is complex.
    // For now, if we pass the main doc check, we attempt save.
    // If frm.save() fails due to child table validation, it WILL show a popup.
    // However, the requirement is "don't attempt a save if the form has mandatory fields left unfilled out".
    // We should make a best effort to check child tables if possible, but accessing them reliably generically is tricky.
    // Let's rely on Frappe's visual feedback for child tables if they break,
    // OR we could try to emulate `frm.validate_mandatory_fields()` without the UI part.
    // But `validate_mandatory_fields` is mixed with UI logic.

    // Given the constraints, checking header fields is the 90% solution.
    // If a child row is incomplete, the user will get a popup.
    // The user said: "Forget the silent save part, let the notifications flow through." (Re: Success)
    // But for failures: "abort... avoid validation errors and triggers".
    // So if we missed a child table check, and it errors, we failed the requirement slightly.
    // Let's iterate child tables too.

    const table_fields = frm.meta.fields.filter(df => df.fieldtype === 'Table');
    for (let tf of table_fields) {
        const rows = frm.doc[tf.fieldname] || [];
        // We need the meta for the child doctype
        const child_meta = frappe.get_meta(tf.options);
        if (!child_meta) continue;

        for (let row of rows) {
            for (let child_field of child_meta.fields) {
                if (child_field.reqd && !row[child_field.fieldname]) {
                    missing_mandatory = true;
                    break;
                }
            }
            if (missing_mandatory) break;
        }
        if (missing_mandatory) break;
    }

    if (missing_mandatory) return;

    // 4. Execute Save
    console.log("[ERPNext Enhancements] Triggering Auto-Save for", frm.docname);
    frm.save();
}

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
