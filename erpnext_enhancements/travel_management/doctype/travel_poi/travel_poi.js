/**
 * Travel POI form.
 *
 *  - Google Maps location picker in the `location_map` HTML field. The map is
 *    centred on the POI's point if one is set; otherwise it falls back to the
 *    linked **Address** — using the exact point stored when that address was
 *    picked from the Places autocomplete (v1.205.0) if it has one, and only
 *    geocoding the address text when it does not. So a POI located purely by
 *    its address still appears, and increasingly without a billable geocode.
 *    Click the map or drag the pin to set an exact
 *    point, or press "Locate from linked address" to (re)place it from the
 *    address. The chosen point is stored in the *hidden* native `geolocation`
 *    field as a GeoJSON Point — the shape api/travel.py `_poi_latlng` reads — so
 *    the trip agenda map and /itinerary page consume it unchanged. The native
 *    Geolocation control (Leaflet/OpenStreetMap) is hidden in favour of this.
 *  - "Open in Google Maps" button (the POI's coordinates, else its Address).
 *
 * Auto-geocoding from the address is *display only* — it does not dirty the
 * form. Persisting coordinates happens when the user sets a point explicitly,
 * or automatically when the POI is rendered on the trip agenda map (which
 * caches the geocode via api.travel.cache_poi_geocode). Geocoding needs the
 * Geocoding API enabled on the Maps key.
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

	// The POI's linked Address as {text, lat, lng}; any part may be null.
	//
	// lat/lng are the point the Places autocomplete stored when the address was
	// picked (v1.205.0) — exact, and free, where geocoding the text is neither.
	// 0 means absent, not Null Island: the columns are NOT NULL DEFAULT 0 and a
	// hand edit blanks them back to a literal 0.
	function addressLocation(frm) {
		if (!frm.doc.address) return Promise.resolve({ text: null, lat: null, lng: null });

		const BASE = ['address_line1', 'address_line2', 'city', 'state', 'pincode', 'country'];
		const POINT = ['custom_latitude', 'custom_longitude'];

		const shape = (r) => {
			const a = (r && r.message) || {};
			const parts = [
				a.address_line1,
				a.address_line2,
				a.city,
				a.state,
				a.pincode,
				a.country,
			].filter(Boolean);
			const lat = a.custom_latitude || null;
			const lng = a.custom_longitude || null;
			return {
				text: parts.length ? parts.join(', ') : null,
				lat: lat && lng ? lat : null,
				lng: lat && lng ? lng : null,
			};
		};

		return frappe.db.get_value('Address', frm.doc.address, BASE.concat(POINT)).then(
			shape,
			// The point fields arrive with `bench migrate`, and main auto-deploys —
			// so this code can be live on a site that does not have them yet. The
			// server half guards with frappe.db.has_column; the client cannot, and
			// asking for a field that is not in meta is a hard rejection ("Field
			// not permitted in query"), not a null. Without this retry the whole
			// picker dies: every consumer of this function lives inside .then, so
			// the map would never render and even the "link an Address first"
			// message would never fire. Falling back to the six fields that have
			// always existed leaves the form geocoding exactly as it did before.
			() => frappe.db.get_value('Address', frm.doc.address, BASE).then(shape)
		);
	}

	function ensureKey(frm) {
		if (frm._mapsApiKey !== undefined) return Promise.resolve(frm._mapsApiKey);
		return frappe
			.call({ method: 'erpnext_enhancements.api.travel.get_maps_api_key' })
			.then((r) => {
				frm._mapsApiKey = (r && r.message) || '';
				return frm._mapsApiKey;
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

		let $relocate = null;
		if (editable) {
			$relocate = $(
				'<button class="btn btn-xs btn-default" style="margin-bottom:6px;">' +
				__('Locate from linked address') +
				'</button>'
			).appendTo($wrapper);
		}

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
								marker.addListener('dragend', (e) => commit(e.latLng.lat(), e.latLng.lng()));
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
					// Geocode the linked address. `persist` commits (explicit user
					// action); otherwise it only displays, so opening a POI never
					// dirties the form.
					const locateFromAddress = (persist) => {
						addressLocation(frm).then((addr) => {
							// The Address was picked from the autocomplete, so its
							// exact point is already known — no Geocoder call, no
							// bill, and the pin lands on the building rather than
							// on Google's reading of the text. Routed through the
							// same place()/commit() pair, so the display-only
							// contract above still holds for the auto-run.
							if (addr.lat && addr.lng) {
								map.setCenter({ lat: addr.lat, lng: addr.lng });
								map.setZoom(POINT_ZOOM);
								persist ? commit(addr.lat, addr.lng) : place(addr.lat, addr.lng);
								return;
							}
							if (!addr.text) {
								if (persist) frappe.msgprint(__('The linked Address has no details to locate.'));
								return;
							}
							new maps.Geocoder().geocode({ address: addr.text }, (results, status) => {
								if (status === 'OK' && results && results[0]) {
									const loc = results[0].geometry.location;
									const lat = loc.lat();
									const lng = loc.lng();
									map.setCenter({ lat, lng });
									map.setZoom(POINT_ZOOM);
									persist ? commit(lat, lng) : place(lat, lng);
								} else if (persist) {
									frappe.msgprint(
										__('Could not locate that address. Is the Geocoding API enabled on the Maps key?')
									);
								}
							});
						});
					};

					if (existing) {
						place(existing.lat, existing.lng);
					} else if (frm.doc.address) {
						locateFromAddress(false); // show the address location (no dirty)
					}
					if (editable) {
						map.addListener('click', (e) => commit(e.latLng.lat(), e.latLng.lng()));
						if ($relocate) $relocate.on('click', () => locateFromAddress(true));
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
		addressLocation(frm).then((addr) => {
			// The Address's own picked point beats its text: Maps opens on the
			// building instead of re-resolving the string.
			if (addr.lat && addr.lng) {
				window.open('https://maps.google.com/?q=' + addr.lat + ',' + addr.lng, '_blank');
				return;
			}
			if (!addr.text) {
				frappe.msgprint(__('Set the location on the map or link an Address first.'));
				return;
			}
			window.open('https://maps.google.com/?q=' + encodeURIComponent(addr.text), '_blank');
		});
	}

	frappe.ui.form.on('Travel POI', {
		refresh(frm) {
			frm.add_custom_button(__('Open in Google Maps'), () => openInGoogleMaps(frm));
			ensureKey(frm).then((key) => renderPicker(frm, key));
		},
		address(frm) {
			// Address changed: re-render so the map reflects the new address (it
			// auto-shows the address location when no point is set; an existing
			// point is kept — use "Locate from linked address" to override it).
			ensureKey(frm).then((key) => renderPicker(frm, key));
		},
	});
})();
