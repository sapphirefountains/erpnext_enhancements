/**
 * Shared Google Maps JavaScript API loader.
 *
 * This exists because the Maps API can only be injected once per page.
 * Previously, four different files injected it (travel_trip_map, pick_routing_map,
 * address_autocomplete, fountain_move), which led to race conditions and "already loaded"
 * errors. Now they all call this shared loader.
 *
 * It is plain ES5 with no dependencies, so it works both in the desk bundle and
 * as a bare <script> on standalone pages like the Time Kiosk.
 *
 * CRITICAL RULE: Always resolve via `importLibrary()`, never just on `window.google.maps`.
 * `window.google.maps` being present only means the loader is injected; individual
 * namespaces like `google.maps.places` remain undefined until explicitly imported.
 * This loader guarantees `importLibrary` is called for every requested library,
 * reproducing the exact fix for bugs we already shipped.
 *
 * CRITICAL RULE: `mapId` and `styles` are mutually exclusive. Supplying both logs
 * a console error and ignores `styles`.
 */
(function() {
	"use strict";

	var loaderPromise = null;

	var DARK_STYLES = [
		{ elementType: "geometry", stylers: [{ color: "#242f3e" }] },
		{ elementType: "labels.text.stroke", stylers: [{ color: "#242f3e" }] },
		{ elementType: "labels.text.fill", stylers: [{ color: "#746855" }] },
		{
			featureType: "administrative.locality",
			elementType: "labels.text.fill",
			stylers: [{ color: "#d59563" }]
		},
		{
			featureType: "poi",
			elementType: "labels.text.fill",
			stylers: [{ color: "#d59563" }]
		},
		{
			featureType: "poi.park",
			elementType: "geometry",
			stylers: [{ color: "#263c3f" }]
		},
		{
			featureType: "poi.park",
			elementType: "labels.text.fill",
			stylers: [{ color: "#6b9a76" }]
		},
		{
			featureType: "road",
			elementType: "geometry",
			stylers: [{ color: "#38414e" }]
		},
		{
			featureType: "road",
			elementType: "geometry.stroke",
			stylers: [{ color: "#212a37" }]
		},
		{
			featureType: "road",
			elementType: "labels.text.fill",
			stylers: [{ color: "#9ca5b3" }]
		},
		{
			featureType: "road.highway",
			elementType: "geometry",
			stylers: [{ color: "#746855" }]
		},
		{
			featureType: "road.highway",
			elementType: "geometry.stroke",
			stylers: [{ color: "#1f2835" }]
		},
		{
			featureType: "road.highway",
			elementType: "labels.text.fill",
			stylers: [{ color: "#f3d19c" }]
		},
		{
			featureType: "transit",
			elementType: "geometry",
			stylers: [{ color: "#2f3948" }]
		},
		{
			featureType: "transit.station",
			elementType: "labels.text.fill",
			stylers: [{ color: "#d59563" }]
		},
		{
			featureType: "water",
			elementType: "geometry",
			stylers: [{ color: "#17263c" }]
		},
		{
			featureType: "water",
			elementType: "labels.text.fill",
			stylers: [{ color: "#515c6d" }]
		},
		{
			featureType: "water",
			elementType: "labels.text.stroke",
			stylers: [{ color: "#17263c" }]
		}
	];

	function bootstrap(key) {
		return new Promise(function(resolve, reject) {
			if (window.google && window.google.maps && window.google.maps.importLibrary) {
				resolve();
				return;
			}
			try {
				(function(g) {
					var h, a, k, p = "The Google Maps JavaScript API", c = "google", l = "importLibrary", q = "__ib__", m = document, b = window;
					b = b[c] || (b[c] = {});
					var d = b.maps || (b.maps = {}), r = new Set(), e = new URLSearchParams(), u = function() {
						return h || (h = new Promise(function(f, n) {
							a = m.createElement("script");
							e.set("libraries", Array.from(r).join(",") || "");
							for (k in g) e.set(k.replace(/[A-Z]/g, function(t) { return "_" + t[0].toLowerCase(); }), g[k]);
							e.set("callback", c + ".maps." + q);
							a.src = "https://maps." + c + "apis.com/maps/api/js?" + e;
							d[q] = f;
							a.onerror = function() { h = n(new Error(p + " could not load.")); };
							var nonceNode = m.querySelector("script[nonce]");
							a.nonce = nonceNode ? nonceNode.nonce || "" : "";
							m.head.appendChild(a);
						}));
					};
					if (d[l]) {
						console.warn(p + " only loads once. Ignoring.");
					} else {
						d[l] = function(f) {
							var args = Array.prototype.slice.call(arguments, 1);
							r.add(f);
							return u().then(function() { return d[l].apply(d, [f].concat(args)); });
						};
					}
				})({ key: key, v: "weekly" });
				resolve();
			} catch (e) {
				reject(e);
			}
		});
	}

	window.EEGoogleMaps = {
		load: function(opts) {
			if (!opts || !opts.apiKey) {
				return Promise.reject(new Error("Google Maps API key is not configured."));
			}
			if (!loaderPromise) {
				loaderPromise = bootstrap(opts.apiKey).catch(function(err) {
					loaderPromise = null;
					throw err;
				});
			}

			return loaderPromise.then(function() {
				// "maps" is ALWAYS imported, even when the caller asked for nothing.
				//
				// `bootstrap()` only *defines* `google.maps.importLibrary`; it does not
				// fetch anything. The script is requested by the first importLibrary()
				// call and by nothing else. So a caller that passed no libraries used to
				// get back a `google.maps` containing importLibrary and not one other
				// symbol — `new google.maps.Map(...)` then threw, and the Travel Trip
				// agenda map went blank. The old `<script src=...>` tag these callers
				// were written against populated the namespace by itself, so importing
				// the core library here is what keeps that contract.
				var libs = ["maps"];
				var requested = opts.libraries || [];
				for (var i = 0; i < requested.length; i++) {
					if (libs.indexOf(requested[i]) === -1) libs.push(requested[i]);
				}
				var imports = [];
				for (var j = 0; j < libs.length; j++) {
					imports.push(window.google.maps.importLibrary(libs[j]));
				}
				return Promise.all(imports).then(function() {
					return window.google.maps;
				});
			});
		},
		mapOptions: function(cfg, theme) {
			cfg = cfg || {};
			var isDark = theme === "dark";
			var mapId = isDark ? cfg.map_id_dark : cfg.map_id_light;
			if (mapId) {
				return { mapId: mapId };
			}
			return { styles: isDark ? DARK_STYLES : [] };
		},
		DARK_STYLES: DARK_STYLES
	};
})();
