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

			frappe.call({
				method: "erpnext_enhancements.api.search.search_global_docs",
				args: { txt: txt },
				callback: function (r) {
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
});

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
