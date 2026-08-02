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
 *    `custom_latitude` / `custom_longitude`. The widget itself is global — see
 *    public/js/global_enhancements/address_autocomplete.js — because the Address
 *    quick-entry dialog needs it too and no form script runs there.
 *  - Lets the coordinates be typed directly, for a site the address cannot
 *    locate (new construction, a lot number, a stake in a field). That makes
 *    provenance matter: a point Google derived from the address text is thrown
 *    away when that text is edited, because coordinates outliving the address
 *    they were picked for are worse than none — but a typed point exists
 *    *because* the text cannot locate the site, so it survives. Which is which
 *    is recorded in `custom_location_source`, and the hand-edit detector is
 *    `df.onchange` rather than a field handler, since a field handler cannot
 *    tell a person typing from our own `set_value` during a pick.
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
		frm.trigger("setup_coordinate_fields");
	},

	/**
	 * Watch the two coordinate boxes for HAND edits.
	 *
	 * `df.onchange` rather than a `frappe.ui.form.on("Address", {custom_latitude})`
	 * handler, because those fire for programmatic writes too: every pick, and
	 * the clear above, would look exactly like somebody typing. onchange is
	 * reached only from the control's own validate-and-set path, so it means
	 * "a person edited this field" and nothing else — no latch needed.
	 *
	 * Re-planted on every refresh on purpose: the layout swaps `df` for a
	 * per-docname copy each time it renders, before the form script runs, so a
	 * handler planted once would be dropped the next time you open a record.
	 */
	setup_coordinate_fields: function (frm) {
		const places = window.erpnext_enhancements && erpnext_enhancements.address_autocomplete;
		if (!places) return;

		["custom_latitude", "custom_longitude"].forEach((fieldname) => {
			const field = frm.fields_dict[fieldname];
			if (!field) return;

			field.df.onchange = () => {
				// Typing over a picked point makes it yours: it must now survive
				// address edits, or the correction you just made is discarded by
				// the next keystroke in the city box.
				const point = places.usable_point(frm.doc.custom_latitude, frm.doc.custom_longitude);
				const source = point ? "Manual" : "";
				if (frm.doc.custom_location_source !== source) {
					frm.set_value("custom_location_source", source);
				}
				frm.trigger("render_map");
			};

			// See parse_point_paste: frappe eval()s Float input, so a pasted
			// "lat, lng" becomes a single plausible-but-wrong latitude. Intercept
			// it before the control sees it and fill both boxes instead.
			places.bind_point_paste(field, (point) => {
				frm.set_value({
					custom_latitude: point.lat,
					custom_longitude: point.lng,
					custom_location_source: "Manual",
				});
				frm.trigger("render_map");
			});
		});
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
				});
				const picked = places.usable_point(meta.latitude, meta.longitude);
				if (picked) {
					fields.custom_latitude = picked.lat;
					fields.custom_longitude = picked.lng;
					// An explicit pick outranks a typed point, and re-arms the
					// clear-on-edit rule for it.
					fields.custom_location_source = "Google";
				} else if (!places.point_survives_text_edit(frm.doc)) {
					// A Place can resolve without a location. Whatever point is
					// on the doc now belongs to the address being replaced, so it
					// has to go — otherwise picking a second address leaves the
					// first one's coordinates sitting under the new text, which
					// every map then prefers over that text.
					fields.custom_latitude = 0;
					fields.custom_longitude = 0;
					fields.custom_location_source = "";
				}
				// The remaining case — no point from the pick, but a Manual one
				// on the doc — deliberately writes nothing: it was typed because
				// the address could not locate the site, and picking a suggestion
				// for the text does not make it locatable.
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

		const places = window.erpnext_enhancements && erpnext_enhancements.address_autocomplete;
		const cleared = {};

		// The Place ID identifies the address TEXT, so a hand edit always
		// invalidates it, whoever the point belongs to.
		if (frm.doc.custom_google_place_id) cleared.custom_google_place_id = "";

		// The point only dies with the text if Google derived it from that text.
		// A Place ID and coordinates still pointing at the previously chosen
		// building, while the visible address says somewhere else, is worse than
		// storing nothing: anything downstream trusts the point over the text and
		// routes to the wrong place, with nothing on screen looking wrong.
		//
		// A typed point is the opposite case — it exists precisely because the
		// text cannot locate the site, so editing the text must not touch it.
		const survives = places && places.point_survives_text_edit(frm.doc);
		if (!survives && (frm.doc.custom_latitude || frm.doc.custom_longitude)) {
			cleared.custom_latitude = 0;
			cleared.custom_longitude = 0;
			cleared.custom_location_source = "";
		}

		if (Object.keys(cleared).length) frm.set_value(cleared);

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
		const places = window.erpnext_enhancements && erpnext_enhancements.address_autocomplete;
		// The point wins when there is one. For a site located by coordinates the
		// address text is by definition the thing that could not find it, so
		// embedding the text would show the wrong place — and typing a pair with
		// nothing moving on screen reads as "it didn't work". `z` frames it at
		// building scale, which the text form cannot ask for.
		const point =
			places && places.usable_point(frm.doc.custom_latitude, frm.doc.custom_longitude);
		const query = point
			? point.lat.toFixed(6) + "," + point.lng.toFixed(6)
			: frm.doc.custom_full_address;

		if (!query) {
			if (frm.fields_dict.custom_map_placeholder) {
				frm.fields_dict.custom_map_placeholder.$wrapper.html("");
			}
			return;
		}

		// `z` is its own query parameter, so it goes outside encodeURIComponent —
		// folded into the q= value it would arrive as a literal "&" in the search
		// text and Google would look for an address called "…&z=17".
		const zoom = point ? "&z=17" : "";
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
						query
					)}${zoom}&output=embed">
                </iframe>
            </div>
        `;

		if (frm.fields_dict.custom_map_placeholder) {
			frm.fields_dict.custom_map_placeholder.$wrapper.html(map_html);
		}
	},
});
