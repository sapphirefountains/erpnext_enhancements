/**
 * Traveler itinerary UI (vanilla JS, no frappe desk bundle).
 *
 * Targets: the chrome-free /itinerary web page (www/itinerary.html).
 * Loaded via: a raw <script> tag in itinerary.html carrying the
 *   ?v={{ deploy_version }} cache-bust token (raw /assets are 1-year
 *   immutable — kiosk convention).
 *
 * Boot payload (window.ITIN_BOOT, computed by www/itinerary.py) carries the
 * session employee and their active trips; the day-by-day detail is fetched
 * per trip from erpnext_enhancements.api.travel.get_trip_itinerary. The
 * server scopes everything to the session user — nothing here is trusted.
 *
 * Maps: Leaflet is lazy-loaded from frappe's bundled assets the first time
 * the user taps "Map" (same source as location_timeline.js); each day section
 * gets its own small map with that day's POI stops. "Open in Maps" deep links
 * are the no-tiles fallback.
 */
(function () {
	'use strict';

	var BOOT = window.ITIN_BOOT || {};
	var CSRF = window.ITIN_CSRF || BOOT.csrf_token || '';
	var root = document.getElementById('itinerary-root');

	var state = {
		trips: BOOT.trips || [],
		currentTrip: null,
		itinerary: null,
	};

	// -- API -----------------------------------------------------------------
	function api(method, args) {
		var headers = {
			'Accept': 'application/json',
			'Content-Type': 'application/json',
			'X-Frappe-CSRF-Token': CSRF,
		};
		return fetch('/api/method/' + method, {
			method: 'POST',
			headers: headers,
			credentials: 'same-origin',
			body: JSON.stringify(args || {}),
		}).then(function (res) {
			return res.json().catch(function () { return null; }).then(function (data) {
				if (!res.ok) {
					throw new Error((data && (data.exception || data._server_messages)) || ('HTTP ' + res.status));
				}
				return data ? data.message : null;
			});
		});
	}

	// -- Helpers ---------------------------------------------------------------
	function el(tag, className, text) {
		var node = document.createElement(tag);
		if (className) node.className = className;
		if (text != null) node.textContent = text;
		return node;
	}

	function fmtDate(iso) {
		var d = new Date(iso + 'T00:00:00');
		return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
	}

	function fmtTime(value) {
		if (!value) return '';
		// "HH:MM:SS" or "YYYY-MM-DD HH:MM:SS"
		var match = String(value).match(/(\d{2}):(\d{2})(?::\d{2})?$/);
		return match ? match[1] + ':' + match[2] : '';
	}

	function copyText(text, button) {
		var done = function () {
			var old = button.textContent;
			button.textContent = '✓ Copied';
			setTimeout(function () { button.textContent = old; }, 1500);
		};
		if (navigator.clipboard && navigator.clipboard.writeText) {
			navigator.clipboard.writeText(text).then(done, function () {});
		}
	}

	function mapsLink(lat, lng) {
		return 'https://maps.google.com/?q=' + lat + ',' + lng;
	}

	// -- Leaflet (lazy) --------------------------------------------------------
	var leafletPromise = null;
	function ensureLeaflet() {
		if (window.L) return Promise.resolve(window.L);
		if (leafletPromise) return leafletPromise;
		leafletPromise = new Promise(function (resolve, reject) {
			var css = document.createElement('link');
			css.rel = 'stylesheet';
			css.href = '/assets/frappe/js/lib/leaflet/leaflet.css';
			document.head.appendChild(css);
			var script = document.createElement('script');
			script.src = '/assets/frappe/js/lib/leaflet/leaflet.js';
			script.onload = function () {
				window.L ? resolve(window.L) : reject(new Error('Leaflet failed to load'));
			};
			script.onerror = function () { reject(new Error('Leaflet failed to load')); };
			document.head.appendChild(script);
		});
		return leafletPromise;
	}

	function renderDayMap(container, stops) {
		ensureLeaflet().then(function (L) {
			container.style.display = 'block';
			if (container._map) return;
			var map = L.map(container).setView([stops[0].lat, stops[0].lng], 12);
			container._map = map;
			L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
				maxZoom: 19,
				attribution: '© OpenStreetMap contributors',
			}).addTo(map);
			var bounds = [];
			stops.forEach(function (stop) {
				L.marker([stop.lat, stop.lng]).addTo(map)
					.bindPopup('<b>' + stop.label + '</b>' + (stop.sub ? '<br>' + stop.sub : '') +
						'<br><a href="' + mapsLink(stop.lat, stop.lng) + '" target="_blank" rel="noopener">Google Maps</a>');
				bounds.push([stop.lat, stop.lng]);
			});
			if (bounds.length > 1) map.fitBounds(bounds, { padding: [24, 24] });
		}).catch(function () {
			container.textContent = 'Map unavailable — use the Open in Maps links.';
		});
	}

	// -- Item cards ------------------------------------------------------------
	var RENDERERS = {
		flight: function (item) {
			var card = el('div', 'ti-card ti-flight');
			card.appendChild(el('div', 'ti-card-kicker', '✈ Flight · ' + (item.airline || '')));
			card.appendChild(el('div', 'ti-card-title',
				(item.flight_number || '') + '  ' +
				(item.departure_airport || '?') + ' → ' + (item.arrival_airport || '?')));
			var times = el('div', 'ti-card-sub');
			times.textContent = fmtTime(item.departure_time) +
				(item.arrival_time ? ' – ' + fmtTime(item.arrival_time) : '');
			card.appendChild(times);
			if (item.booking_reference) {
				var pnrRow = el('div', 'ti-pnr');
				pnrRow.appendChild(el('span', null, 'PNR: ' + item.booking_reference));
				var copy = el('button', 'ti-copy', 'Copy');
				copy.addEventListener('click', function () { copyText(item.booking_reference, copy); });
				pnrRow.appendChild(copy);
				card.appendChild(pnrRow);
			}
			appendAttachment(card, item.attachment);
			return card;
		},
		hotel_checkin: function (item) { return hotelCard(item, 'Check-in'); },
		hotel_checkout: function (item) { return hotelCard(item, 'Check-out'); },
		ground: function (item) {
			var card = el('div', 'ti-card ti-ground');
			card.appendChild(el('div', 'ti-card-kicker', '🚗 ' + (item.transport_type || 'Ground transport')));
			card.appendChild(el('div', 'ti-card-title',
				(item.pickup_location || '?') + ' → ' + (item.dropoff_location || '?')));
			var sub = [];
			if (item.provider) sub.push(item.provider);
			if (item.pickup_datetime) sub.push(fmtTime(item.pickup_datetime));
			if (item.booking_reference) sub.push('Ref: ' + item.booking_reference);
			if (sub.length) card.appendChild(el('div', 'ti-card-sub', sub.join(' · ')));
			appendAttachment(card, item.attachment);
			return card;
		},
		agenda: function (item) {
			var card = el('div', 'ti-card ti-agenda');
			var kicker = '📍 Stop' + (item.time ? ' · ' + fmtTime(item.time) : '');
			card.appendChild(el('div', 'ti-card-kicker', kicker));
			card.appendChild(el('div', 'ti-card-title', item.activity || ''));
			var sub = [];
			if (item.related_party) sub.push(item.related_party);
			if (item.poi) sub.push(item.poi.poi_name);
			if (sub.length) card.appendChild(el('div', 'ti-card-sub', sub.join(' · ')));
			if (item.visit_notes) card.appendChild(el('div', 'ti-notes', item.visit_notes));
			if (item.poi && item.poi.lat != null) {
				var link = el('a', 'ti-maps-link', 'Open in Maps ↗');
				link.href = mapsLink(item.poi.lat, item.poi.lng);
				link.target = '_blank';
				link.rel = 'noopener';
				card.appendChild(link);
			}
			return card;
		},
	};

	function hotelCard(item, kind) {
		var card = el('div', 'ti-card ti-hotel');
		card.appendChild(el('div', 'ti-card-kicker', '🏨 Hotel ' + kind));
		card.appendChild(el('div', 'ti-card-title', item.hotel || ''));
		var sub = [];
		if (item.address) sub.push(item.address);
		if (sub.length) card.appendChild(el('div', 'ti-card-sub', sub.join(' · ')));
		if (item.booking_confirmation) {
			var row = el('div', 'ti-pnr');
			row.appendChild(el('span', null, 'Confirmation: ' + item.booking_confirmation));
			var copy = el('button', 'ti-copy', 'Copy');
			copy.addEventListener('click', function () { copyText(item.booking_confirmation, copy); });
			row.appendChild(copy);
			card.appendChild(row);
		}
		appendAttachment(card, item.attachment);
		return card;
	}

	function appendAttachment(card, fileUrl) {
		if (!fileUrl) return;
		var link = el('a', 'ti-attachment', '📎 Attachment');
		link.href = fileUrl;
		link.target = '_blank';
		link.rel = 'noopener';
		card.appendChild(link);
	}

	// -- Rendering ---------------------------------------------------------------
	function render() {
		root.innerHTML = '';
		root.removeAttribute('aria-busy');

		var header = el('header', 'ti-header');
		header.appendChild(el('div', 'ti-header-title', 'My Itinerary'));
		if (BOOT.employee_name) header.appendChild(el('div', 'ti-header-sub', BOOT.employee_name));
		root.appendChild(header);

		if (!state.trips.length) {
			root.appendChild(el('div', 'ti-empty',
				BOOT.employee
					? 'No upcoming or recent trips. Safe travels when the next one comes!'
					: 'No employee record is linked to your user account.'));
			return;
		}

		if (state.trips.length > 1) {
			var switcher = el('div', 'ti-switcher');
			state.trips.forEach(function (trip) {
				var chip = el('button', 'ti-trip-chip' + (trip.name === state.currentTrip ? ' active' : ''));
				chip.appendChild(el('span', 'ti-chip-title', trip.purpose));
				chip.appendChild(el('span', 'ti-chip-sub', trip.start_date + ' → ' + trip.end_date));
				chip.addEventListener('click', function () { loadTrip(trip.name); });
				switcher.appendChild(chip);
			});
			root.appendChild(switcher);
		}

		if (!state.itinerary) {
			root.appendChild(el('div', 'ti-boot', 'Loading trip…'));
			return;
		}

		var trip = state.itinerary;
		var meta = el('div', 'ti-trip-meta');
		meta.appendChild(el('div', 'ti-trip-purpose', trip.purpose));
		var bits = [trip.status, trip.travel_type];
		if (trip.travel_for) bits.push('For: ' + trip.travel_for);
		meta.appendChild(el('div', 'ti-trip-sub', bits.filter(Boolean).join(' · ')));
		meta.appendChild(el('div', 'ti-trip-dates', fmtDate(trip.start_date) + ' – ' + fmtDate(trip.end_date)));
		root.appendChild(meta);

		if (!trip.days.length) {
			root.appendChild(el('div', 'ti-empty', 'Nothing scheduled yet — check back once bookings land.'));
			appendFooter();
			return;
		}

		var todayIso = new Date().toISOString().slice(0, 10);

		trip.days.forEach(function (day) {
			var section = el('section', 'ti-day' + (day.date === todayIso ? ' today' : ''));
			var heading = el('div', 'ti-day-heading');
			heading.appendChild(el('span', 'ti-day-date', fmtDate(day.date)));
			if (day.date === todayIso) heading.appendChild(el('span', 'ti-today-badge', 'Today'));

			var mappable = day.items
				.filter(function (i) { return i.type === 'agenda' && i.poi && i.poi.lat != null; })
				.map(function (i) {
					return { lat: i.poi.lat, lng: i.poi.lng, label: i.poi.poi_name, sub: i.activity };
				});
			var mapHolder = null;
			if (mappable.length) {
				mapHolder = el('div', 'ti-day-map');
				var mapBtn = el('button', 'ti-map-btn', '🗺 Map');
				mapBtn.addEventListener('click', function () { renderDayMap(mapHolder, mappable); });
				heading.appendChild(mapBtn);
			}
			section.appendChild(heading);

			day.items.forEach(function (item) {
				var renderer = RENDERERS[item.type];
				if (renderer) section.appendChild(renderer(item));
			});
			if (mapHolder) section.appendChild(mapHolder);
			root.appendChild(section);
		});

		appendFooter();
	}

	function appendFooter() {
		var footer = el('footer', 'ti-footer');
		var guidelines = el('a', 'ti-footer-link', 'Company travel guidelines');
		guidelines.href = '/travel_guidelines';
		footer.appendChild(guidelines);
		root.appendChild(footer);
	}

	function loadTrip(name) {
		state.currentTrip = name;
		state.itinerary = null;
		render();
		api('erpnext_enhancements.api.travel.get_trip_itinerary', { trip: name })
			.then(function (itinerary) {
				if (state.currentTrip !== name) return; // user switched again
				state.itinerary = itinerary;
				render();
			})
			.catch(function (err) {
				root.appendChild(el('div', 'ti-error', 'Could not load the trip: ' + err.message));
			});
	}

	// -- Boot --------------------------------------------------------------------
	if (state.trips.length) {
		// Prefer the trip happening now, else the next upcoming, else the first.
		var todayIso = new Date().toISOString().slice(0, 10);
		var current = state.trips.find(function (t) {
			return t.start_date <= todayIso && todayIso <= t.end_date;
		});
		var upcoming = state.trips.find(function (t) { return t.start_date >= todayIso; });
		loadTrip((current || upcoming || state.trips[0]).name);
	} else {
		render();
	}
})();
