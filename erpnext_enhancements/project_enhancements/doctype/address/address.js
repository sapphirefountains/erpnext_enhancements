frappe.ui.form.on("Address", {
	refresh: function (frm) {
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
