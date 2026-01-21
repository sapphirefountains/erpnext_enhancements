frappe.provide("frappe.search");

console.log("[ERPNext Enhancements] Script loaded");

$(document).on("app_ready", function () {
	console.log("[ERPNext Enhancements] app_ready");

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
	}

    // Setup Home Buttons
    try {
        setup_home_buttons();
    } catch (e) {
        console.error("Error setting up home buttons:", e);
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

    // 2. Desk Grid Button
    // Listen to route changes to re-inject if needed
    if (frappe.router) {
        frappe.router.on('change', () => {
            render_desk_home_button();
        });
    }

    // Initial check
    render_desk_home_button();
}

function render_desk_home_button() {
    // Only on Workspaces view (Desk) or if route is empty (root)
    const route = frappe.get_route();
    // Broad check for desk/workspace
    // In v13/v14, '/desk' usually redirects to '/app/workspace_name' or '/app/home'
    // But the user says they are at '/desk'

    // We will target the .layout-main-section which contains the workspace widgets

    setTimeout(() => {
        if ($('.desk-home-button-wrapper').length > 0) return;

        // Try to find the container.
        const $container = $('.layout-main-section');

        if ($container.length && $container.is(':visible')) {
             const btn_html = `
                <div class="desk-home-button-wrapper" style="margin-bottom: 20px; padding-left: 15px;">
                    <button class="btn btn-default" onclick="frappe.set_route('home')" style="display: inline-flex; align-items: center; gap: 8px; font-size: 14px; padding: 8px 16px; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
                        <svg class="icon icon-md" style="width: 16px; height: 16px;">
                            <use href="#icon-home"></use>
                        </svg>
                        Go to Home
                    </button>
                </div>
            `;
            $container.prepend(btn_html);
        }
    }, 1000); // Delay to ensure DOM is ready
}
