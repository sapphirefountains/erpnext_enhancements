/**
 * Traveler itinerary UI (vanilla JS, no frappe desk bundle).
 *
 * Targets: the chrome-free /itinerary web page (www/itinerary.html).
 * Loaded via: a raw <script> tag in itinerary.html carrying the
 *   ?v={{ deploy_version }} cache-bust token (raw /assets are 1-year
 *   immutable — kiosk convention).
 *
 * Boot payload (window.ITIN_BOOT, computed by www/itinerary.py) carries the
 * session employee and their trips: the ones they travel on, plus the ones they
 * own but are not on (`mine: false`). The day-by-day detail is fetched per trip
 * from erpnext_enhancements.api.travel.get_trip_itinerary. The server scopes
 * everything to the session user — nothing here is trusted: a ?trip= that is not
 * in the boot list is still asked for, and the server's read permission decides
 * (crew, the trip's owner and travel coordinators can all open it).
 *
 * Whose itinerary: ?as=<employee> shows one person's view (their bookings, their
 * own confirmation numbers — exactly what their own /itinerary shows), ?as=crew
 * the whole crew, and no ?as= the default (you, when you are on the trip, else
 * the whole crew). The picker under the trip lists "Me", "Whole crew" and each
 * person; the server sends that crew with every answer and refuses someone who
 * is not on the trip.
 *
 * Maps: Leaflet is lazy-loaded from frappe's bundled assets the first time
 * the user taps "Map" (same source as location_timeline.js); each day section
 * gets its own small map with that day's POI stops. "Open in Maps" deep links
 * are the no-tiles fallback.
 *
 * Back / Forward: what is on screen is in the address as ?trip=<name>&as=<who>
 * (same path, so a reload, Back from /travel_guidelines and the login redirect all
 * keep it). A trip chip tap or a person pick pushes one entry, from the tap itself
 * (a trip chip drops ?as=: another trip opens on its default view); Back and Forward
 * arrive as popstate and load that entry's trip and person without pushing. The
 * entry the page opened on is replaced only when ?trip= is missing, or when the
 * server refuses it (someone else's trip, a deleted one, a person no longer on it),
 * never pushed, so Back from it leaves the page. A refused entry the page pushed
 * itself is stepped back off instead when its fallback is the entry behind it, so no
 * two entries in a row are the same (see loadFailed). Signed out since the page
 * loaded is not a refusal: the address stays, and the page offers to sign in again.
 * "Report a problem" (capture/panel.js) owns its own entry: popstate is left to it
 * while window.ee_capture.isOpen(), and so is every history write.
 */
(function () {
	'use strict';

	var BOOT = window.ITIN_BOOT || {};
	var CSRF = window.ITIN_CSRF || BOOT.csrf_token || '';
	var root = document.getElementById('itinerary-root');

	var state = {
		trips: BOOT.trips || [],
		currentTrip: null,
		// '' = the server's default view, 'crew' = the whole crew, else an employee id.
		currentAs: '',
		itinerary: null,
		// The server refused the trip on screen and there is no trip of their own to fall back on.
		denied: false,
		// The session ended since the page loaded (expired, or signed out in another tab).
		signedOut: false,
		// The last crew the server sent: {trip, crew, viewer, onTrip}. Keeps the person picker
		// on screen while another person's view loads.
		people: null,
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
					// `status` tells a refusal (the server answered: 403, 404, 417) from no answer.
					var err = new Error(serverMessage(data) || ('HTTP ' + res.status));
					err.status = res.status;
					// Signed out since the page loaded: frappe carries on as Guest, and Guest may
					// not call this method, so the answer is the same 403 that someone else's
					// trip gets. An expired session says so in the answer (`session_expired`); a
					// session ended in another tab leaves the browser's `user_id` cookie at Guest,
					// or gone.
					err.signedOut = !!(data && data.session_expired) || (res.status === 403 && signedOutCookie());
					throw err;
				}
				return data ? data.message : null;
			});
		});
	}

	// True when this browser no longer holds a signed-in session: frappe keeps the user in a
	// readable `user_id` cookie, and sets it to Guest or clears it when the session ends. A
	// document with no cookies to read (not a browser) proves nothing, so it counts as signed in.
	function signedOutCookie() {
		try {
			if (typeof document.cookie !== 'string') return false;
			var match = document.cookie.match(/(?:^|;\s*)user_id=([^;]*)/);
			return !match || !match[1] || decodeURIComponent(match[1]) === 'Guest';
		} catch (e) {
			return false;
		}
	}

	// Back to exactly this address after signing in: /login?redirect-to=, encoded as
	// www/itinerary.py's login_redirect encodes it (everything but "/").
	function signInUrl() {
		var here = window.location.pathname + window.location.search;
		return '/login?redirect-to=' + encodeURIComponent(here).replace(/%2F/g, '/');
	}

	// The first message frappe queued for the user (frappe.throw), as plain text.
	function serverMessage(data) {
		if (!data) return '';
		try {
			var messages = JSON.parse(data._server_messages || '[]');
			for (var i = 0; i < messages.length; i++) {
				var message = JSON.parse(messages[i]);
				if (message && message.message) return String(message.message).replace(/<[^>]*>/g, '');
			}
		} catch (e) {
			// Not the shape frappe sends: fall back to the exception line.
		}
		return data.exception ? String(data.exception).replace(/^[\w.]+:\s*/, '') : '';
	}

	// -- Helpers ---------------------------------------------------------------
	function el(tag, className, text) {
		var node = document.createElement(tag);
		if (className) node.className = className;
		if (text != null) node.textContent = text;
		return node;
	}

	// Today on this phone's calendar, "YYYY-MM-DD". Not toISOString(), which is UTC: in
	// Arizona that turned into tomorrow at 5 PM, and the Today badge moved to the next day.
	function localIso(date) {
		var d = date || new Date();
		var month = d.getMonth() + 1;
		var day = d.getDate();
		return d.getFullYear() + '-' + (month < 10 ? '0' : '') + month + '-' + (day < 10 ? '0' : '') + day;
	}

	function fmtDate(iso) {
		var d = new Date(iso + 'T00:00:00');
		return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
	}

	function fmtTime(value) {
		if (!value) return '';
		// "HH:MM:SS" or "YYYY-MM-DD HH:MM:SS" -> "2:30 PM". Midnight is "no time given":
		// Plan a Trip stores a flight or drive whose time is not known yet at 00:00:00.
		var match = String(value).match(/(\d{1,2}):(\d{2})(?::\d{2})?$/);
		if (!match) return '';
		var hour = parseInt(match[1], 10);
		if (hour === 0 && match[2] === '00' && String(value).length > 8) return '';
		return (hour % 12 || 12) + ':' + match[2] + ' ' + (hour < 12 ? 'AM' : 'PM');
	}

	function fmtRange(from, to) {
		var a = fmtTime(from);
		var b = fmtTime(to);
		if (a && b) return a + ' – ' + b;
		return a || b;
	}

	// A PNR / confirmation / tracking number, with a Copy button — the thing a traveler
	// reads out at a counter.
	function appendRef(card, label, value) {
		if (!value) return;
		var row = el('div', 'ti-pnr');
		row.appendChild(el('span', null, label + ': ' + value));
		row.appendChild(copyButton(value));
		card.appendChild(row);
	}

	function copyButton(value) {
		var copy = el('button', 'ti-copy', 'Copy');
		copy.addEventListener('click', function () { copyText(value, copy); });
		return copy;
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

	// A marker's popup, built as elements: the place name and the stop's activity are typed
	// in by people, so they are text, never markup.
	function stopPopup(stop) {
		var box = el('div', 'ti-popup');
		box.appendChild(el('b', null, stop.label || ''));
		if (stop.sub) box.appendChild(el('div', null, stop.sub));
		var link = el('a', null, 'Google Maps');
		link.href = mapsLink(stop.lat, stop.lng);
		link.target = '_blank';
		link.rel = 'noopener';
		box.appendChild(link);
		return box;
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
				L.marker([stop.lat, stop.lng]).addTo(map).bindPopup(stopPopup(stop));
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
			appendWhoAndRefs(card, item, 'PNR', item.booking_reference);
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
			var when = fmtRange(item.pickup_datetime, item.arrival_datetime);
			if (when) sub.push(when);
			if (sub.length) card.appendChild(el('div', 'ti-card-sub', sub.join(' · ')));
			if (item.return_datetime && fmtTime(item.return_datetime)) {
				card.appendChild(el('div', 'ti-card-sub', 'Return by ' + fmtTime(item.return_datetime) +
					' (' + String(item.return_datetime).slice(0, 10) + ')'));
			}
			if (item.cargo) card.appendChild(el('div', 'ti-notes', 'Hauling: ' + item.cargo));
			appendWhoAndRefs(card, item, 'Confirmation', item.booking_reference);
			appendAttachment(card, item.attachment);
			return card;
		},
		freight: function (item) {
			var card = el('div', 'ti-card ti-ground');
			card.appendChild(el('div', 'ti-card-kicker', '📦 Freight · ' + (item.carrier || '')));
			card.appendChild(el('div', 'ti-card-title', item.contents || 'Shipment'));
			var delivery = fmtRange(item.delivery_from, item.delivery_to);
			if (item.deliver_to || delivery) {
				card.appendChild(el('div', 'ti-card-sub',
					'Delivers' + (delivery ? ' ' + delivery : '') + (item.deliver_to ? ' to ' + item.deliver_to : '')));
			}
			var pickup = fmtRange(item.pickup_from, item.pickup_to);
			if (item.ship_from || pickup) {
				card.appendChild(el('div', 'ti-card-sub',
					'Picked up' + (pickup ? ' ' + pickup : '') +
					(item.pickup_from ? ' on ' + String(item.pickup_from).slice(0, 10) : '') +
					(item.ship_from ? ' from ' + item.ship_from : '')));
			}
			if (item.received_by) card.appendChild(el('div', 'ti-card-sub', 'Received by ' + item.received_by));
			appendRef(card, 'Tracking', item.tracking_number);
			appendAttachment(card, item.attachment);
			return card;
		},
		agenda: function (item) {
			var card = el('div', 'ti-card ti-agenda');
			var span = fmtRange(item.time, item.end_time);
			var kicker = '📍 Stop' + (span ? ' · ' + span : '');
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
		card.appendChild(el('div', 'ti-card-kicker', '🏨 Hotel ' + kind + (item.time ? ' · ' + fmtTime(item.time) : '')));
		card.appendChild(el('div', 'ti-card-title', item.hotel || ''));
		var sub = [];
		if (item.address) sub.push(item.address);
		if (sub.length) card.appendChild(el('div', 'ti-card-sub', sub.join(' · ')));
		appendWhoAndRefs(card, item, 'Confirmation', item.booking_confirmation);
		appendAttachment(card, item.attachment);
		return card;
	}

	// Who is on a booking, and the confirmation number to read out.
	//
	// One person's view (their own, or "view as" someone) has one row per booking: that
	// person's own number, and `travelers` null. The whole-crew view collapses a booking's
	// per-person rows into one card (api/travel.py shape_itinerary): `members` then names each
	// person with their OWN number ('' employee = a row for the whole crew), so a shared flight
	// shows who holds which PNR. A payload without `members` falls back to the names and the
	// numbers joined, as before.
	function appendWhoAndRefs(card, item, label, ref) {
		if (!item.members || !item.members.length) {
			appendWho(card, item);
			appendRef(card, label, ref);
			return;
		}
		var list = el('div', 'ti-members');
		item.members.forEach(function (member) {
			var row = el('div', 'ti-member');
			row.appendChild(el('span', 'ti-member-name',
				member.employee ? (member.employee_name || member.employee) : 'Everyone'));
			if (member.ref) {
				row.appendChild(el('span', 'ti-member-ref', label + ': ' + member.ref));
				row.appendChild(copyButton(member.ref));
			}
			list.appendChild(row);
		});
		card.appendChild(list);
	}

	function appendWho(card, item) {
		if (!item.travelers || !item.travelers.length) return;
		card.appendChild(el('div', 'ti-card-sub', 'With: ' + item.travelers.join(', ')));
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

		var view = viewTitle();
		var header = el('header', 'ti-header');
		header.appendChild(el('div', 'ti-header-title', view.title));
		if (view.sub) header.appendChild(el('div', 'ti-header-sub', view.sub));
		root.appendChild(header);
		setDocumentTitle(view.page);

		if (state.signedOut) {
			var expired = el('div', 'ti-empty', 'Your session has expired. ');
			var signIn = el('a', 'ti-signin', 'Sign in again');
			signIn.href = signInUrl();
			expired.appendChild(signIn);
			root.appendChild(expired);
			return;
		}

		if (!state.currentTrip) {
			root.appendChild(el('div', 'ti-empty',
				BOOT.employee
					? 'No upcoming or recent trips. Safe travels when the next one comes!'
					: 'No employee record is linked to your user account.'));
			return;
		}

		// A trip opened from a link that is not in the list still gets the list, to go back to.
		if (state.trips.length > 1 || (state.trips.length && !listedTrip(state.currentTrip))) {
			var someMine = state.trips.some(function (t) { return t.mine !== false; });
			var switcher = el('div', 'ti-switcher');
			state.trips.forEach(function (trip) {
				var chip = el('button', 'ti-trip-chip' + (trip.name === state.currentTrip ? ' active' : ''));
				chip.appendChild(el('span', 'ti-chip-title', trip.purpose));
				chip.appendChild(el('span', 'ti-chip-sub', trip.start_date + ' → ' + trip.end_date));
				// A trip they set up but are not going on.
				if (someMine && trip.mine === false) chip.appendChild(el('span', 'ti-chip-note', 'Not traveling'));
				chip.addEventListener('click', function () {
					if (trip.name !== state.currentTrip) writeTripEntry(true, trip.name);
					// A different trip is a new entry and opens on its default view; the one on
					// screen just reloads, as it is shown.
					loadTrip(trip.name, trip.name === state.currentTrip ? state.currentAs : '');
				});
				switcher.appendChild(chip);
			});
			root.appendChild(switcher);
		}

		if (state.denied) {
			root.appendChild(el('div', 'ti-empty', 'You don\'t have access to this trip, or it no longer exists.'));
			return;
		}

		if (!state.itinerary) {
			appendPeople();
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
		appendPeople();

		var days = trip.days || [];
		if (!days.length) {
			root.appendChild(el('div', 'ti-empty', 'Nothing scheduled yet — check back once bookings land.'));
			appendFooter();
			return;
		}

		var todayIso = localIso();

		days.forEach(function (day) {
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

	// The person picker: "Me" (when this person is on the trip), "Whole crew", then everyone
	// else on the crew by name. Drawn only when there is a choice to make.
	function appendPeople() {
		var people = state.people;
		if (!people || people.trip !== state.currentTrip) return;
		var shown = shownAs();
		var choices = [];
		var seen = {};
		if (people.onTrip && people.viewer) choices.push({ as: people.viewer, label: 'Me' });
		choices.push({ as: 'crew', label: 'Whole crew' });
		seen[people.viewer || ''] = true;
		people.crew.forEach(function (member) {
			if (member.employee && !seen[member.employee]) {
				seen[member.employee] = true;
				choices.push({ as: member.employee, label: member.employee_name || member.employee });
			}
		});
		if (choices.length < 2) return;

		var picker = el('div', 'ti-people');
		picker.setAttribute('role', 'group');
		picker.setAttribute('aria-label', 'Whose itinerary');
		picker.appendChild(el('span', 'ti-people-label', 'Showing'));
		choices.forEach(function (choice) {
			var on = choice.as === shown;
			var chip = el('button', 'ti-person-chip' + (on ? ' active' : ''), choice.label);
			chip.setAttribute('aria-pressed', on ? 'true' : 'false');
			chip.addEventListener('click', function () {
				if (choice.as !== shown) writeTripEntry(true, state.currentTrip, choice.as);
				// Another person is a new entry; the one on screen just reloads.
				loadTrip(state.currentTrip, choice.as);
			});
			picker.appendChild(chip);
		});
		root.appendChild(picker);
	}

	function appendFooter() {
		var footer = el('footer', 'ti-footer');
		var guidelines = el('a', 'ti-footer-link', 'Company travel guidelines');
		guidelines.href = '/travel_guidelines';
		footer.appendChild(guidelines);
		root.appendChild(footer);
	}

	// Never pushes: popstate calls this too, and a push from there would eat the
	// Forward entries and spend no tap (Chrome then skips the entry on Back).
	// `as` is '' (the default view), 'crew' or an employee id; an answer is only shown
	// while that same trip AND person are still the ones asked for.
	function loadTrip(name, as) {
		as = as || '';
		state.currentTrip = name;
		state.currentAs = as;
		state.itinerary = null;
		state.denied = false;
		state.signedOut = false;
		render();
		var args = { trip: name };
		if (as) args.as_employee = as;
		api('erpnext_enhancements.api.travel.get_trip_itinerary', args)
			.then(function (itinerary) {
				if (state.currentTrip !== name || state.currentAs !== as) return; // user moved on
				state.itinerary = itinerary || { days: [] };
				rememberPeople(name, state.itinerary);
				render();
			})
			.catch(function (err) {
				if (state.currentTrip !== name || state.currentAs !== as) return; // not on screen any more
				loadFailed(name, as, err);
			});
	}

	// -- History (Back / Forward) ------------------------------------------------
	function defaultTrip() {
		// Prefer a trip this person travels on (`mine: false` is a trip they only own): the
		// one happening now, else the next upcoming, else the first.
		var todayIso = localIso();
		var own = state.trips.filter(function (t) { return t.mine !== false; });
		var pool = own.length ? own : state.trips;
		var current = pool.find(function (t) {
			return t.start_date <= todayIso && todayIso <= t.end_date;
		});
		var upcoming = pool.find(function (t) { return t.start_date >= todayIso; });
		return (current || upcoming || pool[0]).name;
	}

	// The server answered with a refusal, or did not answer at all.
	//
	// Signed out since the page loaded is not a refusal, though it arrives as the same 403
	// (see api). The entry stays as it is and the page offers to sign in, which comes back to
	// it. Read as "not your trip", it rewrote the address and told a traveler they had no
	// access to their own trip.
	//
	// A refusal falls back. A person no longer on the trip (the server's 417, and only that:
	// a 400 for a stale CSRF token is not about the person) falls back to the trip's default
	// view. A trip that is not theirs to see, or is gone (403, 404), falls back to their
	// default trip, or the page says so when they have none. The fallback replaces the
	// refused entry and never pushes, so Back still goes where it went. The one exception is
	// an entry the page pushed from exactly the view it would fall back to. Replacing it would
	// leave two identical entries in a row, and a Back that changes nothing, so the page steps
	// back onto the entry behind it instead. While "Report a problem" is open, the entry on
	// screen belongs to the panel and gets no history write: the fallback is only shown. Once
	// the panel closes, popstate asks for the address's trip again, with the panel out of the
	// way.
	function loadFailed(name, as, err) {
		var status = (err && err.status) || 0;
		if (err && err.signedOut) {
			state.signedOut = true;
			render();
			return;
		}
		var fallback = null;
		if (status === 417 && as) {
			fallback = { trip: name, as: '' };
		} else if (status === 403 || status === 404) {
			var trip = state.trips.length ? defaultTrip() : null;
			if (!trip || trip === name) {
				state.denied = true;
				render();
				return;
			}
			fallback = { trip: trip, as: '' };
		}
		if (!fallback) {
			root.appendChild(el('div', 'ti-error', 'Could not load the trip: ' + ((err && err.message) || 'no answer')));
			return;
		}
		if (!captureOpen()) {
			var from = pushedFrom();
			if (from && from.trip === fallback.trip && (from.as || '') === fallback.as) {
				window.history.back();
			} else {
				writeTripEntry(false, fallback.trip, fallback.as);
			}
		}
		loadTrip(fallback.trip, fallback.as);
	}

	// The view the entry on screen was pushed from ({trip, as}), when this page pushed it.
	function pushedFrom() {
		try {
			var entry = window.history.state;
			return entry && entry.itin_from ? entry.itin_from : null;
		} catch (e) {
			return null;
		}
	}

	function rememberPeople(name, itinerary) {
		var crew = Array.isArray(itinerary.crew) ? itinerary.crew : [];
		var viewer = itinerary.viewer_employee || BOOT.employee || null;
		var onTrip;
		if (typeof itinerary.viewer_on_trip === 'boolean') {
			onTrip = itinerary.viewer_on_trip;
		} else if (crew.length) {
			onTrip = crew.some(function (member) { return member.employee === viewer; });
		} else {
			// An answer without the crew: a listed trip of their own is theirs to be on.
			var listed = listedTrip(name);
			onTrip = !!(viewer && listed && listed.mine !== false);
		}
		state.people = { trip: name, crew: crew, viewer: viewer, onTrip: onTrip };
	}

	function listedTrip(name) {
		for (var i = 0; i < state.trips.length; i++) {
			if (state.trips[i].name === name) return state.trips[i];
		}
		return null;
	}

	// The person whose view is on screen: 'crew' or an employee id. The server's answer says
	// (`viewing`, null for the whole crew); until it lands, the address does, and the default
	// is this person on a trip they travel on, else the whole crew.
	function shownAs() {
		var itinerary = state.itinerary;
		if (itinerary && Object.prototype.hasOwnProperty.call(itinerary, 'viewing')) {
			return itinerary.viewing || 'crew';
		}
		if (state.currentAs) return state.currentAs;
		var people = state.people;
		if (people && people.trip === state.currentTrip) {
			return people.onTrip && people.viewer ? people.viewer : 'crew';
		}
		var listed = listedTrip(state.currentTrip);
		return listed && listed.mine !== false && BOOT.employee ? BOOT.employee : 'crew';
	}

	function personName(employee) {
		var people = state.people;
		var crew = people && people.trip === state.currentTrip ? people.crew : [];
		for (var i = 0; i < crew.length; i++) {
			if (crew[i].employee === employee) return crew[i].employee_name || employee;
		}
		return null;
	}

	// Header and page title: "My Itinerary" only for your own view.
	function viewTitle() {
		var mine = { title: 'My Itinerary', sub: BOOT.employee_name || '', page: 'My Itinerary' };
		if (!state.currentTrip || state.denied || state.signedOut) return mine;
		var as = shownAs();
		if (as === 'crew') return { title: 'Whole crew', sub: 'Everyone\'s bookings', page: 'Whole crew itinerary' };
		var me = (state.people && state.people.viewer) || BOOT.employee || null;
		if (as === me) return mine;
		var name = personName(as);
		if (!name) return { title: 'Itinerary', sub: '', page: 'Itinerary' };
		return { title: name + '\'s itinerary', sub: 'Their bookings and confirmation numbers', page: name + '\'s itinerary' };
	}

	function setDocumentTitle(text) {
		try {
			document.title = text;
		} catch (e) {
			// No title to set (a stand-in document): nothing is lost.
		}
	}

	// ?trip= and ?as= from the address. ?as= means nothing without a trip.
	function addressed() {
		try {
			var params = new URLSearchParams(window.location.search);
			var trip = params.get('trip') || '';
			return { trip: trip, as: trip ? (params.get('as') || '') : '' };
		} catch (e) {
			return { trip: '', as: '' };
		}
	}

	// This page's address for a trip and person. No `as` drops ?as=: another trip opens on its
	// default view.
	function tripUrl(name, as) {
		var params = new URLSearchParams(window.location.search);
		params.set('trip', name);
		if (as) params.set('as', as);
		else params.delete('as');
		return window.location.pathname + '?' + params.toString() + window.location.hash;
	}

	function writeTripEntry(push, name, as) {
		var entry = { itin_trip: name, itin_as: as || null };
		// A pushed entry remembers the view it was pushed from, which is the entry behind it,
		// so a refusal that would fall back to exactly that view can step back onto it
		// (loadFailed). A replace keeps what the entry it rewrites remembered: the entry behind
		// it has not changed.
		var from = push ? { trip: state.currentTrip, as: state.currentAs || null } : pushedFrom();
		if (from && from.trip) entry.itin_from = from;
		try {
			if (push) window.history.pushState(entry, '', tripUrl(name, as));
			else window.history.replaceState(entry, '', tripUrl(name, as));
		} catch (e) {
			// Safari refuses bursts of history calls: lose the entry, never the page.
		}
	}

	// True while "Report a problem" is open, and until the popstate of its own
	// closing Back has arrived (capture/panel.js, isPanelOpen).
	function captureOpen() {
		try {
			var cap = window.ee_capture;
			return !!(cap && typeof cap.isOpen === 'function' && cap.isOpen());
		} catch (e) {
			return false;
		}
	}

	function showTripFromUrl() {
		var want = addressed();
		var name = want.trip || (state.trips.length ? defaultTrip() : null);
		if (!name) return;
		if (name !== state.currentTrip || want.as !== state.currentAs) loadTrip(name, want.as);
	}

	window.addEventListener('popstate', function () {
		if (captureOpen()) {
			// This Back is the report panel's to answer. Once it has (the panel's own
			// listener runs after this one), show the trip of the entry it left us on,
			// which is nearly always the trip already on screen.
			setTimeout(function () {
				if (!captureOpen()) showTripFromUrl();
			}, 0);
			return;
		}
		showTripFromUrl();
	});

	// -- Boot --------------------------------------------------------------------
	// A ?trip= is always asked for, listed or not (the server decides; a refusal falls back
	// in loadFailed). Without one, the default trip is named in place.
	var first = addressed();
	if (first.trip) {
		loadTrip(first.trip, first.as);
	} else if (state.trips.length) {
		first.trip = defaultTrip();
		writeTripEntry(false, first.trip);
		loadTrip(first.trip);
	} else {
		render();
	}
})();
