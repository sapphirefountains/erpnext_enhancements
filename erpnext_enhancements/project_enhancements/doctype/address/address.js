/**
 * Address form customization — live map preview.
 *
 * Customizes: the Address doctype form (loaded via `doctype_js["Address"]` in
 * hooks.py).
 *
 * Behavior:
 *  - Keeps the read-only `custom_full_address` Data field in sync by joining the
 *    individual address components (line1/line2/city/state/pincode/country)
 *    whenever any of them changes.
 *  - Renders a Google Maps embed of that address into the `custom_map_placeholder`
 *    HTML field, refreshing it whenever the full address changes.
 *
 * The two custom fields used here (`custom_full_address`, `custom_map_placeholder`)
 * are managed by the app fixtures (fixtures/custom_field.json), synced on
 * migrate. Note the server side also sets
 * `custom_full_address` on save via the `Address` `before_save` hook
 * (script_migrations.address.set_full_address); this script keeps the field and
 * the map live in the browser before a save round-trip.
 */
frappe.ui.form.on("Address", {
	after_save: function (frm) {
		// Push fresh directory data at every cached party form this address
		// links to (fixes the stale Contacts & Addresses section on route-back;
		// see contact_address_quick_entry.js).
		if (
			window.erpnext_enhancements &&
			erpnext_enhancements.contacts_ux &&
			erpnext_enhancements.contacts_ux.refresh_linked_sources
		) {
			erpnext_enhancements.contacts_ux.refresh_linked_sources(frm);
		}
	},

	refresh: function (frm) {
		// Render the map immediately if we already have a full address; otherwise
		// build the full address first (which then triggers the map render).
		if (frm.doc.custom_full_address) {
			frm.trigger("render_map");
		} else {
			frm.trigger("update_full_address");
		}
	},
	address_line1: function (frm) {
		frm.trigger("update_full_address");
	},
	address_line2: function (frm) {
		frm.trigger("update_full_address");
	},
	city: function (frm) {
		frm.trigger("update_full_address");
	},
	state: function (frm) {
		frm.trigger("update_full_address");
	},
	country: function (frm) {
		frm.trigger("update_full_address");
	},
	pincode: function (frm) {
		frm.trigger("update_full_address");
	},

	update_full_address: function (frm) {
		let parts = [
			frm.doc.address_line1,
			frm.doc.address_line2,
			frm.doc.city,
			frm.doc.state,
			frm.doc.pincode,
			frm.doc.country,
		];
		let full_address = parts.filter((p) => p).join(", ");

		if (frm.doc.custom_full_address !== full_address) {
			frm.set_value("custom_full_address", full_address);
			frm.trigger("render_map");
		}
	},

	custom_full_address: function (frm) {
		frm.trigger("render_map");
	},

	render_map: function (frm) {
		if (!frm.doc.custom_full_address) {
			if (frm.fields_dict.custom_map_placeholder) {
				frm.fields_dict.custom_map_placeholder.$wrapper.html("");
			}
			return;
		}

		const address = frm.doc.custom_full_address;
		const map_html = `
            <div class="map-wrapper" style="width: 100%; height: 400px; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 5px rgba(0,0,0,0.1);">
                <iframe
                    width="100%"
                    height="100%"
                    frameborder="0"
                    scrolling="no"
                    marginheight="0"
                    marginwidth="0"
                    src="https://maps.google.com/maps?q=${encodeURIComponent(
						address
					)}&output=embed">
                </iframe>
            </div>
        `;

		if (frm.fields_dict.custom_map_placeholder) {
			frm.fields_dict.custom_map_placeholder.$wrapper.html(map_html);
		}
	},
});
