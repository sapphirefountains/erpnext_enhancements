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
 * Documents (Trip Document rows, Plan a Trip's uploads): a booking's files — a boarding
 * pass, a hotel confirmation, a rental agreement, a bill of lading — are listed on its
 * card, and the Documents screen (?view=docs) lists every file this person can see: "For
 * the whole trip" first (a site map, a safety plan, an insurance certificate), then each
 * booking's. The server decides which files those are (get_trip_itinerary's `documents`,
 * on each booking and at the top): one person's view has the files for them, plus the
 * ones for everyone on a booking they are on or on the whole trip; the whole crew has
 * them all. A receipt is money, so none is ever sent. A booking row's own `attachment` is
 * its receipt now and has left the answer; a stale answer that still carries one draws
 * nothing. A picture opens in the viewer over the page (&file=<Trip Document>, big enough
 * to show a boarding pass at the gate). Anything else, a PDF say, opens in a new tab,
 * which on a phone is the phone's own viewer. Never an iframe: iOS shows only the first
 * page of a PDF in one.
 *
 * Contacts: a card at the top of the trip (get_trip_itinerary's `contacts`, built by
 * api/travel._trip_contacts) says who to call, for the person shown: 911, the office's travel
 * desk, who booked the trip, the trip lead's work mobile, the job site's contact, and each of
 * their hotels with the nearest urgent care and directions. Only those. The server never
 * sends a crew member's own phone, email, address, next of kin or health details, and this
 * page has no way to show them. Every value is typed in by people, so it is drawn as text. A
 * phone becomes a tel: link from its digits (and a leading +) only, an email a mailto: link
 * only when it is one, and the server's map links become links only when they are https. The
 * card starts shut, as one line that says what is in it ("Contacts & emergency · travel desk,
 * trip lead, site, 2 hotels, 911": only the parts it has, 911 always last), and a tap opens it
 * (Nik, 2026-09-27). That is not a history entry, so Back never opens or shuts it. This
 * browser remembers it open for that trip, so a trip someone opened stays open (localStorage
 * where the browser allows it, else for as long as the page is open). An answer with no
 * `contacts` draws no card.
 *
 * Trip sheet: "Print / save as PDF", under the screens, opens the trip sheet (the Trip Sheet
 * print format) for the person shown, or for the whole trip on the whole crew's view, as a PDF
 * in a new tab. The phone's own viewer prints and saves it. The server builds both addresses
 * (`my_sheet_url`, `sheet_url`) and leaves money off the sheet for anyone but a travel
 * coordinator. The link writes no history.
 *
 * Offline (Nik, 2026-09-26): the itinerary and its files still open in airplane mode or at a
 * job site with no signal. Every answer get_trip_itinerary sends is kept on this phone, in
 * IndexedDB (database "sapphire-itinerary", store "answers", key "<user>|<trip>|<as>"), with the
 * boot's trip list. After the first trip loads, the person's own upcoming trips (in progress,
 * or starting within 14 days) are saved ahead, quietly (one saved within the hour is not asked
 * for again), and their pictures and PDFs are handed to the service worker (www/itinerary-sw.js,
 * scope /itinerary; the one this page registered, never `serviceWorker.ready`, which on a phone
 * with the kiosk is the kiosk's), which keeps them and the page itself. When there is no usable
 * answer (fetch itself fails, as with no signal; the gateway's 502, 503 or 504 while a deploy
 * restarts the site; a 200 whose body was cut off; a stale CSRF token that could not be
 * replaced; a refusal, a plain 500 or another 4xx is an answer, handled as before), the copy
 * saved for that same trip and person is drawn where the answer would have been, under
 * "You're offline — showing your itinerary as saved <time>." (or "The server isn't answering
 * right now — …"), and it writes no history. So it is when no answer has come after 6 seconds
 * (one bar of signal): the request carries on, and its answer replaces the copy where it
 * stands. Nothing saved for that view says so, and the 'online' event asks again. "Me" offline
 * shows the saved default view when it is theirs. Offline, a PDF opens from the phone's
 * copy in a tab the page opens at the tap: a link's own new tab is outside /itinerary, where
 * the worker cannot answer. A phone can be shared, so a saved copy is only ever shown to the
 * person it was saved for (the boot's `user`), and only while this browser still holds the
 * offline marker it was saved with: the `ee_itinerary_key` cookie
 * (travel_management/itinerary_offline.py), which the server sets whenever it draws this page
 * for someone (the boot's `offline_key` is the same value) and deletes on every sign-out, and on
 * a sign-in as anybody else. frappe's `user_id` cookie cannot say that alone: it is a session
 * cookie, which a home-screen app started again has dropped whether or not anybody signed out.
 * So every answer is saved with the marker, and the worker's files cache is named after it.
 * Offline, a missing or different marker shows nothing saved ("Sign in to see your
 * itinerary.") and deletes everything saved on the phone, answers and files alike. `user_id`
 * still counts too: when it names somebody else, or "Guest" (a session that ran out), nothing
 * saved is shown either, and the copy is kept for its person. A page drawn with some other
 * marker than the phone's (the page kept on the phone, from before a sign-out) draws nothing of
 * its boot until the server answers, and one drawn for somebody other than `user_id` is refused
 * at boot. At boot everything saved for anybody but the marker's person goes, and with no marker
 * at all, everything. No money is in any of it: the answer has none. No IndexedDB, no service
 * worker, storage that throws or refuses: the page is exactly what it was without them.
 *
 * Back / Forward: what is on screen is in the address as
 * ?trip=<name>&as=<who>&view=docs&file=<document> (same path, so a reload, Back from
 * /travel_guidelines and the login redirect all keep it). A trip chip tap, a person pick,
 * "Documents" and a picture each push one entry, from the tap itself. A trip chip drops
 * ?as=, ?view= and ?file=, so another trip opens on its default view; a person pick keeps
 * the screen. Back and Forward arrive as popstate and load that entry's trip and person
 * without pushing, or only redraw its screen and picture when those are all that changed.
 * The viewer's Close and Escape, and "Day by day" on the Documents screen, go Back when
 * the entry behind is exactly where they lead, so no copy of it is left in between. The
 * entry the page opened on is replaced only when ?trip= is missing, or when the
 * server refuses it (someone else's trip, a deleted one, a person no longer on it),
 * never pushed, so Back from it leaves the page. A refused entry the page pushed
 * itself is stepped back off instead when its fallback is the entry behind it, so no
 * two entries in a row are the same (see loadFailed). Signed out since the page
 * loaded is not a refusal: the address stays, and the page offers to sign in again.
 * "Report a problem" (capture/panel.js) owns its own entry: popstate is left to it
 * while window.ee_capture.isOpen(), and so is every history write. The viewer does
 * nothing while the panel is open, Escape included: the panel is on top of it. The
 * Contacts card's open/shut and "Print / save as PDF" are not entries: neither writes
 * history.
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
		// '' = the day-by-day screen, 'docs' = the Documents screen.
		currentView: '',
		// The Trip Document open in the picture viewer, '' for none. Only a picture opens there.
		currentFile: '',
		itinerary: null,
		// The server refused the trip on screen and there is no trip of their own to fall back on.
		denied: false,
		// The session ended since the page loaded (expired, or signed out in another tab).
		signedOut: false,
		// The last crew the server sent: {trip, crew, viewer, onTrip}. Keeps the person picker
		// on screen while another person's view loads.
		people: null,
		// The answer on screen is the copy saved on this phone ({saved_at}), not the server's.
		offline: null,
		// This browser's session is not the person the page was drawn for: nothing saved is shown.
		otherUser: false,
		// The page was drawn with some other offline marker than this phone holds (the page kept on
		// the phone, from before a sign-out): nothing of its boot is drawn until the server answers.
		unverified: false,
	};

	// -- API -----------------------------------------------------------------
	var ITINERARY = 'erpnext_enhancements.api.travel.get_trip_itinerary';

	// What the gateway in front of frappe answers while the site is down, not frappe refusing: a
	// deploy restarting the bench (502), its maintenance window (503, SessionStopped), a timeout
	// (504). The service worker already treats them as no answer for the page itself.
	var GATEWAY_STATUSES = [502, 503, 504];

	// `retried`: this is the one retry after a stale CSRF token was replaced (freshToken).
	//
	// No usable answer (noAnswer, `unreachable`, which draws the copy saved on this phone): fetch
	// itself failed (no signal, airplane mode); the gateway's 502, 503 or 504; a 200 whose body
	// never arrived whole (a connection dropped part way, which drew an empty trip and saved it
	// over the good copy); and a stale CSRF token that could not be replaced. Everything else is an
	// answer, handled as before: a refusal (403, 404, 417), a plain 500, a 400.
	function api(method, args, retried) {
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
		}).then(null, function (err) {
			throw noAnswer(err, 'offline');
		}).then(function (res) {
			return res.json().then(function (data) {
				return { data: data, whole: true };
			}, function () {
				return { data: null, whole: false };
			}).then(function (body) {
				var data = body.data;
				if (GATEWAY_STATUSES.indexOf(res.status) >= 0) throw noAnswer(new Error('HTTP ' + res.status), 'down');
				// A token from an older session: the page kept on the phone, served because the
				// network was slow, after a sign-in elsewhere started a new one. Replaced once and
				// asked again; failing that, it is no answer (and not "Invalid Request" for good).
				if (res.status === 400 && data && data.exc_type === 'CSRFTokenError') {
					if (retried) throw noAnswer(new Error('Invalid Request'), 'down');
					return freshToken().then(function (token) {
						if (!token || token === CSRF) throw noAnswer(new Error('Invalid Request'), 'down');
						CSRF = token;
						// The "Report a problem" panel posts with the same session's token.
						if (window.EE_CAPTURE) window.EE_CAPTURE.csrf_token = token;
						return api(method, args, true);
					});
				}
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
				if (!body.whole) throw noAnswer(new Error('The answer was cut off'), 'slow');
				return data ? data.message : null;
			});
		});
	}

	// `err` marked as no answer at all, and why (offlineBanner's words): 'offline' (fetch failed),
	// 'down' (the gateway, or a token that could not be replaced), 'slow' (nothing yet, or cut off).
	function noAnswer(err, reason) {
		if (!err || typeof err !== 'object') err = new Error(String(err || 'no answer'));
		err.unreachable = true;
		err.reason = reason;
		return err;
	}

	// This session's CSRF token, read from the page's own address fetched afresh: not a navigation,
	// so the service worker passes it to the network and keeps nothing. '' when it cannot be had.
	function freshToken() {
		return fetch('/itinerary', { credentials: 'same-origin', cache: 'no-store' }).then(function (res) {
			return res && res.ok && typeof res.text === 'function' ? res.text() : '';
		}).then(function (html) {
			var match = String(html || '').match(/window\.ITIN_CSRF = "([^"]*)"/);
			return match ? match[1] : '';
		}, function () {
			return '';
		});
	}

	// An answer get_trip_itinerary gives for `name`: an object that names the trip. Anything else
	// (a body that came back empty) is drawn and saved as nothing.
	function isAnswerFor(answer, name) {
		return !!answer && typeof answer === 'object' && answer.trip === name;
	}

	// True when this browser no longer holds a signed-in session: frappe keeps the user in a
	// readable `user_id` cookie, and sets it to Guest or clears it when the session ends. A
	// document with no cookies to read (not a browser) proves nothing, so it counts as signed in.
	function signedOutCookie() {
		var user = cookieUser();
		return user === '' || user === 'Guest';
	}

	// The `user_id` cookie: the user, 'Guest', '' when there is none, or null when this document
	// has no cookies to read (or reading them throws).
	function cookieUser() {
		try {
			if (typeof document.cookie !== 'string') return null;
			var match = document.cookie.match(/(?:^|;\s*)user_id=([^;]*)/);
			return match && match[1] ? decodeURIComponent(match[1]) : '';
		} catch (e) {
			return null;
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
			// A failure (offline, most often: the saved copy has Map buttons too) is not kept: the
			// tags go, and the next tap tries again, once the signal is back.
			var failed = function () {
				leafletPromise = null;
				[css, script].forEach(function (tag) {
					if (tag.parentNode) tag.parentNode.removeChild(tag);
				});
				reject(new Error('Leaflet failed to load'));
			};
			script.onload = function () {
				if (window.L) resolve(window.L);
				else failed();
			};
			script.onerror = failed;
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
			container.textContent = ''; // a "Map unavailable" from a tap while offline
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
			// Shown: the map's box is hidden until it has a map (itinerary.css), so without this a
			// tap offline did nothing visible at all.
			container.style.display = 'block';
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
			appendDocuments(card, item.documents);
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
			appendDocuments(card, item.documents);
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
			appendDocuments(card, item.documents);
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
		appendDocuments(card, item.documents);
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
			var note = memberNote(member);
			if (note) row.appendChild(el('span', 'ti-member-note', note));
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

	// A room guest (someone staying in another person's room, Trip Accommodation `guest`), and
	// anyone whose own nights in a room are not the booking's: "guest · Mon, Sep 28 – Tue, Sep 29".
	// The server sends both on the whole-crew view only, and the dates only where they differ.
	function memberNote(member) {
		var bits = [];
		if (member.guest) bits.push('guest');
		var nights = [member.check_in_date, member.check_out_date].filter(Boolean).map(function (iso) {
			return fmtDate(String(iso).slice(0, 10));
		});
		if (nights.length) bits.push(nights.join(' – '));
		return bits.join(' · ');
	}

	// -- Documents -------------------------------------------------------------
	// Trip Document kinds (the Select's options), each with its icon. Anything else is a page.
	var KIND_ICONS = {
		'Boarding pass': '🎫',
		'Booking confirmation': '📋',
		'Rental agreement': '🔑',
		'Bill of lading': '📦',
		'Site map': '🗺',
		'Safety plan': '🦺',
		'Insurance certificate': '🛡',
		'Job packet': '🗂',
	};

	function kindIcon(kind) {
		return Object.prototype.hasOwnProperty.call(KIND_ICONS, kind) ? KIND_ICONS[kind] : '📄';
	}

	function docTitle(doc) {
		return doc.title || doc.file_name || doc.kind || 'Document';
	}

	// "Picture" for anything the viewer opens, else the file's extension ("PDF").
	function fileType(doc) {
		if (doc.is_image) return 'Picture';
		var match = String(doc.file_name || doc.url || '').match(/\.([a-z0-9]{1,5})(?:[?#].*)?$/i);
		return match ? match[1].toUpperCase() : 'File';
	}

	// An address the server sent that this page may put in a link: one of this site's paths or a
	// web address. Never a `javascript:` one (frappe's File refuses those too; this page does not
	// rely on it), and never one with a backslash: a browser reads "/\host" as "//host", which is
	// another site.
	function linkable(url) {
		return typeof url === 'string' && /^(\/(?![/\\])|https?:\/\/)/i.test(url) && url.indexOf('\\') < 0;
	}

	// The documents in an answer that can be opened: a row with no file has nothing to show. A
	// file's address goes into a link, so it must be `linkable`.
	function openable(docs) {
		return (Array.isArray(docs) ? docs : []).filter(function (doc) {
			return doc && linkable(doc.url);
		});
	}

	// Every file this person can see: the answer's own list, or, from an answer without one, each
	// booking's, once each.
	function tripDocuments(itinerary) {
		if (!itinerary) return [];
		if (Array.isArray(itinerary.documents)) return openable(itinerary.documents);
		var out = [];
		var seen = {};
		(itinerary.days || []).forEach(function (day) {
			(day.items || []).forEach(function (item) {
				openable(item.documents).forEach(function (doc) {
					if (seen['d:' + doc.name]) return;
					seen['d:' + doc.name] = true;
					out.push(doc);
				});
			});
		});
		return out;
	}

	function findDocument(name) {
		var docs = tripDocuments(state.itinerary);
		for (var i = 0; i < docs.length; i++) {
			if (docs[i].name === name) return docs[i];
		}
		return null;
	}

	// One file, as a big tap target (a boarding pass is shown at the gate): its kind's icon, its
	// title, then its kind, who it is for (on the whole-crew view) and what sort of file it is. A
	// picture opens in the viewer over the page; anything else opens in a new tab, which on a
	// phone is its own viewer. Both are real links, so a long press or a Ctrl+click still offers
	// the browser's own "open in new tab".
	function docLink(doc) {
		var link = el('a', 'ti-doc');
		link.href = doc.url;
		link.setAttribute('data-doc', doc.name);
		var icon = el('span', 'ti-doc-icon', kindIcon(doc.kind));
		icon.setAttribute('aria-hidden', 'true');
		link.appendChild(icon);
		var title = docTitle(doc);
		var body = el('span', 'ti-doc-body');
		body.appendChild(el('span', 'ti-doc-title', title));
		var sub = [];
		if (doc.kind && doc.kind !== title) sub.push(doc.kind);
		if (doc.for_name && shownAs() === 'crew') sub.push('for ' + doc.for_name);
		sub.push(fileType(doc));
		body.appendChild(el('span', 'ti-doc-sub', sub.join(' · ')));
		link.appendChild(body);
		var go = el('span', 'ti-doc-go', doc.is_image ? 'View' : 'Open ↗');
		go.setAttribute('aria-hidden', 'true');
		link.appendChild(go);
		if (doc.is_image) {
			link.setAttribute('aria-haspopup', 'dialog');
			link.addEventListener('click', function (ev) {
				// A click asking for a new tab or a download is the browser's to answer.
				if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || (ev.button && ev.button !== 0)) return;
				ev.preventDefault();
				openPicture(doc);
			});
		} else {
			link.target = '_blank';
			link.rel = 'noopener';
			link.appendChild(el('span', 'ti-sr-only', ' (opens in a new tab)'));
			openSavedWhenOffline(link, doc);
		}
		return link;
	}

	function appendDocuments(card, docs) {
		var list = openable(docs);
		if (!list.length) return;
		var box = el('div', 'ti-docs');
		list.forEach(function (doc) { box.appendChild(docLink(doc)); });
		card.appendChild(box);
	}

	// "Day by day" and "Documents (N)", the trip's two screens. Drawn when there is a file to
	// show, and always on the Documents screen, so a reload of one whose files have since gone
	// still has a way back.
	function appendScreens(count) {
		if (!count && state.currentView !== 'docs') return;
		var nav = el('div', 'ti-screens');
		nav.setAttribute('role', 'group');
		nav.setAttribute('aria-label', 'Screen');
		[
			{ view: '', label: 'Day by day' },
			{ view: 'docs', label: 'Documents (' + count + ')' },
		].forEach(function (screen) {
			var on = screen.view === state.currentView;
			var tab = el('button', 'ti-screen-tab' + (on ? ' active' : ''), screen.label);
			tab.setAttribute('aria-pressed', on ? 'true' : 'false');
			tab.addEventListener('click', function () { openScreen(screen.view); });
			nav.appendChild(tab);
		});
		root.appendChild(nav);
	}

	// A file's booking, as the Documents screen groups it: its `group` (null for the whole
	// trip's), else its label from an answer that has no group.
	function bookingKey(doc) {
		return doc.group ? 'g:' + doc.group : (doc.booking_label ? 'l:' + doc.booking_label : '');
	}

	// Who is on a booking ("Ann Rivera, Bo"; "Whole crew") and when ("Mon, Oct 5 – Thu, Oct 8"),
	// from what the server sends with each of its files. '' when it sent nothing.
	function bookingPeople(doc) {
		if (!Array.isArray(doc.booking_people)) return '';
		return doc.booking_people.length ? doc.booking_people.join(', ') : 'Whole crew';
	}

	function bookingDates(doc) {
		var dates = Array.isArray(doc.booking_dates) ? doc.booking_dates : [];
		return dates.filter(function (iso) { return typeof iso === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(iso); })
			.map(fmtDate).join(' – ');
	}

	// What each booking is called on the Documents screen, {bookingKey: heading}. Usually its
	// label. Two bookings can be called the same: the policy is one adult to a room, so four
	// rooms at one hotel are four bookings named after it, each with a confirmation for nobody
	// in particular. Those say who is on each on the whole crew's list, and when on one
	// person's (theirs: a split stay), and when as well if who is the same.
	function bookingHeadings(docs) {
		var groups = [];
		var seen = {};
		docs.forEach(function (doc) {
			var key = bookingKey(doc);
			if (!key || seen[key]) return;
			seen[key] = true;
			groups.push({ key: key, doc: doc, heading: doc.booking_label || 'Booking' });
		});
		var crew = shownAs() === 'crew';
		var tell = function (describe) {
			var count = {};
			groups.forEach(function (g) { count[g.heading] = (count[g.heading] || 0) + 1; });
			groups.forEach(function (g) {
				var more = count[g.heading] > 1 ? describe(g.doc) : '';
				if (more) g.heading += ' · ' + more;
			});
		};
		if (crew) tell(bookingPeople);
		tell(bookingDates);
		var out = {};
		groups.forEach(function (g) { out[g.key] = g.heading; });
		return out;
	}

	// What an empty Documents screen says. A person's own list leaves out what is not theirs,
	// so "no documents for this trip" would be false beside the whole crew's ten.
	function noDocumentsText() {
		var as = shownAs();
		if (as === 'crew') return 'No documents for this trip yet.';
		var me = (state.people && state.people.viewer) || BOOT.employee || null;
		if (as === me) return 'No files for you on this trip yet.';
		var name = personName(as);
		return name ? 'No files for ' + name + ' on this trip yet.' : 'No files on this view yet.';
	}

	// The Documents screen: "For the whole trip" first, then each booking's files under its
	// name, in the order the answer lists them (the itinerary's). A file's booking is its
	// `group` (null for the whole trip's), not its label: two rooms at the same hotel are two
	// bookings, and a booking's label can be blank (bookingHeadings tells them apart).
	function renderDocuments(docs) {
		if (!docs.length) {
			root.appendChild(el('div', 'ti-empty', noDocumentsText()));
			return;
		}
		var groups = [];
		docs.forEach(function (doc) {
			var key = bookingKey(doc);
			var group = null;
			for (var i = 0; i < groups.length; i++) {
				if (groups[i].key === key) group = groups[i];
			}
			if (!group) {
				group = { key: key, docs: [] };
				groups.push(group);
			}
			group.docs.push(doc);
		});
		groups.sort(function (a, b) { return (a.key ? 1 : 0) - (b.key ? 1 : 0); });
		var headings = bookingHeadings(docs);
		groups.forEach(function (group) {
			var section = el('section', 'ti-doc-group');
			section.appendChild(el('h2', 'ti-doc-group-title',
				group.key ? headings[group.key] : 'For the whole trip'));
			var list = el('div', 'ti-docs');
			group.docs.forEach(function (doc) { list.appendChild(docLink(doc)); });
			section.appendChild(list);
			root.appendChild(section);
		});
	}

	// -- Contacts ------------------------------------------------------------------
	// The card at the top of the trip: who to call, from the answer's `contacts` (see the note at
	// the top of this file). Values are typed in by people and drawn as text. What becomes a link
	// is decided here, in telHref, mailHref and webHref, and nowhere else.

	// Remembered open, per trip, for this page load: {'t:<trip>': true|false}. Filled from
	// localStorage the first time a trip's card is drawn, so it holds when there is no storage.
	var contactsOpen = {};
	var CONTACTS_KEY = 'ti-contacts-open:';

	// "(602) 555-0100 ext. 12" -> "tel:6025550100". Digits only, and a + only in front. An
	// extension is dropped, not run into the number. A note beside the number ("Front desk
	// 702-555-0150") is dropped too, but a number spelled in letters ("1-800-FLOWERS") would
	// dial something else entirely (1800), so it is no number to dial, and neither is one with
	// fewer than three digits: '' (shown as typed).
	function telHref(phone) {
		var text = String(phone == null ? '' : phone).replace(/^\s*tel:/i, '').split(/ext|x|#|;|,/i)[0];
		if (/[a-z]/i.test(text)) {
			var runs = text.match(/\+?\d[\d\s().-]*\d/g) || [];
			if (runs.length !== 1 || runs[0].replace(/\D/g, '').length < 7) return '';
			text = runs[0];
		}
		var digits = text.replace(/\D/g, '');
		if (digits.length < 3 || digits.length > 15) return '';
		return 'tel:' + (/^\s*\+/.test(text) ? '+' : '') + digits;
	}

	// An address that is plainly one ("travel@example.com"), else ''. Nothing that could carry
	// a header (?cc=, &body=) or an encoding (%) past it.
	function mailHref(email) {
		var text = String(email == null ? '' : email).trim();
		return /^[A-Za-z0-9._+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/.test(text) ? 'mailto:' + text : '';
	}

	// A link the server built (urgent care, directions): an https address, else ''.
	function webHref(url) {
		return typeof url === 'string' && /^https:\/\/[^\s]+$/i.test(url) ? url : '';
	}

	function directionsHref(address) {
		return 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(address);
	}

	// An address as one line of text. The server sends text; a line break sent as <br> (an
	// Address's display) becomes ", " rather than markup on the page.
	function plainText(value) {
		return String(value == null ? '' : value)
			.replace(/<br\s*\/?>/gi, ', ')
			.replace(/<[^>]*>/g, '')
			.replace(/\s*[\r\n]+\s*/g, ', ')
			.replace(/\s+/g, ' ')
			.replace(/^[\s,]+|[\s,]+$/g, '');
	}

	// One contact link as a big tap target, short enough that two share a line on a phone: an
	// icon and a word or the number. `label` is its whole name for a screen reader and a mouse
	// ("Email Sapphire travel desk, travel@…"), since "Email" alone says neither who nor where.
	// A web link opens in a new tab.
	function contactLink(icon, text, href, label, external) {
		var link = el('a', 'ti-contact-link');
		link.href = href;
		var mark = el('span', 'ti-contact-icon', icon);
		mark.setAttribute('aria-hidden', 'true');
		link.appendChild(mark);
		link.appendChild(el('span', 'ti-contact-text', text));
		if (external) {
			link.target = '_blank';
			link.rel = 'noopener';
			label += ' (opens in a new tab)';
		}
		link.setAttribute('aria-label', label);
		link.setAttribute('title', label);
		return link;
	}

	// One person or place on the card: what they are to the trip ("Trip lead"), their name and
	// any lines of detail, then the links. A phone that cannot be dialed from here, or an email
	// that is not an address, is shown as typed. `callText` replaces the number on its link
	// ("Call 911"), and `who` is whom the links reach when that is not the name (a job site's
	// contact).
	function contactRow(o) {
		var who = o.who || o.name || o.role;
		var row = el('div', 'ti-contact' + (o.cls ? ' ' + o.cls : ''));
		var about = el('div', 'ti-contact-who');
		about.appendChild(el('div', 'ti-contact-role', o.role));
		if (o.name) about.appendChild(el('div', 'ti-contact-name', o.name));
		row.appendChild(about);
		var lines = (o.lines || []).slice();
		var links = [];
		var phone = o.phone == null ? '' : String(o.phone).trim();
		var tel = telHref(phone);
		if (tel) links.push(contactLink('📞', o.callText || phone, tel, o.callText || 'Call ' + who + ', ' + phone, false));
		else if (phone) lines.push(phone);
		var email = o.email == null ? '' : String(o.email).trim();
		var mail = mailHref(email);
		if (mail) links.push(contactLink('✉', 'Email', mail, 'Email ' + who + ', ' + email, false));
		else if (email) lines.push(email);
		(o.web || []).forEach(function (w) {
			var href = webHref(w.href);
			if (href) links.push(contactLink(w.icon, w.text, href, w.label, true));
		});
		lines.forEach(function (line) {
			if (line) about.appendChild(el('div', 'ti-contact-sub', line));
		});
		if (links.length) {
			var box = el('div', 'ti-contact-links');
			links.forEach(function (link) { box.appendChild(link); });
			row.appendChild(box);
		}
		return row;
	}

	function trimmed(value) {
		return value == null ? '' : String(value).trim();
	}

	// The card's rows, 911 first, and the shut card's one line: a word for each part it has, in
	// the card's order, then 911, which is always there and always last ("travel desk, trip
	// lead, site, 2 hotels, 911"). A part the answer leaves out (null), or sends with nobody named
	// and nothing to call, write or find, is no row and no word: a travel desk with no phone or
	// email, a job site with only the job's name. A booker, trip lead or site contact known only
	// by name is still a row and a word, with no link, as the sheet, the email and Plan a Trip
	// list them: most trip leads have no work cell on file, and the crew still needs the name.
	function contactRows(c) {
		var rows = [];
		var words = [];
		var emergency = trimmed(c.emergency) || '911';
		rows.push(contactRow({ role: 'Emergency', phone: emergency, callText: 'Call ' + emergency, cls: 'ti-contact-emergency' }));
		var office = c.office || null;
		if (office && (trimmed(office.phone) || trimmed(office.email))) {
			rows.push(contactRow({ role: 'Travel desk', name: trimmed(office.label), phone: office.phone, email: office.email }));
			words.push('travel desk');
		}
		var booked = c.booked_by || null;
		if (booked && (trimmed(booked.name) || trimmed(booked.phone) || trimmed(booked.email))) {
			rows.push(contactRow({ role: 'Booked by', name: trimmed(booked.name), phone: booked.phone, email: booked.email }));
			words.push('booked by');
		}
		var lead = c.lead || null;
		if (lead && (trimmed(lead.name) || trimmed(lead.phone))) {
			rows.push(contactRow({ role: 'Trip lead', name: trimmed(lead.name), phone: lead.phone }));
			words.push('trip lead');
		}
		var site = c.site || null;
		var siteAddress = site ? plainText(site.address) : '';
		if (site && (trimmed(site.contact_name) || trimmed(site.phone) || trimmed(site.email) || siteAddress)) {
			words.push('site');
			rows.push(contactRow({
				role: 'Job site',
				name: trimmed(site.label),
				who: trimmed(site.contact_name),
				lines: [trimmed(site.contact_name), siteAddress],
				phone: site.phone,
				email: site.email,
				web: siteAddress ? [{ icon: '🧭', text: 'Directions', label: 'Directions to ' + siteAddress, href: directionsHref(siteAddress) }] : [],
			}));
		}
		var hotels = (Array.isArray(c.hotels) ? c.hotels : []).filter(function (h) {
			return h && (trimmed(h.name) || trimmed(h.phone) || plainText(h.address));
		});
		hotels.forEach(function (hotel) {
			var where = trimmed(hotel.name) || plainText(hotel.address) || 'the hotel';
			rows.push(contactRow({
				role: 'Hotel',
				name: trimmed(hotel.name),
				lines: [plainText(hotel.address)],
				phone: hotel.phone,
				web: [
					{ icon: '🏥', text: 'Urgent care nearby', label: 'Urgent care near ' + where, href: hotel.urgent_care_url },
					{ icon: '🧭', text: 'Directions', label: 'Directions to ' + where, href: hotel.directions_url },
				],
			}));
		});
		if (hotels.length) words.push(hotels.length === 1 ? '1 hotel' : hotels.length + ' hotels');
		words.push(emergency);
		return { rows: rows, summary: words.join(', ') };
	}

	function contactsAreOpen(trip) {
		var key = 't:' + trip;
		if (Object.prototype.hasOwnProperty.call(contactsOpen, key)) return contactsOpen[key];
		var open = false;
		try {
			open = window.localStorage.getItem(CONTACTS_KEY + trip) === '1';
		} catch (e) {
			// No storage here (a private window, blocked site data): shut, as on a first visit.
		}
		contactsOpen[key] = open;
		return open;
	}

	// Only an open card is written down: shut is where every trip starts.
	function rememberContacts(trip, open) {
		contactsOpen['t:' + trip] = open;
		try {
			if (open) window.localStorage.setItem(CONTACTS_KEY + trip, '1');
			else window.localStorage.removeItem(CONTACTS_KEY + trip);
		} catch (e) {
			// Kept for as long as the page is open.
		}
	}

	// The card, shut until this person opens it on this trip: a button whose one line says what
	// is in it, "Contacts & emergency · travel desk, trip lead, site, 2 hotels, 911". Open, the
	// button reads "Contacts & emergency" over the rows. It opens and shuts the card in place:
	// no redraw, no history entry, and aria-expanded says which.
	function appendContacts(contacts) {
		if (!contacts || typeof contacts !== 'object') return;
		var parts = contactRows(contacts);
		var trip = state.currentTrip;
		var card = el('section', 'ti-contacts');
		card.setAttribute('aria-label', 'Contacts & emergency');
		var heading = el('h2', 'ti-contacts-heading');
		var toggle = el('button', 'ti-contacts-toggle');
		toggle.setAttribute('type', 'button');
		toggle.setAttribute('aria-controls', 'ti-contacts-body');
		// One run of inline text, so the line wraps as a sentence on a narrow phone rather than
		// cutting 911 off its end.
		var line = el('span', 'ti-contacts-line');
		line.appendChild(el('span', 'ti-contacts-title', 'Contacts & emergency'));
		var summary = el('span', 'ti-contacts-summary', ' · ' + parts.summary);
		line.appendChild(summary);
		toggle.appendChild(line);
		var chevron = el('span', 'ti-contacts-chevron', '▾');
		chevron.setAttribute('aria-hidden', 'true');
		toggle.appendChild(chevron);
		heading.appendChild(toggle);
		card.appendChild(heading);
		var body = el('div', 'ti-contacts-body');
		body.id = 'ti-contacts-body';
		parts.rows.forEach(function (row) { body.appendChild(row); });
		card.appendChild(body);
		var show = function (open) {
			body.hidden = !open;
			summary.hidden = open;
			toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
			card.classList.toggle('open', open);
		};
		show(contactsAreOpen(trip));
		toggle.addEventListener('click', function () {
			var open = body.hidden;
			show(open);
			rememberContacts(trip, open);
		});
		root.appendChild(card);
	}

	// -- Trip sheet ------------------------------------------------------------------
	// "Print / save as PDF": the person shown's sheet (`my_sheet_url`), else the whole trip's
	// (`sheet_url`), as the server addresses it (views.trip_sheet_url). Only a `linkable` address
	// is made a link. A PDF in a new tab, the phone's own viewer, and no history entry.
	function sheetLink(trip) {
		if (linkable(trip.my_sheet_url)) return { url: trip.my_sheet_url, whole: false };
		if (linkable(trip.sheet_url)) return { url: trip.sheet_url, whole: true };
		return null;
	}

	// Whose sheet it is, said under the link: "Your trip sheet", "Sam's trip sheet", or "The
	// whole trip" (the whole crew's view, or no sheet of the person's own to open).
	function sheetWhose(whole) {
		var as = shownAs();
		if (whole || as === 'crew') return 'The whole trip';
		var me = (state.people && state.people.viewer) || BOOT.employee || null;
		if (as === me) return 'Your trip sheet';
		var name = personName(as);
		return name ? name + '\'s trip sheet' : 'Trip sheet';
	}

	function appendPrint(trip) {
		var sheet = sheetLink(trip);
		if (!sheet) return;
		var bar = el('div', 'ti-print');
		var link = el('a', 'ti-print-link');
		link.href = sheet.url;
		link.target = '_blank';
		link.rel = 'noopener';
		var icon = el('span', 'ti-print-icon', '🖨');
		icon.setAttribute('aria-hidden', 'true');
		link.appendChild(icon);
		var body = el('span', 'ti-print-body');
		body.appendChild(el('span', 'ti-print-title', 'Print / save as PDF'));
		body.appendChild(el('span', 'ti-print-sub', sheetWhose(sheet.whole)));
		link.appendChild(body);
		link.appendChild(el('span', 'ti-sr-only', ' (opens in a new tab)'));
		bar.appendChild(link);
		root.appendChild(bar);
	}

	// -- Picture viewer ----------------------------------------------------------
	// Drawn over the page, outside #itinerary-root, from the state: open while the address
	// names a picture this person can see, shut otherwise. A name the answer does not have (a
	// file since removed, one for someone else, a PDF) is dropped and nothing opens. The address
	// keeps it until the next entry is written, which does not carry it.
	var viewer = null; // {node, doc, close, original, returnFocus}

	function syncViewer() {
		var doc = null;
		if (state.currentFile && state.itinerary) {
			doc = findDocument(state.currentFile);
			if (!doc || !doc.is_image) {
				doc = null;
				state.currentFile = '';
			}
		}
		if (viewer && (!doc || viewer.doc.name !== doc.name)) shutViewer();
		if (doc && !viewer) drawViewer(doc);
	}

	function drawViewer(doc) {
		var host = document.body;
		if (!host) return;
		var returnFocus = document.activeElement || null;
		var title = docTitle(doc);
		var box = el('div', 'ti-viewer');
		box.setAttribute('role', 'dialog');
		box.setAttribute('aria-modal', 'true');
		box.setAttribute('aria-labelledby', 'ti-viewer-title');

		var bar = el('div', 'ti-viewer-bar');
		var heading = el('div', 'ti-viewer-heading');
		var name = el('div', 'ti-viewer-title', title);
		name.id = 'ti-viewer-title';
		heading.appendChild(name);
		var sub = [];
		if (doc.kind && doc.kind !== title) sub.push(doc.kind);
		if (doc.for_name) sub.push('for ' + doc.for_name);
		// Its booking as the Documents screen names it: which of four rooms at one hotel.
		if (bookingKey(doc)) sub.push(bookingHeadings(tripDocuments(state.itinerary))[bookingKey(doc)] || doc.booking_label);
		if (sub.length) heading.appendChild(el('div', 'ti-viewer-sub', sub.join(' · ')));
		bar.appendChild(heading);
		var close = el('button', 'ti-viewer-close', 'Close');
		close.setAttribute('type', 'button');
		close.addEventListener('click', function () { closePicture(); });
		bar.appendChild(close);
		box.appendChild(bar);

		var stage = el('div', 'ti-viewer-stage');
		var img = el('img', 'ti-viewer-img');
		img.alt = title;
		// A picture this browser cannot draw (a HEIC photo anywhere but Safari), or a sign-in
		// that has run out: say so, and leave "Open original".
		img.addEventListener('error', function () {
			stage.textContent = '';
			stage.appendChild(el('div', 'ti-viewer-error', 'This picture can\'t be shown here. Try Open original.'));
		});
		img.src = doc.url;
		stage.appendChild(img);
		box.appendChild(stage);

		var foot = el('div', 'ti-viewer-foot');
		var original = el('a', 'ti-viewer-original', 'Open original');
		original.href = doc.url;
		original.target = '_blank';
		original.rel = 'noopener';
		openSavedWhenOffline(original, doc);
		foot.appendChild(original);
		box.appendChild(foot);

		// Tab stays inside: Close and "Open original" are all there is.
		box.addEventListener('keydown', function (ev) {
			if (ev.key !== 'Tab') return;
			var active = document.activeElement;
			if (ev.shiftKey && active === close) {
				ev.preventDefault();
				focusOn(original);
			} else if (!ev.shiftKey && active === original) {
				ev.preventDefault();
				focusOn(close);
			}
		});

		host.appendChild(box);
		host.classList.add('ti-viewer-open');
		// The page underneath is out of reach until the viewer closes (inert where the browser
		// has it, hidden from screen readers either way).
		root.setAttribute('inert', '');
		root.setAttribute('aria-hidden', 'true');
		viewer = { node: box, doc: doc, close: close, original: original, returnFocus: returnFocus };
		focusOn(close);
	}

	function shutViewer() {
		var open = viewer;
		viewer = null;
		if (open.node.parentNode) open.node.parentNode.removeChild(open.node);
		if (document.body) document.body.classList.remove('ti-viewer-open');
		root.removeAttribute('inert');
		root.removeAttribute('aria-hidden');
		// Back to what opened it, while that is still on the page.
		var back = open.returnFocus;
		if (back && back.isConnected !== false) focusOn(back);
	}

	function focusOn(node) {
		try {
			if (node && typeof node.focus === 'function') node.focus();
		} catch (e) {
			// Nothing to focus: the courtesy is lost, never the page.
		}
	}

	// A picture tapped: the viewer, as an entry of its own (&file=), from the tap.
	function openPicture(doc) {
		if (captureOpen() || !doc || !doc.is_image || state.currentFile === doc.name) return;
		writeTripEntry(true, state.currentTrip, state.currentAs, state.currentView, doc.name);
		state.currentFile = doc.name;
		syncViewer();
	}

	// Close and Escape. When the entry behind is the screen the picture was opened over (always,
	// when a tap here opened it, reloaded or not), closing is Back, which leaves the picture's
	// entry for Forward instead of a copy of the screen on top of it. Opened straight from an
	// address (a link with &file=), there is nothing of this page's behind it and Back would
	// leave the page, so the entry stops naming the file instead. Shut at once either way: a
	// second tap on Close before the Back lands must not go Back again.
	function closePicture() {
		if (!state.currentFile || captureOpen()) return;
		var under = { trip: state.currentTrip, as: state.currentAs, view: state.currentView };
		if (samePlace(pushedFrom(), under)) {
			window.history.back();
		} else {
			writeTripEntry(false, state.currentTrip, state.currentAs, state.currentView);
		}
		state.currentFile = '';
		syncViewer();
	}

	document.addEventListener('keydown', function (ev) {
		if (!viewer || ev.key !== 'Escape' || ev.defaultPrevented) return;
		// "Report a problem" is over the viewer: its Escape is its own.
		if (captureOpen()) return;
		ev.preventDefault();
		closePicture();
	});

	// A tap on "Day by day" or "Documents": one entry, from the tap. Back onto the screen it was
	// opened from is a step Back rather than a new entry when that screen is the entry behind
	// (the Documents screen reached from the day list, then "Day by day"), or the same screen
	// would be in history twice in a row, with a Back between them that changes nothing.
	function openScreen(view) {
		if (captureOpen() || view === state.currentView) return;
		if (samePlace(pushedFrom(), { trip: state.currentTrip, as: state.currentAs, view: view })) {
			window.history.back();
		} else {
			writeTripEntry(true, state.currentTrip, state.currentAs, view);
		}
		showScreen(view, '');
	}

	// The screen and picture an entry names, for the trip and person already on screen: nothing
	// is fetched. With no answer on screen yet (still loading, or an error) nothing is drawn: the
	// answer draws the screen asked for.
	function showScreen(view, file) {
		var redraw = view !== state.currentView;
		state.currentView = view;
		state.currentFile = file;
		if (redraw && state.itinerary) render();
		else syncViewer();
	}

	// -- Rendering ---------------------------------------------------------------
	function render() {
		root.innerHTML = '';
		root.removeAttribute('aria-busy');
		// The viewer is outside the root: shut when its trip or person is gone, opened once the
		// answer holds the picture the address names.
		syncViewer();

		var view = viewTitle();
		var header = el('header', 'ti-header');
		header.appendChild(el('div', 'ti-header-title', view.title));
		if (view.sub) header.appendChild(el('div', 'ti-header-sub', view.sub));
		root.appendChild(header);
		var onDocs = state.currentView === 'docs' && state.currentTrip && !state.denied && !state.signedOut && !state.otherUser;
		setDocumentTitle(onDocs ? 'Documents – ' + view.page : view.page);

		if (state.signedOut) {
			var expired = el('div', 'ti-empty', 'Your session has expired. ');
			var signIn = el('a', 'ti-signin', 'Sign in again');
			signIn.href = signInUrl();
			expired.appendChild(signIn);
			root.appendChild(expired);
			return;
		}

		// Somebody else's session, or signed out, on a page drawn for another person: nothing
		// saved on this phone is shown, not even the trip list. Never a dead end: somebody else
		// signed in (the page kept on a shared phone, served on a slow connection) is offered a
		// reload, which draws their own page; nobody signed in, the way to sign in.
		if (state.otherUser) {
			var who = cookieUser();
			var refused = el('div', 'ti-refused');
			if (who && who !== 'Guest' && who !== bootUser() && !phoneSaysOffline()) {
				refused.appendChild(el('div', 'ti-empty', 'This page was saved for someone else.'));
				var reload = el('button', 'ti-reload', 'Reload');
				reload.type = 'button';
				reload.addEventListener('click', function () { window.location.reload(); });
				refused.appendChild(reload);
			} else {
				refused.appendChild(el('div', 'ti-empty', 'Sign in to see your itinerary.'));
				var signInLink = el('a', 'ti-signin', 'Sign in');
				signInLink.href = signInUrl();
				refused.appendChild(signInLink);
			}
			root.appendChild(refused);
			return;
		}

		if (!state.currentTrip) {
			root.appendChild(el('div', 'ti-empty',
				BOOT.employee
					? 'No upcoming or recent trips. Safe travels when the next one comes!'
					: 'No employee record is linked to your user account.'));
			return;
		}

		// A page drawn with another marker than this phone's: nothing more of its boot (the trip
		// list; the name is already off the header) until the server has answered (see answered).
		if (state.unverified) {
			root.appendChild(el('div', 'ti-boot', 'Loading itinerary…'));
			return;
		}

		// The copy saved on this phone, shown because the server could not be reached.
		if (state.offline && state.itinerary && !state.denied) root.appendChild(offlineBanner(state.offline));

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
					// A different trip is a new entry and opens on its default view, day by day;
					// the one on screen just reloads, as it is shown.
					var same = trip.name === state.currentTrip;
					loadTrip(trip.name, same ? state.currentAs : '', same ? state.currentView : '');
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
			// No answer and nothing saved for this view: said in the place of "Loading trip…", which
			// would otherwise stay above it for good (the 'online' listener asks again).
			if (state.offline && state.offline.missing) {
				root.appendChild(el('div', 'ti-error', offlineWords(state.offline.reason) + ', and this itinerary isn\'t saved on this phone yet.'));
			} else {
				root.appendChild(el('div', 'ti-boot', 'Loading trip…'));
			}
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
		appendContacts(trip.contacts);

		var documents = tripDocuments(trip);
		appendScreens(documents.length);
		appendPrint(trip);
		if (state.currentView === 'docs') {
			renderDocuments(documents);
			appendFooter();
			return;
		}

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
				if (choice.as !== shown) writeTripEntry(true, state.currentTrip, choice.as, state.currentView);
				// Another person is a new entry, on the same screen; the one on screen just reloads.
				loadTrip(state.currentTrip, choice.as, state.currentView);
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
	// while that same trip AND person are still the ones asked for. `view` and `file` are
	// the screen and the picture to draw once it lands (none, for a different trip).
	// No usable answer (no signal, the gateway down, a body cut off: see api) draws the copy saved
	// on this phone instead, if there is one (showSaved); every answer that does land is saved for
	// next time (keepForOffline). No answer yet after ANSWER_WAIT_MS — one bar of signal, where a
	// request can hang for minutes — draws the saved copy too, if there is one, and the request
	// carries on: when it lands, the answer replaces the copy where it stands.
	function loadTrip(name, as, view, file) {
		as = as || '';
		state.currentTrip = name;
		state.currentAs = as;
		state.currentView = view || '';
		state.currentFile = file || '';
		state.itinerary = null;
		state.offline = null;
		state.denied = false;
		state.signedOut = false;
		render();
		var args = { trip: name };
		if (as) args.as_employee = as;
		var settled = false;
		var timer = setTimeout(function () {
			var stillAsked = state.currentTrip === name && state.currentAs === as;
			if (settled || state.itinerary || !stillAsked) return;
			showSaved(name, as, noAnswer(new Error('no answer yet'), 'slow'), true);
		}, ANSWER_WAIT_MS);
		var done = function () {
			settled = true;
			clearTimeout(timer);
		};
		api(ITINERARY, args)
			.then(function (itinerary) {
				done();
				if (state.currentTrip !== name || state.currentAs !== as) return; // user moved on
				if (answered()) return;
				// Nothing that names this trip is not an answer: never drawn, never saved over the
				// copy on the phone.
				if (!isAnswerFor(itinerary, name)) throw noAnswer(new Error('no answer'), 'slow');
				// The server has just answered for this session (frappe sets user_id again on every
				// response): a refusal drawn while offline is over, unless it is still somebody
				// else's session.
				if (state.otherUser && !shellIsSomeoneElses()) state.otherUser = false;
				state.itinerary = itinerary;
				state.offline = null;
				rememberPeople(name, state.itinerary);
				render();
				keepForOffline(name, as, state.itinerary);
			})
			.catch(function (err) {
				done();
				if (state.currentTrip !== name || state.currentAs !== as) return; // not on screen any more
				// The copy saved on this phone is on screen already (the wait ran out): it stays.
				if (err && err.unreachable && state.offline && state.itinerary) return;
				if (err && err.unreachable) showSaved(name, as, err);
				else if (!answered(err)) loadFailed(name, as, err);
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
	// view, on the same screen. A trip that is not theirs to see, or is gone (403, 404), falls
	// back to their default trip, day by day, or the page says so when they have none. A
	// picture named in the address is dropped either way. The fallback replaces the
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
			fallback = { trip: name, as: '', view: state.currentView };
		} else if (status === 403 || status === 404) {
			var trip = state.trips.length ? defaultTrip() : null;
			if (!trip || trip === name) {
				state.denied = true;
				render();
				return;
			}
			fallback = { trip: trip, as: '', view: '' };
		}
		if (!fallback) {
			root.appendChild(el('div', 'ti-error', 'Could not load the trip: ' + ((err && err.message) || 'no answer')));
			return;
		}
		if (!captureOpen()) {
			if (samePlace(pushedFrom(), fallback)) {
				window.history.back();
			} else {
				writeTripEntry(false, fallback.trip, fallback.as, fallback.view);
			}
		}
		loadTrip(fallback.trip, fallback.as, fallback.view);
	}

	// Whether a place the page remembered (history.state.itin_from) is exactly this trip,
	// person, screen and picture.
	function samePlace(from, place) {
		return !!from && from.trip === place.trip &&
			(from.as || '') === (place.as || '') &&
			(from.view || '') === (place.view || '') &&
			(from.file || '') === (place.file || '');
	}

	// The place the entry on screen was pushed from ({trip, as}, plus `view` and `file` when it
	// had them), when this page pushed it.
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
		// The name the page was drawn for is not this session's, or not yet known to be: it is not
		// shown either.
		if (state.otherUser || state.unverified) return { title: 'My Itinerary', sub: '', page: 'My Itinerary' };
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

	// ?trip=, ?as=, ?view= and ?file= from the address. None of the others means anything
	// without a trip, and `docs` is the only screen besides the day list.
	function addressed() {
		try {
			var params = new URLSearchParams(window.location.search);
			var trip = params.get('trip') || '';
			if (!trip) return { trip: '', as: '', view: '', file: '' };
			return {
				trip: trip,
				as: params.get('as') || '',
				view: params.get('view') === 'docs' ? 'docs' : '',
				file: params.get('file') || '',
			};
		} catch (e) {
			return { trip: '', as: '', view: '', file: '' };
		}
	}

	// This page's address for a trip, person, screen and picture, always in that order after the
	// trip, and any other query kept. What is not given is dropped: a trip chip names the trip
	// alone, so another trip opens on its default view, day by day.
	function tripUrl(name, as, view, file) {
		var params = new URLSearchParams(window.location.search);
		params.set('trip', name);
		params.delete('as');
		params.delete('view');
		params.delete('file');
		if (as) params.set('as', as);
		if (view) params.set('view', view);
		if (file) params.set('file', file);
		return window.location.pathname + '?' + params.toString() + window.location.hash;
	}

	function writeTripEntry(push, name, as, view, file) {
		var entry = { itin_trip: name, itin_as: as || null };
		if (view) entry.itin_view = view;
		if (file) entry.itin_file = file;
		// A pushed entry remembers the place it was pushed from, which is the entry behind it, so
		// a refusal that would fall back to exactly that place can step back onto it (loadFailed),
		// and so can the viewer's Close and "Day by day". A replace keeps what the entry it
		// rewrites remembered: the entry behind it has not changed.
		var from = push ? { trip: state.currentTrip, as: state.currentAs || null } : pushedFrom();
		if (push && state.currentView) from.view = state.currentView;
		if (push && state.currentFile) from.file = state.currentFile;
		if (from && from.trip) entry.itin_from = from;
		try {
			if (push) window.history.pushState(entry, '', tripUrl(name, as, view, file));
			else window.history.replaceState(entry, '', tripUrl(name, as, view, file));
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

	// Another trip or person is fetched; the same one only has its screen and picture drawn.
	function showTripFromUrl() {
		var want = addressed();
		var name = want.trip || (state.trips.length ? defaultTrip() : null);
		if (!name) return;
		if (name !== state.currentTrip || want.as !== state.currentAs) loadTrip(name, want.as, want.view, want.file);
		else showScreen(want.view, want.file);
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

	// -- Offline: saved on this phone ----------------------------------------------------
	// See "Offline" in the note at the top of this file. All of it is a courtesy that may cost
	// the page nothing: no IndexedDB, no service worker, storage that throws, refuses or never
	// answers, and the page is exactly what it was without them (the Back/Forward harness has
	// none of them). Nothing here writes history.

	var SAVED_DB = 'sapphire-itinerary';
	var SAVED_DB_VERSION = 1;
	var ANSWERS = 'answers';
	var TRIP_LISTS = 'trips';
	var DAY_MS = 24 * 60 * 60 * 1000;
	// Safari has shipped versions whose indexedDB.open() neither succeeds nor fails
	// (capture/drafts.js): the page stops waiting for it after this long.
	var SAVED_OPEN_MS = 4000;
	// The person's own trips saved ahead: in progress, or starting within this many days.
	var AHEAD_DAYS = 14;
	// At most this many of one trip's files are kept on the phone.
	var FILES_PER_TRIP = 20;
	// A saved answer for a trip no longer in the person's trip list goes this long after it was
	// saved; a person keeps at most this many (the newest).
	var KEEP_DAYS = 7;
	var MAX_SAVED = 40;
	// How long a trip waits for the server before the copy saved on this phone is drawn (loadTrip):
	// the worker's own wait for the page (itinerary-sw.js PAGE_WAIT_MS). The request carries on.
	var ANSWER_WAIT_MS = 6000;
	// A trip saved ahead less than this long ago is not asked for again on the next page load.
	var FRESH_MS = 60 * 60 * 1000;
	var savedDb = null;
	var savedAhead = false;

	function storageFactory() {
		try {
			return window.indexedDB || null;
		} catch (e) {
			// Some privacy modes throw on the mere property access.
			return null;
		}
	}

	function openSaved() {
		if (savedDb) return savedDb;
		var factory = storageFactory();
		if (!factory) return Promise.reject(new Error('No storage on this device.'));
		savedDb = new Promise(function (resolve, reject) {
			var timer = setTimeout(function () {
				reject(new Error('Storage did not answer.'));
			}, SAVED_OPEN_MS);
			var request;
			try {
				request = factory.open(SAVED_DB, SAVED_DB_VERSION);
			} catch (e) {
				clearTimeout(timer);
				reject(e);
				return;
			}
			request.onupgradeneeded = function () {
				var db = request.result;
				if (!db.objectStoreNames.contains(ANSWERS)) db.createObjectStore(ANSWERS);
				if (!db.objectStoreNames.contains(TRIP_LISTS)) db.createObjectStore(TRIP_LISTS);
			};
			request.onsuccess = function () {
				clearTimeout(timer);
				var db = request.result;
				// Another tab upgrading the database must not be blocked by this one.
				db.onversionchange = function () {
					try {
						db.close();
					} catch (e) {
						// Already closed.
					}
					savedDb = null;
				};
				resolve(db);
			};
			request.onerror = function () {
				clearTimeout(timer);
				reject(request.error || new Error('Storage refused.'));
			};
			request.onblocked = function () {
				clearTimeout(timer);
				reject(new Error('Storage is busy in another tab.'));
			};
		});
		// A failed open is tried again next time rather than kept.
		savedDb.catch(function () {
			savedDb = null;
		});
		return savedDb;
	}

	// `fn(tx, out)` in one transaction, resolving with `out.value` once it commits (a write is not
	// kept until then).
	function savedRun(stores, mode, fn) {
		return openSaved().then(function (db) {
			return new Promise(function (resolve, reject) {
				var out = { value: null };
				var tx;
				try {
					tx = db.transaction(stores, mode);
					tx.oncomplete = function () { resolve(out.value); };
					tx.onerror = function () { reject(tx.error || new Error('Storage refused.')); };
					tx.onabort = function () { reject(tx.error || new Error('Storage was interrupted.')); };
					fn(tx, out);
				} catch (e) {
					try {
						if (tx) tx.abort();
					} catch (e2) {
						// Nothing to abort.
					}
					// A closed connection (another tab upgraded) opens again next time.
					savedDb = null;
					reject(e);
				}
			});
		});
	}

	function savedKey(user, trip, as) {
		return user + '|' + trip + '|' + (as || '');
	}

	// Each saved answer carries the offline marker it was saved under (`key`): it is shown only
	// while this phone's marker is still that one (showSaved).
	function saveAnswer(user, key, trip, as, answer) {
		if (!user || !key || !trip || !answer || !storageFactory()) return Promise.resolve(false);
		return savedRun(ANSWERS, 'readwrite', function (tx) {
			tx.objectStore(ANSWERS).put(
				{ user: user, key: key, trip: trip, as: as || '', answer: answer, saved_at: Date.now() },
				savedKey(user, trip, as)
			);
		}).then(function () { return true; }, function () { return false; });
	}

	// {saved, readable}: the copy saved for this person, marker, trip and view (null when there is
	// none), and whether this phone's storage could be read at all. Never rejects.
	function readAnswer(user, key, trip, as) {
		if (!user || !key || !storageFactory()) return Promise.resolve({ saved: null, readable: false });
		return savedRun(ANSWERS, 'readonly', function (tx, out) {
			var request = tx.objectStore(ANSWERS).get(savedKey(user, trip, as));
			request.onsuccess = function () { out.value = request.result || null; };
		}).then(function (saved) {
			var ok = saved && saved.user === user && saved.key === key && saved.answer && typeof saved.answer === 'object';
			return { saved: ok ? saved : null, readable: true };
		}, function () {
			return { saved: null, readable: false };
		});
	}

	function saveTripList(user, key, trips) {
		if (!user || !key || !storageFactory()) return Promise.resolve(false);
		return savedRun(TRIP_LISTS, 'readwrite', function (tx) {
			tx.objectStore(TRIP_LISTS).put({ user: user, key: key, trips: trips || [], saved_at: Date.now() }, user);
		}).then(function () { return true; }, function () { return false; });
	}

	// Everything saved under any other marker goes (answers and trip lists), and so do this
	// marker's answers for a trip no longer in its list once they are a week old, and any beyond
	// the newest MAX_SAVED. Resolves with how many answers went; never rejects.
	function pruneSaved(key) {
		if (!key || !storageFactory()) return Promise.resolve(0);
		var now = Date.now();
		return savedRun([ANSWERS, TRIP_LISTS], 'readwrite', function (tx, out) {
			out.value = 0;
			var lists = tx.objectStore(TRIP_LISTS);
			var answers = tx.objectStore(ANSWERS);
			var listKeys = lists.getAllKeys();
			var listValues = lists.getAll();
			var keys = answers.getAllKeys();
			var values = answers.getAll();
			// Requests in one transaction finish in the order they were made: this one is last.
			values.onsuccess = function () {
				var listed = {};
				(listKeys.result || []).forEach(function (listKey, i) {
					var list = (listValues.result || [])[i];
					if (!list || list.key !== key) {
						lists.delete(listKey);
						return;
					}
					(list.trips || []).forEach(function (trip) {
						if (trip && trip.name) listed[trip.name] = true;
					});
				});
				var kept = [];
				(keys.result || []).forEach(function (answerKey, i) {
					var saved = (values.result || [])[i] || {};
					var recent = Number(saved.saved_at) > now - KEEP_DAYS * DAY_MS;
					if (saved.key !== key || !(listed[saved.trip] || recent)) {
						answers.delete(answerKey);
						out.value += 1;
					} else {
						kept.push({ key: answerKey, at: Number(saved.saved_at) || 0 });
					}
				});
				kept.sort(function (a, b) { return b.at - a.at; });
				kept.slice(MAX_SAVED).forEach(function (old) {
					answers.delete(old.key);
					out.value += 1;
				});
			};
		}).catch(function () {
			return 0;
		});
	}

	// Everything saved on this phone, for everybody: every answer and trip list, and (the worker's
	// 'purge') every file and the kept page. Nobody holding this phone now is who it was saved for.
	// Resolves true once the answers are gone; never rejects.
	function forgetSaved() {
		toWorker({ type: 'purge' });
		if (!storageFactory()) return Promise.resolve(false);
		return savedRun([ANSWERS, TRIP_LISTS], 'readwrite', function (tx) {
			tx.objectStore(ANSWERS).clear();
			tx.objectStore(TRIP_LISTS).clear();
		}).then(function () { return true; }, function () { return false; });
	}

	// The person the page was drawn for (the boot's `user`), or '' when it names nobody.
	function bootUser() {
		return typeof BOOT.user === 'string' && BOOT.user !== 'Guest' ? BOOT.user : '';
	}

	// The offline marker (travel_management/itinerary_offline.py): 64 hex characters, never an
	// email, the same for one person every time.
	function isKey(value) {
		return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
	}

	// The marker the server drew this page with (the boot's `offline_key`), '' for none.
	function bootKey() {
		return isKey(BOOT.offline_key) ? BOOT.offline_key : '';
	}

	// This phone's marker, the `ee_itinerary_key` cookie: the key, '' when there is none (signed
	// out since, never set, or not a key), or null when this document has no cookies to read (or
	// reading them throws). Only the server writes it; the page never does.
	function markerCookie() {
		try {
			if (typeof document.cookie !== 'string') return null;
			var match = document.cookie.match(/(?:^|;\s*)ee_itinerary_key=([^;]*)/);
			var value = match && match[1] ? decodeURIComponent(match[1]) : '';
			return isKey(value) ? value : '';
		} catch (e) {
			return null;
		}
	}

	// True when this phone still holds the marker this page was drawn with: nobody has signed out
	// or in since the server drew it. A document with no cookies to read proves nothing, and
	// nothing is saved or shown on nothing.
	function markerIsBoots() {
		var key = bootKey();
		return !!key && markerCookie() === key;
	}

	// True when this browser's session is not the person the page was drawn for: the `user_id`
	// cookie names somebody else, or "Guest" (signed out). No cookie at all is not a sign-out
	// (see the note at the top of this file), and a document with none to read proves nothing.
	function shellIsSomeoneElses() {
		var boot = bootUser();
		var cookie = cookieUser();
		return !!boot && !!cookie && cookie !== boot;
	}

	// Whose copy this page may save and show: the person it was drawn for, while the session is
	// still theirs and the phone still holds the marker the page was drawn with (bootKey is then
	// the key to save it under). '' for nobody.
	function savingUser() {
		return shellIsSomeoneElses() || !markerIsBoots() ? '' : bootUser();
	}

	// An answer from the server (loadTrip), on a page drawn with another marker than this phone's
	// (state.unverified, set at boot). The server is reachable, so the page is what it always was,
	// unless the session that answer came under is not the person the page was drawn for: its
	// `user_id` (which frappe sets again on any response) names somebody else or Guest, or `err`
	// says nobody is signed in. True when that was the end of it: the page is refused, and the
	// answer is not drawn.
	function answered(err) {
		if (!state.unverified) return false;
		state.unverified = false;
		if (!shellIsSomeoneElses() && !(err && err.signedOut)) return false;
		state.otherUser = true;
		render();
		return true;
	}

	// Nothing saved is shown: somebody else's session, or a marker that is not the one the copy
	// was saved with. `forget`: and everything saved on the phone goes too (forgetSaved). No
	// history write.
	function refuseSaved(forget) {
		state.otherUser = true;
		state.unverified = false;
		state.offline = null;
		render();
		if (forget) forgetSaved();
	}

	// No answer from the server (loadTrip): the copy saved for this trip and person, if this
	// session is the person it was saved for and the phone still holds the marker it was saved
	// with. Drawn where the answer would have been, with no history write. A marker missing or
	// changed (a sign-out, or someone else's sign-in, since) shows nothing and deletes everything
	// saved; a `user_id` naming somebody else, or Guest, shows nothing and keeps it for its person.
	// Nothing saved says so; no storage to read is the error the page has always shown.
	//
	// "Me" (?as=<their own employee>) is the same answer as the default view, which is the one
	// saved ahead: when nothing was saved under their own id, the default copy is shown if it is
	// theirs (`viewing`), so tapping "Me" offline does not lose the itinerary on screen.
	//
	// `quiet`: the server has not answered yet but may still (loadTrip's wait ran out). Only a copy
	// that may be shown is drawn; nothing is refused, deleted or said, and the answer decides.
	function showSaved(name, as, err, quiet) {
		if (!markerIsBoots()) {
			if (quiet) return;
			// A page drawn with no marker (the server could not make one) has nothing saved to show
			// and nothing to refuse while the session is still its person's: it is the page it
			// always was, and says it is offline.
			if (!bootKey() && !shellIsSomeoneElses()) {
				showMissing(err);
				return;
			}
			refuseSaved(markerCookie() !== null);
			return;
		}
		if (shellIsSomeoneElses()) {
			if (!quiet) refuseSaved(false);
			return;
		}
		readAnswer(savingUser(), bootKey(), name, as).then(function (found) {
			if (found.saved || !as || as === 'crew') return found;
			return readAnswer(savingUser(), bootKey(), name, '').then(function (mine) {
				var answer = mine.saved && mine.saved.answer;
				return answer && answer.viewing === as ? mine : found;
			});
		}).then(function (found) {
			if (state.currentTrip !== name || state.currentAs !== as) return; // not on screen any more
			if (state.itinerary && !state.offline) return; // the server answered meanwhile
			if (!found.saved) {
				if (quiet) return;
				if (found.readable) showMissing(err);
				else loadFailed(name, as, err);
				return;
			}
			state.itinerary = found.saved.answer;
			state.offline = { saved_at: found.saved.saved_at, reason: (err && err.reason) || 'offline' };
			rememberPeople(name, state.itinerary);
			render();
		});
	}

	// No answer, and nothing saved for this trip and person: said where the trip would be, and
	// remembered (state.offline.missing), so the 'online' listener asks again.
	function showMissing(err) {
		state.itinerary = null;
		state.offline = { missing: true, reason: (err && err.reason) || 'offline' };
		render();
	}

	// Why there is no answer, in the words the banner and the "not saved" line start with.
	function offlineWords(reason) {
		if (reason === 'down') return 'The server isn\'t answering right now';
		if (reason === 'slow') return 'Can\'t reach the server';
		return 'You\'re offline';
	}

	function offlineBanner(offline) {
		var banner = el('div', 'ti-offline', offlineWords(offline.reason) + ' — showing your itinerary as saved ' + savedWhen(offline.saved_at) + '.');
		banner.setAttribute('role', 'status');
		return banner;
	}

	// "Fri, Sep 25, 7:05 PM": when the copy was saved, on this phone's clock.
	function savedWhen(ms) {
		var d = new Date(Number(ms) || 0);
		var minutes = d.getMinutes();
		return fmtDate(localIso(d)) + ', ' + fmtTime(d.getHours() + ':' + (minutes < 10 ? '0' : '') + minutes);
	}

	// An answer that landed (loadTrip): saved for next time. After the first one, once per page
	// load, the person's own upcoming trips are saved ahead, with their files. Nothing is asked
	// for ahead on a phone that could not keep it.
	// Only under the marker the page was drawn with, and only while the phone still holds it.
	function keepForOffline(name, as, answer) {
		var user = savingUser();
		var key = bootKey();
		if (!user || !key || !storageFactory()) return;
		saveAnswer(user, key, name, as, answer).then(function (saved) {
			if (!saved || savedAhead) return;
			savedAhead = true;
			saveTripList(user, key, state.trips);
			saveAhead(user, key, name, as, answer);
		});
	}

	// Each trip they travel on (`mine`) that is in progress or starts within AHEAD_DAYS: their
	// own itinerary of it (no ?as=), asked for one at a time, saved, and its files handed to the
	// worker. The one on screen, when it is that view, is not asked for again, and nor is one
	// saved less than FRESH_MS ago (every page load used to ask for every one again); its files
	// are still handed over, which costs nothing for a file the worker has.
	function saveAhead(user, key, name, as, answer) {
		var today = localIso();
		var horizon = localIso(new Date(Date.now() + AHEAD_DAYS * DAY_MS));
		var ahead = state.trips.filter(function (trip) {
			return trip && trip.name && trip.mine !== false &&
				String(trip.start_date || '') <= horizon && String(trip.end_date || '') >= today;
		});
		var chain = Promise.resolve();
		ahead.forEach(function (trip) {
			chain = chain.then(function () {
				if (trip.name === name && !as) return answer;
				return readAnswer(user, key, trip.name, '').then(function (found) {
					var age = found.saved ? Date.now() - Number(found.saved.saved_at) : -1;
					if (age >= 0 && age < FRESH_MS) return found.saved.answer;
					return api(ITINERARY, { trip: trip.name }).then(function (got) {
						if (!isAnswerFor(got, trip.name)) return null;
						saveAnswer(user, key, trip.name, '', got);
						return got;
					});
				});
			}).then(function (got) {
				keepFiles(key, got);
			}).catch(function () {
				// That trip keeps whatever was saved before; the next one is still asked for.
			});
		});
		return chain;
	}

	// A file the phone may keep: a picture or a PDF at one of this site's own file addresses.
	function savableFile(doc) {
		var url = doc && doc.url;
		if (typeof url !== 'string' || !/^\/(private\/)?files\/[^/]/.test(url)) return '';
		if (url.indexOf('\\') >= 0 || url.indexOf('..') >= 0) return '';
		var named = String(doc.file_name || '') + ' ' + url;
		return doc.is_image || /\.pdf(\s|[?#]|$)/i.test(named) ? url : '';
	}

	// The worker keeps them in the files cache named after the marker (itinerary-files-<key>).
	function keepFiles(key, answer) {
		if (!key || !answer) return;
		var urls = [];
		tripDocuments(answer).forEach(function (doc) {
			var url = savableFile(doc);
			if (url && urls.length < FILES_PER_TRIP && urls.indexOf(url) < 0) urls.push(url);
		});
		if (urls.length) toWorker({ type: 'cache-files', key: key, urls: urls });
	}

	// The service worker (www/itinerary-sw.js), scoped to this page: never the site root, where
	// the kiosk's worker is. Without one, nothing is kept and the page works online as before.
	//
	// Messages go to the worker this registration holds (itinWorker), never through
	// navigator.serviceWorker.ready. On a phone that already has the kiosk's (or the wall's)
	// worker at "/", `ready` on the first /itinerary visit resolves with THAT registration — the
	// one that matches the page and is already active — and stays resolved to it, so every
	// 'user', 'cache-files' and 'purge' went to kiosk-sw.js, which drops what it does not know: no
	// boarding pass was kept until a second visit. The technicians who travel are the ones with
	// the kiosk.
	var itinWorker = null;

	function registerWorker() {
		try {
			if (!('serviceWorker' in navigator)) return;
			var build = window.ITIN_BUILD || '';
			itinWorker = navigator.serviceWorker.register('/itinerary-sw.js?v=' + encodeURIComponent(build), { scope: '/itinerary' })
				.then(newestWorker)
				.catch(function () {
					// No worker: the page works online as it always has.
					return null;
				});
		} catch (e) {
			// The same.
			itinWorker = null;
		}
	}

	// The registration's newest worker once it is running: one installing or waiting (a first
	// visit, a deploy's new ?v=) when it has activated, else the active one. Not the active one
	// first: during a deploy that is the old worker, about to be replaced.
	function newestWorker(registration) {
		if (!registration) return null;
		var worker = registration.installing || registration.waiting;
		if (!worker) return registration.active || null;
		if (worker.state === 'activated') return worker;
		return new Promise(function (resolve) {
			worker.addEventListener('statechange', function () {
				if (worker.state === 'activated') resolve(worker);
				else if (worker.state === 'redundant') resolve(registration.active || null);
			});
		});
	}

	function toWorker(message) {
		if (!itinWorker) return;
		itinWorker.then(function (worker) {
			if (worker) worker.postMessage(message);
		}).catch(function () {
			// No worker to tell.
		});
	}

	// At boot: the worker, and whose saved copies may stay on this phone. The marker says: it is
	// the person the server last drew this page for, who has not signed out since. Whatever is
	// saved under any other marker goes, answers and files. No marker at all (signed out since,
	// or never set), and everything goes. A document with no cookies to read proves nothing, and
	// nothing is touched.
	function startOffline() {
		registerWorker();
		var marker = markerCookie();
		if (marker === null) return;
		if (!marker) {
			forgetSaved();
			return;
		}
		toWorker({ type: 'user', key: marker });
		pruneSaved(marker);
	}

	// True while the page cannot reach the server: the copy on screen is the saved one, or the
	// phone says it has no connection.
	function offlineNow() {
		return !!state.offline || phoneSaysOffline();
	}

	// The phone says it has no connection. Not saying so proves nothing (it is often wrong the
	// other way, on one bar of signal).
	function phoneSaysOffline() {
		try {
			return navigator.onLine === false;
		} catch (e) {
			return false;
		}
	}

	// A file that opens in a new tab (a PDF, and a picture's "Open original"): offline, that tab
	// would be outside /itinerary, where the worker cannot answer, so the page opens the tab
	// itself, at the tap, and puts the phone's copy in it. Online, the link is left to do what
	// it always has.
	function openSavedWhenOffline(link, doc) {
		link.addEventListener('click', function (ev) {
			if (!offlineNow() || ev.defaultPrevented) return;
			if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || (ev.button && ev.button !== 0)) return;
			ev.preventDefault();
			openSavedCopy(link, doc);
		});
	}

	function openSavedCopy(link, doc) {
		var tab = null;
		try {
			tab = window.open('', '_blank');
		} catch (e) {
			tab = null;
		}
		Promise.resolve().then(function () {
			return fetch(doc.url, { credentials: 'same-origin' });
		}).then(function (res) {
			if (!res || !res.ok) throw new Error('not saved');
			return res.blob();
		}).then(function (blob) {
			if (!tab) {
				var blocked = new Error('no tab');
				blocked.noTab = true;
				throw blocked;
			}
			var href = URL.createObjectURL(blob);
			tab.location.href = href;
			setTimeout(function () {
				try {
					URL.revokeObjectURL(href);
				} catch (e) {
					// Gone with the tab.
				}
			}, 60000);
		}).catch(function (err) {
			try {
				if (tab) tab.close();
			} catch (e) {
				// Already closed.
			}
			fileNote(link, err && err.noTab
				? 'This phone didn\'t open a new tab for the file. Try again, or once you\'re back online.'
				: 'This file isn\'t saved on this phone. It opens once you\'re back online.');
		});
	}

	// A line under the file's link saying why it did not open.
	function fileNote(link, text) {
		var box = link.parentNode;
		if (!box) return;
		if (!link.tiNote) {
			link.tiNote = el('div', 'ti-doc-note');
			link.tiNote.setAttribute('role', 'status');
			box.appendChild(link.tiNote);
		}
		link.tiNote.textContent = text;
	}

	// Back online while a saved copy is on screen: the server's answer replaces it, quietly, where
	// it is. With nothing of the server's on screen for the trip (a view never saved, or the "could
	// not load" of a phone that keeps nothing), the trip is asked for again, as loadTrip does. No
	// history write either way, and nothing if the person has moved on.
	window.addEventListener('online', function () {
		if (!state.currentTrip || state.denied || state.signedOut || state.otherUser || state.unverified) return;
		var name = state.currentTrip;
		var as = state.currentAs;
		if (!state.offline || state.offline.missing) {
			if (state.itinerary && !state.offline) return; // the server's answer is on screen
			loadTrip(name, as, state.currentView, state.currentFile);
			return;
		}
		var args = { trip: name };
		if (as) args.as_employee = as;
		api(ITINERARY, args).then(function (itinerary) {
			if (state.currentTrip !== name || state.currentAs !== as || !state.offline) return;
			if (!isAnswerFor(itinerary, name)) return; // no usable answer: the saved copy stays
			state.itinerary = itinerary;
			state.offline = null;
			rememberPeople(name, state.itinerary);
			render();
			keepForOffline(name, as, state.itinerary);
		}).catch(function () {
			// Still no answer: the saved copy stays.
		});
	});

	// -- Boot --------------------------------------------------------------------
	// A ?trip= is always asked for, listed or not (the server decides; a refusal falls back
	// in loadFailed), and a reload lands on the screen and picture it names. Without one, the
	// default trip is named in place. A page drawn for somebody other than this session (the
	// page kept on this phone, from before another person signed in, or everyone signed out)
	// shows nothing of theirs and asks for nothing.
	var first = addressed();
	// A page drawn with another marker than this phone holds is the page kept on the phone from
	// before a sign-out (or before somebody else's sign-in): nothing of its boot is drawn until
	// the server answers (answered), and with no answer, showSaved refuses it. A boot with no
	// marker saves and shows nothing offline, and draws as it always has.
	state.unverified = !!bootKey() && markerCookie() !== null && markerCookie() !== bootKey();
	if (shellIsSomeoneElses()) {
		state.otherUser = true;
		render();
	} else if (first.trip) {
		loadTrip(first.trip, first.as, first.view, first.file);
	} else if (state.trips.length) {
		first.trip = defaultTrip();
		writeTripEntry(false, first.trip);
		loadTrip(first.trip);
	} else {
		render();
	}
	startOffline();
})();
