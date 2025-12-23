frappe.provide("frappe.search");

console.log("[ERPNext Enhancements] Script loaded");

// Auto-Save Configuration Cache
let auto_save_config = {
    // Default Whitelist
    doctypes: ['Sales Order', 'Purchase Order', 'Quotation', 'Project', 'Task', 'Issue'],
    loaded: false
};

// Track registered handlers to avoid duplicates
let registered_autosave_doctypes = new Set();

$(document).on("app_ready", function () {
	console.log("[ERPNext Enhancements] app_ready");

    // Load Auto-Save Settings
    frappe.call({
        method: "erpnext_enhancements.erpnext_enhancements.doctype.erpnext_enhancements_settings.erpnext_enhancements_settings.get_auto_save_configuration",
        callback: function(r) {
            if (r.message) {
                const settings = r.message;
                // Merge allowed doctypes with defaults
                if (settings.auto_save_doctypes && Array.isArray(settings.auto_save_doctypes)) {
                    // Create a unique set of doctypes
                    const merged = new Set([...auto_save_config.doctypes, ...settings.auto_save_doctypes]);
                    auto_save_config.doctypes = Array.from(merged);
                }
                auto_save_config.loaded = true;
                console.log("[ERPNext Enhancements] Auto-Save Config Loaded:", auto_save_config);

                // Register Global Handlers
                register_global_autosave_handlers(auto_save_config.doctypes);
            } else {
                // Fallback to defaults if no settings found
                register_global_autosave_handlers(auto_save_config.doctypes);
            }
        }
    });

	if (frappe.search.AwesomeBar) {
		const original_make_global_search = frappe.search.AwesomeBar.prototype.make_global_search;
		frappe.search.AwesomeBar.prototype.make_global_search = function (txt) {
			// Call the original method to populate options
			original_make_global_search.call(this, txt);

			// Find the "Search for [text]" option and boost its index
			if (this.options && this.options.length > 0) {
				const searchItem = this.options.find((opt) => opt.default === "Search");
				if (searchItem) {
					// Set a very high index to ensure it is always at the top
					searchItem.index = 100000;
				}
			}
		};
	}

	// Global Map Placeholder Logic & Auto Save Injection
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

            // Init Auto-Save Logic
            try {
                setup_auto_save(frm);
            } catch (e) {
                console.error("Error in Auto-Save logic:", e);
            }

			return ret;
		};
	}
});

// ==========================================
// Auto-Save Logic (Client-Side LocalStorage)
// ==========================================

function register_global_autosave_handlers(doctypes) {
    doctypes.forEach(doctype => {
        if (!registered_autosave_doctypes.has(doctype)) {
            frappe.ui.form.on(doctype, 'after_save', function(frm) {
                // Cleanup Logic
                if (frm._autosave_storage_key) {
                    // Safety Guard: Ensure we only delete the key belonging to THIS instance
                    // (frm._autosave_storage_key is unique to the frm instance)
                    localStorage.removeItem(frm._autosave_storage_key);
                    console.log("[Auto-Save] Cleared local draft for", frm._autosave_storage_key);

                    // If it was new, also ensure we clear that specifically just in case
                    if (frm._autosave_is_new_at_init) {
                         const user = frappe.session.user;
                         const new_key = `sf_autosave_${user}_${frm.doctype}_new`;
                         localStorage.removeItem(new_key);
                    }
                }
            });
            registered_autosave_doctypes.add(doctype);
            console.log(`[Auto-Save] Registered after_save handler for ${doctype}`);
        }
    });
}

function update_silent_indicator(frm) {
    const $header = $(frm.wrapper).find('.page-head .page-title .indicator-pill').parent();

    // Safety check: if we can't find the header location, abort
    if ($header.length === 0) return;

    let $indicator = $('#sf-autosave-status');

    // Create if not exists
    if ($indicator.length === 0) {
        $indicator = $('<span id="sf-autosave-status"></span>');
        // Insert before the indicator pill or append to title area
        $header.append($indicator);
    }

    const now = frappe.datetime.now_time();
    $indicator.text(`Local Draft Saved ${now}`);

    // Show
    $indicator.addClass('visible');

    // Fade out after 3 seconds
    setTimeout(() => {
        $indicator.removeClass('visible');
    }, 3000);
}

function setup_auto_save(frm) {
    if (!frm || !frm.wrapper) return;

    // 0. Skip if Submitted or Cancelled
    if (frm.doc.docstatus > 0) return;

    // Check Whitelist
    if (!auto_save_config.doctypes.includes(frm.doctype)) {
        return;
    }

    // Determine Storage Key
    const user = frappe.session.user;
    const is_new = frm.is_new();
    const doc_key = is_new ? `${frm.doctype}_new` : `${frm.doctype}_${frm.docname}`;
    const storage_key = `sf_autosave_${user}_${doc_key}`;

    // Store key on frm for access in cleanup hook
    frm._autosave_storage_key = storage_key;
    frm._autosave_is_new_at_init = is_new;

    // 1. Recovery Check (On Refresh)
    try {
        const stored_raw = localStorage.getItem(storage_key);
        if (stored_raw) {
            const stored = JSON.parse(stored_raw);
            const server_ts = frm.doc.modified;

            let should_prompt = false;

            if (is_new) {
                // For New Docs: Just check if we have data
                should_prompt = true;
            } else {
                // For Existing Docs: Server-Anchored Check
                // 1. Check if the draft was created from the CURRENT server version
                // We compare the 'source_modified' stored in draft vs current 'modified'
                if (stored.source_modified && stored.source_modified === server_ts) {
                     // The base versions match. Now check if content is different.
                     // Simple check: if timestamps differ (which they should if draft is newer)
                     // Or we could do a deep diff, but assumption is if we saved a draft, it's different.
                     // However, we must ensure we don't prompt if the draft is identical to current state (unlikely but possible)
                     should_prompt = true;
                } else {
                     // Mismatch! The server has moved on since this draft was made.
                     // Do NOT prompt. Log it.
                     console.log("[Auto-Save] Local draft ignored due to version mismatch (Server-Anchored check).");
                }
            }

            if (should_prompt) {
                frappe.warn(
                    'Unsaved Changes',
                    'Unsaved local changes found for this document. Would you like to restore them?',
                    () => {
                        restore_local_draft(frm, stored.data);
                    },
                    'Restore Draft',
                    () => {
                        localStorage.removeItem(storage_key);
                        // Removed update_silent_indicator to avoid "Saved" confusion
                        frappe.show_alert({message: 'Draft Discarded', indicator: 'orange'});
                    },
                    'Discard Draft'
                );
            }
        }
    } catch (e) {
        console.error("[Auto-Save] Error reading storage:", e);
    }

    // 2. Setup Debounced Save Listener
    const $wrapper = $(frm.wrapper);
    $wrapper.off('keydown.autosave change.autosave input.autosave');

    let save_timer = null;

    $wrapper.on('keydown.autosave change.autosave input.autosave', function() {
        if (save_timer) {
            clearTimeout(save_timer);
        }

        save_timer = setTimeout(function() {
            if (frm.is_dirty()) {
                const now = frappe.datetime.now_datetime();
                const current_data = frm.doc;

                try {
                    // Use the potentially updated key (if needed) but usually we stick to the init key
                    // until refresh happens. For New docs, key is _new until saved and refreshed.
                    localStorage.setItem(storage_key, JSON.stringify({
                        timestamp: now,
                        source_modified: frm.doc.modified, // Anchor to current server version
                        data: current_data
                    }));

                    update_silent_indicator(frm);
                } catch (e) {
                    console.error("[Auto-Save] Write failed:", e);
                }
            }
        }, 15000);
    });
}

function restore_local_draft(frm, data) {
    if (!data) return;

    frappe.dom.freeze("Restoring Draft...");

    // System fields to skip
    const system_fields = [
        'name', 'owner', 'creation', 'modified', 'modified_by',
        'docstatus', 'idx', 'parent', 'parenttype', 'parentfield'
    ];

    const simple_fields_dict = {};
    let child_tables = {};

    Object.keys(data).forEach(key => {
        if (system_fields.includes(key)) return;
        if (key.startsWith('_')) return;

        const value = data[key];

        if (Array.isArray(value)) {
             child_tables[key] = value;
        } else {
             simple_fields_dict[key] = value;
        }
    });

    // 1. Batch Update Simple Fields
    // This is much faster than one-by-one
    let promise_chain = frm.set_value(simple_fields_dict);

    // 2. Handle Child Tables sequentially
    // Child tables are tricky to batch-set safely without resetting the whole table sometimes
    // But usually frm.set_value(fieldname, array) works for tables too in newer Frappe
    // Let's try adding them to the chain
    Object.keys(child_tables).forEach(key => {
        promise_chain = promise_chain.then(() => {
            return frm.set_value(key, child_tables[key]);
        });
    });

    promise_chain.then(() => {
        frappe.dom.unfreeze();
        frappe.show_alert({message: 'Draft Restored', indicator: 'green'});
    }).catch(e => {
        frappe.dom.unfreeze();
        console.error("Restoration Failed", e);
        frappe.msgprint("Error restoring draft: " + e.message);
    });
}


// ==========================================
// Existing Logic (Map & Link Navigation)
// ==========================================

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

function get_field_control(element) {
	// Try to find the control instance
	let $el = $(element);
	let field = $el.data("control");
	if (field) return field;

	const fieldname = $el.closest("[data-fieldname]").attr("data-fieldname");

	// Iterate cur_frm fields
	if (fieldname && window.cur_frm && window.cur_frm.fields_dict) {
		if (cur_frm.fields_dict[fieldname]) {
			return cur_frm.fields_dict[fieldname];
		}
	}

	// Iterate cur_dialog fields
	if (fieldname && window.cur_dialog && window.cur_dialog.fields_dict) {
		if (cur_dialog.fields_dict[fieldname]) {
			return cur_dialog.fields_dict[fieldname];
		}
	}

	// Grid Row fields
	const grid_row = $el.closest(".grid-row");
	if (grid_row.length) {
		const grid_row_obj = grid_row.data("grid_row");
		if (grid_row_obj && grid_row_obj.docfields && fieldname) {
			const df = grid_row_obj.docfields.find((d) => d.fieldname === fieldname);
			if (df) {
				return {
					df: df,
					get_value: () => {
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
		if ($linkBtn.length === 0) $linkBtn = $control.find(".link-btn");
		if ($linkBtn.length === 0) $linkBtn = $control.find("a.btn-open");

		// If we found the button, we can just click it (Proxy Click)
		if ($linkBtn.length > 0) {
			if (e.ctrlKey || e.metaKey) {
                // Let manual logic handle new tab
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
		const control = get_field_control(target);
		let doctype = null;
		let docname = null;

		if (control && control.df) {
			// Resolve DocType
			if (control.df.fieldtype === "Link") {
				doctype = control.df.options;
			} else if (control.df.fieldtype === "Dynamic Link") {
				if (control.df.options && window.cur_frm) {
					doctype = cur_frm.doc[control.df.options];
				}
			}

			// Resolve DocName (Value)
			if (control.get_value) {
				docname = control.get_value();
			} else {
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
