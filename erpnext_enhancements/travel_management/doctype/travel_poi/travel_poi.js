/**
 * Travel POI form.
 *
 *  - Google Maps location picker in the `location_map` HTML field: click the
 *    map (or drag the pin) to set this POI's coordinates. The point is stored
 *    in the *hidden* native `geolocation` field as a GeoJSON FeatureCollection
 *    Point — the same shape api/travel.py `_poi_latlng` reads — so the trip
 *    agenda map and itinerary page keep working unchanged. The native
 *    Geolocation control (Leaflet/OpenStreetMap) is hidden in favour of this.
 *  - "Open in Google Maps" button (the POI's coordinates, else a text search on
 *    the linked Address).
 *
 * The key comes from Travel Settings via api.travel.get_maps_api_key. The Maps
 * loader here is self-contained (the trip agenda map carries its own) — the
 * page-level `window.google` singleton means whichever form loads first wins
 * and the other reuses it.
 */
(function () {
	'use strict';

	const DEFAULT_CENTER = { lat: 39.5, lng: -98.35 }; // continental US
	const DEFAULT_ZOOM = 4;
	const POINT_ZOOM = 15;

	let mapsPromise = null;
	function ensureGoogleMaps(apiKey) {
		if (window.google && window.google.maps) return Promise.resolve(window.google.maps);
		if (mapsPromise) return mapsPromise;
		if (!apiKey) return Promise.reject(new Error('Google Maps API key not set'));
		mapsPromise = new Promise((resolve, reject) => {
			const callbackName = '__eeGoogleMapsPoiReady';
			window[callbackName] = () => {
				delete window[callbackName];
				resolve(window.google.maps);
			};
			const script = document.createElement('script');
			script.src =
				'https://maps.googleapis.com/maps/api/js?key=' +
				encodeURIComponent(apiKey) +
				'&callback=' + callbackName +
				'&loading=async';
			script.async = true;
			script.onerror = () => {
				mapsPromise = null; // let a later refresh retry
				reject(new Error('Google Maps failed to load'));
			};
			document.head.appendChild(script);
		});
		return mapsPromise;
	}

	// GeoJSON FeatureCollection <-> {lat, lng}. GeoJSON Point coords are
	// [lng, lat] — note the swap, mirroring api/travel.py _poi_latlng.
	function pointFromGeolocation(geolocationJson) {
		if (!geolocationJson) return null;
		try {
			const collection = JSON.parse(geolocationJson);
			for (const feature of collection.features || []) {
				const geometry = feature.geometry || {};
				if (geometry.type === 'Point') {
					const [lng, lat] = geometry.coordinates;
					return { lat, lng };
				}
			}
		} catch (e) {
			// malformed → treat as unset
		}
		return null;
	}

	function geolocationFromPoint(lat, lng) {
		return JSON.stringify({
			type: 'FeatureCollection',
			features: [
				{ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: [lng, lat] } },
			],
		});
	}

	function renderPicker(frm, apiKey) {
		const field = frm.get_field('location_map');
		if (!field || !field.$wrapper) return;
		const $wrapper = field.$wrapper;

		if (field._mapObserver) {
			field._mapObserver.disconnect();
			field._mapObserver = null;
		}
		$wrapper.empty();

		if (!apiKey) {
			$wrapper.html(
				'<div class="text-muted" style="padding:8px 0;">' +
				__('Add a Google Maps API key in Travel Settings to pick a location on the map.') +
				'</div>'
			);
			return;
		}

		const editable = !!(frm.perm[0] && frm.perm[0].write);

		const $hint = $('<div class="text-muted" style="margin-bottom:6px;font-size:12px;"></div>');
		const $coords = $('<span></span>');
		$hint.text(editable ? __('Click the map or drag the pin to set this location. ') : __('Location: '));
		$hint.append($coords);
		$wrapper.append($hint);

		const container = $(
			'<div style="height:340px;border-radius:8px;border:1px solid var(--border-color);"></div>'
		).appendTo($wrapper)[0];

		const existing = pointFromGeolocation(frm.doc.geolocation);
		const showCoords = (lat, lng) => $coords.text(lat.toFixed(6) + ', ' + lng.toFixed(6));
		if (existing) showCoords(existing.lat, existing.lng);

		let built = false;
		const build = () => {
			if (built) return;
			if (!container.offsetWidth || !container.offsetHeight) return; // not visible/sized yet
			built = true;
			if (field._mapObserver) {
				field._mapObserver.disconnect();
				field._mapObserver = null;
			}
			ensureGoogleMaps(apiKey)
				.then((maps) => {
					const map = new maps.Map(container, {
						center: existing || DEFAULT_CENTER,
						zoom: existing ? POINT_ZOOM : DEFAULT_ZOOM,
						mapTypeControl: false,
						streetViewControl: false,
						fullscreenControl: true,
						gestureHandling: 'cooperative',
					});
					let marker = null;
					const place = (lat, lng) => {
						if (marker) {
							marker.setPosition({ lat, lng });
						} else {
							marker = new maps.Marker({ position: { lat, lng }, map, draggable: editable });
							if (editable) {
								marker.addListener('dragend', (e) =>
									commit(e.latLng.lat(), e.latLng.lng())
								);
							}
						}
						showCoords(lat, lng);
					};
					const commit = (lat, lng) => {
						place(lat, lng);
						// Write straight to the model + mark dirty rather than
						// frm.set_value: the geolocation field is the hidden native
						// control, and set_value can nudge it to render its Leaflet
						// (OpenStreetMap) map — the very thing we're replacing.
						frm.doc.geolocation = geolocationFromPoint(lat, lng);
						frm.dirty();
					};
					if (existing) place(existing.lat, existing.lng);
					if (editable) {
						map.addListener('click', (e) => commit(e.latLng.lat(), e.latLng.lng()));
					}
				})
				.catch(() => {
					$(container).replaceWith(
						'<div class="text-muted">' +
						__('Map unavailable (Google Maps failed to load — check the API key).') +
						'</div>'
					);
				});
		};

		if (window.IntersectionObserver) {
			field._mapObserver = new IntersectionObserver((entries) => {
				if (entries.some((e) => e.isIntersecting)) build();
			});
			field._mapObserver.observe(container);
		}
		build();
	}

	function openInGoogleMaps(frm) {
		const coords = pointFromGeolocation(frm.doc.geolocation);
		if (coords) {
			window.open('https://maps.google.com/?q=' + coords.lat + ',' + coords.lng, '_blank');
			return;
		}
		if (frm.doc.address) {
			frappe.db.get_doc('Address', frm.doc.address).then((addr) => {
				const parts = [
					addr.address_line1,
					addr.address_line2,
					addr.city,
					addr.state,
					addr.pincode,
					addr.country,
				].filter(Boolean);
				if (!parts.length) {
					frappe.msgprint(__('The linked Address has no location details to map.'));
					return;
				}
				window.open(
					'https://maps.google.com/?q=' + encodeURIComponent(parts.join(', ')),
					'_blank'
				);
			});
			return;
		}
		frappe.msgprint(__('Set the location on the map or link an Address first.'));
	}

	frappe.ui.form.on('Travel POI', {
		refresh(frm) {
			frm.add_custom_button(__('Open in Google Maps'), () => openInGoogleMaps(frm));
			frappe
				.call({ method: 'erpnext_enhancements.api.travel.get_maps_api_key' })
				.then((r) => renderPicker(frm, (r && r.message) || ''));
		},
	});
})();
