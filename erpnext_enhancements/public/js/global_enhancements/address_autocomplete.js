/**
 * Google Places address autocomplete for `address_line1`.
 *
 * Exposes `erpnext_enhancements.address_autocomplete.attach(input, options)`,
 * which turns a plain desk <input> into an ARIA combobox that suggests real
 * addresses as you type and fills the sibling address fields on selection.
 *
 * Two callers, because there are two surfaces that show `address_line1`:
 *  - the Address form (project_enhancements/doctype/address/address.js), and
 *  - the Address quick-entry dialog (contact_address_quick_entry.js), which is
 *    where list+New / awesomebar / link-field "Create a new..." actually land
 *    when `frappe.boot.ee_contacts_ux` is on.
 * A `frappe.ui.form.on("Address")` handler never fires for the second one — it
 * is a frappe.ui.Dialog, not a Form — so the widget lives here, globally, and
 * both surfaces attach to it. That is also why this is in the global bundle
 * rather than in `doctype_js["Address"]`: the dialog opens from any doctype.
 *
 * WHICH GOOGLE API, and why not something simpler:
 *  - `places.Autocomplete` (the old widget that attached itself to an input) is
 *    closed to new customers.
 *  - `PlaceAutocompleteElement` is a sealed custom element; it cannot wrap an
 *    existing field, and the desk field is built by Frappe.
 * So this uses the Autocomplete DATA API (`AutocompleteSuggestion`) and renders
 * its own listbox. Google's policy for the data API requires the "Powered by
 * Google" attribution shown in that listbox whenever results appear off-map.
 *
 * The key is `Travel Settings.google_maps_api_key` (api.travel.get_maps_api_key
 * — a referrer-restricted browser key, exposed to the client by design). It
 * needs **Places API (New)** enabled on it in the Google Cloud console, on top
 * of Maps JavaScript. Nothing here can check that: a key with Maps but without
 * Places passes every init check and then fails every single request, so the
 * widget counts consecutive failures and hands the field back to the browser
 * after three rather than sitting there dead.
 *
 * Degrades to a plain text field on every failure path — no key, blocked
 * script, wrong API tier — and says so with console.warn. It is deliberately
 * never silent: a swallowed TypeError here once looked exactly like "no key
 * configured" for days (v1.160.2).
 */
(function () {
	"use strict";

	frappe.provide("erpnext_enhancements.address_autocomplete");

	var MIN_QUERY = 3;
	var DEBOUNCE_MS = 250;
	var BLUR_CLOSE_MS = 150;
	var MAX_FAILURES = 3;
	var BOX_CLASS = "ee-address-suggest";
	var ATTRIBUTION =
		"https://maps.gstatic.com/mapfiles/api-3/images/powered-by-google-on-white3.png";

	var seq = 0;

	// ------------------------------------------------------------------ loading

	var key_promise = null;
	var places_promise = null;

	function ensure_key() {
		if (!key_promise) {
			key_promise = frappe
				.xcall("erpnext_enhancements.api.travel.get_maps_api_key")
				.then(function (key) {
					return key || "";
				})
				.catch(function (err) {
					// Drop the memo so a later form visit retries a transient
					// network failure. A *blank* key is a real answer and stays
					// cached — no point asking again every time a form opens.
					key_promise = null;
					throw err;
				});
		}
		return key_promise;
	}

	/*
	 * Load the Maps JS API using Google's own inline bootstrap loader.
	 *
	 * NOT a plain <script src="…maps/api/js?loading=async">. That URL returns a
	 * *loader* which injects main.js/places.js afterwards, and it does not define
	 * google.maps.importLibrary itself — verified: the returned bootstrap contains
	 * zero occurrences of the string. So the script's onload fires while
	 * importLibrary is still undefined and calling it throws (v1.160.2, diagnosed
	 * live against production over several days). The loader below defines
	 * importLibrary SYNCHRONOUSLY and queues calls until the library is ready.
	 */
	function bootstrap_maps(key) {
		((g) => {
			var h,
				a,
				k,
				p = "The Google Maps JavaScript API",
				c = "google",
				l = "importLibrary",
				q = "__ib__",
				m = document,
				b = window;
			b = b[c] || (b[c] = {});
			var d = b.maps || (b.maps = {}),
				r = new Set(),
				e = new URLSearchParams(),
				u = () =>
					h ||
					(h = new Promise((f, n) => {
						a = m.createElement("script");
						e.set("libraries", [...r] + "");
						for (k in g)
							e.set(
								k.replace(/[A-Z]/g, (t) => "_" + t[0].toLowerCase()),
								g[k]
							);
						e.set("callback", c + ".maps." + q);
						a.src = "https://maps." + c + "apis.com/maps/api/js?" + e;
						d[q] = f;
						a.onerror = () => (h = n(Error(p + " could not load.")));
						a.nonce = (m.querySelector("script[nonce]") || {}).nonce || "";
						m.head.append(a);
					}));
			d[l]
				? console.warn(p + " only loads once. Ignoring:", g)
				: (d[l] = (f, ...n) => r.add(f) && u().then(() => d[l](f, ...n)));
		})({ key: key, v: "weekly" });
	}

	function ensure_places() {
		if (places_promise) return places_promise;

		places_promise = ensure_key()
			.then(function (key) {
				if (!key) throw new Error("Google Maps API key not set in Travel Settings");
				// Never resolve on `window.google.maps` alone. That singleton is
				// shared with the trip map, the POI picker and the pick-routing
				// map, none of which import `places` — the root namespace looks
				// complete while google.maps.places is still undefined (v1.204.0).
				if (!(window.google && window.google.maps && window.google.maps.importLibrary)) {
					bootstrap_maps(key);
				}
				return window.google.maps.importLibrary("places");
			})
			.then(function (places) {
				if (!places || !places.AutocompleteSuggestion || !places.AutocompleteSessionToken) {
					throw new Error("AutocompleteSuggestion unavailable — enable Places API (New)");
				}
				return places;
			})
			.catch(function (err) {
				places_promise = null; // let a later form visit retry
				throw err;
			});

		return places_promise;
	}

	// ---------------------------------------------------------------- countries

	// Google speaks CLDR region codes; Frappe's Country doctype is keyed by
	// English name and carries the same code in `Country.code`. Translating
	// through the code rather than the name is what makes this locale-proof:
	// `longText` follows the request language, `shortText` never does.
	var code_by_country = {};
	var country_by_code = {};

	function region_codes_for(country) {
		if (!country) return Promise.resolve(undefined);
		if (Object.prototype.hasOwnProperty.call(code_by_country, country)) {
			return Promise.resolve(code_by_country[country] ? [code_by_country[country]] : undefined);
		}
		return frappe.db
			.get_value("Country", country, "code")
			.then(function (r) {
				var code = ((r && r.message && r.message.code) || "").toLowerCase();
				// Only a call that actually answered gets cached — see below.
				code_by_country[country] = code;
				return code ? [code] : undefined;
			})
			.catch(function () {
				return undefined;
			});
	}

	function country_for_code(code) {
		code = (code || "").toLowerCase();
		if (!code) return Promise.resolve("");
		if (Object.prototype.hasOwnProperty.call(country_by_code, code)) {
			return Promise.resolve(country_by_code[code]);
		}
		return frappe.db
			.get_value("Country", { code: code }, "name")
			.then(function (r) {
				var name = (r && r.message && r.message.name) || "";
				// An empty answer is still an answer ("Frappe has no Country with
				// this code") and is worth caching. A *failed* call is not:
				// caching that would poison the code for the life of the page, so
				// every later pick from that country would silently skip the
				// field after one blip during a deploy.
				country_by_code[code] = name;
				return name;
			})
			.catch(function () {
				return "";
			});
	}

	// -------------------------------------------------------- component mapping

	/**
	 * Google address components -> Frappe Address fields. Pure; no frappe, no
	 * DOM. scripts/test_address_components.js slices this region out between
	 * these two banner comments and asserts against it, so keep it that way.
	 */
	function index_components(components) {
		var parts = {};
		(components || []).forEach(function (component) {
			(component.types || []).forEach(function (type) {
				parts[type] = { long: component.longText, short: component.shortText };
			});
		});
		return parts;
	}

	/**
	 * "1600 Amphitheatre Parkway" but "Hauptstrasse 12" — half the world puts
	 * the number after the street. Rather than keep a list of which countries
	 * do, read the order back off the place's own formatted address, whose
	 * first segment is the street line written the local way. Falls back to
	 * number-first (the US/UK form) whenever that cannot be determined — the
	 * formatted line often abbreviates the route ("Pkwy" for "Parkway"), which
	 * is exactly the case this must not guess at.
	 */
	function street_line(street_number, route, formatted_address) {
		if (!street_number || !route) return [street_number, route].filter(Boolean).join(" ");
		var head = String(formatted_address || "").split(",")[0];
		var at_number = head.indexOf(street_number);
		var at_route = head.indexOf(route);
		if (at_number > -1 && at_route > -1 && at_route < at_number) {
			return route + " " + street_number;
		}
		return street_number + " " + route;
	}

	function map_address_components(components, formatted_address) {
		var parts = index_components(components);
		var street_number = parts.street_number ? parts.street_number.long : "";
		var route = parts.route ? parts.route.long : "";
		var admin1 = parts.administrative_area_level_1;

		return {
			address_line1: street_line(street_number, route, formatted_address),
			// Unit/apartment. Google's own guidance is that Autocomplete (New)
			// often returns a partial prediction for subpremise addresses, so
			// this is frequently empty — never gate anything on it.
			address_line2: parts.subpremise ? parts.subpremise.long : "",
			// postal_town is the UK/Nordic mailing town; sublocality catches the
			// unincorporated-area case where there is no locality at all.
			city:
				(parts.locality && parts.locality.long) ||
				(parts.postal_town && parts.postal_town.long) ||
				(parts.sublocality && parts.sublocality.long) ||
				"",
			// "CA", not "California" — but only the US reliably has the short
			// form, so fall back to the long name rather than writing nothing.
			state: admin1 ? admin1.short || admin1.long : "",
			// The bare ZIP. The +4 arrives separately as postal_code_suffix and
			// is deliberately not joined on.
			pincode: parts.postal_code ? parts.postal_code.long : "",
			country_code: parts.country ? (parts.country.short || "").toLowerCase() : "",
		};
	}

	// ------------------------------------------------------------------- widget

	function warn(message, err) {
		if (window.console && console.warn) {
			console.warn("[address-autocomplete] " + message + ":", (err && err.message) || err || "");
		}
	}

	function ensure_box(state) {
		if (state.box) return state.box;
		// Appended to <body> and positioned fixed rather than nested in the
		// control: the quick-entry dialog scrolls its own body and stacks above
		// the form, and a nested listbox would be clipped by one and buried by
		// the other. Same reason the field-description tooltip does it.
		var box = document.createElement("div");
		box.className = BOX_CLASS;
		box.id = BOX_CLASS + "-" + state.id;
		box.hidden = true;
		document.body.appendChild(box);
		state.box = box;
		return box;
	}

	function position_box(state) {
		if (!state.box || state.box.hidden) return;
		var rect = state.input.getBoundingClientRect();
		var box = state.box;
		box.style.left = rect.left + "px";
		box.style.width = rect.width + "px";
		// Flip above the field when the space below cannot hold the list.
		var below = window.innerHeight - rect.bottom;
		if (below < box.offsetHeight + 8 && rect.top > below) {
			box.style.top = Math.max(4, rect.top - box.offsetHeight - 4) + "px";
		} else {
			box.style.top = rect.bottom + 4 + "px";
		}
	}

	function open_box(state) {
		if (state.box.hidden) {
			state.box.hidden = false;
			// Only while open: a capture-phase scroll listener fires for every
			// scrollable ancestor, and a fixed box does not move with them.
			window.addEventListener("scroll", state.handlers.reposition, true);
			window.addEventListener("resize", state.handlers.reposition);
			document.addEventListener("mousedown", state.handlers.outside, true);
		}
		state.input.setAttribute("aria-expanded", "true");
		position_box(state);
	}

	function close_box(state) {
		if (!state.box) return;
		// Closed means CLOSED: cancel the pending debounce and invalidate any
		// in-flight response (its .then compares against last_query, which this
		// blanks), or a slow fetch resurrects the box over the fields below it —
		// after a pick, after blur, even after Escape.
		if (state.timer) {
			window.clearTimeout(state.timer);
			state.timer = null;
		}
		state.last_query = "";
		state.items = [];
		state.active = -1;
		if (!state.box.hidden) {
			state.box.hidden = true;
			window.removeEventListener("scroll", state.handlers.reposition, true);
			window.removeEventListener("resize", state.handlers.reposition);
			document.removeEventListener("mousedown", state.handlers.outside, true);
		}
		state.box.innerHTML = "";
		state.input.setAttribute("aria-expanded", "false");
		state.input.removeAttribute("aria-activedescendant");
	}

	function on_input(state) {
		if (state.timer) window.clearTimeout(state.timer);
		var query = state.input.value.trim();
		if (query.length < MIN_QUERY) {
			close_box(state);
			return;
		}
		// Debounced: every keystroke would bill a request and race its
		// predecessors. Google's own guidance is 3–4 characters minimum and a
		// request "every few keystrokes", not per character.
		state.timer = window.setTimeout(function () {
			fetch_suggestions(state, query);
		}, DEBOUNCE_MS);
	}

	function fetch_suggestions(state, query) {
		state.last_query = query;
		var country = state.options.get_country ? state.options.get_country() : "";

		region_codes_for(country)
			.then(function (region_codes) {
				var request = {
					input: query,
					sessionToken: state.token,
					// Pinned, not left to the browser locale: without it the
					// component text comes back in whatever language the browser
					// asks for, and "Estados Unidos" is not a Country record.
					language: "en",
				};
				if (region_codes) request.includedRegionCodes = region_codes;
				return state.places.AutocompleteSuggestion.fetchAutocompleteSuggestions(request);
			})
			.then(function (result) {
				state.failures = 0;
				// A slow response for an old query must not clobber newer typing,
				// and a response for a CLOSED box must not resurrect it.
				if (state.last_query !== query) return;
				render_suggestions(state, (result && result.suggestions) || []);
			})
			.catch(function (err) {
				warn("address suggestions failed", err);
				// Same staleness rule as the success path. A slow request that
				// fails after a newer one already painted the box must not rip
				// that box away mid-read, and must not count against a key that
				// is plainly working.
				if (state.last_query !== query) return;
				close_box(state);
				// A key without Places API (New) fails every call: after three in
				// a row, give the field back to the browser for good.
				state.failures += 1;
				if (state.failures >= MAX_FAILURES) teardown(state);
			});
	}

	function render_suggestions(state, list) {
		// The box must never (re)open under a field the user has already left.
		if (document.activeElement !== state.input) return;

		// placePrediction is documented optional — query predictions come back
		// on the same list and have nothing to fill the form with.
		state.items = list.filter(function (item) {
			return item && item.placePrediction;
		});
		state.active = -1;
		state.box.innerHTML = "";
		state.input.removeAttribute("aria-activedescendant");

		if (!state.items.length) {
			close_box(state);
			return;
		}

		var listbox = document.createElement("div");
		listbox.setAttribute("role", "listbox");
		listbox.id = state.box.id + "-list";

		state.items.forEach(function (item, index) {
			var option = document.createElement("div");
			option.className = "ee-address-suggest-option";
			option.setAttribute("role", "option");
			option.setAttribute("aria-selected", "false");
			option.id = state.box.id + "-option-" + index;
			// textContent, never innerHTML: this is Google-supplied text, but no
			// external string becomes markup.
			option.textContent = item.placePrediction.text
				? item.placePrediction.text.text
				: String(item.placePrediction);
			option.addEventListener("mousedown", function (event) {
				// mousedown, not click: it fires before the input's blur, so the
				// pick lands while the box is still alive.
				event.preventDefault();
				pick(state, index);
			});
			option.addEventListener("mouseenter", function () {
				set_active(state, index);
			});
			listbox.appendChild(option);
		});
		state.box.appendChild(listbox);

		// Required attribution for the Autocomplete Data API off-map. The image
		// is a Google-hosted hotlink; if it is blocked, plain text keeps the
		// attribution (and the policy) intact.
		var footer = document.createElement("div");
		footer.className = "ee-address-suggest-footer";
		var mark = document.createElement("img");
		mark.src = ATTRIBUTION;
		mark.alt = "Powered by Google";
		mark.onerror = function () {
			footer.textContent = "Powered by Google";
		};
		footer.appendChild(mark);
		state.box.appendChild(footer);

		open_box(state);
	}

	function set_active(state, index) {
		var options = state.box.querySelectorAll(".ee-address-suggest-option");
		for (var i = 0; i < options.length; i++) {
			options[i].setAttribute("aria-selected", i === index ? "true" : "false");
		}
		state.active = index;
		if (index >= 0 && options[index]) {
			state.input.setAttribute("aria-activedescendant", options[index].id);
			if (options[index].scrollIntoView) {
				options[index].scrollIntoView({ block: "nearest" });
			}
		} else {
			state.input.removeAttribute("aria-activedescendant");
		}
	}

	function on_keydown(state, event) {
		if (!state.box || state.box.hidden) return;
		if (event.key === "ArrowDown") {
			event.preventDefault();
			set_active(state, (state.active + 1) % state.items.length);
		} else if (event.key === "ArrowUp") {
			event.preventDefault();
			// From "nothing highlighted", up means the LAST option — the plain
			// modulo would land on the second-to-last.
			set_active(
				state,
				state.active < 0
					? state.items.length - 1
					: (state.active - 1 + state.items.length) % state.items.length
			);
		} else if (event.key === "Enter") {
			// Only intercept when an option is highlighted — a bare Enter with the
			// box open but nothing chosen must not be swallowed (it saves the form).
			if (state.active >= 0) {
				event.preventDefault();
				pick(state, state.active);
			} else {
				close_box(state);
			}
		} else if (event.key === "Escape") {
			// Escape must go no further. In the quick-entry dialog it reaches
			// both bootstrap's modal handler and frappe's window-level
			// handle_escape_key -> cur_dialog.cancel(), so dismissing the
			// suggestion list would throw away every field already typed.
			// Frappe stops Escape for its own dropdowns, but only for controls
			// wrapped in `.awesomplete` — a Data field is not one.
			event.preventDefault();
			event.stopPropagation();
			close_box(state);
		} else if (event.key === "Tab") {
			// Tab keeps its default: close the list and move to the next field.
			close_box(state);
		}
	}

	function pick(state, index) {
		var item = state.items[index];
		if (!item) return;
		var place = item.placePrediction.toPlace();
		close_box(state);
		place
			.fetchFields({ fields: ["addressComponents", "formattedAddress", "location", "id"] })
			.then(function () {
				return apply(state, place);
			})
			.catch(function (err) {
				warn("could not read the selected address", err);
			})
			.finally(function () {
				// A session ends at place details; the next keystroke starts a
				// fresh one. Minted even when the details call failed — reusing a
				// spent token is billed as if no token had been sent at all.
				if (!state.destroyed && state.places) {
					state.token = new state.places.AutocompleteSessionToken();
				}
			});
	}

	/**
	 * `Place.location` is a LatLng (lat()/lng() methods), but the same field
	 * comes back as a plain {lat, lng} literal often enough that reading it
	 * both ways is cheaper than finding out the hard way that every saved
	 * coordinate is undefined.
	 */
	function coord(latlng, key) {
		if (!latlng) return null;
		var value = latlng[key];
		if (typeof value === "function") return latlng[key]();
		return typeof value === "number" ? value : null;
	}

	function place_meta(place) {
		return {
			// Google exempts the place ID from its no-caching rule — it is the
			// one field we are allowed to store indefinitely.
			place_id: place.id || "",
			formatted_address: place.formattedAddress || "",
			latitude: coord(place.location, "lat"),
			longitude: coord(place.location, "lng"),
		};
	}

	function apply(state, place) {
		var components = place.addressComponents || [];
		// Nothing to apply, and blanking fields off an empty response would be
		// pure loss.
		if (!components.length) return Promise.resolve();

		var mapped = map_address_components(components, place.formattedAddress);
		return country_for_code(mapped.country_code).then(function (country) {
			var values = {};
			// A pick REPLACES the address, empties included. Merging into what
			// was already there is how a record ends up reading "1600
			// Amphitheatre Parkway, Suite 5, Mountain View" — the unit number of
			// a different building 700 miles away, on an address that looks
			// perfectly deliverable. Same for a stale ZIP when the new place has
			// no postal_code.
			["address_line2", "city", "state", "pincode"].forEach(function (f) {
				values[f] = mapped[f] || "";
			});
			// Line 1 is the exception: picking a locality or a POI returns no
			// street at all, and it is mandatory. Never blank what was typed.
			if (mapped.address_line1) values.address_line1 = mapped.address_line1;
			// Country is a Link, and mandatory. Only ever written from the code
			// lookup, so a country Frappe does not have leaves the existing
			// value alone rather than emptying a required field or planting one
			// that fails validation on save.
			if (country) values.country = country;

			if (state.destroyed) return;
			if (state.options.on_pick) state.options.on_pick(values, place_meta(place));
		});
	}

	function bind(state) {
		// Frappe already sets autocomplete="off" on every Data input, so unlike
		// the public form there is no native autofill menu to switch off here.
		state.input.setAttribute("role", "combobox");
		state.input.setAttribute("aria-autocomplete", "list");
		state.input.setAttribute("aria-expanded", "false");
		state.input.setAttribute("aria-controls", ensure_box(state).id);

		state.input.addEventListener("input", state.handlers.input);
		state.input.addEventListener("keydown", state.handlers.keydown);
		state.input.addEventListener("blur", state.handlers.blur);
		state.bound = true;
	}

	function unbind(state) {
		if (!state.bound) return;
		state.input.removeEventListener("input", state.handlers.input);
		state.input.removeEventListener("keydown", state.handlers.keydown);
		state.input.removeEventListener("blur", state.handlers.blur);
		state.input.removeAttribute("role");
		state.input.removeAttribute("aria-autocomplete");
		state.input.removeAttribute("aria-expanded");
		state.input.removeAttribute("aria-controls");
		state.bound = false;
	}

	function teardown(state) {
		warn("address suggestions disabled after repeated failures", "");
		close_box(state);
		unbind(state);
	}

	function destroy(state) {
		state.destroyed = true;
		close_box(state);
		unbind(state);
		if (state.box && state.box.parentNode) {
			state.box.parentNode.removeChild(state.box);
		}
		state.box = null;
	}

	/**
	 * Turn `input` into an address combobox.
	 *
	 * @param {HTMLInputElement} input
	 * @param {Object} [options]
	 * @param {Function} [options.get_country] - current country, to scope results
	 * @param {Function} [options.on_pick] - (values, meta) => void. `values` is the
	 *        Address fields to write; `meta` is {place_id, formatted_address,
	 *        latitude, longitude} for callers that have somewhere to keep it
	 * @returns {Object|null} controller: { input, reset(), destroy() }
	 */
	function attach(input, options) {
		if (!input) return null;

		var state = {
			id: ++seq,
			input: input,
			options: options || {},
			box: null,
			places: null,
			token: null,
			items: [],
			active: -1,
			timer: null,
			last_query: "",
			// Consecutive fetch failures. A key that loads Maps but is not enabled
			// for Places API (New) passes every init check and then fails every
			// request — after a few of those the combobox tears itself down.
			failures: 0,
			bound: false,
			destroyed: false,
		};

		state.handlers = {
			input: function () {
				on_input(state);
			},
			keydown: function (event) {
				on_keydown(state, event);
			},
			blur: function () {
				// Let a click on an option land before the box disappears.
				window.setTimeout(function () {
					close_box(state);
				}, BLUR_CLOSE_MS);
			},
			reposition: function () {
				position_box(state);
			},
			outside: function (event) {
				if (state.box && !state.box.contains(event.target) && event.target !== state.input) {
					close_box(state);
				}
			},
		};

		ensure_places()
			.then(function (places) {
				if (state.destroyed) return;
				state.places = places;
				state.token = new places.AutocompleteSessionToken();
				bind(state);
			})
			.catch(function (err) {
				// The field still works as plain text, but this must NOT be
				// silent — an earlier revision swallowed it and a TypeError
				// looked exactly like "no key configured" for days.
				warn("address autocomplete unavailable", err);
			});

		return {
			input: input,
			/** New document in a reused form: drop the old search session. */
			reset: function () {
				close_box(state);
				state.failures = 0;
				if (state.places && !state.destroyed) {
					state.token = new state.places.AutocompleteSessionToken();
				}
			},
			destroy: function () {
				destroy(state);
			},
		};
	}

	erpnext_enhancements.address_autocomplete = {
		attach: attach,
		map_address_components: map_address_components,
	};
})();
