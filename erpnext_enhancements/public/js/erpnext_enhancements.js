frappe.provide("frappe.search");
frappe.provide("erpnext_enhancements.utils");

// ==========================================
// Utilities
// ==========================================

erpnext_enhancements.utils.waitFor = function(check, callback, max_attempts=20, interval=500) {
    let attempts = 0;

    // Immediate check
    if (check()) {
        callback();
        return;
    }

    const i = setInterval(() => {
        attempts++;
        if (check()) {
            clearInterval(i);
            callback();
        } else if (attempts >= max_attempts) {
            clearInterval(i);
            // console.warn("[ERPNext Enhancements] WaitFor timed out");
        }
    }, interval);
};

$(document).on("app_ready", function () {
	if (frappe.search.AwesomeBar) {
		const original_make_global_search = frappe.search.AwesomeBar.prototype.make_global_search;

		// Add debounced search method to prototype
		frappe.search.AwesomeBar.prototype.global_search_debounced = frappe.utils.debounce(function (txt) {
			const me = this;
			if (!txt || txt.length < 3) return;

            // Record the search string to handle race conditions
            this._last_search_txt = txt;

			frappe.call({
				method: "erpnext_enhancements.api.search.search_global_docs",
				args: { txt: txt },
				callback: function (r) {
                    // Abort if user has typed a different string in the meantime
                    if (me._last_search_txt !== txt) return;

					if (r.message && r.message.length) {
						const new_options = r.message.map((d) => ({
							label: d.label,
							value: d.value,
							route: d.route,
							index: d.index || 50,
							match: d.match || d.value,
							description: d.description,
						}));

						if (!me.options) me.options = [];

						// Deduplicate based on route or value
						const existing_values = new Set(me.options.map((o) => o.value));

						new_options.forEach((opt) => {
							if (!existing_values.has(opt.value)) {
								me.options.push(opt);
							}
						});

						// Re-sort (descending index)
						me.options.sort((a, b) => (b.index || 0) - (a.index || 0));

						// Refresh Awesomplete
						if (me.awesomplete) {
							me.awesomplete.list = me.options;
							me.awesomplete.evaluate();
						}
					}
				},
			});
		}, 300);

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

			// Trigger Live Search
			this.global_search_debounced(txt);
		};
	}

	// Global Map Placeholder Logic
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

			return ret;
		};

        // Override save to handle draft cleanup
        const original_form_save = frappe.ui.form.Controller.prototype.save;
        frappe.ui.form.Controller.prototype.save = function(...args) {
            const ret = original_form_save.apply(this, args);
            // ret is typically a Promise in recent Frappe versions
            if (ret && ret.then) {
                ret.then(() => {
                    const frm = this.frm || this;
                    cleanup_draft(frm);
                });
            }
            return ret;
        };

        // Override trigger to check drafts on load
        const original_form_trigger = frappe.ui.form.Controller.prototype.trigger;
        frappe.ui.form.Controller.prototype.trigger = function (event, ...args) {
            let ret;
            if (original_form_trigger) {
                 ret = original_form_trigger.apply(this, [event, ...args]);
            }

            if (event === 'onload') {
                const frm = this.frm || this;
                // Delay slightly to ensure dashboard wrapper is rendered
                setTimeout(() => {
                    try {
                         if (frm && !frm.is_new()) {
                            check_draft_on_load(frm);
                        }
                    } catch (e) {
                        console.error("Error checking drafts on load:", e);
                    }
                }, 500);
            }
            return ret;
        };
	}

    // Setup Home Buttons
    try {
        setup_home_buttons();
    } catch (e) {
        console.error("Error setting up home buttons:", e);
    }

    // Setup Global Navigation Guard
    try {
        setup_navigation_guard();
    } catch (e) {
        console.error("Error setting up navigation guard:", e);
    }

    // Initialize Auto-Save Listeners
    try {
        init_auto_save_listeners();
    } catch (e) {
        console.error("Error initializing auto-save:", e);
    }

    // Sidebar Enforcement
    try {
        enforce_sidebar_expanded();
    } catch (e) {
        console.error("Error enforcing sidebar:", e);
    }

    // Remove "Go to Home" button
    try {
        remove_go_to_home_button();
    } catch (e) {
        console.error("Error removing 'Go to Home' button:", e);
    }

    // Add "Add to Desk" in List View & Form View
    try {
        setup_add_to_desk_global();
    } catch (e) {
        console.error("Error setting up 'Add to Desk':", e);
    }

    // Default Full Width
    try {
        setup_default_full_width();
    } catch (e) {
        console.error("Error setting up default full width:", e);
    }
});

// ==========================================
// Default Full Width
// ==========================================

function setup_default_full_width() {
    // If the setting hasn't been configured yet, default to full width
    if (localStorage.getItem("container_fullwidth") === null) {
        localStorage.setItem("container_fullwidth", "true");

        // Ensure the class is applied if frappe missed it
        if (!$('body').hasClass('full-width')) {
            $('body').addClass('full-width');
        }
    }
}

// ==========================================
// Add to Desk (Global)
// ==========================================

function setup_add_to_desk_global() {
    // 1. List View Hook
    erpnext_enhancements.utils.waitFor(
        () => frappe.views && frappe.views.ListView,
        () => {
            const original_get_menu_items = frappe.views.ListView.prototype.get_menu_items;
            frappe.views.ListView.prototype.get_menu_items = function () {
                const items = original_get_menu_items.apply(this, arguments) || [];
                items.push({
                    label: __('Add to Desk'),
                    action: () => {
                        const route = frappe.get_route_str();
                        add_to_workspace_dialog(this.doctype, route, 'List');
                    },
                    standard: true
                });
                return items;
            };
        }
    );

    // 2. Form View Hook (via Controller)
    erpnext_enhancements.utils.waitFor(
        () => frappe.ui && frappe.ui.form && frappe.ui.form.Controller,
        () => {
             const original_form_refresh = frappe.ui.form.Controller.prototype.refresh;
             frappe.ui.form.Controller.prototype.refresh = function() {
                 const ret = original_form_refresh.apply(this, arguments);
                 if (this.frm && !this.frm.is_new() && !this.frm._add_to_desk_added) {
                     this.frm.page.add_menu_item(__('Add to Desk'), () => {
                         const route = frappe.get_route_str();
                         add_to_workspace_dialog(this.frm.doctype, route, 'Form');
                     });
                     this.frm._add_to_desk_added = true;
                 }
                 return ret;
             };
        }
    );
}


function add_to_workspace_dialog(doctype, route, type_hint) {
    // Fetch available workspaces for the user
    frappe.call({
        method: "erpnext_enhancements.api.workspace_utils.get_workspaces_for_user",
        callback: (r) => {
            const workspaces = r.message || [];
            const options = workspaces.map(w => ({ label: w.title || w.label || w.name, value: w.name }));

            const d = new frappe.ui.Dialog({
                title: __('Add to Desk'),
                fields: [
                    {
                        label: __('Label'),
                        fieldname: 'label',
                        fieldtype: 'Data',
                        default: doctype,
                        reqd: 1
                    },
                    {
                        label: __('Workspace'),
                        fieldname: 'workspace',
                        fieldtype: 'Link',
                        options: 'Workspace',
                        filters: {
                            'public': 1
                        },
                        reqd: 1,
                        get_query: () => {
                            return {
                                filters: {
                                    'public': 1
                                }
                            };
                        }
                    },
                    {
                        label: __('Type'),
                        fieldname: 'type',
                        fieldtype: 'Select',
                        options: ['Shortcut', 'Link', 'Card', 'DocType'],
                        default: type_hint === 'List' ? 'DocType' : 'Link'
                    }
                ],
                primary_action_label: __('Add'),
                primary_action: (values) => {
                    frappe.call({
                        method: "erpnext_enhancements.api.workspace_utils.add_shortcut_to_workspace",
                        args: {
                            workspace: values.workspace,
                            label: values.label,
                            type: values.type,
                            link_to: values.type === 'DocType' ? doctype : route,
                            doc_type: doctype 
                        },
                        callback: (res) => {
                            if (!res.exc) {
                                frappe.msgprint(__('Added to Workspace successfully'));
                                d.hide();
                            }
                        }
                    });
                }
            });
            d.show();
        }
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

// ==========================================
// Home Button Logic
// ==========================================

function setup_home_buttons() {
    // 0. Idempotency Check
    if (window._home_buttons_installed) return;
    window._home_buttons_installed = true;

    // 1. Navbar Button
    // Check if it exists to avoid duplicates
    if ($('.navbar-home-link').length === 0) {
        const home_html = `
            <li class="nav-item navbar-home-link">
                <a class="nav-link" onclick="frappe.set_route('home')" title="Home" style="cursor: pointer;">
                    <span class="home-icon">
                        <svg class="icon icon-md" style="width: 16px; height: 16px; vertical-align: middle;">
                            <use href="#icon-home"></use>
                        </svg>
                    </span>
                    <span class="hidden-xs" style="margin-left: 5px;">Home</span>
                </a>
            </li>
        `;
        // Append to the first navbar-nav (left side)
        $('.navbar-nav').first().append(home_html);
    }

}

// ==========================================
// Global Navigation Guard
// ==========================================

function setup_navigation_guard() {
    if (window._nav_guard_installed) {
        // console.log("[ERPNext Enhancements] Navigation Guard already installed.");
        return;
    }

    let is_navigating = false;
    function is_dirty() {
        try {
            if (window.cur_frm && window.cur_frm.doc && window.cur_frm.is_dirty()) {
                return true;
            }
        } catch (e) {
            // ignore
        }
        return false;
    }

    function clear_dirty() {
        if (window.cur_frm && window.cur_frm.doc) {
            window.cur_frm.doc.__unsaved = 0;
        }
    }

    // 1. Global Click Interceptor (Capture Phase)
    // This catches clicks on Sidebar, Breadcrumbs, and Link fields BEFORE Frappe/Browser handles them.
    document.addEventListener('click', function (e) {
        // Find closest anchor tag
        const target = e.target.closest('a');
        if (!target) return;

        // Check if it's an internal link (hash-based)
        const href = target.getAttribute('href');
        if (!href || !href.startsWith('#')) return;

        // Ignore new tabs or modifiers
        if (e.ctrlKey || e.metaKey || e.shiftKey || target.target === '_blank') return;

        // If dirty, intercept
        if (is_dirty()) {
            // Prevent the default navigation
            e.preventDefault();
            e.stopPropagation();
            e.stopImmediatePropagation();

            if (is_navigating) return;
            is_navigating = true;

            frappe.confirm(
                __("You have unsaved changes. Are you sure you want to leave?"),
                () => {
                    // User chose to leave
                    is_navigating = false;
                    clear_dirty();

                    // Manually proceed with the navigation
                    // Since we blocked the click, the hash didn't change.
                    // We can now safely set the hash or click again?
                    // Setting hash is safest.
                    window.location.href = href;
                },
                () => {
                    // User chose to stay
                    is_navigating = false;
                    // Do nothing, we already prevented default.
                }
            );
        }
    }, true); // true = Capture Phase (runs before bubbling/other listeners)


    // 2. Intercept frappe.set_route (Programmatic / Awesomebar)
    if (frappe.set_route) {
        const original_set_route = frappe.set_route;
        frappe.set_route = function (...args) {
            if (is_dirty()) {
                if (is_navigating) return Promise.resolve();
                is_navigating = true;

                return new Promise((resolve, reject) => {
                    frappe.confirm(
                        __("You have unsaved changes. Are you sure you want to leave?"),
                        () => {
                            is_navigating = false;
                            clear_dirty();
                            resolve(original_set_route.apply(this, args));
                        },
                        () => {
                            is_navigating = false;
                            reject();
                        }
                    );
                });
            } else {
                return original_set_route.apply(this, args);
            }
        };
    }

    // 3. Intercept Router Render (Back/Forward Button Fallback)
    // If a navigation event bypasses the click listener (e.g. Browser Back Button),
    // the hash changes and Frappe's router picks it up.
    // We override 'render' to stop the view from actually changing.
    if (frappe.router && frappe.router.render) {
        const original_render = frappe.router.render;
        frappe.router.render = function(...args) {
            if (is_dirty()) {
                 // The hash has already changed. We must stop rendering.
                 if (is_navigating) return; // Prevent double dialogs
                 is_navigating = true;

                 frappe.confirm(
                    __("You have unsaved changes. Are you sure you want to leave?"),
                    () => {
                        is_navigating = false;
                        clear_dirty();
                        // Proceed with rendering the new page
                        original_render.apply(this, args);
                    },
                    () => {
                        is_navigating = false;
                        // User chose to stay.
                        // We do NOT call original_render, so the DOM remains on the old form.
                        // Note: The URL in the browser bar will show the NEW hash (from the back button).
                        // This is a known trade-off to avoid infinite hash-revert loops.
                        // If the user attempts to Save, it will work for the current DOM.
                    }
                 );
                 // We return here to BLOCK the render
                 return;
            }

            return original_render.apply(this, args);
        };
    }

    window._nav_guard_installed = true;
}

// ==========================================
// Safe Auto-Save (User Form Drafts)
// ==========================================

function init_auto_save_listeners() {
    if (window._auto_save_installed) return;
    window._auto_save_installed = true;

    // 1. Listen for changes on all Frappe Control inputs
    // We delegate to document to capture dynamically created fields
    $(document).on('change', '.frappe-control input, .frappe-control select, .frappe-control textarea', function(e) {
        // Find the form
        if (window.cur_frm) {
            save_draft_handler(window.cur_frm);
        }
    });

    // 2. Listen for Visibility Change (Context Switch)
    document.addEventListener("visibilitychange", function() {
        if (document.visibilityState === 'hidden') {
            if (window.cur_frm) {
                save_draft_handler(window.cur_frm);
            }
        }
    });
}

// ==========================================
// Remove "Go to Home" Button
// ==========================================

function remove_go_to_home_button() {
    const selector = '.desk-home-button-wrapper';

    // 1. Immediate Removal
    $(selector).remove();

    // 2. Setup Observer for dynamic injection
    if (window._home_button_remover_installed) return;
    window._home_button_remover_installed = true;

    const observer = new MutationObserver((mutations) => {
        if ($(selector).length > 0) {
            $(selector).remove();
        }
    });

    observer.observe(document.body, { childList: true, subtree: true });
}

// ==========================================
// Sidebar Enforcement
// ==========================================

function enforce_sidebar_expanded() {
    // 1. Initial Cleanup (Immediate)
    check_and_expand_sidebar();

    // 2. Setup Observer (Once)
    if (window._sidebar_observer_installed) return;
    window._sidebar_observer_installed = true;

    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            if (mutation.attributeName === "class") {
                check_and_expand_sidebar();
            }
        });
    });

    observer.observe(document.body, { attributes: true });
}

function check_and_expand_sidebar() {
    // Only target desktop
    if (window.innerWidth >= 992) {
        if ($('body').hasClass('sidebar-collapsed')) {
             $('body').removeClass('sidebar-collapsed');
        }

        // Update runtime preference if available
        if(frappe && frappe.boot && frappe.boot.user && frappe.boot.user.sidebar_collapsed !== 0) {
            frappe.boot.user.sidebar_collapsed = 0;
        }
    }
}

// Handle resize events
$(window).on('resize', function() {
    check_and_expand_sidebar();
});

function save_draft_handler(frm) {
    // Basic validations
    if (!frm || !frm.doc || frm.is_new()) return;

    // Ignore if not dirty? The prompt says "When user finishes editing...".
    // Usually if I edit, it becomes dirty.
    // If I just click around, I don't want to save.
    // But 'change' event implies modification.
    // However, visibilitychange happens always.
    if (!frm.is_dirty()) return;

    // 1. Save to LocalStorage (Immediate)
    save_to_local_storage(frm);

    // 2. Sync to Server (Debounced)
    // Initialize debounce function for this form instance if needed
    if (!frm.save_draft_debounced) {
        frm.save_draft_debounced = frappe.utils.debounce((f) => {
            sync_draft_to_server(f);
        }, 2000);
    }
    frm.save_draft_debounced(frm);
}

function save_to_local_storage(frm) {
    const key = `draft::${frm.doc.doctype}::${frm.doc.name}`;
    try {
        localStorage.setItem(key, JSON.stringify(frm.doc));
    } catch (e) {
        console.warn("Auto-Save: Failed to save to localStorage", e);
    }
}

function sync_draft_to_server(frm) {
    // Double check state
    if (!frm || !frm.doc) return;

    frappe.call({
        method: "erpnext_enhancements.api.user_drafts.save_draft",
        args: {
            ref_doctype: frm.doc.doctype,
            ref_name: frm.doc.name,
            form_data: JSON.stringify(frm.doc)
        },
        callback: function(r) {
            // Optional: Update status indicator?
            // console.log("Draft synced");
        }
    });
}

function check_draft_on_load(frm) {
    const key = `draft::${frm.doc.doctype}::${frm.doc.name}`;
    const local_data_str = localStorage.getItem(key);

    if (local_data_str) {
        try {
            const local_doc = JSON.parse(local_data_str);

            // Compare to see if we really need to restore
            if (!are_docs_equal(frm.doc, local_doc)) {
                show_restore_alert(frm, local_doc);
            }
        } catch (e) {
            console.error("Auto-Save: Corrupt draft data", e);
        }
    }
}

function are_docs_equal(doc1, doc2) {
    // Simple comparison ignoring system fields
    const ignore = ['modified', 'creation', 'modified_by', 'owner', 'docstatus', 'idx', '__last_sync_on', '__unsaved', '__islocal'];

    const d1 = Object.assign({}, doc1);
    const d2 = Object.assign({}, doc2);

    ignore.forEach(k => {
        delete d1[k];
        delete d2[k];
    });

    // Also ignore keys starting with _ if they are UI specific?
    // Using simple JSON stringify comparison for now
    return JSON.stringify(d1) === JSON.stringify(d2);
}

function show_restore_alert(frm, local_doc) {
    if (!frm.dashboard || !frm.dashboard.wrapper) return;

    const msg = __("You have unsaved changes from a previous session.");

    // Check if alert already exists
    if (frm.dashboard.wrapper.find('.draft-restore-alert').length > 0) return;

    const alert_html = `
        <div class="alert alert-warning draft-restore-alert" style="display: flex; justify-content: space-between; align-items: center;">
            <div>
                <i class="fa fa-exclamation-triangle"></i> ${msg}
            </div>
            <div>
                <button class="btn btn-xs btn-primary btn-restore-draft">${__("Restore")}</button>
                <button class="btn btn-xs btn-default btn-discard-draft" style="margin-left: 5px;">${__("Discard")}</button>
            </div>
        </div>
    `;

    frm.dashboard.wrapper.prepend(alert_html);

    frm.dashboard.wrapper.find('.btn-restore-draft').on('click', function() {
        restore_draft(frm, local_doc);
    });

    frm.dashboard.wrapper.find('.btn-discard-draft').on('click', function() {
        discard_draft(frm);
    });
}

function restore_draft(frm, local_doc) {
    // Extend current doc with local data
    $.extend(frm.doc, local_doc);

    // Refresh fields to show new data
    frm.refresh_fields();

    // Mark as dirty so user knows they need to save
    frm.doc.__unsaved = 1;
    frm.trigger('save_expected'); // Updates indicator

    // Remove alert
    frm.dashboard.wrapper.find('.draft-restore-alert').remove();

    frappe.show_alert({message: __("Draft Restored"), indicator: 'green'});
}

function discard_draft(frm) {
    cleanup_draft(frm);
    frm.dashboard.wrapper.find('.draft-restore-alert').remove();
    frappe.show_alert({message: __("Draft Discarded"), indicator: 'orange'});
}

function cleanup_draft(frm) {
    if (!frm || !frm.doc) return;

    const key = `draft::${frm.doc.doctype}::${frm.doc.name}`;
    localStorage.removeItem(key);

    // Also delete from server
    frappe.call({
        method: "erpnext_enhancements.api.user_drafts.delete_draft",
        args: {
            ref_doctype: frm.doc.doctype,
            ref_name: frm.doc.name
        },
        callback: function() {
            // console.log("Server draft deleted");
        }
    });
}

// ==========================================
// Sidebar Enhancements (Attachment Manager)
// ==========================================
console.log("[ERPNext Enhancements] Loading Sidebar Enhancements...");

frappe.provide('erpnext_enhancements.sidebar');

(function() {
    const patch_sidebar = () => {
        if (frappe.ui && frappe.ui.form && frappe.ui.form.Sidebar && !frappe.ui.form.Sidebar.prototype._refresh_attachments_patched) {
            console.log("[ERPNext Enhancements] Patching frappe.ui.form.Sidebar.prototype.refresh_attachments");
            
            const original_refresh = frappe.ui.form.Sidebar.prototype.refresh_attachments;
            
            frappe.ui.form.Sidebar.prototype.refresh_attachments = function() {
                // Call original if we want to maintain some base logic, 
                // but since we are overriding the UI entirely, we'll implement our own.
                
                if (!this.frm || !this.frm.doc) return;

                let attachments = (this.frm.get_docinfo ? this.frm.get_docinfo().attachments : this.frm.doc._attachments) || [];
                
                // Find or create the attachments section
                let $section = this.sidebar.find('.sidebar-section[data-section="attachments"]');
                if (!$section.length) {
                    $section = this.sidebar.find('.attachments-actions').closest('.sidebar-section');
                }

                if (!$section.length) {
                    // If still not found, Frappe might have a different structure or it's not rendered yet
                    // We can try to call the original to let it create the section, then we override it
                    original_refresh.apply(this, arguments);
                    $section = this.sidebar.find('.sidebar-section[data-section="attachments"]');
                    if (!$section.length) return;
                }

                // Clear and Rebuild
                $section.empty();
                $section.html(`
                    <div class="sidebar-section-header">
                        <div class="sidebar-label">
                            <svg class="icon icon-sm"><use href="#icon-attachment"></use></svg>
                            ${__('Attachments')}
                        </div>
                    </div>
                    <div class="sidebar-items attachments-items"></div>
                    <div class="sidebar-actions mt-2">
                        <button class="btn btn-default btn-xs w-100" id="btn-open-file-manager" style="font-weight: 500; display: flex; align-items: center; justify-content: center;">
                            <span class="m-r-1">📂</span> ${__('Open File Manager')}
                        </button>
                    </div>
                `);

                let $items = $section.find('.attachments-items');
                let recent = attachments.slice().reverse().slice(0, 5);

                if (recent.length) {
                    recent.forEach(at => {
                        const file_name = at.file_name || at.name;
                        const file_url = at.file_url;
                        const size = at.file_size ? frappe.form.formatters.FileSize(at.file_size) : '';
                        const icon = frappe.utils.icon('attachment', 'sm') || '📎';

                        $items.append(`
                            <div class="sidebar-item" style="display: flex; align-items: center; justify-content: space-between; padding: 4px 0; font-size: var(--text-xs);">
                                <a href="${file_url}" target="_blank" class="text-muted ellipsis" style="display: flex; align-items: center; max-width: 75%; text-decoration: none;" title="${file_name}">
                                    <span class="m-r-1">${icon}</span>
                                    <span class="ellipsis">${file_name}</span>
                                </a>
                                <span class="text-muted" style="font-size: 10px; flex-shrink: 0;">${size}</span>
                            </div>
                        `);
                    });
                } else {
                    $items.append(`<div class="text-muted p-2 text-center" style="font-size: var(--text-xs);">${__('No attachments')}</div>`);
                }

                $section.find('#btn-open-file-manager').on('click', (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    erpnext_enhancements.sidebar.open_file_manager(this.frm);
                });
            };

            frappe.ui.form.Sidebar.prototype._refresh_attachments_patched = true;
        }
    };

    // Try patching immediately
    patch_sidebar();

    // Also hook into app_ready and page change to ensure it stays patched or catches late loads
    $(document).on('app_ready', patch_sidebar);
    $(document).on('page-change', patch_sidebar);
})();

erpnext_enhancements.sidebar.open_file_manager = function(frm) {
    console.log("[ERPNext Enhancements] Opening File Manager for", frm.doctype, frm.docname);
    
    if (!frm || !frm.doc) return;

    const dialog = new frappe.ui.Dialog({
        title: __('File Manager'),
        size: 'extra-large',
        fields: [
            {
                fieldname: 'vue_wrapper',
                fieldtype: 'HTML'
            }
        ]
    });

    dialog.show();

    if (typeof Vue === 'undefined') {
        dialog.fields_dict.vue_wrapper.$wrapper.html(`<div class="alert alert-danger">${__('Vue 3 is not available.')}</div>`);
        return;
    }

    const app = Vue.createApp({
        template: `
            <div class="file-manager-container" style="min-height: 500px; display: flex; flex-direction: column; font-family: var(--font-stack);">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid var(--border-color);">
                    <h4 class="m-0" style="font-weight: 600;">{{ doctype }}: <span class="text-muted">{{ docname }}</span></h4>
                    <div style="display: flex; gap: 10px;">
                        <button class="btn btn-primary btn-sm" @click="trigger_upload">
                            <i class="fa fa-upload m-r-1"></i> ${__('Upload')}
                        </button>
                        <button class="btn btn-default btn-sm" @click="fetch_files">
                            <i class="fa fa-refresh"></i>
                        </button>
                    </div>
                </div>

                <div v-if="loading" class="text-center" style="padding: 100px 0;">
                    <div class="spinner-border text-primary" role="status"></div>
                    <p class="mt-3 text-muted">${__('Fetching documents...')}</p>
                </div>

                <div v-else-if="files.length > 0" 
                     class="file-grid" 
                     style="display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 20px; overflow-y: auto; padding: 5px;">
                    <div v-for="file in files" :key="file.name" 
                         class="file-card shadow-sm border rounded" 
                         style="background: #fff; transition: transform 0.2s; display: flex; flex-direction: column; overflow: hidden;">
                        
                        <div class="preview-box" style="height: 140px; background: #f8f9fa; display: flex; align-items: center; justify-content: center; position: relative; border-bottom: 1px solid #eee;">
                            <img v-if="is_image(file.file_name)" :src="file.file_url" style="width: 100%; height: 100%; object-fit: cover;" />
                            <div v-else class="text-center">
                                <i :class="get_icon_class(file.file_name)" style="font-size: 3rem; color: #adb5bd;"></i>
                                <div class="text-uppercase font-weight-bold mt-2" style="font-size: 10px; color: #6c757d;">{{ get_extension(file.file_name) }}</div>
                            </div>
                        </div>

                        <div class="p-2" style="flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between;">
                            <div class="ellipsis font-weight-bold text-sm" :title="file.file_name">{{ file.file_name }}</div>
                            <div class="text-muted" style="font-size: 11px;">{{ format_size(file.file_size) }}</div>
                            
                            <div class="mt-2 d-flex" style="gap: 5px;">
                                <button class="btn btn-xs btn-default flex-fill" @click="download_file(file)">
                                    <i class="fa fa-download"></i>
                                </button>
                                <button class="btn btn-xs btn-danger flex-fill" @click="delete_file(file)">
                                    <i class="fa fa-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>

                <div v-else class="text-center text-muted" style="padding: 100px 0; border: 2px dashed #ddd; border-radius: 8px;">
                    <i class="fa fa-cloud-upload" style="font-size: 4rem; opacity: 0.2;"></i>
                    <h5 class="mt-3">${__('No files found')}</h5>
                    <p>${__('Drag and drop files anywhere in this window to upload.')}</p>
                </div>
            </div>
        `,
        data() {
            return {
                files: [],
                doctype: frm.doctype,
                docname: frm.docname,
                loading: false
            };
        },
        mounted() {
            this.fetch_files();
            this.setup_drag_drop();
        },
        methods: {
            fetch_files() {
                this.loading = true;
                frappe.call({
                    method: 'frappe.client.get_list',
                    args: {
                        doctype: 'File',
                        filters: {
                            attached_to_doctype: this.doctype,
                            attached_to_name: this.docname
                        },
                        fields: ['name', 'file_name', 'file_url', 'file_size'],
                        order_by: 'creation desc'
                    },
                    callback: (r) => {
                        this.files = r.message || [];
                        this.loading = false;
                    }
                });
            },
            is_image(filename) {
                const exts = ['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp'];
                return exts.includes(this.get_extension(filename));
            },
            get_extension(filename) {
                return filename ? filename.split('.').pop().toLowerCase() : '';
            },
            get_icon_class(filename) {
                const ext = this.get_extension(filename);
                if (ext === 'pdf') return 'fa fa-file-pdf-o';
                if (['doc', 'docx'].includes(ext)) return 'fa fa-file-word-o';
                if (['xls', 'xlsx', 'csv'].includes(ext)) return 'fa fa-file-excel-o';
                if (['zip', 'rar', '7z'].includes(ext)) return 'fa fa-file-archive-o';
                return 'fa fa-file-o';
            },
            format_size(size) {
                return frappe.form.formatters.FileSize(size);
            },
            download_file(file) {
                window.open(file.file_url, '_blank');
            },
            delete_file(file) {
                frappe.confirm(__('Delete this file?'), () => {
                    frappe.call({
                        method: 'frappe.client.delete',
                        args: { doctype: 'File', name: file.name },
                        callback: () => {
                            this.fetch_files();
                            frm.reload_docinfo();
                        }
                    });
                });
            },
            trigger_upload() {
                new frappe.ui.FileUploader({
                    doctype: this.doctype,
                    docname: this.docname,
                    on_success: () => {
                        this.fetch_files();
                        frm.reload_docinfo();
                    }
                });
            },
            setup_drag_drop() {
                const el = dialog.get_fields_dict().vue_wrapper.$wrapper[0].closest('.modal-content');
                el.addEventListener('dragover', (e) => { e.preventDefault(); el.style.boxShadow = '0 0 15px var(--primary-color)'; });
                el.addEventListener('dragleave', () => { el.style.boxShadow = ''; });
                el.addEventListener('drop', (e) => {
                    e.preventDefault();
                    el.style.boxShadow = '';
                    if (e.dataTransfer.files.length) {
                        new frappe.ui.FileUploader({
                            doctype: this.doctype,
                            docname: this.docname,
                            files: e.dataTransfer.files,
                            on_success: () => {
                                this.fetch_files();
                                frm.reload_docinfo();
                            }
                        });
                    }
                });
            }
        }
    });

    app.mount(dialog.fields_dict.vue_wrapper.$wrapper[0]);
    dialog.onhide = () => app.unmount();
};
