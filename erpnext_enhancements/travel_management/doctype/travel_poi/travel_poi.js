/**
 * Travel POI form — "Open in Google Maps" button.
 *
 * Opens the POI's coordinates (first Point feature of the Geolocation field —
 * GeoJSON stores [lng, lat], note the swap, mirroring api/travel.py
 * _poi_latlng) in Google Maps; falls back to a text search on the linked
 * Address when no coordinates are set.
 */

function get_point_coords(geolocation_json) {
	if (!geolocation_json) return null;
	try {
		const collection = JSON.parse(geolocation_json);
		for (const feature of collection.features || []) {
			const geometry = feature.geometry || {};
			if (geometry.type === "Point") {
				const [lng, lat] = geometry.coordinates;
				return { lat, lng };
			}
		}
	} catch (e) {
		// Malformed geolocation — treat as unset
	}
	return null;
}

frappe.ui.form.on("Travel POI", {
	refresh(frm) {
		frm.add_custom_button(__("Open in Google Maps"), () => {
			const coords = get_point_coords(frm.doc.geolocation);
			if (coords) {
				window.open(`https://maps.google.com/?q=${coords.lat},${coords.lng}`, "_blank");
				return;
			}

			if (frm.doc.address) {
				frappe.db.get_doc("Address", frm.doc.address).then((addr) => {
					const parts = [
						addr.address_line1,
						addr.address_line2,
						addr.city,
						addr.state,
						addr.pincode,
						addr.country,
					].filter(Boolean);
					if (!parts.length) {
						frappe.msgprint(__("The linked Address has no location details to map."));
						return;
					}
					window.open(
						"https://maps.google.com/?q=" + encodeURIComponent(parts.join(", ")),
						"_blank"
					);
				});
				return;
			}

			frappe.msgprint(__("Set the Geolocation or link an Address first."));
		});
	},
});
