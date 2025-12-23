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

// Session Management for Auto-Save
const TAB_ID = Math.random().toString(36).substring(2, 10);
console.log("[Auto-Save] Session ID:", TAB_ID);

$(document).on("app_ready", function () {
	console.log("[ERPNext Enhancements] app_ready");

    // Run Auto-Save Cleanup (LRU or Age-based)
    try {
        cleanup_old_drafts();
    } catch (e) {
        console.error("[Auto-Save] Cleanup failed:", e);
    }

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

function cleanup_old_drafts() {
    const keys = Object.keys(localStorage);
    const now = new Date();
    const expiry_ms = 7 * 24 * 60 * 60 * 1000; // 7 Days

    let removed_count = 0;

    keys.forEach(key => {
        if (key.startsWith("sf_autosave_")) {
            try {
                const item = JSON.parse(localStorage.getItem(key));
                if (item && item.timestamp) {
                    const saved_time = frappe.datetime.str_to_obj(item.timestamp);
                    if ((now - saved_time) > expiry_ms) {
                        localStorage.removeItem(key);
                        removed_count++;
                    }
                }
            } catch (e) {
                // Corrupt data, remove it
                localStorage.removeItem(key);
            }
        }
    });

    if (removed_count > 0) {
        console.log(`[Auto-Save] Cleaned up ${removed_count} old drafts.`);
    }
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

function is_modified(current_doc, stored_data) {
    if (!stored_data) return true;

    // Top-level field comparison (Shallow)
    const keys = Object.keys(current_doc);
    for (let i = 0; i < keys.length; i++) {
        const key = keys[i];
        if (key.startsWith('_')) continue; // Skip internal fields

        const val = current_doc[key];
        const stored_val = stored_data[key];

        // If Array (Child Table)
        if (Array.isArray(val)) {
             // Simple length check or reference check
             // Note: stored_val might be null if not in draft
             if (!stored_val || val.length !== stored_val.length) return true;
        } else {
             if (val !== stored_val) return true;
        }
    }

    return false;
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
    frm._last_autosaved_json = null; // Memory cache to prevent redundant writes
    frm._last_known_storage_ts = null; // For Conflict Detection

    // 1. Recovery Check (On Refresh)
    try {
        const stored_raw = localStorage.getItem(storage_key);
        if (stored_raw) {
            const stored = JSON.parse(stored_raw);
            const server_ts = frm.doc.modified;

            // Initialize last known storage timestamp
            if (stored.timestamp) {
                frm._last_known_storage_ts = stored.timestamp;
            }

            let should_prompt = false;

            if (is_new) {
                // For New Docs: Just check if we have data
                should_prompt = true;
            } else {
                // For Existing Docs: Server-Anchored Check
                if (stored.source_modified && stored.source_modified === server_ts) {
                     should_prompt = true;
                } else {
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
                const current_json = JSON.stringify(current_data);

                if (frm._last_autosaved_json && frm._last_autosaved_json === current_json) {
                    return;
                }

                // --- CONFLICT CHECK START ---
                try {
                    const current_stored_raw = localStorage.getItem(storage_key);
                    if (current_stored_raw) {
                        const current_stored = JSON.parse(current_stored_raw);

                        // If another tab wrote to it:
                        // 1. tab_id differs
                        // 2. AND the content changed from what we last knew (timestamp check)

                        if (current_stored.tab_id && current_stored.tab_id !== TAB_ID) {
                            if (frm._last_known_storage_ts && current_stored.timestamp !== frm._last_known_storage_ts) {
                                // CONFLICT: The storage changed since we last touched it/loaded it
                                console.warn("[Auto-Save] Conflict detected. Pausing auto-save.");
                                frappe.show_alert({
                                    message: 'Auto-save paused: Draft is being edited in another tab',
                                    indicator: 'red'
                                }, 5);
                                return; // ABORT SAVE
                            }
                        }
                    }
                } catch (e) {
                    console.error("Conflict check error:", e);
                }
                // --- CONFLICT CHECK END ---

                try {
                    const payload = {
                        timestamp: now,
                        source_modified: frm.doc.modified,
                        data: current_data,
                        tab_id: TAB_ID // Identify this session
                    };

                    localStorage.setItem(storage_key, JSON.stringify(payload));

                    frm._last_autosaved_json = current_json;
                    frm._last_known_storage_ts = now; // Update our known timestamp
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
