/**
 * Travel Trip form map — agenda-stop POIs on a Leaflet map.
 *
 * Targets: the "Travel Trip" doctype form (the `agenda_map_html` HTML field
 *   on the Itinerary tab).
 * Loaded via: hooks.py `doctype_js["Travel Trip"]` (alongside travel_trip.js).
 *
 * Renders on form refresh only (v1 — no live re-render while editing the
 * itinerary grid). Leaflet comes from frappe's bundled assets, the same
 * loader pattern as enhancements_core/page/location_timeline. POIs and
 * coordinates come from erpnext_enhancements.api.travel.get_trip_pois;
 * markers carry the agenda dates that visit each POI.
 */
(function () {
	'use strict';

	function ensureLeaflet() {
		return new Promise((resolve, reject) => {
			if (window.L) return resolve(window.L);
			frappe.require(
				['/assets/frappe/js/lib/leaflet/leaflet.css', '/assets/frappe/js/lib/leaflet/leaflet.js'],
				() => (window.L ? resolve(window.L) : reject(new Error('Leaflet failed to load')))
			);
		});
	}

	function renderMap(frm, pois) {
		const field = frm.get_field('agenda_map_html');
		if (!field || !field.$wrapper) return;
		const $wrapper = field.$wrapper;
		$wrapper.empty();

		if (!pois.length) {
			$wrapper.html(
				`<div class="text-muted" style="padding: 8px 0;">
					${__('Link itinerary stops to Travel POIs with coordinates to see them here.')}
				</div>`
			);
			return;
		}

		// Frappe CSS vars keep the chrome correct in Frappe Light + Timeless Night.
		const container = $(
			'<div class="travel-trip-map" style="height: 360px; border-radius: 8px; border: 1px solid var(--border-color);"></div>'
		).appendTo($wrapper)[0];

		ensureLeaflet()
			.then((L) => {
				const map = L.map(container).setView([pois[0].lat, pois[0].lng], 10);
				L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
					maxZoom: 19,
					attribution: '© OpenStreetMap contributors',
				}).addTo(map);

				const bounds = [];
				pois.forEach((poi) => {
					const gmaps = `https://maps.google.com/?q=${poi.lat},${poi.lng}`;
					const popup = `
						<b>${frappe.utils.escape_html(poi.label)}</b><br>
						${frappe.utils.escape_html(poi.category || '')}<br>
						${(poi.agenda_dates || []).map(frappe.utils.escape_html).join(', ')}<br>
						<a href="/app/travel-poi/${encodeURIComponent(poi.poi)}">${__('Open POI')}</a>
						&middot; <a href="${gmaps}" target="_blank" rel="noopener">${__('Google Maps')}</a>`;
					L.marker([poi.lat, poi.lng]).addTo(map).bindPopup(popup);
					bounds.push([poi.lat, poi.lng]);
				});
				if (bounds.length > 1) map.fitBounds(bounds, { padding: [24, 24] });
			})
			.catch(() => {
				$(container).replaceWith(
					`<div class="text-muted">${__('Map unavailable (Leaflet failed to load).')}</div>`
				);
			});
	}

	frappe.ui.form.on('Travel Trip', {
		refresh(frm) {
			if (frm.is_new()) return;
			frappe
				.call({
					method: 'erpnext_enhancements.api.travel.get_trip_pois',
					args: { trip: frm.doc.name },
				})
				.then((r) => renderMap(frm, r.message || []));
		},
	});
})();
