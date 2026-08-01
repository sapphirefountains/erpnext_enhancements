/**
 * Address form customization — live map preview + address autocomplete.
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
 *  - Turns `address_line1` into a Google Places combobox, filling the rest of
 *    the address from the picked place, plus `custom_google_place_id` /
 *    `custom_latitude` / `custom_longitude` — which are cleared again the
 *    moment any component field is edited by hand, since coordinates that
 *    outlive the address they were picked for are worse than none. The widget
 *    itself is global — see public/js/global_enhancements/address_autocomplete.js
 *    — because the Address quick-entry dialog needs it too and no form script
 *    runs there.
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
		frm.trigger("setup_address_autocomplete");
	},

	setup_address_autocomplete: function (frm) {
		const places = window.erpnext_enhancements && erpnext_enhancements.address_autocomplete;
		const field = frm.fields_dict.address_line1;
		if (!places || !field || !field.$input) return;

		const input = field.$input.get(0);
		// There is ONE Form object — and one <input> — per doctype for the whole
		// page load; routing to another Address just refreshes it. Binding on
		// every refresh would stack listeners on the same node, so attach once
		// per input and only reset the search session for the new document.
		// (Compared by node, not a boolean: a layout rebuild would replace the
		// input while leaving a flag on `frm` looking satisfied.)
		if (frm.__ee_address_autocomplete) {
			if (frm.__ee_address_autocomplete.input === input) {
				frm.__ee_address_autocomplete.reset();
				return;
			}
			frm.__ee_address_autocomplete.destroy();
		}

		frm.__ee_address_autocomplete = places.attach(input, {
			get_country: function () {
				return frm.doc.country;
			},
			on_pick: function (values, meta) {
				// Google's own identity for the place, kept alongside the text:
				// the ID is the one field Google exempts from its no-caching
				// rule, and the coordinates save a billable geocode for every
				// map that would otherwise have to look this address up again.
				const fields = Object.assign({}, values, {
					custom_google_place_id: meta.place_id || "",
					custom_latitude: meta.latitude || 0,
					custom_longitude: meta.longitude || 0,
				});
				// One object call, not eight — but each field still fires its own
				// handler below, and every one of those rebuilds the map iframe.
				// Suspend the join for the duration and run it once when the whole
				// address has landed, or a single pick rebuilds the map five times.
				frm.__ee_suspend_full_address = true;
				const done = () => {
					frm.__ee_suspend_full_address = false;
					frm.trigger("update_full_address");
				};
				const applied = frm.set_value(fields);
				if (applied && applied.then) {
					applied.then(done, done);
				} else {
					done();
				}
			},
		});
	},
	address_line1: function (frm) {
		frm.trigger("address_component_changed");
	},
	address_line2: function (frm) {
		frm.trigger("address_component_changed");
	},
	city: function (frm) {
		frm.trigger("address_component_changed");
	},
	state: function (frm) {
		frm.trigger("address_component_changed");
	},
	country: function (frm) {
		frm.trigger("address_component_changed");
	},
	pincode: function (frm) {
		frm.trigger("address_component_changed");
	},

	address_component_changed: function (frm) {
		// Suspended only while an autocomplete pick is being applied — that is
		// the one caller whose writes must NOT count as a hand edit.
		if (frm.__ee_suspend_full_address) return;

		// A hand edit invalidates the picked place. A Place ID and coordinates
		// still pointing at the previously chosen building, while the visible
		// address says somewhere else, is worse than storing nothing: anything
		// downstream that trusts the coordinates over the text routes to the
		// wrong place, and nothing on screen would look wrong.
		if (frm.doc.custom_google_place_id || frm.doc.custom_latitude || frm.doc.custom_longitude) {
			frm.set_value({
				custom_google_place_id: "",
				custom_latitude: 0,
				custom_longitude: 0,
			});
		}

		frm.trigger("update_full_address");
	},

	update_full_address: function (frm) {
		// Held down while an autocomplete pick is being written field by field —
		// see setup_address_autocomplete, which releases it and calls this once.
		if (frm.__ee_suspend_full_address) return;

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
