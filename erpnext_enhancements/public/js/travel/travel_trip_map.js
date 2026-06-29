/**
 * Travel Trip form map — agenda-stop POIs on a Google map.
 *
 * Targets: the "Travel Trip" doctype form (the `agenda_map_html` HTML field on
 *   the Itinerary tab, under the "Map" section).
 * Loaded via: hooks.py `doctype_js["Travel Trip"]` (alongside travel_trip.js).
 *
 * Data + key come from erpnext_enhancements.api.travel.get_trip_map_data:
 *   { api_key, pois }. Each POI carries coordinates and the agenda dates that
 *   visit it; api_key is the Google Maps **browser** key from Travel Settings
 *   (Maps JavaScript API, referrer-restricted) — client-side by design.
 *
 * Two things this file is careful about:
 *  - The map lives on a tab that is hidden at form load. Google Maps (like any
 *    map lib) renders blank when initialised in a 0×0 container, so map
 *    creation is deferred until the container is actually visible and sized
 *    (IntersectionObserver) — this is the fix for the "blank Map box" report.
 *  - Google's classic Marker has no full-text label, so each pin gets an
 *    always-visible name chip via a small OverlayView, plus a click InfoWindow.
 */
(function () {
	'use strict';

	// The Maps JS API can only be injected once per page; share one promise so
	// repeated form refreshes don't re-add the <script>.
	let mapsPromise = null;
	function ensureGoogleMaps(apiKey) {
		if (window.google && window.google.maps) return Promise.resolve(window.google.maps);
		if (mapsPromise) return mapsPromise;
		mapsPromise = new Promise((resolve, reject) => {
			const callbackName = '__ee_google_maps_ready';
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

	// Always-visible name chip anchored to a POI (classic markers can't render a
	// full label). Defined lazily because it extends google.maps.OverlayView,
	// which only exists once the API has loaded. Literal colours, not theme
	// vars: the chip sits on (always-light) Google tiles, so readability there
	// matters more than matching the desk theme.
	function makeLabelOverlay(maps) {
		class PoiLabel extends maps.OverlayView {
			constructor(position, text) {
				super();
				this._position = position;
				this._text = text;
				this._div = null;
			}
			onAdd() {
				const div = document.createElement('div');
				div.textContent = this._text;
				div.style.cssText =
					'position:absolute;transform:translate(-50%,-160%);white-space:nowrap;' +
					'background:#fff;color:#202124;border:1px solid rgba(0,0,0,.15);' +
					'border-radius:6px;padding:2px 6px;font-size:11px;font-weight:600;' +
					'line-height:1.3;box-shadow:0 1px 3px rgba(0,0,0,.3);pointer-events:none;';
				this._div = div;
				this.getPanes().floatPane.appendChild(div);
			}
			draw() {
				if (!this._div) return;
				const point = this.getProjection().fromLatLngToDivPixel(this._position);
				if (!point) return;
				this._div.style.left = point.x + 'px';
				this._div.style.top = point.y + 'px';
			}
			onRemove() {
				if (this._div) {
					this._div.remove();
					this._div = null;
				}
			}
		}
		return PoiLabel;
	}

	function popupHtml(poi) {
		const esc = frappe.utils.escape_html;
		const gmaps = 'https://maps.google.com/?q=' + poi.lat + ',' + poi.lng;
		const dates = (poi.agenda_dates || []).map(esc).join(', ');
		return (
			'<div style="min-width:160px">' +
			'<b>' + esc(poi.label) + '</b><br>' +
			(poi.category ? esc(poi.category) + '<br>' : '') +
			(dates ? dates + '<br>' : '') +
			'<a href="/app/travel-poi/' + encodeURIComponent(poi.poi) + '">' + __('Open POI') + '</a>' +
			' &middot; <a href="' + gmaps + '" target="_blank" rel="noopener">' + __('Directions') + '</a>' +
			'</div>'
		);
	}

	function drawMap(maps, container, pois) {
		const map = new maps.Map(container, {
			zoom: 12,
			center: { lat: pois[0].lat, lng: pois[0].lng },
			mapTypeControl: false,
			streetViewControl: false,
			fullscreenControl: true,
			gestureHandling: 'cooperative',
		});
		const PoiLabel = makeLabelOverlay(maps);
		const info = new maps.InfoWindow();
		const bounds = new maps.LatLngBounds();

		pois.forEach((poi) => {
			const position = { lat: poi.lat, lng: poi.lng };
			const marker = new maps.Marker({ position, map, title: poi.label });
			new PoiLabel(new maps.LatLng(poi.lat, poi.lng), poi.label).setMap(map);
			marker.addListener('click', () => {
				info.setContent(popupHtml(poi));
				info.open({ map, anchor: marker });
			});
			bounds.extend(position);
		});

		if (pois.length > 1) map.fitBounds(bounds, 48);
	}

	// No map (no key, or the API failed to load): keep the stops usable as a
	// list of Google Maps deep links rather than an empty box.
	function fallbackList($wrapper, pois, message) {
		const esc = frappe.utils.escape_html;
		const links = pois
			.map((poi) => {
				const gmaps = 'https://maps.google.com/?q=' + poi.lat + ',' + poi.lng;
				return (
					'<li><a href="' + gmaps + '" target="_blank" rel="noopener">' +
					esc(poi.label) + '</a></li>'
				);
			})
			.join('');
		$wrapper.html(
			'<div class="text-muted" style="padding:8px 0;">' + esc(message) + '</div>' +
			(links ? '<ul style="margin:0;padding-left:18px;">' + links + '</ul>' : '')
		);
	}

	function renderMap(frm, data) {
		const field = frm.get_field('agenda_map_html');
		if (!field || !field.$wrapper) return;
		const $wrapper = field.$wrapper;

		// A previous refresh may have left an observer watching a now-detached
		// container; stop it before rebuilding.
		if (field._mapObserver) {
			field._mapObserver.disconnect();
			field._mapObserver = null;
		}
		$wrapper.empty();

		const pois = (data && data.pois) || [];
		const apiKey = (data && data.api_key) || '';

		if (!pois.length) {
			$wrapper.html(
				'<div class="text-muted" style="padding:8px 0;">' +
				__('Link itinerary stops to Travel POIs with coordinates to see them here.') +
				'</div>'
			);
			return;
		}
		if (!apiKey) {
			fallbackList(
				$wrapper,
				pois,
				__('Add a Google Maps API key in Travel Settings to show the map.')
			);
			return;
		}

		const container = $(
			'<div class="travel-trip-map" style="height:360px;border-radius:8px;' +
			'border:1px solid var(--border-color);"></div>'
		).appendTo($wrapper)[0];

		let built = false;
		const build = () => {
			if (built) return;
			if (!container.offsetWidth || !container.offsetHeight) return; // still hidden tab
			built = true;
			if (field._mapObserver) {
				field._mapObserver.disconnect();
				field._mapObserver = null;
			}
			ensureGoogleMaps(apiKey)
				.then((maps) => drawMap(maps, container, pois))
				.catch(() =>
					fallbackList(
						$wrapper,
						pois,
						__('Map unavailable (Google Maps failed to load — check the API key).')
					)
				);
		};

		// The map sits on the Itinerary tab, hidden at load, so it has no size
		// until the tab is shown. Build it the moment the container is on-screen.
		if (window.IntersectionObserver) {
			field._mapObserver = new IntersectionObserver((entries) => {
				if (entries.some((e) => e.isIntersecting)) build();
			});
			field._mapObserver.observe(container);
		}
		build(); // already on the Itinerary tab (e.g. refresh after save)?
	}

	frappe.ui.form.on('Travel Trip', {
		refresh(frm) {
			if (frm.is_new()) return;
			frappe
				.call({
					method: 'erpnext_enhancements.api.travel.get_trip_map_data',
					args: { trip: frm.doc.name },
				})
				.then((r) => renderMap(frm, r.message || {}))
				.catch(() => {
					// Frappe shows its own error toast; leave the section with a
					// short note instead of a blank box.
					const field = frm.get_field('agenda_map_html');
					if (field && field.$wrapper) {
						field.$wrapper.html(
							'<div class="text-muted" style="padding:8px 0;">' +
							__('Could not load the map right now.') +
							'</div>'
						);
					}
				});
		},
	});
})();
